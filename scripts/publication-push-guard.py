#!/usr/bin/env python3
# Script: publication-push-guard.py
# Purpose: PreToolUse hook — fail-closed dev-block keeping `dev` private in a repo that adopted the dev/main publication model
"""PreToolUse hook — keep `dev` local in a repo that has adopted the dev/main publication model.

Called by Claude Code hooks with the tool-call JSON on stdin. When a `git push` (or an alias that
expands to one) is about to run and the target repo has a `.publication.toml` marker at its root
(the "adopted" signal — see specs/2026-07-18-publication-model-foundation.md), this blocks (exit 2)
any push that could publish `dev` — as a plain branch refspec, via a bare push whose current branch
is `dev`, or via a tag reachable only from `dev`. An ordinary push in a repo with no marker is
untouched (the mechanism is dormant until a repo adopts) — EXCEPT the one bounded case described
below where the target repo cannot be determined at all: root-unknown blocks regardless of marker,
in an adopted repo or not, because "unknown repo" means adoption can't be confirmed either way.

WHAT THIS IS, AND WHAT IT IS NOT. This is a fail-closed gate, not a deliberateness nudge like
push-guard.py: it is NOT overridable by `ALLOW_PUSH=1`, and ambiguity of any kind — an unparseable
command, an unresolvable repo root, a `--git-dir`/`--work-tree`/`GIT_DIR=` override, an
unresolvable refspec — blocks rather than allows.

But it is NOT the enforcement boundary, and must not be read as one. It is a text parser looking at
a command string BEFORE the shell touches it, so it can only ever be an early, informative
accident-catcher: everything under "Known residuals" below is a shape it cannot see, and that list
is bounded only by what has been measured, never by what exists. The LOAD-BEARING boundary is the
git-native `pre-push` hook (`git-hooks/pre-push`), which runs inside git with the resolved refs in
hand and therefore cannot be fooled by quoting, nesting, or a wrapper this file has never heard of.
Its own residual is named and short: `--no-verify`, or removing the hook. When the two disagree,
the hook is right.

Detection is an allowlist ON THE REFSPEC AXIS ONLY — a push is judged safe only when every refspec
it carries matches the narrow `[+]<plain-branch>[:<plain-branch>]` grammar (or is a tag provably
reachable from `main`) and none of it is `dev`; anything else is ambiguous and blocked, even where
the equivalent push would in fact be safe. On the SUBCOMMAND axis it is a blocklist: an
unrecognised subcommand that is not a known alias is ALLOWED, because the alternative would block
every ordinary git command in the session. `PUBLISHING_SUBCOMMANDS` denies the specific transports
that publish without ever invoking `push` (`send-pack`, `svn dcommit`, `p4 submit`, `daemon`), and
that set is by construction a list of what someone thought of. Saying "detection is an allowlist"
without this split is the kind of claim that reads as a guarantee and is not one.

Configuration is a THIRD axis, and it is a deny list scoped to each invocation's own env prefix and
global-option run (`_config_injection_reason`): `core.hooksPath`, `include.path`/`includeIf`, any
environment assignment outside an allowlist of names verified boundary-inert (this repo's own
control-plane variables, author and committer identity, locale/timezone, `PAGER`, `GIT_PAGER`,
`GIT_TERMINAL_PROMPT`, `GIT_CONFIG_NOSYSTEM`), plus any `-c`
value that does not resolve to a literal `key=value`. These are refused whatever the command goes
on to do, because they can relocate or silence the `pre-push` hook — and a command that disables
layer 2 while carrying a refspec layer 1 allows was measured to defeat the whole stack in one line.
A second, COMMAND-scoped check (`_exported_injection_reason`) extends the same allowlist past that
one-invocation prefix, to a denied name that reaches a git invocation via an `export` in a
different segment of the same command, an `export NAME` naming an already-assigned variable, or
`set -a`/`set -o allexport` — shapes the prefix walk cannot see by construction. It is still not
whole-environment: an export that happened in an earlier, separately-allowed command (a shell
profile, `.envrc`) reaches git
with an empty inline prefix and is a named residual, not something this check closes.

Cheap pre-filter, then fail closed: a command that does not even contain the word `git` returns 0
immediately (this hook is a global PreToolUse(Bash) hook and must be free for the overwhelming
majority of commands that touch no git invocation at all). Past that point — once the command is
plausibly git-related — `scripts/lib/git_command` is imported LAZILY, inside the same try/except
that wraps all further evaluation, so that even an ImportError (a corrupted install, a moved
module) blocks rather than silently passing every push through. This means a command containing the
word "git" that is genuinely unparseable (unbalanced quotes) also blocks, even if it turns out not
to be a push and even in a non-adopted repo — parsing must succeed before adoption can even be
checked, and there is no safe way to tell "not a push" from "can't tell if it's a push" once
tokenizing itself fails.

Known, documented residuals (bounded, never silent — same posture as recast-commit-gate.py). Read
these as the reason the `pre-push` hook exists, not as a short list of edge cases:
  - `-c "$K=v"`, `-c $(…)`, or any option value that is not a literal token after shlex has
    stripped quoting. This gate cannot resolve what the shell will expand, so it refuses rather
    than guessing — but the mirror case is the real residual: a denied config key ASSEMBLED at
    expansion time is invisible here in principle, not by oversight.
  - `sh -c "git push …"` / `eval "git push …"`: the nested command string is opaque to the
    tokenizer, so these are NOT detected (same accepted gap as git_command's own WRAPPERS scope).
  - **The env axis is an ALLOWLIST over every name, not a `GIT_*` shape.** `HOME=`,
    `XDG_CONFIG_HOME=`, `PATH=`, `LD_PRELOAD=`, `DYLD_*=` and every other unlisted name are
    DENIED where they reach a git invocation. This closed a measured full-stack bypass: a
    hostile `HOME` moved `core.hooksPath`, the real hook did not run, and the push returned
    rc=0. REACH is a property of the NAME, not the construct: a STANDALONE bare assignment (no
    command word ends its segment -- `HOME=/x ; git …`), `declare`/`typeset`/`readonly`/`local`
    without `-x`, and `+=` all reach git's environment when the name ALREADY carries the export
    attribute (measured against real bash — `HOME`, `PATH`, `XDG_CONFIG_HOME`, `LD_PRELOAD` and
    anything already in `os.environ`), even with allexport off and no `-x`/`export`. A FRESH
    name in one of those shapes, or a bare assignment scoped as a PREFIX to a different,
    non-git command (`HOME=/x true` — bash gives it only to `true`'s environment), keeps only
    the older `^GIT_` over-block.
  - `env` with ANY OPTION (`env -i`, `env -u`, `env -S`), and `env` by ABSOLUTE PATH
    (`/usr/bin/env`), yield no git invocation from the tokenizer at all, so no arm sees them --
    the refspec rule included. The OPTION-LESS `env NAME=v git <verb>` IS parsed and IS judged.
    Same accepted class as `sh -c` above, named here rather than left undocumented.
  - **An OVER-BLOCK, accepted deliberately rather than narrowed away.** An expansion-shaped token
    IMMEDIATELY followed by an export-family word is treated as possibly-vanishing, so a command
    whose program name is an expansion and whose first argument happens to be the literal word
    `export` is refused: `$TOOL export secrets.json`, `$HOME/bin/mytool export data.csv`,
    `"$TOOL" export data.csv`. All three measured rc=2. The trigger is narrow and the boundary is
    measured: `./mytool export …`, `mytool export …`, `$(which doppler) secrets export` (the word
    is not adjacent) and `$TOOL set -a` all stay ALLOWED.
    **Not narrowed on purpose.** The predicate exists because an expansion in command-prefix
    position may vanish or become a wrapper, and bash then runs `export` in the current shell --
    that is a measured bypass (`$(echo command) export HOME=/x ; git <verb>`). Narrowing the
    predicate to exclude tokens carrying literal text (`$HOME/bin/tool`) would re-open exactly
    that shape, which is this module's recorded narrowing hazard: the repair for a false positive
    silently drops true positives, and the author writes tests from the false positive rather
    than from what quietly left. The refusal names the offending token and the remedy, and
    `shell_shape_expansion_program_with_export_argument` pins this trade so it cannot widen
    unnoticed.
  - Alias resolution chases the chain recursively (matching real git), bounded by a depth cap and
    cycle guard (both fail closed if hit). A chain that resolves — at ANY depth — to `push` is
    BLOCKED unconditionally: this hook does not attempt to reproduce git's own alias-argument
    substitution well enough to prove such a push safe, so it never re-verifies an alias-resolved
    push against the refspec allowlist the way a literal `git push` invocation is. A shell alias
    (`alias.x = "!…"`), an unparseable expansion, or a hop that lands on a subcommand that is
    neither a known-safe built-in nor itself a resolvable alias is treated as ambiguous → blocked,
    rather than silently allowed.
  - `cd`/`pushd` targets are tracked textually across the whole token stream to compute each push's
    effective root (iter_git_invocations exposes per-invocation `-C` but not `cd` state). A `cd`
    target that isn't a static path — `cd -`, a bare `cd`, anything containing `$` or `~` — makes
    the working directory unresolvable, and `popd` is not tracked as a stack (any `popd` marks the
    directory unresolvable too) — all of which force a block on any push that follows, per the
    root-must-resolve-or-block rule; this trades a few false blocks in scripts that legitimately use
    `popd`/`~` for never silently losing track of an adopted repo's root.
  - `--git-dir`/`--work-tree`/`GIT_DIR=` detection is COARSE (a whole-command substring check, not
    scoped to the specific invocation that carries it) — a command containing any of these anywhere
    forces every push candidate in that command to block, which can over-block a compound command
    where the override applies to an unrelated git invocation. This check runs BEFORE the
    `.publication.toml` adoption check (see `_judge_invocation`), so it fires in a non-adopted repo
    too, not only an adopted one: root-unknown blocks regardless of marker, by design, because a
    repo whose identity can't be pinned down is one whose adoption can't be confirmed either way.
    Accepted: precise per-invocation scoping would require re-deriving the exact global-option
    token span this hook otherwise avoids duplicating from git_command, and over-blocking is the
    safe direction.
  - `remote.<name>.push` is consulted only when the command names its remote explicitly
    (`git push <remote>`, no refspec); a fully bare `git push` with no remote does not resolve the
    implicit default remote to check its `remote.push` config (this hook still checks HEAD's branch
    directly, which covers the common case `remote.push` is meant to override).
  - A hook process that is killed outright (a wall-clock timeout) exits via signal, bypassing the
    try/except below entirely — Task 3's settings.json wiring is responsible for giving this hook a
    generous timeout so a killed-hook window does not become a silent bypass.

Exit codes:
  0 — allow: no git push found, the repo is not adopted, or every push found is unambiguously safe.
  2 — block: EITHER an adopted repo's push targets `dev` (directly, via a tag, or ambiguously) OR
      the repo the push targets could not be determined at all (root-unknown blocks regardless of
      marker, adopted or not) — stderr names this guard so a runbook can grep for it specifically.

Internal-error diagnostics. A block from the INTERNAL-error branch is categorically different from
the two above: it is a BUG in this guard, not a judgement about the command, and it is indisputably
reachable — it fired twice on 2026-07-29 against ordinary non-push commands. Because that branch
used to report only the exception's class name, each occurrence was undiagnosable by construction
(six reproduction attempts came back clean for want of the input). It now appends the offending
command verbatim, plus cwd, size, sha256 and traceback, to `~/.claude/logs/` — override with
$PUBLICATION_PUSH_GUARD_LOG, which every test that drives this branch MUST set so suite runs do not
bury a rare genuine fault under synthetic records. Recording can never change the verdict: it is
contractually non-raising, and a failure to record is reported on stderr rather than swallowed.

THE AMBIGUITY MARKER GETS ITS OWN MESSAGE. The tokenizer signals a reading of the command it could
not parse in-band, as a synthetic invocation (`git_command._indeterminate_invocation`) whose
subcommand is outside `KNOWN_SAFE_SUBCOMMANDS` and whose `effective_dir` is None. That is a
deliberate over-read and it correctly blocks — but it lands in `_judge_invocation`'s
unresolvable-root arm, so until now the refusal read "the effective working directory could not be
resolved (cd/pushd target unknown)", a sentence about a `cd` the command does not contain. Measured
on the witness `bash <<'(x'` / `echo hello` / an `x=$` line ending in a backslash / `(x`, which
carries no cd, no push and no git word. `Block.ambiguous_reading` routes it to wording that names
the real cause.

Two later corrections to that routing, both measured:

* The precedence is per RECORD, not per command. It used to be `ambiguous_reading and not is_push`
  in `main`, where `is_push` is `carries_push` — true for ANY literal push including one this guard
  allows. So the same witness with `git push origin main` appended (a push that is rc=0 on its own)
  came back "refusing to push private 'dev' … the effective working directory could not be resolved
  (cd/pushd target unknown)": two false claims in one sentence. `_find_block_reason` now holds a
  marker refusal back and keeps walking, so a real refusable record still wins when there is one.
* The marker is a string an OPERATOR CAN TYPE, so the subcommand test alone cannot tell a
  synthesized record from `git '$<ambiguous-heredoc-reading>'`. The raw command text settles it —
  a synthesized marker corresponds to no command text by construction.

And the REMEDY the refusal prints is chosen from why the reading was lost (`guard_ambiguity`): the
marker has two causes, a reading that RAISED and an exhausted parse BUDGET, and the fixed sentence
this guard used to print named the one that does not occur in practice.

A DEADLINE THIS PROCESS OWNS. `settings.json` registers this hook with a 60 s `timeout`; a hook
that outlives its registration is killed silently, its output discarded, and the guarded command
RUNS. One command under `MAX_COMMAND_LENGTH` was measured at 224.89 s here (172.40 s on shipped
`dev`). `main` therefore arms a 50 s deadline of its own whose handler `os._exit(2)`s — see
`scripts/lib/guard_deadline.py`, including why it cannot be a raise.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import os
import re
import subprocess
import sys
import traceback
from pathlib import Path
from types import ModuleType
from typing import NamedTuple

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import guard_ambiguity  # noqa: E402
import guard_deadline  # noqa: E402

HOOK_NAME = "publication-push-guard.py"

PREFIX = "publication-push-guard:"


class Block(NamedTuple):
    """A refusal, plus WHICH question produced it. All values block — this is not a severity.

    `is_push` is True only when a push was actually identified (a literal `push`, or an alias
    chain that resolves to one). It is False when the guard could not judge the command at all:
    an unresolvable root, an unknown cwd, a `--git-dir` override, a subcommand that is not a
    literal name.

    It exists because one message used to prefix every reason with "refusing to push private
    'dev'", including reasons that had found no push. That is not cosmetic: a refusal phrased as
    a push decision, on a command carrying no push, is exactly what teaches an operator to answer
    it with an override — the reflex the push rules exist to prevent. The inverse matters just as
    much, which is why this is a two-axis split and not a rename: `git --git-dir=$X/.git push
    origin dev` IS a push and is unjudgeable, so it must keep the alarming wording.

    `boundary_unverifiable` is a THIRD, orthogonal category, set by two call sites in
    `_find_block_reason` for the same underlying reason: the refusal is a statement about the
    GATE, not about the target, so neither of the two `is_push` messages fits.

    - An `_hook_integrity_reason` refusal fires only after `_judge_push` already returned None,
      i.e. always on an allowlisted target, so `is_push` is correctly True here — the invocation
      was a real one — yet the "refusing to push private 'dev'" wording is still wrong, because
      the target being refused is not private. Flipping `is_push` to False would be equally
      wrong: that branch says "no push was identified", and one was.
    - A config/env-injection refusal (`_config_injection_reason`'s inline env/`-c`/`config` arm,
      or `_exported_injection_reason`'s command-scoped arm) is refused whatever the command goes
      on to do, before `_judge_push` ever runs — so `is_push` can legitimately be either value
      here, and BOTH of the two-axis messages are wrong regardless: measured, an inline
      `GIT_COMMON_DIR=` ahead of an allowlisted target claimed a private-branch refusal on that
      allowed target (`is_push=True` case), and an inline denied name ahead of an unrelated
      subcommand claimed the guard "could not judge" a command it had in fact judged completely
      (`is_push=False` case).

    Neither of the two existing messages fits either case, so this is its own boolean rather than
    a third value squeezed onto `is_push` — the boundary could not be verified, so no publish
    from this repo can be judged, which is a statement about the gate, not about the target.

    `ambiguous_reading` is a FOURTH category, and the one refusal none of the three above can
    phrase honestly. The tokenizer signals a reading of the command it could not parse in-band, as
    a synthetic invocation whose subcommand is `AMBIGUOUS_READING_SUBCOMMAND` and whose
    `effective_dir` is None; that lands in `_judge_invocation`'s unresolvable-root arm, so the
    refusal came out as "the effective working directory could not be resolved (cd/pushd target
    unknown)" — a sentence about a `cd` for a command that contains no `cd`, no push and, on the
    measured witness, no git word at all. It is the same class of mislabel the two booleans above
    exist to prevent, one level further in: the reason is true of the SYNTHETIC record and false of
    the command the operator wrote. It is its own flag rather than a value on `is_push` because the
    two are orthogonal here.

    "A command carrying a real refusal AND a lost reading keeps the alarming wording" survives, but
    NOT through `is_push`: that is `carries_push`, true for any literal push including one this
    guard ALLOWS, so gating on it printed a private-'dev' refusal for a command whose only push
    targets `main`. The precedence is per RECORD, in `_find_block_reason`, which holds a marker
    refusal back and keeps walking — see the PRECEDENCE note there.

    `lost_cause` is set alongside it, and only ever reads the trails of the walk that already
    happened. It picks the REMEDY printed with the refusal (`guard_ambiguity`) and decides nothing.
    """

    reason: str
    is_push: bool
    boundary_unverifiable: bool = False
    ambiguous_reading: bool = False
    # WHY a reading was lost, for `ambiguous_reading` refusals only: one of
    # `guard_ambiguity`'s UNREADABLE / TRUNCATED / both / UNDETERMINED. It picks the
    # REMEDY the refusal prints and nothing else -- no verdict here varies with it, which
    # is the contract `iter_git_invocations_with_readings` states for its trails.
    lost_cause: str = guard_ambiguity.UNDETERMINED


def _explain_tool_clause() -> str:
    """The sentence naming THIS guard's copy of the explain tool, or "" on any failure.

    Non-raising by contract, like everything else reached from a block-emitting path here: an
    exception escaping would exit 1, which `scripts/HOOKS.md` treats as noise rather than a veto,
    so the guarded command would RUN. It needs no `git_command` import, which is why it is
    separate from `_ambiguity_detail` rather than inlined there — this guard deliberately keeps
    that module out of module scope, and the marker branch in `main` has no exception to describe.
    """
    try:
        tool = Path(__file__).resolve().parent / "explain-git-command.py"
        return f" For the full parse, run: python3 {tool} -"
    except Exception:  # noqa: BLE001 - deliberate: a broken diagnostic must never unblock
        return ""


def _ambiguity_detail(exc: BaseException | None) -> str:
    """The shared location clause, plus THIS guard's path to the explain tool.

    The location logic lives in the tokenizer (`describe_ambiguity`) so both guards share one
    non-raising implementation rather than two that can drift; the tool path is per-caller
    (`_explain_tool_clause`), since a repo-relative string resolves to nothing from the deployed
    config farm.
    """
    try:
        # Import lazily, exactly as `_find_block_reason` does — this guard deliberately keeps
        # `git_command` out of module scope so an ImportError is caught and BLOCKS rather than
        # crashing at load. By the time this runs the module is already imported (a ParseAmbiguity
        # came from it), so this is a cache hit; and if it somehow is not, the except below
        # degrades to "" rather than letting an ImportError escape the block-emitting handler.
        sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
        import git_command as gitcmd  # noqa: E402

        clause = gitcmd.describe_ambiguity(exc)
        if not clause:
            return ""
        return f"{clause}{_explain_tool_clause()}"
    except Exception:  # noqa: BLE001 - deliberate: a broken diagnostic must never unblock
        return ""


class AmbiguousCommand(ValueError):
    """The WALK could not parse the command — designed ambiguity, not a fault in this guard.

    Deliberately raised only around `iter_git_invocations_with_cwd`. A bare `except ValueError` in
    `main` is too broad and was measured to regress: the suite forces a ValueError out of the lazy
    `git_command` import to exercise the internal-error path, and a blanket catch swallowed it —
    silencing the diagnostic log for a genuine fault. Narrowing to the walk is what separates
    "your command is unreadable" from "this guard broke".
    """


# Where a fail-closed INTERNAL error records what it was evaluating. Outside every repo on purpose:
# a path under the guard's own directory would drop an untracked file into the repo being guarded,
# where it would show up in `git status` and in audit sweeps. `PUBLICATION_PUSH_GUARD_LOG`
# overrides it (the suite relies on that to avoid writing to the real one).
LOG_ENV_VAR = "PUBLICATION_PUSH_GUARD_LOG"
DEFAULT_LOG_PATH = (
    Path.home() / ".claude" / "logs" / "publication-push-guard-errors.log"
)

# Cheap pre-filter: a standalone "git" word anywhere in the command. Deliberately broad — it
# only decides whether the more expensive,
# fail-closed evaluation below is worth entering, never whether to block. Matched against the
# DEQUOTED view of the command (see _dequote below), not the raw string — a raw-text match would
# miss a quote-split `gi''t`/`g""it`/`gi\t`, which shlex (and a real shell) collapse to `git` before
# execution, so a raw-only check could fail-OPEN on an obfuscated push. Dequoting only removes
# characters, so it can reveal a hidden `git` but can never manufacture one that isn't there.
GIT_WORD_RE = re.compile(r"(?:^|[^A-Za-z0-9_])git(?:[^A-Za-z0-9_]|$)")

# Coarse, whole-command detection of a repo-root override. See the "COARSE" residual above. Also
# matched against the DEQUOTED view (see _dequote below) so a quote-split `--git-di''r=`/
# `GIT_DI''R=` can't hide a real override from this check — same reasoning as GIT_WORD_RE.
GITDIR_RE = re.compile(r"--git-dir|--work-tree|(?<![A-Za-z0-9_])GIT_DIR=")

# Shell quoting/escaping metacharacters stripped to produce the dequoted view GIT_WORD_RE and
# GITDIR_RE actually match against. This mirrors (approximately, and only in the direction that
# matters here) the quote-removal a real shell — and this hook's own shlex tokenizer — performs
# before a command runs: `'`, `"`, and `\` are removed outright, so `gi''t` reads as `git` and
# `--git-di''r=` reads as `--git-dir=`. It is intentionally NOT a full shell-quoting parser (it does
# not track quote state, so it also collapses quote characters that a real shell would keep as
# literal content); that is safe here because dequoting is used only to WIDEN these two detectors,
# never to decide what actually gets tokenized/executed — removing extra characters can make a
# detector fire on something it previously missed, never suppress a real match, so over-collapsing
# only trades a few more false blocks for zero missed obfuscated pushes.
_DEQUOTE_CHARS = str.maketrans("", "", "'\"\\")

# A real git subcommand is a literal name. A token that is not — `$s`, `"$@"`, `${x}` — cannot be
# resolved statically at all, so it is UNRESOLVABLE, never "not a push". Treating an unresolvable
# subcommand as benign was a fail-open, and a purely asymmetric one: the refspec slot one token to
# the right already fails closed on exactly this ambiguity (see _valid_plain_name), so
# `git push origin "$B"` blocked while `git $s origin dev` sailed through.
LITERAL_SUBCOMMAND_RE = re.compile(r"^[A-Za-z][A-Za-z0-9-]*$")


def _dequote(command: str) -> str:
    """Strip quote/backslash characters so quote-split obfuscation reads as the plain word it
    resolves to at shell-execution time. Used only to feed GIT_WORD_RE/GITDIR_RE — see their
    comments and _DEQUOTE_CHARS above for why this is safe as a widen-only transform."""
    return command.translate(_DEQUOTE_CHARS)


# git subcommands that are never `push` and never worth an alias-config lookup (the common case,
# kept fast). Anything NOT in this set — including genuinely unknown words — is treated as a
# possible custom alias and resolved via `git config alias.<sub>` once the repo is confirmed
# adopted (see _find_block_reason). "push" itself is deliberately absent: it is always a candidate.
KNOWN_SAFE_SUBCOMMANDS = frozenset(
    {
        "status",
        "commit",
        "add",
        "rm",
        "mv",
        "fetch",
        "pull",
        "log",
        "diff",
        "show",
        "branch",
        "checkout",
        "switch",
        "restore",
        "reset",
        "stash",
        "tag",
        "clone",
        "init",
        "config",
        "remote",
        "merge",
        "rebase",
        "cherry-pick",
        "revert",
        "blame",
        "grep",
        "describe",
        "shortlog",
        "reflog",
        "gc",
        "fsck",
        "clean",
        "submodule",
        "worktree",
        "apply",
        "am",
        "format-patch",
        "send-email",
        "bisect",
        "archive",
        "bundle",
        "notes",
        "request-pull",
        "verify-commit",
        "verify-tag",
        "whatchanged",
        "help",
        "version",
        # Added 2026-08-03. Each was measured MISSING while its neighbours were present, so
        # `git -C "$live" show-ref` was refused where `git -C "$live" rev-parse` passed — an
        # unexpanded `-C` makes the root unresolvable, and only the short-circuit above spares
        # a subcommand from that. The refusal then blamed a push the command never made, which
        # is what trains the operator to answer it with the override.
        #
        # Safe on BOTH counts required here, not one: (a) git ignores an alias that shadows a
        # known git command — measured for builtins AND shipped scripts — so none can be a
        # disguised push; and (b) none pushes to a git remote itself. Membership in
        # `git --list-cmds=main` buys only (a); `svn` and `p4` are members too and DO publish.
        "show-ref",
        "count-objects",
        "var",
        "show-branch",
        "cherry",
        "merge-tree",
        "pack-refs",
        "rerere",
        "stripspace",
        "replace",
        "rev-parse",
        "rev-list",
        "symbolic-ref",
        "merge-base",
        "ls-files",
        "ls-remote",
        "ls-tree",
        "cat-file",
        "diff-index",
        "diff-tree",
        "name-rev",
        "update-ref",
        "update-index",
        "write-tree",
        "read-tree",
        "commit-tree",
        "hash-object",
        "prune",
        "repack",
        "fast-export",
        "fast-import",
        "instaweb",
        "mergetool",
        "difftool",
        "annotate",
        "range-diff",
        "sparse-checkout",
        "maintenance",
        "credential",
        "credential-cache",
        "credential-store",
        "for-each-ref",
        "check-ignore",
        "check-attr",
        "check-ref-format",
    }
)

# Publishing/plumbing subcommands that can move content to a remote WITHOUT going through `push`'s
# refspec judgment below and WITHOUT running the `pre-push` hook (layer 2) — `send-pack` is the
# transport `push` itself invokes, so a direct call skips this guard's refspec allowlist entirely.
# None of these is EVER a candidate for KNOWN_SAFE_SUBCOMMANDS: absence from that set is not a
# block (an unrecognised subcommand falls through to `_resolve_alias_chain`, which allows at
# depth 0 when it is not a configured alias — measured: `git send-pack`, `git svn dcommit`,
# `git p4 submit`, `git daemon --export-all` all exit 0 against the shipped guard). Consulted
# before `_resolve_alias_chain` is even called (see `_judge_invocation`), so a custom alias
# literally NAMED one of these is still denied — git runs its own builtin/plumbing command over an
# alias of the same name (see the KNOWN_SAFE_SUBCOMMANDS comment above for the same fact cutting
# the other way), so the alias's target is irrelevant here.
PUBLISHING_SUBCOMMANDS = frozenset(
    {"send-pack", "receive-pack", "upload-pack", "http-backend", "daemon", "svn", "p4"}
)

# `git push` long options that take a value token when not attached via `=`.
PUSH_VALUE_LONG = {"--repo", "--receive-pack", "--exec", "--push-option"}
# Short option characters (after the leading `-`) that take a value token.
PUSH_VALUE_SHORT = {"o"}

# A ref side must be this shape once `refs/heads/`/`refs/tags/` is stripped and any leading `+`
# force-marker removed: plain identifier characters only — no `~`, `^`, `@`, `*`, `?`, `:`, `[`,
# `\`, which is exactly what excludes revision suffixes, wildcards, and `@{...}` forms.
PLAIN_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")
SHA_RE = re.compile(r"^[0-9a-fA-F]{7,40}$")


def _valid_plain_name(name: str) -> bool:
    """True if name is an unambiguous plain ref name (not a SHA, revision expression, or wildcard)."""
    if not name or not PLAIN_REF_RE.match(name):
        return False
    if ".." in name or name.endswith(".lock") or name.endswith("/"):
        return False
    return not SHA_RE.match(name)


def _combine(base: str | None, sub: str | None) -> str | None:
    """Join a possibly-relative `sub` (a `-C`/`cd` target) onto `base`; None propagates (an
    unresolved base or target stays unresolved, never silently defaulting to somewhere else)."""
    if base is None:
        return base
    if not sub:
        return base
    return sub if sub.startswith("/") else str(Path(base) / sub)


def _resolve_root(effective_dir: str) -> str | None:
    """The toplevel of the repo containing effective_dir, or None if it cannot be resolved."""
    out = subprocess.run(
        ["git", "-C", effective_dir, "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    if out.returncode != 0:
        return None
    return out.stdout.strip()


def _git_capture(root: str, *args: str) -> str | None:
    """Stripped stdout of a git command in `root`, or None if it failed."""
    out = subprocess.run(
        ["git", "-C", root, *args],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    return out.stdout.strip() if out.returncode == 0 else None


def _head_branch(root: str) -> str | None:
    """The short name of the branch HEAD points to, or None if detached/unresolvable."""
    out = subprocess.run(
        ["git", "-C", root, "symbolic-ref", "--quiet", "--short", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    if out.returncode != 0:
        return None
    return out.stdout.strip()


def _ref_exists(root: str, ref: str) -> bool:
    out = subprocess.run(
        ["git", "-C", root, "rev-parse", "--quiet", "--verify", ref],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    return out.returncode == 0


def _tag_reachable_from_main(root: str, tag_name: str) -> bool:
    """True only if refs/tags/<tag_name> resolves AND its commit is an ancestor of `main`. A repo
    with no `main` branch, or an unresolvable tag, is NOT reachable (fail closed — we cannot prove
    safety, so we do not assume it)."""
    commit_out = subprocess.run(
        [
            "git",
            "-C",
            root,
            "rev-parse",
            "--quiet",
            "--verify",
            f"refs/tags/{tag_name}^{{commit}}",
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    if commit_out.returncode != 0:
        return False
    commit = commit_out.stdout.strip()
    ancestor = subprocess.run(
        ["git", "-C", root, "merge-base", "--is-ancestor", commit, "main"],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    return ancestor.returncode == 0


def _classify_ref(name: str, root: str) -> tuple[str, str] | None:
    """Classify one side of a refspec as ("branch", short_name) or ("tag", short_name); None means
    ambiguous/unresolvable (the caller must block)."""
    if name == "HEAD":
        branch = _head_branch(root)
        return ("branch", branch) if branch else None
    if name.startswith("refs/heads/"):
        rest = name[len("refs/heads/") :]
        return ("branch", rest) if _valid_plain_name(rest) else None
    if name.startswith("refs/tags/"):
        rest = name[len("refs/tags/") :]
        return ("tag", rest) if _valid_plain_name(rest) else None
    if not _valid_plain_name(name):
        return None
    if _ref_exists(root, f"refs/heads/{name}"):
        return ("branch", name)
    if _ref_exists(root, f"refs/tags/{name}"):
        return ("tag", name)
    return None  # a plain, syntactically valid name that names nothing we can find — unresolvable


def _refspec_blocks(spec: str, root: str) -> bool:
    """True if this single refspec must block: `dev` appears (as a branch, on either side) or
    resolves as a tag NOT reachable from `main`, or the refspec is ambiguous in any way."""
    body = spec[1:] if spec.startswith("+") else spec
    if not body:
        return True  # e.g. a bare "+" or ":" — ambiguous
    src, _, dst = body.partition(":")
    if ":" not in body:
        dst = src
    sides = []
    for side in (src, dst):
        if not side:
            return True  # an empty side (a delete refspec) — treated conservatively as ambiguous
        classified = _classify_ref(side, root)
        if classified is None:
            return True
        sides.append(classified)
    for kind, name in sides:
        if kind == "branch" and name == "dev":
            return True
        if kind == "tag" and not _tag_reachable_from_main(root, name):
            return True
    return False


def _remote_push_blocks(root: str, remote: str) -> bool:
    """True if remote.<remote>.push configures a dev-spanning (or ambiguous) refspec."""
    out = subprocess.run(
        ["git", "-C", root, "config", "--get-all", f"remote.{remote}.push"],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    if out.returncode != 0:
        return False  # not configured — no opinion, the caller falls back to the HEAD check
    return any(
        _refspec_blocks(line.strip(), root)
        for line in out.stdout.splitlines()
        if line.strip()
    )


def _tags_block(root: str) -> bool:
    """True if any local tag (the set --tags/--follow-tags would sweep) is not main-reachable."""
    out = subprocess.run(
        ["git", "-C", root, "tag", "--list"],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    if out.returncode != 0:
        return True  # can't enumerate tags — can't prove safety
    tags = [t.strip() for t in out.stdout.splitlines() if t.strip()]
    return any(not _tag_reachable_from_main(root, t) for t in tags)


def _split_push_args(args: list[str]) -> tuple[set[str], list[str]]:
    """Split the tokens after `push` into (flag names seen, positional args) — the positional args
    are `[<remote> [<refspec>...]]` per git's own grammar.

    A clustered short option (`-fo blah`, `-vo blah`) must consume its value the same way git does:
    scanning stops at the first value-taking char in the cluster — if it's the LAST char, the next
    token is its value (`-fo` + `blah`); if chars follow it in the same token, those chars ARE the
    attached value (git's `-o<value>` form, e.g. `-oci.skip`) and no next-token is consumed. Getting
    this wrong lets a value token (like `blah` above) fall through as a positional, which shifts the
    real remote into the refspec slot and can route a bare push into the explicit-refspec branch —
    skipping the current-branch-is-dev check entirely. Modeled on recast-commit-gate.py's
    `parse_scope` COMMIT_VALUE_SHORT cluster handling."""
    flags: set[str] = set()
    positional: list[str] = []
    i, n = 0, len(args)
    while i < n:
        t = args[i]
        if t == "--":
            positional += args[i + 1 :]
            break
        if t.startswith("--") and len(t) > 2:
            name = t.split("=", 1)[0]
            flags.add(name)
            if name in PUSH_VALUE_LONG and "=" not in t:
                i += 1  # consume the value token (space-separated long form)
        elif t.startswith("-") and len(t) > 1:
            cluster = t[1:]
            c = 0
            while c < len(cluster):
                ch = cluster[c]
                flags.add("-" + ch)
                if ch in PUSH_VALUE_SHORT:
                    if c == len(cluster) - 1:
                        i += 1  # value is the next token (e.g. `-fo blah`)
                    break  # any remaining chars are the attached value — stop scanning
                c += 1
        else:
            positional.append(t)
        i += 1
    return flags, positional


def _judge_push(root: str, args: list[str]) -> str | None:
    """The core allowlist: a push is safe only if every refspec it carries is an unambiguous
    non-dev plain branch, or a tag reachable from main, and it carries none of the sweep flags this
    hook cannot evaluate precisely. Returns a block reason, or None if safe."""
    flags, positional = _split_push_args(args)

    if "--all" in flags:
        return "'--all' sweeps every local branch — ambiguous, cannot rule out dev"
    if "--mirror" in flags:
        return "'--mirror' mirrors the whole local ref set — ambiguous, cannot rule out dev"

    if len(positional) >= 2:
        remote, refspecs = positional[0], positional[1:]
        for spec in refspecs:
            if _refspec_blocks(spec, root):
                return f"refspec '{spec}' targets, or ambiguously might target, 'dev'"
    else:
        remote = positional[0] if positional else None
        if remote is not None and _remote_push_blocks(root, remote):
            return f"remote.{remote}.push configures a refspec that targets 'dev'"
        branch = _head_branch(root)
        if branch is None:
            return (
                "HEAD is detached or unresolvable on a bare push — cannot rule out dev"
            )
        if branch == "dev":
            return "a bare push resolves to the current branch 'dev'"

    if ("--tags" in flags or "--follow-tags" in flags) and _tags_block(root):
        return "'--tags'/'--follow-tags' would sweep a tag not reachable from main"

    return None


def _resolve_alias_chain(
    root: str, sub: str, seg: list[str], gitcmd: ModuleType, max_depth: int = 10
) -> tuple[str, list[str] | None]:
    """Chase `git config alias.<X>` recursively — the way real git resolves an alias chain — with a
    depth cap and cycle guard standing in for git's own loop-abort (real git aborts on an alias
    loop; 10 hops is generous headroom for any legitimate chain).

    Args:
        root: The repo toplevel to consult `git config alias.<X>` in.
        sub: The initial subcommand token to resolve.
        seg: The argument tokens following `sub` (folded into diagnostics on a `push` result).
        gitcmd: The lazily-imported `git_command` module, supplying the tokenizer.
        max_depth: Hop cap before a too-deep chain fails closed.

    Returns:
        A ``(kind, args)`` tuple, one of:
          ("none", None)  — `sub` is not configured as an alias at all: not a push, allow.
          ("safe", None)  — the chain resolves (at any depth) to a subcommand in
                             KNOWN_SAFE_SUBCOMMANDS: allow.
          ("push", args)  — the chain resolves (at any depth) to `push`. The caller blocks this
                             UNCONDITIONALLY — reproducing git's own alias-argument substitution well
                             enough to prove such a push safe is not attempted (see module
                             docstring); `args` is returned only for diagnostics.
          ("block", None) — the chain is a shell alias (`!...`), unparseable, cyclic, exceeds
                             max_depth, or a hop lands on a subcommand that is itself neither a
                             resolvable alias nor a known-safe built-in: ambiguous, fails closed.
    """
    current_sub, current_args = sub, seg
    seen: set[str] = set()
    depth = 0
    while True:
        if current_sub == "push":
            return "push", current_args
        if current_sub in KNOWN_SAFE_SUBCOMMANDS:
            return "safe", None
        if not LITERAL_SUBCOMMAND_RE.match(current_sub):
            # `git $s`, `git "$@"`, `git ${x}`: the subcommand only exists after expansion, so it
            # can be neither recognized nor resolved. Ambiguous -> fail closed. Without this, the
            # `git config alias.<X>` lookup below returns rc!=0 and depth==0 reports "none"
            # (not an alias, therefore not a push) — allowing an invocation that may well be one.
            return "block", None
        if current_sub in seen or depth >= max_depth:
            return "block", None  # cycle, or chain too deep to trust
        seen.add(current_sub)
        out = subprocess.run(
            ["git", "-C", root, "config", "--get", f"alias.{current_sub}"],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        if out.returncode != 0:
            # Not configured as an alias. At depth 0 that means `sub` itself was never an
            # alias — not a push, allow. At any deeper hop, it means the chain landed on a
            # subcommand we can neither recognize as safe nor resolve further — ambiguous.
            return ("none", None) if depth == 0 else ("block", None)
        value = out.stdout.strip()
        if not value or value.startswith("!"):
            return "block", None
        try:
            toks = gitcmd.tokenize(value)
        except ValueError:
            return "block", None
        if not toks:
            return "block", None
        alias_sub, alias_args = toks[0], toks[1:]
        current_sub, current_args = alias_sub, alias_args + current_args
        depth += 1


# The tracked copy of the boundary hook this repo ships (see git-hooks/pre-push and
# scripts/install-git-hooks.sh). `_hook_integrity_reason` compares the INSTALLED copy's bytes
# against this working-tree file to detect a stale or hand-edited install.
TRACKED_HOOK_RELPATH = "git-hooks/pre-push"

# The installer every per-cause remedy below either names or presupposes. A single constant so
# the five call sites cannot drift from one another (or from the tracked-source comment above).
INSTALL_SCRIPT_RELPATH = "scripts/install-git-hooks.sh"

# Appended to every remedy `_hook_integrity_reason` returns. This gate does not read the
# ALLOW_PUSH=1 override at all (see the module docstring), and an integrity refusal is exactly
# the case where reaching for it is most tempting -- the refspec itself is fine.
_NO_OVERRIDE_NOTE = "ALLOW_PUSH=1 will not help."


def _hook_integrity_reason(root: str) -> tuple[str, str] | None:
    """Why the boundary is not in force at `root`, or None when it is.

    Returns a ``(reason, remedy)`` pair instead of a bare string. The single call site used to
    append ONE remedy ("reinstall with scripts/install-git-hooks.sh") to whichever of this
    function's causes fired -- wrong for a relocated `core.hooksPath` (reinstalling cannot undo
    a relocation) and impossible in principle for a missing tracked source (the installer's own
    input is the missing file). Each cause below carries the remedy that is actually reachable
    from it.

    Checked in a fixed order, and the order is load-bearing, not incidental. The full ordering is
    `1 < 2 < S < 5 < {3, 4, 6}` -- cause 1 is the unresolvable-path branch just above, 2 is
    containment, S is the symlink cause, 5 is the missing tracked source, and 3/4/6 are the
    install-state causes below that:

    - The symlink flag (cause S) is COMPUTED before `.resolve()` is trusted -- `.resolve()`
      follows symlinks, so probing afterward would always read False and this cause would be
      unreachable, indistinguishable from a dead line. But computing early does not require
      REPORTING first: cause S's remedy ("remove the symlink, then reinstall") is unreachable
      when the hooks path is ALSO relocated, because `scripts/install-git-hooks.sh` resolves its
      destination through `git rev-parse --git-path hooks`, which honours a relocated
      `core.hooksPath` -- so reinstalling lands right back in the decoy and the command stays
      refused. Reporting containment (cause 2) FIRST closes that: its own remedy already covers
      both a relocated `core.hooksPath` and a symlinked hooks *directory*, so cause S is reported
      only once containment has already PASSED -- exactly the case its call site names, a
      symlinked hook FILE whose target resolves inside the repository's own hooks directory.
      Narrower than containment on its own terms too: a symlinked hooks directory holding a real
      regular hook file leaves the leaf `is_symlink()` probe False (see the containment cause's
      wording below) and falls through to containment on its own, never reaching cause S at all.
    - The tracked-source cause comes AFTER containment and the symlink cause but BEFORE the
      install-state causes (missing / not executable / digest mismatch), because every
      install-class remedy presupposes that source existing -- but it must not be hoisted all the
      way to the top: a checkout with both a missing source and a relocated hooks path must not be
      told "update the checkout, then install", which cannot clear a relocation either.

    This reorder is message-selection only: the function still blocks iff any cause holds, so
    the refusal SET is invariant under it -- only which text gets printed changes.

    Three facts observed WITHOUT the boundary's cooperation, so no spelling of any
    command evades it: git's own resolution of the hook path (which already honours
    core.hooksPath -- measured), that the file is executable, and that its bytes match
    the tracked source.

    The digest reads the WORKING TREE copy, unlike `_repo_is_adopted` (Task 4), which
    deliberately does not. Safe only because it fails in the BLOCKING direction: a
    checkout without `git-hooks/pre-push` refuses, where the adoption predicate under
    the same conditions would allow. Never invert this to allow-on-missing.
    """
    resolved = _git_capture(root, "rev-parse", "--git-path", "hooks/pre-push")
    common = _git_capture(
        root, "rev-parse", "--path-format=absolute", "--git-common-dir"
    )
    if resolved is None or common is None:
        return (
            "the hook path could not be resolved",
            "confirm .git exists here and that ordinary git commands run in this repo -- git "
            f"itself could not resolve its own hooks path. {_NO_OVERRIDE_NOTE}",
        )
    try:
        hook_raw = Path(root, resolved)
        # Probed on the RAW (unresolved) path -- see the ordering note in the docstring above.
        is_hook_symlink = hook_raw.is_symlink()
        hook = hook_raw.resolve()
        common_dir = Path(common).resolve()
    except OSError:
        return (
            "the hook path could not be resolved",
            "confirm .git exists here and that ordinary git commands run in this repo -- git "
            f"itself could not resolve its own hooks path. {_NO_OVERRIDE_NOTE}",
        )
    if common_dir not in hook.parents:
        # Reported BEFORE cause S, even though `is_hook_symlink` was already computed above --
        # see the ordering note in the docstring. A symlink whose resolution lands outside the
        # repo's own hooks directory (this branch) needs the containment remedy, which covers a
        # relocated `core.hooksPath` -- cause S's remedy (remove the symlink, reinstall) cannot
        # clear a relocation, because the installer's own destination resolution honours it too.
        return (
            f"git resolves the boundary hook to {hook}, outside the repository's own hooks "
            "directory. This can happen because core.hooksPath has been relocated, or because "
            ".git/hooks itself is a symlinked directory pointing elsewhere",
            "find where core.hooksPath is set (git config --show-origin --get "
            "core.hooksPath) and unset it in that scope; if .git/hooks is itself a symlinked "
            "directory, point it back at a real directory holding the tracked hook. "
            f"{_NO_OVERRIDE_NOTE}",
        )
    if is_hook_symlink:
        # Reachable ONLY here, now that containment has already passed: the symlink's target
        # resolves inside the repository's own hooks directory, so unsetting core.hooksPath
        # (cause 2's remedy) would do nothing -- there is no relocation to undo, just a symlink
        # standing in for what should be a real file. See the call-site comment above for the
        # motivating shape (a byte-identical target inside .git/hooks, installed but dead).
        return (
            f"the installed boundary hook at {hook_raw} is a symlink",
            f"remove the symlink at {hook_raw} yourself, then run {INSTALL_SCRIPT_RELPATH} -- "
            "the installer refuses to install over a symlink even with --force, so removing it "
            f"by hand comes first. {_NO_OVERRIDE_NOTE}",
        )
    tracked = Path(root, TRACKED_HOOK_RELPATH)
    if not tracked.is_file():
        return (
            f"the tracked hook source {TRACKED_HOOK_RELPATH} is missing from this checkout",
            "this checkout predates the publication boundary; fetch and check out a revision "
            f"carrying {TRACKED_HOOK_RELPATH} and {INSTALL_SCRIPT_RELPATH}, then run the "
            f"installer -- fetching is not a publish, so nothing here blocks it. "
            f"{_NO_OVERRIDE_NOTE}",
        )
    if not hook.is_file():
        return (
            f"the boundary hook is missing at {hook}",
            f"run {INSTALL_SCRIPT_RELPATH}, or copy {TRACKED_HOOK_RELPATH} to {hook} by hand "
            f"and chmod +x it. {_NO_OVERRIDE_NOTE}",
        )
    if not os.access(hook, os.X_OK):
        return (
            f"the boundary hook at {hook} is not executable",
            f"chmod +x {hook}, or re-run {INSTALL_SCRIPT_RELPATH}. {_NO_OVERRIDE_NOTE}",
        )
    if (
        hashlib.sha256(hook.read_bytes()).hexdigest()
        != hashlib.sha256(tracked.read_bytes()).hexdigest()
    ):
        return (
            f"the installed boundary hook at {hook} does not match {TRACKED_HOOK_RELPATH}",
            f"run {INSTALL_SCRIPT_RELPATH} --force -- the installer refuses to overwrite a "
            f"differing regular file without --force. {_NO_OVERRIDE_NOTE}",
        )
    return None


_BOUNDARY_FLAG = "--no-verify"


def _boundary_disabling_flag(flags: set[str]) -> str | None:
    """The flag name in `flags` that would make git skip the boundary hook, or None.

    Matches `--no-verify` and any abbreviation of it down to `--no-v`, because git parses this
    subcommand's options through `parse-options`, which accepts unambiguous long-option
    abbreviations INCLUDING negated forms -- measured on git 2.55, where `git branch --no-colo` is
    accepted. A literal `"--no-verify" in flags` would miss `--no-veri`.

    Over-blocking a prefix git would call AMBIGUOUS costs nothing, because git refuses those
    itself; `--no-v` and `--no-ver` are in fact ambiguous here, since this subcommand also has
    `-v/--verbose` and therefore an auto-negated `--no-verbose`. So this deliberately does NOT
    enumerate the other `--no-*` options to compute a minimum unambiguous length: that would be a
    second blocklist, and it would go stale the next time git grows an option.

    `--no-verbose` is excluded by construction -- it is not a prefix of `--no-verify`.
    """
    for name in flags:
        if name.startswith("--no-v") and _BOUNDARY_FLAG.startswith(name):
            return name
    return None


def _judge_invocation(
    effective_dir: str | None,
    sub: str,
    seg: list[str],
    gitcmd: ModuleType,
    gitdir_override: bool,
) -> Block | None:
    """Judge one git invocation. Returns a Block, or None to allow it.

    `Block.is_push` records WHICH question was answered — see the type. It is not a severity: both
    values block. It exists so the refusal cannot claim a push it never found, and it is derived
    from `sub`, which is in scope at every unjudgeable site.
    """
    # ABOVE the adoption test, and above the root resolution, ON PURPOSE. This gate is registered
    # globally, and a flag whose entire effect is "the boundary hook shall not run" is judged the
    # same everywhere: it is the one class where this layer's incompleteness has NO backstop,
    # because the backstop is precisely what is being removed. Note what this does NOT claim --
    # a command that changes the FACTS the hook evaluates (moving `main` with update-ref, branch,
    # reset, merge) is ordinary work and is deliberately not refused here.
    #
    # Placed inside `_judge_push` this would sit BELOW the dormant return a few lines down and
    # silently become adoption-scoped, while every adopted-cwd corpus row still passed. The
    # `boundary_bypass_flag_outside_adopted_repo` row exists to fail if anyone moves it there.
    # This position is also free: no subprocess has run yet.
    if sub == "push":
        disabling = _boundary_disabling_flag(_split_push_args(seg)[0])
        if disabling is not None:
            return Block(
                f"'{disabling}' makes git skip the boundary hook, which is the gate this check "
                "exists to keep in force. Publish through the promote path, or take it by hand "
                "in your own terminal.",
                True,
            )
    if gitdir_override:
        return Block(
            "the command carries --git-dir/--work-tree or a GIT_DIR= assignment — root unknown",
            sub == "push",
        )
    if effective_dir is None:
        return Block(
            "the effective working directory could not be resolved (cd/pushd target unknown)",
            sub == "push",
        )

    root = _resolve_root(effective_dir)
    if not root:
        # Not `root is None`: `_resolve_root` cannot currently return "" (measured — a bare
        # repo's `--show-toplevel` exits 128, which already yields None), so this is defensive
        # hardening, not a live path. It closes the same hole `_repo_is_adopted_root`'s own `if
        # not root: return True` already closes at ITS call site, one call earlier: an empty
        # root must never reach a `git -C ""` subprocess, which git documents as leaving the
        # working directory UNCHANGED — silently judging whatever repo this process happens to
        # be sitting in, rather than refusing outright.
        #
        # Name the commonest cause when the evidence is right here in the token: a hook sees
        # command text UNEXPANDED, so `-C "$live"` is four literal characters and can never
        # resolve. Saying so turns an inscrutable refusal into a one-word fix.
        looks_unexpanded = "$" in effective_dir
        detail = (
            f"the repo root could not be resolved from {effective_dir!r}, which is an "
            "unexpanded shell variable — a hook sees command text before the shell expands it. "
            "Pass -C a literal path"
            if looks_unexpanded
            else "the repo root could not be resolved"
        )
        return Block(detail, sub == "push")
    # Refs-based, matching git-hooks/pre-push's own is_dormant(). The working-tree test this
    # replaces disagreed with the hook exactly where it mattered: a linked worktree, a checkout of
    # a pre-adoption commit, or a plain `rm` flips it while the hook stays ARMED. This is the
    # SWAP, not a disjunction with the old test -- they differ at fresh adoption (marker present,
    # committed nowhere), and there the disjunction would arm this layer while the hook stays
    # dormant, which is the disagreement this change exists to remove.
    #
    # `root` cannot be empty here -- the `if not root:` check above already refused it, closing
    # an empty string at THIS call site directly. `_repo_is_adopted_root`'s own `if not root:
    # return True` guard is not redundant, though: it is still load-bearing for its OTHER caller,
    # `_repo_is_adopted`, which the config-injection call site in `_find_block_reason` uses and
    # which never runs through this function at all.
    if not _repo_is_adopted_root(root):
        return None  # not adopted — dormant

    if sub != "push":
        if sub in PUBLISHING_SUBCOMMANDS:
            # Checked BEFORE alias resolution, deliberately: an alias named e.g. `send-pack` must
            # still be denied (see PUBLISHING_SUBCOMMANDS's comment), so this cannot be folded into
            # the "safe"/"push"/"block" alias-chain result below it.
            return Block(
                f"'{sub}' is a git publishing/plumbing subcommand — it can move content to a "
                "remote directly, running through neither `push`'s refspec allowlist nor the "
                "pre-push hook. This is not a push this guard can evaluate, so ALLOW_PUSH=1 will "
                "not help.",
                False,
            )
        kind, _chain_args = _resolve_alias_chain(root, sub, seg, gitcmd)
        if kind == "none":
            return None  # not an alias at all — not a push
        if kind == "safe":
            return None  # chain resolves (possibly through multiple hops) to a known-safe built-in
        if kind == "push":
            # _chain_args is diagnostics only — see _resolve_alias_chain: never re-verified.
            return Block(
                f"subcommand '{sub}' resolves (via an alias chain) to 'push' — alias-based "
                "pushes are always blocked, never re-verified against the refspec allowlist",
                True,
            )
        return Block(
            f"subcommand '{sub}' resolves to an unverifiable or unresolvable alias chain",
            False,
        )

    reason = _judge_push(root, seg)
    if reason is not None:
        return Block(reason, True)
    integrity = _hook_integrity_reason(root)
    if integrity is not None:
        integrity_reason, integrity_remedy = integrity
        return Block(
            # NOT "is not in force" -- that is true of six of the seven causes but FALSE of a
            # symlinked hook that resolves to a good, digest-matching file inside the repo's own
            # hooks directory: git follows it and the boundary really does run. Measured. That
            # shape is refused anyway (the installer refuses symlinks even under --force, because
            # a DANGLING one is silently ignored), but it is refused as a state this guard will
            # not clear, not as an absent boundary. Overclaiming here would re-create, at this new
            # cause, exactly the false-attribution defect the per-cause split exists to remove.
            f"the publication boundary is not in a state this guard will clear: "
            f"{integrity_reason}. {integrity_remedy}",
            is_push=True,
            boundary_unverifiable=True,
        )
    return None


# Config keys that relocate or silence the LOAD-BEARING gate. `core.hooksPath` points git at a
# different hooks directory (`/dev/null` disables the lot); `include.path` and `include-if.*.path`
# pull in a file that can set either. Compared case-INSENSITIVELY because git config section and
# key names are case-insensitive — `core.hookspath` and `CORE.HOOKSPATH` both work, and a
# case-sensitive check would read as coverage while missing two trivial spellings.
DENIED_CONFIG_KEYS = ("core.hookspath", "include.path", "includeif.")

# `git config` actions that address NO named key: they operate on a whole SECTION, or on the
# config file itself, so the key the operation ultimately writes never appears as a token.
# Measured 2026-09-08 against the live guard with valid controls -- all eight spellings of
# these three were ALLOWED by the denied-key scan alone, including two the backlog entry that
# scheduled this fix never named: the `rename-section` / `remove-section` / `edit` SUBCOMMAND
# words that `git config -h` documents first, and a rename whose target is `core`, which
# reaches the hooks path DIRECTLY rather than through an include.
_CONFIG_UNSAFE_ACTIONS = ("edit", "rename-section", "remove-section")

# Short forms of those actions. git documents `-e` for `--edit`, and its parser BUNDLES short
# flags -- `git config -ze` is accepted (measured) -- so a whole-token match is not enough.
_CONFIG_UNSAFE_SHORT = frozenset("e")

# Short flags that take NO value, so the character after them is another flag rather than the
# start of a value. Scanning a bundle has to stop at the first character outside this set,
# because `-f` takes its value ATTACHED: `-f/home/user/.gitconfig` is one token whose tail is a
# PATH, not flags.
#
# Measured 2026-09-08, and this is the whole reason the set exists: an earlier draft matched
# any single-dash token CONTAINING `e` and would have refused `-f/home/user/.gitconfig`, a
# legitimate and common write, because the path happens to contain the letter. A diverse-model
# review found the attached form unpinned; the over-block was worse than the missing row.
#
# Deliberately NOT a general arity model -- `_config_scope_is_local`'s docstring rules that out,
# and this is not one. It is a stop condition: an unrecognised character ends the scan, so a
# short flag added by a future git release stops it early rather than being misread as a
# bundle. That direction is the residual named in the spec, and rule 2 still backstops it.
_CONFIG_VALUELESS_SHORT = frozenset("elz")

# The `-c`/`--config-env` arm denies one key the `git config` seg scan must NOT: `alias.`.
#
# A `-c alias.<n>=<command>` value is per-invocation in FORM but arbitrary in EFFECT — the aliased
# command runs with full privileges. Measured, both allowed before this was added:
# `git -c alias.zz='config --global core.hooksPath /x' zz` wrote the global file, and
# `git -c alias.zz='<publish> origin dev' zz` reached the private branch through a gate that never
# saw a publish, because `sub` resolves to `zz` and never `config`, while `_resolve_alias_chain`
# looks the alias up in the repo's PERSISTED config, which cannot see one defined by this
# invocation's own `-c`.
#
# Scoped to THIS arm deliberately: `DENIED_CONFIG_KEYS` is shared with the seg scan below, where
# adding `alias.` would start refusing an ordinary `git config alias.co checkout`.
DENIED_C_KEYS = DENIED_CONFIG_KEYS + ("alias.",)

_CONFIG_READ_ACTIONS = frozenset({"get", "list"})
_CONFIG_READ_FLAGS = frozenset(
    {
        "--get",
        "--get-all",
        "--get-regexp",
        "--get-urlmatch",
        "--get-color",
        "--get-colorbool",
        "--list",
        "-l",
    }
)

# Options that take NO following argv token as a value, so encountering one must not end the
# scan below. Everything NOT in this set, and not carrying its value ATTACHED via `=`, is
# assumed to consume the next token -- the safe direction, since misjudging a value-taking
# option as value-less would read that value as if it were the next flag and could, in
# principle, misread a read flag sitting in the value position.
# The real hazard measured here runs the other way: `--comment` (NOT in this set, correctly)
# swallows the very next token, so `git config --comment --get core.hooksPath /dev/null` has its
# `--get` consumed as `--comment`'s value rather than read as the read flag it looks like.
_CONFIG_VALUELESS_OPTS = frozenset(
    {
        "--global",
        "--local",
        "--system",
        "--worktree",
        "--includes",
        "--no-includes",
        "--show-origin",
        "--show-scope",
        "--name-only",
        "--null",
        "-z",
        "--fixed-value",
        "--all",
        "--regexp",
        "--bool",
        "--int",
        "--path",
        "--bool-or-int",
        "--bool-or-str",
        "--expiry-date",
    }
)


def _config_read_flag_matches(base: str) -> bool:
    """True if `base` is a `_CONFIG_READ_FLAGS` member, or an unambiguous-enough prefix of one.

    git accepts any unambiguous abbreviation of a long option -- measured 2026-09-08 and new
    breakage against dev (319edac): `git config --li`, `--lis`, `--get-reg branch` and
    `--get-regex alias` were all ALLOWED there as abbreviations of `--list` / `--get-regexp`,
    and started wrongly BLOCKing once `_CONFIG_READ_FLAGS` required an exact spelling. It was
    also arbitrary in the interim -- `--get-r core.editor` allowed while `--get-reg core`
    blocked, purely because the first happened to carry a dotted argument rule 2's key-
    visibility scan could see.

    Mirrors rule 1's own `_CONFIG_UNSAFE_ACTIONS` prefix match in `_config_action_is_unjudgeable`
    (`any(n.startswith(stem) for n in ...)`): an AMBIGUOUS stem is one git itself refuses to run
    before this gate's answer can have any effect, so treating it as a read here costs nothing.
    """
    if base in _CONFIG_READ_FLAGS:
        return True
    if not base.startswith("--"):
        return False
    stem = base[2:]
    return bool(stem) and any(
        name.startswith("--") and name[2:].startswith(stem)
        for name in _CONFIG_READ_FLAGS
    )


def _config_is_read(seg: list[str]) -> bool:
    """True only when `git config`'s LEADING option run marks this a read.

    POSITION IS LOAD-BEARING. git stops parsing options at the first non-option
    token, so a read flag after the key is consumed as a positional and the WRITE
    still lands: `git config core.hooksPath --get` was measured to write
    `core.hooksPath = --get`, relocating the hooks path. A position-insensitive rule
    allows exactly that.

    Measured 2026-09-08 and FALSE until this fix: this was claimed to only ever turn a read into
    a WRITE (over-block), never the reverse. `git config --comment --get core.hooksPath
    /dev/null` refutes it -- `--comment` takes its value ATTACHED to the next argv token
    (confirmed, git 2.55), so it swallows the following `--get` as its OWN value rather than
    leaving it to be read as the read flag it looks like; the scan that stopped at the first
    unrecognised option therefore quit before ever seeing a read flag, read the whole invocation
    as a write, and the arm below it wrote `core.hooksPath`. Same mechanism `_config_scope_is_
    local`'s docstring already records for `--comment note --global` breaking THAT scan; this is
    its mirror on the read side. There are TWO paths that let the scan continue past a token
    without stopping it, not one: `_CONFIG_VALUELESS_OPTS` membership is one, and a token
    carrying its value ATTACHED via `=` is the other -- an attached value consumes no following
    argv token, so nothing about it can swallow a read flag the way `--comment`'s detached form
    does. `--comment=note --get core.hooksPath /dev/null` and `--type=bool --get core.bare` both
    continue past their leading option on the `=` path and correctly reach `--get`, returning
    True. Anything else -- no `=` and not a `_CONFIG_VALUELESS_OPTS` member -- STOPS the scan on
    sight, classifying the invocation as not-a-read -- the safe direction, since a wrong
    not-a-read verdict still falls through to rule 1 and the denied-key scan below rather than
    being waved through. This does NOT skip an unrecognised option's own next token and keep
    scanning past it: that would require modelling each option's ARITY, which
    `_config_scope_is_local`'s docstring already rules out for its own scan, for the mirror
    reason -- misjudging a value-taking option as value-less would skip a real read flag sitting
    right after it. Stopping is the one direction here with no such mirror failure mode.
    """
    for tok in seg:
        if tok == "--":
            return False
        if not tok.startswith("-"):
            return tok in _CONFIG_READ_ACTIONS  # the subcommand form's action word
        base = tok.split("=", 1)[0]
        if _config_read_flag_matches(base):
            return True
        if "=" not in tok and base not in _CONFIG_VALUELESS_OPTS:
            return False
    return False


def _looks_like_config_key(tok: str) -> bool:
    """A token shaped like a config key: dotted, and not starting like an option or a path.

    Discriminates on the LEADING CHARACTER, never on containing a slash. A config key's
    SUBSECTION may legitimately contain one -- `branch.feature/x.remote`,
    `submodule.vendor/lib.url`, and the per-URL `http.<url>.*` and `credential.<url>.*` forms
    are all real, and all verified accepted by git 2.55. Rejecting a slash-bearing token
    discards the only key such a write names, so rule 2 below refuses it for want of a key.
    That over-block lands hardest in this repo, whose branch convention puts a slash in every
    branch name, making `git config branch.<current-branch>.remote` a slash key by
    construction. A diverse-model review caught it; the first draft of this helper shipped it.

    The exclusion that remains is narrow and is over-block avoidance with a small fail-closed
    benefit, NOT a safety property -- do not promote it to one. A dangerous write naming no key
    is an unsafe ACTION, caught by rule 1; a dangerous write naming a key names a DENIED key,
    caught by the unchanged scan below, which does not consult this function at all. What the
    leading-character test still buys is that an absolute path value (`-f /tmp/some.cfg`)
    cannot stand in as the visible key for some future action nobody here has heard of. A
    relative dotted path can still do so; that residual is named in the spec, not closed.
    """
    return "." in tok and not tok.startswith(("-", "/", "~", "."))


def _config_short_bundle_hit(base: str, targets: frozenset[str]) -> bool:
    """True if any of `targets` appears in this single-dash bundle before an attached-value stop.

    Walks `base[1:]` one character at a time and stops at the first character outside
    `_CONFIG_VALUELESS_SHORT` -- everything from there on is that flag's ATTACHED value (a
    path, typically), not more bundled flags. `_CONFIG_VALUELESS_SHORT`'s own comment names the
    reason this stop condition exists at all: `-f/tmp/local.cfg` must not read the `l` in the
    path as a bundled `-l`.

    Shared by rule 1 below (`targets=_CONFIG_UNSAFE_SHORT`) and rule 2's read-flag standdown
    (`targets=frozenset("l")`), so both use the SAME bundle-walk rule rather than two matching
    styles. Before this helper, rule 2's standdown matched with a naive `"l" in base[1:]`
    substring scan -- the exact defect rule 1 was built to avoid, reintroduced one rule below it.
    Measured 2026-09-08: `git config -f/tmp/abc.cfg --future-keyless-action` BLOCKed while
    `git config -f/tmp/local.cfg --future-keyless-action` ALLOWed, differing only in whether the
    attached path happens to spell the letter `l` (as in `local`, `global`, `lib`, `.claude`,
    `.gitmodules`).
    """
    for ch in base[1:]:
        if ch in targets:
            return True
        if ch not in _CONFIG_VALUELESS_SHORT:
            return False
    return False


def _config_action_is_unjudgeable(seg: list[str]) -> str | None:
    """Why the denied-key scan cannot judge this `git config` WRITE, or None if it can.

    TWO rules, and NEITHER is redundant -- deleting either re-opens a measured bypass.

    Rule 1 refuses an action that addresses no named key. Rule 2 refuses a write in which no
    `section.key` is visible at all, which is what makes this arm fail CLOSED on an action
    added by a git release nobody here has heard of.

    Rule 2 alone is NOT sufficient, and the case that proves it is
    `git config rename-section a.b include`: the dotted token `a.b` satisfies rule 2, while
    the dangerous half of that command is the rename TARGET, which is never a key. Rule 1
    alone is not fail-closed. Each covers exactly what the other cannot.

    Models NO option arity, deliberately. `_config_scope_is_local`'s docstring records why:
    skipping a flag's value requires knowing which flags take one, and misjudging that skips a
    real flag. Both loops here visit every token and cannot skip. The cost is over-blocking a
    VALUE that spells an action word or an action flag -- the safe direction, and the same
    residual that function already accepts for scope words appearing as values.
    """
    for tok in seg:
        if tok == "--":
            break
        base = tok.split("=", 1)[0].lower()
        if base.startswith("--"):
            stem = base[2:]
            if stem and any(n.startswith(stem) for n in _CONFIG_UNSAFE_ACTIONS):
                return (
                    f"it runs `git config {base}`, an action addressing no named key, so this "
                    "gate cannot see which key the write lands on"
                )
        elif base.startswith("-") and len(base) > 1:
            if _config_short_bundle_hit(base, _CONFIG_UNSAFE_SHORT):
                return (
                    f"it runs `git config {base}`, a short form of an action addressing "
                    "no named key, so this gate cannot see which key the write lands on"
                )
        elif base in _CONFIG_UNSAFE_ACTIONS:
            return (
                f"it runs `git config {base}`, an action addressing no named key, so this "
                "gate cannot see which key the write lands on"
            )

    # Rule 2 stands down in the presence of a read flag. With `_CONFIG_VALUELESS_OPTS` in place
    # (see `_config_is_read`), an invocation carrying a read flag and no dotted key cannot be a
    # WRITE at all -- there is nothing here for rule 2 to refuse for want of a key, and standing
    # down leaves the denied-key scan below to run, which is what blocks a real write disguised
    # behind a swallowed read flag (`--comment --get core.hooksPath /dev/null`: `--get` here is
    # `--comment`'s consumed value, but it is still the LITERAL token `--get`, so this scan sees
    # it and stands down -- exactly the invocations `_config_is_read` no longer misreads as pure
    # reads now still reach the denied-key scan instead of being refused here for naming no key).
    for tok in seg:
        base = tok.split("=", 1)[0]
        if _config_read_flag_matches(base):
            return None
        if base.startswith("-") and not base.startswith("--"):
            if _config_short_bundle_hit(base, frozenset("l")):
                return None

    if not any(_looks_like_config_key(tok) for tok in seg):
        return "it is a `git config` write in which this gate can see no `section.key` to judge"
    return None


# Env assignments that redirect where `git config` WRITES. Measured: both landed a
# write in an unrelated repo's config from a cwd with no relationship to it.
#
# These three are ALSO denied upstream, by the env-allowlist arms (see
# `_env_name_is_cleared`), so this set is no longer the only thing standing between them and a
# redirected write -- it answers a different question (which SCOPE does this `git config` write
# target) and is kept for that. Do not fold the two together: an earlier, narrower env matcher
# missed bare `GIT_CONFIG` and `GIT_COMMON_DIR` entirely, which is the F2 finding, and the presence
# of those names HERE is what made that gap read as already covered.
#
# EXPECTED MUTATION SURVIVORS, recorded so the next campaign is not misread. Because the env arms
# now deny every non-allowlisted name BEFORE `_config_scope_is_local` is reached, its loop
# over this set can no longer return False on any input that arrives through `_find_block_reason`.
# A campaign will therefore report mutations to that loop as SURVIVED. **That is not licence to
# delete it.** The obvious reading -- "dead code, remove it" -- is a NARROWING: the function is
# still called directly by `test_publication_push_guard.sh`, and the arm above it is the only
# thing making the loop unreachable, so removing this set would silently re-open the scope
# classification the moment that arm is ever narrowed. Survivor, not corpse.
_CONFIG_REDIRECT_ENV = frozenset({"GIT_CONFIG", "GIT_COMMON_DIR", "GIT_DIR"})

# git accepts any UNAMBIGUOUS ABBREVIATION of each file-location flag, plus `-f` and
# the attached `--file=` form -- measured: `--glo` wrote the GLOBAL file. Prefix
# matching against these names is therefore required; exact spellings are not enough.
_CONFIG_SCOPE_NAMES = ("global", "system", "local", "worktree", "file", "blob")


def _config_scope_is_local(seg: list[str], env: list[str]) -> bool:
    """True only when this `git config` write unambiguously targets the cwd's own repo.

    Scans EVERY token and never breaks early. A leading-run scan was measured wrong:
    `git config --comment note --global core.hooksPath X` writes the GLOBAL file, but
    a scan that stops at the bareword `note` calls it local. Modelling option arity
    would fix that and introduce the mirror error -- misjudging a flag as
    value-taking would skip a real `--global`. Scanning everything cannot skip, and
    its only error is over-blocking a scope word that appears as a VALUE.
    """
    for assignment in env:
        if _assign_name(assignment) in _CONFIG_REDIRECT_ENV:
            return False
    for tok in seg:
        base = tok.split("=", 1)[0]
        if base == "-f":  # documented short form of --file
            return False
        if not base.startswith("--"):
            continue
        negated = base.startswith("--no-")
        stem = base[5:] if negated else base[2:]
        if not stem:
            continue
        hits = [n for n in _CONFIG_SCOPE_NAMES if n.startswith(stem)]
        if not hits:
            continue  # not a file-location flag at all
        if hits == ["local"] and not negated:
            continue  # positively and only --local
        return False
    return True


def _git_batch_check(root: str, stdin_data: str) -> str | None:
    """Like `_git_capture`, but for `git cat-file --batch-check`, which takes its object list on
    STDIN rather than argv -- the one genuinely new subprocess call `_repo_is_adopted_root` needs
    beyond `_git_capture` itself. Same `timeout=10` and None-on-any-failure contract; unlike
    `_git_capture` this does not `.strip()` the output, because the caller needs it split into
    lines (one verdict per probed ref) and a leading/trailing blank line changes nothing there."""
    out = subprocess.run(
        ["git", "-C", root, "cat-file", "--batch-check"],
        input=stdin_data,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    return out.stdout if out.returncode == 0 else None


def _repo_is_adopted_root(root: str) -> bool:
    """Whether SOME ref under refs/heads/ carries a tracked `.publication.toml`.

    Mirrors `git-hooks/pre-push`'s own `is_dormant()`. Takes an already-resolved root so the push
    arm does not re-run `_resolve_root` a second time.

    Two subprocesses regardless of branch count: the per-branch `cat-file -e` loop this replaces
    ran 1+N spawns, and after this predicate moved onto the push arm (every git invocation in every
    repo on this machine, not just a push) that N-spawn cost lands hardest on non-adopted repos,
    since the short-circuit only fires once a marked branch is found. Slowness is itself a bypass
    here: past the hook's own timeout the process is killed by signal and never reaches its own
    fail-closed handler.

    FAIL-CLOSED: unresolvable means True.
    """
    if not root:
        # NOT `git -C ""`: git documents that as leaving the working directory UNCHANGED, so every
        # subprocess below would silently judge whatever repo the hook process happens to be
        # sitting in, rather than failing. This explicit guard is what closes the empty-root case
        # -- the swap to refs-based detection does not close it on its own.
        return True
    refs_out = _git_capture(root, "for-each-ref", "--format=%(refname)", "refs/heads/")
    if refs_out is None:
        return True
    refs = refs_out.splitlines()
    if not refs:
        return False
    probe = "\n".join(f"{r}:.publication.toml" for r in refs) + "\n"
    out = _git_batch_check(root, probe)
    if out is None:
        return True
    return any(line and not line.endswith("missing") for line in out.splitlines())


def _repo_is_adopted(effective_dir: str | None) -> bool:
    """Thin wrapper over `_repo_is_adopted_root`, for the existing config-injection call site in
    `_find_block_reason`, which has an `effective_dir` (a `cd`/`-C` combination, not yet a
    resolved root).

    FAIL-CLOSED: unresolvable means True -- unchanged from before this became a wrapper. `config`
    is in KNOWN_SAFE_SUBCOMMANDS and never reaches `_judge_invocation`'s own root-unknown block, so
    returning False here would allow the invocation outright.
    """
    if effective_dir is None:
        return True
    root = _resolve_root(effective_dir)
    if root is None:
        return True
    return _repo_is_adopted_root(root)


# Environment-assignment names cleared to reach a git invocation. An ALLOWLIST over the WHOLE
# namespace, not a `^GIT_` shape rule -- the shape rule is what failed. It cleared every
# non-GIT_ name, so `HOME=`, `XDG_CONFIG_HOME=`, `PATH=` and `LD_PRELOAD=` were waved through;
# measured full-stack, a hostile HOME moved the hooks path, the boundary hook did not run, and
# the push SUCCEEDED (rc=0, against rc=1 with a clean env).
#
# THE PROPERTY each member satisfies, stated ADDITIVELY: the name cannot make git read a config
# source, run a hook, act on a repository, or execute a program it otherwise would not.
# (`GIT_CONFIG_NOSYSTEM` only ever REMOVES a source, which is why the additive phrasing matters.)
# Values are not examined. Membership is ENUMERATED -- never a prefix (`ALLOW_*`) or a shape.
#
# DENIED by not being here, which is the whole point: HOME, XDG_CONFIG_HOME (relocate the global
# config, hence core.hooksPath); PATH, LD_PRELOAD, DYLD_* (replace or subvert the binary);
# EDITOR, VISUAL, GIT_EDITOR, GIT_SSH*, GIT_EXEC_PATH (name a program git executes); GIT_DIR,
# GIT_COMMON_DIR, GIT_CONFIG*.
#
# NEVER reuse or extend `_CONFIG_REDIRECT_ENV`: it answers where `git config` WRITES, and the
# overlap is coincidental. See its own comment.
_ENV_ALLOWLIST = frozenset(
    {
        # -- repo control plane: read by THIS repo's hooks, never by git ------------
        # ALLOW_PUSH is a HARD CONSTRAINT: the authorization override CLAUDE.md prescribes,
        # 776 uses across real transcripts. Omitting it makes authorized pushing impossible.
        "ALLOW_PUSH",
        "ALLOW_GIT_WRITE",
        "ALLOW_NONEXEC",
        "ALLOW_LONG_SUBJECT",
        "PUBLICATION_PUSH_GUARD_LOG",
        # -- data only -------------------------------------------------------------
        "GIT_AUTHOR_NAME",
        "GIT_AUTHOR_EMAIL",
        "GIT_AUTHOR_DATE",
        "GIT_COMMITTER_NAME",
        "GIT_COMMITTER_EMAIL",
        "GIT_COMMITTER_DATE",
        "GIT_TERMINAL_PROMPT",
        "GIT_CONFIG_NOSYSTEM",
        # Locale/timezone. Checked before clearing: the boundary hook parses no localized
        # text, so a locale cannot alter what it decides.
        "TZ",
        "LANG",
        "LC_ALL",
        "LC_COLLATE",
        "LC_CTYPE",
        "LC_MESSAGES",
        "LC_TIME",
        # -- pager class -----------------------------------------------------------
        # These NAME a command git executes, cleared on measured ground rather than by feel:
        # git does not page without a TTY on stdout and a PreToolUse hook's commands never have
        # one. Verified with a pty.fork() POSITIVE control; `-p`, `--paginate` and
        # `-c pager.<cmd>=` were each measured NOT to defeat it. The justification is
        # ENVIRONMENTAL, not intrinsic -- these are the first entries to revisit if a hook ever
        # sees a TTY.
        "PAGER",
        "GIT_PAGER",
    }
)

# Names still refused by SHAPE wherever a construct CANNOT put the name in git's environment.
# There are TWO such places, not one:
#
#   1. an assignment run scoped as a prefix to another command word (`DEBUG=1 npm test && git
#      status`) -- bash scopes it to that command and it never persists; and
#   2. a bare or declare-family assignment whose NAME does not already carry the export
#      attribute, with allexport off (`x=1 ; git status`, `declare -r N=1 ; git status`).
#
# The second clause is the one that is easy to state wrongly. An earlier draft of this comment
# said "a bare assignment in another segment, with allexport off" full stop -- FALSE, and
# measured so: `HOME=/x ; git <push>` is exactly that shape and BLOCKS, because HOME already
# carries the export attribute and therefore reaches. Reach is a property of the NAME, not of the
# construct; see `_name_carries_export_attribute`.
#
# Judging the unreachable cases against the full allowlist instead would refuse `x=1 ; git
# status` -- measured, with `set -e` and command substitution, as a mass false-block class. The
# `^GIT_` refusal here is a deliberate conservative over-block that predates this change and that
# the corpus pins (assigned_but_never_exported_is_also_blocked); it costs no new false blocks and
# no new false allows.
_UNREACHABLE_DENY_RE = re.compile(r"^GIT_")


def _env_name_is_cleared(name: str, *, reaches_git_env: bool) -> bool:
    """Whether `name` is cleared, given whether the construct puts it in git's environment.

    `reaches_git_env=True` -- the construct really does put `name` in git's environment. Judged
    against `_ENV_ALLOWLIST`: cleared ONLY if enumerated there, whatever the name's shape. The
    cases, all measured against real bash:
      - an inline prefix on the invocation (`HOME=/x git <verb>`);
      - `export NAME=v` (unless `-f`, which names a function), or `declare`/`typeset` WITH `-x`;
      - any bare or declare-family assignment while allexport is on;
      - any bare or declare-family assignment whose NAME already carries the export attribute --
        `HOME=/x ; git <verb>` reaches with no `export` and no allexport at all.

    `reaches_git_env=False` -- the construct cannot reach git, so only the pre-existing `^GIT_`
    over-block applies. Two cases: an assignment run scoped as a prefix to another command word
    (bash scopes it there and it never persists), and a bare or declare-family assignment of a
    name that does NOT already carry the attribute, with allexport off (`x=1 ; git status`,
    `declare -r N=1 ; git status`).

    Do not restate either list as "a bare assignment with allexport off". That was this
    docstring's own earlier wording and it is false: it describes the construct when the rule is
    about the NAME.

    ONE function, so "which names are safe" is still answered in exactly one place. The reach is
    an explicit PARAMETER rather than a second frozenset: the two populations are genuinely
    different, and collapsing them either reopens the bypass or refuses ordinary shell.
    """
    if not reaches_git_env:
        return not _UNREACHABLE_DENY_RE.match(name)
    return name in _ENV_ALLOWLIST


def _config_injection_reason(
    tokens, sub: str, seg: list[str]
) -> tuple[str, bool] | None:
    """Detect an attempt to relocate or disable the git-native `pre-push` boundary.

    Scoped to ONE invocation's own env prefix, global-option run and (for `config`) its argument
    segment — never the whole command string. That scoping is what makes a read-only
    `grep -n 'core.hooksPath=/dev/null' git-hooks/pre-push` impossible to false-block *by
    construction* rather than by a carefully-worded regex: a `grep` command has no git invocation in
    command position, so there are no tokens here to match. The previous design's whole-command
    regex false-blocked exactly that command, because its `git`-word prefilter matched the path
    token `git-hooks`.

    Returns `(reason, unconditional)`, or None when this invocation carries no such attempt.
    `unconditional` is True for the env and `-c`/`--config-env` arms — see the module's revision
    note for why: a `-c` value is per-invocation, but the ALIASED COMMAND it can run is arbitrary
    (`git -c alias.zz='config --global core.hooksPath /x' zz` writes the global file), so that
    arm's blast radius is not per-invocation and gating it on adoption would reopen the hole this
    detector exists to close. `GIT_CONFIG_COUNT`/`_KEY_n`/`_VALUE_n` can define an alias the same
    way. It is False only for the `config` arm, whose write actually IS scoped by
    `_config_scope_is_local` (see `_find_block_reason`, which decides adoption-gating from this
    flag together with `gitdir_override`).
    """
    for assignment in tokens.env:
        name = _assign_name(assignment)
        if _env_name_is_cleared(name, reaches_git_env=True):
            continue  # on _ENV_ALLOWLIST -- see _env_name_is_cleared
        return (
            f"it sets {name}, which is not on this gate's environment allowlist -- an "
            "assignment reaching a git invocation can relocate the config git reads, the "
            "hooks it runs, or the binary that runs as git. Either drop the prefix, or add "
            f"{name} to _ENV_ALLOWLIST with its property class recorded",
            True,
        )

    opts = list(tokens.opts)
    for idx, opt in enumerate(opts):
        if opt == "-c":
            # Only the SEPARATED form. There is deliberately no attached `-c<key>=<value>` arm:
            # git does not accept that spelling (measured, git 2.55 — rc 129, "unknown option:
            # -cfoo.bar=baz"), so `classify_global_opt` classifies the token UNKNOWN, the
            # invocation is unjudgeable, and the walk's caller refuses it before this detector
            # runs. A branch for it existed here and was INERT — it read as coverage while unable
            # to fire, and the refusal it never produced was credited to it. What forces that
            # outcome is pinned in the tokenizer's own suite, since this comment is not a check.
            value = opts[idx + 1] if idx + 1 < len(opts) else ""
        elif opt.startswith("--config-env"):
            # `--config-env=key=envvar` takes its VALUE from the environment, so the key is
            # visible here but what it will be set to is not. Unresolvable => block.
            return (
                "it uses --config-env, whose value this gate cannot resolve statically",
                True,
            )
        else:
            continue
        if "=" not in value:
            # `-c "$K"` / `-c $(…)`: shlex has already stripped the quotes, so what arrives is
            # whatever survived expansion — never a literal `key=value`. Named as a residual in
            # the threat model rather than pretended to be caught.
            return (
                "it passes -c a value this gate cannot resolve to a literal key=value",
                True,
            )
        key = value.split("=", 1)[0].strip().lower()
        if any(key.startswith(denied) for denied in DENIED_C_KEYS):
            return (
                f"it sets {key} via -c, which can relocate or disable the hooks path, "
                "or (for an alias) run an arbitrary git command this gate never sees",
                True,
            )

    if sub == "config":
        # `git config --local core.hooksPath /dev/null && <a push>` is a TWO-command bypass: the
        # first is a `config` (in KNOWN_SAFE_SUBCOMMANDS, so otherwise waved through) and the
        # second is a push the refspec rule may well allow on its own. Blocking the config half is
        # what makes the pair unreachable, so this must run BEFORE the known-safe shortcut.
        #
        # The read carve-out sits INSIDE this branch only, never as a whole-detector short-circuit
        # — otherwise `git -c core.hooksPath=X config --get y` would bypass the `-c` arm above.
        if _config_is_read(seg):
            return None
        unjudgeable = _config_action_is_unjudgeable(seg)
        if unjudgeable is not None:
            return (unjudgeable, not _config_scope_is_local(seg, list(tokens.env)))
        for arg in seg:
            if any(arg.strip().lower().startswith(d) for d in DENIED_CONFIG_KEYS):
                return (
                    f"it writes {arg} via `git config`, which can disable the push boundary",
                    not _config_scope_is_local(seg, list(tokens.env)),
                )
    return None


# Command words whose ARGUMENTS are variable names (`export FOO=1`, `export FOO` naming an
# already-assigned var, `declare -x FOO=1`).
#
# `set` is deliberately ABSENT, and this branch is what REMOVED it -- `git show dev:` gives
# `frozenset({"export", "declare", "typeset", "set"})`, so it was a member until now. (An earlier
# draft of this comment said it "never was in the set below": false, and the kind of history claim
# that is cheap to check and easy to assert.) `set` names no variable -- `set -e`, `set --`,
# `set -- a b` name none -- and treating its arguments as names is what made
# `git config set rerere.enabled true` refuse, blaming 'rerere.enabled'. What `set` CAN do is
# switch ALLEXPORT on (`set -a`, `set -o allexport`), a MODE affecting later bare assignments,
# tracked separately. The corpus row exported_injection_set_allexport already said exactly this
# in prose -- "set -a exports bare assignments that follow; it assigns nothing itself" -- while
# the code did the opposite.
#
# `readonly` and `local` joined `declare`/`typeset` here: they take names as arguments the same
# way and, like declare/typeset, do NOT export by shape alone. Whether an assignment through any
# of them reaches git's environment is a property of the NAME (see
# `_name_carries_export_attribute` below), not of the construct.
#
# Kept separate from `WRAPPERS`/`GIT_ONLY_WRAPPERS` in git_command.py: those mark the WORD AFTER
# them as still being in command position (`sudo git …`); these mark everything AFTER them, up to
# the next segment separator, as an argument to the export construct itself -- a different
# relationship, not a stronger version of the same one.
_EXPORT_WORDS = frozenset({"export", "declare", "typeset", "readonly", "local"})
_SET_WORD = "set"

# Wrappers that run the BUILTIN in the CURRENT shell, so an export-family word behind one still
# exports. ENUMERATED from measurement against real bash, not guessed -- and the enumeration was
# run twice, because the first pass missed a member and shipped a live hole:
#
#   EXPORT:      command, builtin, time, eval   (also `command -p`, and nesting to any depth)
#   DO NOT:      nohup, nice, stdbuf, setsid, env, sudo  (they exec an external process, and
#                `export` is not a binary), `source` / `.` (no effect on a following word)
#   `exec export X=1` errors outright ("not found"), so allowing it is correct.
#
# `eval` was the member the first pass missed. `eval export GIT_CONFIG_COUNT=1 ; git <verb>`
# measured dev=BLOCK, branch=ALLOW -- the SAME regression class this set was added to fix,
# reintroduced by an incomplete enumeration one commit later. `eval set -a` enables allexport
# too. It is not the quoted `eval "git push …"` form the module documents as a residual: that one
# is opaque to the tokenizer, this one is fully visible and was simply never considered.
#
# NOT `gitcmd.WRAPPERS`, deliberately. That set answers "where does a git invocation start", and
# now carries `builtin` and `eval` too (fix/eval-wrapper-bypass) -- it no longer leaves either one
# out. But it still carries `nohup`/`nice`/`sudo`/`env`, none of which preserve export, so it
# remains the wrong set to reuse here.
_SHELL_BUILTIN_WRAPPERS = frozenset({"command", "builtin", "time", "eval"})

# `_SEGMENT_SEPARATORS` USED TO LIVE HERE: a literal frozenset of seven operators, with a comment
# claiming it was "deliberately the same operator set `is_op` recognises". It was not, and the
# gap was invisible because the claim read as a design note rather than an assertion.
#
# shlex's `punctuation_chars` GROUPS operator runs, so `)&&`, `)||`, `;;`, `;&` and `;;&` each
# arrive as ONE token and matched none of the seven. Measured consequences, both directions:
#   `(true)&&export GIT_CONFIG_COUNT=1 ; git <verb>`     dev BLOCK -> branch ALLOW  (regression)
#   `case x in (x) HOME=/tmp/h ;; esac ; git <verb>`      allowed on every build     (open class)
#   `case x in (x) export ALLOW_PUSH=1 ;; esac ; …`       branch BLOCKED it, blaming `;;` as a
#                                                        variable name -- refusing the repo's own
#                                                        authorization override
#
# `git_command.is_op` is CHARACTER-based and was written for exactly this: its own tokenizer
# comment warns that `(cd /x && ls)&&git push` arrives as `)&&` and that "a check written against
# the spaced, depth-1 form passes its own test and leaks everywhere else". Both call sites now use
# it, and the duplicate is deleted rather than extended -- a second, weaker spelling of one
# predicate is the defect itself, not a performance convenience. This is the THIRD finding on this
# branch with that shape (`_looks_like_unresolvable_expansion` wired into one of two sites was the
# second); the lesson is to reach for the shared predicate, never to re-state it locally.

# `VAR+=value` (append). `gitcmd.ENV_ASSIGN` now matches this shape too (widened -- see its own
# comment -- to fix a tokenizer fail-open where `FOO+=1 git push` produced ZERO invocations at
# all). This local regex remains because matching the SHAPE is not enough: `token.split("=",
# 1)[0]` on `"FOO+=1"` still yields `"FOO+"`, not `"FOO"`. `_assign_name` below uses this to strip
# the `+` and recover the real name; matching alone is delegated to `gitcmd.ENV_ASSIGN`.
_APPEND_ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*\+=")

# Names whose export attribute this hook may not assume it can see. `os.environ` is a good proxy
# for the shell's exported set -- the hook is a child of the same session -- but it is a PROXY,
# and it goes stale in exactly one direction that matters: a session launched without the
# operator's shell profile (a GUI or IDE launcher) may lack a name the profile exports. These
# four are the ones that relocate what git reads or runs, so they are asserted rather than
# discovered. This is the repo's derive-plus-declared-FLOOR rule applied to REACH, not to safety
# -- which is why it does not put a second safety verdict inside `_env_name_is_cleared`.
_REACH_FLOOR = frozenset({"HOME", "PATH", "XDG_CONFIG_HOME", "LD_PRELOAD"})
_REACH_FLOOR_PREFIX = "DYLD_"


def _name_carries_export_attribute(name: str) -> bool:
    """Whether assigning to `name` would land in a later command's environment.

    Measured: bash keeps the export attribute on assignment, so `HOME=/x` in its own segment
    reaches a later git invocation with no `export` and no allexport, while a FRESH name does
    not. That is a property of the NAME, not of the construct -- which is why `declare`,
    `typeset`, `readonly`, a function-local assignment and `+=` all reach too.
    """
    return (
        name in os.environ
        or name in _REACH_FLOOR
        or name.startswith(_REACH_FLOOR_PREFIX)
    )


def _is_assign_token(token: str, gitcmd: ModuleType) -> bool:
    """Whether `token` is a `VAR=...` or `VAR+=...` assignment shape (the two forms
    `_exported_injection_reason`'s walker must treat as assignments).

    One clause, deliberately. This carried `or _APPEND_ASSIGN_RE.match(token)` while
    `gitcmd.ENV_ASSIGN` still lacked the append form; once that was widened the clause became
    INERT -- verified by enumeration, no token matches the append pattern that `ENV_ASSIGN` does
    not. An inert clause in a security predicate reads as a safety property while unable to
    change any outcome, and a test written around it would pass forever and pin nothing, so it
    is deleted rather than kept "for clarity". `_APPEND_ASSIGN_RE` itself is NOT dead -- it still
    does real work in `_assign_name`, which must strip the `+` the widened regex now admits.
    """
    return gitcmd.ENV_ASSIGN.match(token) is not None


def _assign_name(token: str) -> str:
    """The variable name a `VAR=...` or `VAR+=...` assignment token names."""
    if _APPEND_ASSIGN_RE.match(token):
        return token.split("+=", 1)[0]
    return token.split("=", 1)[0]


def _looks_like_unresolvable_expansion(token: str, gitcmd: ModuleType) -> bool:
    """Whether `token` is shaped like something bash may expand to NOTHING: a command-substitution
    placeholder (`iter_context_token_streams` replaces `$(...)`/`` `...` ``/`<(...)`/`>(...)` with
    one of these before this walker ever sees them) or a bare `$`-led expansion (`$UNSET_VAR`,
    `${X}`, `$((1+1))`). Bash's own rule for a command PREFIX is that if no command name survives
    expansion, the assignment affects the CURRENT shell -- so such a token is not proof a real
    command word follows, the same posture `_config_injection_reason` already takes for `-c "$K"`:
    unresolvable means refuse to guess, not clear it.
    """
    return token.startswith("$") or gitcmd.PLACEHOLDER_PREFIX in token


def _leading_assign_run_is_scoped(
    stream: list[str], i: int, gitcmd: ModuleType
) -> bool:
    """Whether the run of leading `VAR=...`/`VAR+=...` assignments starting at `stream[i]` is a
    PREFIX to a command word within the same simple command, rather than a STANDALONE bare
    assignment with no command word at all.

    Measured against real bash: `HOME=/x true` gives `HOME` only to `true`'s environment and
    does not reach a later `git` invocation, while `HOME=/x` alone in its segment (followed by a
    separator or nothing) persists in the shell and keeps whatever export attribute `HOME`
    already carries. Scans forward only past further assignment-shaped tokens (`VAR1=1 VAR2=2
    cmd` is one prefix run) to the first token that is not one.

    The token that ends the run must also be a token that CANNOT vanish under expansion --
    `HOME=/x $(true) ; git …` and `HOME=/x $UNSET_VAR ; git …` both end their run on a token that
    is statically a "following word" but can expand to nothing, in which case bash gives the
    assignment to the CURRENT shell rather than to a command that never materializes. Such a
    token is therefore not proof of a following command word; see
    `_looks_like_unresolvable_expansion`.
    """
    j = i + 1
    while j < len(stream) and _is_assign_token(stream[j], gitcmd):
        j += 1
    if j >= len(stream):
        return False
    return (
        not gitcmd.is_op(stream[j])
        and stream[j] not in gitcmd.RESERVED_WORDS
        and not _looks_like_unresolvable_expansion(stream[j], gitcmd)
    )


def _exported_injection_reason(
    command: str, invocations: list, gitcmd: ModuleType
) -> str | None:
    """A denied name reaching a git invocation from OUTSIDE its own env prefix.

    `_env_prefix` deliberately collects only the assignments immediately preceding one invocation
    -- the env the shell would apply to THAT command -- and must stay in step with
    `starts_command`. Widening it would break that invariant. So this is a separate,
    command-scoped check for the shapes that prefix walk cannot see: an `export` in another
    segment, an `export NAME` naming an already-assigned variable, and `set -a` / `set -o
    allexport` enabling export for bare assignments.

    Keyed on the NAME, not on a construct shape: `GIT_CONFIG_KEY_0=x; export GIT_CONFIG_KEY_0`
    assigns nothing inside the exporting construct, and `set -a` assigns nothing at all.

    The git-invocation conjunct is a FALSE-POSITIVE CONTROL, not a completeness argument. It keeps
    `grep -n 'export GIT_CONFIG_COUNT=1' foo && git status` allowed. It does NOT establish that an
    injection must share a command with the git invocation -- a shell profile or `.envrc` written
    by one allowed command, or a direct edit to `.git/config` through a non-Bash tool, reaches git
    with an empty inline prefix. Those are named residuals, not closed by this check: see the
    module docstring's "Known, documented residuals" list. (The design record this was drafted
    against claimed this check "covers the env half of this for free" -- it does not: with no
    invocation recorded below, this returns `None` before looking at anything, and conjunct (2)
    cannot be widened to a bare "has a git word" without re-creating the `git-hooks`-path false
    positive `_config_injection_reason`'s own docstring names.)
    """
    if not invocations:
        return None
    claimed = {tok for inv in invocations for tok in inv.tokens.env}
    for stream in gitcmd.iter_context_token_streams(command):
        exporting = False  # command word of the current segment is export-family
        at_prefix = True  # still in the segment's command-prefix position
        in_set = False  # command word of the current segment is `set`
        in_wrapper = False  # inside a wrapper word: its options, not names
        allexport = False  # `set -a` / `set -o allexport` is in effect -- persists across segments
        prev_set_opt = (
            ""  # previous token, to catch the two-token `-o allexport` spelling
        )
        export_exports = False  # the current export-family construct actually exports (not `declare N=1`)
        export_is_functions = (
            False  # `export -f` -- its arguments are function names, not variables
        )
        # Whether the CURRENT run of leading VAR=... assignments has already been classified as
        # scoped (a prefix to a command word) or standalone (Task 3b) -- computed once per run,
        # on its first token, and reused for every assignment token after it in the same run.
        in_assign_run = False
        assign_run_is_scoped = False
        for i, token in enumerate(stream):
            if gitcmd.is_op(token):
                exporting, at_prefix, in_set, prev_set_opt = False, True, False, ""
                in_assign_run = False
                continue
            if token in gitcmd.RESERVED_WORDS:
                # Command-PREFIX position only -- never `exporting`. Folding this into the
                # separator branch above would also clear `exporting`, dropping tokens that
                # reach detection only through it: `export do GIT_CONFIG_COUNT ; git <verb> …`
                # is measured BLOCK -> ALLOW under that fold, because `do` would end the export
                # construct early. A bare assignment right after a reserved word (`if true; then
                # GIT_CONFIG_COUNT=1 ; fi ; git <verb> …`) is the gap this branch closes: the
                # else-branch below used to clear `at_prefix` on the reserved word itself (it is
                # not an assignment), so the assignment immediately after it was never checked.
                at_prefix = True
                in_assign_run = False
                continue
            name = _assign_name(token)
            is_assign = _is_assign_token(token, gitcmd)
            here_at_prefix = at_prefix

            if here_at_prefix and is_assign:
                # A bare VAR=... (or VAR+=...) in command-prefix position. Task 3b: whether it
                # reaches git's environment is no longer just "was allexport on" -- a construct
                # that leaves the assignment STANDALONE (no command word terminates its run; see
                # `_leading_assign_run_is_scoped`) persists in the shell, keeping any export
                # attribute the name already carries (`HOME=/x ; git …`). A construct that scopes
                # it to another command (`HOME=/x true`) never reaches git regardless of the
                # name -- bash gives the assignment only to that one command's environment. The
                # `^GIT_` arm below still refuses an unreachable GIT_* name regardless -- a
                # deliberate, corpus-pinned over-block (assigned_but_never_exported_is_also_blocked)
                # that this repair preserves.
                if not in_assign_run:
                    assign_run_is_scoped = _leading_assign_run_is_scoped(
                        stream, i, gitcmd
                    )
                    in_assign_run = True
                if assign_run_is_scoped:
                    reaches_git_env = False
                else:
                    reaches_git_env = allexport or _name_carries_export_attribute(name)
            elif exporting:
                if token.startswith("-"):
                    # OPTIONS, never variable names. Two of them change what the construct
                    # MEANS and are tracked rather than merely skipped -- both measured
                    # against real bash, not reasoned:
                    #   -x  `declare`/`typeset` do NOT export without it: `declare N=1`,
                    #       `declare -r N=1`, `declare -i N=1`, `typeset N=1` all leave N
                    #       absent from the environment; only the `-x` forms put it there.
                    #       Without this, `declare -r N=1 ; git status` blocks blaming 'N'.
                    #   -f  `export -f` operates on FUNCTIONS, whose names never become
                    #       variables: after `export -f f`, `f=` is absent from env.
                    if "x" in token[1:]:
                        export_exports = True
                    if "f" in token[1:]:
                        export_is_functions = True
                    continue
                if export_is_functions:
                    continue  # a function name, not a variable
                # Task 3b: an export-family construct that does not itself export (`declare N=1`,
                # `readonly N=1`, no `-x`) still reaches git's environment if `N` already carries
                # the export attribute -- assigning to an already-exported name keeps it exported
                # regardless of the construct that does the assigning (measured against real
                # bash). `export_exports` (unconditional for `export`, or set by `-x` above) and
                # `allexport` each still force it on their own.
                reaches_git_env = (
                    export_exports or allexport or _name_carries_export_attribute(name)
                )
            elif in_set:
                # `set` names no variables. Only its allexport switches matter here.
                #
                # Short options BUNDLE, so `-a` must be matched as a CHARACTER, never as a whole
                # token. Measured against real bash: `set -ao allexport`, `set -ea` and `set -xa`
                # all turn allexport ON, and `set -ea; N=1` really does put N in the environment,
                # while `set -e`, `set -x` and `set do -a` leave it OFF. Matching the exact token
                # `-a` missed every bundled spelling, so a following `HOME=` was judged
                # unreachable and CLEARED -- a fail-OPEN one keystroke from the pinned `set -a`
                # form, reopening the very bypass this module was changed to close.
                #
                # This mirrors the `"x" in token[1:]` technique used for declare/typeset above;
                # exact-token matching here was the one place that did not.
                #
                # `+a` is deliberately NOT honoured as a disable, and allexport is never reset:
                # both leave the gate over-blocking, which is the accepted direction. Same for a
                # reserved word mid-construct (`set do -a`), which real bash treats as ending the
                # option run. Refusing a command bash would have allowed costs one turn; clearing
                # one it would have exported costs the boundary.
                if token.startswith("-") and not token.startswith("--"):
                    if "a" in token[1:]:
                        allexport = True
                    # A bundled `o` means the NEXT token is `-o`'s argument (`set -ao allexport`).
                    prev_set_opt = "-o" if "o" in token[1:] else token
                else:
                    if prev_set_opt == "-o" and token == "allexport":
                        allexport = True
                    prev_set_opt = token
                continue
            else:
                # Either a real command word, or an assignment-shaped token past the
                # command-prefix position (already an argument, never judged). Either way any
                # pending leading-assignment run is over -- reset so the next one is classified
                # fresh rather than reusing a stale verdict.
                in_assign_run = False
                # A wrapper that runs the BUILTIN in the current shell keeps command-prefix
                # position. Measured against real bash: `command export X=1`, `command -p export
                # X=1`, `builtin export X=1`, `time export X=1` and nested `command command
                # export X=1` ALL export, and `command set -a` really enables allexport --
                # whereas `nohup`, `nice` and `stdbuf` do NOT, because they exec an external
                # process and `export` is not a binary.
                #
                # This is why the set is enumerated here rather than taken from
                # `gitcmd.WRAPPERS`: that set is for finding a git invocation, and now carries
                # `builtin` and `eval` too (fix/eval-wrapper-bypass) -- both are included there now.
                # But it still carries `nohup`/`nice`/`sudo`/`env`, which do not preserve export.
                # Reusing it would still be wrong: it would admit those four into an export-arm
                # verdict they cannot actually produce.
                #
                # WITHOUT this, one word defeated the whole arm: `command export
                # GIT_CONFIG_COUNT=1 ; git <push>` measured BLOCK on dev and ALLOW from the
                # commit that added the command-position gate -- a REGRESSION, and the `no
                # verdict changes` claim that commit made was false. Found by whole-branch
                # review; the three benign de-over-blocks it did make are what hid it.
                if here_at_prefix and token in _SHELL_BUILTIN_WRAPPERS:
                    in_wrapper = True
                    continue  # at_prefix deliberately NOT cleared
                if in_wrapper and token.startswith("-"):
                    continue  # an option to the wrapper, e.g. `command -p export X=1`
                # An UNRESOLVABLE EXPANSION in command-prefix position is not a command word.
                # It may vanish (`$UNSET export X=1`) or expand to a wrapper (`$(echo command)
                # export X=1`) -- measured, both really export, because bash's rule is that if no
                # command name survives expansion the assignment runs in the CURRENT shell.
                # Treating it as a command word ended prefix position and the whole arm went dark.
                #
                # This module already owned the right predicate and consulted it at only ONE of
                # the two sites that need it -- `_leading_assign_run_is_scoped` had it,
                # this arm did not. That is the repo's own "derive both readings from ONE
                # predicate" hazard: two spellings of one intent, with the weaker reading
                # guarding the arm that matters. No word list could have closed this: the wrapper
                # SET is complete (68 candidates measured), and the missing members were not
                # words at all.
                if here_at_prefix and _looks_like_unresolvable_expansion(token, gitcmd):
                    continue  # keep command-prefix position; refuse to guess what it becomes
                in_wrapper = False
                if not is_assign:
                    at_prefix = False
                # COMMAND position only. Without this test, `git config set k v` reads `set`
                # as an exporter and judges `k`.
                if here_at_prefix and token in _EXPORT_WORDS:
                    # `export` exports by default; `declare`/`typeset` only with -x.
                    exporting, at_prefix = True, False
                    export_exports = token == "export"
                    export_is_functions = False
                elif here_at_prefix and token == _SET_WORD:
                    in_set, at_prefix, prev_set_opt = True, False, ""
                continue

            if token in claimed:
                continue  # already judged as this invocation's inline prefix
            if _env_name_is_cleared(name, reaches_git_env=reaches_git_env):
                continue  # same allowlist the inline arm consults -- see _env_name_is_cleared
            return (
                f"'{name}' reaches the environment of a git invocation in this same command "
                "and is not on this gate's environment allowlist -- it can relocate the config "
                "git reads, the hooks it runs, or the binary that runs as git. Either drop it, "
                f"or add {name} to _ENV_ALLOWLIST with its property class recorded"
            )
    return None


def _find_block_reason(command: str, cwd: str) -> Block | None:
    """Lazily import git_command (an ImportError here is caught by the caller, and blocks), then
    evaluate every git invocation in `command`. Returns the first Block found, or None."""
    sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
    import git_command as gitcmd  # noqa: E402

    gitdir_override = bool(GITDIR_RE.search(_dequote(command)))

    # ONE walk yields both the invocations and the working directory each runs in, so there is no
    # second sequence to fall out of alignment with. This replaces a pair of independently-ordered
    # walks zipped together with `strict=True`: that pairing was asserted by comment rather than
    # structure, and it raised — silently converting a nested-context command into a fail-closed
    # block — as soon as the two walks stopped finding the same number of invocations.
    #
    # Raises ValueError on tokenizing ambiguity, propagated uncaught to the caller's fail-closed
    # handler. Context-aware, so `cd`/`pushd`/`popd` and subshell isolation are resolved inside the
    # library where token position and invocation identity come from the same traversal.
    # Scoped narrowly ON PURPOSE: only the WALK's ambiguity is designed. A ValueError from
    # anywhere else in this function — the lazy import above, most of all — is a genuine fault and
    # must keep reaching the internal-error handler, log and all.
    #
    # `_exported_injection_reason` runs its OWN tokenizing pass (`iter_context_token_streams`) over
    # the same `command`, so its ValueError must be caught here too — otherwise a command this
    # walk's own ambiguity handling would call "unjudgeable, fail closed" instead falls through to
    # `main`'s internal-error branch and reads as a BUG in this guard rather than designed
    # ambiguity. Calling it here, still inside this `try`, is what keeps that routing correct.
    try:
        # `iter_git_invocations_with_readings` rather than `iter_git_invocations_detailed`:
        # SAME walk (the latter is literally this one with the trails dropped), so no second pass
        # and no second ordering to fall out of step. The trails are used ONLY to name the cause
        # in an ambiguity refusal's remedy -- never to decide anything.
        invocation_list, trails = gitcmd.iter_git_invocations_with_readings(
            command, cwd
        )
        invocations = list(invocation_list)
        exported = _exported_injection_reason(command, invocations, gitcmd)
    except ValueError as exc:
        raise AmbiguousCommand(str(exc)) from exc

    if exported is not None:
        # Derived, never hardcoded False: a command that plainly carries a push must keep the
        # alarming "refusing to push private 'dev'" wording (see `Block`'s two-axis docstring) --
        # hardcoding False here would route every one of these through the "no push was
        # identified" branch even when one of the invocations in `invocations` is a literal push.
        carries_push = any(s == "push" for _d, _c, s, _g, _t in invocations)
        # `boundary_unverifiable=True` -- this refusal is about the GATE (a denied name
        # reaching a git invocation via export), never about the target, exactly like the inline
        # env-prefix arm below: both arms answer the identical question -- can this reach a git
        # invocation and move or silence the boundary hook -- so leaving only the inline arm
        # fixed would reproduce F1b's defect for the export-in-a-separate-segment shape, one call
        # site over. An exported denied name ahead of a non-`dev` refspec would still falsely
        # claim a private-branch refusal on an allowlisted target; ahead of an unrelated
        # subcommand it would still falsely claim the guard could not judge the command, when in
        # both cases it judged the gate completely.
        return Block(exported, carries_push, boundary_unverifiable=True)

    # A refusal produced by the tokenizer's in-band marker, held back in case a REAL refusable
    # invocation turns up later in the walk — see the PRECEDENCE note below.
    marker_block = None
    # Whether the marker is in the OPERATOR's own text; see the `and not operator_marker` note.
    operator_marker = gitcmd.AMBIGUOUS_READING_SUBCOMMAND in command

    for effective_dir, cdir, sub, seg, tokens in invocations:
        # BEFORE the known-safe shortcut, deliberately. `config` is known-safe, and the whole
        # point of the two-command bypass is that its first half looks innocuous — skipping it
        # here would leave the pair intact. This judges the invocation's own tokens, so it costs
        # nothing on the overwhelming majority of commands that carry no such option.
        injection = _config_injection_reason(tokens, sub, seg)
        if injection is not None:
            reason_text, unconditional = injection
            # `-C` arrives separately from cd/pushd tracking and is joined only here; gating on
            # effective_dir alone would flip `git -C <adopted> config ...` from block to allow.
            # gitdir_override means the root is unknown => block.
            if (
                unconditional
                or gitdir_override
                or _repo_is_adopted(_combine(effective_dir, cdir))
            ):
                return Block(
                    reason=(
                        f"this command is refused because {reason_text}. The `pre-push` hook "
                        "is the load-bearing publication boundary; a command that can move or "
                        "silence it is refused whatever it goes on to do."
                    ),
                    is_push=any(s == "push" for _d, _c, s, _g, _t in invocations),
                    # `boundary_unverifiable=True` -- see `Block`'s docstring. This refusal is
                    # about the GATE, not the target: measured false before this fix, an inline
                    # `GIT_COMMON_DIR=` ahead of an allowlisted target claimed a private-branch
                    # refusal on that ALLOWED target, and an inline denied name ahead of a
                    # non-push subcommand claimed the guard "could not judge" a command it had in
                    # fact judged completely. Neither the is_push=True nor the is_push=False
                    # wording fits a refusal about the gate itself -- same argument as F1b, one
                    # call site over (that one for `_hook_integrity_reason`, this one for a
                    # denied config/env name reaching the invocation directly).
                    boundary_unverifiable=True,
                )
        if sub != "push" and sub in KNOWN_SAFE_SUBCOMMANDS:
            continue
        root_dir = _combine(effective_dir, cdir)
        reason = _judge_invocation(root_dir, sub, seg, gitcmd, gitdir_override)
        if reason is not None:
            # PRECEDENCE, per RECORD. A refusal produced by the tokenizer's in-band marker is
            # remembered and the scan keeps going, so a command carrying BOTH a lost reading and
            # a real refusable invocation reports the real one — which is the only one the
            # operator can act on. `push-guard.py:_block_kind` states the identical rule for the
            # stream-shaped side; this is its invocation-shaped twin.
            #
            # It replaces a command-level test (`ambiguous_reading and not is_push` in `main`)
            # that was measured FALSE: `is_push` is `carries_push`, true for ANY literal push
            # including one this guard would allow, so the witness `bash <<'(x'` / an `x=$` line
            # ending in a backslash / `(x` / `git push origin main` was refused with "refusing to
            # push private 'dev' (or an ambiguous target): the effective working directory could
            # not be resolved (cd/pushd target unknown)" — two false claims in one sentence, on a
            # command whose only push targets `main` (allowed on its own, rc=0) and which contains
            # no `cd` and no `pushd`. Both came from the SYNTHETIC record. Shipped `dev` answers
            # the same input honestly.
            #
            # The verdict cannot move: every arm here blocks, so continuing past a marker record
            # either finds another blocking record (still rc 2) or falls through to the remembered
            # marker (still rc 2). Only the message changes.
            #
            # UNCONSTRUCTED, stated as such, exactly like `push-guard.py:_block_kind`'s twin of
            # this note. `_walk_context` appends a context's indeterminate invocation AFTER that
            # context's own invocations, so on every shape measured the marker record arrives
            # last and this hold-back never fires: five shapes were probed for a marker record
            # PRECEDING a real one -- a top-level heredoc before a push, and the same heredoc
            # inside `$( )`, backticks, another command's argument span, and a subshell -- and
            # three raised outright while two put the marker last. Deleting the hold-back
            # therefore changes nothing measurable today (its mutant SURVIVES the suite, recorded
            # rather than papered over). It stays because the alternative is to depend on an
            # ordering nobody has pinned, which is what the sibling guard already refused to do.
            # The WORDING is a claim about the whole command, not about the invocation that
            # happened to block first. `git -C "$live" frobnicate && git push origin dev` blocks
            # on `frobnicate`, whose is_push is False — but the command carries a literal push,
            # and "no push was identified" is then the exact inverse of the mislabel this split
            # exists to prevent. The verdict does not move (both arms block); only the message.
            carries_push = any(s == "push" for _d, _c, s, _g, _t in invocations)
            # `ambiguous_reading` is set HERE, on the one record that produced the refusal, rather
            # than from "does this command contain a marker anywhere": a command can carry a lost
            # reading AND block for an unrelated, fully-judged reason, and relabelling that refusal
            # would be the same mislabel one step over.
            #
            # `and not operator_marker`: the marker is a string a person can TYPE, so the
            # subcommand test alone cannot tell a synthesized record from `git
            # '$<ambiguous-heredoc-reading>'`. A synthesized marker corresponds to no command text
            # by construction, which is exactly what makes the raw text decisive. Same discriminator
            # as `push-guard.py:_operator_typed_the_marker`, where it was measured.
            block = reason._replace(
                is_push=reason.is_push or carries_push,
                ambiguous_reading=(
                    gitcmd.subcommand_is_ambiguous_reading(sub) and not operator_marker
                ),
                lost_cause=guard_ambiguity.cause(gitcmd, command, trails),
            )
            if not block.ambiguous_reading:
                return block
            marker_block = marker_block if marker_block is not None else block
    return marker_block


def _record_internal_error(command: str, cwd: str, exc: BaseException) -> str | None:
    """Append a diagnostic for a fail-closed INTERNAL error. Returns the log path, or None.

    The internal-error branch in `main` is the one refusal this guard cannot explain from its own
    output: it names the exception class and nothing else. Measured twice on 2026-07-29, it refused
    two legitimate non-push commands, and six reproduction attempts all came back clean — the
    failure path had discarded its only evidence, making a recurring fault undiagnosable by
    construction.

    Recording the command VERBATIM is the entire point. A length+digest fingerprint cannot be read
    back into a reproduction, and reproduction is precisely the step that was blocked; the digest is
    kept alongside only so two occurrences can be compared at a glance. This adds no new class of
    exposure — the session transcript under ~/.claude/projects/ already stores every Bash command
    verbatim on the same disk — but the file is still opened 0600 rather than inheriting the umask.

    NEVER raises, for any input or filesystem state. This runs inside a fail-closed security gate's
    except branch, so a diagnostic that could throw would hand that gate a brand-new failure mode in
    exchange for nothing. Every failure to record degrades to None and is reported to stderr.
    """
    try:
        override = os.environ.get(LOG_ENV_VAR)
        path = Path(override) if override else DEFAULT_LOG_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        raw = command.encode("utf-8", "replace")
        stamp = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
        record = (
            f"===== {stamp} {exc.__class__.__name__} =====\n"
            f"cwd: {cwd}\n"
            f"bytes: {len(raw)}  sha256: {hashlib.sha256(raw).hexdigest()}\n"
            f"--- command (verbatim) ---\n{command}\n"
            f"--- traceback ---\n"
            + "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        )
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.write(fd, record.encode("utf-8", "replace"))
        finally:
            os.close(fd)
        return str(path)
    except Exception:  # noqa: BLE001 - a diagnostic must never add a failure mode to the gate
        return None


def main() -> int:
    """Hook entry point: 2 blocks the push, 0 allows it."""
    # FIRST, before the stdin read and both heavy tokenizer walks: a deadline this PROCESS owns.
    # The harness kills a hook at its registered timeout, discards its output and tells nobody, so
    # this gate killed at 60 s is SILENT and the push RUNS. Measured on a single command under
    # MAX_COMMAND_LENGTH: 224.89 s here, 172.40 s on shipped `dev` (see `git_command.py`'s
    # MAX_TOTAL_PARSES docstring, which files this deadline as the remedy). The handler `os._exit`s
    # rather than raising, because the `opaque_only` arms below return 0 for ANY exception and
    # would convert the deadline into an ALLOW — see `scripts/lib/guard_deadline.py`.
    guard_deadline.install(HOOK_NAME)
    # HOOK CONTRACT: the target arrives as a JSON payload on stdin; argv is ignored. Refuse the
    # two invocations this cannot serve, because each otherwise reads as SUCCESS — with argv and
    # stdin at EOF it exits 0 having examined nothing, and with a terminal stdin it blocks
    # forever. See scripts/HOOKS.md for the payload form.
    if len(sys.argv) > 1 or sys.stdin.isatty():
        sys.stderr.write(
            "%s is a Claude Code hook: it reads a JSON payload on stdin and ignores\n"
            "arguments. Running it with filenames examines nothing — see scripts/HOOKS.md.\n"
            % "publication-push-guard.py"
        )
        sys.exit(2)
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0
    # This is the FIRST layer — "is there even a parseable command?" — and its ambiguity fails
    # OPEN (exit 0), exactly like the garbage-stdin case above: valid JSON that isn't an object
    # (`42`, `"str"`, `[1,2]`), or a `tool_input` that isn't an object, carries no command to
    # evaluate. Exit 2 here would block every Bash call on an odd payload, not just pushes.
    if not isinstance(data, dict):
        return 0
    tool_input = data.get("tool_input")
    if not isinstance(tool_input, dict):
        tool_input = {}
    command = tool_input.get("command") or ""
    if not isinstance(command, str):
        command = ""
    cwd = data.get("cwd") or os.getcwd()
    if not isinstance(cwd, str):
        cwd = os.getcwd()
    if not command:
        return 0  # cheap: nothing resembling a git invocation at all
    dequoted = _dequote(command)
    # `opaque_only`: no literal `git` word survives de-quoting, but a `$` or backtick is present --
    # a command word can still be OPAQUE and reduce to `git` at run time (`g$(true)it`, `gi${X}t`,
    # `"$X"git`): de-quoting merges quote-split obfuscation, but none of these shapes ever lands on
    # a bounded `git` GIT_WORD_RE can see, because the opaque fragment still sits between the
    # letters and a word boundary. A LENIENT TEXT pre-gate was measured instead -- accept once `$`
    # sigils and substitution punctuation are stripped and the letters `git` remain -- and it would
    # have newly false-blocked 51 of 32,259 real commands, all unparseable and none carrying a git
    # word at all: this form adds zero of those. So instead: run the REAL tokenizer, but degrade
    # EVERY exception it raises here -- `AmbiguousCommand` and the internal-error class alike -- to
    # `return 0`, exactly what today's pre-gate already does for every one of these commands (none
    # satisfies `GIT_WORD_RE.search` today, so none ever reached `_find_block_reason` at all).
    # Without this, a guard BUG would newly exit 2 for ~10% of real commands -- every one carrying
    # a `$` -- that never reached the tokenizer before. What this still leaves open, pre-existing
    # and dominated by the wholly-dynamic residual: a hidden git word followed by a tail the
    # tokenizer cannot parse (`g$(true)it <push> origin dev ; echo $'it\'s'`) still fails open, at
    # every guard.
    opaque_only = not GIT_WORD_RE.search(dequoted)
    if opaque_only and "$" not in dequoted and "`" not in dequoted:
        return 0  # cheap: nothing resembling a git invocation at all, even after de-quoting

    try:
        reason = _find_block_reason(command, cwd)
    except AmbiguousCommand as exc:
        if opaque_only:
            # No literal git word was ever seen, so today's pre-gate would already have returned 0
            # here without entering the tokenizer at all -- reaching it only because an opaque word
            # MIGHT be git must not newly block on an ambiguity the pre-gate itself would never
            # have surfaced. See the `opaque_only` comment above.
            return 0
        # DESIGNED ambiguity, not a fault. An unbalanced quote or unterminated context is input the
        # module docstring already defines as unjudgeable, and the walk signals it with ValueError.
        # It used to fall through to the handler below, which labels the refusal "a BUG in the
        # guard" and appends to the diagnostic log — so ordinary malformed input buried the rare
        # genuine fault that log exists to capture. (Measured previously: 12 synthetic records from
        # 4 suite runs.) Same verdict, honest wording, and nothing written.
        print(
            f"{PREFIX} refusing a git command it could not parse unambiguously "
            f"({exc}); failing closed. This is not a policy decision about a push — no push was "
            f"identified, because the command could not be read. Fix the quoting and re-run."
            # __cause__, not exc: the raise site flattens the message with str(exc) but preserves
            # the original via `from exc`. Verified end to end before this was written.
            f"{_ambiguity_detail(exc.__cause__)}",
            file=sys.stderr,
        )
        return 2
    except Exception as exc:  # noqa: BLE001 - deliberate: any crash here must fail CLOSED
        if opaque_only:
            # Same reasoning as the AmbiguousCommand arm above: no literal git word was ever seen,
            # so today's pre-gate would already have returned 0 here without ever entering
            # `_find_block_reason` -- a guard BUG on a command that only MIGHT carry an opaque git
            # word must not newly exit 2 for the ~10% of real commands that carry a `$`.
            return 0
        # Record BEFORE printing: the verdict must not depend on the diagnostic succeeding, and the
        # operator needs the path in the same breath as the refusal. `_record_internal_error` is
        # contractually non-raising, so nothing here can convert a block into a crash.
        log_path = _record_internal_error(command, cwd, exc)
        print(
            f"{PREFIX} refusing to allow a git command after an internal error "
            f"({exc.__class__.__name__}) while evaluating it; failing closed.",
            file=sys.stderr,
        )
        if log_path is not None:
            print(
                f"{PREFIX} the offending command and its traceback were recorded at {log_path} "
                f"— this refusal is a BUG in the guard, not a policy decision about your command.",
                file=sys.stderr,
            )
        else:
            print(
                f"{PREFIX} could not record a diagnostic; set {LOG_ENV_VAR} to a writable path "
                f"to capture the next occurrence.",
                file=sys.stderr,
            )
        return 2

    if reason is not None:
        if reason.boundary_unverifiable:
            # A THIRD category, checked before the two-axis split below, set from either of two
            # call sites in `_find_block_reason` -- see `Block`'s docstring. For an
            # `_hook_integrity_reason` refusal, `_judge_push` already returned None, so `is_push`
            # is correctly True -- but the target is allowlisted, so "refusing to push private
            # 'dev'" is simply false. For a config/env-injection refusal `is_push` can be either
            # value, and "no push was identified" is equally false whenever one was. Neither
            # two-axis message fits either case: the refusal is about the GATE, not the target,
            # so it gets its own wording regardless of which call site produced it.
            print(
                f"{PREFIX} refusing this git invocation -- this refusal is about the boundary "
                f"itself, not the target: {reason.reason}",
                file=sys.stderr,
            )
        elif reason.ambiguous_reading:
            # A FOURTH category -- see `Block`'s docstring. The refusal came from the tokenizer's
            # in-band marker for a reading it could not parse, so `reason.reason` is a sentence
            # about the SYNTHETIC record ("the effective working directory could not be resolved")
            # and false of the command the operator wrote: measured on the witness
            # `bash <<'(x'` / `echo hello` / an `x=$` line ending in a backslash / `(x`, which
            # carries no cd, no push and no git word at all. It is dropped rather than quoted.
            #
            # NO `and not reason.is_push` GUARD, deliberately, and its removal is a bug fix rather
            # than a relaxation. `is_push` is the command-level `carries_push`, true for ANY
            # literal push including one this guard would ALLOW, so that guard sent the witness
            # `bash <<'(x'` / `x=$` + backslash / `(x` / `git push origin main` down the branch
            # below and produced "refusing to push private 'dev' (or an ambiguous target): the
            # effective working directory could not be resolved (cd/pushd target unknown)" --
            # false about the target (`git push origin main` alone is rc=0 here) and false about
            # the cause (the command has no `cd` and no `pushd`). The comment that used to sit
            # here claimed such a command "keeps the alarming wording below, which is the one it
            # can act on"; the tree falsified it, because the push was not what was being refused.
            #
            # "A real refusal outranks the marker" survives, moved to where it can be TRUE: it is
            # now per RECORD, in `_find_block_reason`, which holds a marker refusal back and keeps
            # walking. So a command carrying both still reports the real one; `reason` only
            # reaches this branch when the marker is all there was.
            print(
                f"{PREFIX} refusing a git command because one READING of it could not be parsed. "
                f"The command was therefore treated as possibly performing a push, rather than "
                f"allowed unchecked. No env prefix authorizes it: the reading that was lost "
                f"corresponds to no command text, so there is no segment for an override to lead "
                f"-- and this gate does not honour ALLOW_PUSH=1 in any case. bash reads a heredoc "
                f"whose last body line ends in a backslash two ways - dropping the continuation, "
                f"or joining it onto the terminator - and only one of those readings parses here, "
                f"so what the other one would have run is unknown. "
                # The remedy is chosen from WHY the reading was lost, never fixed. The marker has
                # TWO causes -- a reading that RAISED, and an exhausted parse BUDGET -- and the
                # sentence that used to be hardcoded here named the one that does not occur in
                # practice (6 of 6 real markers over 90,675 commands are the BUDGET cause; see
                # `guard_ambiguity`). This guard already walked, so its TRAILS are passed in and
                # nothing is re-walked.
                f"{guard_ambiguity.remedy(reason.lost_cause)}"
                f"{_explain_tool_clause()}",
                file=sys.stderr,
            )
        elif reason.is_push:
            # A push WAS identified. Unchanged wording — a runbook greps for this line, and this
            # is the case where alarm is correct.
            print(
                f"{PREFIX} refusing to push private 'dev' (or an ambiguous target): "
                f"{reason.reason}",
                file=sys.stderr,
            )
        else:
            # No push was identified — but that is NOT the same as "there is no push here", and
            # the wording must not say so. `git $s origin dev` may well be one; the guard simply
            # could not read it. Claiming "no push detected" would invite exactly the dismissal
            # this branch exists to prevent.
            print(
                f"{PREFIX} refusing a git command it could not judge: {reason.reason}. "
                f"No push was identified — but the guard could not determine whether this "
                f"pushes, so it fails closed. This gate does not honour ALLOW_PUSH=1; setting it "
                f"will not help.",
                file=sys.stderr,
            )
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())

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
`^GIT_` environment assignment outside a small allowlist of names verified boundary-inert (author
and committer identity, `GIT_PAGER`, `GIT_TERMINAL_PROMPT`, `GIT_CONFIG_NOSYSTEM`), plus any `-c`
value that does not resolve to a literal `key=value`. These are refused whatever the command goes
on to do, because they can relocate or silence the `pre-push` hook — and a command that disables
layer 2 while carrying a refspec layer 1 allows was measured to defeat the whole stack in one line.
A second, COMMAND-scoped check (`_exported_injection_reason`) extends the same allowlist past that
one-invocation prefix, to a denied `GIT_*` name that reaches a git invocation via an `export` in a
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
  - **`HOME=` and `XDG_CONFIG_HOME=` relocate the boundary, and are NOT denied.** The env
    allowlist below is keyed on a `^GIT_` name shape, so it closes the `GIT_*` part of the env
    axis and nothing else. Both of these redirect where git reads its GLOBAL config, which can
    carry `core.hooksPath` — measured: with a hostile `HOME`, `--git-path hooks/pre-push` moves,
    the real hook does not run, and this gate ALLOWS the invocation. `_hook_integrity_reason`
    cannot see it by construction: that probe runs in THIS process's environment, never the
    proposed command's. Pre-existing (the narrower `^GIT_CONFIG_` matcher this allowlist replaced
    cleared them too) and deliberately not closed here, because widening the deny beyond `GIT_*`
    needs its own false-block measurement. **Do not read the allowlist as closing the env axis.**
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

    `boundary_unverifiable` is a THIRD, orthogonal category: an `_hook_integrity_reason` refusal
    fires only after `_judge_push` already returned None, i.e. always on an allowlisted target,
    so `is_push` is correctly True here — the invocation was a real one — yet the "refusing to
    push private 'dev'" wording is still wrong, because the target being refused is not private.
    Flipping `is_push` to False would be equally wrong: that branch says "no push was
    identified", and one was. Neither of the two existing messages fits, so this is its own
    boolean rather than a third value squeezed onto `is_push` — the boundary could not be
    verified, so no publish from this repo can be judged, which is a statement about the GATE,
    not about the target.
    """

    reason: str
    is_push: bool
    boundary_unverifiable: bool = False


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

    Checked in a fixed order, and the order is load-bearing, not incidental:

    - The new symlink cause (S) is probed BEFORE `.resolve()` is trusted. `.resolve()` follows
      symlinks, so a hook that is itself a symlink pointing outside the common dir would trip
      the containment cause first, leaving this branch installed but never reachable --
      indistinguishable from a dead line. Narrower than containment: a symlinked hooks
      *directory* holding a real regular hook file leaves the leaf `is_symlink()` probe False
      (see the containment cause's wording below) and falls through to containment on its own.
    - The tracked-source cause comes AFTER containment but BEFORE the install-state causes
      (missing / not executable / digest mismatch), because every install-class remedy
      presupposes that source existing -- but it must not be hoisted all the way to the top: a
      checkout with both a missing source and a relocated hooks path must not be told "update
      the checkout, then install", which cannot clear a relocation either.

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
    if is_hook_symlink:
        return (
            f"the installed boundary hook at {hook_raw} is a symlink",
            f"remove the symlink at {hook_raw} yourself, then run {INSTALL_SCRIPT_RELPATH} -- "
            "the installer refuses to install over a symlink even with --force, so removing it "
            f"by hand comes first. {_NO_OVERRIDE_NOTE}",
        )
    if common_dir not in hook.parents:
        return (
            f"git resolves the boundary hook to {hook}, outside the repository's own hooks "
            "directory. This can happen because core.hooksPath has been relocated, or because "
            ".git/hooks itself is a symlinked directory pointing elsewhere",
            "find where core.hooksPath is set (git config --show-origin --get "
            "core.hooksPath) and unset it in that scope; if .git/hooks is itself a symlinked "
            "directory, point it back at a real directory holding the tracked hook. "
            f"{_NO_OVERRIDE_NOTE}",
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


def _config_is_read(seg: list[str]) -> bool:
    """True only when `git config`'s LEADING option run marks this a read.

    POSITION IS LOAD-BEARING. git stops parsing options at the first non-option
    token, so a read flag after the key is consumed as a positional and the WRITE
    still lands: `git config core.hooksPath --get` was measured to write
    `core.hooksPath = --get`, relocating the hooks path. A position-insensitive rule
    allows exactly that.

    This can only ever turn a read into a WRITE (over-block), never a write into a
    read, because git refuses multiple actions in one invocation -- so it opens no
    hole. A value-taking option before the read flag (`--type bool --get k`) breaks
    the scan early and over-blocks; accepted residual, safe direction.
    """
    for tok in seg:
        if tok == "--":
            return False
        if not tok.startswith("-"):
            return tok in _CONFIG_READ_ACTIONS  # the subcommand form's action word
        if tok.split("=", 1)[0] in _CONFIG_READ_FLAGS:
            return True
    return False


# Env assignments that redirect where `git config` WRITES. Measured: both landed a
# write in an unrelated repo's config from a cwd with no relationship to it.
#
# These three are ALSO denied upstream, by the `^GIT_`-shaped env arms (see
# `_git_env_name_is_cleared`), so this set is no longer the only thing standing between them and a
# redirected write -- it answers a different question (which SCOPE does this `git config` write
# target) and is kept for that. Do not fold the two together: an earlier, narrower env matcher
# missed bare `GIT_CONFIG` and `GIT_COMMON_DIR` entirely, which is the F2 finding, and the presence
# of those names HERE is what made that gap read as already covered.
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
        if assignment.split("=", 1)[0] in _CONFIG_REDIRECT_ENV:
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


# Names of `GIT_*` env assignments cleared to reach a git invocation -- each verified individually
# (this branch's design record, finding F2 / decision D3) as boundary-INERT: git's own resolution
# of the `pre-push` hook path is unchanged under it, and the repo's own hook still runs. Every
# OTHER `GIT_`-prefixed assignment name is denied by BOTH env arms below -- the own-prefix loop in
# `_config_injection_reason` and the command-scoped walk in `_exported_injection_reason`. Those two
# arms are coupled by `if token in claimed: continue`, so consulting this allowlist from only one
# of them leaves the other form open -- a regression, not partial progress, and this must not be
# split across separate changes.
#
# NEVER reuse or extend `_CONFIG_REDIRECT_ENV` (below, consulted only by `_config_scope_is_local`)
# for this: it answers a different question -- where does `git config` WRITE -- and already
# contains `GIT_COMMON_DIR`. That overlap is coincidental, not a signal the two sets should merge;
# treating it as "already handled" is exactly the trap this allowlist exists to avoid.
_GIT_ENV_ALLOWLIST = frozenset(
    {
        "GIT_AUTHOR_NAME",
        "GIT_AUTHOR_EMAIL",
        "GIT_AUTHOR_DATE",
        "GIT_COMMITTER_NAME",
        "GIT_COMMITTER_EMAIL",
        "GIT_COMMITTER_DATE",
        "GIT_PAGER",
        "GIT_TERMINAL_PROMPT",
        "GIT_CONFIG_NOSYSTEM",
    }
)

# Broad by design: every `GIT_`-prefixed name, not only the narrower `^GIT_CONFIG_` shape this
# replaced (that constant is gone -- once both arms consulted the allowlist it had no consumer left,
# and a compiled regex with no caller reads as an active matcher while deciding nothing).
# `GIT_COMMON_DIR` and bare `GIT_CONFIG` are exactly what the narrower shape missed -- see the
# design record's F2 finding. `GIT_DIR` also matches here,
# redundantly and safely, with the coarse whole-command `GITDIR_RE` above; this predicate stays
# correct standing alone rather than depending on that separate, differently-scoped check.
_GIT_ENV_NAME_RE = re.compile(r"^GIT_")


def _git_env_name_is_cleared(name: str) -> bool:
    """Whether `name` is cleared to reach a git invocation's environment.

    True for any name that is not `GIT_`-shaped at all (an ordinary env var this check has no
    opinion on) and for the nine names in `_GIT_ENV_ALLOWLIST` above. False for every other `^GIT_`
    name. The single predicate both env arms below consult before denying an assignment -- the
    allowlist is the only place "which names are safe" is answered, never a per-arm literal or a
    second frozenset.
    """
    return not _GIT_ENV_NAME_RE.match(name) or name in _GIT_ENV_ALLOWLIST


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
        name = assignment.split("=", 1)[0]
        if _git_env_name_is_cleared(name):
            continue  # not GIT_-shaped, or on _GIT_ENV_ALLOWLIST -- see _git_env_name_is_cleared
        return (
            f"it sets {name}, a `GIT_*` name outside the small allowlist of names verified "
            "boundary-inert -- git may honour it in ways that relocate or silence the hooks path",
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
        for arg in seg:
            if any(arg.strip().lower().startswith(d) for d in DENIED_CONFIG_KEYS):
                return (
                    f"it writes {arg} via `git config`, which can disable the push boundary",
                    not _config_scope_is_local(seg, list(tokens.env)),
                )
    return None


# Command words whose whole job is to export or already-exported-mark the assignments that follow
# them (`export FOO=1`, `export FOO` naming an already-assigned var, `declare -x`, `set -a`). Kept
# separate from `WRAPPERS`/`GIT_ONLY_WRAPPERS` in git_command.py: those mark the WORD AFTER them as
# still being in command position (`sudo git …`); these mark everything AFTER them, up to the next
# segment separator, as an argument to the export construct itself — a different relationship, not
# a stronger version of the same one.
_EXPORT_WORDS = frozenset({"export", "declare", "typeset", "set"})

# Command/segment boundaries `_exported_injection_reason` resets its position tracking on. `;`,
# `&&`, `||`, `|`, `&` all end one command and (for `;`/`&&`/`||`/`&`) start another at the shell's
# top level; `(`/`)` bound a subshell. Deliberately the same operator set `is_op` recognises, but
# named here rather than imported as a function, because this walk tests membership token-by-token
# against a frozenset, not a predicate call per token.
_SEGMENT_SEPARATORS = frozenset({";", "&&", "||", "|", "&", "(", ")"})


def _exported_injection_reason(
    command: str, invocations: list, gitcmd: ModuleType
) -> str | None:
    """A denied `GIT_*` name reaching a git invocation from OUTSIDE its own env prefix.

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
        for token in stream:
            if token in _SEGMENT_SEPARATORS:
                exporting, at_prefix = False, True
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
                continue
            name = token.split("=", 1)[0]
            is_assign = gitcmd.ENV_ASSIGN.match(token) is not None
            if at_prefix and is_assign:
                pass  # a bare VAR=... in command-prefix position
            elif exporting:
                pass  # an argument to export/declare/typeset/set
            else:
                if not is_assign:
                    at_prefix = False
                if token in _EXPORT_WORDS:
                    exporting, at_prefix = True, False
                continue
            if token in claimed:
                continue  # already judged as this invocation's inline prefix
            if _git_env_name_is_cleared(name):
                continue  # same allowlist the inline arm consults -- see _git_env_name_is_cleared
            return (
                f"'{name}' is exported into the environment of a git invocation in this "
                "same command, which can relocate or silence the boundary hook"
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
        invocations = list(gitcmd.iter_git_invocations_detailed(command, cwd))
        exported = _exported_injection_reason(command, invocations, gitcmd)
    except ValueError as exc:
        raise AmbiguousCommand(str(exc)) from exc

    if exported is not None:
        # Derived, never hardcoded False: a command that plainly carries a push must keep the
        # alarming "refusing to push private 'dev'" wording (see `Block`'s two-axis docstring) --
        # hardcoding False here would route every one of these through the "no push was
        # identified" branch even when one of the invocations in `invocations` is a literal push.
        carries_push = any(s == "push" for _d, _c, s, _g, _t in invocations)
        return Block(exported, carries_push)

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
                )
        if sub != "push" and sub in KNOWN_SAFE_SUBCOMMANDS:
            continue
        root_dir = _combine(effective_dir, cdir)
        reason = _judge_invocation(root_dir, sub, seg, gitcmd, gitdir_override)
        if reason is not None:
            # The WORDING is a claim about the whole command, not about the invocation that
            # happened to block first. `git -C "$live" frobnicate && git push origin dev` blocks
            # on `frobnicate`, whose is_push is False — but the command carries a literal push,
            # and "no push was identified" is then the exact inverse of the mislabel this split
            # exists to prevent. The verdict does not move (both arms block); only the message.
            carries_push = any(s == "push" for _d, _c, s, _g, _t in invocations)
            return reason._replace(is_push=reason.is_push or carries_push)
    return None


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
    if not command or not GIT_WORD_RE.search(_dequote(command)):
        return 0  # cheap: nothing resembling a git invocation at all, even after de-quoting

    try:
        reason = _find_block_reason(command, cwd)
    except AmbiguousCommand as exc:
        # DESIGNED ambiguity, not a fault. An unbalanced quote or unterminated context is input the
        # module docstring already defines as unjudgeable, and the walk signals it with ValueError.
        # It used to fall through to the handler below, which labels the refusal "a BUG in the
        # guard" and appends to the diagnostic log — so ordinary malformed input buried the rare
        # genuine fault that log exists to capture. (Measured previously: 12 synthetic records from
        # 4 suite runs.) Same verdict, honest wording, and nothing written.
        print(
            f"{PREFIX} refusing a git command it could not parse unambiguously "
            f"({exc}); failing closed. This is not a policy decision about a push — no push was "
            f"identified, because the command could not be read. Fix the quoting and re-run.",
            file=sys.stderr,
        )
        return 2
    except Exception as exc:  # noqa: BLE001 - deliberate: any crash here must fail CLOSED
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
            # A THIRD category, checked before the two-axis split below. `_judge_push` already
            # returned None to reach `_hook_integrity_reason`, so `is_push` is correctly True
            # here -- but the target is allowlisted, so "refusing to push private 'dev'" is
            # simply false, while claiming "no push was identified" (the other branch) is
            # equally false, since one was. Neither fits: the refusal is about the GATE, not the
            # target, so it gets its own wording.
            print(
                f"{PREFIX} refusing this git invocation -- this refusal is about the boundary "
                f"itself, not the target: {reason.reason}",
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

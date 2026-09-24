"""Shared shell-command tokenizer for git-invocation-detecting hooks.

Tokenizes an arbitrary shell command string safely (``shlex``, never ``eval``) and walks it to
find every `git` invocation in *command position* — the same primitive a PreToolUse hook needs to
tell a real `git commit`/`git push`/etc. from a look-alike, and to see it even inside a compound
command (`git add -A && git commit`), behind an exec-wrapper (`sudo git commit`), or past an
env-assignment prefix (`ALLOW_PUSH=1 git push`).

Known fail-open forms (a caller sees no invocation — degrading to *no gate*, never a false
positive): a command reached only through a git alias (``git ci``), wrapped in ``sh -c "…"``, run
under a wrapper the ``WRAPPERS`` set does not list *with its own arguments*
(``timeout 60 git commit``), or an unknown leading word before `git` (``echo git commit`` is NOT
in command position). The bare ``eval git …`` form IS followed since fix/eval-wrapper-bypass; the
residual that remains is `eval`'s RE-PARSE — a quoted word carrying whitespace or syntax, or an
empty word (``eval "git push origin dev"``, ``eval ':;' git push``) — which is opaque to this
tokenizer exactly as ``sh -c "…"`` is. A `cd`/`pushd`/`popd` reached through any wrapper makes the
tracked cwd UNRESOLVABLE rather than followed, since whether the shell really moves depends on the
whole chain. Resolving aliases and nested command strings is out of scope here.

Since 2026-09-18, `is_git` also recognises many command words bash reduces to `git` through
substitution and expansion — see `is_git`'s own docstring for the widened rule. Three classes
stay residual there: BRACE expansion (``{git,}`` — a clause for it was built and deleted, see the
comment above `_literal_git`); a command word whose EXPANSION SUPPLIES letters of `git` — wholly dynamic
(``$G``, ``$(echo git)``) or partly (``gi$X``, ``${X}it``, ``$(echo g)it``), all measured allowed
at every guard — since closing it by text was measured to false-block 1.5%-10% of real commands;
and WHITESPACE inside ``${…}`` in the command
word (``git${X:+ }``, ``${X:- }git``) — bash ran git for ``git${X:+ } status`` under `/bin/bash`
3.2.57 and MacPorts bash 5.3.15 (X=1), and `shlex` splits the word into ``git${X:+`` / ``}``, so
nothing is recorded.
"""

from __future__ import annotations

import itertools
import os
import re
import shlex
from collections import Counter
from collections.abc import Iterator, Mapping
from typing import NamedTuple

# git *global* options (before the subcommand), classified by whether they consume the FOLLOWING
# token. The pair is an ALLOWLIST and the classifier below defaults to "unknown", because the
# blocklist shape this replaces — "anything I don't recognise is valueless, so step over it" —
# was a measured fail-open: `git --attr-source HEAD <push> origin dev` yielded subcommand `HEAD`
# with the real push sitting unexamined in the argument segment, and the guard's only question
# about a non-push subcommand is whether it is an alias FOR push, so `HEAD` cleared it. Every
# option git grows in future lands as "unknown" and blocks, which is the safe direction; the cost
# is a false block that adding one name fixes.
GLOBAL_VALUE_OPTS = {
    "-c",
    "-C",
    "--git-dir",
    "--work-tree",
    "--namespace",
    "--attr-source",
    "--config-env",
}

# Valueless: they never consume the following token, so the token after one is still a candidate
# subcommand. `--list-cmds` is real only in its `=<spec>` form and `--exec-path` only prints when
# bare, but both are harmless to step over, and omitting `--list-cmds` would block an agent
# following publication-push-guard's own comment, which cites `--list-cmds=main`.
GLOBAL_FLAG_OPTS = {
    "--exec-path",
    "-v",
    "--version",
    "-h",
    "--help",
    "--html-path",
    "--man-path",
    "--info-path",
    "-p",
    "-P",
    "--paginate",
    "--no-pager",
    "--bare",
    "--no-replace-objects",
    "--no-lazy-fetch",
    "--no-optional-locks",
    "--no-advice",
    "--literal-pathspecs",
    "--noglob-pathspecs",
    "--icase-pathspecs",
    "--glob-pathspecs",
    "--list-cmds",
}

# The FLOOR. Discovery cannot detect ABSENCE — a set that quietly loses a member still parses, and
# a *misclassified* one is worse than a missing one: moving `--git-dir` to the valueless set would
# make its value token read as the subcommand. So name the members whose absence must alarm and a
# minimum size, and let the sets only ever grow past it. This raises rather than warns, and every
# consumer imports this module inside a try/except that BLOCKS on failure, so a violated floor
# fails closed. `--super-prefix` is deliberately absent: it does not exist in git 2.55, so pinning
# it would freeze a name that is already gone.
_FLOOR_VALUE = frozenset({"-c", "-C", "--git-dir", "--work-tree", "--namespace"})
_FLOOR_FLAG = frozenset({"--exec-path", "--no-pager", "-P", "--paginate", "--bare"})
if not _FLOOR_VALUE <= GLOBAL_VALUE_OPTS:
    raise RuntimeError(
        f"GLOBAL_VALUE_OPTS lost required members: {sorted(_FLOOR_VALUE - GLOBAL_VALUE_OPTS)}"
    )
if not _FLOOR_FLAG <= GLOBAL_FLAG_OPTS:
    raise RuntimeError(
        f"GLOBAL_FLAG_OPTS lost required members: {sorted(_FLOOR_FLAG - GLOBAL_FLAG_OPTS)}"
    )
if GLOBAL_VALUE_OPTS & GLOBAL_FLAG_OPTS:
    raise RuntimeError(
        "an option cannot be both value-taking and valueless: "
        f"{sorted(GLOBAL_VALUE_OPTS & GLOBAL_FLAG_OPTS)}"
    )
if len(GLOBAL_VALUE_OPTS) < 7 or len(GLOBAL_FLAG_OPTS) < 22:
    raise RuntimeError(
        f"global-option allowlist shrank below its floor: {len(GLOBAL_VALUE_OPTS)} value-taking "
        f"(min 7), {len(GLOBAL_FLAG_OPTS)} valueless (min 22)"
    )


def classify_global_opt(opt: str) -> str:
    """Classify a global option token as ``value`` / ``flag`` / ``unknown``.

    ``value`` consumes the following token; ``flag`` does not; ``unknown`` means this walk cannot
    say which, so the caller must treat the invocation as unjudgeable and block rather than guess.
    An attached long value (``--git-dir=/x``) is self-contained and therefore classifies as
    ``flag`` — but only once its BASE name is recognised, or an unknown ``--foo=bar`` would read
    as judged simply for containing an ``=``.
    """
    if opt.startswith("--") and "=" in opt:
        base = opt.split("=", 1)[0]
        return (
            "flag"
            if base in GLOBAL_VALUE_OPTS or base in GLOBAL_FLAG_OPTS
            else "unknown"
        )
    if opt in GLOBAL_VALUE_OPTS:
        return "value"
    if opt in GLOBAL_FLAG_OPTS:
        return "flag"
    # Attached `-C<path>`, the one short option with an attached form git actually accepts. The
    # caller reads the path off it; here it only needs to not consume the next token.
    if opt.startswith("-C") and len(opt) > 2:
        return "flag"
    return "unknown"


# `\+?=` (not just `=`): a `VAR+=val` append-form prefix is legal bash and, before this widened,
# was invisible here -- `starts_command`/`_env_prefix` read `git` in `FOO+=1 git push` as being in
# ARGUMENT position (the append token matched neither this regex nor a known wrapper), so
# `iter_git_invocations_detailed` returned ZERO invocations for the whole command. Nothing
# downstream -- publication-push-guard.py, git-timing-guard.py, and every other consumer of this
# walk -- could judge what it never saw. Widening only GROWS the match set, so no token that was a
# command word stops being one; the audited risk is a caller that extracts a NAME via
# `token.split("=", 1)[0]`, which now yields `"VAR+"` for an append token instead of `"VAR"` --
# every such call site in this repo was audited when this widened (see the branch history), and
# the THREE that consume env-prefix tokens (`_config_scope_is_local`, `_config_injection_reason`
# and `_exported_injection_reason`'s walker) use `_assign_name`/`_is_assign_token` rather than a bare
# split.
#
# THAT AUDIT WAS INCOMPLETE. Consumers are exposed in TWO distinct ways, and this audit had only
# looked at one of them:
#
#   SELECTION -- takes a decision from "the FIRST push found", so it changes purely because the
#     walk now RETURNS MORE INVOCATIONS. `git-timing-guard.py` is the one in this repo:
#       ALLOW_GIT_WRITE=1 FOO+=1 git <push> … && git <push> …
#         dev     -> 1 invocation; the FIRST push is invisible and runs unjudged
#         widened -> 2 invocations, and its target directory is read from the first
#   TOKEN-PARSING -- reads the leading assignment run itself. `push-guard.py` is this, not a
#     selection consumer: its own docstring says it "Consumes `iter_context_token_streams`, NOT
#     `iter_git_invocations_with_cwd`". Its verdict moved because ENV_ASSIGN now matches
#     `FOO+=1` inside `_leading_env_authorized`'s scan. (An earlier version of this paragraph
#     filed it under SELECTION -- wrong, and it deleted the timing-guard example that was the
#     real selection case, leaving the file asserting two of a kind with evidence for neither.)
#
# Measured END TO END on `push-guard.py`, by exit code rather than a helper's return value:
#
#   FOO+=1 ALLOW_PUSH=1 git <push> origin dev   dev rc=0 -> now rc=0   (unchanged)
#   FOO+=1 git <push> origin dev                dev rc=0 -> now rc=2   (MOVED, safe direction)
#   FOO=1 ... (control, both spellings)                    unchanged
#
# So the widening makes that gate STRICTLY STRONGER: on `dev` an unauthorized push behind a `+=`
# prefix was invisible and allowed; it is now caught. `_leading_env_authorized` does read
# differently on the two builds, but the composed verdict never becomes more permissive -- which
# is why a helper's reading is not the thing to quote.
#
# Two lessons for the next person widening this. Ask what changes for consumers that SELECT among
# invocations AND for those that PARSE the leading run -- they are different exposures and a
# consumer is usually only one of them. And read the composed VERDICT: a helper's return moving
# is not the gate's answer moving, and the two pointed opposite ways here.
ENV_ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*\+?=")


class ParseAmbiguity(ValueError):
    """A construct this tokenizer cannot read unambiguously, carrying WHERE it gave up.

    `str()` is BYTE-IDENTICAL to the bare `ValueError` message this replaced. That is the whole
    safety property: every consumer catches bare `except ValueError`, so behaviour is unchanged and
    the position is purely additive — a reader that wants it opts in via `isinstance`, and one that
    does not is unaffected.

    `pos` indexes the PREPARED, per-context text the scanner was reading, NOT the user's original
    command. Earlier passes insert escapes, delete comment spans and fold continuations, and at
    `depth > 0` the text is an extracted context body. Anything rendering `pos` must say so, and
    should present the EXCERPT as the locator rather than the offset — an offset the reader cannot
    find in their own text is worse than none, because it mis-teaches.

    Not every ambiguity carries a position, and that is deliberate. Depth-exceeded, length-exceeded
    and internal-marker errors keep raising plain `ValueError`: for those the CATEGORY is the whole
    answer and a caret would be decoration. `shlex` also raises its own `ValueError` through this
    module (`No escaped character`, `No closing quotation`), which likewise carries nothing. Any
    consumer must therefore treat position data as optional and degrade to the category alone.
    """

    def __init__(
        self,
        category: str,
        *,
        text: str = "",
        pos: int | None = None,
        depth: int = 0,
    ) -> None:
        # Single positional arg to super() is what pins str() to the category.
        super().__init__(category)
        self.category = category
        self.text = text
        self.pos = pos
        self.depth = depth


def describe_ambiguity(exc: BaseException | None) -> str:
    """A human-locatable clause for a `ParseAmbiguity`, or "" for anything without a position.

    CONTRACTUALLY NON-RAISING, and that is load-bearing rather than defensive habit. Both push
    guards call this from INSIDE the `except` block that emits their refusal, and per
    `scripts/HOOKS.md` only exit 2 blocks — any other nonzero exit is noise, not a veto. An
    exception escaping here would exit 1 and the guarded command would RUN. Every failure therefore
    degrades to "", the refusal is emitted unchanged, and the block is never contingent on the
    diagnostic succeeding. A diagnostic that can unblock is strictly worse than no diagnostic.

    Returns "" for: a None cause; a plain `ValueError` (the depth, length, internal-marker and
    reserved-marker sites, plus `shlex`'s own errors, none of which carry attributes); a `pos`
    that is None or out of range after prepared-text drift.

    The offsets are into the PREPARED, per-context text — earlier passes insert escapes, delete
    comment spans and fold continuations, and at depth > 0 the text is an extracted context body.
    The clause therefore offers the EXCERPT as the locator and states the caveat inline: an offset
    a reader cannot find in their own command mis-teaches, which is the one way adding a position
    could be worse than the category alone.

    It deliberately does NOT name a tool: the path differs per caller and resolving it here would
    hardcode one caller's layout into the shared tokenizer.

    WHICH READING the position belongs to, now that the walk enumerates readings: always the
    PRIMARY (all-drop) one, so this function needs no variant dimension and deliberately has none.
    Two facts compose to that. `_walk_context` raises only when EVERY reading of a context failed,
    so a surviving reading never reaches here — a lost reading beside a surviving one is reported
    in-band by `_indeterminate_invocation`/`_indeterminate_stream` instead, and named per record by
    `iter_git_invocations_with_readings`. And `_reading_assignments` yields all-drop FIRST while
    the walk keeps the FIRST failure, so the exception that escapes is the one the all-drop reading
    produced. Measured rather than reasoned: over 4,000 generated inputs plus crafted witnesses,
    2,669 raises carried a position and every one was byte-identical in category, text and pos to
    what a primary-only walk raises — with no case where the primary alone parsed. So the excerpt
    an operator is shown is text from the reading bash takes at the top level, not from a variant
    they would fail to find in their own command.
    """
    try:
        text = getattr(exc, "text", None)
        pos = getattr(exc, "pos", None)
        if not isinstance(text, str) or not isinstance(pos, int):
            return ""
        if not 0 <= pos < len(text):
            return ""
        nl = text.rfind("\n", 0, pos)
        line = text.count("\n", 0, pos) + 1
        col = pos - (nl + 1) + 1
        end = text.find("\n", pos)
        excerpt = text[nl + 1 :] if end == -1 else text[nl + 1 : end]
        if len(excerpt) > 120:
            excerpt = excerpt[:117] + "..."
        depth = getattr(exc, "depth", 0)
        where = "the command" if not depth else f"an extracted context (depth {depth})"
        clause = (
            f" The construct opens at line {line}, col {col} of {where}, in: {excerpt!r}"
            f" (offsets are into the text AFTER comment-stripping and escape-masking, so search"
            f" for the excerpt rather than the offset)."
        )
        if "<<" in text:
            clause += (
                " This command contains a heredoc: if its delimiter is UNQUOTED, bash really does"
                " expand backticks and $( ) inside the body, so this refusal is protecting you."
                " Quoting the delimiter makes the body literal and removes the ambiguity."
            )
        return clause
    except Exception:  # noqa: BLE001 - deliberate: a broken diagnostic must never unblock
        return ""


class InvocationTokens(NamedTuple):
    """The tokens a consumer needs to judge an invocation's CONFIGURATION, not just its subcommand.

    Carried on the invocation record rather than returned as a parallel list, for the same reason
    the walk tracks `cd` itself: only this walk knows which tokens belong to which invocation, so
    alignment is structural instead of asserted. A consumer scoping a check to `opts`/`env` cannot
    match text belonging to some other command in the same line — which is what makes a
    whole-command regex false-block a read-only `grep 'core.hooksPath=…' git-hooks/pre-push`.

    Attributes:
        env: Env-assignment tokens immediately preceding the `git` token (`FOO=1 git …`).
        opts: The global-option run between `git` and the subcommand, verbatim and in order.
    """

    env: list[str]
    opts: list[str]


class Invocation(NamedTuple):
    """One `git` invocation the walk found, with everything known about it at that position.

    A NamedTuple rather than a bare tuple so the record's SHAPE is stated once, here, instead of
    being respelled by every annotation that mentions it. It was previously a bare 5-tuple restated
    in three places — `_walk_context`'s return annotation, its `results` declaration and its
    docstring — and all three had drifted to four while the code appended five. Nothing failed,
    because an annotation is not checked at runtime; the cost was paid by the next reader.

    Slicing yields a PLAIN tuple, which is what keeps `iter_git_invocations_with_cwd`'s four-tuple
    contract byte-identical for the ~40 call sites that unpack it positionally.

    Attributes:
        effective_dir: Working directory in force here, or None when it is not statically knowable.
        cdir: This invocation's own `-C` value, if it carried one.
        subcommand: The token in the subcommand slot — which may be an unjudgeable option token.
        arg_tokens: The invocation's argument segment.
        tokens: Its env prefix and global-option run — see `InvocationTokens`.
    """

    effective_dir: str | None
    cdir: str | None
    subcommand: str
    arg_tokens: list[str]
    tokens: InvocationTokens


# Exec-wrappers that run their argument as a command, so `git` right after one is still in command
# position (`sudo git commit`, `time git commit`). Bounded on purpose — an unknown leading word
# (`echo git commit`) is treated as NOT a command, preserving the phantom-commit guard.
#
# `eval` and `builtin` run their arguments too: measured under bash 3.2.57 and 5.3.15, `eval git …`
# and `builtin eval git …` run git, and both were invisible to every consumer before
# fix/eval-wrapper-bypass. `builtin git …` itself errors ("not a shell builtin"); reading it as an
# invocation over-blocks a command that cannot run, the safe direction.
#
# A `cd`/`pushd`/`popd` reached through ANY member makes the tracked cwd UNRESOLVABLE — see
# `_cd_command_position` — because whether the shell really moves depends on the whole chain.
#
# Adding a member moves consumers along the two axes `ENV_ASSIGN`'s comment names. SELECTION:
# `git-timing-guard.py` now finds eval-reached pushes, in bash's own order — but that guard judges
# only the FIRST push it finds in a command's token stream, so seeing an eval'd push can change
# WHICH push gets judged: a push that would otherwise have been second (and so ignored either way)
# can become the one this guard sees instead. Pre-existing, not introduced by this set gaining
# `eval`; filed (see Ruling R8, design record 2026-09-11-eval-wrapper-bypass). TOKEN-PARSING:
# push-guard's and the timing guard's `_leading_env_authorized` never skip a member of this set, so
# an override placed BEHIND `eval` is refused there although bash would authorize it — a
# safe-direction over-block, pinned in test_push_guard.sh and test_git_timing_guard.sh. Each guard
# checks its OWN override token: `eval ALLOW_PUSH=1 git <push>` is refused by push-guard.py, and
# `eval ALLOW_GIT_WRITE=1 <publish>` is refused by git-timing-guard.py — the two guards use
# different token names (`ALLOW_PUSH=1` and `ALLOW_GIT_WRITE=1` respectively). An override placed
# BEFORE `eval` (`ALLOW_PUSH=1 eval git <push>` / `ALLOW_GIT_WRITE=1 eval <publish>`) is authorized
# by each guard, in each case because bash passes the assignment straight through to eval.
WRAPPERS = {
    "time",
    "env",
    "sudo",
    "doas",
    "nice",
    "ionice",
    "nohup",
    "setsid",
    "stdbuf",
    "command",
    "xargs",
    "timeout",
    "eval",
    "builtin",
}

# Reserved words that open a new command exactly as a control operator does: `if git push` puts
# `git` in command position the same way `; git push` does. Consulted by BOTH `_git_starts_command`
# — the `is_git(...)`-guarded call sites below, recognising a `git` invocation — and
# `_cd_command_position`, which classifies the cwd. The two read this set for DIFFERENT purposes,
# and a membership change must be justified against BOTH before it lands.
#
# A THIRD reading lives in `_walk_context`'s argument-segment scan, which must NOT treat this set as
# ending an argument list: behind an argument word none of these is a keyword to bash, so the scan
# runs on to the next operator and only resumes at the nested `git`. See the comment at that scan.
#
# `_cd_command_position` returns None — UNRESOLVABLE — for a reserved word, and returning True
# there would be wrong. Measured: reading a `cd` right after `if`/`while`/`!` as TRACKED makes
# three EXISTING blocks disappear, because cwd tracking follows it into a directory with no
# `.publication.toml` and the guard goes dormant there —
# `! cd OTHER ; <push>`, `if cd OTHER ; then :; fi ; <push>` and
# `while cd OTHER ; do break; done ; <push>` all flip BLOCK -> ALLOW under a TRACKED reading, even
# though the push invocation is still *detected*. A detection-only property passes while that
# happens: the property that matters here is that the BLOCKED SET does not shrink AT THE
# PUBLICATION GUARD. That is a per-consumer property — the TIMING guard's blocked set DOES shrink
# for this class, because that guard maps an unresolvable cwd onto the PAYLOAD cwd instead of
# blocking (pinned by the "reserved-word cd trade" rows in scripts/tests/test_git_timing_guard.sh;
# the full per-consumer table is in `specs/2026-09-11-reserved-word-cd.md`).
#
# That three-block measurement is exactly WHY the answer is None and not True: None keeps all
# three BLOCKED, since an unresolvable cwd is fail-closed for the publication guard, while ALSO
# closing the opposite direction that False left open — `if cd ADOPTED; then <push>; fi` from a
# plain repo, measured rc 0 before this branch and rc 2 after. Do not re-derive the old conclusion
# from the three rows above: they argue against TRACKING, never against this set being consulted
# at all.
#
# `in` and `;;` are deliberately excluded: both are followed by a *pattern*, not a command, so
# treating them as boundaries would let `for f in git; do …` manufacture a phantom `git` invocation
# out of a loop list item. `}`, `fi`, `done`, `esac` are also excluded: each ENDS a block, and bash
# treats a command placed directly after one — with no `;` or newline between — as a syntax error,
# so admitting them as boundaries would add no real detection.
#
# For `_cd_command_position` those same exclusions are right for DIFFERENT reasons. `in` is
# followed by a pattern, so `for f in cd; do …` runs no `cd` at all and must not be made
# unresolvable. `}`, `fi`, `done` and `esac` cannot be followed directly by a command, so no `cd`
# can sit after one for this function to classify.
RESERVED_WORDS = frozenset(
    {"{", "!", "if", "then", "elif", "else", "while", "until", "do", "coproc"}
)

# `exec` replaces the shell with its argument, so `git` right after it is in command position
# exactly as after `sudo`/`time`/etc. — but it is kept OUT of the shared `WRAPPERS` set. Folding it
# in was once measured to send `exec cd OTHER ; <push>` from BLOCK to ALLOW, because a `cd` right
# after `exec` would then also start being TRACKED into a directory with no marker. That leak can
# no longer occur: a `cd`/`pushd`/`popd` reached through any `WRAPPERS` member now makes the cwd
# UNRESOLVABLE rather than tracked — see `_cd_command_position` — so `exec` staying out buys nothing
# there any more.
#
# The set stays anyway, for a reason unrelated to cd tracking: `push-guard.py` and
# `git-timing-guard.py` consult `gitcmd.GIT_ONLY_WRAPPERS` at the head of their own authorization
# walks (push-guard.py:129, git-timing-guard.py:248), and folding `exec` into `WRAPPERS` would
# change their reading of `exec ALLOW_PUSH=1 git …`. Within this module it is consulted by
# `_git_starts_command` and by `_env_prefix`'s own wrapper walk.
GIT_ONLY_WRAPPERS = frozenset({"exec"})

# Inert marker substituted for each extracted nested context. It must tokenize as an ordinary word
# and must never look like a git invocation, a control operator, or a redirect. It is INDEXED
# because the walk needs to recurse into a context at the SOURCE POSITION where it appeared — `cd`
# semantics depend on order, so an unindexed marker would lose the information the walk needs.
PLACEHOLDER_PREFIX = "__GIT_COMMAND_SUBST_"
PLACEHOLDER_SUFFIX = "__"
_PLACEHOLDER_RE = re.compile(rf"{PLACEHOLDER_PREFIX}(\d+){PLACEHOLDER_SUFFIX}")

# Bound on nesting depth. Far beyond any realistic command; exists only to stop pathological input.
MAX_CONTEXT_DEPTH = 8

# Bound on input SIZE. Placeholder substitution fuses adjacent substitutions into one very large
# shlex token and shlex accumulates character-by-character, so cost grows quadratically: measured
# ~0.5 s at 16 KB, ~7 s at 62 KB, ~30 s at 128 KB. A consumer gate runs under a 60 s hook timeout,
# past which the hook is KILLED by signal and never reaches its own fail-closed handler — a silent
# bypass produced by slowness alone. Refusing is loud and bounded; no real command approaches this.
MAX_COMMAND_LENGTH = 65_536


def _placeholder(index: int) -> str:
    """The inert marker standing in for extracted context `index`."""
    return f"{PLACEHOLDER_PREFIX}{index}{PLACEHOLDER_SUFFIX}"


def _placeholder_indices(token: str) -> list[int]:
    """Context indices marked inside one token, in order (a token may carry several)."""
    return [int(m) for m in _PLACEHOLDER_RE.findall(token)]


class CommandContext(NamedTuple):
    """One nested command context extracted from a command string.

    Every construct this module models (`$( … )`, backticks, `<( … )`, `>( … )`) is a real
    subshell, so cwd isolation is universal and needs no per-context flag — a flag that is always
    True would be a constant named like a model.

    Attributes:
        text: The context's own command text, to be scanned in its own quote context.
        depth: Nesting depth, 0 for the top-level command.
    """

    text: str
    depth: int


def strip_comments(text: str) -> str:
    """Remove `#`-to-end-of-line comments, quote- and escape-aware, preserving newlines.

    This is the right LAYER for shell comments, and both obvious shortcuts are measured defects.
    Disabling shlex's comment handling instead makes comment CONTENT into live tokens (a false
    block on `git push origin main  # publish`); stripping after newlines are folded to `;` lets a
    comment swallow the rest of the command instead of the rest of the line.

    A `#` starts a comment only at a word boundary, and the boundary set here is deliberately a
    SUBSET of the shell's: this pass may over-KEEP a comment (costing at most a loud false block)
    but must never over-REMOVE, because it deletes text before anything else sees it — a false
    positive here is a bypass, not a false block.

    NOT sufficient alone: quote tracking is linear over pre-split text, which is exactly what
    Defect A shows can drift, so `tokenize` also disables shlex comments to make a surviving `#`
    inert. See the two-layer note in `tokenize`.

    Args:
        text: The command text, with newlines still present.

    Returns:
        The text with comment spans removed and newlines preserved.
    """
    out: list[str] = []
    i, n = 0, len(text)
    quote: str | None = None
    at_word_start = True
    while i < n:
        ch = text[i]
        if ch == "\\" and quote != "'" and i + 1 < n:
            out.append(ch)
            out.append(text[i + 1])
            i += 2
            at_word_start = False
            continue
        if quote is not None:
            if ch == quote:
                quote = None
            out.append(ch)
            i += 1
            at_word_start = False
            continue
        if ch in "'\"":
            quote = ch
            out.append(ch)
            i += 1
            at_word_start = False
            continue
        if ch == "#" and at_word_start:
            while i < n and text[i] != "\n":
                i += 1
            continue
        out.append(ch)
        at_word_start = ch in " \t\n;&|("
        i += 1
    return "".join(out)


def _skip_comment(text: str, i: int) -> int:
    """Index of the newline ending an unquoted comment starting at `i` (or end of text)."""
    while i < len(text) and text[i] != "\n":
        i += 1
    return i


# Characters that end an unquoted heredoc delimiter word.
_HEREDOC_DELIM_END = " \t\n;&|<>()"


def _first_unmatched_quote(text: str) -> int | None:
    """Index of the quote character that is still open at the end of `text`, or None if balanced.

    Backslash is modelled as escaping, matching what the later passes (and shlex) do — this
    function exists to predict THEIR reading of the text, not the shell's reading of a heredoc.
    """
    i, n = 0, len(text)
    quote: str | None = None
    opener: int | None = None
    while i < n:
        ch = text[i]
        if ch == "\\" and quote != "'" and i + 1 < n:
            i += 2
            continue
        if quote is not None:
            if ch == quote:
                quote = None
                opener = None
            i += 1
            continue
        if ch in "'\"":
            quote = ch
            opener = i
        i += 1
    return opener


def _neutralize_unmatched_quotes(body: str) -> str:
    """Backslash-escape only the quote characters that would leave `body` unbalanced.

    Escaping every quote in the body is the obvious version and it is a measured REGRESSION: it
    rewrites bodies that already parse correctly, so `bash <<EOF` carrying `"git" push origin dev`
    stopped being seen at all (the token became `"git"`, which `is_git` does not match) — a catch
    that worked before the heredoc pass existed. Touching only unmatched quotes keeps every body
    that parses today byte-identical, so the only behaviour that can change is the behaviour that
    was already broken.

    Deleting the offending quote instead of escaping it is the other tempting shortcut, and it is
    a fail-open: `echo "#" ; git push origin dev` would become `echo # ; git push …`, whose `#`
    then starts a comment that swallows the push.

    Terminates because each pass escapes one quote character and re-scans; the loop is bounded by
    the number of quote characters present.
    """
    text = body
    for _ in range(text.count("'") + text.count('"') + 1):
        idx = _first_unmatched_quote(text)
        if idx is None:
            break
        text = text[:idx] + "\\" + text[idx:]
    return text


def _read_heredoc_delimiter(text: str, i: int) -> tuple[str | None, int, bool]:
    """Read the delimiter word of a heredoc operator at `i`.

    The delimiter may be quoted (`<<'EOF'`, `<<"EOF"`) or escaped (`<<\\EOF`) — which in a real
    shell decides whether the BODY is expanded, but never changes where the body ENDS, which is all
    this pass needs. The quotes are part of the operator, not of the delimiter it names, so they are
    stripped here to get the terminator text to compare lines against.

    Args:
        text: The full command string.
        i: Index of the first character of the delimiter word.

    Returns:
        The delimiter text, the index just past it, and whether ANY part of the word was quoted or
        escaped — bash's rule for a literal body (`E'OF'` counts) — or ``(None, i, False)`` when no
        delimiter can be read (an unterminated quote, or nothing there at all), in which case the
        caller treats the `<<` as ordinary text rather than guessing.
    """
    n = len(text)
    start = i
    parts: list[str] = []
    quoted = False
    while i < n:
        ch = text[i]
        if ch in _HEREDOC_DELIM_END:
            break
        if ch in "'\"":
            close = text.find(ch, i + 1)
            if close == -1:
                return None, start, False
            quoted = True
            parts.append(text[i + 1 : close])
            i = close + 1
            continue
        if ch == "\\" and i + 1 < n:
            quoted = True
            parts.append(text[i + 1])
            i += 2
            continue
        parts.append(ch)
        i += 1
    delim = "".join(parts)
    return (delim, i, quoted) if delim else (None, start, False)


def _odd_trailing_backslashes(s: str) -> bool:
    """True when `s` ends in an ODD run of backslashes — its last one unescaped."""
    return (len(s) - len(s.rstrip("\\"))) % 2 == 1


def _drop_final_continuation(body: str) -> str:
    """Drop a continuation the body's LAST line ends in.

    A heredoc body is the consumer shell's whole input, so a last line ending in an odd backslash
    run hands it a continuation into end of input, which it drops (measured, bash 3.2.57 and 5.3.15:
    `bash <<'EOF'` / `git <push> origin dev\\` / `EOF` pushes `dev`). Doing the same here keeps the
    fold pass from gluing the body onto the terminator line after it — which once recorded the
    refspec `devEOF`, and let the fuzzy target matcher reach the verdict the parse should have.
    """
    if body.endswith("\\\n") and _odd_trailing_backslashes(body[:-1]):
        return body[:-2] + "\n"
    return body


def _bash_unquoted_heredoc(
    text: str, i: int, delim: str, strip_tabs: bool
) -> tuple[str, int, int | None]:
    """Apply bash's own pass to an UNQUOTED heredoc body, returning the text the consumer reads.

    For an unquoted delimiter bash processes the body before the consumer ever sees it: a line
    ending in an odd backslash run is continued into the next BEFORE the terminator comparison —
    so a continuation can swallow the terminator line, and the body then runs on to the next one —
    and `\\\\`, `\\$` and `` \\` `` lose their backslash (see `_unescape_heredoc`). `<<-` strips
    leading tabs from each LOGICAL line, never from a continued physical line (measured:
    `foo \\` / `<TAB>EOF` gives `foo <TAB>EOF`).

    Returns:
        The processed body, the index just past the terminator line(s), and the index where the
        terminator's first physical line starts — or None when no terminator was found, in which
        case the body runs to the end of `text`, as it does in bash.
    """
    n = len(text)
    j = i
    logical: list[str] = []
    body_lines: list[str] = []
    lstart = i
    while j < n:
        if not logical:
            lstart = j
        eol = text.find("\n", j)
        line_end = n if eol == -1 else eol
        piece = text[j:line_end]
        nxt = n if eol == -1 else eol + 1
        if eol != -1 and _odd_trailing_backslashes(piece):
            logical.append(piece[:-1])
            j = nxt
            continue
        logical.append(piece)
        line = "".join(logical)
        if strip_tabs:
            line = line.lstrip("\t")
        if line == delim:
            body = "\n".join(body_lines) + ("\n" if body_lines else "")
            return _unescape_heredoc(body), nxt, lstart
        body_lines.append(line)
        logical = []
        j = nxt
    if logical:
        body_lines.append("".join(logical))
    tail = "\n" if text.endswith("\n") and body_lines else ""
    return _unescape_heredoc("\n".join(body_lines) + tail), n, None


def _opener_is_closed(body: str, idx: int, opener: str) -> bool:
    """Does `body` close the substitution opener at `idx`?

    Answered with the same scanners the walk uses, so the question this asks and the question
    `split_command_contexts` later asks cannot drift apart — two spellings of one intent is itself
    the defect this module has already paid for elsewhere.
    """
    try:
        if opener == "`":
            _scan_to_backtick(body, idx + 1)
        else:
            if not body.startswith("$(", idx):
                return False
            _scan_to_unbalanced_paren(body, idx + 2)
    except ValueError:
        return False
    return True


def _unescape_heredoc(body: str) -> str:
    """bash's backslash handling inside an UNQUOTED heredoc body, working on backslash RUNS.

    - A run directly before `$` or a backtick: when EVEN, the outer shell expands the opener, so
      the run is kept as it is and the later scanner sees an unescaped opener. When ODD, the outer
      shell passes the opener through as text after `(run - 1) / 2` literal backslashes, and the
      consumer shell then decides — so exactly that text is emitted. Measured: a first version
      collapsed `\\\\` before `$(` to one backslash, which the scanner then read as `\\$(` and
      the substitution vanished; `echo "\\\\\\\\$(git status)"` lost its invocation the same way.
    - Any other run: each pair collapses to one backslash; an odd leftover stays with its character.
    - `$( … )` and backtick spans are copied RAW: the outer shell expands them from their raw text
      (measured: `$(echo x\\\\` / `git status)` in an unquoted body runs git at the outer level).
    """
    out: list[str] = []
    k, n = 0, len(body)
    while k < n:
        ch = body[k]
        if ch == "$" and body.startswith("$(", k):
            try:
                _inner, end = _scan_to_unbalanced_paren(body, k + 2)
            except ValueError:
                out.append(body[k:])
                break
            out.append(body[k:end])
            k = end
            continue
        if ch == "`":
            try:
                _inner, end = _scan_to_backtick(body, k + 1)
            except ValueError:
                out.append(body[k:])
                break
            out.append(body[k:end])
            k = end
            continue
        if ch == "\\":
            j = k
            while j < n and body[j] == "\\":
                j += 1
            run = j - k
            nxt = body[j : j + 1]
            if nxt in ("$", "`"):
                if run % 2 == 0:
                    out.append("\\" * run)
                    k = j
                    continue
                # ODD run: the outer shell passes the opener through as TEXT and the consumer
                # shell decides — but only if the body CLOSES it. An unclosed opener runs nothing
                # in either shell (literal text outside, a syntax error inside), so emitting it
                # bare buys no capability and costs the whole command: it reaches
                # `split_command_contexts`, which raises, and two guards then fail closed on
                # ordinary prose while the timing guard fails OPEN and loses a push the
                # pre-change tokenizer saw. `Don\\`t` is the only legal spelling of a literal
                # backtick in an unquoted body, so that is prose, not an adversarial shape.
                #
                # Leaving the author's own escape in place is what `dev` already emits here, so
                # this narrows to exactly the inputs this change had widened. It is NOT the
                # 2026-09-03 "escape the unmatched opener" repair, which ADDED an escape to a raw
                # opener and was measured taking command position and swallowing the `git` behind
                # it — nothing is added here.
                if _opener_is_closed(body, j, nxt):
                    out.append("\\" * ((run - 1) // 2))
                    out.append(nxt)
                    k = j + 1
                    continue
                out.append("\\" * ((run - 1) // 2))
                out.append("\\")
                out.append(nxt)
                k = j + 1
                continue
            out.append("\\" * (run // 2 + run % 2))
            k = j
            continue
        out.append(ch)
        k += 1
    return "".join(out)


class _ReadingState:
    """Which READING each ambiguous heredoc gets, and how many were found.

    An ambiguous heredoc is a QUOTED-delimiter body whose last line ends in an odd backslash run:
    bash drops that continuation at the top level, joins it onto the terminator inside backticks,
    and splits by version inside `$( … )` (3.2 joins, 5.3 drops). The module used to pick one
    reading from a CONTEXT question its own quote model answers unreliably; a desync then dropped a
    refspec both bashes pass to git, and the publication guard went from blocking to allowing.

    Ordinals are consumed ONLY by an ambiguous heredoc, in encounter order, so ordinal `i` names the
    same heredoc under every assignment — which is what makes `count` a sound basis for enumerating
    `2**count` assignments without a second scan.
    """

    __slots__ = ("readings", "count")

    def __init__(self, readings: Mapping[int, bool] | None = None) -> None:
        self.readings = dict(readings or {})
        self.count = 0

    def next_reading(self) -> bool:
        """Reading for the next ambiguous heredoc: True = JOIN, False (the default) = DROP."""
        ordinal = self.count
        self.count += 1
        return self.readings.get(ordinal, False)


def _consume_heredoc_body(
    text: str,
    i: int,
    delim: str,
    strip_tabs: bool,
    out: list[str],
    quoted: bool = False,
    top_level: bool = True,
    depth: int = 0,
    state: "_ReadingState | None" = None,
) -> int:
    """Copy one heredoc body to `out` as the consumer shell will read it, with its unmatched quotes
    neutralised, and return the index just past its terminator line.

    The terminator line is operator text rather than body, so it is copied verbatim — neutralising
    it could stop a later run from recognising it and swallow the rest of the command.

    Since 2026-09-18 the body is no longer copied byte-for-byte in every case, because bash itself
    does not hand it over byte-for-byte — each rule below was measured against bash 3.2.57 and
    5.3.15, and together they are held to a differential test that runs bash itself:

    - `<<-` strips leading tabs from every body line (quoted and unquoted alike);
    - an UNQUOTED body at the top level gets bash's own pass (`_bash_unquoted_heredoc`), and the
      result is re-masked, since it is command text the consumer reads its OWN heredocs from —
      bounded by `MAX_CONTEXT_DEPTH`, past which the body is copied through verbatim instead;
    - a QUOTED body whose last line ends in a continuation is AMBIGUOUS in every context — bash
      drops it at the top level, joins it onto the terminator inside backticks, and splits by
      version inside `$( … )` (3.2 joins, 5.3 drops). Which reading this pass emits is an INPUT
      (`state`), not a question about context: the context answer came from this module's own
      quote model, which desyncs from bash on known shapes, and a desync then dropped a refspec
      both bashes pass to git. Exactly ONE well-formed reading is emitted per call, so no text is
      ever duplicated — emitting the body twice was measured to unbalance an enclosing `$( … )`
      into a raise, which two guards read as ALLOW. The WALK enumerates the assignments;
    - an unquoted body inside a substitution keeps the old handling; the walk re-prepares each
      extracted context as top-level text, which is where its body gets bash's pass.

    An unterminated body (no line ever equals `delim`) consumes the remainder. That tolerates
    quotes in text the shell would also treat as body, and keeps the text visible; it never hides
    a command.

    Args:
        state: Reading assignment plus the ambiguity counter, shared with the caller and with the
            recursive re-mask of an unquoted body so parent and child share ONE ordinal namespace.
            None creates a private all-drop state: calling `next_reading()` on None would raise
            `AttributeError`, which is OUTSIDE this module's ValueError taxonomy and would escape
            every consumer that catches only ValueError.
    """
    state = state if state is not None else _ReadingState()
    n = len(text)
    if not quoted and top_level and depth < MAX_CONTEXT_DEPTH:
        body, end, found = _bash_unquoted_heredoc(text, i, delim, strip_tabs)
        if found is not None:
            body = _drop_final_continuation(body)
        # ONE ordinal namespace with the parent: without the shared state the heredocs inside an
        # unquoted body would be drop-only forever, unreachable by any assignment.
        body = mask_heredoc_quotes(body, depth + 1, state)
        out.append(_neutralize_unmatched_quotes(body))
        if found is None:
            return n
        out.append(text[found:end])  # the terminator line(s), verbatim
        return end
    body_start = i
    term_start: int | None = None
    term_end = n
    j = i
    while j < n:
        eol = text.find("\n", j)
        line_end = n if eol == -1 else eol
        line = text[j:line_end]
        if (line.lstrip("\t") if strip_tabs else line) == delim:
            term_start = j
            term_end = n if eol == -1 else eol + 1
            break
        if eol == -1:
            break
        j = eol + 1
    body_end = n if term_start is None else term_start
    body = text[body_start:body_end]
    if strip_tabs:
        body = "\n".join(line.lstrip("\t") for line in body.split("\n"))
    if quoted and term_start is not None:
        dropped = _drop_final_continuation(body)
        if dropped != body:
            # Ambiguous in EVERY context; the reading is an input. Duplicating the body here
            # instead was measured to unbalance an enclosing `$( … )` into a raise, which two
            # guards read as ALLOW.
            body = body if state.next_reading() else dropped
    out.append(_neutralize_unmatched_quotes(body))
    if term_start is None:
        return n
    # The terminator is operator text, not body — copy it verbatim.
    out.append(text[term_start:term_end])
    return term_end


def mask_heredoc_quotes(
    command: str, _depth: int = 0, state: "_ReadingState | None" = None
) -> str:
    """Escape the UNMATCHED quote characters inside each heredoc body, leaving all else alone.

    **A heredoc body is literal text, not a quoting context.** A real shell performs no quote
    removal on heredoc content, so `'` and `"` in a body are ordinary characters. Modelling them as
    quoting operators drifts this module's quote state, and the drift is not contained: the body's
    lone apostrophe made an enclosing `$( … )` look unterminated, so `split_command_contexts` raised
    and every consumer gate FAILED CLOSED on an innocent command. Measured 2026-07-28 against the
    heredoc commit form `/commit`'s own SKILL.md prescribes — the plain form passed and an
    apostrophe-free heredoc passed, so only the combination failed and nothing had ever run it.

    The body is escaped rather than removed **on purpose**. A heredoc body is still ordinary command
    text to the walk that follows — `bash <<EOF … EOF` really does execute what it carries, and that
    is caught today — so a pass that made bodies inert would trade a loud false block for a silent
    bypass. Escaping fixes the parity while leaving every word exactly where it was.

    Only UNMATCHED quotes are touched (see `_neutralize_unmatched_quotes`), which is what keeps this
    pass from having a blast radius: a body whose quotes already balance comes out byte-identical,
    so no command that parses today can start parsing differently. Escaping every quote instead was
    measured to lose a real catch.

    Runs FIRST, ahead of `strip_comments`: a body's quotes must already be inert before any later
    pass tracks quote state, and comment stripping is itself one of those passes.

    `<<<` is a herestring, not a heredoc, and is deliberately not matched — its operand is ordinary
    quoted text that the existing scanners already handle.

    Args:
        command: The raw shell-command string.
        _depth: Nesting depth of `command` itself, for the recursive unquoted-body re-mask.
        state: Reading assignment for the ambiguous heredocs this pass meets, plus the counter that
            numbers them. None makes a private all-drop state, so a caller that does not care about
            readings keeps the historical single-text behaviour. `_mask_with_count` is the entry
            point that reads the count back out.

    Returns:
        The command with quote characters inside heredoc bodies backslash-escaped.
    """
    state = state if state is not None else _ReadingState()
    out: list[str] = []
    # (delimiter, strip leading tabs), in the order the operators appeared on the line.
    pending: list[tuple[str, bool]] = []
    # Quote state saved at each open command substitution. A `$( … )` body has its OWN quote
    # context — the same fact `split_command_contexts` relies on — so without this stack the `"` of
    # `git commit -m "$(cat <<'EOF' … )"` keeps this pass in double-quote mode and it never sees the
    # heredoc operator at all. That is the reported form, so the stack is not an edge case.
    contexts: list[str | None] = []
    # Whether an unescaped backtick is open. Backticks are not quote contexts here (the stack above
    # is); this feeds `top_level`, which since 2026-09-19 gates ONLY the unquoted-body pass. A
    # heredoc inside a backtick span is not top-level text — backtick text loses one backslash of
    # each pair before it runs, so this pass cannot yet see the body bash will read, and the walk
    # re-prepares the extracted backtick text afterwards. The drop/join decision no longer asks
    # this question at all: it is an input (`state`), because the answer came from this pass's own
    # quote model and a desync let a push of `dev` through the publication guard.
    backtick_open = False
    i, n = 0, len(command)
    quote: str | None = None
    at_word_start = True
    while i < n:
        ch = command[i]
        if ch == "\\" and quote != "'" and i + 1 < n:
            out.append(command[i : i + 2])
            i += 2
            at_word_start = False
            continue
        if ch == "`" and quote != "'":
            backtick_open = not backtick_open
            out.append(ch)
            i += 1
            # As before backticks were tracked: a `#` right after one never starts a comment.
            at_word_start = False
            continue
        if quote != "'" and command.startswith("$(", i):
            contexts.append(quote)
            quote = None
            out.append("$(")
            i += 2
            at_word_start = True
            continue
        if quote is None and ch in "<>" and command.startswith("(", i + 1):
            contexts.append(quote)
            out.append(command[i : i + 2])
            i += 2
            at_word_start = True
            continue
        if quote is None and ch == ")" and contexts:
            quote = contexts.pop()
            out.append(ch)
            i += 1
            at_word_start = False
            continue
        if quote is not None:
            if ch == quote:
                quote = None
            out.append(ch)
            i += 1
            at_word_start = False
            continue
        if ch in "'\"":
            quote = ch
            out.append(ch)
            i += 1
            at_word_start = False
            continue
        # A comment is copied through verbatim: `#` runs to end of line, and a `<<` inside one is
        # not an operator. Skipping this would let commented-out text open a phantom heredoc.
        if ch == "#" and at_word_start:
            end = _skip_comment(command, i)
            out.append(command[i:end])
            i = end
            continue
        if command.startswith("<<", i) and not command.startswith("<<<", i):
            j = i + 2
            strip_tabs = False
            if j < n and command[j] == "-":
                strip_tabs = True
                j += 1
            k = j
            while k < n and command[k] in " \t":
                k += 1
            delim, past, quoted = _read_heredoc_delimiter(command, k)
            if delim is not None:
                out.append(command[i:past])
                pending.append((delim, strip_tabs, quoted))
                i = past
                at_word_start = False
                continue
        if ch == "\n" and pending:
            # Bodies start on the line AFTER the operators, in the order the operators appeared —
            # `cat <<A <<B` reads A's body first, then B's.
            out.append(ch)
            i += 1
            for delim, strip_tabs, quoted in pending:
                i = _consume_heredoc_body(
                    command,
                    i,
                    delim,
                    strip_tabs,
                    out,
                    quoted,
                    top_level=not contexts and not backtick_open,
                    depth=_depth,
                    state=state,
                )
            pending = []
            at_word_start = True
            continue
        out.append(ch)
        at_word_start = ch in " \t\n;&|("
        i += 1
    return "".join(out)


def fold_continuations(command: str) -> str:
    """Remove the backslash-newline continuations bash removes — and only those.

    One half of `normalize_command`. The halves are exposed separately because they belong on
    OPPOSITE sides of the context scan: continuations must be folded BEFORE scanning (or
    `x="$\\<newline>(git push)"` reassembles into a substitution nothing ever scanned), while
    newlines must survive until AFTER it (comment handling needs the newline that ends a comment).
    Composed in order they are exactly `normalize_command` — never re-derive either half.

    **Only an UNESCAPED backslash before an LF continues a line**, i.e. the run of backslashes
    ending at the LF has ODD length. Until 2026-09-18 this removed every `\\` + LF and every
    `\\` + CRLF, which HID commands: `echo a\\\\` + LF + `git <push> origin dev` is two commands in
    bash (the backslash is escaped) and `echo a\\` + CRLF + `git <push> …` is two as well (the
    backslash escapes the CR, and the LF still ends the line) — measured under bash 3.2.57 and
    5.3.15 — yet both folded into one `echo` and both push guards allowed the push. Joining lines
    REMOVES a command boundary, so folding one bash does not fold is a fail-open, not a safe
    over-read. Quote context is still not modelled: inside single quotes bash keeps a backslash-
    newline literal and this folds it, a difference that stays inside the quoted word.

    **Backslashes at END OF INPUT are dropped.** bash 3.2 drops an unescaped one (and, measured,
    more in some contexts); 5.3 keeps them literal, and git then rejects the word as a command or
    a ref. Dropping them yields the reading that RUNS under every version.

    Args:
        command: The raw shell-command string.

    Returns:
        The command with bash's backslash-newline continuations removed.
    """
    out: list[str] = []
    i, n = 0, len(command)
    while i < n:
        if command[i] != "\\":
            j = command.find("\\", i)
            j = n if j == -1 else j
            out.append(command[i:j])
            i = j
            continue
        j = i
        while j < n and command[j] == "\\":
            j += 1
        run = j - i
        if run % 2 == 1 and j < n and command[j] == "\n":
            out.append("\\" * (run - 1))
            i = j + 1
            continue
        if j == n:
            # backslashes at END OF INPUT: bash 3.2 drops an unescaped one (and, measured, more
            # in some contexts); 5.3 keeps them literal, which git then rejects as a command or
            # ref. Dropping them all yields the reading that RUNS under every version.
            i = j
            continue
        out.append(command[i:j])
        i = j
    return "".join(out)


def newlines_to_separators(command: str) -> str:
    """Rewrite the newlines that survive into `;` command separators.

    The other half of `normalize_command`; see `fold_continuations` for why they are separate.

    Args:
        command: The command text, continuations already folded.

    Returns:
        The command with newlines rewritten as `;` separators.
    """
    return command.replace("\n", " ; ").replace("\r", " ")


def _scan_to_unbalanced_paren(text: str, start: int) -> tuple[str, int]:
    """Scan from just after an opening `$(`/`<(`/`>(` to its matching `)`.

    Quote state is tracked so a `)` inside a quoted span does not close the context, and nesting is
    counted so `$(( … ))` and `$( (…) )` both consume correctly. A backslash escapes the next
    character, and a `#` comment runs to end-of-line — a `)` inside either does NOT close the
    context, which bash agrees with (`x="$(echo hi  # )"` is an unterminated-substitution error).
    Without the comment rule the body is truncated at the commented paren and everything after it
    silently vanishes.

    Args:
        text: The full command string.
        start: Index of the first character INSIDE the context.

    Returns:
        The context body, and the index just past the closing `)`.

    Raises:
        ValueError: If the context is never closed.
    """
    depth = 1
    i = start
    quote: str | None = None
    at_word_start = True
    while i < len(text):
        ch = text[i]
        if ch == "\\" and quote != "'":
            i += 2  # escaped character is inert; single quotes take no escapes
            at_word_start = False
            continue
        if quote is not None:
            if ch == quote:
                quote = None
            i += 1
            at_word_start = False
            continue
        if ch == "#" and at_word_start:
            i = _skip_comment(text, i)
            continue
        if ch in "'\"":
            quote = ch
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return text[start:i], i + 1
        at_word_start = ch in " \t\n;&|("
        i += 1
    raise ParseAmbiguity(
        "unterminated command substitution", text=text, pos=max(start - 2, 0)
    )


def _unescape_backquote_body(body: str) -> str:
    """Remove the backslashes the shell removes when it processes a backquote body.

    Inside `` ` … ` `` the shell strips a backslash preceding `` ` ``, `\\` or `$`. Skipping this
    leaves `` \\` `` pairs intact in the extracted body, so the recursion never opens the nested
    context and the tokenizer glues the escaped backtick onto the next word (`` `git ``), which
    `is_git` does not match. Since depth >= 2 backtick nesting *requires* backslashes in bash, that
    is the only form nested backticks take — so without this, "backticks at any depth" is false.

    Safe by direction: unescaping only ever reveals more contexts, never fewer.

    Args:
        body: The raw text between an opening and closing backtick.

    Returns:
        The body with the shell's backquote escapes removed.
    """
    out: list[str] = []
    i = 0
    while i < len(body):
        if body[i] == "\\" and i + 1 < len(body) and body[i + 1] in "`\\$":
            out.append(body[i + 1])
            i += 2
            continue
        out.append(body[i])
        i += 1
    return "".join(out)


def _scan_to_backtick(text: str, start: int) -> tuple[str, int]:
    """Scan from just after an opening backtick to its closing backtick.

    A backslash escapes the next character, so an escaped `` \\` `` does not close the context; the
    body is then unescaped. BOTH halves are required, and omitting the second is the subtler bug —
    the context boundary comes out right while its contents stay inert, which looks correct in
    every structural test while the bypass stays open.

    Args:
        text: The full command string.
        start: Index of the first character INSIDE the context.

    Returns:
        The unescaped context body, and the index just past the closing backtick.

    Raises:
        ValueError: If the context is never closed.
    """
    i = start
    while i < len(text):
        if text[i] == "\\":
            i += 2
            continue
        if text[i] == "`":
            return _unescape_backquote_body(text[start:i]), i + 1
        i += 1
    raise ParseAmbiguity(
        "unterminated backtick substitution", text=text, pos=max(start - 1, 0)
    )


def split_command_contexts(
    text: str, depth: int = 0
) -> tuple[str, list[CommandContext]]:
    """Split ONE level of nested command contexts out of a command string.

    Each `$( … )`, `` ` … ` ``, `<( … )` and `>( … )` is replaced in the returned outer string by
    an indexed placeholder and returned separately as a `CommandContext`. Replacing bodies is what
    lets the outer string tokenize: a body's quotes are scanned in their own context, so a
    quote-heavy substitution can no longer drift the enclosing quote state.

    Quote state decides which constructs are active, mirroring the shell: nothing expands inside
    single quotes; `$( )` and backticks expand inside double quotes; process substitution does not.

    Backslash handling is asymmetric, deliberately, and the asymmetry is about CONSEQUENCE. Inside
    a body scanner the escape is honored, because ignoring it ends a context early and hides the
    command inside it — a silent bypass. At this level the escape is consumed with its following
    character, so `\\$(` yields no context; that diverges from bash (which rejects unquoted
    `\\$(…)` outright) but costs at most a missed inert span, never a hidden command.

    Args:
        text: The command string to split.
        depth: Nesting depth of `text` itself; extracted contexts get `depth + 1`.

    Returns:
        The outer string with each nested context replaced by a placeholder, and the extracted
        contexts in source order.

    Raises:
        ValueError: On an unterminated context, an unbalanced quote, or input that already
            contains the reserved context marker.
    """
    # The marker must be UNFORGEABLE. If the input already contains it, an index parsed out of the
    # caller's own text would select a real context — a forged marker can consume the true context
    # at an earlier cwd, so the real invocation is then attributed elsewhere and allowed. An
    # out-of-range index would raise IndexError, outside this module's documented ValueError
    # taxonomy, and escape into a consumer that swallows only ValueError. Refusing is ambiguity,
    # which the push gates already fail closed on.
    if PLACEHOLDER_PREFIX in text:
        raise ValueError(
            "command contains the reserved context marker; refusing to parse"
        )
    out: list[str] = []
    contexts: list[CommandContext] = []
    i, n = 0, len(text)
    quote: str | None = None
    # Initialised even though the raise below is only reachable with `quote` set, which
    # today always assigns this too. That coupling is an invariant nobody enforces: a second
    # `quote = ch` site added later would make this an UnboundLocalError INSIDE a
    # fail-closed tokenizer, and a crash here exits 1, which the hook contract treats as
    # noise rather than a veto — i.e. the guarded command runs. -1 degrades to no position.
    quote_at = -1
    while i < n:
        ch = text[i]
        if quote == "'":
            if ch == "'":
                quote = None
            out.append(ch)
            i += 1
            continue
        # A backslash escapes the next character for QUOTE-STATE purposes, so `\"` does not open or
        # close a double-quoted span. Both characters are copied through, and because the pair is
        # consumed here the construct tests below do NOT see an escaped opener: `\$(` yields no
        # context. Measured — do not describe this as "still detected after an escape."
        if ch == "\\" and i + 1 < n:
            out.append(ch)
            out.append(text[i + 1])
            i += 2
            continue
        expandable = quote is None or quote == '"'
        if expandable and ch == "$" and i + 1 < n and text[i + 1] == "(":
            body, i = _scan_to_unbalanced_paren(text, i + 2)
            contexts.append(CommandContext(body, depth + 1))
            out.append(_placeholder(len(contexts) - 1))
            continue
        if expandable and ch == "`":
            body, i = _scan_to_backtick(text, i + 1)
            contexts.append(CommandContext(body, depth + 1))
            out.append(_placeholder(len(contexts) - 1))
            continue
        if quote is None and ch in "<>" and i + 1 < n and text[i + 1] == "(":
            body, i = _scan_to_unbalanced_paren(text, i + 2)
            contexts.append(CommandContext(body, depth + 1))
            out.append(_placeholder(len(contexts) - 1))
            continue
        if quote is None and ch in "'\"":
            quote = ch
            quote_at = i
        elif quote == '"' and ch == '"':
            quote = None
        out.append(ch)
        i += 1
    if quote is not None:
        raise ParseAmbiguity("unbalanced quote", text=text, pos=quote_at)
    return "".join(out), contexts


def tokenize(command: str) -> list[str]:
    """shlex with punctuation_chars: control/redirect operators become their own tokens even when
    fused to a word (`-A&&git`), while quoted values stay intact.

    Args:
        command: The raw shell-command string to tokenize.

    Returns:
        The command's tokens in order, with control/redirect operators split into their own tokens.

    Raises:
        ValueError: On unbalanced quotes (the caller fails open).
    """
    lex = shlex.shlex(command, posix=True, punctuation_chars="();<>|&")
    lex.whitespace_split = True
    # BOTH comment layers are required, and they are complementary rather than redundant:
    #   `strip_comments` removes real comments, so nothing here has to.
    #   `commenters = ""` makes any `#` that SURVIVES that pass a literal word, not a comment.
    #
    # Neither alone is safe, and they fail in OPPOSITE directions:
    #   - `commenters = ""` alone FALSE-BLOCKS. Comment CONTENT becomes live tokens; measured, that
    #     turned `ALLOW_PUSH=1 git push origin main  # publish the bricks` -- this repo's own
    #     publish command -- into a refused push, and made an apostrophe in a comment an unbalanced
    #     quote. It regressed 3 of 6 ordinary commands while every gate suite still passed.
    #   - `strip_comments` alone leaves a BYPASS. Its quote tracking is linear over pre-split text,
    #     the very thing Defect A shows drifts, so on an odd-inner-quote body it fails to strip and
    #     the surviving `#` reaches shlex, which (newlines already `;`) eats the rest of the
    #     COMMAND. Measured: the push vanished entirely.
    #
    # ORDERING: this line may only exist once EVERY consumer routes through `strip_comments`, and it
    # was deliberately held back until that was true. Composed, ordinary input reaches shlex with no
    # `#` at all (no false block), while anything that survives drifted and becomes inert
    # (over-block, loud) -- the divergence principle's direction.
    lex.commenters = ""
    return list(lex)


def normalize_command(command: str) -> str:
    """Fold line continuations, then treat remaining newlines as command separators.

    **Order is load-bearing.** A backslash-newline is a *continuation* — the shell removes both and
    joins the lines — so it must be folded BEFORE newlines are rewritten to ``;``. Doing it the
    other way round leaves the backslash escaping the injected separator, so
    ``git \\<newline> push origin dev`` tokenizes with a single SPACE as its subcommand and the
    push becomes invisible to every consumer. That was a live fail-open in both push gates, and
    `\\` + newline is simply how a long git command is written.

    The fold itself is `fold_continuations`, called rather than re-derived: this function once
    carried its own copy of the old unconditional fold, and the copy stated that folding "can never
    hide" a command. It can — see `fold_continuations` for the two measured shapes.

    Args:
        command: The raw shell-command string.

    Returns:
        The command with continuations folded and newlines rewritten as ``;`` separators.
    """
    command = fold_continuations(command)
    return command.replace("\n", " ; ").replace("\r", " ")


def is_op(token: str) -> bool:
    """A control operator / command boundary: `&&`, `||`, `;`, `|`, `&`, `(`, `)` — not a redirect."""
    return (
        bool(token)
        and all(c in "();|&" for c in token)
        and not any(c in "<>" for c in token)
    )


def is_redirect(token: str) -> bool:
    """A redirection operator token: `>`, `>>`, `<`, `>&`, `&>`, …"""
    return (
        bool(token)
        and all(c in "<>&|" for c in token)
        and any(c in "<>" for c in token)
    )


def strip_redirects(seg: list[str]) -> list[str]:
    """Drop redirection operators, their targets, and a preceding bare fd number (`2 >& 1`), so a
    redirect is never misread as a commit pathspec — a phantom pathspec would silently narrow the
    gate's scope to nothing (fail open)."""
    out: list[str] = []
    i = 0
    while i < len(seg):
        t = seg[i]
        if is_redirect(t):
            if out and out[-1].isdigit():
                out.pop()  # the fd number in e.g. `2 >& 1`
            i += 2  # skip the operator and its target
            continue
        out.append(t)
        i += 1
    return out


# Parameter expansions a word can carry. Non-rescanning by construction: `[^{}]*` stops at the next
# brace of EITHER kind, so a run of `${` costs one step each. The first draft used `[^}]*`, which
# rescans to end-of-token from every `$` — measured 5.9 s on 60k of them.
_PARAM_EXPANSION_RE = re.compile(
    r"\$(?:\{[^{}]*\}|[A-Za-z_][A-Za-z0-9_]*|[0-9@*#?$!-])"
)
# `${NAME-word}`, `${NAME:-word}`, `${NAME=word}`, `${NAME:=word}`, `${NAME+word}`, `${NAME:+word}`:
# the expansion's VALUE may be the literal `word`, so it is reduced to that word before judging.
_WORD_EXPANSION_RE = re.compile(r"\$\{[A-Za-z_][A-Za-z0-9_]*:?[-=+]([^{}]*)\}")
# Brace expansion (`{git,}`) is deliberately NOT recognised. A clause for it was built and then
# DELETED, after it produced both BLOCKERs of this change's final review, each a guard pushed past
# its hook timeout — which lets the command run: its regex first backtracked quadratically on an
# unclosed `{` (14.3 s for one 32k-comma word), and once that was fixed, recursing into each
# alternative with the whole prefix and suffix was quadratic again (67.8 s for one 64k word). The
# shape it closed occurred 0 times in 120,781 real commands. A residual, stated below.


def _literal_git(word: str) -> bool:
    return word == "git" or word.endswith("/git")


def _is_opaque(token: str) -> bool:
    """True when bash would EXPAND part of this token: a substitution placeholder or a `$`."""
    return PLACEHOLDER_PREFIX in token or "$" in token


def is_git(token: str) -> bool:
    """True if this word, in command position, may run git.

    Literally `git` or a path ending in `/git`, as before — and, since 2026-09-18, a word bash
    REDUCES to one of those at run time. Every shape below ran git under bash 3.2.57 and 5.3.15, and
    every guard consuming this module allowed all of them while this accepted only the literal text.

    - never an env ASSIGNMENT: an assignment is never the command word, and command position already
      steps over it. Matching `REPO=$BASE/foo.git` made push-guard's own scan take the real `git`
      after it as the subcommand and bury the push.
    - an OPAQUE word (a substitution placeholder or a `$`) is git when, with placeholders removed,
      it ENDS in `git` — this is the only clause that sees the quote-merged forms, since
      `shlex(posix=True)` turns `"$X"git`, `$X""git` and `$'git'` into `$Xgit` / `$git` — or when,
      with default-value expansions reduced to their word and every other expansion removed, what
      remains is literally git (`git$X`, `git${X}`, `${X:-git}`).

    Residual, stated rather than closed — the rule is that a word is caught only when its LITERAL
    letters end in `git`, or reduce to git once expansions are removed, so any word whose
    expansion SUPPLIES letters of `git` passes: wholly dynamic (`$G`, `$(echo git)`) and partly
    dynamic alike (`gi$X`, `${X}it`, `$(echo g)it`, `` `echo g`it ``, measured allowed at all four
    guards). Closing it was measured to false-block 1.5%-10% of real commands. Also residual:
    escape-encoded ANSI-C (`$'\\x67it'`); BRACE expansion (`{git,}`, `{nope,git}` — see the
    comment above `_literal_git` for why a clause for it was deleted); globs; and WHITESPACE
    inside `${…}` (`git${X:+ }`, `${X:- }git`), which bash
    keeps as one word but `shlex` splits before any predicate runs — a tokenizer-level defect,
    filed separately. Over-reads, harmless and rare: an opaque word that merely ends in `git`
    (`$HOME/bin/legit`), and `'$X'git` (single-quoted, so bash runs a command literally named
    `$Xgit`).
    """
    if ENV_ASSIGN.match(token):
        return False
    if _literal_git(token):
        return True
    if not _is_opaque(token):
        return False
    literal = _PLACEHOLDER_RE.sub("", token)
    if literal.endswith("git"):
        return True
    literal = _WORD_EXPANSION_RE.sub(r"\1", literal)
    return _literal_git(_PARAM_EXPANSION_RE.sub("", literal))


def subcommand_is_indeterminate(sub: str) -> bool:
    """True when a recorded SUBCOMMAND cannot be read as text: a git-like word (an alias named `git`
    makes `git git …` run anything) or an opaque one (`git $V origin dev` with V=<push>). A consumer
    that compares subcommands literally must treat this as possibly the operation it guards."""
    return is_git(sub) or _is_opaque(sub)


def _steps_as_wrapper(
    tokens: list[str], j: int, extra_wrappers: frozenset[str] = frozenset()
) -> bool:
    """True if `tokens[j]` is part of an exec-wrapper a backward walk steps over.

    The ONE rule every backward walk consults — `starts_command`, `_env_prefix` and
    `_cd_command_position` — so they cannot drift apart. A `WRAPPERS` member, or an
    `extra_wrappers` member at the sites that pass them, qualifies; so does a `--` whose OWNER, the
    token before it, qualifies the same way — the wrapper's end-of-options marker. Measured under
    bash 3.2.57 and 5.3.15: `eval -- git …`, `builtin -- eval git …`, `command -- git …`,
    `exec -- git …` and `env -- git …` all run git; `eval`/`builtin` take no other option.

    One `--` per wrapper: `eval -- -- git …` runs a command named `--` (measured), so a `--` owned
    by another `--` is not stepped. For git detection a stepped `--` can only ever ADD an
    invocation — `time -- git …` runs `--` under bash 3.2 and git under 5.3, and `timeout -- git …`
    fails before running anything — so the over-read is the safe direction.
    """
    tok = tokens[j]
    if tok in WRAPPERS or tok in extra_wrappers:
        return True
    if tok == "--" and j > 0:
        owner = tokens[j - 1]
        return owner in WRAPPERS or owner in extra_wrappers
    return False


def starts_command(
    tokens: list[str],
    idx: int,
    reserved_words: frozenset[str] = frozenset(),
    extra_wrappers: frozenset[str] = frozenset(),
) -> bool:
    """True if tokens[idx] is in *command position* — reachable from the input start or a control
    operator by stepping back over only leading `VAR=val` env assignments and known exec-wrappers
    (`sudo`/`time`/`env`/…). A bare word before it (e.g. `echo`) means it is that command's
    argument, so `echo VAR=1 git commit` is NOT mistaken for a commit, while `sudo git commit` and
    `ALLOW_GIT_WRITE=1 git commit` are. (Redirects are stripped globally before this runs, so a
    leading `2>&1 git commit` also resolves to command position.)

    `reserved_words` and `extra_wrappers` are both empty by default, so an unqualified call is
    byte-for-byte the pre-existing behavior. `_git_starts_command` is the only caller IN THIS
    MODULE that passes them — `push-guard.py` and `git-timing-guard.py` pass
    `reserved_words=gitcmd.RESERVED_WORDS` at their own detection sites; see `RESERVED_WORDS` for
    the two different purposes that set now serves. The
    `cd`/`pushd`/`popd` sites this module tracks no longer call this function at all — they call
    `_cd_command_position`, which classifies the fail-closed cwd behavior directly rather than
    reusing this command-position predicate.

    Args:
        tokens: The token stream.
        idx: Index of the token being tested.
        reserved_words: Words that, like a control operator, themselves mark tokens[idx] as
            starting a command — checked before wrapper-stepping, so a reserved word ends the walk
            immediately rather than being stepped over.
        extra_wrappers: Additional exec-wrappers to step over, on top of the shared `WRAPPERS` set.
    """
    j = idx - 1
    while j >= 0:
        prev = tokens[j]
        if is_op(prev) or prev in reserved_words:
            return True
        if ENV_ASSIGN.match(prev) or _steps_as_wrapper(tokens, j, extra_wrappers):
            j -= 1
            continue
        return False
    return True


def _env_prefix(tokens: list[str], idx: int) -> list[str]:
    """Collect the env-assignment tokens immediately preceding `tokens[idx]`, in source order.

    Mirrors `starts_command`'s backward walk and must stay in step with it: the same tokens it
    steps OVER to prove command position are the ones that carry env into this invocation, so a
    consumer reading these sees exactly what the shell would apply. Wrappers are stepped over
    without being collected — `env FOO=1 git …` puts the assignment after the wrapper, and
    `sudo git …` carries no assignment at all.

    This walk consults `_steps_as_wrapper` — the same rule `starts_command` consults — rather than
    restating it. This walk previously hand-copied `GIT_ONLY_WRAPPERS`'s single member as a literal
    `prev == "exec"`, directly beneath the sentence above promising the two walks stay in step —
    undetectable while that set has one element, and fail-open the moment it gains a second:
    `_git_starts_command` would still put `git` in command position after the new wrapper while
    this walk stopped short of the assignments in front of it, so a `GIT_CONFIG_*` prefix would
    vanish from every check scoped to these tokens.
    """
    out: list[str] = []
    j = idx - 1
    while j >= 0:
        prev = tokens[j]
        if ENV_ASSIGN.match(prev):
            out.append(prev)
        elif not _steps_as_wrapper(tokens, j, GIT_ONLY_WRAPPERS):
            break
        j -= 1
    out.reverse()
    return out


def _git_starts_command(tokens: list[str], idx: int) -> bool:
    """`starts_command`, scoped for recognising a `git` invocation: reserved words (`if`, `!`, …)
    and `exec` both count as command-position boundaries here. Every `is_git(...)`-guarded call
    site in this module must call this, not bare `starts_command` — see `RESERVED_WORDS` for what
    this reading of that set is for, and how `_cd_command_position`'s differs. The `cd`/`pushd`/
    `popd` sites do not call this function at all; they call `_cd_command_position` instead, which
    classifies the fail-closed cwd behavior directly — see `GIT_ONLY_WRAPPERS` for why the leak
    this scoping once risked (a `cd` right after `exec` being tracked rather than made
    unresolvable) can no longer occur."""
    return starts_command(
        tokens, idx, reserved_words=RESERVED_WORDS, extra_wrappers=GIT_ONLY_WRAPPERS
    )


def _cd_command_position(tokens: list[str], i: int) -> bool | None:
    """Classify the `cd`/`pushd`/`popd` at `tokens[i]` for the cwd walk.

    True when it runs directly in THIS shell — reached from a command boundary over env assignments
    only (`FOO=1 cd X` moves the shell) — so its target is tracked exactly. None when it is reached
    through ANY wrapper or a wrapper's `--`, AND None when the token in front of it is a member of
    `RESERVED_WORDS` (`if`, `!`, `{`, `while`, `then`, `coproc`, …); either way the cwd becomes
    UNRESOLVABLE. False when it is an argument (`echo cd X`) and not a directory change at all. A
    None on the reserved-word path is therefore the designed answer, not a bug — the paragraph
    below says why it is neither True nor False.

    None is the fail-CLOSED answer, and it REPLACED a model. Whether the shell moves depends on the
    whole chain: `eval cd` and `builtin cd` move it; `nohup cd` and `env eval cd` (env cannot run
    eval) do not; `time cd` moves only where `time` is still the keyword, so `builtin time cd` runs
    /usr/bin/time in a child. Two diverse-model review rounds each found a fail-open in a set that
    tried to enumerate the movers — both in that one parameter, none in the rule. An unresolvable
    cwd blocks every guarded operation and no read (measured), and 0 of 17,929 real commands
    carrying a cd reached it through a wrapper: the precision protected nothing and cost two holes.

    `exec` is not stepped: `exec cd` never continues — the non-interactive shell exits — so nothing
    after it runs and no reading of it can leak.

    A reserved word before the `cd` yields None as a DELIBERATE, UNIFORM OVER-BLOCK. Do not read
    it as a claim that each member is individually unknowable: the set does NOT divide that way,
    and saying it does invites a maintainer to falsify the sentence in a minute and then "repair"
    it by deciding per word. Measured, with `cd /usr; <shape>; pwd`: after `if cd /bin; then :;
    fi`, `while cd /bin; do break; done`, `until cd /bin; do break; done` and `! cd /bin` the
    shell really is at /bin — for those four the `cd` provably DOES run here, and True would be
    the accurate per-word answer. The others go the other way, also measured: `coproc cd /bin`
    forks a subshell and the shell stays at /usr; `then`/`do`/`else`/`elif` run the `cd` only on a
    branch actually taken (`if false; then cd /bin; fi` runs none at all); a `{` opening a
    function BODY runs it only when the function is CALLED, never at definition time (`f() { cd
    /bin; }`). What IS uniform across all ten is that False — "an argument, never a directory
    change", the `echo cd X` answer — is wrong, since the word puts the `cd` in command position.
    So the real choice is between deciding per word and answering one way for every member; the
    paragraph below is why this module does not decide per word, and None is the uniform answer
    because it is the one that cannot be got wrong, not because the shell's behaviour is a
    mystery.

    Enumerating which words move this shell is the SAME parameter deleted in the paragraph above
    for wrappers, and it is deliberately not re-introduced here: EVERY member of `RESERVED_WORDS`
    yields None. Nor may the branch be narrowed to the words where the `cd` might not run at all —
    that re-opens what this closed: `if true; then cd ADOPTED; git <push> origin dev; fi` from a
    plain cwd measured rc 0 before and rc 2 after. The cost is measured and small — 2 of 17,326
    real cd-bearing commands are reached this way, and 0 of 27,998 lose a guarded op AT THE
    PUBLICATION GUARD. That figure is per-consumer, not a whole-system claim: the TIMING guard maps
    an unresolvable cwd onto the PAYLOAD cwd instead of blocking, so ITS blocked set does shrink
    for this class — measured, and pinned by the "reserved-word cd trade" rows in
    scripts/tests/test_git_timing_guard.sh; the full per-consumer table is in
    `specs/2026-09-11-reserved-word-cd.md`.

    Consumers do not all read a None the same way, and this function does not decide that for them.
    `publication-push-guard.py` (the security boundary keeping a `dev` branch private) treats it as
    fail-CLOSED, per the paragraph above: it blocks every guarded operation and no read.
    git-timing-guard.py instead falls back to the PAYLOAD cwd on None rather than blocking — a
    DECIDED trade (Ruling R6, design records 2026-09-11-eval-wrapper-bypass and
    2026-09-11-reserved-word-cd), pinned in test_git_timing_guard.sh, not a second fail-closed
    reading of the same value: a `cd` reached through a wrapper OR after a reserved word leaves
    that guard scoped to the payload cwd, exactly as it was before this module's `cd`-tracking
    existed. Both routes reach it, and they are NOT comparably rare — the wrapper route was
    measured at 0 of 17,929 real commands, while the reserved-word route is constructible (`if cd
    sub; then …`) and was measured to move that guard's verdict.
    """
    j = i - 1
    through_wrapper = False
    while j >= 0:
        prev = tokens[j]
        if is_op(prev):
            break
        if ENV_ASSIGN.match(prev):
            j -= 1
            continue
        if _steps_as_wrapper(tokens, j):
            through_wrapper = True
            j -= 1
            continue
        if prev in RESERVED_WORDS:
            return None
        return False
    return None if through_wrapper else True


MAX_TRACKED_CWD = 4096
"""Longest tracked working directory `_resolve_cd` will carry before answering *unresolvable*.

Sized from the operating systems rather than from taste: `PATH_MAX` is 1024 on Darwin and 4096 on
Linux, so no directory a command could actually enter is longer than this. It exists to bound
COST, not to judge paths — see `_resolve_cd`.
"""


def _resolve_cd(cwd_state: str | None, target: str | None) -> str | None:
    """Apply one `cd`/`pushd` target to the tracked working directory.

    Args:
        cwd_state: Directory in force before the `cd`, or None if already unresolvable.
        target: The `cd` target token, or None when the command names no target.

    Returns:
        The new directory, or None meaning *unresolvable* — the conservative answer whenever the
        target cannot be resolved statically (`cd -`, `cd "$VAR"`, `cd ~`, `cd "$(…)"`), or when
        the tracked path has grown past `MAX_TRACKED_CWD` (see below).
    """
    if cwd_state is None:
        return None
    if target is None or target == "-" or "$" in target or "~" in target:
        return None
    # A cd whose target is a command substitution is no more statically resolvable than `cd "$VAR"`,
    # and joining the placeholder as a path segment would invent a directory that is NOT the adopted
    # repo — turning a push that should block into one that is allowed. Same rule, same reason.
    if PLACEHOLDER_PREFIX in target:
        return None
    if os.path.isabs(target):
        joined = os.path.normpath(target)
    else:
        joined = os.path.normpath(os.path.join(cwd_state, target))
    # Past the bound the answer is *unresolvable*, the same conservative value every other branch
    # here returns — a push then blocks rather than being judged against an invented directory.
    # This is a COST bound, not a correctness one: each relative hop appends to the path and each
    # normpath rescans it, so N hops cost O(N**2) in ONE walk, and this module walks a context once
    # per reading variant up to `MAX_TOTAL_PARSES`. Measured 2026-09-22 without it: a 65,533-byte
    # command holding 7 ambiguous heredocs (k=7, so exactly 128 assignments, all of which parse),
    # 13,083 `cd x;` hops and ONE push spent 83.6 s in the walk — against 0.63 s for the same input
    # on the pre-change tokenizer — and drove `publication-push-guard.py` to 102.8 s against the
    # 60 s timeout `settings.json` registers for it. A PreToolUse hook killed at its timeout is
    # SILENT and the command RUNS, so the guard reached `refusing to push` and died before saying
    # it. No real directory comes close: `PATH_MAX` is 1024 on Darwin and 4096 on Linux, so a path
    # past this bound cannot name one — which is why discarding it loses no capability.
    if len(joined) > MAX_TRACKED_CWD:
        return None
    return joined


def _mask_with_count(
    text: str, depth: int, readings: Mapping[int, bool] | None = None
) -> tuple[str, int]:
    """Mask under one reading assignment and report how many ambiguous heredocs were seen.

    Split from `_prepare` because the count must be knowable BEFORE anything that can raise: the
    enumeration needs `k` to build its assignments, and the split half (`split_command_contexts`)
    raises on exactly the inputs the enumeration exists to survive. Masking is total.
    """
    state = _ReadingState(readings)
    return mask_heredoc_quotes(text, depth, state), state.count


def _reading_assignments(count: int) -> Iterator[dict[int, bool]]:
    """Every assignment to try, primary first, all-join second, in a FIXED order.

    A pure function of the count: `k` is fixed for a text whatever the assignment (the mask scans by
    input index and the reading changes only what is emitted), so no feedback from a parse is
    needed — which is what lets the caller take `k` from the total mask and never from a parse that
    may raise.

    LAZY, and that is a security property rather than a style choice. `k` is bounded by input
    LENGTH, not by `MAX_CONTEXT_DEPTH` — about 2053 at `MAX_COMMAND_LENGTH` — so materialising the
    assignments puts the whole `2**k` in front of the parse cap, where no cap value can reach it.
    The first draft did exactly that, and its `assignment not in ordered` scan over a growing list
    made it `4**k` besides: measured, a 444-BYTE command with k=16 spent 38 s inside this function
    having parsed nothing, against a 60 s hook registration that two heavy tokenizer calls must
    share. A PreToolUse hook killed at its timeout is SILENT and the command RUNS, so that was a
    denial-of-guard bypass reachable from a tiny input, not a slow path.

    Yielding lazily lets `_walk_context` stop at the cap: the caller consumes at most
    `MAX_TOTAL_PARSES` of these however large `2**k` is. `itertools.product` is itself lazy, and
    building one assignment dict costs O(count), paid only for assignments actually walked.
    """
    if count <= 0:
        yield {}
        return
    yield {}  # the primary: all-drop, what bash does at the top level
    yield {i: True for i in range(count)}  # all-join, the other extreme
    for bits in itertools.product((False, True), repeat=count):
        # The two extremes are already yielded; skipping them by SHAPE costs O(1) per candidate,
        # where testing membership of what was yielded costs O(2**k) and was the `4**k` above.
        if not any(bits) or all(bits):
            continue
        yield {i: True for i, bit in enumerate(bits) if bit}


MAX_TOTAL_PARSES = 128
"""One cap on the TOTAL parses a single walk may spend across every context and every variant.

CALIBRATED (2026-09-21) against a term that does NOT bind, and corrected 2026-09-22. Read the
table for the column it can answer, never for the one a headroom figure made it look like it
answered. Wall clock is the REAL hook process on the ADVERSARIAL BACKTICK LADDER at depth
`MAX_CONTEXT_DEPTH`, one ambiguous heredoc per level, padded to 64,718 chars, driven through
`publication-push-guard.py` — which makes TWO heavy tokenizer calls per run,
`iter_context_token_streams` and `iter_git_invocations_detailed`.

| cap | that guard's wall clock, ON THE LADDER | real commands truncated, of 88,662 |
| :-- | :--- | :--- |
| 64  |  8.83 s |  61 (0.069%) |
| 128 | 17.89 s |   6 (0.0068%) |
| 256 | 36.65 s |   2 (0.0023%) |

**A "headroom vs the 60 s registration" column used to sit in the middle of that table. It was
deleted because it described the FIXTURE while reading as a property of this constant.** The
ladder is not the worst shape, and the registration is not this cap's to satisfy. Measured
2026-09-22 on the shape nobody thought to build: a FLAT bundle of ambiguous quoted heredocs in ONE
context, no nesting, padded to 65,529 chars, through the same guard — **224.89 s at this very
cap**, against the ladder's 17.89 s at the same length. Both tokenizer calls together are 24.51 s
of that; the rest is the guard's own per-invocation work. Shipped `dev`, whose tokenizer answers
the identical input in **0.15 s**, takes **172.40 s** on it. So that guard overruns its 60 s
registration on `dev` exactly as on this branch, at every value of this constant, and no value has
ever governed it: the binding term was four `subprocess.run` calls per invocation against one
root, uncached — **8,212 on BOTH sides** of that fixture, because the extra invocations this
branch finds are `status`, which the guard's known-safe `continue` skips before judging. As of
2026-09-23 that cost is closed — `_git`'s per-judgment memo and its `MAX_GIT_SPAWNS` budget
replaced the uncached per-invocation spawns (each identical query answered once per judgment), and
the tag sweep that used to cost two spawns per tag is now the two-query `_tags_block` — and it was
never a reason to move this number, still is not.

What this cap DOES govern is the right-hand column, and the tokenizer's own share: it holds the
walk to 128 parses however large `2**k` is, which is what keeps the flat fixture's two tokenizer
calls to 24.51 s at `MAX_COMMAND_LENGTH` instead of unbounded. 256 is rejected on cost for no
matching gain — the per-parse price is linear, so it roughly doubles that worst case to buy four
fewer truncations in 88,662. 64 is rejected on the right-hand column: those 61 are not
pathological input but the operator's own document-writing shape (a redirect into `plans/` with a
quoted heredoc delimiter), whose context count grows with the document, so that column only gets
worse with time.

Both differential corpora clear 128 by 9x and neither reaches it: max 14 parses over the original
generator's 9 seeds x 600 scripts, max 9 over the desync generator's 5 seeds x 150 x 2. The cost
is linear in the cap — 68–80 ms per parse at `MAX_COMMAND_LENGTH`, measured out to 510 parses —
against a pre-change baseline of 0.28–0.30 s for the whole call.

What this cap does NOT bound, stated because the table above reads as if it did: a parse is
charged per CONTEXT per variant, and the real corpus's heaviest command spends 451 parses in
62 ms — **0.138 ms each, 500x cheaper than the adversarial ladder's**. Parse COUNT therefore
prices the two apart by a factor this cap cannot see, which is exactly why no single value
satisfies both columns and why the value had to be chosen rather than derived. A budget in bytes
parsed, or in wall clock, is the instrument that would price them the same.
"""

# Contains `$`, so `_is_opaque` is True for it and therefore `subcommand_is_indeterminate` is too.
AMBIGUOUS_READING_SUBCOMMAND = "$<ambiguous-heredoc-reading>"


def subcommand_is_ambiguous_reading(sub: str) -> bool:
    """True for the ONE subcommand meaning "a reading of this command was lost", not "a git word
    I cannot resolve".

    `subcommand_is_indeterminate` is True for this word and for every other opaque one alike, and
    that is right for the VERDICT and wrong for the MESSAGE. An opaque word (`git $V origin dev`)
    really may be a push, sitting in a segment an operator can lead with `ALLOW_PUSH=1`; this one
    corresponds to NO command text, so no env prefix can ever authorize it — see
    `_indeterminate_stream`, which emits it with an empty leading env run by construction. A guard
    that cannot tell the two apart prescribes the one remedy that cannot work, and the spec's
    Diagnosability residual names an unexplainable false block as what later gets "fixed" by
    narrowing a matcher — the repair this repo has twice measured as the fail-open it removed.

    A PREDICATE rather than three `sub == AMBIGUOUS_READING_SUBCOMMAND` comparisons in three
    guards, for the same reason `subcommand_is_indeterminate` is one: this module already
    distinguishes a variant that RAISED from a budget that TRUNCATED (see
    `iter_context_token_streams`), and should those ever carry separate tokens, one predicate
    changes while three hand-typed equality tests go stale in silence. What it deliberately does
    NOT own is the MESSAGE: each guard's override token, framing and precedence against a real
    push are its own policy, exactly as `describe_ambiguity` owns the location clause and leaves
    the tool path to its caller.

    Residual, stated rather than guarded against: an operator who types this sentinel verbatim
    gets the ambiguity wording for a word they wrote themselves. The command blocks either way —
    the word contains `$`, so `_is_opaque` and therefore `subcommand_is_indeterminate` are already
    True for it — so the cost is a mislabelled message, never a changed verdict.
    """
    return sub == AMBIGUOUS_READING_SUBCOMMAND


class _ParseBudget:
    """One cap on TOTAL parses across a whole walk, charged by every variant in every context.

    Budgeting per-context VARIANTS instead was measured at 75,421 parses / 9.14 s on a 65,536-char
    input, because nested contexts are re-walked once per parent variant: work is variants x
    contexts and a per-context cap counts one factor. A hook timeout lets the command RUN, so an
    unbounded walk is a bypass, not a slowdown.
    """

    __slots__ = ("remaining",)

    def __init__(self, limit: int) -> None:
        self.remaining = limit

    def spend(self) -> bool:
        if self.remaining <= 0:
            return False
        self.remaining -= 1
        return True


_MAX_REPORTED_READINGS = 4
"""How many failed reading assignments one context reports. `k` ambiguous heredocs give up to
`2**k` assignments and every one of them can raise, so an uncapped list is unbounded diagnostic
text on exactly the adversarial input that produced it. Four is enough to show the pattern; the
`untried` count carries the rest of the story."""


class ReadingStep(NamedTuple):
    """Which reading ONE context's ambiguous heredocs were given for the record this hangs off.

    Attributes:
        depth: That context's nesting depth — 0 is the command the caller handed in.
        count: How many ambiguous heredoc continuations the mask pass met in that context. 0 means
            the context had no reading choice to make, so nothing here varies.
        joined: The 0-based encounter ordinals read as JOIN. `()` is the PRIMARY (all-drop)
            reading — what bash does at the top level and the only one that occurs in real
            commands. None means the record is not attributable to any one assignment: the
            indeterminate marker stands for a reading that was never read at all.
    """

    depth: int
    count: int
    joined: tuple[int, ...] | None


class ReadingTrail(NamedTuple):
    """The reading assignment behind ONE invocation, carried BY LIST POSITION.

    Deliberately not a field on `Invocation` (~40 call sites unpack that record positionally) and
    deliberately not a map keyed by `_invocation_key`. The union is a MAX-MULTISET, so identical
    keys legitimately repeat — a command that genuinely runs `git status` twice contributes two
    records with the same key — and a key-based map would collide exactly where the union did its
    work. A list indexed the same way as the invocation list cannot collide by construction.

    Why this exists at all: the redesign accepts an over-read, and an over-read is only acceptable
    while the operator can see WHY a command blocked. An unexplainable false block is what gets
    "fixed" by narrowing a matcher — the repair this repo has twice measured as the fail-open it
    was trying to remove (see the spec's Diagnosability residual).

    Attributes:
        steps: One `ReadingStep` per context on the path from the top-level command down to the
            context that produced this invocation, OUTERMOST FIRST. A nested invocation carries
            its ancestors' assignments too, because the parent's reading decides the child's text:
            naming only the innermost would report "primary" for a record that exists solely
            because an ancestor was read as JOIN.
        lost: "" for a real invocation. On the indeterminate marker: "unreadable" when a variant
            raised, "truncated" when the parse cap stopped the enumeration, "unreadable+truncated"
            when both happened in that context.
        unreadable: `(joined, category)` for each variant that raised, in the order tried, capped
            at `_MAX_REPORTED_READINGS`.
        untried: How many assignments the parse cap never tried in that context.
    """

    steps: tuple[ReadingStep, ...]
    lost: str = ""
    unreadable: tuple[tuple[tuple[int, ...], str], ...] = ()
    untried: int = 0

    @property
    def primary(self) -> bool:
        """True when every context on the path took the all-drop reading and nothing was lost."""
        return not self.lost and all(step.joined == () for step in self.steps)


def _reading_category(exc: BaseException) -> str:
    """The ambiguity CATEGORY of a failed variant, for the trail. Never raises: a trail is a
    diagnostic, and a diagnostic that can raise inside the walk would turn a block into a crash."""
    try:
        category = getattr(exc, "category", None)
        return category if isinstance(category, str) else str(exc)
    except Exception:  # noqa: BLE001 - a broken diagnostic must never change a verdict
        return "unknown"


def _indeterminate_invocation() -> Invocation:
    """An in-band marker that some reading of this context could not be read or was not tried.

    `effective_dir` is None DELIBERATELY: `subcommand_is_indeterminate` is True for this subcommand,
    so push-guard and the timing guard treat it as push-shaped, the publication guard routes it out
    of `KNOWN_SAFE_SUBCOMMANDS` into a judgement whose root is unresolvable, and `plan-rehearse.py`
    refuses it as not statically resolvable. Passing a real directory instead was measured to make
    plan-rehearse VOUCH for a command whose reading it could not determine.
    """
    return Invocation(
        None, None, AMBIGUOUS_READING_SUBCOMMAND, [], InvocationTokens([], [])
    )


def _indeterminate_stream() -> list[str]:
    """The STREAM form of `_indeterminate_invocation`, for `iter_context_token_streams`.

    The two primitives must carry the SAME in-band signal or they disagree about what a context
    contains, and the disagreement is a fail-open rather than a mismatch. Measured on the witness
    `bash <<'(x'` / `git status` / `x=$\\` / `(x`: the walk recorded
    `[('status', []), (AMBIGUOUS_READING_SUBCOMMAND, [])]` while the streams primitive silently
    `continue`d past the variant that raised and returned ONE marker-free stream, so the
    publication guard (invocation-shaped) blocked at rc=2 while push-guard and the timing guard
    (both stream-shaped) returned rc=0 — turning `dev`'s rc=2 push-guard BLOCK into an ALLOW.

    Two tokens, no trailing separator. Both stream consumers close the final segment at `i == n`
    (`push-guard.py:_block_kind`, `git-timing-guard.py:_find_first_push` each loop
    `for i in range(n + 1)`), so a two-token list IS one complete segment; a trailing `;` would be
    inert, and relying on one would make the signal depend on a separator convention neither
    consumer requires. `git` leads so `is_git`/`starts_command` put the marker in SUBCOMMAND
    position, which is where `subcommand_is_indeterminate` is consulted (`push-guard.py:208`,
    `git-timing-guard.py:227`). Nothing precedes `git`, so the segment's leading env-assignment run
    is empty and no `ALLOW_PUSH=1`/`ALLOW_GIT_WRITE=1` can authorize it.
    """
    return ["git", AMBIGUOUS_READING_SUBCOMMAND]


def _invocation_key(inv: Invocation) -> tuple:
    return (
        inv.effective_dir,
        inv.cdir,
        inv.subcommand,
        tuple(inv.arg_tokens),
        tuple(inv.tokens.env),
        tuple(inv.tokens.opts),
    )


def _union_max_multiset(
    base: list[Invocation],
    base_trails: list[ReadingTrail],
    extra: list[Invocation],
    extra_trails: list[ReadingTrail],
) -> tuple[list[Invocation], list[ReadingTrail]]:
    """Multiplicity of each key is the MAX across variants; `base` keeps its order.

    A set union collapses a command that genuinely runs one invocation twice — measured as
    hidden_valid=92 on the differential's default seed, against 0 here.

    Each trail travels with its invocation BY POSITION, appended in the same step that appends the
    invocation. That is what keeps the answer to "which reading produced THIS record" attached to
    the record the max-multiset actually kept: a key-based map would merge the trail of the
    duplicate it dropped with the trail of the one it kept, which is precisely the case the
    multiset exists for.
    """
    counts = Counter(_invocation_key(inv) for inv in base)
    seen: Counter[tuple] = Counter()
    out = list(base)
    out_trails = list(base_trails)
    for inv, trail in zip(extra, extra_trails, strict=True):
        key = _invocation_key(inv)
        seen[key] += 1
        if seen[key] > counts[key]:
            out.append(inv)
            out_trails.append(trail)
    return out, out_trails


def _prepare(
    text: str, depth: int, readings: Mapping[int, bool] | None = None
) -> tuple[str, list[CommandContext], int]:
    r"""Apply the fixed preparation order and split out one level of nested contexts.

    ORDER IS LOAD-BEARING and `normalize_command` is deliberately SPLIT, because its two halves
    belong on opposite sides of the scan. Composing them in this order is exactly
    `normalize_command`, which is what keeps the shipped continuation fold intact rather than
    re-derived:

      1. strip comments   — FIRST, while the newlines that TERMINATE them still exist. A backslash
         does not continue a comment, so folding first would join the next line into the comment
         and eat both.
      2. fold continuations — BEFORE the scan. `x="$\<newline>(git push)"` is a real substitution
         to the shell; a scanner on raw text walks past the split `$` and `(`, and folding
         afterwards reassembles a `$(` that nothing ever scans.
      3. scan for contexts — here.
      4. newlines to `;`  — AFTER the scan, done by the caller, because the body scanners' own
         comment handling needs the newline that ends a comment.

    Args:
        text: The context's raw command text.
        depth: Nesting depth of `text` itself. MUST be threaded through — extracted contexts get
            `depth + 1`, and that is the only thing that makes `MAX_CONTEXT_DEPTH` enforceable.
            Letting it default here silently pins every child at depth 1, so the limit never trips
            and deeply nested input recurses until Python raises `RecursionError` — which is
            OUTSIDE this module's ValueError taxonomy and would escape a consumer that swallows
            only ValueError.

        readings: Which ambiguous heredocs to read as JOIN, by encounter ordinal. None (the
            default) is all-drop — the PRIMARY reading, which is what bash does at the top level
            and the only reading that occurs in real commands.

    Returns:
        The outer string (contexts replaced by placeholders, newlines still present), the extracted
        contexts, and how many ambiguous heredocs the mask pass met. The count comes back even when
        the caller is about to enumerate: it is what `_reading_assignments` needs, and taking it
        from `_mask_with_count` rather than from a parse is what keeps it available on the inputs
        that make the split half raise.

    Raises:
        ValueError: On unbalanced quotes, an unterminated context, or a reserved marker in input.
    """
    masked, count = _mask_with_count(text, depth, readings)
    outer, nested = split_command_contexts(
        fold_continuations(strip_comments(masked)), depth
    )
    return outer, nested, count


_EMPTY_TRAIL = ReadingTrail(())
"""The trail of an invocation found in the context currently being walked.

Empty rather than "primary": `_walk_once` is not told which reading produced the text it was
handed, so claiming one here would be a guess. `_walk_context` knows, and prepends its own step.
"""


class _Recorder(list):
    """The invocation list, plus one `ReadingTrail` per invocation, appended in ONE operation.

    A `list` subclass so every record site inside `_walk_once` keeps its existing shape
    (`results.append(Invocation(...))`) while no site can append an invocation WITHOUT a trail.
    Five separate record sites maintaining a parallel list by hand is an alignment nobody checks,
    and a trail one position out is a diagnostic naming the wrong reading -- worse than no
    diagnostic, because it reads as authoritative. `trails[i]` describes `self[i]`, structurally.

    `extend` is refused rather than inherited: it is the one list operation that would append
    invocations with no trail, and a child's records arrive with trails already filled in, so
    `extend_child` is the only correct spelling.
    """

    __slots__ = ("trails",)

    def __init__(self) -> None:
        super().__init__()
        self.trails: list[ReadingTrail] = []

    def append(self, inv: Invocation) -> None:
        super().append(inv)
        self.trails.append(_EMPTY_TRAIL)

    def extend(self, _invocations) -> None:  # type: ignore[override]
        # A ValueError, not a TypeError: this module's documented taxonomy is ValueError, and a
        # consumer that swallows only ValueError would otherwise meet an escaping exception --
        # which every guard here treats as noise rather than a veto, so the command RUNS.
        raise ValueError("_Recorder.extend would drop reading trails; use extend_child")

    def extend_child(
        self, invocations: list[Invocation], trails: list[ReadingTrail]
    ) -> None:
        """Take a child context's records and the trails that child already filled in."""
        if len(invocations) != len(trails):
            raise ValueError("invocation/trail misalignment from a child context")
        super().extend(invocations)
        self.trails.extend(trails)


def _walk_once(
    outer: str,
    nested: list[CommandContext],
    base_cwd: str | None,
    max_depth: int,
    budget: _ParseBudget,
    primary_chain: bool = False,
) -> tuple[list[Invocation], str | None, list[ReadingTrail]]:
    """Walk ONE prepared reading of one context in source order, recursing into its children.

    Split out of `_walk_context` so that function can drive this one once per reading assignment.
    Everything here is the walk as it always was; the variant dimension lives entirely in the
    caller.

    Args:
        outer: The prepared outer string, contexts already replaced by placeholders.
        nested: The contexts `outer`'s placeholders refer to.
        base_cwd: Working directory in force when this context starts.
        max_depth: Maximum nesting depth before the input is treated as ambiguous.
        budget: The walk-wide parse budget, threaded into every child walk. Never defaulted — a
            per-call default silently disables the limit it exists to impose.
        primary_chain: True when this reading was reached through its parents' FIRST assignments,
            which makes each child's own first assignment free of the budget. Defaults to False:
            an unmarked caller gets the charging behaviour, since the wrong answer here withdraws
            a reading rather than adding one.

    Returns:
        The `Invocation` records found, in command order, the working directory in force at the
        END of this context, and one `ReadingTrail` per invocation AT THE SAME LIST POSITION. The
        record names its own fields — this line deliberately does not respell them, which is how
        it came to disagree with the code it describes.

        The trails this function returns are RELATIVE: an invocation found in this very context
        carries no step at all, because this function is not told which assignment produced the
        text it was handed. `_walk_context` knows, and prepends its own step to every trail on the
        way out — including the ones a child already filled in, so the chain reads outermost-first.

    Raises:
        ValueError: On unbalanced quotes, an unterminated context, or excessive nesting.
    """
    tokens = strip_redirects(tokenize(newlines_to_separators(outer)))

    # `results` is a recorder rather than a plain list so that EVERY append carries a trail with
    # it: a parallel list appended at five separate record sites is an alignment nobody checks,
    # and a trail one position out is a diagnostic that names the wrong reading — worse than none,
    # because it reads as authoritative. Appending both in one operation makes the alignment
    # structural, exactly as the walk carries `InvocationTokens` on the record instead of beside it.
    results = _Recorder()
    cwd_state = base_cwd
    walked: set[int] = set()

    def _descend(span: list[str], cwd: str | None) -> None:
        """Walk every nested context marked anywhere in `span`, in source order.

        A nested context executes BEFORE the command whose tokens carry it, so its invocations are
        appended first. All contexts are real subshells: each sees `cwd`, none of their `cd`s
        escape.
        """
        for tok in span:
            for idx in _placeholder_indices(tok):
                if idx >= len(nested):
                    # Unreachable while the marker is unforgeable, but a bare IndexError here would
                    # escape this module's documented ValueError taxonomy and reach a consumer that
                    # swallows only ValueError.
                    raise ValueError("context marker index out of range")
                if idx in walked:
                    continue
                walked.add(idx)
                sub_results, _, sub_trails = _walk_context(
                    nested[idx], cwd, max_depth, budget, primary_chain=primary_chain
                )
                results.extend_child(sub_results, sub_trails)

    i, n = 0, len(tokens)
    subshell_cwds: list[str | None] = []
    while i < n:
        tok = tokens[i]

        # An ordinary `( … )` subshell isolates cwd exactly as a substitution does. COUNT the
        # parens inside any all-operator token rather than comparing the token: `punctuation_chars`
        # GROUPS operator runs, so `((cd /x && ls))` arrives as `((` and `))`, and
        # `(cd /x && ls)&&git push` arrives as `)&&`. A check written against the spaced, depth-1
        # form passes its own test and leaks everywhere else — and a leaked cwd is the direction
        # that ALLOWS.
        if tok and all(c in "();|&" for c in tok) and ("(" in tok or ")" in tok):
            for ch in tok:
                if ch == "(":
                    subshell_cwds.append(cwd_state)
                elif ch == ")":
                    cwd_state = subshell_cwds.pop() if subshell_cwds else None
            i += 1
            continue

        cd_pos = (
            _cd_command_position(tokens, i) if tok in ("cd", "pushd", "popd") else False
        )
        if tok in ("cd", "pushd") and cd_pos is not False:
            j = i + 1
            target = None
            while j < n and not is_op(tokens[j]):
                if not tokens[j].startswith("-"):
                    target = tokens[j]
                    break
                j += 1
            _descend(tokens[i : j + 1], cwd_state)
            cwd_state = _resolve_cd(cwd_state, target) if cd_pos else None
            i += 1
            continue

        # `popd` is not tracked as a stack: any popd makes the cwd unresolvable. This mirrors the
        # rule a consumer gate implements today, and porting it is MANDATORY — that gate's own cwd
        # walk is deleted once it consumes this primitive, so omitting it opens a new hole.
        if tok == "popd" and cd_pos is not False:
            cwd_state = None
            i += 1
            continue

        if is_git(tok) and _git_starts_command(tokens, i):
            j = i + 1
            cdir = None
            # Captured BEFORE the option walk moves `j`: `tokens[i + 1 : j]` at each record site
            # below is then exactly this invocation's global-option run, however that walk ended.
            env_pre = _env_prefix(tokens, i)
            # Set when a value-taking option's value slot holds an operator-LOOKING token. That is
            # genuinely ambiguous: `is_op` classifies by TEXT and shlex(posix=True) strips quotes,
            # so a real `;` and a quoted `';'` (a directory literally named `;`) are the same
            # token here. Bash treats them oppositely — one ends the command, one is a plain
            # argument — and this walk cannot tell them apart without tokenize carrying shlex's
            # punctuation state, which it does not.
            #
            # So do not DROP the invocation on that ambiguity. Dropping it was measured to turn
            # `git -C ';' push origin dev` from blocked into ALLOWED, because the whole invocation
            # (push included) then vanished from the walk. Fall back instead to recording the
            # token as the subcommand — the pre-existing behaviour — which fails the consumers'
            # literal-subcommand rule and blocks. Over-block on ambiguity; never disappear.
            value_slot_op = False
            unknown_opt: str | None = None
            while j < n and tokens[j].startswith("-"):
                opt = tokens[j]
                kind = classify_global_opt(opt)
                if kind == "unknown":
                    # F2. The old fallback here was `else: j += 1` — assume valueless and step
                    # over. That is a blocklist, and it admits every option nobody listed: for
                    # `git --attr-source HEAD <push> origin dev` it stepped over `--attr-source`,
                    # took `HEAD` as the subcommand, and left the push in the argument segment,
                    # which no consumer re-examines. Measured ALLOWED against the shipped guard.
                    #
                    # Guessing the other way is no better — assuming value-taking would let an
                    # unknown FLAG swallow a real subcommand. There is no safe guess, so stop
                    # walking and mark the invocation unjudgeable, exactly as the ambiguous
                    # value-slot below does. Break rather than continue: every token past here
                    # depends on the arity we just failed to determine.
                    unknown_opt = opt
                    break
                # An option that takes a VALUE must never consume a control operator as that
                # value. `git -c ; git push origin dev` made `-c` swallow the `;`, so the walk
                # resumed at `git` and recorded ONE invocation with subcommand "git" and the push
                # buried in its argument segment — `git` is not `push`, is not allowlisted, and
                # `alias.git` does not exist, so the guard ALLOWED it. Measured exit 0.
                #
                # This is the same theft the operator branch below fixes one slot to the right,
                # and the two must agree: hand the operator back to the loop rather than eating
                # it. A real shell would fail on the missing value anyway, so nothing legitimate
                # is lost.
                takes_value = kind == "value"
                if takes_value and j + 1 < n and is_op(tokens[j + 1]):
                    value_slot_op = True
                    j += 1
                elif opt == "-C" and j + 1 < n:
                    cdir = tokens[j + 1]
                    j += 2
                elif opt.startswith("-C") and len(opt) > 2:
                    cdir = opt[2:]
                    j += 1
                elif takes_value and j + 1 < n:
                    j += 2
                else:
                    # Reached only by a classified FLAG, or by a value-taking option with no token
                    # left to consume (the truncated branch below then handles it). It is no
                    # longer the catch-all it used to be: "unknown" broke out above.
                    j += 1
            if unknown_opt is not None:
                # Same posture as the ambiguous value slot below, and for the same reason: record
                # the invocation rather than dropping it. The unknown option becomes the recorded
                # subcommand, which no consumer's literal-subcommand rule accepts and no alias
                # lookup resolves, so the invocation blocks. Deliberately NO argument scan (seg=[])
                # — scanning forward past an option whose arity is unknown is exactly the guess
                # this branch exists to refuse, and it would bury any following invocation in a
                # segment nothing independently judges.
                _descend(tokens[i:j], cwd_state)
                results.append(
                    Invocation(
                        cwd_state,
                        cdir,
                        unknown_opt,
                        [],
                        InvocationTokens(env_pre, tokens[i + 1 : j]),
                    )
                )
                i = j
                continue
            if j >= n:
                # Truncated invocation: no subcommand. Still descend into the global-option run
                # before giving up, or a context hidden there is lost.
                _descend(tokens[i:n], cwd_state)
                break
            if is_op(tokens[j]):
                if value_slot_op:
                    # F3: `value_slot_op` means tokens[j] is the SAME ambiguous token the comment
                    # above already named — it might be a real control operator (a `)` closing a
                    # subshell, a `;` ending the command) or the literal quoted value a directory
                    # named e.g. `;` would produce. Recording tokens[j] as the subcommand (below)
                    # covers the second reading: an unresolvable, non-word "subcommand" fails the
                    # consumers' literal-subcommand rule and blocks, so content that was hiding
                    # behind the quoted value is never silently allowed.
                    #
                    # But scanning forward from here for an argument segment — the way the generic
                    # fallback below does for a genuine subcommand — covers that second reading only
                    # by ACCIDENT, and breaks the first one: if tokens[j] really is a `)`, grabbing
                    # tokens after it as this invocation's `seg` both buries whatever real
                    # invocation follows (never independently judged, allowed outright if THIS
                    # invocation's dir turns out dormant) and skips past the operator itself, so the
                    # paren-counting branch at the top of this loop never sees it and never pops
                    # `subshell_cwds`. That is exactly the leak
                    # `(cd OTHER && git -c) ; <push of dev>` measures: two invocations, both judged
                    # in `OTHER`, the push included.
                    #
                    # So: append the ambiguous invocation with NO argument scan (seg=[]) — it
                    # over-blocks on its own via the unresolvable subcommand, never disappearing —
                    # and hand the token back to the main loop (`i = j`, never `i = j + 1` or the
                    # `k`-scanning fallback below) so a real operator is processed normally (the
                    # paren branch pops the subshell cwd) and a real subsequent invocation is walked
                    # on its own, in its own correctly resolved cwd. Both halves are required
                    # together: dropping the append (nothing recorded) turns the preserve rows
                    # `git -C ';' <push> origin dev` / `git -c ';' <push> origin dev` from
                    # blocked into allowed, since neither reading is a `git`-prefixed invocation the
                    # walk would otherwise notice.
                    _descend(tokens[i:j], cwd_state)
                    results.append(
                        Invocation(
                            cwd_state,
                            cdir,
                            tokens[j],
                            [],
                            InvocationTokens(env_pre, tokens[i + 1 : j]),
                        )
                    )
                    i = j
                    continue
                # OPTIONS-ONLY invocation (`git --version | head`): the token in command position
                # is the following control operator, not a subcommand. Recording it as one was a
                # FAIL-OPEN, not just a nonsense verdict — it STEALS the token from the paren
                # branch above, so `(cd /elsewhere && git --version) && git push origin dev` never
                # popped `subshell_cwds`, the subshell's cd leaked, and the push was judged in
                # `/elsewhere`. Measured against the shipped guard: that command was ALLOWED while
                # the bare push blocked.
                #
                # Hand the operator BACK to the loop — `i = j`, never `i = j + 1`, which skips the
                # paren handling and re-creates the very leak. And never `break` (the truncated
                # branch above): everything after the operator would go unscanned, so
                # `git --version && git push origin dev` — caught today — would newly slip through.
                # Classify with `is_op`, which counts characters, because `punctuation_chars` fuses
                # runs like `)&&`; a literal comparison passes its own test and leaks on the rest.
                _descend(tokens[i:j], cwd_state)
                i = j
                continue
            if is_git(tokens[j]):
                # The option run ended on a git-like word, so the SUBCOMMAND slot holds it.
                # This used to DROP the invocation, on the premise that a bare `git` is never a
                # real subcommand. An alias named `git` makes it one (`git -c alias.git=<push>
                # git origin dev` ran a push past both push guards), and since `is_git` accepts
                # opaque words, an expansion here may be anything (`git $(true)git origin dev`).
                # So record it, exactly as the unknown-option branch above does: the git-like
                # word becomes the subcommand, `subcommand_is_indeterminate` says so, and every
                # consumer blocks it. Deliberately NO argument scan (seg=[]), for that branch's
                # reason. Then resume AT `j`, never past it: the `git -c ; git <push> …` shape
                # this branch was written for still has its real invocation walked on its own.
                _descend(tokens[i:j], cwd_state)
                results.append(
                    Invocation(
                        cwd_state,
                        cdir,
                        tokens[j],
                        [],
                        InvocationTokens(env_pre, tokens[i + 1 : j]),
                    )
                )
                i = j
                continue
            seg: list[str] = []
            k = j + 1
            while (
                k < n
                and not is_op(tokens[k])
                and not (is_git(tokens[k]) and _git_starts_command(tokens, k))
            ):
                seg.append(tokens[k])
                k += 1
            # The stop at a nested command-position `git` is what cut argument lists short. Inside
            # an argument list that `git` is reached through a reserved word -- walking back from a
            # `git` otherwise meets an argument word first, and bash recognises a reserved word only
            # in command position, so behind an argument it is an ordinary word and the rest of the
            # line is still THIS command's argv. (`is_git` no longer matches an env-assignment token,
            # so a nested `git` can no longer be reached that way instead.) Stopping there once cut the
            # list short -- `git <push> origin main -o then HEAD:refs/heads/x/git dev` recorded
            # `['origin', 'main', '-o', 'then']` and the publication guard never saw `dev`.
            #
            # So the argument list runs on to the next operator, while the walk still RESUMES at the
            # nested `git` and records it. That phantom is deliberately kept: dropping it would be
            # exact only while `is_op` never misses a real operator, and a missed operator would turn
            # a dropped phantom into a real push hidden in another command's argv. Do not "fix" the
            # phantom by recognising reserved words only in command position either -- that reading
            # was measured to stop detecting `for NAME do`, `function NAME {` and `coproc NAME {`.
            # The rule is recursive: each phantom's own list grows the same way.
            resume = k
            while k < n and not is_op(tokens[k]):
                seg.append(tokens[k])
                k += 1
            # THE SPAN RULE. Descend into every token this invocation consumes — its global-option
            # run AND its argument segment — not just the token in command position.
            # `git commit -m "$(git push origin dev)"` hides the push in the argument segment, and
            # the walk once jumped straight past it. The descent stops at `resume`, not at the end of
            # the grown list: every token past `resume` belongs to the nested invocation the walk
            # resumes at, and is descended once, in order, when the walk resumes there -- by that
            # invocation's own walk, or by the main loop's per-token fallthrough -- so nested
            # contexts keep their order.
            _descend(tokens[i:resume], cwd_state)
            results.append(
                Invocation(
                    cwd_state,
                    cdir,
                    tokens[j],
                    seg,
                    InvocationTokens(env_pre, tokens[i + 1 : j]),
                )
            )
            i = resume
            continue

        _descend([tok], cwd_state)
        i += 1

    # BACKSTOP: no extracted context may be silently dropped. `strip_redirects` deletes a redirect
    # operator AND its target, so a context used as a redirect target never reaches the loop above
    # — yet the shell still executes it. Rather than enumerate every token-consuming path, assert
    # the invariant directly. Walk these with an UNRESOLVABLE cwd, never `base_cwd`: we do not know
    # where in the command they ran, and guessing the entry directory MIS-ATTRIBUTES them, which is
    # the direction that allows.
    for idx, context in enumerate(nested):
        if idx not in walked:
            sub_results, _, sub_trails = _walk_context(context, None, max_depth, budget)
            results.extend_child(sub_results, sub_trails)

    return list(results), cwd_state, results.trails


def _lost_trail(
    depth: int,
    count: int,
    unreadable: list[tuple[tuple[int, ...], str]],
    untried: int,
) -> ReadingTrail:
    """The trail of an `_indeterminate_invocation` -- WHICH readings were lost, and how.

    The marker corresponds to no command text, so its step carries `joined=None` rather than an
    assignment: it stands for readings that were never read, which is a different fact from "this
    record came from the all-drop reading" and must not render as one.
    """
    parts = []
    if unreadable:
        parts.append("unreadable")
    if untried:
        parts.append("truncated")
    return ReadingTrail(
        steps=(ReadingStep(depth, count, None),),
        lost="+".join(parts) or "unreadable",
        unreadable=tuple(unreadable),
        untried=untried,
    )


def _walk_context(
    ctx: CommandContext,
    base_cwd: str | None,
    max_depth: int,
    budget: _ParseBudget | None = None,
    primary_chain: bool = True,
) -> tuple[list[Invocation], str | None, list[ReadingTrail]]:
    """Walk ONE context under every reading of its ambiguous heredocs, and union what they found.

    A quoted heredoc body ending in an odd backslash run reads two ways, and which one bash takes
    depends on a context question this module answers unreliably — so it answers neither: it walks
    the context once per reading assignment and records the union. An EXTRA invocation is an
    over-read, costing a false block; a LOST one is a bypass, because the join reading mangles a
    word (`dev` -> `devEOF`) and can lose a push target.

    Three rules, each measured rather than reasoned:

    - **The primary (all-drop) variant fixes ORDER and cwd.** All-drop is what bash does at the top
      level, the only case that occurs in real commands, and drop never swallows a delimiter.
    - **Raise only if EVERY variant raises.** A quoted delimiter may contain `(`, so the all-join
      reading of a command both bashes run can raise `unterminated command substitution`;
      propagating that raise discards a correct read and hands the timing guard, and push-guard
      behind a hidden git word, a fail-open. When some variant raised but another parsed, ONE
      `_indeterminate_invocation` is appended so the ambiguity is visible in-band.
    - **Exceeding the parse budget never raises** — it emits the same marker. Raising there would
      hand the timing guard and the publication guard's opaque path an ALLOW.

    Args:
        ctx: The context to walk.
        base_cwd: Working directory in force when this context starts.
        max_depth: Maximum nesting depth before the input is treated as ambiguous.
        budget: The walk-wide parse budget. None creates one, which is correct ONLY at the entry
            point: every recursive call must pass the budget it was given, or the cap counts one
            context's parses instead of the walk's.

    Returns:
        The `Invocation` records found, in command order, the working directory in force at the
        END of this context — both from the primary variant, topped up by later variants — and one
        `ReadingTrail` per invocation, at the SAME list position, naming the reading assignment
        that produced it. The trails are a DIAGNOSTIC: no consumer's verdict may vary with them,
        because which reading bash takes is exactly the question this walk refuses to guess.

    Raises:
        ValueError: On unbalanced quotes, an unterminated context, or excessive nesting — only when
            no reading of this context parsed.
    """
    if ctx.depth > max_depth:
        raise ValueError("maximum command-context depth exceeded")
    if budget is None:
        budget = _ParseBudget(MAX_TOTAL_PARSES)

    # Total; never raises — which is why the count survives a parse that does.
    _masked, count = _mask_with_count(ctx.text, ctx.depth)
    assignments = _reading_assignments(count)
    results: list[Invocation] = []
    trails: list[ReadingTrail] = []
    primary_cwd = base_cwd
    first_error: ValueError | None = None
    parsed_any = False
    truncated = False
    untried = 0
    unreadable: list[tuple[tuple[int, ...], str]] = []

    for tried, readings in enumerate(assignments):
        # The PRIMARY reading is free. It is what the pre-change tokenizer walked, so charging it
        # to the budget this change introduced lets a large enough input withdraw a reading the
        # old code always had -- measured 2026-09-22 as a capability regression at
        # `commit-subject-guard.py`, which reads no marker and so simply stopped seeing an
        # 80-character commit subject from 127 ambiguous contexts on (dev blocks it at every N).
        # Free parses total one per CONTEXT, exactly the old cost; the variants this change added
        # remain capped, which is the term that made an unbounded walk a bypass.
        free = primary_chain and tried == 0
        if not free and not budget.spend():
            truncated = True
            # Every assignment from this one on, including this one: the spend FAILED, so nothing
            # was walked under it.
            untried = (1 << count) - tried
            break
        joined = tuple(sorted(i for i, as_join in readings.items() if as_join))
        try:
            # BOTH the prepare and the walk sit inside the try: a child context that raises is one
            # variant failing, not the parent's failure.
            outer, nested, _count = _prepare(ctx.text, ctx.depth, readings)
            found, end_cwd, found_trails = _walk_once(
                outer, nested, base_cwd, max_depth, budget, primary_chain=free
            )
        except ValueError as exc:
            first_error = first_error if first_error is not None else exc
            if len(unreadable) < _MAX_REPORTED_READINGS:
                unreadable.append((joined, _reading_category(exc)))
            continue
        # This context's own step, prepended to every trail the variant produced — including the
        # ones a child already filled in, so the chain reads outermost-first. A nested record must
        # name its ANCESTORS' assignments too: it may exist only because a parent was read as
        # JOIN, and a trail naming just the innermost context would report that record as primary.
        step = ReadingStep(ctx.depth, count, joined)
        found_trails = [t._replace(steps=(step, *t.steps)) for t in found_trails]
        if not parsed_any:
            results, primary_cwd, trails = found, end_cwd, found_trails
        else:
            results, trails = _union_max_multiset(results, trails, found, found_trails)
        parsed_any = True

    if not parsed_any:
        if truncated:
            # The cap, not the input: "exceeding the cap never raises".
            return (
                [_indeterminate_invocation()],
                base_cwd,
                [_lost_trail(ctx.depth, count, unreadable, untried)],
            )
        raise (
            first_error
            if first_error is not None
            else ValueError("no readable reading of this command context")
        )
    if first_error is not None or truncated:
        results = results + [_indeterminate_invocation()]
        trails = trails + [_lost_trail(ctx.depth, count, unreadable, untried)]
    return results, primary_cwd, trails


def iter_git_invocations_with_cwd(
    command: str, base_cwd: str | None, max_depth: int = MAX_CONTEXT_DEPTH
) -> list[tuple[str | None, str | None, str, list[str]]]:
    """Find every `git` invocation in command position, across all contexts, with its cwd.

    This is the primitive an invocation-shaped consumer derives from. Tracking `cd` here rather
    than in a caller is deliberate: only this walk knows both a token's position and which
    invocation it belongs to, so alignment between the two is structural instead of asserted.

    Args:
        command: The raw shell-command string to scan.
        base_cwd: Working directory the command starts in, or None if already unknown.
        max_depth: Maximum context nesting depth before the input is treated as ambiguous.

    Returns:
        One ``(effective_dir, cdir, subcommand, arg_tokens)`` tuple per invocation, in command
        order. ``effective_dir`` is None when the directory could not be resolved statically.

    Raises:
        ValueError: On unbalanced quotes, an unterminated context, a reserved marker in the input,
            nesting deeper than ``max_depth``, or input longer than ``MAX_COMMAND_LENGTH``.
            Callers decide whether that means block or allow.
    """
    if len(command) > MAX_COMMAND_LENGTH:
        raise ValueError("command exceeds the maximum length this scanner will parse")
    # Sliced to four deliberately: the walk records a fifth `InvocationTokens` element, and this
    # signature is unpacked positionally at ~40 call sites. Widening it here would break every one
    # of them for the benefit of the single consumer that wants the tokens, which asks instead via
    # `iter_git_invocations_detailed` below.
    return [
        r[:4] for r in _walk_context(CommandContext(command, 0), base_cwd, max_depth)[0]
    ]


def iter_git_invocations_detailed(
    command: str, base_cwd: str | None, max_depth: int = MAX_CONTEXT_DEPTH
) -> list[Invocation]:
    """`iter_git_invocations_with_cwd`, plus each invocation's env prefix and global-option run.

    Same walk, same order, same raises — the extra element is recorded by the walk itself, so a
    consumer scoping a check to one invocation's own tokens gets an alignment it does not have to
    assert. Use this when the question is about an invocation's CONFIGURATION (`-c`, `GIT_CONFIG_*`)
    rather than its subcommand; use the four-tuple form for everything else.

    Args:
        command: The raw shell-command string to scan.
        base_cwd: Working directory the command starts in, or None if already unknown.
        max_depth: Maximum context nesting depth before the input is treated as ambiguous.

    Returns:
        One ``(effective_dir, cdir, subcommand, arg_tokens, tokens)`` tuple per invocation.

    Raises:
        ValueError: Same conditions as `iter_git_invocations_with_cwd`.
    """
    if len(command) > MAX_COMMAND_LENGTH:
        raise ValueError("command exceeds the maximum length this scanner will parse")
    return iter_git_invocations_with_readings(command, base_cwd, max_depth)[0]


def iter_git_invocations_with_readings(
    command: str, base_cwd: str | None, max_depth: int = MAX_CONTEXT_DEPTH
) -> tuple[list[Invocation], list[ReadingTrail]]:
    """`iter_git_invocations_detailed`, plus WHICH READING of the command produced each record.

    Same walk, same order, same raises — `iter_git_invocations_detailed` is now this function with
    the trails dropped, so the two can never disagree about what the walk found.

    The trails are parallel to the invocations BY POSITION and are never keyed by the invocation
    itself: the union is a MAX-MULTISET, so two records legitimately share a key and a key-based
    map would collide exactly where the union did its work.

    For a DIAGNOSTIC, never for a verdict. No guard's ALLOW/BLOCK may vary with which reading found
    an invocation — that is the question this walk exists to stop guessing. What this enables is
    `explain-git-command.py` telling an operator WHY a command blocked: the design accepts an
    over-read from a second reading, and an unexplainable over-read is what gets "fixed" by
    narrowing a matcher, the repair this repo has twice measured as the fail-open it removed.

    Args:
        command: The raw shell-command string to scan.
        base_cwd: Working directory the command starts in, or None if already unknown.
        max_depth: Maximum context nesting depth before the input is treated as ambiguous.

    Returns:
        The invocations, and one `ReadingTrail` per invocation at the same list position.

    Raises:
        ValueError: Same conditions as `iter_git_invocations_with_cwd`.
    """
    if len(command) > MAX_COMMAND_LENGTH:
        raise ValueError("command exceeds the maximum length this scanner will parse")
    invocations, _cwd, trails = _walk_context(
        CommandContext(command, 0), base_cwd, max_depth
    )
    return invocations, trails


def iter_context_token_streams(
    command: str, max_depth: int = MAX_CONTEXT_DEPTH
) -> list[list[str]]:
    """Every command context's normalized token stream, outermost first, in source order.

    For a consumer whose own logic is token- or segment-shaped rather than invocation-shaped: it
    gets each context's tokens and applies its existing rules per context, instead of re-deriving
    them from an invocation tuple that has already discarded segment structure.

    Preparation MUST match `_walk_context` exactly — both call `_prepare`, over the SAME
    `_reading_assignments` and charged to one `_ParseBudget`, so the two primitives can never
    disagree about what a context contains. That is not tidiness: the publication guard pairs
    `_exported_injection_reason` with `_find_block_reason`, and the timing guard correlates
    `_find_first_push` with `_push_target_dirs` BY ORDER. Leaving this primitive single-reading
    while the walk enumerates would take a reading away from three guards.

    **The primary stream precedes every variant stream**, because `_reading_assignments` puts the
    all-drop assignment first. Load-bearing, not cosmetic: `_find_first_push` returns on the FIRST
    push-carrying segment and the timing guard returns 0 when that one is authorized, so a
    join-first order would let a body whose last line ends `ALLOW_GIT_WRITE=1` plus a continuation
    authorize a push it does not authorize.

    **A LOST reading is signalled in-band**, by `_indeterminate_stream`, exactly as `_walk_context`
    signals it with `_indeterminate_invocation` — one marker per affected CONTEXT, appended after
    that context's streams (so the primary-first invariant above is untouched). Leaving it out was
    measured as a REGRESSION against shipped `dev`: on the witness `bash <<'(x'` / `git status` /
    `x=$\\` / `(x` the walk recorded `[('status', []), (AMBIGUOUS_READING_SUBCOMMAND, [])]` and the
    publication guard blocked at rc 2, while this primitive skipped the raising variant with a bare
    `continue` and push-guard and the timing guard both returned rc 0 — where `dev` blocks the same
    witness at rc 2. Both branches that can lose a reading emit it: a variant that RAISES, and an
    exhausted BUDGET. Exceeding the cap never raises here either.

    Known cost, accepted: children are re-collected under each variant, so a child whose text the
    reading did not change contributes duplicate streams. That is an over-read — more text scanned,
    never less.

    Placeholders appear as ordinary word tokens (never `git`, never a control operator), so they
    are inert to a caller's command-position and segment logic.

    A consumer that tested the stream COUNT as a shape guard must stop: the count now varies with
    the readings. `commit-subject-guard.py` did, and its test moved onto `split_command_contexts`.

    Args:
        command: The raw shell-command string to scan.
        max_depth: Maximum context nesting depth before the input is treated as ambiguous.

    Returns:
        One token list per context per readable variant, the top-level command's primary first,
        plus one `_indeterminate_stream` per context that lost a reading.

    Raises:
        ValueError: Same conditions as `iter_git_invocations_with_cwd` — for a context, only when
            no reading of it parsed AND the cap was not what stopped it.
    """
    if len(command) > MAX_COMMAND_LENGTH:
        raise ValueError("command exceeds the maximum length this scanner will parse")
    streams: list[list[str]] = []
    budget = _ParseBudget(MAX_TOTAL_PARSES)

    def _collect(ctx: CommandContext, primary_chain: bool = True) -> None:
        if ctx.depth > max_depth:
            raise ValueError("maximum command-context depth exceeded")
        _masked, count = _mask_with_count(ctx.text, ctx.depth)
        parsed_any = False
        truncated = False
        first_error: ValueError | None = None
        for tried, readings in enumerate(_reading_assignments(count)):
            # Free primary, exactly as `_walk_context` does it and for the same measured reason.
            # This primitive is the one push-guard reads, so a withdrawn primary here is a
            # fail-open at that guard even when the walk is perfect -- the shape this branch has
            # already had to fix twice.
            free = primary_chain and tried == 0
            if not free and not budget.spend():
                truncated = True
                break
            # EVERYTHING this variant does sits inside the try, and a failure rolls back what
            # it appended. `_walk_context` has always done it this way; this primitive did not,
            # and the gap was a measured fail-open: only `_prepare` was guarded, so a variant
            # whose CHILD context was unparseable raised out of the whole primitive instead of
            # being one failed reading. Measured on `x=`bash <<'\"'` / a hidden-git-word push /
            # `echo tail \\` / `\"` / backtick: both bashes RUN the push, the walk records it,
            # and push-guard and the publication guard went from BLOCK on dev to ALLOW here,
            # because both consume this primitive and both treat its ValueError as nothing to
            # judge. 24 such ALLOW regressions in 432 push-carrying shapes.
            mark = len(streams)
            try:
                outer, nested, _ = _prepare(ctx.text, ctx.depth, readings)
                streams.append(strip_redirects(tokenize(newlines_to_separators(outer))))
                for child in nested:
                    _collect(child, primary_chain=free)
            except ValueError as exc:
                # This reading contributed nothing; leave no partial trace behind.
                del streams[mark:]
                first_error = first_error if first_error is not None else exc
                continue
            parsed_any = True
        # A LOST reading is signalled in-band, exactly as `_walk_context` does it, and for the same
        # reason: silently dropping it is the fail-open direction. ONE marker per affected CONTEXT,
        # not per command -- the walk appends one per context too, and the timing guard correlates
        # `_find_first_push` with `_push_target_dirs` BY ORDER, so a per-command marker would
        # collapse several contexts' losses into a single record the walk does not match.
        # It goes AFTER this context's streams and its children's, so the invariant
        # "the primary stream precedes every variant stream" is untouched.
        if not parsed_any:
            if truncated:
                # The cap, not the input: "exceeding the cap never raises". Returning no stream at
                # all here would be the same silent loss this marker exists to close.
                streams.append(_indeterminate_stream())
                return
            if first_error is not None:
                raise first_error
            return
        if first_error is not None or truncated:
            streams.append(_indeterminate_stream())

    _collect(CommandContext(command, 0))
    return streams


# Deliberately GENEROUS: this decides whether an UNPARSEABLE command is worth failing closed over,
# so a false positive costs a loud block on a command that mentions git, while a false negative
# silently reopens the fail-open it exists to close. It must over-match, never under-match — which
# is why it runs on dequoted text and ignores quoting entirely. Quoting controls EXPANSION, not
# EXECUTION: `'git' 'push'` still pushes.
GIT_WORD_RE = re.compile(r"(?:^|[^A-Za-z0-9_])git(?:[^A-Za-z0-9_]|$)")
_QUOTING_CHARS = re.compile(r"""['"\\]""")


def has_git_word(command: str) -> bool:
    """True if `command` mentions `git` at all, ignoring quoting and escaping.

    Args:
        command: The raw shell-command string.

    Returns:
        True if a `git` word appears anywhere in the dequoted text.
    """
    return bool(GIT_WORD_RE.search(_QUOTING_CHARS.sub("", command)))


def iter_git_invocations(command: str) -> list[tuple[str | None, str, list[str]]]:
    """Find every `git` invocation in command position, across all nested contexts.

    Thin wrapper over `iter_git_invocations_with_cwd` that drops the resolved directory, preserving
    this function's original signature and its swallow-to-empty behavior.

    Args:
        command: The raw shell-command string to scan.

    Returns:
        One ``(cdir, subcommand, arg_tokens)`` tuple per invocation. Empty on any tokenizing
        ambiguity — this function never raises, so a caller needing fail-closed behavior must use
        `iter_git_invocations_with_cwd` directly.
    """
    try:
        return [
            (cdir, sub, seg)
            for _dir, cdir, sub, seg in iter_git_invocations_with_cwd(command, None)
        ]
    except ValueError:
        return []

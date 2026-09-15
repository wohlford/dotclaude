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
"""

from __future__ import annotations

import os
import re
import shlex
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
# `git` in command position the same way `; git push` does. Consulted ONLY by `_git_starts_command`
# — the `is_git(...)`-guarded call sites below — and never by `_cd_command_position`, which
# recognises `cd`/`pushd`/`popd` and likewise consults no reserved word. That scoping is
# load-bearing, not incidental: widening it to cover
# `cd`/`pushd`/`popd` too was measured to make three EXISTING blocks disappear. A `cd` right after
# `if`/`while`/`!` would then itself be read as starting a command, cwd tracking would follow it
# into a directory with no `.publication.toml`, and the guard would go dormant there —
# `! cd OTHER ; <push>`, `if cd OTHER ; then :; fi ; <push>` and
# `while cd OTHER ; do break; done ; <push>` all flip BLOCK -> ALLOW under the wider scoping, even
# though the push invocation is still *detected* (a detection-only property passes while this
# happens — the property that matters here is that the BLOCKED SET does not shrink).
#
# `in` and `;;` are deliberately excluded: both are followed by a *pattern*, not a command, so
# treating them as boundaries would let `for f in git; do …` manufacture a phantom `git` invocation
# out of a loop list item. `}`, `fi`, `done`, `esac` are also excluded: each ENDS a block, and bash
# treats a command placed directly after one — with no `;` or newline between — as a syntax error,
# so admitting them as boundaries would add no real detection.
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


def _read_heredoc_delimiter(text: str, i: int) -> tuple[str | None, int]:
    """Read the delimiter word of a heredoc operator at `i`.

    The delimiter may be quoted (`<<'EOF'`, `<<"EOF"`) or escaped (`<<\\EOF`) — which in a real
    shell decides whether the BODY is expanded, but never changes where the body ENDS, which is all
    this pass needs. The quotes are part of the operator, not of the delimiter it names, so they are
    stripped here to get the terminator text to compare lines against.

    Args:
        text: The full command string.
        i: Index of the first character of the delimiter word.

    Returns:
        The delimiter text and the index just past it, or ``(None, i)`` when no delimiter can be
        read (an unterminated quote, or nothing there at all) — in which case the caller treats the
        `<<` as ordinary text rather than guessing.
    """
    n = len(text)
    start = i
    parts: list[str] = []
    while i < n:
        ch = text[i]
        if ch in _HEREDOC_DELIM_END:
            break
        if ch in "'\"":
            close = text.find(ch, i + 1)
            if close == -1:
                return None, start
            parts.append(text[i + 1 : close])
            i = close + 1
            continue
        if ch == "\\" and i + 1 < n:
            parts.append(text[i + 1])
            i += 2
            continue
        parts.append(ch)
        i += 1
    delim = "".join(parts)
    return (delim, i) if delim else (None, start)


def _consume_heredoc_body(
    text: str, i: int, delim: str, strip_tabs: bool, out: list[str]
) -> int:
    """Copy one heredoc body to `out` with its unmatched quotes neutralised, and return the index
    just past its terminator line.

    The terminator line is operator text rather than body, so it is copied verbatim — neutralising
    it could stop a later run from recognising it and swallow the rest of the command.

    An unterminated body (no line ever equals `delim`) consumes the remainder. That tolerates
    quotes in text the shell would also treat as body, and keeps the text visible; it never hides
    a command.
    """
    n = len(text)
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
    out.append(_neutralize_unmatched_quotes(text[body_start:body_end]))
    if term_start is None:
        return n
    # The terminator is operator text, not body — copy it verbatim.
    out.append(text[term_start:term_end])
    return term_end


def mask_heredoc_quotes(command: str) -> str:
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

    Returns:
        The command with quote characters inside heredoc bodies backslash-escaped.
    """
    out: list[str] = []
    # (delimiter, strip leading tabs), in the order the operators appeared on the line.
    pending: list[tuple[str, bool]] = []
    # Quote state saved at each open command substitution. A `$( … )` body has its OWN quote
    # context — the same fact `split_command_contexts` relies on — so without this stack the `"` of
    # `git commit -m "$(cat <<'EOF' … )"` keeps this pass in double-quote mode and it never sees the
    # heredoc operator at all. That is the reported form, so the stack is not an edge case.
    contexts: list[str | None] = []
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
            delim, past = _read_heredoc_delimiter(command, k)
            if delim is not None:
                out.append(command[i:past])
                pending.append((delim, strip_tabs))
                i = past
                at_word_start = False
                continue
        if ch == "\n" and pending:
            # Bodies start on the line AFTER the operators, in the order the operators appeared —
            # `cat <<A <<B` reads A's body first, then B's.
            out.append(ch)
            i += 1
            for delim, strip_tabs in pending:
                i = _consume_heredoc_body(command, i, delim, strip_tabs, out)
            pending = []
            at_word_start = True
            continue
        out.append(ch)
        at_word_start = ch in " \t\n;&|("
        i += 1
    return "".join(out)


def fold_continuations(command: str) -> str:
    """Remove backslash-newline continuations, as the shell does before anything else.

    One half of `normalize_command`. The halves are exposed separately because they belong on
    OPPOSITE sides of the context scan: continuations must be folded BEFORE scanning (or
    `x="$\\<newline>(git push)"` reassembles into a substitution nothing ever scanned), while
    newlines must survive until AFTER it (comment handling needs the newline that ends a comment).
    Composed in order they are exactly `normalize_command` — never re-derive either half.

    Args:
        command: The raw shell-command string.

    Returns:
        The command with backslash-newline continuations removed.
    """
    return command.replace("\\\r\n", "").replace("\\\n", "")


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

    Folding is deliberately unconditional, including inside single quotes where a real shell would
    keep the backslash literal. That divergence is safe by direction: folding only ever *joins*
    text, which can reveal a command the scan would otherwise miss, and can never hide one.

    Args:
        command: The raw shell-command string.

    Returns:
        The command with continuations folded and newlines rewritten as ``;`` separators.
    """
    command = command.replace("\\\r\n", "").replace("\\\n", "")
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


def is_git(token: str) -> bool:
    """True if token invokes git (bare name or a path ending in /git)."""
    return token == "git" or token.endswith("/git")


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
    byte-for-byte the pre-existing behavior. `_git_starts_command` is the only caller that passes
    them; see `RESERVED_WORDS`'s docstring for why that scoping must not widen. The
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
    site in this module must call this, not bare `starts_command` — see `RESERVED_WORDS` for why
    that scoping is confined to `git` recognition. The `cd`/`pushd`/`popd` sites do not call this
    function at all; they call `_cd_command_position` instead, which classifies the fail-closed
    cwd behavior directly — see `GIT_ONLY_WRAPPERS` for why the leak this scoping once risked
    (a `cd` right after `exec` being tracked rather than made unresolvable) can no longer occur."""
    return starts_command(
        tokens, idx, reserved_words=RESERVED_WORDS, extra_wrappers=GIT_ONLY_WRAPPERS
    )


def _cd_command_position(tokens: list[str], i: int) -> bool | None:
    """Classify the `cd`/`pushd`/`popd` at `tokens[i]` for the cwd walk.

    True when it runs directly in THIS shell — reached from a command boundary over env assignments
    only (`FOO=1 cd X` moves the shell) — so its target is tracked exactly. None when it is reached
    through ANY wrapper or a wrapper's `--`, so the cwd becomes UNRESOLVABLE. False when it is an
    argument (`echo cd X`) and not a directory change at all.

    None is the fail-CLOSED answer, and it REPLACED a model. Whether the shell moves depends on the
    whole chain: `eval cd` and `builtin cd` move it; `nohup cd` and `env eval cd` (env cannot run
    eval) do not; `time cd` moves only where `time` is still the keyword, so `builtin time cd` runs
    /usr/bin/time in a child. Two diverse-model review rounds each found a fail-open in a set that
    tried to enumerate the movers — both in that one parameter, none in the rule. An unresolvable
    cwd blocks every guarded operation and no read (measured), and 0 of 17,929 real commands
    carrying a cd reached it through a wrapper: the precision protected nothing and cost two holes.

    `exec` is not stepped: `exec cd` never continues — the non-interactive shell exits — so nothing
    after it runs and no reading of it can leak. Reserved words are not boundaries here either;
    see `RESERVED_WORDS`.

    Consumers do not all read a None the same way, and this function does not decide that for them.
    `publication-push-guard.py` (the security boundary keeping a `dev` branch private) treats it as
    fail-CLOSED, per the paragraph above: it blocks every guarded operation and no read.
    git-timing-guard.py instead falls back to the PAYLOAD cwd on None rather than blocking — a
    DECIDED trade (Ruling R6, design record 2026-09-11-eval-wrapper-bypass), pinned in
    test_git_timing_guard.sh, not a second fail-closed reading of the same value: a `cd` reached
    through a wrapper leaves that guard scoped to the payload cwd, exactly as it was before this
    module's `cd`-tracking existed.
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
        return False
    return None if through_wrapper else True


def _resolve_cd(cwd_state: str | None, target: str | None) -> str | None:
    """Apply one `cd`/`pushd` target to the tracked working directory.

    Args:
        cwd_state: Directory in force before the `cd`, or None if already unresolvable.
        target: The `cd` target token, or None when the command names no target.

    Returns:
        The new directory, or None meaning *unresolvable* — the conservative answer whenever the
        target cannot be resolved statically (`cd -`, `cd "$VAR"`, `cd ~`, `cd "$(…)"`).
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
        return os.path.normpath(target)
    return os.path.normpath(os.path.join(cwd_state, target))


def _prepare(text: str, depth: int) -> tuple[str, list[CommandContext]]:
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

    Returns:
        The outer string (contexts replaced by placeholders, newlines still present) and the
        extracted contexts.

    Raises:
        ValueError: On unbalanced quotes, an unterminated context, or a reserved marker in input.
    """
    return split_command_contexts(
        fold_continuations(strip_comments(mask_heredoc_quotes(text))), depth
    )


def _walk_context(
    ctx: CommandContext, base_cwd: str | None, max_depth: int
) -> tuple[list[Invocation], str | None]:
    """Walk ONE context in source order, recursing into the contexts it introduces.

    Args:
        ctx: The context to walk.
        base_cwd: Working directory in force when this context starts.
        max_depth: Maximum nesting depth before the input is treated as ambiguous.

    Returns:
        The `Invocation` records found, in command order, and the working directory in force at the
        END of this context. The record names its own fields — this line deliberately does not
        respell them, which is how it came to disagree with the code it describes.

    Raises:
        ValueError: On unbalanced quotes, an unterminated context, or excessive nesting.
    """
    if ctx.depth > max_depth:
        raise ValueError("maximum command-context depth exceeded")

    outer, nested = _prepare(ctx.text, ctx.depth)
    tokens = strip_redirects(tokenize(newlines_to_separators(outer)))

    results: list[Invocation] = []
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
                sub_results, _ = _walk_context(nested[idx], cwd, max_depth)
                results.extend(sub_results)

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
                # A bare `git` can never be a legitimate subcommand, so an option run ending on
                # one means THIS invocation had no subcommand — and recording `git` as one buries
                # the real invocation's subcommand in an argument segment nothing judges.
                # Measured shape: `git -c ; git push origin dev` recorded ("git", ["push", …]).
                #
                # `and starts_command(tokens, j)` was here and is deliberately gone: it made the
                # branch UNSATISFIABLE. Every token between `i` and `j` starts with `-` or is a
                # consumed value, so `starts_command` always walks back to `tokens[i]` — the `git`
                # token itself, which is neither an operator nor a wrapper — and returns False.
                # The comment claimed a defence the code could not provide, which reads as
                # coverage while pinning nothing.
                _descend(tokens[i:j], cwd_state)
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
            # THE SPAN RULE. Descend into every token this invocation consumes — its global-option
            # run AND its argument segment — not just the token in command position.
            # `git commit -m "$(git push origin dev)"` hides the push in the argument segment, and
            # the walk jumps `i = k` straight past it.
            _descend(tokens[i:k], cwd_state)
            results.append(
                Invocation(
                    cwd_state,
                    cdir,
                    tokens[j],
                    seg,
                    InvocationTokens(env_pre, tokens[i + 1 : j]),
                )
            )
            i = k
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
            sub_results, _ = _walk_context(context, None, max_depth)
            results.extend(sub_results)

    return results, cwd_state


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
    return _walk_context(CommandContext(command, 0), base_cwd, max_depth)[0]


def iter_context_token_streams(
    command: str, max_depth: int = MAX_CONTEXT_DEPTH
) -> list[list[str]]:
    """Every command context's normalized token stream, outermost first, in source order.

    For a consumer whose own logic is token- or segment-shaped rather than invocation-shaped: it
    gets each context's tokens and applies its existing rules per context, instead of re-deriving
    them from an invocation tuple that has already discarded segment structure.

    Preparation MUST match `_walk_context` exactly — both call `_prepare`, so the two primitives
    can never disagree about what a context contains. Placeholders appear as ordinary word tokens
    (never `git`, never a control operator), so they are inert to a caller's command-position and
    segment logic.

    Args:
        command: The raw shell-command string to scan.
        max_depth: Maximum context nesting depth before the input is treated as ambiguous.

    Returns:
        One token list per context, the top-level command first.

    Raises:
        ValueError: Same conditions as `iter_git_invocations_with_cwd`.
    """
    if len(command) > MAX_COMMAND_LENGTH:
        raise ValueError("command exceeds the maximum length this scanner will parse")
    streams: list[list[str]] = []

    def _collect(ctx: CommandContext) -> None:
        if ctx.depth > max_depth:
            raise ValueError("maximum command-context depth exceeded")
        outer, nested = _prepare(ctx.text, ctx.depth)
        streams.append(strip_redirects(tokenize(newlines_to_separators(outer))))
        for child in nested:
            _collect(child)

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

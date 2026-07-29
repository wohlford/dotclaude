"""Shared shell-command tokenizer for git-invocation-detecting hooks.

Tokenizes an arbitrary shell command string safely (``shlex``, never ``eval``) and walks it to
find every `git` invocation in *command position* — the same primitive a PreToolUse hook needs to
tell a real `git commit`/`git push`/etc. from a look-alike, and to see it even inside a compound
command (`git add -A && git commit`), behind an exec-wrapper (`sudo git commit`), or past an
env-assignment prefix (`ALLOW_PUSH=1 git push`).

Known fail-open forms (a caller sees no invocation — degrading to *no gate*, never a false
positive): a command reached only through a git alias (``git ci``), wrapped in ``sh -c "…"`` /
``eval "…"``, run under a wrapper the ``WRAPPERS`` set does not list *with its own arguments*
(``timeout 60 git commit``), or an unknown leading word before `git` (``echo git commit`` is NOT
in command position). Resolving aliases and nested command strings is out of scope here.
"""

from __future__ import annotations

import os
import re
import shlex
from typing import NamedTuple

# git *global* options (before the subcommand) that consume a following value token.
GLOBAL_VALUE_OPTS = {"-c", "--git-dir", "--work-tree", "--namespace"}
ENV_ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")

# Exec-wrappers that run their argument as a command, so `git` right after one is still in command
# position (`sudo git commit`, `time git commit`). Bounded on purpose — an unknown leading word
# (`echo git commit`) is treated as NOT a command, preserving the phantom-commit guard.
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
}

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
    raise ValueError("unterminated command substitution")


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
    raise ValueError("unterminated backtick substitution")


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
        elif quote == '"' and ch == '"':
            quote = None
        out.append(ch)
        i += 1
    if quote is not None:
        raise ValueError("unbalanced quote")
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


def starts_command(tokens: list[str], idx: int) -> bool:
    """True if tokens[idx] is in *command position* — reachable from the input start or a control
    operator by stepping back over only leading `VAR=val` env assignments and known exec-wrappers
    (`sudo`/`time`/`env`/…). A bare word before it (e.g. `echo`) means it is that command's
    argument, so `echo VAR=1 git commit` is NOT mistaken for a commit, while `sudo git commit` and
    `ALLOW_GIT_WRITE=1 git commit` are. (Redirects are stripped globally before this runs, so a
    leading `2>&1 git commit` also resolves to command position.)"""
    j = idx - 1
    while j >= 0:
        prev = tokens[j]
        if is_op(prev):
            return True
        if ENV_ASSIGN.match(prev) or prev in WRAPPERS:
            j -= 1
            continue
        return False
    return True


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
) -> tuple[list[tuple[str | None, str | None, str, list[str]]], str | None]:
    """Walk ONE context in source order, recursing into the contexts it introduces.

    Args:
        ctx: The context to walk.
        base_cwd: Working directory in force when this context starts.
        max_depth: Maximum nesting depth before the input is treated as ambiguous.

    Returns:
        The invocations found — each ``(effective_dir, cdir, subcommand, arg_tokens)`` — and the
        working directory in force at the END of this context.

    Raises:
        ValueError: On unbalanced quotes, an unterminated context, or excessive nesting.
    """
    if ctx.depth > max_depth:
        raise ValueError("maximum command-context depth exceeded")

    outer, nested = _prepare(ctx.text, ctx.depth)
    tokens = strip_redirects(tokenize(newlines_to_separators(outer)))

    results: list[tuple[str | None, str | None, str, list[str]]] = []
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

        if tok in ("cd", "pushd") and starts_command(tokens, i):
            j = i + 1
            target = None
            while j < n and not is_op(tokens[j]):
                if not tokens[j].startswith("-"):
                    target = tokens[j]
                    break
                j += 1
            _descend(tokens[i : j + 1], cwd_state)
            cwd_state = _resolve_cd(cwd_state, target)
            i += 1
            continue

        # `popd` is not tracked as a stack: any popd makes the cwd unresolvable. This mirrors the
        # rule a consumer gate implements today, and porting it is MANDATORY — that gate's own cwd
        # walk is deleted once it consumes this primitive, so omitting it opens a new hole.
        if tok == "popd" and starts_command(tokens, i):
            cwd_state = None
            i += 1
            continue

        if is_git(tok) and starts_command(tokens, i):
            j = i + 1
            cdir = None
            while j < n and tokens[j].startswith("-"):
                opt = tokens[j]
                if opt == "-C" and j + 1 < n:
                    cdir = tokens[j + 1]
                    j += 2
                elif opt.startswith("-C") and len(opt) > 2:
                    cdir = opt[2:]
                    j += 1
                elif opt in GLOBAL_VALUE_OPTS and "=" not in opt:
                    j += 2
                else:
                    j += 1
            if j >= n:
                # Truncated invocation: no subcommand. Still descend into the global-option run
                # before giving up, or a context hidden there is lost.
                _descend(tokens[i:n], cwd_state)
                break
            seg: list[str] = []
            k = j + 1
            while (
                k < n
                and not is_op(tokens[k])
                and not (is_git(tokens[k]) and starts_command(tokens, k))
            ):
                seg.append(tokens[k])
                k += 1
            # THE SPAN RULE. Descend into every token this invocation consumes — its global-option
            # run AND its argument segment — not just the token in command position.
            # `git commit -m "$(git push origin dev)"` hides the push in the argument segment, and
            # the walk jumps `i = k` straight past it.
            _descend(tokens[i:k], cwd_state)
            results.append((cwd_state, cdir, tokens[j], seg))
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
    results, _ = _walk_context(CommandContext(command, 0), base_cwd, max_depth)
    return results


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

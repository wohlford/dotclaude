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
    # NOTE: shlex's default `commenters="#"` is deliberately LEFT IN PLACE here for now.
    #
    # The eventual design pairs `strip_comments` (which removes real comments) with
    # `lex.commenters = ""` (which makes any `#` surviving that pass an inert word rather than a
    # comment). The two are complementary and fail in OPPOSITE directions -- see `strip_comments`.
    #
    # But `commenters = ""` is only safe where `strip_comments` has ALREADY run, and no consumer
    # routes through it yet. Setting it now was measured to regress 3 of 6 ordinary commands,
    # including `ALLOW_PUSH=1 git push origin main  # publish the bricks`, while every gate suite
    # still passed. It must land in the same change that routes every consumer through
    # `strip_comments`, never before.
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


def iter_git_invocations(command: str) -> list[tuple[str | None, str, list[str]]]:
    """Find every `git` invocation in *command position*, in command order.

    ``cdir`` is that invocation's `-C <dir>` value (or None), ``subcommand`` is the token
    immediately following git's global options, and ``arg_tokens`` is the segment of tokens from
    just after the subcommand up to the next control operator or the next git invocation in
    command position. An invocation whose subcommand token is missing (the command ends mid
    global-options) is not included, matching how the scan stops today.

    Newlines join separate commands the way `;` does; normalized first so a newline-joined
    `git add -A\\ngit commit` splits into two segments. A `\\n` inside a quoted message is preserved
    as quoted content by shlex, so this is safe. Redirects are stripped from the whole stream so a
    leading `2>&1 git commit` is still seen and a redirect is never misread as a pathspec.

    Args:
        command: The raw shell-command string to scan.

    Returns:
        One ``(cdir, subcommand, arg_tokens)`` tuple per `git` invocation in command position, in
        command order — ``cdir`` is the invocation's `-C <dir>` value or None, ``subcommand`` is the
        token after git's global options, and ``arg_tokens`` is that invocation's argument segment.
        Empty list if none are found or on tokenizing ambiguity.

    Raises:
        Nothing — a ValueError from `tokenize` (unbalanced quotes) is swallowed to an empty list,
        never claiming an invocation exists on ambiguous input.
    """
    command = normalize_command(command)
    try:
        tokens = strip_redirects(tokenize(command))
    except ValueError:
        return []

    invocations: list[tuple[str | None, str, list[str]]] = []
    i, n = 0, len(tokens)
    while i < n:
        if not (is_git(tokens[i]) and starts_command(tokens, i)):
            i += 1
            continue
        # Skip global options to reach the subcommand, capturing `-C <dir>`.
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
            break
        sub = tokens[j]
        # Collect this invocation's args until a control operator or the next `git` invocation in
        # command position.
        seg = []
        k = j + 1
        while (
            k < n
            and not is_op(tokens[k])
            and not (is_git(tokens[k]) and starts_command(tokens, k))
        ):
            seg.append(tokens[k])
            k += 1
        invocations.append((cdir, sub, seg))
        i = k
    return invocations

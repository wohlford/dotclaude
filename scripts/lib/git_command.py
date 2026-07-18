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


def tokenize(command: str) -> list[str]:
    """shlex with punctuation_chars: control/redirect operators become their own tokens even when
    fused to a word (`-A&&git`), while quoted values stay intact. Raises ValueError on unbalanced
    quotes (caller fails open)."""
    lex = shlex.shlex(command, posix=True, punctuation_chars="();<>|&")
    lex.whitespace_split = True
    return list(lex)


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
    """Return one (cdir, subcommand, arg_tokens) tuple per `git` invocation in *command position*,
    in command order (empty list if none or on tokenizing ambiguity).

    ``cdir`` is that invocation's `-C <dir>` value (or None), ``subcommand`` is the token
    immediately following git's global options, and ``arg_tokens`` is the segment of tokens from
    just after the subcommand up to the next control operator or the next git invocation in
    command position. An invocation whose subcommand token is missing (the command ends mid
    global-options) is not included, matching how the scan stops today.

    Newlines join separate commands the way `;` does; normalized first so a newline-joined
    `git add -A\\ngit commit` splits into two segments. A `\\n` inside a quoted message is preserved
    as quoted content by shlex, so this is safe. Redirects are stripped from the whole stream so a
    leading `2>&1 git commit` is still seen and a redirect is never misread as a pathspec. Raises
    nothing — a ValueError from `tokenize` (unbalanced quotes) fails to an empty list, never
    claiming an invocation exists on ambiguous input."""
    command = command.replace("\n", " ; ").replace("\r", " ")
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

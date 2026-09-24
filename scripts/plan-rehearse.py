#!/usr/bin/env python3
# Script: plan-rehearse.py
# Purpose: Run an implementation plan's verification checks in an isolated clone and report what ran
# Usage: plan-rehearse.py --plan <f> --clone <d> [--create --scope <repo> --branch <b>] [--base <b>]
"""Rehearse an implementation plan's checks in a scratch clone, before any implementer sees it.

**Why this exists.** `/feature` step 5 requires that every check in a plan be *run* rather than
reasoned about, because a check written from a model of the finished tree can be unsatisfiable in
ways prose review cannot see. That rehearsal was hand-rolled once per full-lane run. This tool
mechanizes the running.

**What it does NOT do, and cannot.** It never applies an edit. Measured over the 90 plans in
`plans/`: 107 `- Modify:` entries against 32 `- Create:` entries. The state most checks need is
described in prose rather than supplied as content, so a tool that
"replays the plan" would replay the minority and silently mis-grade the rest. What it does instead
is DETECT whether an edit was applied — the paths are extractable even when the edits are not —
and refuse to present a verdict for a check whose prerequisites are still identical to base.

**The word PASS does not appear in this tool's vocabulary, deliberately.** Every `Expected:` in a
plan is prose, and a mechanical comparison fails in both directions: within one plan,
`2026-07-04-exec-bit-guard.md:112` expects exit 0 while `:294` expects exit 127, so an rc-based
verdict scores every RED-phase row backwards, and `:115` expects "ONLY the 5 mode changes", which
no matcher decides. This tool reports each check's command, its exit status and its output beside
the plan's own quoted expectation. **The reader does the comparing** — which is what step 5 already
assigns to the model. A `RESULT: PASS` line would be quoted as "the plan's checks pass", an
assertion nothing mechanical can support.

**Anything unrehearsed is INDETERMINATE, never quietly omitted.** An `Expected:` whose command
could not be extracted is an UNPAIRED row naming its file and line; a command that would escape the
clone is REFUSED with its reason; a check whose prerequisites never moved is TAINTED. All four
counts sit in the terminal line, not only in the body, because a reader skims the terminal line.

**Extraction fails loud, and prefers a miss to a guess.** The shape list here was measured, not
imagined, and an earlier hand-made one was wrong: a `^Expected:` scan misses the inline
`Run: `x` -> Expected: y` form entirely. Widening has the opposite hazard — a step heading whose
backticked span is a *description* rather than a command would be run, producing a confident answer
about something the plan never asked. So a bare-backtick shape pairs only when it is the sole
backticked span on its line and that line is not a step heading; everything else is UNPAIRED.

Exit codes: 0 only when the clone is EDITED, the plan yielded at least one check, and every one
of them ran with nothing tainted, refused, unpaired or unrun; 1 the rehearsal was incomplete, which
is the normal outcome mid-plan and on an unedited tree; 2 a usage or precondition failure.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple

# Every shape `_pair` and `extract` can assign. Membership is ASSERTED when a Check is built, so
# this cannot drift into an inert list: adding a shape without naming it here fails immediately,
# and the suite requires every member to carry a corpus excerpt.
SHAPES = frozenset(
    {
        "run-line",
        "inline-run-arrow",
        "fence",
        "fence-unknown-language",
        "bare-backtick",
        "trailing-backtick",
        "unpaired",
    }
)

SHELL_LANGS = frozenset({"bash", "sh", "console", "shell"})
PYTHON_LANGS = frozenset({"python", "python3", "py"})
DEFAULT_TIMEOUT = 120
OUTPUT_EXCERPT_LINES = 12

TASK_RE = re.compile(r"^#{2,4}\s+Task\s+(\d+)\b", re.I)
FILES_RE = re.compile(r"^\*\*Files:\*\*")
FILE_ENTRY_RE = re.compile(r"^-\s+(Create|Modify|Delete):\s*`?([^\s`]+)`?")
EXPECTED_ANCHORED_RE = re.compile(r"^Expected:\s*(.*)$")
EXPECTED_INLINE_RE = re.compile(
    r"^Run\b[^:]*:\s*`(.+?)`\s*(?:->|→)\s*Expected:\s*(.*)$"
)
RUN_LINE_RE = re.compile(r"^Run\b[^:]*:\s*(.+?)\s*$")
BACKTICK_ONLY_RE = re.compile(r"^\s*(?:[-*]\s*)?`([^`]{4,})`\s*[.;]?\s*$")
STEP_HEADING_RE = re.compile(r"^\s*(?:-\s*\[[ x]\]\s*)?\*\*")
# The tail after a heading's closing ** is the command only when it IS the span, optionally
# behind a dash. With prose in front, the span belongs to the sentence: measured,
# "**Step 6: Verify** the summary line must read `0 failed`" yielded the command `0 failed` —
# the EXPECTATION executed as a command.
TRAILING_CMD_RE = re.compile(r"^\s*[\u2014\u2013-]?\s*`([^`]{4,})`\s*[.;]?\s*$")

# Variables whose value cannot move a command outside the clone. `$PWD` IS the clone, because
# every check runs with cwd=clone; the positional and status forms are not paths at all.
SAFE_VARS = frozenset(
    {"PWD", "?", "!", "$", "#", "@", "*", "0", "1", "2", "3", "4", "5"}
)
VAR = re.compile(r"\$\{?([A-Za-z_]\w*|[?!$#@*0-9])\}?")
ASSIGNMENT = re.compile(r"^([A-Za-z_]\w*)=(.*)$", re.S)
# Constructs the shell expands into something this gate cannot see. These are OPAQUE in ANY
# position: a QUOTED substitution survives tokenization as a SINGLE token, so
# `echo "$(curl …)"` reached no command position at all while the unquoted form did.
# Measured: 18 of the 213 corpus checks carry a quoted `$( )`, so this is the idiom rather
# than a corner case, and the docstring previously told authors to prefer it.
OPAQUE_EXPANSION = re.compile(r"""\$[('\"]""")
TILDE = re.compile(r"(?<![\w/])~")

# /dev/null and its siblings are sinks, not escapes.
SINKS = frozenset({"/dev/null", "/dev/stdout", "/dev/stderr"})

# git subcommands that write outside the clone or reach the network.
WRITING_SUBCOMMANDS = frozenset(
    {
        "push",
        "tag",
        "commit",
        "fetch",
        "clone",
        "pull",
        "remote",
        "submodule",
        "am",
        "send-email",
    }
)

# Non-git commands refused by NAME, judged only in COMMAND POSITION — see `_command_words`.
NETWORK_COMMANDS = frozenset(
    {"curl", "wget", "ssh", "scp", "rsync", "nc", "sftp", "telnet"}
)
INSTALLERS = frozenset({"pip", "pip3", "uv", "npm", "pnpm", "yarn", "gem", "cargo"})
INSTALL_VERBS = frozenset({"install", "add", "sync", "update", "upgrade"})
# A command whose argument is itself a command string cannot be read statically at all.
# A shell only takes an opaque COMMAND STRING with -c; `bash script.sh` runs a file this gate
# can see. Refusing the file form over-blocked an ordinary in-clone check.
SHELLS = frozenset({"sh", "bash", "zsh", "dash", "ksh"})
# These always take a command string or build one, so their argument cannot be read statically.
OPAQUE_RUNNERS = frozenset({"xargs", "eval"})
DIR_CHANGERS = frozenset({"cd", "pushd", "popd"})
# Options whose VALUE relocates the tool, whether or not it contains a slash.
DIR_OPTIONS = frozenset({"-C", "--git-dir", "--work-tree", "--namespace"})

# Tokens `git_command.tokenize` emits for control/redirect operators. A token in command position
# is the first word, or the first word after one of these.
OPERATORS = frozenset({";", "&&", "||", "|", "&", "(", ")", "<", ">", ">>", "<<", "\n"})


class Check(NamedTuple):
    """One `Expected:` in a plan, with the command it was paired to."""

    line: int
    task: int | None
    shape: str
    command: str | None
    lang: str
    expectation: str


def make_check(
    line: int,
    task: int | None,
    shape: str,
    command: str | None,
    lang: str,
    expectation: str,
) -> Check:
    """Build a Check, refusing a shape outside the declared vocabulary."""
    if shape not in SHAPES:
        raise ValueError(
            f"unknown check shape {shape!r}; add it to SHAPES and give it a fixture"
        )
    return Check(line, task, shape, command, lang, expectation)


class Task(NamedTuple):
    """A plan task's declared file set, and whether that declaration parsed."""

    index: int
    line: int
    paths: tuple[str, ...]
    parsed: bool


class Row(NamedTuple):
    """The outcome of one check."""

    check: Check
    state: str
    reason: str
    rc: int | None
    output: str
    # Carried EXPLICITLY, never inferred from the reason text. Inferring it was tried and was
    # wrong: the first attempt looked for a `"; "` separator, and a state's own explanation
    # contains one ("…cannot be measured; re-running will not change that"), so every such row
    # counted as a finding — the exact thing the check existed to prevent.
    finding: bool = False


def _read(path: Path) -> str:
    """Read a UTF-8 text file, preserving its newlines."""
    return path.read_text(encoding="utf-8")


def _git(clone: Path, *args: str) -> subprocess.CompletedProcess:
    """Run a git command inside the clone and capture its output."""
    return subprocess.run(
        ["git", "-C", str(clone), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def parse_tasks(lines: list[str]) -> list[Task]:
    """Split a plan into tasks and collect each one's declared file paths.

    A task whose `**Files:**` block is present but yields no parsable entry is marked
    ``parsed=False``. That distinction is load-bearing: discovery cannot detect absence, so an
    unparsed task must taint every later check rather than read as "this task touches nothing".
    """
    tasks: list[Task] = []
    current: int | None = None
    start = 0
    paths: list[str] = []
    saw_files_block = False
    saw_entry = False

    def flush() -> None:
        if current is None:
            return
        tasks.append(
            Task(current, start, tuple(paths), not saw_files_block or saw_entry)
        )

    for i, line in enumerate(lines):
        m = TASK_RE.match(line)
        if m:
            flush()
            current = int(m.group(1))
            start = i + 1
            paths = []
            saw_files_block = False
            saw_entry = False
            continue
        if current is None:
            continue
        if FILES_RE.match(line):
            saw_files_block = True
            continue
        entry = FILE_ENTRY_RE.match(line)
        if entry:
            saw_entry = True
            paths.append(entry.group(2))
    flush()
    return tasks


def _task_for_line(tasks: list[Task], line: int) -> int | None:
    """Return the index of the task containing a 1-based line, or None."""
    found = None
    for t in tasks:
        if t.line <= line:
            found = t.index
        else:
            break
    return found


def _fence_language(lines: list[str], closing: int) -> tuple[str, str | None]:
    """Given the index of a closing fence, return its language and body."""
    k = closing - 1
    while k >= 0 and not lines[k].startswith("```"):
        k -= 1
    if k < 0:
        return "", None
    lang = lines[k][3:].strip().lower() or "(bare)"
    return lang, "\n".join(lines[k + 1 : closing])


def _pair(lines: list[str], i: int) -> tuple[str, str | None, str]:
    """Pair the `Expected:` at index i with the command that precedes it.

    Returns ``(shape, command, lang)``; ``command`` is None when nothing pairs, which is
    reported as UNPAIRED rather than guessed at.
    """
    j = i - 1
    while j >= 0 and not lines[j].strip():
        j -= 1
    if j < 0:
        return "unpaired", None, ""
    prev = lines[j]

    run = RUN_LINE_RE.match(prev)
    if run:
        rest = run.group(1).strip()
        # Take the FIRST backticked span, never the whole remainder: a Run: line routinely
        # carries a trailing aside ("Run: `stat -c %a x` (GNU `stat` is first on PATH here)"),
        # and swallowing it would execute prose. Measured on a real plan.
        span = re.search(r"`([^`]+)`", rest)
        # No backticked span means no command was marked up, and the remainder is prose. Taking
        # it would run the sentence: `Run the audit and compare: the summary against base`
        # yielded the command `the summary against base`.
        if not span:
            return "unpaired", None, ""
        return "run-line", span.group(1), "bash"

    if prev.startswith("```"):
        lang, body = _fence_language(lines, j)
        if body is None:
            return "unpaired", None, ""
        if lang in SHELL_LANGS:
            return "fence", body, "bash"
        # A BARE fence declares nothing, and a plan is as likely to show expected OUTPUT in one as
        # a command — measured, a ``` block holding `0 failed` was extracted as a command to run.
        if lang == "(bare)":
            return "fence-unknown-language", None, lang
        if lang in PYTHON_LANGS:
            return "fence", body, "python"
        return "fence-unknown-language", None, lang

    # A backticked span on a STEP HEADING pairs only when it sits AFTER the closing `**`.
    # Measured, one plan, four lines apart: "**Step 3: Re-run the probe with `git -C ... ` added**"
    # describes a config to add, while "**Step 4: Run the suite** — `bash .../test.sh`" IS the
    # command. Inside the bold span is prose; outside it is the command.
    if STEP_HEADING_RE.match(prev):
        # Two markers are needed before one of them can be a CLOSING marker. A line carrying only
        # ONE — `- [ ] **`bash scripts/tests/test_x.sh`` — would otherwise have `rfind` return that
        # OPENER, so the command text after it would pair as though the heading had closed.
        #
        # This comment previously cited a DIFFERENT example, one with two markers, which this
        # guard therefore never sees; that case is decided by TRAILING_CMD_RE's anchoring, and is
        # attributed correctly where that pattern is defined. One example cannot justify two
        # mechanisms, and the misattribution is why no row exercised this branch.
        if prev.count("**") < 2:
            return "unpaired", None, ""
        closing = prev.rfind("**")
        tail = prev[closing + 2 :] if closing != -1 else ""
        trailing = TRAILING_CMD_RE.match(tail)
        if trailing:
            return "trailing-backtick", trailing.group(1), "bash"
        return "unpaired", None, ""

    # A bare backticked line pairs only when the WHOLE line is that one span. The pattern anchors
    # both ends and its body excludes backticks, so a line carrying a second span cannot match —
    # a `prev.count("`") == 2` guard stood here and was DELETED as inert once mutation showed no
    # input could make it change an outcome. The anchoring is what does the work.
    bare = BACKTICK_ONLY_RE.match(prev)
    if bare:
        return "bare-backtick", bare.group(1), "bash"

    return "unpaired", None, ""


def extract(text: str) -> tuple[list[Check], list[Task]]:
    """Extract every `Expected:` in a plan, paired to its command where one is findable."""
    lines = text.splitlines()
    tasks = parse_tasks(lines)
    checks: list[Check] = []
    for i, line in enumerate(lines):
        inline = EXPECTED_INLINE_RE.match(line)
        if inline:
            checks.append(
                make_check(
                    i + 1,
                    _task_for_line(tasks, i + 1),
                    "inline-run-arrow",
                    inline.group(1),
                    "bash",
                    inline.group(2).strip(),
                )
            )
            continue
        anchored = EXPECTED_ANCHORED_RE.match(line)
        if not anchored:
            continue
        shape, command, lang = _pair(lines, i)
        checks.append(
            make_check(
                i + 1,
                _task_for_line(tasks, i + 1),
                shape,
                command,
                lang,
                anchored.group(1).strip(),
            )
        )
    return checks, tasks


_GC = None


def _gc():
    """The repo's shared shell tokenizer, imported the way every other consumer imports it.

    Deliberately lazy (as `git-timing-guard.py` is) and deliberately a PLAIN import on its own
    line. An `importlib` load worked, but `publication-push-guard-test.sh` discovers the
    tokenizer's dependents by matching import statements, so the importlib form registered as an
    unclassifiable MENTION and its CONTROL row — "every real importer of the tokenizer is wired" —
    failed. That guard exists so a change to the tokenizer re-runs every dependent's suite; being
    invisible to it means this tool's suite would NOT run when the thing it is built on changes.
    """
    global _GC
    if _GC is None:
        sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
        import git_command  # noqa: E402

        _GC = git_command
    return _GC


def _command_words(tokens: list[str]) -> list[int]:
    """Indices of tokens sitting in COMMAND POSITION.

    Reusing `git_command`'s own `ENV_ASSIGN` and `WRAPPERS` rather than restating them: an
    env-assignment prefix (`FOO=1 cmd`) and a transparent wrapper (`sudo cmd`, `time cmd`) both
    leave the next word still in command position, and both were holes when this was a regex over
    raw text.
    """
    gc = _gc()
    positions: list[int] = []
    at_start = True
    for i, token in enumerate(tokens):
        # A RESERVED WORD (`if`, `then`, `do`, `while`, `{`, `!`) ends the previous command just
        # as `;` does, and gc.is_op/gc.is_redirect know spellings a hand-written set does not
        # (`|&`, `;;`, `>&`, `&>`). Restating any of this by hand restates it one hardening short:
        # measured, 8 of 10 name-based refusals leaked behind a single `if true; then ... fi`,
        # while a git write in the IDENTICAL position was still refused — because THAT path runs
        # through gc.iter_git_invocations_with_cwd, which consults gc.RESERVED_WORDS. One
        # function, two readings of command position, and only the hand-rolled one was wrong.
        if (
            token in OPERATORS
            or token in gc.RESERVED_WORDS
            or gc.is_op(token)
            or gc.is_redirect(token)
        ):
            at_start = True
            continue
        if not at_start:
            continue
        if gc.ENV_ASSIGN.match(token):
            continue  # an assignment prefix leaves the next word in command position
        if token in gc.WRAPPERS or token in gc.GIT_ONLY_WRAPPERS:
            # The WRAPPER ITSELF is a command word and must be judged — `xargs` is in this set,
            # so skipping silently past it landed on its `-I{}` option and let the wrapped
            # command through entirely. Judge it, then stay in command position for what follows.
            positions.append(i)
            continue
        positions.append(i)
        at_start = False
    return positions


def _takes_command_string(tokens: list[str], shell_at: int) -> bool:
    """True when a shell invocation carries `-c`, in any of its spellings.

    Matching a literal `-c` inside a fixed three-token window missed both directions: a short
    option CLUSTER (`sh -euc`, `bash -lc` — the latter is what the deleted resolution probe itself
    used) and a window overrun (`bash -o pipefail -e -u -c`). Scan forward to the first
    non-option token instead, and accept any cluster ending in `c`.
    """
    skip_value = False
    for token in tokens[shell_at + 1 :]:
        if skip_value:
            # `-o` and `+o` consume the FOLLOWING word (`-o pipefail`), which does not start with
            # a dash — so a scan that stops at the first non-option token stopped there and let
            # `bash -o pipefail -e -u -c '…'` through.
            skip_value = False
            continue
        if token in ("-o", "+o"):
            skip_value = True
            continue
        if not token.startswith("-") or token == "-":
            return False
        if token == "--":
            return False
        if token.startswith("--"):
            continue
        if token.endswith("c"):
            return True
    return False


def escape_reason(command: str, clone: Path, lang: str = "bash") -> str | None:
    """Why this command cannot be vouched for as contained, or None if it can.

    **CONSERVATIVE, PARTIAL, AND NOT A SANDBOX.** It refuses what it cannot read statically and is
    an advisory that turns an escaping check into a visible REFUSED row. `/feature` step 5 requires
    the operator read every check independently, including the ones this runs.

    **Tokenization is delegated to `scripts/lib/git_command.py`, not hand-rolled.** That module is
    the repo's shared shell tokenizer, hardened across four published fixes for exactly the
    problems a regex over raw text keeps getting wrong. Three review rounds of this gate leaked in
    the same place before it was rebuilt on that tokenizer, and the last round's "repair" turned
    six refusals into executions: a newline is a command separator, so a multi-line fence body —
    which is how a third of this repo's plan checks are written — hid everything after its first
    line from a start-of-text anchor. Matching TOKENS in COMMAND POSITION removes that whole class
    rather than answering it one shape at a time.

    A command this tokenizer cannot parse is REFUSED, never allowed: an unreadable command is the
    one case where failing open would be indistinguishable from judging it safe.

    **Scope, stated once and not extended.** It refuses what it can read STATICALLY: a word in
    command position that writes or reaches the network, and a path, redirect, `cd` or `-C` target
    that resolves outside the clone. **It does not follow a value through an indirection** — a
    command name reached through a variable, a command behind a wrapper carrying its own arguments
    (`timeout 5 …`, `nice -n 19 …`), or anything a `python` fence computes rather than quotes.
    Those are the shared tokenizer's accepted limits, already recorded as such by three other
    scripts that depend on it; they are not a defect list to work through, and a private wrapper
    set here would put two readings of command position back into one repo.

    **THERE IS NO SANDBOX, and this paragraph will not pretend otherwise.** The clone is disposable
    and has no remote, and `create_clone` installs a deny-all `pre-push` hook — which stops an
    ACCIDENTAL push and nothing more. It does not contain a check that is actively routing around
    it: `--no-verify` and `-c core.hooksPath=…` are the check's own to pass, and both were measured
    landing a ref past that hook. Nor do this repo's guards help — they are PreToolUse hooks on the
    OPERATOR's shell and never see a child this tool spawns, also measured.

    Two earlier drafts of this paragraph each named a backstop that did not exist, and each was
    written to justify accepting the wrapper limit conceded above. The limit is real and the
    backstop is not, so the honest statement is the one `/feature` step 5 already made before this
    tool existed: **read every check before you run it, including the ones this ran.**
    """
    gc = _gc()
    root = clone.resolve()

    if lang == "python":
        # A PYTHON body is not shell, and reading it as shell produced confident nonsense: three
        # of the four python-fence checks in this repo's plans were refused for "contains backtick
        # substitution … use $( )", where the backtick sat inside a Python STRING LITERAL and the
        # remediation is meaningless. Shell SYNTAX rules do not apply; what still applies is the
        # PATH question, which is language-independent — an absolute path outside the clone means
        # the same thing in either. So this judges paths only, and says plainly that it did.
        for token in re.findall(r"""[\'\"]([^\'\"]*/[^\'\"]*)[\'\"]""", command):
            if token in SINKS:
                continue
            try:
                landed = (
                    Path(token).resolve()
                    if token.startswith("/")
                    else (root / token).resolve()
                )
            except (OSError, ValueError, RuntimeError):
                return f"python body names a path that cannot be resolved: {token}"
            if not (landed == root or root in landed.parents):
                return f"python body names a path outside the clone: {token}"
        return None

    def outside(token: str) -> bool:
        """True when a token cannot be vouched for as landing inside the clone.

        An UNRESOLVED expansion counts as outside. Without this, a value reached through one hop
        survived into `Path.resolve()` as a literal directory component and absorbed exactly one
        level: `cd $PWD/..` was refused while `ROOT=$PWD; cd $ROOT/..` was allowed. That is the
        `$PWD` defect this function already records as closed, re-opened one name away — the same
        shape twice is the signal that the rule, not the spelling, is what to state.
        """
        token = expand_safe(token)
        if "$" in token:
            return True
        try:
            landed = (
                (root / token).resolve()
                if not token.startswith("/")
                else Path(token).resolve()
            )
        except (OSError, ValueError, RuntimeError):
            return True
        return not (landed == root or root in landed.parents)

    def expand_safe(token: str) -> str:
        """Substitute the variables whose value IS known, so the rest can be resolved.

        `$PWD` is the clone — but leaving the literal text in place made it count as a directory
        COMPONENT, so `$PWD/..` resolved to `<clone>/$PWD/..`, back inside the clone. One absorbed
        level is exactly one `..` laundered, and `rm -rf $PWD/..` — which deletes the clone's
        PARENT — was ALLOWED.
        """
        return re.sub(r"\$\{?PWD\}?", ".", token)

    def path_like(token: str) -> bool:
        token = expand_safe(token)
        return "/" in token or token == ".." or token.startswith("..")

    # BACKTICK substitution is refused outright, and this is a STATED LIMITATION rather than a
    # policy: the tokenizer splits `$( )` into tokens — so a command inside one reaches command
    # position and is judged — but it does not split backticks, measured:
    # "echo `curl http://…`" yielded the command words ['echo'] alone. Refusing is the only honest
    # answer for a construct this gate cannot see into. Use $( ) in a plan check instead.
    if "`" in command:
        return (
            "contains backtick substitution, which this gate cannot read into; use $( )"
        )

    # Newlines become separators BEFORE anything else, so a fence body's later lines are judged.
    try:
        text = gc.newlines_to_separators(
            gc.strip_comments(gc.mask_heredoc_quotes(command))
        )
        tokens = gc.tokenize(text)
    except (ValueError, RecursionError) as exc:
        return f"cannot be parsed, so cannot be vouched for: {exc}"

    # Every git invocation in command position, with its own -C and the cwd in force there.
    try:
        invocations = gc.iter_git_invocations_with_cwd(text, str(root))
    except (ValueError, RecursionError) as exc:
        return f"git invocation could not be resolved: {exc}"
    # The walk's `cdir` was also checked here and is genuinely REDUNDANT — every input it refused
    # is refused by the directory-option token scan below. `effective_dir` is NOT: it was deleted
    # on a probe of twelve commands that found no distinguishing input, and a reviewer then found
    # six. The probe contained nothing that makes a `cd` target unresolvable, which is the only
    # thing this branch decides — an inertness probe is only as strong as its inputs, and mine
    # were drawn from the cases I had already thought of. Restoring it costs NOTHING on the real
    # corpus (51/213 refused either way) and closes `cd $(cat somefile) && git status`, where the
    # working directory comes from file content and could be anywhere.
    for effective_dir, _cdir, subcommand, _args in invocations:
        if subcommand in WRITING_SUBCOMMANDS:
            return f"runs `git {subcommand}`, which writes or reaches the network"
        if effective_dir is None:
            return "runs git in a directory this gate cannot resolve statically"

    positions = set(_command_words(tokens))
    # Names assigned in THIS text, whose assigned value was itself judged contained. A later
    # `$name` is then resolvable after all. Collected in a first pass so order does not matter.
    locally_assigned: set[str] = set()
    for token in tokens:
        assignment = ASSIGNMENT.match(token)
        if assignment:
            value = assignment.group(2)
            # A name is recorded only when its VALUE is fully resolvable. A value carrying any
            # `$` is not: the tokenizer SPLITS a substitution, so `X=$(cat f)` arrives here as the
            # token `X=$` with the value `$`, and recording X then let `cat $X` through while the
            # real value came from file content and could be any path. Every unsafe assignment is
            # still refused at its own token below; this only decides whether later USES of the
            # name are resolvable.
            value = assignment.group(2)
            if "$" not in value or not VAR.sub("", value).count("$"):
                locally_assigned.add(assignment.group(1))
    for i, token in enumerate(tokens):
        assignment = ASSIGNMENT.match(token)
        if assignment:
            # The value is judged for EXPANSIONS as well as paths. Judging only the path half
            # left the aliased form open: `X=$HOME; cat $X/.ssh/id_rsa` was ALLOWED, because
            # `$HOME` reached this branch as an assignment VALUE and the branch then `continue`d
            # past the token's own `~`/variable checks. An alias is the same hole one hop away.
            # Judge the VALUE, not just the name. Suppressing `$X` because X was assigned here,
            # without looking at what it was assigned, laundered `X=/etc/passwd; cat $X`.
            value = assignment.group(2)
            if TILDE.search(value) or (path_like(value) and outside(value)):
                return f"assigns a path outside the clone: {token}"
            unknown = [n for n in VAR.findall(value) if n not in SAFE_VARS]
            if unknown:
                return f"assigns ${unknown[0]}, whose value this gate cannot resolve: {token}"
            if re.search(r"\$(?=\S)", VAR.sub("", value)):
                return f"assigns an expansion this gate cannot read: {token}"
            continue
        if i in positions:
            word = token.rsplit("/", 1)[-1]
            if word in NETWORK_COMMANDS:
                return f"runs `{word}`, which reaches the network"
            if word in OPAQUE_RUNNERS:
                return f"runs `{word}`, whose argument is a command string this gate cannot read"
            if word in SHELLS and _takes_command_string(tokens, i):
                return f"runs `{word}` with a command string this gate cannot read"
            if word in INSTALLERS and any(
                a in INSTALL_VERBS for a in tokens[i + 1 : i + 3]
            ):
                return f"runs `{word}`, which installs packages"
            if word in DIR_CHANGERS:
                target = tokens[i + 1] if i + 1 < len(tokens) else ""
                if not target or target in OPERATORS:
                    return f"bare `{word}` goes to the home directory"
                if TILDE.search(target) or outside(target):
                    return f"`{word}` target outside the clone: {target}"
        if token in DIR_OPTIONS and i + 1 < len(tokens):
            # Restored after being deleted as "inert" on a probe set with no SYMLINK target: a
            # symlink name carries no slash, so the path scan cannot see it.
            if outside(tokens[i + 1]):
                return f"{token} target outside the clone: {tokens[i + 1]}"
        option = token.split("=", 1)
        if len(option) == 2 and option[0] in DIR_OPTIONS and outside(option[1]):
            return f"{option[0]} target outside the clone: {option[1]}"
        # Any `$` still standing once the KNOWN variables are removed is an expansion this
        # gate cannot read — `$(`, `${x:-…}`, or a `$'…'` whose quotes shlex has already
        # stripped, leaving the bare token `$/etc/passwd`. Matching the spellings one by
        # one missed that last shape; matching the RESIDUAL closes the class.
        # A TRAILING `$` is not an expansion — the shell expands nothing at end of word, and
        # it is how a regex anchor reaches this gate: `grep -c 'byte diff);$'` is a real
        # corpus check and the only false positive this rule produced. Require a following
        # character.
        if re.search(r"\$(?=\S)", VAR.sub("", token)):
            return f"contains an expansion this gate cannot read: {token}"
        if TILDE.search(token):
            return (
                "contains ~, which the shell expands to a path this gate cannot resolve"
            )
        for name in VAR.findall(token):
            if name not in SAFE_VARS and name not in locally_assigned:
                return f"contains ${name}, whose value this gate cannot resolve"
        # Strip a leading `--opt=` before judging a token as a path, so `--file=/tmp/x` is seen.
        candidate = (
            token.split("=", 1)[1]
            if ("=" in token and token.startswith("-"))
            else token
        )
        if candidate in SINKS:
            continue
        if path_like(candidate) and outside(candidate):
            return f"path outside the clone: {candidate}"
    return None


class TreeState(NamedTuple):
    """What the clone's working tree looks like relative to base."""

    diffstat: str
    porcelain: str
    edited: bool
    digest: str


def tree_state(clone: Path, base: str) -> TreeState:
    """Read the clone's state from BOTH the diff and the index.

    `git diff` does not show an untracked file — measured: a new file returns empty from
    `diff --stat HEAD` while `git status --porcelain -uall` reports `?? path`. The defect that
    motivated this tool was an untracked file, so a diffstat-only reading would hide exactly the
    case it exists for.
    """
    diffstat = _git(clone, "diff", "--stat", base).stdout.strip()
    porcelain = _git(clone, "status", "--porcelain", "-uall").stdout.strip()
    digest = hashlib.sha256(porcelain.encode("utf-8")).hexdigest()[:12]
    return TreeState(diffstat, porcelain, bool(diffstat or porcelain), digest)


def unmoved_prerequisites(
    clone: Path, base: str, tasks: list[Task], upto: int | None
) -> tuple[list[str], list[int]]:
    """Paths an earlier task declares that are still identical to base.

    A task whose file declaration did not parse contributes the sentinel `<task N: Files: unparsed>`
    rather than nothing, so an unparsed task can never come out cleaner than a parsed one.
    """
    if upto is None:
        return [], []
    stale: list[str] = []
    unreadable: list[int] = []
    for t in tasks:
        if t.index > upto:
            break
        if not t.parsed:
            # NOT a taint reading. The declaration could not be read, so nothing about the tree is
            # being asserted — and crucially, no edit can clear it. Measured: 77 of 305 tasks in
            # this repo's plans declare their files in prose ("- Test: a live subagent probe",
            # "- Modify: sync tables (regenerated)"), which makes 50 of 223 checks unclearable.
            # Reported under its own word so the row does not read as "you have not applied the
            # edits yet" and send the operator to redo work that already landed. Widening the
            # parser was measured and rejected: it recovers 77 -> 70 and the remainder is prose.
            unreadable.append(t.index)
            continue
        for path in t.paths:
            # Two readings, because neither alone is sufficient: `git diff` is blind to a file
            # that does not exist at base, and the index is blind to a modification in place.
            # `git diff --quiet` answers 0 (same) or 1 (differs). ANYTHING else is git
            # declining to answer — 128 for a bad revision above all — and reading that as
            # "differs" is what silently emptied `stale` for every check, so nothing was ever
            # TAINTED and the run reported its only clean verdict. An unanswerable comparison
            # taints; it never clears.
            rc = _git(clone, "diff", "--quiet", base, "--", path).returncode
            if rc not in (0, 1):
                stale.append(
                    f"<{path}: base {base!r} could not be compared (git rc={rc})>"
                )
                continue
            if rc == 0 and not _is_untracked(clone, path):
                stale.append(path)
    return stale, unreadable


def _is_untracked(clone: Path, path: str) -> bool:
    """True when the path shows as untracked in the clone's index."""
    out = _git(clone, "status", "--porcelain", "-uall", "--", path).stdout
    return any(line.startswith("??") for line in out.splitlines())


def run_check(check: Check, clone: Path, timeout: int) -> tuple[int | None, str]:
    """Run one check inside the clone, bounding it so an overrun costs attention, not a score."""
    if check.lang == "python":
        argv = [sys.executable, "-c", check.command or ""]
    else:
        argv = ["bash", "--noprofile", "--norc", "-c", check.command or ""]
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            cwd=clone,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return None, f"(no verdict: exceeded the {timeout}s bound)"
    return proc.returncode, (proc.stdout + proc.stderr).rstrip()


def _literal_expectation(expectation: str) -> str | None:
    """Return a bare literal the expectation quotes, when it quotes exactly one."""
    spans = re.findall(r"`([^`]+)`", expectation)
    if len(spans) == 1 and len(spans[0]) <= 24 and "\n" not in spans[0]:
        return spans[0]
    return None


def rehearse(
    plan: Path, clone: Path, base: str, timeout: int, dry: bool
) -> tuple[list[Row], TreeState, list[Task]]:
    """Extract, pre-flight and run every check, returning one row each."""
    checks, tasks = extract(_read(plan))
    state = tree_state(clone, base)
    rows: list[Row] = []
    for check in checks:
        if check.shape == "unpaired":
            rows.append(Row(check, "UNPAIRED", "no command precedes it", None, ""))
            continue
        if check.shape == "fence-unknown-language":
            rows.append(
                Row(
                    check,
                    "UNRUN",
                    f"fence language {check.lang!r} is not a shell",
                    None,
                    "",
                )
            )
            continue
        assert check.command is not None
        escaped = escape_reason(check.command, clone, check.lang)
        if escaped:
            rows.append(Row(check, "REFUSED", escaped, None, ""))
            continue
        stale, unreadable = unmoved_prerequisites(clone, base, tasks, check.task)
        if dry:
            rows.append(Row(check, "UNRUN", "--list: nothing was executed", None, ""))
            continue
        rc, output = run_check(check, clone, timeout)
        reason = ""
        if rc is None:
            rows.append(Row(check, "UNRUN", reason or "timed out", None, output))
            continue
        literal = _literal_expectation(check.expectation)
        if literal and rc != 0:
            reason = f"{reason + '; ' if reason else ''}expects the literal {literal!r} but exited {rc}"
        # A TAINTED check is still RUN, and its output still reported. Skipping it threw away the
        # whole base-tree reading: two of step 5's unsatisfiable shapes — an instrument that cannot
        # answer the question at all, and a check the untouched tree already satisfies — are tree
        # INDEPENDENT and surface on a fresh clone with no operator input. Measured by dogfooding:
        # on a real plan at base every check was tainted, so short-circuiting left the tool inert
        # until edits landed. The row still says TAINTED, so nothing reads as rehearsed; the rc and
        # the output are evidence either way.
        if unreadable and not stale:
            # Its own state and its own word. UNREADABLE is never cleared by re-running, so it
            # must not be spelled the same as TAINTED, which is.
            tasks_named = ", ".join(str(i) for i in unreadable[:4])
            unreadable_reason = (
                f"task {tasks_named} declares its files in prose, so this check's "
                f"prerequisites cannot be measured; re-running will not change that"
            )
            # `reason` carries the expectation-mismatch finding and must survive here exactly as
            # it does on the TAINTED row below. Dropping it silenced that finding on 50 of this
            # repo's 223 checks — and UNREADABLE takes over from TAINTED at precisely the re-run
            # where the operator is looking for a verdict.
            rows.append(
                Row(
                    check,
                    "UNREADABLE",
                    f"{unreadable_reason}; {reason}" if reason else unreadable_reason,
                    rc,
                    output,
                    bool(reason),
                )
            )
            continue
        if stale:
            taint = f"unchanged since base: {', '.join(stale[:4])}"
            rows.append(
                Row(
                    check,
                    "TAINTED",
                    f"{taint}; {reason}" if reason else taint,
                    rc,
                    output,
                    bool(reason),
                )
            )
            continue
        rows.append(Row(check, "RAN", reason, rc, output, bool(reason)))
    return rows, state, tasks


def _emit_row(row: Row, plan_name: str) -> None:
    """Print one check's row, its quoted expectation, and an output excerpt."""
    cmd = (row.check.command or "").replace("\n", " ; ")
    if len(cmd) > 100:
        cmd = cmd[:97] + "..."
    rc = "-" if row.rc is None else str(row.rc)
    print(f"{row.state:<10} {plan_name}:{row.check.line} [{row.check.shape}] rc={rc}")
    if cmd:
        print(f"           $ {cmd}")
    if row.reason:
        print(f"           ! {row.reason}")
    print(f"           expected: {row.check.expectation[:150]}")
    if row.output:
        lines = row.output.splitlines()
        for line in lines[:OUTPUT_EXCERPT_LINES]:
            print(f"           | {line[:150]}")
        if len(lines) > OUTPUT_EXCERPT_LINES:
            print(f"           | ... {len(lines) - OUTPUT_EXCERPT_LINES} more lines")
    print()


def _install_deny_all_pre_push(clone: Path) -> None:
    """Refuse any push from a rehearsal clone.

    Accident protection, NOT containment: `--no-verify` and `-c core.hooksPath=…` both walk past
    it, measured. It is here because nothing legitimate pushes from a rehearsal clone, so refusing
    is free — not because it closes the wrapper limit the scope note concedes.
    """
    hooks = clone / ".git" / "hooks"
    hooks.mkdir(parents=True, exist_ok=True)
    pre_push = hooks / "pre-push"
    pre_push.write_text(
        "#!/bin/sh\n"
        "echo 'plan-rehearse: this is a rehearsal clone; pushing from it is never correct.' >&2\n"
        "exit 1\n",
        encoding="utf-8",
    )
    pre_push.chmod(0o755)


def create_clone(scope: Path, clone: Path, branch: str, base: str) -> str | None:
    """Make the sandbox exactly as /feature step 5 prescribes; return an error or None."""
    if clone.exists():
        return f"--clone already exists: {clone}"
    proc = subprocess.run(
        ["git", "clone", "-q", "-b", branch, str(scope), str(clone)],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return f"clone failed: {proc.stderr.strip()}"
    # Immediately, BEFORE any path that can fail and return. A later failure leaves the clone
    # DIRECTORY behind; `--create` then refuses it as already existing, and the documented re-run
    # form rehearses inside it. Measured — a mistyped `--base` left a hookless, un-recreatable
    # clone, so the one failure mode an operator is most likely to hit produced the least
    # protected tree.
    _install_deny_all_pre_push(clone)
    # The hook's two neighbours belong above the returns for the SAME reason, and were left
    # behind when it moved: the scope note in both files says the clone has no remote, and that
    # was false for exactly the residual directory the documented re-run form rehearses inside.
    # An unconfigured one also prompts a hardware key on any check that commits. `remote remove`
    # is the one piece that cannot come first -- it deletes `refs/remotes/origin/*`, which the
    # `git branch` below reads -- so it sits immediately after that call and still above the
    # return. Removing it DOES change what the check below can resolve: `origin` and
    # `origin/<branch>` resolve through `refs/remotes/origin/HEAD` and `refs/remotes/origin/*`
    # while the remote exists, and stop afterwards (measured). That is not a regression --
    # both were refused end to end before this reorder too, one stage later by main's own base
    # probe and with rc=2 either way. Only the stage and the message moved.
    _git(clone, "config", "commit.gpgsign", "false")
    _git(clone, "config", "tag.gpgsign", "false")
    made = _git(clone, "branch", base, f"origin/{base}")
    _git(clone, "remote", "remove", "origin")
    # Assert the OUTCOME, not the command's exit status. When `--branch` equals `--base` the
    # branch already exists and `git branch` fails "already exists" — benign, and reporting that
    # broke a passing row. What matters is only whether the base RESOLVES afterwards, because
    # that is what every later comparison depends on.
    if (
        _git(clone, "rev-parse", "--verify", "--quiet", f"{base}^{{commit}}").returncode
        != 0
    ):
        return (
            f"cloned, but base {base!r} does not resolve afterwards "
            f"(git branch said: {made.stderr.strip() or 'nothing'})"
        )
    return None


def main(argv: list[str]) -> int:
    """Parse arguments, rehearse, and print the terminal verdict line."""
    parser = argparse.ArgumentParser(prog="plan-rehearse.py", add_help=True)
    parser.add_argument("--plan", required=True)
    parser.add_argument("--clone", required=True)
    parser.add_argument("--base", default="dev")
    parser.add_argument("--scope")
    parser.add_argument("--branch")
    parser.add_argument("--create", action="store_true")
    parser.add_argument("--list", action="store_true", dest="dry")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    try:
        args = parser.parse_args(argv[1:])
    except SystemExit as exc:
        if exc.code == 0:  # --help is not a usage failure
            raise
        print(
            "RESULT: ERROR rc=2 checks=0 ran=0 tainted=0 refused=0 unpaired=0 unrun=0"
        )
        return 2

    plan, clone = Path(args.plan), Path(args.clone)
    if not plan.is_file():
        print(f"ERROR: no such plan: {plan}", file=sys.stderr)
        print(
            "RESULT: ERROR rc=2 checks=0 ran=0 tainted=0 refused=0 unpaired=0 unrun=0"
        )
        return 2
    if args.create:
        if not args.scope or not args.branch:
            print("ERROR: --create needs --scope and --branch", file=sys.stderr)
            print(
                "RESULT: ERROR rc=2 checks=0 ran=0 tainted=0 refused=0 unpaired=0 unrun=0"
            )
            return 2
        err = create_clone(Path(args.scope), clone, args.branch, args.base)
        if err:
            print(f"ERROR: {err}", file=sys.stderr)
            print(
                "RESULT: ERROR rc=2 checks=0 ran=0 tainted=0 refused=0 unpaired=0 unrun=0"
            )
            return 2
    if not (clone / ".git").exists():
        print(f"ERROR: --clone is not a git repo: {clone}", file=sys.stderr)
        print(
            "RESULT: ERROR rc=2 checks=0 ran=0 tainted=0 refused=0 unpaired=0 unrun=0"
        )
        return 2

    # Validate the base BEFORE rehearsing. The tool already computed this tell and used it only
    # as a display string; promoting it to a precondition is the whole fix. Without it a `--base`
    # absent from the clone (the default `dev`, against a clone made from a `main` repo) produced
    # `RESULT: RAN rc=0` on a tree whose declared files had not been touched at all.
    probe = _git(clone, "rev-parse", "--verify", "--quiet", f"{args.base}^{{commit}}")
    if probe.returncode != 0:
        print(
            f"ERROR: --base {args.base!r} does not resolve in {clone}. Taint detection compares "
            f"against it, so an unresolvable base would report a clean rehearsal over a tree "
            f"nothing was checked against.",
            file=sys.stderr,
        )
        print(
            "RESULT: ERROR rc=2 checks=0 ran=0 tainted=0 refused=0 unpaired=0 unrun=0 flagged=0"
        )
        return 2

    rows, state, _ = rehearse(plan, clone, args.base, args.timeout, args.dry)
    base_sha = _git(clone, "rev-parse", "--short", args.base).stdout.strip() or "?"

    print(f"plan:  {plan}")
    print(f"clone: {clone}   base: {args.base} ({base_sha})")
    if state.edited:
        print(
            f"state: EDITED  diff: {state.diffstat.splitlines()[-1] if state.diffstat else '-'}"
        )
        if state.porcelain:
            print(
                f"       index: {len(state.porcelain.splitlines())} path(s) differ from base"
            )
    else:
        print(
            "state: UNEDITED -- every check below graded the BASE tree, not the plan's"
        )
    print()
    for row in rows:
        _emit_row(row, plan.name)

    tally = {
        "ran": sum(1 for r in rows if r.state == "RAN"),
        "tainted": sum(1 for r in rows if r.state == "TAINTED"),
        "refused": sum(1 for r in rows if r.state == "REFUSED"),
        "unpaired": sum(1 for r in rows if r.state == "UNPAIRED"),
        "unrun": sum(1 for r in rows if r.state == "UNRUN"),
        "unreadable": sum(1 for r in rows if r.state == "UNREADABLE"),
        # A flagged row is a finding, and a reader skims the terminal line rather than the body,
        # so the count belongs here and not only beside the row that earned it.
        # Counted over TAINTED as well as RAN. At base every row is TAINTED by design, so a
        # RAN-only count was structurally 0 for exactly the population the base reading exists to
        # surface — the terminal line said flagged=0 while the body carried the finding.
        # UNREADABLE belongs here for the same reason TAINTED does: it is a row that RAN, with
        # an rc and output, so a finding on it is a finding a reader skimming the terminal line
        # must still see.
        # Counted from the row's own flag, over every state that RAN. A row's state explanation
        # is not a finding about the check; only something appended to it is.
        "flagged": sum(1 for r in rows if r.finding),
    }
    # No `and not args.dry`: in dry mode nothing reaches state RAN, so `ran == len(rows)` is
    # already false for any non-empty plan. The clause was inert and is deliberately absent.
    complete = rows and tally["ran"] == len(rows) and state.edited
    status, rc = ("RAN", 0) if complete else ("INDETERMINATE", 1)
    print(
        f"RESULT: {status} rc={rc} checks={len(rows)} ran={tally['ran']} "
        f"tainted={tally['tainted']} refused={tally['refused']} "
        f"unpaired={tally['unpaired']} unrun={tally['unrun']} "
        f"unreadable={tally['unreadable']} "
        f"flagged={tally['flagged']} "
        f"base={base_sha} edited={'yes' if state.edited else 'no'} tree={state.digest}"
    )
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv))

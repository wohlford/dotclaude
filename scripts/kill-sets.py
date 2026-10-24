#!/usr/bin/env python3
# Script: kill-sets.py
# Purpose: Run a mutation campaign's mutants one at a time on a fresh copy of the tree and report each mutant's kill set
# Usage: kill-sets.py --campaign scripts/tests/mutate_x.py [--timeout S] [--root DIR] [--keep]
"""Report each mutant's KILL SET: the suite rows it made fail, read on a copy of the tree.

**Why this exists.** A campaign run through `scripts/lib/mutate.py` reports `caught=N
survived=0`, which reads the same whether the mutants are distinct, whether two are
duplicates, or whether one killed EVERY row (collection or startup died, not a check). Only
each mutant's set of failing rows tells them apart, and `mutate.py` keeps none. Every change
that followed CLAUDE.md's "compare KILL SETS" hand-rolled a one-off harness and lost it.

    kill-sets.py --campaign scripts/tests/mutate_x.py

Run it through `run-long.sh` (a large campaign takes tens of minutes) with
`--expect '^RESULT: (PASS|FAIL|ERROR) rc=[0-9]+ mutants=[0-9]+ identical_pairs=[0-9]+
killed_all=[0-9]+'`, anchored at line start so a mutant label quoting the grammar cannot
match.

**How a row is identified.** A row is the name a suite prints when it PASSES; a kill is that
name's ABSENCE from the mutant's run. FAIL lines never identify rows (their detail varies too
much to match honestly); they only EXPLAIN absences by count, and print as diagnostics. The
universe `U` comes from the unmutated suite, run twice, and must be non-empty and stable.

**How the copy is kept honest.** A master copy `M` of the whole tree (with `.git` and ignored
files, without caches) pins the launch tree; a working copy `W`, at one fixed path, is deleted
and re-copied from `M` before every run, so nothing a mutant's suite wrote reaches the next one.
Every absolute path in the campaign's `SUITE` is judged by the tree it resolves into: into the
live tree or the master is an ERROR. Nothing runs `git`, and nothing is written in the repo.

**Not here (increment 1).** No `--jobs`, no subset selection, no signal handler. A killed run
has no `RESULT:` line; it leaves its `kill-sets.` temp directory (the master and working
copies, about twice the tree's size; named by the first `copy:` line, printed as it is made)
and possibly a suite still running in its own session, up to about 300 s. Find both before
editing the tree or starting another run:

    ls -d "${TMPDIR:-/tmp}"/kill-sets.*      # the leftover directory
    lsof +D <that directory>                 # any process with its cwd or a file in it
    pgrep -fl kill-sets                      # processes naming the copy path

`pgrep` alone can print nothing while a suite still runs: it matches only command lines that
carry the copy path, and a campaign whose `SUITE` names its tests by a relative path runs with
no such argument. `lsof +D` sees the suite through its working directory, which is in the copy;
read what it prints, not its exit status (measured: rc 1 while it listed the live suite).

**Verdict.** The last line: `RESULT: PASS|FAIL|ERROR rc=0|1|2` followed by fixed counts.
ERROR outranks FAIL outranks PASS.

`--root` is for test fixtures only; the default root is this file's own repository, resolved
through `realpath` (a symlinked `~/.claude/scripts/` resolves into the production checkout).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path
from typing import NamedTuple

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
# This interpreter's own import writes no bytecode beside `mutate.py` (a run from the live
# checkout would otherwise leave `scripts/lib/__pycache__` in the tree). Scoped to this one
# import, and never a suite's: no variable is set for any child process.
_wrote_bytecode, sys.dont_write_bytecode = sys.dont_write_bytecode, True
try:
    import mutate  # noqa: E402
except Exception:  # an unimportable runner is reported as `incompatible mutate.py`
    mutate = None
finally:
    sys.dont_write_bytecode = _wrote_bytecode

# The public `mutate` names this tool uses. A rename there must fail loudly here, never fall
# back to a restated predicate: two spellings of "did the suite notice" must not exist.
REQUIRED_MUTATE_NAMES = (
    "is_caught",
    "COLLECTION_ABORT_PATTERN",
    "derive_timeout",
    "BASELINE_TIMEOUT_SECONDS",
    "judge_mutant_parse",
    "check_parse",
    "PARSES",
    "UNPARSEABLE",
    "CANNOT_RUN",
)

# Not a flag (increment 1): a tree over this many MB is refused before anything is copied.
MAX_COPY_MB = 1024
CACHE_DIRS = ("__pycache__", ".pytest_cache", ".ruff_cache")
DISCOVERY_SECONDS = 60

OPAQUE_REASON = "opaque -c body: pytest cannot be armed; increment 1 does not grade it"
SHELLS = ("bash", "sh", "zsh", "dash")
ENV_ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
# `env` options that take the NEXT token as their value (GNU and BSD spellings).
ENV_VALUE_OPTS = {"-u", "--unset", "-C", "--chdir", "-P", "-L", "-U", "-a", "--argv0"}
ENV_SPLIT_OPTS = ("-S", "--split-string")
# Short options whose value follows them (attached or as the next token), per head family.
PY_VALUE_LETTERS = "WX"
SH_VALUE_LETTERS = "oO"
LONG_VALUE_OPTS = {"--rcfile", "--init-file", "--check-hash-based-pycs"}
CLUSTER = re.compile(r"^[-+][A-Za-z]+$")

# pytest is ARMED to list passed, failed and errored ids in its short summary. Never `-rA`: it
# also prints the captured stdout of PASSED tests, so a passing test echoing a column-0
# `FAIL  ...` would make a green baseline read as noticed.
PYTEST_REPORT = "-rpfE"
EXITFIRST = {"-x", "--exitfirst"}

# The canary: one line that is invalid Python, bash and JSON, so a subject that tolerates being
# EMPTY (a sourced shell file, a reader treating empty as `{}`) still notices it.
CANARY_TEXT = "{"
SILENT_CANARY = (
    "this suite loses rows silently when its subject breaks; mutants that abort it will "
    "read incomplete (a lower bound), not an error."
)
ABORTED_CANARY = (
    "the canary aborts this suite's collection; mutants that break the subject's import "
    "will read INVALID (no test ran), not killed."
)

SCRUBBED = ("KILLSETS", "KILL_LOG")
WATCHED = (
    "PYTEST_ADDOPTS",
    "PYTEST_PLUGINS",
    "HOOK_TESTS_ONLY",
    "CLAUDE_PROJECT_DIR",
    "PYTHONDONTWRITEBYTECODE",
)
WATCHED_PREFIX = "AUDIT_TEST_"

GRADES = "grades the tree as copied at launch; later live edits are not graded"
SERIAL = "serial, one run per mutant: determinism NOT assessed"
TREE_NAMES = {"W": "working copy", "M": "master copy", "L": "live tree"}
INDENT = " " * 10

# The RESULT fields after `mutants=`, in their fixed order, and what each gates.
FIELDS = (
    "identical_pairs",
    "killed_all",
    "survivors",
    "unattributed",
    "invalid",
    "timedout",
    "notapplied",
    "incomplete",
    "single_row",
)
FIELD_SEVERITY = {
    "survivors": "FAIL",
    "identical_pairs": "FAIL",
    "killed_all": "FAIL",
    "incomplete": "FAIL",
    "unattributed": "ERROR",
    "invalid": "ERROR",
    "timedout": "ERROR",
    "notapplied": "ERROR",
}

# The discovery child: imports the campaign by path and writes ONE JSON answer to a file. It
# never speaks on stdout, so a campaign's own top-level prints cannot corrupt the channel.
LOADER = r"""
import importlib.util, json, pathlib, sys, types
path, out = sys.argv[1], sys.argv[2]

def answer(data):
    with open(out, "w") as f:
        json.dump(data, f)

def fail(why):
    answer({"error": why})
    sys.exit(0)

try:
    sys.argv = [path]
    spec = importlib.util.spec_from_file_location("kill_sets_campaign", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
except SystemExit as exc:
    fail(f"the campaign raised SystemExit({exc.code!r}) at import")
except BaseException as exc:
    fail(f"the campaign did not import: {type(exc).__name__}: {exc}")
for name in ("SUBJECT", "MUTATIONS", "SUITE"):
    if not hasattr(mod, name):
        fail(f"the campaign defines no module-level {name}")
subject, mutations, suite = mod.SUBJECT, mod.MUTATIONS, mod.SUITE
if not isinstance(subject, pathlib.PurePath):
    fail(f"SUBJECT is not a Path (it is a {type(subject).__name__})")
if not isinstance(mutations, list):
    fail(f"MUTATIONS is not a list (it is a {type(mutations).__name__})")
rows = []
for n, m in enumerate(mutations, 1):
    if not isinstance(m, (tuple, list)) or len(m) != 3:
        fail(f"mutant [{n}] is not a (label, old, new) triple")
    if not all(isinstance(x, str) for x in m):
        fail(f"mutant [{n}] holds a non-string element")
    rows.append(list(m))
if not isinstance(suite, list) or not all(isinstance(t, str) for t in suite):
    fail("SUITE is not a list of strings")
bound = getattr(mod, "mutate", None)
where = getattr(bound, "__file__", None) if isinstance(bound, types.ModuleType) else None
answer({"subject": str(subject), "suite": suite, "mutations": rows, "mutate": where})
"""


class KillSetsError(Exception):
    """A named pre-run failure: each argument is printed as one `ERROR` line."""


class Campaign(NamedTuple):
    subject: Path
    suite: list
    mutations: list
    mutate_path: str | None


class State:
    """What the run has established so far; the RESULT line is built from it on any exit."""

    def __init__(self) -> None:
        self.counts = {"mutants": 0, **{f: 0 for f in FIELDS}}
        self.error = False
        self.tmp: Path | None = None


def emit(line: str) -> None:
    print(line, flush=True)


def _child_env() -> dict:
    """The parent's environment minus the two sidecar switches that bend `SUITE` at import."""
    return {k: v for k, v in os.environ.items() if k not in SCRUBBED}


def _env_header() -> list[str]:
    """One header line: HOME, TMPDIR, and every watched variable that is set."""
    env = os.environ
    watched = [k for k in WATCHED if k in env]
    watched += sorted(k for k in env if k.startswith(WATCHED_PREFIX))
    return [
        f"env: HOME={env.get('HOME', '(unset)')} TMPDIR={env.get('TMPDIR', '(unset)')} "
        f"(inherited; KILLSETS and KILL_LOG scrubbed; set: {', '.join(watched) or 'none'})"
    ]


def _inside(path, tree) -> bool:
    """Is `path` equal to or below `tree`? Both sides through realpath, always."""
    p, t = os.path.realpath(path), os.path.realpath(tree)
    return p == t or p.startswith(t.rstrip(os.sep) + os.sep)


def _tree_of(path, work, live, master) -> str | None:
    for name, tree in (("W", work), ("M", master), ("L", live)):
        if _inside(path, tree):
            return name
    return None


def _path_values(token: str) -> list[str]:
    """The absolute paths a token carries: the token itself, or the value of `X=/abs`."""
    if token.startswith("/"):
        return [token]
    if "=" in token and token.split("=", 1)[1].startswith("/"):
        return [token.split("=", 1)[1]]
    return []


def judge_paths(argv, work, live, master) -> list[str]:
    """One ERROR per argv path that resolves into the live tree or the master copy."""
    errors = []
    for token in argv:
        for value in _path_values(token):
            tree = _tree_of(value, work, live, master)
            if tree in ("L", "M"):
                errors.append(
                    f"copy unproven: SUITE token {token!r} resolves into the "
                    f"{TREE_NAMES[tree]}: {value}"
                )
    return errors


def outside_paths(argv, work, live, master) -> list[str]:
    return [
        value
        for token in argv
        for value in _path_values(token)
        if _tree_of(value, work, live, master) is None
    ]


def strip_env(argv) -> list | None:
    """The argv after a leading `env`, its options and its `NAME=v` tokens.

    Value-taking options (`-u X`, `-C dir`, `-P path`, `--unset X`) skip their value too, and
    `-S`/`--split-string` splices its split string back in, as `env` itself does. None when
    that string cannot be split: the program is then one the tool cannot read.
    """
    argv = list(argv)
    if not argv or os.path.basename(argv[0]) != "env":
        return argv
    i = 1
    while i < len(argv):
        token = argv[i]
        split = None
        if token in ENV_SPLIT_OPTS:
            split, rest = argv[i + 1 : i + 2], argv[i + 2 :]
        elif token.startswith("--split-string="):
            split, rest = [token.split("=", 1)[1]], argv[i + 1 :]
        elif token.startswith("-S") and not token.startswith("--"):
            split, rest = [token[2:]], argv[i + 1 :]
        if split is not None:
            try:
                words = shlex.split(split[0]) if split else []
            except ValueError:
                return None
            return strip_env(["env", *words, *rest])
        if token == "--":
            return argv[i + 1 :]
        if token in ENV_VALUE_OPTS:
            i += 2
        elif token.startswith("-") or ENV_ASSIGN.match(token):
            i += 1  # a flag (`-i`, `-`, `-uX`, `--unset=X`) or an assignment
        else:
            break
    return argv[i:]


def scan_options(python: bool, tokens) -> tuple[str, str | None]:
    """Scan a shell or python head's option tokens; say how the options END.

    `("c", None)` at a `-c`; `("m", module)` at python's `-m` (the module named after it,
    attached as in `-mpytest` or as the next token); `("end", None)` at a script path, `--`,
    a shell's `-m`, any other positional, or the end of the argv. A CLUSTER (`-ec`, `-Ic`)
    holding a standalone `c` is a `-c`; the letters after a value-taking option inside a
    cluster (`-Werror`, `-Xdev`) are its value, and a separate value (`-W error`,
    `-X a=/b`, `-o pipefail`) is skipped. A token carrying `=` or `:` is a value.
    """
    value_letters = PY_VALUE_LETTERS if python else SH_VALUE_LETTERS
    i = 0
    while i < len(tokens):
        token = tokens[i]
        i += 1
        if token == "-m" and python:
            return "m", tokens[i] if i < len(tokens) else None
        if token in ("-m", "--") or token.endswith((".sh", ".py")):
            return "end", None
        if token in LONG_VALUE_OPTS:
            i += 1
        elif token.startswith("--"):
            continue  # a long flag, or `--opt=value`
        elif CLUSTER.match(token):
            for j, letter in enumerate(token[1:]):
                if letter == "c" and token[0] == "-":
                    return "c", None
                if python and letter == "m":
                    module = token[j + 2 :] or (tokens[i] if i < len(tokens) else "")
                    return "m", module or None
                if letter in value_letters:
                    i += 1 if j == len(token) - 2 else 0  # the value is the next token
                    break  # else the rest of the cluster is the value
        elif token.startswith(("-", "+")) or "=" in token or ":" in token:
            continue  # an option with an attached value (`-Werror::X`), or a value
        else:
            return "end", None  # a positional: the script; what follows is its own
    return "end", None


def _head(rest) -> tuple[str, bool] | None:
    """The head's basename and whether it is python, when it is a shell or `python*`."""
    if not rest:
        return None
    head = os.path.basename(rest[0])
    python = head.startswith("python")
    return (head, python) if python or head in SHELLS else None


def opaque(argv) -> bool:
    """Is the suite a `-c` body the tool cannot read (a shell or python head given `-c`)?

    After `strip_env`, the head is a shell or `python*` by basename and `scan_options`
    reaches a `-c` among its options.
    """
    rest = strip_env(argv)
    if rest is None:
        return True
    head = _head(rest)
    return head is not None and scan_options(head[1], rest[1:])[0] == "c"


def preflight(root) -> None:
    """Refuse a tree carrying a live in-place mutant or a torn git state, before any copy."""
    root = str(root)
    backups, locks = [], []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d != ".git"]
        backups += [
            os.path.join(dirpath, f) for f in filenames if f.endswith(".mutate-backup")
        ]
    for dirpath, _dirnames, filenames in os.walk(os.path.join(root, ".git")):
        locks += [os.path.join(dirpath, f) for f in filenames if f.endswith(".lock")]
    errors = []
    # A `.git` that is not a real directory (a worktree's or submodule's gitdir FILE, or a
    # symlink) is copied as a POINTER: every suite's git in the copy would then read and
    # write the live git directory (its index, refs, locks) while the run reads as clean.
    dotgit = os.path.join(root, ".git")
    if os.path.islink(dotgit):
        errors.append(
            f"preflight: {dotgit} is a symlink; the copy would share the live git "
            "directory"
        )
    elif os.path.lexists(dotgit) and not os.path.isdir(dotgit):
        errors.append(
            f"preflight: {dotgit} is a gitdir file; the copy would share the live git "
            "directory"
        )
    if backups:
        errors.append(
            "preflight: a mutation campaign may be running in place; a copy would carry "
            f"its live mutant (*.mutate-backup): {', '.join(sorted(backups))}"
        )
    if locks:
        errors.append(
            "preflight: a git operation may be in flight; every copy would inherit its "
            f"lock (.git/*.lock): {', '.join(sorted(locks))}"
        )
    if errors:
        raise KillSetsError(*errors)


def _tree_bytes(root) -> int:
    total = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in CACHE_DIRS]
        for f in filenames:
            total += os.lstat(os.path.join(dirpath, f)).st_size
    return total


def make_master(root, tmp) -> Path:
    """Copy the whole tree (with `.git` and ignored files, without caches) to `tmp/master`."""
    size = _tree_bytes(root)
    if size > MAX_COPY_MB * 1024 * 1024:
        raise KillSetsError(
            f"the tree holds {size / 1048576:.1f} MB, over MAX_COPY_MB = {MAX_COPY_MB}; "
            "nothing was copied"
        )
    master = Path(tmp) / "master"
    shutil.copytree(
        root, master, symlinks=True, ignore=shutil.ignore_patterns(*CACHE_DIRS)
    )
    return master


def refresh_working_copy(master, work) -> None:
    """Delete `work` and copy it again from the master: no run sees another run's writes."""
    if os.path.lexists(work):
        shutil.rmtree(work)
    shutil.copytree(master, work, symlinks=True)


def load_campaign(copy_root, campaign, scratch_parent=None) -> Campaign:
    """Import the campaign in a child (`python3 -I -B`, cwd = `copy_root`, 60 s bound).

    The answer file lives in a scratch directory under `scratch_parent` (the run's announced
    temp directory), so a kill during discovery leaves no second, unannounced directory.
    Without it (an in-process caller) the scratch is a `kill-sets.` directory of its own.
    """
    if scratch_parent is None:
        scratch = Path(tempfile.mkdtemp(prefix="kill-sets."))
    else:
        scratch = Path(tempfile.mkdtemp(prefix="discovery.", dir=scratch_parent))
    try:
        answer, err = scratch / "campaign.json", scratch / "discovery.err"
        with open(err, "wb") as fe:
            try:
                subprocess.run(
                    [
                        sys.executable,
                        "-I",
                        "-B",
                        "-c",
                        LOADER,
                        str(campaign),
                        str(answer),
                    ],
                    cwd=str(copy_root),
                    env=_child_env(),
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=fe,
                    timeout=DISCOVERY_SECONDS,
                )
            except subprocess.TimeoutExpired:
                raise KillSetsError(
                    f"discovery of {campaign} did not finish within "
                    f"{DISCOVERY_SECONDS} s"
                ) from None
        if not answer.exists():
            tail = err.read_text(errors="replace").strip().splitlines()[-3:]
            raise KillSetsError(
                f"discovery of {campaign} wrote no answer: {' | '.join(tail)}"
            )
        data = json.loads(answer.read_text())
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    if "error" in data:
        raise KillSetsError(f"{campaign}: {data['error']}")
    mutations = [tuple(m) for m in data["mutations"]]
    if not mutations:
        raise KillSetsError(
            f"{campaign}: no mutants; a campaign of zero proves nothing"
        )
    for n, (label, old, new) in enumerate(mutations, 1):
        if old == new:
            raise KillSetsError(f"{campaign}: mutant [{n}] {label!r} has old == new")
    subject = Path(data["subject"])
    if not subject.is_absolute():
        subject = Path(copy_root) / subject
    return Campaign(subject, data["suite"], mutations, data["mutate"])


class RunResult(NamedTuple):
    rc: int | None  # None when the run timed out
    stdout: str
    stderr: str
    elapsed: float


def _kill_group(pgid: int) -> None:
    """SIGKILL the group; a group with nothing left alive to signal is not an error.

    Linux says so with ESRCH. Darwin says EPERM when the group's only members are zombies
    (measured: a `true` suite's unreaped leader alone gives EPERM, then ESRCH once reaped).
    The id is this run's own new session, held by its unreaped leader, so it cannot name
    anyone else's group.
    """
    try:
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def _exited(pid: int, limit) -> bool:
    """Wait (up to `limit` s, or forever) for `pid` to exit WITHOUT reaping it.

    `WNOWAIT` leaves the child an unreaped zombie, which still holds its process group id,
    so the group can be killed afterwards with no risk of the id having been reused.
    """
    deadline = None if limit is None else time.monotonic() + limit
    pause = 0.005
    while True:
        if os.waitid(os.P_PID, pid, os.WEXITED | os.WNOWAIT | os.WNOHANG) is not None:
            return True
        if deadline is not None and time.monotonic() >= deadline:
            return False
        time.sleep(pause)
        pause = min(pause * 2, 0.1)


def run_suite(argv, cwd, env, limit, runs_dir) -> RunResult:
    """One suite run in its own session; both streams to fresh files; wait on the PROCESS.

    Never a pipe: a backgrounded grandchild holds a pipe open after the suite exits, so a
    pipe read would wait for it. The files are opened fresh under unique names, so a late
    write by an escaped grandchild cannot land in the next run's file. The group recorded
    at spawn is killed after EVERY run, normal or timed out, while the leader is still an
    unreaped zombie holding it; only then is the leader reaped.
    """
    fd_out, out_path = tempfile.mkstemp(prefix="run.", suffix=".out", dir=runs_dir)
    fd_err, err_path = tempfile.mkstemp(prefix="run.", suffix=".err", dir=runs_dir)
    with os.fdopen(fd_out, "wb") as fo, os.fdopen(fd_err, "wb") as fe:
        started = time.monotonic()
        proc = subprocess.Popen(
            argv,
            cwd=str(cwd),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=fo,
            stderr=fe,
            start_new_session=True,
        )
        pgid = proc.pid  # a new session's group id is its leader's pid
        timed_out = not _exited(proc.pid, limit)
        if timed_out:
            _kill_group(pgid)
            _exited(proc.pid, None)
        elapsed = time.monotonic() - started
        _kill_group(pgid)
        rc = proc.wait()
        if timed_out:
            rc = None
    return RunResult(
        rc,
        Path(out_path).read_bytes().decode("utf-8", "replace"),
        Path(err_path).read_bytes().decode("utf-8", "replace"),
        elapsed,
    )


PASS_LINE = re.compile(r"^PASS  (.+)$")
FAIL_LINE = re.compile(r"^FAIL  (.+)$")
DUMP_START = re.compile(r"^\s*--- output ---\s*$")
DUMP_END = re.compile(r"^\s*-{10,}\s*$")


class Reading(NamedTuple):
    passed: frozenset  # the bases of the PASS lines outside nested dumps (pytest: ids)
    texts: dict  # base -> every PASS text printed under it
    fails: list  # the `FAIL  ` lines outside nested dumps (pytest: FAILED/ERROR lines)
    dumped: int  # lines inside nested dumps, never read as rows
    first: str
    last: str
    failing: frozenset | None = None  # pytest only: the FAILED and ERROR ids
    stopped: str = ""  # pytest only: the `stopping after N failures` banner, if any


def row_base(text: str) -> str:
    """The pass text minus ONE trailing balanced `(...)` group and the space before it.

    The group holds VALUES (`(19)`, `(took 3s)`) that vary run to run. An unbalanced
    trailing `)` strips nothing, and so does a group that would leave nothing.
    """
    if not text.endswith(")"):
        return text
    depth = 0
    for i in range(len(text) - 1, -1, -1):
        if text[i] == ")":
            depth += 1
        elif text[i] == "(":
            depth -= 1
            if depth == 0:
                head = text[:i].removesuffix(" ")
                return head or text
    return text


def read_shell(stdout: str) -> Reading:
    """Rows are `PASS  <name>` lines (two spaces) outside `--- output ---` dumps."""
    texts: dict = {}
    fails, dumped, in_dump = [], 0, False
    lines = stdout.split("\n")
    for line in lines:
        if in_dump:
            dumped += 1
            in_dump = not DUMP_END.match(line)
            continue
        if DUMP_START.match(line):
            in_dump = True
            continue
        m = PASS_LINE.match(line)
        if m:
            texts.setdefault(row_base(m.group(1)), []).append(m.group(1))
        elif FAIL_LINE.match(line):
            fails.append(line)
    shown = [x for x in lines if x.strip()]
    return Reading(
        frozenset(texts),
        texts,
        fails,
        dumped,
        shown[0] if shown else "",
        shown[-1] if shown else "",
    )


SUMMARY_START = re.compile(r"^=+ short test summary info =+$")
SECTION = re.compile(r"^=+ ")
PYTEST_ROW = re.compile(r"^(PASSED|FAILED|ERROR) (\S+::\S.*)$")
STOP_BANNER = re.compile(r"^!+ stopping after [0-9]+ failures? !+$", re.M)


def is_pytest(argv) -> bool:
    """After `strip_env`: a `python*` head whose options end in `-m pytest`.

    The interpreter's options are read by the same `scan_options` as the opaque refusal, so
    a separate option value (`-W error`) does not end them. An `env -S` string is never
    armed: its words are not argv tokens `arm_pytest` could strip or append after, so the
    stripped argv must be a suffix of the real one (unarmed, it fails closed as blind).
    """
    rest = strip_env(argv)
    if rest is None or list(argv[len(argv) - len(rest) :]) != rest:
        return False
    head = _head(rest)
    if head is None or not head[1]:
        return False
    return scan_options(True, rest[1:]) == ("m", "pytest")


def arm_pytest(argv) -> list:
    """Strip `-x`/`--exitfirst`/`--maxfail` (a stop hides rows) and append the report flag."""
    out, skip = [], False
    for token in argv:
        if skip:
            skip = False
            continue
        if token in EXITFIRST or token.startswith("--maxfail="):
            continue
        if token == "--maxfail":
            skip = True
            continue
        out.append(token)
    return out + [PYTEST_REPORT]


def _node_id(text: str) -> str:
    """The id is the text up to the first ` - ` outside square brackets."""
    depth = 0
    for i, ch in enumerate(text):
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth = max(0, depth - 1)
        elif depth == 0 and text.startswith(" - ", i):
            return text[:i]
    return text


def read_pytest(stdout: str) -> Reading:
    """Rows are read ONLY inside the short test summary block, never from captured text."""
    texts: dict = {}
    fails, failed, errored = [], set(), set()
    lines = stdout.split("\n")
    # Residual closed here: a failing test's captured stdout can hold its own summary header,
    # which would turn row reading back on; read ONLY the LAST block (the real one is last).
    starts = [i for i, line in enumerate(lines) if SUMMARY_START.match(line)]
    block = []
    for line in lines[starts[-1] + 1 :] if starts else []:
        if SECTION.match(line):
            break
        block.append(line)
    for line in block:
        m = PYTEST_ROW.match(line)
        if not m:
            continue
        node = _node_id(m.group(2))
        if m.group(1) == "PASSED":
            texts.setdefault(node, []).append(line)
            continue
        fails.append(line)
        (failed if m.group(1) == "FAILED" else errored).add(node)
    failing = set(failed)
    failing.update(errored)  # a setup or teardown ERROR is a failing row too
    banner = STOP_BANNER.search(stdout)
    shown = [x for x in lines if x.strip()]
    return Reading(
        frozenset(texts),
        texts,
        fails,
        0,
        shown[0] if shown else "",
        shown[-1] if shown else "",
        frozenset(failing),
        banner.group(0) if banner else "",
    )


class Verdict(NamedTuple):
    status: str
    kill: list
    flags: list
    new_labels: list
    nF: int


def _kills_all(universe, canary_absent, kill) -> bool:
    """KILLED-ALL: the rows that cannot run when the subject is unparseable all died."""
    if len(universe) < 2:
        return False
    if canary_absent:
        return kill >= canary_absent
    return kill == universe


def score(universe, canary_absent, reading: Reading, noticed: bool) -> Verdict:
    """The ladder: a kill is a baseline row whose pass name did not appear in this run."""
    kill = universe - reading.passed
    nF = len(reading.fails)
    new_labels = reading.passed - universe
    if reading.failing is not None:
        failing = reading.failing & universe
        kill = kill | failing
        nF = len(failing)
        new_labels = new_labels | (reading.failing - universe)
    unexplained, excess = max(0, len(kill) - nF), max(0, nF - len(kill))
    dup_in_run = sorted(b for b, texts in reading.texts.items() if len(texts) > 1)
    flags = []
    if unexplained:
        why = f"incomplete: {unexplained} absent rows without a FAIL line"
        why += f" (reason: {reading.stopped})" if reading.stopped else ""
        flags.append(why)
    if excess:
        flags.append(f"excess: {excess} FAIL lines own no absent row")
    if dup_in_run:
        flags.append(f"dup_in_run: {len(dup_in_run)} bases printed more than once")
    if kill and noticed:
        status = "KILLED-ALL" if _kills_all(universe, canary_absent, kill) else "KILLED"
    elif kill or noticed:
        status = "UNATTRIBUTED"
    else:
        status = "SURVIVED"
    return Verdict(status, sorted(kill), flags, sorted(new_labels), nF)


def verdict(counts, error: bool) -> tuple[str, int]:
    gates = {FIELD_SEVERITY[f] for f in FIELD_SEVERITY if counts[f]}
    if error or "ERROR" in gates:
        return "ERROR", 2
    if "FAIL" in gates:
        return "FAIL", 1
    return "PASS", 0


def result_line(counts, status: str, rc: int) -> str:
    head = f"RESULT: {status} rc={rc} mutants={counts['mutants']}"
    return head + "".join(f" {f}={counts[f]}" for f in FIELDS)


def _sha256(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_canary(subject_in_work, work) -> None:
    """Replace the COPY's subject with the canary; refused unless it resolves inside `W`."""
    if not _inside(subject_in_work, work):
        raise KillSetsError(
            f"copy unproven: the canary target {subject_in_work} does not resolve "
            f"inside the working copy {work}; nothing was written"
        )
    Path(subject_in_work).write_text(CANARY_TEXT + "\n", encoding="utf-8")


def _resolve_campaign(given: str, root: Path) -> Path:
    real = Path(os.path.realpath(given))
    if not os.path.lexists(given) or not real.exists():
        raise KillSetsError(f"--campaign {given}: no such file")
    if not _inside(real, root):
        raise KillSetsError(
            f"--campaign {given} resolves to {real}, outside the root {root}"
        )
    if not real.is_file():
        raise KillSetsError(f"--campaign {given}: not a file")
    return real.relative_to(root)


def _startup_checks() -> None:
    if not hasattr(os, "waitid"):
        raise KillSetsError(
            "this Python has no os.waitid, which the tool needs to own each suite's "
            "process group; run it with the repo's python3"
        )
    missing = [n for n in REQUIRED_MUTATE_NAMES if not hasattr(mutate, n)]
    if mutate is None or missing:
        where = getattr(mutate, "__file__", "unimportable")
        raise KillSetsError(
            f"incompatible mutate.py ({where}): missing {', '.join(missing)}"
        )


def _run(args, state: State) -> None:
    default_root = Path(os.path.realpath(__file__)).parent.parent
    root = Path(os.path.realpath(args.root or default_root))
    emit(
        f"kill-sets: campaign {args.campaign}  root {root}"
        + ("  (--root: fixture-only)" if args.root else "")
    )
    _startup_checks()
    rel = _resolve_campaign(args.campaign, root)
    preflight(root)

    # Copy assertion 1, judged BEFORE anything is created: every copy lands under the temp
    # directory, so one inside the root would put the master inside the tree it copies
    # (copytree then recurses into its own output, and writes into the live tree).
    tmp_base = tempfile.gettempdir()
    if _inside(tmp_base, root):
        raise KillSetsError(
            f"copy unproven: {tmp_base} and {root} overlap (the temp directory is inside "
            "the root, so every copy would land in the tree it copies; set TMPDIR "
            "outside it); nothing was copied"
        )
    state.tmp = Path(tempfile.mkdtemp(prefix="kill-sets.", dir=tmp_base))
    # Named the moment it exists, before anything is copied into it: a run killed at any
    # later point leaves THIS directory, and every other scratch the run makes is inside it.
    emit(f"copy: {state.tmp}  (master M, working copy W re-created per mutant)")
    master = state.tmp / "master"
    work = state.tmp / "work"
    for a, b in ((work, root), (master, root), (work, master)):
        if _inside(a, b) or _inside(b, a):
            raise KillSetsError(f"copy unproven: {a} and {b} overlap")
    master = make_master(root, state.tmp)
    emit(f"copy: size {_tree_bytes(master) / 1048576:.0f} MB (master; W is the same)")

    refresh_working_copy(master, work)
    c = load_campaign(work, work / rel, scratch_parent=state.tmp)
    state.counts["mutants"] = len(c.mutations)
    subject_rel = os.path.relpath(os.path.realpath(c.subject), os.path.realpath(work))
    emit(f"subject: {subject_rel}  mutants={len(c.mutations)}")
    argv = arm_pytest(c.suite) if is_pytest(c.suite) else list(c.suite)
    emit(f"suite: {shlex.join(argv)}")
    emit(f"campaign mutate path: {c.mutate_path or 'not bound'}")
    for value in outside_paths(c.suite, work, root, master):
        emit(f"outside every tree (recorded, not judged): {value}")
    for line in _env_header():
        emit(line)
    emit(GRADES)
    emit(SERIAL)

    if opaque(c.suite):
        raise KillSetsError(f"{rel}: {OPAQUE_REASON}")
    errors = judge_paths(c.suite, work, root, master)
    if _tree_of(c.subject, work, root, master) != "W":
        errors.append(
            f"copy unproven: SUBJECT {c.subject} does not resolve inside the working copy"
        )
    bound = c.mutate_path and _tree_of(c.mutate_path, work, root, master)
    if bound in ("L", "M"):
        errors.append(
            f"copy unproven: the campaign's mutate resolves into the "
            f"{TREE_NAMES[bound]}: {c.mutate_path}"
        )
    if errors:
        raise KillSetsError(*errors)

    # The pristine text every mutant is applied to, and the live subject's digest at launch
    # for the leak check: both read before any suite runs.
    original = Path(c.subject).read_text(encoding="utf-8", errors="surrogateescape")
    live_subject = root / subject_rel
    live_sha = _sha256(live_subject)
    control = mutate.check_parse(c.subject, original)
    if control[0] == mutate.CANNOT_RUN:
        raise KillSetsError(f"the parse check could not run: {control[1]}")
    emit(
        "parse check: on"
        if control[0] == mutate.PARSES
        else f"parse check: OFF ({control[1]})"
    )

    g = Grading(c, work, master, state.tmp / "runs", subject_rel, original, control)
    g.argv = argv
    # The leak check runs however this block ends (an ERROR or an internal exception too),
    # and never replaces the exception in flight: its own failure is one more ERROR line.
    try:
        g.runs_dir.mkdir()
        g.baseline(args.timeout)
        g.canary()
        g.mutants(state)
        g.duplicates(state)
    finally:
        try:
            _leak_check(live_subject, live_sha, g.applied, state)
        except Exception:
            emit("ERROR  the leak check itself failed:")
            for line in traceback.format_exc().rstrip().splitlines():
                emit(line)
            state.error = True


class Grading:
    """The baseline, the mutant loop and the duplicate report over one working copy."""

    def __init__(self, c, work, master, runs_dir, subject_rel, original, control):
        self.c, self.work, self.master, self.runs_dir = c, work, master, runs_dir
        self.subject_rel, self.original, self.control = subject_rel, original, control
        self.env = _child_env()
        self.argv: list = list(c.suite)  # as run: pytest armed, `-x` stripped
        self.universe: frozenset = frozenset()
        self.canary_absent: frozenset = frozenset()
        self.limit = 0.0
        self.applied: list = []  # (n, label, sha256 of the mutated text)
        self.killed: list = []  # (n, kill) of unflagged KILLED mutants

    def _fresh_run(self, limit, prepare=None) -> RunResult:
        refresh_working_copy(master=self.master, work=self.work)
        if prepare is not None:
            prepare()
        return run_suite(self.argv, self.work, self.env, limit, self.runs_dir)

    def read(self, stdout: str) -> Reading:
        """The ONE format the argv arms: pytest rows for armed pytest, else shell rows."""
        return read_pytest(stdout) if is_pytest(self.argv) else read_shell(stdout)

    def _gate(self, run: RunResult, limit) -> Reading:
        """One baseline run must be green, name rows, not abort, and not repeat a name."""
        suite = shlex.join(self.argv)
        reading = self.read(run.stdout)
        if run.rc is None:
            raise KillSetsError(
                f"the baseline timed out after {limit:.0f}s, last stdout line "
                f"{reading.last!r}: the suite never finishes unmutated, so no mutant can "
                "be judged (raise it with --timeout)"
            )
        universe = reading.passed
        if mutate.is_caught(run.rc, run.stdout):
            raise KillSetsError(
                f"the baseline is not green: rc={run.rc}, last stdout line "
                f"{reading.last!r}; every mutant would score as killed"
            )
        if not universe:
            raise KillSetsError(
                f"blind to this suite: a green run named no row (first stdout line "
                f"{reading.first!r}, last {reading.last!r}); suite {suite}"
            )
        if re.search(mutate.COLLECTION_ABORT_PATTERN, run.stdout, re.M):
            raise KillSetsError(
                "the collection-abort banner appears in a green baseline"
            )
        shared = {b: t for b, t in reading.texts.items() if len(t) > 1}
        if shared:
            labels = "; ".join(" | ".join(t) for t in shared.values())
            raise KillSetsError(
                f"suite {suite} prints two PASS lines with one base, which absence cannot "
                f"tell apart: {labels}; the remedy is a one-line label edit in the suite"
            )
        return reading

    def baseline(self, timeout) -> None:
        cap = timeout if timeout is not None else mutate.BASELINE_TIMEOUT_SECONDS
        first = self._fresh_run(cap)
        u1 = self._gate(first, cap).passed
        second = self._fresh_run(cap)
        u2 = self._gate(second, cap).passed
        if u1 != u2:
            raise KillSetsError(
                f"the suite's pass names differ between the two baseline runs: only in "
                f"run 1: {sorted(u1 - u2)}; only in run 2: {sorted(u2 - u1)}; suite "
                f"{shlex.join(self.argv)} (volatile labels; fix them in the suite)"
            )
        self.universe = u1
        elapsed = max(first.elapsed, second.elapsed)
        self.limit = mutate.derive_timeout(elapsed, timeout)
        emit(
            f"BASELINE green rc=0 {elapsed:.1f}s rows={len(u1)} (run twice, universes "
            f"equal) (limit {self.limit:.0f}s)"
        )

    def canary(self) -> None:
        """One run with the copy's subject replaced by `{`: the suite must notice it."""
        run = self._fresh_run(
            self.limit, prepare=lambda: write_canary(self.c.subject, self.work)
        )
        if run.rc is None:
            raise KillSetsError(
                f"the canary run timed out after {self.limit:.0f}s (raise --timeout)"
            )
        aborted = re.search(mutate.COLLECTION_ABORT_PATTERN, run.stdout, re.M)
        reading = self.read(run.stdout)
        noticed = mutate.is_caught(run.rc, run.stdout)
        if not noticed and not aborted:
            raise KillSetsError(
                "canary not noticed: the suite stayed green with the copied subject "
                f"replaced by {CANARY_TEXT!r}, so it does not read the copied subject "
                "(does it grade the live or an installed one?)"
            )
        v = score(self.universe, frozenset(), reading, True)
        self.canary_absent = self.universe if aborted else frozenset(v.kill)
        k = len(self.canary_absent)
        silent = max(0, k - v.nF)
        emit(
            f"canary: {'collection aborted' if aborted else 'noticed'}, {k} of "
            f"{len(self.universe)} rows absent, {silent} without a FAIL line"
        )
        if aborted:
            # Not the shell sentence: a mutant that aborts collection reads INVALID.
            emit(ABORTED_CANARY)
        elif silent:
            emit(SILENT_CANARY)

    def anchor(self, old: str) -> str:
        idx = self.original.find(old)
        line = self.original[:idx].count("\n") + 1
        text = next((x.strip() for x in old.split("\n") if x.strip()), "")[:100]
        return f"at {self.subject_rel}:{line}  `{text}`"

    def mutants(self, state: State) -> None:
        total = len(self.c.mutations)
        for n, (label, old, new) in enumerate(self.c.mutations, 1):
            head = f"[{n}/{total}]"
            count = self.original.count(old)
            if count != 1:
                state.counts["notapplied"] += 1
                emit(f"{head} {'NOT-APPLIED':<12} k=0  {label}")
                emit(f"{INDENT}the anchor occurs {count}x in the copy subject, need 1")
                continue
            mutated = self.original.replace(old, new, 1)
            parse = mutate.judge_mutant_parse(
                self.c.subject, self.original, mutated, self.control
            )
            if parse[0] in (mutate.UNPARSEABLE, mutate.CANNOT_RUN):
                state.counts["invalid"] += 1
                emit(f"{head} {'INVALID':<12} k=0  {label}")
                emit(f"{INDENT}{parse[0]}: {parse[1]}; the suite was NOT run")
                continue
            refresh_working_copy(self.master, self.work)
            Path(self.c.subject).write_text(
                mutated, encoding="utf-8", errors="surrogateescape"
            )
            digest = mutated.encode("utf-8", "surrogateescape")
            self.applied.append((n, label, hashlib.sha256(digest).hexdigest()))
            run = run_suite(self.argv, self.work, self.env, self.limit, self.runs_dir)
            self._resolve(state, head, n, label, old, run)

    def _resolve(self, state, head, n, label, old, run: RunResult) -> None:
        if run.rc is None:
            state.counts["timedout"] += 1
            emit(f"{head} {'TIMEOUT':<12} k=0  {label}")
            emit(
                f"{INDENT}did not finish within {self.limit:.0f}s; process group killed "
                "(a campaign's own timeout= is invisible here: pass --timeout)"
            )
            return
        if re.search(mutate.COLLECTION_ABORT_PATTERN, run.stdout, re.M):
            state.counts["invalid"] += 1
            emit(f"{head} {'INVALID':<12} k=0  {label}")
            emit(f"{INDENT}pytest's collection aborted, so no test ran")
            return
        reading = self.read(run.stdout)
        noticed = mutate.is_caught(run.rc, run.stdout)
        v = score(self.universe, self.canary_absent, reading, noticed)
        emit(f"{head} {v.status:<12} k={len(v.kill)}  {label}")
        if v.status == "SURVIVED":
            state.counts["survivors"] += 1
            emit(f"{INDENT}{self.anchor(old)}")
            _emit_dumped(reading)
            return
        if v.status == "KILLED-ALL":
            emit(f"{INDENT}(all {len(v.kill)} rows)")
        else:
            for row in v.kill:
                emit(f"{INDENT}| {row}")
        if reading.fails:
            emit(
                f"{INDENT}explained by {v.nF} FAIL lines (nF={v.nF}), shown as "
                "diagnostics:"
            )
            for line in reading.fails:
                emit(f"{INDENT}! {line}")
        if v.status == "UNATTRIBUTED":
            state.counts["unattributed"] += 1
            why = "noticed, no row vanished" if noticed else "rows vanished under rc 0"
            emit(f"{INDENT}unattributed: {why} (rc={run.rc})")
            for line in run.stderr.strip().split("\n")[-3:]:
                if line:
                    emit(f"{INDENT}! stderr: {line}")
            _emit_dumped(reading)
            return
        if v.status == "KILLED-ALL":
            emit(
                f"{INDENT}killed-all: the rows that cannot run when the subject is "
                "unparseable all died (collection or startup, not a check)"
            )
            _emit_dumped(reading)
            state.counts["killed_all"] += 1
            return
        if len(self.universe) == 1:
            emit(
                f"{INDENT}killed-all: n/a (a one-row suite: every kill kills every row)"
            )
        for flag in v.flags:
            emit(f"{INDENT}{flag}")
        if v.new_labels:
            emit(
                f"{INDENT}new_labels: {len(v.new_labels)} PASS labels outside the "
                "baseline (a diagnostic, not counted):"
            )
            for name in v.new_labels:
                emit(f"{INDENT}+ {name}")
        _emit_dumped(reading)
        if len(v.kill) == 1:
            state.counts["single_row"] += 1
        if v.flags:
            state.counts["incomplete"] += 1
        else:
            self.killed.append((n, frozenset(v.kill)))

    def duplicates(self, state: State) -> None:
        """Pairs of unflagged KILLED mutants with equal non-empty kill sets."""
        labels = {
            n: (label, old) for n, (label, old, _new) in enumerate(self.c.mutations, 1)
        }
        groups: dict = {}
        for n, kill in self.killed:
            groups.setdefault(kill, []).append(n)
        for kill, ns in groups.items():
            for i, a in enumerate(ns):
                for b in ns[i + 1 :]:
                    state.counts["identical_pairs"] += 1
                    emit(f"DUPLICATE [{a}] [{b}] (k={len(kill)})")
                    for m in (a, b):
                        label, old = labels[m]
                        emit(f"{INDENT}[{m}] {label}  {self.anchor(old)}")


def _emit_dumped(reading: Reading) -> None:
    """The nested-dump diagnostic, for every graded status that read a run."""
    if reading.dumped:
        emit(f"{INDENT}nested dumps: {reading.dumped} lines not read as rows")


def _leak_check(live_subject, live_sha, applied, state: State) -> None:
    """The live subject must not hold any mutant's text at the end."""
    now = _sha256(live_subject)
    if now == live_sha:
        emit("leak: none (the live subject is unchanged)")
        return
    hits = [f"[{n}] {label}" for n, label, sha in applied if sha == now]
    if hits:
        emit(
            f"ERROR  leak: the live subject holds the text of mutant {', '.join(hits)}"
        )
        state.error = True
        return
    emit("leak: none (the live subject changed during the run, matching no mutant)")


def _teardown(state: State, keep: bool) -> None:
    if state.tmp is None:
        return
    if keep:
        emit(
            f"copy: kept {state.tmp}  (master {state.tmp / 'master'}, "
            f"working copy {state.tmp / 'work'})"
        )
        return
    shutil.rmtree(state.tmp, ignore_errors=True)
    # Verified, never assumed: a normal or ERROR exit removes the temp directory (spec 3.2,
    # Teardown), so one that survives the removal is an ERROR naming it.
    if os.path.lexists(state.tmp):
        emit(f"ERROR  copy: NOT removed {state.tmp}")
        state.error = True
        return
    emit("copy: removed")


def _parse(argv):
    p = argparse.ArgumentParser(
        prog="kill-sets.py",
        description="Report each mutant's kill set, read on a fresh copy of the tree.",
    )
    p.add_argument("--campaign", required=True, help="the mutate_*.py campaign file")
    p.add_argument(
        "--timeout",
        type=float,
        help="seconds per suite run, the baseline included (default: derived)",
    )
    p.add_argument("--root", help="FIXTURE-ONLY: the tree to copy (default: this repo)")
    p.add_argument("--keep", action="store_true", help="keep the copies; print paths")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = _parse(argv)
    state = State()
    try:
        _run(args, state)
    except KillSetsError as exc:
        for why in exc.args:
            emit(f"ERROR  {why}")
        state.error = True
    except Exception:
        emit("ERROR  internal exception; no verdict was reached:")
        for line in traceback.format_exc().rstrip().splitlines():
            emit(line)
        state.error = True
    try:
        _teardown(state, args.keep)
    except Exception:
        emit("ERROR  teardown failed:")
        for line in traceback.format_exc().rstrip().splitlines():
            emit(line)
        state.error = True
    status, rc = verdict(state.counts, state.error)
    emit(result_line(state.counts, status, rc))
    return rc


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
# Script: kill-sets.py
# Purpose: Run a mutation campaign's mutants, one at a time or on --jobs N parallel copies of the tree, and report each mutant's kill set
# Usage: kill-sets.py --campaign scripts/tests/mutate_x.py [--timeout S] [--root DIR] [--keep] [--jobs N]
"""Report each mutant's KILL SET: the suite rows it made fail, read on a copy of the tree.

**Why this exists.** A campaign run through `scripts/lib/mutate.py` reports `caught=N
survived=0`, which reads the same whether the mutants are distinct, whether two are
duplicates, or whether one killed EVERY row (collection or startup died, not a check). Only
each mutant's set of failing rows tells them apart, and `mutate.py` keeps none. Every change
that followed CLAUDE.md's "compare KILL SETS" hand-rolled a one-off harness and lost it.

    kill-sets.py --campaign scripts/tests/mutate_x.py
    kill-sets.py --campaign scripts/tests/mutate_x.py --jobs 4

Run it through `run-long.sh` (a large campaign takes tens of minutes) with
`--expect '^RESULT: (PASS|FAIL|ERROR) rc=[0-9]+ mutants=[0-9]+ identical_pairs=[0-9]+
killed_all=[0-9]+'`, anchored at line start so a mutant label quoting the grammar cannot
match. A clean parallel run ends `PARALLEL-PASS`, which that pattern does not match (it
reads INDETERMINATE, and the artifact must then be read), so launch a parallel run with the
superset `--expect '^RESULT: (PASS|PARALLEL-PASS|FAIL|ERROR) rc=[0-9]+ mutants=[0-9]+
identical_pairs=[0-9]+ killed_all=[0-9]+'`, which keeps the anchoring and still matches the
verdict's shape rather than its passing value.

**How a row is identified.** A row is the name a suite prints when it PASSES; a kill is that
name's ABSENCE from the mutant's run. FAIL lines never identify rows (their detail varies too
much to match honestly); they only EXPLAIN absences by count, and print as diagnostics. The
universe `U` comes from the unmutated suite, run twice, and must be non-empty and stable.

**How the copies are kept honest.** A master copy `M` of the whole tree (with `.git` and
ignored files, without caches) pins the launch tree. Each worker grades on a working copy of
its own, at one fixed path: `W` at `--jobs 1`, `W0` to `W{N-1}` above 1. The campaign is
loaded from `W0`; for a worker above 0 every absolute path of the `SUITE` that points into
`W0` is moved onto that worker's copy, and an argument that still names `W0` after the move
is an ERROR. A copy is deleted and re-copied from `M` before every run, so nothing a mutant's
suite wrote reaches the next one. Every absolute path in the campaign's `SUITE` is judged by
the tree it resolves into: into the live tree or the master is an ERROR. Nothing runs `git`,
and nothing is written in the repo. Above 1 the tree is copied up to N+1 times, and the tool
refuses before copying anything when the free space under the temp directory is less than
twice N+1 times the tree's size.

**`--jobs N`** takes an integer from 1 to 8 (default 1, serial). Above 1, up to N mutants are
graded at once, one worker thread and one copy each; the worker count is capped by the number
of mutants that can run, and the `jobs:` line says what it came to. Only the main thread
prints, and each mutant's block is printed in mutant order whatever order they finish in.
`--jobs 1` prints no parallel line and no parallel field. Above 1 the header has a `parallel`
sentence in place of the `serial` one (both end `determinism NOT assessed`), a `jobs:` line
with the load average at launch and the CPU count, a `nested pools:` line when any of
`AUDIT_SHELL_JOBS`, `AUDIT_PYTEST_JOBS`, `AUDIT_SHELLCHECK_JOBS` or `AUDIT_TEST_JOBS` is set
(every suite inherits it, and N suites each starting a pool multiply the load), and a closing
`jobs: finished` line with the load average; a TIMEOUT block gains a line with the load
average and the advice to lower `--jobs` or raise `--timeout`. Do not launch above the
physical core count, and read `uptime` first. `--jobs` is no reason to skip `run-long.sh`: a
parallel campaign is still heavy work.

**The proof (above 1 worker).** Before the first mutant the tool runs the unmutated suite
on every worker's copy at once, to refuse a suite that cannot run concurrently. It takes R
rounds, R being the smallest number with C(R, floor(R/2)) at least E, the number of workers:
2, 3, 4, 4, 4, 5, 5 rounds for E = 2 to 8. Each round refreshes every copy from `M`, writes the
canary (the subject replaced by `{`) into the copies whose worker holds it that round, then
runs the suite once in every copy. Each worker holds the canary in some rounds and not in
others, and for every pair of workers some round has one pristine while the other holds it.
A holder must read as the serial canary did and a pristine worker as the serial baseline did
(green, same pass names); any other reading ends the run in an ERROR before the first mutant,
saying `the suite is not safe to run concurrently here`. The tool prints a header line, one
line per round (`proof: ran round r/R in Ns, judged after the last round`), and, once the last
round has ended and every run was judged right, the judged round lines, a coverage line and a
summary with the slowest pristine run against the serial one and the limit per suite run,
which the proof can raise (an explicit `--timeout` always wins). A refused proof prints only
its `ERROR` lines after the progress lines. With one effective worker it prints `proof: skipped (one
effective worker)`, with no runnable mutant `proof: skipped (no runnable mutants)`; at
`--jobs 1` it does not run.

What the proof vouches for is narrow. It is a check for the common failures (a sibling copy
read through the subject, a row label that embeds the copy path, a fixed lock path or port),
not a proof of isolation. The canary exercises only the rows whose verdict changes when the
subject is broken, so a read of another copy's subject by any other row is invisible to it,
and so is any read of a file that is not the subject. Every run that ran the proof says how
much it saw, in one coverage line: `proof: the canary exercises C of U rows; a read of
another copy's subject by the other U-C rows is not visible to this proof`, or `proof: the
canary exercises U of U rows`, or, when the canary aborted the suite's collection (typically a
pytest suite that imports the subject), `proof: the canary aborted collection, so no row is
judged singly; a read of another copy's subject is visible only where it turns a pristine run
red`.

**Failures while grading.** An exception while one mutant is graded, or a failed re-creation
of its worker's copy, is a `DIED` block and costs that mutant. A failed re-creation also
retires the worker; when no worker is left, the mutants still queued read `DIED` with `no
worker left: every worker retired after a failed refresh`. Any `DIED` ends the run in an
ERROR, at `--jobs 1` too.

**Stopping.** SIGTERM, SIGHUP and SIGINT are handled, except one that arrived ignored (an
ignored signal is left ignored). The handler only records the signal. The main thread then
kills every suite process group the tool started and, when it acts on the stop at one of
the tool's check points, prints `STOP   {SIGNAME} received, copy at {path}`, waits up to 20 s
in all for the worker threads, removes the temp directory (it is kept under `--keep`, or when a worker
thread did not stop in that time, and the `interrupted by` line then names that worker),
prints `ERROR  interrupted by {SIGNAME}: {N} suite groups killed, copy removed` (or `copy
kept at {path}`) in place of the `RESULT:` line, and dies of the signal, so the exit status is
the signal's. The first check point comes after the temp directory is created, so a stop
that first shows up as an error from a child the signal also killed (the discovery child
under `run-long.sh`, where the gate signals the whole process group), or as a refusal before
the directory exists, ends in the `interrupted by` line with no `STOP` line before it. A copy
in flight is cut short at its next directory (the main thread's own copies too): the check
runs once per directory, so a worker mid-copy first copies the files of the directory it is
in, and the deletion of the old copy that precedes each copy is not cut short; the discovery of the campaign is a helper
child bounded at 60 s, and a stop recorded during it is acted on when it ends. A second
signal during that cleanup is counted and ignored; a signal after the `RESULT:` line is
printed changes nothing. When the output has gone away by then (a hangup from a dead
terminal), the cleanup and the exit by the signal still happen, silently. After an internal
error that is not a signal, the tool also kills the suites still running before it removes
the copies, and keeps them when a worker thread did not stop.

Under `run-long.sh` the gate sends SIGTERM to the job's process group and SIGKILL about 10 s
later. The tool prints the `STOP` line as soon as the suites are killed, before it joins its
workers or removes anything (a stop with no `STOP` line, described above, has none here
either). If the removal outlasts that time, the SIGKILL ends the tool
mid-removal: the artifact then holds the `STOP` line (the `leak:` and `jobs: finished` lines
may follow it) and no `interrupted by` line, `run-long.sh --status` reads DIED, and the
directory the `copy:` and `STOP` lines name is left behind, possibly partly removed. No suite
survives. Remove it with the leftover recipe below. A wide `--jobs` or a large tree makes
this more likely; no removal time is guaranteed.

**What a stop leaves.** A clean finish, ERROR or FAIL included, leaves no suite running and
removes the `kill-sets.` temp directory; one that cannot be removed is kept, and the run ends
in an ERROR (`copy: NOT removed`). A handled signal leaves no suite either, and the directory
is removed or kept as above. Only SIGKILL (or `kill -9` of the tool) or power loss strands
anything: the temp directory (the master and the working copies; named by the first `copy:`
line, printed the moment the directory exists) and up to N suites still running in their own
sessions. Nothing enforces a limit once the tool is gone (measured: with `--timeout 5` a suite
was alive 10 s after it started), so each runs until it ends on its own, and a hung suite
does not end. A SIGKILL that lands after the `STOP` line strands only the directory, since
the suites are dead by then. Under `run-long.sh` only SIGTERM reaches the tool, because HUP
and INT arrive ignored there and an ignored signal stays ignored; a HUP or INT sent to it
stops nothing, and the run goes on to its `RESULT:` line. Find the leftovers before editing
the tree or starting another run:

    ls -d "${TMPDIR:-/tmp}"/kill-sets.*      # the leftover directory
    lsof +D <that directory>                 # any process with its cwd or a file in it
    pgrep -fl kill-sets                      # processes naming the copy path

`pgrep` alone can print nothing while a suite still runs: it matches only command lines that
carry the copy path, and a campaign whose `SUITE` names its tests by a relative path runs with
no such argument. `lsof +D` sees the suite through its working directory, which is in the copy;
read what it prints, not its exit status (measured: rc 1 while it listed the live suite).

**Verdict.** The last line: `RESULT: PASS|FAIL|ERROR rc=0|1|2` followed by fixed counts.
ERROR outranks FAIL outranks PASS. Above 1 a clean run ends `PARALLEL-PASS` (rc 0) in place
of `PASS`, FAIL and ERROR keep their words, and the line gains `died=N` and `jobs=N` after
`single_row`. `died=N` also appears at `--jobs 1`, but only when it is not zero; `jobs=N` only
above 1.

**How far to trust a parallel run.** A `PARALLEL-PASS` is a lead and not a verdict of record.
A parallel result has no strong side, because load changes readings in both directions. It
can hide a survivor (a true survivor plus one row that fails only under load reads KILLED, so
`survivors=0` is as soft as `identical_pairs=0`), and it can create a false duplicate pair
(two kill sets that differ by one row read equal when load flips that row in one of them). An
interpreter that fails to start under load can make a suite lose rows with no FAIL line (read
as `incomplete`) or die whole (read as `killed_all`). What load cannot touch is the pre-pass:
NOT-APPLIED and INVALID-by-parse are decided in the main thread and are the same at every
`--jobs`; `died` and `timedout` say a mutant could not be read, not what it read. So a
parallel finding, a pass included, is settled by a serial run, unless parity with serial was
measured for that campaign, at that width, on that tree. A measured parity run is a sample,
not a proof of isolation: whether one copy's read of another's state changes a result depends
on what the other copy held at that instant. The `determinism NOT assessed` line of the header
stays true at every `--jobs` value, since each mutant is read once.

`--root` is for test fixtures only; the default root is this file's own repository, resolved
through `realpath` (a symlinked `~/.claude/scripts/` resolves into the production checkout).
"""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import math
import os
import queue
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
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

# Not a flag: a tree over this many MB is refused before anything is copied.
MAX_COPY_MB = 1024
CACHE_DIRS = ("__pycache__", ".pytest_cache", ".ruff_cache")
DISCOVERY_SECONDS = 60
POLL_SECONDS = 0.5  # the longest the main thread blocks before it looks again
JOIN_SECONDS = 20  # the longest `Pool.close` waits for the worker threads to stop
MAX_JOBS = 8
DISK_FACTOR = 2
PARALLEL_PASS = "PARALLEL-PASS"
# The audit pools a suite may start for itself, in the order the header names them.
NESTED_POOLS = (
    "AUDIT_SHELL_JOBS",
    "AUDIT_PYTEST_JOBS",
    "AUDIT_SHELLCHECK_JOBS",
    "AUDIT_TEST_JOBS",
)

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
PARALLEL = (
    "parallel, one run per mutant on its own copy, {jobs} workers: kill sets are read under "
    "concurrent load, and a row that fails only under load reads as a kill; determinism "
    "NOT assessed"
)
PROOF_REFUSAL = (
    "the suite is not safe to run concurrently here (a shared path or port, a label that "
    "carries the copy path, or a suite that reads another copy); re-run with --jobs 1"
)
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
    "died": "ERROR",
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


class RefreshFailed(Exception):
    """A worker's copy could not be re-created before a mutant's suite ran.

    The `OSError` from the delete or the copy is carried as `__cause__`.
    """


class Stopped(BaseException):
    """A worker saw the stop flag (or the closed registry) and gave up its task.

    A `BaseException`, so no `except Exception` of a worker, `_run` or `main` can turn it into
    a DIED block or an `internal exception`: the worker loop catches it by name and returns.
    """


class StopRequested(BaseException):
    """Raised by the MAIN thread's own code once it has acted on a recorded signal."""

    def __init__(self, signum: int) -> None:
        super().__init__(signum)
        self.signum = signum


class Campaign(NamedTuple):
    subject: Path
    suite: list
    mutations: list
    mutate_path: str | None


class State:
    """What the run has established so far; the RESULT line is built from it on any exit."""

    def __init__(self) -> None:
        self.counts = {"mutants": 0, **{f: 0 for f in FIELDS}, "died": 0}
        self.jobs = 1  # the requested worker count, set once `--jobs` is validated
        self.error = False
        self.tmp: Path | None = None
        self.stuck: list = []  # indices of worker threads that outlived the join bound
        self.stuck_told = False  # an `ERROR` line already named them


def emit(line: str) -> None:
    """The one place every output line goes through.

    A failed write is an error and propagates, as it always did, until a stop has been
    recorded. After that the run is ending by the signal and must get there: the hangup that
    stops it normally comes with a terminal whose reader is gone, and a raise from the leak
    check's line would replace `StopRequested`, so the cleanup and the exit by the signal
    would never run. The first failed write then points descriptor 1 at the null device,
    which also lets the data left in the stream's buffer flush without a second failure.
    """
    try:
        print(line, flush=True)
    except (OSError, ValueError):
        if STOP["signum"] is None:
            raise
        _silence_stdout()


def _silence_stdout() -> None:
    """Point descriptor 1 at the null device; whatever it was is no longer written."""
    try:
        null = os.open(os.devnull, os.O_WRONLY)
        try:
            os.dup2(null, 1)
        finally:
            os.close(null)
    except OSError:
        pass


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


def _nested_pools_header() -> list[str]:
    """One header line naming the audit pools this process carries into every suite, if any."""
    env = os.environ
    carried = [f"{name}={env[name]}" for name in NESTED_POOLS if name in env]
    if not carried:
        return []
    return [f"nested pools: {' '.join(carried)} (inherited by every suite)"]


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


def worker_copy_path(tmp, k) -> Path:
    """Worker k's working copy: `<tmp>/work` for the canonical copy, `<tmp>/pool/<k>` above it.

    No other path the tool creates under `tmp` starts with the text `<tmp>/work`, so a token
    that was re-based onto a worker copy never reads as a residue of the canonical one.
    """
    if k == 0:
        return Path(tmp) / "work"
    return Path(tmp) / "pool" / str(k)


def _canonical_spellings(canonical) -> list[str]:
    """The canonical copy's logical text and its realpath text, each once, no trailing slash."""
    texts = [str(canonical).rstrip("/"), os.path.realpath(canonical).rstrip("/")]
    return list(dict.fromkeys(texts))


def _rebase_value(value: str, canonical, target) -> str:
    """One extracted path value, moved from the canonical copy onto `target`; else unchanged."""
    if not _inside(value, canonical):
        return value
    logical, real = str(canonical).rstrip("/"), os.path.realpath(canonical)
    spellings = ((logical, str(target).rstrip("/")), (real, os.path.realpath(target)))
    if ".." not in value.split("/"):
        for old, new in spellings:
            if value == old or value.startswith(old + "/"):
                return new + value[len(old) :]
    rel = os.path.relpath(os.path.realpath(value), real)
    base = os.path.realpath(target)
    return base if rel == "." else os.path.join(base, rel)


def rebase_argv(argv, canonical, target) -> list:
    """A new argv with every path value inside `canonical` moved onto the copy at `target`.

    A value keeps the spelling it had: the logical prefix becomes the target's logical text,
    the realpath prefix the target's realpath, and any other form that still resolves inside
    the canonical copy (a `..` component, a symlinked directory) is rebuilt from the target's
    realpath. Text before the value (`X=`) is kept and a token carrying no path is unchanged.
    """
    rebased = []
    for token in argv:
        values = _path_values(token)
        if values:
            value = values[0]
            head = token[: len(token) - len(value)]
            token = head + _rebase_value(value, canonical, target)
        rebased.append(token)
    return rebased


def residue_errors(argv, canonical, k) -> list[str]:
    """One message per token that still contains either spelling of the canonical copy as text."""
    spellings = _canonical_spellings(canonical)
    return [
        f"copy unproven: worker {k}: SUITE token {token!r} "
        f"still names the canonical copy {canonical}"
        for token in argv
        if any(text in token for text in spellings)
    ]


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
        root,
        master,
        symlinks=True,
        ignore=_cancellable(shutil.ignore_patterns(*CACHE_DIRS)),
    )
    return master


def parse_jobs(text) -> int:
    """The worker count `--jobs` asked for, or a refusal: an integer from 1 to `MAX_JOBS`."""
    value = int(text) if re.fullmatch(r"[0-9]+", str(text)) else 0
    if not 1 <= value <= MAX_JOBS:
        raise KillSetsError(f"--jobs {text}: expected an integer from 1 to {MAX_JOBS}")
    return value


def disk_gate(jobs, tmp_base, tree_bytes) -> None:
    """Refuse, before any copy, when the free space cannot hold the master and every worker."""
    size = tree_bytes / 1048576
    free = shutil.disk_usage(tmp_base).free / 1048576
    need = DISK_FACTOR * (jobs + 1) * size
    if free < need:
        raise KillSetsError(
            f"jobs={jobs}: {free:.0f} MB free under {tmp_base}, less than {DISK_FACTOR} x "
            f"({jobs} + 1) x {size:.0f} MB = {need:.0f} MB; nothing was copied"
        )


def refresh_working_copy(master, work) -> None:
    """Delete `work` and copy it again from the master: no run sees another run's writes."""
    if os.path.lexists(work):
        shutil.rmtree(work)
    shutil.copytree(master, work, symlinks=True, ignore=_cancellable())


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


# The signal numbers the handler recorded, and what the main thread did about them. The
# handler stores `signum` once and counts every later signal in `count`; it does nothing
# else (no lock, no print, no kill, no raise). `killed`, the number of suite groups the main
# thread killed, and `tmp`, the run's temp directory (None until it exists), are written by
# the main thread alone: `killed` set in `check_stop` and added to in `_run`'s `finally` when
# the run ends any way but a clean finish, `tmp` set in `_run`.
STOP: dict = {"signum": None, "count": 0, "killed": 0, "tmp": None}


class Registry:
    """The suite process groups alive right now, under one `RLock`, with a `closed` flag.

    `run_suite` adds a group at spawn and removes it after the kill, both under the lock, and
    reaps the leader only after the removal: an id in `groups` therefore always belongs to a
    group whose leader is an unreaped zombie, so the id cannot name anyone else's group.
    Once `closed` is set no new suite is spawned.
    """

    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.groups: dict = {}  # group id -> has `kill_all` already killed it?
        self.closed = False

    def kill_all(self) -> int:
        """Close the registry and SIGKILL every group in it; return how many were new.

        A group an earlier call killed is killed again (harmless) but not counted again, so
        the total over several calls is the number of distinct groups killed.
        """
        with self.lock:
            self.closed = True
            fresh = 0
            for pgid in sorted(self.groups):
                _kill_group(pgid)
                if not self.groups[pgid]:
                    self.groups[pgid] = True
                    fresh += 1
            return fresh


REGISTRY = Registry()


def _note_signal(signum, frame) -> None:
    """The signal handler: record the number and return."""
    if STOP["signum"] is None:
        STOP["signum"] = signum
    STOP["count"] += 1


def _install_handlers() -> None:
    """Handle SIGTERM, SIGHUP and SIGINT, except a signal that arrived ignored."""
    for sig in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
        if signal.getsignal(sig) != signal.SIG_IGN:
            signal.signal(sig, _note_signal)


def _stop_flagged() -> bool:
    """Was a stop recorded, or the registry closed (by a stop or by an error exit)?"""
    return STOP["signum"] is not None or REGISTRY.closed


def _stop_if_flagged() -> None:
    """WORKER side: give the task up when a stop was recorded or the registry was closed."""
    if _stop_flagged():
        raise Stopped


def _cancellable(inner=None):
    """An `ignore` callback for `shutil.copytree` that looks for a stop once per directory.

    On the main thread a recorded stop is acted on at once (`check_stop`); on a worker
    thread it raises `Stopped`, so a copy in flight ends at its next directory instead of
    holding up the join. It returns what `inner` returns for the directory (nothing is
    ignored without one), so the tree it copies is the same as without it.
    """

    def ignore(src, names):
        if threading.current_thread() is threading.main_thread():
            check_stop()
        elif _stop_flagged():
            raise Stopped
        return inner(src, names) if inner is not None else ()

    return ignore


def _stop_notice(signum: int) -> str:
    """The early line: which signal arrived and where the copy is, with no verdict in it."""
    where = f"copy at {STOP['tmp']}" if STOP["tmp"] is not None else "no copy made yet"
    return f"STOP   {signal.Signals(signum).name} received, {where}"


def check_stop(pool=None) -> None:
    """MAIN thread only: act on a recorded signal, else return at once.

    Acting is: kill every registered suite group, print the one `STOP` line (the groups are
    dead by then, and nothing has been joined or removed yet, so a reader of an artifact the
    run's own killer cut short still learns which signal arrived and where the copy is),
    join the worker threads within `JOIN_SECONDS` in total (`Pool.close`; a later `close`
    returns that same answer), then raise `StopRequested` from this thread's own code. The
    outcomes of work still in flight are never scored: nothing releases a block after this
    raises. The temp directory is read from `STOP["tmp"]`, which `_run` sets once it exists,
    so the signature and every call site stay as they were.
    """
    signum = STOP["signum"]
    if signum is None:
        return
    killed = REGISTRY.kill_all()
    emit(_stop_notice(signum))
    STOP["killed"] = killed
    if pool is not None:
        pool.close()
    raise StopRequested(signum)


def run_suite(argv, cwd, env, limit, runs_dir) -> RunResult:
    """One suite run in its own session; both streams to fresh files; wait on the PROCESS.

    Never a pipe: a backgrounded grandchild holds a pipe open after the suite exits, so a
    pipe read would wait for it. The files are opened fresh under unique names, so a late
    write by an escaped grandchild cannot land in the next run's file. The group recorded
    at spawn is killed after EVERY run, normal or timed out, while the leader is still an
    unreaped zombie holding it; only then is the leader reaped. The spawn and the
    registration are one step under the registry lock, which refuses (`Stopped`) once a stop
    was recorded or the registry was closed, so no suite starts after the main thread has
    killed the live ones.
    """
    fd_out, out_path = tempfile.mkstemp(prefix="run.", suffix=".out", dir=runs_dir)
    fd_err, err_path = tempfile.mkstemp(prefix="run.", suffix=".err", dir=runs_dir)
    with os.fdopen(fd_out, "wb") as fo, os.fdopen(fd_err, "wb") as fe:
        started = time.monotonic()
        with REGISTRY.lock:
            if STOP["signum"] is not None or REGISTRY.closed:
                raise Stopped
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
            REGISTRY.groups[pgid] = False
        try:
            timed_out = not _exited(proc.pid, limit)
            if timed_out:
                _kill_group(pgid)
                _exited(proc.pid, None)
            elapsed = time.monotonic() - started
        finally:
            with REGISTRY.lock:
                _kill_group(pgid)
                REGISTRY.groups.pop(pgid, None)
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


class Worker(NamedTuple):
    """One working copy and everything a suite run in it reads, writes or runs in."""

    index: int  # 0 is the canonical copy
    work: Path
    subject: Path  # the subject inside `work`
    argv: list  # the argv this worker runs (pytest armed)


def build_workers(tmp, canonical, subject_rel, argv, count) -> list:
    """The `count` workers: the canonical copy as worker 0, `pool/k` copies for k >= 1.

    Worker 0 keeps the discovered argv. Every other worker's argv is re-based onto its own
    copy, and a token that still names the canonical copy afterwards is refused, all in ONE
    error and before anything runs.
    """
    workers = [Worker(0, canonical, canonical / subject_rel, list(argv))]
    residue: list = []
    for k in range(1, count):
        work_k = worker_copy_path(tmp, k)
        subject_k = work_k / subject_rel
        argv_k = rebase_argv(argv, canonical, work_k)
        residue += residue_errors(argv_k, canonical, k)
        workers.append(Worker(k, work_k, subject_k, argv_k))
    if residue:
        raise KillSetsError(*residue)
    return workers


class Outcome(NamedTuple):
    """One mutant's graded block: what to print and count, built without printing."""

    n: int  # mutant number, 1-based
    # The status word: KILLED, KILLED-ALL, SURVIVED, UNATTRIBUTED, TIMEOUT, INVALID,
    # NOT-APPLIED or DIED.
    status: str
    kill: tuple  # the kill set as sorted row names; () when none
    lines: tuple  # the block's output lines, exactly as printed
    deltas: tuple  # ((field, int), ...) increments for State.counts
    killed: frozenset | None  # the kill set of an unflagged KILLED mutant, else None


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


def verdict(counts, error: bool, jobs: int = 1) -> tuple[str, int]:
    gates = {FIELD_SEVERITY[f] for f in FIELD_SEVERITY if counts[f]}
    if error or "ERROR" in gates:
        return "ERROR", 2
    if "FAIL" in gates:
        return "FAIL", 1
    return (PARALLEL_PASS if jobs > 1 else "PASS"), 0


def result_line(counts, status: str, rc: int, jobs: int = 1) -> str:
    """Today's ten fields; `died=` joins them when non-zero or parallel, `jobs=` only above 1."""
    head = f"RESULT: {status} rc={rc} mutants={counts['mutants']}"
    line = head + "".join(f" {f}={counts[f]}" for f in FIELDS)
    if counts["died"] or jobs > 1:
        line += f" died={counts['died']}"
    if jobs > 1:
        line += f" jobs={jobs}"
    return line


def proof_rounds(count) -> int:
    """The rounds of the worker proof: the smallest R with C(R, R // 2) >= `count` workers."""
    rounds = 1
    while math.comb(rounds, rounds // 2) < count:
        rounds += 1
    return rounds


def holds_canary(k, r, rounds) -> bool:
    """Does worker k's copy hold the canary in round r of `rounds`?

    Worker k's code is the k-th subset of R // 2 rounds, in lexicographic order. All codes
    have one weight, so none contains another: for any two workers a and b some round has a
    pristine and b holding the canary, and every worker holds it in some round and is
    pristine in another.
    """
    return r in tuple(itertools.combinations(range(rounds), rounds // 2))[k]


def derive_proof_limit(serial_elapsed, pristine_elapsed, timeout) -> float:
    """The per-mutant limit once the proof ran: from the slowest of the serial and pristine runs."""
    return mutate.derive_timeout(max([serial_elapsed, *pristine_elapsed]), timeout)


def _sha256(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_in_copy(target, work, text, what, errors="strict") -> None:
    """The one writer of a subject in a copy; refused unless `target` resolves inside `work`."""
    if not _inside(target, work):
        raise KillSetsError(
            f"copy unproven: the {what} target {target} does not resolve "
            f"inside the working copy {work}; nothing was written"
        )
    Path(target).write_text(text, encoding="utf-8", errors=errors)


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


def _load_average() -> str:
    return " ".join(f"{x:.2f}" for x in os.getloadavg())


def _run(args, state: State) -> None:
    default_root = Path(os.path.realpath(__file__)).parent.parent
    root = Path(os.path.realpath(args.root or default_root))
    emit(
        f"kill-sets: campaign {args.campaign}  root {root}"
        + ("  (--root: fixture-only)" if args.root else "")
    )
    jobs = parse_jobs(args.jobs)
    state.jobs = jobs
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
    STOP["tmp"] = state.tmp
    # Named the moment it exists, before anything is copied into it: a run killed at any
    # later point leaves THIS directory, and every other scratch the run makes is inside it.
    if jobs == 1:
        emit(f"copy: {state.tmp}  (master M, working copy W re-created per mutant)")
    else:
        emit(
            f"copy: {state.tmp}  (master M, working copies W0..W{jobs - 1} "
            "re-created per mutant)"
        )
    master = state.tmp / "master"
    work = worker_copy_path(state.tmp, 0)
    pool_root = state.tmp / "pool"
    for a, b in (
        (work, root),
        (master, root),
        (work, master),
        (pool_root, root),
        (pool_root, master),
        (pool_root, work),
    ):
        if _inside(a, b) or _inside(b, a):
            raise KillSetsError(f"copy unproven: {a} and {b} overlap")
    if jobs > 1:
        disk_gate(jobs, tmp_base, _tree_bytes(root))
    check_stop()
    master = make_master(root, state.tmp)
    check_stop()
    size_mb = _tree_bytes(master) / 1048576
    if jobs == 1:
        emit(f"copy: size {size_mb:.0f} MB (master; W is the same)")
    else:
        emit(
            f"copy: size {size_mb:.0f} MB (master; each W is the same; {jobs + 1} copies)"
        )

    refresh_working_copy(master, work)
    c = load_campaign(work, work / rel, scratch_parent=state.tmp)
    check_stop()
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
    emit(SERIAL if jobs == 1 else PARALLEL.format(jobs=jobs))

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

    # Classified once, in the main thread, so the effective worker count is known before any
    # worker copy is described. The discovery's own subject path serves the parse check.
    first = Worker(0, work, Path(c.subject), argv)
    g = Grading(
        c, master, state.tmp / "runs", subject_rel, original, control, [first], jobs
    )
    runnable = g.classify()
    check_stop()
    effective = min(jobs, runnable)
    workers = build_workers(state.tmp, work, subject_rel, argv, max(1, effective))
    g.workers = workers
    if jobs > 1:
        emit(
            f"jobs: {jobs} requested, {effective} workers ({runnable} runnable mutants); "
            f"load average at launch {_load_average()} on {os.cpu_count()} CPUs"
        )
        for line in _nested_pools_header():
            emit(line)
    pool = Pool(workers)
    # However this block ends, the pool is closed and then the leak check runs; neither
    # replaces the exception in flight (a stop, an ERROR or an internal exception): a
    # failure of the leak check itself is one more ERROR line. Before the pool is closed, an
    # exit that is not a clean finish kills every suite group still registered, so a
    # sibling worker's suite cannot outlive the copies that are removed under it; a clean
    # finish has none left to kill. Killing closes the registry, which is also what ends a
    # worker's loop: it takes no further mutant once the registry is closed.
    finished = False
    try:
        check_stop(pool)
        g.runs_dir.mkdir()
        g.baseline(args.timeout, pool)
        check_stop(pool)
        g.canary(pool)
        check_stop(pool)
        g.proof(pool, state)
        check_stop(pool)
        g.run_mutants(state, pool)
        check_stop(pool)
        g.duplicates(state)
        finished = True
    finally:
        if not finished:
            # Added to the total `check_stop` may already hold: only new groups count.
            STOP["killed"] += REGISTRY.kill_all()
        alive = pool.close()
        state.stuck = alive
        # A recorded signal is reported by the one interrupted line, which names the
        # workers; any other exit prints its own line here.
        if alive and STOP["signum"] is None:
            emit(
                f"ERROR  worker thread(s) {', '.join(map(str, alive))} did not stop "
                f"within {JOIN_SECONDS}s"
            )
            state.error = True
            state.stuck_told = True
        try:
            _leak_check(live_subject, live_sha, g.applied, state)
        except Exception:
            emit("ERROR  the leak check itself failed:")
            for line in traceback.format_exc().rstrip().splitlines():
                emit(line)
            state.error = True
        if jobs > 1:
            emit(f"jobs: finished; load average {_load_average()}")


class Ticket:
    """One task handed to a worker thread; the main thread waits on `done`."""

    def __init__(self, fn) -> None:
        self.fn = fn
        self.done = threading.Event()
        self.value = None
        self.error: BaseException | None = None


class Pool:
    """One daemon thread per worker, each looping on its own inbox until `close`.

    Only the tasks handed to `submit` run on a worker thread; they return values or raise,
    and never print. The main thread alone calls `wait`, `call` and `close`.
    """

    def __init__(self, workers: list) -> None:
        self.workers = workers
        # Set after every finished ticket (and by `Grading` after every stored outcome),
        # so the main thread wakes when there is something to look at.
        self.activity = threading.Event()
        self._closed: list | None = None
        self._inboxes = {w.index: queue.Queue() for w in workers}
        self._threads = {
            w.index: threading.Thread(
                target=self._loop,
                args=(w.index,),
                name=f"kill-sets-worker-{w.index}",
                daemon=True,
            )
            for w in workers
        }
        for thread in self._threads.values():
            thread.start()

    def _loop(self, index: int) -> None:
        inbox = self._inboxes[index]
        while True:
            ticket = inbox.get()
            if ticket is None:
                return
            try:
                ticket.value = ticket.fn()
            except BaseException as exc:  # carried to the main thread, never lost here
                ticket.error = exc
            finally:
                ticket.done.set()
                self.activity.set()

    def submit(self, index: int, fn) -> Ticket:
        """Queue `fn()` on worker `index`'s thread."""
        ticket = Ticket(fn)
        self._inboxes[index].put(ticket)
        return ticket

    def wait(self, tickets, poll=POLL_SECONDS) -> list:
        """MAIN thread only: poll until every ticket is done; re-raise the first error seen."""
        while True:
            # One read of every `done` per pass drives both decisions. A ticket assigns its
            # error before it sets `done`, so a ticket read as done has its error visible.
            finished = [t for t in tickets if t.done.is_set()]
            # After the snapshot: a worker that gave up on a stop did so after the flag
            # was set, so this read sees it, and a ticket that ended on it is never read as
            # a finished one.
            check_stop(self)
            for ticket in finished:
                if ticket.error is not None:
                    raise ticket.error
            pending = [t for t in tickets if t not in finished]
            if not pending:
                return [t.value for t in tickets]
            pending[0].done.wait(poll)

    def call(self, index: int, fn):
        """Run `fn()` on worker `index`'s thread and return its value (MAIN thread only)."""
        return self.wait([self.submit(index, fn)])[0]

    def close(self, bound=JOIN_SECONDS) -> list:
        """Stop every inbox and join the threads; return the indices still alive.

        A second call returns the first call's result and neither signals nor joins again.
        """
        if self._closed is not None:
            return self._closed
        for inbox in self._inboxes.values():
            inbox.put(None)
        deadline = time.monotonic() + bound
        for thread in self._threads.values():
            thread.join(max(0.0, deadline - time.monotonic()))
        self._closed = [i for i, thread in self._threads.items() if thread.is_alive()]
        return self._closed


class Grading:
    """The baseline, the mutant loop and the duplicate report over the workers' copies."""

    def __init__(
        self, c, master, runs_dir, subject_rel, original, control, workers, jobs=1
    ):
        self.c, self.master, self.runs_dir = c, master, runs_dir
        self.jobs = jobs  # the requested worker count
        self.subject_rel, self.original, self.control = subject_rel, original, control
        self.workers: list = workers  # worker 0 is the canonical copy
        self.env = _child_env()
        self.universe: frozenset = frozenset()
        self.canary_absent: frozenset = frozenset()
        self.limit = 0.0
        # The explicit --timeout, which wins over every derived limit.
        self.timeout = None
        self.serial_elapsed = 0.0  # the slower of the two serial baseline runs
        self.canary_aborted = False  # did the serial canary abort collection?
        self.applied: list = []  # (n, label, sha256 of the mutated text)
        self._applied_lock = threading.Lock()
        self.killed: list = []  # (n, kill) of unflagged KILLED mutants
        self._pending: queue.Queue = queue.Queue()  # runnable mutants, in mutant order
        self._results: dict = {}  # n -> Outcome, in completion order
        self._results_lock = threading.Lock()
        self._runnable: int | None = None  # set once by `classify`
        self._next_n = 1  # the next block number due for release
        self._activity = threading.Event()

    def _fresh_copy(self, worker: Worker, prepare=None) -> None:
        _stop_if_flagged()  # a stop must not start a copy it would then wait for
        refresh_working_copy(master=self.master, work=worker.work)
        if prepare is not None:
            prepare()

    def _fresh_run(self, worker: Worker, limit, prepare=None) -> RunResult:
        self._fresh_copy(worker, prepare)
        return run_suite(worker.argv, worker.work, self.env, limit, self.runs_dir)

    def read(self, stdout: str, argv) -> Reading:
        """The ONE format the argv arms: pytest rows for armed pytest, else shell rows."""
        return read_pytest(stdout) if is_pytest(argv) else read_shell(stdout)

    def _gate(self, run: RunResult, limit, argv) -> Reading:
        """One baseline run must be green, name rows, not abort, and not repeat a name."""
        suite = shlex.join(argv)
        reading = self.read(run.stdout, argv)
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

    def baseline(self, timeout, pool: Pool) -> None:
        cap = timeout if timeout is not None else mutate.BASELINE_TIMEOUT_SECONDS
        w = self.workers[0]
        first = pool.call(w.index, lambda: self._fresh_run(w, cap))
        u1 = self._gate(first, cap, w.argv).passed
        second = pool.call(w.index, lambda: self._fresh_run(w, cap))
        u2 = self._gate(second, cap, w.argv).passed
        if u1 != u2:
            raise KillSetsError(
                f"the suite's pass names differ between the two baseline runs: only in "
                f"run 1: {sorted(u1 - u2)}; only in run 2: {sorted(u2 - u1)}; suite "
                f"{shlex.join(w.argv)} (volatile labels; fix them in the suite)"
            )
        self.universe = u1
        elapsed = max(first.elapsed, second.elapsed)
        self.timeout, self.serial_elapsed = timeout, elapsed
        self.limit = mutate.derive_timeout(elapsed, timeout)
        emit(
            f"BASELINE green rc=0 {elapsed:.1f}s rows={len(u1)} (run twice, universes "
            f"equal) (limit {self.limit:.0f}s)"
        )

    def canary(self, pool: Pool) -> None:
        """One run with the copy's subject replaced by `{`: the suite must notice it."""
        w = self.workers[0]
        run = pool.call(
            w.index,
            lambda: self._fresh_run(w, self.limit, prepare=self._canary_writer(w)),
        )
        if run.rc is None:
            raise KillSetsError(
                f"the canary run timed out after {self.limit:.0f}s (raise --timeout)"
            )
        aborted = re.search(mutate.COLLECTION_ABORT_PATTERN, run.stdout, re.M)
        self.canary_aborted = aborted is not None
        reading = self.read(run.stdout, w.argv)
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

    def _canary_writer(self, worker: Worker):
        """A callable that replaces the worker's own subject by the canary text."""
        return lambda: write_in_copy(
            worker.subject, worker.work, CANARY_TEXT + "\n", "canary"
        )

    def proof(self, pool: Pool, state: State) -> None:
        """Run the unmutated suite on every worker copy at once, in barrier-sequenced rounds.

        Round r first refreshes every copy from the master and writes the canary into the
        copies whose worker holds it (worker k's subset of the rounds, see `holds_canary`),
        then runs the suite once in every copy. The MAIN thread sequences both steps: no suite of the round starts before every
        copy is written, and no copy is touched for the next round before every suite of
        this one has exited. A holder must read as the serial canary did and a pristine
        worker as the serial baseline did; any other reading ends the run before the first
        mutant.

        What it can see, and what it cannot. No worker's code contains another's, so for
        every ordered pair (a, b) some round has a pristine and b holding the canary: a read
        of b's subject by a then turns a's pristine run red, whatever a's own copy holds in
        the rounds where it is the holder (a's own canary can mask the read there). That
        works for the rows the canary exercises: the rows whose verdict changes under `{`
        (the serial canary's absent set C), or, when the canary aborts collection, a read
        that turns the run red. A row outside C answers the same under `{` and pristine in
        every round, so a read of another copy's subject by such a row is NOT visible here,
        and neither is any read of a file that is not the subject. The coverage line says
        how much the canary exercises.
        """
        if state.jobs == 1:
            return
        effective = min(state.jobs, self.classify())
        if effective == 0:
            emit("proof: skipped (no runnable mutants)")
            return
        if effective == 1:
            emit("proof: skipped (one effective worker)")
            return
        workers = pool.workers[:effective]
        count = len(workers)
        total = proof_rounds(count)
        rounds = []
        emit(
            f"proof: {total} rounds x {count} workers, each about one suite run; "
            "nothing is judged until the last round ends"
        )
        for r in range(total):
            started = time.monotonic()
            prepared = [
                pool.submit(
                    w.index,
                    lambda w=w, r=r: self._fresh_copy(
                        w,
                        self._canary_writer(w)
                        if holds_canary(w.index, r, total)
                        else None,
                    ),
                )
                for w in workers
            ]
            pool.wait(prepared)
            ran = [
                pool.submit(
                    w.index,
                    lambda w=w: run_suite(
                        w.argv, w.work, self.env, self.limit, self.runs_dir
                    ),
                )
                for w in workers
            ]
            pool.wait(ran)
            emit(
                f"proof: ran round {r + 1}/{total} in {time.monotonic() - started:.1f}s, "
                "judged after the last round"
            )
            rounds.append(ran)
        every_run = pool.wait([t for r in rounds for t in r])
        differences, lines, pristine_elapsed = [], [], []
        for r in range(total):
            holders, pristine, slowest = [], [], 0.0
            for i, w in enumerate(workers):
                run = every_run[r * count + i]
                holder = holds_canary(w.index, r, total)
                (holders if holder else pristine).append(w.index)
                if not holder:
                    pristine_elapsed.append(run.elapsed)
                slowest = max(slowest, run.elapsed)
                role = "canary holder" if holder else "pristine"
                for detail in self._judge_proof_run(w, r, holder, run):
                    differences.append(
                        f"proof: worker {w.index}, round {r + 1}/{total} ({role}): {detail}"
                    )
            canary = (
                f", canary absent={len(self.canary_absent)} equal" if holders else ""
            )
            lines.append(
                f"proof: round {r + 1}/{total} canary in workers "
                f"{','.join(map(str, holders)) or 'none'} pristine in workers "
                f"{','.join(map(str, pristine))}: green rows={len(self.universe)} equal"
                f"{canary} ({slowest:.1f}s)"
            )
        if differences:
            raise KillSetsError(*differences, PROOF_REFUSAL)
        for line in lines:
            emit(line)
        exercised, rows = len(self.canary_absent), len(self.universe)
        if self.canary_aborted:
            emit(
                "proof: the canary aborted collection, so no row is judged singly; a read "
                "of another copy's subject is visible only where it turns a pristine run "
                "red"
            )
        elif exercised < rows:
            emit(
                f"proof: the canary exercises {exercised} of {rows} rows; a read of "
                f"another copy's subject by the other {rows - exercised} rows is not "
                "visible to this proof"
            )
        else:
            emit(f"proof: the canary exercises {rows} of {rows} rows")
        before = self.limit
        self.limit = derive_proof_limit(
            self.serial_elapsed, pristine_elapsed, self.timeout
        )
        raised = f" (raised from {before:.0f}s)" if self.limit > before else ""
        emit(
            f"proof: {total} rounds x {count} workers concurrent; slowest pristine run "
            f"{max(pristine_elapsed, default=0.0):.1f}s against serial {self.serial_elapsed:.1f}s; "
            f"limit {self.limit:.0f}s{raised}"
        )

    def _judge_proof_run(self, worker, r, holder, run: RunResult) -> list:
        """How one proof run differs from what its copy's state demands; empty when it reads right.

        A holder must read as the serial canary did (collection aborted when that one aborted,
        else noticed with the same absent rows); a pristine worker must read green with the
        serial baseline's pass names. A run that timed out is a difference either way.
        """
        if run.rc is None:
            return [f"timed out after {self.limit:.0f}s; process group killed"]
        reading = self.read(run.stdout, worker.argv)
        aborted = (
            re.search(mutate.COLLECTION_ABORT_PATTERN, run.stdout, re.M) is not None
        )
        if holder:
            if aborted != self.canary_aborted:
                return [
                    f"collection {'aborted' if aborted else 'did not abort'} under the "
                    f"canary, as the serial canary {'did not' if aborted else 'did'}"
                ]
            if aborted:
                return []
            if not mutate.is_caught(run.rc, run.stdout):
                return [
                    f"canary not noticed (rc={run.rc}, last stdout line {reading.last!r}): "
                    "this suite did not read its own copy"
                ]
            absent = frozenset(score(self.universe, frozenset(), reading, True).kill)
            if absent != self.canary_absent:
                return [
                    "rows absent under the canary differ from the serial canary: only "
                    f"here {sorted(absent - self.canary_absent)}; only in the serial "
                    f"canary {sorted(self.canary_absent - absent)}"
                ]
            return []
        if aborted or run.rc != 0 or mutate.is_caught(run.rc, run.stdout):
            return [
                f"read red where the serial run was green: rc={run.rc}, last stdout line "
                f"{reading.last!r}"
            ]
        if reading.passed != self.universe:
            return [
                "pass names differ from the serial run: only in the serial run "
                f"{sorted(self.universe - reading.passed)}; only here "
                f"{sorted(reading.passed - self.universe)}"
            ]
        return []

    def anchor(self, old: str) -> str:
        idx = self.original.find(old)
        line = self.original[:idx].count("\n") + 1
        text = next((x.strip() for x in old.split("\n") if x.strip()), "")[:100]
        return f"at {self.subject_rel}:{line}  `{text}`"

    def preclassify(self, n, label, old, new) -> tuple[Outcome | None, str]:
        """The NOT-APPLIED or INVALID outcome (else None) and the mutated text."""
        head = f"[{n}/{len(self.c.mutations)}]"
        count = self.original.count(old)
        if count != 1:
            lines = (
                f"{head} {'NOT-APPLIED':<12} k=0  {label}",
                f"{INDENT}the anchor occurs {count}x in the copy subject, need 1",
            )
            return Outcome(n, "NOT-APPLIED", (), lines, (("notapplied", 1),), None), ""
        mutated = self.original.replace(old, new, 1)
        parse = mutate.judge_mutant_parse(
            self.workers[0].subject, self.original, mutated, self.control
        )
        if parse[0] in (mutate.UNPARSEABLE, mutate.CANNOT_RUN):
            lines = (
                f"{head} {'INVALID':<12} k=0  {label}",
                f"{INDENT}{parse[0]}: {parse[1]}; the suite was NOT run",
            )
            return Outcome(n, "INVALID", (), lines, (("invalid", 1),), None), mutated
        return None, mutated

    def classify(self) -> int:
        """Decide every NOT-APPLIED and INVALID mutant here, queue the rest; return how many.

        The main thread alone calls it, and a second call changes nothing: the count of
        runnable mutants is fixed by the first.
        """
        if self._runnable is None:
            runnable = 0
            for n, (label, old, new) in enumerate(self.c.mutations, 1):
                outcome, mutated = self.preclassify(n, label, old, new)
                if outcome is None:
                    self._pending.put((n, label, old, mutated))
                    runnable += 1
                else:
                    self._results[n] = outcome
            self._runnable = runnable
        return self._runnable

    def run_mutants(self, state: State, pool: Pool) -> None:
        """Grade every mutant on the pool and release the blocks in mutant order.

        The main thread classifies every mutant first (NOT-APPLIED and INVALID need no
        copy and no suite, and the parse check stays single-threaded), queues the runnable
        ones in mutant order, hands each worker one `_drain` ticket, and releases blocks as
        they become due. The loop ends once every drain is over, whether or not the queue
        is empty, so a worker that stopped taking mutants cannot leave it waiting.
        """
        self.classify()
        self._activity = pool.activity
        tickets = [
            pool.submit(w.index, lambda w=w: self._drain(w)) for w in pool.workers
        ]
        while True:
            # The `done` flags are read BEFORE the release and the error scan: a ticket
            # assigns its error before it sets `done`, so a drain read as over has its
            # error visible to the scan, and the loop never leaves without seeing it.
            finished = all(t.done.is_set() for t in tickets)
            check_stop(pool)  # after the read, as in `Pool.wait`
            self._release_ready(state)
            for ticket in tickets:
                if ticket.error is not None:
                    # An outcome stored just before the failure is still due its block.
                    self._release_ready(state)
                    raise ticket.error
            if finished:
                break
            pool.activity.wait(POLL_SECONDS)
            pool.activity.clear()
        # The closing sequence: release what is left, then require that nothing is missing.
        self._release_ready(state)
        self._fill_unserved(state)
        self._release_ready(state)
        self._check_all_graded()

    def _drain(self, worker: Worker) -> None:
        """Worker side: take the lowest-numbered pending mutant until none is left.

        A mutant whose grading raises is a DIED outcome and the worker takes the next one.
        A failed refresh is a DIED outcome too, and the worker retires: it returns without
        taking another mutant, and its copy is never re-created at another path.
        """
        retire = False
        try:
            while not retire:
                _stop_if_flagged()
                try:
                    n, label, old, mutated = self._pending.get_nowait()
                except queue.Empty:
                    return
                try:
                    outcome = self.run_one(worker, n, label, old, mutated)
                except RefreshFailed as failure:
                    outcome = self._died(n, label, worker, failure, refresh=True)
                    retire = True
                except Exception as failure:
                    outcome = self._died(n, label, worker, failure, refresh=False)
                # A stop leaves no outcome: the run it killed would read as a kill.
                _stop_if_flagged()
                with self._results_lock:
                    self._results[n] = outcome
                self._activity.set()
        except Stopped:
            return

    def _died_head(self, n, label) -> str:
        return f"[{n}/{len(self.c.mutations)}] {'DIED':<12} k=0  {label}"

    def _died(self, n, label, worker: Worker, failure, refresh: bool) -> Outcome:
        """The DIED outcome of a mutant whose grading raised `failure` on `worker`."""
        # A failed refresh names the OSError that caused it, not the wrapper.
        cause = (
            failure.__cause__ if refresh and failure.__cause__ is not None else failure
        )
        where = (
            f"worker {worker.index}, refresh" if refresh else f"worker {worker.index}"
        )
        text = " ".join(str(cause).split())
        lines = [
            self._died_head(n, label),
            f"{INDENT}{type(cause).__name__}: {text}  ({where})",
        ]
        shown = "".join(
            traceback.format_exception(type(cause), cause, cause.__traceback__)
        )
        # The exception line itself is the block's second line already: the tail is frames.
        shown = shown.removesuffix(
            "".join(traceback.format_exception_only(type(cause), cause))
        )
        tail = [x.rstrip() for x in shown.splitlines() if x.strip(" ~^")]
        lines.extend(f"{INDENT}! {x}" for x in tail[-3:])
        return Outcome(n, "DIED", (), tuple(lines), (("died", 1),), None)

    def _fill_unserved(self, state: State) -> None:
        """Give every mutant still queued, in mutant order, the no-worker-left outcome.

        Called by the main thread once every drain is over, so no worker is left to take
        them. Each is taken off the queue here, so nothing is left queued.
        """
        while True:
            try:
                n, label, _old, _mutated = self._pending.get_nowait()
            except queue.Empty:
                return
            lines = (
                self._died_head(n, label),
                f"{INDENT}no worker left: every worker retired after a failed refresh",
            )
            outcome = Outcome(n, "DIED", (), lines, (("died", 1),), None)
            with self._results_lock:
                self._results[n] = outcome

    def _next_to_release(self) -> int:
        return self._next_n

    def _release_ready(self, state: State) -> None:
        """Release, in mutant order, every block whose predecessors are all released."""
        while self._next_to_release() in self._results:
            with self._results_lock:
                outcome = self._results.pop(self._next_to_release())
            self.release(state, outcome)
            self._next_n += 1

    def _check_all_graded(self) -> None:
        """After the drains: every mutant was released, or the first gap is named."""
        total = len(self.c.mutations)
        if self._next_n <= total:
            label = self.c.mutations[self._next_n - 1][0]
            raise RuntimeError(
                f"mutant [{self._next_n}/{total}] {label!r} has no outcome after "
                "every worker stopped"
            )

    def run_one(self, worker: Worker, n, label, old, mutated) -> Outcome:
        """One mutant on one worker's copy: re-create it, write, record, run, grade."""
        _stop_if_flagged()
        try:
            refresh_working_copy(self.master, worker.work)
        except OSError as exc:
            raise RefreshFailed(f"worker {worker.index}: {exc}") from exc
        write_in_copy(
            worker.subject, worker.work, mutated, "mutant", errors="surrogateescape"
        )
        digest = mutated.encode("utf-8", "surrogateescape")
        sha = hashlib.sha256(digest).hexdigest()
        with self._applied_lock:
            self.applied.append((n, label, sha))
        run = run_suite(worker.argv, worker.work, self.env, self.limit, self.runs_dir)
        outcome = self.grade(n, label, old, run)
        return outcome

    def release(self, state: State, outcome: Outcome) -> None:
        """The only place a mutant block is printed, counted and kept for `duplicates`."""
        for line in outcome.lines:
            emit(line)
        for field, amount in outcome.deltas:
            state.counts[field] += amount
        if outcome.killed is not None:
            self.killed.append((outcome.n, outcome.killed))

    def grade(self, n, label, old, run: RunResult) -> Outcome:
        """Grade one mutant's run into an Outcome; prints nothing, touches no State."""
        head = f"[{n}/{len(self.c.mutations)}]"
        lines: list = []
        deltas: list = []

        def done(status, kill=(), killed=None) -> Outcome:
            return Outcome(n, status, tuple(kill), tuple(lines), tuple(deltas), killed)

        if run.rc is None:
            deltas.append(("timedout", 1))
            lines.append(f"{head} {'TIMEOUT':<12} k=0  {label}")
            lines.append(
                f"{INDENT}did not finish within {self.limit:.0f}s; process group killed "
                "(a campaign's own timeout= is invisible here: pass --timeout)"
            )
            if self.jobs > 1:
                lines.append(
                    f"{INDENT}jobs={self.jobs}: load average now {_load_average()}; a "
                    "timeout under load is not a kill; re-run with a lower --jobs or a "
                    "larger --timeout"
                )
            return done("TIMEOUT")
        if re.search(mutate.COLLECTION_ABORT_PATTERN, run.stdout, re.M):
            deltas.append(("invalid", 1))
            lines.append(f"{head} {'INVALID':<12} k=0  {label}")
            lines.append(f"{INDENT}pytest's collection aborted, so no test ran")
            return done("INVALID")
        reading = self.read(run.stdout, self.workers[0].argv)
        noticed = mutate.is_caught(run.rc, run.stdout)
        v = score(self.universe, self.canary_absent, reading, noticed)
        lines.append(f"{head} {v.status:<12} k={len(v.kill)}  {label}")
        if v.status == "SURVIVED":
            deltas.append(("survivors", 1))
            lines.append(f"{INDENT}{self.anchor(old)}")
            lines.extend(_dumped_lines(reading))
            return done(v.status)
        if v.status == "KILLED-ALL":
            lines.append(f"{INDENT}(all {len(v.kill)} rows)")
        else:
            for row in v.kill:
                lines.append(f"{INDENT}| {row}")
        if reading.fails:
            lines.append(
                f"{INDENT}explained by {v.nF} FAIL lines (nF={v.nF}), shown as "
                "diagnostics:"
            )
            for line in reading.fails:
                lines.append(f"{INDENT}! {line}")
        if v.status == "UNATTRIBUTED":
            deltas.append(("unattributed", 1))
            why = "noticed, no row vanished" if noticed else "rows vanished under rc 0"
            lines.append(f"{INDENT}unattributed: {why} (rc={run.rc})")
            for line in run.stderr.strip().split("\n")[-3:]:
                if line:
                    lines.append(f"{INDENT}! stderr: {line}")
            lines.extend(_dumped_lines(reading))
            return done(v.status, v.kill)
        if v.status == "KILLED-ALL":
            lines.append(
                f"{INDENT}killed-all: the rows that cannot run when the subject is "
                "unparseable all died (collection or startup, not a check)"
            )
            lines.extend(_dumped_lines(reading))
            deltas.append(("killed_all", 1))
            return done(v.status, v.kill)
        if len(self.universe) == 1:
            lines.append(
                f"{INDENT}killed-all: n/a (a one-row suite: every kill kills every row)"
            )
        for flag in v.flags:
            lines.append(f"{INDENT}{flag}")
        if v.new_labels:
            lines.append(
                f"{INDENT}new_labels: {len(v.new_labels)} PASS labels outside the "
                "baseline (a diagnostic, not counted):"
            )
            for name in v.new_labels:
                lines.append(f"{INDENT}+ {name}")
        lines.extend(_dumped_lines(reading))
        if len(v.kill) == 1:
            deltas.append(("single_row", 1))
        if v.flags:
            deltas.append(("incomplete", 1))
            return done(v.status, v.kill)
        return done(v.status, v.kill, frozenset(v.kill))

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


def _dumped_lines(reading: Reading) -> list[str]:
    """The nested-dump diagnostic, for every graded status that read a run."""
    if reading.dumped:
        return [f"{INDENT}nested dumps: {reading.dumped} lines not read as rows"]
    return []


def _leak_check(live_subject, live_sha, applied, state: State) -> None:
    """The live subject must not hold any mutant's text at the end."""
    now = _sha256(live_subject)
    if now == live_sha:
        emit("leak: none (the live subject is unchanged)")
        return
    hits = [f"[{n}] {label}" for n, label, sha in sorted(applied) if sha == now]
    if hits:
        emit(
            f"ERROR  leak: the live subject holds the text of mutant {', '.join(hits)}"
        )
        state.error = True
        return
    emit("leak: none (the live subject changed during the run, matching no mutant)")


def _worker_copies(tmp) -> list[Path]:
    """The worker copies that exist under `tmp`: the canonical one, then `pool/1`, `pool/2`..."""
    pool = Path(tmp) / "pool"
    numbered = [p for p in pool.iterdir() if p.name.isdigit()] if pool.is_dir() else []
    found = [worker_copy_path(tmp, 0), *sorted(numbered, key=lambda p: int(p.name))]
    return [p for p in found if p.is_dir()]


def _teardown(state: State, keep: bool) -> None:
    if state.tmp is None:
        return
    if keep:
        if state.jobs > 1:
            copies = " ".join(str(p) for p in _worker_copies(state.tmp))
            emit(
                f"copy: kept {state.tmp}  (master {state.tmp / 'master'}, "
                f"working copies {copies})"
            )
            return
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


def _interrupted(state: State, keep: bool, signum: int) -> int:
    """The end of a stopped run: clean up, say so once, and die of the signal.

    Prints the teardown's own `copy:` line, then ONE `interrupted by` line in place of the
    `RESULT:` line (a stopped run has no verdict, and its absence is what a reader of the
    artifact takes for DIED), restores the signal's default disposition and sends it to this
    process. The copy is kept when `--keep` was given or a worker thread did not stop within
    `JOIN_SECONDS`, because removing it would race a worker still copying into it.
    """
    try:
        try:
            _teardown(state, keep or bool(state.stuck))
        except Exception:
            emit("ERROR  teardown failed:")
            for line in traceback.format_exc().rstrip().splitlines():
                emit(line)
        if state.tmp is None:
            copy = "no copy was made"
        elif os.path.lexists(state.tmp):
            copy = f"copy kept at {state.tmp}"
        else:
            copy = "copy removed"
        late = ""
        if state.stuck and not state.stuck_told:
            late = (
                f"; worker thread(s) {', '.join(map(str, state.stuck))} did not stop "
                f"within {JOIN_SECONDS}s"
            )
        emit(
            f"ERROR  interrupted by {signal.Signals(signum).name}: "
            f"{STOP['killed']} suite groups killed, {copy}{late}"
        )
    except (OSError, ValueError):
        pass  # the output went away (a hangup): the exit by the signal still happens
    signal.signal(signum, signal.SIG_DFL)
    os.kill(os.getpid(), signum)
    # Not reached: the signal's default action ends the process.
    return 128 + signum


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
    p.add_argument(
        "--jobs",
        default="1",
        help=(
            f"worker copies to grade mutants on, an integer from 1 to {MAX_JOBS} "
            "(default 1, serial); above 1 a clean run ends PARALLEL-PASS"
        ),
    )
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = _parse(argv)
    _install_handlers()
    state = State()
    try:
        _run(args, state)
    except StopRequested:
        pass  # the recorded signal is read once, below
    except Stopped:
        # A worker gave up with no stop recorded: no verdict may rest on its missing outcome.
        if STOP["signum"] is None:
            emit("ERROR  internal exception; a worker stopped with no stop requested")
            state.error = True
    except KillSetsError as exc:
        for why in exc.args:
            emit(f"ERROR  {why}")
        state.error = True
    except Exception:
        emit("ERROR  internal exception; no verdict was reached:")
        for line in traceback.format_exc().rstrip().splitlines():
            emit(line)
        state.error = True
    # The one read of the flag: a stop recorded by here ends the run as a stopped one; the
    # verdict, once its line is printed, stands whatever arrives later.
    signum = STOP["signum"]
    if signum is not None:
        return _interrupted(state, args.keep, signum)
    # A worker thread that outlived the join may still be copying into its copy (a copy
    # re-creates its parents), so removing the directory under it would race it: keep it.
    keep = args.keep or bool(state.stuck)
    if state.stuck and not args.keep:
        emit(
            f"copy: not removed, worker thread(s) {', '.join(map(str, state.stuck))} "
            "did not stop and may still be writing into it"
        )
    try:
        _teardown(state, keep)
    except Exception:
        emit("ERROR  teardown failed:")
        for line in traceback.format_exc().rstrip().splitlines():
            emit(line)
        state.error = True
    status, rc = verdict(state.counts, state.error, state.jobs)
    emit(result_line(state.counts, status, rc, state.jobs))
    return rc


if __name__ == "__main__":
    sys.exit(main())

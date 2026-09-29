#!/usr/bin/env python3
# Script: guard-parity.py
# Purpose: Replay every distinct transcript Bash command through two pinned guard builds and list each verdict difference
# Usage: guard-parity.py --old REV [--new REV] --artifact-dir DIR [--guard NAME]... [--transcripts DIR] [--corpus FILE]
"""Replay real commands through two builds of the git guards and enumerate what changed.

**Why this exists.** Every change to a guard, or to the tokenizer they share
(the command tokenizer under `scripts/lib`), needs one answer: *what changed on the commands people
actually ran?* A suite of hand-labelled cases answers a different question. This tool
takes every distinct Bash command in the session transcripts (about 100,000), runs each
through the OLD and the NEW build of every guard, and lists every difference. The same
harness was hand-built at least three times in scratchpads and lost each time.

    guard-parity.py --old dev --new HEAD --artifact-dir <a directory outside the repo>

Run it through `run-long.sh` (a full corpus takes over an hour; see "How it stays fast") with
`--expect 'RESULT: (PASS|FAIL|ERROR|INCOMPLETE) rc=[0-9]+'`. Commit first: revisions only,
never a working tree.

**What it judges.** The four guards that share the tokenizer and one contract: a JSON
payload on stdin, exit 2 to refuse, an `if __name__ == "__main__"` block:
`git-timing-guard.py`, `push-guard.py`, `publication-push-guard.py`,
`commit-subject-guard.py`. The entry point is the script itself, so there is no
per-guard adapter to drift. `recast-commit-gate.py` (its verdict is a pytest run) and
`exec-bit-guard.sh` (bash) are named as not judged, with the reason, in every report.

**How it stays fast, and how it proves it stayed faithful.** A genuine process per
execution costs 134-547 ms, hours over the corpus. So the sweep runs a preloaded worker
per build that forks a child per unit: fresh state per row, the guard's own `__main__`
block, real file descriptors, `os._exit` after the interpreter's own shutdown flush. A fork is not the process boundary, so every unit that drew anything but
an allow, every unit whose builds disagree, every timeout, and a seeded sample are ALSO
run as genuine processes, and twice more for stability. A unit that reads differently
through the two is `EXEC_MISMATCH` and the run is INCOMPLETE.

**How long it takes: an observation, not a promise.** The one full run measured, over
101,005 rows (404,024 units), took about 71 minutes at 24 workers: roughly 10 ms of wall
time per unit across all the workers, or at most about 250 ms of worker time per unit, against
about 12 ms for the fork itself: the difference is the guards' own work, not measured
separately. The time depends on the worker count and on what the guards spawn, so the default worker
count is `min(CPU count, 8)` (an explicit `--workers` is used as given) and a run at that
default is expected to take longer than the 24-worker figure.

**What is pinned, and what is not.** Both builds are extracted with `git archive` and
proven against the tree by blob id. The guard runs under the operator's own environment
with three names moved: a mirrored `HOME` whose only difference is a generated timing
conf with an always-open window (the timing guard reads `datetime.now()`, so an unpinned
replay measures the time of day, and outside its window every row reads "allowed"); one
shared publication-guard log inside the artifact dir; and `PYTHONHASHSEED=0` in the
workers, so message text cannot differ between builds by set order (a verdict that
depends on hash order is therefore hidden). The child's cwd is an empty neutral
directory. `--artifact-dir` is locked while a run holds it, and a revision that is HEAD
is refused when `scripts/` has uncommitted changes, since the run would grade what was
committed, not them.

**Canaries.** One command per judged guard that the guard must refuse, in a fixture repo
it needs. commit-subject-guard refused none of 6,000 sampled real commands, so without a
canary "it refused nothing" cannot be told from "this instrument cannot reach its block
path". Each canary must be refused by the OLD build or the run is INCOMPLETE.

**Verdict.** The last line of stdout: `RESULT: PASS|FAIL|ERROR|INCOMPLETE rc=0|1|2|3`
and counts. `FAIL` means differences exist and are listed above the line: an intended
change fails it; it is not a claim of a defect. Only an exit status of 2 refuses, so
`OPENED` (2 -> anything else, an exit-120 included) is listed first. A registration
change (a hook added, removed or re-timed in `settings.json`) counts as a difference.
`INCOMPLETE` comes before `FAIL`: a run that cannot be believed says so first: a floor
unmet, a canary not refused, `--limit` or `--sample 0`, unstable or incomparable units
above `--tolerate-unproven`, any `EXEC_MISMATCH`, a worker shard that never finished, or
any internal-error record in the guards' log (each is a fail-closed refusal the guard never
judged; the report header counts them, and a record an earlier run left in the artifact
directory is charged too, since a resume reuses that run's shards: start a fresh directory to
clear it). The comparable-unit floor (`--min-rows`) counts
units whose readings were actually compared: unstable and incomparable units are excluded,
so tolerating them cannot license a PASS over a run that read almost nothing, and the
`comparable=` field of the RESULT line is that same count.
`noise=<n>/<m>` is the disagreement rate over the seeded sample alone.

**A guard's own deadline.** Each git guard arms a deadline of its own (`lib/guard_deadline.py`,
50 s for the publication guard, below this tool's 120 s timeout) and, on expiry, prints "the
guard reached its own <n>s deadline" and exits with a clean status. That is the guard giving
up, not a verdict, and both builds print the same words, so a reading carrying that phrase, in
either mode and on either build, makes its unit `INCOMPARABLE`, never `SAME`, `OPENED` or
`CLOSED`. A refusal that merely uses the word "deadline" is still compared.

**A timeout of the audit.** When the genuine-process run of a unit times out, nothing
was measured for it, so an otherwise-`OPENED` unit is classified `INCOMPARABLE` instead:
it is not listed as OPENED and is not a difference. It counts as unproven, so at
`--tolerate-unproven 0` (the default) the run is INCOMPLETE and never PASS or FAIL; a
run that tolerates unproven units can PASS or FAIL with that unit never compared.

**What it does not cover.** Fault-injected stdio (a closed or broken-pipe stderr making
the shutdown flush fail) is not replayed; the audit proves fork == genuine process for
healthy files only. A forked child skips interpreter finalisation, so `atexit` handlers
and `__del__` never run in it; no current guard registers one, and a future guard that
does would differ only in the fork, which the audit catches on the units it re-runs and
nowhere else. A `KeyboardInterrupt` in the child is handled as an ordinary uncaught
error (status 1 and a traceback), not by the interpreter's own interrupt handling; these
differences are documented, not fixed. The child is made to look like the real process
(its own `__main__` module with the interpreter's loader and empty spec, its own
directory first on `sys.path`, only descriptors 0-2 open) and measured to. What still
differs: stdin is a regular file where the harness gives a pipe, stdout and stderr are
regular files in the fork AND in the audit, `sys.orig_argv`, and how many modules are
already imported. A stream is read back to 4 MiB and beyond that compared by its size; a
guard is stopped by a 256 MiB write limit (a guard writing without end gets an
`OSError`, not a full disk). There is no spawn census. PostToolUse hooks are out of
scope, and a payload field beyond `tool_name`, `tool_input`, `cwd` and `hook_event_name`
is absent. The replay runs read-only git queries against every directory the operator
once worked in, as the live hooks did on every call; nothing is written to any of them.
`--artifact-dir` holds raw commands and is created 0700.

**Resuming.** A shard's key covers everything that made it: its rows, the build's commit, the
guards, mode, repeat, timeout, a digest of the environment NAMES, a digest of the
environment VALUES (compared and never printed; `_`, `SHLVL` and `OLDPWD` are left out
because a shell rewrites them on every invocation), the interpreter's path and version, the
fixture salt and THIS FILE's own source. A shard must also end in its `done` line, and its
key is written by its worker only after that. A shard index is reused only when EVERY
build's shard at that index is: if one build's shard is stale or unfinished, the shard at
that index runs again for all builds, because the guards read live repository state and a
pair read at different times is not a comparison. Pass `--corpus
<artifact>/corpus.jsonl` to freeze the population across a killed run: re-extracting
transcripts that have grown re-splits the shards.

**The worker deadline.** Each worker may run for `worker_grace` (300 s) plus the timeout
times its units times the repeat; past that its whole process group is killed and its
shard counts as unfinished. It bounds a hung preload or a wedged fork server, not a
mid-shard wedge that stays inside that budget.
"""

from __future__ import annotations

import argparse
import builtins
import fcntl
import hashlib
import importlib.machinery
import json
import os
import pkgutil
import random
import re
import resource
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import traceback
import types
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, NamedTuple

EXIT_BLOCK = 2  # the only exit status Claude Code treats as a refusal
TIMEOUT_S = 120.0
WORKER_GRACE_S = 300.0  # what a worker may spend beyond its units' own timeouts
DEFAULT_WORKERS_CAP = (
    8  # the default only; at 24 workers the old publication guard hit its own deadline
)
OUTPUT_TEXT_CAP = 4096
DIGEST_LEN = 12
BUILD_MARK = "<BUILD>"

# The guards this tool can judge, by script basename. Every one speaks the same contract — a JSON
# payload on stdin, exit 2 to refuse — and ends in an `if __name__ == "__main__"` block, so the
# entry point is the script itself and no per-guard adapter exists.
JUDGED = (
    "git-timing-guard.py",
    "push-guard.py",
    "publication-push-guard.py",
    "commit-subject-guard.py",
)
NOT_JUDGED = {
    "recast-commit-gate.py": "its verdict is a pytest run inside the recast skill, not a guard "
    "decision (side effects, live-repo dependence)",
    "exec-bit-guard.sh": "a bash hook; the fork server runs Python source and cannot serve it",
}


class ParityError(Exception):
    """A setup failure. The run reports ERROR, never a verdict over something it did not set up."""


class Row(NamedTuple):
    """One distinct `(cwd, command)` the transcripts recorded."""

    id: str
    cwd: str
    tool_input: dict


class Reading(NamedTuple):
    """One execution of one guard on one row.

    `rc` is None when the run timed out or would not launch — never a verdict. `out` and `err`
    are short digests with the build's root path normalised to `<BUILD>`; `text` is the stderr
    itself (capped), kept only so a report can say WHY a guard refused.
    """

    rc: int | None
    out: str
    err: str
    text: str

    def to_json(self) -> list:
        return [self.rc, self.out, self.err, self.text]

    @staticmethod
    def from_json(raw: list) -> Reading:
        return Reading(raw[0], raw[1], raw[2], raw[3])


# ---------------------------------------------------------------------------------------------
# Corpus: every distinct Bash command the transcripts recorded
# ---------------------------------------------------------------------------------------------


class CorpusError(ParityError):
    """The corpus could not be built or read; the run is ERROR, never a smaller run."""


class CorpusStats(NamedTuple):
    transcripts: int
    records: int  # Bash tool_use records seen, before dedupe
    skipped: dict[str, int]


def row_id(cwd: str, command: str) -> str:
    """Stable identity of a row. `surrogatepass`: a transcript string may hold a lone surrogate,
    which no other error handler can encode."""
    raw = (cwd + "\0" + command).encode("utf-8", "surrogatepass")
    return hashlib.sha256(raw).hexdigest()[:16]


def order_key(identity: str) -> str:
    """Pseudo-shuffle key independent of the order transcripts were enumerated in, so a corpus
    and `--limit N` mean the same rows on every machine and every run."""
    return hashlib.sha256((identity + "order").encode("ascii")).hexdigest()


def corpus_digest(rows: list[Row]) -> str:
    return hashlib.sha256("\n".join(r.id for r in rows).encode("ascii")).hexdigest()


def _transcript_paths(root: Path) -> list[Path]:
    # os.walk on the path itself descends even when `root` is a symlink; a bare glob or a
    # `find <symlink> -maxdepth 1` would report the link and nothing under it.
    # A directory already visited under another spelling is pruned, so a symlink cycle
    # terminates and no transcript is counted twice.
    found, seen = [], set()
    for base, dirs, names in os.walk(root, followlinks=True):
        real = os.path.realpath(base)
        if real in seen:
            dirs[:] = []
            continue
        seen.add(real)
        found.extend(Path(base, n) for n in names if n.endswith(".jsonl"))
    return sorted(found)


def extract_corpus(root: Path) -> tuple[list[Row], CorpusStats]:
    """Every distinct Bash `(cwd, command)` under `root`, in `order_key` order.

    No filter beyond validity: a filter written from a guard's pre-gate re-implements the subject
    and would hide exactly the change that alters that pre-gate.
    """
    skipped: dict[str, int] = {}

    def skip(why: str) -> None:
        skipped[why] = skipped.get(why, 0) + 1

    rows: dict[str, Row] = {}
    paths = _transcript_paths(root)
    records = 0
    for path in paths:
        try:
            handle = open(path, encoding="utf-8", errors="surrogateescape")
        except OSError:
            skip("unreadable_transcript")
            continue
        with handle:
            for line in handle:
                if '"Bash"' not in line:  # the tool name is quoted on every such line
                    continue
                try:
                    record = json.loads(line)
                except ValueError:
                    skip("bad_json_line")
                    continue
                if not isinstance(record, dict):
                    skip("non_object_record")
                    continue
                message = record.get("message")
                if not isinstance(message, dict):
                    skip("non_object_message")
                    continue
                content = message.get("content")
                if not isinstance(content, list):
                    skip("non_list_content")
                    continue
                for block in content:
                    if not (
                        isinstance(block, dict)
                        and block.get("type") == "tool_use"
                        and block.get("name") == "Bash"
                    ):
                        continue
                    records += 1
                    tool_input = block.get("input")
                    cwd = record.get("cwd")
                    if not isinstance(tool_input, dict):
                        skip("non_object_input")
                    elif not isinstance(tool_input.get("command"), str):
                        skip("non_string_command")
                    elif not tool_input["command"]:
                        skip("empty_command")
                    elif not isinstance(cwd, str) or not cwd:
                        skip("no_cwd")
                    else:
                        identity = row_id(cwd, tool_input["command"])
                        seen = rows.get(identity)
                        if seen is None:
                            rows[identity] = Row(identity, cwd, tool_input)
                        elif (seen.cwd, seen.tool_input["command"]) != (
                            cwd,
                            tool_input["command"],
                        ):
                            raise CorpusError(f"row id collision on {identity}")
    ordered = sorted(rows.values(), key=lambda r: order_key(r.id))
    return ordered, CorpusStats(len(paths), records, skipped)


def write_corpus(rows: list[Row], path: Path) -> None:
    """A header line — the row count and the corpus digest — then one line per row, so a frozen
    corpus that was cut short, padded or edited can be told from a whole one."""
    header = {"corpus": {"rows": len(rows), "digest": corpus_digest(rows)}}
    with open(path, "w", encoding="utf-8") as out:
        out.write(json.dumps(header) + "\n")
        for row in rows:
            out.write(json.dumps(row._asdict()) + "\n")


def read_corpus(path: Path) -> list[Row]:
    """A frozen corpus, checked against its own header. Each id is re-derived and must be unique,
    and the count and digest must match what was written: a file cut at a line boundary, padded
    with one repeated row, or hand-edited fails loudly instead of being graded as a different
    population."""
    try:
        lines = path.read_text(encoding="utf-8", errors="surrogateescape").splitlines()
    except OSError as exc:
        raise CorpusError(f"cannot read corpus {path}: {exc}") from exc
    try:
        header = json.loads(lines[0])["corpus"]
        expected_rows, expected_digest = header["rows"], header["digest"]
    except (IndexError, ValueError, KeyError, TypeError) as exc:
        raise CorpusError(f"{path}: no corpus header on its first line") from exc
    rows, seen = [], set()
    for number, line in enumerate(lines[1:], 2):
        try:
            raw = json.loads(line)
            row = Row(raw["id"], raw["cwd"], raw["tool_input"])
            good = row.id == row_id(row.cwd, row.tool_input["command"])
        except (ValueError, KeyError, TypeError) as exc:
            raise CorpusError(f"{path}:{number}: malformed corpus row: {exc}") from exc
        if not good:
            raise CorpusError(
                f"{path}:{number}: row id does not match its cwd and command"
            )
        if row.id in seen:
            raise CorpusError(f"{path}:{number}: duplicate row id {row.id}")
        seen.add(row.id)
        rows.append(row)
    if len(rows) != expected_rows:
        raise CorpusError(
            f"{path} holds {len(rows)} rows but its header says {expected_rows}"
        )
    if corpus_digest(rows) != expected_digest:
        raise CorpusError(f"{path}: its rows do not match the digest in its header")
    return rows


# ---------------------------------------------------------------------------------------------
# Builds: the code under test, pinned as bytes
# ---------------------------------------------------------------------------------------------


class PinError(ParityError):
    """A build could not be pinned or trusted; the run is ERROR, never a run over other bytes."""


class Build(NamedTuple):
    label: str  # "old" or "new"
    rev: str  # what the operator named
    sha: str  # the commit it resolved to
    root: Path  # a directory holding the pinned `scripts/`
    files: int  # how many files were verified against the tree


def _scope_git(scope: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(scope), *args], capture_output=True, check=False
    )


def _blob_id(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def _extract(scope: Path, sha: str, dest: Path, label: str) -> None:
    dest.mkdir(parents=True)
    archive = subprocess.Popen(
        ["git", "-C", str(scope), "archive", sha, "scripts"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    unpack = subprocess.run(
        ["tar", "-x", "-C", str(dest)],
        stdin=archive.stdout,
        capture_output=True,
        check=False,
    )
    archive.stdout.close()
    archive_err = archive.stderr.read().decode(errors="replace").strip()
    archive.stderr.close()
    # A pipeline's exit status is the last stage's, so both stages are read.
    if archive.wait() != 0 or unpack.returncode != 0:
        detail = archive_err or unpack.stderr.decode(errors="replace").strip()
        raise PinError(f"{label}: cannot unpack scripts/ at {sha[:12]}: {detail}")


def _verify(scope: Path, sha: str, dest: Path, label: str) -> int:
    """Prove `dest/scripts` is byte-for-byte the tree at `sha`; returns the file count."""
    listing = _scope_git(scope, "ls-tree", "-r", "-z", sha, "scripts")
    if listing.returncode != 0:
        raise PinError(f"{label}: cannot list scripts/ at {sha[:12]}")
    entries = []
    for record in listing.stdout.split(b"\0"):
        if not record:
            continue
        meta, _, name = record.partition(b"\t")
        mode, _kind, blob = meta.decode().split(" ")
        entries.append((mode, blob, name.decode("utf-8", "surrogateescape")))
    if not entries:
        raise PinError(f"{label}: no files under scripts/ at {sha[:12]}")
    bad = []
    for mode, blob, name in entries:
        if mode == "160000":  # a submodule gitlink carries no content to extract
            continue
        path = dest / name
        if len(blob) != 40:
            raise PinError(f"{label}: unsupported object format (blob id {blob!r})")
        try:
            if mode == "120000":
                data = os.readlink(path).encode() if path.is_symlink() else None
            else:
                data = (
                    path.read_bytes()
                    if path.is_file() and not path.is_symlink()
                    else None
                )
        except OSError:
            data = None  # unreadable is not proven
        if data is None or _blob_id(data) != blob:
            bad.append(name)
        elif mode == "100755" and not os.access(path, os.X_OK):
            bad.append(f"{name} (lost its exec bit)")
    # Files the tree does NOT have: a directory left by a revision that still held one would
    # otherwise pass, and stay importable, after a later revision deleted it. `__pycache__` is
    # what running the guards leaves behind, and is no part of the proof.
    tracked = {name for mode, _blob, name in entries if mode != "160000"}
    for base, dirs, names in os.walk(dest / "scripts"):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        linked = [d for d in dirs if os.path.islink(os.path.join(base, d))]
        for leaf in [*names, *linked]:
            rel = os.path.relpath(os.path.join(base, leaf), dest)
            if rel not in tracked:
                bad.append(f"{rel} (not in the tree)")
    if bad:
        shown = ", ".join(bad[:5]) + (
            f" and {len(bad) - 5} more" if len(bad) > 5 else ""
        )
        raise PinError(f"{label}: pinned scripts/ differ from {sha[:12]}: {shown}")
    return len(entries)


def pin_build(scope: Path, rev: str, label: str, dest: Path) -> Build:
    """Extract `scripts/` at `rev` into `dest` and prove every byte matches the commit.

    A committed rev only: a working tree is refused by construction, because a run grades the
    tree it launched on. Extraction is `git archive`, which honours `export-ignore` and
    `export-subst` — so the proof is against the TREE (`ls-tree` blob ids), never against the
    archive's own idea of what it wrote. An existing `dest` is reused only if it passes that same
    proof; one left by another revision, or damaged, is rebuilt from scratch and proven again.
    """
    resolved = _scope_git(
        scope, "rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}"
    )
    if resolved.returncode != 0:
        raise PinError(f"{label}: {rev!r} does not name a commit in {scope}")
    sha = resolved.stdout.decode().strip()
    if dest.exists():
        try:
            return Build(label, rev, sha, dest, _verify(scope, sha, dest, label))
        except PinError:
            shutil.rmtree(dest)
    _extract(scope, sha, dest, label)
    return Build(label, rev, sha, dest, _verify(scope, sha, dest, label))


def require_guards(builds: Sequence[Build], names: Sequence[str]) -> None:
    for build in builds:
        for name in names:
            if not (build.root / "scripts" / name).is_file():
                raise PinError(
                    f"{build.label}: scripts/{name} is missing at {build.sha[:12]}"
                )


def uncommitted(scope: Path) -> list[str]:
    """Paths under `scripts/` that differ from HEAD in the working tree: modified, staged or
    untracked. Every setting the repo could hold is passed explicitly — a repo's
    `status.showUntrackedFiles=no` would otherwise hide a new file from the check."""
    done = _scope_git(
        scope,
        "status",
        "--porcelain",
        "--untracked-files=all",
        "--ignore-submodules=none",
        "--",
        "scripts",
    )
    if done.returncode != 0:
        raise PinError(f"cannot read the working-tree status of {scope}")
    return [line[3:] for line in done.stdout.decode("utf-8", "replace").splitlines()]


def _matches_bash(matcher: str) -> bool:
    if matcher in ("", "*"):
        return True
    try:
        return re.fullmatch(matcher, "Bash") is not None
    except re.error:
        return True  # unreadable: kept, so the comparison can still show it


def registrations(scope: Path, sha: str) -> set[tuple[str, str, int | None]] | None:
    """`(matcher, script basename, timeout)` for every PreToolUse hook that fires on Bash at
    `sha`, or None when that revision carries no settings.json."""
    shown = _scope_git(scope, "show", f"{sha}:settings.json")
    if shown.returncode != 0:
        return None
    try:
        data = json.loads(shown.stdout)
    except ValueError as exc:
        raise PinError(f"settings.json at {sha[:12]} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise PinError(f"settings.json at {sha[:12]} is not a JSON object")
    found = set()
    try:
        for entry in (data.get("hooks") or {}).get("PreToolUse") or []:
            matcher = entry.get("matcher") or ""
            if not _matches_bash(matcher):
                continue
            for hook in entry.get("hooks") or []:
                words = (hook.get("command") or "").split()
                if not words:
                    continue
                script = next(
                    (w for w in words if w.endswith((".py", ".sh"))), words[0]
                )
                found.add((matcher, os.path.basename(script), hook.get("timeout")))
    except (AttributeError, TypeError) as exc:
        raise PinError(
            f"settings.json at {sha[:12]} has an unexpected shape: {exc}"
        ) from exc
    return found


def registration_delta(old: set | None, new: set | None) -> list[str]:
    """One line per hook registration added, removed or re-timed between the two revisions."""
    lines = [
        f"- PreToolUse[{m or '*'}] {name} timeout={t}"
        for m, name, t in sorted((old or set()) - (new or set()), key=str)
    ]
    lines += [
        f"+ PreToolUse[{m or '*'}] {name} timeout={t}"
        for m, name, t in sorted((new or set()) - (old or set()), key=str)
    ]
    return lines


def unjudged_registered(
    regs: set | None, judged: Sequence[str]
) -> list[tuple[str, str]]:
    """Registered hooks this run does not judge, each with the reason."""
    seen = sorted({name for _m, name, _t in regs or set()} - set(judged))
    return [(n, NOT_JUDGED.get(n, "not covered by this tool")) for n in seen]


# ---------------------------------------------------------------------------------------------
# Replay environment, fixture home, canaries
# ---------------------------------------------------------------------------------------------

TIMING_CONF = ".git-timing-guard.conf"
MIRRORED_FOR_GIT = (".gitconfig", ".config")
CONF_STRIP = (
    "\"' \r"  # what the timing guard deletes from a conf value, wherever it sits
)


class FixtureError(ParityError):
    """A fixture the run depends on could not be built."""


def read_timing_pattern(
    real_home: Path, override: str | None
) -> tuple[str | None, str]:
    """The repo pattern the timing guard is scoped to, and where it came from.

    Parsed as the guard parses it: the LAST `GUARD_REPO_PATTERN=` line wins and quotes, spaces and
    CRs are deleted. With no pattern the guard is disabled, and a replay of a disabled guard reads
    "allowed" on every row — so no pattern means the guard is not judged, never a vacuous run.
    """
    if override:
        return override, "--timing-pattern"
    conf = real_home / ".claude" / TIMING_CONF
    try:
        text = conf.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None, f"no readable {conf}"
    value = None
    for line in text.splitlines():
        if line.startswith("GUARD_REPO_PATTERN="):
            value = line[len("GUARD_REPO_PATTERN=") :]
    cleaned = "".join(ch for ch in (value or "") if ch not in CONF_STRIP)
    if not cleaned:
        return None, f"{conf} names no GUARD_REPO_PATTERN"
    return cleaned, str(conf)


def make_fixture_home(dest: Path, real_home: Path, pattern: str | None) -> Path:
    """A HOME that is the real one in every respect but the policy file.

    Every entry of `real_home` except `.claude` is symlinked in, so git still reads the operator's
    global config; `.claude` is a real directory holding only a generated timing conf whose window
    is always open (`0000`-`2400`, every day). The guard reads `datetime.now()` with no override,
    so without this a replay measures the time of day, and outside the window every row reads
    "allowed".
    """
    if pattern == "":
        raise FixtureError("the timing pattern is empty; give one or none")
    if pattern is not None and set(pattern) & set(CONF_STRIP + "\n"):
        raise FixtureError(
            f"timing pattern {pattern!r} holds a character the guard would strip"
        )
    # Absolute before linking: a relative target resolves against the LINK directory, so every
    # link made from one would dangle.
    real_home = Path(real_home).resolve()
    try:
        entries = os.listdir(real_home)
    except OSError as exc:
        raise FixtureError(f"cannot read HOME {real_home}: {exc}") from exc
    try:
        (dest / ".claude").mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise FixtureError(f"cannot create {dest / '.claude'}: {exc}") from exc
    for entry in entries:
        if entry == ".claude":
            continue
        link = dest / entry
        try:
            if link.is_symlink():  # a reused dest: replace what an earlier run linked
                link.unlink()
            os.symlink(real_home / entry, link)
        except OSError as exc:
            # Git reads the operator global config through these two; without them the replay
            # runs a different git, so that is an error. Any other entry is best-effort.
            if entry in MIRRORED_FOR_GIT:
                raise FixtureError(
                    f"cannot mirror {entry} into the fixture HOME: {exc}"
                ) from exc
    if pattern is not None:
        conf = f"GUARD_REPO_PATTERN={pattern}\nGUARD_DAYS=1-7\nGUARD_START=0000\nGUARD_END=2400\n"
        (dest / ".claude" / TIMING_CONF).write_text(conf, encoding="utf-8")
    return dest


def replay_env(
    base: Mapping[str, str], fixture_home: Path, log: Path
) -> dict[str, str]:
    """The environment every guard runs under: the operator's own, with three names moved.

    Returned as a NEW mapping and handed to each child per call — never exported — because a
    setting exported for one call is inherited by every child. `PYTHONHASHSEED` is fixed at
    interpreter start, so it only takes effect in a process LAUNCHED with it; message text built
    from set iteration then cannot differ between the two builds' workers.
    """
    env = dict(base)
    env["HOME"] = str(fixture_home)
    env["PUBLICATION_PUSH_GUARD_LOG"] = str(log)
    env["PYTHONHASHSEED"] = "0"
    return env


def env_names_digest(env: Mapping[str, str]) -> str:
    """A digest of the environment's NAMES only, so two runs can be compared without a value
    (a token, a path) ever reaching a report."""
    return hashlib.sha256("\n".join(sorted(env)).encode()).hexdigest()[:DIGEST_LEN]


# Names a shell rewrites on every invocation. A guard cannot read a verdict from them, and
# keying on them would make a resume from a fresh shell never reuse anything.
SHELL_VOLATILE = frozenset({"_", "SHLVL", "OLDPWD"})


def env_values_digest(env: Mapping[str, str]) -> str:
    """A digest of the environment's NAME=VALUE pairs, for a resume key only. Values can be
    secrets, so this is compared and never printed: no report may carry it. The names in
    `SHELL_VOLATILE` are left out; every other value can change a reading."""
    pairs = "\0".join(
        f"{k}={v}" for k, v in sorted(env.items()) if k not in SHELL_VOLATILE
    )
    return hashlib.sha256(pairs.encode("utf-8", "surrogateescape")).hexdigest()


def _fixture_git(repo: Path, *args: str) -> None:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
    done = subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "commit.gpgsign=false",
            "-c",
            "tag.gpgsign=false",
            "-c",
            "user.email=t@t.invalid",
            "-c",
            "user.name=t",
            *args,
        ],
        capture_output=True,
        env=env,
        check=False,
    )
    if done.returncode != 0:
        detail = done.stderr.decode(errors="replace").strip()
        raise FixtureError(f"fixture git {' '.join(args)} failed in {repo}: {detail}")


def _fixture_repo(dest: Path, name: str) -> Path:
    repo = dest / name
    repo.mkdir(parents=True, exist_ok=True)
    _fixture_git(repo, "init", "-q", "-b", "main")
    return repo


def _commit_all(repo: Path) -> None:
    _fixture_git(repo, "add", "-A")
    _fixture_git(repo, "commit", "-q", "-m", "init")


CANARY_PREFIX = "canary:"


def canary_guard(identity: str) -> str | None:
    """The guard a canary row exists to exercise, or None for an ordinary row."""
    return (
        identity[len(CANARY_PREFIX) :] if identity.startswith(CANARY_PREFIX) else None
    )


def build_canaries(dest: Path, pattern: str | None, guards: Sequence[str]) -> list[Row]:
    """One command per judged guard that the guard MUST refuse, each in a fixture repo it needs.

    A sweep in which a guard blocked nothing cannot tell "no command needed blocking" from "this
    instrument cannot reach that guard's block path" — commit-subject-guard refused 0 of 6,000
    sampled real commands. A canary makes the second reading checkable.
    """
    dest.mkdir(parents=True, exist_ok=True)
    rows = []
    for guard in guards:
        if guard == "push-guard.py":
            cwd = dest / "plain"
            cwd.mkdir(exist_ok=True)
            command = "git push origin main"
        elif guard == "publication-push-guard.py":
            cwd = _fixture_repo(dest, "adopted")
            (cwd / "README.md").write_text("x\n")
            (cwd / ".publication.toml").write_text('production = "dev"\n')
            for hook in (cwd / "git-hooks", cwd / ".git" / "hooks"):
                hook.mkdir(exist_ok=True)
                (hook / "pre-push").write_text("#!/bin/sh\nexit 0\n")
                (hook / "pre-push").chmod(0o755)
            _commit_all(cwd)
            _fixture_git(cwd, "branch", "dev")
            command = "git push origin dev"
        elif guard == "commit-subject-guard.py":
            cwd = _fixture_repo(dest, "optin")
            (cwd / ".commit-conventions.toml").write_text(
                "subject_advise = 72\nsubject_block = 80\n"
            )
            _commit_all(cwd)
            command = "git commit -m 'feat: " + "x" * 100 + "'"
        elif guard == "git-timing-guard.py":
            if pattern is None:
                raise FixtureError("the timing guard's canary needs a repo pattern")
            cwd = _fixture_repo(dest, "timing")
            _fixture_git(
                cwd, "remote", "add", "origin", f"https://example.invalid/{pattern}.git"
            )
            command = "git push origin main"
        else:
            raise FixtureError(f"no canary is defined for {guard}")
        rows.append(Row(CANARY_PREFIX + guard, str(cwd), {"command": command}))
    return rows


# ---------------------------------------------------------------------------------------------
# Worker: one build, many units
#
# The sweep runs every guard on every row for two builds, so a genuine process per execution
# (134-547 ms) would take hours. A worker pays interpreter and module start-up once, then forks a
# child per unit (about 12 ms): fresh state per row by construction, the guard's own `__main__`
# block as the entry point. What the fork does NOT reproduce is the process boundary, which is
# why the audit stage re-runs the units that matter as genuine processes.
# ---------------------------------------------------------------------------------------------


class Compiled(NamedTuple):
    path: str
    code: object  # a code object


_INHERITED: list = []  # open streams the parent holds buffered data in; see `_flush_inherited`


def _flush_inherited() -> None:
    """Flush every stream a child would inherit with data still buffered in it.

    The child flushes `sys.stdout` and `sys.stderr` before it exits, so a byte the PARENT left
    buffered in either is written into that child's capture. Measured in the spike's first
    version: a child flushing the parent's pending result buffer wrote 541,423 lines for 5,518
    rows.
    """
    for stream in (sys.stdout, sys.stderr, *_INHERITED):
        try:
            if stream is not None:
                stream.flush()
        except (OSError, ValueError):
            pass


FSIZE_CAP = (
    256 * 1024 * 1024
)  # a guard that writes without end is stopped here, not at the disk
READ_CAP = (
    4 * 1024 * 1024
)  # how much of a stream is read back; beyond it, only its size


def _limit_writes() -> None:
    resource.setrlimit(resource.RLIMIT_FSIZE, (FSIZE_CAP, FSIZE_CAP))


def _read_capped(staged) -> bytes:
    """A staged stream's bytes, or its first READ_CAP and how many there were — one rule for both
    modes, so a huge output reads identically through each."""
    staged.seek(0)
    data = staged.read(READ_CAP + 1)
    if len(data) > READ_CAP:
        data = (
            data[:READ_CAP]
            + b"\0[truncated: %d bytes]" % os.fstat(staged.fileno()).st_size
        )
    return data


def hook_payload(row: Row) -> bytes:
    """The JSON a hook receives on stdin. ASCII-only, so a lone surrogate in a command travels as
    an escape and both builds are handed identical bytes."""
    return json.dumps(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": row.tool_input,
            "cwd": row.cwd,
        }
    ).encode("ascii")


def build_markers(root: Path) -> list[bytes]:
    """The spellings of a build's root that can appear in a guard's output: as given and resolved
    (`/tmp` is `/private/tmp` here, and a guard prints `Path(__file__).resolve()`). Longest first."""
    spellings = {str(root), str(root.resolve())}
    return sorted((s.encode() for s in spellings), key=len, reverse=True)


def _normalise(data: bytes, markers: Sequence[bytes]) -> bytes:
    for marker in markers:
        data = data.replace(marker, BUILD_MARK.encode())
    return data


def make_reading(
    rc: int | None, out: bytes, err: bytes, markers: Sequence[bytes]
) -> Reading:
    out, err = _normalise(out, markers), _normalise(err, markers)
    return Reading(
        rc,
        hashlib.sha256(out).hexdigest()[:DIGEST_LEN],
        hashlib.sha256(err).hexdigest()[:DIGEST_LEN],
        err.decode("utf-8", "replace")[:OUTPUT_TEXT_CAP],
    )


TIMED_OUT = Reading(None, "", "", "")
# The `err` digest of a reading whose guard never started: rc is None as for a hang, and the
# classifier refuses both alike, but a report can tell them apart.
LAUNCH_FAILED = "launch-failed"


def launch_failure(exc: OSError) -> Reading:
    return Reading(None, "", LAUNCH_FAILED, f"could not launch: {exc}")


def preload(build_root: Path) -> None:
    """Import the build's `scripts/lib` modules once, in the parent, so each forked child finds
    them already loaded. Speed only: an import that fails here is left to fail in the child,
    exactly as it would in the real hook, and `sys.path` is restored so the guards insert their
    own lib path as they do when run for real."""
    lib = build_root / "scripts" / "lib"
    if not lib.is_dir():
        return
    sys.path.insert(0, str(lib))
    try:
        for module in pkgutil.iter_modules([str(lib)]):
            try:
                importlib.import_module(module.name)
            except BaseException:  # noqa: BLE001 - see docstring
                pass
    finally:
        sys.path.remove(str(lib))


def compile_guard(path: Path) -> Compiled | None:
    """The guard's source, compiled once; None when it will not compile, which the worker leaves
    to a genuine process so the syntax error is reported the way the interpreter reports it."""
    try:
        return Compiled(
            str(path), compile(path.read_bytes(), str(path), "exec", dont_inherit=True)
        )
    except (OSError, SyntaxError, ValueError):
        return None


def _status(code: object) -> int:
    """The exit status `sys.exit(code)` produces: None is 0, an int is itself, anything else is
    printed to stderr and is 1."""
    if code is None:
        return 0
    if isinstance(code, int):
        # A C `long` is truncated to the low byte by exit(); anything wider is -1, so 255.
        return code & 0xFF if -(2**63) <= code < 2**63 else 255
    print(code, file=sys.stderr)
    return 1


def _flushed(name: str) -> bool:
    """Flush `sys.<name>` as interpreter shutdown does. On failure the interpreter prints
    `Exception ignored on flushing sys.stdout: ...` and exits 120 — which a hook's caller reads as
    an allow — so this prints the same and says so."""
    try:
        getattr(sys, name).flush()
    except BaseException as exc:  # noqa: BLE001
        detail = "".join(traceback.format_exception_only(type(exc), exc))
        try:
            sys.stderr.write(f"Exception ignored on flushing sys.{name}:\n{detail}")
        except BaseException:  # noqa: BLE001 - stderr may be the very stream that failed
            pass
        return False
    return True


def _child(compiled: Compiled, files: tuple, neutral: str) -> None:
    """Runs in the forked child and never returns: the guard's source as `__main__`, then the
    same flush the interpreter does at shutdown (a failed one is status 120, as there), then
    `os._exit`. Interpreter finalisation is skipped on purpose — it was 45 of the 56 ms."""
    status = 0
    try:
        os.chdir(neutral)
        for target, staged in enumerate(files):
            os.dup2(staged.fileno(), target)
        os.closerange(3, 4096)  # a genuine hook holds only 0, 1 and 2
        _limit_writes()
        path = compiled.path
        # A script run directly IS the `__main__` module, with the loader and the empty spec the
        # interpreter gives it, and finds its siblings because its own directory is first on
        # sys.path — in place of the worker's, which would put this tool's directory there.
        main = types.ModuleType("__main__")
        main.__dict__.update(
            __file__=path,
            __cached__=None,
            __builtins__=builtins,
            __loader__=importlib.machinery.SourceFileLoader("__main__", path),
        )
        sys.modules["__main__"] = main
        sys.argv = [path]
        sys.path[0] = os.path.dirname(os.path.realpath(path))
        try:
            exec(compiled.code, main.__dict__)
        except SystemExit as exc:
            status = _status(exc.code)
        except BaseException as exc:  # noqa: BLE001 - the interpreter prints an uncaught error
            # The default hook prints the exception's own `__traceback__` and ignores the
            # argument, so the first entry — this frame, not the guard's — is dropped in place.
            trace = exc.__traceback__
            exc.__traceback__ = trace.tb_next if trace else None
            sys.excepthook(type(exc), exc, exc.__traceback__)
            status = 1
        for name in ("stdout", "stderr"):
            if not _flushed(name):
                status = 120  # unconditional, as in the interpreter: it overrides a real status
    except BaseException as exc:  # noqa: BLE001 - a tool failure must not read as a guard verdict
        status = 70
        # 70 is a status a guard can exit with, so the capture itself names the failure. Written
        # to the staged stderr file directly: the failure may precede the dup2 onto fd 2.
        try:
            os.write(
                files[2].fileno(),
                f"guard-parity internal error: {type(exc).__name__}: {exc}\n".encode(
                    "utf-8", "replace"
                ),
            )
        except BaseException:  # noqa: BLE001 - nowhere left to say it
            pass
    finally:
        os._exit(status)


def _reap(pid: int, timeout: float) -> int | None:
    """Wait for `pid`; kill it and return None after `timeout`. Polled, not alarmed: a guard
    installs its own SIGALRM (`guard_deadline`) and would replace a child-side one."""
    deadline = time.monotonic() + timeout
    delay = 0.0005
    while True:
        done, status = os.waitpid(pid, os.WNOHANG)
        if done:
            return os.waitstatus_to_exitcode(status)
        if time.monotonic() >= deadline:
            os.kill(pid, signal.SIGKILL)
            os.waitpid(pid, 0)
            return None
        time.sleep(delay)
        delay = min(delay * 2, 0.004)


def run_fork(
    compiled: Compiled,
    payload: bytes,
    neutral: Path,
    markers: Sequence[bytes],
    timeout: float = TIMEOUT_S,
) -> Reading:
    # Fresh files for every unit: a grandchild a guard left holding one of them could otherwise
    # write into the NEXT unit's capture.
    with (
        tempfile.TemporaryFile() as stdin,
        tempfile.TemporaryFile() as stdout,
        tempfile.TemporaryFile() as stderr,
    ):
        stdin.write(payload)
        stdin.flush()
        stdin.seek(0)
        _flush_inherited()
        pid = os.fork()
        if pid == 0:
            _child(compiled, (stdin, stdout, stderr), str(neutral))
        status = _reap(pid, timeout)
        if status is None:
            return TIMED_OUT
        return make_reading(status, _read_capped(stdout), _read_capped(stderr), markers)


def run_exec(
    script: Path,
    payload: bytes,
    neutral: Path,
    markers: Sequence[bytes],
    timeout: float = TIMEOUT_S,
) -> Reading:
    """The same unit as a genuine process — the script itself, through its shebang, as the
    harness runs it."""
    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        try:
            done = subprocess.run(
                [str(script)],
                input=payload,
                stdout=stdout,
                stderr=stderr,
                cwd=neutral,
                timeout=timeout,
                check=False,
                preexec_fn=_limit_writes,
            )
        except subprocess.TimeoutExpired:
            return TIMED_OUT
        except OSError as exc:
            return launch_failure(exc)
        return make_reading(
            done.returncode, _read_capped(stdout), _read_capped(stderr), markers
        )


def worker_main(argv: Sequence[str]) -> int:
    """`guard-parity.py --worker`: judge the rows in `--rows` against one build, writing one JSON
    line per unit and a final `done` line. A shard without that line is incomplete by definition."""
    parser = argparse.ArgumentParser(prog="guard-parity.py --worker")
    parser.add_argument("--build", required=True, type=Path)
    parser.add_argument("--guards", required=True)
    parser.add_argument("--rows", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--neutral", required=True, type=Path)
    parser.add_argument("--mode", choices=("fork", "exec"), default="fork")
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--timeout", type=float, default=TIMEOUT_S)
    parser.add_argument(
        "--key", help="the shard key to write once this shard is finished"
    )
    parser.add_argument("--key-file", type=Path)
    args = parser.parse_args(list(argv))
    guards = args.guards.split(",")
    # Absolute before any use: the child changes directory before it runs a guard.
    args.build = Path(os.path.abspath(args.build))
    scripts = args.build / "scripts"
    markers = build_markers(args.build)
    preload(args.build)
    compiled = {name: compile_guard(scripts / name) for name in guards}
    units = 0
    with (
        open(args.rows, encoding="utf-8") as rows,
        open(args.out, "w", encoding="utf-8") as out,
    ):
        _INHERITED.append(out)
        try:
            for line in rows:
                spec = json.loads(line)
                row = Row(spec["id"], spec["cwd"], spec["tool_input"])
                payload = hook_payload(row)
                for name in spec.get("guards") or guards:
                    readings = []
                    for _ in range(args.repeat):
                        if args.mode == "fork" and compiled[name] is not None:
                            reading = run_fork(
                                compiled[name],
                                payload,
                                args.neutral,
                                markers,
                                args.timeout,
                            )
                        else:
                            reading = run_exec(
                                scripts / name,
                                payload,
                                args.neutral,
                                markers,
                                args.timeout,
                            )
                        readings.append(reading.to_json())
                    out.write(
                        json.dumps({"id": row.id, "guard": name, "readings": readings})
                    )
                    out.write("\n")
                    units += 1
            out.write(json.dumps({"done": True, "units": units}) + "\n")
        finally:
            _INHERITED.remove(out)
    # After the `done` line is closed into the file, and by the worker rather than the parent:
    # the parent only reaches a key while collecting, after every shard has finished, so a run
    # killed during a long sweep would otherwise keep none of it.
    if args.key is not None and args.key_file is not None:
        args.key_file.write_text(args.key)
    return 0


# ---------------------------------------------------------------------------------------------
# Classification, the verification set, the stage runner, the verdict
# ---------------------------------------------------------------------------------------------

Unit = tuple[str, str]  # (row id, guard script name)

KINDS = (
    "SAME",
    "OPENED",
    "CLOSED",
    "OTHER",
    "OUTPUT_DIFF",
    "UNSTABLE",
    "INCOMPARABLE",
    "EXEC_MISMATCH",
)
DIFFERENCES = ("OPENED", "CLOSED", "OTHER")  # a verdict changed


class Verdict(NamedTuple):
    kind: str
    noisy: (
        bool  # readings within a build differed in output only; output was not compared
    )


# What every guard prints when it stops itself at its own deadline (`lib/guard_deadline.py`): a
# clean exit status (2 for the two push guards, 0 for the timing guard) with stderr identical on
# both builds. It is the guard giving up, never a judgment, so a reading carrying it proves
# nothing. The guard's exact phrase, not the bare word "deadline", which an ordinary refusal
# message may use.
DEADLINE_MARKER = re.compile(r"the guard reached its own \S+ deadline")


def _deadlined(reading: Reading | None) -> bool:
    return reading is not None and DEADLINE_MARKER.search(reading.text) is not None


def _output(reading: Reading) -> tuple[str, str]:
    return (reading.out, reading.err)


def classify(
    old: Sequence[Reading],
    new: Sequence[Reading],
    old_exec: Reading | None = None,
    new_exec: Reading | None = None,
) -> Verdict:
    """What one unit's readings say, as a pure function of the readings.

    `old` and `new` are the fork readings of each build (the first is the sweep's); the `_exec`
    readings are genuine-process runs, present only for units in the verification set.
    """
    # Checked FIRST: two absent readings compare equal, which would read as SAME — the reassuring
    # answer for a subject that never ran.
    if not old or not new:
        return Verdict("INCOMPARABLE", False)
    # A guard that reached its own deadline judged nothing, on whichever build and in whichever
    # mode: as unproven as a timeout, and never the same, opened or closed.
    if any(_deadlined(r) for r in (*old, *new, old_exec, new_exec)):
        return Verdict("INCOMPARABLE", False)
    if all(r.rc is None for r in old) or all(r.rc is None for r in new):
        return Verdict("INCOMPARABLE", False)
    noise = []  # per build: did its own readings differ in output alone?
    for readings in (old, new):
        # A timeout beside a status counts as disagreement.
        if len({r.rc for r in readings}) > 1:
            return Verdict("UNSTABLE", False)
        noise.append(len({_output(r) for r in readings}) > 1)
    noisy = any(noise)
    for readings, ran, own_noise in (
        (old, old_exec, noise[0]),
        (new, new_exec, noise[1]),
    ):
        if ran is None:
            continue
        # The audit run itself timed out: nothing was measured, so nothing is proven.
        if ran.rc is None:
            return Verdict("INCOMPARABLE", noisy)
        first = readings[0]
        # A build's OWN noise excuses only its own audit output, never the other build's.
        if ran.rc != first.rc or (not own_noise and _output(ran) != _output(first)):
            return Verdict("EXEC_MISMATCH", noisy)
    before, after = old[0], new[0]
    if before.rc != after.rc:
        # Refused before and not now is the fail-open shape.
        if before.rc == EXIT_BLOCK:
            return Verdict("OPENED", noisy)
        if after.rc == EXIT_BLOCK:
            return Verdict("CLOSED", noisy)
        return Verdict("OTHER", noisy)
    if not noisy and _output(before) != _output(after):
        return Verdict("OUTPUT_DIFF", noisy)
    return Verdict("SAME", noisy)


def select_verification(
    sweep: Mapping[str, Mapping[Unit, list[Reading]]],
    units: Sequence[Unit],
    sample: int,
    seed: str,
) -> tuple[list[Unit], set[Unit]]:
    """The units to re-run as stability repeats and genuine processes, and the seeded sample
    among them that noise is measured over.

    Every unit that drew anything but an allow in either build, every unit whose builds disagree,
    and every unit with a timeout or a missing reading is in — so no difference goes unconfirmed
    and a load-induced timeout must reproduce to stand — plus `sample` units drawn from all of
    them, which is what makes "noise=n/m" a rate over ordinary units rather than over the
    interesting ones.
    """
    chosen: set[Unit] = set()
    for unit in units:
        before = sweep["old"].get(unit)
        after = sweep["new"].get(unit)
        if not before or not after:
            chosen.add(unit)
            continue
        a, b = before[0], after[0]
        if a.rc != 0 or b.rc != 0 or (a.rc, *_output(a)) != (b.rc, *_output(b)):
            chosen.add(unit)
    pool = sorted(units)
    sampled = (
        set(random.Random(seed).sample(pool, min(sample, len(pool))))
        if sample > 0
        else set()
    )
    return sorted(chosen | sampled), sampled


def _read_shard(out_file: Path) -> tuple[dict[Unit, list[Reading]], bool]:
    """A shard's readings, and whether it ended in its `done` line. A line that is not JSON — the
    tail of a worker killed mid-write — ends the read, so the shard reads as unfinished instead
    of taking the whole run down."""
    units: dict[Unit, list[Reading]] = {}
    try:
        lines = out_file.read_text(encoding="utf-8").splitlines()
    except OSError:
        return units, False
    for line in lines:
        try:
            entry = json.loads(line)
        except ValueError:
            return units, False
        if entry.get("done"):
            return units, True
        units[(entry["id"], entry["guard"])] = [
            Reading.from_json(r) for r in entry["readings"]
        ]
    return units, False


class StageError(ParityError):
    """A stage could not be run at all."""


class StageResult(NamedTuple):
    readings: dict[str, dict[Unit, list[Reading]]]  # label -> unit -> readings
    incomplete: list[str]  # shard outputs that never reached their `done` line
    reused: int = 0  # shards taken from an earlier run rather than run again
    total: int = 0


def _split(rows: Sequence[dict], parts: int) -> list[list[dict]]:
    return [list(rows[i::parts]) for i in range(parts) if rows[i::parts]]


def _finished(out_file: Path) -> bool:
    """Whether a shard output ends in its `done` line."""
    return _read_shard(out_file)[1]


def run_stage(*args: Any, **kwargs: Any) -> StageResult:
    """`_run_stage`, then whatever it launched is gone. Workers run in their own sessions so a
    hung one can be killed as a group; that also means a Ctrl-C or a kill of this tool no longer
    reaches them, so an exit by ANY path, an exception included, kills and reaps them here."""
    live: list[subprocess.Popen] = []
    try:
        return _run_stage(*args, live=live, **kwargs)
    finally:
        for proc in live:
            if proc.poll() is None:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except OSError:
                    proc.kill()
                proc.wait()


def _run_stage(
    builds: Sequence[Build],
    rows: Sequence[dict],
    guards: Sequence[str],
    *,
    mode: str,
    repeat: int,
    workdir: Path,
    tag: str,
    workers: int,
    env: Mapping[str, str],
    neutral: Path,
    timeout: float = TIMEOUT_S,
    salt: str = "",
    worker_grace: float = WORKER_GRACE_S,
    live: list[subprocess.Popen] | None = None,
) -> StageResult:
    """Judge `rows` against every build at once — one set of worker processes per build, all
    walking the same rows in the same order, so the two builds meet the same state together.

    A row spec is `{id, cwd, tool_input}` and may carry `guards`, the subset to judge for that row.
    A worker that dies, or a shard that never wrote `done`, is reported in `incomplete`; the units
    it owed simply have no reading, which `classify` refuses to call the same.
    """
    workdir.mkdir(parents=True, exist_ok=True)
    per_build = max(1, workers // max(1, len(builds)))
    shards = _split(rows, per_build)
    launched = []
    reused = 0
    # The worker code is part of what produced a shard: edit the tool, and its shards are stale.
    try:
        tool = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    except OSError:
        # Unreadable, so nothing can be said about what produced a shard: a value that never
        # repeats, and the shard runs again.
        tool = "unreadable:" + os.urandom(16).hex()
    # Every (build, index) is judged BEFORE anything launches: a shard index is reused only when
    # EVERY build's shard at that index is reusable, so the two readings of a row were always
    # taken together, against one state of the repositories the guards read live.
    plan = []  # one tuple per (build, shard index)
    reusable = [True] * len(shards)
    for build in builds:
        for index, shard in enumerate(shards):
            base = workdir / f"{tag}-{build.label}-{index}"
            rows_file, out_file, err_file = (
                base.with_suffix(s) for s in (".rows", ".out", ".err")
            )
            key_file = base.with_suffix(".key")
            rows_text = "".join(json.dumps(r) + "\n" for r in shard)
            # The key covers everything that produced this shard: its rows, the build's commit,
            # and how it was run. It is written only after the shard has finished, so a killed
            # run leaves no key beside a half-written output.
            key = hashlib.sha256(
                (
                    rows_text
                    + json.dumps(
                        [
                            build.sha,
                            list(guards),
                            mode,
                            repeat,
                            timeout,
                            env_names_digest(env),
                            env_values_digest(env),
                            sys.executable,
                            sys.version,
                            salt,
                            tool,
                        ]
                    )
                ).encode()
            ).hexdigest()
            try:
                keyed = key_file.read_text() == key
            except (OSError, ValueError):  # ValueError: bytes that are not UTF-8
                keyed = False
            if not (keyed and _finished(out_file)):
                reusable[index] = False
            plan.append(
                (
                    build,
                    index,
                    shard,
                    rows_text,
                    rows_file,
                    out_file,
                    err_file,
                    key_file,
                    key,
                )
            )
    for (
        build,
        index,
        shard,
        rows_text,
        rows_file,
        out_file,
        err_file,
        key_file,
        key,
    ) in plan:
        if reusable[index]:
            reused += 1
            launched.append((build.label, out_file, key_file, key, None))
            continue
        for stale in (key_file, out_file):
            stale.unlink(missing_ok=True)
        rows_file.write_text(rows_text, encoding="utf-8")
        with open(err_file, "w") as err:
            proc = subprocess.Popen(
                [
                    sys.executable,
                    str(Path(__file__).resolve()),
                    "--worker",
                    "--build",
                    str(build.root),
                    "--guards",
                    ",".join(guards),
                    "--rows",
                    str(rows_file),
                    "--out",
                    str(out_file),
                    "--neutral",
                    str(neutral),
                    "--mode",
                    mode,
                    "--repeat",
                    str(repeat),
                    "--timeout",
                    str(timeout),
                    "--key",
                    key,
                    "--key-file",
                    str(key_file),
                ],
                env=dict(env),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=err,
                start_new_session=True,  # so the whole group can be killed
            )
        if live is not None:
            live.append(proc)
        units = sum(len(r.get("guards") or guards) for r in shard)
        budget = worker_grace + timeout * units * repeat
        launched.append(
            (
                build.label,
                out_file,
                key_file,
                key,
                (proc, time.monotonic() + budget),
            )
        )
    readings: dict[str, dict[Unit, list[Reading]]] = {b.label: {} for b in builds}
    incomplete = []
    for label, out_file, _key_file, _key, running in launched:
        proc, hung = None, False
        if running is not None:
            proc, deadline = running
            try:
                proc.wait(timeout=max(0.0, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                # A worker past its deadline is hung (build preload, a wedged fork server); its
                # whole group goes, so no grandchild outlives the run.
                hung = True
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except OSError:
                    proc.kill()
                proc.wait()
        units, finished = _read_shard(out_file)
        readings[label].update(units)
        # The key is the worker's to write, after its `done` line; the parent never writes one.
        if proc is not None and (hung or proc.returncode != 0 or not finished):
            incomplete.append(str(out_file))
    return StageResult(readings, incomplete, reused, len(launched))


def judge_units(
    units: Sequence[Unit],
    sweep: Mapping[str, Mapping[Unit, list[Reading]]],
    repeats: Mapping[str, Mapping[Unit, list[Reading]]],
    execs: Mapping[str, Mapping[Unit, list[Reading]]],
) -> dict[Unit, Verdict]:
    """Classify every unit from all the readings it has: the sweep's, the stability repeats and,
    for units that were audited, the genuine process."""
    verdicts = {}
    for unit in units:
        old = sweep["old"].get(unit, []) + repeats["old"].get(unit, [])
        new = sweep["new"].get(unit, []) + repeats["new"].get(unit, [])
        ran_old = (execs["old"].get(unit) or [None])[0]
        ran_new = (execs["new"].get(unit) or [None])[0]
        verdicts[unit] = classify(old, new, ran_old, ran_new)
    return verdicts


def count_noise(
    sampled: set[Unit],
    sweep: Mapping[str, Mapping[Unit, list[Reading]]],
    repeats: Mapping[str, Mapping[Unit, list[Reading]]],
) -> int:
    """How many sampled units read differently from one run of a build to the next."""
    noisy = 0
    for unit in sampled:
        for label in ("old", "new"):
            readings = sweep[label].get(unit, []) + repeats[label].get(unit, [])
            if len({(r.rc, *_output(r)) for r in readings}) > 1:
                noisy += 1
                break
    return noisy


class Evaluation(NamedTuple):
    """Everything `decide` needs, and nothing it would have to go and look up."""

    rows: int
    transcripts: int | None  # None when a frozen corpus was reused
    verdicts: dict[Unit, Verdict]
    sampled: int
    noise: int
    canaries: list[
        tuple[str, Reading | None, Reading | None]
    ]  # guard, OLD reading, NEW reading
    registration: list[str]
    limited: bool
    unsampled: bool
    incomplete_shards: list[str]
    min_rows: int
    tolerate: int
    guard_errors: (
        int  # internal-error records in the guards' log (this run's and any earlier)
    )

    def count(self, kind: str) -> int:
        return sum(1 for v in self.verdicts.values() if v.kind == kind)

    @property
    def comparable(self) -> int:
        """Units whose readings were actually compared. UNSTABLE and INCOMPARABLE are decided
        before any comparison happens, so neither counts. The floor and the RESULT line both
        read this one predicate."""
        return len(self.verdicts) - self.count("INCOMPARABLE") - self.count("UNSTABLE")


class Decision(NamedTuple):
    token: str  # PASS, FAIL or INCOMPLETE; ERROR never gets this far
    rc: int
    reasons: list[str]


def decide(ev: Evaluation) -> Decision:
    """ERROR is decided earlier. INCOMPLETE — can this run be believed at all? — comes before
    FAIL, and both come before PASS."""
    reasons = []
    if ev.rows < ev.min_rows:
        reasons.append(
            f"the corpus has {ev.rows} rows, below the floor of {ev.min_rows}"
        )
    # The floor is on what was actually COMPARED: tolerating unproven units must not be able to
    # license a PASS over a run that read almost nothing.
    if ev.comparable < ev.min_rows:
        reasons.append(
            f"only {ev.comparable} units were comparable, below the floor of {ev.min_rows}"
        )
    if ev.transcripts is not None and ev.transcripts < 1:
        reasons.append("no transcript was found")
    for guard, old, _new in ev.canaries:
        if old is None or old.rc != EXIT_BLOCK:
            got = "no reading" if old is None else f"rc={old.rc}"
            reasons.append(
                f"the OLD build did not refuse the {guard} canary ({got}): "
                "this instrument cannot see that guard block"
            )
    if ev.limited:
        reasons.append("--limit was given: a partial corpus can never PASS")
    if ev.unsampled:
        reasons.append("--sample 0: nothing was audited against a genuine process")
    unproven = ev.count("UNSTABLE") + ev.count("INCOMPARABLE")
    if unproven > ev.tolerate:
        reasons.append(
            f"{unproven} units are unproven (unstable or incomparable), above {ev.tolerate}"
        )
    if ev.count("EXEC_MISMATCH"):
        reasons.append(
            f"{ev.count('EXEC_MISMATCH')} units read differently as a genuine process "
            "than through the fork server"
        )
    if ev.incomplete_shards:
        reasons.append(f"{len(ev.incomplete_shards)} worker shards never finished")
    if ev.guard_errors > 0:
        reasons.append(
            f"{ev.guard_errors} guard-internal errors in the guard error log "
            "(each is a fail-closed refusal the guard never judged)"
        )
    if reasons:
        return Decision("INCOMPLETE", 3, reasons)
    differences = sum(ev.count(k) for k in DIFFERENCES) + len(ev.registration)
    if differences:
        return Decision("FAIL", 1, [f"{differences} verdict differences"])
    return Decision("PASS", 0, [])


# ---------------------------------------------------------------------------------------------
# Report, verdict line, command line
# ---------------------------------------------------------------------------------------------

MAX_LISTED = 200  # per section; sorted worst-first, so a cap keeps the least severe


class Detail(NamedTuple):
    """Where a report line gets its row and its readings from."""

    rows: Mapping[str, Row]
    sweep: Mapping[str, Mapping[Unit, list[Reading]]]
    repeats: Mapping[str, Mapping[Unit, list[Reading]]]


def _first(detail: Detail, label: str, unit: Unit) -> Reading | None:
    # A unit the sweep never read may still have readings from the repeats.
    got = detail.sweep[label].get(unit) or detail.repeats[label].get(unit)
    return got[0] if got else None


def _rc(reading: Reading | None) -> str:
    if reading is None:
        return "none"
    if reading.rc is None:
        return "launch-failed" if reading.err == LAUNCH_FAILED else "timeout"
    return str(reading.rc)


def _command(detail: Detail, identity: str) -> str:
    row = detail.rows.get(identity)
    if row is None:
        return "?"
    text = row.tool_input.get("command", "")
    return repr(text if len(text) <= 300 else text[:300] + "...")


def _reason(reading: Reading | None) -> str:
    if reading is None or not reading.text.strip():
        return ""
    # The same repr-style escaping the `cmd=` field gets: a guard quotes the command it refused,
    # and a command can hold ESC or OSC sequences that must not reach a terminal from a report.
    return repr(reading.text.strip().splitlines()[0][:160])[1:-1]


def _unit_line(kind: str, unit: Unit, detail: Detail) -> str:
    identity, guard = unit
    old, new = _first(detail, "old", unit), _first(detail, "new", unit)
    return (
        f"  {kind:<13} {guard} id={identity} old={_rc(old)} new={_rc(new)} "
        f"cmd={_command(detail, identity)}"
    )


def _section(title: str, lines: list[str], count: int | None = None) -> list[str]:
    """A titled block. `count` is what the title reports — units, where a unit takes several lines."""
    if not lines:
        return []
    shown = lines[:MAX_LISTED]
    if len(lines) > MAX_LISTED:
        shown.append(
            f"  ... {len(lines) - MAX_LISTED} more (all of them are in differences.jsonl)"
        )
    return [f"{title} ({len(lines) if count is None else count}):", *shown, ""]


def outcome_table(ev: Evaluation, detail: Detail, guards: Sequence[str]) -> list[str]:
    """Per guard and build: how many units were allowed, refused, or ended any other way. A table
    in which nothing was ever refused is the tell of a sweep that cannot see a block."""
    lines = ["outcomes (the sweep's first reading of each unit):"]
    for guard in guards:
        cells = []
        for label in ("old", "new"):
            counts = {"allow": 0, "block": 0, "other": 0, "none": 0}
            for (identity, name), got in detail.sweep[label].items():
                if name != guard or canary_guard(identity) is not None:
                    continue
                rc = got[0].rc if got else None
                key = (
                    "none"
                    if rc is None
                    else "allow"
                    if rc == 0
                    else "block"
                    if rc == 2
                    else "other"
                )
                counts[key] += 1
            cells.append(f"{label}: " + " ".join(f"{k}={v}" for k, v in counts.items()))
        lines.append(f"  {guard:<27} " + "   ".join(cells))
    return [*lines, ""]


def render_report(
    header: list[str],
    ev: Evaluation,
    decision: Decision,
    detail: Detail,
    guards: Sequence[str],
) -> str:
    out = list(header) + [""]
    out += outcome_table(ev, detail, guards)
    by_kind: dict[str, list[Unit]] = {}
    for unit, verdict in sorted(
        ev.verdicts.items(), key=lambda kv: (kv[0][1], kv[0][0])
    ):
        by_kind.setdefault(verdict.kind, []).append(unit)
    for kind in DIFFERENCES:
        lines = []
        for unit in by_kind.get(kind, []):
            lines.append(_unit_line(kind, unit, detail))
            old, new = _first(detail, "old", unit), _first(detail, "new", unit)
            for label, reading in (("old", old), ("new", new)):
                if _reason(reading):
                    lines.append(f"      {label} said: {_reason(reading)}")
        out += _section(
            f"verdict differences: {kind}", lines, len(by_kind.get(kind, []))
        )
    out += _section(
        "registration differences", [f"  {line}" for line in ev.registration]
    )
    for kind in ("EXEC_MISMATCH", "UNSTABLE", "INCOMPARABLE"):
        lines = [_unit_line(kind, u, detail) for u in by_kind.get(kind, [])]
        out += _section(kind, lines)
    groups: dict[tuple, list[Unit]] = {}
    for unit in by_kind.get("OUTPUT_DIFF", []):
        old, new = _first(detail, "old", unit), _first(detail, "new", unit)
        groups.setdefault((unit[1], old.rc, old.err, new.err), []).append(unit)
    lines = []
    for (guard, rc, _a, _b), members in sorted(
        groups.items(), key=lambda kv: -len(kv[1])
    ):
        unit = members[0]
        old, new = _first(detail, "old", unit), _first(detail, "new", unit)
        lines.append(f"  {guard} rc={rc}: {len(members)} units, e.g. id={unit[0]}")
        lines.append(f"      old said: {_reason(old) or '(nothing)'}")
        lines.append(f"      new said: {_reason(new) or '(nothing)'}")
    out += _section("output-only differences, grouped", lines, len(groups))
    out.append("canaries (each must be refused by the OLD build):")
    for guard, old, new in ev.canaries:
        out.append(f"  {guard}: old={_rc(old)} new={_rc(new)}")
    out.append("")
    if decision.reasons:
        out.append(f"{decision.token}:")
        out += [f"  - {r}" for r in decision.reasons]
        out.append("")
    return "\n".join(out)


def verdict_line(ev: Evaluation, decision: Decision) -> str:
    units = len(ev.verdicts)
    refused = sum(
        1 for _g, old, _n in ev.canaries if old is not None and old.rc == EXIT_BLOCK
    )
    return (
        f"RESULT: {decision.token} rc={decision.rc} rows={ev.rows} units={units} "
        f"comparable={ev.comparable} "
        f"verdict_diffs={sum(ev.count(k) for k in DIFFERENCES) + len(ev.registration)} "
        f"output_diffs={ev.count('OUTPUT_DIFF')} unstable={ev.count('UNSTABLE')} "
        f"incomparable={ev.count('INCOMPARABLE')} exec_mismatch={ev.count('EXEC_MISMATCH')} "
        f"noise={ev.noise}/{ev.sampled} canaries={refused}/{len(ev.canaries)}"
    )


def error_line(message: str) -> str:
    return "RESULT: ERROR rc=2 " + " ".join(message.split())


def write_report(path: Path, text: str) -> None:
    """The report file. `backslashreplace`: a lone surrogate must not turn the write into a
    traceback that ends the run with no RESULT line."""
    with open(path, "w", encoding="utf-8", errors="backslashreplace") as out:
        out.write(text)


def emit(text: str) -> None:
    """Print `text` to stdout without ever raising on an unencodable character."""
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    sys.stdout.write(text.encode(encoding, "backslashreplace").decode(encoding))


def _inside_worktree(path: Path) -> bool:
    probe = path
    while not probe.exists():
        probe = probe.parent
    done = subprocess.run(
        ["git", "-C", str(probe), "rev-parse", "--show-toplevel"],
        capture_output=True,
        check=False,
    )
    return done.returncode == 0


def _python_version(env: Mapping[str, str]) -> str:
    try:
        done = subprocess.run(
            ["python3", "--version"],
            capture_output=True,
            text=True,
            env=dict(env),
            check=False,
        )
        return (done.stdout or done.stderr).strip() or "unknown"
    except OSError:
        return "unavailable"


def _count_log_records(path: Path) -> int:
    """How many internal-error records the publication guard wrote during the replay — each is a
    fail-closed refusal, so the status comparison already saw it; the count says how many."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return 0
    # `===== <stamp> <ExceptionClass> =====`; the log also holds commands verbatim, and one of
    # those can begin with the same marker.
    header = re.compile(r"===== \S+ \S+ =====")
    return sum(1 for line in text.splitlines() if header.fullmatch(line))


def choose_guards(
    requested: Sequence[str], pattern: str | None
) -> tuple[list[str], list[str]]:
    """The guards to judge and the header lines for those that will not be."""
    for name in requested:
        if name in NOT_JUDGED:
            raise ParityError(f"{name} is not judged: {NOT_JUDGED[name]}")
        if name not in JUDGED:
            raise ParityError(
                f"{name} is not a guard this tool can judge ({', '.join(JUDGED)})"
            )
    names = list(requested) or list(JUDGED)
    skipped = []
    if "git-timing-guard.py" in names and pattern is None:
        if requested:
            raise ParityError(
                "git-timing-guard.py was requested but no repo pattern is available"
            )
        names.remove("git-timing-guard.py")
        skipped.append(
            "not judged: git-timing-guard.py (no repo pattern; see --timing-pattern)"
        )
    return names, skipped


def run(args: argparse.Namespace) -> tuple[int, str]:
    """The whole run: returns (exit status, the report text ending in its RESULT line)."""
    art = Path(args.artifact_dir).expanduser().resolve()
    if _inside_worktree(art):
        raise ParityError(
            f"--artifact-dir {art} is inside a git worktree; it would fail the "
            "next step's clean-tree precondition"
        )
    art.mkdir(parents=True, exist_ok=True)
    art.chmod(0o700)  # it holds raw commands
    # Held until this function returns. Two runs in one directory would delete each other's
    # fixtures and shards while both are judging.
    lock = open(art / ".lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        raise ParityError(
            f"another run holds {art / '.lock'}; two runs cannot share an artifact directory"
        ) from exc
    # A verdict from an earlier run in this directory must not outlive a run that fails: a stale
    # report ending in a RESULT line reads as this run's.
    for stale in ("report.txt", "differences.jsonl"):
        (art / stale).unlink(missing_ok=True)
    scope = (
        Path(args.scope)
        if args.scope
        else Path(
            subprocess.run(
                ["git", "rev-parse", "--show-toplevel"],
                capture_output=True,
                text=True,
                check=False,
            ).stdout.strip()
            or "."
        )
    )
    real_home = Path(os.path.expanduser("~"))
    pattern, pattern_source = read_timing_pattern(real_home, args.timing_pattern)
    names, header_skips = choose_guards(args.guard, pattern)

    builds = [
        pin_build(scope, args.old, "old", art / "build-old"),
        pin_build(scope, args.new, "new", art / "build-new"),
    ]
    require_guards(builds, names)
    head = _scope_git(scope, "rev-parse", "HEAD").stdout.decode().strip()
    for build in builds:
        # A revision that is HEAD is graded as committed; edits to the tree beside it are not.
        if build.sha == head and (dirty := uncommitted(scope)):
            raise ParityError(
                f"scripts/ has uncommitted changes ({', '.join(dirty[:5])}); {build.label} = "
                f"{build.rev} would grade HEAD, not them — commit first"
            )
    regs = [registrations(scope, b.sha) for b in builds]
    delta = registration_delta(regs[0], regs[1])
    unjudged = unjudged_registered(regs[1], names) if regs[1] is not None else []

    if args.corpus:
        rows, stats = read_corpus(Path(args.corpus)), None
        corpus_note = f"corpus: {args.corpus} (frozen), {len(rows)} rows"
    else:
        rows, stats = extract_corpus(Path(args.transcripts).expanduser())
        write_corpus(rows, art / "corpus.jsonl")
        corpus_note = (
            f"corpus: {len(rows)} rows from {stats.transcripts} transcripts "
            f"({stats.records} Bash records; skipped {dict(sorted(stats.skipped.items()))})"
        )
    digest = corpus_digest(rows)
    if args.limit > 0:
        rows = rows[: args.limit]

    for stale in ("home", "canaries"):
        shutil.rmtree(art / stale, ignore_errors=True)
    fixture = make_fixture_home(art / "home", real_home, pattern)
    guard_log = art / "guard-errors.log"
    # The log outlives a run, and so do the shards a resume reuses: every record in it is
    # charged, because an error logged while an EARLIER run made a reused shard would otherwise
    # be invisible to this one. A directory that carries an old record needs a fresh one.
    env = replay_env(os.environ, fixture, guard_log)
    git_names = sorted(k for k in env if k.startswith("GIT_"))
    neutral = art / "neutral"
    neutral.mkdir(exist_ok=True)
    canaries = build_canaries(art / "canaries", pattern, names)

    specs = [{**c._asdict(), "guards": [canary_guard(c.id)]} for c in canaries]
    specs += [r._asdict() for r in rows]
    units = [(s["id"], g) for s in specs for g in (s.get("guards") or names)]
    common = dict(
        workers=args.workers,
        env=env,
        neutral=neutral,
        timeout=args.timeout,
        salt=f"{pattern}|{digest}",  # what the fixture HOME and the corpus were made from
    )

    sweep = run_stage(
        builds,
        specs,
        names,
        mode="fork",
        repeat=1,
        workdir=art / "work",
        tag="sweep",
        **common,
    )
    chosen, sampled = select_verification(sweep.readings, units, args.sample, digest)
    wanted: dict[str, list[str]] = {}
    for identity, guard in chosen:
        wanted.setdefault(identity, []).append(guard)
    by_id = {s["id"]: s for s in specs}
    verify = [{**by_id[i], "guards": g} for i, g in sorted(wanted.items())]
    repeats = run_stage(
        builds,
        verify,
        names,
        mode="fork",
        repeat=2,
        workdir=art / "work",
        tag="repeat",
        **common,
    )
    execs = run_stage(
        builds,
        verify,
        names,
        mode="exec",
        repeat=1,
        workdir=art / "work",
        tag="exec",
        **common,
    )

    stages = (sweep, repeats, execs)
    verdicts = judge_units(units, sweep.readings, repeats.readings, execs.readings)
    detail = Detail(
        {r.id: r for r in [*canaries, *rows]}, sweep.readings, repeats.readings
    )
    ev = Evaluation(
        rows=len(rows),
        transcripts=None if stats is None else stats.transcripts,
        verdicts=verdicts,
        sampled=len(sampled),
        noise=count_noise(sampled, sweep.readings, repeats.readings),
        canaries=[
            (
                canary_guard(c.id),
                _first(detail, "old", (c.id, canary_guard(c.id))),
                _first(detail, "new", (c.id, canary_guard(c.id))),
            )
            for c in canaries
        ],
        registration=delta,
        limited=args.limit > 0,
        unsampled=args.sample <= 0,
        incomplete_shards=[*sweep.incomplete, *repeats.incomplete, *execs.incomplete],
        min_rows=args.min_rows,
        tolerate=args.tolerate_unproven,
        guard_errors=_count_log_records(guard_log),
    )
    decision = decide(ev)

    header = [
        "guard-parity: replay of real commands through two pinned guard builds",
        *(
            f"{b.label}: {b.rev} -> {b.sha[:12]} ({b.files} files verified against the tree)"
            for b in builds
        ),
        corpus_note
        + f", digest {digest[:12]}"
        + (f", LIMITED to {args.limit}" if args.limit else ""),
        f"judged: {', '.join(names)}",
        *header_skips,
        *(f"not judged: {n} ({why})" for n, why in unjudged),
        *(
            [f"timing guard: window FORCED always-open; pattern from {pattern_source}"]
            if "git-timing-guard.py" in names
            else []
        ),
        *(
            [f"GIT_* names in the environment (names only): {', '.join(git_names)}"]
            if git_names
            else []
        ),
        f"guard error log: {ev.guard_errors} records in the file",
        f"environment: {len(env)} names (digest {env_names_digest(env)}), HOME mirrored at {fixture}, "
        f"PYTHONHASHSEED=0",
        f"interpreters: workers {sys.version.split()[0]}; genuine-process audit "
        f"{_python_version(env)}",
        f"artifacts: {art}",
        f"units: {len(units)} ({len(chosen)} verified, {len(sampled)} sampled)",
        f"shards: {sum(s.reused for s in stages)} reused of {sum(s.total for s in stages)}",
    ]
    text = render_report(header, ev, decision, detail, names)
    with open(art / "differences.jsonl", "w", encoding="utf-8") as out:
        for unit, verdict in sorted(verdicts.items()):
            if verdict.kind == "SAME":
                continue
            out.write(
                json.dumps(
                    {
                        "id": unit[0],
                        "guard": unit[1],
                        "kind": verdict.kind,
                        "noisy": verdict.noisy,
                        "cwd": detail.rows[unit[0]].cwd
                        if unit[0] in detail.rows
                        else None,
                        "command": detail.rows[unit[0]].tool_input.get("command")
                        if unit[0] in detail.rows
                        else None,
                        "old": [
                            r.to_json()
                            for r in sweep.readings["old"].get(unit, [])
                            + repeats.readings["old"].get(unit, [])
                        ],
                        "new": [
                            r.to_json()
                            for r in sweep.readings["new"].get(unit, [])
                            + repeats.readings["new"].get(unit, [])
                        ],
                    }
                )
                + "\n"
            )
    text += "\n" + verdict_line(ev, decision) + "\n"
    write_report(art / "report.txt", text)
    return decision.rc, text


def _bounded(kind, low: float, *, strict: bool = False):
    """An argparse type: `kind(text)`, refused (a usage error) below `low` — at or below it when
    `strict`."""

    def parse(text: str):
        try:
            value = kind(text)
        except ValueError as exc:
            raise argparse.ArgumentTypeError(f"invalid value {text!r}") from exc
        if value != value or (value <= low if strict else value < low):
            bound = f"greater than {low:g}" if strict else f"at least {low:g}"
            raise argparse.ArgumentTypeError(f"{text!r} must be {bound}")
        return value

    return parse


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="guard-parity.py",
        description="Replay real commands through two pinned guard builds; list every difference.",
    )
    parser.add_argument("--old", required=True, help="the baseline revision")
    parser.add_argument(
        "--new", default="HEAD", help="the revision under test (default HEAD)"
    )
    parser.add_argument(
        "--scope", help="the repository holding both revisions (default: cwd's)"
    )
    parser.add_argument(
        "--artifact-dir", required=True, help="a directory OUTSIDE any worktree"
    )
    parser.add_argument(
        "--guard", action="append", default=[], help="judge only these (repeat)"
    )
    parser.add_argument("--transcripts", default="~/.claude/projects/")
    parser.add_argument(
        "--corpus", help="reuse a frozen corpus.jsonl instead of extracting"
    )
    parser.add_argument(
        "--workers",
        type=_bounded(int, 1),
        default=min(os.cpu_count() or 2, DEFAULT_WORKERS_CAP),
        help=f"worker processes, split across the two builds (default: min(CPU count, "
        f"{DEFAULT_WORKERS_CAP}); an explicit value is used as given)",
    )
    parser.add_argument(
        "--sample", type=_bounded(int, 0), default=4000, help="units audited at random"
    )
    parser.add_argument("--min-rows", type=_bounded(int, 1), default=1000)
    parser.add_argument("--tolerate-unproven", type=int, default=0)
    parser.add_argument(
        "--timing-pattern", help="GUARD_REPO_PATTERN when no real conf exists"
    )
    parser.add_argument(
        "--limit",
        type=_bounded(int, 0),
        default=0,
        help="first N rows only (never PASS)",
    )
    parser.add_argument(
        "--timeout",
        type=_bounded(float, 0, strict=True),
        default=TIMEOUT_S,
        help="seconds per execution",
    )
    return parser


def install_termination_handlers() -> dict[int, Any]:
    """Turn SIGTERM and SIGHUP into an exit, so the `finally` blocks run. The default action
    ends the process at once and skips them, which leaves every worker (each in a session of its
    own) running against an artifact directory the next run will reuse. Returns the handlers it
    replaced, so a caller can put them back."""

    def leave(signum: int, _frame: object) -> None:
        raise SystemExit(128 + signum)

    return {
        signum: signal.signal(signum, leave)
        for signum in (signal.SIGTERM, signal.SIGHUP)
    }


def main(argv: Sequence[str]) -> int:
    if argv and argv[0] == "--worker":
        return worker_main(argv[1:])
    install_termination_handlers()
    try:
        args = build_parser().parse_args(list(argv))
    except SystemExit as exc:
        if exc.code in (0, None):
            raise
        print(error_line("usage error (see the message above)"))
        return 2
    try:
        rc, text = run(args)
    except ParityError as exc:
        emit(error_line(str(exc)) + "\n")
        return 2
    except Exception as exc:  # noqa: BLE001 - last resort: rc 1 is FAIL, a traceback is no verdict
        emit(error_line(f"unexpected {type(exc).__name__}: {exc}") + "\n")
        return 2
    emit(text)
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

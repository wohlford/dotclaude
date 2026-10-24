#!/usr/bin/env python3
# Script: memory-status-check.py
# Purpose: Report memory-store status claims that git ancestry has falsified (not promoted, not published)
# Usage: memory-status-check.py [--memory-dir DIR] [--dev REPO] [--production REPO] [--only promoted|published] [--range A..B] [--since ISO8601Z] [--all]
"""Report memory-store status claims that git ancestry has falsified. Read-only.

A status line in a memory record ("NOT promoted", "unpublished") is written once, when it is true,
and a later promote or publish falsifies it while nothing re-checks. Ancestry only advances, so only
a claim of NON-attainment can go stale; this tool finds those, names each by `file:line` with the
evidence and a suggested correction, and ends in one verdict line.

It never writes: it opens no file for writing and creates no file, in the memory directory or
anywhere else. A fix is a hint for a person or agent to apply with the Edit tool.

Terminal line, always the LAST line of stdout:
    RESULT: <PASS|FAIL|ERROR|INCOMPLETE|NOT-APPLICABLE> rc=<n> checks=<live>/<stale>/<skip> ...
Clean is exactly `RESULT: PASS`. `NOT-APPLICABLE` is its own clean outcome, never a pass. The line
cannot be emitted on SIGKILL, so its ABSENCE means the run did not complete.

Exit codes:
  0   PASS, or NOT-APPLICABLE
  1   FAIL: at least one listed STALE unit
  2   ERROR: the check could not be made (a named reason line precedes the verdict)
  3   INCOMPLETE: an uncaught exception (distinct from 1, so a crash never reads as "stale found")
  129/130/143   INCOMPLETE: caught SIGHUP/SIGINT/SIGTERM

Design and measurements: specs/2026-10-20-memory-status-staleness-check.md.
"""

from __future__ import annotations

import argparse
import math
import os
import re
import signal
import subprocess
import sys
import time
import traceback
from bisect import bisect_left
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import NamedTuple

BACKLOG_NAME = "BACKLOG.md"
INDEX_NAME = "MEMORY.md"
DEV_REF = "refs/heads/dev"
WATERMARK_REF = "refs/published/main"
ORIGIN_REF = "refs/remotes/origin/main"
PUBLICATION_TOML = ".publication.toml"
CLASSES = ("promoted", "published")
COMMIT_ID = re.compile(r"[0-9a-f]{7,40}")
GIT_TIMEOUT = 30.0
DEFAULT_DEADLINE = 60.0
# Every variable that changes WHICH repository, objects or refs a read sees. GIT_INDEX_FILE is
# kept for defence, as is GIT_NAMESPACE: no read here depends on either, so no row can tell.
AMBIENT_GIT_ENV = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_COMMON_DIR",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_NAMESPACE",
    "GIT_REPLACE_REF_BASE",
    "GIT_NO_REPLACE_OBJECTS",
)
STATUS_PARAGRAPH = re.compile(r"^\W{0,6}(?:superseded )?status\b", re.IGNORECASE)
STATUS_SCAN_LINES = 40
FENCE_OPEN = re.compile(r"^ {0,3}(`{3,}|~{3,})")
SINCE_FORMATS = ("%Y%m%dT%H%M%SZ", "%Y-%m-%dT%H:%M:%SZ")
REFLOG_LINE = re.compile(r"^([0-9a-f]{40}) HEAD@\{(\d+)\}$")
CLAIM_PATTERNS = {
    "promoted": re.compile(
        r"\b(?:not[\s-]+(?:yet[\s-]+)?|un-?)(?:promoted|propagated)\b", re.IGNORECASE
    ),
    "published": re.compile(
        r"\b(?:not[\s-]+(?:yet[\s-]+)?|un-?)(?:published|pushed)\b", re.IGNORECASE
    ),
}
CLAIM_NAMES = {"promoted": "not-promoted", "published": "not-published"}
STAGE_NAMES = {"promoted": "PROMOTED", "published": "PUBLISHED"}
HEX_TOKEN = re.compile(r"(?<![0-9A-Za-z_])[0-9a-f]{7,40}(?![0-9A-Za-z_])")
TAG_TOKEN = re.compile(r"(?<![0-9A-Za-z_.])v\d+\.\d+\.\d+(?![0-9A-Za-z_])")
# A base marker, then only connectors (dev, main, on, of, at, is, was, commit, ":", "=" or "@")
# up to the token:
# any other word ("base branch X at T", "base fix landed T") means the token is not the base.
BASE_CLAUSE = re.compile(
    r"(?:\bbase\b|\bbased on\b|\bcut from\b|\bbranch(?:ed)? (?:from|off)\b)"
    r"(?:\s|[:=@]|\b(?:dev|main|on|of|at|is|was|commit)\b){0,8}$",
    re.IGNORECASE,
)
CLAUSE_REACH = 45
LABEL = re.compile(r"\*\*(LANDED|PROMOTED)\b(?=\s*[.;:,\u2014\u2013])")
HIGHER_LABEL = {
    "LANDED": re.compile(r"\b(?:PROMOTED|PUBLISHED)\b"),
    "PROMOTED": re.compile(r"\bPUBLISHED\b"),
}
LABEL_CLAIMS = {
    "LANDED": ("promoted", "label-landed"),
    "PROMOTED": ("published", "label-promoted"),
}
INDEX_LINK = re.compile(r"\]\(([^)\s#]{1,200}\.md)(?:#[^)\s]{0,100})?\)")
DIRECT_CHILD = re.compile(r"(?:\./)?[^/\\:]+")
LABEL_TAIL = re.compile(r"\s*[.;:,\u2014\u2013]")
LINK_TARGET = re.compile(r"\]\([^)\n]{0,300}\)|\[\[[^\]\n]{0,300}\]\]")
# A count is a WHOLE integer token ("5 files", "0"): digits glued to "/", "=", "-", ".", ":" or a
# word character belong to a ratio, a date, a decimal or a name, and the claim after them is live.
COUNT_BEFORE = re.compile(r"(?<![\w/=.:-])\d+\+?\s+(?:[A-Za-z-]+\s+)?$")
COUNT_WINDOW = 41
QUOTE_PREFIX = re.compile(r"(?m)^[ \t]{0,3}>[ \t>]{0,10}")
ANCHOR = re.compile(r"at the time of writing|as of \d{4}-\d\d-\d\d", re.IGNORECASE)
ANCHOR_REACH = 120
# Straight double quotes pair per line, and only on a line holding an even number of them: an odd
# count (`a 5" pipe`) would otherwise pair across the real claims after it.
DOUBLE_QUOTED = re.compile(r'"[^"\n]{1,200}"')
QUOTE_SPANS = (
    re.compile("\u201c[^\u201d\n]{1,200}\u201d"),
    re.compile(r"(?<![A-Za-z])'[^'\n]{1,80}'(?![A-Za-z])"),
)


class State:
    """Counters the verdict line reports; module-level so the signal handler can read them."""

    live = 0
    stale = 0
    skip = 0
    notes = 0
    outside = 0
    files = 0
    zones = 0
    topic_zones = 0
    header_done = False
    child: subprocess.Popen | None = None
    hdr: dict[str, str] = {
        "memory": "unknown",
        "dev": "unknown",
        "production": "unresolved",
        "watermark": "unresolved",
        "origin-main": "unresolved",
        "classes": "promoted,published",
        "range": "none",
    }
    promoted_anchors: list[list[str]] = []
    classes: tuple[str, ...] = CLASSES
    deadline = 0.0
    rows: list[str] = []


S = State()


class ErrorExit(Exception):
    """The check could not be made; carries the reason the preceding line must name."""

    def __init__(self, reason: str, detail: str = "", classes: tuple[str, ...] = ()):
        super().__init__(reason)
        self.reason = reason
        self.detail = detail
        self.classes = classes


class StdoutFailed(Exception):
    """Writing to stdout failed (a closed pipe, a read-only descriptor, a full disk): nobody listens."""


class NotApplicable(Exception):
    """The tool means nothing here (not an adopted repository); a clean, distinct outcome."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class Zone(NamedTuple):
    kind: str
    line: int
    text: str


class Claim(NamedTuple):
    klass: str
    start: int
    end: int
    excluded: str | None
    name: str


class Unit(NamedTuple):
    """One judged subject: a zone paragraph of a topic file, with its claims."""

    subject: str
    name: str
    zone: Zone
    claims: list[Claim]
    source: str


class Verdict(NamedTuple):
    kind: str
    reason: str
    evidence: list[str]
    anchors: list[str]


def emit(line: str) -> None:
    try:
        print(line, flush=True)
    except (OSError, ValueError) as err:
        # ValueError covers UnicodeEncodeError and "I/O operation on closed file".
        raise StdoutFailed(str(err)) from err


def use_lossless_stdout() -> None:
    """Make stdout write what it cannot encode as an escape instead of raising.

    A memory record holds an em dash or an accent; a stdout configured as ASCII (a bare
    `PYTHONIOENCODING=ascii`, a C locale) would otherwise die half-way through the report.
    """
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if reconfigure is not None:
        try:
            reconfigure(errors="backslashreplace")
        except (OSError, ValueError):
            pass


def known_header() -> str:
    """The parameter header from whatever was resolved before the run stopped."""
    fields = " ".join(f"{k}={v}" for k, v in S.hdr.items())
    return f"memory-status-check: {fields} files={S.files} zones={S.zones}"


def emit_header_once(line: str) -> None:
    """Every outcome starts with the header, so a wrong default is visible even on a clean one."""
    if not S.header_done:
        S.header_done = True
        emit(line)


def row(line: str) -> None:
    """Queue a row; rows print after the header, in the order they were queued."""
    S.rows.append(line)


def flush_rows() -> None:
    # Pop BEFORE printing: a write that fails part-way must not leave the row queued, or the
    # verdict path would print it a second time.
    while S.rows:
        emit(S.rows.pop(0))


def result_line(status: str, rc: int) -> str:
    return (
        f"RESULT: {status} rc={rc} checks={S.live}/{S.stale}/{S.skip} "
        f"files={S.files} notes={S.notes} outside-range={S.outside}"
    )


def finish(status: str, rc: int) -> int:
    flush_rows()
    emit(result_line(status, rc))
    return rc


def error_line(err: ErrorExit) -> str:
    """A fault of a requested class is NOT-MEASURABLE; every other fault is an ERROR line."""
    if err.classes:
        return f"NOT-MEASURABLE class={','.join(err.classes)} reason={err.reason}"
    detail = " ".join(err.detail.split())
    return f"ERROR reason={err.reason} detail={detail}"


def deadline_secs() -> float:
    """The whole-run budget; only a positive finite number replaces the default."""
    raw = os.environ.get("MEMORY_STATUS_DEADLINE_SECS")
    if raw is None:
        return DEFAULT_DEADLINE
    try:
        value = float(raw)
    except ValueError:
        value = float("nan")
    if math.isfinite(value) and value > 0:
        return value
    print(
        f"memory-status-check: ignoring MEMORY_STATUS_DEADLINE_SECS={raw!r}",
        file=sys.stderr,
    )
    return DEFAULT_DEADLINE


def check_deadline() -> None:
    """CPU-bound judging checks the run budget too; git calls check it themselves."""
    if S.deadline and time.monotonic() > S.deadline:
        raise ErrorExit("deadline", "the run budget is spent", S.classes)


def git(repo: Path, *args: str, stdin: str | None = None):
    """Run git in `repo`, bounded by the per-call timeout and the whole-run deadline.

    The ambient variables listed in AMBIENT_GIT_ENV (the ones that choose a repository, its
    objects or its refs) are removed: a hook or a wrapper that exported one would otherwise
    redirect every `-C` to a repository the caller never named.
    """
    remaining = S.deadline - time.monotonic()
    if remaining <= 0:
        raise ErrorExit("deadline", "the run budget is spent", S.classes)
    env = {k: v for k, v in os.environ.items() if k not in AMBIENT_GIT_ENV}
    cmd = ["git", "-c", "log.showSignature=false", "-C", str(repo), *args]
    # Its own session, so a signal or a timeout can end git AND anything git started.
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
        start_new_session=True,
    )
    S.child = proc
    try:
        out, err = proc.communicate(input=stdin, timeout=min(GIT_TIMEOUT, remaining))
    except subprocess.TimeoutExpired:
        kill_child()
        proc.communicate()
        raise ErrorExit(
            "deadline", f"git {args[0]} exceeded its time budget", S.classes
        ) from None
    finally:
        S.child = None
    return subprocess.CompletedProcess(cmd, proc.returncode, out, err)


def kill_child() -> None:
    """End the running git call's whole process group; safe from a signal handler."""
    child = S.child
    if child is not None:
        try:
            os.killpg(child.pid, signal.SIGKILL)
        except OSError:
            pass


class Parser(argparse.ArgumentParser):
    def error(self, message: str):  # type: ignore[override]
        raise ErrorExit("usage", message)


def parse(argv: list[str]) -> argparse.Namespace:
    p = Parser(prog="memory-status-check.py", description=__doc__.splitlines()[0])
    p.add_argument("--memory-dir")
    p.add_argument("--dev")
    p.add_argument("--production")
    p.add_argument("--only", choices=("promoted", "published"))
    p.add_argument("--range")
    p.add_argument("--since")
    p.add_argument("--all", action="store_true")
    return p.parse_args(argv)


def topic_files(mem: Path) -> list[Path]:
    """The scanned files: top-level *.md, BACKLOG.md excluded by exact name."""
    return sorted(p for p in mem.glob("*.md") if p.is_file() and p.name != BACKLOG_NAME)


def split_frontmatter(lines: list[str]) -> tuple[int, bool]:
    """Return (index of the first body line, whether the frontmatter closed)."""
    if not lines or lines[0].strip() != "---":
        return 0, True
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return i + 1, True
    return len(lines), False


def unwrap_description(value: str) -> str:
    """Remove one pair of enclosing YAML double quotes; they are not a quotation of a claim."""
    value = value.strip()
    if len(value) > 1 and value[0] == '"' and value[-1] == '"':
        return value[1:-1]
    return value


def fence_flags(lines: list[str], start: int) -> list[bool]:
    """True for every line from `start` that is a code-fence delimiter or sits inside a fence.

    Prose in a fenced block is an example, not a status: a fence often holds blank lines, so
    paragraph splitting alone would open a zone inside it. An unterminated fence runs to the end.
    """
    flags = [False] * len(lines)
    closer = ""
    for i in range(start, len(lines)):
        if closer:
            flags[i] = True
            stripped = lines[i].strip()
            if stripped.startswith(closer) and set(stripped) == {closer[0]}:
                closer = ""
            continue
        opened = FENCE_OPEN.match(lines[i])
        if opened:
            flags[i] = True
            closer = opened.group(1)
    return flags


def paragraph_end(lines: list[str], start: int, fenced: list[bool]) -> int:
    """Index just past the paragraph that begins at `start`: the next blank or fence line."""
    end = start
    while end < len(lines) and lines[end].strip() and not fenced[end]:
        end += 1
    return end


def zones_of(lines: list[str]) -> tuple[list[Zone], bool]:
    """The live status zones of one topic file, and whether its frontmatter closed.

    Zones: the `description:` value; the lead paragraph (the first paragraph after the
    frontmatter, skipping at most one leading heading); and any paragraph within the first
    STATUS_SCAN_LINES body lines whose first line begins `Status`. Everything else is narrative.
    """
    body, closed = split_frontmatter(lines)
    if not closed:
        return [], False
    zones: list[Zone] = []
    for i in range(1, max(body - 1, 1)):
        if lines[i].startswith("description:"):
            text = unwrap_description(lines[i][len("description:") :])
            zones.append(Zone("description", i + 1, text))
    n = len(lines)
    fenced = fence_flags(lines, body)
    covered: set[int] = set()
    k = body
    while k < n and not lines[k].strip():
        k += 1
    if k < n and not fenced[k] and lines[k].lstrip().startswith("#"):
        k += 1
        while k < n and not lines[k].strip():
            k += 1
    if k < n and not fenced[k] and not lines[k].lstrip().startswith("#"):
        end = paragraph_end(lines, k, fenced)
        zones.append(Zone("lead", k + 1, "\n".join(lines[k:end])))
        covered.update(range(k, end))
    j = body
    while j < min(n, body + STATUS_SCAN_LINES):
        # A paragraph starts after a blank line, a heading or a fence delimiter.
        first_line = (
            j == body
            or not lines[j - 1].strip()
            or lines[j - 1].lstrip().startswith("#")
            or fenced[j - 1]
        )
        if (
            j not in covered
            and not fenced[j]
            and first_line
            and STATUS_PARAGRAPH.match(lines[j])
        ):
            end = paragraph_end(lines, j, fenced)
            zones.append(Zone("status", j + 1, "\n".join(lines[j:end])))
            covered.update(range(j, end))
            j = end
            continue
        j += 1
    return zones, True


def out_of(repo: Path, *args: str) -> str | None:
    """A git command's stdout, stripped; None when it failed."""
    proc = git(repo, *args)
    return proc.stdout.strip() if proc.returncode == 0 else None


def resolve_dev(arg: str | None) -> Path:
    """The primary working tree of the dev repository (a /feature worktree resolves to it)."""
    if arg == "":
        raise ErrorExit(
            "usage", "--dev is empty (did a command substitution that names it fail?)"
        )
    start = Path(arg) if arg else Path.cwd()
    common = out_of(start, "rev-parse", "--git-common-dir")
    if common is None:
        if arg:
            raise ErrorExit("usage", f"--dev {arg} is not a git repository")
        raise NotApplicable("dev-not-git")
    common_path = Path(common)
    if not common_path.is_absolute():
        common_path = start / common_path
    common_path = common_path.resolve()
    top = common_path.parent if common_path.name == ".git" else start.resolve()
    S.hdr["dev"] = str(top)
    return top


def read_status(dev: Path, *args: str, ok: tuple[int, ...] = (0, 1)) -> tuple[int, str]:
    """Run a git read whose answer is its exit status; any other status is a FAILED read.

    `rev-parse --verify -q` exits 1 for a ref that is cleanly absent and 128 when the repository
    cannot be read; folding both into "absent" would turn a broken repository into a skip.
    """
    proc = git(dev, *args)
    if proc.returncode not in ok:
        raise ErrorExit(
            "object-store-unreadable",
            f"git {args[0]} exited {proc.returncode}",
            S.classes,
        )
    return proc.returncode, proc.stdout


def is_adopted(dev: Path) -> bool:
    """True when the watermark exists or `dev` tracks .publication.toml; a failed read is an ERROR.

    A repository with neither (or with no `dev` branch at all) is cleanly NOT adopted.
    """
    if read_status(dev, "rev-parse", "--verify", "-q", WATERMARK_REF)[0] == 0:
        return True
    if read_status(dev, "rev-parse", "--verify", "-q", DEV_REF)[0] == 1:
        return False
    listing = read_status(
        dev, "ls-tree", "--name-only", DEV_REF, "--", PUBLICATION_TOML, ok=(0,)
    )[1]
    return bool(listing.strip())


def default_memory_dir(dev: Path) -> Path:
    mangled = re.sub(r"[^A-Za-z0-9]", "-", str(dev))
    return Path(os.path.expanduser("~/.claude/projects")) / mangled / "memory"


def short(sha: str | None) -> str:
    return sha[:7] if sha else "absent"


class Ctx:
    """The git facts one run judges against; built once, before any file is judged."""

    dev: Path
    dev_tip: str
    production: Path | None = None
    prod_head: str | None = None
    watermark: str | None = None
    origin_tip: str | None = None
    dev_set: set[str]
    prod_set: set[str] = set()
    wm_set: set[str] = set()
    origin_set: set[str] = set()
    trees: dict[str, list[str]] = {}
    range_text: str | None = None
    range_commits: set[str] | None = None


def commit_set(repo: Path, rev: str) -> set[str]:
    out = out_of(repo, "rev-list", rev)
    if out is None:
        raise ErrorExit("object-store-unreadable", f"rev-list {rev} failed", S.classes)
    return set(out.split())


def is_ancestor(repo: Path, older: str, newer: str) -> bool:
    """True or False for a clean answer; any other status is an unreadable store, never a "no".

    `merge-base --is-ancestor` exits 1 for "not an ancestor" and 128 when git could not read: a
    caller that took 128 for 1 would turn a real ancestor into a twin or an unrelated commit.
    """
    rc = git(repo, "merge-base", "--is-ancestor", older, newer).returncode
    if rc not in (0, 1):
        raise ErrorExit(
            "object-store-unreadable",
            f"merge-base --is-ancestor exited {rc}",
            S.classes,
        )
    return rc == 0


def roots(repo: Path, rev: str) -> set[str] | None:
    """The root commits of `rev`, or None when git could not say.

    An empty set would read as "no shared root", so a failed read must never become one: a valid
    commit always has at least one root.
    """
    found = set((out_of(repo, "rev-list", "--max-parents=0", rev) or "").split())
    return found or None


def resolve_production(arg: str | None, dev: Path) -> tuple[Path, str]:
    """The production clone and its HEAD; an unreadable one is a named NOT-MEASURABLE."""
    classes = ("promoted",)
    if arg:
        path = Path(arg)
        S.hdr["production"] = f"{path}@unresolved"
        if not path.is_dir():
            raise ErrorExit("production-absent", f"{path} is not a directory", classes)
    else:
        scripts = Path(os.path.expanduser("~/.claude/scripts"))
        if not scripts.exists():
            raise ErrorExit("production-absent", f"{scripts} does not exist", classes)
        path = Path(os.path.realpath(scripts)).parent
    S.hdr["production"] = f"{path}@unresolved"
    top = out_of(path, "rev-parse", "--show-toplevel")
    if top is None:
        raise ErrorExit("production-not-git", f"{path} is not a git repo", classes)
    top_path = Path(top).resolve()
    if top_path == dev.resolve():
        raise ErrorExit("production-is-dev", f"{top_path} is the dev repo", classes)
    head = out_of(top_path, "rev-parse", "HEAD")
    if head is None:
        raise ErrorExit("production-absent", f"{top_path} has no HEAD", classes)
    S.hdr["production"] = f"{top_path}@{short(head)}"
    return top_path, head


def check_production(ctx: Ctx, args: argparse.Namespace) -> None:
    ctx.production, ctx.prod_head = resolve_production(args.production, ctx.dev)
    classes = ("promoted",)
    known = (
        git(ctx.dev, "cat-file", "-e", f"{ctx.prod_head}^{{commit}}").returncode == 0
    )
    if known and is_ancestor(ctx.dev, ctx.prod_head, ctx.dev_tip):
        ctx.prod_set = commit_set(ctx.production, ctx.prod_head)
        return
    dev_roots = roots(ctx.dev, ctx.dev_tip)
    prod_roots = roots(ctx.production, ctx.prod_head)
    if dev_roots is None or prod_roots is None:
        raise ErrorExit(
            "object-store-unreadable",
            "could not read the root commits to compare lineage",
            classes,
        )
    disjoint = not (dev_roots & prod_roots)
    if disjoint and not args.production:
        raise NotApplicable("not-this-config")
    raise ErrorExit(
        "production-unrelated",
        f"production HEAD {short(ctx.prod_head)} is not an ancestor of the dev tip",
        classes,
    )


def check_watermark(ctx: Ctx) -> None:
    classes = ("published",)
    ctx.watermark = out_of(ctx.dev, "rev-parse", "--verify", "-q", WATERMARK_REF)
    if ctx.watermark is None:
        raise ErrorExit("watermark-absent", f"{WATERMARK_REF} does not exist", classes)
    ok = git(ctx.dev, "cat-file", "-e", f"{ctx.watermark}^{{commit}}").returncode == 0
    if not (ok and is_ancestor(ctx.dev, ctx.watermark, ctx.dev_tip)):
        raise ErrorExit(
            "watermark-unrelated",
            f"{WATERMARK_REF} is not an ancestor of the dev tip",
            classes,
        )
    ctx.wm_set = commit_set(ctx.dev, ctx.watermark)


def batch_check(ctx: Ctx, names: list[str]) -> list[tuple[str, str]]:
    """Classify objects with ONE `cat-file --batch-check`; a short answer is an error.

    Returns (oid-or-name, type) per input, where type is `missing` or `ambiguous` for a name
    git could not resolve. Fewer output lines than inputs means the instrument did not read.
    """
    proc = git(
        ctx.dev, "cat-file", "--batch-check", stdin="".join(f"{n}\n" for n in names)
    )
    lines = proc.stdout.splitlines()
    if proc.returncode != 0 or len(lines) != len(names):
        raise ErrorExit(
            "object-store-unreadable",
            f"cat-file answered {len(lines)} lines for {len(names)} names",
            S.classes,
        )
    answers = []
    for line in lines:
        parts = line.split()
        answers.append(
            (parts[0], parts[1]) if len(parts) >= 2 else (parts[0], "missing")
        )
    return answers


def self_check(ctx: Ctx) -> None:
    """The positive controls: the objects this run depends on must classify as commits."""
    controls = [ctx.dev_tip]
    if ctx.prod_head and "promoted" in S.classes:
        controls.append(ctx.prod_head)
    if ctx.watermark and "published" in S.classes:
        controls.append(ctx.watermark)
    for oid, kind in batch_check(ctx, controls):
        if kind != "commit":
            raise ErrorExit(
                "object-store-unreadable", f"{oid} is not a commit", S.classes
            )


def dev_trees(dev: Path, tip: str) -> dict[str, list[str]]:
    """Map each tree id to the dev commits that carry it (for the oracle equivalence)."""
    out = out_of(dev, "log", "--format=%H %T", tip)
    trees: dict[str, list[str]] = {}
    for line in (out or "").splitlines():
        parts = line.split()
        if len(parts) != 2:
            raise ErrorExit(
                "object-store-unreadable", f"log answered {line!r}", S.classes
            )
        trees.setdefault(parts[1], []).append(parts[0])
    if not trees:
        # The dev tip is itself a commit, so at least one tree must come back.
        raise ErrorExit("object-store-unreadable", "log listed no commits", S.classes)
    return trees


def build_context(args: argparse.Namespace, dev: Path) -> Ctx:
    ctx = Ctx()
    ctx.dev = dev
    tip = out_of(dev, "rev-parse", "--verify", "-q", DEV_REF)
    if tip is None:
        raise ErrorExit("dev-ref-absent", f"{DEV_REF} does not exist", S.classes)
    ctx.dev_tip = tip
    S.hdr["dev"] = f"{dev}@{short(tip)}"
    ctx.dev_set = commit_set(dev, tip)
    if "promoted" in S.classes:
        check_production(ctx, args)
    else:
        S.hdr["production"] = "absent"
    if "published" in S.classes:
        check_watermark(ctx)
    else:
        ctx.watermark = out_of(dev, "rev-parse", "--verify", "-q", WATERMARK_REF)
    S.hdr["watermark"] = short(ctx.watermark)
    ctx.trees = dev_trees(dev, tip)
    ctx.origin_tip = out_of(dev, "rev-parse", "--verify", "-q", ORIGIN_REF)
    S.hdr["origin-main"] = short(ctx.origin_tip)
    if ctx.origin_tip:
        ctx.origin_set = commit_set(dev, ctx.origin_tip)
    self_check(ctx)
    return ctx


@lru_cache(maxsize=8)
def quote_index(text: str) -> tuple[list[int], list[int], list[int]]:
    """Backtick positions and the merged quotation spans of `text`, computed once per text.

    Rescanning the text for every claim made a paragraph of N claims cost N times its length.
    """
    ticks: list[int] = []
    spans = [(m.start(), m.end()) for span in QUOTE_SPANS for m in span.finditer(text)]
    offset = 0
    pending = None
    for line in text.split("\n"):
        found = [offset + i for i, ch in enumerate(line) if ch == "`"]
        # Backticks pair within a line. A line's odd trailing backtick may pair with the FIRST
        # one of the next line (a code span wrapped across lines); anywhere further it would be
        # a stray, and pairing it would flip every claim after it.
        if pending is not None:
            if found:
                ticks.extend([pending, found[0]])
                found = found[1:]
            pending = None
        if len(found) % 2:
            pending = found.pop()
        ticks.extend(found)
        if line.count('"') % 2 == 0:
            spans.extend(
                (offset + m.start(), offset + m.end())
                for m in DOUBLE_QUOTED.finditer(line)
            )
        offset += len(line) + 1
    spans.sort()
    merged: list[tuple[int, int]] = []
    for start, end in spans:
        if merged and start < merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return ticks, [s for s, _ in merged], [e for _, e in merged]


def is_quoted(text: str, pos: int) -> bool:
    """True when `pos` sits inside backticks or a quotation: a mention, not a claim."""
    ticks, starts, ends = quote_index(text)
    if bisect_left(ticks, pos) % 2 == 1:
        return True
    i = bisect_left(starts, pos) - 1
    return i >= 0 and pos < ends[i]


def exclusion(text: str, m: re.Match) -> str | None:
    """Why a match is not a live claim: a count, a noun phrase, a mention, or an anchor."""
    lo = max(0, m.start() - COUNT_WINDOW)
    counted = COUNT_BEFORE.search(text[lo : m.start()])
    # A match at the window's first character cannot see what precedes it: not a whole token.
    if counted and (lo == 0 or counted.start() > 0):
        return "count"
    if text[m.start() : m.end() + 5].lower() == "unpushed tags":
        return "noun"
    if is_quoted(text, m.start()):
        return "quoted"
    near = text[max(0, m.start() - ANCHOR_REACH) : m.end() + ANCHOR_REACH]
    if ANCHOR.search(near):
        return "anchored"
    return None


def claims_in(text: str) -> list[Claim]:
    """Every claim of non-attainment in a zone's text, in order of appearance.

    Link targets and wikilinks are blanked first: a file name such as
    `...publish-built-not-pushed.md` is not a claim.
    """
    scan = QUOTE_PREFIX.sub(lambda m: " " * len(m.group(0)), text)
    scan = LINK_TARGET.sub(lambda m: " " * len(m.group(0)), scan)
    found = []
    for klass, pattern in CLAIM_PATTERNS.items():
        for m in pattern.finditer(scan):
            found.append(
                Claim(klass, m.start(), m.end(), exclusion(scan, m), CLAIM_NAMES[klass])
            )
    return sorted(found, key=lambda c: c.start)


def hex_tokens(text: str) -> list[str]:
    return list(dict.fromkeys(m.group(0) for m in HEX_TOKEN.finditer(text)))


def build_units(topics: list[tuple[Path, list[Zone]]]) -> list[Unit]:
    units = []
    for path, zones in topics:
        for zone in zones:
            claims = [
                c
                for c in claims_in(zone.text)
                if c.klass in S.classes and c.excluded in (None, "anchored")
            ]
            if claims:
                units.append(Unit("topic", path.name, zone, claims, zone.text))
    return units


def index_entries(text: str) -> list[Zone]:
    """The index's entries: a `- [` bullet plus its indented continuation lines."""
    entries: list[Zone] = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        if lines[i].startswith("- ["):
            j = i + 1
            while j < len(lines) and lines[j][:1] in (" ", "\t") and lines[j].strip():
                j += 1
            entries.append(Zone("index", i + 1, "\n".join(lines[i:j])))
            i = j
        else:
            i += 1
    return entries


def position(claim: Claim) -> int:
    return claim.start


def label_claims(text: str) -> list[Claim]:
    """A bold LANDED or PROMOTED label that nothing higher in the same entry supersedes."""
    found = []
    for m in LABEL.finditer(text):
        word = m.group(1)
        if HIGHER_LABEL[word].search(text):
            continue
        klass, name = LABEL_CLAIMS[word]
        found.append(Claim(klass, m.start(), m.end(), None, name))
    return found


def index_units(
    entries: list[Zone], topics: list[tuple[Path, list[Zone]]]
) -> list[Unit]:
    """Index entries as subjects of their own; the evidence is the linked topic's zones."""
    by_name = {path.name: zones for path, zones in topics}
    units = []
    for entry in entries:
        found = sorted(claims_in(entry.text) + label_claims(entry.text), key=position)
        claims = [
            c
            for c in found
            if c.klass in S.classes and c.excluded in (None, "anchored")
        ]
        if not claims:
            continue
        link = INDEX_LINK.search(entry.text)
        target = link.group(1) if link else ""
        # Only a direct child of the memory directory is the entry's topic: `../x/t.md`, an
        # absolute path, a subdirectory or a URL must not bind to a different top-level `t.md`.
        if not DIRECT_CHILD.fullmatch(target):
            target = ""
        target = target[2:] if target.startswith("./") else target
        source = "\n\n".join(z.text for z in by_name.get(target, []))
        units.append(Unit("index", INDEX_NAME, entry, claims, source))
    return units


def tag_tokens(text: str) -> list[str]:
    return list(dict.fromkeys(m.group(0) for m in TAG_TOKEN.finditer(text)))


def classify_tokens(ctx: Ctx, units: list[Unit]) -> dict[str, tuple[str, str]]:
    """One `cat-file --batch-check` over every distinct hex token and tag the units mention."""
    tokens = list(dict.fromkeys(t for u in units for t in hex_tokens(u.source)))
    tags = list(dict.fromkeys(t for u in units for t in tag_tokens(u.source)))
    if not tokens and not tags:
        return {}
    names = tokens + [f"refs/tags/{t}^{{commit}}" for t in tags]
    answers = batch_check(ctx, names)
    return {key: answers[i] for i, key in enumerate(tokens + tags)}


def attained_set(ctx: Ctx, klass: str) -> set[str]:
    return ctx.prod_set if klass == "promoted" else ctx.wm_set


def context_tokens(text: str) -> set[str]:
    """Hex tokens that are context in EVERY occurrence: a base, or the left side of `a..b`.

    Context neither attains nor vetoes: a base is attained by construction and says nothing
    about the work the claim is about.
    """
    verdict: dict[str, bool] = {}
    for m in HEX_TOKEN.finditer(text):
        before = text[max(0, m.start() - CLAUSE_REACH) : m.start()]
        clause = re.split(r"[,;]", before.replace("`", "").replace("(", " "))[-1]
        after = text[m.end() : m.end() + 4].replace("`", "")
        after_base = BASE_CLAUSE.search(clause) is not None
        is_context = after_base or after.startswith("..")
        verdict[m.group(0)] = verdict.get(m.group(0), True) and is_context
    return {tok for tok, is_context in verdict.items() if is_context}


def is_backticked(text: str, token: str) -> bool:
    return f"`{token}`" in text


def equivalents(ctx: Ctx, oid: str) -> list[str]:
    """The dev commits an unlanded commit is equivalent to; empty when there is none.

    Equivalent means its tree equals that of a dev commit D that is NOT an ancestor of it: a
    branch tip whose tree equals its own base, or one that merged dev and reverted, is not.
    """
    tree = out_of(ctx.dev, "rev-parse", f"{oid}^{{tree}}")
    return [
        d for d in ctx.trees.get(tree or "", []) if not is_ancestor(ctx.dev, d, oid)
    ]


def evaluate(
    ctx: Ctx, kinds: dict[str, tuple[str, str]], unit: Unit, klass: str
) -> Verdict:
    """STALE iff there is evidence and every item of it is attained for the claim's class.

    Landed commits are judged by ancestry. An unlanded commit is work still on a branch and
    vetoes, unless it is equivalent to a dev commit, is an ancestor of an equivalent commit the
    paragraph also cites (subsumed), or is a main-side commit (attains published, ignored for
    promoted). Context tokens abstain; trees and blobs are ignored; a backticked token that no
    object answers to vetoes.
    """
    text = unit.source
    attained = attained_set(ctx, klass)
    context = context_tokens(text)
    commits, missing, saw_context = [], False, False
    for tok in hex_tokens(text):
        oid, kind = kinds[tok]
        if kind == "commit" and tok in context:
            saw_context = True
        elif kind == "commit":
            commits.append(oid)
        elif kind in ("missing", "ambiguous") and is_backticked(text, tok):
            missing = True
    commits = list(dict.fromkeys(commits))
    items: list[bool] = []
    shown: list[str] = []
    anchors: list[str] = []
    unlanded = [o for o in commits if o not in ctx.dev_set]
    twins = {o: equivalents(ctx, o) for o in unlanded}
    for oid in commits:
        if oid in ctx.dev_set:
            items.append(oid in attained)
            anchors.append(oid)
        elif twins[oid]:
            items.append(all(d in attained for d in twins[oid]))
            anchors.extend(twins[oid])
        elif any(
            o != oid and twins[o] and is_ancestor(ctx.dev, oid, o) for o in unlanded
        ):
            continue
        elif oid in ctx.origin_set:
            if klass == "published":
                items.append(True)
            else:
                continue
        else:
            items.append(False)
        shown.append(oid)
    if ctx.origin_tip and klass == "published":
        for tag in tag_tokens(text):
            oid, kind = kinds[tag]
            if kind == "commit":
                items.append(oid in ctx.origin_set)
                shown.append(oid)
    if missing and not items:
        return Verdict("SKIP", "sha-unresolved", [], [])
    if missing:
        items.append(False)
    if not items:
        if saw_context and not tag_tokens(text):
            return Verdict("SKIP", "context-only", [], [])
        if klass == "promoted" and tag_tokens(text) and not commits:
            return Verdict("SKIP", "tag-cannot-decide-promotion", [], [])
        return Verdict("SKIP", "no-landed-sha", [], [])
    return Verdict("STALE" if all(items) else "LIVE", "", shown, anchors)


def snippet(text: str, claim: Claim) -> str:
    around = text[max(0, claim.start - 80) : claim.end + 60]
    return " ".join(around.split())


def ref_label(ctx: Ctx, klass: str) -> str:
    if klass == "promoted":
        return f"production@{short(ctx.prod_head)}"
    return f"watermark@{short(ctx.watermark)}"


def claim_line(unit: Unit, claim: Claim) -> int:
    return unit.zone.line + unit.zone.text.count("\n", 0, claim.start)


def fix_hint(unit: Unit, claim: Claim, klass: str, shas: str, ref: str) -> str:
    """A hint, never an instruction: the index only ever loses a label, never gains one."""
    if unit.subject == "index" and claim.name.startswith("label-"):
        text = unit.zone.text
        tail = LABEL_TAIL.match(text, claim.end)
        label = text[claim.start : tail.end() if tail else claim.end]
        closing = ' and its closing "**"' if "**" in text[claim.end :] else ""
        return (
            f'delete the label "{label}"{closing} (never replace it with another label)'
        )
    if unit.subject == "index":
        return "delete the claim from the line; the topic file owns the status"
    return (
        f"state {STAGE_NAMES[klass]} ({shas} attained {ref}); if the sentence is true "
        f'history, date it ("as of <date>"), never delete it; re-read clauses '
        f"attached to the negative"
    )


def in_range(ctx: Ctx, anchors: list[str]) -> bool:
    """True when no range was asked for, or one of the anchor commits is in it.

    A unit whose only evidence is main-side commits or tags has no anchors, so a ranged run
    counts it as outside the range: a promote never changes the published class.
    """
    return ctx.range_commits is None or any(a in ctx.range_commits for a in anchors)


def judge_unit(
    ctx: Ctx, kinds: dict[str, tuple[str, str]], args: argparse.Namespace, unit: Unit
) -> None:
    """Judge one unit: its live claims decide its row; anchored claims only add NOTE rows."""
    live_claims = [c for c in unit.claims if c.excluded is None]
    cache: dict[str, Verdict] = {}

    def class_verdict(klass: str) -> Verdict:
        if klass not in cache:
            cache[klass] = evaluate(ctx, kinds, unit, klass)
        return cache[klass]

    for klass in dict.fromkeys(c.klass for c in unit.claims if c.excluded):
        noted = class_verdict(klass)
        if noted.kind == "STALE" and in_range(ctx, noted.anchors):
            first = next(
                c for c in unit.claims if c.klass == klass and c.excluded == "anchored"
            )
            S.notes += 1
            row(
                f"NOTE {unit.name}:{claim_line(unit, first)} anchored-claim "
                f"attained={ref_label(ctx, klass)}"
            )
    if not live_claims:
        return
    verdicts = {}
    for klass in dict.fromkeys(c.klass for c in live_claims):
        verdicts[klass] = class_verdict(klass)
    stale = [k for k, v in verdicts.items() if v.kind == "STALE"]
    live = [k for k, v in verdicts.items() if v.kind == "LIVE"]
    if stale:
        reach = [a for k in stale for a in verdicts[k].anchors]
        if not in_range(ctx, reach):
            S.outside += 1
            return
        if "promoted" in stale:
            S.promoted_anchors.append(verdicts["promoted"].anchors)
        first = next(c for c in live_claims if c.klass in stale)
        head = verdicts[stale[0]]
        ref = ref_label(ctx, stale[0])
        shas = ",".join(short(o) for o in head.evidence)
        names = ",".join(dict.fromkeys(c.name for c in live_claims if c.klass in stale))
        S.stale += 1
        row(
            f"STALE {unit.name}:{claim_line(unit, first)} subject={unit.subject} "
            f"zone={unit.zone.kind} claim={names} evidence={len(head.evidence)} "
            f"attained={ref} sha={shas}"
        )
        row(f"  text: {snippet(unit.zone.text, first)}")
        top = max(stale, key=CLASSES.index)
        top_shas = ",".join(short(o) for o in verdicts[top].evidence)
        row(f"  fix: {fix_hint(unit, first, top, top_shas, ref_label(ctx, top))}")
    elif live:
        S.live += 1
        live_names = ",".join(
            dict.fromkeys(c.name for c in live_claims if c.klass in live)
        )
        if args.all:
            row(
                f"LIVE {unit.name}:{unit.zone.line} subject={unit.subject} "
                f"zone={unit.zone.kind} claim={live_names}"
            )
    else:
        S.skip += 1
        reason = next(iter(verdicts.values())).reason
        row(f"SKIP {unit.name}:{unit.zone.line} subject={unit.subject} reason={reason}")


def judge(ctx: Ctx, args: argparse.Namespace, units: list[Unit]) -> None:
    """Judge every unit and queue its rows; counters feed the verdict line."""
    kinds = classify_tokens(ctx, units)
    for unit in units:
        check_deadline()
        judge_unit(ctx, kinds, args, unit)


def parse_range(ctx: Ctx, text: str) -> None:
    """Validate `--range A..B`: both ends commits in the dev store, A an ancestor of or equal to B.

    Equal ends are a legal no-op promote (git rewrites ORIG_HEAD to HEAD when nothing moved), so
    they yield an empty range, not an error.
    """
    parts = text.split("..")
    if len(parts) != 2 or not parts[0] or not parts[1] or "..." in text:
        raise ErrorExit("range-invalid", f"{text!r} is not A..B with both ends named")
    if not all(COMMIT_ID.fullmatch(p) for p in parts):
        raise ErrorExit(
            "range-invalid", f"{text!r}: both ends must be commit ids, not names"
        )
    ends = []
    for name in parts:
        oid = out_of(ctx.dev, "rev-parse", "--verify", "-q", f"{name}^{{commit}}")
        if oid is None:
            raise ErrorExit(
                "range-invalid", f"{name!r} is not a commit in the dev repo"
            )
        ends.append(oid)
    if not is_ancestor(ctx.dev, ends[0], ends[1]):
        raise ErrorExit("range-invalid", f"{parts[0]} is not an ancestor of {parts[1]}")
    ctx.range_text = f"{short(ends[0])}..{short(ends[1])}"
    ctx.range_commits = commit_set(ctx.dev, f"{ends[0]}..{ends[1]}")
    S.hdr["range"] = f"{ctx.range_text} range-commits={len(ctx.range_commits)}"


def parse_since(text: str) -> float:
    for fmt in SINCE_FORMATS:
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc).timestamp()
        except ValueError:
            continue
    raise ErrorExit(
        "usage", f"--since {text!r} is not an ISO-8601 UTC time ending in Z"
    )


def production_reflog(ctx: Ctx) -> list[tuple[str, int]]:
    """Production's HEAD reflog, oldest first, as (commit, unix time); empty if unreadable."""
    assert ctx.production is not None
    out = out_of(
        ctx.production, "reflog", "show", "--date=unix", "--format=%H %gd", "HEAD"
    )
    entries = []
    for line in (out or "").splitlines():
        m = REFLOG_LINE.match(line.strip())
        if m:
            entries.append((m.group(1), int(m.group(2))))
    entries.reverse()
    return entries


def promote_time(
    ctx: Ctx, entries: list[tuple[str, int]], anchors: list[str]
) -> int | None:
    """When production first held every anchor: production only fast-forwards, so bisect."""
    assert ctx.production is not None

    def holds(i: int) -> bool:
        return all(is_ancestor(ctx.production, a, entries[i][0]) for a in anchors)

    if not anchors or not entries or not holds(len(entries) - 1):
        return None
    lo, hi = 0, len(entries) - 1
    while lo < hi:
        mid = (lo + hi) // 2
        if holds(mid):
            hi = mid
        else:
            lo = mid + 1
    return entries[lo][1]


def stale_before_since(ctx: Ctx, since: float) -> str:
    """How many listed promoted-class stale units were promoted before `since`; a lower bound.

    The published class has no time source (the watermark ref has no reflog), so it is not
    counted, and an unreadable reflog is `unknown`, never 0.
    """
    if "promoted" not in S.classes or ctx.production is None:
        return "unknown"
    try:
        entries = production_reflog(ctx)
        if not entries:
            return "unknown"
        times = [promote_time(ctx, entries, a) for a in S.promoted_anchors]
    except ErrorExit:
        return "unknown"
    return str(sum(1 for t in times if t is not None and t < since))


def header(ctx: Ctx, mem: Path, tail: str = "") -> str:
    prod = f"{ctx.production}@{short(ctx.prod_head)}" if ctx.prod_head else "absent"
    if ctx.range_commits is None:
        span = "range=none"
    else:
        span = f"range={ctx.range_text} range-commits={len(ctx.range_commits)}"
    return (
        f"memory-status-check: memory={mem} dev={ctx.dev}@{short(ctx.dev_tip)} "
        f"production={prod} watermark={short(ctx.watermark)} "
        f"origin-main={short(ctx.origin_tip)} classes={','.join(S.classes)} "
        f"{span} files={S.files} zones={S.zones}{tail}"
    )


def install_signal_handlers() -> None:
    def handler(signum: int, _frame) -> None:
        rc = 128 + signum
        # os.write, not print: the signal can land inside a print, and a re-entrant write raises.
        # Every outcome starts with the header, so a run stopped before it was printed gets the
        # header of whatever had resolved by then.
        head = "" if S.header_done else known_header() + "\n"
        kill_child()
        try:
            os.write(1, (head + result_line("INCOMPLETE", rc) + "\n").encode())
        except OSError:
            pass
        os._exit(rc)

    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(sig, handler)


def run(argv: list[str]) -> int:
    args = parse(argv)
    since = parse_since(args.since) if args.since else None
    S.deadline = time.monotonic() + deadline_secs()
    S.classes = (args.only,) if args.only else CLASSES
    S.hdr["classes"] = ",".join(S.classes)
    dev = resolve_dev(args.dev)
    if not is_adopted(dev):
        raise NotApplicable("not-adopted")
    mem = Path(args.memory_dir) if args.memory_dir else default_memory_dir(dev)
    S.hdr["memory"] = str(mem)
    if not mem.is_dir():
        raise ErrorExit("memory-dir-absent", f"{mem} is not a directory")
    files = topic_files(mem)
    S.files = len(files)
    if not files:
        raise ErrorExit("no-files", f"no *.md files in {mem}")
    topics: list[tuple[Path, list[Zone]]] = []
    entries: list[Zone] = []
    for path in files:
        text = path.read_text(encoding="utf-8", errors="replace")
        if path.name == INDEX_NAME:
            entries = index_entries(text)
            S.zones += len(entries)
            continue
        zones, closed = zones_of(text.splitlines())
        if not closed:
            S.skip += 1
            row(f"SKIP {path.name}:1 subject=topic reason=frontmatter-unterminated")
        S.zones += len(zones)
        S.topic_zones += len(zones)
        topics.append((path, zones))
    if S.zones == 0 or (topics and S.topic_zones == 0):
        raise ErrorExit("zones-empty", f"no status zone parsed from {S.files} files")
    ctx = build_context(args, dev)
    if args.range:
        parse_range(ctx, args.range)
    judge(ctx, args, build_units(topics) + index_units(entries, topics))
    tail = ""
    if since is not None:
        tail = f" stale-before-since={stale_before_since(ctx, since)} scope=promoted"
    emit_header_once(header(ctx, mem, tail))
    return finish("FAIL", 1) if S.stale else finish("PASS", 0)


def guarded(argv: list[str] | None) -> int:
    """Run, turning every ending into a verdict line and an exit status."""
    try:
        return run(sys.argv[1:] if argv is None else argv)
    except StdoutFailed:
        # Listed before `Exception` below: a verdict cannot be printed when stdout is gone, and a
        # second attempt to print one would raise again.
        raise
    except ErrorExit as err:
        emit_header_once(known_header())
        flush_rows()
        emit(error_line(err))
        return finish("ERROR", 2)
    except NotApplicable as na:
        emit_header_once(known_header())
        flush_rows()
        emit(f"NOT-APPLICABLE reason={na.reason}")
        return finish("NOT-APPLICABLE", 0)
    except Exception:
        traceback.print_exc(file=sys.stderr)
        emit_header_once(known_header())
        return finish("INCOMPLETE", 3)


def main(argv: list[str] | None = None) -> int:
    use_lossless_stdout()
    install_signal_handlers()
    try:
        return guarded(argv)
    except StdoutFailed:
        # Nobody can read stdout. Point it at /dev/null so the interpreter's own flush at
        # exit cannot fail too: that second failure exits 120, which a caller reads as an allow.
        try:
            os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        except (OSError, ValueError):
            pass
        return 3


if __name__ == "__main__":
    sys.exit(main())

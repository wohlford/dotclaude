#!/usr/bin/env python3
# Script: gate-sweep-eval.py
# Purpose: Decide whether the parallel gate's acceptance bound holds from a sweep's gate.tsv and its flake-sweep line
# Usage: gate-sweep-eval.py --tsv <gate.tsv> --sweep <file> --threshold <cores> --k <samples> [--serial-lo S --serial-hi S --margin F]
"""Judge the acceptance sweep of the opt-in parallel gate from artifacts a wrapper outside the repo owns.

Inputs: `gate.tsv` (one tab-separated record per run, eleven fields: profile, rc, result, wall, notes, digest_before,
digest_after, fmax, nover, maxrun, launchwait) and the flake-sweep's own terminal line in `--sweep`. Nothing else is
read; the sweep's run-NNN.log files are write-only side copies and are never consulted.

It fails closed. Exactly 10 `parallel` and 2 `serial` records are required, each with a numeric wall, an anchored
`RESULT:` line from the allowlist, the three sampler fields, both digests and a NOTE state matching its profile. Any
missing value, wrong count, changed `plans/` digest, loaded bracket, bracket outside the serial band widened by
`--margin` of each endpoint, loaded RED parallel run, sampler record inconsistent with `--threshold`, or unreadable
sweep line is INDETERMINATE (rc 3), never PASS and never a red. A run is LOADED when `maxrun` is at least `--k`
or its launch wait expired.

Further refusals, each INDETERMINATE: a serial bracket whose own result is not `RESULT: PASS rc=0`; a parallel run whose wall is
below the plausibility floor (60 s: a parallel gate run cannot finish in under a minute, its pytest phase alone measured
329 s and the static sweep about 30 s); a sweep line whose rc disagrees with its status word (PASS 0, FAIL 1, ERROR 2,
INCOMPLETE 3, INDETERMINATE 4), a sweep file with more than one anchored `RESULT:` line, or a record whose `result` field does
not hold exactly one. Unreadable input (an absent file, or bytes that are not UTF-8) and a numeric argument that is not finite
or not in its meaningful range are rc 2 with an ERROR line.

The bound, exactly as ruled. PASS (rc 0) only when BOTH hold: (1) the sweep line is `RESULT: PASS ... varying=0` over
10/10 runs AND all 10 parallel runs are `RESULT: PARALLEL-PASS rc=0`; (2) the median wall of the 10 parallel runs
(a loaded green run keeps its wall in it) is at most 60% of the mean of the two serial brackets AND at most 30 minutes.
Otherwise FAIL (rc 1). The median over the unloaded runs is printed, labelled, and is never the judged one.

The last line is the only verdict: `RESULT: <PASS|FAIL|INDETERMINATE|ERROR> rc=<n> runs=<p>/<s> green=<g> loaded=<l>
ratio=<r> bound1=<held|missed|na> bound2=<held|missed|na>`. Exit codes: 0 PASS, 1 FAIL, 2 usage or unreadable input,
3 INDETERMINATE.
"""

from __future__ import annotations

import argparse
import math
import re
import statistics
import sys
from pathlib import Path
from typing import NamedTuple

EXPECT_PARALLEL = 10
EXPECT_SERIAL = 2
BOUND_RATIO = 0.60
BOUND_ABS_SECONDS = 30 * 60
# A parallel gate run cannot finish in under a minute (its pytest phase alone measured 329 s, the static sweep about 30 s).
MIN_PARALLEL_WALL = 60
DEFAULT_SERIAL_LO = 47 * 60 + 40
DEFAULT_SERIAL_HI = 52 * 60 + 46
DEFAULT_MARGIN = 0.10

COLUMNS = (
    "profile",
    "rc",
    "result",
    "wall",
    "notes",
    "digest_before",
    "digest_after",
    "fmax",
    "nover",
    "maxrun",
    "launchwait",
)
NOTES_FOR = {"parallel": "both", "serial": "none"}
RESULT_RE = re.compile(
    r"^RESULT: (PASS|PARALLEL-PASS|FAIL|ERROR|INCOMPLETE) rc=([0-9]+)(?: .*)?$"
)
SWEEP_RE = re.compile(
    r"^RESULT: (PASS|FAIL|ERROR|INCOMPLETE|INDETERMINATE) rc=([0-9]+) runs=([0-9]+)/([0-9]+) "
    r"rows=([0-9]+) varying=([0-9]+) subject=(\S+)$"
)


# flake-sweep.sh's own contract: the rc that goes with each verdict word it can print.
SWEEP_RC = {"PASS": 0, "FAIL": 1, "ERROR": 2, "INCOMPLETE": 3, "INDETERMINATE": 4}


class Rec(NamedTuple):
    number: int
    profile: str
    rc: int
    word: str
    result_rc: int
    wall: float
    notes: str
    same_digest: bool
    maxrun: int
    expired: bool


def parse_number(text: str) -> float | None:
    try:
        value = float(text)
    except ValueError:
        return None
    return value if math.isfinite(value) and value >= 0 else None


def parse_count(text: str) -> int | None:
    return int(text) if re.fullmatch(r"[0-9]+", text) else None


def parse_record(number: int, line: str, threshold: float) -> tuple[Rec | None, str]:
    fields = line.split("\t")
    if len(fields) != len(COLUMNS):
        return None, "record %d: %d fields, want %d" % (
            number,
            len(fields),
            len(COLUMNS),
        )
    f = dict(zip(COLUMNS, fields, strict=True))
    if f["profile"] not in NOTES_FOR:
        return None, "record %d: profile %r is not parallel or serial" % (
            number,
            f["profile"],
        )
    rc = parse_count(f["rc"])
    wall = parse_number(f["wall"])
    fmax = parse_number(f["fmax"])
    nover = parse_count(f["nover"])
    maxrun = parse_count(f["maxrun"])
    if rc is None or wall is None or fmax is None or nover is None or maxrun is None:
        return None, "record %d: a numeric field is missing or malformed" % number
    match = RESULT_RE.match(f["result"])
    if match is None or f["result"].count("RESULT:") != 1:
        return None, "record %d: no single anchored RESULT line" % number
    if f["notes"] not in ("both", "none", "other"):
        return None, "record %d: notes %r is not both, none or other" % (
            number,
            f["notes"],
        )
    if not f["digest_before"] or not f["digest_after"]:
        return None, "record %d: a plans digest is missing" % number
    if f["launchwait"] not in ("ok", "expired"):
        return None, "record %d: launchwait %r is not ok or expired" % (
            number,
            f["launchwait"],
        )
    if int(match.group(2)) != rc:
        return (
            None,
            "record %d: the RESULT line's rc differs from the run's rc" % number,
        )
    over = nover > 0
    if (fmax > threshold) != over or (maxrun > 0) != over or maxrun > nover:
        return (
            None,
            "record %d: the sampler fields contradict each other or the threshold"
            % number,
        )
    rec = Rec(
        number,
        f["profile"],
        rc,
        match.group(1),
        int(match.group(2)),
        wall,
        f["notes"],
        f["digest_before"] == f["digest_after"],
        maxrun,
        f["launchwait"] == "expired",
    )
    return rec, ""


def parse_tsv(text: str, threshold: float) -> tuple[list[Rec], list[str]]:
    records: list[Rec] = []
    problems: list[str] = []
    number = 0
    for line in text.splitlines():
        if not line.strip() or line.startswith("profile\t"):
            continue
        number += 1
        rec, problem = parse_record(number, line, threshold)
        if rec is None:
            problems.append(problem)
        else:
            records.append(rec)
    return records, problems


def last_sweep_line(text: str) -> re.Match[str] | None:
    found = None
    for line in text.splitlines():
        match = SWEEP_RE.match(line)
        if match:
            found = match
    return found


def judge(records, problems, sweep_text, k, lo, hi):
    reasons = list(problems)
    out: list[str] = []
    par = [r for r in records if r.profile == "parallel"]
    ser = [r for r in records if r.profile == "serial"]
    if len(par) != EXPECT_PARALLEL or len(ser) != EXPECT_SERIAL:
        reasons.append(
            "want %d parallel and %d serial records, got %d and %d"
            % (EXPECT_PARALLEL, EXPECT_SERIAL, len(par), len(ser))
        )

    def loaded(r: Rec) -> bool:
        return r.maxrun >= k or r.expired

    def green(r: Rec) -> bool:
        return r.word == "PARALLEL-PASS" and r.rc == 0 and r.result_rc == 0

    for r in records:
        if r.notes != NOTES_FOR[r.profile]:
            reasons.append(
                "record %d: its NOTE lines do not match the %s profile"
                % (r.number, r.profile)
            )
        if not r.same_digest:
            reasons.append(
                "record %d: the plans digest changed during the run" % r.number
            )
    for r in ser:
        if r.word != "PASS" or r.rc != 0 or r.result_rc != 0:
            reasons.append(
                "record %d: a serial bracket's own result is not RESULT: PASS rc=0 (%s rc=%d)"
                % (r.number, r.word, r.result_rc)
            )
        if loaded(r):
            reasons.append("record %d: a serial bracket ran loaded" % r.number)
        if not lo <= r.wall <= hi:
            reasons.append(
                "record %d: bracket wall %.0fs is outside the band %.0fs-%.0fs"
                % (r.number, r.wall, lo, hi)
            )
    for r in par:
        if r.wall < MIN_PARALLEL_WALL:
            reasons.append(
                "record %d: wall %.0fs is below the plausibility floor of %ds; a parallel gate run cannot finish that fast"
                % (r.number, r.wall, MIN_PARALLEL_WALL)
            )
        if not green(r) and loaded(r):
            reasons.append(
                "record %d: a loaded parallel run was red; unattributable, it names no row"
                % r.number
            )
    sweep = last_sweep_line(sweep_text)
    sweep_ok = False
    anchored = [x for x in sweep_text.splitlines() if x.startswith("RESULT:")]
    if len(anchored) > 1:
        reasons.append(
            "the sweep file holds %d anchored RESULT lines, not one" % len(anchored)
        )
    if sweep is None:
        reasons.append("no anchored flake-sweep RESULT line")
    else:
        status, rc_text, runs, total, rows, varying, subject = sweep.groups()
        if int(rc_text) != SWEEP_RC[status]:
            reasons.append(
                "the sweep line's rc=%s does not agree with its status %s (want rc=%d)"
                % (rc_text, status, SWEEP_RC[status])
            )
        elif (
            status in ("ERROR", "INCOMPLETE", "INDETERMINATE")
            or int(runs) != EXPECT_PARALLEL
            or int(total) != EXPECT_PARALLEL
        ):
            reasons.append(
                "the sweep did not complete %d/%d runs (status %s, runs=%s/%s)"
                % (EXPECT_PARALLEL, EXPECT_PARALLEL, status, runs, total)
            )
        elif subject != "stable":
            reasons.append("the sweep's subject was not stable (%s)" % subject)
        elif status == "PASS" and int(varying) == 0 and int(rows) > 0:
            sweep_ok = True
        elif status == "PASS":
            reasons.append(
                "the sweep passed with no rows or a varying row, which is not a clean reading"
            )
    for r in par + ser:
        out.append(
            "run %d: profile=%s result=%s wall=%.0fs loaded=%s"
            % (r.number, r.profile, r.word, r.wall, "yes" if loaded(r) else "no")
        )
    greens = sum(1 for r in par if green(r))
    nloaded = sum(1 for r in records if loaded(r))
    runs_field = "%d/%d" % (len(par), len(ser))
    if reasons:
        out.extend("indeterminate: " + reason for reason in reasons)
        tail = "runs=%s green=%d loaded=%d ratio=na bound1=na bound2=na" % (
            runs_field,
            greens,
            nloaded,
        )
        return "INDETERMINATE", 3, out, tail
    walls = [r.wall for r in par]
    median = statistics.median(walls)
    unloaded = [r.wall for r in par if not loaded(r)]
    mean_serial = statistics.mean(r.wall for r in ser)
    ratio = median / mean_serial
    bound1 = sweep_ok and greens == EXPECT_PARALLEL
    # BOUND_RATIO is exactly 3/5: compared by cross-multiplication so the boundary value itself is not at the mercy of float rounding.
    bound2 = median * 5 <= mean_serial * 3 and median <= BOUND_ABS_SECONDS
    out.append(
        "bound 1: %d of %d parallel runs are PARALLEL-PASS rc=0; sweep varying=0: %s"
        % (greens, len(par), sweep_ok)
    )
    out.append(
        "bound 2: median=%.0fs serial_mean=%.0fs ratio=%.3f absolute=%.1fmin"
        % (median, mean_serial, ratio, median / 60)
    )
    if unloaded:
        out.append(
            "median_unloaded=%.0f (labelled; not the judged median)"
            % statistics.median(unloaded)
        )
    else:
        out.append("median_unloaded=none (labelled; not the judged median)")
    tail = "runs=%s green=%d loaded=%d ratio=%.3f bound1=%s bound2=%s" % (
        runs_field,
        greens,
        nloaded,
        ratio,
        "held" if bound1 else "missed",
        "held" if bound2 else "missed",
    )
    if bound1 and bound2:
        return "PASS", 0, out, tail
    return "FAIL", 1, out, tail


def bad_argument(args: argparse.Namespace) -> str:
    """The first numeric argument that is not finite or not in its meaningful range, else an empty string."""
    for name, value in (
        ("--threshold", args.threshold),
        ("--margin", args.margin),
        ("--serial-lo", args.serial_lo),
        ("--serial-hi", args.serial_hi),
    ):
        if not math.isfinite(value):
            return "%s must be finite (got %r)" % (name, value)
    if args.threshold <= 0:
        return "--threshold must be above 0 cores (got %r)" % args.threshold
    if args.k < 1:
        return "--k must be a positive integer (got %r)" % args.k
    if not 0 <= args.margin < 1:
        return "--margin must be at least 0 and below 1 (got %r)" % args.margin
    if args.serial_lo <= 0:
        return "--serial-lo must be above 0 seconds (got %r)" % args.serial_lo
    if args.serial_lo > args.serial_hi:
        return "--serial-lo (%r) must not exceed --serial-hi (%r)" % (
            args.serial_lo,
            args.serial_hi,
        )
    return ""


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="gate-sweep-eval.py")
    parser.add_argument("--tsv", required=True)
    parser.add_argument("--sweep", required=True)
    parser.add_argument("--threshold", type=float, required=True)
    parser.add_argument("--k", type=int, required=True)
    parser.add_argument("--serial-lo", type=float, default=DEFAULT_SERIAL_LO)
    parser.add_argument("--serial-hi", type=float, default=DEFAULT_SERIAL_HI)
    parser.add_argument("--margin", type=float, default=DEFAULT_MARGIN)
    try:
        args = parser.parse_args(argv)
    except SystemExit:
        print(
            "RESULT: ERROR rc=2 runs=0/0 green=0 loaded=0 ratio=na bound1=na bound2=na"
        )
        return 2
    problem = bad_argument(args)
    if problem:
        print("argument error: %s" % problem)
        print(
            "RESULT: ERROR rc=2 runs=0/0 green=0 loaded=0 ratio=na bound1=na bound2=na"
        )
        return 2
    try:
        tsv_text = Path(args.tsv).read_text()
        sweep_text = Path(args.sweep).read_text()
    except (OSError, ValueError) as exc:
        print("unreadable input: %s" % exc)
        print(
            "RESULT: ERROR rc=2 runs=0/0 green=0 loaded=0 ratio=na bound1=na bound2=na"
        )
        return 2
    records, problems = parse_tsv(tsv_text, args.threshold)
    lo = args.serial_lo * (1 - args.margin)
    hi = args.serial_hi * (1 + args.margin)
    status, rc, lines, tail = judge(records, problems, sweep_text, args.k, lo, hi)
    for line in lines:
        print(line)
    print("RESULT: %s rc=%d %s" % (status, rc, tail))
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

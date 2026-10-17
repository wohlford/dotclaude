#!/usr/bin/env python3
"""Mutation campaign for the plan-corpus floor check in scripts/tests/test_plan_rehearse.py.

Deliberately NOT named `test_*`: it mutates a tracked file in place, so pytest must not collect
it. Run it on demand and never while editing the subject. Set KILL_LOG to a path OUTSIDE the repo
to record each run's failing test ids, so kill sets can be compared: a mutant that kills every
selected row killed collection, not a check.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "scripts" / "lib"))
import mutate  # noqa: E402

SUBJECT = REPO / "scripts" / "tests" / "test_plan_rehearse.py"

WRAPPER = r"""
import os, subprocess, sys
r = subprocess.run(
    [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--tb=no", "-rf",
     "-k", "corpus_floor or declared_floor", sys.argv[1]],
    capture_output=True, text=True,
)
sys.stdout.write(r.stdout)
sys.stderr.write(r.stderr)
log = os.environ.get("KILL_LOG")
if log:
    with open(log, "a", encoding="utf-8") as fh:
        fh.write("--- run\n")
        for line in r.stdout.splitlines():
            if line.startswith("FAILED "):
                fh.write(line + "\n")
sys.exit(r.returncode)
"""
SUITE = [sys.executable, "-c", WRAPPER, str(SUBJECT)]

M = mutate.Mutation
COMPARE = "        if found != (total, unpaired):"
CHECKS_FOUND = "        found = (len(checks), sum(c.command is None for c in checks))"
MUTATIONS = [
    M("per-file-comparison-dropped", COMPARE, "        if False:"),
    M("per-file-comparison-flipped", COMPARE, "        if found == (total, unpaired):"),
    M("check-floor-dropped", "    if checks_present < min_checks:", "    if False:"),
    M(
        "check-floor-flipped",
        "    if checks_present < min_checks:",
        "    if checks_present > min_checks:",
    ),
    M(
        "check-floor-counts-files-not-checks",
        "        checks_present += total",
        "        checks_present += 1",
    ),
    M(
        "anchor-presence-check-dropped",
        "        if not (corpus_dir / name).is_file():",
        "        if False:",
    ),
    M(
        "exempts-every-known-plan-that-has-unpaired-rows",
        "        if not path.is_file():",
        "        if not path.is_file() or unpaired:",
    ),
    M("compares-total-only", CHECKS_FOUND, "        found = (len(checks), unpaired)"),
    M(
        "compares-unpaired-only",
        CHECKS_FOUND,
        "        found = (total, sum(c.command is None for c in checks))",
    ),
    M("regeneration-hint-dropped", "    if mismatched:", "    if False:"),
    M("regeneration-hint-always-added", "    if mismatched:", "    if True:"),
    M("skip-when-no-known-plan-dropped", "    if present == 0:", "    if False:"),
    M("skips-a-partial-corpus-too", "    if present == 0:", "    if present < 2:"),
    M(
        "asserting-wrapper-ignores-problems",
        '    assert not problems, "; ".join(problems)',
        "    assert True",
    ),
    M(
        "check-floor-fraction-zero",
        "CHECK_FLOOR_FRACTION = 0.5",
        "CHECK_FLOOR_FRACTION = 0.0",
    ),
    M(
        "check-floor-rounds-down",
        "math.ceil(CHECK_FLOOR_FRACTION",
        "math.floor(CHECK_FLOOR_FRACTION",
    ),
    M(
        "declared-anchor-count-drifts-from-the-manifest",
        '    "2026-09-09-env-boundary-relocation.md": 2,',
        '    "2026-09-09-env-boundary-relocation.md": 1,',
    ),
]


def main() -> int:
    report = mutate.run(SUBJECT, SUITE, MUTATIONS, cwd=str(REPO))
    print(report.text)
    return report.rc


if __name__ == "__main__":
    sys.exit(main())

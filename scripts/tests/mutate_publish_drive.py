#!/usr/bin/env python3
"""Mutation-test scripts/publish-drive.py.

Run on demand: `python3 scripts/tests/mutate_publish_drive.py`. Deliberately NOT named `test_*`,
so pytest never collects it — it mutates a tracked file in place (restored in a `finally`).

**Why a driver of all things earns a campaign.** This tool exists because the same throwaway loop
was hand-written three times, and the argument for building it was that each re-derivation drops a
different safety property. That argument is only worth anything if the properties are actually
pinned — a suite that passes over a driver which no longer halts, or which accepts a PASS naming a
different brick, would leave the repo believing a checkpoint it does not have. Every row below is
one of those properties, removed.

The stakes differ from an ordinary campaign: this driver is what applies bricks to the published
branch, and published `main` is append-only. A property that silently stops holding here produces
commits that cannot be withdrawn, only superseded.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "scripts" / "lib"))
import mutate  # noqa: E402

SUBJECT = REPO / "scripts" / "publish-drive.py"
SUITE = [
    sys.executable,
    "-m",
    "pytest",
    str(REPO / "scripts" / "tests" / "test_publish_drive.py"),
    "-q",
]

MUTATIONS = [
    mutate.Mutation(
        "THE load-bearing property — the driver no longer HALTS, so every brick after an "
        "unproven one is applied on top of it, onto an append-only published branch",
        "                break",
        "                pass",
    ),
    mutate.Mutation(
        "a verdict naming a DIFFERENT brick counts as this brick's pass",
        "    if last != want:",
        "    if False:",
    ),
    mutate.Mutation(
        "an engine that died before emitting any verdict reads as proven — the killed-run "
        "shape, where a prefix of good-looking lines is all you get",
        "    if not last:",
        "    if False:",
    ),
    mutate.Mutation(
        "a PASS line contradicted by a non-zero exit status is believed anyway",
        "    if proc.returncode != 0:",
        "    if False:",
    ),
    mutate.Mutation(
        "a hang is reported as a FAILURE verdict rather than INDETERMINATE, stating an "
        "outcome nobody measured",
        '        return "INDETERMINATE", f"exceeded {timeout}s without a verdict"',
        '        return "FAIL", f"exceeded {timeout}s without a verdict"',
    ),
    mutate.Mutation(
        "the artifact may live INSIDE the repo, so the driver's own log fails the next "
        "brick's clean-tree precondition — breaking the run it is driving, at brick 2",
        "    if scope_r == art_r or scope_r in art_r.parents:",
        "    if False:",
    ),
    mutate.Mutation(
        "a dirty tree no longer refuses, so the first brick fails on a precondition the "
        "driver could have checked for free",
        "    if dirty.stdout.strip():",
        "    if False:",
    ),
    mutate.Mutation(
        "a plan naming NO bricks reports success — zero is the loudest false pass there is",
        "    if not bricks:",
        "    if False:",
    ),
    mutate.Mutation(
        "a malformed brick line is silently SKIPPED, so a plan that did not fully parse "
        "reads exactly like a shorter plan and publishes less than the operator reviewed",
        "        if len(args) < 3:\n            raise DriveError(",
        "        if len(args) < 3:\n            continue\n        if len(args) < 0:\n            raise DriveError(",
    ),
]


def main() -> int:
    baseline = subprocess.run(SUITE, capture_output=True, text=True)
    print(
        f"pre-flight suite: rc={baseline.returncode}  {baseline.stdout.strip()[-60:]}"
    )

    report = mutate.run(SUBJECT, SUITE, MUTATIONS)
    print(report.text)
    return report.rc


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Mutation campaign for scripts/git-timing-guard.sh, driven by scripts/lib/mutate.py.

Deliberately NOT named `test_*`: it mutates a tracked file in place, so pytest must not collect it
and `check_tests` must not discover it. Run it on demand, and never while editing the subject — the
restore writes back a pre-run snapshot and would silently revert a concurrent edit.

Why this campaign earns its keep more than most: the subject is a LIVE fail-closed gate whose suite
had never run anywhere before this commit. A suite with no track record cannot be trusted on a green
run alone, because a green suite and an absent suite look identical. These mutants are the evidence
that the rows reach the code they name.

Each mutation targets a DIFFERENT decision, so no single over-broad row can account for all four:

* the window comparison — the gate's central decision;
* the override branch — its documented escape hatch;
* the origin scope check — what keeps it off unrelated repos;
* the push detection — what decides a command is publishing at all.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "scripts" / "lib"))
import mutate  # noqa: E402

SUBJECT = REPO / "scripts" / "git-timing-guard.sh"
SUITE = ["bash", str(REPO / "scripts" / "tests" / "test_git_timing_guard.sh")]

MUTATIONS = [
    # Inside the window becomes outside and vice versa. If nothing notices, the suite is not
    # testing the gate's central decision at all.
    mutate.Mutation(
        "window-inverted",
        '[ "$now" -ge "$smin" ] && [ "$now" -lt "$emin" ]',
        '[ "$now" -lt "$smin" ] && [ "$now" -ge "$emin" ]',
    ),
    # The allow-path stops allowing. A row asserting the override lets a push through must go red;
    # one that merely asserts "something happened" will not.
    mutate.Mutation(
        "override-ignored",
        "exit 0\n  fi\ndone",
        "exit 1\n  fi\ndone",
    ),
    # The gate stops confining itself to the configured repo and begins judging every repo.
    # Survival here would mean the scope check is untested.
    mutate.Mutation(
        "origin-scope-dropped",
        'printf \'%s\' "$origin" | grep -qF "$repo_pat" || exit 0',
        'printf \'%s\' "$origin" | grep -qF "$repo_pat" || true',
    ),
    # Nothing is ever recognised as publishing, so the gate never fires. A suite that only checks
    # "allowed commands stay allowed" passes this happily.
    mutate.Mutation(
        "push-detection-broken",
        'grep -qE "${gp}push([^A-Za-z0-9_]|\\$)"',
        'grep -qE "${gp}pushX([^A-Za-z0-9_]|\\$)"',
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

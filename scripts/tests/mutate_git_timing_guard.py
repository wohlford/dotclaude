#!/usr/bin/env python3
"""Mutation campaign for scripts/git-timing-guard.py, driven by scripts/lib/mutate.py.

Deliberately NOT named `test_*`: it mutates a tracked file in place, so pytest must not collect it
and `check_tests` must not discover it. Run it on demand, and never while editing the subject — the
restore writes back a pre-run snapshot and would silently revert a concurrent edit.

Why this campaign earns its keep more than most: the subject is a LIVE fail-closed gate whose suite
had never run anywhere before this commit. A suite with no track record cannot be trusted on a green
run alone, because a green suite and an absent suite look identical. These mutants are the evidence
that the rows reach the code they name.

SUBJECT moved here from scripts/git-timing-guard.sh (2026-09-02): that file is now a two-line
TRANSITIONAL shim that `exec`s this one, so mutating the shim's bash text would prove nothing about
the logic that actually runs. The SUITE below still drives the shim path, not the `.py` directly —
`exec` replaces the process, so invoking the shim exercises whatever the `.py` currently contains,
mutated or not.

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

SUBJECT = REPO / "scripts" / "git-timing-guard.py"
SUITE = ["bash", str(REPO / "scripts" / "tests" / "test_git_timing_guard.sh")]

MUTATIONS = [
    # Inside the window becomes outside and vice versa (a precise boolean negation of
    # `smin <= minute_of_day < emin`, parenthesized so it does not silently rebind to the
    # preceding `and` via `or`'s lower precedence). If nothing notices, the suite is not testing
    # the gate's central decision at all.
    mutate.Mutation(
        "window-inverted",
        "smin <= minute_of_day < emin",
        "(minute_of_day < smin or minute_of_day >= emin)",
    ),
    # The override stops authorizing. A row asserting ALLOW_GIT_WRITE=1 lets a push through must
    # go red; one that merely asserts "something happened" will not.
    mutate.Mutation(
        "override-ignored",
        "        if tok == OVERRIDE_TOKEN:\n            authorized = True",
        "        if tok == OVERRIDE_TOKEN:\n            authorized = False",
    ),
    # The gate stops confining itself to the configured repo and begins judging every repo.
    # Survival here would mean the scope check is untested.
    mutate.Mutation(
        "origin-scope-dropped",
        "    if repo_pat not in origin:\n        return 0",
        "    if False:\n        return 0",
    ),
    # Nothing is ever recognised as publishing, so the gate never fires. A suite that only checks
    # "allowed commands stay allowed" passes this happily.
    # The distinct-target cap stops bounding the per-target subprocess cost, so a decoy flood is
    # resolved one target at a time again. The cap row (nine distinct unguarded targets must block)
    # has to go red; the flood's own timing row would too, on the pre-cap cost.
    mutate.Mutation(
        "target-cap-dropped",
        "    if len(distinct) > MAX_PUSH_TARGETS:\n        return repo_pat\n",
        "",
    ),
    mutate.Mutation(
        "push-detection-broken",
        '            seg[sub_idx] == "push" or gitcmd.subcommand_is_indeterminate(seg[sub_idx])\n',
        '            seg[sub_idx] == "pushX" or gitcmd.subcommand_is_indeterminate(seg[sub_idx])\n',
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

#!/usr/bin/env python3
"""Mutation campaign for scripts/tests/test_hook_suite_guard.sh's SELECTION mechanism, driven by
scripts/lib/mutate.py.

Deliberately NOT named `test_*`: it mutates a tracked file in place, so pytest must not collect it.
Run it on demand — `./scripts/tests/mutate_hook_suite_guard_selection.py` — never while editing the
suite, never while another suite is running, and never alongside `mutate_audit_test.py`,
`mutate_publication_push_guard_test.py` or `mutate_hook_machinery_test.py`: all four campaigns share
this suite — the three others drive it against the REAL `scripts/audit-test.sh`,
`scripts/publication-push-guard-test.sh` and `scripts/hook-machinery-test.sh`, and this campaign
mutates the suite itself, so a live mutant in ANY of them corrupts every other campaign's rows too.
Nor alongside `mutate_hook_budget.py`: this suite's own `drive_audit`, `drive_ppg` and `drive_hm`
helpers shell out to the REAL `audit-test.sh`, `publication-push-guard-test.sh` and
`hook-machinery-test.sh`, each of which sources `scripts/lib/hook_budget.sh` to run its bounded
suites, so a live library mutant would score this campaign's rows as false catches too.

This suite is its own subject here, so it is also its own SUITE: the runner drives a nested,
selected invocation of `scripts/tests/test_hook_suite_guard.sh` against a mutated copy of the same
file, asking whether that nested run's own selection-related output still proves the mechanism
works.

Each row deletes or weakens ONE mechanism of `HOOK_TESTS_ONLY` selection, and names the row inside
this same file (run via the nested-selection block) that must catch it. A mutant the suite
survives is a claim about selection that nothing tests.

The repo root is derived from __file__: invoking this through a symlinked `~/.claude/scripts/tests/`
would resolve into PRODUCTION's tree and mutate that instead. Run it from the working copy you intend
to grade.

**Cost note:** under the CASES-filter mutant (`selected "$hook" || continue` deleted), every CASES
row runs in every selection instead of being filtered out. This campaign's suite invocation drives
three NESTED self-runs of `test_hook_suite_guard.sh` (one per selection assertion in the
hook-machinery-test.sh block), but only two of those grow — the third
(`HOOK_TESTS_ONLY=no-such-test.sh`) exits at the unknown-name check before the CASES loop is ever
reached, so nothing about that run changes cost under this mutant. Each of the two that DO reach the
loop now executes every CASES row instead of the handful its selection names — roughly ~60 s per
nested self-run instead of a few seconds. Measured 2026-09-16: that mutant's run took 210.9 s, well
inside mutate.py's derived cap but far slower than every other row in this campaign.

**Named gap:** the summary's marker-counting lines (the final `pass`/`fail` tally and the
`command-not-found` marker check that folds into it) have no mutation row of their own. A mutant
deleting them survives, because the only undefined command anywhere in the suite is the tripwire's
own deliberate probe (`tripwire_probe_undefined_command_zz`), and the row asserting THAT marker is
caught is the tripwire row above — removing the summary's marker-counting logic leaves that row's own
assertion (`pass_line`/`fail_line` on the marker file) intact, so nothing downstream of it can tell
the difference.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "scripts" / "lib"))
import mutate  # noqa: E402

SUBJECT = REPO / "scripts" / "tests" / "test_hook_suite_guard.sh"
SUITE = ["env", "HOOK_TESTS_ONLY=hook-machinery-test.sh", "bash", str(SUBJECT)]

MUTATIONS = [
    mutate.Mutation(
        "CASES rows ignore the selection — caught by the other-hook-rows-did-not-run row",
        '  selected "$hook" || continue\n',
        "",
    ),
    mutate.Mutation(
        "a bespoke section ignores the selection — caught by the unselected-bespoke-section row",
        "if selected audit-test.sh; then",
        "if true; then",
    ),
    mutate.Mutation(
        "an unknown name selects nothing and passes — caught by the unknown-name rows",
        '  if [ -n "$unknown" ]; then',
        "  if false; then",
    ),
    mutate.Mutation(
        "the tripwire handler records nothing — caught by the tripwire row",
        '  : > "$tmp/command-not-found"\n  return 127',
        "  return 127",
    ),
]


def main() -> int:
    report = mutate.run(SUBJECT, SUITE, MUTATIONS, cwd=str(REPO))
    print(report.text)
    return report.rc


if __name__ == "__main__":
    sys.exit(main())

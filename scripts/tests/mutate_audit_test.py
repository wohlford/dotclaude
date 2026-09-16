#!/usr/bin/env python3
"""Mutation campaign for scripts/audit-test.sh, driven by scripts/lib/mutate.py.

Deliberately NOT named `test_*`: it mutates a tracked file in place, so pytest must not collect it.
Run it on demand — `./scripts/tests/mutate_audit_test.py` — never while editing the hook or the
library it sources, never while another suite is running, and never alongside mutate_hook_budget.py:
the hook sources scripts/lib/hook_budget.sh, so a live library mutant would score this campaign's
rows as false catches. Nor alongside mutate_publication_push_guard_test.py: both campaigns share
this suite, test_hook_suite_guard.sh, which drives the REAL scripts/audit-test.sh and
scripts/publication-push-guard-test.sh directly from the working tree, so a live mutant in either
hook's own file corrupts the other campaign's rows too.

Each row deletes or weakens ONE mechanism of the bounded run, and names the guard-suite row that must
catch it. The suite drives the hook, so a surviving mutant is a claim about the kill that nothing
tests.

The repo root is derived from __file__: invoking this through a symlinked `~/.claude/scripts/tests/`
would resolve into PRODUCTION's tree and mutate that instead. Run it from the working copy you intend
to grade.

**Named gap:** the rule that `AUDIT_TEST_BUDGET` may only LOWER the budget has no mutation row. A
raised override is observable only by waiting past 540 s, which no suite row can afford; the
`audit_override_high` guard row is a CONTROL (green before and after) proving an ignored override
leaves failure reporting intact, not a test of the rule.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "scripts" / "lib"))
import mutate  # noqa: E402

SUBJECT = REPO / "scripts" / "audit-test.sh"
SUITE = ["bash", str(REPO / "scripts" / "tests" / "test_hook_suite_guard.sh")]

MUTATIONS = [
    mutate.Mutation(
        "an overrun reads as success — caught by the audit overrun rc and message rows",
        '  } >&2\n  exit 2\nfi\nif [ "$suite_rc" -ne 0 ]; then',
        '  } >&2\n  exit 0\nfi\nif [ "$suite_rc" -ne 0 ]; then',
    ),
    mutate.Mutation(
        "the budget is never applied — caught by the audit elapsed and survivor rows",
        'hb_run_bounded suite_rc live "$budget" "$root"',
        'hb_run_bounded suite_rc live 99999 "$root"',
    ),
    mutate.Mutation(
        "a missing library is skipped — caught by the audit library-absent message row",
        'if [ ! -r "$hook_lib" ]; then',
        "if false; then",
    ),
    mutate.Mutation(
        "an unusable python3 is not probed — caught by the audit python3 message row",
        "if ! python3 -c 'import os, sys; sys.exit(0 if hasattr(os, \"setsid\") else 1)' >/dev/null 2>&1; then",
        "if false; then",
    ),
    mutate.Mutation(
        "a failing suite reads as success — caught by the audit failing-suite row",
        '  tail -40 "$tmpdir/suite.out" >&2 || true\n  exit 2\n',
        '  tail -40 "$tmpdir/suite.out" >&2 || true\n  exit 0\n',
    ),
]


def main() -> int:
    report = mutate.run(SUBJECT, SUITE, MUTATIONS, cwd=str(REPO))
    print(report.text)
    return report.rc


if __name__ == "__main__":
    sys.exit(main())

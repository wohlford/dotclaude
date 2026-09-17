#!/usr/bin/env python3
"""Mutation campaign for scripts/hook-machinery-test.sh, driven by scripts/lib/mutate.py.

Deliberately NOT named `test_*`: it mutates a tracked file in place, so pytest must not collect it.
Run it on demand — `./scripts/tests/mutate_hook_machinery_test.py` — never while editing the hook or
the library it sources, never while another suite is running, and never alongside
`mutate_audit_test.py` or `mutate_publication_push_guard_test.py`: all three campaigns share this
suite, `test_hook_suite_guard.sh`, which drives the REAL `scripts/audit-test.sh`,
`scripts/publication-push-guard-test.sh` and `scripts/hook-machinery-test.sh` directly from the
working tree, so a live mutant in any one hook's own file corrupts the other campaigns' rows too.
Nor alongside `mutate_hook_suite_guard_selection.py`, which mutates the suite this campaign drives —
a live mutant there would score every row here as a false catch or a false survivor. Nor alongside
`mutate_hook_budget.py`: this hook sources `scripts/lib/hook_budget.sh` to run its bounded suites, so
a live library mutant would score this campaign's rows as false catches too.

Each row deletes or weakens ONE mechanism of the hook's classification, selection or bounded run, and
names the guard-suite row that must catch it. The suite drives the hook, so a surviving mutant is a
claim about the kill that nothing tests.

The repo root is derived from __file__: invoking this through a symlinked `~/.claude/scripts/tests/`
would resolve into PRODUCTION's tree and mutate that instead. Run it from the working copy you intend
to grade.

**Cost note:** measured 2026-09-16, `HOOK_TESTS_ONLY=hook-machinery-test.sh` narrows the guard suite
enough that the unmutated baseline runs in ~25.7 s, and each mutant's run costs roughly another 25 s
— roughly 4-5 minutes total across all 10 rows.

**Named gap:** the rule that `HOOK_MACHINERY_TEST_BUDGET` may only LOWER the registered budget has no
mutation row. A raised override is observable only by waiting past the hook's registered timeout,
which no suite row can afford — the same shape as `mutate_audit_test.py`'s named gap for
`AUDIT_TEST_BUDGET`.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "scripts" / "lib"))
import mutate  # noqa: E402

SUBJECT = REPO / "scripts" / "hook-machinery-test.sh"
SUITE = [
    "env",
    "HOOK_TESTS_ONLY=hook-machinery-test.sh",
    "bash",
    str(REPO / "scripts" / "tests" / "test_hook_suite_guard.sh"),
]

MUTATIONS = [
    mutate.Mutation(
        "an edit to the guard suite falls through to the nested-path exit — caught by the"
        " guard-suite-edit row",
        "  scripts/tests/test_hook_suite_guard.sh) guard_sel=ALL; trio_sel=ALL ;;\n",
        "",
    ),
    mutate.Mutation(
        "a hook edit runs the whole guard suite — caught by the selects-ONLY-that-hook row",
        'scripts/*-test.sh) guard_sel=$(basename "$rel"); trio_sel=$guard_sel ;;',
        'scripts/*-test.sh) guard_sel=ALL; trio_sel=$(basename "$rel") ;;',
    ),
    mutate.Mutation(
        "a *-test.sh below scripts/tests is classified as a hook — caught by the nested-test.sh row",
        "  scripts/*/*) exit 0 ;;\n",
        "",
    ),
    mutate.Mutation(
        "the edited file is not resolved through symlinks — caught by every owner row under a"
        " symlinked TMPDIR, and by the file-symlink row where TMPDIR is physical",
        "real=$(python3 -c 'import os, sys; print(os.path.realpath(sys.argv[1]))' \"$file_path\" 2>/dev/null) || exit 0",
        "real=$file_path",
    ),
    mutate.Mutation(
        "a lost hook_budget.sh consumer is not noticed — caught by the consumer-floor rows",
        '      *) missing="${missing}  scripts/$want no longer sources',
        '      *) : "${missing}  scripts/$want no longer sources',
    ),
    mutate.Mutation(
        "an inherited selection narrows an unselected run — caught by the unselected-trio and"
        " inherited-selection rows",
        'env HOOK_TESTS_ONLY="$sel" "$@"',
        'env "$@"',
    ),
    mutate.Mutation(
        "unavailable pytest is not probed — caught by the pytest-unavailable rows",
        'if [ -z "$missing" ] && [ -n "${trio_sel}${extra}" ] && ! python3 -c \'import pytest\''
        " >/dev/null 2>&1; then",
        "if false; then",
    ),
    mutate.Mutation(
        "a failing suite reads as success — caught by the failing-guard-suite rows",
        '  if [ "$one_rc" -ne 0 ]; then\n    failed=1\n',
        '  if [ "$one_rc" -ne 0 ]; then\n    failed=0\n',
    ),
    mutate.Mutation(
        "an overrun is not reported — caught by the overrun rows",
        '  if [ "$one_rc" = killed ]; then',
        "  if false; then",
    ),
    mutate.Mutation(
        "a non-owner alarms — caught by the generic NON-owner row",
        'if [ -n "$missing" ]; then\n  if grep -q \'dotclaude-test-runner-hook\' "$root/scripts/$(basename "$0")" 2>/dev/null; then',
        'if [ -n "$missing" ]; then\n  if true; then',
    ),
]


def main() -> int:
    report = mutate.run(SUBJECT, SUITE, MUTATIONS, cwd=str(REPO))
    print(report.text)
    return report.rc


if __name__ == "__main__":
    sys.exit(main())

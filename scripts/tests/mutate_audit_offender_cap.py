#!/usr/bin/env python3
# Script: mutate_audit_offender_cap.py
# Purpose: Mutation campaign for print_offenders full-detail preservation, driven by lib/mutate.py
# Usage:   ./scripts/tests/mutate_audit_offender_cap.py
"""Mutation campaign for `print_offenders`' artifact preservation.

Deliberately NOT named `test_*`: it mutates a tracked file in place, so pytest must not collect
it. Never run it while editing the subject — the restore writes back its own snapshot and would
silently discard your edits.

Why this subject earns its own campaign, separate from `mutate_audit_tests_report.py`. That one
is scoped to `check_tests`' reporting; this is the shared printer every other failing check routes
through. The defect it guards was measured: a 44-row index table plus one bad file produced 103
diff lines with the offending row at line 100, so all 50 visible lines were deletions of rows
whose content had not changed — the row explaining the failure was unreachable through the sweep
at all, structurally rather than by luck.

The rows below exist because a first pass at this campaign covered the `wrote_any` seam eight
ways and the NEW mechanism not at all: `audit_artifact_last`, the path print, and the
write-failure branch had zero mutants between them. A 7-of-7 score over the wrong seven is the
failure this file is written against — the out-param in particular WAS the fix for a measured
defect (echoing the path for `$( )` capture ran every assignment in a subshell), so a suite that
cannot notice its removal is not guarding it.
"""

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(Path.home() / ".claude" / "scripts" / "lib"))
import mutate  # noqa: E402

SUBJECT = REPO / "skills" / "audit" / "audit.sh"
SUITE = ["bash", str(REPO / "scripts" / "tests" / "test_audit.sh")]

MUTATIONS = [
    mutate.Mutation(
        "the out-param is never set, so the printed path is empty and names nothing",
        'audit_artifact_last="$target"',
        ":",
    ),
    mutate.Mutation(
        "the path is never printed, so the preserved detail is unreachable",
        "printf '  complete list saved to: %s\\n' \"$audit_artifact_last\"",
        ":",
    ),
    mutate.Mutation(
        "a failed write claims preservation instead of admitting it did not happen",
        "printf '  complete list: unavailable (could not create an artifact directory)\\n'",
        "printf '  complete list saved to: %s\\n' \"${audit_artifact_last:-/dev/null}\"",
    ),
    mutate.Mutation(
        "the excerpt shrinks below 50, dropping lines that were visible before this change",
        "sed -n '1,50p'",
        "sed -n '1,25p'",
    ),
    mutate.Mutation(
        "the re-run instruction is dropped on the theory the artifact replaces it",
        "printf '  … more (run the underlying tool for the full list)\\n'",
        ":",
    ),
    mutate.Mutation(
        "the scope guard is dropped, degenerating the containment test to `== /*`",
        'audit_artifact_write "${audit_scope:-}" "$label" "$detail"',
        'audit_artifact_write "" "$label" "$detail"',
    ),
]


def main() -> int:
    report = mutate.run(SUBJECT, SUITE, MUTATIONS, cwd=str(REPO))
    print(report.text)
    return report.rc


if __name__ == "__main__":
    sys.exit(main())

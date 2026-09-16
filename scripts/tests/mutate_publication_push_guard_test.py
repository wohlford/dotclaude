#!/usr/bin/env python3
"""Mutation campaign for scripts/publication-push-guard-test.sh, driven by scripts/lib/mutate.py.

Deliberately NOT named `test_*`: it mutates a tracked file in place, so pytest must not collect it.
Run it on demand — `./scripts/tests/mutate_publication_push_guard_test.py` — never while editing the
hook, since the restore would clobber the edit, and never while another suite is running. And never
alongside mutate_hook_budget.py: the hook sources scripts/lib/hook_budget.sh, so a live library
mutant would score this campaign's rows as false catches. Nor alongside mutate_audit_test.py: both
campaigns share this suite, test_hook_suite_guard.sh, which drives the REAL scripts/audit-test.sh
and scripts/publication-push-guard-test.sh directly from the working tree, so a live mutant in
either hook's own file corrupts the other campaign's rows too.

Each row deletes or weakens ONE mechanism the hook's header claims, and names the guard-suite row
that must catch it. A mutant the suite survives is a claim nothing tests.

The repo root is derived from __file__: invoking this through a symlinked `~/.claude/scripts/tests/`
would resolve into PRODUCTION's tree and mutate that instead. Run it from the working copy you intend
to grade.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "scripts" / "lib"))
import mutate  # noqa: E402

SUBJECT = REPO / "scripts" / "publication-push-guard-test.sh"
SUITE = ["bash", str(REPO / "scripts" / "tests" / "test_hook_suite_guard.sh")]

MUTATIONS = [
    mutate.Mutation(
        "a DEPENDENTS row is deleted — caught by ppg-push-guard's owner-missing row and the real-repo control",
        "scripts/push-guard.py|scripts/tests/test_push_guard.sh\n",
        "",
    ),
    mutate.Mutation(
        "an edited SUITE file no longer selects its row — caught by the suite-as-trigger rows",
        '  for s in ${row_suites[@]+"${row_suites[@]}"}; do\n    case "$file_path" in */"$s") hit=1 ;; esac\n  done\n',
        "",
    ),
    mutate.Mutation(
        "the import predicate stops at column 0 — caught by the lazy-importer MESSAGE row (its rc row still reads 2, via the tripwire) and the control",
        "printf '^[[:space:]]*(import",
        "printf '^(import",
    ),
    mutate.Mutation(
        "the closure over scripts/lib is dropped — caught by the transitive row's message",
        '        mods+=("$mod")\n        changed=1\n',
        "        changed=0\n",
    ),
    # The two rows below append to an array nothing reads, rather than commenting the line out: a
    # `: problems+=(...)` spelling is a bash SYNTAX error, which exits 2 for every row and would be
    # "caught" for a reason unrelated to the mechanism named.
    mutate.Mutation(
        "the floor stops alarming — caught by the floor row",
        '      *) problems+=("  discovery no longer sees $gate',
        '      *) ignored+=("  discovery no longer sees $gate',
    ),
    mutate.Mutation(
        "the tripwire stops alarming — caught by the tripwire row",
        '      problems+=("  $f mentions git_command',
        '      ignored+=("  $f mentions git_command',
    ),
    mutate.Mutation(
        "discovery failure reads as an empty set — caught by the discovery-fails row",
        "listing=$(git -C \"$root\" -c core.quotePath=false ls-files -co --exclude-standard -- '*.py') || return 1",
        "listing=$(git -C \"$root\" -c core.quotePath=false ls-files -co --exclude-standard -- '*.py') || listing=''",
    ),
    # The rule that a quoted git ls-files entry fails discovery. The overrun path's kill moved to
    # scripts/lib/hook_budget.sh and is mutated by scripts/tests/mutate_hook_budget.py.
    mutate.Mutation(
        "a quoted ls-files entry is skipped instead of failing discovery — caught by both quoted-path rows",
        '      \\"*) return 1 ;;\n',
        "",
    ),
    mutate.Mutation(
        "pytest unavailability becomes a silent skip — caught by the pytest-unavailable MESSAGE row (its rc row still reads 2: the shim fails every .py suite)",
        '    missing+=("  $s (present, but pytest is unavailable)")',
        "    :",
    ),
    mutate.Mutation(
        "a failing suite is not recorded — caught by both failing-suite rows",
        '    failures+=("${rel##*/}")\n',
        "",
    ),
]


def main() -> int:
    report = mutate.run(SUBJECT, SUITE, MUTATIONS, cwd=str(REPO))
    print(report.text)
    return report.rc


if __name__ == "__main__":
    sys.exit(main())

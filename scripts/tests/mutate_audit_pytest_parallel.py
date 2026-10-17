#!/usr/bin/env python3
# Script: mutate_audit_pytest_parallel.py
# Purpose: Mutation campaign for the opt-in parallel pytest mode of check_tests in audit.sh, driven by lib/mutate.py
# Usage: python3 scripts/tests/mutate_audit_pytest_parallel.py
"""Mutation campaign for the AUDIT_PYTEST_JOBS mode of `check_tests` in skills/audit/audit.sh.

Deliberately NOT named `test_*`: it mutates a tracked file in place, so pytest must not collect it.
Never run it while editing the subject: the restore writes back its own snapshot and would silently
discard your edits. It grades the `rPT` section of `test_audit.sh` ALONE, through the
`AUDIT_TEST_ONLY=rPT` switch, so a mutant is judged by the rows written for it (about a minute and a
half per mutant) and not by the whole six-minute suite. Start it through `scripts/run-long.sh`,
never inline: 24 mutants are well over a quarter of an hour.

Each row breaks ONE property of the mode and names, in its label, the row that must catch it. Rows
whose mutant is killed by no row would mean the row asserts nothing about its subject. Two rows can
share a kill set when their code differs; identical kill sets flag a POSSIBLE duplicate, to be
checked by reading the two. Rows PT9b, PT10, PT12 and PT15 are controls with no mutant on this
subject: they assert text or behaviour this change does not edit, or (PT9b) a negative that no
mutant here inverts. PT7g is not one: its first assertion kills the PT7 mutant alongside PT7c, and
only its second (the unfiltered collect is not blamed) is a negative.

Named gaps, stated rather than discovered: the `ser` pass's executed-versus-collected equality line
and the scrub inside `pytest_run` (the parallel path's runner helper) have no mutant here; the first
is the sibling of the par-pass equality mutant below, and the second is held by the PT16b assertion
over every recorded call and by PT16's `--version addopts=absent` assertion; no mutant targets it.
The `&& "$pytest_red" == true` gate on the xdist note printed before `FAIL tests` also has no
mutant; rows PT8 and PT9b assert it from both sides. Row PT6h's count floor is verified by hand, not
by a mutant. The 24 mutants cover the mode switch and its validation, the two passes' selections
and scrubs, the conservation checks, the terminal status word and mode note, the scratch-directory
lifecycle, the collect helper's unreadable-summary arm (PT7c) and exit-5-over-a-nonzero-count
clause (PT7f), the `pytest-conserve` artifact write (PT6i), and the red flag on the
conservation-failure path (PT6i).
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "scripts" / "lib"))
import mutate  # noqa: E402

SUBJECT = REPO / "skills" / "audit" / "audit.sh"
SUITE = [
    "env",
    "AUDIT_TEST_ONLY=rPT",
    "bash",
    str(REPO / "scripts" / "tests" / "test_audit.sh"),
]

MUTATIONS = [
    mutate.Mutation(
        "PT1 the default arm treats an empty AUDIT_PYTEST_JOBS as a parallel request",
        '[[ -n "$pytest_jobs" && "$pytest_jobs" -gt 1 ]]',
        '[[ -z "$pytest_jobs" || "$pytest_jobs" -gt 1 ]]',
    ),
    mutate.Mutation(
        "PT2 a count of 1 selects the parallel path instead of the serial gate",
        '"$pytest_jobs" -gt 1 ]]',
        '"$pytest_jobs" -ge 1 ]]',
    ),
    mutate.Mutation(
        "PT3 the worker count is hard-coded instead of taken from the variable",
        'pytest_run -q -n "$jobs" -m "not serial_only"',
        'pytest_run -q -n 8 -m "not serial_only"',
    ),
    mutate.Mutation(
        "PT3 the serial pass is given the worker pool, so the rows that must run alone share it",
        "pytest_run -q -m serial_only --junitxml",
        'pytest_run -q -n "$jobs" -m serial_only --junitxml',
    ),
    mutate.Mutation(
        "PT3 a completed parallel run prints the clean status word, so a gate accepts it",
        "audit_result_line PARALLEL-PASS 0",
        "audit_result_line PASS 0",
    ),
    mutate.Mutation(
        "PT4 an invalid AUDIT_PYTEST_JOBS is never refused, so a typo runs a pool it did not mean",
        'shellcheck_jobs_invalid "$pytest_jobs"; then',
        "false; then",
    ),
    mutate.Mutation(
        "PT5 a runner that does not list -n passes the probe, so the pool dies as a usage error",
        "*--numprocesses*) ;;",
        "*) ;;",
    ),
    mutate.Mutation(
        "PT6 the par pass is not required to have executed what it collected",
        '[[ "$c_par" -eq "$n_par" ]]',
        "true",
    ),
    mutate.Mutation(
        "PT6 a test run in both passes is not detected",
        '[[ "$overlap" -eq 0 ]]',
        "true",
    ),
    mutate.Mutation(
        "PT6 the two passes are not required to be EXACTLY the collected tests, compared as sets",
        '[[ "$missing" -eq 0 && "$extra" -eq 0 ]]',
        "true",
    ),
    mutate.Mutation(
        "PT6 the distinct pairs are sorted by classname alone, so comm's byte order assumption breaks and an overlap can go unseen",
        'lines = sorted({"%s|%s" % (c.get("classname", ""), c.get("name", "")) for c in root.iter("testcase")})',
        'lines = sorted({"%s|%s" % (c.get("classname", ""), c.get("name", "")) for c in root.iter("testcase")}, key=lambda s: s.split("|")[0])',
    ),
    mutate.Mutation(
        "PT7 exit 5 over a nonzero selection is accepted",
        '[[ "$rcs" -eq 5 && "$n_ser" -ne 0 ]]',
        "false",
    ),
    mutate.Mutation(
        "PT7 exit 5 over an empty selection is rejected, so a repo with no marked rows can never pass",
        '"$rcs" -ne 0 && "$rcs" -ne 5',
        '"$rcs" -ne 0',
    ),
    mutate.Mutation(
        "PT8 the par pass's own status is never tested, so a red parallel pass reads clean",
        'if [[ "$rcp" -ne 0 ]]; then pytest_pass_report "$scope" pytest-par',
        'if false; then pytest_pass_report "$scope" pytest-par',
    ),
    mutate.Mutation(
        "PT9 the ser pass's own status is never tested, so a red serial-only row reads clean",
        'if [[ "$rcs" -ne 0 && "$rcs" -ne 5 ]]; then pytest_pass_report "$scope" pytest-ser',
        'if false; then pytest_pass_report "$scope" pytest-ser',
    ),
    mutate.Mutation(
        "PT11 the parallel marker is set at the top of the check, so a run that never launched pass one reads parallel",
        'local pytest_jobs="${AUDIT_PYTEST_JOBS:-}" want_parallel=false runner_ok=false runner_out="" runner_rc=0',
        'local pytest_jobs="${AUDIT_PYTEST_JOBS:-}" want_parallel=false runner_ok=false runner_out="" runner_rc=0\n'
        '  tests_parallel_jobs="${AUDIT_PYTEST_JOBS:-}"',
    ),
    mutate.Mutation(
        "PT13 the scratch directory is not removed on the normal path",
        "  pytest_remove_tmp   # normal path",
        "  :   # normal path",
    ),
    mutate.Mutation(
        "PT14 the exit trap does not remove the scratch directory of an interrupted run",
        "  pytest_remove_tmp   # exit-trap backstop",
        "  :   # exit-trap backstop",
    ),
    mutate.Mutation(
        "PT16 the serial default no longer scrubs PYTEST_ADDOPTS, so an exported -n or -k changes the gate",
        'out="$(cd "$scope" && env -u PYTEST_ADDOPTS python3 -m pytest -q 2>&1)"; rc=$?',
        'out="$(cd "$scope" && python3 -m pytest -q 2>&1)"; rc=$?',
    ),
    mutate.Mutation(
        "PT17 a scratch location inside the scope is not refused, so the run writes into the tree it judges",
        'if audit_scratch_outside_scope "$scope" "$pbase"; then',
        "if true; then",
    ),
    mutate.Mutation(
        "PT7 an unreadable collect summary is accepted as a zero count, so a changed pytest wording reads clean",
        '    pytest_err="pytest [${label}] collect summary unreadable: ${last}"\n    return 1\n',
        "    pytest_collect_n=0\n",
    ),
    mutate.Mutation(
        "PT7 exit 5 over a nonzero collected count is accepted by the collect helper",
        ' \\\n    || [[ "$pytest_collect_rc" -eq 5 && "$pytest_collect_n" -ne 0 ]]; then',
        "; then",
    ),
    mutate.Mutation(
        "PT6 a conservation failure is not written as the pytest-conserve artifact",
        '            if audit_artifact_write "$scope" pytest-conserve "$pytest_err"; then wrote_any=true; fi\n',
        "",
    ),
    mutate.Mutation(
        "PT6 a conservation failure does not mark the pytest pass red, so the xdist note is not printed",
        "            detail=\"${detail}${pytest_err}\"$'\\n'\n            pytest_red=true\n",
        "            detail=\"${detail}${pytest_err}\"$'\\n'\n",
    ),
]


def main() -> int:
    report = mutate.run(SUBJECT, SUITE, MUTATIONS, cwd=str(REPO))
    print(report.text)
    return report.rc


if __name__ == "__main__":
    sys.exit(main())

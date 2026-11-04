#!/usr/bin/env python3
# Script: mutate_audit_shell_pool.py
# Purpose: Mutation campaign for the opt-in shell-suite pool of check_tests in audit.sh, driven by lib/mutate.py
# Usage: python3 scripts/tests/mutate_audit_shell_pool.py
"""Mutation campaign for the AUDIT_SHELL_JOBS pool of `check_tests` in skills/audit/audit.sh.

Deliberately NOT named `test_*`: it mutates a tracked file in place, so pytest must not collect it. Never run it while
editing the subject: the restore writes back its own snapshot and would silently discard your edits. It grades the `rSJ`
unit of `test_audit.sh` ALONE through the `AUDIT_TEST_ONLY=rSJ` switch (the unit runs after `rML` and `rPT`, which the
serial profile also executes first, so a mutant costs about five minutes). Start it through `scripts/run-long.sh`, never
inline: 37 mutants are hours. A mutant that survives is a finding, never a retry: either a row is missing (add it) or the
mutated line is inert (delete it).

Each row breaks ONE property and names, in its label, the row that must catch it. Kill sets are compared with
`scripts/kill-sets.py` from the clone's root through `run-long.sh`; `identical_pairs` must be 0 or each pair read and
justified. The five scrub mutants are distinct only because S8 and S19 assert ONE ROW PER NAME.

Named gaps, stated rather than discovered: the `unset AUDIT_SHELL_JOBS` preamble line is the SUITE (a second subject file),
so S18's evidence is the RED run recorded when it was added; the evaluator `scripts/gate-sweep-eval.py` has no campaign of
its own, its rows E0-E22 carry the inside/outside fixtures; `shell_pool_descendants`' recursion has no mutant (S17 reaches it
only through a one-level tree).
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
    "AUDIT_TEST_ONLY=rSJ",
    "bash",
    str(REPO / "scripts" / "tests" / "test_audit.sh"),
]

M = mutate.Mutation

MUTATIONS = [
    M(
        "S1 an invalid AUDIT_SHELL_JOBS is never refused, so a typo runs a pool it did not mean",
        'shellcheck_jobs_invalid "$shell_jobs"; then',
        "false; then",
    ),
    M(
        "S1 the pattern check is replaced by arithmetic, so 04 and 99999 are accepted",
        '[[ -n "$shell_jobs" ]] && shellcheck_jobs_invalid "$shell_jobs"; then',
        '[[ -n "$shell_jobs" ]] && [[ "$((shell_jobs))" -lt 1 ]]; then',
    ),
    M(
        "S6 conservation compares COUNTS, so equal counts with different sets read clean and the missing suite is not named",
        'LC_ALL=C comm -23 "$d/listed.sorted" "$d/recnames.sorted" > "$d/norec"',
        'if [ "$(wc -l < "$d/listed.sorted")" -eq "$(wc -l < "$d/recnames.sorted")" ]; then : > "$d/norec"; '
        'else cp "$d/listed.sorted" "$d/norec"; fi',
    ),
    M(
        "S5 the verdict is scraped from the suite's text, so a noisy suite that exits 0 is red",
        'if [[ "$rec_rc" -eq 0 && "$rec_cat" -eq 0 ]]; then',
        "if ! grep -q 'FAIL' \"$d/$i.out\" 2>/dev/null; then",
    ),
    M(
        "S4 the nonzero-status arm is dropped, so a suite that exits 3 reads green",
        'if [[ "$rec_rc" -eq 0 && "$rec_cat" -eq 0 ]]; then',
        'if [[ "$rec_cat" -eq 0 ]]; then',
    ),
    M(
        "S4b the capture status is ignored, so a failed capture over an exit-0 suite reads green",
        'if [[ "$rec_rc" -eq 0 && "$rec_cat" -eq 0 ]]; then',
        'if [[ "$rec_rc" -eq 0 ]]; then',
    ),
    M(
        "S8 the scrub drops AUDIT_TEST_JOBS",
        "    unset AUDIT_TEST_JOBS AUDIT_TEST_ONLY AUDIT_TEST_UNIT AUDIT_TEST_RUNDIR AUDIT_SHELL_JOBS",
        "    unset AUDIT_TEST_ONLY AUDIT_TEST_UNIT AUDIT_TEST_RUNDIR AUDIT_SHELL_JOBS",
    ),
    M(
        "S8 the scrub drops AUDIT_TEST_ONLY",
        "    unset AUDIT_TEST_JOBS AUDIT_TEST_ONLY AUDIT_TEST_UNIT AUDIT_TEST_RUNDIR AUDIT_SHELL_JOBS",
        "    unset AUDIT_TEST_JOBS AUDIT_TEST_UNIT AUDIT_TEST_RUNDIR AUDIT_SHELL_JOBS",
    ),
    M(
        "S8 the scrub drops AUDIT_TEST_UNIT",
        "    unset AUDIT_TEST_JOBS AUDIT_TEST_ONLY AUDIT_TEST_UNIT AUDIT_TEST_RUNDIR AUDIT_SHELL_JOBS",
        "    unset AUDIT_TEST_JOBS AUDIT_TEST_ONLY AUDIT_TEST_RUNDIR AUDIT_SHELL_JOBS",
    ),
    M(
        "S8 the scrub drops AUDIT_TEST_RUNDIR",
        "    unset AUDIT_TEST_JOBS AUDIT_TEST_ONLY AUDIT_TEST_UNIT AUDIT_TEST_RUNDIR AUDIT_SHELL_JOBS",
        "    unset AUDIT_TEST_JOBS AUDIT_TEST_ONLY AUDIT_TEST_UNIT AUDIT_SHELL_JOBS",
    ),
    M(
        "S8 the scrub drops AUDIT_SHELL_JOBS, so a suite that runs the audit can recurse into a pool",
        "    unset AUDIT_TEST_JOBS AUDIT_TEST_ONLY AUDIT_TEST_UNIT AUDIT_TEST_RUNDIR AUDIT_SHELL_JOBS",
        "    unset AUDIT_TEST_JOBS AUDIT_TEST_ONLY AUDIT_TEST_UNIT AUDIT_TEST_RUNDIR",
    ),
    M(
        "S10 the pool loop ends when everything is LAUNCHED, not finished, so the tail and pytest overlap it (S11 too)",
        '  while [[ "$finished" -lt "$total" ]]; do',
        '  while [[ "$next" -lt "$total" ]]; do',
    ),
    M(
        "S10 the serial tail is never run",
        '    sh_serial_list="$shell_serial_list"',
        '    sh_serial_list=""',
    ),
    M(
        "S10 the serial tail also runs BEFORE the pool",
        r'''  shell_serial_list="${shell_serial_list%$'\n'}"''',
        r'''  shell_serial_list="${shell_serial_list%$'\n'}"'''
        "\n"
        '  for t in $shell_serial_list; do run_suite_scrubbed "$scope/$t" > /dev/null 2>&1; done',
    ),
    M(
        "S11 the scratch directory is not removed on the normal path (read from inside pytest)",
        "  shell_pool_remove_tmp   # normal path",
        "  :   # normal path",
    ),
    M(
        "S17 the exit path never stops the children (must die on S17)",
        "  shell_pool_stop   # children first, then the directory they write into",
        "  :   # children first, then the directory they write into",
    ),
    M(
        "S13 the exit path stops the children but never removes the scratch directory (group TERM)",
        "  shell_pool_stop   # children first, then the directory they write into\n  shell_pool_remove_tmp\n",
        "  shell_pool_stop   # children first, then the directory they write into\n  :\n",
    ),
    M(
        "S17 the exit handling runs BEFORE the INCOMPLETE line is printed (must die on S17)",
        '  if [[ "$audit_phase" == init && "$1" -eq 2 ]]; then',
        '  shell_pool_cleanup\n  if [[ "$audit_phase" == init && "$1" -eq 2 ]]; then',
    ),
    M(
        "S16 the wrapper's pipe is replaced by a plain file redirect, so a leaked descendant no longer delays the record",
        '2>&1 | cat > "$d/$i.out"',
        '> "$d/$i.out" 2>&1 | cat > /dev/null',
    ),
    M(
        "S19 the serial loop starts suites without the scrub helper",
        'out="$(run_suite_scrubbed "$scope/$t" 2>&1)"; rc=$?',
        'out="$("$scope/$t" 2>&1)"; rc=$?',
    ),
    M(
        "S7 the width is off by one upward (width 2 runs three at once)",
        '    while [[ "$running" -lt "$jobs" && "$next" -lt "$total" ]]; do',
        '    while [[ "$running" -lt "$((jobs + 1))" && "$next" -lt "$total" ]]; do',
    ),
    M(
        "S7 the width is off by one downward (the rendezvous never completes)",
        '    while [[ "$running" -lt "$jobs" && "$next" -lt "$total" ]]; do',
        '    while [[ "$running" -lt "$((jobs - 1))" && "$next" -lt "$total" ]]; do',
    ),
    M(
        "S2 the terminal word is left as PASS after a pool run, so a gate would accept it",
        '    if [[ -n "$tests_parallel_jobs" || -n "$tests_shell_parallel" ]]; then',
        '    if [[ -n "$tests_parallel_jobs" ]]; then',
    ),
    M(
        "S2 the pool NOTE is dropped",
        r'''  printf 'NOTE tests: shell suites run in a pool of %s (AUDIT_SHELL_JOBS), not the serial gate; a red here is a finding to re-run serially, not load to dismiss\n' "$jobs"''',
        "  :",
    ),
    M(
        "S20 the drained line is dropped",
        r'''    printf 'NOTE tests: shell phase drained in %ss\n' "$((SECONDS - shell_pool_t0))"''',
        "    :",
    ),
    M(
        "S4 a failing suite's complete output is never preserved; only the excerpt survives",
        '        if audit_artifact_write "$scope" "$sname" "$sout"; then snote=""; wrote_any=true\n'
        "        else snote=' (full output NOT preserved)'; fi",
        '        snote=""',
    ),
    M(
        "S14 the conservation message is not written as the shell-conserve artifact",
        '    if audit_artifact_write "$scope" shell-conserve "$shell_conserve_err"; then wrote_any=true; fi\n',
        "",
    ),
    M(
        "S14 the record is moved into place unconditionally, so a broken record path arrives as an unreadable record",
        '&& mv -f "$d/$i.rec.tmp" "$d/$i.rec"',
        '; mv -f "$d/$i.rec.tmp" "$d/$i.rec"',
    ),
    M(
        "S6 a record whose suite does not match its index is not noticed",
        '    elif [[ "$rec_suite" != "$name" ]]; then',
        "    elif false; then",
    ),
    M(
        "S6 a duplicate record is not noticed",
        'LC_ALL=C uniq -d "$d/recnames.sorted" > "$d/dups"',
        ': > "$d/dups"',
    ),
    M(
        "S6 a record for an unlisted suite is not noticed by the set compare",
        'LC_ALL=C comm -13 "$d/listed.sorted" "$d/recnames.sorted" > "$d/unlisted"',
        ': > "$d/unlisted"',
    ),
    M(
        "S12 a floor suite absent from the listing is not noticed",
        '    if ! grep -qxF -- "$fname" "$d/all.base"; then',
        "    if false; then",
    ),
    M(
        "S10 a serial-tail name that is not a tracked suite is not noticed",
        '    if ! grep -qxF -- "$tname" "$d/all.base"; then',
        "    if false; then",
    ),
    M(
        "S12 a listing of zero suites over a repo that has test_*.sh files is not noticed",
        '  if [[ ! -s "$d/all" && "$2" -gt 0 ]]; then',
        "  if false; then",
    ),
    M(
        "S13 the scratch containment gate is dropped, so a scratch inside the scope is accepted",
        '    if audit_scratch_outside_scope "$scope" "$sbase"; then',
        "    if true; then",
    ),
    M(
        "S4 the shell-pool note before a red run is dropped",
        '    [[ -n "$tests_shell_parallel" && "$shell_red" == true ]] && shell_red_note',
        "    :",
    ),
    M(
        "S14 the longest-first order is dropped (the target no longer has pool index 0)",
        "| sort -k1,1nr -k2,2n | cut -d' ' -f3-",
        "| sort -k2,2n | cut -d' ' -f3-",
    ),
]


def main() -> int:
    report = mutate.run(SUBJECT, SUITE, MUTATIONS, cwd=str(REPO))
    print(report.text)
    return report.rc


if __name__ == "__main__":
    sys.exit(main())

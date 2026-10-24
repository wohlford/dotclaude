#!/usr/bin/env python3
"""Mutation campaign for scripts/lib/suite_parallel.sh, driven by scripts/lib/mutate.py.

Deliberately NOT named `test_*`: it mutates a tracked file in place, so pytest must not collect it.
Run it on demand: `./scripts/tests/mutate_suite_parallel.py`. Never run it while the library or
scripts/tests/test_suite_parallel.sh is being edited, and never alongside another campaign that runs
that suite: the suite sources the library from the working tree, so a live mutant corrupts every other
run's rows.

Each row breaks ONE verdict-deciding mechanism of the parallel profile and names the suite row that
must kill it. The suite is test_suite_parallel.sh, which drives fixture suites against the real library
(about 25 s), so the whole campaign takes a few minutes. The signal handling, the output ordering and the failing-unit MESSAGE are
not here: they decide nothing about pass or fail (a unit with fail > 0 already exits non-zero, so the
exit-status branch fails the run anyway), and test_suite_parallel.sh pins them directly.

`--kill-sets` runs the DUPLICATE-MUTANT check instead. A campaign that reports `caught=N survived=0`
measures redundancy, not omission, and a mutant that kills the same rows as another inflates the count
while measuring nothing. This mode applies each mutant to a COPY of the library, runs the suite against
the copy, and prints the set of FAIL rows each mutant produced. It exits 1 when a mutant killed nothing
(a survivor) or two mutants produced the same set, and 0 otherwise. It never touches the repository.

The repo root is derived from __file__: invoking this through a symlinked `~/.claude/scripts/tests/`
would resolve into PRODUCTION's tree and mutate that instead. Run it from the working copy you intend
to grade.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "scripts" / "lib"))
import mutate  # noqa: E402

SUBJECT = REPO / "scripts" / "lib" / "suite_parallel.sh"
SUITE = ["bash", str(REPO / "scripts" / "tests" / "test_suite_parallel.sh")]

MUTATIONS = [
    mutate.Mutation(
        "a unit with no result record reads as success - caught by the crash and silent rows C1-C3",
        "(crashed, killed or silent; exit ${sp_rcs[$i]})\"$'\\n'\n      failed=1\n",
        "(crashed, killed or silent; exit ${sp_rcs[$i]})\"$'\\n'\n      failed=0\n",
    ),
    mutate.Mutation(
        "the child's exit status is ignored - caught by the record-then-nonzero-exit row C4",
        '    elif [ "${sp_rcs[$i]}" -ne 0 ]; then\n',
        "    elif false; then\n",
    ),
    mutate.Mutation(
        "an ordered unit list that came out empty runs nothing and passes - caught by the empty-ordering row D10",
        '  if [ "$total" -eq 0 ] || [ "$total" -ne "$nlist" ]; then\n',
        "  if false; then\n",
    ),
    mutate.Mutation(
        "rows outside every unit are not detected - caught by rows D3 and D4",
        '  if [ "$outside" != 0 ]; then\n',
        "  if false; then\n",
    ),
    mutate.Mutation(
        "a unit that lost its wrapper is not noticed - caught by rows D1 and D2",
        '  if [ -n "$missing" ]; then\n',
        "  if false; then\n",
    ),
    mutate.Mutation(
        "a unit with no rows and no skip passes - caught by rows D5 and D6",
        '    elif [ $((rp + rf)) -eq 0 ] && [ "$rs" -eq 0 ]; then\n',
        "    elif false; then\n",
    ),
    mutate.Mutation(
        "skip_line does not count - caught by the counted-skip row D7",
        "  SP_SKIPS=$((SP_SKIPS + 1))\n",
        "  :\n",
    ),
    mutate.Mutation(
        "child mode is keyed on the name alone - caught by rows E3 and I3 (I3 only in the real-suite form: it must print no PASS row)",
        '    if [ -z "$AUDIT_TEST_RUNDIR" ] || [ ! -d "$AUDIT_TEST_RUNDIR" ]; then\n',
        "    if false; then\n",
    ),
]


def kill_sets() -> int:
    """Print each mutant's set of FAIL rows, measured on a copy; flag survivors and duplicates."""
    layout = (
        "scripts/lib/suite_parallel.sh",
        "scripts/tests/test_suite_parallel.sh",
        "scripts/tests/test_audit.sh",
    )
    sets: dict[str, frozenset[str]] = {}
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        for rel in layout:
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(REPO / rel, root / rel)
        copy = root / "scripts" / "lib" / "suite_parallel.sh"
        original = copy.read_text()
        suite = ["bash", str(root / "scripts" / "tests" / "test_suite_parallel.sh")]

        def failing_rows() -> frozenset[str]:
            done = subprocess.run(
                suite, capture_output=True, text=True, check=False, timeout=900
            )
            return frozenset(
                line for line in done.stdout.splitlines() if line.startswith("FAIL  ")
            )

        baseline = failing_rows()
        if baseline:
            print("kill-sets: the UNMUTATED copy already fails: %s" % sorted(baseline))
            print("RESULT: ERROR rc=2 mutants=%d" % len(MUTATIONS))
            return 2
        for m in MUTATIONS:
            if original.count(m.old) != 1:
                print("kill-sets: anchor does not occur exactly once: %s" % m.label)
                print("RESULT: ERROR rc=2 mutants=%d" % len(MUTATIONS))
                return 2
            copy.write_text(original.replace(m.old, m.new))
            try:
                sets[m.label] = failing_rows()
            finally:
                copy.write_text(original)
    bad = 0
    for label, rows in sets.items():
        print("%3d FAIL rows  %s" % (len(rows), label))
        if not rows:
            print("    SURVIVED: no row failed")
            bad += 1
    labels = list(sets)
    for i, a in enumerate(labels):
        for b in labels[i + 1 :]:
            if sets[a] and sets[a] == sets[b]:
                print("DUPLICATE kill set:\n  %s\n  %s" % (a, b))
                bad += 1
    print(
        "RESULT: %s rc=%d mutants=%d"
        % ("FAIL" if bad else "PASS", 1 if bad else 0, len(sets))
    )
    return 1 if bad else 0


def main() -> int:
    if sys.argv[1:] == ["--kill-sets"]:
        return kill_sets()
    report = mutate.run(SUBJECT, SUITE, MUTATIONS, cwd=str(REPO))
    print(report.text)
    return report.rc


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Mutation campaign for scripts/kill-sets.py, driven by scripts/lib/mutate.py.

Deliberately NOT named `test_*`: it mutates a tracked file in place, so pytest must not collect
it. Run it on demand through `scripts/run-long.sh` (every row of the suite spawns the tool, and
the tool spawns suites, so one suite run takes minutes and the campaign tens of minutes) and
never while editing the subject, since the restore would clobber your edits:

    ./scripts/tests/mutate_kill_sets.py

Every row removes ONE core mechanism of the tool and names, after `caught by`, the
`scripts/tests/test_kill_sets.py` row that must notice. A survivor means that row does not
assert the effect the mechanism exists for: strengthen the row (a failing case first), never
reword the mutation. Each `old` string occurs exactly once in the subject
(`scripts/mutation-anchors-check.py` enforces it). The ids `(1)` to `(22)` are the spec's
campaign list for this tool, in its order.

Mechanisms pinned by a test row but deliberately NOT mutated here, by the spec's sizing rule
(one mutant per core mechanism; each of these would add a full suite run for a mechanism a row
already pins):

* the process-group kill after every run (`_kill_group`, pinned by t09);
* the environment scrub of `KILLSETS` and `KILL_LOG` (`_child_env`, pinned by t13a);
* the two INVALID detectors: the parse check (t10a) and the collection-abort banner (t10b);
* the duplicate-base refusal in the baseline gate (t04b);
* the empty-set duplicate exclusion: only unflagged KILLED mutants, whose kill sets are
  non-empty, enter the duplicate report (t01, t05).
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "scripts" / "lib"))
import mutate  # noqa: E402

M = mutate.Mutation

SUBJECT = REPO / "scripts" / "kill-sets.py"
SUITE = [
    sys.executable,
    "-m",
    "pytest",
    "-q",
    "-p",
    "no:cacheprovider",
    str(REPO / "scripts/tests/test_kill_sets.py"),
]

MUTATIONS = [
    M(
        "(1) absence counted from the mutant's own PASS count, not U's names - caught by t03a",
        "    kill = universe - reading.passed\n",
        "    kill = frozenset(sorted(universe)[len(reading.passed) :])\n",
    ),
    M(
        "(2) the nF comparison dropped: unexplained and excess always 0 - caught by t03a",
        "    unexplained, excess = max(0, len(kill) - nF), max(0, nF - len(kill))\n",
        "    unexplained, excess = 0, 0\n",
    ),
    M(
        "(3) the nested-dump skip removed - caught by t03a",
        "        if DUMP_START.match(line):\n",
        "        if False:\n",
    ),
    M(
        "(4) pytest armed with -rA instead of -rpfE - caught by t02",
        'PYTEST_REPORT = "-rpfE"',
        'PYTEST_REPORT = "-rA"',
    ),
    M(
        "(5) pytest ERROR ids not counted as explaining absences - caught by t02",
        "    failing.update(errored)  # a setup or teardown ERROR is a failing row too\n",
        "    pass\n",
    ),
    M(
        "(6) the -x strip removed - caught by t08",
        'EXITFIRST = {"-x", "--exitfirst"}',
        "EXITFIRST = set()",
    ),
    M(
        "(7) the stopping-banner reason string removed - caught by t08",
        '        why += f" (reason: {reading.stopped})" if reading.stopped else ""\n',
        '        why += ""\n',
    ),
    M(
        "(8) the non-empty-universe check removed - caught by t04b",
        "        if not universe:\n",
        "        if False:\n",
    ),
    M(
        "(9) the second-baseline universe comparison removed - caught by t04b",
        "        if u1 != u2:\n",
        "        if False:\n",
    ),
    M(
        "(10) the canary skipped - caught by t12",
        "        g.canary()\n",
        "        pass\n",
    ),
    M(
        "(11) the canary text made empty - caught by t12",
        'CANARY_TEXT = "{"',
        'CANARY_TEXT = ""',
    ),
    M(
        "(12) the argv tree classification removed - caught by t11",
        "            tree = _tree_of(value, work, live, master)\n",
        '            tree = "W"\n',
    ),
    M(
        "(13) the *.mutate-backup and .git/*.lock preflight removed - caught by t11",
        "\n    preflight(root)\n",
        "\n",
    ),
    M(
        "(14) the working copy reused across mutants instead of re-created - caught by t13a",
        "            refresh_working_copy(self.master, self.work)\n",
        "            pass\n",
    ),
    M(
        "(15) the anchor-count check accepts a count other than 1 - caught by t05",
        "            if count != 1:\n",
        "            if count == 0:\n",
    ),
    M(
        "(16) the KILLED-ALL test kill >= C weakened to |kill| >= |C|-1 - caught by t06a",
        "        return kill >= canary_absent\n",
        "        return len(kill) >= len(canary_absent) - 1\n",
    ),
    M(
        "(17) the dup_in_run check dropped - caught by t03a",
        "    dup_in_run = sorted(b for b, texts in reading.texts.items() if len(texts) > 1)\n",
        "    dup_in_run = []\n",
    ),
    M(
        "(18) UNATTRIBUTED mapped to FAIL, not ERROR - caught by t07a",
        '    "unattributed": "ERROR",',
        '    "unattributed": "FAIL",',
    ),
    M(
        "(19) the live-subject leak check removed - caught by t13a",
        "    if now == live_sha:\n",
        "    if True:\n",
    ),
    M(
        "(20) a field inserted before mutants= in the RESULT line - caught by t14b",
        "rc={rc} mutants={counts['mutants']}\"",
        "rc={rc} extra=0 mutants={counts['mutants']}\"",
    ),
    M(
        "(21) the opaque-argv refusal removed - caught by t04a",
        "    if opaque(c.suite):\n",
        "    if False:\n",
    ),
    M(
        "(22) row_base dropped from the shell reader - caught by t01",
        "            texts.setdefault(row_base(m.group(1)), []).append(m.group(1))\n",
        "            texts.setdefault(m.group(1), []).append(m.group(1))\n",
    ),
]


def main() -> int:
    report = mutate.run(SUBJECT, SUITE, MUTATIONS, cwd=str(REPO))
    print(report.text)
    return report.rc


if __name__ == "__main__":
    sys.exit(main())

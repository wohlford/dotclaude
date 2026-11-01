#!/usr/bin/env python3
"""Mutation campaign for scripts/lib/cycle_profile_cycles.py, driven by scripts/lib/mutate.py.

Deliberately NOT named `test_*`: it mutates a tracked file in place, so pytest must not collect
it. Run it on demand through `scripts/run-long.sh` and never while editing the subject, since
the restore would clobber your edits:

    ./scripts/tests/mutate_cycle_profile_cycles.py

Every row removes ONE core mechanism of the cycle roster, the closing rule or the run-long
pairing and names, after `caught by`, the `scripts/tests/test_cycle_profile_cycles.py` row that
must notice (a test function id, or a parametrized node). A survivor means that row does not
assert the effect the mechanism exists for: strengthen the row (a failing case first), never
reword the mutation. Each `old` string occurs exactly once in the subject, and no two rows
cite the same id. A mutant may fail other rows too; the cited one is the one that must.

Mechanisms pinned by a row but deliberately NOT mutated here, by the spec's sizing rule (one
mutant per core mechanism):

* the 180 s nearest-wait bound and its claimed set (pinned by the t11a nearest-arm rows; the
  key-arm mutant below already fails most of them);
* the optional feature-tip suite and the chain's label rule (t09f, g8d): second-order shapes of
  the closing rule, whose boundaries the three constants' rows already exercise;
* the lane decision and the reconstructable flag (t10g, t10h): derived fields downstream of
  the window, not a part of the closing rule;
* the two read-only cross-checks `artifact_end_check` and `commit_time_lower_bound` (t36, t38):
  optional readers that feed nothing back into the roster.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "scripts" / "lib"))
import mutate  # noqa: E402

M = mutate.Mutation

SUBJECT = REPO / "scripts" / "lib" / "cycle_profile_cycles.py"
SUITE = [
    sys.executable,
    "-m",
    "pytest",
    "-q",
    "-p",
    "no:cacheprovider",
    str(REPO / "scripts/tests/test_cycle_profile_cycles.py"),
]

MUTATIONS = [
    M(
        "(1) re-run boundary exclusive: `<` for `<=` at the 900 s chain limit - caught by t09a",
        "0 <= p.launch_ms - last.terminal_ms <= rerun_ms\n                and (p",
        "0 <= p.launch_ms - last.terminal_ms < rerun_ms\n                and (p",
    ),
    M(
        "(2) closing-marker upper edge exclusive: `<` for `<=` at 1800 s - caught by t09b",
        "if end < t <= end + post_ms:",
        "if end < t < end + post_ms:",
    ),
    M(
        "(3) real-suite boundary exclusive: `>` for `>=` at 600 s - caught by t09c",
        "p.terminal_ms - p.launch_ms >= min_ms",
        "p.terminal_ms - p.launch_ms > min_ms",
    ),
    M(
        "(4) search made unbounded: every invocation searches to the end - caught by t09d",
        "bound = roster[index + 1].t_ms if index + 1 < len(roster) else last_ms + 1000",
        "bound = last_ms + 1000",
    ),
    M(
        "(5) order fallback removed: no unlabelled pair ever closes a cycle - caught by t09e",
        '            if 0 <= b.launch_ms - a.terminal_ms <= rerun_ms:\n                f1, d1, basis = a, b, "order"',
        '            if False:\n                f1, d1, basis = a, b, "order"',
    ),
    M(
        "(6) a closing constant hardcoded: the re-run limit ignores `Params` - caught by t09g[rerun]",
        "rerun_ms = params.close_rerun_min * 60000",
        "rerun_ms = 10 * 60000",
    ),
    M(
        "(7) dedupe window inclusive: `<=` for `<` at 60 s - caught by t10b",
        "abs(k.t_ms - event.t_ms) < 60000",
        "abs(k.t_ms - event.t_ms) <= 60000",
    ),
    M(
        "(8) sidechain test removed: delegate records take part - caught by t10d",
        "if not e.sidechain]",
        "if True]",
    ),
    M(
        "(9) other skill names counted in the roster: commit and propagate - caught by t10c",
        'tok.kind == "invocation" and tok.a == "feature":',
        'tok.kind == "invocation" and tok.a in ("feature", "commit", "propagate"):',
    ),
    M(
        "(10) key arm drops the artifact-key test: any later wait pairs - caught by t11a",
        "            if not call.key or later.key != call.key:\n                continue",
        "            if not call.key:\n                continue",
    ),
    M(
        "(11) UNPAIRED counted as a wait: a launch starts with a paired method - caught by t11c",
        '        self.method = "UNPAIRED"\n',
        '        self.method = "bg-wait-nearest"\n',
    ),
]


def main() -> int:
    report = mutate.run(SUBJECT, SUITE, MUTATIONS, cwd=str(REPO))
    print(report.text)
    return report.rc


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Mutation campaign for scripts/lib/cycle_profile_classes.py, driven by scripts/lib/mutate.py.

Deliberately NOT named `test_*`: it mutates a tracked file in place, so pytest must not collect
it. Run it on demand through `scripts/run-long.sh` (one suite run per mutant, so the campaign
takes about a minute and a half of suite time plus the harness's own baseline) and never while
editing the subject, since the restore would clobber your edits:

    ./scripts/tests/mutate_cycle_profile_classes.py

Every row removes ONE core mechanism of the module and names, after `caught by`, the
`scripts/tests/test_cycle_profile_classes.py` row that must notice. A survivor means that row
does not assert the effect the mechanism exists for: strengthen the row (a failing case
first), never reword the mutation. Each `old` string occurs exactly once in the subject, and
no two rows cite the same sub-row, so no two mutants are the same defect twice. The ids are
the sub-row ids of the plan's ownership table; the four regression defects the original
scripts found mid-run are t04a, t05a, t06a and t08a.

Mechanisms pinned by a test row but deliberately NOT mutated here, by the spec's sizing rule
(one mutant per core mechanism; each of these is either pinned by a row that a mutant above
would only re-kill, or lives in another module):

* the wrapper-commit human-prompt rule (t01b) and the auto-continuation, ask-user and
  interrupt turn marks (t08d to t08h): the reader's token classification, not the sweep;
* the delivery-record fallback and the orphan rule (t05b, t05c): the same `_dispatches`
  branch as the ack-end mutant, and a row for each already asserts the one outcome;
* the stage cut and its keyword map (t41, t46, t40): slice B, pinned by their own rows and
  not a core mechanism of the class seconds;
* the sensitivity sets, the order indicators and the sub-check (t40, t44a to t44g, t45):
  derived reports over the same sweep, which the mutants below already break first;
* the inner-fence equality and `count_above` strictness (t07j, t07k): one `>` each, the
  same boundary shape as the HOOK threshold mutant;
* the leave-one-out fence range (t07i, t44c): a derived report, not a class second.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "scripts" / "lib"))
import mutate  # noqa: E402

M = mutate.Mutation

SUBJECT = REPO / "scripts" / "lib" / "cycle_profile_classes.py"
SUITE = [
    sys.executable,
    "-m",
    "pytest",
    "-q",
    "-p",
    "no:cacheprovider",
    str(REPO / "scripts/tests/test_cycle_profile_classes.py"),
]

MUTATIONS = [
    M(
        "priority order swapped: SUBAGENT outranks MACHINE_WAIT - caught by t03a",
        '_SWEEP_ORDER = tuple(name for name in PRIORITY if name != "CONTROLLER")\n',
        '_SWEEP_ORDER = ("SUBAGENT", "MACHINE_WAIT", "HOOK", "OPERATOR_RESPONSE", "AWAY")\n',
    ),
    M(
        "sweep boundary off by one second at the window end - caught by t02",
        "            a, b = max(span[0], lo_ms), min(span[1], hi_ms)\n",
        "            a, b = max(span[0], lo_ms), min(span[1], hi_ms - 1000)\n",
    ),
    M(
        "a class dropped: the OPERATOR_RESPONSE intervals never reach the sweep - caught by t01a",
        '        "OPERATOR_RESPONSE": [s for s in booked if s[2] == "OPERATOR_RESPONSE"],\n',
        '        "OPERATOR_RESPONSE": [],\n',
    ),
    M(
        "HOOK summed over its concurrent spans, not unioned - caught by t04a",
        '        parts: Dict[str, float] = {}\n        if cls == "MACHINE_WAIT":\n',
        '        if cls == "HOOK":\n            dt *= sum(active["HOOK"].values())\n'
        '        parts: Dict[str, float] = {}\n        if cls == "MACHINE_WAIT":\n',
    ),
    M(
        ">= for > at the HOOK latency threshold - caught by t04b",
        "        if c.end_ms - c.start_ms > threshold_ms and lo_ms <= c.start_ms < hi_ms\n",
        "        if c.end_ms - c.start_ms >= threshold_ms and lo_ms <= c.start_ms < hi_ms\n",
    ),
    M(
        "the hook-latency parameter parsed and dropped: the default always applies"
        " - caught by t04d",
        '        "HOOK": hook_spans(context.edits, context.params.hook_latency_s * 1000, lo, hi),\n',
        '        "HOOK": hook_spans(context.edits, Params().hook_latency_s * 1000, lo, hi),\n',
    ),
    M(
        "subagent interval ends at the ack, not the enqueue - caught by t05a",
        "        end = enqueued.get(tool_id)\n",
        "        end = acks.get(tool_id, (None, None))[0]\n",
    ),
    M(
        "resumed dispatches merged into their task's first interval - caught by t06a",
        "    subagent = [\n        (d.start_ms, d.end_ms, d.role)\n",
        "    firsts: Dict[Any, int] = {}\n"
        "    for d in dispatches:\n"
        "        firsts.setdefault(d.task_id, d.start_ms)\n"
        "    subagent = [\n        (firsts.get(d.task_id, d.start_ms), d.end_ms, d.role)\n",
    ),
    M(
        "the outer fence used as the inner one by default - caught by t07a",
        "        q1, q3, 10 ** (q3 + k * iqr), 10 ** (q3 + OUTER_FENCE_K * iqr), k, method\n",
        "        q1, q3, 10 ** (q3 + OUTER_FENCE_K * iqr), 10 ** (q3 + OUTER_FENCE_K * iqr), k, method\n",
    ),
    M(
        "the quartile method argument ignored: always inclusive - caught by t07c",
        "    q1, _, q3 = statistics.quantiles(logs, n=4, method=method)\n",
        '    q1, _, q3 = statistics.quantiles(logs, n=4, method="inclusive")\n',
    ),
    M(
        "the fit floor ignored: any two gaps fit a fence - caught by t07e",
        "    if len(gaps_s) < max(floor, 2):\n",
        "    if len(gaps_s) < 2:\n",
    ),
    M(
        "job-free remainder booked but left out of the fit list - caught by t08a",
        "    gaps_s = [e.seconds for e in entries]\n",
        '    gaps_s = [e.seconds for e in entries if e.kind == "pure"]\n',
    ),
]


def main() -> int:
    report = mutate.run(SUBJECT, SUITE, MUTATIONS, cwd=str(REPO))
    print(report.text)
    return report.rc


if __name__ == "__main__":
    sys.exit(main())

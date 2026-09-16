#!/usr/bin/env python3
"""Mutation campaign for scripts/lib/hook_budget.sh, driven by scripts/lib/mutate.py.

Deliberately NOT named `test_*`: it mutates a tracked file in place, so pytest must not collect it.
Run it on demand — `./scripts/tests/mutate_hook_budget.py` — never while editing the library or a hook
that sources it, never while another suite is running, and never alongside
mutate_publication_push_guard_test.py or mutate_audit_test.py: those hooks source this library, so a
live mutant here would score their rows as false catches.

Each row deletes or weakens ONE mechanism of the bounded run, and names the guard-suite row that must
catch it. The suite drives the hooks that source the library, so a surviving mutant is a claim about
the kill that nothing tests.

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

SUBJECT = REPO / "scripts" / "lib" / "hook_budget.sh"
SUITE = ["bash", str(REPO / "scripts" / "tests" / "test_hook_suite_guard.sh")]

MUTATIONS = [
    mutate.Mutation(
        "an overrun no longer stops the suite — caught by the survivor and elapsed rows",
        '      hb_kill_suite "$_hb_pid"\n      wait "$_hb_pid" 2>/dev/null || true\n',
        '      wait "$_hb_pid" 2>/dev/null || true\n',
    ),
    mutate.Mutation(
        "the pre-setsid fallback is dropped — caught by the launch-window elapsed row",
        'kill -TERM -- "-$_hb_pid" 2>/dev/null || kill -TERM "$_hb_pid" 2>/dev/null || true',
        'kill -TERM -- "-$_hb_pid" 2>/dev/null || true',
    ),
    mutate.Mutation(
        "the kill polls only the leader, so a TERM-ignoring descendant survives — caught by the overrun survivor row",
        '    if ! kill -0 -- "-$_hb_pid" 2>/dev/null && ! kill -0 "$_hb_pid" 2>/dev/null; then',
        '    if ! kill -0 "$_hb_pid" 2>/dev/null; then',
    ),
    mutate.Mutation(
        "no KILL escalation, so a TERM-ignoring descendant survives — caught by the overrun survivor row",
        '  kill -KILL -- "-$_hb_pid" 2>/dev/null || kill -KILL "$_hb_pid" 2>/dev/null || true\n',
        "  :\n",
    ),
    mutate.Mutation(
        "the deadline is never checked while a suite runs — caught by the overrun rows",
        '    if [[ "$SECONDS" -ge "$_hb_deadline" ]]; then\n      hb_kill_suite',
        "    if false; then\n      hb_kill_suite",
    ),
    mutate.Mutation(
        "a killed suite reads as a completed one — caught by the two killed mid-run message rows (the GATE DID NOT COMPLETE report still arrives via not_run)",
        "      printf -v \"$_hb_rc_var\" '%s' killed\n",
        "      printf -v \"$_hb_rc_var\" '%s' 0\n",
    ),
    mutate.Mutation(
        "a failing suite's status is dropped — caught by both failing-suite rows",
        '  wait "$_hb_pid" || _hb_rc=$?\n',
        '  wait "$_hb_pid" || true\n',
    ),
]


def main() -> int:
    report = mutate.run(SUBJECT, SUITE, MUTATIONS, cwd=str(REPO))
    print(report.text)
    return report.rc


if __name__ == "__main__":
    sys.exit(main())

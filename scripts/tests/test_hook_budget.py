"""Every test-runner hook that bounds itself declares a budget at least REQUIRED_MARGIN_SECS below its registered timeout.

Claude Code kills a hook that outlives its `settings.json` `timeout`, discards its output and never
tells Claude it timed out, so a test-runner hook that overruns is a gate that silently did not run.
A hook that bounds itself declares `HOOK_BUDGET_SECS=<n>` on a line of its own and reports an
overrun as exit 2. That only works while the budget stays below the registration — two numbers in
two files, which drift silently unless something holds them together. This module is that thing.

An ABSENT `timeout` is read as the harness default (600 s), never as "unbounded": the harness
applies its default whether or not the key is written.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent.parent
SETTINGS = REPO / "settings.json"
PREFIX = "$HOME/.claude/scripts/"
POPULATION_SUFFIX = "-test.sh"
BUDGET_RE = re.compile(r"^HOOK_BUDGET_SECS=(\d+)$", re.M)

sys.path.insert(0, str(REPO / "scripts" / "lib"))
import settings_hooks  # noqa: E402

# Named so that a hook LOSING its budget line alarms — a derivation over "hooks that declare a
# budget" silently shrinks to nothing and passes.
FLOOR = frozenset(
    {"audit-test.sh", "hook-machinery-test.sh", "publication-push-guard-test.sh"}
)

# The overrun path runs PAST the budget before it can report: a suite's TERM→KILL escalation takes up
# to ~5.5 s, then the hook prints its report. A budget closer to the timeout than this lets the
# harness kill the hook first and discard the very report the budget exists to deliver.
REQUIRED_MARGIN_SECS = 30


def _budgeted() -> list[tuple[str, int, float]]:
    doc = json.loads(SETTINGS.read_text())
    rows = []
    for _event, _matcher, entry in settings_hooks.walk_hook_entries(doc, str(SETTINGS)):
        base = settings_hooks.hook_basename(entry["command"], PREFIX)
        if not base or not base.endswith(POPULATION_SUFFIX):
            continue
        match = BUDGET_RE.search((REPO / "scripts" / base).read_text())
        if match:
            timeout = settings_hooks.entry_timeout(entry, str(SETTINGS))
            rows.append((base, int(match.group(1)), timeout))
    return rows


def test_floor_hooks_declare_a_budget():
    names = {base for base, _budget, _timeout in _budgeted()}
    missing = sorted(FLOOR - names)
    assert not missing, (
        "registered hooks with no `HOOK_BUDGET_SECS=<n>` line: %s — a hook that runs minutes of "
        "suites must bound itself below its registered timeout" % missing
    )


def test_every_budget_is_below_its_registered_timeout():
    rows = _budgeted()
    assert rows, "no registered hook declares a budget — the derivation matched nothing"
    bad = [
        (b, budget, timeout)
        for b, budget, timeout in rows
        if not budget + REQUIRED_MARGIN_SECS <= timeout
    ]
    assert not bad, (
        "budget not at least %d s below the registered timeout — the overrun path needs that long "
        "to stop a suite and report: %s" % (REQUIRED_MARGIN_SECS, bad)
    )


def test_walk_hook_entries_keeps_the_entry_and_its_timeout():
    doc = {
        "hooks": {
            "PostToolUse": [
                {
                    "matcher": "Edit|Write",
                    "hooks": [
                        {"type": "command", "command": "a.sh", "timeout": 120},
                        {"type": "command", "command": "b.sh"},
                    ],
                }
            ]
        }
    }
    entries = settings_hooks.walk_hook_entries(doc, "fixture")
    by_command = {entry["command"]: entry for _e, _m, entry in entries}
    assert settings_hooks.entry_timeout(by_command["a.sh"], "fixture") == 120
    assert settings_hooks.entry_timeout(by_command["b.sh"], "fixture") == 600
    assert settings_hooks.walk_hook_triples(doc, "fixture") == {
        ("PostToolUse", "Edit|Write", "a.sh"),
        ("PostToolUse", "Edit|Write", "b.sh"),
    }


@pytest.mark.parametrize("bad", [True, "600", 0, -5, None])
def test_entry_timeout_refuses_a_value_the_harness_would_not_honour(bad):
    with pytest.raises(ValueError):
        settings_hooks.entry_timeout({"command": "x.sh", "timeout": bad}, "fixture")


def test_walk_hook_entries_still_raises_on_a_non_string_command():
    doc = {"hooks": {"PostToolUse": [{"hooks": [{"command": 7}]}]}}
    with pytest.raises(ValueError):
        settings_hooks.walk_hook_entries(doc, "fixture")


def test_parity_module_is_present():
    """Nothing but this row names test_hook_parity.py; it names this module back."""
    path = REPO / "scripts" / "tests" / "test_hook_parity.py"
    assert path.is_file(), "%s is missing" % path

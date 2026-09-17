"""Every registered test-runner hook carries the three properties that make a deleted suite alarm.

The mechanism this pins: each `*-test.sh` hook registered under `$HOME/.claude/scripts/` carries an
ownership sentinel comment, and on finding its suite missing it greps its OWN source for that
sentinel. Present ⇒ this repo owns the hook ⇒ the suite was DELETED and the gate did not run ⇒
`exit 2`. Absent ⇒ a foreign repo ⇒ inert `exit 0`. The ownership proof deliberately requires
nothing from the absent suite, so a deletion cannot conceal itself. **A deleted test must never be
quieter than a failing one.**

Measured 2026-09-04: the class had grown from 9 hooks to 14 while `recast-test.sh` carried neither
the sentinel nor the ownership branch, and two armed hooks had no guard-suite row. Nothing alarmed.

## Why this is a pytest module and not an `/audit` check

An `/audit` check takes a `--scope` and must therefore answer "does this rule apply to that repo?".
Three successive answers to that question were measured WRONG — "does `$scope/settings.json` exist"
false-FAILed a repo registering nothing under the prefix; resolving settings.json as a list then
graded repos that REGISTER the farm without BACKING it (a symlinked settings.json), turning SKIP
into FAIL on three live repos; and the fix for that swallowed an unsafe-basename finding behind its
own gate. Every one of those defects lived in the applicability machinery, none in the parity rule
itself. So the rule is asserted HERE, about THIS repo, where there is no scope to resolve and no
foreign repo to classify. It runs under `/audit --tests`, alongside the guard suite whose rows
property (c) requires.

What that costs, stated plainly: a plain `/audit` no longer covers this, and drift is caught at
`--tests` time rather than on every sweep. That is the price of deleting the question that produced
every measured defect.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
SETTINGS = REPO / "settings.json"
PREFIX = "$HOME/.claude/scripts/"
POPULATION_SUFFIX = "-test.sh"
GUARD_SUITE = REPO / "scripts" / "tests" / "test_hook_suite_guard.sh"

sys.path.insert(0, str(REPO / "scripts" / "lib"))
import settings_hooks  # noqa: E402

# (a) The convention. NOT load-bearing at runtime and labelled so: every armed hook contains the
# sentinel phrase at least twice — this header line, and the grep line below that searches for
# it — so a hook's own `grep -q` succeeds off its grep line alone. Measured: strip this header from
# audit-test.sh and `grep -q` still returns 0. Match the EXACT line, or the check is satisfied by
# any file carrying (b) and asserts nothing.
HEADER_LINE = "# Ownership sentinel (do not remove): dotclaude-test-runner-hook"

# (b) The load-bearing property: the branch that actually decides alarm-vs-inert. One spelling
# exists across all 15 hooks, so a literal anchor is sound and a refactor away from it fails
# LOUDLY, which is the safe direction. The guarding `exit 2` is deliberately NOT asserted
# structurally — that is bash parsing, brittle in the loud direction; the guard suite drives each
# hook and is the behavioural assertion of the exit code.
GREP_NEEDLE = 'grep -q \'dotclaude-test-runner-hook\' "$root/scripts/$(basename "$0")"'

# Named so their ABSENCE alarms — discovery cannot detect absence, and a derivation matching
# nothing reports success loudest of all. Three of these are the gap measured and closed on
# 2026-09-04; the fourth, hook-machinery-test.sh, was added the day it was created, 2026-09-16,
# for the identical reason — a floor member the derivation must never silently lose.
#
# Deliberately 4 of 15, not all 15, because two mechanisms already divide the work: an actual
# DEREGISTRATION is caught loudly on every plain /audit by the sync-docs row, whose hooks table is
# generated from settings.json. What sync-docs cannot see, and what this floor is for, is a
# registration whose FORM drifts until the derivation stops matching it — the population silently
# shrinking while settings.json still lists the hook.
FLOOR = frozenset(
    {
        "recast-test.sh",
        "env-claims-check-test.sh",
        "mutation-anchors-check-test.sh",
        "hook-machinery-test.sh",
    }
)


def _population() -> list[str]:
    """Basenames of every `*-test.sh` hook `settings.json` registers under the farm prefix."""
    doc = json.loads(SETTINGS.read_text())
    names = set()
    for _event, _matcher, command in settings_hooks.walk_hook_triples(
        doc, str(SETTINGS)
    ):
        base = settings_hooks.hook_basename(command, PREFIX)
        if base and base.endswith(POPULATION_SUFFIX):
            names.add(base)
    return sorted(names)


HOOKS = _population()


def _cases_rows() -> set[str]:
    """Column 2 of every non-commented row inside the guard suite's CASES block.

    A whole-file substring would be satisfied by a mention in a comment or in another row's suite
    list. A `#`-prefixed row counts as ABSENT — note the suite's own `while IFS='|' read` loop does
    NOT treat `#` as a comment, so a commented row is one it would run under a junk label; counting
    it absent is the conservative direction.
    """
    text = GUARD_SUITE.read_text()
    block = re.search(r'^CASES="\n(.*?)^"', text, re.S | re.M)
    assert block, "could not locate the CASES block in %s" % GUARD_SUITE
    rows = set()
    for line in block.group(1).splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        parts = line.split("|")
        if len(parts) >= 2:
            rows.add(parts[1].strip())
    return rows


def _has_exact_line(text: str, wanted: str) -> bool:
    return any(line.strip() == wanted for line in text.splitlines())


def _has_grep_line(text: str) -> bool:
    """The needle on a line that is not commented out.

    A substring search over the whole file would accept a commented-out occurrence, which is a
    DISARMED hook reading as armed — the silent-keep-matching direction.
    """
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if GREP_NEEDLE in stripped:
            return True
    return False


# The measured population on 2026-09-04, when the class had grown 9 -> 14 unnoticed. Asserting
# against len(FLOOR) instead would be strictly subsumed by test_floor_members_are_all_registered
# — FLOOR being a subset, no state exists where that passes and a >= len(FLOOR) row fails.
# 15 measured on 2026-09-16.
MEASURED_POPULATION = 15


def test_population_is_non_trivial():
    """A sweep over a shrunken population passes against anything and reads like a clean run."""
    assert len(HOOKS) >= MEASURED_POPULATION, (
        "derived only %d hooks, expected at least the %d measured on 2026-09-16: %s — if a hook "
        "was deregistered deliberately, lower this number in the same commit"
        % (len(HOOKS), MEASURED_POPULATION, HOOKS)
    )


def test_floor_members_are_all_registered():
    """Discovery cannot detect absence; the floor is what makes a deregistration alarm."""
    missing = sorted(FLOOR - set(HOOKS))
    assert not missing, (
        "declared floor members absent from the derivation: %s — if one was deregistered "
        "deliberately, drop it from FLOOR in this file too" % missing
    )


def test_guard_suite_is_present():
    """Nothing else in this repo alarms on this file's deletion."""
    assert GUARD_SUITE.is_file(), (
        "%s is missing — property (c) cannot be judged" % GUARD_SUITE
    )


META_SUITES = (
    REPO / "scripts" / "tests" / "test_hook_argv_refusal.py",
    REPO / "scripts" / "tests" / "test_hook_budget.py",
)


def test_hook_meta_suites_are_present():
    """Glob discovery cannot see a deleted suite, and nothing else names these two.

    test_hook_budget.py names this module in turn. Deleting all three at once is the named residual.
    """
    missing = [p.name for p in META_SUITES if not p.is_file()]
    assert not missing, "hook meta-suites missing: %s" % missing


def test_every_hook_carries_the_header_sentinel():
    bad = [
        n
        for n in HOOKS
        if not _has_exact_line((REPO / "scripts" / n).read_text(), HEADER_LINE)
    ]
    assert not bad, "hooks missing the exact header sentinel line: %s" % bad


def test_every_hook_carries_the_ownership_grep_line():
    """The load-bearing one: without it a hook cannot tell 'we own this' from 'foreign repo'."""
    bad = [n for n in HOOKS if not _has_grep_line((REPO / "scripts" / n).read_text())]
    assert not bad, "hooks with no live ownership grep line: %s" % bad


def test_every_hook_has_a_guard_suite_row():
    """A row proves the guard suite EXERCISES the hook. It does not prove the suite passed."""
    rows = _cases_rows()
    bad = [n for n in HOOKS if n not in rows]
    assert not bad, "hooks with no non-commented CASES row in %s: %s" % (
        GUARD_SUITE.name,
        bad,
    )


def test_guard_suite_helpers_precede_every_selection_wrapper():
    """A helper defined inside a skipped section is `command not found` under a selection — measured
    2026-09-16: six rows went uncounted and the run exited 0. An unselected run defines every helper,
    so only a selected run can see it; this row sees it on every run instead."""
    lines = GUARD_SUITE.read_text().splitlines()
    wrappers = [
        i for i, line in enumerate(lines) if re.match(r"(if )?selected [\w.-]+", line)
    ]
    assert wrappers, "no selection wrapper found in %s" % GUARD_SUITE.name
    assert any(line.strip() == 'selected "$hook" || continue' for line in lines), (
        "the CASES loop no longer filters on the selection"
    )
    late = [
        line.split("(")[0]
        for i, line in enumerate(lines)
        if i > wrappers[0] and re.match(r"[A-Za-z_][A-Za-z0-9_]*\(\) \{", line)
    ]
    assert not late, "helpers defined after the first selection wrapper: %s" % late

#!/usr/bin/env python3
"""Mutation campaign for scripts/lib/settings_hooks.py, driven by scripts/lib/mutate.py.

Deliberately NOT named `test_*`: it mutates a tracked file in place, so pytest must not collect
it. Run it on demand — `./scripts/tests/mutate_settings_hooks_lib.py` — and never while editing
the subject, since the restore would clobber your edits. And never CONCURRENTLY with
`mutate_settings_hooks_check.py`: the two subjects are disjoint files, so their restores do not
clobber each other — the real hazard runs both ways. `scripts/settings-hooks-check.py` IMPORTS
this module's subject, so a live walker mutant makes the checker campaign's rows score as false
catches; and this campaign's own suite runs `test_settings_hooks_check.py`, which runs the checker,
so a live checker mutant corrupts this campaign's rows. Run the two campaigns one at a time.

This subject is the shared walker `settings_hooks.walk_hook_entries` — the single parse
`scripts/settings-hooks-check.py::_registrations` and `scripts/tests/test_hook_budget.py`'s
`_budgeted()` both go through. Its shape-validation rows moved here from
`mutate_settings_hooks_check.py` once the checker stopped keeping its own duplicate parse (see
this module's docstring in `scripts/lib/settings_hooks.py` for why the duplicate existed and what
made converting it safe).

The repo root is derived from __file__, which means invoking this through a symlinked
`~/.claude/scripts/tests/` would resolve into PRODUCTION's tree and mutate that instead. Run it
from the working copy you intend to grade.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "scripts" / "lib"))
import mutate  # noqa: E402

SUBJECT = REPO / "scripts" / "lib" / "settings_hooks.py"
SUITE = [
    sys.executable,
    "-m",
    "pytest",
    "-q",
    "-p",
    "no:cacheprovider",
    str(REPO / "scripts" / "tests" / "test_settings_hooks_check.py"),
    str(REPO / "scripts" / "tests" / "test_hook_budget.py"),
]

MUTATIONS = [
    mutate.Mutation(
        "'hooks' is not an object is silently read as no hooks at all — caught by hooks-not-an-object",
        "        raise ValueError(\"%s: 'hooks' is not an object\" % origin)",
        "        hooks = {}",
    ),
    mutate.Mutation(
        "hooks.%s is not a list is silently skipped — caught by event-not-a-list",
        '            raise ValueError("%s: hooks.%s is not a list" % (origin, event))',
        "            continue",
    ),
    mutate.Mutation(
        "a group under hooks.%s is not an object is silently skipped — caught by group-not-an-object",
        "                raise ValueError(\n"
        '                    "%s: a group under hooks.%s is not an object" % (origin, event)\n'
        "                )",
        "                continue",
    ),
    mutate.Mutation(
        "hooks.%s[].hooks is not a list is silently skipped — caught by entries-not-a-list",
        '                raise ValueError("%s: hooks.%s[].hooks is not a list" % (origin, event))',
        "                continue",
    ),
    mutate.Mutation(
        "an entry under hooks.%s is not an object is silently skipped — caught by entry-not-an-object",
        "                    raise ValueError(\n"
        '                        "%s: an entry under hooks.%s is not an object" % (origin, event)\n'
        "                    )",
        "                    continue",
    ),
    mutate.Mutation(
        "a non-string command is silently skipped — caught by"
        " test_walk_hook_entries_still_raises_on_a_non_string_command",
        "                    raise ValueError(\n"
        '                        "%s: an entry under hooks.%s has a non-string command (%r)"\n'
        "                        % (origin, event, command)\n"
        "                    )",
        "                    continue",
    ),
]


def main() -> int:
    report = mutate.run(SUBJECT, SUITE, MUTATIONS, cwd=str(REPO))
    print(report.text)
    return report.rc


if __name__ == "__main__":
    sys.exit(main())

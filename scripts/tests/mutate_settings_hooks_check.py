#!/usr/bin/env python3
"""Mutation campaign for scripts/settings-hooks-check.py, driven by scripts/lib/mutate.py.

Deliberately NOT named `test_*`: it mutates a tracked file in place, so pytest must not collect
it. Run it on demand — `./scripts/tests/mutate_settings_hooks_check.py` — and never while editing
the subject, since the restore would clobber your edits. And never CONCURRENTLY with
`mutate_settings_hooks_lib.py`: the two subjects are disjoint files, so their restores do not
clobber each other — the real hazard runs both ways. This subject,
`scripts/settings-hooks-check.py`, IMPORTS `scripts/lib/settings_hooks.py`, so a live walker mutant
makes this campaign's rows score as false catches; and the other campaign's suite runs this
subject, so a live mutant here corrupts that campaign's rows. Run the two campaigns one at a time.

This subject is a CHECK, which is the case where mutation testing earns the most. Every failure
mode below turns it into an instrument that passes while a dead gate ships — the precise shape
the check was built to end. A weakened checker produces no error, no diff noise, and a green
promote; nothing but a deliberate mutation reveals it.

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

SUBJECT = REPO / "scripts" / "settings-hooks-check.py"
SUITE = [
    sys.executable,
    "-m",
    "pytest",
    "-q",
    str(REPO / "scripts" / "tests" / "test_settings_hooks_check.py"),
]

MUTATIONS = [
    mutate.Mutation(
        "the comparison direction flips, so machine-local extras fail and dead gates pass",
        "    missing = sorted(committed - runtime)\n    extra = sorted(runtime - committed)",
        "    missing = sorted(runtime - committed)\n    extra = sorted(committed - runtime)",
    ),
    mutate.Mutation(
        "a missing registration stops being a failure — the verdict decouples from the finding",
        'status, rc = ("FAIL", 1) if missing else ("PASS", 0)',
        'status, rc = ("PASS", 0)',
    ),
    mutate.Mutation(
        "identity drops the MATCHER, so a hook rewired to match nothing reads as present",
        '    key = (event, matcher, entry["command"])',
        '    key = (event, None, entry["command"])',
    ),
    mutate.Mutation(
        "identity drops the EVENT, so a hook moved to a different trigger reads as present",
        '    key = (event, matcher, entry["command"])',
        '    key = (None, matcher, entry["command"])',
    ),
    mutate.Mutation(
        "an empty expected set becomes a vacuous PASS instead of an ERROR",
        "    if not committed:",
        "    if False:",
    ),
    mutate.Mutation(
        "unreadable or malformed input reports success rather than refusing to judge",
        '        sys.stdout.write("RESULT: ERROR rc=2\\n")\n        return 2\n\n    if not committed:',
        '        sys.stdout.write("RESULT: PASS rc=0\\n")\n        return 0\n\n    if not committed:',
    ),
    mutate.Mutation(
        "a lowered timeout stops being a failure — caught by the lowered and absent-default rows",
        '    if lowered:\n        status, rc = ("FAIL", 1)',
        '    if False:\n        status, rc = ("FAIL", 1)',
    ),
    mutate.Mutation(
        "the timeout comparison flips direction — caught by the lowered and raised rows",
        "        if runtime_t[t] < committed_t[t]:",
        "        if runtime_t[t] > committed_t[t]:",
    ),
    mutate.Mutation(
        "a duplicate registration counts its LONGEST timeout — caught by the duplicate row",
        "out[key] = min(seconds, out.get(key, seconds))",
        "out[key] = max(seconds, out.get(key, seconds))",
    ),
    mutate.Mutation(
        "an unimportable walker reads as a verdict — caught by the unimportable row",
        '        sys.stdout.write("RESULT: ERROR rc=2\\n")\n        return 2\n\n    try:',
        '        sys.stdout.write("RESULT: PASS rc=0\\n")\n        return 0\n\n    try:',
    ),
]


def main() -> int:
    report = mutate.run(SUBJECT, SUITE, MUTATIONS, cwd=str(REPO))
    print(report.text)
    return report.rc


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Mutation campaign for scripts/settings-hooks-check.py, driven by scripts/lib/mutate.py.

Deliberately NOT named `test_*`: it mutates a tracked file in place, so pytest must not collect
it. Run it on demand — `./scripts/tests/mutate_settings_hooks_check.py` — and never while editing
the subject, since the restore would clobber your edits.

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
        "out.add((event, matcher, command))",
        "out.add((event, None, command))",
    ),
    mutate.Mutation(
        "identity drops the EVENT, so a hook moved to a different trigger reads as present",
        "out.add((event, matcher, command))",
        "out.add((None, matcher, command))",
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
        "a malformed hooks block is silently treated as an empty one, dropping registrations",
        "        if not isinstance(groups, list):\n"
        '            raise ValueError("%s: hooks.%s is not a list" % (origin, event))',
        "        if not isinstance(groups, list):\n            continue",
    ),
]


def main() -> int:
    report = mutate.run(SUBJECT, SUITE, MUTATIONS, cwd=str(REPO))
    print(report.text)
    return report.rc


if __name__ == "__main__":
    sys.exit(main())

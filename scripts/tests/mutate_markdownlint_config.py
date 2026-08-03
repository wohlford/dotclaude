#!/usr/bin/env python3
"""Mutation campaign for .markdownlint-cli2.jsonc, driven by scripts/lib/mutate.py.

Deliberately NOT named `test_*`: it mutates a tracked file in place, so pytest must not collect
it. Run it on demand — `./scripts/tests/mutate_markdownlint_config.py` — and never while editing
the subject, since the restore would clobber your edits.

The subject is a CONFIG, which is what makes the campaign worth having. A rule silently switched
off produces no error, no diff noise, and a green /audit — the exact shape that let a heading
ship with no blank line above it once already. Every row below is a one-token edit that a code
review would wave through.

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

SUBJECT = REPO / ".markdownlint-cli2.jsonc"
SUITE = ["bash", str(REPO / "scripts" / "tests" / "test_markdownlint_config.sh")]

LIVE = '"blanks-around-headings": { "lines_above": 1, "lines_below": -1 }'

MUTATIONS = [
    mutate.Mutation(
        "the rule goes back to OFF — the exact state that shipped a squashed heading",
        LIVE,
        '"blanks-around-headings": false',
    ),
    mutate.Mutation(
        "the enforced side is disabled while the key still LOOKS configured",
        LIVE,
        '"blanks-around-headings": { "lines_above": -1, "lines_below": -1 }',
    ),
    mutate.Mutation(
        "the rule is switched on WHOLESALE, re-flagging the house style it must tolerate",
        LIVE,
        '"blanks-around-headings": true',
    ),
]


def main() -> int:
    report = mutate.run(SUBJECT, SUITE, MUTATIONS, cwd=str(REPO))
    print(report.text)
    return report.rc


if __name__ == "__main__":
    sys.exit(main())

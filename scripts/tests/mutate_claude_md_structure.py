#!/usr/bin/env python3
"""Mutation-test scripts/claude-md-structure.py.

Run on demand: `python3 scripts/tests/mutate_claude_md_structure.py`. Deliberately NOT named
`test_*`, so pytest never collects it — it mutates a tracked file in place (restored in a
`finally`) and takes a couple of minutes.

**Why this campaign exists at all, when the suite is already green.** The tool replaces four
consecutive hand-derivations, two of which returned different WRONG answers that nobody could see
from the output. A green suite over a fresh tool proves only that the tool agrees with itself; what
has to be proven is that the suite would go RED if either historical mistake were reintroduced.
Rows 1 and 2 below are exactly those two mistakes, transcribed into code.

If a row here SURVIVES, the corresponding test is decorative and the measurement it guards is back
to being unverified — which is the state that produced 12-against-10 and 13-against-10 in the
first place.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "scripts" / "lib"))
import mutate  # noqa: E402

SUBJECT = REPO / "scripts" / "claude-md-structure.py"
SUITE = [
    sys.executable,
    "-m",
    "pytest",
    str(REPO / "scripts" / "tests" / "test_claude_md_structure.py"),
    "-q",
]

MUTATIONS = [
    mutate.Mutation(
        "MEASURED FAILURE 1 — a member no longer stops at the heading below it, so the last "
        "member of every group swallows the next `####` and reads several lines too long "
        "(reported 12 against a true 10, in the direction that manufactures work)",
        '        [g["at"] for g in groups]',
        "        []",
    ),
    mutate.Mutation(
        "MEASURED FAILURE 2 — the section is no longer bounded, so the walk runs on into "
        "Package Management and counts its `#### Python (uv)` subheadings as hazard groups "
        "(reported 13 groups / 35 members against a true 10 / 33)",
        "        if SECTION_END_RE.match(lines[j]):",
        "        if False:",
    ),
    mutate.Mutation(
        "the section now ends at its own FIRST group, because a level-4 heading counts as a "
        "boundary — the opposite over-correction to failure 2, and it reports one tiny group",
        r'SECTION_END_RE = re.compile(r"^#{1,3} (?!#)|^#{1,3}$")',
        r'SECTION_END_RE = re.compile(r"^#{1,4} ")',
    ),
    mutate.Mutation(
        "a parser that matched NOTHING reports a clean PASS on a denominator of zero — the "
        "loudest false pass there is",
        "    if not groups or not members:",
        "    if False:",
    ),
    mutate.Mutation(
        "an unreadable file reports a measurement instead of an error",
        "    except OSError as exc:",
        "    except ZeroDivisionError as exc:",
    ),
    mutate.Mutation(
        "the longest member is reported as the SHORTEST, so the line budget can never fire",
        '    longest_member = max(members, key=lambda m: m["lines"])',
        '    longest_member = min(members, key=lambda m: m["lines"])',
    ),
    mutate.Mutation(
        "blank lines are counted inside a member, inflating every length by the gaps",
        '            m["lines"] = sum(1 for line in body[m["at"] : nxt] if line.strip())',
        '            m["lines"] = sum(1 for line in body[m["at"] : nxt])',
    ),
]


def main() -> int:
    baseline = subprocess.run(SUITE, capture_output=True, text=True)
    print(
        f"pre-flight suite: rc={baseline.returncode}  {baseline.stdout.strip()[-60:]}"
    )

    report = mutate.run(SUBJECT, SUITE, MUTATIONS)
    print(report.text)
    return report.rc


if __name__ == "__main__":
    sys.exit(main())

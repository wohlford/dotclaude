#!/usr/bin/env python3
# Script: claude-md-structure.py
# Purpose: Measure CLAUDE.md's verification-hazards section — group sizes, member counts, lengths
# Usage: claude-md-structure.py [--file <path>] [--section <heading prefix>]
"""Measure the structure of CLAUDE.md's verification-hazards section.

`/debrief` steps 1-2 need these numbers EVERY session — group sizes, member counts, the longest
member and the longest heading — because they are what the admission rules are stated in: the
~4-member cap that fires as a SPLIT trigger, and the per-bullet line budget. They were hand-derived
in four consecutive sessions, and **two of those hand-rolls returned different wrong answers**.
That is what this exists to stop; the numbers gate admission decisions, so a wrong one does not
merely waste time, it fires a policy rule that should not fire.

## The two measured failures, which are the two things this must get right

1. **Bullet length, over-reported by a naive split.** `re.split(r'\\n(?=- \\*\\*)', section)` was
   used to check a new bullet against the length cap. It reported **max 12 lines** against a true
   **10**: a group's LAST bullet has no following `- **` to split on, so it swallows the `####`
   heading that follows it and that heading's blank lines. It read in the direction that
   MANUFACTURES work — a healthy bullet would have been "fixed" and a trigger declared fired.
   Hence: a member ends at the next member, **the next heading, or the section end** — whichever
   comes first.

2. **Group count, over-reported by an unbounded walk.** A later hand-roll avoided (1) and instead
   scanned the whole file, counting `#### Python (uv)`, `#### Node.js (NVM)` and `#### System Tools
   (MacPorts)` — which live under Package Management, far below — as hazard groups. It reported
   **13 groups / 35 members** against a true **10 / 33**, and was caught only because the prior
   session's figure happened to still be in context. Hence: the subject is a BOUNDED section, from
   its heading to the next heading of the same level or shallower.

Both are the same lesson from opposite ends — a region has two edges, and each hand-roll got one
of them wrong. Neither is detectable from the output, which is why they are mutation rows.

## What this deliberately does NOT do

**It measures; it does not enforce.** No exit code depends on whether a group is over the cap or a
member over the budget — that is a separate open question, and the entire point of measuring first
is to decide it on numbers nobody hand-derived. `RESULT: FAIL` here would mean the file could not
be measured, not that it was found wanting.

**No default output path.** It prints; it writes nothing. A default destination makes every run a
writer of real state, which this repo has measured twice in opposite directions.

Exit codes: 0 measured, 2 the section could not be found or contains nothing to measure.
Terminal verdict line: `RESULT: <STATUS> rc=<n> groups=<n> members=<n> max_member_lines=<n>
max_heading=<n>`.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

DEFAULT_SECTION = "### Verification hazards"
MEMBER_RE = re.compile(r"^- ")
GROUP_RE = re.compile(r"^#### ")
# Any heading of level 3 or shallower ENDS the section. Level 4 (`#### `) is a group inside it,
# so it must not match — the negative lookahead is what keeps the section from ending at its own
# first group, which would report one group and nothing else.
SECTION_END_RE = re.compile(r"^#{1,3} (?!#)|^#{1,3}$")


class MeasureError(Exception):
    """The section could not be located, or holds nothing to measure."""


def find_section(lines: list[str], section: str) -> tuple[int, int]:
    """Return [start, end) line indices of the section, bounded at the next heading.

    The END is the half every unbounded hand-roll got wrong: without it the walk runs on into
    later sections and counts their `####` subheadings as members of this one.
    """
    start = None
    for i, line in enumerate(lines):
        if line.startswith(section):
            start = i
            break
    if start is None:
        raise MeasureError(f"no section starting {section!r}")

    for j in range(start + 1, len(lines)):
        if SECTION_END_RE.match(lines[j]):
            return start, j
    return start, len(lines)


def measure(lines: list[str], section: str = DEFAULT_SECTION) -> dict:
    """Measure one bounded section into a plain dict of counts."""
    start, end = find_section(lines, section)
    body = lines[start:end]

    groups: list[dict] = []
    for offset, line in enumerate(body):
        if GROUP_RE.match(line):
            groups.append({"heading": line.rstrip("\n"), "at": offset, "members": []})
            continue
        if MEMBER_RE.match(line) and groups:
            groups[-1]["members"].append({"first": line.rstrip("\n"), "at": offset})

    # A member runs to the next member, the next GROUP HEADING, or the section end — whichever
    # comes first. Dropping the heading from that list is failure (1): the last member of every
    # group then absorbs the heading below it and reads several lines too long.
    stops = sorted(
        [g["at"] for g in groups]
        + [m["at"] for g in groups for m in g["members"]]
        + [len(body)]
    )
    for g in groups:
        for m in g["members"]:
            nxt = next(s for s in stops if s > m["at"])
            m["lines"] = sum(1 for line in body[m["at"] : nxt] if line.strip())

    members = [m for g in groups for m in g["members"]]
    if not groups or not members:
        # Zero is the loudest false pass there is: a parser that matched nothing reports a clean
        # sweep on a denominator of zero. It is an ERROR, never a PASS.
        raise MeasureError(
            f"matched {len(groups)} groups and {len(members)} members in "
            f"{section!r} — nothing to measure, which is not a clean result"
        )

    longest_member = max(members, key=lambda m: m["lines"])
    longest_heading = max(groups, key=lambda g: len(g["heading"]))
    return {
        "section_heading": body[0].rstrip("\n"),
        "start": start + 1,
        "end": end,
        "section_lines": end - start,
        "total_lines": len(lines),
        "groups": groups,
        "sizes": [len(g["members"]) for g in groups],
        "members": len(members),
        "longest_member": longest_member,
        "longest_heading": longest_heading,
    }


def render(m: dict, cap: int) -> str:
    """Format the measurement, with the sizes in the form the admission rule is stated in."""
    pct = round(100 * m["section_lines"] / m["total_lines"])
    at_cap = [g for g in m["groups"] if len(g["members"]) >= cap]
    out = [
        f"section:  {m['section_heading']}",
        f"bounds:   lines {m['start']}-{m['end']} "
        f"({m['section_lines']} of {m['total_lines']}, {pct}%)",
        f"groups:   {len(m['groups'])}",
        f"members:  {m['members']}",
        f"sizes:    {'/'.join(str(n) for n in m['sizes'])}",
        f"at cap:   {len(at_cap)} group(s) with >= {cap} members"
        " — the cap is a SPLIT trigger, not a rejection",
        f"longest member:  {m['longest_member']['lines']} non-blank lines"
        f"  {m['longest_member']['first'][:64]}",
        f"longest heading: {len(m['longest_heading']['heading'])} chars"
        f"  {m['longest_heading']['heading'][:64]}",
        "",
    ]
    for g in m["groups"]:
        out.append(f"  [{len(g['members'])}] {g['heading']}")
        for mem in g["members"]:
            out.append(f"        {mem['lines']:2d}  {mem['first'][:72]}")
    return "\n".join(out)


def main(argv: list[str]) -> int:
    """Measure the section and print the numbers, or explain why it could not be measured."""
    parser = argparse.ArgumentParser(add_help=True, description=__doc__)
    parser.add_argument(
        "--file", default=None, help="defaults to CLAUDE.md beside this repo"
    )
    parser.add_argument("--section", default=DEFAULT_SECTION)
    parser.add_argument("--cap", type=int, default=4)
    args = parser.parse_args(argv)

    path = Path(args.file) if args.file else Path.cwd() / "CLAUDE.md"
    try:
        lines = path.read_text().split("\n")
    except OSError as exc:
        print(f"cannot read {path}: {exc}", file=sys.stderr)
        print("RESULT: ERROR rc=2 groups=0 members=0 max_member_lines=0 max_heading=0")
        return 2

    try:
        m = measure(lines, args.section)
    except MeasureError as exc:
        print(f"cannot measure: {exc}", file=sys.stderr)
        print("RESULT: ERROR rc=2 groups=0 members=0 max_member_lines=0 max_heading=0")
        return 2

    print(render(m, args.cap))
    print(
        f"\nRESULT: PASS rc=0 groups={len(m['groups'])} members={m['members']} "
        f"max_member_lines={m['longest_member']['lines']} "
        f"max_heading={len(m['longest_heading']['heading'])}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

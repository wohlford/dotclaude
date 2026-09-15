#!/usr/bin/env python3
"""Mutation campaign for skills/debrief/backlog.py, driven by scripts/lib/mutate.py.

Deliberately NOT named `test_*`: it mutates a tracked file in place, so pytest must not collect
it. Run it on demand — `./scripts/tests/mutate_backlog.py` — and never while editing the subject,
since the restore would clobber your edits.

## Why this file exists at all

`backlog.py` is the sole permitted writer to a private file that is untracked, has no other copy
and no git history — the highest-consequence subject in this repo — and until 2026-09-08 it had
**no committed campaign**, while nineteen lesser scripts did. The `amend_head` campaign run on
2026-09-04 was ad-hoc and evaporated with its session, so "amend_head's guards are still covered"
was a claim no artifact could settle. That gap was found by a plan review asking where the
PRESERVE instrument was, not by anything the suite reported: a suite with 246 green rows looks
identical whether or not a campaign backs it.

## What the rows below are FOR

Every mutation here disables ONE refusal that `save()` or a mutating operation makes, and requires
`skills/debrief/tests/test_backlog.py` to notice. They are deliberately concentrated on the
*postcondition* machinery rather than on the operations, because that machinery is what stands
between a wrong edit and an unrecoverable file — and because a postcondition is precisely the code
whose deletion changes no observable outcome when everything else is correct. A survivor here is
evidence about the SUITE, not about the subject: strengthen the suite row, never reword the
mutation.

`mutate.py` refuses any anchor that does not appear EXACTLY ONCE in the file. Two consequences
worth knowing before editing this list: `if _is_head(ln):` is deliberately absent because it occurs
twice, and any new message text added to the subject must not duplicate a phrase anchored here —
`"unrecognized entry head, refusing to guess: "` already occurs three times and is unusable as an
anchor for that reason.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "scripts" / "lib"))
import mutate  # noqa: E402

SUBJECT = REPO / "skills" / "debrief" / "backlog.py"
SUITE = [
    sys.executable,
    "-m",
    "pytest",
    "-q",
    str(REPO / "skills" / "debrief" / "tests" / "test_backlog.py"),
]

MUTATIONS = [
    # ---- selector: the wrong entry is worse than no entry
    mutate.Mutation(
        "ambiguous-needle-allowed: `_find_open` stops refusing a needle that matches SEVERAL open "
        "entries and silently takes the first. Measured on the real file: the needle 'flake' "
        "matches two open entries, so this is the shape that edits the wrong one",
        "        if len(hits) > 1:",
        "        if False:",
    ),
    mutate.Mutation(
        "missing-entry-allowed: `_find_open` stops raising when NOTHING matches, so an operation "
        "addressed to a typo'd needle proceeds against whatever `hits[0]` then resolves to",
        "        if not hits:",
        "        if False:",
    ),
    # ---- save()'s multiset contract: the edit did what it declared, and nothing else
    mutate.Mutation(
        "lost-check-off: `save` no longer verifies that the lines which VANISHED are the ones the "
        "operation declared it would remove, so an edit that deletes an unrelated entry writes "
        "cleanly",
        "        if lost != self._expected_lost:",
        "        if False:",
    ),
    mutate.Mutation(
        "gained-check-off: `save` no longer verifies that the lines which APPEARED are the ones "
        "declared, so an edit that duplicates or fabricates an entry writes cleanly",
        "        if gained != self._expected_gained:",
        "        if False:",
    ),
    # ---- save()'s placement contract: a note must land in the entry it was addressed to
    mutate.Mutation(
        "placement-check-off: `save` stops requiring a note's needle to match EXACTLY ONE entry "
        "after the edit, so a note can be attached against an ambiguous or vanished anchor",
        "            if len(spans) != 1:",
        "            if False:",
    ),
    mutate.Mutation(
        "note-outside-allowed: `save` stops checking that the note actually landed INSIDE the "
        "entry it names. This is the postcondition that caught a real defect: a scan for an "
        "end-of-entry sentinel ran past its target and reattached a note to the wrong entry",
        "            if placement.line not in new_lines[s:e]:",
        "            if False:",
    ),
    # ---- save()'s section invariant: open above the heading, closed below it
    mutate.Mutation(
        "section-invariant-off: `save` stops rejecting a document with closed entries above the "
        "Closed heading or open ones below it, so a botched close silently strands an entry where "
        "the next step-0 read will never see it",
        "        if strays_open or strays_closed:",
        "        if False:",
    ),
    # ---- amend_head's refusals (PRESERVE: `retier` must not weaken these)
    mutate.Mutation(
        "tier-overwrite-allowed: `amend_head` stops refusing a head that ALREADY carries a tier, "
        "so an insert-what-is-missing operation silently becomes an overwrite-a-judgement one. "
        "This is exactly the boundary `retier` exists so that `amend_head` need not cross",
        "            if _TIER_PREFIX_RE.match(rest):",
        "            if False:",
    ),
    mutate.Mutation(
        "no-bold-guard-off: `amend_head` stops requiring the headline to open with '**' before "
        "inserting a tier inside it. Measured: 23 of 91 open entries carry a plain 'TIER — ' head, "
        "and without this guard each would be rewritten to '**MEDIUM — DIUM — …', eating two "
        "characters of real text",
        '            if not rest.startswith("**"):',
        "            if False:",
    ),
    mutate.Mutation(
        "subsequence-check-off: `amend_head` stops requiring the new head to be a SUPERSEQUENCE of "
        "the old one, so an amend that rewrites or drops existing headline text passes as an "
        "insertion",
        "        if not _is_subsequence(old, new):",
        "        if False:",
    ),
]


def main() -> int:
    report_path = sys.argv[1] if len(sys.argv) > 1 else None
    report = mutate.run(SUBJECT, SUITE, MUTATIONS, cwd=REPO, report_path=report_path)
    print(report.text)
    return report.rc


if __name__ == "__main__":
    sys.exit(main())

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
    # ---- the atomic write. Every row below is UNOBSERVABLE in the written file: the bytes are
    # identical whichever way they got there, so none of these can be caught by reading the
    # result. They are caught by instrumenting the SYSCALLS, which is why those rows exist.
    mutate.Mutation(
        "chmod-dropped: the temp file's mode is no longer set to the target's, so every save "
        "silently retightens the file from its own mode to `mkstemp`'s 0600. Invisible in the "
        "file's CONTENT, and invisible to a fixture created with `write_text` under a strict "
        "umask, where 0600 is what the fixture already had",
        "            os.chmod(tmp, mode)",
        "            pass",
    ),
    mutate.Mutation(
        "temp-outside-dir: the temp file is created in the system temp directory instead of the "
        "target's own. `os.replace` is atomic only WITHIN one filesystem, so this makes the "
        "rename a cross-device copy and silently gives up the guarantee the whole change exists "
        "for — while passing every test that reads the resulting file",
        '            dir=directory, prefix=".backlog-", suffix=".tmp"',
        '            prefix=".backlog-", suffix=".tmp"',
    ),
    mutate.Mutation(
        "no-fsync-before-replace: the temp's contents are never flushed to disk before it is "
        "renamed over the target, so a crash can leave the rename durable and its CONTENT not",
        "                os.fsync(handle.fileno())",
        "                pass",
    ),
    mutate.Mutation(
        "missing-ok-dropped: the temp cleanup stops tolerating an already-renamed temp. After a "
        "SUCCESSFUL `os.replace` that name is gone, so this raises `FileNotFoundError` on every "
        "good save — a mutation that breaks the success path, not an edge case",
        "            tmp.unlink(missing_ok=True)",
        "            tmp.unlink()",
    ),
    mutate.Mutation(
        "snapshot-skipped: no snapshot is taken, so the one thing that could undo a logically "
        "wrong but successfully written edit is gone. `Report.snapshot` still reports a path",
        "            snapshot = self._write_snapshot(mode)",
        "            snapshot = None",
    ),
    mutate.Mutation(
        "snapshot-failure-aborts: a snapshot that cannot be written now kills the save instead of "
        "warning. This is the WRONG direction — blocking a legitimate edit because a secondary "
        "artifact failed is worse than the risk the artifact covers",
        '                f"proceeds, but there is nothing to restore from",\n'
        "                file=sys.stderr,\n"
        "            )\n"
        "            return None",
        "            raise",
    ),
    mutate.Mutation(
        "dirfsync-propagates: a failure to fsync the DIRECTORY after the replace is raised "
        "instead of warned. The edit has already landed at that point, so raising invites a "
        "caller retry that re-applies it — `append_note` would double-append",
        "            print(\n"
        '                f"backlog: wrote {self.path} but could not fsync {directory}: {exc}",\n'
        "                file=sys.stderr,\n"
        "            )",
        "            raise",
    ),
    mutate.Mutation(
        "symlink-guard-off: `save` stops refusing a symlinked target. `write_text` follows a "
        "symlink while `os.replace` replaces the LINK itself, so this change would silently "
        "alter what the write means for such a path",
        "        if self.path.is_symlink():",
        "        if False:",
    ),
    # ---- retier: changing a JUDGEMENT, where amend_head only inserts a missing FACT
    mutate.Mutation(
        "tier-allowlist-off: `retier` stops validating its tiers at the METHOD level. argparse "
        "`choices` still guards the command line, so this is invisible from the CLI and open to "
        "every library caller — which is exactly why the check is not left to argparse",
        "            if value not in _TIER_TOKENS:",
        "            if False:",
    ),
    mutate.Mutation(
        "from-equals-to-ok: `retier` accepts `--from X --to X`, writing a reason line announcing "
        "a change of grade that did not happen",
        "        if from_tier == to_tier:",
        "        if False:",
    ),
    mutate.Mutation(
        "cas-off: the compare-and-swap stops checking that the head carries what `--from` claims, "
        "so a stale expectation silently overwrites whatever is actually there — and a REPEATED "
        "run re-applies instead of refusing",
        "        if current != from_tier:",
        "        if False:",
    ),
    mutate.Mutation(
        "no-tier-guard-off: `retier` stops refusing a head with no bold tier to change. 23 of the "
        "91 live open entries are that shape",
        "        if located is None:",
        "        if False:",
    ),
    mutate.Mutation(
        "dated-head-guard-off: `retier` stops refusing a head that is not a dated entry head",
        '                f"head is not a dated entry head, refusing to retier it: {old[:90]!r}"',
        '                f"unused: {old[:90]!r}" if False else "x"',
    ),
    mutate.Mutation(
        "note-keyed-on-needle: the reason line is keyed on the CALLER'S NEEDLE instead of the new "
        "head. This is the composition bug the design exists to dissolve: a needle overlapping the "
        "tier text no longer matches once the head is rewritten, so a CORRECT retier fails. "
        "Reordering does not fix it — noting first raises ShapeViolation instead",
        '        self.append_note(new, f"  - {date} — {from_tier} → {to_tier}: {reason}")',
        '        self.append_note(needle, f"  - {date} — {from_tier} → {to_tier}: {reason}")',
    ),
    mutate.Mutation(
        "structural-to-replace: the structural splice becomes a bare-token `str.replace`. The "
        "DELIMITED form would be genuinely equivalent (a promoted stamp cannot contain `*`), but "
        "the BARE token is not: a stamp reason or headline carrying a tier word is legal and would "
        "be rewritten instead of the tier",
        "        new = _splice(old, span_start, span_end, to_tier)",
        "        new = old.replace(from_tier, to_tier, 1)",
    ),
    # The three postcondition clauses. A postcondition changes NO observable outcome while the
    # construction is correct — all four of `amend_head`'s survived its campaign for exactly that
    # reason. These are catchable only because the suite carries REACHABILITY rows that
    # monkeypatch `_splice` to emit a deliberately wrong head. If one of these ever survives, the
    # reachability row is what regressed, not the postcondition.
    mutate.Mutation(
        "postcond-1-off: the clause forbidding head text to change OUTSIDE the tier token no "
        "longer raises",
        '            raise BacklogError("retier changed head text outside the tier token")',
        "            pass",
    ),
    mutate.Mutation(
        "postcond-2-off: the clause requiring the span to EQUAL the requested tier no longer "
        "raises. This is the one the obvious two-clause postcondition omits, and without it "
        "`**MEDIUN — `, `**medium — ` and `**       — ` all pass while destroying the tier",
        '            raise BacklogError("retier did not write the requested tier into the token")',
        "            pass",
    ),
    mutate.Mutation(
        "postcond-3-off: the independent re-derivation from `new` stops being consulted. It is "
        "the only clause carrying no offset from the construction, so a MISLOCATED span — which "
        "fools clauses 1 and 2 identically — is caught here or nowhere",
        '        if rechecked is None or rechecked["tier"] != to_tier:',
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

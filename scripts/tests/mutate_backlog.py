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
worth knowing before editing this list: anchors are SUBSTRING counts over the whole file, so
indentation never disambiguates two lines (a bare `return 1`, which occurs several times in
`main()`, is absent for that reason), and any new message text added to the subject must not
duplicate a phrase anchored here — `"unrecognized entry head, refusing to guess: "` already occurs
three times and is unusable as an anchor for that reason.
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
        "inserting a tier inside it. The fixture is a plain head with no tier; without this guard "
        "it would be rewritten to '**MEDIUM — DIUM — …', eating two characters of real text",
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
        "            print(\n"
        '                f"backlog: no snapshot written to {snapshot} ({exc}) — the edit itself "\n'
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
        "no-tier-guard-off: `retier` stops refusing a head with no bold tier to change: the "
        "legacy no-tier shape and the parenthetical shape (3 of 230 live entries are "
        "parenthetical)",
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
    # `unpromote`. Its postcondition clauses are catchable for the same reason as `retier`'s: the
    # suite monkeypatches `_strip_stamp` to emit a deliberately wrong head. There is no
    # "stamp-count check off" row on purpose: the anchored-stamp count is a suite-level assertion
    # only, so it has no subject line to mutate.
    mutate.Mutation(
        "unpromote-no-stamp-check-off: `unpromote` stops refusing a head that carries no promoted "
        "stamp. With the guard gone an unstamped head strips to itself and the later clauses all "
        "hold, so the tool would append a withdrawal record for a promotion that never existed",
        "        if not removed:",
        "        if False:",
    ),
    mutate.Mutation(
        "unpromote-undated-result-check-off: the refusal for a stripped result that is no longer "
        "a dated entry head stops firing. A message-precision row: without it the next line "
        "dereferences None and raises AttributeError instead of a BacklogError",
        "        if check is None:",
        "        if False:",
    ),
    mutate.Mutation(
        "unpromote-multi-stamp-check-off: message-precision row; without the refusal for a head "
        "still carrying a second promoted stamp after the strip, a doubly stamped head is "
        "refused with a different message",
        '        if check.start("rest") != len(check["prefix"]):',
        "        if False:",
    ),
    mutate.Mutation(
        "unpromote-construction-check-off: the clause requiring the old head to equal prefix, "
        "removed stamp and rest stops raising, so an over-wide strip could eat head text outside "
        "the stamp and still be written",
        '        if old != check["prefix"] + removed + check["rest"]:',
        "        if False:",
    ),
    mutate.Mutation(
        "unpromote-strip-too-wide: the strip also eats the head's last character. Every "
        "successful unpromote then trips the construction clause, so the whole success path goes "
        "red; the mutated line still contains its anchor, so only a by-content restore sees it",
        "        new = _strip_stamp(match)",
        "        new = _strip_stamp(match)[:-1]",
    ),
    mutate.Mutation(
        "unpromote-append-keyed-on-needle: the withdrawal record is keyed on the CALLER'S "
        "NEEDLE instead of the rewritten head. A needle that matched only inside the stamp no "
        "longer matches once the stamp is gone, so a CORRECT unpromote fails",
        '        self.append_note(new, composed + "\\n" + note)',
        '        self.append_note(needle, composed + "\\n" + note)',
    ),
    mutate.Mutation(
        "unpromote-cli-branch-dropped: the `unpromote` command stops being dispatched in "
        "`main()` and falls through to the `amend_head` branch, which dies on the missing "
        "`tier` attribute",
        '        elif args.command == "unpromote":',
        '        elif args.command == "unpromote-disabled":',
    ),
    # ---- repair: relocating stray blocks must move exactly the blocks, in order, and only then
    mutate.Mutation(
        "repair-block-slice-truncated: `repair` lifts only the first line of each stray closed "
        "block, so a multi-line entry is split and its continuation lines are left behind",
        "closed_blocks = [lines[s : _trimmed_end(lines, s, e)] for s, e in moved_x]",
        "closed_blocks = [lines[s : s + 1] for s, e in moved_x]",
    ),
    mutate.Mutation(
        "repair-block-check-off: `repair` stops comparing the staged block sequence with the one "
        "it expected, so a relocation that reorders, drops or duplicates a block writes cleanly",
        "        if _block_sequence(staged) != expected:",
        "        if False:",
    ),
    mutate.Mutation(
        "repair-order-reversed: `repair` relocates the stray closed blocks in reverse order, so "
        "the history of the Closed section is silently inverted",
        "closed_blocks = [lines[s : _trimmed_end(lines, s, e)] for s, e in moved_x]",
        "closed_blocks = [lines[s : _trimmed_end(lines, s, e)] for s, e in reversed(moved_x)]",
    ),
    mutate.Mutation(
        "repair-bottom-insert-closed: `repair` appends relocated closed blocks at the very end of "
        "the Closed section instead of at its insertion point, past any trailing prose",
        "    at = _insert_at(rest, rest.index(CLOSED_HEADING))",
        "    at = _section_end(rest, rest.index(CLOSED_HEADING))",
    ),
    mutate.Mutation(
        "repair-bottom-insert-open: `repair` appends relocated open blocks at the very end of the "
        "Open section instead of at its insertion point, past any trailing prose",
        "    at = _insert_at(rest, rest.index(OPEN_HEADING))",
        "    at = _section_end(rest, rest.index(OPEN_HEADING))",
    ),
    mutate.Mutation(
        "repair-skips-below: `repair` stops collecting stray open blocks that sit BELOW the "
        "Closed heading, so the commonest misplacement is reported repaired and is not",
        "        moved_o = _stray_spans(self.lines, closed_at, below=True)",
        "        moved_o = []",
    ),
    mutate.Mutation(
        "repair-malformed-off: `repair` stops refusing a file holding a malformed checkbox line, "
        "so it relocates blocks around a line it cannot classify",
        "        if scan.malformed:",
        "        if False:",
    ),
    mutate.Mutation(
        "repair-heading-check-off: `repair` stops refusing a file with a duplicated or reversed "
        "section heading, so it picks a section to repair in a file whose layout is unknown",
        "        if scan.extra_headings or scan.order_reversed:",
        "        if False:",
    ),
    mutate.Mutation(
        "repair-always-saves: `main()` stops skipping `save()` when `repair` moved nothing, so a "
        "clean file is rewritten and gains a snapshot. DEVIATION from the spec's table, which "
        "anchored this on `if not moved_x and not moved_o:` inside `repair()`: that guard only "
        "returns early, and without it `repair()` stages an unchanged document and returns [], "
        "so that mutant is equivalent. Anchored on the `main()` guard instead, per the controller",
        '        if args.command == "repair" and not moved:',
        "        if False:",
    ),
    # ---- status: read-only, and its exit code is the verdict
    mutate.Mutation(
        "status-rc-always-0: `status_report` exits 0 whatever it found, so a file with strays or "
        "malformed lines reads as clean to any caller that checks the exit code",
        "    rc = 0 if clean else 1",
        "    rc = 0",
    ),
    mutate.Mutation(
        "status-writes: `status` constructs a `Backlog` and saves the unchanged file, breaking the "
        "read-only promise: the file is rewritten and a snapshot directory appears",
        "        text = Path(path).read_text()",
        "        text = Path(path).read_text(); Backlog(path).save()",
    ),
    mutate.Mutation(
        "status-unicode-uncaught: `status` stops catching UnicodeDecodeError, so a file that is "
        "not valid UTF-8 crashes with a traceback instead of reporting an unreadable file",
        "    except (OSError, UnicodeDecodeError) as exc:",
        "    except OSError as exc:",
    ),
    mutate.Mutation(
        "status-open-count-head-re: `status` counts only entries whose head matches HEAD_RE, so "
        "an open entry with a malformed or undated head vanishes from the count",
        'open_count = sum(1 for ln in lines[:boundary] if ln.startswith("- [ ]"))',
        'open_count = sum(1 for ln in lines[:boundary] if HEAD_RE.match(ln) and ln.startswith("- [ ]"))',
    ),
    # ---- malformed checkbox lines: refused on every write path that could swallow them
    mutate.Mutation(
        "malformed-predicate-widened: the checkbox-shaped predicate matches any text inside the "
        "brackets, so a body line such as `- [see below]` is classified as a malformed head",
        '_CHECKBOX_SHAPED_RE = re.compile(r"^- \\[.?\\]")',
        '_CHECKBOX_SHAPED_RE = re.compile(r"^- \\[.*\\]")',
    ),
    mutate.Mutation(
        "note-malformed-allowed: `append_note` stops refusing a note whose first line is a "
        "malformed checkbox head, so the note is written as a body line that reads as an entry",
        "            if _is_malformed_head(ln):",
        "            if False:",
    ),
    mutate.Mutation(
        "add-malformed-allowed: `add` stops refusing an entry block containing a malformed "
        "checkbox line, so it lands inside the open section as a phantom entry",
        "            if _is_malformed_head(line):",
        "            if False:",
    ),
    # ---- head collisions in both directions, and the blank line above a heading after repair
    mutate.Mutation(
        "collision-one-way: `_require_addressable` refuses only a new head contained in another "
        "open head, so a new head that CONTAINS another open head is accepted and leaves that "
        "other entry permanently ambiguous",
        "and (new in staged[s] or staged[s] in new)",
        "and new in staged[s]",
    ),
    mutate.Mutation(
        "relocate-drops-heading-blank: `_relocate` stops restoring the blank line before a "
        "heading when the lifted block carried it away, so an open entry abuts `## Closed`",
        'if ln.startswith("## ") and i - 1 in dropped and rest and rest[-1].strip():',
        "if False:",
    ),
    # ---- the repair hint
    mutate.Mutation(
        "hint-unconditional: the repair hint is printed after every non-repair command, even when "
        "the file has no strays, so the operator is told to repair a healthy file",
        '        if args.command != "repair" and _has_strays(backlog._original):',
        '        if args.command != "repair":',
    ),
    mutate.Mutation(
        "hint-never: the repair hint is never printed, so strays are never surfaced to an "
        "operator who has not run `status`",
        '        if args.command != "repair" and _has_strays(backlog._original):',
        "        if False:",
    ),
    # ---- head forms: tier located in bold and plain heads, parenthetical tiers refused
    mutate.Mutation(
        "tier-regex-bold-only: the tier regex stops accepting a plain `TIER — ` head, so a plain "
        "head reads as having no tier",
        r'rf"^(?:\*\*)?(?P<tier>',
        r'rf"^\*\*(?P<tier>',
    ),
    mutate.Mutation(
        "paren-tier-guard-off: `amend_head` stops refusing a head whose tier is parenthetical, "
        "so it inserts a second tier beside the existing one",
        "            if _PAREN_TIER_RE.search(rest):",
        "            if False:",
    ),
    mutate.Mutation(
        "retier-paren-branch-true: `retier` treats every head as parenthetical, so an ordinary "
        "head is refused as one",
        "            if paren:",
        "            if True:",
    ),
    mutate.Mutation(
        "retier-paren-branch-false: `retier` never treats a head as parenthetical, so a "
        "parenthetical tier is rewritten as though it were a bold one",
        "            if paren:",
        "            if False:",
    ),
    mutate.Mutation(
        "tier-span-early: the located tier span starts two characters early, so a retier eats "
        "the characters before the tier",
        '    return base + located.start("tier"), base + located.end("tier")',
        '    return base + located.start("tier") - 2, base + located.end("tier")',
    ),
    mutate.Mutation(
        "lead-clause-off: `retier` stops checking that the text before the tier is unchanged "
        "after the rewrite, so a retier that disturbs the head's lead passes",
        '        if check["rest"][: rechecked.start("tier")] != lead:',
        "        if False:",
    ),
    # ---- calendar-date validation in the library
    mutate.Mutation(
        "date-promote-off: `promote` stops validating its date, so a malformed date is stamped "
        "into the entry",
        '        _require_date(date, "promote")',
        "        pass",
    ),
    mutate.Mutation(
        "date-unpromote-off: `unpromote` stops validating its date, so a malformed date is "
        "stamped into the withdrawal record",
        '        _require_date(date, "unpromote")',
        "        pass",
    ),
    mutate.Mutation(
        "date-retier-off: `retier` stops validating its date, so a malformed date is written "
        "into the retier stamp",
        '        _require_date(date, "retier")',
        "        pass",
    ),
    mutate.Mutation(
        "date-amend-off: `amend_head` stops validating an optional date when one is given, so a "
        "malformed date is written into the head",
        '        _require_date(date, "amend-head", optional=True)',
        "        pass",
    ),
    mutate.Mutation(
        "date-add-off: `add` stops validating the head date of a new entry, so an entry with an "
        "impossible date is appended",
        '        _require_date(lines[0][6:16], "add", label="head date")',
        "        pass",
    ),
    mutate.Mutation(
        "date-optional-truthiness: an optional date is skipped when merely falsy, so an empty "
        "string passes as 'not supplied' instead of being refused",
        "    if optional and value is None:",
        "    if optional and not value:",
    ),
    mutate.Mutation(
        "date-calendar-off: the date check stops asking the calendar, so a well-shaped but "
        "impossible date such as 2026-02-30 is accepted",
        "        datetime.date.fromisoformat(value)",
        "        pass",
    ),
    mutate.Mutation(
        "date-shape-off: the date check drops the strict YYYY-MM-DD shape test and keeps only the "
        "type test. Observable only on Python 3.12 and later: on 3.10 `fromisoformat` alone "
        "rejects `20261103` and `2026-W44-2`, so the mutant is equivalent there",
        "    if not isinstance(value, str) or not _DATE_RE.fullmatch(value):",
        "    if not isinstance(value, str):",
    ),
    # ---- collisions: a rewritten head must not become indistinguishable from another entry
    mutate.Mutation(
        "collision-unpromote-off: `unpromote` stops checking that the rewritten head is still "
        "addressable, so it can produce an entry no needle selects uniquely",
        '        self._require_addressable(staged, start, new, "unpromote")',
        "        pass",
    ),
    mutate.Mutation(
        "collision-retier-off: `retier` stops checking that the rewritten head is still "
        "addressable, so it can produce an entry no needle selects uniquely",
        '        self._require_addressable(staged, start, new, "retier")',
        "        pass",
    ),
]


def main() -> int:
    report_path = sys.argv[1] if len(sys.argv) > 1 else None
    report = mutate.run(SUBJECT, SUITE, MUTATIONS, cwd=REPO, report_path=report_path)
    print(report.text)
    return report.rc


if __name__ == "__main__":
    sys.exit(main())

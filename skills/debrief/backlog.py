#!/usr/bin/env python3
"""backlog — edit BACKLOG.md with the shape postcondition built in.

`/debrief` step 0 rewrites entries (stamp a promotion, append evidence, close one out) and step 5
appends new ones. Before this module, each session hand-wrote a script to do that. One of them
corrupted the file: it found an entry by its first line, then scanned forward for a `→ [[link]]`
sentinel to find its last — but that sentinel sits inline at the end of a prose line, so the scan
ran past its target and moved three entries, reattaching a note to the wrong one. Every assertion
in that script passed, because all of them constrained where the edit STARTED and none constrained
how far it REACHED.

So the shape check is not a helper the caller may remember to call — it is the only path to disk.
`save()` is the sole writer, and it refuses unless the edit's actual effect on the document equals
what the staged operations declared:

  * the multiset of non-blank lines LOST is exactly what was declared lost;
  * the multiset GAINED is exactly what was declared gained (so an edit that reaches too far
    shows up as an undeclared loss, whatever the locator believed);
  * every appended note sits INSIDE the derived block of the entry it was addressed to — the
    multiset alone cannot see *where* a line landed, which is the corrupting script's bug exactly;
  * the section invariant holds: no `- [x]` above `## Closed`, no `- [ ]` below it.

Entry spans are DERIVED, never sentinel-scanned: an entry runs from its `- [ ]`/`- [x]` line to the
next such line or the next `## ` heading. Anchors must sit at column 0 — the real file contains
entries that quote other entries at an indent, and treating one of those as a head would split an
entry in half.

Usage:
  backlog.py --path PATH add                      < entry.md
  backlog.py --path PATH append   NEEDLE          < note.md
  backlog.py --path PATH close    NEEDLE          < note.md
  backlog.py --path PATH promote  NEEDLE --date YYYY-MM-DD [--reason TEXT]

Those four cover every place `/debrief` mutates the file: step 5's new deferral (`add`) and step
0's three dispositions — keep leaves the entry alone, `promote` stamps it, `close` ticks and moves
it, and `append` records evidence on any of them.

NEEDLE is a substring that must match exactly one OPEN entry's head line; matching none or several
is an error, never a guess. Notes are read from stdin verbatim, indentation included.

Exit codes:
  0  the edit landed and every postcondition held
  1  the edit was refused (entry not found or ambiguous, bad note, shape violation)
  2  usage error
"""

from __future__ import annotations

import argparse
import collections
import re
import sys
from dataclasses import dataclass
from pathlib import Path

OPEN_HEADING = "## Open"
CLOSED_HEADING = "## Closed"
HEAD_RE = re.compile(r"^- \[[ x]\] ")
# A head is `- [ ] YYYY-MM-DD — rest`, optionally already carrying a `*promoted …* — ` stamp.
# The stamp group is anchored right after the date so that prose merely *mentioning* promotion
# later in the headline is not mistaken for one — the real file contains exactly that.
STAMPED_HEAD_RE = re.compile(
    r"^(?P<prefix>- \[[ x]\] \d{4}-\d{2}-\d{2} — )"
    r"(?:\*promoted [^*]*\* — )?"
    r"(?P<rest>.*)$"
)


class BacklogError(Exception):
    """Base class for every refusal this module raises."""


class EntryNotFound(BacklogError):
    """No open entry's head line contains the needle."""


class AmbiguousEntry(BacklogError):
    """More than one open entry's head line contains the needle."""


class InvalidNote(BacklogError):
    """A note would restructure the document rather than annotate an entry."""


class ShapeViolation(BacklogError):
    """The edit's actual effect on the document differs from what was declared."""


@dataclass(frozen=True)
class Report:
    """What the document looked like after a successful save."""

    path: Path
    open_count: int
    closed_count: int
    lines_gained: int
    lines_lost: int


@dataclass
class _Placement:
    """A staged claim that `line` must end up inside the entry matching `needle`."""

    needle: str
    line: str


def _is_head(line: str) -> bool:
    """Report whether `line` opens an entry (column 0 only — an indent means prose)."""
    return bool(HEAD_RE.match(line))


def _is_heading(line: str) -> bool:
    """Report whether `line` is a section heading."""
    return line.startswith("## ")


def entry_blocks(lines: list[str]) -> list[tuple[int, int]]:
    """Derive each entry's span.

    Args:
        lines: The document, split on newlines.

    Returns:
        `(start, end)` index pairs, one per entry, `end` exclusive. An entry ends at the next
        entry head or the next `## ` heading — never at a content sentinel, which is what let an
        earlier hand-written script run past its target.
    """
    anchors = [i for i, ln in enumerate(lines) if _is_head(ln) or _is_heading(ln)]
    spans = []
    for pos, i in enumerate(anchors):
        if not _is_head(lines[i]):
            continue
        end = anchors[pos + 1] if pos + 1 < len(anchors) else len(lines)
        spans.append((i, end))
    return spans


def _nonblank(text: str) -> collections.Counter[str]:
    """Count the document's non-blank lines, which is the multiset the shape check compares."""
    return collections.Counter(ln for ln in text.split("\n") if ln.strip())


class Backlog:
    """A staged batch of edits to one BACKLOG.md, verified as a whole before it reaches disk.

    Operations mutate an in-memory copy and declare their intended effect. Nothing is written
    until `save()`, which is the only method that touches the filesystem and which refuses any
    batch whose actual effect differs from the sum of those declarations.
    """

    def __init__(self, path: Path | str) -> None:
        """Read `path` and stage nothing yet."""
        self.path = Path(path)
        self._original = self.path.read_text()
        self.lines: list[str] = self._original.split("\n")
        self._expected_lost: collections.Counter[str] = collections.Counter()
        self._expected_gained: collections.Counter[str] = collections.Counter()
        self._placements: list[_Placement] = []

    # ---------- staging primitives ----------

    def _stage(
        self,
        lines: list[str],
        lost: collections.Counter[str],
        gained: collections.Counter[str],
    ) -> None:
        """Replace the working lines and record what that change is claimed to have done."""
        self.lines = lines
        self._expected_lost += lost
        self._expected_gained += gained

    def _expect_note_inside(self, needle: str, line: str) -> None:
        """Record that `line` must land inside the entry `needle` identifies."""
        self._placements.append(_Placement(needle, line))

    # ---------- locating ----------

    def _heading_index(self, heading: str, lines: list[str] | None = None) -> int:
        """Return the index of `heading`, which every operation needs to place its edit."""
        target = self.lines if lines is None else lines
        for i, ln in enumerate(target):
            if ln == heading:
                return i
        raise BacklogError(f"{self.path}: no {heading!r} heading")

    def _closed_index(self, lines: list[str] | None = None) -> int:
        """Return the index of the `## Closed` heading."""
        return self._heading_index(CLOSED_HEADING, lines)

    def _find_open(self, needle: str) -> tuple[int, int]:
        """Return the span of the one OPEN entry whose head contains `needle`.

        Raises:
            EntryNotFound: No open entry matches.
            AmbiguousEntry: Several do — the caller must disambiguate, never this module.
        """
        closed_at = self._closed_index()
        hits = [
            (s, e)
            for s, e in entry_blocks(self.lines)
            if s < closed_at and needle in self.lines[s]
        ]
        if not hits:
            raise EntryNotFound(f"no open entry matches {needle!r}")
        if len(hits) > 1:
            heads = "\n  ".join(self.lines[s][:90] for s, _ in hits)
            raise AmbiguousEntry(
                f"{len(hits)} open entries match {needle!r}:\n  {heads}"
            )
        return hits[0]

    # ---------- validation ----------

    @staticmethod
    def _note_lines(note: str) -> list[str]:
        """Split a note into lines, rejecting one that would restructure the document.

        Raises:
            InvalidNote: The note is blank, or a line of it would parse as an entry head or a
                section heading — which would silently create an entry rather than annotate one.
        """
        lines = note.rstrip("\n").split("\n")
        if not any(ln.strip() for ln in lines):
            raise InvalidNote("note is blank")
        for ln in lines:
            if _is_head(ln):
                raise InvalidNote(
                    f"note line would parse as an entry head: {ln[:70]!r}"
                )
            if _is_heading(ln):
                raise InvalidNote(f"note line would parse as a heading: {ln[:70]!r}")
        return lines

    # ---------- operations ----------

    def add_entry(self, text: str) -> None:
        """Insert a new open entry at the top of the Open section (step 5's deferral index line).

        Args:
            text: The whole entry — a `- [ ] ` head line, plus any indented continuation lines.

        Raises:
            InvalidNote: The first line is not an open head, or a continuation line would open a
                second entry or a section of its own.
        """
        lines = text.rstrip("\n").split("\n")
        if not lines[0].startswith("- [ ] "):
            raise InvalidNote(f"a new entry must open with '- [ ] ': {lines[0][:70]!r}")
        for ln in lines[1:]:
            if _is_head(ln):
                raise InvalidNote(f"line would open a second entry: {ln[:70]!r}")
            if _is_heading(ln):
                raise InvalidNote(f"line would parse as a heading: {ln[:70]!r}")

        open_at = self._heading_index(OPEN_HEADING)
        staged = self.lines[: open_at + 1] + ["", *lines] + self.lines[open_at + 1 :]
        gained = collections.Counter(ln for ln in lines if ln.strip())
        self._stage(staged, collections.Counter(), gained)
        # Keyed on the head itself, so the new entry must end up uniquely addressable: a head
        # duplicating (or contained in) another one leaves a pair no later needle can separate.
        body = [ln for ln in lines[1:] if ln.strip()]
        self._expect_note_inside(lines[0], body[-1] if body else lines[0])

    def append_note(self, needle: str, note: str) -> None:
        """Append `note` to the end of the open entry `needle` identifies (insert-only)."""
        note_lines = self._note_lines(note)
        start, end = self._find_open(needle)
        while end > start and not self.lines[end - 1].strip():
            end -= 1
        staged = self.lines[:end] + note_lines + self.lines[end:]
        gained = collections.Counter(ln for ln in note_lines if ln.strip())
        self._stage(staged, collections.Counter(), gained)
        self._expect_note_inside(needle, next(ln for ln in note_lines if ln.strip()))

    def stamp_promoted(self, needle: str, date: str, reason: str | None = None) -> None:
        """Mark the open entry `needle` identifies as promoted on `date`.

        Re-stamping replaces any stamp already present rather than accumulating one, so a
        repeated call with the same arguments is a no-op.

        Raises:
            BacklogError: The head does not have the `- [ ] YYYY-MM-DD — ` shape, or `reason`
                contains `*`, which would break the stamp's own delimiters.
        """
        if reason is not None and "*" in reason:
            raise BacklogError("a promote reason may not contain '*'")
        start, _ = self._find_open(needle)
        old = self.lines[start]
        match = STAMPED_HEAD_RE.match(old)
        if not match:
            raise BacklogError(
                f"unrecognized entry head, refusing to guess: {old[:90]!r}"
            )
        stamp = f"*promoted {date}" + (f" — {reason}" if reason else "") + "* — "
        new = f"{match['prefix']}{stamp}{match['rest']}"
        if new == old:
            return
        staged = list(self.lines)
        staged[start] = new
        self._stage(staged, collections.Counter([old]), collections.Counter([new]))

    def close_entry(self, needle: str, note: str) -> None:
        """Tick the open entry `needle` identifies, append `note`, and move it under Closed."""
        note_lines = self._note_lines(note)
        start, end = self._find_open(needle)
        block = self.lines[start:end]
        while block and not block[-1].strip():
            block.pop()
        old_head = block[0]
        new_head = old_head.replace("- [ ]", "- [x]", 1)
        block = [new_head, *block[1:], *note_lines]

        rest = self.lines[:start] + self.lines[end:]
        closed_at = self._closed_index(rest)
        staged = rest[: closed_at + 1] + ["", *block, ""] + rest[closed_at + 1 :]

        gained = collections.Counter(ln for ln in note_lines if ln.strip())
        gained[new_head] += 1
        self._stage(staged, collections.Counter([old_head]), gained)
        self._expect_note_inside(needle, next(ln for ln in note_lines if ln.strip()))

    # ---------- the only writer ----------

    def save(self) -> Report:
        """Verify every postcondition, then write. Nothing else in this module touches disk.

        Returns:
            A `Report` describing the saved document.

        Raises:
            ShapeViolation: The batch's actual effect differs from what its operations declared,
                or a note landed outside its entry, or the section invariant broke. The file is
                left byte-identical in every one of those cases — the checks all run first.
        """
        text = "\n".join(self.lines).rstrip("\n") + "\n"
        before, after = _nonblank(self._original), _nonblank(text)
        lost, gained = before - after, after - before

        if lost != self._expected_lost:
            raise ShapeViolation(self._describe("lost", lost, self._expected_lost))
        if gained != self._expected_gained:
            raise ShapeViolation(
                self._describe("gained", gained, self._expected_gained)
            )

        new_lines = text.split("\n")
        for placement in self._placements:
            spans = [
                (s, e)
                for s, e in entry_blocks(new_lines)
                if placement.needle in new_lines[s]
            ]
            if len(spans) != 1:
                raise ShapeViolation(
                    f"{placement.needle!r} matches {len(spans)} entries after the edit"
                )
            s, e = spans[0]
            if placement.line not in new_lines[s:e]:
                raise ShapeViolation(
                    f"note landed OUTSIDE the entry it was addressed to "
                    f"({placement.needle!r}): {placement.line[:70]!r}"
                )

        closed_at = self._closed_index(new_lines)
        strays_open = [ln for ln in new_lines[:closed_at] if ln.startswith("- [x]")]
        strays_closed = [ln for ln in new_lines[closed_at:] if ln.startswith("- [ ]")]
        if strays_open or strays_closed:
            raise ShapeViolation(
                f"section invariant broken: {len(strays_open)} closed entries above "
                f"{CLOSED_HEADING}, {len(strays_closed)} open entries below it"
            )

        self.path.write_text(text)
        return Report(
            path=self.path,
            open_count=sum(1 for ln in new_lines[:closed_at] if ln.startswith("- [ ]")),
            closed_count=sum(
                1 for ln in new_lines[closed_at:] if ln.startswith("- [x]")
            ),
            lines_gained=sum(gained.values()),
            lines_lost=sum(lost.values()),
        )

    @staticmethod
    def _describe(
        label: str,
        actual: collections.Counter[str],
        expected: collections.Counter[str],
    ) -> str:
        """Render a shape mismatch, naming the lines that were not accounted for."""
        surprise = list((actual - expected).elements())[:4]
        missing = list((expected - actual).elements())[:4]
        parts = [f"edit {label} lines it did not declare"] if surprise else []
        if missing:
            parts.append(f"declared {label} lines that did not change")
        detail = ""
        if surprise:
            detail += "\n  undeclared: " + "\n              ".join(
                s[:80] for s in surprise
            )
        if missing:
            detail += "\n  unmet:      " + "\n              ".join(
                m[:80] for m in missing
            )
        return "; ".join(parts) + detail


# ---------- CLI ----------


def _read_note() -> str:
    """Read a note from stdin, refusing an empty one rather than writing a bare edit."""
    if sys.stdin.isatty():
        raise BacklogError("expected a note on stdin")
    return sys.stdin.read()


def _build_parser() -> argparse.ArgumentParser:
    """Construct the CLI parser."""
    parser = argparse.ArgumentParser(
        prog="backlog.py",
        description="Edit BACKLOG.md with shape postconditions enforced.",
    )
    parser.add_argument("--path", required=True, type=Path, help="path to BACKLOG.md")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("add", help="insert a new open entry, read whole from stdin")

    for name, helptext in (
        ("append", "append a note (from stdin) to an open entry"),
        ("close", "tick an entry, append a note (from stdin), move it under Closed"),
    ):
        p = sub.add_parser(name, help=helptext)
        p.add_argument(
            "needle", help="substring matching exactly one open entry's head"
        )

    p = sub.add_parser("promote", help="stamp an open entry as promoted")
    p.add_argument("needle", help="substring matching exactly one open entry's head")
    p.add_argument("--date", required=True, help="promotion date, YYYY-MM-DD")
    p.add_argument("--reason", default=None, help="short reason shown inside the stamp")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the CLI.

    Returns:
        0 when the edit landed and every postcondition held, 1 when it was refused.
    """
    args = _build_parser().parse_args(argv)
    try:
        backlog = Backlog(args.path)
        if args.command == "add":
            backlog.add_entry(_read_note())
        elif args.command == "append":
            backlog.append_note(args.needle, _read_note())
        elif args.command == "close":
            backlog.close_entry(args.needle, _read_note())
        else:
            backlog.stamp_promoted(args.needle, args.date, args.reason)
        report = backlog.save()
    except BacklogError as exc:
        print(f"backlog: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"backlog: {exc}", file=sys.stderr)
        return 1
    print(
        f"{report.path}: {report.open_count} open, {report.closed_count} closed "
        f"(+{report.lines_gained}/-{report.lines_lost} lines)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

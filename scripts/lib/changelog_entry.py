"""Insert one CHANGELOG brick entry, proving the edit is an INSERT and nothing more.

Used by the publish path (`scripts/publish-brick.sh`) to add a brick's `## vX.Y.Z — DATE`
section, in `/commit`'s living-changelog format, ahead of every existing version section.

**Why the assertion is on the SHAPE of the edit rather than on where it landed.** Constraining
where an edit STARTS does not constrain how far it REACHES: a locator that finds the right spot
and then overshoots satisfies every start-anchored check while silently eating the lines it ran
past. So the guarantee here is stated over the whole file and verified after the fact — the
multiset of non-blank lines may only GAIN the two new lines, and the surviving lines must still
appear in their original relative order (a multiset alone cannot see a reordering). A failure
raises, and the caller writes nothing.

The one deliberate normalization: the result always ends with exactly one newline, whatever the
input ended with. That touches trailing blank lines only, never content.

Run it as a CLI (`python3 changelog_entry.py <path> <version> <date> <message>`) or import
`insert_entry` for a pure-text transform. Exit codes: 0 written, 1 refused (file untouched),
2 usage error.
"""

from __future__ import annotations

import re
import sys
from collections import Counter
from pathlib import Path

SEPARATOR = " — "
VERSION_RE = re.compile(r"^v[0-9]+\.[0-9]+\.[0-9]+$")
DATE_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
# The living format /commit writes and this repo's CHANGELOG uses. A file whose first
# version section is anything else (Keep a Changelog's `## [1.2.3]`, or a section owned by
# other tooling) is refused rather than having a foreign convention injected above it.
LIVING_HEADING_RE = re.compile(r"^## v[0-9]")


class ChangelogError(Exception):
    """A refusal. The edit was not a clean insert, so the caller must write nothing."""


def render(version: str, date: str, message: str) -> tuple[str, str]:
    """Return the (heading, bullet) pair for one brick entry."""
    return f"## {version}{SEPARATOR}{date}", f"- {message}"


def _heading_version(line: str) -> str:
    """Return the version token of a `## ` heading line, ignoring its date."""
    rest = line[3:].strip()
    return rest.split(SEPARATOR, 1)[0].strip()


def _nonblank(text: str) -> Counter[str]:
    return Counter(line for line in text.split("\n") if line.strip())


def _is_subsequence(needle: list[str], haystack: list[str]) -> bool:
    """True when every element of `needle` appears in `haystack`, in order."""
    it = iter(haystack)
    return all(item in it for item in needle)


def _validate(version: str, date: str, message: str) -> None:
    for label, value in (("version", version), ("date", date), ("message", message)):
        if "\n" in value or "\r" in value:
            raise ChangelogError(f"{label} must be a single line, got {value!r}")
    if not VERSION_RE.match(version):
        raise ChangelogError(f"version must look like vX.Y.Z, got {version!r}")
    if not DATE_RE.match(date):
        raise ChangelogError(f"date must be YYYY-MM-DD, got {date!r}")
    if not message.strip():
        raise ChangelogError("message must not be empty")


def insert_entry(text: str, version: str, date: str, message: str) -> str:
    """Return `text` with one brick entry inserted above every existing version section.

    Args:
        text: The current CHANGELOG contents.
        version: The brick's tag, `vX.Y.Z`.
        date: The brick commit's date, `YYYY-MM-DD`.
        message: The brick's single-line commit subject.

    Returns:
        The new contents, ending in exactly one newline.

    Raises:
        ChangelogError: The version is already present, the file is not in the living
            format, an argument is malformed, or the resulting edit was not a pure
            order-preserving insert of exactly the two new lines.
    """
    _validate(version, date, message)
    heading, bullet = render(version, date, message)

    lines = text.split("\n")
    sections = [i for i, line in enumerate(lines) if line.startswith("## ")]

    for i in sections:
        if _heading_version(lines[i]) == version:
            raise ChangelogError(f"{version} is already present: {lines[i]!r}")

    if sections:
        first = sections[0]
        if not LIVING_HEADING_RE.match(lines[first]):
            raise ChangelogError(
                f"format guard: the first section is {lines[first]!r}, not this repo's "
                "living `## vX.Y.Z` format — refusing to inject a foreign convention"
            )
        prefix = lines[:first]
        block = [heading, bullet, ""]
        if prefix and prefix[-1].strip():
            block.insert(0, "")
        new_lines = prefix + block + lines[first:]
    else:
        # No version sections at all — the entry becomes the first, appended at EOF.
        trimmed = list(lines)
        while trimmed and not trimmed[-1].strip():
            trimmed.pop()
        new_lines = trimmed + ["", heading, bullet]

    result = "\n".join(new_lines).rstrip("\n") + "\n"

    before, after = _nonblank(text), _nonblank(result)
    lost = before - after
    if lost:
        raise ChangelogError(f"edit LOST lines: {sorted(lost)[:3]}")
    gained = after - before
    if gained != Counter([heading, bullet]):
        raise ChangelogError(f"edit gained unexpected lines: {sorted(gained)[:3]}")
    if not _is_subsequence(
        [line for line in text.split("\n") if line.strip()],
        [line for line in result.split("\n") if line.strip()],
    ):
        raise ChangelogError("edit reordered existing lines")

    heads = [line for line in new_lines if line.startswith("## ")]
    if heads[0] != heading:
        raise ChangelogError(f"the new section is not first: {heads[0]!r}")

    return result


def main(argv: list[str]) -> int:
    """Insert one entry into the file named by `argv`, or explain why it was refused."""
    if len(argv) != 4:
        print(
            "Usage: changelog_entry.py <path> <version> <date> <message>",
            file=sys.stderr,
        )
        return 2
    path = Path(argv[0])
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"cannot read {path}: {exc}", file=sys.stderr)
        return 2

    try:
        result = insert_entry(text, argv[1], argv[2], argv[3])
    except ChangelogError as exc:
        print(f"changelog refused: {exc}", file=sys.stderr)
        return 1

    path.write_text(result, encoding="utf-8")
    print(
        f"  changelog: inserted {argv[1]}{SEPARATOR}{argv[2]} (insert-only, +2 lines)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

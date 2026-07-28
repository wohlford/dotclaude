"""Regression test: CLAUDE.md's user-only-skills region must never go
silently empty, and must never drift from the marker's own filter directive.

Everything this test asserts is DERIVED from CLAUDE.md itself (the marker's
existence, its filter=field:value directive, and the rows rendered inside
it) rather than hand-copied as literals. A prior version of this test
hand-copied the filter key, the extractor chain, and the globs as literal
constants: it would not have failed if CLAUDE.md's region were deleted
outright, nor if the marker's filter= key were renamed or typo'd, because
none of those changes touch the literals it actually checked.

What this test PROVES: the marker exists, its filter directive is
well-formed, at least one skill satisfies it, and the rows rendered inside
the marker match the set this test derives independently from the SKILL.md
files on disk. What it does NOT prove: that `sync_docs.py`'s own render
path (handlers.py) would produce the same table -- this test deliberately
avoids calling into handlers.py so a shared bug there and here would not be
invisible to it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import extractors
import formatters
import markers

# tests/ -> sync-docs/ -> skills/ -> repo root
REPO_ROOT = Path(__file__).resolve().parents[3]
CLAUDE_MD = REPO_ROOT / "CLAUDE.md"

# Mirrors handlers.SkillsHandler's defaults: the extractor chain used when no
# `extract=` directive is present, and the two glob locations it searches.
# Deliberately independent of handlers.py -- calling SkillsHandler.discover()
# or handlers._apply_filter()/_field_matches() here would exercise the
# generator's own code path and make this near-tautological.
_DEFAULT_CHAIN = ["yaml-frontmatter", "heading-meta"]
_SKILL_GLOBS = ["skills/*/SKILL.md", ".claude/skills/*/SKILL.md"]


def _find_skills_marker() -> markers.MarkerBlock:
    """Locate CLAUDE.md's `<!-- sync:skills ... -->` marker block.

    Asserting the marker exists is the point of this helper: without it, a
    future edit that deletes the region (or renames the handler) would
    leave nothing here to derive an expectation from, and the rest of this
    module would silently have nothing to check.
    """
    text = CLAUDE_MD.read_text(encoding="utf-8")
    doc = markers.parse(text)
    skills_blocks = [b for b in doc.blocks if b.handler == "skills"]
    assert skills_blocks, (
        "CLAUDE.md must contain a '<!-- sync:skills ... -->' marker -- this "
        "test derives every expectation below from that marker and cannot "
        "meaningfully run without it"
    )
    return skills_blocks[0]


def _parse_filter_directive(block: markers.MarkerBlock) -> tuple[str, str]:
    """Extract and validate the marker's filter=field:value directive.

    Returns (field, value). A missing or malformed filter= would silently
    widen CLAUDE.md's region to every skill in the repo, not just the
    user-only ones -- so its presence and shape are asserted, not assumed.
    """
    filt = block.directives.get("filter")
    assert filt, (
        "CLAUDE.md's sync:skills marker must carry a filter= directive -- "
        "without one the region would render every skill in the repo, not "
        "just the user-only ones"
    )
    assert ":" in filt, f"filter={filt!r} is not field:value form"
    field, _, value = filt.partition(":")
    field, value = field.strip(), value.strip()
    assert field, f"filter={filt!r} has an empty field name"
    assert value, f"filter={filt!r} has an empty value"
    return field, value


def _field_matches(value: Any, want: str) -> bool:
    """Independent re-implementation of the boolean/string match rule.

    Deliberately duplicated rather than imported from handlers.py:
    importing handlers._field_matches would make this test exercise the
    very code path it exists to cross-check, and a bug shared by both
    copies would then be invisible to it.
    """
    if value is None:
        return False
    if isinstance(value, bool):
        return str(value).lower() == want.lower()
    return str(value) == want


def _matching_skill_names(field: str, value: str) -> list[str]:
    """Derive the skill names that satisfy field:value straight from the
    SKILL.md files on disk -- independent of handlers.SkillsHandler.discover.
    """
    chain = extractors.get_chain(_DEFAULT_CHAIN)
    names: list[str] = []
    seen: set[Path] = set()
    for glob in _SKILL_GLOBS:
        for path in REPO_ROOT.glob(glob):
            if path in seen:
                continue
            seen.add(path)
            fields = extractors.extract_chain(path, chain)
            if _field_matches(fields.get(field), value):
                names.append(fields.get("name") or path.parent.name)
    return names


def _rendered_command_names(block: markers.MarkerBlock) -> list[str]:
    """Extract the skill names actually rendered in the marker's `Command`
    column (`` `/name` `` -> `name`)."""
    _, rows = formatters.parse_table(block.body_lines)
    return [row.get("Command", "").strip("`").lstrip("/") for row in rows]


def test_claude_md_skills_marker_exists():
    """The marker itself must exist -- see _find_skills_marker's docstring
    for why this assertion is the point, not a formality."""
    _find_skills_marker()


def test_claude_md_filter_directive_is_present_and_well_formed():
    """The marker's filter= must be present and genuine field:value form."""
    block = _find_skills_marker()
    _parse_filter_directive(block)


def test_disable_model_invocation_filter_is_non_empty_on_live_repo():
    """At least one skill must satisfy the marker's filter= directive.

    Guards CLAUDE.md's `## Skills, agents, hooks, and plugins` region: if
    this set ever goes empty, that heading's sync region silently renders
    as an empty table with no signal from `--check`, since an
    empty-but-consistent region is not drift.
    """
    block = _find_skills_marker()
    field, value = _parse_filter_directive(block)
    names = _matching_skill_names(field, value)
    assert names, (
        f"no skill in this repo satisfies filter={field}:{value} -- "
        "CLAUDE.md's region would silently render empty, and --check "
        "cannot detect a consistently empty region as drift"
    )


def test_claude_md_rendered_rows_match_derived_filter_set():
    """The rows actually rendered inside the marker must equal the set this
    test derives independently from the filter directive and the SKILL.md
    files on disk -- catching drift between what the marker claims to show
    and what it actually contains, without asking handlers.py to check its
    own work.
    """
    block = _find_skills_marker()
    field, value = _parse_filter_directive(block)
    derived = set(_matching_skill_names(field, value))
    rendered = set(_rendered_command_names(block))
    assert rendered == derived

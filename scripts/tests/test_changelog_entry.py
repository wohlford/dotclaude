"""Unit and property tests for scripts/lib/changelog_entry.py — the insert-only CHANGELOG
brick-entry helper used by the publish path.

The claim this module makes is quantified over ALL inputs: an entry insertion may only ADD the
two new lines and may never lose, reorder away, or rewrite anything already in the file. A
handful of hand-written witnesses cannot establish that — constraining where an edit STARTS
does not constrain how far it REACHES, which is the exact shape of the measured defect that
motivated the multiset assertion. So the witnesses below are joined by properties over a
generated corpus.
"""

import random
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import changelog_entry as ce  # noqa: E402, I001

MODULE = Path(__file__).resolve().parent.parent / "lib" / "changelog_entry.py"

LIVING = """# Changelog

All notable changes.

## v0.2.0 — 2026-01-02
- feat(b): second

## v0.1.0 — 2026-01-01
- feat(a): first
"""

KEEP_A_CHANGELOG = """# Changelog

## [1.2.3] - 2026-01-02
### Added
- something
"""

NO_SECTIONS = """# Changelog

Nothing released yet.
"""


def nonblank(text):
    return Counter(ln for ln in text.split("\n") if ln.strip())


# ---------- witnesses: placement ----------


def test_inserts_before_the_first_version_section():
    out = ce.insert_entry(LIVING, "v0.3.0", "2026-01-03", "feat(c): third")
    heads = [ln for ln in out.split("\n") if ln.startswith("## ")]
    assert heads[0] == "## v0.3.0 — 2026-01-03"
    assert heads[1] == "## v0.2.0 — 2026-01-02"


def test_bullet_immediately_follows_the_heading():
    out = ce.insert_entry(LIVING, "v0.3.0", "2026-01-03", "feat(c): third")
    lines = out.split("\n")
    i = lines.index("## v0.3.0 — 2026-01-03")
    assert lines[i + 1] == "- feat(c): third"


def test_exactly_one_blank_line_on_each_side():
    out = ce.insert_entry(LIVING, "v0.3.0", "2026-01-03", "feat(c): third")
    lines = out.split("\n")
    i = lines.index("## v0.3.0 — 2026-01-03")
    assert lines[i - 1] == "" and lines[i - 2].strip() != ""
    assert lines[i + 2] == "" and lines[i + 3].strip() != ""


def test_inserts_a_blank_separator_when_the_heading_abuts_prose():
    tight = "# Changelog\n## v0.1.0 — 2026-01-01\n- feat(a): first\n"
    out = ce.insert_entry(tight, "v0.2.0", "2026-01-02", "feat(b): second")
    lines = out.split("\n")
    i = lines.index("## v0.2.0 — 2026-01-02")
    assert lines[i - 1] == ""


def test_appends_at_eof_when_the_file_has_no_version_section():
    out = ce.insert_entry(NO_SECTIONS, "v0.1.0", "2026-01-01", "feat(a): first")
    lines = [ln for ln in out.split("\n") if ln.strip()]
    assert lines[-2:] == ["## v0.1.0 — 2026-01-01", "- feat(a): first"]


def test_output_ends_with_exactly_one_newline():
    for src in (LIVING, NO_SECTIONS):
        out = ce.insert_entry(src, "v9.9.9", "2026-01-03", "feat(z): last")
        assert out.endswith("\n") and not out.endswith("\n\n")


# ---------- witnesses: refusals ----------


def test_refuses_a_version_already_present():
    with pytest.raises(ce.ChangelogError, match="already present"):
        ce.insert_entry(LIVING, "v0.2.0", "2026-01-02", "feat(b): second")


def test_refuses_a_version_already_present_under_a_different_date():
    with pytest.raises(ce.ChangelogError, match="already present"):
        ce.insert_entry(LIVING, "v0.2.0", "2026-06-06", "feat(b): second")


def test_refuses_a_keep_a_changelog_file():
    with pytest.raises(ce.ChangelogError, match="format guard"):
        ce.insert_entry(KEEP_A_CHANGELOG, "v1.3.0", "2026-01-03", "feat(c): third")


def test_refuses_a_message_spanning_lines():
    with pytest.raises(ce.ChangelogError, match="single line"):
        ce.insert_entry(LIVING, "v0.3.0", "2026-01-03", "feat(c): a\nb")


def test_refuses_a_version_spanning_lines():
    with pytest.raises(ce.ChangelogError, match="single line"):
        ce.insert_entry(LIVING, "v0.3\n.0", "2026-01-03", "feat(c): third")


@pytest.mark.parametrize("bad", ["0.3.0", "v0.3", "v0.3.0-rc1", "vX.Y.Z", ""])
def test_refuses_a_malformed_version(bad):
    with pytest.raises(ce.ChangelogError, match="vX.Y.Z"):
        ce.insert_entry(LIVING, bad, "2026-01-03", "feat(c): third")


@pytest.mark.parametrize("bad", ["2026-1-3", "03/01/2026", "today", ""])
def test_refuses_a_malformed_date(bad):
    with pytest.raises(ce.ChangelogError, match="YYYY-MM-DD"):
        ce.insert_entry(LIVING, "v0.3.0", bad, "feat(c): third")


def test_refuses_an_empty_message():
    with pytest.raises(ce.ChangelogError, match="must not be empty"):
        ce.insert_entry(LIVING, "v0.3.0", "2026-01-03", "   ")


# ---------- properties over a generated corpus ----------


def corpus():
    """Changelog-shaped files varying every axis the insert has to survive."""
    rng = random.Random(20260729)
    out = []
    for i in range(300):
        parts = []
        if rng.random() < 0.8:
            parts.append("# Changelog\n")
        if rng.random() < 0.5:
            parts.append("\nSome prose about the project.\n")
        n = rng.randrange(0, 6)
        for k in range(n):
            ver = f"v0.{n - k}.{rng.randrange(0, 9)}"
            parts.append(f"\n## {ver} — 2026-01-0{rng.randrange(1, 9)}\n")
            for _ in range(rng.randrange(1, 4)):
                parts.append(f"- fix(area{rng.randrange(0, 3)}): line {i}\n")
        text = "".join(parts)
        if rng.random() < 0.3:
            text = text.replace("\n\n", "\n")
        if text.strip():
            out.append(text)
    return out


CORPUS = corpus()


@pytest.mark.parametrize("text", CORPUS)
def test_property_insert_is_purely_additive(text):
    """No pre-existing non-blank line may vanish, and exactly two may appear."""
    heading = "## v42.0.0 — 2026-12-31"
    bullet = "- feat(new): a brand new brick"
    if heading in text:
        pytest.skip("generated corpus collided with the probe version")
    out = ce.insert_entry(text, "v42.0.0", "2026-12-31", "feat(new): a brand new brick")
    before, after = nonblank(text), nonblank(out)
    assert not (before - after), "insert LOST pre-existing lines"
    assert (after - before) == Counter([heading, bullet])


@pytest.mark.parametrize("text", CORPUS)
def test_property_relative_order_of_existing_lines_is_preserved(text):
    """A subsequence check — a multiset alone cannot catch a reordering."""
    out = ce.insert_entry(text, "v42.0.0", "2026-12-31", "feat(new): a brand new brick")
    old = [ln for ln in text.split("\n") if ln.strip()]
    new = [ln for ln in out.split("\n") if ln.strip()]
    it = iter(new)
    assert all(ln in it for ln in old), "existing lines were reordered"


@pytest.mark.parametrize("text", CORPUS)
def test_property_the_new_section_is_always_first(text):
    out = ce.insert_entry(text, "v42.0.0", "2026-12-31", "feat(new): a brand new brick")
    heads = [ln for ln in out.split("\n") if ln.startswith("## ")]
    assert heads[0] == "## v42.0.0 — 2026-12-31"


@pytest.mark.parametrize("text", CORPUS)
def test_property_second_insert_of_the_same_version_always_refuses(text):
    out = ce.insert_entry(text, "v42.0.0", "2026-12-31", "feat(new): a brand new brick")
    with pytest.raises(ce.ChangelogError):
        ce.insert_entry(out, "v42.0.0", "2026-12-31", "feat(new): a brand new brick")


# ---------- CLI ----------


def run_cli(*args):
    return subprocess.run(
        [sys.executable, str(MODULE), *args], capture_output=True, text=True
    )


def test_cli_writes_the_entry_and_exits_zero(tmp_path):
    p = tmp_path / "CHANGELOG.md"
    p.write_text(LIVING)
    proc = run_cli(str(p), "v0.3.0", "2026-01-03", "feat(c): third")
    assert proc.returncode == 0, proc.stderr
    assert "## v0.3.0 — 2026-01-03" in p.read_text()


def test_cli_refusal_exits_one_and_leaves_the_file_byte_identical(tmp_path):
    p = tmp_path / "CHANGELOG.md"
    p.write_text(KEEP_A_CHANGELOG)
    proc = run_cli(str(p), "v1.3.0", "2026-01-03", "feat(c): third")
    assert proc.returncode == 1
    assert p.read_text() == KEEP_A_CHANGELOG
    assert "format guard" in proc.stderr


def test_cli_usage_error_exits_two(tmp_path):
    proc = run_cli(str(tmp_path / "CHANGELOG.md"), "v0.3.0")
    assert proc.returncode == 2


def test_cli_missing_file_exits_two(tmp_path):
    proc = run_cli(str(tmp_path / "absent.md"), "v0.3.0", "2026-01-03", "feat(c): x")
    assert proc.returncode == 2


@pytest.mark.parametrize(
    ("source", "version", "date"),
    [
        (KEEP_A_CHANGELOG, "v1.3.0", "2026-01-03"),  # format guard
        (LIVING, "v0.2.0", "2026-01-03"),  # already present
        (LIVING, "0.3.0", "2026-01-03"),  # malformed version
        (LIVING, "v0.3.0", "today"),  # malformed date
    ],
)
def test_cli_refusals_are_not_disabled_by_optimised_python(
    tmp_path, source, version, date
):
    """Every refusal must survive `python3 -O`, which strips bare `assert` statements."""
    p = tmp_path / "CHANGELOG.md"
    p.write_text(source)
    proc = subprocess.run(
        [sys.executable, "-O", str(MODULE), str(p), version, date, "x: y"],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert p.read_text() == source

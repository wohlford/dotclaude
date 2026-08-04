"""Tests for scripts/claude-md-structure.py — the CLAUDE.md hazards-section measurer.

Every row here names a measured wrong answer, not a hypothetical one. The tool exists because the
same numbers were hand-derived in four consecutive `/debrief` runs and TWO of those hand-rolls
returned different wrong answers — and neither was detectable from its own output, which is the
part that decides the test list. A measurement that is merely plausible is indistinguishable from
a correct one; only a fixture whose true value is known by construction can tell them apart.

The two failures sit at opposite edges of the same region:

  * the LAST member of a group has no following member to stop at, so a naive split runs on into
    the `####` heading below it — over-reporting bullet length (12 against a true 10);
  * the section itself has no following `###` to stop at unless you look for one, so an unbounded
    walk runs on into Package Management and counts its `#### Python (uv)` subheadings as hazard
    groups — over-reporting group count (13/35 against a true 10/33).

Both read in the direction that MANUFACTURES work: a healthy bullet gets "fixed", or three healthy
groups get declared under-populated.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

TOOL = Path(__file__).resolve().parent.parent / "claude-md-structure.py"


def run(path, *args):
    return subprocess.run(
        [sys.executable, str(TOOL), "--file", str(path), *args],
        capture_output=True,
        text=True,
    )


# A section whose true shape is known BY CONSTRUCTION: two groups, sizes 2 and 1, and the last
# member of group A is exactly 2 non-blank lines followed by a heading — the precise shape the
# naive split got wrong.
SECTION = """# Title

Some preamble that is not part of the section.

### Verification hazards — instruments that read as verified while proving nothing

Intro prose for the section.

#### Group A

- **First member of A.** One continuation line.
  This is the continuation.
- **Last member of A, right before a heading.** It is two non-blank lines.
  Second and final line.

#### Group B

- **Only member of B.** A single line.

## Package Management

Text that is not a hazard group.

#### Python (uv)

- Install: `uv pip install <package>`
- Create venv: `uv venv`

#### Node.js (NVM)

- Install: `npm install <package>`
"""


@pytest.fixture
def doc(tmp_path):
    path = tmp_path / "CLAUDE.md"
    path.write_text(SECTION)
    return path


def test_the_last_member_of_a_group_does_not_swallow_the_heading_below_it(doc):
    """Failure (1), the measured one: `re.split(r'\\n(?=- \\*\\*)')` reported 12 against a true 10.

    Here the last member of Group A is two non-blank lines by construction. A splitter that runs
    to the next `- **` instead of stopping at the heading reports it as four — the bullet's two
    lines plus `#### Group B` plus B's own bullet.
    """
    out = run(doc).stdout
    lengths = [
        int(line.split()[0]) for line in out.splitlines() if line.startswith("        ")
    ]
    assert lengths == [2, 2, 1], out
    assert "max_member_lines=2" in out, out


def test_the_section_is_bounded_so_later_subheadings_are_not_counted(doc):
    """Failure (2), the measured one: an unbounded walk reported 13 groups against a true 10.

    `#### Python (uv)` and `#### Node.js (NVM)` sit under `## Package Management`, BELOW the
    section. A walk that does not stop at the next `##`/`###` counts them and their bullets.
    """
    out = run(doc).stdout
    assert "groups:   2" in out, out
    assert "members:  3" in out, out
    assert "Python (uv)" not in out, out
    assert "Node.js" not in out, out


def test_a_group_heading_does_not_itself_end_the_section(doc):
    """The bound must stop at `###`/`##` but NOT at `####`, or it ends at its own first group."""
    out = run(doc).stdout
    assert "Group A" in out and "Group B" in out, out


def test_sizes_are_reported_in_the_form_the_admission_rule_is_stated_in(doc):
    out = run(doc).stdout
    assert "sizes:    2/1" in out, out


def test_sizes_sum_to_the_member_count(doc):
    out = run(doc).stdout
    sizes = next(ln for ln in out.splitlines() if ln.startswith("sizes:")).split()[1]
    members = int(
        next(ln for ln in out.splitlines() if ln.startswith("members:")).split()[1]
    )
    assert sum(int(n) for n in sizes.split("/")) == members, out


def test_a_section_with_no_members_is_an_ERROR_not_a_clean_pass(tmp_path):
    """Zero is the loudest false pass there is — a parser matching nothing must not report PASS."""
    path = tmp_path / "CLAUDE.md"
    path.write_text(
        "### Verification hazards — nothing here\n\nProse only, no groups.\n\n## Next\n"
    )
    proc = run(path)
    assert proc.returncode == 2, proc.stdout
    assert "RESULT: ERROR rc=2" in proc.stdout, proc.stdout
    assert "RESULT: PASS" not in proc.stdout, proc.stdout


def test_a_missing_section_is_an_ERROR(tmp_path):
    path = tmp_path / "CLAUDE.md"
    path.write_text("# Title\n\n## Something else\n\n- a bullet\n")
    proc = run(path)
    assert proc.returncode == 2, proc.stdout
    assert "RESULT: ERROR rc=2" in proc.stdout, proc.stdout


def test_an_unreadable_file_is_an_ERROR_not_a_zero_measurement(tmp_path):
    proc = run(tmp_path / "does-not-exist.md")
    assert proc.returncode == 2, proc.stdout
    assert "RESULT: ERROR rc=2" in proc.stdout, proc.stdout


def test_the_verdict_line_is_last_and_carries_every_headline_number(doc):
    out = run(doc).stdout.strip()
    last = out.splitlines()[-1]
    assert last.startswith("RESULT: PASS rc=0"), out
    for key in ("groups=", "members=", "max_member_lines=", "max_heading="):
        assert key in last, last


def test_it_measures_the_repos_own_CLAUDE_md():
    """A live-file smoke row: it must reach a real verdict on the real subject, not just fixtures.

    Deliberately asserts SHAPE, not the current counts — pinning those would fail on every
    legitimate edit to the file and teach the next session to ignore this suite.
    """
    real = TOOL.parent.parent / "CLAUDE.md"
    proc = run(real)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    last = proc.stdout.strip().splitlines()[-1]
    assert last.startswith("RESULT: PASS rc=0 groups="), last
    groups = int(last.split("groups=")[1].split()[0])
    members = int(last.split("members=")[1].split()[0])
    assert groups > 0 and members >= groups, last

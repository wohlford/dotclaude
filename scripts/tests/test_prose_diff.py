"""Regression tests for scripts/prose-diff.py — the restructuring-losslessness verifier.

The subject answers "did this rewrite move content without changing it", in two granularities that
answer genuinely different questions: WORDS (a reflow may rewrap freely, but no word may vanish)
and LINES (a section move may reorder, but no line may change). Both are needed — the backlog
entry prescribed only the word mode while its own fifth instance used the line mode.

The rows that matter most are the ones asserting a FAILURE is reported, and the zero-denominator
guard: a section that matches nothing must ERROR, never pass. An empty comparison is the loudest
false clean there is.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent.parent
SUBJECT = REPO / "scripts" / "prose-diff.py"


def run(*args):
    """Invoke the subject bare-path, so the exec bit and shebang are exercised too."""
    proc = subprocess.run(
        [str(SUBJECT), *[str(a) for a in args]],
        capture_output=True,
        text=True,
    )
    return proc.returncode, proc.stdout + proc.stderr


@pytest.fixture
def bed(tmp_path):
    # tmp_path.resolve() pins the PHYSICAL path: $TMPDIR is a symlink on macOS, and a logical
    # path that does not physically contain the fixture lets a path-resolving subject take a
    # different branch and never reach what these rows test.
    return tmp_path.resolve()


def write(path, text):
    path.write_text(text)
    return path


# --------------------------------------------------------------------- usage


def test_no_arguments_is_a_usage_error():
    rc, out = run()
    assert rc == 2
    assert "Usage:" in out


def test_help_exits_zero_and_documents_both_modes():
    rc, out = run("--help")
    assert rc == 0
    assert "words" in out and "lines" in out


def test_a_missing_input_file_is_a_usage_error(bed):
    after = write(bed / "after.md", "hello\n")
    rc, out = run(bed / "nope.md", after)
    assert rc == 2
    assert "nope.md" in out


# --------------------------------------------------------------------- word mode


def test_identical_files_pass(bed):
    a = write(bed / "a.md", "the quick brown fox\n")
    b = write(bed / "b.md", "the quick brown fox\n")
    rc, out = run(a, b)
    assert rc == 0
    assert "RESULT: PASS" in out


def test_a_pure_rewrap_passes(bed):
    """The whole point: line breaks may move freely as long as no word is lost."""
    a = write(bed / "a.md", "the quick brown fox jumps over the lazy dog\n")
    b = write(bed / "b.md", "the quick brown\nfox jumps over\nthe lazy dog\n")
    rc, out = run(a, b)
    assert rc == 0


def test_a_removed_word_fails_and_is_named(bed):
    a = write(bed / "a.md", "record the rc inside the artifact\n")
    b = write(bed / "b.md", "record the rc in the artifact\n")
    rc, out = run(a, b)
    assert rc == 1
    assert "RESULT: FAIL" in out
    assert "inside" in out


def test_an_added_word_fails_too_the_diff_is_bidirectional(bed):
    """One direction alone would bless a rewrite that smuggled in a new claim."""
    a = write(bed / "a.md", "ship them together\n")
    b = write(bed / "b.md", "ship them together immediately\n")
    rc, out = run(a, b)
    assert rc == 1
    assert "immediately" in out


def test_punctuation_only_changes_pass(bed):
    a = write(bed / "a.md", "run it, then read the artifact.\n")
    b = write(bed / "b.md", "run it — then read the artifact\n")
    rc, out = run(a, b)
    assert rc == 0


def test_a_recapitalization_is_reported_by_default(bed):
    a = write(bed / "a.md", "the launcher owns the backgrounding\n")
    b = write(bed / "b.md", "The launcher owns the backgrounding\n")
    rc, out = run(a, b)
    assert rc == 1


def test_ignore_case_permits_a_recapitalization(bed):
    """Promoting a mid-sentence clause to a bullet legitimately changes its first letter."""
    a = write(bed / "a.md", "the launcher owns the backgrounding\n")
    b = write(bed / "b.md", "The launcher owns the backgrounding\n")
    rc, out = run(a, b, "--ignore-case")
    assert rc == 0


# --------------------------------------------------------------------- line mode


def test_line_mode_passes_a_pure_reordering(bed):
    a = write(bed / "a.md", "- alpha\n- beta\n- gamma\n")
    b = write(bed / "b.md", "- gamma\n- alpha\n- beta\n")
    rc, out = run(a, b, "--mode", "lines")
    assert rc == 0


def test_line_mode_fails_a_rewrap_which_word_mode_allows(bed):
    """The two modes are not interchangeable — this is why both exist."""
    a = write(bed / "a.md", "the quick brown fox jumps\n")
    b = write(bed / "b.md", "the quick brown\nfox jumps\n")
    assert run(a, b, "--mode", "lines")[0] == 1
    assert run(a, b, "--mode", "words")[0] == 0


def test_line_mode_fails_an_insertion_by_default(bed):
    a = write(bed / "a.md", "- alpha\n- beta\n")
    b = write(bed / "b.md", "- alpha\n- new line\n- beta\n")
    rc, out = run(a, b, "--mode", "lines")
    assert rc == 1
    assert "new line" in out


def test_allow_additions_permits_an_insert_only_edit(bed):
    """CLAUDE.md's own prescription is 'insert-only OR a pure reordering' — this is the former."""
    a = write(bed / "a.md", "- alpha\n- beta\n")
    b = write(bed / "b.md", "- alpha\n- new line\n- beta\n")
    rc, out = run(a, b, "--mode", "lines", "--allow-additions")
    assert rc == 0


def test_allow_additions_still_fails_a_removal(bed):
    a = write(bed / "a.md", "- alpha\n- beta\n")
    b = write(bed / "b.md", "- alpha\n")
    rc, out = run(a, b, "--mode", "lines", "--allow-additions")
    assert rc == 1
    assert "beta" in out


def test_allow_additions_is_refused_in_words_mode(bed):
    """A flag that does not apply to the chosen mode must be refused, not silently ignored.
    Found by a surviving mutation: nothing asserted what --allow-additions did in words mode,
    and the answer was 'quietly nothing' — the shape this repo measured in six of seven handlers.
    """
    a = write(bed / "a.md", "alpha beta\n")
    b = write(bed / "b.md", "alpha beta gamma\n")
    rc, out = run(a, b, "--allow-additions")
    assert rc == 2
    assert "allow-additions" in out


def test_blank_lines_are_not_content(bed):
    a = write(bed / "a.md", "- alpha\n\n- beta\n")
    b = write(bed / "b.md", "- alpha\n- beta\n\n\n")
    rc, out = run(a, b, "--mode", "lines")
    assert rc == 0


# --------------------------------------------------------------------- sections


SECTIONED = """# Title

intro words here

## Alpha

alpha body one
alpha body two

### Alpha Sub

nested content

## Beta

beta body
"""


def test_a_section_scopes_the_comparison(bed):
    a = write(bed / "a.md", SECTIONED)
    b = write(
        bed / "b.md", SECTIONED.replace("beta body", "beta body rewritten entirely")
    )
    # The change is in Beta, so scoping to Alpha must pass.
    assert run(a, b, "--section", "Alpha")[0] == 0
    # ...and scoping to Beta must fail.
    assert run(a, b, "--section", "Beta")[0] == 1


def test_a_section_includes_its_deeper_subsections(bed):
    a = write(bed / "a.md", SECTIONED)
    b = write(
        bed / "b.md", SECTIONED.replace("nested content", "nested content changed")
    )
    rc, out = run(a, b, "--section", "Alpha")
    assert rc == 1, "a ### subsection is part of its ## parent"


def test_a_section_ends_at_the_next_heading_of_the_same_level(bed):
    a = write(bed / "a.md", SECTIONED)
    b = write(bed / "b.md", SECTIONED.replace("beta body", "beta body changed"))
    rc, out = run(a, b, "--section", "Alpha")
    assert rc == 0, "Alpha must not run past the ## Beta heading"


def test_a_section_matching_nothing_ERRORS_rather_than_passing(bed):
    """The zero-denominator guard. Two empty extractions compare equal, so the natural failure
    here is a triumphant PASS over nothing at all — the loudest false clean there is."""
    a = write(bed / "a.md", SECTIONED)
    b = write(bed / "b.md", SECTIONED)
    rc, out = run(a, b, "--section", "Nonexistent")
    assert rc == 2
    assert "Nonexistent" in out


def test_a_section_dropped_by_the_rewrite_ERRORS(bed):
    a = write(bed / "a.md", SECTIONED)
    b = write(bed / "b.md", SECTIONED.replace("## Beta\n\nbeta body\n", ""))
    rc, out = run(a, b, "--section", "Beta")
    assert rc == 2


def test_an_ambiguous_section_name_ERRORS(bed):
    doubled = SECTIONED + "\n## Alpha\n\nsecond alpha\n"
    a = write(bed / "a.md", doubled)
    b = write(bed / "b.md", doubled)
    rc, out = run(a, b, "--section", "Alpha")
    assert rc == 2, "matching two headings must not silently pick one"


# --------------------------------------------------------------------- anchors


def test_an_anchor_that_survives_contributes_to_a_pass(bed):
    a = write(bed / "a.md", "record the rc inside the artifact\n")
    b = write(bed / "b.md", "inside the artifact, record the rc\n")
    rc, out = run(a, b, "--anchor", "record the rc")
    assert rc == 0


def test_a_missing_anchor_fails_and_is_named(bed):
    a = write(bed / "a.md", "record the rc inside the artifact\n")
    b = write(bed / "b.md", "record the rc inside the artifact\n")
    rc, out = run(a, b, "--anchor", "ship them together")
    assert rc == 1
    assert "ship them together" in out


def test_an_anchor_matches_across_a_line_WRAP(bed):
    """Measured twice: a phrase that wraps mid-line defeats a raw substring test. Whitespace
    must be normalized before matching or every anchor is a coin flip on where the text reflows."""
    a = write(bed / "a.md", "record the rc inside the artifact so a death is legible\n")
    b = write(
        bed / "b.md", "record the rc inside\nthe artifact so a death is legible\n"
    )
    rc, out = run(a, b, "--anchor", "the rc inside the artifact")
    assert rc == 0


def test_a_failing_anchor_warns_against_shortening_it(bed):
    """The repair for a false-FAIL is to restore the line, never to weaken the anchor — an
    instruction that has to travel with the failure or it will not be followed."""
    a = write(bed / "a.md", "alpha\n")
    b = write(bed / "b.md", "alpha\n")
    rc, out = run(a, b, "--anchor", "gone")
    assert rc == 1
    assert "restore" in out.lower()
    assert "shorten" in out.lower()


# --------------------------------------------------------------------- verdict line


def test_the_verdict_is_the_last_line_of_stdout(bed):
    a = write(bed / "a.md", "alpha beta\n")
    b = write(bed / "b.md", "alpha\n")
    proc = subprocess.run(
        [str(SUBJECT), str(a), str(b)], capture_output=True, text=True
    )
    assert proc.stdout.strip().split("\n")[-1].startswith("RESULT: FAIL rc=1")


def test_exactly_one_verdict_line_is_emitted(bed):
    """ "Last line is a verdict" is weaker than it looks — it stays true when a SPURIOUS earlier
    verdict is also printed, and a consumer doing `grep '^RESULT:' | head -1` then reads the
    wrong one. Found by a surviving mutation that prepended a fake PASS line."""
    a = write(bed / "a.md", "alpha beta\n")
    b = write(bed / "b.md", "alpha\n")
    proc = subprocess.run(
        [str(SUBJECT), str(a), str(b)], capture_output=True, text=True
    )
    assert proc.stdout.count("RESULT:") == 1


def test_the_verdict_reports_both_directions_and_the_mode(bed):
    a = write(bed / "a.md", "alpha beta\n")
    b = write(bed / "b.md", "alpha gamma\n")
    rc, out = run(a, b)
    assert "mode=words" in out
    assert "removed=1" in out
    assert "added=1" in out


def test_a_nonzero_denominator_is_asserted(bed):
    """Two empty files compare equal. That is a comparison of nothing, not a clean bill."""
    a = write(bed / "a.md", "\n\n")
    b = write(bed / "b.md", "\n")
    rc, out = run(a, b)
    assert rc == 2, "an empty comparison must not report PASS"


# --------------------------------------------------------------------- git convenience


def test_git_mode_reads_the_before_side_from_a_revision(bed):
    """The hand step this tool exists to remove is `git show REV:path > /tmp/before`."""
    subprocess.run(["git", "init", "-q"], cwd=bed, check=True)
    subprocess.run(["git", "config", "user.email", "t@e.st"], cwd=bed, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=bed, check=True)
    f = write(bed / "doc.md", "alpha beta gamma\n")
    subprocess.run(["git", "add", "doc.md"], cwd=bed, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=bed, check=True)
    f.write_text("alpha beta\n")

    proc = subprocess.run(
        [str(SUBJECT), "--git", "HEAD", "doc.md"],
        capture_output=True,
        text=True,
        cwd=bed,
    )
    assert proc.returncode == 1
    assert "gamma" in proc.stdout + proc.stderr


def test_git_mode_on_an_unchanged_file_passes(bed):
    subprocess.run(["git", "init", "-q"], cwd=bed, check=True)
    subprocess.run(["git", "config", "user.email", "t@e.st"], cwd=bed, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=bed, check=True)
    write(bed / "doc.md", "alpha beta gamma\n")
    subprocess.run(["git", "add", "doc.md"], cwd=bed, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=bed, check=True)

    proc = subprocess.run(
        [str(SUBJECT), "--git", "HEAD", "doc.md"],
        capture_output=True,
        text=True,
        cwd=bed,
    )
    assert proc.returncode == 0

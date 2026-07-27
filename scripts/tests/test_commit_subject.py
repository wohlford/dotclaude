"""Tests for the commit-subject policy and classification helpers."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import commit_subject as cs  # noqa: E402


def write_marker(tmp_path: Path, text: str) -> Path:
    (tmp_path / cs.MARKER_NAME).write_text(text, encoding="utf-8")
    return tmp_path


def test_absent_marker_is_inert():
    assert cs.load_policy(None) is None


def test_missing_marker_file_is_inert(tmp_path):
    assert cs.load_policy(str(tmp_path)) is None


def test_present_marker_uses_declared_values(tmp_path):
    write_marker(tmp_path, "subject_advise = 60\nsubject_block = 70\n")
    assert cs.load_policy(str(tmp_path)) == cs.Policy(advise=60, block=70)


def test_absent_key_falls_back_to_its_default(tmp_path):
    write_marker(tmp_path, "subject_advise = 50\n")
    assert cs.load_policy(str(tmp_path)) == cs.Policy(advise=50, block=cs.DEFAULT_BLOCK)


def test_empty_marker_uses_both_defaults(tmp_path):
    write_marker(tmp_path, "# opt in, take the documented numbers\n")
    assert cs.load_policy(str(tmp_path)) == cs.Policy(
        cs.DEFAULT_ADVISE, cs.DEFAULT_BLOCK
    )


def test_comments_and_inline_comments_are_ignored(tmp_path):
    write_marker(
        tmp_path, "# lead\nsubject_advise = 60  # inline\nsubject_block = 70\n"
    )
    assert cs.load_policy(str(tmp_path)) == cs.Policy(60, 70)


@pytest.mark.parametrize(
    "body",
    [
        "subject_advise = seventy\n",  # our key, non-integer value
        "subject_advise =\n",  # our key, empty value
        "this is not a key-value line\n",  # not parseable as key = value
        "subject_advise = 90\nsubject_block = 80\n",  # advise > block: incoherent
        "subject_advise = ²\n",  # NON-ASCII digit: isdigit()-true, int()-false (raises ValueError)
        "subject_advise = "
        + "9" * 4301
        + "\n",  # trips CPython's int-string conversion limit (>4300 digits)
        "# "
        + "x" * 70_000
        + "\nsubject_advise = 60\nsubject_block = 70\n",  # oversized file (> the 64KB read cap)
    ],
)
def test_malformed_marker_is_INERT_never_defaulted(tmp_path, body):
    """A marker we cannot read must never enable enforcement at assumed thresholds.

    Note: `١` (Arabic-Indic ONE) is deliberately NOT included here even though it is both
    `isdigit()`-true and `int()`-valid. The `isascii()` guard makes it inert too — a conservative
    choice, not a bug — so it is not a "should work" case.
    """
    write_marker(tmp_path, body)
    assert cs.load_policy(str(tmp_path)) is None


def test_unknown_key_is_ignored_for_forward_compatibility(tmp_path):
    write_marker(tmp_path, "subject_advise = 60\nfuture_option = 3\n")
    assert cs.load_policy(str(tmp_path)) == cs.Policy(60, cs.DEFAULT_BLOCK)


@pytest.mark.parametrize(
    "subject",
    [
        "fixup! feat(x): original",
        "squash! feat(x): original",
        "amend! feat(x): original",
        'Revert "feat(x): original"',
    ],
)
def test_machine_prefixes_are_exempt(subject):
    assert cs.is_exempt(subject)


def test_ordinary_subject_is_not_exempt():
    assert not cs.is_exempt("feat(x): an ordinary subject")


def test_lower_bound_first_line_splits_on_the_newline_substitute():
    # A multi-line -m arrives as one token with newlines rewritten to " ; ".
    assert (
        cs.lower_bound_first_line("feat(x): subject ; and the body")
        == "feat(x): subject"
    )


def test_lower_bound_first_line_rstrips():
    assert cs.lower_bound_first_line("feat(x): subject   ") == "feat(x): subject"


def test_lower_bound_is_a_LOWER_bound_never_higher():
    """A literal ' ; ' inside the real first line under-measures — which fails OPEN, by design."""
    real = "feat(x): a ; b"
    assert len(cs.lower_bound_first_line(real)) <= len(real)


@pytest.mark.parametrize(
    "length,expected",
    [(71, "ok"), (72, "advise"), (79, "advise"), (80, "block"), (93, "block")],
)
def test_classify_boundaries_are_inclusive_at_the_threshold(length, expected):
    policy = cs.Policy(advise=72, block=80)
    subject = "x" * length
    assert cs.classify(subject, policy) == expected


def test_classify_measures_code_points_not_bytes():
    policy = cs.Policy(advise=10, block=20)
    # 12 code points, but far more UTF-8 bytes.
    assert cs.classify("é" * 12, policy) == "advise"


def test_classify_ignores_a_trailing_space_at_the_boundary():
    policy = cs.Policy(advise=72, block=80)
    assert cs.classify("x" * 71 + " ", policy) == "ok"


def test_classify_returns_ok_for_an_exempt_subject_however_long():
    policy = cs.Policy(advise=72, block=80)
    assert cs.classify("fixup! " + "x" * 100, policy) == "ok"


# ------------------------------------------------------------------------------------
# Extra coverage (not in the plan's Step 1 listing): segments, has_leading_override,
# and command_is_overridden — added because both hooks (Tasks 3 and 4) depend on their
# exact contracts and the plan's given test file does not exercise them directly.
# ------------------------------------------------------------------------------------


def test_segments_splits_on_control_operators():
    tokens = ["git", "add", ".", "&&", "git", "commit", "-m", "x"]
    assert cs.segments(tokens) == [["git", "add", "."], ["git", "commit", "-m", "x"]]


def test_segments_single_segment_when_no_operator():
    tokens = ["git", "commit", "-m", "x"]
    assert cs.segments(tokens) == [["git", "commit", "-m", "x"]]


def test_command_is_overridden_true_when_override_leads():
    assert cs.command_is_overridden(f"{cs.OVERRIDE} git commit -m x")


def test_command_is_overridden_false_when_override_is_a_pathspec():
    # The override appears in the segment, but AFTER `--`, as a pathspec — not leading.
    assert not cs.command_is_overridden(f'git commit -m "x" -- {cs.OVERRIDE}')


def test_command_is_overridden_false_on_tokenizer_ambiguity():
    # Nesting past gitcmd's MAX_CONTEXT_DEPTH raises ValueError inside the tokenizer;
    # command_is_overridden must fail closed on the *advisory-suppression* question by
    # returning False (i.e. do not silently suppress an advisory on ambiguous input).
    deep = "$(" * 10 + "x" + ")" * 10
    assert not cs.command_is_overridden(f'git commit -m "{deep}"')


def test_has_leading_override_true_when_first_token_is_override():
    assert cs.has_leading_override([cs.OVERRIDE, "git", "commit"])


def test_has_leading_override_true_past_other_leading_env_assignments():
    assert cs.has_leading_override(["FOO=1", cs.OVERRIDE, "git", "commit"])


def test_has_leading_override_false_when_not_leading():
    # OVERRIDE appears, but only after a non-assignment token has already started the
    # command — it is a message word / pathspec, not a leading env assignment.
    assert not cs.has_leading_override(["git", "commit", "-m", cs.OVERRIDE])


def test_has_leading_override_false_on_empty_segment():
    assert not cs.has_leading_override([])

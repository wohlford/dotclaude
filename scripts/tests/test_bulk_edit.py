"""Tests for scripts/lib/bulk_edit.py — the bulk-mechanical-edit safety kit.

Each property below was carried by at least one hand-written apply script and dropped by another:
the anchor-exactly-once refusal, the non-blank-line shape assertion, the syntax check, the
already-applied skip, the column limit on added lines, the rehearsed-copy comparison, and counts
predicted before the run. A row names the property it pins, and many refusal rows also assert
that nothing was written. Do not read that as a rule about every row — the whole-file
property, one bad file and nothing is written anywhere, is what matters, and it is pinned by
`test_one_bad_file_writes_nothing_anywhere` and by the campaign's `partial-write` row.
"""

from __future__ import annotations

import re
import stat
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import bulk_edit  # noqa: E402, I001

Edit = bulk_edit.Edit
Prediction = bulk_edit.Prediction

HOOK = "#!/usr/bin/env bash\nset -uo pipefail\n\nmain() {\n  :\n}\nmain\n"
GUARD = Edit("set -uo pipefail\n", 'set -uo pipefail\n[ -n "${SKIP:-}" ] && exit 0\n')
RESULT_RE = re.compile(
    r"^RESULT: (PASS|FAIL|ERROR) rc=[012] files=\d+ apply=\d+ already=\d+ "
    r"written=\d+ mode=(write|dry-run)$"
)


def make(root: Path, files: dict[str, str]) -> None:
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8", newline="") as handle:
            handle.write(text)


def read(root: Path, name: str) -> str:
    with (root / name).open(encoding="utf-8", newline="") as handle:
        return handle.read()


def last(report) -> str:
    line = report.text.splitlines()[-1]
    assert RESULT_RE.match(line), line
    return line


# ---------- applying ----------


def test_replacements_apply_exactly(tmp_path):
    make(tmp_path, {"doc.md": "one\ntwo\nthree\nfour\n"})
    edits = [Edit("two\n", "TWO\ntwo-b\n"), Edit("four\n", "FOUR\n")]
    report = bulk_edit.run(edits, ["doc.md"], root=tmp_path)
    assert (report.status, report.rc, report.written) == ("PASS", 0, 1)
    assert read(tmp_path, "doc.md") == "one\nTWO\ntwo-b\nthree\nFOUR\n"
    assert "apply=1 already=0 written=1 mode=write" in last(report)
    assert f"root={tmp_path.resolve()}" in report.text


def test_edits_splice_against_the_original_not_each_other(tmp_path):
    # Edit 1's new text contains edit 2's old anchor; applied in sequence with str.replace, edit 2
    # would land inside edit 1's insertion. By position, it lands on the original line.
    make(tmp_path, {"doc.md": "a\nb\n"})
    edits = [Edit("a\n", "a\nb-copy\n"), Edit("b\n", "B\n")]
    report = bulk_edit.run(edits, ["doc.md"], root=tmp_path)
    assert report.status == "PASS"
    assert read(tmp_path, "doc.md") == "a\nb-copy\nB\n"


def test_same_edits_apply_across_many_files(tmp_path):
    names = [f"hooks/h{i}.sh" for i in range(5)]
    make(tmp_path, dict.fromkeys(names, HOOK))
    report = bulk_edit.run([GUARD], names, root=tmp_path)
    assert (report.status, report.written) == ("PASS", 5)
    assert all(read(tmp_path, n).count("SKIP") == 1 for n in names)


def test_crlf_is_preserved(tmp_path):
    make(tmp_path, {"doc.txt": "a\r\nb\r\n"})
    report = bulk_edit.run([Edit("a\r\n", "A\r\n")], ["doc.txt"], root=tmp_path)
    assert report.status == "PASS"
    assert (tmp_path / "doc.txt").read_bytes() == b"A\r\nb\r\n"


def test_exec_bit_survives_the_write(tmp_path):
    make(tmp_path, {"h.sh": HOOK})
    (tmp_path / "h.sh").chmod(0o755)
    assert bulk_edit.run([GUARD], ["h.sh"], root=tmp_path).status == "PASS"
    assert stat.S_IMODE((tmp_path / "h.sh").stat().st_mode) == 0o755


def test_removal_that_keeps_its_anchor_applies(tmp_path):
    # new sits inside old, so both resolve before the edit; that is an unapplied removal.
    make(tmp_path, {"doc.md": "keep\ndrop\nend\n"})
    edits = [Edit("keep\ndrop\n", "keep\n")]
    report = bulk_edit.run(edits, ["doc.md"], root=tmp_path)
    assert report.status == "PASS"
    assert read(tmp_path, "doc.md") == "keep\nend\n"
    again = bulk_edit.run(edits, ["doc.md"], root=tmp_path)
    assert (again.status, again.written) == ("PASS", 0)
    assert "ALREADY" in again.text


# ---------- already applied ----------


def test_insertion_rerun_is_already_and_never_doubles(tmp_path):
    make(tmp_path, {"h.sh": HOOK})
    bulk_edit.run([GUARD], ["h.sh"], root=tmp_path)
    again = bulk_edit.run([GUARD], ["h.sh"], root=tmp_path)
    assert (again.status, again.written) == ("PASS", 0)
    assert "apply=0 already=1" in last(again)
    assert read(tmp_path, "h.sh").count("SKIP") == 1


def test_replacement_rerun_is_already(tmp_path):
    make(tmp_path, {"doc.md": "old line\n"})
    edits = [Edit("old line\n", "new line\n")]
    bulk_edit.run(edits, ["doc.md"], root=tmp_path)
    again = bulk_edit.run(edits, ["doc.md"], root=tmp_path)
    assert (again.status, again.written) == ("PASS", 0)
    assert "already=1" in last(again)


def test_killed_run_recovers_by_rerun(tmp_path):
    # A run killed after writing the first file: the re-run skips it and finishes the other.
    make(tmp_path, {"a.sh": HOOK, "b.sh": HOOK})
    make(tmp_path, {"a.sh": HOOK.replace(GUARD.old, GUARD.new)})
    report = bulk_edit.run([GUARD], ["a.sh", "b.sh"], root=tmp_path)
    assert "apply=1 already=1 written=1" in last(report)
    assert read(tmp_path, "b.sh").count("SKIP") == 1


@pytest.mark.parametrize(
    ("text", "edits"),
    [
        # An applied insertion beside an unapplied replacement: every old still resolves.
        (
            "set -uo pipefail\nGUARD=1\necho old\n",
            [
                Edit("set -uo pipefail\n", "set -uo pipefail\nGUARD=1\n"),
                Edit("echo old\n", "echo new\n"),
            ],
        ),
        # Two insertions, one made.
        ("A\na1\nB\n", [Edit("A\n", "A\na1\n"), Edit("B\n", "B\nb1\n")]),
    ],
)
def test_applied_insertion_beside_an_unapplied_edit_is_refused(tmp_path, text, edits):
    make(tmp_path, {"f.sh": text})
    report = bulk_edit.run(edits, ["f.sh"], root=tmp_path)
    assert (report.status, report.written) == ("FAIL", 0)
    assert "edit 1: applied" in report.text
    assert read(tmp_path, "f.sh") == text


def test_partial_application_is_refused(tmp_path):
    make(tmp_path, {"doc.md": "A\nb\n"})
    report = bulk_edit.run(
        [Edit("a\n", "A\n"), Edit("b\n", "B\n")], ["doc.md"], root=tmp_path
    )
    assert (report.status, report.rc) == ("FAIL", 1)
    assert "edit 1: applied" in report.text and "edit 2: unapplied" in report.text
    assert "edit 1: old x0 new x1" in report.text
    assert read(tmp_path, "doc.md") == "A\nb\n"


def test_old_and_new_both_present_apart_is_ambiguous(tmp_path):
    make(tmp_path, {"doc.md": "a\nb\n"})
    report = bulk_edit.run([Edit("a\n", "b\n")], ["doc.md"], root=tmp_path)
    assert report.status == "FAIL"
    assert "edit 1: ambiguous" in report.text
    assert read(tmp_path, "doc.md") == "a\nb\n"


def test_deletion_rerun_is_refused_loudly(tmp_path):
    make(tmp_path, {"doc.md": "x\ny\n"})
    edits = [Edit("x\n", "")]
    assert bulk_edit.run(edits, ["doc.md"], root=tmp_path).status == "PASS"
    again = bulk_edit.run(edits, ["doc.md"], root=tmp_path)
    assert again.status == "FAIL"
    assert "edit 1: missing" in again.text


def test_old_occurring_twice_is_ambiguous_not_already(tmp_path):
    # Final review, measured: `old` occurring twice beside its `new` occurring once used to read
    # as applied, so the file reported ALREADY and PASS for an edit that was never made.
    text = "#!/bin/bash\nexit 0\nexit 1\nexit 0\n"
    make(tmp_path, {"f.sh": text})
    report = bulk_edit.run([Edit("exit 0\n", "exit 1\n")], ["f.sh"], root=tmp_path)
    assert report.status == "FAIL"
    assert "edit 1: ambiguous" in report.text
    assert read(tmp_path, "f.sh") == text


def test_new_occurring_twice_is_ambiguous_not_applied(tmp_path):
    # Mirror of the above: `new` occurring twice used to read as unapplied, and the edit was
    # applied again, leaving it three times.
    text = "Y=1\nX=1\nY=1\n"
    make(tmp_path, {"f.sh": text})
    report = bulk_edit.run([Edit("X=1\n", "Y=1\n")], ["f.sh"], root=tmp_path)
    assert report.status == "FAIL"
    assert "edit 1: ambiguous" in report.text
    assert read(tmp_path, "f.sh") == text


# ---------- anchors ----------


@pytest.mark.parametrize(
    ("text", "why", "state"),
    [
        ("nothing here\n", "old x0", "missing"),
        # o=2, n=0: falls into "anything else -> ambiguous", not "missing" (final review ruling).
        ("a\nmid\na\n", "old x2", "ambiguous"),
        ("x a\n", "old x0", "missing"),  # present, but not at the start of a line
    ],
)
def test_anchor_must_resolve_once_at_a_line_start(tmp_path, text, why, state):
    make(tmp_path, {"doc.md": text})
    report = bulk_edit.run([Edit("a\n", "A\n")], ["doc.md"], root=tmp_path)
    assert report.status == "FAIL"
    assert why in report.text
    assert f"edit 1: {state}" in report.text
    assert read(tmp_path, "doc.md") == text


def test_an_indented_twin_is_not_a_second_anchor(tmp_path):
    hook = "#!/usr/bin/env bash\nif true; then\n  exit 0\nfi\nexit 0\n"
    make(tmp_path, {"h.sh": hook})
    report = bulk_edit.run(
        [Edit("exit 0\n", "exit 0\n# tail\n")], ["h.sh"], root=tmp_path
    )
    assert report.status == "PASS"
    assert read(tmp_path, "h.sh") == hook + "# tail\n"


def test_overlapping_anchors_are_refused(tmp_path):
    make(tmp_path, {"doc.md": "a\nb\nc\n"})
    edits = [Edit("a\nb\n", "AB\n"), Edit("b\nc\n", "BC\n")]
    report = bulk_edit.run(edits, ["doc.md"], root=tmp_path)
    assert report.status == "FAIL"
    assert "anchors overlap" in report.text
    assert read(tmp_path, "doc.md") == "a\nb\nc\n"


def test_one_bad_file_writes_nothing_anywhere(tmp_path):
    make(tmp_path, {"a.sh": HOOK, "b.sh": "#!/usr/bin/env bash\necho no anchor\n"})
    report = bulk_edit.run([GUARD], ["a.sh", "b.sh"], root=tmp_path)
    assert (report.status, report.written) == ("FAIL", 0)
    assert read(tmp_path, "a.sh") == HOOK
    assert "nothing written" in report.text


# ---------- syntax ----------


def test_edit_breaking_bash_is_refused(tmp_path):
    make(tmp_path, {"h.sh": HOOK})
    broken = Edit("set -uo pipefail\n", "set -uo pipefail\nif true; then\n")
    report = bulk_edit.run([broken], ["h.sh"], root=tmp_path)
    assert report.status == "FAIL"
    assert "breaks syntax" in report.text and "syntax=bash" in report.text
    assert read(tmp_path, "h.sh") == HOOK


def test_bash_is_detected_by_shebang_without_suffix(tmp_path):
    make(tmp_path, {"hook": HOOK})
    broken = Edit("set -uo pipefail\n", "set -uo pipefail\nif true; then\n")
    report = bulk_edit.run([broken], ["hook"], root=tmp_path)
    assert report.status == "FAIL"
    assert "syntax=bash" in report.text


def test_edit_breaking_python_is_refused(tmp_path):
    make(tmp_path, {"m.py": "X = 1\n"})
    report = bulk_edit.run([Edit("X = 1\n", "def f(:\n")], ["m.py"], root=tmp_path)
    assert report.status == "FAIL"
    assert "SyntaxError" in report.text and "syntax=python" in report.text
    assert read(tmp_path, "m.py") == "X = 1\n"


def test_file_already_broken_is_not_blamed_on_the_edit(tmp_path):
    make(tmp_path, {"m.py": "X = 1\ndef f(:\n"})
    report = bulk_edit.run([Edit("X = 1\n", "X = 2\n")], ["m.py"], root=tmp_path)
    assert report.status == "FAIL"
    assert "already broken before the edit" in report.text


def test_prose_is_reported_unchecked_not_checked(tmp_path):
    make(tmp_path, {"doc.md": "a\n"})
    report = bulk_edit.run([Edit("a\n", "if true; then\n")], ["doc.md"], root=tmp_path)
    assert report.status == "PASS"
    assert "syntax=unchecked" in report.text


def test_already_applied_file_that_no_longer_parses_is_refused(tmp_path):
    # Final review, measured: an ALREADY file was never parsed, so `if then` (invalid bash) read
    # as PASS with `syntax=bash` printed for a file the run never actually ran `bash -n` over.
    make(tmp_path, {"g.sh": "#!/bin/bash\nGUARD=1\nif then\n"})
    edits = [Edit("#!/bin/bash\n", "#!/bin/bash\nGUARD=1\n")]
    report = bulk_edit.run(edits, ["g.sh"], root=tmp_path)
    assert report.status == "FAIL"
    assert "already applied, but does not parse" in report.text
    assert "syntax=bash" in report.text


def test_classification_fail_prints_syntax_not_run(tmp_path):
    # No parse is ever attempted when the file's edit state itself is refused, so the printed
    # `syntax=` must say so rather than naming a kind nothing checked.
    text = "#!/bin/bash\nexit 0\nexit 1\nexit 0\n"
    make(tmp_path, {"h.sh": text})
    report = bulk_edit.run([Edit("exit 0\n", "exit 1\n")], ["h.sh"], root=tmp_path)
    assert report.status == "FAIL"
    assert "syntax=not-run" in report.text


def test_already_applied_clean_file_is_parsed_and_prints_its_kind(tmp_path):
    make(tmp_path, {"h.sh": HOOK.replace(GUARD.old, GUARD.new)})
    report = bulk_edit.run([GUARD], ["h.sh"], root=tmp_path)
    assert report.status == "PASS"
    assert "already=1" in report.text
    assert "syntax=bash" in report.text


# ---------- columns ----------


def test_column_limit_reads_the_value_given(tmp_path):
    make(tmp_path, {"doc.md": "a\n"})
    at_limit = Edit("a\n", "x" * 72 + "\n")
    report = bulk_edit.run([at_limit], ["doc.md"], root=tmp_path, max_columns=72)
    assert report.status == "PASS"
    assert "columns=72" in report.text
    make(tmp_path, {"doc.md": "a\n"})
    over = Edit("a\n", "x" * 73 + "\n")
    refused = bulk_edit.run([over], ["doc.md"], root=tmp_path, max_columns=72)
    assert refused.status == "FAIL"
    assert "over 72 columns" in refused.text
    assert read(tmp_path, "doc.md") == "a\n"


def test_pre_existing_long_lines_are_not_the_edits(tmp_path):
    long_line = "y" * 200 + "\n"
    make(tmp_path, {"doc.md": long_line + "a\n"})
    report = bulk_edit.run(
        [Edit("a\n", "A\n")], ["doc.md"], root=tmp_path, max_columns=72
    )
    assert report.status == "PASS"


def test_long_line_added_twice_counts_each_occurrence(tmp_path):
    make(tmp_path, {"doc.md": "a\n"})
    edits = [Edit("a\n", ("x" * 80 + "\n") * 2)]
    report = bulk_edit.run(edits, ["doc.md"], root=tmp_path, max_columns=72)
    assert report.status == "FAIL"
    assert "2 added line(s) over 72 columns" in report.text


def test_columns_unset_is_reported_unchecked(tmp_path):
    make(tmp_path, {"doc.md": "a\n"})
    report = bulk_edit.run([Edit("a\n", "x" * 300 + "\n")], ["doc.md"], root=tmp_path)
    assert report.status == "PASS"
    assert "columns=unchecked" in report.text


# ---------- predictions ----------


def test_correct_predictions_pass(tmp_path):
    make(tmp_path, {"doc.md": "alpha\nbeta\n"})
    preds = [Prediction("doc.md", "beta", before=1, after=2)]
    edits = [Edit("alpha\n", "alpha beta\n")]
    report = bulk_edit.run(edits, ["doc.md"], root=tmp_path, predictions=preds)
    assert report.status == "PASS"
    assert "predictions=1" in report.text


@pytest.mark.parametrize(("before", "after"), [(0, 2), (1, 1)])
def test_wrong_prediction_is_refused_and_writes_nothing(tmp_path, before, after):
    make(tmp_path, {"doc.md": "alpha\nbeta\n"})
    preds = [Prediction("doc.md", "beta", before=before, after=after)]
    edits = [Edit("alpha\n", "alpha beta\n")]
    report = bulk_edit.run(edits, ["doc.md"], root=tmp_path, predictions=preds)
    assert report.status == "FAIL"
    assert "before=1 after=2" in report.text
    assert read(tmp_path, "doc.md") == "alpha\nbeta\n"


def test_prediction_counts_a_phrase_across_a_wrap(tmp_path):
    make(tmp_path, {"doc.md": "a\n"})
    edits = [Edit("a\n", "can pass\nat all\n")]
    preds = [Prediction("doc.md", "pass\nat all", before=0, after=1)]
    report = bulk_edit.run(edits, ["doc.md"], root=tmp_path, predictions=preds)
    assert report.status == "PASS"


def test_prediction_on_an_already_applied_file_checks_after_only(tmp_path):
    make(tmp_path, {"doc.md": "new\n"})
    preds = [Prediction("doc.md", "new", before=0, after=1)]
    report = bulk_edit.run(
        [Edit("old\n", "new\n")], ["doc.md"], root=tmp_path, predictions=preds
    )
    assert report.status == "PASS"
    wrong = [Prediction("doc.md", "new", before=0, after=5)]
    refused = bulk_edit.run(
        [Edit("old\n", "new\n")], ["doc.md"], root=tmp_path, predictions=wrong
    )
    assert refused.status == "FAIL"


def test_prediction_outside_the_file_set_is_an_error(tmp_path):
    make(tmp_path, {"doc.md": "a\n"})
    preds = [Prediction("other.md", "a", before=1, after=0)]
    report = bulk_edit.run(
        [Edit("a\n", "b\n")], ["doc.md"], root=tmp_path, predictions=preds
    )
    assert (report.status, report.rc) == ("ERROR", 2)
    assert read(tmp_path, "doc.md") == "a\n"


# ---------- rehearsal match ----------


def test_match_root_accepts_an_identical_rehearsal(tmp_path):
    real, clone = tmp_path / "real", tmp_path / "clone"
    make(real, {"d/doc.md": "a\n"})
    make(clone, {"d/doc.md": "A\n"})
    report = bulk_edit.run(
        [Edit("a\n", "A\n")], ["d/doc.md"], root=real, match_root=clone
    )
    assert report.status == "PASS"
    assert read(real, "d/doc.md") == "A\n"


def test_match_root_refuses_an_already_applied_file_that_drifted(tmp_path):
    real, clone = tmp_path / "real", tmp_path / "clone"
    make(real, {"doc.md": "A\nhand-edited extra\n"})
    make(clone, {"doc.md": "A\n"})
    report = bulk_edit.run(
        [Edit("a\n", "A\n")], ["doc.md"], root=real, match_root=clone
    )
    assert report.status == "FAIL"
    assert "already applied, but differs from rehearsed" in report.text


@pytest.mark.parametrize("rehearsed", ["A-different\n", None])
def test_match_root_refuses_a_differing_or_missing_rehearsal(tmp_path, rehearsed):
    real, clone = tmp_path / "real", tmp_path / "clone"
    make(real, {"doc.md": "a\n"})
    clone.mkdir()
    if rehearsed is not None:
        make(clone, {"doc.md": rehearsed})
    report = bulk_edit.run(
        [Edit("a\n", "A\n")], ["doc.md"], root=real, match_root=clone
    )
    assert report.status == "FAIL"
    assert "computed result: " in report.text
    assert read(real, "doc.md") == "a\n"


@pytest.mark.parametrize("cut", ["anchor\nGUARD\n", "anchor\nGU"])
def test_match_root_catches_a_file_cut_off_by_a_killed_write(tmp_path, cut):
    # Final re-review, measured: without --match-root the first cut reads ALREADY and the
    # second is applied again, corrupt — both PASS. The rehearsed clone is the recovery.
    real, clone = tmp_path / "real", tmp_path / "clone"
    make(real, {"h.txt": cut})
    make(clone, {"h.txt": "anchor\nGUARD\nrest\n"})
    edits = [Edit("anchor\n", "anchor\nGUARD\n")]
    report = bulk_edit.run(edits, ["h.txt"], root=real, match_root=clone)
    assert (report.status, report.written) == ("FAIL", 0)
    assert read(real, "h.txt") == cut


# ---------- modes and refusals of the run itself ----------


def test_dry_run_judges_and_writes_nothing(tmp_path):
    make(tmp_path, {"doc.md": "a\n"})
    report = bulk_edit.run(
        [Edit("a\n", "A\n")], ["doc.md"], root=tmp_path, dry_run=True
    )
    assert report.status == "PASS"
    assert "apply=1 already=0 written=0 mode=dry-run" in last(report)
    assert read(tmp_path, "doc.md") == "a\n"


@pytest.mark.parametrize(
    ("edits", "files", "why"),
    [
        ([], ["doc.md"], "no edits"),
        ([Edit("a\n", "A\n")], [], "no files"),
        ([Edit("a", "A\n")], ["doc.md"], "old must be"),
        ([Edit("", "A\n")], ["doc.md"], "old must be"),
        ([Edit("a\n", "A")], ["doc.md"], "new must be"),
        ([Edit("a\n", "a\n")], ["doc.md"], "identical"),
        ([Edit("a\n", "A\n")], ["doc.md", "./doc.md"], "listed twice"),
        ([Edit("a\n", "A\n")], ["../outside.md"], "outside root"),
    ],
)
def test_malformed_runs_are_errors(tmp_path, edits, files, why):
    make(tmp_path, {"doc.md": "a\n"})
    report = bulk_edit.run(edits, files, root=tmp_path)
    assert (report.status, report.rc) == ("ERROR", 2)
    assert why in report.text
    assert read(tmp_path, "doc.md") == "a\n"


def test_unreadable_file_is_an_error(tmp_path):
    (tmp_path / "bin.dat").write_bytes(b"\xff\xfe\n")
    report = bulk_edit.run([Edit("a\n", "A\n")], ["bin.dat"], root=tmp_path)
    assert report.status == "ERROR"
    assert "cannot read" in report.text
    assert "syntax=not-run" in report.text


def test_file_changed_between_judging_and_writing_stops(tmp_path, monkeypatch):
    make(tmp_path, {"doc.md": "a\n"})
    real_read = bulk_edit._read
    calls = {"n": 0}

    def racing_read(path):
        calls["n"] += 1
        return "a\nsomeone else\n" if calls["n"] == 2 else real_read(path)

    monkeypatch.setattr(bulk_edit, "_read", racing_read)
    report = bulk_edit.run([Edit("a\n", "A\n")], ["doc.md"], root=tmp_path)
    assert (report.status, report.written) == ("FAIL", 0)
    assert "changed since it was read" in report.text
    assert real_read(tmp_path / "doc.md") == "a\n"


def test_deletion_in_an_empty_file_is_refused_not_already(tmp_path):
    make(tmp_path, {"empty.md": ""})
    report = bulk_edit.run([Edit("x\n", "")], ["empty.md"], root=tmp_path)
    assert report.status == "FAIL"
    assert "edit 1: missing" in report.text


def test_prediction_with_an_empty_needle_is_an_error(tmp_path):
    make(tmp_path, {"doc.md": "a\n"})
    preds = [Prediction("doc.md", "", before=0, after=0)]
    report = bulk_edit.run(
        [Edit("a\n", "b\n")], ["doc.md"], root=tmp_path, predictions=preds
    )
    assert report.status == "ERROR"
    assert "empty needle" in report.text


def test_root_that_is_not_a_directory_is_an_error(tmp_path):
    make(tmp_path, {"doc.md": "a\n"})
    report = bulk_edit.run([Edit("a\n", "b\n")], ["doc.md"], root=tmp_path / "doc.md")
    assert report.status == "ERROR"
    assert "root is not a directory" in report.text


def test_write_that_does_not_read_back_is_an_error(tmp_path, monkeypatch):
    make(tmp_path, {"doc.md": "a\n"})
    real_write = bulk_edit._write
    monkeypatch.setattr(
        bulk_edit, "_write", lambda path, text: real_write(path, "mangled\n")
    )
    report = bulk_edit.run([Edit("a\n", "A\n")], ["doc.md"], root=tmp_path)
    assert (report.status, report.rc) == ("ERROR", 2)
    assert "re-read differs" in report.text


def test_write_failure_is_an_error(tmp_path):
    make(tmp_path, {"doc.md": "a\n"})
    (tmp_path / "doc.md").chmod(0o444)
    try:
        report = bulk_edit.run([Edit("a\n", "A\n")], ["doc.md"], root=tmp_path)
    finally:
        (tmp_path / "doc.md").chmod(0o644)
    assert (report.status, report.rc) == ("ERROR", 2)
    assert "write failed" in report.text


def test_main_help_is_not_an_error(capsys):
    assert bulk_edit.main(["x", "--help"], [Edit("a\n", "A\n")], ["doc.md"]) == 0
    assert "--match-root" in capsys.readouterr().out


# ---------- the engine's own self-check ----------


def test_shape_check_accepts_the_declared_edit():
    edits = [Edit("b\n", "B\nB2\n")]
    assert bulk_edit._check_shape("a\nb\nc\n", "a\nB\nB2\nc\n", edits, [(2, 4)]) is None


def test_a_repeated_line_before_the_anchor_is_not_mistaken_for_it(tmp_path):
    # The removed `x` is the SECOND one; subtracting old's lines from the first equal copy
    # instead reads the survivors as reordered and refuses a correct edit (plan review, measured).
    make(tmp_path, {"d.md": "x\ny\nx\nz\n"})
    report = bulk_edit.run([Edit("x\nz\n", "N\n")], ["d.md"], root=tmp_path)
    assert report.status == "PASS"
    assert read(tmp_path, "d.md") == "x\ny\nN\n"


@pytest.mark.parametrize(
    ("after", "why"),
    [
        (
            "a\nB\nc\n",
            "unexpected line delta",
        ),  # the edit reached less far than declared
        (
            "a\nB\nB2\n",
            "unexpected line delta",
        ),  # the edit reached further than declared
        ("c\nB\nB2\na\n", "reordered"),  # same lines, surviving ones out of order
    ],
)
def test_shape_check_refuses_a_wrong_result(after, why):
    edits = [Edit("b\n", "B\nB2\n")]
    assert why in bulk_edit._check_shape("a\nb\nc\n", after, edits, [(2, 4)])


def test_shape_self_check_failure_is_an_error_and_writes_nothing(tmp_path, monkeypatch):
    make(tmp_path, {"doc.md": "a\nb\n"})
    monkeypatch.setattr(bulk_edit, "_splice", lambda text, edits, spans: "A\nA\nb\n")
    report = bulk_edit.run([Edit("a\n", "A\n")], ["doc.md"], root=tmp_path)
    assert (report.status, report.rc) == ("ERROR", 2)
    assert "shape:" in report.text
    assert read(tmp_path, "doc.md") == "a\nb\n"


# ---------- main() ----------


def test_main_reads_root_dry_run_and_match_root(tmp_path, capsys):
    real, clone = tmp_path / "real", tmp_path / "clone"
    make(real, {"doc.md": "a\n"})
    make(clone, {"doc.md": "A\n"})
    edits, files = [Edit("a\n", "A\n")], ["doc.md"]
    rc = bulk_edit.main(["x", "--root", str(real), "--dry-run"], edits, files)
    assert rc == 0 and read(real, "doc.md") == "a\n"
    assert capsys.readouterr().out.rstrip().endswith("mode=dry-run")
    make(clone, {"doc.md": "not what was rehearsed\n"})
    rc = bulk_edit.main(
        ["x", "--root", str(real), "--match-root", str(clone)], edits, files
    )
    assert rc == 1 and read(real, "doc.md") == "a\n"
    make(clone, {"doc.md": "A\n"})
    rc = bulk_edit.main(
        ["x", "--root", str(real), "--match-root", str(clone)], edits, files
    )
    assert rc == 0 and read(real, "doc.md") == "A\n"


def test_main_root_defaults_to_the_working_directory(tmp_path, monkeypatch):
    make(tmp_path, {"doc.md": "a\n"})
    monkeypatch.chdir(tmp_path)
    assert bulk_edit.main(["x"], [Edit("a\n", "A\n")], ["doc.md"]) == 0
    assert read(tmp_path, "doc.md") == "A\n"


def test_main_bad_flag_is_an_error_verdict(capsys):
    rc = bulk_edit.main(["x", "--no-such-flag"], [Edit("a\n", "A\n")], ["doc.md"])
    assert rc == 2
    out = capsys.readouterr().out
    assert out.strip(), "a usage error must still print a verdict line"
    assert out.strip().splitlines()[-1].startswith("RESULT: ERROR rc=2")


def test_module_docstring_example_runs(tmp_path):
    # The documented usage, executed as written against a scratch tree; only the import path
    # is re-pointed at this checkout's copy.
    doc = bulk_edit.__doc__
    example = doc[doc.index("    import sys") : doc.index("Run it with")]
    source = "\n".join(line[4:] for line in example.splitlines())
    lib = 'Path.home() / ".claude" / "scripts" / "lib"'
    assert lib in source
    source = source.replace(lib, repr(str(Path(bulk_edit.__file__).parent)))
    make(tmp_path, {"hooks/a.sh": HOOK, "hooks/b.sh": HOOK})
    script = tmp_path / "apply_example.py"
    script.write_text(source)
    proc = subprocess.run(
        [sys.executable, str(script), "--root", str(tmp_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert proc.stdout.splitlines()[-1].startswith("RESULT: PASS rc=0 files=2 apply=2")
    assert read(tmp_path, "hooks/a.sh").count("GUARD=1") == 1

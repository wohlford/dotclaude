#!/usr/bin/env python3
"""Mutation campaign for scripts/lib/bulk_edit.py, driven by scripts/lib/mutate.py.

Deliberately NOT named `test_*`: it mutates a tracked file in place, so pytest must not collect
it. Run it on demand — `./scripts/tests/mutate_bulk_edit.py` — and never while editing the
subject, since the restore would clobber your edits.

Every row disables ONE refusal or one piece of plumbing and requires
`scripts/tests/test_bulk_edit.py` to notice. The kit exists because hand-written apply scripts
kept dropping exactly these checks without anything going red, so a survivor here means the suite
has the same blind spot the scripts had: strengthen the suite row, never reword the mutation.

Two pieces of the subject are deliberately NOT mutated, and why:

* `_write`'s `newline=""`. On a POSIX host text mode writes `\\n` untranslated, so removing it
  changes nothing observable here; it is kept for hosts where it would. The READ side's
  `newline=""` is mutated below — dropping it turns CRLF into LF before the anchors are matched.
* `SYNTAX_TIMEOUT`. It bounds a `bash -n` that never returns, which no fixture can produce
  cheaply; it changes an outcome only on a hang.
A third piece has no row because it no longer exists: `_spans`'s per-needle resolution check was
REMOVED during the final review's fix, not excluded from this list. Measured then: mutating it
SURVIVED, because both of `_spans`'s callers reach it only after `_edit_state` has confirmed the
same needle resolves exactly once. Kept, it would have been a row that can never fail.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "scripts" / "lib"))
import mutate  # noqa: E402

SUBJECT = REPO / "scripts" / "lib" / "bulk_edit.py"
SUITE = [
    sys.executable,
    "-m",
    "pytest",
    "-q",
    str(REPO / "scripts" / "tests" / "test_bulk_edit.py"),
]

M = mutate.Mutation

MUTATIONS = [
    # ---- anchors: exactly once, at a line start, apart
    M(
        "mid-line-anchor-allowed: an anchor inside a line resolves",
        '        if pos == 0 or text[pos - 1] == "\\n":',
        "        if True:",
    ),
    M(
        "overlap-allowed: two anchors sharing lines both resolve",
        "        left[1] > right[0] for left, right in zip(ordered, ordered[1:], strict=False)",
        "        False for left, right in zip(ordered, ordered[1:], strict=False)",
    ),
    M(
        "empty-needle-resolves: an empty file reads as an applied deletion",
        "    if not needle:\n        return 0",
        "    if False:\n        return 0",
    ),
    # ---- already applied: each edit on its own, then all must agree
    M(
        "any-applied-is-already: one applied edit skips a file whose sibling is not applied",
        '    if all(s == "applied" for s in states):',
        '    if any(s == "applied" for s in states):',
    ),
    M(
        "any-unapplied-is-apply: an applied insertion beside an unapplied edit goes in twice",
        '    if all(s == "unapplied" for s in states):',
        '    if any(s == "unapplied" for s in states):',
    ),
    M(
        "overlap-unchecked-at-classify: overlapping anchors reach the splice",
        "        if _spans(text, [e.old for e in edits]) is None:",
        "        if False:",
    ),
    M(
        "applied-insertion-unrecognised: an insertion's re-run is not called applied",
        "        if n_start <= o_start and o_end <= n_end:",
        "        if False:",
    ),
    M(
        "anchored-removal-refused: a removal that keeps its anchor is called ambiguous",
        "        if o_start <= n_start and n_end <= o_end:",
        "        if False:",
    ),
    M(
        "applied-replacement-unrecognised: a re-run over an applied replacement FAILs",
        '    if o == 0 and n == 1:\n        return "applied"',
        '    if o == 0 and n == 1:\n        return "missing"',
    ),
    M(
        "unapplied-read-as-applied: an edit still needed is skipped as already made",
        '    if o == 1 and n == 0:\n        return "unapplied"',
        '    if o == 1 and n == 0:\n        return "applied"',
    ),
    M(
        "ambiguous-read-as-unapplied: an edit whose spans neither nest is applied",
        "        if o_start <= n_start and n_end <= o_end:\n"
        '            return "unapplied"\n'
        '        return "ambiguous"',
        "        if o_start <= n_start and n_end <= o_end:\n"
        '            return "unapplied"\n'
        '        return "unapplied"',
    ),
    M(
        "missing-read-as-unapplied: an edit whose anchors are both gone is reported as unapplied",
        '    if o == 0 and n == 0:\n        return "missing"',
        '    if o == 0 and n == 0:\n        return "unapplied"',
    ),
    M(
        "count-two-reads-as-unapplied: an anchor occurring 2+ times falls into unapplied, not "
        "ambiguous (final review: the ruling this closes, proven non-inert)",
        '    if o == 0 and n == 0:\n        return "missing"\n    return "ambiguous"',
        '    if o == 0 and n == 0:\n        return "missing"\n    return "unapplied"',
    ),
    # ---- the splice and its self-check
    M(
        "splice-ascending: offsets drift once an earlier edit changes length",
        "sorted(zip(spans, edits, strict=True), reverse=True)",
        "zip(spans, edits, strict=True)",
    ),
    M(
        "shape-delta-ignored: a result with lost or extra lines is accepted",
        "    if expected != actual:",
        "    if False:",
    ),
    M(
        "shape-order-ignored: a result with reordered surviving lines is accepted",
        "    if not all(line in remaining for line in survivors):",
        "    if False:",
    ),
    M(
        "shape-survivors-include-removed: the removed span is counted as surviving",
        "        kept.append(before[cursor:start])",
        "        kept.append(before[cursor:end])",
    ),
    M(
        "shape-refusal-dropped: the self-check's verdict is computed and discarded",
        "    if shape:\n        return FileResult(name, ERROR, shape, kind), None",
        "    if False:\n        return FileResult(name, ERROR, shape, kind), None",
    ),
    # ---- syntax
    M(
        "broken-edit-allowed: an edit that breaks the parser is written",
        "    if after_error:",
        "    if False:",
    ),
    M(
        "broken-original-blamed-on-edit: a pre-existing breakage is not named as such",
        "        if before_error:",
        "        if False:",
    ),
    M(
        "bash-rc-ignored: bash -n's refusal is discarded",
        "        if proc.returncode != 0:",
        "        if False:",
    ),
    M(
        "bash-shebang-ignored: a hook without .sh goes unchecked",
        '    if path.suffix == ".sh" or (first.startswith("#!") and "bash" in first):',
        '    if path.suffix == ".sh":',
    ),
    M(
        "python-unchecked: Python files are never parsed",
        '    if path.suffix == ".py" or (first.startswith("#!") and "python" in first):',
        "    if False:",
    ),
    M(
        "already-not-parsed-allowed: an ALREADY file that no longer parses is accepted",
        "        if error:",
        "        if False:",
    ),
    M(
        "refused-file-prints-a-kind-it-never-parsed: a classification FAIL prints a guessed "
        "syntax kind instead of not-run",
        '        return FileResult(name, FAIL, detail, "not-run"), None',
        "        return FileResult(name, FAIL, detail, kind), None",
    ),
    # ---- columns
    M(
        "columns-ignored: an over-long added line is written",
        "        if long:",
        "        if False:",
    ),
    M(
        "columns-off-by-one: a line exactly at the limit is refused",
        "    return sorted(line for line in gained.elements() if len(line) > limit)",
        "    return sorted(line for line in gained.elements() if len(line) >= limit)",
    ),
    M(
        "columns-dedupe-occurrences: two copies of one over-long gained line count as one",
        "    return sorted(line for line in gained.elements() if len(line) > limit)",
        "    return sorted(set(line for line in gained.elements() if len(line) > limit))",
    ),
    M(
        "columns-judge-old-lines: a pre-existing long line is blamed on the edit",
        "    gained = Counter(_lines(after)) - Counter(_lines(before))",
        "    gained = Counter(_lines(after))",
    ),
    # ---- predictions
    M(
        "prediction-before-ignored: only the after count is compared",
        "        if (got_before, got_after) != (pred.before, pred.after):",
        "        if got_after != pred.after:",
    ),
    M(
        "already-prediction-ignored: an applied file's after count is never compared",
        "            if got != pred.after:",
        "            if False:",
    ),
    M(
        "prediction-outside-set-ignored: a prediction naming no listed file is dropped",
        "        if path not in targets:",
        "        if False:",
    ),
    M(
        "prediction-empty-needle-allowed: an empty needle counts every position",
        "        if not pred.needle:",
        "        if False:",
    ),
    # ---- rehearsal match (one shared helper now serves the ALREADY and APPLY branches)
    M(
        "match-root-ignored: a result differing from the rehearsal — ALREADY or APPLY — is "
        "written",
        "    if rehearsed != text:",
        "    if False:",
    ),
    # ---- nothing written unless everything passed
    M(
        "partial-write: files that passed are written while another FAILed",
        "    if any(r.status == FAIL for r in results):",
        "    if False:",
    ),
    M(
        "error-not-aggregated: an unreadable file does not make the run an ERROR",
        "    if any(r.status == ERROR for r in results):",
        "    if False:",
    ),
    M(
        "dry-run-writes: --dry-run writes the files",
        "    if dry_run:\n",
        "    if False:\n",
    ),
    M(
        "race-ignored: a file changed after judging is overwritten",
        "            if _read(path) != original:",
        "            if False:",
    ),
    M(
        "readback-ignored: a write that did not land reads as PASS",
        "            if _read(path) != computed:",
        "            if False:",
    ),
    M(
        "read-translates-newlines: CRLF is folded to LF before matching",
        '    with path.open(encoding="utf-8", newline="") as handle:',
        '    with path.open(encoding="utf-8") as handle:',
    ),
    # ---- malformed runs
    M(
        "no-edits-allowed: an empty edit list runs",
        '    if not edits:\n        return "no edits given"',
        '    if False:\n        return "no edits given"',
    ),
    M(
        "no-files-allowed: an empty file list reports PASS over nothing",
        "    if not files:",
        "    if False:",
    ),
    M(
        "old-without-newline-allowed: a partial-line old is accepted",
        '        if not edit.old or not edit.old.endswith("\\n"):',
        "        if not edit.old:",
    ),
    M(
        "new-without-newline-allowed: a partial-line new is accepted",
        '        if edit.new and not edit.new.endswith("\\n"):',
        "        if False:",
    ),
    M(
        "no-op-edit-allowed: old == new is accepted",
        "        if edit.old == edit.new:",
        "        if False:",
    ),
    M(
        "outside-root-allowed: a path escaping root is edited",
        "        if not path.is_relative_to(base):",
        "        if False:",
    ),
    M(
        "duplicate-file-allowed: one file listed twice is judged twice",
        "        if path in targets:",
        "        if False:",
    ),
    M(
        "root-not-dir-allowed: a file given as root is not refused up front",
        "    if not base.is_dir():",
        "    if False:",
    ),
    # ---- main() plumbing: each flag must be READ, not defaulted
    M(
        "root-flag-dropped",
        "        root=args.root,",
        '        root=".",',
    ),
    M(
        "match-root-flag-dropped",
        "        match_root=args.match_root,",
        "        match_root=None,",
    ),
    M(
        "dry-run-flag-dropped",
        "        dry_run=args.dry_run,",
        "        dry_run=False,",
    ),
    M(
        "usage-error-no-verdict: a bad flag prints no RESULT line",
        '        print("RESULT: ERROR rc=2 files=0 apply=0 already=0 written=0 mode=usage")',
        "        pass",
    ),
    M(
        "help-is-an-error: --help returns 2",
        "        if exc.code == 0:\n            return 0",
        "        if False:\n            return 0",
    ),
]


def main() -> int:
    report = mutate.run(SUBJECT, SUITE, MUTATIONS, cwd=str(REPO))
    print(report.text)
    return report.rc


if __name__ == "__main__":
    sys.exit(main())

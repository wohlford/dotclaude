"""Apply whole-line OLD -> NEW edits across files, proving every edit safe before writing any.

A bulk mechanical edit — one guard inserted into 25 hook scripts, four revisions of one skill —
kept being applied by a throwaway script, and each re-derivation carried a different subset of
the checks that make such an edit trustworthy. This is the same drift `mutate.py` ended for
mutation harnesses, so the checks live here once and a per-change script supplies only its
`Edit` list.

Usage (import form; the per-change script lives outside the repo, e.g. in a scratchpad):

    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path.home() / ".claude" / "scripts" / "lib"))
    import bulk_edit

    EDITS = [bulk_edit.Edit("set -uo pipefail\\n", "set -uo pipefail\\nGUARD=1\\n")]
    FILES = ["hooks/a.sh", "hooks/b.sh"]
    PREDICTIONS = [bulk_edit.Prediction("hooks/a.sh", "GUARD=1", before=0, after=1)]
    sys.exit(bulk_edit.main(sys.argv, EDITS, FILES, predictions=PREDICTIONS, max_columns=100))

Run it with `--root <scratch clone> --dry-run` to rehearse, then `--root <clone>`, then against
the real checkout with `--match-root <clone>`, which refuses unless every result equals the copy
you rehearsed. Read the verdict from the last line: `RESULT: PASS|FAIL|ERROR rc=0|1|2 …`.

## Validate everything, then write — so there is nothing to revert

Every check runs on the computed text in memory, for every file, before the first write; any
refusal writes nothing. That is why this module has no restore machinery where `mutate.py` needs
a signal handler and a backup sidecar: `mutate.py` must break its subject to test it, and this
module never writes a result it has not already judged.

## Whole-line edits, applied by position against the ORIGINAL text

`old` is non-empty and ends with a newline, `new` is empty or ends with one, and every match must
start a line. Each `old` must occur exactly once in the original — zero and two are both refused,
since neither is visible afterwards — and matched spans must not overlap. Replacements are then
spliced by offset in one pass, so no edit can match text another edit introduced. What this does
not cover, deliberately: an edit inside a line. That is `sed`'s job.

## Already applied is a SAFETY property, not a convenience

An insertion's `new` contains its own anchor, so re-running it over an applied file still finds
the anchor exactly once and silently inserts the text a second time — measured on the guard shape
this module was built for. So EACH EDIT is classified on its own, from `o`/`n`, the COUNT of
line-start occurrences of its `old`/`new` (an empty `new` has `n = 0`):

* `o == 1, n == 0` -> unapplied; `o == 0, n == 1` -> applied;
* `o == 1, n == 1` -> applied when `old` sits inside `new` (an insertion already made), unapplied
  when `new` sits inside `old` (a removal that keeps its anchor, not yet made), else ambiguous;
* `o == 0, n == 0` -> missing; anything else — a count of two or more on either side — is
  ambiguous too, since neither a doubled anchor nor a doubled new text says which copy is the
  edit's. Measured before this rule (final review): an `old` occurring twice beside its `new`
  occurring once read as applied, so the file reported ALREADY and PASS for an edit never
  made; the mirror, a `new` occurring twice, read as unapplied and was applied again.

"Ambiguous" bites on a FIRST run too: an edit whose `new` already resolves elsewhere in the
file — including a chained rename, where one edit's `new` is another's `old` — is refused before
anything is written. Split such a change into two runs.

The file is APPLY only when EVERY edit is unapplied (and their anchors do not overlap), ALREADY
only when every edit is applied, and refused otherwise, naming each edit's state. Deciding over
the whole edit list at once is the trap: a file carrying one insertion and lacking a sibling
edit still has every `old` resolving, so it reads as unapplied and the insertion goes in twice
(caught in plan review, measured).

A pure deletion (`new` empty) can never be recognised as applied, so re-running one is refused
loudly. That is the accepted cost of never guessing. The rule is also what makes a run killed
mid-write recoverable — the re-run skips the files already written and finishes the rest — but
only when every edit is a replacement or insertion with a `new` of its own. A deletion, or two
edits sharing one `new`, cannot be recognised as applied, so the written files FAIL loudly on the
re-run and recovery is by hand.

One further gap, left deliberately open: `_write` truncates the file in place before writing the
replacement text, so a process killed DURING one file's write can leave that file empty or
partial — and a re-run does NOT reliably notice. An empty file reads as `missing` and FAILs,
but a file cut off just after the new text reads as ALREADY and PASSes, and one cut off
mid-line can read as unapplied and be written again, corrupt, with a PASS (all measured in
final review). So after any killed run, restore the files from version control, or re-run with
`--match-root` pointed at the rehearsed clone, which compares ALREADY files as well as applied
ones. (Atomic writes, e.g. write-a-temp-and-rename, are deliberately deferred.)

## The shape check guards this module, not the caller

Given the rules above, the non-blank-line multiset of a correct result is fully determined:
`lines outside the matched spans + lines(new)`, with every surviving line in order. A correct
splice always satisfies it, so its job is to catch a defect in the splice itself: a result that
loses, duplicates or reorders NON-BLANK lines is an ERROR, and nothing is written; blank lines
are outside its view. What it cannot see, by
construction: a correct `new` block placed at the WRONG position among unchanged lines keeps both
the multiset and the survivors' order. Only exact-output rows in the suite pin placement.

## The syntax checks run THIS machine's interpreters, not the file's own

`compile()` is called in the process running this module, and `bash -n` resolves `bash` from
PATH; neither is the interpreter a file's shebang names. So a file written against a different
Python, or a different bash, is only checked against the grammar the local one accepts —
measured in review: a `#!/bin/bash` script using syntax this machine's bash 3.2 rejects passes
here, because PATH bash is 5.x. A real gap for cross-version work, accepted rather than hidden.
"""

from __future__ import annotations

import argparse
import subprocess
from collections import Counter
from pathlib import Path
from typing import NamedTuple, Sequence

STATUSES = ("PASS", "FAIL", "ERROR")

APPLY = "APPLY"
ALREADY = "ALREADY"
FAIL = "FAIL"
ERROR = "ERROR"

# Bounds a `bash -n` that never returns. Measured parse times are milliseconds; this only turns a
# hang into a refusal instead of a run that never reaches its verdict.
SYNTAX_TIMEOUT = 30


class Edit(NamedTuple):
    """One whole-line replacement: `old` must occur exactly once, at the start of a line."""

    old: str
    new: str


class Prediction(NamedTuple):
    """Literal, NON-overlapping (`str.count`) counts of `needle` in `path`, written first."""

    path: str
    needle: str
    before: int
    after: int


class FileResult(NamedTuple):
    """The verdict for one file: APPLY, ALREADY, FAIL or ERROR, with the reason."""

    path: str
    status: str
    detail: str
    syntax: str


class Report(NamedTuple):
    """The run's verdict, per-file results, and the text to print."""

    status: str
    rc: int
    files: tuple[FileResult, ...]
    written: int
    text: str


def _read(path: Path) -> str:
    """Read `path` as UTF-8, preserving its newlines exactly (CRLF survives)."""
    with path.open(encoding="utf-8", newline="") as handle:
        return handle.read()


def _write(path: Path, text: str) -> None:
    """Truncate and rewrite `path` in place, which keeps its mode bits."""
    with path.open("w", encoding="utf-8", newline="") as handle:
        handle.write(text)


def _occurrences(text: str, needle: str) -> list[int]:
    """Every offset at which `needle` starts a LINE, overlapping matches included.

    Only line starts count, so an indented twin of a line-start anchor (`fi`, `exit 0`) is not
    a second match and cannot make the anchor read as ambiguous (plan review, measured).
    """
    hits = []
    pos = text.find(needle)
    while pos != -1:
        if pos == 0 or text[pos - 1] == "\n":
            hits.append(pos)
        pos = text.find(needle, pos + 1)
    return hits


def _spans(text: str, needles: Sequence[str]) -> list[tuple[int, int]] | None:
    """Return one span per needle, or None if any two overlap.

    Every caller reaches this only after `_edit_state` has already confirmed each needle here
    resolves exactly once: `_classify` calls this with `[e.old for e in edits]` only once every
    edit's state is 'unapplied' (o == 1), and `_judge` calls it again only for a file `_classify`
    already put in that state. A per-needle resolution check used to live here too, doing that
    same work a second time; mutating it proved the second copy unreachable, so it was removed
    rather than kept as an untestable no-op.
    """
    spans = []
    for needle in needles:
        hits = _occurrences(text, needle)
        spans.append((hits[0], hits[0] + len(needle)))
    ordered = sorted(spans)
    if any(
        left[1] > right[0] for left, right in zip(ordered, ordered[1:], strict=False)
    ):
        return None
    return spans


def _counts(text: str, edits: Sequence[Edit]) -> str:
    """Name every edit's old/new occurrence counts, for a refusal the reader can act on."""
    return ", ".join(
        f"edit {i}: old x{len(_occurrences(text, e.old))} new x"
        f"{len(_occurrences(text, e.new)) if e.new else '-'}"
        for i, e in enumerate(edits, 1)
    )


def _count(text: str, needle: str) -> int:
    """Line-start occurrences of `needle`; an empty `new` (a deletion) never resolves as present.

    In an EMPTY file `needle=""` would otherwise match once at offset 0 and read as an applied
    deletion of text that was never there.
    """
    if not needle:
        return 0
    return len(_occurrences(text, needle))


def _edit_state(text: str, edit: Edit) -> str:
    """Classify ONE edit from COUNTS of line-start occurrences: `o` for `old`, `n` for `new`.

    Only the o == 1, n == 1 case needs the spans themselves, to tell an insertion already made
    (`old` sits inside `new`) from a removal not yet made (`new` sits inside `old`). Any other
    count — including two or more on either side — is ambiguous: neither a doubled anchor nor a
    vanished one is visible after the fact.
    """
    o, n = _count(text, edit.old), _count(text, edit.new)
    if o == 1 and n == 0:
        return "unapplied"
    if o == 0 and n == 1:
        return "applied"
    if o == 1 and n == 1:
        o_start = _occurrences(text, edit.old)[0]
        o_end = o_start + len(edit.old)
        n_start = _occurrences(text, edit.new)[0]
        n_end = n_start + len(edit.new)
        if n_start <= o_start and o_end <= n_end:
            return "applied"
        if o_start <= n_start and n_end <= o_end:
            return "unapplied"
        return "ambiguous"
    if o == 0 and n == 0:
        return "missing"
    return "ambiguous"


def _classify(text: str, edits: Sequence[Edit]) -> tuple[str, str]:
    """Decide APPLY, ALREADY or FAIL for one file, per the module docstring's rule."""
    states = [_edit_state(text, e) for e in edits]
    if all(s == "unapplied" for s in states):
        if _spans(text, [e.old for e in edits]) is None:
            return FAIL, f"anchors overlap ({_counts(text, edits)})"
        return APPLY, ""
    if all(s == "applied" for s in states):
        return ALREADY, "every edit already present"
    named = ", ".join(f"edit {i}: {s}" for i, s in enumerate(states, 1))
    return FAIL, f"edits do not agree ({named}; {_counts(text, edits)})"


def _splice(text: str, edits: Sequence[Edit], spans: Sequence[tuple[int, int]]) -> str:
    """Replace each span with its edit's `new`, last span first so offsets stay valid."""
    for (start, end), edit in sorted(zip(spans, edits, strict=True), reverse=True):
        text = text[:start] + edit.new + text[end:]
    return text


def _nonblank(text: str) -> list[str]:
    return [line for line in text.split("\n") if line.strip()]


def _check_shape(
    before: str,
    after: str,
    edits: Sequence[Edit],
    spans: Sequence[tuple[int, int]],
) -> str | None:
    """Return why `after` is not exactly the declared edit of `before`, or None when it is.

    Survivors are the lines OUTSIDE the matched spans — never "every line minus old's lines",
    which removes the FIRST equal copy of a repeated line rather than the one the edit matched.
    """
    kept, cursor = [], 0
    for start, end in sorted(spans):
        kept.append(before[cursor:start])
        cursor = end
    kept.append(before[cursor:])
    survivors = _nonblank("".join(kept))
    expected = Counter(survivors)
    expected.update(line for e in edits for line in _nonblank(e.new))
    actual = Counter(_nonblank(after))
    if expected != actual:
        lost = sorted((expected - actual).elements())[:3]
        gained = sorted((actual - expected).elements())[:3]
        return f"shape: unexpected line delta (missing {lost}, extra {gained})"
    remaining = iter(_nonblank(after))
    if not all(line in remaining for line in survivors):
        return "shape: surviving lines were reordered"
    return None


def _syntax_kind(path: Path, text: str) -> str:
    """Pick the parser a file's name or shebang implies; 'unchecked' when there is none."""
    first = text.split("\n", 1)[0]
    if path.suffix == ".sh" or (first.startswith("#!") and "bash" in first):
        return "bash"
    if path.suffix == ".py" or (first.startswith("#!") and "python" in first):
        return "python"
    return "unchecked"


def _syntax_error(kind: str, path: Path, text: str) -> str | None:
    """Return the parser's complaint about `text`, or None when it parses."""
    if kind == "bash":
        proc = subprocess.run(
            ["bash", "-n"],
            input=text,
            capture_output=True,
            text=True,
            timeout=SYNTAX_TIMEOUT,
            check=False,
        )
        if proc.returncode != 0:
            lines = proc.stderr.strip().splitlines() or [
                f"bash -n exited {proc.returncode}"
            ]
            return lines[-1]
        return None
    if kind == "python":
        try:
            compile(text, str(path), "exec")
        except (SyntaxError, ValueError) as exc:
            return f"{type(exc).__name__}: {exc}"
    return None


def _lines(text: str) -> list[str]:
    return [line.rstrip("\r") for line in text.split("\n")]


def _long_lines(before: str, after: str, limit: int) -> list[str]:
    """Every line the edit GAINED that is longer than `limit` columns, once per occurrence.

    `.elements()` — a bare `for line in gained` walks a Counter's KEYS, so two identical
    over-long lines gained together would report as one.
    """
    gained = Counter(_lines(after)) - Counter(_lines(before))
    return sorted(line for line in gained.elements() if len(line) > limit)


def _report(
    status: str,
    rc: int,
    results: Sequence[FileResult],
    written: int,
    lines: list[str],
    mode: str,
) -> Report:
    applied = sum(r.status == APPLY for r in results)
    already = sum(r.status == ALREADY for r in results)
    lines.append(
        f"RESULT: {status} rc={rc} files={len(results)} apply={applied} "
        f"already={already} written={written} mode={mode}"
    )
    return Report(status, rc, tuple(results), written, "\n".join(lines))


def _error(message: str, mode: str) -> Report:
    return _report(ERROR, 2, [], 0, [f"ERROR  {message}"], mode)


def _validate_edits(edits: Sequence[Edit]) -> str | None:
    if not edits:
        return "no edits given"
    for i, edit in enumerate(edits, 1):
        if not edit.old or not edit.old.endswith("\n"):
            return f"edit {i}: old must be non-empty whole lines ending in a newline"
        if edit.new and not edit.new.endswith("\n"):
            return f"edit {i}: new must be empty or whole lines ending in a newline"
        if edit.old == edit.new:
            return f"edit {i}: old and new are identical"
    return None


def run(
    edits: Sequence[Edit],
    files: Sequence[str],
    *,
    root: str | Path,
    predictions: Sequence[Prediction] = (),
    max_columns: int | None = None,
    match_root: str | Path | None = None,
    dry_run: bool = False,
) -> Report:
    """Apply `edits` to every file in `files` (relative to `root`), or write nothing.

    Args:
        edits: whole-line replacements applied to each file.
        files: paths relative to `root`; at least one, no duplicates, none outside `root`.
        root: directory the paths resolve against.
        predictions: occurrence counts to verify before and after.
        max_columns: longest permitted line the edit adds; None leaves it unchecked.
        match_root: when set, every result must equal the same relative path under it.
        dry_run: judge everything and write nothing.

    Returns:
        A Report whose status is PASS (rc 0), FAIL (rc 1) or ERROR (rc 2).
    """
    mode = "dry-run" if dry_run else "write"
    problem = _validate_edits(edits)
    if problem:
        return _error(problem, mode)
    if not files:
        return _error("no files given — a run over nothing proves nothing", mode)
    base = Path(root).resolve()
    if not base.is_dir():
        return _error(f"root is not a directory: {base}", mode)

    targets: dict[Path, str] = {}
    for name in files:
        path = (base / name).resolve()
        if not path.is_relative_to(base):
            return _error(f"{name} resolves outside root {base}", mode)
        if path in targets:
            return _error(f"{name} is listed twice", mode)
        targets[path] = name
    by_path: dict[Path, list[Prediction]] = {}
    for pred in predictions:
        path = (base / pred.path).resolve()
        if path not in targets:
            return _error(
                f"prediction names {pred.path}, which is not in the file set", mode
            )
        if not pred.needle:
            return _error(f"prediction for {pred.path} has an empty needle", mode)
        by_path.setdefault(path, []).append(pred)

    lines = [
        f"checks: root={base} "
        f"columns={max_columns if max_columns is not None else 'unchecked'} "
        f"predictions={len(predictions)} "
        f"match-root={Path(match_root).resolve() if match_root is not None else 'unset'}"
    ]
    results: list[FileResult] = []
    plans: list[tuple[Path, str, str]] = []
    for path, name in targets.items():
        result, plan = _judge(
            path, name, edits, by_path.get(path, []), max_columns, base, match_root
        )
        results.append(result)
        if plan is not None:
            plans.append((path, plan[0], plan[1]))
        detail = f" — {result.detail}" if result.detail else ""
        lines.append(f"{result.status:7s} {name}{detail}; syntax={result.syntax}")

    if any(r.status == ERROR for r in results):
        return _report(ERROR, 2, results, 0, lines, mode)
    if any(r.status == FAIL for r in results):
        lines.append("nothing written: every file must pass before any is written")
        return _report(FAIL, 1, results, 0, lines, mode)
    if dry_run:
        return _report("PASS", 0, results, 0, lines, mode)

    written = 0
    for path, original, computed in plans:
        name = targets[path]
        try:
            if _read(path) != original:
                lines.append(
                    f"FAIL    {name} — changed since it was read; stopped writing"
                )
                return _report(FAIL, 1, results, written, lines, mode)
            _write(path, computed)
            if _read(path) != computed:
                lines.append(f"ERROR   {name} — re-read differs from what was written")
                return _report(ERROR, 2, results, written, lines, mode)
        except (OSError, UnicodeDecodeError) as exc:
            lines.append(f"ERROR   {name} — write failed: {exc}")
            return _report(ERROR, 2, results, written, lines, mode)
        written += 1
    return _report("PASS", 0, results, written, lines, mode)


def _rehearsed_mismatch(
    path: Path, base: Path, match_root: str | Path, text: str
) -> str | None:
    """Return why `text` doesn't match its rehearsed twin under `match_root`, or None when it does.

    Shared by the ALREADY and APPLY branches of `_judge`, which differ only in which text they
    compare (the original, or the computed result) and how they phrase the refusal.
    """
    twin = Path(match_root).resolve() / path.relative_to(base)
    try:
        rehearsed = _read(twin)
    except (OSError, UnicodeDecodeError) as exc:
        return f"no rehearsed copy at {twin}: {exc}"
    if rehearsed != text:
        return f"differs from rehearsed {twin}"
    return None


def _judge(
    path: Path,
    name: str,
    edits: Sequence[Edit],
    predictions: Sequence[Prediction],
    max_columns: int | None,
    base: Path,
    match_root: str | Path | None,
) -> tuple[FileResult, tuple[str, str] | None]:
    """Run every check for one file; the plan is (original, computed) for an APPLY.

    `syntax=` reflects whether a parse actually RAN: the kind (`bash`/`python`) only when one
    did, `not-run` when a classification FAIL or an unreadable file skipped it entirely, and
    `unchecked` only for a file with no parser at all. One exception, measured in review: when
    the parser itself could not run (a timeout, a missing `bash`) the ERROR keeps the kind,
    because naming the parser is what explains the failure — its detail says so outright.
    """
    try:
        original = _read(path)
    except (OSError, UnicodeDecodeError) as exc:
        return FileResult(name, ERROR, f"cannot read: {exc}", "not-run"), None
    kind = _syntax_kind(path, original)

    state, detail = _classify(original, edits)
    if state == FAIL:
        return FileResult(name, FAIL, detail, "not-run"), None
    if state == ALREADY:
        try:
            error = _syntax_error(kind, path, original)
        except (OSError, subprocess.SubprocessError) as exc:
            return FileResult(
                name, ERROR, f"syntax check could not run: {exc}", kind
            ), None
        if error:
            return FileResult(
                name, FAIL, f"already applied, but does not parse: {error}", kind
            ), None
        for pred in predictions:
            got = original.count(pred.needle)
            if got != pred.after:
                return FileResult(
                    name,
                    FAIL,
                    f"prediction {pred.needle!r}: after={got}, predicted {pred.after}",
                    kind,
                ), None
        if match_root is not None:
            reason = _rehearsed_mismatch(path, base, match_root, original)
            if reason:
                return FileResult(
                    name, FAIL, f"already applied, but {reason}", kind
                ), None
        return FileResult(name, ALREADY, detail, kind), None

    spans = _spans(original, [e.old for e in edits])
    computed = _splice(original, edits, spans)
    try:
        before_error = _syntax_error(kind, path, original)
        if before_error:
            return FileResult(
                name, FAIL, f"already broken before the edit: {before_error}", kind
            ), None
        after_error = _syntax_error(kind, path, computed)
    except (OSError, subprocess.SubprocessError) as exc:
        return FileResult(name, ERROR, f"syntax check could not run: {exc}", kind), None
    if after_error:
        return FileResult(
            name, FAIL, f"the edit breaks syntax: {after_error}", kind
        ), None

    shape = _check_shape(original, computed, edits, spans)
    if shape:
        return FileResult(name, ERROR, shape, kind), None
    if max_columns is not None:
        long = _long_lines(original, computed, max_columns)
        if long:
            return FileResult(
                name,
                FAIL,
                f"{len(long)} added line(s) over {max_columns} columns: "
                f"{long[0][:60]!r}…",
                kind,
            ), None
    for pred in predictions:
        got_before = original.count(pred.needle)
        got_after = computed.count(pred.needle)
        if (got_before, got_after) != (pred.before, pred.after):
            return FileResult(
                name,
                FAIL,
                f"prediction {pred.needle!r}: before={got_before} "
                f"after={got_after}, predicted before={pred.before} after={pred.after}",
                kind,
            ), None
    if match_root is not None:
        reason = _rehearsed_mismatch(path, base, match_root, computed)
        if reason:
            return FileResult(name, FAIL, f"computed result: {reason}", kind), None
    return FileResult(name, APPLY, "", kind), (original, computed)


def main(
    argv: Sequence[str],
    edits: Sequence[Edit],
    files: Sequence[str],
    *,
    predictions: Sequence[Prediction] = (),
    max_columns: int | None = None,
) -> int:
    """Parse `--root`, `--match-root` and `--dry-run`, run, print the report, return its rc."""
    parser = argparse.ArgumentParser(prog=Path(argv[0]).name if argv else "bulk_edit")
    parser.add_argument(
        "--root", default=".", help="directory the file paths are relative to"
    )
    parser.add_argument(
        "--match-root", help="refuse unless each result equals its copy here"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="judge everything, write nothing"
    )
    try:
        args = parser.parse_args(list(argv[1:]))
    except SystemExit as exc:
        if exc.code == 0:
            return 0
        print("RESULT: ERROR rc=2 files=0 apply=0 already=0 written=0 mode=usage")
        return 2
    report = run(
        edits,
        files,
        root=args.root,
        predictions=predictions,
        max_columns=max_columns,
        match_root=args.match_root,
        dry_run=args.dry_run,
    )
    print(report.text)
    return report.rc

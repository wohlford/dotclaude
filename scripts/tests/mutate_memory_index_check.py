#!/usr/bin/env python3
"""Mutation campaign for scripts/memory-index-check.py, driven by scripts/lib/mutate.py.

Deliberately NOT named `test_*`: it mutates a tracked file in place, so pytest must not collect
it. Run it on demand — `./scripts/tests/mutate_memory_index_check.py` — and never while editing
the subject, since the restore would clobber your edits.

Every row names ONE safety property the suite (`test_memory_index_check.sh`) is supposed
to be defending, and mutates exactly what it names. A row that survives is not evidence the code
is fine — it is evidence the suite is not testing what the row claims; the fix is to strengthen
the SUITE ROW, never to reword this file's assertion.

## The one row that looks wrong and is not

`failopen` replaces the line-split in `entry_blocks` with an unconditional raise. `main()` returns
*before* ever calling `entry_blocks` on any path that is out of scope, absent, not opted in or not a
covered name, so every row that exercises one of those declines is unreachable by this mutation and
MUST stay green: the out-of-scope and look-alike-name rows, the opt-in-absent row,
and the absent-file row. That is not a coverage gap, it is the scope/opt-in guard working as
designed; each of those rows gets its own coverage from the mutation that loosens the guard it
names. What this mutation tests is the fail-open handler's own print statement: under the mutation
the checker still exits 0 (fail-open), so only a row that asserts **stderr empty** on top of **rc 0**
can notice anything changed, for the exit-0 rows that DO reach `entry_blocks`; every exit-2 row is
caught more simply, by its own rc mismatch. It is the only mutation here that exercises the outer
`except Exception` print path at all.

The repo root is derived from __file__, which means invoking this through a symlinked
`~/.claude/scripts/tests/` would resolve into PRODUCTION's tree and mutate that instead. Run it
from the working copy you intend to grade.

## Kill sets

`python3 scripts/tests/mutate_memory_index_check.py --killsets` applies each mutation to a COPY of the
checker, runs the whole suite over that copy and prints the rows each mutant fails, ending in
`KILLSETS mutations=N survivors=S duplicates=D`. It writes nothing into the repo. A duplicate mutant
inflates the caught count and measures nothing, so both numbers must be zero.

## Named gaps: defence in depth that no single-point mutation can reach

* `scope-name`: a file name outside the covered set is declined by THREE independent guards (the
  suffix test in the cheap guard, exact membership of the resolved name, and the last component of
  the tail pattern). `mutate.py` applies one `(old, new)` pair per mutation, so no single-point edit
  removes all three; killing one still leaves the other two declining it. `scope-name`'s row stands
  on the strength of its assertion (rc 0, stderr empty, distinct fixture), and a `caught=N/N` verdict
  does not imply otherwise.
* The cheap suffix guard (`file_path.endswith(tuple(CAPS))`) is one of the three guards behind
  `scope-name` and `x-memory`: widening it (to admit any name) changes no outcome,
  because exact membership of the resolved name declines the same files. Only NARROWING it is
  observable, and the mutant that narrows it to a name nothing carries kills the first tier's rows
  (narrowing it to `MEMORY.md` would duplicate the tail-pattern mutant's kill set, so it is not used).
* The `shipped-declined` row is killed alone by the mutant that re-admits `MEMORY-SHIPPED.md` as a covered name.
* The mutant that calls the threshold something else kills a strict SUBSET of what the mutant whose
  line check is never reached kills: suppressing the breach removes every line message, so a
  row asserting the message cannot survive it. Only the row `lines: exactly N lines — trips, and
  reports the count without needing the threshold's name` separates them; keep it.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "scripts" / "lib"))
import mutate  # noqa: E402

SUBJECT = REPO / "scripts" / "memory-index-check.py"
SUITE = ["bash", str(REPO / "scripts" / "tests" / "test_memory_index_check.sh")]

MUTATIONS = [
    # ---- aggregate whole-file cap. These exist because a first pass at this campaign covered
    # the per-entry path eight ways and the aggregate path not at all; a perfect score over the
    # wrong mutants is the failure this block is written against.
    mutate.Mutation(
        "the aggregate boundary flips to strict >, so a file exactly at the threshold passes",
        "total_bytes >= agg_max",
        "total_bytes > agg_max",
    ),
    mutate.Mutation(
        "the aggregate is summed from entry blocks, under-counting every non-entry byte",
        "total_bytes = len(content)",
        "total_bytes = sum(b[1] for b in blocks)",
    ),
    mutate.Mutation(
        "the aggregate branch is never reached, so only the per-entry cap survives",
        "agg_breach = total_bytes >= agg_max",
        "agg_breach = False",
    ),
    mutate.Mutation(
        "the opt-in gate is removed, enrolling every project index on the machine",
        "if OPT_IN_NEEDLE not in content.lower():\n        return 0",
        "if False:\n        return 0",
    ),
    # ---- the cap itself and its boundary
    mutate.Mutation(
        "the cap is widened 100x, so the 4052B defect and every over-cap fixture reads clean",
        "MAX_ENTRY_BYTES = 1000",
        "MAX_ENTRY_BYTES = 100000",
    ),
    mutate.Mutation(
        "the boundary admits the exact-cap entry too (>= not >), so 1000B alone now blocks",
        "b[1] > MAX_ENTRY_BYTES",
        "b[1] >= MAX_ENTRY_BYTES",
    ),
    # ---- scope: each mutation loosens exactly one path component the scope-* rows pin down
    mutate.Mutation(
        "the scope glob drops the 'projects' component, so any top-level dir qualifies",
        'SCOPE_DIRS = "projects/*/memory"',
        'SCOPE_DIRS = "*/*/memory"',
    ),
    mutate.Mutation(
        "the scope glob drops the 'memory' component, so any subdirectory qualifies",
        'SCOPE_DIRS = "projects/*/memory"',
        'SCOPE_DIRS = "projects/*/*"',
    ),
    mutate.Mutation(
        "scope-redundant: the tail-pattern match is unconditionally satisfied — this flips "
        "scope-projects and scope-memory (already independently covered above), NOT scope-name: "
        "that row stays declined by the suffix test and the membership test earlier in main(), "
        "independent enforcements of the same filename requirement (see the module docstring)",
        'not abs_file.match(f"{SCOPE_DIRS}/{abs_file.name}")',
        "False",
    ),
    mutate.Mutation(
        "the path is used as-given instead of resolved, so a symlinked tail never survives",
        "Path(file_path).resolve()",
        "Path(file_path)",
    ),
    # ---- the block-grouping algorithm that makes wrapping not an evasion
    mutate.Mutation(
        "a continuation line no longer grows its block's byte count, so wrapping evades the cap",
        "size += 1 + len(nxt)",
        "size += 0",
    ),
    mutate.Mutation(
        "no line is ever recognised as a bullet head, so prose is measured as if it were one",
        'if not lines[i].startswith(b"- ["):',
        "if False:",
    ),
    mutate.Mutation(
        "terminator: the continuation terminator reverts to the bare b'- ' test, so a bracket-"
        "less dash line is cheaper than hard-wrapping as an evasion again — must turn "
        "bare-dash-evasion red",
        'nxt.startswith(b"- [") or nxt.startswith(b"#")',
        'nxt.startswith(b"- ") or nxt.startswith(b"#")',
    ),
    # ---- the report cap
    mutate.Mutation(
        "the report-limit widens past the fixture's 7 offenders, so the '... and N more' line "
        "never fires",
        "REPORT_LIMIT = 5",
        "REPORT_LIMIT = 50",
    ),
    # ---- the hook-contract refusal (argv ignored, stdin required, no TTY)
    mutate.Mutation(
        "the argv/TTY refusal is disabled, so a misinvocation examines nothing or hangs instead "
        "of refusing",
        "len(sys.argv) > 1 or sys.stdin.isatty()",
        "False",
    ),
    # ---- the outer fail-open handler's own print path (see the module docstring above)
    mutate.Mutation(
        "entry_blocks always raises, so every in-scope file falls through to fail-open — caught "
        "only by the stderr-empty half of the exit-0 rows, never by rc alone",
        '    lines = raw.split(b"\\n")',
        '    raise RuntimeError("mutant")',
    ),
    mutate.Mutation(
        "isfile: the existence check is unconditionally satisfied, so a nonexistent in-scope "
        "path reaches read_bytes() and raises FileNotFoundError — caught the same way as "
        "failopen, via the stderr-empty half of absent-file's exit-0 assertion",
        "not abs_file.is_file()",
        "False",
    ),
    # ---- the 190-line check
    mutate.Mutation(
        "the line threshold flips to strict >, so a file at exactly the threshold passes",
        "n_lines >= LINE_MAX",
        "n_lines > LINE_MAX",
    ),
    mutate.Mutation(
        "the line check is never reached, so only the byte checks survive",
        "line_breach = n_lines >= LINE_MAX",
        "line_breach = False",
    ),
    mutate.Mutation(
        "the line threshold is raised to 250, past the client's own 200-line cut",
        "LINE_MAX = 190",
        "LINE_MAX = 250",
    ),
    mutate.Mutation(
        "a line-only breach no longer ends the early return, so it passes unreported",
        "if not offenders and not agg_breach and not line_breach:",
        "if not offenders and not agg_breach:",
    ),
    mutate.Mutation(
        "the line message reports the threshold where the file's own line count belongs",
        "{abs_file} has {n_lines} lines",
        "{abs_file} has {LINE_MAX} lines",
    ),
    mutate.Mutation(
        "the line message calls the threshold something else, so the needle row loses it",
        'f"{LINE_MAX}-line threshold (the client cuts this file at 200 lines or 25,000 UTF-16 units).",',
        'f"{LINE_MAX}-line limit (the client cuts this file at 200 lines or 25,000 UTF-16 units).",',
    ),
    # ---- the covered name: cheap guard, exact membership, and the declined second-tier name
    mutate.Mutation(
        "the cheap suffix guard admits no covered name, so the first tier is declined before any read",
        "file_path.endswith(tuple(CAPS))",
        'file_path.endswith("NO-SUCH-NAME.md")',
    ),
    mutate.Mutation(
        "exact membership is skipped, so X-MEMORY.md reaches the CAPS lookup",
        "if abs_file.name not in CAPS:\n        return 0",
        "if False:\n        return 0",
    ),
    mutate.Mutation(
        "MEMORY-SHIPPED.md is re-admitted as a covered name, so an over-cap second tier is enforced again",
        'CAPS = {"MEMORY.md": AGG_MAX_BYTES}',
        'CAPS = {"MEMORY.md": AGG_MAX_BYTES, "MEMORY-SHIPPED.md": AGG_MAX_BYTES}',
    ),
    # ---- the breach wording
    mutate.Mutation(
        "the first tier's headline reverts to the false claim that entries are already invisible",
        '"units, 200 lines) so entries are not silently dropped."',
        '"units, 200 lines) but entries past it are ALREADY invisible."',
    ),
    mutate.Mutation(
        "the aggregate remedy reinstates 'archive closed items'",
        '"the pointer. Shortening the file is the only fix; do not narrow this check."',
        '"the pointer, or archive closed items. Shortening the file is the only fix."',
    ),
]


def suite_over_copy(source: str) -> subprocess.CompletedProcess[str]:
    """Run the whole suite over a COPY of the checker whose text is SOURCE."""
    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp) / "repo"
        for rel in (
            "scripts/memory-index-check.py",
            "scripts/tests/test_memory_index_check.sh",
        ):
            (copy / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(REPO / rel, copy / rel)
        (copy / "scripts" / "memory-index-check.py").write_text(source)
        return subprocess.run(
            ["bash", str(copy / "scripts" / "tests" / "test_memory_index_check.sh")],
            capture_output=True,
            text=True,
            cwd=copy,
        )


def killset(label: str, old: str, new: str) -> frozenset[str]:
    """Return the suite rows one mutation fails, run over a COPY of the checker and its suite."""
    source = SUBJECT.read_text()
    assert source.count(old) == 1 and old != new, label
    proc = suite_over_copy(source.replace(old, new))
    return frozenset(re.findall(r"^FAIL  (.+)$", proc.stdout, re.M))


def killsets() -> int:
    """Print each mutation's kill set and the survivors and duplicates; return 0 if clean."""
    # The unmutated BASELINE first: a suite already red kills every mutant, so survivors=0 would
    # read flawless. Require the suite's own PASS summary on the untouched checker.
    base = suite_over_copy(SUBJECT.read_text())
    if base.returncode != 0 or not re.search(r"^PASS \d+/\d+$", base.stdout, re.M):
        print(
            "KILLSETS BASELINE FAIL: the unmutated suite is not green; no verdict reached"
        )
        print(base.stdout[-2000:])
        return 1
    seen: dict[frozenset[str], str] = {}
    survivors = duplicates = 0
    for m in MUTATIONS:
        rows = killset(m.label, m.old, m.new)
        print(f"{m.label[:60]:<60} kills {len(rows)} row(s)")
        if not rows:
            survivors += 1
        elif rows in seen:
            duplicates += 1
            print(f"DUPLICATE KILL SET: {m.label[:60]} == {seen[rows][:60]}")
        else:
            seen[rows] = m.label
    print(
        f"KILLSETS mutations={len(MUTATIONS)} survivors={survivors} duplicates={duplicates}"
    )
    return 0 if not survivors and not duplicates else 1


def main() -> int:
    if sys.argv[1:] == ["--killsets"]:
        return killsets()
    report_path = sys.argv[1] if len(sys.argv) > 1 else None
    report = mutate.run(SUBJECT, SUITE, MUTATIONS, cwd=REPO, report_path=report_path)
    print(report.text)
    return report.rc


if __name__ == "__main__":
    sys.exit(main())

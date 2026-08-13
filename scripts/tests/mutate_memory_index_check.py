#!/usr/bin/env python3
"""Mutation campaign for scripts/memory-index-check.py, driven by scripts/lib/mutate.py.

Deliberately NOT named `test_*`: it mutates a tracked file in place, so pytest must not collect
it. Run it on demand — `./scripts/tests/mutate_memory_index_check.py` — and never while editing
the subject, since the restore would clobber your edits.

Every row names ONE safety property the 18-row suite (`test_memory_index_check.sh`) is supposed
to be defending, and mutates exactly what it names. A row that survives is not evidence the code
is fine — it is evidence the suite is not testing what the row claims; the fix is to strengthen
the SUITE ROW, never to reword this file's assertion.

## The one row that looks wrong and is not

`failopen` replaces the line-split in `entry_blocks` with an unconditional raise. `main()` returns
*before* ever calling `entry_blocks` on any path that is out of scope, absent, or not opted in, so
those rows are unreachable by this mutation and MUST stay green — that is not a coverage gap, it
is the scope/opt-in guard working as designed. Naming the rows precisely, rather than rounding up
to "the `scope-*` rows": the unreachable set is exactly the three out-of-scope rows
(`scope-projects`, `scope-memory`, `scope-name`), plus `optin-absent` (declined by the
content-opt-in gate) and `absent-file` (declined by `is_file()`) — five rows, one per declining
reason named above. `absent-file`'s own coverage comes from the `isfile` mutation below, not this
one. `scope-symlink`'s fixture resolves the payload path THROUGH a
symlink to a real, opted-in, in-scope `MEMORY.md`, so it DOES reach `entry_blocks` and joins the
in-scope rows that flip — it is not a fourth row that stays green. What the mutation actually
tests is the fail-open handler's own print statement: under the mutation the checker still exits 0
(fail-open), so only a row that asserts **stderr empty** on top of **rc 0** can notice anything
changed, for the three exit-0 in-scope rows (`healthy-corpus`, `boundary-under`, `prose-long`);
every exit-2 in-scope row (`defect`, `boundary-over`, `multibyte`, `wrapped-entry`, `report-limit`,
`scope-symlink`, `bare-dash-evasion`) is caught more simply, by its own rc mismatch. It is the only
mutation in this campaign that exercises the outer `except Exception` print path at all — every
other row resolves inside `read_file_path`'s own handler or via a clean scope/opt-in decision.

The repo root is derived from __file__, which means invoking this through a symlinked
`~/.claude/scripts/tests/` would resolve into PRODUCTION's tree and mutate that instead. Run it
from the working copy you intend to grade.

## A named gap: `scope-name` has no independent single-point mutation coverage

The filename requirement (`…MEMORY.md`) is enforced TWICE, ~45 lines apart: the cheap
`file_path.endswith("MEMORY.md")` guard at the top of `main()`, and again as the literal last
component of `SCOPE_PATTERN` matched a few lines later. `mutate.py` applies one `(old, new)` pair
per mutation, so no single-point edit can remove both at once — a mutation that kills only the
`SCOPE_PATTERN` match still gets declined by the `endswith` guard before ever reaching it, and the
`scope-name` suite row (filename `NOTES.md`) never turns red. Measured directly against isolated
copies of the checker, invoked on the `scope-name` fixture (a `…/projects/<slug>/memory/NOTES.md`
carrying the defect):

    baseline                              -> rc=0 (correctly declined)
    kill SCOPE_PATTERN match only         -> rc=0 (still declined by the cheap endswith guard)
    kill endswith("MEMORY.md") only       -> rc=0 (still declined by SCOPE_PATTERN's own literal)
    kill BOTH                             -> rc=2

This is genuine defence in depth in the checker, not a bug — do not delete either guard to "fix"
this. What the `scope-redundant` mutation below actually flips is `scope-projects` and
`scope-memory` (already independently covered by their own dedicated mutations); it is kept and
renamed rather than deleted because it is still a faithful test of the `SCOPE_PATTERN` match as a
whole, it is simply not evidence about `scope-name` specifically. `scope-name`'s own suite row
stands on the strength of its assertion (rc 0, stderr empty, distinct fixture) rather than on any
mutation in this campaign — a `caught=N/N` verdict below does not imply otherwise.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "scripts" / "lib"))
import mutate  # noqa: E402

SUBJECT = REPO / "scripts" / "memory-index-check.py"
SUITE = ["bash", str(REPO / "scripts" / "tests" / "test_memory_index_check.sh")]

MUTATIONS = [
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
        "the literal scope glob drops the 'projects' component, so any top-level dir qualifies",
        '"projects/*/memory/MEMORY.md"',
        '"*/*/memory/MEMORY.md"',
    ),
    mutate.Mutation(
        "the scope glob drops the 'memory' component, so any subdirectory qualifies",
        'SCOPE_PATTERN = "projects/*/memory/MEMORY.md"',
        'SCOPE_PATTERN = "projects/*/*/MEMORY.md"',
    ),
    mutate.Mutation(
        "scope-redundant: the SCOPE_PATTERN match is unconditionally satisfied — this flips "
        "scope-projects and scope-memory (already independently covered above), NOT scope-name: "
        "that row stays declined by the endswith('MEMORY.md') guard earlier in main(), a second "
        "independent enforcement of the same filename requirement (see the module docstring)",
        "not abs_file.match(SCOPE_PATTERN)",
        "False",
    ),
    mutate.Mutation(
        "the path is used as-given instead of resolved, so a symlinked tail never survives",
        "Path(file_path).resolve()",
        "Path(file_path)",
    ),
    mutate.Mutation(
        "optin: the content opt-in gate is neutralised, so the checker fires on any in-scope "
        "file whether it declares the rule or not — must turn optin-absent red",
        "if OPT_IN_NEEDLE not in content.lower():",
        "if False:",
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
]


def main() -> int:
    report_path = sys.argv[1] if len(sys.argv) > 1 else None
    report = mutate.run(SUBJECT, SUITE, MUTATIONS, cwd=REPO, report_path=report_path)
    print(report.text)
    return report.rc


if __name__ == "__main__":
    sys.exit(main())

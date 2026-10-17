#!/usr/bin/env python3
# Script: mutate_audit_shellcheck_parallel.py
# Purpose: Mutation campaign for the bounded-parallel shellcheck check in audit.sh, driven by lib/mutate.py
# Usage:   python3 scripts/tests/mutate_audit_shellcheck_parallel.py
"""Mutation campaign for `check_shellcheck`'s parallel pool in skills/audit/audit.sh.

Deliberately NOT named `test_*`: it mutates a tracked file in place, so pytest must not collect it.
Never run it while editing the subject: the restore writes back its own snapshot and would silently
discard your edits. It runs the whole of `test_audit.sh` once per row, so it takes hours; start it
through `scripts/run-long.sh`, never inline.

Why this campaign exists. The rows of the `rSC` section of `test_audit.sh` that hold invariants the
pre-change code already satisfied (byte identity, ordering, teardown, hygiene) are green before the
change by construction, so only a mutant can show that they can go red. Each row below breaks one
property of the pool and names, in its label, the property it breaks. Two rows can share a kill set
when their code differs (a missing status file read as 0, and a status variable not reset, are
different defects); identical kill sets flag a POSSIBLE duplicate, to be checked by reading the two.

M7c deletes the default worker cap and is observable only on a host with more than 8 cores. On any
other host the campaign omits it and prints that it did, so it is recorded as host-conditional and
never as SURVIVED.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "scripts" / "lib"))
import mutate  # noqa: E402

SUBJECT = REPO / "skills" / "audit" / "audit.sh"
SUITE = ["bash", str(REPO / "scripts" / "tests" / "test_audit.sh")]

MUTATIONS = [
    mutate.Mutation(
        "M1 the report is read in reverse index order, which on an inverted-completion fixture is completion order",
        'out="$(<"$dir/$n.out")"',
        'out="$(<"$dir/$((total - n + 1)).out")"',
    ),
    mutate.Mutation(
        "M2 the parent ignores every child's nonzero status",
        '[[ "$v" -ne 0 ]] && rc="$v"',
        '[[ "$v" -ne 0 ]] && rc=0',
    ),
    mutate.Mutation(
        "M3 the worker records 0 whatever shellcheck returned",
        'printf "%d\\n" "$?" >"$d/$i.rc"',
        'printf "%d\\n" 0 >"$d/$i.rc"',
    ),
    mutate.Mutation(
        "M4 the denominator floor is deleted, so a child that never ran reads as clean",
        '[[ "$ran" -ne "$total" ]]',
        "false",
    ),
    mutate.Mutation(
        "M5 the pool's own status is dropped (the only place xrc is tested)",
        '[[ "$xrc" -ne 0 ]] && xsuf=" (xargs exit status $xrc)"',
        'xsuf=""',
    ),
    mutate.Mutation(
        "M6 the override is read and then replaced by the default",
        '[[ -n "$req" ]] && jobs="$req"',
        '[[ -n "$req" ]] && jobs="$default_jobs"',
    ),
    mutate.Mutation(
        "M7a the clamp to the file count is deleted",
        '[[ "$jobs" -gt "$total" ]] && jobs="$total"',
        ":",
    ),
    mutate.Mutation(
        "M7b the 64-worker cap is deleted",
        '[[ "$jobs" -gt 64 ]] && jobs=64',
        ":",
    ),
    mutate.Mutation(
        "M7c the 8-worker default cap is deleted (kills only on a host with more than 8 cores)",
        '[[ "$cores" -gt 8 ]] && cores=8',
        ":",
    ),
    mutate.Mutation(
        "M8a the exit trap no longer removes the scratch directory",
        "shellcheck_remove_tmp   # exit-trap backstop",
        ":   # exit-trap backstop",
    ),
    mutate.Mutation(
        "M8b the check no longer removes its own scratch directory on a normal path",
        "shellcheck_remove_tmp   # normal path",
        ":   # normal path",
    ),
    mutate.Mutation(
        "M9 the override is validated before the SKIP exits, so an invalid value turns a SKIP into a FAIL",
        'files="$(git -C "$scope" ls-files -- \'*.sh\' 2>/dev/null)"',
        (
            'req="${AUDIT_SHELLCHECK_JOBS:-}"; [[ -n "$req" ]] && shellcheck_jobs_invalid "$req" && { verdict_fail shellcheck "AUDIT_SHELLCHECK_JOBS must be a positive integer (got \'$req\')"; return; }\n'
            '  files="$(git -C "$scope" ls-files -- \'*.sh\' 2>/dev/null)"'
        ),
    ),
    mutate.Mutation(
        "M10 an empty output is appended as a blank line",
        '[[ -n "$out" ]] && detail="${detail}${out}"$\'\\n\'',
        "true && detail=\"${detail}${out}\"$'\\n'",
    ),
    mutate.Mutation(
        "M11 the worker captures stdout only, so a stderr-only finding is lost",
        '>"$d/$i.out" 2>&1',
        '>"$d/$i.out"',
    ),
    mutate.Mutation(
        "M12 a child's output is truncated to 64 KB",
        'out="$(<"$dir/$n.out")"',
        'out="$(head -c 65536 "$dir/$n.out")"',
    ),
    mutate.Mutation(
        "M13 an invalid override falls back to the default instead of failing",
        "{ verdict_fail shellcheck \"AUDIT_SHELLCHECK_JOBS must be a positive integer (got '$req')\"; return; }",
        'req=""',
    ),
    mutate.Mutation(
        "M14 a missing status file reads as 0",
        'v=""; [[ -r "$dir/$n.rc" ]]',
        'v=0; [[ -r "$dir/$n.rc" ]]',
    ),
    mutate.Mutation(
        "M14b the status variable is not reset: a missing file inherits the previous file's value, or aborts under set -u when it is the first",
        'v=""; [[ -r',
        "[[ -r",
    ),
    mutate.Mutation(
        "M15 the validity test accepts the empty string",
        '[[ "$v" =~ ^(0|[1-9][0-9]{0,2})$ ]]',
        '[[ "$v" != *[!0-9]* ]]',
    ),
    mutate.Mutation(
        "M24 the validity test is a bare digit test, so 08 and 2^64 read as statuses",
        '[[ "$v" =~ ^(0|[1-9][0-9]{0,2})$ ]]',
        '[[ "$v" =~ ^[0-9]+$ ]]',
    ),
    mutate.Mutation(
        "M16 the pool runs in the background, so a kill of the script alone orphans it",
        '< "$dir/list"; xrc=$?',
        '< "$dir/list" & wait $!; xrc=$?',
    ),
    mutate.Mutation(
        "M18 the scratch base is no longer checked against the scope",
        'audit_scratch_outside_scope "$scope" "$base" || jobs=1',
        ":",
    ),
    mutate.Mutation(
        "M19 the population comes from the filesystem instead of git",
        'files="$(git -C "$scope" ls-files -- \'*.sh\' 2>/dev/null)"',
        'files="$(find "$scope" -name \'*.sh\' 2>/dev/null)"',
    ),
    mutate.Mutation(
        "M20 a bash-4-only expansion is introduced",
        "ran=0; total=0",
        'ran=0; total=0; : "${ran,,}"',
    ),
    mutate.Mutation(
        "M21 BASH_ENV is no longer cleared for the pool",
        'BASH_ENV="" xargs',
        "xargs",
    ),
    mutate.Mutation(
        "M22 each worker is handed one argument instead of an index and a path",
        'xargs -0 -n 2 -P "$jobs"',
        'xargs -0 -n 1 -P "$jobs"',
    ),
    mutate.Mutation(
        "M23 a second FAIL line is emitted for the same check",
        'verdict_fail shellcheck "$fail_msg"',
        'verdict_fail shellcheck "$fail_msg"; verdict_fail shellcheck "$fail_msg"',
    ),
]


def online_cores() -> int:
    try:
        out = subprocess.run(
            ["getconf", "_NPROCESSORS_ONLN"],
            capture_output=True,
            text=True,
            check=False,
        )
        return int(out.stdout.strip())
    except (OSError, ValueError):
        return os.cpu_count() or 1


def main() -> int:
    rows = MUTATIONS
    cores = online_cores()
    if cores <= 8:
        print(
            f"HOST-CONDITIONAL (cores={cores} <= 8): M7c not run; it can only be killed on a host with more than 8 cores"
        )
        rows = [m for m in MUTATIONS if not m.label.startswith("M7c")]
    report = mutate.run(SUBJECT, SUITE, rows, cwd=str(REPO))
    print(report.text)
    return report.rc


if __name__ == "__main__":
    sys.exit(main())

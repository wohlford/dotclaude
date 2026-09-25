#!/usr/bin/env python3
"""Mutation campaign for scripts/publish-fold-plan.py, driven by scripts/lib/mutate.py.

Deliberately NOT named `test_*`: it mutates a tracked file in place, so pytest must not collect
it. Run it on demand — `./scripts/tests/mutate_publish_fold_plan.py` — and never while editing
the subject, since the restore would clobber your edits.

Every row removes one piece of the jumped-collision rule (2026-09-24) or of the path-quoting
fixes that shipped with it, and requires `scripts/tests/test_publish_fold_plan.py` to notice.
Those changes exist because a fold that jumps a commit sharing one of its paths publishes that
commit's change under the wrong subject with an IDENTICAL final tree, so no convergence check
can see it — a survivor here means the suite has that same blind spot.

Deliberately NOT mutated: the culprit search order in `prune_unsafe_folds`
(`reversed(bricks)`). Dropping one unit's folds cannot create or remove a collision in any
other unit — jumps are defined on range order, which a drop does not change — so any search
order converges on the same drops, and the row could never fail.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "scripts" / "lib"))
import mutate  # noqa: E402

SUBJECT = REPO / "scripts" / "publish-fold-plan.py"
SUITE = [
    sys.executable,
    "-m",
    "pytest",
    "-q",
    "-x",
    str(REPO / "scripts" / "tests" / "test_publish_fold_plan.py"),
]

MUTATIONS = [
    mutate.Mutation(
        "diff_lines reads C-quoted headers again",
        '        "-c",\n        "core.quotePath=false",\n        "show",\n',
        '        "show",\n',
    ),
    mutate.Mutation(
        "commit_paths reads the quoted --name-only listing again",
        'out = git(scope, "show", "--format=", "--name-only", "-z", "--no-renames", sha)\n'
        '    return frozenset(p for p in out.split("\\0") if p)',
        'out = git(scope, "show", "--format=", "--name-only", "--no-renames", sha)\n'
        '    return frozenset(p for p in out.split("\\n") if p)',
    ),
    mutate.Mutation(
        "jumped_collisions never finds a shared path",
        "for path in commit_paths(scope, sha) & union:",
        "for path in commit_paths(scope, sha) & set():",
    ),
    mutate.Mutation(
        "jumped_commits misses the last jumped commit",
        "order[first_idx + 1 : last_idx]",
        "order[first_idx + 1 : last_idx - 1]",
    ),
    mutate.Mutation(
        "prune_unsafe_folds ignores every collision it finds",
        "            if found:\n",
        "            if found and False:\n",
    ),
    mutate.Mutation(
        "the collision evidence stops naming the jumped commit",
        'f"collides on: {path} (changed by "',
        'f"collides on: {path} ("',
    ),
    mutate.Mutation(
        "the path-disjoint jump advisory is never printed",
        "    if jump_notes:\n",
        "    if False:\n",
    ),
    mutate.Mutation(
        "the advisory loses its comment prefix",
        'jump_notes.append(f"# {first[:7]}',
        'jump_notes.append(f"{first[:7]}',
    ),
    mutate.Mutation(
        "git() decodes a non-UTF-8 path strictly again",
        '        errors="surrogateescape",\n        check=False,\n    )\n'
        "    if proc.returncode != 0:",
        "        check=False,\n    )\n    if proc.returncode != 0:",
    ),
    mutate.Mutation(
        "published_lines decodes a non-UTF-8 path strictly again",
        'f"{self.published}:{path}"],\n                capture_output=True,\n'
        '                text=True,\n                errors="surrogateescape",\n',
        'f"{self.published}:{path}"],\n                capture_output=True,\n'
        "                text=True,\n",
    ),
    mutate.Mutation(
        "blob decodes a non-UTF-8 path strictly again",
        'f"{ref}:{path}"],\n        capture_output=True,\n        text=True,\n'
        '        errors="surrogateescape",\n',
        'f"{ref}:{path}"],\n        capture_output=True,\n        text=True,\n',
    ),
    mutate.Mutation(
        "the FAIL verdict line drops its dropped= field",
        'f"folds={folds} undecided={undecided} dropped={len(dropped)} "\n'
        '            f"diverging={len(residual)}"',
        'f"folds={folds} undecided={undecided} "\n            f"diverging={len(residual)}"',
    ),
]

if __name__ == "__main__":
    report = mutate.run(SUBJECT, SUITE, MUTATIONS, cwd=REPO, timeout=300)
    print(report.text)
    raise SystemExit(report.rc)

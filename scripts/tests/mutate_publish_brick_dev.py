#!/usr/bin/env python3
"""Mutation-test the DEV mode of scripts/publish-brick.sh.

Run on demand: `python3 scripts/tests/mutate_publish_brick_dev.py`. Deliberately NOT named `test_*`,
so pytest never collects it — it mutates a tracked file in place (restored in a `finally`).

Dev mode exists because the same brick assertions were hand-written five times, each copy able to
drop a property without a sound. That argument is only worth anything if the properties are pinned.
Every row below removes one, and the suite must notice. The progress invariant carries the most
weight: it is the one failure the tip convergence check cannot see, because convergence still passes
by reverting whatever landed on dev after the branch was cut.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "scripts" / "lib"))
import mutate  # noqa: E402

SUBJECT = REPO / "scripts" / "publish-brick.sh"
SUITE = ["bash", str(REPO / "scripts" / "tests" / "test_publish_brick.sh")]

MUTATIONS = [
    mutate.Mutation(
        "pathspecs are globs again, so a brick file named with glob characters drags its "
        "lookalike into the brick",
        'git_lit() { git --literal-pathspecs -C "$scope" "$@"; }',
        'git_lit() { git -C "$scope" "$@"; }',
    ),
    mutate.Mutation(
        "THE plan-review BLOCKER, restored — literal pathspecs are EXPORTED, reaching the audit, "
        "which then skips every file-discovering check and still prints PASS",
        'git_lit() { git --literal-pathspecs -C "$scope" "$@"; }',
        'git_lit() { git --literal-pathspecs -C "$scope" "$@"; }\nexport GIT_LITERAL_PATHSPECS=1',
    ),
    mutate.Mutation(
        "THE load-bearing dev property — a path that no longer matches the oracle is ignored, so "
        "a dev that moved after the branch was cut is silently reverted at convergence",
        '  [ "${#diverged_files[@]}" -gt 0 ] || return 0',
        "  return 0",
    ),
    mutate.Mutation(
        "the progress invariant never looks at what dev changed, so it can never fire",
        '  [ "${#progress_files[@]}" -gt 0 ] || return 0',
        "  return 0",
    ),
    mutate.Mutation(
        "a listed file that already matches HEAD — a typo, a path in neither tree — becomes a "
        "silently smaller brick instead of a refusal",
        '      if git_lit diff --quiet --no-renames HEAD "$endpoint" -- "$f"; then',
        "      if false; then",
    ),
    mutate.Mutation(
        "a non-conventional subject reaches dev, where no subject guard will ever see it",
        '  [[ "$subject" =~ $re ]] \\',
        "  true \\",
    ),
    mutate.Mutation(
        "a subject in the ADVISE band is no longer refused by name",
        "    advise|block)",
        "    block)",
    ),
    mutate.Mutation(
        "a repo with no policy file refuses every subject — an inert policy read as a block",
        "    inert|ok) ;;",
        "    ok) ;;",
    ),
    mutate.Mutation(
        "--final no longer asserts anything, so a re-derivation short of the oracle reads as done",
        '  [ "$final" = yes ] || return 0',
        "  return 0",
    ),
    mutate.Mutation(
        "a dev brick falls through into the tag step and mints a tag on dev",
        '    rm -f "$files_list"\n    result_line PASS 0\n    return 0\n  fi',
        "  fi",
    ),
    mutate.Mutation(
        "a dev brick writes a CHANGELOG entry that versioning on main never asked for (caught "
        "because changelog_entry.py refuses the version `dev` and every dev brick goes red — not "
        "by the no-CHANGELOG row alone)",
        '  if [ "$mode" = dev ]; then\n    printf \'  changelog: dev mode writes none',
        "  if false; then\n    printf '  changelog: dev mode writes none",
    ),
    mutate.Mutation(
        "a dev brick is accepted on a branch other than dev",
        '      || fail_brick "not on branch $WORKING_BRANCH',
        '      || true "not on branch $WORKING_BRANCH',
    ),
]


def main() -> int:
    baseline = subprocess.run(SUITE, capture_output=True, text=True)
    print(
        f"pre-flight suite: rc={baseline.returncode}  {baseline.stdout.strip()[-60:]}"
    )

    report = mutate.run(SUBJECT, SUITE, MUTATIONS)
    print(report.text)
    return report.rc


if __name__ == "__main__":
    sys.exit(main())

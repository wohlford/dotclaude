#!/usr/bin/env python3
"""Mutation campaign for audit.sh's `tests` reporting, driven by scripts/lib/mutate.py.

Deliberately NOT named `test_*`: it mutates a tracked file in place, so pytest must not collect
it. Run it on demand — `./scripts/tests/mutate_audit_tests_report.py` — and never while editing
the subject, since the restore would clobber your edits.

Why this subject earns a campaign. `check_tests` emits the verdict of the repo's only
deterministic gate, the one the publish path runs at the tip before an irreversible push. Its
reporting was measured TWICE discarding the failing row it existed to show, and neither failure
reproduced, so both diagnoses are gone for good. A suite written by the change's own author
confirms what that author thought of; these mutations ask instead whether the suite would notice
the safety properties being removed one at a time.

Two of these were, in an earlier draft of the plan, uncatchable by construction — no fixture built
a detail block large enough, and every fixture's failure row began `FAIL `. That was a finding
about the TESTS, not a reason to weaken the table: rows 15l and 15m exist because of it. Run this
only against a suite carrying them.

The repo root is derived from __file__, which means invoking this through a symlinked
`~/.claude/scripts/tests/` would resolve into PRODUCTION's tree and mutate that instead. Run it
from the working copy you intend to grade.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "scripts" / "lib"))
import mutate  # noqa: E402

SUBJECT = REPO / "skills" / "audit" / "audit.sh"
SUITE = ["bash", str(REPO / "scripts" / "tests" / "test_audit.sh")]

LIVE_RE = (
    "TESTS_FAILURE_RE='FAIL|ERROR|Traceback|AssertionError|"
    "^E[[:space:]]|fatal:|[0-9]+ (failed|error)'"
)

LIVE_PATH_FIRST = """    if [[ -n "$TESTS_ARTIFACT_DIR" ]]; then
      printf '  full output: %s\\n' "$TESTS_ARTIFACT_DIR"
    else
      printf '  full output: (unavailable — could not create an artifact directory)\\n'
    fi
    printf '%s\\n' "${detail%$'\\n'}" | sed 's/^/  /'"""

MUTATED_PATH_LAST = """    printf '%s\\n' "${detail%$'\\n'}" | sed 's/^/  /'
    if [[ -n "$TESTS_ARTIFACT_DIR" ]]; then
      printf '  full output: %s\\n' "$TESTS_ARTIFACT_DIR"
    else
      printf '  full output: (unavailable — could not create an artifact directory)\\n'
    fi"""

LIVE_DECOLLIDE = """  target="$TESTS_ARTIFACT_DIR/$safe.log"
  n=2
  while [[ -e "$target" ]]; do
    [[ "$n" -gt 99 ]] && return 1
    target="$TESTS_ARTIFACT_DIR/$safe-$n.log"
    n=$((n + 1))
  done"""

LIVE_FALLBACK = """  else
    printf '(no failure-shaped line; last %d lines)\\n' "$TESTS_EXCERPT_MAX"
    printf '%s\\n' "$out" | tail -n "$TESTS_EXCERPT_MAX"
  fi"""

MUTATIONS = [
    mutate.Mutation(
        "the gate's verdict is inverted — a failing suite reports PASS",
        "verdict_fail tests 'test suite failure(s)'",
        "verdict_pass tests",
    ),
    mutate.Mutation(
        "the complete output is never preserved; only the excerpt survives",
        'if tests_artifact_write "$scope" "$t" "$out"; then note=""; '
        "else note=' (full output NOT preserved)'; fi",
        'note=""',
    ),
    mutate.Mutation(
        "the de-collision loop is removed, so one suite's log silently clobbers another's",
        LIVE_DECOLLIDE,
        '  target="$TESTS_ARTIFACT_DIR/$safe.log"',
    ),
    mutate.Mutation(
        "the artifact path is printed AFTER the detail, where a downstream cap eats it first",
        LIVE_PATH_FIRST,
        MUTATED_PATH_LAST,
    ),
    mutate.Mutation(
        "the tail fallback is dropped, so a suite matching nothing prints a bare header",
        LIVE_FALLBACK,
        "  fi",
    ),
    mutate.Mutation(
        "the failure pattern is narrowed to ^FAIL, silently dropping every other failure shape",
        LIVE_RE,
        "TESTS_FAILURE_RE='^FAIL'",
    ),
]


def main() -> int:
    report = mutate.run(SUBJECT, SUITE, MUTATIONS, cwd=str(REPO))
    print(report.text)
    return report.rc


if __name__ == "__main__":
    sys.exit(main())

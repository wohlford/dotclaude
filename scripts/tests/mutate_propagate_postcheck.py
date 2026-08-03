#!/usr/bin/env python3
"""Mutation campaign for scripts/propagate-postcheck.sh, driven by scripts/lib/mutate.py.

Deliberately NOT named `test_*`: it mutates a tracked file in place, so pytest must not collect
it. Run it on demand — `./scripts/tests/mutate_propagate_postcheck.py` — and never while editing
the subject, since the restore would clobber your edits.

The subject is a CHECK, which is where mutation testing earns the most: every failure mode below
turns it into an instrument that reports a verified promote while a dead gate ships. None of them
produce an error, diff noise, or a red suite on their own — a weakened checker looks exactly like
a working one.

Two mutations deserve naming, because they are the ones the suite was initially blind to and
whose rows had to be written before the campaign could grade them:

* **hooks-registered gated on the branch.** Asserting `PASS hooks-registered` on a healthy strict
  promote does not test that the check RAN — a version that skipped it and printed PASS satisfies
  that row too. Only a strict-branch promote carrying a genuinely dropped registration can tell
  the two apart.
* **an undeterminable range failing OPEN.** The fail-closed direction is a design decision, not
  an accident of control flow, so it gets an explicit mutation rather than trusting that the
  `unknown` value happens to fall through the right way.

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

SUBJECT = REPO / "scripts" / "propagate-postcheck.sh"
SUITE = ["bash", str(REPO / "scripts" / "tests" / "test_propagate_postcheck.sh")]

MUTATIONS = [
    mutate.Mutation(
        "hooks-registered is gated on the branch, so a mis-determined range hides a dead gate",
        '  if [[ ! -f "$helper" ]]; then',
        '  if [[ "$in_range" != yes ]]; then verdict_pass hooks-registered\n'
        '  elif [[ ! -f "$helper" ]]; then',
    ),
    mutate.Mutation(
        "byte-identity is asserted on the in-range branch, where the hand-add changes it by design",
        '  if [[ "$in_range" == yes ]]; then',
        '  if [[ "$in_range" == never ]]; then',
    ),
    mutate.Mutation(
        "the identity comparison always holds, so a clobbered runtime file reads clean",
        '  elif [[ "$(lower "$got")" == "$(lower "$before_sha")" ]]; then\n'
        "    verdict_pass settings-identical",
        "  elif true; then\n    verdict_pass settings-identical",
    ),
    mutate.Mutation(
        "an undeterminable range fails OPEN — identity stops being required",
        "    verdict_fail range \\\n"
        '      "no usable pre-merge HEAD ($head_src unset or unresolvable)',
        "    in_range=yes\n"
        "    verdict_fail range \\\n"
        '      "no usable pre-merge HEAD ($head_src unset or unresolvable)',
    ),
    mutate.Mutation(
        "a non-ancestor pre-merge HEAD is believed rather than refused",
        '  elif ! git -C "$scope" merge-base --is-ancestor "$base" "$head_sha" 2>/dev/null; then',
        "  elif false; then",
    ),
    mutate.Mutation(
        "a parked stash stops being a failure, so an unrestored promote passes",
        '  if [[ -z "$out" ]]; then\n    verdict_pass stash-empty',
        "  if true; then\n    verdict_pass stash-empty",
    ),
    mutate.Mutation(
        "a cleared skip-worktree flag is accepted, exposing the runtime file to every checkout",
        "    S*) verdict_pass skip-worktree ;;",
        "    S*|H*) verdict_pass skip-worktree ;;",
    ),
    mutate.Mutation(
        "merge-applied always passes, so a promote that never landed reads as verified",
        '  elif [[ "$head_sha" == "$ref_sha" ]]; then\n    verdict_pass merge-applied',
        "  elif true; then\n    verdict_pass merge-applied",
    ),
    mutate.Mutation(
        "the hooks-check allowlist widens to any RESULT line, so its FAIL reads as clean",
        "      'RESULT: PASS'*) verdict_pass hooks-registered ;;",
        "      'RESULT: '*) verdict_pass hooks-registered ;;",
    ),
    mutate.Mutation(
        "a missing helper passes instead of failing closed — the gate becomes a rubber stamp",
        "    verdict_fail hooks-registered \\\n"
        '      "$helper is missing — the registration check could not run, '
        'which is not the same as passing"',
        "    verdict_pass hooks-registered",
    ),
    mutate.Mutation(
        "the unrecoverable BEFORE digest becomes optional, and is quietly defaulted",
        '  [[ -n "$before_sha" ]] \\',
        '  [[ -n "${before_sha:-x}" ]] \\',
    ),
    mutate.Mutation(
        "an absent runtime settings.json stops being an ERROR",
        '  [[ -f "$scope/$SETTINGS" ]] \\',
        '  [[ -d "$scope" ]] \\',
    ),
    mutate.Mutation(
        "an unknown flag is silently ignored, so a typo'd argument runs the tool's own defaults",
        '      *) fatal "unknown argument: $1" ;;',
        "      *) shift ;;",
    ),
    mutate.Mutation(
        "the verdict decouples from the findings — every run reports PASS",
        '  if [[ "$fail_count" -eq 0 ]]; then\n    result_line PASS 0',
        "  if true; then\n    result_line PASS 0",
    ),
]


def main() -> int:
    report = mutate.run(SUBJECT, SUITE, MUTATIONS, cwd=str(REPO))
    print(report.text)
    return report.rc


if __name__ == "__main__":
    sys.exit(main())

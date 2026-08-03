#!/usr/bin/env python3
"""Mutation campaign for scripts/mutation-anchors-check.py, driven by scripts/lib/mutate.py.

Deliberately NOT named `test_*`: it mutates a tracked file in place, so pytest must not collect
it. Run it on demand — `./scripts/tests/mutate_mutation_anchors_check.py` — and never while
editing the subject, since the restore would clobber your edits.

The subject is a VERIFICATION tool, which is the case where a silently-weakened check costs the
most: every defect it stops catching is one the operator believes has been ruled out. Its own
failure modes are all in the passing direction — a guard dropped here does not error, it reports
a clean sweep over less than it claims.

Every row names ONE safety property and mutates what it names. A row that goes red off some
unrelated assertion raising first is a green suite wearing a red hat, so the labels are written
to be falsifiable: if the suite survives a row, the property that row names is not being tested.

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

SUBJECT = REPO / "scripts" / "mutation-anchors-check.py"
SUITE = [
    sys.executable,
    "-m",
    "pytest",
    str(REPO / "scripts" / "tests" / "test_mutation_anchors_check.py"),
    "-q",
    "--no-header",
]

MUTATIONS = [
    # ---- the vacuous-pass guards. Each of these turns "I checked nothing" into "all clear",
    # which is the failure mode this whole tool exists to make impossible.
    mutate.Mutation(
        "zero campaigns discovered reports PASS — a sweep over nothing, reading as clean",
        "    if not campaigns:",
        "    if False:",
    ),
    mutate.Mutation(
        "a campaign declaring ZERO mutations is accepted, so an empty list grades nothing",
        "    if not rows:",
        "    if False:",
    ),
    mutate.Mutation(
        "ERROR stops outranking FAIL, so an unread campaign reports the weaker verdict",
        '    if errors:\n        status, rc = "ERROR", 2',
        '    if False:\n        status, rc = "ERROR", 2',
    ),
    # ---- what counts as a defect
    mutate.Mutation(
        "an AMBIGUOUS anchor (2 occurrences) stops being a finding",
        "        if count != 1:",
        "        if count == 0:",
    ),
    mutate.Mutation(
        "a MISSING anchor stops being a finding — the leftover-mutant case goes silent",
        "        if count != 1:",
        "        if count > 1:",
    ),
    # ---- the allowlist resolver. An expression it cannot read must ERROR, never be skipped.
    mutate.Mutation(
        "an unreadable expression resolves to the empty string instead of erroring",
        "    raise Unresolvable(\n"
        '        "cannot read a %s statically — make it a string literal or a module-level "\n'
        '        "constant" % type(node).__name__\n'
        "    )",
        '    return ""',
    ),
    mutate.Mutation(
        "a subject that cannot be read is skipped rather than erroring",
        "        raise Unresolvable(\n"
        '            "%s names a subject that cannot be read: %s" % (relative, exc)\n'
        "        ) from None",
        "        return 0, []",
    ),
    mutate.Mutation(
        "a SUBJECT escaping the scope is permitted, aiming the check outside the repo",
        '        if part in ("", ".", "..") or Path(part).is_absolute():',
        "        if False:",
    ),
    # ---- discovery: the population the check claims to cover
    mutate.Mutation(
        "discovery stops being tracked-only, so a scratch campaign can fail an audit",
        '["git", "-C", str(scope), "ls-files"], capture_output=True, text=True',
        '["git", "-C", str(scope), "ls-files", "--others", "--cached"],\n'
        "        capture_output=True,\n"
        "        text=True,",
    ),
    mutate.Mutation(
        "the campaign-name rule widens to every .py file, sweeping the runner itself",
        '        if name.startswith(CAMPAIGN_PREFIX) and name.endswith(".py"):',
        '        if name.endswith(".py"):',
    ),
]


def main() -> int:
    report = mutate.run(SUBJECT, SUITE, MUTATIONS, cwd=str(REPO))
    print(report.text)
    return report.rc


if __name__ == "__main__":
    sys.exit(main())

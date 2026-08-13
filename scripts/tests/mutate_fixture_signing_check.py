#!/usr/bin/env python3
"""Mutation campaign for scripts/fixture-signing-check.py, driven by scripts/lib/mutate.py.

Deliberately NOT named `test_*`: it mutates a tracked file in place, so pytest must not collect
it. Run it on demand — `./scripts/tests/mutate_fixture_signing_check.py` — and never while
editing the subject, since the restore would clobber your edits.

The subject decides whether a test fixture can inherit the operator's global git signing config.
Every one of its failure modes is in the PASSING direction: a guard dropped here does not error,
it reports a clean sweep over a smaller population than it claims. And the defect it exists to
catch is invisible by construction — a fixture that inherits `tag.gpgsign=true` HANGS on a
hardware-key PIN prompt rather than failing, reaching no teardown and emitting no verdict, and a
cached PIN masks it entirely. So a weakened check here is not merely less useful; it restores a
condition nobody can observe from a suite's output.

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

SUBJECT = REPO / "scripts" / "fixture-signing-check.py"
SUITE = [
    sys.executable,
    "-m",
    "pytest",
    str(REPO / "scripts" / "tests" / "test_fixture_signing_check.py"),
    "-q",
    "--no-header",
]

MUTATIONS = [
    # ---- the population. Under-reporting is the direction that costs a silent hang, so each of
    # these shrinks the set of files graded while leaving the verdict reading clean.
    mutate.Mutation(
        "enumeration drops untracked files, so a brand-new violating fixture is never graded",
        '            "--cached",\n            "--others",\n            "--exclude-standard",',
        '            "--cached",',
    ),
    mutate.Mutation(
        "every file counts as a repo creator, so guard-tokenizer input strings are graded too",
        "        if not CREATES.search(raw):\n            continue",
        "        if False:\n            continue",
    ),
    # ---- the config resolution. This hop is the difference between grading four recast modules
    # correctly and reporting all four as violations they are not.
    mutate.Mutation(
        "the same-directory import hop stops contributing, so an imported config is unseen",
        '                text += "\\n" + sib.read_text(errors="replace")',
        "                pass",
    ),
    # ---- the verdict itself. Each of these turns a real finding into a clean exit.
    mutate.Mutation(
        "violations stop affecting the exit status, so a FAIL reports as a clean pass",
        "    return 1 if violations else 0",
        "    return 0",
    ),
    mutate.Mutation(
        "a scan that found nothing reports success instead of erroring — the vacuous pass",
        "    if not creators:",
        "    if False:",
    ),
    mutate.Mutation(
        "an unreadable file is skipped rather than recorded, so it is silently ungraded",
        '            violations.append((rel, [f"unreadable: {exc}"]))',
        "            pass",
    ),
    # ---- which keys are required. Requiring only one leaves the other inherited from global,
    # and `tag.gpgsign` is precisely the one the measured live exposure was missing.
    mutate.Mutation(
        "only commit.gpgsign is required, so the live exposure's exact shape passes",
        "k for k, rx in DISABLED.items() if not rx.search(reachable_text(scope, rel))",
        "k for k, rx in list(DISABLED.items())[:1] if not rx.search(reachable_text(scope, rel))",
    ),
]


def main() -> int:
    report = mutate.run(SUBJECT, SUITE, MUTATIONS, cwd=REPO, timeout=300)
    print(report.text)
    return report.rc


if __name__ == "__main__":
    sys.exit(main())

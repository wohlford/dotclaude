#!/usr/bin/env python3
"""Mutation campaign for scripts/env-claims-check.py, driven by scripts/lib/mutate.py.

Deliberately NOT named `test_*`: it mutates a tracked file in place, so pytest must not collect
it. Run it on demand — `./scripts/tests/mutate_env_claims_check.py` — and never while editing the
subject, since the restore would clobber your edits.

The subject grades the instruction file's environment claims, so every one of its failure modes
is in the PASSING direction: a guard dropped here does not error, it reports a clean sweep over
less than it claims. Worse than usual, because the whole point of the checker is to stop a stale
claim reading as a verified one — a weakened checker rebuilds exactly the defect it exists to
remove, and nothing downstream can tell the difference.

Every row names ONE safety property and mutates what it names. A row that goes red off some
unrelated assertion raising first is a green suite wearing a red hat, so the labels are written
to be falsifiable: if the suite survives a row, the property that row names is not being tested.

Two rows deliberately mutate the SAME anchor in opposite directions — `platform_applies()` forced
True and forced False. They are different defects: forced True ships a permanent false FAIL to
every clone, forced False makes the check vacuous on the one machine whose claims it grades. Only
a killer asserting BOTH directions can tell an always-False predicate from an honestly-False one.

Row 8 mutates the environment SCRUBBING, not the `bash` flags. Measured: an exported function
survives `bash --noprofile --norc -c`, because it arrives through the environment as a
`BASH_FUNC_*` entry rather than from any rc file — so a row dropping those flags would be an
EQUIVALENT MUTANT that no suite row could ever kill.

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

SUBJECT = REPO / "scripts" / "env-claims-check.py"
SUITE = [
    sys.executable,
    "-m",
    "pytest",
    str(REPO / "scripts" / "tests" / "test_env_claims_check.py"),
    "-q",
    "--no-header",
]

MUTATIONS = [
    # ---- the floor. Its only job is to notice a documented path nobody verifies.
    mutate.Mutation(
        "the floor never fails — a documented path covered by nothing stops being reported, so "
        "the table's path coverage can shrink silently",
        "    unaccounted = [p for p in paths if p not in covered and p not in exempt]",
        "    unaccounted = []",
    ),
    # ---- the two vacuous-pass guards. Each turns "I checked nothing" into "all clear".
    mutate.Mutation(
        "the zero-paths guard is dropped — a matcher that reached nothing reports a verdict "
        "instead of an instrument failure",
        "    if not paths:",
        "    if False:",
    ),
    mutate.Mutation(
        "the zero-verified guard is dropped — a table that checked nothing reports PASS",
        "    if verified == 0:",
        "    if False:",
    ),
    # ---- the anchor mechanism: what stops the table verifying a claim the doc no longer makes.
    mutate.Mutation(
        "the stale-anchor check is dropped — the table goes on verifying claims CLAUDE.md has "
        "stopped making, which is the motivating defect rebuilt inside the checker",
        "        if c.anchor not in text:",
        "        if False:",
    ),
    # ---- exemptions: derived from what EXISTS, so a deleted skill stops being exempt.
    mutate.Mutation(
        "the skill exemption is hand-listed rather than derived, so a doc reference to a DELETED "
        "skill stays exempt forever",
        '    skills_dir = scope / "skills"\n'
        "    if not skills_dir.is_dir():\n"
        "        return set()\n"
        '    return {f"/{d.name}" for d in skills_dir.iterdir() if d.is_dir()}',
        '    return {"/debrief", "/propagate", "/recast"}',
    ),
    # ---- the platform predicate, mutated in BOTH directions; see the module docstring.
    mutate.Mutation(
        "platform_applies() returns True unconditionally — every clone of the public main FAILs "
        "on claims that were never about its machine",
        '    if os.environ.get("ENV_CLAIMS_FORCE_INAPPLICABLE"):\n'
        "        return False\n"
        '    return platform.system() == "Darwin" and Path("/opt/local").is_dir()',
        "    return True",
    ),
    mutate.Mutation(
        "platform_applies() returns False unconditionally — the probe phase silently becomes "
        "vacuous on the one machine whose claims it exists to grade",
        '    if os.environ.get("ENV_CLAIMS_FORCE_INAPPLICABLE"):\n'
        "        return False\n"
        '    return platform.system() == "Darwin" and Path("/opt/local").is_dir()',
        "    return False",
    ),
    # ---- the probe: it must grade the ENVIRONMENT, never the harness's injected shim.
    mutate.Mutation(
        "probe_tool stops scrubbing the child environment — an exported BASH_FUNC_* shim answers "
        "the probe and the verdict is a truthful answer about the wrong subject",
        "    env = {\n"
        "        k: v\n"
        "        for k, v in os.environ.items()\n"
        '        if not k.startswith("BASH_FUNC_")\n'
        '        and k not in ("BASH_ENV", "ENV", "SHELLOPTS", "BASHOPTS")\n'
        "    }",
        "    env = dict(os.environ)",
    ),
    # ---- the exit-code contract: inapplicable must never read as verified.
    mutate.Mutation(
        "rc 3 is emitted as rc 0 — a run that skipped every probe starts reading as one where "
        "every claim held",
        "        return 3",
        "        return 0",
    ),
    # ---- phase ORDER: the static half is machine-independent and must never be gated.
    mutate.Mutation(
        "the static phase moves back BEHIND the platform predicate — anchors stop being checked "
        "on every machine without the documented environment, which is every clone",
        "    static_failures: list[str] = []",
        "    if not platform_applies():\n"
        "        sys.stdout.write(\n"
        "            _verdict(\n"
        '                "SKIP",\n'
        "                3,\n"
        "                claims=len(CLAIMS),\n"
        "                verified=0,\n"
        "                stale=0,\n"
        "                paths=len(paths),\n"
        "                exempt=len(exempt & set(paths)),\n"
        "                unaccounted=0,\n"
        "                failed=0,\n"
        "            )\n"
        "        )\n"
        "        sys.stderr.write(\n"
        '            "env-claims-check: the documented environment is not present here, so the '
        'probe "\n'
        '            "phase was skipped. The static checks (floor, anchors) DID run and held.\\n"\n'
        "        )\n"
        "        return 3\n"
        "\n"
        "    static_failures: list[str] = []",
    ),
]


def main() -> int:
    report_path = sys.argv[1] if len(sys.argv) > 1 else None
    report = mutate.run(
        SUBJECT, SUITE, MUTATIONS, cwd=str(REPO), report_path=report_path
    )
    print(report.text)
    return report.rc


if __name__ == "__main__":
    sys.exit(main())

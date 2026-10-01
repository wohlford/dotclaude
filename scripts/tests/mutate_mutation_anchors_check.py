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
    # ---- discovery: the population the check GRADES, and the one it merely reports
    mutate.Mutation(
        "the graded population widens past the commit, so an IGNORED campaign is graded",
        "    return _git_campaigns(scope)",
        '    return _git_campaigns(scope, "--others", "--cached")',
    ),
    mutate.Mutation(
        "untracked discovery loses --exclude-standard, so an ignored campaign false-blocks",
        '    return _git_campaigns(scope, "--others", "--exclude-standard")',
        '    return _git_campaigns(scope, "--others")',
    ),
    mutate.Mutation(
        "the untracked guard goes inert — campaigns are found and then never reported",
        "    for relative in untracked:",
        "    for relative in []:",
    ),
    mutate.Mutation(
        "the verdict under-reports coverage, claiming nothing was skipped when something was",
        "            len(untracked),\n",
        "            0,\n",
    ),
    mutate.Mutation(
        "the campaign-name rule widens to every .py file, sweeping the runner itself",
        '        if name.startswith(CAMPAIGN_PREFIX) and name.endswith(".py"):',
        '        if name.endswith(".py"):',
    ),
    # ---- a mutant that does not PARSE. Each of these leaves the sweep reading clean over a
    # campaign whose mutant never exercises its suite.
    mutate.Mutation(
        "the parse judgment is never run, so an unparseable mutant is never found",
        "    for job, (verdict, detail) in zip(jobs, verdicts):",
        "    for job, (verdict, detail) in []:",
    ),
    mutate.Mutation(
        "an invalid mutant stops failing the run — it is printed and the verdict is PASS",
        "    elif findings or invalids:",
        "    elif findings:",
    ),
    mutate.Mutation(
        "`unchecked` is reported as 0, so a clean run claims coverage it does not have",
        "            len(invalids),\n            sum(unchecked.values()),",
        "            len(invalids),\n            0,",
    ),
    mutate.Mutation(
        # Expect: test_a_pool_that_raises_anything_else_still_falls_back_to_serial.
        "the pool fallback is narrowed back to (BrokenProcessPool, OSError)",
        "    except Exception:  # noqa: BLE001 — see below",
        "    except (concurrent.futures.process.BrokenProcessPool, OSError):",
    ),
    mutate.Mutation(
        # Expect: test_a_judge_that_raises_is_CANNOT_RUN_with_a_RESULT_line.
        "the per-row judge no longer catches a raise — main dies with no RESULT line",
        "    except Exception as exc:  # noqa: BLE001 — a judge that raised reached no verdict",
        "    except ZeroDivisionError as exc:  # noqa: BLE001 — a judge that raised reached no verdict",
    ),
    mutate.Mutation(
        # Expect: test_a_bash_parser_that_cannot_run_is_ERROR_and_not_counted_unchecked.
        "CANNOT_RUN rows fall into the unchecked bucket — a failed parser reads as by-design",
        "        elif verdict == mutate.CANNOT_RUN:",
        "        elif False:",
    ),
    mutate.Mutation(
        # Expect: test_a_bash_parser_that_cannot_run_is_ERROR_and_not_counted_unchecked.
        "CANNOT_RUN rows are collected but never become an ERROR entry — the run PASSes",
        "    if cannot_run:\n        errors.append(",
        "    if False:\n        errors.append(",
    ),
    mutate.Mutation(
        "a row whose `new` cannot be read is treated as valid: neither judged nor counted",
        '            unchecked.append("`new` is not a static literal")',
        "            pass",
    ),
    mutate.Mutation(
        "a stranded mutate.py sidecar stops refusing a verdict from a possibly-mutated judge",
        "    return sorted(p for p in found if p.exists())",
        "    return []",
    ),
]


def main() -> int:
    report = mutate.run(SUBJECT, SUITE, MUTATIONS, cwd=str(REPO))
    print(report.text)
    return report.rc


if __name__ == "__main__":
    sys.exit(main())

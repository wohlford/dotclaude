#!/usr/bin/env python3
"""Mutation campaign for skills/debrief/ledger.py, driven by scripts/lib/mutate.py.

Deliberately NOT named `test_*`: it mutates a tracked file in place, so pytest must not collect
it. Run it on demand — `./scripts/tests/mutate_debrief_ledger.py` — and never while editing the
subject, since the restore would clobber your edits.

## The row this campaign exists for

`test_complete_ledger_asks_nothing` asserts that a COMPLETE ledger produces no question. That is
the specific defect the three-valued classification corrects: a reader that asked whenever a
ledger existed would prompt on every debrief forever and silently repeal `/debrief`'s own central
contract, "a plain `/debrief` never pauses". A row asserting an ABSENCE is exactly the shape that
passes for free, so `prompt-always` below deletes the completeness predicate and requires the
suite to notice. Without that row, "the test asserts no question is asked" is a claim about
wording rather than about behaviour.

Its mirror, `never-prompts`, is here for the opposite failure: a classification that answers
PROCEED for everything also passes an absence assertion, and only the RESUMABLE rows catch it.

## The second row worth naming: a resume must never go BACKWARDS

Step 5 runs *only* when the user asks for an automation design at invocation, so a plain run
passes it by. `gap-is-a-resume-point` and `skip-not-recorded` attack that from the two sides it
can fail from — the RULE (a gap below a recorded step is a step already passed) and the RECORD
(the skip itself). Either alone routes a resumed plain `/debrief` into step 5 and dispatches
`/feature --plan-only` unrequested, which is an authorization failure rather than a wasted turn,
so both sides get their own mutant and their own row.

The `skip-*` rows beneath them cover the other half of the same property: a skipped step is
RECORDED but never COMPLETED, and every place the report could blur those two — the step list's
marker, the dedicated skipped line, the re-run costs — is mutated separately, because a report
that blurs them lets a resumed run conclude an automation was designed when none was.

Every other row names ONE property the suite (`skills/debrief/tests/test_ledger.py`) claims to
defend and mutates exactly what it names. A survivor is evidence about the SUITE, not about the
subject: strengthen the suite row, never reword the assertion here.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "scripts" / "lib"))
import mutate  # noqa: E402

SUBJECT = REPO / "skills" / "debrief" / "ledger.py"
SUITE = [
    sys.executable,
    "-m",
    "pytest",
    "-q",
    str(REPO / "skills" / "debrief" / "tests" / "test_ledger.py"),
]

MUTATIONS = [
    # ---- the classification: the no-prompt path, in both directions
    mutate.Mutation(
        "prompt-always: the completeness predicate always says NO, so every ledger — including a "
        "completed one — classifies RESUMABLE and the routine asks on every debrief. This is the "
        "design defect `test_complete_ledger_asks_nothing` exists to catch",
        "return self.completed_at is not None",
        "return False",
    ),
    mutate.Mutation(
        "never-prompts: the completeness predicate always says YES, so an INTERRUPTED run reads "
        "as finished and nothing is ever offered for resumption",
        "        return self.completed_at is not None",
        "        return True",
    ),
    mutate.Mutation(
        "completion-not-recorded: `complete` stops writing the completion record, so a finished "
        "run is indistinguishable from one interrupted at step 7",
        '        self.data["completed_at"] = at',
        '        self.data["completed_at"] = self.data.get("completed_at")',
    ),
    # ---- malformed input must report, not crash
    mutate.Mutation(
        "malformed-crashes: a ledger that cannot be parsed propagates instead of returning the "
        "MALFORMED verdict, so the routine dies on its own bookkeeping file",
        "        return 4, malformed_report(path, str(exc))",
        "        raise RuntimeError(str(exc))",
    ),
    mutate.Mutation(
        "malformed-shape-unchecked: `steps` is no longer required to be a list, so a wrong-shaped "
        "ledger reads as a legitimate one",
        "    if not isinstance(steps, list):",
        "    if False:",
    ),
    # ---- the report must MOVE with its input
    mutate.Mutation(
        "report-frozen: the re-run notes are emitted for EVERY step rather than for the steps the "
        "ledger records, so the report no longer varies with what the run reached",
        "        for number in step_numbers",
        "        for number in sorted(RERUN_COST)",
    ),
    mutate.Mutation(
        "next-step-frozen: the resume point is always the last step, so it carries no "
        "information about how far the run actually got",
        "        return min(max(recorded) + 1, LAST_STEP)",
        "        return LAST_STEP",
    ),
    # ---- the resume point must never go BACKWARDS into a step the run passed
    mutate.Mutation(
        "gap-is-a-resume-point: `next_step` reverts to *lowest unrecorded step*, so a run that "
        "recorded 0-4 and 6 resumes at the GAP, 5 — dispatching `/feature --plan-only` for an "
        "automation design the user never requested. This is the authorization defect the "
        "`{0,1,2,3,4,6}` row exists to catch",
        "        return min(max(recorded) + 1, LAST_STEP)",
        "        return next(\n"
        "            (c for c in range(FIRST_STEP, LAST_STEP + 1) if c not in recorded),\n"
        "            LAST_STEP,\n"
        "        )",
    ),
    mutate.Mutation(
        "skip-not-recorded: a SKIPPED step stops counting toward the resume point, so the "
        "routine goes back to the step it deliberately did not run — the same unauthorized "
        "step-5 dispatch, reached by removing the record instead of the rule",
        "        recorded = set(self.step_numbers)",
        "        recorded = set(self.done_steps)",
    ),
    # ---- a skipped step must never be presented as completed work
    mutate.Mutation(
        "skip-reads-as-done: `--skipped` is parsed and then discarded, so every step lands as "
        "`done` and a step 5 that never ran reads as a finished automation design",
        '        kept.append({"step": number, "at": at, "disposition": disposition})',
        '        kept.append({"step": number, "at": at, "disposition": DONE})',
    ),
    mutate.Mutation(
        "skip-unmarked: the step list drops the `(skipped)` marker, so `0, 1, 2, 3, 4, 5` "
        "describes a run whose step 5 never happened",
        "        f\"{item['step']} (skipped)\"",
        '        str(item["step"])',
    ),
    mutate.Mutation(
        "skip-line-dropped: the report stops naming the skipped steps on their own line, so the "
        "only thing distinguishing them from completed work is a parenthetical",
        "    if run.skipped_steps:",
        "    if False:",
    ),
    mutate.Mutation(
        "skipped-quoted-a-rerun-cost: the re-run costs are emitted for every RECORDED step "
        "rather than every COMPLETED one, so a skipped step is listed among work already done",
        "        lines.extend(rerun_notes(run.done_steps))",
        "        lines.extend(rerun_notes(run.step_numbers))",
    ),
    mutate.Mutation(
        "disposition-unvalidated-on-write: `record_step` accepts any disposition, so a typo "
        "lands in the record as a third, undefined state",
        "        if disposition not in DISPOSITIONS:",
        "        if False:",
    ),
    mutate.Mutation(
        "disposition-unchecked-on-read: an unrecognised disposition is no longer MALFORMED, so "
        "it is silently absorbed into `done` by the default",
        '        if "disposition" in item and item["disposition"] not in DISPOSITIONS:',
        "        if False:",
    ),
    # ---- run identity: concurrent sessions, and same-second starts
    mutate.Mutation(
        "session-unscoped: the read stops filtering by session, so a concurrent session's ledger "
        "is read back as this session's",
        '        if match and match.group("session") == session_id:',
        "        if match:",
    ),
    mutate.Mutation(
        "collision-order: two runs starting in the same second are ordered by filename, which "
        "gets it backwards — `…Z-2.json` sorts before `…Z.json`, so the newest reads as oldest",
        '            found.append((match.group("started"), sequence, candidate))',
        '            found.append((match.group("started"), candidate.name, candidate))',
    ),
    mutate.Mutation(
        "start-overwrites: a colliding run filename is reused instead of suffixed, so a second "
        "live run clobbers the first one's ledger",
        "    while candidate.exists():",
        "    while False:",
    ),
    # ---- lifecycle: the operator's choice, and the archive
    mutate.Mutation(
        "start-never-refuses: `start` buries an incomplete ledger without `--supersede`, so the "
        "resume-or-restart choice can be skipped silently",
        "    if stale is not None and not supersede_incomplete:",
        "    if False:",
    ),
    mutate.Mutation(
        "supersede-deletes: a superseded ledger is destroyed rather than archived, so an operator "
        "who chose a fresh start can never see how far the abandoned run got",
        "    path.replace(target)",
        "    path.unlink()",
    ),
    mutate.Mutation(
        "step-bound-dropped: a step number outside the routine's 0-7 is accepted, so a typo lands "
        "in the record as a real step",
        "        if not FIRST_STEP <= number <= LAST_STEP:",
        "        if False:",
    ),
    mutate.Mutation(
        "resume-forgets: `resume` stops noting itself, so the artifact cannot show that a run was "
        "picked back up",
        '        self.data.setdefault("resumed_at", []).append(at)',
        '        self.data.setdefault("resumed_at", [])',
    ),
]


def main() -> int:
    report_path = sys.argv[1] if len(sys.argv) > 1 else None
    report = mutate.run(SUBJECT, SUITE, MUTATIONS, cwd=REPO, report_path=report_path)
    print(report.text)
    return report.rc


if __name__ == "__main__":
    sys.exit(main())

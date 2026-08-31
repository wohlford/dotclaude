#!/usr/bin/env python3
"""ledger — record which `/debrief` steps a run finished, and read it back honestly.

`/debrief` is a seven-step routine whose expensive middle (steps 1-4) is delegate invocations, and
whose step 0 writes to `BACKLOG.md`. When a run is interrupted — a compaction, a crash, a session
that ended — the next invocation has no way to know how far the last one got. Conversation memory
is not an artifact, and **step-0 completion is not derivable from any artifact at all**: a *keep*
disposition writes nothing, so a finished step 0 and a step 0 that never ran leave byte-identical
backlogs. This module is where that non-derivable state is written down.

## Why a helper rather than a hand-written file each run

The same reason `backlog.py` exists next door. A format re-invented per run cannot be read back
by a later run, and a reader that has to guess is a reader that will guess wrong in the direction
nobody notices. So the ledger has exactly one writer and one reader, and the classification a
resuming run acts on (`PROCEED` / `RESUMABLE` / `MALFORMED`) is decided here, once.

## The classification is the whole point, and it is THREE-valued for a reason

`/debrief`'s central contract is that a plain run never pauses. A reader that asked "resume or
restart?" whenever a ledger existed would prompt on every debrief forever and silently repeal
that contract. The ambiguity that justifies a question exists ONLY for an INCOMPLETE ledger:

  * **absent**, or **complete** -> `PROCEED`, exit 0. Nothing to resume. No question.
  * **incomplete** -> `RESUMABLE`, exit 3. Report what was recorded and ask.
  * **malformed** -> `MALFORMED`, exit 4. Report and offer a fresh start; never raise.

`complete` versus `incomplete` is what the completion record at step 7 buys: without it,
*finished at 7* and *interrupted at 7* are the same file.

## Filenames carry run identity because a session id is not one

Measured 2026-08-24: one session id spanned 23 days and every restart in between, so a ledger
keyed on session id alone hands a week-old run's ledger today's id. Run identity is therefore
session id PLUS the run's start timestamp, both in the filename. The session id scopes the read
(the project memory directory is shared by concurrent sessions of the same project, and a fixed
name lets two live runs clobber one file); the timestamp orders the runs and separates them.
`start` never overwrites: a colliding name gains a `-2`, `-3`, ... suffix rather than replacing a
run that may still be live.

## A recorded step is not necessarily a COMPLETED one

Step 5 (design an automation) runs *only* when the user asks for it at invocation, so a plain run
reaches it and passes it by. If that skip is never written down, the run's ledger has a HOLE at 5,
and a resume that reads the hole as "the next thing to do" dispatches `/feature --plan-only` —
an automation-design session the user never requested. That is an authorization property, not a
convenience.

So a step is recorded with a DISPOSITION — `done` or `skipped`. Both count as RECORDED, so the
resume point advances past a skip; only `done` is ever presented as completed or quoted a re-run
cost. `next_step()` additionally treats any GAP below a recorded step as passed, since steps are
recorded in order — a backstop for a ledger whose skip was never written, and a rule that names no
step number.

## The re-run notes are DERIVED, not prose the operator has to trust

`RERUN_COST` below is the single source for what re-running a recorded step costs, and the report
quotes it only for the steps the ledger actually records. That is what makes the report MOVE with
its input: a different set of completed steps yields different text. It also keeps the report from
ever saying "re-running is safe", which is FALSE for steps 4 and 5.

Usage:
  ledger.py --memory-dir DIR status
  ledger.py --memory-dir DIR start [--supersede]
  ledger.py --memory-dir DIR resume
  ledger.py --run PATH step N [--skipped]
  ledger.py --run PATH complete

Exit codes:
  0  PROCEED (status), or the write landed
  1  refused (no ledger to resume, an incomplete ledger in the way, an unreadable directory)
  2  usage error
  3  RESUMABLE — an incomplete ledger for this session (status only)
  4  MALFORMED — a ledger file that cannot be read back (status only)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

RUNS_SUBDIR = "debrief-runs"
SUPERSEDED_SUBDIR = "superseded"
SCHEMA = 1
FIRST_STEP = 0
LAST_STEP = 7
UNKNOWN_SESSION = "unknown-session"

# A recorded step carries a DISPOSITION, because "recorded" and "completed" are not the same
# fact and conflating them is what routed a resume into an unrequested design session. `done` =
# the step ran to its end; `skipped` = the routine reached it and deliberately did not run it
# (step 5 in every plain run). Both count as RECORDED — a skipped step must never become a
# resume point — and only `done` is ever reported as completed or quoted a re-run cost.
#
# An entry with no `disposition` key reads as `done`: that is what the key's absence meant
# before the field existed, and a ledger already on disk must not be re-read as skipping steps
# it actually finished. The allowlist below is what a *present* value is cleared against, so a
# value nobody anticipated is MALFORMED rather than quietly treated as one of these two.
DONE = "done"
SKIPPED = "skipped"
DISPOSITIONS = (DONE, SKIPPED)
# `run-<session-id>-<YYYYMMDDTHHMMSSZ>[-<n>].json`. The session id is matched loosely (any run of
# name-safe characters) so that a harness may use a readable stand-in; only OUR files match, so a
# stray file in the directory is never mistaken for a ledger.
#
# The `-<n>` collision suffix is CAPTURED rather than skipped, because it is what orders two runs
# that started in the same second. Sorting those by filename gets it backwards — `…Z-2.json` sorts
# BEFORE `…Z.json`, since `-` precedes `.` — so the newest run would read as the oldest.
RUN_NAME_RE = re.compile(
    r"^run-(?P<session>[A-Za-z0-9_.-]+?)-(?P<started>\d{8}T\d{6}Z)"
    r"(?:-(?P<seq>\d+))?\.json$"
)
SESSION_ENV = "CLAUDE_CODE_SESSION_ID"

# What re-running an already-recorded step costs. Steps 0, 4 and 5 are MEASURED against
# `backlog.py` on a scratch copy (2026-08-24); the rest state the mechanism without claiming a
# measurement, because none was taken. Nothing here says "safe" as a blanket.
RERUN_COST = {
    0: (
        "measured: re-running duplicates evidence notes (`append` returns 0 twice on the same "
        "day and the note lands twice) and re-stamps promotions with today's date, resetting "
        "the age clock the stall report reads. `close` on an already-closed entry refuses, "
        "byte-unchanged. Nothing is erased."
    ),
    1: "re-invokes `revise-claude-md` and re-applies its edits: a delegate's cost, not corruption.",
    2: "re-invokes `claude-md-improver` and re-applies its edits: a delegate's cost, not corruption.",
    3: (
        "re-writes memory entries. The step-3 protocol updates an existing file rather than "
        "creating a second one, so this costs a rewrite, not a duplicate — provided that "
        "protocol is followed."
    ),
    4: (
        "MEASURED HARMFUL: `add` of a REGENERATED entry returns 0 and silently DUPLICATES it — "
        "the duplicate guard compares byte-identical heads, and a regenerated entry differs by "
        "more than its date prefix. Search BACKLOG.md's open section for an entry covering the "
        "same pick BEFORE any `add`, and skip it if one is there."
    ),
    5: (
        "MEASURED HARMFUL: same `add` duplication as step 4, plus a second `/feature --plan-only` "
        "run. Cross-check BACKLOG.md's open section for the deferral before filing it again."
    ),
    6: "re-invokes `/commit`; with no tracked changes it says so and continues.",
    7: "reprints the hand-off.",
}


class LedgerError(Exception):
    """Base class for every refusal this module raises."""


def utc_now() -> datetime:
    """The current time, in UTC. Isolated so tests can pin it."""
    return datetime.now(timezone.utc)


def stamp(moment: datetime) -> str:
    """Render a moment as the `2026-08-24T18:03:11Z` form used inside the file."""
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def name_stamp(moment: datetime) -> str:
    """Render a moment as the `20260824T180311Z` form used inside a filename."""
    return moment.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def resolve_session_id(explicit: str | None = None) -> str:
    """The session id to scope on: the flag, else the environment, else a named fallback.

    The fallback is named rather than empty so that every report can say WHICH session it looked
    for. "Found nothing" that cannot name where it looked is the reading that stops anyone from
    looking further.
    """
    if explicit:
        return explicit
    value = (os.environ.get(SESSION_ENV) or "").strip()
    return value if value else UNKNOWN_SESSION


def runs_dir(memory_dir: Path) -> Path:
    """The ledger directory beneath a project memory directory."""
    return Path(memory_dir) / RUNS_SUBDIR


def disposition_of(item: dict) -> str:
    """A recorded step's disposition, defaulting to `done` when the key is absent."""
    return item.get("disposition", DONE)


# ---------- the record ----------


class Run:
    """One `/debrief` run's ledger: where it lives, and what it recorded."""

    def __init__(self, path: Path, data: dict) -> None:
        self.path = Path(path)
        self.data = data

    @property
    def session_id(self) -> str:
        """The session this run belonged to."""
        return self.data["session_id"]

    @property
    def started(self) -> str:
        """When the run began, as an ISO-8601 UTC stamp."""
        return self.data["run_started"]

    @property
    def steps(self) -> list[dict]:
        """Recorded step completions, ascending by step number."""
        return sorted(self.data["steps"], key=lambda item: item["step"])

    @property
    def step_numbers(self) -> list[int]:
        """Every step number RECORDED — done and skipped alike — ascending.

        This is the resume arithmetic's input, and skipped steps belong in it: a step the run
        deliberately passed must not become the point a resume goes back to.
        """
        return [item["step"] for item in self.steps]

    @property
    def done_steps(self) -> list[int]:
        """The steps that actually RAN to completion, ascending.

        Everything that reports a step as finished — the re-run costs above all — reads this and
        never `step_numbers`, so a skipped step can never be presented as completed work.
        """
        return [item["step"] for item in self.steps if disposition_of(item) == DONE]

    @property
    def skipped_steps(self) -> list[int]:
        """The steps the run reached and deliberately did NOT run, ascending."""
        return [item["step"] for item in self.steps if disposition_of(item) == SKIPPED]

    @property
    def completed_at(self) -> str | None:
        """When the run recorded completion, or None if it never did."""
        return self.data.get("completed_at")

    @property
    def is_complete(self) -> bool:
        """Whether a completion record was written.

        This is the ONE predicate the no-prompt path depends on. `status` asks nothing else
        before deciding between PROCEED and RESUMABLE, so there is no second, weaker reading of
        "complete" for a mutating path to consult.
        """
        return self.completed_at is not None

    @property
    def resumed_at(self) -> list[str]:
        """Stamps of every prior resume of this run."""
        return list(self.data.get("resumed_at", []))

    def next_step(self) -> int:
        """The step a resume must continue at: the first one this run has not reached.

        NOT the lowest unrecorded step. Steps are recorded in order, so a step sitting BELOW one
        already recorded is a step the run PASSED — it was skipped, whether or not the skip was
        itself recorded. Reading such a gap as the resume point sends the routine BACKWARDS into a
        step it deliberately did not take, and for step 5 that means dispatching a
        `/feature --plan-only` design session the user never asked for. That is an authorization
        property, not a convenience, which is why the rule lives here rather than in the caller:
        `SKILL.md` forbids the operator re-deriving the resume point, and a rule re-derived at the
        call site drifts from the one the tool applies.

        The rule names no step number. Recording the skip (`step N --skipped`) is the primary
        mechanism and this is its backstop, for a ledger written before the skip was recorded or
        by a run that failed to record it.
        """
        recorded = set(self.step_numbers)
        if not recorded:
            return FIRST_STEP
        return min(max(recorded) + 1, LAST_STEP)

    def write(self) -> None:
        """Persist the record. Writes to a sibling temp file, then renames over the target."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_name(self.path.name + ".tmp")
        temp.write_text(json.dumps(self.data, indent=2, sort_keys=True) + "\n")
        temp.replace(self.path)

    def record_step(self, number: int, at: str, disposition: str = DONE) -> None:
        """Record what became of `number` at `at`, replacing any earlier record of that step.

        `disposition` is `done` or `skipped`. A SKIPPED record is not bookkeeping trivia: it is
        how "the routine reached this step and deliberately did not run it" becomes a fact about
        the run rather than a fact about the conversation, which is the only place it lived
        before. Without it a plain run leaves step 5 unrecorded, and step 5 is the one that
        dispatches a design session.

        Raises:
            LedgerError: if `number` is outside the routine's own range of steps, or
                `disposition` is not one of the two allowed values.
        """
        if not FIRST_STEP <= number <= LAST_STEP:
            raise LedgerError(f"step must be {FIRST_STEP}-{LAST_STEP}, got {number}")
        if disposition not in DISPOSITIONS:
            raise LedgerError(
                f"disposition must be one of {', '.join(DISPOSITIONS)}, got {disposition!r}"
            )
        kept = [item for item in self.data["steps"] if item["step"] != number]
        kept.append({"step": number, "at": at, "disposition": disposition})
        self.data["steps"] = sorted(kept, key=lambda item: item["step"])

    def record_completion(self, at: str) -> None:
        """Write the completion record that distinguishes *finished* from *interrupted*."""
        self.data["completed_at"] = at

    def record_resume(self, at: str) -> None:
        """Note that this run was picked up again, so the artifact shows the resume happened."""
        self.data.setdefault("resumed_at", []).append(at)


def parse_run(path: Path) -> Run:
    """Read a ledger file, or raise `LedgerError` naming what is wrong with it.

    Every shape failure raises the same exception type on purpose: the caller's job is to report
    *malformed* and offer a fresh start, never to crash the routine it was invoked to protect.
    """
    try:
        raw = path.read_text()
    except OSError as exc:
        raise LedgerError(f"cannot read {path}: {exc}") from None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise LedgerError(f"{path.name}: not valid JSON ({exc})") from None
    if not isinstance(data, dict):
        raise LedgerError(
            f"{path.name}: top level is {type(data).__name__}, not an object"
        )
    for key, kind in (("session_id", str), ("run_started", str)):
        if not isinstance(data.get(key), kind):
            raise LedgerError(f"{path.name}: missing or non-string `{key}`")
    steps = data.get("steps")
    if not isinstance(steps, list):
        raise LedgerError(f"{path.name}: `steps` is missing or not a list")
    for item in steps:
        if not isinstance(item, dict) or not isinstance(item.get("step"), int):
            raise LedgerError(f"{path.name}: a `steps` entry has no integer `step`")
        # Cleared by ALLOWLIST: a disposition nobody anticipated is MALFORMED, never silently
        # folded into `done`. An ABSENT key is the one legal omission — it predates the field.
        if "disposition" in item and item["disposition"] not in DISPOSITIONS:
            raise LedgerError(
                f"{path.name}: step {item['step']} has disposition "
                f"{item['disposition']!r}, not one of {', '.join(DISPOSITIONS)}"
            )
    completed = data.get("completed_at")
    if completed is not None and not isinstance(completed, str):
        raise LedgerError(f"{path.name}: `completed_at` is neither null nor a string")
    return Run(path, data)


# ---------- discovery ----------


def session_runs(memory_dir: Path, session_id: str) -> list[Path]:
    """Every ledger file for this session, oldest first.

    Ordering comes from the filename's timestamp and collision sequence, not from the file's
    contents: a MALFORMED file cannot be parsed, and the newest file is exactly the one whose
    contents may be unreadable.
    """
    directory = runs_dir(memory_dir)
    if not directory.is_dir():
        return []
    found = []
    for candidate in directory.iterdir():
        match = RUN_NAME_RE.match(candidate.name)
        if match and match.group("session") == session_id:
            sequence = int(match.group("seq") or 1)
            found.append((match.group("started"), sequence, candidate))
    return [path for _, _, path in sorted(found)]


def latest_run(memory_dir: Path, session_id: str) -> Path | None:
    """The newest ledger file for this session, or None."""
    runs = session_runs(memory_dir, session_id)
    return runs[-1] if runs else None


def new_run_path(memory_dir: Path, session_id: str, moment: datetime) -> Path:
    """A ledger path for a run starting now, never colliding with one already on disk."""
    directory = runs_dir(memory_dir)
    base = f"run-{session_id}-{name_stamp(moment)}"
    candidate = directory / f"{base}.json"
    suffix = 1
    while candidate.exists():
        suffix += 1
        candidate = directory / f"{base}-{suffix}.json"
    return candidate


def supersede(path: Path) -> Path:
    """Move a ledger out of the live directory so a later read cannot mix runs.

    Superseded ledgers are archived rather than deleted: an operator who chose a fresh start may
    still want to see what the abandoned run had reached, and an accumulating pile of incomplete
    ledgers in the live directory is exactly what would make the next read ambiguous.
    """
    target_dir = path.parent / SUPERSEDED_SUBDIR
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / path.name
    suffix = 1
    while target.exists():
        suffix += 1
        target = target_dir / f"{path.stem}-{suffix}{path.suffix}"
    path.replace(target)
    return target


# ---------- the report ----------


def rerun_notes(step_numbers: list[int]) -> list[str]:
    """One `step N — <cost>` line per COMPLETED step, derived from `RERUN_COST`.

    Callers pass `run.done_steps`, never `run.step_numbers`: a step that was skipped never ran,
    so it has no re-run to cost, and listing it here would present it as work already done.
    """
    return [
        f"    step {number} — {RERUN_COST.get(number, 'no recorded cost for this step.')}"
        for number in step_numbers
    ]


def proceed_report(memory_dir: Path, session_id: str, run: Run | None) -> list[str]:
    """The PROCEED report: states what it looked for and where, and asks nothing."""
    lines = [
        f"debrief-ledger: PROCEED — no incomplete run for session {session_id} "
        f"in {runs_dir(memory_dir)}"
    ]
    if run is None:
        lines.append("  no ledger for this session; this is a fresh run.")
    else:
        lines.append(
            f"  most recent: {run.path.name} — started {run.started}, "
            f"completed {run.completed_at}."
        )
        lines.append(f"  recorded steps: {format_steps(run.steps)}")
    lines.append(
        "  Nothing to resume. Continue the routine from step 0 without pausing."
    )
    return lines


def resumable_report(run: Run) -> list[str]:
    """The RESUMABLE report: what the interrupted run reached, and what re-running each step costs."""
    lines = [
        f"debrief-ledger: RESUMABLE — {run.path}",
        f"  session: {run.session_id}",
        f"  run started: {run.started} (no completion record — the run was interrupted)",
        f"  recorded steps: {format_steps(run.steps)}",
        f"  resume at step: {run.next_step()}",
    ]
    if run.skipped_steps:
        # Named separately, and never folded in with the completed steps: a resumed run that
        # read step 5 as finished would conclude an automation had been designed when the
        # previous run's step 5 never ran at all.
        lines.append(
            f"  SKIPPED, never run — do NOT treat as completed, and do not run them on the "
            f"resume: {format_numbers(run.skipped_steps)}"
        )
    if run.resumed_at:
        lines.append(f"  already resumed at: {', '.join(run.resumed_at)}")
    if run.done_steps:
        lines.append("  What re-running each COMPLETED step costs:")
        lines.extend(rerun_notes(run.done_steps))
    elif not run.step_numbers:
        lines.append(
            "  No step finished, so nothing would be re-run; resuming and restarting are "
            "the same work."
        )
    return lines


def malformed_report(path: Path | None, reason: str) -> list[str]:
    """The MALFORMED report: names the file and the defect, and offers a fresh start."""
    where = str(path) if path is not None else "(unknown path)"
    return [
        f"debrief-ledger: MALFORMED — {where}",
        f"  reason: {reason}",
        "  Nothing can be resumed from it, and nothing was changed. Offer the operator a fresh "
        "start (`start --supersede` archives this file), and say that the previous run's "
        "progress is unknown rather than absent.",
    ]


def format_steps(steps: list[dict]) -> str:
    """Render the recorded steps, marking the skipped ones, and naming emptiness.

    The marker is not decoration. `recorded steps: 0, 1, 2, 3, 4, 5, 6` and
    `recorded steps: 0, 1, 2, 3, 4, 5 (skipped), 6` describe different runs, and only the second
    one is honest about a step-5 that never dispatched `/feature --plan-only`. Every place this
    routine prints a step list prints it through here, so there is no second rendering that
    could omit the marker.
    """
    if not steps:
        return "(none)"
    return ", ".join(
        f"{item['step']} (skipped)"
        if disposition_of(item) == SKIPPED
        else str(item["step"])
        for item in steps
    )


def format_numbers(numbers: list[int]) -> str:
    """Render a bare list of step numbers, naming emptiness rather than printing a blank."""
    return ", ".join(str(number) for number in numbers) if numbers else "(none)"


# ---------- commands ----------


def cmd_status(memory_dir: Path, session_id: str) -> tuple[int, list[str]]:
    """Classify this session's newest ledger. Returns (exit code, report lines)."""
    path = latest_run(memory_dir, session_id)
    if path is None:
        return 0, proceed_report(memory_dir, session_id, None)
    try:
        run = parse_run(path)
    except LedgerError as exc:
        return 4, malformed_report(path, str(exc))
    if run.is_complete:
        return 0, proceed_report(memory_dir, session_id, run)
    return 3, resumable_report(run)


def cmd_start(
    memory_dir: Path, session_id: str, supersede_incomplete: bool
) -> tuple[int, list[str]]:
    """Begin a new run's ledger, refusing to leave an incomplete one lying beside it."""
    existing = latest_run(memory_dir, session_id)
    stale = None
    if existing is not None:
        try:
            stale = None if parse_run(existing).is_complete else existing
        except LedgerError:
            stale = existing
    if stale is not None and not supersede_incomplete:
        return 1, [
            f"debrief-ledger: REFUSED — {stale} is incomplete or unreadable.",
            "  Run `status` and put the resume-or-restart choice to the operator. Re-run "
            "`start --supersede` only once they have chosen a fresh start.",
        ]
    archived = supersede(stale) if stale is not None else None
    moment = utc_now()
    path = new_run_path(memory_dir, session_id, moment)
    run = Run(
        path,
        {
            "schema": SCHEMA,
            "session_id": session_id,
            "run_started": stamp(moment),
            "steps": [],
            "completed_at": None,
        },
    )
    run.write()
    lines = [f"debrief-ledger: STARTED — {path}"]
    if archived is not None:
        lines.append(f"  superseded: {archived}")
    lines.append(f"  Record each finished step with: ledger.py --run {path} step <N>")
    lines.append(
        f"  Record a step the routine REACHED and did not run — step 5 in a plain run — with: "
        f"ledger.py --run {path} step <N> --skipped"
    )
    return 0, lines


def cmd_resume(memory_dir: Path, session_id: str) -> tuple[int, list[str]]:
    """Pick the incomplete ledger back up, noting the resume inside it."""
    path = latest_run(memory_dir, session_id)
    if path is None:
        return 1, ["debrief-ledger: REFUSED — no ledger for this session to resume."]
    try:
        run = parse_run(path)
    except LedgerError as exc:
        return 1, [f"debrief-ledger: REFUSED — {exc}"]
    if run.is_complete:
        return 1, [
            f"debrief-ledger: REFUSED — {path} records a COMPLETED run; there is nothing to "
            "resume. Start a new run instead."
        ]
    run.record_resume(stamp(utc_now()))
    run.write()
    return 0, [
        f"debrief-ledger: RESUMED — {path}",
        f"  recorded steps: {format_steps(run.steps)}",
        f"  continue at step {run.next_step()}",
    ]


def cmd_step(
    run_path: Path, number: int, skipped: bool = False
) -> tuple[int, list[str]]:
    """Record one step's outcome — completed, or reached and deliberately skipped."""
    run = parse_run(run_path)
    disposition = SKIPPED if skipped else DONE
    run.record_step(number, stamp(utc_now()), disposition)
    run.write()
    return 0, [
        f"debrief-ledger: STEP {number} recorded as {disposition.upper()} — {run_path}",
        f"  recorded steps: {format_steps(run.steps)}",
    ]


def cmd_complete(run_path: Path) -> tuple[int, list[str]]:
    """Write the completion record. Without this the next run cannot tell finished from stopped."""
    run = parse_run(run_path)
    run.record_completion(stamp(utc_now()))
    run.write()
    return 0, [
        f"debrief-ledger: COMPLETE — {run_path}",
        f"  recorded steps: {format_steps(run.steps)}",
    ]


def _build_parser() -> argparse.ArgumentParser:
    """Construct the CLI parser."""
    parser = argparse.ArgumentParser(
        prog="ledger.py",
        description="Record and read back which /debrief steps a run finished.",
    )
    parser.add_argument(
        "--memory-dir",
        type=Path,
        default=None,
        help="the project memory directory (the one holding BACKLOG.md)",
    )
    parser.add_argument(
        "--run", type=Path, default=None, help="path to one run's ledger file"
    )
    parser.add_argument(
        "--session-id",
        default=None,
        help=f"session to scope on (default: ${SESSION_ENV})",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status", help="classify this session's newest ledger")
    starter = sub.add_parser("start", help="begin a new run's ledger")
    starter.add_argument(
        "--supersede",
        action="store_true",
        help="archive an incomplete ledger first (the operator chose a fresh start)",
    )
    sub.add_parser("resume", help="pick this session's incomplete ledger back up")
    stepper = sub.add_parser("step", help="record one step's outcome")
    stepper.add_argument(
        "number", type=int, help=f"step number, {FIRST_STEP}-{LAST_STEP}"
    )
    stepper.add_argument(
        "--skipped",
        action="store_true",
        help=(
            "the routine REACHED this step and deliberately did not run it (step 5 in every "
            "plain run). It counts as recorded, so a resume never goes back to it, and it is "
            "never reported as completed"
        ),
    )
    sub.add_parser("complete", help="record that the run finished")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the CLI.

    Returns:
        0 PROCEED or a landed write, 1 refused, 3 RESUMABLE, 4 MALFORMED.
    """
    parser = _build_parser()
    args = parser.parse_args(argv)
    needs_dir = args.command in ("status", "start", "resume")
    if needs_dir and args.memory_dir is None:
        parser.error(f"--memory-dir is required for `{args.command}`")
    if not needs_dir and args.run is None:
        parser.error(f"--run is required for `{args.command}`")
    session_id = resolve_session_id(args.session_id)
    try:
        if args.command == "status":
            code, lines = cmd_status(args.memory_dir, session_id)
        elif args.command == "start":
            code, lines = cmd_start(args.memory_dir, session_id, args.supersede)
        elif args.command == "resume":
            code, lines = cmd_resume(args.memory_dir, session_id)
        elif args.command == "step":
            code, lines = cmd_step(args.run, args.number, args.skipped)
        else:
            code, lines = cmd_complete(args.run)
    except LedgerError as exc:
        print(f"ledger: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"ledger: {exc}", file=sys.stderr)
        return 1
    print("\n".join(lines))
    return code


if __name__ == "__main__":
    sys.exit(main())

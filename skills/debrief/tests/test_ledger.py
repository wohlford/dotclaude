"""Properties of the /debrief step ledger.

The ledger exists so an interrupted debrief resumes from a record instead of from conversation
memory. Two of its properties are worth more than the rest, and both are asserted negatively:

* a COMPLETE ledger must produce NO question. `/debrief`'s central contract is that a plain run
  never pauses, and the obvious wrong design — ask whenever a ledger exists — repeals it silently.
  `test_complete_ledger_asks_nothing` is the row that fails if the prompt ever fires
  unconditionally; the mutation campaign at `scripts/tests/mutate_debrief_ledger.py` is what
  proves it actually would.
* a MALFORMED ledger must not crash the routine it was invoked to protect. Every shape failure
  is a refusal with a verdict, never an exception escaping to the caller.
* a resume must never go BACKWARDS into a step the previous run passed. Step 5 dispatches
  `/feature --plan-only` and runs *only* when the user asks for an automation design at
  invocation, so a resume that lands on it starts a design session nobody requested — an
  authorization failure. Two rows guard it from the two sides it can fail from: the RECORD
  (`test_a_skipped_step_advances_the_resume_point_past_it`) and the RULE
  (`test_a_gap_below_a_recorded_step_is_never_the_resume_point`, for a ledger whose skip was
  never written down). Recorded and completed are then kept apart deliberately, since a resumed
  run that read a skipped step 5 as finished would conclude an automation had been designed.

The report rows assert that the report MOVES with its input. A report that cannot change is not
reading the ledger, and would pass a "does it print something" test forever.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from ledger import (
    LAST_STEP,
    RERUN_COST,
    SESSION_ENV,
    UNKNOWN_SESSION,
    LedgerError,
    Run,
    main,
    name_stamp,
    parse_run,
    resolve_session_id,
    runs_dir,
    session_runs,
)

LEDGER_PY = Path(__file__).resolve().parent.parent / "ledger.py"
SESSION = "sess-aaaa"
OTHER_SESSION = "sess-bbbb"

# Exit codes, named so a row reads as the verdict it asserts rather than as a number.
PROCEED = 0
REFUSED = 1
RESUMABLE = 3
MALFORMED = 4


# ---------- fixtures and builders ----------


def moment(day=24, hour=18, minute=3, second=11):
    return datetime(2026, 8, day, hour, minute, second, tzinfo=timezone.utc)


def write_ledger(
    memory_dir,
    *,
    session=SESSION,
    steps=(),
    skipped=(),
    completed=None,
    at=None,
    extra=None,
):
    """Put one ledger file on disk and return its path.

    `steps` are recorded `done`; `skipped` are recorded `skipped`. The `done` entries carry NO
    `disposition` key on purpose — that is the on-disk shape a ledger written before the field
    existed has, so every row using this builder also holds the rule that an absent disposition
    reads as `done`.
    """
    at = at or moment()
    directory = runs_dir(memory_dir)
    directory.mkdir(parents=True, exist_ok=True)
    recorded = [{"step": n, "at": f"2026-08-24T18:0{n}:00Z"} for n in steps]
    recorded += [
        {"step": n, "at": f"2026-08-24T18:0{n}:00Z", "disposition": "skipped"}
        for n in skipped
    ]
    data = {
        "schema": 1,
        "session_id": session,
        "run_started": "2026-08-24T18:03:11Z",
        "steps": sorted(recorded, key=lambda item: item["step"]),
        "completed_at": completed,
    }
    if extra:
        data.update(extra)
    path = directory / f"run-{session}-{name_stamp(at)}.json"
    path.write_text(json.dumps(data, indent=2) + "\n")
    return path


def write_raw(memory_dir, text, *, session=SESSION, at=None):
    """Put arbitrary bytes where a ledger file belongs."""
    at = at or moment()
    directory = runs_dir(memory_dir)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"run-{session}-{name_stamp(at)}.json"
    path.write_text(text)
    return path


def run_cli(capsys, *argv, session=SESSION):
    """Invoke the CLI in-process and return (exit code, stdout).

    `--session-id` leads, because it is a top-level option: argparse hands everything after the
    subcommand to that subparser, which does not know the flag.
    """
    code = main(["--session-id", session, *argv])
    return code, capsys.readouterr().out


@pytest.fixture
def memory_dir(tmp_path):
    directory = tmp_path / "memory"
    directory.mkdir()
    return directory


# ---------- the four states ----------


def test_absent_ledger_proceeds(capsys, memory_dir):
    code, out = run_cli(capsys, "--memory-dir", str(memory_dir), "status")
    assert code == PROCEED
    assert "PROCEED" in out
    # "found nothing" must name where it looked.
    assert str(runs_dir(memory_dir)) in out
    assert SESSION in out


def test_incomplete_ledger_is_resumable(capsys, memory_dir):
    write_ledger(memory_dir, steps=(0, 1, 2))
    code, out = run_cli(capsys, "--memory-dir", str(memory_dir), "status")
    assert code == RESUMABLE
    assert "RESUMABLE" in out
    assert "recorded steps: 0, 1, 2" in out
    assert "resume at step: 3" in out


def test_complete_ledger_proceeds(capsys, memory_dir):
    write_ledger(memory_dir, steps=(0, 1, 2), completed="2026-08-24T19:00:00Z")
    code, out = run_cli(capsys, "--memory-dir", str(memory_dir), "status")
    assert code == PROCEED
    assert "PROCEED" in out


@pytest.mark.parametrize(
    "text",
    [
        "",
        "not json at all",
        "[1, 2, 3]",
        '{"session_id": "sess-aaaa"}',
        '{"session_id": 7, "run_started": "x", "steps": []}',
        '{"session_id": "s", "run_started": "x", "steps": "nope"}',
        # The four `steps` shapes the list check UNIQUELY decides. Measured with that check
        # deleted: `5` and `null` escape as TypeError and crash the routine, while `{}` and `""`
        # are ACCEPTED as a legitimate empty-step ledger. `"nope"` above is decided by the
        # per-entry check one line later, so on its own it grades a rule downstream of this one.
        '{"session_id": "s", "run_started": "x", "steps": 5}',
        '{"session_id": "s", "run_started": "x", "steps": null}',
        '{"session_id": "s", "run_started": "x", "steps": {}}',
        '{"session_id": "s", "run_started": "x", "steps": ""}',
        '{"session_id": "s", "run_started": "x", "steps": [{"at": "x"}]}',
        '{"session_id": "s", "run_started": "x", "steps": [], "completed_at": 5}',
    ],
)
def test_malformed_ledger_reports_and_does_not_crash(capsys, memory_dir, text):
    path = write_raw(memory_dir, text)
    code, out = run_cli(capsys, "--memory-dir", str(memory_dir), "status")
    assert code == MALFORMED
    assert "MALFORMED" in out
    assert path.name in out
    assert "reason:" in out
    # The whole point: a fresh start is offered, and nothing was mutated.
    assert "start --supersede" in out
    assert path.read_text() == text


def test_malformed_parse_raises_only_ledger_error(memory_dir):
    """Every shape failure funnels through one exception type, so no caller has to guess."""
    path = write_raw(memory_dir, '{"steps": {}}')
    with pytest.raises(LedgerError):
        parse_run(path)


# ---------- the no-prompt path ----------


def test_complete_ledger_asks_nothing(capsys, memory_dir):
    """A COMPLETE ledger must produce no question.

    This is the row that would fail if the prompt fired unconditionally — the specific defect the
    three-valued classification corrects. It asserts the absence of the question, not merely the
    presence of a verdict, because "prints a verdict" is true of the broken design too.
    """
    write_ledger(
        memory_dir, steps=tuple(range(LAST_STEP + 1)), completed="2026-08-24T19:00:00Z"
    )
    code, out = run_cli(capsys, "--memory-dir", str(memory_dir), "status")
    assert code == PROCEED
    # No question, in either shape: no interrogative, and none of the phrases that only the
    # RESUMABLE and MALFORMED reports carry.
    assert "?" not in out
    assert "RESUMABLE" not in out
    assert "MALFORMED" not in out
    assert "no completion record" not in out
    assert "resume at step" not in out
    assert "Offer the operator a fresh start" not in out
    assert "without pausing" in out


def test_completion_record_is_what_separates_finished_from_interrupted(
    capsys, memory_dir, tmp_path
):
    """Two ledgers with the SAME steps differ only by the completion record — and must differ."""
    every_step = tuple(range(LAST_STEP + 1))
    interrupted = tmp_path / "a"
    finished = tmp_path / "b"
    for directory in (interrupted, finished):
        directory.mkdir()
    write_ledger(interrupted, steps=every_step, completed=None)
    write_ledger(finished, steps=every_step, completed="2026-08-24T19:00:00Z")

    stopped_code, _ = run_cli(capsys, "--memory-dir", str(interrupted), "status")
    done_code, _ = run_cli(capsys, "--memory-dir", str(finished), "status")
    assert (stopped_code, done_code) == (RESUMABLE, PROCEED)


# ---------- the report moves ----------


def test_report_moves_with_the_steps_recorded(capsys, tmp_path):
    """A ledger naming different completed steps yields a different report."""
    reports = {}
    for label, steps in (("early", (0,)), ("late", (0, 1, 2, 3))):
        directory = tmp_path / label
        directory.mkdir()
        write_ledger(directory, steps=steps)
        _, reports[label] = run_cli(capsys, "--memory-dir", str(directory), "status")
    assert reports["early"] != reports["late"]
    assert "resume at step: 1" in reports["early"]
    assert "resume at step: 4" in reports["late"]
    # The re-run notes are quoted only for the steps actually recorded.
    assert RERUN_COST[3] not in reports["early"]
    assert RERUN_COST[3] in reports["late"]


def test_report_carries_the_backlog_cross_check_for_steps_four_and_five(
    capsys, memory_dir
):
    """A ledger recording step 4 must tell the resuming run to consult BACKLOG.md first."""
    write_ledger(memory_dir, steps=(0, 1, 2, 3, 4))
    _, out = run_cli(capsys, "--memory-dir", str(memory_dir), "status")
    assert "BACKLOG.md" in out
    assert "DUPLICATE" in out


def test_no_step_recorded_says_so_rather_than_printing_a_blank(capsys, memory_dir):
    write_ledger(memory_dir, steps=())
    _, out = run_cli(capsys, "--memory-dir", str(memory_dir), "status")
    assert "recorded steps: (none)" in out
    assert "resuming and restarting are the same work" in out


def test_rerun_cost_covers_every_step(capsys, memory_dir):
    """A declared floor: discovery cannot detect the absence of a step's cost note."""
    assert set(RERUN_COST) == set(range(LAST_STEP + 1))
    write_ledger(memory_dir, steps=tuple(range(LAST_STEP + 1)))
    _, out = run_cli(capsys, "--memory-dir", str(memory_dir), "status")
    for number in range(LAST_STEP + 1):
        assert f"step {number} — " in out


def test_no_blanket_safety_claim_anywhere_in_the_report(capsys, memory_dir):
    """`re-running is safe` is FALSE for steps 4 and 5, so the report must never say it."""
    write_ledger(memory_dir, steps=tuple(range(LAST_STEP + 1)))
    _, out = run_cli(capsys, "--memory-dir", str(memory_dir), "status")
    lowered = out.lower()
    assert "re-running is safe" not in lowered
    assert "safe to re-run" not in lowered
    for number in (4, 5):
        assert "MEASURED HARMFUL" in RERUN_COST[number]


# ---------- run identity and concurrency ----------


def test_two_sessions_do_not_clobber_one_file(capsys, memory_dir):
    """Two live runs of the same project keep separate ledgers, and each reads back its own."""
    mine, _ = run_cli(capsys, "--memory-dir", str(memory_dir), "start", session=SESSION)
    theirs, _ = run_cli(
        capsys, "--memory-dir", str(memory_dir), "start", session=OTHER_SESSION
    )
    assert (mine, theirs) == (PROCEED, PROCEED)
    assert len(session_runs(memory_dir, SESSION)) == 1
    assert len(session_runs(memory_dir, OTHER_SESSION)) == 1
    assert session_runs(memory_dir, SESSION) != session_runs(memory_dir, OTHER_SESSION)

    # One session completing its run must not make the other session's run read as complete.
    mine_path = session_runs(memory_dir, SESSION)[0]
    run_cli(capsys, "--run", str(mine_path), "complete")
    code, _ = run_cli(
        capsys, "--memory-dir", str(memory_dir), "status", session=OTHER_SESSION
    )
    assert code == RESUMABLE
    code, _ = run_cli(
        capsys, "--memory-dir", str(memory_dir), "status", session=SESSION
    )
    assert code == PROCEED


def test_start_in_the_same_second_never_overwrites(capsys, memory_dir, monkeypatch):
    """Two runs whose start stamps collide get two files, not one."""
    monkeypatch.setattr("ledger.utc_now", lambda: moment())
    first = write_ledger(memory_dir, steps=(0, 1), completed="2026-08-24T19:00:00Z")
    code, out = run_cli(capsys, "--memory-dir", str(memory_dir), "start")
    assert code == PROCEED
    assert first.exists()
    assert first.read_text() != ""
    files = session_runs(memory_dir, SESSION)
    assert len(files) == 2
    assert out.splitlines()[0].endswith(str(files[-1]))


def test_only_this_session_s_files_are_read(memory_dir):
    """A stray file in the ledger directory is never mistaken for a ledger."""
    write_ledger(memory_dir, session=OTHER_SESSION)
    (runs_dir(memory_dir) / "notes.txt").write_text("unrelated")
    (runs_dir(memory_dir) / "run-nonsense.json").write_text("{}")
    assert session_runs(memory_dir, SESSION) == []
    assert len(session_runs(memory_dir, OTHER_SESSION)) == 1


def test_session_id_resolution(monkeypatch):
    monkeypatch.setenv(SESSION_ENV, "from-env")
    assert resolve_session_id() == "from-env"
    assert resolve_session_id("explicit") == "explicit"
    monkeypatch.setenv(SESSION_ENV, "   ")
    assert resolve_session_id() == UNKNOWN_SESSION
    monkeypatch.delenv(SESSION_ENV)
    assert resolve_session_id() == UNKNOWN_SESSION


# ---------- lifecycle ----------


def test_start_refuses_over_an_incomplete_ledger(capsys, memory_dir):
    """The operator's choice cannot be skipped: `start` alone will not bury a live run."""
    stale = write_ledger(memory_dir, steps=(0, 1))
    code, out = run_cli(capsys, "--memory-dir", str(memory_dir), "start")
    assert code == REFUSED
    assert "REFUSED" in out
    assert stale.exists()
    assert len(session_runs(memory_dir, SESSION)) == 1


def test_supersede_archives_rather_than_deletes(capsys, memory_dir):
    stale = write_ledger(memory_dir, steps=(0, 1))
    before = stale.read_text()
    code, out = run_cli(capsys, "--memory-dir", str(memory_dir), "start", "--supersede")
    assert code == PROCEED
    assert "superseded:" in out
    assert not stale.exists()
    archived = runs_dir(memory_dir) / "superseded" / stale.name
    assert archived.read_text() == before
    # Exactly one live ledger remains, so a later read cannot mix runs.
    assert len(session_runs(memory_dir, SESSION)) == 1
    code, _ = run_cli(capsys, "--memory-dir", str(memory_dir), "status")
    assert code == RESUMABLE


def test_supersede_archives_a_malformed_ledger_too(capsys, memory_dir):
    broken = write_raw(memory_dir, "not json")
    code, _ = run_cli(capsys, "--memory-dir", str(memory_dir), "start", "--supersede")
    assert code == PROCEED
    assert not broken.exists()
    assert (runs_dir(memory_dir) / "superseded" / broken.name).exists()


def test_step_and_complete_round_trip(capsys, memory_dir):
    run_cli(capsys, "--memory-dir", str(memory_dir), "start")
    path = session_runs(memory_dir, SESSION)[0]
    for number in (0, 1, 2):
        code, _ = run_cli(capsys, "--run", str(path), "step", str(number))
        assert code == PROCEED
    assert parse_run(path).step_numbers == [0, 1, 2]
    assert not parse_run(path).is_complete
    code, _ = run_cli(capsys, "--run", str(path), "complete")
    assert code == PROCEED
    assert parse_run(path).is_complete


def test_recording_a_step_twice_keeps_one_entry(capsys, memory_dir):
    path = write_ledger(memory_dir, steps=(0,))
    run_cli(capsys, "--run", str(path), "step", "0")
    assert parse_run(path).step_numbers == [0]


@pytest.mark.parametrize("number", [-1, 8, 99])
def test_step_outside_the_routine_is_refused(capsys, memory_dir, number):
    path = write_ledger(memory_dir, steps=())
    code = main(["--session-id", SESSION, "--run", str(path), "step", str(number)])
    capsys.readouterr()
    assert code == REFUSED
    assert parse_run(path).step_numbers == []


def test_resume_notes_itself_in_the_artifact(capsys, memory_dir):
    write_ledger(memory_dir, steps=(0, 1))
    code, out = run_cli(capsys, "--memory-dir", str(memory_dir), "resume")
    assert code == PROCEED
    assert "continue at step 2" in out
    path = session_runs(memory_dir, SESSION)[0]
    assert len(parse_run(path).resumed_at) == 1
    _, status = run_cli(capsys, "--memory-dir", str(memory_dir), "status")
    assert "already resumed at:" in status


def test_resume_refuses_a_completed_run(capsys, memory_dir):
    write_ledger(memory_dir, steps=(0,), completed="2026-08-24T19:00:00Z")
    code, out = run_cli(capsys, "--memory-dir", str(memory_dir), "resume")
    assert code == REFUSED
    assert "nothing to " in out


def test_resume_refuses_when_there_is_no_ledger(capsys, memory_dir):
    code, out = run_cli(capsys, "--memory-dir", str(memory_dir), "resume")
    assert code == REFUSED
    assert "no ledger" in out


def test_next_step_after_a_full_run(memory_dir):
    path = write_ledger(memory_dir, steps=tuple(range(LAST_STEP + 1)))
    assert parse_run(path).next_step() == LAST_STEP


# ---------- a gap is a step that was PASSED, never a step to go back to ----------


def test_a_gap_below_a_recorded_step_is_never_the_resume_point(memory_dir):
    """A run that recorded 0-4 and 6 must resume at 6 or 7 — NEVER at the gap, 5.

    This is an authorization property, not a convenience. Step 5 dispatches
    `/feature --plan-only`, and `SKILL.md` runs it *only* when the user asked for an automation
    design at invocation. Steps are recorded in order, so a gap below a recorded step is a step
    the run PASSED; routing a resume back into it starts a design session nobody requested.

    The rule is general — no step number appears in it — and it lives in `next_step()`, because
    `SKILL.md` forbids the operator re-deriving the resume point at the call site.
    """
    path = write_ledger(memory_dir, steps=(0, 1, 2, 3, 4, 6))
    assert parse_run(path).next_step() in (6, LAST_STEP)


# ---------- a SKIPPED step: recorded, but never completed ----------


def test_a_skipped_step_advances_the_resume_point_past_it(memory_dir):
    """The primary mechanism: the routine records that it skipped step 5, so a resume clears it.

    Deliberately built with NO step above the skip, so the gap-backstop above cannot decide this
    row. With `{0..4}` done and `5` skipped the resume point is 6 only if the skip itself counts
    as recorded — the exact property `skip-not-recorded` mutates.
    """
    path = write_ledger(memory_dir, steps=(0, 1, 2, 3, 4), skipped=(5,))
    run = parse_run(path)
    assert run.next_step() == 6
    assert run.step_numbers == [0, 1, 2, 3, 4, 5]
    assert run.done_steps == [0, 1, 2, 3, 4]
    assert run.skipped_steps == [5]


def test_a_skipped_step_is_never_reported_as_completed(capsys, memory_dir):
    """Recorded is not completed, and the report must never let the two read alike.

    A resumed run that took step 5 for finished work would conclude an automation had been
    designed when `/feature --plan-only` never ran. So the skip is marked in the step list, named
    on its own line, and — the load-bearing half — kept out of the re-run costs, which describe
    work that actually happened. Step 4's note is the CONTROL: it must still be there, or this
    row would pass for a report that had simply stopped printing costs.
    """
    write_ledger(memory_dir, steps=(0, 1, 2, 3, 4), skipped=(5,))
    code, out = run_cli(capsys, "--memory-dir", str(memory_dir), "status")
    assert code == RESUMABLE
    assert "recorded steps: 0, 1, 2, 3, 4, 5 (skipped)" in out
    assert "SKIPPED, never run" in out
    assert "resume at step: 6" in out
    # The re-run costs are for COMPLETED steps only.
    assert RERUN_COST[5] not in out
    assert "step 5 — " not in out
    assert RERUN_COST[4] in out


def test_the_plain_run_shape_never_routes_a_resume_into_step_five(capsys, memory_dir):
    """End to end, through the documented commands, in the shape a plain `/debrief` produces.

    A plain run passes step 5 by. Interrupted after step 6, its ledger must resume at 7 — never
    at 5, which would dispatch a `/feature --plan-only` design session the user never asked for.
    """
    run_cli(capsys, "--memory-dir", str(memory_dir), "start")
    path = session_runs(memory_dir, SESSION)[0]
    for number in (0, 1, 2, 3, 4):
        run_cli(capsys, "--run", str(path), "step", str(number))
    code, out = run_cli(capsys, "--run", str(path), "step", "5", "--skipped")
    assert code == PROCEED
    assert "recorded as SKIPPED" in out
    run_cli(capsys, "--run", str(path), "step", "6")

    code, out = run_cli(capsys, "--memory-dir", str(memory_dir), "status")
    assert code == RESUMABLE
    assert "resume at step: 7" in out
    assert "resume at step: 5" not in out


def test_the_skipped_flag_is_actually_read(capsys, memory_dir):
    """Pass a value that DIFFERS from the default, and assert that distinctive value landed.

    `--skipped` defaults to off, so a row recording a step WITHOUT it cannot tell whether the
    flag is plumbed through at all. This one records the same step both ways and requires the
    stored disposition to differ.
    """
    path = write_ledger(memory_dir, steps=())
    run_cli(capsys, "--run", str(path), "step", "5")
    assert json.loads(path.read_text())["steps"][0]["disposition"] == "done"
    run_cli(capsys, "--run", str(path), "step", "5", "--skipped")
    stored = json.loads(path.read_text())["steps"]
    assert len(stored) == 1
    assert stored[0]["disposition"] == "skipped"


def test_a_step_with_no_disposition_reads_as_done(memory_dir):
    """The one legal omission: the key's absence predates the field and means `done`.

    Without this, a ledger already on disk would be re-read as having SKIPPED every step it in
    fact finished — the failure in the opposite direction.
    """
    path = write_ledger(memory_dir, steps=(0, 1))
    run = parse_run(path)
    assert run.done_steps == [0, 1]
    assert run.skipped_steps == []


def test_an_unknown_disposition_is_malformed_not_quietly_done(capsys, memory_dir):
    """Cleared by allowlist: a value nobody anticipated is reported, never folded into `done`."""
    write_raw(
        memory_dir,
        json.dumps(
            {
                "session_id": SESSION,
                "run_started": "2026-08-24T18:03:11Z",
                "steps": [{"step": 5, "at": "x", "disposition": "maybe"}],
            }
        ),
    )
    code, out = run_cli(capsys, "--memory-dir", str(memory_dir), "status")
    assert code == MALFORMED
    assert "maybe" in out


def test_record_step_refuses_an_unknown_disposition(memory_dir):
    run = parse_run(write_ledger(memory_dir, steps=()))
    with pytest.raises(LedgerError):
        run.record_step(5, "2026-08-24T18:00:00Z", "sort-of")
    assert run.step_numbers == []


def test_write_is_atomic_and_leaves_no_temp_file(memory_dir):
    path = write_ledger(memory_dir, steps=())
    run = parse_run(path)
    run.record_step(0, "2026-08-24T18:00:00Z")
    run.write()
    leftovers = [p.name for p in runs_dir(memory_dir).iterdir() if p.suffix == ".tmp"]
    assert leftovers == []


def test_run_write_creates_its_directory(tmp_path):
    target = (
        tmp_path / "memory" / "debrief-runs" / "run-sess-aaaa-20260824T180311Z.json"
    )
    Run(
        target,
        {
            "schema": 1,
            "session_id": SESSION,
            "run_started": "2026-08-24T18:03:11Z",
            "steps": [],
            "completed_at": None,
        },
    ).write()
    assert target.exists()


# ---------- the documented invocation, run as written ----------


def test_the_documented_command_runs_as_written(memory_dir):
    """A command written into SKILL.md is unverified until it has been run."""
    proc = subprocess.run(
        [
            sys.executable,
            str(LEDGER_PY),
            "--memory-dir",
            str(memory_dir),
            "--session-id",
            SESSION,
            "status",
        ],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == PROCEED, proc.stderr
    assert "PROCEED" in proc.stdout


def test_missing_required_flag_is_a_usage_error(memory_dir):
    with pytest.raises(SystemExit) as excinfo:
        main(["status"])
    assert excinfo.value.code == 2
    with pytest.raises(SystemExit) as excinfo:
        main(["--memory-dir", str(memory_dir), "step", "0"])
    assert excinfo.value.code == 2

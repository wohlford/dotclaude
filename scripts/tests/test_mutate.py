"""Tests for scripts/lib/mutate.py — the shared mutation-campaign runner.

Ten hand-written harnesses preceded this module, and measuring them is what set the test list
below. No two agreed on the safety properties, and no single copy had all of them: 7 of 8 never
ran an unmutated baseline, 5 of 8 emitted no `RESULT:` verdict line, 7 of 8 never cleared
`__pycache__`, and the predicate deciding whether a mutation was CAUGHT existed in three
mutually incompatible versions. Every property asserted here is one that drifted in the wild, so
each row names a measured defect rather than a hypothetical one.

The load-bearing one is the baseline. A campaign whose suite is ALREADY red reports every
mutation as CAUGHT and prints a flawless sweep — the failure mode is a clean-looking report, so
nothing downstream ever questions it. Running the baseline through the SAME predicate that judges
the mutants closes a second hole in the same move: if the FAIL pattern spuriously matches this
suite's ordinary green output, the baseline sees it too and the campaign ERRORs, instead of
reporting a perfect score built on a predicate that is always true.
"""

from __future__ import annotations

import os
import signal
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import mutate  # noqa: E402, I001


# ---------- fixtures: a subject file and a suite that judges it ----------

SUBJECT_SRC = """#!/usr/bin/env python3
GUARD = True
LIMIT = 10
"""

# Exits 1 and prints a repo-style `FAIL  <label>` line when the guard is gone.
SUITE_SRC = """#!/usr/bin/env bash
if grep -q 'GUARD = True' "$1"; then
  printf 'PASS  guard intact\\n'
  exit 0
fi
printf 'FAIL  guard missing\\n'
exit 1
"""

# Prints the failure line but exits 0 — the fail-open shape measured in this repo's own
# test-runner hooks, where the rc alone would call a real failure a pass.
SUITE_FAIL_OPEN_SRC = """#!/usr/bin/env bash
if grep -q 'GUARD = True' "$1"; then
  printf 'PASS  guard intact\\n'
  exit 0
fi
printf 'FAIL  guard missing\\n'
exit 0
"""

# Never notices anything: always green, whatever the subject says.
SUITE_BLIND_SRC = """#!/usr/bin/env bash
printf 'PASS  nothing is actually checked\\n'
exit 0
"""

# Red before a single mutation is applied.
SUITE_ALREADY_RED_SRC = """#!/usr/bin/env bash
printf 'FAIL  broken for an unrelated reason\\n'
exit 1
"""


@pytest.fixture
def bed(tmp_path):
    """A subject + a suite, on a PHYSICAL path.

    `tmp_path` is resolved because macOS hands out `/var/...`, a symlink to `/private/var/...`.
    A subject reached through the logical path is a different string to any tool that resolves
    its own location, and this repo has already measured a fixture that never reached its
    subject for exactly that reason.
    """
    root = tmp_path.resolve()
    subject = root / "subject.py"
    subject.write_text(SUBJECT_SRC)

    def suite(src=SUITE_SRC, name="suite.sh"):
        path = root / name
        path.write_text(src)
        path.chmod(0o755)
        return ["bash", str(path), str(subject)]

    return root, subject, suite


DROP_GUARD = mutate.Mutation("drop the guard", "GUARD = True", "GUARD = False")
RAISE_LIMIT = mutate.Mutation("raise the limit", "LIMIT = 10", "LIMIT = 99")


# ---------- P0: progress is observable DURING the run, not only after it ----------

# A campaign runs for tens of minutes. Buffering every line until the end makes the artifact
# byte-identical to a stall for the whole run, and that is not ergonomics: it produced a wrong
# conclusion twice. A 14-row campaign was declared "cannot complete", filed as a HIGH, and
# diagnosed three ways (a sidecar file, a specific mutation, a backgrounding bug) before the
# truth — it simply takes 30+ minutes — was recalled rather than read. Cost: a false HIGH and
# about an hour. So the property is not "the text contains progress lines" (buffering satisfies
# that); it is that a line is READABLE while the process is still running.

SLOW_SUITE_SRC = """#!/usr/bin/env bash
sleep 1
if grep -q 'GUARD = True' "$1"; then
  printf 'PASS  guard intact\\n'
  exit 0
fi
printf 'FAIL  guard missing\\n'
exit 1
"""

DRIVER_SRC = """import sys
sys.path.insert(0, {lib!r})
import mutate
report = mutate.run(
    {subject!r},
    ["bash", {suite!r}, {subject!r}],
    [
        mutate.Mutation("drop the guard", "GUARD = True", "GUARD = False"),
        mutate.Mutation("raise the limit", "LIMIT = 10", "LIMIT = 99"),
    ],
    timeout=30,
)
print(report.text)
"""


def test_a_mutation_line_is_readable_before_the_campaign_exits(bed):
    root, subject, suite = bed
    cmd = suite(SLOW_SUITE_SRC, "slow.sh")
    driver = root / "driver.py"
    driver.write_text(
        DRIVER_SRC.format(
            lib=str(Path(mutate.__file__).parent),
            subject=str(subject),
            suite=cmd[1],
        )
    )
    artifact = root / "run.log"

    with open(artifact, "w") as sink:
        proc = subprocess.Popen(
            [sys.executable, str(driver)], stdout=sink, stderr=subprocess.STDOUT
        )
        seen_while_alive = ""
        deadline = time.monotonic() + 60
        while proc.poll() is None and time.monotonic() < deadline:
            body = artifact.read_text()
            if any(
                word in body for word in ("BASELINE", "CAUGHT", "SURVIVED", "TIMEOUT")
            ):
                seen_while_alive = body
                break
            time.sleep(0.05)
        proc.wait(timeout=60)

    assert proc.returncode == 0, artifact.read_text()
    assert seen_while_alive, (
        "the artifact carried no progress line at any point while the campaign was alive — "
        "empty is byte-identical to a stall, which is the measured defect:\n"
        + artifact.read_text()
    )


def test_the_verdict_is_still_the_last_line_despite_streaming(bed):
    """Streaming must not displace the verdict — a consumer reads the LAST line."""
    root, subject, suite = bed
    report = mutate.run(subject, suite(), [DROP_GUARD], timeout=30)
    assert report.text.strip().splitlines()[-1] == report.verdict
    assert report.verdict.startswith("RESULT: ")


def test_progress_can_be_silenced_for_a_programmatic_caller(bed, capfd):
    """`progress=None` restores the silent behaviour a library caller may depend on."""
    root, subject, suite = bed
    mutate.run(subject, suite(), [DROP_GUARD], timeout=30, progress=None)
    assert capfd.readouterr().out == ""


# ---------- the caught predicate: one definition, four cases ----------


@pytest.mark.parametrize(
    "rc, stdout, want, why",
    [
        (
            1,
            "FAIL  guard missing\n",
            True,
            "the ordinary case: red rc and a failure line",
        ),
        (
            1,
            "",
            True,
            "a CRASH exits non-zero with no output and must not read as survived",
        ),
        (
            0,
            "FAIL  guard missing\n",
            True,
            "a FAIL-OPEN suite prints the failure and exits 0",
        ),
        (0, "PASS  guard intact\n", False, "genuinely green"),
        (0, "FAILED subject.py::test_x\n", True, "pytest spells it FAILED"),
    ],
)
def test_caught_predicate(rc, stdout, want, why):
    assert mutate.is_caught(rc, stdout) is want, why


# ---------- P1: the baseline is mandatory and is judged by the same predicate ----------


def test_already_red_suite_errors_instead_of_reporting_a_clean_sweep(bed):
    """The headline defect: 7 of 8 prior harnesses would report 1/1 CAUGHT here."""
    _, subject, suite = bed
    report = mutate.run(subject, suite(SUITE_ALREADY_RED_SRC), [DROP_GUARD])
    assert report.status == "ERROR"
    assert report.rc == 2
    assert report.caught == 0, (
        "nothing may be credited as caught when the baseline never passed"
    )
    assert "baseline" in report.verdict.lower() or "baseline" in report.text.lower()


def test_baseline_also_catches_an_always_true_fail_pattern(bed):
    """If the pattern matches ordinary green output, the sweep would score a perfect false clean.

    The baseline runs through the SAME predicate, so an always-true pattern makes the baseline
    itself read as failing and the campaign ERRORs rather than reporting PASS.
    """
    _, subject, suite = bed
    report = mutate.run(subject, suite(), [DROP_GUARD], fail_pattern=r"^")
    assert report.status == "ERROR"
    assert report.caught == 0


def test_an_empty_campaign_errors_rather_than_passing(bed):
    """Zero is the limiting case of a discovery that found nothing, and it reports success loudest.

    `PASS caught=0 survived=0` is what a campaign of no mutations would otherwise print: a clean
    verdict on a denominator of zero. CLAUDE.md's rule is to assert a non-zero denominator.
    """
    _, subject, suite = bed
    report = mutate.run(subject, suite(), [])
    assert report.status == "ERROR"
    assert report.rc == 2
    assert report.total == 0


# ---------- P2: a mutation that did not apply is SURVIVED, never skipped ----------


@pytest.mark.parametrize(
    "old, occurrences",
    [("NOT PRESENT ANYWHERE", 0), ("= ", 2)],
)
def test_patch_target_not_found_exactly_once_counts_as_survived(bed, old, occurrences):
    _, subject, suite = bed
    m = mutate.Mutation("unapplied", old, "XXX")
    report = mutate.run(subject, suite(), [m])
    assert report.survived == 1, (
        "an unapplied mutation proves nothing and must not be a skip"
    )
    assert report.caught == 0
    assert report.status == "FAIL"
    assert report.rc == 1
    detail = report.outcomes[0].detail
    assert str(occurrences) in detail, (
        f"the detail should name the real count: {detail!r}"
    )


# ---------- P3/P4: the subject is restored, byte-for-byte and mode-for-mode ----------


def test_subject_is_restored_after_a_campaign(bed):
    _, subject, suite = bed
    mutate.run(subject, suite(), [DROP_GUARD, RAISE_LIMIT])
    assert subject.read_text() == SUBJECT_SRC


def test_subject_is_restored_even_when_the_command_raises(bed, monkeypatch):
    """The raise must land AFTER a mutant is on disk, or the restore passes for free.

    A bogus command would blow up on the BASELINE run, before anything was written — the
    subject would be pristine for the trivial reason that it was never touched, and the test
    would pass identically with no `finally` at all.
    """
    _, subject, suite = bed
    real_popen = mutate.subprocess.Popen
    calls = []

    def blow_up_after_baseline(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            return real_popen(*args, **kwargs)
        assert subject.read_text() != SUBJECT_SRC, "precondition: a mutant is on disk"
        raise RuntimeError("suite runner died mid-campaign")

    monkeypatch.setattr(mutate.subprocess, "Popen", blow_up_after_baseline)
    with pytest.raises(RuntimeError):
        mutate.run(subject, suite(), [DROP_GUARD])
    monkeypatch.undo()
    assert subject.read_text() == SUBJECT_SRC, (
        "restore must happen in a finally, not on the happy path"
    )
    assert not mutate.backup_path(subject).exists(), (
        "the backup must go with it — a stray one has to mean the subject is still mutated"
    )


def test_execute_bit_survives_the_campaign(bed):
    """A property that currently holds BY CONSTRUCTION, and is asserted anyway.

    Mutation-testing showed the runner's explicit `chmod` restore surviving every mutation:
    `write_text` truncates in place, so the mode is never lost and the call was a no-op. The
    no-op was deleted rather than given a fixture — but this row stays, because the property is
    real and externally visible. If the implementation ever moves to write-a-temp-and-rename,
    the mode WOULD be lost and this goes red.
    """
    _, subject, suite = bed
    subject.chmod(0o755)
    before = stat.S_IMODE(subject.stat().st_mode)
    mutate.run(subject, suite(), [DROP_GUARD])
    assert stat.S_IMODE(subject.stat().st_mode) == before


def test_a_failed_restore_is_reported_as_ERROR(bed, monkeypatch):
    """Losing the subject must never be reported as a passing campaign."""
    _, subject, suite = bed
    real = mutate.Path.write_text

    def clobber(self, data, *a, **kw):
        # Let mutations through; corrupt only the final restore.
        return real(self, data if data != SUBJECT_SRC else "CLOBBERED\n", *a, **kw)

    report = mutate.run(subject, suite(), [DROP_GUARD])
    assert report.status == "PASS", (
        "precondition: this campaign passes when restore works"
    )

    monkeypatch.setattr(mutate.Path, "write_text", clobber)
    report = mutate.run(subject, suite(), [DROP_GUARD])
    monkeypatch.undo()
    subject.write_text(SUBJECT_SRC)
    assert report.status == "ERROR"
    assert report.rc == 2
    assert "restore" in report.text.lower()


# ---------- P5: a real campaign discriminates ----------


def test_a_mutation_the_suite_notices_is_CAUGHT(bed):
    _, subject, suite = bed
    report = mutate.run(subject, suite(), [DROP_GUARD])
    assert report.status == "PASS"
    assert report.rc == 0
    assert (report.caught, report.survived) == (1, 0)


def test_a_mutation_the_suite_ignores_is_SURVIVED(bed):
    """RAISE_LIMIT applies cleanly; the suite only ever looks at the guard."""
    _, subject, suite = bed
    report = mutate.run(subject, suite(), [RAISE_LIMIT])
    assert (report.caught, report.survived) == (0, 1)
    assert report.status == "FAIL"


def test_a_blind_suite_survives_everything(bed):
    _, subject, suite = bed
    report = mutate.run(subject, suite(SUITE_BLIND_SRC), [DROP_GUARD, RAISE_LIMIT])
    assert report.survived == 2
    assert report.status == "FAIL"


def test_a_fail_open_suite_is_still_credited_with_catching(bed):
    """rc stays 0, so an rc-only predicate would call this a survivor and hide a real catch."""
    _, subject, suite = bed
    report = mutate.run(subject, suite(SUITE_FAIL_OPEN_SRC), [DROP_GUARD])
    assert report.caught == 1
    assert report.status == "PASS"


# ---------- P6: the verdict line ----------


def test_verdict_line_is_last_and_well_formed(bed):
    _, subject, suite = bed
    report = mutate.run(subject, suite(), [DROP_GUARD])
    lines = [ln for ln in report.text.split("\n") if ln.strip()]
    assert lines[-1] == report.verdict, (
        "the verdict must be the LAST line a reader sees"
    )
    assert report.verdict.startswith("RESULT: ")
    assert "caught=1" in report.verdict
    assert "survived=0" in report.verdict
    assert "total=1" in report.verdict


@pytest.mark.parametrize("status", ["PASS", "FAIL", "ERROR"])
def test_status_is_drawn_from_the_allowlist(status):
    assert status in mutate.STATUSES


def test_rc_and_status_never_disagree(bed):
    _, subject, suite = bed
    for mutations, want in ([DROP_GUARD], "PASS"), ([RAISE_LIMIT], "FAIL"):
        report = mutate.run(subject, suite(), mutations)
        assert report.status == want
        assert (
            report.verdict == f"RESULT: {report.status} rc={report.rc} "
            f"caught={report.caught} survived={report.survived} "
            f"timedout={report.timedout} total={report.total}"
        )
        assert (report.rc == 0) == (report.status == "PASS")


# ---------- P7: no default output path (CLAUDE.md group 5) ----------


def test_a_run_without_report_path_writes_nothing(bed):
    """A default destination makes every run a writer of real state — measured twice in this repo."""
    root, subject, suite = bed
    command = suite()  # built BEFORE the snapshot; suite.sh is not the campaign's doing
    before = {p for p in root.rglob("*")}
    mutate.run(subject, command, [DROP_GUARD])
    assert {p for p in root.rglob("*")} == before


def test_report_path_is_honoured_when_given(bed):
    root, subject, suite = bed
    dest = root / "out" / "report.txt"
    report = mutate.run(subject, suite(), [DROP_GUARD], report_path=dest)
    assert dest.read_text() == report.text + "\n"
    assert dest.read_text().strip().split("\n")[-1] == report.verdict


# ---------- P8: a stale .pyc must not answer for the mutant ----------


def test_no_stale_pycache_survives_into_a_suite_run(bed):
    """Only 1 of 8 prior harnesses cleared caches; a stale .pyc makes a mutation read as survived.

    Asserted on the MECHANISM rather than end-to-end on purpose. Whether a stale `.pyc` actually
    answers depends on the mutation leaving the source the same byte-length AND the rewrite
    landing inside the same mtime second — sub-second timing this test does not control. An
    end-to-end version would therefore pass or fail by luck, which is the flaky-fixture shape
    this repo has already been bitten by. So the suite here records what it SAW at the moment it
    ran, and the assertion is on that record — deterministic, and it still fails if the clearing
    is removed.
    """
    root, subject, _ = bed
    pkg = root / "pkg"
    pkg.mkdir()
    (pkg / "mod.py").write_text("VALUE = 1\n")
    log = root / "seen.log"

    # Always green: the verdict must not be what carries this signal, or a RED here would be
    # ambiguous between "cache was stale" and "the mutation was caught" (the wrong-reason trap).
    watcher = root / "watch.sh"
    watcher.write_text(
        "#!/usr/bin/env bash\n"
        f'if [ -d "{pkg}/__pycache__" ]; then echo stale >> "{log}"; '
        f'else echo clean >> "{log}"; fi\n'
        "printf 'PASS  observed\\n'\n"
        "exit 0\n"
    )
    watcher.chmod(0o755)
    command = ["bash", str(watcher)]

    # Warm a real cache so there is something to clear.
    subprocess.run(
        [sys.executable, "-c", f"import sys; sys.path.insert(0, r'{pkg}'); import mod"],
        capture_output=True,
        check=True,
    )
    assert list(pkg.rglob("__pycache__")), "precondition: a .pyc was actually written"

    mutate.run(
        pkg / "mod.py",
        command,
        [mutate.Mutation("change VALUE", "VALUE = 1", "VALUE = 2")],
    )
    seen = log.read_text().split()
    assert seen, "the suite ran at least once (baseline + one mutant)"
    assert set(seen) == {"clean"}, (
        f"a stale __pycache__ was visible to the suite: {seen}"
    )


# ---------- P9: a KILLED campaign must not strand a mutant on disk ----------

# Hangs once the mutant lands, which is what gives these tests a window to signal the driver
# while a mutation is actually on disk. Without the hang the campaign finishes in milliseconds
# and every signal arrives after the restore — the rows would pass for free, fix or no fix.
#
# 30s is bounded from BOTH sides and neither bound is slack. It must comfortably outlast the
# explicit `timeout=2`/`timeout=3` these rows pass, or the timeout never fires and they prove
# nothing. It must also stay short enough that the mutation *removing* the timeout leaves the
# suite inside its own derived cap — otherwise that row scores a self-inflicted TIMEOUT instead
# of CAUGHT, and the campaign ERRORs on the very feature it is meant to verify. Measured: at
# 300s it did exactly that.
SUITE_HANGS_ON_MUTANT_SRC = """#!/usr/bin/env bash
if grep -q 'GUARD = True' "$1"; then
  printf 'PASS  guard intact\\n'
  exit 0
fi
sleep 30
"""


@pytest.fixture
def killable(bed):
    """A campaign running in its OWN process, stoppable while a mutant is on disk.

    Out-of-process on purpose: the defect is that a signal tears the interpreter down before
    `finally` runs, and nothing raised inside this one can reproduce that. `pytest.raises` on a
    simulated exception is the shape that would pass while the real hole stayed open.
    """
    root, subject, suite = bed
    command = suite(SUITE_HANGS_ON_MUTANT_SRC, "hang.sh")
    lib = str(Path(mutate.__file__).resolve().parent)
    driver = root / "driver.py"
    # Pinning SIGINT is part of the FIXTURE, not the subject. A shell backgrounding a job hands
    # it SIGINT already set to SIG_IGN, so Python never installs its KeyboardInterrupt handler
    # and the signal is ignored outright — the driver would simply run on. Measured: the SIGINT
    # row passed 3/3 in a foreground TTY and failed 3/3 backgrounded, which is how campaigns are
    # actually run. Without this line the row grades the shell's job control, not mutate.py.
    driver.write_text(
        f"import signal, sys\nsys.path.insert(0, {lib!r})\nimport mutate\n"
        "signal.signal(signal.SIGINT, signal.default_int_handler)\n"
        f"mutate.run({str(subject)!r}, {command!r},\n"
        f"           [mutate.Mutation('drop the guard', 'GUARD = True', 'GUARD = False')])\n"
    )

    def start_and_signal(sig):
        proc = subprocess.Popen(
            [sys.executable, str(driver)],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        deadline = time.time() + 30
        while time.time() < deadline:
            if "GUARD = False" in subject.read_text():
                break
            if proc.poll() is not None:
                raise AssertionError(f"driver exited early: {proc.communicate()[0]}")
            time.sleep(0.02)
        else:
            proc.kill()
            proc.wait()
            raise AssertionError("precondition: no mutant ever reached disk")
        proc.send_signal(sig)
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            raise AssertionError("the driver ignored the signal") from None
        return proc

    return root, subject, start_and_signal


def test_a_SIGTERM_mid_campaign_still_restores_the_subject(killable):
    """The measured defect, and the reason this section exists.

    A default SIGTERM terminates the interpreter without running `finally`, so the harness's
    2-minute foreground cap left `scripts/settings-hooks-check.py` carrying a live mutant that
    made it report `RESULT: PASS rc=0` on unreadable input — a checker inverted into a rubber
    stamp, sitting in the working tree.
    """
    _, subject, start_and_signal = killable
    proc = start_and_signal(signal.SIGTERM)
    assert subject.read_text() == SUBJECT_SRC, (
        f"SIGTERM stranded the mutant on disk (driver rc={proc.returncode})"
    )
    assert proc.returncode == -signal.SIGTERM, (
        "a handled signal must still kill the process with that signal — swallowing it turns a "
        f"killed run into an apparently clean finish (rc={proc.returncode})"
    )
    assert not mutate.backup_path(subject).exists()


def test_a_SIGINT_mid_campaign_still_restores_the_subject(killable):
    """Green BEFORE the fix — SIGINT raises `KeyboardInterrupt`, which does run `finally`.

    Recorded as a measurement, not as evidence the fix is safe: a row that was already green is
    not a test of the change. Its job is to go red if a SIGTERM handler is written in a way that
    breaks the path that already worked.

    The backup assertion is NOT free, and was red when first written: `KeyboardInterrupt`
    unwinds past the normal return, so a cleanup placed after the `try` stranded an undamaged
    backup on every Ctrl-C — litter a `git add -A` could commit.
    """
    _, subject, start_and_signal = killable
    start_and_signal(signal.SIGINT)
    assert subject.read_text() == SUBJECT_SRC
    assert not mutate.backup_path(subject).exists(), (
        "a verified restore must drop the backup, whatever unwound to get there"
    )


def test_a_SIGKILL_leaves_the_original_recoverable_on_disk(killable):
    """No handler catches SIGKILL, so the guarantee cannot BE a handler.

    A signal handler closes the measured case and nothing else; power loss and `kill -9` strand
    the subject exactly as before, and just as silently. The only thing that survives an
    uncatchable kill is a file that was already written.
    """
    _, subject, start_and_signal = killable
    start_and_signal(signal.SIGKILL)
    assert subject.read_text() != SUBJECT_SRC, (
        "precondition: SIGKILL really did strand the mutant"
    )
    backup = mutate.backup_path(subject)
    assert backup.exists(), "nothing on disk holds the original"
    assert backup.read_text() == SUBJECT_SRC


def test_a_signal_the_parent_IGNORED_is_left_ignored(bed):
    """Un-ignoring a signal the operator arranged to survive would kill the run they protected.

    `nohup` ignores SIGHUP; a shell backgrounding a job ignores SIGINT/SIGQUIT. Both are how
    these campaigns actually run, since the foreground cap is what stranded a subject to begin
    with — so blanket handler installation would trade one killed run for another.

    Observed through the suite SUBPROCESS, which is where the disposition becomes externally
    visible: `exec` resets HANDLED signals to the default but leaves IGNORED ones ignored. A
    child that still sees SIGHUP ignored therefore proves the parent left it alone.
    """
    root, subject, _ = bed
    log = root / "hup.log"
    watcher = root / "hup.sh"
    watcher.write_text(
        "#!/usr/bin/env bash\n"
        f'if [ -n "$(trap -p HUP)" ]; then echo ignored >> "{log}"; '
        f'else echo installed >> "{log}"; fi\n'
        "printf 'PASS  observed\\n'\nexit 0\n"
    )
    watcher.chmod(0o755)

    previous = signal.signal(signal.SIGHUP, signal.SIG_IGN)
    try:
        mutate.run(subject, ["bash", str(watcher)], [DROP_GUARD])
    finally:
        signal.signal(signal.SIGHUP, previous)

    seen = log.read_text().split()
    assert seen, "the suite ran at least once (baseline + one mutant)"
    assert set(seen) == {"ignored"}, (
        f"a deliberately-ignored SIGHUP was un-ignored by the campaign: {seen}"
    )


def test_a_stale_backup_over_a_CORRUPT_subject_refuses_to_run(bed):
    """The teeth. A leftover mutant in an UNCOVERED path leaves the baseline green.

    The baseline check only rescues a leftover mutation the suite happens to cover; where it does
    not, the campaign runs against a corrupted subject and reports a clean sweep. Refusing at
    startup is what makes that case loud instead of invisible.
    """
    _, subject, suite = bed
    mutate.backup_path(subject).write_text(SUBJECT_SRC)
    subject.write_text(SUBJECT_SRC.replace("GUARD = True", "GUARD = False"))
    report = mutate.run(subject, suite(), [RAISE_LIMIT])
    assert report.status == "ERROR"
    assert report.rc == 2
    assert report.caught == 0
    assert str(mutate.backup_path(subject)) in report.text, (
        "the refusal must name the file holding the original"
    )


def test_a_stale_backup_over_an_INTACT_subject_is_cleared_and_the_run_proceeds(bed):
    """A kill before any mutation landed damaged nothing — refusing there would be a false block.

    Comparing the backup's CONTENT against the subject discriminates the two cases exactly, so
    the refusal above costs no legitimate campaign.
    """
    _, subject, suite = bed
    backup = mutate.backup_path(subject)
    backup.write_text(SUBJECT_SRC)
    report = mutate.run(subject, suite(), [DROP_GUARD])
    assert report.status == "PASS", report.text
    assert not backup.exists()


def test_the_backup_exists_while_the_suite_runs_and_is_gone_afterwards(bed):
    """Asserts the protection window, not merely the absence of litter.

    Checking only that no backup remains would pass vacuously against a module that never writes
    one — which is exactly the state this row was written in. So the suite records what it SAW,
    the same instrument the `__pycache__` row uses, and the assertion is on that record.
    """
    root, subject, _ = bed
    log = root / "seen.log"
    backup = mutate.backup_path(subject)
    watcher = root / "watch.sh"
    watcher.write_text(
        "#!/usr/bin/env bash\n"
        f'if [ -f "{backup}" ]; then echo present >> "{log}"; '
        f'else echo absent >> "{log}"; fi\n'
        "printf 'PASS  observed\\n'\nexit 0\n"
    )
    watcher.chmod(0o755)
    mutate.run(subject, ["bash", str(watcher)], [DROP_GUARD])
    seen = log.read_text().split()
    assert seen, "the suite ran at least once (baseline + one mutant)"
    assert set(seen) == {"present"}, (
        f"the original was unprotected during part of the run: {seen}"
    )
    assert not backup.exists(), "a clean campaign must leave no litter behind"


def test_a_failed_restore_KEEPS_the_backup(bed, monkeypatch):
    """The one case where litter is the correct outcome — it is the only copy left."""
    _, subject, suite = bed
    real = mutate.Path.write_text
    backup = mutate.backup_path(subject)

    def clobber(self, data, *a, **kw):
        if self == subject and data == SUBJECT_SRC:
            return real(self, "CLOBBERED\n", *a, **kw)
        return real(self, data, *a, **kw)

    monkeypatch.setattr(mutate.Path, "write_text", clobber)
    report = mutate.run(subject, suite(), [DROP_GUARD])
    monkeypatch.undo()
    assert report.status == "ERROR"
    assert backup.exists(), (
        "the backup is the recovery; a failed restore must not delete it"
    )
    assert backup.read_text() == SUBJECT_SRC


# ---------- P10: a suite that HANGS must not hang the campaign ----------

SUITE_HANGS_ALWAYS_SRC = """#!/usr/bin/env bash
sleep 30
"""

# Backgrounds a grandchild before hanging. Killing only the direct child orphans it, which is
# how a campaign accumulates stray processes that outlive the run and disturb later mutations.
#
# The grandchild's two details are both load-bearing, and the row SURVIVED its mutation without
# them. `>/dev/null 2>&1` hands it fresh fds: inheriting the captured stdout pipe makes it hold
# that pipe open, so `communicate()` blocks on EOF even after the direct child is dead — masking
# the leak as a mere delay. And its sleep must outlast the whole campaign, or it dies of natural
# causes during that block and the assertion finds it gone either way. At `sleep 30` sharing the
# pipe, the mutation that kills only the direct child scored CAUGHT=0 — a green row proving
# nothing. The PARENT's hang stays short for the opposite reason: see SUITE_HANGS_ON_MUTANT_SRC.
SUITE_SPAWNS_THEN_HANGS_SRC = """#!/usr/bin/env bash
if grep -q 'GUARD = True' "$1"; then
  printf 'PASS  guard intact\\n'
  exit 0
fi
sleep 300 >/dev/null 2>&1 &
echo $! > "$2"
sleep 30
"""


def test_a_hanging_mutant_is_INDETERMINATE_rather_than_caught(bed):
    """A hang is not evidence the suite noticed — scoring it CAUGHT would inflate the sweep.

    That is the direction every prior mutate.py hole failed in, so the outcome gets its own name
    and forces the campaign to ERROR: no verdict was reached about this mutation.
    """
    _, subject, suite = bed
    report = mutate.run(
        subject, suite(SUITE_HANGS_ON_MUTANT_SRC, "hang.sh"), [DROP_GUARD], timeout=2
    )
    assert report.outcomes[0].status == mutate.TIMEOUT
    assert report.caught == 0, "a hang must never be credited as a catch"
    assert report.survived == 0, "nor as a survivor — neither is known"
    assert report.timedout == 1
    assert report.status == "ERROR"
    assert report.rc == 2


def test_a_hanging_mutant_still_restores_the_subject(bed):
    """The whole point: a hung campaign used to strand the mutant on disk indefinitely."""
    _, subject, suite = bed
    mutate.run(
        subject, suite(SUITE_HANGS_ON_MUTANT_SRC, "hang.sh"), [DROP_GUARD], timeout=2
    )
    assert subject.read_text() == SUBJECT_SRC
    assert not mutate.backup_path(subject).exists()


def test_a_hanging_BASELINE_errors_rather_than_hanging(bed):
    """The baseline is a suite run too, and it is the first thing that can hang."""
    _, subject, suite = bed
    report = mutate.run(
        subject, suite(SUITE_HANGS_ALWAYS_SRC, "halt.sh"), [DROP_GUARD], timeout=2
    )
    assert report.status == "ERROR"
    assert report.rc == 2
    assert report.caught == 0
    assert "timed out" in report.text.lower()


def test_a_timed_out_suite_does_not_leak_its_CHILDREN(bed):
    """Killing only the direct child orphans its grandchildren, which then outlive the campaign.

    Measured during this work: a killed campaign left `sleep 60` processes and three concurrent
    copies of a suite still running, which is noise the next mutation inherits.
    """
    root, subject, suite = bed
    pidfile = root / "grandchild.pid"
    path = root / "spawn.sh"
    path.write_text(SUITE_SPAWNS_THEN_HANGS_SRC)
    path.chmod(0o755)
    command = ["bash", str(path), str(subject), str(pidfile)]

    mutate.run(subject, command, [DROP_GUARD], timeout=3)

    assert pidfile.exists(), "precondition: the suite really did spawn a grandchild"
    pid = int(pidfile.read_text().strip())
    time.sleep(0.5)
    alive = True
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError):
        alive = False
    if alive:  # do not leave it behind whatever the verdict
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
    assert not alive, f"grandchild {pid} outlived the timed-out suite"


@pytest.mark.parametrize(
    "baseline, want, why",
    [
        (0.5, 60.0, "a fast suite gets the floor, not a uselessly tight 10s"),
        (2.0, 60.0, "still under the floor at 40s"),
        (17.0, 340.0, "a slow suite scales past the floor rather than being truncated"),
        (
            28.0,
            560.0,
            "covers the MEASURED 297s a genuinely-caught run-long.sh mutant takes on a 28s "
            "baseline — the case where the first multiplier tried scored a false TIMEOUT",
        ),
    ],
)
def test_the_derived_timeout_scales_off_the_MEASURED_baseline(baseline, want, why):
    """Derived from what was just measured, with a declared floor — not a hand-picked constant.

    A fixed default cannot serve both a 6s suite and a 28s one: tight enough to catch the fast
    one's hang quickly, it truncates the slow one and reports a hang that never happened. That
    is not hypothetical — the 28s row below is the case that actually misfired.
    """
    assert mutate.derive_timeout(baseline, None) == want, why


def test_an_explicit_timeout_overrides_the_derivation():
    assert mutate.derive_timeout(17.0, 5) == 5


# ---------- the module's own gates ----------


def test_module_declares_the_future_annotations_import():
    """scripts/lib/*.py is globbed by test_py39_compat.sh; this fails faster and names why."""
    src = (Path(__file__).resolve().parent.parent / "lib" / "mutate.py").read_text()
    assert "from __future__ import annotations" in src


def test_module_is_not_executable():
    """A library that is only ever imported must not carry the exec bit (or a shebang)."""
    path = Path(__file__).resolve().parent.parent / "lib" / "mutate.py"
    assert not os.access(path, os.X_OK)
    assert not path.read_text().startswith("#!")

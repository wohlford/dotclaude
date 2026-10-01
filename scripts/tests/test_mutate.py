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

import inspect
import os
import re
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
# that); it is that a line is READABLE while the process is still running — and "still running"
# must be established BY CONSTRUCTION, since a child flushes its whole buffer at exit and a
# parent can read that finished artifact before `poll()` reports the exit: measured at 2/20 and
# 1/20 false clears.

# The suite parks the campaign inside its SECOND mutation's run, identified by that mutation's
# own marker. By then the campaign has emitted both the baseline line and a per-mutation line,
# so what the test observes is progress arriving INCREMENTALLY, not merely an opening line.
# While this script blocks, the campaign is inside `communicate()` and cannot have exited — which
# is what makes an exit-time flush impossible here rather than merely unlikely.
#
# The park must stay EARLY in the campaign. Measured at this point the artifact holds 224 bytes,
# and nothing reaches the file until ~8200 — the binding limit is TextIOWrapper's 8192-char
# pending buffer, NOT the 4096-byte block size underneath it — so there are about 200 further
# progress lines of headroom. Move the park later and a buffered mutant would fill that buffer
# and flush for FREE before the snapshot, so this row would go green with the defect present,
# which is byte-identical to green without it.
#
# The wait is bounded in WALL CLOCK, not iterations: an iteration count is not a bound, because
# the multiplier belongs to the host. Measured here, `sleep 0.1` costs 0.263s — this machine adds
# a flat ~0.146s to every sleep, so a 150-iteration version ran 38s, not the 16s it claimed, i.e.
# OVER the driver's 30s per-mutant timeout rather than under it. 20s of wall clock is genuinely
# under that cap. On overrun either way the behaviour is safe: whichever bound expires first, the
# campaign's own `_terminate_group` reaps this script and its `sleep` along with it.
# `-F --` because the marker is DERIVED: a renamed row could carry a leading `-` or a regex
# metacharacter, and as a BRE those silently never match, so the gate would quietly stop firing.
# (A marker containing a single quote would still break the generated script — out of bounds.)
GATED_SUITE_SRC = """#!/usr/bin/env bash
if grep -qF -- '{marker}' "$1"; then
  : > "{ready}"
  end=$(( $(date +%s) + 20 ))
  while [ ! -e "{release}" ] && [ "$(date +%s)" -lt "$end" ]; do sleep 0.1; done
fi
if grep -q 'GUARD = True' "$1"; then
  printf 'PASS  guard intact\\n'
  exit 0
fi
printf 'FAIL  guard missing\\n'
exit 1
"""

# The mutation list is DERIVED from the module's own rows rather than retyped here. The gate
# above greps for the second row's marker, so a hand-copy would let a rename unhook the gate
# silently: the campaign would apply one string while the script waited for another, and the
# park would simply never happen.
DRIVER_SRC = """import sys
sys.path.insert(0, {lib!r})
import mutate
report = mutate.run(
    {subject!r},
    ["bash", {suite!r}, {subject!r}],
    [mutate.Mutation(*m) for m in {mutations!r}],
    timeout=30,
)
print(report.text)
"""


def _terminate_driver_group(proc) -> None:
    """Reap the driver's process GROUP, not just the driver.

    A bare `kill()` leaves whatever the driver spawned into its own group, which is the leak
    `mutate._terminate_group` exists for and which `test_a_timed_out_suite_does_not_leak_its_
    CHILDREN` asserts against. The driver is started in its own session so this can `killpg`
    without taking pytest down with it.

    Honest limit: `mutate.run` gives its suite `start_new_session=True` too, so the gated script
    and its `sleep` sit in a group this cannot reach. That leak is BOUNDED rather than absent —
    the caller has already touched the release file, and the script's own wall-clock cap expires
    regardless — but it is not reaped here, and saying otherwise would be the reassuring line
    that stops the next reader looking.
    """
    # Bare OSError, mirroring `mutate._terminate_group`: this runs inside a `finally`, so any
    # exception escaping here would mask the genuine assertion — the exact masking the caller's
    # `parked is not None` guard exists to prevent. A narrower list is a bet on the errno set.
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except OSError:
        try:
            proc.kill()
        except OSError:
            pass
    try:
        proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        pass


def test_a_mutation_line_is_readable_before_the_campaign_exits(bed):
    """Progress must reach the artifact DURING the run, not in one flush at exit.

    The campaign is parked inside its second mutation's suite run, so it cannot have exited and
    an exit-time flush cannot be what the snapshot shows. Asserting the verdict is still ABSENT
    is what makes that checkable rather than assumed: it is the assertion that fails if the park
    ever stops parking.

    Measured before this shape existed: polling `while proc.poll() is None` and accepting any
    progress word passed 2/20 and 1/20 against the flush mutation on two subjects, because a
    child flushes its whole buffer at interpreter shutdown and the parent can read that complete
    artifact before `poll()` reports the exit. All three spurious passes contained the final
    verdict line.
    """
    root, subject, suite = bed
    ready, release = root / "ready", root / "release"
    # Both the gate's marker and the campaign's rows come from RAISE_LIMIT, so they cannot drift
    # apart: rename it and the gate follows, rather than waiting for a string nobody applies.
    cmd = suite(
        GATED_SUITE_SRC.format(marker=RAISE_LIMIT.new, ready=ready, release=release),
        "gated.sh",
    )
    driver = root / "driver.py"
    driver.write_text(
        DRIVER_SRC.format(
            lib=str(Path(mutate.__file__).parent),
            subject=str(subject),
            suite=cmd[1],
            mutations=[tuple(DROP_GUARD), tuple(RAISE_LIMIT)],
        )
    )
    artifact = root / "run.log"
    # PYTHONUNBUFFERED makes the interpreter flush stdout whatever `_stream` does, so a driver
    # that inherited it would pass this test with the flush REMOVED — the environment answering
    # for the subject, silently, because green-with-defect is byte-identical to green-without.
    # Strip it rather than trusting whatever the ambient environment happens to hold.
    env = {k: v for k, v in os.environ.items() if k != "PYTHONUNBUFFERED"}

    with open(artifact, "w") as sink:
        proc = subprocess.Popen(
            [sys.executable, str(driver)],
            stdout=sink,
            stderr=subprocess.STDOUT,
            env=env,
            # Its own session, so the teardown can reap a process GROUP without signalling
            # pytest itself. Without this a `killpg` here would kill the test run.
            start_new_session=True,
        )
        parked = None
        try:
            deadline = time.monotonic() + 60
            while not ready.exists() and time.monotonic() < deadline:
                # An early exit is a hard failure, never a reason to stop looking — it is the
                # one state in which the artifact could hold a complete, flushed-at-exit run.
                assert proc.poll() is None, (
                    "the campaign exited before it ever parked:\n"
                    + artifact.read_text()
                )
                time.sleep(0.02)
            assert ready.exists(), (
                "the campaign never reached its second mutation:\n"
                + artifact.read_text()
            )
            parked = artifact.read_text()
        finally:
            release.touch()
            # A campaign that hangs is the very defect class this suite polices, so the teardown
            # must not hang with it — but it must not TALK OVER it either. The two conditions
            # share a cause: a campaign that hung before mutation 2 fails the assert above AND
            # then times out here, so raising unconditionally would replace the real diagnosis
            # with a message about a park that never happened. Only speak when nothing else has.
            try:
                proc.wait(timeout=60)
            except subprocess.TimeoutExpired:
                _terminate_driver_group(proc)
                if parked is not None:
                    # Carry the snapshot: this branch pre-empts the three assertions below, so
                    # dropping it would repeat one line down the very masking the guard above
                    # exists to stop — the evidence would be gone with the tmp_path.
                    raise AssertionError(
                        "the driver never exited after the park was released:\n"
                        + parked
                    ) from None

    assert "RESULT:" not in parked, (
        "the campaign had already finished, so this snapshot proves nothing about streaming — "
        "the park failed to park:\n" + parked
    )
    assert "BASELINE" in parked and "[1/2]" in parked, (
        "the artifact carried no progress while the campaign was parked mid-run — empty is "
        "byte-identical to a stall, which is the measured defect:\n" + parked
    )
    assert proc.returncode == 0, artifact.read_text()


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
    assert report.verdict.endswith("total=1 invalid=0"), (
        "invalid= is appended AFTER total=, so an unanchored `--expect ... total=[0-9]+` "
        "pattern keeps matching"
    )


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
            f"timedout={report.timedout} total={report.total} invalid={report.invalid}"
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


def test_the_backup_sits_beside_the_REAL_file_whatever_spelling_the_subject_had(bed):
    """The edit guard looks for the sidecar beside the file an edit RESOLVES to.

    A campaign handed a FILE symlink used to park its sidecar beside the link, so an edit through
    the real path found nothing — the one spelling pair scripts/mutate-edit-guard.py cannot reach
    on its own. Both spellings must now name the one sidecar beside the real file.
    """
    root, subject, _ = bed
    link = root / "link.py"
    link.symlink_to(subject)
    linkdir = root / "linkdir"
    linkdir.symlink_to(root, target_is_directory=True)
    want = root / ("subject.py" + mutate.BACKUP_SUFFIX)
    assert mutate.backup_path(subject) == want
    assert mutate.backup_path(link) == want, (
        "a file-symlink subject parked its sidecar by the link"
    )
    assert mutate.backup_path(linkdir / "subject.py") == want


def test_a_TORN_backup_write_leaves_no_partial_sidecar(bed, monkeypatch):
    """A partial sidecar would be PRESENTED AS THE ORIGINAL.

    scripts/mutate-edit-guard.py blocks any file whose sidecar exists and prints
    `cp <sidecar> <subject>` as the recovery, so a torn write left on disk turns a disk-full
    moment into an instructed overwrite of a pristine subject with a truncated one.
    """
    _, subject, suite = bed
    backup = mutate.backup_path(subject)
    real = mutate.Path.write_text

    def torn(self, data, *a, **kw):
        if self.name.startswith(backup.name):  # the sidecar and any temp beside it
            real(self, data[:5], *a, **kw)
            raise OSError("simulated disk full")
        return real(self, data, *a, **kw)

    monkeypatch.setattr(mutate.Path, "write_text", torn)
    report = mutate.run(subject, suite(), [DROP_GUARD])
    assert report.status == "ERROR", report.text
    assert "cannot write the backup" in report.text
    leftovers = sorted(
        p.name for p in subject.parent.iterdir() if p.name.startswith(backup.name)
    )
    assert leftovers == [], f"a torn write left {leftovers} on disk"
    assert subject.read_text() == SUBJECT_SRC


def test_a_campaign_through_a_FILE_SYMLINK_restores_the_target_and_clears_the_sidecar(
    bed,
):
    """PRESERVE (green before and after this change): end to end through a link.

    Its job is to go red if resolving the sidecar path ever changes WHERE the restore lands or
    strands a sidecar beside either spelling.
    """
    root, subject, suite = bed
    link = root / "link.py"
    link.symlink_to(subject)
    report = mutate.run(link, suite(), [DROP_GUARD])
    assert report.status == "PASS", report.text
    assert link.is_symlink(), "the restore replaced the link with a regular file"
    assert subject.read_text() == SUBJECT_SRC
    assert not mutate.backup_path(subject).exists()
    assert not (root / ("link.py" + mutate.BACKUP_SUFFIX)).exists()


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


# ---------- P11: HEADROOM — how close the slowest row came to the cap ----------

# A per-mutant TIMEOUT is INDETERMINATE, so it carries the whole campaign to ERROR and reads like
# a defect in the change under test. Measured 2026-08-09: a campaign returned `ERROR … timedout=1`
# on a pre-existing row after its suite grew 80 -> 108 rows, and diagnosing it cost a full re-run
# plus an isolated single-row probe. The run BEFORE it had a slowest row at 628.5s of a 900s cap —
# 70%, recorded nowhere. The value of this line is RETROSPECTIVE: it collapses that diagnosis to
# one grep of the previous artifact. Nobody reads a green campaign's report, and claiming
# otherwise would be the overclaim this section is written against.

# Manufactures the elapsed spread the figure is read from. Every mutant runs the SAME suite, so
# real per-row elapsed differs only by noise — a fixture that relied on that difference would be a
# timing flake in waiting, green or red by luck. This gives one NAMED row a genuine 2s delay,
# better than an order of magnitude clear of the sub-100ms the other rows run in. `-F --` because
# the marker is DERIVED from a Mutation's `new`: a renamed row could carry a leading `-` or a
# regex metacharacter, and as a BRE those silently never match, so the delay would stop happening
# and the row would go green on noise.
SUITE_SLOW_ON_MARKER_SRC = """#!/usr/bin/env bash
if grep -qF -- '{marker}' "$1"; then sleep 2; fi
if grep -q 'GUARD = True' "$1"; then
  printf 'PASS  guard intact\\n'
  exit 0
fi
printf 'FAIL  guard missing\\n'
exit 1
"""


def _headroom(report) -> str:
    """The one `HEADROOM: ` line, or raise.

    Scoped to a line, and matched on the PREFIX every state shares: the whole point of the shared
    prefix is that one grep finds the line whatever happened, so a test that searched the whole
    report would pass with the line deleted and some other line mentioning the word.
    """
    found = [ln for ln in report.text.split("\n") if ln.startswith("HEADROOM: ")]
    assert len(found) == 1, f"expected exactly one HEADROOM line, got {found}"
    return found[0]


def test_the_headroom_line_does_not_displace_the_verdict(bed):
    """The spike's decisive finding: three rows assert the verdict is LAST and a mutation pins it.

    So the figure goes on its own line immediately BEFORE the verdict. Appending it after would
    break all four at once — which is why the emission site is not a matter of taste here.
    """
    _, subject, suite = bed
    report = mutate.run(subject, suite(), [DROP_GUARD], timeout=30)
    lines = report.text.split("\n")
    assert lines[-1] == report.verdict
    assert _headroom(report) in lines[:-1]


def test_the_RESULT_line_is_BYTE_IDENTICAL_beside_the_headroom_line(bed):
    """Campaigns are launched through `run-long.sh --expect '<regex>'`, matched against this line.

    The patterns are supplied per invocation and are not stored in-repo, so there is no corpus to
    satisfy — and that cuts both ways: any future prefix-anchored pattern must keep matching, so
    the safe move is not to touch the line at all. Pinned against a LITERAL rather than left to
    inspection: a reformat that reads fine to a human is exactly what stops a pattern matching.
    """
    _, subject, suite = bed
    report = mutate.run(subject, suite(), [DROP_GUARD], timeout=30)
    assert report.verdict == (
        "RESULT: PASS rc=0 caught=1 survived=0 timedout=0 total=1 invalid=0"
    )
    assert re.match(r"RESULT: (PASS|FAIL) rc=[0-9]+ caught=", report.verdict), (
        "the prefix-anchored --expect shape these campaigns are actually launched with"
    )


@pytest.mark.parametrize("slow", [DROP_GUARD, RAISE_LIMIT], ids=lambda m: m.label)
def test_the_headroom_figure_MOVES_with_whichever_row_is_slowest(bed, slow):
    """A figure that cannot change is not reading anything.

    Both parametrisations run the same two mutations against the same suite; only WHICH row is
    delayed differs. Asserting the LABEL rather than a percentage is deliberate — the percentage
    is the same in both, so a percentage assertion would pass against a hard-coded constant.
    """
    _, subject, suite = bed
    cmd = suite(SUITE_SLOW_ON_MARKER_SRC.format(marker=slow.new), "slow.sh")
    report = mutate.run(subject, cmd, [DROP_GUARD, RAISE_LIMIT], timeout=30)
    assert report.timedout == 0, report.text
    line = _headroom(report)
    assert line.startswith("HEADROOM: slowest="), line
    other = RAISE_LIMIT if slow is DROP_GUARD else DROP_GUARD
    assert repr(slow.label) in line, line
    assert repr(other.label) not in line, (
        "the fast row was named as the slowest — a min/max inversion reads exactly like this: "
        + line
    )


def test_the_DERIVED_cap_is_printed_only_when_an_override_is_in_force(bed):
    """`derived=` asserts nothing; it makes "this row survives only because of the override" legible.

    The override here (30s) is deliberately NOT the value the derivation would produce (60s, the
    floor), so the assertion can tell which number reached the line. A test supplying the option's
    own default cannot tell whether the option is read at all.
    """
    _, subject, suite = bed
    overridden = _headroom(mutate.run(subject, suite(), [DROP_GUARD], timeout=30))
    assert "cap=30s" in overridden, overridden
    assert " derived=60s" in overridden, overridden

    plain = _headroom(mutate.run(subject, suite(), [DROP_GUARD]))
    assert "derived=" not in plain, (
        "with no override there is nothing to compare against: " + plain
    )
    assert "cap=60s" in plain, plain


def test_the_BASELINE_cap_is_reported_too(bed):
    """`BASELINE_TIMEOUT_SECONDS` is FLAT and printed nowhere else.

    The existing `BASELINE green` line shows the baseline's elapsed and the MUTANT limit — never
    the limit the baseline itself ran under. The per-mutant cap DERIVES from the baseline, so it
    rescales as a suite grows; the flat one does not, which makes it the cap guaranteed to erode
    by exactly the mechanism that caused the measured incident (a suite going 80 -> 108 rows).
    """
    _, subject, suite = bed
    plain = _headroom(mutate.run(subject, suite(), [DROP_GUARD]))
    assert f"/{mutate.BASELINE_TIMEOUT_SECONDS:.0f}s" in plain, plain
    # An override replaces the baseline's cap as well as the mutants', and the line must follow
    # the value the baseline ACTUALLY ran under rather than restating the constant.
    overridden = _headroom(mutate.run(subject, suite(), [DROP_GUARD], timeout=30))
    assert "baseline=" in overridden and "/30s" in overridden, overridden


def test_a_timed_out_row_makes_the_line_LEAD_with_exhausted(bed):
    """The case the whole design turns on: a timed-out row has NO elapsed.

    `run_suite` returns None on `TimeoutExpired` and the `_Run` carrying `elapsed` is never built,
    so the row contributes nothing to a "slowest completed" figure — while being known to have
    consumed at least the cap. A naive implementation prints a reassuring percentage beside an
    actual failure on the one run that matters most.

    Asserted on the PREFIX, not on "exhausted" appearing somewhere: a substring test is satisfied
    by a line leading `used=12%` and mentioning exhaustion afterwards, which is exactly the
    reassuring shape this forbids.
    """
    _, subject, suite = bed
    cmd = suite(SUITE_HANGS_ON_MUTANT_SRC, "hang.sh")
    report = mutate.run(subject, cmd, [DROP_GUARD, RAISE_LIMIT], timeout=2)
    assert report.timedout == 1, report.text
    assert report.survived == 1, (
        "precondition: one row must COMPLETE, or this cannot distinguish a leading percentage "
        "from the absence of one:\n" + report.text
    )
    line = _headroom(report)
    assert line.startswith("HEADROOM: exhausted"), line
    assert repr(DROP_GUARD.label) in line, "the timed-out row must be named: " + line
    assert "secondary" in line, (
        "the completed row's figure must be marked secondary, never left to lead: "
        + line
    )
    assert line.index("secondary") > line.index(repr(DROP_GUARD.label)), line


def test_every_row_timing_out_still_leads_with_exhausted_and_never_divides(bed):
    """No completed row at all — so there is nothing to be secondary, and still nothing to divide.

    The suite that hangs on EVERY run cannot serve here: `timeout` caps the baseline too, so it
    would time out first and the campaign would ERROR before any row ran. The hangs-on-mutant
    suite gives a fast baseline and a single row with nowhere to go but the cap.
    """
    _, subject, suite = bed
    cmd = suite(SUITE_HANGS_ON_MUTANT_SRC, "hang.sh")
    report = mutate.run(subject, cmd, [DROP_GUARD], timeout=2)
    assert report.timedout == 1, report.text
    assert report.caught == report.survived == 0, (
        "precondition: no row completed at all:\n" + report.text
    )
    line = _headroom(report)
    assert line.startswith("HEADROOM: exhausted"), line
    assert "used=" not in line, (
        "nothing completed, so no percentage may appear: " + line
    )


def test_rows_that_never_APPLIED_report_plainly_rather_than_zero_percent(bed):
    """`timedout == 0` here, so this must NOT say "exhausted" — and 0% would be a lie either way.

    A mutation whose anchor does not match exactly once never runs the suite at all, so the
    campaign measured nothing. `0%` is indistinguishable from a fast healthy run, which is the
    reading that stops anyone looking.
    """
    _, subject, suite = bed
    report = mutate.run(
        subject,
        suite(),
        [
            mutate.Mutation("absent anchor", "NOT PRESENT ANYWHERE", "XXX"),
            mutate.Mutation("ambiguous anchor", "= ", "XXX"),
        ],
        timeout=30,
    )
    assert (report.timedout, report.survived) == (0, 2), report.text
    line = _headroom(report)
    assert line.startswith("HEADROOM: not measured"), line
    assert "exhausted" not in line, (
        "nothing timed out, so the exhausted wording would misattribute the cause: "
        + line
    )
    assert "used=" not in line and "slowest=" not in line, (
        "no row completed, so there is no figure to divide: " + line
    )


def test_the_restore_FAILURE_report_carries_the_line_too(bed, monkeypatch):
    """Emitted before the restore check, so BOTH post-loop reports carry it.

    An implementation that appended just before the final `_report` would silently drop the line
    from this path — the ERROR report, which is the one a reader has most reason to grep.
    """
    _, subject, suite = bed
    real = mutate.Path.write_text

    def clobber(self, data, *a, **kw):
        return real(self, data if data != SUBJECT_SRC else "CLOBBERED\n", *a, **kw)

    monkeypatch.setattr(mutate.Path, "write_text", clobber)
    report = mutate.run(subject, suite(), [DROP_GUARD], timeout=30)
    monkeypatch.undo()
    subject.write_text(SUBJECT_SRC)
    assert report.status == "ERROR", "precondition: the restore really did fail"
    line = _headroom(report)
    assert line.startswith("HEADROOM: slowest="), line


# ---------- P12: INVALID — a mutant that never exercised the suite must not score CAUGHT ----------

# `is_caught` reads "non-zero exit" as a catch, and a mutant that makes the suite unable to RUN
# satisfies that exactly as a real catch does. Two detectors, one outcome: the mutant does not
# parse (checked before the suite runs), or pytest's collection aborted (read from the output).
# Each row below names the shape it pins; the mention-versus-perform rows put the banner text in
# output that only QUOTES it, because a match that cannot tell the two apart is the defect.

UNPARSEABLE = mutate.Mutation("does not parse", "GUARD = True", "GUARD = (")

# Counts every run of the suite, so a row can prove a mutant never reached it.
SUITE_COUNTING_SRC = """#!/usr/bin/env bash
printf 'x\\n' >> "{counter}"
""" + SUITE_SRC.split("\n", 1)[1]

# Green on the pristine subject; on a mutant prints pytest's banner ALONE on its line and exits 2,
# with other lines before AND after it as real pytest output has, so the banner is neither the first
# nor the last line and a search that drops `re.M` cannot find it.
SUITE_BANNER_SRC = """#!/usr/bin/env bash
if grep -q 'GUARD = True' "$1"; then
  printf 'PASS  guard intact\\n'
  exit 0
fi
printf 'ERROR test_bad.py\\n'
printf '!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!\\n'
printf '1 error in 0.2s\\n'
exit 2
"""

# The banner phrase only QUOTED (mid-line / with trailing text) beside a genuine failing row.
SUITE_QUOTED_BANNER_SRC = """#!/usr/bin/env bash
if grep -q 'GUARD = True' "$1"; then
  printf 'PASS  guard intact\\n'
  exit 0
fi
printf '{quoted}\\n'
printf 'FAIL  guard missing\\n'
exit 1
"""

# Prints the banner even on a GREEN run: a predicate that matches the unmutated baseline.
SUITE_BANNER_ON_GREEN_SRC = """#!/usr/bin/env bash
printf 'collected 1 item\\n'
printf '!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!\\n'
printf 'PASS  guard intact\\n'
exit 0
"""


def _suite_for(root, subject, src, name="suite2.sh"):
    path = root / name
    path.write_text(src)
    path.chmod(0o755)
    return ["bash", str(path), str(subject)]


def test_an_UNPARSEABLE_mutant_is_INVALID_and_ends_the_campaign_ERROR(bed):
    """Old code: the suite fails on the broken file, so it scored CAUGHT and the campaign PASSed."""
    _, subject, suite = bed
    report = mutate.run(subject, suite(), [UNPARSEABLE])
    assert [o.status for o in report.outcomes] == [mutate.INVALID]
    assert report.invalid == 1
    assert report.caught == 0
    assert (report.status, report.rc) == ("ERROR", 2)
    assert report.verdict.endswith("total=1 invalid=1")
    assert "does not parse" in report.outcomes[0].detail


def test_an_UNPARSEABLE_mutant_never_runs_the_suite_and_the_subject_is_restored(bed):
    root, subject, suite = bed
    counter = root / "runs.txt"
    command = suite(SUITE_COUNTING_SRC.replace("{counter}", str(counter)))
    report = mutate.run(subject, command, [UNPARSEABLE])
    assert report.invalid == 1
    assert counter.read_text().count("x") == 1, (
        "the baseline ran; the invalid mutant did not"
    )
    assert subject.read_text() == SUBJECT_SRC
    assert "restored: sha256 unchanged" in report.text


def test_a_PARSEABLE_mutant_still_runs_the_suite_beside_an_INVALID_one(bed):
    """Control: INVALID must not deflate a real catch — the suite runs for the row that parses."""
    root, subject, suite = bed
    counter = root / "runs.txt"
    command = suite(SUITE_COUNTING_SRC.replace("{counter}", str(counter)))
    report = mutate.run(subject, command, [UNPARSEABLE, DROP_GUARD])
    assert (report.invalid, report.caught) == (1, 1)
    assert counter.read_text().count("x") == 2, "baseline + the one parseable mutant"


def test_a_collection_abort_banner_ALONE_on_its_line_is_INVALID(bed):
    _, subject, suite = bed
    report = mutate.run(subject, suite(SUITE_BANNER_SRC), [DROP_GUARD])
    assert [o.status for o in report.outcomes] == [mutate.INVALID]
    assert report.caught == 0
    assert (report.status, report.rc) == ("ERROR", 2)
    assert "collection aborted" in report.outcomes[0].detail


@pytest.mark.parametrize(
    "quoted",
    [
        "note: !!!! Interrupted: 1 error during collection !!!!",
        "!!!! Interrupted: 1 error during collection !!!! (from an inner run)",
    ],
    ids=["mid-line", "trailing-text"],
)
def test_a_QUOTED_banner_beside_a_real_failure_is_still_CAUGHT(bed, quoted):
    """Mention versus perform: the pattern is anchored at BOTH ends (`^` and `$`)."""
    _, subject, suite = bed
    src = SUITE_QUOTED_BANNER_SRC.replace("{quoted}", quoted)
    report = mutate.run(subject, suite(src), [DROP_GUARD])
    assert [o.status for o in report.outcomes] == [mutate.CAUGHT]
    assert (report.status, report.rc) == ("PASS", 0)


def test_the_banner_on_a_GREEN_baseline_is_an_ERROR_before_any_mutation(bed):
    _, subject, suite = bed
    report = mutate.run(subject, suite(SUITE_BANNER_ON_GREEN_SRC), [DROP_GUARD])
    assert (report.status, report.rc) == ("ERROR", 2)
    assert report.outcomes == ()
    assert "collection-abort predicate matches a green baseline" in report.text


def test_a_subject_kind_with_no_parser_is_reported_OFF_and_judged_by_the_suite(bed):
    root, _, _ = bed
    txt = root / "subject.txt"
    txt.write_text("GUARD = True\n")
    command = _suite_for(root, txt, SUITE_SRC)
    report = mutate.run(txt, command, [UNPARSEABLE])
    assert "parse check: OFF" in report.text
    assert [o.status for o in report.outcomes] == [mutate.CAUGHT]
    assert (report.status, report.invalid) == ("PASS", 0)


def test_a_parseable_subject_reports_the_parse_check_ON(bed):
    _, subject, suite = bed
    report = mutate.run(subject, suite(), [DROP_GUARD])
    assert "parse check: on" in report.text


def test_a_PRISTINE_subject_that_does_not_parse_leaves_the_check_OFF(bed):
    """Without the control every mutant of such a subject would read INVALID for no mutant's fault."""
    root, subject, _ = bed
    subject.write_text(SUBJECT_SRC + "LIMIT = (\n")
    command = _suite_for(root, subject, SUITE_SRC)
    report = mutate.run(subject, command, [UNPARSEABLE])
    assert "parse check: OFF" in report.text
    assert [o.status for o in report.outcomes] == [mutate.CAUGHT]
    assert report.invalid == 0


def test_an_UNPARSEABLE_bash_mutant_is_INVALID(bed):
    root, _, _ = bed
    sh = root / "subject.sh"
    sh.write_text("#!/usr/bin/env bash\nGUARD=1\necho hi\n")
    command = _suite_for(
        root,
        sh,
        SUITE_SRC.replace("GUARD = True", "GUARD=1"),
    )
    bad = mutate.Mutation("bash does not parse", "GUARD=1", "GUARD=$(")
    report = mutate.run(sh, command, [bad])
    assert [o.status for o in report.outcomes] == [mutate.INVALID]
    assert (report.status, report.rc) == ("ERROR", 2)


def test_check_parse_picks_its_parser_from_the_SHEBANG_too():
    assert (
        mutate.check_parse("tool", "#!/usr/bin/env python3\nx = (\n")[0]
        == mutate.UNPARSEABLE
    )
    assert (
        mutate.check_parse("tool", "#!/usr/bin/env python3\nx = 1\n")[0]
        == mutate.PARSES
    )
    assert mutate.check_parse("tool", "#!/bin/bash\nif then\n")[0] == mutate.UNPARSEABLE
    assert mutate.check_parse("tool", "no shebang\n")[0] == mutate.UNCHECKED
    assert mutate.check_parse("conf.jsonc", "{}")[0] == mutate.UNCHECKED


@pytest.mark.parametrize(
    "name, text, kind",
    [
        # The suffix decides first: a comment on the shebang line naming the OTHER kind must not.
        ("a.py", "#!/usr/bin/env python3 # was bash\nif True:\n    pass\n", "python"),
        ("a.py", "#!/bin/bash\nx = 1\n", "python"),
        ("a.sh", "#!/usr/bin/env python3\nfi\n", "bash"),
        # No suffix: the interpreter's BASENAME, after `env` and its flags.
        ("tool", "#!/usr/bin/env python3 # was bash\nx = 1\n", "python"),
        ("tool", "#!/usr/bin/env -S python3 -u\nx = 1\n", "python"),
        ("tool", "#!/usr/bin/env -i bash\nx=1\n", "bash"),
        ("tool", "#!/usr/bin/env FOO=1 bash\nx=1\n", "bash"),
        ("tool", "#!/opt/bin/python3.11\nx = 1\n", "python"),
        ("tool", "#!/bin/bash\nx=1\n", "bash"),
        ("tool", "#!/usr/local/bin/mybash\nx=1\n", "unchecked"),
        ("tool", "#!/usr/bin/env node # python\nx=1\n", "unchecked"),
        ("tool", "#!/bin/sh\nx=1\n", "unchecked"),
        ("tool", "#!\nx=1\n", "unchecked"),
        ("tool", "#!/usr/bin/env\nx=1\n", "unchecked"),
    ],
)
def test_parse_kind_reads_the_suffix_first_then_the_shebang_interpreter_basename(
    name, text, kind
):
    assert mutate._parse_kind(name, text) == kind


def test_a_py_file_whose_shebang_comment_says_bash_is_parsed_as_python():
    text = "#!/usr/bin/env python3 # was bash\nif True:\n    pass\n"
    assert mutate.check_parse("a.py", text)[0] == mutate.PARSES


def test_judge_mutant_parse_needs_a_PARSING_control():
    ok = mutate.check_parse("a.py", "x = 1\n")
    assert (
        mutate.judge_mutant_parse("a.py", "x = 1\n", "x = (\n", ok)[0]
        == mutate.UNPARSEABLE
    )
    bad = mutate.check_parse("a.py", "x = (\n")
    assert (
        mutate.judge_mutant_parse("a.py", "x = (\n", "y = (\n", bad)[0]
        == mutate.UNCHECKED
    )


# ---------- CANNOT_RUN: a parser that FAILED to run is not "no parser for this kind" ----------

# UNCHECKED means "no parser for this kind" (by design, visible, a non-failure). A `bash -n` that
# timed out or could not spawn is a different answer, and reading it as UNCHECKED let a transient
# infrastructure failure pass as reduced coverage. Every row below patches `subprocess.run` ONLY:
# the suite runs through `Popen`, so the campaign's own suite is untouched by the patch.


def _run_raises(exc):
    def fake(*args, **kwargs):
        raise exc

    return fake


@pytest.mark.parametrize(
    "exc",
    [
        subprocess.TimeoutExpired(["bash", "-n"], 1),
        OSError("no bash"),
        subprocess.SubprocessError("boom"),
    ],
)
def test_check_parse_of_a_bash_parser_that_cannot_run_is_CANNOT_RUN(monkeypatch, exc):
    monkeypatch.setattr(mutate.subprocess, "run", _run_raises(exc))
    verdict, detail = mutate.check_parse("tool.sh", "echo hi\n")
    assert verdict == mutate.CANNOT_RUN
    assert "bash -n could not run" in detail
    # The kind with no parser keeps its own answer, under the same patch.
    assert mutate.check_parse("notes.txt", "hi\n")[0] == mutate.UNCHECKED


@pytest.mark.parametrize("rc", [126, 127, 137, -9])
def test_a_bash_n_exit_other_than_1_or_2_is_the_PARSER_failing_not_the_mutant(
    monkeypatch, rc
):
    """rc 1/2 are bash reporting a syntax error; a wrapper that cannot exec bash exits 126/127."""

    def fake(*args, **kwargs):
        return subprocess.CompletedProcess(args, rc, "", "bash: cannot execute\n")

    monkeypatch.setattr(mutate.subprocess, "run", fake)
    verdict, detail = mutate.check_parse("tool.sh", "echo hi\n")
    assert verdict == mutate.CANNOT_RUN
    assert f"exited {rc}" in detail


@pytest.mark.parametrize("rc", [1, 2])
def test_a_bash_n_syntax_exit_with_EMPTY_stderr_is_the_PARSER_failing(monkeypatch, rc):
    """A syntax error always comes with a message; rc 1/2 and silence is a parser that died."""

    def fake(*args, **kwargs):
        return subprocess.CompletedProcess(args, rc, "", "")

    monkeypatch.setattr(mutate.subprocess, "run", fake)
    verdict, detail = mutate.check_parse("tool.sh", "echo hi\n")
    assert verdict == mutate.CANNOT_RUN
    assert f"bash -n exited {rc}" in detail


def test_a_bash_n_exit_1_with_stderr_is_UNPARSEABLE_carrying_the_first_line(
    monkeypatch,
):
    """bash 5.3 exits 1, not 2, for a syntax error inside an array assignment."""

    def fake(*args, **kwargs):
        return subprocess.CompletedProcess(
            args, 1, "", "bash: line 2: syntax error: unexpected end of file\nsecond\n"
        )

    monkeypatch.setattr(mutate.subprocess, "run", fake)
    verdict, detail = mutate.check_parse("tool.sh", "a=(\n")
    assert verdict == mutate.UNPARSEABLE
    assert detail == "bash: line 2: syntax error: unexpected end of file"


def _path_bash_accepts(text):
    return (
        subprocess.run(
            ["bash", "-n"], input=text, capture_output=True, text=True
        ).returncode
        == 0
    )


def test_a_REAL_bash_rejects_an_unterminated_array_assignment():
    """Real bash, not a fake: 5.3 exits 1 here, and a rule keyed on rc 2 alone misread it."""
    if _path_bash_accepts("a=(\n"):
        pytest.skip("this PATH bash accepts `a=(` (bash 3.2 returns rc 0)")
    assert mutate.check_parse("x.sh", "a=(\n")[0] == mutate.UNPARSEABLE
    multi = "arr=(\n  one\n  two\nif true; then :; fi\n"
    if not _path_bash_accepts(multi):
        assert mutate.check_parse("x.sh", multi)[0] == mutate.UNPARSEABLE


def test_check_parse_passes_utf8_surrogateescape_not_the_locale_codec(monkeypatch):
    seen = {}

    def fake(*args, **kwargs):
        seen.update(kwargs)
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(mutate.subprocess, "run", fake)
    assert mutate.check_parse("tool.sh", "echo hi\n")[0] == mutate.PARSES
    assert seen.get("encoding") == "utf-8"
    assert seen.get("errors") == "surrogateescape"
    assert "text" not in seen


def test_a_REAL_bash_parses_a_non_ascii_literal():
    assert mutate.check_parse("x.sh", "echo \u2014 hi\n")[0] == mutate.PARSES


def test_a_bash_n_exit_2_is_an_UNPARSEABLE_verdict_carrying_bashs_own_words(
    monkeypatch,
):
    def fake(*args, **kwargs):
        return subprocess.CompletedProcess(
            args, 2, "", "line 1: syntax error near `fi'\n"
        )

    monkeypatch.setattr(mutate.subprocess, "run", fake)
    verdict, detail = mutate.check_parse("tool.sh", "fi\n")
    assert verdict == mutate.UNPARSEABLE
    assert "syntax error" in detail


def test_check_parse_of_a_python_compile_that_gives_up_is_CANNOT_RUN(monkeypatch):
    def deep(*args, **kwargs):
        raise RecursionError("too deep")

    # A module-global shadow: patching the builtin would break pytest's own traceback rendering.
    monkeypatch.setattr(mutate, "compile", deep, raising=False)
    verdict, detail = mutate.check_parse("a.py", "x = 1\n")
    assert verdict == mutate.CANNOT_RUN
    assert "compile() gave up" in detail


def test_judge_mutant_parse_propagates_a_control_that_CANNOT_RUN():
    control = (mutate.CANNOT_RUN, "bash -n could not run: x")
    verdict, detail = mutate.judge_mutant_parse("a.sh", "echo\n", "fi\n", control)
    assert verdict == mutate.CANNOT_RUN
    assert detail.startswith("no control: ")


def test_judge_mutant_parse_keeps_UNCHECKED_for_an_unchecked_or_unparseable_control():
    for control in (
        mutate.check_parse("c.jsonc", "{}"),
        mutate.check_parse("a.py", "x = (\n"),
    ):
        assert control[0] in (mutate.UNCHECKED, mutate.UNPARSEABLE)
        verdict, _ = mutate.judge_mutant_parse("a.py", "x\n", "y\n", control)
        assert verdict == mutate.UNCHECKED


def test_judge_mutant_parse_propagates_a_mutants_own_CANNOT_RUN(monkeypatch):
    control = (mutate.PARSES, "")
    monkeypatch.setattr(mutate.subprocess, "run", _run_raises(OSError("gone")))
    assert (
        mutate.judge_mutant_parse("a.sh", "echo\n", "echo 2\n", control)[0]
        == mutate.CANNOT_RUN
    )


def _sh_bed(root):
    """A `.sh` subject plus a counting suite for it: (subject, command, counter)."""
    sh = root / "subject.sh"
    sh.write_text("#!/usr/bin/env bash\nGUARD=1\necho hi\n")
    counter = root / "runs.txt"
    command = _suite_for(
        root,
        sh,
        SUITE_COUNTING_SRC.replace("{counter}", str(counter)).replace(
            "GUARD = True", "GUARD=1"
        ),
    )
    return sh, command, counter


def test_a_control_whose_parse_check_CANNOT_RUN_ends_the_campaign_ERROR_untouched(
    bed, monkeypatch
):
    root, _, _ = bed
    sh, command, counter = _sh_bed(root)
    monkeypatch.setattr(mutate.subprocess, "run", _run_raises(OSError("no bash")))
    before = sh.read_text()
    report = mutate.run(sh, command, [mutate.Mutation("m", "GUARD=1", "GUARD=2")])
    assert (report.status, report.rc) == ("ERROR", 2)
    assert "the parse check could not run" in report.text
    assert "no verdict about any mutant can be reached" in report.text
    assert "parse check: OFF" not in report.text, (
        "a misleading OFF line preceded the ERROR"
    )
    assert "parse check: COULD NOT RUN" in report.text
    assert len(report.outcomes) == 0 and report.caught == 0
    assert counter.read_text().count("x") == 1, "only the baseline ran; no mutant did"
    assert sh.read_text() == before
    assert not mutate.backup_path(sh).exists()


def test_a_mutant_whose_parse_check_CANNOT_RUN_is_INVALID_and_never_runs(
    bed, monkeypatch
):
    root, _, _ = bed
    sh, command, counter = _sh_bed(root)
    before = sh.read_text()
    real = mutate.subprocess.run
    calls = []

    def flaky(*args, **kwargs):
        # The control (first call) parses; the mutant's check (second) cannot run.
        calls.append(1)
        if len(calls) == 1:
            return real(*args, **kwargs)
        raise subprocess.TimeoutExpired(["bash", "-n"], 1)

    monkeypatch.setattr(mutate.subprocess, "run", flaky)
    report = mutate.run(sh, command, [mutate.Mutation("m", "GUARD=1", "GUARD=2")])
    assert len(calls) == 2, "the control and the one mutant were each checked"
    assert [o.status for o in report.outcomes] == [mutate.INVALID]
    assert "the parse check could not run" in report.outcomes[0].detail
    assert "the suite was NOT run" in report.outcomes[0].detail
    assert report.invalid == 1 and report.caught == 0
    assert (report.status, report.rc) == ("ERROR", 2)
    assert "parse check could not run" in report.text
    assert counter.read_text().count("x") == 1
    assert sh.read_text() == before


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


# ---------- CLI usage: `--help` derives its call signature, it does not restate it ----------

# `python3 mutate.py --help` printed 0 bytes and exited 0, same as a bare invocation — a library
# with no visible call signature and no error either. The fix derives the usage text from
# `inspect.signature(run)` and `Mutation._fields` rather than hand-typing a copy that drifts the
# moment either side changes silently.

MUTATE_PY = Path(mutate.__file__).resolve()


def _run_cli(*args):
    return subprocess.run(
        [sys.executable, str(MUTATE_PY), *args],
        capture_output=True,
        text=True,
    )


def _own_line(text: str, prefix: str) -> str:
    """The one line of `text` that starts with `prefix`, or raise.

    Scoped to a single line on purpose: `report_path`, `fail_pattern`, `old` and `label` all
    recur in the usage's prose section too, so a whole-stdout assertion would still pass with
    the line under test deleted outright.
    """
    for line in text.splitlines():
        if line.strip().startswith(prefix):
            return line
    raise AssertionError(f"no line starts with {prefix!r} in:\n{text}")


def test_help_stdout_nonempty_and_stderr_silent():
    proc = _run_cli("--help")
    assert proc.returncode == 0
    assert proc.stdout.strip(), "the help text is empty"
    assert proc.stderr == "", proc.stderr


def test_h_alias_matches_help():
    proc = _run_cli("-h")
    assert proc.returncode == 0
    assert proc.stdout.strip()


def test_help_run_line_names_every_run_parameter():
    """The set is DERIVED from `run`'s own signature, so a renamed/added parameter cannot drift."""
    proc = _run_cli("--help")
    run_line = _own_line(proc.stdout, "run(")
    for name in inspect.signature(mutate.run).parameters:
        assert name in run_line, (
            f"{name!r} missing from the run(...) line: {run_line!r}"
        )


def test_help_mutation_fields_line_names_every_field():
    proc = _run_cli("--help")
    fields_line = _own_line(proc.stdout, "Mutation fields:")
    for name in mutate.Mutation._fields:
        assert name in fields_line, (
            f"{name!r} missing from the fields line: {fields_line!r}"
        )


def test_help_has_no_memory_address_noise():
    """A callable default rendered with a bare `repr()` leaks `<function ... at 0x...>`."""
    proc = _run_cli("--help")
    assert "0x" not in proc.stdout


def test_example_runs_against_a_real_fixture(tmp_path):
    """Extract `mutate._EXAMPLE`, substitute real fixture paths, and actually run it.

    This is the row that makes the printed example drift-proof: if `run`'s signature or
    behaviour ever moves out from under the example text, this executes the mismatch instead of
    a human eyeballing prose that looks plausible either way.
    """
    root = tmp_path.resolve()
    subject = root / "subject.py"
    subject.write_text("VALUE = 'old text'\n")
    suite = root / "test_suite.py"
    suite.write_text(
        "import pathlib\n"
        f"SUBJECT = pathlib.Path({str(subject)!r})\n"
        "def test_guard():\n"
        "    assert 'old text' in SUBJECT.read_text()\n"
    )
    report = root / "report.txt"

    example = mutate._EXAMPLE
    assert example.count("SUBJECT") == 1
    assert example.count("SUITE") == 1
    assert example.count("REPORT") == 1
    example = example.replace("SUBJECT", repr(str(subject)))
    example = example.replace("SUITE", repr(str(suite)))
    example = example.replace("REPORT", repr(str(report)))

    driver = root / "run_example.py"
    driver.write_text(
        f"import sys\nsys.path.insert(0, {str(MUTATE_PY.parent)!r})\n{example}\n"
    )

    proc = subprocess.run(
        [sys.executable, str(driver)], capture_output=True, text=True, cwd=root
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    written = report.read_text()
    assert _own_line(written, "RESULT:")


def test_bare_invocation_exits_2_with_stderr_only():
    proc = _run_cli()
    assert proc.returncode == 2
    assert proc.stderr.strip()
    assert proc.stdout == ""


def test_unknown_argument_exits_2_with_stderr_only():
    proc = _run_cli("--nope")
    assert proc.returncode == 2
    assert proc.stderr.strip()
    assert proc.stdout == ""


def test_importing_the_module_is_silent():
    """The module must do nothing on import — all of its behaviour lives behind `__main__`."""
    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            f"import sys; sys.path.insert(0, {str(MUTATE_PY.parent)!r}); import mutate",
        ],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0
    assert proc.stdout == ""
    assert proc.stderr == ""

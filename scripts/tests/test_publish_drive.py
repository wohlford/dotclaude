"""Tests for scripts/publish-drive.py — the per-brick driver for /propagate's publish path.

The driver replaces a throwaway loop that has now been hand-written three times. Every row here
names a safety property that loop carried and that a fourth re-derivation would plausibly drop —
which is not hypothetical: `scripts/lib/mutate.py`'s docstring records ten hand-rolled harnesses
where *each* re-derivation dropped a different property, the worst missing from 7 of 8 copies.

A fake engine stands in for `publish-brick.sh` so every failure path is reachable: a killed engine
that emits no verdict, a verdict naming the wrong brick, a PASS contradicted by the exit status, a
hang. Those are precisely the shapes a real run produces rarely and at the worst possible moment.
"""

from __future__ import annotations

import importlib.util
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

TOOL = Path(__file__).resolve().parent.parent / "publish-drive.py"

# Reads `<version> <outcome>` from behaviour.txt beside it and acts it out. `pass` emits the exact
# verdict the driver requires; the rest are the failure shapes that must not read as a pass.
FAKE_ENGINE = """#!/usr/bin/env bash
set -uo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
args=()
artifactdir=""
while [ $# -gt 0 ]; do
  case "$1" in
    --scope) shift 2 ;;
    --artifact-dir) artifactdir="$2"; shift 2 ;;
    *) args+=("$1"); shift ;;
  esac
done
version="${args[0]}"
printf '%s\\n' "${args[*]}" >> "$here/invocations.txt"
mode="$(grep "^$version " "$here/behaviour.txt" | awk '{print $2}')"
case "$mode" in
  pass)        printf 'RESULT: PASS rc=0 brick=%s\\n' "$version"; exit 0 ;;
  fail)        printf 'RESULT: FAIL rc=1 brick=%s\\n' "$version"; exit 1 ;;
  silent)      printf 'some output but no verdict at all\\n'; exit 0 ;;
  wrongbrick)  printf 'RESULT: PASS rc=0 brick=v9.9.9\\n'; exit 0 ;;
  lying)       printf 'RESULT: PASS rc=0 brick=%s\\n' "$version"; exit 3 ;;
  hang)        sleep 30 ;;
  # Mirrors publish-brick.sh's on_exit EXIT trap: it still emits a genuine RESULT: line for a
  # killed engine, unlike `silent`/`hang` above. This is THE shape the STATUS allowlist exists
  # to catch — a verdict was read, but INCOMPLETE means nothing was proven and no rollback ran.
  incomplete)  printf 'zz-sentinel-incomplete-emitted\\n'; printf 'RESULT: INCOMPLETE rc=143 brick=%s\\n' "$version"; exit 143 ;;
  # A status this driver has never seen at all — neither PASS, FAIL, ERROR nor INCOMPLETE.
  # Proves the gate is a real ALLOWLIST, not a two-way branch that only names INCOMPLETE.
  weirdstatus) printf 'RESULT: WEIRDSTATUS rc=9 brick=%s\\n' "$version"; exit 9 ;;
  # 20+ filler lines BEFORE the failure block, so a fixture smaller than the tail window
  # cannot hide a head-instead-of-tail bug: [-12:] and [:12] must give DIFFERENT answers.
  failrestored)
    n=1
    while [ "$n" -le 20 ]; do printf 'filler-line-%02d\\n' "$n"; n=$((n + 1)); done
    printf '  audit: %s/audit-report.txt\\n' "$artifactdir"
    printf 'FAIL zz-sentinel-audit-refused\\n'
    printf '  nothing was committed; the working tree was restored.\\n'
    printf 'RESULT: FAIL rc=1 brick=%s\\n' "$version"
    exit 1 ;;
  faillanded)
    n=1
    while [ "$n" -le 20 ]; do printf 'filler-line-%02d\\n' "$n"; n=$((n + 1)); done
    printf 'FAIL zz-sentinel-audit-refused\\n'
    printf '  the brick commit ALREADY LANDED on main. Recovery (run it yourself):\\n'
    printf '    git -C %s tag -d %s   # only if the tag was minted\\n' "$here" "$version"
    printf '    git -C %s reset --hard HEAD~1\\n' "$here"
    printf 'RESULT: FAIL rc=1 brick=%s\\n' "$version"
    exit 1 ;;
  *)           printf 'RESULT: ERROR rc=2 brick=%s\\n' "$version"; exit 2 ;;
esac
"""


@pytest.fixture
def bed(tmp_path):
    """A clean git scope, an artifact dir OUTSIDE it, and a programmable fake engine."""
    root = tmp_path.resolve()
    scope = root / "repo"
    scope.mkdir()
    subprocess.run(["git", "-C", str(scope), "init", "-q"], check=True)
    (scope / "f.txt").write_text("x\n")
    subprocess.run(["git", "-C", str(scope), "add", "-A"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(scope),
            "-c",
            "user.email=t@t.invalid",
            "-c",
            "user.name=t",
            "-c",
            "commit.gpgsign=false",
            "-c",
            "tag.gpgsign=false",
            "commit",
            "-qm",
            "init",
        ],
        check=True,
    )
    enginedir = root / "engine"
    enginedir.mkdir()
    engine = enginedir / "publish-brick.sh"
    engine.write_text(FAKE_ENGINE)
    engine.chmod(0o755)
    art = root / "artifacts"

    def plan(*rows):
        """rows are (version, endpoint, subject, *constituents)."""
        path = root / "plan.txt"
        lines = ["proposed bricks — run in order:"]
        for r in rows:
            extra = " ".join(r[3:])
            lines.append(f"  publish-brick.sh {r[0]} {r[1]} '{r[2]}' {extra}".rstrip())
        path.write_text("\n".join(lines) + "\n")
        return path

    def behaviour(**modes):
        (enginedir / "behaviour.txt").write_text(
            "\n".join(f"{v} {m}" for v, m in modes.items()) + "\n"
        )

    return root, scope, engine, art, plan, behaviour


def run(plan, scope, engine, art, *extra):
    return subprocess.run(
        [
            sys.executable,
            str(TOOL),
            "--plan",
            str(plan),
            "--scope",
            str(scope),
            "--engine",
            str(engine),
            "--artifact-dir",
            str(art),
            *extra,
        ],
        capture_output=True,
        text=True,
    )


def invocations(engine):
    log = engine.parent / "invocations.txt"
    return log.read_text().strip().split("\n") if log.exists() else []


# ---------- the property the whole driver exists for ----------


@pytest.mark.parametrize(
    "mode, why",
    [
        ("fail", "an honest failure verdict"),
        (
            "silent",
            "a KILLED engine prints a prefix of good lines and no verdict at all",
        ),
        ("wrongbrick", "a PASS naming a DIFFERENT brick is not a pass for this one"),
        ("lying", "the verdict says PASS but the engine exited non-zero"),
    ],
)
def test_nothing_after_an_unproven_brick_runs(bed, mode, why):
    """Halt on the FIRST brick that is not proven — the loop's one load-bearing property."""
    _, scope, engine, art, plan, behaviour = bed
    behaviour(**{"v0.1.0": "pass", "v0.2.0": mode, "v0.3.0": "pass"})
    p = plan(
        ("v0.1.0", "aaa", "first"),
        ("v0.2.0", "bbb", "second"),
        ("v0.3.0", "ccc", "third"),
    )
    proc = run(p, scope, engine, art)

    assert proc.returncode != 0, f"{why}: {proc.stdout}"
    assert "RESULT: PASS" not in proc.stdout.split("\n")[-2], proc.stdout
    ran = [ln.split()[0] for ln in invocations(engine)]
    assert ran == ["v0.1.0", "v0.2.0"], f"{why}: brick 3 must not have run — {ran}"
    assert "applied=1" in proc.stdout and "halted=v0.2.0" in proc.stdout, proc.stdout


def test_every_brick_runs_when_all_are_proven(bed):
    _, scope, engine, art, plan, behaviour = bed
    behaviour(**{"v0.1.0": "pass", "v0.2.0": "pass"})
    p = plan(("v0.1.0", "aaa", "first"), ("v0.2.0", "bbb", "second"))
    proc = run(p, scope, engine, art)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert proc.stdout.strip().split("\n")[-1] == (
        "RESULT: PASS rc=0 bricks=2 applied=2 halted=none"
    ), proc.stdout
    assert len(invocations(engine)) == 2


def test_a_hang_is_INDETERMINATE_not_a_failure_verdict(bed):
    """A bound that is too tight must cost attention, not manufacture a verdict nobody measured."""
    _, scope, engine, art, plan, behaviour = bed
    behaviour(**{"v0.1.0": "hang", "v0.2.0": "pass"})
    p = plan(("v0.1.0", "aaa", "first"), ("v0.2.0", "bbb", "second"))
    proc = run(p, scope, engine, art, "--timeout", "1")
    assert "INDETERMINATE" in proc.stdout, proc.stdout
    assert proc.returncode == 2, proc.stdout
    assert "RESULT: ERROR" in proc.stdout, proc.stdout
    ran = [ln.split()[0] for ln in invocations(engine)]
    assert ran == ["v0.1.0"], ran


def test_the_driver_records_its_own_exit_status_inside_the_artifact(bed):
    """An absent status is how a killed run looks; the artifact must carry the real one."""
    _, scope, engine, art, plan, behaviour = bed
    behaviour(**{"v0.1.0": "fail"})
    p = plan(("v0.1.0", "aaa", "first"))
    run(p, scope, engine, art)
    assert "DRIVER_EXIT_STATUS=1" in (art / "drive.log").read_text()


def test_a_missing_verdict_is_diagnosed_as_a_DEATH_not_as_a_wrong_verdict(bed):
    """The two point at different recoveries, so they must not collapse into one message.

    Added because a mutation campaign SURVIVED here: deleting the no-verdict branch changed
    nothing, since an empty verdict also fails the equality check below it. The halt was never
    at risk — but the diagnostic was, and "the engine died" sends you somewhere different from
    "the engine disagreed". A survivor means the code is either dead or unasserted; this one
    was unasserted.
    """
    _, scope, engine, art, plan, behaviour = bed
    behaviour(**{"v0.1.0": "silent"})
    p = plan(("v0.1.0", "aaa", "first"))
    proc = run(p, scope, engine, art)
    assert "emitted no RESULT line" in proc.stdout, proc.stdout
    assert "wanted" not in proc.stdout, proc.stdout


# ---------- the halt relays the engine's own block, and only when there is one ----------
#
# THE RULE (plans/2026-08-31-drive-halt-reproduction.md): the "+ <invocation>" line and the
# relayed engine tail travel TOGETHER, and both print if and only if the engine reached a
# verdict — i.e. drive_one read a RESULT: line. PRINT for a FAIL whose verdict was read.
# SUPPRESS for INDETERMINATE (timeout) and for the absent-verdict FAIL, where the engine was
# killed, there is no block to relay, and a bare invocation would stand uncontextualised.
#
# None of this exists yet — every row below is RED against today's driver, by design (Task 1
# is RED-only; the feature lands in Task 3).


def test_a_restored_failure_relays_the_engine_tail_and_the_invocation(bed):
    """The halt must show the engine's own FAIL block, not just the driver's own summary line.

    Uses a 2-brick plan halting on the SECOND brick, so an implementation that mistakenly
    rebuilds the invocation from bricks[0] instead of the brick that actually halted would
    fail this row. The sentinel `zz-sentinel-audit-refused` cannot appear in today's output —
    publish-drive.py already prints bare `FAIL`/`RESULT: FAIL` text elsewhere, so asserting on
    those would be green before the feature exists and would prove nothing.
    """
    _, scope, engine, art, plan, behaviour = bed
    behaviour(**{"v0.1.0": "pass", "v0.2.0": "failrestored"})
    p = plan(("v0.1.0", "aaa", "first"), ("v0.2.0", "bbb", "second"))
    proc = run(p, scope, engine, art)

    assert proc.returncode == 1, proc.stdout
    assert "zz-sentinel-audit-refused" in proc.stdout, proc.stdout
    assert f"audit: {art}" in proc.stdout, proc.stdout
    # A verdict WAS read and relayed here — the engine concluded on its own and ran its own
    # recovery. Both existing rows below assert presence only, so an implementation that prints
    # the killed sentence unconditionally, on every halt, would pass all of them.
    assert "the engine was killed" not in proc.stdout, proc.stdout

    expected_argv = [
        str(engine),
        "--scope",
        str(scope),
        "--artifact-dir",
        str(art),
        "v0.2.0",
        "bbb",
        "second",
    ]
    expected_line = "+ " + " ".join(shlex.quote(c) for c in expected_argv)
    assert expected_line in proc.stdout, proc.stdout


def test_the_relay_shows_the_TAIL_of_the_engine_output_not_the_HEAD(bed):
    """Pins tail-not-head. `failrestored` prints 20 filler lines before its 4-line failure
    block — well past a 12-line window — so `[-12:]` (correct) and `[:12]` (a plausible bug)
    give DIFFERENT answers here: a head-instead-of-tail implementation would show the first
    filler line and never reach the failure block, which is exactly the truncation failure
    this design exists to prevent.
    """
    _, scope, engine, art, plan, behaviour = bed
    behaviour(**{"v0.1.0": "pass", "v0.2.0": "failrestored"})
    p = plan(("v0.1.0", "aaa", "first"), ("v0.2.0", "bbb", "second"))
    proc = run(p, scope, engine, art)

    assert "filler-line-01" not in proc.stdout, proc.stdout
    assert "zz-sentinel-audit-refused" in proc.stdout, proc.stdout


def test_a_failure_after_landing_relays_the_ALREADY_LANDED_recovery(bed):
    """The recovery instructions for a failure that lands ON TOP of an already-committed brick
    are a different shape (nothing to roll back) and must reach the operator just the same.
    """
    _, scope, engine, art, plan, behaviour = bed
    behaviour(**{"v0.1.0": "pass", "v0.2.0": "faillanded"})
    p = plan(("v0.1.0", "aaa", "first"), ("v0.2.0", "bbb", "second"))
    proc = run(p, scope, engine, art)

    assert proc.returncode == 1, proc.stdout
    assert "ALREADY LANDED" in proc.stdout, proc.stdout


def test_an_INDETERMINATE_halt_states_the_engine_was_killed_and_suppresses_the_relay(
    bed,
):
    """Per THE RULE: no verdict was read on a timeout, so there is nothing to relay and no
    invocation to label — printing either would misrepresent an unproven state as reviewed
    engine output. The absence half is already true today (nothing is relayed at all yet); the
    statement half — that the operator is told the engine was killed — is not.
    """
    _, scope, engine, art, plan, behaviour = bed
    behaviour(**{"v0.1.0": "pass", "v0.2.0": "hang"})
    p = plan(("v0.1.0", "aaa", "first"), ("v0.2.0", "bbb", "second"))
    proc = run(p, scope, engine, art, "--timeout", "1")

    assert not any(ln.startswith("+ ") for ln in proc.stdout.split("\n")), proc.stdout
    assert "the engine was killed" in proc.stdout, proc.stdout
    assert "the tree state is UNKNOWN" in proc.stdout, proc.stdout
    assert "Read the log before doing anything." in proc.stdout, proc.stdout


def test_an_absent_verdict_halt_states_the_engine_was_killed_and_suppresses_the_relay(
    bed,
):
    """New shape revision 1 covered nowhere: a FAIL with NO verdict read at all (the engine
    died before printing one) gets the same suppression as INDETERMINATE per THE RULE — there
    is no block to relay either way, only the reason differs (a `silent` engine vs. a timeout).
    """
    _, scope, engine, art, plan, behaviour = bed
    behaviour(**{"v0.1.0": "pass", "v0.2.0": "silent"})
    p = plan(("v0.1.0", "aaa", "first"), ("v0.2.0", "bbb", "second"))
    proc = run(p, scope, engine, art)

    assert not any(ln.startswith("+ ") for ln in proc.stdout.split("\n")), proc.stdout
    assert "the engine was killed" in proc.stdout, proc.stdout
    assert "the tree state is UNKNOWN" in proc.stdout, proc.stdout
    assert "Read the log before doing anything." in proc.stdout, proc.stdout


def test_an_INCOMPLETE_verdict_from_a_killed_engine_suppresses_the_relay(bed):
    """THE BLOCKER regression test.

    `publish-brick.sh`'s `on_exit` fires from its EXIT trap and emits a REAL `RESULT:
    INCOMPLETE ...` verdict when the engine dies partway — confirmed against the real engine:
    `bash -c 'source scripts/publish-brick.sh; (phase=proving; version=vPROBE; reported=no;
    on_exit 143)'` prints exactly `RESULT: INCOMPLETE rc=143 brick=vPROBE`. So a killed engine
    (SIGTERM from any supervisor, an operator `kill`, a shell-fatal inside `main`) DOES emit a
    verdict, and the OLD gate — `if tail is not None` — only asked "was a verdict read at all".
    INCOMPLETE reads one just as well as FAIL or PASS does, so the old gate relayed a
    paste-ready invocation in precisely the state where `on_exit` did NOT call `fail_brick`:
    no rollback ran, and a commit may already be sitting on the append-only published branch.

    The fix is a STATUS allowlist (RELAY_STATUSES): FAIL and ERROR relay because the engine
    handled its own state; INCOMPLETE must not, because on_exit's non-init branch is exactly
    the "died mid-flight, nothing was handled" shape.
    """
    _, scope, engine, art, plan, behaviour = bed
    behaviour(**{"v0.1.0": "pass", "v0.2.0": "incomplete"})
    p = plan(("v0.1.0", "aaa", "first"), ("v0.2.0", "bbb", "second"))
    proc = run(p, scope, engine, art)

    assert proc.returncode != 0, proc.stdout
    assert not any(ln.startswith("+ ") for ln in proc.stdout.split("\n")), proc.stdout
    assert "----- engine stdout" not in proc.stdout, proc.stdout
    assert "zz-sentinel-incomplete-emitted" not in proc.stdout, (
        "the engine's own stdout must not be relayed for an INCOMPLETE verdict: "
        + proc.stdout
    )
    # Assert the sentence's OPENING CLAUSE *and* its warning body. Pinning the opener alone
    # is what an earlier fix got wrong: the opener names the status, but the operator
    # INSTRUCTION after it was then unpinned, so a reword could delete the whole warning
    # and leave the suite green. Not two substrings that fire off something else:
    # "INCOMPLETE" alone is already present in the pre-existing detail line printed just above
    # ("verdict was 'RESULT: INCOMPLETE rc=143 brick=v0.2.0', wanted ..."), so a status-free
    # sentence would still pass that check. This exact phrase appears ONLY when the elif
    # branch actually fires with this status — it also does not appear if that branch is
    # collapsed into the generic `else`, whose sentence never names a status at all.
    assert "the engine reported INCOMPLETE" in proc.stdout, proc.stdout
    assert "the tree state is UNKNOWN" in proc.stdout, proc.stdout
    assert "Read the log before doing anything." in proc.stdout, proc.stdout


def test_an_unrecognized_engine_status_also_suppresses_the_relay(bed):
    """The gate must be a real ALLOWLIST, not a two-way branch that special-cases only the one
    status (INCOMPLETE) this task was filed about. A status this driver has never seen at all —
    `WEIRDSTATUS`, neither PASS, FAIL, ERROR nor INCOMPLETE — must take the same safe branch.
    """
    _, scope, engine, art, plan, behaviour = bed
    behaviour(**{"v0.1.0": "pass", "v0.2.0": "weirdstatus"})
    p = plan(("v0.1.0", "aaa", "first"), ("v0.2.0", "bbb", "second"))
    proc = run(p, scope, engine, art)

    assert proc.returncode != 0, proc.stdout
    assert not any(ln.startswith("+ ") for ln in proc.stdout.split("\n")), proc.stdout
    assert "----- engine stdout" not in proc.stdout, proc.stdout
    # Same reasoning as the INCOMPLETE test above: pin the SENTENCE, which fires only from
    # the elif branch and only for THIS status — collapsing that branch into the generic
    # `else` (whose sentence never names a status) makes this go RED.
    assert "the engine reported WEIRDSTATUS" in proc.stdout, proc.stdout
    assert "the tree state is UNKNOWN" in proc.stdout, proc.stdout
    assert "Read the log before doing anything." in proc.stdout, proc.stdout


def test_an_ERROR_verdict_from_the_engine_relays_the_block_and_the_invocation(bed):
    """The fake engine's `*)` default arm — reached by no row before this one — emits a genuine
    `RESULT: ERROR rc=2 brick=<version>` verdict, the same shape `fatal()` or on_exit's
    init-phase branch produce in the real engine: a precondition refused, nothing materialised.
    ERROR sits on the allowlist beside FAIL — the engine reached ITS OWN conclusion and
    reported it — so, unlike INCOMPLETE, the halt must still relay it.

    CONTROL row: this test is green both before and after the MINOR-1/MINOR-2 fixes to the
    suppression tests above it, and that green is not vacuous — it is the only row whose engine
    reports ERROR, so it is what catches ERROR being dropped from RELAY_STATUSES. Measured:
    dropping ERROR reddens this row alone; dropping FAIL reddens five OTHER rows and not this
    one, so this row guards the ERROR member specifically, not the allowlist as a whole.
    """
    _, scope, engine, art, plan, behaviour = bed
    behaviour(**{"v0.1.0": "pass", "v0.2.0": "this-mode-does-not-exist"})
    p = plan(("v0.1.0", "aaa", "first"), ("v0.2.0", "bbb", "second"))
    proc = run(p, scope, engine, art)

    assert proc.returncode != 0, proc.stdout
    assert "RESULT: ERROR rc=2 brick=v0.2.0" in proc.stdout, proc.stdout
    assert any(ln.startswith("+ ") for ln in proc.stdout.split("\n")), proc.stdout
    assert "the engine was killed" not in proc.stdout, proc.stdout


def test_the_recovery_label_avoids_the_rejected_phrasings_CONTROL(bed):
    """CONTROL row.

    The negative half below is a THREE-PHRASE TRIPWIRE against the rejected design's own
    canonical phrasings ('re-run this' / 'run this to reproduce' / 'retry with') — it is NOT a
    guarantee of descriptiveness. A blocklist admits every wording it does not name: a
    determined implementer could still land 'paste this', 'execute:', or 'you should rerun'
    and this half would stay green throughout. It is the POSITIVE assertion on the label
    string 'the invocation that halted' that actually pins the chosen wording. The negative
    half is green before and after this change; only the positive half is RED today.
    """
    _, scope, engine, art, plan, behaviour = bed
    behaviour(**{"v0.1.0": "pass", "v0.2.0": "failrestored"})
    p = plan(("v0.1.0", "aaa", "first"), ("v0.2.0", "bbb", "second"))
    proc = run(p, scope, engine, art)

    lowered = proc.stdout.lower()
    assert "re-run this" not in lowered, proc.stdout
    assert "run this to reproduce" not in lowered, proc.stdout
    assert "retry with" not in lowered, proc.stdout
    assert "the invocation that halted" in proc.stdout, proc.stdout


def test_the_drivers_own_verdict_stays_last_even_with_a_relayed_verdict_mid_stream(bed):
    """`wrongbrick` relays a PASS-shaped RESULT: line for a DIFFERENT brick — the first time an
    engine RESULT: line appears mid-stream instead of only at the very end. The driver's own
    contract is a last-line allowlist; a reader that greps the wrong line reads a false PASS.

    Asserts an EXACT standalone line, not a substring: today's diagnostic already embeds the
    wrong-brick verdict inside a longer sentence (`verdict was 'RESULT: PASS rc=0
    brick=v9.9.9', wanted ...`), so a bare substring check on that text would be green before
    the feature exists and would prove nothing — the same collision hazard as the `FAIL`
    sentinel above, in a different shape. Only a genuine relay of the engine's raw stdout
    reproduces the engine's line VERBATIM as its own line.
    """
    _, scope, engine, art, plan, behaviour = bed
    behaviour(**{"v0.1.0": "pass", "v0.2.0": "wrongbrick"})
    p = plan(("v0.1.0", "aaa", "first"), ("v0.2.0", "bbb", "second"))
    proc = run(p, scope, engine, art)

    lines = [ln for ln in proc.stdout.split("\n") if ln.strip()]
    assert "RESULT: PASS rc=0 brick=v9.9.9" in lines, proc.stdout
    assert lines[-1].startswith("RESULT: FAIL rc=1 bricks="), lines[-1]


# ---------- the tail window is derived by MEASURING the real engine, not by parsing it ----------


def test_HALT_TAIL_LINES_is_at_least_twice_the_measured_worst_case_engine_block(
    tmp_path,
):
    """A printf count is not an emitted-line count — revision 1's own arithmetic proved that.

    It asserted "one FAIL line + 3 recovery lines + result_line = 6"; that is actually 5, and
    the 6 only came out right because it silently ALSO counted the `  audit: <artifact>` line
    at publish-brick.sh:190 — which lives in `run_audit`, a DIFFERENT function from
    `fail_brick`. A test that regex-parses `fail_brick` alone would derive 5, pass a `<= 6`
    bound with a line of silent slack, and leave the `audit:` line — the component the whole
    design's "pointer to the failing check" claim rests on — completely unguarded.

    So this row does not parse the shell source at all. It SOURCES the real
    `publish-brick.sh` in a bash subshell (safe: the file is source-guarded at `:385`, so
    sourcing defines functions without running `main`) and MEASURES what each function
    actually emits, the same way an operator would experience it.

    Declared floor: a measurement of nothing must never read as a pass. This row fails loudly
    — not silently as a 0-line "pass" — if `fail_brick`/`run_audit` are not defined after
    sourcing, or if either measured count comes back 0.
    """
    real_engine = TOOL.parent / "publish-brick.sh"
    assert real_engine.is_file(), f"real engine not found at {real_engine}"

    # ---- measure fail_brick's own emission, in the ALREADY-LANDED (committed=yes) shape ----
    fail_brick_probe = """
set -uo pipefail
source "$1" || { printf 'SOURCE_FAILED\\n' >&2; exit 97; }
declare -F fail_brick >/dev/null || { printf 'FAIL_BRICK_NOT_DEFINED\\n' >&2; exit 98; }
(
  committed=yes
  version="vMEASURE"
  scope="/nonexistent-measurement-scope"
  fail_brick "measurement probe"
)
"""
    proc = subprocess.run(
        ["bash", "-c", fail_brick_probe, "_", str(real_engine)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode != 97, f"could not source the real engine: {proc.stderr}"
    assert proc.returncode != 98, (
        f"fail_brick was not defined after sourcing: {proc.stderr}"
    )
    assert proc.returncode == 1, (proc.returncode, proc.stdout, proc.stderr)

    fail_brick_lines = [ln for ln in proc.stdout.split("\n") if ln]
    assert len(fail_brick_lines) > 0, (
        f"measured ZERO lines from fail_brick — a measurement of nothing is not a pass: "
        f"{proc.stdout!r} / stderr {proc.stderr!r}"
    )
    assert len(fail_brick_lines) == 5, (
        f"fail_brick's committed=yes block emitted {len(fail_brick_lines)} lines, expected "
        f"exactly 5 — the window's derivation below depends on this number: {proc.stdout!r}"
    )

    # ---- measure run_audit's own unconditional pre-verdict "  audit: <path>" line ----
    fake_audit = tmp_path / "fake-audit-pass.sh"
    fake_audit.write_text(
        "#!/usr/bin/env bash\nprintf 'RESULT: PASS rc=0 brick=fake\\n'\nexit 0\n"
    )
    fake_audit.chmod(0o755)
    artifact_path = tmp_path / "audit-artifact.txt"

    audit_probe = """
set -uo pipefail
source "$1" || { printf 'SOURCE_FAILED\\n' >&2; exit 97; }
declare -F run_audit >/dev/null || { printf 'RUN_AUDIT_NOT_DEFINED\\n' >&2; exit 96; }
audit_path="$2"
version="vMEASURE"
scope="/nonexistent-measurement-scope"
run_audit "$3"
"""
    proc2 = subprocess.run(
        [
            "bash",
            "-c",
            audit_probe,
            "_",
            str(real_engine),
            str(fake_audit),
            str(artifact_path),
        ],
        capture_output=True,
        text=True,
    )
    assert proc2.returncode != 97, f"could not source the real engine: {proc2.stderr}"
    assert proc2.returncode != 96, (
        f"run_audit was not defined after sourcing: {proc2.stderr}"
    )
    assert proc2.returncode == 0, (proc2.returncode, proc2.stdout, proc2.stderr)

    audit_lines = [ln for ln in proc2.stdout.split("\n") if ln.startswith("  audit: ")]
    assert len(audit_lines) > 0, (
        f"measured ZERO '  audit: <path>' lines — a measurement of nothing is not a pass: "
        f"{proc2.stdout!r}"
    )
    assert len(audit_lines) == 1, (
        f"expected exactly one '  audit: <path>' line, got {len(audit_lines)}: {proc2.stdout!r}"
    )

    worst_case_block = len(fail_brick_lines) + len(audit_lines)
    assert worst_case_block == 6, worst_case_block

    # ---- assert the driver's own constant against the measured worst case ----
    # `import publish_drive` cannot work — the filename is dashed. Copy the pattern at
    # scripts/publish-rehearse.py:91-105.
    spec = importlib.util.spec_from_file_location("publish_drive_measure", TOOL)
    assert spec is not None and spec.loader is not None, f"cannot load {TOOL}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    halt_tail_lines = module.HALT_TAIL_LINES
    assert halt_tail_lines >= 2 * worst_case_block, (halt_tail_lines, worst_case_block)


def test_the_relayed_block_shows_HALT_TAIL_LINES_worth_of_engine_lines_not_one_fewer(
    bed,
):
    """The header claims `last {HALT_TAIL_LINES} lines`; `split("\n")` on newline-terminated
    stdout ends in a trailing empty element that, left in, silently occupies one slot of the
    `[-HALT_TAIL_LINES:]` window — so only 11 real engine lines ever reached the operator, not
    12, understating the delivered margin (2x the measured worst case) to 1.83x.

    The row above pins a property of the CONSTANT (it is >= 2x a measured worst case); it says
    nothing about how many of the engine's OWN lines actually land in the block the operator
    reads. This fixture emits EXACTLY `HALT_TAIL_LINES` lines of real engine stdout and counts
    how many of them land inside the relayed block, closing that gap.
    """
    _, scope, engine, art, plan, behaviour = bed

    spec = importlib.util.spec_from_file_location("publish_drive_measure3", TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    n = module.HALT_TAIL_LINES

    # n-1 filler lines + 1 terminal RESULT: line = exactly n lines of engine stdout.
    filler = "\n".join(f"printf 'exact-filler-{i:02d}\\n'" for i in range(1, n))
    engine.write_text(
        "#!/usr/bin/env bash\n"
        f"{filler}\n"
        "printf 'RESULT: FAIL rc=1 brick=v0.1.0\\n'\n"
        "exit 1\n"
    )
    engine.chmod(0o755)

    p = plan(("v0.1.0", "aaa", "first"))
    proc = run(p, scope, engine, art)

    header = f"----- engine stdout (last {n} lines) -----\n"
    assert header in proc.stdout, proc.stdout
    block = proc.stdout.split(header, 1)[1].split("\n-----\n", 1)[0]
    engine_lines = [ln for ln in block.split("\n") if ln]
    assert len(engine_lines) == n, (
        f"the fixture emitted exactly {n} engine lines; only {len(engine_lines)} reached the "
        f"relayed block: {engine_lines!r}"
    )
    assert engine_lines[0] == "exact-filler-01", (
        "the FIRST of the n emitted lines must survive — losing it is the off-by-one this row "
        f"exists to catch: {engine_lines!r}"
    )


# ---------- a timeout must not discard what the engine already printed ----------


# ---------- preconditions: refuse rather than half-run ----------


def test_an_artifact_dir_inside_the_scope_is_refused(bed):
    """An untracked file in the repo fails the NEXT brick's clean-tree precondition."""
    _, scope, engine, _, plan, behaviour = bed
    behaviour(**{"v0.1.0": "pass"})
    p = plan(("v0.1.0", "aaa", "first"))
    proc = run(p, scope, engine, scope / "logs")
    assert proc.returncode == 2, proc.stdout
    assert "RESULT: ERROR rc=2" in proc.stdout, proc.stdout
    assert invocations(engine) == [], "no brick may run"


def test_a_dirty_tree_is_refused_before_any_brick_runs(bed):
    _, scope, engine, art, plan, behaviour = bed
    behaviour(**{"v0.1.0": "pass"})
    (scope / "untracked.txt").write_text("dirty\n")
    p = plan(("v0.1.0", "aaa", "first"))
    proc = run(p, scope, engine, art)
    assert proc.returncode == 2, proc.stdout
    assert invocations(engine) == []


def test_a_plan_with_no_bricks_is_an_ERROR_not_a_clean_run(bed, tmp_path):
    """Zero is the loudest false pass: driving nothing must never report success."""
    _, scope, engine, art, _, behaviour = bed
    behaviour(**{"v0.1.0": "pass"})
    empty = tmp_path / "empty.txt"
    empty.write_text("range: aaa..dev (0 commits)\nno bricks here\n")
    proc = run(empty, scope, engine, art)
    assert proc.returncode == 2, proc.stdout
    assert "RESULT: ERROR rc=2" in proc.stdout, proc.stdout


def test_a_malformed_brick_line_is_an_ERROR_not_a_silent_skip(bed, tmp_path):
    """A quietly discarded line reads exactly like a shorter plan — and publishes less."""
    _, scope, engine, art, _, behaviour = bed
    behaviour(**{"v0.1.0": "pass"})
    bad = tmp_path / "bad.txt"
    bad.write_text("  publish-brick.sh v0.1.0 aaa 'ok'\n  publish-brick.sh v0.2.0\n")
    proc = run(bad, scope, engine, art)
    assert proc.returncode == 2, proc.stdout
    assert "RESULT: ERROR rc=2" in proc.stdout, proc.stdout
    assert invocations(engine) == [], (
        "nothing may run from a plan that did not fully parse"
    )


# ---------- the plan is parsed as a shell would, without executing it ----------


def test_a_subject_with_spaces_and_an_apostrophe_survives_intact(bed):
    """The engine must receive the subject byte-for-byte; this is what `eval` would risk."""
    _, scope, engine, art, plan, behaviour = bed
    behaviour(**{"v0.1.0": "pass"})
    # The planner emits an embedded apostrophe as '\'' — the shell's own escaping.
    p = plan(("v0.1.0", "aaa", "fix(x): don'\\''t drop the guard"))
    proc = run(p, scope, engine, art)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "fix(x): don't drop the guard" in invocations(engine)[0], invocations(engine)


def test_folded_constituents_are_passed_through_after_the_subject(bed):
    _, scope, engine, art, plan, behaviour = bed
    behaviour(**{"v0.1.0": "pass"})
    p = plan(("v0.1.0", "endp", "feat: thing", "c111111", "c222222"))
    run(p, scope, engine, art)
    assert invocations(engine)[0] == "v0.1.0 endp feat: thing c111111 c222222"


# ---------- resume is explicit and never silent ----------


def test_from_skips_earlier_bricks_and_says_how_many(bed):
    _, scope, engine, art, plan, behaviour = bed
    behaviour(**{"v0.1.0": "pass", "v0.2.0": "pass", "v0.3.0": "pass"})
    p = plan(("v0.1.0", "aaa", "a"), ("v0.2.0", "bbb", "b"), ("v0.3.0", "ccc", "c"))
    proc = run(p, scope, engine, art, "--from", "v0.2.0")
    assert proc.returncode == 0, proc.stdout
    assert "SKIPPING 2 earlier brick(s)" not in proc.stdout, proc.stdout
    assert "SKIPPING 1 earlier brick(s)" in proc.stdout, proc.stdout
    assert [ln.split()[0] for ln in invocations(engine)] == ["v0.2.0", "v0.3.0"]


def test_from_naming_no_brick_in_the_plan_is_an_ERROR(bed):
    _, scope, engine, art, plan, behaviour = bed
    behaviour(**{"v0.1.0": "pass"})
    p = plan(("v0.1.0", "aaa", "a"))
    proc = run(p, scope, engine, art, "--from", "v9.9.9")
    assert proc.returncode == 2, proc.stdout
    assert invocations(engine) == []


def test_dry_run_works_on_a_dirty_tree(bed):
    """A dry run invokes nothing, so the ENGINE's clean-tree precondition does not apply to it.

    Found by running the documented flow rather than by review: the moment you want to check
    that a plan parses is *before* you clean the tree, so refusing there makes the dry run
    useless at exactly the point it is reached for.
    """
    _, scope, engine, art, plan, behaviour = bed
    behaviour(**{"v0.1.0": "pass"})
    (scope / "untracked.txt").write_text("dirty\n")
    p = plan(("v0.1.0", "aaa", "a"))
    proc = run(p, scope, engine, art, "--dry-run")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert invocations(engine) == []


def test_dry_run_still_refuses_an_artifact_dir_inside_the_scope(bed):
    """Relaxing the tree check must not relax the one that catches a misconfiguration."""
    _, scope, engine, _, plan, behaviour = bed
    behaviour(**{"v0.1.0": "pass"})
    p = plan(("v0.1.0", "aaa", "a"))
    proc = run(p, scope, engine, scope / "logs", "--dry-run")
    assert proc.returncode == 2, proc.stdout


def test_dry_run_invokes_nothing(bed):
    _, scope, engine, art, plan, behaviour = bed
    behaviour(**{"v0.1.0": "pass"})
    p = plan(("v0.1.0", "aaa", "a"))
    proc = run(p, scope, engine, art, "--dry-run")
    assert proc.returncode == 0, proc.stdout
    assert invocations(engine) == []
    assert "applied=0" in proc.stdout


def test_it_never_pushes_and_never_moves_the_watermark(bed):
    """The driver applies; publishing stays foreground and human, after this step."""
    _, scope, engine, art, plan, behaviour = bed
    behaviour(**{"v0.1.0": "pass", "v0.2.0": "pass"})
    p = plan(("v0.1.0", "aaa", "a"), ("v0.2.0", "bbb", "b"))
    run(p, scope, engine, art)
    src = TOOL.read_text()
    assert '"push"' not in src and "'push'" not in src, "the driver must never push"
    assert "update-ref" not in src, "the driver must never move the watermark"

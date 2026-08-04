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
while [ $# -gt 0 ]; do
  case "$1" in
    --scope|--artifact-dir) shift 2 ;;
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

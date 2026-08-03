"""Tests for scripts/settings-hooks-check.py — the one-directional hook-registration check.

The defect it exists for: `/propagate`'s skip-worktree dance parks the runtime `settings.json`,
fast-forwards, then restores the parked copy — which puts back a file that predates the incoming
commit and therefore lacks whatever hooks that commit registered. Measured once at 21 runtime
entries against 23 committed: three newly built, fully-tested hooks would have deployed and never
fired. `git diff FETCH_HEAD -- settings.json` reports CLEAN throughout, because skip-worktree
makes git assume worktree == index for that path.

The assertion is deliberately ONE-DIRECTIONAL — *no committed registration is absent from the
runtime* — never an equality. Two reasons, both measured on this machine:

  * The runtime legitimately carries machine-local hooks the repo does not track, so an equality
    check false-alarms on every promote.
  * Counts are not sets. A promote was measured where runtime and commit BOTH held 24 entries
    while differing in both directions at once — a machine-local extra present, a committed gate
    missing. Every count-based instrument passes there while a dead gate ships. `test_count_trap`
    is that exact case.

Identity is the triple `(event, matcher, command)`, not the command alone: the same script
registered under a different matcher or a different event is a different registration, and fires
on different things.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

TOOL = Path(__file__).resolve().parent.parent / "settings-hooks-check.py"
SETTINGS = "settings.json"


def porcelain(repo):
    return subprocess.run(
        ["git", "-C", str(repo), "status", "--porcelain"],
        capture_output=True,
        text=True,
    ).stdout.strip()


def hooks(*triples):
    """Build a settings.json dict from (event, matcher, command) triples."""
    out: dict = {}
    for event, matcher, command in triples:
        groups = out.setdefault(event, [])
        for g in groups:
            if g["matcher"] == matcher:
                g["hooks"].append({"type": "command", "command": command})
                break
        else:
            groups.append(
                {"matcher": matcher, "hooks": [{"type": "command", "command": command}]}
            )
    return {"hooks": out}


def git(sandbox, *args):
    subprocess.run(
        ["git", "-C", str(sandbox), *args],
        check=True,
        capture_output=True,
        text=True,
    )


@pytest.fixture
def sandbox(tmp_path):
    # pwd -P equivalent: on macOS $TMPDIR is reached through a symlink, and a logical path that
    # does not physically contain the file can send git down a different resolution path.
    root = Path(tmp_path).resolve()
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "t@example.invalid")
    git(root, "config", "user.name", "T")
    git(root, "config", "commit.gpgsign", "false")
    return root


def commit_settings(sandbox, payload):
    (sandbox / "settings.json").write_text(json.dumps(payload, indent=2))
    git(sandbox, "add", "settings.json")
    git(sandbox, "commit", "-q", "-m", "settings")


def set_runtime(sandbox, payload):
    """Overwrite the WORKING copy only — the committed side is untouched, exactly as the
    skip-worktree runtime file diverges from its committed twin in production."""
    (sandbox / "settings.json").write_text(json.dumps(payload, indent=2))


def run(sandbox, *extra):
    proc = subprocess.run(
        [sys.executable, str(TOOL), "--scope", str(sandbox), *extra],
        capture_output=True,
        text=True,
    )
    return proc


def verdict(proc):
    """The last non-empty stdout line. Its ABSENCE is itself a failure — a tool that died
    partway prints a plausible prefix and no verdict."""
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    assert lines, f"no stdout at all; stderr={proc.stderr!r}"
    last = lines[-1]
    assert last.startswith("RESULT: "), f"last line is not a verdict: {last!r}"
    return last


A = ("PreToolUse", "Bash", "guard-a.sh")
B = ("PostToolUse", "Edit|Write", "check-b.sh")
C = ("PreToolUse", "Bash", "guard-c.sh")
LOCAL = ("PreToolUse", "Bash", "machine-local.sh")


def test_identical_passes(sandbox):
    commit_settings(sandbox, hooks(A, B))
    proc = run(sandbox)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "RESULT: PASS" in verdict(proc)
    assert "missing=0" in verdict(proc)


def test_machine_local_extra_is_not_a_failure(sandbox):
    """The whole reason the check is one-directional. An equality check fails here."""
    commit_settings(sandbox, hooks(A, B))
    set_runtime(sandbox, hooks(A, B, LOCAL))
    proc = run(sandbox)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "RESULT: PASS" in verdict(proc)
    assert "extra=1" in verdict(proc)


def test_dropped_committed_hook_fails(sandbox):
    """The dead-gate defect: the restore put back a runtime file predating the commit."""
    commit_settings(sandbox, hooks(A, B, C))
    set_runtime(sandbox, hooks(A, B))
    proc = run(sandbox)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "RESULT: FAIL" in verdict(proc)
    assert "missing=1" in verdict(proc)
    assert "guard-c.sh" in proc.stdout


def test_count_trap(sandbox):
    """Equal counts, differing in BOTH directions — measured for real on a promote.

    Runtime and commit each hold 3 registrations. A tally reports agreement; a dead gate ships.
    """
    commit_settings(sandbox, hooks(A, B, C))
    set_runtime(sandbox, hooks(A, B, LOCAL))
    proc = run(sandbox)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "RESULT: FAIL" in verdict(proc)
    assert "missing=1" in verdict(proc)
    assert "guard-c.sh" in proc.stdout


def test_same_command_different_matcher_is_a_different_registration(sandbox):
    commit_settings(sandbox, hooks(("PreToolUse", "Bash", "g.sh")))
    set_runtime(sandbox, hooks(("PreToolUse", "Read|Edit", "g.sh")))
    proc = run(sandbox)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "RESULT: FAIL" in verdict(proc)


def test_same_command_different_event_is_a_different_registration(sandbox):
    commit_settings(sandbox, hooks(("PreToolUse", "Bash", "g.sh")))
    set_runtime(sandbox, hooks(("PostToolUse", "Bash", "g.sh")))
    proc = run(sandbox)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "RESULT: FAIL" in verdict(proc)


def test_zero_committed_registrations_is_an_error_not_a_pass(sandbox):
    """A discovery matching NOTHING reports success loudest of all. Assert the denominator."""
    commit_settings(sandbox, {"hooks": {}})
    proc = run(sandbox)
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "RESULT: ERROR" in verdict(proc)


def test_malformed_runtime_is_an_error_not_a_pass(sandbox):
    commit_settings(sandbox, hooks(A, B))
    (sandbox / "settings.json").write_text("{ this is not json")
    proc = run(sandbox)
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "RESULT: ERROR" in verdict(proc)


def test_missing_runtime_file_is_an_error(sandbox):
    commit_settings(sandbox, hooks(A, B))
    (sandbox / "settings.json").unlink()
    proc = run(sandbox)
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "RESULT: ERROR" in verdict(proc)


# Structurally valid JSON whose hooks block cannot be read as registrations. Each of these is a
# shape the flattener must REFUSE rather than skip: a block it silently treats as empty shrinks
# the expected set, and a shrunken expected set is a PASS that proves nothing. Added after a
# mutation survived — replacing one such raise with `continue` left the whole suite green.
MALFORMED = [
    pytest.param({"hooks": "nope"}, id="hooks-not-an-object"),
    pytest.param({"hooks": {"PreToolUse": "nope"}}, id="event-not-a-list"),
    pytest.param({"hooks": {"PreToolUse": ["nope"]}}, id="group-not-an-object"),
    pytest.param(
        {"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": "nope"}]}},
        id="entries-not-a-list",
    ),
    pytest.param(
        {
            "hooks": {
                "PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command"}]}]
            }
        },
        id="entry-without-a-command",
    ),
]


@pytest.mark.parametrize("payload", MALFORMED)
def test_malformed_committed_hooks_block_is_an_error(sandbox, payload):
    """The dangerous side: silently skipping shrinks what we require of the runtime."""
    commit_settings(sandbox, payload)
    set_runtime(sandbox, hooks(A, B))
    proc = run(sandbox)
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "RESULT: ERROR" in verdict(proc)


@pytest.mark.parametrize("payload", MALFORMED)
def test_malformed_runtime_hooks_block_is_an_error(sandbox, payload):
    """The other side fails closed too — an unreadable runtime file is not a verified one."""
    commit_settings(sandbox, hooks(A, B))
    set_runtime(sandbox, payload)
    proc = run(sandbox)
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "RESULT: ERROR" in verdict(proc)


def test_ref_without_settings_json_is_an_error(sandbox):
    commit_settings(sandbox, hooks(A, B))
    git(sandbox, "rm", "-q", "settings.json")
    git(sandbox, "commit", "-q", "-m", "drop")
    set_runtime(sandbox, hooks(A, B))
    proc = run(sandbox)
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "RESULT: ERROR" in verdict(proc)


def test_explicit_ref_is_honoured(sandbox):
    """After a promote the operator may want to compare against FETCH_HEAD rather than HEAD."""
    commit_settings(sandbox, hooks(A, B))
    first = subprocess.run(
        ["git", "-C", str(sandbox), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    commit_settings(sandbox, hooks(A, B, C))
    set_runtime(sandbox, hooks(A, B))
    # Against HEAD, C is missing.
    assert run(sandbox).returncode == 1
    # Against the earlier commit, it is not.
    assert run(sandbox, "--ref", first).returncode == 0


def test_the_actual_propagate_restore_drops_a_hook_and_the_check_catches_it(tmp_path):
    """End-to-end rehearsal of the defect, through the REAL park/restore sequence.

    Every row above builds the divergence by hand, which proves the comparison but assumes the
    mechanism. This one runs `/propagate` step 5's documented commands verbatim against a
    skip-worktree runtime file and lets them produce the dead gate on their own.

    That matters here more than usual: the restore path has now gone SIX consecutive promotes
    without executing once, because no incoming range happened to touch settings.json. A check
    for a path nobody has watched fire is a check nobody has tested, so this forces the firing.
    """
    root = Path(tmp_path).resolve()
    dev = root / "dev"
    dev.mkdir()
    git(dev, "init", "-q", "-b", "dev")
    git(dev, "config", "user.email", "t@example.invalid")
    git(dev, "config", "user.name", "T")
    git(dev, "config", "commit.gpgsign", "false")
    commit_settings(dev, hooks(A, B))

    # Production is a clone that tracks dev, exactly as the dogfood repo does.
    live = root / "live"
    subprocess.run(
        ["git", "clone", "-q", "--branch", "dev", str(dev), str(live)],
        check=True,
        capture_output=True,
    )
    git(live, "config", "user.email", "t@example.invalid")
    git(live, "config", "user.name", "T")
    git(live, "config", "commit.gpgsign", "false")

    # The runtime file carries a machine-local hook and is marked skip-worktree, so git assumes
    # worktree == index for it — the property that makes a plain diff probe useless.
    set_runtime(live, hooks(A, B, LOCAL))
    git(live, "update-index", "--skip-worktree", SETTINGS)
    assert porcelain(live) == "", "skip-worktree should hide the runtime modification"

    # dev registers a NEW hook.
    commit_settings(dev, hooks(A, B, C))

    # --- /propagate step 5, verbatim ---
    git(live, "fetch", str(dev), "dev")
    blocked = subprocess.run(
        ["git", "-C", str(live), "merge", "--ff-only", "FETCH_HEAD"],
        capture_output=True,
        text=True,
    )
    assert blocked.returncode != 0, (
        "expected the runtime settings.json to block the merge"
    )
    assert SETTINGS in (blocked.stdout + blocked.stderr)

    git(live, "update-index", "--no-skip-worktree", SETTINGS)
    git(live, "stash", "push", "-m", "runtime settings.json", "--", SETTINGS)
    git(live, "merge", "--ff-only", "FETCH_HEAD")
    git(live, "checkout", "stash@{0}", "--", SETTINGS)
    git(live, "stash", "drop")
    git(live, "reset", "-q", "HEAD", "--", SETTINGS)
    git(live, "update-index", "--skip-worktree", SETTINGS)
    # --- end of the documented sequence ---

    # The restore put back a file predating the incoming commit. Nothing above errored, the tree
    # reports clean, and hook C is now deployed-but-never-firing.
    assert porcelain(live) == "", (
        "the tree reads clean — which is exactly why this needs a check"
    )

    proc = run(live)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "RESULT: FAIL" in verdict(proc)
    assert "missing=1" in verdict(proc)
    assert "guard-c.sh" in proc.stdout
    # ...and the machine-local hook survived the restore, so it must not be reported as missing.
    assert "machine-local.sh" in proc.stdout
    assert "extra=1" in verdict(proc)


def test_non_repo_scope_is_an_error(tmp_path):
    proc = subprocess.run(
        [sys.executable, str(TOOL), "--scope", str(tmp_path)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 2, proc.stdout + proc.stderr
    # Demand the verdict LINE, not just the status. A missing interpreter target also exits 2,
    # so rc alone made this row pass while the tool did not exist — caught on the RED run.
    assert "RESULT: ERROR" in verdict(proc)

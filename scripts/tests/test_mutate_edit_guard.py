"""Tests for scripts/mutate-edit-guard.py — the PreToolUse refusal of an edit to a campaign-owned file.

`scripts/lib/mutate.py` restores its pre-run snapshot unconditionally when a campaign ends, so an
edit made to the subject meanwhile is overwritten while the report says `restored: sha256
unchanged`. The sidecar `<subject>.mutate-backup` exists exactly while a campaign may still own the
file; this hook turns that into a refusal at edit time.

Driven by BARE PATH throughout, so a hook committed without its exec bit fails every row — the
harness invokes it that way. The live and stranded rows use a REAL campaign in its own process,
because the sidecar's lifetime is mutate.py's to decide and a hand-made sidecar only proves the
hook reads a file.
"""

from __future__ import annotations

import importlib.util
import io
import json
import os
import shlex
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent.parent
HOOK = REPO / "scripts" / "mutate-edit-guard.py"
LIB = REPO / "scripts" / "lib"
sys.path.insert(0, str(LIB))
import mutate  # noqa: E402

_spec = importlib.util.spec_from_file_location("mutate_edit_guard", HOOK)
guard = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(guard)

ORIGINAL = "GUARD = True\n"


def drive(payload=None, raw=None, env=None, cwd=None):
    data = raw if raw is not None else json.dumps(payload)
    proc = subprocess.run(
        [str(HOOK)],
        input=data,
        capture_output=True,
        text=True,
        timeout=30,
        env={**os.environ, **(env or {})},
        cwd=cwd,
    )
    return proc.returncode, proc.stdout, proc.stderr


def edit(path, tool="Edit", key="file_path", **extra):
    return {"tool_name": tool, "tool_input": {key: str(path)}, **extra}


@pytest.fixture
def root(tmp_path):
    # Resolved: macOS hands out /var/..., a symlink to /private/var/... — see test_mutate.py's bed.
    # A space in the name so every printed recovery command is exercised against quoting.
    d = tmp_path.resolve() / "has space"
    d.mkdir()
    return d


@pytest.fixture
def owned(root):
    subject = root / "subject.py"
    subject.write_text(ORIGINAL)
    sidecar = mutate.backup_path(subject)
    sidecar.write_text(ORIGINAL)
    return subject, sidecar


# ---------- allow ----------


def test_allows_a_file_no_campaign_owns(root):
    f = root / "free.py"
    f.write_text(ORIGINAL)
    rc, out, err = drive(edit(f))
    assert (rc, out, err) == (0, "", "")


# ---------- deny ----------


@pytest.mark.parametrize("tool", ["Edit", "Write", "MultiEdit"])
def test_refuses_an_owned_file(owned, tool):
    subject, sidecar = owned
    rc, out, err = drive(edit(subject, tool=tool))
    assert rc == 2, err
    assert out == "", "a deny must write stderr only"
    assert "BLOCKED" in err and tool in err
    assert str(sidecar) in err and str(subject) in err


def test_refuses_a_notebook_edit(root):
    nb = root / "n.ipynb"
    nb.write_text("{}")
    mutate.backup_path(nb).write_text("{}")
    rc, _, err = drive(edit(nb, tool="NotebookEdit", key="notebook_path"))
    assert rc == 2, err


def test_refuses_a_write_recreating_a_subject_that_vanished(owned):
    subject, _ = owned
    subject.unlink()
    rc, _, err = drive(edit(subject, tool="Write"))
    assert rc == 2, err


def test_a_relative_path_is_judged_against_the_payload_cwd(owned, root):
    rc, _, err = drive(edit("subject.py", cwd=str(root)))
    assert rc == 2, err


def test_a_relative_path_with_no_cwd_is_allowed(owned, root):
    subject, _ = owned
    rc, _, err = drive(edit("subject.py"), cwd=str(root))
    assert rc == 0, err


def test_a_tilde_path_is_expanded_not_joined_to_cwd(owned, root):
    rc, _, err = drive(edit("~/subject.py", cwd="/"), env={"HOME": str(root)})
    assert rc == 2, err


def test_a_legacy_sidecar_beside_a_link_is_still_found(root):
    """An older mutate.py (an old session, production before promote) parked it by the link."""
    real = root / "real.py"
    real.write_text(ORIGINAL)
    link = root / "link.py"
    link.symlink_to(real)
    (root / ("link.py" + guard.BACKUP_SUFFIX)).write_text(ORIGINAL)
    rc, _, err = drive(edit(link))
    assert rc == 2, err


def test_an_unwritable_stderr_still_blocks(owned, monkeypatch):
    subject, _ = owned

    class Broken:
        def write(self, _text):
            raise BrokenPipeError("reader closed")

    class Stdin(io.StringIO):
        def isatty(self):
            return False

    monkeypatch.setattr(guard.sys, "stderr", Broken())
    stdin = Stdin(json.dumps(edit(subject)))
    assert guard.main(["mutate-edit-guard.py"], stdin) == 2


def test_a_closed_stderr_still_blocks(owned, monkeypatch):
    subject, _ = owned

    class Stdin(io.StringIO):
        def isatty(self):
            return False

    monkeypatch.setattr(guard.sys, "stderr", None)
    stdin = Stdin(json.dumps(edit(subject)))
    assert guard.main(["mutate-edit-guard.py"], stdin) == 2


def test_a_closed_stderr_still_blocks_in_a_subprocess(owned):
    subject, _ = owned
    proc = subprocess.run(
        ["bash", "-c", 'exec 2>&-; exec "$0"', str(HOOK)],
        input=json.dumps(edit(subject)),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 2


def test_a_broken_pipe_stderr_still_blocks_in_a_subprocess(owned):
    subject, _ = owned
    r, w = os.pipe()
    os.close(r)
    try:
        proc = subprocess.run(
            [str(HOOK)],
            input=json.dumps(edit(subject)),
            stderr=w,
            stdout=subprocess.DEVNULL,
            text=True,
            timeout=30,
        )
    finally:
        os.close(w)
    assert proc.returncode == 2


# ---------- parity with mutate.py: one predicate, spelled twice on purpose ----------


def test_the_suffix_matches_mutate():
    assert guard.BACKUP_SUFFIX == mutate.BACKUP_SUFFIX


def test_every_campaign_spelling_is_caught_from_every_edit_spelling(root):
    """3x3: the campaign names the subject one way, the editor another; each pair must meet."""
    real = root / "subject.py"
    real.write_text(ORIGINAL)
    filelink = root / "filelink.py"
    filelink.symlink_to(real)
    dirlink = root.parent / "dirlink"
    dirlink.symlink_to(root, target_is_directory=True)
    spellings = [real, filelink, dirlink / "subject.py"]
    for campaign in spellings:
        for editor in spellings:
            assert str(mutate.backup_path(campaign)) in guard.sidecar_candidates(
                str(editor)
            ), f"campaign via {campaign} not seen from an edit via {editor}"


# ---------- fail open ----------


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "not json",
        "[]",
        '{"tool_input": "x"}',
        '{"tool_input": {"file_path": 7}}',
        '{"tool_input": {}}',
    ],
)
def test_anything_unjudgeable_is_allowed(raw):
    rc, _, _ = drive(raw=raw)
    assert rc == 0


def test_a_CLOSED_stdin_is_allowed_without_a_traceback():
    proc = subprocess.run(
        ["bash", "-c", 'exec 0<&-; exec "$0"', str(HOOK)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    assert "Traceback" not in proc.stderr


def test_argv_is_refused():
    proc = subprocess.run(
        [str(HOOK), "some/file.py"],
        input="",
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 2
    assert "stdin" in proc.stderr.lower() and "HOOKS.md" in proc.stderr


# ---------- a REAL campaign: live, then stranded ----------

HANG_ON_MUTANT = """#!/usr/bin/env bash
if grep -q 'GUARD = True' "$1"; then printf 'PASS  intact\\n'; exit 0; fi
sleep 30
"""


@pytest.fixture
def campaign(root):
    """Start mutate.run in its own process; return once a mutant is on disk.

    Leaks a BOUNDED orphan, exactly as test_mutate.py's `killable` does: mutate.run starts the
    suite with start_new_session, so neither proc.kill() nor killpg on the driver reaches the
    hanging `sleep 30`; it exits on its own within 30 s and matches no
    `pgrep -fl '(^|[ /])mutate_[a-z0-9_]+[.]py'` (the refusal's own current hint).
    """
    subject = root / "subject.py"
    subject.write_text(ORIGINAL)
    suite = root / "hang.sh"
    suite.write_text(HANG_ON_MUTANT)
    suite.chmod(0o755)
    driver = root / "driver.py"
    driver.write_text(
        f"import sys\nsys.path.insert(0, {str(LIB)!r})\nimport mutate\n"
        f"mutate.run({str(subject)!r}, ['bash', {str(suite)!r}, {str(subject)!r}],\n"
        "           [mutate.Mutation('drop', 'GUARD = True', 'GUARD = False')])\n"
    )
    proc = subprocess.Popen(
        [sys.executable, str(driver)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    deadline = time.time() + 30
    while "GUARD = False" not in subject.read_text():
        if proc.poll() is not None or time.time() > deadline:
            proc.kill()
            proc.wait()
            raise AssertionError("precondition: no mutant ever reached disk")
        time.sleep(0.02)
    yield subject, proc
    if proc.poll() is None:
        proc.kill()
        proc.wait()


def _recovery_lines(err):
    return [
        ln.split("#")[0].strip()
        for ln in err.splitlines()
        if ln.strip().startswith(("cmp -s ", "cp "))
    ]


def test_a_RUNNING_campaign_blocks_the_edit_and_releases_it_after(campaign):
    subject, proc = campaign
    rc, _, err = drive(edit(subject))
    assert rc == 2, "a live campaign's subject was editable"
    proc.send_signal(signal.SIGTERM)  # mutate.py's handler restores, then dies
    proc.wait(timeout=30)
    assert subject.read_text() == ORIGINAL
    rc, _, err = drive(edit(subject))
    assert rc == 0, f"still blocked after a verified restore: {err}"


def test_a_stranded_MUTATED_subject_is_recovered_by_the_printed_commands(campaign):
    subject, proc = campaign
    proc.kill()  # SIGKILL: no handler, sidecar and mutant both stranded
    proc.wait()
    assert subject.read_text() != ORIGINAL, "precondition: the mutant is stranded"
    rc, _, err = drive(edit(subject))
    assert rc == 2
    lines = _recovery_lines(err)
    assert len(lines) == 2, err
    # AS PRINTED, in order: cmp differs so its rm is skipped; cp restores
    for line in lines:
        subprocess.run(["bash", "-c", line], cwd="/", timeout=30)
    assert subject.read_text() == ORIGINAL
    assert drive(edit(subject))[0] == 0


def test_a_stranded_UNDAMAGED_sidecar_is_cleared_by_the_printed_cmp_line(owned):
    subject, sidecar = owned
    rc, _, err = drive(edit(subject))
    assert rc == 2
    cmp_line = _recovery_lines(err)[0]
    assert cmp_line.startswith("cmp -s ")
    subprocess.run(["bash", "-c", cmp_line], cwd="/", timeout=30)
    assert not sidecar.exists()
    assert subject.read_text() == ORIGINAL
    assert drive(edit(subject))[0] == 0


def test_printed_paths_are_shell_quoted(owned):
    subject, sidecar = owned
    _, _, err = drive(edit(subject))
    assert shlex.quote(str(sidecar)) in err

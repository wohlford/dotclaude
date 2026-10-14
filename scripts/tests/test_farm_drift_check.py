"""Tests for scripts/farm-drift-check.py — the warn-only SessionStart farm-drift hook.

Every row drives the SHIPPED script as a real process, because the contract is the process exit
code (an in-process call of `main()` never reaches interpreter shutdown, where a broken pipe turns
an exit 0 into 120). Rows that run the real `install.sh` build a true farm in a throwaway tree:
`HOME/.claude` is a symlink to a physically separate target, the live farm's topology (install.sh's
self-install guard needs it). The hook resolves `$HOME/.claude` with realpath, which no row can
distinguish from not resolving it: `isfile` and `lstat` follow the intermediate link, so that
resolution is defensive and unpinned. The real HOME and the production clone are never read.

Rows that need `install.sh` to misbehave swap `prod/install.sh` for a stub. Rows that run the real
one export FARM_DRIFT_DEADLINE_SECS=60, so a slow `--check` can never print "did not finish" and
fail a row for a reason unrelated to the subject. Only the two deadline rows
(`test_deadline_is_honoured_and_reaches_grandchildren` and `test_sigkill_ends_a_child_that_ignores_term`)
exercise a real timeout, with a 1 s override. The five invalid-override rows also run without the
60 s override, which is fine: their stub prints its verdict at once, so no deadline can expire.
"""

from __future__ import annotations

import getpass
import importlib.util
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent.parent
HOOK_SRC = REPO / "scripts" / "farm-drift-check.py"
INSTALL_SRC = REPO / "install.sh"
SETTINGS = REPO / "settings.json"
PREFIX = "$HOME/.claude/scripts/"
HOOK_NAME = "farm-drift-check.py"

sys.path.insert(0, str(REPO / "scripts" / "lib"))
import settings_hooks  # noqa: E402

SESSION = json.dumps(
    {"hook_event_name": "SessionStart", "source": "startup", "session_id": "t"}
).encode()
INERT = b'{"tool_input":{"file_path":"/nonexistent/does-not-exist.zzz"}}'
SLOW_OK = {"FARM_DRIFT_DEADLINE_SECS": "60"}
DIRS = ("agents", "skills", "scripts")


def _floor() -> list:
    """The FLOOR names, derived from install.sh itself, with a floor of their own."""
    text = INSTALL_SRC.read_text()
    block = re.search(r"readonly FLOOR=\((.*?)\n\)", text, re.S).group(1)
    names = block.split()
    assert len(names) >= 15, names
    return names


def _git(cwd, *args):
    # Both signing keys are disabled on every fixture repo: a hardware-key prompt hangs a
    # suite instead of failing it. The identity is a test one.
    subprocess.run(
        [
            "git",
            "-c",
            "commit.gpgsign=false",
            "-c",
            "tag.gpgsign=false",
            "-c",
            "user.name=test",
            "-c",
            "user.email=test@test.invalid",
            *args,
        ],
        cwd=cwd,
        check=True,
        capture_output=True,
    )


def _env(home, **extra):
    env = {"HOME": str(home), "PATH": os.environ["PATH"]}
    env.update(extra)
    return env


class Farm:
    """A built farm: `prod` (source clone), `home`, `target` (home/.claude's real directory)."""

    def __init__(self, root: Path, saved: dict):
        self.root = root
        self.prod = root / "prod"
        self.home = root / "home"
        self.target = root / "target-real"
        self.saved = saved

    def hook(self, base=None) -> Path:
        return (base or self.prod) / "scripts" / HOOK_NAME

    def stub(self, body: str, mode=0o755):
        path = self.prod / "install.sh"
        path.write_text("#!/bin/bash\n" + body)
        path.chmod(mode)

    def run(self, payload=SESSION, home=None, hook=None, tmp=None, **extra_env):
        env = _env(home or self.home, **extra_env)
        return subprocess.run(
            [str(hook or self.hook())],
            input=payload,
            capture_output=True,
            env=env,
            cwd=str(tmp or self.root),
            timeout=120,
        )

    def message(self, proc) -> str:
        assert proc.returncode == 0, proc.stderr
        assert proc.stdout.endswith(b"\n") and proc.stdout.count(b"\n") == 1, (
            proc.stdout
        )
        obj = json.loads(proc.stdout)
        assert list(obj) == ["systemMessage"], obj
        return obj["systemMessage"]

    def replace_with_file(self, name, text="replacement\n"):
        tmp = self.target / (name + ".tmp")
        tmp.write_text(text)
        os.replace(tmp, self.target / name)


def _restore(farm: Farm):
    """Return prod and target to the freshly installed state (cheap; no install.sh run)."""
    for child in list(farm.target.iterdir()):
        if child.is_symlink() or child.is_file():
            child.unlink()
        else:
            shutil.rmtree(child)
    for name, (kind, value) in farm.saved.items():
        path = farm.target / name
        if kind == "link":
            os.symlink(value, path)
        else:
            path.write_bytes(value)
    shutil.copy2(INSTALL_SRC, farm.prod / "install.sh")
    (farm.prod / "install.sh").chmod(0o755)
    shutil.copy2(HOOK_SRC, farm.prod / "scripts" / HOOK_NAME)
    (farm.prod / "scripts" / HOOK_NAME).chmod(0o755)


@pytest.fixture(scope="module")
def _built(tmp_path_factory):
    root = Path(os.path.realpath(tmp_path_factory.mktemp("farm")))
    prod = root / "prod"
    prod.mkdir()
    _git(prod, "init", "-q")
    for name in _floor():
        if name in DIRS:
            (prod / name).mkdir()
            (prod / name / "keep").write_text("x\n")
        else:
            (prod / name).write_text(name + "\n")
    shutil.copy2(INSTALL_SRC, prod / "install.sh")
    shutil.copy2(HOOK_SRC, prod / "scripts" / HOOK_NAME)
    _git(prod, "add", "-A")
    _git(prod, "commit", "-q", "-m", "fixture")
    for untracked in (".claude", ".pytest_cache", ".ruff_cache"):
        (prod / untracked).mkdir()  # production has these; install.sh WARNs about each
    target = root / "target-real"
    target.mkdir()
    home = root / "home"
    home.mkdir()
    os.symlink(target, home / ".claude")
    done = subprocess.run(
        [str(prod / "install.sh")],
        env=_env(home),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert done.returncode == 0, done.stdout + done.stderr
    assert (target / ".installed-from").is_file()
    saved = {}
    for child in target.iterdir():
        if child.is_symlink():
            saved[child.name] = ("link", os.readlink(child))
        else:
            saved[child.name] = ("file", child.read_bytes())
    assert len(saved) >= 16, sorted(saved)
    return Farm(root, saved)


@pytest.fixture
def farm(_built, tmp_path):
    _restore(_built)
    _built.scratch = Path(os.path.realpath(tmp_path))
    return _built


def test_clean_farm_is_silent(farm):
    proc = farm.run(**SLOW_OK)
    assert (proc.returncode, proc.stdout, proc.stderr) == (0, b"", b"")


def test_warn_lines_before_the_verdict_do_not_hide_it(farm):
    farm.stub(
        "echo 'WARN: untracked working-tree root entry: .ruff_cache'\n"
        "echo 'RESULT: PASS (source=/x check=clean)'\n"
    )
    proc = farm.run()
    assert (proc.returncode, proc.stdout) == (0, b"")
    farm.stub(
        "echo 'WARN: untracked working-tree root entry: .ruff_cache'\n"
        "echo 'DRIFT: STYLE.md (missing or not a live symlink at /x/STYLE.md)'\n"
        "echo 'RESULT: FAIL (source=/x check=drift)'\nexit 1\n"
    )
    assert "STYLE.md (" in farm.message(farm.run())


def test_replaced_link_is_named(farm):
    farm.replace_with_file("settings.json")
    msg = farm.message(farm.run(**SLOW_OK))
    assert re.search(r"settings\.json \(last changed \d{4}-\d{2}-\d{2}\)", msg), msg
    assert " ago" not in msg and "SessionStart" not in msg
    assert "install.sh" in msg
    assert str(farm.root) not in msg and str(farm.home) not in msg
    user = getpass.getuser()
    if len(user) >= 4:
        assert user not in msg


def test_foreign_link_form_is_named_by_member_only(farm):
    os.unlink(farm.target / "CLAUDE.md")
    os.symlink(farm.prod / "LICENSE", farm.target / "CLAUDE.md")
    msg = farm.message(farm.run(**SLOW_OK))
    assert "CLAUDE.md (" in msg
    assert "resolved" not in msg and str(farm.prod) not in msg and "want" not in msg


def test_cap_names_five_then_counts_the_rest(farm):
    seven = [
        "CLAUDE.md",
        "CONTRIBUTING.md",
        "LICENSE",
        "README.md",
        "STYLE.md",
        "templates.md",
        "workflows.md",
    ]
    for name in seven:
        farm.replace_with_file(name)
    msg = farm.message(farm.run(**SLOW_OK))
    assert sum(1 for n in seven if n + " (" in msg) == 5, msg
    assert ", and 2 more." in msg


def test_absent_member_says_absent(farm):
    os.unlink(farm.target / "STYLE.md")
    assert "STYLE.md (absent)" in farm.message(farm.run(**SLOW_OK))


def _sentinel_stub(farm):
    sentinel = farm.scratch / "ran"
    farm.stub("touch '%s'\necho 'RESULT: PASS (source=/x check=clean)'\n" % sentinel)
    return sentinel


def test_not_a_farm_is_silent_and_does_not_run_the_check(farm):
    sentinel = _sentinel_stub(farm)
    bare = farm.scratch / "bare-home"
    bare.mkdir()
    proc = farm.run(home=bare)
    assert (proc.returncode, proc.stdout, proc.stderr) == (0, b"", b"")
    assert not sentinel.exists()


def test_the_marker_must_be_a_regular_file(farm):
    sentinel = _sentinel_stub(farm)
    marker = farm.target / ".installed-from"
    marker.unlink()
    marker.mkdir()
    proc = farm.run()
    assert (proc.returncode, proc.stdout) == (0, b"")
    assert not sentinel.exists(), "a directory is not the marker"
    marker.rmdir()
    marker.write_text("x\n")
    farm.run()
    assert sentinel.exists(), "control: with a marker file the stub must run"


def test_payloads_that_are_not_session_start_do_not_run_the_check(farm):
    sentinel = _sentinel_stub(farm)
    for payload in (
        b"\xff",
        b"{}",
        INERT,
        b"",
        b"not json",
        b"[]",
        b'{"hook_event_name":"PostToolUse"}',
    ):
        proc = farm.run(payload=payload)
        assert (proc.returncode, proc.stdout) == (0, b""), payload
        assert not sentinel.exists(), payload
    farm.run()
    assert sentinel.exists(), "control: a SessionStart payload must run the stub"


def _alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def test_deadline_is_honoured_and_reaches_grandchildren(farm):
    pidfile = farm.scratch / "pid"
    farm.stub("sleep 30 &\necho $! > '%s'\nwait\n" % pidfile)
    started = time.monotonic()
    proc = farm.run(FARM_DRIFT_DEADLINE_SECS="1")
    assert time.monotonic() - started < 6
    assert "did not finish within 1s" in farm.message(proc)
    pid = int(pidfile.read_text())
    for _ in range(30):
        if not _alive(pid):
            break
        time.sleep(0.1)
    assert not _alive(pid), "the grandchild survived: only the direct child was killed"


def test_sigkill_ends_a_child_that_ignores_term(farm):
    # An ignored signal is inherited by the background child, so only KILL ends both processes.
    shell_pid = farm.scratch / "shell-pid"
    child_pid = farm.scratch / "child-pid"
    farm.stub(
        "trap '' TERM\necho $$ > '%s'\nsleep 30 &\necho $! > '%s'\nwait\n"
        % (shell_pid, child_pid)
    )
    started = time.monotonic()
    proc = farm.run(FARM_DRIFT_DEADLINE_SECS="1")
    assert time.monotonic() - started < 8
    assert "did not finish within 1s" in farm.message(proc)
    pids = [int(shell_pid.read_text()), int(child_pid.read_text())]
    for _ in range(40):
        if not any(_alive(pid) for pid in pids):
            break
        time.sleep(0.1)
    assert not any(_alive(pid) for pid in pids), "a TERM-ignoring process survived"


def test_a_missing_home_is_silent_and_does_not_run_the_check(farm):
    sentinel = _sentinel_stub(farm)
    proc = subprocess.run(
        [str(farm.hook())],
        input=SESSION,
        capture_output=True,
        env={"PATH": os.environ["PATH"]},
        cwd=str(farm.root),
        timeout=120,
    )
    assert (proc.returncode, proc.stdout, proc.stderr) == (0, b"", b"")
    assert not sentinel.exists()


@pytest.mark.parametrize("bad", ["abc", "-3", "0", "nan", "inf"])
def test_invalid_deadline_override_is_reported_not_silently_ignored(farm, bad):
    farm.stub("echo 'RESULT: PASS (source=/x check=clean)'\n")
    proc = farm.run(FARM_DRIFT_DEADLINE_SECS=bad)
    assert proc.returncode == 0 and proc.stdout == b""
    err = proc.stderr.decode()
    assert repr(bad) in err and "using 8s" in err, err


def test_a_refusal_is_reported_never_silent(farm):
    farm.stub("echo 'refuse: x' >&2\nexit 1\n")
    assert "could not run (refuse: x)." in farm.message(farm.run())


def test_a_run_with_no_verdict_is_reported_never_silent(farm):
    farm.stub("exit 0\n")
    assert "printed no verdict" in farm.message(farm.run())


def test_a_drift_verdict_without_names_is_reported(farm):
    farm.stub("echo 'RESULT: FAIL (source=/x check=drift)'\nexit 1\n")
    assert "names were unreadable" in farm.message(farm.run())


def test_a_name_with_a_slash_is_unusable(farm):
    farm.stub(
        "echo 'DRIFT: a/b (missing or not a live symlink at /x)'\n"
        "echo 'RESULT: FAIL (source=/x check=drift)'\nexit 1\n"
    )
    msg = farm.message(farm.run())
    assert "names were unreadable" in msg and "a/b" not in msg


def test_control_characters_never_reach_the_message(farm):
    farm.stub(
        "printf 'DRIFT: bad\\033[31mname (missing or not a live symlink at /x)\\n'\n"
        "echo 'RESULT: FAIL (source=/x check=drift)'\nexit 1\n"
    )
    msg = farm.message(farm.run())
    assert "\x1b" not in msg and "bad[31mname" in msg


def test_an_unexecutable_install_sh_is_reported(farm):
    farm.stub("echo 'RESULT: PASS (source=/x check=clean)'\n", mode=0o644)
    assert "cannot execute" in farm.message(farm.run())


def test_a_long_stderr_line_is_capped_at_100_characters(farm):
    farm.stub("echo 'refuse: " + "x" * 200 + "' >&2\nexit 1\n")
    msg = farm.message(farm.run())
    assert "refuse: " + "x" * 92 in msg and "x" * 93 not in msg


def test_a_missing_install_sh_is_silent(farm):
    (farm.prod / "install.sh").unlink()
    proc = farm.run()
    assert (proc.returncode, proc.stdout, proc.stderr) == (0, b"", b"")


def test_the_member_date_is_the_ctime_not_an_older_mtime(farm):
    farm.replace_with_file("settings.json")
    old = 1000000000  # 2001-09-09: long before the replacement
    os.utime(farm.target / "settings.json", (old, old))
    before = time.strftime("%Y-%m-%d")
    msg = farm.message(farm.run(**SLOW_OK))
    after = time.strftime("%Y-%m-%d")
    found = re.search(r"settings\.json \(last changed (\d{4}-\d{2}-\d{2})\)", msg)
    assert found, msg
    assert found.group(1) in (before, after)
    assert found.group(1) != time.strftime("%Y-%m-%d", time.localtime(old))


def test_a_dangling_link_member_is_dated_not_absent(farm):
    os.unlink(farm.target / "CLAUDE.md")
    os.symlink("/nonexistent/target", farm.target / "CLAUDE.md")
    msg = farm.message(farm.run(**SLOW_OK))
    assert "CLAUDE.md (last changed" in msg and "(absent)" not in msg


def test_scripts_copied_alone_names_scripts_with_the_whole_list(farm):
    os.unlink(farm.target / "scripts")
    shutil.copytree(farm.prod / "scripts", farm.target / "scripts")
    copy = farm.target / "scripts" / HOOK_NAME
    msg = farm.message(farm.run(hook=copy, **SLOW_OK))
    assert "scripts (" in msg and "running from a copy" not in msg
    farm.replace_with_file("CLAUDE.md")
    msg = farm.message(farm.run(hook=copy, **SLOW_OK))
    assert "scripts (" in msg and "CLAUDE.md (" in msg


def test_scripts_and_install_both_copied_says_running_from_a_copy(farm):
    os.unlink(farm.target / "scripts")
    shutil.copytree(farm.prod / "scripts", farm.target / "scripts")
    os.unlink(farm.target / "install.sh")
    shutil.copy2(farm.prod / "install.sh", farm.target / "install.sh")
    msg = farm.message(farm.run(hook=farm.target / "scripts" / HOOK_NAME, **SLOW_OK))
    assert "running from a copy of the source" in msg
    assert str(farm.target) not in msg and "script=" not in msg
    # The mapping is read from stderr's first line: the exact prefix maps, another refusal does not.
    farm.stub(
        "echo 'refuse: install.sh must not run from inside the install target "
        "(script=/a target=/b)' >&2\nexit 1\n"
    )
    msg = farm.message(farm.run())
    assert "running from a copy of the source" in msg and "/a" not in msg
    farm.stub("echo 'refuse: some other refusal' >&2\nexit 1\n")
    assert "could not run (refuse: some other refusal)" in farm.message(farm.run())


def _shell(farm, script, payload=SESSION, **extra_env):
    pay = farm.scratch / "payload"
    pay.write_bytes(payload)
    return subprocess.run(
        ["bash", "-c", script, "_", str(farm.hook()), str(pay)],
        env=_env(farm.home, **extra_env),
        capture_output=True,
        timeout=120,
    )


def test_a_closed_or_broken_stdout_still_exits_zero(farm):
    farm.replace_with_file("settings.json")
    for script in ('"$1" <"$2" >&-', '"$1" <"$2" | true; exit "${PIPESTATUS[0]}"'):
        proc = _shell(farm, script, **SLOW_OK)
        assert proc.returncode == 0, (script, proc.returncode, proc.stderr)


def test_a_closed_stderr_still_exits_zero_and_stdout_stays_silent(farm):
    farm.stub("echo 'RESULT: PASS (source=/x check=clean)'\n")
    proc = _shell(farm, '"$1" <"$2" 2>&-', FARM_DRIFT_DEADLINE_SECS="abc")
    assert (proc.returncode, proc.stdout) == (0, b"")


def test_a_broken_stderr_pipe_still_exits_zero(farm):
    farm.stub("echo 'RESULT: PASS (source=/x check=clean)'\n")
    script = '"$1" <"$2" 2>&1 >/dev/null | true; exit "${PIPESTATUS[0]}"'
    proc = _shell(farm, script, FARM_DRIFT_DEADLINE_SECS="abc")
    assert proc.returncode == 0


def _snapshot(*roots):
    rows = []
    for root in roots:
        for dirpath, dirnames, filenames in os.walk(root):
            for name in sorted(dirnames + filenames):
                path = os.path.join(dirpath, name)
                st = os.lstat(path)
                rows.append((path, stat.S_IFMT(st.st_mode), st.st_size, st.st_mtime_ns))
    return sorted(rows)


def test_a_run_writes_no_state(farm):
    private_tmp = farm.scratch / "tmp"
    cwd = farm.scratch / "cwd"
    private_tmp.mkdir()
    cwd.mkdir()
    roots = (farm.prod, farm.target, farm.home, private_tmp, cwd)
    for drifted in (False, True):
        if drifted:
            farm.replace_with_file("settings.json")
        before = _snapshot(*roots)
        assert before, "the snapshot read nothing"
        proc = farm.run(tmp=cwd, TMPDIR=str(private_tmp), **SLOW_OK)
        assert proc.returncode == 0
        assert _snapshot(*roots) == before


def test_registration_is_explicit_and_leaves_room_below_the_timeout():
    doc = json.loads(SETTINGS.read_text())
    found = [
        (matcher, entry)
        for event, matcher, entry in settings_hooks.walk_hook_entries(
            doc, str(SETTINGS)
        )
        if event == "SessionStart" and entry["command"] == PREFIX + HOOK_NAME
    ]
    assert len(found) == 1, "SessionStart registration of %s: %r" % (HOOK_NAME, found)
    matcher, entry = found[0]
    assert matcher == "startup|resume"
    assert "timeout" in entry, "an absent timeout is the 600 s harness default"
    spec = importlib.util.spec_from_file_location("farm_drift_check", HOOK_SRC)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.DEADLINE_SECS + 2 * mod.KILL_GRACE_SECS + 2 <= entry["timeout"]

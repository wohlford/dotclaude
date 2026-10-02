"""Hermetic suite for scripts/run-hooks.py.

Every row drives the SHIPPED PROCESS with `subprocess` — an in-process call of `main()` never reaches
what happens at process exit — against stub hook scripts in a temp dir and a temp settings file. No
real hook, no network, no repo state: `settings.json` is always a fixture, and a stub that must never
run records any execution so its absence is assertable.

Run: python3 -m pytest scripts/tests/test_run_hooks.py -q
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent.parent
TOOL = REPO / "scripts" / "run-hooks.py"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "hook-payloads"


def settings(tmp: Path, regs: list[dict]) -> Path:
    """regs: {event, matcher, command, **extra entry keys} -> a settings.json; one group each."""
    hooks: dict[str, list] = {}
    for reg in regs:
        reg = dict(reg)
        event = reg.pop("event", "PostToolUse")
        matcher = reg.pop("matcher", "Edit|Write")
        entry = {"type": "command", **reg}
        group = {"hooks": [entry]}
        if matcher is not None:  # None = the key is absent
            group["matcher"] = matcher
        hooks.setdefault(event, []).append(group)
    path = tmp / "settings.json"
    path.write_text(json.dumps({"hooks": hooks}))
    return path


def stub(tmp: Path, name: str, body: str = "exit 0") -> Path:
    """An executable hook: records its stdin to <name>.rec and each run to <name>.runs, then body."""
    path = tmp / name
    path.write_text(
        "#!/bin/sh\necho run >> '%s.runs'\ncat > '%s.rec'\n%s\n" % (path, path, body)
    )
    path.chmod(0o755)
    return path


def runs(tmp: Path, name: str) -> int:
    f = tmp / (name + ".runs")
    return len(f.read_text().split()) if f.exists() else 0


def target(tmp: Path) -> Path:
    path = tmp / "f.txt"
    path.write_text("x\n")
    return path


def tool(args, env=None, cwd=None, timeout=90):
    e = dict(os.environ)
    e.update(env or {})
    return subprocess.run(
        [sys.executable, str(TOOL), *map(str, args)],
        capture_output=True,
        text=True,
        env=e,
        cwd=cwd,
        timeout=timeout,
    )


def result(cp) -> tuple[str, dict]:
    last = cp.stdout.rstrip("\n").splitlines()[-1]
    assert last.startswith("RESULT: "), "last stdout line is not a verdict: %r" % last
    tokens = last.split()
    fields = dict(t.split("=", 1) for t in tokens[2:] if "=" in t)
    return tokens[1], fields


def edit(tmp, regs, *extra, **kw):
    return tool(
        [target(tmp), "--settings", settings(tmp, regs), "--cwd", tmp, *extra], **kw
    )


# --- matcher semantics, as measured against the real harness -------------------------------


@pytest.mark.parametrize(
    "matcher,tool_name,expected",
    [
        ("Edit", "Edit", True),
        ("Edit", "Write", False),
        ("Edit|Write", "Write", True),
        ("Ed", "Edit", False),  # whole-name match, not a search and not a prefix
        ("^Edit$", "Edit", True),
        ("edit", "Edit", False),  # case-sensitive
        ("*", "Bash", True),
        ("", "Bash", True),
        ("Write, Bash", "Bash", True),  # comma list
        ("Write, Bash", "Edit", False),
        ("Wri.*", "Write", True),
        ("Wri.*", "Edit", False),
        (
            None,
            "Edit",
            True,
        ),  # an absent matcher key: NOT measured, assumed match-all like ""
        (None, "Bash", True),
    ],
)
def test_matcher_rules(tmp_path, matcher, tool_name, expected):
    # PostToolUse with Bash is refused (its tool_response cannot be synthesized), so Bash rows are Pre
    event = "PreToolUse" if tool_name == "Bash" else "PostToolUse"
    s = settings(
        tmp_path,
        [{"event": event, "matcher": matcher, "command": str(stub(tmp_path, "h.sh"))}],
    )
    if tool_name == "Bash":
        args = ["--tool", "Bash", "--command", "true"]
    else:
        args = [target(tmp_path), "--tool", tool_name]
    cp = tool([*args, "--event", event, "--settings", s, "--cwd", tmp_path, "--list"])
    if expected:
        assert cp.returncode == 0, cp.stdout
        assert "LIST: 1 to run" in cp.stdout
    else:
        assert result(cp)[0] == "ERROR" and cp.returncode == 2


def test_invalid_matcher_regex_is_unmodelled_not_guessed(tmp_path):
    h = stub(tmp_path, "h.sh")
    cp = edit(tmp_path, [{"matcher": "Ed(", "command": str(h)}])
    status, f = result(cp)
    assert (status, cp.returncode, f["unmodelled"]) == ("INCOMPLETE", 3, "1")
    assert runs(tmp_path, "h.sh") == 0


def test_a_non_string_matcher_is_unmodelled_not_guessed(tmp_path):
    h = stub(tmp_path, "h.sh")
    cp = edit(tmp_path, [{"matcher": 7, "command": str(h)}])
    status, f = result(cp)
    assert (status, f["unmodelled"]) == ("INCOMPLETE", "1")
    assert runs(tmp_path, "h.sh") == 0


@pytest.mark.parametrize("bad", [0, -5, "10", True, float("nan"), float("inf")])
def test_a_timeout_the_harness_would_not_honour_is_unmodelled(tmp_path, bad):
    h = stub(tmp_path, "h.sh")
    status, f = result(edit(tmp_path, [{"command": str(h), "timeout": bad}]))
    assert (status, f["unmodelled"]) == ("INCOMPLETE", "1")
    assert runs(tmp_path, "h.sh") == 0


@pytest.mark.parametrize("timeouts", [(600, 1), (1, 600)])
def test_identical_commands_keep_the_earliest_kill(tmp_path, timeouts):
    h = str(stub(tmp_path, "h.sh", "sleep 5"))
    regs = [{"command": h, "timeout": t} for t in timeouts]
    status, f = result(edit(tmp_path, regs))
    assert (status, f["killed"], f["deduped"]) == ("INCOMPLETE", "1", "1")


def test_identical_commands_run_once(tmp_path):
    h = str(stub(tmp_path, "h.sh"))
    regs = [
        {"matcher": "Edit", "command": h},
        {"matcher": "Edit|Write", "command": h},
        {"matcher": "*", "command": h},
    ]
    status, f = result(edit(tmp_path, regs))
    assert (status, f["matched"], f["deduped"], f["ran"]) == ("PASS", "3", "2", "1")
    assert runs(tmp_path, "h.sh") == 1


def test_event_selects_registrations(tmp_path):
    pre = stub(tmp_path, "pre.sh")
    post = stub(tmp_path, "post.sh")
    regs = [
        {"event": "PreToolUse", "command": str(pre)},
        {"event": "PostToolUse", "command": str(post)},
    ]
    result(edit(tmp_path, regs, "--event", "PreToolUse"))
    assert (runs(tmp_path, "pre.sh"), runs(tmp_path, "post.sh")) == (1, 0)


# --- the payload the hook receives ---------------------------------------------------------


def keys(d):
    return sorted(d)


@pytest.mark.parametrize(
    "event,tool_name",
    [
        ("PreToolUse", "Edit"),
        ("PreToolUse", "Write"),
        ("PreToolUse", "Bash"),
        ("PostToolUse", "Edit"),
        ("PostToolUse", "Write"),
    ],
)
def test_payload_key_sets_match_the_captured_harness_payload(
    tmp_path, event, tool_name
):
    h = stub(tmp_path, "h.sh")
    s = settings(tmp_path, [{"event": event, "matcher": "*", "command": str(h)}])
    if tool_name == "Bash":
        subject = ["--tool", "Bash", "--command", "git status"]
    else:
        subject = [target(tmp_path), "--tool", tool_name]
    cp = tool([*subject, "--event", event, "--settings", s, "--cwd", tmp_path])
    assert result(cp)[0] == "PASS", cp.stdout
    got = json.loads((tmp_path / "h.sh.rec").read_text())
    want = json.loads((FIXTURES / ("%s-%s.json" % (event, tool_name))).read_text())
    assert keys(got) == keys(want)
    assert keys(got["tool_input"]) == keys(want["tool_input"])
    assert keys(got.get("tool_response", {})) == keys(want.get("tool_response", {}))
    assert got["hook_event_name"] == event and got["tool_name"] == tool_name
    if tool_name == "Bash":
        assert got["tool_input"]["command"] == "git status"
    else:
        assert got["tool_input"]["file_path"] == str(tmp_path / "f.txt")


def test_cwd_and_project_dir_are_the_given_cwd(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    h = stub(
        tmp_path, "h.sh", "echo \"$PWD|$CLAUDE_PROJECT_DIR\" > '%s/env.out'" % tmp_path
    )
    s = settings(tmp_path, [{"command": str(h)}])
    cp = tool([target(tmp_path), "--settings", s, "--cwd", work])
    assert result(cp)[0] == "PASS"
    pwd, proj = (tmp_path / "env.out").read_text().strip().split("|")
    assert os.path.realpath(pwd) == os.path.realpath(work) and proj == str(work)
    assert json.loads((tmp_path / "h.sh.rec").read_text())["cwd"] == str(work)


# --- verdicts: harness exit-code semantics -------------------------------------------------


@pytest.mark.parametrize(
    "body,status,rc,verdict",
    [
        ("exit 0", "PASS", 0, "ALLOW"),
        ("echo nope >&2; exit 2", "FAIL", 1, "BLOCK"),
        ("exit 1", "INCOMPLETE", 3, "NOISE"),
        ("kill -9 $$", "INCOMPLETE", 3, "DIED"),
    ],
)
def test_verdict_mapping(tmp_path, body, status, rc, verdict):
    cp = edit(tmp_path, [{"command": str(stub(tmp_path, "h.sh", body))}])
    assert (result(cp)[0], cp.returncode) == (status, rc)
    assert verdict in cp.stdout


def test_block_shows_stderr_and_allow_hides_it_unless_full(tmp_path):
    blocker = stub(tmp_path, "b.sh", "echo MESSAGE-FOR-CLAUDE >&2; exit 2")
    quiet = stub(tmp_path, "q.sh", "echo CHATTER >&2; exit 0")
    regs = [{"command": str(blocker)}, {"command": str(quiet)}]
    out = edit(tmp_path, regs).stdout
    assert "MESSAGE-FOR-CLAUDE" in out and "CHATTER" not in out
    assert "CHATTER" in edit(tmp_path, regs, "--full").stdout


def test_stderr_tail_keeps_the_last_lines_and_names_the_omission(tmp_path):
    body = "i=1; while [ $i -le 30 ]; do echo line$i >&2; i=$((i+1)); done; exit 2"
    out = edit(tmp_path, [{"command": str(stub(tmp_path, "h.sh", body))}]).stdout
    assert "(10 earlier stderr lines omitted)" in out
    assert "line30" in out and "line11" in out and "line10" not in out
    assert "line1\n" not in out


def test_block_outranks_an_incomplete_sibling(tmp_path):
    regs = [
        {"command": str(stub(tmp_path, "n.sh", "exit 1"))},
        {"command": str(stub(tmp_path, "b.sh", "exit 2"))},
    ]
    status, f = result(edit(tmp_path, regs))
    assert (status, f["block"], f["noise"]) == ("FAIL", "1", "1")


def test_overrun_is_killed_and_not_a_pass(tmp_path):
    h = stub(tmp_path, "h.sh", "sleep 30")
    t0 = time.monotonic()
    cp = edit(tmp_path, [{"command": str(h), "timeout": 1}])
    assert time.monotonic() - t0 < 20
    status, f = result(cp)
    assert (status, cp.returncode, f["killed"]) == ("INCOMPLETE", 3, "1")
    assert "KILLED" in cp.stdout


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def wait_dead(pid: int, secs: float = 5.0) -> bool:
    end = time.monotonic() + secs
    while time.monotonic() < end:
        if not alive(pid):
            return True
        time.sleep(0.05)
    return not alive(pid)


def pid_of(path: Path, secs: float = 10.0) -> int:
    end = time.monotonic() + secs
    while time.monotonic() < end:
        if path.exists() and path.read_text().strip():
            return int(path.read_text().strip())
        time.sleep(0.05)
    raise AssertionError("no pid in %s" % path)


def test_timed_out_hooks_descendants_are_killed_too(tmp_path):
    pidf = tmp_path / "child.pid"
    h = stub(tmp_path, "h.sh", "sleep 60 & echo $! > '%s'; wait" % pidf)
    # 3 s, not 1: the hook must have recorded its child's pid before it is killed
    result(edit(tmp_path, [{"command": str(h), "timeout": 3}]))
    assert wait_dead(pid_of(pidf)), "a descendant outlived its timed-out hook"


def test_a_hook_ignoring_term_is_killed_after_the_grace(tmp_path):
    pidf = tmp_path / "child.pid"
    h = stub(tmp_path, "h.sh", "trap '' TERM; sleep 60 & echo $! > '%s'; wait" % pidf)
    cp = edit(tmp_path, [{"command": str(h), "timeout": 3}])
    assert result(cp)[1]["killed"] == "1"
    assert wait_dead(pid_of(pidf))


@pytest.mark.parametrize(
    "sig,code",
    [
        (signal.SIGTERM, 143),
        (signal.SIGINT, 130),
        (signal.SIGHUP, 129),
        (signal.SIGQUIT, 131),
    ],
)
def test_a_signal_to_the_tool_leaves_no_hook_running(tmp_path, sig, code):
    pidf = tmp_path / "child.pid"
    h = stub(tmp_path, "h.sh", "sleep 60 & echo $! > '%s'; wait" % pidf)
    s = settings(tmp_path, [{"command": str(h)}])
    proc = subprocess.Popen(
        [
            sys.executable,
            str(TOOL),
            str(target(tmp_path)),
            "--settings",
            str(s),
            "--cwd",
            str(tmp_path),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    child = pid_of(pidf)
    proc.send_signal(sig)
    out, _ = proc.communicate(timeout=30)
    assert proc.returncode == code
    assert "RESULT: PASS" not in out
    assert wait_dead(child), "a hook survived the tool being terminated"


# --- concurrency ---------------------------------------------------------------------------


def handshake(tmp: Path, me: str, other: str) -> str:
    return (
        "touch '%s/%s'; i=0; while [ ! -f '%s/%s' ] && [ $i -lt 60 ]; do sleep 0.05; i=$((i+1)); done; "
        "[ -f '%s/%s' ]" % (tmp, me, tmp, other, tmp, other)
    )


def test_concurrent_overlaps_and_sequential_does_not(tmp_path):
    regs = [
        {
            "command": str(
                stub(tmp_path, "a.sh", handshake(tmp_path, "a.flag", "b.flag"))
            )
        },
        {
            "command": str(
                stub(tmp_path, "b.sh", handshake(tmp_path, "b.flag", "a.flag"))
            )
        },
    ]
    assert result(edit(tmp_path, regs, "--concurrent"))[0] == "PASS"
    for f in ("a.flag", "b.flag"):
        (tmp_path / f).unlink()
    # sequentially, `a` waits for a `b` that has not started: it times out itself
    assert result(edit(tmp_path, regs))[0] == "INCOMPLETE"


def test_output_is_in_registration_order_even_when_concurrent(tmp_path):
    regs = [
        {"command": str(stub(tmp_path, "slow.sh", "sleep 2"))},
        {"command": str(stub(tmp_path, "fast.sh", "exit 0"))},
    ]
    out = edit(tmp_path, regs, "--concurrent").stdout
    assert out.index("slow.sh") < out.index("fast.sh")


# --- what is selected, refused, or never run ------------------------------------------------


def test_the_standing_constraint_hook_is_skipped_and_never_executed(tmp_path):
    # NB: pytest names tmp_path after this function, so its name must not itself match the pattern
    forbidden = stub(tmp_path, "exec-bit-guard-test.sh")
    ok = stub(tmp_path, "ok.sh")
    cp = edit(tmp_path, [{"command": str(forbidden)}, {"command": str(ok)}])
    status, f = result(cp)
    assert (status, f["skipped"], f["ran"]) == ("PASS", "1", "1")
    assert (
        runs(tmp_path, "exec-bit-guard-test.sh") == 0 and runs(tmp_path, "ok.sh") == 1
    )
    assert "SKIPPED" in cp.stdout
    only = edit(tmp_path, [{"command": str(forbidden)}], "--list")
    assert runs(tmp_path, "exec-bit-guard-test.sh") == 0 and "SKIPPED" in only.stdout


def test_only_that_is_all_skipped_is_incomplete_not_a_pass(tmp_path):
    cp = edit(tmp_path, [{"command": str(stub(tmp_path, "exec-bit-guard.sh"))}])
    assert result(cp)[0] == "INCOMPLETE" and cp.returncode == 3


def test_an_unmodelled_sibling_keeps_a_passing_run_from_being_a_pass(tmp_path):
    ok = stub(tmp_path, "ok.sh")
    regs = [
        {"command": str(ok)},
        {"command": str(stub(tmp_path, "x.sh")), "if": "Edit(*.md)"},
    ]
    status, f = result(edit(tmp_path, regs))
    assert (status, f["allow"], f["unmodelled"]) == ("INCOMPLETE", "1", "1")


def test_a_usage_error_still_ends_in_a_verdict_line(tmp_path):
    s = settings(tmp_path, [{"command": str(stub(tmp_path, "h.sh"))}])
    cp = tool([target(tmp_path), "--event", "Nope", "--settings", s])
    assert (result(cp)[0], cp.returncode) == ("ERROR", 2)


@pytest.mark.parametrize(
    "extra", [{"if": "Edit(*.md)"}, {"async": True}, {"type": "prompt"}]
)
def test_unmodelled_entry_keys_are_reported_and_not_run(tmp_path, extra):
    h = stub(tmp_path, "h.sh")
    cp = edit(tmp_path, [{"command": str(h), **extra}])
    status, f = result(cp)
    assert (status, f["unmodelled"], f["ran"]) == ("INCOMPLETE", "1", "0")
    assert runs(tmp_path, "h.sh") == 0 and "UNMODELLED" in cp.stdout


def test_only_selects_by_script_name(tmp_path):
    a, b = stub(tmp_path, "a.sh"), stub(tmp_path, "b.sh")
    status, f = result(
        edit(tmp_path, [{"command": str(a)}, {"command": str(b)}], "--only", "a.sh")
    )
    assert (status, f["matched"], f["filtered"]) == ("PASS", "1", "1")
    assert (runs(tmp_path, "a.sh"), runs(tmp_path, "b.sh")) == (1, 0)


def test_only_names_what_it_dropped_so_the_pass_is_not_a_full_one(tmp_path):
    # a dropped sibling that would block must leave a trace in both lines; a full run reports 0
    a = stub(tmp_path, "a.sh")
    b = stub(tmp_path, "b.sh", "exit 2")
    regs = [{"command": str(a)}, {"command": str(b)}]
    cp = edit(tmp_path, regs, "--only", "a.sh")
    status, f = result(cp)
    assert (status, f["filtered"]) == ("PASS", "1")
    assert "filtered=1" in next(
        ln for ln in cp.stdout.splitlines() if ln.startswith("plan:")
    )
    status, f = result(edit(tmp_path, [{"command": str(a)}]))
    assert (status, f["filtered"]) == ("PASS", "0")


def test_scripts_dir_runs_the_branch_copy_and_names_it(tmp_path):
    home = tmp_path / "home"
    farm = home / ".claude" / "scripts"
    branch = tmp_path / "branch"
    farm.mkdir(parents=True)
    branch.mkdir()
    for d, tag in ((farm, "installed"), (branch, "override")):
        (d / "s.sh").write_text(
            "#!/bin/sh\ncat >/dev/null\necho %s > '%s/which'\n" % (tag, tmp_path)
        )
        (d / "s.sh").chmod(0o755)
    s = settings(tmp_path, [{"command": "$HOME/.claude/scripts/s.sh"}])
    env = {"HOME": str(home)}
    base = [target(tmp_path), "--settings", s, "--cwd", tmp_path]
    cp = tool(base, env=env)
    assert (
        tmp_path / "which"
    ).read_text().strip() == "installed" and "copy=installed" in cp.stdout
    cp = tool([*base, "--scripts-dir", branch], env=env)
    assert (
        tmp_path / "which"
    ).read_text().strip() == "override" and "copy=override" in cp.stdout
    assert str(branch / "s.sh") in cp.stdout


# --- refusals: a verdict about nothing must not read as a pass -----------------------------


def test_zero_matches_is_an_error_naming_where_it_looked(tmp_path):
    cp = edit(tmp_path, [{"matcher": "Bash", "command": str(stub(tmp_path, "h.sh"))}])
    status, f = result(cp)
    assert (status, cp.returncode) == ("ERROR", 2)
    assert "settings.json" in cp.stdout and "PostToolUse" in cp.stdout


def test_unknown_only_name_is_an_error(tmp_path):
    cp = edit(tmp_path, [{"command": str(stub(tmp_path, "h.sh"))}], "--only", "nope.sh")
    assert (result(cp)[0], cp.returncode) == ("ERROR", 2)


def test_missing_and_directory_paths_are_errors(tmp_path):
    s = settings(tmp_path, [{"command": str(stub(tmp_path, "h.sh"))}])
    for bad in (tmp_path / "absent.txt", tmp_path):
        cp = tool([bad, "--settings", s, "--cwd", tmp_path])
        assert (result(cp)[0], cp.returncode) == ("ERROR", 2)
    assert runs(tmp_path, "h.sh") == 0


def test_unmeasured_tools_and_argument_shapes_are_refused(tmp_path):
    # PreToolUse throughout: under PostToolUse every Bash row would be refused by the earlier
    # PostToolUse+Bash rule and never reach the check it is named for, so each row also asserts WHICH
    # check refused it
    s = settings(
        tmp_path,
        [
            {
                "event": "PreToolUse",
                "matcher": "*",
                "command": str(stub(tmp_path, "h.sh")),
            }
        ],
    )
    for args, why in (
        ([target(tmp_path), "--tool", "MultiEdit"], "payload shape not measured"),
        (["--tool", "Bash"], "takes --command and no PATH"),
        ([target(tmp_path), "--command", "ls"], "takes --command and no PATH"),
        ([], "takes a PATH and no --command"),
    ):
        cp = tool([*args, "--event", "PreToolUse", "--settings", s, "--cwd", tmp_path])
        assert (result(cp)[0], cp.returncode) == ("ERROR", 2), args
        assert why in cp.stdout, (args, cp.stdout)
    assert runs(tmp_path, "h.sh") == 0


@pytest.mark.parametrize("text", ["{not json", "[]", '{"hooks": {"PostToolUse": "x"}}'])
def test_unreadable_settings_are_an_error(tmp_path, text):
    s = tmp_path / "settings.json"
    s.write_text(text)
    cp = tool([target(tmp_path), "--settings", s, "--cwd", tmp_path])
    assert (result(cp)[0], cp.returncode) == ("ERROR", 2)


def test_list_runs_nothing_and_prints_no_verdict(tmp_path):
    h = stub(tmp_path, "h.sh")
    cp = edit(tmp_path, [{"command": str(h)}], "--list")
    assert cp.returncode == 0 and runs(tmp_path, "h.sh") == 0
    assert "RESULT:" not in cp.stdout and cp.stdout.splitlines()[-1].startswith(
        "LIST: "
    )


def test_post_tool_use_bash_is_refused_not_passed_over_an_empty_response(tmp_path):
    h = stub(tmp_path, "h.sh")
    s = settings(tmp_path, [{"matcher": "Bash", "command": str(h)}])
    cp = tool(["--tool", "Bash", "--command", "ls", "--settings", s, "--cwd", tmp_path])
    assert (result(cp)[0], cp.returncode) == ("ERROR", 2)
    assert runs(tmp_path, "h.sh") == 0


@pytest.mark.parametrize(
    "doc",
    [
        '{"decision":"block","reason":"secret in file"}',
        '{"hookSpecificOutput":{"permissionDecision":"deny"}}',
        '{"continue":false,"stopReason":"no"}',
        '{"stopReason":"no"}',
        '{"permissionDecision":"deny"}',
        '{"systemMessage":"no"}',
        '{"suppressOutput":true}',
        '{"updatedInput":{}}',
    ],
)
def test_a_hook_blocking_through_stdout_json_is_not_a_pass(tmp_path, doc):
    body = "echo '%s'; exit 0" % doc
    h = stub(tmp_path, "h.sh", body)
    cp = edit(tmp_path, [{"command": str(h)}])
    status, f = result(cp)
    assert (status, cp.returncode, f["unmodelled"], f["allow"]) == (
        "INCOMPLETE",
        3,
        "1",
        "0",
    )
    assert "UNMODELLED" in cp.stdout


@pytest.mark.parametrize("out", ["", "plain text", '{"note": 1}', "{not json"])
def test_other_stdout_does_not_change_an_allow(tmp_path, out):
    h = stub(tmp_path, "h.sh", "printf '%%s' '%s'" % out)
    assert result(edit(tmp_path, [{"command": str(h)}]))[0] == "PASS"


def test_a_late_exit_is_killed_and_an_in_time_exit_stays_allowed_under_load(tmp_path):
    # `a` ignores TERM, so the loop spends ~6 s killing it; `b` exits after its deadline and `c`
    # exits in time but is only noticed during that window. `c` has a 3 s margin on purpose: this row
    # failed intermittently (3 of 9 runs in one window, never again in 36) with a 1 s margin, and a
    # stall of that size must not flip it. Settled at ~6 s, `c` would still read as late (> 5 s) if
    # elapsed were taken when the loop gets to it rather than when the hook exited.
    regs = [
        {
            "command": str(stub(tmp_path, "a.sh", "trap '' TERM; sleep 60")),
            "timeout": 1,
        },
        {"command": str(stub(tmp_path, "b.sh", "sleep 4")), "timeout": 1},
        {"command": str(stub(tmp_path, "c.sh", "sleep 2")), "timeout": 5},
    ]
    cp = edit(tmp_path, regs, "--concurrent")
    status, f = result(cp)
    assert (status, f["killed"], f["allow"]) == ("INCOMPLETE", "2", "1")
    assert "finished after its deadline" in cp.stdout


@pytest.mark.parametrize(
    "name",
    [
        "exec-bit-guard.sh",
        "test_exec_bit_guard.sh",
        "EXEC-BIT-GUARD.SH",  # APFS is case-insensitive: this IS the guard file
    ],
)
def test_a_subject_naming_the_guard_is_refused(tmp_path, name):
    h = stub(tmp_path, "h.sh")
    subject = tmp_path / name
    subject.write_text("x\n")
    s = settings(tmp_path, [{"command": str(h)}])
    cp = tool([subject, "--settings", s, "--cwd", tmp_path])
    assert (result(cp)[0], cp.returncode) == ("ERROR", 2)
    assert runs(tmp_path, "h.sh") == 0


def test_an_underscored_guard_command_is_skipped_too(tmp_path):
    forbidden = stub(tmp_path, "exec_bit_guard_test.sh")
    status, f = result(edit(tmp_path, [{"command": str(forbidden)}]))
    assert (status, f["skipped"]) == ("INCOMPLETE", "1")
    assert runs(tmp_path, "exec_bit_guard_test.sh") == 0


def test_scripts_dir_with_a_space_is_quoted_and_a_quoted_prefix_is_unmodelled(tmp_path):
    branch = tmp_path / "branch dir"
    branch.mkdir()
    (branch / "s.sh").write_text("#!/bin/sh\ncat >/dev/null\n")
    (branch / "s.sh").chmod(0o755)
    s = settings(
        tmp_path,
        [
            {"command": "$HOME/.claude/scripts/s.sh"},
            {"command": '"$HOME/.claude/scripts/q.sh"'},
        ],
    )
    cp = tool(
        [target(tmp_path), "--settings", s, "--cwd", tmp_path, "--scripts-dir", branch],
        env={"HOME": str(tmp_path)},
    )
    status, f = result(cp)
    assert (status, f["allow"], f["unmodelled"]) == ("INCOMPLETE", "1", "1")
    assert "copy=override" in cp.stdout


def test_a_relative_scripts_dir_resolves_against_the_tools_cwd(tmp_path):
    branch = tmp_path / "br"
    work = tmp_path / "work"
    branch.mkdir()
    work.mkdir()
    (branch / "s.sh").write_text("#!/bin/sh\ncat >/dev/null\n")
    (branch / "s.sh").chmod(0o755)
    s = settings(tmp_path, [{"command": "$HOME/.claude/scripts/s.sh"}])
    # the hook runs in `work`, not where the tool was started, so a path left relative would not resolve
    cp = tool(
        [target(tmp_path), "--settings", s, "--cwd", work, "--scripts-dir", "br"],
        cwd=tmp_path,
        env={"HOME": str(tmp_path)},
    )
    assert result(cp)[0] == "PASS"


def test_a_relative_cwd_reaches_the_hooks_absolute(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    h = stub(
        tmp_path, "h.sh", "echo \"$CLAUDE_PROJECT_DIR\" > '%s/proj.out'" % tmp_path
    )
    s = settings(tmp_path, [{"command": str(h)}])
    assert (
        result(
            tool([target(tmp_path), "--settings", s, "--cwd", "work"], cwd=tmp_path)
        )[0]
        == "PASS"
    )
    proj = (tmp_path / "proj.out").read_text().strip()
    assert os.path.isabs(proj) and os.path.realpath(proj) == os.path.realpath(work)
    cwd = json.loads((tmp_path / "h.sh.rec").read_text())["cwd"]
    assert os.path.isabs(cwd)


@pytest.mark.parametrize(
    "body,extra,want",
    [("exit 0", [], 2), ("exit 2", [], 1), ("exit 0", ["--list"], 2)],
)
def test_a_stdout_reader_that_went_away_keeps_the_tools_own_status(
    tmp_path, body, extra, want
):
    # drive the REAL process: the interpreter's flush at exit is what turns this into 120
    h = stub(tmp_path, "h.sh", body)
    s = settings(tmp_path, [{"command": str(h)}])
    p = subprocess.Popen(
        [
            sys.executable,
            str(TOOL),
            *extra,
            str(target(tmp_path)),
            "--settings",
            str(s),
            "--cwd",
            str(tmp_path),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    p.stdout.close()
    assert p.wait(timeout=30) == want


def test_list_with_a_usage_error_still_ends_in_a_verdict_line(tmp_path):
    s = settings(
        tmp_path, [{"matcher": "Bash", "command": str(stub(tmp_path, "h.sh"))}]
    )
    cp = tool([target(tmp_path), "--settings", s, "--cwd", tmp_path, "--list"])
    assert (result(cp)[0], cp.returncode) == ("ERROR", 2)


def test_a_hook_that_cannot_be_started_is_noise_not_a_traceback(tmp_path):
    # a command longer than the OS argument limit makes the spawn itself fail
    s = settings(tmp_path, [{"command": "x" * 3_000_000}])
    cp = tool([target(tmp_path), "--settings", s, "--cwd", tmp_path])
    status, f = result(cp)
    assert (status, cp.returncode, f["noise"]) == ("INCOMPLETE", 3, "1")
    assert "could not start" in cp.stdout


def test_a_symlink_to_a_guard_file_is_refused(tmp_path):
    h = stub(tmp_path, "h.sh")
    real = tmp_path / "exec-bit-guard.sh"
    real.write_text("x\n")
    link = tmp_path / "innocent.txt"
    link.symlink_to(real)
    s = settings(tmp_path, [{"command": str(h)}])
    cp = tool([link, "--settings", s, "--cwd", tmp_path])
    assert (result(cp)[0], cp.returncode) == ("ERROR", 2)
    assert runs(tmp_path, "h.sh") == 0


def test_a_symlink_named_for_the_guard_is_refused_whatever_it_points_at(tmp_path):
    # the link's own name matches; its target does not — only the basename half of the check sees it
    h = stub(tmp_path, "h.sh")
    plain = tmp_path / "plain.txt"
    plain.write_text("x\n")
    link = tmp_path / "exec-bit-guard-link.sh"
    link.symlink_to(plain)
    s = settings(tmp_path, [{"command": str(h)}])
    cp = tool([link, "--settings", s, "--cwd", tmp_path])
    assert (result(cp)[0], cp.returncode) == ("ERROR", 2)
    assert runs(tmp_path, "h.sh") == 0


def test_a_closed_stdout_never_exits_zero(tmp_path):
    h = stub(tmp_path, "h.sh")
    s = settings(tmp_path, [{"command": str(h)}])
    cmd = 'exec "$0" "$1" "$2" --settings "$3" --cwd "$4" >&-'
    cp = subprocess.run(
        [
            "bash",
            "-c",
            cmd,
            sys.executable,
            str(TOOL),
            str(target(tmp_path)),
            str(s),
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert runs(tmp_path, "h.sh") == 1
    assert cp.returncode == 2


def test_a_hook_that_never_reads_a_large_payload_is_still_stopped_on_time(tmp_path):
    # the payload outgrows the pipe buffer, so a blocking write would stall the tool past the deadline
    h = tmp_path / "deaf.sh"
    h.write_text("#!/bin/sh\nsleep 30\n")
    h.chmod(0o755)
    s = settings(
        tmp_path,
        [{"event": "PreToolUse", "matcher": "Bash", "command": str(h), "timeout": 1}],
    )
    t0 = time.monotonic()
    cp = tool(
        [
            "--event",
            "PreToolUse",
            "--tool",
            "Bash",
            "--command",
            "x" * 300_000,
            "--settings",
            s,
            "--cwd",
            tmp_path,
        ],
        timeout=25,
    )
    assert time.monotonic() - t0 < 20
    assert result(cp)[1]["killed"] == "1"


def test_a_path_with_a_space_and_non_ascii_reaches_the_hook_intact(tmp_path):
    d = tmp_path / "sp ace é"
    d.mkdir()
    f = d / "f.txt"
    f.write_text("x\n")
    h = stub(tmp_path, "h.sh")
    s = settings(tmp_path, [{"command": str(h)}])
    assert result(tool([f, "--settings", s, "--cwd", tmp_path]))[0] == "PASS"
    got = json.loads((tmp_path / "h.sh.rec").read_text())
    assert got["tool_input"]["file_path"] == str(f)


def test_a_relative_path_is_sent_absolute(tmp_path):
    h = stub(tmp_path, "h.sh")
    s = settings(tmp_path, [{"command": str(h)}])
    target(tmp_path)
    assert (
        result(tool(["f.txt", "--settings", s, "--cwd", tmp_path], cwd=tmp_path))[0]
        == "PASS"
    )
    got = json.loads((tmp_path / "h.sh.rec").read_text())["tool_input"]["file_path"]
    assert os.path.realpath(got) == os.path.realpath(
        tmp_path / "f.txt"
    ) and os.path.isabs(got)


def test_the_tool_leaves_nothing_behind_in_its_cwd(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    h = stub(tmp_path, "h.sh")
    s = settings(tmp_path, [{"command": str(h)}])
    result(tool([target(tmp_path), "--settings", s, "--cwd", work], cwd=work))
    assert list(work.iterdir()) == []


def test_a_guard_registration_in_any_letter_case_is_skipped(tmp_path):
    forbidden = stub(tmp_path, "Exec-Bit-Guard.sh")
    status, f = result(edit(tmp_path, [{"command": str(forbidden)}]))
    assert (status, f["skipped"]) == ("INCOMPLETE", "1")
    assert runs(tmp_path, "Exec-Bit-Guard.sh") == 0


def test_a_guard_named_past_the_script_word_is_skipped(tmp_path):
    # the script itself is innocent, so only the registration-text rule can see the guard
    innocent = stub(tmp_path, "innocent.sh")
    cp = edit(tmp_path, [{"command": "%s exec-bit-guard.sh" % innocent}])
    status, f = result(cp)
    assert (status, f["skipped"]) == ("INCOMPLETE", "1"), cp.stdout
    assert "standing constraint: never run exec-bit-guard*" in cp.stdout
    assert runs(tmp_path, "innocent.sh") == 0


@pytest.mark.parametrize("farm", [False, True])
def test_a_registered_symlink_to_the_guard_is_skipped(tmp_path, farm):
    # the registration's own text is innocent; only its resolved target names the guard
    real = stub(tmp_path, "exec_bit_guard_real.sh")
    home = tmp_path / "home"
    scripts = home / ".claude" / "scripts"
    scripts.mkdir(parents=True)
    link = scripts / "innocent.sh"
    link.symlink_to(real)
    command = "$HOME/.claude/scripts/innocent.sh" if farm else str(link)
    cp = edit(tmp_path, [{"command": command}], env={"HOME": str(home)})
    status, f = result(cp)
    assert (status, f["skipped"]) == ("INCOMPLETE", "1"), cp.stdout
    assert runs(tmp_path, "exec_bit_guard_real.sh") == 0


def test_a_nul_byte_in_a_command_is_noise_not_a_traceback(tmp_path):
    s = settings(tmp_path, [{"command": "true\x00false"}])
    cp = tool([target(tmp_path), "--settings", s, "--cwd", tmp_path])
    status, f = result(cp)
    assert (status, cp.returncode, f["noise"]) == ("INCOMPLETE", 3, "1")
    assert "could not start" in cp.stdout and "Traceback" not in cp.stderr


def test_a_dotdot_path_resolves_physically_through_a_symlinked_cwd(tmp_path):
    sub = tmp_path / "real" / "sub"
    sub.mkdir(parents=True)
    link = tmp_path / "link"
    link.symlink_to(sub)
    # the file is NOT at tmp_path/f.py, where a lexical `..` lands
    (tmp_path / "real" / "f.py").write_text("x\n")
    h = stub(tmp_path, "h.sh")
    s = settings(tmp_path, [{"command": str(h)}])
    cp = tool(["../f.py", "--settings", s], cwd=link, env={"PWD": str(link)})
    assert result(cp)[0] == "PASS", cp.stdout + cp.stderr
    want = Path(os.path.realpath(tmp_path / "real")) / "f.py"
    assert "subject:  PostToolUse Edit %s " % want in cp.stdout
    got = json.loads((tmp_path / "h.sh.rec").read_text())
    assert got["tool_input"]["file_path"] == str(want)


@pytest.mark.parametrize("pwd_is_current", [True, False])
def test_the_default_cwd_is_the_logical_pwd_when_it_names_this_directory(
    tmp_path, pwd_is_current
):
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real)
    (real / "f.txt").write_text("x\n")
    h = stub(
        tmp_path, "h.sh", "echo \"$CLAUDE_PROJECT_DIR\" > '%s/proj.out'" % tmp_path
    )
    s = settings(tmp_path, [{"command": str(h)}])
    # a stale $PWD (naming some other directory) must not be believed
    pwd = str(link) if pwd_is_current else str(tmp_path)
    cp = tool(["f.txt", "--settings", s], cwd=link, env={"PWD": pwd})
    assert result(cp)[0] == "PASS", cp.stdout
    want = link if pwd_is_current else Path(os.path.realpath(real))
    got = json.loads((tmp_path / "h.sh.rec").read_text())
    assert (tmp_path / "proj.out").read_text().strip() == str(want)
    assert got["cwd"] == str(want)
    assert got["tool_input"]["file_path"] == str(want / "f.txt")

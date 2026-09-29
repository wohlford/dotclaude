"""Tests for scripts/guard-parity.py.

Each row names the property it pins. Rows that stand in for a defect this tool exists to catch say
which: a comparison of two absent readings, a fast path that drifts from the shipped process, a
clock or a fixture that lets every row read "allowed".
"""

import fcntl
import importlib.util
import json
import os
import shutil
import signal
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
MODULE = REPO / "scripts" / "guard-parity.py"


def load():
    spec = importlib.util.spec_from_file_location("guard_parity", MODULE)
    module = importlib.util.module_from_spec(spec)
    sys.modules["guard_parity"] = module
    spec.loader.exec_module(module)
    return module


gp = load()


# ---------------------------------------------------------------------------------------------
# corpus
# ---------------------------------------------------------------------------------------------


def bash_record(command, cwd="/work/a", **extra):
    block = {"type": "tool_use", "name": "Bash", "input": {"command": command, **extra}}
    return {"cwd": cwd, "message": {"content": [block]}}


def write_transcript(root, name, records, raw_lines=()):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "".join(json.dumps(r) + "\n" for r in records) + "".join(raw_lines)
    path.write_text(text, encoding="utf-8")
    return path


def test_corpus_dedupes_on_cwd_and_command(tmp_path):
    write_transcript(
        tmp_path,
        "p/s.jsonl",
        [
            bash_record("ls", "/a"),
            bash_record("ls", "/a"),  # same row again
            bash_record("ls", "/b"),  # same command, another cwd: a distinct row
        ],
    )
    rows, stats = gp.extract_corpus(tmp_path)
    assert len(rows) == 2 and stats.records == 3 and stats.transcripts == 1


def test_corpus_reaches_subagent_transcripts_and_a_symlinked_root(tmp_path):
    real = tmp_path / "real"
    write_transcript(real, "proj/sess/subagents/agent-1.jsonl", [bash_record("pwd")])
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    rows, stats = gp.extract_corpus(link)
    assert [r.tool_input["command"] for r in rows] == ["pwd"]
    assert (
        stats.transcripts == 1
    )  # a root that yields no transcripts is what the floor catches


def test_corpus_follows_a_symlinked_project_directory(tmp_path):
    elsewhere = tmp_path / "elsewhere"
    write_transcript(elsewhere, "s.jsonl", [bash_record("pwd")])
    root = tmp_path / "root"
    root.mkdir()
    (root / "proj").symlink_to(elsewhere, target_is_directory=True)
    rows, _stats = gp.extract_corpus(root)
    assert [r.tool_input["command"] for r in rows] == ["pwd"]


def test_a_symlink_cycle_under_the_root_terminates_and_counts_each_transcript_once(
    tmp_path,
):
    root = tmp_path / "root"
    write_transcript(root, "proj/s.jsonl", [bash_record("pwd")])
    (root / "proj" / "back").symlink_to(root, target_is_directory=True)  # a cycle
    rows, stats = gp.extract_corpus(root)
    assert stats.transcripts == 1 and len(rows) == 1


def test_corpus_counts_every_skip_reason_and_keeps_the_whole_input(tmp_path):
    read = {
        "cwd": "/a",
        "message": {
            "content": [
                {"type": "tool_use", "name": "Read", "input": {"file_path": "/x"}}
            ]
        },
    }
    write_transcript(
        tmp_path,
        "s.jsonl",
        [
            bash_record("keep", "/a", description="why"),
            {
                "cwd": "/a",
                "message": {
                    "content": [
                        {"type": "tool_use", "name": "Bash", "input": "not an object"}
                    ]
                },
            },
            bash_record(7, "/a"),
            bash_record("", "/a"),
            bash_record("nocwd", cwd=None),
            read,
            {"cwd": "/a", "message": "Bash"},
            {"cwd": "/a", "message": {"content": "Bash"}},
        ],
        raw_lines=['{"this is": "Bash" but not json\n', '"Bash"\n'],
    )
    rows, stats = gp.extract_corpus(tmp_path)
    assert [r.tool_input for r in rows] == [{"command": "keep", "description": "why"}]
    assert stats.skipped == {
        "non_object_input": 1,
        "non_string_command": 1,
        "empty_command": 1,
        "no_cwd": 1,
        "bad_json_line": 1,
        "non_object_record": 1,
        "non_object_message": 1,
        "non_list_content": 1,
    }


def test_corpus_order_is_independent_of_enumeration_order(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    recs = [bash_record(f"echo {i}") for i in range(20)]
    write_transcript(a, "1.jsonl", recs[:10])
    write_transcript(a, "2.jsonl", recs[10:])
    write_transcript(b, "z.jsonl", recs[10:])
    write_transcript(b, "y.jsonl", recs[:10])
    ids_a = [r.id for r in gp.extract_corpus(a)[0]]
    ids_b = [r.id for r in gp.extract_corpus(b)[0]]
    assert ids_a == ids_b and len(ids_a) == 20
    assert ids_a != sorted(ids_a)  # a pseudo-shuffle, not a lexical sort
    assert gp.corpus_digest(gp.extract_corpus(a)[0]) == gp.corpus_digest(
        gp.extract_corpus(b)[0]
    )


def test_corpus_survives_a_lone_surrogate(tmp_path):
    path = tmp_path / "s.jsonl"
    path.write_text(
        '{"cwd": "/a", "message": {"content": [{"type": "tool_use", "name": "Bash", '
        '"input": {"command": "echo \\ud800"}}]}}\n',
        encoding="utf-8",
    )
    rows, _stats = gp.extract_corpus(tmp_path)
    assert len(rows) == 1 and len(rows[0].id) == 16


def test_corpus_round_trips_and_refuses_a_tampered_cut_or_padded_file(tmp_path):
    write_transcript(
        tmp_path / "t", "s.jsonl", [bash_record(f"echo {i}") for i in range(6)]
    )
    rows, _stats = gp.extract_corpus(tmp_path / "t")
    frozen = tmp_path / "corpus.jsonl"
    gp.write_corpus(rows, frozen)
    assert gp.read_corpus(frozen) == rows
    lines = frozen.read_text().splitlines()

    def rewrite(*kept):
        frozen.write_text("\n".join(kept) + "\n")

    tampered = json.loads(lines[1])
    tampered["tool_input"]["command"] = "rm -rf /"  # a row whose command was edited
    rewrite(lines[0], json.dumps(tampered), *lines[2:])
    with pytest.raises(gp.CorpusError, match="does not match its cwd"):
        gp.read_corpus(frozen)
    rewrite(*lines[:4])  # cut at a line boundary: every row left is valid
    with pytest.raises(gp.CorpusError, match="header says 6"):
        gp.read_corpus(frozen)
    rewrite(*lines, lines[1])  # padded with one repeated row
    with pytest.raises(gp.CorpusError, match="duplicate row id"):
        gp.read_corpus(frozen)
    other = gp.Row(gp.row_id("/x", "other"), "/x", {"command": "other"})
    rewrite(
        lines[0], json.dumps(other._asdict()), *lines[2:]
    )  # same count, another row
    with pytest.raises(gp.CorpusError, match="digest"):
        gp.read_corpus(frozen)
    rewrite(*lines[1:])  # rows with no header
    with pytest.raises(gp.CorpusError, match="no corpus header"):
        gp.read_corpus(frozen)
    frozen.write_text("not json\n")
    with pytest.raises(gp.CorpusError, match="no corpus header"):
        gp.read_corpus(frozen)
    rewrite(lines[0], "not json")
    with pytest.raises(gp.CorpusError, match="malformed"):
        gp.read_corpus(frozen)


def test_corpus_refuses_an_id_collision(tmp_path, monkeypatch):
    write_transcript(tmp_path, "s.jsonl", [bash_record("ls"), bash_record("pwd")])
    monkeypatch.setattr(gp, "row_id", lambda cwd, command: "0" * 16)
    with pytest.raises(gp.CorpusError, match="collision"):
        gp.extract_corpus(tmp_path)


# ---------------------------------------------------------------------------------------------
# builds
# ---------------------------------------------------------------------------------------------


def git(root, *args):
    return subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, check=True
    ).stdout.strip()


def make_repo(tmp_path, files, name="repo"):
    """A repo whose commit carries `files` ({path: text or (text, executable)}); returns
    (root, sha)."""
    root = tmp_path / name
    root.mkdir()
    git(root, "init", "-q", "-b", "dev")
    for key, value in (
        ("user.email", "t@t"),
        ("user.name", "t"),
        ("commit.gpgsign", "false"),
        ("tag.gpgsign", "false"),
    ):
        git(root, "config", key, value)
    for rel, value in files.items():
        text, executable = value if isinstance(value, tuple) else (value, False)
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        if executable:
            path.chmod(0o755)
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "seed")
    return root, git(root, "rev-parse", "HEAD")


SCRIPTS = {
    "scripts/push-guard.py": ("#!/usr/bin/env python3\nraise SystemExit(0)\n", True),
    "scripts/lib/helper.py": "VALUE = 1\n",
}


def test_pin_extracts_and_verifies_every_file(tmp_path):
    root, sha = make_repo(tmp_path, SCRIPTS)
    build = gp.pin_build(root, "dev", "old", tmp_path / "build-old")
    assert build.sha == sha and len(sha) == 40 and build.files == 2
    assert (build.root / "scripts" / "lib" / "helper.py").read_text() == "VALUE = 1\n"
    assert os.access(build.root / "scripts" / "push-guard.py", os.X_OK)


def test_pin_refuses_an_unknown_rev_and_a_rev_without_scripts(tmp_path):
    root, _sha = make_repo(tmp_path, SCRIPTS)
    with pytest.raises(gp.PinError, match="does not name a commit"):
        gp.pin_build(root, "no-such-rev", "old", tmp_path / "b1")
    bare, _ = make_repo(tmp_path, {"README.md": "x\n"}, name="bare")
    with pytest.raises(gp.PinError, match="cannot unpack"):
        gp.pin_build(bare, "dev", "new", tmp_path / "b2")


def test_pin_reuses_a_verified_directory_and_rebuilds_a_stale_or_damaged_one(tmp_path):
    root, first = make_repo(tmp_path, SCRIPTS)
    dest = tmp_path / "build-old"
    gp.pin_build(root, "dev", "old", dest)
    cache = dest / "scripts" / "lib" / "__pycache__" / "helper.cpython-313.pyc"
    cache.parent.mkdir()
    cache.write_text("c")  # what running the guards leaves behind: no part of the proof
    assert (
        gp.pin_build(root, "dev", "old", dest).files == 2 and cache.exists()
    )  # a reuse
    stray = dest / "scripts" / "lib" / "not_in_the_tree.py"
    stray.write_text("x = 1\n")  # a file the tree does not have
    assert gp.pin_build(root, "dev", "old", dest).files == 2
    assert not stray.exists() and not cache.exists()  # rebuilt, not patched
    # damaged: content, then the exec bit
    (dest / "scripts" / "lib" / "helper.py").write_text("VALUE = 2\n")
    assert gp.pin_build(root, "dev", "old", dest).files == 2
    assert (dest / "scripts" / "lib" / "helper.py").read_text() == "VALUE = 1\n"
    (dest / "scripts" / "push-guard.py").chmod(0o644)
    gp.pin_build(root, "dev", "old", dest)
    assert os.access(dest / "scripts" / "push-guard.py", os.X_OK)
    # stale: the directory holds another commit's scripts
    (root / "scripts" / "lib" / "helper.py").write_text("VALUE = 3\n")
    git(root, "commit", "-q", "-am", "second")
    second = gp.pin_build(root, "dev", "old", dest)
    assert (dest / "scripts" / "lib" / "helper.py").read_text() == "VALUE = 3\n"
    assert second.sha != first
    # a later revision that DELETES a file: what the old directory still holds must not survive
    git(root, "rm", "-q", "scripts/lib/helper.py")
    git(root, "commit", "-q", "-m", "third")
    third = gp.pin_build(root, "dev", "old", dest)
    assert third.files == 1 and not (dest / "scripts" / "lib" / "helper.py").exists()


def test_pin_notices_a_file_git_archive_left_out(tmp_path):
    files = dict(SCRIPTS)
    files[".gitattributes"] = "scripts/lib/helper.py export-ignore\n"
    root, _sha = make_repo(tmp_path, files)
    with pytest.raises(gp.PinError, match=r"helper\.py"):
        gp.pin_build(root, "dev", "old", tmp_path / "b")


def test_uncommitted_reports_modified_and_untracked_scripts_and_only_those(tmp_path):
    root, _sha = make_repo(tmp_path, {**SCRIPTS, "README.md": "r\n"})
    assert gp.uncommitted(root) == []
    (root / "scripts" / "lib" / "helper.py").write_text("VALUE = 9\n")  # modified
    (root / "scripts" / "new.py").write_text("x\n")  # untracked
    (root / "README.md").write_text("changed\n")  # outside scripts/
    assert sorted(gp.uncommitted(root)) == ["scripts/lib/helper.py", "scripts/new.py"]
    git(root, "config", "status.showUntrackedFiles", "no")
    assert "scripts/new.py" in gp.uncommitted(
        root
    )  # the repo's own setting cannot hide it


def test_require_guards_names_the_build_and_the_file(tmp_path):
    root, _sha = make_repo(tmp_path, SCRIPTS)
    build = gp.pin_build(root, "dev", "new", tmp_path / "b")
    gp.require_guards([build], ["push-guard.py"])
    with pytest.raises(gp.PinError, match=r"new: scripts/absent-guard\.py is missing"):
        gp.require_guards([build], ["push-guard.py", "absent-guard.py"])


def settings(*entries):
    return json.dumps({"hooks": {"PreToolUse": list(entries)}})


def hook(matcher, command, timeout=None):
    entry = {"matcher": matcher, "hooks": [{"type": "command", "command": command}]}
    if timeout is not None:
        entry["hooks"][0]["timeout"] = timeout
    return entry


def test_registrations_read_only_hooks_that_fire_on_bash(tmp_path):
    root, sha = make_repo(
        tmp_path,
        {
            "settings.json": settings(
                hook("Bash", "$HOME/.claude/scripts/push-guard.py"),
                hook("Bash|Edit", "$HOME/.claude/scripts/a-guard.sh", 60),
                hook("", "$HOME/.claude/scripts/every.py"),
                hook("Edit", "$HOME/.claude/scripts/edit-only.py"),
                hook("(", "$HOME/.claude/scripts/broken-regex.py"),
            )
        },
    )
    got = gp.registrations(root, sha)
    assert got == {
        ("Bash", "push-guard.py", None),
        ("Bash|Edit", "a-guard.sh", 60),
        ("", "every.py", None),
        (
            "(",
            "broken-regex.py",
            None,
        ),  # unreadable as a regex: kept, never silently dropped
    }


def test_registrations_none_without_settings_and_error_on_bad_json(tmp_path):
    root, sha = make_repo(tmp_path, SCRIPTS)
    assert gp.registrations(root, sha) is None
    bad, bad_sha = make_repo(tmp_path, {"settings.json": "{"}, name="bad")
    with pytest.raises(gp.PinError, match="not valid JSON"):
        gp.registrations(bad, bad_sha)
    listy, listy_sha = make_repo(tmp_path, {"settings.json": "[]"}, name="listy")
    with pytest.raises(gp.PinError, match="not a JSON object"):
        gp.registrations(listy, listy_sha)
    shaped, shaped_sha = make_repo(
        tmp_path, {"settings.json": '{"hooks": {"PreToolUse": [5]}}'}, name="shaped"
    )
    with pytest.raises(gp.PinError, match="unexpected shape"):
        gp.registrations(shaped, shaped_sha)


def test_registration_delta_reports_added_removed_and_retimed():
    old = {
        ("Bash", "push-guard.py", None),
        ("Bash", "gone.py", None),
        ("Bash", "t.py", 60),
    }
    new = {
        ("Bash", "push-guard.py", None),
        ("Bash", "added.py", None),
        ("Bash", "t.py", 30),
    }
    delta = gp.registration_delta(old, new)
    assert sorted(delta) == [
        "+ PreToolUse[Bash] added.py timeout=None",
        "+ PreToolUse[Bash] t.py timeout=30",
        "- PreToolUse[Bash] gone.py timeout=None",
        "- PreToolUse[Bash] t.py timeout=60",
    ]
    assert gp.registration_delta(None, None) == []
    assert gp.registration_delta(old, old) == []


def test_unjudged_registered_names_the_reason():
    regs = {
        ("Bash", "push-guard.py", None),
        ("Bash", "recast-commit-gate.py", 600),
        ("Bash", "mystery.py", None),
    }
    got = dict(gp.unjudged_registered(regs, ["push-guard.py"]))
    assert set(got) == {"recast-commit-gate.py", "mystery.py"}
    assert "pytest" in got["recast-commit-gate.py"]
    assert got["mystery.py"] == "not covered by this tool"


# ---------------------------------------------------------------------------------------------
# replay environment, fixture home, canaries
# ---------------------------------------------------------------------------------------------


def test_timing_pattern_is_read_the_way_the_guard_reads_it(tmp_path):
    (tmp_path / ".claude").mkdir()
    conf = tmp_path / ".claude" / ".git-timing-guard.conf"
    conf.write_text(
        "GUARD_REPO_PATTERN=\"old/x\"\nGUARD_REPO_PATTERN= 'wohl ford/dot'\r\n"
    )
    assert gp.read_timing_pattern(tmp_path, None) == ("wohlford/dot", str(conf))
    assert gp.read_timing_pattern(tmp_path, "over/ride") == (
        "over/ride",
        "--timing-pattern",
    )
    conf.write_text("GUARD_REPO_PATTERN=\n")
    assert gp.read_timing_pattern(tmp_path, None)[0] is None
    conf.unlink()
    pattern, why = gp.read_timing_pattern(tmp_path, None)
    assert pattern is None and "no readable" in why


def test_fixture_home_mirrors_everything_but_dot_claude(tmp_path):
    real = tmp_path / "real"
    (real / ".claude").mkdir(parents=True)
    (real / ".claude" / "secret").write_text("s")
    (real / ".config").mkdir()
    (real / ".gitconfig").write_text("[user]\n\tname = op\n")
    fixture = gp.make_fixture_home(tmp_path / "fix", real, "wohlford/dotclaude")
    assert (fixture / ".gitconfig").is_symlink()
    assert (fixture / ".gitconfig").read_text() == "[user]\n\tname = op\n"
    assert (fixture / ".config").is_symlink()
    assert not (fixture / ".claude").is_symlink() and (fixture / ".claude").is_dir()
    assert sorted(p.name for p in (fixture / ".claude").iterdir()) == [
        ".git-timing-guard.conf"
    ]
    conf = (fixture / ".claude" / ".git-timing-guard.conf").read_text()
    assert conf.splitlines() == [
        "GUARD_REPO_PATTERN=wohlford/dotclaude",
        "GUARD_DAYS=1-7",
        "GUARD_START=0000",
        "GUARD_END=2400",
    ]


def test_fixture_home_without_a_pattern_writes_no_conf_and_refuses_a_mangled_one(
    tmp_path,
):
    real = tmp_path / "real"
    real.mkdir()
    fixture = gp.make_fixture_home(tmp_path / "fix", real, None)
    assert list((fixture / ".claude").iterdir()) == []
    for n, bad in enumerate(("has space", 'has"quote', "has\nnewline")):
        with pytest.raises(gp.FixtureError, match="would strip"):
            gp.make_fixture_home(tmp_path / f"f{n}", real, bad)
    with pytest.raises(gp.FixtureError, match="cannot read HOME"):
        gp.make_fixture_home(tmp_path / "f2", tmp_path / "absent", None)


def test_replay_env_moves_three_names_without_touching_its_input(tmp_path):
    base = {"PATH": "/usr/bin", "HOME": "/real", "TOKEN": "s3cret"}
    env = gp.replay_env(base, tmp_path / "fix", tmp_path / "log")
    assert base == {"PATH": "/usr/bin", "HOME": "/real", "TOKEN": "s3cret"}
    assert env["HOME"] == str(tmp_path / "fix")
    assert env["PUBLICATION_PUSH_GUARD_LOG"] == str(tmp_path / "log")
    assert env["PYTHONHASHSEED"] == "0" and env["TOKEN"] == "s3cret"
    # handed to children per call, never exported into this process
    assert os.environ.get("PUBLICATION_PUSH_GUARD_LOG") != str(tmp_path / "log")
    assert os.environ.get("HOME") != str(tmp_path / "fix")


def test_env_values_digest_ignores_only_the_names_a_shell_rewrites_per_invocation():
    base = {
        "PATH": "/bin",
        "TOKEN": "s1",
        "_": "/usr/bin/a",
        "SHLVL": "1",
        "OLDPWD": "/x",
    }
    a = gp.env_values_digest(base)
    # what a fresh shell changes on its own must not make a resume useless
    assert a == gp.env_values_digest(dict(base, _="/usr/bin/b", SHLVL="3", OLDPWD="/y"))
    # any other value can change a reading, so it must move the digest
    assert a != gp.env_values_digest(dict(base, TOKEN="s2"))
    assert a != gp.env_values_digest(dict(base, PATH="/usr/bin"))


def test_env_names_digest_reads_names_only():
    a = gp.env_names_digest({"A": "1", "B": "2"})
    assert a == gp.env_names_digest({"B": "other", "A": "values"})
    assert a != gp.env_names_digest({"A": "1", "B": "2", "C": "3"})
    assert len(a) == gp.DIGEST_LEN


def genuine(guard, row, home, tmp_path):
    """Run a guard from THIS repo exactly as the harness does: a real process, JSON on stdin."""
    payload = json.dumps(
        {"tool_name": "Bash", "tool_input": row.tool_input, "cwd": row.cwd}
    ).encode()
    env = gp.replay_env(os.environ, home, tmp_path / "guard.log")
    done = subprocess.run(
        [str(REPO / "scripts" / guard)],
        input=payload,
        capture_output=True,
        env=env,
        cwd=tmp_path,
        check=False,
    )
    return done.returncode


def test_every_canary_is_refused_by_its_real_guard_and_a_control_is_not(tmp_path):
    """The canaries are only worth having if the real guard refuses each one under the replay
    environment; the paired benign command is what shows the refusal is the guard's, not the
    fixture's."""
    real = tmp_path / "real"
    real.mkdir()
    home = gp.make_fixture_home(tmp_path / "fix", real, "example.invalid/pat")
    rows = gp.build_canaries(tmp_path / "canaries", "example.invalid/pat", gp.JUDGED)
    assert [gp.canary_guard(r.id) for r in rows] == list(gp.JUDGED)
    for row in rows:
        guard = gp.canary_guard(row.id)
        assert genuine(guard, row, home, tmp_path) == 2, guard
        benign = gp.Row("x", row.cwd, {"command": "git status"})
        assert genuine(guard, benign, home, tmp_path) == 0, guard


def test_canaries_refuse_what_they_cannot_build(tmp_path):
    with pytest.raises(gp.FixtureError, match="needs a repo pattern"):
        gp.build_canaries(tmp_path / "c", None, ["git-timing-guard.py"])
    with pytest.raises(gp.FixtureError, match="no canary is defined"):
        gp.build_canaries(tmp_path / "c2", None, ["mystery-guard.py"])
    assert gp.canary_guard("0123456789abcdef") is None


# ---------------------------------------------------------------------------------------------
# worker: fork server, exit-shape translation, agreement with a genuine process
# ---------------------------------------------------------------------------------------------

GUARD_FILE = "push-guard.py"


def fake_build(tmp_path, sources, name="build", lib=None):
    """A `scripts/` directory of tiny guards ({file name: python source}); returns its root."""
    root = tmp_path / name
    (root / "scripts").mkdir(parents=True)
    for file_name, source in sources.items():
        path = root / "scripts" / file_name
        path.write_text(
            "#!/usr/bin/env python3\n" + textwrap.dedent(source), encoding="utf-8"
        )
        path.chmod(0o755)
    for file_name, source in (lib or {}).items():
        (root / "scripts" / "lib").mkdir(exist_ok=True)
        (root / "scripts" / "lib" / file_name).write_text(textwrap.dedent(source))
    return root


def run_worker(
    tmp_path,
    build,
    rows,
    guards=(GUARD_FILE,),
    mode="fork",
    repeat=1,
    timeout=30,
    cwd=None,
):
    """Drive the worker as a real process, the shape that ships; returns {(id, guard): [Reading]}
    plus the raw lines."""
    neutral = tmp_path / "neutral"
    neutral.mkdir(exist_ok=True)
    rows_file = tmp_path / f"rows-{mode}.jsonl"
    rows_file.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    out = tmp_path / f"out-{mode}.jsonl"
    env = dict(os.environ, HOME=str(tmp_path), PYTHONHASHSEED="0")
    done = subprocess.run(
        [
            sys.executable,
            str(MODULE),
            "--worker",
            "--build",
            str(build),
            "--guards",
            ",".join(guards),
            "--rows",
            str(rows_file),
            "--out",
            str(out),
            "--neutral",
            str(neutral),
            "--mode",
            mode,
            "--repeat",
            str(repeat),
            "--timeout",
            str(timeout),
        ],
        capture_output=True,
        text=True,
        env=env,
        stdin=subprocess.DEVNULL,
        cwd=cwd,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    lines = [json.loads(line) for line in out.read_text().splitlines()]
    units = {
        (ln["id"], ln["guard"]): [gp.Reading.from_json(r) for r in ln["readings"]]
        for ln in lines
        if "guard" in ln
    }
    return units, lines


def spec(command, identity=None, **extra):
    return {
        "id": identity or command,
        "cwd": "/work",
        "tool_input": {"command": command},
        **extra,
    }


BLOCKER = """
    import json, sys
    data = json.load(sys.stdin)
    if "BLOCKME" in data["tool_input"]["command"]:
        sys.stderr.write("refused: BLOCKME\\n")
        sys.exit(2)
"""


def test_a_block_and_an_allow_read_as_rc_2_and_rc_0(tmp_path):
    build = fake_build(tmp_path, {GUARD_FILE: BLOCKER})
    units, _lines = run_worker(tmp_path, build, [spec("ls"), spec("echo BLOCKME")])
    allow = units[("ls", GUARD_FILE)][0]
    block = units[("echo BLOCKME", GUARD_FILE)][0]
    assert (allow.rc, allow.text) == (0, "")
    assert (block.rc, block.text) == (2, "refused: BLOCKME\n")
    assert allow.out == block.out  # nothing on stdout in either


# Each body is a whole guard. The fork server must read every one exactly as a genuine process
# does: rc, stdout digest, stderr digest and stderr text.
EXIT_SHAPES = {
    "allow": "pass",
    "block": "import sys; sys.stderr.write('no\\n'); sys.exit(2)",
    "exit-none": "import sys; sys.exit(None)",
    "exit-3": "import sys; sys.exit(3)",
    "exit-string": "import sys; sys.exit('a message')",
    "exit-256": "import sys; sys.exit(256)",
    "exit-wider-than-c-int": "import sys; sys.exit(2**40 + 5)",
    "exit-wider-than-c-long": "import sys; sys.exit(2**70)",
    "exit-negative": "import sys; sys.exit(-3)",
    "undefined-annotation": "def f(x: Undefined) -> None: pass\nprint(f.__annotations__)",
    "uncaught": "raise RuntimeError('boom')",
    "os-exit": "import os; os._exit(7)",
    "stdout-only": "print('to stdout')",
    "non-ascii": "print('h\\u00e9llo \\u2713'); import sys; sys.stderr.write('\\u00e9\\n')",
    "big-output": "import sys; sys.stdout.write('x' * 300000); sys.stderr.write('y' * 300000)",
    "stdout-closed": "import os, sys; sys.stdout.write('lost'); os.close(1)",
    "reads-stdin": "import json, sys; d = json.load(sys.stdin); print(d['tool_name'], d['cwd'])",
    "sibling-path": "import sys; print(sys.path[0]); print(sys.argv[0]); print(__file__)",
    "shows-name": "print(__name__)",
    "shows-main-module": (
        "import sys\nm = sys.modules['__main__']\n"
        "print(m.__file__ == __file__, m.__spec__, m.__package__, m.__cached__, "
        "type(m.__loader__).__name__, m.__doc__)"
    ),
    "shows-docstring": '"""the docstring"""\nimport sys\nprint(sys.modules["__main__"].__doc__)',
    "shows-sys-path": "import sys\nprint(sys.path)",
    "shows-open-fds": "import os\nprint(sorted(int(n) for n in os.listdir('/dev/fd')))",
    "own-traceback": "import traceback\ntry:\n    1 / 0\nexcept ZeroDivisionError:\n    traceback.print_exc()",
}


@pytest.mark.parametrize("shape", sorted(EXIT_SHAPES))
def test_the_fork_server_reads_every_exit_shape_as_a_genuine_process_does(
    tmp_path, shape
):
    build = fake_build(tmp_path, {GUARD_FILE: EXIT_SHAPES[shape] + "\n"})
    rows = [spec("ls")]
    forked, _ = run_worker(tmp_path, build, rows, mode="fork")
    genuine_, _ = run_worker(tmp_path, build, rows, mode="exec")
    assert forked == genuine_, shape


def test_the_shapes_are_not_all_the_same_reading(tmp_path):
    """A table whose every row agrees proves nothing if every row read identically."""
    readings = set()
    for shape in (
        "allow",
        "block",
        "exit-3",
        "uncaught",
        "stdout-closed",
        "exit-string",
    ):
        build = fake_build(
            tmp_path, {GUARD_FILE: EXIT_SHAPES[shape] + "\n"}, name=shape
        )
        units, _ = run_worker(tmp_path, build, [spec("ls")], mode="fork")
        readings.add(units[("ls", GUARD_FILE)][0])
    assert len(readings) == 6
    codes = {r.rc for r in readings}
    assert codes == {0, 1, 2, 3, 120}


def test_a_closed_stdout_is_status_120_as_the_interpreter_reports_it(tmp_path):
    build = fake_build(tmp_path, {GUARD_FILE: EXIT_SHAPES["stdout-closed"] + "\n"})
    units, _ = run_worker(tmp_path, build, [spec("ls")], mode="fork")
    assert units[("ls", GUARD_FILE)][0].rc == 120


def test_a_build_reached_through_a_symlink_normalises_to_the_same_reading(tmp_path):
    # `__file__` is the path as invoked; the real guards also print `Path(__file__).resolve()`,
    # which is where a symlinked build shows its other spelling.
    source = (
        "import os, sys\n"
        "print(__file__, os.path.realpath(__file__))\n"
        "sys.stderr.write(__file__ + ' ' + os.path.realpath(__file__) + '\\n')\n"
    )
    real_a = fake_build(tmp_path, {GUARD_FILE: source}, name="real-a")
    real_b = fake_build(tmp_path, {GUARD_FILE: source}, name="real-b")
    link = tmp_path / "linked-b"
    link.symlink_to(real_b, target_is_directory=True)
    a, _ = run_worker(tmp_path, real_a, [spec("ls")], mode="fork")
    b, _ = run_worker(tmp_path, link, [spec("ls")], mode="fork")
    assert (
        a == b
    )  # two different directories, one reading, whichever way each is spelled


def test_a_hang_reads_as_timed_out_never_as_a_verdict(tmp_path):
    build = fake_build(tmp_path, {GUARD_FILE: "import time; time.sleep(60)\n"})
    for mode in ("fork", "exec"):
        units, _ = run_worker(tmp_path, build, [spec("ls")], mode=mode, timeout=0.5)
        assert units[("ls", GUARD_FILE)][0] == gp.TIMED_OUT


def test_the_child_runs_in_the_neutral_dir_with_the_replay_env_and_no_tty(tmp_path):
    source = """
        import os, sys
        print(os.getcwd(), os.environ.get("HOME"), os.environ.get("PYTHONHASHSEED"))
        print(sys.stdin.isatty(), sys.stdout.isatty())
    """
    build = fake_build(tmp_path, {GUARD_FILE: source})
    forked, _ = run_worker(tmp_path, build, [spec("ls")], mode="fork")
    genuine_, _ = run_worker(tmp_path, build, [spec("ls")], mode="exec")
    assert forked == genuine_


def test_a_script_sibling_import_resolves_in_the_fork_as_it_does_for_real(tmp_path):
    """The interpreter puts a script's own directory first on sys.path; a bare fork does not."""
    build = fake_build(tmp_path, {GUARD_FILE: "import sibling\nprint(sibling.VALUE)\n"})
    (build / "scripts" / "sibling.py").write_text("VALUE = 41\n")
    forked, _ = run_worker(tmp_path, build, [spec("ls")], mode="fork")
    genuine_, _ = run_worker(tmp_path, build, [spec("ls")], mode="exec")
    assert forked == genuine_ and forked[("ls", GUARD_FILE)][0].rc == 0


def test_a_parents_buffered_output_never_reaches_a_childs_capture(tmp_path):
    """The child flushes sys.stdout before it exits, so anything the parent left buffered there
    would be written into every child's capture. Driven as a subprocess because the property
    needs a real fd-backed stdout in the parent."""
    build = fake_build(tmp_path, {GUARD_FILE: "print('ok')\n"})
    neutral = tmp_path / "neutral"
    neutral.mkdir()
    driver = textwrap.dedent(
        f"""
        import importlib.util, sys
        from pathlib import Path
        spec = importlib.util.spec_from_file_location("gp", {str(MODULE)!r})
        gp = importlib.util.module_from_spec(spec)
        sys.modules["gp"] = gp
        spec.loader.exec_module(gp)
        build = Path({str(build)!r})
        compiled = gp.compile_guard(build / "scripts" / {GUARD_FILE!r})
        sys.stdout.write("NOISE")  # buffered: stdout here is a pipe, and there is no newline
        sys.stderr.write("NOISE")
        reading = gp.run_fork(
            compiled, b"{{}}", Path({str(neutral)!r}), gp.build_markers(build)
        )
        sys.stdout.flush()
        print("\\n" + reading.out, reading.err)
        """
    )
    done = subprocess.run(
        [sys.executable, "-c", driver], capture_output=True, text=True, check=True
    )
    import hashlib

    want_out = hashlib.sha256(b"ok\n").hexdigest()[: gp.DIGEST_LEN]
    want_err = hashlib.sha256(b"").hexdigest()[: gp.DIGEST_LEN]
    assert done.stdout.split()[-2:] == [want_out, want_err]


def test_a_very_large_command_neither_stalls_the_worker_nor_reads_differently(tmp_path):
    """The payload reaches a child through a file, not a pipe: a pipe would fill at 64 KB and
    deadlock a parent that writes it before the child reads."""
    source = (
        "import json, sys\nprint(len(json.load(sys.stdin)['tool_input']['command']))\n"
    )
    build = fake_build(tmp_path, {GUARD_FILE: source})
    rows = [spec("x" * 700_000, identity="big")]
    forked, _ = run_worker(tmp_path, build, rows, mode="fork")
    genuine_, _ = run_worker(tmp_path, build, rows, mode="exec")
    assert forked == genuine_ and forked[("big", GUARD_FILE)][0].rc == 0


def test_a_huge_output_reads_the_same_in_both_modes_and_is_capped(tmp_path):
    import hashlib

    source = (
        "import sys\nsys.stdout.write('x' * 6_000_000)\nsys.stderr.write('e' * 100)\n"
    )
    build = fake_build(tmp_path, {GUARD_FILE: source})
    forked, _ = run_worker(tmp_path, build, [spec("ls")], mode="fork")
    genuine_, _ = run_worker(tmp_path, build, [spec("ls")], mode="exec")
    assert forked == genuine_ and forked[("ls", GUARD_FILE)][0].rc == 0
    capped = b"x" * gp.READ_CAP + b"\0[truncated: 6000000 bytes]"
    want = hashlib.sha256(capped).hexdigest()[: gp.DIGEST_LEN]
    assert (
        forked[("ls", GUARD_FILE)][0].out == want
    )  # the first READ_CAP bytes and the size


def test_a_guard_that_writes_without_end_is_stopped_not_left_to_fill_the_disk(tmp_path):
    source = "import sys\nwhile True:\n    sys.stdout.write('x' * 65536)\n    sys.stdout.flush()\n"
    build = fake_build(tmp_path, {GUARD_FILE: source})
    forked, _ = run_worker(tmp_path, build, [spec("ls")], mode="fork")
    genuine_, _ = run_worker(tmp_path, build, [spec("ls")], mode="exec")
    assert forked == genuine_
    reading = forked[("ls", GUARD_FILE)][0]
    assert reading.rc not in (0, None) and "File too large" in reading.text


def test_the_worker_writes_one_line_per_unit_and_a_done_line(tmp_path):
    build = fake_build(tmp_path, {GUARD_FILE: BLOCKER, "other-guard.py": "pass\n"})
    rows = [
        spec("one"),
        spec("two BLOCKME"),
        spec("three", guards=["other-guard.py"]),  # a subset for this row only
    ]
    units, lines = run_worker(
        tmp_path, build, rows, guards=(GUARD_FILE, "other-guard.py"), repeat=2
    )
    assert sorted(units) == sorted(
        [
            ("one", GUARD_FILE),
            ("one", "other-guard.py"),
            ("two BLOCKME", GUARD_FILE),
            ("two BLOCKME", "other-guard.py"),
            ("three", "other-guard.py"),
        ]
    )
    assert all(len(r) == 2 for r in units.values())  # `--repeat 2`: two readings each
    assert len(lines) == 6 and lines[-1] == {
        "done": True,
        "units": 5,
    }  # no duplicated lines
    assert units[("two BLOCKME", GUARD_FILE)][0].rc == 2


def test_an_uncompilable_guard_is_left_to_a_genuine_process(tmp_path):
    build = fake_build(tmp_path, {GUARD_FILE: "def broken(:\n"})
    forked, _ = run_worker(tmp_path, build, [spec("ls")], mode="fork")
    genuine_, _ = run_worker(tmp_path, build, [spec("ls")], mode="exec")
    assert forked == genuine_
    reading = forked[("ls", GUARD_FILE)][0]
    assert reading.rc == 1 and "SyntaxError" in reading.text


def test_preload_tolerates_a_broken_module_and_restores_sys_path(tmp_path):
    root = fake_build(
        tmp_path,
        {},
        lib={
            "good_mod.py": "VALUE = 1\n",
            "bad_mod.py": "raise RuntimeError('at import')\n",
        },
    )
    before = list(sys.path)
    try:
        gp.preload(root)
        assert sys.path == before
        assert "good_mod" in sys.modules and "bad_mod" not in sys.modules
    finally:
        sys.modules.pop("good_mod", None)
    gp.preload(tmp_path / "no-such-build")  # a build without a lib directory is fine


def test_make_reading_normalises_the_build_root_and_caps_the_text():
    marker = [b"/x/build-old"]
    a = gp.make_reading(0, b"see /x/build-old/f", b"e /x/build-old/g", marker)
    b = gp.make_reading(0, b"see <BUILD>/f", b"e <BUILD>/g", marker)
    assert a == b and a.text == "e <BUILD>/g"
    long = gp.make_reading(2, b"", b"z" * (gp.OUTPUT_TEXT_CAP + 500), marker)
    assert len(long.text) == gp.OUTPUT_TEXT_CAP
    assert gp.make_reading(2, b"", b"one", marker) != gp.make_reading(
        2, b"", b"two", marker
    )


# ---------------------------------------------------------------------------------------------
# classification, verification set, stages, verdict
# ---------------------------------------------------------------------------------------------


def R(rc, out="o", err="e", text=""):
    return gp.Reading(rc, out, err, text)


ALLOW, BLOCK = R(0), R(2, err="refused", text="refused")


@pytest.mark.parametrize(
    ("old", "new", "kind"),
    [
        ([ALLOW], [ALLOW], "SAME"),
        ([BLOCK], [BLOCK], "SAME"),
        ([BLOCK], [ALLOW], "OPENED"),
        ([BLOCK], [R(120)], "OPENED"),  # a refusal that became an exit-120 is an allow
        ([BLOCK], [R(1)], "OPENED"),
        ([ALLOW], [BLOCK], "CLOSED"),
        ([R(1)], [BLOCK], "CLOSED"),
        ([ALLOW], [R(1)], "OTHER"),
        ([R(1)], [R(120)], "OTHER"),
        ([ALLOW], [R(0, err="different words")], "OUTPUT_DIFF"),
        ([BLOCK], [R(2, err="other words")], "OUTPUT_DIFF"),
        ([ALLOW, ALLOW], [ALLOW, ALLOW], "SAME"),
        ([ALLOW, R(2)], [ALLOW, ALLOW], "UNSTABLE"),  # a build disagreeing with itself
        ([ALLOW, ALLOW], [BLOCK, ALLOW], "UNSTABLE"),
        (
            [ALLOW, gp.TIMED_OUT],
            [ALLOW, ALLOW],
            "UNSTABLE",
        ),  # a timeout beside a status
        ([gp.TIMED_OUT], [ALLOW], "INCOMPARABLE"),
        ([ALLOW], [gp.TIMED_OUT, gp.TIMED_OUT], "INCOMPARABLE"),
    ],
)
def test_classification_table(old, new, kind):
    assert gp.classify(old, new).kind == kind


def test_two_absent_readings_are_never_the_same():
    """Equality is the success value, so a unit that never ran must not reach the comparison."""
    assert gp.classify([], []).kind == "INCOMPARABLE"
    assert gp.classify([], [ALLOW]).kind == "INCOMPARABLE"
    assert gp.classify([ALLOW], []).kind == "INCOMPARABLE"


DEADLINE_TEXT = (
    "publication-push-guard: refusing this git command \u2014 the guard reached its own "
    "47.5s deadline before it could judge it, so no push was identified and it fails closed."
)
DEADLINED = R(2, err="d34dl1ne", text=DEADLINE_TEXT)


def test_the_deadline_marker_matches_what_the_guards_print(monkeypatch):
    """Read from the guards' own table, so a reworded message breaks this row, not the run."""
    monkeypatch.syspath_prepend(str(MODULE.parent / "lib"))
    import guard_deadline

    assert gp.DEADLINE_MARKER.search(DEADLINE_TEXT)
    assert len(guard_deadline.DEADLINES) == 3
    for name, entry in guard_deadline.DEADLINES.items():
        assert gp.DEADLINE_MARKER.search(entry.message % 50.0), name


def test_a_guards_own_deadline_is_never_a_reading_and_never_the_same():
    """Measured by review: a guard's deadline is a clean rc 2 with identical stderr on both
    builds, so a spawn-heavy command that deadlines under load read SAME."""
    assert gp.classify([DEADLINED], [DEADLINED]).kind == "INCOMPARABLE"
    assert gp.classify([BLOCK], [DEADLINED]).kind == "INCOMPARABLE"  # one build only
    assert gp.classify([DEADLINED], [BLOCK]).kind == "INCOMPARABLE"
    assert gp.classify([BLOCK, DEADLINED], [BLOCK, BLOCK]).kind == "INCOMPARABLE"
    # the timing guard's deadline ALLOWS, and is just as unproven
    allowed = R(0, text=DEADLINE_TEXT.replace("publication-push-guard", "timing"))
    assert gp.classify([allowed], [ALLOW]).kind == "INCOMPARABLE"
    # a genuine-process (exec) reading carries it too
    assert gp.classify([BLOCK], [BLOCK], old_exec=DEADLINED).kind == "INCOMPARABLE"
    assert gp.classify([BLOCK], [BLOCK], new_exec=DEADLINED).kind == "INCOMPARABLE"


def test_a_refusal_that_merely_mentions_a_deadline_is_still_compared():
    mention = R(2, err="m3nt10n", text="refused: the deadline for this push has passed")
    assert gp.classify([mention], [mention]).kind == "SAME"
    assert gp.classify([mention], [ALLOW]).kind == "OPENED"


def test_output_only_noise_within_a_build_is_flagged_and_not_compared():
    noisy = [R(0, out="a"), R(0, out="b")]
    verdict = gp.classify(noisy, [R(0, out="z")])
    assert verdict == gp.Verdict(
        "SAME", True
    )  # differs in output, but the output is noise
    assert gp.classify(noisy, [BLOCK]).kind == "CLOSED"  # the status is still compared


def test_an_audit_mismatch_outranks_a_verdict_difference():
    forked_2 = [BLOCK]
    ran_120 = R(120, err="refused", text="Exception ignored on flushing sys.stdout")
    verdict = gp.classify(forked_2, [ALLOW], old_exec=ran_120)
    assert verdict.kind == "EXEC_MISMATCH"
    assert (
        gp.classify([BLOCK], [ALLOW], old_exec=BLOCK).kind == "OPENED"
    )  # agreement passes
    assert (
        gp.classify([ALLOW], [ALLOW], new_exec=R(0, out="other")).kind
        == "EXEC_MISMATCH"
    )
    # an audit run that timed out measured nothing: unproven, not a disagreement
    assert gp.classify([ALLOW], [ALLOW], new_exec=gp.TIMED_OUT).kind == "INCOMPARABLE"


def test_classification_is_symmetric_and_identity_is_the_same():
    """Swapping the builds swaps OPENED and CLOSED and moves nothing else."""
    import itertools
    import random

    rng = random.Random(7)
    pool = [ALLOW, BLOCK, R(1), R(120), R(0, out="x"), R(2, err="y"), gp.TIMED_OUT]
    flip = {"OPENED": "CLOSED", "CLOSED": "OPENED"}
    seen = set()
    for _ in range(3000):
        old = [rng.choice(pool) for _ in range(rng.randint(0, 3))]
        new = [rng.choice(pool) for _ in range(rng.randint(0, 3))]
        a = gp.classify(old, new)
        b = gp.classify(new, old)
        assert b.kind == flip.get(a.kind, a.kind), (old, new)
        seen.add(a.kind)
        if old:
            assert gp.classify(old, old).kind in {"SAME", "UNSTABLE", "INCOMPARABLE"}
    assert {
        "SAME",
        "OPENED",
        "CLOSED",
        "OTHER",
        "OUTPUT_DIFF",
        "UNSTABLE",
        "INCOMPARABLE",
    } <= seen
    assert list(itertools.chain(gp.DIFFERENCES)) == ["OPENED", "CLOSED", "OTHER"]


def sweep_of(old, new):
    return {
        "old": {k: [v] for k, v in old.items()},
        "new": {k: [v] for k, v in new.items()},
    }


def test_output_noise_in_one_build_never_hides_the_other_builds_audit():
    noisy = [R(0, out="a"), R(0, out="b")]
    assert (
        gp.classify(noisy, [ALLOW], new_exec=R(0, out="other")).kind == "EXEC_MISMATCH"
    )
    assert (
        gp.classify([ALLOW], noisy, old_exec=R(0, out="other")).kind == "EXEC_MISMATCH"
    )
    # a build's OWN noise still excuses its own audit output
    assert gp.classify(noisy, [ALLOW], old_exec=R(0, out="x")).kind == "SAME"


def test_a_shard_read_stops_at_a_torn_line_and_reads_as_unfinished(tmp_path):
    good = json.dumps({"id": "a", "guard": "g", "readings": [[0, "o", "e", ""]]})
    done = json.dumps({"done": True, "units": 1})
    shard = tmp_path / "s.out"
    shard.write_text(good + "\n" + done + "\n")
    units, finished = gp._read_shard(shard)
    assert finished and units == {("a", "g"): [R(0)]}
    shard.write_text(good + "\n" + done[:10])  # a worker killed mid-write
    units, finished = gp._read_shard(shard)
    assert not finished and list(units) == [("a", "g")]
    shard.write_text(
        good + "\n{not json\n" + done + "\n"
    )  # nothing after garbage is trusted
    assert gp._read_shard(shard)[1] is False
    shard.write_text(good + "\n")
    assert gp._read_shard(shard)[1] is False
    assert gp._read_shard(tmp_path / "absent.out") == ({}, False)


def test_the_verification_set_holds_everything_that_matters_and_a_sample():
    units = [(f"r{i}", "g") for i in range(40)]
    old = {u: ALLOW for u in units}
    new = {u: ALLOW for u in units}
    old[("r1", "g")] = BLOCK  # refused in one build
    new[("r1", "g")] = BLOCK
    new[("r2", "g")] = R(0, err="reworded")  # output differs
    old[("r3", "g")] = gp.TIMED_OUT  # a timeout
    del new[("r4", "g")]  # a missing reading
    new[("r5", "g")] = R(1)  # any non-allow at all
    chosen, sampled = gp.select_verification(sweep_of(old, new), units, 0, "seed")
    must = {("r1", "g"), ("r2", "g"), ("r3", "g"), ("r4", "g"), ("r5", "g")}
    assert set(chosen) == must and sampled == set()
    chosen, sampled = gp.select_verification(sweep_of(old, new), units, 10, "seed")
    assert must <= set(chosen) and len(sampled) == 10 and sampled <= set(units)
    assert chosen == sorted(chosen)
    again = gp.select_verification(sweep_of(old, new), units, 10, "seed")
    assert again == (chosen, sampled)  # seeded: the same run twice draws the same units
    other = gp.select_verification(sweep_of(old, new), units, 10, "another seed")
    assert other[1] != sampled
    big = gp.select_verification(sweep_of(old, new), units, 10_000, "seed")
    assert len(big[1]) == 40  # a sample larger than the population is the population


def two_builds(tmp_path, old_source, new_source):
    olds = fake_build(tmp_path, {GUARD_FILE: old_source}, name="build-old")
    news = fake_build(tmp_path, {GUARD_FILE: new_source}, name="build-new")
    return [
        gp.Build("old", "old", "0" * 40, olds, 1),
        gp.Build("new", "new", "1" * 40, news, 1),
    ]


def stage(tmp_path, builds, rows, **kw):
    neutral = tmp_path / "neutral"
    neutral.mkdir(exist_ok=True)
    args = dict(
        mode="fork",
        repeat=1,
        workdir=tmp_path / "work",
        tag="t",
        workers=4,
        env=dict(os.environ, PYTHONHASHSEED="0"),
        neutral=neutral,
        timeout=30,
    )
    args.update(kw)
    return gp.run_stage(builds, rows, [GUARD_FILE], **args)


def test_a_stage_judges_every_row_against_both_builds(tmp_path):
    builds = two_builds(tmp_path, BLOCKER, "pass\n")
    rows = [spec(f"cmd {i}") for i in range(7)] + [spec("x BLOCKME")]
    result = stage(tmp_path, builds, rows)
    assert result.incomplete == []
    assert set(result.readings) == {"old", "new"}
    for label in ("old", "new"):
        assert (
            len(result.readings[label]) == 8
        )  # the rows were split across shards, none lost
    assert result.readings["old"][("x BLOCKME", GUARD_FILE)][0].rc == 2
    assert result.readings["new"][("x BLOCKME", GUARD_FILE)][0].rc == 0


def test_a_dead_worker_is_reported_and_its_units_have_no_reading(tmp_path):
    builds = two_builds(tmp_path, BLOCKER, BLOCKER)
    rows = [
        spec("ok"),
        spec("owed", guards=["not-a-judged-guard.py"]),
    ]  # the worker dies on it
    result = stage(tmp_path, builds, rows, workers=2)
    assert result.incomplete  # every shard holding the poisoned row
    assert ("owed", "not-a-judged-guard.py") not in result.readings["old"]


def test_a_worker_hung_in_preload_is_killed_at_its_deadline_and_reported(tmp_path):
    """The wait on a worker used to be unbounded: a build whose lib module never finishes
    importing hung the tool with no verdict. The module records its own pid so the test can
    prove the process is gone, not merely that the stage returned."""
    pid_file = tmp_path / "hung.pid"
    lib = {
        "stuck.py": f"import os, time\nopen({str(pid_file)!r}, 'a').write(str(os.getpid()) + '\\n')\n"
        "time.sleep(3600)\n"
    }
    root = fake_build(tmp_path, {GUARD_FILE: "pass\n"}, name="build-hung", lib=lib)
    builds = [gp.Build("old", "old", "0" * 40, root, 1)]
    started = time.monotonic()

    def too_long(_signum, _frame):
        raise AssertionError(
            "the stage did not return: its wait on a worker has no bound"
        )

    previous = signal.signal(signal.SIGALRM, too_long)
    signal.setitimer(
        signal.ITIMER_REAL, 45.0
    )  # red in 45 s, not a suite hung for an hour
    try:
        result = stage(
            tmp_path, builds, [spec("ls")], workers=1, timeout=0.5, worker_grace=2
        )
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)
    assert time.monotonic() - started < 30
    assert len(result.incomplete) == 1 and result.readings["old"] == {}
    pids = [int(p) for p in pid_file.read_text().split()]
    assert pids
    for pid in pids:
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)


def test_an_interrupted_stage_leaves_no_worker_running(tmp_path):
    """Workers run in their own sessions so a hung one can be killed as a group; that also
    means a Ctrl-C or a kill of the tool no longer reaches them, so the stage has to."""
    pid_file = tmp_path / "hung.pid"
    lib = {
        "stuck.py": f"import os, time\nopen({str(pid_file)!r}, 'a').write(str(os.getpid()) + '\\n')\n"
        "time.sleep(3600)\n"
    }
    root = fake_build(tmp_path, {GUARD_FILE: "pass\n"}, name="build-hung", lib=lib)
    builds = [gp.Build("old", "old", "0" * 40, root, 1)]

    def interrupt(_signum, _frame):
        raise KeyboardInterrupt

    previous = signal.signal(signal.SIGALRM, interrupt)
    signal.setitimer(signal.ITIMER_REAL, 3.0)
    try:
        with pytest.raises(KeyboardInterrupt):
            stage(tmp_path, builds, [spec("ls")], workers=1, worker_grace=600)
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)
    pids = [int(p) for p in pid_file.read_text().split()]
    assert pids
    for pid in pids:
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)


def test_a_terminated_run_leaves_no_worker_running(tmp_path):
    """SIGTERM is what `timeout` and a stopped job send, and its default action skips every
    `finally`: the workers, in sessions of their own, then outlive the tool. The handler turns
    the signal into an exit that runs the cleanup."""
    pid_file = tmp_path / "hung.pid"
    lib = {
        "stuck.py": f"import os, time\nopen({str(pid_file)!r}, 'a').write(str(os.getpid()) + '\\n')\n"
        "time.sleep(3600)\n"
    }
    root = fake_build(tmp_path, {GUARD_FILE: "pass\n"}, name="build-hung", lib=lib)
    builds = [gp.Build("old", "old", "0" * 40, root, 1)]
    previous = gp.install_termination_handlers()
    timer = threading.Timer(3.0, os.kill, (os.getpid(), signal.SIGTERM))
    timer.start()
    try:
        with pytest.raises(SystemExit) as raised:
            stage(tmp_path, builds, [spec("ls")], workers=1, worker_grace=600)
    finally:
        timer.cancel()
        for signum, handler in previous.items():
            signal.signal(signum, handler)
    assert raised.value.code == 128 + signal.SIGTERM
    pids = [int(p) for p in pid_file.read_text().split()]
    assert pids
    for pid in pids:
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)


def _worker_with_key(tmp_path, build, key, wait):
    tmp_path.mkdir(exist_ok=True)
    rows_file = tmp_path / "rows.jsonl"
    rows_file.write_text(json.dumps(spec("ls")) + "\n", encoding="utf-8")
    neutral = tmp_path / "neutral"
    neutral.mkdir(exist_ok=True)
    out, key_file = tmp_path / "out.jsonl", tmp_path / "shard.key"
    proc = subprocess.Popen(
        [sys.executable, str(MODULE), "--worker", "--build", str(build)]
        + ["--guards", GUARD_FILE, "--rows", str(rows_file), "--out", str(out)]
        + ["--neutral", str(neutral), "--key", key, "--key-file", str(key_file)],
        env=dict(os.environ, HOME=str(tmp_path), PYTHONHASHSEED="0"),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        proc.wait(timeout=wait)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
    return out, key_file


def test_a_worker_keys_its_own_shard_once_it_is_finished_and_not_before(tmp_path):
    """The parent only reaches a shard's key while collecting, after every shard has finished,
    so a run killed during a long sweep used to lose all of it. The worker writes the key
    itself, after its `done` line."""
    done_build = fake_build(tmp_path, {GUARD_FILE: "pass\n"}, name="build-ok")
    out, key_file = _worker_with_key(
        tmp_path / "ok", done_build, "distinct-key-8842", 60
    )
    assert key_file.read_text() == "distinct-key-8842"
    assert json.loads(out.read_text().splitlines()[-1])["done"] is True
    hung = fake_build(
        tmp_path,
        {GUARD_FILE: "pass\n"},
        name="build-hung2",
        lib={"stuck.py": "import time\ntime.sleep(3600)\n"},
    )
    _out, hung_key = _worker_with_key(tmp_path / "hung", hung, "never-written", 3)
    assert not hung_key.exists()


def test_a_shard_is_reused_only_when_every_builds_shard_at_that_index_is(tmp_path):
    """Measured by review: reusing one build's shard beside a re-run of the other paired readings
    taken at different times, against a repo the guard reads live. Index 0 of `old` loses its key;
    `new`'s index 0 must run again too, and index 1 stays reused for both."""
    builds = two_builds(tmp_path, BLOCKER, "pass\n")
    rows = [spec(f"cmd-{i}") for i in range(6)]
    first = stage(tmp_path, builds, rows)
    assert (first.reused, first.total) == (0, 4)
    work = tmp_path / "work"
    names = {
        (label, i): work / f"t-{label}-{i}.out"
        for label in ("old", "new")
        for i in (0, 1)
    }
    assert all(p.exists() for p in names.values())
    before = {k: p.stat().st_mtime_ns for k, p in names.items()}
    (work / "t-old-0.key").unlink()
    again = stage(tmp_path, builds, rows)
    after = {k: p.stat().st_mtime_ns for k, p in names.items()}
    assert (again.reused, again.total) == (2, 4)
    assert after[("old", 0)] != before[("old", 0)]
    assert after[("new", 0)] != before[("new", 0)]  # re-run only to stay paired
    assert after[("old", 1)] == before[("old", 1)]
    assert after[("new", 1)] == before[("new", 1)]


def test_verification_end_to_end_finds_the_opened_unit_and_nothing_else(tmp_path):
    builds = two_builds(tmp_path, BLOCKER, "pass\n")  # the new build refuses nothing
    rows = [spec(f"cmd {i}") for i in range(12)] + [spec("y BLOCKME")]
    units = [(r["id"], GUARD_FILE) for r in rows]
    swept = stage(tmp_path, builds, rows)
    chosen, sampled = gp.select_verification(swept.readings, units, 4, "s")
    assert ("y BLOCKME", GUARD_FILE) in chosen
    by_id = {r["id"]: r for r in rows}
    verify_rows = [by_id[i] for i, _g in chosen]
    repeats = stage(tmp_path, builds, verify_rows, repeat=2, tag="rep")
    execs = stage(tmp_path, builds, verify_rows, mode="exec", tag="exe")
    verdicts = gp.judge_units(units, swept.readings, repeats.readings, execs.readings)
    assert verdicts[("y BLOCKME", GUARD_FILE)].kind == "OPENED"
    assert {v.kind for u, v in verdicts.items() if u[0] != "y BLOCKME"} == {"SAME"}
    assert gp.count_noise(sampled, swept.readings, repeats.readings) == 0


def test_noise_counts_units_that_read_differently_between_runs():
    sweep = {
        "old": {("a", "g"): [ALLOW], ("b", "g"): [ALLOW]},
        "new": {("a", "g"): [ALLOW]},
    }
    repeats = {
        "old": {("a", "g"): [R(2), ALLOW], ("b", "g"): [ALLOW]},
        "new": {("a", "g"): [ALLOW]},
    }
    assert gp.count_noise({("a", "g"), ("b", "g")}, sweep, repeats) == 1
    assert gp.count_noise(set(), sweep, repeats) == 0


def evaluation(**over):
    """A run that should PASS, so each row below breaks exactly one thing."""
    base = gp.Evaluation(
        rows=5000,
        transcripts=10,
        verdicts={(f"r{i}", "g"): gp.Verdict("SAME", False) for i in range(1000)},
        sampled=100,
        noise=0,
        canaries=[("push-guard.py", BLOCK, BLOCK)],
        registration=[],
        limited=False,
        unsampled=False,
        incomplete_shards=[],
        min_rows=1000,
        tolerate=0,
        guard_errors=0,
    )
    return base._replace(**over)


def verdicts_of(*kinds):
    """The given verdicts among enough ordinary ones to clear the comparable-unit floor."""
    padding = {(f"ok{i}", "g"): gp.Verdict("SAME", False) for i in range(1000)}
    return {
        **padding,
        **{(f"r{i}", "g"): gp.Verdict(k, False) for i, k in enumerate(kinds)},
    }


def test_a_clean_run_passes():
    decision = gp.decide(evaluation())
    assert (decision.token, decision.rc, decision.reasons) == ("PASS", 0, [])


@pytest.mark.parametrize(
    ("over", "phrase"),
    [
        ({"rows": 999}, "below the floor"),
        ({"transcripts": 0}, "no transcript"),
        (
            {"canaries": [("push-guard.py", ALLOW, ALLOW)]},
            "did not refuse the push-guard.py canary",
        ),
        ({"canaries": [("push-guard.py", None, BLOCK)]}, "no reading"),
        ({"limited": True}, "--limit"),
        ({"unsampled": True}, "--sample 0"),
        ({"verdicts": verdicts_of("UNSTABLE")}, "unproven"),
        ({"verdicts": verdicts_of("INCOMPARABLE")}, "unproven"),
        ({"verdicts": verdicts_of("EXEC_MISMATCH")}, "genuine process"),
        ({"incomplete_shards": ["/x/shard.out"]}, "never finished"),
    ],
)
def test_each_floor_alone_makes_a_run_incomplete(over, phrase):
    decision = gp.decide(evaluation(**over))
    assert decision.token == "INCOMPLETE" and decision.rc == 3
    assert any(phrase in r for r in decision.reasons), decision.reasons


def test_tolerating_unproven_units_cannot_license_a_pass_over_almost_nothing():
    """Measured by review: 4,999 incomparable units and one comparable one, tolerated, was a PASS
    while the only floor counted rows SUBMITTED."""
    verdicts = {(f"r{i}", "g"): gp.Verdict("INCOMPARABLE", False) for i in range(4999)}
    verdicts[("ok", "g")] = gp.Verdict("SAME", False)
    decision = gp.decide(evaluation(verdicts=verdicts, tolerate=5000))
    assert decision.token == "INCOMPLETE"
    assert any("units were comparable" in r for r in decision.reasons)


def test_unstable_units_cannot_license_a_pass_over_almost_nothing():
    """Measured by review: 2,000 units all UNSTABLE, tolerated, was a PASS with comparable=2000,
    because `classify` returns UNSTABLE before it compares anything."""
    verdicts = {(f"r{i}", "g"): gp.Verdict("UNSTABLE", False) for i in range(4999)}
    verdicts[("ok", "g")] = gp.Verdict("SAME", False)
    ev = evaluation(verdicts=verdicts, tolerate=10**9)
    decision = gp.decide(ev)
    assert decision.token == "INCOMPLETE"
    assert any("units were comparable" in r for r in decision.reasons)
    line = gp.verdict_line(ev, decision)
    assert "comparable=1 " in line and "unstable=4999" in line


def test_guard_internal_errors_logged_during_the_replay_make_a_run_incomplete():
    """Measured by review: an internal error is a fail-closed refusal the status comparison
    reads as an ordinary block; only the log says the guard never judged the command."""
    decision = gp.decide(evaluation(guard_errors=3))
    assert decision.token == "INCOMPLETE" and decision.rc == 3
    assert any("3 guard-internal errors" in r for r in decision.reasons), decision
    assert gp.decide(evaluation(guard_errors=0)).token == "PASS"


def test_a_reused_corpus_has_no_transcript_floor():
    assert gp.decide(evaluation(transcripts=None)).token == "PASS"


def test_tolerating_unproven_units_is_one_knob_and_only_for_unproven_ones():
    two = verdicts_of("UNSTABLE", "INCOMPARABLE", "SAME")
    assert gp.decide(evaluation(verdicts=two, tolerate=1)).token == "INCOMPLETE"
    assert gp.decide(evaluation(verdicts=two, tolerate=2)).token == "PASS"
    mismatch = verdicts_of("EXEC_MISMATCH")
    assert gp.decide(evaluation(verdicts=mismatch, tolerate=99)).token == "INCOMPLETE"


@pytest.mark.parametrize(
    "kinds", [("OPENED",), ("CLOSED",), ("OTHER",), ("SAME", "OPENED", "SAME")]
)
def test_a_believable_run_with_a_changed_verdict_fails(kinds):
    decision = gp.decide(evaluation(verdicts=verdicts_of(*kinds)))
    assert (decision.token, decision.rc) == ("FAIL", 1)


def test_a_registration_change_alone_fails_and_output_only_changes_do_not():
    assert (
        gp.decide(
            evaluation(registration=["- PreToolUse[Bash] x.py timeout=None"])
        ).token
        == "FAIL"
    )
    assert (
        gp.decide(evaluation(verdicts=verdicts_of("OUTPUT_DIFF", "SAME"))).token
        == "PASS"
    )


def test_a_run_that_cannot_be_believed_outranks_one_that_found_a_difference():
    ev = evaluation(verdicts=verdicts_of("OPENED"), limited=True)
    assert gp.decide(ev).token == "INCOMPLETE"


# ---------------------------------------------------------------------------------------------
# command line: the whole run, end to end, over fake builds
# ---------------------------------------------------------------------------------------------

GUARD_OLD = """
    import json, sys
    cmd = json.load(sys.stdin)["tool_input"]["command"]
    if "BLOCKME" in cmd or "git push" in cmd:
        sys.stderr.write("refused: policy\\n")
        sys.exit(2)
"""
GUARD_ALLOWS_ALL = "pass\n"
GUARD_REWORDED = GUARD_OLD.replace("refused: policy", "declined: policy")
GUARD_STRICTER = GUARD_OLD.replace(
    '"BLOCKME" in cmd', '"BLOCKME" in cmd or "cmd 3" in cmd'
)
LONG_COMMAND = "echo BLOCKME " + "z" * 400


def scope_repo(
    tmp_path,
    old_source,
    new_source,
    old_settings=None,
    new_settings=None,
    name="scope",
    extra=None,
):
    """A repo whose first commit holds the OLD guard and whose second holds the NEW one; `extra`
    is {file name: source} for further guards, identical in both."""
    guards = {GUARD_FILE: old_source, **(extra or {})}
    repo, first = make_repo(
        tmp_path,
        {
            **{
                f"scripts/{n}": ("#!/usr/bin/env python3\n" + textwrap.dedent(s), True)
                for n, s in guards.items()
            },
            **({"settings.json": old_settings} if old_settings else {}),
        },
        name=name,
    )
    guard = repo / "scripts" / GUARD_FILE
    guard.write_text("#!/usr/bin/env python3\n" + textwrap.dedent(new_source))
    if new_settings:
        (repo / "settings.json").write_text(new_settings)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "--allow-empty", "-m", "new")
    return repo, first, git(repo, "rev-parse", "HEAD")


def transcripts_dir(tmp_path, commands=None):
    root = tmp_path / "transcripts"
    commands = commands or [f"cmd {i}" for i in range(10)] + ["x BLOCKME", LONG_COMMAND]
    write_transcript(root, "p/s.jsonl", [bash_record(c) for c in commands])
    return root


def cli(
    tmp_path,
    repo,
    old,
    new,
    *extra,
    guard=GUARD_FILE,
    art="art",
    commands=None,
    module=MODULE,
):
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    args = [
        sys.executable,
        str(module),
        "--old",
        old,
        "--new",
        new,
        "--scope",
        str(repo),
        "--artifact-dir",
        str(tmp_path / art),
        "--transcripts",
        str(transcripts_dir(tmp_path, commands)),
        *[
            part
            for g in ([guard] if isinstance(guard, str) else guard)
            for part in ("--guard", g)
        ],
        "--min-rows",
        "1",
        "--sample",
        "6",
        "--workers",
        "2",
        *extra,
    ]
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        env=dict(os.environ, HOME=str(home)),
        check=False,
    )


def fields(result):
    last = result.stdout.rstrip("\n").splitlines()[-1]
    assert last.startswith("RESULT: "), result.stdout[-400:] + result.stderr[-400:]
    head, *rest = last.split()[1:]
    return head, dict(part.split("=", 1) for part in rest if "=" in part)


def test_identical_builds_pass_with_every_floor_met(tmp_path):
    repo, old, _new = scope_repo(tmp_path, GUARD_OLD, GUARD_OLD)
    done = cli(tmp_path, repo, old, old)
    verdict, got = fields(done)
    assert (verdict, done.returncode) == ("PASS", 0), done.stdout[-600:]
    assert (
        got["rc"] == "0" and got["rows"] == "12" and got["units"] == "13"
    )  # 12 rows + 1 canary
    assert got["comparable"] == "13" and got["verdict_diffs"] == "0"
    assert got["canaries"] == "1/1" and got["noise"].startswith("0/")
    assert (
        "old: allow=10 block=2 other=0 none=0" in done.stdout
    )  # it saw refusals to compare
    assert "guard error log: 0 records" in done.stdout
    assert "timing guard:" not in done.stdout  # that guard was not judged in this run


def test_an_allow_that_used_to_be_a_refusal_fails_and_names_the_command(tmp_path):
    repo, old, new = scope_repo(tmp_path, GUARD_OLD, GUARD_ALLOWS_ALL)
    done = cli(tmp_path, repo, old, new)
    verdict, got = fields(done)
    assert (verdict, done.returncode) == ("FAIL", 1)
    assert got["verdict_diffs"] == "3"  # both refused rows and the canary
    assert "verdict differences: OPENED (3)" in done.stdout
    assert (
        "cmd='x BLOCKME'" in done.stdout and "old said: refused: policy" in done.stdout
    )
    assert repr(LONG_COMMAND[:300] + "...") in done.stdout  # capped at 300 characters
    assert LONG_COMMAND not in done.stdout
    full = [
        json.loads(line)["command"]
        for line in (tmp_path / "art" / "differences.jsonl").read_text().splitlines()
    ]
    assert LONG_COMMAND in full  # the file keeps what the report truncates


def test_a_newly_refused_command_is_closed_and_a_reworded_refusal_is_not_a_verdict(
    tmp_path,
):
    repo, old, new = scope_repo(tmp_path, GUARD_OLD, GUARD_STRICTER)
    done = cli(tmp_path, repo, old, new)
    assert fields(done)[0] == "FAIL" and "CLOSED (1)" in done.stdout
    assert "cmd='cmd 3'" in done.stdout
    repo2, old2, new2 = scope_repo(tmp_path, GUARD_OLD, GUARD_REWORDED, name="scope2")
    reworded = cli(tmp_path, repo2, old2, new2, art="art2")
    verdict, got = fields(reworded)
    assert (
        verdict == "PASS" and got["output_diffs"] == "3"
    )  # two refused rows and the canary
    assert "output-only differences, grouped (1):" in reworded.stdout
    assert "old said: refused: policy" in reworded.stdout
    assert "new said: declined: policy" in reworded.stdout


def test_a_registration_change_fails_even_when_every_guard_reads_the_same(tmp_path):
    def settings(*names):
        return json.dumps(
            {
                "hooks": {
                    "PreToolUse": [
                        {
                            "matcher": "Bash",
                            "hooks": [{"type": "command", "command": f"$HOME/{n}"}],
                        }
                        for n in names
                    ]
                }
            }
        )

    repo, old, new = scope_repo(
        tmp_path,
        GUARD_OLD,
        GUARD_OLD,
        old_settings=settings("push-guard.py", "other-guard.py"),
        new_settings=settings("push-guard.py"),
    )
    done = cli(tmp_path, repo, old, new)
    verdict, got = fields(done)
    assert verdict == "FAIL" and got["verdict_diffs"] == "1"
    assert "registration differences (1):" in done.stdout
    assert "- PreToolUse[Bash] other-guard.py timeout=None" in done.stdout


@pytest.mark.parametrize(
    ("extra", "phrase"),
    [
        (("--limit", "2"), "--limit"),
        (("--sample", "0"), "--sample 0"),
        (("--min-rows", "1000"), "below the floor"),
    ],
)
def test_a_run_that_cannot_be_believed_is_incomplete(tmp_path, extra, phrase):
    repo, old, _new = scope_repo(tmp_path, GUARD_OLD, GUARD_OLD)
    done = cli(tmp_path, repo, old, old, *extra)
    assert fields(done)[0] == "INCOMPLETE" and done.returncode == 3
    assert phrase in done.stdout


def test_an_old_build_that_never_refuses_the_canary_is_incomplete(tmp_path):
    lax = GUARD_OLD.replace(' or "git push" in cmd', "")
    repo, old, _new = scope_repo(tmp_path, lax, lax)
    done = cli(tmp_path, repo, old, old)
    assert fields(done)[0] == "INCOMPLETE" and fields(done)[1]["canaries"] == "0/1"
    assert "did not refuse the push-guard.py canary" in done.stdout


@pytest.mark.parametrize(
    ("extra", "phrase"),
    [
        (("--old", "no-such-rev"), "does not name a commit"),
        (("--guard", "mystery-guard.py"), "not a guard this tool can judge"),
        (("--guard", "recast-commit-gate.py"), "pytest run"),
        (("--guard", "git-timing-guard.py"), "no repo pattern"),
        (("--corpus", "/no/such/corpus.jsonl"), "cannot read corpus"),
    ],
)
def test_setup_failures_are_an_error_with_a_result_line(tmp_path, extra, phrase):
    repo, old, _new = scope_repo(tmp_path, GUARD_OLD, GUARD_OLD)
    done = cli(tmp_path, repo, old, old, *extra)
    line = done.stdout.rstrip("\n").splitlines()[-1]
    assert done.returncode == 2 and line.startswith("RESULT: ERROR rc=2 "), done.stdout
    assert phrase in line


def test_an_artifact_dir_inside_a_worktree_and_a_usage_error_are_both_errors(tmp_path):
    repo, old, _new = scope_repo(tmp_path, GUARD_OLD, GUARD_OLD)
    inside = cli(tmp_path, repo, old, old, art="scope/artifacts")
    assert inside.returncode == 2 and "inside a git worktree" in inside.stdout
    usage = subprocess.run(
        [sys.executable, str(MODULE), "--new", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert usage.returncode == 2
    assert usage.stdout.rstrip("\n").splitlines()[-1].startswith("RESULT: ERROR rc=2")


def test_a_dirty_scripts_tree_is_refused_when_a_revision_is_head(tmp_path):
    repo, old, _new = scope_repo(tmp_path, GUARD_OLD, GUARD_OLD)
    (repo / "scripts" / "scratch.py").write_text("x = 1\n")  # untracked, under scripts/
    refused = cli(tmp_path, repo, old, "HEAD")
    assert refused.returncode == 2 and "uncommitted changes" in refused.stdout
    assert "scripts/scratch.py" in refused.stdout
    # neither revision is HEAD, so the working tree is not what is graded and is no obstacle
    fine = cli(tmp_path, repo, old, old, art="art2")
    assert fields(fine)[0] == "PASS"


def test_two_runs_cannot_share_an_artifact_directory(tmp_path):
    repo, old, _new = scope_repo(tmp_path, GUARD_OLD, GUARD_OLD)
    art = tmp_path / "art"
    art.mkdir()
    with open(art / ".lock", "w") as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        refused = cli(tmp_path, repo, old, old)
    assert refused.returncode == 2 and "another run holds" in refused.stdout


def test_the_guard_error_log_records_are_counted(tmp_path):
    log = tmp_path / "guard-errors.log"
    log.write_text(
        "===== 2026-09-28T10:00:00+00:00 ValueError =====\ncwd: /x\n"
        "--- command (verbatim) ---\n===== not a record start\n"
        "===== 2026-09-28T10:00:01+00:00 KeyError =====\ncwd: /y\n"
    )
    assert gp._count_log_records(log) == 2
    assert gp._count_log_records(tmp_path / "absent.log") == 0


def test_a_report_line_survives_a_unit_the_sweep_never_read():
    unit = ("id1", "g")
    detail = gp.Detail(
        {"id1": gp.Row("id1", "/x", {"command": "ls"})},
        {"old": {}, "new": {}},
        {"old": {unit: [R(0, err="a")]}, "new": {unit: [R(0, err="b")]}},
    )
    ev = evaluation(verdicts={unit: gp.Verdict("OUTPUT_DIFF", False)})
    text = gp.render_report(["header"], ev, gp.decide(ev), detail, ["g"])
    assert "output-only differences, grouped (1):" in text


def test_a_frozen_corpus_reproduces_the_run_without_the_transcripts(tmp_path):
    repo, old, _new = scope_repo(tmp_path, GUARD_OLD, GUARD_OLD)
    first = cli(tmp_path, repo, old, old)
    frozen = tmp_path / "art" / "corpus.jsonl"
    assert frozen.is_file()
    second = cli(tmp_path, repo, old, old, "--corpus", str(frozen), art="art2")
    assert fields(first)[1]["units"] == fields(second)[1]["units"]
    assert fields(second)[0] == "PASS" and "(frozen)" in second.stdout


def test_the_run_leaves_a_private_artifact_dir_and_the_result_line_last(tmp_path):
    repo, old, _new = scope_repo(tmp_path, GUARD_OLD, GUARD_OLD)
    done = cli(tmp_path, repo, old, old)
    art = tmp_path / "art"
    assert (art.stat().st_mode & 0o777) == 0o700  # it holds raw commands
    for name in ("report.txt", "differences.jsonl", "corpus.jsonl", "guard-errors.log"):
        assert (art / name).exists() or name == "guard-errors.log"
    assert done.stdout == (art / "report.txt").read_text()
    assert done.stdout.rstrip("\n").splitlines()[-1].startswith("RESULT: PASS rc=0 ")


DIVERGES_UNDER_FORK = """
    import os, stat, sys
    # stdin is a regular file under the fork server and a pipe for a genuine process: one of the
    # differences the audit exists to expose, and the one this control uses.
    sys.exit(2 if stat.S_ISREG(os.fstat(0).st_mode) else 0)
"""


@pytest.mark.parametrize("diverging_side", ["old", "new", "both"])
def test_a_guard_that_reads_differently_as_a_genuine_process_is_caught_on_either_side(
    tmp_path, diverging_side
):
    """A control that must MOVE: this guard refuses only under the fork server, so a sweep
    alone would call the builds identical and every refusal legitimate. Each build's audit is
    read on its own, so one diverging side cannot hide behind a sound other."""
    old_source = DIVERGES_UNDER_FORK if diverging_side in ("old", "both") else GUARD_OLD
    new_source = DIVERGES_UNDER_FORK if diverging_side in ("new", "both") else GUARD_OLD
    repo, old, new = scope_repo(tmp_path, old_source, new_source)
    done = cli(tmp_path, repo, old, new)
    verdict, got = fields(done)
    assert verdict == "INCOMPLETE" and int(got["exec_mismatch"]) > 0, done.stdout[-500:]
    assert "genuine process" in done.stdout


# Not the clock: macOS `time_ns` ticks in whole microseconds, so its parity never moves. Refuses
# the canary, so the run can otherwise be believed.
FLAKY_ON_ROWS = """
    import json, random, sys
    if "git push" in json.load(sys.stdin)["tool_input"]["command"]:
        sys.exit(2)
    sys.exit(random.randrange(2) * 2)
"""


@pytest.mark.parametrize("flaky_side", ["old", "new", "both"])
def test_a_nondeterministic_build_is_unstable_whichever_side_it_is_on(
    tmp_path, flaky_side
):
    """Each build's own repeats have to be read: with only one side flaky, the other side's
    stable readings would otherwise hide it."""
    old_source = FLAKY_ON_ROWS if flaky_side in ("old", "both") else GUARD_OLD
    new_source = FLAKY_ON_ROWS if flaky_side in ("new", "both") else GUARD_OLD
    repo, old, new = scope_repo(tmp_path, old_source, new_source)
    done = cli(tmp_path, repo, old, new, "--sample", "12")
    verdict, got = fields(done)
    assert verdict == "INCOMPLETE" and int(got["unstable"]) > 0, done.stdout[-500:]
    assert int(got["noise"].split("/")[0]) > 0


def test_a_hang_is_incomparable_and_the_tolerance_knob_is_honest_about_it(tmp_path):
    slow = (
        textwrap.dedent(GUARD_OLD)
        + "\nimport time\nif 'SLOW' in cmd:\n    time.sleep(30)\n"
    )
    repo, old, _new = scope_repo(tmp_path, slow, slow)
    commands = [f"cmd {i}" for i in range(5)] + ["SLOW one"]
    done = cli(tmp_path, repo, old, old, "--timeout", "2", commands=commands)
    verdict, got = fields(done)
    assert verdict == "INCOMPLETE" and got["incomparable"] == "1"
    assert got["comparable"] == str(int(got["units"]) - 1)
    tolerated = cli(
        tmp_path,
        repo,
        old,
        old,
        "--timeout",
        "2",
        "--tolerate-unproven",
        "1",
        commands=commands,
        art="art2",
    )
    assert (
        fields(tolerated)[0] == "PASS" and fields(tolerated)[1]["incomparable"] == "1"
    )


COMMIT_GUARD = """
    import json, sys
    if "git commit" in json.load(sys.stdin)["tool_input"]["command"]:
        sys.exit(2)
"""


def test_each_canary_runs_only_against_its_own_guard(tmp_path):
    repo, old, _new = scope_repo(
        tmp_path, GUARD_OLD, GUARD_OLD, extra={"commit-subject-guard.py": COMMIT_GUARD}
    )
    done = cli(tmp_path, repo, old, old, guard=[GUARD_FILE, "commit-subject-guard.py"])
    verdict, got = fields(done)
    assert verdict == "PASS", done.stdout[-500:]
    assert got["rows"] == "12" and got["canaries"] == "2/2"
    assert got["units"] == str(12 * 2 + 2)  # a canary is judged by its own guard alone


def test_message_text_cannot_differ_between_builds_by_hash_order(tmp_path):
    """Set iteration order follows the hash seed, which is fixed when an interpreter starts, so it
    has to be pinned in the environment the workers are LAUNCHED with."""
    # refuses the canary (so the run can be believed), and on every other row says something
    # whose text depends on set order
    ordered = (
        textwrap.dedent(GUARD_OLD)
        + 'sys.stderr.write(repr(set("abcdefghijklmnopqrstuvwxyz")) + "\\n")\n'
    )
    repo, old, _new = scope_repo(tmp_path, ordered, ordered)
    done = cli(tmp_path, repo, old, old)
    verdict, got = fields(done)
    assert verdict == "PASS" and got["output_diffs"] == "0", done.stdout[-500:]


# ---------------------------------------------------------------------------------------------
# resume: a shard is reused only when everything that produced it is identical
# ---------------------------------------------------------------------------------------------


GUARD_LOGS_AN_ERROR = (
    GUARD_OLD
    + """
    import os
    if "cmd 3" in cmd:
        with open(os.environ["PUBLICATION_PUSH_GUARD_LOG"], "a") as log:
            log.write("===== 2026-09-29T00:00:00+00:00 ValueError =====\\ncwd: /x\\n")
"""
)
GUARD_DEADLINES = (
    GUARD_OLD
    + """
    if "cmd 3" in cmd:
        sys.stderr.write("the guard reached its own 9.5s deadline before it could judge it\\n")
        sys.exit(2)
"""
)


def test_a_guard_error_logged_during_the_replay_makes_the_run_incomplete(tmp_path):
    repo, old, _new = scope_repo(tmp_path, GUARD_LOGS_AN_ERROR, GUARD_LOGS_AN_ERROR)
    done = cli(tmp_path, repo, old, old)
    verdict, _got = fields(done)
    assert (verdict, done.returncode) == ("INCOMPLETE", 3), done.stdout[-600:]
    assert "guard-internal errors in the guard error log" in done.stdout
    assert "guard error log: 0 records" not in done.stdout
    clean_repo, clean, _ = scope_repo(tmp_path, GUARD_OLD, GUARD_OLD, name="clean")
    # a record an EARLIER run left in the artifact directory IS charged: a resumed run reuses
    # shards the earlier run made, and an error logged while making them would otherwise be
    # invisible to it
    (tmp_path / "art-clean").mkdir()
    (tmp_path / "art-clean" / "guard-errors.log").write_text(
        "===== 2026-09-01T00:00:00+00:00 KeyError =====\ncwd: /old\n"
    )
    stale = cli(tmp_path, clean_repo, clean, clean, art="art-clean")
    assert (fields(stale)[0], stale.returncode) == ("INCOMPLETE", 3), stale.stdout[
        -600:
    ]
    assert "guard error log: 1 records in the file" in stale.stdout
    fresh = cli(tmp_path, clean_repo, clean, clean, art="art-fresh")
    assert fields(fresh)[0] == "PASS"
    assert "guard error log: 0 records in the file" in fresh.stdout


def test_a_guard_that_reached_its_own_deadline_is_incomparable_end_to_end(tmp_path):
    repo, old, _new = scope_repo(tmp_path, GUARD_DEADLINES, GUARD_DEADLINES)
    done = cli(tmp_path, repo, old, old)
    verdict, got = fields(done)
    assert verdict == "INCOMPLETE", done.stdout[-600:]
    assert got["incomparable"] == "1" and got["verdict_diffs"] == "0"
    assert got["comparable"] == "12"


def shards_line(result):
    return next(ln for ln in result.stdout.splitlines() if ln.startswith("shards: "))


def outs(tmp_path, art="art"):
    return sorted((tmp_path / art / "work").glob("*.out"))


def test_a_second_identical_run_reuses_every_shard_and_reads_the_same(tmp_path):
    repo, old, _new = scope_repo(tmp_path, GUARD_OLD, GUARD_OLD)
    first = cli(tmp_path, repo, old, old)
    assert shards_line(first) == "shards: 0 reused of 6"
    stamps = {p: p.stat().st_mtime_ns for p in outs(tmp_path)}
    second = cli(
        tmp_path, repo, old, old, "--corpus", str(tmp_path / "art" / "corpus.jsonl")
    )
    assert shards_line(second) == "shards: 6 reused of 6"
    assert {
        p: p.stat().st_mtime_ns for p in outs(tmp_path)
    } == stamps  # nothing was rewritten
    assert fields(first) == fields(second)


def test_a_shard_that_lost_its_done_line_or_its_key_is_run_again(tmp_path):
    repo, old, _new = scope_repo(tmp_path, GUARD_OLD, GUARD_OLD)
    cli(tmp_path, repo, old, old)
    frozen = str(tmp_path / "art" / "corpus.jsonl")
    sweep = tmp_path / "art" / "work" / "sweep-old-0.out"
    sweep.write_text(
        "".join(sweep.read_text().splitlines(keepends=True)[:-1])
    )  # killed mid-run
    (
        tmp_path / "art" / "work" / "repeat-new-0.key"
    ).unlink()  # its key was never written
    second = cli(tmp_path, repo, old, old, "--corpus", frozen)
    # each damaged shard takes its partner with it: only the exec pair is reused
    assert shards_line(second) == "shards: 2 reused of 6"
    assert fields(second)[0] == "PASS"
    assert json.loads(sweep.read_text().splitlines()[-1]) == {"done": True, "units": 13}


def test_a_shard_from_a_different_build_is_never_reused(tmp_path):
    """The stale readings here are all `allow`; reusing them for the new build would call two
    different guards identical."""
    repo, old, new = scope_repo(tmp_path, GUARD_OLD, GUARD_ALLOWS_ALL)
    cli(tmp_path, repo, old, old)
    frozen = str(tmp_path / "art" / "corpus.jsonl")
    second = cli(tmp_path, repo, old, new, "--corpus", frozen)
    assert fields(second)[0] == "FAIL" and "OPENED" in second.stdout
    # the old build's shards are intact, but reading them beside a re-run of the new build's
    # would pair readings taken at different times: nothing is reused
    assert shards_line(second) == "shards: 0 reused of 6"


def test_a_shard_made_by_another_version_of_the_tool_is_not_reused(tmp_path):
    repo, old, _new = scope_repo(tmp_path, GUARD_OLD, GUARD_OLD)
    copy = tmp_path / "tool" / "guard-parity.py"
    copy.parent.mkdir()
    shutil.copy(MODULE, copy)
    cli(tmp_path, repo, old, old, module=copy)
    frozen = str(tmp_path / "art" / "corpus.jsonl")
    copy.write_text(copy.read_text() + "\n# edited after the first run\n")
    again = cli(tmp_path, repo, old, old, "--corpus", frozen, module=copy)
    assert shards_line(again) == "shards: 0 reused of 6"


def test_a_shard_made_under_another_fixture_is_not_reused(tmp_path):
    repo, old, _new = scope_repo(tmp_path, GUARD_OLD, GUARD_OLD)
    cli(tmp_path, repo, old, old)
    frozen = str(tmp_path / "art" / "corpus.jsonl")
    other = cli(tmp_path, repo, old, old, "--corpus", frozen, "--timing-pattern", "a/b")
    assert shards_line(other) == "shards: 0 reused of 6"


def test_a_shard_made_under_another_timeout_is_not_reused(tmp_path):
    """Its own test, against a clean first run: sharing a run with the fixture case would let that
    difference alone force the rerun and leave the timeout unexamined."""
    repo, old, _new = scope_repo(tmp_path, GUARD_OLD, GUARD_OLD)
    cli(tmp_path, repo, old, old)
    frozen = str(tmp_path / "art" / "corpus.jsonl")
    other = cli(tmp_path, repo, old, old, "--corpus", frozen, "--timeout", "90")
    assert shards_line(other) == "shards: 0 reused of 6"


# ---------------------------------------------------------------------------------------------
# fix pass: last-resort error line, usage validation, report safety
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("failure", [OSError("disk exploded"), KeyError("lost-key")])
def test_a_crash_that_is_not_a_setup_error_still_ends_in_one_error_line(
    tmp_path, monkeypatch, capsys, failure
):
    """A traceback with no RESULT line and rc 1 read as a FAIL verdict."""

    def boom(*_a, **_k):
        raise failure

    monkeypatch.setattr(gp, "_inside_worktree", boom)
    rc = gp.main(["--old", "x", "--artifact-dir", str(tmp_path / "a")])
    lines = capsys.readouterr().out.rstrip("\n").splitlines()
    assert rc == 2
    assert [ln for ln in lines if ln.startswith("RESULT:")] == [lines[-1]]
    assert lines[-1].startswith("RESULT: ERROR rc=2 ")
    assert type(failure).__name__ in lines[-1] and "unexpected" in lines[-1]


def test_the_default_worker_count_is_capped_and_an_explicit_one_is_not(monkeypatch):
    """The real run used 24 workers, all sharing the publication guard's subprocess spawns;
    the default stops at 8, and `--workers` still says exactly what it is told."""

    def parse(*extra):
        return gp.build_parser().parse_args(
            ["--old", "a", "--artifact-dir", "/x", *extra]
        )

    monkeypatch.setattr(os, "cpu_count", lambda: 37)
    assert parse().workers == 8
    assert parse("--workers", "29").workers == 29
    monkeypatch.setattr(os, "cpu_count", lambda: 3)
    assert parse().workers == 3
    monkeypatch.setattr(os, "cpu_count", lambda: None)
    assert parse().workers == 2
    assert "default: min(CPU count, 8)" in " ".join(
        gp.build_parser().format_help().split()
    )


@pytest.mark.parametrize(
    "bad",
    [
        ("--min-rows", "0"),
        ("--min-rows", "-7"),
        ("--workers", "0"),
        ("--workers", "-3"),
        ("--timeout", "0"),
        ("--timeout", "-2.5"),
        ("--sample", "-1"),
        ("--limit", "-4"),
    ],
)
def test_a_numeric_flag_out_of_range_is_a_usage_error(tmp_path, capsys, bad):
    rc = gp.main(["--old", "x", "--artifact-dir", str(tmp_path / "a"), *bad])
    captured = capsys.readouterr()
    assert rc == 2
    assert captured.out.rstrip("\n").splitlines()[-1].startswith("RESULT: ERROR rc=2 ")
    assert bad[0] in captured.err  # argparse named the flag it refused


def test_the_zero_values_that_mean_something_are_still_accepted():
    args = gp.build_parser().parse_args(
        ["--old", "x", "--artifact-dir", "d", "--sample", "0", "--limit", "0"]
    )
    assert (args.sample, args.limit) == (0, 0)


ESCAPING_GUARD = GUARD_OLD.replace(
    "refused: policy", "refused: \\x1b]0;pwned-title\\x07policy"
)


def test_guard_stderr_shown_in_the_report_carries_no_raw_control_character(tmp_path):
    """A guard quotes the command it refused; a command can hold ESC and OSC sequences, which
    would reach the operator's terminal from the report."""
    repo, old, new = scope_repo(tmp_path, ESCAPING_GUARD, GUARD_ALLOWS_ALL)
    done = cli(tmp_path, repo, old, new)
    assert fields(done)[0] == "FAIL"
    report = (tmp_path / "art" / "report.txt").read_text()
    for text in (done.stdout, report):
        assert "\x1b" not in text and "\x07" not in text
        assert "old said: refused: \\x1b]0;pwned-title\\x07policy" in text


def test_a_lone_surrogate_in_a_reading_cannot_break_the_report_or_the_print(
    tmp_path, capsys
):
    unit = ("id1", "g")
    detail = gp.Detail(
        {"id1": gp.Row("id1", "/x", {"command": "ls"})},
        {"old": {unit: [R(2, text="refused \ud800 here\n")]}, "new": {unit: [R(0)]}},
        {"old": {}, "new": {}},
    )
    ev = evaluation(verdicts={unit: gp.Verdict("OPENED", False)})
    text = gp.render_report(["header"], ev, gp.decide(ev), detail, ["g"])
    assert "refused \\ud800 here" in text  # shown escaped, as the cmd= field is
    report = tmp_path / "report.txt"
    gp.write_report(report, "raw \ud800 surrogate\n")
    assert report.read_text() == "raw \\ud800 surrogate\n"
    gp.emit("printed \ud800 surrogate\n")
    assert capsys.readouterr().out == "printed \\ud800 surrogate\n"


def test_a_failed_run_leaves_no_report_from_an_earlier_one(tmp_path):
    """A stale report.txt ending in `RESULT: PASS` beside a run that failed reads as that run's
    verdict."""
    repo, old, new = scope_repo(tmp_path, GUARD_OLD, GUARD_ALLOWS_ALL)
    first = cli(tmp_path, repo, old, new)  # FAIL: leaves both files
    art = tmp_path / "art"
    assert fields(first)[0] == "FAIL"
    assert (art / "report.txt").exists() and (art / "differences.jsonl").exists()
    second = cli(tmp_path, repo, "no-such-rev", new)  # fails AFTER the lock is taken
    assert second.returncode == 2 and "does not name a commit" in second.stdout
    assert not (art / "report.txt").exists()
    assert not (art / "differences.jsonl").exists()


PROBE_GUARD = """
    import os, sys
    sys.stderr.write("probe=" + os.environ.get("GP_PROBE_VAR", "MISSING"))
"""


def test_the_stage_launches_workers_under_exactly_the_env_it_was_given(tmp_path):
    """Pins `env=dict(env)` on the launch: without it a worker inherits THIS process's
    environment and the replay HOME, hash seed and log path never reach a guard."""
    root = fake_build(tmp_path, {GUARD_FILE: PROBE_GUARD}, name="probe")
    builds = [gp.Build("old", "old", "0" * 40, root, 1)]
    env = dict(os.environ, PYTHONHASHSEED="0", GP_PROBE_VAR="distinct-7431")
    before = dict(os.environ)
    result = stage(tmp_path, builds, [spec("ls")], workers=1, env=env)
    assert result.incomplete == []
    assert result.readings["old"][("ls", GUARD_FILE)][0].text == "probe=distinct-7431"
    assert dict(os.environ) == before  # handed per call, never exported
    assert "GP_PROBE_VAR" not in os.environ


def _real_home(tmp_path):
    real = tmp_path / "real"
    (real / ".config").mkdir(parents=True)
    (real / ".gitconfig").write_text("[user]\n\tname = op\n")
    (real / ".bashrc").write_text("x\n")
    return real


@pytest.mark.parametrize("entry", [".gitconfig", ".config"])
def test_a_fixture_home_that_cannot_mirror_git_config_is_an_error(
    tmp_path, monkeypatch, entry
):
    """Git reads the operator's global config through these two; a home without them replays a
    different git, and every row reads under it."""
    real = _real_home(tmp_path)
    original = os.symlink

    def refuse(src, dst, *a, **k):
        if os.path.basename(dst) == entry:
            raise PermissionError(13, "denied")
        return original(src, dst, *a, **k)

    monkeypatch.setattr(os, "symlink", refuse)
    with pytest.raises(gp.FixtureError, match=rf"{entry}.*denied|denied.*{entry}"):
        gp.make_fixture_home(tmp_path / "fix", real, "a/b")


def test_a_fixture_home_still_skips_an_unmirrorable_other_entry(tmp_path, monkeypatch):
    real = _real_home(tmp_path)
    original = os.symlink

    def refuse(src, dst, *a, **k):
        if os.path.basename(dst) == ".bashrc":
            raise PermissionError(13, "denied")
        return original(src, dst, *a, **k)

    monkeypatch.setattr(os, "symlink", refuse)
    fixture = gp.make_fixture_home(tmp_path / "fix", real, "a/b")
    assert (fixture / ".gitconfig").is_symlink() and not (fixture / ".bashrc").exists()


def test_a_relative_real_home_yields_links_that_resolve(tmp_path, monkeypatch):
    _real_home(tmp_path)
    monkeypatch.chdir(tmp_path)
    fixture = gp.make_fixture_home(tmp_path / "fix", Path("real"), "a/b")
    assert (fixture / ".gitconfig").read_text() == "[user]\n\tname = op\n"
    assert (fixture / ".config").is_dir()


def test_a_reused_fixture_home_dest_does_not_raise(tmp_path):
    real = _real_home(tmp_path)
    dest = tmp_path / "fix"
    gp.make_fixture_home(dest, real, "a/b")
    again = gp.make_fixture_home(dest, real, "c/d")
    assert (again / ".gitconfig").read_text() == "[user]\n\tname = op\n"
    assert (
        "GUARD_REPO_PATTERN=c/d"
        in (again / ".claude" / ".git-timing-guard.conf").read_text()
    )


def test_an_unusable_dot_claude_is_a_fixture_error_and_an_empty_pattern_is_refused(
    tmp_path,
):
    real = _real_home(tmp_path)
    dest = tmp_path / "fix"
    dest.mkdir()
    (dest / ".claude").write_text("a file where a directory belongs")
    with pytest.raises(gp.FixtureError, match=r"\.claude"):
        gp.make_fixture_home(dest, real, "a/b")
    with pytest.raises(gp.FixtureError, match="empty"):
        gp.make_fixture_home(tmp_path / "fix2", real, "")


def test_a_guard_that_cannot_be_launched_is_not_a_hang_and_never_compares_as_same(
    tmp_path,
):
    """A launch failure used to read as TIMED_OUT, the same as a guard that hung."""
    build = fake_build(tmp_path, {GUARD_FILE: "pass\n"})
    (build / "scripts" / GUARD_FILE).chmod(
        0o644
    )  # not executable: exec mode cannot start it
    units, _ = run_worker(tmp_path, build, [spec("ls")], mode="exec")
    reading = units[("ls", GUARD_FILE)][0]
    assert reading.rc is None and reading != gp.TIMED_OUT
    assert reading.text.startswith("could not launch: ")
    assert gp.classify([reading], [reading]).kind == "INCOMPARABLE"
    assert gp.classify([reading], [reading], reading, reading).kind == "INCOMPARABLE"
    detail = gp.Detail(
        {"i": gp.Row("i", "/x", {"command": "ls"})},
        {"old": {("i", "g"): [reading]}, "new": {("i", "g"): [reading]}},
        {"old": {}, "new": {}},
    )
    assert "old=launch-failed" in gp._unit_line("INCOMPARABLE", ("i", "g"), detail)


def test_a_fork_child_that_fails_inside_the_tool_says_so_in_its_capture(tmp_path):
    """Status 70 alone is a value a guard can exit with; the capture has to name the failure."""
    build = fake_build(tmp_path, {GUARD_FILE: "pass\n"})
    compiled = gp.compile_guard(build / "scripts" / GUARD_FILE)
    reading = gp.run_fork(compiled, b"{}", tmp_path / "no-such-neutral-dir", [])
    assert reading.rc == 70
    assert reading.text.startswith("guard-parity internal error: ")
    assert "no-such-neutral-dir" in reading.text


@pytest.mark.parametrize("mode", ["fork", "exec"])
def test_a_relative_build_path_reads_the_same_as_the_absolute_one(tmp_path, mode):
    """The child changes directory before it runs the guard, so a relative build path would point
    its `__file__`, sys.path and sibling imports somewhere else."""
    source = "import sibling, sys\nprint(sibling.VALUE, __file__, sys.path[0])\n"
    build = fake_build(tmp_path, {GUARD_FILE: source}, name="rel-build")
    (build / "scripts" / "sibling.py").write_text("VALUE = 8123\n")
    absolute, _ = run_worker(tmp_path, build, [spec("ls")], mode=mode)
    relative, _ = run_worker(
        tmp_path, Path("rel-build"), [spec("ls")], mode=mode, cwd=tmp_path
    )
    assert absolute[("ls", GUARD_FILE)][0].rc == 0
    assert relative == absolute


# ---------------------------------------------------------------------------------------------
# resume key: each input that made a shard is varied ALONE
# ---------------------------------------------------------------------------------------------

KEYED_GUARDS = [GUARD_FILE, "other-guard.py"]


def keyed_build(tmp_path):
    root = fake_build(
        tmp_path, {GUARD_FILE: "pass\n", "other-guard.py": "pass\n"}, name="keyed"
    )
    return [gp.Build("old", "old", "0" * 40, root, 1)]


def keyed_stage(tmp_path, builds, **over):
    """One shard, every keyed input at a distinctive baseline, `over` replacing some of them."""
    neutral = tmp_path / "neutral"
    neutral.mkdir(exist_ok=True)
    args = dict(
        mode="fork",
        repeat=1,
        workdir=tmp_path / "work",
        tag="k",
        workers=1,
        env=dict(os.environ, PYTHONHASHSEED="0", GP_KEYED_VALUE="value-one"),
        neutral=neutral,
        timeout=30,
        salt="salt-one",
    )
    guards = over.pop("guards", KEYED_GUARDS)
    rows = over.pop("rows", [spec("ls")])
    args.update(over)
    return gp.run_stage(builds, rows, guards, **args)


def _with_env(**extra):
    return {"env": dict(os.environ, PYTHONHASHSEED="0", **extra)}


def _executable_alias(tmp_path):
    alias = tmp_path / "python-alias"
    alias.symlink_to(os.path.realpath(sys.executable))
    return alias


VARIANTS = {
    "guards": lambda mp, tp: {"guards": [GUARD_FILE]},
    "mode": lambda mp, tp: {"mode": "exec"},
    "repeat": lambda mp, tp: {"repeat": 2},
    "rows": lambda mp, tp: {"rows": [spec("a different command")]},
    "env-name": lambda mp, tp: _with_env(GP_KEYED_VALUE="value-one", GP_NEW_NAME="x"),
    "env-value": lambda mp, tp: _with_env(GP_KEYED_VALUE="value-two"),
    "salt": lambda mp, tp: {"salt": "salt-two"},
    "interpreter-version": lambda mp, tp: (
        mp.setattr(sys, "version", "9.9.9 distinct") or {}
    ),
    "interpreter-path": lambda mp, tp: (
        mp.setattr(sys, "executable", str(_executable_alias(tp))) or {}
    ),
}


def test_the_baseline_is_reused_when_nothing_changes(tmp_path):
    builds = keyed_build(tmp_path)
    first = keyed_stage(tmp_path, builds)
    assert first.reused == 0 and first.total == 1 and first.incomplete == []
    again = keyed_stage(tmp_path, builds)
    assert again.reused == again.total == 1


@pytest.mark.parametrize("variant", sorted(VARIANTS))
def test_a_shard_is_run_again_when_only_one_keyed_input_changes(
    tmp_path, monkeypatch, variant
):
    builds = keyed_build(tmp_path)
    keyed_stage(tmp_path, builds)
    changed = VARIANTS[variant](monkeypatch, tmp_path)
    second = keyed_stage(tmp_path, builds, **changed)
    assert second.incomplete == [] and second.reused == 0, variant


def test_a_key_file_that_is_not_utf8_reads_as_a_rerun(tmp_path):
    builds = keyed_build(tmp_path)
    keyed_stage(tmp_path, builds)
    for key in (tmp_path / "work").glob("*.key"):
        key.write_bytes(b"\xff\xfe\x80 not text")
    second = keyed_stage(tmp_path, builds)
    assert second.reused == 0 and second.incomplete == []


def test_an_unreadable_tool_source_forces_a_rerun_instead_of_a_crash(
    tmp_path, monkeypatch
):
    builds = keyed_build(tmp_path)
    keyed_stage(tmp_path, builds)
    monkeypatch.setattr(gp, "__file__", str(tmp_path / "gone" / "guard-parity.py"))
    second = keyed_stage(tmp_path, builds)  # its worker cannot start either
    assert second.reused == 0

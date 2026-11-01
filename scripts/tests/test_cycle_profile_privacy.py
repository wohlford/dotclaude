"""Tests for the cycle-profile privacy module.

Every transcript and delegate file is synthetic (fictional base date 2030-01-01, invented ids,
lorem text). The rows pin the out-dir guard (git exit status, the filesystem walk, symlinks,
the marker rule), the single writer and its modes, the output allowlist, the leak check with its
planted control and floor, the pinned delegate re-read, the sampler and the remedy-word finder.
Two rows isolate one half of the repository test by replacing the walk (`_walk_finds_repository`)
with a function that finds nothing, so the git exit status alone decides; another isolates the
walk with a `git` shim that always exits 128.
"""

import hashlib
import inspect
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import cycle_profile_helpers as h  # noqa: E402, I001
import cycle_profile_model as m  # noqa: E402
import cycle_profile_privacy as p  # noqa: E402
import cycle_profile_records as r  # noqa: E402

TIMEOUT_S = 60


def _git(*args, cwd):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=TIMEOUT_S,
        check=False,
    )


def _init_repo(path, bare=False):
    path.mkdir(parents=True)
    args = ["init", "-q"] + (["--bare"] if bare else [])
    assert _git(*args, cwd=path).returncode == 0
    assert _git("config", "commit.gpgsign", "false", cwd=path).returncode == 0
    assert _git("config", "tag.gpgsign", "false", cwd=path).returncode == 0
    return path


def _layout(tmp_path):
    """Resolved temp root plus a synthetic transcript at `<root>/in/synth000.jsonl`."""
    root = Path(tmp_path).resolve()
    transcript = h.write_transcript(root, b"")
    return root, transcript


def _refusal(out, transcript, delegates=None):
    with pytest.raises(p.Refusal) as caught:
        p.check_out_dir(out, transcript=transcript, delegates_dir=delegates)
    return caught.value


def _no_walk(monkeypatch):
    monkeypatch.setattr(p, "_walk_finds_repository", lambda resolved: False)


# ---------------------------------------------------------------------------------------------
# t21p: the out-dir guard
# ---------------------------------------------------------------------------------------------


def test_t21pa_nonzero_git_exit_is_not_allow(tmp_path, monkeypatch):
    root, transcript = _layout(tmp_path)
    repo = _init_repo(root / "repo")
    bare = _init_repo(root / "bare.git", bare=True)
    for out in (repo / ".git" / "x", bare / "x"):
        # `rev-parse --show-toplevel` exits 128 at both places (a status that reads as a
        # plain directory), so the exit-0 answer of `--git-dir` alone must refuse them.
        _no_walk(monkeypatch)
        err = _refusal(out, transcript)
        assert err.reason == "inside-work-tree"
        assert str(err) == err.reason and "/" not in str(err)
        monkeypatch.undo()
        assert _refusal(out, transcript).reason == "inside-work-tree"
    # Control: with the walk replaced, a plain directory is still accepted.
    plain = root / "plain"
    plain.mkdir()
    _no_walk(monkeypatch)
    assert p.check_out_dir(plain / "out", transcript=transcript).existed is False


def test_t21pb_gitignored_directory_refused(tmp_path, monkeypatch):
    root, transcript = _layout(tmp_path)
    repo = _init_repo(root / "repo")
    (repo / ".gitignore").write_text("ign/\n")
    (repo / "ign").mkdir()
    out = repo / "ign" / "out"
    assert _git("check-ignore", "-q", "ign", cwd=repo).returncode == 0
    _no_walk(monkeypatch)
    assert _refusal(out, transcript).reason == "inside-work-tree"
    monkeypatch.undo()
    assert _refusal(out, transcript).reason == "inside-work-tree"


def test_t21pc_symlink_resolved_before_comparing(tmp_path, monkeypatch):
    root, transcript = _layout(tmp_path)
    (root / "link").symlink_to(transcript.parent)
    assert (
        _refusal(root / "link" / "out", transcript).reason == "inside-transcript-tree"
    )
    # The transcript side is resolved too: a symlinked path to the transcript.
    (root / "tlink").symlink_to(transcript.parent)
    tpath = root / "tlink" / transcript.name
    assert _refusal(transcript.parent / "out", tpath).reason == "inside-transcript-tree"
    # An out-dir that merely shares a name prefix with the transcript's directory is fine.
    sibling = root / "in-other" / "out"
    assert p.check_out_dir(sibling, transcript=transcript).existed is False
    # Resolution alone: with the filesystem-identity fallback disabled, only the resolved
    # paths can tell that the symlinked spellings name the protected directory.
    monkeypatch.setattr(p, "_ident", lambda path: None)
    assert (
        _refusal(root / "link" / "out", transcript).reason == "inside-transcript-tree"
    )
    assert _refusal(transcript.parent / "out", tpath).reason == "inside-transcript-tree"


def test_t21pd_marker_rule_and_trees(tmp_path):
    root, transcript = _layout(tmp_path)
    full = root / "full"
    full.mkdir()
    (full / "someone-elses-file").write_text("x")
    assert _refusal(full, transcript).reason == "non-empty-no-marker"

    empty = root / "empty"
    empty.mkdir()
    plan = p.check_out_dir(empty, transcript=transcript)
    assert plan.existed and not plan.marked

    reuse = root / "reuse"
    reuse.mkdir()
    (reuse / p.MARKER).write_text("")
    (reuse / "report.md").write_text("old")
    (reuse / ".report.md.tmp").write_text("stale")
    (reuse / ".data.json.tmp").write_text("stale")
    plan = p.check_out_dir(reuse, transcript=transcript)
    assert plan.existed and plan.marked
    assert p.prepare_out_dir(plan) == 2
    assert sorted(e.name for e in os.scandir(reuse)) == [p.MARKER, "report.md"]
    assert p.prepare_out_dir(plan) == 0

    one = root / "one"
    one.mkdir()
    (one / p.MARKER).write_text("")
    (one / ".data.json.tmp").write_text("stale")
    assert p.prepare_out_dir(p.check_out_dir(one, transcript=transcript)) == 1

    assert (
        _refusal(transcript.parent / "out", transcript).reason
        == "inside-transcript-tree"
    )
    delegates = root / "dele"
    delegates.mkdir()
    err = _refusal(delegates / "out", transcript, delegates)
    assert err.reason == "inside-transcript-tree"
    assert _refusal(root, transcript).reason == "contains-transcript"
    assert _refusal(root, transcript, delegates).reason == "contains-transcript"


def test_t21pe_git_absent_is_could_not_decide(tmp_path, monkeypatch):
    root, transcript = _layout(tmp_path)
    plain = root / "plain"
    plain.mkdir()
    empty_path = root / "empty-bin"
    empty_path.mkdir()
    monkeypatch.setenv("PATH", str(empty_path))
    err = _refusal(plain / "out", transcript)
    assert err.reason == "could-not-decide"
    assert str(err) == "could-not-decide"


def _shim_git(root, status):
    shim = root / "shim-bin"
    shim.mkdir()
    exe = shim / "git"
    exe.write_text("#!/bin/sh\nexit %d\n" % status)
    exe.chmod(0o755)
    return shim


def test_t21pf_filesystem_walk_alone_refuses(tmp_path, monkeypatch):
    root, transcript = _layout(tmp_path)
    repo = _init_repo(root / "repo")
    bare = _init_repo(root / "bare.git", bare=True)
    shim = _shim_git(root, 128)
    monkeypatch.setenv("PATH", str(shim) + os.pathsep + os.environ["PATH"])
    # The shim answers 128 ("not a repository") inside a real repository: only the walk
    # can refuse. A repository work tree, its `.git` directory and a bare repository.
    for out in (repo / "out", repo / ".git" / "x", bare / "x"):
        assert _refusal(out, transcript).reason == "inside-work-tree"
    # Control: the shim really is the git consulted (walk replaced, so nothing refuses).
    _no_walk(monkeypatch)
    assert p.check_out_dir(repo / "out", transcript=transcript).existed is False
    # And a status other than 0 or 128 refuses as could-not-decide.
    other = root / "other-shim"
    other.mkdir()
    (other / "git").write_text("#!/bin/sh\nexit 3\n")
    (other / "git").chmod(0o755)
    monkeypatch.setenv("PATH", str(other) + os.pathsep + os.environ["PATH"])
    assert _refusal(repo / "out", transcript).reason == "could-not-decide"


@pytest.mark.parametrize(
    "variable", ["GIT_DIR", "GIT_CEILING_DIRECTORIES", "GIT_WORK_TREE"]
)
def test_t21pg_git_environment_scrubbed(tmp_path, monkeypatch, variable):
    root, transcript = _layout(tmp_path)
    repo = _init_repo(root / "repo")
    (repo / "sub").mkdir()
    # Each variable is set alone, at a value that changes what git answers for `ancestor`:
    # GIT_DIR names a missing directory; the ceiling forbids climbing into the repository
    # from its subdirectory. GIT_WORK_TREE alone does not change `rev-parse --git-dir` (it
    # exits 0 either way, measured), so that case cannot go red by itself; it is kept as a
    # guard that setting it never makes the guard allow.
    values = {
        "GIT_DIR": root / "nowhere",
        "GIT_CEILING_DIRECTORIES": repo,
        "GIT_WORK_TREE": root / "nowhere-either",
    }
    monkeypatch.setenv(variable, str(values[variable]))
    unscrubbed = subprocess.run(
        ["git", "-C", str(repo / "sub"), "rev-parse", "--git-dir"],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=TIMEOUT_S,
        check=False,
    )
    if variable != "GIT_WORK_TREE":
        # Control: unscrubbed, the variable really does break git inside the repository.
        assert unscrubbed.returncode != 0
    else:
        assert unscrubbed.returncode == 0
    _no_walk(monkeypatch)
    assert _refusal(repo / "sub" / "out", transcript).reason == "inside-work-tree"
    assert _refusal(repo / ".git" / "x", transcript).reason == "inside-work-tree"


def test_t21ph_plain_directory_accepted_and_created_0700(tmp_path):
    root, transcript = _layout(tmp_path)
    plain = root / "plain"
    plain.mkdir()
    out = plain / "out"
    old = os.umask(0)
    try:
        plan = p.check_out_dir(out, transcript=transcript)
        assert not out.exists(), "the guard itself writes nothing"
        assert plan.path == out and not plan.existed and not plan.marked
        assert p.prepare_out_dir(plan) == 0
        assert stat.S_IMODE(out.stat().st_mode) == 0o700
    finally:
        os.umask(old)
    # An existing empty directory is accepted as is.
    again = p.check_out_dir(out, transcript=transcript)
    assert again.existed and not again.marked


def _case_insensitive(directory):
    probe = directory / "CaseProbe"
    probe.mkdir()
    return (directory / "caseprobe").exists()


def test_t21pi_case_variant_path_is_the_same_directory(tmp_path):
    root, transcript = _layout(tmp_path)
    delegates = root / "dele"
    delegates.mkdir()
    folded = _case_insensitive(root)
    variant = root / "IN"
    variant_dele = root / "DELE"
    if folded:
        # One directory under two spellings: refused, existing or not-yet-created tail.
        assert (
            _refusal(variant / "newout", transcript).reason == "inside-transcript-tree"
        )
        assert (
            _refusal(variant / "a" / "b" / "c", transcript).reason
            == "inside-transcript-tree"
        )
        err = _refusal(variant_dele / "newout", transcript, delegates)
        assert err.reason == "inside-transcript-tree"
        # The contains direction: an existing out-dir spelled with the other case.
        deep = h.write_transcript(root / "deep", b"")
        assert _refusal(root / "DEEP", deep).reason == "contains-transcript"
    else:
        # Distinct directories on a case-sensitive volume: nothing to refuse.
        assert not variant.exists()
        assert (
            p.check_out_dir(variant / "newout", transcript=transcript).existed is False
        )
    # Same spelling is always refused (the fast path), whatever the volume.
    assert (
        _refusal(root / "in" / "newout", transcript).reason == "inside-transcript-tree"
    )


def test_t21pj_symlinked_transcript_protects_its_logical_directory(tmp_path):
    root, _ = _layout(tmp_path)
    real = root / "real"
    real.mkdir()
    (real / "r.jsonl").write_bytes(b"")
    logical = root / "logical"
    logical.mkdir()
    link = logical / "link.jsonl"
    link.symlink_to(real / "r.jsonl")
    # Both the directory the link sits in and the one it points into are protected.
    assert _refusal(logical / "o2", link).reason == "inside-transcript-tree"
    assert _refusal(real / "o3", link).reason == "inside-transcript-tree"
    assert _refusal(root, link).reason == "contains-transcript"
    # A hard link to the transcript changes nothing: its directory is its own parent.
    hard = root / "hard"
    hard.mkdir()
    os.link(real / "r.jsonl", hard / "h.jsonl")
    assert _refusal(hard / "o4", hard / "h.jsonl").reason == "inside-transcript-tree"
    other = root / "other"
    assert p.check_out_dir(other / "out", transcript=link).existed is False


def _assert_closed(err, *secrets):
    assert err.reason in p.REFUSAL_REASONS
    for text in (str(err), repr(err), repr(err.args), err.reason):
        for secret in secrets:
            assert secret not in text
    assert err.__cause__ is None and err.__context__ is None


def test_t21pk_nul_byte_path_is_could_not_decide(tmp_path):
    root, transcript = _layout(tmp_path)
    bad = str(root / "plain") + "\0" + "secretname"
    for kwargs in (
        {"out_dir": bad, "transcript": transcript},
        {"out_dir": root / "plain", "transcript": bad},
        {"out_dir": root / "plain", "transcript": transcript, "delegates_dir": bad},
    ):
        with pytest.raises(p.Refusal) as caught:
            p.check_out_dir(**kwargs)
        assert caught.value.reason == "could-not-decide"
        _assert_closed(caught.value, "secretname", root.name, "plain")


def test_t21pl_unsearchable_directory_is_could_not_decide(tmp_path):
    root, transcript = _layout(tmp_path)
    locked = root / "lockeddir"
    locked.mkdir()
    (locked / "inner").mkdir()
    locked.chmod(0)
    try:
        if os.access(locked, os.R_OK | os.X_OK):
            pytest.fail("the fixture directory is still searchable (running as root?)")
        for out in (locked, locked / "newout", locked / "inner"):
            err = _refusal(out, transcript)
            assert err.reason == "could-not-decide"
            _assert_closed(err, "lockeddir", "inner")
    finally:
        locked.chmod(0o700)


# ---------------------------------------------------------------------------------------------
# t22p: the writer
# ---------------------------------------------------------------------------------------------


def test_t22pa_write_names(tmp_path, monkeypatch):
    out = Path(tmp_path).resolve() / "o"
    out.mkdir()
    replaced = []
    real_replace = os.replace

    def spy(src, dst):
        replaced.append((Path(src).name, Path(dst).name))
        real_replace(src, dst)

    monkeypatch.setattr(p.os, "replace", spy)
    (out / ".report.md.tmp").write_text("stale temp")
    p.write_output(out, "report.md", b"report")
    p.write_output(out, "data.json", b"{}")
    p.write_output(out, ".cycle-profile-out", b"")
    assert sorted(e.name for e in os.scandir(out)) == [
        ".cycle-profile-out",
        "data.json",
        "report.md",
    ]
    assert (out / "report.md").read_bytes() == b"report"
    assert replaced == [
        (".report.md.tmp", "report.md"),
        (".data.json.tmp", "data.json"),
    ]
    p.write_output(out, "report.md", b"second")
    assert (out / "report.md").read_bytes() == b"second"
    for bad in ("other.txt", "../escape", ".report.md.tmp", "report.md/x", ""):
        with pytest.raises(p.PrivacyRefusal) as caught:
            p.write_output(out, bad, b"x")
        assert bad not in str(caught.value) or bad == ""
    with pytest.raises(p.PrivacyRefusal):
        p.write_output(out, "report.md", "not bytes")
    assert sorted(e.name for e in os.scandir(out)) == [
        ".cycle-profile-out",
        "data.json",
        "report.md",
    ]
    ok = p.outscope_listing(out)
    assert (ok.name, ok.verdict) == ("OUTSCOPE", "pass")
    (out / "zz-stray-entry").write_text("x")
    bad = p.outscope_listing(out)
    assert bad.verdict == "fail" and "unknown=1" in bad.detail
    assert "stray" not in bad.detail
    (out / "zz-stray-entry").unlink()
    (out / ".data.json.tmp").write_text("left over")
    assert p.outscope_listing(out).verdict == "fail"


def test_t22pb_modes_0600(tmp_path):
    out = Path(tmp_path).resolve() / "o"
    out.mkdir()
    (out / ".data.json.tmp").write_text("old")
    (out / ".data.json.tmp").chmod(0o644)
    old = os.umask(0)
    try:
        for name in p.OUTPUT_NAMES:
            p.write_output(out, name, b"x")
    finally:
        os.umask(old)
    for name in p.OUTPUT_NAMES:
        assert stat.S_IMODE((out / name).stat().st_mode) == 0o600, name


def test_t22pc_failed_write_leaves_no_temp(tmp_path, monkeypatch):
    out = Path(tmp_path).resolve() / "o"
    out.mkdir()

    def boom(*args, **kwargs):
        raise OSError("simulated write failure")

    for target in ("fdopen", "replace"):
        monkeypatch.setattr(p.os, target, boom)
        names = (
            (p.REPORT, p.DATA, p.MARKER) if target == "fdopen" else (p.REPORT, p.DATA)
        )
        for name in names:
            with pytest.raises(OSError):
                p.write_output(out, name, b"data")
            assert sorted(e.name for e in os.scandir(out)) == []
        monkeypatch.undo()
    # The failure path leaves the previous good file alone.
    p.write_output(out, p.REPORT, b"good")
    monkeypatch.setattr(p.os, "replace", boom)
    with pytest.raises(OSError):
        p.write_output(out, p.REPORT, b"new")
    assert (out / p.REPORT).read_bytes() == b"good"
    assert sorted(e.name for e in os.scandir(out)) == [p.REPORT]


# ---------------------------------------------------------------------------------------------
# t24p, t25: the leak check
# ---------------------------------------------------------------------------------------------


def _windows(count):
    return [h.lorem(i)[:40] for i in range(count)]


def test_t24pa_planted_control_missed_is_not_detected():
    windows = _windows(5)
    report = b"nothing of the transcript here"
    data = b"{}"
    blind = p.leak_check(report, data, windows, floor=1, matcher=lambda w, hays: False)
    assert blind.control == "not-detected"
    assert p.leak_verdict(blind) == "ERROR"
    sound = p.leak_check(report, data, windows, floor=1)
    assert sound.control == "detected" and sound.hits == 0
    assert p.leak_verdict(sound) == "PASS"

    def raw_only(window, hays):
        return any(window in hay for hay in hays)

    # A matcher that cannot see the escaped form misses the escaped half of the control.
    half = p.leak_check(report, data, windows, floor=1, matcher=raw_only)
    assert half.control == "not-detected"
    # An all-seeing matcher keeps the control and reports every window as a hit.
    eager = p.leak_check(report, data, windows, floor=1, matcher=lambda w, hays: True)
    assert (eager.control, eager.hits) == ("detected", 5)
    assert p.leak_verdict(eager) == "FAIL"
    # Hits are counted per window, in either output; a miss is not a hit.
    leaky = p.leak_check(windows[0].encode(), windows[1].encode(), windows, floor=1)
    assert leaky.hits == 2


def test_t24pb_floor():
    windows = _windows(5)
    assert inspect.signature(p.leak_check).parameters["floor"].default == (
        m.Params().leak_floor
    )
    under_default = p.leak_check(b"", b"{}", windows)
    assert not under_default.floor_ok
    assert p.leak_verdict(under_default) == "INCOMPLETE"
    at_floor = p.leak_check(b"", b"{}", windows, floor=5)
    assert at_floor.floor_ok and p.leak_verdict(at_floor) == "PASS"
    below = p.leak_check(b"", b"{}", windows, floor=6)
    assert not below.floor_ok and p.leak_verdict(below) == "INCOMPLETE"
    assert below.samples == 5
    # Precedence: a hit outranks a missing floor; a missed control outranks both.
    both = p.leak_check(windows[0].encode(), b"{}", windows, floor=6)
    assert p.leak_verdict(both) == "FAIL"
    blind = p.leak_check(
        windows[0].encode(),
        b"{}",
        windows,
        floor=6,
        matcher=lambda w, hays: w in hays[0],
    )
    assert p.leak_verdict(blind) == "ERROR"


def test_t24pc_nothing_sampled_never_passes():
    empty = p.leak_check(b"", b"{}", [], floor=0)
    assert empty.samples == 0 and not empty.floor_ok
    assert p.leak_verdict(empty) == "INCOMPLETE"
    # Boilerplate sharing one window counts once, so it cannot inflate `samples`.
    same = p.leak_check(b"", b"{}", [_windows(1)[0]] * 7 + _windows(3), floor=1)
    assert same.samples == len(set(_windows(1) + _windows(3)))
    assert p.leak_check(b"", b"{}", [_windows(1)[0]] * 7, floor=2).floor_ok is False
    assert p.leak_check(b"", b"{}", [_windows(1)[0]] * 7, floor=1).floor_ok is True


def test_t25_escaped_form_is_found():
    window = 'quoted "word" and café lorem ipsum dolor sit'[:40]
    assert '"' in window and "é" in window
    data = json.dumps({"x": window}).encode()
    assert window not in data.decode()
    assert json.dumps(window)[1:-1] in data.decode()
    result = p.leak_check(b"", data, [window], floor=1)
    assert result.hits == 1
    # The same window absent from both outputs is not a hit.
    other = json.dumps({"x": "unrelated"}).encode()
    assert p.leak_check(b"", other, [window], floor=1).hits == 0
    # A report copy carrying it raw is found too.
    assert p.leak_check(window.encode(), b"{}", [window], floor=1).hits == 1


# ---------------------------------------------------------------------------------------------
# t26p: the allowlist
# ---------------------------------------------------------------------------------------------

KEYS = {"a", "b", "c", "id", "when"}


def _refused(obj, **kw):
    with pytest.raises(p.PrivacyRefusal) as caught:
        p.validate_tree(obj, KEYS, **kw)
    return str(caught.value)


def test_t26p_allowlist():
    stem = "abcd" + "ef01"
    good = {
        "a": "CONTROLLER",
        "b": [
            "2030-01-01T00:00:00Z",
            "2030-01-01T00:00:00.123Z",
            "deadbeef",
            stem,
            "unnamed",
            "gate",
            3,
            1.5,
            None,
            True,
        ],
        "c": {"when": "2030-01-01T00:00:01Z"},
        "id": h.AGENT1,
    }
    p.validate_tree(good, KEYS)

    text = h.lorem(3)
    message = _refused({"a": text})
    assert text not in message and "lorem" not in message
    _refused({"a": "lorem"})  # id-shaped, but not under an id-bearing key
    p.validate_tree({"id": "lorem"}, KEYS)
    _refused({"id": "lorem"}, id_keys=())
    _refused({"zzz": "CONTROLLER"})
    _refused({1: "CONTROLLER"})
    stamp_key = "2030-01-01T00:00:00Z"
    with pytest.raises(p.PrivacyRefusal):
        p.validate_tree({stamp_key: 1}, KEYS | {stamp_key})
    _refused({"a": float("nan")})
    _refused({"a": float("inf")})
    _refused({"a": b"bytes"})
    _refused({"a": {"b": {"c": ["ok" * 100]}}})
    # A sentence with a dynamic value is refused unless the value is a closed channel.
    _refused({"a": "SUMCHECK"})
    p.validate_tree({"a": "SUMCHECK"}, KEYS, vocab={"SUMCHECK"})
    nested = {"a": 1}
    for _ in range(p.MAX_DEPTH + 2):
        nested = {"a": nested}
    _refused(nested)

    # The validators on their own.
    assert p.safe_token("HOOK", m.CLASSES) == "HOOK"
    for bad in ("hook", 3, None):
        with pytest.raises(p.PrivacyRefusal):
            p.safe_token(bad, m.CLASSES)
    assert p.safe_timestamp("2030-01-01T00:00:00Z")
    for bad in ("2030-01-01 00:00:00", "2030-01-01T00:00:00", 5):
        with pytest.raises(p.PrivacyRefusal):
            p.safe_timestamp(bad)
    assert p.safe_hex("deadbeef") and p.safe_hex("a" * 64)
    for bad in ("deadbee"[:6], "DEADBEEF", "a" * 65, "xyzxyzxyz"):
        with pytest.raises(p.PrivacyRefusal):
            p.safe_hex(bad)
    assert p.safe_id(h.AGENT1) == h.AGENT1
    for bad in ("has space", "", "a" * 65, "slash/ed"):
        with pytest.raises(p.PrivacyRefusal):
            p.safe_id(bad)
    assert p.safe_stem(stem) == stem and p.safe_stem("unnamed") == "unnamed"
    with pytest.raises(p.PrivacyRefusal):
        p.safe_stem("synth000")
    assert p.transcript_stem(stem + ".jsonl") == stem
    assert p.transcript_stem("synth000.jsonl") == "unnamed"
    assert p.transcript_stem("ABCDEF01.jsonl") == "unnamed"
    assert p.safe_number(2) == 2
    with pytest.raises(p.PrivacyRefusal):
        p.safe_number(float("nan"))
    assert p.safe_format("{n} of {c}", n=3, c="HOOK") == "3 of HOOK"
    with pytest.raises(p.PrivacyRefusal):
        p.safe_format("{c}", c=h.lorem(1))


def test_t26p_timestamp_is_ascii_and_bounded():
    arabic = "\u0662\u0660\u0663\u0660-01-01T00:00:00Z"
    assert arabic[:4].isdigit()
    with pytest.raises(p.PrivacyRefusal):
        p.safe_timestamp(arabic)
    with pytest.raises(p.PrivacyRefusal):
        p.validate_tree({"a": arabic}, KEYS)
    nine = "2030-01-01T00:00:00." + "1" * 9 + "Z"
    assert p.safe_timestamp(nine) == nine
    for digits in (10, 500):
        long_fraction = "2030-01-01T00:00:00." + "1" * digits + "Z"
        with pytest.raises(p.PrivacyRefusal):
            p.safe_timestamp(long_fraction)
        with pytest.raises(p.PrivacyRefusal):
            p.validate_tree({"a": long_fraction}, KEYS)
    with pytest.raises(p.PrivacyRefusal):
        p.safe_timestamp("2030-01-01T00:00:00.Z")


# ---------------------------------------------------------------------------------------------
# t34p: the pinned delegate re-read
# ---------------------------------------------------------------------------------------------


def _jsonl(*texts):
    lines = []
    for text in texts:
        rec = {"type": "user", "message": {"role": "user", "content": text}}
        lines.append(json.dumps(rec) + "\n")
    return "".join(lines).encode()


def test_t34p_delegate_reread_is_pinned(tmp_path):
    base = Path(tmp_path).resolve()
    first = _jsonl(h.lorem(1), h.lorem(2))
    path = base / "agent-x.jsonl"
    path.write_bytes(first)
    pin = (len(first), hashlib.sha256(first).hexdigest())
    appended = _jsonl("appended zzzz text that was added after the pin was taken ok")
    path.write_bytes(first + appended)
    assert p.reread_delegate(path, *pin) == first
    # A different recorded size is honoured, not the file's size.
    cut = first.index(b"\n") + 1
    short_pin = (cut, hashlib.sha256(first[:cut]).hexdigest())
    assert p.reread_delegate(path, *short_pin) == first[:cut]
    sampled = " ".join(p.sample_candidates(p.reread_delegate(path, *pin), limit=10))
    assert "zzzz" not in sampled
    # Shortened, deleted and prefix-changed files raise.
    path.write_bytes(first[:-5])
    with pytest.raises(r.PinError):
        p.reread_delegate(path, *pin)
    path.write_bytes(b"X" + first[1:] + appended)
    with pytest.raises(r.PinError):
        p.reread_delegate(path, *pin)
    path.write_bytes(first)
    with pytest.raises(r.PinError):
        p.reread_delegate(path, pin[0], "0" * 64)
    path.unlink()
    with pytest.raises(r.ReadError):
        p.reread_delegate(path, *pin)


# ---------------------------------------------------------------------------------------------
# t35: remedy words
# ---------------------------------------------------------------------------------------------


def test_t35_remedy_boundaries():
    hits = p.find_remedy_words(
        "You should avoid it; unavoidable shoulder address change"
    )
    assert sorted(set(hits)) == ["avoid", "should"]
    assert p.find_remedy_words("Avoid") == ["avoid"]
    assert p.find_remedy_words("SHOULD, Propose; speed  up. FIX BY it") == [
        "should",
        "propose",
        "speed up",
        "fix by",
    ]
    for phrase in ("replace with", "we could", "could be", "optimise", "optimize"):
        assert p.find_remedy_words("x " + phrase + " y") == [phrase], phrase
    for word in "propose recommend suggest consider improve eliminate".split():
        assert p.find_remedy_words("x " + word + " y") == [word], word
    for clean in (
        "unavoidable",
        "shoulder",
        "address",
        "change",
        "improved",
        "suggestion",
        "considerable",
        "recommended",
        "worked out",
        "",
    ):
        assert p.find_remedy_words(clean) == [], clean


# ---------------------------------------------------------------------------------------------
# t43: the sampler
# ---------------------------------------------------------------------------------------------


def _sampler_input():
    keep = [h.lorem(i, words=16) for i in range(1, 9)]
    skip = [
        "zzzz short string with spaces",
        "zzzz" + "y" * 80,  # long, no spaces
        "zzzz  " + "y" * 70,  # two spaces
        "/zzzz/path with enough spaces to pass the other tests yyyy yyyy yyyy",
        "~/zzzz home path with enough spaces to pass the other tests yyyy yyyy",
        "zzzz" + "y" * 60,  # id-like
    ]
    recs = []
    for i, text in enumerate(keep):
        if i % 4 == 0:
            rec = {"type": "user", "message": {"content": text}}
        elif i % 4 == 1:
            rec = {
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": text}]},
            }
        elif i % 4 == 2:
            blocks = [{"type": "tool_result", "tool_use_id": "t1", "content": text}]
            rec = {"type": "user", "message": {"content": blocks}}
        else:
            blocks = [
                {"type": "tool_use", "id": "t2", "name": "Bash", "input": {"x": [text]}}
            ]
            rec = {"type": "assistant", "message": {"content": blocks}}
        recs.append(rec)
    for text in skip:
        recs.append({"type": "user", "message": {"content": text}})
    recs.append({"type": "user", "message": {"content": keep[0]}})  # a duplicate
    raw = "\n".join(json.dumps(rec) for rec in recs).encode() + b"\nnot json\n\n"
    assert len(set(keep)) == len(keep)
    return raw, keep


def test_t43_candidate_rule_and_seeded_windows(tmp_path, monkeypatch):
    raw, keep = _sampler_input()
    monkeypatch.chdir(tmp_path)
    before = sorted(os.listdir(tmp_path))
    windows = p.sample_candidates(raw)
    assert len(windows) == len(keep)
    assert all(len(w) == p.WINDOW_CHARS for w in windows)
    assert all("zzzz" not in w for w in windows)
    for window in windows:
        assert any(window in text for text in keep)
    assert windows == p.sample_candidates(raw)
    assert windows == p.sample_candidates(raw, seed=0)
    # A different seed gives different windows over the same candidates.
    assert p.sample_candidates(raw, seed=1) != windows
    assert p.sample_candidates(raw, seed=1) == p.sample_candidates(raw, seed=1)
    # Mid-string windows are probed: across seeds the window is not always a prefix.
    offsets = set()
    for seed in range(6):
        got = p.sample_candidates(raw, seed=seed)
        assert len(got) == len(keep)
        for i, window in enumerate(got):
            offsets.add(keep[i].find(window))
    assert any(off > 0 for off in offsets)
    # The limit is honoured (non-default value) and the subset is seed-determined.
    few = p.sample_candidates(raw, limit=3)
    assert len(few) == 3
    assert all(any(w in text for text in keep) for w in few)
    assert p.sample_candidates(raw, limit=3) == few
    assert p.sample_candidates(raw, limit=300) == windows
    assert p.sample_candidates(b"") == []
    assert sorted(os.listdir(tmp_path)) == before, "nothing is written to disk"


def test_t43_window_needs_text(monkeypatch):
    padding = "ab c" + " " * 59
    assert len(padding) == 63 and padding.count(" ") >= 3
    raw = _jsonl(padding)
    assert p.sample_candidates(raw) == []
    # A string where every 40-character window holds at least 20 non-space characters.
    dense = "a " * 40
    for seed in range(8):
        got = p.sample_candidates(_jsonl(dense), seed=seed)
        assert len(got) == 1 and len(got[0]) == p.WINDOW_CHARS
        assert sum(1 for c in got[0] if not c.isspace()) >= p.MIN_WINDOW_NONSPACE
    # Non-default thresholds move the answer both ways.
    monkeypatch.setattr(p, "MIN_WINDOW_NONSPACE", 1)
    assert len(p.sample_candidates(raw)) == 1
    monkeypatch.setattr(p, "MIN_WINDOW_NONSPACE", 41)
    assert p.sample_candidates(_jsonl(dense)) == []

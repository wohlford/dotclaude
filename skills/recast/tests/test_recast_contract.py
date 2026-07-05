"""Contract tests for recast-contract.sh — per-commit verifier, fail-closed selection."""

from recast_helpers import CONTRACT_SH, git, run


def _repo(tmp_path, marker_at=(1, 2, 3)):
    """Three tagged commits c1..c3 (v0.1.0..v0.3.0); marker.txt exists at the listed ones."""
    r = tmp_path / "repo"
    r.mkdir()
    git(r, "init", "-q")
    for i in (1, 2, 3):
        (r / f"c{i}.txt").write_text(f"commit {i}\n")
        marker = r / "marker.txt"
        if i in marker_at:
            marker.write_text("present\n")
        elif marker.exists():
            marker.unlink()
        git(r, "add", "-A")
        git(r, "commit", "-q", "-m", f"c{i}")
        git(r, "tag", "-a", f"v0.{i}.0", "-m", f"v0.{i}.0")
    return r


def test_all_tags_pass(tmp_path):
    r = _repo(tmp_path)
    res = run(CONTRACT_SH, "--tags", r, "test -f marker.txt")
    assert res.returncode == 0, res.stderr
    assert "PASS" in res.stdout


def test_failing_tag_named(tmp_path):
    r = _repo(tmp_path, marker_at=(2, 3))  # marker absent at v0.1.0 only
    res = run(CONTRACT_SH, "--tags", r, "test -f marker.txt")
    assert res.returncode == 1, res.stderr
    assert "v0.1.0: FAIL" in res.stdout
    assert "v0.2.0: PASS" in res.stdout


def test_range_excludes_pre_range_breakage(tmp_path):
    r = _repo(tmp_path, marker_at=(2, 3))  # c1 broken, but out of range
    res = run(CONTRACT_SH, "--range", "v0.1.0..HEAD", r, "test -f marker.txt")
    assert res.returncode == 0, res.stderr


def test_range_includes_first_commit(tmp_path):
    """birth^..tip must verify the range's FIRST commit (the birth brick itself)."""
    r = _repo(tmp_path, marker_at=(1, 3))  # c2 ("birth") is the broken one
    res = run(CONTRACT_SH, "--range", "v0.2.0^..HEAD", r, "test -f marker.txt")
    assert res.returncode == 1, res.stderr


def test_single_ref_range_includes_root(tmp_path):
    """--range <tip> sweeps the whole history including the root commit."""
    r = _repo(tmp_path, marker_at=(2, 3))  # root (c1) broken
    res = run(CONTRACT_SH, "--range", "HEAD", r, "test -f marker.txt")
    assert res.returncode == 1, res.stderr


def test_archived_tree_not_worktree(tmp_path):
    """The verify runs against the COMMITTED tree — untracked files must be invisible."""
    r = _repo(tmp_path)
    (r / "wt.txt").write_text("untracked\n")
    res = run(CONTRACT_SH, "--tags", r, "test ! -f wt.txt")
    assert res.returncode == 0, res.stderr


def test_verify_command_with_quotes(tmp_path):
    r = _repo(tmp_path)
    res = run(CONTRACT_SH, "--tags", r, 'grep -q "commit 1" c1.txt')
    assert res.returncode == 0, res.stderr


def test_tagless_repo_fails_loud(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    git(r, "init", "-q")
    (r / "f.txt").write_text("x\n")
    git(r, "add", "-A")
    git(r, "commit", "-q", "-m", "c")
    res = run(CONTRACT_SH, "--tags", r, "true")
    assert res.returncode == 2, res.stderr


def test_empty_range_fails_loud(tmp_path):
    r = _repo(tmp_path)
    res = run(CONTRACT_SH, "--range", "HEAD..HEAD", r, "true")
    assert res.returncode == 2, res.stderr


def test_bad_repo(tmp_path):
    assert run(CONTRACT_SH, "--tags", tmp_path / "nope", "true").returncode == 2


def test_flag_shaped_verify_rejected(tmp_path):
    r = _repo(tmp_path)
    assert run(CONTRACT_SH, "--tags", r, "--range").returncode == 2


def test_missing_selector_rejected(tmp_path):
    r = _repo(tmp_path)
    assert run(CONTRACT_SH, r, "true").returncode == 2


def test_usage_lists_contract():
    res = run(CONTRACT_SH)
    assert res.returncode == 2
    for tok in ("0", "1", "2", "--tags", "--range"):
        assert tok in res.stderr, res.stderr


def test_archive_failure_is_error_not_pass(tmp_path):
    """A broken object store must yield exit 2 — never PASS/skip (fail-closed)."""
    r = _repo(tmp_path)
    blob = git(r, "rev-parse", "v0.1.0:c1.txt").stdout.strip()
    obj = r / ".git" / "objects" / blob[:2] / blob[2:]
    assert obj.exists(), "expected a loose object"
    obj.unlink()
    res = run(CONTRACT_SH, "--tags", r, "true")
    assert res.returncode == 2, f"stdout={res.stdout} stderr={res.stderr}"

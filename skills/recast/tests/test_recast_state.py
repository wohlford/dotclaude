"""Contract tests for recast-state.sh — classify a target branch HEAD.

Each case asserts the exit code AND the STATE= line in stdout (never prose:
BSD/GNU git wording differs across hosts).
"""

from recast_helpers import STATE_SH, git, run


def test_empty_no_commits(tmp_path):
    repo = tmp_path / "e"
    repo.mkdir()
    git(repo, "init", "-q")
    r = run(STATE_SH, repo)
    assert r.returncode == 0
    assert "STATE=EMPTY" in r.stdout


def test_unpublished_no_upstream(make_repo):
    repo = make_repo(commits=1)
    r = run(STATE_SH, repo)
    assert r.returncode == 0
    assert "STATE=UNPUBLISHED" in r.stdout


def test_published_via_upstream(make_repo, tmp_path):
    src = make_repo(name="src", commits=1)
    dst = tmp_path / "clone"
    git(tmp_path, "clone", "-q", str(src), str(dst))
    r = run(STATE_SH, dst)
    assert r.returncode == 0
    assert "STATE=PUBLISHED" in r.stdout


def test_published_via_ls_remote_without_tracking(make_repo):
    src = make_repo(name="src", commits=1)
    work = make_repo(name="work", commits=1)
    git(work, "remote", "add", "origin", str(src))
    git(work, "fetch", "-q", "origin")
    # No upstream is set; src's `main` is still discoverable via ls-remote.
    r = run(STATE_SH, work)
    assert r.returncode == 0
    assert "STATE=PUBLISHED" in r.stdout


def test_failsafe_on_unreachable_remote(make_repo):
    work = make_repo(name="work", commits=1)
    git(work, "remote", "add", "origin", "/nonexistent/none.git")
    r = run(STATE_SH, work)
    assert r.returncode == 0
    assert "STATE=PUBLISHED" in r.stdout


def test_detached_head(make_repo):
    repo = make_repo(commits=2)
    git(repo, "checkout", "-q", "--detach", "HEAD")
    r = run(STATE_SH, repo)
    assert r.returncode == 3
    assert "STATE=DETACHED" in r.stdout


def test_published_via_tag_only(make_repo, tmp_path):
    """A tag-only publish (branch absent on remote) is PUBLISHED."""
    bare = tmp_path / "bare.git"
    git(tmp_path, "init", "-q", "--bare", str(bare))
    work = make_repo(name="work", commits=2)
    git(work, "tag", "-a", "v1", "-m", "v1")  # annotated tag on HEAD
    git(work, "remote", "add", "origin", str(bare))
    git(work, "push", "-q", "origin", "v1")  # push the TAG only, not the branch
    r = run(STATE_SH, work)
    assert r.returncode == 0
    assert "STATE=PUBLISHED" in r.stdout


def test_published_via_differently_named_remote_branch(make_repo, tmp_path):
    """HEAD's history pushed under a different remote branch name is PUBLISHED."""
    bare = tmp_path / "bare2.git"
    git(tmp_path, "init", "-q", "--bare", str(bare))
    work = make_repo(name="work2", commits=2)
    git(work, "remote", "add", "origin", str(bare))
    git(work, "push", "-q", "origin", "HEAD:refs/heads/other")
    r = run(STATE_SH, work)
    assert r.returncode == 0
    assert "STATE=PUBLISHED" in r.stdout


def test_published_then_amended_under_other_remote_name(make_repo, tmp_path):
    """Pushed under a different remote name, then amended locally: the remote sha is no
    longer an ancestor of HEAD but is still OUR published history — PUBLISHED (the
    catastrophic direction to get wrong; regression for the diverged-rename hole)."""
    bare = tmp_path / "bare-amend.git"
    git(tmp_path, "init", "-q", "--bare", str(bare))
    work = make_repo(name="work-amend", commits=2)
    git(work, "remote", "add", "origin", str(bare))
    git(work, "push", "-q", "origin", "HEAD:refs/heads/master")
    git(work, "commit", "-q", "--amend", "-m", "two-amended")
    r = run(STATE_SH, work)
    assert r.returncode == 0
    assert "STATE=PUBLISHED" in r.stdout


def test_foreign_remote_tag_stays_unpublished(make_repo, tmp_path):
    """A remote tag on a commit NOT in our local history does not mark us PUBLISHED."""
    bare = tmp_path / "bare3.git"
    git(tmp_path, "init", "-q", "--bare", str(bare))
    other = make_repo(name="other", commits=1)
    # make the tagged commit GENUINELY distinct — two bare make_repo roots share an identical
    # tree/message/identity/second-timestamp and thus the same SHA, which would read as in-history.
    (other / "foreign.txt").write_text("foreign only\n")
    git(other, "add", "-A")
    git(other, "commit", "-q", "-m", "foreign only")
    git(other, "tag", "-a", "foreign", "-m", "f")  # tag the distinct commit
    git(other, "remote", "add", "origin", str(bare))
    git(other, "push", "-q", "origin", "foreign")
    work = make_repo(name="work3", commits=2)  # separate repo, not fetched
    git(work, "remote", "add", "origin", str(bare))
    r = run(STATE_SH, work)
    assert r.returncode == 0
    assert "STATE=UNPUBLISHED" in r.stdout

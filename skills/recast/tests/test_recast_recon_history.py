"""Contract tests for recast-recon-history.sh — metadata sweep, ref:kind only, fail-closed."""

import os
import re

from recast_helpers import HISTORY_SH, git, run

REFKIND = re.compile(r"\S+:(message|identity|tagger)")


def _repo(tmp_path, subject="init", body="", an="Jane Dev", ae="jane@example.invalid"):
    r = tmp_path / "repo"
    r.mkdir()
    git(r, "init", "-q")
    (r / "f.txt").write_text("hello\n")
    git(r, "add", "f.txt")
    msg = subject + ("\n\n" + body if body else "")
    git(r, "-c", f"user.name={an}", "-c", f"user.email={ae}", "commit", "-q", "-m", msg)
    return r


def test_clean_repo(tmp_path):
    assert run(HISTORY_SH, _repo(tmp_path)).returncode == 0


def test_usage_lists_exit_codes():
    res = run(HISTORY_SH)
    assert res.returncode == 2
    for tok in ("0", "1", "2", "--traces-only"):
        assert tok in res.stderr, res.stderr


def test_flag_shaped_positional_rejected(tmp_path):
    assert run(HISTORY_SH, _repo(tmp_path), "--redact").returncode == 2


def test_bad_repo_errors(tmp_path):
    assert run(HISTORY_SH, tmp_path / "not-a-repo").returncode == 2


def test_bad_range_errors(tmp_path):
    assert run(HISTORY_SH, _repo(tmp_path), "nonexistent-ref-xyz..HEAD").returncode == 2


def test_missing_pattern_file_errors(tmp_path):
    assert (
        run(HISTORY_SH, _repo(tmp_path), "HEAD", tmp_path / "nope.txt").returncode == 2
    )


def test_empty_repo_clean(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    git(r, "init", "-q")  # unborn HEAD, no commits
    assert run(HISTORY_SH, r).returncode == 0


# --- message + tag sweeps (Task 4) ---


def test_coauthor_trailer_flagged_both_modes(tmp_path):
    r = _repo(tmp_path, subject="fix: thing", body="Co-Authored-By: Someone <s@x>")
    for extra in ([], ["--traces-only"]):
        res = run(HISTORY_SH, *extra, r)
        assert res.returncode == 1, res.stderr
        assert REFKIND.search(res.stdout), res.stdout
        assert "Co-Authored-By" not in res.stdout


def test_robot_emoji_in_message_flagged(tmp_path):
    assert (
        run(HISTORY_SH, _repo(tmp_path, subject="feat: x \U0001f916")).returncode == 1
    )


def test_name_in_subject_mode_sensitive(tmp_path):
    r = _repo(tmp_path, subject="docs: explain Claude Code setup")
    assert run(HISTORY_SH, r).returncode == 1  # default: names on
    assert run(HISTORY_SH, "--traces-only", r).returncode == 0  # --keep-code: names off


def test_tag_annotation_trace_flagged(tmp_path):
    r = _repo(tmp_path)
    git(r, "tag", "-a", "v1.0.0", "-m", "release\n\nGenerated with a tool")
    res = run(HISTORY_SH, r)
    assert res.returncode == 1, res.stderr
    assert "v1.0.0" in res.stdout


def test_lightweight_tag_ignored(tmp_path):
    r = _repo(tmp_path)
    git(r, "tag", "v1.0.0")  # lightweight → no annotation
    assert run(HISTORY_SH, r).returncode == 0


def test_redact_pattern_hits_message(tmp_path):
    r = _repo(tmp_path, subject="chore: bump", body="internal-codename FALCON9")
    pf = tmp_path / "p.txt"
    pf.write_text("FALCON[0-9]\n")
    assert run(HISTORY_SH, r, "HEAD", pf).returncode == 1


# --- identity sweeps (Task 5) ---


def test_ai_author_identity_flagged_even_traces_only(tmp_path):
    r = _repo(tmp_path, subject="feat: x", an="Claude", ae="noreply@anthropic.com")
    assert run(HISTORY_SH, r).returncode == 1  # default
    assert (
        run(HISTORY_SH, "--traces-only", r).returncode == 1
    )  # identity comprehensive-always


def test_ai_committer_identity_flagged(tmp_path):
    """Clean AUTHOR, AI COMMITTER → caught (proves %cn/%ce swept, not just %an/%ae)."""
    r = tmp_path / "repo"
    r.mkdir()
    git(r, "init", "-q")
    (r / "f.txt").write_text("hello\n")
    git(r, "add", "f.txt")
    env = {
        **os.environ,
        "GIT_COMMITTER_NAME": "Claude",
        "GIT_COMMITTER_EMAIL": "b@anthropic.com",
    }
    git(
        r, "commit", "-q", "-m", "clean subject", env=env
    )  # author from _GIT_CFG (clean)
    assert run(HISTORY_SH, r).returncode == 1


def test_ai_tagger_identity_flagged(tmp_path):
    r = _repo(tmp_path)
    git(
        r,
        "-c",
        "user.name=Claude",
        "-c",
        "user.email=bot@anthropic.com",
        "tag",
        "-a",
        "v1.0.0",
        "-m",
        "clean message",
    )
    res = run(HISTORY_SH, "--traces-only", r)
    assert res.returncode == 1, res.stderr
    assert "tagger" in res.stdout


def test_human_identity_clean(tmp_path):
    r = _repo(tmp_path, subject="feat: x", an="Jason Wohlford", ae="jason@wohlford.org")
    assert run(HISTORY_SH, r).returncode == 0


def test_redact_pattern_hits_identity(tmp_path):
    """Redact patterns apply to identity fields too (not only messages)."""
    r = _repo(tmp_path, subject="clean", an="Jane Dev", ae="jane@secretcorp.invalid")
    pf = tmp_path / "p.txt"
    pf.write_text("secretcorp\n")
    assert run(HISTORY_SH, r, "HEAD", pf).returncode == 1


def test_single_ref_range_scopes_one_commit(tmp_path):
    r = _repo(tmp_path, subject="clean start")  # HEAD~1
    (r / "g.txt").write_text("x\n")
    git(r, "add", "g.txt")
    git(
        r,
        "-c",
        "user.name=Claude",
        "-c",
        "user.email=b@anthropic.com",
        "commit",
        "-q",
        "-m",
        "second",
    )  # HEAD (dirty identity)
    assert run(HISTORY_SH, r, "HEAD~1").returncode == 0  # only the clean commit
    assert run(HISTORY_SH, r, "HEAD").returncode == 1  # only the dirty commit


def test_lightweight_tag_name_flagged(tmp_path):
    """A marker in a LIGHTWEIGHT tag's name is a tell — previously skipped entirely."""
    r = _repo(tmp_path)
    git(r, "tag", "claude-checkpoint")  # lightweight
    res = run(HISTORY_SH, r)
    assert res.returncode == 1, res.stderr
    assert ":refname" in res.stdout


def test_annotated_tag_name_flagged(tmp_path):
    r = _repo(tmp_path)
    git(r, "tag", "-a", "anthropic-rel", "-m", "clean body")
    res = run(HISTORY_SH, r)
    assert res.returncode == 1, res.stderr
    assert "anthropic-rel:refname" in res.stdout


def test_branch_name_flagged(tmp_path):
    r = _repo(tmp_path)
    git(r, "branch", "gemini-wip")
    res = run(HISTORY_SH, r)
    assert res.returncode == 1, res.stderr
    assert "gemini-wip:refname" in res.stdout


def test_clean_ref_names_stay_clean(tmp_path):
    r = _repo(tmp_path)
    git(r, "tag", "v1.0.0")
    git(r, "tag", "-a", "release-1", "-m", "ship it")
    git(r, "branch", "feature-x")
    res = run(HISTORY_SH, r)
    assert res.returncode == 0, res.stdout + res.stderr


def test_out_of_range_tag_name_not_flagged(tmp_path):
    """A marker-named tag whose target is OUTSIDE the swept range must not fire."""
    r = _repo(tmp_path, subject="one")
    git(r, "tag", "claude-old")  # on the first commit
    (r / "f.txt").write_text("second\n")
    git(r, "add", "f.txt")
    git(r, "commit", "-q", "-m", "two")
    head = git(r, "rev-parse", "HEAD").stdout.strip()
    res = run(HISTORY_SH, r, f"{head}..{head}")  # empty range: nothing swept
    assert res.returncode == 0, res.stdout + res.stderr

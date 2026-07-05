"""Contract tests for recast-verify.sh — STRUCTURE audit cases.

Asserts exit code + verdict only (BSD/GNU prose differs); see Task 4 in
plans/2026-06-15-recast-skill.md.
"""

import os
import subprocess

from recast_helpers import VERIFY_SH, git, run


def _seed(repo, files):
    """Write `files` (name -> content), stage, commit. Returns HEAD sha."""
    for name, content in files.items():
        p = repo / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "x")
    return git(repo, "rev-parse", "HEAD").stdout.strip()


def test_identical_trees(make_repo):
    src = make_repo(name="src", commits=0)
    tgt = make_repo(name="tgt", commits=0)
    files = {"a.txt": "a\n", "dir/b.txt": "b\n"}
    _seed(src, files)
    _seed(tgt, dict(files))

    cp = run(VERIFY_SH, tgt, src, "HEAD")
    assert cp.returncode == 0


def test_extra_file_in_target(make_repo, tmp_path):
    src = make_repo(name="src", commits=0)
    tgt = make_repo(name="tgt", commits=0)
    _seed(src, {"a.txt": "a\n"})
    _seed(tgt, {"a.txt": "a\n", "extra.local": "x\n"})

    cp = run(VERIFY_SH, tgt, src, "HEAD")
    assert cp.returncode == 1

    dev = tmp_path / "dev1.txt"
    dev.write_text("# exempt the extra\n*.local\n")
    cp = run(VERIFY_SH, tgt, src, "HEAD", dev)
    assert cp.returncode == 0


def test_missing_source_file(make_repo, tmp_path):
    src = make_repo(name="src", commits=0)
    tgt = make_repo(name="tgt", commits=0)
    _seed(src, {"a.txt": "a\n", "docs/gone.md": "g\n"})
    _seed(tgt, {"a.txt": "a\n"})

    cp = run(VERIFY_SH, tgt, src, "HEAD")
    assert cp.returncode == 1

    dev = tmp_path / "dev2.txt"
    dev.write_text("docs/*\n")
    cp = run(VERIFY_SH, tgt, src, "HEAD", dev)
    assert cp.returncode == 0


def test_comm_collation_matches_c_sort(make_repo, tmp_path):
    """Regression: comm must collate like the LC_ALL=C sort that feeds it.

    The inputs are byte-sorted (LC_ALL=C), but a bare `comm` under a UTF-8
    locale collates case-insensitively. Merging two *differing* streams then
    trips comm's order check ("not in sorted order") and can miss or duplicate
    lines — a false verdict. The mix below (uppercase top-level files + lowercase
    subdirs, plus a source-only `skills/recast/*` that creates the interleave)
    reproduces it; the fix runs comm under LC_ALL=C so its collation matches.

    Asserts on the "sorted order" diagnostic — the defect's signature, stable
    across comm implementations — not on the (here coincidentally-correct) verdict.
    """
    common = {
        "README.md": "r\n",
        "STYLE.md": "s\n",
        "TESTING.md": "t\n",
        "agents/README.md": "ar\n",
        "agents/style-reviewer.md": "sr\n",
        "scripts/guard-secrets.sh": "g\n",
        "skills/vet/SKILL.md": "v\n",
    }
    src_only = {
        "skills/recast/SKILL.md": "x\n",
        "skills/recast/recast-verify.sh": "y\n",
    }
    src = make_repo(name="src", commits=0)
    tgt = make_repo(name="tgt", commits=0)
    _seed(src, {**common, **src_only})
    _seed(tgt, dict(common))

    dev = tmp_path / "dev.txt"
    dev.write_text("skills/recast/*\n")

    env = dict(os.environ, LC_ALL="en_US.UTF-8")
    cmd = ["bash", str(VERIFY_SH), str(tgt), str(src), "HEAD", str(dev)]
    cp = subprocess.run(cmd, capture_output=True, text=True, timeout=30, env=env)
    assert "sorted order" not in cp.stderr, cp.stderr
    assert cp.returncode == 0, (cp.stdout, cp.stderr)

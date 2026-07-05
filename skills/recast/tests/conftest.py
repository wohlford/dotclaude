"""Fixtures for /recast helper tests — throwaway git-repo factories.

Shared `run`/`git`/script-path constants live in `recast_helpers` (uniquely named so a combined
`pytest` run across skills does not clash on the `conftest` module name). Import those with
`from recast_helpers import …`; request fixtures below by name.
"""

import pytest
from recast_helpers import git


@pytest.fixture
def make_repo(tmp_path):
    """Factory → a git work tree on branch `main`. make_repo(name='r', commits=1)."""

    def _make(name="r", commits=1):
        repo = tmp_path / name
        repo.mkdir()
        git(repo, "init", "-q")
        git(repo, "symbolic-ref", "HEAD", "refs/heads/main")
        for i in range(commits):
            (repo / f"f{i}.txt").write_text(f"line {i}\n")
            git(repo, "add", "-A")
            git(repo, "commit", "-q", "-m", f"commit {i}")
        return repo

    return _make

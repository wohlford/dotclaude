"""The write path must REFUSE when the scope carries a different copy of sync-docs itself.

Measured 2026-07-27, in both directions, from one root cause — a globally-installed tool operating
on a repo that contains a *different* copy of that same tool:

  * READ direction: `~/.claude/skills/audit/audit.sh` graded a branch that MODIFIES sync-docs using
    production's stale checker — `FAIL rc=1`, a true verdict about the wrong question, while the
    branch's own copy said `PASS`.
  * WRITE direction, which is why this file exists: running production's `/sync-docs` against this
    repo would have SILENTLY rewritten `CLAUDE.md`'s filtered region, because the running copy did
    not implement the `filter=` directive the region declares.

The two are not equally coverable. The read direction produces a verdict a human can question, and
attention has in fact caught it every time it arose. The write direction produces **no symptom at
all** — a correct-looking table, regenerated wrongly — and a prompt cannot catch what emits nothing.
That asymmetry is the whole argument for a tool-level guard here and an advisory line there.

Note what the guard must compare: sync-docs is a PACKAGE (`sync_docs.py`, `handlers.py`,
`extractors.py`, `formatters.py`, `markers.py`). `filter=` is implemented in `handlers.py` and
appears nowhere in the entry point, so a guard hashing only the script it was launched as would
have declared the exact historical hazard "identical".
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
SCRIPT = SKILL_DIR / "sync_docs.py"

MARKED_DOC = """# Title

<!-- sync:skills cols=Command:key,Purpose:auto -->
<!-- /sync:skills -->
"""


def run_sync(scope: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--scope", str(scope), "sync", *args],
        capture_output=True,
        text=True,
        cwd=str(scope),
    )


def make_repo(tmp_path: Path, *, embed_copy: bool, mutate: bool) -> Path:
    """A scope that optionally carries its own sync-docs copy, optionally a DIFFERENT one."""
    repo = tmp_path / "repo"
    (repo / "skills").mkdir(parents=True)
    (repo / "README.md").write_text(MARKED_DOC)
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
    if embed_copy:
        dest = repo / "skills" / "sync-docs"
        shutil.copytree(
            SKILL_DIR,
            dest,
            ignore=shutil.ignore_patterns("__pycache__", "tests", "fixtures"),
        )
        if mutate:
            # A real difference in a NON-entry-point module — the shape the historical failure
            # took, and the one a script-only hash would miss.
            handlers = dest / "handlers.py"
            handlers.write_text(handlers.read_text() + "\n# divergent copy\n")
    return repo


def test_it_refuses_when_the_scope_carries_a_DIFFERENT_copy(tmp_path):
    """The load-bearing row: writing under a stale runner is the silent-corruption direction."""
    repo = make_repo(tmp_path, embed_copy=True, mutate=True)
    before = (repo / "README.md").read_text()
    result = run_sync(repo)
    assert result.returncode == 2, result.stdout + result.stderr
    combined = result.stdout + result.stderr
    assert "sync-docs" in combined and str(repo) in combined, combined
    assert (repo / "README.md").read_text() == before, (
        "it must not have written anything"
    )


def test_the_difference_is_detected_in_a_NON_entry_point_module(tmp_path):
    """`filter=` lives in handlers.py and nowhere in sync_docs.py.

    A guard that hashed only the script it was launched as would call the exact historical
    hazard identical — so this asserts the comparison covers the package.
    """
    repo = make_repo(tmp_path, embed_copy=True, mutate=True)
    assert (
        repo / "skills" / "sync-docs" / "sync_docs.py"
    ).read_text() == SCRIPT.read_text(), (
        "the entry point is byte-identical; only handlers.py differs"
    )
    assert run_sync(repo).returncode == 2


def test_it_proceeds_when_the_scope_carries_an_IDENTICAL_copy(tmp_path):
    """Running a repo's own copy is the normal case and must not be blocked."""
    repo = make_repo(tmp_path, embed_copy=True, mutate=False)
    result = run_sync(repo)
    assert result.returncode == 0, result.stdout + result.stderr


def test_it_proceeds_when_the_scope_carries_no_copy_at_all(tmp_path):
    """An ordinary consumer repo has no sync-docs of its own; the guard must stay silent."""
    repo = make_repo(tmp_path, embed_copy=False, mutate=False)
    result = run_sync(repo)
    assert result.returncode == 0, result.stdout + result.stderr


def test_check_mode_is_also_guarded(tmp_path):
    """`--check` reports drift, and a stale runner reports drift that is not there.

    It writes nothing, so this is the read direction — but unlike `audit.sh` the verdict is
    consumed by a hook, where no human is looking at it.
    """
    repo = make_repo(tmp_path, embed_copy=True, mutate=True)
    assert run_sync(repo, "--check").returncode == 2

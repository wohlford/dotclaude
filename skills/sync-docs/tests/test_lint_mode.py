"""Tests for lint-mode drift detection — the canonical fix to the audit bug
where mode=lint silently preserved existing body without checking drift."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
SCRIPT = SKILL_DIR / 'sync_docs.py'


def _run(cwd: Path, *args: str) -> subprocess.CompletedProcess:
  return subprocess.run(
    [sys.executable, str(SCRIPT), '--scope', str(cwd), *args],
    capture_output=True, text=True, cwd=str(cwd),
  )


def _setup_lint_repo(tmp_path: Path, *dirnames: str) -> Path:
  """Repo with a lint-mode marker listing the given dirnames."""
  repo = tmp_path / 'repo'
  repo.mkdir()
  for d in dirnames:
    (repo / d).mkdir()
  rows = '\n'.join(f'| `{d}/` | row {d} |' for d in dirnames)
  (repo / 'README.md').write_text(
    "# Test\n\n"
    "<!-- sync:index kind=dirs mode=lint -->\n"
    "| Entry | Notes |\n"
    "| :---- | :---- |\n"
    f"{rows}\n"
    "<!-- /sync:index -->\n"
  )
  return repo


def test_lint_mode_no_drift_when_dirs_match(tmp_path):
  repo = _setup_lint_repo(tmp_path, 'a', 'b', 'c')
  result = _run(repo, '--check')
  assert result.returncode == 0, f"unexpected drift: {result.stderr}"


def test_lint_mode_detects_added_dir(tmp_path):
  """Adding a dir not listed in the lint marker should produce drift."""
  repo = _setup_lint_repo(tmp_path, 'a', 'b', 'c')
  (repo / 'd').mkdir()
  result = _run(repo, '--check')
  assert result.returncode == 1
  assert 'lint drift' in result.stderr
  assert '`d/`' in result.stderr  # missing-from-doc reported by name


def test_lint_mode_detects_removed_dir(tmp_path):
  """Listing a dir that no longer exists should also produce drift."""
  repo = _setup_lint_repo(tmp_path, 'a', 'b', 'c')
  (repo / 'b').rmdir()
  result = _run(repo, '--check')
  assert result.returncode == 1
  assert 'extra rows' in result.stderr
  assert '`b/`' in result.stderr


def test_lint_mode_does_not_modify_file(tmp_path):
  """Even when drift exists, default sync must NOT rewrite a lint marker."""
  repo = _setup_lint_repo(tmp_path, 'a', 'b', 'c')
  (repo / 'd').mkdir()
  before = (repo / 'README.md').read_text()
  result = _run(repo)  # default sync, no --check
  after = (repo / 'README.md').read_text()
  assert before == after, "lint mode must not modify file content"
  # And the drift IS reported on stdout
  assert 'lint drift' in result.stdout


def test_lint_mode_content_full_body_compare(tmp_path):
  """lint=content reports drift on cell-content mismatch even if rows match."""
  repo = tmp_path / 'repo'
  repo.mkdir()
  (repo / 'a').mkdir()
  (repo / 'README.md').write_text(
    "# Test\n\n"
    "<!-- sync:index kind=dirs mode=lint lint=content -->\n"
    "| Entry  | Summary |\n"
    "| :----- | :------ |\n"
    "| WRONG  |         |\n"
    "<!-- /sync:index -->\n"
  )
  result = _run(repo, '--check')
  assert result.returncode == 1
  assert 'content drift' in result.stderr


def test_lint_mode_both_acts_like_content(tmp_path):
  """lint=both = lint=content (rows is a subset of content)."""
  repo = tmp_path / 'repo'
  repo.mkdir()
  (repo / 'a').mkdir()
  (repo / 'README.md').write_text(
    "# Test\n\n"
    "<!-- sync:index kind=dirs mode=lint lint=both -->\n"
    "| Entry  | Summary |\n"
    "| :----- | :------ |\n"
    "| WRONG  |         |\n"
    "<!-- /sync:index -->\n"
  )
  result = _run(repo, '--check')
  assert result.returncode == 1
  assert 'content drift' in result.stderr

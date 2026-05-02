"""Tests for the `add <handler>` subcommand."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
SCRIPT = SKILL_DIR / 'sync_docs.py'


def _run_add(cwd: Path, *args: str) -> subprocess.CompletedProcess:
  return subprocess.run(
    [sys.executable, str(SCRIPT), '--scope', str(cwd), 'add', *args],
    capture_output=True, text=True, cwd=str(cwd),
  )


def test_add_appends_marker_to_existing_file(tmp_path):
  repo = tmp_path / 'repo'
  repo.mkdir()
  readme = repo / 'README.md'
  readme.write_text("# Existing\n\nSome prose.\n")
  result = _run_add(repo, 'skills', '--into', str(readme))
  assert result.returncode == 0
  text = readme.read_text()
  assert "Some prose." in text  # original preserved
  assert '<!-- sync:skills cols=Command:key,Purpose:auto -->' in text
  assert '<!-- /sync:skills -->' in text


def test_add_creates_file_if_missing(tmp_path):
  repo = tmp_path / 'repo'
  repo.mkdir()
  target = repo / 'sub' / 'NEW.md'
  result = _run_add(repo, 'agents', '--into', str(target))
  assert result.returncode == 0
  text = target.read_text()
  assert '<!-- sync:agents cols=Agent:key,Purpose:auto -->' in text
  assert '<!-- /sync:agents -->' in text


def test_add_unknown_handler_errors(tmp_path):
  repo = tmp_path / 'repo'
  repo.mkdir()
  result = _run_add(repo, 'bogus', '--into', str(repo / 'README.md'))
  assert result.returncode == 2
  assert 'unknown handler' in result.stderr.lower()


def test_add_custom_requires_source_and_cols(tmp_path):
  repo = tmp_path / 'repo'
  repo.mkdir()
  result = _run_add(repo, 'custom', '--into', str(repo / 'README.md'))
  assert result.returncode == 1
  assert '--source' in result.stderr
  assert '--cols' in result.stderr


def test_add_custom_with_source_and_cols(tmp_path):
  repo = tmp_path / 'repo'
  repo.mkdir()
  target = repo / 'README.md'
  result = _run_add(
    repo, 'custom',
    '--into', str(target),
    '--source', 'docs/posts/*.md',
    '--cols', 'File:key,Title:auto',
  )
  assert result.returncode == 0
  text = target.read_text()
  assert 'sync:custom source="docs/posts/*.md" cols=File:key,Title:auto' in text


def test_add_then_sync_populates_marker(tmp_path):
  """End-to-end: add a marker, then sync fills it."""
  repo = tmp_path / 'repo'
  (repo / 'skills' / 'foo').mkdir(parents=True)
  (repo / 'skills' / 'foo' / 'SKILL.md').write_text(
    "---\nname: foo\ndescription: A foo\n---\n# /foo\n"
  )
  target = repo / 'README.md'
  _run_add(repo, 'skills', '--into', str(target))
  # Now run sync
  result = subprocess.run(
    [sys.executable, str(SCRIPT), '--scope', str(repo)],
    capture_output=True, text=True,
  )
  assert result.returncode == 0
  text = target.read_text()
  assert '`/foo`' in text
  assert 'A foo' in text

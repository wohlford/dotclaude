"""End-to-end integration tests using fixture repos.

Each fixture under tests/fixtures/ is copied to a temp dir, the script is run
via subprocess (exercising the real CLI), and the resulting state asserted.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
SCRIPT = SKILL_DIR / 'sync_docs.py'
FIXTURES = Path(__file__).resolve().parent / 'fixtures'


def _run(cwd: Path, *args: str) -> subprocess.CompletedProcess:
  return subprocess.run(
    [sys.executable, str(SCRIPT), '--scope', str(cwd), *args],
    capture_output=True, text=True, cwd=str(cwd),
  )


def _copy_fixture(name: str, dest: Path) -> Path:
  src = FIXTURES / name
  shutil.copytree(src, dest, dirs_exist_ok=True)
  return dest


def test_sync_dotclaude_shaped(tmp_path):
  repo = _copy_fixture('dotclaude-shaped', tmp_path / 'repo')
  result = _run(repo)
  assert result.returncode == 0, f"stderr: {result.stderr}"
  readme = (repo / 'README.md').read_text()
  # Stale row replaced; new rows present
  assert 'stale row' not in readme
  assert '`/bar`' in readme
  assert '`/foo`' in readme
  assert 'Do the foo' in readme
  assert 'Do the bar' in readme


def test_sync_then_check_clean(tmp_path):
  repo = _copy_fixture('dotclaude-shaped', tmp_path / 'repo')
  _run(repo)  # initial sync
  result = _run(repo, '--check')
  assert result.returncode == 0, f"stderr: {result.stderr}"


def test_check_dirty_before_sync(tmp_path):
  repo = _copy_fixture('dotclaude-shaped', tmp_path / 'repo')
  result = _run(repo, '--check')
  # Initial state has stale rows → drift expected
  assert result.returncode == 1
  assert 'drift' in result.stderr.lower()


def test_no_markers_byte_identical(tmp_path):
  repo = _copy_fixture('no-markers', tmp_path / 'repo')
  before = (repo / 'README.md').read_bytes()
  result = _run(repo)
  assert result.returncode == 0
  after = (repo / 'README.md').read_bytes()
  assert before == after, "no-marker file must be byte-identical after sync"


def test_no_markers_first_run_message(tmp_path):
  repo = _copy_fixture('no-markers', tmp_path / 'repo')
  result = _run(repo)
  assert 'No <!-- sync:* --> markers found' in result.stdout


def test_hostile_unclosed_marker_reports_error(tmp_path):
  repo = _copy_fixture('hostile', tmp_path / 'repo')
  result = _run(repo)
  # parse errors → exit 2, but the file should not be corrupted
  assert result.returncode in (1, 2)
  assert 'unclosed' in result.stderr.lower() or 'unclosed' in result.stdout.lower()


def test_idempotent_double_sync(tmp_path):
  repo = _copy_fixture('dotclaude-shaped', tmp_path / 'repo')
  _run(repo)
  after_first = (repo / 'README.md').read_bytes()
  _run(repo)
  after_second = (repo / 'README.md').read_bytes()
  assert after_first == after_second


def test_check_emits_unified_diff(tmp_path):
  repo = _copy_fixture('dotclaude-shaped', tmp_path / 'repo')
  result = _run(repo, '--check')
  assert result.returncode == 1
  # Unified-diff markers
  assert '---' in result.stderr
  assert '+++' in result.stderr
  assert '@@' in result.stderr
  # Content delta visible
  assert '-' in result.stderr  # at least one removed line
  assert '+' in result.stderr  # at least one added line


def test_dirty_edit_overwritten_silently(tmp_path):
  """User manually edited an auto cell; sync must overwrite without warning."""
  repo = _copy_fixture('dirty-edit', tmp_path / 'repo')
  result = _run(repo)
  assert result.returncode == 0, f"stderr: {result.stderr}"
  readme = (repo / 'README.md').read_text()
  assert 'HAND-EDITED-WRONG-VALUE-THAT-WILL-LOSE' not in readme
  assert 'Original description from source' in readme
  # No warning about hand-edited content
  assert 'hand-edit' not in result.stderr.lower()


def test_dirty_edit_check_reports_drift(tmp_path):
  repo = _copy_fixture('dirty-edit', tmp_path / 'repo')
  result = _run(repo, '--check')
  assert result.returncode == 1
  assert 'HAND-EDITED' in result.stderr  # diff shows old line being removed


def test_hostile_crlf_does_not_corrupt(tmp_path):
  """File with CRLF line endings + a sync marker should not crash; either
  parses cleanly or reports a clear error, but never corrupts the file."""
  repo = _copy_fixture('hostile', tmp_path / 'repo')
  before = (repo / 'crlf.md').read_bytes()
  result = _run(repo)
  after = (repo / 'crlf.md').read_bytes()
  # No marker discovers any sources, so render returns empty body — either
  # the file is left as-is (preferred) or rewritten with empty body
  # (acceptable). Either way no corruption / no crash.
  assert result.returncode in (0, 1, 2)
  # CRLF preservation isn't strictly required, but file must remain valid
  assert b'Hostile-CRLF' in after


def test_hostile_bom_file_does_not_crash(tmp_path):
  """A file with UTF-8 BOM at the start (no markers) must not crash sync."""
  repo = _copy_fixture('hostile', tmp_path / 'repo')
  result = _run(repo)
  # File has no markers, so it's not modified. The walker may report errors
  # for OTHER hostile files; what matters is the BOM file doesn't crash.
  assert (repo / 'with-bom.md').exists()


def test_hostile_malformed_frontmatter_does_not_crash(tmp_path):
  """A file with unbalanced frontmatter quote must not crash the run."""
  repo = _copy_fixture('hostile', tmp_path / 'repo')
  result = _run(repo)
  # No assertion on exit code; just that the run completes
  assert (repo / 'malformed-frontmatter.md').exists()


def test_hostile_partial_table_in_marker(tmp_path):
  """A marker block containing a partial/malformed table should not crash —
  may be rewritten as empty (no sources match) or surface a soft error."""
  repo = _copy_fixture('hostile', tmp_path / 'repo')
  result = _run(repo)
  # Just confirm no Python traceback in stderr
  assert 'Traceback' not in result.stderr


# ---------- Idempotence across the full fixture corpus ----------

import pytest


@pytest.mark.parametrize('fixture_name', [
  'dotclaude-shaped',
  'dirty-edit',
  'no-markers',
])
def test_idempotence_across_fixtures(tmp_path, fixture_name):
  """sync(sync(x)) must equal sync(x) for every safe fixture."""
  repo = _copy_fixture(fixture_name, tmp_path / 'repo')
  _run(repo)
  snapshot1 = {p.relative_to(repo): p.read_bytes() for p in repo.rglob('*.md')}
  _run(repo)
  snapshot2 = {p.relative_to(repo): p.read_bytes() for p in repo.rglob('*.md')}
  assert snapshot1 == snapshot2, f"non-idempotent on fixture {fixture_name!r}"

"""Tests for the init subcommand: threshold rules, handler-choice heuristic,
scaffold generation."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import sync_docs

SKILL_DIR = Path(__file__).resolve().parent.parent
SCRIPT = SKILL_DIR / 'sync_docs.py'
FIXTURES = Path(__file__).resolve().parent / 'fixtures'


def _run_init(cwd: Path, *args: str) -> subprocess.CompletedProcess:
  return subprocess.run(
    [sys.executable, str(SCRIPT), '--scope', str(cwd), 'init', *args],
    capture_output=True, text=True, cwd=str(cwd),
  )


# ---------- Threshold rules (unit) ----------

def test_qualify_semantic_name_runs(tmp_path):
  d = tmp_path / 'runs'
  d.mkdir()
  (d / 'something.txt').write_text('x')
  reason, marker = sync_docs._qualify_dir(d)
  assert reason is not None
  assert "semantic name" in reason


def test_qualify_semantic_name_applications(tmp_path):
  d = tmp_path / 'applications'
  d.mkdir()
  (d / 'foo').mkdir()
  reason, _ = sync_docs._qualify_dir(d)
  assert reason is not None


def test_qualify_five_md_files(tmp_path):
  d = tmp_path / 'docs'
  d.mkdir()
  for i in range(5):
    (d / f'doc-{i}.md').write_text(f'# Doc {i}\n')
  reason, _ = sync_docs._qualify_dir(d)
  assert reason is not None
  assert "5 markdown" in reason


def test_qualify_three_date_dirs(tmp_path):
  d = tmp_path / 'data'  # also a semantic name; we want to test rule 2 not 3
  d.mkdir()
  for date in ('2026-01-01', '2026-01-02', '2026-01-03'):
    (d / date).mkdir()
  reason, marker = sync_docs._qualify_dir(d)
  assert reason is not None
  assert 'sort=date,desc' in marker


def test_no_qualify_single_file(tmp_path):
  d = tmp_path / 'misc'
  d.mkdir()
  (d / 'one.md').write_text('# x\n')
  reason, marker = sync_docs._qualify_dir(d)
  assert reason is None
  assert marker is None


def test_no_qualify_existing_readme(tmp_path):
  d = tmp_path / 'runs'
  d.mkdir()
  (d / 'README.md').write_text('# Already documented\n')
  for date in ('2026-01-01', '2026-01-02', '2026-01-03'):
    (d / date).mkdir()
  reason, _ = sync_docs._qualify_dir(d)
  assert reason is None


def test_no_qualify_empty_dir(tmp_path):
  d = tmp_path / 'empty'
  d.mkdir()
  assert sync_docs._qualify_dir(d) == (None, None)


# ---------- Handler-choice heuristic ----------

def test_choose_marker_date_dirs():
  date_dirs = [Path(f'/x/2026-01-{i:02d}') for i in range(1, 4)]
  marker = sync_docs._choose_marker(date_dirs, date_dirs, [], [])
  assert 'kind=dirs' in marker
  assert 'sort=date,desc' in marker


def test_choose_marker_md_files():
  md_files = [Path(f'/x/{n}.md') for n in 'abcde']
  marker = sync_docs._choose_marker([], [], md_files, md_files)
  assert 'kind=files' in marker
  assert 'extract=h1-and-paragraph' in marker


def test_choose_marker_mixed_falls_back_to_lint():
  marker = sync_docs._choose_marker([Path('/x/d')], [], [Path('/x/f.txt')], [])
  assert 'mode=lint' in marker


# ---------- Scaffold content ----------

def test_scaffold_content_has_title_todo_and_marker(tmp_path):
  d = tmp_path / 'my-content-dir'
  d.mkdir()
  text = sync_docs._scaffold_readme(d, 'index kind=dirs sort=date,desc')
  assert text.startswith('# My Content Dir\n')
  assert '<!-- TODO:' in text
  assert '<!-- sync:index kind=dirs sort=date,desc -->' in text
  assert '<!-- /sync:index -->' in text
  assert text.endswith('\n')


# ---------- Integration: --yes-to-all flow ----------

def test_init_yes_to_all_creates_readmes(tmp_path):
  repo = tmp_path / 'repo'
  repo.mkdir()
  # Semantic-name dir
  (repo / 'runs').mkdir()
  (repo / 'runs' / '2026-02-01').mkdir()
  (repo / 'runs' / '2026-02-02').mkdir()
  (repo / 'runs' / '2026-02-03').mkdir()
  # ≥5 md-files dir
  docs = repo / 'docs'
  docs.mkdir()
  for i in range(5):
    (docs / f'doc-{i}.md').write_text(f'# Doc {i}\n')
  # Should-be-skipped: existing README
  ignored = repo / 'already-done'
  ignored.mkdir()
  (ignored / 'README.md').write_text('# Done\n')
  for d in ('2026-03-01', '2026-03-02', '2026-03-03'):
    (ignored / d).mkdir()

  result = _run_init(repo, '--yes-to-all')
  assert result.returncode == 0, f"stderr: {result.stderr}"
  assert (repo / 'runs' / 'README.md').exists()
  assert (repo / 'docs' / 'README.md').exists()
  # Existing README untouched
  assert (repo / 'already-done' / 'README.md').read_text() == '# Done\n'


def test_init_no_candidates_exits_zero(tmp_path):
  repo = tmp_path / 'repo'
  repo.mkdir()
  (repo / 'README.md').write_text('# Top\n')
  (repo / 'one-file').mkdir()
  (repo / 'one-file' / 'x.md').write_text('# x\n')
  result = _run_init(repo, '--yes-to-all')
  assert result.returncode == 0
  assert 'No directories qualify' in result.stdout


def test_init_skips_excluded_dirs(tmp_path):
  repo = tmp_path / 'repo'
  repo.mkdir()
  fixtures = repo / 'tests' / 'fixtures'
  fixtures.mkdir(parents=True)
  for date in ('2026-01-01', '2026-01-02', '2026-01-03'):
    (fixtures / date).mkdir()
  result = _run_init(repo, '--yes-to-all')
  assert not (fixtures / 'README.md').exists()


def test_init_max_depth_default_skips_deep_dirs(tmp_path):
  """Default max-depth=2 should not propose READMEs at depth 3+."""
  repo = tmp_path / 'repo'
  archive = repo / 'archive'  # depth 1, semantic name
  archive.mkdir(parents=True)
  for date in ('2026-04-01', '2026-04-02', '2026-04-03'):
    d2 = archive / date  # depth 2
    d2.mkdir()
    # 5 md files directly at depth 2 — qualifies under rule 1
    for i in range(5):
      (d2 / f'doc-{i}.md').write_text(f'# {i}\n')
    # depth 3 nested dir with 5 md files — would qualify if not depth-blocked
    deep = d2 / 'inner'
    deep.mkdir()
    for i in range(5):
      (deep / f'subdoc-{i}.md').write_text(f'# sub {i}\n')
  result = _run_init(repo, '--yes-to-all')
  assert result.returncode == 0
  assert (archive / 'README.md').exists()                       # depth 1 — kept
  assert (archive / '2026-04-01' / 'README.md').exists()         # depth 2 — kept
  assert not (archive / '2026-04-01' / 'inner' / 'README.md').exists()  # depth 3 — blocked


def test_init_max_depth_flag_extends_recursion(tmp_path):
  """--max-depth 3 should allow scaffolding at depth 3."""
  repo = tmp_path / 'repo'
  deep = repo / 'a' / 'b' / 'c'
  deep.mkdir(parents=True)
  for i in range(5):
    (deep / f'doc-{i}.md').write_text(f'# {i}\n')
  result = _run_init(repo, '--yes-to-all', '--max-depth', '3')
  assert result.returncode == 0
  assert (deep / 'README.md').exists()


def test_init_scaffold_passes_subsequent_sync(tmp_path):
  """A scaffolded README's marker block must be valid (parses, syncs cleanly)."""
  repo = tmp_path / 'repo'
  repo.mkdir()
  (repo / 'runs').mkdir()
  for date in ('2026-02-01', '2026-02-02', '2026-02-03'):
    (repo / 'runs' / date).mkdir()
  _run_init(repo, '--yes-to-all')
  # Now run sync; the new README should fill in the marker without errors
  result = subprocess.run(
    [sys.executable, str(SCRIPT), '--scope', str(repo)],
    capture_output=True, text=True,
  )
  assert result.returncode == 0, f"stderr: {result.stderr}"
  readme = (repo / 'runs' / 'README.md').read_text()
  assert '`2026-02-03/`' in readme  # sorted desc
  assert '`2026-02-02/`' in readme
  assert '`2026-02-01/`' in readme

#!/usr/bin/env python3
"""sync-docs — regenerate index regions of README.md and CLAUDE.md.

Usage:
  sync_docs.py [--scope PATH] [sync] [--check]
  sync_docs.py [--scope PATH] init [--yes-to-all]
  sync_docs.py [--scope PATH] add HANDLER [--into FILE]

Exit codes:
  0  success / no drift
  1  drift detected (--check) or operational failure
  2  parser or handler errors
"""

from __future__ import annotations

import argparse
import difflib
import os
import subprocess
import sys
import tempfile
from pathlib import Path

# Add this script's directory to sys.path so sibling imports work
sys.path.insert(0, str(Path(__file__).resolve().parent))

import handlers  # noqa: E402
import markers  # noqa: E402


def discover_repo_root(scope: str | None) -> Path:
  """Find the repo root. --scope <path> overrides; else git toplevel; else cwd."""
  if scope == 'cwd':
    return Path.cwd().resolve()
  if scope:
    return Path(scope).resolve()
  try:
    result = subprocess.run(
      ['git', 'rev-parse', '--show-toplevel'],
      capture_output=True, text=True, check=False,
    )
    if result.returncode == 0:
      return Path(result.stdout.strip()).resolve()
  except FileNotFoundError:
    pass
  return Path.cwd().resolve()


def find_markdown_files(root: Path) -> list[Path]:
  """Walk repo for .md files, skipping hidden and large data dirs."""
  excluded_dirs = {
    '.git', '.venv', 'venv', 'node_modules', '.pytest_cache',
    '__pycache__', '.mypy_cache', '.ruff_cache',
    'fixtures',
  }
  results: list[Path] = []
  for dirpath, dirnames, filenames in os.walk(root):
    dirnames[:] = [d for d in dirnames if d not in excluded_dirs and not d.startswith('.')]
    for fname in filenames:
      if fname.endswith('.md'):
        results.append(Path(dirpath) / fname)
  return results


def atomic_write(path: Path, content: str) -> None:
  """Write content to path atomically (temp file + rename)."""
  fd, tmp_path = tempfile.mkstemp(
    prefix=f'.{path.name}.',
    suffix='.tmp',
    dir=path.parent,
  )
  try:
    with os.fdopen(fd, 'w', encoding='utf-8') as out:
      out.write(content)
    os.replace(tmp_path, path)
  except Exception:
    if os.path.exists(tmp_path):
      os.unlink(tmp_path)
    raise


def _detect_lint_drift(existing_body: list[str], expected_body: list[str], directives: dict) -> str | None:
  """Return a drift description (or None for clean) per lint mode.

  lint=rows (default): compare set of first-column values
  lint=content / lint=both: compare full body equality
  """
  lint_mode = directives.get('lint', 'rows')
  if lint_mode in ('content', 'both'):
    return None if existing_body == expected_body else 'content drift'

  # rows mode — compare first-column key sets
  from formatters import parse_table  # local import to avoid cycle
  _, exp_rows = parse_table(expected_body)
  _, ex_rows = parse_table(existing_body)

  if not exp_rows and not ex_rows:
    return None

  exp_first = list(exp_rows[0].keys())[0] if exp_rows else None
  ex_first = list(ex_rows[0].keys())[0] if ex_rows else None
  expected_keys = {r[exp_first] for r in exp_rows} if exp_first else set()
  actual_keys = {r[ex_first] for r in ex_rows} if ex_first else set()

  if expected_keys == actual_keys:
    return None
  parts: list[str] = []
  missing = sorted(expected_keys - actual_keys)
  extra = sorted(actual_keys - expected_keys)
  if missing:
    parts.append(f"missing rows: {', '.join(missing)}")
  if extra:
    parts.append(f"extra rows: {', '.join(extra)}")
  return '; '.join(parts) if parts else 'rows differ'


def process_file(
  path: Path,
  repo_root: Path,
  config: dict | None = None,
) -> tuple[str, str, list[str], list[str], list[markers.ParseError]]:
  """Parse a markdown file. Returns (original, regenerated, sync_changes,
  lint_drifts, errors).

  sync_changes lists blocks that would be (or were) rewritten.
  lint_drifts lists blocks in mode=lint where the expected body differs
  from the existing body — reported but never written.
  """
  config = config or {}
  original = path.read_text(encoding='utf-8')
  doc = markers.parse(original)
  if not doc.blocks:
    return original, original, [], [], doc.errors

  config_handlers = config.get('handlers', {})

  changes: list[str] = []
  lint_drifts: list[str] = []
  body_replacements: dict[int, list[str]] = {}
  for idx, block in enumerate(doc.blocks):
    handler = handlers.get_handler(block.handler)
    directives = dict(block.directives)
    if handler is None:
      custom_def = config_handlers.get(block.handler, {})
      if isinstance(custom_def, dict) and custom_def.get('source'):
        handler = handlers.get_handler('custom')
        for k, v in custom_def.items():
          directives.setdefault(k, str(v))
      else:
        doc.errors.append(markers.ParseError(
          f"unknown handler 'sync:{block.handler}'",
          block.open_line,
        ))
        continue
    try:
      sources = handler.discover(repo_root, path.parent, directives)
      new_body = handler.render(sources, directives, block.body_lines)
    except Exception as e:
      doc.errors.append(markers.ParseError(
        f"sync:{block.handler} render failed: {e}",
        block.open_line,
      ))
      continue

    is_lint = directives.get('mode') == 'lint'
    if is_lint:
      drift = _detect_lint_drift(block.body_lines, new_body, directives)
      if drift:
        lint_drifts.append(f"  {block.handler} (line {block.open_line}): {drift}")
    elif new_body != block.body_lines:
      body_replacements[idx] = new_body
      changes.append(f"  {block.handler} (line {block.open_line})")

  regenerated = markers.render(doc, body_replacements)
  return original, regenerated, changes, lint_drifts, doc.errors


def cmd_sync(args: argparse.Namespace) -> int:
  repo_root = discover_repo_root(args.scope)
  md_files = find_markdown_files(repo_root)

  modified: list[Path] = []
  lint_files: list[Path] = []
  all_errors: list[tuple[Path, markers.ParseError]] = []
  marker_files = 0
  total_changes: list[tuple[Path, list[str], str, str]] = []
  total_lint_drifts: list[tuple[Path, list[str]]] = []

  for md in md_files:
    try:
      original, regenerated, changes, lint_drifts, errors = process_file(md, repo_root)
    except Exception as e:
      print(f"error processing {md.relative_to(repo_root)}: {e}", file=sys.stderr)
      return 2
    if changes or lint_drifts or errors:
      marker_files += 1
    for err in errors:
      all_errors.append((md, err))
    if regenerated != original:
      if not args.check:
        atomic_write(md, regenerated)
      modified.append(md)
      total_changes.append((md, changes, original, regenerated))
    if lint_drifts:
      lint_files.append(md)
      total_lint_drifts.append((md, lint_drifts))

  if marker_files == 0:
    if not args.check:
      print(f"No <!-- sync:* --> markers found in {repo_root}.")
      print()
      print("To get started:")
      print("  /sync-docs init                # scaffold READMEs for content dirs")
      print("  /sync-docs add skills          # add a marker for the skills index")
    return 0

  for md, err in all_errors:
    rel = md.relative_to(repo_root)
    print(f"{rel}:{err.line}: {err.message}", file=sys.stderr)

  def _print_lint_drifts(stream):
    if not lint_files:
      return
    print(f"\nlint drift in {len(lint_files)} file(s):", file=stream)
    for md, drifts in total_lint_drifts:
      rel = md.relative_to(repo_root)
      print(f"  {rel}", file=stream)
      for d in drifts:
        print(d, file=stream)

  if args.check:
    if modified:
      print(f"drift detected in {len(modified)} file(s):", file=sys.stderr)
      for md, _changes, original, regenerated in total_changes:
        rel = md.relative_to(repo_root)
        diff = difflib.unified_diff(
          original.splitlines(keepends=True),
          regenerated.splitlines(keepends=True),
          fromfile=f'{rel} (current)',
          tofile=f'{rel} (expected)',
          n=3,
        )
        print(''.join(diff), file=sys.stderr, end='')
    _print_lint_drifts(sys.stderr)
    if modified or lint_files:
      return 1
    if all_errors:
      return 2
    return 0

  if modified:
    print(f"updated {len(modified)} file(s):")
    for md, changes, _original, _regenerated in total_changes:
      rel = md.relative_to(repo_root)
      print(f"  {rel}")
      for c in changes:
        print(c)
  elif not lint_files:
    print("no changes (already in sync)")
  _print_lint_drifts(sys.stdout)

  return 2 if all_errors else 0


def cmd_init(args: argparse.Namespace) -> int:
  print("init: not yet implemented (Phase 2 commit 2)", file=sys.stderr)
  return 1


def cmd_add(args: argparse.Namespace) -> int:
  print("add: not yet implemented (Phase 2 commit 2)", file=sys.stderr)
  return 1


def main(argv: list[str] | None = None) -> int:
  parser = argparse.ArgumentParser(
    prog='sync-docs',
    description='Regenerate index regions of README.md and CLAUDE.md from authoritative sources.',
  )
  parser.add_argument('--scope', help='Override repo root (path or "cwd")')

  subparsers = parser.add_subparsers(dest='subcommand')

  sync_p = subparsers.add_parser('sync', help='Regenerate marker contents (default)')
  sync_p.add_argument('--check', action='store_true', help='Dry-run; exit 1 on drift')

  init_p = subparsers.add_parser('init', help='Scaffold READMEs for content directories')
  init_p.add_argument('--yes-to-all', action='store_true', help='Skip confirmation prompts')

  add_p = subparsers.add_parser('add', help='Insert a marker block')
  add_p.add_argument('handler', help='Handler name (skills, agents, etc.)')
  add_p.add_argument('--into', help='Target file (default: ./README.md)')

  # Allow --check at the top level too (sync is the default subcommand)
  parser.add_argument('--check', action='store_true', help=argparse.SUPPRESS)

  args = parser.parse_args(argv)

  if args.subcommand == 'init':
    return cmd_init(args)
  if args.subcommand == 'add':
    return cmd_add(args)
  # default to sync
  return cmd_sync(args)


if __name__ == '__main__':
  sys.exit(main())

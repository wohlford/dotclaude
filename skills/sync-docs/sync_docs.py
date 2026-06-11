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
import re
import subprocess
import sys
import tempfile
from pathlib import Path

# Add this script's directory to sys.path so sibling imports work
sys.path.insert(0, str(Path(__file__).resolve().parent))

import handlers  # noqa: E402
import markers  # noqa: E402

try:
  import yaml  # type: ignore[import-not-found]
  _HAS_YAML = True
except ImportError:
  _HAS_YAML = False


SEMANTIC_DIR_NAMES = frozenset({
  'applications', 'runs', 'jobs', 'incoming', 'archive',
  'data', 'reports', 'extracts', 'dumps',
})

MD_STYLE_EXTENSIONS = frozenset({'.md', '.eml', '.rst'})

EXCLUDED_DIR_NAMES = frozenset({
  '.git', '.venv', 'venv', 'node_modules', '.pytest_cache',
  '__pycache__', '.mypy_cache', '.ruff_cache', 'fixtures',
})

DATE_PREFIX_RE = re.compile(r'^\d{4}-\d{2}-\d{2}')


def load_project_config(repo_root: Path) -> dict:
  """Load .claude/sync-docs.yaml if present. Returns {} on absence or parse failure."""
  config_path = repo_root / '.claude' / 'sync-docs.yaml'
  if not config_path.exists():
    return {}
  if not _HAS_YAML:
    print(
      f"warning: {config_path} found but pyyaml not installed; "
      f"install via 'uv pip install pyyaml' to enable project config",
      file=sys.stderr,
    )
    return {}
  try:
    text = config_path.read_text(encoding='utf-8')
    data = yaml.safe_load(text)
    return data if isinstance(data, dict) else {}
  except Exception as e:
    print(f"warning: failed to parse {config_path}: {e}", file=sys.stderr)
    return {}


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
  results: list[Path] = []
  for dirpath, dirnames, filenames in os.walk(root):
    dirnames[:] = [d for d in dirnames
                   if d not in EXCLUDED_DIR_NAMES and not d.startswith('.')]
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
) -> tuple[str, str, list[str], list[str], list[markers.ParseError], int]:
  """Parse a markdown file. Returns (original, regenerated, sync_changes,
  lint_drifts, errors, block_count).

  sync_changes lists blocks that would be (or were) rewritten.
  lint_drifts lists blocks in mode=lint where the expected body differs
  from the existing body — reported but never written.
  block_count is the number of marker blocks parsed (used by callers to
  distinguish "no markers" from "markers all clean").
  """
  config = config or {}
  original = path.read_text(encoding='utf-8')
  doc = markers.parse(original)
  if not doc.blocks:
    return original, original, [], [], doc.errors, 0

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
  return original, regenerated, changes, lint_drifts, doc.errors, len(doc.blocks)


def cmd_sync(args: argparse.Namespace) -> int:
  repo_root = discover_repo_root(args.scope)
  config = load_project_config(repo_root)
  handlers.set_project_config(config)
  md_files = find_markdown_files(repo_root)

  modified: list[Path] = []
  lint_files: list[Path] = []
  all_errors: list[tuple[Path, markers.ParseError]] = []
  marker_files = 0
  total_changes: list[tuple[Path, list[str], str, str]] = []
  total_lint_drifts: list[tuple[Path, list[str]]] = []

  for md in md_files:
    try:
      original, regenerated, changes, lint_drifts, errors, block_count = process_file(md, repo_root, config)
    except Exception as e:
      print(f"error processing {md.relative_to(repo_root)}: {e}", file=sys.stderr)
      return 2
    if block_count > 0:
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


def _walk_dirs(root: Path):
  """Yield every non-excluded subdirectory of root (recursive)."""
  for dirpath, dirnames, _ in os.walk(root):
    dirnames[:] = [d for d in dirnames
                   if d not in EXCLUDED_DIR_NAMES and not d.startswith('.')]
    for d in dirnames:
      yield Path(dirpath) / d


def _qualify_dir(dir_path: Path) -> tuple[str | None, str | None]:
  """Return (reason, suggested_marker_directive) if dir qualifies for a README,
  else (None, None). The marker directive is the open-marker payload, e.g.
  'index kind=dirs sort=date,desc'."""
  if (dir_path / 'README.md').exists():
    return None, None

  try:
    children = list(dir_path.iterdir())
  except (OSError, PermissionError):
    return None, None

  files = [c for c in children if c.is_file() and not c.name.startswith('.')]
  dirs = [c for c in children if c.is_dir() and not c.name.startswith('.')
          and c.name not in EXCLUDED_DIR_NAMES]

  if not files and not dirs:
    return None, None

  date_dirs = [d for d in dirs if DATE_PREFIX_RE.match(d.name)]
  md_files = [f for f in files if f.suffix in MD_STYLE_EXTENSIONS]

  marker = _choose_marker(dirs, date_dirs, files, md_files)

  # Rule 3: semantic name
  if dir_path.name in SEMANTIC_DIR_NAMES:
    return f"semantic name '{dir_path.name}'", marker

  # Rule 2: ≥3 patterned subdirs (date-prefixed)
  if len(date_dirs) >= 3:
    return f"{len(date_dirs)} date-stamped subdirectories", marker

  # Rule 1: ≥5 markdown-style files
  if len(md_files) >= 5:
    return f"{len(md_files)} markdown-style files", marker

  return None, None


def _choose_marker(dirs: list[Path], date_dirs: list[Path],
                   files: list[Path], md_files: list[Path]) -> str:
  """Pick the most appropriate marker directive for this directory."""
  if date_dirs and len(date_dirs) >= max(3, len(dirs) // 2):
    return 'index kind=dirs sort=date,desc'
  if md_files and len(md_files) >= max(3, len(files) // 2):
    return 'index kind=files extract=h1-and-paragraph sort=alpha'
  return 'index mode=lint'


def _scaffold_readme(dir_path: Path, marker_directive: str) -> str:
  """Generate the README.md content for a fresh scaffold."""
  handler = marker_directive.split(' ', 1)[0]
  title = dir_path.name.replace('_', ' ').replace('-', ' ').title()
  return (
    f"# {title}\n"
    f"\n"
    f"<!-- TODO: one-line purpose -->\n"
    f"\n"
    f"## Index\n"
    f"\n"
    f"<!-- sync:{marker_directive} -->\n"
    f"<!-- /sync:{handler} -->\n"
  )


def cmd_init(args: argparse.Namespace) -> int:
  repo_root = discover_repo_root(args.scope)
  config = load_project_config(repo_root)
  exclude_patterns = config.get('init', {}).get('exclude', []) or []
  candidates: list[tuple[Path, str, str]] = []
  for d in _walk_dirs(repo_root):
    rel = d.relative_to(repo_root)
    rel_depth = len(rel.parts)
    if rel_depth > args.max_depth:
      continue
    rel_str = str(rel) + '/'
    if any(rel_str.startswith(p.rstrip('/') + '/') or str(rel) == p.rstrip('/')
           for p in exclude_patterns):
      continue
    reason, marker = _qualify_dir(d)
    if reason and marker:
      candidates.append((d, reason, marker))

  if not candidates:
    print("No directories qualify for README scaffolding.")
    return 0

  candidates.sort(key=lambda c: c[0])
  created = 0
  skipped = 0
  for dir_path, reason, marker in candidates:
    rel = dir_path.relative_to(repo_root)
    print(f"\n{rel}/  ({reason})")
    print(f"  Suggested marker: <!-- sync:{marker} -->")
    if args.yes_to_all:
      proceed = True
    else:
      try:
        response = input("  Create README.md? [y/N]: ").strip().lower()
      except (EOFError, KeyboardInterrupt):
        print()
        return 1
      proceed = response in ('y', 'yes')
    if proceed:
      readme = dir_path / 'README.md'
      atomic_write(readme, _scaffold_readme(dir_path, marker))
      print(f"  Created {readme.relative_to(repo_root)}")
      created += 1
    else:
      print("  Skipped.")
      skipped += 1

  print(f"\n{created} README(s) created, {skipped} skipped.")
  return 0


HANDLER_DEFAULT_MARKERS = {
  'skills': 'sync:skills cols=Command:key,Purpose:auto',
  'agents': 'sync:agents cols=Agent:key,Purpose:auto',
  'plugins': 'sync:plugins cols=Plugin:key,Purpose:manual',
  'hooks': 'sync:hooks cols=Event:auto,Matcher:auto,Script:key,Purpose:auto',
  'scripts': 'sync:scripts cols=Script:key,Purpose:auto',
  'index': 'sync:index',
}


def cmd_add(args: argparse.Namespace) -> int:
  handler = args.handler

  if handler == 'custom':
    if not args.source or not args.cols:
      print("add custom requires --source GLOB and --cols COLS", file=sys.stderr)
      return 1
    open_marker = f'<!-- sync:custom source="{args.source}" cols={args.cols} -->'
    close_marker = '<!-- /sync:custom -->'
  elif handler in HANDLER_DEFAULT_MARKERS:
    open_marker = f'<!-- {HANDLER_DEFAULT_MARKERS[handler]} -->'
    close_marker = f'<!-- /sync:{handler} -->'
  else:
    print(f"unknown handler: {handler!r}; choices: {', '.join(list(HANDLER_DEFAULT_MARKERS) + ['custom'])}",
          file=sys.stderr)
    return 2

  target = Path(args.into).resolve() if args.into else Path.cwd().resolve() / 'README.md'
  block = f'\n{open_marker}\n{close_marker}\n'

  if target.exists():
    text = target.read_text(encoding='utf-8')
    if not text.endswith('\n'):
      text += '\n'
    atomic_write(target, text + block)
    print(f"appended {handler} marker to {target}")
  else:
    target.parent.mkdir(parents=True, exist_ok=True)
    title = target.parent.name.replace('_', ' ').replace('-', ' ').title() or 'Index'
    atomic_write(target, f'# {title}\n\n## Index\n{block}')
    print(f"created {target} with {handler} marker")

  print("Run /sync-docs to populate.")
  return 0


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
  init_p.add_argument('--max-depth', type=int, default=2,
                      help='Maximum directory depth to scaffold (default: 2)')

  add_p = subparsers.add_parser('add', help='Insert a marker block')
  add_p.add_argument('handler', help='Handler name (skills, agents, plugins, hooks, scripts, index, custom)')
  add_p.add_argument('--into', help='Target file (default: ./README.md)')
  add_p.add_argument('--source', help='Glob source (custom handler only)')
  add_p.add_argument('--cols', help='Column spec (custom handler only)')

  # Allow --check at the top level too (sync is the default subcommand). Use a
  # distinct dest so the sync subparser's default cannot clobber it when the
  # flag precedes the subcommand (`--check sync`); OR the two after parsing.
  parser.add_argument(
    '--check', action='store_true', dest='top_check', help=argparse.SUPPRESS)

  args = parser.parse_args(argv)
  args.check = getattr(args, 'check', False) or args.top_check

  if args.subcommand == 'init':
    return cmd_init(args)
  if args.subcommand == 'add':
    return cmd_add(args)
  # default to sync
  return cmd_sync(args)


if __name__ == '__main__':
  sys.exit(main())

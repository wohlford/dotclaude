"""Built-in sync-docs handlers.

Each handler discovers its source files (relative to repo root and the marker's
containing directory), extracts metadata, and renders the canonical Markdown
representation.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from extractors import (
  EXTRACTORS,
  extract_chain,
  get_chain,
)
from formatters import ColSpec, render_list, render_table, parse_table


_PROJECT_CONFIG: dict = {}


def set_project_config(cfg: dict) -> None:
  """Install the project-local config dict (from .claude/sync-docs.yaml).

  Handlers consult this to override default discovery globs and to look up
  project-defined custom handler definitions.
  """
  global _PROJECT_CONFIG
  _PROJECT_CONFIG = cfg or {}


def _config_for(handler_name: str) -> dict:
  return _PROJECT_CONFIG.get('handlers', {}).get(handler_name, {})


@dataclass
class Source:
  path: Path
  fields: dict[str, Any]


class Handler(Protocol):
  name: str

  def discover(
    self,
    repo_root: Path,
    marker_dir: Path,
    directives: dict[str, str],
  ) -> list[Source]: ...

  def render(
    self,
    sources: list[Source],
    directives: dict[str, str],
    existing_body: list[str],
  ) -> list[str]: ...


# ---------- Helpers ----------

def _parse_cols(directive: str | None) -> list[ColSpec]:
  """Parse a cols= directive value into ColSpecs."""
  if not directive:
    return []
  cols: list[ColSpec] = []
  for part in directive.split(','):
    part = part.strip()
    if ':' in part:
      name, role = part.rsplit(':', 1)
      cols.append(ColSpec(name=name.strip(), role=role.strip()))
    else:
      cols.append(ColSpec(name=part, role='auto'))
  return cols


def _validate_cols(cols: list[ColSpec], handler_name: str) -> str | None:
  """Return error message if cols are invalid, else None."""
  if not cols:
    return None
  key_count = sum(1 for c in cols if c.role == 'key')
  if key_count == 0:
    return f"sync:{handler_name} cols= directive requires exactly one :key column"
  if key_count > 1:
    return f"sync:{handler_name} cols= directive has {key_count} :key columns; expected 1"
  return None


def _get_key_col(cols: list[ColSpec]) -> ColSpec | None:
  for c in cols:
    if c.role == 'key':
      return c
  return None


def _preserve_manual(
  rows: list[dict[str, str]],
  cols: list[ColSpec],
  existing_body: list[str],
) -> list[dict[str, str]]:
  """Merge manual column values from existing_body into newly-generated rows.

  Matches existing rows to new rows by the key column's value.
  """
  manual_cols = [c for c in cols if c.role == 'manual']
  if not manual_cols:
    return rows
  key = _get_key_col(cols)
  if key is None:
    return rows
  _, existing_rows = parse_table(existing_body)
  if not existing_rows:
    return rows
  by_key = {r.get(key.name, ''): r for r in existing_rows}
  for row in rows:
    k = row.get(key.name, '')
    prev = by_key.get(k)
    if prev:
      for mc in manual_cols:
        row[mc.name] = prev.get(mc.name, '')
    else:
      for mc in manual_cols:
        row.setdefault(mc.name, '')
  return rows


def _sort_rows(rows: list[dict[str, str]], directive: str | None, key_field: str) -> list[dict[str, str]]:
  """Apply sort=... directive to rows. Default: alpha ascending by key."""
  if directive is None:
    return sorted(rows, key=lambda r: r.get(key_field, ''))
  parts = [p.strip() for p in directive.split(',')]
  mode = parts[0]
  desc = len(parts) > 1 and parts[1] == 'desc'
  if mode == 'alpha':
    rows = sorted(rows, key=lambda r: r.get(key_field, ''))
  elif mode == 'date':
    # For now, alpha over date-prefixed names sorts correctly
    rows = sorted(rows, key=lambda r: r.get(key_field, ''))
  return list(reversed(rows)) if desc else rows


# ---------- skills ----------

class SkillsHandler:
  name = 'skills'

  def discover(
    self,
    repo_root: Path,
    marker_dir: Path,
    directives: dict[str, str],
  ) -> list[Source]:
    chain = get_chain(directives.get('extract', 'yaml-frontmatter,heading-meta').split(','))
    cfg_source = _config_for('skills').get('source')
    globs = [cfg_source] if cfg_source else ['skills/*/SKILL.md', '.claude/skills/*/SKILL.md']
    sources: list[Source] = []
    seen: set[Path] = set()
    for g in globs:
      for path in repo_root.glob(g):
        if path in seen:
          continue
        seen.add(path)
        fields = extract_chain(path, chain)
        # Skill canonical identity is the directory name; fall back if extractors didn't yield one
        if not fields.get('name'):
          fields['name'] = path.parent.name
        sources.append(Source(path=path, fields=fields))

    filt = directives.get('filter')
    if filt and ':' in filt:
      key, val = filt.split(':', 1)
      sources = [s for s in sources if s.fields.get(key) == val]

    return sources

  def render(
    self,
    sources: list[Source],
    directives: dict[str, str],
    existing_body: list[str],
  ) -> list[str]:
    cols_spec = directives.get('cols', 'Command:key,Purpose:auto')
    cols = _parse_cols(cols_spec)
    err = _validate_cols(cols, self.name)
    if err:
      raise ValueError(err)

    rows: list[dict[str, str]] = []
    for s in sources:
      name = s.fields.get('name', '')
      desc = s.fields.get('description', '')
      row: dict[str, str] = {}
      for c in cols:
        if c.role == 'manual':
          row[c.name] = ''
          continue
        if c.name == 'Command':
          row[c.name] = f'`/{name}`' if name else ''
        elif c.name in ('Skill', 'Name'):
          row[c.name] = f'`{name}`' if name else ''
        elif c.name.lower() in s.fields:
          row[c.name] = str(s.fields[c.name.lower()])
        else:
          # Default for any other auto/key column: the description
          row[c.name] = desc
      rows.append(row)

    key = _get_key_col(cols)
    rows = _sort_rows(rows, directives.get('sort'), key.name if key else cols[0].name)
    rows = _preserve_manual(rows, cols, existing_body)
    return render_table(rows, cols)


# ---------- agents ----------

class AgentsHandler:
  name = 'agents'

  def discover(
    self,
    repo_root: Path,
    marker_dir: Path,
    directives: dict[str, str],
  ) -> list[Source]:
    chain = get_chain(directives.get('extract', 'yaml-frontmatter,heading-meta').split(','))
    cfg_source = _config_for('agents').get('source')
    globs = [cfg_source] if cfg_source else ['agents/*.md', '.claude/agents/*.md']
    excluded = {'README.md', 'index.md'}
    sources: list[Source] = []
    seen: set[Path] = set()
    for g in globs:
      for path in repo_root.glob(g):
        if path.name in excluded or path in seen:
          continue
        seen.add(path)
        fields = extract_chain(path, chain)
        # Agent canonical identity is the filename stem; fall back if extractors didn't yield one
        if not fields.get('name'):
          fields['name'] = path.stem
        sources.append(Source(path=path, fields=fields))

    filt = directives.get('filter')
    if filt and ':' in filt:
      key, val = filt.split(':', 1)
      sources = [s for s in sources if s.fields.get(key) == val]

    return sources

  def render(
    self,
    sources: list[Source],
    directives: dict[str, str],
    existing_body: list[str],
  ) -> list[str]:
    cols_spec = directives.get('cols', 'Agent:key,Purpose:auto')
    cols = _parse_cols(cols_spec)
    err = _validate_cols(cols, self.name)
    if err:
      raise ValueError(err)

    rows: list[dict[str, str]] = []
    for s in sources:
      name = s.fields.get('name', '')
      desc = s.fields.get('description', '')
      row: dict[str, str] = {}
      for c in cols:
        if c.role == 'manual':
          row[c.name] = ''
          continue
        if c.name == 'Agent':
          row[c.name] = f'`{name}`' if name else ''
        elif c.name in ('Skill', 'Name'):
          row[c.name] = f'`{name}`' if name else ''
        elif c.name.lower() in s.fields:
          row[c.name] = str(s.fields[c.name.lower()])
        else:
          # Default for any other auto/key column: the description
          row[c.name] = desc
      rows.append(row)

    key = _get_key_col(cols)
    rows = _sort_rows(rows, directives.get('sort'), key.name if key else cols[0].name)
    rows = _preserve_manual(rows, cols, existing_body)
    return render_table(rows, cols)


# ---------- plugins ----------

class PluginsHandler:
  name = 'plugins'

  def discover(
    self,
    repo_root: Path,
    marker_dir: Path,
    directives: dict[str, str],
  ) -> list[Source]:
    settings = repo_root / 'settings.json'
    if not settings.exists():
      return []
    try:
      data = json.loads(settings.read_text())
    except json.JSONDecodeError:
      return []
    enabled = data.get('enabledPlugins', {})
    sources: list[Source] = []
    for plugin_id, is_enabled in enabled.items():
      if is_enabled:
        # plugin id is "name@vendor" form; render the name part
        short_name = plugin_id.split('@', 1)[0]
        sources.append(Source(
          path=settings,
          fields={'name': short_name, 'plugin_id': plugin_id},
        ))
    return sources

  def render(
    self,
    sources: list[Source],
    directives: dict[str, str],
    existing_body: list[str],
  ) -> list[str]:
    cols_spec = directives.get('cols', 'Plugin:key,Purpose:manual')
    cols = _parse_cols(cols_spec)
    err = _validate_cols(cols, self.name)
    if err:
      raise ValueError(err)

    rows: list[dict[str, str]] = []
    for s in sources:
      row: dict[str, str] = {}
      for c in cols:
        if c.name == 'Plugin':
          row[c.name] = f'`{s.fields.get("name", "")}`'
        elif c.name == 'Purpose':
          row[c.name] = ''
        else:
          row[c.name] = ''
      rows.append(row)

    key = _get_key_col(cols)
    rows = _sort_rows(rows, directives.get('sort'), key.name if key else cols[0].name)
    rows = _preserve_manual(rows, cols, existing_body)
    return render_table(rows, cols)


# ---------- hooks ----------

class HooksHandler:
  name = 'hooks'

  def discover(
    self,
    repo_root: Path,
    marker_dir: Path,
    directives: dict[str, str],
  ) -> list[Source]:
    settings = repo_root / 'settings.json'
    if not settings.exists():
      return []
    try:
      data = json.loads(settings.read_text())
    except json.JSONDecodeError:
      return []
    sources: list[Source] = []
    for event, hook_groups in data.get('hooks', {}).items():
      for group in hook_groups:
        matcher = group.get('matcher', '')
        for hook in group.get('hooks', []):
          cmd = hook.get('command', '')
          # Try to extract purpose from script header if path resolves locally
          purpose = ''
          script_basename = ''
          script_path = self._resolve_script(cmd, repo_root)
          if script_path and script_path.exists():
            from extractors import BashHeaderExtractor
            fields = BashHeaderExtractor().extract(script_path)
            purpose = fields.get('description', '')
            script_basename = script_path.name
          else:
            script_basename = cmd.split('/')[-1] if '/' in cmd else cmd
          sources.append(Source(
            path=settings,
            fields={
              'event': event,
              'matcher': matcher,
              'script': script_basename,
              'description': purpose,
            },
          ))
    return sources

  def _resolve_script(self, cmd: str, repo_root: Path) -> Path | None:
    # Replace $HOME with repo_root for in-repo script references
    cmd = cmd.replace('$HOME/.claude/', str(repo_root) + '/')
    parts = cmd.split()
    if not parts:
      return None
    candidate = Path(parts[0])
    if candidate.is_absolute():
      return candidate
    return repo_root / candidate

  def render(
    self,
    sources: list[Source],
    directives: dict[str, str],
    existing_body: list[str],
  ) -> list[str]:
    cols_spec = directives.get('cols', 'Event:auto,Matcher:auto,Script:key,Purpose:auto')
    cols = _parse_cols(cols_spec)
    err = _validate_cols(cols, self.name)
    if err:
      raise ValueError(err)

    rows: list[dict[str, str]] = []
    for s in sources:
      row: dict[str, str] = {}
      for c in cols:
        if c.name == 'Event':
          row[c.name] = s.fields.get('event', '')
        elif c.name == 'Matcher':
          row[c.name] = f'`{s.fields["matcher"]}`' if s.fields.get('matcher') else ''
        elif c.name == 'Script':
          row[c.name] = f'`{s.fields["script"]}`' if s.fields.get('script') else ''
        elif c.name == 'Purpose':
          row[c.name] = s.fields.get('description', '')
        else:
          row[c.name] = ''
      rows.append(row)

    rows = _preserve_manual(rows, cols, existing_body)
    return render_table(rows, cols)


# ---------- scripts ----------

class ScriptsHandler:
  name = 'scripts'

  def discover(
    self,
    repo_root: Path,
    marker_dir: Path,
    directives: dict[str, str],
  ) -> list[Source]:
    chain = get_chain(directives.get('extract', 'bash-header,py-docstring,h1-and-paragraph').split(','))
    cfg_source = _config_for('scripts').get('source')
    globs = [cfg_source] if cfg_source else ['scripts/*.sh', 'scripts/*.py']
    sources: list[Source] = []
    seen: set[Path] = set()
    for g in globs:
      for path in repo_root.glob(g):
        if path in seen:
          continue
        seen.add(path)
        fields = extract_chain(path, chain)
        if 'name' not in fields:
          fields['name'] = path.name
        sources.append(Source(path=path, fields=fields))
    return sources

  def render(
    self,
    sources: list[Source],
    directives: dict[str, str],
    existing_body: list[str],
  ) -> list[str]:
    cols_spec = directives.get('cols', 'Script:key,Purpose:auto')
    cols = _parse_cols(cols_spec)
    err = _validate_cols(cols, self.name)
    if err:
      raise ValueError(err)

    rows: list[dict[str, str]] = []
    for s in sources:
      row: dict[str, str] = {}
      name = s.path.name
      for c in cols:
        if c.name == 'Script':
          row[c.name] = f'`{name}`'
        elif c.name == 'Purpose':
          row[c.name] = s.fields.get('description', '')
        else:
          row[c.name] = ''
      rows.append(row)

    key = _get_key_col(cols)
    rows = _sort_rows(rows, directives.get('sort'), key.name if key else cols[0].name)
    rows = _preserve_manual(rows, cols, existing_body)
    return render_table(rows, cols)


# ---------- index ----------

class IndexHandler:
  name = 'index'

  def discover(
    self,
    repo_root: Path,
    marker_dir: Path,
    directives: dict[str, str],
  ) -> list[Source]:
    kind = directives.get('kind', 'all')
    extensions = directives.get('extensions')
    pattern = directives.get('pattern')

    children: list[Path] = []
    for child in marker_dir.iterdir():
      if child.name.startswith('.'):
        continue
      if child.name == 'README.md':
        continue
      if kind == 'dirs' and not child.is_dir():
        continue
      if kind == 'files' and not child.is_file():
        continue
      if extensions and child.is_file():
        ext = child.suffix.lstrip('.')
        if ext not in [e.strip() for e in extensions.split(',')]:
          continue
      if pattern and not re.search(pattern, child.name):
        continue
      children.append(child)

    sources: list[Source] = []
    summary_from = directives.get('summary-from', 'auto')
    for child in children:
      summary = self._extract_summary(child, summary_from)
      sources.append(Source(
        path=child,
        fields={'name': child.name, 'summary': summary, 'is_dir': child.is_dir()},
      ))
    return sources

  def _extract_summary(self, path: Path, mode: str) -> str:
    if mode == 'none':
      return ''
    if path.is_dir():
      readme = path / 'README.md'
      if mode in ('README.md', 'auto') and readme.exists():
        return self._first_paragraph_after_h1(readme)
      return ''
    if path.suffix == '.md':
      if mode in ('first-h1', 'auto'):
        return self._first_paragraph_after_h1(path)
      if mode == 'first-paragraph':
        return self._first_paragraph(path)
    return ''

  def _first_paragraph_after_h1(self, path: Path) -> str:
    text = path.read_text(encoding='utf-8', errors='replace')
    lines = text.splitlines()
    if lines and lines[0].strip() == '---':
      for i, ln in enumerate(lines[1:], start=1):
        if ln.strip() == '---':
          lines = lines[i + 1:]
          break
    in_h1 = False
    for ln in lines:
      if ln.startswith('# '):
        in_h1 = True
        continue
      if in_h1 and ln.strip() and not ln.startswith('#'):
        return ln.strip()
      if in_h1 and ln.startswith('#'):
        return ''
    return ''

  def _first_paragraph(self, path: Path) -> str:
    text = path.read_text(encoding='utf-8', errors='replace')
    for ln in text.splitlines():
      if ln.strip() and not ln.startswith('#') and ln.strip() != '---':
        return ln.strip()
    return ''

  def render(
    self,
    sources: list[Source],
    directives: dict[str, str],
    existing_body: list[str],
  ) -> list[str]:
    # Always compute canonical body. Lint vs sync semantics are enforced by
    # the dispatcher (process_file) — lint mode reports drift but does not
    # write the rendered body back to the file.
    sort_directive = directives.get('sort', 'alpha')
    parts = [p.strip() for p in sort_directive.split(',')]
    mode = parts[0]
    desc = len(parts) > 1 and parts[1] == 'desc'
    if mode == 'mtime':
      sources = sorted(sources, key=lambda s: s.path.stat().st_mtime, reverse=desc)
    else:
      # 'alpha' and 'date' both sort by name (YYYY-MM-DD prefix sorts naturally)
      sources = sorted(sources, key=lambda s: s.path.name, reverse=desc)

    limit = directives.get('limit')
    if limit:
      try:
        sources = sources[:int(limit)]
      except ValueError:
        pass

    rows: list[dict[str, str]] = []
    for s in sources:
      name = s.path.name
      label = f'`{name}/`' if s.fields.get('is_dir') else f'`{name}`'
      rows.append({'Entry': label, 'Summary': s.fields.get('summary', '')})

    cols = [ColSpec(name='Entry', role='key'), ColSpec(name='Summary', role='auto')]
    return render_table(rows, cols)


# ---------- custom ----------

class CustomHandler:
  """Generic handler for ad-hoc indexes.

  Configured per-marker via directives:
    source=<glob>           — files to discover (relative to repo root)
    extract=<chain>         — extractor chain (default: yaml-frontmatter,heading-meta)
    cols=<col-spec>         — column list with role annotations (required)

  Each column maps to a frontmatter field by lowercased name. Special
  treatment: a column literally named 'File', 'Name', or 'Path' renders
  the source file's path/name in backticks.
  """
  name = 'custom'

  def discover(
    self,
    repo_root: Path,
    marker_dir: Path,
    directives: dict[str, str],
  ) -> list[Source]:
    source = directives.get('source')
    if not source:
      raise ValueError("sync:custom requires source=<glob>")
    chain_names = directives.get('extract', 'yaml-frontmatter,heading-meta').split(',')
    chain = get_chain(chain_names)
    sources: list[Source] = []
    for path in repo_root.glob(source):
      if not path.is_file():
        continue
      fields = extract_chain(path, chain)
      sources.append(Source(path=path, fields=fields))
    return sources

  def render(
    self,
    sources: list[Source],
    directives: dict[str, str],
    existing_body: list[str],
  ) -> list[str]:
    cols_spec = directives.get('cols')
    if not cols_spec:
      raise ValueError("sync:custom requires cols=<col-spec>")
    cols = _parse_cols(cols_spec)
    err = _validate_cols(cols, self.name)
    if err:
      raise ValueError(err)

    rows: list[dict[str, str]] = []
    for s in sources:
      row: dict[str, str] = {}
      for c in cols:
        if c.role == 'manual':
          row[c.name] = ''
          continue
        if c.name in ('File', 'Path'):
          row[c.name] = f'`{s.path.name}`'
        elif c.name == 'Name':
          row[c.name] = f'`{s.path.stem}`'
        elif c.name.lower() in s.fields:
          row[c.name] = str(s.fields[c.name.lower()])
        else:
          row[c.name] = ''
      rows.append(row)

    key = _get_key_col(cols)
    rows = _sort_rows(rows, directives.get('sort'), key.name if key else cols[0].name)
    rows = _preserve_manual(rows, cols, existing_body)
    return render_table(rows, cols)


# ---------- registry ----------

HANDLERS: dict[str, Handler] = {
  'skills': SkillsHandler(),
  'agents': AgentsHandler(),
  'plugins': PluginsHandler(),
  'hooks': HooksHandler(),
  'scripts': ScriptsHandler(),
  'index': IndexHandler(),
  'custom': CustomHandler(),
}


def get_handler(name: str) -> Handler | None:
  return HANDLERS.get(name)

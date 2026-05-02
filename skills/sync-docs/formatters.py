"""Canonical Markdown table and list rendering.

Idempotent: same input always produces same output, byte-identical. Cell
padding to longest column value ensures stable layout across reruns.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ColSpec:
  name: str
  role: str = 'auto'   # 'auto', 'manual', 'key'
  align: str = 'left'  # 'left', 'right', 'center'


def _escape_cell(s: str) -> str:
  """Escape a value for safe inclusion in a Markdown table cell.

  - Newlines are collapsed to single spaces (cells are one line).
  - Literal '|' is escaped as '\\|' per GFM table syntax.
  """
  s = s.replace('\r\n', ' ').replace('\n', ' ').replace('\r', ' ')
  s = s.replace('|', '\\|')
  return s.strip()


def render_table(rows: list[dict[str, str]], cols: list[ColSpec]) -> list[str]:
  """Render a GitHub-flavored Markdown table.

  Each column's width is the max of its header name and all row values for
  that column (after escaping), with a floor of 3 to keep separator dashes
  canonical. Returns a list of lines without trailing newline.
  """
  if not cols:
    return []

  # Escape values once; widths are computed against escaped strings
  escaped_rows: list[dict[str, str]] = [
    {c.name: _escape_cell(row.get(c.name, '')) for c in cols}
    for row in rows
  ]

  widths: list[int] = []
  for c in cols:
    w = max(len(c.name), 3)
    for row in escaped_rows:
      w = max(w, len(row.get(c.name, '')))
    widths.append(w)

  header_cells = [c.name.ljust(w) for c, w in zip(cols, widths)]
  out = ['| ' + ' | '.join(header_cells) + ' |']

  sep_cells: list[str] = []
  for c, w in zip(cols, widths):
    if c.align == 'center':
      sep_cells.append(':' + '-' * (w - 2) + ':')
    elif c.align == 'right':
      sep_cells.append('-' * (w - 1) + ':')
    else:
      sep_cells.append(':' + '-' * (w - 1))
  out.append('| ' + ' | '.join(sep_cells) + ' |')

  for row in escaped_rows:
    cells = [row.get(c.name, '').ljust(w) for c, w in zip(cols, widths)]
    out.append('| ' + ' | '.join(cells) + ' |')

  return out


def render_list(items: list[str]) -> list[str]:
  """Render a bulleted list."""
  return [f'- {item}' for item in items]


def parse_table(lines: list[str]) -> tuple[list[ColSpec], list[dict[str, str]]]:
  """Parse a Markdown table out of body lines. Used to recover existing
  manual-column data before regeneration. Returns (cols, rows). Returns
  ([], []) if no parseable table found."""
  table_lines = [ln for ln in lines if ln.lstrip().startswith('|')]
  if len(table_lines) < 2:
    return [], []
  header = _split_row(table_lines[0])
  if not header:
    return [], []
  # second line should be separator; we don't strictly validate alignment
  data_rows: list[dict[str, str]] = []
  for ln in table_lines[2:]:
    cells = _split_row(ln)
    if len(cells) != len(header):
      continue
    data_rows.append({h: cells[i] for i, h in enumerate(header)})
  cols = [ColSpec(name=h) for h in header]
  return cols, data_rows


def _split_row(line: str) -> list[str]:
  """Split a Markdown table row, respecting backslash-escaped pipes (\\|)."""
  s = line.strip()
  if not s.startswith('|') or not s.endswith('|'):
    return []
  inner = s[1:-1]
  cells: list[str] = []
  buf: list[str] = []
  i = 0
  while i < len(inner):
    if inner[i] == '\\' and i + 1 < len(inner) and inner[i + 1] == '|':
      buf.append('|')
      i += 2
    elif inner[i] == '|':
      cells.append(''.join(buf).strip())
      buf = []
      i += 1
    else:
      buf.append(inner[i])
      i += 1
  cells.append(''.join(buf).strip())
  return cells

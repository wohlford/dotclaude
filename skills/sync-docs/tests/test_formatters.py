"""Tests for formatters.py — table rendering, idempotence, parse_table."""

from __future__ import annotations

import formatters


def test_render_table_basic():
  rows = [
    {'Command': '`/commit`', 'Purpose': 'Create commit'},
    {'Command': '`/init-skill`', 'Purpose': 'Scaffold skill'},
  ]
  cols = [
    formatters.ColSpec(name='Command', role='key'),
    formatters.ColSpec(name='Purpose', role='auto'),
  ]
  out = formatters.render_table(rows, cols)
  assert len(out) == 4  # header + sep + 2 rows
  assert out[0].startswith('| Command')
  assert ' | Purpose' in out[0]
  assert ':---' in out[1]
  assert '`/commit`' in out[2]


def test_render_table_idempotent_with_canonical_padding():
  rows = [{'A': 'short', 'B': 'longer-value'}]
  cols = [formatters.ColSpec(name='A', role='key'), formatters.ColSpec(name='B', role='auto')]
  out1 = formatters.render_table(rows, cols)
  out2 = formatters.render_table(rows, cols)
  assert out1 == out2
  # parse-then-render should also be stable
  parsed_cols, parsed_rows = formatters.parse_table(out1)
  out3 = formatters.render_table(parsed_rows, cols)
  assert out1 == out3


def test_render_table_column_widths_pad_to_longest():
  rows = [{'Name': 'a'}, {'Name': 'longer'}]
  cols = [formatters.ColSpec(name='Name', role='key')]
  out = formatters.render_table(rows, cols)
  # all data cells should have same length
  for line in out[2:]:
    cell = line.split('|')[1]
    assert len(cell) == len(out[2].split('|')[1])


def test_parse_table_recovers_rows():
  lines = [
    "| A | B |",
    "| :--- | :--- |",
    "| x | y |",
    "| p | q |",
  ]
  cols, rows = formatters.parse_table(lines)
  assert [c.name for c in cols] == ['A', 'B']
  assert rows == [{'A': 'x', 'B': 'y'}, {'A': 'p', 'B': 'q'}]


def test_parse_table_no_table_returns_empty():
  lines = ["just text", "no tables here"]
  cols, rows = formatters.parse_table(lines)
  assert cols == []
  assert rows == []


def test_render_list():
  out = formatters.render_list(['a', 'b', 'c'])
  assert out == ['- a', '- b', '- c']


def test_render_table_escapes_pipes_in_cells():
  rows = [{'A': 'has | a pipe', 'B': 'plain'}]
  cols = [formatters.ColSpec(name='A', role='key'), formatters.ColSpec(name='B', role='auto')]
  out = formatters.render_table(rows, cols)
  data_line = out[2]
  assert 'has \\| a pipe' in data_line
  # Row count: 1 header + 1 sep + 1 data = no broken extra cells
  assert len(out) == 3


def test_render_table_collapses_newlines():
  rows = [{'A': 'line1\nline2\nline3'}]
  cols = [formatters.ColSpec(name='A', role='key')]
  out = formatters.render_table(rows, cols)
  assert 'line1 line2 line3' in out[2]
  assert '\n' not in out[2]


def test_parse_table_handles_escaped_pipes():
  lines = [
    "| A           | B     |",
    "| :---------- | :---- |",
    "| has \\| pipe | plain |",
  ]
  cols, rows = formatters.parse_table(lines)
  assert rows == [{'A': 'has | pipe', 'B': 'plain'}]


def test_render_then_parse_roundtrips_pipes():
  rows = [{'A': 'value | with pipe', 'B': 'normal'}]
  cols = [formatters.ColSpec(name='A', role='key'), formatters.ColSpec(name='B', role='auto')]
  rendered = formatters.render_table(rows, cols)
  _, parsed = formatters.parse_table(rendered)
  assert parsed == rows

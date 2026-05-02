"""Marker-block parser for sync-docs.

Scans a markdown document and extracts <!-- sync:<handler> [directives] -->
... <!-- /sync:<handler> --> regions. Markers inside fenced code blocks are
inert. Errors are collected (not raised) so a single malformed marker does
not abort processing of the rest of the document.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

OPEN_RE = re.compile(r'^\s*<!--\s*sync:([a-z][a-z0-9-]*)(?:\s+(.+?))?\s*-->\s*$')
CLOSE_RE = re.compile(r'^\s*<!--\s*/sync:([a-z][a-z0-9-]*)\s*-->\s*$')
FENCE_RE = re.compile(r'^(\s*)(```+|~~~+)')


@dataclass(frozen=True)
class MarkerBlock:
  handler: str
  directives: dict[str, str]
  open_line: int    # 1-indexed
  close_line: int   # 1-indexed
  body_lines: list[str]


@dataclass(frozen=True)
class ParseError:
  message: str
  line: int


@dataclass
class ParsedDoc:
  source_lines: list[str]
  blocks: list[MarkerBlock] = field(default_factory=list)
  errors: list[ParseError] = field(default_factory=list)


def parse_directives(s: str) -> dict[str, str]:
  """Tokenize 'key=val key2="val with spaces" key3=a,"b c",d' into a dict.

  A directive value is a sequence of bare and quoted segments concatenated
  with no separator; segments end at whitespace (outside quotes). This
  supports embedded quoted substrings like cols=Agent:key,"Used by":manual.
  Quotes in the source are stripped from the captured value.
  """
  result: dict[str, str] = {}
  i, n = 0, len(s)
  while i < n:
    while i < n and s[i].isspace():
      i += 1
    if i >= n:
      break
    key_start = i
    while i < n and s[i] != '=' and not s[i].isspace():
      i += 1
    if i >= n or s[i] != '=':
      raise ValueError(f"expected '=' after key starting at column {key_start + 1}")
    key = s[key_start:i]
    if not key:
      raise ValueError(f"empty key at column {key_start + 1}")
    i += 1
    parts: list[str] = []
    while i < n and not s[i].isspace():
      if s[i] == '"':
        i += 1
        seg_start = i
        while i < n and s[i] != '"':
          i += 1
        if i >= n:
          raise ValueError(f"unbalanced quote starting at column {seg_start}")
        parts.append(s[seg_start:i])
        i += 1
      else:
        seg_start = i
        while i < n and s[i] != '"' and not s[i].isspace():
          i += 1
        parts.append(s[seg_start:i])
    result[key] = ''.join(parts)
  return result


def parse(text: str) -> ParsedDoc:
  """Parse markdown text into ParsedDoc. Errors are collected, not raised."""
  lines = text.splitlines(keepends=False)
  doc = ParsedDoc(source_lines=lines)

  state = "NORMAL"
  fence_marker: str | None = None
  open_handler: str | None = None
  open_directives: dict[str, str] = {}
  open_line: int = 0
  body: list[str] = []

  for idx, line in enumerate(lines):
    line_num = idx + 1

    if state == "NORMAL":
      fm = FENCE_RE.match(line)
      if fm:
        state = "IN_FENCED"
        fence_marker = fm.group(2)[:3]
        continue
      cm = CLOSE_RE.match(line)
      if cm:
        doc.errors.append(ParseError(
          f"close marker '/sync:{cm.group(1)}' without matching open",
          line_num,
        ))
        continue
      om = OPEN_RE.match(line)
      if om:
        handler = om.group(1)
        raw_directives = om.group(2) or ""
        try:
          directives = parse_directives(raw_directives)
        except ValueError as e:
          doc.errors.append(ParseError(f"directive parse error: {e}", line_num))
          continue
        state = "IN_MARKER"
        open_handler = handler
        open_directives = directives
        open_line = line_num
        body = []
      continue

    if state == "IN_FENCED":
      if FENCE_RE.match(line):
        # Closing fence: any same-or-longer run of the same char on a line
        # by itself counts as a close. We treat the first fence-shaped line
        # encountered as the close (lenient).
        state = "NORMAL"
        fence_marker = None
      continue

    if state == "IN_MARKER":
      cm = CLOSE_RE.match(line)
      if cm:
        if cm.group(1) == open_handler:
          doc.blocks.append(MarkerBlock(
            handler=open_handler,
            directives=open_directives,
            open_line=open_line,
            close_line=line_num,
            body_lines=list(body),
          ))
          state = "NORMAL"
          open_handler = None
          open_directives = {}
          open_line = 0
          body = []
        else:
          doc.errors.append(ParseError(
            f"mismatched close marker '/sync:{cm.group(1)}' "
            f"(expected '/sync:{open_handler}' from line {open_line})",
            line_num,
          ))
          # Recover: treat as close anyway to avoid swallowing rest of file
          state = "NORMAL"
          open_handler = None
          open_directives = {}
          open_line = 0
          body = []
        continue
      om = OPEN_RE.match(line)
      if om:
        doc.errors.append(ParseError(
          f"nested marker 'sync:{om.group(1)}' inside open 'sync:{open_handler}' "
          f"from line {open_line}",
          line_num,
        ))
        # Recover: keep current marker open, swallow nested as body
        body.append(line)
        continue
      body.append(line)
      continue

  if state == "IN_MARKER":
    doc.errors.append(ParseError(
      f"unclosed marker 'sync:{open_handler}' opened at line {open_line}",
      len(lines),
    ))

  return doc


def render(doc: ParsedDoc, body_replacements: dict[int, list[str]] | None = None) -> str:
  """Re-emit the document, optionally replacing block bodies.

  body_replacements maps block-index (0-based into doc.blocks) to new body
  lines (without the open/close marker lines themselves). Blocks not in the
  map keep their original body. Trailing newline is preserved if present in
  the original source.
  """
  body_replacements = body_replacements or {}
  out: list[str] = []
  block_iter = iter(enumerate(doc.blocks))
  next_block = next(block_iter, None)

  i = 0
  n = len(doc.source_lines)
  while i < n:
    if next_block is not None and i + 1 == next_block[1].open_line:
      block_idx, block = next_block
      out.append(doc.source_lines[i])  # open marker line
      replacement = body_replacements.get(block_idx, block.body_lines)
      out.extend(replacement)
      out.append(doc.source_lines[block.close_line - 1])  # close marker line
      i = block.close_line
      next_block = next(block_iter, None)
      continue
    out.append(doc.source_lines[i])
    i += 1

  result = "\n".join(out)
  # Preserve final newline if original had one
  return result + "\n" if not result.endswith("\n") else result

"""Tests for extractors.py — yaml, heading-meta, bash-header, py-docstring, h1-and-paragraph."""

from __future__ import annotations

import extractors


def test_yaml_frontmatter_basic(tmp_path):
  p = tmp_path / "skill.md"
  p.write_text("---\nname: foo\ndescription: A foo skill\n---\n# /foo\n")
  fields = extractors.YamlFrontmatterExtractor().extract(p)
  assert fields['name'] == 'foo'
  assert fields['description'] == 'A foo skill'


def test_yaml_frontmatter_quoted_string(tmp_path):
  p = tmp_path / "skill.md"
  p.write_text('---\nname: "foo"\ndescription: \'A foo\'\n---\n')
  fields = extractors.YamlFrontmatterExtractor().extract(p)
  assert fields['name'] == 'foo'
  assert fields['description'] == 'A foo'


def test_yaml_frontmatter_boolean(tmp_path):
  p = tmp_path / "skill.md"
  p.write_text("---\nname: foo\ndisable-model-invocation: true\n---\n")
  fields = extractors.YamlFrontmatterExtractor().extract(p)
  assert fields['disable-model-invocation'] is True


def test_yaml_frontmatter_no_frontmatter(tmp_path):
  p = tmp_path / "skill.md"
  p.write_text("# /foo\nsome description\n")
  assert extractors.YamlFrontmatterExtractor().extract(p) == {}


def test_heading_meta_with_skill_heading(tmp_path):
  p = tmp_path / "skill.md"
  p.write_text("# /foo — Do the foo\n\nA description of foo.\n")
  fields = extractors.HeadingMetaExtractor().extract(p)
  assert fields['name'] == 'foo'
  assert fields['title'] == 'Do the foo'
  assert fields['description'] == 'A description of foo.'


def test_heading_meta_model_and_tools_sections(tmp_path):
  """Legacy agent format: ## Model and ## Tools sections become fields."""
  p = tmp_path / "agent.md"
  p.write_text(
    "# Foo Agent\n\n"
    "Does the foo work.\n\n"
    "## Model\n\n"
    "opus\n\n"
    "## Tools\n\n"
    "Read, Write, Bash\n"
  )
  fields = extractors.HeadingMetaExtractor().extract(p)
  assert fields['model'] == 'opus'
  assert fields['tools'] == 'Read, Write, Bash'


def test_heading_meta_no_slash_prefix_omits_name(tmp_path):
  """H1 without /<name> prefix should not set name (handler fills from filesystem)."""
  p = tmp_path / "agent.md"
  p.write_text("# Packager Agent\n\nGenerates packages.\n")
  fields = extractors.HeadingMetaExtractor().extract(p)
  assert 'name' not in fields
  assert fields['title'] == 'Packager Agent'
  assert fields['description'] == 'Generates packages.'


def test_heading_meta_configuration_block(tmp_path):
  p = tmp_path / "skill.md"
  p.write_text(
    "# /foo — Do the foo\n\n"
    "## Configuration\n\n"
    "category: extraction\n"
    "disable-model-invocation: true\n"
    "\n## Other\n"
  )
  fields = extractors.HeadingMetaExtractor().extract(p)
  assert fields['category'] == 'extraction'
  assert fields['disable-model-invocation'] is True


def test_bash_header(tmp_path):
  p = tmp_path / "x.sh"
  p.write_text(
    "#!/usr/bin/env bash\n"
    "set -euo pipefail\n"
    "\n"
    "# Script: process-data.sh\n"
    "# Purpose: Process data files\n"
    "# Usage: ./process-data.sh <file>\n"
  )
  fields = extractors.BashHeaderExtractor().extract(p)
  assert fields['name'] == 'process-data.sh'
  assert fields['description'] == 'Process data files'


def test_py_docstring(tmp_path):
  p = tmp_path / "x.py"
  p.write_text(
    '#!/usr/bin/env python3\n'
    '"""Process data with validation.\n'
    '\n'
    'Reads a CSV and emits JSON.\n'
    '"""\n'
    'import sys\n'
  )
  fields = extractors.PyDocstringExtractor().extract(p)
  assert fields['title'] == 'Process data with validation.'
  assert 'Reads a CSV' in fields['description']


def test_h1_and_paragraph_fallback(tmp_path):
  p = tmp_path / "x.md"
  p.write_text("# Some Title\n\nSome paragraph that\nspans two lines.\n")
  fields = extractors.H1AndParagraphExtractor().extract(p)
  assert fields['name'] == 'some-title'
  assert fields['title'] == 'Some Title'
  assert 'Some paragraph that' in fields['description']


def test_chain_merge_yaml_and_heading_meta(tmp_path):
  """YAML provides name; heading-meta fills in description."""
  p = tmp_path / "skill.md"
  p.write_text(
    "---\n"
    "name: foo\n"
    "---\n"
    "# /foo — Do the foo\n"
    "\n"
    "Falls through to heading meta.\n"
  )
  chain = extractors.get_chain(['yaml-frontmatter', 'heading-meta'])
  fields = extractors.extract_chain(p, chain)
  assert fields['name'] == 'foo'
  assert fields['description'] == 'Falls through to heading meta.'


def test_chain_earlier_wins(tmp_path):
  """If both extractors provide the same key, earlier wins."""
  p = tmp_path / "skill.md"
  p.write_text(
    "---\n"
    "name: from-yaml\n"
    "---\n"
    "# /from-heading — title\n"
  )
  chain = extractors.get_chain(['yaml-frontmatter', 'heading-meta'])
  fields = extractors.extract_chain(p, chain)
  assert fields['name'] == 'from-yaml'


def test_slugify():
  assert extractors.slugify("Hello World") == 'hello-world'
  assert extractors.slugify("Foo / Bar! Baz") == 'foo-bar-baz'
  assert extractors.slugify("  trimmed  ") == 'trimmed'

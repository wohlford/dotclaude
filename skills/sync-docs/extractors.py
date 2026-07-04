"""Metadata extractors for source files.

Five extractors cover the common metadata formats found in skill, agent, and
script files. Each takes a Path and returns a partial dict of fields. Extractors
chain via extract_chain(), with earlier extractors winning per key (later ones
fill gaps).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Protocol


class Extractor(Protocol):
    """Protocol for extractors: a name plus extract(path) -> partial field dict."""

    name: str

    def extract(self, path: Path) -> dict[str, Any]: ...


def extract_chain(path: Path, chain: list[Extractor]) -> dict[str, Any]:
    """Run extractors in order; merge with earlier wins per key."""
    result: dict[str, Any] = {}
    for ex in chain:
        try:
            partial = ex.extract(path)
        except Exception:
            continue
        for k, v in partial.items():
            if k not in result and v is not None and v != "":
                result[k] = v
    return result


def slugify(s: str) -> str:
    """Derive a kebab-case identifier from a heading."""
    s = s.strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-")


# ---------- yaml_frontmatter ----------


class YamlFrontmatterExtractor:
    """Extract fields from a leading `---` YAML frontmatter block."""

    name = "yaml-frontmatter"

    def extract(self, path: Path) -> dict[str, Any]:
        text = path.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()
        if not lines or lines[0].strip() != "---":
            return {}
        end = None
        for i, ln in enumerate(lines[1:], start=1):
            if ln.strip() == "---":
                end = i
                break
        if end is None:
            return {}
        return _parse_simple_yaml(lines[1:end])


def _parse_simple_yaml(lines: list[str]) -> dict[str, Any]:
    """Parse a constrained YAML subset suitable for skill/agent frontmatter.

    Supported:
      key: value           # bare scalar
      key: "value"         # double-quoted scalar
      key: 'value'         # single-quoted scalar
      key: 42              # integer
      key: true / false    # boolean
      key:                 # opens a list
        - item1
        - item2
      # comments at line start

    NOT supported: multiline scalars (>, |), anchors/aliases, nested mappings.
    Lines that don't fit are silently skipped (returned dict has only what
    parses cleanly).
    """
    result: dict[str, Any] = {}
    current_list_key: str | None = None
    for ln in lines:
        stripped = ln.lstrip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(ln) - len(stripped)
        if current_list_key is not None and indent > 0 and stripped.startswith("-"):
            item = stripped[1:].strip()
            result[current_list_key].append(_coerce_scalar(item))
            continue
        current_list_key = None
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_-]*)\s*:\s*(.*)$", stripped)
        if not m:
            continue
        key, rest = m.group(1), m.group(2).rstrip()
        if rest == "":
            result[key] = []
            current_list_key = key
            continue
        result[key] = _coerce_scalar(rest)
    return result


def _coerce_scalar(s: str) -> Any:
    """Coerce a scalar string to bool, int, or str (unquoting quoted values)."""
    s = s.strip()
    if not s:
        return ""
    if (s.startswith('"') and s.endswith('"')) or (
        s.startswith("'") and s.endswith("'")
    ):
        return s[1:-1]
    if s in ("true", "True"):
        return True
    if s in ("false", "False"):
        return False
    if re.match(r"^-?\d+$", s):
        return int(s)
    return s


# ---------- heading_meta ----------


class HeadingMetaExtractor:
    """Extract name/title/description (and legacy sections) from markdown headings."""

    name = "heading-meta"

    def extract(self, path: Path) -> dict[str, Any]:
        text = path.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()
        # Skip past any YAML frontmatter
        if lines and lines[0].strip() == "---":
            for i, ln in enumerate(lines[1:], start=1):
                if ln.strip() == "---":
                    lines = lines[i + 1 :]
                    break

        result: dict[str, Any] = {}
        h1_idx = None
        for i, ln in enumerate(lines):
            if ln.startswith("# "):
                title = ln[2:].strip()
                # Strip leading "/<name> — " prefix common in skill headings.
                # If no slash prefix, set title only — let the handler derive name
                # from the filesystem (filename or directory) instead of slugifying
                # the H1, which often mismatches the canonical identifier.
                m = re.match(r"^/([a-z0-9-]+)\s*[—–-]\s*(.+)$", title)
                if m:
                    result["name"] = m.group(1)
                    result["title"] = m.group(2)
                else:
                    result["title"] = title
                h1_idx = i
                break

        if h1_idx is not None:
            # First non-empty paragraph after H1
            desc_lines: list[str] = []
            i = h1_idx + 1
            while i < len(lines) and not lines[i].strip():
                i += 1
            while i < len(lines) and lines[i].strip() and not lines[i].startswith("#"):
                desc_lines.append(lines[i].strip())
                i += 1
            if desc_lines:
                result["description"] = " ".join(desc_lines)

        # ## Configuration → key: value lines until next H2
        in_config = False
        for ln in lines:
            stripped = ln.strip()
            if stripped == "## Configuration":
                in_config = True
                continue
            if in_config:
                if stripped.startswith("## "):
                    break
                if not stripped or stripped.startswith("#"):
                    continue
                m = re.match(r"^([A-Za-z_][A-Za-z0-9_-]*)\s*:\s*(.*)$", stripped)
                if m:
                    result[m.group(1)] = _coerce_scalar(m.group(2))

        # Known single-value sections for legacy agent format: ## Model, ## Tools
        KNOWN_SECTIONS = {"Model": "model", "Tools": "tools"}
        for section_name, field_name in KNOWN_SECTIONS.items():
            if field_name in result:
                continue
            target = f"## {section_name}"
            for i, ln in enumerate(lines):
                if ln.strip() == target:
                    # Capture the first non-empty line that follows, before the next H2
                    for j in range(i + 1, len(lines)):
                        content = lines[j].strip()
                        if content.startswith("## "):
                            break
                        if content:
                            result[field_name] = content
                            break
                    break

        return result


# ---------- bash_header ----------


class BashHeaderExtractor:
    """Extract name/description from `# Script:` / `# Purpose:` header comments."""

    name = "bash-header"

    def extract(self, path: Path) -> dict[str, Any]:
        text = path.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()[:10]
        result: dict[str, Any] = {}
        for ln in lines:
            m = re.match(r"^#\s*Script:\s*(.+)$", ln)
            if m:
                result["name"] = m.group(1).strip()
            m = re.match(r"^#\s*Purpose:\s*(.+)$", ln)
            if m:
                result["description"] = m.group(1).strip()
        return result


# ---------- py_docstring ----------


class PyDocstringExtractor:
    """Extract title/description from a Python module's first docstring."""

    name = "py-docstring"

    def extract(self, path: Path) -> dict[str, Any]:
        text = path.read_text(encoding="utf-8", errors="replace")
        # Skip shebang and imports/blank/comment lines until first """
        m = re.search(r'^"""(.*?)"""', text, re.MULTILINE | re.DOTALL)
        if not m:
            return {}
        body = m.group(1).strip()
        if not body:
            return {}
        first, _, rest = body.partition("\n")
        result: dict[str, Any] = {"title": first.strip()}
        if rest.strip():
            # First paragraph of the rest as description
            paragraph: list[str] = []
            for ln in rest.split("\n"):
                if not ln.strip():
                    if paragraph:
                        break
                    continue
                paragraph.append(ln.strip())
            if paragraph:
                result["description"] = " ".join(paragraph)
        elif first:
            result["description"] = first.strip()
        return result


# ---------- h1_and_paragraph ----------


class H1AndParagraphExtractor:
    """Extract name/title/description from a plain H1 and its first paragraph."""

    name = "h1-and-paragraph"

    def extract(self, path: Path) -> dict[str, Any]:
        text = path.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()
        # Skip frontmatter
        if lines and lines[0].strip() == "---":
            for i, ln in enumerate(lines[1:], start=1):
                if ln.strip() == "---":
                    lines = lines[i + 1 :]
                    break
        result: dict[str, Any] = {}
        for i, ln in enumerate(lines):
            if ln.startswith("# "):
                title = ln[2:].strip()
                result["name"] = slugify(title)
                result["title"] = title
                # First paragraph
                desc: list[str] = []
                j = i + 1
                while j < len(lines) and not lines[j].strip():
                    j += 1
                while (
                    j < len(lines) and lines[j].strip() and not lines[j].startswith("#")
                ):
                    desc.append(lines[j].strip())
                    j += 1
                if desc:
                    result["description"] = " ".join(desc)
                break
        return result


# ---------- registry ----------

EXTRACTORS: dict[str, Extractor] = {
    "yaml-frontmatter": YamlFrontmatterExtractor(),
    "heading-meta": HeadingMetaExtractor(),
    "bash-header": BashHeaderExtractor(),
    "py-docstring": PyDocstringExtractor(),
    "h1-and-paragraph": H1AndParagraphExtractor(),
}


def get_chain(names: list[str]) -> list[Extractor]:
    """Resolve extractor names to instances, silently skipping unknown names."""
    return [EXTRACTORS[n] for n in names if n in EXTRACTORS]

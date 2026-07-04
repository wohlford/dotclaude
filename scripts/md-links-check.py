#!/usr/bin/env python3
# Script: md-links-check.py
# Purpose: PostToolUse hook — verify relative links and anchors in edited markdown resolve
"""PostToolUse hook — verify relative links and anchors in edited markdown resolve.

Called by Claude Code hooks with the tool-call JSON on stdin. When the edited file is
markdown, every inline link, image, and reference definition is resolved against the
file's directory; missing targets and unresolvable #fragments (same-file and cross-file,
GitHub-style slugs) are reported on exit 2 so Claude fixes them. External URLs are never
fetched.

False-positive control drives the design (this is a global, always-on hook): fenced,
indented, and inline code are masked/skipped before extraction; YAML frontmatter is
stripped; targets markdown itself would treat as literal text (raw whitespace outside <>)
are skipped; bracket-paren prose like element[i](j) is rejected by a lookbehind, and a
"[Note]: prose" callout is not judged a ref-def unless its target is path-shaped; files
under plans/ or specs/ are skipped (design docs link to files that do not exist yet);
fragments match case-insensitively and HTML id=/name= anchors are honored; and any
internal error fails open (exit 0) — exit 2 fires only on a verified broken link.
Known leniencies (false negatives, by choice): links indented 4+ spaces (nested lists)
and destinations containing parentheses are not checked.
"""

import json
import re
import sys
import urllib.parse
from pathlib import Path

SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
ATX_RE = re.compile(r"^ {0,3}#{1,6}\s+(.+?)(?:\s+#+\s*)?$")
SETEXT_RE = re.compile(r"^ {0,3}(=+|-+)\s*$")
NOT_SETEXT_TEXT_RE = re.compile(r"^ {0,3}([#>*+-]|\d+[.)])")
CODE_SPAN_RE = re.compile(r"(`+)(.+?)\1")
INDENTED_CODE_RE = re.compile(r"^(?: {4}|\t)")
INLINE_LINK_RE = re.compile(r"(?<![\w\])])!?\[[^\]]*\]\(([^()]*)\)")
REF_DEF_RE = re.compile(r"^ {0,3}\[[^\]]+\]:\s+(\S+)")
PATH_SHAPED_RE = re.compile(r"[./#]")
TITLE_SPLIT_RE = re.compile(r"""^(\S+)\s+["'(]""")
HEADING_LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")
HTML_ANCHOR_RE = re.compile(r"""(?:id|name)\s*=\s*["']([^"']+)["']""", re.IGNORECASE)
KEEP_RE = re.compile(r"[^\w\- ]")


def read_file_path() -> str | None:
    """Parse the edited file's path from the tool-call JSON on stdin."""
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    file_path = (data.get("tool_input") or {}).get("file_path")
    if isinstance(file_path, str) and file_path:
        return file_path
    return None


def strip_frontmatter(lines: list[str]) -> list[str]:
    """Blank out a leading YAML frontmatter block, preserving line numbering."""
    if lines and lines[0].strip() == "---":
        for i in range(1, len(lines)):
            if lines[i].strip() in ("---", "..."):
                return [""] * (i + 1) + lines[i + 1 :]
    return lines


def mask_fences(lines: list[str]) -> list[str]:
    """Blank out fenced code blocks, preserving line numbering."""
    out = []
    fence = None  # (char, min_length) of the open fence
    for line in lines:
        if fence is None:
            m = FENCE_RE.match(line)
            if m:
                fence = (m.group(1)[0], len(m.group(1)))
                out.append("")
            else:
                out.append(line)
        else:
            stripped = line.strip()
            if (
                stripped
                and stripped == fence[0] * len(stripped)
                and len(stripped) >= fence[1]
            ):
                fence = None
            out.append("")
    return out


def slugify(title: str) -> str:
    """GitHub-style heading slug: drop formatting, keep word chars/-/_, spaces->hyphens."""
    t = HEADING_LINK_RE.sub(r"\1", title)  # keep link text, drop the URL
    t = t.replace("`", "").lower()
    t = KEEP_RE.sub("", t)
    return t.replace(" ", "-")


def collect_anchors(text: str) -> set[str]:
    """All anchors a fragment may point at: heading slugs (deduped) + HTML id/name."""
    lines = mask_fences(strip_frontmatter(text.splitlines()))
    counts: dict[str, int] = {}
    anchors = set()

    def add(title: str) -> None:
        slug = slugify(title.strip())
        n = counts.get(slug, 0)
        counts[slug] = n + 1
        anchors.add(slug if n == 0 else f"{slug}-{n}")

    prev = ""
    for line in lines:
        m = ATX_RE.match(line)
        if m:
            add(m.group(1))
            prev = ""
            continue
        if (
            SETEXT_RE.match(line)
            and prev.strip()
            and not NOT_SETEXT_TEXT_RE.match(prev)
        ):
            add(prev)
            prev = ""
            continue
        prev = line
    for m in HTML_ANCHOR_RE.finditer(text):
        anchors.add(m.group(1).lower())
    return anchors


def clean_target(raw: str) -> str | None:
    """Normalize a raw link destination; None means 'skip, do not judge'."""
    t = raw.strip()
    if not t:
        return None
    if t.startswith("<"):
        end = t.find(">")
        return t[1:end] if end != -1 else None
    m = TITLE_SPLIT_RE.match(t)
    if m:
        t = m.group(1)
    if re.search(r"\s", t):
        return None  # raw whitespace outside <> is not a CommonMark destination
    return t


def extract_links(lines: list[str]) -> list[tuple[int, str]]:
    """Yield (line_number, raw_destination) for links, images, and ref definitions."""
    links = []
    for i, line in enumerate(lines, 1):
        if INDENTED_CODE_RE.match(line):
            continue  # indented code (or deep list nesting): lenient skip, never flag
        text = CODE_SPAN_RE.sub(lambda m: " " * len(m.group(0)), line)
        m = REF_DEF_RE.match(text)
        if m:
            # "[Note]: This callout…" is prose, not a ref-def — require a
            # path-shaped target (contains . / or #) before judging it.
            if PATH_SHAPED_RE.search(m.group(1)):
                links.append((i, m.group(1)))
            continue
        for m in INLINE_LINK_RE.finditer(text):
            links.append((i, m.group(1)))
    return links


def check(file_path: str) -> list[str]:
    """Check every relative link and anchor in file_path; return error strings."""
    with open(file_path, encoding="utf-8", errors="replace") as f:
        src = f.read()
    lines = mask_fences(strip_frontmatter(src.splitlines()))
    abs_file = Path(file_path).resolve()
    base = abs_file.parent
    anchor_cache: dict[Path, set[str]] = {}
    errors = []

    def anchors_of(path: Path, text: str | None = None) -> set[str]:
        if path not in anchor_cache:
            if text is None:
                with open(path, encoding="utf-8", errors="replace") as f:
                    text = f.read()
            anchor_cache[path] = collect_anchors(text)
        return anchor_cache[path]

    for lineno, raw in extract_links(lines):
        target = clean_target(raw)
        if target is None:
            continue
        if SCHEME_RE.match(target) or target.startswith("//") or target.startswith("/"):
            continue
        path, _, frag = target.partition("#")
        path = urllib.parse.unquote(path)
        frag = urllib.parse.unquote(frag).lower() if frag else None
        if not path:
            if frag and frag not in anchors_of(abs_file, src):
                errors.append(f"line {lineno}: (#{frag}) — anchor not found")
            continue
        resolved = (base / path).resolve()
        if not resolved.exists():
            errors.append(f"line {lineno}: ({raw}) — file not found: {resolved}")
            continue
        if frag and resolved.suffix.lower() == ".md" and resolved.is_file():
            if frag not in anchors_of(resolved):
                errors.append(
                    f"line {lineno}: ({raw}) — anchor '#{frag}' not found in {resolved}"
                )
    return errors


def main() -> int:
    """Hook entry point: 0 allows the edit; 2 blocks with errors on stderr."""
    file_path = read_file_path()
    if not file_path or not file_path.lower().endswith(".md"):
        return 0
    abs_file = Path(file_path).resolve()
    if not abs_file.is_file():
        return 0
    parts = abs_file.parts[:-1]
    if "plans" in parts or "specs" in parts:
        return 0  # design docs legitimately link to files that do not exist yet
    errors = check(file_path)
    if errors:
        print(f"md-links-check FAILED for {file_path}:", file=sys.stderr)
        for e in errors:
            print(f"  {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit(0)  # fail open: a checker bug must never block edits

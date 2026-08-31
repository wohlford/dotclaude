#!/usr/bin/env python3
# Script: script-header-check.py
# Purpose: Verify every scripts/ file carries a Purpose header sync-docs' extractor can read whole
# Usage: script-header-check.py --scope <repo>
"""Fail-closed gate over the population sync-docs indexes as `scripts/README.md`.

`BashHeaderExtractor` (skills/sync-docs/extractors.py) reads only the first
`HEADER_WINDOW_LINES` lines of a script and binds `description` to the LAST `# Purpose:` match
in that window, taking only its own physical line — a continuation on the next line is silently
dropped. Three ways that produces a wrong or empty index row:

* `missing` — no `# Purpose:` line anywhere in the file.
* `out-of-window` — one exists, but only past line `HEADER_WINDOW_LINES`.
* `wrapped` — the bound header line is itself continued by the next line, so the rendered
  Purpose cell is silently truncated (measured live: two committed rows today).

This is a precondition on the extractor's INPUT, not a change to sync-docs itself — nothing here
touches `sync_docs.py`'s rc contract. Population and window are both DERIVED from sync-docs's own
code (`ScriptsHandler().discover()`, `extractors.HEADER_WINDOW_LINES`) rather than hand-copied, so
this cannot drift out of step with what the extractor actually does.

`wrapped` is deliberately fire-narrow / clear-generous, and its residual is accepted rather than
narrowed (see `_is_wrapped`): any un-separated LOWERCASE comment directly below the bound header
is treated as a continuation, including a standalone idiom that merely happens to start lowercase
(`# requires GNU coreutils`, `# see README.md for details`). The boundary must be explicit — a
bare `#` separator line clears it — because an un-separated lowercase comment sitting directly
under a Purpose line is genuinely ambiguous to a human reader too, not only to this checker.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# A `# Purpose:` line, matched exactly as BashHeaderExtractor matches it.
PURPOSE_RE = re.compile(r"^#\s*Purpose:\s*(.+)$")

# A continuation starting with one of these (no colon) is a known tool directive, not prose —
# clears rule 5 below. Directives that DO carry a colon (noqa:, type:, pylint:, mypy: — each
# written as `# <name>:`) are already cleared by rule 4's `Key:` pattern and need no entry here.
TOOL_DIRECTIVES_NO_COLON = ("shellcheck", "nolint", "pragma", "fmt")

REMEDY = {
    "missing": (
        "add a `# Purpose:` header (if one already exists, a leading BOM may be hiding it "
        "from this checker and from the extractor alike)"
    ),
    "wrapped": (
        "make line 1 self-contained, or insert a bare `#` separator line before the next "
        "line if it is unrelated elaboration rather than a continuation"
    ),
}


def _find_last_match_index(lines: list[str], limit: int) -> int | None:
    """Index of the LAST `# Purpose:` match within lines[:limit], or None.

    Mirrors BashHeaderExtractor.extract's loop-and-overwrite exactly: it walks the window
    top to bottom and the final match wins, so the subject line is the last in-window match,
    never the first.
    """
    idx = None
    for i, ln in enumerate(lines[:limit]):
        if PURPOSE_RE.match(ln):
            idx = i
    return idx


def _is_wrapped(lines: list[str], i: int) -> bool:
    """Whether the Purpose line at index i is continued by line i+1.

    Fail-closed gate: fire NARROWLY, clear GENEROUSLY. Each clause below clears a shape a
    false positive could take. What reaches the final `return True` is any un-separated
    lowercase comment — a genuine continuation, and also a standalone lowercase idiom. See
    the module docstring for why that residual is accepted rather than narrowed away.
    """
    if i + 1 >= len(lines):
        return False  # 1: no next line — a Purpose line at EOF is clean, never an index error
    s = lines[i + 1].lstrip()
    if not s.startswith("#"):
        return False  # 2: next line isn't even a comment
    body = s[1:].lstrip()
    if not body:
        return False  # 3: bare `#` — the deliberate separator
    if re.match(r"^[A-Za-z][A-Za-z0-9 _-]*:", body):
        return (
            False  # 4: a `Key:` header (also clears noqa:, type:, pylint:, and similar)
        )
    if body.startswith(TOOL_DIRECTIVES_NO_COLON):
        return False  # 5: a known tool directive with no colon
    if not ("a" <= body[0] <= "z"):
        return False  # 6: not a lowercase ASCII letter — clears `-*- coding:`, dividers, `Cap...`
    return True  # 7: otherwise, wrapped


def check_file(path: Path, window: int) -> str | None:
    """Return a violation reason for `path`, or None if clean."""
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    idx = _find_last_match_index(lines, window)
    if idx is None:
        # A file with no in-window match. Scan the rest of the file ONLY to choose wording
        # (missing vs. out-of-window) — never to create a violation an in-window match would
        # have cleared; that branch already returned above.
        found_later = any(PURPOSE_RE.match(ln) for ln in lines[window:])
        return "out-of-window" if found_later else "missing"
    return "wrapped" if _is_wrapped(lines, idx) else None


def _remedy(reason: str, window: int) -> str:
    if reason == "out-of-window":
        return (
            f"a `# Purpose:` line exists only past line {window} — move it into the "
            f"first {window} lines if it is real, or add a header there if that later "
            "line is template text (e.g. inside a heredoc) rather than a genuine header"
        )
    return REMEDY[reason]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scope", required=True, help="repo root holding scripts/")
    args = parser.parse_args(argv)
    scope = Path(args.scope).resolve()

    sync_docs_dir = scope / "skills" / "sync-docs"
    sys.path.insert(0, str(sync_docs_dir))
    try:
        import extractors  # type: ignore[import-not-found]
        import handlers  # type: ignore[import-not-found]
        import sync_docs  # type: ignore[import-not-found]
    except ImportError as exc:
        # An instrument failure, not a finding: exit 2, never the violations-found rc of 1.
        sys.stderr.write(
            f"script-header-check: cannot import sync-docs from {sync_docs_dir}: {exc}\n"
        )
        return 2

    window = extractors.HEADER_WINDOW_LINES

    handlers.set_project_config(sync_docs.load_project_config(scope))
    sources = handlers.ScriptsHandler().discover(scope, scope / "scripts", {})
    if not sources:
        # Zero-denominator guard: an empty population is an instrument failure (wrong scope,
        # missing scripts/), never a silent clean pass.
        sys.stderr.write(
            f"script-header-check: discovered no scripts under {scope} — the population is "
            "empty, which is an instrument failure rather than a clean subject\n"
        )
        return 2

    violations: list[tuple[Path, str]] = []
    for s in sorted(sources, key=lambda src: src.path):
        try:
            reason = check_file(s.path, window)
        except Exception as exc:  # an ungradeable member must not read as a clean one
            rel = s.path.relative_to(scope)
            sys.stderr.write(
                f"script-header-check: cannot read {rel} ({exc.__class__.__name__}): "
                "an unreadable population member is an instrument failure, not a "
                "finding\n"
            )
            return 2
        if reason:
            violations.append((s.path, reason))

    # Print the summary FIRST: audit.sh's print_offenders caps at 50 lines and the
    # summary is otherwise the last line written, so on a large violation count the cap
    # would eat the summary before ever eating an offender line.
    sys.stdout.write(f"scripts checked={len(sources)} violations={len(violations)}\n")

    for path, reason in violations:
        rel = path.relative_to(scope)
        sys.stdout.write(f"{rel}: {reason} — {_remedy(reason, window)}\n")

    return 1 if violations else 0


if __name__ == "__main__":
    sys.exit(main())

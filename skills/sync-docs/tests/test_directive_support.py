"""Tests for per-handler directive support — an unsupported directive must fail loudly.

Regression cover for a measured fail-open: five of seven handlers accepted
``filter=`` and discarded it, and six of seven did the same to the documented
``limit=``, each time exiting 0 and reporting "no changes (already in sync)".
A directive the author wrote and the handler cannot honor must be an error,
never silence.

The last test is the durable half: it derives what each handler *actually
reads* from the source and compares that to what the handler *declares*, so a
handler that grows a new directive without declaring it fails here rather than
silently re-opening the hole.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

import handlers

SKILL_DIR = Path(__file__).resolve().parent.parent
SCRIPT = SKILL_DIR / "sync_docs.py"
FIXTURES = Path(__file__).resolve().parent / "fixtures"

# Directives consumed by the generic layer (sync_docs.py), legal on every handler.
GENERIC = frozenset({"mode", "lint"})

DIRECTIVE_KEY_RE = re.compile(r"""directives(?:\.get\(|\[)\s*["'](\w[\w-]*)["']""")


def _run(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--scope", str(cwd), *args],
        capture_output=True,
        text=True,
        cwd=str(cwd),
    )


def _repo_with_marker(tmp_path: Path, marker: str, close: str) -> Path:
    """Copy the shaped fixture and replace its marker block with the given one."""
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURES / "dotclaude-shaped", repo, dirs_exist_ok=True)
    readme = repo / "README.md"
    text = readme.read_text()
    text = text.replace("<!-- sync:skills -->", marker)
    text = text.replace("<!-- /sync:skills -->", close)
    readme.write_text(text)
    return repo


def test_filter_on_handler_that_cannot_honor_it_is_an_error(tmp_path):
    """filter= on a handler that does not implement it must not be silently dropped."""
    repo = _repo_with_marker(
        tmp_path,
        "<!-- sync:hooks filter=nosuchfield:nosuchvalue -->",
        "<!-- /sync:hooks -->",
    )
    result = _run(repo)
    combined = result.stdout + result.stderr
    assert result.returncode == 2, (
        f"expected a hard error, got rc={result.returncode}\n{combined}"
    )
    assert "filter" in combined, f"error must name the offending directive:\n{combined}"


def test_documented_limit_on_skills_is_an_error(tmp_path):
    """limit= is documented as general but only IndexHandler implements it."""
    repo = _repo_with_marker(
        tmp_path,
        "<!-- sync:skills limit=2 -->",
        "<!-- /sync:skills -->",
    )
    result = _run(repo)
    combined = result.stdout + result.stderr
    assert result.returncode == 2, (
        f"expected a hard error, got rc={result.returncode}\n{combined}"
    )
    assert "limit" in combined, f"error must name the offending directive:\n{combined}"


def test_supported_directive_still_renders(tmp_path):
    """The guard must not block a directive the handler genuinely implements."""
    repo = _repo_with_marker(
        tmp_path,
        "<!-- sync:skills cols=Command:key,Purpose:auto -->",
        "<!-- /sync:skills -->",
    )
    result = _run(repo)
    combined = result.stdout + result.stderr
    assert result.returncode == 0, f"supported directive must render:\n{combined}"
    assert "`/foo`" in (repo / "README.md").read_text()


def _declared(handler) -> frozenset[str]:
    return frozenset(getattr(handler, "supported", frozenset()))


def _read_by_class() -> dict[str, frozenset[str]]:
    """Derive, per handler class, the directive keys its source actually reads."""
    src = (SKILL_DIR / "handlers.py").read_text()
    names = re.findall(r"^class\s+(\w+)\b", src, re.M)
    bodies = re.split(r"^class\s+\w+\b", src, flags=re.M)[1:]
    return {
        name: frozenset(DIRECTIVE_KEY_RE.findall(body))
        for name, body in zip(names, bodies, strict=True)
    }


def test_every_handler_declares_supported_directives():
    """Each registered handler must declare a non-empty `supported` set."""
    for name, handler in handlers.HANDLERS.items():
        assert hasattr(handler, "supported"), f"{name} does not declare supported"
        assert _declared(handler), f"{name} declares an empty supported set"


def test_reference_doc_matches_the_declared_support():
    """DERIVED, not hand-checked: reference.md's Supported-by column vs the declarations.

    reference.md promising a directive no handler implements is the exact
    defect that shipped `limit` as general when only `index` honored it. The
    doc is the other hand-made copy in this system, so it gets bound too.
    """
    doc = (SKILL_DIR / "reference.md").read_text()
    all_handlers = frozenset(handlers.HANDLERS)

    # Scope to the Common directives table. Other tables in this file also lead
    # with a backticked cell (Built-in handlers), so an unscoped parse silently
    # grades the wrong subject.
    section = re.search(r"^## Common directives$(.*?)^## ", doc, re.M | re.S)
    assert section, "could not locate the Common directives section in reference.md"

    rows = re.findall(
        r"^\|\s*`(\w[\w-]*)`\s*\|[^|]*\|([^|]*)\|", section.group(1), re.M
    )
    documented = {}
    for directive, cell in rows:
        cell = cell.strip()
        if "every handler" in cell:
            documented[directive] = all_handlers
        else:
            named = frozenset(re.findall(r"`(\w[\w-]*)`", cell))
            if named:
                documented[directive] = named

    assert documented, "no Supported-by rows parsed from reference.md"

    for directive, doc_handlers in documented.items():
        if directive in GENERIC:
            assert doc_handlers == all_handlers, (
                f"reference.md says {directive}= is supported by {sorted(doc_handlers)}, "
                f"but it is generic and legal everywhere"
            )
            continue
        actual = frozenset(
            n for n, h in handlers.HANDLERS.items() if directive in _declared(h)
        )
        assert doc_handlers == actual, (
            f"reference.md says {directive}= is supported by {sorted(doc_handlers)}, "
            f"but the handlers declaring it are {sorted(actual)}"
        )


def test_declared_supported_matches_what_the_handler_reads():
    """DERIVED, not hand-copied: declaration must equal the directives actually read.

    Guards the hole from reopening — a handler that starts reading a new
    directive without declaring it, or declares one it never reads, fails here.
    """
    read_by_class = _read_by_class()
    for name, handler in handlers.HANDLERS.items():
        cls = type(handler).__name__
        actually_reads = read_by_class.get(cls, frozenset()) - GENERIC
        declares = _declared(handler) - GENERIC
        assert declares == actually_reads, (
            f"{name} ({cls}) declares {sorted(declares)} "
            f"but its source reads {sorted(actually_reads)}"
        )

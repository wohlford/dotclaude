"""Tests for handlers delegated to external tooling via .claude/sync-docs.yaml.

A repo may own a marker format with its own generator (the court repo's
`sync:index-files` regions are produced by `scripts/index-gen.py`, whose table
cells are LLM-curated and must never be machine-regenerated). Declaring such a
handler `external: true` makes sync-docs leave those blocks alone and report
them, instead of failing with `unknown handler`.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
SCRIPT = SKILL_DIR / "sync_docs.py"

CURATED = (
    "# Index\n"
    "\n"
    '<!-- sync:index-files section="Files" -->\n'
    "| File | Summary |\n"
    "| :--- | :--- |\n"
    "| `a.pdf` | A hand-written interpretive summary |\n"
    "<!-- /sync:index-files -->\n"
)

EXTERNAL_CONFIG = "handlers:\n  index-files:\n    external: true\n"


def _run(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--scope", str(cwd), *args],
        capture_output=True,
        text=True,
        cwd=str(cwd),
    )


def _repo(tmp_path: Path, config: str | None, body: str = CURATED) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "INDEX.md").write_text(body)
    if config is not None:
        cfg_dir = repo / ".claude"
        cfg_dir.mkdir(exist_ok=True)
        (cfg_dir / "sync-docs.yaml").write_text(config)
    return repo


def test_undeclared_unknown_handler_still_errors(tmp_path):
    """Regression guard: silencing must be opt-in per handler name."""
    repo = _repo(tmp_path, config=None)
    result = _run(repo, "sync")
    assert result.returncode == 2
    assert "unknown handler 'sync:index-files'" in result.stderr


def test_external_handler_does_not_error(tmp_path):
    repo = _repo(tmp_path, EXTERNAL_CONFIG)
    result = _run(repo, "sync")
    assert result.returncode == 0, f"stderr: {result.stderr}"
    assert "unknown handler" not in result.stderr


def test_external_handler_leaves_body_untouched(tmp_path):
    """The curated cells survive a full sync run verbatim."""
    repo = _repo(tmp_path, EXTERNAL_CONFIG)
    result = _run(repo, "sync")
    assert result.returncode == 0, f"stderr: {result.stderr}"
    assert (repo / "INDEX.md").read_text() == CURATED


def test_external_handler_is_reported_not_silent(tmp_path):
    """A delegated block is visible in the output, so it can't mask a typo."""
    repo = _repo(tmp_path, EXTERNAL_CONFIG)
    result = _run(repo, "sync")
    out = result.stdout + result.stderr
    assert "delegated" in out.lower()
    assert "index-files" in out


def test_external_handler_reports_block_count(tmp_path):
    """The count reflects blocks, not files — multi-region INDEXes are common."""
    two_regions = (
        "# Index\n"
        "\n"
        '<!-- sync:index-files section="One" -->\n'
        "| File | Summary |\n"
        "| :--- | :--- |\n"
        "| `a.pdf` | first |\n"
        "<!-- /sync:index-files -->\n"
        "\n"
        '<!-- sync:index-files section="Two" -->\n'
        "| File | Summary |\n"
        "| :--- | :--- |\n"
        "| `b.pdf` | second |\n"
        "<!-- /sync:index-files -->\n"
    )
    repo = _repo(tmp_path, EXTERNAL_CONFIG, body=two_regions)
    result = _run(repo, "sync")
    out = result.stdout + result.stderr
    assert "2 block(s) delegated" in out


def test_external_handler_clean_under_check(tmp_path):
    """--check is the /audit path: delegated blocks must not read as drift."""
    repo = _repo(tmp_path, EXTERNAL_CONFIG)
    result = _run(repo, "sync", "--check")
    assert result.returncode == 0, f"stdout: {result.stdout}\nstderr: {result.stderr}"


def test_external_false_falls_through_to_error(tmp_path):
    """Only a truthy external: delegates; the key's presence alone must not."""
    repo = _repo(tmp_path, "handlers:\n  index-files:\n    external: false\n")
    result = _run(repo, "sync")
    assert result.returncode == 2
    assert "unknown handler 'sync:index-files'" in result.stderr


def test_external_takes_precedence_over_source(tmp_path):
    """A declaration carrying both keys delegates rather than routing to custom."""
    repo = _repo(
        tmp_path,
        "handlers:\n"
        "  index-files:\n"
        "    external: true\n"
        "    source: '*.pdf'\n"
        "    cols: File:key\n",
    )
    result = _run(repo, "sync")
    assert result.returncode == 0, f"stderr: {result.stderr}"
    assert (repo / "INDEX.md").read_text() == CURATED


def test_external_declaration_does_not_affect_builtin_handlers(tmp_path):
    """Delegating one name must not disturb a built-in marker in the same repo."""
    repo = _repo(tmp_path, EXTERNAL_CONFIG)
    (repo / "scripts").mkdir()
    (repo / "scripts" / "tool.sh").write_text("#!/bin/bash\n# Purpose: do a thing\n")
    (repo / "README.md").write_text(
        "# Repo\n\n<!-- sync:scripts -->\n<!-- /sync:scripts -->\n"
    )
    result = _run(repo, "sync")
    assert result.returncode == 0, f"stderr: {result.stderr}"
    assert "`tool.sh`" in (repo / "README.md").read_text()
    assert (repo / "INDEX.md").read_text() == CURATED

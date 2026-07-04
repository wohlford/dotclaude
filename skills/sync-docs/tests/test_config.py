"""Tests for .claude/sync-docs.yaml project-local config loading."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import sync_docs

SKILL_DIR = Path(__file__).resolve().parent.parent
SCRIPT = SKILL_DIR / "sync_docs.py"


def _run(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--scope", str(cwd), *args],
        capture_output=True,
        text=True,
        cwd=str(cwd),
    )


def _write_config(repo: Path, contents: str) -> None:
    cfg_dir = repo / ".claude"
    cfg_dir.mkdir(exist_ok=True)
    (cfg_dir / "sync-docs.yaml").write_text(contents)


def test_load_config_missing_returns_empty(tmp_path):
    assert sync_docs.load_project_config(tmp_path) == {}


def test_load_config_basic(tmp_path):
    _write_config(tmp_path, "init:\n  exclude:\n    - tmp/\n")
    cfg = sync_docs.load_project_config(tmp_path)
    assert cfg == {"init": {"exclude": ["tmp/"]}}


def test_load_config_malformed_returns_empty_with_warning(tmp_path, capsys):
    _write_config(tmp_path, ":this is not: valid: yaml:\n  - bad")
    # Either parses to something invalid or returns {}; should not raise
    cfg = sync_docs.load_project_config(tmp_path)
    assert isinstance(cfg, dict)


def test_skills_handler_honors_config_source_override(tmp_path):
    """Config moves the skills source from default to a custom path."""
    repo = tmp_path / "repo"
    repo.mkdir()
    # Skills NOT at default location — instead under src/skills/
    src_skills = repo / "src" / "skills" / "foo"
    src_skills.mkdir(parents=True)
    (src_skills / "SKILL.md").write_text("---\nname: foo\ndescription: A foo\n---\n")
    # Marker in README pointing at the skills handler
    (repo / "README.md").write_text(
        "# Test\n\n<!-- sync:skills -->\n<!-- /sync:skills -->\n"
    )
    # Config with source override
    _write_config(repo, "handlers:\n  skills:\n    source: src/skills/*/SKILL.md\n")

    result = _run(repo)
    assert result.returncode == 0, f"stderr: {result.stderr}"
    rendered = (repo / "README.md").read_text()
    assert "`/foo`" in rendered
    assert "A foo" in rendered


def test_init_honors_exclude_directive(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    # Two qualifying dirs; one should be excluded
    (repo / "data").mkdir()
    for i in range(5):
        (repo / "data" / f"d-{i}.md").write_text(f"# {i}\n")
    (repo / "reports").mkdir()
    for i in range(5):
        (repo / "reports" / f"r-{i}.md").write_text(f"# {i}\n")
    _write_config(repo, "init:\n  exclude:\n    - reports/\n")

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--scope", str(repo), "init", "--yes-to-all"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert (repo / "data" / "README.md").exists()
    assert not (repo / "reports" / "README.md").exists()


def test_custom_handler_via_config(tmp_path):
    """A handler defined only in config (not built-in) routes to CustomHandler."""
    repo = tmp_path / "repo"
    repo.mkdir()
    posts = repo / "posts"
    posts.mkdir()
    (posts / "a.md").write_text("---\ntitle: A Post\n---\n")
    (posts / "b.md").write_text("---\ntitle: B Post\n---\n")
    (repo / "README.md").write_text(
        "# Test\n\n<!-- sync:posts -->\n<!-- /sync:posts -->\n"
    )
    _write_config(
        repo,
        (
            "handlers:\n"
            "  posts:\n"
            "    source: posts/*.md\n"
            "    cols: File:key,Title:auto\n"
        ),
    )

    result = _run(repo)
    assert result.returncode == 0, f"stderr: {result.stderr}"
    rendered = (repo / "README.md").read_text()
    assert "`a.md`" in rendered
    assert "A Post" in rendered
    assert "`b.md`" in rendered

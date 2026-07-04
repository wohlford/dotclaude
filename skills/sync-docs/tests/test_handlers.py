"""Tests for handlers.py — discovery and rendering per handler."""

from __future__ import annotations

import json

import handlers


def _make_skill(root, name, description, category=None):
    d = root / "skills" / name
    d.mkdir(parents=True, exist_ok=True)
    fm = ["---", f"name: {name}", f"description: {description}"]
    if category:
        fm.append(f"category: {category}")
    fm += [
        "disable-model-invocation: true",
        "---",
        "",
        f"# /{name} — {description}",
        "",
    ]
    (d / "SKILL.md").write_text("\n".join(fm))


def _make_agent(root, name, description, model="haiku"):
    d = root / "agents"
    d.mkdir(parents=True, exist_ok=True)
    fm = [
        "---",
        f"name: {name}",
        f"description: {description}",
        f"model: {model}",
        "---",
        "",
        f"You are the {name} agent.",
    ]
    (d / f"{name}.md").write_text("\n".join(fm))


def test_skills_handler_discovers(tmp_path):
    _make_skill(tmp_path, "commit", "Create a commit")
    _make_skill(tmp_path, "review", "Review code")
    h = handlers.SkillsHandler()
    sources = h.discover(tmp_path, tmp_path, {})
    names = sorted(s.fields["name"] for s in sources)
    assert names == ["commit", "review"]


def test_skills_handler_renders_table(tmp_path):
    _make_skill(tmp_path, "commit", "Create a commit")
    h = handlers.SkillsHandler()
    sources = h.discover(tmp_path, tmp_path, {})
    out = h.render(sources, {}, [])
    assert any("`/commit`" in line for line in out)
    assert any("Create a commit" in line for line in out)
    assert out[0].startswith("|")
    assert ":---" in out[1]


def test_skills_handler_filter_by_category(tmp_path):
    _make_skill(tmp_path, "a", "A1", category="extraction")
    _make_skill(tmp_path, "b", "B1", category="pipeline")
    h = handlers.SkillsHandler()
    sources = h.discover(tmp_path, tmp_path, {"filter": "category:extraction"})
    names = [s.fields["name"] for s in sources]
    assert names == ["a"]


def test_skills_handler_rejects_missing_key_col(tmp_path):
    _make_skill(tmp_path, "a", "A1")
    h = handlers.SkillsHandler()
    sources = h.discover(tmp_path, tmp_path, {})
    import pytest

    with pytest.raises(ValueError, match="requires exactly one :key column"):
        h.render(sources, {"cols": "Command:auto,Purpose:auto"}, [])


def test_agents_handler_legacy_heading_meta_format(tmp_path):
    """Agents using ## Model/## Tools sections (no YAML frontmatter) — name from filename."""
    d = tmp_path / "agents"
    d.mkdir()
    (d / "reviewer.md").write_text(
        "# Reviewer Agent\n\n"
        "Reviews code for style.\n\n"
        "## Model\n\nhaiku\n\n"
        "## Tools\n\nRead, Grep\n"
    )
    h = handlers.AgentsHandler()
    sources = h.discover(tmp_path, tmp_path, {})
    assert len(sources) == 1
    s = sources[0]
    # Name comes from filename, not slugified H1
    assert s.fields["name"] == "reviewer"
    assert s.fields["model"] == "haiku"
    assert s.fields["description"] == "Reviews code for style."


def test_skills_handler_falls_back_to_dirname(tmp_path):
    """Skill SKILL.md with no slash-prefix H1 — name from directory."""
    d = tmp_path / "skills" / "my-skill"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text("# My Skill\n\nDoes things.\n")
    h = handlers.SkillsHandler()
    sources = h.discover(tmp_path, tmp_path, {})
    assert len(sources) == 1
    assert sources[0].fields["name"] == "my-skill"


def test_agents_handler_discovers_excluding_readme(tmp_path):
    _make_agent(tmp_path, "reviewer", "Reviews stuff")
    (tmp_path / "agents" / "README.md").write_text("# Agents\n\nDocs.\n")
    h = handlers.AgentsHandler()
    sources = h.discover(tmp_path, tmp_path, {})
    names = [s.fields["name"] for s in sources]
    assert names == ["reviewer"]


def test_agents_handler_hybrid_columns_preserve_manual(tmp_path):
    _make_agent(tmp_path, "code-reviewer", "Reviews changes for style")
    _make_agent(tmp_path, "security-auditor", "Flags security issues")
    existing_body = [
        "| Agent              | Used by    | Purpose            |",
        "| :----------------- | :--------- | :----------------- |",
        "| `code-reviewer`    | `/review`  | Old purpose        |",
        "| `security-auditor` | `/audit`   | Old auditor        |",
    ]
    h = handlers.AgentsHandler()
    sources = h.discover(tmp_path, tmp_path, {})
    out = h.render(
        sources,
        {"cols": "Agent:key,Used by:manual,Purpose:auto"},
        existing_body,
    )
    joined = "\n".join(out)
    # Used by column preserved from existing body
    assert "`/review`" in joined
    assert "`/audit`" in joined
    # Purpose column regenerated from sources
    assert "Reviews changes for style" in joined
    assert "Flags security issues" in joined
    assert "Old purpose" not in joined


def test_agents_handler_hybrid_key_rename_drops_manual(tmp_path):
    """Documented destructive behavior: renaming the key value loses manual data
    attached to the old key (rename = different identity)."""
    _make_agent(tmp_path, "reviewer-renamed", "Reviews stuff")
    existing_body = [
        "| Agent              | Used by    | Purpose      |",
        "| :----------------- | :--------- | :----------- |",
        "| `reviewer-old`     | `/old`     | Old purpose  |",
    ]
    h = handlers.AgentsHandler()
    sources = h.discover(tmp_path, tmp_path, {})
    out = h.render(
        sources,
        {"cols": "Agent:key,Used by:manual,Purpose:auto"},
        existing_body,
    )
    joined = "\n".join(out)
    # New key present
    assert "`reviewer-renamed`" in joined
    # Old key gone
    assert "`reviewer-old`" not in joined
    # Manual data attached to old key NOT preserved (rename is destructive)
    assert "`/old`" not in joined


def test_plugins_handler(tmp_path):
    (tmp_path / "settings.json").write_text(
        json.dumps(
            {
                "enabledPlugins": {
                    "foo@vendor": True,
                    "bar@vendor": True,
                    "disabled@vendor": False,
                }
            }
        )
    )
    h = handlers.PluginsHandler()
    sources = h.discover(tmp_path, tmp_path, {})
    names = sorted(s.fields["name"] for s in sources)
    assert names == ["bar", "foo"]


def test_hooks_handler(tmp_path):
    (tmp_path / "settings.json").write_text(
        json.dumps(
            {
                "hooks": {
                    "PostToolUse": [
                        {
                            "matcher": "Edit|Write",
                            "hooks": [
                                {"type": "command", "command": "scripts/style-check.sh"}
                            ],
                        }
                    ]
                }
            }
        )
    )
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "style-check.sh").write_text(
        "#!/usr/bin/env bash\n"
        "# Script: style-check.sh\n"
        "# Purpose: Validate file format\n"
    )
    h = handlers.HooksHandler()
    sources = h.discover(tmp_path, tmp_path, {})
    assert len(sources) == 1
    assert sources[0].fields["event"] == "PostToolUse"
    assert sources[0].fields["matcher"] == "Edit|Write"
    assert sources[0].fields["description"] == "Validate file format"


def test_hooks_two_same_event_dont_collapse(tmp_path):
    """Two hooks under the same event must render as two rows. Regression: the
    default key was Event (not unique), so by_key collapsed them to one."""
    (tmp_path / "settings.json").write_text(
        json.dumps(
            {
                "hooks": {
                    "PostToolUse": [
                        {
                            "matcher": "Edit|Write",
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": "scripts/style-check.sh",
                                },
                                {
                                    "type": "command",
                                    "command": "scripts/sync-docs-check.sh",
                                },
                            ],
                        }
                    ]
                }
            }
        )
    )
    h = handlers.HooksHandler()
    sources = h.discover(tmp_path, tmp_path, {})
    assert len(sources) == 2
    body = h.render(sources, {}, [])
    joined = "\n".join(body)
    assert "`style-check.sh`" in joined
    assert "`sync-docs-check.sh`" in joined
    # Re-render with the prior output as existing body: both rows survive.
    body2 = h.render(sources, {}, body)
    assert body2 == body, "hooks table must be idempotent under preserve-manual"


def test_hooks_pretooluse_renders(tmp_path):
    """PreToolUse hooks render too — the handler is event-generic, taking the
    event straight from the settings.json key."""
    (tmp_path / "settings.json").write_text(
        json.dumps(
            {
                "hooks": {
                    "PreToolUse": [
                        {
                            "matcher": "Bash",
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": "scripts/example-hook.sh",
                                }
                            ],
                        }
                    ]
                }
            }
        )
    )
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "example-hook.sh").write_text(
        "#!/usr/bin/env bash\n"
        "# Script: example-hook.sh\n"
        "# Purpose: Example PreToolUse check\n"
    )
    h = handlers.HooksHandler()
    sources = h.discover(tmp_path, tmp_path, {})
    assert len(sources) == 1
    assert sources[0].fields["event"] == "PreToolUse"
    assert sources[0].fields["matcher"] == "Bash"
    assert sources[0].fields["description"] == "Example PreToolUse check"


def test_scripts_handler(tmp_path):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "a.sh").write_text(
        "#!/usr/bin/env bash\n# Script: a.sh\n# Purpose: Do A\n"
    )
    (scripts / "b.py").write_text('#!/usr/bin/env python3\n"""Do B with style."""\n')
    h = handlers.ScriptsHandler()
    sources = h.discover(tmp_path, tmp_path, {})
    out = h.render(sources, {}, [])
    joined = "\n".join(out)
    assert "`a.sh`" in joined
    assert "`b.py`" in joined


def test_index_handler_lists_subdirs(tmp_path):
    for name in ("alpha", "beta", "gamma"):
        (tmp_path / name).mkdir()
    h = handlers.IndexHandler()
    sources = h.discover(tmp_path, tmp_path, {"kind": "dirs"})
    names = sorted(s.fields["name"] for s in sources)
    assert names == ["alpha", "beta", "gamma"]


def test_index_handler_lists_files_with_extension_filter(tmp_path):
    (tmp_path / "a.md").write_text("# A")
    (tmp_path / "b.txt").write_text("B")
    (tmp_path / "c.py").write_text("# c")
    h = handlers.IndexHandler()
    sources = h.discover(tmp_path, tmp_path, {"kind": "files", "extensions": "md,txt"})
    names = sorted(s.path.name for s in sources)
    assert names == ["a.md", "b.txt"]


def test_index_handler_sort_mtime_desc(tmp_path):
    """sort=mtime,desc should order by file mtime (newest first), not alphabetically."""
    import os
    import time

    d = tmp_path / "data"
    d.mkdir()
    # Create files in a specific order with different mtimes
    for name in ("z-old.md", "a-middle.md", "m-newest.md"):
        p = d / name
        p.write_text(f"# {name}\n")
    # Force mtime ordering: z-old oldest, m-newest newest
    os.utime(d / "z-old.md", (time.time() - 300, time.time() - 300))
    os.utime(d / "a-middle.md", (time.time() - 100, time.time() - 100))
    os.utime(d / "m-newest.md", (time.time(), time.time()))
    h = handlers.IndexHandler()
    sources = h.discover(tmp_path, d, {"kind": "files", "sort": "mtime,desc"})
    out = h.render(sources, {"sort": "mtime,desc"}, [])
    joined = "\n".join(out)
    pos_newest = joined.find("m-newest")
    pos_middle = joined.find("a-middle")
    pos_oldest = joined.find("z-old")
    assert pos_newest < pos_middle < pos_oldest, (
        "expected mtime,desc to put newest first"
    )


def test_custom_handler_with_yaml_frontmatter(tmp_path):
    posts = tmp_path / "posts"
    posts.mkdir()
    (posts / "first.md").write_text(
        "---\ntitle: First Post\nauthor: Alice\n---\n# First\n"
    )
    (posts / "second.md").write_text(
        "---\ntitle: Second Post\nauthor: Bob\n---\n# Second\n"
    )
    h = handlers.CustomHandler()
    sources = h.discover(tmp_path, tmp_path, {"source": "posts/*.md"})
    out = h.render(
        sources,
        {"source": "posts/*.md", "cols": "File:key,Title:auto,Author:auto"},
        [],
    )
    joined = "\n".join(out)
    assert "`first.md`" in joined
    assert "First Post" in joined
    assert "Alice" in joined
    assert "`second.md`" in joined
    assert "Bob" in joined


def test_custom_handler_requires_source(tmp_path):
    h = handlers.CustomHandler()
    import pytest

    with pytest.raises(ValueError, match="source="):
        h.discover(tmp_path, tmp_path, {})


def test_custom_handler_requires_cols(tmp_path):
    h = handlers.CustomHandler()
    import pytest

    with pytest.raises(ValueError, match="cols="):
        h.render([], {"source": "x"}, [])


def test_custom_handler_preserves_manual_column(tmp_path):
    posts = tmp_path / "posts"
    posts.mkdir()
    (posts / "a.md").write_text("---\ntitle: A\n---\n")
    (posts / "b.md").write_text("---\ntitle: B\n---\n")
    existing = [
        "| File   | Status      | Title |",
        "| :----- | :---------- | :---- |",
        "| `a.md` | published   | OLD   |",
        "| `b.md` | draft       | OLD   |",
    ]
    h = handlers.CustomHandler()
    sources = h.discover(tmp_path, tmp_path, {"source": "posts/*.md"})
    out = h.render(
        sources,
        {"source": "posts/*.md", "cols": "File:key,Status:manual,Title:auto"},
        existing,
    )
    joined = "\n".join(out)
    assert "published" in joined  # manual preserved
    assert "draft" in joined
    assert " A " in joined or "| A " in joined  # title rebuilt
    assert "OLD" not in joined


def test_index_handler_lint_mode_renders_canonical_body(tmp_path):
    """Lint vs sync semantics live in the dispatcher; the handler always
    renders the canonical body. The dispatcher decides whether to write."""
    (tmp_path / "foo").mkdir()
    existing = ["| Path | Purpose |", "| :--- | :--- |", "| foo  | hand-tuned |"]
    h = handlers.IndexHandler()
    sources = h.discover(tmp_path, tmp_path, {"kind": "dirs", "mode": "lint"})
    out = h.render(sources, {"mode": "lint"}, existing)
    joined = "\n".join(out)
    assert "`foo/`" in joined  # discovered child rendered

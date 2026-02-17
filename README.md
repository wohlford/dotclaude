# dotclaude

Global [Claude Code](https://docs.anthropic.com/en/docs/claude-code) configuration: style standards, development workflows, custom skills, agents, and hooks.

## Installation

Clone into your home directory:

```bash
git clone https://github.com/wohlford/dotclaude.git ~/.claude
```

## What's Included

### Documentation

| File | Purpose |
|------|---------|
| [CLAUDE.md](CLAUDE.md) | Global instructions loaded into every Claude Code session |
| [STYLE.md](STYLE.md) | Code style and formatting standards (Bash, Python, JS, YAML, JSON, Markdown) |
| [templates.md](templates.md) | Starter templates for Bash scripts, Python modules, and JavaScript |
| [workflows.md](workflows.md) | Development workflows (Explore/Plan/Code/Commit, TDD) |

### Skills (Slash Commands)

| Command | Purpose |
|---------|---------|
| `/commit` | Create a signed git commit with automatic semver tagging |
| `/init-bash` | Scaffold a new Bash script from the standard template |
| `/init-python` | Scaffold a new Python module from the standard template |

### Agents

| Agent | Model | Purpose |
|-------|-------|---------|
| `style-reviewer` | haiku | Review code files for STYLE.md compliance |

### Hooks

| Event | Trigger | Script |
|-------|---------|--------|
| PostToolUse | Edit, Write | [`scripts/style-check.sh`](scripts/style-check.sh) — validates file format, syntax, and style on every edit |

### Plugins

| Plugin | Purpose |
|--------|---------|
| `code-review` | Code review pull requests |
| `superpowers` | Enhanced development workflows and skills |
| `pyright-lsp` | Python type checking via Pyright |
| `claude-md-management` | Audit and improve CLAUDE.md files |
| `claude-code-setup` | Recommend Claude Code automations |

Plugins are configured in [`settings.json`](settings.json) and resolved automatically by Claude Code.

## License

[MIT](LICENSE)

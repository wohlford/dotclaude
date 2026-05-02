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

<!-- sync:skills cols=Command:key,Purpose:auto -->
| Command        | Purpose                                                                                 |
| :------------- | :-------------------------------------------------------------------------------------- |
| `/commit`      | Create a signed git commit with automatic semver tagging following STYLE.md conventions |
| `/init-bash`   | Scaffold a new Bash script from the standard template in templates.md                   |
| `/init-python` | Scaffold a new Python module from the standard template in templates.md                 |
| `/init-skill`  | Scaffold a new skill at skills/<name>/SKILL.md following the standard structure         |
| `/sync-docs`   | Regenerate index regions of README.md and CLAUDE.md from authoritative sources          |
<!-- /sync:skills -->

### Agents

<!-- sync:agents cols=Agent:key,Model:auto,Purpose:auto -->
| Agent            | Model | Purpose                                                             |
| :--------------- | :---- | :------------------------------------------------------------------ |
| `style-reviewer` | haiku | Review code files for compliance with the global STYLE.md standards |
<!-- /sync:agents -->

### Hooks

| Event | Trigger | Script |
|-------|---------|--------|
| PostToolUse | Edit, Write | [`scripts/style-check.sh`](scripts/style-check.sh) — validates file format, syntax, and style on every edit |

### Plugins

<!-- sync:plugins cols=Plugin:key,Purpose:manual -->
| Plugin                 | Purpose                                   |
| :--------------------- | :---------------------------------------- |
| `claude-code-setup`    | Recommend Claude Code automations         |
| `claude-md-management` | Audit and improve CLAUDE.md files         |
| `code-review`          | Code review pull requests                 |
| `pyright-lsp`          | Python type checking via Pyright          |
| `superpowers`          | Enhanced development workflows and skills |
<!-- /sync:plugins -->

Plugins are configured in [`settings.json`](settings.json) and resolved automatically by Claude Code.

## License

[MIT](LICENSE)

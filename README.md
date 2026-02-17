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
| `/commit` | Create a git commit following `<type>: <subject>` format |
| `/init-bash` | Scaffold a new Bash script from the standard template |
| `/init-python` | Scaffold a new Python module from the standard template |

### Agents

| Agent | Model | Purpose |
|-------|-------|---------|
| `style-reviewer` | haiku | Review code files for STYLE.md compliance |

## License

[MIT](LICENSE)

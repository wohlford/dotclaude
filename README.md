# dotclaude

Global [Claude Code](https://docs.anthropic.com/en/docs/claude-code) configuration: style standards, development workflows, custom skills, agents, and hooks.

## Installation

This repo is symlinked into `~/.claude` rather than cloned over it, so the checkout stays the source of truth. Clone it anywhere, then run the installer:

```bash
git clone https://github.com/wohlford/dotclaude.git
cd dotclaude
./install.sh
```

`install.sh` symlinks the tracked files and directories (`CLAUDE.md`, `CONTRIBUTING.md`, `STYLE.md`, `templates.md`, `workflows.md`, `README.md`, `LICENSE`, `skills/`, `agents/`, `scripts/`) into `~/.claude`, backing up anything already present. `settings.json` is intentionally **not** linked — Claude Code rewrites it at runtime, so manage it manually. Restart Claude Code after installing (or after pulling updates) to reload the configuration.

Wondering how the pieces fit — what takes effect where, and what owns what? See [ARCHITECTURE.md](ARCHITECTURE.md).

## What's Included

### Documentation

| File | Purpose |
|------|---------|
| [CLAUDE.md](CLAUDE.md) | Global instructions loaded into every Claude Code session |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Commit-message and semantic-versioning conventions for contributors |
| [STYLE.md](STYLE.md) | Code style and formatting standards (Bash, Python, JS, YAML, JSON, Markdown) |
| [templates.md](templates.md) | Starter templates for Bash scripts, Python modules, and JavaScript |
| [workflows.md](workflows.md) | Development workflows — the `/feature` pipeline, with Explore/Plan/Code/Commit and TDD as primitives |
| [ARCHITECTURE.md](ARCHITECTURE.md) | How this repo fits together — enforcement mesh, staging→propagate lifecycle, source-of-truth map |
| TESTING.md | Test layout and conventions (pytest suites, fixture-repo factories, harness style) |

### Skills (Slash Commands)

<!-- sync:skills cols=Command:key,Purpose:auto -->
| Command   | Purpose                                                                                                                  |
| :-------- | :----------------------------------------------------------------------------------------------------------------------- |
| `/commit` | Create a git commit with automatic semver tagging following STYLE.md conventions; signing and identity follow git config |
<!-- /sync:skills -->

### Agents

<!-- sync:agents cols=Agent:key,Model:auto,Purpose:auto -->
| Agent | Model | Purpose |
| :---- | :---- | :------ |
<!-- /sync:agents -->

### Hooks

<!-- sync:hooks -->
| Event | Matcher | Script | Purpose |
| :---- | :------ | :----- | :------ |
<!-- /sync:hooks -->

### Plugins

<!-- sync:plugins cols=Plugin:key,Purpose:manual -->
| Plugin                 | Purpose |
| :--------------------- | :------ |
| `claude-code-setup`    |         |
| `claude-md-management` |         |
| `code-review`          |         |
| `pyright-lsp`          |         |
| `superpowers`          |         |
<!-- /sync:plugins -->

Plugins are configured in [`settings.json`](settings.json) and resolved automatically by Claude Code.

## License

[MIT](LICENSE)

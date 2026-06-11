# dotclaude

Global [Claude Code](https://docs.anthropic.com/en/docs/claude-code) configuration: style standards, development workflows, custom skills, agents, and hooks.

## Installation

This repo is symlinked into `~/.claude` rather than cloned over it, so the checkout stays the source of truth. Clone it anywhere, then run the installer:

```bash
git clone https://github.com/wohlford/dotclaude.git
cd dotclaude
./install.sh
```

`install.sh` symlinks the tracked files and directories (`CLAUDE.md`, `STYLE.md`, `templates.md`, `workflows.md`, `skills/`, `agents/`, `scripts/`) into `~/.claude`, backing up anything already present. `settings.json` is intentionally **not** linked — Claude Code rewrites it at runtime, so manage it manually. Restart Claude Code after installing (or after pulling updates) to reload the configuration.

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
| Command          | Purpose                                                                                                                                            |
| :--------------- | :------------------------------------------------------------------------------------------------------------------------------------------------- |
| `/commit`        | Create a git commit with automatic semver tagging following STYLE.md conventions; signing and identity follow git config                           |
| `/debrief`       | Run the end-of-session pre-compaction routine (CLAUDE.md refresh, memory save, automation review)                                                  |
| `/feature`       | Run the methodical, risk-tiered design pipeline for a change (triage → spec → spike → plan → reviews) and stop at a reviewed plan ready to execute |
| `/init-bash`     | Scaffold a new Bash script from the standard template in templates.md                                                                              |
| `/init-python`   | Scaffold a new Python module from the standard template in templates.md                                                                            |
| `/init-skill`    | Scaffold a new skill at skills/<name>/SKILL.md following the standard structure                                                                    |
| `/propagate`     | Propagate committed changes from this working copy to the live ~/.claude repo (push, then fast-forward)                                            |
| `/release-notes` | Generate grouped Markdown release notes from git history between two tags or refs                                                                  |
| `/sync-docs`     | Regenerate index regions of README.md and CLAUDE.md from authoritative sources                                                                     |
<!-- /sync:skills -->

### Agents

<!-- sync:agents cols=Agent:key,Model:auto,Purpose:auto -->
| Agent                    | Model  | Purpose                                                                                                     |
| :----------------------- | :----- | :---------------------------------------------------------------------------------------------------------- |
| `skill-content-reviewer` | sonnet | Review SKILL.md files for prose and content quality — clarity, completeness, consistency, and actionability |
| `skill-reviewer`         | haiku  | Review SKILL.md files for compliance with the repo's canonical skill structure                              |
| `style-reviewer`         | haiku  | Review code files for compliance with the global STYLE.md standards                                         |
<!-- /sync:agents -->

### Hooks

<!-- sync:hooks -->
| Event       | Matcher       | Script               | Purpose                                                                     |
| :---------- | :------------ | :------------------- | :-------------------------------------------------------------------------- |
| PostToolUse | `Edit\|Write` | `style-check.sh`     | Global PostToolUse hook — validate file edits against STYLE.md              |
| PostToolUse | `Edit\|Write` | `sync-docs-check.sh` | PostToolUse hook — warn when an edit leaves /sync-docs index tables drifted |
<!-- /sync:hooks -->

### Plugins

<!-- sync:plugins cols=Plugin:key,Purpose:manual -->
| Plugin                 | Purpose                                                                   |
| :--------------------- | :------------------------------------------------------------------------ |
| `claude-code-setup`    | Recommend Claude Code automations                                         |
| `claude-md-management` | Audit and improve CLAUDE.md files                                         |
| `code-review`          | Code review pull requests (`/review`, `/security-review`, `/ultrareview`) |
| `pyright-lsp`          | Python type checking via Pyright                                          |
| `superpowers`          | Enhanced development workflows and skills                                 |
<!-- /sync:plugins -->

Plugins are configured in [`settings.json`](settings.json) and resolved automatically by Claude Code.

## License

[MIT](LICENSE)

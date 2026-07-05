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
| [TESTING.md](TESTING.md) | Test layout and conventions (pytest suites, fixture-repo factories, harness style) |

### Skills (Slash Commands)

<!-- sync:skills cols=Command:key,Purpose:auto -->
| Command               | Purpose                                                                                                                                                                                                    |
| :-------------------- | :--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `/audit`              | Run the mechanical compliance sweep — linters, format, link, exec-bit, and config-validity checks over a repo's tracked files; repos exclude generated paths via .auditignore                              |
| `/commit`             | Create a git commit with automatic semver tagging following STYLE.md conventions; signing and identity follow git config                                                                                   |
| `/debrief`            | Run the end-of-session pre-compaction routine (CLAUDE.md refresh, memory save, automation review and implementation)                                                                                       |
| `/feature`            | Run the methodical, risk-tiered pipeline for a change (triage → spec → spike → plan → reviews), then continue through subagent-driven execution to a merged change; --plan-only stops at the reviewed plan |
| `/idempotency-tester` | Verify a script is idempotent by running it twice in an isolated sandbox and diffing the resulting state                                                                                                   |
| `/init-bash`          | Scaffold a new Bash script from the standard template in templates.md                                                                                                                                      |
| `/init-js`            | Scaffold a new JavaScript module from the standard template in templates.md                                                                                                                                |
| `/init-python`        | Scaffold a new Python module from the standard template in templates.md                                                                                                                                    |
| `/init-skill`         | Scaffold a new skill at skills/<name>/SKILL.md following the standard structure                                                                                                                            |
| `/propagate`          | Promote committed changes from this dev working copy to the live ~/.claude repo locally; --push also publishes to origin (explicit)                                                                        |
| `/recast`             | Re-develop a git source repo into a target as a genuine ground-up, proven-per-commit history converging to functional equivalence (never copies the tree, never pushes)                                    |
| `/sync-docs`          | Regenerate index regions of README.md and CLAUDE.md from authoritative sources                                                                                                                             |
| `/vet`                | Vet an authored skill, agent, or script — or the whole repo with --all — by dispatching the matching reviewer agent(s) and reporting their findings                                                        |
<!-- /sync:skills -->

### Agents

<!-- sync:agents cols=Agent:key,Model:auto,Purpose:auto -->
| Agent                    | Model  | Purpose                                                                                                     |
| :----------------------- | :----- | :---------------------------------------------------------------------------------------------------------- |
| `agent-reviewer`         | haiku  | Review agent definition files for compliance with the canonical agent frontmatter and structure             |
| `skill-content-reviewer` | sonnet | Review SKILL.md files for prose and content quality — clarity, completeness, consistency, and actionability |
| `skill-reviewer`         | haiku  | Review SKILL.md files for compliance with the repo's canonical skill structure                              |
| `style-reviewer`         | haiku  | Review code files for compliance with the global STYLE.md standards                                         |
<!-- /sync:agents -->

### Hooks

<!-- sync:hooks -->
| Event       | Matcher                              | Script                       | Purpose                                                                                                                    |
| :---------- | :----------------------------------- | :--------------------------- | :------------------------------------------------------------------------------------------------------------------------- |
| PreToolUse  | `Read\|Edit\|Write\|MultiEdit\|Grep` | `guard-secrets.sh`           | Global PreToolUse hook — deny reading/editing secret files (.env*, keys, pem)                                              |
| PreToolUse  | `Bash`                               | `push-guard.sh`              | PreToolUse hook — block `git push` unless the push segment leads with an ALLOW_PUSH=1 override                             |
| PreToolUse  | `Bash`                               | `exec-bit-guard.sh`          | PreToolUse hook — block `git commit` when it would record a new shebang file without the exec bit (or a 755→644 downgrade) |
| PreToolUse  | `Bash`                               | `recast-commit-gate.py`      | PreToolUse hook — run the recast suite before a commit that touches recast source                                          |
| PostToolUse | `Edit\|Write`                        | `style-check.sh`             | Global PostToolUse hook — validate file edits against STYLE.md                                                             |
| PostToolUse | `Edit\|Write`                        | `shellcheck-check.sh`        | PostToolUse hook — run shellcheck on edited shell scripts                                                                  |
| PostToolUse | `Edit\|Write`                        | `ruff-check.sh`              | PostToolUse hook — run ruff lint+format check on edited Python in ruff projects                                            |
| PostToolUse | `Edit\|Write`                        | `style-check-test.sh`        | PostToolUse hook — run the style-check test suite when style-check changes                                                 |
| PostToolUse | `Edit\|Write`                        | `sync-docs-check.sh`         | PostToolUse hook — block edits that leave /sync-docs index tables drifted                                                  |
| PostToolUse | `Edit\|Write`                        | `sync-docs-test.sh`          | PostToolUse hook — run the sync-docs test suite when its Python changes                                                    |
| PostToolUse | `Edit\|Write`                        | `guard-secrets-test.sh`      | PostToolUse hook — run the guard-secrets test suite when the guard changes                                                 |
| PostToolUse | `Edit\|Write`                        | `recast-test.sh`             | PostToolUse hook — run the matching recast test file when a recast source changes                                          |
| PostToolUse | `Edit\|Write`                        | `md-links-check.py`          | PostToolUse hook — verify relative links and anchors in edited markdown resolve                                            |
| PostToolUse | `Edit\|Write`                        | `md-links-check-test.sh`     | PostToolUse hook — run the md-links-check test suite when the checker changes                                              |
| PostToolUse | `Edit\|Write`                        | `markdownlint-check.sh`      | PostToolUse hook — run markdownlint-cli2 on edited markdown in opted-in repos                                              |
| PostToolUse | `Edit\|Write`                        | `markdownlint-check-test.sh` | PostToolUse hook — run the markdownlint-check test suite when the lint hook changes                                        |
| PostToolUse | `Edit\|Write`                        | `exec-bit-guard-test.sh`     | PostToolUse hook — run the exec-bit-guard test suite when the gate or its suite changes                                    |
| PostToolUse | `Edit\|Write`                        | `audit-test.sh`              | PostToolUse hook — run the audit engine test suite when the engine or its suite changes                                    |
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

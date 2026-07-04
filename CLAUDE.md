# Global Claude Code Instructions

Universal instructions for all projects.

- **Code style and formatting:** [STYLE.md](./STYLE.md)
- **Code templates:** [templates.md](./templates.md) (Bash, Python, JavaScript)
- **Development workflows:** [workflows.md](./workflows.md) (the `/feature` pipeline; Explore/Plan/Code/Commit and TDD as primitives)
- **Contributing conventions:** [CONTRIBUTING.md](./CONTRIBUTING.md) (commit messages, semantic versioning)

> Auto-generated index tables sit between `<!-- sync:* -->` markers — don't hand-edit them; update the source and run `/sync-docs`.

> **Pushing is explicit-only.** Never `git push` to any remote unless the user has authorized *this*
> push — publishing is a deliberate, per-push decision. The `push-guard` hook enforces it (a bare
> `git push` is blocked; lead the command with `ALLOW_PUSH=1` only on explicit authorization).
> `/propagate` promotes to production locally by default; `/propagate --push` publishes to `origin`.

## Skills (Slash Commands)

<!-- sync:skills cols=Command:key,Purpose:auto -->
| Command               | Purpose                                                                                                                                             |
| :-------------------- | :-------------------------------------------------------------------------------------------------------------------------------------------------- |
| `/commit`             | Create a git commit with automatic semver tagging following STYLE.md conventions; signing and identity follow git config                            |
| `/idempotency-tester` | Verify a script is idempotent by running it twice in an isolated sandbox and diffing the resulting state                                            |
| `/init-bash`          | Scaffold a new Bash script from the standard template in templates.md                                                                               |
| `/init-js`            | Scaffold a new JavaScript module from the standard template in templates.md                                                                         |
| `/init-python`        | Scaffold a new Python module from the standard template in templates.md                                                                             |
| `/init-skill`         | Scaffold a new skill at skills/<name>/SKILL.md following the standard structure                                                                     |
| `/sync-docs`          | Regenerate index regions of README.md and CLAUDE.md from authoritative sources                                                                      |
| `/vet`                | Vet an authored skill, agent, or script — or the whole repo with --all — by dispatching the matching reviewer agent(s) and reporting their findings |
<!-- /sync:skills -->

## Agents

<!-- sync:agents cols=Agent:key,Model:auto,Purpose:auto -->
| Agent                    | Model  | Purpose                                                                                                     |
| :----------------------- | :----- | :---------------------------------------------------------------------------------------------------------- |
| `agent-reviewer`         | haiku  | Review agent definition files for compliance with the canonical agent frontmatter and structure             |
| `skill-content-reviewer` | sonnet | Review SKILL.md files for prose and content quality — clarity, completeness, consistency, and actionability |
| `skill-reviewer`         | haiku  | Review SKILL.md files for compliance with the repo's canonical skill structure                              |
| `style-reviewer`         | haiku  | Review code files for compliance with the global STYLE.md standards                                         |
<!-- /sync:agents -->

## Hooks

<!-- sync:hooks -->
| Event       | Matcher                              | Script                  | Purpose                                                                         |
| :---------- | :----------------------------------- | :---------------------- | :------------------------------------------------------------------------------ |
| PreToolUse  | `Read\|Edit\|Write\|MultiEdit\|Grep` | `guard-secrets.sh`      | Global PreToolUse hook — deny reading/editing secret files (.env*, keys, pem)   |
| PostToolUse | `Edit\|Write`                        | `style-check.sh`        | Global PostToolUse hook — validate file edits against STYLE.md                  |
| PostToolUse | `Edit\|Write`                        | `shellcheck-check.sh`   | PostToolUse hook — run shellcheck on edited shell scripts                       |
| PostToolUse | `Edit\|Write`                        | `ruff-check.sh`         | PostToolUse hook — run ruff lint+format check on edited Python in ruff projects |
| PostToolUse | `Edit\|Write`                        | `style-check-test.sh`   | PostToolUse hook — run the style-check test suite when style-check changes      |
| PostToolUse | `Edit\|Write`                        | `sync-docs-check.sh`    | PostToolUse hook — block edits that leave /sync-docs index tables drifted       |
| PostToolUse | `Edit\|Write`                        | `sync-docs-test.sh`     | PostToolUse hook — run the sync-docs test suite when its Python changes         |
| PostToolUse | `Edit\|Write`                        | `guard-secrets-test.sh` | PostToolUse hook — run the guard-secrets test suite when the guard changes      |
<!-- /sync:hooks -->

## Plugins

Enabled in [settings.json](./settings.json):

<!-- sync:plugins cols=Plugin:key,Purpose:manual -->
| Plugin                 | Purpose |
| :--------------------- | :------ |
| `claude-code-setup`    |         |
| `claude-md-management` |         |
| `code-review`          |         |
| `pyright-lsp`          |         |
| `superpowers`          |         |
<!-- /sync:plugins -->

### Superpowers plan/spec location (override)

The superpowers skills hardcode `docs/superpowers/plans/` and `docs/superpowers/specs/`.
Override that in every repo: save **plans** to `plans/` and design **specs** to `specs/`
at the repo root, dropping the `docs/superpowers/` prefix. Keep the `YYYY-MM-DD-<name>.md`
filename convention. When a skill (writing-plans, brainstorming, subagent-driven-development,
requesting-code-review, executing-plans) reads or writes a plan/spec, use these paths instead.

## Environment

- **Platform**: macOS with MacPorts package manager
- **Editor**: BBEdit (primary code editor)
- **Shell**: Prefer MacPorts bash (`/opt/local/bin/bash`) for scripts requiring advanced features
- **Default bash**: `/bin/bash` is the system bash (version 3.x, limited features)
- **GNU Core Utilities**: Installed via MacPorts (`coreutils`)
  - GNU tools are prefixed with `g` (e.g., `gls`, `ggrep`, `gdate`)
  - Use GNU versions for advanced features like `--long-options`

## Language and Tooling Preferences

- **Preferred**: Unix tools orchestrated through Bash scripts
- **Secondary**: Python for complex tasks requiring rich libraries
- Favor command-line tools and shell scripts over GUI methods
- Use Python when Bash becomes unwieldy or complex data structures are needed

### Package Management

#### Python (uv)

- **Version**: Python 3.13 (MacPorts)
- Create venv: `uv venv` — Activate: `source .venv/bin/activate`
- Install: `uv pip install <package>` (NOT standard `pip`)
- Sync: `uv pip sync requirements.txt`
- One-shot script with deps: `uv run --with <pkg1> --with <pkg2> python3 -c '...'` (no venv needed; ephemeral)

#### Node.js (NVM)

- **NVM**: 0.40.3 — **Node**: v26.3.0 (versions under `~/.nvm/versions/node/`)
- Initialize: `source /opt/local/share/nvm/init-nvm.sh` — in non-interactive shells this may
  leave `node` off PATH; call the binary directly: `~/.nvm/versions/node/<ver>/bin/node`
- Install: `npm install <package>`

#### System Tools (MacPorts)

- Install: `sudo port install <package>`
- Check: `port installed | grep <package>`
- Location: `/opt/local/bin/`, `/opt/local/lib/`

## macOS Notes

### Bash Versions

| Version | Location | Use Case |
|---------|----------|----------|
| 3.x | `/bin/bash` | System/POSIX scripts |
| 5.x | `/opt/local/bin/bash` | Modern scripts (associative arrays, `[[`, etc.) |

### GNU vs BSD Tools

macOS ships BSD tools by default. GNU versions (MacPorts) provide more features:

| Tool | BSD | GNU | Key Difference |
|------|-----|-----|----------------|
| grep | `/usr/bin/grep` | `ggrep` | `-P` (Perl regex) |
| sed | `/usr/bin/sed` | `gsed` | Extended features |
| date | `/bin/date` | `gdate` | Better parsing |
| ls | `/bin/ls` | `gls` | `--color`, `--group-directories-first` |

To use GNU by default: `export PATH="/opt/local/libexec/gnubin:$PATH"`

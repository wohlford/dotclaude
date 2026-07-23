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

> **Rewriting published history never unpublishes.** Old commits stay reachable by SHA on the host
> and in every existing clone or fork; and any **tag** still pointing at them keeps them fully
> browsable, so a branch force-push that leaves tags behind removes nothing. Delete those tags as
> part of the rewrite, and describe the result as not-current — never as erased.

> **Bugs get a regression test first.** When a bug is found, reproduce it as a failing test *before*
> fixing it (RED→GREEN; see [workflows.md](./workflows.md)). Skipping is a flagged exception — state
> why at fix time (e.g. untestable: timing/environment/interactive), never skip silently.

> **Never state a time of day** ("this morning", "tonight") unless you just read the clock (`date`) —
> name the trigger or step instead ("the push step", "next session"). Don't echo a time word from
> earlier in the conversation; time has moved since.

## Skills (Slash Commands)

<!-- sync:skills cols=Command:key,Purpose:auto -->
| Command               | Purpose                                                                                                                                                                                                         |
| :-------------------- | :-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `/audit`              | Run the mechanical compliance sweep — linters, format, link, exec-bit, and config-validity checks over a repo's tracked files; repos exclude generated paths via .auditignore                                   |
| `/commit`             | Create a git commit with automatic semver tagging following STYLE.md conventions; signing and identity follow git config                                                                                        |
| `/debrief`            | Run the end-of-session pre-compaction routine (deferral follow-up, CLAUDE.md refresh, memory save, automation review, and deferred design)                                                                      |
| `/feature`            | Run the methodical, risk-tiered pipeline for a change (triage → spec → spike → plan → reviews), then continue through subagent-driven execution to an integrated change; --plan-only stops at the reviewed plan |
| `/idempotency-tester` | Verify a script is idempotent by running it twice in an isolated sandbox and diffing the resulting state                                                                                                        |
| `/init-bash`          | Scaffold a new Bash script from the standard template in templates.md                                                                                                                                           |
| `/init-js`            | Scaffold a new JavaScript module from the standard template in templates.md                                                                                                                                     |
| `/init-python`        | Scaffold a new Python module from the standard template in templates.md                                                                                                                                         |
| `/init-skill`         | Scaffold a new skill at skills/<name>/SKILL.md following the standard structure                                                                                                                                 |
| `/propagate`          | Promote committed changes from this dev working copy to the live ~/.claude repo locally; --push also publishes to origin (explicit)                                                                             |
| `/recast`             | Re-develop a git source repo into a target as a genuine ground-up, proven-per-commit history converging to functional equivalence (never copies the tree, never pushes)                                         |
| `/sync-docs`          | Regenerate index regions of README.md and CLAUDE.md from authoritative sources                                                                                                                                  |
| `/vet`                | Vet an authored skill, agent, or script — or the whole repo with --all — by dispatching the matching reviewer agent(s) and reporting their findings                                                             |
<!-- /sync:skills -->

## Agents

<!-- sync:agents cols=Agent:key,Model:auto,Purpose:auto -->
| Agent                    | Model  | Purpose                                                                                                     |
| :----------------------- | :----- | :---------------------------------------------------------------------------------------------------------- |
| `agent-reviewer`         | haiku  | Review agent definition files for compliance with the canonical agent frontmatter and structure             |
| `skill-content-reviewer` | sonnet | Review SKILL.md files for prose and content quality — clarity, completeness, consistency, and actionability |
| `skill-reviewer`         | haiku  | Review SKILL.md files for compliance with the repo's canonical skill structure                              |
| `style-reviewer`         | sonnet | Review code files for compliance with the global STYLE.md standards                                         |
<!-- /sync:agents -->

## Hooks

<!-- sync:hooks -->
| Event       | Matcher                              | Script                           | Purpose                                                                                                                        |
| :---------- | :----------------------------------- | :------------------------------- | :----------------------------------------------------------------------------------------------------------------------------- |
| PreToolUse  | `Read\|Edit\|Write\|MultiEdit\|Grep` | `guard-secrets.sh`               | Global PreToolUse hook — deny reading/editing secret files (.env*, keys, pem)                                                  |
| PreToolUse  | `Bash`                               | `push-guard.py`                  | PreToolUse hook — block `git push` unless the push segment leads with an ALLOW_PUSH=1 override                                 |
| PreToolUse  | `Bash`                               | `exec-bit-guard.sh`              | PreToolUse hook — block `git commit` when it would record a new shebang file without the exec bit (or a 755→644 downgrade)     |
| PreToolUse  | `Bash`                               | `recast-commit-gate.py`          | PreToolUse hook — run the recast suite before a commit that touches recast source                                              |
| PreToolUse  | `Bash`                               | `publication-push-guard.py`      | PreToolUse hook — fail-closed dev-block keeping `dev` private in a repo that adopted the dev/main publication model            |
| PostToolUse | `Edit\|Write`                        | `style-check.sh`                 | Global PostToolUse hook — validate file edits against STYLE.md                                                                 |
| PostToolUse | `Edit\|Write`                        | `shellcheck-check.sh`            | PostToolUse hook — run shellcheck on edited shell scripts                                                                      |
| PostToolUse | `Edit\|Write`                        | `ruff-check.sh`                  | PostToolUse hook — run ruff lint+format check on edited Python in ruff projects                                                |
| PostToolUse | `Edit\|Write`                        | `style-check-test.sh`            | PostToolUse hook — run the style-check test suite when style-check changes                                                     |
| PostToolUse | `Edit\|Write`                        | `sync-docs-check.sh`             | PostToolUse hook — block edits that leave /sync-docs index tables drifted                                                      |
| PostToolUse | `Edit\|Write`                        | `sync-docs-test.sh`              | PostToolUse hook — run the sync-docs test suite when its Python changes                                                        |
| PostToolUse | `Edit\|Write`                        | `guard-secrets-test.sh`          | PostToolUse hook — run the guard-secrets test suite when the guard changes                                                     |
| PostToolUse | `Edit\|Write`                        | `recast-test.sh`                 | PostToolUse hook — run the matching recast test file when a recast source changes                                              |
| PostToolUse | `Edit\|Write`                        | `md-links-check.py`              | PostToolUse hook — verify relative links and anchors in edited markdown resolve                                                |
| PostToolUse | `Edit\|Write`                        | `md-links-check-test.sh`         | PostToolUse hook — run the md-links-check test suite when the checker changes                                                  |
| PostToolUse | `Edit\|Write`                        | `markdownlint-check.sh`          | PostToolUse hook — run markdownlint-cli2 on edited markdown in opted-in repos                                                  |
| PostToolUse | `Edit\|Write`                        | `markdownlint-check-test.sh`     | PostToolUse hook — run the markdownlint-check test suite when the lint hook changes                                            |
| PostToolUse | `Edit\|Write`                        | `exec-bit-guard-test.sh`         | PostToolUse hook — run the exec-bit-guard test suite when the gate or its suite changes                                        |
| PostToolUse | `Edit\|Write`                        | `audit-test.sh`                  | PostToolUse hook — run the audit engine test suite when the engine or its suite changes                                        |
| PostToolUse | `Edit\|Write`                        | `publication-push-guard-test.sh` | PostToolUse hook — run the publication-push-guard suite when the guard, its suite, or the shared git_command tokenizer changes |
<!-- /sync:hooks -->

> Hooks fire per-edit: a multi-step change that passes through an invalid intermediate state
> (e.g. resolving conflict markers with two Edits) trips transient PostToolUse errors — verify
> the final file state instead of reacting to the mid-sequence report, or make it one edit.

## Plugins

Enabled in [settings.json](./settings.json):

<!-- sync:plugins cols=Plugin:key,Purpose:manual -->
| Plugin                 | Purpose                                                                  |
| :--------------------- | :----------------------------------------------------------------------- |
| `claude-code-setup`    | Recommend Claude Code automations                                        |
| `claude-md-management` | Audit and improve CLAUDE.md files                                        |
| `code-review`          | Code review a diff or PR (`/code-review`, `/review`, `/security-review`) |
| `pyright-lsp`          | Python type checking via Pyright                                         |
| `superpowers`          | Enhanced development workflows and skills                                |
<!-- /sync:plugins -->

### Superpowers plan/spec location (override)

The superpowers skills hardcode `docs/superpowers/plans/` and `docs/superpowers/specs/`.
Override that in every repo: save **plans** to `plans/` and design **specs** to `specs/`
at the repo root, dropping the `docs/superpowers/` prefix. Keep the `YYYY-MM-DD-<name>.md`
filename convention. When a skill (writing-plans, brainstorming, subagent-driven-development,
requesting-code-review, executing-plans) reads or writes a plan/spec, use these paths instead.

### Superpowers SDD: the progress ledger has no plan identity

`subagent-driven-development`'s `.superpowers/sdd/progress.md` records `Task N: complete` with **no
reference to which plan** — yet the skill says to trust it over your own recollection. A ledger left
by a *previous* plan therefore reads as if this plan's tasks are already done. Confirm it names the
plan and base you are actually executing before trusting any line; reset it when starting a new plan.

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
- **Multi-line literal checks are one such case.** `grep -F` treats an embedded newline
  as *alternation*, not a sequence: `grep -Fc "$(printf 'a\nb')"` counts lines matching **either**,
  so a multi-line check returns a plausible-but-wrong count and reads as verified. Use
  `python3 -c "..."` (`needle in open(f).read()`) or `grep -Pzo`. **In wrapped text, use it even for
  a phrase you believe is one line** — if it happens to wrap, a line-based grep returns 0 and absence
  is not evidence of absence.
- **A pipeline's exit status is the LAST command's.** `some-check | tail -20` reports `tail`'s
  success however the check exited — so a run that "completed (exit code 0)" can have proven
  nothing, and a backgrounded one reads as a clean pass. Read the tool's own verdict/summary lines
  rather than the rc, or don't pipe it (`set -o pipefail`, or `${PIPESTATUS[0]}`, when you must).

### Package Management

#### Python (uv)

- **Version**: Python 3.13 (MacPorts)
- Create venv: `uv venv` — Activate: `source .venv/bin/activate`
- Install: `uv pip install <package>` (NOT standard `pip`)
- Sync: `uv pip sync requirements.txt`
- One-shot script with deps: `uv run --with <pkg1> --with <pkg2> python3 -c '...'` (no venv needed; ephemeral)

#### Node.js (NVM)

- **NVM** manages Node (versions under `~/.nvm/versions/node/`); use the newest installed (v26.x line)
- Initialize: `source /opt/local/share/nvm/init-nvm.sh` — in non-interactive shells this may
  leave `node` off PATH; call the binary directly: `~/.nvm/versions/node/<ver>/bin/node`
- Install: `npm install <package>`
- npm-global CLIs live per-version in `~/.nvm/versions/node/<ver>/bin` and need that dir ON
  PATH (their `env node` shebang; an absolute launcher path alone fails). `markdownlint-cli2`
  is installed there — the markdownlint hook uses it; repos opt in via `.markdownlint-cli2.jsonc`
  (for a repo-wide run pass the glob explicitly — `markdownlint-cli2 "**/*.md"` — a bare invocation
  lints 0 files when the config contains only ignores, which false-reads as a clean pass)

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

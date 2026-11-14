# dotclaude

Global [Claude Code](https://docs.anthropic.com/en/docs/claude-code) configuration: style standards, development workflows, custom skills, agents, and hooks.

## Installation

This repo is symlinked into `~/.claude` rather than cloned over it, so the checkout stays the source of truth. Clone it anywhere, then run the installer:

```bash
git clone https://github.com/wohlford/dotclaude.git
cd dotclaude
./install.sh
```

`install.sh` symlinks this repo's tracked root entries into `~/.claude`. The membership is **derived, not hand-listed** — everything tracked at the root except a declared exclusion list (the dotfiles, `ruff.toml`, and `git-hooks/`, which are consumed by tooling running inside this repo rather than by Claude Code). So a document added here appears in `~/.claude` on the next run, with no list to update. Anything already present is backed up first, and backups are stripped of their exec bit.

`settings.json` **is** linked. Claude Code rewrites it at runtime, and the live clone marks it `skip-worktree` so those rewrites never surface as repository changes.

The installer records which clone owns the farm in `~/.claude/.installed-from`. That marker lives only in the target — it is never tracked here and is never a farm member — and it is what lets a later run tell "this farm belongs to another clone" from "this is a first install", whether the managed paths are symlinks or ordinary files.

Two flags: `--check` reports drift and changes nothing (non-zero exit when the farm has drifted); `--rewire` authorizes repointing a farm that another clone owns, and updates the marker. The script refuses to run from inside `~/.claude` itself — running a stray copy from there would relink every managed path to itself.

Restart Claude Code after installing (or after pulling updates) to reload the configuration.

Wondering how the pieces fit — what takes effect where, and what owns what? See [ARCHITECTURE.md](ARCHITECTURE.md).

### Running Claude Code in another clone

In a clone that the global `CLAUDE.md` link does not point to, such as a development clone kept beside the checkout that `install.sh` linked, the same `CLAUDE.md` text can load twice: once as the global `CLAUDE.md`, the link `install.sh` created, and once as the clone's own `CLAUDE.md`, which Claude Code finds by walking up from the working directory. Sessions at the root and in a subdirectory of such a clone were both measured. Excluding the clone's copy cut the session's startup load by about one third, and the global copy still loaded once.

In the checkout the global link points to, one run listed the file once, and naming it in the exclusion changed nothing, so no key is needed there.

To exclude the clone's copy, add a `claudeMdExcludes` key to the clone's `.claude/settings.local.json`. That file is gitignored, so the setting never ships. Merge the key into the existing JSON rather than replacing the file, which may hold other settings:

```json
{ "claudeMdExcludes": ["/path/to/clone/CLAUDE.md"] }
```

The value is the absolute path of the clone's `CLAUDE.md`. The safe spelling is the clone's directory as `pwd -P` prints it, followed by `/CLAUDE.md`. In one test, the same file named through a symlinked directory matched as well.

The exclusion does not apply in these cases, and nothing reports it:

- Another clone or a second checkout does not match the path, so it loads both copies again.
- A fresh clone has no local settings file at all.

A settings file copied from another clone is harmless: its key names a path that does not exist in the new clone, so it matches nothing.

While the global `CLAUDE.md` link resolves, these failures cost only startup load: the global copy still loads, so no instructions are lost. If that link is missing or dangling, the exclusion leaves a session in the clone with no `CLAUDE.md` at all, silently. That follows from how the exclusion works; it was not run. `install.sh --check` reports a drifted farm, and the `farm-drift-check.py` SessionStart hook listed under Hooks warns when the farm has drifted from its source clone.

With the key applied, a session in the clone runs under the text the global `CLAUDE.md` link resolves to, and so do the delegates (subagents) it dispatches; in one measurement, with one agent type, a delegate inherited the exclusion. So an edit to the clone's `CLAUDE.md` does not reach those sessions or delegates until the checkout the global link points to has that edit. That follows from how the exclusion works and is not a measurement. In one test, a delegate dispatched with worktree isolation from a throwaway clone that had its own key carried only the global copy; no run without the key was made for that case, so this shows what it carried, not why. To test an edit to `CLAUDE.md` live, remove the key and start a new session.

Applying or removing the key changes only new sessions. Whether a settings reload re-reads `CLAUDE.md` in a running session was not measured, so assume it does not.

To check the exclusion, start a fresh session in the clone and ask it to list the headers of the form `Contents of <path>` that its instructions carry. The clone's own `CLAUDE.md` should not be among them. The global `CLAUDE.md` should be. If the project has a memory index, it is listed too; it is not a copy of `CLAUDE.md`. This list is a convenience, since a model describing its own context can be wrong; the sturdier check is to compare the session's startup load with and without the key.

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
| [DELEGATING.md](DELEGATING.md) | Constraints a delegated subagent must observe — the block to paste into each dispatch |

### Skills (Slash Commands)

<!-- sync:skills cols=Command:key,Purpose:auto -->
| Command               | Purpose                                                                                                                                                                                                                                                                                                                   |
| :-------------------- | :------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `/audit`              | Run the mechanical compliance sweep — linters, format, link, exec-bit, and config-validity checks over a repo's tracked files; repos exclude generated paths via .auditignore. Mechanical only — the test suite is a SEPARATE requirement and runs only under --tests, so a plain PASS is never evidence the suite passed |
| `/commit`             | Create a git commit with automatic semver tagging following STYLE.md conventions; signing and identity follow git config                                                                                                                                                                                                  |
| `/debrief`            | Run the end-of-session pre-compaction routine (deferral follow-up, CLAUDE.md refresh, memory save, automation review, and deferred design)                                                                                                                                                                                |
| `/feature`            | Run the methodical, risk-tiered pipeline for a change (triage → spec → spike → plan → reviews), then continue through subagent-driven execution to an integrated change; --plan-only stops at the reviewed plan                                                                                                           |
| `/idempotency-tester` | Verify a script is idempotent by running it twice in an isolated sandbox and diffing the resulting state                                                                                                                                                                                                                  |
| `/init-bash`          | Scaffold a new Bash script from the standard template in templates.md                                                                                                                                                                                                                                                     |
| `/init-js`            | Scaffold a new JavaScript module from the standard template in templates.md                                                                                                                                                                                                                                               |
| `/init-python`        | Scaffold a new Python module from the standard template in templates.md                                                                                                                                                                                                                                                   |
| `/init-skill`         | Scaffold a new skill at skills/<name>/SKILL.md following the standard structure                                                                                                                                                                                                                                           |
| `/propagate`          | Promote committed changes from this dev working copy to the live ~/.claude repo locally; --push also publishes to origin (explicit)                                                                                                                                                                                       |
| `/recast`             | Re-develop a git source repo into a target as a genuine ground-up, proven-per-commit history converging to functional equivalence (never copies the tree, never pushes)                                                                                                                                                   |
| `/sync-docs`          | Regenerate index regions of README.md and CLAUDE.md from authoritative sources                                                                                                                                                                                                                                            |
| `/vet`                | Vet an authored skill, agent, or script — or the whole repo with --all — by dispatching the matching reviewer agent(s) and reporting their findings                                                                                                                                                                       |
<!-- /sync:skills -->

### Agents

Unlike the reviewer agents in the table below, [`prose-writer`](agents/prose-writer.md) writes documents. It is pinned to `opus`, whose row in the model and effort policy of [agents/README.md](agents/README.md) lists authoring (not reviewing) standalone documentation prose alongside architecture and design, whole-branch review and final gates. It has no shell — its tools are Read, Grep, Glob, Write and Edit — so the session that dispatches it commits what it returns. [CLAUDE.md](CLAUDE.md) routes standalone documents to it, says to dispatch it with no `model` or `effort` override, which would defeat the definition's settings, and has the dispatching session verify every return itself. Where the facts live picks the mode: in *author* mode it writes from the tree, and in *revise* mode it rewrites a draft of decisions or measurements, supplied in the brief or as the target file itself, marking each missing fact as a gap rather than filling it.

<!-- sync:agents cols=Agent:key,Model:auto,Purpose:auto -->
| Agent                    | Model  | Purpose                                                                                                                                                                                                                        |
| :----------------------- | :----- | :----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `agent-reviewer`         | haiku  | Review agent definition files for compliance with the canonical agent frontmatter and structure                                                                                                                                |
| `prose-writer`           | opus   | Write or rewrite standalone documents a reader opens to learn how the project works — README, architecture and reference docs, policy guides, release notes; author mode reads the tree, revise mode rewrites a supplied draft |
| `skill-content-reviewer` | sonnet | Review SKILL.md files for prose and content quality — clarity, completeness, consistency, and actionability                                                                                                                    |
| `skill-reviewer`         | haiku  | Review SKILL.md files for compliance with the repo's canonical skill structure                                                                                                                                                 |
| `style-reviewer`         | sonnet | Review code files for compliance with the global STYLE.md standards                                                                                                                                                            |
<!-- /sync:agents -->

### Hooks

<!-- sync:hooks -->
| Event        | Matcher                                            | Script                           | Purpose                                                                                                                    |
| :----------- | :------------------------------------------------- | :------------------------------- | :------------------------------------------------------------------------------------------------------------------------- |
| PreToolUse   | `Read\|Edit\|Write\|MultiEdit\|Grep\|NotebookEdit` | `guard-secrets.sh`               | Global PreToolUse hook — deny reading/editing secret files (.env*, keys, pem)                                              |
| PreToolUse   | `Edit\|Write\|MultiEdit\|NotebookEdit`             | `mutate-edit-guard.py`           | PreToolUse hook — refuse an edit to a file a mutation campaign owns (its sidecar exists)                                   |
| PreToolUse   | `Bash`                                             | `git-timing-guard.py`            | PreToolUse hook — block PUBLISHING outside a configured time window                                                        |
| PreToolUse   | `Bash`                                             | `push-guard.py`                  | PreToolUse hook — block `git push` unless the push segment leads with an ALLOW_PUSH=1 override                             |
| PreToolUse   | `Bash`                                             | `exec-bit-guard.sh`              | PreToolUse hook — block `git commit` when it would record a new shebang file without the exec bit (or a 755→644 downgrade) |
| PreToolUse   | `Bash`                                             | `recast-commit-gate.py`          | PreToolUse hook — run the recast suite before a commit that touches recast source                                          |
| PreToolUse   | `Bash`                                             | `publication-push-guard.py`      | PreToolUse hook — fail-closed dev-block keeping `dev` private in a repo that adopted the dev/main publication model        |
| PreToolUse   | `Bash`                                             | `commit-subject-guard.py`        | PreToolUse hook — refuse a commit whose subject is provably at or over the block limit                                     |
| PostToolUse  | `Bash`                                             | `commit-subject-advisor.py`      | PostToolUse hook — advise an amend when a committed subject reaches the advisory limit                                     |
| PostToolUse  | `Edit\|Write`                                      | `style-check.sh`                 | Global PostToolUse hook — validate file edits against STYLE.md                                                             |
| PostToolUse  | `Edit\|Write`                                      | `shellcheck-check.sh`            | PostToolUse hook — run shellcheck on edited shell scripts                                                                  |
| PostToolUse  | `Edit\|Write`                                      | `ruff-check.sh`                  | PostToolUse hook — run ruff lint+format check on edited Python in ruff projects                                            |
| PostToolUse  | `Edit\|Write`                                      | `style-check-test.sh`            | PostToolUse hook — run the style-check test suite when style-check changes                                                 |
| PostToolUse  | `Edit\|Write`                                      | `sync-docs-check.sh`             | PostToolUse hook — block edits that leave /sync-docs index tables drifted                                                  |
| PostToolUse  | `Edit\|Write`                                      | `sync-docs-test.sh`              | PostToolUse hook — run the sync-docs test suite when its Python changes                                                    |
| PostToolUse  | `Edit\|Write`                                      | `guard-secrets-test.sh`          | PostToolUse hook — run the guard-secrets test suite when the guard changes                                                 |
| PostToolUse  | `Edit\|Write`                                      | `recast-test.sh`                 | PostToolUse hook — run the matching recast test file when a recast source changes                                          |
| PostToolUse  | `Edit\|Write`                                      | `md-links-check.py`              | PostToolUse hook — verify relative links and anchors in edited markdown resolve                                            |
| PostToolUse  | `Edit\|Write`                                      | `md-links-check-test.sh`         | PostToolUse hook — run the md-links-check test suite when the checker changes                                              |
| PostToolUse  | `Edit\|Write`                                      | `mutation-anchors-check-test.sh` | PostToolUse hook — run the mutation-anchors-check test suite when the checker changes                                      |
| PostToolUse  | `Edit\|Write`                                      | `env-claims-check-test.sh`       | PostToolUse hook — run the env-claims-check test suite when the checker changes                                            |
| PostToolUse  | `Edit\|Write`                                      | `mutate-edit-guard-test.sh`      | PostToolUse hook — run the mutate-edit-guard test suite when the guard or mutate.py changes                                |
| PostToolUse  | `Edit\|Write`                                      | `markdownlint-check.sh`          | PostToolUse hook — run markdownlint-cli2 on edited markdown in opted-in repos                                              |
| PostToolUse  | `Edit\|Write`                                      | `markdownlint-check-test.sh`     | PostToolUse hook — run the markdownlint-check test suite when the lint hook changes                                        |
| PostToolUse  | `Edit\|Write`                                      | `memory-index-check.py`          | PostToolUse hook — flag a memory-index entry, or the whole file, grown too large                                           |
| PostToolUse  | `Edit\|Write`                                      | `memory-index-check-test.sh`     | PostToolUse hook — run the memory-index-check test suite when the checker changes                                          |
| PostToolUse  | `Edit\|Write`                                      | `exec-bit-guard-test.sh`         | PostToolUse hook — run the exec-bit-guard test suite when the gate or its suite changes                                    |
| PostToolUse  | `Edit\|Write`                                      | `audit-test.sh`                  | PostToolUse hook — run the audit engine suite, or the suite-driver suite, when its files change                            |
| PostToolUse  | `Edit\|Write`                                      | `commit-subject-test.sh`         | PostToolUse hook — run the commit-subject suites, and py39-compat on any scripts/*.py edit                                 |
| PostToolUse  | `Edit\|Write`                                      | `publication-push-guard-test.sh` | PostToolUse hook — run the suites of every gate built on git_command.py when a gate, its suite, or the tokenizer changes   |
| PostToolUse  | `Edit\|Write`                                      | `hook-machinery-test.sh`         | PostToolUse hook — run the hook tests when a test-runner hook or the machinery around it changes                           |
| PostToolUse  | `Edit\|Write`                                      | `debrief-backlog-test.sh`        | PostToolUse hook — run the debrief backlog-helper suite when its Python changes                                            |
| SessionStart | `startup\|resume`                                  | `farm-drift-check.py`            | SessionStart hook — warn when the ~/.claude symlink farm has drifted from its source clone                                 |
<!-- /sync:hooks -->

### Plugins

<!-- sync:plugins cols=Plugin:key,Purpose:manual -->
| Plugin                 | Purpose                                                                  |
| :--------------------- | :----------------------------------------------------------------------- |
| `claude-code-setup`    | Recommend Claude Code automations                                        |
| `claude-md-management` | Audit and improve CLAUDE.md files                                        |
| `code-review`          | Code review a diff or PR (`/code-review`, `/review`, `/security-review`) |
| `pyright-lsp`          | Python type checking via Pyright                                         |
| `superpowers`          | Enhanced development workflows and skills                                |
<!-- /sync:plugins -->

Plugins are configured in [`settings.json`](settings.json) and resolved automatically by Claude Code.

## License

[MIT](LICENSE)

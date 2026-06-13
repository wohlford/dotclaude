# Scripts

Hook and utility scripts shipped with this `~/.claude/` configuration. The scripts here back
the PostToolUse hooks declared in [`settings.json`](../settings.json); `install.sh` (repo
root) symlinks this directory into `~/.claude`. See [STYLE.md](../STYLE.md) "Documentation
Sync" for how this index stays in sync.

## Index

<!-- sync:scripts -->
| Script                | Purpose                                                                    |
| :-------------------- | :------------------------------------------------------------------------- |
| `git-timing-guard.sh` | PreToolUse hook — block git writes outside a configured time window        |
| `shellcheck-check.sh` | PostToolUse hook — run shellcheck on edited shell scripts                  |
| `style-check-test.sh` | PostToolUse hook — run the style-check test suite when style-check changes |
| `style-check.sh`      | Global PostToolUse hook — validate file edits against STYLE.md             |
| `sync-docs-check.sh`  | PostToolUse hook — block edits that leave /sync-docs index tables drifted  |
| `sync-docs-test.sh`   | PostToolUse hook — run the sync-docs test suite when its Python changes    |
<!-- /sync:scripts -->

# Scripts

Hook and utility scripts shipped with this `~/.claude/` configuration. The scripts here back
the hooks declared in [`settings.json`](../settings.json); `install.sh` (repo
root) symlinks this directory into `~/.claude`. New hooks follow the shared protocol in
[HOOKS.md](HOOKS.md). See [`../skills/sync-docs/reference.md`](../skills/sync-docs/reference.md)
for how the index below stays in sync.

## Index

<!-- sync:scripts -->
| Script                           | Purpose                                                                                                                        |
| :------------------------------- | :----------------------------------------------------------------------------------------------------------------------------- |
| `audit-test.sh`                  | PostToolUse hook — run the audit engine test suite when the engine or its suite changes                                        |
| `exec-bit-guard-test.sh`         | PostToolUse hook — run the exec-bit-guard test suite when the gate or its suite changes                                        |
| `exec-bit-guard.sh`              | PreToolUse hook — block `git commit` when it would record a new shebang file without the exec bit (or a 755→644 downgrade)     |
| `guard-secrets-test.sh`          | PostToolUse hook — run the guard-secrets test suite when the guard changes                                                     |
| `guard-secrets.sh`               | Global PreToolUse hook — deny reading/editing secret files (.env*, keys, pem)                                                  |
| `markdownlint-check-test.sh`     | PostToolUse hook — run the markdownlint-check test suite when the lint hook changes                                            |
| `markdownlint-check.sh`          | PostToolUse hook — run markdownlint-cli2 on edited markdown in opted-in repos                                                  |
| `md-links-check-test.sh`         | PostToolUse hook — run the md-links-check test suite when the checker changes                                                  |
| `md-links-check.py`              | PostToolUse hook — verify relative links and anchors in edited markdown resolve                                                |
| `publication-push-guard-test.sh` | PostToolUse hook — run the publication-push-guard suite when the guard, its suite, or the shared git_command tokenizer changes |
| `publication-push-guard.py`      | PreToolUse hook — fail-closed dev-block keeping `dev` private in a repo that adopted the dev/main publication model            |
| `push-guard.py`                  | PreToolUse hook — block `git push` unless the push segment leads with an ALLOW_PUSH=1 override                                 |
| `recast-commit-gate.py`          | PreToolUse hook — run the recast suite before a commit that touches recast source                                              |
| `recast-test.sh`                 | PostToolUse hook — run the matching recast test file when a recast source changes                                              |
| `ruff-check.sh`                  | PostToolUse hook — run ruff lint+format check on edited Python in ruff projects                                                |
| `shellcheck-check.sh`            | PostToolUse hook — run shellcheck on edited shell scripts                                                                      |
| `style-check-test.sh`            | PostToolUse hook — run the style-check test suite when style-check changes                                                     |
| `style-check.sh`                 | Global PostToolUse hook — validate file edits against STYLE.md                                                                 |
| `sync-docs-check.sh`             | PostToolUse hook — block edits that leave /sync-docs index tables drifted                                                      |
| `sync-docs-test.sh`              | PostToolUse hook — run the sync-docs test suite when its Python changes                                                        |
<!-- /sync:scripts -->

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
| `claude-md-structure.py`         | Measure CLAUDE.md's verification-hazards section — group sizes, member counts, lengths                                         |
| `commit-subject-advisor.py`      | PostToolUse hook — advise an amend when a committed subject reaches the advisory limit                                         |
| `commit-subject-guard.py`        | PreToolUse hook — refuse a commit whose subject is provably at or over the block limit                                         |
| `commit-subject-test.sh`         | PostToolUse hook — run the commit-subject suites, and py39-compat on any scripts/*.py edit                                     |
| `debrief-backlog-test.sh`        | PostToolUse hook — run the debrief backlog-helper suite when its Python changes                                                |
| `exec-bit-guard-test.sh`         | PostToolUse hook — run the exec-bit-guard test suite when the gate or its suite changes                                        |
| `exec-bit-guard.sh`              | PreToolUse hook — block `git commit` when it would record a new shebang file without the exec bit (or a 755→644 downgrade)     |
| `guard-secrets-test.sh`          | PostToolUse hook — run the guard-secrets test suite when the guard changes                                                     |
| `guard-secrets.sh`               | Global PreToolUse hook — deny reading/editing secret files (.env*, keys, pem)                                                  |
| `markdownlint-check-test.sh`     | PostToolUse hook — run the markdownlint-check test suite when the lint hook changes                                            |
| `markdownlint-check.sh`          | PostToolUse hook — run markdownlint-cli2 on edited markdown in opted-in repos                                                  |
| `md-links-check-test.sh`         | PostToolUse hook — run the md-links-check test suite when the checker changes                                                  |
| `md-links-check.py`              | PostToolUse hook — verify relative links and anchors in edited markdown resolve                                                |
| `mutation-anchors-check-test.sh` | PostToolUse hook — run the mutation-anchors-check test suite when the checker changes                                          |
| `mutation-anchors-check.py`      | Assert every mutation campaign's `old` anchor still resolves exactly once in its subject                                       |
| `propagate-postcheck.sh`         | Verify /propagate's LOCAL promote landed correctly, choosing the postcondition branch                                          |
| `prose-diff.py`                  | Verify a restructuring is lossless by diffing word or line multisets in both directions                                        |
| `publication-push-guard-test.sh` | PostToolUse hook — run the publication-push-guard suite when the guard, its suite, or the shared git_command tokenizer changes |
| `publication-push-guard.py`      | PreToolUse hook — fail-closed dev-block keeping `dev` private in a repo that adopted the dev/main publication model            |
| `publish-brick.sh`               | Materialise, prove, commit and tag ONE recast brick onto the published branch                                                  |
| `publish-drive.py`               | Drive publish-brick.sh once per brick over a reviewed plan, halting on the first failure                                       |
| `publish-fold-plan.py`           | Propose brick boundaries for the publish path by classifying what each commit removes                                          |
| `publish-preflight.sh`           | Verify /propagate's publish start-invariant before any brick is applied, tagged, or pushed                                     |
| `push-guard.py`                  | PreToolUse hook — block `git push` unless the push segment leads with an ALLOW_PUSH=1 override                                 |
| `recast-commit-gate.py`          | PreToolUse hook — run the recast suite before a commit that touches recast source                                              |
| `recast-test.sh`                 | PostToolUse hook — run the matching recast test file when a recast source changes                                              |
| `ruff-check.sh`                  | PostToolUse hook — run ruff lint+format check on edited Python in ruff projects                                                |
| `run-long.sh`                    | Launch a long job in the background and record its real exit status inside the artifact                                        |
| `settings-hooks-check.py`        | Verify a promoted runtime settings.json kept every hook registration the commit added                                          |
| `shellcheck-check.sh`            | PostToolUse hook — run shellcheck on edited shell scripts                                                                      |
| `style-check-test.sh`            | PostToolUse hook — run the style-check test suite when style-check changes                                                     |
| `style-check.sh`                 | Global PostToolUse hook — validate file edits against STYLE.md                                                                 |
| `sync-docs-check.sh`             | PostToolUse hook — block edits that leave /sync-docs index tables drifted                                                      |
| `sync-docs-test.sh`              | PostToolUse hook — run the sync-docs test suite when its Python changes                                                        |
<!-- /sync:scripts -->

## Libraries

`lib/` holds importable modules rather than runnable hooks, so the index above — which globs
`scripts/*.sh` and `scripts/*.py` — does not reach them. Each module's docstring carries its own
rationale; that is deliberate, since a second copy here would be a hand-maintained list with
nothing keeping it honest.

One is worth naming, because you need it *before* you would think to look it up.
**`lib/mutate.py`** is the shared mutation-campaign runner: import it and supply only the
`(label, old, new)` list instead of hand-writing a harness. Ten were written and discarded before
it existed, and every re-derivation silently lost a different safety property — most often the
unmutated baseline, without which an already-red suite scores every mutation as caught and the
campaign prints a flawless sweep. `tests/mutate_lib_mutate.py` is the runner's own campaign, run
on demand rather than collected by pytest.

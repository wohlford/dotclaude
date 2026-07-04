# Authoring hooks

The scripts in this directory are Claude Code hooks — most are **PostToolUse** (run after every
`Edit`/`Write`, waving the change through or blocking it), plus a **PreToolUse** deny-gate (see
below). They share one protocol; a new hook should follow it. The existing scripts are the worked
examples.

## The contract

A hook reads a JSON payload on **stdin** and signals its verdict through the **exit code**:

| Exit | Meaning |
|---|---|
| `0` | Allow — the edit proceeds. Also the code for "not my business" (wrong file type, tool absent). |
| `2` | **Block** — everything the hook wrote to **stderr** is surfaced to Claude as a blocking error to fix. |

PostToolUse runs *after* the write lands, so exit 2 does not undo the file — it feeds the message back
so Claude corrects it. Nothing else blocks: any other nonzero exit is treated as noise, not a veto, so
a hook that dies on its own bug does not wedge the user. Design for that — the only path to `2` is a
genuine violation.

## The shape

Extract the edited path, then guard from cheapest to most expensive, exiting `0` the instant the hook
doesn't apply:

```bash
#!/usr/bin/env bash
set -euo pipefail

input=$(cat)
file_path=$(printf '%s' "$input" | jq -r '.tool_input.file_path // empty')
[ -n "$file_path" ] || exit 0

# 1. cheap path/extension guard — the common case exits here, fast
case "$file_path" in
  *.py) ;;
  *) exit 0 ;;
esac

# 2. repo guard (if the check only makes sense inside this project)
root=$(git -C "$(dirname "$file_path")" rev-parse --show-toplevel 2>/dev/null) || exit 0

# 3. tool-availability guard — NEVER falsely block when the tool is absent
command -v ruff >/dev/null 2>&1 || exit 0

# 4. the actual check — the only path that may exit 2
if ! ruff check "$file_path" >/dev/null 2>&1; then
  ruff check "$file_path" >&2
  exit 2
fi
exit 0
```

## Principles

- **Never falsely block.** A missing tool (`shellcheck`/`ruff`/`jq`/`pytest`), a file the hook doesn't
  cover, or a repo without the relevant config must exit `0` — never `2`. A blocked edit the user
  can't explain is worse than a missed check. Every existing hook is a silent no-op outside its scope.
- **Cheapest guard first.** Order the guards so the 99% of edits the hook doesn't care about return in
  microseconds. The extension/path `case` comes before any `git` or tool call.
- **Only real violations reach `exit 2`,** and they print a fix-oriented message to stderr (the user
  reads it as Claude's feedback). Use `printf`, not `echo`, for that message.
- **bash-3.2 / BSD-safe.** These run under macOS's system bash; no `mapfile`, no associative arrays, no
  GNU-only flags. See [`../STYLE.md`](../STYLE.md).

## Header format

Every hook opens with the standard block — and `# Purpose:` is not just documentation: the
`sync:hooks` / `sync:scripts` index tables render it, so keep it a crisp one-liner.

```bash
# Script: name.sh
# Purpose: <one crisp line — this is what the index tables show>
# Usage: PostToolUse(Edit|Write) hook — reads JSON on stdin
# Exit codes: 0 allow/no-op · 2 block with stderr message
```

## PreToolUse: deny-gates

A **PreToolUse** hook runs *before* the tool call, and its exit 2 **denies the call outright** —
the file is never read/written, and stderr tells Claude why. That's the difference from PostToolUse
(fix-it feedback after the write has landed): use PreToolUse when the action itself must not happen,
PostToolUse when the result should be checked.

Same stdin-JSON contract, same `jq` idiom, same fail-safe principle — only a genuine match may
exit 2, and a broken hook fails open (any non-2 exit allows the call). Worked example:
`guard-secrets.sh`, which denies `Read`/`Edit`/`Write`/`MultiEdit` on universally-secret files
(`.env*`, `*.env`, `*.key`, `*.pem`, SSH private keys), enforcing CONTRIBUTING's never-commit list
at access time.

Accepted limits of that guard, for the record: it intercepts **file tools only** (a `cat` via the
Bash tool still reaches the file); it matches **basenames** (a file inside a directory named `.env`
slips); and `*.key`/`*.pem` occasionally hit non-secrets (Keynote decks, public certs) — a rare,
explainable false block we accept for a tight deny list.

## Regression-guard pairing

A hook with real logic worth protecting gets a **companion hook** that runs its test suite when that
logic changes — so a regression in a gate is caught the moment it's edited. See
`style-check-test.sh` (guards `style-check.sh`) and `sync-docs-test.sh` (guards the sync-docs Python).
A new gate with a test suite should follow the same pairing.

## Wiring

Register the hook in the `hooks` → `PostToolUse` (or `PreToolUse`) array in
[`../settings.json`](../settings.json) with the appropriate tool matcher (`Edit|Write` for the
post-edit checks; `Read|Edit|Write|MultiEdit` for the secrets deny-gate) and the
`~/.claude/scripts/<name>.sh` command path. After wiring, restart Claude to load it.

**The exec bit is load-bearing.** settings.json invokes hooks by bare path, so a script
committed without `chmod +x` fails with "permission denied" on every real event — and
verifying with `bash script.sh` masks exactly that defect. Always verify by bare-path
invocation (`./scripts/<name>.sh`) and check the committed mode is `100755`
(`git ls-files -s scripts/<name>.sh`). Caught in review on 2026-07-04 after a 644 hook
shipped; the runner passed every `bash`-prefixed test while being unrunnable as wired.

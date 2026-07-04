---
name: audit
description: Run the mechanical compliance sweep — linters, format, link, exec-bit, and config-validity checks over a repo's tracked files
---

# /audit — Mechanical Compliance Sweep

One command for the mechanical half of a repo audit: deterministic tools (linters, formatters,
link/exec-bit/config checks) run over the target repo's tracked files, each reported as
`PASS`/`FAIL`/`SKIP`, exiting 0 clean, 1 on any finding, 2 on usage error. Read-only and
advisory — it never edits and never blocks. Complements `/vet` (dispatched model reviewers,
the judgment half of an audit); the two together cover a full repo audit.

## Instructions

Run `~/.claude/skills/audit/audit.sh` with the caller's flags, surface its stdout verbatim, then
summarize: the pass/fail/skip counts and which checks FAILed. On a FAIL, point at the relevant
output — deciding what (if anything) to fix is the caller's call, not this skill's.

### Arguments

The user may optionally provide:

- `--scope <path>` — target repo (default: git toplevel of cwd)
- `--tests` — also run the repo's shell suites (`scripts/tests/test_*.sh`) and pytest (off by
  default; the sweep is otherwise static)

### Process

1. Run `~/.claude/skills/audit/audit.sh`, forwarding `--scope`/`--tests` as given.
2. Surface its stdout verbatim — the per-check verdict lines and the summary line.
3. Summarize: counts (passed/failed/skipped) and which checks FAILed, if any.
4. On FAIL, point the caller at the output; do not attempt a fix unless asked.

The sweep runs 13 checks: `format-trailing-ws`, `format-crlf`, `format-final-newline`,
`format-tabs` (formatting); `shellcheck`, `ruff` (linters); `markdownlint` (opt-in, see Rules);
`md-links` (relative link/anchor validity); `exec-bit` (tracked shebang files must be
executable); `json`, `toml` (config validity); `sync-docs` (index-table drift); and `tests`
(shell suites + pytest, only with `--tests`).

### Rules

- **Read-only** — never auto-fix a FAIL without the caller asking; `/audit` only runs the sweep
  and reports.
- A tool that isn't installed surfaces as `SKIP`, not a silent pass — always relay `SKIP`s; each
  is a coverage gap, not a clean bill of health.
- `markdownlint` only runs in repos opted in via `.markdownlint-cli2.jsonc` — opting in is a
  per-repo decision this skill reports, never makes.
- Never run `/audit` as a substitute for `/vet` when skills or agents were edited — the sweep
  checks mechanics only; it has no judgment about content or structure.

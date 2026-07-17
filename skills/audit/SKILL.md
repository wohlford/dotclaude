---
name: audit
description: Run the mechanical compliance sweep — linters, format, link, exec-bit, and config-validity checks over a repo's tracked files; repos exclude generated paths via .auditignore
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
3. **On a non-zero exit with no verdict lines, relay stderr and the exit code — never an empty
   summary.** `audit.sh` returns 2 for a usage error (missing `--scope` value, unknown flag, or a
   scope that isn't a git repo) after printing to stderr and before any check runs, so stdout is
   empty. Report the exit code and whatever stderr said. **An empty sweep is not a clean sweep** —
   and note that the stderr is only the bare usage synopsis, so on exit 2 say which flags were
   actually passed; the synopsis alone does not identify the cause.
4. Summarize: counts (passed/failed/skipped) and which checks FAILed, if any.
5. On FAIL, point the caller at the output; do not attempt a fix unless asked.

The sweep runs 13 checks: `format-trailing-ws`, `format-crlf`, `format-final-newline`,
`format-tabs` (formatting); `shellcheck`, `ruff` (linters); `markdownlint` (opt-in, see Rules);
`md-links` (relative link/anchor validity); `exec-bit` (tracked shebang files must be
executable); `json`, `toml` (config validity); `sync-docs` (index-table drift); and `tests`
(shell suites + pytest, only with `--tests`).

### .auditignore

A `<scope>/.auditignore` file is an opt-in exclusion mechanism: one **git pathspec glob** per
line, `#` comments and blank lines ignored, leading/trailing whitespace trimmed. Each pattern
becomes a `:(exclude)` pathspec — this mirrors the repo's own `.markdownlint-cli2.jsonc`
`ignores` model. **`!` negation is not supported in v1.**

It scopes ONLY the five text-content checks: `format-trailing-ws`, `format-crlf`,
`format-final-newline`, `format-tabs`, `md-links`. Code/config checks (`shellcheck`, `ruff`,
`markdownlint`, `exec-bit`, `json`, `toml`, `sync-docs`, `tests`) are deliberately never scoped
by it — a repo cannot hide a broken tracked `.json` or a non-executable shebang file from the
audit.

An absent `.auditignore` is fully backward compatible — behavior is identical to before it
existed. A present-but-empty file (or one containing only comments/blank lines) behaves exactly
like an absent one: zero active patterns, no visibility line. When at least one active pattern
exists, the run prints `(.auditignore: N exclude pattern(s) active)` up front, so a PASS over a
reduced file set is visibly different from a PASS over everything.

Each pattern is probed against git before use. An invalid one — an anchored gitignore-style
pattern (e.g. `/gen/*`) or one that escapes the repo (e.g. `../outside`) — makes git reject the
pathspec outright, so the sweep never trusts it silently: it reports `FAIL auditignore` naming
every bad pattern (guaranteeing exit 1, never a false-clean run), then still sweeps using only
the remaining valid patterns — one broken exclude line degrades, it doesn't blind the whole run.

A document-store or generated-heavy repo should add a `.auditignore` — otherwise the format
sweep will be slow and will FAIL on intentionally-nonconforming files (generated transcripts
with load-bearing trailing whitespace, vendored dumps, etc.).

### Rules

- **Read-only** — never auto-fix a FAIL without the caller asking; `/audit` only runs the sweep
  and reports.
- Offender output is capped at 50 lines per check (global to every check, not an `.auditignore`
  feature), ending with `… more (run the underlying tool for the full list)` when more exist.
- A tool that isn't installed surfaces as `SKIP`, not a silent pass — always relay `SKIP`s; each
  is a coverage gap, not a clean bill of health.
- `markdownlint` only runs in repos opted in via `.markdownlint-cli2.jsonc` — opting in is a
  per-repo decision this skill reports, never makes.
- Never run `/audit` as a substitute for `/vet` when skills or agents were edited — the sweep
  checks mechanics only; it has no judgment about content or structure.

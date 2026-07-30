---
name: audit
description: Run the mechanical compliance sweep — linters, format, link, exec-bit, and config-validity checks over a repo's tracked files; repos exclude generated paths via .auditignore
---

# /audit — Mechanical Compliance Sweep

One command for the mechanical half of a repo audit: deterministic tools (linters, formatters,
link/exec-bit/config checks) run over the target repo's tracked files, each reported as
`PASS`/`FAIL`/`SKIP`, exiting 0 clean, 1 on any finding, 2 on usage error. Every run it exits
from itself ends with a machine-readable `RESULT:` line carrying its own exit code. Read-only
and advisory — it never edits and never blocks. Complements `/vet` (dispatched model reviewers,
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
2. Surface its stdout verbatim — on a normal run the per-check verdict lines, the summary
   line, and the final `RESULT:` line; on a usage error, stdout is the `RESULT:` line
   alone (no per-check lines, no summary), with the synopsis on stderr.
3. **Read the verdict from the `RESULT:` line, not from the absence of `FAIL`.** Every run
   `audit.sh` exits from itself ends with
   `RESULT: <STATUS> rc=<n> checks=<pass>/<fail>/<skip>` as its last line of stdout. Clear
   the sweep **only** on `RESULT: PASS rc=0` — an allowlist, so any value not listed here
   reads as not-clean:

   | Line | Meaning |
   | :--- | :--- |
   | `RESULT: PASS rc=0 …` | Completed, zero FAILs. **The only clean verdict.** |
   | `RESULT: FAIL rc=1 …` | Completed, at least one FAIL. |
   | `RESULT: ERROR rc=2 …` | Usage error — no sweep ran. Relay stderr; on exit 2 say which flags were actually passed, since the stderr synopsis alone does not identify the cause. |
   | `RESULT: INCOMPLETE rc=<n> …` | The run began but did not finish — a catchable signal, or any other abnormal exit once the sweep had started. The counts are a prefix, not a result. |
   | *(no line at all)* | The run did not complete — killed uncatchably, or the output was lost. |

   **Match the whole `RESULT: PASS rc=0` prefix, never the bare word `PASS`.** Every
   passing check prints its own `PASS <check-name>` line, so the literal string `PASS`
   appears many times in a run that overall FAILed or died partway through — matching on
   the word alone clears almost anything. **And an absent `RESULT:` line NEVER means
   clean.** `rc=` is
   inside the line precisely so a piped or backgrounded run cannot separate the verdict
   from its status — read the line, not the harness's report of the exit code.
4. Summarize: counts (passed/failed/skipped) and which checks FAILed, if any.
5. On any verdict other than `RESULT: PASS rc=0`, say plainly that the sweep did not
   clear and point the caller at the relevant output — the offender lines for `FAIL`, the
   stderr synopsis and the flags actually passed for `ERROR`, and the fact that the counts
   are only a prefix for `INCOMPLETE` or an absent line. **Do not retry automatically**,
   and do not attempt a fix unless asked. An `INCOMPLETE` or missing verdict is a reason
   to re-run deliberately, not to assume the sweep would have passed.

The sweep runs 15 checks: `format-trailing-ws`, `format-crlf`, `format-final-newline`,
`format-tabs` (formatting); `shellcheck`, `ruff` (linters); `markdownlint` (opt-in, see Rules);
`md-links` (relative link/anchor validity); `exec-bit` (tracked shebang files must be
executable); `json`, `toml` (config validity); `sync-docs` (index-table drift); `tests`
(shell suites + pytest); `hermetic` (the suite left the working tree as it found it); and
`hermetic-outside` (the suite wrote nothing under the Claude config root). The last three run
only with `--tests`.

### Hermeticity — what a suite run leaves behind

`--tests` is the only part of the sweep that executes repo code, so it is the only part that can
write. Two checks bracket it, because a suite can pollute in two directions and each is invisible
to the other's instrument:

- **`hermetic`** compares `git status --porcelain -uall` either side of the run. It *compares*
  rather than demanding a clean tree, so a tree you had already dirtied is not a finding — only
  what the run itself changed. Ignored paths are deliberately out of scope: this mirrors the
  instrument a publish path's clean-tree precondition uses, and leaves `__pycache__/` alone.
- **`hermetic-outside`** watches the Claude config root (`$CLAUDE_CONFIG_DIR`, else `~/.claude`),
  resolved physically and walked with `find -L` — the root is typically a symlink, and a probe that
  fails to follow it reports zero files, which reads exactly like "nothing changed". Session-state
  directories that legitimately churn are exempt by name; **everything else is watched by default**,
  so a directory nobody anticipated is covered. A timestamp marker catches *appends*, which leave the
  path set unchanged. It `SKIP`s when the root lies inside the scope (`hermetic` covers that) and
  **`FAIL`s if it ever watches zero files** — a probe that measured nothing is never a clean result.

`hermetic-outside` attributes to the suite anything that changed under the root during the window.
Run non-interactively that is exact; run alongside a live session that also writes there, a `FAIL`
may name that session's work. It never fails the other way: nothing turns a real write into a `PASS`.

### .auditignore

A `<scope>/.auditignore` file is an opt-in exclusion mechanism: one **git pathspec glob** per
line, `#` comments and blank lines ignored, leading/trailing whitespace trimmed. Each pattern
becomes a `:(exclude)` pathspec — this mirrors the repo's own `.markdownlint-cli2.jsonc`
`ignores` model. **`!` negation is not supported in v1.**

It scopes ONLY the five text-content checks: `format-trailing-ws`, `format-crlf`,
`format-final-newline`, `format-tabs`, `md-links`. Code/config checks (`shellcheck`, `ruff`,
`markdownlint`, `exec-bit`, `json`, `toml`, `sync-docs`, `tests`, `hermetic`,
`hermetic-outside`) are deliberately never scoped by it — a repo cannot hide a broken tracked
`.json`, a non-executable shebang file, or an artifact its own suite dropped from the audit.

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
- **A clean verdict on a killed run is structurally impossible, not merely unlikely.**
  `PASS` and `FAIL` are emitted by the sweep itself, on the line after the last check;
  the exit handler that covers every abnormal path can emit only `ERROR` or `INCOMPLETE`.
  So no signal — trapped or not — can produce `RESULT: PASS`. This is why the guarantee
  does not depend on enumerating which signals are trapped.
- **What the `RESULT:` line does and does not guarantee.** It is emitted on every exit
  `audit.sh` performs itself — normal completion *and* the usage-error `exit 2`. On a
  catchable signal it is emitted only once bash can run the trap, which requires the
  current foreground child to have exited first: measured ~29s late when only the script
  was signalled while a child ran, and immediately when the whole process group was
  signalled. It can never be emitted on SIGKILL, and SIGQUIT was measured to skip the
  handler entirely. So its presence is a real verdict and its absence is a real alarm,
  but "the line always appears" is not a claim this makes.
- **On an untrapped signal the `rc=` field reads 0 — the status does not.** Only
  HUP/INT/TERM are trapped; any other catchable fatal signal leaves `$?` inside the exit
  handler reading `0` rather than `128+n` (measured: SIGUSR1 kills the process with 158
  while the handler sees 0). The line then says `RESULT: INCOMPLETE rc=0`, which never
  clears the allowlist, so the sweep is still correctly read as not-clean — but do not
  treat `rc=` as authoritative on a line whose status is `INCOMPLETE`.
- **The signal traps convert death-by-signal into a normal exit** carrying the same
  numeric status (TERM→143, INT→130, HUP→129). Every consumer reads `$?`, so nothing
  breaks, but a wrapper loop no longer observes that the sweep was *signalled* — Ctrl-C
  during a looped audit ends that iteration rather than the loop. The status stays
  non-zero, so `&&` chains still short-circuit. Under `nohup`, SIGHUP is ignored before
  the script starts and so cannot be trapped at all: a HUP then has no effect whatever —
  the run continues to completion and emits a normal verdict.
- **`checks=<pass>/<fail>/<skip>` counts emitted verdict lines, not the 15 named checks.**
  Two things make the totals differ from 15: an invalid `.auditignore` pattern adds a
  `FAIL auditignore` that is not one of the 15, and without `--tests` none of `tests`,
  `hermetic`, or `hermetic-outside` emits a line at all — so a static sweep totals 12 and a
  full one 15. Compare counts only across runs invoked with the same flags.

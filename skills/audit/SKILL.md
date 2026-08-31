---
name: audit
description: Run the mechanical compliance sweep — linters, format, link, exec-bit, and config-validity checks over a repo's tracked files; repos exclude generated paths via .auditignore. Mechanical only — the test suite is a SEPARATE requirement and runs only under --tests, so a plain PASS is never evidence the suite passed
---

# /audit — Mechanical Compliance Sweep

One command for the mechanical half of a repo audit: deterministic tools (linters, formatters,
link/exec-bit/config checks) run over the target repo's tracked files — with four exceptions that
also see UNTRACKED, unignored files: `hermetic` and `mutation-anchors` deliberately, `sync-docs`
and `script-headers` incidentally, because the tools behind them discover by filesystem glob
rather than from the index (`audit.sh`'s own header gives the reason for each). So a `FAIL` can
name a file you have not staged yet. Each check is reported as
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
   clear and point the caller at the relevant output — the offender lines for `FAIL` (and for
   `FAIL tests`, the `full output:` directory, which holds each failing suite's complete text;
   relay it verbatim, since it may read `(unavailable — …)` rather than a path, and a path that
   does resolve is temp-rooted and may be aged out by the time anyone opens it), the
   stderr synopsis and the flags actually passed for `ERROR`, and the fact that the counts
   are only a prefix for `INCOMPLETE` or an absent line. **Do not retry automatically**,
   and do not attempt a fix unless asked. An `INCOMPLETE` or missing verdict is a reason
   to re-run deliberately, not to assume the sweep would have passed.

The sweep runs 19 checks: `format-trailing-ws`, `format-crlf`, `format-final-newline`,
`format-tabs` (formatting); `shellcheck`, `ruff` (linters); `markdownlint` (opt-in, see Rules);
`md-links` (relative link/anchor validity); `env-claims` (opt-in, see Rules; CLAUDE.md's
documented environment claims still hold on this machine);
`exec-bit` (tracked shebang files must be executable);
`json`, `toml` (config validity); `sync-docs` (index-table drift);
`script-headers` (opt-in, see Rules; every scripts-index script's bound `# Purpose:` header reads
as one self-contained line);
`mutation-anchors` (every mutation campaign's anchor still resolves exactly once in the file it
mutates); `pre-push-installed` (adopted repos: the tracked `git-hooks/pre-push` is installed at
the resolved hooks path, executable, and matches its source); `tests` (shell suites + pytest);
`hermetic` (the suite left the working tree as it found it); and `hermetic-outside` (the suite
wrote nothing under the Claude config root). The last three run only with `--tests`.

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
  path set unchanged. Its **one** `SKIP` is the root lying inside the scope, where `hermetic` covers
  it; every other way of not measuring is a `FAIL`, since a probe that measured nothing is never a
  clean result. So it **`FAIL`s if it watches zero files** under a root that exists, if it cannot
  create its marker, if any watched root cannot be enumerated — the last aggregated across
  roots, because reporting only the last root's status made a failure invisible unless it happened
  to sort last — and if the configured root **exists but cannot be resolved**, which is a third way
  of measuring nothing rather than a fourth kind of absence. An **absent** root is not skipped
  either: absent-and-still-absent is a verified `PASS`, while a root the suite **created** is the
  outside write it always was and now `FAIL`s. The `PASS` there is vacuous-but-verified, and it is
  narrow on purpose — a path that is merely unusable (not a directory, or a directory that cannot
  be traversed) resolves to the same empty string as one that does not exist, so gating on
  resolution alone would have handed a positive verdict to exactly the unmeasured probe this check
  exists to reject.

`hermetic-outside` attributes to the suite anything that changed under the root during the window.
Run non-interactively that is exact; run alongside a live session that also writes there, a `FAIL`
may name that session's work — and a session *deleting* under a watched root mid-walk can surface
as an `unprovable` enumeration failure rather than an attribution one, so quiesce the tree and
re-run once before reading a lone `unprovable` as a broken instrument. It never fails the other
way: nothing turns a real write into a `PASS`.
One bound it does **not** claim: a suite that creates a path and deletes it again reads as clean —
a path-set comparison cannot see a create-then-delete, and the marker only dates files that survive.

**Not every hermeticity `FAIL` is pollution, and the reason text after the em dash says which.**
Only `the suite changed the working tree` and `the suite wrote outside the scope, under <root>`
mean the suite wrote. The rest report that the sweep could not measure, or that its watch had been
weakened — hunting for a dirty tree will find nothing. **Do not use the indented block as the
signal:** four of the five print one, and on an instrument failure it carries the tool's own error
text rather than offending paths. `hermetic` emits `could not read the working tree BEFORE the
suite ran` and its `AFTER` twin (both ends are guarded: a symmetric failure would compare equal and
read as a clean pass). `hermetic-outside` emits `could not snapshot the config root BEFORE the
suite ran`, `watched 0 files under <root> — the probe measured nothing`, or `a protected path was
moved onto the churn exemption list`. Repair the instrument, then re-run.

### .auditignore

A `<scope>/.auditignore` file is an opt-in exclusion mechanism: one **git pathspec glob** per
line, `#` comments and blank lines ignored, leading/trailing whitespace trimmed. Each pattern
becomes a `:(exclude)` pathspec — this mirrors the repo's own `.markdownlint-cli2.jsonc`
`ignores` model. **`!` negation is not supported in v1.**

It scopes ONLY the five text-content checks: `format-trailing-ws`, `format-crlf`,
`format-final-newline`, `format-tabs`, `md-links`. Code/config checks (`shellcheck`, `ruff`,
`markdownlint`, `env-claims`, `exec-bit`, `json`, `toml`, `sync-docs`, `script-headers`,
`mutation-anchors`, `pre-push-installed`, `tests`, `hermetic`, `hermetic-outside`) are
deliberately never scoped by it — a repo cannot hide a broken tracked `.json`, a non-executable
shebang file, or an artifact its own suite dropped from the audit.

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
- Offender output is capped at 50 lines per check (not an `.auditignore` feature), ending with
  `… more (run the underlying tool for the full list)` when more exist. **`tests` and `ruff` do
  NOT use that cap** — see the next bullet for what each does instead, and why.
- **The flat cap is correct only where one offender is one self-naming line.** That is
  `format-trailing-ws`, `format-crlf`, `format-final-newline`, `format-tabs` and `exec-bit`: the
  first 50 are representative, the rest are more of the same, and no file is lost entirely.
- **Everywhere else the cap is a known, UNFIXED defect — never read a capped FAIL as complete.**
  `toml`, `json`, `shellcheck` and `md-links` emit several lines per offender, so a flat cap drops
  whole files (measured: six invalid `.toml`, four named, two never named at all). `markdownlint`,
  `sync-docs`, `env-claims` and `script-headers` pipe a whole tool's stdout, preamble and summary
  included.
  **`mutation-anchors` has `ruff`'s exact shape but has not crossed yet**: it prints a
  `campaign: <file>` line for EVERY campaign, passing or failing, so its padding grows with the
  campaign COUNT rather than with the findings. Measured today: 18 campaigns, so 18 padding lines
  precede any finding and 32 of the cap remain — it does not yet hide anything. It starts to at
  ~50 campaigns, and nothing signals the crossing. This list is deliberately explicit: three
  earlier drafts stated a rule quantified over "every other check" and were wrong each time.
- **The two exceptions, and why each is one.** `tests` holds another tool's *entire* stdout, mostly
  passes, whose interesting lines are the FAILURES — and suites print passes as they go, so a
  head-cap there reliably keeps the useless half. `ruff` had the same shape, and was measured
  failing the same way: both its sub-tools print on success (`All checks passed!`, `1 file already
  formatted`), so once the repo held more `.py` files than the cap has lines, the success padding
  alone filled it and the real failure became *structurally* unprintable — while the 50 lines that
  did print looked exactly like a clean run.
  It now collects only from invocations that FAILED and emits a per-file, per-sub-tool header with
  a bounded excerpt, so its block ends with `… N more line(s) — run: …` rather than the shared
  notice above. Past an aggregate excerpt budget a block degrades to its header plus
  `… excerpt omitted (aggregate budget) — run: …`, and the detail closes by naming how many
  invocations were degraded — so a saturated run says so rather than truncating silently. Both exceptions share one rule: **every failing unit is always named; only its
  excerpt is bounded** — a flat cap would drop whole files, since a ruff diagnostic names its file
  once per block rather than once per line.
- **`FAIL tests` therefore preserves the complete output** of each failing suite in a temporary
  directory, reported once as `full output: <dir>` **before** the per-suite excerpts, and shows a
  bounded excerpt of failure-shaped lines inline (falling back to the tail, and marking its own
  truncation, so a partial excerpt never reads as complete). Every failing suite is always named;
  only its excerpt is bounded. The directory is created only when a suite actually fails, and never
  inside the scope — **if it could not be created the line reads `(unavailable — …)` instead of a
  path**, so treat `full output:` as a path only after checking. Preservation is reported per
  suite, not per run: a suite whose own write failed is marked `(full output NOT preserved)` on its
  header, so the directory existing is never taken as proof that every suite's text is in it. **These artifacts are not
  durable** either: they live under the system temp root and are aged out, so a path read from an
  old transcript may be gone.
- A tool that isn't installed surfaces as `SKIP` only when nothing needed it — always relay
  `SKIP`s; each is a coverage gap, not a clean bill of health. When applicable work exists
  (e.g. `test_*.py` files tracked) and the tool that would run it is missing, that is instrument
  failure, not inapplicability: it surfaces as `FAIL … unprovable`, since a probe that measured
  nothing is never a clean result.
- `markdownlint` only runs in repos opted in via `.markdownlint-cli2.jsonc` — opting in is a
  per-repo decision this skill reports, never makes.
- `env-claims` only runs where the audited repo ships its own `scripts/env-claims-check.py` —
  `SKIP` elsewhere, which is the expected verdict in most repos. The checker is resolved from
  that repo rather than from this skill's own installation on purpose: its claim table is written
  against that repo's own `CLAUDE.md`, so an installation-resolved checker would grade every
  other repo against the wrong document. It also `SKIP`s when `python3` is absent, and when the
  documented environment is not present on the machine — that last one is what stops a clone of a
  published repo reporting a false `FAIL` for claims that were only ever true elsewhere.
- `script-headers` only runs where the audited repo ships both
  `scripts/script-header-check.py` and a `<!-- sync:scripts -->` marker — `SKIP` elsewhere,
  which is the expected verdict in a repo that has not adopted the scripts-index convention.
  It also `SKIP`s when `python3` is absent — a missing *interpreter* reads as `SKIP`, unlike the
  missing *runner* the general rule above calls unprovable, and `env-claims` draws the same line.
  It reports three failure shapes, and the offender line names which: **`missing`** (no
  `# Purpose:` line at all), **`out-of-window`** (one exists, but past the first 10 lines, which
  is all `BashHeaderExtractor` reads), and **`wrapped`** (the header continues onto the next
  comment line, which the extractor silently drops — so the generated row truncates mid-clause).
  It discovers by filesystem glob, so it sees **untracked** scripts too. A checker that cannot
  reach a verdict (exit code 2 or higher) reports `FAIL … unprovable` — an instrument failure,
  never a finding, the same distinction `env-claims` draws. Coverage is the **scripts index only** (`ScriptsHandler`'s
  `scripts/*.sh` / `scripts/*.py` population) — the same `BashHeaderExtractor` also feeds the
  hooks table, which this check does not read. Complete today by overlap, not by construction:
  every registered hook resolves inside `scripts/*.{sh,py}`, but a hook registered from
  `scripts/lib/` or `skills/*/` would sit outside the scripts index and go unchecked.
- `pre-push-installed` only runs in adopted repos (any `refs/heads/*` branch carrying a
  tracked `.publication.toml`) — `SKIP` elsewhere. It answers registration only: whether the
  hook that would enforce the push boundary is actually installed, executable, and current.
  It says nothing about whether that hook actually blocks a push — that is
  `scripts/tests/test_pre_push_hook.sh`'s job, not this sweep's.
  It emits a **second** `SKIP`, in an adopted repo, when a source mismatch is attributable to
  which commit is checked out rather than to a stale install — the worktree hook matches
  `HEAD`'s tracked blob and the installed hook matches `refs/heads/dev`'s, so nothing installed
  is out of date. That is what per-brick auditing of a historical tree produces, and the `SKIP`
  names its reason inline. Accepted residual, deliberately not excluded: an unlanded feature
  branch that edited the hook satisfies the same two conditions, so it takes the same `SKIP` —
  the advice it carries (do **not** install this tree's hook) is the right advice there too, and
  a stale install resurfaces as a `FAIL` once the branch lands, because the `refs/heads/dev`
  side moves and the installed side does not. Only the source comparison is set aside: the
  not-installed, symlink, not-regular and not-executable arms judge the live hooks directory and
  keep firing, because a historical audit runs at exactly the moment before real pushes.
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
- **`checks=<pass>/<fail>/<skip>` counts emitted verdict lines, not the 19 named checks.**
  Two things make the totals differ from 19: an invalid `.auditignore` pattern adds a
  `FAIL auditignore` that is not one of the 19, and without `--tests` none of `tests`,
  `hermetic`, or `hermetic-outside` emits a line at all — so a static sweep totals 16 and a
  full one 19. Compare counts only across runs invoked with the same flags.

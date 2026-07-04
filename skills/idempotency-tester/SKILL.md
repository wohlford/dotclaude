---
name: idempotency-tester
description: Verify a script is idempotent by running it twice in an isolated sandbox and diffing the resulting state
---

# /idempotency-tester — Idempotency Compliance Check

Run a target script twice in an env-redirected sandbox and confirm the second run changes nothing —
proving `f(f(x)) == f(x)`. Built for other skills/processes to call as an idempotency-compliance gate.
The work is done by the deterministic helper `idempotency-test.sh`; this skill is the thin wrapper.

## Instructions

Invoke the helper directly (it is the engine — do not reason about idempotency by hand):

```bash
bash ~/.claude/skills/idempotency-tester/idempotency-test.sh [opts] -- <target> [target-args…]
```

**Exit codes:** `0` idempotent · `1` not idempotent — when the two runs' **exit codes** differ, only
an exit-parity-mismatch message is printed to stderr (no diff); when **state** diverges, a `diff` is
printed to stderr · `2` harness or target error (bad usage — including a missing target or `--seed` dir —
`--setup` failed, run 1 failed without `--allow-nonzero`).

### Arguments
- `--seed DIR` — copy `DIR/.` into the sandbox before run 1 (give the target the state it acts on).
- `--setup CMD` — run once after seeding, before run 1 (repeatable); non-zero aborts with 2. Setup
  commands run with only the built-in sandbox env — `--env` values apply to the target runs only.
- `--stdin FILE` — replay FILE to the target's stdin on both runs (stdin-driven targets; default `/dev/null`).
- `--env K=V` — extra env var, applied after the built-in redirection (repeatable). May punch
  through the sandbox: a value like `HOME=...` overrides the `HOME`/`XDG`/`TMPDIR` redirection,
  letting the target act on the real filesystem outside the sandbox.
- `--git` — `git init && add && commit` the seed (pinned identity/date) before run 1.
- `--ignore GLOB` — exclude matching paths from the comparison (repeatable).
- `--runner CMD` — override interpreter detection (else: extension `.sh/.py/.js` picks the
  interpreter → an executable file is run directly, so the OS honors its shebang → anything else
  errors with exit `2` asking for `--runner`).
- `--allow-nonzero` — don't abort when run 1 exits non-zero (still requires run-2 exit parity).
- `--keep` — keep the sandbox on exit and print its path.
- `{{SANDBOX}}` (in args / `--env` values / the *contents* of the `--stdin` file — not its
  path) expands to the sandbox path;
  `$IDEMPOTENCY_SANDBOX` is exported into the target/setup environment.

### How another skill or process calls it
```bash
bash ~/.claude/skills/idempotency-tester/idempotency-test.sh \
  --seed skills/sync-docs/tests/fixtures/dotclaude-shaped -- skills/sync-docs/sync_docs.py --scope .
```

### Process

1. Prepare the target script and any seed state it needs (a `--seed` directory, `--setup` commands,
   a `--stdin` file).
2. Invoke `idempotency-test.sh` with the appropriate options, then `--`, the target, and its args.
3. Interpret the exit code: `0` idempotent · `1` not idempotent · `2` setup or usage error.
4. On exit `1`, review the stderr output to identify the divergence — either an exit-parity-mismatch
   message (the two runs exited differently) or a `diff` of the state between run 1 and run 2.
5. On exit `2`, read the stderr message (usage, `--setup`, or runner error) and fix the invocation
   or `--setup` before retrying — the run was not evaluated for idempotency.

### Rules

- Model-invocable by design (other skills call it as a compliance check); the engine is deterministic —
  never wrap it in a subagent.
- Extend `--runner`/extension detection to add a language; never special-case an individual target.

## What counts as state
A per-file, **content-only** manifest of the sandbox (`work/` + redirected `home/`/`xdg/`): file
contents (sha256) + exec bit, symlink targets (**never dereferenced**), and directory presence. A `.git`
is compared by synthesized porcelain (`status`/`HEAD`/refs), not raw bytes. **Excluded:** mtimes,
stdout/stderr (a convergent script legitimately prints differently on run 2), and `tmp/`. A fixed
built-in ignore set is always applied on top of `--ignore` globs: `__pycache__`, `*.pyc`,
`.pytest_cache`, `.mypy_cache`, `.ruff_cache`, `.DS_Store`.

## Trust boundary & limits (read before relying on it)
- **Caller-trusted:** it executes the arbitrary `target`/`--setup`/`--runner` commands you pass. Containment
  is env-redirection of `HOME`/`XDG`/`TMPDIR` (+ `GIT_CONFIG_NOSYSTEM`) — **not a hard sandbox**; a target
  using absolute paths can still affect the real system.
- Commands run via this harness **bypass PreToolUse command-pattern hooks**.
- **Converge-then-clean caveat:** under `--allow-nonzero`, a target that exits `1` then `0` with identical
  state is still flagged non-idempotent — exit parity is mandatory in v1.
- A **self-modifying** target (one that edits its own file) changes state outside the measured sandbox.

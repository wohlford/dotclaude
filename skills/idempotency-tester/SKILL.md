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

**Exit codes:** `0` idempotent · `1` not idempotent (a `diff` is printed to stderr) · `2` harness or
target error (bad usage, `--setup` failed, run 1 failed without `--allow-nonzero`).

### Options
- `--seed DIR` — copy `DIR/.` into the sandbox before run 1 (give the target the state it acts on).
- `--setup CMD` — run once after seeding, before run 1 (repeatable); non-zero aborts with 2.
- `--stdin FILE` — replay FILE to the target's stdin on both runs (stdin-driven targets; default `/dev/null`).
- `--env K=V` — extra env var, applied after the built-in redirection (repeatable; may punch through).
- `--git` — `git init && add && commit` the seed (pinned identity/date) before run 1.
- `--ignore GLOB` — exclude matching paths from the comparison (repeatable).
- `--runner CMD` — override interpreter detection (else: extension `.sh/.py/.js` → shebang → executable).
- `--allow-nonzero` — don't abort when run 1 exits non-zero (still requires run-2 exit parity).
- `--keep` — keep the sandbox on exit and print its path.
- `{{SANDBOX}}` (in args / the `--stdin` file / `--env` values) expands to the sandbox path;
  `$IDEMPOTENCY_SANDBOX` is exported into the target/setup environment.

### How another skill or process calls it
```bash
bash ~/.claude/skills/idempotency-tester/idempotency-test.sh \
  --seed skills/sync-docs/tests/fixtures/dotclaude-shaped -- skills/sync-docs/sync_docs.py --scope .
```

## What counts as state
A per-file, **content-only** manifest of the sandbox (`work/` + redirected `home/`/`xdg/`): file
contents (sha256) + exec bit, symlink targets (**never dereferenced**), and directory presence. A `.git`
is compared by synthesized porcelain (`status`/`HEAD`/refs), not raw bytes. **Excluded:** mtimes,
stdout/stderr (a convergent script legitimately prints differently on run 2), and `tmp/`.

## Trust boundary & limits (read before relying on it)
- **Caller-trusted:** it executes the arbitrary `target`/`--setup`/`--runner` commands you pass. Containment
  is env-redirection of `HOME`/`XDG`/`TMPDIR` (+ `GIT_CONFIG_NOSYSTEM`) — **not a hard sandbox**; a target
  using absolute paths can still affect the real system.
- Commands run via this harness **bypass PreToolUse command-pattern hooks** (e.g. the git-timing-guard).
- **Converge-then-clean caveat:** under `--allow-nonzero`, a target that exits `1` then `0` with identical
  state is still flagged non-idempotent — exit parity is mandatory in v1.
- A **self-modifying** target (one that edits its own file) changes state outside the measured sandbox.

## Rules
- Model-invocable by design (other skills call it as a compliance check); the engine is deterministic —
  never wrap it in a subagent.
- Extend `--runner`/extension detection to add a language; never special-case an individual target.

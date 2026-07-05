# Testing conventions

How tests are organized in this repo. The conventions are shaped by one fact: a single `pytest` run
often spans **many** skills' test suites at once, so the suites must not collide.

## Layout

| Suite kind | Lives in |
|---|---|
| Python (pytest) for a skill | `skills/<name>/tests/` |
| Bash harness for a script | `scripts/tests/` |

## The uniquely-named-helper-module rule

When a skill's tests import shared helpers — a wrapper that invokes the script under test, path
constants, a git wrapper — put them in a module named for the skill (`<skill>_helpers.py`), **not**
`conftest.py` and **not** a generic name like `helpers.py`.

Why: when `pytest skills/foo/tests skills/bar/tests …` runs across suites in one invocation, two
**imported** modules that share a name (two `helpers.py`, or `from conftest import …` in two suites)
collide on the module name and the run fails. pytest handles per-directory `conftest.py` fixtures fine
— the clash is on *imported* modules — so a shared helper gets a unique name per skill. So:

- **`conftest.py`** holds *only* pytest fixtures (they're discovered by path, no import needed).
- **`<skill>_helpers.py`** holds everything imported with `from <skill>_helpers import …`.

In practice most suites avoid the problem entirely: the `sync-docs` suite imports its own
package modules directly (`import extractors`, `import handlers`, `import sync_docs`), which are
already uniquely named, and keeps fixtures in `conftest.py`.

## Fixtures

Tests that need a repo-shaped input use **static fixture trees**, not a live checkout. The
`sync-docs` suite runs its marker and handler code against fake repo trees under
`skills/sync-docs/tests/fixtures/` (e.g. a `dotclaude-shaped` tree), so a test never depends on the
real working copy. The `idempotency-tester` suite builds a throwaway working directory and drives the
script through `subprocess`, asserting on what it observes.

Assert on **exit code and observable state** (file contents, generated tables, emitted paths), not on
prose — tool wording differs across platforms (BSD vs GNU), so matching messages makes tests brittle.

## Style of test code

- `ruff format` and the `I` (import order) + `B` (bugbear) lint rules **apply** to test code.
- The **type-hint and Google-style-docstring rules do not** — test modules under `tests/`,
  `conftest.py`, and fixtures are exempt. See the test-code exemption in
  [`STYLE.md`](STYLE.md#python) (this doc does not restate it — STYLE is canonical).
- Bash test harnesses may use `set -uo pipefail` (dropping `-e`) so one failing assertion doesn't
  abort the whole suite — the test-runner exemption in STYLE.md.

## Interactions with the tooling

- **`/vet --all --tests`** routes test code to the `style-reviewer` agent — which honors the exemption
  above, so it won't flag missing type hints on a fixture.
- **The `*-test.sh` hooks** (`style-check-test.sh`, `sync-docs-test.sh`) run the matching suite when
  its source changes — the regression-guard pairing described in
  [`scripts/HOOKS.md`](scripts/HOOKS.md).
- **Keep test litter out of commits:** `__pycache__/` and `.pytest_cache/` are gitignored; don't stage
  them.

## Running

```bash
# one suite
python3 -m pytest skills/sync-docs/tests -q
# the combined run (why the naming rule exists)
python3 -m pytest skills/sync-docs/tests skills/idempotency-tester/tests -q
```

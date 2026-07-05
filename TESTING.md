# Testing conventions

How tests are organized in this repo. The conventions are shaped by one fact: a single `pytest` run
often spans **many** skills' test suites at once, so the suites must not collide.

## Layout

| Suite kind | Lives in |
|---|---|
| Python (pytest) for a skill | `skills/<name>/tests/` |
| Bash harness for a script | `scripts/tests/` |

## The uniquely-named-helper-module rule

Each skill's tests import shared helpers — `run` (invoke the script under test), `git` (a
signing-off git wrapper), and the script-path constants — from a module named for the skill (e.g.
`recast_helpers.py`). **Not** `conftest.py`, and **not** a generic name.

Why: when `pytest skills/recast/tests skills/sync-docs/tests …` runs across suites in one invocation,
two **imported** modules that share a name (two `helpers.py`, or `from conftest import …` in two
suites) collide on the module name and the run fails. pytest handles per-directory `conftest.py`
fixtures fine — the clash is on *imported* modules — so the shared helpers get a unique name per
skill. So:

- **`conftest.py`** holds *only* pytest fixtures (they're discovered by path, no import needed).
- **`<skill>_helpers.py`** holds everything imported with `from <skill>_helpers import …`.

## Fixture-repo factories

Tests that exercise git machinery build **throwaway repos** via fixtures — e.g. `make_repo` (a plain
branch with N commits, in the recast suite). The helper's `git()` forces
`commit.gpgsign=false`/`tag.gpgsign=false` and a fixed identity, so tests never touch a real signing
key and are deterministic. Assert on **exit code and observable repo state** (SHAs, tag messages,
file contents), not on prose (BSD vs GNU git word their messages differently).

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
python3 -m pytest skills/recast/tests -q
# the combined run (why the naming rule exists)
python3 -m pytest skills/recast/tests skills/sync-docs/tests -q
```

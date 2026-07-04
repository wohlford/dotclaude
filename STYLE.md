# Code Style Guide

Universal code style and formatting standards. Full templates: templates.md

## File Format Standards

- **Encoding**: UTF-8, Unix LF line endings, final newline required
- **Trailing whitespace**: Remove from all lines
- **Indentation**: 2 spaces (no tabs) for Bash, JavaScript, YAML, and JSON; **Python uses 4 spaces** per PEP 8
- **Empty lines**: No indentation on blank lines
- **Scripts**: an executed entry-point script (run by path) carries `chmod +x` and a shebang; a **sourced library** carries no `+x` and a source guard (see Source Guard); a **Python module run via `python3 …`** needs neither

### File Naming

| Type | Convention |
|------|-----------|
| Scripts | `lowercase-with-dashes.sh` |
| Python modules | `lowercase_with_underscores.py` |
| Config files | `lowercase-with-dashes.yaml` |
| Documentation | `UPPERCASE.md` for canonical meta-docs (README, CHANGELOG, CONTRIBUTING, STYLE); `lowercase.md` otherwise |

## Shell Scripts (Bash)

### Required Header

```bash
#!/usr/bin/env bash
set -euo pipefail

# Script: name.sh
# Purpose: Brief description
# Usage: ./name.sh [options] <arguments>
```

### Key Rules

- Always `set -euo pipefail` (`-e` exit on error, `-u` exit on undefined var, `-o pipefail` exit on pipe failure)
  - Exception: test-runner harnesses may omit `-e` (use `set -uo pipefail`) so one failing assertion doesn't abort the whole suite
- Always quote variables: `"$file"`, `"${array[@]}"`
- Use `$()` not backticks
- Use `[[ ]]` for pattern/regex matches and compound conditions inside the test; plain `[ ]` is fine for simple single-condition tests (`-n`/`-z`/`-f`, string and numeric comparisons like `-gt`/`-eq`)
- Use `trap cleanup EXIT` for temp file cleanup — bash runs the EXIT trap on signal death too; add `INT TERM` only when the handler itself `exit`s (without that, the script keeps running after Ctrl-C once the trap returns)
- Use `mktemp` + `mv` for atomic writes
- Use `command -v tool` to check dependencies
- Prefer long-form flags for clarity (`--recursive`, `--force`, `--parents`) — but macOS BSD `mkdir`/`rm`/`cp`/`ln` reject GNU long flags, so in portable scripts use the ubiquitous short forms (`-p`, `-rf`, `-s`, `-a`); reserve long flags for GNU-only contexts or less-common options where the name aids the reader
- Use `printf` for formatted or user-facing output; `echo "$x" |` piped into another command is fine
- Use ANSI-C quoting (`$'...'`) for escape sequences: `$'\033[0;32m'`
- Prefer parameter expansion (`${var#pattern}`, `${var%%pattern}`, `${var%.*}`) over calling `sed`/`awk`

### Naming

| Type | Convention | Example |
|------|-----------|---------|
| Variables | `lower_snake_case` | `file_count` |
| Functions | `lower_snake_case` | `process_file` |
| Constants | `UPPER_SNAKE_CASE` | `MAX_RETRIES` |
| Files | `kebab-case.sh` | `process-data.sh` |

### Script Structure

Scripts follow a `main()` / `main "$@"` skeleton. Core logic goes in named functions, not at the top level:

```bash
#!/usr/bin/env bash
set -euo pipefail

deploy() {
  local target="$1"
  # ... core logic ...
}

main() {
  local verbose=false

  while [[ $# -gt 0 ]]; do
    case "$1" in
      --verbose) verbose=true; shift ;;
      --)        shift; break ;;
      -*)        printf 'Unknown flag: %s\n' "$1" >&2; exit 1 ;;
      *)         break ;;
    esac
  done

  deploy "$@"
}

main "$@"
```

A script that performs a **single linear task and does no command-line argument parsing** (e.g. a
PostToolUse hook that reads stdin and runs one tool) may run at the top level without the `main()`
wrapper. The skeleton is required once a script **parses flags or has more than one core function**
(rough guide: over ~30 lines of logic).

### Source Guard

Files that must be sourced (not executed) check `BASH_SOURCE` at the top:

```bash
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  printf 'Cannot run directly. Usage: source %s\n' "$0" >&2
  exit 1
fi
```

### Path Resolution

Scripts resolve their own directory with the portable `cd` + `pwd` idiom (macOS only gained BSD
`realpath` in 13/Ventura, so `realpath` is reserved for GNU-only contexts — it also resolves
symlinks, which the portable form deliberately does not):

```bash
script_dir="$(cd "$(dirname "$0")" && pwd)"
```

Sourced files use `BASH_SOURCE` instead of `$0`:

```bash
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
```


## Python

### Key Rules

- **Indentation: 4 spaces** (PEP 8)
- Line length: 88 characters (ruff/PEP 8 default). `ruff format` is authoritative: E501 stays
  off in lint, so lines the formatter leaves long — unsplittable strings and comment/docstring
  lines — are tolerated over 88
- Lint with `ruff check`; format with `ruff format` (4-space, 88-col, double quotes)
- Imports: stdlib, then third-party, then local (blank line between groups)
- Use `pathlib.Path` not `os.path`
- Use f-strings not `%` or `.format()`
- Use type hints on all function signatures
- Use Google-style docstrings: a one-liner is fine when the behavior is obvious; add the
  Args/Returns/Raises sections when a signature or contract isn't self-evident
- **Test-code exemption:** the **type-hint and Google-style-docstring** rules apply to
  shipping/library code. Any module under a `tests/` directory — test files, `conftest.py`, fixtures,
  and test-only helpers — is exempt: test functions need not be type-annotated, and a one-line
  docstring (or none) is fine (standard pytest practice). All other rules still apply.
- CLI tools: program output to stdout, diagnostics to stderr (`print(..., file=sys.stderr)` is fine); use `logging` in libraries and long-running services
- Catch specific exceptions, never bare `except:`
- Never use mutable default arguments

### Naming

| Type | Convention | Example |
|------|-----------|---------|
| Variables/functions | `lower_snake_case` | `file_count`, `process_data()` |
| Classes | `PascalCase` | `FileProcessor` |
| Constants | `UPPER_SNAKE_CASE` | `MAX_SIZE` |
| Private | `_leading_underscore` | `_internal()` |
| Modules | `lower_snake_case.py` | `data_processor.py` |

## JavaScript/Node.js

### Key Rules

- 2-space indentation, semicolons required
- Single quotes for strings
- `const` by default, `let` when reassignment needed
- Arrow functions for callbacks
- `async`/`await` over callbacks
- Trailing commas in multiline arrays/objects
- Modules: ESM (`import`/`export`); the JS template in templates.md is ESM — use CommonJS only
  when the user or host explicitly requires it (no `.js` ships in this repo yet)

### Naming

| Type | Convention | Example |
|------|-----------|---------|
| Variables/functions | `camelCase` | `fileName`, `processData()` |
| Classes | `PascalCase` | `FileProcessor` |
| Constants | `UPPER_SNAKE_CASE` | `MAX_SIZE` |
| Files | `kebab-case.js` | `file-processor.js` |

## YAML

- 2-space indentation
- Quote strings that look like numbers or booleans: `"1.0"`, `"yes"`
- Use `|` for multi-line (preserves newlines), `>` for folded (joins lines)
- Validate with `yamllint -d relaxed`

## JSON

- 2-space indentation, double quotes only, no trailing commas
- Validate with `jq .` or `python3 -m json.tool`

## Markdown

- ATX-style headers (`#`), blank line before and after
- Fenced code blocks always carry a language label (`bash`, `python`, `json`, `text`, …) — never a bare fence
- Use exactly **three** backticks to open and close a code block. The **only** exception is nesting: a block that contains another fenced block uses **four** backticks on the outer fence (always one more than the longest fence inside it), because a closing fence must be at least as long as its opener
- Consistent list markers (`-` not mixed with `*`)

## Documentation Sync

Index sections of `README.md` and `CLAUDE.md` files (skills tables, agents tables, plugin lists, content directory listings) drift from reality as a project grows. The `/sync-docs` skill regenerates these index regions from authoritative sources on demand. Hand-written prose is never touched.

**Full reference** (marker syntax, directives, handlers, extractor chain, lint mode, project
config): `skills/sync-docs/reference.md`.

## Timestamps

When emitting time-of-day timestamps into human-readable Markdown, use the canonical local format with DST-aware abbreviation.

### Canonical format

| Form | Example | When to use |
|------|---------|-------------|
| Full | `2026-04-28 12:55 CDT` | Files where date is not implicit (e.g., flat append-only logs) |
| Time-only | `12:55 CDT` | Files where surrounding context implies the date (daily notes, per-day section in dated logs) |

### Rules

- **Local time at write**, DST-aware abbreviation — `CDT` in summer, `CST` in winter. Time zone follows the device's current TZ.
- **Em-dash delimiter** (` — `) between timestamp and body in bulleted entries: `- 12:55 CDT — body text`.
- **Don't use** UTC `Z` suffix in entry bodies.
- **Don't use** ISO 8601 datetime in entry bodies (e.g., `2026-04-28T12:55:00Z`).

### Computation

Bash:

```bash
date "+%Y-%m-%d %H:%M %Z"   # full
date "+%H:%M %Z"            # time-only
```

Python:

```python
from datetime import datetime
from zoneinfo import ZoneInfo
datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %Z")              # uses OS local TZ
datetime.now(ZoneInfo("America/Chicago")).strftime("%Y-%m-%d %H:%M %Z") # explicit IANA TZ
```

### When NOT to use this format

- **Filenames** — keep `YYYY-MM-DD` ISO-only (e.g., `2026-04-28-decision-foo.md`).
- **Machine-parseable logs in code** — use full ISO 8601 with offset: `2026-04-28T12:55:00-05:00`.
- **Cross-system timestamps** — anywhere the consumer is non-human, prefer ISO 8601 with offset.

## Comments

- Explain **why**, not **what**
- Remove commented-out code (use git history)
- Markers: `TODO`, `FIXME`, `NOTE`, `HACK`, `SECURITY`

### Formatting

- One space after the delimiter — `# text` (Bash/Python/YAML), `// text` (JS); never `#text`/`//text`. JS doc-blocks use `/** … */` (JSDoc).
- Prefer a comment on its **own line, directly above** the code it explains; reserve **trailing** (end-of-line) comments for a short note about that one line.
- Trailing comments: at least one space before the delimiter — **two for Python** (PEP 8 E261): `value = 1  # why`. Aligning a run of trailing comments with extra spaces is fine when it aids readability.
- Fragments need no leading capital or trailing period; write full-sentence comments as prose. Be consistent within a file.
- Long files may use section dividers: `# ---------- label ----------`.

## Version Control

### Commit Messages

```text
<type>[(scope)][!]: <subject>
```

Single line, imperative mood, no period, under 72 characters; `!` before the colon for breaking changes. Scope is optional but encouraged. **Full conventions — the type list, `docs`-vs-`feat`, semver bump rules, and the never-commit list — live in [CONTRIBUTING.md](./CONTRIBUTING.md) (canonical).**

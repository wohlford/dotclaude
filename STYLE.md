# Code Style Guide

Universal code style and formatting standards. Full templates: [templates.md](./.claude/templates.md)

## File Format Standards

- **Encoding**: UTF-8, Unix LF line endings, final newline required
- **Trailing whitespace**: Remove from all lines
- **Indentation**: 2 spaces (no tabs) — applies to all languages including Python
- **Empty lines**: No indentation on blank lines
- **Scripts**: Must be executable (`chmod +x`)

### File Naming

| Type | Convention |
|------|-----------|
| Scripts | `lowercase-with-dashes.sh` |
| Python modules | `lowercase_with_underscores.py` |
| Config files | `lowercase-with-dashes.yaml` |
| Documentation | `UPPERCASE.md` (README, CHANGELOG) |

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
- Always quote variables: `"$file"`, `"${array[@]}"`
- Use `$()` not backticks
- Use `[[ ]]` not `[ ]`
- Use `trap cleanup EXIT` for temp file cleanup
- Use `mktemp` + `mv` for atomic writes
- Use `command -v tool` to check dependencies
- Prefer long-form flags: `--recursive` not `-r`, `--force` not `-f`, `--parents` not `-p`
- Use `printf` not `echo` for output
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

### Source Guard

Files that must be sourced (not executed) check `BASH_SOURCE` at the top:

```bash
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  printf 'Cannot run directly. Usage: source %s\n' "$0" >&2
  exit 1
fi
```

### Path Resolution

Scripts resolve their own directory using `dirname` + `realpath`:

```bash
script_dir="$(dirname "$(realpath "$0")")"
```

Sourced files use `BASH_SOURCE` instead of `$0`:

```bash
script_dir="$(dirname "$(realpath "${BASH_SOURCE[0]}")")"
```


## Python

### Key Rules

- **Indentation: 2 spaces** (overrides PEP 8's 4-space default)
- Line length: 88 characters (Black default)
- Imports: stdlib, then third-party, then local (blank line between groups)
- Use `pathlib.Path` not `os.path`
- Use f-strings not `%` or `.format()`
- Use type hints on all function signatures
- Use Google-style docstrings (Args, Returns, Raises)
- Use `logging` not `print` for diagnostics
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
- Fenced code blocks with language identifiers
- Consistent list markers (`-` not mixed with `*`)

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

## Version Control

### Commit Messages

```text
<type>[(scope)][!]: <subject>
```

Single line only. No body or footer. Lowercase, imperative mood, no period. Scope is optional but encouraged to specify the area of impact. Append `!` after the type/scope for breaking changes.

**Types:** `feat`, `fix`, `docs`, `style`, `refactor`, `perf`, `test`, `chore`, `ci`, `revert`

**`docs` vs `feat` for markdown files:**
- `docs` — repo usage documentation (README, CONTRIBUTING, setup guides)
- `feat` — markdown files consumed as AI execution context, configuration, or runtime logic (scoring rubrics, resume master, pipeline plans, agent/skill definitions)

**Examples:**

```text
feat: add user authentication system
feat(auth): add OAuth2 support
fix: handle null values in data parser
refactor(parser): extract validation logic to separate module
feat!: remove legacy API endpoint
chore(build)!: drop support for Node 6
```

### Semantic Versioning

All projects follow [Semantic Versioning 2.0.0](https://semver.org/). Versions are tracked with annotated git tags (`v1.2.3`).

**Breaking changes** use `!` after the type:

```text
feat!: remove legacy authentication API
refactor!: change config file format
```

**Version bump rules:**

| Bump | Trigger |
|------|---------|
| MAJOR | Any type with `!` suffix |
| MINOR | `feat` |
| PATCH | All other types (`fix`, `perf`, `docs`, `style`, `refactor`, `test`, `chore`, `ci`, `revert`) |

### Never Commit

Secrets (`.env`, `*.key`, `*.pem`), dependencies (`node_modules/`, `.venv/`), generated files (`.pyc`, `dist/`), OS files (`.DS_Store`), logs, temp files.

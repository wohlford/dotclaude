# Code Style Guide

Universal code style and formatting standards.

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

### Naming

| Type | Convention | Example |
|------|-----------|---------|
| Variables | `lower_snake_case` | `file_count` |
| Functions | `lower_snake_case` | `process_file` |
| Constants | `UPPER_SNAKE_CASE` | `MAX_RETRIES` |
| Files | `kebab-case.sh` | `process-data.sh` |

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

## Comments

- Explain **why**, not **what**
- Remove commented-out code (use git history)
- Markers: `TODO`, `FIXME`, `NOTE`, `HACK`, `SECURITY`

## Version Control

### Commit Messages

```text
<type>: <subject>
```

Single line only. No body or footer. Lowercase, imperative mood, no period.

**Types:** `feat`, `fix`, `docs`, `style`, `refactor`, `perf`, `test`, `chore`, `ci`, `revert`

**Examples:**

```text
feat: add user authentication system
fix: handle null values in data parser
refactor: extract validation logic to separate module
```

### Never Commit

Secrets (`.env`, `*.key`, `*.pem`), dependencies (`node_modules/`, `.venv/`), generated files (`.pyc`, `dist/`), OS files (`.DS_Store`), logs, temp files.

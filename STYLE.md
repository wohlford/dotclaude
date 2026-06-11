# Code Style Guide

Universal code style and formatting standards. Full templates: [templates.md](./templates.md)

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

## Documentation Sync

Index sections of `README.md` and `CLAUDE.md` files (skills tables, agents tables, plugin lists, content directory listings) drift from reality as a project grows. The `/sync-docs` skill regenerates these index regions from authoritative sources on demand. Hand-written prose is never touched.

### When a directory should have a README

A directory needs its own `README.md` if any of:

1. It has ≥5 sibling `.md`-style files (`.md`, `.eml`, `.rst`) in its root
2. It has ≥3 sibling subdirectories matching the same naming pattern (e.g., `^\d{4}-\d{2}-\d{2}`, `^[a-z0-9-]+-\d{4}-\d{2}-\d{2}$`)
3. Its name is one of the conventional content-bucket names: `applications`, `runs`, `jobs`, `incoming`, `archive`, `data`, `reports`, `extracts`, `dumps`

Skip even if a rule matches: hidden dirs (`.git`, `.venv`, `.pytest_cache`, `node_modules`), single-file dirs, dirs explicitly excluded in `.claude/sync-docs.yaml`.

`/sync-docs init` walks the repo and proposes scaffolds for qualifying directories interactively.

### Marker syntax

A marker block delimits an auto-managed region inside any `.md` file:

```markdown
<!-- sync:<handler> [directive=value]... -->
... auto-generated content (table, list, etc.) ...
<!-- /sync:<handler> -->
```

Anything between the open/close markers is owned by `/sync-docs`; anything else is hand-edited and never touched. The skill never modifies a file without markers.

**Quoting:**

- Bare values: `[A-Za-z0-9_:./*-]+` (covers `category:extraction`, `*.md`, `30`)
- Quoted values: `"..."` for anything containing spaces, commas, equals
- `cols=Agent:key,Used by:manual` ✗ (space — must quote)
- `cols=Agent:key,"Used by":manual` ✓

Close markers carry no directives. Mismatched handler name in close marker → parse error with line number.

### Common directives

| Directive | Meaning | Example |
| :--- | :--- | :--- |
| `filter` | Subset source files by metadata field | `filter=category:extraction` |
| `cols` | Column list with role annotations | `cols=Agent:key,"Used by":manual,Purpose:auto` |
| `sort` | Sort order | `sort=date,desc` |
| `limit` | Max entries shown | `limit=30` |
| `mode` | `sync` (default) or `lint` (drift-report only) | `mode=lint` |
| `lint` | Granularity when `mode=lint`: `rows` (default), `content`, `both` | `lint=rows` |
| `summary-from` | Where to pull a one-line summary per entry | `summary-from=README.md` |
| `extract` | Override default extractor chain | `extract=heading-meta` |
| `extensions` | For `index` handler, file extensions to include | `extensions=md,txt` |
| `pattern` | Regex over basename for filtering entries | `pattern="^\d{4}-\d{2}-\d{2}"` |

### Built-in handlers

| Handler | Discovery | Default fields | Default rendering |
| :--- | :--- | :--- | :--- |
| `skills` | `.claude/skills/*/SKILL.md`, `skills/*/SKILL.md` | `name`, `description`, `category`, `disable-model-invocation` | Table: `Command` (=`/<name>`), `Purpose` (=`description`) |
| `agents` | `.claude/agents/*.md`, `agents/*.md` (excludes `README.md`, `index.md`) | `name`, `description`, `model`, `tools`, `category` | Table: `Agent`, `Purpose` |
| `plugins` | `settings.json` → `enabledPlugins` | plugin id | Table: `Plugin`, `Purpose` (Purpose always manual; rows-list is sync) |
| `hooks` | `settings.json` → `hooks` + each script's header | event, matcher, command path, purpose | Table: `Event`, `Matcher`, `Script`, `Purpose` |
| `scripts` | `scripts/*.sh`, `scripts/*.py` | filename, purpose | Table: `Script`, `Purpose` |
| `index` | Direct children of marker's containing dir | dirname or filename, summary | List or table per directives |
| `custom` | Per `source=<glob>` directive (relative to repo root) | Per `extract=` chain and `cols=` (column names map to lowercased frontmatter fields; `File`/`Path`/`Name` render the source filename) | Generic table |

The `index` handler accepts: `kind=dirs|files|all` (default `all`), `extensions=<csv>`, `pattern=<regex>`, `sort=alpha|date|mtime[,desc]`, `summary-from=README.md|first-h1|first-paragraph|none`, `limit=N`, `mode=sync|lint`.

### Extractor chain

A source file may carry metadata in multiple formats. Extractors run in order; results are merged with **earlier extractors winning per-key**:

1. **`yaml-frontmatter`** — `---\n...\n---` at file head
2. **`heading-meta`** — first H1 as title, first paragraph as `description`, `## Configuration` section parsed line-by-line as `key: value`
3. **`bash-header`** — first 10 lines, `# Script: <name>`, `# Purpose: <text>` patterns
4. **`py-docstring`** — first triple-quoted string after shebang/imports
5. **`h1-and-paragraph`** — fallback: first H1 + immediately-following paragraph

Merge example: YAML provides `name`, heading-meta provides `description` → merged result has YAML's `name` and heading-meta's `description`. This handles partial migrations (a file with both formats) gracefully.

Each handler ships a default chain. Markers may override with `extract=` (single extractor) or `extract=yaml-frontmatter,heading-meta` (chain).

### Hybrid columns

The `cols=` directive annotates each column with a role:

- **`:auto`** — regenerate from source on every sync
- **`:manual`** — preserve verbatim across syncs
- **`:key`** — regenerate from source AND serve as the row-matching identity for `:manual` columns. Implies `:auto`. **Exactly one column per marker must be `:key`.** A marker without a `:key` annotation is rejected at parse time.

Manual cols are preserved by matching rows on the key column's value. Source files without a corresponding row get added with empty manual cells. Rows in the marker without a corresponding source file are dropped (including any manual cell content).

**Key renames are destructive.** If a source's identifying field changes (e.g., a skill renamed from `commit` to `git-commit`), the manual-column data attached to the old key value is lost. Renames are different identities.

Worked example — agents table with a hand-curated "Used by" column:

```markdown
<!-- sync:agents cols=Agent:key,"Used by":manual,Purpose:auto -->
| Agent | Used by | Purpose |
| :--- | :--- | :--- |
| `code-reviewer` | `/review` | Review changes for style and correctness |
| `security-auditor` | `/review` | Flag potential security issues |
<!-- /sync:agents -->
```

After sync: `Agent` and `Purpose` cols rebuild from agent files; `Used by` cells are preserved by row.

For pure-auto markers (no manual columns), the first column is conventionally the key:

```markdown
<!-- sync:skills cols=Command:key,Purpose:auto -->
```

The `custom` handler indexes arbitrary frontmatter-decorated files (blog posts, references, etc.):

```markdown
<!-- sync:custom source="docs/posts/*.md" cols=File:key,Title:auto,Date:auto,Author:auto -->
<!-- /sync:custom -->
```

`source` is a glob relative to the repo root. `cols=` names map to lowercased frontmatter fields (`Title:auto` reads the `title` field). The `File`, `Path`, and `Name` column names are special — they render the source file's name in backticks.

### Lint mode

`mode=lint` markers don't rewrite content. They detect drift only:

- `lint=rows` (default): check that the set of rows present matches the set of source files (by key value). Reports missing or extra rows.
- `lint=content`: also check that auto column values match the source. Reports cell mismatches.
- `lint=both`: rows and content.

Use lint mode for hand-curated tables where presence/absence of rows can be machine-checked but cell prose is too nuanced to auto-write. Example: a "Repository Structure" table where each directory's purpose is hand-tuned.

### Single source of truth

For each kind of index, source files are authoritative; marker blocks are mirrors derived from sources. Hand-edits to auto cells are silently overwritten on next sync. Hand-edits to manual cells are preserved.

To *change* what an index says, edit the source (the SKILL.md, the agent .md, etc.) — never the marker block.

### Backwards compatibility

The skill never modifies a file that doesn't contain a `<!-- sync:* -->` marker. Existing READMEs in any project are safe by default; opt-in by adding markers. `init` is the only operation that creates new files, and only with explicit user confirmation.

### Project-local overrides

`.claude/sync-docs.yaml` (optional, project-local, checked into the project repo) overrides built-in handler discovery and registers project-specific custom handlers:

```yaml
handlers:
  skills:
    source: "src/skills/*/SKILL.md"     # nested layout — overrides default
  posts:                                 # project-defined handler (no built-in)
    source: "content/posts/*.md"
    extract: yaml-frontmatter,heading-meta
    cols: "File:key,Title:auto,Date:auto"

init:
  exclude:
    - tmp/
    - generated/
    - vendor/
```

When `<!-- sync:posts -->` appears in a marker, it routes to the `custom` handler with the directives merged from the config.

Requires `pyyaml`: `uv pip install pyyaml`. If pyyaml is not installed, the file is ignored with a warning.

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

Single line only. No body or footer. Lowercase, imperative mood, no period. Keep the whole subject under 72 characters. Scope is optional but encouraged to specify the area of impact. Append `!` after the type/scope for breaking changes.

**Types:** `feat`, `fix`, `docs`, `style`, `refactor`, `perf`, `test`, `chore`, `ci`, `revert`

**`docs` vs `feat` for markdown files:**
- `docs` — repo usage documentation (README, CONTRIBUTING, setup guides)
- `feat` — markdown files consumed as AI execution context, configuration, or runtime logic (agent/skill definitions, scoring rubrics, prompt templates, pipeline configs)

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

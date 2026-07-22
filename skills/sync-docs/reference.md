# /sync-docs reference

The complete manual for the `/sync-docs` marker system — the machinery behind the
auto-generated index regions in `README.md`/`CLAUDE.md`. The convention summary lives in
[STYLE.md](../../STYLE.md); this is the full reference.

## When a directory should have a README

A directory needs its own `README.md` if any of:

1. It has ≥5 sibling `.md`-style files (`.md`, `.eml`, `.rst`) in its root
2. It has ≥3 sibling subdirectories whose names share a date prefix (`^\d{4}-\d{2}-\d{2}`)
3. Its name is one of the conventional content-bucket names: `applications`, `runs`, `jobs`, `incoming`, `archive`, `data`, `reports`, `extracts`, `dumps`

Skip even if a rule matches: hidden dirs and common build/test dirs (`.git`, `.venv`, `venv`, `node_modules`, `.pytest_cache`, `__pycache__`, `.mypy_cache`, `.ruff_cache`, `fixtures`), single-file dirs, dirs explicitly excluded in `.claude/sync-docs.yaml`.

`/sync-docs init` walks the repo and proposes scaffolds for qualifying directories interactively.

## Marker syntax

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

## Common directives

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

## Built-in handlers

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

## Extractor chain

A source file may carry metadata in multiple formats. Extractors run in order; results are merged with **earlier extractors winning per-key**:

1. **`yaml-frontmatter`** — `---\n...\n---` at file head
2. **`heading-meta`** — first H1 as title, first paragraph as `description`, `## Configuration` section parsed line-by-line as `key: value`
3. **`bash-header`** — first 10 lines, `# Script: <name>`, `# Purpose: <text>` patterns
4. **`py-docstring`** — first triple-quoted string after shebang/imports
5. **`h1-and-paragraph`** — fallback: first H1 + immediately-following paragraph

Merge example: YAML provides `name`, heading-meta provides `description` → merged result has YAML's `name` and heading-meta's `description`. This handles partial migrations (a file with both formats) gracefully.

Each handler ships a default chain. Markers may override with `extract=` (single extractor) or `extract=yaml-frontmatter,heading-meta` (chain).

## Hybrid columns

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

## Lint mode

`mode=lint` markers don't rewrite content. They detect drift only:

- `lint=rows` (default): check that the set of rows present matches the set of source files (by key value). Reports missing or extra rows.
- `lint=content`: also check that auto column values match the source. Reports cell mismatches.
- `lint=both`: rows and content.

Use lint mode for hand-curated tables where presence/absence of rows can be machine-checked but cell prose is too nuanced to auto-write. Example: a "Repository Structure" table where each directory's purpose is hand-tuned.

## Single source of truth

For each kind of index, source files are authoritative; marker blocks are mirrors derived from sources. Hand-edits to auto cells are silently overwritten on next sync. Hand-edits to manual cells are preserved.

To *change* what an index says, edit the source (the SKILL.md, the agent .md, etc.) — never the marker block.

## Backwards compatibility

The skill never modifies a file that doesn't contain a `<!-- sync:* -->` marker. Existing READMEs in any project are safe by default; opt-in by adding markers. `init` is the only operation that creates new files, and only with explicit user confirmation.

## Project-local overrides

`.claude/sync-docs.yaml` (optional, project-local, checked into the project repo) overrides built-in handler discovery and registers project-specific custom handlers:

```yaml
handlers:
  skills:
    source: "src/skills/*/SKILL.md"     # nested layout — overrides default
  posts:                                 # project-defined handler (no built-in)
    source: "content/posts/*.md"
    extract: yaml-frontmatter,heading-meta
    cols: "File:key,Title:auto,Date:auto"
  index-files:                           # owned by the project's own generator
    external: true
    owner: scripts/index-gen.py          # free-form note; not interpreted

init:
  exclude:
    - tmp/
    - generated/
    - vendor/
```

When `<!-- sync:posts -->` appears in a marker, it routes to the `custom` handler with the directives merged from the config.

### `external: true` — blocks owned by other tooling

A project may own a marker format whose body its *own* generator produces — typically because the cells are curated (hand- or LLM-written) and cannot be re-derived from the filesystem. Declaring the handler `external: true` makes sync-docs skip those blocks entirely: never rendered, never rewritten, never an `unknown handler` error. The declaration is checked before the built-in lookup, so it also works to hand a built-in handler's name back to the project.

Delegation is **opt-in per handler name and always reported** — a run prints `N block(s) delegated to external tooling: <names>`, so an undeclared or misspelled marker still fails loudly rather than being swallowed. Only a truthy `external:` delegates; `external: false` falls through to normal handling. `external` wins over `source`/`cols` if both are present.

Drift detection for a delegated format is the project generator's job, not sync-docs'. (Live example: the court repo's `sync:index-files` INDEX tables — `scripts/index-gen.py --check` verifies them, and the `index-md-auditor` agent aggregates that repo-wide.)

Requires `pyyaml`: `uv pip install pyyaml`. If pyyaml is not installed, the file is ignored with a warning.

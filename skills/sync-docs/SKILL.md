---
name: sync-docs
description: Regenerate index regions of README.md and CLAUDE.md from authoritative sources
disable-model-invocation: true
---

# /sync-docs — Sync Documentation Indexes

Regenerate `<!-- sync:* -->` marker regions in `README.md`, `CLAUDE.md`, and any other Markdown file that carries sync markers, from authoritative sources (skill files, agent files, `settings.json`, content directories). Hand-written prose outside markers is never modified.

See [`reference.md`](reference.md) for marker syntax, the handler list, and convention details.

## Instructions

The user wants to run `/sync-docs`. Execute the Python implementation and surface its output verbatim. Do not paraphrase, summarize, or interpret the script's stdout/stderr — pass them through as-is.

### Arguments

The user may provide:

- No args (default `sync`) — regenerate all marker contents in the current repo
- `--check` — dry-run; print a unified diff of what would change; exit 1 on drift
- `init` — scaffold READMEs for content directories meeting threshold rules (interactive); add `--yes-to-all` for non-interactive mode
- `--max-depth <N>` (with `init`) — limit the scaffold scan depth (default 2)
- `add <handler>` — insert a marker block for `<handler>` into a target file (`--into <file>`, default `./README.md`; `--source <glob>` and `--cols <spec>` apply to the `custom` handler)
- `--scope <path>` — override repo root (default: git toplevel from cwd)
- `--scope cwd` — limit scan to current working directory

### Exit codes

- **0** — success / no drift / no markers found
- **1** — drift detected in `--check` mode, or an operational failure (e.g. `add custom` without `--source`/`--cols`, aborted `init` prompt)
- **2** — parser or handler errors (the script printed details to stderr)

### Process

1. Run the Python script with the user's arguments:

```bash
python3 ~/.claude/skills/sync-docs/sync_docs.py "$@"
```

2. Surface stdout and stderr to the user verbatim.

3. If exit code is non-zero, briefly explain to the user what state the repo is in (drift detected, parser errors, unknown `add` handler) — but do not retry, do not "fix" anything Claude infers from the error.

### Rules

- **Never** edit the contents of a marker block by hand. To change what an index says, edit the source files (the SKILL.md, agent .md, settings.json, etc.) and re-run `/sync-docs`.
- **Never** add files to placate a `--check` failure unless the user asks. The check exists to surface drift; the user decides what to do about it.
- **Never** add `pyyaml` or other dependencies for any other purpose without flagging it explicitly — the script targets pure stdlib; `pyyaml` is an optional dependency imported best-effort solely to parse the project-config file (`.claude/sync-docs.yaml`) when present, and the hand-rolled frontmatter parser never uses it.

## Testing

Test suite lives at `skills/sync-docs/tests/`:

```bash
python3 -m pytest skills/sync-docs/tests/ -v
```

Run from the repo root.

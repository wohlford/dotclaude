---
name: sync-docs
description: Regenerate index regions of README.md and CLAUDE.md from authoritative sources
disable-model-invocation: true
---

# /sync-docs — Sync Documentation Indexes

Regenerate `<!-- sync:* -->` marker regions in `README.md` and `CLAUDE.md` files from authoritative sources (skill files, agent files, `settings.json`, content directories). Hand-written prose outside markers is never modified.

See `STYLE.md` "Documentation Sync" section for marker syntax, handler list, and convention details.

## Instructions

The user wants to run `/sync-docs`. Execute the Python implementation and surface its output verbatim. Do not paraphrase, summarize, or interpret the script's stdout/stderr — pass them through as-is.

### Arguments

The user may provide:

- No args (default `sync`) — regenerate all marker contents in the current repo
- `--check` — dry-run; print a unified diff of what would change; exit 1 on drift
- `init` — scaffold READMEs for content directories meeting threshold rules (interactive); add `--yes-to-all` for non-interactive mode
- `add <handler>` — insert a marker block for `<handler>` into a target file
- `--scope <path>` — override repo root (default: git toplevel from cwd)
- `--scope cwd` — limit scan to current working directory

### Exit codes

- **0** — success / no drift / no markers found
- **1** — drift detected in `--check` mode
- **2** — parser or handler errors (the script printed details to stderr)

### Process

1. Run the Python script with the user's arguments:

```bash
python3 ~/.claude/skills/sync-docs/sync_docs.py "$@"
```

2. Surface stdout and stderr to the user verbatim.

3. If exit code is non-zero, briefly explain to the user what state the repo is in (drift detected, parser errors, unimplemented subcommand) — but do not retry, do not "fix" anything Claude infers from the error.

### Rules

- **Never** edit the contents of a marker block by hand. To change what an index says, edit the source files (the SKILL.md, agent .md, settings.json, etc.) and re-run `/sync-docs`.
- **Never** add files to placate a `--check` failure unless the user asks. The check exists to surface drift; the user decides what to do about it.
- **Never** add `pyyaml` or other dependencies without flagging it explicitly — the script targets pure stdlib and falls back to `pyyaml` only if real-world fixtures break the hand-rolled YAML parser.

## Testing

Test suite lives at `skills/sync-docs/tests/`:

```bash
python3 -m pytest skills/sync-docs/tests/ -v
```

Run from the repo root.

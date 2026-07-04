# sync-docs

Implementation of the `/sync-docs` skill. See [SKILL.md](SKILL.md) for user-facing instructions and [`reference.md`](reference.md) for the marker syntax, handlers, and conventions this skill enforces.

## Layout

| File | Purpose |
| :--- | :--- |
| `SKILL.md` | Skill entry point invoked via `/sync-docs` |
| `sync_docs.py` | CLI dispatch, repo discovery, atomic writes |
| `markers.py` | Marker-block parser (state machine) |
| `extractors.py` | Five metadata extractors (yaml, heading-meta, bash-header, py-docstring, h1-and-paragraph) |
| `handlers.py` | Six built-in handlers (skills, agents, plugins, hooks, scripts, index) |
| `formatters.py` | Canonical Markdown table rendering |
| `tests/` | pytest suite + fixture mini-repos |

## Testing

Run from the repo root:

```bash
python3 -m pytest skills/sync-docs/tests/ -v
```

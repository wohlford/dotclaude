# Architecture

How the pieces of this repo fit together. This is the map for "where does X live and how does it
take effect" — read it when a change spans more than one file or you've lost track of what owns what.

## The two moving parts

This repo is **configuration that Claude Code loads**, plus **the tooling that keeps that
configuration honest**. Four kinds of artifact:

| Kind | Lives in | What it is |
|---|---|---|
| **Skills** | `skills/<name>/SKILL.md` (+ bundled scripts) | Slash commands (`/commit`, `/feature`, …) |
| **Agents** | `agents/*.md` | Subagent definitions (the reviewers `/vet` dispatches) |
| **Hooks** | `scripts/*.sh` + `settings.json` | PreToolUse deny-gates and PostToolUse checks on every edit |
| **Standards** | `STYLE.md`, `CONTRIBUTING.md`, `templates.md`, `workflows.md` | The universal rules |

## The enforcement mesh

A standard is not just written down — it is enforced from three directions, so a rule that matters is
caught whether you're editing, reviewing, or committing:

```text
        STYLE.md (source of truth for code style)
          │
   ┌──────┼───────────────┐
   ▼      ▼               ▼
 hooks   reviewer agents   /sync-docs
(scripts/*.sh)  (agents/*.md)   (index-table drift)
 edit-time,   on-demand via     blocks a stale
 block on     /vet              <!-- sync:* --> table
 write
```

- **Hooks** (`scripts/style-check.sh`, `shellcheck-check.sh`, `ruff-check.sh`, …) run on every
  `Edit`/`Write` and **block** a non-conforming change immediately (exit 2). See
  `scripts/HOOKS.md` for how they're built.
- **Reviewer agents** (`agents/style-reviewer.md`, `skill-reviewer.md`, …) are dispatched **on demand**
  by [`/vet`](skills/vet/SKILL.md) for a deeper read than a hook can do at edit time.
- **`/sync-docs`** keeps the auto-generated index regions (`<!-- sync:* -->` tables in README/CLAUDE)
  from drifting; a companion hook blocks an edit that leaves one stale. Full marker reference:
  [`skills/sync-docs/reference.md`](skills/sync-docs/reference.md).

The pattern: **one source of truth → enforced at edit time (hook) + on demand (reviewer) + for drift
(sync).** When you add a rule, decide which of the three should enforce it.

## The staging → live lifecycle

Edits don't take effect where you make them. There are two working clones plus the remote:

```text
  dotclaude-staging  ──/propagate──▶  origin (GitHub)  ──▶  dotclaude (live)
  (tracked working copy)                                    symlinked into ~/.claude
```

- **`dotclaude-staging`** — the tracked working copy. All development happens here.
- **`dotclaude` (live)** — a separate clone whose `skills/`, `agents/`, `scripts/`, and the standards
  docs are **symlinked into `~/.claude`** (see `install.sh`). This is what Claude Code
  actually loads.
- **[`/propagate`](skills/propagate/SKILL.md)** pushes committed work from staging to `origin`, then
  fast-forwards the live clone from `origin`. So an edit is: commit in staging → `/propagate` → restart
  Claude to reload. Nothing goes live until propagated.

**Why `settings.json` is not symlinked:** Claude Code rewrites it at runtime (model, enabled plugins).
`install.sh` deliberately omits it, and in the live clone it is marked `skip-worktree` so those runtime
rewrites never show up as git changes. `/propagate` has a park/restore dance for it.

## Source-of-truth map

To *change* what something says, edit its canonical home — never the derived copy:

| For… | Canonical file |
|---|---|
| Code style / formatting | `STYLE.md` |
| Commit messages, semver, never-commit | `CONTRIBUTING.md` |
| Bash/Python/JS starter templates | `templates.md` |
| The `<!-- sync:* -->` marker system | `skills/sync-docs/reference.md` |
| A skills/agents/hooks **index table** | the source files (SKILL.md, agent .md, `settings.json`) — regenerate with `/sync-docs`, never hand-edit the table |
| Hook wiring | `settings.json` (+ each script's `# Purpose:` header) |
| How to author a hook | `scripts/HOOKS.md` |
| Test conventions | `TESTING.md` |

## Extension points

- **New skill** → `/init-skill` scaffolds `skills/<name>/SKILL.md`; `skill-reviewer` +
  `skill-content-reviewer` check it; add its row via `/sync-docs`.
- **New agent** → follow `agents/agent-reviewer.md` as the canonical shape.
- **New hook** → write it per `scripts/HOOKS.md`, wire it into `settings.json`.
- **New standard** → put it in the right canonical doc, then decide which enforcement arm(s) apply.

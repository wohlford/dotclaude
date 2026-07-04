---
name: vet
description: Vet an authored skill, agent, or script — or the whole repo with --all — by dispatching the matching reviewer agent(s) and reporting their findings
---

# /vet — Dispatch the Right Reviewer for an Artifact

Review an **authored** skill, agent, or script by dispatching the repo's reviewer agent(s) that match
its type, then report their findings. Read-only and non-blocking — it surfaces issues; the caller
decides what to fix. Best run once an artifact's content is written (a fresh scaffold is still stubs,
so a reviewer would mostly flag placeholders). Pass `--all` to sweep the whole repo (see Repo-wide
mode below).

## Instructions

The caller gives one or more paths (or `--all`). For each artifact, classify it by type, dispatch the
matching reviewer agent(s) via the Agent tool, collect the findings, and report them grouped by file.
Run multiple reviewers in parallel. Never edit the reviewed artifact — this only reviews.

### Arguments

The user must provide **one of**:
- One or more paths to review — a `SKILL.md` (or a `skills/<name>/` directory), an `agents/<name>.md`,
  or a code file (`*.sh`, `*.py`, `*.js`); **or**
- `--all` — vet the whole repo (see **Repo-wide mode** below). If paths are given alongside
  `--all`, `--all` takes precedence — sweep the whole repo and note the conflict to the caller.

The user may optionally provide:
- `--tests` (with `--all` only; a no-op otherwise) — also vet **test code** (`.sh`/`.py` under a
  `tests/` directory, `test_*`, `conftest`). **Off by default** to keep the sweep focused on shipping
  code (test code has only a narrow, Python-only STYLE exemption — type hints and docstrings; all
  other rules still apply, and shell test code has no exemption). The
  **skills** category's `tests/` exclusion is permanent (those are malformed fixtures, not real
  skills) — `--tests` only affects the scripts and python categories.

If invoked with **neither a path nor `--all`**, ask what to vet rather than guessing — never default
to a whole-repo sweep.

### Process

1. **Resolve each path.** A `skills/<name>/` directory resolves to its `skills/<name>/SKILL.md`.
   If a path does not exist, report it as missing and continue with the remaining valid paths.
2. **Classify by type** and pick the reviewer(s):

   | Path | Reviewer agent(s) |
   | :--- | :--- |
   | a `SKILL.md` | `skill-reviewer` (structure) **and** `skill-content-reviewer` (content) |
   | an `agents/*.md` | `agent-reviewer` (structure) |
   | a code file (`*.sh`, `*.py`, `*.js`) | `style-reviewer` (against `STYLE.md`) |

   If a path fits no category, ask the caller rather than guessing.
3. **Dispatch** the chosen reviewer(s) with the Agent tool, passing the path(s). When a file draws
   more than one reviewer (a `SKILL.md`), or several files are given, launch them **in parallel** —
   one message, multiple Agent calls. If a reviewer dispatch fails or returns nothing, report that
   reviewer as failed/unavailable for that file rather than silently dropping it.
4. **Report** the findings grouped by file and reviewer, with each reviewer's verdict. Order
   most-severe first — files with any reviewer FAIL before files where every reviewer passes; within
   a file, majors before minors. Do not edit anything; the caller decides what to act on.

### Repo-wide mode (`--all`)

Vet every shipping artifact in the repo, batched by category, in one pass:

1. **Discover** vettable artifacts by category (tracked files only):
   - **skills** — `git ls-files 'skills/*/SKILL.md'`. Git's pathspec `*` matches `/`, so this also
     returns nested fixture `SKILL.md` files under `skills/sync-docs/tests/fixtures/`; **exclude any
     path containing `/tests/`** (malformed fixtures, not real skills).
   - **agents** — `git ls-files 'agents/*.md'`, dropping `README.md`.
   - **scripts** — `git ls-files '*.sh'`; **python** — `git ls-files '*.py'`. **Without `--tests`,**
     drop files under a `tests/` directory and `test_*` / `conftest` files (default = what ships).
     Note: `test_*` / `conftest` files outside a `tests/` directory are skipped by the sweep as test
     code, but receive no STYLE exemption when actually reviewed — STYLE.md exempts only modules
     under a `tests/` directory.
   - **javascript** — `git ls-files '*.js'`, with the same `tests/` / `test_*` / `conftest`
     exclusions without `--tests`. (The repo currently ships no `.js`, so this category is usually
     empty.)
   - **Skip** plain docs (STYLE/README/CLAUDE/templates/workflows/CONTRIBUTING/CHANGELOG) — no reviewer
     covers them.
2. **Announce the scale first.** State the artifact count per category and the rough subagent count
   (each `SKILL.md` draws two reviewers), so the caller sees the cost before dozens of agents fire.
3. **Batch by category** (skills → agents → scripts → python → javascript), reporting each category as it
   completes. **Scale the mechanism to the count:** for ~10 artifacts or fewer, direct parallel
   Agent-tool dispatches (one message, multiple Agent calls) suffice; for more, orchestrate each
   category as a background workflow via the **Workflow tool**, which caps concurrency automatically.
   Invoking `/vet --all` is itself the opt-in for this multi-agent fan-out.
4. **Dispatch** the matching reviewer(s) per artifact (the mapping in Process step 2).
5. **Close with a consolidated summary** across all categories — grouped by file, most-severe first,
   non-blocking as always. (Per-category reports stream as they finish; this is the final roll-up.)

### Rules

- **Read-only** — never modify the reviewed artifact; `/vet` only dispatches reviewers and reports.
- **Non-blocking** — surface findings; do not gate, refuse, or auto-fix based on them.
- Dispatch matching reviewers **in parallel** (a `SKILL.md` gets structure + content at once).
- Choose reviewers by **path type**; on an ambiguous path, ask rather than guess.
- Best run on **authored** content — a scaffold stub will mostly flag placeholders.
- **`--all` announces the artifact + subagent count before firing** — a large fan-out is never silent.
  Bare `/vet` (no path, no `--all`) asks what to vet. `--all` defaults to shipping artifacts; `--tests`
  opts into test code (which has only a narrow, Python-only STYLE exemption).

---
name: commit
description: Create a git commit with automatic semver tagging following STYLE.md conventions; signing and identity follow git config
---

# /commit — Create a Git Commit

Stage changes, create a commit following STYLE.md conventions, and tag with a semver version.

## Dynamic Context

```bash
git status -s
```

```bash
git diff --cached --stat
```

```bash
git log --oneline --decorate -5
```

Scopes already used in this repo — reuse one when it fits and is specific; prefer a precise name over a vague frequent one.

```bash
git log --pretty=%s -n 300 2>/dev/null \
  | sed -nE 's/^[a-z]+\(([^)]+)\)!?:.*/\1/p' \
  | tr ',' '\n' \
  | sed -E 's/^ +//; s/ +$//' \
  | sort | uniq -c | sort -rn | head -30
```

```bash
git describe --tags --abbrev=0 2>/dev/null || echo "v0.0.0"
```

## Instructions

The user wants to create a git commit. Follow the commit message format below exactly (summarized in `~/.claude/STYLE.md`; the full conventions are canonical in `~/.claude/CONTRIBUTING.md`).

### Signing and identity

Signing and identity are **config-driven — never hardcoded**. Use plain `git commit` and
`git tag -a` (never pass `-S` or `-s`). Whether commits and tags are signed then follows the
repo's effective `commit.gpgsign` / `tag.gpgsign`; the author identity follows the repo's
effective `user.name` / `user.email`. Do not set, override, or hardcode any identity, signing
key, host, or path. A primary global identity applies by default; additional identities (a
different email, an optional signing key, or unsigned when no key is configured) are selected
per-repo via git `includeIf` in a private git config the skill never touches.

### Commit Message Format

```text
<type>[(scope)][!]: <subject>
```

**Single line only.** No body, no footer, no `Co-Authored-By`. Lowercase, imperative mood, no trailing period. **Scope is encouraged** — include it whenever the area of impact is inferable, omitting it only for truly cross-cutting changes; choose it per **Choosing a scope** below. Append `!` after the type/scope for breaking changes.

**Types:** `feat`, `fix`, `docs`, `style`, `refactor`, `perf`, `test`, `chore`, `ci`, `revert`

**`docs` vs `feat` for markdown files:** pick the type by how the file is *used*, not by its extension.
- `docs` — repo usage documentation (README, CONTRIBUTING, setup guides)
- `feat` — markdown files consumed as AI execution context, configuration, or runtime logic (agent/skill definitions, scoring rubrics, prompt templates, pipeline configs)

### Choosing a scope

Include a scope whenever one is inferable; omit only for truly cross-cutting changes.

Choose the scope in this order:

1. **Reuse an existing repo scope** if one fits the change and is specific enough — see the prior-scopes list in Dynamic Context. Don't inherit a vague frequent scope (a bare `skill`, `scripts`) when a precise name is clearly better.
2. Otherwise **coin a new scope** by this precedence:
   - **Logical area** — the subsystem or concept the change is about, regardless of which files it touches (`env`, `install`, `certificates`).
   - **Component / directory** — when the change maps cleanly to one unit (`commit`, `python-v3.12`).
   - **Filename with extension** — when confined to one file and no broader area fits (`build.sh`, `functions.sh`).
   - **Comma-joined** for a few related units (`build.sh,functions.sh`); **glob** when one change spans many directories (`*/build.sh`).
   - **Omit** (or `*`) when the change is genuinely repo-wide.
3. When several candidates fit, pick the name a reader recognizes fastest.

Normalize: match the real filename and extension (`preseed.yaml`, not `preseed.yml`); keep precision at the directory's actual name (`python-v3.12`); lowercase unless the name is genuinely cased (`README.md`); no space after commas.

### Granular-by-default

**Always split unrelated changes into separate commits.** A single `/commit` invocation may produce multiple commits when the working tree contains independent changes.

Before committing, group the changed files by logical unit:
- Each bug fix, feature, refactor, or documentation update is its own commit
- Pipeline data updates (extraction runs, status changes, contact log entries) are each their own commit
- Configuration changes are separated from code changes
- A change to one component does not get bundled with an unrelated change to another component

If `git status` shows changes that would naturally take different commit messages (different `<type>` or unrelated `<subject>`), commit them separately — even if the user invoked `/commit` once. Tag each one independently.

**When to bundle into one commit:**
- All changes share the same logical purpose (e.g., adding a feature touches 5 files)
- The user explicitly passes `--batch` or describes the changes as a single unit
- Trivial mechanical changes that travel together (rename across files, formatter sweep)

When in doubt, split. The cost of an extra commit is negligible; the cost of an opaque "kitchen sink" commit is real.

### Process

Per logical group, the order is **stage → version → changelog → commit → tag** (version and changelog come *before* the commit so the entry lands inside it).

1. Review `git status` and `git diff` to understand the changes.
2. **Group changes into logical units.** If multiple independent commits are needed, plan them before staging anything, then walk each group through steps 3–8. **If `CHANGELOG.md` is already modified** in the working tree before you start, those hand edits are their *own* group — commit them separately or flag to the user; never let step 6 silently fold them into another group.
3. For the current group, stage only its files (prefer explicit filenames over `git add .` / `git add -A`).
4. Draft the single-line message `<type>[(scope)][!]: <subject>` per **Commit Message Format** and **Choosing a scope** (imperative mood; `!` if breaking; under 72 chars). **This freezes the message** — the version (5) and changelog entry (6) derive from it; if you later refine it, redo 5–6.
5. **Determine the version.** *If `--no-tag` was passed, skip this step, the changelog step (6), and the tag step (8) — still stage and commit.* Otherwise apply the first matching rule:
   - `!` suffix → **MAJOR** (reset minor+patch to 0) — **except before v1.0.0**: when the base tag's MAJOR is `0`, a breaking `!` bumps **MINOR** (SemVer 0.x: "anything may change"; reaching 1.0.0 is a deliberate choice, never an automatic consequence of the first `feat!`).
   - `feat` → **MINOR** (reset patch to 0).
   - all other types → **PATCH**.
   **Base tag:** for the first commit of the invocation, the latest tag from dynamic context (default `v0.0.0`); for each later commit in the same invocation, the tag you created in the previous iteration. Increment it to get `vX.Y.Z`.
6. **Living changelog entry.** Prepend a section that mirrors the tag:

   ```text
   ## vX.Y.Z — <YYYY-MM-DD>
   - <subject>
   ```

   literal em-dash `—`; date is today (= the commit's own date); `<subject>` is the exact message from step 4. Insert it **immediately before the first `^## ` heading**, with exactly one blank line on each side; if the file has no `## ` heading, append at EOF. Then **stage `CHANGELOG.md`** with the group.
   - **Format guard (portability — the skill is copied into other repos).** Add the entry only if the file's **first `## ` heading matches `^## v[0-9]`** (this living format) **or** the file has **no `## ` version sections** at all. If it's a Keep-a-Changelog file (`## [1.2.3]`, `## [Unreleased]`) or owned by other tooling, **skip the entry and tell the user** — never inject a `## vX.Y.Z` above a different convention. Skip **silently** when there is no `CHANGELOG.md`.
   - **Idempotency.** Before prepending, if the first `^## v` line **already equals** the `vX.Y.Z` you just computed, it's a leftover from a prior/blocked attempt — **update that section's bullet in place** to match the current message; **never add a second section**.
7. Create the commit (the group's files + `CHANGELOG.md` are already staged):

   ```bash
   git commit -m "$(cat <<'EOF'
   <type>[(scope)][!]: <subject>
   EOF
   )"
   ```

   Show it: `git log --oneline -1`.
8. **Tag** (skip if `--no-tag`): `git tag -a vX.Y.Z -m "<message>"` (signing follows `tag.gpgsign`; never `-s`). Show: `git log --oneline -1 --decorate`.
9. **If more groups remain, return to step 3.**
10. Final summary: `git log --oneline -<N> --decorate` (N = commits created).

`/commit` does **not** regenerate index/manifest tables — index freshness is the edit-time `sync-docs`-style hook's job, not the commit's. A `--no-tag` commit never appears in the changelog, by design. In a `/recast` build, bricks follow **recast's own** changelog rules (date from the brick's commit; `[declared, not proven]` suffix), not these.

**Amend flow (`--amend`):** skip the grouping loop; run `git commit --amend` (message via `-m` heredoc, or `--no-edit`). Do not create a new tag; if the amended commit was tagged, move it: `git tag -f -a <tag> -m "<amended subject>"`. **Keep the changelog in sync:** if a living-format `CHANGELOG.md` is present and its tip `## v<tag>` bullet differs from the amended subject, edit that one bullet in place and include `CHANGELOG.md` in the amend. **Tolerate a missing tip entry** (the commit may predate this feature or was `--no-tag`) — do not fabricate one.

### Arguments

The user may provide:
- A commit message directly (use it as-is if it follows the format; otherwise reformat it to conform before using it). The supplied message applies to the group it describes; any unrelated remaining changes are still split into separately drafted commits.
- A description of changes (derive the proper commit message)
- `--amend` — amend the previous commit instead of creating a new one
- `--batch` — bundle related changes into a single commit even when they could be split
- `--no-tag` — skip version tagging for this commit
- No arguments — analyze staged/unstaged changes and draft the message

### Rules

- **Never** pass `-S` (commit) or `-s` (tag) — signing follows the repo's `commit.gpgsign` / `tag.gpgsign`
- **Never** set or hardcode `user.name`, `user.email`, a signing key, host, or path — identity follows the repo's effective git config, selected per-repo via `includeIf`
- **Never** add `Co-Authored-By` or any footer
- **Never** add a message body (second paragraph)
- **Never** use a period at the end of the subject
- **Never** commit `.env`, `*.key`, `*.pem`, or other secrets
- Prefer staging specific files over `git add -A`
- If pre-commit hooks fail, fix the issue and create a **new** commit (do not amend)

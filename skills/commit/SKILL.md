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

The user wants to create a git commit. Follow the commit message format from `~/.claude/STYLE.md` exactly.

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

1. Review `git status` and `git diff` to understand the changes
2. **Group changes into logical units.** If multiple independent commits are needed, plan them before staging anything. Walk through each group sequentially: stage → commit → tag, then move to the next.
3. For the current group, stage only its files (prefer explicit filenames over `git add .` or `git add -A`)
4. Draft a commit message for this group:
   - Choose the correct type based on the nature of the change
   - Add a scope as `(scope)` whenever the area of impact is inferable — choose it per **Choosing a scope** (reuse a fitting existing repo scope, else lead with the logical area; break ties by recognizability); omit only for truly cross-cutting changes
   - Write a concise subject in imperative mood (e.g., "add X", "fix Y", "remove Z")
   - Append `!` after the type/scope if the change is breaking (e.g., `feat!: remove legacy API`, `chore(build)!: drop Node 6`)
   - Keep the entire message under 72 characters
5. Create the commit using a heredoc:

```bash
git commit -m "$(cat <<'EOF'
<type>[(scope)][!]: <subject>
EOF
)"
```

6. Show the result with `git log --oneline -1`
7. Determine the version bump from the commit message:
   - Type has `!` suffix → **MAJOR** (reset minor and patch to 0)
   - Type is `feat` → **MINOR** (reset patch to 0)
   - All other types → **PATCH**
8. Read the latest tag from dynamic context (default `v0.0.0`) — for subsequent commits in the same `/commit` invocation, use the tag you created in the previous iteration as the base
9. Increment the appropriate version component
10. Create an annotated tag (signing follows `tag.gpgsign`; do not pass `-s`):

```bash
git tag -a vX.Y.Z -m "<type>[(scope)][!]: <subject>"
```

11. Show the tagged result: `git log --oneline -1 --decorate`
12. **If more groups remain from step 2, return to step 3** with the next group. Continue until all logical units have their own commit and tag.
13. After all commits are done, show a final summary: `git log --oneline -<N> --decorate` where N is the number of commits created.

### Arguments

The user may provide:
- A commit message directly (use it as-is if it follows the format)
- A description of changes (derive the proper commit message)
- `--amend` — amend the previous commit instead of creating a new one
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

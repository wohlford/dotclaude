---
name: commit
description: Create a signed git commit with automatic semver tagging following STYLE.md conventions
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

```bash
git describe --tags --abbrev=0 2>/dev/null || echo "v0.0.0"
```

## Instructions

The user wants to create a git commit. Follow the commit message format from `~/.claude/STYLE.md` exactly.

### Commit Message Format

```text
<type>[!]: <subject>
```

**Single line only.** No body, no footer, no `Co-Authored-By`. Lowercase, imperative mood, no trailing period. Append `!` after the type for breaking changes.

**Types:** `feat`, `fix`, `docs`, `style`, `refactor`, `perf`, `test`, `chore`, `ci`, `revert`

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
   - Write a concise subject in imperative mood (e.g., "add X", "fix Y", "remove Z")
   - Append `!` after the type if the change is breaking (e.g., `feat!: remove legacy API`)
   - Keep the entire message under 72 characters
5. Create the commit using a heredoc:

```bash
git commit -S -m "$(cat <<'EOF'
<type>[!]: <subject>
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
10. Create a signed, annotated tag:

```bash
git tag -s -a vX.Y.Z -m "<type>[!]: <subject>"
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

- **Never** add `Co-Authored-By` or any footer
- **Never** add a message body (second paragraph)
- **Never** use a period at the end of the subject
- **Never** commit `.env`, `*.key`, `*.pem`, or other secrets
- Prefer staging specific files over `git add -A`
- If pre-commit hooks fail, fix the issue and create a **new** commit (do not amend)

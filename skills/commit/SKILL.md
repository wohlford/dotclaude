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

### Process

1. Review `git status` and `git diff` to understand the changes
2. If nothing is staged, stage the relevant files (prefer explicit filenames over `git add .`)
3. Draft a commit message:
   - Choose the correct type based on the nature of the change
   - Write a concise subject in imperative mood (e.g., "add X", "fix Y", "remove Z")
   - Append `!` after the type if the change is breaking (e.g., `feat!: remove legacy API`)
   - Keep the entire message under 72 characters
4. Create the commit using a heredoc:

```bash
git commit -S -m "$(cat <<'EOF'
<type>[!]: <subject>
EOF
)"
```

5. Show the result with `git log --oneline -1`
6. Determine the version bump from the commit message:
   - Type has `!` suffix → **MAJOR** (reset minor and patch to 0)
   - Type is `feat` → **MINOR** (reset patch to 0)
   - All other types → **PATCH**
7. Read the latest tag from dynamic context (default `v0.0.0`)
8. Increment the appropriate version component
9. Create a signed, annotated tag:

```bash
git tag -s -a vX.Y.Z -m "<type>[!]: <subject>"
```

10. Show the tagged result: `git log --oneline -1 --decorate`

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

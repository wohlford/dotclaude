---
name: commit
description: Create a signed git commit following STYLE.md conventions
---

# /commit — Create a Git Commit

Stage changes and create a commit following STYLE.md conventions.

## Dynamic Context

```bash
git status -s
```

```bash
git diff --cached --stat
```

```bash
git log --oneline -5
```

## Instructions

The user wants to create a git commit. Follow the commit message format from `~/.claude/STYLE.md` exactly.

### Commit Message Format

```text
<type>: <subject>
```

**Single line only.** No body, no footer, no `Co-Authored-By`. Lowercase, imperative mood, no trailing period.

**Types:** `feat`, `fix`, `docs`, `style`, `refactor`, `perf`, `test`, `chore`, `ci`, `revert`

### Process

1. Review `git status` and `git diff` to understand the changes
2. If nothing is staged, stage the relevant files (prefer explicit filenames over `git add .`)
3. Draft a commit message:
   - Choose the correct type based on the nature of the change
   - Write a concise subject in imperative mood (e.g., "add X", "fix Y", "remove Z")
   - Keep the entire message under 72 characters
4. Create the commit using a heredoc:

```bash
git commit -S -m "$(cat <<'EOF'
<type>: <subject>
EOF
)"
```

5. Show the result with `git log --oneline -1`

### Arguments

The user may provide:
- A commit message directly (use it as-is if it follows the format)
- A description of changes (derive the proper commit message)
- `--amend` — amend the previous commit instead of creating a new one
- No arguments — analyze staged/unstaged changes and draft the message

### Rules

- **Never** add `Co-Authored-By` or any footer
- **Never** add a message body (second paragraph)
- **Never** use a period at the end of the subject
- **Never** commit `.env`, `*.key`, `*.pem`, or other secrets
- Prefer staging specific files over `git add -A`
- If pre-commit hooks fail, fix the issue and create a **new** commit (do not amend)

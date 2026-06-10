---
name: release-notes
description: Generate grouped Markdown release notes from git history between two tags or refs
---

# /release-notes — Generate Release Notes

Render grouped Markdown release notes from the conventional-commit history between two refs.
Read-only: it summarizes what `/commit` already recorded — it does not write files or tag.

## Dynamic Context

```bash
git describe --tags --abbrev=0 2>/dev/null || echo "(no tags)"
```

```bash
git tag --sort=-v:refname | head -20
```

```bash
git log --oneline --decorate -10
```

## Instructions

The user wants release notes for a range of commits. Resolve the range, read the commit
subjects, group them by conventional-commit type, and print Markdown notes. Do not write files
or change git state unless the user explicitly asks.

### Arguments

`/release-notes [<from-ref>] [<to-ref>]`

- `<to-ref>` — the end of the range (inclusive); defaults to `HEAD`.
- `<from-ref>` — the start (exclusive); defaults to the previous tag
  (`git describe --tags --abbrev=0 <to-ref>^`).
- For a meaningful range, pass an explicit `<from-ref>` (the last version you shipped or
  announced) — especially in repos that tag frequently (e.g. every commit), where the
  previous-tag default spans only one commit. Examples:
  - `/release-notes v1.25.0` → notes from `v1.25.0` to `HEAD`.
  - `/release-notes v1.25.0 v1.28.0` → notes for that span.
  - `/release-notes` → just the latest change (previous tag to `HEAD`).

### Process

1. **Resolve refs.** Set `to` (default `HEAD`) and `from`
   (default `$(git describe --tags --abbrev=0 "$to^")`). Verify both with `git rev-parse --verify`.
2. **Collect commits** in the range, newest first:
   ```bash
   git log --no-merges --pretty=format:'%h%x09%s' "<from>..<to>"
   ```
3. **Parse each subject** as `<type>[(scope)][!]: <subject>` — capture `type`, optional `scope`,
   and the breaking flag (`!` before the colon). Subjects that don't match go to **Other**.
4. **Group** into sections, in this order, omitting any that are empty:
   - **⚠ Breaking Changes** — every commit whose subject has `!` (listed here only, not also under
     its type).
   - **Features** — `feat`
   - **Fixes** — `fix`
   - **Performance** — `perf`
   - **Refactors** — `refactor`
   - **Docs** — `docs`
   - **Other** — `chore`, `ci`, `style`, `test`, `revert`, and anything unparseable
5. **Render Markdown** and print it (do not write a file):
   - Heading: `## <to-ref> — <YYYY-MM-DD>`, where the date is
     `git log -1 --format=%ad --date=short "<to>"`.
   - Under each section, one bullet per commit: `` - <subject> (`<hash>`) ``. Keep the scope as
     written in the subject; do not rewrite the wording.
6. If the range is empty, say so (e.g., "No commits between `<from>` and `<to>`.").

### Rules

- **Read-only.** Never commit, tag, push, or write files unless the user explicitly asks; this
  skill only summarizes existing history.
- Group strictly by the commit `type`; never editorialize or invent content beyond the recorded
  subjects.
- A commit marked breaking (`!`) is listed once, under **⚠ Breaking Changes**.
- `<from>` is exclusive and `<to>` is inclusive (standard `git log A..B` semantics).

---
name: init-skill
description: Scaffold a new skill at skills/<name>/SKILL.md following the standard structure
disable-model-invocation: true
---

# /init-skill — Scaffold a Skill

Create a new skill from the canonical structure used by all skills in this repo.

## Instructions

The user wants to create a new skill. Generate `skills/<name>/SKILL.md` with valid YAML frontmatter and the standard section layout matching `skills/commit/SKILL.md`, `skills/init-bash/SKILL.md`, and `skills/init-python/SKILL.md`.

### Arguments

The user must provide:
- A skill name in `kebab-case` (e.g., `release-notes`, `gen-test`)
- A one-line description (used as the frontmatter `description:` and as `/<name> — <subject>` in the heading)

The user may optionally provide:
- `--user-only` — set `disable-model-invocation: true` in the frontmatter (skill is invokable only via `/<name>`, not by Claude during a session). Use for skills with side effects (deploy, commit, send).
- `--with-dynamic-context` — include a `## Dynamic Context` section with placeholder bash blocks (modeled on `skills/commit/SKILL.md`). Use for skills that need fresh git/system state at invocation.

### Process

1. Validate the name is `kebab-case` (lowercase letters, digits, dashes only — no underscores or capitals). Reject otherwise.
2. Verify `skills/<name>/` does not already exist. Refuse to overwrite.
3. Create the directory `skills/<name>/`.
4. Write `skills/<name>/SKILL.md` with the frontmatter and section stubs shown below.
5. Remind the user to add the new skill to:
   - `CLAUDE.md` Skills table
   - `README.md` Skills (Slash Commands) table

### Skill Structure

The scaffolded `SKILL.md` follows this layout:

```markdown
---
name: <name>
description: <one-line description>
[disable-model-invocation: true]   # only if --user-only
---

# /<name> — <Subject>

<one-paragraph summary of what the skill does>

[## Dynamic Context                # only if --with-dynamic-context

```bash
# fresh state to inject at invocation, e.g.:
git status -s
```
]

## Instructions

<what the user wants and the high-level approach>

### Arguments

The user must provide:
- <required arg>

The user may optionally provide:
- <optional arg or flag>

### Process

1. <first step>
2. <second step>
3. <final step — show result, confirm, etc.>

### Rules

- <hard constraints, e.g., never overwrite, never commit secrets>
```

### Naming

- Skill name must be `kebab-case` (e.g., `release-notes`, not `release_notes` or `ReleaseNotes`)
- The skill directory name and the frontmatter `name:` field must match
- The heading `# /<name> — <Subject>` uses the same `<name>` followed by an em-dash and a noun phrase

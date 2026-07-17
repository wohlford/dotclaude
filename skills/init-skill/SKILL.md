---
name: init-skill
description: Scaffold a new skill at skills/<name>/SKILL.md following the standard structure
---

# /init-skill — Scaffold a Skill

Create a new skill from the canonical structure used by all skills in this repo.

## Instructions

The user wants to create a new skill. Generate `skills/<name>/SKILL.md` with valid YAML frontmatter and the standard section layout matching `skills/commit/SKILL.md`, `skills/init-bash/SKILL.md`, `skills/init-js/SKILL.md`, and `skills/init-python/SKILL.md`.

### Arguments

The user must provide:
- A skill name in `kebab-case` (e.g., `gen-test`, `api-doc`)
- A one-line description (used verbatim as the frontmatter `description:`; the heading's `<Subject>` is a short Title-Case noun phrase distilled from it — e.g., description "Create a git commit with automatic semver tagging…" → heading `# /commit — Create a Git Commit`)

The user may optionally provide:
- `--user-only` — set `disable-model-invocation: true` in the frontmatter (skill is invokable only via `/<name>`, not by Claude during a session). Use it only when the skill is **outward-facing or irreversible** (e.g. `skills/propagate/SKILL.md` pushes to a remote), or when the act is the **user's prerogative** to authorise — in which case state that reason in the skill's own summary using the literal phrase `the user's call`, since that exact wording is the marker `skill-reviewer` greps for; a paraphrase reads like a reason but fails the check. Writing a file is not itself a reason: Claude can Write the same file directly, so the flag would block only the template. See `agents/skill-reviewer.md` for the canonical rule.
- `--with-dynamic-context` — include a `## Dynamic Context` section with placeholder bash blocks (modeled on `skills/commit/SKILL.md`). Use for skills that need fresh git/system state at invocation.

### Process

1. If the name or the one-line description was not provided, ask the user for it before proceeding. Validate the name is `kebab-case` (lowercase letters, digits, dashes only — no underscores or capitals). If it isn't, stop and ask the user for a corrected kebab-case name.
2. Verify `skills/<name>/` does not already exist. If it does, stop and ask the user for a different name or explicit confirmation before touching the existing skill.
3. Create the directory `skills/<name>/`.
4. Write `skills/<name>/SKILL.md` with the frontmatter and section stubs shown below. Include the `disable-model-invocation: true` line only if `--user-only` was given, and the `## Dynamic Context` section only if `--with-dynamic-context` was given (the template's bracket comments mark both). Substitute `<name>`, `<one-line description>`, and `<Subject>` with the provided values; leave every other bracketed placeholder in the body verbatim as an authoring prompt — do not invent Process steps or Rules from the one-line description; a later authoring pass (the user, or a follow-up session working the skill's content) fills those in before `/vet`.
5. Run `/sync-docs` to regenerate any `<!-- sync:skills -->` index tables in the repo (CLAUDE.md, README.md, skills/README.md, etc.). The new skill registers automatically.
6. **Nudge a review.** Once the content is authored (beyond the stubs), suggest running `/vet skills/<name>/SKILL.md` — it dispatches `skill-reviewer` (structure) + `skill-content-reviewer` (content).

### Skill Structure

The scaffolded `SKILL.md` follows this layout:

````markdown
---
name: <name>
description: <one-line description>
[disable-model-invocation: true]   # only if --user-only; a prerogative claim must say "the user's call" in the summary
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
````

### Naming

- Skill name must be `kebab-case` (e.g., `gen-test`, not `gen_test` or `GenTest`)
- The skill directory name and the frontmatter `name:` field must match
- The heading `# /<name> — <Subject>` uses the same `<name>` followed by an em-dash and a short Title-Case noun phrase

### Rules

- Reject a name that isn't `kebab-case` — stop and ask the user for a corrected name.
- Never overwrite an existing skill directory — if `skills/<name>/` exists, stop and ask.
- The frontmatter `name:` must match the directory name exactly.

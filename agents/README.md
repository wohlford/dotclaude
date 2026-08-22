# Agents

Specialized subagents shipped with this `~/.claude/` configuration. Each agent lives as a single `.md` file with YAML frontmatter declaring its `name`, `description`, `model`, and `tools`.

## Model policy

Pick the `model:` from the **task**, not the artifact's importance. Match models, never versions —
write the alias (`haiku`, `sonnet`, `opus`, `fable`), never a pinned id like `claude-sonnet-5`, so
agents ride model upgrades for free.

| Tier | Use for | Test |
| :----- | :------ | :--- |
| `haiku` | Compliance against an explicit rubric — is a field present, does a name match its directory, does a required section exist | The rubric answers it; no interpretation needed |
| `sonnet` | Judgment against a rubric **with carve-outs and exceptions** — prose quality, idiom, "is this exempt?" | A wrong call costs the caller a triage pass |
| `opus` | Architecture and design, whole-branch review, final gates | Being wrong is expensive and hard to detect |
| `fable` | Diverse-model review — a reviewer that **differs from the author** (see `/feature`) | Same-model blind spots are the risk |

Two rules that decide most cases:

- **Never ask a model to do a linter's job.** Character-level mechanics — trailing whitespace, line
  length, encodings, tabs — belong to `/audit`, which checks them exactly. A model approximating
  them produces false positives, and false positives cost more than missed nits because every
  finding is triaged by hand. This is why `/vet` (judgment) and `/audit` (mechanics) are
  complements: an agent that drifts into the other's half is a defect, not thoroughness.
- **Carve-outs pull a task up a tier.** A checklist with documented exceptions ("tests/ are exempt
  from docstrings") is no longer a checklist — deciding whether the exception applies is judgment.

## Index

Authored or edited agents are vetted with `/vet agents/<name>.md`, which dispatches `agent-reviewer`.

<!-- sync:agents cols=Agent:key,Model:auto,Purpose:auto -->
| Agent                    | Model  | Purpose                                                                                                     |
| :----------------------- | :----- | :---------------------------------------------------------------------------------------------------------- |
| `agent-reviewer`         | haiku  | Review agent definition files for compliance with the canonical agent frontmatter and structure             |
| `skill-content-reviewer` | sonnet | Review SKILL.md files for prose and content quality — clarity, completeness, consistency, and actionability |
| `skill-reviewer`         | haiku  | Review SKILL.md files for compliance with the repo's canonical skill structure                              |
| `style-reviewer`         | sonnet | Review code files for compliance with the global STYLE.md standards                                         |
<!-- /sync:agents -->

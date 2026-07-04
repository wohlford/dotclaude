# Agents

Specialized subagents shipped with this `~/.claude/` configuration. Each agent lives as a single `.md` file with YAML frontmatter declaring its `name`, `description`, `model`, and `tools`.

## Index

Authored or edited agents are vetted with `/vet agents/<name>.md`, which dispatches `agent-reviewer`.

<!-- sync:agents cols=Agent:key,Model:auto,Purpose:auto -->
| Agent                    | Model  | Purpose                                                                                                     |
| :----------------------- | :----- | :---------------------------------------------------------------------------------------------------------- |
| `agent-reviewer`         | haiku  | Review agent definition files for compliance with the canonical agent frontmatter and structure             |
| `skill-content-reviewer` | sonnet | Review SKILL.md files for prose and content quality — clarity, completeness, consistency, and actionability |
| `skill-reviewer`         | haiku  | Review SKILL.md files for compliance with the repo's canonical skill structure                              |
| `style-reviewer`         | haiku  | Review code files for compliance with the global STYLE.md standards                                         |
<!-- /sync:agents -->

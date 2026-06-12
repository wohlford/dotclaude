# Skills

User-invokable slash commands shipped with this `~/.claude/` configuration. Each skill lives in its own directory with a `SKILL.md` defining its name, description, and behavior. See [STYLE.md](../STYLE.md) "Documentation Sync" for how this index stays in sync.

## Index

<!-- sync:skills cols=Command:key,Purpose:auto -->
| Command               | Purpose                                                                                                                                            |
| :-------------------- | :------------------------------------------------------------------------------------------------------------------------------------------------- |
| `/commit`             | Create a git commit with automatic semver tagging following STYLE.md conventions; signing and identity follow git config                           |
| `/debrief`            | Run the end-of-session pre-compaction routine (CLAUDE.md refresh, memory save, automation review)                                                  |
| `/feature`            | Run the methodical, risk-tiered design pipeline for a change (triage → spec → spike → plan → reviews) and stop at a reviewed plan ready to execute |
| `/idempotency-tester` | Verify a script is idempotent by running it twice in an isolated sandbox and diffing the resulting state                                           |
| `/init-bash`          | Scaffold a new Bash script from the standard template in templates.md                                                                              |
| `/init-js`            | Scaffold a new JavaScript module from the standard template in templates.md                                                                        |
| `/init-python`        | Scaffold a new Python module from the standard template in templates.md                                                                            |
| `/init-skill`         | Scaffold a new skill at skills/<name>/SKILL.md following the standard structure                                                                    |
| `/propagate`          | Propagate committed changes from this working copy to the live ~/.claude repo (push, then fast-forward)                                            |
| `/release-notes`      | Generate grouped Markdown release notes from git history between two tags or refs                                                                  |
| `/sync-docs`          | Regenerate index regions of README.md and CLAUDE.md from authoritative sources                                                                     |
<!-- /sync:skills -->

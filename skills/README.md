# Skills

User-invokable slash commands shipped with this `~/.claude/` configuration. Each skill lives in its own directory with a `SKILL.md` defining its name, description, and behavior. See [STYLE.md](../STYLE.md) "Documentation Sync" for how this index stays in sync.

## Index

<!-- sync:skills cols=Command:key,Purpose:auto -->
| Command               | Purpose                                                                                                                                                                                                    |
| :-------------------- | :--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `/commit`             | Create a git commit with automatic semver tagging following STYLE.md conventions; signing and identity follow git config                                                                                   |
| `/debrief`            | Run the end-of-session pre-compaction routine (CLAUDE.md refresh, memory save, automation review)                                                                                                          |
| `/feature`            | Run the methodical, risk-tiered pipeline for a change (triage → spec → spike → plan → reviews), then continue through subagent-driven execution to a merged change; --plan-only stops at the reviewed plan |
| `/idempotency-tester` | Verify a script is idempotent by running it twice in an isolated sandbox and diffing the resulting state                                                                                                   |
| `/init-bash`          | Scaffold a new Bash script from the standard template in templates.md                                                                                                                                      |
| `/init-js`            | Scaffold a new JavaScript module from the standard template in templates.md                                                                                                                                |
| `/init-python`        | Scaffold a new Python module from the standard template in templates.md                                                                                                                                    |
| `/init-skill`         | Scaffold a new skill at skills/<name>/SKILL.md following the standard structure                                                                                                                            |
| `/propagate`          | Promote committed changes from this dev working copy to the live ~/.claude repo locally; --push also publishes to origin (explicit)                                                                        |
| `/sync-docs`          | Regenerate index regions of README.md and CLAUDE.md from authoritative sources                                                                                                                             |
| `/vet`                | Vet an authored skill, agent, or script — or the whole repo with --all — by dispatching the matching reviewer agent(s) and reporting their findings                                                        |
<!-- /sync:skills -->

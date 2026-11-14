# Agents

Specialized subagents shipped with this `~/.claude/` configuration. Each agent lives as a single `.md` file with YAML frontmatter declaring its `name`, `description`, `model`, `effort`, and `tools`.

## Model and effort policy

Pick the `model:` from the **task**, not the artifact's importance. Match models, never versions —
write the alias (`haiku`, `sonnet`, `opus`, `fable`), never a pinned id like `claude-sonnet-5`, so
agents ride model upgrades for free.

| Tier | Effort | Use for | Test |
| :----- | :----- | :------ | :--- |
| `haiku` | `medium` | Compliance against an explicit rubric — is a field present, does a name match its directory, does a required section exist | The rubric answers it; no interpretation needed |
| `sonnet` | `high` | Judgment against a rubric **with carve-outs and exceptions** — prose quality, idiom, "is this exempt?"; and `/feature`'s pinned author, which drafts its spec, plan and folds under Opus review | A wrong call costs the caller a triage pass |
| `opus` | `medium`; `high` for the final whole-branch review | Architecture and design, whole-branch review, final gates, the diverse-model review of `/feature`'s spec and plan (its pinned author is Sonnet, so Opus is the gate), authoring (not reviewing) standalone documentation prose | Being wrong is expensive and hard to detect |
| `fable` | `medium` | Open questions, put to it before the user (see `/feature`), and the arbiter of an author–reviewer disagreement; the diverse reviewer when the author set includes Opus | Same-model blind spots are the risk |

Two rules that decide most cases:

- **Never ask a model to do a linter's job.** Character-level mechanics — trailing whitespace, line
  length, encodings, tabs — belong to `/audit`, which checks them exactly. A model approximating
  them produces false positives, and false positives cost more than missed nits because every
  finding is triaged by hand. This is why `/vet` (judgment) and `/audit` (mechanics) are
  complements: an agent that drifts into the other's half is a defect, not thoroughness.
- **Carve-outs pull a task up a tier.** A checklist with documented exceptions ("tests/ are exempt
  from docstrings") is no longer a checklist — deciding whether the exception applies is judgment.

### Effort by tier

A dispatch also carries a reasoning-effort level. With nothing set, a dispatch on the session's own model
inherits the session's level, and a dispatch on a different model runs at that model's default, `medium` in
the one case read (measured; see Evidence), so without an explicit level a role's level follows whoever the
controller happens to be and whatever they set. A named agent carries its level as `effort:` in its frontmatter, directly after
`model:`. An ad-hoc dispatch (the Agent tool with no named agent) carries it as the `effort` parameter,
written beside the alias in one backticked span, such as `model: sonnet, effort: high`. The Effort column
of the tier table is each tier's default, for these reasons:

- `haiku` at `medium`: the evidence does not justify `low` (index 19 against 25, six points on the cheapest model), and
  nothing in the evidence justifies spending more on a role the rubric decides.
- `sonnet` at `high`: medium to high is 31 to 42, the largest step up from medium on any model (Fable +6,
  Haiku +5, Opus +4), for 1.86 times the output tokens (29.4M to 54.7M) and $0.78 to $1.64 per task.
- `opus` at `medium`: medium to high is 48 to 52 for 42.5M to 56.1M output tokens, four points that one
  sample cannot separate, and an Opus reviewer's independence comes from its different model, not from its
  level. The exception is the final whole-branch review, at `high`: that is a judgment about stakes (the last
  gate before integration, reading the whole diff), not a measured gain.
- `fable` at `medium`: medium to high is 56 to 62 for $3.80 to $9.08 per task, 2.4 times, on gates that do
  not run on most changes.

No role uses `xhigh` or `max`. Fable `xhigh` scores 62, the same as Fable `high`, on 144M against 85.3M
output tokens, and the `max` rows of Fable, Opus and Sonnet carry error rates of 2.4%, 3.0% and 2.5%. A
fallback takes the level this policy gives the model that stepped in, never the level of the model it replaced.

Precedence: the `CLAUDE_CODE_EFFORT_LEVEL` environment variable beats the Agent tool parameter and
frontmatter alike (documented, not measured here). The parameter beats the definition (measured: parameter
`max` over a definition of `low` recorded `max`), so a named agent is dispatched with no `effort` parameter.
Before relying on any level here, re-read that variable in the session and in the `env` block of every
settings scope (user, project, local, managed).

### Effort by role

These are the roles that are not agent definitions. Each named agent carries its own level, shown in the
Effort column of the Index below. The figures behind every level are under Evidence.

| Role | Model | Effort | Mechanism |
| :--- | :--- | :--- | :--- |
| Controller | the session's own | unchanged | none; a skill cannot pin it |
| `/feature` author | `sonnet` | `high` | Agent tool, `model: sonnet, effort: high` (Authoring dispatch) |
| Diverse reviewer of the spec and plan, and the fold re-read | `opus` | `medium` | Agent tool, `model: opus, effort: medium` (Diverse-model review) |
| Open-question role | `fable` | `medium` | Agent tool, `model: fable, effort: medium` (Models) |
| Open-question fallback, Opus (Fable unavailable) | `opus` | `medium` | Agent tool, `model: opus, effort: medium` (Models) |
| Open-question fallback, Sonnet (Fable unavailable, Opus in the author set) | `sonnet` | `high` | Agent tool, `model: sonnet, effort: high` (Models) |
| Arbiter | `fable` | `medium` | Agent tool, `model: fable, effort: medium` |
| Reviewer fallback when Opus is an author | `fable` | `medium` | as the arbiter |
| Reviewer, last link of the chain | `sonnet` | `high` | Agent tool, `model: sonnet, effort: high` |
| SDD implementer, transcription | `haiku` | `medium` | Agent tool, `model: haiku, effort: medium` (Execute and integrate) |
| SDD implementer, integration or judgment | `sonnet` | `high` | Agent tool, `model: sonnet, effort: high` |
| Fix rounds 4 and 5 (fresh Sonnet) | `sonnet` | `high` | as the integration implementer |
| SDD task reviewer | `sonnet` | `high` | as the integration implementer |
| Final whole-branch review | `opus` | `high` | Agent tool, `model: opus, effort: high` |
| Final review fallback, Fable | `fable` | `medium` | Agent tool, `model: fable, effort: medium` |
| Final review fallback, Sonnet | `sonnet` | `high` | Agent tool, `model: sonnet, effort: high` |
| `/vet` | none (controller) | each reviewer's own | dispatches by `subagent_type`, sets nothing itself |
| `/debrief` | none (controller) | unchanged | its one delegate is `prose-writer` |

### Evidence

Source: artificialanalysis.ai, the comparison table at `https://artificialanalysis.ai/providers/anthropic`
and each row's own page under `https://artificialanalysis.ai/models/`. Index version: Intelligence Index
v5.0.1 (nine evaluations: AA-Briefcase v1.1, AutomationBench-AA, Terminal-Bench 4.0, FrontierCode 2.0,
AA-Omniscience v1.1, GDP.pdf, HLE-Diamond, CritPt v2 Hard Subset, Terminal-Bench-Science 0.1). Read date:
2026-11-10. Models: Fable 5.2, Opus 5.5, Sonnet 5.5, Haiku 5.5. Every row was read twice, once from the
provider table and once from its own page, and the two agreed on index and cost for all 20 rows. "Cost/task"
is at API list price; "Out tok" and "In tok" are totals for the whole index run; "Err" is the provider
table's error-rate column. API list prices, input and output per 1M tokens: Fable 5.2 $8 and $40, Opus 5.5
$4 and $20, Sonnet 5.5 $2 and $10, Haiku 5.5 $0.10 and $0.50.

| Model | Effort | Index | Cost/task | Min/task | First answer token (s) | Out tok | In tok | Err | Slug |
| :--- | :--- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | :--- |
| Fable 5.2 | low | 54 | $3.10 | 6.5 | 8.73 | 34.9M | 1.98B | 0.0% | claude-fable-5-2-low |
| Fable 5.2 | medium | 56 | $3.80 | 8.1 | 13.45 | 41M | 2.6B | 0.0% | claude-fable-5-2-medium |
| Fable 5.2 | high | 62 | $9.08 | 17.5 | 30.79 | 85.3M | 7.89B | 0.0% | claude-fable-5-2-high |
| Fable 5.2 | xhigh | 62 | $14.83 | 26.1 | 73.44 | 144M | 14.1B | 0.0% | claude-fable-5-2-xhigh |
| Fable 5.2 | max | 65 | $26.54 | 44.4 | 235.06 | 275M | 28.3B | 2.4% | claude-fable-5-2 |
| Opus 5.5 | low | 35 | $0.98 | 3.0 | 4.99 | 21.1M | 1.17B | 0.0% | claude-opus-5-5-low |
| Opus 5.5 | medium | 48 | $2.45 | 7.1 | 13.71 | 42.5M | 3.25B | 0.0% | claude-opus-5-5-medium |
| Opus 5.5 | high | 52 | $3.46 | 9.5 | 24.49 | 56.1M | 4.74B | 0.0% | claude-opus-5-5-high |
| Opus 5.5 | xhigh | 57 | $6.05 | 16.2 | 78.56 | 101M | 9.02B | 0.0% | claude-opus-5-5-xhigh |
| Opus 5.5 | max | 59 | $10.04 | 25.1 | 369.72 | 218M | 15.6B | 3.0% | claude-opus-5-5 |
| Sonnet 5.5 | low | 26 | $0.51 | 2.6 | 2.52 | 21.3M | 1.09B | 0.0% | claude-sonnet-5-5-low |
| Sonnet 5.5 | medium | 31 | $0.78 | 3.6 | 4.48 | 29.4M | 1.66B | 0.0% | claude-sonnet-5-5-medium |
| Sonnet 5.5 | high | 42 | $1.64 | 7.0 | 9.59 | 54.7M | 3.78B | 0.0% | claude-sonnet-5-5-high |
| Sonnet 5.5 | xhigh | 50 | $4.09 | 15.0 | 26.38 | 111M | 11.2B | 0.7% | claude-sonnet-5-5-xhigh |
| Sonnet 5.5 | max | 55 | $10.02 | 31.4 | 260.60 | 335M | 30.6B | 2.5% | claude-sonnet-5-5 |
| Haiku 5.5 | low | 19 | $0.04 | 2.2 | 6.05 | 29.9M | 1.47B | 0.0% | claude-haiku-5-5-low |
| Haiku 5.5 | medium | 25 | $0.09 | 4.0 | 9.51 | 52.7M | 3.13B | 0.0% | claude-haiku-5-5-medium |
| Haiku 5.5 | high | 30 | $0.16 | 6.5 | 18.96 | 89.8M | 5.52B | 0.0% | claude-haiku-5-5-high |
| Haiku 5.5 | xhigh | 35 | $0.25 | 9.6 | 49.57 | 151M | 8.76B | 0.0% | claude-haiku-5-5-xhigh |
| Haiku 5.5 | max | 36 | $0.41 | 14.8 | 181.16 | 298M | 14.6B | 0.0% | claude-haiku-5-5 |

Baseline, measured 2026-11-10 on `claude` 2.1.322 (some earlier records written by 2.1.316): with nothing
set, every dispatch recorded `medium`, same-model and cross-model alike, while the main session itself
recorded `medium`, although the user settings hold `high` (why was not established), so those readings
could not tell inheriting the session's level from a default of `medium`. A fixture run with the parent
session forced to `high` (`claude -p --model haiku --effort high`, whose own records read `high`) separated
them: three dispatches with nothing set recorded `high` for a `model: haiku` definition and `high` for a
definition with no `model:` line (both on the session's model), and `medium` for a `model: sonnet`
definition (a different model). That is one run, a Haiku parent and one cross-model case, so the
cross-model default is read for Sonnet only. The recorded level is the `effort` field
on every `assistant` record of a subagent transcript
(`~/.claude/projects/<dir>/<session>/subagents/agent-<id>.jsonl`), read beside `message.model` and the
sibling `.meta.json`'s `agentType`. Both mechanisms were measured to take effect on 2.1.322: the Agent tool
parameter, and `effort:` in an agent definition (exercised on Haiku only), with the parameter overriding the
definition.

Limits: the table has no error bars, and one index run is one sample per row, so a gap of a few points
(Opus medium 48 against high 52) is a direction, not a measured difference. The nine evaluations are generic
tasks in the site's own harness, not this repository's roles, and no per-evaluation scores were read. The
pages were read through a summarising fetch tool and cross-checked, not copied by hand. Cost is API list
pricing and says nothing about how a subscription allocation is charged. Nothing here shows that any level
improves outcomes on this repository's work.

Re-read this section, and treat every assignment as "as of 2026-11-10, v5.0.1" until then, when any of
these happens:

1. The model id recorded in a dispatch's transcript (`message.model` on every `assistant` record) no longer
   maps to the names above. The mapping is `claude-opus-5-5` to Opus 5.5, `claude-sonnet-5-5` to Sonnet 5.5,
   `claude-fable-5-2` to Fable 5.2 and `claude-haiku-5-5` to Haiku 5.5.
2. Claude Code release notes or docs change the default effort.
3. A new index version is published.

A moved alias is the event that makes this section stale, not an error anyone could have prevented: the
alias rule above stays.

## Index

Authored or edited agents are vetted with `/vet agents/<name>.md`, which dispatches `agent-reviewer`.

<!-- sync:agents cols=Agent:key,Model:auto,Effort:auto,Purpose:auto -->
| Agent                    | Model  | Effort | Purpose                                                                                                                                                                                                                        |
| :----------------------- | :----- | :----- | :----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `agent-reviewer`         | haiku  | medium | Review agent definition files for compliance with the canonical agent frontmatter and structure                                                                                                                                |
| `prose-writer`           | opus   | medium | Write or rewrite standalone documents a reader opens to learn how the project works — README, architecture and reference docs, policy guides, release notes; author mode reads the tree, revise mode rewrites a supplied draft |
| `skill-content-reviewer` | sonnet | high   | Review SKILL.md files for prose and content quality — clarity, completeness, consistency, and actionability                                                                                                                    |
| `skill-reviewer`         | haiku  | medium | Review SKILL.md files for compliance with the repo's canonical skill structure                                                                                                                                                 |
| `style-reviewer`         | sonnet | high   | Review code files for compliance with the global STYLE.md standards                                                                                                                                                            |
<!-- /sync:agents -->

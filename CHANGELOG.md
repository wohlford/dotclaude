# Changelog

All notable changes — one entry per released brick, mirroring its annotated tag. The full per-brick
history also lives in the annotated tags (`git log`).

## v0.63.0 — 2026-07-29
- feat(publish): add the per-brick engine and the fold planner

## v0.62.0 — 2026-07-29
- feat(changelog-entry): add the insert-only brick-entry helper

## v0.61.2 — 2026-07-29
- fix(publication-push-guard): log what an internal error was evaluating

## v0.61.1 — 2026-07-29
- docs(CLAUDE.md): equal counts are not equal sets

## v0.61.0 — 2026-07-28
- feat(debrief): make backlog.py the only writer to BACKLOG.md

## v0.60.6 — 2026-07-28
- docs(CLAUDE.md): pair a derived input with a declared floor

## v0.60.5 — 2026-07-28
- docs(CLAUDE.md): index PIPESTATUS by position, not [0]

## v0.60.4 — 2026-07-28
- fix(hooks): run every git_command suite when the tokenizer changes

## v0.60.3 — 2026-07-28
- docs(CLAUDE.md): assert the shape of a programmatic document edit

## v0.60.2 — 2026-07-28
- docs(CLAUDE.md): distrust a green suite you wrote for your own fix

## v0.60.1 — 2026-07-28
- fix(git_command): stop a heredoc body drifting the quote state

## v0.60.0 — 2026-07-28
- feat(propagate): wire the preflight into the publish start-invariant

## v0.59.0 — 2026-07-28
- feat(publish-preflight): script the publish start-invariant

## v0.58.9 — 2026-07-28
- docs(CLAUDE.md): distrust a probe's verdict your environment decided

## v0.58.8 — 2026-07-28
- docs(CLAUDE.md): watch a regression test fail before trusting its pass

## v0.58.7 — 2026-07-28
- fix(markdownlint): lint from the directory holding the config

## v0.58.6 — 2026-07-28
- docs(CLAUDE.md): re-measure a deferral before building its fix

## v0.58.5 — 2026-07-28
- fix(sync-docs): reject a directive the handler cannot honor

## v0.58.4 — 2026-07-28
- docs(CLAUDE.md): add the stale-tooling hazard and a measured instance

## v0.58.3 — 2026-07-28
- docs(CLAUDE.md): replace the duplicated indexes with a README pointer

## v0.58.2 — 2026-07-27
- fix(sync-docs): make filter= match reliably or fail loudly

## v0.58.1 — 2026-07-27
- docs(CLAUDE.md): group the verification hazards and gate admissions

## v0.58.0 — 2026-07-27
- feat(feature,workflows): present the design instead of pausing

## v0.57.5 — 2026-07-27
- fix(hooks): alarm when a test-runner suite is missing or unrunnable

## v0.57.4 — 2026-07-27
- fix(feature,propagate): gate on the audit RESULT verdict line

## v0.57.3 — 2026-07-27
- fix(audit): emit a machine-readable RESULT verdict line

## v0.57.2 — 2026-07-27
- docs(CLAUDE.md): add the instrument-class verification rule

## v0.57.1 — 2026-07-27
- fix(feature): restore the security gate in adopted repos

## v0.57.0 — 2026-07-27
- feat(security-reviewer): add the fallback security review agent

## v0.56.3 — 2026-07-27
- docs(CLAUDE.md): add the evidence-not-instruction and liveness rules

## v0.56.2 — 2026-07-27
- docs(recast): note the commit-subject gate on replays

## v0.56.1 — 2026-07-27
- fix(commit): assert the tag exists after tagging

## v0.56.0 — 2026-07-27
- feat(commit-subject): activate the gate in this repo

## v0.55.0 — 2026-07-27
- feat(commit-subject): add the suite-runner hook

## v0.54.0 — 2026-07-27
- feat(commit-subject): add the two-tier commit-subject gate

## v0.53.0 — 2026-07-27
- feat(commit-subject): add shared policy and tier helpers

## v0.52.5 — 2026-07-27
- fix(hooks): keep the python gates alive under python 3.9

## v0.52.4 — 2026-07-27
- docs(CLAUDE.md): treat a combination-only fix as one unit of work

## v0.52.3 — 2026-07-26
- fix(push-guard): see nested contexts and fail closed on git ambiguity

## v0.52.2 — 2026-07-26
- fix(publication-push-guard): judge pushes in nested command contexts

## v0.52.1 — 2026-07-26
- fix(git_command): walk git invocations across nested command contexts

## v0.52.0 — 2026-07-26
- feat(git_command): add a quote-aware nested command-context scanner

## v0.51.4 — 2026-07-26
- docs(CLAUDE.md): treat a killed run as unproven, not as a pass

## v0.51.3 — 2026-07-25
- fix(propagate): unquote the marker parse so the push gate allows it

## v0.51.2 — 2026-07-25
- docs(CLAUDE.md): route open questions to Fable before the user

## v0.51.1 — 2026-07-25
- docs(CLAUDE.md): group verification hazards, add unrun-command rule

## v0.51.0 — 2026-07-25
- feat(feature): prove design records durable, route ignored to memory

## v0.50.0 — 2026-07-25
- feat(feature,workflows): let only one agent run the test suite

## v0.49.9 — 2026-07-25
- docs(CLAUDE.md): warn against skipping the last gate before publishing

## v0.49.8 — 2026-07-24
- fix(publication-push-guard): treat a non-literal subcommand as unresolvable

## v0.49.7 — 2026-07-24
- fix(git_command): fold line continuations before rewriting newlines

## v0.49.6 — 2026-07-24
- docs(CLAUDE.md): extend the exit-status and history-rewrite standing rules

## v0.49.5 — 2026-07-24
- chore(markdownlint): widen the superpowers ignore to the whole directory

## v0.49.4 — 2026-07-24
- fix(propagate): state the publish path cannot remove published content

## v0.49.3 — 2026-07-24
- fix(propagate): assert production is on the marker's branch before merging

## v0.49.2 — 2026-07-24
- docs(publication-model): the orphan cutover shipped, not forthcoming

## v0.49.1 — 2026-07-23
- docs(CLAUDE.md): add history-rewrite and time-of-day standing rules

## v0.49.0 — 2026-07-23
- feat(sync-docs): skip handler blocks declared external in project config

## v0.48.12 — 2026-07-23
- docs(CLAUDE.md): warn a pipeline's exit status is the last command's

## v0.48.11 — 2026-07-20
- docs(STYLE.md): require the exec bit on sourced libraries too

## v0.48.10 — 2026-07-20
- style(shellcheck-check): use the long-form severity flag

## v0.48.9 — 2026-07-19
- docs(skills): address vet content findings across skills

## v0.48.8 — 2026-07-19
- fix(init-js): check the target module type before scaffolding ESM

## v0.48.7 — 2026-07-19
- fix(commit): add base-tag fallback and tag-recovery guidance

## v0.48.6 — 2026-07-19
- fix(propagate): correct the crash-recovery tag-delete order

## v0.48.5 — 2026-07-19
- docs(guards): document tokenizer and guard contracts, annotate gitcmd

## v0.48.4 — 2026-07-19
- refactor(sync-docs): narrow the extractor catch and document handler contracts

## v0.48.3 — 2026-07-19
- style(scripts): conform shell scripts to STYLE.md

## v0.48.2 — 2026-07-19
- test(publication-push-guard): add cutover force-push cross-check cases

## v0.48.1 — 2026-07-18
- docs(publication-model): document the publish engine and built state

## v0.48.0 — 2026-07-18
- feat(propagate): make /propagate aware of the dev/main publication model

## v0.47.0 — 2026-07-18
- feat(commit): default to no-tag on dev of an adopted repo

## v0.46.0 — 2026-07-18
- feat(feature): re-derive onto dev as the marker-scoped adopted-repo finish

## v0.45.5 — 2026-07-18
- chore(publication): adopt the dev-main publication model

## v0.45.4 — 2026-07-18
- fix(git-guard): detect git-remote publishing by subcommand, not substring

## v0.45.3 — 2026-07-18
- fix(publication-guard): close shell-quote-split bypass of the git/gitdir pre-checks

## v0.45.2 — 2026-07-18
- docs(publication-guard): reconcile dormancy claims with the non-adopted root-unknown block

## v0.45.1 — 2026-07-18
- docs(publication-model): document the .publication.toml marker and dev-block

## v0.45.0 — 2026-07-18
- feat(hooks): wire publication-guard hook + shared-dep test runner

## v0.44.0 — 2026-07-18
- feat(publication-guard): add fail-closed guard barring dev from remotes

## v0.43.8 — 2026-07-18
- refactor(git-command): extract shared shell-command tokenizer into scripts/lib

## v0.43.7 — 2026-07-17
- fix(init): list install targets, exclude non-installable imports

## v0.43.6 — 2026-07-17
- fix(feature): mechanize re-triage's opus re-run and scope its exclusions

## v0.43.5 — 2026-07-17
- fix(vet): normalize discovered paths at discovery, before dispatch

## v0.43.4 — 2026-07-17
- fix(init): surface presumed-uninstalled imports in the closing step

## v0.43.3 — 2026-07-17
- fix(feature): specify what late re-triage re-runs and what it skips

## v0.43.2 — 2026-07-17
- test(scaffold): assert exact missing-token set equality, not subset

## v0.43.1 — 2026-07-17
- test(scaffold): skip instead of fail when python3 is unavailable

## v0.43.0 — 2026-07-17
- feat(debrief): run the routine to completion without pausing

## v0.42.9 — 2026-07-17
- test(scaffold): add reference-coverage test for init-bash no-args strip

## v0.42.8 — 2026-07-17
- fix(feature): drop the spike's unstated time bound and align workflows.md

## v0.42.7 — 2026-07-17
- fix(feature): name which checklist each ultrathink step must not repeat

## v0.42.6 — 2026-07-17
- fix(feature): anchor full-lane "high stakes" to Step 0's stakes axis

## v0.42.5 — 2026-07-17
- fix(feature): define the fast lane's stakes band instead of "high stakes"

## v0.42.4 — 2026-07-17
- fix(feature): retitle the lanes for risk, not uncertainty alone

## v0.42.3 — 2026-07-17
- fix(feature): judge Step 0 risk on two axes, not uncertainty alone

## v0.42.2 — 2026-07-17
- docs(CLAUDE.md): warn a line-based grep misses a phrase that wraps

## v0.42.1 — 2026-07-17
- docs(CLAUDE.md): warn the SDD ledger has no plan identity

## v0.42.0 — 2026-07-17
- feat(feature): check each task's verification, not just its content

## v0.41.18 — 2026-07-17
- fix(vet): resolve paths to absolute before dispatching reviewers

## v0.41.17 — 2026-07-17
- fix(feature): say what to do when /audit cannot run

## v0.41.16 — 2026-07-17
- fix(feature): say the diverse review saw the design, not just the plan

## v0.41.15 — 2026-07-17
- fix(recast): name the deviation-file obligation in both directions

## v0.41.14 — 2026-07-17
- fix(workflows): label the security step as full lane

## v0.41.13 — 2026-07-16
- docs(CLAUDE.md): warn grep -F treats a newline as alternation

## v0.41.12 — 2026-07-16
- style(recast): title-case Gemini and settle the brick plan spelling

## v0.41.11 — 2026-07-16
- fix(feature): say what to do when the user declines the plan

## v0.41.10 — 2026-07-16
- fix(init-python): put package installation out of scope like init-js

## v0.41.9 — 2026-07-16
- fix(recast): document the deviation-file that --strip obligates

## v0.41.8 — 2026-07-16
- fix(feature): route every security-flagged change to the full lane

## v0.41.7 — 2026-07-16
- docs(CLAUDE.md): drop the deprecated /ultrareview from the plugin table

## v0.41.6 — 2026-07-16
- fix(init-bash): follow INPUT_FILE out of main when scaffolding a no-arg script

## v0.41.5 — 2026-07-16
- fix(feature): fold the fast lane's diverse-review findings before presenting

## v0.41.4 — 2026-07-16
- fix(recast): stop asking the caller about imported-but-atomic files

## v0.41.3 — 2026-07-16
- fix(propagate): confirm the settings.json blocker from the merge error

## v0.41.2 — 2026-07-16
- fix(audit): relay stderr on a usage error instead of an empty summary

## v0.41.1 — 2026-07-16
- fix(init): make the scaffolders agree on parent dirs and the exec bit

## v0.41.0 — 2026-07-16
- feat(skills): judge model-invocation by risk and prerogative, not by writing

## v0.40.0 — 2026-07-16
- feat(feature): gate the merge on /audit and vet touched skills and agents

## v0.39.0 — 2026-07-16
- feat(feature): scale execution by tier and end duplicate self-review

## v0.38.0 — 2026-07-16
- feat(models): add a tier policy and match each process to it

## v0.37.0 — 2026-07-16
- feat(debrief): stop at the reviewed plan and follow up on deferrals

## v0.36.0 — 2026-07-16
- feat(feature): run /security-review on the diff when triage flags security

## v0.35.0 — 2026-07-16
- feat(vet): add the reciprocal /audit pointer for the mechanical half

## v0.34.0 — 2026-07-16
- feat(recast): dispatch /idempotency-tester for the provisioning double-run

## v0.33.0 — 2026-07-16
- feat(recast): forbid /commit for brick commits and tags

## v0.32.0 — 2026-07-16
- feat(debrief): route SDD task commits through /commit in the foreground

## v0.31.0 — 2026-07-16
- feat(workflows): route commit primitives through /commit

## v0.30.0 — 2026-07-16
- feat(feature): route pipeline commits through /commit in the foreground

## v0.29.2 — 2026-07-16
- docs(CONTRIBUTING.md): generalize conventions and expand never-commit

## v0.29.1 — 2026-07-05
- docs(hooks): note per-edit hooks trip on invalid intermediate states

## v0.29.0 — 2026-07-05
- feat(hooks): add audit-test hook running the audit suite on engine edits

## v0.28.1 — 2026-07-04
- perf(audit): drop per-file git cat-file forks from the exec-bit check

## v0.28.0 — 2026-07-04
- feat(audit): add .auditignore scoping and offender caps

## v0.27.0 — 2026-07-04
- feat(audit): add /audit skill doc and register it in the skill indexes

## v0.26.1 — 2026-07-04
- fix(audit): iterate file lists safely instead of xargs word-splitting

## v0.26.0 — 2026-07-04
- feat(audit): add mechanical compliance sweep engine and test suite

## v0.25.3 — 2026-07-04
- fix(skills): correct contradictions and gaps found by vet audit

## v0.25.2 — 2026-07-04
- style(recast-commit-gate): convert forced-path handling to pathlib

## v0.25.1 — 2026-07-04
- style(md-links-check): add type hints and docstrings, use pathlib

## v0.25.0 — 2026-07-04
- feat(exec-bit-guard): add test-runner hook and wire the gate into settings

## v0.24.0 — 2026-07-04
- feat(exec-bit-guard): add commit gate blocking scripts committed without exec bit

## v0.23.3 — 2026-07-04
- fix(exec-bit): restore exec bits and add tracked-shebang integrity audit

## v0.23.2 — 2026-07-04
- docs(hooks): warn that the exec bit is load-bearing for wired hooks

## v0.23.1 — 2026-07-04
- docs(env): note npm-global CLI PATH requirement and markdownlint-cli2

## v0.23.0 — 2026-07-04
- feat(markdownlint): adopt lenient repo config and fix genuine markdown findings

## v0.22.0 — 2026-07-04
- feat(markdownlint): add test-runner hook and wire both hooks into settings

## v0.21.0 — 2026-07-04
- feat(markdownlint): add markdownlint-cli2 opt-in lint hook

## v0.20.0 — 2026-07-04
- feat(md-links-check): add test-runner hook and wire both hooks into settings

## v0.19.0 — 2026-07-04
- feat(md-links-check): add markdown link and anchor checker hook

## v0.18.0 — 2026-07-04
- feat(style-check): validate TOML files with tomllib

## v0.17.1 — 2026-07-04
- chore(env): de-pin NVM and Node versions in environment notes

## v0.17.0 — 2026-07-04
- feat(workflows): require a failing regression test before every bugfix

## v0.16.0 — 2026-07-04
- feat(recast): add /recast skill and commit-gate

## v0.15.2 — 2026-07-04
- docs(index): add TESTING and README indexes; regenerate all sync-docs tables

## v0.15.1 — 2026-07-04
- docs(hooks): add HOOKS.md guide for authoring PreToolUse and PostToolUse hooks

## v0.15.0 — 2026-07-04
- feat(install): add installer that symlinks tracked config into ~/.claude

## v0.14.0 — 2026-07-04
- feat(debrief): add /debrief end-of-session routine orchestrating memory and automation review

## v0.13.0 — 2026-07-04
- feat(feature): add /feature risk-tiered change pipeline with diverse-model review

## v0.12.0 — 2026-07-04
- feat(propagate): add /propagate (local-default) and push-guard hook

## v0.11.0 — 2026-07-04
- feat(idempotency-tester): add /idempotency-tester skill with sandbox harness and pytest suite

## v0.10.0 — 2026-07-04
- feat(vet): add /vet skill dispatching reviewer agents over skills, agents, and scripts

## v0.9.0 — 2026-07-04
- feat(agents): add skill, agent, and style reviewer subagents with README

## v0.8.0 — 2026-07-04
- feat(sync-docs-hooks): add index-drift and test-runner hooks for sync-docs

## v0.7.0 — 2026-07-04
- feat(sync-docs): add index-region generator with marker system and pytest suite

## v0.6.0 — 2026-07-04
- feat(lint-hooks): add shellcheck and ruff edit-time hooks with ruff regression suite

## v0.5.0 — 2026-07-04
- feat(style-check): add STYLE.md edit-time validator with regression suite and test hook

## v0.4.0 — 2026-07-04
- feat(guard-secrets): add secret-file deny-gate with regression suite and test hook

## v0.3.0 — 2026-07-04
- feat(init): add init-bash, init-js, init-python, and init-skill scaffolders

## v0.2.0 — 2026-07-04
- feat(commit): add /commit skill with semver tagging and scope guidance

## v0.1.0 — 2026-07-04
- feat(templates): add Bash, Python, and JavaScript starter templates

## v0.0.1 — 2026-07-04
- chore(seed): scaffold repo with standards, docs, and base config

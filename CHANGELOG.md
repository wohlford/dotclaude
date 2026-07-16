# Changelog

All notable changes — one entry per released brick, mirroring its annotated tag. The full per-brick
history also lives in the annotated tags (`git log`).

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

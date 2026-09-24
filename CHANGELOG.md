# Changelog

All notable changes — one entry per released brick, mirroring its annotated tag. The full per-brick
history also lives in the annotated tags (`git log`).

## v0.111.3 — 2026-09-17
- docs(CLAUDE.md): admit the documented-recovery hazard

## v0.111.2 — 2026-09-17
- docs(CLAUDE.md): point bulk edits at bulk_edit.py

## v0.111.1 — 2026-09-17
- test(bulk_edit): add its mutation campaign

## v0.111.0 — 2026-09-17
- feat(bulk_edit): add the bulk-mechanical-edit safety kit

## v0.110.7 — 2026-09-17
- docs(CLAUDE.md): admit the unsatisfiable-check hazard, split its group

## v0.110.6 — 2026-09-17
- fix(feature): ask whether each plan check can pass at all

## v0.110.5 — 2026-09-17
- docs(CLAUDE.md): admit the inherited-setting hazard and split its group

## v0.110.4 — 2026-09-16
- test(audit): add the hermetic-outside subtree exemption campaign

## v0.110.3 — 2026-09-16
- fix(audit): exempt the skill-sync subtree from hermetic-outside

## v0.110.2 — 2026-09-16
- docs(feature): build re-derivation bricks with publish-brick.sh --dev

## v0.110.1 — 2026-09-16
- test(publish-brick): add the dev mode mutation campaign

## v0.110.0 — 2026-09-16
- feat(publish-brick): build re-derivation bricks onto dev with --dev

## v0.109.3 — 2026-09-16
- docs(CLAUDE.md): admit the performed-versus-carried matcher hazard

## v0.109.2 — 2026-09-16
- docs(CLAUDE.md): drop the stale SDD ledger identity section

## v0.109.1 — 2026-09-16
- test(hook-machinery-test): add hook and selection mutation campaigns

## v0.109.0 — 2026-09-16
- feat(hook-machinery-test): gate edits to the test-runner hooks

## v0.108.23 — 2026-09-15
- docs(CLAUDE.md): a subagent never hears its background waiter

## v0.108.22 — 2026-09-15
- fix(propagate): carry lowered hook timeouts named by the checker

## v0.108.21 — 2026-09-15
- fix(settings-hooks-check): fail a lowered runtime hook timeout

## v0.108.20 — 2026-09-15
- fix(audit-test): bound the audit suite via a shared hook_budget lib

## v0.108.19 — 2026-09-14
- test(publication-push-guard-test): add the hook mutation campaign

## v0.108.18 — 2026-09-14
- fix(publication-push-guard-test): run every tokenizer gate's suites

## v0.108.17 — 2026-09-14
- refactor(settings_hooks): expose each hook entry and its timeout

## v0.108.16 — 2026-09-14
- test(git_command): add the reserved-word argument mutation campaign

## v0.108.15 — 2026-09-14
- test(recast-commit-gate): pin reserved words in commit argv

## v0.108.14 — 2026-09-14
- fix(git_command): stop a reserved word cutting a git argument list

## v0.108.13 — 2026-09-14
- docs(CLAUDE.md): add the staged-change text hazard

## v0.108.12 — 2026-09-14
- test(git_command): add the reserved-word cd mutation campaign

## v0.108.11 — 2026-09-14
- test(git-timing-guard): pin the accepted reserved-word cd trade

## v0.108.10 — 2026-09-14
- fix(git_command): make a cd after a reserved word unresolvable

## v0.108.9 — 2026-09-11
- docs(CLAUDE.md): background run-long's --wait so it is tracked

## v0.108.8 — 2026-09-11
- test(git_command): pin the eval fix by mutation, each row by name

## v0.108.7 — 2026-09-11
- fix(git_command): see git behind eval and fail closed on wrapped cd

## v0.108.6 — 2026-09-11
- test(publication-push-guard): pin the env axis by mutation

## v0.108.5 — 2026-09-11
- fix(publication-push-guard): allowlist env names that reach git

## v0.108.4 — 2026-09-08
- fix(publication-push-guard): close eight config bypass spellings

## v0.108.3 — 2026-09-08
- test(publication-push-guard): pin allowed writes and open bypasses

## v0.108.2 — 2026-09-08
- docs(CLAUDE.md): split the check-building group at its seam

## v0.108.1 — 2026-09-08
- docs(CLAUDE.md): send waiters to run-long's own --wait mode

## v0.108.0 — 2026-09-08
- feat(backlog): add a retier operation with a compare-and-swap

## v0.107.2 — 2026-09-08
- fix(backlog): make save() atomic and mode-preserving with a snapshot

## v0.107.1 — 2026-09-08
- test(backlog): add a campaign and cover the bold-delimiter guard

## v0.107.0 — 2026-09-08
- feat(settings-hooks): share the walker and assert test-runner parity

## v0.106.3 — 2026-09-08
- fix(recast-test): close both fail-opens and add the missing guard rows

## v0.106.2 — 2026-09-08
- docs(CLAUDE.md): add the failures-cluster-in-one-parameter hazard

## v0.106.1 — 2026-09-04
- fix(propagate): drop the false quoting warning from the marker block

## v0.106.0 — 2026-09-04
- feat(backlog): add amend-head and enforce a dated head contract

## v0.105.5 — 2026-09-04
- docs(CLAUDE.md): split the input repairs from the logic repairs

## v0.105.4 — 2026-09-04
- test(git_command): pin the boundary-relocation fail-open class

## v0.105.3 — 2026-09-02
- docs(CLAUDE.md): split the edit hazards from the tool-writes group

## v0.105.2 — 2026-09-02
- docs(tests): correct the guard rationale in two orphaned files

## v0.105.1 — 2026-09-02
- fix(push-guard): treat reserved words as command position

## v0.105.0 — 2026-09-02
- feat(audit): re-point the observer and register the python hook

## v0.104.0 — 2026-09-02
- feat(timing-guard): decide by command position, not by regex

## v0.103.2 — 2026-09-02
- docs(delegating): forbid executing a command that could publish

## v0.103.1 — 2026-09-01
- fix(timing-guard): refuse argv and a terminal stdin

## v0.103.0 — 2026-09-01
- feat(audit): add the timing-guard-conf check

## v0.102.1 — 2026-09-01
- test(timing-guard): pin fail-open for a broken policy config

## v0.102.0 — 2026-09-01
- feat(timing-guard): point the registration at the tracked copy

## v0.101.3 — 2026-09-01
- docs(CLAUDE.md): add the absent-readings comparison hazard

## v0.101.2 — 2026-08-31
- docs(CLAUDE.md): give the allowlist rule its own failure mode

## v0.101.1 — 2026-08-31
- fix(publish): log the engine's partial output on a timeout

## v0.101.0 — 2026-08-31
- feat(publish): relay the engine's failure block at a halt

## v0.100.1 — 2026-08-31
- docs(CLAUDE.md): add the semantic-vs-structural assertion hazard

## v0.100.0 — 2026-08-31
- feat(memory-index): fail when the index outgrows its reader's limit

## v0.99.1 — 2026-08-31
- fix(audit): keep the full offender list when the excerpt truncates

## v0.99.0 — 2026-08-30
- feat(audit): check every script header is one sync-docs can read whole

## v0.98.8 — 2026-08-30
- docs(CLAUDE.md): add the recall-driven blast-radius hazard

## v0.98.7 — 2026-08-30
- docs(CLAUDE.md): add the same-bytes and crashed-child hazards

## v0.98.6 — 2026-08-28
- fix(markdownlint): herestring the config suite comparators

## v0.98.5 — 2026-08-28
- fix(tests): compare with a herestring, not a pipe into grep -q

## v0.98.4 — 2026-08-27
- docs(CLAUDE.md): add the false-measurement and enumeration hazards

## v0.98.3 — 2026-08-27
- fix(exec-bit-guard): reach large commands without forking per line

## v0.98.2 — 2026-08-26
- fix(propagate-postcheck): stop the range row inverting its own verdict

## v0.98.1 — 2026-08-26
- fix(audit): tell a historical checkout from a stale hook install

## v0.98.0 — 2026-08-26
- feat(timing-guard): track the guard and its suite in the repo

## v0.97.1 — 2026-08-25
- fix(feature): give each merge precondition its own remedy

## v0.97.0 — 2026-08-25
- feat(mutate): record timeout headroom in the campaign report

## v0.96.0 — 2026-08-24
- feat(debrief): resume an interrupted run from a per-run step ledger

## v0.95.0 — 2026-08-24
- feat(propagate): refresh the config farm on promote and assert it

## v0.94.0 — 2026-08-24
- feat(propagate): show the clock before both irreversible pushes

## v0.93.0 — 2026-08-24
- feat(explain-git-command): show where the tokenizer gave up

## v0.92.0 — 2026-08-24
- feat(guards): locate the construct that stopped the tokenizer

## v0.91.8 — 2026-08-24
- fix(audit): report only failing ruff invocations, bounded per file

## v0.91.7 — 2026-08-23
- test(mutate-run-long): add a row for classify's observation order

## v0.91.6 — 2026-08-23
- fix(run-long): sample liveness before reading the status trailer

## v0.91.5 — 2026-08-23
- docs(CLAUDE.md): add the reader-limit and premise-vs-remedy hazards

## v0.91.4 — 2026-08-22
- fix(debrief): bound step 0's backlog read and cross-check the count

## v0.91.3 — 2026-08-21
- docs(CLAUDE.md): add the loud-vs-silent hazard and split its group

## v0.91.2 — 2026-08-21
- fix(publish): comment the fold plan's per-commit diagnostics

## v0.91.1 — 2026-08-21
- fix(publish): express deletions when materialising a brick

## v0.91.0 — 2026-08-21
- feat(publish): add plan-time per-brick rehearsal

## v0.90.2 — 2026-08-21
- docs(CLAUDE.md): add the wrong-location and no-op-early-return hazards

## v0.90.1 — 2026-08-21
- docs(CLAUDE.md): add the stored-status hazard and split its group

## v0.90.0 — 2026-08-21
- feat(agents)!: remove the security-reviewer agent

## v0.89.0 — 2026-08-21
- feat(feature)!: remove the security-review gate from the pipeline

## v0.88.8 — 2026-08-20
- fix(feature): split a finding's verdict from its suggested repair

## v0.88.7 — 2026-08-20
- docs(CLAUDE.md): add the weaker-predicate hazard and split its group

## v0.88.6 — 2026-08-20
- docs(install): describe derived membership and correct settings.json

## v0.88.5 — 2026-08-20
- fix(install): derive farm membership and refuse self-install

## v0.88.4 — 2026-08-20
- docs(CLAUDE.md): add the pipefail SIGPIPE and default-value test hazards

## v0.88.3 — 2026-08-20
- docs(CLAUDE.md): add the working-directory config-discovery hazard

## v0.88.2 — 2026-08-14
- docs(CLAUDE.md): say a whole-sequence check does not validate the parts

## v0.88.1 — 2026-08-12
- docs(CLAUDE.md): add the lossy-converter extraction hazard

## v0.88.0 — 2026-08-10
- feat(delegating): add DELEGATING.md and wire it into the dispatch path

## v0.87.2 — 2026-08-10
- docs(CLAUDE.md): add the stale-liveness-reading hazard

## v0.87.1 — 2026-08-10
- test(env-claims): add the mutation campaign

## v0.87.0 — 2026-08-10
- feat(env-claims): run the suite when the checker changes

## v0.86.0 — 2026-08-10
- feat(audit): add the scope-gated env-claims check

## v0.85.0 — 2026-08-10
- feat(env-claims): verify CLAUDE.md's documented environment claims

## v0.84.3 — 2026-08-09
- docs(CLAUDE.md): split the probe bullet and add the harness-shim vector

## v0.84.2 — 2026-08-09
- test(mutate-run-long): add rows for the expect verdict

## v0.84.1 — 2026-08-09
- fix(run-long): correct the matcher note measured through a shim

## v0.84.0 — 2026-08-09
- feat(run-long): report INDETERMINATE when a run reached no verdict

## v0.83.5 — 2026-08-09
- docs(CLAUDE.md): add the one-sample-per-cell comparison hazard

## v0.83.4 — 2026-08-10
- docs(CLAUDE.md): call the unprefixed tools GNU, not GNU coreutils

## v0.83.3 — 2026-08-09
- docs(CLAUDE.md): correct the GNU-vs-BSD default this machine uses

## v0.83.2 — 2026-08-08
- fix(mutate): print a derived call signature instead of nothing

## v0.83.1 — 2026-08-08
- fix(test_mutate.py): park the campaign instead of racing exit flush

## v0.83.0 — 2026-08-07
- feat(memory-index-check): flag index entries grown into content

## v0.82.3 — 2026-08-07
- docs(CLAUDE.md): add the output-format rubber-stamp hazard

## v0.82.2 — 2026-08-07
- fix(feature): trace security-gate rounds and bound the finish

## v0.82.1 — 2026-08-07
- docs(CLAUDE.md): add the widened-matcher and indirection hazards

## v0.82.0 — 2026-08-07
- feat(fixture-signing): flag fixture repos that inherit signing config

## v0.81.13 — 2026-08-07
- test(recast_helpers.py): bound git calls so a hang fails loudly

## v0.81.12 — 2026-08-07
- test: disable both git signing keys in every fixture repo

## v0.81.11 — 2026-08-07
- docs(CLAUDE.md): add the widened-verdict hazard and split its group

## v0.81.10 — 2026-08-07
- docs(CLAUDE.md): add the shared-harness corroboration hazard

## v0.81.9 — 2026-08-07
- docs(audit): mark a plain sweep PASS as no evidence the suite ran

## v0.81.8 — 2026-08-07
- fix(audit): fail hermetic-outside when it could not measure

## v0.81.7 — 2026-08-07
- fix(audit): fail when an applicable test family could not run

## v0.81.6 — 2026-08-06
- docs(CLAUDE.md): add the default-mode scope hazard

## v0.81.5 — 2026-08-06
- fix(test_publish_fold_plan.py): disable tag signing in repo fixtures

## v0.81.4 — 2026-08-09
- docs(CLAUDE.md): point run-long users at --expect

## v0.81.3 — 2026-08-06
- docs(CLAUDE.md): name run-long.sh alongside mutate.py

## v0.81.2 — 2026-08-06
- docs(CLAUDE.md): add the deploy-half ordering hazard

## v0.81.1 — 2026-08-06
- test(propagate-postcheck): anchor the boundary-hook check

## v0.81.0 — 2026-08-06
- feat(propagate-postcheck): assert the boundary hook is installed

## v0.80.0 — 2026-08-06
- feat(propagate): re-install the boundary hook after a promote

## v0.79.17 — 2026-08-06
- docs(CLAUDE.md): add the wrapper-overclaim and snapshot-restore hazards

## v0.79.16 — 2026-08-05
- docs(publication-push-guard): record the expected scope-set survivor

## v0.79.15 — 2026-08-05
- fix(publication-push-guard): name the right cause and remedy

## v0.79.14 — 2026-08-05
- docs(publication-push-guard): name the config-redirect env residual

## v0.79.13 — 2026-08-05
- fix(publication-push-guard): refuse an empty repo root, not only None

## v0.79.12 — 2026-08-05
- test(publication-push-guard): witness every reserved word, not eight

## v0.79.11 — 2026-08-05
- fix(publication-push-guard): reset command position on a reserved word

## v0.79.10 — 2026-08-05
- fix(publication-push-guard): give each boundary failure its own remedy

## v0.79.9 — 2026-08-05
- fix(publication-push-guard): deny env names that relocate hooks

## v0.79.8 — 2026-08-05
- docs(CLAUDE.md): widen the environment hazard and reorder the ask rule

## v0.79.7 — 2026-08-05
- fix(publication-push-guard): adopt the hook's refs-based adoption test

## v0.79.6 — 2026-08-05
- fix(publication-push-guard): refuse a boundary-hook bypass flag

## v0.79.5 — 2026-08-05
- docs(audit): name instrument failure as a second hermeticity FAIL cause

## v0.79.4 — 2026-08-04
- test(publication-push-guard): cover two branches a campaign found bare

## v0.79.3 — 2026-08-04
- docs(recast-commit-gate): name the unknown-global-option fail-open

## v0.79.2 — 2026-08-04
- fix(git_command): name the walk's record and derive the wrapper set

## v0.79.1 — 2026-08-04
- fix(publication-push-guard): scope config injection to what it protects

## v0.79.0 — 2026-08-04
- feat(publication-push-guard): add the config read and scope classifiers

## v0.78.0 — 2026-08-04
- feat(publication-push-guard): assert the boundary hook is in force

## v0.77.6 — 2026-08-05
- fix(publication-push-guard): catch an exported config injection

## v0.77.5 — 2026-08-04
- fix(publication-push-guard): drop the inert attached -c branch

## v0.77.4 — 2026-08-04
- fix(git_command,publication-push-guard): close two measured fail-opens

## v0.77.3 — 2026-08-03
- fix(git_command): stop the ambiguous value slot eating the boundary

## v0.77.2 — 2026-08-03
- fix(git_command): treat bash reserved words as command boundaries

## v0.77.1 — 2026-08-07
- fix(tests): disable git signing in the two fixtures that sign today

## v0.77.0 — 2026-08-06
- feat(install-git-hooks): license a stale-hook overwrite by blob history

## v0.76.1 — 2026-08-05
- docs(pre-push): scope the verb-splitting note to new rows

## v0.76.0 — 2026-08-05
- feat(pre-push): add the git-native publication boundary and its check

## v0.75.17 — 2026-08-04
- test(publication-push-guard): add a frozen-baseline regression corpus

## v0.75.16 — 2026-08-05
- docs(CLAUDE.md): make the ask-directly list survive Fable, not skip it

## v0.75.15 — 2026-08-05
- docs(CLAUDE.md): run /feature by default and let its triage right-size

## v0.75.14 — 2026-08-05
- docs(CLAUDE.md): record the standing grant to delegate freely

## v0.75.13 — 2026-08-05
- docs(CLAUDE.md): warn that a background run outlives its session

## v0.75.12 — 2026-08-04
- docs(CLAUDE.md): warn that a stronger consequence is no wider trigger

## v0.75.11 — 2026-08-04
- docs(CLAUDE.md): warn when a probe's rows share a masking condition

## v0.75.10 — 2026-08-04
- docs(CLAUDE.md): warn that a small-N fixture hides a threshold

## v0.75.9 — 2026-08-04
- fix(audit): stop the offender cap discarding test failures

## v0.75.8 — 2026-08-04
- docs(CLAUDE.md): say when a re-run clears a failed gate

## v0.75.7 — 2026-08-04
- docs(CLAUDE.md): add the silent-delegate hazard and widen the brief one

## v0.75.6 — 2026-08-03
- docs(CLAUDE.md): add the narrowing-drops-true-positives hazard

## v0.75.5 — 2026-08-03
- fix(publication-push-guard): describe what it actually judged

## v0.75.4 — 2026-08-03
- fix(publication-push-guard): allow read-only commands it cannot judge

## v0.75.3 — 2026-08-03
- fix(git_command): stop a stolen operator leaking a subshell cwd

## v0.75.2 — 2026-08-03
- fix(mutation-anchors): refuse to skip an untracked campaign

## v0.75.1 — 2026-08-03
- docs(CLAUDE.md): add the inert-code and adjacent-edit hazards

## v0.75.0 — 2026-08-03
- feat(sync-docs): refuse to render a repo carrying a different copy

## v0.74.1 — 2026-08-03
- fix(CLAUDE.md): repair the cross-reference the restore edit broke

## v0.74.0 — 2026-08-03
- feat(publish): drive the brick loop from a reviewed plan file

## v0.73.1 — 2026-08-03
- fix(CLAUDE.md): verify a restore by content, not string presence

## v0.73.0 — 2026-08-03
- feat(claude-md-structure): measure the hazards section mechanically

## v0.72.4 — 2026-08-03
- fix(mutate): stream each mutation as it resolves instead of buffering

## v0.72.3 — 2026-08-03
- fix(publish): drop folds a later brick would overwrite

## v0.72.2 — 2026-08-03
- docs(CLAUDE.md): add the composition hazard and split its group

## v0.72.1 — 2026-08-03
- docs(CLAUDE.md): add the version-control population hazard

## v0.72.0 — 2026-08-02
- feat(propagate): script the branching post-merge promote verification

## v0.71.2 — 2026-08-01
- docs(CLAUDE.md): add the blanket-fixer and truncated-output hazards

## v0.71.1 — 2026-08-01
- fix(hooks): refuse argv and a terminal stdin instead of reading nothing

## v0.71.0 — 2026-08-01
- feat(mutation-anchors): require every anchor to resolve exactly once

## v0.70.3 — 2026-08-01
- docs(CLAUDE.md): split the teardown hazards, add the never-ends case

## v0.70.2 — 2026-07-31
- test(run-long): give the slow campaign an explicit suite timeout

## v0.70.1 — 2026-07-31
- docs(CLAUDE.md): add the killed-teardown and ignored-argument hazards

## v0.70.0 — 2026-07-31
- feat(propagate): check a promote kept every committed hook registration

## v0.69.1 — 2026-07-31
- fix(markdownlint): enforce blanks above headings, not below

## v0.69.0 — 2026-07-31
- feat(run-long): add --wait and stamp the tree each verdict graded

## v0.68.7 — 2026-07-31
- docs(CLAUDE.md): add the stale-subject hazard, sever positional refs

## v0.68.6 — 2026-07-31
- fix(debrief): stop stalling at delegate returns and losing deferrals

## v0.68.5 — 2026-07-30
- docs(CLAUDE.md): add the tool-scope hazard and sharpen a matcher bullet

## v0.68.4 — 2026-07-30
- fix(CLAUDE.md): restore the blank line above a split-out group heading

## v0.68.3 — 2026-07-30
- docs(CLAUDE.md): sever the counts hazard from the derive-FLOOR bullet

## v0.68.2 — 2026-07-30
- docs(CLAUDE.md): make the hazard cap a split trigger, not a rejection

## v0.68.1 — 2026-07-30
- docs(CLAUDE.md): note that a restore covers only what it snapshotted

## v0.68.0 — 2026-07-30
- feat(prose-diff): verify a restructuring is lossless in both directions

## v0.67.1 — 2026-07-30
- docs(debrief): correct the claim that the backlog is repo-external

## v0.67.0 — 2026-07-30
- feat(run-long): record a backgrounded job's exit status in its artifact

## v0.66.2 — 2026-07-30
- docs(CLAUDE.md): note that a reachability excuse is one grep away

## v0.66.1 — 2026-07-29
- docs(mutate): surface the shared runner at the point of use

## v0.66.0 — 2026-07-31
- feat(mutate): extract the shared mutation-campaign runner

## v0.65.3 — 2026-07-29
- docs(CLAUDE.md): note that a RED can fire for the wrong reason

## v0.65.2 — 2026-07-29
- test(audit): assert the check list against every prose restatement

## v0.65.1 — 2026-07-29
- docs(CLAUDE.md): generalize two hazard clauses beyond their first case

## v0.65.0 — 2026-07-29
- feat(audit): check what a --tests run leaves behind, inside and out

## v0.64.2 — 2026-07-29
- docs(CLAUDE.md): split the hazards into a group for checks that write

## v0.64.1 — 2026-07-29
- docs(publish-preflight): restate what stays foreground and unscripted

## v0.64.0 — 2026-07-29
- feat(propagate): route the publish path through the brick engine

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

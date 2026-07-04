---
name: debrief
description: Run the end-of-session pre-compaction routine (CLAUDE.md refresh, memory save, automation review)
disable-model-invocation: true
---

# /debrief — End-of-Session Pre-Compaction Routine

Walk through the end-of-session ritual before compacting: refresh CLAUDE.md from the
session, save anything durable to memory, review and implement automation recommendations,
commit the result, and hand off the manual compaction steps. Invoked deliberately by the
user near the end of a working session.

## Instructions

The user is about to compact the conversation and wants to capture everything worth keeping
first. Orchestrate the routine below: invoke each sub-skill in order and surface its output.
Most steps now apply automatically — the CLAUDE.md refresh and audit (steps 1–2) and the
memory save (step 3) auto-apply, and the automation pass (step 4) auto-accepts its top picks.
The routine pauses only where judgment is still required: a CLAUDE.md edit that trips the
sensitivity carve-out (below), a medium-tier automation, and the implementation plan in step 5.

Seed a TodoWrite list with one item per step (1–7) so progress is visible and resumable.

This skill stops at the hand-off. It CANNOT run `/compact`, exit Claude, or restart it —
those remain manual steps for the user.

### Process

1. **Refresh CLAUDE.md from the session.** Invoke `claude-md-management:revise-claude-md`.
   **Auto-apply** its proposed CLAUDE.md edits, then show the resulting diff so the change
   stays visible. **Sensitivity carve-out:** do not auto-write content the repo keeps out of
   tracked public files (operational-security notes — see private memory); surface any such
   edit for explicit confirmation and prefer routing it to private memory or `.claude.local.md`.

2. **Audit CLAUDE.md (length-gated).** Judge the session's size: treat it as substantial if
   it covered several distinct tasks or topics, or ran long. State the judgment and the
   reason. If substantial, invoke `claude-md-management:claude-md-improver` and **auto-apply
   its recommended improvements**, showing the diff. If the session was short, say so and move
   on. When it is genuinely borderline, say so and lean toward running the audit. Step 1's
   sensitivity carve-out applies here too — surface, don't auto-write, anything that belongs
   out of the public file.

3. **Memory / file save check.** Review the session for durable facts worth persisting —
   user traits, feedback on how to work, project context, or reference pointers — plus
   anything that belongs in a repo file. **Auto-write** the memory entries (each with its
   one-line `MEMORY.md` pointer) and report what was saved; before creating a memory file,
   check for an existing one that already covers the fact and update it instead. Memory lives
   in private storage outside the repo, so no carve-out applies; but for any write to a
   *tracked repo file*, apply step 1's sensitivity carve-out.

4. **Automation recommendations.** Invoke
   `claude-code-setup:claude-automation-recommender`. Triage its recommendations by the
   priority it assigns: **auto-accept at most the top 2 high-priority picks** (if more than 2
   are high-priority, pause for the user's decision on the rest), **auto-decline low-priority**
   ones (noting what was dropped), and **pause for the user's decision on any medium-tier**
   recommendation, regardless of count. If nothing is accepted, skip to step 6.

5. **Design and implement the accepted automations via the `/feature` pipeline** (only when one
   or more were accepted in step 4):
   1. For the accepted automations — as one cohesive set only when they share a mechanism or
      touch the same files, otherwise each on its own — **follow** the
      `/feature` design pipeline documented in `skills/feature/SKILL.md`: Step 0 risk triage, then
      the chosen lane (brainstorming → spec/plan, an ultrathink-level self-review, the optional
      empirical spike, and the budget-gated diverse-model review) to produce a reviewed plan.
      Follow `/feature`'s documented steps directly rather than firing it via the Skill tool: the
      routine continues past `/feature`'s stop-at-plan boundary (step 5.3 below), so it orchestrates
      the pipeline inline. The SKILL.md is the single source of truth.
   2. **Present the reviewed plan and pause** for the user's confirmation.
   3. On confirmation, **implement** the plan (`superpowers:subagent-driven-development`).
      This deliberately continues past `/feature`'s stop-at-plan boundary — a standalone
      `/feature` run hands off here, but the debrief routine owns execution.
      After implementation, **finish with `superpowers:finishing-a-development-branch`** to merge
      the feature branch back to its base — matching `/feature`'s own default end action. If the
      merge is deferred, the step-7 hand-off must tell the user the repo is still on an unmerged
      feature branch.

6. **Commit what the routine changed.** If the working tree has tracked changes from this
   routine (accepted CLAUDE.md edits, any repo files written in step 3, implemented
   automations), invoke `/commit` to commit them. The commit skill is granular by default,
   so it splits unrelated changes into separate commits and tags. If there are no tracked
   changes, say so and continue.

7. **Hand-off.** Tell the user the routine is complete and print the three manual steps the
   skill cannot perform:
   1. Run `/compact`.
   2. Exit Claude.
   3. Restart Claude to reload configuration.

### Rules

- Steps 1–3 auto-apply (CLAUDE.md refresh/audit, memory); step 4 auto-accepts the top picks
  and auto-declines low picks. Pause only for: a CLAUDE.md edit that trips the sensitivity
  carve-out, a medium-tier automation (step 4), and the implementation plan (step 5).
- **Sensitivity carve-out:** never auto-write into a tracked public file (CLAUDE.md or any
  other) content the repo keeps out of public history (operational-security notes — see
  private memory); surface it for confirmation and prefer private memory or `.claude.local.md`.
- After an auto-applied CLAUDE.md change (steps 1–2), show the diff so the result stays visible.
- Never run `/compact`, exit, or restart Claude — stop at the hand-off and let the user do
  those.
- Memory entries persist through Claude Code's memory store, which lives outside this repo;
  only repo files need committing in step 6.
- In step 5, follow `/feature`'s artifact convention (spec/plan under `specs/`/`plans/`); those
  files are governed by the repo's own tracking rules (often gitignored), so only the implemented
  changes need committing in step 6.
- In step 3, follow the memory protocol: one fact per file with frontmatter and a
  `MEMORY.md` pointer line; update an existing memory file rather than duplicating it.

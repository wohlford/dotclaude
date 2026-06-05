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
first. Orchestrate the routine below: invoke each sub-skill in order, surface its output,
then STOP at each gate and wait for the user's accept/reject before continuing. Apply only
what the user approves — the user routinely accepts some recommendations and declines
others.

Seed a TodoWrite list with one item per step (1–7) so progress is visible and resumable.

This skill stops at the hand-off. It CANNOT run `/compact`, exit Claude, or restart it —
those remain manual steps for the user.

### Process

1. **Refresh CLAUDE.md from the session.** Invoke `claude-md-management:revise-claude-md`.
   Present its proposed CLAUDE.md edits, then pause. Apply only the edits the user accepts.

2. **Audit CLAUDE.md (length-gated).** Judge the session's size: treat it as substantial if
   it covered several distinct tasks or topics, or ran long. State the judgment and the
   reason. If substantial, invoke `claude-md-management:claude-md-improver`, present its
   recommendations, and pause for the user to pick which to apply; apply those. If the
   session was short, say so and move on. When it is genuinely borderline, say so and lean
   toward running the audit.

3. **Memory / file save check.** Review the session for durable facts worth persisting —
   user traits, feedback on how to work, project context, or reference pointers — plus
   anything that belongs in a repo file. Propose specific memory entries (each with its
   one-line `MEMORY.md` pointer) and/or file writes, then pause. Before creating a memory
   file, check for an existing one that already covers the fact and update it instead.
   Write only what the user approves.

4. **Automation recommendations.** Invoke
   `claude-code-setup:claude-automation-recommender`. Present its recommendations and let
   the user select which to accept. If the user accepts none, skip to step 6.

5. **Plan, review, and implement the accepted automations** (only when one or more were
   accepted in step 4):
   1. Write a plan covering the accepted recommendations to a temporary, untracked Markdown
      file (e.g. `"$TMPDIR/debrief-automation-plan.md"`).
   2. Re-read the plan critically with maximum reasoning depth — the equivalent of an
      "ultrathink" pass: challenge each item, look for missing steps, wrong ordering, and
      risks — and revise the plan file in place.
   3. Implement the revised plan.

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

- Pause for the user's decision at every gate (steps 1–4). Never auto-apply a sub-skill's
  recommendations.
- Never run `/compact`, exit, or restart Claude — stop at the hand-off and let the user do
  those.
- Memory entries persist through Claude Code's memory store, which lives outside this repo;
  only repo files need committing in step 6.
- In step 5, keep the plan file out of version control (a temporary location); only the
  implemented changes get committed.
- In step 3, follow the memory protocol: one fact per file with frontmatter and a
  `MEMORY.md` pointer line; update an existing memory file rather than duplicating it.

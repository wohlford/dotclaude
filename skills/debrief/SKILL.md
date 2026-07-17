---
name: debrief
description: Run the end-of-session pre-compaction routine (deferral follow-up, CLAUDE.md refresh, memory save, automation review, and deferred design)
disable-model-invocation: true
---

# /debrief — End-of-Session Pre-Compaction Routine

Walk through the end-of-session ritual before compacting: follow up on what a past debrief
deferred, refresh CLAUDE.md from the session, save anything durable to memory, defer the
session's automation recommendations to the hand-off, commit the result, and hand off the
manual compaction steps. **User-invoked only — deciding when a session has ended is the user's call,
not Claude's** — so it is run deliberately, near the end of one.

## Instructions

The user is about to compact the conversation and wants to capture everything worth keeping
first. Orchestrate the routine below: invoke each sub-skill in order and surface its output.
Steps 1–4 apply automatically — the CLAUDE.md refresh and audit (steps 1–2) and the memory
save (step 3) auto-apply, and the automation pass (step 4) triages its recommendations and defers
them to the hand-off. **A plain `/debrief` runs to completion without pausing:** where a step would
once have asked, it takes the safe default and records the decision for the step-7 hand-off instead
— open deferrals default to **keep** (step 0), automation recommendations **defer** (step 4), and a
CLAUDE.md edit that trips the sensitivity carve-out routes to private memory rather than surfacing
(below). The one exception is step 5 (design an automation), which runs *only* when the user asks
for it at invocation and then inherits `/feature --plan-only`'s confirmation pause — a plain
`/debrief` never reaches it. So the user can start a plain `/debrief` and walk away to a
compact-ready session.

**The debrief designs; it never builds.** It is a wind-down, so it stops at a decision or a
reviewed plan and records the rest for later. Implementing here would burn the context the user
is about to compact, and a plan deserves a session with room to execute it.

Seed a TodoWrite list with one item per step (0–7) so progress is visible and resumable.

This skill stops at the hand-off. It CANNOT run `/compact`, exit Claude, or restart it —
those remain manual steps for the user.

### Process

0. **Follow up on open deferrals.** Read `BACKLOG.md` in this session's memory directory (skip
   silently if it doesn't exist) and report every open (`- [ ]`) entry with its age. Choose
   a disposition for each **without pausing** — default to **keep**, and depart from it only on
   positive evidence from *this* session (drop when something demonstrably overtook the entry,
   promote when the session made it the clear next job). State each entry's disposition and why in
   the step-7 hand-off. Each disposition writes something different back to `BACKLOG.md`:
   - **keep** — still wanted, just not now. Leave the entry untouched.
   - **drop** — overtaken by events. Tick it to `- [x]`, append what overtook it, and move it
     under `## Closed`.
   - **promote** — worth doing next session. Leave it open, but stamp the line
     (`promoted <YYYY-MM-DD>`) so a later debrief can see it was already called up and flag the
     stall in its hand-off, rather than re-reading it as freshly deferred.

   Never implement a promoted item here; name it in the step-7 hand-off as the next session's
   first job.

1. **Refresh CLAUDE.md from the session.** Invoke `claude-md-management:revise-claude-md`.
   **Auto-apply** its proposed CLAUDE.md edits, then show the resulting diff so the change
   stays visible. **Sensitivity carve-out:** do not auto-write content the repo keeps out of
   tracked public files (operational-security notes — see private memory); **route any such
   content to private memory or `.claude.local.md` instead of surfacing it** — the safe route was
   always preferred, so taking it automatically drops the pause without weakening the guarantee.
   Note the routing in the hand-off.

2. **Audit CLAUDE.md (length-gated).** Judge the session's size: treat it as substantial if
   it covered several distinct tasks or topics, or ran long. State the judgment and the
   reason. If substantial, invoke `claude-md-management:claude-md-improver` and **auto-apply
   its recommended improvements**, showing the diff. If the session was short, say so and move
   on. When it is genuinely borderline, say so and lean toward running the audit. Step 1's
   sensitivity carve-out applies here too — route anything that belongs out of the public file to
   private memory instead of auto-writing it.

3. **Memory / file save check.** Review the session for durable facts worth persisting —
   user traits, feedback on how to work, project context, or reference pointers — plus
   anything that belongs in a repo file. **Auto-write** the memory entries (each with its
   one-line `MEMORY.md` pointer) and report what was saved; before creating a memory file,
   check for an existing one that already covers the fact and update it instead. Memory lives
   in private storage outside the repo, so no carve-out applies; but for any write to a
   *tracked repo file*, apply step 1's sensitivity carve-out.

4. **Automation recommendations.** Invoke
   `claude-code-setup:claude-automation-recommender`. The recommender groups its output by
   category and assigns no priority tiers — assign each recommendation a tier yourself and
   state it: **high** = clear, recurring value in this repo's actual workflow; **low** =
   speculative or one-off; **medium** = everything between. Then triage by those tiers:
   **auto-decline low-priority** ones (noting what was dropped) and **defer every surviving
   medium- and high-tier recommendation to the hand-off** for the user to pick up next session.
   The debrief does not accept or design an automation unattended — that path (step 5's
   `/feature --plan-only`) ends at an approval pause and defers to the backlog regardless, so an
   unattended run reports the recommendations tiered rather than acting on them. If there is
   nothing worth reporting, say so and skip to step 6.

5. **Design the automation(s) the user directed, then defer them** (only when the user asked *at
   invocation* for a specific automation to be designed — e.g. `/debrief, and design the caching
   hook`; a plain `/debrief` defers every recommendation in step 4 and accepts none, so this step
   is normally skipped. When it does run it dispatches `/feature --plan-only`, which pauses for the
   user's confirmation — so a run that reaches step 5 is a deliberate design session, not a
   walk-away run):
   1. For each such automation — as one cohesive set only when they share a mechanism or
      touch the same files, otherwise each on its own — run **`/feature --plan-only`**, which
      pauses for the user's confirmation and ends at the reviewed plan. (Whether that plan lands as
      a commit depends on the repo — step 5.4 owns that.)
   2. **Defer every plan; never implement one here.** Do not ask the user whether to implement —
      the answer is always "not in the debrief". Record the deferral (5.3) and move on.
   3. **Record the deferral in the private backlog.** Deferrals live in this session's memory
      directory, outside every repo: private by construction, and durable through the
      `git clean -fdx` that would wipe a gitignored `plans/`. Write both halves:
      - **The design**, as its own memory file (`type: project`, one deferral per file)
        following the step-3 memory protocol. It **must be self-contained** — enough to
        re-derive the plan from scratch, including the rationale and any defect a review
        caught. The plan file itself is *not* durable (`plans/` is commonly gitignored), so
        name its path as a convenience but never let the entry be a bare pointer to it.
      - **The index entry**, appended under `## Open` in `BACKLOG.md` in that same directory.
        Create the file if absent — same frontmatter shape as any memory file, plus its
        `MEMORY.md` pointer — but note it is an *index*, one line per deferral, not a one-fact
        memory. Each line is what step 0 reads back:

        ```text
        - [ ] <YYYY-MM-DD> — <one line: what it is, and why it's worth doing> — [[<memory-slug>]]
        ```

        Record why the work *matters*, not why the debrief didn't build it — that reason is
        always the same and carries no signal.

   4. **Return to the base branch, always.** `/feature --plan-only` creates a feature branch and
      leaves it checked out. **Check the base branch back out before step 6** — otherwise step 6
      commits the routine's own CLAUDE.md edits and step-3 repo files onto an abandoned feature
      branch, where they are invisible to `/propagate` and to the next session. Then, by what the
      branch holds:
      - **Zero commits** — the normal case when `plans/` is gitignored, so the plan was never
        committable. Delete the branch (after checking out the base; you cannot delete the branch
        you are standing on). An empty branch is litter, not state.
      - **The plan commit landed** — keep the branch and name it in the hand-off, so the user
        knows where the plan lives and that it is unmerged.

6. **Commit what the routine changed.** If the working tree has tracked changes from this
   routine (accepted CLAUDE.md edits, any repo files written in step 3), invoke `/commit` to
   commit them. The commit skill is granular by default, so it splits unrelated changes into
   separate commits and tags. If there are no tracked changes, say so and continue.

7. **Hand-off.** Tell the user the routine is complete. Because a plain `/debrief` no longer
   pauses, the hand-off is where the user learns every decision it made unattended — report all of it:
   - the step-0 disposition of **each** open deferral (keep / drop / promote), so an autonomous
     drop or promote is visible and reversible; a **promoted** item is the next session's first job
   - every automation recommendation **deferred** in step 4 (each surviving high- or medium-tier
     pick), tiered, for the user to pick up next session
   - any content the sensitivity carve-out **routed to private memory** instead of a public file
   - a plan **deferred** in step 5, and where its backlog entry lives
   - a feature branch left in place, if step 5.4 kept one

   Then print the three manual steps the skill cannot perform:
   1. Run `/compact`.
   2. Exit Claude.
   3. Restart Claude to reload configuration.

### Rules

- **Design, never build.** Step 0 stops at a disposition and step 5 stops at a reviewed plan.
  Neither implements, and neither asks the user whether to — a promoted or deferred item is
  named in the hand-off and executed in a later session.
- **A plain `/debrief` never pauses.** Steps 1–3 auto-apply (CLAUDE.md refresh/audit, memory);
  step 4 auto-declines low picks and defers every surviving recommendation to the hand-off. Every
  point that once asked now takes a safe default and reports it in the step-7 hand-off: the
  deferral triage defaults to **keep** (step 0), automation recommendations **defer** (step 4), and
  carve-out content **routes to private memory** (below). The one exception is step 5, which runs
  only when the user asks for an automation to be designed at invocation and then inherits
  `/feature --plan-only`'s confirmation pause; a plain `/debrief` never reaches it.
- **Sensitivity carve-out:** never auto-write into a tracked public file (CLAUDE.md or any
  other) content the repo keeps out of public history (operational-security notes — see
  private memory); **route it to private memory or `.claude.local.md`** — silently, never
  surfacing it for confirmation. The guarantee (nothing op-sec reaches a public file) is
  unchanged; only the pause is gone.
- After an auto-applied CLAUDE.md change (steps 1–2), show the diff so the result stays visible.
- Never run `/compact`, exit, or restart Claude — stop at the hand-off and let the user do
  those.
- **The backlog is private and repo-external.** `BACKLOG.md` and the per-deferral memory files
  live in the session's memory directory — never in a repo, which keeps them out of public
  history and off the propagate path. Only repo files need committing in step 6.
- In step 3, follow the memory protocol: one fact per file with frontmatter and a
  `MEMORY.md` pointer line; update an existing memory file rather than duplicating it.
- In step 5, `/feature` owns its own artifact convention (spec/plan under `specs/`/`plans/`) and
  its own commit discipline; the debrief does not restate them.

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
save (step 3) auto-apply, and the automation pass (step 4) triages its recommendations, files the
survivors in `BACKLOG.md`, and defers them to the hand-off. **A plain `/debrief` runs to completion
without pausing:** where a step would once have asked, it takes the safe default and records the
decision for the step-7 hand-off instead
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

   **Make every one of those write-backs with `backlog.py`, never a hand-written script** — see
   **Editing BACKLOG.md** below. Five sessions in a row hand-rolled one, and one of them corrupted
   the file.

   Never implement a promoted item here; name it in the step-7 hand-off as the next session's
   first job.

1. **Refresh CLAUDE.md from the session.** Invoke `claude-md-management:revise-claude-md`.
   **Auto-apply** its proposed CLAUDE.md edits, then show the resulting diff so the change
   stays visible.

   **Get the structure numbers from `scripts/claude-md-structure.py`, never by hand** — group
   sizes, member counts, the longest member and the longest heading, which are what the admission
   rules are stated in. Steps 1 and 2 both need them, and they were hand-derived in four
   consecutive runs; **two of those hand-rolls returned different wrong answers**, neither visible
   in its own output. One over-reported bullet length by letting a group's last member swallow the
   `####` below it (12 against a true 10); the other over-reported group count by scanning past the
   section into Package Management (13/35 against a true 10/33). Both read in the direction that
   MANUFACTURES work — a healthy bullet "fixed", or healthy groups declared under-populated. Pass
   `--file <repo>/CLAUDE.md`; it prints the sizes in the `3/4/3/4/…` form the cap is written in,
   and it measures only — no exit code depends on whether a group is over the cap. **Sensitivity carve-out:** do not auto-write content the repo keeps out of
   tracked public files (operational-security notes — see private memory); **route any such
   content to private memory or `.claude.local.md` instead of surfacing it** — the safe route was
   always preferred, so taking it automatically drops the pause without weakening the guarantee.
   Note the routing in the hand-off. **End this invocation's arguments with the return
   instruction** (see **Overriding a delegate's closing pause** below) — this delegate's own
   workflow ends at "Apply with Approval", and the next step here is 2.

2. **Audit CLAUDE.md (length-gated).** Judge the session's size: treat it as substantial if
   it covered several distinct tasks or topics, or ran long. State the judgment and the
   reason. If substantial, invoke `claude-md-management:claude-md-improver` and **auto-apply
   its recommended improvements**, showing the diff. If the session was short, say so and move
   on. When it is genuinely borderline, say so and lean toward running the audit. Step 1's
   sensitivity carve-out applies here too — route anything that belongs out of the public file to
   private memory instead of auto-writing it. **End this invocation's arguments with the return
   instruction** (see **Overriding a delegate's closing pause** below) — this delegate's own
   Phase 4 ends at "ask user for confirmation", and the next step here is 3. Its returned quality
   report is not a stopping point.

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
   nothing worth reporting, say so and skip to step 6 (unless step 5's own trigger — a
   user-requested automation design at invocation — is set, in which case proceed to step 5).
   **End this invocation's arguments with the return instruction** (see **Overriding a delegate's
   closing pause** below) — this delegate's report template closes by offering to implement, and
   the next step here is 6 (or 5 when its trigger is set).

   **Then file each surviving pick in `BACKLOG.md` before moving on.** A hand-off is prose, and
   `/compact` is the next thing the user runs, so a pick deferred only to the hand-off is
   *discarded*, not deferred — the routine's own promise that the user can "pick it up next
   session" is one the storage cannot keep. Measured: the 2026-07-27 run deferred one **high** and
   two mediums; a grep of the backlog the next day found no trace of any of them, and they were
   recovered only because that session's summary happened to survive in context. Append one entry
   per surviving pick with `backlog.py … add` (see **Editing BACKLOG.md**) — shaped like step
   5.3's index line but with no `[[slug]]`, since there is no memory file behind it and the entry
   itself is the record:

   ```text
   - [ ] <YYYY-MM-DD> — <HIGH|MEDIUM> — <the recommendation, and why it matters>
   ```

   Make the head specific enough to be a unique needle for a later step 0, which is what `add`
   enforces. This is bookkeeping, not designing, so it does not violate **the debrief designs; it
   never builds** — the pick is still deferred, merely to storage that outlives the compaction.

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
      directory under `~/.claude/projects/` — untracked rather than repo-external, so they are
      private by construction and survive the `git clean -fdx` that would wipe a gitignored
      `plans/` **in the working repo**. Run that same command in `~/.claude` itself and it
      deletes every deferral, the backlog, and every session transcript: `-x` takes ignored
      paths, and nothing there is tracked, so no history can restore it. Write both halves:
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
        always the same and carries no signal. Append it with `backlog.py … add` (see **Editing
        BACKLOG.md**), which places it under `## Open` and refuses a head that would not be
        uniquely addressable by a later step 0.

   4. **Return to the base branch, always** (the branch checked out before `/feature --plan-only`
      created the feature branch). `/feature --plan-only` creates a feature branch and
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
     pick), tiered — naming these here is a courtesy summary, not the record; step 4 already filed
     each one in `BACKLOG.md`, which is what survives the `/compact` that follows this hand-off
   - any content the sensitivity carve-out **routed to private memory** instead of a public file
   - a plan **deferred** in step 5, and where its backlog entry lives
   - a feature branch left in place, if step 5.4 kept one

   Then print the three manual steps the skill cannot perform:
   1. Run `/compact`.
   2. Exit Claude.
   3. Restart Claude to reload configuration.

### Overriding a delegate's closing pause

Steps 1, 2 and 4 delegate to plugin skills whose own written workflow **ends by asking the user**:
`revise-claude-md`'s Step 5 is "Apply with Approval", `claude-md-improver`'s Phase 4 is "ask user
for confirmation before updating", and the recommender's report template closes with "Want help
implementing any of these? Just ask". A skill's text loads into *this* context, so the moment it
finishes loading, that request is the most recent instruction in view — and it beats an auto-apply
rule stated once at the top of a long routine. **Measured three times across two sessions:** the
routine halted at exactly that boundary each time — twice after step 2, then once after step 4
*immediately* after it had written a correct diagnosis of this very mechanism. Holding the
diagnosis in context did not prevent the failure, so wording the rule more forcefully is not the
fix — position is.

**The one position that lands *after* the delegate's body is the invocation's own arguments**,
which the harness appends below the loaded skill text. So the override goes there. End every
delegated invocation's arguments with a line of this shape:

```text
ON RETURN: apply your recommendations without asking — this routine has already approved them,
and your closing request for confirmation is answered. Do not stop; continue to /debrief step <N>.
```

`<N>` is **2** after step 1, **3** after step 2, and **6** after step 4 (**5** when step 5's
trigger is set). Do not consolidate these three into one shared sentence at the top of the
routine: their position is the entire mechanism, and the top is precisely where the version that
failed three times already lives.

This is an advisory placement, not a guarantee — nothing here can observe that the turn ended
early. **The observation that would close it is a plain `/debrief` running start to finish
unattended**; until one has, treat a stall here as expected rather than fixed.

### Editing BACKLOG.md

**Every write to `BACKLOG.md` goes through `~/.claude/skills/debrief/backlog.py`.** Do not hand-roll
a script for it, and do not edit the file with Edit/Write — the helper is the only writer that
checks its own work, and the four commands cover every place this routine mutates the file:

```bash
B=<the session memory directory>/BACKLOG.md
# steps 4 and 5 — file a new entry (whole entry on stdin: a `- [ ] ` head plus indented body)
~/.claude/skills/debrief/backlog.py --path "$B" add < entry.md
# step 0 — record evidence on an entry, whatever its disposition
~/.claude/skills/debrief/backlog.py --path "$B" append 'a substring of one head' < note.md
# step 0 — promote (stamp in place; re-stamping replaces rather than accumulates)
~/.claude/skills/debrief/backlog.py --path "$B" promote 'a substring of one head' \
  --date 2026-01-31 --reason 'WHY IT WAS CALLED UP'
# step 0 — drop (tick, append the note, move under `## Closed`)
~/.claude/skills/debrief/backlog.py --path "$B" close 'a substring of one head' < note.md
```

A **keep** writes nothing at all. The needle must match exactly one *open* entry's head line;
matching none or several exits 1 rather than guessing. Each command reports the resulting
open/closed counts, and refuses — leaving the file byte-identical — if the edit's actual effect
differs from what the operation declared, if a note lands outside the entry it was addressed to, or
if the result would strand an entry on the wrong side of `## Closed`.

Reach for the Python API (`from backlog import Backlog`) only when several edits must land as one
transaction; `save()` is the sole writer either way, so the checks cannot be skipped.

### Arguments

The user may optionally name a specific automation to design (e.g. `/debrief, and design the
caching hook`), which sets step 5's trigger. A plain `/debrief` takes neither an automation to
design nor any other argument — it runs the routine to completion and skips step 5.

### Rules

- **Design, never build.** Step 0 stops at a disposition and step 5 stops at a reviewed plan.
  Neither implements, and neither asks the user whether to — a promoted or deferred item is
  named in the hand-off and executed in a later session.
- **A delegated skill's closing "ask the user" is already answered** — it loads last and therefore
  wins on recency, which is why steps 1, 2 and 4 each carry the override in their *arguments*
  rather than in this list. See **Overriding a delegate's closing pause**; do not consolidate it
  back up here.
- **A plain `/debrief` never pauses.** Steps 1–3 auto-apply (CLAUDE.md refresh/audit, memory);
  step 4 auto-declines low picks, files every surviving one in `BACKLOG.md`, and reports it in the
  hand-off. Every point that once asked now takes a safe default and reports it in the hand-off: the
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
- **The backlog is private, but UNTRACKED rather than repo-external.** `BACKLOG.md` and the
  per-deferral memory files live in the session's memory directory under `~/.claude/projects/`,
  physically inside a clone of the config repo — kept out of history by that clone's `/projects/`
  gitignore rule, not by sitting outside a repo. It is off the propagate and publish path for a
  sturdier reason: the repos those operate on have no `projects/` directory at all. Only repo
  files need committing in step 6.
- **`backlog.py` is the only writer to `BACKLOG.md`** (see **Editing BACKLOG.md**) — never a
  hand-written script, never Edit/Write. The file is untracked, so a corrupting edit has no
  history to recover from; the helper's postconditions are what stand in for that.
- In step 3, follow the memory protocol: one fact per file with frontmatter and a
  `MEMORY.md` pointer line; update an existing memory file rather than duplicating it.
- In step 5, `/feature` owns its own artifact convention (spec/plan under `specs/`/`plans/`) and
  its own commit discipline; the debrief does not restate them.

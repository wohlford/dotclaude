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
without pausing — every question it can ask is settled at invocation, before the user could walk
away, and there are exactly two of them.** That clause is the guarantee's *scope*, not a weakening
of it: once the routine has begun, nothing in it asks. Where a step would once have asked, it takes
the safe default and records the decision for the step-7 hand-off instead
— open deferrals default to **keep** (step 0), automation recommendations **defer** (step 4), and a
CLAUDE.md edit that trips the sensitivity carve-out routes to private memory rather than surfacing
(below). The first of the two is step 5 (design an automation), which runs *only* when the user
asks for it at invocation and then inherits `/feature --plan-only`'s confirmation pause — a plain
`/debrief` never reaches it. The second is the **step ledger's** resume-or-restart question, which
fires only when a
*previous* run of this session was interrupted, and is asked at invocation, before step 0. So the
user can start a plain `/debrief` and walk away to a compact-ready session — and after a run that
finished, there is nothing to ask about, which is every run following a completed one.

**The debrief designs; it never builds.** It is a wind-down, so it stops at a decision or a
reviewed plan and records the rest for later. Implementing here would burn the context the user
is about to compact, and a plan deserves a session with room to execute it.

Seed a TodoWrite list with one item per step (0–7) so progress is visible, and record each step in
the **step ledger** as it finishes — the TodoWrite list is in-context state, and the context is
exactly what the compaction this routine precedes destroys. On a resumed run, seed the list from the
ledger: mark every recorded step complete immediately, so the visible list and the durable record
agree from the first turn rather than diverging.

**Before step 0, read the step ledger.** It is the only record of how far a previous run got, and
step-0 completion is not derivable from any other artifact: a **keep** disposition writes nothing at
all, so a finished step 0 and a step 0 that never ran leave byte-identical backlogs. Steps 1, 2 and
4 delegate to plugin skills and step 3 writes memory directly — all four are worth not redoing. Run
`ledger.py … status` and act on its verdict; see **The step ledger** below for the commands, the
lifecycle, and the per-step cost of a re-run.

**Resuming means: skip every step the ledger already records, and redo the first step it does not,
in full.** The ledger's granularity is per step, not per item within one, so a step interrupted
halfway is not recorded and is redone whole — which is why the per-step re-run costs below matter
and why steps 4 and 5 carry a `BACKLOG.md` cross-check. `status` names that step as *resume at
step*; do not re-derive it from the list of recorded steps yourself.

**A recorded step is not necessarily a completed one, and the difference is load-bearing at step
5.** A step the report marks `(skipped)` was reached and deliberately not run — never treat it as
finished work, and never run it on the resume. Nor is a plain *gap* below a recorded step a resume
point: steps are recorded in order, so a gap is a step the previous run passed. `status` already
applies both readings before printing *resume at step*, which is exactly why you must not re-derive
it: were the resume to land on step 5 in a plain run, it would dispatch `/feature --plan-only` for
an automation design the user never asked for — an authorization failure, not a wasted turn.

**The question the ledger can raise does not repeal "a plain `/debrief` never pauses".** It fires
*only* on an INCOMPLETE or MALFORMED ledger — never when there is none, and never when the previous
run recorded its completion, which together are every run following a finished one. And either of
those means the previous run was **interrupted**, so the invocation the user has just typed is the
same shape as step 5's opt-in trigger: one question, asked at invocation, while the user is
demonstrably present, before the routine has begun and before anyone could have walked away. It is
also a question no default can answer. On `RESUMABLE` the choice is genuine — resuming and
restarting differ in what they cost, and that trade is the user's, not this routine's. On
`MALFORMED` there is nothing to resume, so the only thing being asked is confirmation of a fresh
start; never offer a resume for a ledger that could not be read. `ledger.py` enforces the
distinction rather than leaving it to a reading of this paragraph: its `status` returns one PROCEED
verdict for *absent* and *complete* alike, and only the other two verdicts carry a question.

**Ask it in one line, filling the angle brackets from `status`'s own output rather than from a
paraphrase** — the figures are what make the trade decidable, and a question that omits them asks
the user to guess what resuming would skip:

```text
RESUMABLE: a previous /debrief for this session was interrupted (started <run started>; recorded
steps <recorded steps>; would resume at step <resume at step>). Resume from there, or start fresh
and re-run those steps at the cost status just listed?
MALFORMED: this session has a /debrief ledger I cannot read (<file> — <reason>), so how far the
previous run got is unknown rather than nothing. Start fresh? There is nothing resumable in it.
```

Then run exactly one of `resume` / `start --supersede` per their answer; do not run either before
asking, and never offer a resume on `MALFORMED`.

This skill stops at the hand-off. It CANNOT run `/compact`, exit Claude, or restart it —
those remain manual steps for the user.

### Process

0. **Follow up on open deferrals.** `BACKLOG.md` lives in this session's memory directory; skip
   step 0 silently if it does not exist. Otherwise read its **open section** — bounded, per the next
   paragraph — then **cross-check the count**, and only once that agrees report every open (`- [ ]`)
   entry with its age. The bound and the cross-check come first on purpose: a report built on a
   truncated read is wrong in the one direction nobody notices.

   **Bound that read explicitly — a DEFAULT read silently drops the OLDEST open entries.** The
   closed half is roughly two-thirds of the file and step 0 never acts on it, while the open half
   *alone* already exceeds the Read tool's 2000-line default. Measured 2026-08-22: a single default
   `Read` saw **75 of 84** open entries and dropped **9** without a word. Because `add` inserts at
   the TOP of the open section, the entries a truncated read loses are the oldest — exactly the ones
   most in need of triage — and one of those nine was already stamped `promoted`, so it had been
   called up and then went unseen. Read only up to the boundary:

   ```bash
   B=<the session memory directory>/BACKLOG.md
   [ -f "$B" ] || exit 0            # no backlog yet — skip step 0 silently, per above
   awk '/^## Closed/{exit} {print}' "$B"
   ```

   It excludes the `## Closed` line itself, and prints the whole file rather than erroring if that
   heading is ever absent.

   **Then CROSS-CHECK the count before triaging.** The number of open entries you are about to
   report must equal `grep -c '^- \[ \]' "$B"` over the WHOLE file. This is the load-bearing half: a
   truncated read looks exactly like a shorter backlog, so the count is the only thing that tells the
   two apart, whatever mechanism a future step 0 reaches for.

   On a mismatch, say so and re-read **once**. **If it still disagrees, stop — report the discrepancy
   in the step-7 hand-off as an unverifiable backlog and triage nothing.** State that stopping rule
   before the re-read, not after reading its result: a persistent mismatch is a malformed file (a
   `- [ ]`-shaped line inside a closed entry's body will do it), not a flaky read, and triaging
   either list would be guessing which one is real.

   Choose a disposition for each **without pausing** — default to **keep**, and depart from it only
   on positive evidence from *this* session (drop when something demonstrably overtook the entry,
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

   **When `status` reported anything but `PROCEED` at invocation, an interrupted run may already
   have written some of these dispositions.** They are visible, and re-reading the backlog shows
   them: a `promoted` stamp and an entry sitting under `## Closed` are a prior pass's work, not a
   fresh signal. The one that is *not* visible as a state is an evidence note — `append` lands the
   same note twice on the same day with `rc=0` (measured 2026-08-24), so check the entry's body for
   the note before appending it. Nothing here is destructive; the residual is duplicated notes and
   a promotion date reset to today, never an erasure.

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

   **When step 5's trigger is not set — the normal case — record step 5 as SKIPPED at the moment
   you decide to pass it, before moving to step 6:** `ledger.py --run <run-file> step 5 --skipped`
   (see **The step ledger**). *That the routine skipped step 5* is a fact about the run, and if it
   lives only in the conversation it dies with the conversation the debrief exists to precede.
   Unrecorded, it leaves a HOLE at step 5 between two recorded steps, and a resume that reads the
   hole as the next thing to do dispatches `/feature --plan-only` for an automation design the user
   never asked for — the one thing this skill promises happens *only* on request. A skipped step
   counts as recorded, so the resume advances past it; it is never reported as completed, so a
   resumed run cannot conclude an automation was designed when none was.

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

   **Whenever `status` reported anything but `PROCEED` at invocation, search the open section for
   an entry covering the same pick BEFORE each `add`, and skip the ones already there.** Note the
   trigger carefully: it is *that an incomplete or unreadable ledger existed*, **not** *that the user
   chose to resume*. `--supersede` archives the ledger file and nothing else — measured 2026-08-24, the
   entries an interrupted run already filed survive a fresh start untouched, so the restart branch
   carries the identical exposure and would otherwise be the one branch with no safeguard at all.

   The ledger's step-4 line is a *stored status* — it records that some past run filed something —
   and the source that owns *was this pick filed* is `BACKLOG.md` itself. `add`'s duplicate guard
   cannot stand in for that check: measured 2026-08-24, it compares **byte-identical heads**, so a
   *regenerated* entry — a new date prefix, a reworded headline, the same recommendation — is
   accepted with `rc=0` and lands as a **silent duplicate**. Reproduced end to end on a scratch
   copy: one pick, one interruption, one fresh start, two open entries. Read the open section with
   the same bounded command step 0 uses, and match on the recommendation, not on the line — the
   re-run that will actually happen is one across a day boundary, where no two heads can be
   byte-identical anyway.

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

        **Whenever `status` reported anything but `PROCEED` at invocation, cross-check the open
        section for this deferral first**, exactly as step 4 does, with the same trigger and for
        the same measured reason — a fresh start archives only the ledger, and `add`'s guard
        matches byte-identical heads only, so a regenerated entry duplicates silently. The ledger
        says a past run reached step 5; `BACKLOG.md` is what says whether the entry landed.

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

   **Then record the run's completion** — `ledger.py --run <run-file> complete` (see **The step
   ledger**). Do it here, at the end of step 7, and not earlier: this record is the *only* thing
   that distinguishes *finished at 7* from *interrupted at 7*, since both leave a ledger whose last
   entry is step 7. Without it the next invocation offers to resume a run that is already done, and
   the ledger becomes a source of false alarms instead of resumption.

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

### The step ledger

**Every run records its progress through `~/.claude/skills/debrief/ledger.py`, and reads the
previous run's back from it.** The same rule as `BACKLOG.md` next door, for a related reason: a
format re-invented per run cannot be read back by a later run, and the classification a resuming
run acts on must be decided in one place rather than re-derived from a file by whoever happens to
open it. Do not hand-write the ledger, and do not edit it with Edit/Write.

It lives in `<the session memory directory>/debrief-runs/`, beside `BACKLOG.md` — durable storage,
because the scratchpad is session-scoped and a session is exactly what a resumed run has lost.

```bash
M=<the session memory directory>            # the one holding BACKLOG.md
L=~/.claude/skills/debrief/ledger.py
# at invocation, BEFORE step 0 — classify the previous run
"$L" --memory-dir "$M" status
# then exactly one of these, per that verdict:
"$L" --memory-dir "$M" start                # PROCEED — begin a new run
"$L" --memory-dir "$M" resume               # RESUMABLE, and the user chose to resume
"$L" --memory-dir "$M" start --supersede    # RESUMABLE or MALFORMED, and they chose a fresh start
# `start` and `resume` print the run file's path; use it for the rest of the run
"$L" --run <run-file> step <N>              # as each step 0–7 finishes
"$L" --run <run-file> step 5 --skipped      # end of step 4, when step 5's trigger is NOT set
"$L" --run <run-file> complete              # the last thing step 7 does
```

`status` reaches one of three verdicts, and the exit code carries it so the routine is not reading
prose to decide whether to pause:

| verdict | exit | when | what the routine does |
| :--- | :--- | :--- | :--- |
| `PROCEED` | 0 | no ledger, **or** the last run recorded its completion | `start`, then run from step 0 — **asking nothing** |
| `RESUMABLE` | 3 | a ledger with no completion record | surface the report and ask resume-or-restart |
| `MALFORMED` | 4 | a ledger that cannot be read back | surface the report, say the previous run's progress is *unknown* rather than *absent*, and offer a fresh start |

A malformed ledger never raises and never mutates anything — it is reported like any other verdict.
The alternative fails in the worst direction: a routine that crashes on its own bookkeeping file
has been stopped by the thing that existed to make it resumable.

**`start` refuses to run over an incomplete *or unreadable* ledger unless given `--supersede`.** That makes the
user's choice unskippable rather than advisory. `--supersede` **archives** the old file under
`debrief-runs/superseded/` rather than deleting it, so abandoned ledgers neither pile up in the live
directory — where a later read would have to choose between two runs of one session — nor vanish
before anyone can see how far the abandoned run got.

**`step <N> --skipped` records that the routine REACHED a step and deliberately did not run it**,
which is what a plain run does to step 5 every single time. It is a distinct disposition, not a
softer `done`: both count as RECORDED, so the resume point advances past a skip, but only a
completed step is ever presented as finished or quoted a re-run cost, and the report names the
skipped ones on their own line. The reason the distinction is worth a field is step 5 alone — an
unrecorded skip leaves a hole a resume walks into, and step 5's body is a `/feature --plan-only`
dispatch, so the cost of getting it wrong is an automation-design session the user never
authorized. `status` additionally treats any *gap* below a recorded step as passed, since steps are
recorded in order; the record is the mechanism and the gap rule is its backstop, for a ledger whose
skip went unrecorded. Neither rule names a step number, and neither is re-derived by the caller.

**A ledger is evidence about a past run, never an instruction**, so its report states what each
*completed* step costs to re-run and never claims that re-running is safe. That claim would be
false. A *skipped* step is absent from those costs entirely: it never ran, so it has no re-run to
cost. Measured 2026-08-24 against `backlog.py` on a scratch copy:

- **step 0** — `append` returns `rc=0` twice on the *same day* and lands the evidence note
  **twice**; `promote` re-stamps with today's date, resetting the age clock the stall report in
  step 0 reads; `close` on an already-closed entry refuses, byte-unchanged. Nothing is erased.
- **steps 4 and 5** — `add` accepts a **regenerated** entry with `rc=0` and duplicates it silently,
  because its guard compares byte-identical heads. These are the two steps that need the
  `BACKLOG.md` cross-check — on a resume *and* on a fresh start alike, since a fresh start
  archives only the ledger. What the cross-check closes is exactly one gap, on either branch: the
  silent duplicate `add`. It closes nothing else, and in particular it does not touch what step 5
  costs to re-run — a second `/feature --plan-only` session, which no cross-check can prevent.
- **steps 1, 2, 3, 6, 7** — re-running costs a delegate invocation or a reprint. No measurement was
  taken of what their delegates do twice, and the report does not pretend otherwise.

**Run identity is the session id PLUS the run's start timestamp, and the filename carries both.** A
session id alone is not a run identity: measured 2026-08-24, one id spanned 23 days and every
restart in between, so a ledger keyed on it hands a week-old run's ledger today's id — reproducing
the stale-ledger trap it was meant to prevent while appearing to close it. The session id also
*scopes* the read, because the project memory directory is shared by concurrent sessions of the same
project and a fixed filename would let two live runs clobber one file. `ledger.py` takes the id from
`$CLAUDE_CODE_SESSION_ID` and falls back to a named placeholder, so a report can always say which
session it looked for and where — "found nothing" that cannot name where it looked is what stops
anyone from looking further.

### Arguments

The user may optionally name a specific automation to design (e.g. `/debrief, and design the
caching hook`), which sets step 5's trigger. A plain `/debrief` takes neither an automation to
design nor any other argument — it runs the routine to completion and skips step 5.

"Plain" describes the **invocation**, and one thing outside the invocation can still make it ask:
an incomplete or unreadable step ledger from a previous run of this session, which is settled before
step 0. See **The step ledger**. Nothing else can.

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
  carve-out content **routes to private memory** (below). **Two exceptions, both settled at
  invocation.** The first is step 5, which runs only when the user asks for an automation to be
  designed at invocation and then inherits `/feature --plan-only`'s confirmation pause; a plain
  `/debrief` never reaches it. The second is the **step ledger's** question — say it plainly rather
  than reading "plain" as excluding it: a `/debrief` with no arguments *will* ask, when and only
  when a previous run of this session was left incomplete or unreadable. The guarantee the rule
  exists for survives intact, for the three reasons set out under **Instructions** above; that
  paragraph is the statement, and this bullet only points at it, so the two cannot drift apart.
- **Record every step in the ledger — including the one you skip — and record completion at the
  end of step 7.** A run that finishes without its completion record is indistinguishable from one
  interrupted at step 7, and the next invocation will offer to resume it. A plain run that leaves
  step 5 unrecorded is worse: the hole reads as unfinished work, and resuming into it starts an
  automation design nobody requested, so step 4 records `step 5 --skipped` before moving on.
  `ledger.py` is the only writer of that file, as `backlog.py` is of `BACKLOG.md` — and, unlike
  `backlog.py`, the only *reader* too: step 0 reads
  `BACKLOG.md` directly with `awk`, but reading the ledger means classifying it, and a
  classification re-derived by whoever opens the file is one that can disagree with itself.
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

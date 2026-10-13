---
name: feature
description: Run the methodical, risk-tiered pipeline for a change (triage → spec → spike → plan → reviews), then continue through subagent-driven execution to an integrated change; --plan-only stops at the reviewed plan
---

# /feature — Methodical Change Pipeline

Drive a change from idea to an **integrated change**: a risk-triaged design half (spec → spike → plan →
review) followed by **subagent-driven execution and integration** — a merge in a non-adopted repo, or a
re-derivation onto `dev` in a repo that has adopted the publication model (`.publication.toml` present
at the repo root). Orchestrates the `superpowers` skills and adds risk triage, an empirical spike, a
diverse-model review, an `/audit` sweep before the branch integrates, and — conditionally — `/vet` on
any skill or agent the diff touches. **Scale the rigor to the risk.**
Pass `--plan-only`
to stop at the reviewed plan instead.

## Instructions

The user wants to carry a change through the risk-tiered pipeline. Triage the work, run the
appropriate design lane (orchestrating the `superpowers` skills) to a reviewed, durably recorded plan
(committed, or in memory per Step 0.5's durability rule), then
**execute it with `superpowers:subagent-driven-development` and integrate the result** — merge in a
non-adopted repo, re-derive onto `dev` in an adopted repo — unless `--plan-only` was passed, in which
case stop at the plan.

### Arguments

The user must provide:
- `<one-line description of the change>` — seeds the brainstorming.

The user may optionally provide:
- `--plan-only` — stop at the reviewed, durably recorded plan (the legacy design-only behavior) instead
  of executing and merging.

### Models

Four roles, each with a default model (implementers follow the tier ladder under **Execute and integrate**),
so the pipeline's independence never depends on remembering it:

- **Controller: the session's own model, whatever it is.** It dispatches, verifies every return by content,
  commits, runs the long-run waiters, routes every question, and writes no design content and no task code
  (**Execute and integrate** names the two finish operations it keeps). The frontmatter carries no `model:` on
  purpose. Measured (claude 2.1.296, headless `claude -p`, 2026-10-12, a probe skill requesting `opus` from a
  Sonnet session): a frontmatter `model:` is honoured when the model invoked the skill through the Skill tool and
  ignored when the prompt typed the command, and the harness docs scope any such override to the current turn — so
  it could only downgrade an Opus controller's verification turn and never pin an author. State your own model id
  (from your system prompt) in Step 0's announcement.
- **Author: a pinned Sonnet subagent** — the Agent tool's `model: sonnet`, which does hold. It drafts the spec, the
  plan and every fold, through **Authoring dispatch**. Step 0 announces the REQUESTED author model; the actual one
  is the model id on line 1 of each return, and the presentation reports it. Every rule below keys on the
  **author set** — the models that actually wrote the artifact — never on the controller's model or on the alias
  this section requests.
- **Reviewer: Opus**, on the spec and on the plan of a full-lane run — see **Diverse-model review**, which also
  names the arbiter when author and reviewer disagree.
- **Open questions: Fable first.** Any genuinely open question that arises — a design fork, an ambiguity,
  and the clarifying questions `superpowers:brainstorming` would put to the user — goes to a Fable subagent
  (`model: fable`) before it reaches the user — a fold's own questions are the one exception, ruled by the controller
  under **Authoring dispatch** — per the global CLAUDE.md's "Asking Questions": ask it to
  answer on the merits **and** to judge whether the question is really the user's, and bring back a
  conclusion, not a menu, and send the questions brainstorming would ask at one checkpoint in a single
  dispatch. Fable cannot supply a fact only the user holds, so its second answer is what routes
  those back; **batch the questions it returns as the user's and ask them once, before the work that needs
  the answers.** **Authorization — pushing,
  publishing, anything outward-facing or hard to reverse — never goes to Fable**, and neither do the consent
  pauses named under **Design approval** (Step 0.5's branch confirmation among them): those ask the user
  directly. Risk appetite and scope still go to Fable first, which judges whether they are the user's. This
  section changes who answers a design question, never *may I?*. If Fable is unavailable, ask an Opus
  subagent instead (a Sonnet one if Opus is in the author set) and say so; if that is unavailable too, ask the user.

### Process

#### Step 0 — Risk triage (always; state the verdict)

Judge the work's **risk** on two axes. They are independent — either one alone routes to the
**full lane**, and a change can be high on one and low on the other.

**Uncertainty** — how likely you are to be wrong; reasoning alone cannot settle it:
- a novel mechanism or unfamiliar tool/library;
- an **empirical** unknown reasoning can't settle (e.g. "will this boot?", "does this API behave like X?").

**Stakes** — what it costs when you are wrong:
- it touches security or a fail-closed gate;
- large blast radius (hard to reverse; many consumers).

Otherwise the **fast lane**. Announce the lane, **which axis routed it** (or that neither fired), and
why. Bias borderline cases to the full lane. Include your own model id (the controller's) and the requested
author model, a pinned Sonnet subagent, in that announcement (see **Models**).

**The security trigger is absolute:** a security-flagged change *is* a full-lane change, so no
fast-lane change is ever security-flagged. If security surfaces after triage, the triage was wrong —
re-triage to the full lane rather than staying in it. The full lane buys a spec, a spike and two
diverse-model reviews of the spec and of the plan; **no separate security gate runs at the finish**, so the design
half is where a security-flagged change actually gets its scrutiny. Spend it there.

#### Step 0.5 — Create the work branch (always)

Before producing artifacts, ensure work is on a dedicated feature branch so the design commits and
the implementation share one branch that integrates as a unit — merges atomically in a non-adopted
repo, re-derives onto `dev` in an adopted repo. **Determine the base branch first:** if
`.publication.toml` exists at the repo root (adopted repo), the base is `dev` — never run the
pipeline directly on `dev`; otherwise (non-adopted repo) the base is `main`/`master`, as today. If on
the base branch, create and switch to `<type>/<kebab-name>` — `<type>` matching the change's commit
type (`feat`, `fix`, `chore`, …) and the name derived from the one-line change description
(CONTRIBUTING.md's convention). If on a branch that is neither the base nor one created for this
change, confirm with the user before proceeding; if they decline, ask which branch to use (or create
the standard `<type>/<kebab-name>` branch). With `--plan-only`, the branch is still created but is
left in place at the end (no merge).

**Tagging is marker-scoped for every commit this pipeline makes — set once, here, for the whole
run.** In an adopted repo (`.publication.toml` present) **every** pipeline commit — the design-phase
spec/plan/recommit commits (the draft, and the recommit after every fold, in either lane) and the
execution/gate commits under **Execute and integrate** alike — is a dev-side commit and goes through
`/commit --no-tag` (no tag, no `CHANGELOG` entry; versioning is `main`-only, minted at publish). (The
adopted finish's re-derivation bricks are the one exception to *going through `/commit`*: the brick
engine commits them, untagged — see **Adopted-repo finish**.) In a non-adopted repo every pipeline
commit is tagged per `/commit`, exactly as today. The design-lane steps below and **Execute and
integrate** apply this rule; they do not re-decide it.

**Design records must be proven durable — settle the destination once, here, for the whole run.**

- **Prove each record landed — the obligation is unconditional, every repo; only the instrument
  depends on where the record went.** For a **committed** artifact assert **both**:
  `git log --oneline -1 -- <artifact-path>` names a commit, **and**
  `git diff --quiet HEAD -- <artifact-path>` is clean. Neither alone suffices — the first passes on
  stale committed content, the second passes on a file never committed at all. An empty working tree
  is indistinguishable from a completed commit, and `nothing to commit, working tree clean` is a
  **failure reported calmly**; never infer success from the absence of an error.
- **Where that cannot hold, route to private memory — the remedy, not an escalation.** Probe each
  directory **separately** — `git check-ignore -q specs`, then `git check-ignore -q plans`; `-q`
  takes a **single** pathname and dies `fatal: --quiet is only valid with a single pathname` if given
  two — and you learn this before wasting the attempt. A gitignored `plans/` is common, anywhere
  those roots collide with Claude Code's own runtime dirs. Treat the two answers **independently**:
  only the ignored one reroutes. **Whether you learned it from that probe or from the post-condition
  failing**, the record then goes to memory *instead of* that commit — never as a routine second copy
  beside a healthy one — in the **primary checkout's** project memory directory: the main repo the
  worktree was made from. (`/feature` can run under worktree isolation, and a worktree's own memory
  directory is keyed to its path, where no later session will look.) One file per change named for
  the artifact's `YYYY-MM-DD-<name>` slug, spec and plan together, written to `/debrief` step 5's
  bar: **self-contained enough to re-derive the plan from scratch, including the rationale and any
  defect a review caught.** **The controller writes this record, carrying each artifact VERBATIM beside a
  transcription of the review findings and rulings** — no design content of its own — so a restore reproduces the
  author's text and does not make the controller its author.
  **Update that same file in place** at every later fold/recommit — a
  record that exists but has gone stale rebuilds this same bug one level up. **This record is a
  restore source**, and the two roots reroute independently, so keep out of it anything that must
  never reach a tracked path — where only one root is ignored, a restore writes it straight into
  one. The working file still
  lives at `specs/`/`plans/` for the pipeline and SDD to read; restore it from the memory record if
  it vanishes mid-run. **Then prove the memory record just as concretely** — it exists, is non-empty,
  and its content reflects the fold you just made. Same standard as bullet 1, not a weaker one.
- **Never `git add -f` a design artifact past an ignore rule.** Force-tracking publishes it: the
  publish path's convergence gate diffs the whole tree bar `CHANGELOG.md`, so anything tracked at
  `dev`'s tip is forced onto public `main` — and a design record routinely carries unfixed defects
  and live vulnerability detail.

The design-lane steps below apply this rule too; they do not re-decide it.

#### Design approval — present and proceed (both lanes)

The design half ends in a **presentation, not a gate.** At the end of either lane, present the
recorded spec/plan and **continue into Execute and integrate in the same turn** — never stop and
wait for the user to approve the design. The presentation is not optional and not abbreviated: it
is the record of what was decided and the user's opportunity to redirect. It names, per artifact, the actual author
model id, the reviewer and the arbiter, every dissent, every second-batch ruling, any arbitration that was not
diverse, and any ruling left unapplied. Only the *blocking wait*
is gone. If the user objects — then, or after execution has started — stop, dispatch the revision, and re-present;
work already done is discarded, not defended — unwind the commits the superseded plan produced
rather than building on them.

**This overrides `superpowers:brainstorming`'s design-approval gate in full.** That skill blocks
**for design approval** in three places: its `<HARD-GATE>`, its per-section approval while
presenting the design, and its wait for the user to review the written spec. Override all three —
honoring any one of them re-creates the same stall one step further on. Identify them **by
behavior, not by quoted text or line number**, since the plugin is versioned and its wording moves
between releases; but identify them **only as design-approval gates**. That skill waits for other
things too (its one-question-at-a-time dialogue, its visual-companion offer); those are not design
approval and are outside this override — but no one can answer them from inside an authoring dispatch, so the author
skips both and its `OPEN QUESTIONS` reach a Fable subagent first (**Authoring dispatch**, per **Models**).

**The authority is the user-instruction layer, and it is checkable.** `CLAUDE.md` names
`workflows.md` as this pipeline's definition, and `workflows.md` states that the design half ends in
a presentation rather than an approval gate. `superpowers:using-superpowers` places user
instructions — naming `CLAUDE.md` explicitly — above skills. **This subsection implements that
instruction; it does not invent it**, and a reader who distrusts it can check every link: the two
repo files just named, plus the precedence rule in `using-superpowers` itself. Two further signals
need no document at all — `/feature` is user-*installed* configuration, and installing a pipeline is
adopting it; and a run that reaches this point was invoked **without `--plan-only`**, the flag whose
only purpose is to stop at the plan, which is the user declining that stop in the one way the
pipeline offers.
This is the same move `/feature` already makes three times — overriding `brainstorming`'s terminal
"invoke `writing-plans`" step, `writing-plans`' own execution-choice menu (**Execute and integrate**
pre-answers it rather than putting it to the user), and `finishing-a-development-branch`'s option
menu. Identify each by behavior too, for the same versioning reason. **Never re-ground this in
an unwritten instruction.** An override resting on a past conversation the reader cannot check is
indistinguishable from rationalizing past the gate — and is a template for doing it to any other
gate — which is exactly what that gate exists to prevent.

**Three pauses are a different question and always survive.** This drops *is this design right?* —
never *may I?*:
- **`--plan-only`**, whose whole purpose is to stop at the plan;
- **every authorization gate** — pushing, publishing, or anything outward-facing or hard to
  reverse. CLAUDE.md owns these and `/feature` never relaxes one;
- **Step 0.5's branch confirmation**, which asks which workspace to use, not whether the design is
  good.

That list enumerates **consent** pauses only — the points where the pipeline asks the user to choose.
It is **not** a registry of every remaining stop. Every *failure-driven* stop is a separate,
untouched category: an `/audit` FAIL this branch introduced, a failing suite, an unresolved
conflict, a convergence check that does not match. Those
still stop and report, and nothing here licenses continuing past one.

#### Authoring dispatch (how every draft and every fold is made)

The spec, the plan and every fold are drafted by a **pinned Sonnet subagent** (Agent tool, `model: sonnet`), never
by the controller, which dispatches, verifies, commits and asks. A frontmatter pin does not hold (**Models**); the
Agent tool's parameter does.

- **Brief.** It sees none of this conversation, so carry everything: the change; Step 0's verdict; the branch and
  base; the ABSOLUTE artifact path and the durability rule (Step 0.5 — the author writes the working file even where
  the root is gitignored, and you record it to memory); the required shape, by pointer to the
  `superpowers:brainstorming` or `superpowers:writing-plans` skill file with its sections identified by behavior; for
  a plan, the spec path, the spike result and the rulings so far; the required sections (under **Verify by content**)
  and that the artifact file is written whichever path `brainstorming` would take; the return format below; that the user is
  unreachable from inside the dispatch; that it has Bash but no Grep (measured 2026-10-12, a general-purpose Sonnet
  subagent). Name the gates to skip, by behavior:
  `brainstorming`'s clarifying dialogue, its approval gates, its commit-the-design-document step and its terminal
  "invoke `writing-plans`" step, and `writing-plans`' execution hand-off — each asks a user a subagent cannot
  reach, and a stalled delegate reads as
  COMPLETED. It DOES run the parts that are its own: the spec's sectioned design and spec self-review; the plan's
  header, `## Global Constraints`, task structure, `Expected:` rows and plan self-review. Paste
  [`DELEGATING.md`](../../DELEGATING.md)'s block **last** and whole, its path cited around it, as for any implementer.
- **No nested pipeline.** Tell it, as procedure with the reason: do not dispatch any agent (Fable included) and do
  not invoke `/feature` or any pipeline skill — it inherits the global CLAUDE.md, which says to run `/feature` and to
  put questions to a Fable subagent, and either would add an author the reviewer chain cannot see. Questions go in
  `OPEN QUESTIONS`; the controller owns both. It reads the superpowers skills as shape references only. Tool use
  inside a subagent is not visible, so `DISPATCHES: none` is a self-report, never a measurement; the structural
  remedy, an agent definition whose `tools:` omits `Agent`, is not built here — it loads only after a promote and a
  restart, so it cannot be measured in the run that writes it.
- **Return — for drafts and folds alike.** Line 1: its exact model id. Then `OPEN QUESTIONS` (each: the question, the
  assumption the draft took, its recommended answer — present even if "none"), `FILES READ`, `INTENTIONAL MATCHES`
  (any placeholder-pattern hit that is real content) and `DISPATCHES: none`. The artifact carries NO placeholders: an
  assumption is taken for every open point. A fold's return adds its disposition per finding (below) and gets the same
  content check, because a fresh Sonnet may be a new model id.
- **Verify by content, never from the report.** Line 1 names a model id; the artifact has its required sections
  (spec: goal, decisions or design, out of scope; plan: header, `## Global Constraints`, tasks each with files
  and a verification; the fast lane's combined artifact: what/why and the task steps, each with a verification, and
  nothing more is required of it); `grep -E 'TBD|TODO|FIXME|XXX|\[GAP|<placeholder'` over it is empty, or every hit is listed under
  `INTENTIONAL MATCHES` — recorded, never refolded; `OPEN QUESTIONS` and `DISPATCHES: none` are present. A non-Sonnet
  model id joins the **author set**. An author that cannot be established gets ONE re-ask (SendMessage for its id); if
  that fails too, the author set is Sonnet plus the controller's own model. Silence is not a report: re-derive the
  whole verification from the artifact. A draft or fold that fails the check gets ONE re-dispatch with the failures
  named, counted inside that artifact's budget; failing again stops and reports.
- **One question round per artifact version.** Send the `OPEN QUESTIONS` to Fable in one dispatch (the merits, and
  whether each is really the user's — **Models**); batch the survivors to the user once. The rulings go back to the
  same author (SendMessage), or to a fresh Sonnet handed the artifact path and the rulings; recommit once they are
  applied. A fold's own `OPEN QUESTIONS` form a second batch, which you rule yourself and list in the presentation —
  an exception to Fable-first that keeps the user's own: authorization and anything only the user can answer (scope, a
  fact only they hold) go to the user.
- **Folds.** Review findings go back to the author, which returns the revised artifact plus a disposition per
  finding, `FOLDED` or `DECLINED — reason`. The fold is its own re-derivation of the finding, never a transcription of
  the reviewer's suggested repair. The controller does not edit design content: if tempted, dispatch the fold. That
  covers every revision in the design half — the review's findings (the controller's step 2 and step 5 findings the
  reviewer confirmed, or the controller kept, among them), the spike's result and the record of each unrun check (a
  non-empty unrun-check list alone triggers the fold); and, on the fast lane when no review runs, the controller's
  step 2 findings. An author declining a controller finding that is a MINOR one, or any on a fast lane with no
  review, is the controller's ruling, not an arbitration; a controller finding
  confirmed at BLOCKER or MAJOR or raised to one of them by the severity floor, or one the controller kept over a
  dismissal and rated BLOCKER or MAJOR (the dismissal, floor and rating rules are in **Diverse-model review**), is
  treated as the reviewer's for this purpose and goes through arbitration if declined. A ruling of yours that differs from what the artifact says — a MINOR the author
  declined, a second batch, a controller finding it declined (a MINOR one, or any on a fast lane with no review) —
  travels in the next scheduled
  author dispatch (a review fold or the post-ruling fold); if none remains, record it as unapplied in the
  presentation.
- **Who counts as an author.** A model that wrote content a reviewer could find against. Committing, fixing a
  filename date and pasting the constraint block are not authoring. If the controller ever authors content (a failed
  post-ruling fold, under **Diverse-model review**), its model joins the author set.

#### Fast lane (low risk on both axes)

1. Dispatch the author (**Authoring dispatch**) for a **short combined spec+plan** (a few sentences
   of what/why + the task steps) — or just a short plan if there's no design to settle. "Lightly"
   trims what the brief asks for: no collaborative dialogue, no 2–3 alternative
   approaches — **not** the author's self-review, which still runs and which step 2 depends on having
   run. One author dispatch drafts the whole combined artifact, so an Opus controller never becomes its author. It
   saves to `plans/YYYY-MM-DD-<name>.md`; commit via `/commit`.
2. **Ultrathink the design, not the author's checklist.** The author's own
   self-review already covered placeholders, internal consistency, scope, and ambiguity — do **not**
   repeat that. If the short-plan path skipped the spec half of that self-review, nothing has run those
   checks: do one quick pass over them first. Either way, the pass that earns its keep is the one no
   checklist can make: is this the right design, is the decomposition sound, does it solve the
   actual problem? Then ask the full lane's step-5 questions of any task that carries a
   verification: **is that check falsifiable, and can it pass at all?** Judge both by step 5's
   tests, and run the check the way step 5 says — the defect class does not care which lane
   produced the plan. Number what you find `C1..Cn`, list the checks you left unrun and why, and decide step 3 first,
   before anything is dispatched: if step 3 buys a review, hand the findings and the unrun-check list to it
   (**Diverse-model review**) and dispatch no fold of your own; if it buys none, dispatch ONE fold of them and of the
   unrun-check list (a non-empty list alone triggers it), briefed with the rehearsal clone's ABSOLUTE path and step
   5's rules, and recommit via `/commit` (no fold check and no arbitration: both belong to a review). Step 3's
   recommit covers the review's fold, the controller findings included. With no findings and an empty unrun-check
   list, no fold is dispatched, and a review step 3 bought still runs, with no second turn since there is nothing to
   hand it; a non-empty list alone triggers the artifact's one fold (the review's, or this one), in the same slot, so
   the list always reaches the plan.
3. Run a diverse-model review **only if** the change's **stakes** are real even though they stayed
   under Step 0's bar — the fast lane means the design is easy to reason about, not that being wrong
   is cheap. The clearest case is stakes that sit in the **rework, not the reach**: a long mechanical
   change, few consumers, cleanly reversible, but a wrong plan means implementing all of it twice.
   One outside read is worth it there; a spec and a spike are not. Above Step 0's bar you would be on
   the full lane, whose step 6 reviews the plan regardless. Else skip. Pick the reviewer per
   **Diverse-model review** below. If it ran, **dispatch the one fold of its findings, the controller findings it
   confirmed or kept and the unrun-check list (a non-empty list alone triggers the fold), briefed with the rehearsal
   clone's ABSOLUTE path and step 5's rules, run the fold check and any arbitration (there), re-run per step 5 any
   check the fold changes, and recommit via `/commit`** — same as the full
   lane's step 6. A review whose findings are not
   folded is a review you paid for and did not use.
4. Present the recorded spec+plan, then
   **execute and integrate** (below) **in the same turn** — per **Design approval — present and
   proceed** above.

#### Full lane (real unknowns or high stakes)

1. **Spec.** Dispatch the author (**Authoring dispatch**) to produce the spec; its `OPEN QUESTIONS` take the
   question round there. Among the gates the brief names to skip is `brainstorming`'s terminal "invoke
   `writing-plans`" step — **do NOT follow it**: you interpose the spike + reviews first. The author saves the
   spec to `specs/YYYY-MM-DD-<name>.md`; commit via `/commit`.
2. **Ultrathink the spec — the design, not the author's self-review.** The author already
   self-reviewed for placeholders, internal consistency, scope, and ambiguity; re-running that list
   is a second same-model pass over the same blind spot, which is exactly what the diverse
   review exists to cover. Ask instead what its checklist cannot: is this the right approach, are the
   boundaries sound, what did we fail to consider? Number what you find `C1..Cn` and hand it to the review below as
   controller findings (a second turn after the reviewer's own pass); dispatch no fold of your own. **Then the spec gets the
   diverse-model review (see below) on the full lane — always, before the spike**, so a finding can still
   redirect the spike: Opus reviews it, the author makes the one fold (the review's findings and the controller
   findings it confirmed or kept), the fold check and any arbitration run, and you
   recommit via `/commit`. It is the one outside read the
   design gets before anything is built on it. Do not stop to ask: which reviews to buy is a budget decision
   this pipeline owns, not a design approval.
3. **Spike the #1 risk.** Name the assumption whose failure invalidates the most downstream work. If
   it is **empirical** and its cheapest *decisive* signal is **cheap to probe**, run a throwaway
   probe of that one assumption now — probe the decisive signal ("does GRUB appear when a blank VM
   boots the ISO?"), not the whole thing ("does the 60-min install finish?"). Dispatch the fold of the result
   (invalidated → adjust, maybe loop to step 1 — a loop-back starts a new artifact version with fresh review
   counters), then recommit via `/commit`; a fold that only adjusts the
   spec gets no review of its own, and the plan's Opus review reads the adjusted spec. If the top risk is empirical but **not** cheaply
   probeable, say so and rely on the plan's decision-gates instead. If it isn't empirical, say "no
   spike" and continue.
4. **Plan.** Dispatch the author (**Authoring dispatch**), pointing it at `superpowers:writing-plans` with the
   spec path, the spike result and the rulings so far; the brief names the execution hand-off among the gates to
   skip. It saves to `plans/YYYY-MM-DD-<name>.md`; commit via
   `/commit`.
5. **Ultrathink the plan — the design, not the author's checklist.** The author's `writing-plans` pass already
   self-reviewed for spec coverage, placeholders, and type consistency. Your pass asks what it
   cannot: does the task decomposition hold, does the spike's result still hold, is each task
   independently reviewable, and **is each task's verification falsifiable** — would it actually fail
   if the task were done wrong? On that last one, read each task's check and not just its
   instructions: does it test the real deliverable or a proxy that can pass while the requirement is
   missed; is it anchored by content rather than a line number the task's own earlier steps will
   shift; does it contradict the text it verifies? **Then ask whether each check can pass at all**
   — one that never can is still falsifiable, so the questions above wave it through. Look for an
   expected result the tree at that step cannot produce: a count of 0 whose needle the task's own
   new text adds; a count omitting occurrences its other edits add; rows expected red that the old
   code already passes; a gate the untouched tree already fails; an anchor for a line shape the
   file does not have. **Answer by running the check, never from your model of the finished tree**
   — that model wrote the check. Rehearse in a scratch clone made outside the repo and discarded once the one fold that
   follows the review (on the fast lane without a review, the controller's own fold) and the controller's confirming
   re-run of what it changes are done, or when the review returns with nothing to fold, or, on a fast lane with no
   review, no findings and no unrun checks, when the controller's pass ends, keeping the base branch and dropping the remote:
   `git clone -q -b <branch> <repo> <dir>`, `git -C <dir> branch <base> origin/<base>`,
   `git -C <dir> remote remove origin`, `git -C <dir> config commit.gpgsign false`,
   `git -C <dir> config tag.gpgsign false`. Those settings
   are not the safety net — a push to an explicit URL, or a repo a check creates for itself, still
   escapes them. **`~/.claude/scripts/plan-rehearse.py` does the mechanical half** — `--create`
   builds that clone:

   ```bash
   ~/.claude/scripts/plan-rehearse.py --create --scope "$PWD" --branch <branch> \
       --base <base> --plan plans/<plan>.md --clone <dir outside the repo>
   ```

   Then re-run it without `--create` after applying each task's edits. A run extracts every
   `Expected:`, TOKENIZES each command with `scripts/lib/git_command.py` — the same tokenizer the
   push guards use — and refuses a word in COMMAND POSITION that writes or reaches the network, a
   path or `-C`/`cd` target landing outside the clone, and anything it cannot resolve statically
   (`~`, an unknown variable, an opaque `sh -c`, a backtick it cannot read into). It runs the rest
   inside the clone and prints each one's exit status and output beside the plan's own quoted
   expectation. **`--base` must name a ref the clone actually has** — it is what taint is measured
   against, and the tool refuses rather than proceed without it, because an unresolvable base once
   produced a clean verdict over a tree nothing had been compared to.

   **The gate's scope, stated once and not extended.** It refuses what it can read STATICALLY: a
   word in command position that writes or reaches the network, and a path, redirect, `cd` or
   `-C` target that resolves outside the clone. **It does not follow a value through an
   indirection** — a command name reached through a variable, a command behind a wrapper carrying
   its own arguments (`timeout 5 …`, `nice -n 19 …`), or anything a `python` fence computes rather
   than quotes. Those are the shared tokenizer's accepted limits, recorded in three other scripts
   that depend on it, and not a defect list to work through. **There is NO sandbox.** The clone is
   disposable and has no remote, and the tool installs a deny-all `pre-push` hook that stops an
   ACCIDENTAL push — it does not contain a check routing around it, since `--no-verify` and
   `-c core.hooksPath=…` are the check's own to pass, both measured landing a ref past it. This
   repo's push guards do not help either: they are PreToolUse hooks on YOUR shell and never see a
   child the tool spawns. **So the rule is the one this step already gave you — read every check
   before you run it, including the ones the tool ran.**
   **It never applies an edit, and the token PASS is not
   in its vocabulary.** Both are measured rather than caution: across this repo's plans, prose
   `Modify:` entries outrun `Create:` ones 107 to 32, so the state most checks need is yours to
   produce; and every `Expected:` is prose no matcher can adjudicate — in
   `plans/2026-07-04-exec-bit-guard.md`, `:112` expects exit 0 while `:294` expects exit 127, so
   any rc-based verdict scores the RED-phase rows backwards. It reports what RAN; you compare.
   A check whose earlier tasks' paths are still identical to base comes back TAINTED — still run,
   because an instrument that cannot answer at all fails the same way at base — one whose command
   could not be extracted UNPAIRED, one it would not execute REFUSED, and the run is INDETERMINATE
   while any remain, so what went unrehearsed is visible rather than absent. In the clone, apply
   every earlier task's edits and this task's up to the check's step — by hand where the plan
   describes an edit in prose — **then run the tool again**, so a check that was TAINTED now
   reports against the state you just produced; a TAINTED row you never re-run is a check nobody
   rehearsed. **An UNREADABLE row is different, and re-running will not clear it**: that task
   declares its files in prose, so the tool cannot tell whether its edits landed. Measured, 25% of
   the tasks in this repo's plans do, which leaves about a fifth of all checks unmeasurable — read
   those by hand, and do not read the row as work still outstanding.
   Its report is where you read what each check DID; **the judging is still yours, and
   so is reading every check — including the ones it ran.** A gate with holes becomes a licence the
   moment the text says otherwise: an earlier draft of this paragraph exempted its RAN rows from
   the read below, and the review then found seven command shapes it wrongly allowed, each of which
   would have landed in exactly that exempted bucket.
   **Read each check before running it:**
   re-point every path and `-C` target at the clone, since one still aimed at the real checkout
   grades the unedited tree; a check that would still reach outside the clone — a write, a push, a signing prompt, a
   network call — is itself a finding: hand it to step 6's review as a controller finding (a check never run has no
   run behind it, so the rehearsal tool's refused row or the controller's reading of the check is its evidence; for
   the dismissal standard and the severity floor in **Diverse-model review** it counts as run-backed, with result
   class `REACHES-OUTSIDE`), and never run it as it stands. Then run each check from inside the
   clone; one that outruns a tool call goes through
   `~/.claude/scripts/run-long.sh`, as any long check does. Leave a check unrun only when it cannot
   be expressed as a command, must reach outside the clone, or depends on an edit the task leaves
   to the implementer's judgment; hand each unrun check and why to the review as an un-numbered list in the second
   turn, and have the fold record it in the plan, where the implementer will see it. Re-run every check that is
   revised — but rehearsal drives ONE fold per artifact
   version: brief the fold that follows the review (on the fast lane with no review, the controller's own fold) with the rehearsal clone's ABSOLUTE path and this step's rules — read each check before
   running it, re-point every path and `-C` target at the clone, run only inside it — so it re-runs the checks it
   changes there. A check that must reach outside the clone, or would outrun a tool call, it records as unrun, with
   why, for you to run through `run-long.sh`; any still failing it records in the plan and in its return. Your own
   re-run confirms it, and a disagreement goes in the presentation, never into another fold. Measured: seven unsatisfiable
   checks passed this step, each caught later by a reviewer doing exactly this or by a delegate
   refusing to bend correct work to a wrong number. Number what it finds `C1..Cn`, carrying the output and the task and
   step the clone had reached for each finding backed by a run, and hand it to step 6's review as controller findings
   (a second turn to the reviewer, after its own pass); dispatch no fold at this step.
6. **One diverse-model review of the plan** (see below). Dispatch one fold of its findings, of the controller
   findings it confirmed or kept and of the unrun-check list (a non-empty list alone triggers the fold), then run the fold
   check and any arbitration there; re-run per step 5 any check the fold changes (one still failing is recorded as
   unrun by the fold itself, per step 5, and the fold check reads that note); recommit via
   `/commit`. (If a review at this stage invalidates the spec, loop
   back to step 1 as with spike invalidation — a new artifact version, fresh counters.)
7. Present the recorded spec + plan, then
   **execute and integrate** (below) **in the same turn** — per **Design approval — present and
   proceed** above.

#### Diverse-model review

The value is a **different model than the author** catching blind spots same-model review misses.
- **Pick a reviewer that differs from every model in the author set** (**Authoring dispatch**). Normally that is
  Sonnet alone (**Models**), so the default is **Opus**. If Opus is in the author set, use **Fable**; if Fable
  is, use Opus; if both are, use Sonnet. **If that reviewer is
  unavailable** (when
  Anthropic has it disabled, the Agent returns a "… currently unavailable" error), take whichever of Opus
  and Fable still differs from every author. **If no such model is available**, review with Sonnet
  anyway; when Sonnet wrote the artifact, announce that pass as same-model, not diverse, and do not count
  it as the diverse review. State which model actually reviewed. Announce the choice.
- Spawn it with the **Agent tool**, `model:` = the chosen **alias** — `fable`, `sonnet`, or `opus`.
  **Never `haiku`:** critiquing a plan is design judgment, the top of the tier ladder
  ([agents/README.md](../../agents/README.md)), and a cheap reviewer here buys a cheap review of
  the one artifact the whole pipeline rests on. Use a skeptical critical-review prompt asking for
  prioritized findings
  (`[BLOCKER|MAJOR|MINOR] location — problem — fix`), passing the artifact path + relevant files.
- **Detect silent degradation:** ask the reviewer to state its exact model id as the first line of its
  reply; if it doesn't match what you requested, treat the pass as reviewed by the model it states: if that
  is an author's model it is same-model and not the diverse review, so dispatch the next reviewer in the
  chain above once, then degrade as above. **Never block** — degrade and tell the user which model actually
  reviewed.
- **Controller findings — a second turn, never part of the first brief.** In the design half the controller's own
  reasoning pass and its runs (full-lane steps 2 and 5, fast-lane step 2) are inputs to the review, not folds of their
  own. Number each finding `C<n>`, with its location, the problem and, for a finding backed by a run, the command
  exactly as run, the tree or clone state it ran in (the task and step the clone had reached) and the output verbatim,
  as text in the brief or as a file path named in it, never summarised: a summary of a run is a model of the run.
  Attach no suggested repair, no severity and no result class, since severity is the reviewer's call and a repair is
  the author's own re-derivation (`Folds`, in **Authoring dispatch**); the severity floor on a run-backed finding is
  applied afterwards, by the dismissal bullet below. The reviewer's first dispatch carries no controller findings and
  returns its own findings, its model id on line 1. The controller then sends the `C<n>` list by SendMessage to the
  reviewer whose pass counts as the diverse review (after **Detect silent degradation**, the reviewer of record, never
  a model it replaced); if that fails, a fresh dispatch of the same model is handed the artifact, the reviewer's
  first-pass findings verbatim and the list. If every reviewer in the chain degraded to a same-model pass, no pass
  counts as the diverse review: the second turn goes to the reviewer that did read the artifact, and its replies are
  recorded as non-diverse in the presentation. The reviewer's first-pass findings are frozen: it does not revise them after reading
  the list. It returns, per controller finding, `C<n> CONFIRMED [BLOCKER|MAJOR|MINOR] — reason` or
  `C<n> DISMISSED — reason`, and a controller finding that duplicates one of its own is `CONFIRMED` with the label
  "same as own finding at <location>" and enters the fold once, at the reviewer's severity. Verify by content that
  every `C<n>` has a disposition; a missing one gets ONE re-ask (SendMessage, or the fresh dispatch above), and one still
  missing after it is treated as kept under the floor in the dismissal bullet below (a reasoning-only one enters at the
  controller's own rating). The second turn and a re-ask are neither author dispatches nor diverse-model passes: they
  take no author slot and no review slot. The checks the controller left unrun, with why, travel in the same second
  turn as an un-numbered list. It is information, not a finding: it is not dispositioned, and it always goes to the
  fold brief, which records it in the plan; a non-empty unrun-check list alone triggers the artifact's one fold, in the
  slot already counted. The reviewer may raise its own finding that a check should have been run, but it cannot dismiss
  the list. Confirmed findings enter the fold at the reviewer's severity, raised only by the floor in the dismissal
  bullet below; a controller finding confirmed at BLOCKER or MAJOR, or raised to one of them by the floor, is read by
  the fold check and counts for arbitration as the reviewer's.
- **Dismissing a controller finding that rests on a run.** The reviewer is read-only and runs nothing, so a dismissal
  of such a finding stands only if it carries a misrun showing — it quotes the lines of the controller's own handed
  output that show the run did not measure what it was taken to measure (wrong tree or path, a refusal read as a
  failure, a TAINTED row read as a result, state not yet produced) — or a requested counter-run: the exact command it
  wants run and the state that command needs, named as a task and step of the plan. The controller adjudicates by
  RUNNING, never from a model of the tree: in a fresh clone made with `plan-rehearse.py --create` and brought to the
  named task and step by applying the plan's edits as step 5 does, never in the clone the review fold is briefed with,
  at the coordinate the reviewer named or, where it named none, the one recorded with the finding. It reads the
  requested command under step 5's rules first; a command that reaches outside the clone or would outrun a tool call
  is not run, and the dismissal then carries no counter-measurement. It runs the check the finding rests on and the
  requested command in that one state, then discards the clone. The dismissal is accepted only when the controller's
  own run of the check the finding rests on, in the state the reviewer named (or the corrected state a misrun showing
  points at), shows it passes: the requested command shows the check passes, or, for a misrun showing, a re-run of the
  check in the state it should have been in passes. The finding is then dropped and recorded in the presentation.
  Where that run still fails, the finding is kept and its result class is read from that run. Otherwise the finding is
  kept over a dismissal. A dismissal of a run-backed finding
  that carries neither a misrun showing nor a requested counter-run is not accepted, and the finding is kept with no
  counter-run to make. A severity floor, which is a property of the run's result, applies to ANY run-backed controller
  finding whatever its disposition (confirmed at any severity, kept after a dismissal, or left undispositioned after
  the re-ask): when the controller's run shows the check fails its own `Expected:` row (`FAILS-EXPECTED`), cannot pass
  against the tree the plan produces (`CANNOT-PASS`), or reaches outside the clone as written (`REACHES-OUTSIDE`), the
  finding enters the fold at AT LEAST MAJOR. Only the controller raises, only after the dispositions are in, only
  upward, and the reviewer's own severity stands everywhere else; MAJOR versus BLOCKER is the controller's call for a
  finding it rates; a run showing only a MINOR-grade discrepancy (the verdict unchanged, a diagnostic line or a path
  that still resolves is off) (`MINOR-DISCREPANCY`) leaves the finding where it stands, and a kept one may be rated
  MINOR. A
  finding at BLOCKER or MAJOR enters the fold and the fold check, and if the author declines it, arbitration applies
  as for any declined BLOCKER or MAJOR; the arbiter receives, besides the author's reason and the fold-check verdict,
  the controller's run output, the reviewer's dismissal with its reason and the counter-run's result. A dismissal that
  rests on reasoning alone, with no run behind the finding, stands. The presentation records each dismissal and each
  raise beside the dissents, one row each: a raise reads `C<n>` — confirmed at X, raised to Y — result class — the
  reason; a dismissal reads `C<n>` — location — DISMISSED — the reviewer's reason — accepted, or kept at
  [BLOCKER|MAJOR|MINOR] — result class (`FAILS-EXPECTED`, `CANNOT-PASS`, `REACHES-OUTSIDE` or `MINOR-DISCREPANCY`) —
  the reason for the rating.
- **Fold check.** After a fold, the same reviewer (the original by SendMessage, or a fresh dispatch of the same
  model handed the artifact and the findings) re-reads only the BLOCKER and MAJOR findings against the fold (a
  controller finding confirmed at BLOCKER or MAJOR or raised to one of them by the severity floor, and a finding kept
  over a dismissal and rated BLOCKER or MAJOR, included),
  returns `ADDRESSED` or `NOT ADDRESSED` for each, and names any NEW breakage the fold introduced. One per artifact
  version, on both lanes wherever a review ran, by the model the chain picked for that review. It is not SDD's
  "re-review", which judges task code. New BLOCKER or MAJOR breakage counts as a contested finding below.
- **Arbitration (design half only, both lanes).** A disagreement exists iff the author marks a BLOCKER or MAJOR
  `DECLINED`, or the fold check returns `NOT ADDRESSED` on one or names new BLOCKER or MAJOR breakage; declare it
  from those two artifacts, never on
  either party's say-so. A MINOR the author declines you rule and record. Send every contested finding, with the
  author's reason and the fold-check verdict, to ONE arbiter dispatch per artifact version, chosen by the same chain
  as the reviewer — a model that differs from every author **and** from the reviewer (Fable in the normal case of a
  Sonnet author and an Opus reviewer). Its ruling decides, over an Opus BLOCKER too. If the chain yields none, you
  rule and the presentation says the arbitration was not diverse. A Fable that already ruled a fork the reviewer is
  now contesting is named in the dissent line and dispatched without its earlier ruling's reasoning.
  **It ends there.** The author gets exactly one post-ruling fold dispatch. Its content check is the usual one plus:
  every ruled finding's disposition matches the ruling (a finding the ruling upheld is `FOLDED`; one it rejected
  leaves the text unchanged). If that fold fails the check, you author it: your model joins the author set, the
  chain re-picks, that one fold is reviewed once, and you rule on what it finds with no further arbitration. A later
  review that re-raises a ruled finding is closed by citing the ruling, unless it carries a measurement the ruling
  lacked — that is a spike result, and it loops back to step 1 as a **new artifact version with fresh counters**.
  A run takes at most two loop-backs; a third stops and reports that the design keeps invalidating itself.
  A ruling that the change is security-flagged or larger in blast radius re-triages to the full lane, which counts as a
  new artifact version but not as a loop-back; a user's objection after the presentation likewise starts a new version
  at the stage it concerns, and the ceiling never limits the user. The arbiter's own model id is checked on line 1
  exactly as the reviewer's is. Authorization
  and anything only the user can answer never reach the arbiter (**Models**); the user is pulled in only for
  authorization, for a question Fable says is theirs, and for the pipeline's failure-driven stops. The presentation
  records each dissent: finding — severity — the author's reason — the ruling in one line — the reviewer's
  dissent, if any.

#### Execute and integrate (default; both lanes)

With the plan reviewed and durably recorded,
**continue** (do not stop). **Check the publication model
once, here, before starting** — Step 0.5 already set the pipeline-wide tagging rule; this check
determines how *this phase's* commits carry that rule out and how the branch finishes, and nothing
else in this section depends on it: does `.publication.toml` exist at the repo root?
- **Not present (non-adopted repo) — today's behavior, unchanged.** Steps 1–4 below apply exactly
  as written: every task commit goes through `/commit`, which applies the repo's per-commit semver
  tag (per the Step 0.5 rule), and step 4's finish **merges** the feature branch back to its base.
- **Present (adopted repo) — the publication model.** Steps 1–3 — Execute and the two gates — apply
  unchanged, **except tagging**: per the Step 0.5 rule, every dev-side commit — the SDD task commits
  in step 1 and the gate-fix commits folded during steps 2–3's `/audit` and `/vet` — goes through
  **`/commit --no-tag`**. Dev-side commits carry **no semver tag
  and no `CHANGELOG` entry** — versioning is **`main`-only, minted at publish** (a later sub-project).
  Step 4's finish also diverges: instead of merging, it follows **Adopted-repo finish: re-derive onto
  `dev`** (below).

1. **Execute** the plan with `superpowers:subagent-driven-development` on the feature branch — fresh
   subagent per task, per-task spec+quality reviews, and a final whole-branch review. (When
   `writing-plans` offers to set up execution, this IS that execution.) **Make every task commit
   through `/commit`, in the foreground** — a signed-commit repo cannot sign inside a background
   subagent, so the controller commits each completed task via `/commit` rather than relying on SDD
   subagents to `git commit`. **Tagging is marker-conditional; `/commit` itself is not.** Non-adopted
   repos: `/commit` applies the repo's per-commit semver tag, as today. Adopted repos: per the marker
   check above, these are dev-side commits — go through `/commit --no-tag` (no tag, no `CHANGELOG`
   entry; versioning is `main`-only). **Never bare `git commit` either way** (the re-derivation bricks
   in **Adopted-repo finish** are the one exception: the brick engine commits them, enforcing the
   subject and signing itself) — where a tag applies it skips the tag and corrupts the release
   sequence, and in every case it skips `/commit`'s foreground-signing discipline.

   **The controller writes no task code either.** A throwaway spike probe and the scratch edits in step 5's rehearsal
   clone are measurements, not task code or design content, and stay the controller's to run. It dispatches,
   verifies, commits and asks, in the design half and
   in SDD alike. An `/audit` FAIL or a `/vet` finding the branch introduced is fixed by a dispatched implementer,
   and a plan defect found mid-execution is an author fold: you record it in the end-of-run report (your closing
   message at the finish), update the memory
   record in place and recommit via `/commit` (SDD's own rulings govern there, not arbitration; the final
   whole-branch review covers the edit).
   Two finish operations stay the controller's,
   because they repartition or reconcile code a review already judged: this skill marks the adopted finish's brick
   re-narration non-delegable, and whether a rebase conflict can be resolved without altering a commit's frozen
   intent is the controller's own stop-or-resolve call. A plan-supplied driver that writes tracked files is run by
   the controller too, since the constraint block rightly forbids a delegate to run one outside a fixture; a
   task reviewer then reviews the applied diff.

   **Execution tiers do not vary by lane** — the triage governs the design half and, through the plan, what is executed.
   **Scale by model tier, never by dropping a review.** SDD's per-task review and its final
   whole-branch review ask different questions of the same code (did this task build what its brief
   demanded, vs. does the branch hold together), and SDD names skipping either a hard *never* — even
   on a single-task plan, where they read the same diff through different rubrics. Honor its contract:
   the model tier changes *what you pay per pass*, never *how many gates the code clears*.
   - **Always pass the model explicitly.** SDD inherits *your session's* model when none is given, so
     every implementer and reviewer silently runs at the session tier — usually the most capable and
     most expensive. That one omission costs more than every review in this pipeline combined, and it
     is invisible: nothing reports it.
   - **Deliver [`DELEGATING.md`](../../DELEGATING.md)'s constraint block into every implementer
     dispatch, the way that file prescribes** — pasted verbatim and positioned **last**, plus the two
     companion steps it names (copy it into the plan's `## Global Constraints`; cite the file's path
     around the block, never inside it). Last, because a delegate's own template may instruct it to
     commit its work, and an override only lands if it arrives after the text it overrides. Paste the
     block **whole**: only one of its lines concerns committing, and the rest are independently
     load-bearing, so trimming it to what a given dispatch appears to need silently drops them.
   - Dispatch per the tier ladder in [agents/README.md](../../agents/README.md): **implementers** at
     the cheapest tier that fits (a task whose plan text carries the complete code is transcription →
     `haiku`; multi-file integration or judgment → `sonnet`); **task reviewers** at `sonnet`; the
     **final whole-branch review** at `opus` on **both** lanes: implementers and task reviewers are
     normally Sonnet and this ladder never dispatches an implementer at `opus`, so this is the pass that
     gives the code a different model's read, the same reason **Diverse-model review** exists for the plan.
     If Opus is unavailable, use Fable; if neither, review at Sonnet and announce it as same-model when
     Sonnet wrote the code.
   - **Escalation ceiling.** SDD's fix rounds 1–3 resume the implementer and rounds 4–5 use a fresh **Sonnet**: a
     task that began at haiku reaches Sonnet there, and this overrides SDD's "a tier above the stuck implementer"
     for a Sonnet one, since this ladder never dispatches an implementer at Opus. SDD's round-5 breaker is the
     controller's adjudication — park the task or rule — and a plan defect it finds takes the fold route above.
   - **Parallelize writes, serialize the suite.** Concurrent tasks may edit freely, but only **one**
     agent runs the tests at a time, and only after the writes it depends on have landed — suites read
     the code under test from the shared working tree, so a peer mid-write means a sibling executes a
     half-written file. Never explain away a contended run as flakiness: its verdict proves nothing
     either way, so quiesce the writes and re-run rather than re-reading the result.
2. **Sweep the mechanics — `/audit` (always).** Run `/audit` over the repo before the branch integrates
   and have a dispatched implementer fix every `FAIL` **this branch introduced** — you commit the fix via `/commit`,
   in the foreground. **It is MECHANICAL ONLY: a plain
   `/audit` does not run the test suite, which is a separate requirement behind `--tests`.** So a
   plain `RESULT: PASS` is never evidence the suite passed — reading `audit.sh`'s source confirms
   the wrong belief here, since its pytest call sits inside the opt-in branch and looks
   unconditional. Only the run's own enumeration of the checks it performed settles it. A repo may carry **pre-existing** FAILs the
   change never touched (generated content, a lint rule adopted after the fact): report those and
   leave them — do not loop chasing a clean sweep this branch cannot deliver, and never widen the
   change's scope to fix them here. **Decide which bucket a FAIL is in mechanically, never by
   impression:** it is pre-existing if it also fires with `/audit` on the base branch, or if its
   offending file is absent from `git diff <base>...HEAD`; otherwise this branch introduced it and it
   must be fixed. Every other gate in this pipeline is model judgment;
   this is the only deterministic one. In a non-adopted repo, `superpowers:finishing-a-development-branch`
   verifies the test suite and nothing else; in an adopted repo the per-brick `/audit` run during
   re-derivation is the authoritative mechanical gate instead (below). Either way, without this
   branch-level run a broken relative link, a lost exec bit, or drifted index tables can ride through
   to the finish, none of which a reviewer reliably catches and all of which `/audit` catches exactly.
   The per-edit hooks already cover most of it, but only for files touched
   via Edit/Write: a task that writes a file from a shell heredoc trips no hook, so this is the
   backstop. Read-only, deterministic, seconds — run it **first**, before the model gates below, so
   mechanical breakage fails fast instead of after a paid review. Relay any `SKIP` — each is a
   coverage gap, not a clean bill of health. **Read the verdict from `audit.sh`'s last line of
   stdout, as an allowlist.** Only `RESULT: PASS` and `RESULT: FAIL` are results; `ERROR`, `INCOMPLETE`, a hedged or
   unrecognized value, and **an absent line** are not a result and are not clean — anything not named here is
   not-clean by default. Once the last line is `RESULT: PASS` or `RESULT: FAIL`, the bucketing above decides whether
   the FAILs are this branch's to fix: a `RESULT: FAIL` whose FAILs are **all** mechanically confirmed pre-existing still clears this gate, and an
   introduced FAIL does not. `fail_count` is repo-wide, not diff-scoped, so a repo carrying pre-existing FAILs can
   never reach `PASS` and demanding it would make the gate unreachable. Why the last line of stdout, and not a count of
   `FAIL` lines or an exit code: CLAUDE.md's hazard groups `Nothing ever ran — silence is not a pass` (a killed sweep,
   and "no `FAIL`" is not a pass) and `The exit status you read is not the verdict` (an exit code, or a harness's
   "completed", is not the verdict) say why, and the `rc=` inside the line is part of what is read. "Say so and
   continue" is the *wrong* answer here, unlike step 3 below: continuing past the pipeline's
   only deterministic gate is exactly integrating on judgment alone. A usage error (`RESULT: ERROR
   rc=2` — a bad flag, a
   `--scope` that isn't a git repo) is yours to fix — correct the invocation and re-run. If it still
   cannot run, stop and report; do not integrate the branch. **Adopted repos:** this branch-level run is fail-fast
   only — the authoritative mechanical gate on the adopted path is a **per-brick `/audit`** run
   during the re-derivation (the finish step, below), and a clean sweep here does not stand in for
   that one.
3. **Vet the skills and agents the diff touches (conditional).** If the branch's diff touches
   `skills/*/SKILL.md` or `agents/*.md`, run **`/vet <paths>`** over them. Nothing else here knows the
   canonical skill/agent shape: SDD's reviewers judge the code as code and will pass a `SKILL.md`
   whose `name` contradicts its directory or whose frontmatter is malformed. `/vet` dispatches the
   reviewers that do. Fold findings (a dispatched implementer fixes them; you commit via `/commit`, in the foreground)
   and re-run until clean; **verify each
   finding against the artifact and `STYLE.md`'s documented carve-outs before applying it** — the
   reviewers are advisory and do produce false positives. A finding you verified as a false positive
   and said so does **not** block "clean" if a re-run restates it — record the dismissal and move on.
   If `/vet` reports a reviewer as **failed or unavailable** instead of a verdict, say so and continue
   — never loop waiting on a verdict that cannot arrive. If the diff touches neither, skip and say so.
4. **Finish.** **Non-adopted repos** (today's behavior, unchanged): finish with
   `superpowers:finishing-a-development-branch`: verify the project's test suite
   passes (if the repo has none, say so and rely on the per-task reviews), then
   **merge the feature branch** back to its base and clean up. The **merge is the default end
   action** — do not pause to choose it. If tests fail, stop and report; do not merge. **The base
   must not have moved either** — assert `git merge-base <base> <feature-tip>` equals the base tip
   before merging. **If it moved, do not merge: rebase onto the base tip** and re-run **at minimum**
   the suite and `/audit` — re-running a review changes nothing about the base, so it is no remedy
   for this precondition. Grade that `/audit` exactly as step 2 grades it: an introduced FAIL blocks,
   a pre-existing one does not. **If the rebase conflicts, resolve against each commit's frozen
   intent — and if one cannot be resolved without altering what a commit was meant to do, stop and
   report**, exactly as the adopted finish's point 1 does. That is a failure disposition, not an
   alternative to rebasing: it ends the finish, and never licenses merging a branch whose base moved. The re-run list differs from the adopted finish's, which names the
   suite and the final review instead — not because the two arms disagree about what a rebase
   invalidates, but because the adopted path gets its authoritative `/audit` structurally, per brick,
   during re-derivation, while this path's only full `/audit` run was the branch-level one in step 2,
   which the rebase invalidates. **Also assert the final review's commit and the feature tip have the
   same tree** — a fast-forward guarantees the merged tree equals the *tip's*, never that the tip is
   what any review judged, and the adopted arm needs the same assert for the same reason. **Re-check
   this assert against the rebased tip: a rebase that changes the tree makes it fire again**, so the
   final review's re-run is a *consequence* of rebasing, never an *alternative* to it — a tree-neutral
   base advance leaves the tree unchanged and needs no re-review. **On a tree mismatch, do not
   merge:** stop and report, or re-run SDD's final whole-branch review on the tip and merge only if
   **that review comes back clean** — a FAIL there is a stop, not a formality discharged by having
   re-run it. Otherwise the merge integrates a tree no review judged, and any conflict resolution
   lands in it having appeared in no reviewer's diff.
   **Adopted repos**: do not merge — finish instead via **Adopted-repo finish: re-derive onto
   `dev`**, below.

#### Adopted-repo finish: re-derive onto `dev`

Adopted repos never merge the feature branch. Instead, re-derive the completed, gate-passed change as
clean, proven-per-brick commits appended directly onto `dev`, then discard the feature branch. The
feature branch's SDD task history is a scratch pad that got the change *right*; `dev`'s history is
what production runs and what a future recast reads, so it earns the narrative bricks the messy task
history never had to be.

1. **Freeze the oracle.** The feature-tip tree — the SDD-reviewed, gate-passed final state from steps
   1–3 above — is the convergence target; it is never re-coded, only repartitioned into bricks.
   **Record the feature-tip SHA at the freeze** — after the full-suite precondition below has passed
   on it, and before the branch is discarded — the tip tree-compare in point 4 needs it. (Order: the
   review's tree assert, then that suite run, then the freeze.) **Assert the commit that SDD's final whole-branch review judged and the feature tip
   have the same tree** before freezing; on mismatch, do not freeze — re-run that review on the tip
   and freeze only if **that review comes back clean**, or stop and report; a FAIL there is a stop,
   not a formality discharged by having re-run it — never freeze a broken tip as the oracle. Point 4
   compares the integrated tree to this *oracle*, never to what a review judged, so without this
   assert any commit landing between the final review and the freeze rides in unreviewed and point 4
   still passes.
   - **Precondition (BLOCKER): `dev` must not have moved since the branch was cut.** Assert
     `git merge-base dev <feature-tip>` equals `dev`'s current tip. `/feature` spans sessions, so
     `dev` advancing underneath a long-running branch is plausible, not a corner case. If `dev`
     moved, **rebase the feature branch onto `dev`'s current tip, re-run at minimum SDD's final
     whole-branch review, since a rebase changes the tree that review judged, and the full-suite
     run below — and freeze the *rebased* tip** as the oracle instead. Skip this and the
     tip `git diff --quiet` in point 4 would still pass — but only by *reverting* `dev`'s interim commits back out, a false
     GREEN that silently deletes work `dev` already has. If the rebase conflicts, resolve them against
     each commit's frozen intent (never silently drop a hunk) — and if one cannot be resolved without
     altering what a commit was meant to do, **stop and report**; never force a resolution that
     changes the brick's intent. If the post-rebase review **or the full-suite run below** fails, **stop
     and report — do not freeze a broken tip as the oracle.**
   - **Precondition: the full suite has passed on this exact tip.** Step 2's `/audit` and every
     per-brick `/audit` below are the plain sweep, which runs no suite — so without this run the
     first suite to grade the change is point 4's, after the bricks are already on `dev`, and a
     failure there costs a reset of `dev` and a full re-derivation. Order it: rebase if `dev`
     moved, settle the final review and its tree assert, **then** run this, then freeze. One run
     serves the rebase too. From the repo root, with a fresh `--out` path each run (it refuses an
     existing one):

     ```bash
     ~/.claude/scripts/run-long.sh --out <file outside the repo> \
       --expect 'RESULT: (PASS|FAIL|ERROR|INCOMPLETE) rc=[0-9]+' -- \
       skills/audit/audit.sh --scope "$PWD" --tests
     ```

     Wait on it with `run-long.sh --wait` through `run_in_background`, and read its verdict back with
     `--status`. Freeze only on `RESULT: PASS rc=0`; a `FAIL`, any other value, or an absent
     line is a stop — point 4 needs a passing suite, so a pre-existing FAIL would stop there too.
     It grades the tree as of its launch, so any change to the tip afterwards — a fix for a FAIL, a
     rebase — re-runs it, **and re-runs the final review and its tree assert**, which judged a
     tree that no longer exists. A repo with no `skills/audit/audit.sh` of its own has no sweep to run; run its full
     suite by hand instead. Point 4's tip suite is **unchanged and still mandatory**: this is an
     earlier run that keeps a failure cheap, not a substitute for the one that gates the deploy.
2. **Re-plan a clean brick sequence.** Working from `dev..<feature-tip>`, narrate the total change as
   a ground-up sequence of bricks — **repartitioning the reviewed code, not re-inventing it**. This is
   a mini-recast per feature: a foreground, judgment-driven re-narration that cannot be delegated to a
   subagent, because every brick becomes a signed commit the re-narrator is accountable for. **Record
   `dev`'s current tip as the pre-finish SHA now** — point 3's PROGRESS-refusal recovery needs it, and
   it must be captured before the first brick lands.
3. **Per brick:** build it with the brick engine's dev mode — the **installed** copy, since `dev`'s
   working tree may predate the branch's own:

   ```bash
   ~/.claude/scripts/publish-brick.sh --dev [--final] --scope <repo> \
     [--artifact-dir <dir outside the repo>] <feature-tip-SHA> '<subject>' <file>...
   ```

   Point `--artifact-dir` outside the repo — an artifact written inside it would fail the *next*
   brick's own clean-tree precondition. Draft each subject exactly as `/commit` would — its format
   and scope rules — and pass `--final` on the **last** brick. The engine refuses unless `HEAD` is on
   `dev` with a clean tree, every listed file differs from the oracle at `HEAD`, the subject is
   conventional and under the repo's `.commit-conventions.toml` advise threshold
   (`ALLOW_LONG_SUBJECT=1` overrides, as for `/commit`), and every path `dev` has changed since it
   last met the oracle already equals the oracle — this finish's "`dev` must not have moved"
   precondition, re-checked at every brick. **A repo with no
   `skills/audit/audit.sh` of its own is re-derived entirely by hand** — the engine refuses before
   committing, since a dev brick cannot be proven with nothing to prove it against. It then checks the
   files out of the oracle, asserts they match it and that nothing else moved, commits, and runs
   **`/audit`** on the live repo at that commit, reading the verdict as an allowlist. This per-brick
   `/audit` is the **authoritative** mechanical gate on the adopted path — the branch-level run in step
   2 above is fail-fast only and does not stand in for it. **Proceed only on its last line,
   `RESULT: PASS rc=0 brick=dev`.** **The engine partitions by whole file.** A re-derivation that
   needs any brick holding part of a file is built **entirely by hand**, re-deriving the same
   assertions, and says so — do not mix the two: after a partial brick the engine's progress check
   refuses every later brick, correctly, because a path short of the oracle is indistinguishable from
   `dev` having moved. **An in-script commit passes no PreToolUse hook**, so the recast suite gate that
   a typed `git commit` touching `skills/recast/` source would trigger does not fire per brick; point
   4's tip suite covers it. **On an `/audit` FAIL** the brick's files already equal the oracle, so
   there is nothing to fix inside it: the partition split something `/audit` validates as a pair. Drop
   the brick (`git reset --hard HEAD~1`, as the engine prints) and **re-plan the brick sequence**; if it
   still cannot be resolved, stop and report — the feature branch stays intact (discard happens only
   at point 5). **On a PROGRESS refusal once bricks from this finish have already landed**, list
   `git log --oneline <pre-finish-SHA>..dev` and reset to the pre-finish SHA
   (`git -C <repo> reset --hard <pre-finish-SHA>`) **only if every commit it lists is a brick this
   finish made** — resetting past a commit this finish did not make can delete another session's
   work. **Otherwise stop and report; never reset past a commit this finish did not make.** If every
   listed commit is this finish's own, `dev` did not move — an earlier brick of this finish diverged
   from the oracle — so reset and re-plan the brick sequence from point 2 against the same oracle.
   **Run each brick with a Bash tool timeout of at least ten minutes**; on `RESULT: INCOMPLETE`, an
   unanticipated status, or no verdict line at all, check `git log -1` first — the commit may already
   have landed with its audit unread — and if so drop it (`git reset --hard HEAD~1`) before re-running.
   **Never a fixup commit**: it pollutes the narrative the re-derivation exists to produce.
4. **At the tip, prove convergence.** Assert the tree matches the frozen oracle exactly, lossless —
   the last brick's `--final` does this, reporting `converged: HEAD tree == oracle`; by hand it is
   **`git diff --quiet <feature-tip-SHA> HEAD`** — **and** run the **full test suite once more**.
   Both must pass. (This subsumes `finishing-a-development-branch`'s suite check; adopted repos never
   invoke that skill.) **Run that suite even when you already ran it on the oracle and the tree was
   just proven identical** — it is the last suite run before `/propagate` updates the live
   configuration, and *"the suite already passed on an identical tree"* is the exact argument
   CLAUDE.md's "Never reason your way past the last gate" rule refuses there. A run you made on the
   branch verified your last fix; this one gates the deploy, and a shared tree does not merge their
   jobs. A `--final` failure that lists remaining paths (`still differs: <path>`) is
   **not** this stop condition: build another brick from the paths it names, with `--final` on the
   new last one. If either check fails for any other reason, **stop and report; do NOT discard the
   feature branch** — it is the only record of the re-derivation and is needed to diagnose the
   divergence.
5. **Discard the feature branch.** `dev` has advanced by the new bricks and is what production runs;
   the branch's messy per-task history is intentionally not preserved.

**Brick-boundary rule:** never split across two bricks anything `/audit` validates holistically — an
intermediate brick that separates such a pair is a false FAIL, not a real one, and chasing it defeats
the per-brick gate's purpose. At minimum:
- a new skill or agent and its regenerated `sync-docs` index-table entry land in the **same** brick
  (split them and the intermediate brick carries a drifted index table, which fails the sync-docs
  check even though the full change is correct);
- a new shebang file and the commit that sets its exec bit land in the **same** brick (split them and
  the intermediate brick has a non-executable script, which fails the exec-bit check).

A moved file and the referrers whose links it breaks (`md-links`) is a fine third example of the same
rule, but the two above are required in every re-derivation.

**Honest guarantee — do not overclaim.** The re-derivation guarantees that `dev`'s intermediate
bricks are each `/audit`-clean and that the sequence **converges to a suite-passing tip**. It does
**not** mean each brick is independently proven against the full test suite — only the tip carries
that proof (point 4). Report it that way; never write "each brick proven" or anything implying
per-brick suite coverage this procedure does not run.

#### Stop at the plan (`--plan-only`)

When invoked with `--plan-only`, `/feature` **ends at the reviewed, durably recorded plan**. Do not invoke
an execution skill — when `writing-plans` offers to set up execution, **decline it.** Tell the user
the plan is reviewed and recorded, the feature branch is left in place, and execution is a separate
step — e.g.
`superpowers:executing-plans` or `superpowers:subagent-driven-development`.

### Rules

- **Default: execute then integrate.** After the reviewed plan, run
  `superpowers:subagent-driven-development`, then finish per the marker check above: a non-adopted
  repo merges via `superpowers:finishing-a-development-branch`; an adopted repo instead follows
  **Adopted-repo finish** (re-derivation onto `dev`), which never invokes that skill. **Only
  `--plan-only` stops at the plan** (then never implement; decline `writing-plans`' execution offer).
- **Invocation** — model-invocable; launch it (or let the user run `/feature`) only for changes
  that warrant methodical design, not trivial edits. A deliberate, scale-to-risk kickoff.
- Save spec/plan at the repo root (`specs/`, `plans/`, `YYYY-MM-DD-<name>.md`); commit each as
  produced via `/commit` — **unless that path is gitignored**, in which case the durable record goes
  to private memory instead and is never force-added past the ignore (Step 0.5's durability rule).
  Either way, confirm the record actually landed rather than reading an empty tree as success.
- **Every commit the pipeline creates goes through `/commit`**, design-phase and implementation
  alike, run in the **foreground** for signing — never fall back to bare `git commit`. Tagging is
  marker-conditional: non-adopted repos get the repo's per-commit semver tag + `CONTRIBUTING.md`
  conventions, as today; adopted-repo dev-side commits go through `/commit --no-tag` (no tag, no
  `CHANGELOG` entry — versioning is `main`-only). **Outside the adopted finish's re-derivation
  bricks, which the brick engine's dev mode commits — enforcing the conventional subject, the repo's
  length policy and the signing assertion that this path would otherwise supply — the invariant that
  never bends is `/commit`, always, in the foreground; only the tagging it applies is marker-scoped.**
- **Never integrate on judgment alone.** `/audit` runs before every branch integrates — merge in a
  non-adopted repo, per-brick during re-derivation in an adopted repo — it is the pipeline's only
  deterministic gate, and the model reviews do not substitute for it any more than it substitutes for
  them (`/audit` = the mechanical half, `/vet` and the reviews = the judgment half; a full review is
  both). It is read-only and near-free, so it is never the thing to cut for budget.
- **A change that *touches* a `SKILL.md` or an `agents/*.md` gets `/vet` before the branch
  integrates** — editing an existing one counts, not just authoring a new one. The generic reviewers
  judge code as code; only `/vet`'s reviewers know the canonical skill/agent shape. Self-limiting — the
  condition never fires in a repo that has neither.
- **A security-flagged change routes to the full lane and is scrutinised THERE** — in its spec, its
  spike, and the diverse-model reviews of its spec and its plan. There is no separate security gate at the finish;
  `/audit` and `/vet` are the only gates the branch clears before it integrates.
- Scope the spike to one assumption; bias borderline triage to the full lane.
- Budget, per artifact version (a loop-back counts separately, and a run takes at most two): the diverse-model passes — on the full lane
  **two**, Opus on the spec and Opus on the plan, each with at most one fold check and one arbitration; on the fast
  lane none unless stakes warrant one, on the plan. Author dispatches are bounded per trigger — the draft, one
  question round, one fold of the spike's result (the spec only), one fold per review (which also carries the
  confirmed and kept controller findings and the unrun-check list; a non-empty list alone triggers it), one fold of the controller findings when no review
  runs (the fast lane only; where a review runs, the controller findings ride its fold, so the two are never both
  paid), and one post-ruling fold (the single re-dispatch of a failed content check repeats
  the dispatch it follows and takes no new slot): at most four per artifact version and five for the
  spec, so a full-lane run pays up to five for the spec and four for the plan, and a fast-lane run typically one to
  three and at most four. Ultrathink is cheap, but
  step 5 and fast-lane step 2 also run the plan's checks, which cost their own runtime; the spike
  substitutes for a second reasoning pass. The default execute-then-integrate phase adds the SDD subagent passes (one implementer +
  reviews per task, plus the final whole-branch review, `opus` on both lanes, so a fast-lane run pays that Opus pass) — and `/vet`'s reviewer dispatch when the
  diff touches a skill or agent (`/audit` is
  deterministic and near-free — excluded from this accounting, and never cut for budget); `--plan-only`
  skips all execution cost. The adopted arm adds one cost the non-adopted merge finish never pays: the
  re-derivation's per-brick re-narration (**Adopted-repo finish**, point 2) is foreground,
  judgment-driven, and non-delegable to a subagent. **Execution spend is controlled by tier, not by
  cutting gates** — see "Scale by model tier, never by dropping a review"; never trade a review away to save budget.
- **Never repeat the author's mechanical checklist as a second pass on one artifact.** The author already self-reviews its own
  output against each sub-skill's (`brainstorming`, `writing-plans`) mechanical checklist; the
  ultrathink steps own the altitude question those checklists cannot ask, plus the rehearsal that
  runs each plan check, and nothing else.
  Repeating a checklist doubles the pass that shares the author's blind spot — the reason the
  diverse-model review exists at all. Independence is what catches defects, not repetition.

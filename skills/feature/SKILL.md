---
name: feature
description: Run the methodical, risk-tiered pipeline for a change (triage → spec → spike → plan → reviews), then continue through subagent-driven execution to a merged change; --plan-only stops at the reviewed plan
---

# /feature — Methodical Change Pipeline

Drive a change from idea to a **merged change**: a risk-triaged design half (spec → spike → plan →
review) followed by **subagent-driven execution and a merge**. Orchestrates the `superpowers` skills
and adds risk triage, an empirical spike, a diverse-model review, an `/audit` sweep before the merge,
and — conditionally — `/vet` on any skill or agent the diff touches and a `/security-review` of the
branch's diff. **Scale the rigor to the risk.** Pass `--plan-only` to stop at the reviewed plan
instead.

## Instructions

The user wants to carry a change through the risk-tiered pipeline. Triage the work, run the
appropriate design lane (orchestrating the `superpowers` skills) to a reviewed, committed plan, then
**execute it with `superpowers:subagent-driven-development` and merge** — unless `--plan-only` was
passed, in which case stop at the plan.

### Arguments

The user must provide:
- `<one-line description of the change>` — seeds the brainstorming.

The user may optionally provide:
- `--plan-only` — stop at the reviewed, committed plan (the legacy design-only behavior) instead
  of executing and merging.

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
why. Bias borderline cases to the full lane.

**The security trigger is absolute:** a security-flagged change *is* a full-lane change, so no
fast-lane change is ever security-flagged. If security surfaces after triage, the triage was wrong —
re-triage to the full lane rather than staying in the fast lane and relying on the merge-time
`/security-review` to compensate.

#### Step 0.5 — Create the work branch (always)

Before producing artifacts, ensure work is on a dedicated feature branch so the design commits and
the implementation share one branch that merges atomically. If on the base branch (`main`/`master`),
create and switch to `<type>/<kebab-name>` — `<type>` matching the change's commit type (`feat`,
`fix`, `chore`, …) and the name derived from the one-line change description (CONTRIBUTING.md's
convention). If on a branch that is neither the base nor one created for this change, confirm with
the user before proceeding; if they decline, ask which branch to use (or create the standard
`<type>/<kebab-name>` branch). With `--plan-only`, the branch is still created but is left in place at
the end (no merge).

#### Fast lane (low risk on both axes)

1. Use `superpowers:brainstorming` lightly to produce a **short combined spec+plan** (a few sentences
   of what/why + the task steps) — or just a short plan if there's no design to settle. "Lightly"
   trims the collaborative dialogue (the one-question-at-a-time exploration, the 2–3 alternative
   approaches) — **not** its spec self-review, which still runs and which step 2 depends on having
   run. Save to `plans/YYYY-MM-DD-<name>.md`; commit via `/commit`.
2. **Ultrathink the design, not `brainstorming`'s checklist.** If `brainstorming` ran, its own
   self-review already covered placeholders, internal consistency, scope, and ambiguity — do **not**
   repeat that. If you took the short-plan path and skipped brainstorming, nothing has run those
   checks: do one quick pass over them first. Either way, the pass that earns its keep is the one no
   checklist can make: is this the right design, is the decomposition sound, does it solve the
   actual problem? Then ask the full lane's step-5 question of any task that carries a verification:
   **is that check falsifiable?** Judge it by the three tests step 5 lists — the defect class does
   not care which lane produced the plan. Revise inline.
3. Run a diverse-model review **only if** the change's **stakes** are real even though they stayed
   under Step 0's bar — the fast lane means the design is easy to reason about, not that being wrong
   is cheap. The clearest case is stakes that sit in the **rework, not the reach**: a long mechanical
   change, few consumers, cleanly reversible, but a wrong plan means implementing all of it twice.
   One outside read is worth it there; a spec and a spike are not. Above Step 0's bar you would be on
   the full lane, whose step 6 reviews the plan regardless. Else skip. Pick the reviewer per
   **Diverse-model review** below. If it ran, **fold its findings; revise; recommit via `/commit`** —
   same as the full lane's step 6. A review whose findings are not folded is a review you paid for
   and did not use.
4. Present for confirmation, then **execute and merge** (below). If the user declines or asks for
   changes, revise and re-present — never execute an unconfirmed plan.

#### Full lane (real unknowns or high stakes)

1. **Spec.** Use `superpowers:brainstorming` to produce the spec. When brainstorming reaches its
   terminal "invoke `writing-plans`" step, **do NOT follow it** — return here to step 2 (you interpose
   the spike + reviews first). Save the spec to `specs/YYYY-MM-DD-<name>.md`; commit via `/commit`.
2. **Ultrathink the spec — the design, not `brainstorming`'s checklist.** `brainstorming` already
   self-reviewed for placeholders, internal consistency, scope, and ambiguity; re-running that list
   is a second same-model pass over the same blind spot, which is exactly what the step-6 diverse
   review exists to cover. Ask instead what its checklist cannot: is this the right approach, are the
   boundaries sound, what did we fail to consider? Revise inline. **High stakes** here means Step 0's
   stakes axis — the one naming security and large blast radius. For such designs a second
   diverse-model review of the *spec* (the step-6 mechanism) is opt-in — offer it here, before the
   spike.
3. **Spike the #1 risk.** Name the assumption whose failure invalidates the most downstream work. If
   it is **empirical** and its cheapest *decisive* signal is **cheap to probe**, run a throwaway
   probe of that one assumption now — probe the decisive signal ("does GRUB appear when a blank VM
   boots the ISO?"), not the whole thing ("does the 60-min install finish?"). Fold the result in
   (invalidated → adjust, maybe loop to step 1). If the top risk is empirical but **not** cheaply
   probeable, say so and rely on the plan's decision-gates instead. If it isn't empirical, say "no
   spike" and continue.
4. **Plan.** Use `superpowers:writing-plans`. Save to `plans/YYYY-MM-DD-<name>.md`; commit via `/commit`.
5. **Ultrathink the plan — the design, not `writing-plans`' checklist.** `writing-plans` already
   self-reviewed for spec coverage, placeholders, and type consistency. Your pass asks what it
   cannot: does the task decomposition hold, does the spike's result still hold, is each task
   independently reviewable, and **is each task's verification falsifiable** — would it actually fail
   if the task were done wrong? On that last one, read each task's check and not just its
   instructions: does it test the real deliverable or a proxy that can pass while the requirement is
   missed; is it anchored by content rather than a line number the task's own earlier steps will
   shift; does it contradict the text it verifies? Revise inline.
6. **One diverse-model review of the plan** (see below). Fold findings; revise; recommit via `/commit`. (If a
   review at this stage invalidates the spec, loop back to step 1 as with spike
   invalidation.)
7. Present the committed spec + plan. Pause for the user's confirmation, then **execute and merge**
   (below). If the user declines or asks for changes, revise and re-present — never execute an
   unconfirmed plan.

#### Diverse-model review

The value is a **different model than the author** catching blind spots same-model review misses.
- **Pick a reviewer that differs from you (the author).** Prefer **Fable** (Agent-tool alias `fable`;
  underlying `claude-fable-5`) **when it is available** — *unless you are Fable, then use Opus.* **If
  Fable is unavailable** (when Anthropic has it disabled, the Agent returns a "Fable … currently
  unavailable" error), **explicitly fall back to Sonnet** (Opus only if you are Sonnet), and
  state which model actually reviewed (not Fable). Announce the choice.
- Spawn it with the **Agent tool**, `model:` = the chosen **alias** — `fable`, `sonnet`, or `opus`.
  **Never `haiku`:** critiquing a plan is design judgment, the top of the tier ladder
  ([agents/README.md](../../agents/README.md)), and a cheap reviewer here buys a cheap review of
  the one artifact the whole pipeline rests on. Use a skeptical critical-review prompt asking for
  prioritized findings
  (`[BLOCKER|MAJOR|MINOR] location — problem — fix`), passing the artifact path + relevant files.
- **Detect silent degradation:** ask the reviewer to state its exact model id as the first line of its
  reply; if it doesn't match what you requested, treat the pass as the fallback and say so. **Never
  block** — degrade and tell the user which model actually reviewed.

#### Execute and merge (default; both lanes)

With the plan reviewed and committed, **continue** (do not stop):
1. **Execute** the plan with `superpowers:subagent-driven-development` on the feature branch — fresh
   subagent per task, per-task spec+quality reviews, and a final whole-branch review. (When
   `writing-plans` offers to set up execution, this IS that execution.) **Make every task commit
   through `/commit`, in the foreground** — a signed-commit repo cannot sign inside a background
   subagent, so the controller commits each completed task via `/commit` (which applies the repo's
   per-commit semver tag) rather than relying on SDD subagents to `git commit`. Never bare `git
   commit`: it skips the tag and corrupts the release sequence.

   **Scale execution to Step 0's lane** — the triage governs rigor here too, not just the design half.
   **Scale by model tier, never by dropping a review.** SDD's per-task review and its final
   whole-branch review ask different questions of the same code (did this task build what its brief
   demanded, vs. does the branch hold together), and SDD names skipping either a hard *never* — even
   on a single-task plan, where they read the same diff through different rubrics. Honor its contract:
   the lane changes *what you pay per pass*, not *how many gates the code clears*.
   - **Always pass the model explicitly.** SDD inherits *your session's* model when none is given, so
     every implementer and reviewer silently runs at the session tier — usually the most capable and
     most expensive. That one omission costs more than every review in this pipeline combined, and it
     is invisible: nothing reports it.
   - Dispatch per the tier ladder in [agents/README.md](../../agents/README.md): **implementers** at
     the cheapest tier that fits (a task whose plan text carries the complete code is transcription →
     `haiku`; multi-file integration or judgment → `sonnet`); **task reviewers** at `sonnet`; the
     **final whole-branch review** at `opus` on the full lane, `sonnet` on the fast lane.
2. **Sweep the mechanics — `/audit` (always).** Run `/audit` over the repo before the merge and fix,
   via `/commit`, every `FAIL` **this branch introduced**. A repo may carry **pre-existing** FAILs the
   change never touched (generated content, a lint rule adopted after the fact): report those and
   leave them — do not loop chasing a clean sweep this branch cannot deliver, and never widen the
   change's scope to fix them here. **Decide which bucket a FAIL is in mechanically, never by
   impression:** it is pre-existing if it also fires with `/audit` on the base branch, or if its
   offending file is absent from `git diff <base>...HEAD`; otherwise this branch introduced it and it
   must be fixed. Every other gate in this pipeline is model judgment;
   this is the only deterministic one, and `superpowers:finishing-a-development-branch` verifies the
   test suite and nothing else — so without this a branch merges with a broken relative link, a lost
   exec bit, or drifted index tables, none of which a reviewer reliably catches and all of which
   `/audit` catches exactly. The per-edit hooks already cover most of it, but only for files touched
   via Edit/Write: a task that writes a file from a shell heredoc trips no hook, so this is the
   backstop. Read-only, deterministic, seconds — run it **first**, before the model gates below, so
   mechanical breakage fails fast instead of after a paid review. Relay any `SKIP` — each is a
   coverage gap, not a clean bill of health. **If `/audit` cannot run at all** — a non-zero exit with
   no verdict lines — do not read that as a pass: an empty sweep is not a clean sweep. "Say so and
   continue" is the *wrong* answer here, unlike steps 3 and 4 below: continuing past the pipeline's
   only deterministic gate is exactly merging on judgment alone. A usage error (a bad flag, a
   `--scope` that isn't a git repo) is yours to fix — correct the invocation and re-run. If it still
   cannot run, stop and report; do not merge.
3. **Vet the skills and agents the diff touches (conditional).** If the branch's diff touches
   `skills/*/SKILL.md` or `agents/*.md`, run **`/vet <paths>`** over them. Nothing else here knows the
   canonical skill/agent shape: SDD's reviewers judge the code as code and will pass a `SKILL.md`
   whose `name` contradicts its directory or whose frontmatter is malformed. `/vet` dispatches the
   reviewers that do. Fold findings (fixing via `/commit`) and re-run until clean; **verify each
   finding against the artifact and `STYLE.md`'s documented carve-outs before applying it** — the
   reviewers are advisory and do produce false positives. A finding you verified as a false positive
   and said so does **not** block "clean" if a re-run restates it — record the dismissal and move on.
   If `/vet` reports a reviewer as **failed or unavailable** instead of a verdict, say so and continue
   — never loop waiting on a verdict that cannot arrive. If the diff touches neither, skip and say so.
4. **Security-review the diff (conditional; full lane only).** If Step 0's triage flagged the change as
   touching **security or a fail-closed gate**, run **`/security-review`** (code-review plugin) over
   the branch's diff before finishing. This **complements — never replaces — the
   diverse-model review**: that one critiques the *design* (the plan, and on the full lane optionally
   the spec) before this code existed; this one inspects the *code that actually landed*, which is
   where security defects live. Fold any findings (fixing via `/commit`), re-run until clean, then
   continue. If `/security-review` reports that it could not complete instead of returning a verdict,
   say so and continue — as with `/vet`, never loop waiting on a verdict that cannot arrive. If
   triage did not flag security, skip it and say so — **every** fast-lane change skips here by
   construction, since Step 0 routes security to the full lane without exception. If security has
   surfaced *since* triage, the triage was wrong: re-triage per Step 0 rather than bolting this
   review onto a fast-lane change. Re-triaging here is **relabelling forward, not a rewind of the
   design phase**: it makes this `/security-review` mandatory (the gate the mis-triage skipped) and
   re-runs the **final whole-branch review at the full lane's `opus` tier** — a code-level review
   *can* be redone on finished code, and that is what makes re-triage more than bolting this review
   on. It does **not** retroactively owe the design-phase spec and spike (those inform design; the
   code is already written). A security finding this step's fold-and-re-run cannot resolve is a stop —
   never merge around it.
5. **Finish** with `superpowers:finishing-a-development-branch`: verify the project's test suite
   passes (if the repo has none, say so and rely on the per-task reviews), then
   **merge the feature branch** back to its base and clean up. The **merge is the default end
   action** — do not pause to choose it. If tests fail, stop and report; do not merge.

#### Stop at the plan (`--plan-only`)

When invoked with `--plan-only`, `/feature` **ends at the approved, committed plan.** Do not invoke
an execution skill — when `writing-plans` offers to set up execution, **decline it.** Tell the user
the plan is approved, the feature branch is left in place, and execution is a separate step — e.g.
`superpowers:executing-plans` or `superpowers:subagent-driven-development`.

### Rules

- **Default: execute then merge.** After the reviewed plan, run
  `superpowers:subagent-driven-development`, then `superpowers:finishing-a-development-branch`
  defaulting to a merge. **Only `--plan-only` stops at the plan** (then never implement; decline
  `writing-plans`' execution offer).
- **Invocation** — model-invocable; launch it (or let the user run `/feature`) only for changes
  that warrant methodical design, not trivial edits. A deliberate, scale-to-risk kickoff.
- Save spec/plan at the repo root (`specs/`, `plans/`, `YYYY-MM-DD-<name>.md`); commit each as produced via `/commit`.
- **Every commit the pipeline creates goes through `/commit`** (semver tag + `CONTRIBUTING.md`
  conventions), design-phase and implementation alike, run in the **foreground** for signing. This is
  the invariant that keeps the release sequence intact — never fall back to bare `git commit`.
- **Never merge on judgment alone.** `/audit` runs before every merge — it is the pipeline's only
  deterministic gate, and the model reviews do not substitute for it any more than it substitutes for
  them (`/audit` = the mechanical half, `/vet` and the reviews = the judgment half; a full review is
  both). It is read-only and near-free, so it is never the thing to cut for budget.
- **A change that *touches* a `SKILL.md` or an `agents/*.md` gets `/vet` before the merge** — editing
  an existing one counts, not just authoring a new one. The generic reviewers judge code as code; only
  `/vet`'s reviewers know the canonical skill/agent shape. Self-limiting — the condition never fires
  in a repo that has neither.
- **Security-flagged changes get `/security-review` before the merge**, reusing Step 0's own trigger
  — which means the full lane, always. It inspects the branch's diff — the diverse-model review only
  ever critiqued the design (the plan, and optionally the spec) before this code existed, so one
  never substitutes for the other.
- Scope the spike to one assumption; bias borderline triage to the full lane.
- Budget: the diverse-model agent pass — default to **one** (on the plan); ultrathink is cheap; the
  spike substitutes for a second reasoning pass; the fast lane skips the diverse pass unless stakes
  warrant it. The default execute-then-merge phase adds the SDD subagent passes (one implementer +
  reviews per task, plus the final whole-branch review), one `/security-review` pass when triage
  flagged security, and `/vet`'s reviewer dispatch when the diff touches a skill or agent (`/audit` is
  deterministic and near-free — excluded from this accounting, and never cut for budget); `--plan-only`
  skips all execution cost. **Execution spend is controlled by tier, not by cutting gates** — see
  "Scale execution to Step 0's lane"; never trade a review away to save budget.
- **Never spend two same-model passes on one artifact.** Each sub-skill (`brainstorming`,
  `writing-plans`) already self-reviews its own output against a mechanical checklist; the
  ultrathink steps own the altitude question those checklists cannot ask, and nothing else.
  Repeating a checklist doubles the pass that shares the author's blind spot — the reason the
  diverse-model review exists at all. Independence is what catches defects, not repetition.

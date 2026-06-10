---
name: feature
description: Run the methodical, risk-tiered design pipeline for a change (triage → spec → spike → plan → reviews) and stop at a reviewed plan ready to execute
disable-model-invocation: true
---

# /feature — Methodical Design Pipeline

Drive a change from idea to a **reviewed, committed implementation plan**, then stop and hand off to
a separate execution step. Orchestrates the `superpowers` skills and adds risk triage, an empirical
spike, and a diverse-model review. **Scale the rigor to the uncertainty.**

## Instructions

The user wants to design a change through the risk-tiered pipeline. Triage the work, run the
appropriate lane (orchestrating the `superpowers` skills), and stop at a reviewed, committed plan.
**Do not implement.**

### Arguments

`/feature <one-line description of the change>` — the description seeds the brainstorming. No flags.

### Process

#### Step 0 — Risk triage (always; state the verdict)

Judge the work's uncertainty. Choose the **full lane** if any hold:
- a novel mechanism or unfamiliar tool/library;
- an **empirical** unknown reasoning can't settle (e.g. "will this boot?", "does this API behave like X?");
- it touches security or a fail-closed gate;
- large blast radius (hard to reverse; many consumers).

Otherwise the **fast lane**. Announce the lane and why. Bias borderline cases to the full lane.

#### Fast lane (low uncertainty)

1. Use `superpowers:brainstorming` lightly to produce a **short combined spec+plan** (a few sentences
   of what/why + the task steps) — or just a short plan if there's no design to settle. Save to
   `plans/YYYY-MM-DD-<name>.md`; commit.
2. Do **one deep self-review** (ultrathink-level): placeholders, contradictions, missed steps, scope.
   Revise inline.
3. Run a diverse-model review **only if** it touches security / high stakes (see below); else skip.
4. Present for confirmation, then **hand off** (below).

#### Full lane (real unknowns)

1. **Spec.** Use `superpowers:brainstorming` to produce the spec. When brainstorming reaches its
   terminal "invoke `writing-plans`" step, **do NOT follow it** — return here to step 2 (you interpose
   the spike + reviews first). Save the spec to `specs/YYYY-MM-DD-<name>.md`; commit.
2. **Ultrathink the spec.** Deep self-review; revise inline.
3. **Spike the #1 risk.** Name the assumption whose failure invalidates the most downstream work. If
   it is **empirical** and its cheapest *decisive* signal is **cheap to probe**, run a throwaway,
   time-boxed probe now — probe the decisive signal ("does GRUB appear when a blank VM boots the
   ISO?"), not the whole thing ("does the 60-min install finish?"). Fold the result in (invalidated →
   adjust, maybe loop to step 1). If the top risk is empirical but **not** cheaply probeable, say so
   and rely on the plan's decision-gates instead. If it isn't empirical, say "no spike" and continue.
4. **Plan.** Use `superpowers:writing-plans`. Save to `plans/YYYY-MM-DD-<name>.md`; commit.
5. **Ultrathink the plan.** Deep self-review; revise inline.
6. **One diverse-model review of the plan** (see below). Fold findings; revise; recommit. (A second
   diverse review of the *spec* is opt-in for high-stakes designs — ask the user first.)
7. Present the committed spec + plan. **Stop at the user's confirmation**, then **hand off** (below).

#### Diverse-model review

The value is a **different model than the author** catching blind spots same-model review misses.
- **Pick a reviewer that differs from you (the author).** Through **2026-06-22 (inclusive)**, prefer
  **Fable** (Agent-tool alias `fable`; underlying `claude-fable-5`) — *unless you are Fable, then use
  Opus.* From **2026-06-23** on, or if a Fable agent is unavailable, use the strongest model that
  isn't the author: **Sonnet by default; Opus if you are Sonnet.** Announce the choice.
- Spawn it with the **Agent tool**, `model:` = the chosen **alias** (`fable` / `sonnet` / `opus` /
  `haiku`), and a skeptical critical-review prompt asking for prioritized findings
  (`[BLOCKER|MAJOR|MINOR] location — problem — fix`), passing the artifact path + relevant files.
- **Detect silent degradation:** ask the reviewer to state its exact model id as the first line of its
  reply; if it doesn't match what you requested, treat the pass as the fallback and say so. **Never
  block** — degrade and tell the user which model actually reviewed.

#### Hand off (both lanes)

`/feature` **ends at the approved plan.** Do **not** invoke an execution skill — and when
`writing-plans` offers to set up execution, **decline it.** Tell the user the plan is approved and
that execution is a separate step — e.g. `superpowers:executing-plans` (or `subagent-driven-development`),
which sets up the worktree and implements task-by-task.

### Rules

- **Stop at the reviewed plan.** Never implement or invoke an execution skill; decline `writing-plans`'
  execution offer.
- **User-only** invocation — a deliberate, side-effecting kickoff.
- Save spec/plan at the repo root (`specs/`, `plans/`, `YYYY-MM-DD-<name>.md`); commit each as produced.
- Time/scope-box the spike to one assumption; bias borderline triage to the full lane.
- Budget: the expensive move is the diverse-model agent pass — default to **one** (on the plan);
  ultrathink is cheap; the spike substitutes for a second reasoning pass; the fast lane skips the
  diverse pass unless stakes warrant it.

# Development Workflows

How work gets done: **scale the rigor to the uncertainty.** A trivial edit or a conversational answer
happens directly. Anything with real design, risk, or blast radius runs through the `/feature`
pipeline. The classic loops — Explore→Plan→Code→Commit and TDD — are the *primitives* that pipeline
orchestrates, and remain the right frame for lighter work.

## The spine: `/feature`

`/feature <description>` drives a change from idea to a merged result through a risk-tiered pipeline.
It orchestrates the `superpowers` skills and adds risk triage, an empirical spike, and a diverse-model
review. `--plan-only` stops at the reviewed, committed plan instead of executing.

**Step 0 — Risk triage.** Pick the lane and say why:

- **Full lane** if any hold: a novel mechanism or unfamiliar tool; an *empirical* unknown reasoning
  can't settle ("will it boot?", "does this API behave like X?"); it touches security or a fail-closed
  gate; or large blast radius (hard to reverse, many consumers). Bias borderline cases here.
- **Fast lane** otherwise.

**Full lane:** brainstorming → **spec** → ultrathink self-review → **spike the #1 risk** (a throwaway,
time-boxed probe of the one assumption whose failure invalidates the most downstream work — probe the
decisive signal, not the whole thing) → **writing-plans** → ultrathink → **diverse-model review** of
the plan → execute → merge.

**Fast lane:** a short combined spec+plan → one deep (ultrathink-level) self-review → a diverse review
only if it's high-stakes → execute → merge.

**Execute & merge (both lanes):** present the reviewed plan and **pause for confirmation**, then
`subagent-driven-development` runs it task-by-task — a fresh subagent per task with per-task and
whole-branch reviews. When triage flagged security — which means the full lane — `/security-review`
inspects the implemented diff before the merge. Then `finishing-a-development-branch` verifies the
suite passes and merges. If tests fail, stop and report; don't merge.

## The primitives

### Explore → Plan → Code → Commit
For lighter work, or *inside* a `/feature` task. **Explore** (read the relevant context, write no code
yet) → **Plan** (reach for a thinking trigger — `think` / `think hard` / `ultrathink` — for deeper
analysis) → **Code** (verify reasonableness as you go, work incrementally) → **Commit** (granular, via
`/commit`; see [CONTRIBUTING.md](CONTRIBUTING.md)). Separating research from coding is what stops
premature implementation.

### Test-Driven Development
The default for anything testable, and how `/feature` execution actually works:

1. Write the failing test from the expected behavior.
2. Run it — confirm it fails for the right reason (**RED**).
3. Write the minimal implementation.
4. Run — confirm it passes (**GREEN**). Commit via `/commit`.

Don't let the implementation edit the tests. A change to a gate or hook should land with its failing
test first — the check that would have caught the regression.

### Bug found → regression test first

Every bug gets a regression test **before** the fix, wherever it was found — by a user, a review, or
mid-task. Reproduce the bug as a failing test (RED), fix it, watch the test pass (GREEN), and keep
the test forever; the failing run is the proof the test actually catches the bug, and the passing
run is the proof it's fixed.

Skipping is a flagged exception, never the quiet default: if a regression test is impractical
(timing-dependent, environment-specific, interactive-only), say so explicitly at fix time and record
why alongside the fix — in the commit message or a code comment.

## Cross-cutting disciplines

- **Diverse-model review.** A *different* model than the author catches blind spots that same-model
  review glosses over. `/feature` bakes this into the plan review; use it for any high-stakes artifact.
  **Verify the reviewer's model — don't assume it.** The `model:` override can silently fall back to
  the author's own model, and a *stated* id can even be false; treat any review that won't confirm its
  real id under a direct re-ask as same-model and **void**. When working autonomously, a
  confirmed-diverse reviewer can serve as the deciding vote on a fork rather than deciding solo.
- **Subagents for breadth.** Delegate exploration and independent, parallel work to subagents to keep
  the main context focused — then use their conclusions instead of redoing the search.
- **Verify before claiming done.** Evidence before assertions: run the check and show the output. If
  tests fail, say so; if a step was skipped, say that.
- **Granular commits, via `/commit`.** One logical change per commit —
  [CONTRIBUTING.md](CONTRIBUTING.md) holds the conventions; `/commit` applies them and adds the
  semver tag and changelog entry. Run it in the **foreground**: a signed repo can't sign inside a
  background subagent, and bare `git commit` skips the tag, corrupting the release sequence. The
  pipeline commits per task, tagging each. `/recast` is the one exception — it owns its own
  per-brick commit/tag/changelog discipline.

## Choosing the approach

| Situation | Approach |
|---|---|
| Trivial edit, or a conversational answer | Direct — no pipeline |
| Testable change with clear input/output | TDD (RED→GREEN→commit) |
| Fixing a discovered bug | Regression test first — failing test, then the fix |
| Real design, risk, or blast radius | `/feature` — full lane |
| Well-scoped, low-uncertainty change | `/feature` — fast lane |
| Want a reviewed plan but not execution | `/feature --plan-only` |

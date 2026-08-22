# Global Claude Code Instructions

Universal instructions for all projects.

- **Code style and formatting:** [STYLE.md](./STYLE.md)
- **Code templates:** [templates.md](./templates.md) (Bash, Python, JavaScript)
- **Development workflows:** [workflows.md](./workflows.md) (the `/feature` pipeline; Explore/Plan/Code/Commit and TDD as primitives)
- **Contributing conventions:** [CONTRIBUTING.md](./CONTRIBUTING.md) (commit messages, semantic versioning)

> Auto-generated index tables sit between `<!-- sync:* -->` markers — don't hand-edit them; update the source and run `/sync-docs`.

> **Pushing is explicit-only.** Never `git push` to any remote unless the user has authorized *this*
> push — publishing is a deliberate, per-push decision. The `push-guard` hook enforces it (a bare
> `git push` is blocked; lead the command with `ALLOW_PUSH=1` only on explicit authorization).
> `/propagate` promotes to production locally by default; `/propagate --push` publishes to `origin`.

> **Rewriting published history never unpublishes.** Old commits stay reachable by SHA on the host
> and in every existing clone or fork; and any **tag** still pointing at them keeps them fully
> browsable, so a branch force-push that leaves tags behind removes nothing. Delete those tags as
> part of the rewrite, and describe the result as not-current — never as erased. **Then sweep every
> clone you control, not just the one you rewrote** — a second checkout keeps the old commits alive
> through its own stale tags *and* a stale local branch. Verify by asking which refs still **contain**
> the commit, never which tags you deleted.

> **Run `/feature` for anything with design content — its step-0 triage IS the right-sizing
> mechanism.** The pipeline is risk-tiered and its fast lane keeps a small change cheap, so judging a
> change "too small to need it" substitutes your triage for the pipeline's, silently, at the moment
> you can least see your own blind spot. Don't pre-judge the tier in the arguments — let triage
> announce the lane. Measured: a "bounded one-function fix" came back **full lane on stakes**, and the
> plan review then returned a BLOCKER: the design re-created the defect it was fixing, and the
> author's own fixture passed and hid it. A skipped pipeline reads exactly like one that found nothing.

> **Bugs get a regression test first.** When a bug is found, reproduce it as a failing test *before*
> fixing it (RED→GREEN; see [workflows.md](./workflows.md)). Skipping is a flagged exception — state
> why at fix time (e.g. untestable: timing/environment/interactive), never skip silently.

> **Never reason your way past the last gate before something irreversible.** Publishing, deploying,
> force-pushing, or a destructive migration is where the gate you can argue is redundant — *"the suite
> already passed on an identical tree"* — is the one to actually run. That argument is usually right,
> which is what makes skipping it a habit, and a skipped gate is indistinguishable from no gate.
> **And a gate that FAILED is cleared by a re-run only if the subject was pinned and verified
> unchanged across both runs** — a fixed subject cannot host a non-deterministic defect, so a failure
> that will not reproduce is then a fact about the instrument. Without that verification, "re-run
> until green" is laundering; state the stopping rule *before* the re-run, not after reading it.

> **Never state a time of day** ("this morning", "tonight") unless you just read the clock (`date`) —
> name the trigger or step instead ("the push step", "next session"). Don't echo a time word from
> earlier in the conversation; time has moved since.

## Asking Questions

**Default to deciding.** Make the call, state the reasoning and the assumption you made, and keep
going — a wrong-but-stated assumption is cheap to correct; a stalled turn is not.

**When a question does feel genuinely open, put it to a Fable subagent before putting it to the
user** (Agent tool, `model: fable` — a diverse model when you are Opus). Ask it both halves: to
answer the question on the merits, *and* to judge whether this is really the user's call. Bring back
the conclusion and its reasoning — never a menu of options for the user to arbitrate.

**The list below is what SURVIVES Fable, never what SKIPS it** — judging whether to ask is one of
Fable's two jobs, so pre-classifying a question as the user's routes around the only check on that
classification, which is the least trustworthy step since over-asking is the failure mode.
Authorization alone is a true exception: it is not a question about the merits, so there is nothing
for Fable to weigh. **Only then, ask the user directly for:** authorization (anything outward-facing
or hard to reverse), risk appetite they own (how strict a gate should be, what false-block rate is
acceptable), the scope they are paying for, and anything where proceeding wrongly would be unsafe or
waste substantial work. Batch whatever survives — ask once, at a natural checkpoint, not as each
item arises.

## Delegating

**Dispatch subagents freely — the standing preference is MORE of them, not fewer**, and no
per-dispatch permission is needed. Once a plan is reviewed, delegate execution by default; keep only
what is genuinely yours — verifying each returned result, foreground commits, and decisions the user
owns. Never give your own remaining context as a reason to stop; the real ceiling is the DEPENDENCY
GRAPH (disjoint file sets parallelize; two tasks rewriting one function do not), which is checkable,
where "low on context" is not.

## Skills, agents, hooks, and plugins

**[~/.claude/README.md](./README.md) carries the full generated indexes** — every skill, agent,
hook, and plugin, with purposes; plugins are enabled in [settings.json](./settings.json). That is
the single index. This file keeps only what you would need *before* you thought to look it up.

These skills carry `disable-model-invocation: true`, so they are **user-invoked only** and absent
from the harness's injected skill list — and the moment you would want one is the moment you might
otherwise hand-roll what it already does:

<!-- sync:skills cols=Command:key,Purpose:auto filter=disable-model-invocation:true -->
| Command      | Purpose                                                                                                                                                                 |
| :----------- | :---------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `/debrief`   | Run the end-of-session pre-compaction routine (deferral follow-up, CLAUDE.md refresh, memory save, automation review, and deferred design)                              |
| `/propagate` | Promote committed changes from this dev working copy to the live ~/.claude repo locally; --push also publishes to origin (explicit)                                     |
| `/recast`    | Re-develop a git source repo into a target as a genuine ground-up, proven-per-commit history converging to functional equivalence (never copies the tree, never pushes) |
<!-- /sync:skills -->

**Non-skills earn a place here on the same test.** Before hand-writing a mutation harness — which
the RED→GREEN rule below sends you to do — use `~/.claude/scripts/lib/mutate.py`, supplying only
the `(label, old, new)` list. Ten were hand-rolled and discarded before it existed, each
re-derivation dropping a different safety property; worst was the unmutated BASELINE, absent from 7
of 8 — without it an already-red suite scores every mutation CAUGHT and the sweep reads flawless.
And before backgrounding a check that outruns the tool timeout, use `~/.claude/scripts/run-long.sh`
rather than a wrapper of your own — it is the shipped remedy for the killed-run and
graded-the-launch-tree hazards below, and you read its verdict back with `--status`, never from the
launch. Four wrappers were hand-written in one session before it existed, three byte-equivalent;
the fourth still let a sweep that had run 4 checks of 15 read as a clean pass. Give it `--expect
'<verdict regex>'` at LAUNCH too — the pattern is recorded INTO the artifact, so a run that
finished having executed nothing reports INDETERMINATE instead of a truthful, useless `DONE rc=0`.
Measured: exactly that happened, and only an absent verdict line caught it.

> Hooks (indexed in README.md) fire per-edit: a multi-step change that passes through an invalid
> intermediate state (e.g. resolving conflict markers with two Edits) trips transient PostToolUse
> errors — verify the final file state instead of reacting to the mid-sequence report, or make it
> one edit.

### Superpowers plan/spec location (override)

The superpowers skills hardcode `docs/superpowers/plans/` and `docs/superpowers/specs/`.
Override that in every repo: save **plans** to `plans/` and design **specs** to `specs/`
at the repo root, dropping the `docs/superpowers/` prefix. Keep the `YYYY-MM-DD-<name>.md`
filename convention. When a skill (writing-plans, brainstorming, subagent-driven-development,
requesting-code-review, executing-plans) reads or writes a plan/spec, use these paths instead.

### Superpowers SDD: the progress ledger has no plan identity

`subagent-driven-development`'s `.superpowers/sdd/progress.md` records `Task N: complete` with **no
reference to which plan** — yet the skill says to trust it over your own recollection. A ledger left
by a *previous* plan therefore reads as if this plan's tasks are already done. Confirm it names the
plan and base you are actually executing before trusting any line; reset it when starting a new plan.

## Environment

- **Platform**: macOS with MacPorts package manager
- **Editor**: BBEdit (primary code editor)
- **Shell**: Prefer MacPorts bash (`/opt/local/bin/bash`) for scripts requiring advanced features
- **Default bash**: `/bin/bash` is the system bash (version 3.x, limited features)
- **GNU Core Utilities**: Installed via MacPorts (`coreutils`)
  - **`/opt/local/libexec/gnubin` is already on PATH, so the UNPREFIXED names are GNU** — plain
    `date`, `grep`, `sed`, `ls` are GNU, not BSD. Measured: BSD-only flags fail there
    (`date -j` → `invalid option -- 'j'`), and reaching for `gdate` to "get GNU" is a no-op.
  - The `g`-prefixed names (`gls`, `ggrep`, `gdate`) still resolve, so both spellings work
  - Use GNU versions for advanced features like `--long-options`

## Language and Tooling Preferences

- **Preferred**: Unix tools orchestrated through Bash scripts
- **Secondary**: Python for complex tasks requiring rich libraries
- Favor command-line tools and shell scripts over GUI methods
- Use Python when Bash becomes unwieldy or complex data structures are needed

### Verification hazards — instruments that read as verified while proving nothing

When a check reads clean, work through the groups below in order before trusting it. A hazard with
no measured instance does not belong here at all, and **one bullet carries one mechanism** — plus
that remedy's own failure mode, needed at the same moment; a *different* mechanism sharing only a
topic word gets its own bullet. A group splits once it passes about four members, and **that cap is
a SPLIT trigger, not a rejection**: an earned hazard arriving at a full group splits it, and is
never welded onto an existing bullet as a clause. Measured — read as a rejection, the cap grew this
section from 88 lines to 177 with every group count frozen, a clause being the only landing spot it
left legal.

#### Nothing ever ran — silence is not a pass

- **A killed run never ran — absence of a verdict is not a pass.** A check stopped by a timeout
  (SIGTERM, rc 143) prints a *prefix* of `PASS` lines, never emits one for the check still in flight,
  and never reaches its summary — so grepping for `FAIL` finds nothing and the output reads clean.
  Require the specific verdict line **and** the summary to be *present*; "no FAIL" is not "passed".
  Record the real exit status **inside** the artifact you will read, so its **absence** is itself the
  signal that the run died — the harness's announcement can't be trusted, since it reports the
  wrapper's exit status rather than the check's.
- **A check that is merely INSTALLED has never run — registration is not liveness.** Seen: a config
  restore put back a runtime file lacking the three hook registrations the incoming commit added — 21
  entries where the commit had 23 — while the obvious diff reported *clean*; the gate was then
  unprovable until a reload. Watch it fire once, against a target you can afford to have it miss.
- **A comparison whose two sides are resolved differently can never match, so the guard silently
  never fires.** Resolve both sides the same way before comparing two paths — measured, in shipped
  code: a containment guard took `pwd` on one side and `cd -P` on the other, so no input could have
  tripped it.
- **A delegate that returns no verdict has not reported — verify from the artifact it left, never
  from its silence.** Measured: an implementer's entire final message was *"I'll stop polling now"*.
  Its work was there and turned out correct, and the harness announced the task **completed** — so
  nothing distinguished it from a finished report. Note what does NOT rescue you here: recording an
  exit status inside the artifact, the usual remedy for a run that dies, needs an rc and an artifact
  you control — and a delegate that completes normally gives you **neither**. The only remedy is to
  re-derive the whole verification yourself, against the same baseline you would have demanded of
  your own work. That it was correct is not a reason to have trusted it.

#### You chose not to run it — the excuse is checkable

- **A check that never fired never ran, either.** Editing outside your tooling's normal path skips its
  hooks silently — they do not fail, they never run — and a hand-substitute is reliably narrower than
  what it replaced. Name what you skipped and run it, or use the normal path.
- **When the reason you skip is *"that change class cannot reach it"*, that is a claim about the
  DEPENDENCY GRAPH — one grep settles it,** cheaper than the gate you were skipping. Measured: a
  CLAUDE.md prose edit, argued in two consecutive sessions to be unable to reach any suite — one grep
  then found a suite that reads CLAUDE.md and asserts on the very marker region the edit sat beside.
- **A command you write into documentation is unverified until you run it.** An un-runnable one reads
  exactly like a working one, so prose review never catches it — only execution does. Seen: a flag
  rejecting the arity it was given (`git check-ignore -q a b` → `fatal: --quiet is only valid with a
  single pathname`), and a snippet its own guard blocks. Run every documented command once **as
  written**; when one needs a hand workaround twice, the doc is the defect, not the workaround.
- **"This tool covers that job" is a claim you have not run — one trial settles it.** An instrument
  answers ONE question, and a job needing a different question is uncovered however alike the two
  sound. Measured: a *losslessness* checker (did anything leave?) was twice named, in consecutive
  turns, as what unblocked a *compression* pass — where content leaves by design. Run against the
  real job at last, it returned FAIL with 130 words removed for 7 lines saved: an unreadable bag,
  the very hand-accounting it was meant to replace. Named the enabler twice, never once run.

#### It ran, but not on what you think

- **Before believing a probe's verdict — FAIL *or* PASS — confirm it REACHED the subject at all.**
  An unresolvable shell variable produces both errors, since a tool sees the command text
  *unexpanded*: one gate blocked on the literal path (a FAIL about nothing), another allowed because
  the lookup keyed on it came back empty (a PASS about nothing). A shell with no TTY does it too — a
  card-backed key cannot prompt for its PIN, so the agent REFUSES, byte-identical to a rejected
  credential; "auth is down, go fix the card" was reported for a card that was present and unlocked.
  Both measured. The clean run is the dangerous one — nobody investigates it.
- **A probe that DID reach a subject may have reached the WRONG one — and then *did it reach the
  subject* CLEARS it, which is exactly what hides it.** Ask instead whether the thing that answered
  is the thing that matters. Measured twice, both truthful answers about the wrong subject. A file
  flagged `skip-worktree` reads as clean in `git status` while differing from the commit, so a
  "before" digest reconstructed from committed content was wrong and a real postcondition checker
  returned a FAIL that was purely the input — the tool ran, and answered honestly about what it had
  been told to see. And an agent shell can wrap a standard command in an unexported shell FUNCTION,
  so a script child resolves the real binary and disagrees: a version probe reported the wrapper,
  and a TRUE documented fact went into a durable record as false. **Two resolution probes
  disagreeing — a bare name versus a path — was the only tell.**
- **A change that is only correct in COMBINATION is one unit of work.** Two halves of a fix can be
  individually wrong in *opposite* directions — one alone over-blocks, the other alone lets the bug
  through — so landing half is not partial progress, it is a regression. And it is one no suite can
  catch: every test passes at both commits, because the broken state exists only *between* them. Seen:
  a filter and the flag that makes it safe, split across two tasks; the interval shipped the
  over-blocking half and broke a real workflow while three suites stayed green. Ship them together, or
  say plainly that the interval is broken and why.
- **A liveness reading is a SAMPLE, not a state — "still running" goes stale exactly like the
  verdict it stands in for.** The job may have ended normally, written its verdict and exited, while
  you go on quoting a status taken minutes or hours earlier. Measured three times in one session: a
  backgrounded check called still-running long after it had finished — once masking a FAIL for
  several minutes, once masking a PASS for over an hour — and each time the operator, not the
  reporter, noticed. The instrument was blameless throughout: its status query and its artifact both
  answered truthfully whenever asked. **Re-read before every claim about liveness**; "it is still
  going" is a measurement carrying a timestamp, never remembered state.

#### A second run proves less than it looks like

- **A second instrument AGREEING is not a second measurement when it inherits the same harness —
  so "independently corroborated" can mean one broken probe run twice.** The environment lies
  identically to everyone who reaches for the obvious probe, so agreement is produced BY the defect
  rather than despite it. Measured: a defect recorded as *confirmed by measurement* and later
  *independently corroborated by a different reviewer* did not exist — both had run the probe in a
  default shell instead of the file's own `set -uo pipefail`, which inverts the answer. The
  corroboration was logged as *raising confidence that it is real*, and its prescribed repair would
  have replaced a working guard with a dead one. **Ask what the second run VARIED, never that it
  agreed** — if it reused the harness, the axis that could be wrong was never tested twice.
- **A DIFFERENCE between two runs is about their subjects only if the instrument is deterministic —
  and one sample per side cannot establish that.** The mirror of the bullet above: there, agreement
  is manufactured by a shared defect; here DISAGREEMENT is manufactured by a noisy oracle and read
  as signal. Measured: a verification harness scored 30/30 on one pinned tree and 34/35 on another,
  and a 2×2 of four ~20-minute runs concluded the change had introduced the regression, blocking a
  correct change for a day. The oracle was flaky at ~5–10%, so both cells were single draws from
  one distribution; repeating the one named check 20 times per side settled it in under a minute —
  2/20 against 1/20. Everything else was RIGHT, which is what hides it: both subjects pinned, the
  harness held. **And "it reproduced" is not a rate** — the surprising cell was confirmed twice,
  back to back, which replicates the machine's state as readily as the subject's. Measure each
  side's RATE before believing the gap.

- **Two runs that DISAGREE may have been two different instruments — one that discovers its
  configuration from the WORKING DIRECTORY varies silently, even when you named each target by
  ABSOLUTE PATH.** The third leg of the two above: here both subjects were pinned AND the
  instrument was deterministic, and each side still answered a different question. Measured: one
  file, byte-identical by digest across three repositories, read clean in two and returned three
  findings in the third — the checker had resolved its ruleset from the directory the shell sat
  in, not from the file it was handed. The absolute path is what made the working directory feel
  irrelevant, and the fake clean side pointed the investigation away from a defect that in fact
  reproduced everywhere. Note what does NOT rescue you: each side is perfectly reproducible, so
  **the remedy directly above — measure each side's RATE — returns the same wrong answer twenty
  times.** Re-run each side from inside its own subject, or make the tool report the configuration
  it actually loaded.

#### It ran and could never have failed

- **A regression test that never reaches the defect passes for free — watch it FAIL before you trust
  its PASS.** The fixture's environment is part of the subject: `mktemp -d` under a symlinked
  `$TMPDIR` (`/tmp` → `/private/tmp`) yields a *logical* path that does not physically contain the
  file, and a tool resolving paths can take a different branch there and never reach the bug. Seen: a
  cwd-resolution fix whose test passed identically with and without it, until the sandbox was pinned
  with `pwd -P`; nothing in the green output hinted at it, since a correct fix produces the same
  green. RED→GREEN is not ceremony — the RED is the only evidence the fixture reaches what you fixed.
  **But a RED proves only that SOMETHING failed, not that your named subject did** — a row titled for
  one guard fired off a *different* assertion that raised first, so deleting the guard it named left
  the suite green (measured). Mutate what a row names; if the suite holds, the row is not testing it.
- **A test that supplies the option's own DEFAULT cannot tell whether the option is read at all.**
  Measured by mutation: five separate "parse the flag, then discard its value" mutants ALL SURVIVED
  a green suite, because every test invoked the script with the value the script would have chosen
  anyway. The worst asserted *"the reload command must be carried through, not silently dropped"*
  while grepping for that default — and it was the one assertion that would have caught a real
  defect shipping in that exact field. Pass a value that DIFFERS from the default, assert on that
  distinctive value, then delete the option's plumbing and watch the row go red.

#### Every row got the same verdict, whatever it contained

- **When every row of a probe shares a condition some EARLIER rule already decides, the sheet
  answers a question it never asked — and reads as "no work needed".** Not the reached-the-subject
  hazard above: the probe does reach it, and a rule upstream of the mechanism then disposes of every
  row before that mechanism is ever consulted. Measured: a probe of a config-injection detector
  paired each injection with the one input an earlier rule already refused, came back *blocked* on
  every row, and was written up as "already covered — the plan's premise is wrong". The real hole
  appeared only once the paired input was varied to one that rule ALLOWS. Vary the dimension you are
  actually testing, and require at least one row whose verdict would MOVE if the mechanism were
  deleted — a sheet where nothing moves is measuring the rule above it.
- **A comparison against another party's OUTPUT FORMAT can be unsatisfiable — and gating an *allow*
  on it builds a rubber stamp, not a dead guard.** Not two sides you resolved differently yourself,
  where normalising is the fix: you do not own both producers, so the move is to SAMPLE what each
  actually emits before comparing them. Measured: an exemption became available when every file a
  report named was absent from a diff's file list — but the reporting party's contract was
  `file:line` and its harness separately demanded absolute paths, while the diff listed bare
  relative ones. Nothing could ever match, so every item read as absent and every item took the
  exemption, on files the change was actively editing. It ran on each item and returned a verdict
  each time, so *did it reach the subject* clears it.
- **One defect can have a LOUD shape and a SILENT one, and a fixture carrying BOTH exercises only
  the loud one.** Reading aborts at the first thing that raises, so every row dies from the
  identical exception and the silent shape — the one that CORRUPTS rather than stops — is never
  reached. Measured: a text collision that raised loudly when it occurred mid-line, and at
  end-of-line parsed cleanly while fabricating an extra entry and inflating a count; the combined
  fixture went red three times for one reason, and the silent half was pinned only by an ad-hoc
  check nobody would ever re-run. **Give the silent shape its OWN fixture**, where nothing raises.
  Then note what that fixture's *did not raise* row looks like: **green before AND after the fix —
  normally the signature of a vacuous test, and here the CONTROL** proving the failure is silent.
  The discriminator is what the row CLAIMS: one claiming to pin the change is condemned by staying
  green, while one asserting a property the change does not alter is green by construction — and it
  earns its keep only by licensing sibling assertions that DO move.

#### Right verdict, then the world moved under it

- **A checker that resolves its helpers relative to itself grades your branch with the OLD tools.**
  So a change *to* the tooling is judged by the copy it replaces — the verdict is **true**, just
  about a different question than you asked, and it reads GREEN whenever the installed copy is the
  laxer one. Seen: one sweep gave `FAIL … drift` from the installed copy and `PASS rc=0` from the
  branch's own, minutes apart, both correct. Run the change's own tools, and name which copy
  produced the verdict.
- **A long-running check grades the tree as of its LAUNCH, not as of when you read the verdict** —
  the stale-helpers hazard's mirror, with the tool current and the *subject* stale. Every edit
  made while it runs is unjudged, and nothing in the output says which tree it saw, so the PASS you
  read at the end is byte-identical to one covering your final state. Measured twice in one session:
  a ~15-minute sweep whose fast static checks finish in the first seconds, read as clean over files
  edited minutes later. Freeze the tree for the run, or re-run afterwards and name the tree each
  verdict covers. **Re-running only the cheap subset is the trap** — that asserts the skipped checks
  could not have been reached, which is the dependency-graph excuse, and it owes the same grep.
  **The run may even predate your SESSION** — a backgrounded job survives the exit while the waiter
  that would have notified you does not, so the next session edits inside a window it cannot see and
  reads the failure as pollution rather than its own doing. Look for a live run before editing.
- **A STORED STATUS is a measurement carrying a timestamp, not a standing fact — re-derive it
  before you relay it.** A note recording that something is *not yet done* was true when written and
  reads exactly like a live reading, so nothing about it looks stale; and unlike a stale tool or
  tree, no run is involved that you might think to repeat. Measured twice in one session, both
  stated to the operator as current fact, both one command from being checked: a count of
  outstanding items, quoted across several turns from a six-day-old note, was wrong by more than
  half; and an item recorded as *still open, decide before the irreversible step* described a step
  that had ALREADY happened — so the advice was not merely stale, it counselled acting inside a
  window that no longer existed. **Re-derive any state you are about to act or advise on from the
  source that owns it**; a record's own status line is never that source, and the older the record
  the more authoritative it reads. **Write-side too: clear a status the moment its condition
  clears** — the one moment nobody thinks to. Measured: a record still saying BLOCKED sent the
  operator to redo finished work; stale *blocked* survives because it reads as caution.

#### Right verdict, wrong population or parameters

- **A tool that IGNORES an argument it cannot parse answers with its OWN defaults, and the run looks
  normal.** Neither refusal nor crash: the wrong-shaped input is discarded unread and the tool falls
  back to what it discovers for itself, so the verdict is **true** about a configuration you never
  chose. Measured: a config supplied in the wrong schema was ignored, the tool's own discovered copy
  decided the run, and it reported **0 findings against a true 20** — confident enough to be quoted
  out loud before anyone questioned it. Note what does not catch this: the probe really did reach the
  subject, so "did it run on the right thing" clears it. Echo back the parameters the tool reports
  using, or check that the verdict MOVES when you deliberately change them.
- **An instrument that enumerates its subjects from VERSION CONTROL grades the COMMITTED
  population, not the one in your working tree — so the newest work sits outside its scope at
  exactly the moment nothing has ever checked it.** Measured: a checker discovering subjects with
  `git ls-files` reported `PASS … campaigns=6` where seven existed, silently skipping the one
  written minutes earlier; `git add` alone made it seven. Note what clears it and should not — the
  denominator is non-zero, and a declared FLOOR cannot name a member that did not exist when the
  floor was written, so both of the usual under-coverage remedies pass. Compare the enumerated
  count against the working tree and name what the predicate excluded; **do not just switch to a
  filesystem glob**, which then grades artifacts the commit will never contain.

- **A checker that hard-codes ONE location, where the tool it checks SEARCHES A LIST, grades a
  directory nobody is using — and reports "not configured yet", which is exactly what stops anyone
  looking.** Measured twice in one session, one level apart. A guard over a client's root-sourced
  config tree hard-coded `~/.getssl`; the client searches `/etc/getssl`, two directories beside its
  own binary, then `$HOME/.getssl`, and takes the FIRST holding a config — so on a host configured
  at `/etc/getssl` the guard found nothing, verdicted PASS-WITH-GAPS, and never looked at the tree
  actually sourced as root. Resolving the same list fixed it; the same shape then reappeared one
  level DOWN, because a documented key *inside the file just resolved* relocates the per-domain
  configs elsewhere again. **Resolve the subject the way the tool resolves it — including keys the
  tool reads out of the file you resolved** — and make "found nothing" name WHERE it looked.

#### Your matcher matched text you did not mean — or missed text you did

- **Multi-line literal checks are a case for Python.** `grep -F` treats an embedded newline as
  *alternation*, not a sequence: `grep -Fc "$(printf 'a\nb')"` counts lines matching **either**, so a
  multi-line check returns a plausible-but-wrong count and reads as verified. Use `python3 -c "..."`
  (`needle in open(f).read()`) or `grep -Pzo`.
- **A phrase you believe is one line may have WRAPPED — then a line-based grep returns 0, and
  absence is not evidence of absence.** Match against the file's whole text, not line by line.
- **A property can arrive by INDIRECTION, so its absence from a file's TEXT is not its absence in
  EFFECT.** Matching the whole text does not rescue you here, because the setting really is not in
  the file — it is supplied by an imported or included module one hop away. Measured twice in one
  session, independently: a queued note called a file defective because it contained no occurrence
  of the setting, and a re-check of that claim reproduced the identical error before either was
  run — the file imported a helper that had passed the setting on every call since the day it was
  written. Grep the CLOSURE rather than the node, or assert the property's effect rather than its
  text.
- **A CONVERTER can silently drop most of a document and still exit 0, so you search a SUBSET that
  reads as the whole.** Neither wrapping nor indirection: the text never reached you at all, and the
  extraction reported success. Measured: `pdftotext -layout` over a printed code review returned 372
  plausible lines with every inline `code` span intact and **every prose paragraph absent** — the
  body font carried no usable ToUnicode map and the monospace font did. Nothing looked broken, only
  sparse, so a summary built from it would have named the right files while missing every reviewer's
  actual point; rendering the pages to images recovered all of it. **Check the extraction's YIELD
  against the source before trusting any search over it** — not by size or line count, which is what
  a genuinely sparse document also produces, but by confirming one specific passage you can see with
  your own eyes survived the conversion.

#### The exit status you read is not the verdict

- **A pipeline's exit status is the LAST command's.** `some-check | tail -20` reports `tail`'s success
  however the check exited — so a run that "completed (exit code 0)" can have proven nothing, and a
  backgrounded one reads as a clean pass. Read the tool's own verdict/summary lines rather than the
  rc, or don't pipe it (`set -o pipefail`, or `${PIPESTATUS[i]}`, when you must) — and index that by
  POSITION: `[0]` is the FIRST stage, so in `printf … | tool | tail` it reports the *printf*, and a
  tool that exited 2 reads as 0. Measured, on a gate that had correctly blocked.
- **A script, function, or `{ … }` wrapper exits with its last command's status too** — so a
  diagnostic `echo` appended after an assertion discards the verdict it was meant to report, and the
  check reports the *echo's* success. Capture `rc=$?` on the very next line, then `exit "$rc"`. The
  harness will otherwise announce "completed (exit code 0)" for a run that was killed.
- **`set -o pipefail` makes an early-exiting READER report its PRODUCER's death as the pipeline's
  verdict — so the remedy in the bullet above becomes a defect one line later.** `grep -q` exits at
  the first match, the producer is then killed by SIGPIPE (141), and `pipefail` returns 141 for a
  pipeline whose grep MATCHED: `printf '%s' "$hay" | grep -qF "$needle"` is false precisely when the
  needle is PRESENT. Measured in shipped code — a trust-store guard reported an anchor ABSENT while
  the same run validated a certificate chain against that very bundle; `PIPESTATUS=[141,0]`, and
  40/40 false FAILs at a 26KB payload against 0/40 for the pipe-free form. It is SIZE-DEPENDENT, so
  it hides from its own regression test: the identical code reproduced 0/50 at 1.2KB, where the
  producer finishes before the reader exits. Use `case "$var" in *needle*)` or a `<<<` herestring —
  anything with no live producer to signal. `grep -q`, `grep -m1` and `head` carry it; `tr`, `wc`
  and `grep -c` consume their input and do not.

- **A helper that returns 0 for a GOOD verdict makes `cmd || exit 1` a no-op, and the early return
  then falls through into code that was never meant to run.** Measured: a check script's
  not-yet-configured branch ended `pr_summary … || exit 1`, intending to stop there. That helper
  returns 0 for PASS *and* for PASS-WITH-GAPS, so on a healthy run the `||` arm never fired and
  execution continued into rows that read a path which did not exist, reporting it as `owned by ,
  not by root`. It had only ever appeared to work because an earlier draft of that branch measured
  NOTHING, so the zero-denominator rule failed the run and exited 1 — **the structure was wrong the
  whole time and a wrong verdict was hiding it**, and fixing the first defect is what exposed the
  second. Terminate explicitly (`pr_summary; exit $?`), and treat an early-return branch as unproven
  until you have watched it take the PASSING path.

#### The signal you read belongs to something else

- **Equal COUNTS are not equal sets** — two collections can match in size while differing in both
  directions at once. Measured: a runtime config and the commit it was restored from each held 24
  hook entries, which a tally reads as agreement, while the runtime carried a machine-local extra
  *and* was missing a gate the commit added. Compare the one-directional difference you actually
  care about, never the tally.
- **A truncated output keeps the wrong half — suites print passes as they go, so the FAILURES sort
  last and the cap eats exactly them.** Measured: a sweep reported `FAIL` naming one suite, then
  printed 50 of that suite's `PASS` rows and `… more`, cutting off before the row that failed; the
  failure never reproduced, so the diagnosis is now unrecoverable. Output caps are written for a
  list of like-for-like offenders, where the first 50 are representative — but when the thing being
  capped is another tool's whole stdout, those are the least informative lines it produced. Filter to
  failure-shaped lines before capping, or write the full output to an artifact whose destination
  sits outside everything else's clean-tree precondition.

#### The fix it prescribes is not the defect it found

- **A check's output is evidence, not instruction.** Its *verdict* is usually right; its *suggested
  repair*, and your reading of a *failure*, are not. Seen: an exec-bit check reporting "has a shebang
  but committed 100644 — chmod +x" for a module that is only ever imported, where the correct fix was
  the opposite — delete the shebang; obeying the message would have made a library executable.
- **A deferred item's prescribed FIX is a past self's hypothesis; only its defect is a finding.**
  Re-measure before building what an earlier session queued. Seen: an entry read "implement
  `filter=` in the other five handlers" — measuring first showed the gap was never `filter`-specific:
  nothing validated *any* directive against its handler, so a *documented* one was ignored by six of
  seven. One declared allowlist closed every case plus future typos; building the entry as written
  would have left the larger hole open and added five more places to forget. **The brief you write for
  someone else is the same hypothesis** — measured: an instruction of mine to add one name to a
  shared set would have RE-OPENED the hole the task existed to close, since that set was consulted at
  more call sites than I had in mind. The implementer declined the literal wording. **Read a
  delegate's push-back as evidence, not insubordination.**

#### The repair you would reach for first makes it worse

- **A surviving mutant's obvious remedy — write a stronger assertion — is the wrong one when the
  code it names cannot change any outcome.** Measured twice in one session, resolving oppositely.
  In one, a branch's verdict was already forced by the check below it, so the survivor was really
  reporting that the branch was INERT: a line reading as a safety property while unable to fail, and
  the repair was to DELETE it. In the other the branch did decide something real but narrower than
  it looked — only the diagnostic, and *it died* sends you somewhere different from *it disagreed* —
  so the repair was to assert that. **Ask what the code could still decide before writing a test for
  it**; a test written around inert code passes forever and pins nothing.
- **The obvious repair for a false positive is to narrow the matcher — and narrowing silently drops
  true positives too.** Measured twice in one session, in one parser, each time while fixing the
  previous attempt. A gate over-blocked; the fix stopped a token being misread, which also removed
  an *accidental* catch, and a command that had been correctly blocked became invisible. The
  replacement then treated the same input as ambiguous and DROPPED it rather than recording it —
  turning a second blocked case into an allowed one. Both fixes were right about the noise they
  targeted, and both shipped green: a suite cannot see cases that stopped arriving, and the author
  writes tests from the false positive, never from what quietly left. **Ask what stops being
  MATCHED, not whether the noise stopped** — and prove it with a corpus of things that must STILL
  match, run against the old build and the new one.
- **Upgrading a "could not measure" verdict into a POSITIVE one hands that positive to every case
  the vague verdict was quietly absorbing.** The mirror of the bullet above: nothing stops being
  matched here, the same inputs arrive and are simply judged more strongly. A helper reporting
  failure through ONE sentinel — an empty string, `None`, a bare nonzero — has already erased *why*,
  so a caller re-reading it as "nothing to measure, therefore fine" asserts fine for its whole
  preimage. Measured: a resolver returned that sentinel on three conditions — target absent, not a
  directory, not enterable — and a fix that correctly turned *absent* into a verified pass turned
  *unmeasurable* into one too, rebuilding the defect it was written to remove; worse than the vague
  verdict it replaced, which the docs at least told the reader to relay as a coverage gap.
  **Enumerate the sentinel's preimage before promoting any of it**, and take the discriminator from
  the source the helper consulted — re-deriving it at the call site copies a rule that then drifts.
- **Widening a matcher to fix an under-report can make it NON-TERMINATING — and the check you would
  run next passes.** Not the narrowing hazard: nothing stops being matched here, and every answer it
  gives is still right; it just never finishes giving one. An alternation whose branches can split
  one token more than one way costs `k` parses per repetition and `k**n` over `n` of them, and a
  backtracking engine memoises nothing. Measured: a change that widened one option group ran 0.05s,
  0.48s, 4.4s, 31s at n=5..8 on a single crafted line — reachable from any file the scan reads.
  Note what CLEARS it and should not: *prove the corpus still
  matches* passes flawlessly, because the match SET only grew. Nor is the repair to narrow back — an
  unambiguous form that was wider still ran in 0.0000s. **Ask what a widened alternation costs in
  PARSES, not only in what it now matches.**

#### It answered its own question, not the one you are relying on

- **A gate that fails CLOSED on an INTERNAL ERROR has not judged your command — it never evaluated
  it.** The refusal reads exactly like a policy block, so the natural response, reach for the
  override, aims at a gate that was not objecting to anything. Seen: a commit refused with *"internal
  error (ValueError) while evaluating it; failing closed"*, triggered by an apostrophe inside the
  heredoc form the repo's own commit skill prescribes — the plain form passed, an apostrophe-free
  heredoc passed, so only the combination failed and nothing had ever run it. Ask whether the tool
  reached a verdict before believing the verdict.
- **A tool's DEFAULT MODE can be narrower than its name, its call site, and even its source imply —
  so its PASS answers a smaller question than the one you are relying on.** Measured: a sweep
  reported 14 checks passing by default and 17 under the flag that adds the test suite and its
  pollution checks, and a gate was nearly cleared by citing the plain PASS as evidence the suite
  had passed. Note what CONFIRMS the wrong belief — the source carries an unmistakable call to the
  test runner that reads as unconditional until you notice it sits inside the opt-in branch, so
  checking the code is the natural move and it agrees with you. Only the run's own ENUMERATION of
  the checks it performed settles it: a missing row is the sole artifact that names what did not
  happen. Distinct from reaching for the wrong instrument — this is the right one, in a mode you
  never asked for.
- **Two readings of ONE question, and the WEAKER one guards the mutating path's verdict — so a tool
  blesses the state its own read-only mode calls broken.** Both readings run and each is truthful
  about the question it asks; the strict one is simply the one you never consult before acting.
  Measured: a read-only mode asked whether a managed path resolved to its EXACT source, while the
  post-action pass asked only whether it resolved SOMEWHERE INSIDE the source tree. Repointed at a
  different file inside that tree, the documented repair command reported `PASS … unverified=0`,
  exit 0, and left it wrong — while the read-only mode on the byte-identical state reported drift
  and FAIL. Note what does NOT rescue you: the neighbouring remedy, read the run's own ENUMERATION
  of what it checked, answers `verified=16` — true and useless. The suite missed it too, carrying
  rows for a dangling target and for one resolving OUTSIDE the tree, both of which the weak
  predicate also rejects. **Derive both readings from ONE predicate**; their disagreement is the
  only cheap detector, so two spellings of one intent is itself the defect.

#### You verified what you had in mind — the gap is what you did not

- **A suite you wrote for your own fix confirms what you thought of — not that the fix is safe.**
  Ten assertions written for one change, three of them PRESERVE rows verified green *before* it, all
  passed while that fix silently removed a live catch from a fail-closed gate; the regression sat in
  the one shape nobody had listed, and the author is the last person able to list it. What found it
  was a PROPERTY quantified over inputs nobody chose — *may only insert escapes*, *is idempotent*,
  *4000 random inputs*, *the blocked thing stays blocked*. The repair was to shrink the change until
  the property held: touch only what is already broken, so everything that works today comes out
  byte-identical. **When a change's safety is a claim about ALL inputs, assert the claim, not a
  handful of witnesses to it.**
- **Strengthening a rule's CONSEQUENCE does not widen its TRIGGER — "it always blocks now" reads as
  coverage.** Measured: a reviewer supplied a missing entry for a guard's match set; a later
  restructuring made that branch refuse *unconditionally* rather than conditionally, and the entry
  was deleted as redundant, "closed by construction". False — unconditional-versus-conditional
  governs what happens AFTER a match, and the value was never in the match set, so the branch never
  matched. The hole stayed open while the design record and the plan both recorded it CLOSED; one
  probe of the real input settled it, allowed before and after. **Ask what makes a rule FIRE before
  reasoning about what it does when it fires.** What nearly hid it: the plan then asserted the
  block, so a compliant implementer would have written a passing test around the false premise.

#### It answered about the PARTS; your claim is about the WHOLE

- **A tool that answers one question per item has not answered how the items COMPOSE — and an
  ordered plan asserts exactly that.** Measured: a planner judged all 27 changes correctly on its only
  question (does this one merge into an earlier one?), then ordered the resulting units by each unit's
  FIRST member, while the apply step takes a unit's content from its LAST. Merging thus moves content
  forward but leaves position early — safe only when a unit's file set is disjoint from every unit it
  jumps over, which nothing checked because it was never the question. Four of six proposed merges
  were unsafe, one silently fatal: a final state short of the target, catchable only by the
  end-of-run comparison, after every unit was built. **That comparison is no backstop** — measured
  since: it returned a clean converged verdict while 7 of 9 merges were each invalid, only the end
  state right. Per-unit validity and overall convergence are different questions; ask both.
  **Do not fix it by REORDERING** — that is one
  more composition claim nobody checked. Drop the merge; an unmerged unit costs tidiness, a
  misordered one costs the run.
- **A fixture built at the smallest N that exercises the code cannot see a threshold crossed at
  N+1 — and it reads as proof of the general case.** Measured: a report bounded each item's excerpt
  to 21 lines beneath a 50-line total cap, so a TWO-item fixture (42 lines) passed while three
  items (63) silently truncated the third and never named a fourth. Every per-item bound was
  correct; nothing tested the sum, and the passing two-item row is what stopped anyone looking.
  Ask what the property quantifies over — each item, or their total — and build at the smallest N
  where those two readings DIVERGE, never at the smallest N that runs the code. **The catch is
  that N is only computable once you know the bound**, and the binding one is often not yours: a
  downstream reader's cap, a buffer, a rate limit. When you cannot name the number, that is the
  finding — say the limit is unknown rather than picking a fixture size that makes it invisible.
- **Splitting one message into per-case messages does not stop it overclaiming — the shared WRAPPER
  around them is a new claim about EVERY case, and you will only check it against the cases you set
  out to fix.** Measured: a refusal that misattributed its cause was split into seven per-case
  diagnoses with per-case remedies, which fixed the original defect — while the sentence wrapping
  all seven still asserted a condition that was plainly false for the one case just added. The
  split MANUFACTURED the unchecked whole: beforehand there was one claim and it was verified;
  afterwards there were eight and only seven were anyone's job. Re-read the wrapper against the
  case you ADDED — it is the one no reviewer holds a prior for, and the one your own attention
  already spent itself on.
- **Two halves that ARRIVE TOGETHER can still TAKE EFFECT at different instants, so shipping them
  as one unit does not close an ordering constraint.** The unit that fails you is the UPDATE, not
  the commit — atomic delivery buys nothing here. A tool read into memory when an operation is
  INVOKED runs its pre-update version for that entire run, while a tool the same run resolves by
  PATH is already the new one, so the halves activate in the order the runtime chooses. Measured: a
  step that satisfies an assertion and the assertion itself arrived in one update, and the landing
  run still executed the OLD performer against the NEW assertion and failed, once, by construction.
  Nothing in the new code can change what an already-loaded old copy does, so this class is
  foreseeable and never fixable — predict it and say plainly that the first run will fail and what
  clears it, or the failure reads as a broken deploy rather than as the change arriving, which
  invites exactly the wrong repair.

#### The check itself writes — what it leaves behind is the hazard

- **When you mutate a document programmatically, assert the SHAPE of the edit — not just that you
  found the right spot.** A script that located an entry by its first line, then scanned forward for
  a `→ [[link]]` sentinel to find its last, moved THREE entries and reattached a note to the wrong
  one — the sentinel sits inline at the end of a prose line, so the scan ran past its target. Every
  assertion still passed, because all of them constrained where the edit STARTED and none constrained
  how far it reached. The catch needs nothing from the locator: require the edit to be *insert-only*
  or a *pure reordering*, by comparing the multiset of non-blank lines before and after.
- **An edit also leaves behind the text AROUND it, and adjacency is what hides what it broke.**
  Reword a phrase and any nearby sentence naming that phrase now points at nothing — while sitting
  INSIDE the diff's own context lines, so it reads as already reviewed. Measured: renaming a remedy
  left the next paragraph citing it by its old name, and every mechanical gate passed — lint, the
  repo audit, a region suite, and a structural measurer whose counts were unchanged because nothing
  was added or removed. Found by chance, one paragraph below the edit. The near-miss is the
  instructive part: a rename earlier the same session DID get a reference grep, but for the renamed
  HEADING and across OTHER files. **Grep for the words you REMOVED, inside the file you edited** —
  the mirror of the check you thought to run is the one you will not think of. **And that grep is
  not delegable** — measured since: two model reviewers briefed to find exactly this both passed a
  sentence a deletion had orphaned, one reporting *no dangling references*. A reviewer reads for
  sense and finds some; only the removed word finds the wreckage.
- **A DEFAULT output path makes every run of a tool a writer of real state.** Measured twice, in
  opposite directions, neither found by review. **Outward:** a diagnostic log defaulted to the
  operator's own log directory, and long-standing suite rows reach exactly that branch, so **12
  synthetic records** accumulated in that real log across four runs — invisible to lint, to every
  assertion, and *structurally* to the repo audit, which only ever scans inside the repo. **Inward:**
  a documented command wrote its artifact to the repo root, where it fails the **next** step's own
  clean-tree precondition — a workflow blocking itself on a file its own documentation told the
  operator to create. What found them was the first file's existence being surprising, and running
  the second command as written. **Ask what a run LEAVES BEHIND, not only what it reports**, and give
  every artifact an explicit destination outside everything anyone else checks.
- **A fixer pointed at `.` rewrites files your change never touched — scope it to what you edited.**
  Measured: `ruff format .` reformatted a fenced Python example inside `STYLE.md`, a hand-laid block
  with aligned trailing comments, in a file the session never opened and whose extension the tool
  does not own. Nothing flagged it, because the result was *correctly* formatted — lint and every
  suite passed, and only an unexpected path in `git status` gave it away. Two assumptions fail at
  once: that the tool's scope is your change, and that its file types are the ones you associate with
  it, since a formatter follows code fences into markdown. Name the paths you touched, and read an
  unexpected path in `git status` as a finding rather than noise.

#### The teardown you are counting on — narrower than you think, or never reached

- **A RESTORE guarantee covers only what it snapshotted — a clean subject is not a clean tree.** A
  harness that mutates one file, restores it in a `finally`, and reports `restored: sha256
  unchanged` has verified exactly that one path; whatever its mutants wrote ELSEWHERE survives the
  run, and the reassuring line is what stops you looking. Measured: a mutant that introduced a
  default output path left a stray artifact in the repo root, untracked and un-ignored, while the
  campaign reported a clean restore and a perfect score. Deny the subject a writable cwd, or
  snapshot the tree — and read every teardown, stash and rollback the same way.
- **A tool that snapshots a file, mutates it, and restores ITS OWN snapshot silently discards
  whatever you write to that file while it runs.** Not under-restoration but OVER-restoration: the
  snapshot is authoritative and stale, so your edit is reverted without a word. Measured: a mutation
  campaign was running against a file when that file was edited; the restore wrote back the pre-run
  bytes and the edit was gone. Meanwhile the failures its live mutants produced read exactly like
  the edit breaking the suite, so the natural response — debug your own change — aims at nothing.
  The reassuring line traps you here too: `restored: sha256 unchanged` is TRUE and says nothing
  about your edit, because the digest it compares against is the snapshot's. Treat such a tool as
  owning its subject exclusively while it runs, and check for a live mutant before believing any
  failure that coincides with one.
- **A teardown runs only on the path you tested — a KILLED run skips it and leaves the subject
  broken.** `finally`, `trap` and `atexit` do not survive a default SIGTERM, so a harness stopped by
  a timeout restores nothing. Measured: a mutation campaign killed at a 2-minute cap left its
  subject — a checker — carrying a live mutant that made it report `PASS` on unreadable input, a
  verification tool silently inverted into a rubber stamp. Its `restored: …` line was simply
  ABSENT, which is what every killed run looks like, so nothing drew the eye. **And git cannot
  cover you when the subject is UNTRACKED** — no baseline to diff against, and `git status` shows a
  bare `??` identical to a healthy new file. Verify restoration by CONTENT — a pre-run digest, or
  each anchor's occurrence COUNT — never by reading the tree, and never by mere PRESENCE, which
  fails both ways: measured, a replacement occurring elsewhere too read as a live mutant.
- **A run that never ENDS reaches no teardown at all — and "still running" reads as normal for as
  long as you allow.** A kill at least terminates, so `finally`, `trap` and the restoration check
  above eventually get their turn; a hang delivers no signal, returns no control, and leaves no
  verdict, artifact or corpse to notice. Measured: a harness freshly hardened against SIGTERM still
  stranded its subject 13+ minutes, because the hardening was a signal handler and no signal was
  coming. Bound the operation itself. **Then expect that bound to be your next wrong number** — size
  it from the worst LEGITIMATE run actually observed, never from plausibility (5x a measured
  baseline misfired on a real case needing 10.6x), and make an overrun report INDETERMINATE rather
  than a verdict, so too tight a bound costs attention rather than a score.

#### Building a check that holds

- **When a check keeps springing leaks, change its INSTRUMENT CLASS, not its wording.** Three rounds
  of sharpening a *postcondition on an opaque tool's output* gave: a vague test, then a precise one
  **on the wrong axis** (it keyed on a verdict line's *presence*, but "no verdict" was itself a legal
  verdict *value*), then a precise one that was **unsatisfiable** (it demanded the tool enumerate what
  it read; the tool reports findings, not a manifest). A *precondition on its input* closed it in one
  move, needing nothing from the tool — ask **"what can I observe without this thing's cooperation?"**
  before "how do I word this better?". All three failures defaulted to *proceed*.
- **Clear by allowlist, since a blocklist admits every value you forgot.**
- **Derive an input from what you already asserted rather than checking a hand-made copy — but pair
  the derivation with a declared FLOOR, since discovery cannot detect ABSENCE.** Seen twice in one
  session: a gate's hand-listed file set went stale and waved through a file nobody checked, and a
  "durable" test hand-copied the very key it existed to guard — so it would not have failed if that
  key were renamed. The converse bites too: a glob replacing such a list covers whatever you forget
  to ADD, and silently stops covering whatever anyone REMOVES — it matches one fewer file and
  reports success. Name the members whose absence must alarm; let the glob only ever add to them.
  **Zero is that converse's limiting case** — a discovery matching *nothing* reports success loudest
  of all; measured, a symlinked root made `find` return 0 files, reading exactly like "nothing
  changed". Assert a non-zero denominator.
- **A claim's grounding must be checkable from the artifact itself.** Evidence sitting where the
  reader cannot reach it — a private note, an unwritten instruction, "we settled this earlier" — is
  indistinguishable from no evidence, and doubles as a template for asserting anything. Seen: an
  override of a vendored hard gate justified by a memory file no consumer of the published skill
  could read, then re-justified by citing a precedence rule that did not say what was claimed; both
  read as authoritative and neither survived a reader who actually checked. Ground a claim in what
  its audience can verify, or drop the claim.

### Package Management

#### Python (uv)

- **Version**: Python 3.13 (MacPorts)
- Create venv: `uv venv` — Activate: `source .venv/bin/activate`
- Install: `uv pip install <package>` (NOT standard `pip`)
- Sync: `uv pip sync requirements.txt`
- One-shot script with deps: `uv run --with <pkg1> --with <pkg2> python3 -c '...'` (no venv needed; ephemeral)

#### Node.js (NVM)

- **NVM** manages Node (versions under `~/.nvm/versions/node/`); use the newest installed (v26.x line)
- Initialize: `source /opt/local/share/nvm/init-nvm.sh` — in non-interactive shells this may
  leave `node` off PATH; call the binary directly: `~/.nvm/versions/node/<ver>/bin/node`
- Install: `npm install <package>`
- npm-global CLIs live per-version in `~/.nvm/versions/node/<ver>/bin` and need that dir ON
  PATH (their `env node` shebang; an absolute launcher path alone fails). `markdownlint-cli2`
  is installed there — the markdownlint hook uses it; repos opt in via `.markdownlint-cli2.jsonc`
  (for a repo-wide run pass the glob explicitly — `markdownlint-cli2 "**/*.md"` — a bare invocation
  lints 0 files when the config contains only ignores, which false-reads as a clean pass)

#### System Tools (MacPorts)

- Install: `sudo port install <package>`
- Check: `port installed | grep <package>`
- Location: `/opt/local/bin/`, `/opt/local/lib/`

## macOS Notes

### Bash Versions

| Version | Location | Use Case |
|---------|----------|----------|
| 3.x | `/bin/bash` | System/POSIX scripts |
| 5.x | `/opt/local/bin/bash` | Modern scripts (associative arrays, `[[`, etc.) |

### GNU vs BSD Tools

macOS ships BSD tools by default, but **this machine already prepends
`/opt/local/libexec/gnubin`**, so an unprefixed `grep`/`sed`/`date`/`ls` is the GNU one. The table
is what each side offers, not what you get by default here:

| Tool | BSD | GNU | Key Difference |
|------|-----|-----|----------------|
| grep | `/usr/bin/grep` | `ggrep` | `-P` (Perl regex) |
| sed | `/usr/bin/sed` | `gsed` | Extended features |
| date | `/bin/date` | `gdate` | Better parsing |
| ls | `/bin/ls` | `gls` | `--color`, `--group-directories-first` |

Already in effect here: `export PATH="/opt/local/libexec/gnubin:$PATH"` — verify with
`which date` rather than assuming either way, since a shell that lacks it silently gives you BSD.

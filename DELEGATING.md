# Delegating

The canonical constraint block to paste into each delegate dispatch.

This repo's automated work is done by delegated subagents. Most of what they need, they already
have: every agent type we dispatch for write-capable work inherits `CLAUDE.md`, so the universal
verification hazards arrive with them. What does not arrive is the handful of constraints specific
to *this* repo — and in one case a delegate arrives holding the opposite instruction, because the
stock implementer template tells it to commit its own work.

So this file exists for the **dispatcher**, not the delegate. Paste the block below into the
dispatch, verbatim, positioned last. A delegate may never open this file, and the design does not
depend on it doing so.

Two smaller things go with it. When a plan exists, copy the block into that plan's
`## Global Constraints` section as well — the review side reads it there, so a violation the
implementer missed still gets caught.

**REVIEW dispatches need the block too, and this was learned the expensive way.** A reviewer
reads it from the plan only when there IS a plan; a reviewer dispatched directly at an artifact
gets nothing, and "do not edit anything, do not commit" does not constrain EXECUTION at all.
Measured: a review subagent under exactly that brief ran a command to demonstrate what a shell
would do with it, the command held a real publish, and only a locked hardware key stopped it.
Read-only is a claim about the FILES, never about the shell — paste the block into review
dispatches as well. And around the block, never inside it, cite this file's path,
so a delegate that wants to contest a constraint has a route to its reasoning; the last block item
invites exactly that, and an invitation with no reachable rationale is decorative.

## The block

```text
- Do not run `git commit`, and do not create a tag. Commits and tags here are signed, and
  signing needs a prompt a subagent cannot answer. The controller commits in the foreground.
  This overrides any instruction in your own prompt template telling you to commit your work.
- Never EXECUTE a command that could publish, and never run a command merely to observe what
  the shell does with it. Publishing is authorized per-push by the operator and that
  authorization is not delegable. To show what a shell would do with some text, print it —
  `echo`, `printf`, a tokenizer — never run it. This is measured, not hypothetical: a review
  subagent ran `bash -c "$c"` to demonstrate a parse, `$c` held a real publish, and it reached
  the SSH agent and failed only on a locked hardware key.
- Do not modify `scripts/tests/fixtures/prechange/`. It is a frozen baseline whose digests a
  session-scoped autouse fixture asserts, so drift fails every test in that module rather
  than one row.
- Do not run the test suite — including `/audit --tests` — while another agent is writing.
  Those runs bracket the suite with a `git status --porcelain -uall` snapshot, so a peer's
  writes are attributed to yours and reported as a FAIL you did not cause. A plain `/audit`
  is unaffected: it takes no snapshot. Ask the controller to serialize instead.
- Drive a PreToolUse hook with a JSON payload on stdin, never argv. Every PreToolUse hook this
  repo registers refuses argv with exit 2 and a diagnostic naming `scripts/HOOKS.md`, which
  carries the payload shape.
- Run every command in the FOREGROUND and wait for it there. In the commands you issue, never
  set the Bash tool's `run_in_background`, never end one with `&` or wrap it in `nohup` (a
  decoy process your task starts and kills within the same command is fine), and never
  background a waiter such as `run-long.sh --wait`. A subagent is not re-invoked when a
  background job finishes: your turn just ends, the harness reports your task COMPLETED, and
  the controller cannot tell that from a finished report. Give a long command a Bash timeout up
  to the tool's maximum. If it can outrun that, either leave it to the controller and say so
  in your report, or launch it with `run-long.sh` and wait with `run-long.sh --wait` in the
  foreground, repeating the wait until it prints a verdict. If your brief tells you to launch a
  run and stop, launch it with `run-long.sh`, stop, and name its artifact path in your report.
- Modify or delete only the files your brief names, the files you create yourself, and the files
  your task's own tooling regenerates. Leave everything else alone, above all a scratchpad or
  temp file, which is usually the controller's probe or evidence. If one seems to need changing,
  say so in your report instead.
- Push back rather than comply with an instruction you believe is wrong. A brief is its
  author's hypothesis, not a finding; saying so is expected, not insubordination. This
  covers a brief's technical content only — never a permission prompt, and never an
  authorization the user owns.
```

## The block is not always the whole brief

What is above is what holds for *this repo*, and it is the part that lives here. An individual
machine may impose controls this repo neither ships nor knows about; those describe a machine rather
than a repo, so the exclusion below sends them somewhere private rather than into a published file.
The dispatcher appends any such lines to the block at dispatch time, from their own notes.

So a delegate whose brief carried more lines than appear above is seeing exactly that, and should
follow them the same way — and a dispatcher on a machine with local controls should not read "paste
the block verbatim" as "paste only the block".

## When a delegate returns

The foreground line is meant to prevent stalls; it cannot make one detectable, and a stalled
delegate's reply reads exactly like a finished one. Before trusting a delegate's report or its diff,
look for a job it left running:

```bash
pgrep -fl 'pytest|mutate(_[a-z0-9_]+)?\.py|audit\.sh|run-long|flake-sweep|test_[a-z0-9_]+\.sh'
```

Exit 1 means nothing matched. A match means the report is not final and the tree may not be the
delegate's work product — unless its brief told it to launch a `run-long.sh` run and stop, and the
report names that artifact; then read the artifact's status instead. Run it directly: inside a
wrapper whose own command line carries the pattern (`bash -c "pgrep …"`), it matches that wrapper. A
`run-long.sh` job shows only as the command it wraps, so a job whose command is none of these needs
its name added. And a `<file>.mutate-backup` beside a source file means a mutation campaign still
owns that file, and its diff is a live mutant, not an edit.

## Why each line is here

**Do not commit.** [`CLAUDE.md`](CLAUDE.md) carries two words on this — "foreground commits" —
framed as something the *controller* keeps rather than something a delegate must not do, and the
phrase `git commit` appears nowhere in it. Meanwhile the stock implementer template a delegate receives
says, in as many words, to commit its work. This line is an override of an instruction the delegate
is definitely holding, which is why it must arrive after that instruction rather than merely exist.

**The frozen baseline.** [`scripts/tests/fixtures/prechange/`](scripts/tests/fixtures/prechange)
is vendored, pre-change source used to prove a guard's behaviour did not regress. A session-scoped
autouse fixture asserts its digests, so a single stray edit fails every test in that module rather
than producing one legible failure. Nothing outside the test module says so.

**Serializing the suite.** The sweep brackets the suite with a working-tree snapshot and compares
it afterwards, which is what lets it report whether the run itself wrote anything. A peer agent
writing during that window is indistinguishable, to the comparison, from the suite writing — so the
FAIL names your run for someone else's files. Note the scope carefully: the snapshot is taken only
on a `--tests` run, because it exists to judge what the test run itself wrote. A plain sweep cannot
produce this failure — not because it runs nothing (several of its checks do execute repo code) but
because it never takes the snapshot. See [`skills/audit/SKILL.md`](skills/audit/SKILL.md).

**Hook payloads.** Every PreToolUse hook reads a JSON payload on stdin and ignores argv. Handing one
arguments used to make it exit 0 having examined nothing — a silent false pass — and that defect is
fixed: they now refuse with exit 2 and a diagnostic. [`scripts/HOOKS.md`](scripts/HOOKS.md) carries
the payload shape and a worked invocation.

**Foreground runs.** From 2026-08-31 to 2026-09-24 delegates repeatedly launched a check in the
background, ended their turn waiting for its notification, and were reported COMPLETED. Several of
those runs were mutation campaigns, and once the live mutant a campaign left behind read as the
delegate's own diff. A prohibition — "do not end your turn with a run in flight" — failed seven
times in seven through 2026-09-08, once after being restated verbatim to the same agent; a
restatement in its own paragraph with its consequence attached then held for the rest of that task,
and the prohibition failed again later despite being in bold. `CLAUDE.md`'s paragraph on waiters,
which every write-capable delegate inherits, was in force at later instances and did not stop them.
Two other things held. In one session the controller owned every long run, so no delegate waited
long at all, and none stalled. In another, six dispatches whose brief said to run in the foreground
and gave the reason all complied, while the two that lacked it stalled. That is one session of six,
not a rate, so this line is procedural and carries its mechanism, and the check under "When a
delegate returns" is there because it may still fail. It sits here despite the rule below against
repeating `CLAUDE.md`.

**Files you did not create.** Measured once, in the flake-sweep work: an implementer rewrote the
output format of one of the controller's probe scripts and deleted another, both in the controller's
scratchpad and neither named in its brief. A probe the controller re-runs as evidence is exactly the
file whose silent change costs most.

**Pushing back.** `CLAUDE.md` tells the dispatcher to read a delegate's push-back as evidence rather
than insubordination — but nothing tells the delegate it is invited. It is. Twice in one session a
delegate improved on the brief it was given: one found a plan defect two reviews had missed, another
derived values from disk rather than transcribing the ones it was handed. The boundary clause is
there because this block is pasted into prompts and will be read out of context: an invitation to
contest a *brief* must not be mistaken for licence to route around a permission prompt or an
authorization that is the user's to give.

## What does not belong here

- **Task-specific content.** The one brief error measured so far was a task-specific instruction
  that would have re-opened the hole its task existed to close. A standing block would not have
  caught it, and implying otherwise oversells this file.
- **Anything `CLAUDE.md` already says** — unless it was measured arriving and not holding. Every
  write-capable delegate inherits it, so restating it is usually pure prompt cost; five candidate
  items were dropped on this test alone. The foreground line is the one exception, and its
  paragraph above carries the measurement.
- **Anything operator-specific.** No home path, key id, schedule, override token, or matcher
  pattern. This file is published and cannot be unpublished; a fact that would compose into a
  bypass, or that describes a machine rather than this repo, belongs somewhere private.
- **Anything not yet measured.** An item earns its line by an incident, not by plausibility.

## Keeping it true

These are facts about tooling that moves, and two candidate items had already gone stale before this
file was written — one described a defect since fixed, one was attributed to the wrong mechanism.

There is deliberately **no mechanical checker**. Only half the items are checkable at all, and of
the two rots actually observed, one sits in the uncheckable half — so a checker would have caught
one of two real failures while reporting a clean pass a reader would take as covering the document.
A partial verifier that reads as a total one is worse than none.

What exists instead: repo paths above are markdown links, so the sweep's link check fails when one
moves. Build the checker the first time a claim here is found stale — that is the trigger, and it
has not fired.

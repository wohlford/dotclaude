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

> **Bugs get a regression test first.** When a bug is found, reproduce it as a failing test *before*
> fixing it (RED→GREEN; see [workflows.md](./workflows.md)). Skipping is a flagged exception — state
> why at fix time (e.g. untestable: timing/environment/interactive), never skip silently.

> **Never reason your way past the last gate before something irreversible.** Publishing, deploying,
> force-pushing, or a destructive migration is where the gate you can argue is redundant — *"the suite
> already passed on an identical tree"* — is the one to actually run. That argument is usually right,
> which is what makes skipping it a habit, and a skipped gate is indistinguishable from no gate.

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

**Ask the user directly only for:** authorization (anything outward-facing or hard to reverse), risk
appetite they own (how strict a gate should be, what false-block rate is acceptable), the scope they
are paying for, and anything where proceeding wrongly would be unsafe or waste substantial work.
Batch whatever survives — ask once, at a natural checkpoint, not as each item arises.

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
  - GNU tools are prefixed with `g` (e.g., `gls`, `ggrep`, `gdate`)
  - Use GNU versions for advanced features like `--long-options`

## Language and Tooling Preferences

- **Preferred**: Unix tools orchestrated through Bash scripts
- **Secondary**: Python for complex tasks requiring rich libraries
- Favor command-line tools and shell scripts over GUI methods
- Use Python when Bash becomes unwieldy or complex data structures are needed

### Verification hazards — instruments that read as verified while proving nothing

When a check reads clean, work through groups 1–4 in order before trusting it. A group splits once
it passes about four members, and a hazard with no measured instance does not belong here at all.

#### Nothing ever ran — silence is not a pass

- **A killed run never ran — absence of a verdict is not a pass.** A check stopped by a timeout
  (SIGTERM, rc 143) prints a *prefix* of `PASS` lines, never emits one for the check still in flight,
  and never reaches its summary — so grepping for `FAIL` finds nothing and the output reads clean.
  Require the specific verdict line **and** the summary to be *present*; "no FAIL" is not "passed".
  Record the real exit status **inside** the artifact you will read, so its **absence** is itself the
  signal that the run died — the harness's announcement can't be trusted, for the reason below.
- **A check that never fired never ran, either.** Editing outside your tooling's normal path skips its
  hooks silently — they do not fail, they never run — and a hand-substitute is reliably narrower than
  what it replaced. Name what you skipped and run it, or use the normal path.
- **A check that is merely INSTALLED has never run — registration is not liveness.** Seen: a config
  restore put back a runtime file lacking the three hook registrations the incoming commit added — 21
  entries where the commit had 23 — while the obvious diff reported *clean*; the gate was then
  unprovable until a reload. Watch it fire once, against a target you can afford to have it miss.
- **A command you write into documentation is unverified until you run it.** An un-runnable one reads
  exactly like a working one, so prose review never catches it — only execution does. Seen: a flag
  rejecting the arity it was given (`git check-ignore -q a b` → `fatal: --quiet is only valid with a
  single pathname`), and a snippet its own guard blocks. Run every documented command once **as
  written**; when one needs a hand workaround twice, the doc is the defect, not the workaround.

#### It ran, but not on what you think

- **Before believing a probe's verdict — FAIL *or* PASS — confirm it reached the subject, and that
  your ENVIRONMENT did not answer for it.** An unresolvable shell variable produces both errors, since
  a tool sees the command text *unexpanded*: one gate blocked on the literal path (a FAIL about
  nothing), another allowed because the lookup keyed on it came back empty (a PASS about nothing). A
  shell with no TTY does it too — a card-backed key cannot prompt for its PIN, so the agent REFUSES,
  byte-identical to a rejected credential; "auth is down, go fix the card" was reported for a card
  that was present and unlocked. All measured. The clean run is the dangerous one — nobody
  investigates it.
- **A checker that resolves its helpers relative to itself grades your branch with the OLD tools.**
  So a change *to* the tooling is judged by the copy it replaces — the verdict is **true**, just
  about a different question than you asked, and it reads GREEN whenever the installed copy is the
  laxer one. Seen: one sweep gave `FAIL … drift` from the installed copy and `PASS rc=0` from the
  branch's own, minutes apart, both correct. Run the change's own tools, and name which copy
  produced the verdict.
- **A change that is only correct in COMBINATION is one unit of work.** Two halves of a fix can be
  individually wrong in *opposite* directions — one alone over-blocks, the other alone lets the bug
  through — so landing half is not partial progress, it is a regression. And it is one no suite can
  catch: every test passes at both commits, because the broken state exists only *between* them. Seen:
  a filter and the flag that makes it safe, split across two tasks; the interval shipped the
  over-blocking half and broke a real workflow while three suites stayed green. Ship them together, or
  say plainly that the interval is broken and why.
- **A regression test that never reaches the defect passes for free — watch it FAIL before you trust
  its PASS.** The fixture's environment is part of the subject: `mktemp -d` under a symlinked
  `$TMPDIR` (`/tmp` → `/private/tmp`) yields a *logical* path that does not physically contain the
  file, and a tool resolving paths can take a different branch there and never reach the bug. Seen: a
  cwd-resolution fix whose test passed identically with and without it, until the sandbox was pinned
  with `pwd -P`; nothing in the green output hinted at it, since a correct fix produces the same
  green. RED→GREEN is not ceremony — the RED is the only evidence the fixture reaches what you fixed.

#### The signal you read belongs to something else

- **Multi-line literal checks are a case for Python.** `grep -F` treats an embedded newline as
  *alternation*, not a sequence: `grep -Fc "$(printf 'a\nb')"` counts lines matching **either**, so a
  multi-line check returns a plausible-but-wrong count and reads as verified. Use `python3 -c "..."`
  (`needle in open(f).read()`) or `grep -Pzo`.
- **In wrapped text, use it even for a phrase you believe is one line** — if it happens to wrap, a
  line-based grep returns 0 and absence is not evidence of absence.
- **A pipeline's exit status is the LAST command's.** `some-check | tail -20` reports `tail`'s success
  however the check exited — so a run that "completed (exit code 0)" can have proven nothing, and a
  backgrounded one reads as a clean pass. Read the tool's own verdict/summary lines rather than the
  rc, or don't pipe it (`set -o pipefail`, or `${PIPESTATUS[i]}`, when you must) — and index that by
  POSITION: `[0]` is the FIRST stage, so in `printf … | tool | tail` it reports the *printf*, and a
  tool that exited 2 reads as 0. Measured, on a gate that had correctly blocked.
- **A script, function, or `{ … }` wrapper exits with its last command's status too** — so a
  diagnostic `echo` appended after an assertion discards the verdict it was meant to report, and the
  check reports the *echo's* success. Capture `rc=$?` on the very next line, then `exit "$rc"`. The
  harness will otherwise announce "completed (exit code 0)" for a run that was killed, for the reason
  above.

#### The verdict is right — what you conclude from it is the hazard

- **A check's output is evidence, not instruction.** Its *verdict* is usually right; its *suggested
  repair*, and your reading of a *failure*, are not. Seen: an exec-bit check reporting "has a shebang
  but committed 100644 — chmod +x" for a module that is only ever imported, where the correct fix was
  the opposite — delete the shebang; obeying the message would have made a library executable.
- **A deferred item's prescribed FIX is a past self's hypothesis; only its defect is a finding.**
  Re-measure before building what an earlier session queued. Seen: an entry read "implement
  `filter=` in the other five handlers" — measuring first showed the gap was never `filter`-specific:
  nothing validated *any* directive against its handler, so a *documented* one was ignored by six of
  seven. One declared allowlist closed every case plus future typos; building the entry as written
  would have left the larger hole open and added five more places to forget.
- **A gate that fails CLOSED on an INTERNAL ERROR has not judged your command — it never evaluated
  it.** The refusal reads exactly like a policy block, so the natural response, reach for the
  override, aims at a gate that was not objecting to anything. Seen: a commit refused with *"internal
  error (ValueError) while evaluating it; failing closed"*, triggered by an apostrophe inside the
  heredoc form the repo's own commit skill prescribes — the plain form passed, an apostrophe-free
  heredoc passed, so only the combination failed and nothing had ever run it. Ask whether the tool
  reached a verdict before believing the verdict.
- **A suite you wrote for your own fix confirms what you thought of — not that the fix is safe.**
  Ten assertions written for one change, three of them PRESERVE rows verified green *before* it, all
  passed while that fix silently removed a live catch from a fail-closed gate; the regression sat in
  the one shape nobody had listed, and the author is the last person able to list it. What found it
  was a PROPERTY quantified over inputs nobody chose — *may only insert escapes*, *is idempotent*,
  *4000 random inputs*, *the blocked thing stays blocked*. The repair was to shrink the change until
  the property held: touch only what is already broken, so everything that works today comes out
  byte-identical. **When a change's safety is a claim about ALL inputs, assert the claim, not a
  handful of witnesses to it.**

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
- **When you mutate a document programmatically, assert the SHAPE of the edit — not just that you
  found the right spot.** A script that located an entry by its first line, then scanned forward for
  a `→ [[link]]` sentinel to find its last, moved THREE entries and reattached a note to the wrong
  one — the sentinel sits inline at the end of a prose line, so the scan ran past its target. Every
  assertion still passed, because all of them constrained where the edit STARTED and none constrained
  how far it reached. The catch needs nothing from the locator: require the edit to be *insert-only*
  or a *pure reordering*, by comparing the multiset of non-blank lines before and after.
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

macOS ships BSD tools by default. GNU versions (MacPorts) provide more features:

| Tool | BSD | GNU | Key Difference |
|------|-----|-----|----------------|
| grep | `/usr/bin/grep` | `ggrep` | `-P` (Perl regex) |
| sed | `/usr/bin/sed` | `gsed` | Extended features |
| date | `/bin/date` | `gdate` | Better parsing |
| ls | `/bin/ls` | `gls` | `--color`, `--group-directories-first` |

To use GNU by default: `export PATH="/opt/local/libexec/gnubin:$PATH"`

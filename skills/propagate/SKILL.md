---
name: propagate
description: Promote committed changes from this dev working copy to the live ~/.claude repo locally; --push also publishes to origin (explicit)
disable-model-invocation: true
---

# /propagate — Promote the Dev Working Copy to Production (and optionally Publish)

Make this dev repo's committed changes take effect as the live `~/.claude` configuration.
**By default this is entirely local** — dev → production, no network — so you can try changes in
production without going public. **`--push` also publishes** to `origin` (an explicit, deliberate
act) and then refreshes production from what it just published. Paths are derived, never hardcoded,
so the skill is public-safe and works on any machine.

## Dynamic Context

```bash
git rev-parse --abbrev-ref HEAD
```

```bash
git status -sb | head -1
```

```bash
# Live/production repo that ~/.claude/skills resolves to (blank if it isn't a symlink)
if [ -L ~/.claude/skills ]; then
  (cd -P ~/.claude/skills && git rev-parse --show-toplevel)
else
  echo "(not a symlink — cannot determine live repo)"
fi
```

## Instructions

This repo is the **dev** working copy; **production** is the separate repo symlinked into `~/.claude`.
Promoting means fast-forwarding production to this repo's committed state and prompting a restart.
**Promotion is local by default** (dev → production directly, no `origin`). Publishing to `origin`
happens **only** when the user passes `--push` (or explicitly asks) — pushing is a deliberate,
authorized action gated by the push-guard.

### Publication model awareness

Check once, before dispatching: does `.publication.toml` exist at the repo root?

```bash
test -f "$(git rev-parse --show-toplevel)/.publication.toml"
```

- **Absent (non-adopted repo).** Everything below runs completely unchanged — the existing
  local-promote / publish-then-refresh procedure, with no model-specific behavior.
- **Present (adopted repo).** Production tracks `dev` locally (dogfood repos: `production="dev"`)
  while the public `main` is a *divorced*, ground-up recast published separately from `dev`. The
  no-flag and `--push` arms diverge accordingly from here — see **Arguments** and **Process**
  below.

### Arguments
- *(no flag)* — **promote locally**: fast-forward production from this dev repo. No network.
  **Adopted repos:** unchanged — this is still the local dev → production fast-forward
  (`src="$dev"`); the skip-worktree `settings.json` dance still applies.
- `--push` — **non-adopted:** publish then promote — push this repo to `origin`, then refresh
  production from origin. **Adopted repos:** publish only — dispatches to **the publish path
  (adopted `--push`)** below. It does **not** refresh production
  afterward; see Process step 4. Promote production separately with a plain (no-flag)
  `/propagate` from `dev` once you want production to pick up the change.
- A branch name — promote/publish that branch instead of the current one. In an adopted repo's
  publish path the operative branch is always `dev` → `main`; a supplied branch name is a
  non-adopted/local concept and does not redirect the publish path.
- `--cutover` — **adopted repos, operator-only, one-time.** A standalone flag (not combined with
  `--push`): it routes to **the publish path (adopted `--push`)** in *cutover mode* — the two
  shared-engine substitutions, application base = the orphan root (not `main`'s tip) and push mode =
  the one-time force-push — and bypasses the watermark's normal absent-watermark abort so the orphan
  restart can run with none recorded yet. On a **non-adopted repo, report and refuse** (the model,
  hence the cutover, requires the marker). The force-push and the rest of the cutover's mechanics are
  a separate, out-of-scope procedure — see the watermark ref convention below for the seam this flag
  provides.

### Process

1. **Branch:** the supplied name, else `branch=$(git rev-parse --abbrev-ref HEAD)`.
2. **Confirm shareable:** the working tree is clean (`git status -sb` in full — the Dynamic
   Context line is truncated). Its ahead/behind count tracks the branch's upstream (usually
   `origin`), **not** production — whether production actually lacks commits is settled by step
   5's `--ff-only` merge, which is a no-op when already current. If there are uncommitted
   changes the user wants live, tell them to `/commit` first. When a branch name was supplied,
   first verify it exists (`git rev-parse --verify <branch>`; if not, report and stop) — the
   clean-tree check still applies to this working copy, which is what gets fetched from.
3. **Resolve production** from the symlink; capture the dev repo root:

   ```bash
   dev="$(git rev-parse --show-toplevel)"
   if [ ! -L ~/.claude/skills ]; then
     echo "~/.claude/skills is not a symlink — cannot locate production; promote manually." >&2
   else
     live="$(cd -P ~/.claude/skills && git rev-parse --show-toplevel)"
   fi
   ```

   - Not a symlink → stop, tell the user to promote manually.
   - `live` equals `dev` → the working copy IS production; nothing to do; report and stop.

4. **Publish, if requested.**
   - **Adopted repo, `--push`:** do **not** run this step's push — dispatch instead to **the
     publish path (adopted `--push`)** below. **That path publishes `main` only; it does
     NOT refresh production afterward.** In an adopted dogfood repo production tracks `dev`, while
     published `main` is a divorced, ground-up recast with no shared ancestry — fast-forwarding
     production from `origin/main` after an adopted publish would dead-end. Skip step 5 entirely
     for this arm. Promote production separately with a plain (no-flag) `/propagate` from `dev`.
   - **Non-adopted repo, `--push`:** publish then promote. Pushing is explicit-only (the
     push-guard blocks a bare `git push`); the `--push` flag IS the authorization, so lead the
     command with the override:

     ```bash
     ALLOW_PUSH=1 git push origin "$branch" --follow-tags
     ```

     If the push is rejected, do **not** force-push — fetch, report the divergence to the user,
     and **stop**: do not continue to step 5 until the user resolves it and a subsequent push
     succeeds (promoting from origin's stale state would fake a successful promotion).
     Set the **source production fast-forwards from** for step 5: `--push` → `src=origin`;
     default (local promote) → `src="$dev"`.
   - **No flag (either arm):** nothing to publish this step — continue to step 5 with
     `src="$dev"`.
   - **Adopted repo, `--cutover`:** operator-only — dispatch to **the publish path (adopted
     `--push`)** in cutover mode (base = orphan root, one-time force-push, absent watermark allowed),
     per the `--cutover` argument above. The full cutover procedure is a separate, out-of-scope
     one-time step; this dispatch is only the seam. Like `--push`, it publishes `main` only and skips
     step 5.
   - **Non-adopted repo, `--cutover`:** report and refuse — the publication model, and the cutover,
     require the `.publication.toml` marker.

5. **Fast-forward production (never force).**

   **Adopted repos — first, assert production is on the branch the marker names.** Read the
   `production` value from **this (dev) repo's** `.publication.toml` — never production's own copy,
   which a drifted production would supply from the very branch under suspicion — and compare it
   against production's actual checkout:

   ```bash
   marker="${dev:?}/.publication.toml"
   [ -r "$marker" ] || { echo "cannot read $marker — refusing to guess the production branch" >&2; exit 1; }
   # Deliberately UNquoted $( … ): an assignment's command substitution is never word-split, so
   # this is identical in effect to the quoted form — but quoting it wraps a single-quoted body
   # holding an ODD number of double-quote characters, which drifts the push-guard tokenizer's
   # quote state and makes it fail closed on this very block. Do not re-add the outer quotes;
   # keep this comment free of double quotes too, since the parity is what does the damage.
   want=$(sed -nE 's/^[[:space:]]*production[[:space:]]*=[[:space:]]*"([^"]*)".*/\1/p' "$marker")
   want="${want:-main}"   # empty ⇒ "main" — an absent key, never an unreadable file
   got="$(git -C "${live:?}" rev-parse --abbrev-ref HEAD)"
   [ "$want" = "$got" ]
   ```

   The extraction tolerates the trailing comment the marker ships with, and collapses the documented
   defaults into one rule: **empty ⇒ `"main"`**. The `-r` test and the `${dev:?}`/`${live:?}`
   expansions are what keep that rule honest — without them an unreadable marker, or an unset path
   left over from a fresh shell, would silently yield `"main"` (or compare the dev repo against
   itself) and **pass** a drifted production. Every such case must abort, never default.
   (A detached production reports the literal `HEAD`, which mismatches every marker value — the
   desired outcome, since a detached checkout cannot fast-forward either.)

   On mismatch, **report and STOP — do not fetch, do not merge.** Production is serving a different
   branch than the model says it should, so promoting would either fail confusingly (`--ff-only`
   across divorced lineages) or fast-forward a branch that isn't the production target. Name both
   branches — the one production is on and the one the marker specifies — and let the user
   reconcile: checking production out onto the named branch is the usual fix, but a deliberate
   excursion is theirs to end, not this skill's to undo. **Non-adopted repos skip this** — no
   marker, no expected value, behavior unchanged. (The **adopted** publish-only `--push` and
   `--cutover` arms never reach this step, so a publish is never blocked by drift it doesn't touch.)

   Then fetch from `src` and `--ff-only` merge:

   ```bash
   git -C "$live" fetch "$src" "$branch" --tags
   git -C "$live" merge --ff-only FETCH_HEAD
   ```

   (`$src` is `origin` after `--push`, else the absolute `$dev` path for a fully-local promote.)
   - **On `--ff-only` failure, do NOT force.** If `git -C "$live" log FETCH_HEAD..HEAD --oneline` is
     non-empty, production has local commits the source lacks — report and ask the user how to
     reconcile; never merge/rebase/reset automatically. (A deliberately *divorced* takeover — production
     and dev share no history — is a one-off the user performs by hand, not this skill.)
   - **If the only blocker is the runtime `settings.json`** (skip-worktree, so it never shows in
     `git status --porcelain`) — **confirm that from the failed merge's own error before parking
     anything.** `--ff-only` names the paths it refuses to overwrite ("Your local changes to the
     following files would be overwritten by merge: …"); this is the settings.json case only when
     that list is exactly `settings.json`. If it names anything else, take the "any other tracked
     file blocks" branch below instead of stashing. **Do not substitute a `git diff` probe:**
     `skip-worktree` makes git assume worktree == index for this path, so no plain diff can see the
     runtime modification — which is the whole reason this bullet exists. (A standalone probe would
     have to lift the flag first: `update-index --no-skip-worktree settings.json`, then
     `git diff --name-only HEAD`, then re-set it — the merge error is cheaper and already in hand.)
     Then: park it, fast-forward, restore it so the runtime prefs (`model`,
     `enabledPlugins`) survive, then hand-add any new hook entries the committed version gained —
     enumerate them with `git -C "$live" diff FETCH_HEAD -- settings.json` after the restore (the
     runtime file vs the incoming commit: copy over missing `hooks` entries, keep the runtime
     `model`/`enabledPlugins` values):

   ```bash
   git -C "$live" update-index --no-skip-worktree settings.json
   git -C "$live" stash push -m 'runtime settings.json' -- settings.json
   git -C "$live" merge --ff-only FETCH_HEAD
   git -C "$live" checkout 'stash@{0}' -- settings.json && git -C "$live" stash drop
   git -C "$live" reset -q HEAD -- settings.json
   git -C "$live" update-index --skip-worktree settings.json
   ```

   - **If the merge still fails after parking:** restore `settings.json` from the stash
     (`checkout 'stash@{0}' -- settings.json`, `stash drop`) and re-apply `--skip-worktree` before
     reporting the blocker — never leave the file parked with a dangling stash.
   - **Any other tracked file blocks:** do not auto-discard; restore `settings.json` if parked, report
     the blocker and the manual options (`git checkout -- <file>` is safe only when it already equals
     the incoming version).

   **Then verify the promote left no dead gate — always, both arms.** Which check applies is decided
   by one cheap question asked *before* the merge: was `settings.json` in the incoming range?

   ```bash
   git -C "$live" diff --name-only HEAD FETCH_HEAD | grep -qx settings.json
   ```

   - **Not in the range** (the common case — five consecutive promotes ran this way): the parking
     dance never engaged, so the postcondition is the strict one — the runtime file must come out
     **byte-identical**. Take `shasum -a 256 "$live/settings.json"` before and after and compare
     them, and confirm `git -C "$live" stash list` is empty and `skip-worktree` is still set
     (`git -C "$live" ls-files -v settings.json` begins with `S`).
   - **In the range:** the restore just put back a file predating the incoming commit, which is
     exactly how a registration goes missing. Run:

   ```bash
   ~/.claude/scripts/settings-hooks-check.py --scope "$live" --ref FETCH_HEAD
   ```

   **Proceed only on `RESULT: PASS`** — its last stdout line, with `FAIL`, `ERROR` and an absent
   line each a failure to prove the invariant. On `FAIL` it names every registration the commit
   carries and the runtime lacks; add those to the runtime file by hand, keeping its
   `model`/`enabledPlugins` values, and re-run until it passes.

   **Do not substitute a count**, and do not read agreeing counts as agreement. Measured: a promote
   where runtime and commit both held 24 entries while differing in *both* directions at once — a
   machine-local extra present, a committed gate missing. The check is one-directional
   (`committed − runtime`) for that reason, and it reports runtime-only extras without failing on
   them, since this machine legitimately carries hooks the repo does not track.

6. Remind the user to **restart Claude** so the promoted skills/agents/config reload. (Adopted
   repo, `--push` arm: nothing local was promoted by steps 4–5 — this reminder is moot until a
   later plain `/propagate` actually fast-forwards production; **the publish path (adopted
   `--push`)** owns its own completion note.)

#### The publish path (adopted `--push`)

This is the procedure Process step 4 dispatches to for an adopted repo's `--push` arm. It recasts the
unpublished `dev` work into clean bricks appended onto public `main`, in the foreground, and pushes
them. Nothing here runs for a non-adopted repo — the whole subsection lives entirely under the
adopted-`--push` arm above and is otherwise inert.

Recall the model: `dev` is globally messy — later features fix earlier ones as new bricks, never
folded back into the commit they fix. `main` is the **global ground-up recast of `dev`**: every fix
folded into the brick it fixes, bricks building on each other, converging to `dev`'s functional
state. Published `main` is **immutable/append-only** — once a brick is pushed it is never rewritten;
every subsequent publish only **appends** new bricks and fast-forwards.

**Scope — what this path cannot do.** Appending is the entire repertoire: it can add to published
history but structurally **cannot remove anything already published**. If the goal is to take
content *out* of `main`, this path cannot do it — and running it anyway silently produces the
opposite of the intent: the content stays published and a new brick lands on top of it. A rewrite
is a separate, deliberate operation, out of scope here — and even a rewrite only makes content
not-current, never unpublished.

**One ancestry-independent primitive does the mechanical work — materialisation — and
`scripts/publish-brick.sh` is it.** A brick's file set is the UNION of its constituents' paths and
its content is the ENDPOINT commit's, the endpoint being the last constituent; so the brick is
exactly `git checkout <endpoint> -- <files>`. That handles a **non-contiguous fold** with no scratch
branch and no patch application, and it cannot half-apply the way a conflicting `cherry-pick` or
`git apply` can. Its one precondition — no constituent may DELETE or RENAME a path, neither of which
a checkout can express — the script asserts before writing anything.

Brick **boundaries** are judgment — the same brick-boundary discipline the "Adopted-repo finish:
re-derive onto `dev`" subsection of `skills/feature/SKILL.md` documents (a skill and its regenerated
`sync-docs` index entry in the same brick; a shebang file and its exec bit in the same brick) — but
brick **application**, once the boundary is chosen, is mechanical. **Do not re-derive it by hand:**
two consecutive publishes wrote throwaway scripts for exactly this and threw them away with the
session, which is why the tooling below is in the repo.

1. **Start-invariant + crash recovery.** Before anything else, run the scripted preflight — it
   encodes this step's assertions, and step 2's ancestry check, once instead of re-deriving their
   ordering by hand on every publish:

   ```bash
   ./scripts/publish-preflight.sh
   ```

   **Run the copy belonging to the repo being published, and say which copy gave the verdict.** A
   publish that changes the preflight must be judged by the version it is publishing, never by the
   installed copy it is about to replace. Prefer `<repo>/scripts/publish-preflight.sh`; failing that,
   `~/.claude/scripts/publish-preflight.sh --scope <repo>`. An adopted repo carrying neither still
   publishes — assert by hand from the specification below, which is what the script automates.

   **Proceed only on `RESULT: PASS rc=0`**, the script's last line of stdout. `FAIL`, `ERROR`,
   `INCOMPLETE`, and an **absent** line are each a failure to prove the invariant — an allowlist, so
   any value not named here is not-clean. **The absent line is the one to watch:** it means the run
   died having established nothing about the remote, and a killed run reads as quiet to every cheap
   instrument. Never infer the verdict from missing `FAIL` lines or from the harness's account of the
   exit code. A `SKIP` never blocks — `auth` skips when no terminal can take a card PIN prompt, which
   is a statement about the shell, not about the credentials.

   **The script verifies; every recovery below stays yours to run.** On a failure it prints the
   prescribed commands and deliberately executes none of them — recoveries delete tags, reset
   branches, and move the watermark, exactly the mutating class this path keeps foreground and
   human-checkpointed. It also refuses a dirty working tree and an empty `watermark..dev` range;
   both block this path further down, and failing here is cheaper than failing mid-apply.

   The rest of this step is what the preflight asserts — and the manual fallback when it is absent.
   **Fetch** and assert **local `main` == `origin/main`** — the fetch is mandatory, since comparing
   against a stale cached `origin/main` would let a half-finished publish pass unnoticed:

   ```bash
   git fetch origin main
   [ "$(git rev-parse main)" = "$(git rev-parse origin/main)" ]
   ```

   A prior publish that minted tags and advanced local `main` but died before pushing leaves local
   `main` **ahead** of `origin`. On that mismatch, **abort with recovery** — delete the
   minted-but-unpushed `vX.Y.Z` tags *first*, while `main` still points past `origin` so they are
   reachable to enumerate (safe: they never reached `origin`), *then* reset local `main` to
   `origin/main`, then stop and report; never continue a half-finished publish. Order matters: after
   the reset `main == origin/main`, so `--no-merged origin/main` would match nothing and the tags
   would be orphaned instead of deleted.

   ```bash
   git tag --merged main --no-merged origin/main | while read -r t; do git tag -d "$t"; done
   git reset --hard origin/main
   ```

   Only once local and `origin` match, read the **watermark** — the `dev` commit whose tree `main`'s
   tip currently reflects, per the watermark ref convention documented below (this subsection reads
   it, it does not define it). **Absent ⇒ abort** (except operator `--cutover`):

   ```bash
   watermark="$(git rev-parse --verify -q refs/published/main)" \
     || { echo "no watermark — abort (see the watermark ref convention; --cutover is the only bypass)" >&2; exit 1; }
   ```

   **Watermark integrity at start (stranded-behind guard).** The `main == origin/main` check catches
   a watermark stranded *ahead* of a failed push, but **not** one stranded *behind* a **succeeded**
   push whose watermark-advance (step 7) never ran — a crash between steps 6 and 7 leaves `main` and
   `origin/main` already equal, so that check passes. Assert the watermark still matches what `main`
   reflects, reusing step 5's convergence predicate (defined below; the equivalent is given inline here):

   ```bash
   git diff --quiet "$watermark" main -- . ':(exclude)CHANGELOG.md'
   ```

   If it **fails while `main == origin/main`**, a prior publish pushed but never advanced the
   watermark — do **not** re-derive (that would re-append already-published bricks); instead advance
   the watermark to the `dev` commit whose tree `main`'s tip now matches (the `dev` tip as of that
   publish) and report the recovery.

2. **Re-derive the unpublished work** — the `dev` commits after the watermark — into clean bricks,
   ground-up, the same foreground re-narration discipline as the adopted-repo `dev` re-derivation.
   First assert the watermark is still an ancestor of `dev` (integrity — the watermark convention's
   rule 2; a `dev` rebase or amend can strand it), aborting loudly before deriving anything:

   ```bash
   git merge-base --is-ancestor "$watermark" dev
   ```

   Step 1's preflight already asserted this as its `watermark-ancestor` check, so a `RESULT: PASS`
   there covers it. Re-run it by hand only when the preflight was unavailable — or when `dev` moved
   after it ran, which a long foreground re-derivation makes possible.

   **Fold** a fix into the brick it fixes **when that brick is also unpublished** (after the
   watermark); a fix targeting **already-published** work becomes **its own new brick** instead —
   published `main` is immutable and is never rewritten to absorb a later fix.

   **Get the first draft of that mechanically**, then review it:

   ```bash
   ./scripts/publish-fold-plan.py            # still on dev here, so the repo's own copy is fine
   ```

   It classifies every commit in `watermark..dev` by the lines the commit **removes** — removes
   nothing ⇒ its own brick (a `+N/-0` short-circuit needing no `merge-base` call at all, so it runs
   first every time); removes a line still present in the published tree ⇒ its own brick; removes
   only lines added in-range ⇒ folds into the latest commit that added them. It prints the evidence
   for each call and a proposed `publish-brick.sh` invocation per brick, versions included.

   Two things it deliberately does **not** do. It never resolves an ambiguity: where the evidence
   does not settle a commit it reports `UNDECIDED` and leaves it standing alone, because a wrong
   fold converges to the identical tree and is precisely what step 5 cannot catch, while a missed
   fold only costs tidiness. And it does not know the **holistic pairings** — a skill and its
   regenerated `sync-docs` index entry belong in one brick, as do a shebang file and the commit
   setting its exec bit — so merging two proposed bricks for that reason is your call, not its.
   The plan is a proposal; the boundaries remain judgment.

3. **Apply, prove and tag each brick onto `main`'s tip** — check out `main`, then run the engine
   once per brick, in the order the plan gives:

   ```bash
   ~/.claude/scripts/publish-brick.sh --scope <repo> <version> <endpoint> '<subject>' [constituent...]
   ```

   **Invoke the INSTALLED copy, not `<repo>/scripts/`** — unlike step 1's preflight, which must be
   the branch's own. This step checks the repo out to `main`, where the engine may not exist yet
   (the brick adding it has not landed) or may be an older revision than the one being published.
   The installed copy tracks `dev`, so it is both present and current throughout; `--scope` is what
   makes the repo data rather than context. An adopted repo with no installed engine applies its
   bricks by hand from the mechanics below.

   **One brick per invocation, and you drive the loop.** That is the point: the per-brick
   checkpoint that makes this path reviewable survives, while the parts a human reads wrong under
   repetition are read mechanically every time. Per brick the engine materialises the file set,
   asserts **both** shapes (the brick's files match the endpoint byte for byte, **and** nothing
   outside the file set moved — the second is the half that catches a materialisation reaching too
   far), inserts the CHANGELOG entry with an insert-only assertion, commits, runs the audit,
   verifies the tag exists after minting it, and stops on the first thing that does not hold.
   Read its terminal `RESULT: PASS rc=0 brick=<version>` line before running the next one.

   **What it deliberately leaves to you.** Nothing it does is irreversible — it never pushes and
   never moves the watermark. When it fails *after* the commit lands it prints the recovery
   (`git reset --hard HEAD~1`, delete the tag) and runs none of it; when it fails *before*, it
   restores exactly the paths it wrote so the next attempt still meets its clean-tree precondition.

   **Which copy of what**, since the two resolve differently on purpose: the **audit** comes from
   the scope (`<scope>/skills/audit/audit.sh`), because it judges the tree being built and must be
   that tree's own copy rather than the installed one it may be replacing; the engine's **helper
   library** resolves relative to the engine, because a construction tool must not vanish mid-build
   when the working tree is checked out to a commit predating it. Both paths are printed.

   **A brick is proven only by `RESULT: PASS rc=0`**, `audit.sh`'s last line of stdout; `FAIL`,
   `ERROR`, `INCOMPLETE`, an unanticipated status, and an **absent** line are each a failure to
   prove — an allowlist. **The absent one is to watch**, since a killed sweep prints a prefix of
   `PASS` lines and no summary, so every cheap instrument reads it as clean. The engine enforces
   this, records the audit's exit status *inside* the artifact it writes, and additionally fails
   closed when the verdict and the exit status disagree. Never infer the verdict from the absence
   of `FAIL` lines or from the harness's account of the exit code — a wrong read here is not
   contained to this brick, it compounds into every brick built on top of it. On anything but a
   pass, fix the offending commit and re-run; on `ERROR`, correct the invocation rather than
   re-running it unchanged.

   Brick **boundaries must keep `/audit`'s holistic checks intact**: never split across two bricks
   anything `/audit` validates as a pair — a skill and its regenerated `sync-docs` index entry stay
   in the same brick; a shebang file and the commit that sets its exec bit stay in the same brick.
   This is exactly the feature-finish brick-boundary rule, reused here rather than re-derived, and
   it is the pairing the fold plan cannot see for you.

4. **Tag + CHANGELOG per brick** — step 3's engine does both, inside the brick commit, per
   `/commit`'s conventions. Versioning is **main-only**: `dev`-side commits stay untagged
   throughout, unaffected by this step.

5. **Convergence check (non-vacuous, CHANGELOG-aware).** After appending all bricks, assert:

   ```bash
   git diff --quiet <dev-tip> <main-tip> -- . ':(exclude)CHANGELOG.md'
   ```

   `main` carries a per-brick `CHANGELOG.md` entry that `dev` never gets (step 4), so an unqualified
   whole-tree diff would be permanently non-empty and could never pass — that is not this check.
   **`CHANGELOG.md` is the one and only excluded path**; do not widen the exclusion beyond it, or the
   check stops proving real convergence. Once the tree-compare passes, run the **full test suite once
   more, at the tip** — the repo's suites via `/audit --tests` (shell suites + pytest) — both must
   hold — and `audit.sh`'s single `tests` check already runs both sub-suites, so one verdict line
   settles both. **That run counts as held only on `RESULT: PASS rc=0`.** `--tests` is the longest
   sweep there is, which makes it the most likely of all to be cut short by a timeout — and a
   cut-short run is exactly the one that reads clean to every cheap instrument. Require the verdict
   line to be *present* and to say `PASS rc=0`.

   **Measured: this run exceeds a single foreground timeout** (~10 minutes here, against ~21s for
   the per-brick static sweep). So **background it, and record its exit status INSIDE the artifact
   you will read back** — never wrap it in a foreground wait loop, which is itself killable and
   whose own death then looks exactly like the sweep's:

   ```bash
   tip="$(mktemp -d)/tip-audit.txt"
   { ./skills/audit/audit.sh --tests --scope "$PWD" 2>&1; printf 'AUDIT_EXIT_STATUS=%d\n' "$?"; } \
     > "$tip" &
   printf 'reading back: %s\n' "$tip"
   ```

   The recorded status is what makes an **absent** verdict legible as a death rather than as quiet
   success; without it, a killed run and a clean one are the same silence. The artifact goes to a
   temp directory rather than the repo **for a mechanical reason, not tidiness**: an untracked file
   at the repo root fails the next brick's clean-tree precondition. **This is the last check
   before step 6 makes the
   work public and irreversible, so nothing here is advisory:** on `FAIL` or `ERROR`, stop and
   report — do not push — fixing the offending commit for a `FAIL` and the invocation for an
   `ERROR`; on `INCOMPLETE` or a missing line, re-run to completion and read the new verdict, never
   conclude either way from the truncated one.

6. **Push `main`.** Publish with a **plain `git push`**, led by the required override:

   ```bash
   ALLOW_PUSH=1 git push origin main --follow-tags
   ```

   The push-guard confirms the target is `main`, not `dev`, and allows the main-reachable published
   tags through. Note explicitly: **`git push` has no `--ff-only` flag** — a plain push already
   rejects any non-fast-forward update by default, which is the safety property this step relies on.
   **Never `--force`** here; force-push is reserved solely for the one-time orphan cutover (out of
   scope for this subsection). On rejection, do not force — stop and report; a rejected push means
   `origin/main` moved out from under the start-invariant in step 1 and needs investigation, not an
   override.

7. **Advance the watermark** to `dev`'s current tip — **only after step 6's push has succeeded:**

   ```bash
   git update-ref refs/published/main "$(git rev-parse dev)"
   ```

   Advancing it earlier, followed by a failed push, would leave the watermark asserting bricks are
   published that never reached `origin`. The watermark's ref mechanics, its `--cutover` mode, and its
   integrity/absent-abort rules are documented in the watermark ref convention below; this step only
   fixes *when* the advance happens relative to the push.

**Shared-engine parameterization.** This same procedure is written to be reused, not forked, by the
one-time orphan cutover. The cutover substitutes two mechanical axes — the **application base**
(`main`'s current tip for a normal publish vs. the orphan root for the cutover) and the **push mode**
(a fast-forward `git push` in step 6 vs. the one-time force-push) — and additionally enters through
the `--cutover` gate that bypasses the absent-watermark abort. **Step 1's start-invariant does not
apply to a first cutover** (there is no `origin/main` yet to fetch or compare against); steps 2–7 —
re-derivation, per-brick `/audit`, tag+CHANGELOG, convergence check, watermark advance — apply
unchanged with those substitutions. The cutover itself is out of scope here.

**Honest guarantee — do not overclaim.** Step 5's tree-compare proves **losslessness**: `main`'s tip
tree equals `dev`'s tip tree, modulo `CHANGELOG.md`. It does **not** prove **fold-correctness** — a
fix folded into the wrong brick can still converge to the identical final tree while misrepresenting
which brick actually fixed what. Fold quality therefore rests on the per-brick `/audit` run in step 3
plus human judgment during re-derivation, not on the mechanical convergence check; report it that
way, never as proof that every fold landed in the right brick.

#### The watermark ref convention (`refs/published/main`)

**Storage.** The watermark is a custom ref, `refs/published/main` — **not** a `refs/tags/` tag and
**not** a branch — pointing at the `dev` commit whose tree `main`'s current tip reflects.

- **Why a custom ref and not a tag.** This is the actual dev-privacy leak this convention closes: a
  tag would point at a `dev` commit, so an ordinary `git push --tags` / `git push --follow-tags`
  would publish private `dev` history to `origin` right along with it. A ref under `refs/published/`
  is swept by **neither** tag pushes nor branch pushes, so it stays local unconditionally. (A branch
  would carry the same push-exposure risk and would additionally clutter branch listings with a ref
  that isn't meant to be checked out or worked from.)

**Lifecycle.** Three rules, each closing a distinct silent-corruption or privacy failure:

1. **Absent ⇒ abort — except the operator-only `--cutover` mode.** Normally, an absent watermark
   means the publish path must **stop and report** rather than attempt a run: before the one-time
   orphan cutover happens, an adopted repo could otherwise dispatch `--push` to the publish path with
   no watermark yet recorded, and abort-on-absent is what closes that dormancy hole. The **one-time
   orphan-restart cutover** is the sole exception — it needs exactly a "recast from the orphan root,
   empty watermark" invocation, so `/propagate` exposes an explicit **operator-only `--cutover` entry
   point** that bypasses the absent-watermark abort (its application base becomes the orphan root
   instead of `main`'s tip, per the publish path's shared-engine parameterization). Plain `--push`'s
   default stays abort-on-absent; only `--cutover` may proceed with none recorded. The cutover's own
   force-push and remaining mechanics are a separate, out-of-scope procedure — this convention only
   provides the seam it needs. Detect absence with `git rev-parse --verify -q refs/published/main` —
   a non-zero exit means no watermark is recorded.
2. **Integrity ⇒ abort on failure.** Before deriving the `watermark..dev` range, assert
   `git merge-base --is-ancestor <watermark> dev`. `dev` is the messy line with nothing enforcing
   append-only, so a rebase or amend can strand the watermark — it stops being an ancestor of `dev` —
   which would otherwise silently re-publish already-published work or publish the wrong range. On
   failure, abort loudly and report; never guess a replacement watermark.
3. **Advance only after a successful push.** The watermark advances to `dev`'s tip only once the
   publish path's push has actually succeeded, never before. Advancing it earlier and then having the
   push fail would leave the watermark asserting bricks are published that never reached `origin`.

### Rules
- **Marker-aware dispatch.** Read `.publication.toml` at the repo root once per invocation
  (see **Publication model awareness**) for dispatch; step 5's branch assertion additionally reads
  its `production` value. A non-adopted repo runs the unchanged procedure below.
- **Local by default.** Plain `/propagate` never touches `origin` — it promotes dev → production
  locally so you can try things in production without publishing. Unchanged in an adopted repo.
- **Publishing is explicit.** Only `--push` (or an explicit user request) pushes to `origin`, and it
  leads with `ALLOW_PUSH=1` to satisfy the push-guard. Never push without that authorization.
- **Adopted `--push` publishes `main` only.** It does not refresh production afterward — production
  tracks `dev` and `main` is a divorced recast, so there is no shared ancestry to fast-forward from.
  Promote production separately with a plain (no-flag) `/propagate` from `dev`.
- **Never** force-push or force-merge — `--ff-only` only; surface failures with a manual fallback.
- **Never** hardcode machine paths — derive production from the `~/.claude/skills` symlink and dev
  from `git rev-parse --show-toplevel`.
- Do not commit on the user's behalf here — promotion moves already-committed work.

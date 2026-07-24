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
   want="$(sed -nE 's/^[[:space:]]*production[[:space:]]*=[[:space:]]*"([^"]*)".*/\1/p' "$marker")"
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

Two ancestry-independent apply primitives do the mechanical work: `git cherry-pick <dev-commit>` for
a 1:1 (unfolded) brick, and `git diff <base> <target> | git apply --index` for a folded/synthesized
brick. Brick **boundaries** are judgment — the same brick-boundary discipline the
"Adopted-repo finish: re-derive onto `dev`" subsection of `skills/feature/SKILL.md` documents (a
skill and its regenerated `sync-docs` index entry in the same brick; a shebang file and its exec bit
in the same brick) — but brick **application**, once the boundary is chosen, is mechanical.

1. **Start-invariant + crash recovery.** Before anything else, **fetch** and assert **local `main` ==
   `origin/main`** — the fetch is mandatory, since comparing against a stale cached `origin/main`
   would let a half-finished publish pass unnoticed:

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

   **Fold** a fix into the brick it fixes **when that brick is also unpublished** (after the
   watermark); a fix targeting **already-published** work becomes **its own new brick** instead —
   published `main` is immutable and is never rewritten to absorb a later fix. The *published?*
   verdict is **mechanical**: `git merge-base --is-ancestor <fixed-commit> <watermark>` (true ⇒
   published ⇒ new brick). Only identifying *which* commit a fix targets, and where the resulting
   brick boundaries fall, is judgment.

3. **Apply each brick onto `main`'s tip**, foreground, signed:
   - A **1:1 brick** (equals exactly one `dev` commit) applies mechanically:
     `git cherry-pick <dev-commit>`.
   - A **folded/synthesized brick**'s endpoint tree never existed on `dev`, so it cannot be
     cherry-picked directly. Construct it first on a scratch branch based at the watermark (the same
     foreground re-narration the `dev` re-derivation uses) — folded application is judgment-driven up
     to that point, not purely mechanical — then apply the constructed result onto `main` with
     `git diff <staged-base> <staged-target> | git apply --index`.
   - On a `cherry-pick`/`apply` **conflict** (a mid-batch base no longer matches after folding or
     reordering), resolve it in the foreground toward the known `dev` target, or **abort — never
     leave `main` in a partial, half-applied state.**
   - Then **prove the brick: run `/audit` on `main` at that commit** — the same rigor the `dev`
     re-derivation applies. Brick **boundaries must keep `/audit`'s holistic checks intact**: never
     split across two bricks anything `/audit` validates as a pair — a skill and its regenerated
     `sync-docs` index entry stay in the same brick; a shebang file and the commit that sets its exec
     bit stay in the same brick. This is exactly the feature-finish brick-boundary rule, reused here
     rather than re-derived.

4. **Tag + CHANGELOG per brick.** Each appended `main` brick gets a fresh `vX.Y.Z` tag and a
   mirroring `CHANGELOG.md` entry, created here, per `/commit`'s conventions. Versioning is
   **main-only** — `dev`-side commits stay untagged throughout, unaffected by this step.

5. **Convergence check (non-vacuous, CHANGELOG-aware).** After appending all bricks, assert:

   ```bash
   git diff --quiet <dev-tip> <main-tip> -- . ':(exclude)CHANGELOG.md'
   ```

   `main` carries a per-brick `CHANGELOG.md` entry that `dev` never gets (step 4), so an unqualified
   whole-tree diff would be permanently non-empty and could never pass — that is not this check.
   **`CHANGELOG.md` is the one and only excluded path**; do not widen the exclusion beyond it, or the
   check stops proving real convergence. Once the tree-compare passes, run the **full test suite once
   more, at the tip** — the repo's suites via `/audit --tests` (shell suites + pytest) — both must
   hold.

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

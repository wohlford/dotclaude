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

### Arguments
- *(no flag)* — **promote locally**: fast-forward production from this dev repo. No network.
- `--push` — **publish then promote**: push this repo to `origin`, then refresh production from origin.
- A branch name — promote/publish that branch instead of the current one.

### Process

1. **Branch:** the supplied name, else `branch=$(git rev-parse --abbrev-ref HEAD)`.
2. **Confirm shareable:** the working tree is clean and the branch has commits to promote
   (`git status -sb` in full — the Dynamic Context line is truncated). If there are uncommitted
   changes the user wants live, tell them to `/commit` first.
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

4. **Publish first, only with `--push`.** Pushing is explicit-only (the push-guard blocks a bare
   `git push`); the `--push` flag IS the authorization, so lead the command with the override:

   ```bash
   ALLOW_PUSH=1 git push origin "$branch" --follow-tags
   ```

   If the push is rejected, do **not** force-push — fetch and report the divergence to the user.
   Set the **source production fast-forwards from** for step 5: `--push` → `src=origin`; default
   (local promote) → `src="$dev"`.

5. **Fast-forward production (never force).** Fetch from `src` and `--ff-only` merge:

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
     `git status --porcelain`): park it, fast-forward, restore it so the runtime prefs (`model`,
     `enabledPlugins`) survive, then hand-add any new hook entries the committed version gained:

   ```bash
   git -C "$live" update-index --no-skip-worktree settings.json
   git -C "$live" stash push -m 'runtime settings.json' -- settings.json
   git -C "$live" merge --ff-only FETCH_HEAD
   git -C "$live" checkout 'stash@{0}' -- settings.json && git -C "$live" stash drop
   git -C "$live" reset -q HEAD -- settings.json
   git -C "$live" update-index --skip-worktree settings.json
   ```

   - **Any other tracked file blocks:** do not auto-discard; restore `settings.json` if parked, report
     the blocker and the manual options (`git checkout -- <file>` is safe only when it already equals
     the incoming version).

6. Remind the user to **restart Claude** so the promoted skills/agents/config reload.

### Rules
- **Local by default.** Plain `/propagate` never touches `origin` — it promotes dev → production
  locally so you can try things in production without publishing.
- **Publishing is explicit.** Only `--push` (or an explicit user request) pushes to `origin`, and it
  leads with `ALLOW_PUSH=1` to satisfy the push-guard. Never push without that authorization.
- **Never** force-push or force-merge — `--ff-only` only; surface failures with a manual fallback.
- **Never** hardcode machine paths — derive production from the `~/.claude/skills` symlink and dev
  from `git rev-parse --show-toplevel`.
- Do not commit on the user's behalf here — promotion moves already-committed work.

---
name: propagate
description: Propagate committed changes from this working copy to the live ~/.claude repo (push, then fast-forward)
disable-model-invocation: true
---

# /propagate — Push Working Copy to the Live Config

Push this repo's committed changes, then fast-forward the **live** repo that `~/.claude`
resolves to, so edited skills/agents/config actually take effect. Invoked deliberately by
the user (it pushes and pulls). Paths are derived, never hardcoded, so the skill is
public-safe and works on any machine.

## Dynamic Context

```bash
git rev-parse --abbrev-ref HEAD
```

```bash
git status -sb | head -1
```

```bash
# Live repo that ~/.claude/skills resolves to (blank if it isn't a symlink)
if [ -L ~/.claude/skills ]; then
  (cd -P ~/.claude/skills && git rev-parse --show-toplevel)
else
  echo "(not a symlink — cannot determine live repo)"
fi
```

## Instructions

The user wants the committed changes in this working copy to become the live `~/.claude`
configuration. This repo is a working copy; the live config is a separate repo symlinked
into `~/.claude`. Push here, fast-forward there, then prompt a restart.

### Process

1. Determine the branch: `branch=$(git rev-parse --abbrev-ref HEAD)`.
2. Confirm the working tree is clean and the branch has commits to share (`git status -sb`).
   If there are uncommitted changes the user wants live, tell them to `/commit` first.
3. Push this repo: `git push origin "$branch" --follow-tags`.
4. Resolve the live repo from the symlink:

```bash
if [ ! -L ~/.claude/skills ]; then
  echo "~/.claude/skills is not a symlink — cannot locate the live repo; propagate manually." >&2
else
  live="$(cd -P ~/.claude/skills && git rev-parse --show-toplevel)"
fi
```

   - If `~/.claude/skills` is not a symlink, stop and tell the user to propagate manually.
   - If `live` equals this repo's root (`git rev-parse --show-toplevel`), the working copy
     IS the live repo — nothing to propagate; report and stop.
5. Fast-forward the live repo from origin (never force):

```bash
git -C "$live" fetch origin "$branch" --tags
git -C "$live" merge --ff-only "origin/$branch"
```

   - **On `--ff-only` failure, do NOT force.** First list other locally-modified tracked files with
     `git -C "$live" status --porcelain` — the runtime `settings.json` is skip-worktree, so it never
     shows there even when it is the blocker.
   - **If the only blocker is the runtime `settings.json`:** auto-resolve it — park it, fast-forward,
     then restore it so the runtime prefs (`model`, `enabledPlugins`) survive, then hand-add any new
     hook entries the committed version gained into the runtime file and report:

```bash
git -C "$live" update-index --no-skip-worktree settings.json
git -C "$live" stash push -m 'runtime settings.json' -- settings.json
git -C "$live" merge --ff-only "origin/$branch"
git -C "$live" checkout 'stash@{0}' -- settings.json && git -C "$live" stash drop
git -C "$live" reset -q HEAD -- settings.json
git -C "$live" update-index --skip-worktree settings.json
```

   - **If any other tracked file blocks** (e.g. a live-edited skill): do NOT auto-discard — it may hold
     real work. Restore `settings.json` if you parked it, then report the blocking file(s) and the
     manual options; `git checkout -- <file>` is safe only when the file already equals the incoming
     version (`git -C "$live" diff --quiet "origin/$branch" -- <file>`).
6. Remind the user to restart Claude so the propagated skills/agents/config reload.

### Arguments

The user may provide:
- No args — propagate the current branch.
- A branch name — propagate that branch instead of the current one.

### Rules

- **Never** force-push or force-merge — `--ff-only` only; surface failures with a manual fallback.
- **Never** hardcode machine paths — derive the live repo from the `~/.claude/skills` symlink.
- Push outside of normal working hours so the live config changes land off-hours.
- Do not commit on the user's behalf here — propagation moves already-committed work.

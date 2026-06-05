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
# Live repo that ~/.claude/skills resolves to (empty if not a symlink)
tgt="$(readlink ~/.claude/skills 2>/dev/null || true)"
[ -n "$tgt" ] && (cd "$(dirname "$tgt")" && git rev-parse --show-toplevel) || echo "(not a symlink — cannot determine live repo)"
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
tgt="$(readlink ~/.claude/skills 2>/dev/null || true)"
if [ -z "$tgt" ]; then
  echo "~/.claude/skills is not a symlink — cannot locate the live repo; propagate manually." >&2
else
  live="$(cd "$(dirname "$tgt")" && git rev-parse --show-toplevel)"
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

   - If the merge fails (diverged history, or the live tree has local / skip-worktree
     changes such as a runtime-rewritten `settings.json`), do NOT force. Report the failure
     and print the manual command for the user to resolve in `$live`.
6. Remind the user to restart Claude so the propagated skills/agents/config reload.

### Arguments

The user may provide:
- No args — propagate the current branch.
- A branch name — propagate that branch instead of the current one.

### Rules

- **Never** force-push or force-merge — `--ff-only` only; surface failures with a manual fallback.
- **Never** hardcode machine paths — derive the live repo from the `~/.claude/skills` symlink.
- Push only after work hours, per the repo's commit-timing policy.
- Do not commit on the user's behalf here — propagation moves already-committed work.

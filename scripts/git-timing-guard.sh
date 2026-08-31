#!/usr/bin/env bash
set -euo pipefail

# Script: git-timing-guard.sh
# Purpose: PreToolUse hook — block PUBLISHING outside a configured time window
# Usage: Called by Claude Code hooks with JSON on stdin
#
# Exit codes:
#   0 — allow (no config, not a push, out of scope, inside the window, or any internal error)
#   2 — blocked: a push to a configured repo during the blocked window (stderr fed back to Claude)
#
# The gate is on the PUBLICATION BOUNDARY, not on git writes generally. `push` is
# the act that makes work public and is the only thing time-gated; `commit` and
# `tag` never leave the machine, so the window does not block them. Gating those
# too made the override a daily reflex — and a bypass used daily is not a gate.
# Regression suite: scripts/tests/test_git_timing_guard.sh, in this repo. It used to sit beside
# the live copy as .git-timing-guard.test.sh; that is no longer where it lives, and the suite
# resolves this file as its SIBLING (../git-timing-guard.sh), never through $HOME.
#
# The window/repo/day policy lives in an untracked local config; absent → no-op (fail open).
# Global PreToolUse(Bash) hook: exits 0 cheaply for the common non-git case.
#
# PROVENANCE — this is phase 1 of two, and the copy is NOT yet the running gate.
# Byte-identical copy of the machine-local ~/.claude/.git-timing-guard.sh, taken 2026-08-25 at
#   sha256 14ad06c7da787c72299cd27dba6ab04684eda9b04d3082edc5aa472236ce618e
# The live PreToolUse registration in settings.json still points at that UNTRACKED original, not
# at this file, so editing here changes nothing about what the gate does today. The policy config
# (.git-timing-guard.conf) stays untracked by design: policy is local, logic is versioned.
#
# PHASE-2 PRECONDITION — before re-pointing the registration at this file, compare the LIVE
# file's current sha256 against the digest recorded above. Equal → re-point. Different → the live
# copy has drifted since this copy was taken; reconcile the two FIRST, because re-pointing a
# drifted pair silently reverts whatever was changed live. Re-pointing is held for the operator.
#
# DOCUMENTED EXCEPTION (not a defect to fix in this pass) — scripts/HOOKS.md requires hooks to be
# bash-3.2/BSD-safe with "no mapfile", and the segment split below uses `mapfile -t SEG` (line 40
# of the copied original). Measured under /bin/bash 3.2.57: the hook exits 127 with
# "mapfile: command not found", which is a non-2 exit and therefore FAILS OPEN per HOOKS.md's
# contract — it cannot false-block, but it also cannot gate under that interpreter. It runs
# because `env bash` resolves to a 4+/5.x bash here. Recorded rather than rewritten: this pass
# tracks existing behaviour unchanged, and replacing the split would alter a live gate's parsing.

conf="$HOME/.claude/.git-timing-guard.conf"
[ -f "$conf" ] || exit 0
command -v jq >/dev/null 2>&1 || exit 0

input=$(cat) || exit 0
cmd=$(printf '%s' "$input" | jq -r '.tool_input.command // empty' 2>/dev/null) || exit 0
cwd=$(printf '%s' "$input" | jq -r '.cwd // empty' 2>/dev/null) || exit 0
[ -n "$cmd" ] || exit 0

conf_get() {
  grep -E "^$1=" "$conf" 2>/dev/null | tail -1 | cut -d= -f2- | tr -d "\"' \\r"
}
repo_pat=$(conf_get GUARD_REPO_PATTERN || true)
[ -n "$repo_pat" ] || exit 0
start=$(conf_get GUARD_START || true); start=${start:-0800}
end=$(conf_get GUARD_END || true);     end=${end:-1700}
days=$(conf_get GUARD_DAYS || true);   days=${days:-1-5}

# Split the command into segments on && || ; | and newlines.
mapfile -t SEG < <(printf '%s\n' "$cmd" | sed -E 's/(&&|[|][|]|;|[|])/\n/g')

# Override: a leading ALLOW_GIT_WRITE=1 assignment on any segment.
for seg in "${SEG[@]}"; do
  if printf '%s' "$seg" | grep -qE '^[[:space:]]*ALLOW_GIT_WRITE=1([[:space:]]|$)'; then
    exit 0
  fi
done

# git, tolerating an env-var prefix and -C/-c/-<opt> globals, up to the subcommand.
# Only `push` is matched — see the publication-boundary note in the header. A local
# `commit` or `tag` is deliberately NOT detected here; it is not a partial gate, it
# is the whole policy: publishing waits for the window, working never does.
gp='(^|[^A-Za-z0-9_])git([[:space:]]+-C[[:space:]]+[^[:space:]]+|[[:space:]]+-c[[:space:]]+[^[:space:]]+|[[:space:]]+-[^[:space:]]+)*[[:space:]]+'
is_public=0
target="$cwd"
for seg in "${SEG[@]}"; do
  if printf '%s' "$seg" | grep -qE "${gp}push([^A-Za-z0-9_]|\$)"; then
    is_public=1
    cpath=$(printf '%s' "$seg" | grep -oE -- '-C[[:space:]]+[^[:space:]]+' | head -1 | sed -E 's/-C[[:space:]]+//' || true)
    [ -n "$cpath" ] && target="$cpath"
    break
  fi
done
[ "$is_public" = 1 ] || exit 0

# Scope: the target repo's origin must match the configured pattern.
origin=$(git -C "$target" remote get-url origin 2>/dev/null || true)
printf '%s' "$origin" | grep -qF "$repo_pat" || exit 0

# Time window — base-10 forced so 08/09 don't parse as octal.
dow=$(date +%u 2>/dev/null || true); [ -n "$dow" ] || exit 0
hh=$(date +%H 2>/dev/null || true);  [ -n "$hh" ] || exit 0
mm=$(date +%M 2>/dev/null || true);  [ -n "$mm" ] || exit 0
now=$((10#$hh * 60 + 10#$mm))
smin=$((10#${start:0:2} * 60 + 10#${start:2:2}))
emin=$((10#${end:0:2} * 60 + 10#${end:2:2}))
dlo=${days%%-*}; dhi=${days##*-}

if [ "$dow" -ge "$dlo" ] && [ "$dow" -le "$dhi" ] && [ "$now" -ge "$smin" ] && [ "$now" -lt "$emin" ]; then
  printf 'blocked by git timing guard: pushing is paused until %s local — publish after the window. Local commits and tags are not gated.\n' "$end" >&2
  exit 2
fi
exit 0

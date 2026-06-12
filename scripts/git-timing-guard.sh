#!/usr/bin/env bash
set -euo pipefail

# Script: git-timing-guard.sh
# Purpose: PreToolUse hook — block git writes outside a configured time window
# Usage: Called by Claude Code hooks with JSON on stdin
#
# Exit codes:
#   0 — allow (no config, not a git write, out of scope, inside the window, or any internal error)
#   2 — blocked: a git write in a configured repo during the blocked window (stderr fed back to Claude)
#
# The window/repo/day policy lives in an untracked local config; absent → no-op (fail open).
# Global PreToolUse(Bash) hook: exits 0 cheaply for the common non-git case.

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

# Override: a leading ALLOW_WORK_HOURS_GIT=1 assignment on any segment.
for seg in "${SEG[@]}"; do
  if printf '%s' "$seg" | grep -qE '^[[:space:]]*ALLOW_WORK_HOURS_GIT=1([[:space:]]|$)'; then
    exit 0
  fi
done

# git, tolerating an env-var prefix and -C/-c/-<opt> globals, up to the subcommand.
gp='(^|[^A-Za-z0-9_])git([[:space:]]+-C[[:space:]]+[^[:space:]]+|[[:space:]]+-c[[:space:]]+[^[:space:]]+|[[:space:]]+-[^[:space:]]+)*[[:space:]]+'
is_write=0
target="$cwd"
for seg in "${SEG[@]}"; do
  if printf '%s' "$seg" | grep -qE "${gp}(commit|push)([^A-Za-z0-9_]|\$)"; then
    is_write=1
  elif printf '%s' "$seg" | grep -qE "${gp}tag([^A-Za-z0-9_]|\$)"; then
    rest=$(printf '%s' "$seg" | sed -E "s/.*${gp}tag//")
    if printf '%s' "$rest" | grep -qE '(^|[[:space:]])(-l|--list|-d|--delete|-v|--verify)([[:space:]]|=|$)'; then
      : # listing / deleting / verifying — not a write
    elif printf '%s' "$rest" | grep -qE '(^|[[:space:]])(-a|-s|-m|-f)([[:space:]]|=|$)|[[:space:]][^[:space:]-][^[:space:]]*'; then
      is_write=1 # -a/-s/-m/-f or a bare name arg → creating a (signed/annotated) tag
    fi
  fi
  if [ "$is_write" = 1 ]; then
    cpath=$(printf '%s' "$seg" | grep -oE -- '-C[[:space:]]+[^[:space:]]+' | head -1 | sed -E 's/-C[[:space:]]+//' || true)
    [ -n "$cpath" ] && target="$cpath"
    break
  fi
done
[ "$is_write" = 1 ] || exit 0

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
  printf 'blocked by git timing guard: git writes are paused until %s local — commit/push after the window.\n' "$end" >&2
  exit 2
fi
exit 0

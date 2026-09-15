#!/usr/bin/env bash
set -euo pipefail

# Script: recast-test.sh
# Purpose: PostToolUse hook — run the matching recast test file when a recast source changes
# Usage: Called by Claude Code hooks with JSON on stdin
# Ownership sentinel (do not remove): dotclaude-test-runner-hook
#
# Exit codes:
#   0 — no action needed; or the suite is absent/unrunnable in a repo that does not own this hook;
#       or the matched test file passed (silent / brief note)
#   2 — the matched test file failed; or this repo owns the hook and its suite is missing or cannot
#       be run (stderr fed back to Claude to fix)
#
# Global hook: fires on every Edit|Write in every repo, so it exits 0 fast for anything that is not a
# recast source in a repo that carries the suite. Fast feedback only: it runs the ONE test file
# matching the edited script (a few seconds). Helper/conftest edits (whole-suite impact) are deferred
# to the commit-time gate (recast-commit-gate.py).

# ---------- Parse stdin JSON ----------
# HOOK CONTRACT: the target arrives as a JSON payload on stdin; argv is ignored. Refuse the two
# invocations this cannot serve, because each otherwise reads as SUCCESS — with argv and stdin at
# EOF it exits 0 having examined nothing, and with a terminal stdin it blocks forever.
if [ "$#" -gt 0 ] || [ -t 0 ]; then
  printf '%s\n' \
    "$(basename "$0") is a Claude Code hook: it reads a JSON payload on stdin and ignores arguments." \
    "Running it with filenames examines nothing. See scripts/HOOKS.md for the payload form." >&2
  exit 2
fi

input=$(cat)
file_path=$(printf '%s' "$input" | jq -r '.tool_input.file_path // empty' 2>/dev/null) || file_path=""

if [[ -z "$file_path" ]]; then
  exit 0
fi

# ---------- Cheap guard: only recast .sh/.py matters ----------
# '*' spans '/' in case patterns, so these match at any depth.
case "$file_path" in
  */skills/recast/*.sh|*/skills/recast/*.py) ;;
  *) exit 0 ;;
esac

# ---------- Identify subsystem and the matching test file ----------
sub=${file_path##*/skills/}
sub=${sub%%/*}                         # recast
base=${file_path##*/}                  # e.g. recast-recon-history.sh, test_recast_recon.py

case "$base" in
  test_*.py)
    testfile=$base                     # editing a test → run that test file
    ;;
  *.sh)
    # recast-recon-history.sh → test_recast_recon_history.py
    stem=${base%.sh}
    testfile="test_${stem//-/_}.py"
    ;;
  *)
    # Helper / conftest / other .py: whole-suite impact, deferred to the commit gate.
    exit 0
    ;;
esac

# ---------- Resolve repo root ----------
root=$(git -C "$(dirname "$file_path")" rev-parse --show-toplevel 2>/dev/null || true)

# Environment fail-open: not a git repo at all. Deliberate, unchanged.
if [[ -z "$root" ]]; then
  exit 0
fi

# A suite that is MISSING and one that is PRESENT-but-unrunnable (no pytest) are the same event:
# the gate did not run. Collected before either branch below, so the ownership check decides
# whether to alarm — a machine without pytest, or a repo that simply doesn't carry this suite, is
# never blocked in a repo that does not own this hook.
missing=""
if [[ ! -f "$root/skills/$sub/tests/$testfile" ]]; then
  missing="no skills/$sub/tests/$testfile for $base"
elif ! python3 -c 'import pytest' >/dev/null 2>&1; then
  missing="skills/$sub/tests/$testfile is present, but pytest is unavailable"
fi

# ---------- Ownership guard: alarm only where this repo owns this hook ----------
# Ownership is proven by the hook finding its OWN source at its own relative path — nothing is
# required from the absent/unrunnable suite, so a deletion cannot conceal itself. Unlike its
# siblings, recast's trigger set is OPEN (*/skills/recast/*.sh) and its suite is derived PER FILE,
# so "the hook was deleted" is not the likely cause here: the ordinary trigger is a new
# skills/recast/recast-x.sh written before its test — TDD-consistent, and intended to alarm.
if [[ -n "$missing" ]]; then
  if grep -q 'dotclaude-test-runner-hook' "$root/scripts/$(basename "$0")" 2>/dev/null; then
    printf '%s\n' \
      "GATE DID NOT RUN — this repo owns $(basename "$0") but ${missing}." \
      "If $base is new, write skills/$sub/tests/$testfile before continuing." \
      "If the suite was removed deliberately, remove $(basename "$0")'s settings.json registration, then re-run /sync-docs." >&2
    exit 2
  fi
  exit 0
fi

# ---------- Run the matching test file (set -e-safe exit capture) ----------
args=(-q)
if python3 -c 'import xdist' >/dev/null 2>&1; then
  args+=(-n auto)
fi
output=$(cd "$root/skills/$sub" && python3 -m pytest "tests/$testfile" "${args[@]}" 2>&1) && rc=0 || rc=$?

if [[ "$rc" -ne 0 ]]; then
  printf '%s tests FAILED after editing %s:\n' "$sub" "$file_path" >&2
  printf '%s\n' "$output" | tail -20 >&2
  exit 2
fi

printf '%s test passed (%s): %s\n' "$sub" "$testfile" "$(printf '%s\n' "$output" | tail -1)"
exit 0

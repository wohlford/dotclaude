#!/usr/bin/env bash
set -euo pipefail

# Script: recast-test.sh
# Purpose: PostToolUse hook — run the matching recast test file when a recast source changes
# Usage: Called by Claude Code hooks with JSON on stdin
#
# Exit codes:
#   0 — no action needed, or the matched test file passed (silent / brief note)
#   2 — the matched test file failed (stderr fed back to Claude to fix)
#
# Global hook: fires on every Edit|Write in every repo, so it exits 0 fast for anything that is not a
# recast source in a repo that carries the suite. Fast feedback only: it runs the ONE test file
# matching the edited script (a few seconds). Helper/conftest edits (whole-suite impact) are deferred
# to the commit-time gate (recast-commit-gate.py).

# ---------- Parse stdin JSON ----------
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

# ---------- Resolve repo root; act only where the matching test file lives ----------
root=$(git -C "$(dirname "$file_path")" rev-parse --show-toplevel 2>/dev/null || true)
if [[ -z "$root" ]] || [[ ! -f "$root/skills/$sub/tests/$testfile" ]]; then
  exit 0
fi

# ---------- Availability guard: no pytest → can't test, never falsely block ----------
if ! python3 -c 'import pytest' >/dev/null 2>&1; then
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

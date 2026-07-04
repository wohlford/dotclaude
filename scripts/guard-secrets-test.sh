#!/usr/bin/env bash
set -euo pipefail

# Script: guard-secrets-test.sh
# Purpose: PostToolUse hook — run the guard-secrets test suite when the guard changes
# Usage: Called by Claude Code hooks with JSON on stdin
#
# Exit codes:
#   0 — no action needed, or tests passed (silent / brief note)
#   2 — guard-secrets tests failed (stderr fed back to Claude to fix)
#
# Global hook: fires on every Edit|Write in every repo, so guards run cheapest-first
# and exit 0 fast for anything that is not guard-secrets.sh (or its test) in a repo
# that actually carries the suite.

# ---------- Parse stdin JSON ----------
input=$(cat)
file_path=$(printf '%s' "$input" | jq -r '.tool_input.file_path // empty' 2>/dev/null) || file_path=""

if [[ -z "$file_path" ]]; then
  exit 0
fi

# ---------- Cheap guard: only guard-secrets.sh or its test matters ----------
case "$file_path" in
  */scripts/guard-secrets.sh|*/scripts/tests/test_guard_secrets.sh) ;;
  *) exit 0 ;;
esac

# ---------- Resolve repo root; act only where the suite lives ----------
root=$(git -C "$(dirname "$file_path")" rev-parse --show-toplevel 2>/dev/null || true)
if [[ -z "$root" ]] || [[ ! -f "$root/scripts/tests/test_guard_secrets.sh" ]]; then
  exit 0
fi

# ---------- Run the suite (set -e-safe exit capture) ----------
output=$(cd "$root" && bash scripts/tests/test_guard_secrets.sh 2>&1) && rc=0 || rc=$?

if [[ "$rc" -ne 0 ]]; then
  printf 'guard-secrets tests FAILED after editing %s:\n' "$file_path" >&2
  printf '%s\n' "$output" | tail -20 >&2
  exit 2
fi

printf 'guard-secrets tests passed: %s\n' "$(printf '%s\n' "$output" | tail -1)"
exit 0

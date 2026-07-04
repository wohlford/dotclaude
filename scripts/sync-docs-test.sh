#!/usr/bin/env bash
set -euo pipefail

# Script: sync-docs-test.sh
# Purpose: PostToolUse hook — run the sync-docs test suite when its Python changes
# Usage: Called by Claude Code hooks with JSON on stdin
#
# Exit codes:
#   0 — no action needed, or tests passed (silent / brief note)
#   2 — sync-docs tests failed (stderr fed back to Claude to fix)
#
# Global hook: fires on every Edit|Write in every repo, so guards run cheapest-first
# and exit 0 fast for anything that is not a sync-docs Python file in a sync-docs repo.

# ---------- Parse stdin JSON ----------
input=$(cat)
file_path=$(printf '%s' "$input" | jq -r '.tool_input.file_path // empty' 2>/dev/null) || file_path=""

if [[ -z "$file_path" ]]; then
  exit 0
fi

# ---------- Cheap guard: only sync-docs Python (tool or tests) matters ----------
# The glob matches deeper paths too (tests/*.py), since '*' spans '/' in case patterns.
case "$file_path" in
  */skills/sync-docs/*.py) ;;
  *) exit 0 ;;
esac

# ---------- Resolve repo root; act only where the sync-docs suite lives ----------
root=$(git -C "$(dirname "$file_path")" rev-parse --show-toplevel 2>/dev/null || true)
if [[ -z "$root" ]] || [[ ! -d "$root/skills/sync-docs/tests" ]]; then
  exit 0
fi

# ---------- Availability guard: no pytest → can't test, never falsely block ----------
if ! python3 -c 'import pytest' >/dev/null 2>&1; then
  exit 0
fi

# ---------- Run the suite (set -e-safe exit capture) ----------
output=$(cd "$root/skills/sync-docs" && python3 -m pytest tests/ -q 2>&1) && rc=0 || rc=$?

if [[ "$rc" -ne 0 ]]; then
  printf 'sync-docs tests FAILED after editing %s:\n' "$file_path" >&2
  printf '%s\n' "$output" | tail -20 >&2
  exit 2
fi

printf 'sync-docs tests passed: %s\n' "$(printf '%s\n' "$output" | tail -1)"
exit 0

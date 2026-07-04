#!/usr/bin/env bash
set -euo pipefail

# Script: md-links-check-test.sh
# Purpose: PostToolUse hook — run the md-links-check test suite when the checker changes
# Usage: Called by Claude Code hooks with JSON on stdin
#
# Exit codes:
#   0 — no action needed, or tests passed (silent / brief note)
#   2 — md-links-check tests failed (stderr fed back to Claude to fix)
#
# Global hook: fires on every Edit|Write in every repo, so guards run cheapest-first
# and exit 0 fast for anything that is not md-links-check.py (or its test) in a repo
# that actually carries the suite.

# ---------- Parse stdin JSON ----------
input=$(cat)
file_path=$(printf '%s' "$input" | jq -r '.tool_input.file_path // empty' 2>/dev/null) || file_path=""

if [[ -z "$file_path" ]]; then
  exit 0
fi

# ---------- Cheap guard: only the checker or its test matters ----------
case "$file_path" in
  */scripts/md-links-check.py|*/scripts/tests/test_md_links_check.sh) ;;
  *) exit 0 ;;
esac

# ---------- Resolve repo root; act only where the suite lives ----------
root=$(git -C "$(dirname "$file_path")" rev-parse --show-toplevel 2>/dev/null || true)
if [[ -z "$root" ]] || [[ ! -f "$root/scripts/tests/test_md_links_check.sh" ]]; then
  exit 0
fi

# ---------- Dependencies ----------
# The suite needs python3 (it drives the checker with it) and skips itself politely
# when python3 is absent, so no extra guard is needed here.

# ---------- Run the suite (set -e-safe exit capture) ----------
output=$(cd "$root" && bash scripts/tests/test_md_links_check.sh 2>&1) && rc=0 || rc=$?

if [[ "$rc" -ne 0 ]]; then
  printf 'md-links-check tests FAILED after editing %s:\n' "$file_path" >&2
  printf '%s\n' "$output" | tail -20 >&2
  exit 2
fi

printf 'md-links-check tests passed: %s\n' "$(printf '%s\n' "$output" | tail -1)"
exit 0

#!/usr/bin/env bash
set -euo pipefail

# Script: exec-bit-guard-test.sh
# Purpose: PostToolUse hook — run the exec-bit-guard test suite when the gate or its suite changes
# Usage: Called by Claude Code hooks with JSON on stdin
#
# Exit codes:
#   0 — allow (not a guard file, suite passed, or any internal error → fail open)
#   2 — blocked: the exec-bit-guard test suite fails after this edit (stderr fed back to Claude)

command -v jq >/dev/null 2>&1 || exit 0
input=$(cat) || exit 0
file_path=$(printf '%s' "$input" | jq -r '.tool_input.file_path // empty' 2>/dev/null) || exit 0
[ -n "$file_path" ] || exit 0

case "$file_path" in
  */scripts/exec-bit-guard.sh|*/scripts/tests/test_exec_bit_guard.sh) ;;
  *) exit 0 ;;
esac

root=$(git -C "$(dirname "$file_path")" rev-parse --show-toplevel 2>/dev/null) || exit 0
if [ -z "$root" ] || [ ! -f "$root/scripts/tests/test_exec_bit_guard.sh" ]; then
  exit 0
fi

output=$(cd "$root" && bash scripts/tests/test_exec_bit_guard.sh 2>&1) && rc=0 || rc=$?
if [ "$rc" -ne 0 ]; then
  printf 'exec-bit-guard test suite FAILED after this edit:\n' >&2
  printf '%s\n' "$output" | tail -40 >&2
  exit 2
fi
exit 0

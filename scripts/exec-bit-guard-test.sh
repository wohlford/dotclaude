#!/usr/bin/env bash
set -euo pipefail

# Script: exec-bit-guard-test.sh
# Purpose: PostToolUse hook — run the exec-bit-guard test suite when the gate or its suite changes
# Usage: Called by Claude Code hooks with JSON on stdin
# Ownership sentinel (do not remove): dotclaude-test-runner-hook
#
# Exit codes:
#   0 — allow (not a guard file, suite passed, or any internal error → fail open)
#   2 — blocked: the exec-bit-guard test suite fails after this edit (stderr fed back to Claude)

command -v jq >/dev/null 2>&1 || exit 0
# HOOK CONTRACT: the target arrives as a JSON payload on stdin; argv is ignored. Refuse the two
# invocations this cannot serve, because each otherwise reads as SUCCESS — with argv and stdin at
# EOF it exits 0 having examined nothing, and with a terminal stdin it blocks forever.
if [ "$#" -gt 0 ] || [ -t 0 ]; then
  printf '%s\n' \
    "$(basename "$0") is a Claude Code hook: it reads a JSON payload on stdin and ignores arguments." \
    "Running it with filenames examines nothing. See scripts/HOOKS.md for the payload form." >&2
  exit 2
fi

input=$(cat) || exit 0
file_path=$(printf '%s' "$input" | jq -r '.tool_input.file_path // empty' 2>/dev/null) || exit 0
[ -n "$file_path" ] || exit 0

case "$file_path" in
  */scripts/exec-bit-guard.sh|*/scripts/tests/test_exec_bit_guard.sh) ;;
  *) exit 0 ;;
esac

root=$(git -C "$(dirname "$file_path")" rev-parse --show-toplevel 2>/dev/null) || exit 0

# Environment fail-open: not a git repo at all. Deliberate, unchanged.
if [ -z "$root" ]; then
  exit 0
fi

# A missing suite is inert ONLY where this repo does not own these hooks. Ownership is proven by
# the hook finding its OWN source at its own relative path — nothing is required from the suite
# whose absence is the very thing in question, so a deletion cannot conceal itself. Where the repo
# does own it, the suite was DELETED and the gate did not run: alarm, because a deleted test must
# never be quieter than a failing one.
if [ ! -f "$root/scripts/tests/test_exec_bit_guard.sh" ]; then
  if grep -q 'dotclaude-test-runner-hook' "$root/scripts/$(basename "$0")" 2>/dev/null; then
    printf '%s\n' \
      "MISSING SUITE: scripts/tests/test_exec_bit_guard.sh is absent, but this repo owns $(basename "$0") — the gate did NOT run." \
      "To remove this feature deliberately: delete its hook, remove its settings.json registration, then re-run /sync-docs." >&2
    exit 2
  fi
  exit 0
fi

output=$(cd "$root" && bash scripts/tests/test_exec_bit_guard.sh 2>&1) && rc=0 || rc=$?
if [ "$rc" -ne 0 ]; then
  printf 'exec-bit-guard test suite FAILED after this edit:\n' >&2
  printf '%s\n' "$output" | tail -40 >&2
  exit 2
fi
exit 0

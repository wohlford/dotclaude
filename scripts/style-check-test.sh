#!/usr/bin/env bash
set -euo pipefail

# Script: style-check-test.sh
# Purpose: PostToolUse hook — run the style-check test suite when style-check changes
# Usage: Called by Claude Code hooks with JSON on stdin
# Ownership sentinel (do not remove): dotclaude-test-runner-hook
#
# Exit codes:
#   0 — no action needed, or tests passed (silent / brief note)
#   2 — style-check tests failed (stderr fed back to Claude to fix)
#
# Global hook: fires on every Edit|Write in every repo, so guards run cheapest-first
# and exit 0 fast for anything that is not style-check.sh (or its test) in a repo that
# actually carries the suite.

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

# ---------- Cheap guard: only style-check.sh or its test matters ----------
# '*' spans '/' in case patterns, so these match the file at any depth.
case "$file_path" in
  */scripts/style-check.sh|*/scripts/tests/test_style_check.sh) ;;
  *) exit 0 ;;
esac

# ---------- Resolve repo root; act only where the suite lives ----------
root=$(git -C "$(dirname "$file_path")" rev-parse --show-toplevel 2>/dev/null || true)

# Environment fail-open: not a git repo at all. Deliberate, unchanged.
if [[ -z "$root" ]]; then
  exit 0
fi

# A missing suite is inert ONLY where this repo does not own these hooks. Ownership is proven by
# the hook finding its OWN source at its own relative path — nothing is required from the suite
# whose absence is the very thing in question, so a deletion cannot conceal itself. Where the repo
# does own it, the suite was DELETED and the gate did not run: alarm, because a deleted test must
# never be quieter than a failing one.
if [[ ! -f "$root/scripts/tests/test_style_check.sh" ]]; then
  if grep -q 'dotclaude-test-runner-hook' "$root/scripts/$(basename "$0")" 2>/dev/null; then
    printf '%s\n' \
      "MISSING SUITE: scripts/tests/test_style_check.sh is absent, but this repo owns $(basename "$0") — the gate did NOT run." \
      "To remove this feature deliberately: delete its hook, remove its settings.json registration, then re-run /sync-docs." >&2
    exit 2
  fi
  exit 0
fi

# ---------- Dependencies ----------
# jq is already required above to parse stdin (as in every hook), and the suite drives
# style-check with .md files only — needing just grep/tail/wc/awk beyond that. So there
# is no extra optional dependency to guard here. If the suite ever gains .sh/.py/.yaml
# cases, style-check would need shellcheck/python3/yamllint, and a guard for those (exit 0
# when absent, to avoid a false block) should be added before running it.

# ---------- Run the suite (set -e-safe exit capture) ----------
output=$(cd "$root" && bash scripts/tests/test_style_check.sh 2>&1) && rc=0 || rc=$?

if [[ "$rc" -ne 0 ]]; then
  printf 'style-check tests FAILED after editing %s:\n' "$file_path" >&2
  printf '%s\n' "$output" | tail -20 >&2
  exit 2
fi

printf 'style-check tests passed: %s\n' "$(printf '%s\n' "$output" | tail -1)"
exit 0

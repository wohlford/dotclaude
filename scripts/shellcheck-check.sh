#!/usr/bin/env bash
set -euo pipefail

# Script: shellcheck-check.sh
# Purpose: PostToolUse hook — run shellcheck on edited shell scripts
# Usage: Called by Claude Code hooks with JSON on stdin
#
# Exit codes:
#   0 — no action needed, or shellcheck clean (silent)
#   2 — shellcheck reported warnings/errors (stderr fed back to Claude)
#
# Global hook: fires on every Edit|Write in every repo, so guards run cheapest-first
# and exit 0 fast for anything that is not an existing shell script lintable here.

# ---------- Parse stdin JSON ----------
input=$(cat)
file_path=$(printf '%s' "$input" | jq -r '.tool_input.file_path // empty' 2>/dev/null) || file_path=""

if [[ -z "$file_path" ]]; then
  exit 0
fi

# ---------- Cheap guard: only shell scripts ----------
case "$file_path" in
  *.sh) ;;
  *) exit 0 ;;
esac

# ---------- Existence guard: skip deleted/renamed files ----------
if [[ ! -f "$file_path" ]]; then
  exit 0
fi

# ---------- Availability guard: no shellcheck → can't lint, never falsely block ----------
if ! command -v shellcheck >/dev/null 2>&1; then
  exit 0
fi

# ---------- Run shellcheck (set -e-safe exit capture) ----------
# -S warning: report warnings + errors only (skip style/info, which style-check.sh covers).
output=$(shellcheck -S warning "$file_path" 2>&1) && rc=0 || rc=$?

if [[ "$rc" -ne 0 ]]; then
  printf 'shellcheck flagged %s:\n' "$file_path" >&2
  printf '%s\n' "$output" | tail -40 >&2
  exit 2
fi

exit 0

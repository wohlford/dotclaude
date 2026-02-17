#!/usr/bin/env bash
set -euo pipefail

# Script: style-check.sh
# Purpose: Global PostToolUse hook — validate file edits against STYLE.md
# Usage: Called by Claude Code hooks with JSON on stdin
#
# Exit codes:
#   0 — all checks passed (stdout added to context)
#   2 — checks failed (stderr fed back to Claude for correction)

# ---------- Parse stdin JSON ----------
input=$(cat)
file_path=$(echo "$input" | jq -r '.tool_input.file_path // empty')

if [[ -z "$file_path" ]]; then
  exit 0
fi

if [[ ! -f "$file_path" ]]; then
  exit 0
fi

# ---------- Helpers ----------
errors=()

add_error() {
  errors+=("$1")
}

check_final_newline() {
  if [[ -s "$file_path" ]] && [[ "$(tail -c 1 "$file_path" | wc -l)" -eq 0 ]]; then
    add_error "Missing final newline"
  fi
}

check_no_tabs() {
  local tab_lines
  tab_lines=$(grep -n $'^\t' "$file_path" 2>/dev/null || true)
  if [[ -n "$tab_lines" ]]; then
    add_error "Tab indentation found (use 2 spaces):\n${tab_lines}"
  fi
}

# ---------- Route by extension ----------
ext="${file_path##*.}"

case "$ext" in
  py)
    check_no_tabs
    if ! python3 -m py_compile "$file_path" 2>&1; then
      add_error "Python syntax error"
    fi
    check_final_newline
    ;;

  sh|bash)
    # Check shebang
    if ! head -1 "$file_path" | grep -q '^#!'; then
      add_error "Missing shebang line"
    fi
    # Check set -euo pipefail in first 5 lines
    if ! head -5 "$file_path" | grep -q 'set -euo pipefail'; then
      add_error "Missing 'set -euo pipefail' in first 5 lines"
    fi
    # Run shellcheck if available
    if command -v shellcheck >/dev/null 2>&1; then
      sc_output=$(shellcheck -f gcc "$file_path" 2>&1 || true)
      if [[ -n "$sc_output" ]]; then
        add_error "shellcheck warnings:\n${sc_output}"
      fi
    fi
    check_final_newline
    ;;

  js|mjs|cjs)
    check_no_tabs
    check_final_newline
    ;;

  json)
    if ! python3 -m json.tool "$file_path" >/dev/null 2>&1; then
      add_error "Invalid JSON"
    fi
    check_final_newline
    ;;

  yaml|yml)
    if command -v yamllint >/dev/null 2>&1; then
      yl_output=$(yamllint -d relaxed "$file_path" 2>&1 || true)
      if [[ -n "$yl_output" ]]; then
        add_error "yamllint warnings:\n${yl_output}"
      fi
    fi
    check_final_newline
    ;;

  md)
    check_final_newline
    ;;

  *)
    # No checks for unrecognized extensions
    exit 0
    ;;
esac

# ---------- Report results ----------
if [[ ${#errors[@]} -gt 0 ]]; then
  echo "style-check FAILED for ${file_path}:" >&2
  for err in "${errors[@]}"; do
    echo -e "  - ${err}" >&2
  done
  exit 2
fi

echo "style-check passed: ${file_path}"
exit 0

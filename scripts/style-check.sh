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

check_no_trailing_ws() {
  # Exempt Claude Code's auto-memory files. The memory subsystem augments
  # frontmatter after each write (injecting node_type/originSessionId) and
  # leaves the `metadata:` key with a trailing space, which re-triggers this
  # check on every memory write — busywork to strip each time. Upstream-correct
  # fix is in the memory writer (don't emit trailing whitespace when
  # augmenting); this is a workaround — revert it when the writer stops emitting
  # trailing whitespace. Anchored on the real config dir (CLAUDE_CONFIG_DIR, else
  # ~/.claude) so only the genuine memory tree is exempt — not an unrelated
  # */projects/*/memory/*.md elsewhere — and the check stays intact everywhere else.
  local mem_base="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/projects"
  case "$file_path" in
    "$mem_base"/*/memory/*.md)
      return 0
      ;;
  esac
  local ws_lines
  ws_lines=$(grep -nE '[[:blank:]]+$' "$file_path" 2>/dev/null || true)
  if [[ -n "$ws_lines" ]]; then
    add_error "Trailing whitespace found:\n${ws_lines}"
  fi
}

# ---------- Frontmatter checks (skills, agents) ----------
case "$file_path" in
  */tests/fixtures/*)
    : # test fixtures are intentionally varied — never validate their frontmatter
    ;;
  */agents/README.md|*/agents/index.md)
    : # index READMEs carry no frontmatter (mirrors AgentsHandler excludes)
    ;;
  */skills/*/SKILL.md|*/agents/*.md)
    if [[ "$(head -1 "$file_path")" != "---" ]]; then
      add_error "Missing YAML frontmatter (file must start with '---')"
    else
      frontmatter=$(awk '/^---$/{c++; next} c==1' "$file_path")
      if ! grep -q '^name:' <<<"$frontmatter"; then
        add_error "Frontmatter missing 'name:' field"
      fi
      if ! grep -q '^description:' <<<"$frontmatter"; then
        add_error "Frontmatter missing 'description:' field"
      fi
    fi
    ;;
esac

# ---------- Route by extension ----------
ext="${file_path##*.}"

case "$ext" in
  py)
    check_no_tabs
    check_no_trailing_ws
    if ! python3 -m py_compile "$file_path" 2>&1; then
      add_error "Python syntax error"
    fi
    check_final_newline
    ;;

  sh|bash)
    check_no_tabs
    check_no_trailing_ws
    # Check shebang
    if ! head -1 "$file_path" | grep -q '^#!'; then
      add_error "Missing shebang line"
    fi
    # Check set -euo pipefail in first 5 lines
    # Allow set -uo pipefail (without -e) for test harnesses that need to
    # continue execution past assertion failures.
    if ! head -5 "$file_path" | grep -qE 'set -e?uo pipefail'; then
      add_error "Missing 'set -euo pipefail' in first 5 lines"
    fi
    # Run shellcheck if available
    if command -v shellcheck >/dev/null 2>&1; then
      sc_output=$(shellcheck -x --source-path="$(dirname "$file_path")" -e SC1091 -f gcc "$file_path" 2>&1 || true)
      if [[ -n "$sc_output" ]]; then
        add_error "shellcheck warnings:\n${sc_output}"
      fi
    fi
    check_final_newline
    ;;

  js|mjs|cjs)
    check_no_tabs
    check_no_trailing_ws
    check_final_newline
    ;;

  json)
    check_no_tabs
    check_no_trailing_ws
    if ! python3 -m json.tool "$file_path" >/dev/null 2>&1; then
      add_error "Invalid JSON"
    fi
    check_final_newline
    ;;

  yaml|yml)
    check_no_tabs
    check_no_trailing_ws
    if command -v yamllint >/dev/null 2>&1; then
      yl_output=$(yamllint -d relaxed "$file_path" 2>&1 || true)
      if [[ -n "$yl_output" ]]; then
        add_error "yamllint warnings:\n${yl_output}"
      fi
    fi
    check_final_newline
    ;;

  md)
    check_no_tabs
    check_no_trailing_ws
    check_final_newline
    ;;

  *)
    # No checks for unrecognized extensions
    exit 0
    ;;
esac

# ---------- Report results ----------
if [[ ${#errors[@]} -gt 0 ]]; then
  printf 'style-check FAILED for %s:\n' "$file_path" >&2
  for err in "${errors[@]}"; do
    printf '  - %b\n' "$err" >&2
  done
  exit 2
fi

printf 'style-check passed: %s\n' "$file_path"
exit 0

#!/usr/bin/env bash
set -euo pipefail

# Script: ruff-check.sh
# Purpose: PostToolUse hook — run ruff lint+format check on edited Python in ruff projects
# Usage: Called by Claude Code hooks with JSON on stdin
#
# Exit codes:
#   0 — no action needed, or ruff clean (silent)
#   2 — ruff reported lint errors or formatting drift (combined stdout+stderr fed to Claude;
#       `ruff format --diff` writes the diff to stdout, so capture merges both streams)
#
# Global hook: fires on every Edit|Write in every repo, so guards run cheapest-first and
# exit 0 fast for anything that is not an editable .py in a repo that opted into ruff
# (carries a ruff config). Indentation/line-length is a `ruff format` concern; lint rules
# are a `ruff check` concern — this runs both.

# ---------- Parse stdin JSON ----------
input=$(cat)
file_path=$(printf '%s' "$input" | jq -r '.tool_input.file_path // empty' 2>/dev/null) || file_path=""

if [[ -z "$file_path" ]]; then
  exit 0
fi

# ---------- Cheap guard: only Python files ----------
case "$file_path" in
  *.py) ;;
  *) exit 0 ;;
esac

# ---------- Existence guard: skip deleted/renamed files ----------
if [[ ! -f "$file_path" ]]; then
  exit 0
fi

# ---------- Availability guard: no ruff → can't lint, never falsely block ----------
if ! command -v ruff >/dev/null 2>&1; then
  exit 0
fi

# ---------- Config gate (model A): only act in repos that opted into ruff ----------
# Walk from the file's directory up to the filesystem root for a ruff config: ruff.toml,
# .ruff.toml, or a pyproject.toml carrying a [tool.ruff] table. No config → the project
# hasn't adopted our baseline, so stay silent (polite global hook).
dir=$(cd "$(dirname "$file_path")" && pwd)
have_config=0
while true; do
  if [[ -f "$dir/ruff.toml" || -f "$dir/.ruff.toml" ]]; then
    have_config=1
    break
  fi
  if [[ -f "$dir/pyproject.toml" ]] && grep -q '^\[tool\.ruff' "$dir/pyproject.toml"; then
    have_config=1
    break
  fi
  [[ "$dir" == "/" ]] && break
  dir=$(dirname "$dir")
done
if [[ "$have_config" -eq 0 ]]; then
  exit 0
fi

# ---------- Run ruff: lint + format-check (set -e-safe capture) ----------
rc=0
report=""
if ! out=$(ruff check "$file_path" 2>&1); then
  rc=2
  report+="$out"$'\n'
fi
if ! out=$(ruff format --diff "$file_path" 2>&1); then
  rc=2
  report+="$out"$'\n'
fi

if [[ "$rc" -ne 0 ]]; then
  printf 'ruff flagged %s:\n' "$file_path" >&2
  printf '%s' "$report" | tail -60 >&2
  exit 2
fi

exit 0

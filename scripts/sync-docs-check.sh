#!/usr/bin/env bash
set -euo pipefail

# Script: sync-docs-check.sh
# Purpose: PostToolUse hook — block edits that leave /sync-docs index tables drifted
# Usage: Called by Claude Code hooks with JSON on stdin
#
# Exit codes:
#   0 — no action needed, or no drift (silent)
#   2 — drift detected, or the checker itself errored — e.g. an edit broke a
#       <!-- sync:* --> marker region (stderr fed back to Claude to fix)
#
# Global hook: fires on every Edit|Write in every repo, so guards run cheapest-first
# and exit 0 fast for anything that is not a skill/agent file in a sync-docs repo.

# ---------- Parse stdin JSON ----------
input=$(cat)
file_path=$(printf '%s' "$input" | jq -r '.tool_input.file_path // empty' 2>/dev/null) || file_path=""

if [[ -z "$file_path" ]]; then
  exit 0
fi

# ---------- Cheap guard: only skill/agent files matter (before spawning git) ----------
case "$file_path" in
  */skills/*/SKILL.md|*/agents/*.md) ;;
  *) exit 0 ;;
esac

# ---------- Resolve repo root; act only where sync-docs lives ----------
root=$(git -C "$(dirname "$file_path")" rev-parse --show-toplevel 2>/dev/null || true)
if [[ -z "$root" ]] || [[ ! -f "$root/skills/sync-docs/sync_docs.py" ]]; then
  exit 0
fi

# ---------- Check for index drift (set -e-safe exit capture) ----------
rc=0
python3 "$root/skills/sync-docs/sync_docs.py" --check --scope "$root" >/dev/null 2>&1 || rc=$?

if [[ "$rc" -eq 1 ]]; then
  printf 'Index drift in <!-- sync:* --> tables. Regenerate with: python3 skills/sync-docs/sync_docs.py\n' >&2
  exit 2
fi

# rc 2+ is a parser/handler error — e.g. the edit broke a marker region. Letting it
# through would silently exempt that block from every future sync — block it too.
if [[ "$rc" -ge 2 ]]; then
  printf 'sync-docs parse error (broken <!-- sync:* --> region?). Diagnose with: python3 skills/sync-docs/sync_docs.py --check\n' >&2
  exit 2
fi

exit 0

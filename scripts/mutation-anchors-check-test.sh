#!/usr/bin/env bash
set -euo pipefail

# Script: mutation-anchors-check-test.sh
# Purpose: PostToolUse hook — run the mutation-anchors-check test suite when the checker changes
# Usage: Called by Claude Code hooks with JSON on stdin
# Ownership sentinel (do not remove): dotclaude-test-runner-hook
#
# Exit codes:
#   0 — no action needed, or tests passed (silent / brief note)
#   2 — mutation-anchors-check tests failed (stderr fed back to Claude to fix)
#
# Global hook: fires on every Edit|Write in every repo, so guards run cheapest-first and exit 0
# fast for anything that is not mutation-anchors-check.py (or its suite) in a repo that actually
# carries the suite.
#
# NOTE this hook takes its target from the JSON payload on stdin and IGNORES argv entirely. Run
# as a CLI (`mutation-anchors-check-test.sh some/file`) it examines nothing: with stdin at EOF it
# exits 0, which reads exactly like a clean pass, and with stdin inherited it blocks forever. To
# drive it by hand, feed it a payload:
#   printf '{"tool_input":{"file_path":"%s"}}' "$PWD/scripts/mutation-anchors-check.py" | \
#     bash scripts/mutation-anchors-check-test.sh

# ---------- Parse stdin JSON ----------
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

if [[ -z "$file_path" ]]; then
  exit 0
fi

# ---------- Cheap guard: only the checker or its suite matters ----------
case "$file_path" in
  */scripts/mutation-anchors-check.py | \
    */scripts/tests/test_mutation_anchors_check.py) ;;
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
if [[ ! -f "$root/scripts/tests/test_mutation_anchors_check.py" ]]; then
  if grep -q 'dotclaude-test-runner-hook' "$root/scripts/$(basename "$0")" 2>/dev/null; then
    printf '%s\n' \
      "MISSING SUITE: scripts/tests/test_mutation_anchors_check.py is absent, but this repo owns $(basename "$0") — the gate did NOT run." \
      "To remove this feature deliberately: delete its hook, remove its settings.json registration, then re-run /sync-docs." >&2
    exit 2
  fi
  exit 0
fi

# ---------- Dependencies ----------
command -v python3 >/dev/null 2>&1 || exit 0
python3 -m pytest --version >/dev/null 2>&1 || exit 0

# ---------- Run the suite (set -e-safe exit capture) ----------
output=$(cd "$root" && python3 -m pytest scripts/tests/test_mutation_anchors_check.py -q \
  --no-header 2>&1) && rc=0 || rc=$?

if [[ "$rc" -ne 0 ]]; then
  printf 'mutation-anchors-check tests FAILED after editing %s:\n' "$file_path" >&2
  printf '%s\n' "$output" | tail -20 >&2
  exit 2
fi

printf 'mutation-anchors-check tests passed: %s\n' "$(printf '%s\n' "$output" | tail -1)"
exit 0

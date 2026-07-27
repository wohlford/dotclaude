#!/usr/bin/env bash
set -euo pipefail

# Script: publication-push-guard-test.sh
# Purpose: PostToolUse hook — run the publication-push-guard suite when the guard, its suite, or the shared git_command tokenizer changes
# Usage: Called by Claude Code hooks with JSON on stdin
# Ownership sentinel (do not remove): dotclaude-test-runner-hook
#
# Exit codes:
#   0 — no action needed, or the run suite(s) passed (silent / brief note)
#   2 — a run suite failed (stderr fed back to Claude to fix)
#
# Dependency graph: scripts/lib/git_command.py is the shared tokenizer imported by BOTH
# publication-push-guard.py (tested here) and recast-commit-gate.py (tested by
# scripts/tests/test_recast_hooks.sh). Editing git_command.py therefore re-runs ALL THREE of its
# dependents' suites: its own unit tests, the publication-push-guard suite, and the recast-hooks
# suite. test_recast_hooks.sh is the slow one (it boots sandbox repos) — that's intentional
# shared-dep coverage for a rare edit, not a mistake to optimize away.
#
# Global hook: fires on every Edit|Write in every repo, so it exits 0 fast for anything that is
# not one of the paths above in a repo that carries the suites.

# ---------- Parse stdin JSON ----------
input=$(cat)
file_path=$(printf '%s' "$input" | jq -r '.tool_input.file_path // empty' 2>/dev/null) || file_path=""

if [[ -z "$file_path" ]]; then
  exit 0
fi

# ---------- Cheap guard: only the guard, its suite, or the shared tokenizer matters ----------
# '*' spans '/' in case patterns, so the git_command.py match is at any depth.
guard_only=0
shared_dep=0
case "$file_path" in
  */scripts/publication-push-guard.py|*/scripts/tests/test_publication_push_guard.sh)
    guard_only=1
    ;;
  */scripts/lib/git_command.py)
    shared_dep=1
    ;;
  *)
    exit 0
    ;;
esac

# ---------- Resolve repo root ----------
# Environment fail-open: not a git repo at all. Deliberate, unchanged. Suite presence is decided
# per-arm below (via ownership + the missing-suite collection), so a DELETED suite in an owning
# repo can no longer hide behind this early exit the way a single hardcoded -f check would.
root=$(git -C "$(dirname "$file_path")" rev-parse --show-toplevel 2>/dev/null || true)
if [[ -z "$root" ]]; then
  exit 0
fi

failures=()
ran=()

run_shell_suite() {
  local label=$1 relpath=$2
  if [[ ! -f "$root/$relpath" ]]; then
    return
  fi
  ran+=("$label")
  local output rc
  output=$(cd "$root" && bash "$relpath" 2>&1) && rc=0 || rc=$?
  if [[ "$rc" -ne 0 ]]; then
    printf '%s FAILED after editing %s:\n' "$label" "$file_path" >&2
    printf '%s\n' "$output" | tail -20 >&2
    failures+=("$label")
  fi
}

run_pytest_suite() {
  local label=$1 relpath=$2
  if [[ ! -f "$root/$relpath" ]]; then
    return
  fi
  # ---------- Availability guard: no pytest → can't test, never falsely block ----------
  if ! python3 -c 'import pytest' >/dev/null 2>&1; then
    return
  fi
  ran+=("$label")
  local output rc
  output=$(cd "$root" && python3 -m pytest "$relpath" -q 2>&1) && rc=0 || rc=$?
  if [[ "$rc" -ne 0 ]]; then
    printf '%s FAILED after editing %s:\n' "$label" "$file_path" >&2
    printf '%s\n' "$output" | tail -20 >&2
    failures+=("$label")
  fi
}

# Every suite this arm will run must exist BEFORE any of them runs — run_shell_suite and
# run_pytest_suite each `return` silently on a missing file, so a deleted suite would otherwise
# be counted as "passed". Collected per arm, since the two arms run different sets.
missing=""
if [[ "$guard_only" -eq 1 ]]; then
  [[ -f "$root/scripts/tests/test_publication_push_guard.sh" ]] \
    || missing="${missing}  scripts/tests/test_publication_push_guard.sh"$'\n'
elif [[ "$shared_dep" -eq 1 ]]; then
  [[ -f "$root/scripts/tests/test_git_command.py" ]] \
    || missing="${missing}  scripts/tests/test_git_command.py"$'\n'
  [[ -f "$root/scripts/tests/test_publication_push_guard.sh" ]] \
    || missing="${missing}  scripts/tests/test_publication_push_guard.sh"$'\n'
  [[ -f "$root/scripts/tests/test_recast_hooks.sh" ]] \
    || missing="${missing}  scripts/tests/test_recast_hooks.sh"$'\n'
fi
if [[ -n "$missing" ]]; then
  if grep -q 'dotclaude-test-runner-hook' "$root/scripts/$(basename "$0")" 2>/dev/null; then
    {
      printf 'GATE DID NOT RUN — this repo owns %s but these suites are absent:\n' \
        "$(basename "$0")"
      printf '%s' "$missing"
      printf 'To remove this feature deliberately: delete its hook, remove its settings.json registration, then re-run /sync-docs.\n'
    } >&2
    exit 2
  fi
  # Non-owner holding only SOME of these suites: stay inert. Falling through would EXECUTE
  # whichever repo-supplied scripts happen to exist (`bash "$relpath"`, user privileges, on every
  # edit in every repo) — inertness the pre-split guard used to provide and this restores.
  exit 0
fi

if [[ "$guard_only" -eq 1 ]]; then
  run_shell_suite "publication-push-guard suite" "scripts/tests/test_publication_push_guard.sh"
elif [[ "$shared_dep" -eq 1 ]]; then
  run_pytest_suite "git_command unit tests" "scripts/tests/test_git_command.py"
  run_shell_suite "publication-push-guard suite" "scripts/tests/test_publication_push_guard.sh"
  run_shell_suite "recast-hooks suite" "scripts/tests/test_recast_hooks.sh"
fi

if [[ "${#ran[@]}" -eq 0 ]]; then
  exit 0
fi

if [[ "${#failures[@]}" -gt 0 ]]; then
  printf 'publication-push-guard-test: %d/%d suite(s) FAILED (%s)\n' \
    "${#failures[@]}" "${#ran[@]}" "${failures[*]}" >&2
  exit 2
fi

printf 'publication-push-guard-test: %d suite(s) passed (%s)\n' "${#ran[@]}" "${ran[*]}"
exit 0

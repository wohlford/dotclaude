#!/usr/bin/env bash
set -euo pipefail

# Script: hook-machinery-test.sh
# Purpose: PostToolUse hook — run the hook tests when a test-runner hook or the machinery around it changes
# Usage: Called by Claude Code hooks with JSON on stdin
# Ownership sentinel (do not remove): dotclaude-test-runner-hook
#
# Exit codes:
#   0 — allow: not a hook-machinery path, every suite passed, jq or python3 missing, or not a git repo; and, only
#       in a repo that does not own this hook, a suite absent, pytest unavailable, or python3 unable
#       to start a session
#   2 — blocked (stderr fed back to Claude). In every repo: invoked with argv or a terminal stdin, the
#       hook_budget.sh library unreadable, or mktemp failed. Only in a repo that owns this hook: a
#       suite missing, pytest unavailable, python3 unable to start a session, or a known
#       hook_budget.sh consumer no longer sourcing it. Whenever suites run: one failed, or they did
#       not finish inside the budget
#
# WHAT IT GATES. Every other test-runner hook gates a subject; nothing gated the hooks. Measured
# 2026-09-16: an edit to a test-runner hook, the guard suite, the hook meta-tests, the two hook
# libraries or settings.json ran no hook test. The guard suite takes ~150 s, so an edit to ONE hook
# runs only that hook's rows (HOOK_TESTS_ONLY, honoured by test_hook_suite_guard.sh and
# test_hook_argv_refusal.py), plus the parity and budget modules whole. An edit to the guard suite
# itself runs everything unselected. An edit to a meta-test module, lib/settings_hooks.py (plus its
# own checker suite) or settings.json runs the three meta-test modules only, never the guard suite.
# An edit to lib/hook_budget.sh runs the guard suite and the argv module selected to every hook that
# sources it, with the parity and budget modules whole. Measured slices: publication-push-guard-test.sh 114 s,
# audit-test.sh 14 s, hook-machinery-test.sh ~26 s (includes three nested guard-suite runs),
# every other hook 0-6 s. Every run sets HOOK_TESTS_ONLY explicitly, empty for an unselected run, so a value
# inherited from the environment never narrows one.
#
# THE BUDGET. Registered at 600 s; HOOK_BUDGET_SECS bounds the whole hook below that
# (scripts/tests/test_hook_budget.py pins the margin), reporting an overrun as exit 2.
# HOOK_MACHINERY_TEST_BUDGET may LOWER it (the guard suite uses it); any other value is ignored.

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

# ---------- Cheap guard ----------
# '*' spans '/' in case patterns; the exact classification happens below, relative to the repo root.
case "$file_path" in
  */scripts/*-test.sh | */scripts/tests/test_hook_suite_guard.sh | */scripts/tests/test_hook_parity.py | \
    */scripts/tests/test_hook_argv_refusal.py | */scripts/tests/test_hook_budget.py | \
    */scripts/lib/hook_budget.sh | */scripts/lib/settings_hooks.py | */settings.json) ;;
  *) exit 0 ;;
esac

# Resolve the FILE, not just its directory: ~/.claude/settings.json is a file symlink out of one repo
# into dotclaude's, so a directory-only resolution classifies the production registration edit
# against the wrong repository and runs nothing. python3 is needed for every suite below anyway;
# without it this is an environment fail-open.
real=$(python3 -c 'import os, sys; print(os.path.realpath(sys.argv[1]))' "$file_path" 2>/dev/null) || exit 0
[ -n "$real" ] || exit 0
root=$(git -C "$(dirname "$real")" rev-parse --show-toplevel 2>/dev/null) || exit 0
# Environment fail-open: not a git repo at all. Deliberate, unchanged.
[ -n "$root" ] || exit 0
rel=$real
case "$rel" in
  "$root"/*) rel=${rel#"$root"/} ;;
  *) exit 0 ;;
esac

# ---------- Classify ----------
# guard_sel / trio_sel: empty = do not run; ALL = run unselected; otherwise a HOOK_TESTS_ONLY list.
guard_sel=""
trio_sel=""
extra=""
budget_lib=0
case "$rel" in
  scripts/tests/test_hook_suite_guard.sh) guard_sel=ALL; trio_sel=ALL ;;
  scripts/tests/test_hook_parity.py | scripts/tests/test_hook_argv_refusal.py | scripts/tests/test_hook_budget.py) trio_sel=ALL ;;
  scripts/lib/hook_budget.sh) budget_lib=1 ;;
  scripts/lib/settings_hooks.py) trio_sel=ALL; extra=scripts/tests/test_settings_hooks_check.py ;;
  settings.json) trio_sel=ALL ;;
  scripts/*/*) exit 0 ;;
  scripts/*-test.sh) guard_sel=$(basename "$rel"); trio_sel=$guard_sel ;;
  *) exit 0 ;;
esac

missing=""
if [ "$budget_lib" -eq 1 ]; then
  # Derived, so a new consumer is covered with no edit here; the floor makes a LOST one alarm.
  consumers=""
  for h in "$root"/scripts/*-test.sh; do
    [ -f "$h" ] || continue
    if grep -q 'lib/hook_budget.sh' "$h" 2>/dev/null; then
      consumers="${consumers:+$consumers,}$(basename "$h")"
    fi
  done
  for want in audit-test.sh publication-push-guard-test.sh; do
    case ",$consumers," in
      *",$want,"*) ;;
      *) missing="${missing}  scripts/$want no longer sources scripts/lib/hook_budget.sh (a known consumer)"$'\n' ;;
    esac
  done
  guard_sel=$consumers
  trio_sel=$consumers
fi

trio="scripts/tests/test_hook_parity.py scripts/tests/test_hook_argv_refusal.py scripts/tests/test_hook_budget.py"
if [ -n "$guard_sel" ] && [ ! -f "$root/scripts/tests/test_hook_suite_guard.sh" ]; then
  missing="${missing}  scripts/tests/test_hook_suite_guard.sh"$'\n'
fi
if [ -n "$trio_sel" ]; then
  for s in $trio; do
    [ -f "$root/$s" ] || missing="${missing}  $s"$'\n'
  done
fi
if [ -n "$extra" ] && [ ! -f "$root/$extra" ]; then
  missing="${missing}  $extra"$'\n'
fi
# A suite that is PRESENT but cannot be run is the same event as a missing one: the gate did not run.
# Skip the probe once a suite is already known absent — a file that is not there fails the gate on
# its own, and the probe would only spend a python3 start to learn nothing new.
if [ -z "$missing" ] && [ -n "${trio_sel}${extra}" ] && ! python3 -c 'import pytest' >/dev/null 2>&1; then
  missing="${missing}  the pytest suites (present, but pytest is unavailable)"$'\n'
fi

# A missing or unrunnable suite is inert ONLY where this repo does not own this hook. Ownership is
# proven by finding this hook's OWN source, which requires nothing from the suites in question.
if [ -n "$missing" ]; then
  if grep -q 'dotclaude-test-runner-hook' "$root/scripts/$(basename "$0")" 2>/dev/null; then
    {
      printf 'GATE DID NOT RUN — this repo owns %s but these are absent or unrunnable:\n' "$(basename "$0")"
      printf '%s' "$missing"
      printf 'To remove this feature deliberately: delete its hook, remove its settings.json registration, then re-run /sync-docs.\n'
    } >&2
    exit 2
  fi
  exit 0
fi

# ---------- Budget ----------
# SECONDS counts from this shell's start, so the deadline covers everything above as well.
HOOK_BUDGET_SECS=540
budget=$HOOK_BUDGET_SECS
override=${HOOK_MACHINERY_TEST_BUDGET:-}
case "$override" in
  '' | *[!0-9]* | ?????*) ;; # empty, non-numeric, or five or more digits: ignored
  *)
    if [ "$((10#$override))" -gt 0 ] && [ "$((10#$override))" -lt "$HOOK_BUDGET_SECS" ]; then
      budget=$((10#$override))
    fi
    ;;
esac

hook_lib="$(dirname "$0")/lib/hook_budget.sh"
if [ ! -r "$hook_lib" ]; then
  printf '%s\n' \
    "GATE DID NOT RUN — $(basename "$0") cannot read its bounded-run library $hook_lib, so it cannot run the hook tests below its registered timeout." \
    "The hook's install is broken: restore scripts/lib/hook_budget.sh beside it." >&2
  exit 2
fi
# shellcheck source=lib/hook_budget.sh
. "$hook_lib"

if ! python3 -c 'import os, sys; sys.exit(0 if hasattr(os, "setsid") else 1)' >/dev/null 2>&1; then
  if grep -q 'dotclaude-test-runner-hook' "$root/scripts/$(basename "$0")" 2>/dev/null; then
    printf 'GATE DID NOT RUN — %s cannot start a suite session: python3 is missing or lacks os.setsid.\n' \
      "$(basename "$0")" >&2
    exit 2
  fi
  exit 0
fi

# ---------- Run ----------
tmpdir=$(mktemp -d "${TMPDIR:-/tmp}/hook-machinery-test.XXXXXX" 2>/dev/null) || {
  printf 'GATE DID NOT RUN — could not create a temporary directory for suite output.\n' >&2
  exit 2
}
live=""
# shellcheck disable=SC2329  # invoked via the EXIT trap
cleanup() {
  if [ -n "$live" ]; then
    hb_kill_suite "$live"
  fi
  rm -rf "$tmpdir"
}
trap cleanup EXIT
trap 'exit 2' TERM INT

failed=0
: > "$tmpdir/report"
# run_one NAME SELECTION CMD [ARG...] — one bounded run sharing the hook's single deadline.
run_one() {
  local name="$1" sel="$2" one_rc=""
  shift 2
  if [ "$sel" = ALL ]; then
    sel=""
  fi
  hb_run_bounded one_rc live "$budget" "$root" "$tmpdir/out" env HOOK_TESTS_ONLY="$sel" "$@"
  if [ "$one_rc" = killed ]; then
    {
      printf 'GATE DID NOT COMPLETE — %s ran out of its %ss budget after editing %s.\n' \
        "$(basename "$0")" "$budget" "$file_path"
      printf 'killed mid-run: %s (selection: %s) — its last output:\n' "$name" "${sel:-all}"
      tail -20 "$tmpdir/out" || true
      printf 'The harness would have killed this hook silently at its registered timeout. Run %s through scripts/run-long.sh and read its verdict; do not re-edit the file to retry.\n' "$name"
    } >&2
    exit 2
  fi
  if [ "$one_rc" -ne 0 ]; then
    failed=1
    {
      printf -- '--- %s (exit %s, selection: %s) ---\n' "$name" "$one_rc" "${sel:-all}"
      # PASS/FAIL rows print inline as the suite goes, so a FAIL near the top of a long run sorts
      # before the tail window and a fixed tail alone would hide it. Emit every FAIL line first,
      # then the tail for surrounding context.
      grep -E '^FAIL' "$tmpdir/out" || true
      tail -40 "$tmpdir/out" || true
    } >> "$tmpdir/report"
  fi
}

if [ -n "$trio_sel" ]; then
  # shellcheck disable=SC2086  # $trio is a fixed space-separated list of repo-relative paths
  run_one "the hook meta-tests" "$trio_sel" python3 -m pytest -q -p no:cacheprovider $trio
fi
if [ -n "$extra" ]; then
  run_one "$extra" ALL python3 -m pytest -q -p no:cacheprovider "$extra"
fi
if [ -n "$guard_sel" ]; then
  run_one scripts/tests/test_hook_suite_guard.sh "$guard_sel" bash scripts/tests/test_hook_suite_guard.sh
fi

if [ "$failed" -ne 0 ]; then
  {
    printf 'hook machinery tests FAILED after editing %s:\n' "$file_path"
    cat "$tmpdir/report"
    # Only the missing-population shape gets the new-hook hint below: a suite that ran and failed
    # for an unrelated reason should not read as "you forgot a CASES row".
    if grep -qE 'no CASES row|not present' "$tmpdir/report"; then
      printf 'A NEW *-test.sh fails this gate until it has a CASES row in scripts/tests/test_hook_suite_guard.sh, a settings.json registration, and its file — expect one block while adding all three.\n'
    fi
  } >&2
  exit 2
fi
exit 0

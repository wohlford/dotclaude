#!/usr/bin/env bash
set -euo pipefail

# Script: audit-test.sh
# Purpose: PostToolUse hook — run the audit engine test suite when the engine or its suite changes
# Usage: Called by Claude Code hooks with JSON on stdin
# Ownership sentinel (do not remove): dotclaude-test-runner-hook
#
# Exit codes:
#   0 — allow: not an audit file, the suite passed, jq missing, or not a git repo; and, only in a
#       repo that does not own this hook, the suite absent or python3 unable to start a session
#   2 — blocked (stderr fed back to Claude). In every repo: invoked with argv or a terminal stdin,
#       the hook_budget.sh library unreadable, or mktemp failed. Only in a repo that owns this hook:
#       the suite missing, or python3 unable to start a session (no os.setsid). And whenever the
#       suite runs: it fails after this edit, or it did not finish inside its budget
#
# THE BUDGET. Claude Code discards a hook's output when it outlives its registered timeout, and
# Claude never learns that it did — for a gate, a silent fail-open. Measured 2026-09-15: the suite
# took 236 s alone and 317 s on a busier machine while this hook was registered at 120 s, so every
# edit it guarded was killed without a word. Running the real hook against the real suite
# (2026-09-15, Task 2 Step 5.5) measured 351 s, the figure closest to the budget. It is now
# registered at 600 s and bounds itself at HOOK_BUDGET_SECS (scripts/tests/test_hook_budget.py pins
# the margin), reporting an overrun as exit 2. The suite runs in its own session through
# scripts/lib/hook_budget.sh, so an overrun stops its whole process tree — except a descendant that
# starts a process group of its own: test_audit.sh's rM8 row runs the engine under `set -m`, and an
# overrun inside that ~1 s window leaves that read-only sweep to finish on its own. AUDIT_TEST_BUDGET
# may LOWER the budget (the guard suite uses it); any other value is ignored.

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
  */skills/audit/audit.sh|*/scripts/tests/test_audit.sh) ;;
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
if [ ! -f "$root/scripts/tests/test_audit.sh" ]; then
  if grep -q 'dotclaude-test-runner-hook' "$root/scripts/$(basename "$0")" 2>/dev/null; then
    printf '%s\n' \
      "MISSING SUITE: scripts/tests/test_audit.sh is absent, but this repo owns $(basename "$0") — the gate did NOT run." \
      "To remove this feature deliberately: delete its hook, remove its settings.json registration, then re-run /sync-docs." >&2
    exit 2
  fi
  exit 0
fi

# ---------- Budget ----------
# SECONDS counts from this shell's start, so the deadline covers everything above as well. An
# environment-exported SECONDS shifts that origin (measured: `env SECONDS=1000 bash -c 'echo $SECONDS'`
# prints 1000 on bash 3.2 and 5).
HOOK_BUDGET_SECS=540
budget=$HOOK_BUDGET_SECS
override=${AUDIT_TEST_BUDGET:-}
case "$override" in
  '' | *[!0-9]* | ?????*) ;; # empty, non-numeric, or five or more digits: ignored
  *)
    if [ "$((10#$override))" -gt 0 ] && [ "$((10#$override))" -lt "$HOOK_BUDGET_SECS" ]; then
      budget=$((10#$override))
    fi
    ;;
esac

# The bounded-run library is resolved from this hook's own directory, so its absence is a broken
# install of the hook rather than a property of the edited repo: alarm whatever repo was edited.
hook_lib="$(dirname "$0")/lib/hook_budget.sh"
if [ ! -r "$hook_lib" ]; then
  printf '%s\n' \
    "GATE DID NOT RUN — $(basename "$0") cannot read its bounded-run library $hook_lib, so it cannot run scripts/tests/test_audit.sh below its registered timeout." \
    "The hook's install is broken: restore scripts/lib/hook_budget.sh beside it." >&2
  exit 2
fi
# shellcheck source=lib/hook_budget.sh
. "$hook_lib"

# The session launcher needs python3's os.setsid. Without it every run would fail as a misattributed
# suite failure, so say what happened — where this repo owns the hook; elsewhere stay inert.
if ! python3 -c 'import os, sys; sys.exit(0 if hasattr(os, "setsid") else 1)' >/dev/null 2>&1; then
  if grep -q 'dotclaude-test-runner-hook' "$root/scripts/$(basename "$0")" 2>/dev/null; then
    printf '%s\n' \
      "GATE DID NOT RUN — $(basename "$0") cannot start a suite session: python3 is missing or lacks os.setsid." >&2
    exit 2
  fi
  exit 0
fi

# ---------- Run ----------
tmpdir=$(mktemp -d "${TMPDIR:-/tmp}/audit-test.XXXXXX" 2>/dev/null) || {
  printf 'GATE DID NOT RUN — could not create a temporary directory for suite output.\n' >&2
  exit 2
}
# `live` holds the running suite's pid while hb_run_bounded polls it, so the EXIT trap can stop a
# suite this hook is interrupted in the middle of.
live=""
suite_rc=""
# shellcheck disable=SC2329  # invoked via the EXIT trap
cleanup() {
  if [ -n "$live" ]; then
    hb_kill_suite "$live"
  fi
  rm -rf "$tmpdir"
}
trap cleanup EXIT
trap 'exit 2' TERM INT

hb_run_bounded suite_rc live "$budget" "$root" "$tmpdir/suite.out" bash scripts/tests/test_audit.sh

if [ "$suite_rc" = killed ]; then
  {
    printf 'GATE DID NOT COMPLETE — %s ran out of its %ss budget after editing %s.\n' \
      "$(basename "$0")" "$budget" "$file_path"
    printf 'killed mid-run: scripts/tests/test_audit.sh — its last output:\n'
    tail -20 "$tmpdir/suite.out" || true
    printf 'The harness would have killed this hook silently at its registered timeout. Run scripts/tests/test_audit.sh through scripts/run-long.sh and read its verdict; do not re-edit the file to retry.\n'
  } >&2
  exit 2
fi
if [ "$suite_rc" -ne 0 ]; then
  printf 'audit engine test suite FAILED after this edit:\n' >&2
  tail -40 "$tmpdir/suite.out" >&2 || true
  exit 2
fi
exit 0

#!/usr/bin/env bash
set -euo pipefail

# Script: audit-test.sh
# Purpose: PostToolUse hook — run the audit engine suite, or the suite-driver suite, when its files change
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
# WHAT IT RUNS. An edit to skills/audit/audit.sh or scripts/tests/test_audit.sh runs test_audit.sh in
# its PARALLEL profile, AUDIT_TEST_JOBS=min(8, cores), at least 2 (4 when the core count cannot be read): each
# unit of the suite runs as its own process and a driver aggregates them (scripts/lib/suite_parallel.sh).
# This hook is the only caller that asks for it. The bare suite stays SERIAL, and so do /audit --tests
# and the adopted finish's tip suite (check_tests removes the four profile variables from every
# suite's environment), which is the pre-publish gate;
# a red here is a finding to re-run serially, not that gate's verdict. AUDIT_TEST_ONLY, AUDIT_TEST_UNIT
# and AUDIT_TEST_RUNDIR are scrubbed from the suite's environment, so an exported leftover cannot make
# a firing run one unit and pass. An edit to scripts/lib/suite_parallel.sh or
# scripts/tests/test_suite_parallel.sh runs test_suite_parallel.sh instead (fixture suites only).
#
# THE BUDGET. Claude Code discards a hook's output when it outlives its registered timeout, and
# Claude never learns that it did — for a gate, a silent fail-open. Measured: the SERIAL suite took
# 236 s alone on 2026-09-15 (before its rPT and rSC sections existed), 512-530 s under this hook on
# 2026-10-19 (three firings of one implementer dispatch) and 662 s run directly on 2026-10-21 at a
# load of about 5-6 on 16 CPUs. Against a 540 s budget the serial suite sat on the cliff and crossed
# it with ordinary load. A throwaway prototype of the parallel profile ran in 307-312 s at width 8,
# before this hook's own overhead, a speed-up of about 2.2x. The hook is registered at 600 s and
# bounds itself at HOOK_BUDGET_SECS (scripts/tests/test_hook_budget.py pins the margin), reporting an
# overrun as exit 2. The suite runs in its own session through scripts/lib/hook_budget.sh, so an
# overrun stops its whole process tree — except a descendant that starts a process group of its own:
# test_audit.sh's rM8, PT14 and rSC9 rows open one under `set -m`, rM8 for about a second and PT14 and
# rSC9 for up to about 60 s each (their background engine, shim or stubs), and an overrun inside such
# a window leaves that read-only sweep or stub to finish on its own, within about 60 s.
# AUDIT_TEST_BUDGET may LOWER the budget (the guard suite uses it); any other value is ignored.

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
  */scripts/lib/suite_parallel.sh|*/scripts/tests/test_suite_parallel.sh) ;;
  *) exit 0 ;;
esac

# Which suite this edit runs. The engine and its suite run test_audit.sh in the parallel profile
# (below); the suite-driver library and its suite run test_suite_parallel.sh, which is fast.
suite_rel=scripts/tests/test_audit.sh
suite_label='audit engine test suite'
case "$file_path" in
  */scripts/lib/suite_parallel.sh|*/scripts/tests/test_suite_parallel.sh)
    suite_rel=scripts/tests/test_suite_parallel.sh
    suite_label='suite-driver library test suite'
    ;;
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
if [ ! -f "$root/$suite_rel" ]; then
  if grep -q 'dotclaude-test-runner-hook' "$root/scripts/$(basename "$0")" 2>/dev/null; then
    printf '%s\n' \
      "MISSING SUITE: $suite_rel is absent, but this repo owns $(basename "$0") — the gate did NOT run." \
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
    "GATE DID NOT RUN — $(basename "$0") cannot read its bounded-run library $hook_lib, so it cannot run $suite_rel below its registered timeout." \
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

# The audit engine suite runs in the PARALLEL profile because serial it is past this hook's budget at
# ordinary load. The selectors are scrubbed so an exported AUDIT_TEST_ONLY, AUDIT_TEST_UNIT or
# AUDIT_TEST_RUNDIR can never make a firing run one unit and pass, and AUDIT_TEST_JOBS is always set
# here, never inherited: an empty or malformed core count is a fixed width of 4, never an empty value
# that would read as serial, and a one-core host still gets width 2 (width 1 IS the serial profile,
# which is past the budget). TMPDIR is this hook's own temporary directory, so the driver's run
# directory, and anything a KILLed driver leaves behind, is removed by this hook's EXIT trap instead
# of leaking into the operator's temp root. The library suite takes no profile at all.
suite_env=(-u AUDIT_TEST_ONLY -u AUDIT_TEST_UNIT -u AUDIT_TEST_RUNDIR)
if [ "$suite_rel" = scripts/tests/test_audit.sh ]; then
  cores=$(getconf _NPROCESSORS_ONLN 2>/dev/null) || cores=""
  case "$cores" in
    '' | 0* | *[!0-9]* | ?????*) test_jobs=4 ;;
    *) test_jobs=$cores ;;
  esac
  if [ "$test_jobs" -gt 8 ]; then
    test_jobs=8
  fi
  if [ "$test_jobs" -lt 2 ]; then
    test_jobs=2
  fi
  suite_env+=("AUDIT_TEST_JOBS=$test_jobs")
else
  suite_env+=(-u AUDIT_TEST_JOBS)
fi
# env takes every -u option before the first assignment, so the assignment goes last.
suite_env+=("TMPDIR=$tmpdir")

hb_run_bounded suite_rc live "$budget" "$root" "$tmpdir/suite.out" env "${suite_env[@]}" bash "$suite_rel"

if [ "$suite_rc" = killed ]; then
  {
    printf 'GATE DID NOT COMPLETE — %s ran out of its %ss budget after editing %s.\n' \
      "$(basename "$0")" "$budget" "$file_path"
    printf 'killed mid-run: %s — its last output:\n' "$suite_rel"
    tail -20 "$tmpdir/suite.out" || true
    printf 'The harness would have killed this hook silently at its registered timeout. Run %s through scripts/run-long.sh and read its verdict; do not re-edit the file to retry.\n' "$suite_rel"
  } >&2
  exit 2
fi
if [ "$suite_rc" -ne 0 ]; then
  printf '%s FAILED after this edit:\n' "$suite_label" >&2
  tail -40 "$tmpdir/suite.out" >&2 || true
  exit 2
fi
exit 0

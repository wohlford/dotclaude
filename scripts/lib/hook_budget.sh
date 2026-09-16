#!/usr/bin/env bash
set -euo pipefail

# Script: hook_budget.sh
# Purpose: Sourced library — run one test suite in its own session, bounded by a deadline
# Usage: . "$(dirname "$0")/lib/hook_budget.sh" from a test-runner hook; never executed directly
#
# Claude Code kills a hook that outlives its registered timeout and never tells Claude, so a
# test-runner hook whose suites can take minutes bounds itself below its registration and reports an
# overrun as exit 2. This file is the mechanism; the calling hook owns the budget, the deadline's
# origin (bash's SECONDS), its traps, and every message.
#
# Contract: functions read only their arguments and write only the caller variables they are told to
# name, so each file is checkable on its own. The library installs no traps and never assigns SECONDS.
# Its locals carry the _hb_ prefix, so hb_run_bounded refuses (returns 2) any caller variable name
# containing _hb_.
# Under bash 3.2 a killed suite makes the CALLING shell print a `Terminated: 15` job notice naming a line
# of this file on stderr; bash 5 prints nothing. It is noise beside the hook's own overrun report.

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  printf '%s is a library for test-runner hooks: source it, do not execute it.\n' \
    "$(basename "$0")" >&2
  exit 2
fi

HB_SESSION_EXEC='import os, sys; os.setsid(); os.execvp(sys.argv[1], sys.argv[1:])'

# hb_kill_suite PID — stop one suite launched by hb_run_bounded, and everything it started. TERM goes
# to the group. That FAILS in the brief window between launch and os.setsid(), when PID is not yet a
# group leader — so fall back to the process itself, which has started nothing yet. Then poll the
# GROUP, not just its leader, for a few seconds and escalate to KILL if anything in it is still
# alive: a descendant that ignores TERM outlives its leader, and polling the leader alone would miss
# it. A process-group id cannot be reused while any member is alive; once the group is empty it can
# be, so a signal can reach a reused id only within one poll interval — a residual accepted, not
# closed. A descendant that starts a session or process group of its own escapes both signals.
hb_kill_suite() {
  local _hb_pid=$1
  kill -TERM -- "-$_hb_pid" 2>/dev/null || kill -TERM "$_hb_pid" 2>/dev/null || true
  for _ in 1 2 3 4 5 6 7 8 9 10; do
    if ! kill -0 -- "-$_hb_pid" 2>/dev/null && ! kill -0 "$_hb_pid" 2>/dev/null; then
      return 0
    fi
    sleep 0.5
  done
  kill -KILL -- "-$_hb_pid" 2>/dev/null || kill -KILL "$_hb_pid" 2>/dev/null || true
  return 0
}

# hb_run_bounded RC_VAR PID_VAR DEADLINE DIR OUT CMD [ARG...] — run CMD from DIR in its own session,
# stdout and stderr to OUT, until it exits or SECONDS reaches DEADLINE. PID_VAR holds the running pid
# (also its process-group id once os.setsid() has run) for the caller's EXIT trap, and is cleared
# once the suite is reaped. RC_VAR receives the exit status, or `killed`. Returns 2 without running
# anything if RC_VAR or PID_VAR contains _hb_; otherwise always returns 0, so set -e never
# turns an outcome into rc 1. Bash reaps a finished background child as soon as it exits, before
# `wait` runs, so between an exit and the next poll the id can already be free; see hb_kill_suite for
# what that leaves.
hb_run_bounded() {
  case "$1 $2" in
    *_hb_*)
      printf 'hb_run_bounded: caller variable names may not contain _hb_ (%s)\n' "$1 $2" >&2
      return 2
      ;;
  esac
  local _hb_rc_var=$1 _hb_pid_var=$2 _hb_deadline=$3 _hb_dir=$4 _hb_out=$5 _hb_pid _hb_rc=0
  shift 5
  (cd "$_hb_dir" && exec python3 -c "$HB_SESSION_EXEC" "$@") >"$_hb_out" 2>&1 &
  _hb_pid=$!
  printf -v "$_hb_pid_var" '%s' "$_hb_pid"
  while kill -0 "$_hb_pid" 2>/dev/null; do
    if [[ "$SECONDS" -ge "$_hb_deadline" ]]; then
      hb_kill_suite "$_hb_pid"
      wait "$_hb_pid" 2>/dev/null || true
      printf -v "$_hb_pid_var" '%s' ''
      printf -v "$_hb_rc_var" '%s' killed
      return 0
    fi
    sleep 0.5
  done
  wait "$_hb_pid" || _hb_rc=$?
  printf -v "$_hb_pid_var" '%s' ''
  printf -v "$_hb_rc_var" '%s' "$_hb_rc"
  return 0
}

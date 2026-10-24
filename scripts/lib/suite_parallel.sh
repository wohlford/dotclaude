#!/usr/bin/env bash
set -uo pipefail

# Script: suite_parallel.sh
# Purpose: Sourced library — run a unit-structured shell suite serially (the gate) or as concurrent per-unit children
# Usage: . "$(dirname "$0")/lib/suite_parallel.sh" from a suite; never executed directly
#
# A suite that sources this wraps each independent block of rows in `if unit_run <name> <weight>; then
# ... fi` and calls `sp_init` once near its top and `unit_finish` once after its last unit. The suite
# defines the counters `pass` and `fail` and the row printers; this library reads the counters.
#
# THE PROFILES. With no AUDIT_TEST_* variable set the suite runs every unit in the suite's one process,
# in file order, exactly as a suite with no wrappers would: that is the serial profile, and it is the
# pre-publish gate. AUDIT_TEST_JOBS=<n> with n of 2 or more is the parallel profile: the suite becomes
# a DRIVER that lists its units, runs each as a child process (at most n at once), and aggregates.
# Only the per-edit hook asks for it. The driver never changes what a serial run does.
#
# CHILD MODE IS KEYED ON A TOKEN, NEVER ON A NAME. A child is started with AUDIT_TEST_UNIT=<unit> and
# AUDIT_TEST_RUNDIR=<a directory the driver just created>. The library enters child mode only when BOTH
# are set AND the directory exists; either one alone, or a directory that is gone, is a usage error (exit
# 2) before any row runs. A leftover export of a unit name therefore cannot make a gate run one unit and
# pass. Residual: a driver killed with SIGKILL leaves its directory behind, so an export of both
# variables pointing at that corpse still enters child mode until the directory is removed.
#
# THE VERDICT COMES FROM RECORDS, NOT FROM TEXT. A child writes `unit=<name> pass=<n> fail=<m> skip=<k>`
# to $AUDIT_TEST_RUNDIR/<name>.rec, atomically, only when it reaches the end of its unit. The driver
# fails a unit with no record, a non-zero exit status, fail > 0, or no rows and no skip. Nothing is read
# from a unit's stdout for the verdict: failure diagnostics echo fixture output, which can contain any
# summary-shaped text.
#
# bash 3.2 safe: no `wait -n`, no associative arrays, no ${var,,}.

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  printf '%s is a library for unit-structured suites: source it, do not execute it.\n' \
    "$(basename "$0")" >&2
  exit 2
fi

SP_MODE=serial
SP_PROG=suite
SP_ENTERED=0
SP_SKIPS=0
SP_FLOOR=""
sp_rundir=""
sp_suite=""
sp_prev_exit=""
sp_names=()
sp_pids=()
sp_state=()
sp_t0=()
sp_rcs=()

sp_usage() { # MESSAGE -> a usage error: exit 2 before any row
  printf '%s: %s\n' "$SP_PROG" "$1" >&2
  exit 2
}

# 0 when VALUE is not a usable job count: empty, a leading zero (octal), non-digits, over four digits.
sp_jobs_invalid() { # VALUE
  case "$1" in
    '' | 0* | *[!0-9]*) return 0 ;;
  esac
  [ "${#1}" -gt 4 ] && return 0
  return 1
}

# sp_init SUITE_PATH FLOOR_NAMES — validate the four profile variables (AUDIT_TEST_JOBS, AUDIT_TEST_ONLY,
# AUDIT_TEST_UNIT, AUDIT_TEST_RUNDIR), pick the mode, and in the parallel profile run the driver and
# exit with its status.
sp_init() {
  local suite=$1 jobs=1 has_unit has_dir
  SP_PROG=$(basename "$suite")
  SP_FLOOR=${2:-}
  if [ -n "${AUDIT_TEST_JOBS+x}" ]; then
    if sp_jobs_invalid "$AUDIT_TEST_JOBS"; then
      sp_usage "AUDIT_TEST_JOBS must be a positive integer without a leading zero and of at most four digits (got '${AUDIT_TEST_JOBS}')"
    fi
    jobs=$AUDIT_TEST_JOBS
  fi
  has_unit=${AUDIT_TEST_UNIT+x}
  has_dir=${AUDIT_TEST_RUNDIR+x}
  if [ -n "$has_unit$has_dir" ]; then
    [ -n "$has_unit" ] || sp_usage "AUDIT_TEST_RUNDIR is set without AUDIT_TEST_UNIT; child mode needs both"
    [ -n "$has_dir" ] || sp_usage "AUDIT_TEST_UNIT is set without AUDIT_TEST_RUNDIR; child mode needs both"
    [ -n "$AUDIT_TEST_UNIT" ] || sp_usage "AUDIT_TEST_UNIT is empty"
    if [ -z "$AUDIT_TEST_RUNDIR" ] || [ ! -d "$AUDIT_TEST_RUNDIR" ]; then
      sp_usage "AUDIT_TEST_RUNDIR names a directory that does not exist (got '${AUDIT_TEST_RUNDIR}')"
    fi
    if [ "$AUDIT_TEST_UNIT" = --list ]; then
      SP_MODE=list
    else
      case "$AUDIT_TEST_UNIT" in
        [!A-Za-z]* | *[!A-Za-z0-9_-]*) sp_usage "AUDIT_TEST_UNIT is not a unit name (got '${AUDIT_TEST_UNIT}')" ;;
      esac
      SP_MODE=child
    fi
  fi
  if [ -n "${AUDIT_TEST_ONLY:-}" ]; then
    case "$AUDIT_TEST_ONLY" in
      rML | rPT) ;;
      *) sp_usage "AUDIT_TEST_ONLY=${AUDIT_TEST_ONLY} names no selectable section (rML or rPT)" ;;
    esac
    if [ "$jobs" -gt 1 ] || [ "$SP_MODE" != serial ]; then
      sp_usage "AUDIT_TEST_ONLY cannot be combined with AUDIT_TEST_JOBS above 1 or with AUDIT_TEST_UNIT / AUDIT_TEST_RUNDIR: a partial run is serial and single-process"
    fi
    printf 'NOTE: partial run (AUDIT_TEST_ONLY=%s), not the gate\n' "$AUDIT_TEST_ONLY"
  fi
  if [ "$SP_MODE" = serial ] && [ "$jobs" -gt 1 ]; then
    sp_drive "$suite" "$jobs"
    exit $?
  fi
}

# unit_run NAME WEIGHT — the unit wrapper. Exactly two arguments; a third is a usage error so a retired
# word can never become a silently ignored one. Returns 0 when the unit's body should run.
unit_run() {
  if [ "$#" -ne 2 ]; then
    sp_usage "unit_run takes exactly two arguments, a name and a weight (got $#)"
  fi
  case "$1" in
    '' | [!A-Za-z]* | *[!A-Za-z0-9_-]*) sp_usage "unit_run: '$1' is not a unit name" ;;
  esac
  case "$2" in
    '' | 0* | *[!0-9]*) sp_usage "unit_run: the weight of '$1' must be a positive integer (got '$2')" ;;
  esac
  case "$SP_MODE" in
    serial) return 0 ;;
    list)
      if grep -q "^$1 " "$AUDIT_TEST_RUNDIR/units.list" 2>/dev/null; then
        sp_usage "unit '$1' is declared twice"
      fi
      printf '%s %s\n' "$1" "$2" >> "$AUDIT_TEST_RUNDIR/units.list"
      return 1
      ;;
    child)
      if [ "$1" = "$AUDIT_TEST_UNIT" ]; then
        SP_ENTERED=1
        return 0
      fi
      return 1
      ;;
  esac
  return 1
}

# skip_line TEXT — print a tool-absent skip line and count it. The count travels in the result record,
# so a unit with no rows is accepted on a counted skip and never on text that merely looks like one.
skip_line() { # TEXT
  SP_SKIPS=$((SP_SKIPS + 1))
  printf '%s\n' "$1"
}

# unit_finish — call once after the last unit. Serial: a no-op. List: report the rows emitted OUTSIDE
# every unit, and exit. Child: write the result record, print the summary, exit with the unit's status.
# shellcheck disable=SC2154  # pass and fail are the sourcing suite's counters
unit_finish() {
  local rec
  case "$SP_MODE" in
    serial) return 0 ;;
    list)
      printf '%s\n' "$((pass + fail + SP_SKIPS))" > "$AUDIT_TEST_RUNDIR/outside.count"
      exit 0
      ;;
    child)
      if [ "$SP_ENTERED" -ne 1 ]; then
        printf '%s: no unit_run wrapper matched AUDIT_TEST_UNIT=%s\n' "$SP_PROG" "$AUDIT_TEST_UNIT" >&2
        exit 2
      fi
      rec="$AUDIT_TEST_RUNDIR/$AUDIT_TEST_UNIT.rec"
      if ! { printf 'unit=%s pass=%s fail=%s skip=%s\n' "$AUDIT_TEST_UNIT" "$pass" "$fail" "$SP_SKIPS" > "$rec.tmp" \
        && mv -f "$rec.tmp" "$rec"; }; then
        printf '%s: could not write the result record %s\n' "$SP_PROG" "$rec" >&2
        exit 2
      fi
      printf '\n%d passed, %d failed\n' "$pass" "$fail"
      [ "$fail" -eq 0 ]
      exit $?
      ;;
  esac
}

# The descendants of PID, one per line (depth first). Used on a driver-only TERM: a descendant that
# opened its own process group is still a descendant while its parent lives.
sp_descendants() { # PID
  local c
  for c in $(pgrep -P "$1" 2>/dev/null); do
    printf '%s\n' "$c"
    sp_descendants "$c"
  done
}

# TERM every running child and its descendants, wait at most 3 s, KILL what is left.
sp_stop_children() {
  local i all="" d alive
  for ((i = 0; i < ${#sp_pids[@]}; i++)); do
    [ "${sp_state[$i]}" = 1 ] || continue
    all="$all ${sp_pids[$i]} $(sp_descendants "${sp_pids[$i]}" | tr '\n' ' ')"
  done
  [ -n "${all// /}" ] || return 0
  # shellcheck disable=SC2086  # $all is a space-separated list of pids
  kill -TERM $all 2>/dev/null || true
  for _ in 1 2 3 4 5 6; do
    alive=0
    for d in $all; do
      kill -0 "$d" 2>/dev/null && alive=1
    done
    [ "$alive" -eq 0 ] && return 0
    sleep 0.5
  done
  # shellcheck disable=SC2086
  kill -KILL $all 2>/dev/null || true
}

# The driver's signal handler. It SPEAKS FIRST — hook_budget.sh escalates to KILL on the whole group 5 s
# after its TERM, and a driver that waited before reporting would be killed before it said anything.
sp_on_signal() { # SIGNAL
  local i
  trap '' TERM INT HUP
  printf 'SUITE DRIVER: %s received; units still running:\n' "$1"
  for ((i = 0; i < ${#sp_pids[@]}; i++)); do
    [ "${sp_state[$i]}" = 1 ] || continue
    printf '  RUNNING %s (%ss), last line: %s\n' "${sp_names[$i]}" "$((SECONDS - sp_t0[i]))" \
      "$(tail -n 1 "$sp_rundir/${sp_names[$i]}.out" 2>/dev/null)"
  done
  sp_stop_children
  rm -rf "$sp_rundir"
  exit 143
}

# The driver's EXIT handler: remove the run directory, then run the EXIT trap the suite had installed
# before the driver replaced it. Every exit path of the driver ends here, a signal's included.
sp_on_exit() {
  rm -rf "$sp_rundir"
  if [ -n "$sp_prev_exit" ]; then
    eval "$sp_prev_exit"
  fi
}

sp_launch() { # INDEX
  local i=$1 name=${sp_names[$1]}
  printf 'unit %s started\n' "$name"
  env -u AUDIT_TEST_JOBS -u AUDIT_TEST_ONLY AUDIT_TEST_UNIT="$name" AUDIT_TEST_RUNDIR="$sp_rundir" \
    "$BASH" "$sp_suite" > "$sp_rundir/$name.out" 2>&1 &
  sp_pids[i]=$!
  sp_state[i]=1
  sp_t0[i]=$SECONDS
}

sp_finish() { # INDEX — reap a finished child and print its buffered output
  local i=$1 name=${sp_names[$1]} rc=0
  wait "${sp_pids[$i]}" || rc=$?
  sp_rcs[i]=$rc
  sp_state[i]=2
  printf 'unit %s done rc=%s %ss\n' "$name" "$rc" "$((SECONDS - sp_t0[i]))"
  cat "$sp_rundir/$name.out"
}

sp_num() { # VALUE -> 0 when VALUE is a non-empty run of digits
  case "$1" in
    '' | *[!0-9]*) return 1 ;;
  esac
  return 0
}

# sp_drive SUITE JOBS — list the units, run them at most JOBS at a time, aggregate, return 0 or 1.
sp_drive() {
  local suite=$1 jobs=$2 i total=0 next=0 running=0 finished=0 name list_rc=0 outside missing=""
  local failed=0 tp=0 tf=0 ts=0 verdict="" rec f1 f2 f3 f4 ru rp rf rs nfail shown nlist found
  printf 'NOTE: parallel profile (AUDIT_TEST_JOBS=%s) — an iteration run, not the serial gate\n' "$jobs"
  sp_suite="$(cd "$(dirname "$suite")" && pwd)/$(basename "$suite")"
  sp_rundir=$(mktemp -d "${TMPDIR:-/tmp}/audit-suite.XXXXXX") || sp_usage "could not create a run directory"
  # Keep the sourcing suite's own EXIT trap: trap -p prints `trap -- '<quoted command>' EXIT`, or nothing
  # when none is set. It goes through a file because a command substitution is a subshell, and bash 3.2
  # reports no traps there. The quoted word is un-quoted with eval into sp_prev_exit.
  sp_prev_exit=""
  trap -p EXIT > "$sp_rundir/prev.trap"
  sp_prev_trap=$(< "$sp_rundir/prev.trap")
  if [ -n "$sp_prev_trap" ]; then
    sp_prev_trap=${sp_prev_trap#trap -- }
    sp_prev_trap=${sp_prev_trap% EXIT}
    eval "sp_prev_exit=$sp_prev_trap"
  fi
  trap 'sp_on_signal TERM' TERM
  trap 'sp_on_signal INT' INT
  trap 'sp_on_signal HUP' HUP
  trap 'sp_on_exit' EXIT
  printf 'SUITE DRIVER: run directory %s, width %s\n' "$sp_rundir" "$jobs"

  env -u AUDIT_TEST_JOBS -u AUDIT_TEST_ONLY AUDIT_TEST_UNIT=--list AUDIT_TEST_RUNDIR="$sp_rundir" \
    "$BASH" "$sp_suite" > "$sp_rundir/list.out" 2>&1 || list_rc=$?
  if [ "$list_rc" -ne 0 ] || [ ! -s "$sp_rundir/units.list" ]; then
    printf 'FAIL  listing the units of %s failed (exit %s); its output:\n' "$sp_suite" "$list_rc"
    tail -n 20 "$sp_rundir/list.out"
    return 1
  fi
  outside=$(cat "$sp_rundir/outside.count" 2>/dev/null || printf 'missing')
  if [ "$outside" != 0 ]; then
    printf 'FAIL  rows outside every unit (%s): each row must sit inside an if unit_run wrapper; the listing pass printed:\n' "$outside"
    tail -n 20 "$sp_rundir/list.out"
    return 1
  fi
  while read -r name _; do
    sp_names[total]=$name
    sp_state[total]=0
    total=$((total + 1))
  done < <(sort -k2,2nr -k1,1 "$sp_rundir/units.list")
  # Judged on the ORDERED list, the one that will run: a sort that fails or prints nothing leaves
  # total at 0, and the aggregation below would then pass over zero units.
  nlist=$(wc -l < "$sp_rundir/units.list" | tr -d ' ')
  if [ "$total" -eq 0 ] || [ "$total" -ne "$nlist" ]; then
    printf 'FAIL  the unit list could not be ordered: %s listed, %s ordered\n' "$nlist" "$total"
    return 1
  fi
  for name in $SP_FLOOR; do
    found=0
    for ((i = 0; i < total; i++)); do
      [ "${sp_names[$i]}" = "$name" ] && found=1
    done
    [ "$found" -eq 1 ] || missing="$missing $name"
  done
  if [ -n "$missing" ]; then
    printf 'FAIL  units missing from the suite (declared floor):%s\n' "$missing"
    return 1
  fi

  while [ "$finished" -lt "$total" ]; do
    for ((i = 0; i < total; i++)); do
      [ "${sp_state[$i]}" = 1 ] || continue
      if ! kill -0 "${sp_pids[$i]}" 2>/dev/null; then
        sp_finish "$i"
        running=$((running - 1))
        finished=$((finished + 1))
      fi
    done
    while [ "$running" -lt "$jobs" ] && [ "$next" -lt "$total" ]; do
      sp_launch "$next"
      next=$((next + 1))
      running=$((running + 1))
    done
    [ "$finished" -lt "$total" ] && sleep 0.2
  done

  for ((i = 0; i < total; i++)); do
    name=${sp_names[$i]}
    rec="$sp_rundir/$name.rec"
    if [ ! -f "$rec" ]; then
      verdict="$verdict  $name: no result record (crashed, killed or silent; exit ${sp_rcs[$i]})"$'\n'
      failed=1
      continue
    fi
    read -r f1 f2 f3 f4 < "$rec"
    ru=${f1#unit=}
    rp=${f2#pass=}
    rf=${f3#fail=}
    rs=${f4#skip=}
    if [ "$ru" != "$name" ] || ! sp_num "$rp" || ! sp_num "$rf" || ! sp_num "$rs"; then
      verdict="$verdict  $name: unreadable result record"$'\n'
      failed=1
      continue
    fi
    tp=$((tp + rp))
    tf=$((tf + rf))
    ts=$((ts + rs))
    if [ "$rf" -gt 0 ]; then
      verdict="$verdict  $name: $rf failing row(s)"$'\n'
      failed=1
    elif [ "${sp_rcs[$i]}" -ne 0 ]; then
      verdict="$verdict  $name: exit status ${sp_rcs[$i]}"$'\n'
      failed=1
    elif [ $((rp + rf)) -eq 0 ] && [ "$rs" -eq 0 ]; then
      verdict="$verdict  $name: ran no rows and counted no skip"$'\n'
      failed=1
    fi
  done

  printf '\nSUITE DRIVER: %d units, %d passed, %d failed, %d skip line(s), width %s\n' \
    "$total" "$tp" "$tf" "$ts" "$jobs"
  if [ "$failed" -eq 0 ]; then
    printf '\n%d passed, %d failed\n' "$tp" "$tf"
    return 0
  fi
  printf 'FAIL  parallel-profile verdict:\n%s' "$verdict"
  printf '\n%d passed, %d failed\n' "$tp" "$tf"
  printf 'NOTE: this run used the parallel profile (AUDIT_TEST_JOBS=%s), not the serial gate; a red here is a finding to re-run serially (AUDIT_TEST_JOBS=1 bash %s), not load to dismiss\n' \
    "$jobs" "$sp_suite"
  : > "$sp_rundir/digest.all"
  for ((i = 0; i < total; i++)); do
    grep -h '^FAIL  ' "$sp_rundir/${sp_names[$i]}.out" >> "$sp_rundir/digest.all" 2>/dev/null || true
  done
  nfail=$(wc -l < "$sp_rundir/digest.all" | tr -d ' ')
  shown=$nfail
  [ "$shown" -gt 30 ] && shown=30
  head -n "$shown" "$sp_rundir/digest.all"
  if [ "$nfail" -gt "$shown" ]; then
    printf '... and %d more failing row(s); see the unit output above\n' "$((nfail - shown))"
  fi
  return 1
}

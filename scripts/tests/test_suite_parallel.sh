#!/usr/bin/env bash
set -uo pipefail

# Script: test_suite_parallel.sh
# Purpose: Regression tests for scripts/lib/suite_parallel.sh — the unit protocol, the token rule for
#          child mode, the fail-closed aggregation of the parallel profile, and the driver's signals.
# Usage:   ./scripts/tests/test_suite_parallel.sh
#
# Every driver row runs a small FIXTURE suite that sources the real library, so the library is
# judged without paying for scripts/tests/test_audit.sh. A last group of rows runs the REAL
# test_audit.sh in its cheap modes only (a usage error at the top of the file, or the listing pass,
# which executes the preamble and the unit wrappers and no row), so a change that breaks how the driver
# works with the real file's wrappers is caught here, at the edit that caused it.

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
lib="$here/../lib/suite_parallel.sh"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

# The driver's run directories land under this TMPDIR, so a leaked one is visible to a row.
export TMPDIR="$tmp/driver-tmp"
mkdir -p "$TMPDIR"
# An exported selector would change every row below; the rows that need one set it explicitly.
unset AUDIT_TEST_JOBS AUDIT_TEST_ONLY AUDIT_TEST_UNIT AUDIT_TEST_RUNDIR

pass=0
fail=0
pass_line() { printf 'PASS  %s\n' "$1"; pass=$((pass + 1)); }
fail_line() { printf 'FAIL  %s\n' "$1"; fail=$((fail + 1)); }
check_eq() { # got want label
  if [ "$1" = "$2" ]; then pass_line "$3"; else fail_line "$3 (want [$2] got [$1])"; fi
}
check_has() { # haystack needle label
  case "$1" in
    *"$2"*) pass_line "$3" ;;
    *) fail_line "$3 (output lacked [$2]; began: $(printf '%s' "$1" | head -3 | tr '\n' ' '))" ;;
  esac
}
check_lacks() { # haystack needle label
  case "$1" in
    *"$2"*) fail_line "$3 (output unexpectedly contained [$2])" ;;
    *) pass_line "$3" ;;
  esac
}
check_nonzero() { # value label
  case "$1" in
    '' | 0 | *[!0-9]*) fail_line "$2 (got [$1])" ;;
    *) pass_line "$2" ;;
  esac
}

# fx_suite FILE FLOOR — write a fixture suite sourcing the real library; the unit bodies come on stdin.
fx_suite() {
  {
    printf '#!/usr/bin/env bash\nset -uo pipefail\n. "%s"\n' "$lib"
    cat << 'HDR'
pass=0
fail=0
pass_line() { printf 'PASS  %s\n' "$1"; pass=$((pass + 1)); }
fail_line() { printf 'FAIL  %s\n' "$1"; fail=$((fail + 1)); }
trap '[ -n "${FX_EXIT_MARK:-}" ] && [ -n "${AUDIT_TEST_JOBS:-}" ] && [ -z "${AUDIT_TEST_UNIT:-}" ] && : >> "$FX_EXIT_MARK"' EXIT
HDR
    # shellcheck disable=SC2016  # ${BASH_SOURCE[0]} belongs to the generated suite
    printf 'sp_init "${BASH_SOURCE[0]}" "%s"\n' "$2"
    cat
    # shellcheck disable=SC2016  # $pass and $fail belong to the generated suite
    printf 'unit_finish\nprintf "\\n%%d passed, %%d failed\\n" "$pass" "$fail"\n[[ "$fail" -eq 0 ]]\n'
  } > "$1"
}

OUT=""
RC=0
# run_fx FILE [NAME=VALUE ...] — OUT is stdout and stderr merged, RC the exit status.
run_fx() {
  local f="$1"
  shift
  OUT="$(env "$@" bash "$f" 2>&1)"
  RC=$?
}
last_line() { printf '%s\n' "$OUT" | tail -n 1; }

# ---- A: the serial profile is untouched, and the parallel profile agrees with it ---------------------
s="$tmp/equiv.sh"
fx_suite "$s" "a b c" << 'EOB'
if unit_run a 5; then
  for i in 1 2 3; do pass_line "a row $i"; done
fi
if unit_run b 50; then
  pass_line "b row 1"
  pass_line "jobs=${AUDIT_TEST_JOBS-UNSET}"
fi
if unit_run c 20; then
  pass_line "c row 1"
  pass_line "c row 2"
fi
EOB
run_fx "$s"
serial_out=$OUT
check_eq "$RC" 0 'A1: the serial profile of a passing fixture exits 0'
check_has "$serial_out" '7 passed, 0 failed' 'A1: the serial profile prints the ordinary summary'
run_fx "$s" AUDIT_TEST_JOBS=1
# (the fixture's own row prints the AUDIT_TEST_JOBS it sees, so that one row is set aside)
check_eq "$(printf '%s\n' "$OUT" | grep -v 'jobs=')" "$(printf '%s\n' "$serial_out" | grep -v 'jobs=')" \
  'A2: AUDIT_TEST_JOBS=1 is the serial profile byte for byte'
rm -f "$tmp/mark_a"
run_fx "$s" AUDIT_TEST_JOBS=2 "FX_EXIT_MARK=$tmp/mark_a"
par_out=$OUT
check_eq "$RC:$([ -e "$tmp/mark_a" ] && echo marker || echo nomarker)" '0:marker' 'A3: the parallel profile of a passing fixture exits 0, and the suite'"'"'s own EXIT trap still ran'
ser_rows=$(printf '%s\n' "$serial_out" | grep '^PASS  ' | grep -v 'jobs=' | sort)
par_rows=$(printf '%s\n' "$par_out" | grep '^PASS  ' | grep -v 'jobs=' | sort)
check_nonzero "$(printf '%s' "$ser_rows" | grep -c .)" 'A4: the serial reading is non-empty before the comparison'
check_nonzero "$(printf '%s' "$par_rows" | grep -c .)" 'A4: the parallel reading is non-empty before the comparison'
check_eq "$par_rows" "$ser_rows" 'A4: the parallel profile prints the same multiset of PASS rows as the serial one'
check_eq "$(last_line)" '7 passed, 0 failed' 'A5: the parallel profile ends in the aggregate summary'
check_eq "$(printf '%s\n' "$par_out" | head -n 1)" 'NOTE: parallel profile (AUDIT_TEST_JOBS=2) — an iteration run, not the serial gate' \
  'A7: a PASSING parallel run opens with the note that it is an iteration run, not the serial gate'
check_has "$par_out" 'PASS  jobs=UNSET' 'A6: a child never sees AUDIT_TEST_JOBS, so it cannot become a driver'

# ---- B: a failing row fails the run, and the failure is the LAST thing printed -----------------------
s="$tmp/failing.sh"
fx_suite "$s" "a b" << 'EOB'
if unit_run a 9; then
  for i in $(seq 1 70); do pass_line "a bulk $i"; done
fi
if unit_run b 5; then
  pass_line "b ok"
  fail_line "boom b"
fi
EOB
rm -f "$tmp/mark_b"
run_fx "$s" AUDIT_TEST_JOBS=2 "FX_EXIT_MARK=$tmp/mark_b"
check_eq "$RC:$([ -e "$tmp/mark_b" ] && echo marker || echo nomarker)" '1:marker' 'B1: a failing row in one unit fails the parallel run, and the suite'"'"'s own EXIT trap still ran'
check_has "$OUT" 'b: 1 failing row(s)' 'B2: the failing unit is named with its failing-row count'
check_eq "$(last_line)" 'FAIL  boom b' 'B3: the failing row is the last output line, so a tail keeps it'
check_nonzero "$(printf '%s\n' "$OUT" | grep -c '^PASS  ')" 'B4: the run printed PASS rows ahead of the failure (the shape a tail cap would eat)'
check_has "$OUT" 'a red here is a finding to re-run serially' 'B5: a red parallel run says it is not the serial gate'
check_eq "$(printf '%s\n' "$OUT" | head -n 1)" 'NOTE: parallel profile (AUDIT_TEST_JOBS=2) — an iteration run, not the serial gate' 'B6: a failing parallel run opens with the same note'

# ---- C: a crashed, killed or silent unit fails the run -----------------------------------------------
s="$tmp/crash.sh"
fx_suite "$s" "a k q" << 'EOB'
if unit_run a 5; then pass_line "a ok"; fi
if unit_run k 9; then pass_line "k before the kill"; kill -9 $$; fi
if unit_run q 3; then exit 0; fi
EOB
run_fx "$s" AUDIT_TEST_JOBS=3
check_eq "$RC" 1 'C1: a SIGKILLed unit and a silently-exiting unit fail the run'
check_has "$OUT" 'k: no result record' 'C2: the killed unit is named as having no result record'
check_has "$OUT" 'q: no result record' 'C3: the silent unit is named as having no result record'

s="$tmp/exitcode.sh"
fx_suite "$s" "a e" << 'EOB'
if unit_run a 5; then pass_line "a ok"; fi
if unit_run e 3; then
  trap 'exit 7' EXIT
  pass_line "e ok"
fi
EOB
run_fx "$s" AUDIT_TEST_JOBS=2
check_eq "$RC" 1 'C4: a unit that wrote its record and then exited non-zero fails the run'
check_has "$OUT" 'e: exit status 7' 'C4: the exit status is named'

# ---- D: a deleted row, unit or wrapper is never quieter than a failing one ---------------------------
s="$tmp/floor.sh"
fx_suite "$s" "a gone" << 'EOB'
if unit_run a 5; then pass_line "a ok"; fi
EOB
run_fx "$s" AUDIT_TEST_JOBS=2
check_eq "$RC" 1 'D1: a unit that lost its wrapper (named in the floor) fails the run'
check_has "$OUT" 'units missing from the suite (declared floor): gone' 'D2: the missing unit is named'

s="$tmp/outside.sh"
fx_suite "$s" "a" << 'EOB'
if unit_run a 5; then pass_line "a ok"; fi
pass_line "a row outside every unit"
EOB
run_fx "$s" AUDIT_TEST_JOBS=2
check_eq "$RC" 1 'D3: a row outside every unit fails the parallel run'
check_has "$OUT" 'rows outside every unit' 'D4: the failure says why'

s="$tmp/zero.sh"
fx_suite "$s" "a z" << 'EOB'
if unit_run a 5; then pass_line "a ok"; fi
if unit_run z 3; then :; fi
EOB
run_fx "$s" AUDIT_TEST_JOBS=2
check_eq "$RC" 1 'D5: a unit with no rows and no counted skip fails the run'
check_has "$OUT" 'z: ran no rows and counted no skip' 'D6: the failure names the unit and the reason'

s="$tmp/zero_skip.sh"
fx_suite "$s" "a z" << 'EOB'
if unit_run a 5; then pass_line "a ok"; fi
if unit_run z 3; then skip_line "skip - tool absent in this test environment"; fi
EOB
run_fx "$s" AUDIT_TEST_JOBS=2
check_eq "$RC" 0 'D7: a unit with no rows and a counted skip passes, as it does serially'

s="$tmp/zero_echo.sh"
fx_suite "$s" "a z" << 'EOB'
if unit_run a 5; then pass_line "a ok"; fi
if unit_run z 3; then printf 'skip - text that only looks like a counted skip\n'; fi
EOB
run_fx "$s" AUDIT_TEST_JOBS=2
check_eq "$RC" 1 'D8: skip-shaped stdout text does not stand in for a counted skip'

s="$tmp/dup.sh"
fx_suite "$s" "a" << 'EOB'
if unit_run a 5; then pass_line "a ok"; fi
if unit_run a 5; then pass_line "a again"; fi
EOB
run_fx "$s" AUDIT_TEST_JOBS=2
check_eq "$RC" 1 'D9: a unit declared twice fails the parallel run'

# The floor is declared empty here on purpose, so only the count of the ordered list can notice that a
# sort which prints nothing leaves zero units to run.
s="$tmp/sortfail.sh"
fx_suite "$s" "" << 'EOB'
if unit_run a 5; then pass_line "a ok"; fi
EOB
mkdir -p "$tmp/shim_sort"
printf '#!/bin/sh\nexit 0\n' > "$tmp/shim_sort/sort"
chmod +x "$tmp/shim_sort/sort"
run_fx "$s" AUDIT_TEST_JOBS=2 "PATH=$tmp/shim_sort:$PATH"
check_eq "$RC" 1 'D10: an ordering that yields no units fails the parallel run instead of passing over zero units'
check_has "$OUT" 'the unit list could not be ordered' 'D10: the failure says why'

# ---- E: child mode is keyed on a token, never on a name ----------------------------------------------
s="$tmp/token.sh"
fx_suite "$s" "a" << 'EOB'
if unit_run a 5; then pass_line "a ok"; fi
EOB
run_fx "$s" AUDIT_TEST_UNIT=a
check_eq "$RC" 2 'E1: a valid exported unit name with no run directory is a usage error'
check_has "$OUT" 'AUDIT_TEST_RUNDIR' 'E1: the error names the missing variable'
check_lacks "$OUT" 'PASS' 'E1: it fails before any row runs'
d="$tmp/rd_only"
mkdir -p "$d"
run_fx "$s" AUDIT_TEST_RUNDIR="$d"
check_eq "$RC" 2 'E2: a run directory with no unit name is a usage error'
check_has "$OUT" 'AUDIT_TEST_UNIT' 'E2: the error names the missing variable'
run_fx "$s" AUDIT_TEST_UNIT=a AUDIT_TEST_RUNDIR="$tmp/no-such-dir"
check_eq "$RC" 2 'E3: a run directory that does not exist is a usage error, not a silent serial run'
check_lacks "$OUT" 'PASS' 'E3: it fails before any row runs'
run_fx "$s" AUDIT_TEST_UNIT=a AUDIT_TEST_RUNDIR="$d"
check_eq "$RC" 0 'E4: CONTROL — both variables and an existing directory enter child mode'
check_eq "$(cat "$d/a.rec" 2>/dev/null)" 'unit=a pass=1 fail=0 skip=0' 'E4: child mode writes the result record'
run_fx "$s" AUDIT_TEST_UNIT=nosuch AUDIT_TEST_RUNDIR="$d"
check_eq "$RC" 2 'E5: a unit name matching no wrapper exits 2, never 0 passed and rc 0'
run_fx "$s" AUDIT_TEST_UNIT='bad name!' AUDIT_TEST_RUNDIR="$d"
check_eq "$RC" 2 'E6: a malformed unit name is a usage error'

# ---- F: argument validation ---------------------------------------------------------------------------
for bad in 0 08 abc 12345 ''; do
  run_fx "$s" "AUDIT_TEST_JOBS=$bad"
  check_eq "$RC" 2 "F1: AUDIT_TEST_JOBS='$bad' is a usage error"
done
check_has "$OUT" 'AUDIT_TEST_JOBS' 'F1: the error names the variable'
run_fx "$s" AUDIT_TEST_ONLY=bogus
check_eq "$RC" 2 'F2: an unknown AUDIT_TEST_ONLY value is a usage error, not a silent full run'
run_fx "$s" AUDIT_TEST_ONLY=rML AUDIT_TEST_JOBS=2
check_eq "$RC" 2 'F3: AUDIT_TEST_ONLY combined with a parallel width is refused'
check_has "$OUT" 'AUDIT_TEST_ONLY' 'F3: the error names both variables'
run_fx "$s" AUDIT_TEST_ONLY=rML AUDIT_TEST_UNIT=a AUDIT_TEST_RUNDIR="$d"
check_eq "$RC" 2 'F4: AUDIT_TEST_ONLY combined with the child-mode variables is refused'
s2="$tmp/note.sh"
fx_suite "$s2" "rML" << 'EOB'
if unit_run rML 5; then pass_line "rML ok"; fi
EOB
run_fx "$s2" AUDIT_TEST_ONLY=rML
note_first="$(printf '%s\n' "$OUT" | head -n 1)"
run_fx "$s2"
note_control="$(printf '%s\n' "$OUT" | grep -c 'partial run')"
check_eq "$note_first|$note_control" 'NOTE: partial run (AUDIT_TEST_ONLY=rML), not the gate|0' 'F4b: a partial run opens with the not-the-gate note, and an unset run prints none'
s3="$tmp/third.sh"
fx_suite "$s3" "a" << 'EOB'
if unit_run a 5 serial; then pass_line "a ok"; fi
EOB
run_fx "$s3"
check_eq "$RC" 2 'F5: unit_run rejects a third argument, so a dropped word cannot be silently ignored'
s3="$tmp/weight.sh"
fx_suite "$s3" "a" << 'EOB'
if unit_run a 0; then pass_line "a ok"; fi
EOB
run_fx "$s3"
check_eq "$RC" 2 'F6: unit_run rejects a weight that is not a positive integer'

# ---- G: weights order the launch; the listing pass is the executable unit list -----------------------
s="$tmp/order.sh"
fx_suite "$s" "lo hi mid" << 'EOB'
if unit_run lo 5; then pass_line "lo ok"; fi
if unit_run hi 50; then pass_line "hi ok"; fi
if unit_run mid 20; then pass_line "mid ok"; fi
EOB
d="$tmp/rd_list"
mkdir -p "$d"
run_fx "$s" AUDIT_TEST_UNIT=--list AUDIT_TEST_RUNDIR="$d"
check_eq "$RC" 0 'G1: the listing pass exits 0'
check_eq "$(cat "$d/units.list" 2>/dev/null | tr '\n' ',')" 'lo 5,hi 50,mid 20,' 'G1: the listing pass prints every unit with its weight, in file order'
check_eq "$(cat "$d/outside.count" 2>/dev/null)" 0 'G1: the listing pass counts no row outside the units'
run_fx "$s" AUDIT_TEST_JOBS=2
started=$(printf '%s\n' "$OUT" | grep '^unit .* started$' | sed 's/^unit \(.*\) started$/\1/' | tr '\n' ',')
check_eq "$started" 'hi,mid,lo,' 'G2: units start in descending weight order'

# ---- K: the library follows hook_budget.sh -----------------------------------------------------------
lib_out=$(bash "$lib" 2>&1)
check_eq "$?" 2 'K1: executing the library directly is refused'
check_has "$lib_out" 'source it, do not execute it' 'K1: the refusal says how to use it'
if [ -x "$lib" ]; then pass_line 'K2: the library is executable, like hook_budget.sh'; else fail_line 'K2: the library lost its exec bit'; fi

# ---- H: the driver's signals ----------------------------------------------------------------------------
sig_mark="SPSLEEP_$$"
m="$tmp/sig"
mkdir -p "$m"
s="$tmp/sig.sh"
fx_suite "$s" "a b" << 'EOB'
if unit_run a 9; then
  python3 -c 'import time; time.sleep(300)' @MARK@_A &
  : > "@M@/a.up"
  wait
fi
if unit_run b 5; then
  python3 -c 'import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(300)' @MARK@_B &
  : > "@M@/b.up"
  wait
fi
EOB
sed -e "s|@MARK@|$sig_mark|g" -e "s|@M@|$m|g" "$s" > "$s.sub" && mv "$s.sub" "$s"

wait_up() { # up to 10 s for both units to be running
  local _
  for _ in $(seq 1 50); do
    [ -e "$m/a.up" ] && [ -e "$m/b.up" ] && return 0
    sleep 0.2
  done
  return 1
}

rm -f "$m"/*.up "$tmp/mark_h"
FX_EXIT_MARK="$tmp/mark_h" AUDIT_TEST_JOBS=2 bash "$s" > "$m/out" 2>&1 &
dpid=$!
if wait_up; then pass_line 'H0: both sleeping units are running'; else fail_line 'H0: the sleeping units did not start'; fi
kill -TERM "$dpid"
wait "$dpid"
drc=$?
check_eq "$drc:$([ -e "$tmp/mark_h" ] && echo marker || echo nomarker)" '143:marker' 'H1: TERM to the driver pid alone exits 143, and the suite'"'"'s own EXIT trap still ran'
sig_out=$(cat "$m/out")
check_has "$sig_out" 'RUNNING a' 'H2: the TERM report names a unit that was still running'
check_has "$sig_out" 'RUNNING b' 'H2: ...and the other one'
sleep 0.5
check_eq "$(pgrep -f "$sig_mark" | wc -l | tr -d ' ')" 0 'H3: no child or descendant survived a driver-only TERM, including one that ignores TERM'
rd=$(printf '%s\n' "$sig_out" | sed -n 's/^SUITE DRIVER: run directory \(.*\), width.*/\1/p')
if [ -n "$rd" ] && [ ! -d "$rd" ]; then pass_line 'H4: the run directory is gone'; else fail_line "H4: the run directory remains or was not announced ($rd)"; fi

# The report comes BEFORE any wait: the driver is KILLed one second after its TERM, while a
# TERM-ignoring child still holds it in the stop loop, and the report is already in the output.
rm -f "$m"/*.up "$m/out"
AUDIT_TEST_JOBS=2 bash "$s" > "$m/out" 2>&1 &
dpid=$!
wait_up
kill -TERM "$dpid"
sleep 1
kill -KILL "$dpid" 2> /dev/null
wait "$dpid" 2> /dev/null
check_has "$(cat "$m/out")" 'RUNNING b' 'H5: the report was written before the driver could be KILLed'
pkill -f "$sig_mark" 2> /dev/null
sleep 0.3
rm -rf "$TMPDIR"/audit-suite.* 2> /dev/null

# A driver run with stderr closed still fails when a unit fails (the broken-pipe exit-status shape).
s="$tmp/closed.sh"
fx_suite "$s" "a" << 'EOB'
if unit_run a 5; then fail_line "boom"; fi
EOB
AUDIT_TEST_JOBS=2 bash "$s" > /dev/null 2>&-
check_eq "$?" 1 'H6: a failing unit still exits 1 with stderr closed'

# The REAL suite and the floor it declares to sp_init. A row pins the declaration to this list, so
# moving the floor is itself a failure that needs this file to change too.
real_suite="$here/test_audit.sh"
real_floor="rML rPT cases1-14 case15 rAM rN rO rMA rPP rQ rTGC rSC"

# ---- I: the REAL test_audit.sh, in its cheap modes only ----------------------------------------------
real_out=$(env AUDIT_TEST_UNIT=rML bash "$real_suite" 2>&1)
real_rc=$?
check_eq "$real_rc" 2 'I1: the REAL suite with a valid exported unit name and no run directory exits 2'
check_lacks "$real_out" 'PASS' 'I1: ...before any row'
mkdir -p "$tmp/rd_only"
real_out=$(env AUDIT_TEST_RUNDIR="$tmp/rd_only" bash "$real_suite" 2>&1)
check_eq "$?" 2 'I2: the REAL suite with a run directory and no unit name exits 2'
real_out=$(env AUDIT_TEST_UNIT=rML AUDIT_TEST_RUNDIR="$tmp/no-such-dir" bash "$real_suite" 2>&1)
check_eq "$?" 2 'I3: the REAL suite with a run directory that does not exist exits 2'
check_lacks "$real_out" 'PASS' 'I3: ...before any row, so a name alone never runs a unit'
for bad in 0 08 abc 12345 ''; do
  real_out=$(env "AUDIT_TEST_JOBS=$bad" bash "$real_suite" 2>&1)
  check_eq "$?" 2 "I4: the REAL suite with AUDIT_TEST_JOBS='$bad' exits 2"
done
real_out=$(env AUDIT_TEST_ONLY=bogus bash "$real_suite" 2>&1)
check_eq "$?" 2 'I5: the REAL suite with an unknown AUDIT_TEST_ONLY exits 2'
real_out=$(env AUDIT_TEST_ONLY=rML AUDIT_TEST_JOBS=2 bash "$real_suite" 2>&1)
check_eq "$?" 2 'I6: the REAL suite with AUDIT_TEST_ONLY and a parallel width exits 2'

d="$tmp/rd_real_list"
mkdir -p "$d"
real_out=$(env AUDIT_TEST_UNIT=--list AUDIT_TEST_RUNDIR="$d" bash "$real_suite" 2>&1)
real_rc=$?
check_eq "$real_rc" 0 'I7: the listing pass over the REAL suite exits 0'
check_eq "$(cat "$d/outside.count" 2> /dev/null)" 0 'I7: ...and counts no row outside the units'
listed=$(awk '{print $1}' "$d/units.list" 2> /dev/null | tr '\n' ' ')
check_nonzero "$(printf '%s' "$listed" | wc -w | tr -d ' ')" 'I8: the listing of the REAL suite is non-empty'
missing_units=""
for u in $real_floor; do
  case " $listed" in
    *" $u "*) ;;
    *) missing_units="$missing_units $u" ;;
  esac
done
check_eq "$missing_units" "" 'I8: every unit of the declared floor is listed by the REAL suite'
check_eq "$(grep -c "^sp_init \"\${BASH_SOURCE\[0\]}\" \"$real_floor\"\$" "$real_suite")" 1 \
  'I9: the REAL suite declares exactly this floor to sp_init'
check_eq "$(awk '$2 !~ /^[1-9][0-9]*$/' "$d/units.list" | wc -l | tr -d ' ')" 0 'I10: every listed weight is a positive integer'

check_eq "$(grep -c "printf 'skip -" "$real_suite")" 0 'I11: no bare skip-printing printf remains in the REAL suite'
skip_calls=$(grep -c "^ *skip_line 'skip - " "$real_suite")
if [ "$skip_calls" -ge 19 ]; then pass_line "I12: the REAL suite has at least 19 skip_line calls ($skip_calls)"; else fail_line "I12: only $skip_calls skip_line calls (want at least 19)"; fi

# cross_unit_calls FILE — "name defined-in-unit used-in-unit" for every top-level (column 0) function defined
# inside one unit and mentioned in another or between units. A unit runs from its `if unit_run NAME` line
# to the next `fi # unit-end` line, the exact closer every wrapper carries, so code between two units is
# "between" and belongs to none. Functions only: a variable shared between units is caught by the
# parallel profile itself, whose children are each one unit run alone under `set -u`.
cross_unit_calls() {
  awk '
    { line[NR] = $0 }
    /^if unit_run / { n++; start[n] = NR; uname[n] = $3 }
    /^fi # unit-end$/ { stop[n] = NR }
    END {
      for (k = 1; k <= n; k++)
        for (i = start[k]; i <= stop[k]; i++) owner[i] = k
      for (i = 1; i <= NR; i++)
        if (line[i] ~ /^[A-Za-z_][A-Za-z0-9_]*\(\) *\{/ && owner[i] > 0) {
          f = line[i]; sub(/\(\).*/, "", f); def[f] = owner[i]
        }
      for (f in def)
        for (i = 1; i <= NR; i++) {
          if (owner[i] == def[f] || line[i] ~ /^[ \t]*#/) continue
          if (line[i] ~ ("(^|[^A-Za-z0-9_])" f "([^A-Za-z0-9_]|$)")) {
            print f, uname[def[f]], (owner[i] ? uname[owner[i]] : "between")
            break
          }
        }
    }' "$1"
}
cf="$tmp/cross.sh"
cat > "$cf" << 'EOB'
if unit_run a 1; then
helper() { :; }
fi # unit-end
if unit_run b 1; then
helper
fi # unit-end
EOB
check_eq "$(cross_unit_calls "$cf")" 'helper a b' 'I13: CONTROL — the scan finds a function defined in one unit and called in another'
cat > "$cf" << 'EOB'
if unit_run a 1; then
helper() { :; }
fi # unit-end
helper
if unit_run b 1; then
:
fi # unit-end
EOB
check_eq "$(cross_unit_calls "$cf")" 'helper a between' 'I13: CONTROL — the scan finds a call made between two units'
check_eq "$(cross_unit_calls "$real_suite" | tr '\n' ';')" "" 'I13: no function of the REAL suite is defined in one unit and called in another or between units'
check_eq "$(grep -c '^fi # unit-end$' "$real_suite")" 12 'I13: the REAL suite closes exactly 12 unit wrappers with the unit-end closer'

# suite_tail_ok FILE — the last three non-blank lines are unit_finish and the two summary lines. unit_finish
# EXITS in the listing pass and in a child, so a row placed between it and the summary would run serially
# and in no child, and nothing else would see it.
suite_tail_ok() {
  local want got
  want=$(
    cat << 'EOT'
unit_finish
printf '\n%d passed, %d failed\n' "$pass" "$fail"
[[ "$fail" -eq 0 ]]
EOT
  )
  got=$(grep -v '^[[:space:]]*$' "$1" | tail -n 3)
  [ "$got" = "$want" ]
}
cat > "$cf" << 'EOB'
if unit_run a 1; then :; fi # unit-end
unit_finish
printf '\n%d passed, %d failed\n' "$pass" "$fail"
[[ "$fail" -eq 0 ]]
EOB
if suite_tail_ok "$cf"; then pass_line 'I14: CONTROL — a file ending in unit_finish and the summary passes the tail check'; else fail_line 'I14: CONTROL — a correct tail was refused'; fi
cat > "$cf" << 'EOB'
if unit_run a 1; then :; fi # unit-end
unit_finish
pass_line "a row after unit_finish"
printf '\n%d passed, %d failed\n' "$pass" "$fail"
[[ "$fail" -eq 0 ]]
EOB
if suite_tail_ok "$cf"; then fail_line 'I14: CONTROL — a row between unit_finish and the summary went unseen'; else pass_line 'I14: CONTROL — a row between unit_finish and the summary is refused'; fi
if suite_tail_ok "$real_suite"; then pass_line 'I14: the REAL suite ends in unit_finish and the two summary lines'; else fail_line 'I14: the REAL suite has code between unit_finish and its summary'; fi

printf '\n%d passed, %d failed\n' "$pass" "$fail"
[[ "$fail" -eq 0 ]]

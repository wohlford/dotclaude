#!/usr/bin/env bash
set -uo pipefail

# Script: test_run_long.sh
# Purpose: Regression tests for scripts/run-long.sh — the background launcher that records a
#          long job's real exit status INSIDE its artifact. Covers the launch/status split,
#          the three status verdicts (DONE/RUNNING/DIED), the no-default-output-path rule,
#          clobber refusal, and the structural guarantee that the launcher's own exit status
#          carries no information about the work.
# Usage:   ./scripts/tests/test_run_long.sh

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
engine="$here/../run-long.sh"

# `pwd -P` pins the PHYSICAL path: $TMPDIR is a symlink on macOS (/tmp -> /private/tmp), and a
# logical path that does not physically contain the artifact lets a path-resolving subject take a
# different branch and never reach what these rows exist to test.
tmp="$(cd "$(mktemp -d)" && pwd -P)"
trap 'rm -rf "$tmp"' EXIT

pass=0
fail=0

pass_line() { printf 'PASS  %s\n' "$1"; pass=$((pass + 1)); }
fail_line() { printf 'FAIL  %s\n' "$1"; fail=$((fail + 1)); }

check_eq() { # got want label
  if [[ "$1" == "$2" ]]; then
    pass_line "$3"
  else
    fail_line "$3 (want [$2] got [$1])"
  fi
}

check_contains() { # haystack needle label
  case "$1" in
    *"$2"*) pass_line "$3" ;;
    *)
      fail_line "$3 (missing [$2])"
      printf '  --- output ---\n%s\n  --------------\n' "$1"
      ;;
  esac
}

check_absent() { # haystack needle label
  case "$1" in
    *"$2"*)
      fail_line "$3 (unexpectedly present: [$2])"
      printf '  --- output ---\n%s\n  --------------\n' "$1"
      ;;
    *) pass_line "$3" ;;
  esac
}

# The engine is invoked BARE-PATH ("$engine", never `bash "$engine"`) so the suite also
# exercises the exec bit and the shebang.
OUT=""
RC=0
run() {
  OUT="$("$engine" "$@" 2>&1)"
  RC=$?
}

# wait_done ARTIFACT [tries] — poll for the trailer rather than sleeping a fixed guess.
wait_done() {
  local art="$1" tries="${2:-100}" i=0
  while [[ $i -lt $tries ]]; do
    if [[ -f "$art" ]] && grep -q '^RUN_LONG_EXIT_STATUS=' "$art"; then return 0; fi
    sleep 0.05
    i=$((i + 1))
  done
  return 1
}

art() { printf '%s/%s.log' "$tmp" "$1"; }

# ---------------------------------------------------------------- usage / argument handling

run
check_eq "$RC" 2 'u1: bare invocation is a usage error'
check_contains "$OUT" 'Usage:' 'u1b: bare invocation prints the usage synopsis'

run --help
check_eq "$RC" 0 'u2: --help exits 0'
check_contains "$OUT" '--status' 'u2b: --help documents the status mode'

run --nonsense
check_eq "$RC" 2 'u3: an unknown flag is a usage error'

run --out "$(art u4)"
check_eq "$RC" 2 'u4: --out with no command is a usage error'

run --out "$(art u5)" --
check_eq "$RC" 2 'u5: --out with an empty command is a usage error'

# Run this one from INSIDE the sandbox. A mutant that introduces a default destination writes to
# the cwd, and with the campaign's cwd set to the repo that lands a stray artifact in the repo
# root — measured, once, untracked and not gitignored. mutate.py restores the SUBJECT; it cannot
# un-write files a mutant creates elsewhere, so the suite has to deny it a writable cwd.
u6_out="$(cd "$tmp" && "$engine" -- true 2>&1)"
u6_rc=$?
check_eq "$u6_rc" 2 'u6: a command with no --out is a usage error (no default output path)'
check_contains "$u6_out" 'Usage:' 'u6b: the refusal shows the synopsis'

# Group 5: a DEFAULT output path would make every run a writer of real state. Assert the
# ABSENCE of a write, not merely the refusal — a default would satisfy the exit code above.
#
# An absence-assertion passes for free when the subject is missing entirely (measured: this row
# and c2 were the only two green in the RED run, both vacuously). So pair it with a positive
# precondition that OUR script ran and refused for the stated reason: a missing engine exits 127,
# not 2, and prints no such message.
mkdir -p "$tmp/empty"
u7_out="$(cd "$tmp/empty" && "$engine" -- true 2>&1)"
u7_rc=$?
check_eq "$u7_rc" 2 'u7a: the no---out refusal came from the script itself (rc 2, not 127)'
check_contains "$u7_out" 'no default output path' 'u7b: the refusal names the reason'
check_eq "$(find "$tmp/empty" -type f | wc -l | tr -d ' ')" 0 \
  'u7c: a refused launch writes NOTHING — there is no default destination'

run --status
check_eq "$RC" 2 'u8: --status with no path is a usage error'

run --status "$tmp/does-not-exist.log"
check_eq "$RC" 2 'u9: --status on a missing artifact is a usage error, not a verdict'

# ---------------------------------------------------------------- launch + recorded status

a="$(art ok)"
run --out "$a" -- /bin/sh -c 'echo hello-stdout; echo hello-stderr >&2; exit 0'
check_eq "$RC" 0 'l1: a successful launch exits 0'
check_contains "$OUT" "$a" 'l2: the launcher prints the artifact path'
check_eq "$([[ -f "$a" ]] && echo yes || echo no)" yes \
  'l3: the artifact exists by the time the launcher returns (no race)'

if wait_done "$a"; then
  body="$(cat "$a")"
  # Assert against the OUTPUT SECTION, not the whole artifact. The header records the command
  # verbatim, so the literal text `echo hello-stderr >&2` is present there whether or not stderr
  # was ever captured — a whole-file grep passes off the header. Measured: the mutation removing
  # `2>&1` SURVIVED until these two rows were narrowed to the section below the marker.
  emitted="$(sed -n '/^----- output -----$/,$p' "$a")"
  check_contains "$emitted" 'hello-stdout' 'l4: stdout is captured into the artifact'
  check_contains "$emitted" 'hello-stderr' 'l5: stderr is captured into the artifact'
  check_contains "$body" 'RUN_LONG_EXIT_STATUS=0' 'l6: a zero exit status is recorded IN the artifact'
  check_contains "$body" 'RUN_LONG_BEGIN' 'l7: the artifact carries a machine-readable header'
  check_contains "$body" 'RUN_LONG_COMMAND:' 'l8: the artifact records the command that produced it'
  check_eq "$(tail -1 "$a")" 'RUN_LONG_EXIT_STATUS=0' 'l9: the status trailer is the LAST line'
else
  fail_line 'l4-l9: the run never completed (no trailer appeared)'
fi

b="$(art fail7)"
run --out "$b" -- /bin/sh -c 'exit 7'
check_eq "$RC" 0 'l10: the LAUNCHER exits 0 even when the work will fail — its status is not a verdict'
if wait_done "$b"; then
  check_contains "$(cat "$b")" 'RUN_LONG_EXIT_STATUS=7' 'l11: a non-zero exit status is recorded faithfully'
else
  fail_line 'l11: the run never completed (no trailer appeared)'
fi

# Argument fidelity: the wrapper must not re-split or re-glob what it was handed.
c="$(art argv)"
run --out "$c" -- /bin/sh -c 'printf "[%s]" "$@"' _ 'two words' '*' 'a"b'
if wait_done "$c"; then
  check_contains "$(cat "$c")" '[two words][*][a"b]' 'l12: arguments pass through intact (spaces, glob, quote)'
  # l12 asserts what the COMMAND printed; this asserts what the HEADER recorded. They are
  # different subjects, and the header's fidelity had no row at all until a surviving mutation
  # (%q -> a flat "$*" join) pointed at the gap.
  check_contains "$(grep '^RUN_LONG_COMMAND:' "$c")" 'two\ words' \
    'l13: the recorded command line preserves argument boundaries (%q, not a flat join)'
else
  fail_line 'l12-l13: the run never completed (no trailer appeared)'
fi

# The launcher must not return until the header is on disk. Without that wait, a --status racing
# the job finds a file with no BEGIN line and no trailer, and reports a false DIED for a run that
# is doing fine. Deliberately NO wait_done here — the race is the subject.
r="$(art race)"
"$engine" --out "$r" -- /bin/sh -c 'sleep 20' > /dev/null 2>&1
run --status "$r"
check_eq "$RC" 3 'l14: --status immediately after launch reports RUNNING, never a false DIED'

# ---------------------------------------------------------------- clobber refusal

d="$(art clobber)"
printf 'PRECIOUS EVIDENCE\n' > "$d"
run --out "$d" -- true
check_eq "$RC" 2 'c1: an existing artifact is not clobbered'
check_eq "$(cat "$d")" 'PRECIOUS EVIDENCE' 'c2: the refused launch left the old artifact byte-identical'

run --out "$d" --force -- /bin/sh -c 'echo replaced'
check_eq "$RC" 0 'c3: --force permits the overwrite'
if wait_done "$d"; then
  check_absent "$(cat "$d")" 'PRECIOUS EVIDENCE' 'c4: --force actually replaced the old content'
else
  fail_line 'c4: the run never completed (no trailer appeared)'
fi

# ---------------------------------------------------------------- --status verdicts

e="$(art st_ok)"
"$engine" --out "$e" -- true >/dev/null 2>&1
wait_done "$e" || true
run --status "$e"
check_eq "$RC" 0 's1: --status exits 0 for a completed, successful run'
check_contains "$OUT" 'RESULT: DONE rc=0' 's2: --status prints a DONE verdict line'

f="$(art st_fail)"
"$engine" --out "$f" -- /bin/sh -c 'exit 3' >/dev/null 2>&1
wait_done "$f" || true
run --status "$f"
check_eq "$RC" 1 's3: --status exits 1 for a completed, failed run'
check_contains "$OUT" 'RESULT: DONE rc=3' 's4: --status reports the work'"'"'s real rc'

g="$(art st_running)"
"$engine" --out "$g" -- /bin/sh -c 'sleep 30' >/dev/null 2>&1
run --status "$g"
check_eq "$RC" 3 's5: --status exits 3 while the run is still in flight'
check_contains "$OUT" 'RESULT: RUNNING' 's6: --status prints a RUNNING verdict line'

# The measured failure this tool exists for: a killed run leaves a PREFIX of good-looking
# output, no verdict and no trailer. "No FAIL in the file" must NOT read as a pass.
gpid="$(sed -n 's/^RUN_LONG_BEGIN pid=\([0-9]*\).*/\1/p' "$g" | head -1)"
if [[ -n "$gpid" ]]; then
  kill -9 "$gpid" 2>/dev/null
  # reap, then let the OS clear the pid
  i=0
  while kill -0 "$gpid" 2>/dev/null && [[ $i -lt 100 ]]; do sleep 0.05; i=$((i + 1)); done
  run --status "$g"
  check_eq "$RC" 4 's7: a KILLED run is reported as DIED, not as a pass'
  check_contains "$OUT" 'RESULT: DIED' 's8: --status prints a DIED verdict line'
  check_absent "$(cat "$g")" 'RUN_LONG_EXIT_STATUS=' 's9: the killed run recorded no status — its ABSENCE is the signal'
else
  fail_line 's7-s9: could not recover the pid from the artifact header'
fi

# ---------------------------------------------------------------- label

h="$(art labelled)"
run --out "$h" --label 'audit --tests' -- true
if wait_done "$h"; then
  check_contains "$(cat "$h")" 'audit --tests' 'p1: --label is recorded in the artifact header'
else
  fail_line 'p1: the run never completed (no trailer appeared)'
fi

printf '\n%d passed, %d failed\n' "$pass" "$fail"
[[ "$fail" -eq 0 ]]

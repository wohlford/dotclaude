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

# run_bounded SECS ARGS… — like run(), but a hang becomes a FAIL instead of a stuck suite.
# The whole point of --wait is that it must exit on EVERY terminal state; the failure mode it
# guards against is an infinite loop, and an unbounded assertion would hang right along with it
# and report nothing at all. SIGALRM surfaces as rc 142, which fails any check_eq on a verdict.
run_bounded() {
  local secs="$1"
  shift
  OUT="$(perl -e 'my $s = shift; alarm $s; exec @ARGV or exit 127' "$secs" "$engine" "$@" 2>&1)"
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

# ---------------------------------------------------------------- --wait

run --wait
check_eq "$RC" 2 'w1: --wait with no path is a usage error'

run --wait "$tmp/does-not-exist.log"
check_eq "$RC" 2 'w2: --wait on a missing artifact is a usage error, not a verdict'

run --help
check_contains "$OUT" '--wait' 'w3: --help documents the wait mode'

# An already-terminal artifact: --wait must return exactly what --status would, at once.
wa="$(art wait_ok)"
"$engine" --out "$wa" -- true >/dev/null 2>&1
wait_done "$wa" || true
run_bounded 20 --wait "$wa" --interval 1
check_eq "$RC" 0 'w4: --wait exits 0 on an already-finished successful run'
check_contains "$OUT" 'RESULT: DONE rc=0' 'w4b: --wait prints the same DONE verdict line as --status'

wb="$(art wait_fail)"
"$engine" --out "$wb" -- /bin/sh -c 'exit 3' >/dev/null 2>&1
wait_done "$wb" || true
run_bounded 20 --wait "$wb" --interval 1
check_eq "$RC" 1 'w5: --wait exits 1 on an already-finished failed run'

# The flag's reason to exist: block through RUNNING, come back with the terminal verdict.
wblock="$(art wait_block)"
"$engine" --out "$wblock" -- /bin/sh -c 'sleep 2; exit 0' >/dev/null 2>&1
run --status "$wblock"
check_eq "$RC" 3 'w6a: precondition — the job really is still RUNNING when --wait is called'
run_bounded 30 --wait "$wblock" --interval 1
check_eq "$RC" 0 'w6b: --wait blocks through RUNNING and returns the terminal verdict'
# `pid=` is what separates a real verdict line from the usage synopsis, which contains the bare
# strings `RESULT: RUNNING` and `RESULT: DIED` verbatim. Matching without it makes this row fail
# on any usage dump and makes w7b below pass on one — measured, both, in the RED run.
check_absent "$OUT" 'RESULT: RUNNING pid=' 'w6c: --wait never reports the non-terminal state it waited through'

# THE row this flag exists for. The natural hand-rolled loop (`until [ $? -eq 0 ]`) hangs forever
# on a job that died — "silence is not success" reproduced at the call site, inside the very tool
# built to prevent it. Bounded, so a regression reads as a FAIL rather than as a hung suite.
wd="$(art wait_died)"
"$engine" --out "$wd" -- /bin/sh -c 'sleep 30' >/dev/null 2>&1
wdpid="$(sed -n 's/^RUN_LONG_BEGIN pid=\([0-9]*\).*/\1/p' "$wd" | head -1)"
if [[ -n "$wdpid" ]]; then
  kill -9 "$wdpid" 2>/dev/null
  i=0
  while kill -0 "$wdpid" 2>/dev/null && [[ $i -lt 100 ]]; do
    sleep 0.05
    i=$((i + 1))
  done
  run_bounded 20 --wait "$wd" --interval 1
  check_eq "$RC" 4 'w7: --wait exits 4 on a DIED job rather than waiting for a status that never comes'
  check_contains "$OUT" 'RESULT: DIED pid=' 'w7b: --wait prints the DIED verdict'
else
  fail_line 'w7-w7b: could not recover the pid from the artifact header'
fi

run --wait "$wa" --interval 0
check_eq "$RC" 2 'w8: a non-positive --interval is a usage error'

run --wait "$wa" --interval abc
check_eq "$RC" 2 'w9: a non-numeric --interval is a usage error'

run --status "$wa" --interval 5
check_eq "$RC" 2 'w10: --interval outside wait mode is a usage error, not a silent no-op'

# ---------------------------------------------------------------- subject stamp

# A dedicated git sandbox. The stamp must cover UNCOMMITTED work, because uncommitted work is
# exactly what a long check is normally grading — a HEAD-only stamp would call every one of the
# edits this feature exists to catch "unchanged".
repo="$tmp/subject-repo"
mkdir -p "$repo"
git -C "$repo" init -q >/dev/null 2>&1
printf 'one\n' > "$repo/file.txt"
git -C "$repo" add file.txt >/dev/null 2>&1
git -C "$repo" -c user.email=t@example.invalid -c user.name=T -c commit.gpgsign=false \
  -c tag.gpgsign=false commit -qm init >/dev/null 2>&1

if git -C "$repo" rev-parse HEAD >/dev/null 2>&1; then
  sa="$(art subj)"
  (cd "$repo" && "$engine" --out "$sa" -- true) >/dev/null 2>&1
  wait_done "$sa" || true

  check_contains "$(cat "$sa")" 'RUN_LONG_SUBJECT=' \
    't1: a launch inside a git repo stamps the subject into the header'

  run --status "$sa"
  check_contains "$OUT" 'SUBJECT: unchanged' 't2: an unmoved tree is reported as unchanged'
  check_eq "$RC" 0 't2b: the unchanged report leaves the verdict exit code alone'

  # Move the tree WITHOUT committing. This is the row that fails if the stamp is `rev-parse HEAD`.
  printf 'two\n' >> "$repo/file.txt"
  run --status "$sa"
  check_contains "$OUT" 'SUBJECT: MOVED' 't3: an uncommitted edit since launch is reported as MOVED'
  check_eq "$RC" 0 't4: MOVED is a WARNING — it must not change the verdict exit code'
  check_contains "$OUT" 'RESULT: DONE rc=0' 't4b: the verdict line itself still prints alongside the warning'

  git -C "$repo" checkout -- file.txt >/dev/null 2>&1
  run --status "$sa"
  check_contains "$OUT" 'SUBJECT: unchanged' 't5: reverting the edit restores the match'

  printf 'x\n' > "$repo/untracked.txt"
  run --status "$sa"
  check_contains "$OUT" 'SUBJECT: MOVED' 't6: a new untracked file counts as a tree move'
else
  fail_line 't1-t6: could not build the git sandbox'
fi

# Outside a repo the stamp is best-effort: no usage error, no failure — and the artifact says so
# POSITIVELY. Silence here would be indistinguishable from "checked, and unchanged".
sb="$(art subj_none)"
(cd "$tmp" && "$engine" --out "$sb" -- true) >/dev/null 2>&1
wait_done "$sb" || true
run --status "$sb"
check_eq "$RC" 0 't7: a launch outside a git repo still succeeds'
check_contains "$OUT" 'SUBJECT: not recorded' \
  't8: the artifact states positively that no subject was captured'

# ---------------------------------------------------------------- --expect: the flag

# An empty ERE matches every line — measured, `grep -qE -e ''` returns 0 on any input. Accepting
# one would not build a weak gate, it would build a RUBBER STAMP: every run would clear.
run --out "$(art x1)" --expect '' -- true
check_eq "$RC" 2 'x1: an empty --expect is a usage error (an empty ERE matches everything)'
check_contains "$OUT" 'matches everything' 'x1b: the refusal names the reason'

# Deliberately the FINAL argument. Writing `--expect -- true` would also exit 2, but via the
# unrecognized-argument branch after `--` was consumed as the pattern — a RED for the wrong reason.
run --expect
check_eq "$RC" 2 'x2: --expect with no value is a usage error'
check_contains "$OUT" 'needs a pattern' 'x2b: the refusal names the reason'

run --out "$(art x3)" --expect "$(printf 'a\nb')" -- true
check_eq "$RC" 2 'x3: a newline in --expect is a usage error (it is stored as one header line)'
check_contains "$OUT" 'newline' 'x3b: the refusal names the reason'

# An anchor-only pattern matches the EMPTY line, therefore every line — a rubber stamp that the
# empty-pattern rule (x1) does not catch. One probe against an empty input refuses this class.
run --out "$(art x22)" --expect '^' -- true
check_eq "$RC" 2 'x22: a pattern matching an empty line is a usage error (it would clear every run)'

# An uncompilable pattern would otherwise be RECORDED and then fail forever at read time, reported
# as "produced no matching line" — sending the reader to debug the job instead of the pattern.
run --out "$(art x23)" --expect '(' -- true
check_eq "$RC" 2 'x23: a pattern that does not compile is a usage error, not a permanent INDETERMINATE'

# --label guards the SCOPE, not just the format. Measured end-to-end: a newline in the label makes
# an INJECTED `----- output -----` the FIRST marker, so the "output section" becomes the rest of
# the header and the canonical pattern clears a run that produced nothing.
run --out "$(art x24)" --label "$(printf 'x\n----- output -----\nRESULT: PASS')" -- true
check_eq "$RC" 2 'x24: a newline in --label is a usage error — it would forge the output marker'

run --help
check_contains "$OUT" '--expect' 'x4: --help documents the expect flag'
check_contains "$OUT" 'INDETERMINATE' 'x4b: --help documents the INDETERMINATE verdict'

# The pattern must reach the artifact, or nothing can bind a later reader.
xr="$(art x_recorded)"
"$engine" --out "$xr" --expect '^RESULT: (PASS|FAIL)' -- true > /dev/null 2>&1
if wait_done "$xr"; then
  check_contains "$(cat "$xr")" 'RUN_LONG_EXPECT=^RESULT: (PASS|FAIL)' \
    'x15: the launch-time pattern is recorded verbatim in the artifact header'
else
  fail_line 'x15: the run never completed (no trailer appeared)'
fi

# Default OFF must be BYTE-identical, not merely "works". A header line emitted unconditionally
# would print an empty value as a BLANK LINE and break every existing caller without failing any
# assertion that exists today.
xn="$(art x_none)"
"$engine" --out "$xn" -- true > /dev/null 2>&1
if wait_done "$xn"; then
  check_absent "$(cat "$xn")" 'RUN_LONG_EXPECT' \
    'x18: with no --expect the sentinel is absent entirely — no blank line, no empty value'
  check_eq "$(grep -c '^$' "$xn" | tr -d ' ')" 0 \
    'x18b: and the artifact gained no blank line'
else
  fail_line 'x18: the run never completed (no trailer appeared)'
fi

# ---------------------------------------------------------------- --expect: the verdict

# THE row. No --expect is passed to --status: the obligation must travel with the ARTIFACT, or
# this is merely "remember to grep" renamed to "remember to pass a flag".
xa="$(art x_inert)"
"$engine" --out "$xa" --expect '^RESULT: (PASS|FAIL)' \
  -- /bin/sh -c 'echo did-nothing-useful; exit 0' > /dev/null 2>&1
wait_done "$xa" || true
run --status "$xa"
check_eq "$RC" 5 'x5: a finished run that produced no verdict line is INDETERMINATE, exit 5'
check_contains "$OUT" 'RESULT: INDETERMINATE' 'x5b: --status prints an INDETERMINATE verdict line'
check_contains "$OUT" '^RESULT: (PASS|FAIL)' 'x5c: the report names the pattern that went unmatched'

xb="$(art x_ok)"
"$engine" --out "$xb" --expect '^RESULT: (PASS|FAIL)' \
  -- /bin/sh -c 'echo "RESULT: PASS rc=0"; exit 0' > /dev/null 2>&1
wait_done "$xb" || true
run --status "$xb"
check_eq "$RC" 0 'x6: a run whose output carries the expected line is DONE, exit 0'
check_contains "$OUT" 'RESULT: DONE rc=0' 'x6b: the DONE verdict line still prints'
check_contains "$OUT" 'EXPECT:' 'x6c: with a pattern in play the report is never silent about it'

# THE CAUTION. A FAILING run that DID reach a verdict must stay DONE. If INDETERMINATE fired on
# legitimate failures the signal would be trained away, and an ignored alarm is worse than none.
xc="$(art x_fail)"
"$engine" --out "$xc" --expect '^RESULT: (PASS|FAIL)' \
  -- /bin/sh -c 'echo "RESULT: FAIL 2 checks"; exit 1' > /dev/null 2>&1
wait_done "$xc" || true
run --status "$xc"
check_eq "$RC" 1 'x7: a FAILING run that reached a verdict is DONE rc=1, never INDETERMINATE'
check_absent "$OUT" 'INDETERMINATE' 'x7b: --expect asks "reached a verdict", not "was it good"'

# THE FALSE-CLEAR row, and the one that dies if anyone widens the match to the whole artifact.
# --label is recorded RAW via %s, so the caller's own text sits in the header.
xd="$(art x_header)"
"$engine" --out "$xd" --label 'sweep RESULT: PASS run' --expect 'RESULT: PASS' \
  -- /bin/sh -c 'echo nothing-useful; exit 0' > /dev/null 2>&1
wait_done "$xd" || true
# Non-vacuity precondition: without this the row passes for free if the label never landed.
check_contains "$(sed -n '1,/^----- output -----$/p' "$xd")" 'RESULT: PASS' \
  'x8a: precondition — the pattern text really IS present in the header'
run --status "$xd"
check_eq "$RC" 5 'x8: text appearing ONLY in the header never satisfies --expect'

# DIED is already strictly more informative than INDETERMINATE and must win.
xe="$(art x_died)"
"$engine" --out "$xe" --expect '^RESULT: ' -- /bin/sh -c 'sleep 30' > /dev/null 2>&1
xepid="$(sed -n 's/^RUN_LONG_BEGIN pid=\([0-9]*\).*/\1/p' "$xe" | head -1)"
if [[ -n "$xepid" ]]; then
  kill -9 "$xepid" 2>/dev/null
  i=0
  while kill -0 "$xepid" 2>/dev/null && [[ $i -lt 100 ]]; do sleep 0.05; i=$((i + 1)); done
  run --status "$xe"
  check_eq "$RC" 4 'x9: a KILLED run with a pattern is still DIED (4), not INDETERMINATE'
else
  fail_line 'x9: could not recover the pid from the artifact header'
fi

# RUNNING is not terminal — the line may simply not have printed yet.
xf="$(art x_running)"
"$engine" --out "$xf" --expect '^RESULT: ' -- /bin/sh -c 'sleep 30' > /dev/null 2>&1
run --status "$xf"
check_eq "$RC" 3 'x10: an in-flight run with a pattern is RUNNING (3), not INDETERMINATE'

# Read-time pattern on an artifact that recorded none — the ad-hoc case, correctly scoped.
xg="$(art x_readtime)"
"$engine" --out "$xg" -- /bin/sh -c 'echo nothing-useful; exit 0' > /dev/null 2>&1
wait_done "$xg" || true
run --status "$xg" --expect '^RESULT: '
check_eq "$RC" 5 'x11: a read-time pattern applies to an artifact that recorded none'
run --status "$xg"
check_eq "$RC" 0 'x11b: and without it the same artifact is plain DONE — the flag is opt-in'

# The AND rule, both directions. A read-time pattern may only ADD.
xh="$(art x_and)"
"$engine" --out "$xh" --expect '^RESULT: ' \
  -- /bin/sh -c 'echo "RESULT: PASS"; exit 0' > /dev/null 2>&1
wait_done "$xh" || true
run --status "$xh"
check_eq "$RC" 0 'x12a: precondition — the recorded pattern alone is satisfied'
run --status "$xh" --expect 'NEVER-APPEARS-ANYWHERE'
check_eq "$RC" 5 'x12: a read-time pattern ADDS a requirement the recorded one did not impose'

xi="$(art x_launder)"
"$engine" --out "$xi" --expect 'NEVER-APPEARS-ANYWHERE' \
  -- /bin/sh -c 'echo "RESULT: PASS"; exit 0' > /dev/null 2>&1
wait_done "$xi" || true
run --status "$xi" --expect '^RESULT: '
check_eq "$RC" 5 'x13: a read-time pattern CANNOT clear a recorded one — no laundering'

# Terminal by construction: --wait loops only on RUNNING, so INDETERMINATE must return at once.
# Bounded, so a regression reads as a FAIL rather than as a hung suite.
run_bounded 20 --wait "$xa" --interval 1
check_eq "$RC" 5 'x14: --wait treats INDETERMINATE as TERMINAL and returns 5 rather than hanging'

# Default OFF: no pattern anywhere means no EXPECT line at all.
run --status "$xn"
check_eq "$RC" 0 'x16: with no pattern anywhere the verdict is unchanged'
check_absent "$OUT" 'EXPECT:' 'x16b: ...and the report says nothing about expect — byte-identical'

# Fail safe: no output marker means an EMPTY output section, so nothing can match. Note the
# artifact below DOES contain a matching line — above where the marker would be.
xm="$(art x_nomarker)"
printf 'RUN_LONG_BEGIN pid=1 label=x\n%s^RESULT: \nRESULT: PASS\nRUN_LONG_EXIT_STATUS=0\n' \
  'RUN_LONG_EXPECT=' > "$xm"
run --status "$xm"
check_eq "$RC" 5 'x17: a missing output marker yields an empty section, so nothing clears — fail safe'

# The recorded pattern sits in the HEADER, so it can never satisfy itself.
xj="$(art x_selfmatch)"
"$engine" --out "$xj" --expect 'RUN_LONG_EXPECT' -- /bin/sh -c 'echo quiet; exit 0' > /dev/null 2>&1
wait_done "$xj" || true
run --status "$xj"
check_eq "$RC" 5 'x19: the recorded pattern line cannot match itself — the header is out of scope'

# The scoped region must exclude the HARNESS's OWN lines. Both rows below cleared an inert run
# until the marker line and the status trailer were stripped from the section — the same false
# clear as x8, one layer down, and `EXIT_STATUS=0` is a natural way to try to assert success.
xk="$(art x_trailer)"
"$engine" --out "$xk" --expect 'EXIT_STATUS=0' -- /bin/sh -c 'exit 0' > /dev/null 2>&1
wait_done "$xk" || true
check_contains "$(cat "$xk")" 'RUN_LONG_EXIT_STATUS=0' \
  'x20a: precondition — the trailer really IS present in the artifact'
run --status "$xk"
check_eq "$RC" 5 'x20: the status TRAILER is out of scope — a run that printed nothing stays INDETERMINATE'

xl="$(art x_marker)"
"$engine" --out "$xl" --expect 'output' -- /bin/sh -c 'exit 0' > /dev/null 2>&1
wait_done "$xl" || true
run --status "$xl"
check_eq "$RC" 5 'x21: the MARKER line is out of scope — "output" cannot clear an empty run'

# A job that prints the sentinel must not MANUFACTURE a requirement. No flag is passed anywhere
# here, so the only correct verdict is a plain DONE that says nothing about expect at all.
xo="$(art x_adopt)"
"$engine" --out "$xo" -- /bin/sh -c 'echo "RUN_LONG_EXPECT=zzz-never-appears"; exit 0' > /dev/null 2>&1
wait_done "$xo" || true
run --status "$xo"
check_eq "$RC" 0 'x25: a pattern printed by the JOB is not adopted as a requirement'
check_absent "$OUT" 'EXPECT:' 'x25b: ...and no expect assurance is fabricated'

# The empty-section guard's ONLY separating input — and the row exists because without it the
# guard's mutation row SURVIVES.
#
# Every pattern that passes launch validation already returns "no match" against an empty section,
# because the parse probe and the guard are THE SAME PREDICATE applied at two different times. So
# the guard reads as inert against any artifact this tool can produce. It is not inert: it stops
# an artifact whose recorded pattern never went through validation — hand-crafted, or written by a
# build predating the probe. That is the contract this row pins.
#
# Do NOT "fix" a surviving guard-mutant by deleting the guard: that opens the legacy case for real.
xp="$(art x_legacy)"
printf 'RUN_LONG_BEGIN pid=1 label=x\n%s^\n----- output -----\nRUN_LONG_EXIT_STATUS=0\n' \
  'RUN_LONG_EXPECT=' > "$xp"
run --status "$xp"
check_eq "$RC" 5 'x26: a legacy/hand-crafted "^" pattern cannot clear an EMPTY output section'

printf '\n%d passed, %d failed\n' "$pass" "$fail"
[[ "$fail" -eq 0 ]]

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

# The w11/w12 race shims below intercept the engine's own trailer-presence query and recover its
# pid from the header, so they need the engine's BEGIN/STATUS record prefixes. Derive them from
# the engine's own `readonly` lines rather than hand-copying the literals — the same way this file
# already derives $engine from $here — so a rename in run-long.sh cannot silently degrade a shim
# into one that never matches anything, and therefore never actually enters the race it exists to
# probe.
begin_prefix="$(sed -n "s/^readonly BEGIN_PREFIX='\\(.*\\)'\$/\\1/p" "$engine")"
status_prefix="$(sed -n "s/^readonly STATUS_PREFIX='\\(.*\\)'\$/\\1/p" "$engine")"

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

# THE defect this row exists to pin: classify() checks for the RUN_LONG_EXIT_STATUS= trailer
# BEFORE it checks whether the job is still alive. A job that finishes in the gap between those
# two observations is seen as "no trailer, not alive" and reported DIED — a healthy run
# misreported with the strongest "do not trust this" verdict the tool has. Load alone cannot be
# trusted to land in that gap reliably, so it is reproduced DETERMINISTICALLY: a grep SHIM placed
# ahead of the engine on PATH answers the trailer query faithfully (real grep, real exit status),
# but only AFTER releasing a job that was blocked on a trigger file and waiting for its wrapper
# process to actually exit — so the liveness check classify() makes next is guaranteed to observe
# a job that is already gone. Every other grep call (there is at most one more, from a second
# classify() after the --wait poll) delegates to the real grep unchanged; a state file makes the
# special case fire exactly once so it cannot also swallow that second call.
#
# --interval 1 / run_bounded 30, not the suite's usual 20: under the FIXED engine this row's green
# path costs a full poll interval (classify reads a stale alive=true, reports RUNNING, and only the
# NEXT poll sees the trailer and reports DONE) — at the default 15s interval against a 20s bound
# there would be only 5s of margin, enough to make correct code flake red.
toctou_shim_dir="$tmp/toctou-shim"
mkdir -p "$toctou_shim_dir"
toctou_state="$tmp/toctou-fired"
toctou_overrun="$tmp/toctou-wait-overrun"
# Positive evidence that the shim actually recovered a pid and entered the bounded wait — without
# this, w11c's "did not overrun" check passes for free whenever the pid extraction comes back
# empty, since the whole wait (and the only place that could write toctou_overrun) sits inside
# `if [[ -n "$wpid" ]]`. A row that cannot tell "never waited" from "waited and finished" is not
# testing what it claims.
toctou_waited="$tmp/toctou-wait-executed"
# `type -P`, not `command -v`: the latter returns a BARE NAME ("grep") when grep resolves to a
# shell FUNCTION rather than a binary — measured true of this machine's interactive shell — and
# the shim's `exec '$REAL' "$@"` would then re-resolve through the shim-prefixed PATH and recurse
# without bound. `type -P` always returns an absolute path to a real executable.
toctou_real_grep="$(type -P grep)"
wr="$(art wait_toctou)"
wr_trigger="$tmp/toctou-job-trigger"

cat > "$toctou_shim_dir/grep" <<SHIM
#!/usr/bin/env bash
# Delegates every call to the real grep unchanged, except the ONE trailer-presence query
# classify() makes against the artifact under test — answered faithfully, but only after
# releasing the blocked job and waiting for its wrapper to actually exit.
if [[ "\$1" == "-q" && "\$2" == '^${status_prefix}' && "\$3" == '$wr' && ! -e '$toctou_state' ]]; then
  : > '$toctou_state'
  '$toctou_real_grep' "\$@"
  rc=\$?
  : > '$wr_trigger'
  wpid="\$(sed -n 's/^${begin_prefix} pid=\([0-9]*\).*/\1/p' '$wr' | head -1)"
  if [[ -n "\$wpid" ]]; then
    : > '$toctou_waited'
    # Bounded like every other polling loop in this file (wait_done's tries, the engine's
    # tries -lt 250): run_bounded's alarm does NOT cover this — an orphaned child holds the
    # command-substitution pipe open, so SIGALRM never frees it. An overrun leaves a trace file
    # instead of hanging or silently proceeding.
    wtries=0
    while kill -0 "\$wpid" 2>/dev/null && [[ \$wtries -lt 500 ]]; do
      sleep 0.01
      wtries=\$((wtries + 1))
    done
    kill -0 "\$wpid" 2>/dev/null && : > '$toctou_overrun'
  fi
  exit "\$rc"
fi
exec '$toctou_real_grep' "\$@"
SHIM
chmod +x "$toctou_shim_dir/grep"

# Bounded, like every other polling loop in this file: the shim releases this job only once, and
# only via the trigger file below, but a suite bug elsewhere (or a shim that never fires) must not
# leave a 100Hz spinner running forever after the EXIT trap deletes $tmp out from under it —
# including on a Ctrl-C between launch and shim fire. 3000 iterations at 0.01s is 30s, well past
# run_bounded's 30s alarm on the row that uses this job, so the alarm — not this bound — fires
# first on any real hang; this bound exists only to give the spinner itself a way to give up.
#
# `"$1"`, not `"$@"`: this repo already uses `# shellcheck disable=SC2016` for exactly this
# shape (run-long.sh:487, test_push_guard.sh:113) rather than working around the warning with a
# different positional parameter. It is safe only because this `sh -c` body is invoked with
# EXACTLY one positional argument by construction (`_ "$wr_trigger"` below supplies $0 and a
# single $1).
# shellcheck disable=SC2016
"$engine" --out "$wr" -- /bin/sh -c \
  'i=0; while [ ! -f "$1" ] && [ "$i" -lt 3000 ]; do sleep 0.01; i=$((i + 1)); done; exit 0' \
  _ "$wr_trigger" \
  > /dev/null 2>&1
toctou_old_path="$PATH"
PATH="$toctou_shim_dir:$PATH"
run_bounded 30 --wait "$wr" --interval 1
PATH="$toctou_old_path"
check_eq "$RC" 0 'w11: a job that completes DURING classification reports DONE, never a false DIED'
check_contains "$OUT" 'RESULT: DONE rc=0' 'w11b: the raced wait returns the DONE verdict, not DIED'
# Precondition, not decoration: w11c below is satisfied for free whenever the shim's pid
# extraction comes back empty, since the wait it bounds — and the only place that can write
# toctou_overrun — both sit inside `if [[ -n "$wpid" ]]`. Without this row, a broken extraction
# (e.g. a bad match on the derived BEGIN prefix) reads as a clean "did not overrun".
check_eq "$([[ -e "$toctou_waited" ]] && echo yes || echo no)" yes \
  'w11c0: the shim actually recovered the wrapper pid and entered the bounded wait'
check_eq "$([[ -e "$toctou_overrun" ]] && echo yes || echo no)" no \
  'w11c: the shim'"'"'s bounded wrapper-exit wait did not overrun'

# ---------------------------------------------------------------- w12: the self-correction pin
#
# classify()'s comment (scripts/run-long.sh) documents an ACCEPTED regression: `alive` is sampled
# before the trailer is consumed, so a wrapper killed in that gap reads RUNNING once instead of
# DIED. That is safe ONLY because the very next read self-corrects to DIED — RUNNING is
# non-terminal. This row pins that self-correction, the load-bearing half of the safety argument;
# without it, nothing here would catch RUNNING becoming sticky or cached.
#
# The window is opened the same way w11 opens its mirror image: a PATH-shimmed grep sits between
# the alive sample and the trailer check classify() makes right after it — the ONLY external
# command in that gap, so intercepting it is how the gap is entered deterministically. Where w11
# releases a blocked job and waits for it to finish NORMALLY (so the sample was stale-but-now-true),
# this shim kills the wrapper directly and waits, BOUNDED, for it to actually be gone — so the
# absence this run reports is a REAL kill, never a fabricated state, and the second read's DIED is
# a fresh, honest sample rather than a leftover from the first.
#
# This row deliberately PINS an accepted defect, not a desired behaviour: the RUNNING-then-DIED
# sequence it asserts on is the cost classify()'s comment accepts, not the ideal outcome. If that
# window is ever closed properly (e.g. a re-sample gated to move only RUNNING→DIED), w12/w12b go
# red — read that as the pin becoming obsolete, not as a regression, and DELETE this row rather
# than "fixing" it back to green.
w12_shim_dir="$tmp/w12-shim"
mkdir -p "$w12_shim_dir"
w12_state="$tmp/w12-fired"
w12_overrun="$tmp/w12-wait-overrun"
# See toctou_waited above for why this exists: without it, w12f is satisfied for free whenever the
# pid extraction fails, since the wait it bounds lives inside the same `if [[ -n "$wpid" ]]`.
w12_waited="$tmp/w12-wait-executed"
w12_real_grep="$(type -P grep)"
w12_art="$(art wait_selfcorrect)"

"$engine" --out "$w12_art" -- /bin/sh -c 'sleep 30' > /dev/null 2>&1

cat > "$w12_shim_dir/grep" <<SHIM
#!/usr/bin/env bash
# Delegates every call to the real grep unchanged, except the ONE trailer-presence query
# classify() makes against the artifact under test — answered faithfully (the trailer really is
# absent, since the job below never reaches the point of writing one), and only AFTER answering
# does it kill the wrapper and wait, bounded, for it to actually exit — so the NEXT classify() call
# samples liveness fresh rather than racing this one's kill.
if [[ "\$1" == "-q" && "\$2" == '^${status_prefix}' && "\$3" == '$w12_art' && ! -e '$w12_state' ]]; then
  : > '$w12_state'
  '$w12_real_grep' "\$@"
  rc=\$?
  wpid="\$(sed -n 's/^${begin_prefix} pid=\([0-9]*\).*/\1/p' '$w12_art' | head -1)"
  if [[ -n "\$wpid" ]]; then
    : > '$w12_waited'
    kill -9 "\$wpid" 2>/dev/null
    wtries=0
    while kill -0 "\$wpid" 2>/dev/null && [[ \$wtries -lt 500 ]]; do
      sleep 0.01
      wtries=\$((wtries + 1))
    done
    kill -0 "\$wpid" 2>/dev/null && : > '$w12_overrun'
  fi
  exit "\$rc"
fi
exec '$w12_real_grep' "\$@"
SHIM
chmod +x "$w12_shim_dir/grep"

w12_old_path="$PATH"
PATH="$w12_shim_dir:$PATH"
run_bounded 20 --status "$w12_art"
check_eq "$RC" 3 \
  'w12: a wrapper killed between the alive sample and the trailer read gives the SAFE stale verdict — RUNNING, not DIED'
check_contains "$OUT" 'RESULT: RUNNING' 'w12b: ...and the RUNNING verdict line prints'

run_bounded 20 --status "$w12_art"
PATH="$w12_old_path"
check_eq "$RC" 4 'w12c: the VERY NEXT read self-corrects to DIED — the pin this row exists for'
check_contains "$OUT" 'RESULT: DIED' 'w12d: ...and the DIED verdict line prints'

check_absent "$(cat "$w12_art")" 'RUN_LONG_EXIT_STATUS=' \
  'w12e: the trailer is genuinely absent throughout — a real kill, not a fabricated state'
check_eq "$([[ -e "$w12_waited" ]] && echo yes || echo no)" yes \
  'w12f0: the shim actually recovered the wrapper pid and entered the bounded kill-wait'
check_eq "$([[ -e "$w12_overrun" ]] && echo yes || echo no)" no \
  'w12f: the shim'"'"'s bounded wrapper-exit wait did not overrun'

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

# --- rS1: --stamp exposes the subject fingerprint instead of callers copying it ---
# subject_stamp() already hashes HEAD + `git diff HEAD` + `status --porcelain`, and its comment
# records why a HEAD-only stamp is worse than nothing. A caller that needs tree-stability must reuse
# it: a hand-made copy is the measured drift hazard, and there is no shell library here to hold it.
# Run the paired reads inside a git SANDBOX, not this working copy: stamping the live repo makes
# quiescence part of the fixture, so any peer write between the two reads flakes the row -- a flaky
# row introduced by the flake detector's own plan.
mkdir -p "$tmp/stamprepo" && git init -q "$tmp/stamprepo"
printf 'x\n' > "$tmp/stamprepo/f"
# subject_stamp hashes `git status --porcelain`, and an UNTRACKED file's porcelain line is just
# `?? f` regardless of content -- so a content edit to an uncommitted new file is invisible to it.
# Commit first, so the later edit is a TRACKED-file diff that `git diff HEAD` actually shows.
# gpgsign MUST be forced off. Measured: global `commit.gpgsign` is true here and a fresh `git init`
# inherits it, so this commit really does reach for the hardware key -- it survived only because the
# card's PIN was cached from earlier commits in the same session, and would HANG on a cold cache.
# `fixture-signing-check` cannot catch this one: it is FILE-scoped, and another fixture higher in
# this file already sets the flags, so the file passes while this repo signs.
git -C "$tmp/stamprepo" -c user.email=t@t -c user.name=t add -A
git -C "$tmp/stamprepo" -c user.email=t@t -c user.name=t \
  -c commit.gpgsign=false -c tag.gpgsign=false commit -q -m init

st_a="$(cd "$tmp/stamprepo" && "$engine" --stamp 2>/dev/null)"; rc_a=$?
st_b="$(cd "$tmp/stamprepo" && "$engine" --stamp 2>/dev/null)"
check_eq "$rc_a" 0 'stamp: exits 0 on a git tree'
check_eq "$st_a" "$st_b" 'stamp: two reads of an unchanged tree agree'
if [[ -n "$st_a" ]]; then pass_line 'stamp: a git tree yields a non-empty fingerprint'
else fail_line 'stamp: a git tree yielded an EMPTY fingerprint'; fi

printf 'y\n' >> "$tmp/stamprepo/f"
st_c="$(cd "$tmp/stamprepo" && "$engine" --stamp 2>/dev/null)"
if [[ "$st_c" != "$st_a" ]]; then pass_line 'stamp: an edited tree yields a DIFFERENT fingerprint'
else fail_line 'stamp: an edited tree yielded the SAME fingerprint -- drift is undetectable'; fi

st_out="$(cd "$tmp" && "$engine" --stamp 2>/dev/null)"; rc_out=$?
check_eq "$rc_out" 0 'stamp: exits 0 outside a git tree too'
# This row is the affirmative outside-a-repo case: outside_git_repo() matches git's own "not a
# git repository (or any of the parent directories)" message for $tmp (genuinely outside any
# repo) and --stamp exits 0-with-nothing on that match alone -- it never even reaches
# subject_stamp() here. rS6-rS11 below (MAJOR2) are the fixtures that pin the DISTINCTION this
# row cannot: a bare repo, a bogus GIT_DIR, and an unrecognized git failure all also produce
# empty output, and each must exit NONZERO rather than being folded into this same branch.
check_eq "$st_out" '' 'stamp: entirely outside any git repo, prints nothing'

# --- rS2-rS4: --stamp is its own mode; mixing it with another mode's arguments used to be a
# silent partial launch (print the stamp, exit 0, create NO artifact) rather than a usage error --
# MEASURED against the unfixed script: `--stamp --out X -- echo hi` printed a stamp, exited 0, and
# left no file at X. A caller who typo'd would believe the job had launched. Assert the DISTINCTIVE
# behaviour, not something a healthy run also produces: rc 2 (this script's usage-error code, same
# family as u1/u3/w10 above) AND the absence of the artifact the old code silently declined to
# write -- a mutant that merely restored a nonzero rc without also skipping the write would still
# fail rS2b.
rs2_art="$(art rS2)"
rm -f "$rs2_art"
(cd "$tmp/stamprepo" && "$engine" --stamp --out "$rs2_art" -- echo hi > /dev/null 2>&1)
check_eq "$?" 2 'rS2: --stamp with --out is a usage error, not a silent discard'
if [[ ! -e "$rs2_art" ]]; then pass_line 'rS2b: ...and no artifact is created'
else fail_line 'rS2b: an artifact WAS created despite the usage error'; fi

(cd "$tmp/stamprepo" && "$engine" --status "$rs2_art" --stamp > /dev/null 2>&1)
check_eq "$?" 2 'rS3: --status combined with --stamp is a usage error (order-independent: mode flags are tracked by presence, not by which one parses last)'

(cd "$tmp/stamprepo" && "$engine" --stamp -- echo hi > /dev/null 2>&1)
check_eq "$?" 2 'rS4: --stamp with a bare command (no --out) is a usage error too'

# The legitimate, argument-free shape all six real callers use must still work exactly as before.
rs5_rc="$(cd "$tmp/stamprepo" && "$engine" --stamp > /dev/null 2>&1; echo $?)"
check_eq "$rs5_rc" 0 'rS5: bare --stamp (the real call shape) still exits 0'

# --- rS6/rS7: a bare repo is NOT "outside a repo" -- MAJOR2 ---
# `git rev-parse --show-toplevel` fails in a bare repo exactly as it does outside any repo, but
# with a DIFFERENT message ("this operation must be run in a work tree", no "not a git
# repository" at all) -- so outside_git_repo() correctly reads this as "some other failure", not
# as the affirmative outside-a-repo case, and --stamp now dies nonzero instead of silently
# printing nothing at rc=0. Pre-MAJOR2 this row asserted rc_bare=0: a bare repo and genuinely
# being outside a repo were indistinguishable to any caller, exactly the defect class MAJOR2
# closes. `git -C <bare-dir> rev-parse --git-dir` SUCCEEDS in a bare repo (it IS its own
# git-dir), which is why subject_stamp()'s OWN internal guard was never sufficient on its own --
# the outer `outside_git_repo()` check is what has to catch this shape.
mkdir -p "$tmp/barerepo.git" && git init -q --bare "$tmp/barerepo.git"
st_bare="$(cd "$tmp/barerepo.git" && "$engine" --stamp 2>/dev/null)"; rc_bare=$?
check_eq "$st_bare" '' 'rS6: a bare repo (no work tree) prints nothing rather than a bogus fingerprint'
if [[ "$rc_bare" -ne 0 ]]; then pass_line 'rS7: ...but exits NONZERO -- a bare repo is not "outside a repo"'
else fail_line 'rS7: a bare repo exited 0 -- indistinguishable from genuinely outside a repo (MAJOR2 unfixed)'
fi

# --- rS8/rS9: MAJOR2's measured defect -- a bogus GIT_DIR inside a REAL repo ---
# `GIT_DIR` is exported by every git hook, and this repo runs checks from hooks, so this is
# reachable in practice, not a contrived edge case. Pre-fix, `git rev-parse --show-toplevel` under
# a GIT_DIR pointing nowhere real failed with a DIFFERENT stderr message than genuinely being
# outside a repo (no "(or any of the parent directories)" parenthetical) but the same emptiness
# and rc=0 either way, so --stamp reported empty/rc=0 from INSIDE a real, tracked-and-dirty repo --
# byte-identical to the honest outside-a-repo case. `env` scopes GIT_DIR to this one probe only.
gd_repo="$tmp/gitdirrepo"
mkdir -p "$gd_repo"
git init -q "$gd_repo"
git -C "$gd_repo" config commit.gpgsign false
git -C "$gd_repo" config tag.gpgsign false
git -C "$gd_repo" -c user.email=t@t -c user.name=t commit -q --allow-empty -m init
gd_out="$(cd "$gd_repo" && env GIT_DIR=/nonexistent/not-real.git "$engine" --stamp 2>/dev/null)"
gd_rc=$?
check_eq "$gd_out" '' 'rS8: a bogus GIT_DIR inside a real repo prints nothing (same shape as outside-a-repo)'
if [[ "$gd_rc" -ne 0 ]]; then pass_line 'rS9: ...but exits NONZERO, distinguishing it from genuinely outside a repo'
else fail_line 'rS9: a bogus GIT_DIR exited 0 -- indistinguishable from outside-a-repo (MAJOR2 unfixed)'
fi

# --- rS10/rS11: an UNRECOGNIZED git failure must not be folded into outside-a-repo either ---
# outside_git_repo() matches ONE specific message; every other git failure -- corrupt repo, a
# transient error, anything this suite did not anticipate -- must land in the "some other
# failure" bucket too, not be waved through as a false outside-a-repo. A fake `git` ahead on PATH
# stands in for "git ran and failed for a reason nobody has enumerated".
mkdir -p "$tmp/fakebin"
cat > "$tmp/fakebin/git" <<'FAKEGIT'
#!/bin/bash
printf 'fatal: something else entirely\n' >&2
exit 99
FAKEGIT
chmod +x "$tmp/fakebin/git"
fg_out="$(PATH="$tmp/fakebin:$PATH" "$engine" --stamp 2>/dev/null)"
fg_rc=$?
check_eq "$fg_out" '' 'rS10: an unrecognized git failure prints nothing (not a bogus fingerprint)'
if [[ "$fg_rc" -ne 0 ]]; then pass_line 'rS11: ...but exits nonzero rather than reading as outside-a-repo'
else fail_line 'rS11: an unrecognized git failure exited 0 -- silently folded into outside-a-repo'
fi

# --- stamp-de: outside_git_repo()'s message match must survive a translated git locale ---
# outside_git_repo() matches ONE literal English string against git's stderr. gettext (which git
# uses for its own diagnostics) translates that string whenever LC_ALL/LANG selects a locale with
# a German catalogue installed -- measured: MacPorts git on this machine prints "Schwerwiegend:
# Kein Git-Repository (oder irgendeines der Elternverzeichnisse): .git" under LC_ALL=de_DE.UTF-8.
# The English match then silently fails, so a caller genuinely outside a repo under that locale
# is folded into the "some other failure" branch (dies nonzero) instead of the affirmative
# outside-a-repo case (exit 0, silent) -- the exact false-negative rS6-rS11 above exist to close
# for OTHER causes of a failed match.
#
# Gate on the OBSERVED stderr, never on `locale -a`: Apple's /usr/bin/git carries no translation
# catalogues at all and prints the English text regardless of locale, so on a machine where this
# git behaves the same way the defect is unreachable and a "pass" here would prove nothing --
# skip instead of faking green.
de_probe_err="$(LC_ALL=de_DE.UTF-8 git -C "$tmp" rev-parse --show-toplevel 2>&1 1>/dev/null)"
if [[ "$de_probe_err" == *'not a git repository (or any of the parent directories)'* ]]; then
  printf 'SKIP  stamp-de: this git has no German catalogue (prints English even under LC_ALL=de_DE.UTF-8) -- defect unreachable here\n'
else
  de_out="$(cd "$tmp" && LC_ALL=de_DE.UTF-8 "$engine" --stamp 2>/dev/null)"; de_rc=$?
  check_eq "$de_rc" 0 'stamp-de-a: exits 0 outside a git tree under a translated (German) locale'
  check_eq "$de_out" '' 'stamp-de-b: ...with empty stdout too'
fi

# --- rS12-rS14: repo CONFIGURATION must never decide what "unchanged" means ---
# `git status --porcelain` and `git diff HEAD` both honour settings that hide changes:
# `status.showUntrackedFiles=no` drops every `??` line, and `submodule.<name>.ignore` (repo config
# or a committed .gitmodules) drops a dirty submodule entirely -- and even with it shown, the
# default submodule diff format collapses every further edit inside an already-dirty submodule to
# the constant `Subproject commit <sha>-dirty`. Measured before the fix: each of the three shapes
# below left the stamp byte-identical across a real change (flake-sweep reported
# `RESULT: PASS ... subject=stable` over a subject leaking a new untracked file every run).
# Every fixture file the rows do NOT deliberately change lives outside the stamped repo; signing
# is forced off (a fresh `git init` inherits the global commit.gpgsign=true here and would hang on
# a cold hardware-key PIN); a local file-URL submodule add needs protocol.file.allow=always.
stamp_in() { (cd "$1" && "$engine" --stamp 2> /dev/null); } # DIR
cfg_commit() { # DIR MSG
  git -C "$1" -c user.email=t@t -c user.name=t -c commit.gpgsign=false -c tag.gpgsign=false \
    commit -q -m "$2"
}

su_repo="$tmp/showuntracked"
mkdir -p "$su_repo" && git init -q "$su_repo"
git -C "$su_repo" config commit.gpgsign false
git -C "$su_repo" config tag.gpgsign false
git -C "$su_repo" config status.showUntrackedFiles no
printf 'x\n' > "$su_repo/f"
git -C "$su_repo" add f && cfg_commit "$su_repo" init
su_a="$(stamp_in "$su_repo")"
: > "$su_repo/leak.1.tmp"
su_b="$(stamp_in "$su_repo")"
if [[ -n "$su_a" && "$su_a" != "$su_b" ]]; then
  pass_line 'rS12: a new untracked file MOVES the stamp even under status.showUntrackedFiles=no'
else
  fail_line "rS12: status.showUntrackedFiles=no hid a new untracked file from the stamp (a=[$su_a] b=[$su_b])"
fi

# One submodule source repo, shared by rS13 and rS14 (each clones its own copy as `vendor`).
sm_src="$tmp/sm_src"
mkdir -p "$sm_src" && git init -q "$sm_src"
git -C "$sm_src" config commit.gpgsign false
git -C "$sm_src" config tag.gpgsign false
printf 'v1\n' > "$sm_src/lib.txt"
git -C "$sm_src" add lib.txt && cfg_commit "$sm_src" init

mk_super() { # DIR -- a superproject with $sm_src committed as submodule `vendor`
  mkdir -p "$1" && git init -q "$1"
  git -C "$1" config commit.gpgsign false
  git -C "$1" config tag.gpgsign false
  git -C "$1" -c protocol.file.allow=always submodule --quiet add "$sm_src" vendor > /dev/null 2>&1
  cfg_commit "$1" 'add vendor'
}

ign_repo="$tmp/ignoredirty"
mk_super "$ign_repo"
git -C "$ign_repo" config --file .gitmodules submodule.vendor.ignore dirty
git -C "$ign_repo" add .gitmodules && cfg_commit "$ign_repo" 'ignore=dirty'
ign_a="$(stamp_in "$ign_repo")"
printf 'edited\n' > "$ign_repo/vendor/lib.txt"
ign_b="$(stamp_in "$ign_repo")"
if [[ -n "$ign_a" && "$ign_a" != "$ign_b" ]]; then
  pass_line 'rS13: a tracked edit inside a submodule.<name>.ignore=dirty submodule MOVES the stamp'
else
  fail_line "rS13: submodule.vendor.ignore=dirty hid a tracked edit inside the submodule (a=[$ign_a] b=[$ign_b])"
fi

dd_repo="$tmp/dirtydirty"
mk_super "$dd_repo"
printf 'first edit\n' > "$dd_repo/vendor/lib.txt" # already dirty at "launch"
dd_a="$(stamp_in "$dd_repo")"
printf 'second edit\n' > "$dd_repo/vendor/lib.txt"
dd_b="$(stamp_in "$dd_repo")"
if [[ -n "$dd_a" && "$dd_a" != "$dd_b" ]]; then
  pass_line 'rS14: a further edit inside an ALREADY-dirty submodule MOVES the stamp'
else
  fail_line "rS14: an already-dirty submodule hid a further tracked edit (a=[$dd_a] b=[$dd_b])"
fi

# rS15: git's DEFAULT untracked mode (`normal`) collapses an untracked directory to one `?? dir/`
# line, so a NEW file appearing inside an already-untracked directory left the stamp identical --
# no config involved at all, so this repo sets none (measured on the pre-fix stamp, and on the
# `--untracked-files=normal` form of the fix: identical both times).
ud_repo="$tmp/untrackeddir"
mkdir -p "$ud_repo" && git init -q "$ud_repo"
git -C "$ud_repo" config commit.gpgsign false
git -C "$ud_repo" config tag.gpgsign false
git -C "$ud_repo" -c user.email=t@t -c user.name=t commit -q --allow-empty -m init
mkdir -p "$ud_repo/build"
: > "$ud_repo/build/one.o"
ud_a="$(stamp_in "$ud_repo")"
: > "$ud_repo/build/two.o"
ud_b="$(stamp_in "$ud_repo")"
if [[ -n "$ud_a" && "$ud_a" != "$ud_b" ]]; then
  pass_line 'rS15: a new file inside an already-untracked directory MOVES the stamp'
else
  fail_line "rS15: an untracked directory collapsed to one porcelain line hid a new file (a=[$ud_a] b=[$ud_b])"
fi

# --- rS16-rS23: fix wave 2 -- what the top-level flags cannot reach INSIDE a submodule ---
# The top-level `--ignore-submodules=none`/`--untracked-files=all` flags govern only the top-level
# commands. Whether a submodule counts as dirty, and what its diff shows, is decided by a child git
# run INSIDE that submodule under its OWN config and its own .gitmodules. Measured on 991fb57, each
# shape below left the stamp byte-identical across a real change: a nested submodule's committed
# ignore=dirty (depth 2), a further untracked file inside a submodule already holding untracked
# content, a submodule whose own config sets status.showUntrackedFiles=no, and a submodule whose
# own diff.external prints a constant. Every fixture file the rows do not deliberately change
# lives outside the stamped repos; signing is off in every repo (the `w2g` wrapper and the
# explicit per-repo config both force it).
w2g() {
  git -c user.email=t@t -c user.name=t -c commit.gpgsign=false -c tag.gpgsign=false \
    -c protocol.file.allow=always "$@"
}
w2_repo() { # DIR FILE CONTENT -- a repo with one committed file, signing off
  mkdir -p "$1" && git init -q "$1"
  git -C "$1" config commit.gpgsign false
  git -C "$1" config tag.gpgsign false
  printf '%s\n' "$3" > "$1/$2"
  w2g -C "$1" add "$2" && w2g -C "$1" commit -q -m init
}
w2_sup() { # DIR -- a superproject with $sm_src (rS13's source repo) as submodule `vendor`
  w2_repo "$1" s.txt s
  w2g -C "$1" submodule --quiet add "$sm_src" vendor > /dev/null 2>&1
  w2g -C "$1" commit -q -m 'add vendor'
}
stamp_rc() { (cd "$1" && "$engine" --stamp > /dev/null 2>&1); echo "$?"; } # DIR

# rS16: depth 2. mid's own committed .gitmodules marks inner ignore=dirty; top stamps.
w2_repo "$tmp/w2_inner" deep.txt deep
w2_repo "$tmp/w2_mid" mid.txt mid
w2g -C "$tmp/w2_mid" submodule --quiet add "$tmp/w2_inner" inner > /dev/null 2>&1
w2g -C "$tmp/w2_mid" config --file .gitmodules submodule.inner.ignore dirty
w2g -C "$tmp/w2_mid" add .gitmodules && w2g -C "$tmp/w2_mid" commit -q -m 'inner, ignore=dirty'
w2_repo "$tmp/w2_top" top.txt top
w2g -C "$tmp/w2_top" submodule --quiet add "$tmp/w2_mid" mid > /dev/null 2>&1
w2g -C "$tmp/w2_top" commit -q -m 'add mid'
w2g -C "$tmp/w2_top" submodule --quiet update --init --recursive > /dev/null 2>&1
n_a="$(stamp_in "$tmp/w2_top")"
printf 'e1\n' > "$tmp/w2_top/mid/inner/deep.txt"
n_b="$(stamp_in "$tmp/w2_top")"
printf 'e2\n' > "$tmp/w2_top/mid/inner/deep.txt"
n_c="$(stamp_in "$tmp/w2_top")"
if [[ -n "$n_a" && "$n_a" != "$n_b" && "$n_b" != "$n_c" ]]; then
  pass_line 'rS16: tracked edits two submodules deep MOVE the stamp despite a nested committed ignore=dirty'
else
  fail_line "rS16: a nested submodule's committed ignore=dirty hid tracked edits (a=[$n_a] b=[$n_b] c=[$n_c])"
fi

# rS17: a further untracked file inside a submodule that already holds untracked content.
w2_sup "$tmp/w2_pre"
: > "$tmp/w2_pre/vendor/u1"
p_a="$(stamp_in "$tmp/w2_pre")"
: > "$tmp/w2_pre/vendor/u2"
p_b="$(stamp_in "$tmp/w2_pre")"
if [[ -n "$p_a" && "$p_a" != "$p_b" ]]; then
  pass_line 'rS17: a new untracked file inside an already-untracked-dirty submodule MOVES the stamp'
else
  fail_line "rS17: a submodule already holding untracked content hid a new untracked file (a=[$p_a] b=[$p_b])"
fi

# rS18: the submodule's OWN config sets status.showUntrackedFiles=no.
w2_sup "$tmp/w2_suno"
git -C "$tmp/w2_suno/vendor" config status.showUntrackedFiles no
u_a="$(stamp_in "$tmp/w2_suno")"
: > "$tmp/w2_suno/vendor/u1"
u_b="$(stamp_in "$tmp/w2_suno")"
if [[ -n "$u_a" && "$u_a" != "$u_b" ]]; then
  pass_line "rS18: a new untracked file MOVES the stamp even when the submodule's own config sets showUntrackedFiles=no"
else
  fail_line "rS18: a submodule-level status.showUntrackedFiles=no hid a new untracked file (a=[$u_a] b=[$u_b])"
fi

# rS19: the submodule's OWN diff.external prints a constant; the tracked file is re-edited.
printf '#!/bin/sh\necho constant\n' > "$tmp/w2_ext.sh"
chmod +x "$tmp/w2_ext.sh"
w2_sup "$tmp/w2_ext"
git -C "$tmp/w2_ext/vendor" config diff.external "$tmp/w2_ext.sh"
printf 'one\n' > "$tmp/w2_ext/vendor/lib.txt"
x_a="$(stamp_in "$tmp/w2_ext")"
printf 'two\n' > "$tmp/w2_ext/vendor/lib.txt"
x_b="$(stamp_in "$tmp/w2_ext")"
if [[ -n "$x_a" && "$x_a" != "$x_b" ]]; then
  pass_line "rS19: a re-edit inside a submodule MOVES the stamp even when the submodule's own diff.external prints a constant"
else
  fail_line "rS19: a submodule-level diff.external hid a re-edit (a=[$x_a] b=[$x_b])"
fi

# rS20: a git command that FAILS mid-stamp must not yield a (constant) stamp. An ignore=all
# submodule whose .git/modules/vendor is gone made 991fb57's `git diff --submodule=diff` die
# fatally at `vendor`; the old `{ ...; } 2>/dev/null | hasher` hashed the truncated output with the rc
# discarded, so edits to zz.txt (sorting after vendor) read as unchanged (measured on 991fb57:
# identical stamps, rc 0 both times).
w2_sup "$tmp/w2_trunc"
printf 'z\n' > "$tmp/w2_trunc/zz.txt"
w2g -C "$tmp/w2_trunc" add zz.txt
w2g -C "$tmp/w2_trunc" config --file .gitmodules submodule.vendor.ignore all
w2g -C "$tmp/w2_trunc" add .gitmodules && w2g -C "$tmp/w2_trunc" commit -q -m 'zz, ignore=all'
rm -rf "$tmp/w2_trunc/.git/modules/vendor"
printf '1\n' > "$tmp/w2_trunc/zz.txt"
t_out="$(stamp_in "$tmp/w2_trunc")"
t_rc="$(stamp_rc "$tmp/w2_trunc")"
if [[ "$t_rc" -ne 0 ]]; then
  pass_line 'rS20: a git command failing inside the stamp makes --stamp exit NONZERO, never a truncated stamp'
else
  fail_line "rS20: a failing git command still produced a stamp at rc 0 (stamp=[$t_out])"
fi
check_eq "$t_out" '' 'rS20b: ...and prints no stamp at all'

# rS21: step-3 caller -- a LAUNCH whose stamp cannot be computed must not record a hash that a
# later --status reads as "unchanged since launch".
rs21_art="$(art rS21)"
(cd "$tmp/w2_trunc" && "$engine" --out "$rs21_art" -- true > /dev/null 2>&1)
wait_done "$rs21_art" || fail_line 'rS21: precondition -- the launched job never finished'
run --status "$rs21_art"
check_absent "$OUT" 'SUBJECT: unchanged' 'rS21: a launch whose stamp failed is never reported "unchanged since launch"'
check_contains "$OUT" 'could not be computed at launch' 'rS21b: ...--status says the stamp could not be computed at launch'

# rS22: step-3 caller -- a healthy launch whose tree BREAKS before --status must say the stamp
# could not be computed now, not "unchanged" and not a bare MOVED over a truncated re-read.
w2_sup "$tmp/w2_break"
w2g -C "$tmp/w2_break" config --file .gitmodules submodule.vendor.ignore all
w2g -C "$tmp/w2_break" add .gitmodules && w2g -C "$tmp/w2_break" commit -q -m 'ignore=all'
rs22_art="$(art rS22)"
(cd "$tmp/w2_break" && "$engine" --out "$rs22_art" -- true > /dev/null 2>&1)
wait_done "$rs22_art" || fail_line 'rS22: precondition -- the launched job never finished'
rm -rf "$tmp/w2_break/.git/modules/vendor"
run --status "$rs22_art"
check_absent "$OUT" 'SUBJECT: unchanged' 'rS22: a tree whose stamp now fails is never reported "unchanged since launch"'
check_contains "$OUT" 'could not be computed now' 'rS22b: ...--status says the stamp could not be computed now'

# rS23: controls -- the submodule walk must not make two IDENTICAL calls disagree (a stamp that
# moves on its own would read every sweep as MOVED): no submodules, an uninitialized submodule,
# and a clean initialized one. Each also has to produce a real stamp at rc 0.
w2_repo "$tmp/w2_k1" x.txt x
w2_repo "$tmp/w2_k2" x.txt x
w2g -C "$tmp/w2_k2" submodule --quiet add "$sm_src" vendor > /dev/null 2>&1
w2g -C "$tmp/w2_k2" commit -q -m 'add vendor'
git -C "$tmp/w2_k2" submodule --quiet deinit -f vendor > /dev/null 2>&1
w2_sup "$tmp/w2_k3"
for k in k1:'no submodules' k2:'an uninitialized submodule' k3:'a clean initialized submodule'; do
  kd="$tmp/w2_${k%%:*}"
  k_a="$(stamp_in "$kd")"
  k_b="$(stamp_in "$kd")"
  k_rc="$(stamp_rc "$kd")"
  if [[ -n "$k_a" && "$k_a" == "$k_b" && "$k_rc" -eq 0 ]]; then
    pass_line "rS23-${k%%:*}: two identical stamps agree in a repo with ${k#*:}"
  else
    fail_line "rS23-${k%%:*}: identical stamps disagree or failed with ${k#*:} (a=[$k_a] b=[$k_b] rc=$k_rc)"
  fi
done

# rS25: a SECOND commit inside an ignore=all submodule. The submodule walk diffs each submodule
# against its OWN HEAD, so a commit made inside it leaves that walk clean -- only the TOP-LEVEL
# diff's `--ignore-submodules=none` shows the moved gitlink (`Subproject commit <old>..<new>`);
# status alone reports a constant ` M vendor` after the first commit.
w2_sup "$tmp/w2_all"
w2g -C "$tmp/w2_all" config --file .gitmodules submodule.vendor.ignore all
w2g -C "$tmp/w2_all" add .gitmodules && w2g -C "$tmp/w2_all" commit -q -m 'ignore=all'
printf 'c1\n' > "$tmp/w2_all/vendor/lib.txt"
w2g -C "$tmp/w2_all/vendor" commit -q -a -m c1
ga_a="$(stamp_in "$tmp/w2_all")"
printf 'c2\n' > "$tmp/w2_all/vendor/lib.txt"
w2g -C "$tmp/w2_all/vendor" commit -q -a -m c2
ga_b="$(stamp_in "$tmp/w2_all")"
if [[ -n "$ga_a" && "$ga_a" != "$ga_b" ]]; then
  pass_line 'rS25: a further commit inside an ignore=all submodule MOVES the stamp'
else
  fail_line "rS25: an ignore=all submodule hid a commit made inside it (a=[$ga_a] b=[$ga_b])"
fi

# rS26: the same shape one level DOWN -- a second commit inside `inner`, which mid's own committed
# .gitmodules marks ignore=all. Top's diff sees only a constant `mid -dirty`; inner's own diff
# against its own HEAD is clean; only MID's diff with `--ignore-submodules=none` shows inner's
# gitlink moving.
w2_repo "$tmp/w2_in2" deep.txt deep
w2_repo "$tmp/w2_mid2" mid.txt mid
w2g -C "$tmp/w2_mid2" submodule --quiet add "$tmp/w2_in2" inner > /dev/null 2>&1
w2g -C "$tmp/w2_mid2" config --file .gitmodules submodule.inner.ignore all
w2g -C "$tmp/w2_mid2" add .gitmodules && w2g -C "$tmp/w2_mid2" commit -q -m 'inner, ignore=all'
w2_repo "$tmp/w2_top2" top.txt top
w2g -C "$tmp/w2_top2" submodule --quiet add "$tmp/w2_mid2" mid > /dev/null 2>&1
w2g -C "$tmp/w2_top2" commit -q -m 'add mid'
w2g -C "$tmp/w2_top2" submodule --quiet update --init --recursive > /dev/null 2>&1
printf 'c1\n' > "$tmp/w2_top2/mid/inner/deep.txt"
w2g -C "$tmp/w2_top2/mid/inner" commit -q -a -m c1
g2_a="$(stamp_in "$tmp/w2_top2")"
printf 'c2\n' > "$tmp/w2_top2/mid/inner/deep.txt"
w2g -C "$tmp/w2_top2/mid/inner" commit -q -a -m c2
g2_b="$(stamp_in "$tmp/w2_top2")"
if [[ -n "$g2_a" && "$g2_a" != "$g2_b" ]]; then
  pass_line 'rS26: a further commit inside a NESTED ignore=all submodule MOVES the stamp'
else
  fail_line "rS26: a nested ignore=all submodule hid a commit made inside it (a=[$g2_a] b=[$g2_b])"
fi

# rS27: an untracked file MOVING from one submodule to the next. Both already hold untracked
# content (top-level status stays ` M a`/` M b` throughout), and the names sort so the two
# submodules' concatenated status text is identical before and after -- only the per-submodule
# path header tells the two states apart.
w2_repo "$tmp/w2_two" s.txt s
w2g -C "$tmp/w2_two" submodule --quiet add "$sm_src" a > /dev/null 2>&1
w2g -C "$tmp/w2_two" submodule --quiet add "$sm_src" b > /dev/null 2>&1
w2g -C "$tmp/w2_two" commit -q -m 'add a, b'
: > "$tmp/w2_two/a/a0"
: > "$tmp/w2_two/b/zz"
: > "$tmp/w2_two/a/m"
mv_a="$(stamp_in "$tmp/w2_two")"
mv "$tmp/w2_two/a/m" "$tmp/w2_two/b/m"
mv_b="$(stamp_in "$tmp/w2_two")"
if [[ -n "$mv_a" && "$mv_a" != "$mv_b" ]]; then
  pass_line 'rS27: an untracked file moving from one submodule to another MOVES the stamp'
else
  fail_line "rS27: an untracked file moving between submodules left the stamp identical (a=[$mv_a] b=[$mv_b])"
fi

# rS28: a gitlink with NO .gitmodules entry (an embedded repo `git add`ed by accident) makes the
# submodule walk fail, so the stamp is REFUSED -- pinned as the safe direction: no stamp, never
# one that silently skips the embedded repo's content. (On 991fb57 this repo stamped at rc 0.)
w2_repo "$tmp/w2_orph" s.txt s
w2_repo "$tmp/w2_orph/emb" e.txt e
w2g -C "$tmp/w2_orph" add emb > /dev/null 2>&1
w2g -C "$tmp/w2_orph" commit -q -m 'embedded repo, no .gitmodules'
if [[ "$(stamp_rc "$tmp/w2_orph")" -ne 0 && -z "$(stamp_in "$tmp/w2_orph")" ]]; then
  pass_line 'rS28: a gitlink with no .gitmodules entry gets NO stamp (refused, nonzero) -- never a partial one'
else
  fail_line 'rS28: a gitlink with no .gitmodules entry still produced a stamp'
fi

# rS24: PRESERVE row -- an UNBORN branch (fresh `git init`, no commit) is stampable. The old
# stamp hashed a failing `git diff HEAD` with its rc discarded and still moved on a new untracked
# file (measured on 991fb57); checking every rc must not turn this ordinary state into a failure.
mkdir -p "$tmp/w2_unborn" && git init -q "$tmp/w2_unborn"
ub_a="$(stamp_in "$tmp/w2_unborn")"
ub_rc="$(stamp_rc "$tmp/w2_unborn")"
: > "$tmp/w2_unborn/first.txt"
ub_b="$(stamp_in "$tmp/w2_unborn")"
if [[ "$ub_rc" -eq 0 && -n "$ub_a" && "$ub_a" != "$ub_b" ]]; then
  pass_line 'rS24: an unborn branch yields a stamp at rc 0, and a new file MOVES it'
else
  fail_line "rS24: an unborn branch failed to stamp or did not move (rc=$ub_rc a=[$ub_a] b=[$ub_b])"
fi

printf '\n%d passed, %d failed\n' "$pass" "$fail"
[[ "$fail" -eq 0 ]]

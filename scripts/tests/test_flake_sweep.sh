#!/usr/bin/env bash
set -uo pipefail

# Script: test_flake_sweep.sh
# Purpose: Regression tests for scripts/flake-sweep.sh — the tool that runs an arbitrary command
#          N times and reports each label's (pass,fail,skip) rate plus the aggregated exit code,
#          so a check's own determinism can be measured instead of guessed. Covers both verdict
#          formats, the zero-denominator and cannot-execute cases, a real kill mid-sweep, a moved
#          subject, a non-git subject, and a rowless subject whose exit code alone varies.
# Usage:   ./scripts/tests/test_flake_sweep.sh

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
engine="$here/../flake-sweep.sh"

# `pwd -P` pins the PHYSICAL path: $TMPDIR is a symlink on macOS (/tmp -> /private/tmp), and a
# logical path that does not physically contain the fixture lets a path-resolving subject take a
# different branch and never reach what these rows exist to test.
tmp="$(cd "$(mktemp -d)" && pwd -P)"
trap 'rm -rf "$tmp"' EXIT

# T4 containment: flake-sweep.sh's own default `mktemp -d` artifact dirs (its own --artifact-dir,
# and its per-run capture file) are created under $TMPDIR, and neither this suite nor a killed
# sweep's trap reliably cleans them up -- measured 2026-09-24: 24 such directories per suite run,
# left behind in the OPERATOR's real TMPDIR rather than the throwaway one above. Every engine call
# below now lands its own default artifacts inside this fixture's own $tmp instead, which the EXIT
# trap already removes. Rows that set their own TMPDIR per call (R5h, R5k, R5l) keep doing so --
# they override this one for the duration of their own engine invocation.
export TMPDIR="$tmp/tmpdir"
mkdir -p "$TMPDIR"

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

# check_range GOT LOW HIGH LABEL -- LOW <= GOT < HIGH. Used for `runs=<c>/<n>` after a mid-sweep
# skip, where the exact completed count is not fixed (see the deterministic-skip fixture note in
# the plan) and pinning it would make the row brittle.
check_range() {
  if [[ "$1" =~ ^[0-9]+$ ]] && [[ "$1" -ge "$2" ]] && [[ "$1" -lt "$3" ]]; then
    pass_line "$4"
  else
    fail_line "$4 (want [$2,$3) got [$1])"
  fi
}

# check_exists PATH LABEL -- used by the --artifact-dir contract row (T4 item 4) to prove the
# sweep's four artifacts landed in the CALLER-SUPPLIED directory, not wherever `mktemp -d` chose.
check_exists() {
  if [[ -f "$1" ]]; then
    pass_line "$2"
  else
    fail_line "$2 (missing: $1)"
  fi
}

# The engine is invoked BARE-PATH ("$engine", never `bash "$engine"`) so the suite also exercises
# the exec bit and the shebang.
OUT=""
RC=0
run() {
  OUT="$("$engine" "$@" 2>&1)"
  RC=$?
}

# wait_for_line FILE PATTERN [tries] — poll for a line to appear rather than sleeping a fixed
# guess. Reused shape from test_run_long.sh's wait_done: under load, "the first run has not
# finished by T+3s" is a load-dependent race, and a flaky row inside a flake detector is the worst
# defect available.
wait_for_line() {
  local file="$1" pattern="$2" tries="${3:-200}" i=0
  while [[ $i -lt $tries ]]; do
    if [[ -f "$file" ]] && grep -qE "$pattern" "$file" 2> /dev/null; then return 0; fi
    sleep 0.05
    i=$((i + 1))
  done
  return 1
}

# A deterministic subject, and a deterministically VARYING one. The flaky subject is driven by a
# COUNTER FILE, not by randomness: the subject must vary while the test itself stays reproducible.
# A randomly-failing fixture would make this suite flaky, which is the one thing a flake detector
# must never be.
mk_stable() { cat > "$1" <<'SUBJ'
#!/usr/bin/env bash
printf 'PASS  row one\n'
printf 'PASS one-space row\n'
printf 'SKIP  row three\n'
SUBJ
chmod +x "$1"; }

mk_flaky() { cat > "$1" <<'SUBJ'
#!/usr/bin/env bash
c="$1"; n=$(( $(cat "$c" 2>/dev/null || echo 0) + 1 )); echo "$n" > "$c"
printf 'PASS  row one\n'
if [ "$n" = 3 ] || [ "$n" = 7 ]; then printf 'FAIL  row two\n'; else printf 'PASS  row two\n'; fi
SUBJ
chmod +x "$1"; }

# audit.sh's own shape: ONE-space PASS, and a FAIL with the ` — …` trailing diagnostic it emits.
mk_auditstyle() { cat > "$1" <<'SUBJ'
#!/usr/bin/env bash
printf 'PASS one-space-row\n'
printf 'FAIL audit-row — test suite failure(s)\n'
SUBJ
chmod +x "$1"; }

mk_slow() { cat > "$1" <<'SUBJ'
#!/usr/bin/env bash
sleep 0.3
printf 'PASS  ok\n'
SUBJ
chmod +x "$1"; }

mk_rc_alternating() { cat > "$1" <<'SUBJ'
#!/usr/bin/env bash
c="$1"; n=$(( $(cat "$c" 2>/dev/null || echo 0) + 1 )); echo "$n" > "$c"
[ $((n % 2)) -eq 0 ] && exit 1
exit 0
SUBJ
chmod +x "$1"; }

mk_mover() { cat > "$1" <<'SUBJ'
#!/usr/bin/env bash
printf x >> "$1/marker"
printf 'PASS  ok\n'
SUBJ
chmod +x "$1"; }

# T4 item 2: the mechanism item 6's title names ("a subject that mutates the tree it is stamped
# against") needs a TRACKED edit to reach reliably -- mk_mover above only ever proves an untracked
# path's FIRST appearance in porcelain moves the stamp (see the item-6 section below for the
# measured re-run that does not catch it a second time).
mk_mover_tracked() { cat > "$1" <<'SUBJ'
#!/usr/bin/env bash
printf x >> "$1/tracked.txt"
printf 'PASS  ok\n'
SUBJ
chmod +x "$1"; }

# ---------------------------------------------------- the deterministic-skip fixture (T1 rows)
#
# Fixture pieces (subject scripts, markers, counters) live OUTSIDE the git repo being stamped --
# only the cwd is the fixture repo. An untracked counter written INSIDE it would move the stamp
# on its own first appearance and false-flag `subject=MOVED` on a subject that edits nothing (see
# the plan's deterministic-skip fixture section). Every mk_skip_repo below is created with signing
# off, same reason as item 6.

mk_skip_repo() { # DIR
  local dir="$1"
  mkdir -p "$dir"
  git init -q "$dir"
  git -C "$dir" config commit.gpgsign false
  git -C "$dir" config tag.gpgsign false
  git -C "$dir" -c user.email=t@t -c user.name=t commit -q --allow-empty -m init
}

# run_skip SUBJ NN OUTFILE [ARGS...] -- invoke the engine against SUBJ (caller has already cd'd
# into the fixture repo) with an EVENT-DRIVEN restore: the reader chmod +x's SUBJ the instant a
# "could not execute" line appears, so the restore is caused BY the skip rather than scheduled
# after it. No sleep -- a flaky row inside a flake detector is the worst defect available.
# SWEEP_RC=<n> is captured inside the pipeline because the pipeline's own $? is the reader's exit
# status, not the sweep's.
run_skip() {
  local subj="$1" nn="$2" out="$3"
  shift 3
  { "$engine" -n "$nn" -- "$subj" "$@" 2>&1; printf 'SWEEP_RC=%d\n' "$?"; } \
    | while IFS= read -r line; do
        printf '%s\n' "$line"
        [[ "$line" == *"could not execute"* ]] && chmod +x "$subj" 2> /dev/null
      done > "$out"
}

sweep_rc() { grep -oE '^SWEEP_RC=[0-9]+' "$1" | tail -1 | cut -d= -f2; } # OUTFILE
run_count() { sed -n "s#.*runs=\([0-9]\{1,\}\)/${2}.*#\1#p" "$1" | head -1; } # OUTFILE N

# skA: rowless subject, disabled on its own first run (before printing anything -- there is
# nothing to print). MARKER is outside the fixture repo.
mk_skA() { cat > "$1" <<'SUBJ'
#!/usr/bin/env bash
marker="$1"
if [[ ! -e "$marker" ]]; then
  : > "$marker"
  chmod -x "$0"
fi
exit 0
SUBJ
chmod +x "$1"; }

# skB: mid-skip, but every executed run prints the SAME stable rows.
mk_skB() { cat > "$1" <<'SUBJ'
#!/usr/bin/env bash
marker="$1"
if [[ ! -e "$marker" ]]; then
  : > "$marker"
  chmod -x "$0"
fi
printf 'PASS  row one\n'
printf 'PASS  row two\n'
SUBJ
chmod +x "$1"; }

# skC: no self-disable logic -- the caller chmod -x's this BEFORE the sweep starts, so run 1
# itself is the skip. Every executed run (2..n) prints the same stable rows.
mk_skC() { cat > "$1" <<'SUBJ'
#!/usr/bin/env bash
printf 'PASS  row one\n'
printf 'PASS  row two\n'
SUBJ
chmod +x "$1"; }

# skD: mid-skip PLUS a genuine flip. `row one` never varies. `row two` flips on the SECOND actual
# invocation -- guaranteed to exist whenever completed>=2, which the `2 <= c` floor already
# requires, so the flip is present in every trial without pinning which run number it lands on.
mk_skD() { cat > "$1" <<'SUBJ'
#!/usr/bin/env bash
marker="$1"; counter="$2"
if [[ ! -e "$marker" ]]; then
  : > "$marker"
  chmod -x "$0"
fi
n=$(( $(cat "$counter" 2>/dev/null || echo 0) + 1 )); echo "$n" > "$counter"
printf 'PASS  row one\n'
if [ "$n" = 2 ]; then printf 'FAIL  row two\n'; else printf 'PASS  row two\n'; fi
SUBJ
chmod +x "$1"; }

# skE: no skip involved -- varies ONLY on its first invocation.
mk_skE() { cat > "$1" <<'SUBJ'
#!/usr/bin/env bash
c="$1"; n=$(( $(cat "$c" 2>/dev/null || echo 0) + 1 )); echo "$n" > "$c"
if [ "$n" = 1 ]; then printf 'FAIL  row one\n'; else printf 'PASS  row one\n'; fi
SUBJ
chmod +x "$1"; }

# skF: no skip involved -- varies ONLY on its final invocation. TOTAL (arg 2) must match the -n
# given to the engine.
mk_skF() { cat > "$1" <<'SUBJ'
#!/usr/bin/env bash
c="$1"; total="$2"
n=$(( $(cat "$c" 2>/dev/null || echo 0) + 1 )); echo "$n" > "$c"
if [ "$n" = "$total" ]; then printf 'FAIL  row one\n'; else printf 'PASS  row one\n'; fi
SUBJ
chmod +x "$1"; }

# ------------------------------------------------------------- T4 item 3: a shared quiet repo
#
# Items 1, 2, 8 and 9 below used to stamp the SUITE's own inherited cwd rather than a fixture
# repo -- measured 30 passed / 4 failed under a concurrent writer touching that cwd (rows 1a, 1b,
# 8a, 8b), against 34/34 quiesced at the time. Items 2 and 9 stayed green under the same
# contamination only because they already expect FAIL, which is exactly what made it invisible.
# `cd` each into this repo instead, the way item 6 already does. Reuses mk_skip_repo, defined
# above for the T1 rows, since it already creates a repo with signing forced off.
quiet_dir="$tmp/quiet"
mk_skip_repo "$quiet_dir"

# run_in DIR -- ARGS...: like run() above, but stamps DIR instead of the suite's inherited cwd.
# Plain `cd`/`cd back`, not a subshell -- run()'s OUT/RC assignments must land in THIS shell for
# the checks below to see them, and a `(cd dir && run ...)` subshell would drop both silently.
run_in() {
  local dir="$1"
  shift
  local prev
  prev="$(pwd)"
  cd "$dir" || {
    OUT="run_in: cd failed: $dir"
    RC=98
    return
  }
  OUT="$("$engine" "$@" 2>&1)"
  RC=$?
  cd "$prev" || true
}

# ---------------------------------------------------------------- item 1: deterministic subject

s1="$tmp/mk_stable.sh"
mk_stable "$s1"
run_in "$quiet_dir" -n 5 -- "$s1"
check_eq "$RC" 0 '1a: a deterministic subject exits 0'
check_contains "$OUT" 'RESULT: PASS' '1b: ...and the verdict line says PASS'
check_absent "$OUT" 'VARYING LABELS' \
  '1c: ...and no VARYING LABELS block is printed beside varying=0 (kills the unconditional-<exit status>-append mutant)'
check_contains "$OUT" 'varying=0' '1c: ...with zero varying labels'
check_contains "$OUT" 'runs=5/5' '1d: ...and a full denominator'
check_contains "$OUT" 'rows=3' \
  '1e: rows=3 proves BOTH verdict formats and SKIP were all parsed, not one silently dropped'
# T4 item 5: the literal `subject=stable` was never asserted -- renaming it to "BOGUS" survived,
# because 1a-1e (and item 8) assert only `RESULT: PASS`, which `subject=not-a-repo` also produces.
check_contains "$OUT" 'subject=stable' \
  '1f: ...and subject=stable literally, not just an overall PASS verdict'

# ---------------------------------------------------------------- item 2: flaky subject

s2="$tmp/mk_flaky.sh"
mk_flaky "$s2"
c2="$tmp/flaky_counter"
rm -f "$c2"
run_in "$quiet_dir" -n 10 -- "$s2" "$c2"
check_eq "$RC" 1 '2a: a flaky subject exits 1'
check_contains "$OUT" 'RESULT: FAIL' '2b: ...and the verdict line says FAIL'
check_contains "$OUT" 'varying=1' '2c: ...naming exactly one varying label'
check_contains "$OUT" 'row two' '2d: the report NAMES the row that actually varied'
check_absent "$OUT" 'row one' \
  '2e: ...and does NOT name the row that stayed stable — a bare FAIL would pass a weaker check'

# ---------------------------------------------------------------- item 3: zero denominator

run -n 0 -- true
check_eq "$RC" 2 '3a: -n 0 is a usage error'
check_contains "$OUT" 'RESULT: ERROR' '3b: ...verdict ERROR'
check_absent "$OUT" 'RESULT: PASS' '3c: ...and never PASS'

# F3: `-n 1` used to PASS by construction -- nothing can vary within a single run, so every
# per-label tuple and the lone exit code trivially "agree" with themselves. A usage error now.
run_in "$quiet_dir" -n 1 -- true
check_eq "$RC" 2 'n1a: -n 1 is a usage error (a single run cannot vary, so it cannot measure a flake)'
check_contains "$OUT" 'RESULT: ERROR' 'n1b: ...verdict ERROR, never a by-construction PASS'
check_contains "$OUT" 'single run cannot vary' 'n1c: ...and the diagnostic says WHY'

# ---------------------------------------------------------------- item 4: cannot execute

run_in "$quiet_dir" -n 3 -- "$tmp/no-such-subject-$$"
check_eq "$RC" 3 '4a: a subject that cannot execute is INCOMPLETE, exit 3'
check_contains "$OUT" 'RESULT: INCOMPLETE' '4b: ...verdict INCOMPLETE'
check_contains "$OUT" 'runs=0/3' '4c: ...zero completed of three planned'
check_absent "$OUT" 'RESULT: ERROR' \
  '4d: distinguished from item 3 — the sweep BEGAN, so this is not a usage fault'

# ---------------------------------------------------------------- item 5: killed mid-sweep

s5="$tmp/mk_slow.sh"
mk_slow "$s5"
kill_out="$tmp/kill_out.txt"
: > "$kill_out"
# Backgrounded, so it cannot go through run_in() (its OUT/RC assignment would race the kill below)
# -- cd into the quiet fixture in THIS shell, launch directly (never inside a `( cd ... && ... )`
# subshell: that wraps a COMPOUND command, so bash does not exec-replace it, and SIGTERM to the
# subshell's own pid would not propagate to the "$engine" grandchild it started), then cd back.
# The launched process's cwd is fixed at fork time, so cd'ing back here does not move it.
_item5_prev="$(pwd)"
cd "$quiet_dir" || fail_line 'item5: cd into quiet_dir failed (precondition)'
"$engine" -n 20 -- "$s5" > "$kill_out" 2>&1 &
kill_pid=$!
cd "$_item5_prev" || true
if wait_for_line "$kill_out" '^run 1/20' 200; then
  kill -TERM "$kill_pid" 2> /dev/null
  wait "$kill_pid" 2> /dev/null
  pass_line '5a: the first progress line appeared before the kill (precondition)'
else
  fail_line '5a: the first progress line NEVER appeared -- kill test cannot be attributed'
  kill -TERM "$kill_pid" 2> /dev/null
  wait "$kill_pid" 2> /dev/null
fi
last_run="$(grep -oE '^run [0-9]+/20' "$kill_out" | tail -1 | grep -oE '[0-9]+' | head -1)"
last_run="${last_run:-99}"
if [[ "$last_run" -lt 20 ]]; then
  pass_line '5b: the sweep was killed before completing all 20 runs'
else
  fail_line "5b: the sweep reached run $last_run/20 before the kill -- not mid-sweep"
fi
check_eq "$(grep -c '^RESULT:' "$kill_out")" 0 \
  '5c: a killed sweep prints ZERO verdict lines -- the absence IS the signal'

# ---------------------------------------------------------------- item 6: moved subject (tracked)
#
# T4 item 2: the ORIGINAL fixture here (now item 6e-6h below) only ever proved that an untracked
# path's FIRST appearance in `git status --porcelain` moves the stamp. Measured: running the
# IDENTICAL subject against the IDENTICAL tree a second time, with the marker already present,
# gives `RESULT: PASS ... subject=stable` -- the same tree mutation, undetected. That does not
# reach the mechanism this item's title names ("a subject that mutates the tree it is stamped
# against"). This fixture appends to a COMMITTED file instead, so the edit lands as a genuine
# tracked diff, not the untracked-content case flake-sweep.sh documents as a known blind spot.

moved_repo="$tmp/movedrepo"
mkdir -p "$moved_repo"
git init -q "$moved_repo"
# Signing MUST be off in a fixture repo. This machine signs with a hardware key, and a fixture that
# reaches for it blocks on a PIN prompt no suite can answer -- it hangs forever rather than failing,
# which is the one outcome a test harness must never produce. `fixture-signing-check` enforces this
# across every file that creates a repo; it caught this file's omission in the branch sweep.
git -C "$moved_repo" config commit.gpgsign false
git -C "$moved_repo" config tag.gpgsign false
printf 'orig\n' > "$moved_repo/tracked.txt"
git -C "$moved_repo" add tracked.txt
git -C "$moved_repo" -c user.email=t@t -c user.name=t commit -q -m init
s6="$moved_repo/mover.sh"
mk_mover_tracked "$s6"
( cd "$moved_repo" && "$engine" -n 4 -- ./mover.sh "$moved_repo" > "$tmp/moved_out.txt" 2>&1 )
moved_rc=$?
moved_out="$(cat "$tmp/moved_out.txt")"
check_eq "$moved_rc" 1 '6a: a subject that mutates a TRACKED file it is stamped against exits 1'
check_contains "$moved_out" 'RESULT: FAIL' '6b: ...verdict FAIL'
check_contains "$moved_out" 'subject=MOVED' '6c: ...subject=MOVED'
check_contains "$moved_out" 'varying=0' \
  '6d: distinguished from a varying verdict -- the row itself never varied, only the tree'

# --------------------------------------------------- item 6e-h: untracked path, first appearance
#
# Retitled from the original "moved subject" row (T4 item 2). This fixture is real and worth
# keeping on its own terms, but what it actually pins is narrower than the item-6 title above: an
# untracked path's FIRST appearance in porcelain moves the stamp. It does NOT show that ongoing
# mutation of that same path is caught -- measured: re-running this identical subject against the
# identical tree, with the marker already present, reports `RESULT: PASS ... subject=stable`. That
# blind spot is real (flake-sweep.sh's own `stable)` narrative documents it) and is not exercised
# here; this row only claims the first-appearance case.

movedU_repo="$tmp/movedUrepo"
mkdir -p "$movedU_repo"
git init -q "$movedU_repo"
git -C "$movedU_repo" config commit.gpgsign false
git -C "$movedU_repo" config tag.gpgsign false
git -C "$movedU_repo" -c user.email=t@t -c user.name=t commit -q --allow-empty -m init
s6u="$movedU_repo/mover.sh"
mk_mover "$s6u"
( cd "$movedU_repo" && "$engine" -n 4 -- ./mover.sh "$movedU_repo" > "$tmp/movedU_out.txt" 2>&1 )
movedU_rc=$?
movedU_out="$(cat "$tmp/movedU_out.txt")"
check_eq "$movedU_rc" 1 \
  '6e: an untracked path newly appearing in porcelain still fails this sweep (first appearance only)'
check_contains "$movedU_out" 'RESULT: FAIL' '6f: ...verdict FAIL'
check_contains "$movedU_out" 'subject=MOVED' \
  '6g: ...subject=MOVED, from the new untracked path entering porcelain'
check_contains "$movedU_out" 'varying=0' \
  '6h: distinguished from a varying verdict -- the row itself never varied, only the tree'

# ---------------------------------------------------------------- item 7: outside a git tree

notrepo="$tmp/notrepo"
mkdir -p "$notrepo"
( cd "$notrepo" && "$engine" -n 3 -- true > "$tmp/notrepo_out.txt" 2>&1 )
notrepo_rc=$?
notrepo_out="$(cat "$tmp/notrepo_out.txt")"
check_eq "$notrepo_rc" 0 '7a: a non-git subject with a deterministic command still exits 0'
check_contains "$notrepo_out" 'subject=not-a-repo' '7b: subject=not-a-repo, never silent'
check_contains "$notrepo_out" 'drift could not be judged' \
  '7c: the report says drift could not be judged rather than passing quietly'

# ------------------------------------------------------------------------------------ item LC1
#
# T3 fixed run-long.sh's own outside_git_repo() to force LC_ALL=C on its git probe regardless of
# the CALLER's locale, so a translated (e.g. German) gettext catalogue no longer folds a genuine
# outside-a-repo caller into the "some other failure" branch. This row exercises that fix through
# flake-sweep.sh itself (which shells out to run-long.sh --stamp), by exporting LC_ALL=de_DE.UTF-8
# for the whole engine invocation, run from a directory outside every git repo.
#
# Same observed-output gate as test_run_long.sh's stamp-de rows: gate on what THIS git actually
# prints under the translated locale, never on `locale -a` -- a git with no German catalogue
# installed prints the English text regardless of LC_ALL, which would make the defect unreachable
# here and a "pass" would prove nothing. Skip rather than fake green.
lc1_dir="$tmp/lc1"
mkdir -p "$lc1_dir"
lc1_probe_err="$(LC_ALL=de_DE.UTF-8 git -C "$lc1_dir" rev-parse --show-toplevel 2>&1 1>/dev/null)"
if [[ "$lc1_probe_err" == *'not a git repository (or any of the parent directories)'* ]]; then
  printf 'SKIP  LC1: this git has no German catalogue (prints English even under LC_ALL=de_DE.UTF-8) -- defect unreachable here\n'
else
  s_lc1="$tmp/lc1_stable.sh"
  mk_stable "$s_lc1"
  ( cd "$lc1_dir" && LC_ALL=de_DE.UTF-8 "$engine" -n 3 -- "$s_lc1" > "$tmp/lc1_out.txt" 2>&1 )
  lc1_out="$(cat "$tmp/lc1_out.txt")"
  check_contains "$lc1_out" 'subject=not-a-repo' \
    'LC1: outside a repo under a translated (German) locale still reports subject=not-a-repo'
  check_contains "$lc1_out" 'RESULT: PASS' \
    'LC1: ...and the sweep still reaches a clean PASS verdict, not a spurious drift failure'
fi

# ---------------------------------------------------------- item: both verdict formats together

s8="$tmp/mk_auditstyle.sh"
mk_auditstyle "$s8"
run_in "$quiet_dir" -n 3 -- "$s8"
check_eq "$RC" 0 '8a: a mixed one-space/audit-style subject is still deterministic -> exit 0'
check_contains "$OUT" 'RESULT: PASS' '8b: ...verdict PASS'
check_contains "$OUT" 'rows=2' \
  '8c: both the one-space PASS row and the audit-style em-dash FAIL row were parsed as 2 labels'

# ------------------------------------------------------- item: rowless subject, exit code varies

s9="$tmp/mk_rc_alt.sh"
mk_rc_alternating "$s9"
c9="$tmp/rc_counter"
rm -f "$c9"
run_in "$quiet_dir" -n 6 -- "$s9" "$c9"
check_eq "$RC" 1 '9a: a rowless subject with an alternating exit code is FAIL, not a false clean'
check_contains "$OUT" 'RESULT: FAIL' '9b: ...verdict FAIL'
check_contains "$OUT" 'rows=0' '9c: ...rows=0, printed rather than omitted for a rowless subject'
check_contains "$OUT" '<exit status>' \
  '9d: ...and <exit status> is named as the varying label -- half this tool'"'"'s population'

# --------------------------------------------------------- item u1: the stamp COMMAND itself fails
#
# T4 item 1 [BLOCKER]: `subject=UNVERIFIABLE` had no row at all -- three mutations previously
# survived a green suite: subject="UNVERIFIABLE" -> "stable"; the `bad_stamp_count -gt 0` test ->
# `if false`; and dropping UNVERIFIABLE from the FAIL condition. Copy the engine to a directory
# with NO run-long.sh beside it, so `"$run_long" --stamp` fails to even launch (the engine resolves
# `run_long="$here/run-long.sh"` from its OWN location) and every stamp row records a nonzero rc --
# distinct from "the stamp ran fine and found nothing" (subject=not-a-repo, item 7). This row does
# not need a pre-fix RED: nothing about flake-sweep.sh is changing here, and the branch it covers
# is already correct -- its evidence is the mutation coverage below, not a RED/GREEN pair. Run from
# a quiet directory per the global rule; the failure happens before the copy ever reaches git, so a
# plain (non-repo) directory keeps it out of the caller's tree without needing one.

noRL_dir="$tmp/no_run_long"
mkdir -p "$noRL_dir/cwd"
cp "$engine" "$noRL_dir/flake-sweep.sh"
chmod +x "$noRL_dir/flake-sweep.sh"
( cd "$noRL_dir/cwd" && "$noRL_dir/flake-sweep.sh" -n 3 -- true > "$tmp/unverif_out.txt" 2>&1 )
unverif_rc=$?
unverif_out="$(cat "$tmp/unverif_out.txt")"
check_eq "$unverif_rc" 1 'u1a: a subject whose stamp COMMAND itself fails to launch exits 1'
check_contains "$unverif_out" 'RESULT: FAIL' 'u1b: ...verdict FAIL'
check_contains "$unverif_out" 'subject=UNVERIFIABLE' \
  'u1c: ...subject=UNVERIFIABLE -- distinct from a stamp that ran and found nothing (not-a-repo)'

# ------------------------------------------------------------------------ item 10: skA-skF (T1)
#
# All six use n=6 (see the plan: enough inter-run gaps for the event-driven restore to land
# before run 3 in every measured trial). skA-skD go through the mid-skip fixture; skE/skF need no
# skip at all -- they hold the rewritten aggregation loop honest at both ends.

# skA: mid-skip, rowless subject.
skA_dir="$tmp/skA"
mk_skip_repo "$skA_dir/repo"
mk_skA "$skA_dir/subj.sh"
skA_out="$skA_dir/out.txt"
(cd "$skA_dir/repo" && run_skip "$skA_dir/subj.sh" 6 "$skA_out" "$skA_dir/marker")
skA_text="$(cat "$skA_out")"
check_contains "$skA_text" 'RESULT: INCOMPLETE' \
  'skA: a rowless subject skipped mid-sweep is INCOMPLETE, not a false PASS'
check_eq "$(sweep_rc "$skA_out")" 3 'skA: ...exit 3'
check_contains "$skA_text" 'rows=0' 'skA: ...rows=0, rowless throughout'
check_range "$(run_count "$skA_out" 6)" 2 6 'skA: ...runs=<c>/6 with 2 <= c < 6'

# skB: mid-skip, stable rows.
skB_dir="$tmp/skB"
mk_skip_repo "$skB_dir/repo"
mk_skB "$skB_dir/subj.sh"
skB_out="$skB_dir/out.txt"
(cd "$skB_dir/repo" && run_skip "$skB_dir/subj.sh" 6 "$skB_out" "$skB_dir/marker")
skB_text="$(cat "$skB_out")"
check_contains "$skB_text" 'RESULT: INCOMPLETE' 'skB: a stable subject skipped mid-sweep is INCOMPLETE'
check_eq "$(sweep_rc "$skB_out")" 3 'skB: ...exit 3'
check_contains "$skB_text" 'varying=0' \
  'skB: ...and the skip itself must not manufacture variance in a stable row'
check_range "$(run_count "$skB_out" 6)" 2 6 'skB: ...runs=<c>/6 with 2 <= c < 6'
# NT1 (T4): skB is INCOMPLETE with stable rows (varying=0) -- the NOTE that variance survived an
# incomplete denominator must never print here. Kills a mutant that turns the guarding `&&` into
# `||`: with that mutant, "incomplete" alone (true for skB) would be enough to print the NOTE
# regardless of whether any variance was actually found.
check_absent "$skB_text" 'NOTE: variance was found' \
  'NT1: a stable-but-incomplete sweep never prints the variance-survived-incompleteness NOTE'

# skC: run 1 itself is skipped (subject starts non-executable), stable rows.
skC_dir="$tmp/skC"
mk_skip_repo "$skC_dir/repo"
mk_skC "$skC_dir/subj.sh"
chmod -x "$skC_dir/subj.sh"
skC_out="$skC_dir/out.txt"
(cd "$skC_dir/repo" && run_skip "$skC_dir/subj.sh" 6 "$skC_out")
skC_text="$(cat "$skC_out")"
check_contains "$skC_text" 'RESULT: INCOMPLETE' 'skC: skipping run 1 itself is still INCOMPLETE'
check_eq "$(sweep_rc "$skC_out")" 3 'skC: ...exit 3'
check_contains "$skC_text" 'varying=0' \
  'skC: ...and the baseline must not be hardcoded to run 1, which never executed'
check_range "$(run_count "$skC_out" 6)" 2 6 'skC: ...runs=<c>/6 with 2 <= c < 6'

# skD: mid-skip PLUS a genuine flip -- both facts, one row.
skD_dir="$tmp/skD"
mk_skip_repo "$skD_dir/repo"
mk_skD "$skD_dir/subj.sh"
skD_out="$skD_dir/out.txt"
(cd "$skD_dir/repo" && run_skip "$skD_dir/subj.sh" 6 "$skD_out" "$skD_dir/marker" "$skD_dir/counter")
skD_text="$(cat "$skD_out")"
check_contains "$skD_text" 'RESULT: INCOMPLETE' 'skD: a genuine flip stays visible under a skip'
check_eq "$(sweep_rc "$skD_out")" 3 'skD: ...exit 3'
check_contains "$skD_text" 'varying=1' 'skD: ...naming exactly the one real flip'
check_contains "$skD_text" 'row two' 'skD: ...the real flip is named'
check_absent "$skD_text" $'  row one\n' 'skD: ...the stable row is not also named as varying'
check_range "$(run_count "$skD_out" 6)" 2 6 'skD: ...runs=<c>/6 with 2 <= c < 6'
check_contains "$skD_text" 'variance was found despite' \
  'skD: ...and the report says the found variance is not retracted by an incomplete denominator'

# skE: no skip -- varies ONLY on its first run. Holds the new baseline-on-first-executed-run
# honest: a hardcoded baseline of a fixed index other than the true first run would miss this.
s_skE="$tmp/skE.sh"
mk_skE "$s_skE"
c_skE="$tmp/skE_counter"
rm -f "$c_skE"
run_in "$quiet_dir" -n 5 -- "$s_skE" "$c_skE"
check_eq "$RC" 1 'skE: a subject varying only on its first run exits 1'
check_contains "$OUT" 'RESULT: FAIL' 'skE: ...verdict FAIL'
check_contains "$OUT" 'varying=1' 'skE: ...naming exactly the one varying label'

# skF: no skip -- varies ONLY on its final run. Holds the runs[] loop's upper bound honest: a
# loop excluding the last executed run would miss this.
s_skF="$tmp/skF.sh"
mk_skF "$s_skF"
c_skF="$tmp/skF_counter"
rm -f "$c_skF"
run_in "$quiet_dir" -n 5 -- "$s_skF" "$c_skF" 5
check_eq "$RC" 1 'skF: a subject varying only on its final run exits 1'
check_contains "$OUT" 'RESULT: FAIL' 'skF: ...verdict FAIL'
check_contains "$OUT" 'varying=1' 'skF: ...naming exactly the one varying label'

# SK1 (T4): a SKIP count that alternates, verdicts otherwise constant. No skip mechanism (mid-sweep
# disable) is involved here -- "SK1" names the fixture's SKIP-tally focus, not the skA-skF skip
# family above. `flappy` is printed with SKIP once on odd runs and twice (identical raw text both
# times, so never collided) on even runs, so its per-run tuple alternates between (0,0,1) and
# (0,0,2) while the stable `row one` PASS never moves. Kills a SKIP->FAIL-tally alias mutant: one
# that folds the skip count into the fail slot of the (pass,fail,skip) tuple would still see this
# label's tuple as constant (0,0) in the pass/fail slots) and miss the real variance.
mk_sk1() { cat > "$1" <<'SUBJ'
#!/usr/bin/env bash
c="$1"
n=$(( $(cat "$c" 2>/dev/null || echo 0) + 1 )); echo "$n" > "$c"
printf 'PASS  row one\n'
printf 'SKIP  flappy\n'
if [ $((n % 2)) -eq 0 ]; then printf 'SKIP  flappy\n'; fi
SUBJ
chmod +x "$1"; }

s_sk1="$tmp/sk1.sh"
mk_sk1 "$s_sk1"
c_sk1="$tmp/sk1_counter"
rm -f "$c_sk1"
run_in "$quiet_dir" -n 4 -- "$s_sk1" "$c_sk1"
check_eq "$RC" 1 'SK1: a SKIP count that alternates run to run is FAIL, not a false clean'
check_contains "$OUT" 'RESULT: FAIL' 'SK1: ...verdict FAIL'
check_contains "$OUT" 'varying=1' 'SK1: ...naming exactly the one varying label'
check_contains "$OUT" 'flappy' 'SK1: ...the alternating-SKIP label is named'
check_absent "$OUT" $'  row one\n' 'SK1: ...the stable PASS row is not also named as varying'

# --------------------------------------------------- item CC: concurrent sweeps share exits.tsv
#
# BLOCKER, re-opened: `completed` was rederived from exits.tsv, a file --artifact-dir can point
# at a directory a SECOND sweep also writes into (both with --force, since a shared dir is never
# empty for whichever sweep starts second). Two sweeps sharing one artifact dir, one of which
# skips a run, previously let the skipping sweep's own `completed` be inflated by the PEER's rows
# landing in the same [1,n] run-id range -- reported `PASS ... runs=n/n` despite completing fewer
# than n runs itself (measured pre-fix: sweep A at n=6, mid-skip, sharing a dir with sweep B at
# n=4, both rowless subjects: `RESULT: PASS rc=0 runs=6/6 rows=0 varying=0 subject=stable`).
# Reproduced with the suite's existing event-driven skip mechanism (mk_skB / the reader that
# chmod +x's the subject the instant "could not execute" appears) rather than a timer -- a timed
# fixture is a flaky row inside a flake detector. Sweep B is backgrounded so the two genuinely
# overlap; both subjects are ROWLESS so a label-merge across the shared labels.tsv (a separate,
# real exposure this row does not exist to test) cannot itself produce a misleading verdict here.

mk_rowless_cc() { cat > "$1" <<'SUBJ'
#!/usr/bin/env bash
exit 0
SUBJ
chmod +x "$1"; }

cc_dir="$tmp/cc"
mkdir -p "$cc_dir/repo" "$cc_dir/art"
mk_skip_repo "$cc_dir/repo"
cc_subjA="$cc_dir/subjA.sh"
mk_skA "$cc_subjA"
cc_subjB="$cc_dir/subjB.sh"
mk_rowless_cc "$cc_subjB"

# Sweep B: deterministic, rowless, n=4, backgrounded so it overlaps sweep A below. --force since
# it may lose the race to create the shared, still-empty artifact dir.
( cd "$cc_dir/repo" && "$engine" -n 4 --artifact-dir "$cc_dir/art" --force -- "$cc_subjB" > "$cc_dir/outB.txt" 2>&1 ) &
cc_bpid=$!

# Sweep A: n=6, mid-skip subject, the SAME --artifact-dir, also --force. Event-driven restore, no
# sleep -- mirrors run_skip's mechanism directly since run_skip itself takes no --artifact-dir.
cc_outA="$cc_dir/outA.txt"
{ (cd "$cc_dir/repo" && "$engine" -n 6 --artifact-dir "$cc_dir/art" --force -- "$cc_subjA" "$cc_dir/marker") 2>&1; printf 'SWEEP_RC=%d\n' "$?"; } \
  | while IFS= read -r line; do
      printf '%s\n' "$line"
      [[ "$line" == *"could not execute"* ]] && chmod +x "$cc_subjA" 2> /dev/null
    done > "$cc_outA"

wait "$cc_bpid" 2> /dev/null

cc_textA="$(cat "$cc_outA")"
cc_rcA="$(sweep_rc "$cc_outA")"

check_absent "$cc_textA" 'RESULT: PASS' \
  'CC-a: a sweep skipped mid-run, sharing --artifact-dir with a concurrent peer, must not report PASS'
check_contains "$cc_textA" 'RESULT: INCOMPLETE' \
  'CC-b: ...INCOMPLETE instead -- this sweep'"'"'s own completed count, unaffected by the peer'
check_absent "$cc_textA" 'runs=6/6' \
  'CC-c: ...never a full 6/6 denominator -- the peer'"'"'s rows must not backfill the run this sweep skipped'
check_eq "$cc_rcA" 3 'CC-d: ...exit 3'

# --------------------------------------------------- item EXT: a foreign writer's OUT-OF-RANGE
# --------------------------------------------------- rows must not enter THIS sweep's own baseline
#
# Item CC above pins the `completed` denominator half of the fix, but cannot reach the OTHER half
# named in the same fix -- the executed run-id SET handed to the aggregation awk -- because
# `completed` short-circuits the verdict to INCOMPLETE before `rows`/`varying` are ever consulted,
# and CC's rowless subject never even enters the aggregation block. MEASURED: reverting only the
# aggregation's run-set derivation back to reading exits.tsv, while leaving `completed` fixed,
# survived the full suite (item CC included) with zero failures.
#
# This row targets that half directly and deterministically -- no second live sweep, no timing at
# all, since --artifact-dir's own files are truncated at every sweep's START, so genuinely
# concurrent writers can only collide through live interleaving (inherently racy, and a timed or
# interleaving-dependent fixture is the worst defect available inside a flake detector). Instead,
# the SUBJECT itself appends a row at run-id 999 -- clearly out of this sweep's own n=3 range, as
# a peer sweep sharing the dir would leave behind -- directly into exits.tsv/labels.tsv on every
# invocation. Pre-fix, the aggregation's run-set derivation carried NO [1,n] bound (unlike
# `completed`'s own computation beside it), so 999 enters `runs[]` -- and being the run-999 row
# written FIRST in file order every iteration, `runs[1]` (the variance baseline) resolves to run
# 999 itself, which labels.tsv holds no "same-label" row for; every real run then reads as
# DIFFERING from that phantom all-zero baseline, so a subject that never varies reports FAIL.

ext_dir="$tmp/ext"
mkdir -p "$ext_dir/art"
s_ext="$tmp/ext_subj.sh"
cat > "$s_ext" <<'SUBJ'
#!/usr/bin/env bash
art="$1"
printf '999\tforeign-label\t0\t1\t0\n' >> "$art/labels.tsv"
printf '999\t0\n' >> "$art/exits.tsv"
printf 'PASS  same-label\n'
SUBJ
chmod +x "$s_ext"

( cd "$quiet_dir" && "$engine" -n 3 --artifact-dir "$ext_dir/art" -- "$s_ext" "$ext_dir/art" > "$ext_dir/out.txt" 2>&1 )
ext_rc=$?
ext_out="$(cat "$ext_dir/out.txt")"
check_eq "$ext_rc" 0 \
  'EXT-a: a deterministic subject is unaffected by a foreign OUT-OF-RANGE row in a shared artifact dir'
check_contains "$ext_out" 'RESULT: PASS' 'EXT-b: ...verdict PASS'
check_contains "$ext_out" 'varying=0' \
  'EXT-c: ...varying=0 -- run 999 must never become this sweep'"'"'s own variance baseline'
check_absent "$ext_out" $'  same-label\n' \
  'EXT-d: ...and same-label specifically is not named as varying'

# ---------------------------------------------------------------- item T2: close the stamp chain
#
# The sweep stamps the tree BEFORE each run and never after the last one, so a mutation caused by
# the FINAL run goes unobserved -- pre-fix this reports `RESULT: PASS ... subject=stable` with a
# dirty tracked file left sitting in the repo. The edited file must be COMMITTED so the edit lands
# as a tracked diff, not the untracked-content case this tool documents as a known blind spot.
# Subject script and counter live OUTSIDE the fixture repo (only the cwd is stamped) -- see the
# plan's deterministic-skip fixture note on why an in-repo counter would move the stamp itself.

mk_final_editor() { cat > "$1" <<'SUBJ'
#!/usr/bin/env bash
repo="$1"; counter="$2"; total="$3"
n=$(( $(cat "$counter" 2>/dev/null || echo 0) + 1 ))
echo "$n" > "$counter"
if [ "$n" = "$total" ]; then
  printf x >> "$repo/tracked.txt"
fi
printf 'PASS  ok\n'
SUBJ
chmod +x "$1"; }

t2_dir="$tmp/t2"
mkdir -p "$t2_dir/repo"
git init -q "$t2_dir/repo"
git -C "$t2_dir/repo" config commit.gpgsign false
git -C "$t2_dir/repo" config tag.gpgsign false
printf 'orig\n' > "$t2_dir/repo/tracked.txt"
git -C "$t2_dir/repo" add tracked.txt
git -C "$t2_dir/repo" -c user.email=t@t -c user.name=t commit -q -m init

s_t2="$t2_dir/final_editor.sh"
mk_final_editor "$s_t2"
c_t2="$t2_dir/counter"
rm -f "$c_t2"
( cd "$t2_dir/repo" && "$engine" -n 4 -- "$s_t2" "$t2_dir/repo" "$c_t2" 4 > "$t2_dir/out.txt" 2>&1 )
t2_rc=$?
t2_out="$(cat "$t2_dir/out.txt")"
check_eq "$t2_rc" 1 'T2: a tracked edit made only on the FINAL run still fails the sweep'
check_contains "$t2_out" 'RESULT: FAIL' 'T2: ...verdict FAIL'
check_contains "$t2_out" 'subject=MOVED' \
  'T2: ...subject=MOVED -- the closing stamp catches an edit the per-run-before stamps could not'

# ---------------------------------------------------------------- item T3: tabs collide fields
#
# The extraction regex deliberately admits tabs into a label (it only strips the verdict word and
# the whitespace immediately after it), so a label containing a raw TAB writes a 6-field TSV row
# where the aggregator expects 5. When the pre-tab prefix equals a real label's name -- here both
# read back as label "foo" -- the aggregator's $2/$3/$4/$5 read collides with the real row, and
# whichever write lands last (awk's `for (l in seen)` hash-iteration order, machine-dependent)
# overwrites the other's tuple. Subject and counter live outside the fixture dir per the usual
# rule; this row needs no git repo since it asserts nothing about subject/drift.
#
# `rows=2` is the load-bearing assertion: it is violated by `rows=1` under EITHER hash ordering,
# because the collision always merges the two TSV lines' $2 into the same label name "foo" --
# that part does not depend on which write wins. `varying=1` is red only under the ordering this
# machine's awk happens to produce (the tab row lands second and erases the real flip); keep it
# anyway since it is the actual defect this task fixes, and note in the report if it is not red.
# Do not drop `rows=2` as redundant with `varying=1` -- see the plan's T3 section.

mk_t3_flip() { cat > "$1" <<'SUBJ'
#!/usr/bin/env bash
c="$1"
n=$(( $(cat "$c" 2>/dev/null || echo 0) + 1 )); echo "$n" > "$c"
if [ "$n" = 2 ]; then printf 'FAIL  foo\n'; else printf 'PASS  foo\n'; fi
printf 'PASS  foo\tbar\n'
SUBJ
chmod +x "$1"; }

s_t3="$tmp/t3_flip.sh"
mk_t3_flip "$s_t3"
c_t3="$tmp/t3_counter"
rm -f "$c_t3"
run_in "$quiet_dir" -n 4 -- "$s_t3" "$c_t3"
check_contains "$OUT" 'varying=1' \
  'T3: a genuine flip on "foo" is not erased by a tab-bearing label colliding with its prefix'
check_contains "$OUT" 'rows=2' \
  'T3: ...and the tab-bearing label is counted as its own row, not merged into "foo"'

# ------------------------------------------------------- T4 item 4: --artifact-dir contract
#
# No row passed --artifact-dir before this -- every test used the `mktemp -d` default, so both
# "parse the flag, then discard it" mutants (`artifact_dir="$2"` -> `""`; deleting the
# --artifact-dir case from the arg loop) survived a green suite. The directory here is a NAMED
# subdir of $tmp, guaranteed to differ from whatever `mktemp -d` would independently choose -- a
# test supplying the option's own default cannot tell whether the option is read at all.

art4_dir="$tmp/art4_explicit"
s_art4="$tmp/mk_stable_art4.sh"
mk_stable "$s_art4"
run_in "$quiet_dir" -n 3 --artifact-dir "$art4_dir" -- "$s_art4"
check_eq "$RC" 0 'art4a: a deterministic subject with an explicit --artifact-dir still exits 0'
check_contains "$OUT" "artifact-dir=$art4_dir" \
  'art4b: ...and the sweep echoes back the CALLER-SUPPLIED directory, not a mktemp default'
check_exists "$art4_dir/labels.tsv" 'art4c: labels.tsv landed in the explicit directory'
check_exists "$art4_dir/exits.tsv" 'art4d: exits.tsv landed in the explicit directory'
check_exists "$art4_dir/stamps.tsv" 'art4e: stamps.tsv landed in the explicit directory'
check_exists "$art4_dir/run-001.log" 'art4f: run-001.log landed in the explicit directory'

# ------------------------------------------------------- T4 item 6: trailing-diagnostic strips
#
# Both `sub(/ \(.*\)$/, "", label)` and `sub(/ — .*$/, "", label)` were dead to this suite -- no
# fixture emitted a `(...)` diagnostic at all, and a strip only matters when the diagnostic text
# VARIES run to run while the label itself stays constant; that is the only shape in which
# failing to strip turns one stable label into several varying ones. Two separate subjects, one
# per form -- a single subject exercising only one leaves the other strip's mutant alive, the same
# half-covered mistake this item exists to fix.

mk_t6_paren() { cat > "$1" <<'SUBJ'
#!/usr/bin/env bash
c="$1"
n=$(( $(cat "$c" 2>/dev/null || echo 0) + 1 )); echo "$n" > "$c"
printf 'PASS  paren-label (attempt %d)\n' "$n"
SUBJ
chmod +x "$1"; }

mk_t6_dash() { cat > "$1" <<'SUBJ'
#!/usr/bin/env bash
c="$1"
n=$(( $(cat "$c" 2>/dev/null || echo 0) + 1 )); echo "$n" > "$c"
printf 'PASS  dash-label — attempt %d\n' "$n"
SUBJ
chmod +x "$1"; }

s_t6p="$tmp/t6_paren.sh"
mk_t6_paren "$s_t6p"
c_t6p="$tmp/t6_paren_counter"
rm -f "$c_t6p"
run_in "$quiet_dir" -n 4 -- "$s_t6p" "$c_t6p"
check_contains "$OUT" 'RESULT: PASS' \
  't6p: a "(...)" diagnostic that varies run to run does not itself make the label vary'
check_contains "$OUT" 'varying=0' \
  't6p: ...varying=0, proving the " (.*)$" strip is live -- the raw label would vary every run'

s_t6d="$tmp/t6_dash.sh"
mk_t6_dash "$s_t6d"
c_t6d="$tmp/t6_dash_counter"
rm -f "$c_t6d"
run_in "$quiet_dir" -n 4 -- "$s_t6d" "$c_t6d"
check_contains "$OUT" 'RESULT: PASS' \
  't6d: an em-dash diagnostic that varies run to run does not itself make the label vary'
check_contains "$OUT" 'varying=0' \
  't6d: ...varying=0, proving the " — .*$" strip is live -- the raw label would vary every run'

# --------------------------------------------------------- T4 item 7: the usage surface, widened
#
# Only `-n 0` was exercised. Removing the `-n` integer check, removing the `no command given`
# check, and breaking `-h`/`--help` all survived. For `-n abc`, under `set -u` the healthy code's
# own die_usage path is the ONLY one that ever prints a `RESULT: ERROR` line -- the mutant instead
# dies on an unbound-variable reference deep in the loop with rc=1 and no RESULT line at all, so a
# bare "exit code is nonzero" check cannot tell the two apart; `RESULT: ERROR`'s printf is already
# pinned by an existing row (item 3) and is left untouched here.

run -n abc -- true
check_eq "$RC" 2 'usage-n: -n with a non-integer value is a usage error, not silently accepted'
check_contains "$OUT" 'RESULT: ERROR' \
  'usage-n: ...RESULT: ERROR present -- the mutant path dies on an unbound var before ever printing one'

run -n 3 --
check_eq "$RC" 2 'usage-nocmd: -n given but no command after "--" is a usage error'
check_contains "$OUT" 'RESULT: ERROR' \
  'usage-nocmd: ...RESULT: ERROR present -- the mutant path dies on an unbound var before printing one'

run -h
check_eq "$RC" 0 'usage-h: -h prints usage and exits 0'
check_contains "$OUT" 'Usage: flake-sweep.sh' 'usage-h: ...the usage text itself'

run --help
check_eq "$RC" 0 'usage-help: --help behaves identically to -h'
check_contains "$OUT" 'Usage: flake-sweep.sh' 'usage-help: ...the usage text itself'

# ------------------------------------------------ T4 item 9: -n cannot forge a RESULT line
#
# `die_usage` used to interpolate `${n:-0}` into the RESULT grammar unvalidated -- `-n` is raw,
# attacker-controlled text at the point die_usage can be called for it, so a multi-line value
# could inject a second, fake `RESULT:` line onto stdout, immediately after the real one. Measured
# pre-fix: `-n $'1\nRESULT: PASS rc=0 runs=1/1 rows=0 varying=0 subject=stable'` produced TWO
# LINES starting `RESULT:` in the combined stream -- a real `ERROR` on stdout, then a forged
# `PASS` a caller doing `grep RESULT | tail -1` (or just trusting the last line) would read as a
# clean sweep. `check_eq` on the exact COUNT of lines matching `^RESULT:` is the load-bearing
# assertion: a bare substring check for `RESULT: PASS` is satisfied even by this fix's own design
# -- die_usage additionally escapes an embedded newline in ITS OWN diagnostic-message argument
# (several call sites build that message as "... : $n", so the raw value reaches stderr too), so
# the literal text `RESULT: PASS ...` still appears in $OUT as part of one escaped diagnostic
# line, just never again at the START of a line -- the shape a real caller's `^RESULT:` grep
# would ever match.
run -n $'1\nRESULT: PASS rc=0 runs=1/1 rows=0 varying=0 subject=stable' -- true
check_eq "$RC" 2 'inject-n: a usage error, same as any other malformed -n value'
check_eq "$(printf '%s\n' "$OUT" | grep -c '^RESULT:')" 1 \
  'inject-n: exactly ONE line starting RESULT: in the whole output -- the embedded newline did not forge a second'
check_absent "$OUT" $'\nRESULT: PASS' \
  'inject-n: ...and no NEWLINE-then-RESULT:-PASS shape survives -- the injected text is neutralized to one escaped line, not merely relocated'

# --------------------------------------------------- T4 item 10: -n with a leading zero is octal
# --------------------------------------------------- in arithmetic context -- reject it
#
# `-n 010` passed the `^[0-9]+$` shape check but was then read in bash ARITHMETIC context by the
# `for` loop bound and every `-ge`/`-eq` test, where a leading-zero integer literal is OCTAL: the
# sweep silently ran 8 times, not 10, with every progress/RESULT line self-consistently reporting
# the octal value (nothing downstream looked wrong). Rejected outright rather than reinterpreted.
run -n 010 -- true
check_eq "$RC" 2 'octal-n: a leading zero on -n is a usage error, not silently reinterpreted as octal'
check_contains "$OUT" 'RESULT: ERROR' 'octal-n: ...verdict ERROR'
check_contains "$OUT" 'octal' 'octal-n: ...and the diagnostic names WHY, not just that it failed'
check_absent "$OUT" 'runs=8/8' 'octal-n: ...never silently running the octal-8 interpretation'

# ------------------------------------------------- T4 item 8: stable rows, flipping exit code
#
# The two axes were never crossed: the rowless row (item 9) has a flipping exit code but no
# labels, and the flaky-rows row (item 2) has labels that vary but a constant exit code. This
# subject prints the SAME two rows every run and only its exit code alternates.

mk_stable_rc_alt() { cat > "$1" <<'SUBJ'
#!/usr/bin/env bash
c="$1"
n=$(( $(cat "$c" 2>/dev/null || echo 0) + 1 )); echo "$n" > "$c"
printf 'PASS  row one\n'
printf 'PASS  row two\n'
[ $((n % 2)) -eq 0 ] && exit 1
exit 0
SUBJ
chmod +x "$1"; }

s8b="$tmp/mk_stable_rc_alt.sh"
mk_stable_rc_alt "$s8b"
c8b="$tmp/stable_rc_alt_counter"
rm -f "$c8b"
run_in "$quiet_dir" -n 6 -- "$s8b" "$c8b"
check_eq "$RC" 1 'item8: stable rows with a flipping exit code still exits 1'
check_contains "$OUT" 'RESULT: FAIL' 'item8: ...verdict FAIL'
check_contains "$OUT" 'rows=2' 'item8: ...both stable labels were parsed'
check_contains "$OUT" 'varying=1' \
  'item8: ...naming exactly one varying "label" -- the rows themselves never varied'
check_contains "$OUT" '<exit status>' \
  'item8: ...and the varying label is <exit status>, crossing the axis item 9 (rowless) never did'
check_absent "$OUT" $'  row one\n' 'item8: ...row one is not itself named as varying'
check_absent "$OUT" $'  row two\n' 'item8: ...row two is not itself named as varying'

# ------------------------------------------------- item 11: BLOCKER -- exec failure (126/127) is
# ------------------------------------------------- shell DATA, not evidence the subject ran
#
# `command -v -- "$1"` proves the name RESOLVES, not that execve succeeds. A file that exists and
# is executable but whose interpreter is missing passes the guard, the shell returns 126/127, and
# a CONSTANT rc across every run reads as ordinary unvarying data -- PASS. Reproduces the measured
# case verbatim: a script whose shebang names a nonexistent interpreter.

mk_bad_shebang() { cat > "$1" <<'SUBJ'
#!/usr/bin/env python-that-does-not-exist
print(1)
SUBJ
chmod +x "$1"; }

s11="$tmp/bad_shebang.sh"
mk_bad_shebang "$s11"
run_in "$quiet_dir" -n 3 -- "$s11"
check_eq "$RC" 4 '11a: every completed run failing to execve is INDETERMINATE, exit 4 -- never a silent PASS'
check_contains "$OUT" 'RESULT: INDETERMINATE' '11b: ...verdict INDETERMINATE'
check_absent "$OUT" 'RESULT: PASS' '11c: ...and never PASS -- the measured pre-fix false clean'
check_contains "$OUT" 'WARNING' '11d: ...at least one per-run WARNING is printed'
check_contains "$OUT" 'rc=127' '11e: ...naming the actual exec-failure rc'

# 11f-h: a SINGLE legitimate 126/127 among otherwise-zero exits must NOT be over-blocked -- only
# "every completed run" failing to execve triggers INDETERMINATE. A subject may legitimately exit
# 127 for its own reasons on one run out of many.
mk_legit127() { cat > "$1" <<'SUBJ'
#!/usr/bin/env bash
c="$1"
n=$(( $(cat "$c" 2>/dev/null || echo 0) + 1 )); echo "$n" > "$c"
[ "$n" = 3 ] && exit 127
exit 0
SUBJ
chmod +x "$1"; }

s11l="$tmp/legit127.sh"
mk_legit127 "$s11l"
c11l="$tmp/legit127_counter"
rm -f "$c11l"
run_in "$quiet_dir" -n 6 -- "$s11l" "$c11l"
check_absent "$OUT" 'RESULT: INDETERMINATE' \
  '11f: a single legitimate 127 among otherwise-0 exits is not over-blocked into INDETERMINATE'
check_contains "$OUT" 'RESULT: FAIL' \
  '11g: ...it is still a real varying <exit status>, just not misclassified as an exec failure'
check_contains "$OUT" 'WARNING' '11h: ...but the one exec-failure-shaped rc still gets its per-run WARNING'

# ---------------------------------------------- item 12: BLOCKER -- `completed` is corruptible
# ---------------------------------------------- upward when --artifact-dir is shared
#
# `completed` used to be the raw ROW COUNT of exits.tsv, which equals "runs that executed" only
# if this process is that file's SOLE writer -- not true for a caller-supplied --artifact-dir with
# no lock. Simulate a second, concurrent writer sharing the directory by injecting foreign rows
# into exits.tsv WHILE the sweep is still running: one duplicating an already-legitimate run id
# (1) and one entirely out of the planned range (999). Fixed `completed` must count only DISTINCT
# run ids within 1..n, so neither injected row can move it past n.

art12_dir="$tmp/art12"
rm -rf "$art12_dir"
s12="$tmp/mk_slow5.sh"
cat > "$s12" <<'SUBJ'
#!/usr/bin/env bash
sleep 0.15
printf 'PASS  ok\n'
SUBJ
chmod +x "$s12"

( cd "$quiet_dir" && "$engine" -n 5 --artifact-dir "$art12_dir" -- "$s12" > "$tmp/art12_out.txt" 2>&1 ) &
art12_pid=$!
art12_tries=0
while [[ ! -f "$art12_dir/exits.tsv" ]] && [[ $art12_tries -lt 200 ]]; do
  sleep 0.02
  art12_tries=$((art12_tries + 1))
done
# Two foreign rows land mid-sweep: a duplicate of run id 1 (already legitimate) and an
# out-of-range id 999 (what a DIFFERENT sweep, or one with a different -n, would leave behind).
printf '1\t0\n999\t0\n' >> "$art12_dir/exits.tsv" 2> /dev/null
wait "$art12_pid"
art12_out="$(cat "$tmp/art12_out.txt")"
check_contains "$art12_out" 'runs=5/5' \
  '12a: injected foreign rows (dup id=1, out-of-range id=999) do not inflate completed past n'
check_absent "$art12_out" 'runs=6/5' '12b: ...never an impossible runs=6/5'
check_absent "$art12_out" 'runs=7/5' '12c: ...nor runs=7/5 (the raw-NR pre-fix defect, reborn)'

# 12d-f: refuse a non-empty --artifact-dir without --force, mirroring run-long.sh's --out --force
# contract -- the cheapest layer against the SAME hazard: a caller who reuses a directory the
# previous sweep already wrote into.
art12c_dir="$tmp/art12c"
mkdir -p "$art12c_dir"
: > "$art12c_dir/junk.txt"
run_in "$quiet_dir" --artifact-dir "$art12c_dir" -n 2 -- true
check_eq "$RC" 2 '12d: --artifact-dir pointed at a NON-EMPTY directory is refused without --force'
check_contains "$OUT" 'RESULT: ERROR' '12e: ...verdict ERROR, a usage fault -- the sweep never began'
run_in "$quiet_dir" --artifact-dir "$art12c_dir" --force -n 2 -- true
check_eq "$RC" 0 '12f: ...but --force allows reusing it anyway'

# --------------------------------------------------------------- item M1: MAJOR -- collision-aware
# --------------------------------------------------------------- label aggregation (closes D3)
#
# A stripped key that receives two or more DISTINCT raw texts within a single run ("collided") can
# hide a real opposite flip behind a constant merged tuple. Reproduces a parameterised-suite shape:
# "db connect (primary)" and "db connect (replica)" both strip to "db connect", and each run prints
# one PASS and one FAIL between them, alternating which -- so the MERGED tuple (1 pass, 1 fail) is
# constant even though each constituent alone flips every single run.

mk_merge() { cat > "$1" <<'SUBJ'
#!/usr/bin/env bash
c="$1"
n=$(( $(cat "$c" 2>/dev/null || echo 0) + 1 )); echo "$n" > "$c"
if [ $((n % 2)) -eq 1 ]; then
  printf 'FAIL  db connect (primary)\n'
  printf 'PASS  db connect (replica)\n'
else
  printf 'PASS  db connect (primary)\n'
  printf 'FAIL  db connect (replica)\n'
fi
SUBJ
chmod +x "$1"; }

s_m1="$tmp/merge.sh"
mk_merge "$s_m1"
c_m1="$tmp/merge_counter"
rm -f "$c_m1"
run_in "$quiet_dir" -n 6 -- "$s_m1" "$c_m1"
check_contains "$OUT" 'RESULT: FAIL' \
  'M1a: a collided key whose raw texts trade verdicts every run is caught as real variance'
check_contains "$OUT" 'varying=1' \
  'M1b: ...varying=1 -- the merge no longer hides the flip behind a constant summed tuple'
check_contains "$OUT" 'MERGED: db connect (2 raw labels)' \
  'M1c: the MERGED visibility qualifier is unchanged'
check_contains "$OUT" "$(printf 'VARYING LABELS:\n  db connect')" \
  'M1d: ...and the stripped key itself is named under VARYING LABELS:'

# M1e (control, green before and after): both constituents of a collided key hold CONSTANT --
# `primary` always FAILs, `replica` always PASSes -- so the collision is real (two distinct raw
# texts every run) but neither the merged tuple nor either constituent ever moves. Must stay a
# clean PASS: collision alone is not variance.
mk_merge_stable() { cat > "$1" <<'SUBJ'
#!/usr/bin/env bash
printf 'FAIL  db connect (primary)\n'
printf 'PASS  db connect (replica)\n'
SUBJ
chmod +x "$1"; }

s_m1e="$tmp/merge_stable.sh"
mk_merge_stable "$s_m1e"
run_in "$quiet_dir" -n 4 -- "$s_m1e"
check_contains "$OUT" 'RESULT: PASS' \
  'M1e: a collided key whose per-raw tuples are ALL constant stays a clean PASS'
check_contains "$OUT" 'rows=1' \
  'M1e (rows): ...one stripped label ("db connect")'
check_contains "$OUT" 'varying=0' \
  'M1e (varying): ...and no variance is reported'

# M1f: one label printed TWICE per run with a run-dependent diagnostic ("x (0.<n> s)" /
# "x (0.<n+5> s)"), both verdicts constantly PASS. The stripped tuple for "x" never moves (always
# 2 PASS), but the collided key's raw-text SET changes every run (the diagnostic embeds the run
# counter) -- the sweep cannot tell that legitimate diagnostic churn apart from two rows quietly
# trading verdicts, so this must be reported as AMBIGUOUS, not silently absorbed into PASS.
mk_ambig() { cat > "$1" <<'SUBJ'
#!/usr/bin/env bash
c="$1"
n=$(( $(cat "$c" 2>/dev/null || echo 0) + 1 )); echo "$n" > "$c"
printf 'PASS  x (0.%d s)\n' "$n"
printf 'PASS  x (0.%d s)\n' "$((n + 5))"
SUBJ
chmod +x "$1"; }

s_m1f="$tmp/ambig.sh"
mk_ambig "$s_m1f"
c_m1f="$tmp/ambig_counter"
rm -f "$c_m1f"
run_in "$quiet_dir" -n 4 -- "$s_m1f" "$c_m1f"
check_contains "$OUT" 'RESULT: INDETERMINATE rc=4' \
  'M1f: a collided key whose raw-text SET changes every run, under a constant verdict tuple, is INDETERMINATE'
check_contains "$OUT" "$(printf 'AMBIGUOUS LABELS:\n  x')" \
  'M1f (labels): ...and the ambiguous key is named under AMBIGUOUS LABELS:'

# M1g: M1f's ambiguous subject PLUS a genuinely flipping, unrelated row "y" (FAIL on run 2, PASS
# otherwise). Precedence: real variance elsewhere in the same sweep outranks an ambiguous key --
# the sweep must still report FAIL, not INDETERMINATE. Not a RED row: the bash-side elif chain
# already checks `total_varying` (the FAIL arm) before the new ambiguous arm, so this holds
# unchanged before and after this task's awk rewrite -- it pins that PRECEDENCE, falsified only by
# a mutant that reorders the two arms.
mk_ambig_flip() { cat > "$1" <<'SUBJ'
#!/usr/bin/env bash
c="$1"
n=$(( $(cat "$c" 2>/dev/null || echo 0) + 1 )); echo "$n" > "$c"
printf 'PASS  x (0.%d s)\n' "$n"
printf 'PASS  x (0.%d s)\n' "$((n + 5))"
if [ "$n" = 2 ]; then
  printf 'FAIL  y\n'
else
  printf 'PASS  y\n'
fi
SUBJ
chmod +x "$1"; }

s_m1g="$tmp/ambig_flip.sh"
mk_ambig_flip "$s_m1g"
c_m1g="$tmp/ambig_flip_counter"
rm -f "$c_m1g"
run_in "$quiet_dir" -n 4 -- "$s_m1g" "$c_m1g"
check_contains "$OUT" 'RESULT: FAIL' \
  'M1g: real variance elsewhere in the sweep outranks an ambiguous key -- FAIL, not INDETERMINATE'

# M1h (control): the SAME label printed twice per run with IDENTICAL raw text every time ("PASS
# x" / "PASS x", no diagnostic) -- never collided (one distinct raw text per run), so this must
# stay a clean PASS with no AMBIGUOUS LABELS: section at all.
mk_double_stable() { cat > "$1" <<'SUBJ'
#!/usr/bin/env bash
printf 'PASS  x\n'
printf 'PASS  x\n'
SUBJ
chmod +x "$1"; }

s_m1h="$tmp/double_stable.sh"
mk_double_stable "$s_m1h"
run_in "$quiet_dir" -n 4 -- "$s_m1h"
check_contains "$OUT" 'RESULT: PASS' \
  'M1h: a repeated IDENTICAL raw text is never collided -- stays a clean PASS'
check_absent "$OUT" 'AMBIGUOUS LABELS:' \
  'M1h (ambiguous): ...and AMBIGUOUS LABELS: is absent'

# M1i (T2 review): PARTIAL collision -- key "k" is collided (2 distinct raw texts) on odd runs
# only; on even runs both lines print the IDENTICAL raw text ("k (a)" twice), so that run holds
# only ONE distinct raw and is not collided at all. The stripped tuple for "k" never moves (always
# 2 PASS, every run), so the stripped-tuple path alone would read this as a clean PASS -- but the
# key's raw-text SET differs across runs ({a,b} on odd runs, {a} on even runs), which per-raw
# comparison cannot make sense of (there is no stable per-raw identity to compare against when a
# constituent silently disappears). Verified by direct engine run before writing this row: the
# fixed tree reports RESULT: INDETERMINATE rc=4 with AMBIGUOUS LABELS: naming "k".
mk_m1i() { cat > "$1" <<'SUBJ'
#!/usr/bin/env bash
c="$1"
n=$(( $(cat "$c" 2>/dev/null || echo 0) + 1 )); echo "$n" > "$c"
if [ $((n % 2)) -eq 1 ]; then
  printf 'PASS  k (a)\n'
  printf 'PASS  k (b)\n'
else
  printf 'PASS  k (a)\n'
  printf 'PASS  k (a)\n'
fi
SUBJ
chmod +x "$1"; }

s_m1i="$tmp/m1i.sh"
mk_m1i "$s_m1i"
c_m1i="$tmp/m1i_counter"
rm -f "$c_m1i"
run_in "$quiet_dir" -n 4 -- "$s_m1i" "$c_m1i"
check_contains "$OUT" 'RESULT: INDETERMINATE rc=4' \
  'M1i: a key collided on only SOME runs, whose raw-text set changes across runs, is INDETERMINATE'
check_contains "$OUT" "$(printf 'AMBIGUOUS LABELS:\n  k')" \
  'M1i (labels): ...and the ambiguous key is named under AMBIGUOUS LABELS:'

# M1j (T2 review): a duplicate raw text BESIDE a distinct one, where the per-raw tuple (not the
# merged/stripped tuple) is what actually varies. Every run prints raws "a" (twice) and "b" (once)
# for key "k" -- the raw-text SET is identical every run ({a,b}), and the stripped tuple is
# constant every run (2 PASS, 1 FAIL) -- so neither the raw-set-changed path (M1i above) nor the
# stripped-tuple path can see anything wrong. What actually varies is raw "a"'s OWN per-run tuple:
# both "a" lines PASS on odd runs ((2,0,0) for "a"), one PASSes and one FAILs on even runs
# ((1,1,0) for "a"). Verified by direct engine run before writing this row: the fixed tree reports
# RESULT: FAIL rc=1 varying=1 with VARYING LABELS: naming "k".
mk_m1j() { cat > "$1" <<'SUBJ'
#!/usr/bin/env bash
c="$1"
n=$(( $(cat "$c" 2>/dev/null || echo 0) + 1 )); echo "$n" > "$c"
if [ $((n % 2)) -eq 1 ]; then
  printf 'PASS  k (a)\n'
  printf 'PASS  k (a)\n'
  printf 'FAIL  k (b)\n'
else
  printf 'PASS  k (a)\n'
  printf 'FAIL  k (a)\n'
  printf 'PASS  k (b)\n'
fi
SUBJ
chmod +x "$1"; }

s_m1j="$tmp/m1j.sh"
mk_m1j "$s_m1j"
c_m1j="$tmp/m1j_counter"
rm -f "$c_m1j"
run_in "$quiet_dir" -n 4 -- "$s_m1j" "$c_m1j"
check_contains "$OUT" 'RESULT: FAIL' \
  'M1j: a per-raw tuple that flips while the merged/stripped tuple and raw set both stay constant is real variance'
check_contains "$OUT" 'varying=1' 'M1j: ...naming exactly the one varying label'
check_contains "$OUT" "$(printf 'VARYING LABELS:\n  k')" \
  'M1j (labels): ...and the stripped key itself is named under VARYING LABELS:'

# --------------------------------------------------------------- item M3: an unwritable stamps.tsv
# --------------------------------------------------------------- does not corrupt the verdict
#
# Retitled: this used to pin `subject=unknown` -- reached when stamps.tsv itself is unwritable
# (permission-denied), because every per-run stamp write then failed silently under
# `set -uo pipefail` (no `-e`) while `[[ -s "$stamps_tsv" ]]` gated the WHOLE drift computation on
# that same file. Since the read-back BLOCKER fix, subject drift is computed from `stamp_vals`/
# `stamp_rcs` -- this process's own in-loop record of every stamp it took -- which are appended
# unconditionally on every iteration BEFORE the (possibly failing) file write, and never read back
# from stamps.tsv at all. `subject=unknown` is therefore now believed STRUCTURALLY UNREACHABLE
# during a live run: with `n >= 2` enforced by usage validation, the loop always executes at least
# once and `stamp_vals` always gains an entry every iteration regardless of whether the write to
# disk succeeds -- an unwritable *artifact* directory no longer has any bearing on whether the
# *measurement itself* (independent of that directory) can be trusted. The `subject="unknown"`
# initial value and its `case` arm are kept as defensive insurance, the same way the
# `completed -gt n` INDETERMINATE branch is kept -- not a route this suite can currently force to
# fire. This row now pins the IMPROVEMENT directly: the sweep still reports the TRUE, correctly
# computed subject even though its own debugging artifact could not be persisted.

mk_one_row() { cat > "$1" <<'SUBJ'
#!/usr/bin/env bash
printf 'PASS  ok\n'
SUBJ
chmod +x "$1"; }

art13_dir="$tmp/art13"
mkdir -p "$art13_dir"
: > "$art13_dir/stamps.tsv"
chmod 400 "$art13_dir/stamps.tsv"
s13="$tmp/onerow13.sh"
mk_one_row "$s13"
# --force: this fixture must pre-populate stamps.tsv (to chmod it unwritable) before the sweep
# starts, which the item-12 non-empty---artifact-dir refusal would otherwise reject outright --
# unrelated to what THIS row exists to test.
( cd "$quiet_dir" && "$engine" -n 3 --artifact-dir "$art13_dir" --force -- "$s13" > "$tmp/art13_out.txt" 2>&1 )
art13_out="$(cat "$tmp/art13_out.txt")"
chmod 600 "$art13_dir/stamps.tsv" 2> /dev/null # restore, so the trap's rm -rf can clean it up
check_contains "$art13_out" 'SUBJECT:' \
  'M3a: a SUBJECT: line is always printed, even when stamps.tsv itself could not be written'
check_contains "$art13_out" 'subject=stable' \
  'M3b: ...and it names the CORRECT, in-memory-computed subject (quiet_dir never moved) -- not a punted "unknown"'
check_contains "$art13_out" 'RESULT: PASS' \
  'M3c: ...so the sweep reaches a real PASS verdict rather than being forced to abstain over an unrelated artifact-directory permission failure'

# --------------------------------------------------------------- item M4: MAJOR -- the tool's own
# --------------------------------------------------------------- artifacts move the tree it stamps
#
# `--artifact-dir .` inside the stamped repo makes each `run-NNN.log` appear in porcelain between
# stamps: a perfectly deterministic subject would otherwise yield `FAIL ... subject=MOVED`. Refuse
# it up front instead, unless the directory is verified gitignored.
#
# `.` is ALSO caught by the item-12 non-empty-directory refusal (a repo root is never empty), so
# a row built on `.` alone cannot tell whether THIS check -- the containment/gitignore one -- ever
# fires at all. MEASURED by mutation: disabling only the check-ignore guard left `--artifact-dir .`
# refused exactly the same (rc=2, RESULT: ERROR) via the OTHER check, and the mutant SURVIVED. A
# FRESH, not-yet-existing, non-gitignored subdirectory isolates it: empty (so item 12's check lets
# it through) and inside the repo (so only THIS check can refuse it).

mj4_repo="$tmp/mj4repo"
mkdir -p "$mj4_repo"
git init -q "$mj4_repo"
git -C "$mj4_repo" config commit.gpgsign false
git -C "$mj4_repo" config tag.gpgsign false
git -C "$mj4_repo" -c user.email=t@t -c user.name=t commit -q --allow-empty -m init
s_mj4="$tmp/mj4_stable.sh"
mk_stable "$s_mj4"
( cd "$mj4_repo" && "$engine" -n 3 --artifact-dir logs -- "$s_mj4" > "$tmp/mj4_out.txt" 2>&1 )
mj4_rc=$?
mj4_out="$(cat "$tmp/mj4_out.txt")"
check_eq "$mj4_rc" 2 'M4a: a FRESH, non-gitignored --artifact-dir inside the stamped repo is refused before the sweep begins'
check_contains "$mj4_out" 'RESULT: ERROR' 'M4b: ...verdict ERROR, a usage fault'
check_absent "$mj4_out" 'subject=MOVED' 'M4c: ...never reaches a MOVED verdict at all -- refused up front instead'

# M4a2: the repo root itself (`.`) is refused too -- a real call shape, kept even though it is
# confounded with item 12's non-empty check (both independently refuse it).
( cd "$mj4_repo" && "$engine" -n 3 --artifact-dir . -- "$s_mj4" > "$tmp/mj4root_out.txt" 2>&1 )
mj4root_rc=$?
check_eq "$mj4root_rc" 2 'M4a2: --artifact-dir . (the repo root itself) is also refused before the sweep begins'

# M4d-f: the escape hatch -- a GITIGNORED directory inside the repo is provably invisible to
# `git status` and must still be usable.
mj4b_repo="$tmp/mj4brepo"
mkdir -p "$mj4b_repo"
git init -q "$mj4b_repo"
git -C "$mj4b_repo" config commit.gpgsign false
git -C "$mj4b_repo" config tag.gpgsign false
printf 'arts/\n' > "$mj4b_repo/.gitignore"
git -C "$mj4b_repo" add .gitignore
git -C "$mj4b_repo" -c user.email=t@t -c user.name=t commit -q -m init
s_mj4b="$tmp/mj4b_stable.sh"
mk_stable "$s_mj4b"
( cd "$mj4b_repo" && "$engine" -n 3 --artifact-dir arts -- "$s_mj4b" > "$tmp/mj4b_out.txt" 2>&1 )
mj4b_rc=$?
mj4b_out="$(cat "$tmp/mj4b_out.txt")"
check_eq "$mj4b_rc" 0 'M4d: a gitignored --artifact-dir inside the repo is allowed'
check_contains "$mj4b_out" 'RESULT: PASS' 'M4e: ...and the sweep runs to completion'
check_contains "$mj4b_out" 'subject=stable' 'M4f: ...correctly reporting subject=stable, not MOVED'

# =========================================== item R5: the subject's output never round-trips a
# =========================================== path (T1: owned capture)
#
# The sweep's one forbidden output is a FALSE CLEAN -- `RESULT: PASS` over a sweep that did not
# measure what it claims. Four prior rounds each shipped a new one because a verdict input was
# routed through a path neither this process nor the operator exclusively owns. This section
# closes the last two: the per-run log, and bash's own herestring (`<<<`), which bash backs with a
# real temp file -- always on bash 3.2, and on 5.x once the string exceeds the pipe buffer.

# R5a-d: --artifact-dir is READ-ONLY. Pre-fix, the subject's own stdout redirection into a file
# inside that directory fails, so bash never even exec's the subject -- the resulting CONSTANT
# rc=1 reads as a clean, unvarying PASS despite the subject never having run once. Owned capture
# never routes the subject's stdout through --artifact-dir, so the subject runs normally on every
# iteration and its own genuinely alternating exit code becomes real, measured variance.
r5abcd_dir="$tmp/ro5"
mkdir -p "$r5abcd_dir"
chmod 500 "$r5abcd_dir"
s_r5a="$tmp/r5a_rc_alt.sh"
mk_rc_alternating "$s_r5a"
c_r5a="$tmp/c_r5a"
rm -f "$c_r5a"
( cd "$quiet_dir" && "$engine" -n 4 --artifact-dir "$r5abcd_dir" -- "$s_r5a" "$c_r5a" > "$tmp/r5abcd_out.txt" 2>&1 )
r5abcd_out="$(cat "$tmp/r5abcd_out.txt")"
chmod 700 "$r5abcd_dir" 2> /dev/null
check_contains "$r5abcd_out" 'RESULT: FAIL' \
  'R5a: a read-only --artifact-dir no longer makes the subject silently never run'
check_contains "$r5abcd_out" 'varying=1' \
  'R5b: ...the alternating exit code is measured as real variance'
check_eq "$(cat "$c_r5a" 2> /dev/null)" '4' \
  'R5c: ...and the subject'"'"'s own counter proves it actually ran all 4 times'
check_contains "$r5abcd_out" 'ARTIFACTS:' \
  'R5d: ...and the unwritable artifact dir is reported, never silently absorbed'

# R5e: control -- a writable --artifact-dir with a deterministic subject prints no ARTIFACTS: line
# at all (nothing failed to write). -n 4, not the suite's usual 3/5/6, so R5j below can count its
# progress lines without a second sweep.
r5e_dir="$tmp/r5e_art"
s_r5e="$tmp/r5e_stable.sh"
mk_stable "$s_r5e"
( cd "$quiet_dir" && "$engine" -n 4 --artifact-dir "$r5e_dir" -- "$s_r5e" > "$tmp/r5e_out.txt" 2>&1 )
r5e_out="$(cat "$tmp/r5e_out.txt")"
check_absent "$r5e_out" 'ARTIFACTS:' \
  'R5e: control -- a healthy --artifact-dir never prints ARTIFACTS:'

# R5j: every progress line now carries bytes=<k>, the byte count of the subject's own captured
# output. Reuses R5e's run (4 progress lines).
check_eq "$(grep -cE '^run [0-9]+/4 rc=[0-9]+ bytes=[0-9]+$' "$tmp/r5e_out.txt")" '4' \
  'R5j: every progress line carries bytes=, computed from the owned capture'

# R5f/g: the subject unlinks the sweep's OWN per-run log by PATH, right after printing a row that
# flips. Pre-fix, this deletes the file the extraction awk was about to read, and the lost row
# reads as rows=0 -- a false clean. The log is now a write-only side copy nothing reads back, so
# deleting it by path changes nothing about what was measured.
mk_r5fg() { cat > "$1" <<'SUBJ'
#!/usr/bin/env bash
c="$1"
n=$(( $(cat "$c" 2>/dev/null || echo 0) + 1 )); echo "$n" > "$c"
if [ "$n" = 1 ]; then printf 'FAIL  alpha\n'; else printf 'PASS  alpha\n'; fi
rm -f "$ART"/run-*.log 2>/dev/null
SUBJ
chmod +x "$1"; }

s_r5fg="$tmp/r5fg.sh"
mk_r5fg "$s_r5fg"
c_r5fg="$tmp/c_r5fg"
rm -f "$c_r5fg"
r5fg_art="$tmp/r5fg_art"
( cd "$quiet_dir" && ART="$r5fg_art" "$engine" -n 4 --artifact-dir "$r5fg_art" -- "$s_r5fg" "$c_r5fg" > "$tmp/r5fg_out.txt" 2>&1 )
r5fg_out="$(cat "$tmp/r5fg_out.txt")"
check_contains "$r5fg_out" 'RESULT: FAIL' \
  'R5f: unlinking the per-run log by path no longer erases a real flip'
check_contains "$r5fg_out" 'rows=1' \
  'R5g (rows): ...the one label is still counted'
check_contains "$r5fg_out" 'varying=1' \
  'R5g (varying): ...and still reported as varying'

# R5h/i: the realistic shape -- a stable, row-bearing subject that ALSO wipes its own $TMPDIR
# (where flake-sweep.sh's default --artifact-dir AND the owned-capture files both live) on every
# run, and bumps a counter kept OUTSIDE that TMPDIR. Pre-fix this reads FAIL varying=1 rows=0 for
# the WRONG reason (runs 2-4 never execute at all, since the subject's own stdout redirection into
# the now-missing artifact dir fails) with the counter stuck at 1. Owned capture keeps the subject
# running on every iteration regardless of what happens to --artifact-dir or $TMPDIR.
mk_r5h() { cat > "$1" <<'SUBJ'
#!/usr/bin/env bash
counter="$1"
n=$(( $(cat "$counter" 2>/dev/null || echo 0) + 1 )); echo "$n" > "$counter"
printf 'PASS  row one\n'
printf 'PASS one-space row\n'
printf 'SKIP  row three\n'
rm -rf "$TMPDIR"/* 2>/dev/null
SUBJ
chmod +x "$1"; }

s_r5h="$tmp/r5h_subj.sh"
mk_r5h "$s_r5h"
c_r5h="$tmp/c_r5h"
rm -f "$c_r5h"
t5h_dir="$tmp/t5h/"
mkdir -p "$t5h_dir"
( cd "$quiet_dir" && TMPDIR="$t5h_dir" "$engine" -n 4 -- "$s_r5h" "$c_r5h" > "$tmp/r5h_out.txt" 2>&1 )
r5h_out="$(cat "$tmp/r5h_out.txt")"
check_contains "$r5h_out" 'RESULT: PASS' \
  'R5h (result): a subject wiping its own TMPDIR every run no longer stops runs 2-N from executing'
check_contains "$r5h_out" 'rows=3' \
  'R5h (rows): ...all 3 rows still extracted from the owned capture, not the wiped artifact dir'
check_eq "$(cat "$c_r5h" 2> /dev/null)" '4' \
  'R5i: ...and the subject'"'"'s own counter (kept outside TMPDIR) proves all 4 runs executed'

# R5k: (Fable review) truncate-by-path -- the subject prints a real flip on "row two", THEN tries
# to blank the capture file by GUESSING its name pattern. Neither before nor after this task can
# the attempt reach anything: pre-fix there is no such file to find (nothing has ever created
# flake-sweep-cap.*), and post-fix the file is unlinked before the subject ever starts, so no path
# names it any more. The ONLY row pinning "no path reaches the capture" directly: deleting an
# already-open file changes nothing, and neither does truncating it by a guessed path.
mk_r5k() { cat > "$1" <<'SUBJ'
#!/usr/bin/env bash
c="$1"
n=$(( $(cat "$c" 2>/dev/null || echo 0) + 1 )); echo "$n" > "$c"
printf 'PASS  row one\n'
if [ "$n" = 1 ]; then printf 'FAIL  row two\n'; else printf 'PASS  row two\n'; fi
for f in "$TMPDIR"/flake-sweep-cap.*; do [ -e "$f" ] && : > "$f"; done 2>/dev/null
SUBJ
chmod +x "$1"; }

s_r5k="$tmp/r5k_subj.sh"
mk_r5k "$s_r5k"
c_r5k="$tmp/c_r5k"
rm -f "$c_r5k"
t5k_dir="$tmp/t5k/"
mkdir -p "$t5k_dir"
( cd "$quiet_dir" && TMPDIR="$t5k_dir" "$engine" -n 4 -- "$s_r5k" "$c_r5k" > "$tmp/r5k_out.txt" 2>&1 )
r5k_out="$(cat "$tmp/r5k_out.txt")"
check_contains "$r5k_out" 'RESULT: FAIL' \
  'R5k (result): a real flip survives a subject that guesses the capture file'"'"'s path'
check_contains "$r5k_out" 'rows=2' \
  'R5k (rows): ...both labels counted'
check_contains "$r5k_out" 'varying=1' \
  'R5k (varying): ...and exactly the one that flips is named varying'

# R5l: (Fable review) the not-executed branch -- TMPDIR points at a directory that does not
# exist, so the per-run capture file can never be created. Pre-fix there is no such branch at all
# (TMPDIR plays no role in the log-based capture), so this row is RED for a structural reason, not
# a false-clean one: the tip simply runs the subject normally 3 times regardless of TMPDIR.
art5l_dir="$tmp/art5l"
s_r5l="$tmp/r5l_subj.sh"
mk_stable "$s_r5l"
( cd "$quiet_dir" && TMPDIR="$tmp/does-not-exist/" "$engine" -n 3 --artifact-dir "$art5l_dir" -- "$s_r5l" > "$tmp/r5l_out.txt" 2>&1 )
r5l_out="$(cat "$tmp/r5l_out.txt")"
check_contains "$r5l_out" 'RESULT: INCOMPLETE' \
  'R5l (result): a TMPDIR that cannot hold a capture file makes every run "not executed"'
check_contains "$r5l_out" 'runs=0/3' \
  'R5l (runs): ...none of the 3 planned runs are counted as completed'
check_eq "$(grep -c 'not executed' "$tmp/r5l_out.txt")" '3' \
  'R5l (count): ...and each of the 3 says so explicitly'

# R5m: (Fable review) fd isolation -- the subject tries to write directly to fd 7 (the capture's
# write side, closed for it) on its first run only. This row is GREEN before AND after this task
# (pre-fix, fd 7 is not open at all either) -- its job is to kill a mutant that leaves fd 7 open
# for the subject, not to move from RED to GREEN.
mk_r5m() { cat > "$1" <<'SUBJ'
#!/usr/bin/env bash
c="$1"
n=$(( $(cat "$c" 2>/dev/null || echo 0) + 1 )); echo "$n" > "$c"
printf 'PASS  ok\n'
[ "$n" = 1 ] && echo 'FAIL  leak' >&7 2>/dev/null
exit 0
SUBJ
chmod +x "$1"; }

s_r5m="$tmp/r5m_subj.sh"
mk_r5m "$s_r5m"
c_r5m="$tmp/c_r5m"
rm -f "$c_r5m"
run_in "$quiet_dir" -n 3 -- "$s_r5m" "$c_r5m"
check_contains "$OUT" 'RESULT: PASS' \
  'R5m (result): a subject that cannot open fd 7 cannot leak a fake row into the capture'
check_contains "$OUT" 'rows=1' \
  'R5m (rows): ...only the one real row is ever counted'
check_contains "$OUT" 'varying=0' \
  'R5m (varying): ...and no variance is manufactured by the failed leak'

# R5n: extraction failure is never clean. A shim directory holding a fake `awk` goes FIRST on
# PATH for this one engine invocation: it exits 2 whenever its arguments include `-v run=` -- the
# per-run label awk's own signature -- and otherwise execs the REAL awk, resolved via
# `command -v awk` BEFORE the PATH change (so the shim can never recursively invoke itself).
# run-long.sh's own `--stamp` awk call never passes `-v run=`, so it is unaffected -- pinned below
# by `subject=stable`, never `subject=UNVERIFIABLE`. Pre-fix, the tip reads the resulting empty
# extraction as "no rows this run" and reports a clean PASS having never actually measured
# anything.
r5n_shim_dir="$tmp/r5n_shim"
mkdir -p "$r5n_shim_dir"
r5n_real_awk="$(command -v awk)"
cat > "$r5n_shim_dir/awk" <<SHIM
#!/usr/bin/env bash
prev=""
for a in "\$@"; do
  case "\$a" in
    run=*)
      [ "\$prev" = "-v" ] && exit 2
      ;;
  esac
  prev="\$a"
done
exec "$r5n_real_awk" "\$@"
SHIM
chmod +x "$r5n_shim_dir/awk"

s_r5n="$tmp/r5n_stable.sh"
mk_stable "$s_r5n"
( cd "$quiet_dir" && PATH="$r5n_shim_dir:$PATH" "$engine" -n 3 -- "$s_r5n" > "$tmp/r5n_out.txt" 2>&1 )
r5n_out="$(cat "$tmp/r5n_out.txt")"
check_contains "$r5n_out" 'RESULT: INDETERMINATE' \
  'R5n (result): a broken awk on PATH is reported, never read as a clean rowless PASS'
check_contains "$r5n_out" 'extraction(s) failed' \
  'R5n (message): ...and the diagnostic names what happened'
check_contains "$r5n_out" 'subject=stable' \
  'R5n (subject): ...run-long.sh'"'"'s own --stamp awk call (no -v run=) is unaffected by the shim'

# ============================================== fix round 1: the other three awk extractions were
# ============================================== never rc-checked (C1, C2, I1), plus M1 and M3
#
# R5n above only ever proved the PER-RUN label awk's own failure is caught. Three more awk calls
# compute verdict inputs the exact same way (piped from an in-memory string, captured via `$( )`)
# and, until this round, none of their exit statuses were read at all -- an awk shim that breaks
# any ONE of them reproduces R5n's false clean at that specific call site. Each shim below keys on
# an argument (or a substring of the awk PROGRAM text itself, when the call passes no `-v`) that is
# unique to its one target call, confirmed by inspection against every other awk invocation in
# flake-sweep.sh, so `run-long.sh --stamp` and the other three awk calls are unaffected in each row
# (pinned by `subject=stable`, never `subject=UNVERIFIABLE`).

# R5o: the AGGREGATION awk (`agg_out`) -- keys on `runs_str=`, the aggregation's own `-v` variable
# name, which no other awk call in the file passes (the per-run label awk passes `-v run=`, a
# different name; the others pass no `-v` naming a variable at all).
r5o_shim_dir="$tmp/r5o_shim"
mkdir -p "$r5o_shim_dir"
r5o_real_awk="$(command -v awk)"
cat > "$r5o_shim_dir/awk" <<SHIM
#!/usr/bin/env bash
for a in "\$@"; do
  case "\$a" in
    runs_str=*) exit 2 ;;
  esac
done
exec "$r5o_real_awk" "\$@"
SHIM
chmod +x "$r5o_shim_dir/awk"

mk_r5o() { cat > "$1" <<'SUBJ'
#!/usr/bin/env bash
c="$1"
n=$(( $(cat "$c" 2>/dev/null || echo 0) + 1 )); echo "$n" > "$c"
if [ "$n" = 1 ]; then printf 'FAIL  alpha\n'; else printf 'PASS  alpha\n'; fi
SUBJ
chmod +x "$1"; }

s_r5o="$tmp/r5o_subj.sh"
mk_r5o "$s_r5o"
c_r5o="$tmp/c_r5o"
rm -f "$c_r5o"
( cd "$quiet_dir" && PATH="$r5o_shim_dir:$PATH" "$engine" -n 3 -- "$s_r5o" "$c_r5o" > "$tmp/r5o_out.txt" 2>&1 )
r5o_out="$(cat "$tmp/r5o_out.txt")"
check_contains "$r5o_out" 'RESULT: INDETERMINATE' \
  'R5o (result): a broken AGGREGATION awk is reported, never read as a clean rows=0 PASS'
check_contains "$r5o_out" 'extraction(s) failed' \
  'R5o (message): ...and the diagnostic names what happened'
check_contains "$r5o_out" 'subject=stable' \
  'R5o (subject): ...run-long.sh'"'"'s own --stamp awk call is unaffected by the shim'

# R5p: the DISTINCT-EXIT-CODE awk (`distinct`) -- this call passes no `-v` at all, so it keys on
# `print c + 0`, a substring of its own PROGRAM text that appears in no other awk call in the file
# (the aggregation and merged-label programs print different tagged lines; the per-run label awk
# prints `L\t...`/`R\t...`; floor_pct prints a bare percentage).
r5p_shim_dir="$tmp/r5p_shim"
mkdir -p "$r5p_shim_dir"
r5p_real_awk="$(command -v awk)"
cat > "$r5p_shim_dir/awk" <<SHIM
#!/usr/bin/env bash
for a in "\$@"; do
  case "\$a" in
    *"print c + 0"*) exit 2 ;;
  esac
done
exec "$r5p_real_awk" "\$@"
SHIM
chmod +x "$r5p_shim_dir/awk"

s_r5p="$tmp/r5p_subj.sh"
mk_rc_alternating "$s_r5p"
c_r5p="$tmp/c_r5p"
rm -f "$c_r5p"
( cd "$quiet_dir" && PATH="$r5p_shim_dir:$PATH" "$engine" -n 4 -- "$s_r5p" "$c_r5p" > "$tmp/r5p_out.txt" 2>&1 )
r5p_out="$(cat "$tmp/r5p_out.txt")"
check_contains "$r5p_out" 'RESULT: INDETERMINATE' \
  'R5p (result): a broken DISTINCT-EXIT-CODE awk is reported, never read as a clean varying=0 PASS'
check_contains "$r5p_out" 'extraction(s) failed' \
  'R5p (message): ...and the diagnostic names what happened'
check_contains "$r5p_out" 'subject=stable' \
  'R5p (subject): ...run-long.sh'"'"'s own --stamp awk call is unaffected by the shim'

# R5q: the MERGED-LABEL awk (`merged_out`) -- also passes no `-v`, so it keys on `cnt[label]++`, a
# substring found only in this program (the aggregation awk uses `pk`/`fk`/`sk`/`order`, never
# `cnt`). Reuses `mk_merge` (defined earlier in this file for item M1) -- two raw labels
# ("db connect (primary)"/"db connect (replica)") that strip to one key every run. Pre-fix (before
# this round), a broken merged-label extraction only drops the `MERGED:` annotation -- a visibility
# qualifier, never a verdict input on its own -- so this row reads a plain PASS pre-fix, same as
# every OTHER row in this file that is GREEN throughout; confirmed below, not assumed.
s_r5q="$tmp/r5q_merge.sh"
mk_merge "$s_r5q"
c_r5q="$tmp/c_r5q"
rm -f "$c_r5q"
r5q_shim_dir="$tmp/r5q_shim"
mkdir -p "$r5q_shim_dir"
r5q_real_awk="$(command -v awk)"
cat > "$r5q_shim_dir/awk" <<SHIM
#!/usr/bin/env bash
for a in "\$@"; do
  case "\$a" in
    *"cnt[label]++"*) exit 2 ;;
  esac
done
exec "$r5q_real_awk" "\$@"
SHIM
chmod +x "$r5q_shim_dir/awk"
( cd "$quiet_dir" && PATH="$r5q_shim_dir:$PATH" "$engine" -n 4 -- "$s_r5q" "$c_r5q" > "$tmp/r5q_out.txt" 2>&1 )
r5q_out="$(cat "$tmp/r5q_out.txt")"
check_contains "$r5q_out" 'RESULT: INDETERMINATE' \
  'R5q (result): a broken MERGED-LABEL awk is reported, never silently absorbed into a plain PASS'
check_contains "$r5q_out" 'extraction(s) failed' \
  'R5q (message): ...and the diagnostic names what happened'
check_contains "$r5q_out" 'subject=stable' \
  'R5q (subject): ...run-long.sh'"'"'s own --stamp awk call is unaffected by the shim'

# M1: pin the `ran=0` branch directly -- a `mktemp` shim SUCCEEDS (prints a path, exits 0) but the
# path's directory does not exist, so the brace group's OWN `7>`/`8<` redirection fails to OPEN it.
# Distinct from R5l (mktemp itself fails to CREATE anything, caught one branch earlier): this
# exercises the SECOND `not executed` message. An explicit --artifact-dir keeps the shim from also
# intercepting the top-level `mktemp -d` used for the DEFAULT artifact dir, so it only ever fires
# for the per-run capture call. Also pins that the raw `bash: ... No such file or directory` this
# failed redirection prints is now suppressed (round 1's stderr-ordering fix), without hiding a
# subject's own real stderr (R5m above already proves a subject's stderr still reaches the
# capture; this row only needs to prove the bash-level message itself is gone).
mk_m1_marker() { cat > "$1" <<'SUBJ'
#!/usr/bin/env bash
: > "$1"
printf 'PASS  ok\n'
SUBJ
chmod +x "$1"; }

m1_shim_dir="$tmp/m1_shim"
mkdir -p "$m1_shim_dir"
m1_fake_dir="$tmp/m1_does_not_exist"
cat > "$m1_shim_dir/mktemp" <<SHIM
#!/usr/bin/env bash
printf '%s\n' "$m1_fake_dir/flake-sweep-cap.XXXXXX"
exit 0
SHIM
chmod +x "$m1_shim_dir/mktemp"

s_m1="$tmp/m1_subj.sh"
mk_m1_marker "$s_m1"
m1_marker="$tmp/m1_marker"
rm -f "$m1_marker"
m1_art_dir="$tmp/m1_art"
( cd "$quiet_dir" && PATH="$m1_shim_dir:$PATH" "$engine" -n 3 --artifact-dir "$m1_art_dir" -- "$s_m1" "$m1_marker" > "$tmp/m1_out.txt" 2>&1 )
m1_out="$(cat "$tmp/m1_out.txt")"
check_contains "$m1_out" 'RESULT: INCOMPLETE' \
  'M1 (result): a capture path whose directory vanished is "not executed", not a crash or a false PASS'
check_contains "$m1_out" 'runs=0/3' \
  'M1 (runs): ...none of the 3 planned runs are counted as completed'
check_eq "$(grep -c 'not executed (could not open the private capture file)' "$tmp/m1_out.txt")" '3' \
  'M1 (message): ...and each of the 3 names the OPEN failure specifically, not the CREATE one'
check_eq "$([[ -e "$m1_marker" ]] && echo yes || echo no)" 'no' \
  'M1 (counter): ...and the subject itself never ran -- its own marker was never created'
check_absent "$m1_out" 'No such file or directory' \
  'M1 (stderr): ...and the raw bash redirection-failure message is suppressed'

# ------------------------------------------ F1: repo configuration must not hide a moving subject
#
# run-long.sh's stamp hashed `git diff HEAD` + `git status --porcelain`, both of which honour repo
# config that hides changes. Measured before the fix: G (`status.showUntrackedFiles=no`, a subject
# leaking a new untracked file every run) and C1 (a committed `.gitmodules` with
# `submodule.vendor.ignore=dirty`, a subject rewriting a tracked file inside `vendor/` every run)
# both reported `RESULT: PASS ... subject=stable`. Each has a no-op control in the SAME repo, so
# the MOVED verdict is shown to come from the subject, not from the fixture's configuration. The
# subjects and their counters live outside the stamped repos; signing is off in every repo.
f1_commit() { # DIR MSG
  git -C "$1" -c user.email=t@t -c user.name=t -c commit.gpgsign=false -c tag.gpgsign=false \
    commit -q -m "$2"
}

f1g_repo="$tmp/f1g_repo"
mk_skip_repo "$f1g_repo"
git -C "$f1g_repo" config status.showUntrackedFiles no
s_f1g="$tmp/f1g_leak.sh"
cat > "$s_f1g" <<'SUBJ'
#!/usr/bin/env bash
c="$1"; n=$(( $(cat "$c" 2>/dev/null || echo 0) + 1 )); echo "$n" > "$c"
: > "leak.$n.tmp"
printf 'PASS  leaky row\n'
SUBJ
chmod +x "$s_f1g"
c_f1g="$tmp/f1g_counter"
rm -f "$c_f1g"
run_in "$f1g_repo" -n 3 -- true
check_contains "$OUT" 'RESULT: PASS' 'F1g-ctl: a no-op subject in the showUntrackedFiles=no repo PASSes (control)'
check_contains "$OUT" 'subject=stable' 'F1g-ctl: ...subject=stable'
run_in "$f1g_repo" -n 4 -- "$s_f1g" "$c_f1g"
check_eq "$RC" 1 'F1g: a subject leaking a new untracked file every run exits 1 under status.showUntrackedFiles=no'
check_contains "$OUT" 'RESULT: FAIL' 'F1g: ...verdict FAIL'
check_contains "$OUT" 'subject=MOVED' 'F1g: ...subject=MOVED -- the repo config did not hide the leak'

f1c_src="$tmp/f1c_src"
mk_skip_repo "$f1c_src"
printf 'v1\n' > "$f1c_src/lib.txt"
git -C "$f1c_src" add lib.txt && f1_commit "$f1c_src" lib
f1c_repo="$tmp/f1c_repo"
mk_skip_repo "$f1c_repo"
git -C "$f1c_repo" -c protocol.file.allow=always submodule --quiet add "$f1c_src" vendor > /dev/null 2>&1
git -C "$f1c_repo" config --file .gitmodules submodule.vendor.ignore dirty
git -C "$f1c_repo" add .gitmodules && f1_commit "$f1c_repo" 'vendor, ignore=dirty'
s_f1c="$tmp/f1c_rewrite.sh"
cat > "$s_f1c" <<'SUBJ'
#!/usr/bin/env bash
c="$1"; n=$(( $(cat "$c" 2>/dev/null || echo 0) + 1 )); echo "$n" > "$c"
printf 'rewrite %s\n' "$n" > vendor/lib.txt
printf 'PASS  vendored row\n'
SUBJ
chmod +x "$s_f1c"
c_f1c="$tmp/f1c_counter"
rm -f "$c_f1c"
run_in "$f1c_repo" -n 3 -- true
check_contains "$OUT" 'RESULT: PASS' 'F1c-ctl: a no-op subject in the ignore=dirty superproject PASSes (control)'
check_contains "$OUT" 'subject=stable' 'F1c-ctl: ...subject=stable'
run_in "$f1c_repo" -n 4 -- "$s_f1c" "$c_f1c"
check_eq "$RC" 1 'F1c: a subject rewriting a tracked file inside an ignore=dirty submodule exits 1'
check_contains "$OUT" 'RESULT: FAIL' 'F1c: ...verdict FAIL'
check_contains "$OUT" 'subject=MOVED' 'F1c: ...subject=MOVED -- submodule.vendor.ignore=dirty did not hide the edit'

# F1t (fix wave 2): a stamp whose git command FAILS must reach the sweep as UNVERIFIABLE, never as
# a constant digest. An ignore=all submodule whose .git/modules dir is gone made the 991fb57
# stamp's `git diff --submodule=diff` die at `vendor`; measured on 991fb57, the truncated output hashed to
# the same stamp every time, so a subject editing zz.txt (sorting after vendor) every run read
# `RESULT: PASS ... subject=stable`.
f1t_repo="$tmp/f1t_repo"
mk_skip_repo "$f1t_repo"
git -C "$f1t_repo" -c protocol.file.allow=always submodule --quiet add "$f1c_src" vendor > /dev/null 2>&1
printf 'z\n' > "$f1t_repo/zz.txt"
git -C "$f1t_repo" add zz.txt
git -C "$f1t_repo" config --file .gitmodules submodule.vendor.ignore all
git -C "$f1t_repo" add .gitmodules && f1_commit "$f1t_repo" 'vendor ignore=all, zz'
rm -rf "$f1t_repo/.git/modules/vendor"
s_f1t="$tmp/f1t_edit.sh"
cat > "$s_f1t" <<'SUBJ'
#!/usr/bin/env bash
c="$1"; n=$(( $(cat "$c" 2>/dev/null || echo 0) + 1 )); echo "$n" > "$c"
printf 'edit %s\n' "$n" > zz.txt
printf 'PASS  zz row\n'
SUBJ
chmod +x "$s_f1t"
c_f1t="$tmp/f1t_counter"
rm -f "$c_f1t"
run_in "$f1t_repo" -n 3 -- "$s_f1t" "$c_f1t"
check_eq "$RC" 1 'F1t: a stamp whose git command fails makes the sweep exit 1, never a clean PASS'
check_contains "$OUT" 'RESULT: FAIL' 'F1t: ...verdict FAIL'
check_contains "$OUT" 'subject=UNVERIFIABLE' 'F1t: ...subject=UNVERIFIABLE -- a failed stamp is not a stable one'

# -------------------------------------------- F2: a builtin subject must not run in the engine
#
# The subject used to run as a bare `"$@"` inside the engine's own brace group, so a shell builtin
# given as the command executed IN the engine's shell. Measured before the fix: a sourced file
# assigning `n=2` cut a planned `-n 6` sweep to `runs=2/2` and PASSed; `exec true` replaced the
# engine and `exit 0` ended it, each printing no RESULT line and exiting 0. Every fixture here is
# bounded -- a regression shortens the sweep or drops the RESULT line, it cannot loop.
f2_src="$tmp/f2_sourced.sh"
cat > "$f2_src" <<'SUBJ'
n=2
printf 'PASS  sourced row\n'
SUBJ
run_in "$quiet_dir" -n 6 -- source "$f2_src"
check_contains "$OUT" 'RESULT: PASS' 'F2a: a sourced subject that assigns n still yields a PASS verdict'
check_contains "$OUT" 'runs=6/6' 'F2a: ...over all 6 planned runs -- the subject cannot rewrite the run count'

run_in "$quiet_dir" -n 3 -- exec true
check_contains "$OUT" 'RESULT: ' 'F2b: exec as the subject does not replace the engine -- a RESULT line exists'
check_contains "$OUT" 'runs=3/3' 'F2b: ...runs=3/3'

run_in "$quiet_dir" -n 3 -- exit 0
check_contains "$OUT" 'RESULT: ' 'F2c: exit as the subject does not end the engine -- a RESULT line exists'
check_contains "$OUT" 'runs=3/3' 'F2c: ...runs=3/3'

printf '\n%d passed, %d failed\n' "$pass" "$fail"
[[ "$fail" -eq 0 ]]

#!/usr/bin/env bash
set -uo pipefail

# Script: test_heavy_gate.sh
# Purpose: Hermetic regression suite for scripts/heavy-gate.sh, the global turn-taking and thermal gate
# Usage: HG_ONLY="<row ids>" ./scripts/tests/test_heavy_gate.sh
#
# HG_ONLY is a space-separated list of row ids (unset runs every row). Every state directory is a
# subdirectory of a mktemp directory; nothing here reads, writes or joins the default gate directory.

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HG="$here/../heavy-gate.sh"

# `pwd -P` pins the PHYSICAL path: $TMPDIR is a symlink on macOS (/tmp -> /private/tmp).
tmp_raw="$(mktemp -d)"
if [ -z "$tmp_raw" ] || [ ! -d "$tmp_raw" ]; then
  printf 'test_heavy_gate: mktemp -d gave no directory\n' >&2
  exit 2
fi
tmp="$(cd "$tmp_raw" && pwd -P)"
token="${tmp##*/}"
# The token names this run's processes: nothing is killed whose command line lacks it.
# shellcheck disable=SC2329 # invoked by the EXIT trap
cleanup() {
  local p
  for p in $(pgrep -f "$token" 2>/dev/null); do
    if [ "$p" != "$$" ]; then kill -KILL "$p" 2>/dev/null; fi
  done
  rm -rf "$tmp"
}
trap cleanup EXIT
export TMPDIR="$tmp/tmpdir"
mkdir -p "$TMPDIR"

# An inherited seam must never reach a row: rows set exactly the seams they mean to test.
unset GATE_DIR GATE_POLL GATE_FAKE_THROTTLE GATE_FAKE_THROTTLE_FILE GATE_BEAT_TICK GATE_GRACE \
  GATE_RUN_STALE GATE_TICKET_TTL GATE_LOCK_TTL GATE_ORPHAN_LOCK_TTL GATE_LIB_ONLY STUB_LOG

# R0b starts an inner run and marks it with the outer run directory; a stray exported HG_INNER names
# no such directory, so it cannot make a full run skip R0b. Read the marker once, then drop it.
inner=0
if [ -n "${HG_INNER:-}" ] && [ -e "${HG_INNER}/inner-marker" ]; then inner=1; fi
unset HG_INNER

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

# check_range GOT LOW HIGH LABEL -- LOW <= GOT < HIGH, GOT all digits.
check_range() {
  if [[ "$1" =~ ^[0-9]+$ ]] && [[ "$1" -ge "$2" ]] && [[ "$1" -lt "$3" ]]; then
    pass_line "$4"
  else
    fail_line "$4 (got [$1], want $2 to $3)"
  fi
}

# g runs the script with GATE_DIR required and captures G_OUT, G_ERR, G_RC.
G_OUT=""
G_ERR=""
G_RC=0
g() {
  if [ -z "${GATE_DIR:-}" ]; then
    printf 'g: refused, GATE_DIR is not set\n' >&2
    G_OUT=""
    G_ERR=""
    G_RC=97
    return 97
  fi
  G_OUT=$(bash "$HG" "$@" 2>"$tmp/g.err")
  G_RC=$?
  G_ERR=$(<"$tmp/g.err")
  return 0
}

# g_default runs the script with GATE_DIR unset, for the rows that exercise the default path.
# Those rows put a stub `id` first on PATH, so the default path they reach is claude-99999.
g_default() {
  G_OUT=$(env -u GATE_DIR -u GATE_LIB_ONLY bash "$HG" "$@" 2>"$tmp/g.err")
  G_RC=$?
  G_ERR=$(<"$tmp/g.err")
}

new_state() { # NAME -- a fresh state directory under $tmp, exported as GATE_DIR
  mkdir -p "$tmp/$1"
  GATE_DIR="$tmp/$1"
  export GATE_DIR
}

touch_ago() { # FILE SECS -- set FILE's mtime SECS seconds into the past
  local t
  t=$(($(date +%s) - $2))
  touch -d "@$t" "$1" 2>/dev/null || touch -t "$(date -r "$t" '+%Y%m%d%H%M.%S')" "$1"
}

# field KEY -- the value of the first `KEY: value` line of G_OUT.
field() {
  printf '%s\n' "$G_OUT" | awk -v k="$1:" 'index($0, k) == 1 { print substr($0, length(k) + 2); exit }'
}

# pval KEY TEXT -- KEY's value from the `params:` line of TEXT.
pval() {
  printf '%s\n' "$2" | awk -v k="$1" '/^params:/ { for (i = 2; i <= NF; i++) { split($i, a, "="); if (a[1] == k) print a[2] } }'
}

# mk_probe_stubs DIR CPU GPU IO PMLIMIT -- stub sysctl and pmset; "-" means no reading from that source.
mk_probe_stubs() {
  local d=$1 cpu=$2 gpu=$3 io=$4 pm=$5
  mkdir -p "$d"
  cat >"$d/sysctl" <<EOF
#!/bin/sh
[ -n "\${STUB_LOG:-}" ] && echo "sysctl \$*" >> "\$STUB_LOG"
case "\$2" in
  machdep.xcpm.cpu_thermal_level) v='$cpu' ;;
  machdep.xcpm.gpu_thermal_level) v='$gpu' ;;
  machdep.xcpm.io_thermal_level) v='$io' ;;
  *) v=- ;;
esac
[ "\$v" = - ] && exit 1
echo "\$v"
EOF
  cat >"$d/pmset" <<EOF
#!/bin/sh
[ '$pm' = - ] && exit 1
echo "Note: No thermal warning level has been recorded"
echo "CPU_Scheduler_Limit = 50"
echo "CPU_Available_CPUs = 16"
echo "CPU_Speed_Limit = $pm"
EOF
  chmod +x "$d/sysctl" "$d/pmset"
}

# probe_status NAME CPU GPU IO PMLIMIT -- run `status` through those stubs; sets G_OUT.
probe_status() {
  local name=$1 oldpath=$PATH
  new_state "$name"
  mk_probe_stubs "$tmp/stubs-$name" "$2" "$3" "$4" "$5"
  PATH="$tmp/stubs-$name:$oldpath"
  g status
  PATH=$oldpath
}

# The standard timing seams most rows pass (a row that needs another value passes it after these).
STD_SEAMS=(GATE_POLL=1 GATE_BEAT_TICK=1 GATE_GRACE=2 GATE_RUN_STALE=4 GATE_FAKE_THROTTLE=0)

# gate runs the script like g, with the standard seams unless the call already set them.
gate() {
  GATE_POLL="${GATE_POLL:-1}" GATE_BEAT_TICK="${GATE_BEAT_TICK:-1}" GATE_GRACE="${GATE_GRACE:-2}" \
    GATE_RUN_STALE="${GATE_RUN_STALE:-4}" GATE_FAKE_THROTTLE="${GATE_FAKE_THROTTLE:-0}" g "$@"
}

# lib runs CODE with the script sourced (GATE_DIR is already set); extra seams come from the LIBENV array.
LIBENV=()
LIB_OUT=""
lib() { # CODE ARGS...
  local code=$1
  shift
  LIB_OUT=$(env GATE_LIB_ONLY=1 "${STD_SEAMS[@]}" "${LIBENV[@]+"${LIBENV[@]}"}" bash -c ". \"\$1\"; shift; $code" _ "$HG" "$@" 2>&1 </dev/null)
}

# run_bounded runs CMD with its own watchdog and sets RB_OUT, RB_ERR, RB_RC (124 when the watchdog fired).
RB_OUT=""
RB_ERR=""
RB_RC=0
run_bounded() { # SECS CMD...
  local secs=$1 rcf="$tmp/rb.rc" pid i=0 limit kid
  shift
  limit=$((secs * 5))
  rm -f "$rcf"
  (
    "$@" >"$tmp/rb.out" 2>"$tmp/rb.err"
    printf '%s\n' "$?" >"$rcf.part"
    mv "$rcf.part" "$rcf"
  ) &
  pid=$!
  while [ ! -e "$rcf" ] && [ "$i" -lt "$limit" ]; do
    sleep 0.2
    i=$((i + 1))
  done
  if [ -e "$rcf" ]; then
    RB_RC=$(<"$rcf")
  else
    for kid in $(pgrep -P "$pid" 2>/dev/null); do kill -TERM "$kid" 2>/dev/null; done
    kill -TERM "$pid" 2>/dev/null
    RB_RC=124
  fi
  wait "$pid" 2>/dev/null
  RB_OUT=$(<"$tmp/rb.out")
  RB_ERR=$(<"$tmp/rb.err")
}

# wait_for SECS CONDITION-COMMAND... -- poll until the command succeeds or SECS pass.
wait_for() {
  local secs=$1 i=0 limit
  shift
  limit=$((secs * 5))
  while [ "$i" -lt "$limit" ]; do
    if "$@" >/dev/null 2>&1; then return 0; fi
    sleep 0.2
    i=$((i + 1))
  done
  "$@" >/dev/null 2>&1
}

# alive_count TOKEN -- how many processes carry TOKEN in their command line.
alive_count() { pgrep -f "$1" 2>/dev/null | wc -l | tr -d ' '; }

# start_leader CMD... -- start CMD as the leader of its own process group, with job control toggled in
# THIS shell (never a subshell, which would reparent the target to pid 1); the pid is left in $leader.
leader=""
start_leader() {
  set -m
  "$@" &
  leader=$!
  set +m
}

pgid_of() { ps -o pgid= -p "$1" 2>/dev/null | tr -d ' '; }

# start_sentinel LABEL -- a live process in its own group, carrying the run token; pid in $leader.
start_sentinel() {
  start_leader bash -c 'sleep 25; :' "${token}-$1"
}

# stop_sentinel PID -- stop a sentinel started by start_sentinel and reap it.
stop_sentinel() {
  if [ "$(pgid_of "$1")" = "$1" ]; then kill -TERM -- "-$1" 2>/dev/null; else kill -TERM "$1" 2>/dev/null; fi
  wait "$1" 2>/dev/null
}

owner_name_of() { # STATE-DIR -- field 1 of that directory's lock/owner (empty when there is none)
  local o=""
  IFS= read -r o 2>/dev/null <"$1/lock/owner" || true
  printf '%s\n' "${o%% *}"
}

# has_text HAYSTACK NEEDLE -- prints yes or no (a fixed-string containment test)
has_text() {
  case "$1" in
    *"$2"*) printf 'yes\n' ;;
    *) printf 'no\n' ;;
  esac
}

nm() { printf '%s-%s' "$1" "$token"; }

now_ms() {
  local v
  v=$(date +%s%N 2>/dev/null)
  case "$v" in
    '' | *[!0-9]*) printf '%s\n' $(($(date +%s) * 1000)) ;;
    *) printf '%s\n' $((v / 1000000)) ;;
  esac
}

inode_of() { stat -c %i "$1" 2>/dev/null || stat -f %i "$1"; }

# snap_files DIR -- every path under DIR with a digest of each regular file.
snap_files() {
  local f
  find "$1" | LC_ALL=C sort | while IFS= read -r f; do
    if [ -f "$f" ]; then printf '%s %s\n' "$f" "$(shasum -a 256 <"$f")"; else printf '%s\n' "$f"; fi
  done
}

# snap_meta DIR -- inode and mtime of every path under DIR.
snap_meta() {
  local f
  find "$1" | LC_ALL=C sort | while IFS= read -r f; do
    printf '%s %s\n' "$(stat -c '%i %Y' "$f" 2>/dev/null || stat -f '%i %m' "$f")" "$f"
  done
}

mk_ticket() { # NAME EPOCH CONTENT AGE -- fixture ticket queue/EPOCH-NAME, back-dated AGE seconds (0 leaves it fresh)
  local f="$GATE_DIR/queue/$2-$1"
  mkdir -p "$GATE_DIR/queue"
  if [ -n "$3" ]; then printf '%s\n' "$3" >"$f"; else : >"$f"; fi
  if [ "$4" -gt 0 ]; then touch_ago "$f" "$4"; fi
}

mk_lock() { # NAME MODE AGE [PID] -- fixture lock whose owner line is NAME EPOCH PID MODE, back-dated AGE seconds
  mkdir -p "$GATE_DIR/lock"
  printf '%s %s %s %s\n' "$1" "$(date +%s)" "${4:-4242}" "$2" >"$GATE_DIR/lock/owner"
  if [ "$3" -gt 0 ]; then touch_ago "$GATE_DIR/lock/owner" "$3"; fi
}

has_ticket() { # NAME -- prints yes or no
  local t b
  for t in "$GATE_DIR"/queue/*; do
    if [ -f "$t" ]; then
      b=${t##*/}
      if [ "${b#*-}" = "$1" ]; then
        printf 'yes\n'
        return 0
      fi
    fi
  done
  printf 'no\n'
}

ticket_count() { # prints how many files sit in the queue
  local t n=0
  for t in "$GATE_DIR"/queue/*; do
    if [ -f "$t" ]; then n=$((n + 1)); fi
  done
  printf '%s\n' "$n"
}

exists_word() { if [ -e "$1" ]; then printf 'present\n'; else printf 'gone\n'; fi; }

events_has() { # PATTERN -- prints yes or no (a fixed string against the whole events file)
  if [ -f "$GATE_DIR/events" ] && grep -qF -- "$1" "$GATE_DIR/events"; then printf 'yes\n'; else printf 'no\n'; fi
}

# usage_free ERR -- prints ok when ERR is a message and not the usage text.
usage_free() {
  case "$1" in
    '') printf 'empty\n' ;;
    *Usage:*) printf 'usage-text\n' ;;
    *) printf 'ok\n' ;;
  esac
}

# mk_mv_shim DIR -- a mv first on PATH that, once, swaps a different LIVE lock in just before the real mv runs.
mk_mv_shim() {
  mkdir -p "$1"
  cat >"$1/mv" <<'EOF'
#!/bin/sh
if [ ! -e "$SHIM_FLAG" ]; then
  : >"$SHIM_FLAG"
  rm -rf "$GATE_DIR/lock"
  mkdir "$GATE_DIR/lock"
  printf 'live %s 4242 run\n' "$(date +%s)" >"$GATE_DIR/lock/owner"
fi
exec /bin/mv "$@"
EOF
  chmod +x "$1/mv"
}

# write_proto -- the frozen copy of the prototype gate, byte for byte, in $tmp/proto-gate.sh; run only with GATE_DIR set.
PROTO_LINES=126
PROTO_SHA=fc091d5ce0d53c455bfd6139d5c97d4a8d6121581b37b5d6dcb79ba3240638f8
write_proto() {
  cat >"$tmp/proto-gate.sh" <<'PROTO'
#!/usr/bin/env bash
set -euo pipefail
# Shared turn-taking gate for CPU-heavy work on this Mac (all Claude sessions use it; it lives outside every repo).
#   gate.sh throttled          exit 0 if the Mac is thermally throttled right now, 1 if not
#   gate.sh join NAME          take a ticket in the queue (idempotent)
#   gate.sh wait NAME          block until NAME is the oldest ticket AND the Mac is not throttled, then take the
#                              lock and print "GO NAME"; the caller MUST run `gate.sh release NAME` afterwards
#   gate.sh run NAME -- CMD..  wait for the turn, run CMD in the foreground with a heartbeat, always release
#   gate.sh release NAME       give up the lock (if NAME holds it) and the ticket
#   gate.sh status             print the queue, the lock owner and the throttle reading
# Throttle test (no privileges): machdep.xcpm cpu/gpu/io thermal level > 0, or the last CPU_Speed_Limit < 100.
# Mutual exclusion: `mkdir` of the lock directory is atomic, so two sessions can never both take it.
# Fairness: tickets are ordered by join time; only the oldest ticket may take the lock.
# Stale entries: a ticket nobody has touched for 10 minutes and a lock whose owner has not heartbeat for 45 minutes
# are removed, so a crashed session cannot block the others.
# Test seams (never set in real use): GATE_DIR, GATE_FAKE_THROTTLE (1 = throttled, 0 = not), GATE_POLL (seconds).

D="${GATE_DIR:-/private/tmp/claude-501/heavy-gate}"
Q="$D/queue"
L="$D/lock"
POLL="${GATE_POLL:-20}"
mkdir -p "$Q"

throttled() {
  if [ -n "${GATE_FAKE_THROTTLE:-}" ]; then [ "$GATE_FAKE_THROTTLE" = "1" ]; return; fi
  local lv v lim
  for v in cpu gpu io; do
    lv="$(sysctl -n "machdep.xcpm.${v}_thermal_level" 2>/dev/null || echo 0)"
    case "$lv" in '' | *[!0-9]*) lv=0 ;; esac
    if [ "$lv" -gt 0 ]; then return 0; fi
  done
  lim="$(pmset -g therm 2>/dev/null | awk '/CPU_Speed_Limit/ { v = $NF } END { print v }' || true)"
  case "$lim" in '' | *[!0-9]*) return 1 ;; esac
  [ "$lim" -lt 100 ]
}

# Ticket file names (join-epoch-NAME), oldest first. find, not ls, so odd names cannot confuse it.
tickets() { find "$Q" -maxdepth 1 -type f 2>/dev/null | sed 's#.*/##' | sort || true; }

ticket_of() {
  local name="$1" t s
  while IFS= read -r t; do
    s="${t#*-}"
    if [ "$s" = "$name" ]; then printf '%s\n' "$t"; return 0; fi
  done < <(tickets)
  return 0
}

join() {
  local name="$1" t
  t="$(ticket_of "$name")"
  if [ -z "$t" ]; then : >"$Q/$(date +%s)-$name"; else touch "$Q/$t"; fi
}

prune() {
  find "$Q" -type f -mmin +10 -delete 2>/dev/null || true
  if [ -d "$L" ] && [ -f "$L/owner" ] && [ -n "$(find "$L/owner" -mmin +45 2>/dev/null || true)" ]; then rm -rf "$L"; fi
}

head_name() {
  local t
  t="$(tickets | head -n 1 || true)"
  printf '%s\n' "${t#*-}"
}

release() {
  local name="$1" t owner
  if [ -f "$L/owner" ]; then
    owner="$(awk 'NR==1 { print $1 }' "$L/owner" || true)"
    if [ "$owner" = "$name" ]; then rm -rf "$L"; fi
  fi
  t="$(ticket_of "$name")"
  if [ -n "$t" ]; then rm -f "$Q/$t"; fi
}

wait_turn() {
  local name="$1"
  join "$name"
  while :; do
    join "$name"
    prune
    if [ "$(head_name)" = "$name" ] && ! throttled; then
      if mkdir "$L" 2>/dev/null; then
        printf '%s %s\n' "$name" "$(date +%s)" >"$L/owner"
        echo "GO $name"
        return 0
      fi
    fi
    sleep "$POLL"
  done
}

status() {
  echo "queue:"
  tickets | sed 's/^/  /'
  if [ -f "$L/owner" ]; then echo "lock: $(cat "$L/owner")"; else echo "lock: free"; fi
  if throttled; then echo "throttle: THROTTLED"; else echo "throttle: not throttled"; fi
}

cmd="${1:-}"
case "$cmd" in
throttled) throttled ;;
join) join "${2:?name required}" ;;
wait) wait_turn "${2:?name required}" ;;
release) release "${2:?name required}" ;;
status) status ;;
run)
  name="${2:?name required}"
  shift 2
  if [ "${1:-}" != "--" ]; then echo "usage: gate.sh run NAME -- CMD..." >&2; exit 2; fi
  shift
  wait_turn "$name" >&2
  hb=""
  trap 'if [ -n "$hb" ]; then kill "$hb" 2>/dev/null || true; fi; release "$name"' EXIT
  trap 'exit 143' TERM INT HUP
  ( while :; do sleep 60; touch "$L/owner" 2>/dev/null || true; join "$name"; done ) &
  hb=$!
  rc=0
  "$@" || rc=$?
  exit "$rc"
  ;;
*)
  echo "usage: gate.sh throttled | join NAME | wait NAME | run NAME -- CMD... | release NAME | status" >&2
  exit 2
  ;;
esac
PROTO
}

# proto ARGS... -- run the frozen prototype against the fixture directory in GATE_DIR (refuses without one).
PR_RC=0
proto() {
  case "${GATE_DIR:-}" in
    "$tmp"/*) ;;
    *)
      printf 'proto: refused, GATE_DIR is not a fixture under the run directory\n' >&2
      PR_RC=97
      return 97
      ;;
  esac
  env GATE_POLL=1 GATE_FAKE_THROTTLE=0 bash "$tmp/proto-gate.sh" "$@" >/dev/null 2>&1
  PR_RC=$?
}

row_R0() {
  local out rc res=ok
  out=$(
    unset GATE_DIR
    g status 2>&1
  )
  rc=$?
  if [ "$rc" != 97 ]; then res="rc=$rc"; fi
  case "$out" in *'GATE_DIR is not set'*) ;; *) res="$res+no-refusal-text" ;; esac
  case "$out" in *'queue:'*) res="$res+script-ran" ;; esac
  check_eq "$res" ok "R0/g-refuses-unset-gate-dir"
}

row_R0b() {
  local inner res=ok
  : >"$tmp/inner-marker"
  inner=$(HG_INNER="$tmp" HG_ONLY=R0 bash "${BASH_SOURCE[0]}" 2>&1)
  case "$inner" in *'PASS  R0/g-refuses-unset-gate-dir'*) ;; *) res=selected-row-missing ;; esac
  case "$inner" in *'R0b/'* | *'R1/'* | *'R2/'* | *'R2b/'*) res="$res+ran-other-rows" ;; esac
  check_eq "$res" ok "R0b/dispatcher-honours-hg-only"
}

row_R1() {
  local thr
  new_state r1
  GATE_FAKE_THROTTLE=1 g throttled
  check_eq "rc=$G_RC err=[$G_ERR]" "rc=0 err=[]" "R1/fake1-rc0"
  GATE_FAKE_THROTTLE=0 g throttled
  thr="rc=$G_RC"
  GATE_FAKE_THROTTLE=0 g status
  check_eq "$thr $(field throttle)" "rc=1 not throttled" "R1/fake0-rc1"
}

row_R2() {
  probe_status r2-cpu 12 0 0 100
  check_eq "$(field throttle)" "THROTTLED" "R2/cpu-alone"
  probe_status r2-gpu 0 12 0 100
  check_eq "$(field throttle)" "THROTTLED" "R2/gpu-alone"
  probe_status r2-io 0 0 12 100
  check_eq "$(field throttle)" "THROTTLED" "R2/io-alone"
  probe_status r2-pm80 0 0 0 80
  check_eq "$(field throttle)" "THROTTLED" "R2/pmset-80"
  probe_status r2-pm100 0 0 0 100
  check_eq "$(field throttle)" "not throttled" "R2/pmset-100-stale"
  probe_status r2-abc abc - - -
  check_contains "rc=$G_RC $(field throttle)" "rc=0 unmeasurable (" "R2/nonnumeric-level"
  probe_status r2-none - - - -
  check_contains "rc=$G_RC $(field throttle)" "rc=0 unmeasurable (" "R2/all-missing-unmeasurable"
}

row_R2b() {
  local a b sa sb res
  probe_status r2b-sysctl 0 0 0 100
  a=$G_OUT
  sa=$(field source)
  check_eq "$sa" "sysctl" "R2b/sysctl-source"
  probe_status r2b-pmset - - - 100
  b=$G_OUT
  sb=$(field source)
  check_eq "$(field throttle)|$sb" "not throttled|pmset-only" "R2b/pmset-only-source"
  if [ -n "$sa" ] && [ -n "$sb" ] && [ "$sa" != "$sb" ] && [ "$a" != "$b" ]; then res=differ; else res="same-or-empty"; fi
  check_eq "$res" "differ" "R2b/pmset-only-word-differs"
  probe_status r2b-none - - - -
  check_eq "$(field source)" "none" "R2b/none-source"
  new_state r2b-fake
  GATE_FAKE_THROTTLE=0 g status
  check_eq "$(field source)|$(field seam)" "fake|fake" "R2b/fake-source"
}

row_R9a() {
  local d="$tmp/r9a" f a
  mkdir -p "$d"
  f="$d/probe"
  : >"$f"
  touch_ago "$f" 30
  a=$(GATE_DIR="$d" GATE_LIB_ONLY=1 bash -c '. "$1" && age_of "$2"' _ "$HG" "$f" 2>&1)
  check_range "$a" 29 32 "R9a/age-30s-within-1"
  touch "$f"
  a=$(GATE_DIR="$d" GATE_LIB_ONLY=1 bash -c '. "$1" && age_of "$2"' _ "$HG" "$f" 2>&1)
  check_range "$a" 0 3 "R9a/age-fresh-under-3"
}

row_R14e() {
  local sd="$tmp/stubs-r14e" log="$tmp/r14e.log" oldpath=$PATH pos absent never="$tmp/r14e-never" res
  mk_probe_stubs "$sd" 0 0 0 -
  printf '#!/bin/sh\necho 99999\n' >"$sd/id"
  chmod +x "$sd/id"
  : >"$log"
  export STUB_LOG="$log"
  PATH="$sd:$oldpath"

  GATE_FAKE_THROTTLE=1 g_default status
  case "$G_OUT" in *'queue: (no state directory)'*) pos=ok ;; *) pos=missing ;; esac
  check_eq "$pos" "ok" "R14e/status-no-dir-text"

  GATE_FAKE_THROTTLE=1 g_default throttled
  GATE_FAKE_THROTTLE=1 g_default status
  case "$G_OUT" in *'queue: (no state directory)'*) pos=ok ;; *) pos=missing ;; esac
  if [ ! -e /private/tmp/claude-99999 ] && [ ! -e /tmp/claude-99999 ]; then absent=absent; else absent=present; fi
  check_eq "$pos|$absent" "ok|absent" "R14e/default-path-not-created"

  : >"$log"
  GATE_FAKE_THROTTLE=1 g_default status
  case "$G_OUT" in *'queue: (no state directory)'*) res=status-text ;; *) res=status-text-missing ;; esac
  if [ -s "$log" ]; then res="$res+probe-ran"; else res="$res+probe-silent"; fi
  case "$(field source)" in sysctl) res="$res+real-source" ;; *) res="$res+source=$(field source)" ;; esac
  check_eq "$res" "status-text+probe-ran+real-source" "R14e/fake-ignored-without-gate-dir"

  GATE_DIR="$never" g status
  case "$G_OUT" in *'queue: (no state directory)'*) pos=ok ;; *) pos=missing ;; esac
  GATE_DIR="$never" g throttled
  if [ ! -e "$never" ]; then absent=absent; else absent=present; fi
  check_eq "$pos|$absent" "ok|absent" "R14e/gate-dir-nonexistent-stays-nonexistent"

  PATH=$oldpath
  unset STUB_LOG
}

row_R8d1() {
  new_state r8d1
  g --help
  check_contains "$G_OUT" "takes its stage with it within GATE_BEAT_TICK" "R8d1/sigkill-tick-plus-grace"
  check_contains "$G_OUT" "handed on after GATE_RUN_STALE plus one tick plus one second plus one GATE_POLL" "R8d1/hand-on-cost-sentence"
  check_contains "$G_OUT" "75 means this session did not get a turn: report it, do not work around it" "R8d1/exit-75"
  check_contains "$G_OUT" "2 usage, validation or state-directory problem" "R8d1/exit-2"
  check_contains "$G_OUT" "HEAVY_GATE_DONE <NAME> rc=<n>" "R8d1/verdict-line"
  check_contains "$G_OUT" "only the stage's process group is stopped" "R8d1/process-group-limit"
  check_contains "$G_OUT" "a convention, not enforcement" "R8d1/convention-not-enforcement"
}

row_R8d2() {
  local st h p rs tk po want
  new_state r8d2
  g status
  st=$G_OUT
  check_eq "$(field params)" "tick=5 grace=10 run_stale=60 ticket_ttl=600 lock_ttl=2700 orphan_lock_ttl=120 poll=20" "R8d2/params-defaults"
  g --help
  h=$(printf '%s\n' "$G_OUT" | sed -n 's/.*GATE_BEAT_TICK (default \([0-9][0-9]*\) s) plus GATE_GRACE (default \([0-9][0-9]*\) s).*/tick=\1 grace=\2/p' | sed -n 1p)
  p="tick=$(pval tick "$st") grace=$(pval grace "$st")"
  check_eq "$h" "$p" "R8d2/help-tick-grace-equal-params"
  h=$(printf '%s\n' "$G_OUT" | sed -n 's/.*about \([0-9][0-9]*\) s at the defaults.*/\1/p' | sed -n 1p)
  rs=$(pval run_stale "$st")
  tk=$(pval tick "$st")
  po=$(pval poll "$st")
  want=$((${rs:-0} + ${tk:-0} + 1 + ${po:-0}))
  check_eq "$h" "$want" "R8d2/help-hand-on-figure"
}

row_R3() {
  local k r won rounds_ok=0 results_ok=0 go="$tmp/r3.go" bad
  new_state r3
  for _ in $(seq 1 30); do
    rm -rf "$GATE_DIR/lock" "$go"
    for k in 1 2 3 4 5 6 7 8; do
      GATE_LIB_ONLY=1 bash -c '. "$1"; while [ ! -e "$3" ]; do :; done; if take_lock "$2" wait; then echo won; else echo lost; fi' _ "$HG" "r3-$k-$token" "$go" >"$tmp/r3.$k" 2>"$tmp/r3.$k.err" &
    done
    sleep 0.3
    : >"$go"
    wait
    won=0
    bad=0
    for k in 1 2 3 4 5 6 7 8; do
      r=$(<"$tmp/r3.$k")
      case "$r" in
        won) won=$((won + 1)) ;;
        lost) ;;
        *) bad=$((bad + 1)) ;;
      esac
      if [ -s "$tmp/r3.$k.err" ]; then bad=$((bad + 1)); fi
      if [ "$bad" = 0 ]; then results_ok=$((results_ok + 1)); fi
      bad=0
    done
    if [ "$won" = 1 ]; then rounds_ok=$((rounds_ok + 1)); fi
  done
  check_eq "$rounds_ok" 30 "R3/one-winner-per-round"
  check_eq "$results_ok" 240 "R3/all-rounds-ran"
}

row_R4h() {
  local a b c out="$tmp/r4h.out" pid early ctl seen
  a=$(nm a)
  b=$(nm b)
  c=$(nm c)
  new_state r4h
  gate join "$a"
  sleep 1
  gate join "$b"
  sleep 1
  gate join "$c"
  : >"$out"
  env "${STD_SEAMS[@]}" bash "$HG" wait "$c" >"$out" 2>"$tmp/r4h.err" &
  pid=$!
  sleep 3
  early=no-go
  seen=$(<"$out")
  if [ "$(has_text "$seen" GO)" = yes ]; then early=premature-go; fi
  gate release "$a"
  gate release "$b"
  wait_for 3 grep -q '^GO' "$out"
  ctl=$?
  check_eq "$early|control=$ctl" "no-go|control=0" "R4h/non-head-does-not-take-lock"
  kill -TERM "$pid" 2>/dev/null
  wait "$pid" 2>/dev/null
  gate release "$c"

  new_state r4h-oldest
  gate join "$a"
  sleep 1
  gate join "$b"
  sleep 1
  gate join "$c"
  run_bounded 3 env "${STD_SEAMS[@]}" bash "$HG" wait "$a"
  check_eq "rc=$RB_RC out=[$RB_OUT]" "rc=0 out=[GO $a]" "R4h/oldest-takes-lock-despite-newer-tickets"
}

row_R9() {
  local d ep got
  ep=$(date +%s)

  new_state r9-ticket
  d=$GATE_DIR
  mk_ticket old "$((ep - 20))" "" 10
  mk_ticket new "$ep" "" 0
  LIBENV=(GATE_TICKET_TTL=5)
  lib prune
  LIBENV=()
  check_eq "$(has_ticket old)" no "R9/ticket-stale-removed"
  check_eq "old=$(has_ticket old) new=$(has_ticket new)" "old=no new=yes" "R9/ticket-fresh-kept"

  new_state r9-lock-stale
  d=$GATE_DIR
  mk_lock lk wait 10
  LIBENV=(GATE_LOCK_TTL=5)
  lib prune
  LIBENV=()
  check_eq "$(exists_word "$d/lock")|$(events_has 'name=lk rule=c')" "gone|yes" "R9/wait-lock-stale-removed"

  new_state r9-lock-fresh
  d=$GATE_DIR
  mk_lock lk wait 0
  mk_ticket old "$((ep - 20))" "" 10
  LIBENV=(GATE_LOCK_TTL=5 GATE_TICKET_TTL=5)
  lib prune
  LIBENV=()
  got=$(<"$d/lock/owner")
  check_eq "old=$(has_ticket old) lock=$(exists_word "$d/lock") owner=${got%% *}" "old=no lock=present owner=lk" "R9/wait-lock-fresh-kept"

  new_state r9-trash-stale
  d=$GATE_DIR
  mkdir "$d/trash.old"
  touch_ago "$d/trash.old" 10
  LIBENV=(GATE_TICKET_TTL=5)
  lib prune
  LIBENV=()
  check_eq "$(exists_word "$d/trash.old")" gone "R9/trash-stale-swept"

  new_state r9-trash-fresh
  d=$GATE_DIR
  mkdir "$d/trash.old" "$d/trash.new"
  touch_ago "$d/trash.old" 10
  LIBENV=(GATE_TICKET_TTL=5)
  lib prune
  LIBENV=()
  check_eq "old=$(exists_word "$d/trash.old") new=$(exists_word "$d/trash.new")" "old=gone new=present" "R9/trash-fresh-kept"

  new_state r9-non-trash
  d=$GATE_DIR
  mkdir "$d/trash.old"
  touch_ago "$d/trash.old" 10
  printf 'keep\n' >"$d/notes.txt"
  touch_ago "$d/notes.txt" 10
  LIBENV=(GATE_TICKET_TTL=5)
  lib prune
  LIBENV=()
  check_eq "trash=$(exists_word "$d/trash.old") notes=$(exists_word "$d/notes.txt")" "trash=gone notes=present" "R9/non-trash-old-entries-kept"
}

row_R10() {
  local w
  w=$(nm w1)

  new_state r10-before
  mkdir "$GATE_DIR/lock"
  touch_ago "$GATE_DIR/lock" 3
  run_bounded 3 env "${STD_SEAMS[@]}" GATE_ORPHAN_LOCK_TTL=100 bash "$HG" wait "$w"
  check_eq "rc=$RB_RC out=[$RB_OUT] ticket=$(has_ticket "$w") lock=$(exists_word "$GATE_DIR/lock") owner=$(exists_word "$GATE_DIR/lock/owner")" \
    "rc=124 out=[] ticket=yes lock=present owner=gone" "R10/ownerless-not-taken-before-ttl"

  new_state r10-after
  mkdir "$GATE_DIR/lock"
  touch_ago "$GATE_DIR/lock" 10
  run_bounded 4 env "${STD_SEAMS[@]}" GATE_ORPHAN_LOCK_TTL=2 bash "$HG" wait "$w"
  check_eq "rc=$RB_RC out=[$RB_OUT] owner=$(owner_name_of "$GATE_DIR")" "rc=0 out=[GO $w] owner=$w" "R10/ownerless-taken-after-ttl"

  new_state r10-seam
  mkdir "$GATE_DIR/lock"
  touch_ago "$GATE_DIR/lock" 200
  run_bounded 3 env "${STD_SEAMS[@]}" GATE_ORPHAN_LOCK_TTL=1000 bash "$HG" wait "$w"
  check_eq "rc=$RB_RC out=[$RB_OUT] ticket=$(has_ticket "$w") lock=$(exists_word "$GATE_DIR/lock") owner=$(exists_word "$GATE_DIR/lock/owner")" \
    "rc=124 out=[] ticket=yes lock=present owner=gone" "R10/seam-above-constant-not-taken"
}

row_R11() {
  local a b c own ino shim="$tmp/r11-shim" d
  a=$(nm a)
  b=$(nm b)
  c=$(nm c)

  new_state r11-other
  d=$GATE_DIR
  run_bounded 3 env "${STD_SEAMS[@]}" bash "$HG" wait "$a"
  own=$(<"$d/lock/owner")
  ino=$(inode_of "$d/lock")
  gate join "$b"
  gate release "$b"
  check_eq "rc=$G_RC held-by=$(owner_name_of "$d") b-ticket=$(has_ticket "$b") owner-same=$([ -n "$own" ] && [ "$(<"$d/lock/owner")" = "$own" ] && echo yes || echo no)" \
    "rc=0 held-by=$a b-ticket=no owner-same=yes" "R11/release-other-leaves-lock"
  check_eq "held-by=$(owner_name_of "$d") b-ticket=$(has_ticket "$b") inode-same=$([ -n "$ino" ] && [ "$(inode_of "$d/lock")" = "$ino" ] && echo yes || echo no)" \
    "held-by=$a b-ticket=no inode-same=yes" "R11/foreign-lock-inode-stable"

  new_state r11-own
  d=$GATE_DIR
  run_bounded 3 env "${STD_SEAMS[@]}" bash "$HG" wait "$a"
  gate join "$c"
  gate release "$a"
  check_eq "lock=$(exists_word "$d/lock") a-ticket=$(has_ticket "$a") c-ticket=$(has_ticket "$c")" \
    "lock=gone a-ticket=no c-ticket=yes" "R11/release-own-frees-only-own"

  new_state r11-collision
  d=$GATE_DIR
  run_bounded 3 env "${STD_SEAMS[@]}" bash "$HG" wait "$a"
  mkdir -p "$d/lock.gone.4242" "$d/trash.fixture"
  printf '%s 1 1 wait\n' "$b" >"$d/lock.gone.4242/owner"
  gate join "$b"
  gate release "$b"
  check_eq "held-by=$(owner_name_of "$d") b-ticket=$(has_ticket "$b") gone-dir=$(exists_word "$d/lock.gone.4242") trash=$(exists_word "$d/trash.fixture")" \
    "held-by=$a b-ticket=no gone-dir=present trash=present" "R11/collision-no-effect"

  new_state r11-restore
  d=$GATE_DIR
  mk_lock old wait 10
  mk_mv_shim "$shim"
  LIBENV=(GATE_LOCK_TTL=5 "SHIM_FLAG=$tmp/r11-shim.flag")
  PATH="$shim:$PATH" lib prune
  LIBENV=()
  check_eq "shim-ran=$(exists_word "$tmp/r11-shim.flag") owner=$(owner_name_of "$d")" "shim-ran=present owner=live" "R11/replaced-lock-restored"
  check_eq "restored=$(events_has LOCK_RESTORED) restore-failed=$(events_has LOCK_RESTORE_FAILED) removed-event=$(events_has STALE_LOCK_REMOVED)" "restored=yes restore-failed=no removed-event=no" "R11/restored-event-logged"

  new_state r11-unwritable
  d=$GATE_DIR
  gate join "$a"
  chmod 500 "$d/queue"
  run_bounded 5 env "${STD_SEAMS[@]}" bash "$HG" release "$a"
  chmod 700 "$d/queue"
  check_eq "rc=$RB_RC ticket-still-there=$(has_ticket "$a")" "rc=0 ticket-still-there=yes" "R11/release-returns-on-unwritable-queue"
}

# stress_run N SECS -- one 20 s run: 4 waiters and a pruner loop on 1 to 2 s TTLs; sets ST_OV and ST_N.
ST_OV=0
ST_N=0
stress_run() {
  local n=$1 secs=$2 d end k f cs ov flag p
  new_state "r11s-$n"
  d=$GATE_DIR
  cs="$d.cs"
  ov="$tmp/r11s-$n.ov"
  flag="$tmp/r11s-$n.run"
  : >"$ov"
  : >"$flag"
  cat >"$tmp/r11s-waiter.sh" <<'EOF'
#!/usr/bin/env bash
# waiter HG NAME END CS OVERLAPS COUNT -- loops wait, a one-owner critical section, release (every 4th holder never releases)
hg=$1 name=$2 end=$3 cs=$4 ov=$5 cnt=$6
n=0
while [ "$(date +%s)" -lt "$end" ]; do
  out=$(bash "$hg" wait "$name" 2>/dev/null) || continue
  case "$out" in "GO "*) ;; *) continue ;; esac
  n=$((n + 1))
  if mkdir "$cs" 2>/dev/null; then
    sleep 0.2
    rmdir "$cs"
  else
    echo x >>"$ov"
    sleep 0.2
  fi
  if [ $((n % 4)) -ne 0 ]; then bash "$hg" release "$name" >/dev/null 2>&1; fi
done
echo "$n" >"$cnt"
EOF
  end=$(($(date +%s) + secs))
  for k in 1 2 3 4; do
    env "${STD_SEAMS[@]}" GATE_LOCK_TTL=2 GATE_TICKET_TTL=2 bash "$tmp/r11s-waiter.sh" "$HG" "s$k-$token" "$end" "$cs" "$ov" "$tmp/r11s-$n.cnt$k" &
  done
  (
    while [ -e "$flag" ]; do
      # shellcheck disable=SC2016 # the code runs in the child shell
      env GATE_LIB_ONLY=1 "${STD_SEAMS[@]}" GATE_LOCK_TTL=2 GATE_TICKET_TTL=2 bash -c '. "$1"; prune' "pruner-$token" "$HG"
      sleep 0.3
    done
  ) &
  p=$!
  sleep "$secs"
  if ! wait_for 40 all_exist "$tmp/r11s-$n.cnt1" "$tmp/r11s-$n.cnt2" "$tmp/r11s-$n.cnt3" "$tmp/r11s-$n.cnt4"; then
    # a waiter outlived its bound: stop the ones carrying this run's token so the final wait is bounded
    for k in $(pgrep -f "s[1-4]-$token" 2>/dev/null); do kill -KILL "$k" 2>/dev/null; done
  fi
  rm -f "$flag"
  wait
  ST_OV=$(wc -l <"$ov" | tr -d ' ')
  ST_N=0
  for k in 1 2 3 4; do
    f="$tmp/r11s-$n.cnt$k"
    if [ -f "$f" ]; then ST_N=$((ST_N + $(<"$f"))); fi
  done
  wait "$p" 2>/dev/null
}

# shellcheck disable=SC2329 # invoked by wait_for
all_exist() {
  local f
  for f in "$@"; do
    if [ ! -e "$f" ]; then return 1; fi
  done
  return 0
}

row_R11s() {
  local n tov=0 tn=0
  for n in 1 2 3; do
    stress_run "$n" 20
    check_eq "overlaps=$ST_OV acquisitions-seen=$([ "$ST_N" -gt 0 ] && echo yes || echo no)" "overlaps=0 acquisitions-seen=yes" "R11s/no-overlap-run$n"
    tov=$((tov + ST_OV))
    tn=$((tn + ST_N))
  done
  if [ "$tn" -gt 0 ]; then
    pass_line "R11s/overlap-rate-reported (overlap rate $tov/$tn across 3 runs)"
  else
    fail_line "R11s/overlap-rate-reported (no acquisitions were seen)"
  fi
}

row_R14a() {
  local name label
  new_state r14a-ok
  gate join a-b
  check_eq "rc=$G_RC ticket=$(has_ticket a-b)" "rc=0 ticket=yes" "R14a/dash-accepted"
  gate join proj-purpose-123
  check_eq "rc=$G_RC ticket=$(has_ticket proj-purpose-123)" "rc=0 ticket=yes" "R14a/convention-accepted"
  for label in empty-rc2 slash-rc2 space-rc2 newline-rc2 leading-dot-rc2; do
    case "$label" in
      empty-rc2) name='' ;;
      slash-rc2) name='a/b' ;;
      space-rc2) name='a b' ;;
      newline-rc2) name=$'a\nb' ;;
      leading-dot-rc2) name='.hidden' ;;
    esac
    new_state "r14a-$label"
    gate join "$name"
    check_eq "rc=$G_RC err=$(usage_free "$G_ERR") tickets=$(ticket_count)" "rc=2 err=ok tickets=0" "R14a/$label"
  done
}

row_R14b() {
  local n sp dp jrc
  n=$(nm held)

  new_state r14b-live
  start_sentinel r14b
  sp=$leader
  mk_ticket "$n" "$(date +%s)" "$sp" 0
  run_bounded 4 env "${STD_SEAMS[@]}" bash "$HG" wait "$n"
  check_eq "rc=$RB_RC err=$(usage_free "$RB_ERR") go=$(has_text "$RB_OUT" GO)" "rc=2 err=ok go=no" "R14b/live-holder-refused"
  check_contains "$RB_ERR" "$sp" "R14b/refusal-names-holder"
  # shellcheck disable=SC2016 # the code runs in the child shell
  run_bounded 4 env "${STD_SEAMS[@]}" GATE_RUN_STALE=600 bash "$HG" run "$n" -- bash -c ': >"$1"' "$token" "$tmp/r14b-ran"
  check_eq "rc=$RB_RC err=$(usage_free "$RB_ERR") names-holder=$(has_text "$RB_ERR" "$sp") stage=$(exists_word "$tmp/r14b-ran")" \
    "rc=2 err=ok names-holder=yes stage=gone" "R14b/run-live-holder-refused"
  stop_sentinel "$sp"

  new_state r14b-dead
  dp=$(bash -c 'echo $$')
  mk_ticket "$n" "$(date +%s)" "$dp" 0
  run_bounded 4 env "${STD_SEAMS[@]}" bash "$HG" wait "$n"
  check_eq "rc=$RB_RC out=[$RB_OUT]" "rc=0 out=[GO $n]" "R14b/dead-pid-taken-over"

  new_state r14b-empty
  mk_ticket "$n" "$(date +%s)" "" 0
  run_bounded 4 env "${STD_SEAMS[@]}" bash "$HG" wait "$n"
  check_eq "rc=$RB_RC out=[$RB_OUT]" "rc=0 out=[GO $n]" "R14b/empty-ticket-taken-over"

  new_state r14b-join
  gate join "$n"
  jrc=$G_RC
  run_bounded 4 env "${STD_SEAMS[@]}" bash "$HG" wait "$n"
  check_eq "join-rc=$jrc rc=$RB_RC out=[$RB_OUT]" "join-rc=0 rc=0 out=[GO $n]" "R14b/join-only-ticket-taken-over"
}

row_R14d() {
  local n
  n=$(nm w1)
  : >"$tmp/r14d-file"
  GATE_DIR="$tmp/r14d-file/sub"
  export GATE_DIR
  run_bounded 4 env "${STD_SEAMS[@]}" bash "$HG" wait "$n"
  check_eq "rc=$RB_RC err=$(usage_free "$RB_ERR") go=$(has_text "$RB_OUT" GO)" "rc=2 err=ok go=no" "R14d/wait-exit2"
  check_contains "$RB_ERR" "$tmp/r14d-file/sub" "R14d/wait-names-path"
  # shellcheck disable=SC2016 # the code runs in the child shell
  run_bounded 4 env "${STD_SEAMS[@]}" GATE_RUN_STALE=600 bash "$HG" run "$n" -- bash -c ': >"$1"' "$token" "$tmp/r14d-ran"
  check_eq "rc=$RB_RC err=$(usage_free "$RB_ERR")" "rc=2 err=ok" "R14d/run-exit2"
  check_eq "rc=$RB_RC err=$(usage_free "$RB_ERR") stage=$(exists_word "$tmp/r14d-ran")" "rc=2 err=ok stage=gone" "R14d/run-no-stage-ran"
}

row_R14f() {
  local d holder sp lst bf af bm am t0 t1 ev stale
  holder=$(nm hold)
  new_state r14f
  d=$GATE_DIR
  start_sentinel r14f
  sp=$leader
  # shellcheck disable=SC2016 # the code runs in the child shell
  lib 'lstart_of "$1"' "$sp"
  lst=$LIB_OUT
  mk_lock "$holder" run 75 "$sp"
  printf '%s %s\n' "$sp" "$lst" >"$d/lock/stage"
  mk_ticket "stale-$token" "$(date +%s)" "" 700
  mkdir "$d/trash.old"
  touch_ago "$d/trash.old" 700
  printf 'seed\n' >"$d/events"
  bf=$(snap_files "$d")
  bm=$(snap_meta "$d")
  t0=$(now_ms)
  gate status
  t1=$(now_ms)
  af=$(snap_files "$d")
  am=$(snap_meta "$d")
  ev=$(<"$d/events")
  stale=$(has_text "$G_OUT" 'stale by rule 1b')
  check_eq "$([ "$bf" = "$af" ] && echo same || echo changed)" same "R14f/files-unchanged"
  check_eq "$([ "$bm" = "$am" ] && echo same || echo changed)" same "R14f/inodes-mtimes-unchanged"
  check_eq "stale-reported=$stale alive=$(alive_count "${token}-r14f")" "stale-reported=yes alive=1" "R14f/sentinel-alive"
  check_eq "$ev" seed "R14f/events-unappended"
  check_eq "rc=$G_RC fast=$([ $((t1 - t0)) -lt 1000 ] && echo yes || echo no)" "rc=0 fast=yes" "R14f/returns-fast"
  check_contains "$G_OUT" "stale by rule 1b" "R14f/reports-stale-1b"
  touch "$d/lock/owner"
  gate status
  check_eq "first-reported=$stale second-stale=$(has_text "$G_OUT" 'stale by rule')" "first-reported=yes second-stale=no" "R14f/fresh-lock-not-reported-stale"
  stop_sentinel "$sp"
}

row_R15() {
  local lines sha n1 n2 pa pb out="$tmp/r15.out" pid early ctl before st seen
  write_proto
  lines=$(wc -l <"$tmp/proto-gate.sh" | tr -d ' ')
  sha=$(shasum -a 256 "$tmp/proto-gate.sh" | awk '{ print $1 }')
  check_eq "$lines $sha" "$PROTO_LINES $PROTO_SHA" "R15/proto-copy-intact"
  n1=$(nm n1)
  n2=$(nm n2)
  pa=$(nm pa)
  pb=$(nm pb)

  new_state r15-ticket
  proto join "$pa"
  sleep 1
  env "${STD_SEAMS[@]}" bash "$HG" wait "$pb" >"$out" 2>"$tmp/r15.err" &
  pid=$!
  sleep 3
  early=no-go
  seen=$(<"$out")
  if [ "$(has_text "$seen" GO)" = yes ]; then early=premature-go; fi
  proto release "$pa"
  wait_for 3 grep -q '^GO' "$out"
  ctl=$?
  check_eq "$early|control=$ctl" "no-go|control=0" "R15/proto-ticket-honoured"
  kill -TERM "$pid" 2>/dev/null
  wait "$pid" 2>/dev/null

  new_state r15-owner
  run_bounded 4 env GATE_POLL=1 GATE_FAKE_THROTTLE=0 bash "$tmp/proto-gate.sh" wait "$n1"
  touch_ago "$GATE_DIR/lock/owner" 30
  gate status
  before=$G_OUT
  GATE_LOCK_TTL=10 gate status
  st=$(has_text "$before" 'stale by rule')
  check_eq "proto-go=[$RB_OUT] early-stale=$st late-1c=$(has_text "$G_OUT" 'stale by rule 1c')" "proto-go=[GO $n1] early-stale=no late-1c=yes" "R15/two-field-owner-not-run-mode"

  new_state r15-release
  run_bounded 3 env "${STD_SEAMS[@]}" bash "$HG" wait "$n2"
  before=$(exists_word "$GATE_DIR/lock")
  proto release "$n2"
  check_eq "before=$before after=$(exists_word "$GATE_DIR/lock") rc=$PR_RC" "before=present after=gone rc=0" "R15/proto-release-frees-new-lock"

  new_state r15-field1
  run_bounded 3 env "${STD_SEAMS[@]}" bash "$HG" wait "$n1"
  before=$(awk 'NR == 1 { print $1 }' "$GATE_DIR/lock/owner")
  st=$(awk 'NR == 1 { print NF }' "$GATE_DIR/lock/owner")
  check_eq "field1=$before fields=$st" "field1=$n1 fields=4" "R15/new-owner-field1-intact"
}

row_R8f1() {
  local d holder w sp
  holder=$(nm holder)
  w=$(nm w1)
  new_state r8f1
  d=$GATE_DIR
  start_sentinel r8f1
  sp=$leader
  mk_lock "$holder" run 10 4242
  printf '%s %s\n' "$sp" "Thu Jan 1 00:00:00 2099" >"$d/lock/stage"
  mk_ticket "$holder" "$(($(date +%s) - 30))" 4242 0
  mk_ticket "$(nm other)" "$(($(date +%s) + 100))" 4242 0
  run_bounded 9 env "${STD_SEAMS[@]}" bash "$HG" wait "$w"
  check_eq "removed-event=$(events_has "name=$holder rule=b") holder-ticket=$(has_ticket "$holder") owner=$(owner_name_of "$d")" \
    "removed-event=yes holder-ticket=no owner=$w" "R8f1/lock-removed"
  check_eq "holder-ticket=$(has_ticket "$holder") other-ticket=$(has_ticket "$(nm other)")" "holder-ticket=no other-ticket=yes" "R8f1/ticket-kept-when-name-differs"
  check_eq "reused-event=$(events_has 'stage=reused') alive=$(alive_count "${token}-r8f1")" "reused-event=yes alive=1" "R8f1/unrelated-not-killed"
  stop_sentinel "$sp"
}

row_R8f2() {
  local d holder w lead pre grp
  holder=$(nm holder)
  w=$(nm w1)
  new_state r8f2
  d=$GATE_DIR
  # shellcheck disable=SC2016 # the code runs in the child shell
  start_leader bash -c 'bash -c "sleep 25; :" "$0" & wait' "${token}-r8f2"
  lead=$leader
  sleep 0.3
  grp=ok
  if [ "$(pgid_of "$lead")" = "$(pgid_of $$)" ]; then grp=shares-suite-group; fi
  if [ "$grp" = ok ]; then
    kill -KILL "$lead" 2>/dev/null
    wait "$lead" 2>/dev/null
  fi
  pre=gone
  if pgrep -g "$lead" >/dev/null 2>&1; then pre=member-alive; fi
  mk_lock "$holder" run 10 4242
  printf '%s %s\n' "$lead" "Thu Jan 1 00:00:00 2099" >"$d/lock/stage"
  if [ "$grp" = ok ]; then
    run_bounded 12 env "${STD_SEAMS[@]}" bash "$HG" wait "$w"
  fi
  check_eq "group=$grp before=$pre killed-event=$(events_has 'stage=killed') after=$(if pgrep -g "$lead" >/dev/null 2>&1; then echo member-alive; else echo gone; fi)" \
    "group=ok before=member-alive killed-event=yes after=gone" "R8f2/group-killed"
  if pgrep -g "$lead" >/dev/null 2>&1; then kill -KILL -- "-$lead" 2>/dev/null; fi
}

row_R8f3() {
  local d holder w sp wenv lst_a lst_b log="$tmp/r8f3.log" grp
  holder=$(nm holder)
  w=$(nm w1)
  wenv=(TZ=America/Chicago LC_ALL=fr_FR.UTF-8)
  if ! locale -a 2>/dev/null | grep -qi '^fr_FR\.utf-\{0,1\}8$'; then wenv=(TZ=America/Chicago); fi
  cat >"$tmp/r8f3-stage.sh" <<'EOF'
#!/usr/bin/env bash
# stage STATE-DIR LOG TOKEN -- records whether the lock still exists when TERM arrives
trap 'if [ -d "$1/lock" ]; then echo LOCK_PRESENT >"$2"; else echo LOCK_GONE >"$2"; fi; exit 0' TERM
sleep 25 &
wait
EOF
  new_state r8f3
  d=$GATE_DIR
  start_leader bash "$tmp/r8f3-stage.sh" "$d" "$log" "${token}-r8f3"
  sp=$leader
  sleep 0.3
  grp=ok
  if [ "$(pgid_of "$sp")" = "$(pgid_of $$)" ]; then grp=shares-suite-group; fi
  mk_lock "$holder" run 10 4242
  # shellcheck disable=SC2016 # the code runs in the child shell
  env "${wenv[@]}" GATE_LIB_ONLY=1 "${STD_SEAMS[@]}" bash -c '. "$1"; printf "%s %s\n" "$2" "$(lstart_of "$3")" >"$GATE_DIR/lock/stage"' _ "$HG" "$sp" "$sp"
  # shellcheck disable=SC2016 # the code runs in the child shell
  lst_a=$(env "${wenv[@]}" GATE_LIB_ONLY=1 "${STD_SEAMS[@]}" bash -c '. "$1"; lstart_of "$2"' _ "$HG" "$sp")
  # shellcheck disable=SC2016 # the code runs in the child shell
  lst_b=$(env TZ=UTC LC_ALL=C GATE_LIB_ONLY=1 "${STD_SEAMS[@]}" bash -c '. "$1"; lstart_of "$2"' _ "$HG" "$sp")
  if [ "$grp" = ok ]; then
    run_bounded 12 env TZ=UTC LC_ALL=C "${STD_SEAMS[@]}" bash "$HG" wait "$w"
  fi
  check_eq "group=$grp killed-event=$(events_has 'stage=killed') log=$(cat "$log" 2>/dev/null)" "group=ok killed-event=yes log=LOCK_PRESENT" "R8f3/killed-before-lock-removed"
  check_eq "$([ -n "$lst_a" ] && [ "$lst_a" = "$lst_b" ] && echo equal || echo differ)" equal "R8f3/lstart-equal-across-env"
  stop_sentinel "$sp"
}

# --- run rows ---
# Every run row sets GATE_RUN_STALE=600: the stale-lock rule is not what these rows test, and a queued
# waiter must never judge a healthy holder stale while a stage is still running.

# bg_gate OUT ERR ARGS... -- start the script in the background; the wrapper's own pid is left in $bg_pid
# (env execs bash, so $! is the wrapper). Extra environment comes from the BG_ENV array.
BG_ENV=()
bg_pid=""
bg_gate() {
  local out=$1 err=$2
  shift 2
  env "${STD_SEAMS[@]}" GATE_RUN_STALE=600 "${BG_ENV[@]+"${BG_ENV[@]}"}" bash "$HG" "$@" >"$out" 2>"$err" </dev/null &
  bg_pid=$!
}

# shellcheck disable=SC2329 # invoked by wait_for
pid_gone() { ! kill -0 "$1" 2>/dev/null; }

# shellcheck disable=SC2329 # invoked by wait_for
none_alive() { [ "$(alive_count "$1")" = 0 ]; }

# bg_finish PID SECS -- wait, bounded, for a background wrapper; BG_RC is its status (124 when it outlived SECS and was stopped).
BG_RC=0
bg_finish() {
  if wait_for "$2" pid_gone "$1"; then
    wait "$1" 2>/dev/null
    BG_RC=$?
  else
    kill -KILL "$1" 2>/dev/null
    wait "$1" 2>/dev/null
    BG_RC=124
  fi
}

# reap_row -- stop whatever carrying this run's token a row left behind (only a failing row leaves any).
reap_row() {
  local p
  for p in $(pgrep -f "$token" 2>/dev/null); do
    if [ "$p" != "$$" ]; then kill -KILL "$p" 2>/dev/null; fi
  done
}

last_line() { printf '%s\n' "$1" | tail -n 1; }

# state_shape -- the tickets, lock and trash entries of $GATE_DIR as one word.
state_shape() {
  local t n=0
  for t in "$GATE_DIR"/trash.*; do
    if [ -e "$t" ]; then n=$((n + 1)); fi
  done
  printf 'tickets=%s lock=%s trash=%s\n' "$(ticket_count)" "$(exists_word "$GATE_DIR/lock")" "$n"
}

row_R4() {
  local log="$tmp/r4.log" i pid pids=() rcs="" starts seq lines
  cat >"$tmp/r4-stage.sh" <<'EOF'
#!/usr/bin/env bash
# stage LOG LABEL TOKEN -- logs its start and its end around a short sleep, then exits 5
echo "start $2" >>"$1"
sleep 1
echo "end $2" >>"$1"
exit 5
EOF
  : >"$log"
  new_state r4
  for i in a b c d; do
    bg_gate "$tmp/r4.$i.out" "$tmp/r4.$i.err" run "$(nm "$i")" -- bash "$tmp/r4-stage.sh" "$log" "$i" "$token"
    pids+=("$bg_pid")
    sleep 1
  done
  for pid in "${pids[@]}"; do
    bg_finish "$pid" 30
    rcs="$rcs$BG_RC "
  done
  starts=$(awk '/^start/ { printf "%s ", $2 }' "$log")
  seq=$(awk '{ printf "%s%s ", $1, $2 }' "$log")
  lines=$(wc -l <"$log" | tr -d ' ')
  check_eq "$starts" "a b c d " "R4/start-order-equals-join-order"
  check_eq "$seq" "starta enda startb endb startc endc startd endd " "R4/no-overlap"
  check_eq "$rcs" "5 5 5 5 " "R4/all-rc-equal-stage-rc"
  check_eq "lines=$lines $(state_shape)" "lines=8 tickets=0 lock=gone trash=0" "R4/state-dir-clean"
  reap_row
}

row_R5() {
  local n out="$tmp/r5.out" err="$tmp/r5.err" t0 t1 before pid held pgid grp_before grp_after after recheck c
  n=$(nm w)
  new_state r5
  # shellcheck disable=SC2016 # the code runs in the child shell
  bg_gate "$out" "$err" run "$n" -- bash -c 'bash -c "sleep 30; :" "$0" & wait' "$token"
  pid=$bg_pid
  sleep 2
  held=$(owner_name_of "$GATE_DIR")
  before=$(alive_count "$token")
  pgid=$(awk 'NR == 1 { print $1 }' "$GATE_DIR/lock/stage" 2>/dev/null)
  grp_before=gone
  if [ -n "$pgid" ] && pgrep -g "$pgid" >/dev/null 2>&1; then grp_before=alive; fi
  t0=$(date +%s)
  kill -TERM "$pid" 2>/dev/null
  bg_finish "$pid" 12
  t1=$(date +%s)
  wait_for 3 none_alive "$token"
  after=$(alive_count "$token")
  grp_after=gone
  if [ -n "$pgid" ] && pgrep -g "$pgid" >/dev/null 2>&1; then grp_after=alive; fi
  check_eq "rc=$BG_RC fast=$([ $((t1 - t0)) -lt 7 ] && echo yes || echo no)" "rc=143 fast=yes" "R5/exit-143-promptly"
  check_eq "held=$([ "$held" = "$n" ] && echo yes || echo no) running=$([ "$before" -ge 3 ] && echo yes || echo no) group-before=$grp_before after=$after group-after=$grp_after" \
    "held=yes running=yes group-before=alive after=0 group-after=gone" "R5/no-survivors"
  check_eq "$(last_line "$(<"$err")")" "HEAVY_GATE_DONE $n rc=143" "R5/last-line-verdict-143"
  check_eq "rc=$BG_RC $(state_shape)" "rc=143 tickets=0 lock=gone trash=0" "R5/state-clean"
  reap_row

  # A stale run-mode lock (age 10 against GATE_RUN_STALE=4) puts the waiter into prune's re-check, a sleep of
  # GATE_BEAT_TICK + 1 = 6 s; TERM one second in must still end the wrapper within 2 s.
  new_state r5p
  mk_lock "$(nm holder)" run 10
  BG_ENV=(GATE_RUN_STALE=4 GATE_BEAT_TICK=5)
  bg_gate "$tmp/r5p.out" "$tmp/r5p.err" run "$n" -- bash -c ': ' "$token"
  BG_ENV=()
  pid=$bg_pid
  sleep 1
  recheck=no
  for c in $(pgrep -P "$pid" 2>/dev/null); do
    if [ "$(has_text "$(ps -o command= -p "$c" 2>/dev/null)" "sleep 6")" = yes ]; then recheck=yes; fi
  done
  t0=$(date +%s)
  kill -TERM "$pid" 2>/dev/null
  bg_finish "$pid" 12
  t1=$(date +%s)
  check_eq "rc=$BG_RC recheck=$recheck prompt=$([ $((t1 - t0)) -lt 2 ] && echo yes || echo no) verdict=$(has_text "$(last_line "$(<"$tmp/r5p.err")")" "HEAVY_GATE_DONE $n rc=143")" \
    "rc=143 recheck=yes prompt=yes verdict=yes" "R5/term-during-prune-prompt"
  reap_row
}

row_R6() {
  local rl="$here/../run-long.sh" n art mid st trailer revart oldpwd=$PWD pid job c
  n=$(nm w)
  if ! cd "$tmp"; then
    for c in lock-held-during-stage trailer-stage-rc expect-satisfied reverse-form-lock-free; do
      fail_line "R6/$c (could not enter the run directory)"
    done
    return 0
  fi
  new_state r6
  art="$tmp/r6.art"
  env "${STD_SEAMS[@]}" GATE_RUN_STALE=600 bash "$rl" --out "$art" --expect '^HEAVY_GATE_DONE' -- \
    bash "$HG" run "$n" -- bash -c 'sleep 3; exit 3' "$token" >"$tmp/r6.launch" 2>&1
  sleep 1
  gate status
  mid=$G_OUT
  wait_for 20 grep -q '^RUN_LONG_EXIT_STATUS=' "$art"
  trailer=$(grep '^RUN_LONG_EXIT_STATUS=' "$art" 2>/dev/null)
  st=$(bash "$rl" --status "$art" --expect '^HEAVY_GATE_DONE' 2>&1)
  check_contains "$mid" "lock: $n " "R6/lock-held-during-stage"
  check_eq "$trailer" "RUN_LONG_EXIT_STATUS=3" "R6/trailer-stage-rc"
  check_eq "done=$(has_text "$st" 'RESULT: DONE rc=3') expect=$(has_text "$st" 'EXPECT: satisfied')" "done=yes expect=yes" "R6/expect-satisfied"

  new_state r6b
  revart="$tmp/r6b.art"
  bg_gate "$tmp/r6b.out" "$tmp/r6b.err" run "$n" -- bash "$rl" --out "$revart" -- bash -c 'sleep 4; exit 0' "$token"
  pid=$bg_pid
  bg_finish "$pid" 10
  gate status
  job=$(alive_count "$token")
  check_eq "wrapper-rc=$BG_RC lock=$(field lock) job=$([ "$job" -ge 1 ] && echo alive || echo gone)" "wrapper-rc=0 lock=free job=alive" "R6/reverse-form-lock-free"
  wait_for 12 grep -q '^RUN_LONG_EXIT_STATUS=' "$revart"
  reap_row
  cd "$oldpwd" || return 0
}

row_R12() {
  local n thr="$tmp/r12.thr" start="$tmp/r12.start" endm="$tmp/r12.end" pid early alive tk lk word t0 t1 arrived mid_word
  cat >"$tmp/r12-stage.sh" <<'EOF'
#!/usr/bin/env bash
# stage START END TOKEN -- marks its start, runs for 3 s, marks its end, exits 4
: >"$1"
sleep 3
: >"$2"
exit 4
EOF
  n=$(nm w)
  new_state r12
  printf '1\n' >"$thr"
  BG_ENV=("GATE_FAKE_THROTTLE_FILE=$thr")
  bg_gate "$tmp/r12.out" "$tmp/r12.err" run "$n" -- bash "$tmp/r12-stage.sh" "$start" "$endm" "$token"
  BG_ENV=()
  pid=$bg_pid
  sleep 5
  early=waiting
  if [ -e "$start" ]; then early=started; fi
  alive=yes
  if pid_gone "$pid"; then alive=no; fi
  tk=$(has_ticket "$n")
  lk=$(exists_word "$GATE_DIR/lock")
  GATE_FAKE_THROTTLE_FILE="$thr" gate status
  word=$(field throttle)
  printf '0\n' >"$thr"
  t0=$(now_ms)
  wait_for 4 test -e "$start"
  arrived=$?
  t1=$(now_ms)
  printf '1\n' >"$thr"
  GATE_FAKE_THROTTLE_FILE="$thr" gate status
  mid_word=$(field throttle)
  bg_finish "$pid" 10
  check_eq "early=$early alive=$alive ticket=$tk lock=$lk throttle=$word arrived=$arrived" \
    "early=waiting alive=yes ticket=yes lock=gone throttle=THROTTLED arrived=0" "R12/throttled-head-waits"
  check_eq "arrived=$arrived within=$([ $((t1 - t0)) -lt 3000 ] && echo yes || echo no)" "arrived=0 within=yes" "R12/flip-to-0-go-within-2-polls"
  check_eq "rc=$BG_RC end=$(exists_word "$endm") mid=$mid_word" "rc=4 end=present mid=THROTTLED" "R12/running-stage-unaffected-by-flip"
  reap_row
}

row_R13() {
  local n holder other owner0 ino0 sentinel="$tmp/r13.ran" t0 t1 dt
  n=$(nm w)
  holder=$(nm holder)
  other=$(nm other)
  new_state r13
  mk_lock "$holder" run 0
  mk_ticket "$other" "$(($(date +%s) - 30))" "" 0
  owner0=$(<"$GATE_DIR/lock/owner")
  ino0=$(inode_of "$GATE_DIR/lock")
  t0=$(date +%s)
  # shellcheck disable=SC2016 # the code runs in the child shell
  run_bounded 15 env "${STD_SEAMS[@]}" GATE_RUN_STALE=600 bash "$HG" run "$n" --max-wait 3 -- bash -c ': >"$1"' "$token" "$sentinel"
  t1=$(date +%s)
  dt=$((t1 - t0))
  check_eq "rc=$RB_RC err=$(usage_free "$RB_ERR")" "rc=75 err=ok" "R13/exit-75"
  check_eq "rc=$RB_RC waited=$([ "$dt" -ge 2 ] && echo yes || echo no) within=$([ "$dt" -lt 7 ] && echo yes || echo no)" "rc=75 waited=yes within=yes" "R13/within-bound"
  check_eq "rc=$RB_RC mine=$(has_ticket "$n") other=$(has_ticket "$other")" "rc=75 mine=no other=yes" "R13/ticket-removed"
  check_eq "rc=$RB_RC verdict=$(has_text "$RB_ERR" "HEAVY_GATE_DONE $n rc=75") sentinel=$(exists_word "$sentinel")" "rc=75 verdict=yes sentinel=gone" "R13/cmd-never-ran"
  check_eq "rc=$RB_RC owner-same=$([ -n "$owner0" ] && [ "$(<"$GATE_DIR/lock/owner")" = "$owner0" ] && echo yes || echo no) inode-same=$([ -n "$ino0" ] && [ "$(inode_of "$GATE_DIR/lock")" = "$ino0" ] && echo yes || echo no)" \
    "rc=75 owner-same=yes inode-same=yes" "R13/holder-lock-untouched"
  check_eq "$(last_line "$RB_ERR")" "HEAVY_GATE_DONE $n rc=75" "R13/last-line-75"
  reap_row
}

row_R14c() {
  local n
  n=$(nm w1)
  new_state r14c
  # shellcheck disable=SC2016 # the code runs in the child shell
  run_bounded 10 env "${STD_SEAMS[@]}" GATE_RUN_STALE=600 bash -c 'printf x | bash "$1" run "$2" -- bash -c "cat; :" "$3"' _ "$HG" "$n" "$token"
  check_eq "rc=$RB_RC out=[$RB_OUT]" "rc=0 out=[x]" "R14c/stdin-passes-through"
  reap_row
}

# --- Heartbeat rows (R7, R8): the heartbeat, the orphan paths, the whole-tree kill and the grace-to-KILL path ---
# Every target a row signals carries the run token; a group signal goes only to a group the row itself started
# with start_leader, after a positive reading that the group is not the suite's own.

# bg_leader OUT ERR ARGS... -- like bg_gate, but W leads its own process group (job control toggled in the calling
# shell, W stays the suite's child); the pid is left in $bg_pid. The exec chain keeps one pid: env, bash -c, then W.
bg_leader() {
  local out=$1 err=$2
  shift 2
  # shellcheck disable=SC2016 # the code runs in the child shell
  start_leader env "${STD_SEAMS[@]}" GATE_RUN_STALE=600 "${BG_ENV[@]+"${BG_ENV[@]}"}" \
    bash -c 'o=$1; e=$2; shift 2; exec "$@" >"$o" 2>"$e" </dev/null' _ "$out" "$err" bash "$HG" "$@"
  bg_pid=$leader
}

hb_count() { pgrep -f "__beat $1" 2>/dev/null | wc -l | tr -d ' '; }
hb_pid() { pgrep -f "__beat $1" 2>/dev/null | head -n 1; }

# shellcheck disable=SC2329 # invoked by wait_for
hb_none() { [ "$(hb_count "$1")" = 0 ]; }

# shellcheck disable=SC2329 # invoked by wait_for
owner_is() { [ "$(owner_name_of "$1")" = "$2" ]; }

# shellcheck disable=SC2329 # invoked by wait_for
owner_not() { [ "$(owner_name_of "$1")" != "$2" ]; }

# shellcheck disable=SC2329 # invoked by wait_for
ev_seen() { [ "$(events_has "$1")" = yes ]; }

# shellcheck disable=SC2329 # invoked by wait_for
has_recheck_nap() { # PID -- succeeds when PID has a child sleeping the re-check nap (exactly `sleep 2` at tick 1)
  local c cmd
  for c in $(pgrep -P "$1" 2>/dev/null); do
    cmd=$(ps -ww -o command= -p "$c" 2>/dev/null)
    read -r cmd <<<"$cmd"
    if [ "$cmd" = "sleep 2" ]; then return 0; fi
  done
  return 1
}

# group_ok PID WANT-CHILD -- prints ok when PID leads a group other than the suite's (and, when WANT-CHILD is 1,
# is the suite's own child); otherwise the reason. A positive reading, taken before any group signal.
group_ok() {
  local pg pp
  pg=$(pgid_of "$1")
  if [ -z "$pg" ]; then
    printf 'gone\n'
    return 0
  fi
  if [ "$pg" = "$(pgid_of $$)" ]; then
    printf 'shares-suite-group\n'
    return 0
  fi
  if [ "$pg" != "$1" ]; then
    printf 'not-a-leader\n'
    return 0
  fi
  if [ "${2:-0}" = 1 ]; then
    pp=$(ps -o ppid= -p "$1" | tr -d ' ')
    if [ "$pp" != "$$" ]; then
      printf 'ppid-not-suite\n'
      return 0
    fi
  fi
  printf 'ok\n'
}

carries_token() { has_text "$(ps -ww -o command= -p "$1" 2>/dev/null)" "$token"; }

file_age() { # FILE -- seconds since FILE was modified
  local m
  m=$(stat -c %Y "$1" 2>/dev/null || stat -f %m "$1" 2>/dev/null)
  m=${m:-0}
  printf '%s\n' $(($(date +%s) - m))
}

mk_mark_stage() {
  cat >"$tmp/mark-stage.sh" <<'STAGE'
#!/usr/bin/env bash
# stage MARK TOKEN SECS -- marks its start, then runs a token-bearing sleeping grandchild
: >"$1"
bash -c 'sleep "$1"; :' "$2" "$3" &
wait
STAGE
}

mk_term_stage() {
  cat >"$tmp/term-stage.sh" <<'STAGE'
#!/usr/bin/env bash
# stage STATE-DIR LOG TOKEN -- records whether the lock still exists when TERM arrives
trap 'if [ -d "$1/lock" ]; then echo LOCK_PRESENT >"$2"; else echo LOCK_GONE >"$2"; fi; exit 0' TERM
sleep 25 &
wait
STAGE
}

mk_log_stage() {
  cat >"$tmp/log-stage.sh" <<'STAGE'
#!/usr/bin/env bash
# stage LOG LABEL TOKEN SECS -- logs its start, logs term when TERM arrives, logs its end after SECS
trap 'echo "term $2" >>"$1"; exit 143' TERM
echo "start $2" >>"$1"
sleep "$4" &
wait $!
echo "end $2" >>"$1"
STAGE
}

mk_go_stage() {
  cat >"$tmp/go-stage.sh" <<'STAGE'
#!/usr/bin/env bash
# stage LOG TOKEN -- logs GO, the epoch and how many processes still match $S1PAT (kept in the environment, never an argument)
n=$(pgrep -f "$S1PAT" 2>/dev/null | wc -l | tr -d ' ')
echo "GO $(date +%s) s1alive=$n" >>"$1"
sleep 4
STAGE
}

row_R7() {
  local rl="$here/../run-long.sh" n tok art lp jobpid jg mine_g held before pre signalled c oldpwd=$PWD
  local trailers after
  if ! cd "$tmp"; then
    for c in i-group-term-no-survivors i-lock-ticket-gone i-no-trailer ii-plain-term-no-survivors ii-lock-ticket-gone ii-no-trailer iii-orphan-ignoring-stage-no-survivors iii-orphan-ignoring-stage-lock-ticket-gone; do
      fail_line "R7/$c (could not enter the run directory)"
    done
    return 0
  fi
  mk_mark_stage

  # case (i): a process-group TERM of the run-long job (started as the leader of its own group)
  n=$(nm w7i)
  tok="${token}-r7i"
  new_state r7i
  art="$tmp/r7i.art"
  # shellcheck disable=SC2016 # the code runs in the child shell
  start_leader bash -c 'o=$1; shift; exec "$@" >"$o" 2>&1' _ "$tmp/r7i.launch" \
    env "${STD_SEAMS[@]}" GATE_RUN_STALE=600 bash "$rl" --out "$art" -- bash "$HG" run "$n" -- bash "$tmp/mark-stage.sh" "$tmp/r7i.mark" "$tok" 30
  lp=$leader
  wait "$lp" 2>/dev/null
  wait_for 6 test -e "$tmp/r7i.mark"
  sleep 1
  jobpid=$(sed -n 's/.* pid=\([0-9][0-9]*\).*/\1/p' "$art" | head -n 1)
  held=$(owner_name_of "$GATE_DIR")
  before=$(alive_count "$tok")
  pre=bad
  if [ -n "$jobpid" ]; then
    jg=$(pgid_of "$jobpid")
    mine_g=$(pgid_of $$)
    if [ -n "$jg" ] && [ "$jg" != "$mine_g" ] && [ "$jg" = "$lp" ]; then pre=ok; fi
  fi
  signalled=no
  if [ "$pre" = ok ]; then
    kill -TERM -- "-$lp" 2>/dev/null
    signalled=yes
  fi
  wait_for 6 none_alive "$token"
  after=$(alive_count "$token")
  trailers=$(grep -c '^RUN_LONG_EXIT_STATUS=' "$art" 2>/dev/null)
  check_eq "job-group=$pre signalled=$signalled held=$([ "$held" = "$n" ] && echo yes || echo no) running=$([ "$before" -ge 3 ] && echo yes || echo no) after=$after" \
    "job-group=ok signalled=yes held=yes running=yes after=0" "R7/i-group-term-no-survivors"
  check_eq "signalled=$signalled $(state_shape)" "signalled=yes tickets=0 lock=gone trash=0" "R7/i-lock-ticket-gone"
  check_eq "signalled=$signalled header=$(has_text "$(cat "$art" 2>/dev/null)" 'RUN_LONG_COMMAND') trailer-lines=$trailers" \
    "signalled=yes header=yes trailer-lines=0" "R7/i-no-trailer"
  reap_row

  # case (ii): a plain TERM to the artifact header's pid only (the `kill <pid>` CLAUDE.md warns about)
  n=$(nm w7ii)
  tok="${token}-r7ii"
  new_state r7ii
  art="$tmp/r7ii.art"
  env "${STD_SEAMS[@]}" GATE_RUN_STALE=600 bash "$rl" --out "$art" -- bash "$HG" run "$n" -- bash "$tmp/mark-stage.sh" "$tmp/r7ii.mark" "$tok" 30 >"$tmp/r7ii.launch" 2>&1
  wait_for 6 test -e "$tmp/r7ii.mark"
  sleep 1
  jobpid=$(sed -n 's/.* pid=\([0-9][0-9]*\).*/\1/p' "$art" | head -n 1)
  held=$(owner_name_of "$GATE_DIR")
  before=$(alive_count "$tok")
  signalled=no
  if [ -n "$jobpid" ] && [ "$(has_text "$(ps -ww -o command= -p "$jobpid" 2>/dev/null)" "$tok")" = yes ]; then
    kill -TERM "$jobpid" 2>/dev/null
    signalled=yes
  fi
  wait_for 6 none_alive "$token"
  after=$(alive_count "$token")
  trailers=$(grep -c '^RUN_LONG_EXIT_STATUS=' "$art" 2>/dev/null)
  check_eq "signalled=$signalled held=$([ "$held" = "$n" ] && echo yes || echo no) running=$([ "$before" -ge 3 ] && echo yes || echo no) after=$after" \
    "signalled=yes held=yes running=yes after=0" "R7/ii-plain-term-no-survivors"
  check_eq "signalled=$signalled $(state_shape)" "signalled=yes tickets=0 lock=gone trash=0" "R7/ii-lock-ticket-gone"
  check_eq "signalled=$signalled header=$(has_text "$(cat "$art" 2>/dev/null)" 'RUN_LONG_COMMAND') trailer-lines=$trailers" \
    "signalled=yes header=yes trailer-lines=0" "R7/ii-no-trailer"
  reap_row

  # case (iii): the wrapper's PARENT is SIGKILLed while the stage IGNORES TERM and the tick (1) is below the grace (3):
  # the heartbeat re-sends its TERM every tick, and W's trap must reach the KILL step although bash 5.3 re-enters a running
  # trap: this row pins the ABORTING guard, and only under bash 5.3: bash 3.2 drops the repeated TERM, so the row cannot tell the guard's presence on 3.2 (the script's shebang picks the PATH bash). B is not in the `before` count: its command line carries the name, not the token.
  cat >"$tmp/ign-stage.sh" <<'STAGE'
#!/usr/bin/env bash
# stage TOKEN -- ignores TERM (the ignore is inherited by its sleep)
trap '' TERM
sleep 40
:
STAGE
  n=$(nm w7iii)
  tok="${token}-r7iii"
  new_state r7iii
  env "${STD_SEAMS[@]}" GATE_GRACE=3 GATE_RUN_STALE=600 bash -c 'bash "$@"; :' "${token}-r7iiip" "$HG" run "$n" -- bash "$tmp/ign-stage.sh" "$tok" >/dev/null 2>&1 &
  lp=$!
  wait_for 6 test -s "$GATE_DIR/lock/stage"
  sleep 1
  held=$(owner_name_of "$GATE_DIR")
  before=$(alive_count "$tok")
  signalled=no
  if [ "$(has_text "$(ps -ww -o command= -p "$lp" 2>/dev/null)" "${token}-r7iiip")" = yes ]; then
    kill -KILL "$lp" 2>/dev/null
    signalled=yes
  fi
  wait "$lp" 2>/dev/null
  wait_for 10 none_alive "$token"
  after=$(alive_count "$token")
  check_eq "signalled=$signalled held=$([ "$held" = "$n" ] && echo yes || echo no) running=$([ "$before" -ge 3 ] && echo yes || echo no) after=$after" \
    "signalled=yes held=yes running=yes after=0" "R7/iii-orphan-ignoring-stage-no-survivors"
  check_eq "signalled=$signalled $(state_shape)" "signalled=yes tickets=0 lock=gone trash=0" "R7/iii-orphan-ignoring-stage-lock-ticket-gone"
  reap_row
  cd "$oldpwd" || return 0
}

row_R8() {
  local w1 w2 s1 s2 d p1 p2 s_before hb_before t_mark t_kill held gone hb_after
  w1=$(nm w1)
  w2=$(nm w2)
  s1="${token}-r8s1"
  s2="${token}-r8s2"
  mk_mark_stage
  new_state r8
  d=$GATE_DIR
  # no stale rule may be the one that cleans up: every TTL and GATE_RUN_STALE (600 from bg_gate) is far above the window
  BG_ENV=(GATE_LOCK_TTL=3600 GATE_ORPHAN_LOCK_TTL=3600 GATE_TICKET_TTL=3600)
  bg_gate "$tmp/r8.1.out" "$tmp/r8.1.err" run "$w1" -- bash "$tmp/mark-stage.sh" "$tmp/r8.m1" "$s1" 30
  p1=$bg_pid
  wait_for 6 test -e "$tmp/r8.m1"
  t_mark=$(date +%s)
  bg_gate "$tmp/r8.2.out" "$tmp/r8.2.err" run "$w2" -- bash "$tmp/mark-stage.sh" "$tmp/r8.m2" "$s2" 30
  p2=$bg_pid
  BG_ENV=()
  sleep 2
  s_before=$(alive_count "$s1")
  hb_before=$(hb_count "$w1")
  t_kill=$(date +%s)
  kill -KILL "$p1" 2>/dev/null
  wait "$p1" 2>/dev/null
  wait_for 8 none_alive "$s1"
  gone=$(alive_count "$s1")
  wait_for 8 hb_none "$w1"
  hb_after=$(hb_count "$w1")
  wait_for 8 owner_is "$d" "$w2"
  held=$(owner_name_of "$d")
  check_eq "before=$([ "$s_before" -ge 2 ] && echo yes || echo no) after=$gone" "before=yes after=0" "R8/stage-gone"
  check_eq "hb-before=$([ "$hb_before" -ge 1 ] && echo yes || echo no) hb-after=$hb_after" "hb-before=yes hb-after=0" "R8/heartbeat-gone"
  check_eq "owner=$([ "$held" = "$w2" ] && echo second || echo "other:$held") stage2=$(exists_word "$tmp/r8.m2")" "owner=second stage2=present" "R8/second-run-holds-lock"
  check_eq "mark=$(exists_word "$tmp/r8.m1") elapsed-ok=$([ $((t_kill - t_mark)) -ge 1 ] && echo yes || echo no)" "mark=present elapsed-ok=yes" "R8/stage-ran-before-kill"
  kill -TERM "$p2" 2>/dev/null
  bg_finish "$p2" 8
  reap_row
}

# r8b_wait_mode_case -- a wait-mode owner of the same age as a stale run-mode one is not removed
r8b_wait_mode_case() {
  local n holder d own0 own1
  n=$(nm w3)
  holder=$(nm holder3)
  new_state r8bw
  d=$GATE_DIR
  mk_lock "$holder" wait 10 4242
  own0=$(<"$d/lock/owner")
  run_bounded 12 env "${STD_SEAMS[@]}" GATE_RUN_STALE=4 bash "$HG" run "$n" --max-wait 4 -- bash -c ': ' "$token"
  own1=$(<"$d/lock/owner")
  check_eq "rc=$RB_RC err=$(usage_free "$RB_ERR") owner-same=$([ -n "$own0" ] && [ "$own1" = "$own0" ] && echo yes || echo no) removed-event=$(events_has STALE_LOCK_REMOVED)" \
    "rc=75 err=ok owner-same=yes removed-event=no" "R8b/wait-mode-owner-not-removed"
}

row_R8b() {
  local w1 w2 s1 s2 d lg1 lg2 p1 p2 bp sp pre killed t_kill half_owner removed_later go_line go_epoch s_before term_seen
  w1=$(nm w1)
  w2=$(nm w2)
  s1="${token}-r8bs1"
  s2="${token}-r8bs2"
  lg1="$tmp/r8b.s1.log"
  lg2="$tmp/r8b.s2.log"
  mk_term_stage
  mk_go_stage
  new_state r8b
  d=$GATE_DIR
  BG_ENV=(GATE_RUN_STALE=4 GATE_TICKET_TTL=3600 GATE_LOCK_TTL=3600 GATE_ORPHAN_LOCK_TTL=3600)
  bg_leader "$tmp/r8b.1.out" "$tmp/r8b.1.err" run "$w1" -- bash "$tmp/term-stage.sh" "$d" "$lg1" "$s1"
  p1=$bg_pid
  wait_for 6 test -s "$d/lock/stage"
  sleep 1
  BG_ENV=(GATE_RUN_STALE=4 GATE_TICKET_TTL=3600 GATE_LOCK_TTL=3600 GATE_ORPHAN_LOCK_TTL=3600 "S1PAT=$s1")
  bg_gate "$tmp/r8b.2.out" "$tmp/r8b.2.err" run "$w2" -- bash "$tmp/go-stage.sh" "$lg2" "$s2"
  p2=$bg_pid
  BG_ENV=()
  sleep 1
  bp=$(hb_pid "$w1")
  sp=$(awk 'NR == 1 { print $1 }' "$d/lock/stage" 2>/dev/null)
  s_before=$(alive_count "$s1")
  # the positive reading before any group signal: W leads its own group and is the suite's child (G2, M1), B is in W's group,
  # S leads a group of its own, and all three carry the token
  pre=$(group_ok "$p1" 1)
  if [ "$pre" = ok ] && { [ -z "$bp" ] || [ "$(pgid_of "$bp")" != "$p1" ]; }; then pre=heartbeat-not-in-wrapper-group; fi
  if [ "$pre" = ok ] && [ "$(group_ok "${sp:-0}" 0)" != ok ]; then pre=stage-group-bad; fi
  if [ "$pre" = ok ] && { [ "$(carries_token "$p1")" != yes ] || [ "$(carries_token "$bp")" != yes ]; }; then pre=target-lacks-token; fi
  killed=no
  t_kill=$(date +%s)
  if [ "$pre" = ok ]; then
    kill -KILL -- "-$p1" 2>/dev/null
    killed=yes
    wait "$p1" 2>/dev/null
  fi
  sleep 2
  half_owner=$(owner_name_of "$d")
  wait_for 14 ev_seen "name=$w1 rule=b"
  removed_later=$(events_has "name=$w1 rule=b")
  wait_for 14 test -s "$lg2"
  go_line=$(head -n 1 "$lg2" 2>/dev/null)
  go_epoch=$(printf '%s\n' "$go_line" | awk '{ print $2 }')
  term_seen=$(cat "$lg1" 2>/dev/null)
  check_eq "pre=$pre killed=$killed owner-at-half=$([ "$half_owner" = "$w1" ] && echo holder || echo "other:$half_owner") removed-later=$removed_later" \
    "pre=ok killed=yes owner-at-half=holder removed-later=yes" "R8b/lock-kept-at-half-stale"
  check_eq "killed=$killed removed-event=$removed_later owner-not-holder=$([ "$(owner_name_of "$d")" != "$w1" ] && echo yes || echo no)" \
    "killed=yes removed-event=yes owner-not-holder=yes" "R8b/lock-removed-after-stale"
  check_eq "stage-before=$([ "$s_before" -ge 2 ] && echo yes || echo no) go=$([ -n "$go_line" ] && echo yes || echo no) stage-alive-at-go=$(printf '%s\n' "$go_line" | sed -n 's/.*s1alive=//p')" \
    "stage-before=yes go=yes stage-alive-at-go=0" "R8b/stage-gone-before-go"
  check_eq "killed=$killed term-line=$term_seen" "killed=yes term-line=LOCK_PRESENT" "R8b/term-seen-lock-present"
  check_eq "removed-event=$removed_later ticket=$(has_ticket "$w1")" "removed-event=yes ticket=no" "R8b/holder-ticket-gone"
  check_eq "killed=$killed go=$([ -n "$go_line" ] && echo yes || echo no) within-bound=$(if [ -n "$go_epoch" ] && [ $((go_epoch - t_kill)) -le 12 ]; then echo yes; else echo no; fi)" \
    "killed=yes go=yes within-bound=yes" "R8b/second-go-within-bound"
  kill -TERM "$p2" 2>/dev/null
  bg_finish "$p2" 8
  reap_row
  r8b_wait_mode_case
  reap_row
}

# r8c_start CASE WPID -- the fixture of one R8c case: a lock and a ticket, then a token-bearing stand-in wrapper
# (a real process that stays alive) that starts a stage in its own group and runs `__beat` with the passed WPID.
R8C_NAME=""
R8C_TOK=""
R8C_L=""
R8C_SP=""
r8c_start() {
  local pf="$tmp/r8c$1.sp"
  R8C_NAME=$(nm "r8c$1")
  R8C_TOK="${token}-r8c$1"
  new_state "r8c$1"
  mk_lock "$R8C_NAME" run 0 4242
  mk_ticket "$R8C_NAME" "$(($(date +%s) - 5))" "$2" 0
  rm -f "$pf"
  env "${STD_SEAMS[@]}" GATE_RUN_STALE=600 bash "$tmp/r8c-launch.sh" "$HG" "$R8C_NAME" "$2" "$R8C_TOK" "$pf" >/dev/null 2>&1 &
  R8C_L=$!
  wait_for 5 test -s "$pf"
  R8C_SP=$(<"$pf")
  sleep 1
}

# r8c_state -- one word: heartbeat, stage, lock and ticket of the current case
r8c_state() {
  local st=gone lk=gone
  if kill -0 "$R8C_SP" 2>/dev/null; then st=alive; fi
  if [ "$(owner_name_of "$GATE_DIR")" = "$R8C_NAME" ]; then lk=held; fi
  printf 'hb=%s stage=%s lock=%s ticket=%s\n' "$(hb_count "$R8C_NAME")" "$st" "$lk" "$(has_ticket "$R8C_NAME")"
}

row_R8c() {
  local un dead st age
  cat >"$tmp/r8c-launch.sh" <<'LAUNCH'
#!/usr/bin/env bash
# launcher HG NAME WPID TOKEN STAGEPIDFILE -- a stand-in wrapper that stays alive: it starts the stage in its own
# group, records the stage pid, runs the heartbeat with the passed WPID and waits
hg=$1
name=$2
wpid=$3
tok=$4
pf=$5
set -m
bash -c 'sleep 25; :' "$tok" &
sp=$!
set +m
printf '%s\n' "$sp" >"$pf.part"
mv "$pf.part" "$pf"
ls=$(LC_ALL=C TZ=UTC ps -o lstart= -p "$sp" | tr -s ' ')
bash "$hg" __beat "$name" "$wpid" "$sp" "$ls" &
wait
LAUNCH

  # (i) the real parent is alive and the passed pid names a live unrelated process: no cleanup
  bash -c 'sleep 25; :' "${token}-r8cu" &
  un=$!
  r8c_start 1 "$un"
  sleep 3
  st=$(r8c_state)
  age=$(file_age "$GATE_DIR/lock/owner")
  check_eq "$st unrelated=$(if kill -0 "$un" 2>/dev/null; then echo alive; else echo gone; fi) touched=$([ "$age" -le 2 ] && echo yes || echo no)" \
    "hb=1 stage=alive lock=held ticket=yes unrelated=alive touched=yes" "R8c/i-live-parent-live-unrelated-no-cleanup"
  reap_row
  wait "$un" 2>/dev/null
  wait "$R8C_L" 2>/dev/null

  # (ii) the real parent is alive and the passed pid is dead: no cleanup
  bash -c 'exit 0' &
  dead=$!
  wait "$dead" 2>/dev/null
  r8c_start 2 "$dead"
  sleep 3
  st=$(r8c_state)
  age=$(file_age "$GATE_DIR/lock/owner")
  check_eq "$st passed=$(if kill -0 "$dead" 2>/dev/null; then echo alive; else echo dead; fi) touched=$([ "$age" -le 2 ] && echo yes || echo no)" \
    "hb=1 stage=alive lock=held ticket=yes passed=dead touched=yes" "R8c/ii-live-parent-dead-passed-no-cleanup"
  reap_row
  wait "$R8C_L" 2>/dev/null

  # (iii) the real parent is SIGKILLed while the passed pid names a live unrelated process: cleanup
  bash -c 'sleep 25; :' "${token}-r8cu" &
  un=$!
  r8c_start 3 "$un"
  sleep 1
  st=$(r8c_state)
  kill -KILL "$R8C_L" 2>/dev/null
  wait "$R8C_L" 2>/dev/null
  wait_for 8 none_alive "$R8C_TOK"
  wait_for 8 hb_none "$R8C_NAME"
  check_eq "before=[$st] after=[$(r8c_state)] unrelated=$(if kill -0 "$un" 2>/dev/null; then echo alive; else echo gone; fi)" \
    "before=[hb=1 stage=alive lock=held ticket=yes] after=[hb=0 stage=gone lock=gone ticket=no] unrelated=alive" "R8c/iii-parent-killed-passed-live-cleanup"
  reap_row
  wait "$un" 2>/dev/null
}

row_R8d3() {
  local n s pid before hb t0 t1 alive7 later tick=5 grace=10 slack=4 early=3
  n=$(nm w)
  s="${token}-r8d3"
  new_state r8d3
  # default tick (5) and grace (10): only the state directory, the poll and the fake throttle are set
  # shellcheck disable=SC2016 # the code runs in the child shell
  env GATE_POLL=1 GATE_FAKE_THROTTLE=0 bash "$HG" run "$n" -- bash -c 'trap "" TERM; sleep 60; :' "$s" >"$tmp/r8d3.out" 2>"$tmp/r8d3.err" </dev/null &
  pid=$!
  wait_for 6 test -s "$GATE_DIR/lock/stage"
  sleep 2
  before=$(alive_count "$s")
  hb=$(hb_count "$n")
  kill -KILL "$pid" 2>/dev/null
  t0=$(now_ms)
  wait "$pid" 2>/dev/null
  # the earliest KILL is detection (>= 0) plus the grace, a whole-second wall-clock deadline of now + grace + 1 (so >= grace):
  # the stage must still be alive at grace - early (early 3, a margin of 3 s); the latest is tick + grace + 1 (the deadline's
  # extra second) plus slack 4
  sleep $((grace - early))
  alive7=$(alive_count "$s")
  wait_for $((tick + grace + 1 + slack)) none_alive "$s"
  t1=$(now_ms)
  later=$(alive_count "$s")
  check_eq "before=$([ "$before" -ge 1 ] && echo yes || echo no) hb=$([ "$hb" -ge 1 ] && echo yes || echo no) alive-at-grace-minus-slack=$([ "$alive7" -ge 1 ] && echo yes || echo no) later-gone=$([ "$later" = 0 ] && echo yes || echo no)" \
    "before=yes hb=yes alive-at-grace-minus-slack=yes later-gone=yes" "R8d3/alive-at-grace-minus-slack"
  check_eq "gone=$([ "$later" = 0 ] && echo yes || echo no) within-bound=$([ $((t1 - t0)) -le $(((tick + grace + 1 + slack) * 1000)) ] && echo yes || echo no)" \
    "gone=yes within-bound=yes" "R8d3/gone-by-bound"
  reap_row
}

row_R8e() {
  local w1 w2 s1 s2 d lg p1 p2 bp pre bk seq term nap err1 rc1 held
  w1=$(nm w1)
  w2=$(nm w2)
  s1="${token}-r8es1"
  s2="${token}-r8es2"
  lg="$tmp/r8e.log"
  mk_log_stage
  : >"$lg"
  new_state r8e
  d=$GATE_DIR
  BG_ENV=(GATE_TICKET_TTL=3600 GATE_LOCK_TTL=3600 GATE_ORPHAN_LOCK_TTL=3600)
  bg_gate "$tmp/r8e.1.out" "$tmp/r8e.1.err" run "$w1" -- bash "$tmp/log-stage.sh" "$lg" s1 "$s1" 25
  p1=$bg_pid
  wait_for 6 grep -q 'start s1' "$lg"
  sleep 1
  bp=$(hb_pid "$w1")
  pre=bad
  if [ -n "$bp" ] && [ "$(ps -o ppid= -p "$bp" | tr -d ' ')" = "$p1" ]; then pre=ok; fi
  bk=no
  if [ "$pre" = ok ]; then
    kill -KILL "$bp" 2>/dev/null
    bk=yes
  fi
  BG_ENV=(GATE_RUN_STALE=4 GATE_TICKET_TTL=3600 GATE_LOCK_TTL=3600 GATE_ORPHAN_LOCK_TTL=3600)
  bg_gate "$tmp/r8e.2.out" "$tmp/r8e.2.err" run "$w2" -- bash "$tmp/log-stage.sh" "$lg" s2 "$s2" 6
  p2=$bg_pid
  BG_ENV=()
  bg_finish "$p1" 20
  rc1=$BG_RC
  wait_for 10 grep -q 'start s2' "$lg"
  held=$(owner_name_of "$d")
  wait_for 3 none_alive "$s1"
  seq=$(head -n 3 "$lg" | tr '\n' '|')
  term=$(has_text "$(<"$lg")" "term s1")
  err1=$(last_line "$(<"$tmp/r8e.1.err")")
  check_eq "heartbeat-killed=$bk term-logged=$term alive=$(alive_count "$s1")" "heartbeat-killed=yes term-logged=yes alive=0" "R8e/stage-killed"
  check_eq "heartbeat-killed=$bk owner=$([ "$held" = "$w2" ] && echo second || echo "other:$held")" "heartbeat-killed=yes owner=second" "R8e/lock-handed-on"
  check_eq "$seq" "start s1|term s1|start s2|" "R8e/no-overlap-in-log"
  check_eq "heartbeat-killed=$bk rc=$rc1" "heartbeat-killed=yes rc=143" "R8e/first-wrapper-143"
  check_eq "heartbeat-killed=$bk last=$err1" "heartbeat-killed=yes last=HEAVY_GATE_DONE $w1 rc=143" "R8e/first-wrapper-verdict-line"
  bg_finish "$p2" 12
  reap_row

  # the paired case: the owner file is touched late, INSIDE the re-check window, so the lock is kept
  : >"$lg"
  new_state r8e2
  d=$GATE_DIR
  BG_ENV=(GATE_TICKET_TTL=3600 GATE_LOCK_TTL=3600 GATE_ORPHAN_LOCK_TTL=3600)
  bg_gate "$tmp/r8e2.1.out" "$tmp/r8e2.1.err" run "$w1" -- bash "$tmp/log-stage.sh" "$lg" s1 "$s1" 25
  p1=$bg_pid
  wait_for 6 grep -q 'start s1' "$lg"
  sleep 1
  bp=$(hb_pid "$w1")
  bk=no
  if [ -n "$bp" ] && [ "$(ps -o ppid= -p "$bp" | tr -d ' ')" = "$p1" ]; then
    kill -KILL "$bp" 2>/dev/null
    bk=yes
  fi
  BG_ENV=(GATE_RUN_STALE=4 GATE_TICKET_TTL=3600 GATE_LOCK_TTL=3600 GATE_ORPHAN_LOCK_TTL=3600)
  bg_gate "$tmp/r8e2.2.out" "$tmp/r8e2.2.err" run "$w2" -- bash "$tmp/log-stage.sh" "$lg" s2 "$s2" 6
  p2=$bg_pid
  BG_ENV=()
  nap=no
  if wait_for 14 has_recheck_nap "$p2"; then
    touch "$d/lock/owner"
    nap=yes
  fi
  sleep 3
  check_eq "heartbeat-killed=$bk touched-in-window=$nap owner=$([ "$(owner_name_of "$d")" = "$w1" ] && echo holder || echo other) stage-alive=$([ "$(alive_count "$s1")" -ge 1 ] && echo yes || echo no) removed-event=$(events_has STALE_LOCK_REMOVED)" \
    "heartbeat-killed=yes touched-in-window=yes owner=holder stage-alive=yes removed-event=no" "R8e/late-tick-keeps-lock"
  kill -TERM "$p1" 2>/dev/null
  bg_finish "$p1" 10
  kill -TERM "$p2" 2>/dev/null
  bg_finish "$p2" 10
  reap_row
}

# row_R8o pins the order ticket first, lock second, in run_release and in the heartbeat's parent-death path: the sourced
# script's remove_lock is replaced by a probe that records whether the holder's ticket still exists when it is called.
row_R8o() {
  local nm1 nm2 out1="$tmp/r8o.1" out2="$tmp/r8o.2"
  nm1=$(nm rel)
  nm2=$(nm beat)
  new_state r8o
  cat >"$tmp/ord-rel.sh" <<'ORD'
#!/usr/bin/env bash
# ord-rel HG NAME OUT -- run_release with a probing remove_lock
. "$1"
NM=$2
OUT=$3
ensure_state_dir
printf '%s\n' "$$" >"$Q/100-$NM"
mkdir "$L"
LOCK_LINE="$NM 100 $$ run"
printf '%s\n' "$LOCK_LINE" >"$L/owner"
RUN_NAME=$NM
remove_lock() { if [ -e "$Q/100-$NM" ]; then echo ticket-present >"$OUT"; else echo ticket-gone >"$OUT"; fi; }
run_release
ORD
  cat >"$tmp/ord-beat.sh" <<'ORD'
#!/usr/bin/env bash
# ord-beat HG NAME OUT TOKEN -- the heartbeat's parent-death path (started orphaned) with a probing remove_lock
. "$1"
NM=$2
OUT=$3
ensure_state_dir
printf '%s\n' 4242 >"$Q/100-$NM"
mkdir "$L"
printf '%s 100 4242 run\n' "$NM" >"$L/owner"
remove_lock() { if [ -e "$Q/100-$NM" ]; then echo ticket-present >"$OUT"; else echo ticket-gone >"$OUT"; fi; }
beat_main "$NM" 4242 "" ""
ORD
  env GATE_LIB_ONLY=1 "${STD_SEAMS[@]}" bash "$tmp/ord-rel.sh" "$HG" "$nm1" "$out1" >/dev/null 2>&1 </dev/null
  # the intermediate shell exits at once, so the heartbeat is orphaned (parent 1) from its first reading
  env GATE_LIB_ONLY=1 "${STD_SEAMS[@]}" bash -c 'bash "$@" </dev/null >/dev/null 2>&1 &' _ "$tmp/ord-beat.sh" "$HG" "$nm2" "$out2" "$token"
  wait_for 8 test -s "$out2"
  check_eq "$(cat "$out1" 2>/dev/null)" "ticket-gone" "R8o/release-ticket-before-lock"
  check_eq "$(cat "$out2" 2>/dev/null)" "ticket-gone" "R8o/beat-ticket-before-lock"
  reap_row
}

row_R8g() {
  local n d dp e s pid orig_ok content_ok
  n=$(nm w)
  s="${token}-r8g"
  mk_mark_stage
  new_state r8g
  d=$GATE_DIR
  bash -c 'exit 0' &
  dp=$!
  wait "$dp" 2>/dev/null
  e=$(($(date +%s) - 30))
  # the dead holder's lock and its ticket carry the NAME the live waiter then reuses (a duplicate-name run)
  mk_lock "$n" run 10 "$dp"
  mk_ticket "$n" "$e" "$dp" 0
  BG_ENV=(GATE_RUN_STALE=4 GATE_TICKET_TTL=3600)
  bg_gate "$tmp/r8g.out" "$tmp/r8g.err" run "$n" -- bash "$tmp/mark-stage.sh" "$tmp/r8g.mark" "$s" 2
  pid=$bg_pid
  BG_ENV=()
  wait_for 12 ev_seen "name=$n rule=b"
  orig_ok=no
  content_ok=no
  if [ -f "$d/queue/$e-$n" ]; then
    orig_ok=yes
    if [ "$(<"$d/queue/$e-$n")" = "$pid" ]; then content_ok=yes; fi
  fi
  bg_finish "$pid" 15
  check_eq "removed=$(events_has "name=$n rule=b") original-ticket=$orig_ok holds-waiter-pid=$content_ok" \
    "removed=yes original-ticket=yes holds-waiter-pid=yes" "R8g/live-waiter-ticket-present"
  check_eq "rc=$BG_RC stage=$(exists_word "$tmp/r8g.mark") $(state_shape)" "rc=0 stage=present tickets=0 lock=gone trash=0" "R8g/live-waiter-reaches-go"
  reap_row
}

# r8h_case TAG DELAY KIND GRACE -- the harness shape: TERM to W, then (after DELAY seconds) one hard kill of W, B and S.
# KIND is honor (a TERM-honouring stage) or ignore (a TERM-ignoring one). Results are left in R8H_* for the labels.
R8H_PRE="" R8H_KILLED="" R8H_HALF="" R8H_REMOVED="" R8H_WITHIN="" R8H_TICKET="" R8H_GO="" R8H_GOIN="" R8H_SURV="" R8H_TRASH="" R8H_NONE=""
r8h_case() {
  local tag=$1 delay=$2 kind=$3 grace=$4 w1 w2 s1 s2 d lg p1 p2 bp sp s_before hb_before t_hard go_epoch
  w1=$(nm "r8h${tag}1")
  w2=$(nm "r8h${tag}2")
  s1="${token}-r8h${tag}s1"
  s2="${token}-r8h${tag}s2"
  lg="$tmp/r8h$tag.log"
  : >"$lg"
  new_state "r8h$tag"
  d=$GATE_DIR
  BG_ENV=(GATE_RUN_STALE=4 "GATE_GRACE=$grace" GATE_TICKET_TTL=3600 GATE_LOCK_TTL=3600 GATE_ORPHAN_LOCK_TTL=3600)
  if [ "$kind" = honor ]; then
    # shellcheck disable=SC2016 # the code runs in the child shell
    bg_leader "$tmp/r8h$tag.1.out" "$tmp/r8h$tag.1.err" run "$w1" -- bash -c 'bash -c "sleep 30; :" "$0" & wait' "$s1"
  else
    # shellcheck disable=SC2016 # the code runs in the child shell
    bg_leader "$tmp/r8h$tag.1.out" "$tmp/r8h$tag.1.err" run "$w1" -- bash -c 'trap "" TERM; sleep 60; :' "$s1"
  fi
  p1=$bg_pid
  wait_for 6 test -s "$d/lock/stage"
  sleep 1
  BG_ENV=(GATE_RUN_STALE=4 "GATE_GRACE=$grace" GATE_TICKET_TTL=3600 GATE_LOCK_TTL=3600 GATE_ORPHAN_LOCK_TTL=3600 "S1PAT=$s1")
  bg_gate "$tmp/r8h$tag.2.out" "$tmp/r8h$tag.2.err" run "$w2" -- bash "$tmp/go-stage.sh" "$lg" "$s2"
  p2=$bg_pid
  BG_ENV=()
  sleep 1
  bp=$(hb_pid "$w1")
  sp=$(awk 'NR == 1 { print $1 }' "$d/lock/stage" 2>/dev/null)
  s_before=$(alive_count "$s1")
  hb_before=$(hb_count "$w1")
  # the positive reading before any signal (G2, M1): W, B and S are the processes the row means to kill (each carries the
  # token), none shares the suite's group, W is the suite's child and leads a group B belongs to, S leads its own group
  R8H_PRE=$(group_ok "$p1" 1)
  if [ "$R8H_PRE" = ok ] && { [ -z "$bp" ] || [ "$(pgid_of "$bp")" != "$p1" ]; }; then R8H_PRE=heartbeat-not-in-wrapper-group; fi
  if [ "$R8H_PRE" = ok ] && [ "$(group_ok "${sp:-0}" 0)" != ok ]; then R8H_PRE=stage-group-bad; fi
  if [ "$R8H_PRE" = ok ]; then
    if [ "$(carries_token "$p1")" != yes ] || [ "$(carries_token "$bp")" != yes ] || [ "$(carries_token "$sp")" != yes ]; then R8H_PRE=target-lacks-token; fi
  fi
  R8H_KILLED=no
  t_hard=$(date +%s)
  if [ "$R8H_PRE" = ok ]; then
    kill -TERM "$p1" 2>/dev/null
    if [ "$delay" -gt 0 ]; then sleep "$delay"; fi
    t_hard=$(date +%s)
    kill -KILL -- "-$p1" "-$sp" 2>/dev/null
    R8H_KILLED=yes
    wait "$p1" 2>/dev/null
  fi
  sleep 2
  R8H_HALF=$(owner_name_of "$d")
  R8H_WITHIN=no
  if wait_for 14 owner_not "$d" "$w1"; then R8H_WITHIN=yes; fi
  # the lock is momentarily absent between the pruner's rename into trash and its STALE_LOCK_REMOVED line, so owner_not can
  # hold before the event exists: poll for the event (bounded) instead of reading it once
  wait_for 8 ev_seen "name=$w1 rule=b"
  R8H_REMOVED=$(events_has "name=$w1 rule=b")
  wait_for 8 test -s "$lg"
  go_epoch=$(awk 'NR == 1 { print $2 }' "$lg" 2>/dev/null)
  R8H_GO=no
  R8H_GOIN=no
  if [ -n "$go_epoch" ]; then
    R8H_GO=yes
    # nominal worst case from the hard kill: the owner file was last touched up to one tick before it, and the rule is
    # age > RUN_STALE on whole-second mtimes (stale at age 5), so RUN_STALE + 1 + tick; one poll to see it; the re-check
    # tick + 1; one poll to take the lock; slack 5 for whole-second truncation of t_hard and fork overhead under load
    if [ $((go_epoch - t_hard)) -le $((4 + 1 + 1 + 1 + 2 + 1 + 5)) ]; then R8H_GOIN=yes; fi
  fi
  R8H_TICKET=$(has_ticket "$w1")
  R8H_SURV="stage=$(alive_count "$s1") hb=$(hb_count "$w1") before=$([ "$s_before" -ge 2 ] && [ "$hb_before" -ge 1 ] && echo yes || echo no)"
  R8H_TRASH=$(state_shape)
  R8H_NONE=$(events_has "name=$w1 rule=b stage=none")
  kill -TERM "$p2" 2>/dev/null
  bg_finish "$p2" 8
  reap_row
}

row_R8h() {
  mk_go_stage
  # case (i): the hard kill follows the TERM at once
  r8h_case i 0 honor 2
  check_eq "pre=$R8H_PRE killed=$R8H_KILLED within-bound=$R8H_WITHIN removed-event=$R8H_REMOVED" \
    "pre=ok killed=yes within-bound=yes removed-event=yes" "R8h/i-lock-gone-after-bound"
  check_eq "killed=$R8H_KILLED removed-later=$R8H_REMOVED owner-at-half=$([ "$R8H_HALF" = "$(nm r8hi1)" ] && echo holder || echo "other:$R8H_HALF")" \
    "killed=yes removed-later=yes owner-at-half=holder" "R8h/i-not-before-run-stale"
  check_eq "killed=$R8H_KILLED removed-event=$R8H_REMOVED ticket=$R8H_TICKET" "killed=yes removed-event=yes ticket=no" "R8h/i-holder-ticket-gone"
  check_eq "killed=$R8H_KILLED go=$R8H_GO within-bound=$R8H_GOIN" "killed=yes go=yes within-bound=yes" "R8h/i-second-go"
  check_eq "killed=$R8H_KILLED $R8H_SURV" "killed=yes stage=0 hb=0 before=yes" "R8h/i-no-survivors"
  check_eq "killed=$R8H_KILLED removed-event=$R8H_REMOVED $R8H_TRASH" "killed=yes removed-event=yes tickets=1 lock=present trash=0" "R8h/i-no-trash"
  check_eq "killed=$R8H_KILLED stage-none-event=$R8H_NONE" "killed=yes stage-none-event=yes" "R8h/kill-stage-found-nothing-logged"
  # case (ii): the hard kill lands 2 s after the TERM, with a TERM-ignoring stage and a 5 s grace, so W's trap is mid-grace
  r8h_case ii 2 ignore 5
  check_eq "pre=$R8H_PRE killed=$R8H_KILLED within-bound=$R8H_WITHIN removed-event=$R8H_REMOVED ticket=$R8H_TICKET" \
    "pre=ok killed=yes within-bound=yes removed-event=yes ticket=no" "R8h/ii-lock-gone"
  check_eq "killed=$R8H_KILLED go=$R8H_GO within-bound=$R8H_GOIN" "killed=yes go=yes within-bound=yes" "R8h/ii-second-go"
  check_eq "killed=$R8H_KILLED $R8H_SURV" "killed=yes stage=0 hb=0 before=yes" "R8h/ii-no-survivors"
  check_eq "killed=$R8H_KILLED removed-event=$R8H_REMOVED $R8H_TRASH" "killed=yes removed-event=yes tickets=1 lock=present trash=0" "R8h/ii-no-trash"
}

# r16_bullet FILE -- the CLAUDE.md bullet whose first line begins `- **Before a CPU-heavy stage`, joined with its
# indented continuation lines by single spaces (empty when the bullet is absent).
r16_bullet() {
  awk '
    b == 0 && index($0, "- **Before a CPU-heavy stage") == 1 { out = $0; b = 1; next }
    b == 1 {
      if ($0 ~ /^[ \t]+[^ \t]/) {
        sub(/^[ \t]+/, "")
        if (index($0, "- ") == 1) exit
        out = out " " $0
        next
      }
      exit
    }
    END { if (b) print out }
  ' "$1" 2>/dev/null
}

# r16_check LABEL OK DETAIL -- the vacuity rule: a label never passes while the bullet text is empty.
R16_BULLET=""
r16_check() {
  if [ -z "$R16_BULLET" ]; then
    fail_line "$1 (bullet absent or empty)"
  elif [ "$2" = yes ]; then
    pass_line "$1"
  else
    fail_line "$1 ($3)"
  fi
}

# row_R16 [FILE] -- the CLAUDE.md bullet is present, small, names the launcher form, and its launcher line runs as
# written with this clone's own scripts substituted by absolute path. FILE (default: this clone's CLAUDE.md, found
# from the suite's own location) is an optional override that nothing passes: the dispatcher calls it bare.
row_R16() {
  local src=${1:-"$here/../../CLAUDE.md"} size ok span cmd art nm16 root_abs hg_abs rl_abs tilde_pat after_launcher
  local gate_pat bare_pat launch_rc
  R16_BULLET=$(r16_bullet "$src")
  size=$(printf '%s' "$R16_BULLET" | LC_ALL=C wc -c | tr -d ' ')
  r16_check "R16/bullet-found" yes ""
  ok=no
  if [ "$size" -le 600 ]; then ok=yes; fi
  r16_check "R16/size-le-600" "$ok" "joined bytes $size"
  ok=$(has_text "$R16_BULLET" "--expect")
  r16_check "R16/has-expect" "$ok" "no --expect in the bullet"
  gate_pat='run-long\.sh.*~/\.claude/scripts/heavy-gate\.sh run '
  ok=no
  if [[ "$R16_BULLET" =~ $gate_pat ]]; then ok=yes; fi
  r16_check "R16/abs-gate-path-inside-launcher" "$ok" "gate path not after run-long.sh and before run"
  ok=$(has_text "$R16_BULLET" "convention, not enforcement")
  r16_check "R16/convention-not-enforcement" "$ok" "phrase missing"
  ok=$(has_text "$R16_BULLET" "exit 75")
  r16_check "R16/mentions-75" "$ok" "no exit 75"

  # the run half: the backticked span holding the launcher form, with the placeholders and the two path
  # prefixes substituted by this clone's own scripts (absolute, physical paths)
  span=$(printf '%s\n' "$R16_BULLET" | awk -F'`' '{ for (i = 2; i <= NF; i += 2) if (index($i, "run-long.sh --out")) { print $i; exit } }')
  root_abs=$(cd "$here/.." && pwd -P)
  hg_abs="$root_abs/heavy-gate.sh"
  rl_abs="$root_abs/run-long.sh"
  art="$tmp/r16.art"
  nm16="r16-$token"
  # shellcheck disable=SC2088 # a literal pattern: the tilde is the text the bullet carries, never expanded
  tilde_pat='~/.claude/scripts/heavy-gate.sh'
  cmd=${span//"<p>"/$art}
  cmd=${cmd//"<name>"/$nm16}
  cmd=${cmd//"<cmd>"/true}
  cmd=${cmd//"$tilde_pat"/$hg_abs}
  cmd=$(printf '%s\n' "$cmd" | sed -e "s|^run-long\\.sh|$rl_abs|" -e "s| run-long\\.sh| $rl_abs|g")
  bare_pat='(^|[^/])run-long\.sh'
  ok=no
  if [ -n "$cmd" ]; then
    ok=yes
    case "$cmd" in *"~"*) ok=no ;; esac
    if [[ "$cmd" =~ $bare_pat ]]; then ok=no; fi
  fi
  r16_check "R16/substituted-command-has-no-tilde-or-bare-name" "$ok" "the substituted launcher line still names ~ or a bare run-long.sh"
  after_launcher=""
  launch_rc="skipped"
  if [ "$ok" = yes ]; then
    new_state r16-state
    run_bounded 20 env GATE_FAKE_THROTTLE=0 GATE_POLL=1 GATE_BEAT_TICK=1 GATE_GRACE=2 GATE_RUN_STALE=4 bash -c "$cmd"
    launch_rc=$RB_RC
    run_bounded 60 bash "$rl_abs" --wait "$art" --interval 1
    after_launcher="rc=$RB_RC $RB_OUT"
  fi
  ok=no
  if [ "$launch_rc" = 0 ] && [[ "$after_launcher" == *"RESULT: DONE rc=0"* ]] && grep -q "^HEAVY_GATE_DONE $nm16 rc=0\$" "$art" 2>/dev/null; then ok=yes; fi
  r16_check "R16/launcher-line-runs-done-rc0" "$ok" "launch rc=$launch_rc, wait output [$after_launcher]"
  ok=$(has_text "$after_launcher" "EXPECT: satisfied")
  r16_check "R16/launcher-expect-satisfied" "$ok" "wait output [$after_launcher]"
}

run_row() { # ID -- run the row when HG_ONLY selects it
  local id=$1 w
  if [ -n "${HG_ONLY:-}" ]; then
    for w in $HG_ONLY; do
      [ "$w" = "$id" ] && break
      w=""
    done
    [ -n "$w" ] || return 0
  fi
  case "$id" in
    R0) row_R0 ;;
    R0b) if [ "$inner" != 1 ]; then row_R0b; fi ;; # the inner run never starts another inner run
    R1) row_R1 ;;
    R2) row_R2 ;;
    R2b) row_R2b ;;
    R9a) row_R9a ;;
    R14e) row_R14e ;;
    R8d1) row_R8d1 ;;
    R8d2) row_R8d2 ;;
    R3) row_R3 ;;
    R4h) row_R4h ;;
    R4) row_R4 ;;
    R5) row_R5 ;;
    R6) row_R6 ;;
    R12) row_R12 ;;
    R13) row_R13 ;;
    R14c) row_R14c ;;
    R9) row_R9 ;;
    R10) row_R10 ;;
    R11) row_R11 ;;
    R11s)
      if [ "${HG_STRESS:-1}" = 0 ]; then printf 'SKIP  R11s (HG_STRESS=0)\n'; else row_R11s; fi
      ;;
    R14a) row_R14a ;;
    R14b) row_R14b ;;
    R14d) row_R14d ;;
    R14f) row_R14f ;;
    R15) row_R15 ;;
    R8f1) row_R8f1 ;;
    R8f2) row_R8f2 ;;
    R8f3) row_R8f3 ;;
    R7) row_R7 ;;
    R8) row_R8 ;;
    R8b) row_R8b ;;
    R8c) row_R8c ;;
    R8d3) row_R8d3 ;;
    R8e) row_R8e ;;
    R8g) row_R8g ;;
    R8o) row_R8o ;;
    R8h) row_R8h ;;
    R16) row_R16 ;;
  esac
  unset GATE_DIR
}

ROW_IDS="R0 R0b R1 R2 R2b R9a R14e R8d1 R8d2 R3 R4h R4 R5 R6 R9 R10 R11 R11s R12 R13 R14a R14b R14c R14d R14f R15 R8f1 R8f2 R8f3 R7 R8 R8b R8c R8d3 R8e R8g R8o R8h R16"

# A selected id that names no row would otherwise run nothing and read as a pass.
for sel in ${HG_ONLY:-}; do
  case " $ROW_IDS " in
    *" $sel "*) ;;
    *) fail_line "HG_ONLY names no row: $sel" ;;
  esac
done

for id in $ROW_IDS; do
  run_row "$id"
done

printf '\n%s passed, %s failed\n' "$pass" "$fail"
if [ "$fail" -eq 0 ] && [ "$pass" -gt 0 ]; then exit 0; fi
exit 1

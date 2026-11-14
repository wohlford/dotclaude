#!/usr/bin/env bash
set -uo pipefail

# Script: heavy-gate.sh
# Purpose: Global turn-taking and thermal-throttle gate so concurrent Claude sessions start heavy work one at a time
# Usage: heavy-gate.sh throttled
#        heavy-gate.sh join NAME
#        heavy-gate.sh wait NAME
#        heavy-gate.sh run NAME [--max-wait SECS] -- CMD...
#        heavy-gate.sh release NAME
#        heavy-gate.sh renew NAME
#        heavy-gate.sh status
#        heavy-gate.sh held
#        heavy-gate.sh --help
#
# State lives in one directory shared by every session on the host, on purpose: per-session state
# would give no turn-taking. Seams (GATE_POLL, GATE_FAKE_THROTTLE and the other GATE_* names listed
# in --help) are honoured only when GATE_DIR is also set, so an exported seam can never leak into a
# real session and turn the gate against the shared queue into a no-op.
#
# Throttle predicate: the machine is THROTTLED when pmset's CPU_Speed_Limit or CPU_Scheduler_Limit
# reads below 100. The machdep.xcpm thermal levels are informational only (status prints them on a
# levels: line): on some hosts they read 41-96 while pmset reports no limit, so no level threshold
# is applied. A machine with no pmset reading is unmeasurable and counts as not throttled.
#
# Nesting: run exports HEAVY_GATE_HELD (the owner line it wrote) to its stage. held_by_env says a
# caller is nested inside the turn when that token equals the live run-mode owner line byte for byte,
# or when the owner line's wrapper pid is a live ancestor of the caller whose command line names
# heavy-gate.sh. A nested run takes no second turn; a nested wait or join is refused.

usage_text() {
  cat <<'EOF'
Usage: heavy-gate.sh throttled
       heavy-gate.sh join NAME
       heavy-gate.sh wait NAME
       heavy-gate.sh run NAME [--max-wait SECS] -- CMD...
       heavy-gate.sh release NAME
       heavy-gate.sh renew NAME
       heavy-gate.sh status
       heavy-gate.sh held
       heavy-gate.sh --help
EOF
}

help_text() {
  usage_text
  cat <<'EOF'

A global turn-taking and thermal-throttle gate for heavy work started by concurrent sessions.
Exactly one session holds the turn at a time, and nothing starts while the machine is throttled.

Subcommands:
  throttled   exit 0 only when the machine reports thermal throttling by its pmset speed limits; prints nothing
  join        take an empty ticket in the queue (no duplicate-name refusal)
  wait        wait for a turn, take the lock in mode wait and print GO NAME; the caller must then release
  release     free the lock when NAME holds it and remove NAME's tickets
  renew       touch the lock's owner file (when NAME holds it) and NAME's ticket, to outlast the lock TTL
  status      read-only report: queue, lock, throttle word, source, thermal levels (informational), seam, params; never prunes, never blocks
  run         wait for a turn, run CMD as the stage, release the turn, exit with the stage's status
  held        exit 0 and print the holder's NAME when the caller is nested inside a run's turn; exit 1 and print nothing otherwise; read-only

Names:
  A NAME matches [A-Za-z0-9._-]+ and does not start with a dot.
  The convention is <project>-<purpose>-<pid-or-session-short>.

Exit codes:
  the stage's own status, when run executed a stage
  2 usage, validation or state-directory problem
  75 no turn within --max-wait
  75 means this session did not get a turn: report it, do not work around it
  129 the waiting process's parent exited while it was queued: it left the queue and started nothing
  128+signal when the stage was ended by a signal

Verdict line:
  once its arguments parse, run prints, as the last line on stderr and after its release, HEAVY_GATE_DONE <NAME> rc=<n>
  wait prints the same line (rc=129) only when it leaves the queue because its parent is gone
  a malformed call (an unparsable --max-wait, say) exits 2 with no verdict line

Liveness bounds:
  a SIGKILLed wrapper takes its stage with it within GATE_BEAT_TICK (default 5 s) plus GATE_GRACE (default 10 s), plus up to one second of whole-second rounding
  a run whose parent process is gone is stopped within the same bound
  only the stage's process group is stopped, so a container or VM outside that group keeps running
  a waiter killed with SIGKILL while at the head of the queue holds everyone up for up to GATE_TICKET_TTL (default 600 s) before its ticket is pruned

Cost of a hard stop:
  if the whole process tree is killed, as a harness stop of the task does, the lock and ticket are handed on after GATE_RUN_STALE plus one tick plus one second plus one GATE_POLL, about 86 s at the defaults, unless the wrapper's trap finished first

Long stages:
  run is the supported form: its heartbeat keeps a long stage's lock live, so a stage outlasting the 45-minute lock TTL (GATE_LOCK_TTL, 2700 s) is no reason to leave it
  wait, release and renew are for a stage that cannot use run; it must call renew inside that TTL or the lock is judged stale and handed on

Limits of run:
  a stage that detaches from its wrapper (setsid, a daemon) and scrubs its environment is not recognised as nested, and a run it starts queues behind its own parent, so give such a run --max-wait
  --max-wait is checked once per poll, so its resolution is one GATE_POLL (default 20 s); a poll that finds a stale run-mode lock first spends the re-check (GATE_BEAT_TICK plus one second, 6 s at the defaults) and any stage kill (up to GATE_GRACE, 10 s at the defaults), so a wait can end later than SECS by that much
  a signal during that re-check or kill is still handled within about 0.2 s, and a SIGKILL landing between a stage's launch and the record of its process group leaves a stage nothing stops
  the tty fallback is unverified: when stdin is a terminal the stage gets /dev/null and one stderr note, and no row has run that path
  a signal between the lock's mkdir and the write of its owner line can leave an ownerless lock, which rule a clears after GATE_ORPHAN_LOCK_TTL (120 s)
  run waits only for the stage's leader, so a group member the stage backgrounds can outlive the release
  a run started directly by pid 1 (a launchd agent, or a container whose init runs the gate) reads as orphaned and is stopped
  the heartbeat is this script re-executed from the file on disk, with its output discarded: a broken call shows nothing of its own: with a waiter queued the stage is stopped by rule 1(b) after GATE_RUN_STALE plus the re-check (about 86 s before the hand-on, plus up to 11 s if the stage ignores TERM), and with none nothing stops it, and a script replaced on disk while a run is queued gives that run's heartbeat the new version

Nesting:
  run exports HEAVY_GATE_HELD, the owner line it wrote, to its stage; a caller is nested while that line is still the lock's run-mode owner line, or while the lock's wrapper process is an ancestor of the caller
  a nested run takes no second turn: it prints "heavy-gate: nested inside <holder>; running without a second turn" on stderr, runs CMD in the foreground and exits with its status
  a nested wait or join exits 2 naming the holder, since a wait there would wait for its own parent
  held reports it: exit 0 and the holder's NAME when nested, exit 1 and no output otherwise, exit 2 on stray arguments; it never writes state

Scope:
  run-long.sh and audit.sh --tests take a turn by themselves; for every other caller the gate is a convention, not enforcement: a session that does not call it is not stopped
  the token is a courtesy among cooperating sessions

Seams (honoured only with GATE_DIR set):
  GATE_POLL GATE_FAKE_THROTTLE GATE_FAKE_THROTTLE_FILE GATE_BEAT_TICK GATE_GRACE
  GATE_RUN_STALE GATE_TICKET_TTL GATE_LOCK_TTL GATE_ORPHAN_LOCK_TTL GATE_LIB_ONLY
  GATE_SYSCTL_CMD GATE_PMSET_CMD (a path to an executable replacing sysctl / pmset; a set path that is not executable exits 2)
EOF
}

die() {
  printf 'heavy-gate: %s\n' "$*" >&2
  exit 2
}

is_num() { # VALUE -- succeeds when VALUE is all digits
  case "${1:-}" in
    '' | *[!0-9]*) return 1 ;;
  esac
  return 0
}

# shellcheck disable=SC2329 # called by the subcommands that take a NAME, and by the suite through GATE_LIB_ONLY
valid_name() { # NAME -- the allowlist: [A-Za-z0-9._-]+, never starting with a dot
  case "${1:-}" in
    '' | .* | *[!A-Za-z0-9._-]*) return 1 ;;
  esac
  return 0
}

# Seams are honoured only with GATE_DIR set; with it unset every default below is the real one.
if [ -n "${GATE_DIR:-}" ]; then seam_on=1; else seam_on=0; fi

seam_value() { # SEAM-NAME DEFAULT -- the seam's integer when honoured, else DEFAULT
  local v=""
  if [ "$seam_on" = 1 ]; then v="${!1:-}"; fi
  if [ -z "$v" ]; then
    printf '%s\n' "$2"
    return 0
  fi
  is_num "$v" || die "$1 must be a non-negative integer, got [$v]"
  printf '%s\n' "$v"
}

P_POLL=$(seam_value GATE_POLL 20) || exit 2
P_TICK=$(seam_value GATE_BEAT_TICK 5) || exit 2
P_GRACE=$(seam_value GATE_GRACE 10) || exit 2
P_RUN_STALE=$(seam_value GATE_RUN_STALE 60) || exit 2
P_TICKET_TTL=$(seam_value GATE_TICKET_TTL 600) || exit 2
P_LOCK_TTL=$(seam_value GATE_LOCK_TTL 2700) || exit 2
P_ORPHAN_LOCK_TTL=$(seam_value GATE_ORPHAN_LOCK_TTL 120) || exit 2

seam_cmd() { # SEAM-NAME DEFAULT -- the seam's executable path when honoured, else DEFAULT
  local v=""
  if [ "$seam_on" = 1 ]; then v="${!1:-}"; fi
  if [ -z "$v" ]; then
    printf '%s\n' "$2"
    return 0
  fi
  [ -f "$v" ] && [ -x "$v" ] || die "$1 is not an executable file: [$v]"
  printf '%s\n' "$v"
}

# Validated here, at top level: a die inside the $(throttle_reading) subshell would exit only that subshell.
P_SYSCTL_CMD=$(seam_cmd GATE_SYSCTL_CMD sysctl) || exit 2
P_PMSET_CMD=$(seam_cmd GATE_PMSET_CMD pmset) || exit 2

seam_active() { # succeeds when GATE_DIR is set and at least one seam is set
  local v
  [ "$seam_on" = 1 ] || return 1
  for v in GATE_POLL GATE_FAKE_THROTTLE GATE_FAKE_THROTTLE_FILE GATE_BEAT_TICK GATE_GRACE \
    GATE_RUN_STALE GATE_TICKET_TTL GATE_LOCK_TTL GATE_ORPHAN_LOCK_TTL GATE_LIB_ONLY \
    GATE_SYSCTL_CMD GATE_PMSET_CMD; do
    if [ -n "${!v:-}" ]; then return 0; fi
  done
  return 1
}

default_state_dir() { # <base>/claude-<uid>/heavy-gate, <base> is /private/tmp when it exists
  local base uid
  if [ -d /private/tmp ]; then base=/private/tmp; else base=/tmp; fi
  uid=$(id -u)
  is_num "$uid" || die "id -u gave no numeric uid, got [$uid]"
  printf '%s/claude-%s/heavy-gate\n' "$base" "$uid"
}

state_dir() {
  if [ -n "${GATE_DIR:-}" ]; then
    printf '%s\n' "$GATE_DIR"
  else
    default_state_dir
  fi
}

D=$(state_dir) || exit 2
Q="$D/queue"
L="$D/lock"
EV="$D/events"
STAGE_RESULT=none
VERDICT_DONE=0
STAGE_LAUNCHING=0
LOCK_LINE=""
HELD_NAME=""
RUN_NAME=""
NAP_PID=""
BEAT_PID=""
ABORTING=0
PRE_BG=""
stage_pgid=""
stage_lstart=""

# shellcheck disable=SC2329 # called by age_of and by the suite through GATE_LIB_ONLY
now() { date +%s; }

# shellcheck disable=SC2329 # called by age_of
mtime_of() { # PATH -- modification time in epoch seconds
  local p=$1 v
  v=$(stat -c %Y "$p" 2>/dev/null)
  if is_num "$v"; then
    printf '%s\n' "$v"
    return 0
  fi
  v=$(stat -f %m "$p" 2>/dev/null)
  if is_num "$v"; then
    printf '%s\n' "$v"
    return 0
  fi
  die "neither stat -c %Y nor stat -f %m gave a modification time for $p"
}

# shellcheck disable=SC2329 # called by the suite through GATE_LIB_ONLY
age_of() { # PATH -- seconds since PATH was last modified
  local m n
  m=$(mtime_of "$1") || exit 2
  n=$(now)
  printf '%s\n' $((n - m))
}

# fake_value prints the fake seam's reading and succeeds, or fails when no fake seam is honoured.
fake_value() {
  local f v
  [ "$seam_on" = 1 ] || return 1
  f="${GATE_FAKE_THROTTLE_FILE:-}"
  if [ -n "$f" ] && [ -r "$f" ]; then
    v=""
    IFS= read -r v <"$f" || true
    printf '%s\n' "$v"
    return 0
  fi
  if [ -n "${GATE_FAKE_THROTTLE:-}" ]; then
    printf '%s\n' "$GATE_FAKE_THROTTLE"
    return 0
  fi
  return 1
}

# throttle_reading prints two lines: the word (THROTTLED, not throttled, unmeasurable (<why>)),
# then the source that answered (pmset, fake, none). The word comes from pmset's CPU_Speed_Limit and
# CPU_Scheduler_Limit only; the xcpm thermal levels never reach it (see thermal_levels).
throttle_reading() {
  local fv u out lim sched n=0 hot=0 v word src
  if fv=$(fake_value); then
    if [ "$fv" = 1 ]; then printf 'THROTTLED\nfake\n'; else printf 'not throttled\nfake\n'; fi
    return 0
  fi
  u=$(uname -s)
  if [ "$u" != Darwin ]; then
    printf 'unmeasurable (no probe for %s)\nnone\n' "$u"
    return 0
  fi
  out=$("$P_PMSET_CMD" -g therm 2>/dev/null)
  lim=$(printf '%s\n' "$out" | sed -n 's/.*CPU_Speed_Limit[^0-9]*\([0-9][0-9]*\).*/\1/p' | tail -n 1)
  sched=$(printf '%s\n' "$out" | sed -n 's/.*CPU_Scheduler_Limit[^0-9]*\([0-9][0-9]*\).*/\1/p' | tail -n 1)
  for v in "$lim" "$sched"; do
    if is_num "$v"; then
      n=$((n + 1))
      if [ "$v" -lt 100 ]; then hot=1; fi
    fi
  done
  if [ "$hot" = 1 ]; then
    word=THROTTLED
    src=pmset
  elif [ "$n" -gt 0 ]; then
    word="not throttled"
    src=pmset
  else
    word="unmeasurable (no pmset speed limit reading)"
    src=none
  fi
  printf '%s\n%s\n' "$word" "$src"
}

# thermal_levels prints the informational levels: line body; used only by cmd_status.
thermal_levels() {
  local cpu gpu io
  cpu=$("$P_SYSCTL_CMD" -n machdep.xcpm.cpu_thermal_level 2>/dev/null)
  gpu=$("$P_SYSCTL_CMD" -n machdep.xcpm.gpu_thermal_level 2>/dev/null)
  io=$("$P_SYSCTL_CMD" -n machdep.xcpm.io_thermal_level 2>/dev/null)
  is_num "$cpu" || cpu=-
  is_num "$gpu" || gpu=-
  is_num "$io" || io=-
  printf 'cpu=%s gpu=%s io=%s\n' "$cpu" "$gpu" "$io"
}

# throttled succeeds only for THROTTLED; unmeasurable counts as not throttled.
throttled() {
  local r word
  r=$(throttle_reading)
  word=${r%%$'\n'*}
  [ "$word" = THROTTLED ]
}

log_event() { # TEXT... -- append one line to the events file (never creates the state directory)
  { printf '%s %s\n' "$(now)" "$*" >>"$EV"; } 2>/dev/null || true
}

# shellcheck disable=SC2329 # called by the subcommands that take a state directory, and by the suite through GATE_LIB_ONLY
ensure_state_dir() { # create the state directory (mode 700) or exit 2 naming it
  if [ -d "$Q" ]; then return 0; fi
  (umask 077 && mkdir -p "$Q") 2>/dev/null || die "cannot create state directory $D"
  if [ ! -d "$Q" ]; then die "cannot create state directory $D"; fi
}

# tickets prints the ticket file names (<epoch>-<NAME>), oldest first, equal epochs by name.
tickets() {
  local t
  for t in "$Q"/*; do
    if [ -f "$t" ]; then printf '%s\n' "${t##*/}"; fi
  done | LC_ALL=C sort
}

ticket_of() { # NAME -- the first ticket file whose name part is NAME (prints nothing when there is none)
  local t
  while IFS= read -r t; do
    if [ -n "$t" ] && [ "${t#*-}" = "$1" ]; then
      printf '%s\n' "$t"
      return 0
    fi
  done < <(tickets)
  return 0
}

ticket_pid() { # TICKET-FILE -- the content of the ticket (a PID, or empty for join and prototype tickets)
  local p=""
  IFS= read -r p 2>/dev/null <"$Q/$1" || true
  printf '%s\n' "$p"
}

# claim_ticket is for wait and run only: it refuses a name held by a live process, then writes this
# process's PID as the ticket content. This is the one place a stored PID gets a liveness probe, and
# it fails safe: a reused PID can only cause a refusal, never two sessions sharing a ticket.
claim_ticket() { # NAME
  local name=$1 t p
  ensure_state_dir
  t=$(ticket_of "$name")
  if [ -n "$t" ]; then
    p=$(ticket_pid "$t")
    if is_num "$p" && [ "$((10#$p))" -gt 1 ] && [ "$((10#$p))" != "$$" ] && kill -0 "$p" 2>/dev/null; then
      die "NAME $name is held by the live process $p; pick another name"
    fi
    printf '%s\n' "$$" >"$Q/$t"
  else
    printf '%s\n' "$$" >"$Q/$(now)-$name"
  fi
}

touch_ticket() { # NAME -- touch the ticket without ever re-creating it
  local t
  t=$(ticket_of "$1")
  if [ -n "$t" ]; then touch -c "$Q/$t" 2>/dev/null || true; fi
}

drop_holder_ticket() { # NAME PID -- remove the queue/*-NAME ticket whose CONTENT equals PID (name AND content must match)
  local t
  is_num "${2:-}" || return 0
  while IFS= read -r t; do
    if [ -n "$t" ] && [ "${t#*-}" = "$1" ] && [ "$(ticket_pid "$t")" = "$2" ]; then rm -f "$Q/$t"; fi
  done < <(tickets)
}

owner_line() { # the first line of lock/owner (empty when there is none)
  local o=""
  IFS= read -r o 2>/dev/null <"$L/owner" || true
  printf '%s\n' "$o"
}

owner_field() { # N -- field N of the owner line
  owner_line | awk -v n="$1" 'NR == 1 { print $n }'
}

# held_by_ancestry WPID -- succeeds when WPID is a live process whose command line names heavy-gate.sh
# (the basename, never the substring heavy-gate: a checkout path may contain that) and an ancestor of
# this process within 64 parent steps. Read-only.
held_by_ancestry() {
  local wpid=${1:-} cmdline p i=0
  is_num "$wpid" || return 1
  [ "$((10#$wpid))" -gt 1 ] || return 1
  wpid=$((10#$wpid))
  cmdline=$(ps -ww -o command= -p "$wpid" 2>/dev/null)
  case "$cmdline" in *heavy-gate.sh*) ;; *) return 1 ;; esac
  p=$$
  while [ "$i" -lt 64 ]; do
    p=$(ps -o ppid= -p "$p" 2>/dev/null | tr -d ' ')
    is_num "$p" || return 1
    [ "$((10#$p))" -gt 1 ] || return 1
    if [ "$((10#$p))" = "$wpid" ]; then return 0; fi
    i=$((i + 1))
  done
  return 1
}

# held_by_env succeeds, and sets HELD_NAME (call it directly, never inside $( )), when the caller is
# nested inside a live run-mode turn: the token equals the owner line, or the owner's wrapper is an
# ancestor. Both sides of the token comparison must be non-empty. Read-only.
held_by_env() {
  local o
  HELD_NAME=""
  [ -d "$L" ] || return 1
  o=$(owner_line)
  [ -n "$o" ] || return 1
  [ "$(owner_field 4)" = run ] || return 1
  if [ -n "${HEAVY_GATE_HELD:-}" ] && [ "$HEAVY_GATE_HELD" = "$o" ]; then
    HELD_NAME=${o%% *}
    return 0
  fi
  if held_by_ancestry "$(owner_field 3)"; then
    HELD_NAME=${o%% *}
    return 0
  fi
  return 1
}

# shellcheck disable=SC2329 # called by the run lifecycle and by the suite through GATE_LIB_ONLY
lstart_of() { # PID -- the process start time, pinned to one locale and time zone
  LC_ALL=C TZ=UTC ps -o lstart= -p "$1" | tr -s ' '
}

# kill_stage verifies before it kills (PID reuse), then stops the recorded stage's process group.
# It reports through STAGE_RESULT (killed, none or reused), never prints, and always returns 0, so a
# find-nothing result never blocks the lock removal that follows it.
kill_stage() { # PGID LSTART
  local pgid=${1:-} lstart=${2:-} cur curpg mypg deadline
  STAGE_RESULT=none
  is_num "$pgid" || return 0
  if [ "$((10#$pgid))" -le 1 ]; then return 0; fi
  pgid=$((10#$pgid))
  mypg=$(ps -o pgid= -p $$ | tr -d ' ')
  if [ "$pgid" = "$mypg" ]; then return 0; fi
  read -r lstart <<<"$lstart"
  if ps -p "$pgid" >/dev/null 2>&1; then
    read -r cur <<<"$(lstart_of "$pgid")"
    curpg=$(ps -o pgid= -p "$pgid" | tr -d ' ')
    if [ "$cur" = "$lstart" ] && [ "$curpg" = "$pgid" ]; then
      STAGE_RESULT=killed
    else
      STAGE_RESULT=reused
    fi
  elif pgrep -g "$pgid" >/dev/null 2>&1; then
    STAGE_RESULT=killed
  fi
  if [ "$STAGE_RESULT" != killed ]; then return 0; fi
  kill -TERM -- "-$pgid" 2>/dev/null
  # the grace is a wall-clock deadline, polled every 0.2 s (counting passes of the loop made a 10 s grace take about
  # 13 s under load); the +1 absorbs the whole-second truncation, so the KILL never lands before GATE_GRACE has elapsed
  deadline=$(($(now) + 10#$P_GRACE + 1))
  while pgrep -g "$pgid" >/dev/null 2>&1 && [ "$(now)" -lt "$deadline" ]; do
    sleep 0.2
  done
  if pgrep -g "$pgid" >/dev/null 2>&1; then
    kill -KILL -- "-$pgid" 2>/dev/null
  fi
  return 0
}

# is_stale makes one observation of the lock: it prints "<rule> <age>" and succeeds when the lock is
# stale by rule a, b or c, and fails otherwise. No rule probes a stored PID.
is_stale() {
  local age mode
  if [ ! -d "$L" ]; then return 1; fi
  if [ ! -f "$L/owner" ]; then
    age=$(age_of "$L" 2>/dev/null) || return 1
    if [ "$age" -gt "$((10#$P_ORPHAN_LOCK_TTL))" ]; then
      printf 'a %s\n' "$age"
      return 0
    fi
    return 1
  fi
  mode=$(owner_field 4)
  age=$(age_of "$L/owner" 2>/dev/null) || return 1
  if [ "$mode" = run ]; then
    if [ "$age" -gt "$((10#$P_RUN_STALE))" ]; then
      printf 'b %s\n' "$age"
      return 0
    fi
  else
    if [ "$age" -gt "$((10#$P_LOCK_TTL))" ]; then
      printf 'c %s\n' "$age"
      return 0
    fi
  fi
  return 1
}

# remove_lock EXPECT: judge first, rename into trash only when the lock is stale or mine.
# EXPECT is empty (judge staleness only), name=NAME (mine when field 1 is NAME) or a whole owner line
# (mine when the owner line equals it).
remove_lock() {
  local expect=${1:-} p info rule="" mine=0 t o name stage_word
  if [ ! -d "$L" ]; then return 0; fi
  p=$(owner_line)
  if info=$(is_stale); then rule=${info%% *}; fi
  case "$expect" in
    '') ;;
    name=*) if [ -n "$p" ] && [ "${p%% *}" = "${expect#name=}" ]; then mine=1; fi ;;
    *) if [ -n "$p" ] && [ "$p" = "$expect" ]; then mine=1; fi ;;
  esac
  if [ -z "$rule" ] && [ "$mine" = 0 ]; then return 0; fi
  t=$(mktemp -d "$D/trash.XXXXXX" 2>/dev/null)
  if [ -z "$t" ] || [ ! -d "$t" ]; then return 0; fi
  if ! mv "$L" "$t/lock" 2>/dev/null; then
    rmdir "$t" 2>/dev/null
    return 0
  fi
  if [ -d "$t/lock" ]; then
    o=""
    IFS= read -r o 2>/dev/null <"$t/lock/owner" || true
    if [ "$o" != "$p" ]; then
      # No-clobber restore: mkdir is atomic and fails when $L exists, whereas a bare mv would move the
      # lock INTO an existing $L (no portable single mv refuses a directory target). The files then
      # move across; a failure at either step is logged and the lock stays in trash until the trash sweep deletes it after GATE_TICKET_TTL.
      if mkdir "$L" 2>/dev/null && mv "$t/lock"/* "$L"/ 2>/dev/null; then
        log_event LOCK_RESTORED
        rm -rf "$t"
      else
        log_event "LOCK_RESTORE_FAILED trash=${t##*/}"
      fi
      return 0
    fi
  fi
  rm -rf "$t"
  if [ -n "$rule" ] && [ "$mine" = 0 ]; then
    name=${p%% *}
    if [ -z "$name" ]; then name=-; fi
    stage_word=none
    if [ "$rule" = b ]; then stage_word=$STAGE_RESULT; fi
    log_event "STALE_LOCK_REMOVED name=$name rule=$rule stage=$stage_word"
  fi
  STAGE_RESULT=none
}

# prune removes stale tickets, sweeps old trash entries and removes a stale lock (rule b after its re-check).
prune() {
  local t e info rule m1 m2 tk_age tr_age stale_pgid stale_lstart stale_pid stale_name
  if [ ! -d "$D" ]; then return 0; fi
  while IFS= read -r t; do
    if [ -z "$t" ]; then continue; fi
    tk_age=$(age_of "$Q/$t" 2>/dev/null) || continue
    if [ "$tk_age" -gt "$((10#$P_TICKET_TTL))" ]; then rm -f "$Q/$t"; fi
  done < <(tickets)
  for e in "$D"/trash.*; do
    if [ ! -e "$e" ]; then continue; fi
    tr_age=$(age_of "$e" 2>/dev/null) || continue
    if [ "$tr_age" -gt "$((10#$P_TICKET_TTL))" ]; then rm -rf "$e"; fi
  done
  if ! info=$(is_stale); then return 0; fi
  rule=${info%% *}
  if [ "$rule" != b ]; then
    remove_lock ""
    return 0
  fi
  m1=$(mtime_of "$L/owner" 2>/dev/null) || return 0
  sleep "$((10#$P_TICK + 1))" &
  NAP_PID=$!
  wait "$NAP_PID" 2>/dev/null
  NAP_PID=""
  m2=$(mtime_of "$L/owner" 2>/dev/null) || m2=""
  if [ "$m1" != "$m2" ]; then return 0; fi
  STAGE_RESULT=none
  stale_pgid=""
  stale_lstart=""
  IFS=' ' read -r stale_pgid stale_lstart 2>/dev/null <"$L/stage" || true
  stale_pid=$(owner_field 3)
  stale_name=$(owner_field 1)
  kill_stage "$stale_pgid" "$stale_lstart"
  drop_holder_ticket "$stale_name" "$stale_pid"
  remove_lock ""
}

take_lock() { # NAME MODE -- succeeds when this call created the lock directory and wrote its owner line (kept in LOCK_LINE)
  mkdir "$L" 2>/dev/null || return 1
  LOCK_LINE="$1 $(now) $$ $2"
  printf '%s\n' "$LOCK_LINE" >"$L/owner"
}

head_name() { # the NAME of the oldest ticket
  local t
  t=$(tickets | sed -n 1p)
  printf '%s\n' "${t#*-}"
}

wait_turn() { # NAME MODE [MAXWAIT] -- block until NAME holds the lock in MODE; fails when MAXWAIT seconds pass first
  local name=$1 mode=$2 maxwait=${3:-} started nap left entry_ppid="" now_ppid tries=0
  # the entry reading must be numeric (a failed ps under load would read every later ppid as changed), as in beat_main
  while ! is_num "$entry_ppid" && [ "$tries" -lt 20 ]; do
    entry_ppid=$(ps -o ppid= -p $$ 2>/dev/null | tr -d ' ')
    tries=$((tries + 1))
    if ! is_num "$entry_ppid"; then sleep 0.1; fi
  done
  is_num "$entry_ppid" || die "wait_turn could not read its parent pid"
  started=$(now)
  while :; do
    # a waiter whose parent is gone (pid 1, or changed since entry) leaves the queue and never starts anything
    now_ppid=$(ps -o ppid= -p $$ 2>/dev/null | tr -d ' ')
    if is_num "$now_ppid" && { [ "$now_ppid" = 1 ] || [ "$now_ppid" != "$entry_ppid" ]; }; then
      drop_holder_ticket "$name" "$$"
      printf 'heavy-gate: parent gone while queued; leaving the queue\n' >&2
      emit_verdict "$name" 129
      exit 129
    fi
    if [ -z "$(ticket_of "$name")" ]; then
      printf '%s\n' "$$" >"$Q/$(now)-$name"
    else
      touch_ticket "$name"
    fi
    prune
    if [ "$(head_name)" = "$name" ] && ! throttled; then
      if take_lock "$name" "$mode"; then return 0; fi
    fi
    nap=$((10#$P_POLL))
    if [ -n "$maxwait" ]; then
      left=$((10#$maxwait - ($(now) - started)))
      if [ "$left" -le 0 ]; then return 1; fi
      if [ "$left" -lt "$nap" ]; then nap=$left; fi
    fi
    # sleep in the background and wait on it, so a trapped signal is handled at once and not after the nap
    sleep "$nap" &
    NAP_PID=$!
    wait "$NAP_PID" 2>/dev/null
    NAP_PID=""
  done
}

# beat_main is the heartbeat B, re-executed by run as `__beat NAME WPID PGID LSTART` (never a subshell: on
# bash 3.2 a subshell cannot name its own pid). WPID is never used as a liveness test; B's own parent
# is the wrapper, so the parent-of-wrapper watch reads B's own ppid. B owns nothing but the owner line it
# read at start (written by W before B existed).
beat_main() { # NAME WPID PGID LSTART
  local name=${1:-} wpid=${2:-} pgid=${3:-} lstart=${4:-} mine my_ppid cur_ppid w_start w_cur tries=0
  valid_name "$name" || die "invalid NAME [$name] for the heartbeat"
  trap 'if [ -n "$NAP_PID" ]; then kill "$NAP_PID" 2>/dev/null; fi; exit 0' TERM
  # the first reading must be numeric (a failed ps under load would read every later ppid as changed)
  my_ppid=""
  while ! is_num "$my_ppid" && [ "$tries" -lt 20 ]; do
    my_ppid=$(ps -o ppid= -p $$ 2>/dev/null | tr -d ' ')
    tries=$((tries + 1))
    if ! is_num "$my_ppid"; then sleep 0.1; fi
  done
  is_num "$my_ppid" || die "the heartbeat could not read its parent pid"
  mine=$(owner_line)
  w_start=$(ps -o ppid= -p "$my_ppid" 2>/dev/null | tr -d ' ')
  while :; do
    cur_ppid=$(ps -o ppid= -p $$ | tr -d ' ')
    if is_num "$cur_ppid" && { [ "$cur_ppid" = 1 ] || [ "$cur_ppid" != "$my_ppid" ]; }; then
      kill_stage "$pgid" "$lstart"
      drop_holder_ticket "$name" "$wpid"
      if [ -n "$mine" ] && [ "$(owner_line)" = "$mine" ]; then
        remove_lock "$mine"
      fi
      exit 0
    fi
    w_cur=$(ps -o ppid= -p "$my_ppid" 2>/dev/null | tr -d ' ')
    # the TERM is deliberately re-sent on every tick while the wrapper's parent stays changed (a stage that ignores TERM
    # must still be reached); the ABORTING guard in run_abort is what makes the repeats harmless
    if is_num "$w_start" && is_num "$w_cur"; then
      if [ "$w_cur" = 1 ] || [ "$w_cur" != "$w_start" ]; then kill -TERM "$my_ppid" 2>/dev/null; fi
    fi
    if [ -n "$mine" ] && [ "$(owner_line)" = "$mine" ]; then touch -c "$L/owner" 2>/dev/null || true; fi
    touch_ticket "$name"
    sleep "$((10#$P_TICK))" &
    NAP_PID=$!
    wait "$NAP_PID" 2>/dev/null
    NAP_PID=""
  done
}

# stop_beat ends this run's heartbeat and reaps it, so a release is never followed by a heartbeat touching state.
stop_beat() {
  if [ -z "$BEAT_PID" ]; then return 0; fi
  kill -TERM "$BEAT_PID" 2>/dev/null
  wait "$BEAT_PID" 2>/dev/null
  BEAT_PID=""
}

need_name() { # NAME -- exit 2 unless NAME passes the allowlist
  valid_name "${1:-}" || die "invalid NAME [${1:-}]: use letters, digits, dot, underscore and dash, and no leading dot"
}

cmd_join() { # NAME -- take a ticket; an empty one, with no duplicate-name refusal
  local t
  need_name "$1"
  if held_by_env; then
    die "$1 is nested inside $HELD_NAME; a wait here would wait for its own parent"
  fi
  ensure_state_dir
  t=$(ticket_of "$1")
  if [ -n "$t" ]; then
    touch -c "$Q/$t" 2>/dev/null || true
  else
    : >"$Q/$(now)-$1"
  fi
}

cmd_wait() { # NAME -- take the lock in mode wait and print GO NAME; the caller must release
  need_name "$1"
  if held_by_env; then
    die "$1 is nested inside $HELD_NAME; a wait here would wait for its own parent"
  fi
  claim_ticket "$1"
  wait_turn "$1" wait
  printf 'GO %s\n' "$1"
}

emit_verdict() { # NAME RC -- print the verdict line once; a later call prints nothing
  if [ "$VERDICT_DONE" = 1 ]; then return 0; fi
  VERDICT_DONE=1
  printf 'HEAVY_GATE_DONE %s rc=%s\n' "$1" "$2" >&2
}

# run_release frees what this run owns and nothing else: its own lock (the whole owner line it wrote,
# kept in LOCK_LINE, must equal the lock's) and its own ticket (name and content match). It is
# idempotent and never fails.
run_release() {
  stop_beat
  if [ ! -d "$D" ]; then return 0; fi
  # ticket first, then lock: the lock still keeps every other head out until it goes, and a release cut short
  # between the two leaves a stale lock a pruner removes, never a ticket nobody owns at the head of the queue
  drop_holder_ticket "$RUN_NAME" "$$"
  if [ -n "$LOCK_LINE" ] && [ -d "$L" ] && [ "$(owner_line)" = "$LOCK_LINE" ]; then
    remove_lock "$LOCK_LINE"
  fi
  LOCK_LINE=""
  return 0
}

# shellcheck disable=SC2329 # the TERM, INT and HUP traps of cmd_run call it
run_abort() { # SIGNUM -- stop the stage, release, print the verdict, exit 128+SIGNUM
  local rc=$((128 + $1)) last_bg
  # a later signal must not nest a second abort: bash 5.3 re-enters a running trap, and the nested kill_stage would
  # restart the grace, so the outer one never reached its KILL step; bash 3.2 drops the repeat on its own
  if [ "$ABORTING" = 1 ]; then return 0; fi
  ABORTING=1
  if [ -n "$NAP_PID" ]; then kill "$NAP_PID" 2>/dev/null; fi
  # a signal after the stage was forked and before its pgid was recorded: the fork is the last background job,
  # but only when $! differs from the job recorded before the launch (that one has exited and its pid may be reused)
  if [ "$STAGE_LAUNCHING" = 1 ] && [ -z "$stage_pgid" ]; then
    set +u
    last_bg=$!
    set -u
    if [ -n "$last_bg" ] && [ "$last_bg" != "$PRE_BG" ]; then stage_pgid=$last_bg; fi
  fi
  if [ -n "$stage_pgid" ] && [ -z "$stage_lstart" ]; then stage_lstart=$(lstart_of "$stage_pgid"); fi
  kill_stage "$stage_pgid" "$stage_lstart"
  run_release
  emit_verdict "$RUN_NAME" "$rc"
  exit "$rc"
}

# shellcheck disable=SC2329 # the EXIT trap of cmd_run calls it
run_exit() { # RC -- the EXIT trap: release and print the verdict when no earlier path did
  run_release
  emit_verdict "$RUN_NAME" "$1"
}

cmd_run() { # NAME [--max-wait SECS] -- CMD... -- wait for the turn, run CMD as the stage, release, exit with its status
  local name=${1:-} maxwait="" rc stage_pid
  if [ "$#" -lt 1 ]; then
    usage_text >&2
    exit 2
  fi
  shift
  need_name "$name"
  if [ "${1:-}" = --max-wait ]; then
    is_num "${2:-}" || die "--max-wait needs a non-negative integer, got [${2:-}]"
    maxwait=$2
    shift 2
  fi
  if [ "${1:-}" != -- ] || [ "$#" -lt 2 ]; then
    usage_text >&2
    exit 2
  fi
  shift
  if held_by_env; then
    # nested inside a live turn: no ticket, no lock, no heartbeat (LOCK_LINE stays empty, so nothing is released)
    printf 'heavy-gate: nested inside %s; running without a second turn\n' "$HELD_NAME" >&2
    "$@"
    rc=$?
    emit_verdict "$name" "$rc"
    exit "$rc"
  fi
  RUN_NAME=$name
  trap 'run_abort 15' TERM
  trap 'run_abort 2' INT
  trap 'run_abort 1' HUP
  trap 'run_exit $?' EXIT
  claim_ticket "$name"
  if ! wait_turn "$name" run "$maxwait"; then
    drop_holder_ticket "$name" "$$"
    emit_verdict "$name" 75
    exit 75
  fi
  export HEAVY_GATE_HELD="$LOCK_LINE"
  set +u
  PRE_BG=$!
  set -u
  STAGE_LAUNCHING=1
  set -m
  if [ -t 0 ]; then
    printf 'heavy-gate: stdin is a terminal; the stage gets /dev/null\n' >&2
    "$@" </dev/null &
  else
    "$@" <&0 &
  fi
  stage_pgid=$!
  STAGE_LAUNCHING=0
  set +m
  stage_pid=$stage_pgid
  stage_lstart=$(lstart_of "$stage_pid")
  printf '%s %s\n' "$stage_pgid" "$stage_lstart" >"$L/stage"
  "${BASH:-bash}" "$0" __beat "$name" "$$" "$stage_pgid" "$stage_lstart" </dev/null >/dev/null 2>&1 &
  BEAT_PID=$!
  rc=0
  wait "$stage_pid" || rc=$?
  run_release
  emit_verdict "$name" "$rc"
  exit "$rc"
}

release_cmd() { # NAME -- free the lock when NAME holds it, and remove NAME's tickets
  local t
  need_name "$1"
  if [ ! -d "$D" ]; then return 0; fi
  remove_lock "name=$1"
  while IFS= read -r t; do
    if [ -n "$t" ] && [ "${t#*-}" = "$1" ]; then rm -f "$Q/$t"; fi
  done < <(tickets)
}

renew_cmd() { # NAME -- touch the owner file (when NAME holds the lock) and the ticket
  need_name "$1"
  if [ ! -d "$D" ]; then return 0; fi
  if [ "$(owner_field 1)" = "$1" ]; then touch -c "$L/owner" 2>/dev/null || true; fi
  touch_ticket "$1"
}

cmd_status() {
  local owner r word src t n=0 info="" line
  if [ ! -d "$D" ]; then
    printf 'queue: (no state directory)\n'
  else
    if [ -d "$Q" ]; then
      for t in "$Q"/*; do
        if [ -e "$t" ]; then n=$((n + 1)); fi
      done
    fi
    printf 'queue: %s waiting\n' "$n"
    if [ -d "$Q" ]; then
      for t in "$Q"/*; do
        if [ -e "$t" ]; then printf '  %s\n' "${t##*/}"; fi
      done
    fi
  fi
  if [ -d "$L" ]; then
    owner=$(owner_line)
    if [ -n "$owner" ]; then line="lock: $owner"; else line='lock: held (no owner file)'; fi
    if info=$(is_stale); then
      line="$line (stale by rule 1${info%% *}: untouched ${info#* } s; not pruned)"
    fi
    printf '%s\n' "$line"
  else
    printf 'lock: free\n'
  fi
  r=$(throttle_reading)
  word=${r%%$'\n'*}
  src=${r#*$'\n'}
  printf 'throttle: %s\n' "$word"
  printf 'source: %s\n' "$src"
  printf 'levels: %s\n' "$(thermal_levels)"
  if seam_active; then printf 'seam: fake\n'; fi
  printf 'params: tick=%s grace=%s run_stale=%s ticket_ttl=%s lock_ttl=%s orphan_lock_ttl=%s poll=%s\n' \
    "$P_TICK" "$P_GRACE" "$P_RUN_STALE" "$P_TICKET_TTL" "$P_LOCK_TTL" "$P_ORPHAN_LOCK_TTL" "$P_POLL"
}

# shellcheck disable=SC2317 # `return` ends a sourced run, `exit` the run as a script
if [ "${GATE_LIB_ONLY:-}" = 1 ] && [ -n "${GATE_DIR:-}" ]; then return 0 2>/dev/null || exit 0; fi

cmd="${1:-}"
case "$cmd" in
  throttled)
    throttled
    exit $?
    ;;
  status)
    cmd_status
    exit 0
    ;;
  --help | -h)
    help_text
    exit 0
    ;;
  held)
    if [ "$#" -ne 1 ]; then
      usage_text >&2
      exit 2
    fi
    if held_by_env; then
      printf '%s\n' "$HELD_NAME"
      exit 0
    fi
    exit 1
    ;;
  join | wait | release | renew)
    if [ "$#" -ne 2 ]; then
      usage_text >&2
      exit 2
    fi
    case "$cmd" in
      join) cmd_join "$2" ;;
      wait) cmd_wait "$2" ;;
      release) release_cmd "$2" ;;
      renew) renew_cmd "$2" ;;
    esac
    exit 0
    ;;
  run)
    shift
    cmd_run "$@"
    ;;
  __beat)
    shift
    if [ "$#" -ne 4 ]; then
      usage_text >&2
      exit 2
    fi
    beat_main "$@"
    exit 0
    ;;
  *)
    usage_text >&2
    exit 2
    ;;
esac

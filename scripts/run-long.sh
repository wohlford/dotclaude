#!/usr/bin/env bash
set -uo pipefail

# Script: run-long.sh
# Purpose: Launch a long job in the background and record its real exit status inside the artifact
# Usage: run-long.sh --out <path> [--label <text>] [--force] -- <command> [args...]
#        run-long.sh --status <path>
#        run-long.sh --wait <path> [--interval <seconds>]
#
# Why this exists. A check that outruns the tool timeout has to be backgrounded, and a
# backgrounded run is where "no FAIL in the output" stops meaning "passed": a killed run prints a
# PREFIX of good-looking lines, never reaches its summary, and the harness cheerfully reports
# "completed (exit code 0)" for the launcher. Four wrappers were hand-written for this in one
# session, three of them byte-equivalent, and the fourth still got it wrong — an `&` was added
# INSIDE a command the harness was already backgrounding, so the launcher exited instantly and a
# sweep that had run 4 checks of 15 read as a clean pass.
#
# The repair is not "remember not to do that". It is to make the launcher's exit status carry no
# information at all, so it cannot be misread as a verdict:
#
#   * The launcher owns the backgrounding. Callers never add `&` and never need a background flag.
#   * The launcher ALWAYS exits 0 once the job is launched, whatever the job goes on to do.
#   * The only verdict is `RUN_LONG_EXIT_STATUS=<rc>`, written into the artifact by the job itself
#     as its final act. Its ABSENCE is therefore the signal that the run died — the one thing a
#     prefix of PASS lines cannot fake.
#   * `--status` turns that into one of three verdicts (DONE / RUNNING / DIED) so the caller never
#     has to remember which grep distinguishes "still going" from "killed".
#   * `--wait` blocks until a TERMINAL verdict. Callers hand-rolled that loop three times in the
#     week this tool shipped, and the predicate is the easy thing to get wrong: it must break on
#     DIED as well as DONE, or a killed job hangs the waiter forever. One implementation, here.
#
# It also stamps the SUBJECT. A long check grades the tree it was LAUNCHED against; by the time
# the verdict is read that tree may be gone, and nothing in the output would say so — a stale PASS
# is byte-identical to a current one. Measured twice in one session against a ~15-minute sweep
# whose fast static checks finish in the first seconds. So the launch-time working state is
# fingerprinted into the header and compared back at read time. It is a WARNING, never a failure:
# reading a verdict and then continuing to edit is normal, and the warning's only job is to say
# that this verdict does not cover the tree you have now.
#
# There is deliberately NO default for --out. A default output path makes every run of a tool a
# writer of real state, which this repo has measured biting in both directions (synthetic records
# accumulating in a real log; an artifact landing where the next step's clean-tree check trips
# over it). Name the destination or get a usage error.

readonly BEGIN_PREFIX='RUN_LONG_BEGIN'
readonly STATUS_PREFIX='RUN_LONG_EXIT_STATUS='
readonly SUBJECT_PREFIX='RUN_LONG_SUBJECT='
readonly SUBJECT_ROOT_PREFIX='RUN_LONG_SUBJECT_ROOT='
readonly DEFAULT_INTERVAL=15

usage() {
  cat <<'EOF'
Usage: run-long.sh --out <path> [--label <text>] [--force] -- <command> [args...]
       run-long.sh --status <path>
       run-long.sh --wait <path> [--interval <seconds>]
       run-long.sh --help

Launch mode:
  --out <path>     where to write the artifact (REQUIRED — there is no default, by design)
  --label <text>   free text recorded in the artifact header
  --force          replace an existing artifact instead of refusing
  --               everything after this is the command to run

  Exits 0 as soon as the job is launched. That status describes the LAUNCH, never the work.
  Do not add `&` and do not launch this through a background flag — it backgrounds itself.

Status mode:
  --status <path>  report on a previously launched run

  RESULT: DONE rc=<n>   exit 0 if n is 0, else 1
  RESULT: RUNNING       exit 3
  RESULT: DIED          exit 4 — killed before it recorded a status; NOT a pass

Wait mode:
  --wait <path>        block until the run reaches a TERMINAL state, then report
  --interval <secs>    poll cadence, a positive whole number (default 15)

  Exits with the status codes above MINUS RUNNING: 0, 1 or 4. It breaks on DIED as well as
  on DONE — a hand-rolled `until [ $? -eq 0 ]` hangs forever on a job that was killed.

Both read modes also report SUBJECT: whether the git working tree has MOVED since the run was
launched, i.e. whether the verdict still describes the tree you have now. Best-effort (silent
outside a git repo) and a WARNING only — it never changes the exit code.
EOF
}

die() {
  printf 'run-long.sh: %s\n' "$1" >&2
  usage >&2
  exit 2
}

out=""
label=""
force=0
status_path=""
mode="launch"
interval="$DEFAULT_INTERVAL"
interval_set=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h | --help)
      usage
      exit 0
      ;;
    --out)
      [[ $# -ge 2 ]] || die '--out needs a path'
      out="$2"
      shift 2
      ;;
    --label)
      [[ $# -ge 2 ]] || die '--label needs a value'
      label="$2"
      shift 2
      ;;
    --force)
      force=1
      shift
      ;;
    --status)
      [[ $# -ge 2 ]] || die '--status needs a path'
      status_path="$2"
      mode="status"
      shift 2
      ;;
    --wait)
      [[ $# -ge 2 ]] || die '--wait needs a path'
      status_path="$2"
      mode="wait"
      shift 2
      ;;
    --interval)
      [[ $# -ge 2 ]] || die '--interval needs a value'
      interval="$2"
      interval_set=1
      shift 2
      ;;
    --)
      shift
      break
      ;;
    *)
      die "unrecognized argument: $1"
      ;;
  esac
done

if [[ "$interval_set" -eq 1 ]]; then
  [[ "$mode" == "wait" ]] || die '--interval applies to --wait only'
  [[ "$interval" =~ ^[1-9][0-9]*$ ]] ||
    die "--interval needs a positive whole number of seconds: $interval"
fi

# ---------- the subject: which tree did this verdict actually grade? ----------

# Fingerprint the git working state under $1. Best-effort by contract: prints nothing when the
# subject is not a git tree, so a caller outside a repo gets no stamp rather than a usage error.
#
# `git rev-parse HEAD` alone would be WORSE than nothing. The tree under a long check is normally
# dirty — uncommitted work is usually the entire reason for running it — so a HEAD-only stamp
# reports "unchanged" across exactly the edits this exists to catch.
subject_stamp() { # repo-root
  local root="$1" hasher=""
  git -C "$root" rev-parse --git-dir > /dev/null 2>&1 || return 0
  if command -v shasum > /dev/null 2>&1; then
    hasher="shasum"
  elif command -v sha1sum > /dev/null 2>&1; then
    hasher="sha1sum"
  else
    return 0
  fi
  {
    git -C "$root" rev-parse HEAD
    git -C "$root" diff HEAD --no-ext-diff
    git -C "$root" status --porcelain
  } 2> /dev/null | "$hasher" | awk '{print $1}'
}

# Every branch prints SOMETHING. Silence would be indistinguishable from "checked, and unchanged",
# which is the false-clean this whole feature exists to remove — so "no subject was recorded" and
# "the subject cannot be re-read" are stated out loud rather than left to an absent line.
subject_report() { # artifact
  local art="$1" recorded root now
  recorded="$(sed -n "s/^${SUBJECT_PREFIX}//p" "$art" | head -1)"
  root="$(sed -n "s/^${SUBJECT_ROOT_PREFIX}//p" "$art" | head -1)"

  if [[ -z "$recorded" || "$recorded" == "none" ]]; then
    printf 'SUBJECT: not recorded — the launch was outside a git repo, so drift cannot be judged\n'
    return 0
  fi

  now=""
  [[ -n "$root" && -d "$root" ]] && now="$(subject_stamp "$root")"

  if [[ -z "$now" ]]; then
    printf 'SUBJECT: UNREADABLE — cannot re-read %s, so this verdict cannot be tied to a tree\n' \
      "${root:-?}"
  elif [[ "$now" == "$recorded" ]]; then
    printf 'SUBJECT: unchanged since launch (%s)\n' "${recorded:0:12}"
  else
    printf 'SUBJECT: MOVED since launch — this verdict does NOT cover your current tree\n'
    printf '         launched %s, now %s, in %s\n' "${recorded:0:12}" "${now:0:12}" "$root"
  fi
}

# ---------- read modes: --status and --wait ----------

# classify() and report() are deliberately ONE implementation serving both modes. Which states are
# TERMINAL is the single thing callers kept getting wrong when they hand-rolled this loop — an
# `until [ $? -eq 0 ]` never exits on a job that died — so the predicate must not exist in two
# places that can drift apart.
CLASS=""
JOB_RC=""
JOB_PID=""

classify() { # artifact -> sets CLASS to done | running | died
  local art="$1"
  CLASS=""
  JOB_RC=""
  JOB_PID=""

  if grep -q "^${STATUS_PREFIX}" "$art"; then
    JOB_RC="$(sed -n "s/^${STATUS_PREFIX}\\([0-9][0-9]*\\)\$/\\1/p" "$art" | tail -1)"
    CLASS="done"
    return 0
  fi

  JOB_PID="$(sed -n "s/^${BEGIN_PREFIX} pid=\\([0-9][0-9]*\\).*/\\1/p" "$art" | head -1)"
  if [[ -n "$JOB_PID" ]] && kill -0 "$JOB_PID" 2>/dev/null; then
    CLASS="running"
  else
    CLASS="died"
  fi
}

report() { # artifact -> prints the verdict, returns its exit code
  local art="$1"
  case "$CLASS" in
    done)
      printf 'RESULT: DONE rc=%s artifact=%s\n' "${JOB_RC:-?}" "$art"
      subject_report "$art"
      [[ "$JOB_RC" == "0" ]] && return 0
      return 1
      ;;
    running)
      printf 'RESULT: RUNNING pid=%s artifact=%s\n' "$JOB_PID" "$art"
      subject_report "$art"
      return 3
      ;;
    *)
      printf 'RESULT: DIED pid=%s artifact=%s\n' "${JOB_PID:-unknown}" "$art"
      printf '        the job recorded no exit status, so it was killed before finishing.\n'
      printf '        Whatever it printed is a PREFIX — absence of FAIL is not a pass.\n'
      subject_report "$art"
      return 4
      ;;
  esac
}

if [[ "$mode" == "status" || "$mode" == "wait" ]]; then
  [[ -f "$status_path" ]] || die "no such artifact: $status_path"

  classify "$status_path"

  # RUNNING is the ONLY non-terminal state. Looping on "not DONE" would hang forever on a job that
  # was killed, which is the trap this flag exists to take away from the call site.
  if [[ "$mode" == "wait" ]]; then
    while [[ "$CLASS" == "running" ]]; do
      sleep "$interval"
      classify "$status_path"
    done
  fi

  report "$status_path"
  rc=$?
  exit "$rc"
fi

# ---------- launch mode ----------

[[ -n "$out" ]] || die 'no --out given; this tool has no default output path, by design'
[[ $# -ge 1 ]] || die 'no command given (put it after --)'

if [[ -e "$out" && "$force" -ne 1 ]]; then
  die "artifact already exists: $out (pass --force to replace it)"
fi

out_dir="$(dirname "$out")"
mkdir -p "$out_dir" || die "cannot create directory: $out_dir"
: > "$out" || die "cannot write artifact: $out"

# Capture the subject BEFORE the job starts, so the stamp describes the tree the job is about to
# grade. The root is recorded alongside the hash because --status may run from a different
# directory entirely: both sides then resolve through `git -C "$root"`, and a comparison whose two
# sides resolve differently could never match. Costs one git invocation at launch.
subject_root="$(git rev-parse --show-toplevel 2>/dev/null)"
subject_hash=""
[[ -n "$subject_root" ]] && subject_hash="$(subject_stamp "$subject_root")"
subject_block="$(printf '%s%s\n%s%s' \
  "$SUBJECT_ROOT_PREFIX" "$subject_root" \
  "$SUBJECT_PREFIX" "${subject_hash:-none}")"

# The job writes its own header so the pid in the artifact is authoritative, and appends the
# status trailer as its last act. Nothing else may write to the artifact, or the trailer stops
# being the last line. Every value the child needs is passed as an ARGUMENT rather than spliced
# into the quoted body, so the body stays a single unexpanded literal.
# shellcheck disable=SC2016
nohup bash -c '
  art=$1
  tag=$2
  begin=$3
  trailer=$4
  subject=$5
  shift 5
  {
    printf "%s pid=%d label=%s\n" "$begin" "$$" "$tag"
    printf "%s\n" "$subject"
    printf "RUN_LONG_COMMAND:"
    for a in "$@"; do printf " %q" "$a"; done
    printf "\n----- output -----\n"
  } >> "$art"

  "$@" >> "$art" 2>&1
  rc=$?

  # A command whose output lacks a trailing newline would otherwise swallow the trailer onto its
  # last line, and the trailer has to stand alone to be greppable and to be `tail -1`.
  if [ -n "$(tail -c 1 "$art")" ]; then printf "\n" >> "$art"; fi
  printf "%s%d\n" "$trailer" "$rc" >> "$art"
' _ "$out" "$label" "$BEGIN_PREFIX" "$STATUS_PREFIX" "$subject_block" "$@" > /dev/null 2>&1 &
pid=$!

# Return only once the header is on disk, so a caller that immediately runs --status cannot race
# the job into a false DIED. Measured: this loop spends exactly 1 iteration (~20ms) on every
# launch, so the header really is absent when the launcher would otherwise return. The window is
# too small for a separate --status process to observe (40/40 probe runs read RUNNING with this
# guard removed), which is why no test row covers it — it is a flagged untestable-timing guard,
# kept because the ordering holds by accident rather than by construction.
tries=0
while [[ $tries -lt 250 ]]; do
  grep -q "^${BEGIN_PREFIX}" "$out" 2>/dev/null && break
  sleep 0.02
  tries=$((tries + 1))
done

printf 'launched pid=%s\n' "$pid"
printf 'artifact: %s\n' "$out"
printf 'NOTE: this exit status describes the LAUNCH only.\n'
printf '      Read the verdict with: run-long.sh --status %s\n' "$out"
exit 0

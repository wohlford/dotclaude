#!/usr/bin/env bash
set -uo pipefail

# Script: run-long.sh
# Purpose: Launch a long job in the background and record its real exit status inside the artifact
# Usage: run-long.sh --out <path> [--label <text>] [--force] -- <command> [args...]
#        run-long.sh --status <path>
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
#
# There is deliberately NO default for --out. A default output path makes every run of a tool a
# writer of real state, which this repo has measured biting in both directions (synthetic records
# accumulating in a real log; an artifact landing where the next step's clean-tree check trips
# over it). Name the destination or get a usage error.

readonly BEGIN_PREFIX='RUN_LONG_BEGIN'
readonly STATUS_PREFIX='RUN_LONG_EXIT_STATUS='

usage() {
  cat <<'EOF'
Usage: run-long.sh --out <path> [--label <text>] [--force] -- <command> [args...]
       run-long.sh --status <path>
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
    --)
      shift
      break
      ;;
    *)
      die "unrecognized argument: $1"
      ;;
  esac
done

# ---------- status mode ----------

if [[ "$mode" == "status" ]]; then
  [[ -f "$status_path" ]] || die "no such artifact: $status_path"

  if grep -q "^${STATUS_PREFIX}" "$status_path"; then
    rc="$(sed -n "s/^${STATUS_PREFIX}\\([0-9][0-9]*\\)\$/\\1/p" "$status_path" | tail -1)"
    printf 'RESULT: DONE rc=%s artifact=%s\n' "${rc:-?}" "$status_path"
    [[ "$rc" == "0" ]] && exit 0
    exit 1
  fi

  pid="$(sed -n "s/^${BEGIN_PREFIX} pid=\\([0-9][0-9]*\\).*/\\1/p" "$status_path" | head -1)"
  if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
    printf 'RESULT: RUNNING pid=%s artifact=%s\n' "$pid" "$status_path"
    exit 3
  fi

  printf 'RESULT: DIED pid=%s artifact=%s\n' "${pid:-unknown}" "$status_path"
  printf '        the job recorded no exit status, so it was killed before finishing.\n'
  printf '        Whatever it printed is a PREFIX — absence of FAIL is not a pass.\n'
  exit 4
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
  shift 4
  {
    printf "%s pid=%d label=%s\n" "$begin" "$$" "$tag"
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
' _ "$out" "$label" "$BEGIN_PREFIX" "$STATUS_PREFIX" "$@" > /dev/null 2>&1 &
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

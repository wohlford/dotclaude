#!/usr/bin/env bash
set -uo pipefail

# Script: run-long.sh
# Purpose: Launch a long job in the background and record its real exit status inside the artifact
# Usage: run-long.sh --out <path> [--label <text>] [--expect <ere>] [--force] -- <command> [args...]
#        run-long.sh --status <path> [--expect <ere>]
#        run-long.sh --wait <path> [--interval <seconds>] [--expect <ere>]
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
readonly EXPECT_PREFIX='RUN_LONG_EXPECT='
readonly DEFAULT_INTERVAL=15

usage() {
  cat <<'EOF'
Usage: run-long.sh --out <path> [--label <text>] [--expect <ere>] [--force] -- <command> [args...]
       run-long.sh --status <path> [--expect <ere>]
       run-long.sh --wait <path> [--interval <seconds>] [--expect <ere>]
       run-long.sh --help

Launch mode:
  --out <path>     where to write the artifact (REQUIRED — there is no default, by design)
  --label <text>   free text recorded in the artifact header
  --expect <ere>   a line the job's OUTPUT must contain. Recorded INTO the artifact, so every
                   later --status/--wait enforces it without being asked — including a session
                   that did not launch it and cannot know what verdict to expect.
                   Match the verdict's SHAPE, not its passing value: '^RESULT: (PASS|FAIL)',
                   never '^RESULT: PASS'. This answers "did the run reach a verdict", never
                   "was the verdict good".
  --force          replace an existing artifact instead of refusing
  --               everything after this is the command to run

  Exits 0 as soon as the job is launched. That status describes the LAUNCH, never the work.
  Do not add `&` and do not launch this through a background flag — it backgrounds itself.

Status mode:
  --status <path>  report on a previously launched run
  --expect <ere>   additionally require this pattern. It can only ADD to a pattern recorded at
                   launch, never replace one — otherwise any reader could clear an
                   INDETERMINATE by supplying a pattern they know matches.

  RESULT: DONE rc=<n>     exit 0 if n is 0, else 1
  RESULT: RUNNING         exit 3
  RESULT: DIED            exit 4 — killed before it recorded a status; NOT a pass
  RESULT: INDETERMINATE   exit 5 — the run FINISHED but produced no matching line. NOT a pass,
                          and NOT a failure of the work: it means nothing verified the work.

Wait mode:
  --wait <path>        block until the run reaches a TERMINAL state, then report
  --interval <secs>    poll cadence, a positive whole number (default 15)

  Exits with the status codes above MINUS RUNNING: 0, 1, 4 or 5. It breaks on DIED and
  INDETERMINATE as well as on DONE — a hand-rolled `until [ $? -eq 0 ]` hangs forever on a job
  that was killed.

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
expect=""
expect_set=0
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
    --expect)
      [[ $# -ge 2 ]] || die '--expect needs a pattern'
      expect="$2"
      expect_set=1
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

# --label is validated HERE and not only for tidiness: it is written RAW into the header, so a
# newline in it forges a `----- output -----` marker. Measured end-to-end — a label of
# $'x\n----- output -----\nRESULT: PASS' makes the INJECTED marker the first one, the "output
# section" becomes the rest of the header, and the canonical pattern clears an inert run. Refusing
# newlines in the pattern while allowing them here protected the format and left the scope open.
[[ "$label" != *$'\n'* ]] ||
  die '--label cannot contain a newline: it is written into the header, where one would forge the output marker'

if [[ "$expect_set" -eq 1 ]]; then
  [[ -n "$expect" ]] ||
    die '--expect needs a non-empty pattern: an empty one matches everything, so it would clear every run'
  [[ "$expect" != *$'\n'* ]] ||
    die '--expect cannot contain a newline: it is recorded as a single header line'
  # One probe against an EMPTY input, two refusals, on whichever matcher is installed:
  #   rc 0 -> matches the empty line, therefore every line — a stamp. Which patterns fall in this
  #           class is MATCHER-DEPENDENT, which is exactly why this asks the installed matcher
  #           instead of hard-coding a list. Measured on GNU grep 3.12, the matcher a script here
  #           actually resolves: '^', '$', '^$', 'x*' and '.*' are ALL refused.
  #           Do not re-derive that list from an interactive shell — this session's `grep` is a
  #           harness-injected shell FUNCTION shimming to ugrep, which answers differently ('x*'
  #           and '.*' admitted). The function is not exported, so scripts get GNU grep; a probe
  #           run at the prompt grades the shim rather than the environment.
  #   rc 2 -> does not compile. Without this the bad pattern is RECORDED and then fails forever
  #           at read time, reported as "produced no matching line", sending the reader off to
  #           debug the job instead of the pattern.
  # Only rc 1 — compiles, does not match an empty line — is admissible.
  grep -qE -e "$expect" <<< "" 2> /dev/null
  case $? in
    0) die "--expect matches an empty line, so it would clear every run: $expect" ;;
    2) die "--expect is not a valid extended regular expression: $expect" ;;
  esac
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
EXPECT_ACTIVE=0
EXPECT_UNMATCHED=""

# The patterns a finished run must satisfy: the one recorded at launch (which binds every reader,
# including a later session that cannot know what to expect) AND the one given on this command
# line. Emitting them newline-separated is safe precisely BECAUSE a pattern containing a newline
# is refused at parse time — the two rules hold each other up.
#
# `head -1` is load-bearing, not habit: the job's own OUTPUT may contain a line beginning with
# this prefix, and the header is written before the command runs, so first-wins pins the read to
# the header.
expect_patterns() { # artifact
  local art="$1" header recorded
  # Read the recorded pattern ONLY from above the first marker. A whole-file read adopts the first
  # RUN_LONG_EXPECT= line ANYWHERE, so when nothing was recorded a job that prints one
  # MANUFACTURES a requirement — measured: an output line `RUN_LONG_EXPECT=zzz-never` was adopted
  # verbatim, which can fabricate an "EXPECT: satisfied" nobody asked for or flip a good run to
  # INDETERMINATE. `head -1` pins the read to the header only when a header line already exists,
  # which is precisely the case that fails.
  header="$(sed -n '1,/^----- output -----$/p' "$art")"
  recorded="$(sed -n "s/^${EXPECT_PREFIX}//p" <<< "$header" | head -1)"
  [[ -n "$recorded" ]] && printf '%s\n' "$recorded"
  [[ -n "$expect" ]] && printf '%s\n' "$expect"
  return 0
}

# Match ONLY below the output marker. The header echoes the command with %q and records --label
# RAW, so a whole-artifact match is satisfied by the CALLER'S OWN TEXT: measured on both vectors,
# each reporting DONE for a run whose output was empty. This file has already paid for this
# lesson once — the `2>&1` mutation SURVIVED until two rows were narrowed to this section.
#
# The here-string is deliberate and must NOT be "simplified" back into `sed … | grep -q`. This
# script runs under `set -o pipefail`, and `grep -q` exits on its FIRST match, which can SIGPIPE
# sed (141) and make the pipeline non-zero *on a successful match*. That would invert the verdict
# INTERMITTENTLY, depending on artifact size and buffering — the worst possible failure for a
# tool whose whole job is telling you whether a verdict is trustworthy.
#
# A missing marker yields an empty section, so nothing matches: absence of the marker is never a
# clear.
expect_satisfied() { # artifact pattern
  local section
  # Position alone is NOT enough. The scoped range still holds two HARNESS-authored lines — the
  # marker itself and the status trailer — and leaving them in rebuilds the false clear one layer
  # down. Measured on an inert artifact: `--expect 'STATUS=0'` and `--expect 'output'` BOTH
  # cleared a run that produced nothing, and `STATUS=0` is a natural way to try to assert success.
  # `1d` drops the marker; the trailer goes by pattern.
  section="$(sed -n '/^----- output -----$/,$p' "$1" |
    sed -e '1d' -e '/^RUN_LONG_EXIT_STATUS=[0-9][0-9]*$/d')"

  # An EMPTY section must be unmatched BY CONSTRUCTION, never by trusting the matcher. A
  # here-string built from "" feeds it ONE EMPTY LINE, which '^', '$' and '^$' all match — so
  # "no marker means nothing can match" was FALSE until this guard existed.
  [[ -n "$section" ]] || return 1

  grep -qE -e "$2" <<< "$section"
}

classify() { # artifact -> sets CLASS to done | running | died
  local art="$1"
  CLASS=""
  JOB_RC=""
  JOB_PID=""
  EXPECT_ACTIVE=0
  EXPECT_UNMATCHED=""

  if grep -q "^${STATUS_PREFIX}" "$art"; then
    JOB_RC="$(sed -n "s/^${STATUS_PREFIX}\\([0-9][0-9]*\\)\$/\\1/p" "$art" | tail -1)"
    CLASS="done"
    local pat
    # Process substitution, NOT `expect_patterns "$art" | while read`. A pipe runs the loop body
    # in a SUBSHELL, so every assignment below — CLASS included — would be discarded silently and
    # the check would never fire. This repo has already measured that exact bug in shell code.
    while IFS= read -r pat; do
      [[ -n "$pat" ]] || continue
      EXPECT_ACTIVE=1
      if ! expect_satisfied "$art" "$pat"; then
        CLASS="indeterminate"
        EXPECT_UNMATCHED="$pat"
        break
      fi
    done < <(expect_patterns "$art")
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
      if [[ "$EXPECT_ACTIVE" -eq 1 ]]; then
        printf 'EXPECT: satisfied — the output carries the line this run was required to produce\n'
      fi
      subject_report "$art"
      [[ "$JOB_RC" == "0" ]] && return 0
      return 1
      ;;
    indeterminate)
      printf 'RESULT: INDETERMINATE rc=%s artifact=%s\n' "${JOB_RC:-?}" "$art"
      printf '        The job FINISHED and recorded that status, but its output contains no line\n'
      printf '        matching: %s\n' "$EXPECT_UNMATCHED"
      printf '        A finished run that produced no verdict has not been verified. This is the\n'
      printf '        INERT-run case: exiting 0 is not evidence that any work happened.\n'
      printf '        If the run DID reach a verdict, the PATTERN is the defect — it must match\n'
      printf '        the verdict SHAPE (pass and fail alike), never only the passing value.\n'
      subject_report "$art"
      return 5
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

# The pattern rides inside the SAME header block rather than travelling as another child
# argument. Passing it separately would mean the child unconditionally printf'ing a value that is
# empty on almost every launch, and `printf "%s\n" ""` emits a BLANK LINE — silently breaking the
# byte-identity every existing caller relies on, while failing no assertion that exists today.
header_block="$subject_block"
if [[ -n "$expect" ]]; then
  header_block="$(printf '%s\n%s%s' "$header_block" "$EXPECT_PREFIX" "$expect")"
fi

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
  header=$5
  shift 5
  {
    printf "%s pid=%d label=%s\n" "$begin" "$$" "$tag"
    printf "%s\n" "$header"
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
' _ "$out" "$label" "$BEGIN_PREFIX" "$STATUS_PREFIX" "$header_block" "$@" > /dev/null 2>&1 &
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

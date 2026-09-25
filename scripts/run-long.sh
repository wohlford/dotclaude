#!/usr/bin/env bash
set -uo pipefail

# Script: run-long.sh
# Purpose: Launch a long job in the background and record its real exit status inside the artifact
# Usage: run-long.sh --out <path> [--label <text>] [--expect <ere>] [--force] -- <command> [args...]
#        run-long.sh --status <path> [--expect <ere>]
#        run-long.sh --wait <path> [--interval <seconds>] [--expect <ere>]
#        run-long.sh --stamp
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
# Recorded in place of a digest when the launch was inside a repo but the stamp failed.
readonly SUBJECT_UNAVAILABLE='unavailable'
readonly EXPECT_PREFIX='RUN_LONG_EXPECT='
readonly DEFAULT_INTERVAL=15

usage() {
  cat <<'EOF'
Usage: run-long.sh --out <path> [--label <text>] [--expect <ere>] [--force] -- <command> [args...]
       run-long.sh --status <path> [--expect <ere>]
       run-long.sh --wait <path> [--interval <seconds>] [--expect <ere>]
       run-long.sh --stamp
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

Stamp mode:
  --stamp          print the current directory's git working-tree fingerprint (the same one
                   used for SUBJECT drift detection) and exit 0, or print nothing and exit 0
                   ONLY when git affirmatively reports the directory is outside any repo.
                   Every OTHER reason a fingerprint could not be produced -- git itself absent,
                   a GIT_DIR pointing nowhere real, a bare repo with no work tree, no
                   sha1sum/shasum installed, any git command inside the stamp failing --
                   exits NONZERO instead of folding into the same silent branch, so a
                   caller's own bad-stamp-count gate can tell "ran fine, found nothing to
                   stamp" apart from "the stamp cannot be trusted". Exposes subject_stamp()
                   so callers reuse the stamp's git commands instead of hand-copying them.
                   Cannot be combined with --out/--status/--wait or a command: each is its own
                   mode, mixing them is a usage error rather than a silent partial launch.

Both read modes also report SUBJECT: whether the git working tree has MOVED since the run was
launched, i.e. whether the verdict still describes the tree you have now. Best-effort (it says
so outside a git repo, and says UNAVAILABLE/UNREADABLE when the stamp could not be computed at
launch or now) and a WARNING only — it never changes the exit code.
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
stamp_set=0

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
    --stamp)
      mode="stamp"
      stamp_set=1
      shift
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

# --stamp is checked by presence (stamp_set), never by `$mode == stamp`: whichever mode flag is
# parsed LAST wins the $mode variable, so `--status X --stamp` would otherwise read as pure stamp
# mode and never trip this. Without this guard, `--stamp --out X -- cmd` used to print the stamp,
# exit 0, and launch nothing — a caller who typo'd would believe the job had started. Each mode is
# now mutually exclusive with the others, so a mix is a usage error instead of a silent discard.
if [[ "$stamp_set" -eq 1 ]]; then
  [[ -z "$out" ]] || die '--stamp cannot be combined with --out; run one mode at a time'
  [[ -z "$status_path" ]] || die '--stamp cannot be combined with --status/--wait; run one mode at a time'
  [[ $# -eq 0 ]] || die '--stamp cannot be combined with a command; run one mode at a time'
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

# Fingerprint the git working state under $1. Contract: on success, print one digest and return
# 0; on ANY failure, print NOTHING and return 1. There is no third outcome -- every caller must
# treat "no stamp" as "could not be computed", never as "unchanged".
#
# `git rev-parse HEAD` alone would be WORSE than nothing. The tree under a long check is normally
# dirty — uncommitted work is usually the entire reason for running it — so a HEAD-only stamp
# reports "unchanged" across exactly the edits this exists to catch.
#
# Every git command's exit status is checked, each captured into its own variable rather than
# piped straight into the hasher: the old `{ ...; } 2> /dev/null | hasher` discarded every rc, so
# a git command dying mid-stream hashed its TRUNCATED output into a constant stamp (measured: an
# ignore=all submodule whose .git/modules dir was gone made edits to a later file read as
# unchanged). Captures are in memory -- nothing is routed through a file at a path.
subject_stamp() { # repo-root
  local root="$1" hasher="" head="" base="HEAD" diff status subs digest diff_rc status_rc subs_rc
  git -C "$root" rev-parse --git-dir > /dev/null 2>&1 || return 1
  if command -v shasum > /dev/null 2>&1; then
    hasher="shasum"
  elif command -v sha1sum > /dev/null 2>&1; then
    hasher="sha1sum"
  else
    return 1
  fi
  # An UNBORN branch (a fresh `git init`, no commit yet) has no HEAD to diff against, yet its tree
  # is perfectly stampable: diff against the empty tree instead. Only a HEAD that is a symbolic
  # ref to a not-yet-existing branch counts as unborn; any other rev-parse failure is a failure.
  if ! head="$(git -C "$root" rev-parse --verify -q HEAD 2> /dev/null)"; then
    git -C "$root" symbolic-ref -q HEAD > /dev/null 2>&1 || return 1
    head="unborn"
    base="$(git -C "$root" hash-object -t tree /dev/null 2> /dev/null)" || return 1
  fi
  # Explicit flags override SPECIFIC change-hiding config keys, and only those:
  # status.showUntrackedFiles, diff.ignoreSubmodules, submodule.<name>.ignore (at any depth, via
  # the walk below), diff.external, and a submodule's own untracked settings. Other configuration
  # that hides a change is NOT overridden and still decides what "unchanged" means here -- e.g. a
  # lossy or constant `diff.<driver>.textconv` (no `--no-textconv` is passed; measured: a tracked
  # edit to such a file leaves the stamp identical), clean/smudge filters, core.fileMode=false,
  # and assume-unchanged/skip-worktree; see flake-sweep.sh's NOT-covered list. Each flag below is
  # the ONLY thing that catches its case (the named test row fails without it):
  #   - top diff `--ignore-submodules=none`: a commit made INSIDE a submodule marked ignore=all
  #     moves its gitlink, which that config would otherwise hide (rS25);
  #   - top status `--untracked-files=all`: a new untracked file under showUntrackedFiles=no
  #     (rS12), and a new file inside an already-untracked directory, which `normal` collapses to
  #     a constant `?? dir/` line (rS15).
  # Fix wave 2 REMOVED the top-level `--submodule=diff` and the status `--ignore-submodules=none`:
  # the per-submodule walk below stamps every submodule's content directly, and no row failed
  # without them (measured by mutation) -- they read as safety while deciding nothing.
  # Every rc is captured (diff_rc/status_rc/subs_rc) and checked in ONE place below, before any
  # hashing; see the contract comment above this function.
  diff="$(git -C "$root" diff "$base" --no-ext-diff --ignore-submodules=none 2> /dev/null)"
  diff_rc=$?
  status="$(git -C "$root" status --porcelain --untracked-files=all 2> /dev/null)"
  status_rc=$?
  # The top-level flags reach only the top-level commands. Whether a submodule reads as dirty, and
  # what its diff shows, is decided by a child git run INSIDE it, under that submodule's OWN
  # config and its own .gitmodules -- measured to hide: a nested submodule's committed
  # ignore=dirty (depth 2), a further untracked file in a submodule already holding untracked
  # content, a submodule-level showUntrackedFiles=no, and a submodule-level diff.external. So
  # every checked-out submodule, at every depth (`--recursive`; rS16), is stamped directly, with
  # the same flags overriding the same keys (and no others -- a submodule's own textconv or
  # filters still apply inside it): its
  # own diff with `--no-ext-diff` (rS19) and `--ignore-submodules=none` (a commit inside ITS
  # ignore=all child; rS26), and its own status with `--untracked-files=all` (rS18). Each
  # block is headed by its path, so an untracked file moving between two submodules cannot read
  # as unchanged (rS27). `&&` makes any inner failure fail the whole walk. A gitlink with no
  # .gitmodules entry (an embedded repo `git add`ed by accident) makes the walk fail, so such a
  # repo gets NO stamp -- the safe direction (rS28).
  # shellcheck disable=SC2016 # expanded by the per-submodule shell git spawns, not here
  subs="$(git -C "$root" submodule foreach --quiet --recursive \
    'printf "%s\n" "$displaypath" && git diff HEAD --no-ext-diff --ignore-submodules=none && git status --porcelain --untracked-files=all' \
    2> /dev/null)"
  subs_rc=$?
  # The single rc gate: a git command that died mid-stream leaves TRUNCATED output, and hashing
  # that would yield a constant stamp (rS20); any failure means no stamp at all.
  [[ "$diff_rc" -eq 0 && "$status_rc" -eq 0 && "$subs_rc" -eq 0 ]] || return 1
  # The hasher reads its pipe to EOF, so pipefail cannot misfire on an early-exiting reader here.
  digest="$(printf 'head %s\n--diff\n%s\n--status\n%s\n--submodules\n%s\n' \
    "$head" "$diff" "$status" "$subs" | "$hasher" | awk '{print $1}')" \
    || return 1
  [[ -n "$digest" ]] || return 1
  printf '%s\n' "$digest"
}

# Affirmatively determines whether $1 (default: cwd) is OUTSIDE any git repository, as distinct
# from every other reason a git query can fail: git itself absent, a GIT_DIR pointing nowhere
# real, a bare repo with no work tree, a corrupt repo, ... The pre-fix --stamp mode inferred
# "outside a repo" from mere EMPTINESS of `git rev-parse --show-toplevel`, which folded all of
# those into one silent "print nothing, exit 0" branch -- measured false clean: GIT_DIR pointed
# at a nonexistent path inside a real repo whose tracked file the subject edited every run, and
# --stamp reported empty/rc=0 exactly as it does genuinely outside a repo. True only for git's
# own "not a git repository (or any of the parent directories)" message -- measured to differ
# from a bogus GIT_DIR's "fatal: not a git repository: '<path>'" (no parenthetical) and from a
# bare repo's "fatal: this operation must be run in a work tree", so neither is mistaken for
# genuinely being outside one.
outside_git_repo() { # [dir]
  local dir="${1:-.}" err
  command -v -- git > /dev/null 2>&1 || return 1
  # LC_ALL=C: gettext ignores LANGUAGE when the locale is C, so the matched English text is what
  # git emits, regardless of the caller's own locale (e.g. LC_ALL=de_DE.UTF-8).
  if err="$(LC_ALL=C git -C "$dir" rev-parse --show-toplevel 2>&1 1>/dev/null)"; then
    return 1
  fi
  [[ "$err" == *'not a git repository (or any of the parent directories)'* ]]
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
  # The launch was inside a repo, but subject_stamp() failed there: a distinct recorded value, so
  # it is never mistaken for "outside a repo" and never compared as if it were a digest.
  if [[ "$recorded" == "$SUBJECT_UNAVAILABLE" ]]; then
    printf 'SUBJECT: UNAVAILABLE — the stamp could not be computed at launch in %s, so drift cannot be judged\n' \
      "${root:-?}"
    return 0
  fi

  now=""
  if [[ -n "$root" && -d "$root" ]]; then
    now="$(subject_stamp "$root")" || now=""
  fi

  if [[ -z "$now" ]]; then
    printf 'SUBJECT: UNREADABLE — the stamp could not be computed now for %s, so this verdict cannot be tied to a tree\n' \
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

  # ORDER IS LOAD-BEARING: sample liveness BEFORE reading the trailer.
  #
  # The wrapper appends the trailer as its LAST act before exiting (see the launcher block near
  # the end of this file), so for a job that ran TO COMPLETION, "wrapper observably gone" implies
  # "trailer on disk". Reading the trailer FIRST leaves a window: a job finishing between the two
  # observations is seen as no-trailer AND not-alive, and a healthy run is reported DIED — the one
  # verdict a reader must never re-run past. Measured 2/30 under load, 0/80 quiesced.
  #
  # Do NOT restate that as a flat "dead implies trailer on disk". It is FALSE for a killed wrapper
  # (measured 5/5), and the absolute form invites an "improvement" that re-reads until a trailer
  # appears — which would hang forever on every genuine kill. Every other dead-without-trailer
  # state is one where DIED is the correct fail-closed verdict: either the job was killed, or the
  # RECORDING of its status was, and an unrecorded status is untrustworthy either way.
  #
  # Accepted cost: `alive` is sampled earlier and consumed later, so a job killed in THAT gap
  # reads RUNNING once rather than DIED. That is the safe direction — RUNNING is non-terminal, so
  # --wait self-corrects on its next poll and a repeated --status corrects too, where the defect
  # this replaces was terminal. No measurement was taken of THIS window's width, but nothing about
  # it should read as smaller than the 2/30 above: both windows are opened by one external command
  # sitting between a sample and its use (there `sed | head`, here the `grep -q` right below) —
  # same order of magnitude, opposite direction, and — the part that actually matters — this one
  # is non-terminal where that one was.
  #
  # The SELF-CORRECTION is the load-bearing half of that argument, so it is PINNED rather than
  # asserted: row `w12`. The window is deterministically reachable because the `grep -q` below is
  # an EXTERNAL command sitting between the sample and its use, so a PATH shim can open it. An
  # earlier version of this comment called the window untestable and used that to justify leaving
  # it unpinned; both halves were wrong, and the claim is recorded here because "no test can reach
  # this" is exactly the excuse one probe settles.
  local alive=0
  JOB_PID="$(sed -n "s/^${BEGIN_PREFIX} pid=\\([0-9][0-9]*\\).*/\\1/p" "$art" | head -1)"
  # `2>/dev/null` is required, not tidiness: this now runs on EVERY classify, including done-path
  # reads of old artifacts whose pid has been recycled or belongs to another user, and
  # `kill: Operation not permitted` would otherwise land in --status output that callers capture.
  if [[ -n "$JOB_PID" ]] && kill -0 "$JOB_PID" 2>/dev/null; then alive=1; fi

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

  if [[ "$alive" -eq 1 ]]; then
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

# --stamp returns BEFORE every other mode's logic, deliberately: six callers and every mutation
# campaign depend on this file, so a new mode earns its place only if it cannot perturb the three
# that exist. It exposes subject_stamp rather than making callers copy the stamp's git commands,
# which is the measured hand-made-copy drift hazard.
#
# The exit code is derived from outside_git_repo(), the SAME predicate subject_stamp() itself
# does not need to duplicate -- two independently-driftable spellings of "outside a repo" is how
# the pre-fix defect arose (subject_stamp's own emptiness silently absorbed git-absent, a bogus
# GIT_DIR, and no-hasher right alongside the genuine outside-a-repo case). Only the affirmative
# reading exits 0-with-nothing; every other reason a fingerprint could not be produced dies
# nonzero, so a caller's bad-stamp-count gate can tell the two apart instead of classifying both
# as "ran fine, found nothing".
if [[ "$mode" == "stamp" ]]; then
  if outside_git_repo "."; then
    exit 0 # affirmatively outside any repo: silent, by contract
  fi
  root="$(git rev-parse --show-toplevel 2>/dev/null)"
  if [[ -z "$root" ]]; then
    die '--stamp: git could not resolve a repo root, and did not report "not a git repository" either -- treating this as a real failure rather than silently reporting "outside a repo"'
  fi
  if ! hash="$(subject_stamp "$root")" || [[ -z "$hash" ]]; then
    die "--stamp: inside $root but could not compute a fingerprint (a git command inside the stamp failed, or no sha1sum/shasum is installed)"
  fi
  printf '%s\n' "$hash"
  exit 0
fi

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
if [[ -n "$subject_root" ]]; then
  # Inside a repo but the stamp failed: record that fact, never an empty value that --status would
  # read as "outside a repo" (see subject_report).
  subject_hash="$(subject_stamp "$subject_root")" || subject_hash="$SUBJECT_UNAVAILABLE"
  [[ -n "$subject_hash" ]] || subject_hash="$SUBJECT_UNAVAILABLE"
fi
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
# kept because the ordering holds by accident rather than by construction. This is not the "no
# test can reach it" excuse rejected above (:357): a probe WAS run, 40 times, and it DID reach the
# window — it measured that the window is too narrow for that probe's own instrument (forking a
# separate --status process) to resolve, not that no instrument could.
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

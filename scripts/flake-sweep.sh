#!/usr/bin/env bash
set -uo pipefail

# Script: flake-sweep.sh
# Purpose: Run a command N times against a verifiably unchanged tree and report each row's pass/fail/skip rate.
#          Fails when any verdict varies across runs, so a check's own determinism is measured
#          rather than merely asserted from a single run.
# Usage: flake-sweep.sh -n <N> [--artifact-dir <dir>] -- <command> [args...]
#
# Not `set -e`: a subject exiting non-zero is DATA, not an error -- `-e` would abort the sweep on
# the first failing run, which is precisely the measurement being taken.
#
# General over its subject: this tool does not know what a suite is. It runs the command N times
# and extracts per-row PASS/FAIL/SKIP verdicts when the output carries a recognised format
# (`^(PASS|FAIL|SKIP)[[:space:]]+<label>`, with a trailing ` (...)` or ` -- ...` diagnostic
# stripped), tallying each label's (pass,fail,skip) tuple per run. A label is VARYING when that
# tuple differs across runs; absence is (0,0,0), no special case. The per-run EXIT CODE is
# aggregated too, under the reserved synthetic label `<exit status>` -- a rowless subject (every
# mutation campaign, every pytest suite) is otherwise invisible to this tool, and that is half the
# population it exists for.
#
# Tree stability comes from run-long.sh's `--stamp` mode (its subject_stamp(), reused rather than
# copied), taken BEFORE every run so a moving subject names the run that saw it, not just that
# something moved -- plus one closing stamp after the last run, so a mutation caused by that FINAL
# run is bracketed too rather than going unobserved (measured false clean before this closing
# stamp existed: a subject that edits a tracked file only on its last run reported
# `PASS ... subject=stable` with the edit sitting in the tree).
#
# Aggregation happens in awk, never bash associative arrays: a bare `PASS  ` line with no label
# extracted, keyed into a bash associative array, is a *fatal* "bad array subscript" on bash
# 5.3.15 and would kill the sweep mid-run.
#
# Does NOT self-background. run-long.sh is the shipped remedy for outrunning the tool timeout;
# wrap the whole invocation in it (`run-long.sh --out ... -- flake-sweep.sh -n 20 -- ...`) rather
# than reaching for a hand-rolled wrapper -- four were written in one session here, three
# byte-equivalent, and the fourth still let a partial sweep read as clean.
#
# Known capture edge cases (Fable review): a NUL byte in the subject's output is dropped by `$( )`
# silently -- the fd-8 slurp runs inside the capture group whose own `2> /dev/null` swallows bash's
# "ignored null byte" warning before it ever reaches the sweep's stderr, so the only trace is
# `bytes=` undercounting by the dropped byte count; the row itself survives. A subject that reopens
# `/dev/stdout` with `O_TRUNC` (an accidental shape, e.g. one invoked with its own `-o /dev/stdout`
# flag) does not truncate the capture on macOS (reproduced here); a Linux truncation was reported
# during plan review but not reproduced on this host -- platform-dependent either way, and not a
# confirmed bug in this script. A fork failure launching the subject was observed once during plan
# review (bash 3.2 and 5.3) to abort the sweep with no `RESULT:` line; the suite does not reproduce
# it, so treat this as EXPECTED, not verified, behaviour -- a missing verdict means abort, never a
# hung run or a pass.
#
# INVARIANT, stated once here and nowhere else: no verdict input is ever read from a path. Verdict
# inputs are exactly the subject's wait status, its captured output (an unlinked, in-process pair
# of file descriptors -- see the per-run capture below), and the stamp text from run-long.sh
# --stamp via a command substitution. The four artifact files (labels/exits/stamps/rawlabels.tsv)
# and the per-run run-NNN.log are WRITE-ONLY side copies for the operator to read afterward;
# nothing in this script ever reads any of them back, and a failure to write one is reported
# (ARTIFACTS: below) but never changes the verdict -- a directory neither this process nor the
# operator exclusively owns (a subject, a concurrent peer sharing --artifact-dir, or a failing
# disk) can prevent or corrupt any of them without affecting what gets measured. Four prior rounds
# each shipped a false clean by routing a verdict input through such a path; this is the closure.
#
# Threat model. Covered: accidental interference -- a subject or peer that deletes, truncates, or
# cannot write paths in the artifact dir or $TMPDIR; a non-English locale (run-long.sh's
# outside-a-repo check runs under LC_ALL=C for this reason). Not covered, and not detectable by
# this tool:
#   - a subject that deliberately rewrites run-long.sh (the stamp tool) or reopens `/dev/fd/1`
#     with O_TRUNC;
#   - an untracked file's CONTENT changing (inherited from run-long's subject_stamp -- see the
#     `SUBJECT: stable` case near the end of this script);
#   - a full disk truncating the subject's own output identically on every run (partly visible:
#     `bytes=<k>` on the progress line goes to zero for a subject that normally has rows);
#   - identical row text printed twice in one run with the two verdicts trading which occurrence
#     carries which label across runs (`PASS x`/`FAIL x` then `FAIL x`/`PASS x`) -- each run's
#     tallied counts are the same either way, so this is unobservable;
#   - two rows differing only by a TAB vs a space, or by awk's own SUBSEP (`\034`) -- both are
#     flattened to a plain space before any keying (the TSV artifact format needs one separator),
#     so a flip between such rows is hidden;
#   - the per-run capture file's `rm -f` unlink, whose exit status is not checked -- if that unlink
#     ever failed, the capture (a file this process alone created moments earlier via mktemp) would
#     stay reachable by path for the rest of that run;
#   - rows the subject colourises (`\e[32mPASS\e[0m ...`) do not match the row pattern, so they
#     read as `rows=0` -- and a colour setting the subject merely inherits (e.g. `FORCE_COLOR`)
#     produces this silently (measured: `RESULT: PASS ... rows=0`);
#   - BSD `/usr/bin/awk` under a UTF-8 locale fails on an invalid UTF-8 byte in the subject's
#     output, so the sweep reports INDETERMINATE (the safe direction; GNU awk handles the byte;
#     measured);
#   - on NFS an unlinked open file becomes a hidden `.nfsXXXX` file, so the per-run capture stays
#     reachable by path when `$TMPDIR` is on NFS (reasoned, not measured);
#   - git clean/smudge filters (e.g. nbstripout), `core.fileMode=false`, and
#     `update-index --assume-unchanged`/`--skip-worktree` hide changes from the stamp BY DESIGN of
#     the user's own configuration (assume-unchanged measured in review; the others reasoned);
#   - a lossy or constant `diff.<driver>.textconv` hides tracked edits to the files it applies to
#     -- the stamp does not pass `--no-textconv` (measured);
#   - gitignored paths (caches, build output) are invisible to the stamp (measured);
#   - edits inside an UNTRACKED nested git repo are invisible: it shows as one constant
#     `?? nested/` line (measured);
#   - files written into an UNINITIALIZED submodule's directory are invisible (measured);
#   - a gitlink with no `.gitmodules` entry (populated or not) yields no stamp at all, so every
#     sweep in that repo FAILs with subject=UNVERIFIABLE -- and the sweep does not say why, because
#     the stamp's stderr is discarded (measured);
#   - index-only changes that leave the porcelain status letters unchanged (e.g. re-staging over
#     an `MM` entry) are invisible (reasoned, not measured).
#
# Now COVERED by run-long's stamp, and so not in the list above: these SPECIFIC change-hiding
# config keys, which the stamp's explicit flags and its submodule walk override --
# status.showUntrackedFiles, diff.ignoreSubmodules, submodule.<name>.ignore at any depth,
# diff.external, and a submodule's own untracked settings; new untracked files inside
# checked-out submodules; and a git command failing mid-stamp (the stamp is then refused, which
# this sweep reports as subject=UNVERIFIABLE and FAIL -- never as a constant, "stable" digest).
# Any OTHER configuration that hides a change is NOT covered (see the list above).
#
# Collision rule (labels, see the aggregation section below): a stripped label that saw more than
# one distinct raw text in a single run is compared by those raw texts across runs before being
# called stable, so an opposite flip hidden behind a constant merged tally still counts as varying.
# When the raw texts themselves change across runs under a constant merged tally, the sweep cannot
# tell a diagnostic change from traded verdicts and reports that label AMBIGUOUS (INDETERMINATE
# rc=4) instead of guessing.

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
run_long="$here/run-long.sh"

usage() {
  cat <<'EOF'
Usage: flake-sweep.sh -n <N> [--artifact-dir <dir>] [--force] -- <command> [args...]

Runs <command> N times, parses PASS/FAIL/SKIP rows from its output when present, aggregates the
exit code too, and reports each label's rate plus whether the tree stayed stable.

-n <N>                how many runs; must be >= 2 -- a single run cannot vary, so it cannot
                      measure a flake (-n 1 is a usage error, not a by-construction PASS).
--artifact-dir <dir>  where the four artifacts (labels/exits/stamps.tsv, run-NNN.log) land.
                      Refused if the directory already has content, unless --force is given --
                      a shared, un-locked directory is how a concurrent sweep corrupts this
                      one's counts. Also refused if the directory resolves INSIDE the git repo
                      this sweep stamps and is not gitignored: its own run logs would appear in
                      `git status` between stamps and read as a moved subject.
--force               allow reusing a non-empty --artifact-dir anyway.

These artifacts are a write-only debugging record for the operator: nothing in this script ever
reads them back to compute a verdict, so a run that cannot write them still reports one. A write
failure among them is reported as an `ARTIFACTS: <k> write(s) failed` line, never as a verdict
change.
EOF
}

# ERROR is a USAGE fault: the sweep never began. Print a best-effort RESULT line so a caller
# grepping for `^RESULT:` never sees silence on a usage mistake, then exit 2.
#
# BLOCKER: `-n` is raw, attacker-controlled text at the point die_usage can be called for it (every
# call site above the `^[0-9]+$` check runs before that check, and even the check's own failure
# calls die_usage with the very value that failed it). Interpolating it into the RESULT line
# unvalidated lets `-n $'1\nRESULT: PASS ...'` forge a second, fake `RESULT: PASS` line onto
# stdout -- exactly the stream a caller greps `^RESULT:` from -- immediately after the real `ERROR`
# line. Only ever echo `$n` back here if it independently satisfies the same digit-only shape the
# real usage check enforces; otherwise fall back to the fixed literal `0`, which cannot carry a
# newline, a stray `RESULT:` token, or anything else.
die_usage() {
  # $1 itself can carry attacker-controlled text (several call sites build it as "... : $n"), and
  # a raw embedded newline in that text would split it into two literal stderr lines -- the second
  # of which can start with `RESULT:` and read as a forged verdict to any caller merging streams
  # (`2>&1`), even though the sanitized RESULT line on stdout below is now safe on its own.
  # Collapse any embedded newline to a visible `\n` escape so the whole diagnostic always stays
  # ONE line, no matter what a future call site interpolates into it.
  printf 'flake-sweep.sh: %s\n' "${1//$'\n'/\\n}" >&2
  usage >&2
  local n_display=0
  [[ "${n:-}" =~ ^[0-9]+$ ]] && n_display="$n"
  printf 'RESULT: ERROR rc=2 runs=0/%s rows=0 varying=0 subject=unknown\n' "$n_display"
  exit 2
}

n=""
artifact_dir=""
force=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h | --help)
      usage
      exit 0
      ;;
    -n)
      [[ $# -ge 2 ]] || die_usage '-n needs a value'
      n="$2"
      shift 2
      ;;
    --artifact-dir)
      [[ $# -ge 2 ]] || die_usage '--artifact-dir needs a path'
      artifact_dir="$2"
      shift 2
      ;;
    --force)
      force=1
      shift
      ;;
    --)
      shift
      break
      ;;
    *)
      die_usage "unrecognized argument (missing '--' before the command?): $1"
      ;;
  esac
done

[[ -n "$n" ]] || die_usage 'no -n given'
[[ "$n" =~ ^[0-9]+$ ]] || die_usage "-n must be a non-negative integer: $n"
# A leading zero on more than one digit (e.g. "010") is accepted by the shape check above but is
# then read in bash ARITHMETIC context by both the `for` loop bound and every `-ge`/`-eq` test
# below -- where bash parses a leading-0 integer literal as OCTAL, not decimal. "-n 010" would
# silently run 8 times, not 10, with every progress and RESULT line consistently reporting the
# octal value (so nothing downstream looks wrong) while the caller's intended count was ignored.
# Rejected rather than reinterpreted: a caller who typed a leading zero almost certainly meant
# decimal, and guessing which base they meant is worse than asking them to drop the zero.
if [[ "$n" =~ ^0[0-9]+$ ]]; then
  # Report the DECIMAL the caller almost certainly meant, never a computed octal value: the octal
  # reading of "09" does not exist (`$((09))` dies "value too great for base"), so any message that
  # printed one would itself fail on half the inputs it exists to explain.
  n_decimal="$((10#$n))"
  die_usage "-n must not have a leading zero: bash reads a leading-zero value in octal, so $n is not the decimal $n_decimal you almost certainly meant (and 08/09 are not valid octal at all) -- pass $n_decimal instead"
fi
# Floor of 2, not 1: nothing can vary within a single run, so `-n 1` would PASS by construction.
[[ "$n" -ge 2 ]] \
  || die_usage "-n must be >= 2 (a single run cannot vary, so it cannot measure a flake): $n"
[[ $# -ge 1 ]] || die_usage 'no command given (put it after --)'

if [[ -z "$artifact_dir" ]]; then
  artifact_dir="$(mktemp -d)" || die_usage 'cannot create a default artifact dir'
else
  mkdir -p -- "$artifact_dir" 2> /dev/null || die_usage "cannot create artifact dir: $artifact_dir"

  # A directory shared with a concurrent sweep, or left over from a prior one, can corrupt this
  # sweep's counts once artifact writes are unowned (see the INVARIANT in the header) -- refuse to
  # reuse one that already has content unless the caller opts in with --force. Mirrors
  # run-long.sh's `-e "$out" && force -ne 1` treatment of --out.
  if [[ "$force" -ne 1 ]] && [[ -n "$(ls -A -- "$artifact_dir" 2> /dev/null)" ]]; then
    die_usage "--artifact-dir is not empty: $artifact_dir (pass --force to reuse it anyway)"
  fi
fi

# MAJOR: the tool's own artifacts (run-NNN.log, the three tsv files) must not move the tree they
# stamp. `--artifact-dir .` inside the stamped repo makes each new log appear in `git status`
# porcelain between stamps -- a perfectly deterministic subject then reads `subject=MOVED`. A
# directory verified GITIGNORED is provably invisible to `git status` regardless, so only that
# case is exempted; every other in-repo location is refused rather than relying on git's
# untracked-directory porcelain COLLAPSING (`?? dir/` staying one line) to happen to save it,
# which does not hold for the repo root itself or an already-tracked directory. Checked
# unconditionally (not only for an explicit --artifact-dir): the default `mktemp -d` is normally
# outside any repo, but nothing guarantees that of every TMPDIR.
art_repo_root="$(git rev-parse --show-toplevel 2> /dev/null)"
if [[ -n "$art_repo_root" ]]; then
  art_abs="$(cd "$artifact_dir" && pwd -P)"
  art_repo_abs="$(cd "$art_repo_root" && pwd -P)"
  case "$art_abs" in
    "$art_repo_abs" | "$art_repo_abs"/*)
      if ! git -C "$art_repo_root" check-ignore -q -- "$art_abs" 2> /dev/null; then
        die_usage "--artifact-dir is inside the repo this sweep stamps and is not gitignored: $artifact_dir (its own run logs would appear in \`git status\` between stamps and read as a moved subject; point it outside the repo, or add it to .gitignore)"
      fi
      ;;
  esac
fi

labels_tsv="$artifact_dir/labels.tsv"
exits_tsv="$artifact_dir/exits.tsv"
stamps_tsv="$artifact_dir/stamps.tsv"
rawlabels_tsv="$artifact_dir/rawlabels.tsv"

# artifact_failures counts every WRITE-ONLY side-copy write that fails, from these up-front
# truncations through every per-run append below. The verdict never reads these files (see the
# INVARIANT above), so a failure here is reported (ARTIFACTS: near the end) but must never abort
# or skip a single measurement -- an unwritable or vanished --artifact-dir is a fact about the
# operator's debugging record, not about whether the subject was actually run and observed.
artifact_failures=0
: > "$labels_tsv" 2> /dev/null || artifact_failures=$((artifact_failures + 1))
: > "$exits_tsv" 2> /dev/null || artifact_failures=$((artifact_failures + 1))
: > "$stamps_tsv" 2> /dev/null || artifact_failures=$((artifact_failures + 1))
: > "$rawlabels_tsv" 2> /dev/null || artifact_failures=$((artifact_failures + 1))

printf 'planned=%s\n' "$n"
printf 'artifact-dir=%s\n' "$artifact_dir"

# Detection floor, computed rather than asserted: with n runs, a defect at rate p is missed with
# probability (1-p)^n. Reported at p=0.10 so a low-N run is honest about what it cannot see rather
# than silently implying it swept the whole space.
floor_pct="$(awk -v n="$n" 'BEGIN { printf "%.0f", (1 - 0.10) ^ n * 100 }')"
printf 'detection floor: at n=%s, a defect at a 10%% rate is missed by about %s%% of sweeps\n' \
  "$n" "$floor_pct"

# BLOCKER: `command -v -- "$1"` below (inside the run loop) proves the name RESOLVES, not that
# execve succeeds. A file that exists and is executable but whose interpreter is missing (a bad
# shebang, or `env` unable to find the interpreter it names) passes that guard, then the SHELL
# returns 126 or 127 -- its own reserved exec-failure codes -- and that rc gets recorded as ordinary
# DATA. If it is constant across every run (the usual case: the same bad shebang fails the same way
# every time), the aggregation below sees no variance and reports a clean PASS despite the subject
# never having actually run once. `execfail_count` tracks how many COMPLETED runs (i.e. runs that
# got past the command -v guard and were actually exec'd) exited 126/127; the verdict chain below
# refuses PASS only when EVERY completed run did -- a single legitimate 126/127 among
# otherwise-ordinary exits is real data, not evidence of a launch failure, and must not be
# over-blocked.
execfail_count=0

# measure_failures counts a FAILED EXTRACTION -- an awk pass (or the fd-8 slurp of the subject's
# own captured output) that itself exited nonzero -- distinct from a clean extraction that simply
# found zero PASS/FAIL/SKIP rows. The verdict chain's FIRST arm below refuses to report a verdict
# at all when this is nonzero: pre-fix, a failed extraction's empty output was indistinguishable
# from "no rows this run", which reads as an ordinary rowless PASS (measured false clean: an awk
# resolving to a broken interpreter under PATH poisoning made every extraction fail identically
# and silently, and a stable subject reported PASS having never actually been measured).
measure_failures=0

# BLOCKER: `completed` and the executed run-id SET must be facts about THIS process, never
# re-derived by reading exits.tsv back -- --artifact-dir takes any path and a second sweep can
# share it (no lock beyond the non-empty-dir refusal above, which two sweeps racing to create the
# SAME still-empty directory can both pass), so exits.tsv is a shared, mutable, UNOWNED artifact,
# not a source of truth about what this sweep itself executed (measured false clean: sweep A,
# skipped mid-run at n=6, sharing a dir with concurrent sweep B at n=4, reported `PASS ...
# runs=6/6` -- B's own run-id rows backfilled the runs A never executed). `completed` is a plain
# in-loop counter, incremented once per run that actually got past the `command -v` guard and was
# exec'd -- never `completed=$i`, which would reintroduce the original bug (a run the guard
# skipped must leave it untouched). `executed_run_ids` mirrors it as the ordered set of this
# process's own run indices, handed to the aggregation awk below instead of letting it re-derive
# that set from exits.tsv itself.
completed=0
executed_run_ids=()

# BLOCKER: every verdict input must be a fact this process holds in memory -- never re-read from
# the four artifact files, which are shared, mutable and UNOWNED the moment --artifact-dir is
# reused (see the header's INVARIANT; measured witness: a rowless subject that `rm -f`s
# exits.tsv/labels.tsv/rawlabels.tsv on every invocation reported `RESULT: PASS ... varying=0`
# over a run that actually alternated rc=1/rc=0). The files below are still written in full, on
# every run, as the operator's debugging record -- but nothing that feeds `rows`, `row_varying`,
# `row_ambiguous`, `varying_names`, `ambiguous_names`, `merged_lines`, `exit_varying` or `subject`
# may read them back. `exit_codes` mirrors `exits.tsv` (one rc per completed run);
# `rawlabels_mem` mirrors `rawlabels.tsv` (the per-run label-extraction awk's `R` stream, captured
# once and both written to disk AND appended here, rather than run twice or read back); `agg_mem`
# is the aggregation's own in-memory input, never written to disk as such: every `L` row (the same
# untagged row labels.tsv receives, tagged here) plus a per-run `X` row for every distinct
# (label, raw) pair, so the aggregation phase below can tell a collided key's raw texts apart
# without re-deriving them from `rawlabels_mem`, whose rows carry no per-raw verdict counts;
# `stamp_vals`/`stamp_rcs` mirror `stamps.tsv` (one stamp value and one stamp rc per stamp taken,
# including the closing stamp after the loop).
exit_codes=()
stamp_vals=()
stamp_rcs=()
rawlabels_mem=""
agg_mem=""

for ((i = 1; i <= n; i++)); do
  # Stamp BEFORE every run, not once: a single before/after pair only proves the tree ended where
  # it started, not which run saw it move. Never `cd` here -- items relying on drift detection
  # `cd` into their own fixture repo first, and stamping the launch cwd is what makes that work.
  stamp_out="$("$run_long" --stamp 2> /dev/null)"
  stamp_rc=$?
  printf '%d\t%s\t%d\n' "$i" "$stamp_out" "$stamp_rc" >> "$stamps_tsv" 2> /dev/null \
    || artifact_failures=$((artifact_failures + 1))
  stamp_vals+=("$stamp_out")
  stamp_rcs+=("$stamp_rc")

  log="$artifact_dir/$(printf 'run-%03d.log' "$i")"

  if ! command -v -- "$1" > /dev/null 2>&1; then
    # The subject could not even be launched (ENOENT-shaped): this run did not COMPLETE, so it
    # must not count toward the denominator or enter aggregation -- a launch failure is a
    # different fact than "the subject ran and returned a bad exit code".
    printf 'run %d/%d rc=127 (could not execute)\n' "$i" "$n"
    continue
  fi

  # Owned capture: from here on, no PATH reaches the subject's output (see the INVARIANT above).
  # A write fd (7) and an INDEPENDENT read fd (8) are opened on a freshly created private file,
  # which is unlinked immediately -- so the subject inherits neither fd (closed for it in the same
  # redirection that launches it: it cannot leak into its own capture even by guessing the fd
  # number) and, once unlinked, no path -- not the subject's own, not a concurrent peer's, not
  # this process's own --artifact-dir -- can delete, truncate, or replace what was written. Fixed
  # fd numbers (not `{fd}`) keep the bash-3.2 compatibility this tool already maintains. A regular
  # file, not a pipe, keeps the current semantics for a subject that backgrounds a child holding
  # stdout open: the sweep does not wait for it.
  cap_dir="${TMPDIR:-/tmp}"
  cap_dir="${cap_dir%/}"
  if ! cap="$(mktemp "$cap_dir/flake-sweep-cap.XXXXXX" 2> /dev/null)"; then
    # A missing sample, exactly like the command -v skip above: not counted, completed/
    # executed_run_ids left untouched.
    printf 'run %d/%d not executed (could not create a private capture file under %s)\n' "$i" "$n" "$cap_dir"
    continue
  fi
  ran=0
  # shellcheck disable=SC2094 # one fd writes, an INDEPENDENT fd reads from offset 0 after the subject exits
  #
  # `2> /dev/null` is FIRST in the redirection list, ahead of `7>`/`8<`: bash applies a compound
  # command's own redirections in the order written, and stops at the first one that fails without
  # applying the rest -- so placed AFTER `7>`/`8<` it would never take effect in time to silence
  # the raw `bash: ...: No such file or directory` this group prints to the script's own stderr
  # when `$cap`'s directory has vanished (measured: reordered as `7> 8< 2>/dev/null`, the message
  # still prints). Placed FIRST, fd2 is already /dev/null by the time `7>` is attempted, so the
  # message is suppressed (measured). This does NOT hide the SUBJECT's own stderr on a healthy
  # run: the subject's own invocation line below sets its OWN fd1/fd2 via `>&7 2>&1`, which
  # overrides whatever this group-level default is for the duration of that one command
  # (measured: a subject writing to its real stderr is still captured into `out` with this
  # `2> /dev/null` present).
  #
  # The subject ALWAYS runs in a subshell, `( "$@" )`: a bare `"$@"` would run a shell builtin
  # given as the command (`source`, `.`, `eval`, `exec`, `exit`, `cd`, `set`) inside THIS shell,
  # where it can rewrite the engine itself. Measured before the subshell: a sourced file assigning
  # `n=2` cut a planned `-n 6` sweep to `runs=2/2` and PASSed, and `exec true` / `exit 0` ended the
  # engine with no RESULT line at rc 0.
  {
    ran=1
    rm -f -- "$cap" # unlinked before the subject starts: no path reaches the capture from here on
    ( "$@" ) >&7 2>&1 7>&- 8<&-
    rc=$?
    out="$(cat <&8)"
    out_rc=$?
  } 2> /dev/null 7> "$cap" 8< "$cap"
  if [[ "$ran" -eq 0 ]]; then
    # The brace group's OWN redirection failed to open (e.g. the private file vanished between
    # the mktemp above and this open) -- this does NOT exit either bash (spike-verified), so it
    # must be detected explicitly rather than assumed. Also a missing sample, uncounted.
    rm -f -- "$cap"
    printf 'run %d/%d not executed (could not open the private capture file)\n' "$i" "$n"
    continue
  fi
  if [[ "$out_rc" -ne 0 ]]; then
    measure_failures=$((measure_failures + 1))
  fi

  printf '%d\t%d\n' "$i" "$rc" >> "$exits_tsv" 2> /dev/null \
    || artifact_failures=$((artifact_failures + 1))
  exit_codes+=("$rc")

  # The side copy of this run's captured output -- write-only, exactly like the four TSV files.
  # Its own failure to land on disk is reported (ARTIFACTS: below) and never fed back into the
  # verdict, which already has `out` in memory regardless of whether this write succeeds.
  printf '%s\n' "$out" > "$log" 2> /dev/null || artifact_failures=$((artifact_failures + 1))

  # This run COMPLETED (got past the command -v guard and was exec'd): count it and record its id
  # in-process, regardless of whether the exits.tsv append above landed -- a true increment, never
  # `completed=$i`, so a run the guard above skipped leaves this untouched (see skA-skF).
  completed=$((completed + 1))
  executed_run_ids+=("$i")

  if [[ "$rc" -eq 126 || "$rc" -eq 127 ]]; then
    printf 'WARNING: run %d/%d exited %d -- the shell'"'"'s reserved exec-failure code; the subject may not have actually run\n' \
      "$i" "$n" "$rc"
    execfail_count=$((execfail_count + 1))
  fi

  # This awk pass extracts labels from `out` -- the subject's own captured output, held in memory
  # since the brace group above, never read back from a path. Fed by a PIPE, not a herestring: a
  # herestring is backed by a real temp file (always on bash 3.2, and on 5.x above the pipe
  # buffer -- measured: a 70 KB herestring reaches awk as a regular file on both), which is
  # exactly the path-routing this task exists to close. A pipe has no such file, and awk reads to
  # EOF, so `set -o pipefail` cannot invert the verdict the way it can for an early-exiting reader
  # (`grep -q`, `head`) -- the `$( )` below carries the pipeline's own exit status straight through
  # to `extract_rc`. Emits both streams (label counts and raw-label tracking) to its own stdout,
  # tagged `L`/`R`, captured ONCE into `awk_out`; the loop below then writes each tagged line to
  # its usual artifact file (identical format, identical content) AND appends it to `agg_mem`
  # (L) or `rawlabels_mem` (R), so the aggregation phase has this run's contribution without ever
  # reading either artifact file back.
  awk_out="$(printf '%s\n' "$out" | awk -v run="$i" -F'\t' '
    match($0, /^(PASS|FAIL|SKIP)[[:space:]]+/) {
      verdict = substr($0, RSTART, RLENGTH)
      sub(/[[:space:]]+$/, "", verdict)
      raw = substr($0, RSTART + RLENGTH)
      # A tab OR a SUBSEP (awk'\''s own multi-dimensional-array key separator, \034) is a legal
      # character in a subject'\''s own PASS/FAIL/SKIP line, and the extraction regex above only
      # strips the verdict word and the whitespace after it -- so either reaches here unmolested.
      # A tab would write a 6+ FIELD row into labels.tsv, a fixed 5-field format the aggregator
      # relies on; a SUBSEP would corrupt the `rawkey`/`counts` hash keys built below, since it IS
      # that separator. Both flattened to a space here, before ANYTHING else derives from the raw
      # text (the diagnostic strip below, the raw-label tracking, and the eventual hash key all
      # read the SAME flattened text), so `seen[]`/`counts[]` and the eventual TSV row stay
      # consistent with each other.
      gsub(/\t/, " ", raw)
      gsub(SUBSEP, " ", raw)
      label = raw
      sub(/ \(.*\)$/, "", label)     # the suites'\'' trailing diagnostic
      sub(/ — .*$/, "", label)       # the audit.sh trailing diagnostic
      counts[label, verdict]++
      seen[label] = 1
      # MAJOR (closed): two DISTINCT raw labels stripping to the SAME key MERGE their counts, and a
      # merged tuple is NOT guaranteed to move whenever a constituent does -- two constituents
      # flipping in OPPOSITE directions across runs can sum to a CONSTANT tuple (measured: a suite
      # printing "db connect (primary)"/"db connect (replica)", each flipping oppositely every run,
      # merged to a constant one-pass-one-fail and read varying=0). Tracked as the SET of distinct
      # raw texts seen per stripped key, emitted here (tagged R, for the MERGED: visibility line)
      # AND per-run below (tagged X, carrying each raw text'\''s own verdict counts), so the
      # aggregation phase can tell a real opposite flip from a merely-changing diagnostic instead
      # of silently absorbing either into the merged tuple.
      rawkey = label SUBSEP raw
      rc_[rawkey, verdict]++
      if (!(rawkey in rawseen)) {
        rawseen[rawkey] = 1; rlab[rawkey] = label; rtxt[rawkey] = raw
        print "R\t" run "\t" label "\t" raw
      }
    }
    END {
      for (l in seen) {
        p = counts[l, "PASS"] + 0
        f = counts[l, "FAIL"] + 0
        s = counts[l, "SKIP"] + 0
        printf "L\t%d\t%s\t%d\t%d\t%d\n", run, l, p, f, s
      }
      # One X row per distinct (label, raw) pair this run saw, carrying THIS run'\''s own per-raw
      # verdict tuple -- via `rlab`/`rtxt` rather than splitting `rk` back apart on SUBSEP, since a
      # raw label may itself contain SUBSEP-flattened text (see the gsub(SUBSEP, ...) above) and
      # that split would not be invertible. Feeds the aggregation phase'\''s collision detection:
      # a stripped key with more than one distinct raw text in a single run is a "collided" key,
      # and only a collided key needs its raw texts told apart at all.
      for (rk in rawseen)
        printf "X\t%d\t%s\t%s\t%d\t%d\t%d\n", run, rlab[rk], rtxt[rk], rc_[rk, "PASS"] + 0, rc_[rk, "FAIL"] + 0, rc_[rk, "SKIP"] + 0
    }
  ')"
  extract_rc=$?
  if [[ "$extract_rc" -ne 0 ]]; then
    measure_failures=$((measure_failures + 1))
  fi

  if [[ -n "$awk_out" ]]; then
    while IFS=$'\t' read -r tag rest; do
      case "$tag" in
        L)
          printf '%s\n' "$rest" >> "$labels_tsv" 2> /dev/null \
            || artifact_failures=$((artifact_failures + 1))
          # Fed, tagged, into the aggregation stream below; labels.tsv above is the write-only
          # side copy of the same untagged row.
          agg_mem+="L"$'\t'"$rest"$'\n'
          ;;
        R)
          printf '%s\n' "$rest" >> "$rawlabels_tsv" 2> /dev/null \
            || artifact_failures=$((artifact_failures + 1))
          rawlabels_mem+="$rest"$'\n'
          ;;
        X)
          # Memory-only: no artifact file backs this tag, per the INVARIANT above -- it exists
          # solely to feed the aggregation awk's collision detection.
          agg_mem+="X"$'\t'"$rest"$'\n'
          ;;
      esac
    done < <(printf '%s\n' "$awk_out")
  fi

  # bytes=<k> -- the byte count of the subject's own captured output, on every progress line. A
  # full disk truncating the subject's output IDENTICALLY on every run is otherwise invisible (no
  # variance, no extraction failure): this makes an all-zero sweep of a row-bearing subject
  # visible. Computed via a pipe into `wc -c`, which consumes its input to EOF -- pipefail cannot
  # misfire against a reader that never exits early. `tr -d ' '` strips the leading padding both
  # BSD and GNU `wc -c` emit for a single-file count.
  bytes="$(printf '%s' "$out" | wc -c | tr -d ' ')"
  printf 'run %d/%d rc=%d bytes=%d\n' "$i" "$n" "$rc" "$bytes"
done

# Closing stamp, taken once after the loop rather than before-and-after each run: the before-each
# chain plus this single stamp already brackets every run (stamp i .. run i .. stamp i+1, for the
# last run: stamp n .. run n .. this stamp), so a second per-run stamp would be informationally
# equal at twice the cost. Indexed n+1, distinguishable from the n per-run rows; the drift logic
# below compares every stamp's $2 against the first and absorbs this extra row unchanged.
stamp_out="$("$run_long" --stamp 2> /dev/null)"
stamp_rc=$?
printf '%d\t%s\t%d\n' "$((n + 1))" "$stamp_out" "$stamp_rc" >> "$stamps_tsv" 2> /dev/null \
  || artifact_failures=$((artifact_failures + 1))
stamp_vals+=("$stamp_out")
stamp_rcs+=("$stamp_rc")

# `completed` and `executed_run_ids` were already finished, in-process, by the end of the loop
# above -- nothing to re-derive here (they used to be rebuilt from exits.tsv, which is an OUTPUT of
# this sweep, not a source of truth about it; see the BLOCKER above the loop for the measured false
# clean that caused).

# `execfail_all`: every COMPLETED run (one that got past the command -v guard and was actually
# exec'd) exited 126 or 127 -- the subject never once ran successfully enough to produce real
# data. A single legitimate 126/127 among otherwise-ordinary exits is NOT this condition, by
# design (see `execfail_count`'s definition above).
execfail_all=0
if [[ "$completed" -gt 0 && "$execfail_count" -eq "$completed" ]]; then
  execfail_all=1
fi

# ---------- aggregation: awk owns the counting, bash only holds the resulting names ----------
#
# Collision-aware: a stripped key that receives two or more DISTINCT raw texts within a single run
# ("collided") can hide a real opposite flip behind a constant merged tuple (see the per-run awk's
# comment above `rawkey`, and item M1 in the test suite: "db connect (primary)"/"db connect
# (replica)" trading PASS/FAIL every run merge to a constant one-pass-one-fail and used to read
# `varying=0`). For a collided key: if the STRIPPED tuple itself varies, that is unchanged from
# before (a changing diagnostic under a constant verdict must not read as variance, and this still
# holds for a non-collided key with at most one raw text per run). Otherwise, if the collided key's
# SET of raw texts is identical in every executed run, the per-raw tuples are compared directly --
# any difference is a real flip the merge was hiding, and counts as varying. If instead the raw set
# itself changes across runs (a diagnostic legitimately varies) while the merged tuple holds
# constant, the sweep cannot tell a diagnostic change from traded verdicts -- that key is
# AMBIGUOUS, reported separately, and the verdict is INDETERMINATE rc=4 unless FAIL/INCOMPLETE
# already applies (see the verdict chain below, where the ambiguous arm sits after FAIL and before
# PASS).

rows=0
row_varying=0
row_ambiguous=0
varying_names=()
ambiguous_names=()

if [[ -n "$agg_mem" ]]; then
  # The executed run set (`runs[]`, in order) comes from `executed_run_ids` -- this process's OWN
  # in-loop record of what it ran -- never from re-reading exits.tsv, which a concurrent sweep
  # sharing --artifact-dir can also be writing into (see the BLOCKER comment above the loop).
  # Passed in as a single space-joined string; awk splits it in BEGIN, before any row is processed,
  # so the baseline below is always a run this process actually executed. The rows themselves come
  # from `agg_mem` -- this process's own in-memory, tagged copy of every `L` row it wrote to
  # labels.tsv plus every `X` row (memory-only, see above) -- fed via a PIPE (never re-opening any
  # artifact file, and never a herestring, which bash backs with a real temp file -- exactly the
  # path-routing this task closes). awk reads its pipe to EOF, so `set -o pipefail` cannot invert
  # this the way it can an early-exiting reader (`grep -q`, `head`). `agg_mem` already ends in a
  # trailing `\n` (every row appended is `... $'\''\n'\''`), and `printf '%s\n'` would add ANOTHER
  # -- feeding it unstripped would count a phantom empty-string row (measured on the untagged
  # label stream this replaced: a 3-label subject reported `rows=4`). Strip the one trailing
  # newline `agg_mem` already carries so `printf '%s\n'`'\''s own newline is the only one the awk
  # program ever sees.
  agg_out="$(printf '%s\n' "${agg_mem%$'\n'}" | awk -F'\t' -v runs_str="${executed_run_ids[*]}" '
    BEGIN { nruns = split(runs_str, runs, " ") }
    $1 == "L" {
      run = $2 + 0; l = $3
      pk[l SUBSEP run] = $4 + 0; fk[l SUBSEP run] = $5 + 0; sk[l SUBSEP run] = $6 + 0
      if (!(l in seen)) { seen[l] = 1; order[++nlabels] = l }
      next
    }
    $1 == "X" {
      run = $2 + 0; l = $3; key = l SUBSEP $4
      xp[key SUBSEP run] = $5 + 0; xf[key SUBSEP run] = $6 + 0; xs[key SUBSEP run] = $7 + 0
      present[key SUBSEP run] = 1
      nraw[l SUBSEP run]++
      if (!(key in kseen)) { kseen[key] = 1; nk[l]++; keys[l SUBSEP nk[l]] = key }
    }
    END {
      varying = 0; ambiguous = 0
      if (nruns == 0) {
        # Reachable when agg_mem is non-empty but this process executed nothing itself -- kept
        # explicit for the same structural reason the original file-reading form needed it: nruns
        # == 0 must not let `runs[1]` below read as an empty string.
        print "N\t" nlabels "\t0\t0"
        exit
      }
      base = runs[1]
      for (i = 1; i <= nlabels; i++) {
        l = order[i]; b = l SUBSEP base; diff = 0
        for (j = 1; j <= nruns; j++) {
          c = l SUBSEP runs[j]
          if (pk[c] + 0 != pk[b] + 0 || fk[c] + 0 != fk[b] + 0 || sk[c] + 0 != sk[b] + 0) diff = 1
        }
        if (diff) { varying++; print "V\t" l; continue }
        collided = 0
        for (j = 1; j <= nruns; j++) if (nraw[l SUBSEP runs[j]] + 0 > 1) collided = 1
        if (!collided) continue
        sameset = 1
        for (k = 1; k <= nk[l]; k++)
          for (j = 1; j <= nruns; j++) { pkey = keys[l SUBSEP k] SUBSEP runs[j]; if (!(pkey in present)) sameset = 0 }
        if (!sameset) { ambiguous++; print "A\t" l; continue }
        rdiff = 0
        for (k = 1; k <= nk[l]; k++) {
          kb = keys[l SUBSEP k] SUBSEP base
          for (j = 1; j <= nruns; j++) {
            kc = keys[l SUBSEP k] SUBSEP runs[j]
            if (xp[kc] != xp[kb] || xf[kc] != xf[kb] || xs[kc] != xs[kb]) rdiff = 1
          }
        }
        if (rdiff) { varying++; print "V\t" l }
      }
      print "N\t" nlabels "\t" varying "\t" ambiguous
    }
  ')"
  agg_rc=$?
  if [[ "$agg_rc" -ne 0 ]]; then
    measure_failures=$((measure_failures + 1))
  fi

  while IFS=$'\t' read -r tag a b c; do
    case "$tag" in
      V) varying_names+=("$a") ;;
      A) ambiguous_names+=("$a") ;;
      N) rows="$a"; row_varying="$b"; row_ambiguous="$c" ;;
    esac
  done < <(printf '%s\n' "$agg_out")
fi

# BLOCKER (continued): `exit_varying` used to re-read exits.tsv, a shared/unowned file the subject
# could rewrite mid-sweep (measured false clean: a subject that `rm -f`'d exits.tsv left only the
# last run's row, reporting `varying=0` over real variance). It is now computed from `exit_codes`,
# this process's own in-loop record, fed to the same counting-awk shape via a PIPE, not a
# herestring, for the same reason as the label aggregation above -- `$( )` from a command
# substitution has already stripped `exit_codes_str` of trailing newlines, so `printf '%s\n'` below
# reproduces exactly one, the same shape a herestring would have added.
exit_varying=0
if [[ "${#exit_codes[@]}" -gt 0 ]]; then
  exit_codes_str="$(printf '%s\n' "${exit_codes[@]}")"
  distinct="$(printf '%s\n' "$exit_codes_str" | awk '!seen[$0]++ { c++ } END { print c + 0 }')"
  distinct_rc=$?
  if [[ "$distinct_rc" -ne 0 ]]; then
    measure_failures=$((measure_failures + 1))
  fi
  [[ "$distinct" -gt 1 ]] && exit_varying=1
fi
[[ "$exit_varying" -eq 1 ]] && varying_names+=('<exit status>')

total_varying=$((row_varying + exit_varying))

# ---------- merged labels: distinct raw texts that stripped onto the same key ----------
#
# Purely a visibility qualifier, not a verdict input: a merged tuple is not necessarily wrong (it
# may genuinely hold constant), so this does not force FAIL on its own -- it makes a clean verdict
# over a merged key visibly qualified instead of silently absorbing the possibility.
# Fed from `rawlabels_mem` -- this process'\''s own in-memory copy of every row it wrote to
# rawlabels.tsv -- never by re-opening that file, for the same reason as the two aggregations
# above. Piped into awk (never a herestring), with the same trailing-newline strip as
# `agg_mem` above. Captured into `merged_out` via `$( )` first -- exactly like `awk_out` and
# `agg_out` above -- rather than piping straight into `done < <( ... )`: a process substitution's
# own exit status is not observable by the consuming shell at all (unlike a pipeline inside a
# plain `$( )`, which `$?` reports immediately after), so an awk failure inside `< <(...)` would
# be invisible to `measure_failures` -- the same false-clean shape this task closes elsewhere. The
# loop below then reads from `merged_out` via `printf '%s\n' | ...` in a process substitution
# again, purely so `merged_lines+=` lands in this shell rather than a forked one -- `merged_out`
# itself was already fully captured by that point, so this second stage cannot fail silently in a
# way that matters (an empty `merged_out` just means no merged labels, handled by `-n` below).
merged_lines=()
if [[ -n "$rawlabels_mem" ]]; then
  merged_out="$(printf '%s\n' "${rawlabels_mem%$'\n'}" | awk -F'\t' '
    {
      label = $2; raw = $3; key = label SUBSEP raw
      if (!(key in seen)) { seen[key] = 1; cnt[label]++ }
    }
    END {
      for (l in cnt) if (cnt[l] > 1) printf "%s\t%d\n", l, cnt[l]
    }
  ')"
  merged_rc=$?
  if [[ "$merged_rc" -ne 0 ]]; then
    measure_failures=$((measure_failures + 1))
  fi

  if [[ -n "$merged_out" ]]; then
    while IFS=$'\t' read -r mlabel mcount; do
      merged_lines+=("$mlabel ($mcount raw labels)")
    done < <(printf '%s\n' "$merged_out")
  fi
fi

# ---------- subject drift, from the per-run stamps ----------

# Computed entirely from `stamp_vals`/`stamp_rcs` -- this process'\''s own in-loop record of every
# stamp it took (one per run plus the closing stamp) -- never by re-opening stamps.tsv. A stamp
# value is always either empty or a single hex digest token from subject_stamp() (no tabs, no
# embedded newlines), so plain bash string comparison over the array is sufficient here; no
# associative array is used to key on it (the file'\''s own top-of-file warning about a bare/empty
# key going into a bash associative array does not apply to a linear scan).
subject="unknown"
if [[ "${#stamp_vals[@]}" -gt 0 ]]; then
  bad_stamp_count=0
  for _rc in "${stamp_rcs[@]}"; do
    [[ "$_rc" -ne 0 ]] && bad_stamp_count=$((bad_stamp_count + 1))
  done
  if [[ "$bad_stamp_count" -gt 0 ]]; then
    # The stamp COMMAND itself failed to run -- distinct from "ran fine, printed nothing because
    # this is not a git tree". Must not silently pass as stable: that is exactly the promote-a-
    # vague-verdict-into-a-positive hazard this repo has already measured.
    subject="UNVERIFIABLE"
  else
    first_stamp="${stamp_vals[0]}"
    if [[ -z "$first_stamp" ]]; then
      nonempty=0
      for _sv in "${stamp_vals[@]}"; do
        [[ -n "$_sv" ]] && nonempty=$((nonempty + 1))
      done
      if [[ "$nonempty" -eq 0 ]]; then subject="not-a-repo"; else subject="MOVED"; fi
    else
      differs=0
      for _sv in "${stamp_vals[@]}"; do
        [[ "$_sv" != "$first_stamp" ]] && differs=$((differs + 1))
      done
      if [[ "$differs" -eq 0 ]]; then subject="stable"; else subject="MOVED"; fi
    fi
  fi
  unset _rc _sv
fi

# ---------- verdict ----------

if [[ "$measure_failures" -gt 0 ]]; then
  # FIRST arm, ahead of every other check: an extraction that itself failed is not a measurement
  # of "no rows" -- it is no measurement at all, and reporting PASS or FAIL over it would be
  # exactly the promote-a-vague-verdict-into-a-positive hazard this repo has already measured
  # elsewhere. Closes the false clean where a broken extraction (e.g. a poisoned PATH resolving
  # `awk` to something that cannot parse the program) reads identically to a rowless subject.
  verdict="INDETERMINATE"
  result_rc=4
  printf 'REASON: %s extraction(s) failed -- the sweep could not read what it measured\n' "$measure_failures"
elif [[ "$completed" -gt "$n" ]]; then
  # Should be structurally impossible now that `completed` above is bounded to distinct run ids
  # within 1..n -- kept as an assertion on the INVARIANT itself: insurance against a future
  # rewrite (or a corruption route nobody has enumerated yet) reintroducing an impossible
  # denominator, not a route this suite can currently force to fire on its own.
  verdict="INDETERMINATE"
  result_rc=4
  printf 'REASON: completed=%s exceeds planned n=%s -- not a measurement, refusing to report a verdict\n' \
    "$completed" "$n"
elif [[ "$execfail_all" -eq 1 ]]; then
  verdict="INDETERMINATE"
  result_rc=4
  printf 'REASON: every completed run exited 126 or 127 (the shell'"'"'s reserved exec-failure codes) -- the subject was never actually executed\n'
elif [[ "$completed" -lt "$n" ]]; then
  verdict="INCOMPLETE"
  result_rc=3
elif [[ "$subject" == "MOVED" || "$subject" == "UNVERIFIABLE" || "$subject" == "unknown" || "$total_varying" -gt 0 ]]; then
  verdict="FAIL"
  result_rc=1
elif [[ "$row_ambiguous" -gt 0 ]]; then
  verdict="INDETERMINATE"
  result_rc=4
  printf 'REASON: a label received two or more distinct raw rows in one run and its raw texts changed across runs -- a changed diagnostic cannot be told apart from rows trading verdicts\n'
else
  verdict="PASS"
  result_rc=0
fi

# A short denominator does not retract variance already found. Without this, the shape this repo
# has already measured is live: a sweep finds a real flake AND is skipped mid-run -> INCOMPLETE ->
# a caller reads that as "re-run me" -> a clean re-run at a low n misses the flake a large fraction
# of the time -> the clean re-run overwrites a defect that was already caught once.
if [[ "$verdict" == "INCOMPLETE" && "$total_varying" -gt 0 ]]; then
  printf 'NOTE: variance was found despite the incomplete sweep -- a clean re-run does not retract it\n'
fi

if [[ "${#varying_names[@]}" -gt 0 ]]; then
  printf 'VARYING LABELS:\n'
  for l in "${varying_names[@]}"; do
    printf '  %s\n' "$l"
  done
fi

if [[ "${#ambiguous_names[@]}" -gt 0 ]]; then
  printf 'AMBIGUOUS LABELS:\n'
  for l in "${ambiguous_names[@]}"; do
    printf '  %s\n' "$l"
  done
fi

if [[ "${#merged_lines[@]}" -gt 0 ]]; then
  for m in "${merged_lines[@]}"; do
    printf 'MERGED: %s\n' "$m"
  done
fi

case "$subject" in
  MOVED)
    printf 'SUBJECT: MOVED during the sweep -- this verdict does not describe one stable tree\n'
    ;;
  UNVERIFIABLE)
    printf 'SUBJECT: UNVERIFIABLE -- the stamp command itself failed to run; drift could not be judged\n'
    ;;
  not-a-repo)
    printf 'SUBJECT: not-a-repo -- outside a git tree, so drift could not be judged\n'
    ;;
  unknown)
    # MAJOR: "unknown" is the initial value of $subject. Originally reached whenever stamps.tsv
    # itself was unwritable, back when drift was computed by re-reading that file -- since the
    # read-back BLOCKER fix, drift is computed from `stamp_vals`/`stamp_rcs` (this process's own
    # in-loop record, appended unconditionally every iteration before the possibly-failing file
    # write), so this branch is now believed STRUCTURALLY UNREACHABLE during a live run: `n >= 2`
    # is enforced by usage validation, and the loop always populates at least one entry regardless
    # of disk write success. Kept as defensive insurance rather than deleted -- the same reasoning
    # as the `completed -gt n` INDETERMINATE branch above -- against a future rewrite (or a
    # corruption route nobody has enumerated yet) leaving `stamp_vals` empty after all. Must not
    # fall through this case with no output: that is exactly the promote-a-vague-verdict-into-a-
    # positive hazard this repo has already measured for UNVERIFIABLE, one sentinel value over.
    printf 'SUBJECT: unknown -- no stamp data was recorded at all; drift could not be judged\n'
    ;;
  stable)
    # Say what `stable` does NOT cover, because it reads stronger than it is. Measured: the stamp
    # hashes HEAD + `git diff HEAD` + `git status --porcelain`, and an UNTRACKED file's porcelain
    # line is `?? path` whatever its content -- so rewriting an untracked file leaves the stamp
    # identical and this row reports `stable` over a tree that moved. Tracked edits ARE caught,
    # and so is a new untracked file, at the top level and inside every checked-out submodule at
    # any depth, even under the specific config keys the stamp overrides (status.showUntrackedFiles,
    # diff.ignoreSubmodules, submodule.<name>.ignore, diff.external, a submodule's own untracked
    # settings). Other change-hiding configuration is NOT overridden -- a lossy textconv driver,
    # clean/smudge filters, core.fileMode=false, assume-unchanged/skip-worktree -- nor are
    # gitignored paths, untracked nested repos or uninitialized submodule directories; see the
    # header's NOT-covered list. Inherited from run-long's subject_stamp, not introduced here.
    printf 'SUBJECT: stable -- tracked state unchanged across every run. NOTE: an untracked file'\''s\n'
    printf '         CONTENT can change without moving this stamp, so a subject that reads untracked\n'
    printf '         fixtures can still vary for reasons this does not see.\n'
    ;;
esac

# Reported, never trusted: the verdict above never read any of these files (see the INVARIANT at
# the top), so a write failure here does not change it -- it only means the operator's own
# debugging record is incomplete.
if [[ "$artifact_failures" -gt 0 ]]; then
  printf 'ARTIFACTS: %d write(s) failed -- the files in %s are incomplete; the verdict does not read them\n' \
    "$artifact_failures" "$artifact_dir"
fi

printf 'RESULT: %s rc=%d runs=%d/%d rows=%d varying=%d subject=%s\n' \
  "$verdict" "$result_rc" "$completed" "$n" "$rows" "$total_varying" "$subject"

exit "$result_rc"

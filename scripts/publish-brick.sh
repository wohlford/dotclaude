#!/usr/bin/env bash
set -uo pipefail

# Script: publish-brick.sh
# Purpose: Materialise, prove and commit ONE brick, tagged onto main or re-derived onto dev
# Usage:   publish-brick.sh [--scope <path>] [--artifact-dir <path>] \
#            <version> <endpoint-commit> <subject> [folded-constituent...]
#          publish-brick.sh --dev [--final] [--scope <path>] [--artifact-dir <path>] \
#            <oracle-commit> <subject> <file>...
#
# This is the per-brick engine of /propagate's adopted publish path (step 3-4). ONE brick per
# invocation, on purpose: the operator drives the loop, so the per-brick checkpoint that makes
# the publish path reviewable survives, while the parts a human reads wrong under repetition —
# a verdict line, a tag that silently failed to mint — are read mechanically every time.
#
# It never pushes and never touches the watermark. Publishing (step 6) and advancing
# refs/published/main (step 7) stay foreground, human-authorized, and out of this script.
#
# MATERIALISATION. A brick's file set is the UNION of its constituents' files; its content is
# the ENDPOINT commit's, the endpoint being the last constituent. The file set is partitioned by
# presence AT THE ENDPOINT: a path present there is checked out (`git checkout <endpoint> --
# <files>`), which handles a NON-CONTIGUOUS fold with no scratch branch and no patch application,
# and cannot half-apply the way a conflicting cherry-pick or `git apply` can; a path absent there
# was deleted by the constituent set and is removed with `git rm`. Its precondition is that no
# constituent RENAMES or COPIES a path, because neither checkout nor rm can express those; that
# is asserted up front rather than assumed.
#
# TWO SHAPE ASSERTIONS, because one of them is the half that catches an overreach:
#   A. every brick file is byte-identical to the endpoint — the materialisation reached far enough
#   B. nothing OUTSIDE the brick's file set changed — the materialisation reached no further
#
# WHICH COPY OF WHAT. The two resolutions differ on purpose:
#   * the AUDIT comes from the SCOPE (`<scope>/skills/audit/audit.sh`) — it judges the tree being
#     built, so it must be that tree's own copy, never the installed one it may be replacing
#   * this script's own helper library resolves relative to THIS FILE — a construction tool must
#     not vanish mid-build when the working tree is checked out to a commit predating it
# Both paths are printed, so a verdict is never reported without saying what produced it.
#
# THE AUDIT VERDICT IS AN ALLOWLIST. A brick is proven only when audit.sh's LAST non-blank line
# of stdout is `RESULT: PASS rc=0…` AND the process exited 0. FAIL, ERROR, INCOMPLETE, an
# unanticipated status, a line that is not last, and an ABSENT line are each a failure to prove.
# The absent one is the one to watch: a killed sweep prints a prefix of PASS lines and no
# summary, so every cheap instrument reads it as clean. A verdict/rc disagreement also fails —
# fail closed, and name which side disagreed.
#
# ROLLBACK IS LIMITED TO THIS SCRIPT'S OWN MESS. A refusal BEFORE the commit restores exactly the
# paths this run wrote, so the next attempt still meets its clean-tree precondition. A failure
# AFTER the commit lands prints the recovery and runs none of it — resetting a branch and
# deleting tags is the mutating class the publish path keeps human-checkpointed.
#
# DEV MODE (--dev). The same engine builds one brick of /feature's adopted re-derivation onto `dev`:
# the ORACLE is the frozen feature tip, and the brick's files are listed explicitly, because that
# re-derivation partitions one tree BY FILE rather than by constituent commits. What changes:
#   * preconditions — branch `dev`; the oracle is a commit; a PROGRESS INVARIANT (every path dev has
#     changed since it last shared history with the oracle already equals the oracle, which catches
#     dev having moved after the branch was cut, and an earlier brick that diverged — the one failure
#     tip convergence cannot see, since it passes by reverting dev's interim work); every listed file
#     differs between HEAD and the oracle; the subject is conventional and under the repo's
#     .commit-conventions.toml ADVISE threshold, because a commit made inside this script never
#     reaches the PreToolUse subject guard that /commit's path relies on
#   * no CHANGELOG entry, no tag, no version
#   * after the audit it reports how many paths still differ; `--final` asserts there are none
# Renames need no refusal here: a rename is its two paths, listed, and presence at the oracle
# already checks out the new one and removes the old.
#
# Exit codes:
#   0 — the brick is applied, proven and committed (and, in publish mode, tagged)
#   1 — an assertion failed; the brick is NOT proven
#   2 — usage error (bad flags/arguments, unreadable scope, not an adopted repo)
#   129/130/143 — died on a trapped signal (HUP/INT/TERM)
#
# Terminal verdict line: as its LAST line of stdout, `RESULT: <STATUS> rc=<n> brick=<version>`
# (`brick=dev` in dev mode)
# where STATUS is PASS | FAIL | ERROR (nothing ran) | INCOMPLETE (died partway). Clean is
# EXACTLY `RESULT: PASS` — an allowlist. The line cannot be emitted on SIGKILL, so its ABSENCE
# never means clean: it means the run did not complete.
#
# bash-3.2/BSD-safe. NOTE: no `-e` — a failing assertion must reach its verdict line.

script_name="$(basename "${BASH_SOURCE[0]}")"
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# git_lit runs a PATHSPEC-BEARING git call with literal pathspecs. A brick file named `a[1].txt` is
# otherwise a glob that also matches `a1.txt`, and a checkout of it materialises both (measured
# 2026-09-16). Deliberately a wrapper and NOT an exported environment variable: that reaches every
# child, and the audit discovers files with pathspec globs — under it the real audit skipped 6 of 17
# checks as "nothing to check" and still printed RESULT: PASS (measured 2026-09-16). git itself
# still passes literal-pathspec mode down to any hook or filter driven BY these calls (a
# clean/smudge filter, a checkout hook) — that reach is unavoidable and not what this wrapper
# guards against. What it guarantees is narrower and load-bearing: the AUDIT is a SEPARATE process
# this script spawns, never a child of these git calls, so it never inherits literal-pathspec mode
# through them either way.
git_lit() { git --literal-pathspecs -C "$scope" "$@"; }

# Model constants, not configuration: the publication model fixes both branch names.
readonly PUBLISHED_BRANCH=main
readonly WORKING_BRANCH=dev

version=""
scope=""
artifact_dir=""
endpoint=""
subject=""
audit_path=""
lib_path="$script_dir/lib/changelog_entry.py"
subject_lib="$script_dir/lib/commit_subject.py"
mode=publish
final=no
dev_hint=""
dev_note=""
files_raw=""
constituents=""
files_list=""
files=()
phase=init
reported=no
materialised=no
changelog_written=no
committed=no

usage() {
  printf 'Usage: %s [--scope <path>] [--artifact-dir <path>] <version> <endpoint> <subject> [constituent...]\n' \
    "$script_name" >&2
  printf '       %s --dev [--final] [--scope <path>] [--artifact-dir <path>] <oracle> <subject> <file>...\n' \
    "$script_name" >&2
}

result_line() { # status rc
  printf 'RESULT: %s rc=%d brick=%s\n' "$1" "$2" "${version:-?}"
  reported=yes
}

# Covers only the paths main() never reaches the end of, so it can emit ONLY ERROR or
# INCOMPLETE — never a clean verdict.
on_exit() { # exit-status
  [ "$reported" = yes ] && return
  if [ "$phase" = init ] && [ "$1" -eq 2 ]; then
    result_line ERROR "$1"
  else
    result_line INCOMPLETE "$1"
  fi
}

# A fatal precondition prints its reason to STDOUT as well: stdout is the artifact an operator
# (or a transcript) reads back, and a refusal whose cause lives only on a discarded stderr is
# indistinguishable from an unexplained one.
fatal() { # message
  printf '%s: %s\n' "$script_name" "$1"
  usage
  result_line ERROR 2
  exit 2
}

# rollback_worktree restores ONLY the paths this run wrote. A path that did not exist at HEAD is
# removed rather than checked out, since `git checkout HEAD -- <new path>` has nothing to restore.
rollback_worktree() {
  local f
  [ "$materialised" = yes ] || return 0
  [ -n "$files_list" ] || return 0
  while IFS= read -r f; do
    [ -n "$f" ] || continue
    if git -C "$scope" cat-file -e "HEAD:$f" 2>/dev/null; then
      git_lit checkout -q HEAD -- "$f" 2>/dev/null
    else
      git_lit reset -q HEAD -- "$f" 2>/dev/null
      rm -f -- "$scope/$f"
    fi
  done < "$files_list"
  if [ "$changelog_written" = yes ]; then
    git_lit checkout -q HEAD -- CHANGELOG.md 2>/dev/null
  fi
}

fail_brick() { # message
  printf 'FAIL %s\n' "$1"
  if [ "$committed" = yes ] && [ "$mode" = dev ] && [ -n "$dev_note" ]; then
    printf '  the brick commit landed on %s. %s\n' "$WORKING_BRANCH" "$dev_note"
  elif [ "$committed" = yes ] && [ "$mode" = dev ]; then
    printf '  the brick commit ALREADY LANDED on %s. Recovery (run it yourself):\n' "$WORKING_BRANCH"
    if [ -n "$dev_hint" ]; then
      printf '    %s\n' "$dev_hint"
    else
      printf '    git -C %s reset --hard HEAD~1   # drop the brick, fix the cause, re-run\n' "$scope"
    fi
  elif [ "$committed" = yes ]; then
    printf '  the brick commit ALREADY LANDED on %s. Recovery (run it yourself):\n' "$PUBLISHED_BRANCH"
    printf '    git -C %s tag -d %s   # only if the tag was minted\n' "$scope" "$version"
    printf '    git -C %s reset --hard HEAD~1\n' "$scope"
  else
    rollback_worktree
    printf '  nothing was committed; the working tree was restored.\n'
  fi
  result_line FAIL 1
  exit 1
}

# assert_applicable refuses the two edit shapes a checkout cannot express, and the ordering
# error that would silently take the wrong content.
assert_applicable() {
  local c bad
  for c in $constituents; do
    git -C "$scope" rev-parse --verify -q "$c^{commit}" >/dev/null \
      || fail_brick "constituent $c is not a commit in $scope"
    git -C "$scope" merge-base --is-ancestor "$c" "$WORKING_BRANCH" 2>/dev/null \
      || fail_brick "constituent $c is not an ancestor of $WORKING_BRANCH — bricks come from $WORKING_BRANCH"
    # Clear by ALLOWLIST: A(dd), M(odify), T(ype change) survive a checkout, D(elete) survives
    # a `git rm`; R, C, U and anything unanticipated do not — materialisation cannot express a
    # rename or a copy.
    bad="$(git -C "$scope" show --name-status --format= "$c" | awk 'NF && $1 !~ /^[AMTD]$/')"
    if [ -n "$bad" ]; then
      printf '  %s\n' "$bad"
      fail_brick "constituent $c renames or copies a path — materialisation cannot express that"
    fi
    if [ "$c" != "$endpoint" ]; then
      git -C "$scope" merge-base --is-ancestor "$c" "$endpoint" 2>/dev/null \
        || fail_brick "constituent $c is NEWER than the endpoint $endpoint — the endpoint must be the last constituent"
    fi
  done
}

# assert_dev_progress is the adopted finish's "dev must not have moved" precondition, checked at
# EVERY brick rather than once at freeze time. Every path dev has changed since it last shared history
# with the oracle must already equal the oracle: a commit landing on dev after the branch was cut
# breaks that, and so does an earlier brick that diverged.
assert_dev_progress() {
  local base f names
  local progress_files=() diverged_files=()
  base="$(git -C "$scope" merge-base HEAD "$endpoint" 2>/dev/null)" \
    || fail_brick "HEAD and the oracle $endpoint share no history — there is nothing to re-derive onto"
  # NUL-separated (-z), not newline-separated: git's default name-only output QUOTES a path
  # holding non-ASCII bytes (e.g. `café.txt` -> "caf\303\251.txt"), which then never matches the
  # literal pathspec built from it below — silently dropping the path from the diverged set
  # (measured 2026-09-16). -z also disables that quoting. A bash variable cannot hold an embedded
  # NUL (command substitution silently drops the byte, concatenating entries), so each list goes to
  # a file and is read from there. A FILE, not a process substitution: `< <(git …)` discards git's
  # exit status, so a diff that errored would read as an empty list — "nothing changed" — and the
  # invariant would pass having measured nothing.
  names="$(mktemp)"
  git -C "$scope" diff -z --name-only --no-renames "$base" HEAD > "$names" \
    || { rm -f "$names"; fail_brick "progress: could not list what $WORKING_BRANCH changed since $base"; }
  while IFS= read -r -d '' f; do
    [ -n "$f" ] && progress_files[${#progress_files[@]}]="$f"
  done < "$names"
  rm -f "$names"
  [ "${#progress_files[@]}" -gt 0 ] || return 0
  names="$(mktemp)"
  git_lit diff -z --name-only --no-renames "$endpoint" HEAD -- "${progress_files[@]}" > "$names" \
    || { rm -f "$names"; fail_brick "progress: could not compare $WORKING_BRANCH's changed paths with the oracle $endpoint"; }
  while IFS= read -r -d '' f; do
    [ -n "$f" ] && diverged_files[${#diverged_files[@]}]="$f"
  done < "$names"
  rm -f "$names"
  [ "${#diverged_files[@]}" -gt 0 ] || return 0
  for f in "${diverged_files[@]}"; do
    printf '  diverged: %s\n' "$f"
  done
  fail_brick "progress: $WORKING_BRANCH changed the paths above since it last met the oracle, and they no longer match it — $WORKING_BRANCH moved after the branch was cut (rebase the branch and re-freeze), or an earlier brick diverged"
}

# check_dev_subject supplies what /commit and the PreToolUse subject guard supply on the ordinary
# commit path, neither of which ever sees a commit made inside this script. The length policy is the
# repo's own, read through the shared library. It refuses at the ADVISE threshold rather than only at
# BLOCK because the post-commit advisor that would flag 72-79 never sees this commit; ALLOW_LONG_SUBJECT=1
# is honoured for both tiers, exactly as /commit documents it.
check_dev_subject() {
  local re='^(feat|fix|docs|style|refactor|perf|test|chore|ci|revert)(\([^)]+\))?!?: [^[:space:]]'
  local grade
  [[ "$subject" =~ $re ]] \
    || fail_brick "the subject is not conventional — want <type>[(scope)][!]: <subject>, got: $subject"
  case "$subject" in
    *.) fail_brick 'the subject must not end with a period' ;;
  esac
  [ -f "$subject_lib" ] || fail_brick "missing helper library: $subject_lib"
  grade="$(python3 - "$scope" "$subject" "$(dirname "$subject_lib")" <<'PY'
import sys
sys.path.insert(0, sys.argv[3])
import commit_subject as cs
policy = cs.load_policy(sys.argv[1])
print("inert" if policy is None else cs.classify(sys.argv[2], policy))
PY
)"
  case "$grade" in
    inert|ok) ;;
    advise|block)
      if [ "${ALLOW_LONG_SUBJECT:-}" = 1 ]; then
        printf '  subject: %s characters, over the policy threshold — allowed by ALLOW_LONG_SUBJECT=1\n' "${#subject}"
      else
        fail_brick "the subject is ${#subject} characters, at or over this repo's .commit-conventions.toml threshold — shorten it, or lead with ALLOW_LONG_SUBJECT=1 as /commit allows"
      fi ;;
    *) fail_brick "the subject policy check reached no verdict (got: ${grade:-nothing})" ;;
  esac
}

# report_dev_convergence runs after a PROVEN dev brick. It always says how much of the oracle is
# still outstanding; under --final it asserts nothing is.
report_dev_convergence() {
  local remaining n f
  remaining="$(git -C "$scope" diff --name-only --no-renames "$endpoint" HEAD)"
  n="$(grep -c . <<<"$remaining")"
  printf '  remaining: %s path(s) still differ from the oracle %s\n' "$n" "$endpoint"
  [ "$final" = yes ] || return 0
  if [ -n "$remaining" ]; then
    while IFS= read -r f; do
      [ -n "$f" ] && printf '  still differs: %s\n' "$f"
    done <<EOF
$remaining
EOF
    dev_note="The brick itself is proven and stays; build another brick from the paths above, with --final on the last one."
    fail_brick "--final: HEAD does not converge on the oracle $endpoint"
  fi
  printf '  converged: HEAD tree == oracle %s\n' "$endpoint"
}

# The audit belonging to the tree being proven, read as an allowlist over its own verdict line.
run_audit() { # artifact-path
  local artifact="$1" out err arc verdict
  [ -x "$audit_path" ] || fail_brick "no executable audit at $audit_path — the brick cannot be proven"
  out="$(mktemp)"; err="$(mktemp)"
  "$audit_path" --scope "$scope" >"$out" 2>"$err"; arc=$?
  # The exit status is recorded INSIDE the artifact: its absence there is then itself the signal
  # that the run died, which no external account of the exit code can be trusted to tell us.
  {
    cat "$out"
    printf '\n--- stderr ---\n'
    cat "$err"
    printf 'AUDIT_EXIT_STATUS=%d\n' "$arc"
  } > "$artifact"
  verdict="$(grep -v '^[[:space:]]*$' "$out" | tail -1)"
  rm -f "$out" "$err"
  printf '  audit: %s\n' "$artifact"
  case "$verdict" in
    "RESULT: PASS rc=0"*)
      if [ "$arc" -ne 0 ]; then
        fail_brick "audit said PASS but exited $arc — a verdict/status disagreement fails closed"
      fi
      printf '  audit verdict: %s\n' "$verdict"
      ;;
    "") fail_brick "audit printed NO verdict line (exit $arc) — an absent verdict is not a pass" ;;
    *)  fail_brick "audit verdict is not a pass (exit $arc): $verdict" ;;
  esac
}

main() {
  local c date_str changed f sig signing head_subject artifact
  local checkout_files=() rm_files=()
  local head_type end_type audit_mode

  while [ $# -gt 0 ]; do
    case "$1" in
      --scope)
        [ $# -ge 2 ] || fatal 'the --scope flag needs a path'
        scope="$2"; shift 2 ;;
      --artifact-dir)
        [ $# -ge 2 ] || fatal 'the --artifact-dir flag needs a path'
        artifact_dir="$2"; shift 2 ;;
      --dev) mode=dev; shift ;;
      --final) final=yes; shift ;;
      --) shift; break ;;
      -*) fatal "unknown argument: $1" ;;
      *)  break ;;
    esac
  done

  if [ "$mode" = dev ]; then
    [ $# -ge 3 ] || fatal 'dev mode needs <oracle> <subject> <file>...'
    endpoint="$1"; subject="$2"; shift 2
    version=dev
    for f in "$@"; do
      case "$f" in
        *$'\n'*) fatal 'a brick file name must not contain a newline' ;;
        # `--*` only: the engine has no short flags, so a single leading dash (a legitimate file
        # name like `-x.txt`) is never a flag lookalike worth refusing.
        --*) fatal "brick file $f looks like a flag — flags go before <oracle>" ;;
      esac
    done
    files_raw="$(printf '%s\n' "$@")"
  else
    [ "$final" = no ] || fatal 'the --final flag belongs to --dev mode only'
    [ $# -ge 3 ] || fatal 'need <version> <endpoint> <subject>'
    version="$1"; endpoint="$2"; subject="$3"; shift 3
    # The remaining arguments are the folded constituents; the endpoint joins them as the last.
    # Duplicates are dropped, keeping first-seen order.
    constituents="$(printf '%s\n' "$@" "$endpoint" | grep -v '^$' | awk '!seen[$0]++')"

    case "$version" in
      v[0-9]*.[0-9]*.[0-9]*) ;;
      *) fatal "version must look like vX.Y.Z, got: $version" ;;
    esac
  fi
  [ -n "$subject" ] || fatal 'the subject must not be empty'
  case "$subject" in
    *$'\n'*) fatal 'the subject must be a single line' ;;
  esac

  if [ -z "$scope" ]; then
    scope="$(git rev-parse --show-toplevel 2>/dev/null)" \
      || fatal 'no --scope given and the working directory is not a git repository'
  fi
  git -C "$scope" rev-parse --show-toplevel >/dev/null 2>&1 \
    || fatal "not a git repository: $scope"
  scope="$(git -C "$scope" rev-parse --show-toplevel)"
  [ -f "$scope/.publication.toml" ] \
    || fatal "no .publication.toml at $scope — not an adopted repo, so there is no publish path"
  [ -f "$lib_path" ] || fatal "missing helper library: $lib_path"

  audit_path="$scope/skills/audit/audit.sh"
  if [ -z "$artifact_dir" ]; then
    artifact_dir="$(mktemp -d)"
  fi
  mkdir -p "$artifact_dir" || fatal "cannot create the artifact directory: $artifact_dir"
  if [ "$mode" = dev ]; then
    # One artifact per brick: dev bricks share no version, so name each for the HEAD it builds on.
    artifact="$artifact_dir/audit-dev-$(git -C "$scope" rev-parse --short HEAD 2>/dev/null || printf 'nohead').txt"
  else
    artifact="$artifact_dir/audit-$version.txt"
  fi

  phase=checking

  if [ "$mode" = dev ]; then
    printf 'brick dev <- oracle %s\n' "$endpoint"
  else
    printf 'brick %s <- %s\n' "$version" "$(printf '%s' "$constituents" | tr '\n' ' ')"
  fi
  printf '  scope:   %s\n' "$scope"
  printf '  audit:   %s (the tree being proven)\n' "$audit_path"
  printf '  helper:  %s (this tool, not the tree)\n' "$lib_path"

  # ---------- preconditions ----------
  if [ "$mode" = dev ]; then
    [ "$(git -C "$scope" rev-parse --abbrev-ref HEAD)" = "$WORKING_BRANCH" ] \
      || fail_brick "not on branch $WORKING_BRANCH — a re-derivation brick lands on $WORKING_BRANCH only"
  else
    [ "$(git -C "$scope" rev-parse --abbrev-ref HEAD)" = "$PUBLISHED_BRANCH" ] \
      || fail_brick "not on branch $PUBLISHED_BRANCH — a brick is appended to the published branch only"
  fi
  [ -z "$(git -C "$scope" status --porcelain)" ] \
    || fail_brick 'the working tree is not clean — materialisation needs a clean base to be provable'
  if [ "$mode" = dev ]; then
    [ -x "$audit_path" ] \
      || fail_brick "no executable audit at $audit_path — this repo has no audit.sh of its own, so a dev brick cannot be proven; re-derive by hand with the installed /audit"
    git -C "$scope" rev-parse --verify -q "$endpoint^{commit}" >/dev/null \
      || fail_brick "the oracle $endpoint is not a commit in $scope"
    check_dev_subject
    assert_dev_progress
  else
    git -C "$scope" rev-parse --verify -q "refs/tags/$version" >/dev/null \
      && fail_brick "tag $version already exists — refusing to move a tag that may already be published"

    assert_applicable
  fi

  # ---------- file set: the UNION of every constituent's paths ----------
  files_list="$(mktemp)"
  if [ "$mode" = dev ]; then
    printf '%s\n' "$files_raw" | grep -v '^$' | sort -u > "$files_list"
  else
    for c in $constituents; do
      git -C "$scope" show --name-only --format= "$c"
    done | grep -v '^$' | sort -u > "$files_list"
  fi
  [ -s "$files_list" ] || fail_brick 'the brick has an empty file set'
  while IFS= read -r f; do
    [ -n "$f" ] || continue
    files[${#files[@]}]="$f"
    printf '  file: %s\n' "$f"
  done < "$files_list"

  # ---------- dev: every listed file must differ between HEAD and the oracle ----------
  # A path in neither tree, a typo, and a file an earlier brick already landed all read as "nothing
  # to do" to a checkout; each is refused by name instead of becoming a silently smaller brick.
  if [ "$mode" = dev ]; then
    for f in "${files[@]}"; do
      case "$f" in
        /*|..|../*|*/..|*/../*) fail_brick "brick file $f must be a path relative to the repo root" ;;
        .|./) fail_brick "brick file $f is a directory — list its files" ;;
      esac
      # A directory listed as a pathspec still MATCHES (it is not a glob, so --literal-pathspecs
      # does not exclude it) — everything under it, which shape B then trips over one un-listed
      # path at a time rather than refusing the directory by name (measured 2026-09-16, leaving a
      # staged leftover behind on the FAIL path). Refuse only when the path is a TREE on at least
      # one side and neither side is a blob: a blob on HEAD's side is a file becoming a directory
      # (`a.txt` becoming `a.txt/in`, both listed), which builds. What this does NOT make buildable,
      # measured by review: a directory becoming a file (`d d/in`) passes here and then fails closed
      # in the `git rm` arm — build it as two bricks, `d/in` then `d`; and a gitlink reads as ABSENT
      # to `cat-file -t` when its commit is not in this repository, so the `commit` exceptions below
      # rarely fire and a gitlink beside a tree is refused as a directory (fail closed).
      head_type="$(git -C "$scope" cat-file -t "HEAD:$f" 2>/dev/null)"
      end_type="$(git -C "$scope" cat-file -t "$endpoint:$f" 2>/dev/null)"
      if { [ "$head_type" = tree ] || [ "$end_type" = tree ]; } \
        && [ "$head_type" != blob ] && [ "$head_type" != commit ] \
        && [ "$end_type" != blob ] && [ "$end_type" != commit ]; then
        fail_brick "brick file $f is a directory — list its files"
      fi
      if git_lit diff --quiet --no-renames HEAD "$endpoint" -- "$f"; then
        fail_brick "brick file $f already matches the oracle at HEAD — a typo, a path in neither tree, or one an earlier brick landed"
      fi
      # run_audit below reads the SCOPE's live skills/audit/audit.sh, which still exists until the
      # commit lands — a brick that deletes it or drops its exec bit would pass that check and
      # only fail once committed, with nothing left able to prove the NEXT brick either.
      if [ "$f" = skills/audit/audit.sh ]; then
        audit_mode="$(git -C "$scope" ls-tree "$endpoint" -- skills/audit/audit.sh | awk '{print $1}')"
        [ "$audit_mode" = 100755 ] \
          || fail_brick "brick file skills/audit/audit.sh would leave no executable audit to prove this brick — land it in a brick with the audit's replacement, or re-derive by hand"
      fi
    done
  fi

  # ---------- materialise: split the file set by presence at the endpoint ----------
  # A path present at the endpoint is checked out; a path absent there was deleted by the
  # constituent set and is removed with `git rm`. The rm-vs-skip choice is read from presence
  # AT HEAD, decided BEFORE invoking `git rm` — never from `git rm`'s own exit status. rc=128
  # also covers an index lock or a corrupt index, so promoting that code to mean "this is the
  # added-and-deleted case" would hand a SKIP to that whole preimage. A path added and then
  # deleted within this brick's own span is absent at HEAD too, so `git rm` has nothing to
  # remove there — that is the one legitimate SKIP. Any other `git rm` failure stays fatal.
  for f in "${files[@]}"; do
    if git -C "$scope" cat-file -e "$endpoint:$f" 2>/dev/null; then
      checkout_files[${#checkout_files[@]}]="$f"
    elif git -C "$scope" cat-file -e "HEAD:$f" 2>/dev/null; then
      rm_files[${#rm_files[@]}]="$f"
    else
      printf '  skip: %s (added and deleted within this brick — nothing to remove)\n' "$f"
    fi
  done

  # Set before either arm runs: `git checkout <endpoint> -- <mixed set>` is measured to error
  # AND partially apply, so a failure partway through must still be treated as having written
  # to the worktree, or rollback_worktree's own guard (`[ "$materialised" = yes ]`) would skip
  # restoring what already landed.
  materialised=yes
  if [ "${#checkout_files[@]}" -gt 0 ] \
    && ! git_lit checkout "$endpoint" -- "${checkout_files[@]}"; then
    fail_brick "could not materialise the brick from $endpoint"
  fi
  if [ "${#rm_files[@]}" -gt 0 ] \
    && ! git_lit rm -q -- "${rm_files[@]}"; then
    fail_brick "could not remove a deleted path from the brick"
  fi

  # ---------- shape A: the brick's files ARE the endpoint's ----------
  # `git diff` has no --pathspec-from-file, so the pathspecs are passed as arguments.
  if ! git_lit diff --quiet "$endpoint" -- "${files[@]}"; then
    fail_brick "shape A: the brick's files do not match $endpoint after materialisation"
  fi

  # ---------- shape B: and NOTHING else moved ----------
  changed="$( { git -C "$scope" diff --cached --name-only
                git -C "$scope" diff --name-only
                git -C "$scope" ls-files --others --exclude-standard; } | sort -u )"
  while IFS= read -r f; do
    [ -n "$f" ] || continue
    grep -qxF -- "$f" "$files_list" \
      || fail_brick "shape B: $f changed but is not one of the brick's files — the materialisation reached too far"
  done <<EOF
$changed
EOF

  # ---------- changelog, inside the brick commit ----------
  date_str="$(git -C "$scope" log -1 --format=%ad --date=short "$endpoint")"
  if [ "$mode" = dev ]; then
    printf '  changelog: dev mode writes none — versioning is %s-only\n' "$PUBLISHED_BRANCH"
  elif [ -f "$scope/CHANGELOG.md" ]; then
    python3 "$lib_path" "$scope/CHANGELOG.md" "$version" "$date_str" "$subject" \
      || fail_brick 'the changelog entry was refused'
    changelog_written=yes
  else
    printf '  changelog: none at %s — skipping the entry\n' "$scope/CHANGELOG.md"
  fi

  # ---------- commit ----------
  # `git rm` above already staged its own arm; staging it again with `git add` on a path that
  # no longer exists in the worktree fatals (rc=128, "did not match any files"). Only the
  # checkout arm still needs staging.
  if [ "${#checkout_files[@]}" -gt 0 ] && ! git_lit add -- "${checkout_files[@]}"; then
    fail_brick 'could not stage the brick'
  fi
  [ "$changelog_written" = yes ] && git_lit add -- CHANGELOG.md
  git -C "$scope" commit -q -m "$subject" || fail_brick 'the brick commit failed'
  committed=yes

  head_subject="$(git -C "$scope" log -1 --format=%s)"
  [ "$head_subject" = "$subject" ] \
    || fail_brick "the commit subject is [$head_subject], not [$subject]"

  # A batch of commits and tags can exhaust a hardware key's cached PIN partway through, after
  # which signing silently stops. Only asserted when the repo actually asked for signing.
  signing="$(git -C "$scope" config --get commit.gpgsign 2>/dev/null || printf 'false\n')"
  if [ "$signing" = true ]; then
    sig="$(git -C "$scope" log -1 --format='%G?')"
    case "$sig" in
      G|U) printf '  commit %s signed (%%G?=%s)\n' "$(git -C "$scope" log -1 --format=%h)" "$sig" ;;
      *)   fail_brick "commit.gpgsign is true but the commit is not signed (%G?=$sig) — a card PIN may have lapsed" ;;
    esac
  else
    printf '  commit %s (signing not enabled in this repo)\n' "$(git -C "$scope" log -1 --format=%h)"
  fi

  # ---------- prove ----------
  if [ "$mode" = dev ]; then
    dev_hint="git -C $scope reset --hard HEAD~1   # the brick's files already equal the oracle; read the audit artifact first — a split holistic pair means re-plan the boundary, while a machine-dependent check or a killed audit is not fixed by re-planning"
  fi
  run_audit "$artifact"

  if [ "$mode" = dev ]; then
    dev_hint=""
    report_dev_convergence
    rm -f "$files_list"
    result_line PASS 0
    return 0
  fi

  # ---------- tag, and assert it exists: `git tag -a` can fail SILENTLY mid-run ----------
  git -C "$scope" tag -a "$version" -m "$subject" || fail_brick "git tag -a $version failed"
  git -C "$scope" tag --points-at HEAD | grep -qx "$version" \
    || fail_brick "tag $version is MISSING after tagging — do not build the next brick on an untagged one"
  printf '  tagged %s at %s\n' "$version" "$(git -C "$scope" log -1 --format=%h)"

  rm -f "$files_list"
  result_line PASS 0
  return 0
}

if [ "${BASH_SOURCE[0]}" = "$0" ]; then
  trap 'on_exit "$?"' EXIT
  trap 'exit 143' TERM
  trap 'exit 130' INT
  trap 'exit 129' HUP
  main "$@"
fi

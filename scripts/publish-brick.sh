#!/usr/bin/env bash
set -uo pipefail

# Script: publish-brick.sh
# Purpose: Materialise, prove, commit and tag ONE recast brick onto the published branch
# Usage:   publish-brick.sh [--scope <path>] [--artifact-dir <path>] \
#            <version> <endpoint-commit> <subject> [folded-constituent...]
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
# the ENDPOINT commit's, the endpoint being the last constituent. So the brick is exactly
# `git checkout <endpoint> -- <files>` — which handles a NON-CONTIGUOUS fold with no scratch
# branch and no patch application, and cannot half-apply the way a conflicting cherry-pick or
# `git apply` can. Its precondition is that no constituent DELETES or RENAMES a path, because a
# checkout cannot express either; that is asserted up front rather than assumed.
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
# Exit codes:
#   0 — the brick is applied, proven, committed and tagged
#   1 — an assertion failed; the brick is NOT proven
#   2 — usage error (bad flags/arguments, unreadable scope, not an adopted repo)
#   129/130/143 — died on a trapped signal (HUP/INT/TERM)
#
# Terminal verdict line: as its LAST line of stdout, `RESULT: <STATUS> rc=<n> brick=<version>`
# where STATUS is PASS | FAIL | ERROR (nothing ran) | INCOMPLETE (died partway). Clean is
# EXACTLY `RESULT: PASS` — an allowlist. The line cannot be emitted on SIGKILL, so its ABSENCE
# never means clean: it means the run did not complete.
#
# bash-3.2/BSD-safe. NOTE: no `-e` — a failing assertion must reach its verdict line.

script_name="$(basename "${BASH_SOURCE[0]}")"
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

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
      git -C "$scope" checkout -q HEAD -- "$f" 2>/dev/null
    else
      git -C "$scope" reset -q HEAD -- "$f" 2>/dev/null
      rm -f -- "$scope/$f"
    fi
  done < "$files_list"
  if [ "$changelog_written" = yes ]; then
    git -C "$scope" checkout -q HEAD -- CHANGELOG.md 2>/dev/null
  fi
}

fail_brick() { # message
  printf 'FAIL %s\n' "$1"
  if [ "$committed" = yes ]; then
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
    # Clear by ALLOWLIST: A(dd), M(odify), T(ype change) survive a checkout; D, R, C, U and
    # anything unanticipated do not.
    bad="$(git -C "$scope" show --name-status --format= "$c" | awk 'NF && $1 !~ /^[AMT]$/')"
    if [ -n "$bad" ]; then
      printf '  %s\n' "$bad"
      fail_brick "constituent $c deletes, renames or copies a path — materialisation cannot express that"
    fi
    if [ "$c" != "$endpoint" ]; then
      git -C "$scope" merge-base --is-ancestor "$c" "$endpoint" 2>/dev/null \
        || fail_brick "constituent $c is NEWER than the endpoint $endpoint — the endpoint must be the last constituent"
    fi
  done
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

  while [ $# -gt 0 ]; do
    case "$1" in
      --scope)
        [ $# -ge 2 ] || fatal 'the --scope flag needs a path'
        scope="$2"; shift 2 ;;
      --artifact-dir)
        [ $# -ge 2 ] || fatal 'the --artifact-dir flag needs a path'
        artifact_dir="$2"; shift 2 ;;
      --) shift; break ;;
      -*) fatal "unknown argument: $1" ;;
      *)  break ;;
    esac
  done

  [ $# -ge 3 ] || fatal 'need <version> <endpoint> <subject>'
  version="$1"; endpoint="$2"; subject="$3"; shift 3
  # The remaining arguments are the folded constituents; the endpoint joins them as the last.
  # Duplicates are dropped, keeping first-seen order.
  constituents="$(printf '%s\n' "$@" "$endpoint" | grep -v '^$' | awk '!seen[$0]++')"

  case "$version" in
    v[0-9]*.[0-9]*.[0-9]*) ;;
    *) fatal "version must look like vX.Y.Z, got: $version" ;;
  esac
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
  artifact="$artifact_dir/audit-$version.txt"

  phase=checking

  printf 'brick %s <- %s\n' "$version" "$(printf '%s' "$constituents" | tr '\n' ' ')"
  printf '  scope:   %s\n' "$scope"
  printf '  audit:   %s (the tree being proven)\n' "$audit_path"
  printf '  helper:  %s (this tool, not the tree)\n' "$lib_path"

  # ---------- preconditions ----------
  [ "$(git -C "$scope" rev-parse --abbrev-ref HEAD)" = "$PUBLISHED_BRANCH" ] \
    || fail_brick "not on branch $PUBLISHED_BRANCH — a brick is appended to the published branch only"
  [ -z "$(git -C "$scope" status --porcelain)" ] \
    || fail_brick 'the working tree is not clean — materialisation needs a clean base to be provable'
  git -C "$scope" rev-parse --verify -q "refs/tags/$version" >/dev/null \
    && fail_brick "tag $version already exists — refusing to move a tag that may already be published"

  assert_applicable

  # ---------- file set: the UNION of every constituent's paths ----------
  files_list="$(mktemp)"
  for c in $constituents; do
    git -C "$scope" show --name-only --format= "$c"
  done | grep -v '^$' | sort -u > "$files_list"
  [ -s "$files_list" ] || fail_brick 'the brick has an empty file set'
  while IFS= read -r f; do
    [ -n "$f" ] || continue
    files[${#files[@]}]="$f"
    printf '  file: %s\n' "$f"
  done < "$files_list"

  # ---------- materialise ----------
  if ! git -C "$scope" checkout "$endpoint" -- "${files[@]}"; then
    fail_brick "could not materialise the brick from $endpoint"
  fi
  materialised=yes

  # ---------- shape A: the brick's files ARE the endpoint's ----------
  # `git diff` has no --pathspec-from-file, so the pathspecs are passed as arguments.
  if ! git -C "$scope" diff --quiet "$endpoint" -- "${files[@]}"; then
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
  if [ -f "$scope/CHANGELOG.md" ]; then
    python3 "$lib_path" "$scope/CHANGELOG.md" "$version" "$date_str" "$subject" \
      || fail_brick 'the changelog entry was refused'
    changelog_written=yes
  else
    printf '  changelog: none at %s — skipping the entry\n' "$scope/CHANGELOG.md"
  fi

  # ---------- commit ----------
  if ! git -C "$scope" add -- "${files[@]}"; then
    fail_brick 'could not stage the brick'
  fi
  [ "$changelog_written" = yes ] && git -C "$scope" add -- CHANGELOG.md
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
  run_audit "$artifact"

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

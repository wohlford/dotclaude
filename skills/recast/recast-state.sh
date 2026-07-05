#!/usr/bin/env bash
set -euo pipefail

# Script: recast-state.sh
# Purpose: Classify a target repo's branch HEAD as EMPTY/UNPUBLISHED/PUBLISHED/DETACHED
# Usage: recast-state <target>
#
# Output (on success, to stdout):
#   STATE=EMPTY|UNPUBLISHED|PUBLISHED|DETACHED
#   branch=...      (best-effort; may be empty)
#   upstream=...    (best-effort; may be empty)
#   ahead=...       (best-effort; may be empty)
#
# Exit codes:
#   0  EMPTY / UNPUBLISHED / PUBLISHED
#   2  bad usage (missing target argument)
#   3  DETACHED HEAD
#
# Inspects the branch HEAD only. An unreachable remote or shallow clone is
# treated fail-safe as PUBLISHED (with a warning on stderr).

usage() { sed -n '5,8p' "$0" >&2; }

emit() {
  # emit <state> <branch> <upstream> <ahead>
  printf 'STATE=%s\n' "$1"
  printf 'branch=%s\n' "$2"
  printf 'upstream=%s\n' "$3"
  printf 'ahead=%s\n' "$4"
}

main() {
  if [ "$#" -lt 1 ] || [ -z "${1:-}" ]; then
    usage
    return 2
  fi
  local t="$1"

  # Non-git dir or unborn HEAD (no commits) => EMPTY. Both `rev-parse` (non-git)
  # and `rev-parse --verify HEAD` (unborn) fail here; neither must abort.
  if ! git -C "$t" rev-parse >/dev/null 2>&1; then
    emit EMPTY "" "" ""
    return 0
  fi
  if ! git -C "$t" rev-parse --verify -q HEAD >/dev/null 2>&1; then
    emit EMPTY "" "" ""
    return 0
  fi

  # Detached HEAD: symbolic-ref -q HEAD fails when HEAD is not on a branch.
  local branch
  if ! branch=$(git -C "$t" symbolic-ref -q --short HEAD 2>/dev/null); then
    emit DETACHED "" "" ""
    return 3
  fi

  # Upstream (normal absence => empty `up`, an UNPUBLISHED signal, not an error).
  local up
  up=$(git -C "$t" rev-parse --abbrev-ref --symbolic-full-name '@{u}' 2>/dev/null || true)

  # Ahead count, best-effort (only meaningful with an upstream).
  local ahead=""
  if [ -n "$up" ]; then
    ahead=$(git -C "$t" rev-list --count "@{u}..HEAD" 2>/dev/null || true)
  fi

  # Tracking upstream resolves => PUBLISHED.
  if [ -n "$up" ]; then
    emit PUBLISHED "$branch" "$up" "$ahead"
    return 0
  fi

  # No upstream: probe every remote (heads AND tags in one ls-remote). PUBLISHED if the exact
  # branch head is present, OR any remote ref's commit exists in the LOCAL object store — an
  # ancestor of HEAD (tag-only publish, differently-named branch), or prior local history that
  # was published then rewritten away (pushed, then amended: no longer an ancestor, but still
  # ours). Unreachable remote => fail-safe PUBLISHED. A foreign SHA (never ours) is absent
  # locally and skipped. May over-approximate in multi-branch repos — the safe direction for a
  # rewrite gate.
  local remotes remote out rc sha ref
  remotes=$(git -C "$t" remote 2>/dev/null || true)
  if [ -n "$remotes" ]; then
    while IFS= read -r remote; do
      [ -n "$remote" ] || continue
      out=$(git -C "$t" ls-remote "$remote" 2>/dev/null) && rc=0 || rc=$?
      if [ "$rc" -ne 0 ]; then
        printf 'warning: remote %s unreachable; assuming PUBLISHED (fail-safe)\n' \
          "$remote" >&2
        emit PUBLISHED "$branch" "$up" "$ahead"
        return 0
      fi
      while IFS=$'\t' read -r sha ref; do
        [ -n "$sha" ] || continue
        if [ "$ref" = "refs/heads/${branch}" ]; then
          emit PUBLISHED "$branch" "$up" "$ahead"
          return 0
        fi
        if git -C "$t" cat-file -e "${sha}^{commit}" 2>/dev/null; then
          emit PUBLISHED "$branch" "$up" "$ahead"
          return 0
        fi
      done <<<"$out"
    done <<<"$remotes"
  fi

  # Local commits, nothing published anywhere.
  emit UNPUBLISHED "$branch" "$up" "$ahead"
  return 0
}

main "$@"

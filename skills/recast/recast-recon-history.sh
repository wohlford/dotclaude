#!/usr/bin/env bash
set -euo pipefail

# Script: recast-recon-history.sh
# Purpose: Grep a git repo's METADATA — commit messages, tag annotations, and
#          author/committer/tagger identity — for AI-generation traces and tool-name
#          mentions, printing ref:kind ONLY (never the offending content).
# Usage: recast-recon-history.sh [--traces-only] <repo> [<range>] [pattern-file]
#   --traces-only  textual surfaces (messages, tag bodies) keep tool-name mentions,
#                  still scrubbing traces; IDENTITY is ALWAYS comprehensive (traces+names).
#   <range>  a git rev-range (a..b) or a single ref meaning "just that commit";
#            default: all commits reachable from HEAD.
#   pattern-file  positional --redact file (blank/# lines ignored); matched case-sensitively.
# Exit codes:
#   0  clean
#   1  at least one hit (ref:kind printed)
#   2  bad usage: flag-shaped positional, bad repo/range, or unreadable pattern-file
usage() { sed -n '4,17p' "$0" >&2; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=recast-markers.sh
. "$SCRIPT_DIR/recast-markers.sh"

# scan <label> <text> <marker-token>... — grep <text> (via a herestring, so grep -q's early exit
# can't SIGPIPE a producer) against the given marker tokens and, if set, the redact file $pf.
# On a hit: print <label>, set caller-local `hit`=1. Returns 2 on a grep ERROR (rc>1); 0 otherwise.
scan() {
  local label="$1" text="$2"
  shift 2
  local m=("$@") rc
  set +e
  grep -qiF "${m[@]}" <<<"$text"
  rc=$?
  set -e
  [ "$rc" -gt 1 ] && return 2
  if [ "$rc" -eq 0 ]; then
    printf '%s\n' "$label"
    hit=1
    return 0
  fi
  if [ -n "$pf" ]; then
    set +e
    grep -qE -f "$pf" <<<"$text"
    rc=$?
    set -e
    [ "$rc" -gt 1 ] && return 2
    [ "$rc" -eq 0 ] && {
      printf '%s\n' "$label"
      hit=1
    }
  fi
  return 0
}

main() {
  local traces_only=0
  if [[ "${1:-}" == "--traces-only" ]]; then
    traces_only=1
    shift
  fi
  local repo="${1:-}" range="${2:-}" pattern_file="${3:-}"

  if [[ "$repo" == --* || "$range" == --* || "$pattern_file" == --* ]]; then
    usage
    return 2
  fi
  if [ -z "$repo" ] || ! git -C "$repo" rev-parse --git-dir >/dev/null 2>&1; then
    usage
    return 2
  fi
  if [[ -n "$pattern_file" && ! -f "$pattern_file" ]]; then
    printf 'recast-recon-history: pattern-file not readable: %s\n' "$pattern_file" >&2
    return 2
  fi

  # Resolve the commit list, fail-closed. Default: reachable from HEAD (empty if unborn → clean).
  # A range containing '..' is a rev-range; any other non-empty token is a single commit.
  local revs rc
  if [ -z "$range" ]; then
    if git -C "$repo" rev-parse --verify -q HEAD >/dev/null 2>&1; then
      set +e
      revs="$(git -C "$repo" rev-list HEAD)"
      rc=$?
      set -e
    else
      revs=""
      rc=0
    fi
  elif [[ "$range" == *..* ]]; then
    set +e
    revs="$(git -C "$repo" rev-list "$range" 2>/dev/null)"
    rc=$?
    set -e
  else
    set +e
    revs="$(git -C "$repo" rev-list -n 1 "$range" 2>/dev/null)"
    rc=$?
    set -e
  fi
  if [ "$rc" -ne 0 ]; then
    printf 'recast-recon-history: bad range/repo: %s %s (rc=%s)\n' "$repo" "$range" "$rc" >&2
    return 2
  fi

  # Marker sets: text honors the flag; identity is always comprehensive.
  local text_markers=("${TRACE_MARKERS[@]}")
  if [ "$traces_only" -eq 0 ]; then
    text_markers+=("${NAME_MARKERS[@]}")
  fi
  local id_markers=("${TRACE_MARKERS[@]}" "${NAME_MARKERS[@]}")

  local pf="" hit=0
  if [[ -n "$pattern_file" ]]; then
    pf="$(mktemp)"
    # shellcheck disable=SC2064
    trap "rm -f '$pf'" EXIT
    grep -vE '^[[:space:]]*(#|$)' -- "$pattern_file" >"$pf" || true
    [ -s "$pf" ] || pf=""
  fi

  # Commit messages (textual → honor the flag) + identity (comprehensive). $sha from rev-list.
  local sha msg idline
  while IFS= read -r sha; do
    [ -n "$sha" ] || continue
    msg="$(git -C "$repo" log -1 --format='%B' "$sha")"
    scan "${sha}:message" "$msg" "${text_markers[@]}" || {
      printf 'recast-recon-history: grep error on %s message\n' "$sha" >&2
      return 2
    }
    idline="$(git -C "$repo" log -1 --format='%an %ae%n%cn %ce' "$sha")"
    scan "${sha}:identity" "$idline" "${id_markers[@]}" || {
      printf 'recast-recon-history: grep error on %s identity\n' "$sha" >&2
      return 2
    }
  done <<<"$revs"

  # Annotated tags whose target commit is in range: body (textual) + tagger (identity). Capture
  # every rc (for-each-ref, rev-list, membership grep) — a git/grep error must not read as clean.
  local tags trc
  set +e
  tags="$(git -C "$repo" for-each-ref --format='%(refname)%09%(objecttype)' refs/tags)"
  trc=$?
  set -e
  [ "$trc" -ne 0 ] && {
    printf 'recast-recon-history: for-each-ref failed (rc=%s)\n' "$trc" >&2
    return 2
  }
  local tref ttype ttarget body tagger mrc lrc
  while IFS=$'\t' read -r tref ttype; do
    [ -n "$tref" ] || continue
    set +e
    ttarget="$(git -C "$repo" rev-list -n 1 "$tref")"
    lrc=$?
    printf '%s\n' "$revs" | grep -qxF "$ttarget"
    mrc=$?
    set -e
    [ "$lrc" -ne 0 ] && {
      printf 'recast-recon-history: rev-list %s failed (rc=%s)\n' "$tref" "$lrc" >&2
      return 2
    }
    [ "$mrc" -gt 1 ] && {
      printf 'recast-recon-history: grep error (membership %s)\n' "$tref" >&2
      return 2
    }
    [ "$mrc" -eq 0 ] || continue # target not in range → skip this tag
    # The ref NAME is a tell for BOTH lightweight and annotated tags — scan it comprehensively
    # (a claude-*/.claude tag name leaks regardless of --traces-only).
    scan "${tref}:refname" "$tref" "${id_markers[@]}" || {
      printf 'recast-recon-history: grep error on %s refname\n' "$tref" >&2
      return 2
    }
    [ "$ttype" = tag ] || continue # body/tagger exist only for annotated tags
    body="$(git -C "$repo" for-each-ref --format='%(contents)' "$tref")"
    scan "${tref}:message" "$body" "${text_markers[@]}" || {
      printf 'recast-recon-history: grep error on %s body\n' "$tref" >&2
      return 2
    }
    tagger="$(git -C "$repo" for-each-ref --format='%(taggername) %(taggeremail)' "$tref")"
    scan "${tref}:tagger" "$tagger" "${id_markers[@]}" || {
      printf 'recast-recon-history: grep error on %s tagger\n' "$tref" >&2
      return 2
    }
  done <<<"$tags"

  # Branch ref NAMES are tells too (a branch named claude-*/anthropic-* leaks). Scan every local
  # branch whose tip is in range, comprehensively.
  local branches brc bref btip
  set +e
  branches="$(git -C "$repo" for-each-ref --format='%(refname)' refs/heads)"
  brc=$?
  set -e
  [ "$brc" -ne 0 ] && {
    printf 'recast-recon-history: for-each-ref refs/heads failed (rc=%s)\n' "$brc" >&2
    return 2
  }
  while IFS= read -r bref; do
    [ -n "$bref" ] || continue
    set +e
    btip="$(git -C "$repo" rev-parse --verify -q "$bref" 2>/dev/null)"
    lrc=$?
    printf '%s\n' "$revs" | grep -qxF "$btip"
    mrc=$?
    set -e
    [ "$lrc" -ne 0 ] && continue # unresolvable tip → skip (best-effort)
    [ "$mrc" -gt 1 ] && {
      printf 'recast-recon-history: grep error (membership %s)\n' "$bref" >&2
      return 2
    }
    [ "$mrc" -eq 0 ] || continue # tip not in range → skip this branch
    scan "${bref}:refname" "$bref" "${id_markers[@]}" || {
      printf 'recast-recon-history: grep error on %s refname\n' "$bref" >&2
      return 2
    }
  done <<<"$branches"

  [ "$hit" -ne 0 ] && return 1
  return 0
}

main "$@"

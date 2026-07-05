#!/usr/bin/env bash
set -euo pipefail

# Script: recast-contract.sh
# Purpose: Per-commit verifier — archive each selected commit and prove it with a verify command
# Usage: recast-contract.sh (--tags | --range <rev-range>) <repo> <verify-cmd>
#   --tags             verify every tag (sort -V) — the recast brick shape
#   --range <range>    verify every commit in the rev-range (--reverse); a single ref
#                      (e.g. HEAD) sweeps the whole history including the root
#   <verify-cmd>       one shell string, run via `bash -c` with cwd = the archived tree
#
# Exit codes:
#   0  every selected commit passed
#   1  at least one commit FAILed its verify (failing refs named on stdout)
#   2  bad usage, git/archive error, or NOTHING SELECTED (a gate must not pass vacuously)
#
# The verify runs against the COMMITTED tree (git archive), never the working tree.
# NOTE: ref names are sanitized (/ -> _) for temp-dir names; distinct tags that collide
# after sanitization (feat/x vs feat_x) would share a dir — a non-issue for semver tags.

usage() { sed -n '4,16p' "$0" >&2; }

main() {
  local mode="" range=""
  case "${1:-}" in
    --tags) mode=tags; shift ;;
    --range)
      mode=range
      range="${2:-}"
      shift 2 || { usage; return 2; }
      ;;
    *)
      usage
      return 2
      ;;
  esac
  local repo="${1:-}" verify="${2:-}"
  if [[ "$repo" == --* || "$verify" == --* || -z "$repo" || -z "$verify" ]]; then
    usage
    return 2
  fi
  if ! git -C "$repo" rev-parse --git-dir >/dev/null 2>&1; then
    usage
    return 2
  fi

  # Select refs, fail-closed: a selection error must never read as "nothing to verify".
  local refs rc
  if [[ "$mode" == tags ]]; then
    set +e
    refs="$(git -C "$repo" tag | sort -V)"
    rc=$?
    set -e
  else
    if [[ -z "$range" || "$range" == --* ]]; then
      usage
      return 2
    fi
    set +e
    refs="$(git -C "$repo" rev-list --reverse "$range" 2>/dev/null)"
    rc=$?
    set -e
  fi
  if [[ "$rc" -ne 0 ]]; then
    printf 'recast-contract: ref selection failed (rc=%s)\n' "$rc" >&2
    return 2
  fi
  if [[ -z "$refs" ]]; then
    printf 'recast-contract: NOTHING SELECTED — a verification gate must not pass vacuously\n' >&2
    return 2
  fi

  local work
  work="$(mktemp -d)"
  # shellcheck disable=SC2064
  trap "rm -rf '$work'" EXIT

  local ref dir fail=0 out
  while IFS= read -r ref; do
    [[ -n "$ref" ]] || continue
    dir="$work/${ref//\//_}"
    mkdir -p "$dir"
    # pipefail makes rc reflect a git-archive failure even when tar exits 0.
    set +e
    git -C "$repo" archive "$ref" | tar -x -C "$dir"
    rc=$?
    set -e
    if [[ "$rc" -ne 0 ]]; then
      printf 'recast-contract: archive failed for %s (rc=%s)\n' "$ref" "$rc" >&2
      return 2
    fi
    set +e
    out="$(cd "$dir" && bash -c "$verify" 2>&1)"
    rc=$?
    set -e
    if [[ "$rc" -eq 0 ]]; then
      printf '%s: PASS\n' "$ref"
    else
      printf '%s: FAIL\n' "$ref"
      printf '%s\n' "$out" | tail -5 >&2
      fail=$((fail + 1))
    fi
  done <<<"$refs"

  if [[ "$fail" -ne 0 ]]; then
    printf 'FAIL: %d commit(s) violate the contract\n' "$fail" >&2
    return 1
  fi
  printf 'PASS: every selected commit satisfies the contract\n'
  return 0
}

main "$@"

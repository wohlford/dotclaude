#!/usr/bin/env bash
set -euo pipefail

# Script: recast-verify.sh
# Purpose: STRUCTURE audit — compare a target's tracked file set against a source
#          tree at a ref, minus deviation globs (NOT a content/byte comparison)
# Usage: recast-verify <target> <source> <ref> [deviation-file]
#
# Compares `git -C <target> ls-files` against
# `git -C <source> ls-tree -r --name-only <ref>` and reports:
#   unexpected presents: paths in target but not in source (and not deviation-covered)
#   unexpected absents:  paths in source but not in target (and not deviation-covered)
#
# deviation-file: one glob per line; blank lines and #-comment lines ignored.
#   A path matching any glob is exempt from BOTH reports. Globs are matched with
#   bash `[[ == glob ]]` per path (e.g. `docs/*`, `*.local`, `vendor/**` literal).
#
# Exit codes:
#   0  clean (no unexpected presents/absents)
#   1  mismatch (itemized report printed)
#   2  bad args (missing target/source/ref, or not git repos)
#
# Only the SET of paths matters — file contents are never inspected.

usage() { sed -n '5,9p' "$0" >&2; }

dev_globs=()
tmp_t=""
tmp_s=""

# shellcheck disable=SC2329  # cleanup is invoked indirectly via the EXIT trap
cleanup() { rm -f "$tmp_t" "$tmp_s"; }
trap cleanup EXIT

# covered <path> -> 0 if any deviation glob matches, else 1.
covered() {
  local p="$1" g
  for g in "${dev_globs[@]}"; do
    # shellcheck disable=SC2053
    [[ "$p" == $g ]] && return 0
  done
  return 1
}

main() {
  if [ "$#" -lt 3 ] || [ -z "${1:-}" ] || [ -z "${2:-}" ] || [ -z "${3:-}" ]; then
    usage
    return 2
  fi
  local target="$1" source="$2" ref="$3" devfile="${4:-}"

  if ! git -C "$target" rev-parse >/dev/null 2>&1; then
    printf 'error: target is not a git repo: %s\n' "$target" >&2
    return 2
  fi
  if ! git -C "$source" rev-parse >/dev/null 2>&1; then
    printf 'error: source is not a git repo: %s\n' "$source" >&2
    return 2
  fi
  if ! git -C "$source" rev-parse --verify -q "$ref^{tree}" >/dev/null 2>&1; then
    printf 'error: ref does not resolve to a tree in source: %s\n' "$ref" >&2
    return 2
  fi

  # Load deviation globs (one per line; skip blanks and #-comments).
  if [ -n "$devfile" ]; then
    if [ ! -f "$devfile" ]; then
      printf 'error: deviation-file not found: %s\n' "$devfile" >&2
      return 2
    fi
    local line
    while IFS= read -r line || [ -n "$line" ]; do
      [[ "$line" =~ ^[[:space:]]*$ ]] && continue
      [[ "$line" =~ ^[[:space:]]*# ]] && continue
      dev_globs+=("$line")
    done <"$devfile"
  fi

  tmp_t=$(mktemp)
  tmp_s=$(mktemp)

  git -C "$target" ls-files | LC_ALL=C sort >"$tmp_t"
  git -C "$source" ls-tree -r --name-only "$ref" | LC_ALL=C sort >"$tmp_s"

  # presents: in target only (comm -23). absents: in source only (comm -13).
  # comm must collate identically to how the files were sorted (LC_ALL=C above),
  # else it warns "not in sorted order" and its merge can miss/duplicate lines —
  # a false pass/fail. Guard against set -e (a pipe consumer exiting early is fine).
  local presents absents
  presents=$(LC_ALL=C comm -23 "$tmp_t" "$tmp_s" || true)
  absents=$(LC_ALL=C comm -13 "$tmp_t" "$tmp_s" || true)

  local unexpected=0 p
  local -a up=() ua=()
  if [ -n "$presents" ]; then
    while IFS= read -r p; do
      [ -n "$p" ] || continue
      covered "$p" && continue
      up+=("$p")
    done <<EOF
$presents
EOF
  fi
  if [ -n "$absents" ]; then
    while IFS= read -r p; do
      [ -n "$p" ] || continue
      covered "$p" && continue
      ua+=("$p")
    done <<EOF
$absents
EOF
  fi

  if [ "${#up[@]}" -gt 0 ]; then
    unexpected=1
    printf 'unexpected presents (in target, not in source):\n'
    for p in "${up[@]}"; do printf '  + %s\n' "$p"; done
  fi
  if [ "${#ua[@]}" -gt 0 ]; then
    unexpected=1
    printf 'unexpected absents (in source, not in target):\n'
    for p in "${ua[@]}"; do printf '  - %s\n' "$p"; done
  fi

  if [ "$unexpected" -ne 0 ]; then
    return 1
  fi
  printf 'STRUCTURE OK\n'
  return 0
}

main "$@"

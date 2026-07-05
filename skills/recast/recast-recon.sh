#!/usr/bin/env bash
set -euo pipefail

# Script: recast-recon.sh
# Purpose: Grep a tree's contents AND file/dir names for AI-generation traces and tool-name
#          mentions (comprehensive by default; --traces-only narrows to traces), plus optional
#          caller patterns — printing path:line / path:name ONLY, never the content
# Usage: recast-recon.sh [--traces-only] <tree> [pattern-file]
#   --traces-only  sweep only generation traces, keeping tool-name mentions
#                  (Claude/Anthropic/GEMINI); the default sweeps traces + names
#   NOTE: names cover Claude/Anthropic/GEMINI only — other assistants (Copilot,
#         GPT, Cursor, Llama) and non-Anthropic model ids need a --redact file.
#
# Exit codes:
#   0  clean (no markers/patterns found)
#   1  at least one hit (markers and/or custom patterns)
#   2  bad usage: missing/invalid <tree>, flag-shaped positional, or unreadable pattern-file

usage() { sed -n '4,17p' "$0" >&2; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=recast-markers.sh
. "$SCRIPT_DIR/recast-markers.sh"

main() {
  local traces_only=0
  if [[ "${1:-}" == "--traces-only" ]]; then
    traces_only=1
    shift
  fi

  local tree="${1:-}"
  local pattern_file="${2:-}"

  # Fail loud on a flag-shaped token in a positional slot — never silently swallow it (which would
  # skip the secret/custom-pattern sweep with no message).
  if [[ "$tree" == --* || "$pattern_file" == --* ]]; then
    usage
    return 2
  fi
  if [ -z "$tree" ] || [ ! -d "$tree" ]; then
    usage
    return 2
  fi

  # Default sweeps traces + names (comprehensive); --traces-only narrows to traces (keeps names).
  local markers=("${TRACE_MARKERS[@]}")
  if [[ "$traces_only" -eq 0 ]]; then
    markers+=("${NAME_MARKERS[@]}")
  fi

  local hit=0

  # Prepare the optional custom-pattern file ONCE, up front, so EVERY sweep — contents, names, and
  # symlink targets — can apply it, not just the raw-content sweep. (An absent/blank file leaves
  # $pf empty and the custom sweeps are skipped.) A missing pattern-file arg is a hard error.
  local pf=""
  if [[ -n "$pattern_file" ]]; then
    if [[ ! -f "$pattern_file" ]]; then
      printf 'recast-recon: pattern-file not readable: %s\n' "$pattern_file" >&2
      return 2
    fi
    pf="$(mktemp)"
    # shellcheck disable=SC2064
    trap "rm -f '$pf'" EXIT
    # Drop blank + comment lines; if nothing remains, disable the custom sweeps (never grep -f an
    # empty file — that matches every line).
    grep -vE '^[[:space:]]*(#|$)' -- "$pattern_file" > "$pf" || true
    [[ -s "$pf" ]] || pf=""
  fi

  # Built-in marker sweep. grep exits 1 on no-match; capture rc so set -e does not abort.
  local builtin_out rc
  set +e
  builtin_out="$(grep -rniF "${markers[@]}" -- "$tree" | cut -d: -f1,2)"
  rc=$?
  set -e
  # grep exits 0 (match), 1 (no match), >=2 (error). A scrub gate must NOT treat a grep error as
  # "clean" — surface it and fail.
  if [ "$rc" -gt 1 ]; then
    printf 'recast-recon: grep error scanning %s (rc=%s)\n' "$tree" "$rc" >&2
    return 2
  fi
  if [ "$rc" -eq 0 ] && [ -n "$builtin_out" ]; then
    printf '%s\n' "$builtin_out"
    hit=1
  fi

  # Name sweep: the tree's own file/dir NAMES are a tell too (claude-config.md, .claude/). Same
  # marker selection as contents (honors --traces-only). Match paths RELATIVE to the tree root so
  # a marker in the tree's PARENT path can't false-positive every file. Fail closed on any error.
  local names name_rc gnames
  set +e
  names="$(cd "$tree" && find . -print)"
  name_rc=$?
  set -e
  if [ "$name_rc" -ne 0 ]; then
    printf 'recast-recon: error enumerating names in %s (rc=%s)\n' "$tree" "$name_rc" >&2
    return 2
  fi
  set +e
  gnames="$(printf '%s\n' "$names" | grep -iF "${markers[@]}")"
  rc=$?
  set -e
  if [ "$rc" -gt 1 ]; then
    printf 'recast-recon: grep error scanning names in %s (rc=%s)\n' "$tree" "$rc" >&2
    return 2
  fi
  if [ "$rc" -eq 0 ] && [ -n "$gnames" ]; then
    printf '%s\n' "$gnames" | sed 's/$/:name/'
    hit=1
  fi
  # Custom patterns sweep names too (a --redact marker in a filename is as much a tell as in text).
  if [[ -n "$pf" ]]; then
    local cnames crc
    set +e
    cnames="$(printf '%s\n' "$names" | grep -E -f "$pf")"
    crc=$?
    set -e
    if [ "$crc" -gt 1 ]; then
      printf 'recast-recon: grep error scanning custom names in %s (rc=%s)\n' "$tree" "$crc" >&2
      return 2
    fi
    if [ "$crc" -eq 0 ] && [ -n "$cnames" ]; then
      printf '%s\n' "$cnames" | sed 's/$/:name/'
      hit=1
    fi
  fi

  # Wide-encoding sweep: the byte-wise grep above cannot read UTF-16/UTF-32 (NUL-interleaved), so a
  # marker in a wide-encoded file would slip the scrub OPEN. For each file carrying NUL bytes, try
  # decoding it and re-grep; report path:wide (line numbers are unreliable post-transcode). An iconv
  # failure means a genuine binary — nothing textual to leak. Built-in markers only (custom regex
  # patterns are handled against the raw tree below).
  local wide_hit=0 wf dec enc
  while IFS= read -r wf; do
    [ -n "$wf" ] || continue
    [ "$(LC_ALL=C tr -dc '\000' <"$tree/$wf" | wc -c | tr -d ' ')" -gt 0 ] || continue
    for enc in UTF-16LE UTF-16BE UTF-32LE UTF-32BE; do
      # Strip NULs from the decoded text: a real wide-encoded file yields none, but decoding a
      # genuine binary as UTF-16 can, and capturing NULs makes bash warn. Marker text has no NULs.
      dec="$(iconv -f "$enc" -t UTF-8 -- "$tree/$wf" 2>/dev/null | tr -d '\000')" || continue
      [ -n "$dec" ] || continue
      if printf '%s' "$dec" | grep -qiF "${markers[@]}"; then
        printf '%s:wide\n' "${wf#./}"
        wide_hit=1
        break
      fi
    done
  done < <(cd "$tree" && find . -type f)
  [ "$wide_hit" -ne 0 ] && hit=1

  # Symlink-target sweep: grep -r does not read a symlink's target string, yet that path is committed
  # blob content and can itself carry a marker (link -> …/.claude/…). Scan the targets with the
  # built-in markers AND any custom patterns. Fail closed.
  local link_targets link_out
  link_targets="$(cd "$tree" && find . -type l -exec readlink {} \; 2>/dev/null)"
  set +e
  link_out="$(printf '%s\n' "$link_targets" | grep -iF "${markers[@]}")"
  rc=$?
  set -e
  if [ "$rc" -gt 1 ]; then
    printf 'recast-recon: error scanning symlink targets in %s (rc=%s)\n' "$tree" "$rc" >&2
    return 2
  fi
  if [ "$rc" -eq 0 ] && [ -n "$link_out" ]; then
    printf '%s\n' "$link_out" | sed 's/$/:symlink-target/'
    hit=1
  fi
  if [[ -n "$pf" ]]; then
    local clink crc2
    set +e
    clink="$(printf '%s\n' "$link_targets" | grep -E -f "$pf")"
    crc2=$?
    set -e
    if [ "$crc2" -gt 1 ]; then
      printf 'recast-recon: grep error scanning custom symlink targets in %s (rc=%s)\n' "$tree" "$crc2" >&2
      return 2
    fi
    if [ "$crc2" -eq 0 ] && [ -n "$clink" ]; then
      printf '%s\n' "$clink" | sed 's/$/:symlink-target/'
      hit=1
    fi
  fi

  # Optional custom pattern sweep of raw CONTENTS (caller --redact / extended markers). The same
  # patterns were already applied to names and symlink targets above. Matched case-SENSITIVELY as
  # authored — use (?i) or [Cc]laude for case-insensitivity. Other assistants (Copilot, GPT, Cursor,
  # Llama) and email shapes (noreply@…) belong here.
  if [[ -n "$pf" ]]; then
    local custom_out
    set +e
    custom_out="$(grep -rnE -f "$pf" -- "$tree" | cut -d: -f1,2)"
    rc=$?
    set -e
    if [ "$rc" -gt 1 ]; then
      printf 'recast-recon: grep error scanning %s (rc=%s)\n' "$tree" "$rc" >&2
      return 2
    fi
    if [ "$rc" -eq 0 ] && [ -n "$custom_out" ]; then
      printf '%s\n' "$custom_out"
      hit=1
    fi
  fi

  if [ "$hit" -ne 0 ]; then
    return 1
  fi
  return 0
}

main "$@"

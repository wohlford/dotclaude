#!/usr/bin/env bash
set -uo pipefail

# Script: propagate-postcheck.sh
# Purpose: Verify /propagate's LOCAL promote landed correctly, choosing the postcondition branch
#          itself instead of leaving that to the operator
# Usage: propagate-postcheck.sh --scope <live> --before-sha <sha256> [--ref <ref>] [--before-head <commit>]
#
# Scope: this script verifies; it changes nothing. It runs AFTER the fast-forward, so every
# recovery it might suggest belongs to the operator and to the skill, not here.
#
# ## Why this exists
#
# `/propagate`'s postcondition BRANCHES, and both arms were hand-run on every promote. The strict
# arm is four separate commands with no single verdict line, which is exactly the shape a human
# performs correctly until the one time they do not. `publish-preflight.sh` is the direct
# precedent, one layer up.
#
# ## The branch, and why the operator must not be the one choosing it
#
# The deciding question is whether `settings.json` was in the incoming range. Its two answers
# call for different instruments:
#
#   * NOT in range — nothing should have touched the runtime file, so it must be BYTE-IDENTICAL
#     (sha256 before vs after), with no stash left parked and `skip-worktree` still set.
#   * IN range — the skill parks the runtime file, fast-forwards, restores it, then HAND-ADDS the
#     hook entries the incoming commit registered. The restored file therefore predates that
#     commit, so byte-identity is the WRONG instrument here: it reads CLEAN precisely when the
#     hand-add was skipped and a registration was dropped.
#
# A wrong branch choice is silent in both directions, which is the defect this closes.
#
# ## The hooks check runs on BOTH branches, on purpose
#
# `hooks-registered` is not gated on the branch. If it ran only on the in-range arm, a
# mis-determined range would once again hide a dropped registration — the very failure the branch
# decision exists to prevent. Running it unconditionally means the range determination now affects
# only which DIAGNOSIS you get, never whether a dead gate can ship unnoticed. It is safe to run
# unconditionally because it is one-directional: it reports runtime-only entries without failing
# on them, so a machine-local hook never false-alarms.
#
# ## The BEFORE sha is an argument because the merge destroys it
#
# `settings.json` is `skip-worktree` in production, so its runtime content differs from the index
# by design and cannot be recovered from git afterwards. Capture it BEFORE the merge. That
# ordering is the part a human gets wrong under repetition, so the script refuses to run without
# it rather than computing something that would merely look like it.
#
# The pre-merge HEAD is different: `git merge` records it in `ORIG_HEAD`, so it IS recoverable,
# and `--before-head` exists only to override that default. A supplied or recorded value is
# accepted only if it is an ancestor of HEAD; anything else is refused rather than believed.
#
# ## An undeterminable range fails CLOSED
#
# With no usable pre-merge HEAD the branch is unknown, and the script then REQUIRES byte-identity.
# Wrong in that direction is loud and recoverable (a false FAIL on a legitimate hand-add); wrong
# in the other direction is silent (a clobbered runtime preference nobody sees).
#
# Exit codes:
#   0 — every assertion held; the promote is verified
#   1 — at least one assertion FAILED
#   2 — usage error, or the check could not be made at all (no scope, no before-sha, not a git
#       repo, no runtime settings.json)
#   129/130/143 — died on a trapped signal (HUP/INT/TERM)
#
# Terminal verdict line: every run this script exits from itself prints, as its LAST line of
# stdout, `RESULT: <STATUS> rc=<n> checks=<pass>/<fail>/<skip>` where STATUS is PASS | FAIL |
# ERROR (no checks ran) | INCOMPLETE (died partway). Clean is EXACTLY `RESULT: PASS` — an
# allowlist, so any value not anticipated here reads as not-clean. The line CANNOT be emitted on
# SIGKILL, so its ABSENCE never means clean: it means the run did not complete. PASS/FAIL are
# emitted positionally by main(), never decided inside the exit trap, because `$?` in an EXIT
# trap reads 0 for an untrapped fatal signal — deciding there would print PASS for a killed run.
#
# bash-3.2/BSD-safe: no arrays, no mapfile, no GNU-only flags.
#
# NOTE: no `-e` — one failing assertion must not abort the run before its verdict line.

script_name="$(basename "$0")"
script_dir="$(cd "$(dirname "$0")" && pwd)"

readonly SETTINGS=settings.json

pass_count=0
fail_count=0
skip_count=0

# Both MUST stay global — a `local` copy would be invisible to the exit trap. `phase` goes
# init -> checking and exists only to tell a usage error (nothing ran) from a death partway
# through. `reported` records that a verdict line was already printed, so the trap stays silent
# on the normal path rather than emitting a second one.
phase=init
reported=no

usage() {
  printf 'Usage: %s --scope <live> --before-sha <sha256> [--ref <ref>] [--before-head <commit>]\n' \
    "$script_name" >&2
}

# A fatal precondition prints its reason to STDOUT, not just stderr: stdout is the artifact an
# operator (or a transcript) actually reads back, and an ERROR whose cause lives only on a
# discarded stderr is indistinguishable from an unexplained one.
fatal() { printf '%s: %s\n' "$script_name" "$1"; usage; exit 2; }

verdict_pass() { printf 'PASS %s\n' "$1"; pass_count=$((pass_count + 1)); }
verdict_fail() { printf 'FAIL %s — %s\n' "$1" "$2"; fail_count=$((fail_count + 1)); }
verdict_skip() { printf 'SKIP %s — %s\n' "$1" "$2"; skip_count=$((skip_count + 1)); }

result_line() {
  printf 'RESULT: %s rc=%d checks=%d/%d/%d\n' \
    "$1" "$2" "$pass_count" "$fail_count" "$skip_count"
  reported=yes
}

# Covers only the paths main() never reaches the end of, so it can emit ONLY ERROR or
# INCOMPLETE — never a clean verdict.
on_exit() { # exit-status
  [[ "$reported" == yes ]] && return
  if [[ "$phase" == init && "$1" -eq 2 ]]; then
    result_line ERROR "$1"
  else
    result_line INCOMPLETE "$1"
  fi
}

# file_sha256 PATH -> lowercase hex digest. macOS always ships `shasum`; `sha256sum` is the
# coreutils name and is preferred when present because it is cheaper.
file_sha256() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | cut -d' ' -f1
  else
    shasum -a 256 "$1" | cut -d' ' -f1
  fi
}

lower() { printf '%s' "$1" | tr '[:upper:]' '[:lower:]'; }

main() {
  local scope="" before_sha="" ref=FETCH_HEAD before_head="" head_src=ORIG_HEAD
  local head_sha ref_sha base changed in_range helper out rc identity_note got

  while [[ $# -gt 0 ]]; do
    case "$1" in
      --scope)
        [[ $# -ge 2 ]] || fatal 'the --scope flag needs a path'
        scope="$2"; shift 2 ;;
      --before-sha)
        [[ $# -ge 2 ]] || fatal 'the --before-sha flag needs a sha256'
        before_sha="$2"; shift 2 ;;
      --ref)
        [[ $# -ge 2 ]] || fatal 'the --ref flag needs a ref'
        ref="$2"; shift 2 ;;
      --before-head)
        [[ $# -ge 2 ]] || fatal 'the --before-head flag needs a commit'
        before_head="$2"; head_src=explicit; shift 2 ;;
      *) fatal "unknown argument: $1" ;;
    esac
  done

  [[ -n "$scope" ]] || fatal 'no --scope given — name the production repo the promote targeted'
  [[ -n "$before_sha" ]] \
    || fatal "no --before-sha given — capture the runtime $SETTINGS digest BEFORE the merge; it cannot be recovered afterwards"

  git -C "$scope" rev-parse --show-toplevel >/dev/null 2>&1 \
    || fatal "not a git repository: $scope"
  scope="$(git -C "$scope" rev-parse --show-toplevel)"

  [[ -f "$scope/$SETTINGS" ]] \
    || fatal "no $SETTINGS in $scope — the runtime file this check is about does not exist, so no verdict can be reached"

  helper="$script_dir/settings-hooks-check.py"

  phase=checking

  # Echo the resolved inputs back. A verdict about a configuration nobody confirmed is a true
  # answer to a question nobody asked — and the helper is resolved relative to THIS script, so
  # naming the copy is what keeps a stale-tool verdict from being anonymous.
  printf 'scope:       %s\n' "$scope"
  printf 'ref:         %s\n' "$ref"
  printf 'hooks check: %s\n' "$helper"

  # ---------- merge-applied ----------
  head_sha="$(git -C "$scope" rev-parse HEAD 2>/dev/null)" || head_sha=""
  ref_sha="$(git -C "$scope" rev-parse --verify -q "$ref^{commit}" 2>/dev/null)" || ref_sha=""
  if [[ -z "$ref_sha" ]]; then
    verdict_fail merge-applied "cannot resolve $ref in $scope — nothing confirms what was promoted"
  elif [[ "$head_sha" == "$ref_sha" ]]; then
    verdict_pass merge-applied
  else
    verdict_fail merge-applied \
      "HEAD is ${head_sha:0:12} but $ref is ${ref_sha:0:12} — the fast-forward did not land"
  fi

  # ---------- range ----------
  # Determines the branch. `unknown` is a real value here, not an error to paper over: it makes
  # settings-identical required below, which is the fail-closed direction.
  in_range=unknown
  if [[ -z "$before_head" ]]; then
    before_head="$(git -C "$scope" rev-parse --verify -q ORIG_HEAD 2>/dev/null)" || before_head=""
  fi
  base="$(git -C "$scope" rev-parse --verify -q "${before_head:-HEAD_UNSET}^{commit}" 2>/dev/null)" || base=""

  if [[ -z "$base" ]]; then
    verdict_fail range \
      "no usable pre-merge HEAD ($head_src unset or unresolvable) — pass --before-head; treating the branch as UNDETERMINED, so byte-identity is required below"
  elif ! git -C "$scope" merge-base --is-ancestor "$base" "$head_sha" 2>/dev/null; then
    verdict_fail range \
      "the given pre-merge HEAD ${base:0:12} is not an ancestor of HEAD — refusing to believe it; treating the branch as UNDETERMINED"
  else
    changed="$(git -C "$scope" diff --name-only "$base" "${ref_sha:-$head_sha}" 2>/dev/null)"
    if printf '%s\n' "$changed" | grep -qx "$SETTINGS"; then
      in_range=yes
      verdict_pass range
      printf '  %s IS in the incoming range (%s..%s, from %s)\n' \
        "$SETTINGS" "${base:0:12}" "${ref_sha:0:12}" "$head_src"
    else
      in_range=no
      verdict_pass range
      printf '  %s is NOT in the incoming range (%s..%s, from %s)\n' \
        "$SETTINGS" "${base:0:12}" "${ref_sha:0:12}" "$head_src"
    fi
  fi

  # ---------- settings-identical ----------
  got="$(file_sha256 "$scope/$SETTINGS")"
  if [[ "$(lower "$got")" == "$(lower "$before_sha")" ]]; then
    identity_note="digest unchanged (${got:0:12})"
  else
    identity_note="digest changed: before ${before_sha:0:12}, after ${got:0:12}"
  fi

  if [[ "$in_range" == yes ]]; then
    # Not an assertion on this branch: the skill's hand-add step legitimately rewrites the file.
    # Reported anyway, because "identical" here is itself the signature of a SKIPPED hand-add —
    # which hooks-registered below is the instrument for.
    verdict_skip settings-identical \
      "not asserted — $SETTINGS was in the incoming range, so the hand-added registrations change it by design ($identity_note)"
  elif [[ "$(lower "$got")" == "$(lower "$before_sha")" ]]; then
    verdict_pass settings-identical
  else
    verdict_fail settings-identical \
      "the runtime $SETTINGS changed across a promote that did not touch it — $identity_note"
  fi

  # ---------- stash-empty ----------
  out="$(git -C "$scope" stash list 2>/dev/null)"
  if [[ -z "$out" ]]; then
    verdict_pass stash-empty
  else
    verdict_fail stash-empty \
      "$(printf '%s\n' "$out" | grep -c '') stash entr(y/ies) left parked — the promote did not restore what it stashed"
    printf '%s\n' "$out" | sed 's/^/  /'
  fi

  # ---------- skip-worktree ----------
  # `ls-files -v` prefixes a skip-worktree path with a lowercase-to-uppercase tag; `S` is the one
  # that matters. An empty result means the path is not tracked at all, which is also a failure.
  out="$(git -C "$scope" ls-files -v "$SETTINGS" 2>/dev/null)"
  case "$out" in
    S*) verdict_pass skip-worktree ;;
    '') verdict_fail skip-worktree "$SETTINGS is not tracked in $scope" ;;
    *)  verdict_fail skip-worktree \
          "skip-worktree is no longer set (ls-files -v says '${out%% *}') — the runtime file is now exposed to every checkout" ;;
  esac

  # ---------- hooks-registered ----------
  # Deliberately unconditional; see the header. Statuses are an allowlist: only `RESULT: PASS`
  # from the helper clears it, so ERROR (no verdict reached) never reads as clean.
  if [[ ! -f "$helper" ]]; then
    verdict_fail hooks-registered \
      "$helper is missing — the registration check could not run, which is not the same as passing"
  else
    out="$(python3 "$helper" --scope "$scope" --ref "${ref_sha:-$ref}" 2>&1)"; rc=$?
    case "$(printf '%s\n' "$out" | tail -1)" in
      'RESULT: PASS'*) verdict_pass hooks-registered ;;
      *)
        verdict_fail hooks-registered \
          "$(basename "$helper") did not return PASS (rc=$rc)"
        printf '%s\n' "$out" | sed 's/^/  /'
        ;;
    esac
  fi

  if [[ "$fail_count" -eq 0 ]]; then
    result_line PASS 0
    return 0
  fi
  result_line FAIL 1
  return 1
}

# Guarded (not a bare `main "$@"`) so the test suite can source this file to unit-test helpers
# without running a sweep. The traps live INSIDE the guard for the same reason: a top-level EXIT
# trap would install itself into any shell that sources this file.
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  # Single quotes: `$?` must expand when the trap RUNS, not when it is defined.
  trap 'on_exit "$?"' EXIT
  trap 'exit 143' TERM
  trap 'exit 130' INT
  trap 'exit 129' HUP
  main "$@"
fi

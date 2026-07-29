#!/usr/bin/env bash
set -uo pipefail

# Script: publish-preflight.sh
# Purpose: Verify /propagate's publish start-invariant before any brick is applied, tagged, or pushed
# Usage: publish-preflight.sh [--scope <path>]
#
# Scope: this script verifies. Steps 2-7 of the publish path (re-derivation, cherry-pick, tag,
# push, watermark advance) stay foreground and human-checkpointed — scripting them would raise
# blast radius and remove the per-brick checkpoint. Where a failure has a prescribed recovery,
# this prints the commands for the operator to run; it never runs them.
#
# It performs exactly ONE state-changing operation, and only because the invariant is unprovable
# without it: the mandated `git fetch`, which updates remote-tracking refs. It never writes a
# branch, tag, watermark, working-tree file, or anything on the remote. Do not add a second.
#
# Exit codes:
#   0 — every assertion held; the publish path may begin
#   1 — at least one assertion FAILED; do not publish
#   2 — usage error (bad/missing --scope, unknown flag, not a git repo, not an adopted repo)
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
# THE FETCH IS AN ABORT POINT, NOT A CHECK. This is the defect the script exists to make
# unrepeatable: on 2026-07-27 a `git fetch origin main` failed (rc=128, transient SSH refusal)
# and the very next hand-written comparison read the STALE cached origin/main and printed
# START-INVARIANT PASS. Local main equalled the cached ref, so every downstream assertion was
# true — about a remote state nobody had actually observed. When the fetch fails, the remote-
# dependent assertions below are SKIPPED and named as such; they are never allowed to pass.
#
# Assertion provenance: `main-sync`, `watermark-present`, `watermark-integrity` come from the
# publish path's step 1; `watermark-ancestor` from its step 2 opener (watermark rule 2);
# `unpublished-work` from step 2's premise that there is a watermark..dev range to re-derive;
# `auth` is the folded-in pre-flight credential check. `tree-clean` is derived from the path's
# mechanics rather than quoted from it — step 3 checks out main and applies bricks, which a
# dirty tree blocks.
#
# `auth` DIAGNOSES, it does not gate — and it declines to state a conclusion it cannot support.
# Measured 2026-07-28: run with no TTY, gpg-agent cannot deliver the card's PIN prompt and refuses
# to sign ("agent refused operation", rc=255) — byte-identical to a genuinely rejected credential.
# The first cut reported that as FAIL, which sent the operator to re-seat a card that was present,
# unlocked, and being served by the agent the whole time. So a failure is now only reported as FAIL
# when a prompt could actually have reached someone (see can_prompt); otherwise it is a SKIP that
# says so. Nothing is weakened: the fetch below is the gate, and it still fails closed either way.
#
# bash-3.2/BSD-safe: no arrays, no mapfile, no GNU-only flags.
#
# NOTE: no `-e` — one failing assertion must not abort the run before its verdict line.

script_name="$(basename "$0")"

# The publication model fixes both branch names: dev is the messy working line, main the
# divorced public recast. They are model constants, not configuration.
readonly PUBLISHED_BRANCH=main
readonly WORKING_BRANCH=dev
readonly WATERMARK_REF=refs/published/main

pass_count=0
fail_count=0
skip_count=0

# Both MUST stay global — a `local` copy would be invisible to the exit trap. `phase` goes
# init -> checking and exists only to tell a usage error (nothing ran) from a death partway
# through. `reported` records that a verdict line was already printed, so the trap stays silent
# on the normal path rather than emitting a second one.
phase=init
reported=no

usage() { printf 'Usage: %s [--scope <path>]\n' "$script_name" >&2; }

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

# auth_classify RC OUTPUT -> pass|fail
#
# Deliberately an ALLOWLIST on the greeting text, and deliberately ignores RC: a SUCCESSFUL
# GitHub handshake exits 1 ("does not provide shell access"), so keying on rc==0 would report
# every healthy credential as broken. RC is accepted so callers can quote it in the message.
# Measured failure mode this must catch: a smartcard agent refusing to sign exits 255 with
# "agent refused operation" — no greeting, so it lands in the default arm.
auth_classify() {
  case "$2" in
    *"successfully authenticated"*) printf 'pass\n' ;;
    *) printf 'fail\n' ;;
  esac
}

# can_prompt -> 0 when this process has a terminal a pinentry could reach.
#
# A card-backed key needs a PIN, and gpg-agent delivers that prompt through a terminal. With none
# attached it does not hang — it REFUSES, "agent refused operation", rc=255, which is textually
# indistinguishable from a genuinely rejected credential. This is a PRECONDITION on the probe's
# environment rather than a postcondition on its opaque output: it needs nothing from ssh, gpg, or
# the agent, and it cannot be fooled by a message format changing.
can_prompt() { [[ -t 0 || -t 1 || -t 2 ]]; }

# auth_verdict CLASSIFY PROMPTABLE -> pass|fail|skip
#
# A SUCCESSFUL handshake is trustworthy however it was obtained, so it passes either way. Only a
# FAILURE is ambiguous, and only when nothing could have prompted: there, the honest report is
# "could not determine", never "your credentials are bad". Nothing is lost by declining to guess —
# the fetch below is the hard gate and still fails closed, so an unverified remote can never read
# as verified. This check exists to DIAGNOSE, not to gate.
auth_verdict() {
  if [[ "$1" == pass ]]; then printf 'pass\n'; return; fi
  if [[ "$2" == no ]]; then printf 'skip\n'; return; fi
  printf 'fail\n'
}

skip_remote_dependent() { # reason
  verdict_skip main-sync "$1"
  verdict_skip watermark-present "$1"
  verdict_skip watermark-integrity "$1"
  verdict_skip watermark-ancestor "$1"
  verdict_skip unpublished-work "$1"
}

skip_watermark_dependent() { # reason
  verdict_skip watermark-integrity "$1"
  verdict_skip watermark-ancestor "$1"
  verdict_skip unpublished-work "$1"
}

main() {
  local scope="" wm url out rc ahead behind fetch_rc promptable

  while [[ $# -gt 0 ]]; do
    case "$1" in
      --scope)
        [[ $# -ge 2 ]] || fatal 'the --scope flag needs a path'
        scope="$2"; shift 2 ;;
      *) fatal "unknown argument: $1" ;;
    esac
  done

  if [[ -z "$scope" ]]; then
    scope="$(git rev-parse --show-toplevel 2>/dev/null)" \
      || fatal 'no --scope given and the working directory is not a git repository'
  fi
  git -C "$scope" rev-parse --show-toplevel >/dev/null 2>&1 \
    || fatal "not a git repository: $scope"
  scope="$(git -C "$scope" rev-parse --show-toplevel)"

  [[ -f "$scope/.publication.toml" ]] \
    || fatal "no .publication.toml at $scope — not an adopted repo, so there is no publish path"

  phase=checking

  if can_prompt; then promptable=yes; else promptable=no; fi

  # ---------- tree-clean (local) ----------
  if [[ -z "$(git -C "$scope" status --porcelain 2>/dev/null)" ]]; then
    verdict_pass tree-clean
  else
    verdict_fail tree-clean \
      "uncommitted changes — the publish path checks out $PUBLISHED_BRANCH and applies bricks"
  fi

  # ---------- auth ----------
  # Gated on origin's URL, so a non-GitHub or file:// remote never triggers a network
  # handshake — the check reports SKIP there rather than a misleading PASS or FAIL.
  url="$(git -C "$scope" remote get-url origin 2>/dev/null)" || url=""
  case "$url" in
    git@github.com:*|ssh://git@github.com/*)
      out="$(ssh -o BatchMode=yes -o ConnectTimeout=10 -T git@github.com 2>&1)"; rc=$?
      case "$(auth_verdict "$(auth_classify "$rc" "$out")" "$promptable")" in
        pass) verdict_pass auth ;;
        skip) verdict_skip auth \
                "no terminal attached, so a card PIN prompt could not be delivered — this is not evidence about the credentials (rc=$rc): $out" ;;
        *)    verdict_fail auth "github ssh handshake did not authenticate (rc=$rc): $out" ;;
      esac
      ;;
    '') verdict_skip auth 'no origin remote configured' ;;
    *)  verdict_skip auth "origin is not github over ssh ($url)" ;;
  esac

  # ---------- fetch: THE ABORT POINT ----------
  # rc is captured on the very next line, before anything can clobber it.
  out="$(git -C "$scope" fetch origin "$PUBLISHED_BRANCH" 2>&1)"; fetch_rc=$?
  if [[ "$fetch_rc" -ne 0 ]]; then
    verdict_fail fetch "git fetch origin $PUBLISHED_BRANCH exited $fetch_rc: $out"
    if [[ "$promptable" == no ]]; then
      printf '  NOTE: no terminal is attached, so a card PIN prompt could not be delivered here.\n'
      printf '  Before treating this as a credential problem, re-run from an interactive shell.\n'
    fi
    skip_remote_dependent \
      "not run — the fetch failed, so any comparison here would read the STALE cached origin/$PUBLISHED_BRANCH"
    result_line FAIL 1
    return 1
  fi
  verdict_pass fetch

  # ---------- main-sync ----------
  if [[ "$(git -C "$scope" rev-parse "$PUBLISHED_BRANCH" 2>/dev/null)" == \
        "$(git -C "$scope" rev-parse "origin/$PUBLISHED_BRANCH" 2>/dev/null)" ]]; then
    verdict_pass main-sync
  else
    ahead="$(git -C "$scope" rev-list --count "origin/$PUBLISHED_BRANCH..$PUBLISHED_BRANCH" 2>/dev/null)"
    behind="$(git -C "$scope" rev-list --count "$PUBLISHED_BRANCH..origin/$PUBLISHED_BRANCH" 2>/dev/null)"
    verdict_fail main-sync \
      "local $PUBLISHED_BRANCH is ahead ${ahead:-?} / behind ${behind:-?} of origin/$PUBLISHED_BRANCH"
    if [[ "${ahead:-0}" -gt 0 ]]; then
      printf '  a prior publish minted tags and advanced %s but died before pushing.\n' "$PUBLISHED_BRANCH"
      printf '  recovery (delete tags FIRST, while they are still reachable to enumerate):\n'
      # shellcheck disable=SC2016  # literal operator instructions; $t must NOT expand here
      printf '    git tag --merged %s --no-merged origin/%s | while read -r t; do git tag -d "$t"; done\n' \
        "$PUBLISHED_BRANCH" "$PUBLISHED_BRANCH"
      printf '    git reset --hard origin/%s\n' "$PUBLISHED_BRANCH"
    else
      printf '  origin/%s moved ahead of local — investigate before publishing.\n' "$PUBLISHED_BRANCH"
    fi
    verdict_skip watermark-present \
      'not run — the start-invariant failed; read the watermark only once local and origin match'
    skip_watermark_dependent 'not run — the start-invariant failed'
    result_line FAIL 1
    return 1
  fi

  # ---------- watermark-present ----------
  wm="$(git -C "$scope" rev-parse --verify -q "$WATERMARK_REF")" || wm=""
  if [[ -z "$wm" ]]; then
    verdict_fail watermark-present \
      "no $WATERMARK_REF recorded — abort; --cutover is the only bypass"
    skip_watermark_dependent 'not run — no watermark to compare against'
    result_line FAIL 1
    return 1
  fi
  verdict_pass watermark-present

  # ---------- watermark-integrity (stranded BEHIND a succeeded push) ----------
  # CHANGELOG.md is the one and only excluded path: main carries a per-brick entry dev never
  # gets, so an unqualified tree-compare could never pass. Widening this stops proving anything.
  if git -C "$scope" diff --quiet "$wm" "$PUBLISHED_BRANCH" -- . ':(exclude)CHANGELOG.md' 2>/dev/null; then
    verdict_pass watermark-integrity
  else
    verdict_fail watermark-integrity \
      "$PUBLISHED_BRANCH's tree no longer matches the watermark — a publish pushed but never advanced it"
    printf '  do NOT re-derive (that re-appends published bricks). Advance the watermark instead:\n'
    printf '    git update-ref %s <the %s tip as of that publish>\n' "$WATERMARK_REF" "$WORKING_BRANCH"
  fi

  # ---------- watermark-ancestor ----------
  if git -C "$scope" merge-base --is-ancestor "$wm" "$WORKING_BRANCH" 2>/dev/null; then
    verdict_pass watermark-ancestor
  else
    verdict_fail watermark-ancestor \
      "the watermark is no longer an ancestor of $WORKING_BRANCH — a rebase or amend stranded it"
    printf '  abort and report; never guess a replacement watermark.\n'
  fi

  # ---------- unpublished-work ----------
  if [[ "$(git -C "$scope" rev-list --count "$wm..$WORKING_BRANCH" 2>/dev/null || echo 0)" -gt 0 ]]; then
    verdict_pass unpublished-work
  else
    verdict_fail unpublished-work \
      "nothing after the watermark — no bricks to re-derive onto $PUBLISHED_BRANCH"
  fi

  if [[ "$fail_count" -eq 0 ]]; then
    result_line PASS 0
    return 0
  fi
  result_line FAIL 1
  return 1
}

# Guarded (not a bare `main "$@"`) so the test suite can source this file to unit-test
# auth_classify() without running a sweep. The traps live INSIDE the guard for the same reason:
# a top-level EXIT trap would install itself into any shell that sources this file.
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  # Single quotes: `$?` must expand when the trap RUNS, not when it is defined.
  trap 'on_exit "$?"' EXIT
  trap 'exit 143' TERM
  trap 'exit 130' INT
  trap 'exit 129' HUP
  main "$@"
fi

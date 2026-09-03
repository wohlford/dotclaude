#!/usr/bin/env bash
set -euo pipefail

# Script: git-timing-guard.sh
# Purpose: PreToolUse hook — block PUBLISHING outside a configured time window
# Usage: Called by Claude Code hooks with JSON on stdin
#
# Exit codes:
#   0 — allow (no config, not a push, out of scope, inside the window, or any internal error)
#   2 — blocked: a push to a configured repo during the blocked window (stderr fed back to Claude)
#
# The gate is on the PUBLICATION BOUNDARY, not on git writes generally. `push` is
# the act that makes work public and is the only thing time-gated; `commit` and
# `tag` never leave the machine, so the window does not block them. Gating those
# too made the override a daily reflex — and a bypass used daily is not a gate.
# Regression suite: scripts/tests/test_git_timing_guard.sh, in this repo. It used to sit beside
# the live copy as .git-timing-guard.test.sh; that is no longer where it lives, and the suite
# resolves this file as its SIBLING (../git-timing-guard.sh), never through $HOME.
#
# The window/repo/day policy lives in an untracked local config; absent → no-op (fail open).
# Global PreToolUse(Bash) hook: exits 0 cheaply for the common non-git case.
#
# PROVENANCE — BOTH PHASES ARE DONE. THIS FILE IS THE RUNNING GATE.
# Phase 1 (2026-08-25) tracked a byte-identical copy of the machine-local
# ~/.claude/.git-timing-guard.sh, taken at
#   sha256 14ad06c7da787c72299cd27dba6ab04684eda9b04d3082edc5aa472236ce618e
# Phase 2 (2026-09-01) re-pointed the live PreToolUse registration here, on operator
# authorization, after re-measuring that digest against the live file and finding them equal. It
# was then verified live: the gate fired and its refusal named this file's installed path. So an
# edit HERE changes what the gate does, from the next promote onward. The orphaned original is
# left in place untouched; retiring it is a separate, separately authorized cleanup. The policy
# config (.git-timing-guard.conf) stays untracked by design: policy is local, logic is versioned.
#
# The paragraph above replaced one that said "phase 1 of two ... NOT yet the running gate ...
# re-pointing is held for the operator". That text was true when written and FALSE for the seven
# days after phase 2 landed, and it read exactly like a live statement of fact the whole time — a
# reviewer relied on it and built a recommendation on a premise that no longer held. If you change
# what this file IS, change this paragraph in the same commit; a stale status here is not a
# cosmetic defect, it is the same silent-staleness failure the block further down exists to catch.
#
# DOCUMENTED EXCEPTION (not a defect to fix in this pass) — scripts/HOOKS.md requires hooks to be
# bash-3.2/BSD-safe with "no mapfile", and the segment split below uses `mapfile -t SEG` (line 40
# of the copied original). Measured under /bin/bash 3.2.57: the hook exits 127 with
# "mapfile: command not found", which is a non-2 exit and therefore FAILS OPEN per HOOKS.md's
# contract — it cannot false-block, but it also cannot gate under that interpreter. It runs
# because `env bash` resolves to a 4+/5.x bash here. Recorded rather than rewritten: this pass
# tracks existing behaviour unchanged, and replacing the split would alter a live gate's parsing.

# FAIL-OPEN HERE IS DELIBERATE, AND FIVE DIFFERENT CONDITIONS REACH IT.
# Conditions 2, 3 and 4 make the config read below yield an empty GUARD_REPO_PATTERN — and so
# disable the gate. Condition 1 is different: it exits at the `[ -f "$conf" ] || exit 0` test
# that follows, before the file is ever opened, so no config read happens at all for it —
# "yields an empty pattern" describes 2/3/4 only, never 1.
#   1. the file being ABSENT      (the documented way to disable; correct and intended)
#   2. the file being UNREADABLE  (a permissions accident)
#   3. a MISSPELLED key           (an edit that looks right)
#   4. the key present but EMPTY  (a half-finished edit)
# A FIFTH reaches the same fail-open through a DIFFERENT mechanism, earlier than 2, 3 and 4 (not
# earlier than 1 — `command -v jq` runs strictly after the `-f` test that follows, so on an
# absent conf condition 1 is still the one that fires first): `command -v jq` failing exits
# before the conf is even opened, so jq being missing from PATH disables the gate regardless of
# how good the conf is —
#   5. jq being UNAVAILABLE on PATH (an environment change, not a conf problem at all)
# Only (1) is intent. The other four silently retire a live gate, and measurement confirms all
# five are indistinguishable from inside this script.
#
# Do NOT "fix" that by refusing here. Without a usable pattern this script cannot know WHICH
# repositories it governs, so refusing would assert authority over every repository on the
# machine at all hours, triggered by a permissions change — and scripts/HOOKS.md is explicit
# that only a real violation may refuse. Fail-open is the right behaviour; the SILENCE was the
# defect, and it is fixed OUTSIDE this file.
#
# THE OBSERVER: the `timing-guard-conf` check in skills/audit/audit.sh distinguishes all five
# above and reports 2/3/4 as failures against the CONF. Condition 5 surfaces differently: that
# check needs jq itself to even read this repo's registration, so jq being unavailable to IT is
# also FAILed (not folded into its "registration undecidable" SKIP) — on the reasoning that jq
# missing from this machine's PATH is evidence the guard, wherever registered, is failing open
# the identical way, not merely uncertainty about registration. It resolves this same conf path
# the same hardcoded way; if you ever change how the path below is derived, change it there too
# or that check grades a file nobody is using. Why an EXTERNAL observer rather than a warning
# from inside this script: all three hook-side channels were measured and all three fail. A
# structured message field has undocumented user-facing behaviour; a non-2 non-zero exit renders
# identically to this script crashing, which `set -euo pipefail` makes indistinguishable; and a
# prompt-the-user decision re-imports the blocking this design rejected. Recorded here rather
# than by reference, because a pointer a reader cannot follow is the same as no reasoning at all.
# INVOCATION CONTRACT, asserted for every registered hook by
# scripts/tests/test_hook_argv_refusal.py. This must come FIRST, before the policy read below:
# on a machine with no policy file the guard exits 0 at the `-f` test, so a refusal placed after
# it would return 0 and the invariant would go unenforced exactly where it is least observable.
# Both tests are false on the real path — Claude Code invokes this with no arguments and a piped
# payload — so this cannot change any live verdict; it only stops the two malformed invocations
# from reading as a clean pass (argv) or hanging forever on a read that will never arrive (tty).
if [ "$#" -gt 0 ]; then
  printf 'git-timing-guard: expects a JSON payload on stdin, not arguments — see scripts/HOOKS.md\n' >&2
  exit 2
fi
if [ -t 0 ]; then
  printf 'git-timing-guard: expects a JSON payload on stdin; refusing a terminal stdin rather than blocking on a read that will never arrive — see scripts/HOOKS.md\n' >&2
  exit 2
fi

conf="$HOME/.claude/.git-timing-guard.conf"
[ -f "$conf" ] || exit 0
command -v jq >/dev/null 2>&1 || exit 0

input=$(cat) || exit 0
cmd=$(printf '%s' "$input" | jq -r '.tool_input.command // empty' 2>/dev/null) || exit 0
cwd=$(printf '%s' "$input" | jq -r '.cwd // empty' 2>/dev/null) || exit 0
[ -n "$cmd" ] || exit 0

conf_get() {
  grep -E "^$1=" "$conf" 2>/dev/null | tail -1 | cut -d= -f2- | tr -d "\"' \\r"
}
repo_pat=$(conf_get GUARD_REPO_PATTERN || true)
[ -n "$repo_pat" ] || exit 0
start=$(conf_get GUARD_START || true); start=${start:-0800}
end=$(conf_get GUARD_END || true);     end=${end:-1700}
days=$(conf_get GUARD_DAYS || true);   days=${days:-1-5}

# Split the command into segments on && || ; | and newlines.
mapfile -t SEG < <(printf '%s\n' "$cmd" | sed -E 's/(&&|[|][|]|;|[|])/\n/g')

# Override: a leading ALLOW_GIT_WRITE=1 assignment on any segment.
for seg in "${SEG[@]}"; do
  if printf '%s' "$seg" | grep -qE '^[[:space:]]*ALLOW_GIT_WRITE=1([[:space:]]|$)'; then
    exit 0
  fi
done

# git, tolerating an env-var prefix and -C/-c/-<opt> globals, up to the subcommand.
# Only `push` is matched — see the publication-boundary note in the header. A local
# `commit` or `tag` is deliberately NOT detected here; it is not a partial gate, it
# is the whole policy: publishing waits for the window, working never does.
gp='(^|[^A-Za-z0-9_])git([[:space:]]+-C[[:space:]]+[^[:space:]]+|[[:space:]]+-c[[:space:]]+[^[:space:]]+|[[:space:]]+-[^[:space:]]+)*[[:space:]]+'
is_public=0
target="$cwd"
for seg in "${SEG[@]}"; do
  if printf '%s' "$seg" | grep -qE "${gp}push([^A-Za-z0-9_]|\$)"; then
    is_public=1
    cpath=$(printf '%s' "$seg" | grep -oE -- '-C[[:space:]]+[^[:space:]]+' | head -1 | sed -E 's/-C[[:space:]]+//' || true)
    [ -n "$cpath" ] && target="$cpath"
    break
  fi
done
[ "$is_public" = 1 ] || exit 0

# Scope: the target repo's origin must match the configured pattern.
origin=$(git -C "$target" remote get-url origin 2>/dev/null || true)
printf '%s' "$origin" | grep -qF "$repo_pat" || exit 0

# Time window — base-10 forced so 08/09 don't parse as octal.
dow=$(date +%u 2>/dev/null || true); [ -n "$dow" ] || exit 0
hh=$(date +%H 2>/dev/null || true);  [ -n "$hh" ] || exit 0
mm=$(date +%M 2>/dev/null || true);  [ -n "$mm" ] || exit 0
now=$((10#$hh * 60 + 10#$mm))
smin=$((10#${start:0:2} * 60 + 10#${start:2:2}))
emin=$((10#${end:0:2} * 60 + 10#${end:2:2}))
dlo=${days%%-*}; dhi=${days##*-}

if [ "$dow" -ge "$dlo" ] && [ "$dow" -le "$dhi" ] && [ "$now" -ge "$smin" ] && [ "$now" -lt "$emin" ]; then
  printf 'blocked by git timing guard: pushing is paused until %s local — publish after the window. Local commits and tags are not gated.\n' "$end" >&2
  exit 2
fi
exit 0

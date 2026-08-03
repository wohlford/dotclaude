#!/usr/bin/env bash
set -uo pipefail

# Script: test_propagate_postcheck.sh
# Purpose: Regression tests for scripts/propagate-postcheck.sh — the post-merge verification for
#          /propagate's LOCAL promote. Covers both postcondition branches, the fail-closed
#          handling of an undeterminable range, and the property that makes a wrong branch
#          choice non-silent: the hooks check runs on BOTH branches.
# Usage:   ./scripts/tests/test_propagate_postcheck.sh
#
# The measured defects these encode:
#   * The strict branch was four hand-run commands with no single verdict line (2026-08-01).
#   * A restored runtime settings.json predating the incoming commit shipped a dead gate at
#     25 runtime / 25 committed entries — equal counts differing in both directions, which
#     every tally reads as agreement (2026-07-31).
#
# Fixtures model the real topology: a `src` repo standing in for dev, and a `live` clone standing
# in for production with `settings.json` marked skip-worktree and carrying a machine-local key
# the promote must preserve.
#
# The sandbox root is pinned with `pwd -P`: $TMPDIR is a symlink on macOS (/tmp -> /private/tmp)
# and a logical path does not physically contain its files, which silently routes path-resolving
# tools down a different branch than the one under test.

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
engine="$here/../../scripts/propagate-postcheck.sh"
tmproot="$(cd "$(mktemp -d)" && pwd -P)"
trap 'rm -rf "$tmproot"' EXIT

pass=0
fail=0
pass_line() { printf 'PASS  %s\n' "$1"; pass=$((pass + 1)); }
fail_line() { printf 'FAIL  %s\n' "$1"; fail=$((fail + 1)); }

check_eq() { # got want label
  if [[ "$1" == "$2" ]]; then pass_line "$3"; else fail_line "$3 (want [$2] got [$1])"; fi
}

check_has() { # haystack needle label
  if printf '%s' "$1" | grep -qF -- "$2"; then
    pass_line "$3"
  else
    fail_line "$3 (missing [$2])"
  fi
}

check_lacks() { # haystack needle label
  if printf '%s' "$1" | grep -qF -- "$2"; then
    fail_line "$3 (unexpectedly present: [$2])"
  else
    pass_line "$3"
  fi
}

sha256_of() { # path
  if command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$1" | cut -d' ' -f1
  else
    sha256sum "$1" | cut -d' ' -f1
  fi
}

# ---------- fixtures ----------

# settings_json MODEL HOOKCOMMANDS... -> a settings document on stdout.
# `model` stands in for the machine-local runtime preference a promote must never clobber.
settings_json() {
  local model="$1"; shift
  local first=1 c
  printf '{\n  "model": "%s",\n  "hooks": {\n    "PreToolUse": [\n' "$model"
  for c in "$@"; do
    [[ "$first" -eq 1 ]] || printf ',\n'
    first=0
    printf '      {"matcher": "Bash", "hooks": [{"type": "command", "command": "%s"}]}' "$c"
  done
  printf '\n    ]\n  }\n}\n'
}

# mk DIR -> $DIR/src (dev stand-in) and $DIR/live (production stand-in, skip-worktree set,
# carrying a machine-local model the committed file does not have).
mk() {
  local d="$1"
  local s="$d/src"
  local l="$d/live"
  mkdir -p "$d"
  git init -q -b dev "$s"
  git -C "$s" config user.email test@test.invalid
  git -C "$s" config user.name test
  git -C "$s" config commit.gpgsign false

  settings_json committed scripts/guard-one.sh > "$s/settings.json"
  printf 'v1\n' > "$s/payload.txt"
  git -C "$s" add -A
  git -C "$s" commit -qm 'base'

  git clone -qb dev "$s" "$l"
  git -C "$l" config user.email test@test.invalid
  git -C "$l" config user.name test
  git -C "$l" config commit.gpgsign false

  # The runtime file: same registrations, machine-local model. This is the skip-worktree state.
  settings_json runtime-local scripts/guard-one.sh > "$l/settings.json"
  git -C "$l" update-index --skip-worktree settings.json
}

# advance_payload DIR -> a src commit that does NOT touch settings.json (the strict branch).
advance_payload() {
  printf 'v2\n' > "$1/src/payload.txt"
  git -C "$1/src" commit -qam 'payload only'
}

# advance_settings DIR -> a src commit that DOES touch settings.json, registering a second hook
# (the in-range branch).
advance_settings() {
  settings_json committed scripts/guard-one.sh scripts/guard-two.sh > "$1/src/settings.json"
  git -C "$1/src" commit -qam 'register a second hook'
}

# promote DIR -> the plain fast-forward /propagate performs when settings.json is not in range.
promote() {
  local l="$1/live"
  git -C "$l" fetch -q "$1/src" dev
  git -C "$l" merge -q --ff-only FETCH_HEAD
}

# promote_with_dance DIR -> the park/fast-forward/restore sequence /propagate performs when
# settings.json IS in the incoming range. Leaves the runtime file exactly as it was — which is
# precisely the state that ships a dead gate until the hooks are hand-added.
promote_with_dance() {
  local l="$1/live"
  git -C "$l" fetch -q "$1/src" dev
  git -C "$l" update-index --no-skip-worktree settings.json
  git -C "$l" stash push -q -m 'runtime settings.json' -- settings.json
  git -C "$l" merge -q --ff-only FETCH_HEAD
  git -C "$l" checkout -q 'stash@{0}' -- settings.json && git -C "$l" stash drop -q
  git -C "$l" reset -q HEAD -- settings.json
  git -C "$l" update-index --skip-worktree settings.json
}

# ---------- 1. the strict branch, clean ----------

r="$tmproot/strict-clean"; mk "$r"
before="$(sha256_of "$r/live/settings.json")"
advance_payload "$r"; promote "$r"
OUT="$("$engine" --scope "$r/live" --before-sha "$before" 2>/dev/null)"; RC=$?

check_eq   "$RC" 0 'strict clean: rc=0'
check_has  "$OUT" 'RESULT: PASS' 'strict clean: RESULT is PASS'
check_has  "$OUT" 'PASS range' 'strict clean: the range was determined'
check_has  "$OUT" 'PASS merge-applied' 'strict clean: the merge landed'
check_has  "$OUT" 'PASS settings-identical' 'strict clean: the runtime file is byte-identical'
check_has  "$OUT" 'PASS stash-empty' 'strict clean: no stash left behind'
check_has  "$OUT" 'PASS skip-worktree' 'strict clean: the flag is still set'
check_has  "$OUT" 'PASS hooks-registered' 'strict clean: the hooks check ran on the strict branch too'
check_has  "$OUT" 'NOT in the incoming range' 'strict clean: the chosen branch is named'
# The machine-local preference is the thing the strict branch exists to protect.
check_has  "$(cat "$r/live/settings.json")" 'runtime-local' 'strict clean: the runtime model survived'

# The verdict line must be LAST — a summary buried mid-output is one a reader can miss.
check_eq "$(printf '%s' "$OUT" | tail -1 | cut -d' ' -f1-2)" 'RESULT: PASS' 'strict clean: verdict is the last line'

# ---------- 2. strict branch, the runtime file was clobbered ----------

r="$tmproot/strict-clobber"; mk "$r"
before="$(sha256_of "$r/live/settings.json")"
advance_payload "$r"; promote "$r"
settings_json wiped scripts/guard-one.sh > "$r/live/settings.json"   # a promote that lost the prefs
OUT="$("$engine" --scope "$r/live" --before-sha "$before" 2>/dev/null)"; RC=$?

check_eq   "$RC" 1 'clobbered: rc=1'
check_has  "$OUT" 'FAIL settings-identical' 'clobbered: the identity assertion fails'
check_has  "$OUT" 'RESULT: FAIL' 'clobbered: RESULT is FAIL'

# ---------- 3. strict branch, a stash was left behind ----------

r="$tmproot/strict-stash"; mk "$r"
before="$(sha256_of "$r/live/settings.json")"
advance_payload "$r"; promote "$r"
printf 'stray\n' > "$r/live/payload.txt"
git -C "$r/live" stash push -q -m 'stray' -- payload.txt
OUT="$("$engine" --scope "$r/live" --before-sha "$before" 2>/dev/null)"; RC=$?

check_eq   "$RC" 1 'stash left: rc=1'
check_has  "$OUT" 'FAIL stash-empty' 'stash left: the stash assertion fails'

# ---------- 4. strict branch, skip-worktree was dropped ----------

r="$tmproot/strict-flag"; mk "$r"
before="$(sha256_of "$r/live/settings.json")"
advance_payload "$r"; promote "$r"
git -C "$r/live" update-index --no-skip-worktree settings.json
OUT="$("$engine" --scope "$r/live" --before-sha "$before" 2>/dev/null)"; RC=$?

check_eq   "$RC" 1 'flag dropped: rc=1'
check_has  "$OUT" 'FAIL skip-worktree' 'flag dropped: the flag assertion fails'

# ---------- 5. the in-range branch, hooks hand-added as the skill prescribes ----------

r="$tmproot/inrange-ok"; mk "$r"
before="$(sha256_of "$r/live/settings.json")"
advance_settings "$r"; promote_with_dance "$r"
# The skill's hand-add step: copy the newly registered hook into the restored runtime file,
# keeping the machine-local model.
settings_json runtime-local scripts/guard-one.sh scripts/guard-two.sh > "$r/live/settings.json"
OUT="$("$engine" --scope "$r/live" --before-sha "$before" 2>/dev/null)"; RC=$?

check_eq   "$RC" 0 'in-range ok: rc=0'
check_has  "$OUT" 'RESULT: PASS' 'in-range ok: RESULT is PASS'
check_has  "$OUT" 'PASS hooks-registered' 'in-range ok: every committed registration is live'
check_has  "$OUT" 'IS in the incoming range' 'in-range ok: the chosen branch is named'
# Identity must NOT be required here — the hand-add legitimately changes the file.
check_has  "$OUT" 'SKIP settings-identical' 'in-range ok: identity is skipped, not failed'
check_lacks "$OUT" 'FAIL settings-identical' 'in-range ok: identity is not asserted on this branch'

# ---------- 6. the in-range branch with the hand-add SKIPPED — the measured dead gate ----------

r="$tmproot/inrange-dead"; mk "$r"
before="$(sha256_of "$r/live/settings.json")"
advance_settings "$r"; promote_with_dance "$r"
# No hand-add. The restored file predates the incoming commit, so it lacks guard-two.
OUT="$("$engine" --scope "$r/live" --before-sha "$before" 2>/dev/null)"; RC=$?

check_eq   "$RC" 1 'dead gate: rc=1'
check_has  "$OUT" 'FAIL hooks-registered' 'dead gate: the missing registration is caught'
check_has  "$OUT" 'guard-two' 'dead gate: the missing hook is named'
check_has  "$OUT" 'RESULT: FAIL' 'dead gate: RESULT is FAIL'
# This is the case a byte-identity check reads as perfect, which is why identity cannot be the
# instrument on this branch.
check_lacks "$OUT" 'FAIL settings-identical' 'dead gate: identity would have read CLEAN here'

# ---------- 7. an undeterminable range fails CLOSED ----------
# With no ORIG_HEAD and no --before-head the script cannot know which branch applies. The
# fail-closed direction is to REQUIRE identity: wrong that way is loud, the other way is silent.

r="$tmproot/norange"; mk "$r"
before="$(sha256_of "$r/live/settings.json")"
advance_payload "$r"; promote "$r"
rm -f "$r/live/.git/ORIG_HEAD"
settings_json wiped scripts/guard-one.sh > "$r/live/settings.json"
OUT="$("$engine" --scope "$r/live" --before-sha "$before" 2>/dev/null)"; RC=$?

check_eq   "$RC" 1 'no range: rc=1'
check_has  "$OUT" 'FAIL range' 'no range: the undeterminable range is itself reported'
check_has  "$OUT" 'FAIL settings-identical' 'no range: identity is still asserted (fail-closed)'

# ---------- 8. --before-head overrides the ORIG_HEAD default ----------

r="$tmproot/beforehead"; mk "$r"
before="$(sha256_of "$r/live/settings.json")"
base="$(git -C "$r/live" rev-parse HEAD)"
advance_settings "$r"; promote_with_dance "$r"
rm -f "$r/live/.git/ORIG_HEAD"
settings_json runtime-local scripts/guard-one.sh scripts/guard-two.sh > "$r/live/settings.json"
OUT="$("$engine" --scope "$r/live" --before-sha "$before" --before-head "$base" 2>/dev/null)"; RC=$?

check_eq   "$RC" 0 'before-head: rc=0'
check_has  "$OUT" 'PASS range' 'before-head: the explicit base determines the range'
check_has  "$OUT" 'IS in the incoming range' 'before-head: the in-range branch was chosen'

# ---------- 9. a before-head that is not an ancestor is refused, not believed ----------

r="$tmproot/badhead"; mk "$r"
before="$(sha256_of "$r/live/settings.json")"
advance_payload "$r"; promote "$r"
# A commit from the same lineage IS an ancestor, so it would not exercise the guard. Build a
# genuinely unrelated commit object instead — no parent, empty tree — without touching the
# worktree, which a `checkout --orphan` would disturb (settings.json is skip-worktree here).
stray="$(git -C "$r/live" commit-tree "$(git -C "$r/live" mktree </dev/null)" -m orphan </dev/null)"
OUT="$("$engine" --scope "$r/live" --before-sha "$before" --before-head "$stray" 2>/dev/null)"; RC=$?

check_eq   "$RC" 1 'bad before-head: rc=1'
check_has  "$OUT" 'FAIL range' 'bad before-head: a non-ancestor is refused rather than believed'

# ---------- 10. the merge never landed ----------

r="$tmproot/nomerge"; mk "$r"
before="$(sha256_of "$r/live/settings.json")"
advance_payload "$r"
git -C "$r/live" fetch -q "$r/src" dev        # fetched, never merged
OUT="$("$engine" --scope "$r/live" --before-sha "$before" --ref FETCH_HEAD 2>/dev/null)"; RC=$?

check_eq   "$RC" 1 'no merge: rc=1'
check_has  "$OUT" 'FAIL merge-applied' 'no merge: an unapplied promote is caught'

# ---------- 11. usage errors are ERROR, never a quiet pass ----------

OUT="$("$engine" --before-sha deadbeef 2>/dev/null)"; RC=$?
check_eq  "$RC" 2 'no --scope: rc=2'
check_has "$OUT" 'RESULT: ERROR' 'no --scope: RESULT is ERROR'

OUT="$("$engine" --scope "$tmproot/strict-clean/live" 2>/dev/null)"; RC=$?
check_eq  "$RC" 2 'no --before-sha: rc=2'
check_has "$OUT" 'RESULT: ERROR' 'no --before-sha: RESULT is ERROR'
check_has "$OUT" 'before' 'no --before-sha: the reason names the missing value'

OUT="$("$engine" --scope "$tmproot/strict-clean/live" --before-sha x --wat 2>/dev/null)"; RC=$?
check_eq  "$RC" 2 'unknown flag: rc=2'
check_has "$OUT" 'RESULT: ERROR' 'unknown flag: RESULT is ERROR'

OUT="$("$engine" --scope "$tmproot/not-a-repo" --before-sha x 2>/dev/null)"; RC=$?
check_eq  "$RC" 2 'not a repo: rc=2'
check_has "$OUT" 'RESULT: ERROR' 'not a repo: RESULT is ERROR'

# A missing runtime settings.json is ERROR — the tool's whole subject is absent, so it reached
# no verdict. Reporting that as FAIL would misdescribe it as a judged promote.
r="$tmproot/nosettings"; mk "$r"
before="$(sha256_of "$r/live/settings.json")"
advance_payload "$r"; promote "$r"
rm -f "$r/live/settings.json"
OUT="$("$engine" --scope "$r/live" --before-sha "$before" 2>/dev/null)"; RC=$?
check_eq  "$RC" 2 'missing settings.json: rc=2'
check_has "$OUT" 'RESULT: ERROR' 'missing settings.json: RESULT is ERROR'

# ---------- 12. the helper copy that produced the verdict is named ----------
# The repo's standing rule: a checker that resolves its helpers relative to itself must say which
# copy it used, so a stale-tool verdict is never anonymous.

r="$tmproot/named"; mk "$r"
before="$(sha256_of "$r/live/settings.json")"
advance_payload "$r"; promote "$r"
OUT="$("$engine" --scope "$r/live" --before-sha "$before" 2>/dev/null)"
check_has "$OUT" 'settings-hooks-check.py' 'helper: the resolved hooks-check path is printed'

# ---------- 13. a MISSING helper fails closed ----------
# "The registration check could not run" is not "the registrations are fine". An absent helper
# must never be the quiet path — this is the shape that turns a gate into a rubber stamp.

mkdir -p "$tmproot/lonely"
cp "$engine" "$tmproot/lonely/"                     # copied WITHOUT settings-hooks-check.py
r="$tmproot/lonely-repo"; mk "$r"
before="$(sha256_of "$r/live/settings.json")"
advance_payload "$r"; promote "$r"
OUT="$("$tmproot/lonely/$(basename "$engine")" --scope "$r/live" --before-sha "$before" 2>/dev/null)"; RC=$?

check_eq  "$RC" 1 'missing helper: rc=1'
check_has "$OUT" 'FAIL hooks-registered' 'missing helper: an absent helper FAILS rather than passing'
check_has "$OUT" 'RESULT: FAIL' 'missing helper: RESULT is FAIL'

# ---------- 14. the hooks check genuinely RUNS on the strict branch ----------
# The design property that makes a mis-determined range non-silent. Asserting `PASS
# hooks-registered` on a healthy strict promote does NOT prove this — a version that skipped the
# check and reported PASS anyway would satisfy it. So: degrade the runtime file BEFORE the
# promote, leaving byte-identity intact across it. Then the ONLY instrument that can catch the
# dead gate is the hooks check, on the branch where it is not strictly required to run.

r="$tmproot/strict-deadgate"; mk "$r"
printf '{\n  "model": "runtime-local",\n  "hooks": {}\n}\n' > "$r/live/settings.json"
before="$(sha256_of "$r/live/settings.json")"
advance_payload "$r"; promote "$r"
OUT="$("$engine" --scope "$r/live" --before-sha "$before" 2>/dev/null)"; RC=$?

check_eq   "$RC" 1 'strict dead gate: rc=1'
check_has  "$OUT" 'NOT in the incoming range' 'strict dead gate: this is the strict branch'
check_has  "$OUT" 'PASS settings-identical' 'strict dead gate: byte-identity reads CLEAN here'
check_has  "$OUT" 'FAIL hooks-registered' 'strict dead gate: the hooks check still catches it'
check_has  "$OUT" 'guard-one' 'strict dead gate: the missing hook is named'

# ---------- summary ----------
# Printed BEFORE the verdict is computed, so its absence is itself the signal that this
# run died rather than passed.
printf '\nRESULT: %s passed, %s failed\n' "$pass" "$fail"
if [[ "$fail" -eq 0 ]]; then exit 0; else exit 1; fi

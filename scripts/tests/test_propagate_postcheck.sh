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

# The farm rows below run the REAL install.sh, which honours $HOME. Recorded here, before any
# fixture exists, so the last row can assert the operator's live config root was never the
# subject — the 16 symlinks every session resolves through are not something to verify by
# intention.
#
# Only the SYMLINKS are recorded: a live config root is written to constantly (logs, projects,
# todos), so a whole-tree digest would flap for reasons that have nothing to do with this suite.
# The links are exactly what install.sh manages, and exactly what a stray run would change.
farm_symlink_state() { # home-dir
  local root="$1/.claude" e
  [[ -d "$root" ]] || return 0
  for e in "$root"/* "$root"/.*; do
    [[ -L "$e" ]] || continue
    printf '%s -> %s\n' "$(basename "$e")" "$(readlink "$e")"
  done | sort
}

REAL_HOME="$HOME"
live_farm_before="$(farm_symlink_state "$REAL_HOME")"

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
  git -C "$s" config tag.gpgsign false

  settings_json committed scripts/guard-one.sh > "$s/settings.json"
  printf 'v1\n' > "$s/payload.txt"
  git -C "$s" add -A
  git -C "$s" commit -qm 'base'

  git clone -qb dev "$s" "$l"
  git -C "$l" config user.email test@test.invalid
  git -C "$l" config user.name test
  git -C "$l" config commit.gpgsign false
  git -C "$l" config tag.gpgsign false

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

# ---------- N. the boundary hook, adopted fixtures ----------

# mk_adopted DIR -> mk(), plus what makes the boundary real: a tracked .publication.toml (the
# adoption predicate), a tracked git-hooks/pre-push with history, a LOCAL refs/heads/main, and the
# hook installed in live.
#
# The local `main` is load-bearing HERE for a specific reason: the check under test FAILs an armed
# repo whose refs/heads/main does not resolve, mirroring audit.sh. A clone gives you origin/main
# only, so without this line every row below would return that FAIL and none of them would reach
# the digest logic they were written for -- every verdict correct, every verdict about the wrong
# question. (This is also why fixtures for the boundary at large need it: the allowlist is stated
# in terms of reachability from local main.)
mk_adopted() {
  local d="$1"
  mk "$d"
  local s="$d/src" l="$d/live"
  mkdir -p "$s/git-hooks"
  printf '# adopted\nproduction = "dev"\n' > "$s/.publication.toml"
  printf '#!/usr/bin/env bash\n# boundary hook v1\nexit 0\n' > "$s/git-hooks/pre-push"
  chmod +x "$s/git-hooks/pre-push"
  git -C "$s" add -A
  git -C "$s" commit -qm 'adopt: boundary hook v1'
  git -C "$l" fetch -q "$s" dev
  git -C "$l" merge -q --ff-only FETCH_HEAD
  git -C "$l" branch -q main 2>/dev/null || true
  cp "$l/git-hooks/pre-push" "$(cd "$l" && git rev-parse --path-format=absolute --git-path hooks)/pre-push"
  chmod +x "$(cd "$l" && git rev-parse --path-format=absolute --git-path hooks)/pre-push"
}

hookdest() { printf '%s/pre-push\n' "$(cd "$1" && git rev-parse --path-format=absolute --git-path hooks)"; }

# advance_hook DIR -> a src commit changing the boundary hook (the promote that goes stale).
advance_hook() {
  printf '#!/usr/bin/env bash\n# boundary hook v2\nexit 0\n' > "$1/src/git-hooks/pre-push"
  git -C "$1/src" commit -qam 'boundary hook v2'
}

# --- clean: installed matches tracked ---
r="$tmproot/hook-clean"; mk_adopted "$r"
before="$(sha256_of "$r/live/settings.json")"
advance_payload "$r"; promote "$r"
OUT="$("$engine" --scope "$r/live" --before-sha "$before" 2>/dev/null)"; RC=$?
check_eq  "$RC" 0 'hook clean: rc=0'
check_has "$OUT" 'PASS pre-push-installed' 'hook clean: the boundary hook check passes'

# --- stale: the promote changed the hook and nothing re-installed it ---
# This is the exact state the postcheck reported RESULT: PASS over before this check existed.
r="$tmproot/hook-stale"; mk_adopted "$r"
before="$(sha256_of "$r/live/settings.json")"
advance_hook "$r"; promote "$r"
OUT="$("$engine" --scope "$r/live" --before-sha "$before" 2>/dev/null)"; RC=$?
check_eq   "$RC" 1 'hook stale: rc=1'
check_has  "$OUT" 'FAIL pre-push-installed' 'hook stale: the check FAILS'
check_has  "$OUT" 'RESULT: FAIL' 'hook stale: RESULT is FAIL'
check_has  "$OUT" 'install-git-hooks.sh --force-if-ours' 'hook stale: the remedy is named'

# --- foreign: installed matches no tracked version; the installer remedy must NOT be offered ---
r="$tmproot/hook-foreign"; mk_adopted "$r"
before="$(sha256_of "$r/live/settings.json")"
advance_hook "$r"; promote "$r"
printf '#!/usr/bin/env bash\n# planted\nexit 0\n' > "$(hookdest "$r/live")"
OUT="$("$engine" --scope "$r/live" --before-sha "$before" 2>/dev/null)"; RC=$?
check_eq    "$RC" 1 'hook foreign: rc=1'
check_has   "$OUT" 'FAIL pre-push-installed' 'hook foreign: the check FAILS'
check_lacks "$OUT" '--force-if-ours' 'hook foreign: the installer remedy is NOT offered'

# --- missing ---
r="$tmproot/hook-missing"; mk_adopted "$r"
before="$(sha256_of "$r/live/settings.json")"
advance_payload "$r"; promote "$r"
rm -f "$(hookdest "$r/live")"
OUT="$("$engine" --scope "$r/live" --before-sha "$before" 2>/dev/null)"; RC=$?
check_eq  "$RC" 1 'hook missing: rc=1'
check_has "$OUT" 'not installed' 'hook missing: the cause is named'

# --- not executable: git silently ignores such a hook, so this must not read as clean ---
r="$tmproot/hook-noexec"; mk_adopted "$r"
before="$(sha256_of "$r/live/settings.json")"
advance_payload "$r"; promote "$r"
chmod -x "$(hookdest "$r/live")"
OUT="$("$engine" --scope "$r/live" --before-sha "$before" 2>/dev/null)"; RC=$?
check_eq  "$RC" 1 'hook noexec: rc=1'
check_has "$OUT" 'not executable' 'hook noexec: the cause is named'

# --- symlink: refused, and NOT reported as a digest match ---
# Without this row, deleting the `-L` branch would let a symlink pointing at current tracked
# content PASS here while audit.sh FAILs it and the installer refuses it -- three tools, two
# answers, on the one condition the shared check name promises they agree about.
r="$tmproot/hook-symlink"; mk_adopted "$r"
before="$(sha256_of "$r/live/settings.json")"
advance_payload "$r"; promote "$r"
d="$(hookdest "$r/live")"; rm -f "$d"; ln -s "$r/live/git-hooks/pre-push" "$d"
OUT="$("$engine" --scope "$r/live" --before-sha "$before" 2>/dev/null)"; RC=$?
check_eq  "$RC" 1 'hook symlink: rc=1 even though the target content matches'
check_has "$OUT" 'is a symlink' 'hook symlink: the symlink cause is named'

# --- tracked source missing from an ADOPTED repo ---
r="$tmproot/hook-nosource"; mk_adopted "$r"
before="$(sha256_of "$r/live/settings.json")"
advance_payload "$r"; promote "$r"
rm -f "$r/live/git-hooks/pre-push"
OUT="$("$engine" --scope "$r/live" --before-sha "$before" 2>/dev/null)"; RC=$?
check_eq  "$RC" 1 'hook nosource: rc=1'
check_has "$OUT" 'tracked source' 'hook nosource: the missing source is named'

# --- armed but refs/heads/main does not resolve ---
# audit.sh FAILs this state; so must the postcheck, or the two disagree about one repo.
r="$tmproot/hook-nomain"; mk_adopted "$r"
before="$(sha256_of "$r/live/settings.json")"
advance_payload "$r"; promote "$r"
git -C "$r/live" branch -q -D main
OUT="$("$engine" --scope "$r/live" --before-sha "$before" 2>/dev/null)"; RC=$?
check_eq  "$RC" 1 'hook nomain: rc=1'
check_has "$OUT" 'refs/heads/main does not resolve' 'hook nomain: the cause is named'

# --- non-adopted: SKIP, and the 59 pre-existing rows depend on this being the default ---
r="$tmproot/hook-unadopted"; mk "$r"
before="$(sha256_of "$r/live/settings.json")"
advance_payload "$r"; promote "$r"
OUT="$("$engine" --scope "$r/live" --before-sha "$before" 2>/dev/null)"; RC=$?
check_eq  "$RC" 0 'hook unadopted: rc=0'
check_has "$OUT" 'SKIP pre-push-installed' 'hook unadopted: the check skips, it does not pass'

# ---------- F. the config farm, sandboxed fixtures ----------
#
# The farm is the set of symlinks from $HOME/.claude into the production repo. Every row below
# redirects $HOME into the fixture, so the LIVE farm is never read and never touched — the
# installer honours $HOME, and that is the only thing standing between this suite and the 16
# links every session's configuration resolves through.
#
# The installer copied in is the REAL one: these rows grade the instrument production runs, not
# a stand-in that could disagree with it.

# farm_home DIR -> the sandbox $HOME this fixture's farm lives under.
farm_home() { printf '%s/home\n' "$1"; }

# mk_farm DIR -> mk(), plus what makes a farm real: install.sh and every member its declared
# FLOOR demands, tracked at the source root and promoted into live, then a sandbox $HOME whose
# .claude the installer has actually linked.
mk_farm() {
  local d="$1"
  local s="$d/src" l="$d/live" f rc
  mk "$d"
  cp "$here/../../install.sh" "$s/install.sh"
  chmod +x "$s/install.sh"
  for f in CLAUDE.md CONTRIBUTING.md STYLE.md templates.md workflows.md README.md LICENSE \
    ARCHITECTURE.md TESTING.md DELEGATING.md publication-model.md; do
    printf '# %s\n' "$f" > "$s/$f"
  done
  for f in agents skills scripts; do
    mkdir -p "$s/$f"
    printf 'x\n' > "$s/$f/keep.txt"
  done
  git -C "$s" add -A
  git -C "$s" commit -qm 'ship install.sh and the farm floor'
  git -C "$l" fetch -q "$s" dev
  git -C "$l" merge -q --ff-only FETCH_HEAD
  mkdir -p "$(farm_home "$d")"
  HOME="$(farm_home "$d")" "$l/install.sh" > "$d/install.log" 2>&1
  rc=$?
  # A fixture whose farm was never built would make every row below pass or fail for reasons
  # that have nothing to do with the check. Assert the setup, do not assume it.
  if [[ "$rc" -ne 0 ]]; then
    fail_line "farm fixture $(basename "$d"): the bare install failed (rc=$rc)"
    sed 's/^/    /' "$d/install.log"
  fi
}

# add_root_entry DIR NAME -> a src commit adding a NEW TRACKED ROOT ENTRY. This is the class the
# row exists for: directory members mean ordinary file content already flows through the
# existing links on a fast-forward, so the install does real work only when ROOT MEMBERSHIP
# changes.
add_root_entry() {
  printf '# %s\n' "$2" > "$1/src/$2"
  git -C "$1/src" add -- "$2"
  git -C "$1/src" commit -qm "add root entry $2"
}

# --- F1. clean farm: the row PASSES and names the tree the installer graded ---
r="$tmproot/farm-clean"; mk_farm "$r"
before="$(sha256_of "$r/live/settings.json")"
advance_payload "$r"; promote "$r"
OUT="$(HOME="$(farm_home "$r")" "$engine" --scope "$r/live" --before-sha "$before" 2>/dev/null)"; RC=$?
livephys="$(cd -P "$r/live" && pwd -P)"

check_eq  "$RC" 0 'farm clean: rc=0'
check_has "$OUT" 'PASS farm-current' 'farm clean: every current member links correctly'
check_has "$OUT" 'RESULT: PASS' 'farm clean: RESULT is PASS'
# The echo-back: the row states which tree the installer actually graded, not the one handed to
# it. Without this line a wrong-copy invocation is invisible on the passing path.
check_has "$OUT" "links to $livephys" 'farm clean: the graded tree is echoed back'
check_has "$OUT" 'outside what --check iterates' 'farm clean: the claim is stated one-directionally'
# Classification is attached to DRIFT lines only. A clean farm has nothing to classify, and a
# PASS that carried a label would mean the labelling had leaked out of the failure path.
check_lacks "$OUT" 'UNDETERMINED' 'farm clean: a passing row carries no classification'

# --- F2. synthetic drift: one managed link removed ---
# A row that cannot fail is not a check. This is the same fixture as F1 with one link gone.
r="$tmproot/farm-drift"; mk_farm "$r"
before="$(sha256_of "$r/live/settings.json")"
advance_payload "$r"; promote "$r"
rm -f "$(farm_home "$r")/.claude/workflows.md"
OUT="$(HOME="$(farm_home "$r")" "$engine" --scope "$r/live" --before-sha "$before" 2>/dev/null)"; RC=$?

check_eq  "$RC" 1 'farm drift: rc=1'
check_has "$OUT" 'FAIL farm-current' 'farm drift: the check FAILS'
check_has "$OUT" 'RESULT: FAIL' 'farm drift: RESULT is FAIL'
check_has "$OUT" 'out of date' 'farm drift: it is reported as drift, not as a refusal'
check_has "$OUT" 'DRIFT: workflows.md' "farm drift: the installer's own output names the member"
check_has "$OUT" 'run install.sh in' 'farm drift: the remedy is named'
# The REGRESSED cell: the `missing` shape over a name that WAS a root entry before. The promote
# did not add this path — a link that existed has gone.
check_has "$OUT" 'REGRESSED: workflows.md was already a root entry at' \
  'farm drift: a lost link on a pre-existing member is classified REGRESSED'
check_lacks "$OUT" 'EXPECTED: workflows.md' 'farm drift: it is not read as a newly added entry'

# --- F3. the ADDITION case: a NEW tracked root entry, which is the whole point ---
# The measured seven-week class. The fast-forward carries the new file into the repo; nothing
# links it into the config farm, so no session can reach it. The row must notice, and the
# installer must clear it.
r="$tmproot/farm-added"; mk_farm "$r"
before="$(sha256_of "$r/live/settings.json")"
add_root_entry "$r" 'newdoc.md'; promote "$r"
OUT="$(HOME="$(farm_home "$r")" "$engine" --scope "$r/live" --before-sha "$before" 2>/dev/null)"; RC=$?

check_eq  "$RC" 1 'farm added: rc=1'
check_has "$OUT" 'FAIL farm-current' 'farm added: a newly tracked root entry is caught'
check_has "$OUT" 'DRIFT: newdoc.md' 'farm added: the unlinked new member is named'
# The EXPECTED cell: the `missing` shape over a name that was NOT a root entry before. This is
# the ordinary case the farm refresh exists to fix, and saying so is what stops an operator
# treating the whole unit's reason for existing as an emergency.
check_has "$OUT" 'EXPECTED: newdoc.md was NOT a root entry at' \
  'farm added: an unlinked NEW member is classified EXPECTED'
check_lacks "$OUT" 'REGRESSED: newdoc.md' 'farm added: it is not read as a lost link'
# It is genuinely absent from the farm, not merely reported so.
check_lacks "$(ls "$(farm_home "$r")/.claude")" 'newdoc.md' 'farm added: the new member really is missing from the farm'

# ...and running the installer is what clears it. Without this the row could be failing for any
# reason at all and the fixture would not know.
HOME="$(farm_home "$r")" "$r/live/install.sh" > "$r/install2.log" 2>&1
OUT="$(HOME="$(farm_home "$r")" "$engine" --scope "$r/live" --before-sha "$before" 2>/dev/null)"; RC=$?
check_eq  "$RC" 0 'farm added, then installed: rc=0'
check_has "$OUT" 'PASS farm-current' 'farm added, then installed: the row clears'
check_has "$(ls "$(farm_home "$r")/.claude")" 'newdoc.md' 'farm added, then installed: the member is now linked'

# --- F4. refused BEFORE checking is not drift ---
# install.sh requires realpath, probes `mv -T`, and resolves $HOME — all ahead of flag parsing —
# so a pure report can die with ZERO `RESULT:` lines. Reproduced here with a $HOME that is not a
# directory. Reporting this as drift would diagnose a toolchain problem as a broken promote, and
# on such a machine every adopted promote would FAIL forever for the wrong reason.
r="$tmproot/farm-refused"; mk_farm "$r"
before="$(sha256_of "$r/live/settings.json")"
advance_payload "$r"; promote "$r"
printf 'not a directory\n' > "$tmproot/home-is-a-file"
OUT="$(HOME="$tmproot/home-is-a-file" "$engine" --scope "$r/live" --before-sha "$before" 2>/dev/null)"; RC=$?

check_eq    "$RC" 1 'farm refused: rc=1'
check_has   "$OUT" 'FAIL farm-current' 'farm refused: it FAILS rather than passing quietly'
check_has   "$OUT" 'no RESULT: line' 'farm refused: the absent verdict is what is reported'
check_has   "$OUT" 'refused BEFORE checking' 'farm refused: the cause is distinguished from drift'
check_lacks "$OUT" 'out of date' 'farm refused: it is NOT reported as drift'
check_has   "$OUT" 'refuse:' "farm refused: the installer's own refusal is attached"

# --- F5. a wrong-copy invocation, caught only by the echoed source= ---
# `--check` compares every managed link against ITS OWN SCRIPT_DIR, so a copy that is really
# another repo's installer answers truthfully about that other repo. Here the promoted scope
# ships install.sh as a symlink into a DIFFERENT repo whose farm is clean: the tool returns
# `RESULT: PASS`, rc=0, about a tree this promote never touched. Only comparing source= against
# the scope catches it — a row that trusted the path it handed over would report PASS.
r="$tmproot/farm-wrongcopy"; mk "$r"
before="$(sha256_of "$r/live/settings.json")"
advance_payload "$r"; promote "$r"
ln -s "$tmproot/farm-clean/live/install.sh" "$r/live/install.sh"
OUT="$(HOME="$(farm_home "$tmproot/farm-clean")" "$engine" --scope "$r/live" --before-sha "$before" 2>/dev/null)"; RC=$?

check_eq    "$RC" 1 'farm wrong copy: rc=1'
check_has   "$OUT" 'FAIL farm-current' 'farm wrong copy: a PASS about another tree does not clear the row'
check_has   "$OUT" 'not the promoted tree' 'farm wrong copy: the cause names both trees'
check_has   "$OUT" 'RESULT: PASS' "farm wrong copy: the installer's own verdict WAS a PASS"

# --- F6. no installer in the source: SKIP, not PASS and not FAIL ---
# Every pre-existing row in this file lands here, so this is also the reason their verdicts are
# unchanged by the addition of this check.
r="$tmproot/farm-none"; mk "$r"
before="$(sha256_of "$r/live/settings.json")"
advance_payload "$r"; promote "$r"
OUT="$("$engine" --scope "$r/live" --before-sha "$before" 2>/dev/null)"; RC=$?

check_eq    "$RC" 0 'farm none: rc=0'
check_has   "$OUT" 'SKIP farm-current' 'farm none: a source with no install.sh skips'
check_lacks "$OUT" 'PASS farm-current' 'farm none: skipping is not passing'
check_has   "$OUT" 'no config farm for this repo' 'farm none: the reason is named'

# --- F8. classification: did THIS promote cause the drift? ---
#
# The two `missing`-shape cells are covered in F2 (REGRESSED) and F3 (EXPECTED) above, on the
# fixtures that already produce them. What is left is the `resolved to` shape — a LIVE link
# pointing somewhere else — and every reason the classification cannot be made at all.
#
# The load-bearing row is the pair below it: ONE foreign link, one farm, one repo state, read
# twice with different --before-head values and coming back INTRODUCED then PRE-EXISTING. If
# varying the before-ref did not change the label, the classifier would not be reading it, and
# every other row here would pass over a constant.
#
# The verdict is asserted alongside every label: classification is diagnosis, so rc must stay 1
# and the row must stay FAIL no matter which label — UNDETERMINED included — comes back.

# drop_object REPO SHA -> delete a loose object so reading it fails. Asserted, not assumed: a
# PACKED object would leave the object readable and the row would silently test nothing.
drop_object() { # repo sha
  local f="$1/.git/objects/${2:0:2}/${2:2}"
  if [[ -f "$f" ]]; then
    rm -f "$f"
  else
    fail_line "fixture: $2 is not a loose object in $1 — the unreadable-object rows would test nothing"
  fi
}

r="$tmproot/farm-classify"; mk_farm "$r"
before="$(sha256_of "$r/live/settings.json")"
# B: install.sh and the floor are root entries; extra.md is NOT.
B="$(git -C "$r/live" rev-parse HEAD)"
add_root_entry "$r" 'extra.md'; promote "$r"
# C: extra.md IS a root entry. install.sh is byte-identical at B and C, so the range guard below
# cannot be what decides these two rows.
C="$(git -C "$r/live" rev-parse HEAD)"

# The foreign link: a LIVE symlink of the operator's own sitting where a managed link belongs.
# This is what makes --check emit the `resolved to` shape rather than `missing`.
mkdir -p "$tmproot/elsewhere"
printf 'an excursion of the operators own\n' > "$tmproot/elsewhere/extra.md"
ln -s "$tmproot/elsewhere/extra.md" "$(farm_home "$r")/.claude/extra.md"

OUT="$(HOME="$(farm_home "$r")" "$engine" --scope "$r/live" --before-sha "$before" \
  --before-head "$B" 2>/dev/null)"; RC=$?
check_eq    "$RC" 1 'farm introduced: rc=1'
check_has   "$OUT" 'FAIL farm-current' 'farm introduced: the row still FAILS'
check_has   "$OUT" 'DRIFT: extra.md (resolved to' 'farm introduced: the live-link shape is what fired'
check_has   "$OUT" 'INTRODUCED: extra.md was NOT a root entry at' \
  'farm introduced: a promote that made an occupied path managed is classified INTRODUCED'
check_lacks "$OUT" 'PRE-EXISTING' 'farm introduced: it is not read as a standing excursion of the operator'

OUT="$(HOME="$(farm_home "$r")" "$engine" --scope "$r/live" --before-sha "$before" \
  --before-head "$C" 2>/dev/null)"; RC=$?
check_eq    "$RC" 1 'farm pre-existing: rc=1 — the verdict does not move with the label'
check_has   "$OUT" 'FAIL farm-current' 'farm pre-existing: the row still FAILS'
check_has   "$OUT" 'PRE-EXISTING: extra.md was already a root entry at' \
  'farm pre-existing: the SAME foreign link classifies the other way on the other before-ref'
check_lacks "$OUT" 'INTRODUCED' 'farm pre-existing: the introduced label is gone'

# --- F8a. UNDETERMINED: install.sh itself moved in the range ---
# EXCLUDE lives inside install.sh, so if install.sh changed, a name's presence in the before-tree
# no longer settles whether it was a MEMBER then. The row is deliberately built on the state that
# would otherwise read PRE-EXISTING (before-head C, exactly as above): the guard must OVERRIDE a
# derivable label, not merely fill in where none exists.
printf '# a comment appended in the incoming range\n' >> "$r/src/install.sh"
git -C "$r/src" commit -qam 'change install.sh'
promote "$r"
D="$(git -C "$r/live" rev-parse HEAD)"
OUT="$(HOME="$(farm_home "$r")" "$engine" --scope "$r/live" --before-sha "$before" \
  --before-head "$C" 2>/dev/null)"; RC=$?
check_eq    "$RC" 1 'farm undetermined (installer moved): rc=1'
check_has   "$OUT" 'PASS range' 'farm undetermined (installer moved): the before-ref itself is sound'
check_has   "$OUT" 'UNDETERMINED: extra.md -- install.sh itself changed over' \
  'farm undetermined (installer moved): the EXCLUDE-may-have-moved case is named'
check_lacks "$OUT" 'PRE-EXISTING' \
  'farm undetermined (installer moved): the guard OVERRIDES the label this state would otherwise get'

# --- F8b. UNDETERMINED: no usable pre-merge HEAD ---
# The same value the range row already refuses to believe. A pre-merge HEAD this script will not
# trust there must not quietly decide labels here.
git -C "$r/live" update-ref -d ORIG_HEAD
OUT="$(HOME="$(farm_home "$r")" "$engine" --scope "$r/live" --before-sha "$before" 2>/dev/null)"; RC=$?
check_eq    "$RC" 1 'farm undetermined (no before-head): rc=1'
check_has   "$OUT" 'FAIL range' 'farm undetermined (no before-head): the range row reports it too'
check_has   "$OUT" 'UNDETERMINED: extra.md -- the pre-merge HEAD is unusable' \
  'farm undetermined (no before-head): an unresolvable ORIG_HEAD yields no label'
check_lacks "$OUT" 'PRE-EXISTING' 'farm undetermined (no before-head): nothing is guessed'
check_lacks "$OUT" 'INTRODUCED'  'farm undetermined (no before-head): nothing is guessed the other way'

# --- F8c. UNDETERMINED: a supplied pre-merge HEAD that is not an ancestor ---
# `commit-tree` builds a commit object off to one side without touching the working tree: it
# resolves and it is a commit, but it is not an ancestor of HEAD, so the range row refuses it.
SIDE="$(git -C "$r/live" commit-tree "$D^{tree}" -p "$D" -m 'a commit off to one side')"
OUT="$(HOME="$(farm_home "$r")" "$engine" --scope "$r/live" --before-sha "$before" \
  --before-head "$SIDE" 2>/dev/null)"; RC=$?
check_eq    "$RC" 1 'farm undetermined (non-ancestor): rc=1'
check_has   "$OUT" 'UNDETERMINED: extra.md -- the pre-merge HEAD is unusable' \
  'farm undetermined (non-ancestor): a before-head the range row refused decides nothing here'

# --- F8d. UNDETERMINED: the install.sh diff cannot be computed ---
# A commit whose TREE object is gone: it resolves, so --ref accepts it, but no diff against it
# can be produced. The before-tree is fine here, so only the diff branch can be what fires.
BLOB="$(printf 'later\n' | git -C "$r/live" hash-object -w --stdin)"
RTREE="$(printf '100644 blob %s\tlater.txt\n' "$BLOB" | git -C "$r/live" mktree)"
REFC="$(git -C "$r/live" commit-tree "$RTREE" -p "$D" -m 'a ref-only commit')"
git -C "$r/live" update-ref refs/heads/refonly "$REFC"
drop_object "$r/live" "$RTREE"
OUT="$(HOME="$(farm_home "$r")" "$engine" --scope "$r/live" --before-sha "$before" \
  --before-head "$D" --ref refonly 2>/dev/null)"; RC=$?
check_eq    "$RC" 1 'farm undetermined (diff unreadable): rc=1'
check_has   "$OUT" 'UNDETERMINED: extra.md -- whether install.sh changed over' \
  'farm undetermined (diff unreadable): an uncomputable range check yields no label'
check_lacks "$OUT" 'PRE-EXISTING' 'farm undetermined (diff unreadable): nothing is guessed'

# --- F8e. UNDETERMINED: the before-commit's tree cannot be listed ---
# A commit object can be readable while its tree is not — a partial clone with no network, or a
# damaged object store. rev-parse and merge-base both still answer, so nothing upstream notices.
drop_object "$r/live" "$(git -C "$r/live" rev-parse "$B^{tree}")"
OUT="$(HOME="$(farm_home "$r")" "$engine" --scope "$r/live" --before-sha "$before" \
  --before-head "$B" 2>/dev/null)"; RC=$?
check_eq    "$RC" 1 'farm undetermined (unreadable before-tree): rc=1'
check_has   "$OUT" 'UNDETERMINED: extra.md -- git ls-tree could not list the root of' \
  'farm undetermined (unreadable before-tree): an unlistable before-tree yields no label'
check_lacks "$OUT" 'INTRODUCED' \
  'farm undetermined (unreadable before-tree): the label this before-ref would otherwise give is withheld'

# --- F8f. two properties of the labeller no fixture can reach ---
# Sourced in a SUBSHELL: the engine's `if [[ "${BASH_SOURCE[0]}" == "$0" ]]` guard keeps main()
# from running, and the subshell keeps its readonly globals out of this file's scope.
#
# A THIRD DRIFT shape. install.sh emits two today, so no fixture can produce this — but which
# cell a line belongs to DEPENDS on its shape, so an unrecognised one must not be sorted into a
# cell by falling through. The name here IS in the before-names, so a fall-through would produce
# a confident REGRESSED.
# shellcheck source=/dev/null  # $engine is resolved at run time from $here
third_shape="$( ( . "$engine"; \
  farm_drift_label 'DRIFT: odd.md (some third shape)' 'odd.md' '' 0123456789abcdef ) 2>&1 )"
check_has   "$third_shape" 'UNDETERMINED: odd.md' 'labeller: an unrecognised DRIFT shape yields no cell'
check_lacks "$third_shape" 'REGRESSED' 'labeller: it does not fall through into a cell'

# Membership is a FIXED-STRING match. A member name carries dots, so a regex comparison would
# let `a.b.md` match the unrelated entry `axb.md` and report a brand-new member as REGRESSED —
# telling the operator a link had been lost when the promote had simply added the path.
# shellcheck source=/dev/null  # same
dotty="$( ( . "$engine"; \
  farm_drift_label 'DRIFT: a.b.md (missing or not a live symlink at /nowhere)' \
    'axb.md' '' 0123456789abcdef ) 2>&1 )"
check_has   "$dotty" 'EXPECTED: a.b.md' 'labeller: membership matches literally, not as a regex'
check_lacks "$dotty" 'REGRESSED' 'labeller: a dotted name does not match an unrelated entry'

# --- F7. the live farm was never the subject ---
# Every row above redirects $HOME. This asserts the suite's own precondition rather than
# trusting it: the operator's real config root must be untouched by this file.
check_eq "$(farm_symlink_state "$REAL_HOME")" "$live_farm_before" \
  'live farm: every symlink under the real ~/.claude is exactly as this suite found it'

# ---------- summary ----------
# Printed BEFORE the verdict is computed, so its absence is itself the signal that this
# run died rather than passed.
printf '\nRESULT: %s passed, %s failed\n' "$pass" "$fail"
if [[ "$fail" -eq 0 ]]; then exit 0; else exit 1; fi

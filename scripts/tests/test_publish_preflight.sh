#!/usr/bin/env bash
set -uo pipefail

# Script: test_publish_preflight.sh
# Purpose: Regression tests for scripts/publish-preflight.sh — the read-only start-invariant
#          for /propagate's adopted publish path. Covers the fetch-as-abort-point rule (the
#          measured defect: a dead fetch compared against a STALE cached origin/main and
#          printed PASS), each watermark assertion, the CHANGELOG.md exclusion, the auth
#          classifier, and the terminal RESULT line's allowlist semantics.
# Usage:   ./scripts/tests/test_publish_preflight.sh
#
# Fixtures model the real publication topology: `main` is a DIVORCED orphan whose tip tree
# equals some `dev` commit's tree modulo CHANGELOG.md, and `refs/published/main` (the
# watermark) points at that `dev` commit. Sharing no ancestry is the point — a fixture where
# main descends from dev would not exercise the same predicates.
#
# The sandbox root is pinned with `pwd -P`: $TMPDIR is a symlink on macOS (/tmp ->
# /private/tmp) and a logical path does not physically contain its files, which silently
# routes path-resolving tools down a different branch than the one under test.

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
engine="$here/../../scripts/publish-preflight.sh"
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

# ---------- fixture ----------

# make_repo DIR -> a clean, publishable adopted repo:
#   dev:  d1 -> d2 -> d3      (d2 is the watermark; d3 is unpublished work)
#   main: m1 (orphan; tree == d2's tree + CHANGELOG.md), pushed to origin
mk() {
  local d="$1"
  mkdir -p "$d"
  git init -q -b dev "$d"
  git -C "$d" config user.email test@test.invalid
  git -C "$d" config user.name test
  git -C "$d" config commit.gpgsign false
  git -C "$d" config tag.gpgsign false

  printf 'production = "dev"\n' > "$d/.publication.toml"
  printf 'v1\n' > "$d/payload.txt"
  git -C "$d" add -A
  git -C "$d" commit -qm 'brick one'

  printf 'v2\n' > "$d/payload.txt"
  git -C "$d" add -A
  git -C "$d" commit -qm 'brick two'
  local wm
  wm="$(git -C "$d" rev-parse HEAD)"

  printf 'v3\n' > "$d/payload.txt"
  git -C "$d" add -A
  git -C "$d" commit -qm 'brick three (unpublished)'

  # main: orphan carrying d2's tree plus a CHANGELOG.md that dev never gets.
  git -C "$d" checkout -q --orphan main
  git -C "$d" rm -rq --cached . 2>/dev/null || true
  git -C "$d" checkout -q "$wm" -- .
  printf '## v0.0.2 — 2026-01-01\n- brick two\n' > "$d/CHANGELOG.md"
  git -C "$d" add -A
  git -C "$d" commit -qm 'published through brick two'
  git -C "$d" checkout -q dev

  git -C "$d" update-ref refs/published/main "$wm"

  git init -q --bare "$d.origin.git"
  git -C "$d" remote add origin "$d.origin.git"
  git -C "$d" push -q origin main
  git -C "$d" fetch -q origin main 2>/dev/null

  printf '%s' "$wm"
}

# run SCOPE [args...] -> sets OUT (stdout only) / ERR / RC
OUT=""; RC=0
run() {
  local scope="$1"; shift
  OUT="$("$engine" --scope "$scope" "$@" 2>/dev/null)"
  RC=$?
}

last_line() { printf '%s' "$1" | tail -1; }

# ---------- 1. happy path ----------

r="$tmproot/happy"
mk "$r" >/dev/null
run "$r"
check_eq "$RC" 0 'happy: rc=0'
check_has "$OUT" 'RESULT: PASS rc=0' 'happy: RESULT is PASS'
check_eq "$(last_line "$OUT" | cut -d' ' -f1-3)" 'RESULT: PASS rc=0' 'happy: RESULT is the LAST line'
check_has "$OUT" 'PASS fetch' 'happy: fetch passed'
check_has "$OUT" 'PASS main-sync' 'happy: main-sync passed'
check_has "$OUT" 'PASS watermark-present' 'happy: watermark-present passed'
check_has "$OUT" 'PASS watermark-integrity' 'happy: watermark-integrity passed'
check_has "$OUT" 'PASS watermark-ancestor' 'happy: watermark-ancestor passed'
check_has "$OUT" 'PASS unpublished-work' 'happy: unpublished-work passed'
check_has "$OUT" 'PASS tree-clean' 'happy: tree-clean passed'

# The CHANGELOG.md that only main carries must NOT trip the integrity predicate.
check_lacks "$OUT" 'FAIL watermark-integrity' 'happy: CHANGELOG.md alone does not trip integrity'

# ---------- 2. THE REGRESSION: a dead fetch must abort, not compare stale cache ----------
# Local main still EQUALS the cached origin/main, so a naive implementation prints
# `PASS main-sync` and a green RESULT while never having reached the remote at all.

r="$tmproot/deadfetch"
mk "$r" >/dev/null
git -C "$r" remote set-url origin "$tmproot/definitely-not-a-repo"
run "$r"
check_eq "$RC" 1 'dead fetch: rc=1'
check_has "$OUT" 'FAIL fetch' 'dead fetch: fetch is reported FAIL'
check_has "$OUT" 'RESULT: FAIL rc=1' 'dead fetch: RESULT is FAIL'
check_lacks "$OUT" 'PASS main-sync' 'dead fetch: main-sync must NOT pass on stale cache'
check_lacks "$OUT" 'RESULT: PASS' 'dead fetch: no PASS verdict anywhere'
check_has "$OUT" 'SKIP main-sync' 'dead fetch: main-sync is visibly SKIPPED'
check_has "$OUT" 'STALE cached' 'dead fetch: the skip reason names the stale-cache hazard'
check_has "$OUT" 'SKIP watermark-present' 'dead fetch: watermark checks skipped too'

# ---------- 3. main AHEAD of origin (half-finished publish) ----------

r="$tmproot/ahead"
mk "$r" >/dev/null
git -C "$r" checkout -q main
printf '## v0.0.3 — 2026-01-02\n- brick three\n' >> "$r/CHANGELOG.md"
git -C "$r" add -A
git -C "$r" commit -qm 'published through brick three'
git -C "$r" tag -a v0.0.3 -m 'brick three'
git -C "$r" checkout -q dev
run "$r"
check_eq "$RC" 1 'ahead: rc=1'
check_has "$OUT" 'FAIL main-sync' 'ahead: main-sync fails'
check_has "$OUT" 'ahead' 'ahead: direction is named'
check_has "$OUT" 'tag -d' 'ahead: recovery names deleting unpushed tags'
# Recovery is PRINTED, never performed — this script is read-only.
check_eq "$(git -C "$r" tag -l v0.0.3)" 'v0.0.3' 'ahead: the tag was NOT deleted (read-only)'
check_eq "$(git -C "$r" rev-list --count origin/main..main)" '1' 'ahead: main was NOT reset (read-only)'

# ---------- 4. main BEHIND origin ----------

r="$tmproot/behind"
mk "$r" >/dev/null
# Advance origin/main from a second clone so the local main falls behind.
git clone -q "$r.origin.git" "$r.other"
git -C "$r.other" config user.email test@test.invalid
git -C "$r.other" config user.name test
git -C "$r.other" config commit.gpgsign false
printf 'extra\n' >> "$r.other/CHANGELOG.md"
git -C "$r.other" add -A
git -C "$r.other" commit -qm 'someone else published'
git -C "$r.other" push -q origin main
run "$r"
check_eq "$RC" 1 'behind: rc=1'
check_has "$OUT" 'FAIL main-sync' 'behind: main-sync fails'
check_has "$OUT" 'behind' 'behind: direction is named'

# ---------- 5. watermark absent ----------

r="$tmproot/nowm"
mk "$r" >/dev/null
git -C "$r" update-ref -d refs/published/main
run "$r"
check_eq "$RC" 1 'no watermark: rc=1'
check_has "$OUT" 'FAIL watermark-present' 'no watermark: reported'
check_has "$OUT" 'cutover' 'no watermark: names --cutover as the only bypass'
check_has "$OUT" 'SKIP watermark-integrity' 'no watermark: dependent checks skipped'

# ---------- 6. watermark stranded BEHIND a succeeded push ----------
# main == origin/main (so the sync check passes) but main's tree no longer matches the
# watermark — a publish that pushed and then died before advancing the watermark.

r="$tmproot/stranded"
wm="$(mk "$r")"
git -C "$r" checkout -q main
printf 'v3\n' > "$r/payload.txt"
git -C "$r" add -A
git -C "$r" commit -qm 'published through brick three'
git -C "$r" push -q origin main
git -C "$r" checkout -q dev
run "$r"
check_eq "$RC" 1 'stranded: rc=1'
check_has "$OUT" 'PASS main-sync' 'stranded: main-sync still passes (that is the trap)'
check_has "$OUT" 'FAIL watermark-integrity' 'stranded: integrity catches it'
check_has "$OUT" 'update-ref' 'stranded: recovery names advancing the watermark'
check_eq "$(git -C "$r" rev-parse refs/published/main)" "$wm" 'stranded: watermark NOT advanced (read-only)'

# ---------- 7. watermark stranded by a dev rewrite (not an ancestor) ----------

r="$tmproot/rebased"
wm="$(mk "$r")"
git -C "$r" checkout -q dev
# Rewrite dev THROUGH the watermark commit — amending only the tip would leave the
# watermark an ancestor still, and the case would pass for free without reaching the bug.
git -C "$r" reset -q --hard "$(git -C "$r" rev-list --max-parents=0 dev)"
printf 'v2-rewritten\n' > "$r/payload.txt"
git -C "$r" add -A
git -C "$r" commit -qm 'brick two (rewritten, strands the watermark)'
if git -C "$r" merge-base --is-ancestor "$wm" dev 2>/dev/null; then
  fail_line 'rebased: FIXTURE BROKEN — watermark is still an ancestor of dev'
fi
run "$r"
check_eq "$RC" 1 'rebased: rc=1'
check_has "$OUT" 'FAIL watermark-ancestor' 'rebased: ancestry failure caught'

# ---------- 8. nothing to publish ----------

r="$tmproot/nothing"
mk "$r" >/dev/null
git -C "$r" update-ref refs/published/main "$(git -C "$r" rev-parse dev)"
run "$r"
check_eq "$RC" 1 'nothing: rc=1'
check_has "$OUT" 'FAIL unpublished-work' 'nothing: reported'
# A watermark at dev's tip no longer matches main's tree, so integrity fails too; the
# point of this case is only that an empty publish range is itself refused.

# ---------- 9. dirty working tree ----------

r="$tmproot/dirty"
mk "$r" >/dev/null
printf 'uncommitted\n' >> "$r/payload.txt"
run "$r"
check_eq "$RC" 1 'dirty: rc=1'
check_has "$OUT" 'FAIL tree-clean' 'dirty: reported'

# ---------- 10. usage errors -> ERROR, never an empty or clean summary ----------

run "$tmproot/does-not-exist"
check_eq "$RC" 2 'missing scope: rc=2'
check_has "$OUT" 'RESULT: ERROR rc=2' 'missing scope: RESULT is ERROR'

plain="$tmproot/plain"
mkdir -p "$plain"
git init -q "$plain"
run "$plain"
check_eq "$RC" 2 'non-adopted repo: rc=2'
check_has "$OUT" 'RESULT: ERROR rc=2' 'non-adopted repo: RESULT is ERROR'
check_has "$OUT" 'publication.toml' 'non-adopted repo: names the missing marker'

OUT="$("$engine" --bogus-flag 2>/dev/null)"; RC=$?
check_eq "$RC" 2 'unknown flag: rc=2'
check_has "$OUT" 'RESULT: ERROR rc=2' 'unknown flag: RESULT is ERROR'

OUT="$("$engine" --scope 2>/dev/null)"; RC=$?
check_eq "$RC" 2 'scope without value: rc=2'

# ---------- 11. the auth classifier ----------
# Exercised through the engine's own function so BOTH branches are covered: a live
# successful handshake cannot be manufactured offline, and the failure branch below is
# the exact text measured from a refusing smartcard agent.

classify() { # rc output -> prints pass|fail
  # Sourcing defines the functions without running the sweep: the engine guards its
  # entry point on [[ "${BASH_SOURCE[0]}" == "$0" ]], the same way audit.sh does.
  # shellcheck source=/dev/null  # $engine is resolved at runtime from $here
  ( . "$engine"; auth_classify "$1" "$2" )
}
check_eq "$(classify 1 "Hi wohlford! You've successfully authenticated, but GitHub does not provide shell access.")" \
  'pass' 'auth: GitHub greeting classifies as pass (note rc=1, not 0)'
check_eq "$(classify 255 'sign_and_send_pubkey: signing failed for RSA "cardno:11 584 265" from agent: agent refused operation
git@github.com: Permission denied (publickey).')" \
  'fail' 'auth: measured smartcard refusal classifies as fail'
check_eq "$(classify 0 '')" 'fail' 'auth: empty output is fail (allowlist, not blocklist)'
check_eq "$(classify 0 'everything is fine, trust me')" 'fail' 'auth: unrecognised success text is fail'

# ---------- 12. auth SKIPs when the remote is not GitHub over SSH ----------
# The check derives its target from the remote URL rather than hardcoding a host, so the
# file:// fixtures above must never have attempted a network handshake.

r="$tmproot/happy"
run "$r"
check_has "$OUT" 'SKIP auth' 'auth: skipped for a non-ssh remote'

# ---------- 13. a killed run must never read as clean ----------
# A fake `ssh` that sleeps holds the engine inside its auth probe, so the kill lands
# mid-run without a network and without depending on git transport internals. (`ext::`
# was tried first and is unusable: protocol.ext.allow defaults to `never`, so git
# rejects it in 0s — a fixture that never reaches the hang it exists to create.)
# Note bash defers a trap until the foreground child returns, so the engine emits its
# verdict when the fake ssh exits, not at the instant of the signal.

r="$tmproot/hang"
mk "$r" >/dev/null
git -C "$r" remote set-url origin 'git@github.com:wohlford/fixture.git'
mkdir -p "$tmproot/fakebin"
printf '#!/bin/sh\nsleep 4\n' > "$tmproot/fakebin/ssh"
chmod +x "$tmproot/fakebin/ssh"
PATH="$tmproot/fakebin:$PATH" "$engine" --scope "$r" > "$tmproot/hang.out" 2>/dev/null &
enginepid=$!
sleep 1
kill -TERM "$enginepid" 2>/dev/null
wait "$enginepid" 2>/dev/null
killed_rc=$?
killed_out="$(cat "$tmproot/hang.out")"
check_lacks "$killed_out" 'RESULT: PASS' 'killed: never prints a PASS verdict'
check_has "$killed_out" 'RESULT: INCOMPLETE' 'killed: prints INCOMPLETE'
check_eq "$killed_rc" 143 'killed: exits 143 (SIGTERM)'

# ---------- 14. an auth FAILURE is only evidence when a PIN prompt could have reached someone ----------
# Measured 2026-07-28: in a shell with no TTY, gpg-agent cannot deliver a card PIN prompt and
# refuses to sign — "agent refused operation", rc=255, indistinguishable from a genuinely bad
# credential. Reporting that as FAIL states a conclusion the probe never established. A SUCCESS
# is trustworthy either way; only a failure is ambiguous, and only when unpromptable.

verdict() { # classify promptable -> pass|fail|skip
  # shellcheck source=/dev/null  # $engine is resolved at runtime from $here
  ( . "$engine"; auth_verdict "$1" "$2" )
}
check_eq "$(verdict pass yes)" 'pass' 'auth verdict: success + promptable -> pass'
check_eq "$(verdict pass no)"  'pass' 'auth verdict: success is trustworthy even unpromptable'
check_eq "$(verdict fail yes)" 'fail' 'auth verdict: failure + promptable -> fail (a real verdict)'
check_eq "$(verdict fail no)"  'skip' 'auth verdict: failure + unpromptable -> skip, NOT fail'

# ---------- 15. end-to-end: an unpromptable shell must not manufacture a FAIL ----------
# This suite itself runs without a TTY, so the engine takes the unpromptable path for real.

mkfake() { # dir body
  mkdir -p "$1"
  printf '%s\n' "$2" > "$1/ssh"
  chmod +x "$1/ssh"
}

r="$tmproot/nopin"
mk "$r" >/dev/null
git -C "$r" remote set-url origin 'git@github.com:wohlford/fixture.git'
mkfake "$tmproot/fake-refuse" '#!/bin/sh
echo "sign_and_send_pubkey: signing failed for RSA \"cardno:11 584 265\" from agent: agent refused operation" >&2
echo "git@github.com: Permission denied (publickey)." >&2
exit 255'
OUT="$(PATH="$tmproot/fake-refuse:$PATH" "$engine" --scope "$r" 2>/dev/null)"; RC=$?
check_has  "$OUT" 'SKIP auth' 'unpromptable: auth is SKIPPED, not failed'
check_lacks "$OUT" 'FAIL auth' 'unpromptable: auth must NOT be reported as a failure'
check_has  "$OUT" 'prompt' 'unpromptable: the reason names the missing prompt'
# The SKIP must not rescue the run — the fetch is still the hard gate.
check_has  "$OUT" 'FAIL fetch' 'unpromptable: the fetch still fails closed'
check_has  "$OUT" 'RESULT: FAIL rc=1' 'unpromptable: the run still FAILS overall'
check_eq   "$RC" 1 'unpromptable: rc=1'

# ---------- 16. a successful handshake still PASSES in that same shell ----------

r="$tmproot/nopin-ok"
mk "$r" >/dev/null
git -C "$r" remote set-url origin 'git@github.com:wohlford/fixture.git'
mkfake "$tmproot/fake-ok" '#!/bin/sh
echo "Hi wohlford! You have successfully authenticated, but GitHub does not provide shell access."
exit 1'
OUT="$(PATH="$tmproot/fake-ok:$PATH" "$engine" --scope "$r" 2>/dev/null)"
check_has   "$OUT" 'PASS auth' 'unpromptable but authenticated: auth PASSES'
check_lacks "$OUT" 'SKIP auth' 'unpromptable but authenticated: not skipped'

# ---------- summary ----------
# Printed BEFORE the verdict is computed, so its absence is itself the signal that this
# run died rather than passed.
printf '\nRESULT: %s passed, %s failed\n' "$pass" "$fail"
if [[ "$fail" -eq 0 ]]; then exit 0; else exit 1; fi

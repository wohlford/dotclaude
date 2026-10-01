#!/usr/bin/env bash
set -uo pipefail

# Script: test_publish_postflight.sh
# Purpose: Regression tests for scripts/publish-postflight.sh — the read-only END-invariant for /propagate's adopted publish
# Usage:   ./scripts/tests/test_publish_postflight.sh
#
# Covers the allowlist leak check on origin's refs, the live-read abort point (a dead origin over
# a fresh cache must FAIL, never pass from cache), whole-set tag parity (equal counts are not
# equal sets; a retargeted tag object differs), the non-empty floor, each watermark assertion,
# the read-only guarantee, and the terminal RESULT line's allowlist semantics.
#
# Fixtures model the END state of a publish: `main` is a DIVORCED orphan (m1 -> m2) whose tip
# tree equals dev's tip tree plus a CHANGELOG.md, the watermark `refs/published/main` equals
# dev's tip, and a bare origin holds exactly main and the tags. The bare origin is populated
# ONLY by `git -C <bare> fetch <repo> <refspecs>` — no `git push` anywhere, so no push guard is
# engaged.
#
# Three tags exist on both sides: v0.0.1 (m1), v0.0.2 (m2) and V0.0.3 (m1). The uppercase
# initial collates differently under LC_ALL=C and en_US, so a sort that drops LC_ALL=C on one
# side mis-diffs even the CLEAN row.
#
# The sandbox root is pinned with `pwd -P`: $TMPDIR is a symlink on macOS (/tmp ->
# /private/tmp) and a logical path does not physically contain its files.
#
# Every wait is bounded (10 s). Signing is disabled per repo with `git config`; no GIT_CONFIG_*
# variable is exported.

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
engine="$here/../publish-postflight.sh"
tmproot="$(cd "$(mktemp -d)" && pwd -P)"
[[ -d "$tmproot" ]] || exit 2
trap 'rm -rf "$tmproot"' EXIT

pass=0
fail=0
pass_line() { printf 'PASS  %s\n' "$1"; pass=$((pass + 1)); }
fail_line() { printf 'FAIL  %s\n' "$1"; fail=$((fail + 1)); }

check_eq() { # got want label
  if [[ "$1" == "$2" ]]; then pass_line "$3"; else fail_line "$3 (want [$2] got [$1])"; fi
}

check_has() { # haystack needle label
  if grep -qF -- "$2" <<<"$1"; then
    pass_line "$3"
  else
    fail_line "$3 (missing [$2])"
  fi
}

check_lacks() { # haystack needle label
  if grep -qF -- "$2" <<<"$1"; then
    fail_line "$3 (unexpectedly present: [$2])"
  else
    pass_line "$3"
  fi
}

# ---------- fixture ----------

# mk DIR -> the clean END state of a publish:
#   dev:  d1 -> d2            (d2 is the watermark and dev's tip)
#   main: m1 -> m2            (orphan; m2 tree == d2 tree + CHANGELOG.md)
#   tags: v0.0.1 (m1), v0.0.2 (m2), V0.0.3 (m1), all annotated, on both sides
#   origin: bare $DIR.origin.git holding refs/heads/main + the tags, filled by fetch
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
  local d1
  d1="$(git -C "$d" rev-parse HEAD)"

  printf 'v2\n' > "$d/payload.txt"
  git -C "$d" add -A
  git -C "$d" commit -qm 'brick two'
  local d2
  d2="$(git -C "$d" rev-parse HEAD)"

  git -C "$d" checkout -q --orphan main
  git -C "$d" rm -rq --cached . 2>/dev/null || true
  git -C "$d" checkout -q "$d1" -- .
  printf '## v0.0.1\n- brick one\n' > "$d/CHANGELOG.md"
  git -C "$d" add -A
  git -C "$d" commit -qm 'published through brick one'
  local m1
  m1="$(git -C "$d" rev-parse HEAD)"
  git -C "$d" tag -a v0.0.1 -m 'v0.0.1' "$m1"
  git -C "$d" tag -a V0.0.3 -m 'V0.0.3' "$m1"

  git -C "$d" checkout -q "$d2" -- .
  printf '## v0.0.2\n- brick two\n' > "$d/CHANGELOG.md"
  git -C "$d" add -A
  git -C "$d" commit -qm 'published through brick two'
  local m2
  m2="$(git -C "$d" rev-parse HEAD)"
  git -C "$d" tag -a v0.0.2 -m 'v0.0.2' "$m2"

  git -C "$d" checkout -q dev
  git -C "$d" update-ref refs/published/main "$d2"

  git init -q --bare -b main "$d.origin.git"
  git -C "$d.origin.git" fetch -q "$d" 'refs/heads/main:refs/heads/main' 'refs/tags/*:refs/tags/*'
  git -C "$d" remote add origin "$d.origin.git"
  git -C "$d" fetch -q origin
}

# fixture NAME -> sets r (repo) and b (bare origin), builds a fresh fixture
fixture() {
  r="$tmproot/$1"
  b="$r.origin.git"
  mk "$r"
}

# put_bare REFSPEC... -> fetch from the repo INTO the bare (never a push); --no-tags stops tag
# auto-follow from silently restoring a tag the row just deleted from the bare
put_bare() { git -C "$b" fetch -q --no-tags "$r" "$@"; }

# run SCOPE [args...] -> sets OUT (stdout only) / RC. LC_ALL is set per call (never exported) to a
# locale whose collation differs from C, so an engine sort that drops its own LC_ALL=C mis-diffs.
OUT=""; RC=0
run() {
  local scope="$1"; shift
  OUT="$(LC_ALL=en_US.UTF-8 "$engine" --scope "$scope" "$@" 2>/dev/null)"
  RC=$?
}

last_line() { printf '%s' "$1" | tail -1; }
result_prefix() { last_line "$1" | cut -d' ' -f1-3; }

# ---------- 0. locale fixture sanity ----------
# The fixture's three tag names must sort DIFFERENTLY under C and en_US.UTF-8, else the locale
# pinning in the engine is unobservable here.
tagnames=$'v0.0.1\nv0.0.2\nV0.0.3'
locales="$(locale -a 2>/dev/null)"
if grep -qixF 'en_US.UTF-8' <<<"$locales"; then
  sort_c="$(LC_ALL=C sort <<<"$tagnames")"
  sort_us="$(LC_ALL=en_US.UTF-8 sort <<<"$tagnames")"
  if [[ "$sort_c" != "$sort_us" ]]; then
    pass_line 'locale: FIXTURE — the tag names sort differently under C and en_US.UTF-8'
  else
    fail_line 'locale: FIXTURE — the tag names sort identically under C and en_US.UTF-8'
  fi
else
  printf 'NOTE: en_US.UTF-8 is unavailable here, so the LC_ALL=C mutant (dropping it from the sort) is unkillable on this machine\n'
fi

# ---------- 1. clean ----------

fixture clean
run "$r"
check_eq "$RC" 0 'clean: rc=0'
check_eq "$(last_line "$OUT")" 'RESULT: PASS rc=0 checks=9/0/0' 'clean: RESULT is the LAST line, 9/0/0'
for n in origin-url remote-read remote-refs main-sync tags-parity watermark-present \
  watermark-tree watermark-ancestor watermark-current; do
  check_has "$OUT" "PASS $n" "clean: $n passed"
done

# ---------- 2. dev pushed ----------

fixture devpushed
put_bare 'refs/heads/dev:refs/heads/dev'
run "$r"
check_eq "$RC" 1 'dev pushed: rc=1'
check_has "$OUT" 'FAIL remote-refs' 'dev pushed: remote-refs fails'
check_has "$OUT" 'unexpected ref: refs/heads/dev' 'dev pushed: names refs/heads/dev'

# ---------- 3. the watermark pushed ----------

fixture wmpushed
put_bare 'refs/published/main:refs/published/main'
run "$r"
check_eq "$RC" 1 'watermark pushed: rc=1'
check_has "$OUT" 'FAIL remote-refs' 'watermark pushed: remote-refs fails'
check_has "$OUT" 'unexpected ref: refs/published/main' 'watermark pushed: names it'

# ---------- 3b. refs/heads/main2 (exact match, never a glob) ----------

fixture main2
put_bare 'refs/heads/main:refs/heads/main2'
run "$r"
check_eq "$RC" 1 'main2: rc=1'
check_has "$OUT" 'FAIL remote-refs' 'main2: remote-refs fails (a glob spelling admits it)'
check_has "$OUT" 'unexpected ref: refs/heads/main2' 'main2: names refs/heads/main2'

# ---------- 3c. origin holds tags but no refs/heads/main ----------

fixture nomainorigin
git -C "$b" update-ref -d refs/heads/main
run "$r"
check_eq "$RC" 1 'origin without main: rc=1'
check_has "$OUT" 'FAIL main-sync' 'origin without main: main-sync fails on the empty origin SHA'
check_lacks "$OUT" 'PASS main-sync' 'origin without main: never passes main-sync'

# ---------- 4. an unlisted namespace (allowlist, not blocklist) ----------
# refs/pull/1/head contains neither "dev" nor "published": a blocklist of those admits it.

fixture pullref
put_bare 'refs/heads/dev:refs/pull/1/head'
run "$r"
check_eq "$RC" 1 'pull ref: rc=1'
check_has "$OUT" 'FAIL remote-refs' 'pull ref: remote-refs fails'
check_has "$OUT" 'unexpected ref: refs/pull/1/head' 'pull ref: names refs/pull/1/head'

# ---------- 5. origin main behind local ----------

fixture behind
git -C "$b" update-ref refs/heads/main "$(git -C "$r" rev-parse 'v0.0.1^{commit}')"
run "$r"
check_eq "$RC" 1 'origin behind: rc=1'
check_has "$OUT" 'FAIL main-sync' 'origin behind: main-sync fails'

# ---------- 6. dev-only tag on origin ----------

fixture devtag
git -C "$r" tag -a devtag -m devtag "$(git -C "$r" rev-parse dev)"
put_bare 'refs/tags/devtag:refs/tags/devtag'
git -C "$r" tag -d devtag >/dev/null
run "$r"
check_eq "$RC" 1 'dev-only tag: rc=1'
check_has "$OUT" 'FAIL tags-parity' 'dev-only tag: tags-parity fails'
check_has "$OUT" 'only on origin: devtag' 'dev-only tag: names devtag as origin-only'
check_has "$OUT" 'PASS remote-refs' 'dev-only tag: remote-refs still passes (tags are allowed; parity moves)'

# ---------- 7. local-only tag ----------
# Named v0.0.4, not v0.0.3: a tag spelled v0.0.3 would share a loose-ref path with the fixture's
# V0.0.3 on a case-insensitive filesystem (macOS APFS).

fixture localtag
git -C "$r" tag -a v0.0.4 -m 'v0.0.4' "$(git -C "$r" rev-parse 'v0.0.2^{commit}')"
run "$r"
check_eq "$RC" 1 'local-only tag: rc=1'
check_has "$OUT" 'FAIL tags-parity' 'local-only tag: tags-parity fails'
check_has "$OUT" 'only local: v0.0.4' 'local-only tag: names v0.0.4 as local-only'

# ---------- 7b. local tag on a commit main does not contain ----------
# A tag on a dev-only commit, present locally only, is not a published tag: the local listing is
# restricted to tags merged into main. Kills a mutant that drops `--merged refs/heads/main`.

fixture devonlylocal
git -C "$r" tag -a devlocal -m devlocal "$(git -C "$r" rev-parse dev)"
run "$r"
check_eq "$RC" 0 'dev-only local tag: rc=0'
check_eq "$(last_line "$OUT")" 'RESULT: PASS rc=0 checks=9/0/0' 'dev-only local tag: still 9/0/0'
check_has "$OUT" 'PASS tags-parity' 'dev-only local tag: tags-parity passes (unmerged tags are not compared)'

# ---------- 7c. a tag literally named main ----------
# On BOTH sides, so tags-parity stays equal. An unqualified `rev-parse main` resolves the TAG
# first; the engine must spell refs/heads/main.

fixture tagmain
git -C "$r" tag -a main -m 'a tag named main' "$(git -C "$r" rev-parse 'v0.0.1^{commit}')" 2>/dev/null
put_bare 'refs/tags/main:refs/tags/main'
check_eq "$(git -C "$b" rev-parse refs/tags/main)" "$(git -C "$r" rev-parse refs/tags/main)" \
  'tag named main: FIXTURE — the tag object is on both sides'
check_eq "$([[ "$(git -C "$r" rev-parse main 2>/dev/null)" != "$(git -C "$r" rev-parse refs/heads/main)" ]] && echo differs)" differs \
  'tag named main: FIXTURE — the unqualified name resolves to the tag, not the branch'
run "$r"
check_eq "$RC" 0 'tag named main: rc=0'
check_has "$OUT" 'PASS main-sync' 'tag named main: main-sync passes (the ref is fully spelled)'

# ---------- 7d. origin main AHEAD of local ----------
# Equality, not ancestry: an engine testing "local is an ancestor of origin" slips past row 5 only.

fixture ahead
ahead="$(git -C "$r" commit-tree -p refs/heads/main -m ahead 'refs/heads/main^{tree}')"
git -C "$r" update-ref refs/heads/tmpahead "$ahead"
put_bare 'refs/heads/tmpahead:refs/heads/main'
git -C "$r" update-ref -d refs/heads/tmpahead
check_eq "$(git -C "$b" rev-parse refs/heads/main)" "$ahead" 'origin ahead: FIXTURE — origin main moved to the new commit'
run "$r"
check_eq "$RC" 1 'origin ahead: rc=1'
check_has "$OUT" 'FAIL main-sync' 'origin ahead: main-sync fails'

# ---------- 8. SAME COUNT, DIFFERENT SET ----------
# origin loses v0.0.2 and gains vX (on a dev commit, so local does not carry it): both sides
# hold three tags, so a count comparison passes.

fixture sameset
git -C "$b" update-ref -d refs/tags/v0.0.2
git -C "$r" tag -a vX -m vX "$(git -C "$r" rev-parse dev)"
put_bare 'refs/tags/vX:refs/tags/vX'
git -C "$r" tag -d vX >/dev/null
check_eq "$(git -C "$b" for-each-ref refs/tags | wc -l | tr -d ' ')" \
  "$(git -C "$r" for-each-ref --merged main refs/tags | wc -l | tr -d ' ')" \
  'same set: FIXTURE — both sides hold the same number of tags'
run "$r"
check_eq "$RC" 1 'same count: rc=1'
check_has "$OUT" 'FAIL tags-parity' 'same count: tags-parity fails'
check_has "$OUT" 'only on origin: vX' 'same count: shows the origin-only side'
check_has "$OUT" 'only local: v0.0.2' 'same count: shows the local-only side'

# ---------- 9. retargeted tag object ----------
# origin's v0.0.2 becomes a NEW annotated tag object at the SAME commit (identical ^{} peel,
# different tag object). Only this shape separates comparing TAG objects (the spec) from
# comparing peeled commits; a retarget to a DIFFERENT commit fails under both and pins nothing.

fixture retag
git -C "$r" tag -a retarget -m 'a different tag object' "$(git -C "$r" rev-parse 'v0.0.2^{commit}')"
put_bare '+refs/tags/retarget:refs/tags/v0.0.2'
git -C "$r" tag -d retarget >/dev/null
check_eq "$(git -C "$b" rev-parse 'v0.0.2^{commit}')" "$(git -C "$r" rev-parse 'v0.0.2^{commit}')" \
  'retag: FIXTURE — the peeled commits are identical'
run "$r"
check_eq "$RC" 1 'retag: rc=1'
check_has "$OUT" 'FAIL tags-parity' 'retag: tags-parity fails'
check_has "$OUT" 'only on origin: v0.0.2' 'retag: origin side shows v0.0.2'
check_has "$OUT" 'only local: v0.0.2' 'retag: local side shows v0.0.2'

# ---------- 10. zero tags both sides ----------

fixture notags
for t in $(git -C "$r" tag -l); do git -C "$r" tag -d "$t" >/dev/null; done
for t in $(git -C "$b" for-each-ref --format='%(refname)' refs/tags); do
  git -C "$b" update-ref -d "$t"
done
check_eq "$(git -C "$r" tag -l | wc -l | tr -d ' ')" 0 'no tags: FIXTURE — local holds none'
run "$r"
check_eq "$RC" 1 'no tags: rc=1'
check_has "$OUT" 'FAIL tags-parity' 'no tags: two empty sets do not compare EQUAL'
check_has "$OUT" 'nothing was published' 'no tags: the reason names that nothing was published'

# ---------- 11. dead origin over a FRESH cache ----------

fixture dead
git -C "$r" remote set-url origin "$tmproot/definitely-not-a-repo"
run "$r"
check_eq "$RC" 1 'dead origin: rc=1'
check_has "$OUT" 'FAIL remote-read' 'dead origin: remote-read FAILs'
check_has "$OUT" 'SKIP remote-refs' 'dead origin: remote-refs SKIPs'
check_has "$OUT" 'SKIP main-sync' 'dead origin: main-sync SKIPs'
check_has "$OUT" 'SKIP tags-parity' 'dead origin: tags-parity SKIPs'
check_has "$OUT" 'RESULT: FAIL rc=1' 'dead origin: RESULT is FAIL'
check_lacks "$OUT" 'PASS main-sync' 'dead origin: main-sync must NOT pass from the cache'
check_lacks "$OUT" 'RESULT: PASS' 'dead origin: no PASS verdict anywhere'

# ---------- 19. a dead origin still runs the local checks (same fixture) ----------

check_has "$OUT" 'PASS watermark-present' 'dead origin: watermark-present still runs'
check_has "$OUT" 'PASS watermark-tree' 'dead origin: watermark-tree still runs'
check_has "$OUT" 'PASS watermark-ancestor' 'dead origin: watermark-ancestor still runs'
check_has "$OUT" 'PASS watermark-current' 'dead origin: watermark-current still runs'

# ---------- 12. empty origin (ls-remote exits 0 with no output) ----------

# The fixture is NOT named for emptiness: the engine echoes the origin path on its observing line.
fixture noreflist
rm -rf "$b"
git init -q --bare -b main "$b"
run "$r"
check_eq "$RC" 1 'empty origin: rc=1'
check_has "$OUT" 'FAIL remote-read' 'empty origin: remote-read FAILs'
rr_line="$(grep -F 'FAIL remote-read' <<<"$OUT")"
check_has "$rr_line" 'empty' 'empty origin: the remote-read reason itself names emptiness'
check_lacks "$OUT" 'RESULT: PASS' 'empty origin: no PASS verdict'

# ---------- 13. push url differs ----------

fixture pushurl
git init -q --bare -b main "$tmproot/other-push.git"
git -C "$r" remote set-url --push origin "$tmproot/other-push.git"
run "$r"
check_eq "$RC" 1 'push url differs: rc=1'
check_has "$OUT" 'FAIL origin-url' 'push url differs: origin-url FAILs'

# ---------- 14. watermark absent ----------

fixture nowm
git -C "$r" update-ref -d refs/published/main
run "$r"
check_eq "$RC" 1 'no watermark: rc=1'
check_has "$OUT" 'FAIL watermark-present' 'no watermark: watermark-present FAILs'
check_has "$OUT" 'SKIP watermark-tree' 'no watermark: watermark-tree SKIPs'
check_has "$OUT" 'SKIP watermark-ancestor' 'no watermark: watermark-ancestor SKIPs'
check_has "$OUT" 'SKIP watermark-current' 'no watermark: watermark-current SKIPs'

# ---------- 15. watermark tree differs ----------

fixture wmtree
git -C "$r" update-ref refs/published/main "$(git -C "$r" rev-parse dev~1)"
run "$r"
check_eq "$RC" 1 'watermark tree: rc=1'
check_has "$OUT" 'FAIL watermark-tree' 'watermark tree: watermark-tree FAILs'

# ---------- 16. watermark not an ancestor of dev ----------

fixture wmanc
orphan="$(git -C "$r" commit-tree -m unrelated "$(git -C "$r" mktree </dev/null)")"
git -C "$r" update-ref refs/published/main "$orphan"
check_eq "$(git -C "$r" merge-base --is-ancestor "$orphan" dev 2>/dev/null; echo $?)" 1 \
  'watermark ancestor: FIXTURE — the watermark is not an ancestor of dev'
run "$r"
check_eq "$RC" 1 'watermark ancestor: rc=1'
check_has "$OUT" 'FAIL watermark-ancestor' 'watermark ancestor: watermark-ancestor FAILs'

# ---------- 17. watermark lags dev ----------

fixture lag
printf 'v3\n' > "$r/payload.txt"
git -C "$r" add -A
git -C "$r" commit -qm 'brick three (unpublished)'
run "$r"
check_eq "$RC" 1 'watermark lags: rc=1'
check_has "$OUT" 'FAIL watermark-current' 'watermark lags: watermark-current FAILs'
check_has "$OUT" '1 commit' 'watermark lags: the reason carries the distinctive "1 commit"'
check_has "$OUT" 'PASS watermark-tree' 'watermark lags: watermark-tree still passes'
check_has "$OUT" 'PASS watermark-ancestor' 'watermark lags: watermark-ancestor still passes'

# ---------- 17b. dev deleted locally (rev-list errors with EMPTY stdout) ----------

fixture nodev
git -C "$r" checkout -q main
git -C "$r" branch -D dev >/dev/null
run "$r"
check_eq "$RC" 1 'no dev: rc=1'
check_has "$OUT" 'FAIL watermark-current' 'no dev: watermark-current FAILs'
check_lacks "$OUT" 'PASS watermark-current' 'no dev: never PASSes watermark-current (naive -eq 0 does)'

# ---------- 18. no local main ----------

fixture nomain
git -C "$r" branch -D main >/dev/null
run "$r"
check_eq "$RC" 1 'no local main: rc=1'
check_has "$OUT" 'FAIL main-sync' 'no local main: main-sync FAILs'
ms_line="$(grep -F 'FAIL main-sync' <<<"$OUT")"
check_has "$ms_line" 'no local refs/heads/main' 'no local main: the main-sync reason itself names the missing local main'
check_has "$OUT" 'SKIP tags-parity' 'no local main: tags-parity SKIPs'

# ---------- 20. usage errors -> ERROR ----------

fixture usage
run "$tmproot/does-not-exist"
check_eq "$RC" 2 'missing scope: rc=2'
check_has "$OUT" 'RESULT: ERROR rc=2' 'missing scope: RESULT is ERROR'
check_eq "$(result_prefix "$OUT")" 'RESULT: ERROR rc=2' 'missing scope: RESULT is the last line'

plain="$tmproot/plain"
mkdir -p "$plain"
git init -q "$plain"
run "$plain"
check_eq "$RC" 2 'not adopted: rc=2'
check_has "$OUT" 'RESULT: ERROR rc=2' 'not adopted: RESULT is ERROR'
check_eq "$(result_prefix "$OUT")" 'RESULT: ERROR rc=2' 'not adopted: RESULT is the last line'

run "$r" --bogus-flag
check_eq "$RC" 2 'unknown flag: rc=2'
check_has "$OUT" 'RESULT: ERROR rc=2' 'unknown flag: RESULT is ERROR'
check_eq "$(result_prefix "$OUT")" 'RESULT: ERROR rc=2' 'unknown flag: RESULT is the last line'

# ---------- 21. a killed run must never read as clean ----------
# A fake `git` earlier on PATH sleeps only for ls-remote (it holds the engine inside its network
# call) and execs the REAL git, baked in by absolute path, for every other invocation. It drops a
# marker when it starts so the kill lands mid-call; the wait for that marker is bounded at 10 s.
# Bash defers a trap until the foreground child returns, so the verdict appears when the fake's
# sleep ends, not at the instant of the signal.

fixture hang
real_git="$(command -v git)"
mkdir -p "$tmproot/fakebin"
marker="$tmproot/lsremote.started"
cat > "$tmproot/fakebin/git" <<EOF
#!/bin/sh
for a in "\$@"; do
  if [ "\$a" = ls-remote ]; then
    : > "$marker"
    sleep 4
    break
  fi
done
exec "$real_git" "\$@"
EOF
chmod +x "$tmproot/fakebin/git"
PATH="$tmproot/fakebin:$PATH" "$engine" --scope "$r" > "$tmproot/hang.out" 2>/dev/null &
enginepid=$!
i=0
while [[ ! -e "$marker" && "$i" -lt 100 ]]; do
  sleep 0.1
  i=$((i + 1))
done
if [[ -e "$marker" ]]; then
  pass_line 'killed: the engine reached ls-remote within 10 s'
else
  fail_line 'killed: the engine never reached ls-remote within 10 s'
fi
kill -TERM "$enginepid" 2>/dev/null
i=0
while kill -0 "$enginepid" 2>/dev/null && [[ "$i" -lt 150 ]]; do
  sleep 0.1
  i=$((i + 1))
done
if kill -0 "$enginepid" 2>/dev/null; then
  kill -KILL "$enginepid" 2>/dev/null
  fail_line 'killed: the engine did not exit within 15 s of SIGTERM'
fi
wait "$enginepid" 2>/dev/null
killed_rc=$?
killed_out="$(cat "$tmproot/hang.out")"
check_lacks "$killed_out" 'RESULT: PASS' 'killed: never prints a PASS verdict'
check_has "$killed_out" 'RESULT: INCOMPLETE' 'killed: prints INCOMPLETE'
check_eq "$killed_rc" 143 'killed: exits 143 (SIGTERM)'

# ---------- 21b. a COMPLETE-looking listing followed by a non-zero exit ----------
# Only the rc guard stops this: a dead remote prints nothing (the empty-output guard also fails
# it), but a connection dropped after every ref was sent prints a full listing and exits non-zero.
# The fake git runs the REAL ls-remote against the fixture's origin, prints its stdout unchanged,
# writes one line to stderr and exits 128; every other invocation execs the real git. It is
# scoped to this one engine call through a per-call PATH prefix, never exported.

fixture droppedlist
real_git="$(command -v git)"
mkdir -p "$tmproot/fakebin2"
cat > "$tmproot/fakebin2/git" <<EOF
#!/bin/sh
for a in "\$@"; do
  if [ "\$a" = ls-remote ]; then
    "$real_git" "\$@"
    echo 'fatal: the remote end hung up unexpectedly' >&2
    exit 128
  fi
done
exec "$real_git" "\$@"
EOF
chmod +x "$tmproot/fakebin2/git"
OUT="$(PATH="$tmproot/fakebin2:$PATH" LC_ALL=en_US.UTF-8 "$engine" --scope "$r" 2>/dev/null)"
RC=$?
check_eq "$RC" 1 'dropped listing: rc=1'
rr_line="$(grep -F 'FAIL remote-read' <<<"$OUT")"
check_has "$rr_line" 'exited 128' 'dropped listing: the remote-read reason names exited 128'
check_has "$OUT" 'SKIP remote-refs' 'dropped listing: remote-refs SKIPs'
check_has "$OUT" 'SKIP main-sync' 'dropped listing: main-sync SKIPs'
check_has "$OUT" 'SKIP tags-parity' 'dropped listing: tags-parity SKIPs'
check_has "$OUT" 'RESULT: FAIL rc=1' 'dropped listing: RESULT is FAIL'
check_lacks "$OUT" 'PASS remote-refs' 'dropped listing: remote-refs must NOT pass from a complete-looking listing'
check_lacks "$OUT" 'RESULT: PASS' 'dropped listing: no PASS verdict'

# ---------- 22. read-only control ----------
# Digest every ref (remote-tracking included) and the porcelain status before and after a clean
# run. Both readings are asserted NON-EMPTY first (two absent readings compare equal), and a
# control that MOVES proves the digest can see a change.

fixture readonly
digest() { # repo -> cksum over all refs, and over status
  printf 'refs=%s status=%s' \
    "$(git -C "$1" for-each-ref | cksum)" \
    "$(git -C "$1" status --porcelain -uall | cksum)"
}
refs_before="$(git -C "$r" for-each-ref)"
check_eq "$([[ -n "$refs_before" ]] && echo nonempty)" nonempty 'read-only: the refs reading is non-empty'
check_has "$refs_before" 'refs/remotes/origin/main' 'read-only: the reading includes remote-tracking refs'
# Make the tracking cache STALE before the digest: a fetch against an up-to-date origin leaves the
# digest unchanged, so only a stale cache lets an added `git fetch origin` move it. The engine reads
# origin live, so it must still PASS.
git -C "$r" update-ref refs/remotes/origin/main "$(git -C "$r" rev-parse 'v0.0.1^{commit}')"
before="$(digest "$r")"
run "$r"
check_has "$OUT" 'RESULT: PASS' 'read-only: the engine actually ran clean over a stale cache (reached the subject)'
after="$(digest "$r")"
check_eq "$after" "$before" 'read-only: refs and status are byte-identical after a run'
git -C "$r" update-ref refs/published/control "$(git -C "$r" rev-parse dev)"
moved="$(digest "$r")"
if [[ "$moved" != "$before" ]]; then
  pass_line 'read-only: control — an update-ref MOVES the digest'
else
  fail_line 'read-only: control — an update-ref did not move the digest'
fi
git -C "$r" update-ref -d refs/published/control
printf 'x\n' > "$r/untracked.txt"
moved="$(digest "$r")"
if [[ "$moved" != "$before" ]]; then
  pass_line 'read-only: control — an untracked file MOVES the digest'
else
  fail_line 'read-only: control — an untracked file did not move the digest'
fi
rm -f "$r/untracked.txt"

# ---------- 23. the observing line ----------

fixture observing
run "$r"
check_has "$OUT" "observing: origin=$b" 'observing: names the origin url the run used'
check_has "$OUT" "main=$(git -C "$r" rev-parse refs/heads/main)" 'observing: carries the main sha'
obs_n="$(grep -n '^observing:' <<<"$OUT" | head -1 | cut -d: -f1)"
verdict_n="$(grep -nE '^(PASS|FAIL|SKIP) ' <<<"$OUT" | head -1 | cut -d: -f1)"
check_eq "$([[ -n "$obs_n" && -n "$verdict_n" && "$obs_n" -lt "$verdict_n" ]] && echo before)" before \
  "observing: the line (#$obs_n) precedes the first verdict line (#$verdict_n)"

fixture observingdead
git -C "$r" remote set-url origin "$tmproot/definitely-not-a-repo"
run "$r"
check_has "$OUT" ' main=none' 'observing: a dead origin spells main=none'

fixture observingnomain
git -C "$b" update-ref -d refs/heads/main
run "$r"
check_has "$OUT" ' main=none' 'observing: an origin without main spells main=none'

# ---------- 24. tool failures inside tags-parity must never read as parity ----------
# A REAL mismatch (an origin-only tag) plus a fake `sort` / `comm` first on PATH for the ONE engine
# call (PATH scoped to the call, never exported). The shim exits 1 printing nothing, so an engine
# that ignores the substitution's rc compares two empty sets and prints PASS about nothing.
# A control shim that execs the real tool must leave the clean fixture at 9/0/0.

mkdir -p "$tmproot/shim-sort-bad" "$tmproot/shim-comm-bad" "$tmproot/shim-sort-ok" "$tmproot/shim-comm-ok"
for t in sort comm; do
  printf '#!/bin/sh\nexit 1\n' > "$tmproot/shim-$t-bad/$t"
  printf '#!/bin/sh\nexec "%s" "$@"\n' "$(command -v "$t")" > "$tmproot/shim-$t-ok/$t"
  chmod +x "$tmproot/shim-$t-bad/$t" "$tmproot/shim-$t-ok/$t"
done

# run_shim SCOPE SHIMDIR -> like run, with SHIMDIR first on PATH for this one call only
run_shim() {
  OUT="$(PATH="$2:$PATH" LC_ALL=en_US.UTF-8 "$engine" --scope "$1" 2>/dev/null)"
  RC=$?
}

fixture shimmismatch
git -C "$r" tag -a devtag -m devtag "$(git -C "$r" rev-parse dev)"
put_bare 'refs/tags/devtag:refs/tags/devtag'
git -C "$r" tag -d devtag >/dev/null
run "$r"
check_has "$OUT" 'only on origin: devtag' 'shims: FIXTURE — the mismatch is real without any shim'

run_shim "$r" "$tmproot/shim-sort-bad"
check_eq "$RC" 1 'failing sort: rc=1'
check_has "$OUT" 'FAIL tags-parity' 'failing sort: tags-parity FAILs'
check_has "$OUT" 'sort' 'failing sort: the reason names sort'
check_lacks "$OUT" 'PASS tags-parity' 'failing sort: never passes tags-parity'
check_has "$OUT" 'RESULT: FAIL rc=1' 'failing sort: RESULT is FAIL'
check_lacks "$OUT" 'RESULT: PASS' 'failing sort: no PASS verdict'

run_shim "$r" "$tmproot/shim-comm-bad"
check_eq "$RC" 1 'failing comm: rc=1'
check_has "$OUT" 'FAIL tags-parity' 'failing comm: tags-parity FAILs'
check_has "$OUT" 'comm' 'failing comm: the reason names comm'
check_lacks "$OUT" 'PASS tags-parity' 'failing comm: never passes tags-parity'
check_has "$OUT" 'RESULT: FAIL rc=1' 'failing comm: RESULT is FAIL'
check_lacks "$OUT" 'RESULT: PASS' 'failing comm: no PASS verdict'

fixture shimclean
run_shim "$r" "$tmproot/shim-sort-ok"
check_eq "$(last_line "$OUT")" 'RESULT: PASS rc=0 checks=9/0/0' 'control: a pass-through sort shim leaves the clean fixture 9/0/0'
run_shim "$r" "$tmproot/shim-comm-ok"
check_eq "$(last_line "$OUT")" 'RESULT: PASS rc=0 checks=9/0/0' 'control: a pass-through comm shim leaves the clean fixture 9/0/0'

# ---------- 25. origin HEAD detached at a dev commit ----------
# origin's refs/heads/main is right, but its HEAD advertises a dev commit: dev content is reachable
# from the remote's default entry point even though no dev ref exists.

fixture headdev
devsha="$(git -C "$r" rev-parse dev)"
git -C "$b" fetch -q --no-tags "$r" "$devsha"
git -C "$b" update-ref --no-deref HEAD "$devsha"
check_eq "$(git -C "$b" rev-parse HEAD)" "$devsha" 'head detached: FIXTURE — the bare HEAD is at the dev commit'
check_eq "$(git -C "$b" rev-parse refs/heads/main)" "$(git -C "$r" rev-parse refs/heads/main)" 'head detached: FIXTURE — origin main is still correct'
run "$r"
check_eq "$RC" 1 'head detached: rc=1'
check_has "$OUT" 'FAIL remote-refs' 'head detached: remote-refs FAILs'
check_has "$OUT" 'unexpected ref: HEAD' 'head detached: names HEAD'
check_has "$OUT" "$devsha" 'head detached: the detail carries the dev sha'

# ---------- 25b. origin HEAD detached at a dev commit, with NO refs/heads/main ----------
# The HEAD guard must not depend on origin holding a main: with none, HEAD agrees with nothing.

fixture headdevnomain
devsha="$(git -C "$r" rev-parse dev)"
git -C "$b" fetch -q --no-tags "$r" "$devsha"
git -C "$b" update-ref -d refs/heads/main
git -C "$b" update-ref --no-deref HEAD "$devsha"
check_eq "$(git -C "$b" rev-parse HEAD)" "$devsha" 'head detached, no main: FIXTURE — the bare HEAD is at the dev commit'
check_eq "$(git -C "$b" for-each-ref refs/heads/main | wc -l | tr -d ' ')" 0 'head detached, no main: FIXTURE — origin has no refs/heads/main'
run "$r"
check_eq "$RC" 1 'head detached, no main: rc=1'
check_has "$OUT" 'FAIL remote-refs' 'head detached, no main: remote-refs FAILs'
check_has "$OUT" 'unexpected ref: HEAD' 'head detached, no main: names HEAD'
check_has "$OUT" "$devsha" 'head detached, no main: the detail carries the dev sha'
check_has "$OUT" "but origin has no refs/heads/main" 'head detached, no main: the detail says origin has no main'
check_eq "$(last_line "$OUT" | cut -d' ' -f1-3)" 'RESULT: FAIL rc=1' 'head detached, no main: RESULT is FAIL rc=1'

# ---------- 26. a second push url ----------
# The first push url equals the fetch url, so a check that compares only the first one passes.

fixture pushtwo
git init -q --bare -b main "$tmproot/second-push.git"
git -C "$r" remote set-url --push origin "$b"
git -C "$r" remote set-url --add --push origin "$tmproot/second-push.git"
check_eq "$(git -C "$r" remote get-url --push --all origin | wc -l | tr -d ' ')" 2 'second push url: FIXTURE — origin has two push urls'
check_eq "$(git -C "$r" remote get-url --push origin)" "$(git -C "$r" remote get-url origin)" 'second push url: FIXTURE — the first push url equals the fetch url'
run "$r"
check_eq "$RC" 1 'second push url: rc=1'
check_has "$OUT" 'FAIL origin-url' 'second push url: origin-url FAILs'

# ---------- summary ----------
# Printed last, so its absence is itself the signal that this run died rather than passed.
printf '\nRESULT: %s passed, %s failed\n' "$pass" "$fail"
if [[ "$fail" -eq 0 ]]; then exit 0; else exit 1; fi

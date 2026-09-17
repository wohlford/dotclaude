#!/usr/bin/env bash
set -uo pipefail

# Script: test_publish_brick.sh
# Purpose: Regression tests for scripts/publish-brick.sh — the per-brick engine of /propagate's
#          adopted publish path. Covers the materialisation precondition (no deletions or
#          renames, endpoint last), both shape assertions, the audit verdict ALLOWLIST, the
#          tag post-condition, and the rollback that keeps `main` clean after a pre-commit
#          refusal.
# Usage:   ./scripts/tests/test_publish_brick.sh
#
# Fixtures model the real publication topology: `main` is a DIVORCED orphan sharing no ancestry
# with `dev`, whose tip tree equals a `dev` commit's tree plus CHANGELOG.md. The brick engine
# has to work across that divorce, which is why the fixture cannot just branch off `dev`.
#
# The audit is a STUB inside the fixture repo, driven by $AUDIT_MODE. That is deliberate: the
# engine must run the audit belonging to the tree it is proving, so the seam under test is
# "resolve <scope>/skills/audit/audit.sh and read its verdict", not a mockable function.
#
# The sandbox root is pinned with `pwd -P`: $TMPDIR is a symlink on macOS (/tmp ->
# /private/tmp), and a logical path does not physically contain its files — which silently
# routes path-resolving tools down a different branch than the one under test.

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
engine="$here/../../scripts/publish-brick.sh"
tmproot="$(cd "$(mktemp -d)" && pwd -P)"
trap 'rm -rf "$tmproot"' EXIT

pass=0
fail=0
pass_line() { printf 'PASS  %s\n' "$1"; pass=$((pass + 1)); }
fail_line() { printf 'FAIL  %s\n' "$1"; fail=$((fail + 1)); }

check_eq() { # got want label
  if [ "$1" = "$2" ]; then pass_line "$3"; else fail_line "$3 (want [$2] got [$1])"; fi
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

# shellcheck disable=SC2016  # a literal script body; $AUDIT_MODE must NOT expand here
AUDIT_STUB='#!/usr/bin/env bash
# Stub standing in for skills/audit/audit.sh. $AUDIT_MODE selects the verdict shape.
case "${AUDIT_MODE-pass}" in
  pass)
    # A leak detector: the engine must never hand the audit a literal-pathspec environment, under
    # which the real audit skips every file-discovering check and still prints PASS.
    if [ -n "${GIT_LITERAL_PATHSPECS:-}" ]; then printf "RESULT: FAIL rc=1 envleak\n"; exit 1; fi
    printf "PASS format-tabs\n"; printf "RESULT: PASS rc=0 checks=1/0/0\n"; exit 0 ;;
  fail)   printf "FAIL ruff — scripts/x.py:1:1 E999\n"; printf "RESULT: FAIL rc=1 checks=0/1/0\n"; exit 1 ;;
  killed) printf "PASS format-tabs\n"; exit 143 ;;
  weird)  printf "RESULT: SPLENDID rc=0 checks=1/0/0\n"; exit 0 ;;
  liar)   printf "RESULT: PASS rc=0 checks=1/0/0\n"; exit 1 ;;
  trailer) printf "RESULT: PASS rc=0 checks=1/0/0\n"; printf "oh and one more thing\n"; exit 0 ;;
esac
'

# mk DIR -> an adopted repo with a divorced main and four dev commits:
#   dev:  c1(a.txt=1,tooling) -> c2(a.txt=2) -> c3(b.txt=1) -> c4(a.txt=3)
#   main: m1 (orphan; c1's tree + CHANGELOG.md)
mk() {
  local d="$1"
  mkdir -p "$d/skills/audit"
  git init -q -b dev "$d"
  git -C "$d" config user.email test@test.invalid
  git -C "$d" config user.name test
  git -C "$d" config commit.gpgsign false
  git -C "$d" config tag.gpgsign false

  printf 'production = "dev"\n' > "$d/.publication.toml"
  printf '%s' "$AUDIT_STUB" > "$d/skills/audit/audit.sh"
  chmod +x "$d/skills/audit/audit.sh"
  printf '1\n' > "$d/a.txt"
  git -C "$d" add -A
  git -C "$d" commit -qm 'c1 tooling and a'

  printf '2\n' > "$d/a.txt"
  git -C "$d" commit -qam 'c2 bump a'
  printf '1\n' > "$d/b.txt"
  git -C "$d" add b.txt
  git -C "$d" commit -qm 'c3 add b'
  printf '3\n' > "$d/a.txt"
  git -C "$d" commit -qam 'c4 bump a again'

  # main: divorced orphan carrying c1's tree plus the CHANGELOG dev never gets.
  git -C "$d" checkout -q --orphan main
  git -C "$d" reset -q --hard
  git -C "$d" checkout -q "$(git -C "$d" rev-parse dev~3)" -- .
  printf '# Changelog\n\n## v0.1.0 — 2026-01-01\n- feat(a): the first brick\n' > "$d/CHANGELOG.md"
  git -C "$d" add -A
  git -C "$d" commit -qm 'feat(a): the first brick'
}

sha_of() { git -C "$1" rev-parse "$2"; }

# ---------- happy path: a 1:1 brick ----------

r="$tmproot/one"; mk "$r"
c2="$(sha_of "$r" dev~2)"
out="$(cd "$r" && bash "$engine" v0.2.0 "$c2" 'feat(a): bump a' 2>&1)"; rc=$?

check_eq "$rc" 0 '1:1 brick exits 0'
check_has "$out" 'RESULT: PASS rc=0' '1:1 brick prints the PASS verdict line'
check_eq "$(git -C "$r" log -1 --format=%s main)" 'feat(a): bump a' '1:1 brick commit subject'
check_eq "$(git -C "$r" show main:a.txt)" '2' '1:1 brick content comes from the endpoint'
check_has "$(git -C "$r" show main:CHANGELOG.md)" '## v0.2.0 — ' '1:1 brick changelog entry landed'
check_has "$(git -C "$r" tag --points-at main)" 'v0.2.0' '1:1 brick tag points at the new commit'
check_eq "$(git -C "$r" status --porcelain | wc -l | tr -d ' ')" 0 '1:1 brick leaves the tree clean'
check_lacks "$(git -C "$r" show --name-only --format= main)" 'b.txt' '1:1 brick did not drag in b.txt'

# the changelog date comes from the endpoint commit, not from today
want_date="$(git -C "$r" log -1 --format=%ad --date=short "$c2")"
check_has "$(git -C "$r" show main:CHANGELOG.md)" "## v0.2.0 — $want_date" \
  'changelog date is the endpoint commit date'

# ---------- happy path: a folded, non-contiguous brick ----------

r="$tmproot/fold"; mk "$r"
c3="$(sha_of "$r" dev~1)"; c4="$(sha_of "$r" dev)"
out="$(cd "$r" && bash "$engine" v0.2.0 "$c4" 'feat(ab): a and b together' "$c3" 2>&1)"; rc=$?

check_eq "$rc" 0 'folded brick exits 0'
check_eq "$(git -C "$r" show main:a.txt)" '3' 'folded brick takes a.txt from the endpoint'
check_eq "$(git -C "$r" show main:b.txt)" '1' 'folded brick includes the folded file'
changed="$(git -C "$r" show --name-only --format= main | LC_ALL=C sort | tr '\n' ' ')"
check_eq "$changed" 'CHANGELOG.md a.txt b.txt ' 'folded brick touches exactly the union plus CHANGELOG'

# ---------- preconditions ----------

r="$tmproot/notmain"; mk "$r"; git -C "$r" checkout -q dev
out="$(cd "$r" && bash "$engine" v0.2.0 "$(sha_of "$r" dev~2)" 'feat(a): x' 2>&1)"; rc=$?
check_eq "$rc" 1 'refuses when not on main'
check_has "$out" 'RESULT: FAIL' 'not-on-main prints a FAIL verdict'
check_has "$out" 'branch' 'not-on-main names the branch problem'

r="$tmproot/dirty"; mk "$r"; printf 'scratch\n' > "$r/untracked.txt"
out="$(cd "$r" && bash "$engine" v0.2.0 "$(sha_of "$r" dev~2)" 'feat(a): x' 2>&1)"; rc=$?
check_eq "$rc" 1 'refuses a dirty working tree'
check_has "$out" 'clean' 'dirty tree names cleanliness'

r="$tmproot/plain"; mk "$r"; rm "$r/.publication.toml"
git -C "$r" commit -qam 'drop the marker'
out="$(cd "$r" && bash "$engine" v0.2.0 "$(sha_of "$r" dev~2)" 'feat(a): x' 2>&1)"; rc=$?
check_eq "$rc" 2 'refuses a non-adopted repo with a usage error'
check_has "$out" '.publication.toml' 'non-adopted names the missing marker'

# a constituent that DELETES a file: the engine must express it, not refuse it. INVERTED from
# the former "refuses a constituent that deletes a file" rows; see plan Task 1.
#
# THE PRIOR BRICK IS LOAD-BEARING, not scene-setting. `mk` builds `main` as an orphan carrying
# dev~3's tree, where b.txt does not yet exist — so a deleting brick applied straight onto that
# main finds b.txt absent at HEAD and takes the engine's SKIP arm, never its `git rm` arm. The
# rows below then pass while the branch they exist to cover is never executed, and the
# "absent from main" assertion is vacuous because the path was never there. Measured: with the
# rm arm deliberately disabled, this fixture stayed green.
# Landing b.txt on main first mirrors production — the real range MODIFIES
# agents/security-reviewer.md in one brick and DELETES it in a later one, and that file IS
# present at main's tip — so the delete genuinely has something to remove.
r="$tmproot/del"; mk "$r"
c3="$(sha_of "$r" dev~1)"
prior_out="$(cd "$r" && bash "$engine" v0.1.5 "$c3" 'feat(b): add b' 2>&1)" \
  || { printf 'FIXTURE BROKEN: could not land b.txt on main:\n%s\n' "$prior_out"; exit 1; }
git -C "$r" cat-file -e main:b.txt 2>/dev/null \
  || { printf 'FIXTURE BROKEN: b.txt is not on main, so the delete would take the SKIP arm\n'; exit 1; }
git -C "$r" checkout -q dev && git -C "$r" rm -q b.txt && git -C "$r" commit -qm 'c5 drop b'
del="$(sha_of "$r" dev)"; git -C "$r" checkout -q main
out="$(cd "$r" && bash "$engine" v0.2.0 "$del" 'feat(b): drop b' 2>&1)"; rc=$?
check_eq "$rc" 0 'a deleting constituent builds the brick'
check_has "$out" 'RESULT: PASS rc=0 brick=v0.2.0' 'deleting brick prints the PASS verdict line'
if git -C "$r" cat-file -e main:b.txt 2>/dev/null; then
  fail_line 'deletion brick: deleted path is REMOVED from main (it was present before)'
else
  pass_line 'deletion brick: deleted path is REMOVED from main (it was present before)'
fi
check_eq "$(git -C "$r" rev-list --count main)" 3 'deletion brick created a commit'

# a MIXED delete+modify constituent — the real brick 8504a2c is M,M,D. The 'del' fixture above
# deletes its ONLY file, so "the surviving path has the endpoint's content" cannot be asserted
# there; this row is the one that would catch a split that handles the delete arm but skips
# the checkout arm.
# Same prior-brick requirement as the 'del' fixture above, and for the same reason: without
# b.txt on main the delete arm is never reached and this row silently degrades to a
# modify-only test wearing a delete-and-modify name.
r="$tmproot/mixed"; mk "$r"
c3="$(sha_of "$r" dev~1)"
prior_out="$(cd "$r" && bash "$engine" v0.1.5 "$c3" 'feat(b): add b' 2>&1)" \
  || { printf 'FIXTURE BROKEN: could not land b.txt on main:\n%s\n' "$prior_out"; exit 1; }
git -C "$r" cat-file -e main:b.txt 2>/dev/null \
  || { printf 'FIXTURE BROKEN: b.txt is not on main, so the delete would take the SKIP arm\n'; exit 1; }
git -C "$r" checkout -q dev
printf '9\n' > "$r/a.txt"
git -C "$r" rm -q b.txt
git -C "$r" commit -qam 'c5 bump a and drop b'
mixed="$(sha_of "$r" dev)"; git -C "$r" checkout -q main
out="$(cd "$r" && bash "$engine" v0.2.0 "$mixed" 'feat(ab): bump a, drop b' 2>&1)"; rc=$?
check_eq "$rc" 0 'a mixed delete+modify constituent builds the brick'
check_has "$out" 'RESULT: PASS rc=0 brick=v0.2.0' 'mixed brick prints the PASS verdict line'
if git -C "$r" cat-file -e main:b.txt 2>/dev/null; then
  fail_line 'mixed brick: deleted path is REMOVED from main (it was present before)'
else
  pass_line 'mixed brick: deleted path is REMOVED from main (it was present before)'
fi
check_eq "$(git -C "$r" show main:a.txt)" '9' 'mixed brick: surviving path has the endpoint content'
check_eq "$(git -C "$r" rev-list --count main)" 3 'mixed brick created a commit'

# ---------- allowlist corpus: what must STILL be refused (Task 2) ----------
# R, C, U and a truly unanticipated status cannot be produced by any commit `git show
# --name-status` will emit, so they are UNFIXTURABLE through real commits (unlike D above,
# which is). Test assert_applicable's awk predicate directly, EXTRACTED from the engine file
# rather than hand-copied, so this corpus tracks whatever the implementation actually contains
# instead of a second, driftable spelling of the same intent.
# shellcheck disable=SC2016  # the literal '$1' must reach grep unexpanded
awk_line="$(grep -m1 '\$1 !~' "$engine")"
awk_prog="$(printf '%s' "$awk_line" | sed -n "s/.*awk '\\([^']*\\)'.*/\\1/p")"
if [ -z "$awk_prog" ]; then
  fail_line 'allowlist corpus: could not extract the awk predicate from the engine'
else
  pass_line 'allowlist corpus: extracted the awk predicate from the engine'
  bad="$(printf 'R100\told.txt\tnew.txt\n' | awk "$awk_prog")"
  check_has "$bad" 'R100' 'allowlist predicate still flags a RENAME status as bad'
  bad="$(printf 'C50\told.txt\tnew.txt\n' | awk "$awk_prog")"
  check_has "$bad" 'C50' 'allowlist predicate still flags a COPY status as bad'
  bad="$(printf 'U\tconflict.txt\n' | awk "$awk_prog")"
  check_has "$bad" 'U' 'allowlist predicate still flags an UNMERGED status as bad'
  bad="$(printf 'X\tfile.txt\n' | awk "$awk_prog")"
  check_has "$bad" 'X' 'allowlist predicate still flags an unanticipated status as bad'
fi

# a constituent that RENAMES
r="$tmproot/ren"; mk "$r"
git -C "$r" checkout -q dev && git -C "$r" mv b.txt c.txt && git -C "$r" commit -qm 'c5 rename b'
ren="$(sha_of "$r" dev)"; git -C "$r" checkout -q main
out="$(cd "$r" && bash "$engine" v0.2.0 "$ren" 'feat(b): rename b' 2>&1)"; rc=$?
check_eq "$rc" 1 'refuses a constituent that renames a file'
check_eq "$(git -C "$r" rev-list --count main)" 1 'rename refusal created no commit'

# an extra constituent that is NEWER than the endpoint — the endpoint must be last
r="$tmproot/order"; mk "$r"
c2="$(sha_of "$r" dev~2)"; c4="$(sha_of "$r" dev)"
out="$(cd "$r" && bash "$engine" v0.2.0 "$c2" 'feat(a): x' "$c4" 2>&1)"; rc=$?
check_eq "$rc" 1 'refuses a constituent newer than the endpoint'
check_has "$out" 'endpoint' 'ordering refusal names the endpoint rule'

# a constituent that is not on dev at all
r="$tmproot/alien"; mk "$r"
out="$(cd "$r" && bash "$engine" v0.2.0 "$(sha_of "$r" main)" 'feat(a): x' 2>&1)"; rc=$?
check_eq "$rc" 1 'refuses a constituent that is not a dev commit'

# an existing tag must never be silently moved
r="$tmproot/tagged"; mk "$r"
git -C "$r" tag -a v0.2.0 -m 'pre-existing' main
out="$(cd "$r" && bash "$engine" v0.2.0 "$(sha_of "$r" dev~2)" 'feat(a): x' 2>&1)"; rc=$?
check_eq "$rc" 1 'refuses when the version tag already exists'
check_eq "$(git -C "$r" rev-list --count main)" 1 'existing-tag refusal created no commit'

# a version already in the CHANGELOG
r="$tmproot/dupver"; mk "$r"
out="$(cd "$r" && bash "$engine" v0.1.0 "$(sha_of "$r" dev~2)" 'feat(a): x' 2>&1)"; rc=$?
check_eq "$rc" 1 'refuses a version already in the changelog'
check_eq "$(git -C "$r" status --porcelain | wc -l | tr -d ' ')" 0 'duplicate-version refusal leaves the tree clean'
check_eq "$(git -C "$r" rev-list --count main)" 1 'duplicate-version refusal created no commit'

# ---------- the audit verdict is an ALLOWLIST ----------

for mode in fail killed weird liar trailer; do
  r="$tmproot/audit-$mode"; mk "$r"
  out="$(cd "$r" && AUDIT_MODE="$mode" bash "$engine" v0.2.0 "$(sha_of "$r" dev~2)" 'feat(a): x' 2>&1)"; rc=$?
  check_eq "$rc" 1 "audit mode '$mode' fails the brick"
  check_eq "$(git -C "$r" tag -l | wc -l | tr -d ' ')" 0 "audit mode '$mode' mints no tag"
  check_has "$out" 'RESULT: FAIL' "audit mode '$mode' prints a FAIL verdict"
done

# a missing audit means the brick is UNPROVEN, which is a failure, not a skip
r="$tmproot/noaudit"; mk "$r"
git -C "$r" rm -q skills/audit/audit.sh && git -C "$r" commit -qm 'drop the audit'
out="$(cd "$r" && bash "$engine" v0.2.0 "$(sha_of "$r" dev~2)" 'feat(a): x' 2>&1)"; rc=$?
check_eq "$rc" 1 'a missing audit fails the brick rather than skipping'
check_eq "$(git -C "$r" tag -l | wc -l | tr -d ' ')" 0 'missing audit mints no tag'

# ---------- the audit artifact records its own exit status ----------

r="$tmproot/artifact"; mk "$r"; adir="$tmproot/artifacts"; mkdir -p "$adir"
out="$(cd "$r" && bash "$engine" --artifact-dir "$adir" v0.2.0 "$(sha_of "$r" dev~2)" 'feat(a): x' 2>&1)"
art="$adir/audit-v0.2.0.txt"
if [ -f "$art" ]; then pass_line 'audit artifact is written where asked'; else fail_line 'audit artifact is written where asked'; fi
check_has "$(cat "$art" 2>/dev/null)" 'AUDIT_EXIT_STATUS=0' 'artifact records the audit exit status INSIDE itself'
check_has "$(cat "$art" 2>/dev/null)" 'RESULT: PASS rc=0' 'artifact carries the audit verdict line'
check_has "$out" "$art" 'run names the artifact path on stdout'

r="$tmproot/artifact-killed"; mk "$r"; adir2="$tmproot/artifacts2"; mkdir -p "$adir2"
out="$(cd "$r" && AUDIT_MODE=killed bash "$engine" --artifact-dir "$adir2" v0.2.0 "$(sha_of "$r" dev~2)" 'feat(a): x' 2>&1)"
check_has "$(cat "$adir2/audit-v0.2.0.txt" 2>/dev/null)" 'AUDIT_EXIT_STATUS=143' \
  'a killed audit records rc=143 inside the artifact'

# ---------- pathspecs are LITERAL ----------
# A brick file whose name holds glob characters must not materialise its lookalikes. `g[1].txt` is a
# glob matching `g1.txt`; without literal pathspecs `git checkout <endpoint> -- 'g[1].txt'` stages
# BOTH (measured 2026-09-16), and shape B then refuses the brick. The lookalike is bumped on dev in an
# EARLIER commit than the brick's endpoint so it differs at the endpoint while not being one of the
# brick's files — the only arrangement where an overreach is observable.
r="$tmproot/glob"; mk "$r"
git -C "$r" checkout -q dev
printf '1\n' > "$r/g1.txt"; printf '1\n' > "$r/g[1].txt"
git -C "$r" add -A && git -C "$r" commit -qm 'c5 add lookalikes'
g5="$(sha_of "$r" dev)"
printf '2\n' > "$r/g1.txt"; git -C "$r" commit -qam 'c6 bump g1'
printf '2\n' > "$r/g[1].txt"; git -C "$r" commit -qam 'c7 bump the bracketed file'
g7="$(sha_of "$r" dev)"; git -C "$r" checkout -q main
prior_out="$(cd "$r" && bash "$engine" v0.1.5 "$g5" 'feat(g): add lookalikes' 2>&1)" \
  || { printf 'FIXTURE BROKEN: could not land the lookalikes on main:\n%s\n' "$prior_out"; exit 1; }
out="$(cd "$r" && bash "$engine" v0.2.0 "$g7" 'feat(g): bump the bracketed file' 2>&1)"; rc=$?
check_eq "$rc" 0 'a brick file named with glob characters builds'
check_eq "$(git -C "$r" show main:'g[1].txt')" '2' 'the glob-named file takes the endpoint content'
check_eq "$(git -C "$r" show main:g1.txt)" '1' 'its glob lookalike g1.txt is left untouched'

# ---------- usage ----------

r="$tmproot/usage"; mk "$r"
out="$(cd "$r" && bash "$engine" v0.2.0 2>&1)"; rc=$?
check_eq "$rc" 2 'too few arguments is a usage error'
out="$(cd "$r" && bash "$engine" --nonsense v0.2.0 x y 2>&1)"; rc=$?
check_eq "$rc" 2 'an unknown flag is a usage error'
out="$(cd "$r" && bash "$engine" 0.2.0 "$(sha_of "$r" dev~2)" 'feat(a): x' 2>&1)"; rc=$?
check_eq "$rc" 2 'a malformed version is a usage error'

# ---------- scope resolution ----------

r="$tmproot/scope"; mk "$r"
out="$(cd "$tmproot" && bash "$engine" --scope "$r" v0.2.0 "$(sha_of "$r" dev~2)" 'feat(a): x' 2>&1)"; rc=$?
check_eq "$rc" 0 '--scope drives a repo from outside it'
check_has "$(git -C "$r" tag --points-at main)" 'v0.2.0' '--scope run tagged the right repo'

# ---------- consecutive bricks stack ----------

r="$tmproot/stack"; mk "$r"
(cd "$r" && bash "$engine" v0.2.0 "$(sha_of "$r" dev~2)" 'feat(a): bump a' >/dev/null 2>&1)
(cd "$r" && bash "$engine" v0.3.0 "$(sha_of "$r" dev~1)" 'feat(b): add b' >/dev/null 2>&1)
out="$(cd "$r" && bash "$engine" v0.4.0 "$(sha_of "$r" dev)" 'feat(a): bump a again' 2>&1)"; rc=$?
check_eq "$rc" 0 'a third stacked brick exits 0'
check_eq "$(git -C "$r" rev-list --count main)" 4 'three bricks appended onto the orphan root'
check_eq "$(git -C "$r" tag -l | wc -l | tr -d ' ')" 3 'each brick minted exactly one tag'
# the convergence predicate the publish path uses: main's tree == dev's tree modulo CHANGELOG
if git -C "$r" diff --quiet dev main -- . ':(exclude)CHANGELOG.md'; then
  pass_line 'stacked bricks converge main to dev modulo CHANGELOG.md'
else
  fail_line 'stacked bricks converge main to dev modulo CHANGELOG.md'
fi
heads="$(git -C "$r" show main:CHANGELOG.md | grep -c '^## v')"
check_eq "$heads" 4 'the changelog gained one section per brick'

# ============================== DEV MODE ==============================
# `--dev` builds a re-derivation brick onto `dev` from an ORACLE (the frozen feature tip) and an
# explicit file list: no version, no CHANGELOG entry, no tag.

# mkdev DIR [policy] -> an adopted repo on `dev` with one base commit and a branch `feat` whose tip is
# the oracle. The oracle modifies a.txt and b.txt, deletes gone.txt, renames old.txt -> new.txt, sets
# tool.sh's exec bit, and bumps BOTH g[1].txt and its glob lookalike g1.txt. The base carries a
# CHANGELOG.md so "dev mode writes no entry" is observable. With `policy`, the base also carries a
# .commit-conventions.toml advising at 40 characters and blocking at 60.
mkdev() {
  local d="$1" f
  mkdir -p "$d/skills/audit"
  git init -q -b dev "$d"
  git -C "$d" config user.email test@test.invalid
  git -C "$d" config user.name test
  git -C "$d" config commit.gpgsign false
  git -C "$d" config tag.gpgsign false
  printf 'production = "dev"\n' > "$d/.publication.toml"
  printf '%s' "$AUDIT_STUB" > "$d/skills/audit/audit.sh"
  chmod +x "$d/skills/audit/audit.sh"
  for f in a.txt b.txt gone.txt old.txt g1.txt 'g[1].txt'; do printf '1\n' > "$d/$f"; done
  printf '#!/bin/sh\n' > "$d/tool.sh"
  printf '# Changelog\n\n## v0.1.0 — 2026-01-01\n- feat(a): the first brick\n' > "$d/CHANGELOG.md"
  if [ "${2:-}" = policy ]; then
    printf 'subject_advise = 40\nsubject_block = 60\n' > "$d/.commit-conventions.toml"
  fi
  git -C "$d" add -A
  git -C "$d" commit -qm 'base'
  git -C "$d" checkout -q -b feat
  for f in a.txt b.txt g1.txt 'g[1].txt'; do printf '2\n' > "$d/$f"; done
  git -C "$d" rm -q gone.txt
  git -C "$d" mv old.txt new.txt
  chmod +x "$d/tool.sh"
  git -C "$d" add -A
  git -C "$d" commit -qm 'feat: everything'
  git -C "$d" checkout -q dev
}

count_commits() { git -C "$1" rev-list --count dev; }
porcelain_lines() { git -C "$1" status --porcelain | wc -l | tr -d ' '; }

# ---------- dev: a two-brick re-derivation converges ----------
r="$tmproot/dev-happy"; mkdev "$r"; oracle="$(sha_of "$r" feat)"
out="$(cd "$r" && bash "$engine" --dev "$oracle" 'feat(a): bump a and b' a.txt b.txt 2>&1)"; rc=$?
check_eq "$rc" 0 'dev brick 1 exits 0'
check_has "$out" 'RESULT: PASS rc=0 brick=dev' 'dev brick 1 prints the dev PASS verdict'
check_eq "$(git -C "$r" rev-parse --abbrev-ref HEAD)" 'dev' 'dev brick leaves HEAD on dev'
check_eq "$(git -C "$r" log -1 --format=%s dev)" 'feat(a): bump a and b' 'dev brick commit subject'
check_eq "$(git -C "$r" show --name-only --format= dev | LC_ALL=C sort | tr '\n' ' ')" 'a.txt b.txt ' \
  'dev brick 1 touches exactly its files'
check_eq "$(git -C "$r" tag -l | wc -l | tr -d ' ')" 0 'dev brick mints no tag'
check_eq "$(git -C "$r" rev-parse dev:CHANGELOG.md)" "$(git -C "$r" rev-parse dev~1:CHANGELOG.md)" \
  'dev brick writes no CHANGELOG entry'
check_has "$out" 'remaining: 6 path(s) still differ from the oracle' 'dev brick reports what remains'

out="$(cd "$r" && bash "$engine" --dev --final "$oracle" 'feat(tree): drop, move and chmod the rest' \
  gone.txt old.txt new.txt tool.sh g1.txt 'g[1].txt' 2>&1)"; rc=$?
check_eq "$rc" 0 'dev brick 2 with --final exits 0'
check_has "$out" "converged: HEAD tree == oracle $oracle" 'dev --final reports convergence'
if git -C "$r" diff --quiet "$oracle" dev; then
  pass_line 'two dev bricks converge dev on the oracle'
else
  fail_line 'two dev bricks converge dev on the oracle'
fi
check_eq "$(git -C "$r" ls-tree dev tool.sh | cut -c1-6)" '100755' 'dev brick carries the exec bit'
check_eq "$(porcelain_lines "$r")" 0 'dev bricks leave the tree clean'
check_eq "$(count_commits "$r")" 3 'two dev bricks made exactly two commits'

# ---------- dev: literal pathspecs ----------
r="$tmproot/dev-glob"; mkdev "$r"; oracle="$(sha_of "$r" feat)"
out="$(cd "$r" && bash "$engine" --dev "$oracle" 'feat(g): bump the bracketed file' 'g[1].txt' 2>&1)"; rc=$?
check_eq "$rc" 0 'dev brick named with glob characters builds'
check_eq "$(git -C "$r" show dev:g1.txt)" '1' 'dev glob-named brick leaves its lookalike untouched'

# ---------- dev: the progress invariant ----------
# dev moved after the branch was cut, on a path the oracle also changes
r="$tmproot/dev-moved"; mkdev "$r"; oracle="$(sha_of "$r" feat)"
printf 'interim\n' > "$r/b.txt"; git -C "$r" commit -qam 'chore(b): interim work on dev'
out="$(cd "$r" && bash "$engine" --dev "$oracle" 'feat(a): bump a' a.txt 2>&1)"; rc=$?
check_eq "$rc" 1 'dev mode refuses when dev moved on a path the oracle changes'
check_has "$out" '  diverged: b.txt' 'dev-moved refusal names the diverged path'
check_eq "$(count_commits "$r")" 2 'dev-moved refusal created no commit'
check_eq "$(porcelain_lines "$r")" 0 'dev-moved refusal leaves the tree clean'

# dev moved on a path the oracle never touches — the shape tip convergence would silently REVERT
r="$tmproot/dev-moved-elsewhere"; mkdev "$r"; oracle="$(sha_of "$r" feat)"
printf 'interim\n' > "$r/extra.txt"; git -C "$r" add extra.txt
git -C "$r" commit -qm 'chore(extra): interim file on dev'
out="$(cd "$r" && bash "$engine" --dev "$oracle" 'feat(a): bump a' a.txt 2>&1)"; rc=$?
check_eq "$rc" 1 'dev mode refuses when dev moved on a path the oracle never touches'
check_has "$out" '  diverged: extra.txt' 'the untouched-path refusal names that path'

# a non-ASCII path: git's default name-only output quotes/escapes it (e.g. "caf\303\251.txt"),
# which then never matches the literal pathspec passed to git_lit — so without NUL-separated
# names this divergence built RESULT: PASS (measured by the reviewer 2026-09-16)
r="$tmproot/dev-moved-nonascii"; mkdev "$r"; oracle="$(sha_of "$r" feat)"
printf 'interim\n' > "$r/café.txt"; git -C "$r" add café.txt
git -C "$r" commit -qm 'chore(cafe): interim non-ASCII file on dev'
out="$(cd "$r" && bash "$engine" --dev "$oracle" 'feat(a): bump a' a.txt 2>&1)"; rc=$?
check_eq "$rc" 1 'dev mode refuses when dev moved on a non-ASCII path'
check_has "$out" '  diverged: café.txt' 'the non-ASCII refusal names the diverged path exactly'

# ---------- dev: every brick file must differ from HEAD ----------
r="$tmproot/dev-files"; mkdev "$r"; oracle="$(sha_of "$r" feat)"
out="$(cd "$r" && bash "$engine" --dev "$oracle" 'feat(a): bump a' a.txt no-such.txt 2>&1)"; rc=$?
check_eq "$rc" 1 'dev mode refuses a path in neither tree'
check_has "$out" 'brick file no-such.txt already matches' 'the neither-tree refusal names the path'
# paired with a file that DOES differ, so a missing check would yield a silently smaller brick
# (rc 0) rather than an empty commit that fails anyway
out="$(cd "$r" && bash "$engine" --dev "$oracle" 'feat(x): nothing to do' .publication.toml a.txt 2>&1)"; rc=$?
check_eq "$rc" 1 'dev mode refuses a file that already matches the oracle'
check_has "$out" 'brick file .publication.toml already matches' 'the already-matches refusal names the path'
out="$(cd "$r" && bash "$engine" --dev "$oracle" 'feat(x): outside' /etc/hosts 2>&1)"; rc=$?
check_eq "$rc" 1 'dev mode refuses an absolute path'
check_has "$out" 'relative to the repo root' 'the absolute-path refusal says why'
check_eq "$(count_commits "$r")" 1 'refused dev bricks created no commit'
check_eq "$(porcelain_lines "$r")" 0 'refused dev bricks leave the tree clean'

# a directory listed as a brick file: without an explicit refusal, "dir" as a pathspec matches
# everything under it, shape B then trips over dir/y.txt (not literally "dir") and can leave a
# staged leftover behind — a real leftover the reviewer measured 2026-09-16
r="$tmproot/dev-dir"; mkdev "$r"
git -C "$r" checkout -q feat
mkdir -p "$r/dir"; printf 'y\n' > "$r/dir/y.txt"
git -C "$r" add dir/y.txt && git -C "$r" commit -qm 'feat(dir): add dir/y.txt'
git -C "$r" checkout -q dev
oracle="$(sha_of "$r" feat)"
out="$(cd "$r" && bash "$engine" --dev "$oracle" 'feat(a): bump a' dir 2>&1)"; rc=$?
check_eq "$rc" 1 'dev mode refuses a directory as a brick file'
check_has "$out" 'brick file dir is a directory' 'the directory refusal names the reason'
check_eq "$(count_commits "$r")" 1 'the directory refusal created no commit'
check_eq "$(porcelain_lines "$r")" 0 'the directory refusal leaves the tree clean'

# a file BECOMING a directory is a legitimate change, not the directory refusal above: `a.txt` is
# a blob at HEAD, so the refusal (tree on one side, neither side blob/commit on the other) must
# not fire when the other side names a real blob
r="$tmproot/dev-filetodir"; mkdev "$r"
git -C "$r" checkout -q feat
git -C "$r" rm -q a.txt
mkdir -p "$r/a.txt"; printf 'q\n' > "$r/a.txt/in"
git -C "$r" add -A && git -C "$r" commit -qm 'feat(a): turn a.txt into a directory'
git -C "$r" checkout -q dev
oracle="$(sha_of "$r" feat)"
out="$(cd "$r" && bash "$engine" --dev "$oracle" 'feat(a): file to dir' a.txt a.txt/in 2>&1)"; rc=$?
check_eq "$rc" 0 'a file becoming a directory builds when both its old and new paths are listed'
if git -C "$r" cat-file -e dev:a.txt/in 2>/dev/null; then
  pass_line 'the converted path exists at its new location on dev'
else
  fail_line 'the converted path exists at its new location on dev'
fi

# ---------- dev: branch and tree preconditions ----------
r="$tmproot/dev-branch"; mkdev "$r"; oracle="$(sha_of "$r" feat)"; git -C "$r" checkout -q feat
out="$(cd "$r" && bash "$engine" --dev "$oracle" 'feat(a): bump a' a.txt 2>&1)"; rc=$?
check_eq "$rc" 1 'dev mode refuses when not on dev'
check_has "$out" 'not on branch dev' 'the branch refusal names dev'

r="$tmproot/dev-dirty"; mkdev "$r"; oracle="$(sha_of "$r" feat)"; printf 'scratch\n' > "$r/scratch.txt"
out="$(cd "$r" && bash "$engine" --dev "$oracle" 'feat(a): bump a' a.txt 2>&1)"; rc=$?
check_eq "$rc" 1 'dev mode refuses a dirty tree'

# a repo with no audit.sh of its own cannot be proven, so dev mode refuses BEFORE the commit —
# not by falling through into run_audit's own missing-audit check after the fact
r="$tmproot/dev-noaudit"; mkdev "$r"; oracle="$(sha_of "$r" feat)"
git -C "$r" rm -q skills/audit/audit.sh && git -C "$r" commit -qm 'chore(audit): drop the audit'
out="$(cd "$r" && bash "$engine" --dev "$oracle" 'feat(a): bump a' a.txt 2>&1)"; rc=$?
check_eq "$rc" 1 'dev mode refuses a repo with no audit before committing'
check_has "$out" 'no executable audit' 'the missing-audit refusal names the reason'
check_eq "$(count_commits "$r")" 2 'the missing-audit refusal created no commit'
check_eq "$(porcelain_lines "$r")" 0 'the missing-audit refusal leaves the tree clean'

# a brick that DELETES skills/audit/audit.sh would pass the pre-commit run_audit call (it still
# reads the repo's LIVE copy, present until the commit lands) and only fail once committed, with
# nothing left to prove the next brick — refused before it commits instead
r="$tmproot/dev-audit-deleted"; mkdev "$r"
git -C "$r" checkout -q feat
git -C "$r" rm -q skills/audit/audit.sh
git -C "$r" commit -qm 'feat(audit): drop the audit'
git -C "$r" checkout -q dev
oracle="$(sha_of "$r" feat)"
out="$(cd "$r" && bash "$engine" --dev "$oracle" 'chore(audit): drop audit' skills/audit/audit.sh 2>&1)"; rc=$?
check_eq "$rc" 1 'a brick that deletes the audit refuses before it commits'
check_has "$out" "brick file skills/audit/audit.sh would leave no executable audit" \
  'the audit-loss refusal names the reason'
check_eq "$(count_commits "$r")" 1 'the audit-loss refusal created no commit'
check_eq "$(porcelain_lines "$r")" 0 'the audit-loss refusal leaves the tree clean'

# ---------- dev: the subject ----------
r="$tmproot/dev-subject"; mkdev "$r"; oracle="$(sha_of "$r" feat)"
out="$(cd "$r" && bash "$engine" --dev "$oracle" 'bump a' a.txt 2>&1)"; rc=$?
check_eq "$rc" 1 'dev mode refuses a non-conventional subject'
check_has "$out" 'not conventional' 'the conventional refusal says so'
out="$(cd "$r" && bash "$engine" --dev "$oracle" 'feat(a): bump a.' a.txt 2>&1)"; rc=$?
check_eq "$rc" 1 'dev mode refuses a subject ending in a period'
long='feat(a): a subject that runs well past forty characters'
out="$(cd "$r" && bash "$engine" --dev "$oracle" "$long" a.txt 2>&1)"; rc=$?
check_eq "$rc" 0 'without a policy file a long conventional subject builds'

r="$tmproot/dev-policy"; mkdev "$r" policy; oracle="$(sha_of "$r" feat)"
out="$(cd "$r" && bash "$engine" --dev "$oracle" "$long" a.txt 2>&1)"; rc=$?
check_eq "$rc" 1 'a subject in the ADVISE band of the repo policy is refused'
check_has "$out" 'threshold' 'the policy refusal names the threshold'
out="$(cd "$r" && ALLOW_LONG_SUBJECT=1 bash "$engine" --dev "$oracle" "$long" a.txt 2>&1)"; rc=$?
check_eq "$rc" 0 'ALLOW_LONG_SUBJECT=1 lets an over-threshold subject build, as it does for /commit'
check_has "$out" 'ALLOW_LONG_SUBJECT' 'the override is announced, not silent'
git -C "$r" reset -q --hard HEAD~1
out="$(cd "$r" && bash "$engine" --dev "$oracle" 'feat(a): bump a' a.txt 2>&1)"; rc=$?
check_eq "$rc" 0 'a subject under the policy threshold builds'

# ---------- dev: --final ----------
r="$tmproot/dev-final"; mkdev "$r"; oracle="$(sha_of "$r" feat)"
out="$(cd "$r" && bash "$engine" --dev --final "$oracle" 'feat(a): bump a' a.txt 2>&1)"; rc=$?
check_eq "$rc" 1 '--final fails a brick that does not converge'
check_has "$out" '  still differs: b.txt' '--final names a remaining path'
check_has "$out" 'is proven and stays' '--final failure keeps the proven brick'
check_lacks "$out" 'Recovery (run it yourself)' '--final failure does not dress prose up as a command'
check_lacks "$out" 'reset --hard' '--final failure does not tell the operator to drop the brick'
check_eq "$(count_commits "$r")" 2 '--final failure leaves the landed brick in place'

# ---------- dev: the audit verdict is still an allowlist ----------
for mode in fail killed weird liar trailer; do
  r="$tmproot/dev-audit-$mode"; mkdev "$r"; oracle="$(sha_of "$r" feat)"
  out="$(cd "$r" && AUDIT_MODE="$mode" bash "$engine" --dev "$oracle" 'feat(a): bump a' a.txt 2>&1)"; rc=$?
  check_eq "$rc" 1 "dev audit mode '$mode' fails the brick"
  check_has "$out" 'RESULT: FAIL rc=1 brick=dev' "dev audit mode '$mode' prints the dev FAIL verdict"
  check_has "$out" 'reset --hard HEAD~1' "dev audit mode '$mode' names the drop-the-brick recovery"
  check_lacks "$out" 'tag -d' "dev audit mode '$mode' recovery never mentions a tag"
done

# ---------- dev: artifact ----------
r="$tmproot/dev-artifact"; mkdev "$r"; oracle="$(sha_of "$r" feat)"; adir="$tmproot/dev-artifacts"
pre="$(git -C "$r" rev-parse --short HEAD)"
out="$(cd "$r" && bash "$engine" --dev --artifact-dir "$adir" "$oracle" 'feat(a): bump a' a.txt 2>&1)"
check_has "$(cat "$adir/audit-dev-$pre.txt" 2>/dev/null)" 'AUDIT_EXIT_STATUS=0' \
  'dev artifact is named for the pre-brick HEAD and records the audit status'

# ---------- dev: usage ----------
r="$tmproot/dev-usage"; mkdev "$r"; oracle="$(sha_of "$r" feat)"
out="$(cd "$r" && bash "$engine" --dev "$oracle" 'feat(a): bump a' 2>&1)"; rc=$?
check_eq "$rc" 2 'dev mode with no files is a usage error'
out="$(cd "$r" && bash "$engine" --final v0.2.0 "$oracle" 'feat(a): x' 2>&1)"; rc=$?
check_eq "$rc" 2 '--final without --dev is a usage error'
out="$(cd "$r" && bash "$engine" --dev not-a-commit 'feat(a): bump a' a.txt 2>&1)"; rc=$?
check_eq "$rc" 1 'dev mode refuses an oracle that is not a commit'
check_has "$out" 'is not a commit in' 'the not-a-commit refusal names the reason'

# a file positional that looks like a flag never reaches --final's own parsing: the main flag
# loop already broke on the first non-flag argument (the oracle), so this is a usage error, not
# a silent extra brick file
r="$tmproot/dev-dashfile"; mkdev "$r"; oracle="$(sha_of "$r" feat)"
out="$(cd "$r" && bash "$engine" --dev "$oracle" 'feat(a): bump a' a.txt --final 2>&1)"; rc=$?
check_eq "$rc" 2 'a brick file name starting with a dash is a usage error'
check_has "$out" 'looks like a flag' 'the flag-like file name refusal says so'

# a real file named with a SINGLE leading dash has no workaround if the refusal matches `-*` —
# the engine has no short flags, so only `--*` is ever a flag lookalike worth refusing
r="$tmproot/dev-dashfile-legit"; mkdev "$r"
git -C "$r" checkout -q feat
printf 'z\n' > "$r/-x.txt"
git -C "$r" add -- -x.txt
git -C "$r" commit -qm 'feat(x): add a dash-prefixed file'
git -C "$r" checkout -q dev
oracle="$(sha_of "$r" feat)"
out="$(cd "$r" && bash "$engine" --dev "$oracle" 'feat(x): add dash file' -x.txt 2>&1)"; rc=$?
check_eq "$rc" 0 'a legitimate file named with a single leading dash builds'

printf '\n%d passed, %d failed\n' "$pass" "$fail"
[ "$fail" -eq 0 ]

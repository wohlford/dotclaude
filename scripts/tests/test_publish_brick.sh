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

# shellcheck disable=SC2016  # a literal script body; $AUDIT_MODE must NOT expand here
AUDIT_STUB='#!/usr/bin/env bash
# Stub standing in for skills/audit/audit.sh. $AUDIT_MODE selects the verdict shape.
case "${AUDIT_MODE-pass}" in
  pass)   printf "PASS format-tabs\n"; printf "RESULT: PASS rc=0 checks=1/0/0\n"; exit 0 ;;
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

# a constituent that DELETES a file: `git checkout <endpoint> -- <files>` cannot express it
r="$tmproot/del"; mk "$r"
git -C "$r" checkout -q dev && git -C "$r" rm -q b.txt && git -C "$r" commit -qm 'c5 drop b'
del="$(sha_of "$r" dev)"; git -C "$r" checkout -q main
out="$(cd "$r" && bash "$engine" v0.2.0 "$del" 'feat(b): drop b' 2>&1)"; rc=$?
check_eq "$rc" 1 'refuses a constituent that deletes a file'
check_has "$out" 'delet' 'deletion refusal says what it found'
check_eq "$(git -C "$r" status --porcelain | wc -l | tr -d ' ')" 0 'deletion refusal leaves the tree clean'
check_eq "$(git -C "$r" rev-list --count main)" 1 'deletion refusal created no commit'

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

printf '\n%d passed, %d failed\n' "$pass" "$fail"
[ "$fail" -eq 0 ]

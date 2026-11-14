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
# "resolve <scope>/skills/audit/audit.sh and read its verdict", not a mockable function. The one
# other seam is dev mode's `--audit <path>`, legal only in a scope with no audit of its own: its
# stub lives OUTSIDE every fixture repo, and the engine's signing assertion is exercised with a
# real ssh signature (one row), a fake signer that signs but never verifies, and a failing signer.
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
skip=0
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
check_has "$out" "  audit:   $r/skills/audit/audit.sh (the tree being proven)" 'A10 publish mode prints the byte-identical audit header'
check_lacks "$out" 'audit sha256' 'A10 publish mode prints no dev-only audit line'
check_has "$out" 'signing not enabled in this repo)' 'S7 publish mode with signing off prints the quiet line unchanged'

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
check_lacks "$(cat "$art" 2>/dev/null)" 'AUDIT_SHA256' 'A10 publish artifact carries no dev-only audit lines'
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

# mkdev DIR [variant] -> an adopted repo on `dev` with one base commit and a branch `feat` whose tip is
# the oracle. The oracle modifies a.txt and b.txt, deletes gone.txt, renames old.txt -> new.txt, sets
# tool.sh's exec bit, and bumps BOTH g[1].txt and its glob lookalike g1.txt. The base carries a
# CHANGELOG.md so "dev mode writes no entry" is observable. With `policy`, the base also carries a
# .commit-conventions.toml advising at 40 characters and blocking at 60. Other variants, each built so
# the oracle (`feat`) and `dev` AGREE: `noaudit` (the base never holds skills/audit/audit.sh),
# `audit644` (base and oracle carry it at mode 100644), `auditlink` (both carry a dangling symlink there),
# `linkdir` (both carry skills/audit as a symlink to ../tools/audit, which holds the audit), `linkdangling` (both
# carry skills/audit as a symlink to a directory that is not there), `skillslink` (both carry skills as a symlink to
# tools, which holds audit/audit.sh), `skillsother` (the base holds skills/other.txt and no audit),
# `casevariant` (both carry the audit as Skills/audit/audit.sh, a case-variant spelling that a volume folding case
# resolves as skills/audit/audit.sh), `subnoaudit` (no audit, and a submodule entry at vendor/lib with an empty
# directory, in the base and the oracle), `subown` (the ordinary own audit and that same submodule entry),
# `unsetsign` (the local config omits commit.gpgsign). $MKDEV_STUB, when set, replaces AUDIT_STUB as the
# audit body. The variant is an ALLOWLIST: any other name (a typo such as `noaduit`) prints a message on stderr
# and exits 2. No caller runs mkdev inside a command substitution, so that exit ends the suite visibly (no summary
# line, a non-zero status) instead of being swallowed; the self-check row below runs it in a subshell on purpose.
mkdev() {
  local d="$1" variant="${2:-}" f stub="${MKDEV_STUB:-$AUDIT_STUB}"
  case "$variant" in
    ''|policy|noaudit|audit644|auditlink|linkdir|linkdangling|skillslink|skillsother|casevariant|subnoaudit|subown|unsetsign) ;;
    *) printf 'mkdev: unknown variant: %s\n' "$variant" >&2; exit 2 ;;
  esac
  mkdir -p "$d"
  git init -q -b dev "$d"
  git -C "$d" config user.email test@test.invalid
  git -C "$d" config user.name test
  git -C "$d" config commit.gpgsign false
  git -C "$d" config tag.gpgsign false
  printf 'production = "dev"\n' > "$d/.publication.toml"
  case "$variant" in
    noaudit|subnoaudit) ;;
    auditlink)
      mkdir -p "$d/skills/audit"
      ln -s "$tmproot/no-such-audit-target" "$d/skills/audit/audit.sh" ;;
    linkdir)
      mkdir -p "$d/tools/audit" "$d/skills"
      printf '%s' "$stub" > "$d/tools/audit/audit.sh"; chmod +x "$d/tools/audit/audit.sh"
      ln -s ../tools/audit "$d/skills/audit" ;;
    linkdangling)
      mkdir -p "$d/skills"
      ln -s "$tmproot/no-such-audit-dir" "$d/skills/audit" ;;
    skillslink)
      mkdir -p "$d/tools/audit"
      printf '%s' "$stub" > "$d/tools/audit/audit.sh"; chmod +x "$d/tools/audit/audit.sh"
      ln -s tools "$d/skills" ;;
    skillsother)
      mkdir -p "$d/skills"
      printf 'x\n' > "$d/skills/other.txt" ;;
    casevariant)
      mkdir -p "$d/Skills/audit"
      printf '%s' "$stub" > "$d/Skills/audit/audit.sh"; chmod +x "$d/Skills/audit/audit.sh" ;;
    *)
      mkdir -p "$d/skills/audit"
      printf '%s' "$stub" > "$d/skills/audit/audit.sh"
      if [ "$variant" != audit644 ]; then chmod +x "$d/skills/audit/audit.sh"; fi ;;
  esac
  for f in a.txt b.txt gone.txt old.txt g1.txt 'g[1].txt'; do printf '1\n' > "$d/$f"; done
  printf '#!/bin/sh\n' > "$d/tool.sh"
  printf '# Changelog\n\n## v0.1.0 — 2026-01-01\n- feat(a): the first brick\n' > "$d/CHANGELOG.md"
  if [ "$variant" = policy ]; then
    printf 'subject_advise = 40\nsubject_block = 60\n' > "$d/.commit-conventions.toml"
  fi
  git -C "$d" add -A
  case "$variant" in
    subnoaudit|subown)
      mkdir -p "$d/vendor/lib"
      git -C "$d" update-index --add --cacheinfo "160000,e69de29bb2d1d6434b8b29ae775ad8c2e48c5391,vendor/lib" ;;
  esac
  git -C "$d" commit -qm 'base'
  git -C "$d" checkout -q -b feat
  for f in a.txt b.txt g1.txt 'g[1].txt'; do printf '2\n' > "$d/$f"; done
  git -C "$d" rm -q gone.txt
  git -C "$d" mv old.txt new.txt
  chmod +x "$d/tool.sh"
  git -C "$d" add -A
  git -C "$d" commit -qm 'feat: everything'
  git -C "$d" checkout -q dev
  if [ "$variant" = unsetsign ]; then
    # From here this fixture inherits the operator's GLOBAL commit.gpgsign=true and signing key. Every git write
    # against it must run under a per-call HOME and XDG_CONFIG_HOME override (empty directories, which is what
    # hides the global key; row S10 does this) and under `timeout`, or it prompts the real key.
    git -C "$d" config --unset commit.gpgsign
  fi
}

count_commits() { git -C "$1" rev-list --count dev; }
porcelain_lines() { git -C "$1" status --porcelain 2>/dev/null | wc -l | tr -d ' '; }

# Dev rows that do not exercise the signing assertion pass the opt-out through this variable, written
# QUOTED at every call (the unquoted form is a shellcheck note the style-check hook blocks). The rows
# named S1, S2, S4 to S6, S9, S10 and O2 further down are the ones that exercise the default.
unsigned=--allow-unsigned

# ---------- dev: a two-brick re-derivation converges ----------
r="$tmproot/dev-happy"; mkdev "$r"; oracle="$(sha_of "$r" feat)"
out="$(cd "$r" && bash "$engine" --dev "$unsigned" "$oracle" 'feat(a): bump a and b' a.txt b.txt 2>&1)"; rc=$?
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

out="$(cd "$r" && bash "$engine" --dev "$unsigned" --final "$oracle" 'feat(tree): drop, move and chmod the rest' \
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
out="$(cd "$r" && bash "$engine" --dev "$unsigned" "$oracle" 'feat(g): bump the bracketed file' 'g[1].txt' 2>&1)"; rc=$?
check_eq "$rc" 0 'dev brick named with glob characters builds'
check_eq "$(git -C "$r" show dev:g1.txt)" '1' 'dev glob-named brick leaves its lookalike untouched'

# ---------- dev: the progress invariant ----------
# dev moved after the branch was cut, on a path the oracle also changes
r="$tmproot/dev-moved"; mkdev "$r"; oracle="$(sha_of "$r" feat)"
printf 'interim\n' > "$r/b.txt"; git -C "$r" commit -qam 'chore(b): interim work on dev'
out="$(cd "$r" && bash "$engine" --dev "$unsigned" "$oracle" 'feat(a): bump a' a.txt 2>&1)"; rc=$?
check_eq "$rc" 1 'dev mode refuses when dev moved on a path the oracle changes'
check_has "$out" '  diverged: b.txt' 'dev-moved refusal names the diverged path'
check_eq "$(count_commits "$r")" 2 'dev-moved refusal created no commit'
check_eq "$(porcelain_lines "$r")" 0 'dev-moved refusal leaves the tree clean'

# dev moved on a path the oracle never touches — the shape tip convergence would silently REVERT
r="$tmproot/dev-moved-elsewhere"; mkdev "$r"; oracle="$(sha_of "$r" feat)"
printf 'interim\n' > "$r/extra.txt"; git -C "$r" add extra.txt
git -C "$r" commit -qm 'chore(extra): interim file on dev'
out="$(cd "$r" && bash "$engine" --dev "$unsigned" "$oracle" 'feat(a): bump a' a.txt 2>&1)"; rc=$?
check_eq "$rc" 1 'dev mode refuses when dev moved on a path the oracle never touches'
check_has "$out" '  diverged: extra.txt' 'the untouched-path refusal names that path'

# a non-ASCII path: git's default name-only output quotes/escapes it (e.g. "caf\303\251.txt"),
# which then never matches the literal pathspec passed to git_lit — so without NUL-separated
# names this divergence built RESULT: PASS (measured by the reviewer 2026-09-16)
r="$tmproot/dev-moved-nonascii"; mkdev "$r"; oracle="$(sha_of "$r" feat)"
printf 'interim\n' > "$r/café.txt"; git -C "$r" add café.txt
git -C "$r" commit -qm 'chore(cafe): interim non-ASCII file on dev'
out="$(cd "$r" && bash "$engine" --dev "$unsigned" "$oracle" 'feat(a): bump a' a.txt 2>&1)"; rc=$?
check_eq "$rc" 1 'dev mode refuses when dev moved on a non-ASCII path'
check_has "$out" '  diverged: café.txt' 'the non-ASCII refusal names the diverged path exactly'

# ---------- dev: every brick file must differ from HEAD ----------
r="$tmproot/dev-files"; mkdev "$r"; oracle="$(sha_of "$r" feat)"
out="$(cd "$r" && bash "$engine" --dev "$unsigned" "$oracle" 'feat(a): bump a' a.txt no-such.txt 2>&1)"; rc=$?
check_eq "$rc" 1 'dev mode refuses a path in neither tree'
check_has "$out" 'brick file no-such.txt already matches' 'the neither-tree refusal names the path'
# paired with a file that DOES differ, so a missing check would yield a silently smaller brick
# (rc 0) rather than an empty commit that fails anyway
out="$(cd "$r" && bash "$engine" --dev "$unsigned" "$oracle" 'feat(x): nothing to do' .publication.toml a.txt 2>&1)"; rc=$?
check_eq "$rc" 1 'dev mode refuses a file that already matches the oracle'
check_has "$out" 'brick file .publication.toml already matches' 'the already-matches refusal names the path'
out="$(cd "$r" && bash "$engine" --dev "$unsigned" "$oracle" 'feat(x): outside' /etc/hosts 2>&1)"; rc=$?
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
out="$(cd "$r" && bash "$engine" --dev "$unsigned" "$oracle" 'feat(a): bump a' dir 2>&1)"; rc=$?
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
out="$(cd "$r" && bash "$engine" --dev "$unsigned" "$oracle" 'feat(a): file to dir' a.txt a.txt/in 2>&1)"; rc=$?
check_eq "$rc" 0 'a file becoming a directory builds when both its old and new paths are listed'
if git -C "$r" cat-file -e dev:a.txt/in 2>/dev/null; then
  pass_line 'the converted path exists at its new location on dev'
else
  fail_line 'the converted path exists at its new location on dev'
fi

# ---------- dev: branch and tree preconditions ----------
r="$tmproot/dev-branch"; mkdev "$r"; oracle="$(sha_of "$r" feat)"; git -C "$r" checkout -q feat
out="$(cd "$r" && bash "$engine" --dev "$unsigned" "$oracle" 'feat(a): bump a' a.txt 2>&1)"; rc=$?
check_eq "$rc" 1 'dev mode refuses when not on dev'
check_has "$out" 'not on branch dev' 'the branch refusal names dev'

r="$tmproot/dev-dirty"; mkdev "$r"; oracle="$(sha_of "$r" feat)"; printf 'scratch\n' > "$r/scratch.txt"
out="$(cd "$r" && bash "$engine" --dev "$unsigned" "$oracle" 'feat(a): bump a' a.txt 2>&1)"; rc=$?
check_eq "$rc" 1 'dev mode refuses a dirty tree'

# a repo with no audit.sh of its own cannot be proven, so dev mode refuses BEFORE the commit —
# not by falling through into run_audit's own missing-audit check after the fact
r="$tmproot/dev-noaudit"; mkdev "$r"; oracle="$(sha_of "$r" feat)"
git -C "$r" rm -q skills/audit/audit.sh && git -C "$r" commit -qm 'chore(audit): drop the audit'
out="$(cd "$r" && bash "$engine" --dev "$unsigned" "$oracle" 'feat(a): bump a' a.txt 2>&1)"; rc=$?
check_eq "$rc" 1 'dev mode refuses a repo with no audit before committing'
check_has "$out" 'no executable audit' 'the missing-audit refusal names the reason'
check_has "$out" ': absent' 'A6 the missing-audit refusal says the file is absent'
check_has "$out" '--audit' 'A6 the missing-audit refusal names the --audit flag'
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
out="$(cd "$r" && bash "$engine" --dev "$unsigned" "$oracle" 'chore(audit): drop audit' skills/audit/audit.sh 2>&1)"; rc=$?
check_eq "$rc" 1 'a brick that deletes the audit refuses before it commits'
check_has "$out" "brick file skills/audit/audit.sh would leave no executable audit" \
  'the audit-loss refusal names the reason'
check_eq "$(count_commits "$r")" 1 'the audit-loss refusal created no commit'
check_eq "$(porcelain_lines "$r")" 0 'the audit-loss refusal leaves the tree clean'

# ---------- dev: the subject ----------
r="$tmproot/dev-subject"; mkdev "$r"; oracle="$(sha_of "$r" feat)"
out="$(cd "$r" && bash "$engine" --dev "$unsigned" "$oracle" 'bump a' a.txt 2>&1)"; rc=$?
check_eq "$rc" 1 'dev mode refuses a non-conventional subject'
check_has "$out" 'not conventional' 'the conventional refusal says so'
out="$(cd "$r" && bash "$engine" --dev "$unsigned" "$oracle" 'feat(a): bump a.' a.txt 2>&1)"; rc=$?
check_eq "$rc" 1 'dev mode refuses a subject ending in a period'
long='feat(a): a subject that runs well past forty characters'
out="$(cd "$r" && bash "$engine" --dev "$unsigned" "$oracle" "$long" a.txt 2>&1)"; rc=$?
check_eq "$rc" 0 'without a policy file a long conventional subject builds'

r="$tmproot/dev-policy"; mkdev "$r" policy; oracle="$(sha_of "$r" feat)"
out="$(cd "$r" && bash "$engine" --dev "$unsigned" "$oracle" "$long" a.txt 2>&1)"; rc=$?
check_eq "$rc" 1 'a subject in the ADVISE band of the repo policy is refused'
check_has "$out" 'threshold' 'the policy refusal names the threshold'
out="$(cd "$r" && ALLOW_LONG_SUBJECT=1 bash "$engine" --dev "$unsigned" "$oracle" "$long" a.txt 2>&1)"; rc=$?
check_eq "$rc" 0 'ALLOW_LONG_SUBJECT=1 lets an over-threshold subject build, as it does for /commit'
check_has "$out" 'ALLOW_LONG_SUBJECT' 'the override is announced, not silent'
git -C "$r" reset -q --hard HEAD~1
out="$(cd "$r" && bash "$engine" --dev "$unsigned" "$oracle" 'feat(a): bump a' a.txt 2>&1)"; rc=$?
check_eq "$rc" 0 'a subject under the policy threshold builds'

# ---------- dev: --final ----------
r="$tmproot/dev-final"; mkdev "$r"; oracle="$(sha_of "$r" feat)"
out="$(cd "$r" && bash "$engine" --dev "$unsigned" --final "$oracle" 'feat(a): bump a' a.txt 2>&1)"; rc=$?
check_eq "$rc" 1 '--final fails a brick that does not converge'
check_has "$out" '  still differs: b.txt' '--final names a remaining path'
check_has "$out" 'is proven and stays' '--final failure keeps the proven brick'
check_lacks "$out" 'Recovery (run it yourself)' '--final failure does not dress prose up as a command'
check_lacks "$out" 'reset --hard' '--final failure does not tell the operator to drop the brick'
check_eq "$(count_commits "$r")" 2 '--final failure leaves the landed brick in place'

# ---------- dev: the audit verdict is still an allowlist ----------
for mode in fail killed weird liar trailer; do
  r="$tmproot/dev-audit-$mode"; mkdev "$r"; oracle="$(sha_of "$r" feat)"
  out="$(cd "$r" && AUDIT_MODE="$mode" bash "$engine" --dev "$unsigned" "$oracle" 'feat(a): bump a' a.txt 2>&1)"; rc=$?
  check_eq "$rc" 1 "dev audit mode '$mode' fails the brick"
  check_has "$out" 'RESULT: FAIL rc=1 brick=dev' "dev audit mode '$mode' prints the dev FAIL verdict"
  check_has "$out" 'reset --hard HEAD~1' "dev audit mode '$mode' names the drop-the-brick recovery"
  check_lacks "$out" 'tag -d' "dev audit mode '$mode' recovery never mentions a tag"
done

# ---------- dev: artifact ----------
r="$tmproot/dev-artifact"; mkdev "$r"; oracle="$(sha_of "$r" feat)"; adir="$tmproot/dev-artifacts"
pre="$(git -C "$r" rev-parse --short HEAD)"
out="$(cd "$r" && bash "$engine" --dev "$unsigned" --artifact-dir "$adir" "$oracle" 'feat(a): bump a' a.txt 2>&1)"
check_has "$(cat "$adir/audit-dev-$pre.txt" 2>/dev/null)" 'AUDIT_EXIT_STATUS=0' \
  'dev artifact is named for the pre-brick HEAD and records the audit status'

# ---------- dev: usage ----------
r="$tmproot/dev-usage"; mkdev "$r"; oracle="$(sha_of "$r" feat)"
out="$(cd "$r" && bash "$engine" --dev "$unsigned" "$oracle" 'feat(a): bump a' 2>&1)"; rc=$?
check_eq "$rc" 2 'dev mode with no files is a usage error'
out="$(cd "$r" && bash "$engine" --final v0.2.0 "$oracle" 'feat(a): x' 2>&1)"; rc=$?
check_eq "$rc" 2 '--final without --dev is a usage error'
out="$(cd "$r" && bash "$engine" --dev "$unsigned" not-a-commit 'feat(a): bump a' a.txt 2>&1)"; rc=$?
check_eq "$rc" 1 'dev mode refuses an oracle that is not a commit'
check_has "$out" 'is not a commit in' 'the not-a-commit refusal names the reason'

# a file positional that looks like a flag never reaches --final's own parsing: the main flag
# loop already broke on the first non-flag argument (the oracle), so this is a usage error, not
# a silent extra brick file
r="$tmproot/dev-dashfile"; mkdev "$r"; oracle="$(sha_of "$r" feat)"
out="$(cd "$r" && bash "$engine" --dev "$unsigned" "$oracle" 'feat(a): bump a' a.txt --final 2>&1)"; rc=$?
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
out="$(cd "$r" && bash "$engine" --dev "$unsigned" "$oracle" 'feat(x): add dash file' -x.txt 2>&1)"; rc=$?
check_eq "$rc" 0 'a legitimate file named with a single leading dash builds'

# ======================= DEV MODE: the audit override and the signing assertion =======================
# `--audit <path>` is legal only in a scope with no skills/audit/audit.sh of its own. The engine asserts
# commit.gpgsign reads true before it writes anything (`--allow-unsigned` waives only that assertion) and
# prints every brick's %G?. Rows here that carry no "$unsigned" exercise the default.

# ---------- helpers for the rows below ----------
nl=$'\n'
stub_rest="${AUDIT_STUB#*"$nl"}"
make_stub() { # marker-line -> AUDIT_STUB with one extra first line, on stdout
  printf '#!/usr/bin/env bash\n%s\n%s\n' "$1" "$stub_rest"
}
EXT_STUB="$(make_stub 'printf "ext-audit-ran\n"')"
V2_STUB="$(make_stub 'printf "audit-v2-ran\n"')"
# shellcheck disable=SC2016  # a literal stub line; ${AUDIT_ENVDUMP:?} must NOT expand here
DUMP_STUB="$(make_stub 'env | cut -d= -f1 | LC_ALL=C sort > "${AUDIT_ENVDUMP:?}"')"

mkext() { # path [body] -> an executable audit stub at path (EXT_STUB unless a body is given)
  mkdir -p "$(dirname "$1")"
  printf '%s\n' "${2:-$EXT_STUB}" > "$1"
  chmod +x "$1"
}

skip_line() { printf 'SKIP  %s (%s)\n' "$1" "$2"; skip=$((skip + 1)); }

check_before() { # haystack first second label: both occur, and FIRST occurs earlier
  local h="$1" a b
  case "$h" in *"$2"*) ;; *) fail_line "$4 (missing [$2])"; return ;; esac
  case "$h" in *"$3"*) ;; *) fail_line "$4 (missing [$3])"; return ;; esac
  a="${h%%"$2"*}"; b="${h%%"$3"*}"
  if [ "${#a}" -lt "${#b}" ]; then pass_line "$4"; else fail_line "$4 ([$2] does not precede [$3])"; fi
}

# refused2 LABEL MESSAGE FLAG...: a dev run in $r with FLAG... before the oracle must exit 2 with MESSAGE,
# create no commit and leave the tree as it found it. Reads $r and $oracle; sets $out and $rc.
refused2() {
  local label="$1" msg="$2" c0 p0
  shift 2
  c0="$(count_commits "$r")"; p0="$(porcelain_lines "$r")"
  out="$(cd "$r" && bash "$engine" --dev "$@" "$oracle" 'feat(a): bump a' a.txt 2>&1)"; rc=$?
  check_eq "$rc" 2 "$label: exits 2"
  check_has "$out" "$msg" "$label: names the refusal"
  check_eq "$(count_commits "$r")" "$c0" "$label: created no commit"
  check_eq "$(porcelain_lines "$r")" "$p0" "$label: leaves the tree as it found it"
}

# The stub the override rows name: OUTSIDE every fixture repo, one extra first line so an artifact
# shows WHICH audit judged the brick.
ext="$tmproot/ext/audit.sh"; mkext "$ext"
repo_audit="$(cd "$here/../.." && pwd -P)/skills/audit/audit.sh"

# ---------- A1 and A2: the override builds, and its verdict is still an allowlist ----------
r="$tmproot/ov-happy"; mkdev "$r" noaudit; oracle="$(sha_of "$r" feat)"; adir="$tmproot/ov-happy-art"
pre="$(git -C "$r" rev-parse --short HEAD)"
out="$(cd "$r" && bash "$engine" --dev "$unsigned" --audit "$ext" --artifact-dir "$adir" "$oracle" 'feat(a): bump a' a.txt 2>&1)"; rc=$?
art="$(cat "$adir/audit-dev-$pre.txt" 2>/dev/null)"
check_eq "$rc" 0 'A1 override: a brick in an audit-less repo builds with --audit'
check_has "$out" 'RESULT: PASS rc=0 brick=dev' 'A1 override: prints the dev PASS verdict'
check_has "$out" "audit:   $ext (OVERRIDE via --audit" 'A1 override: the header says OVERRIDE and prints the physical path'
check_has "$out" 'audit source: override' 'A1 override: stdout names the audit source'
check_has "$out" '%G?=N' 'A1 override: stdout prints the per-brick %G?'
check_has "$art" 'ext-audit-ran' 'A1 override: the artifact shows the named audit ran'
check_has "$art" 'AUDIT_SOURCE=override' 'A1 override: the artifact records the source'
check_has "$art" "AUDIT_PATH=$ext" 'A1 override: the artifact records the physical path'

for mode in fail killed weird liar trailer; do
  r="$tmproot/ov-audit-$mode"; mkdev "$r" noaudit; oracle="$(sha_of "$r" feat)"
  out="$(cd "$r" && AUDIT_MODE="$mode" bash "$engine" --dev "$unsigned" --audit "$ext" "$oracle" 'feat(a): bump a' a.txt 2>&1)"; rc=$?
  check_eq "$rc" 1 "A2 override audit mode '$mode' fails the brick"
  check_has "$out" 'RESULT: FAIL rc=1 brick=dev' "A2 override audit mode '$mode' prints the dev FAIL verdict"
  check_has "$out" 'reset --hard HEAD~1' "A2 override audit mode '$mode' names the drop-the-brick recovery"
done

# ---------- A3 and A15: what --audit refuses (usage errors, exit 2, nothing written) ----------
r="$tmproot/ov-refuse"; mkdev "$r" noaudit; oracle="$(sha_of "$r" feat)"
mkdir -p "$r/inscope" "$tmproot/ext/adir"
printf 'inscope/\n' >> "$r/.git/info/exclude"   # the in-scope stub is IGNORED, so the tree stays clean
cp "$ext" "$r/inscope/audit.sh"; chmod +x "$r/inscope/audit.sh"
printf 'not an audit\n' > "$tmproot/ext/plain.sh"
ln -s "$r/inscope/audit.sh" "$tmproot/finallink"
ln -s "$r/inscope" "$tmproot/scopelink"
refused2 'A3 relative path' 'the --audit path must be absolute' "$unsigned" --audit ext/audit.sh
refused2 'A3 empty value' 'the --audit path must be absolute' "$unsigned" --audit ''
refused2 'A3 nonexistent path' 'is not a regular file' "$unsigned" --audit "$tmproot/ext/no-such-audit.sh"
refused2 'A3 directory' 'is not a regular file' "$unsigned" --audit "$tmproot/ext/adir"
refused2 'A3 non-executable file' 'is not executable' "$unsigned" --audit "$tmproot/ext/plain.sh"
refused2 'A3 path inside the scope' 'resolves inside the scope' "$unsigned" --audit "$r/inscope/audit.sh"
refused2 'A3 symlink outside the scope whose final component points inside it' 'resolves inside the scope' "$unsigned" --audit "$tmproot/finallink"
refused2 'A15 symlinked directory into the scope' 'resolves inside the scope' "$unsigned" --audit "$tmproot/scopelink/audit.sh"
if [ -d "$r/INSCOPE" ]; then   # true only on a case-insensitive filesystem
  refused2 'A3 in-scope path spelled in another case' 'resolves inside the scope' "$unsigned" --audit "$r/INSCOPE/audit.sh"
else
  skip_line 'A3 in-scope path spelled in another case' 'the filesystem is case-sensitive'
fi
out="$(cd "$r" && bash "$engine" --dev "$unsigned" --audit 2>&1)"; rc=$?
check_eq "$rc" 2 'A3 --audit with no value: exits 2'
check_has "$out" 'the --audit flag needs a path' 'A3 --audit with no value: names the refusal'
out="$(cd "$r" && bash "$engine" --audit "$ext" v0.2.0 "$oracle" 'feat(a): x' 2>&1)"; rc=$?
check_eq "$rc" 2 'A3 --audit without --dev: exits 2'
check_has "$out" 'the --audit flag belongs to --dev mode only' 'A3 --audit without --dev: names the refusal'

# ---------- A4: the scope has its own audit, so the flag is refused; without it the own audit still builds ----------
r="$tmproot/ov-both"; mkdev "$r"; oracle="$(sha_of "$r" feat)"
refused2 'A4 the scope has its own audit' 'is only for a repo with none' "$unsigned" --audit "$ext"
check_lacks "$out" 'exists but is not an executable regular file' 'A4 the usable-audit refusal is not the unusable-audit one'
out="$(cd "$r" && bash "$engine" --dev "$unsigned" "$oracle" 'feat(a): bump a' a.txt 2>&1)"; rc=$?
check_eq "$rc" 0 'A4 without --audit the scope own audit still builds'
check_has "$out" '(the tree being proven)' 'A4 the header still reads the tree being proven'

# ---------- A5: present but unusable is refused differently from absent, with and without the flag ----------
for v in audit644 auditlink; do
  r="$tmproot/ov-unusable-$v"; mkdev "$r" "$v"; oracle="$(sha_of "$r" feat)"
  c0="$(count_commits "$r")"
  out="$(cd "$r" && bash "$engine" --dev "$unsigned" "$oracle" 'feat(a): bump a' a.txt 2>&1)"; rc=$?
  check_eq "$rc" 1 "A5 $v without the flag: exits 1"
  check_has "$out" 'the file exists but is not an executable regular file' "A5 $v without the flag: names an unusable audit"
  check_lacks "$out" ': absent' "A5 $v without the flag: is not reported as absent"
  check_eq "$(count_commits "$r")" "$c0" "A5 $v without the flag: created no commit"
  refused2 "A5 $v with the flag" '(exists but is not an executable regular file)' "$unsigned" --audit "$ext"
done

# ---------- A6b and A7: a clean audit-less repo, no flag: refused, never a silent fallback ----------
r="$tmproot/ov-none"; mkdev "$r" noaudit; oracle="$(sha_of "$r" feat)"
c0="$(count_commits "$r")"
out="$(cd "$r" && bash "$engine" --dev "$unsigned" "$oracle" 'feat(a): bump a' a.txt 2>&1)"; rc=$?
check_eq "$rc" 1 'A6b no audit and no flag: exits 1'
check_has "$out" 'no executable audit at' 'A6b no audit and no flag: names the missing audit'
check_has "$out" ': absent' 'A6b no audit and no flag: says the file is absent'
check_has "$out" '--audit <absolute path outside the repo>' 'A6b no audit and no flag: names the --audit instruction'
check_has "$out" "the installed audit is $repo_audit" 'A6b no audit and no flag: suggests the sibling audit without running it'
check_eq "$(count_commits "$r")" "$c0" 'A6b no audit and no flag: created no commit'
check_eq "$(porcelain_lines "$r")" 0 'A6b no audit and no flag: leaves the tree clean'
out="$(cd "$r" && AUDIT_PATH="$ext" PUBLISH_BRICK_AUDIT="$ext" AUDIT="$ext" bash "$engine" --dev "$unsigned" "$oracle" 'feat(a): bump a' a.txt 2>&1)"; rc=$?
check_eq "$rc" 1 'A7 exported variables naming an audit are not read: exits 1'
check_has "$out" ': absent' 'A7 exported variables naming an audit are not read: still absent'
check_eq "$(count_commits "$r")" "$c0" 'A7 exported variables naming an audit are not read: created no commit'

# ---------- A8: the instrument flips mid-finish, loudly ----------
r="$tmproot/ov-flip"; mkdev "$r" noaudit
git -C "$r" checkout -q feat
mkdir -p "$r/skills/audit"; printf '%s' "$AUDIT_STUB" > "$r/skills/audit/audit.sh"; chmod +x "$r/skills/audit/audit.sh"
git -C "$r" add -A && git -C "$r" commit -qm 'feat(audit): add the audit'
git -C "$r" checkout -q dev
oracle="$(sha_of "$r" feat)"
out="$(cd "$r" && bash "$engine" --dev "$unsigned" --audit "$ext" "$oracle" 'feat(a): bump a and add the audit' a.txt skills/audit/audit.sh 2>&1)"; rc=$?
check_eq "$rc" 0 'A8 brick 1 lands the repo own audit under the override'
refused2 'A8 brick 2 with --audit still given' 'the scope has its own skills/audit/audit.sh' "$unsigned" --audit "$ext"
out="$(cd "$r" && bash "$engine" --dev "$unsigned" "$oracle" 'feat(b): bump b' b.txt 2>&1)"; rc=$?
check_eq "$rc" 0 'A8 brick 2 without --audit builds'
check_has "$out" '(the tree being proven)' 'A8 brick 2 without --audit: the header reads the tree being proven'
r="$tmproot/ov-flip644"; mkdev "$r" noaudit
git -C "$r" checkout -q feat
mkdir -p "$r/skills/audit"; printf '%s' "$AUDIT_STUB" > "$r/skills/audit/audit.sh"
git -C "$r" add -A && git -C "$r" commit -qm 'feat(audit): add the audit without its exec bit'
git -C "$r" checkout -q dev
oracle="$(sha_of "$r" feat)"; c0="$(count_commits "$r")"
out="$(cd "$r" && bash "$engine" --dev "$unsigned" --audit "$ext" "$oracle" 'feat(a): bump a and add the audit' a.txt skills/audit/audit.sh 2>&1)"; rc=$?
check_eq "$rc" 1 'A8 an oracle adding the audit at 100644 is refused: exits 1'
check_has "$out" 'brick file skills/audit/audit.sh would leave no executable audit' 'A8 an oracle adding the audit at 100644 is refused: names the reason'
check_eq "$(count_commits "$r")" "$c0" 'A8 an oracle adding the audit at 100644 is refused: created no commit'

# ---------- A9, A11, A14: paths with spaces and glob characters, a symlinked final component, a symlinked directory ----------
r="$tmproot/ov-quote"; mkdev "$r" noaudit; oracle="$(sha_of "$r" feat)"
qext="$tmproot/ext sp[1] *?/audit.sh"; mkext "$qext"
out="$(cd "$r" && bash "$engine" --dev "$unsigned" --audit "$qext" "$oracle" 'feat(a): bump a' a.txt 2>&1)"; rc=$?
check_eq "$rc" 0 'A9 an audit path with spaces and glob characters builds'

r="$tmproot/ov-flink"; mkdev "$r" noaudit; oracle="$(sha_of "$r" feat)"; adir="$tmproot/ov-flink-art"
ln -s "$ext" "$tmproot/ext-link.sh"
pre="$(git -C "$r" rev-parse --short HEAD)"
out="$(cd "$r" && bash "$engine" --dev "$unsigned" --audit "$tmproot/ext-link.sh" --artifact-dir "$adir" "$oracle" 'feat(a): bump a' a.txt 2>&1)"; rc=$?
check_eq "$rc" 0 'A11 a symlinked final component outside the scope builds'
check_has "$(cat "$adir/audit-dev-$pre.txt" 2>/dev/null)" "AUDIT_PATH=$ext" 'A11 the artifact records the RESOLVED target'

r="$tmproot/ov-dlink"; mkdev "$r" noaudit; oracle="$(sha_of "$r" feat)"; adir="$tmproot/ov-dlink-art"
ln -s "$tmproot/ext" "$tmproot/linkdir"
pre="$(git -C "$r" rev-parse --short HEAD)"
out="$(cd "$r" && bash "$engine" --dev "$unsigned" --audit "$tmproot/linkdir/audit.sh" --artifact-dir "$adir" "$oracle" 'feat(a): bump a' a.txt 2>&1)"; rc=$?
check_eq "$rc" 0 'A14 a symlinked directory outside the scope builds'
check_has "$(cat "$adir/audit-dev-$pre.txt" 2>/dev/null)" "AUDIT_PATH=$ext" 'A14 the artifact records the physical path'

# ---------- A12 and A12b: the artifact records WHICH bytes judged the brick ----------
r="$tmproot/ov-digest"; mkdev "$r" noaudit; oracle="$(sha_of "$r" feat)"; adir="$tmproot/ov-digest-art"
dext="$tmproot/ext-digest/audit.sh"; mkext "$dext"
want1="$(shasum -a 256 "$dext" | cut -d' ' -f1)"
check_eq "${#want1}" 64 'A12 floor: the independent digest is 64 hex characters'
pre="$(git -C "$r" rev-parse --short HEAD)"
out="$(cd "$r" && bash "$engine" --dev "$unsigned" --audit "$dext" --artifact-dir "$adir" "$oracle" 'feat(a): bump a' a.txt 2>&1)"; rc=$?
got1="$(sed -n 's/^AUDIT_SHA256=//p' "$adir/audit-dev-$pre.txt" 2>/dev/null)"
check_eq "$got1" "$want1" 'A12 override: the artifact AUDIT_SHA256 equals an independent shasum'
check_has "$out" "audit sha256: $want1" 'A12 override: stdout agrees with the artifact'
printf '# changed\n' >> "$dext"
want2="$(shasum -a 256 "$dext" | cut -d' ' -f1)"
pre="$(git -C "$r" rev-parse --short HEAD)"
out="$(cd "$r" && bash "$engine" --dev "$unsigned" --audit "$dext" --artifact-dir "$adir" "$oracle" 'feat(b): bump b' b.txt 2>&1)"; rc=$?
got2="$(sed -n 's/^AUDIT_SHA256=//p' "$adir/audit-dev-$pre.txt" 2>/dev/null)"
check_eq "$got2" "$want2" 'A12 control: after the stub changed, the next brick records the new digest'
if [ -n "$got2" ] && [ "$got2" != "$want1" ]; then
  pass_line 'A12 control: the recorded digest MOVED when the audit changed'
else
  fail_line "A12 control: the recorded digest did not move ([$got1] then [$got2])"
fi

r="$tmproot/ov-digest-scope"; mkdev "$r"; oracle="$(sha_of "$r" feat)"; adir="$tmproot/ov-digest-scope-art"
pre="$(git -C "$r" rev-parse --short HEAD)"
want="$(shasum -a 256 "$r/skills/audit/audit.sh" | cut -d' ' -f1)"
out="$(cd "$r" && bash "$engine" --dev "$unsigned" --artifact-dir "$adir" "$oracle" 'feat(a): bump a' a.txt 2>&1)"; rc=$?
art="$(cat "$adir/audit-dev-$pre.txt" 2>/dev/null)"
check_eq "$(sed -n 's/^AUDIT_SHA256=//p' <<<"$art")" "$want" 'A12 scope: the artifact digest equals the scope own audit'
check_has "$art" 'AUDIT_SOURCE=scope' 'A12 scope: the artifact records the source as scope'

r="$tmproot/ov-v2"; mkdev "$r"
git -C "$r" checkout -q feat
printf '%s\n' "$V2_STUB" > "$r/skills/audit/audit.sh"
git -C "$r" commit -qam 'feat(audit): a second audit'
git -C "$r" checkout -q dev
oracle="$(sha_of "$r" feat)"; adir="$tmproot/ov-v2-art"
before="$(shasum -a 256 "$r/skills/audit/audit.sh" | cut -d' ' -f1)"
pre="$(git -C "$r" rev-parse --short HEAD)"
out="$(cd "$r" && bash "$engine" --dev "$unsigned" --artifact-dir "$adir" "$oracle" 'feat(a): bump a and the audit' a.txt skills/audit/audit.sh 2>&1)"; rc=$?
art="$(cat "$adir/audit-dev-$pre.txt" 2>/dev/null)"
after="$(shasum -a 256 "$r/skills/audit/audit.sh" | cut -d' ' -f1)"
check_eq "$rc" 0 'A12b a brick that lands a new own audit builds'
check_has "$art" 'audit-v2-ran' 'A12b the audit that judged the brick is the one it landed'
check_eq "$(sed -n 's/^AUDIT_SHA256=//p' <<<"$art")" "$after" 'A12b the artifact digest equals the audit AFTER the brick'
if [ "$after" != "$before" ]; then
  pass_line 'A12b floor: the brick changed the audit bytes'
else
  fail_line 'A12b floor: the brick did not change the audit bytes, so the row cannot see the run-site digest'
fi

# ---------- A13: no engine variable reaches the audit (a NAME probe; it cannot see a changed value) ----------
base_names="$tmproot/env-base.txt"; dump="$tmproot/env-dump.txt"
check_no_extra() { # label: the probe read a dump, and it adds no variable beyond the baseline
  local extra
  if [ ! -s "$dump" ] || [ ! -s "$base_names" ]; then fail_line "$1 (the probe read nothing)"; return; fi
  extra="$(LC_ALL=C comm -13 "$base_names" "$dump" | grep -vxE '_|PWD|OLDPWD|SHLVL')"
  check_eq "$extra" '' "$1"
}
dstub="$tmproot/ext-env/audit.sh"; mkext "$dstub" "$DUMP_STUB"
r="$tmproot/ov-env"; mkdev "$r" noaudit; oracle="$(sha_of "$r" feat)"
(cd "$r" && AUDIT_ENVDUMP="$dump" AUDIT_MODE=pass env | cut -d= -f1 | LC_ALL=C sort > "$base_names")
out="$(cd "$r" && AUDIT_ENVDUMP="$dump" AUDIT_MODE=pass bash "$engine" --dev "$unsigned" --audit "$dstub" "$oracle" 'feat(a): bump a' a.txt 2>&1)"; rc=$?
check_eq "$rc" 0 'A13 override: the env-dump audit ran and passed'
if [ -s "$dump" ] && [ -s "$base_names" ]; then pass_line 'A13 floor: the probe read a non-empty environment'; else fail_line 'A13 floor: the probe read an empty environment'; fi
check_no_extra 'A13 override: the audit sees no variable the engine added'
rm -f "$dump"
r="$tmproot/ov-env-control"; mkdev "$r" noaudit; oracle="$(sha_of "$r" feat)"
out="$(cd "$r" && A13_CONTROL=leak AUDIT_ENVDUMP="$dump" AUDIT_MODE=pass bash "$engine" --dev "$unsigned" --audit "$dstub" "$oracle" 'feat(a): bump a' a.txt 2>&1)"; rc=$?
extra="$(LC_ALL=C comm -13 "$base_names" "$dump" | grep -vxE '_|PWD|OLDPWD|SHLVL')"
check_has "$extra" 'A13_CONTROL' 'A13 control: a variable that IS exported is visible to the probe'
rm -f "$dump"
MKDEV_STUB="$DUMP_STUB"; r="$tmproot/ov-env-scope"; mkdev "$r"; MKDEV_STUB=""
oracle="$(sha_of "$r" feat)"
out="$(cd "$r" && AUDIT_ENVDUMP="$dump" AUDIT_MODE=pass bash "$engine" --dev "$unsigned" "$oracle" 'feat(a): bump a' a.txt 2>&1)"; rc=$?
check_eq "$rc" 0 'A13 scope: the env-dump own audit ran and passed'
check_no_extra 'A13 scope: the own audit sees no variable the engine added'

# ---------- A16: an unreadable audit is refused before any write ----------
if [ "$(id -u)" = 0 ]; then
  skip_line 'A16 an unreadable audit is refused before any write' 'running as root: root reads a mode 0111 file'
else
  r="$tmproot/ov-unreadable"; mkdev "$r" noaudit; oracle="$(sha_of "$r" feat)"
  uext="$tmproot/ext-unreadable/audit.sh"; mkext "$uext"; chmod 0111 "$uext"
  c0="$(count_commits "$r")"
  out="$(cd "$r" && bash "$engine" --dev "$unsigned" --audit "$uext" "$oracle" 'feat(a): bump a' a.txt 2>&1)"; rc=$?
  check_eq "$rc" 1 'A16 an unreadable audit: exits 1'
  check_has "$out" 'could not compute the audit digest' 'A16 an unreadable audit: names the digest failure'
  check_has "$out" 'nothing was committed' 'A16 an unreadable audit: nothing was committed'
  check_eq "$(count_commits "$r")" "$c0" 'A16 an unreadable audit: created no commit'
fi

# ---------- O1 and O2: the order of the dev preconditions, pinned ----------
r="$tmproot/ord-audit"; mkdev "$r" noaudit; oracle="$(sha_of "$r" feat)"
out="$(cd "$r" && bash "$engine" --dev "$oracle" 'feat(a): bump a' a.txt 2>&1)"; rc=$?
check_eq "$rc" 1 'O1 no audit and signing off, no flags: exits 1'
check_has "$out" 'no executable audit at' 'O1 the audit refusal comes before the signing refusal'
check_lacks "$out" 'commit.gpgsign is' 'O1 the signing refusal did not fire first'

r="$tmproot/ord-progress"; mkdev "$r"; oracle="$(sha_of "$r" feat)"
printf 'interim\n' > "$r/b.txt"; git -C "$r" commit -qam 'chore(b): interim work on dev'
out="$(cd "$r" && bash "$engine" --dev "$oracle" 'feat(a): bump a' a.txt 2>&1)"; rc=$?
check_eq "$rc" 1 'O2 progress would refuse and signing is off, no flags: exits 1'
check_has "$out" 'commit.gpgsign is false' 'O2 the signing refusal comes before the progress refusal'
check_lacks "$out" 'progress:' 'O2 the progress refusal did not fire first'

# ---------- S1, S2, S3, S10: the signing assertion with no signer at all ----------
r="$tmproot/sign-default"; mkdev "$r"; oracle="$(sha_of "$r" feat)"
head0="$(git -C "$r" rev-parse HEAD)"
out="$(cd "$r" && bash "$engine" --dev "$oracle" 'feat(a): bump a' a.txt 2>&1)"; rc=$?
check_eq "$rc" 1 'S1 default refuses (false): exits 1'
check_has "$out" 'commit.gpgsign is false' 'S1 default refuses (false): names the raw value'
check_before "$out" "git -C $r config commit.gpgsign true" '--allow-unsigned' 'S1 default refuses (false): the remedy comes before the opt-out'
check_eq "$(git -C "$r" rev-parse HEAD)" "$head0" 'S1 default refuses (false): HEAD is unchanged'
check_eq "$(porcelain_lines "$r")" 0 'S1 default refuses (false): the tree is clean'

r="$tmproot/sign sp"; mkdev "$r"; oracle="$(sha_of "$r" feat)"
out="$(cd "$r" && bash "$engine" --dev "$oracle" 'feat(a): bump a' a.txt 2>&1)"; rc=$?
check_eq "$rc" 1 'S1 spaced path: exits 1'
check_has "$out" 'sign\ sp config commit.gpgsign true' 'S1 spaced path: the printed remedy escapes the path'

for v in no off 0 maybe; do
  r="$tmproot/sign-val-$v"; mkdev "$r"; git -C "$r" config commit.gpgsign "$v"; oracle="$(sha_of "$r" feat)"
  c0="$(count_commits "$r")"
  out="$(cd "$r" && bash "$engine" --dev "$oracle" 'feat(a): bump a' a.txt 2>&1)"; rc=$?
  check_eq "$rc" 1 "S2 commit.gpgsign=$v: exits 1"
  check_has "$out" "commit.gpgsign is $v, not true" "S2 commit.gpgsign=$v: prints the raw value"
  check_eq "$(count_commits "$r")" "$c0" "S2 commit.gpgsign=$v: created no commit"
done

r="$tmproot/sign-optout"; mkdev "$r"; oracle="$(sha_of "$r" feat)"
out="$(cd "$r" && bash "$engine" --dev "$unsigned" "$oracle" 'feat(a): bump a' a.txt 2>&1)"; rc=$?
check_eq "$rc" 0 'S3 opt-out: --allow-unsigned builds with signing off'
check_has "$out" 'signing: NOT ASSERTED (--allow-unsigned); commit.gpgsign=false' 'S3 opt-out: the flag is printed'
check_has "$out" '%G?=N' 'S3 opt-out: the per-brick %G? is printed'
check_eq "$(git -C "$r" log -1 --format=%G?)" N 'S3 opt-out: an independent %G? reads N'

r="$tmproot/sign-unset"; mkdev "$r" unsetsign; oracle="$(sha_of "$r" feat)"
mkdir -p "$tmproot/emptyhome" "$tmproot/emptyxdg"
got="$(HOME="$tmproot/emptyhome" XDG_CONFIG_HOME="$tmproot/emptyxdg" git -C "$r" config --get commit.gpgsign 2>/dev/null)"; grc=$?
if [ "$grc" = 1 ] && [ -z "$got" ]; then
  c0="$(count_commits "$r")"
  out="$(cd "$r" && HOME="$tmproot/emptyhome" XDG_CONFIG_HOME="$tmproot/emptyxdg" bash "$engine" --dev "$oracle" 'feat(a): bump a' a.txt 2>&1)"; rc=$?
  check_eq "$rc" 1 'S10 unset: exits 1'
  check_has "$out" 'commit.gpgsign is (unset)' 'S10 unset: prints (unset)'
  check_before "$out" "git -C $r config commit.gpgsign true" '--allow-unsigned' 'S10 unset: the remedy comes before the opt-out'
  check_eq "$(count_commits "$r")" "$c0" 'S10 unset: created no commit'
else
  fail_line "S10 floor: commit.gpgsign reads [$got] (status $grc) under an empty HOME, so the fixture is not unset"
fi

# ---------- S5, S6, S6b, S9: signer rows (fakes; they need GNU timeout so a hang is a FAIL, not a stall) ----------
write_fake_signers() { # dir -> gpg-fail.sh (always fails) and gpg-nopub.sh (signs, never verifies)
  mkdir -p "$1"
  printf '#!/bin/sh\nexit 1\n' > "$1/gpg-fail.sh"
  cat > "$1/gpg-nopub.sh" <<'FAKE'
#!/bin/sh
# Signs on request; on --verify reports an unknown key, so the commit lands with %G? = N.
for a in "$@"; do
  if [ "$a" = --verify ]; then
    cat > /dev/null
    echo '[GNUPG:] NO_PUBKEY DEADBEEF' >&2
    exit 1
  fi
done
cat > /dev/null
printf -- '-----BEGIN PGP SIGNATURE-----\n\nZmFrZQ==\n-----END PGP SIGNATURE-----\n'
echo '[GNUPG:] SIG_CREATED D 1 8 00 1 DEADBEEF' >&2
exit 0
FAKE
  chmod +x "$1/gpg-fail.sh" "$1/gpg-nopub.sh"
}

fake_config() { # repo program value -> openpgp signing through a fake signer, LOCAL config only
  git -C "$1" config gpg.format openpgp
  git -C "$1" config gpg.program "$2"
  git -C "$1" config user.signingkey DEADBEEF
  git -C "$1" config tag.gpgsign false
  git -C "$1" config commit.gpgsign "$3"
}

ssh_config() { # repo value -> a real ssh signature, LOCAL config only
  git -C "$1" config gpg.format ssh
  git -C "$1" config user.signingkey "$tmproot/sshkey"
  git -C "$1" config gpg.ssh.program ssh-keygen
  git -C "$1" config gpg.ssh.allowedSignersFile "$tmproot/allowed_signers"
  git -C "$1" config tag.gpgsign false
  git -C "$1" config commit.gpgsign "$2"
}

have_timeout=no; have_sshkeygen=no
if command -v timeout >/dev/null 2>&1; then have_timeout=yes; fi
if command -v ssh-keygen >/dev/null 2>&1; then have_sshkeygen=yes; fi
fake_dir="$tmproot/fake"; write_fake_signers "$fake_dir"

if [ "$have_timeout" = yes ]; then
  r="$tmproot/sign-fake-probe"; mkdev "$r"; fake_config "$r" "$fake_dir/gpg-nopub.sh" true
  printf 'p\n' > "$r/probe.txt"; git -C "$r" add probe.txt
  timeout 20 git -C "$r" commit -q -m probe >/dev/null 2>&1; prc=$?
  psig="$(git -C "$r" log -1 --format=%G?)"
  if [ "$prc" = 0 ] && [ "$psig" = N ]; then
    pass_line 'S6 fixture probe: the fake signer lands a commit whose %G? is N'
  else
    fail_line "S6 fixture probe: the fake signer did not behave (commit rc=$prc, %G?=$psig)"
  fi

  r="$tmproot/sign-fail"; mkdev "$r"; fake_config "$r" "$fake_dir/gpg-fail.sh" true; oracle="$(sha_of "$r" feat)"
  head0="$(git -C "$r" rev-parse HEAD)"
  out="$(cd "$r" && timeout 60 bash "$engine" --dev "$oracle" 'feat(a): bump a' a.txt 2>&1)"; rc=$?
  check_eq "$rc" 1 'S5 failing signer: exits 1'
  check_has "$out" 'the brick commit failed' 'S5 failing signer: names the commit failure'
  check_has "$out" 'nothing was committed' 'S5 failing signer: nothing was committed'
  check_eq "$(git -C "$r" rev-parse HEAD)" "$head0" 'S5 failing signer: HEAD is unchanged'
  check_eq "$(porcelain_lines "$r")" 0 'S5 failing signer: the tree was restored'

  r="$tmproot/sign-nopub"; mkdev "$r"; fake_config "$r" "$fake_dir/gpg-nopub.sh" true; oracle="$(sha_of "$r" feat)"
  out="$(cd "$r" && timeout 60 bash "$engine" --dev "$oracle" 'feat(a): bump a' a.txt 2>&1)"; rc=$?
  check_eq "$rc" 1 'S6 configured but not signed: exits 1'
  check_has "$out" 'is not signed' 'S6 configured but not signed: names the readback failure'
  check_has "$out" 'reset --hard HEAD~1' 'S6 configured but not signed: names the recovery'
  check_eq "$(git -C "$r" log -1 --format=%G?)" N 'S6 configured but not signed: an independent %G? reads N'

  r="$tmproot/sign-nopub-flag"; mkdev "$r"; fake_config "$r" "$fake_dir/gpg-nopub.sh" true; oracle="$(sha_of "$r" feat)"
  out="$(cd "$r" && timeout 60 bash "$engine" --dev "$unsigned" "$oracle" 'feat(a): bump a' a.txt 2>&1)"; rc=$?
  check_eq "$rc" 1 'S6b the flag waives the assertion, never the readback: exits 1'
  check_has "$out" 'is not signed' 'S6b the flag waives the assertion, never the readback: still not signed'

  r="$tmproot/sign-valueless"; mkdev "$r"; fake_config "$r" "$fake_dir/gpg-nopub.sh" true; oracle="$(sha_of "$r" feat)"
  git -C "$r" config --unset commit.gpgsign
  printf '[commit]\n\tgpgsign\n' >> "$r/.git/config"   # a bare key: true under --bool, empty under --get
  out="$(cd "$r" && timeout 60 bash "$engine" --dev "$unsigned" "$oracle" 'feat(a): bump a' a.txt 2>&1)"; rc=$?
  check_eq "$rc" 1 'S13 a valueless commit.gpgsign reads true: exits 1'
  check_has "$out" 'is not signed' 'S13 a valueless commit.gpgsign reads true: the readback ran'
  check_has "$out" 'commit.gpgsign=(no value)' 'S13 a valueless commit.gpgsign: the opt-out line says no value, not unset'

  r="$tmproot/sign-publish-yes"; mk "$r"; fake_config "$r" "$fake_dir/gpg-nopub.sh" yes
  out="$(cd "$r" && timeout 60 bash "$engine" v0.2.0 "$(sha_of "$r" dev~2)" 'feat(a): x' 2>&1)"; rc=$?
  check_eq "$rc" 1 'S9 publish mode, commit.gpgsign=yes with an unverifying signer: exits 1'
  check_has "$out" 'is not signed' 'S9 publish mode, commit.gpgsign=yes: the readback now runs'
  r="$tmproot/sign-publish-false"; mk "$r"; fake_config "$r" "$fake_dir/gpg-nopub.sh" false
  out="$(cd "$r" && timeout 60 bash "$engine" v0.2.0 "$(sha_of "$r" dev~2)" 'feat(a): x' 2>&1)"; rc=$?
  check_eq "$rc" 0 'S9 publish mode, commit.gpgsign=false: exits 0'
  check_has "$out" 'signing not enabled in this repo)' 'S9 publish mode, commit.gpgsign=false: the quiet line is unchanged'
else
  skip_line 'S5 S6 S6b S9 signer rows' 'timeout (GNU coreutils) is absent'
fi

# ---------- S7: --allow-unsigned outside --dev ----------
r="$tmproot/sign-flag-publish"; mk "$r"
out="$(cd "$r" && bash "$engine" --allow-unsigned v0.2.0 "$(sha_of "$r" dev~2)" 'feat(a): x' 2>&1)"; rc=$?
check_eq "$rc" 2 'S7 --allow-unsigned without --dev: exits 2'
check_has "$out" 'the --allow-unsigned flag belongs to --dev mode only' 'S7 --allow-unsigned without --dev: names the refusal'
check_eq "$(git -C "$r" rev-list --count main)" 1 'S7 --allow-unsigned without --dev: wrote nothing'

# ---------- S4 and S12: a real ssh signature ----------
if [ "$have_timeout" = yes ] && [ "$have_sshkeygen" = yes ]; then
  ssh-keygen -q -t ed25519 -N '' -C test@test.invalid -f "$tmproot/sshkey" >/dev/null 2>&1
  printf 'test@test.invalid %s\n' "$(cut -d' ' -f1,2 "$tmproot/sshkey.pub")" > "$tmproot/allowed_signers"
  r="$tmproot/sign-probe"; mkdev "$r"; ssh_config "$r" true
  printf 'p\n' > "$r/probe.txt"; git -C "$r" add probe.txt
  timeout 20 git -C "$r" commit -q -m probe >/dev/null 2>&1; prc=$?
  psig="$(git -C "$r" log -1 --format=%G?)"
  if [ "$prc" = 0 ] && [ "$psig" = G ]; then
    pass_line 'S12 sanity: the ssh signing probe signed and read G'
  else
    fail_line "S12 signing probe failed with tools present (commit rc=$prc, %G?=$psig)"
  fi
  for v in true yes; do
    r="$tmproot/sign-real-$v"; mkdev "$r"; ssh_config "$r" "$v"; oracle="$(sha_of "$r" feat)"
    out="$(cd "$r" && timeout 60 bash "$engine" --dev "$oracle" 'feat(a): bump a' a.txt 2>&1)"; rc=$?
    check_eq "$rc" 0 "S4 real ssh signature (commit.gpgsign=$v): exits 0"
    check_has "$out" 'signed (%G?=G)' "S4 real ssh signature (commit.gpgsign=$v): prints the signed line and %G?"
    check_eq "$(git -C "$r" log -1 --format=%G?)" G "S4 real ssh signature (commit.gpgsign=$v): an independent %G? reads G"
  done
else
  skip_line 'S4 S12 real ssh signature rows' 'ssh-keygen or timeout is absent'
fi

# ---------- A17: the scope judge is an allowlist, so an interpreter that fails with status 1 refuses ----------
# inside_scope reports "outside" through its script's own exit status, and a python3 that dies before the
# script runs (a start-up or site-import failure) exits 1 as well. The shim below passes every call through to
# the real interpreter ($PYSHIM_REAL); when $PYSHIM_FAIL is set it makes ONE call exit 1 instead: the one whose
# arguments mention `samestat`, which only inside_scope's script does (real_path and audit_digest never do).
# So the run clears the absolute, regular-file, executable and scope-resolution refusals, which use the real
# interpreter, and reaches the judge itself. The signing setting stays local to each fixture, as in every row.
pyshim="$tmproot/pyshim"; mkdir -p "$pyshim"
cat > "$pyshim/python3" <<'SHIM'
#!/bin/sh
for a in "$@"; do
  case "$a" in
    *samestat*)
      if [ -n "${PYSHIM_FAIL:-}" ]; then
        printf 'judge-failed\n' >> "${PYSHIM_LOG:?}"
        exit 1
      fi ;;
  esac
done
exec "${PYSHIM_REAL:?}" "$@"
SHIM
chmod +x "$pyshim/python3"
real_py="$(command -v python3)"

r="$tmproot/ov-judge-ok"; mkdev "$r" noaudit; oracle="$(sha_of "$r" feat)"
out="$(cd "$r" && PATH="$pyshim:$PATH" PYSHIM_REAL="$real_py" bash "$engine" --dev "$unsigned" --audit "$ext" "$oracle" 'feat(a): bump a' a.txt 2>&1)"; rc=$?
check_eq "$rc" 0 'A17 control: a pass-through python3 shim still builds with --audit'
check_has "$out" 'RESULT: PASS rc=0 brick=dev' 'A17 control: the pass-through run prints the dev PASS verdict'

r="$tmproot/ov-judge-fail"; mkdev "$r" noaudit; oracle="$(sha_of "$r" feat)"
c0="$(count_commits "$r")"; p0="$(porcelain_lines "$r")"; shimlog="$tmproot/pyshim.log"
out="$(cd "$r" && PATH="$pyshim:$PATH" PYSHIM_REAL="$real_py" PYSHIM_FAIL=1 PYSHIM_LOG="$shimlog" bash "$engine" --dev "$unsigned" --audit "$ext" "$oracle" 'feat(a): bump a' a.txt 2>&1)"; rc=$?
if [ -s "$shimlog" ]; then
  pass_line 'A17 floor: the failing interpreter was reached by the scope judge'
else
  fail_line 'A17 floor: the scope judge never reached the failing interpreter, so the row cannot see the judge'
fi
check_eq "$rc" 2 'A17 a scope judge whose interpreter exits 1: exits 2'
check_has "$out" 'could not judge whether the --audit path is inside the scope' 'A17 a scope judge whose interpreter exits 1: names the refusal'
check_lacks "$out" 'RESULT: PASS' 'A17 a scope judge whose interpreter exits 1: prints no PASS verdict'
check_eq "$(count_commits "$r")" "$c0" 'A17 a scope judge whose interpreter exits 1: created no commit'
check_eq "$(porcelain_lines "$r")" "$p0" 'A17 a scope judge whose interpreter exits 1: leaves the tree as it found it'

# ---------- the fixture helper's own allowlist: an unknown mkdev variant is refused, never built as a default ----------
# A typo such as `noaduit` used to fall into the default arm and build an ordinary repo WITH an audit, so a row
# meant for an audit-less repo could pass or fail for an unrelated reason. mkdev exits 2 with a message on stderr; the
# check runs it in a subshell so the exit cannot end this suite, and the subshell resets the EXIT trap.
out="$( (mkdev "$tmproot/bad-variant" noaduit) 2>&1 )"; rc=$?
check_eq "$rc" 2 'mkdev self-check: an unknown variant exits 2'
check_has "$out" 'mkdev: unknown variant: noaduit' 'mkdev self-check: an unknown variant is named on stderr'
check_eq "$([ -e "$tmproot/bad-variant" ] && echo built || echo nothing)" nothing 'mkdev self-check: an unknown variant builds nothing'

# ======================= the tree's own audit, the signing readback, isolated helpers =======================
# Four things the engine has to guarantee that the rows above did not pin: `--audit` can never replace a tree's own
# gate (judged from the worktree AND from HEAD, and a judge that cannot answer refuses); a dev brick whose signing
# assertion ran stays bound to it even when the effective config changes before the commit; every inline python3 runs
# isolated and a recorded digest has the shape of one; and a RESOLVED audit path is judged as resolved.

# ---------- the tree's own audit is judged from the worktree AND from HEAD ----------
# A skip-worktree entry whose file was removed reads clean to `git status`, so only HEAD shows that the tree has an audit.
r="$tmproot/own-skipwt"; mkdev "$r"; oracle="$(sha_of "$r" feat)"
git -C "$r" update-index --skip-worktree skills/audit/audit.sh && rm "$r/skills/audit/audit.sh"
check_eq "$(porcelain_lines "$r")" 0 'own audit floor: a removed skip-worktree audit reads clean to git status'
check_eq "$(git -C "$r" ls-tree HEAD skills/audit/audit.sh | cut -c1-11)" '100755 blob' 'own audit floor: HEAD still tracks the removed audit'
refused2 'own audit tracked at HEAD and absent from the worktree, with the flag' 'HEAD tracks skills/audit/audit.sh' "$unsigned" --audit "$ext"
c0="$(count_commits "$r")"
out="$(cd "$r" && bash "$engine" --dev "$unsigned" "$oracle" 'feat(a): bump a' a.txt 2>&1)"; rc=$?
check_eq "$rc" 1 'own audit tracked at HEAD and absent from the worktree, no flag: exits 1'
check_has "$out" 'HEAD tracks it but the worktree does not have it' 'own audit tracked at HEAD and absent from the worktree, no flag: names the cause'
check_lacks "$out" '--audit' 'own audit tracked at HEAD and absent from the worktree, no flag: does not advise the override'
check_eq "$(count_commits "$r")" "$c0" 'own audit tracked at HEAD and absent from the worktree, no flag: created no commit'

# A directory that cannot be searched makes the audit path unexaminable, not absent. Root searches any directory.
if [ "$(id -u)" = 0 ]; then
  skip_line 'own audit path that cannot be examined' 'running as root: root searches a mode 644 directory'
else
  r="$tmproot/own-unsearchable"; mkdev "$r"; oracle="$(sha_of "$r" feat)"
  chmod 644 "$r/skills/audit"
  if [ ! -e "$r/skills/audit/audit.sh" ] && [ ! -L "$r/skills/audit/audit.sh" ]; then
    pass_line 'own audit floor: an unsearchable directory makes a tracked audit unexaminable'
  else
    fail_line 'own audit floor: the audit is still examinable under a mode 644 directory'
  fi
  refused2 'own audit that cannot be examined, with the flag' 'could not judge whether the scope has its own' "$unsigned" --audit "$ext"
  c0="$(count_commits "$r")"
  out="$(cd "$r" && bash "$engine" --dev "$unsigned" "$oracle" 'feat(a): bump a' a.txt 2>&1)"; rc=$?
  check_eq "$rc" 1 'own audit that cannot be examined, no flag: exits 1'
  check_has "$out" 'could not judge whether' 'own audit that cannot be examined, no flag: names the cause'
  check_lacks "$out" '--audit' 'own audit that cannot be examined, no flag: does not advise the override'
  check_eq "$(count_commits "$r")" "$c0" 'own audit that cannot be examined, no flag: created no commit'
  chmod 755 "$r/skills/audit"
  # The same state with NOTHING tracked: HEAD alone cannot clear it, since the worktree could still hold an audit.
  r="$tmproot/own-unsearchable-untracked"; mkdev "$r" noaudit; oracle="$(sha_of "$r" feat)"
  mkdir -p "$r/skills/audit"; chmod 644 "$r/skills/audit"
  refused2 'own audit that cannot be examined and is not tracked, with the flag' 'could not judge whether the scope has its own' "$unsigned" --audit "$ext"
  chmod 755 "$r/skills/audit"
fi

# An unborn HEAD gives git nothing to list, so the judge cannot answer and the flag is refused before anything else.
r="$tmproot/own-unborn"; git init -q -b dev "$r"; printf 'production = "dev"\n' > "$r/.publication.toml"
out="$(cd "$r" && bash "$engine" --dev "$unsigned" --audit "$ext" deadbeef 'feat(a): x' a.txt 2>&1)"; rc=$?
check_eq "$rc" 2 'own audit with an unborn HEAD, with the flag: exits 2'
check_has "$out" 'could not judge whether the scope has its own' 'own audit with an unborn HEAD, with the flag: names the refusal'

# ---------- the signing assertion binds the readback ----------
# The brick materialises .gitconfig-shared, which the repo's own config includes and which turns signing off: the effective
# commit.gpgsign reads true when the assertion runs and false when `git commit` does. The include line is appended to the
# config file as text, as row S13 does for its valueless key. Per-call empty HOME and XDG_CONFIG_HOME keep the operator's
# signing key out of reach; GNU timeout makes a hang a FAIL.
mkflip() { # dir -> a dev repo (oracle `feat`) whose first brick flips commit.gpgsign from true to false
  mkdev "$1"
  git -C "$1" checkout -q feat
  printf '[commit]\n\tgpgsign = false\n' > "$1/.gitconfig-shared"
  git -C "$1" add .gitconfig-shared
  git -C "$1" commit -qm 'feat(cfg): add the shared config'
  git -C "$1" checkout -q dev
  git -C "$1" config commit.gpgsign true
  printf '[include]\n\tpath = ../.gitconfig-shared\n' >> "$1/.git/config"
}
if [ "$have_timeout" = yes ]; then
  mkdir -p "$tmproot/emptyhome" "$tmproot/emptyxdg"
  r="$tmproot/flip-default"; mkflip "$r"; oracle="$(sha_of "$r" feat)"
  got="$(HOME="$tmproot/emptyhome" XDG_CONFIG_HOME="$tmproot/emptyxdg" git -C "$r" config --bool --get commit.gpgsign 2>/dev/null)"
  check_eq "$got" true 'config flip floor: the effective commit.gpgsign reads true before the brick'
  out="$(cd "$r" && HOME="$tmproot/emptyhome" XDG_CONFIG_HOME="$tmproot/emptyxdg" timeout 60 bash "$engine" --dev "$oracle" 'feat(cfg): take the shared config' .gitconfig-shared 2>&1)"; rc=$?
  got="$(HOME="$tmproot/emptyhome" XDG_CONFIG_HOME="$tmproot/emptyxdg" git -C "$r" config --bool --get commit.gpgsign 2>/dev/null)"
  check_eq "$got" false 'config flip floor: the brick itself turned the effective commit.gpgsign off'
  check_eq "$rc" 1 'config flip after the assertion, no flag: exits 1'
  check_has "$out" 'changed between the assertion and the commit' 'config flip after the assertion, no flag: names the changed config'
  check_has "$out" 'reset --hard HEAD~1' 'config flip after the assertion, no flag: names the recovery'
  check_lacks "$out" 'RESULT: PASS' 'config flip after the assertion, no flag: prints no PASS verdict'
  check_eq "$(HOME="$tmproot/emptyhome" XDG_CONFIG_HOME="$tmproot/emptyxdg" git -C "$r" log -1 --format=%G?)" N 'config flip after the assertion, no flag: an independent %G? reads N'

  r="$tmproot/flip-optout"; mkflip "$r"; oracle="$(sha_of "$r" feat)"
  out="$(cd "$r" && HOME="$tmproot/emptyhome" XDG_CONFIG_HOME="$tmproot/emptyxdg" timeout 60 bash "$engine" --dev "$unsigned" "$oracle" 'feat(cfg): take the shared config' .gitconfig-shared 2>&1)"; rc=$?
  check_eq "$rc" 0 'config flip with --allow-unsigned: the flag still waives the assertion and its binding: exits 0'
  check_has "$out" 'signing: NOT ASSERTED (--allow-unsigned); commit.gpgsign=true' 'config flip with --allow-unsigned: the assertion was skipped'
  check_has "$out" 'RESULT: PASS rc=0 brick=dev' 'config flip with --allow-unsigned: prints the dev PASS verdict'
else
  skip_line 'config flip rows' 'timeout (GNU coreutils) is absent'
fi

# ---------- every inline python3 runs isolated, and a recorded digest has the shape of one ----------
# The shim logs one line per python3 call, `<class> <first argument>`, and passes the call through. The class is told by the
# module call the helper's script names, or by `-` as an argument (the subject policy check reads its script from stdin).
# $PYSHIM_BADDIGEST, when set, makes the digest helper print that value instead of running.
pyshim2="$tmproot/pyshim2"; pylog="$tmproot/pyshim2.log"; mkdir -p "$pyshim2"
cat > "$pyshim2/python3" <<'SHIM'
#!/bin/sh
cls=other
for a in "$@"; do
  case "$a" in
    *hashlib*) cls=digest ;;
    *realpath*) cls=realpath ;;
    *samestat*) cls=samestat ;;
    *lstat*) cls=lstat ;;
    *S_ISLNK*) cls=component ;;
    *samefile*) cls=gitlink ;;
  esac
done
if [ "${1:-}" = - ] || [ "${2:-}" = - ]; then cls=subject; fi
printf '%s %s\n' "$cls" "${1:-}" >> "${PYSHIM_LOG:?}"
if [ -n "${PYSHIM_FAIL_CLASS:-}" ] && [ "$cls" = "$PYSHIM_FAIL_CLASS" ]; then exit 1; fi
if [ "$cls" = digest ] && [ -n "${PYSHIM_BADDIGEST:-}" ]; then
  printf '%s\n' "$PYSHIM_BADDIGEST"
  exit 0
fi
exec "${PYSHIM_REAL:?}" "$@"
SHIM
chmod +x "$pyshim2/python3"

r="$tmproot/iso-control"; mkdev "$r" noaudit; oracle="$(sha_of "$r" feat)"; rm -f "$pylog"
out="$(cd "$r" && PATH="$pyshim2:$PATH" PYSHIM_REAL="$real_py" PYSHIM_LOG="$pylog" bash "$engine" --dev "$unsigned" --audit "$ext" "$oracle" 'feat(a): bump a' a.txt 2>&1)"; rc=$?
check_eq "$rc" 0 'isolation control: a logging python3 shim still builds with --audit'
for cls in digest realpath samestat lstat subject component gitlink; do
  total="$(grep -c "^$cls " "$pylog" 2>/dev/null)"
  bad="$(grep "^$cls " "$pylog" 2>/dev/null | grep -vc "^$cls -I\$")"
  if [ "${total:-0}" -ge 1 ]; then pass_line "isolation floor: the $cls helper ran under the logging shim"; else fail_line "isolation floor: the $cls helper never ran under the logging shim"; fi
  check_eq "$bad" 0 "isolation: every $cls helper call runs python3 -I"
done

# A hashlib.py in the working directory shadowed the real module for a bare `python3 -c` and forged the recorded digest.
forge="$tmproot/forge"; mkdir -p "$forge"
printf 'class H:\n    def hexdigest(self):\n        return "0" * 64\n\n\ndef sha256(*a, **k):\n    return H()\n' > "$forge/hashlib.py"
got="$(cd "$forge" && "$real_py" -c 'import hashlib; print(hashlib.sha256(b"").hexdigest())' 2>/dev/null)"
check_eq "$got" "$(printf '%064d' 0)" 'forged digest floor: a hashlib.py in the working directory shadows the real module for a bare python3 -c'
r="$tmproot/forge-run"; mkdev "$r" noaudit; oracle="$(sha_of "$r" feat)"; adir="$tmproot/forge-art"
pre="$(git -C "$r" rev-parse --short HEAD)"; want="$(shasum -a 256 "$ext" | cut -d' ' -f1)"
out="$(cd "$forge" && bash "$engine" --dev "$unsigned" --audit "$ext" --scope "$r" --artifact-dir "$adir" "$oracle" 'feat(a): bump a' a.txt 2>&1)"; rc=$?
got="$(sed -n 's/^AUDIT_SHA256=//p' "$adir/audit-dev-$pre.txt" 2>/dev/null)"
check_eq "$rc" 0 'forged digest: a hashlib.py in the working directory does not stop the brick'
check_eq "$got" "$want" 'forged digest: the artifact records the real sha-256, not the forged one'
check_has "$out" "audit sha256: $want" 'forged digest: stdout agrees with the real sha-256'

# A digest of any other shape than 64 lowercase hex digits is no digest: refused before anything is written.
for bad in forged 63hex uppercase; do
  case "$bad" in
    forged) val='not-a-digest' ;;
    63hex) val="$(printf '%063d' 0)" ;;
    uppercase) val="$(printf '%064d' 0 | tr 0 A)" ;;
  esac
  r="$tmproot/shape-$bad"; mkdev "$r" noaudit; oracle="$(sha_of "$r" feat)"; c0="$(count_commits "$r")"; rm -f "$pylog"
  out="$(cd "$r" && PATH="$pyshim2:$PATH" PYSHIM_REAL="$real_py" PYSHIM_LOG="$pylog" PYSHIM_BADDIGEST="$val" bash "$engine" --dev "$unsigned" --audit "$ext" "$oracle" 'feat(a): bump a' a.txt 2>&1)"; rc=$?
  if [ "$(grep -c '^digest ' "$pylog" 2>/dev/null)" -ge 1 ]; then pass_line "digest shape floor ($bad): the digest helper ran under the shim"; else fail_line "digest shape floor ($bad): the digest helper never ran under the shim"; fi
  check_eq "$rc" 1 "digest shape: the $bad digest is refused: exits 1"
  check_has "$out" 'could not compute the audit digest' "digest shape: the $bad digest is refused: names the digest failure"
  check_eq "$(count_commits "$r")" "$c0" "digest shape: the $bad digest is refused: created no commit"
done

# ---------- a RESOLVED audit path is judged as resolved: a newline in it refuses ----------
# `$( )` strips trailing newlines, so a symlink to `x.sh<newline>` resolved to the DIFFERENT file `x.sh` (which passes), and a
# newline inside the resolved path split the AUDIT_PATH= line of the artifact.
nld="$tmproot/nlaud"; mkdir -p "$nld"
mkext "$nld/x.sh"
mkext "$nld/x.sh$nl" '#!/bin/sh
echo "RESULT: FAIL rc=1 the-newline-file-ran"
exit 1'
ln -s "x.sh$nl" "$nld/link-trailing"
mkext "$nld/a${nl}b.sh"
ln -s "a${nl}b.sh" "$nld/link-embedded"
r="$tmproot/nl-run"; mkdev "$r" noaudit; oracle="$(sha_of "$r" feat)"
refused2 'audit symlink whose target ends in a newline' 'resolves to a path containing a newline' "$unsigned" --audit "$nld/link-trailing"
check_lacks "$out" 'RESULT: PASS' 'audit symlink whose target ends in a newline: no other file ran in its place'
refused2 'audit symlink whose target holds an embedded newline' 'resolves to a path containing a newline' "$unsigned" --audit "$nld/link-embedded"

# ======================= the path to the tree's own audit is judged component by component =======================
# `lstat` and `git ls-tree` on the literal path skills/audit/audit.sh both look THROUGH a symbolic link, so a link at
# `skills` or at `skills/audit` hid a tree's own audit from both: with the target audit removed under skip-worktree (or
# the link left dangling) the path read absent on both sides and `--audit` replaced the tree's own gate. The verdict is an
# allowlist over each component, in the worktree and in HEAD; the controls below are the audit-less shapes that must still
# admit the flag.

# A tracked link skills/audit -> ../tools/audit, whose target audit is the tree's own and is removed under skip-worktree.
r="$tmproot/comp-linkdir-sw"; mkdev "$r" linkdir; oracle="$(sha_of "$r" feat)"
git -C "$r" update-index --skip-worktree tools/audit/audit.sh && rm "$r/tools/audit/audit.sh"
check_eq "$(porcelain_lines "$r")" 0 'component floor: a removed skip-worktree audit behind a symlinked directory reads clean to git status'
check_eq "$(git -C "$r" ls-tree HEAD skills/audit | cut -c1-6)" '120000' 'component floor: HEAD tracks skills/audit as a symbolic link'
if [ ! -e "$r/skills/audit/audit.sh" ] && [ ! -L "$r/skills/audit/audit.sh" ]; then
  pass_line 'component floor: the audit path reads absent through the link'
else
  fail_line 'component floor: the audit path still resolves through the link'
fi
refused2 'own audit behind a tracked symlinked directory, removed under skip-worktree, with the flag' 'could not judge whether the scope has its own' "$unsigned" --audit "$ext"
check_has "$out" 'skills/audit is a symbolic link in the worktree' 'own audit behind a tracked symlinked directory, removed under skip-worktree, with the flag: names the link'
c0="$(count_commits "$r")"
out="$(cd "$r" && bash "$engine" --dev "$unsigned" "$oracle" 'feat(a): bump a' a.txt 2>&1)"; rc=$?
check_eq "$rc" 1 'own audit behind a tracked symlinked directory, removed under skip-worktree, no flag: exits 1'
check_has "$out" 'could not judge whether' 'own audit behind a tracked symlinked directory, removed under skip-worktree, no flag: names the refusal'
check_has "$out" 'skills/audit is a symbolic link in the worktree' 'own audit behind a tracked symlinked directory, removed under skip-worktree, no flag: names the link'
check_lacks "$out" '--audit' 'own audit behind a tracked symlinked directory, removed under skip-worktree, no flag: does not advise the override'
check_eq "$(count_commits "$r")" "$c0" 'own audit behind a tracked symlinked directory, removed under skip-worktree, no flag: created no commit'

# The same layout with the audit PRESENT: the own audit still runs as the scope's, and the flag is still refused as before.
r="$tmproot/comp-linkdir-present"; mkdev "$r" linkdir; oracle="$(sha_of "$r" feat)"
refused2 'own audit behind a tracked symlinked directory, present, with the flag' 'is only for a repo with none' "$unsigned" --audit "$ext"
out="$(cd "$r" && bash "$engine" --dev "$unsigned" "$oracle" 'feat(a): bump a' a.txt 2>&1)"; rc=$?
check_eq "$rc" 0 'own audit behind a tracked symlinked directory, present, no flag: builds'
check_has "$out" 'audit source: scope' 'own audit behind a tracked symlinked directory, present, no flag: runs as the scope audit'
check_has "$out" '(the tree being proven)' 'own audit behind a tracked symlinked directory, present, no flag: the header reads the tree being proven'

# A tracked skills/audit link to a directory that is not there.
r="$tmproot/comp-linkdangling"; mkdev "$r" linkdangling; oracle="$(sha_of "$r" feat)"
refused2 'own audit path that is a tracked link to a missing directory, with the flag' 'could not judge whether the scope has its own' "$unsigned" --audit "$ext"
check_has "$out" 'skills/audit is a symbolic link in the worktree' 'own audit path that is a tracked link to a missing directory, with the flag: names the link'
c0="$(count_commits "$r")"
out="$(cd "$r" && bash "$engine" --dev "$unsigned" "$oracle" 'feat(a): bump a' a.txt 2>&1)"; rc=$?
check_eq "$rc" 1 'own audit path that is a tracked link to a missing directory, no flag: exits 1'
check_lacks "$out" '--audit' 'own audit path that is a tracked link to a missing directory, no flag: does not advise the override'
check_eq "$(count_commits "$r")" "$c0" 'own audit path that is a tracked link to a missing directory, no flag: created no commit'

# A link at `skills` itself, the audit removed under skip-worktree behind it.
r="$tmproot/comp-skillslink-sw"; mkdev "$r" skillslink; oracle="$(sha_of "$r" feat)"
git -C "$r" update-index --skip-worktree tools/audit/audit.sh && rm "$r/tools/audit/audit.sh"
check_eq "$(porcelain_lines "$r")" 0 'component floor: a removed skip-worktree audit behind a symlinked skills reads clean to git status'
refused2 'own audit behind a tracked symlinked skills, removed under skip-worktree, with the flag' 'could not judge whether the scope has its own' "$unsigned" --audit "$ext"
check_has "$out" 'skills is a symbolic link in the worktree' 'own audit behind a tracked symlinked skills, removed under skip-worktree, with the flag: names the link'

# A link at `skills` that HEAD knows nothing about: only the worktree can refuse it.
r="$tmproot/comp-skillslink-untracked"; mkdev "$r" noaudit; oracle="$(sha_of "$r" feat)"
ln -s "$tmproot/ext" "$r/skills"
refused2 'an untracked symlink at skills, with the flag' 'could not judge whether the scope has its own' "$unsigned" --audit "$ext"
check_has "$out" 'skills is a symbolic link in the worktree' 'an untracked symlink at skills, with the flag: names the link'

# A regular file where the directory skills/audit belongs: a refusal, said in words that say what was found.
r="$tmproot/comp-auditfile"; mkdev "$r" noaudit; oracle="$(sha_of "$r" feat)"
mkdir -p "$r/skills"; printf 'x\n' > "$r/skills/audit"
git -C "$r" add skills/audit && git -C "$r" commit -qm 'chore: a file at skills/audit'
refused2 'a regular file at skills/audit, with the flag' 'could not judge whether the scope has its own' "$unsigned" --audit "$ext"
check_has "$out" 'skills/audit is not a directory in the worktree' 'a regular file at skills/audit, with the flag: says it is not a directory'
c0="$(count_commits "$r")"
out="$(cd "$r" && bash "$engine" --dev "$unsigned" "$oracle" 'feat(a): bump a' a.txt 2>&1)"; rc=$?
check_eq "$rc" 1 'a regular file at skills/audit, no flag: exits 1'
check_has "$out" 'skills/audit is not a directory in the worktree' 'a regular file at skills/audit, no flag: says it is not a directory'
check_lacks "$out" 'could not be examined' 'a regular file at skills/audit, no flag: does not say the path could not be examined'
check_lacks "$out" '--audit' 'a regular file at skills/audit, no flag: does not advise the override'
check_eq "$(count_commits "$r")" "$c0" 'a regular file at skills/audit, no flag: created no commit'

# HEAD alone holds the link: the worktree has real directories, so only the HEAD half of the allowlist can refuse.
r="$tmproot/comp-head-auditlink"; mkdev "$r" linkdir; oracle="$(sha_of "$r" feat)"
rm "$r/skills/audit"; mkdir "$r/skills/audit"
refused2 'HEAD tracks skills/audit as a symbolic link and the worktree holds a directory, with the flag' 'could not judge whether the scope has its own' "$unsigned" --audit "$ext"
check_has "$out" 'HEAD holds a symbolic link' 'HEAD tracks skills/audit as a symbolic link and the worktree holds a directory, with the flag: names HEAD'
r="$tmproot/comp-head-skillslink"; mkdev "$r" skillslink; oracle="$(sha_of "$r" feat)"
rm "$r/skills"; mkdir -p "$r/skills/audit"
refused2 'HEAD tracks skills as a symbolic link and the worktree holds a directory, with the flag' 'could not judge whether the scope has its own' "$unsigned" --audit "$ext"
check_has "$out" 'HEAD holds a symbolic link' 'HEAD tracks skills as a symbolic link and the worktree holds a directory, with the flag: names HEAD'

# A component helper whose interpreter fails with status 1 has judged nothing, so it refuses; the logging shim of the
# isolation rows can fail one helper class (here the component helper).
r="$tmproot/comp-shimfail"; mkdev "$r" noaudit; oracle="$(sha_of "$r" feat)"; rm -f "$pylog"
c0="$(count_commits "$r")"
out="$(cd "$r" && PATH="$pyshim2:$PATH" PYSHIM_REAL="$real_py" PYSHIM_LOG="$pylog" PYSHIM_FAIL_CLASS=component bash "$engine" --dev "$unsigned" --audit "$ext" "$oracle" 'feat(a): bump a' a.txt 2>&1)"; rc=$?
if [ "$(grep -c '^component ' "$pylog" 2>/dev/null)" -ge 1 ]; then pass_line 'component floor: the component helper ran under the failing shim'; else fail_line 'component floor: the component helper never ran under the failing shim'; fi
check_eq "$rc" 2 'a component helper whose interpreter exits 1: exits 2'
check_has "$out" 'could not judge whether the scope has its own' 'a component helper whose interpreter exits 1: names the refusal'
check_has "$out" 'could not be examined in the worktree' 'a component helper whose interpreter exits 1: says what was found'
check_lacks "$out" 'RESULT: PASS' 'a component helper whose interpreter exits 1: prints no PASS verdict'
check_eq "$(count_commits "$r")" "$c0" 'a component helper whose interpreter exits 1: created no commit'

# Controls: the audit-less shapes that are NOT a link or a file in the way still admit the flag.
r="$tmproot/comp-control-noskills"; mkdev "$r" noaudit; oracle="$(sha_of "$r" feat)"
out="$(cd "$r" && bash "$engine" --dev "$unsigned" --audit "$ext" "$oracle" 'feat(a): bump a' a.txt 2>&1)"; rc=$?
check_eq "$rc" 0 'allowlist control: a scope with no skills directory admits --audit'
check_has "$out" 'audit source: override' 'allowlist control: a scope with no skills directory runs the override'
r="$tmproot/comp-control-otherskills"; mkdev "$r" skillsother; oracle="$(sha_of "$r" feat)"
out="$(cd "$r" && bash "$engine" --dev "$unsigned" --audit "$ext" "$oracle" 'feat(a): bump a' a.txt 2>&1)"; rc=$?
check_eq "$rc" 0 'allowlist control: a scope whose skills directory holds other things admits --audit'
check_has "$out" 'audit source: override' 'allowlist control: a scope whose skills directory holds other things runs the override'
r="$tmproot/comp-control-emptyaudit"; mkdev "$r" noaudit; oracle="$(sha_of "$r" feat)"
mkdir -p "$r/skills/audit"
out="$(cd "$r" && bash "$engine" --dev "$unsigned" --audit "$ext" "$oracle" 'feat(a): bump a' a.txt 2>&1)"; rc=$?
check_eq "$rc" 0 'allowlist control: a scope with an empty untracked skills/audit directory admits --audit'
check_has "$out" 'audit source: override' 'allowlist control: a scope with an empty untracked skills/audit directory runs the override'

# ======================= git's view of the worktree must be faithful before an absence is claimed =======================
# `git status` reads clean in states where the worktree does not hold HEAD: a skip-worktree entry whose file is gone, an
# assume-unchanged entry, a sparse checkout, a lying fsmonitor, and (on a volume that folds case) a case-variant spelling of
# the audit's path in HEAD, which `git ls-tree` cannot see. The engine refuses to claim that a tree has no audit unless the whole
# index is plain, sparse checkout is off and a status that trusts no fsmonitor agrees with the configured one. The controls
# are the audit-less shapes that must still admit the flag and the repo with its own audit that must not be touched.
fsm_hook="$tmproot/fsm-hook.sh"
printf '#!/bin/sh\nprintf "tok\\0"\n' > "$fsm_hook"; chmod +x "$fsm_hook"

# The audit tracked under a case-variant spelling (Skills/audit/audit.sh), skip-worktree, the file removed.
r="$tmproot/idx-case-file"; mkdev "$r" casevariant; oracle="$(sha_of "$r" feat)"
git -C "$r" update-index --skip-worktree Skills/audit/audit.sh && rm "$r/Skills/audit/audit.sh"
check_eq "$(porcelain_lines "$r")" 0 'index floor: a removed skip-worktree audit under a case-variant spelling reads clean to git status'
check_eq "$(git -C "$r" ls-tree HEAD Skills/audit/audit.sh | cut -c1-6)" '100755' 'index floor: HEAD tracks the audit under the case-variant spelling'
if [ ! -e "$r/skills/audit/audit.sh" ] && [ ! -L "$r/skills/audit/audit.sh" ]; then
  pass_line 'index floor: the audit path in its usual spelling reads absent'
else
  fail_line 'index floor: the audit path in its usual spelling still resolves'
fi
refused2 'case-variant audit, skip-worktree, file removed, with the flag' 'could not judge whether the scope has its own' "$unsigned" --audit "$ext"
check_has "$out" 'skip-worktree' 'case-variant audit, skip-worktree, file removed, with the flag: names the tag'
c0="$(count_commits "$r")"
out="$(cd "$r" && bash "$engine" --dev "$unsigned" "$oracle" 'feat(a): bump a' a.txt 2>&1)"; rc=$?
check_eq "$rc" 1 'case-variant audit, skip-worktree, file removed, no flag: exits 1'
check_has "$out" 'skip-worktree' 'case-variant audit, skip-worktree, file removed, no flag: names the tag'
check_lacks "$out" '--audit' 'case-variant audit, skip-worktree, file removed, no flag: does not advise the override'
check_eq "$(count_commits "$r")" "$c0" 'case-variant audit, skip-worktree, file removed, no flag: created no commit'

# The same, with the whole directory removed.
r="$tmproot/idx-case-dir"; mkdev "$r" casevariant; oracle="$(sha_of "$r" feat)"
git -C "$r" update-index --skip-worktree Skills/audit/audit.sh && rm -r "$r/Skills"
check_eq "$(porcelain_lines "$r")" 0 'index floor: a removed skip-worktree case-variant directory reads clean to git status'
refused2 'case-variant audit, skip-worktree, whole directory removed, with the flag' 'could not judge whether the scope has its own' "$unsigned" --audit "$ext"
check_has "$out" 'skip-worktree' 'case-variant audit, skip-worktree, whole directory removed, with the flag: names the tag'

# One bit per row, on a path that has nothing to do with the audit.
r="$tmproot/idx-skipwt-other"; mkdev "$r" noaudit; oracle="$(sha_of "$r" feat)"
git -C "$r" update-index --skip-worktree b.txt
check_eq "$(git -C "$r" ls-files -v b.txt | cut -c1-1)" 'S' 'index floor: the skip-worktree tag reads S'
refused2 'skip-worktree on an unrelated path, with the flag' 'could not judge whether the scope has its own' "$unsigned" --audit "$ext"
check_has "$out" 'skip-worktree' 'skip-worktree on an unrelated path, with the flag: names the tag'
c0="$(count_commits "$r")"
out="$(cd "$r" && bash "$engine" --dev "$unsigned" "$oracle" 'feat(a): bump a' a.txt 2>&1)"; rc=$?
check_eq "$rc" 1 'skip-worktree on an unrelated path, no flag: exits 1'
check_lacks "$out" '--audit' 'skip-worktree on an unrelated path, no flag: does not advise the override'
check_eq "$(count_commits "$r")" "$c0" 'skip-worktree on an unrelated path, no flag: created no commit'

r="$tmproot/idx-assume-other"; mkdev "$r" noaudit; oracle="$(sha_of "$r" feat)"
git -C "$r" update-index --assume-unchanged b.txt
check_eq "$(git -C "$r" ls-files -v b.txt | cut -c1-1)" 'h' 'index floor: the assume-unchanged tag reads h'
refused2 'assume-unchanged on an unrelated path, with the flag' 'could not judge whether the scope has its own' "$unsigned" --audit "$ext"
check_has "$out" 'assume-unchanged' 'assume-unchanged on an unrelated path, with the flag: names the cause'
c0="$(count_commits "$r")"
out="$(cd "$r" && bash "$engine" --dev "$unsigned" "$oracle" 'feat(a): bump a' a.txt 2>&1)"; rc=$?
check_eq "$rc" 1 'assume-unchanged on an unrelated path, no flag: exits 1'
check_lacks "$out" '--audit' 'assume-unchanged on an unrelated path, no flag: does not advise the override'
check_eq "$(count_commits "$r")" "$c0" 'assume-unchanged on an unrelated path, no flag: created no commit'

# Sparse checkout on, with nothing excluded: no entry carries a tag, so only the configuration can refuse it.
r="$tmproot/idx-sparse"; mkdev "$r" noaudit; oracle="$(sha_of "$r" feat)"
git -C "$r" sparse-checkout init --cone >/dev/null 2>&1
check_eq "$(git -C "$r" config --bool --get core.sparseCheckout)" true 'index floor: sparse checkout reads on'
check_eq "$(git -C "$r" ls-files -v 2>/dev/null | grep -vc '^H ')" 0 'index floor: no index entry carries a tag under a sparse checkout that excludes nothing'
refused2 'sparse checkout on, nothing excluded, with the flag' 'could not judge whether the scope has its own' "$unsigned" --audit "$ext"
check_has "$out" 'sparse checkout is on' 'sparse checkout on, nothing excluded, with the flag: names the cause'
c0="$(count_commits "$r")"
out="$(cd "$r" && bash "$engine" --dev "$unsigned" "$oracle" 'feat(a): bump a' a.txt 2>&1)"; rc=$?
check_eq "$rc" 1 'sparse checkout on, nothing excluded, no flag: exits 1'
check_lacks "$out" '--audit' 'sparse checkout on, nothing excluded, no flag: does not advise the override'

# A fsmonitor hook that claims nothing changed makes the configured status read clean over a removed file.
r="$tmproot/idx-fsmonitor"; mkdev "$r" casevariant; oracle="$(sha_of "$r" feat)"
git -C "$r" config core.fsmonitor "$fsm_hook"
git -C "$r" status --porcelain > /dev/null
rm "$r/Skills/audit/audit.sh"
check_eq "$(porcelain_lines "$r")" 0 'index floor: a lying fsmonitor hook makes git status read clean over a removed audit'
check_eq "$(git --no-optional-locks -C "$r" -c core.fsmonitor=false status --porcelain | wc -l | tr -d ' ')" 1 'index floor: a status that trusts no fsmonitor reports the removed audit'
refused2 'removed case-variant audit under a lying fsmonitor, with the flag' 'could not judge whether the scope has its own' "$unsigned" --audit "$ext"
check_has "$out" 'does not trust fsmonitor' 'removed case-variant audit under a lying fsmonitor, with the flag: names the cause'
c0="$(count_commits "$r")"
out="$(cd "$r" && bash "$engine" --dev "$unsigned" "$oracle" 'feat(a): bump a' a.txt 2>&1)"; rc=$?
check_eq "$rc" 1 'removed case-variant audit under a lying fsmonitor, no flag: exits 1'
check_lacks "$out" '--audit' 'removed case-variant audit under a lying fsmonitor, no flag: does not advise the override'
check_eq "$(count_commits "$r")" "$c0" 'removed case-variant audit under a lying fsmonitor, no flag: created no commit'

# An index the engine cannot read is not an index it can vouch for.
if [ "$(id -u)" = 0 ]; then
  skip_line 'an unreadable index' 'running as root: root reads a mode 000 file'
else
  r="$tmproot/idx-unreadable"; mkdev "$r" noaudit; oracle="$(sha_of "$r" feat)"
  chmod 000 "$r/.git/index"
  refused2 'an unreadable index, with the flag' 'could not judge whether the scope has its own' "$unsigned" --audit "$ext"
  check_has "$out" 'the index could not be read' 'an unreadable index, with the flag: names the cause'
  chmod 600 "$r/.git/index"
fi

# An uninitialised submodule at skills/audit or skills leaves status clean, every tag plain and sparse off, with an empty
# directory on disk: only the HEAD side of the component allowlist refuses it.
r="$tmproot/idx-gitlink-audit"; mkdev "$r" noaudit; oracle="$(sha_of "$r" feat)"
mkdir -p "$r/skills/audit"
git -C "$r" update-index --add --cacheinfo 160000,e69de29bb2d1d6434b8b29ae775ad8c2e48c5391,skills/audit
git -C "$r" commit -qm 'chore: a submodule at skills/audit'
check_eq "$(porcelain_lines "$r")" 0 'index floor: an uninitialised submodule at skills/audit reads clean to git status'
check_eq "$(git -C "$r" ls-tree HEAD skills/audit | cut -c1-6)" '160000' 'index floor: HEAD tracks skills/audit as a submodule'
check_eq "$(git -C "$r" ls-files -v 2>/dev/null | grep -vc '^H ')" 0 'index floor: no index entry carries a tag beside the submodule'
refused2 'a submodule at skills/audit, with the flag' 'could not judge whether the scope has its own' "$unsigned" --audit "$ext"
check_has "$out" 'HEAD holds a symbolic link, a submodule or a file' 'a submodule at skills/audit, with the flag: names HEAD'
c0="$(count_commits "$r")"
out="$(cd "$r" && bash "$engine" --dev "$unsigned" "$oracle" 'feat(a): bump a' a.txt 2>&1)"; rc=$?
check_eq "$rc" 1 'a submodule at skills/audit, no flag: exits 1'
check_lacks "$out" '--audit' 'a submodule at skills/audit, no flag: does not advise the override'
check_eq "$(count_commits "$r")" "$c0" 'a submodule at skills/audit, no flag: created no commit'
r="$tmproot/idx-gitlink-skills"; mkdev "$r" noaudit; oracle="$(sha_of "$r" feat)"
mkdir -p "$r/skills"
git -C "$r" update-index --add --cacheinfo 160000,e69de29bb2d1d6434b8b29ae775ad8c2e48c5391,skills
git -C "$r" commit -qm 'chore: a submodule at skills'
check_eq "$(porcelain_lines "$r")" 0 'index floor: an uninitialised submodule at skills reads clean to git status'
refused2 'a submodule at skills, with the flag' 'could not judge whether the scope has its own' "$unsigned" --audit "$ext"
check_has "$out" 'HEAD holds a symbolic link, a submodule or a file' 'a submodule at skills, with the flag: names HEAD'

# Controls. An audit-less repo with none of these states still admits the flag, whether or not it uses fsmonitor honestly.
r="$tmproot/idx-control-plain"; mkdev "$r" noaudit; oracle="$(sha_of "$r" feat)"
out="$(cd "$r" && bash "$engine" --dev "$unsigned" --audit "$ext" "$oracle" 'feat(a): bump a' a.txt 2>&1)"; rc=$?
check_eq "$rc" 0 'precondition control: an ordinary audit-less repo admits --audit'
check_has "$out" 'audit source: override' 'precondition control: an ordinary audit-less repo runs the override'
r="$tmproot/idx-control-fsmonitor"; mkdev "$r" noaudit; oracle="$(sha_of "$r" feat)"
git -C "$r" config core.fsmonitor "$fsm_hook"
git -C "$r" status --porcelain > /dev/null
check_eq "$(git -C "$r" ls-files -f | cut -c1-1 | sort -u)" 'h' 'index floor: the fsmonitor-valid bits are set on every entry'
out="$(cd "$r" && bash "$engine" --dev "$unsigned" --audit "$ext" "$oracle" 'feat(a): bump a' a.txt 2>&1)"; rc=$?
check_eq "$rc" 0 'precondition control: an audit-less repo with a primed, honest fsmonitor admits --audit'
check_has "$out" 'audit source: override' 'precondition control: an audit-less repo with a primed, honest fsmonitor runs the override'
# A repo WITH its own present audit is never asked to prove an absence, so a tag elsewhere does not touch it.
r="$tmproot/idx-control-own-skipwt"; mkdev "$r"; oracle="$(sha_of "$r" feat)"
git -C "$r" update-index --skip-worktree b.txt
out="$(cd "$r" && bash "$engine" --dev "$unsigned" "$oracle" 'feat(a): bump a' a.txt 2>&1)"; rc=$?
check_eq "$rc" 0 'precondition control: an own audit with a skip-worktree entry elsewhere still builds'
check_has "$out" 'audit source: scope' 'precondition control: an own audit with a skip-worktree entry elsewhere runs as the scope audit'
r="$tmproot/idx-control-own-assume"; mkdev "$r"; oracle="$(sha_of "$r" feat)"
git -C "$r" update-index --assume-unchanged b.txt
out="$(cd "$r" && bash "$engine" --dev "$unsigned" "$oracle" 'feat(a): bump a' a.txt 2>&1)"; rc=$?
check_eq "$rc" 0 'precondition control: an own audit with an assume-unchanged entry elsewhere still builds'
check_has "$out" 'audit source: scope' 'precondition control: an own audit with an assume-unchanged entry elsewhere runs as the scope audit'

# ======================= a submodule entry must not be the audit's own directory under another spelling =======================
# `git status` vouches for the worktree content of every file and link entry once the index is plain, but for no gitlink: an
# uninitialised submodule is an empty directory that reads clean, and a populated or deleted one reads clean under
# `ignore = dirty` or `ignore = all`. On a volume that folds case a gitlink spelled `Skills` or `skills/Audit` IS the directory
# `skills` or `skills/audit`, which `git ls-tree` (case-sensitive) cannot see. The engine compares by file identity, so the rows
# that need the volume to fold case are counted SKIPs on one that does not.
mkdir "$tmproot/casep"
if [ -d "$tmproot/CASEP" ]; then folds=yes; else folds=no; fi
fake_sha=e69de29bb2d1d6434b8b29ae775ad8c2e48c5391

if [ "$folds" = yes ]; then
  # A fake gitlink spelled Skills, with the empty directory a clone would leave.
  r="$tmproot/gl-skills-fake"; mkdev "$r" noaudit; oracle="$(sha_of "$r" feat)"
  mkdir -p "$r/Skills"
  git -C "$r" update-index --add --cacheinfo "160000,$fake_sha,Skills"
  git -C "$r" commit -qm 'chore: a submodule at Skills'
  check_eq "$(porcelain_lines "$r")" 0 'submodule floor: an uninitialised case-variant submodule reads clean to git status'
  check_eq "$(git -C "$r" ls-tree HEAD Skills | cut -c1-6)" '160000' 'submodule floor: HEAD tracks the case-variant spelling as a submodule'
  if [ -d "$r/skills" ]; then pass_line 'submodule floor: the usual spelling resolves to the submodule directory'; else fail_line 'submodule floor: the usual spelling does not resolve to the submodule directory'; fi
  refused2 'an uninitialised submodule spelled Skills, with the flag' 'could not judge whether the scope has its own' "$unsigned" --audit "$ext"
  check_has "$out" 'is the same directory as skills or skills/audit' 'an uninitialised submodule spelled Skills, with the flag: names the identity'
  c0="$(count_commits "$r")"
  out="$(cd "$r" && bash "$engine" --dev "$unsigned" "$oracle" 'feat(a): bump a' a.txt 2>&1)"; rc=$?
  check_eq "$rc" 1 'an uninitialised submodule spelled Skills, no flag: exits 1'
  check_has "$out" 'is the same directory as skills or skills/audit' 'an uninitialised submodule spelled Skills, no flag: names the identity'
  check_lacks "$out" '--audit' 'an uninitialised submodule spelled Skills, no flag: does not advise the override'
  check_eq "$(count_commits "$r")" "$c0" 'an uninitialised submodule spelled Skills, no flag: created no commit'

  # A fake gitlink spelled skills/Audit beside a tracked file, so skills itself is an ordinary directory.
  r="$tmproot/gl-audit-fake"; mkdev "$r" noaudit; oracle="$(sha_of "$r" feat)"
  mkdir -p "$r/skills/Audit"; printf 'k\n' > "$r/skills/keep.txt"
  git -C "$r" add skills/keep.txt
  git -C "$r" update-index --add --cacheinfo "160000,$fake_sha,skills/Audit"
  git -C "$r" commit -qm 'chore: a submodule at skills/Audit'
  check_eq "$(porcelain_lines "$r")" 0 'submodule floor: an uninitialised submodule spelled skills/Audit reads clean to git status'
  refused2 'an uninitialised submodule spelled skills/Audit, with the flag' 'could not judge whether the scope has its own' "$unsigned" --audit "$ext"
  check_has "$out" 'is the same directory as skills or skills/audit' 'an uninitialised submodule spelled skills/Audit, with the flag: names the identity'
  c0="$(count_commits "$r")"
  out="$(cd "$r" && bash "$engine" --dev "$unsigned" "$oracle" 'feat(a): bump a' a.txt 2>&1)"; rc=$?
  check_eq "$rc" 1 'an uninitialised submodule spelled skills/Audit, no flag: exits 1'
  check_lacks "$out" '--audit' 'an uninitialised submodule spelled skills/Audit, no flag: does not advise the override'

  # A populated submodule spelled Skills whose audit was deleted inside it, under ignore = dirty: status reads clean.
  sub="$tmproot/gl-subsrc"; git init -q -b main "$sub"; mkdir -p "$sub/audit"; cp "$ext" "$sub/audit/audit.sh"
  git -C "$sub" add -A; git -C "$sub" -c user.name=t -c user.email=t@test.invalid -c commit.gpgsign=false commit -qm sub
  r="$tmproot/gl-skills-populated"; mkdev "$r" noaudit; oracle="$(sha_of "$r" feat)"
  git -C "$r" -c protocol.file.allow=always submodule add -q "$sub" Skills > /dev/null 2>&1
  git -C "$r" config -f .gitmodules submodule.Skills.ignore dirty
  git -C "$r" add -A; git -C "$r" commit -qm 'chore: a populated submodule at Skills'
  rm "$r/Skills/audit/audit.sh"
  check_eq "$(porcelain_lines "$r")" 0 'submodule floor: a populated submodule that lost its audit reads clean under ignore = dirty'
  check_eq "$(git -C "$r/Skills" status --porcelain 2>/dev/null | wc -l | tr -d ' ')" 1 'submodule floor: the submodule itself reports the removed audit'
  refused2 'a populated submodule spelled Skills that lost its audit, with the flag' 'could not judge whether the scope has its own' "$unsigned" --audit "$ext"
  check_has "$out" 'is the same directory as skills or skills/audit' 'a populated submodule spelled Skills that lost its audit, with the flag: names the identity'

  # A submodule spelled skills/audit/Audit.sh is a DIRECTORY at the audit file's own path: the shell's existence test sees it
  # first, so it is a present, unusable audit and the flag is refused as for any broken tree.
  r="$tmproot/gl-file-fake"; mkdev "$r" noaudit; oracle="$(sha_of "$r" feat)"
  mkdir -p "$r/skills/audit/Audit.sh"
  git -C "$r" update-index --add --cacheinfo "160000,$fake_sha,skills/audit/Audit.sh"
  git -C "$r" commit -qm 'chore: a submodule at skills/audit/Audit.sh'
  check_eq "$([ -d "$r/skills/audit/audit.sh" ] && echo yes || echo no)" yes 'submodule floor: the audit file path resolves to the submodule directory'
  refused2 'a submodule spelled skills/audit/Audit.sh, with the flag' '(exists but is not an executable regular file)' "$unsigned" --audit "$ext"
else
  skip_line 'case-variant submodule rows' 'the filesystem is case-sensitive, so a spelling variant is not the same directory'
fi

# A submodule whose directory is missing from disk reads clean when submodule changes are ignored, and identity cannot be
# asked of a path that is not there: refused, whatever the spelling. (Without the ignore setting git status shows it as a deletion.)
r="$tmproot/gl-missing"; mkdev "$r" noaudit; oracle="$(sha_of "$r" feat)"
mkdir -p "$r/vendor/lib"
git -C "$r" update-index --add --cacheinfo "160000,$fake_sha,vendor/lib"
git -C "$r" commit -qm 'chore: a submodule at vendor/lib'
rmdir "$r/vendor/lib" "$r/vendor"
check_eq "$(git -C "$r" status --porcelain | wc -l | tr -d ' ')" 1 'submodule floor: a submodule whose directory is missing shows as a change by default'
git -C "$r" config diff.ignoreSubmodules all
check_eq "$(porcelain_lines "$r")" 0 'submodule floor: the same state reads clean under diff.ignoreSubmodules=all'
refused2 'a submodule whose directory is missing, status ignoring submodules, with the flag' 'could not judge whether the scope has its own' "$unsigned" --audit "$ext"
check_has "$out" 'has no directory on disk' 'a submodule whose directory is missing, status ignoring submodules, with the flag: names the cause'

# Controls: a submodule elsewhere, with its directory present, is none of the audit's business.
r="$tmproot/gl-control-elsewhere"; mkdev "$r" subnoaudit; oracle="$(sha_of "$r" feat)"
check_eq "$(git -C "$r" ls-tree HEAD vendor/lib | cut -c1-6)" '160000' 'submodule floor: the base holds a submodule at vendor/lib'
out="$(cd "$r" && bash "$engine" --dev "$unsigned" --audit "$ext" "$oracle" 'feat(a): bump a' a.txt 2>&1)"; rc=$?
check_eq "$rc" 0 'submodule control: an audit-less repo with a submodule elsewhere admits --audit'
check_has "$out" 'audit source: override' 'submodule control: an audit-less repo with a submodule elsewhere runs the override'
r="$tmproot/gl-control-own"; mkdev "$r" subown; oracle="$(sha_of "$r" feat)"
out="$(cd "$r" && bash "$engine" --dev "$unsigned" "$oracle" 'feat(a): bump a' a.txt 2>&1)"; rc=$?
check_eq "$rc" 0 'submodule control: an own audit with a submodule elsewhere still builds'
check_has "$out" 'audit source: scope' 'submodule control: an own audit with a submodule elsewhere runs as the scope audit'

# A submodule comparison whose interpreter exits 1 has judged nothing, so it refuses.
r="$tmproot/gl-shimfail"; mkdev "$r" noaudit; oracle="$(sha_of "$r" feat)"; rm -f "$pylog"
c0="$(count_commits "$r")"
out="$(cd "$r" && PATH="$pyshim2:$PATH" PYSHIM_REAL="$real_py" PYSHIM_LOG="$pylog" PYSHIM_FAIL_CLASS=gitlink bash "$engine" --dev "$unsigned" --audit "$ext" "$oracle" 'feat(a): bump a' a.txt 2>&1)"; rc=$?
if [ "$(grep -c '^gitlink ' "$pylog" 2>/dev/null)" -ge 1 ]; then pass_line 'submodule floor: the submodule comparison ran under the failing shim'; else fail_line 'submodule floor: the submodule comparison never ran under the failing shim'; fi
check_eq "$rc" 2 'a submodule comparison whose interpreter exits 1: exits 2'
check_has "$out" 'could not judge whether the scope has its own' 'a submodule comparison whose interpreter exits 1: names the refusal'
check_has "$out" 'could not be compared' 'a submodule comparison whose interpreter exits 1: says what was found'
check_lacks "$out" 'RESULT: PASS' 'a submodule comparison whose interpreter exits 1: prints no PASS verdict'
check_eq "$(count_commits "$r")" "$c0" 'a submodule comparison whose interpreter exits 1: created no commit'

# An unmerged path is tagged M, not assume-unchanged, and the refusal must say which it found.
r="$tmproot/idx-unmerged"; mkdev "$r" noaudit
printf 'dev\n' > "$r/a.txt"; git -C "$r" commit -qam 'chore: a on dev'
git -C "$r" merge feat > /dev/null 2>&1
oracle="$(sha_of "$r" feat)"
check_eq "$(git -C "$r" ls-files -v a.txt | cut -c1-1 | sort -u)" 'M' 'index floor: an unmerged path reads M'
refused2 'an unmerged path, with the flag' 'could not judge whether the scope has its own' "$unsigned" --audit "$ext"
check_has "$out" 'a tag other than H' 'an unmerged path, with the flag: names the tag it found'
check_lacks "$out" 'assume-unchanged' 'an unmerged path, with the flag: does not blame assume-unchanged'

# A malformed core.sparseCheckout breaks every git read, scope resolution included, so the engine refuses at argument handling
# and the sparse read of the precondition is never reached.
r="$tmproot/idx-badsparse"; mkdev "$r" noaudit; oracle="$(sha_of "$r" feat)"
printf '[core]\n\tsparseCheckout = maybe\n' >> "$r/.git/config"
refused2 'a malformed core.sparseCheckout, with the flag' 'not a git repository' "$unsigned" --audit "$ext"

printf '\n%d passed, %d failed, %d skipped\n' "$pass" "$fail" "$skip"
[ "$fail" -eq 0 ]

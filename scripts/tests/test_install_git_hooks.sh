#!/usr/bin/env bash
set -uo pipefail

# Script: test_install_git_hooks.sh
# Purpose: Regression tests for scripts/install-git-hooks.sh, and the first suite it has had.
#          The subject owns the overwrite policy for the publication boundary's load-bearing
#          hook, so every row asserts on the resulting INSTALLED DIGEST, never on the script's
#          own success report.
# Usage:   ./scripts/tests/test_install_git_hooks.sh
#
# Sandbox root is pinned with `pwd -P`: a logical path under a symlinked parent does not
# physically contain its files, which silently routes path-resolving tools down a different
# branch than the one under test.

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
subject="$here/../../scripts/install-git-hooks.sh"
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
  # Herestring, not `printf … | grep -qF`. This file runs under `set -o pipefail`, and `grep -q`
  # exits at its first match, SIGPIPEing the writer -- pipefail then surfaces 141 and the check
  # reports MISSING on a needle that is present. Today's haystacks are far under the pipe buffer
  # so no row is flaky, but this is the same hazard the subject was just hardened against, and
  # leaving it in the harness built to guard against it is how it comes back.
  if grep -qF -- "$2" <<<"$1"; then pass_line "$3"
  else fail_line "$3 (missing [$2])"; fi
}

sha_of() { shasum -a 256 "$1" | cut -d' ' -f1; }

# mk NAME -> a repo at $tmproot/NAME whose git-hooks/pre-push has three generations of history
# (v1, v2, v3) and a copy of the subject at scripts/install-git-hooks.sh.
mk() {
  local r="$tmproot/$1"
  mkdir -p "$r/git-hooks" "$r/scripts"
  git init -q -b dev "$r"
  git -C "$r" config user.email t@t.invalid
  git -C "$r" config user.name t
  git -C "$r" config commit.gpgsign false
  cp "$subject" "$r/scripts/install-git-hooks.sh"
  chmod +x "$r/scripts/install-git-hooks.sh"

  local v
  for v in v1 v2 v3; do
    printf '#!/usr/bin/env bash\n# boundary hook %s\nexit 0\n' "$v" > "$r/git-hooks/pre-push"
    chmod +x "$r/git-hooks/pre-push"
    git -C "$r" add -A
    git -C "$r" commit -qm "hook $v"
  done
  printf '%s\n' "$r"
}

# blob_at REPO REV -> the git-hooks/pre-push content at REV, on stdout.
blob_at() { git -C "$1" show "$2:git-hooks/pre-push"; }

dest_of() { # repo -> the resolved installed hook path
  printf '%s/pre-push\n' "$(cd "$1" && git rev-parse --path-format=absolute --git-path hooks)"
}

# ---------- 1. absent destination: a plain install ----------
r="$(mk absent)"; d="$(dest_of "$r")"
out="$("$r/scripts/install-git-hooks.sh" 2>&1)"; rc=$?
check_eq "$rc" 0 'absent: rc=0'
check_eq "$(sha_of "$d")" "$(sha_of "$r/git-hooks/pre-push")" 'absent: installed matches tracked'

# ---------- 2. already current: idempotent no-op ----------
out="$("$r/scripts/install-git-hooks.sh" 2>&1)"; rc=$?
check_eq "$rc" 0 'current: rc=0 on a repeat run'
check_eq "$(sha_of "$d")" "$(sha_of "$r/git-hooks/pre-push")" 'current: still matches'

# ---------- 3. differs, NO flags: refuses and changes nothing ----------
r="$(mk noflag)"; d="$(dest_of "$r")"
"$r/scripts/install-git-hooks.sh" >/dev/null 2>&1
blob_at "$r" HEAD~2 > "$d"          # make it one generation stale
before="$(sha_of "$d")"
out="$("$r/scripts/install-git-hooks.sh" 2>&1)"; rc=$?
check_eq "$rc" 1 'no flag: rc=1'
check_eq "$(sha_of "$d")" "$before" 'no flag: the installed copy is UNCHANGED'

# ---------- 4. differs, --force: overwrites ----------
out="$("$r/scripts/install-git-hooks.sh" --force 2>&1)"; rc=$?
check_eq "$rc" 0 '--force: rc=0'
check_eq "$(sha_of "$d")" "$(sha_of "$r/git-hooks/pre-push")" '--force: installed matches tracked'

# ---------- 5. ONE generation stale, --force-if-ours: licensed ----------
r="$(mk stale1)"; d="$(dest_of "$r")"
"$r/scripts/install-git-hooks.sh" >/dev/null 2>&1
blob_at "$r" HEAD~1 > "$d"
out="$("$r/scripts/install-git-hooks.sh" --force-if-ours 2>&1)"; rc=$?
check_eq "$rc" 0 'stale 1 gen: rc=0'
check_eq "$(sha_of "$d")" "$(sha_of "$r/git-hooks/pre-push")" 'stale 1 gen: installed now matches'
check_has "$out" 'replacing a prior tracked version' 'stale 1 gen: the replacement is reported'

# ---------- 6. TWO generations stale, --force-if-ours: still licensed ----------
# This is the row the rejected ORIG_HEAD predicate failed: it read a two-generation-stale copy
# as FOREIGN and refused to repair the exact state the change exists to repair.
r="$(mk stale2)"; d="$(dest_of "$r")"
"$r/scripts/install-git-hooks.sh" >/dev/null 2>&1
blob_at "$r" HEAD~2 > "$d"
out="$("$r/scripts/install-git-hooks.sh" --force-if-ours 2>&1)"; rc=$?
check_eq "$rc" 0 'stale 2 gen: rc=0'
check_eq "$(sha_of "$d")" "$(sha_of "$r/git-hooks/pre-push")" 'stale 2 gen: installed now matches'

# ---------- 7. FOREIGN file, --force-if-ours: refused ----------
# Rows 5/6 and 7 differ ONLY in the installed bytes and give opposite verdicts -- the control
# that proves this matrix reaches the license instead of some rule above it.
r="$(mk foreign)"; d="$(dest_of "$r")"
"$r/scripts/install-git-hooks.sh" >/dev/null 2>&1
printf '#!/usr/bin/env bash\n# planted by something else\nexit 0\n' > "$d"
before="$(sha_of "$d")"
out="$("$r/scripts/install-git-hooks.sh" --force-if-ours 2>&1)"; rc=$?
check_eq "$rc" 1 'foreign: rc=1'
check_eq "$(sha_of "$d")" "$before" 'foreign: the installed copy is UNCHANGED'
# "Predicate false ⇒ TODAY's refusal" is a claim about the MESSAGE too, and the differential
# harness cannot reach it (the pre-change installer rejects the flag at parse). A refusal worded
# specially for --force-if-ours would satisfy rc and digest while quietly breaking that claim.
check_has "$out" 'refusing to overwrite a hook that may have been installed by something else' \
  'foreign: the refusal is TODAY-identical, not a new force-if-ours wording'

# ---------- 8. SYMLINK to a historical blob: refused even though the license would pass ----------
# `git hash-object` follows a symlink, so the link's content hashes to a licensed historical
# blob. Only the symlink refusal ranking ABOVE the license stops this.
r="$(mk symlink)"; d="$(dest_of "$r")"
"$r/scripts/install-git-hooks.sh" >/dev/null 2>&1
blob_at "$r" HEAD~1 > "$r/decoy"
rm -f "$d"; ln -s "$r/decoy" "$d"
decoy_before="$(sha_of "$r/decoy")"
out="$("$r/scripts/install-git-hooks.sh" --force-if-ours 2>&1)"; rc=$?
check_eq "$rc" 1 'symlink: rc=1 even with the license satisfiable'
check_has "$out" 'is a symlink' 'symlink: the refusal names the symlink cause'
if [[ -L "$d" ]]; then pass_line 'symlink: destination is STILL a symlink'
else fail_line 'symlink: destination was replaced'; fi
# The assertion that actually pins "nothing was written THROUGH the link". Measured: with the
# top-level symlink guard deleted, `cp` follows the link and overwrites the TARGET's bytes while
# leaving the link a link -- so the `-L` check above still passes and the suite scores 21/21 on a
# build that has lost the property. Only the target's digest moves.
check_eq "$(sha_of "$r/decoy")" "$decoy_before" \
  'symlink: the TARGET bytes are unmodified (nothing written through the link)'

# ---------- 9. no committed history: refused at the HEAD-blob lookup ----------
# Titled for the branch that ACTUALLY fires. With git-hooks/pre-push present but never committed,
# `HEAD:git-hooks/pre-push` does not resolve, so the predicate returns at the head_blob guard --
# the enumeration is never even reached. Do not retitle this "floor": a row must name the thing
# whose deletion would turn it red, and deleting the (now absent) count test moves nothing.
#
# The membership floor proper -- HEAD's blob must appear in the enumeration -- has no isolating
# fixture, and that is a fact about the code rather than a gap in the suite: a resolvable
# HEAD:<path> puts its own commit in `rev-list HEAD -- <path>`, so the two conditions cannot be
# made to disagree from outside. It is asserted by construction, not by a row.
r="$tmproot/floor"; mkdir -p "$r/git-hooks" "$r/scripts"
git init -q -b dev "$r"
git -C "$r" config user.email t@t.invalid
git -C "$r" config user.name t
git -C "$r" config commit.gpgsign false
cp "$subject" "$r/scripts/install-git-hooks.sh"; chmod +x "$r/scripts/install-git-hooks.sh"
printf 'seed\n' > "$r/seed.txt"; git -C "$r" add seed.txt; git -C "$r" commit -qm seed
printf '#!/usr/bin/env bash\nexit 0\n' > "$r/git-hooks/pre-push"   # untracked on purpose
d="$(dest_of "$r")"
mkdir -p "$(dirname "$d")"
printf '#!/usr/bin/env bash\n# something else\nexit 0\n' > "$d"
before="$(sha_of "$d")"
out="$("$r/scripts/install-git-hooks.sh" --force-if-ours 2>&1)"; rc=$?
check_eq "$rc" 1 'floor: rc=1 when the path has no committed history'
check_eq "$(sha_of "$d")" "$before" 'floor: the installed copy is UNCHANGED'

printf '\nRESULT: %d passed, %d failed\n' "$pass" "$fail"
[[ "$fail" -eq 0 ]]

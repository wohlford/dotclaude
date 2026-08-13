#!/usr/bin/env bash
set -uo pipefail

# Script: test_pre_push_hook.sh
# Purpose: LIVENESS regression suite for git-hooks/pre-push and its audit-check counterpart,
#   `check_pre_push_installed` in skills/audit/audit.sh. A hook that is merely INSTALLED has
#   never run — every row below performs a REAL push (or `--dry-run`, which still runs the
#   hook) to a throwaway bare remote over a divorced main/dev fixture (orphan branches, so
#   `git merge-base dev main` reports none), and asserts the actual outcome, never the
#   hook's mere presence. The two MUST-FAIL rows are the mirror image: they prove git
#   silently does NOTHING with a mis-installed hook (measured: a non-executable hook is
#   ignored with only a `hint:` line, and a dangling symlink is ignored silently — both let
#   the push through), and that `check_pre_push_installed` is what actually catches it.
# Usage: ./scripts/tests/test_pre_push_hook.sh

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$here/../.." && pwd)"
hook_source="$repo_root/git-hooks/pre-push"
audit_sh="$repo_root/skills/audit/audit.sh"

pass=0
fail=0

pass_line() { printf 'PASS  %s\n' "$1"; pass=$((pass + 1)); }
fail_line() { # label detail
  printf 'FAIL  %s\n' "$1"
  if [[ -n "${2:-}" ]]; then
    printf '  --- detail ---\n%s\n  --------------\n' "$2"
  fi
  fail=$((fail + 1))
}

if [[ ! -f "$hook_source" ]]; then
  fail_line "git-hooks/pre-push exists at $hook_source"
  printf '\n%d passed, %d failed\n' "$pass" "$fail"
  exit 1
fi
if [[ ! -x "$hook_source" ]]; then
  fail_line "git-hooks/pre-push is executable in the working tree"
  printf '\n%d passed, %d failed\n' "$pass" "$fail"
  exit 1
fi
pass_line "git-hooks/pre-push exists and is executable in the working tree"

# Pinned with `pwd -P`: TMPDIR is a symlink on this machine (/tmp -> /private/tmp), and a
# fixture built under the logical path can send a path-resolving subject down another
# branch entirely — measured elsewhere in this repo's own suites. Nothing here writes
# inside this repo; audit.sh's own `check_hermetic` FAILs on exactly that.
sandbox="$(mktemp -d)"
sandbox="$(cd -P "$sandbox" && pwd)"
trap 'rm -rf "$sandbox"' EXIT

git_id() { # dir -> test identity, signing off
  git -C "$1" config user.email test@test.invalid
  git -C "$1" config user.name test
  git -C "$1" config commit.gpgsign false
}

install_hook() { # repo_dir [source]
  local repo_dir="$1" source="${2:-$hook_source}"
  mkdir -p "$repo_dir/.git/hooks"
  cp "$source" "$repo_dir/.git/hooks/pre-push"
  chmod +x "$repo_dir/.git/hooks/pre-push"
}

# Builds the divorced fixture most rows share: a bare remote plus a work repo whose `main`
# and `dev` are ORPHAN branches with no common ancestor (`git merge-base dev main` reports
# none) — the shape that makes the allowlist-over-blocklist row (a feature branch cut from
# dev) meaningful, and the shape a real adopted repo has today. Both `main` and `dev` carry
# the `.publication.toml` marker, matching this repo's own two real clones.
#
# object_format defaults to sha1 -- this repo's ambient `git init` default when no
# init.defaultObjectFormat is configured (confirmed on this machine) -- so every row that
# omits the second argument is byte-for-byte the fixture it always was. Pass "sha256" to
# build the null-OID-width rows: the zero object id is 40 hex digits under sha1 but 64
# under sha256, which is exactly the width the boundary hook's zero-sha match must not
# hardcode.
#
# Sets ORIGIN and WORK (globals, read by every row that follows a build_fixture call).
build_fixture() { # name [object_format]
  local name="$1"
  local object_format="${2:-sha1}"
  ORIGIN="$sandbox/${name}-origin.git"
  WORK="$sandbox/${name}-work"
  git init -q --bare --object-format="$object_format" "$ORIGIN"
  git init -q --object-format="$object_format" "$WORK"
  git_id "$WORK"
  git -C "$WORK" remote add origin "$ORIGIN"

  git -C "$WORK" checkout -q --orphan main
  printf 'adopted = true\n' > "$WORK/.publication.toml"
  printf 'm\n' > "$WORK/main.txt"
  git -C "$WORK" add -A
  git -C "$WORK" commit -q -m "main init"

  git -C "$WORK" checkout -q --orphan dev
  git -C "$WORK" rm -rf --cached -q . >/dev/null 2>&1 || true
  rm -f "$WORK/main.txt"
  printf 'adopted = true\n' > "$WORK/.publication.toml"
  printf 'd\n' > "$WORK/dev.txt"
  git -C "$WORK" add -A
  git -C "$WORK" commit -q -m "dev init, divorced from main"

  git -C "$WORK" checkout -q -b feature-off-dev dev
  printf 'f\n' > "$WORK/feature.txt"
  git -C "$WORK" add -A
  git -C "$WORK" commit -q -m "feature work off dev"

  git -C "$WORK" checkout -q --orphan unrelated
  git -C "$WORK" rm -rf --cached -q . >/dev/null 2>&1 || true
  rm -f "$WORK/dev.txt" "$WORK/feature.txt" "$WORK/.publication.toml"
  printf 'u\n' > "$WORK/unrelated.txt"
  git -C "$WORK" add -A
  git -C "$WORK" commit -q -m "unrelated init"

  git -C "$WORK" checkout -q main
  install_hook "$WORK"
}

remote_refs() { # bare_repo -> one refname per line
  git -C "$1" for-each-ref --format='%(refname)'
}

# ============================================================================
# ROW: dev refused
# ============================================================================
build_fixture row_dev
out="$(git -C "$WORK" push origin dev 2>&1)"; rc=$?
if [[ "$rc" -ne 0 && "$out" == *"refusing refs/heads/dev"* && "$out" == *"not reachable from refs/heads/main"* ]]; then
  pass_line "dev is refused"
else
  fail_line "dev is refused" "$out (rc=$rc)"
fi
if [[ -z "$(remote_refs "$ORIGIN")" ]]; then
  pass_line "a refused dev never transfers anything to the remote"
else
  fail_line "a refused dev never transfers anything to the remote" "$(remote_refs "$ORIGIN")"
fi

# ============================================================================
# ROW: a feature branch cut FROM dev is refused too — this is the row that justifies
# allowlist-over-blocklist. It is NOT an ancestor of dev, so a blocklist ("block if
# reachable from dev") would have let it through and published dev's whole ancestry.
# ============================================================================
build_fixture row_feature
out="$(git -C "$WORK" push origin feature-off-dev 2>&1)"; rc=$?
if [[ "$rc" -ne 0 && "$out" == *"refusing refs/heads/feature-off-dev"* ]]; then
  pass_line "a feature branch cut from dev is refused (allowlist, not blocklist)"
else
  fail_line "a feature branch cut from dev is refused (allowlist, not blocklist)" "$out (rc=$rc)"
fi

# ============================================================================
# ROW: an orphan/unrelated branch (no shared history with main at all) is refused
# ============================================================================
build_fixture row_unrelated
out="$(git -C "$WORK" push origin unrelated 2>&1)"; rc=$?
if [[ "$rc" -ne 0 && "$out" == *"refusing refs/heads/unrelated"* ]]; then
  pass_line "an orphan/unrelated branch is refused"
else
  fail_line "an orphan/unrelated branch is refused" "$out (rc=$rc)"
fi

# ============================================================================
# ROW: main is allowed. `--dry-run` still runs the hook and still transfers nothing, so it
# is used here for the plain-allow case to save the (cheap, but real) object transfer.
# ============================================================================
build_fixture row_main
out="$(git -C "$WORK" push --dry-run origin main 2>&1)"; rc=$?
if [[ "$rc" -eq 0 ]]; then
  pass_line "main is allowed (--dry-run)"
else
  fail_line "main is allowed (--dry-run)" "$out (rc=$rc)"
fi

# ============================================================================
# ROW: force-push of an ancestor of main is allowed — deliberateness of a force-push is
# push-guard's question, not this hook's; this hook only asks reachability.
# ============================================================================
build_fixture row_force
old_main_sha="$(git -C "$WORK" rev-parse main)"
git -C "$WORK" commit -q --allow-empty -m "advance main"
git -C "$WORK" push -q origin main
git -C "$WORK" branch -q -f old-main "$old_main_sha"
out="$(git -C "$WORK" push --force origin old-main:main 2>&1)"; rc=$?
if [[ "$rc" -eq 0 ]]; then
  pass_line "force-push of an ancestor of the current local main is allowed"
else
  fail_line "force-push of an ancestor of the current local main is allowed" "$out (rc=$rc)"
fi

# ============================================================================
# ROW: main --follow-tags is allowed, and the annotated tag actually lands. Deliberately
# NOT preceded by a separate plain push of main — a single `--follow-tags` push is what is
# asserted, so the row does not lean on this operator's global `push.followTags` config.
# An annotated tag's LOCAL sha on the wire is the TAG OBJECT, never the commit it points
# at, which is exactly the shape the hook's explicit `^{commit}` peel exists for.
# ============================================================================
build_fixture row_tag
git -C "$WORK" tag -a -m "v1" v1.0.0 main
out="$(git -C "$WORK" push origin main --follow-tags 2>&1)"; rc=$?
tag_landed="$(remote_refs "$ORIGIN" | grep -Fx 'refs/tags/v1.0.0' || true)"
if [[ "$rc" -eq 0 && -n "$tag_landed" ]]; then
  pass_line "main --follow-tags is allowed and the annotated tag lands"
else
  fail_line "main --follow-tags is allowed and the annotated tag lands" "$out (rc=$rc); remote tags: $(remote_refs "$ORIGIN" | grep '^refs/tags' || echo none)"
fi

# ============================================================================
# ROW: deletion is always allowed — it publishes nothing — even for a ref that a plain
# update of the SAME name would be refused for. `unrelated` is landed on the remote via
# --no-verify first (itself covered below), then deleted WITHOUT --no-verify to prove the
# deletion exemption fires on its own, not by riding the bypass.
# ============================================================================
build_fixture row_delete
git -C "$WORK" push -q --no-verify origin unrelated
out="$(git -C "$WORK" push origin :unrelated 2>&1)"; rc=$?
still_present="$(remote_refs "$ORIGIN" | grep -Fx 'refs/heads/unrelated' || true)"
if [[ "$rc" -eq 0 && -z "$still_present" ]]; then
  pass_line "deleting an otherwise-unreachable ref is allowed and the ref is gone"
else
  fail_line "deleting an otherwise-unreachable ref is allowed and the ref is gone" "$out (rc=$rc); still present: $still_present"
fi

# Assembled for the same reason as test_publication_push_guard.sh's VERB and
# test_guard_corpus.py's _VERB. NOTE: this is a convention for NEWLY authored rows only -- the
# rows above this point spell the verb out next to "git" some fifteen times, and are deliberately
# left alone rather than churned. Do not read this as a property of the file.
VERB="pu""sh"

# ============================================================================
# ROW: deletion is always allowed under sha256 too. The null OID's width is
# object-format-dependent -- 64 hex zeros here, not the 40 above -- and this is the exact
# row that was RED against the pre-fix hook: the fixed-width comparison stopped matching
# once the digest widened, so the deletion came back refused with "does not peel to a
# commit" instead of exempt.
# ============================================================================
build_fixture row_delete_sha256 sha256
git -C "$WORK" "$VERB" -q --no-verify origin unrelated
out="$(git -C "$WORK" "$VERB" origin :unrelated 2>&1)"; rc=$?
still_present="$(remote_refs "$ORIGIN" | grep -Fx 'refs/heads/unrelated' || true)"
if [[ "$rc" -eq 0 && -z "$still_present" ]]; then
  pass_line "sha256: deleting an otherwise-unreachable ref is allowed and the ref is gone"
else
  fail_line "sha256: deleting an otherwise-unreachable ref is allowed and the ref is gone" "$out (rc=$rc); still present: $still_present"
fi

# ============================================================================
# ROW: a non-deletion ref update in a sha256 repo that is NOT reachable from main is still
# refused -- proves the width-agnostic zero-sha match exempted nothing beyond the zero-OID
# branch itself. Same shape as the orphan/unrelated row above, run under sha256.
# ============================================================================
build_fixture row_unrelated_sha256 sha256
out="$(git -C "$WORK" "$VERB" origin unrelated 2>&1)"; rc=$?
if [[ "$rc" -ne 0 && "$out" == *"refusing refs/heads/unrelated"* ]]; then
  pass_line "sha256: an orphan/unrelated branch is still refused"
else
  fail_line "sha256: an orphan/unrelated branch is still refused" "$out (rc=$rc)"
fi

# ============================================================================
# ROW: --mirror is judged PER-REF, including a non-refs/heads/* namespace (refs/remotes/*).
# A synthetic refs/remotes/origin/phantom ref is pointed at dev's tip to prove the hook
# judges by SHA reachability, not by ref namespace, exactly as --mirror itself would send
# it. The whole push is refused as a unit (pre-push runs before any transfer, so one
# refused ref aborts everything) — what this row proves is that BOTH refused refs are
# individually named, not a blanket refusal.
# ============================================================================
build_fixture row_mirror
dev_sha="$(git -C "$WORK" rev-parse dev)"
git -C "$WORK" update-ref refs/remotes/origin/phantom "$dev_sha"
mirror_target="$sandbox/row_mirror-target.git"
git init -q --bare "$mirror_target"
git -C "$WORK" remote add mirror-target "$mirror_target"
out="$(git -C "$WORK" push --mirror mirror-target 2>&1)"; rc=$?
if [[ "$rc" -ne 0 && "$out" == *"refusing refs/heads/dev"* && "$out" == *"refusing refs/remotes/origin/phantom"* ]]; then
  pass_line "--mirror judges refs/heads/dev and refs/remotes/origin/phantom independently"
else
  fail_line "--mirror judges refs/heads/dev and refs/remotes/origin/phantom independently" "$out (rc=$rc)"
fi

# ============================================================================
# ROW: --no-verify bypasses this hook entirely — the residual is PINNED here, not assumed.
# ============================================================================
build_fixture row_noverify
out="$(git -C "$WORK" push --no-verify origin dev 2>&1)"; rc=$?
landed="$(remote_refs "$ORIGIN" | grep -Fx 'refs/heads/dev' || true)"
if [[ "$rc" -eq 0 && -n "$landed" ]]; then
  pass_line "--no-verify bypasses the hook and dev actually lands"
else
  fail_line "--no-verify bypasses the hook and dev actually lands" "$out (rc=$rc)"
fi

# ============================================================================
# BONUS: dormant repo (no branch anywhere carries the marker) — the hook does nothing, so
# even a divorced dev is allowed through. Not one of the plan's named rows, but the other
# half of the contract this suite exists to pin. The marker must be dropped, committed, on
# EVERY branch that inherited it (main, dev, AND feature-off-dev, which branched from dev
# while it still had the file) — dormancy is a for-each-ref scan, so one surviving branch
# keeps the repo armed.
# ============================================================================
build_fixture row_dormant
for b in main dev feature-off-dev; do
  git -C "$WORK" checkout -q "$b"
  git -C "$WORK" rm -q .publication.toml
  git -C "$WORK" commit -q -m "$b: drop the marker"
done
git -C "$WORK" checkout -q main
out="$(git -C "$WORK" push origin dev 2>&1)"; rc=$?
if [[ "$rc" -eq 0 ]]; then
  pass_line "a dormant repo (no branch carries the marker) lets a divorced dev through"
else
  fail_line "a dormant repo (no branch carries the marker) lets a divorced dev through" "$out (rc=$rc)"
fi

# ============================================================================
# BONUS: armed but refs/heads/main unresolvable -> block everything. An unevaluable
# allowlist must refuse, never fall open.
# ============================================================================
ORIGIN="$sandbox/row_nomain-origin.git"
WORK="$sandbox/row_nomain-work"
git init -q --bare "$ORIGIN"
git init -q "$WORK"
git_id "$WORK"
git -C "$WORK" remote add origin "$ORIGIN"
git -C "$WORK" checkout -q --orphan onlybranch
printf 'adopted = true\n' > "$WORK/.publication.toml"
git -C "$WORK" add -A
git -C "$WORK" commit -q -m "armed, no main branch at all"
install_hook "$WORK"
out="$(git -C "$WORK" push origin onlybranch 2>&1)"; rc=$?
if [[ "$rc" -ne 0 && "$out" == *"refs/heads/main does not resolve"* ]]; then
  pass_line "armed but refs/heads/main unresolvable blocks everything"
else
  fail_line "armed but refs/heads/main unresolvable blocks everything" "$out (rc=$rc)"
fi

# ============================================================================
# MUST-FAIL rows (task E): a non-executable hook and a dangling symlink must both be caught
# by check_pre_push_installed. These do NOT test the hook's own liveness — the whole point
# is that git silently runs neither of them, so the git-level push would (wrongly) succeed.
# What must catch it is the audit check, so these rows drive skills/audit/audit.sh directly
# against a throwaway --scope, never against this repo, and never with --tests (that flag
# alone is what would execute scripts/tests/*, out of scope for this file).
# ============================================================================
audit_pre_push_verdict() { # scope -> the "<VERDICT> pre-push-installed ..." line, or ""
  "$audit_sh" --scope "$1" 2>/dev/null | grep '^\(PASS\|FAIL\|SKIP\) pre-push-installed'
}

build_pre_push_installed_fixture() { # name -> WORK is armed, main resolvable, source present
  build_fixture "$1"
  mkdir -p "$WORK/git-hooks"
  cp "$hook_source" "$WORK/git-hooks/pre-push"
  chmod +x "$WORK/git-hooks/pre-push"
  git -C "$WORK" add -A
  git -C "$WORK" commit -q -m "carry the tracked pre-push source in the worktree"
}

if [[ -x "$audit_sh" ]]; then
  # Control: a correctly-installed hook -> PASS. Establishes the contrast the two MUST-FAIL
  # rows below depend on; without this row a FAIL-everywhere audit check would look
  # identical to a working one.
  build_pre_push_installed_fixture row_audit_pass
  verdict="$(audit_pre_push_verdict "$WORK")"
  if [[ "$verdict" == PASS* ]]; then
    pass_line "a correctly-installed hook -> PASS pre-push-installed"
  else
    fail_line "a correctly-installed hook -> PASS pre-push-installed" "$verdict"
  fi

  # MUST-FAIL: non-executable hook.
  build_pre_push_installed_fixture row_audit_noexec
  chmod -x "$WORK/.git/hooks/pre-push"
  verdict="$(audit_pre_push_verdict "$WORK")"
  if [[ "$verdict" == FAIL* ]]; then
    pass_line "MUST-FAIL: a non-executable installed hook -> FAIL pre-push-installed"
  else
    fail_line "MUST-FAIL: a non-executable installed hook -> FAIL pre-push-installed" "$verdict"
  fi

  # MUST-FAIL: dangling symlink at the destination.
  build_pre_push_installed_fixture row_audit_symlink
  rm -f "$WORK/.git/hooks/pre-push"
  ln -s /nonexistent/path/to/nowhere "$WORK/.git/hooks/pre-push"
  verdict="$(audit_pre_push_verdict "$WORK")"
  if [[ "$verdict" == FAIL* ]]; then
    pass_line "MUST-FAIL: a dangling symlink at the hook destination -> FAIL pre-push-installed"
  else
    fail_line "MUST-FAIL: a dangling symlink at the hook destination -> FAIL pre-push-installed" "$verdict"
  fi

  # Confirms the MUST-FAIL rows above are measuring the audit CHECK, not a fluke of the
  # git-level push: git itself still lets a push through in both broken states (this is
  # the exact silent fail-open scripts/install-git-hooks.sh's "never symlink" rule exists
  # to rule out).
  build_pre_push_installed_fixture row_git_noexec
  chmod -x "$WORK/.git/hooks/pre-push"
  out="$(git -C "$WORK" push origin dev 2>&1)"; rc=$?
  if [[ "$rc" -eq 0 ]]; then
    pass_line "measured: a non-executable hook is silently ignored by git -- dev pushes through"
  else
    fail_line "measured: a non-executable hook is silently ignored by git -- dev pushes through" "$out (rc=$rc)"
  fi

  build_pre_push_installed_fixture row_git_symlink
  rm -f "$WORK/.git/hooks/pre-push"
  ln -s /nonexistent/path/to/nowhere "$WORK/.git/hooks/pre-push"
  out="$(git -C "$WORK" push origin dev 2>&1)"; rc=$?
  if [[ "$rc" -eq 0 ]]; then
    pass_line "measured: a dangling symlink hook is silently ignored by git -- dev pushes through"
  else
    fail_line "measured: a dangling symlink hook is silently ignored by git -- dev pushes through" "$out (rc=$rc)"
  fi
else
  fail_line "skills/audit/audit.sh is present and executable (MUST-FAIL rows need it)"
fi

# ============================================================================
# ROW: the zero-sha exemption match in the hook ($hook_source) must be ANCHORED
# across the whole value (^0+$), not a bare substring check. That claim is about
# ALL inputs, and the "still refused" rows above only catch an unanchored match
# when their randomly-generated commit sha happens to contain a "0" digit --
# measured, 1 of 3 sample shas had none at all, so the catch up there is real
# but not guaranteed. This row drives the shipped hook directly on its own
# stdin interface (never a hand-copied regex) with a table of adversarial
# local-sha values, none of them a real object, and asserts the anchor holds
# for every one of them except the two literal all-zero widths.
# ============================================================================
repeat_char() { # char count -> prints count copies of char, no trailing newline
  local char="$1" count="$2" out="" i
  for ((i = 0; i < count; i++)); do
    out+="$char"
  done
  printf '%s' "$out"
}

drive_hook_on_sha() { # local_sha -> the hook's own exit status, one synthetic ref line on stdin
  local sha="$1"
  (cd "$WORK" && printf 'refs/heads/probe %s refs/heads/probe %s\n' "$sha" "$sha" | "$hook_source" >/dev/null 2>&1)
}

build_fixture row_zero_sha_boundary
boundary_total=0
boundary_failures=()
check_boundary_sha() { # local_sha expected(EXEMPT|REFUSED) label
  local sha="$1" expected="$2" label="$3" got
  boundary_total=$((boundary_total + 1))
  if drive_hook_on_sha "$sha"; then got="EXEMPT"; else got="REFUSED"; fi
  if [[ "$got" != "$expected" ]]; then
    boundary_failures+=("$label: expected $expected, got $got")
  fi
}

check_boundary_sha "$(repeat_char 0 40)"  EXEMPT  "40 zeros"
check_boundary_sha "$(repeat_char 0 64)"  EXEMPT  "64 zeros"
check_boundary_sha "$(repeat_char 0 39)a" REFUSED "39 zeros followed by a"
check_boundary_sha "a$(repeat_char 0 39)" REFUSED "a followed by 39 zeros"
check_boundary_sha "$(repeat_char 0 63)1" REFUSED "63 zeros followed by 1"
check_boundary_sha "$(repeat_char f 40)"  REFUSED "40 f characters"

if [[ ${#boundary_failures[@]} -eq 0 ]]; then
  pass_line "zero-sha exemption is ANCHORED across all $boundary_total adversarial local-sha values"
else
  fail_line "zero-sha exemption is ANCHORED across all $boundary_total adversarial local-sha values" "$(printf '%s\n' "${boundary_failures[@]}")"
fi

printf '\n%d passed, %d failed\n' "$pass" "$fail"
[[ "$fail" -eq 0 ]]

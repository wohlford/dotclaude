#!/usr/bin/env bash
set -uo pipefail

# Script: test_exec_bit_guard.sh
# Purpose: Regression tests for exec-bit-guard.sh — a commit adding a 644 shebang file or a 755→644
#          downgrade is blocked; legacy-644 edits, fileMode=false, overrides, and fail-safe paths pass.
# Usage:   bash scripts/tests/test_exec_bit_guard.sh

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
guard="$here/../exec-bit-guard.sh"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

pass=0
fail=0

# NOTE: the guard is invoked BARE-PATH ("$guard", not bash "$guard") on purpose — the suite
# thereby also verifies the exec bit, the very defect class this feature exists to prevent.
run() { # command cwd -> prints exit code
  local got=0
  printf '%s' "$(python3 -c 'import json,sys;print(json.dumps({"tool_input":{"command":sys.argv[1]},"cwd":sys.argv[2]}))' "$1" "$2")" \
    | "$guard" >/dev/null 2>&1 || got=$?
  printf '%s' "$got"
}
assert() { # command cwd want label
  local got; got="$(run "$1" "$2")"
  if [[ "$got" -eq "$3" ]]; then
    printf 'PASS  %s (exit %d)\n' "$4" "$got"; pass=$((pass + 1))
  else
    printf 'FAIL  %s (want %d, got %d)\n' "$4" "$3" "$got"; fail=$((fail + 1))
  fi
}
mkrepo() { # dir — init with identity, signing off, and a seed commit
  git init -q "$1"
  git -C "$1" config user.email test@test.invalid
  git -C "$1" config user.name test
  git -C "$1" config commit.gpgsign false
  git -C "$1" commit -q --allow-empty -m seed
}

# --- r1: new shebang file staged 644 ---
mkrepo "$tmp/r1"
printf '#!/bin/sh\necho hi\n' > "$tmp/r1/hook.sh"
git -C "$tmp/r1" add hook.sh
assert 'git commit -m x' "$tmp/r1" 2 'new 644 shebang file staged -> blocked'
assert 'git add -A && git commit -m x' "$tmp/r1" 2 'compound segment commit -> blocked'
assert 'ALLOW_NONEXEC=1 git commit -m x' "$tmp/r1" 0 'ALLOW_NONEXEC=1 override -> allowed'
assert 'ALLOW_NONEXEC=1 git status && git commit -m x' "$tmp/r1" 2 'override on WRONG segment -> still blocked'
assert 'git status' "$tmp/r1" 0 'non-commit git command -> pass'
assert 'git log --grep=commit' "$tmp/r1" 2 'tolerated over-block: commit word + staged offender'
assert 'git commit -m "use -C foo here"' "$tmp/r1" 2 'quoted -C in the message must not hijack repo resolution'

# --- r2: new files that must pass ---
mkrepo "$tmp/r2"
printf 'plain text, no shebang\n' > "$tmp/r2/notes.txt"
printf '#!/bin/sh\necho ok\n' > "$tmp/r2/good.sh"
chmod +x "$tmp/r2/good.sh"
git -C "$tmp/r2" add notes.txt good.sh
assert 'git commit -m x' "$tmp/r2" 0 'new 644 plain + new 755 shebang -> pass'

# --- r3: staged mode downgrade 755 -> 644 ---
mkrepo "$tmp/r3"
printf '#!/bin/sh\necho v\n' > "$tmp/r3/tool.sh"
chmod +x "$tmp/r3/tool.sh"
git -C "$tmp/r3" add tool.sh && git -C "$tmp/r3" commit -qm one
chmod -x "$tmp/r3/tool.sh"
git -C "$tmp/r3" add tool.sh
assert 'git commit -m x' "$tmp/r3" 2 'staged 755->644 downgrade -> blocked'

# --- r4: edit of a pre-existing 644 shebang file (legacy) passes ---
mkrepo "$tmp/r4"
printf '#!/bin/sh\nold\n' > "$tmp/r4/legacy.sh"
git -C "$tmp/r4" add legacy.sh && git -C "$tmp/r4" commit -qm one   # committed 644 on purpose
printf '#!/bin/sh\nnew content\n' > "$tmp/r4/legacy.sh"
git -C "$tmp/r4" add legacy.sh
assert 'git commit -m x' "$tmp/r4" 0 'edit of pre-existing 644 shebang -> pass (scope decision)'

# --- r5: -a staging of a worktree mode-loss ---
mkrepo "$tmp/r5"
printf '#!/bin/sh\necho v\n' > "$tmp/r5/tool.sh"
chmod +x "$tmp/r5/tool.sh"
git -C "$tmp/r5" add tool.sh && git -C "$tmp/r5" commit -qm one
chmod -x "$tmp/r5/tool.sh"   # NOT git-added
assert 'git commit -am x' "$tmp/r5" 2 'commit -am with worktree mode-loss -> blocked'
assert 'git commit -a -m x' "$tmp/r5" 2 'commit -a -m with worktree mode-loss -> blocked'
assert 'git commit --all -m x' "$tmp/r5" 2 'commit --all with worktree mode-loss -> blocked'
assert 'git commit -m x' "$tmp/r5" 0 'plain commit ignores unstaged mode-loss -> pass'
assert 'git commit --amend --no-edit' "$tmp/r5" 0 '--amend alone must NOT trigger the worktree scan'
assert 'git commit -m "refactor -a mode"' "$tmp/r5" 0 'quoted -a in the message must NOT trigger the worktree scan'
assert 'git commit -m "the -ab option"' "$tmp/r5" 0 'quoted short-cluster in the message must NOT trigger the scan'

# --- r6: core.fileMode=false -> always pass (spike: even +x files stage as 644 there) ---
mkrepo "$tmp/r6"
git -C "$tmp/r6" config core.fileMode false
printf '#!/bin/sh\necho x\n' > "$tmp/r6/exec.sh"
chmod +x "$tmp/r6/exec.sh"
git -C "$tmp/r6" add exec.sh
assert 'git commit -m x' "$tmp/r6" 0 'fileMode=false repo -> pass'

# --- r7: unborn HEAD (first commit of a fresh repo) ---
git init -q "$tmp/r7"
git -C "$tmp/r7" config user.email test@test.invalid
git -C "$tmp/r7" config user.name test
git -C "$tmp/r7" config commit.gpgsign false
printf '#!/bin/sh\nfirst\n' > "$tmp/r7/first.sh"
git -C "$tmp/r7" add first.sh
assert 'git commit -m x' "$tmp/r7" 2 'initial commit in fresh repo (unborn HEAD) -> blocked'

# --- repo resolution ---
assert "cd $tmp/r1 && git commit -m x" "$tmp" 2 'leading cd <repo> && commit -> resolved and blocked'
assert "git -C $tmp/r1 commit -m x" "$tmp" 2 'git -C <repo> commit -> resolved and blocked'
assert 'git commit -m x' "$tmp" 0 'cwd outside any repo -> fail open'

# --- fail-safe (exit 0) ---
failsafe() { # raw-stdin label
  local got=0
  printf '%s' "$1" | "$guard" >/dev/null 2>&1 || got=$?
  if [[ "$got" -eq 0 ]]; then
    printf 'PASS  %s -> 0\n' "$2"; pass=$((pass + 1))
  else
    printf 'FAIL  %s (got %d)\n' "$2" "$got"; fail=$((fail + 1))
  fi
}
failsafe 'not-json' 'garbage stdin'
failsafe '{"tool_input":{}}' 'JSON without .command'
failsafe '{"tool_input":{"command":"echo commit"}}' 'non-git command containing the word commit'

# --- a block must emit an actionable stderr message ---
msg="$(printf '%s' "$(python3 -c 'import json,sys;print(json.dumps({"tool_input":{"command":"git commit -m x"},"cwd":sys.argv[1]}))' "$tmp/r1")" | "$guard" 2>&1 1>/dev/null)" || true
case "$msg" in
  *exec-bit-guard*chmod\ +x*ALLOW_NONEXEC=1*) printf 'PASS  block emits an actionable stderr message\n'; pass=$((pass + 1)) ;;
  *) printf 'FAIL  block stderr message missing/unhelpful: %s\n' "$msg"; fail=$((fail + 1)) ;;
esac

printf '\n%d passed, %d failed\n' "$pass" "$fail"
[[ "$fail" -eq 0 ]]

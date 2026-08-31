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
  git -C "$1" config tag.gpgsign false
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
git -C "$tmp/r7" config tag.gpgsign false
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

# ---------- large-payload rows: the scan must stay correct AND affordable ----------
# Measured 2026-08-26. The loop makes a segment of every LINE (`read -r`), and there is no `break`
# after `found=1`, so every line matching both `git` and `commit` falls through into allow_re, two
# seds and aflag_re -- about five forks per line. Prose *about* git commits is therefore the
# expensive shape, and it is not contrived: it is what a plan or spec document looks like.
#
# The ceiling bounds a CLASS, not a budget. The fixed guard measures ~0.4-0.8 s on these fixtures;
# 20 s is ~25x headroom and still catches the 62 s regression these rows exist to pin. A tight
# ceiling here would be a flaky gate on every commit, which is worse than the defect.
EXEC_BIT_SCAN_CEILING_S=20
# 5000 lines x ~52 bytes ~= 260KB, comfortably past the ~128KB size measured DETERMINISTIC (40/40).
# 2000 lines (99KB) sits in the probabilistic band above macOS's 64KB pipe capacity but below that,
# where a pre-fix run can fail with the WRONG shape -- a ceiling breach instead of a wrong exit --
# which would break the attribution the RED gate rests on. The boundary is a pipe-buffer artifact,
# so headroom is the only defence; do not tune this down.
BIG_LINES=5000

# Without `timeout` the bounded rows below would run unbounded and their FAIL could never fire --
# the rows would still report, but about a different question. Fail loudly rather than quietly
# downgrade to an unbounded assertion.
have_timeout=1
command -v timeout >/dev/null 2>&1 || {
  have_timeout=0
  printf 'FAIL  bounded rows need GNU timeout, which is not on PATH\n'; fail=$((fail + 1)); }

big_cmd() { # nlines outfile -- every padding line carries BOTH tokens, the expensive shape
  local n="$1" out="$2" i
  {
    printf 'git commit -m msg\n'
    for ((i = 0; i < n; i++)); do
      printf '  see the git commit --amend convention note on line %d\n' "$i"
    done
  } > "$out"
}

# The padding line carries a DASH as well as both tokens, deliberately. A dash-free line lets any
# flag-parsing shortcut skip the expensive work, so a dash-free fixture would under-state the cost
# and could be satisfied by an optimisation that does not survive real technical prose. Measured:
# the dash shape is what defeated a dash-guard half-measure, at 81 s where the dash-free shape
# reached 0.6 s.

run_file() { # cmdfile cwd -> exit code, or 124 when it exceeded the ceiling
  local got=0
  python3 -c 'import json,sys;print(json.dumps({"tool_input":{"command":open(sys.argv[1]).read()},"cwd":sys.argv[2]}))' "$1" "$2" \
    | timeout "$EXEC_BIT_SCAN_CEILING_S" "$guard" >/dev/null 2>&1 || got=$?
  printf '%s' "$got"
}

assert_file() { # cmdfile cwd want label
  # Skip rather than run a missing command: without timeout each row would exit 127 and Step 2's
  # "exactly these FAIL labels" check would read three extra failures as unattributable REDs.
  if [[ "$have_timeout" != 1 ]]; then
    printf 'FAIL  %s (skipped: no timeout on PATH)\n' "$4"; fail=$((fail + 1)); return
  fi
  local got; got="$(run_file "$1" "$2")"
  if [[ "$got" -eq "$3" ]]; then
    printf 'PASS  %s (exit %d)\n' "$4" "$got"; pass=$((pass + 1))
  elif [[ "$got" -eq 124 ]]; then
    printf 'FAIL  %s (exceeded the %ss ceiling -- the per-segment scan is forking per line)\n' \
      "$4" "$EXEC_BIT_SCAN_CEILING_S"; fail=$((fail + 1))
  else
    printf 'FAIL  %s (want %d, got %d)\n' "$4" "$3" "$got"; fail=$((fail + 1))
  fi
}

# --- rL1: COST. Below the pipe-buffer threshold the guard is correct today but takes 62 s. ---
mkrepo "$tmp/rL1"
printf '#!/bin/sh\necho hi\n' > "$tmp/rL1/hook.sh"
git -C "$tmp/rL1" add hook.sh
big_cmd 500 "$tmp/rL1.cmd"   # deliberately BELOW the threshold: correct today, but 62s
assert_file "$tmp/rL1.cmd" "$tmp/rL1" 2 'a 24KB command about git commits still blocks, within the ceiling'

# --- rL2: CORRECTNESS. Above the threshold the pre-filter SIGPIPEs and the gate allows. ---
mkrepo "$tmp/rL2"
printf '#!/bin/sh\necho hi\n' > "$tmp/rL2/hook.sh"
git -C "$tmp/rL2" add hook.sh
big_cmd "$BIG_LINES" "$tmp/rL2.cmd"
assert_file "$tmp/rL2.cmd" "$tmp/rL2" 2 'a >=256KB command still reaches the scan rather than being skipped'

# --- rL3: the override must be honoured at SIZE, on the honest path. Today this passes only
# because the pre-filter bails before allow_re is ever consulted, so it is not evidence yet. ---
#
# The padding here must NOT be candidate segments. An earlier draft reused rL2's payload, whose
# every line matches both git and commit without an override -- so each one sets found=1 and the
# FIXED guard correctly returns 2, not 0. The suite already pins that semantics ("override on the
# WRONG segment -> still blocked"), so the draft row asserted a verdict the fix does not produce
# and would have detonated after the RED gate rather than at it. Only the first segment is a
# candidate here, and it carries the override.
mkrepo "$tmp/rL3"
printf '#!/bin/sh\necho hi\n' > "$tmp/rL3/hook.sh"
git -C "$tmp/rL3" add hook.sh
{
  printf 'ALLOW_NONEXEC=1 git commit -m msg\n'
  for ((i = 0; i < BIG_LINES; i++)); do
    printf '  ordinary prose padding with no tokens on line %d\n' "$i"
  done
} > "$tmp/rL3.cmd"
assert_file "$tmp/rL3.cmd" "$tmp/rL3" 0 'a >=256KB command with ALLOW_NONEXEC=1 is still allowed'

# --- rL5: the leading `.*` in the -C extraction is load-bearing and nothing else pins it. With it,
# `git -C /a commit -C /b` yields /b (sed's last-match semantics); without it, /a. A tidier deleting
# the "redundant" .* ships green through every other row. Repo A is clean, repo B holds the
# offender, so the verdict moves 0 <-> 2 on which one is resolved. ---
mkrepo "$tmp/rL5a"
mkrepo "$tmp/rL5b"
printf '#!/bin/sh\necho hi\n' > "$tmp/rL5b/hook.sh"
git -C "$tmp/rL5b" add hook.sh
assert "git -C $tmp/rL5a commit -C $tmp/rL5b -m x" "$tmp/rL5a" 2 \
  'the LAST -C wins, so the offender in the second repo is still found'

printf '\n%d passed, %d failed\n' "$pass" "$fail"
[[ "$fail" -eq 0 ]]

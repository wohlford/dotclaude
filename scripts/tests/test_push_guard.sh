#!/usr/bin/env bash
set -uo pipefail

# Script: test_push_guard.sh
# Purpose: Regression tests for push-guard.sh — a push segment is blocked unless it ITSELF leads with
#          ALLOW_PUSH=1; detection is a broad git-word+push-word match; non-push and fail-safe paths pass.
# Usage:   bash scripts/tests/test_push_guard.sh

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
guard="$here/../push-guard.sh"

pass=0
fail=0
run() { # command -> prints exit code, given a JSON {tool_input:{command}}
  local got=0
  printf '%s' "$(python3 -c 'import json,sys;print(json.dumps({"tool_input":{"command":sys.argv[1]}}))' "$1")" \
    | bash "$guard" >/dev/null 2>&1 || got=$?
  printf '%s' "$got"
}
assert() { # cmd want label
  local got; got="$(run "$1")"
  if [[ "$got" -eq "$2" ]]; then
    printf 'PASS  %s (exit %d)\n' "$3" "$got"; pass=$((pass + 1))
  else
    printf 'FAIL  %s (want %d, got %d)\n' "$3" "$2" "$got"; fail=$((fail + 1))
  fi
}

# --- blocked (exit 2): an unauthorized push segment ---
assert 'git push' 2 'bare git push'
assert 'git push origin main --follow-tags' 2 'git push with args'
assert 'FOO=1 git push' 2 'env-prefixed push, no ALLOW'
assert 'git -C /some/repo push' 2 'git -C <repo> push'
assert 'git -C "/repo with spaces" push' 2 'quoted -C with spaces (regex-evasion) still blocked'
assert 'git add -A && git push' 2 'push in a compound segment'
assert 'ALLOW_PUSH=1 git add -A && git push' 2 'override on the WRONG segment -> push still blocked'
assert 'ALLOW_PUSH=1 git fetch && git push' 2 'override scoped to fetch -> push blocked'
assert 'git push; ALLOW_PUSH=1 true' 2 'override after the push -> blocked'
assert 'git status; git push' 2 'semicolon-separated push'
assert 'git subtree push origin main' 2 'git subtree push (broad match)'
assert 'git commit -m "git push docs"' 2 'tolerated false-block: push word in a commit message'

# --- allowed (exit 0): the push segment itself leads with ALLOW_PUSH=1 ---
assert 'ALLOW_PUSH=1 git push' 0 'ALLOW_PUSH=1 git push'
assert 'ALLOW_PUSH=1 git push origin main --follow-tags' 0 'ALLOW_PUSH=1 push with args'
assert 'ALLOW_PUSH=1 git -C /some/repo push' 0 'ALLOW_PUSH=1 git -C push'
assert 'FOO=1 ALLOW_PUSH=1 git push' 0 'tolerates a preceding assignment before ALLOW_PUSH'
assert 'git add -A && ALLOW_PUSH=1 git push' 0 'override leads the push segment in a compound'

# --- non-push git and non-git: pass (exit 0) ---
assert 'git fetch origin' 0 'git fetch'
assert 'git pull' 0 'git pull'
assert 'git commit -m x' 0 'git commit'
assert 'git status' 0 'git status'
assert 'ls -la' 0 'non-git command'

# --- fail-safe (exit 0) ---
failsafe() { # raw-stdin label
  local got=0
  printf '%s' "$1" | bash "$guard" >/dev/null 2>&1 || got=$?
  if [[ "$got" -eq 0 ]]; then
    printf 'PASS  %s -> 0\n' "$2"; pass=$((pass + 1))
  else
    printf 'FAIL  %s (got %d)\n' "$2" "$got"; fail=$((fail + 1))
  fi
}
failsafe 'not-json' 'garbage stdin'
failsafe '{"tool_input":{}}' 'JSON without .command'
failsafe '{"tool_input":{"command":""}}' 'empty command'

# --- a blocked push must emit a stderr message (Claude needs it to self-correct) ---
msg="$(printf '%s' "$(python3 -c 'import json;print(json.dumps({"tool_input":{"command":"git push"}}))')" | bash "$guard" 2>&1 1>/dev/null)"
case "$msg" in
  *push-guard*ALLOW_PUSH=1*) printf 'PASS  block emits an actionable stderr message\n'; pass=$((pass + 1)) ;;
  *) printf 'FAIL  block stderr message missing/unhelpful: %s\n' "$msg"; fail=$((fail + 1)) ;;
esac

printf '\n%d passed, %d failed\n' "$pass" "$fail"
[[ "$fail" -eq 0 ]]

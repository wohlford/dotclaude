#!/usr/bin/env bash
set -uo pipefail

# Script: test_push_guard.sh
# Purpose: Regression tests for push-guard.py — a push segment is blocked unless it ITSELF leads
#          with ALLOW_PUSH=1; detection is a git-command-position SUBCOMMAND match (`push`, or
#          `subtree` with `push` among its args), not a raw git-word+push-word text match;
#          non-push, wrapper/auth-asymmetry, newline, and fail-open paths all pass.
# Usage:   bash scripts/tests/test_push_guard.sh

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
guard="$here/../push-guard.py"

pass=0
fail=0
run() { # command -> prints exit code, given a JSON {tool_input:{command}}
  local got=0
  printf '%s' "$(python3 -c 'import json,sys;print(json.dumps({"tool_input":{"command":sys.argv[1]}}))' "$1")" \
    | python3 "$guard" >/dev/null 2>&1 || got=$?
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
assert 'git -C "/repo with spaces" push' 2 'quoted -C with spaces still blocked'
assert 'git add -A && git push' 2 'push in a compound segment'
assert 'ALLOW_PUSH=1 git add -A && git push' 2 'override on the WRONG segment -> push still blocked'
assert 'ALLOW_PUSH=1 git fetch && git push' 2 'override scoped to fetch -> push blocked'
assert 'git push; ALLOW_PUSH=1 true' 2 'override after the push -> blocked'
assert 'git status; git push' 2 'semicolon-separated push'
assert 'git subtree push origin main' 2 'git subtree push (subcommand+arg match)'

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
  printf '%s' "$1" | python3 "$guard" >/dev/null 2>&1 || got=$?
  if [[ "$got" -eq 0 ]]; then
    printf 'PASS  %s -> 0\n' "$2"; pass=$((pass + 1))
  else
    printf 'FAIL  %s (got %d)\n' "$2" "$got"; fail=$((fail + 1))
  fi
}
failsafe 'not-json' 'garbage stdin'
failsafe '{"tool_input":{}}' 'JSON without .command'
failsafe '{"tool_input":{"command":""}}' 'empty command'

# --- newline handling (segment boundary; auth must not leak across a newline) ---
assert $'git add -A\ngit push' 2 'newline-joined push is a segment boundary -> blocked'
assert $'ALLOW_PUSH=1 git status\ngit push' 2 'ALLOW_PUSH=1 on one newline-segment does not authorize the next'

# --- INVERT (was blocked under the old raw-word match; the tokenizer sees no `push` subcommand) ---
assert 'git commit -m "git push docs"' 0 'push word inside a commit message is not a push subcommand'

# --- new false-positive regressions the old raw-word match tripped on ---
assert 'git add scripts/publication-push-guard.py' 0 'push word inside a pathspec is not a push subcommand'
assert 'git commit -m "convert the guard to python"' 0 'no push word at all, plain commit'
assert 'git tag -a v1.0.0 -m "push guard tokenizer"' 0 'push word inside a tag message is not a push subcommand'
assert 'git commit -m "git push origin main"' 0 'quote-awareness: a full push invocation quoted as a message is not a subcommand'

# --- wrapper / auth asymmetry (all three corners) ---
assert 'sudo git push' 2 'bare wrapper: still detected as a push'
assert 'ALLOW_PUSH=1 sudo git push' 0 'ALLOW_PUSH=1 ahead of a bare wrapper authorizes it'
assert 'sudo ALLOW_PUSH=1 git push' 2 'wrapper before ALLOW_PUSH=1 breaks the env run (load-bearing)'
assert 'env ALLOW_PUSH=1 git push' 2 'env(1) is itself a wrapper, not an assignment -> breaks the run'

# --- detection internals ---
assert 'git -c foo=bar push' 2 'a -c global option is skipped to reach the push subcommand'
assert 'git --git-dir=/x push' 2 'a --git-dir= global option is skipped to reach the push subcommand'
assert 'ALLOW_PUSH=1 git subtree push origin main' 0 'authorized git subtree push'
assert 'git subtree pull origin main' 0 'git subtree pull: "push" not among its args -> not a push op'
assert 'ALLOW_PUSH=12 git push' 2 'ALLOW_PUSH=12 is not the exact token ALLOW_PUSH=1'
assert 'git push "oops' 0 'unbalanced quote -> tokenizer ValueError -> fail open'

# --- CONCEDED RESIDUAL: an opaque string hides `push` from the tokenizer entirely ---
assert "bash -c 'git push'" 0 'CONCEDED RESIDUAL: push hidden inside an opaque shell -c string'
# --- CONCEDED RESIDUAL: a wrapper WITH its own arguments is not stepped over by starts_command ---
assert 'sudo -u deploy git push' 0 'CONCEDED RESIDUAL: wrapper-with-args is not recognized as a bare wrapper'

# --- a blocked push must emit the EXACT stderr message (full string compare, not a glob) ---
want_msg='blocked by push-guard: pushing is explicit-only. Lead the push segment with ALLOW_PUSH=1 (e.g. ALLOW_PUSH=1 git push ...) to authorize it.'
got_msg="$(printf '%s' "$(python3 -c 'import json;print(json.dumps({"tool_input":{"command":"git push"}}))')" | python3 "$guard" 2>&1 1>/dev/null)"
if [[ "$got_msg" == "$want_msg" ]]; then
  printf 'PASS  block stderr is byte-exact\n'; pass=$((pass + 1))
else
  printf 'FAIL  block stderr mismatch\n  want: %s\n  got:  %s\n' "$want_msg" "$got_msg"; fail=$((fail + 1))
fi

printf '\n%d passed, %d failed\n' "$pass" "$fail"
[[ "$fail" -eq 0 ]]

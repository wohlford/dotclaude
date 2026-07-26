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
# D1 (2026-07-26): this used to fail OPEN. An unparseable command that MENTIONS git is now refused
# rather than allowed unchecked -- that swallow was the same fail-open class as the nested-context
# bypasses. The non-git counterpart below pins the unchanged fail-open posture, which is what keeps
# the blast radius push-shaped.
assert 'git push "oops' 2 'unbalanced quote + git word -> fail closed'

# --- CONCEDED RESIDUAL: an opaque string hides `push` from the tokenizer entirely ---
assert "bash -c 'git push'" 0 'CONCEDED RESIDUAL: push hidden inside an opaque shell -c string'
# --- CONCEDED RESIDUAL: a wrapper WITH its own arguments is not stepped over by starts_command ---
assert 'sudo -u deploy git push' 0 'CONCEDED RESIDUAL: wrapper-with-args is not recognized as a bare wrapper'

# --- CONCEDED RESIDUAL: a non-literal subcommand is not resolved here ---
# `git $s` cannot be recognized as a push without expansion. publication-push-guard now FAILS
# CLOSED on this (it guards branch privacy, where an ambiguous target must never pass); this hook
# is a deliberateness nudge, and blocking here would demand ALLOW_PUSH=1 for a command that may
# not be a push at all. Recorded deliberately so the asymmetry is documented, not discovered.
# shellcheck disable=SC2016
assert 's=push; git $s origin dev' 0 'CONCEDED RESIDUAL: non-literal subcommand not resolved here'

# --- regression: a line continuation must not hide the subcommand ---
# `\` + newline is how any long git command is written. Newlines were rewritten to ` ; ` BEFORE
# shlex saw the backslash, so the escaped space became the subcommand and this exited 0.
assert "$(printf 'git \\\n  push origin dev')" 2 'regression: continuation before the subcommand'
assert "$(printf 'git push \\\n  origin dev')" 2 'regression: continuation mid-arguments'
assert "$(printf 'ALLOW_PUSH=1 git \\\n  push origin dev')" 0 'continuation + override still authorized'
# The fold must not swallow a real newline separating two commands.
assert "$(printf 'git status\ngit push origin dev')" 2 'newline-separated push still blocked'

# --- a blocked push must emit the EXACT stderr message (full string compare, not a glob) ---
want_msg='blocked by push-guard: pushing is explicit-only. Lead the push segment with ALLOW_PUSH=1 (e.g. ALLOW_PUSH=1 git push ...) to authorize it.'
got_msg="$(printf '%s' "$(python3 -c 'import json;print(json.dumps({"tool_input":{"command":"git push"}}))')" | python3 "$guard" 2>&1 1>/dev/null)"
if [[ "$got_msg" == "$want_msg" ]]; then
  printf 'PASS  block stderr is byte-exact\n'; pass=$((pass + 1))
else
  printf 'FAIL  block stderr mismatch\n  want: %s\n  got:  %s\n' "$want_msg" "$got_msg"; fail=$((fail + 1))
fi

# --- nested command contexts: an unauthorized push is still a push (exit 2) ---
assert 'x="$(git push origin dev)"' 2 'push inside quoted $( )'
assert 'x=`git push origin dev`' 2 'push inside backticks'
assert 'x="$(git push origin dev)" && git status' 2 'push in $( ) with trailing git'
assert 'x="$(echo `git push origin dev`)"' 2 'backtick nested inside $( )'
assert 'cat <(git push origin dev)' 2 'push inside process substitution'
assert 'x=`echo \`git push origin dev\`` ' 2 'escaped backticks at depth 2'

# --- the span rule (spec 4.6, evidence rows 10-12) ---
assert 'git commit -m "$(git push origin dev)"' 2 'push in commit'"'"'s own arg span'
assert 'git tag -a v1 -m "$(git push origin dev)"' 2 'push in tag'"'"'s own arg span'
assert 'git -c x="$(git push origin dev)" status' 2 'push in the global-option run'
assert 'git status > "$(git push origin dev)"' 2 'push in a redirect target'

# --- evidence row 13: a trailing # comment must not swallow the next line ---
assert 'git status  # note
git push origin dev' 2 'comment truncation no longer hides the push'

# --- D1: ambiguity now fails CLOSED, but ONLY when the command mentions git ---
assert 'x="$(git push origin dev' 2 'unterminated context fails closed'

# Depth overflow must also fail closed at the GATE, not only in the unit tests. Build the nested
# string in bash rather than writing ten literal levels by hand.
deep='git push origin dev'
for _ in 1 2 3 4 5 6 7 8 9 10; do deep="x=\"\$($deep)\""; done
assert "$deep" 2 'nesting past the depth limit fails closed'

# BLAST-RADIUS BOUND: an unparseable command with NO git word must still fail OPEN. Without this,
# D1 turns push-guard from a push gate into a gate on every malformed command in the session.
assert "echo 'unterminated" 0 'unparseable NON-git command still fails open'
assert 'sed -nE '"'"'s/a"b"c"d/\1/p'"'"' /dev/null' 0 'quote-heavy non-git command still allowed'

# --- the ALLOW_PUSH override survives, at top level and inside a context ---
assert 'ALLOW_PUSH=1 git push origin dev' 0 'override at top level is honored'
assert 'x="$(ALLOW_PUSH=1 git push origin dev)"' 0 'override INSIDE a context is honored'
assert 'ALLOW_PUSH=1 git push origin dev && git status' 0 'override + trailing benign git'

# --- CONCEDED RESIDUALS (spec 7b): still ALLOW; pinning current behavior, not an aspiration ---
# If one goes red, something CLOSED it -- investigate and move the row, never invert the assertion.
assert 'sh -c "git push origin dev"' 0 'RESIDUAL: sh -c still allows'
assert "bash -c 'git push origin dev'" 0 'RESIDUAL: bash -c still allows'
assert 'eval "git push origin dev"' 0 'RESIDUAL: eval still allows'
assert "bash -lc 'git push origin dev'" 0 'RESIDUAL: bash -lc still allows'
assert "/bin/sh -c 'git push origin dev'" 0 'RESIDUAL: path-qualified shell still allows'
assert "echo 'git push origin dev' | sh" 0 'RESIDUAL: pipe-into-shell still allows'
assert 'sh <<< "git push origin dev"' 0 'RESIDUAL: herestring still allows'

# --- NOT residuals: green today, must stay green ---
assert 'echo origin dev | xargs git push' 2 'bare xargs git push still blocks'
assert 'git \
 push origin dev' 2 'backslash-newline continuation still blocks (v0.49.7)'

# --- protected baselines: unchanged ---
assert "echo 'git push origin dev'" 0 'single-quoted literal is not a push'
assert 'x="$(( 1 + 2 ))" && git status' 0 'arithmetic expansion is not a push'
assert 'x="$(git status)"' 0 'non-push git inside a substitution stays allowed'


printf '\n%d passed, %d failed\n' "$pass" "$fail"
[[ "$fail" -eq 0 ]]

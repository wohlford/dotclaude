#!/usr/bin/env bash
set -uo pipefail

# Script: test_publication_push_guard.sh
# Purpose: Regression tests for publication-push-guard.py — the fail-closed dev-block gate that
#          keeps `dev` local in a repo that has adopted the dev/main publication model
#          (.publication.toml at the repo root). Real sandbox repos + a `cwd` distinct from the
#          repo root, following test_recast_hooks.sh's pattern (a command-string-only harness
#          can't exercise branch/root resolution).
# Usage:   bash scripts/tests/test_publication_push_guard.sh

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$here/../.." && pwd)"
guard="$repo_root/scripts/publication-push-guard.py"

sandbox="$(mktemp -d)"
trap 'rm -rf "$sandbox"' EXIT

pass=0
fail=0

assert_eq() { # got want label
  if [[ "$1" -eq "$2" ]]; then
    printf 'PASS  %s (exit %d)\n' "$3" "$1"
    pass=$((pass + 1))
  else
    printf 'FAIL  %s (want %d, got %d)\n' "$3" "$2" "$1"
    fail=$((fail + 1))
  fi
}

# ---------- git with signing off and a fixed identity ----------
gi() { git -C "$1" -c commit.gpgsign=false -c tag.gpgsign=false \
  -c user.email=t@t.invalid -c user.name=t -c init.defaultBranch=main "${@:2}"; }

# ---------- JSON builder (via python to dodge shell-quoting bugs) ----------
push_json() { python3 -c 'import json,sys;print(json.dumps({"tool_input":{"command":sys.argv[1]},"cwd":sys.argv[2]}))' "$1" "$2"; }

push_run() { # cwd command want label
  local got=0
  push_json "$2" "$1" | python3 "$guard" >/dev/null 2>&1 || got=$?
  assert_eq "$got" "$3" "$4"
}

# build_repo <adopted:0|1> [production_value] -> sets global REPO
# The marker (when adopted) is committed on the FIRST commit, before `dev` branches off, so it is
# present in the working tree regardless of which branch ends up checked out later.
build_repo() {
  REPO="$(mktemp -d)"
  gi "$REPO" init -q >/dev/null 2>&1
  printf 'hello\n' >"$REPO/README.md"
  if [[ "${1:-0}" == 1 ]]; then
    printf 'production = "%s"\n' "${2:-dev}" >"$REPO/.publication.toml"
  fi
  gi "$REPO" add -A >/dev/null 2>&1
  gi "$REPO" commit -q -m init >/dev/null 2>&1
  gi "$REPO" branch -q dev >/dev/null 2>&1
}

# A second, throwaway repo used as a "cwd elsewhere" for -C / cd tests: a real (non-adopted) repo,
# not just an empty dir, so an incorrect root-resolution would silently succeed and ALLOW (the
# BLOCKER-2 regression this suite exists to catch).
build_elsewhere() {
  ELSEWHERE="$(mktemp -d)"
  gi "$ELSEWHERE" init -q >/dev/null 2>&1
  printf 'x\n' >"$ELSEWHERE/f.txt"
  gi "$ELSEWHERE" add -A >/dev/null 2>&1
  gi "$ELSEWHERE" commit -q -m init >/dev/null 2>&1
}

# ================= BLOCKED: adopted repo, dev-spanning plain refspecs =================
build_repo 1
push_run "$REPO" "git push origin dev" 2 "blocked: origin dev"
push_run "$REPO" "git push origin dev:main" 2 "blocked: dev:main"
push_run "$REPO" "git push origin main:dev" 2 "blocked: main:dev"

build_repo 1
gi "$REPO" checkout -q dev
push_run "$REPO" "git push" 2 "blocked: bare push, HEAD==dev"
push_run "$REPO" "git push origin HEAD" 2 "blocked: HEAD refspec, HEAD==dev"

# ================= BLOCKED: ambiguous sweeps =================
build_repo 1
push_run "$REPO" "git push --all" 2 "blocked: --all"
push_run "$REPO" "git push --mirror" 2 "blocked: --mirror"

# ================= BLOCKED: force of dev =================
build_repo 1
push_run "$REPO" "git push origin +dev" 2 "blocked: +dev (leading-plus force)"
push_run "$REPO" "git push --force origin dev" 2 "blocked: --force ... dev"

# ================= BLOCKED: root resolved from elsewhere (-C / cd), still adopted =================
build_repo 1
build_elsewhere
push_run "$ELSEWHERE" "git -C $REPO push origin dev" 2 "blocked: git -C <adopted> push origin dev, from non-adopted cwd"
push_run "$ELSEWHERE" "cd $REPO && git push origin dev" 2 "blocked: cd <adopted> && git push origin dev, from non-adopted cwd"

# ================= BLOCKED: --git-dir / GIT_DIR= forces block regardless of an otherwise-safe target =================
build_repo 1
build_elsewhere
push_run "$ELSEWHERE" "git --git-dir=$REPO/.git push origin main" 2 "blocked: --git-dir override (even targeting main)"
push_run "$ELSEWHERE" "GIT_DIR=$REPO/.git git push origin main" 2 "blocked: GIT_DIR= env assignment (even targeting main)"

# ================= BLOCKED: malformed / ambiguous command =================
build_repo 1
push_run "$REPO" "git push origin 'dev" 2 "blocked: unterminated quote (fail-closed on tokenizing ambiguity)"

# ================= BLOCKED: revision suffix / wildcard =================
build_repo 1
push_run "$REPO" "git push origin dev~1" 2 "blocked: revision suffix dev~1"
push_run "$REPO" "git push origin refs/heads/*:refs/heads/*" 2 "blocked: wildcard refspec"

# ================= BLOCKED: tag reachable only from dev =================
build_repo 1
gi "$REPO" checkout -q dev
printf 'dev-only\n' >"$REPO/dev.txt"
gi "$REPO" add -A >/dev/null 2>&1
gi "$REPO" commit -q -m dev-commit >/dev/null 2>&1
gi "$REPO" tag devtag >/dev/null 2>&1
gi "$REPO" checkout -q main
push_run "$REPO" "git push origin devtag" 2 "blocked: explicit tag reachable only from dev"
push_run "$REPO" "git push --tags origin main" 2 "blocked: --tags sweeps a dev-only tag"
push_run "$REPO" "git push --follow-tags origin main" 2 "blocked: --follow-tags sweeps a dev-only tag"

# ================= ALLOWED: adopted repo, safe plain refspecs =================
build_repo 1
push_run "$REPO" "git push origin main" 0 "allowed: origin main"

build_repo 1
push_run "$REPO" "git push" 0 "allowed: bare push, HEAD==main"

# ================= ALLOWED: force of main (the cutover) =================
build_repo 1
push_run "$REPO" "git push --force origin main" 0 "allowed: --force origin main"
push_run "$REPO" "git push --force-with-lease origin main" 0 "allowed: --force-with-lease origin main"
push_run "$REPO" "git push origin +main" 0 "allowed: +main (leading-plus force)"

# ================= Cutover guard cross-check (executed cases, not a stated
# verdict) =====================================================================================
# Proves the real force-push the cutover runbook runs is judged correctly by THIS guard,
# by actually running it here rather than reasoning about the code. Two of the five ground-truth
# cases are already covered above and are not repeated: forced-main bare lease is "allowed:
# --force-with-lease origin main" two lines up, and forced-dev is "blocked: --force ... dev" in the
# "BLOCKED: force of dev" section earlier in this file. The three cases below are the load-bearing
# NEW coverage: the exact locked-lease refspec form the runbook actually runs, why a SHA-shaped
# source is rejected by the plain-name grammar (documenting WHY the runbook pushes the branch name
# `main`, never a commit SHA), and a force-with-lease refspec that disguises a `dev` source behind
# an explicit `refs/heads/main` destination.
build_repo 1
push_run "$REPO" "ALLOW_PUSH=1 git push --force-with-lease=main:0000000000000000000000000000000000000000 origin main:refs/heads/main" 0 \
  "cutover cross-check: forced main, locked lease, explicit refspec -> allowed"

build_repo 1
push_run "$REPO" "ALLOW_PUSH=1 git push --force-with-lease=main:0000000000000000000000000000000000000000 origin deadbeefdeadbeefdeadbeefdeadbeefdeadbeef:refs/heads/main" 2 \
  "cutover cross-check: forced SHA-shaped source refspec fails the plain-name grammar -> blocked"

build_repo 1
push_run "$REPO" "git push --force-with-lease origin dev:refs/heads/main" 2 \
  "cutover cross-check: disguised force-with-lease refspec targeting dev -> blocked"

# ================= ALLOWED: tag reachable from main =================
build_repo 1
gi "$REPO" tag maintag >/dev/null 2>&1
push_run "$REPO" "git push origin maintag" 0 "allowed: explicit tag reachable from main"

build_repo 1
gi "$REPO" tag maintag >/dev/null 2>&1
push_run "$REPO" "git push --follow-tags origin main" 0 "allowed: --follow-tags, all tags main-reachable"

# ================= ALLOWED: any push in a non-adopted repo (dormant) =================
build_repo 0
push_run "$REPO" "git push origin dev" 0 "allowed: non-adopted repo, dev push (dormant until adoption)"
push_run "$REPO" "git push --all" 0 "allowed: non-adopted repo, --all"

build_elsewhere
push_run "$ELSEWHERE" "git -C $ELSEWHERE push origin dev" 0 "allowed: non-adopted repo via -C"

# ================= Deliberate over-block: --git-dir still blocks a NON-adopted repo's push =====
# Root-unknown blocks regardless of marker, by design (see _judge_invocation and the module
# docstring's --git-dir residual note): --git-dir makes the target repo unresolvable to the guard
# BEFORE it ever reaches the .publication.toml check, so an otherwise-ordinary `main` push into a
# repo that never adopted the model is blocked anyway. This is intentional fail-closed behavior,
# not a bug — an unknown repo means adoption can't be confirmed either way, so the guard cannot
# prove the push is safe and refuses rather than guesses.
build_repo 0
build_elsewhere
push_run "$ELSEWHERE" "git --git-dir=$REPO/.git push origin main" 2 "deliberate over-block: --git-dir into a non-adopted repo still blocks (root-unknown)"

# ================= Bonus: alias resolution (Fable MAJOR) =================
build_repo 1
gi "$REPO" config alias.deploy "push origin dev" >/dev/null 2>&1
push_run "$REPO" "git deploy" 2 "bonus blocked: custom alias expanding to a dev push"

build_repo 1
gi "$REPO" config alias.st "status" >/dev/null 2>&1
push_run "$REPO" "git st" 0 "bonus allowed: custom alias expanding to a non-push command"

# ================= Regression: clustered short-option value consumption (FIX 1) =================
# `-fo blah` must consume `blah` as -o's value, not let it fall through as a positional that shifts
# the real remote into the refspec slot and skips the current-branch-is-dev check. Reproduced live
# against the pre-fix hook: exit 0 (real git pushes dev).
build_repo 1
gi "$REPO" checkout -q dev
push_run "$REPO" "git push -fo blah main" 2 "regression: clustered -fo consumes its value, HEAD==dev -> blocked"

# Positive control: -o's value must still be consumed (not over-consumed) when explicit remote +
# refspec follow, so a legitimate push-option doesn't itself get misread as the remote/refspec.
build_repo 1
push_run "$REPO" "git push -o ci.skip origin main" 0 "regression: -o value consumed without over-consuming remote/refspec -> allowed"

# ================= Regression: alias chain resolves to push (FIX 2) =================
# A 2-hop alias chain (a -> b origin dev, b -> push) must be chased recursively and blocked, the
# way real git itself resolves nested aliases. Reproduced live against the pre-fix hook: exit 0
# (real git expands a -> b origin dev -> push origin dev and pushes dev).
build_repo 1
gi "$REPO" config alias.a "b origin dev" >/dev/null 2>&1
gi "$REPO" config alias.b "push" >/dev/null 2>&1
push_run "$REPO" "git a" 2 "regression: 2-hop alias chain resolving to push -> blocked"

# ================= Bonus: detached HEAD on a bare push =================
build_repo 1
detached_sha="$(gi "$REPO" rev-parse HEAD)"
gi "$REPO" checkout -q "$detached_sha"
push_run "$REPO" "git push origin" 2 "bonus blocked: bare push while detached"

# ================= Fail-safe: non-git and garbage input never block =================
build_repo 1
push_run "$REPO" "ls -la" 0 "fail-safe: non-git command in adopted repo -> 0"
push_run "$REPO" "git status" 0 "fail-safe: unrelated git command in adopted repo -> 0"

got=0
printf 'not-json' | python3 "$guard" >/dev/null 2>&1 || got=$?
assert_eq "$got" 0 "fail-safe: garbage stdin -> 0"

got=0
printf '{"tool_input":{}}' | python3 "$guard" >/dev/null 2>&1 || got=$?
assert_eq "$got" 0 "fail-safe: JSON without .command -> 0"

# ================= Regression: non-dict JSON payload must not crash to exit 1 (FIX 3) =================
# Valid JSON that isn't an object (a bare number, string, or list) previously reached
# `data.get("tool_input", ...)` and raised AttributeError, which Python turns into exit 1 —
# fail-OPEN on a PreToolUse hook (only exit 2 blocks). This is the first layer (parseable command
# or not?), so the fix is a clean exit 0, same posture as garbage stdin — never exit 2 here, which
# would block every unrelated Bash command on an odd payload.
got=0
printf '42' | python3 "$guard" >/dev/null 2>&1 || got=$?
assert_eq "$got" 0 "regression: non-dict JSON payload (bare number) -> 0, not a crash"

got=0
printf '"just a string"' | python3 "$guard" >/dev/null 2>&1 || got=$?
assert_eq "$got" 0 "regression: non-dict JSON payload (bare string) -> 0, not a crash"

got=0
printf '[1,2,3]' | python3 "$guard" >/dev/null 2>&1 || got=$?
assert_eq "$got" 0 "regression: non-dict JSON payload (bare list) -> 0, not a crash"

# ================= Regression: quote-split obfuscation of the "git" word (Finding 1) =================
# `gi''t`/`gi\t` are collapsed to `git` by a real shell AND by this hook's own shlex tokenizer, but
# the cheap pre-filter historically matched the RAW command text — which has no contiguous "git" —
# so it returned 0 (ALLOW) before ever reaching the tokenizer/adoption logic. Verified live: a real
# shell runs `git push origin dev` and publishes `dev` while the pre-fix hook exits 0.
build_repo 1
gi "$REPO" checkout -q dev
push_run "$REPO" "gi''t push origin dev" 2 "regression: quote-split gi''t bypassing the git-word pre-filter -> blocked"

build_repo 1
gi "$REPO" checkout -q dev
push_run "$REPO" "gi\\t push origin dev" 2 "regression: backslash-split gi\\t bypassing the git-word pre-filter -> blocked"

# ================= Regression: quote-split obfuscation of --git-dir (Finding 2) =================
# `--git-di''r=<path>` is collapsed to `--git-dir=<path>` by a real shell AND by this hook's own
# shlex tokenizer, but GITDIR_RE historically matched the RAW command text, missing the quote-split
# form — so the root-unknown block never fired and the guard resolved the root from `cwd` instead
# (a non-adopted repo, dormant), silently allowing a push that real git routes into the adopted
# repo via --git-dir. Verified live: a real shell pushes `dev` into the adopted repo from a
# non-adopted cwd while the pre-fix hook exits 0.
build_repo 1
build_elsewhere
push_run "$ELSEWHERE" "git --git-di''r=$REPO/.git push origin dev" 2 "regression: quote-split --git-di''r= bypassing the gitdir pre-check -> blocked (root-unknown)"

# ================= Positive control: quoting alone must not make the pre-filter over-block =========
# A normal non-git command that happens to contain quotes must still exit 0 via the cheap
# pre-filter — de-quoting widens what counts as "the word git", not what counts as "contains a
# quote character".
push_run "$REPO" 'echo "hello"' 0 "positive control: quoted non-git command still exits 0 fast"

# ================= A blocked push must emit a distinct, greppable stderr message =================
msg="$(push_json "git push origin dev" "$REPO" | python3 "$guard" 2>&1 1>/dev/null)"
case "$msg" in
  *publication-push-guard*) printf 'PASS  block emits a distinct publication-push-guard stderr message\n'; pass=$((pass + 1)) ;;
  *) printf 'FAIL  block stderr message missing/unhelpful: %s\n' "$msg"; fail=$((fail + 1)) ;;
esac

printf '\n%d passed, %d failed\n' "$pass" "$fail"
[[ "$fail" -eq 0 ]]

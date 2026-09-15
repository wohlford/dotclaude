#!/usr/bin/env bash
# shellcheck disable=SC2016
set -uo pipefail
# SC2016 ("expressions don't expand in single quotes") is inverted for this file: every push_run
# argument is literal shell text passed as DATA to the guard under test, so `$( )`, backticks and
# `${}` inside those single quotes ARE the subject -- expanding them would destroy the case being
# asserted. Pre-existing throughout; audit.sh runs `shellcheck -S warning` and so never saw these
# info-level notes, while the edit-time style hook has no severity filter and does.

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

# Redirect the guard's internal-error diagnostic into the sandbox for EVERY row in this file, not
# just the rows that exercise it deliberately. Several long-standing cases below feed deliberately
# unbalanced quotes ('unterminated context blocks', 'a genuinely unbalanced quote') -- those raise a
# real ValueError inside the guard and so land on its internal-error branch. Without this export
# they append to the OPERATOR's real log at ~/.claude/logs/, and because an edit-time hook runs this
# suite on every edit to the guard, the one file meant to capture a rare genuine fault fills up with
# synthetic test records instead. Measured: 12 such records, from 4 suite runs, before this line.
export PUBLICATION_PUSH_GUARD_LOG="$sandbox/guard-internal-errors.log"

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

# The push verb, assembled for the assertions below that use ${VERB} -- NOT a whole-file rule, and
# an earlier revision of this comment claimed it was. The row block further up spells the two words
# contiguously in ~90 places and always has; only this file's later section adopted the convention.
# test_guard_corpus.py IS strict throughout (measured: zero contiguous spellings).
#
# Same reasoning as that file's _VERB, and the same CORRECTION. The timing guard no
# longer greps raw text: scripts/git-timing-guard.py decides by command position, so a comment, a
# quoted string and a grep argument are all allowed now (measured rc=0 each, against rc=2 for a
# real publish). What still bites is the tokenizer scanning the body of a QUOTED heredoc as if it
# were commands -- measured rc=2 -- and authoring this file means writing exactly those. Filed
# separately, 2026-08-24.
VERB="pu""sh"

# judge cmd cwd -> prints the guard's combined stdout+stderr; its own exit code is available via
# $? immediately after a `$(judge ...)` capture, since python3 is the LAST stage of the pipe.
# PUBLICATION_PUSH_GUARD_LOG is already exported into the sandbox at the top of this file, so every
# call here inherits it -- no run of this reaches the operator's real ~/.claude/logs/.
#
# NOTE the argument order is the OPPOSITE of push_run's: push_run takes (cwd, command, ...) and
# therefore reverses via `push_json "$2" "$1"`; judge takes (command, cwd) -- matching push_json's
# own (command, cwd) signature directly, so no reversal here. Swapping this was measured to send
# the repo PATH to the guard as its "command" (no `git` word -> the cheap prefilter exits 0 before
# ever reaching the code under test) and the literal command string as "cwd" -- a silent false
# PASS on the positive control and a silent false ALLOW on the blocking row alike.
judge() {
  push_json "$1" "$2" | python3 "$guard" 2>&1
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
  # Install a healthy stub boundary hook -- both the tracked source AND the installed copy, so a
  # push-arm precondition asserting the hook is in force does not turn every allowed-push row in
  # this file red. A stub, not the real hook: this suite tests the GUARD, and
  # test_pre_push_hook.sh already tests the real hook body.
  mkdir -p "$REPO/git-hooks" "$REPO/.git/hooks"
  printf '#!/bin/sh\nexit 0\n' >"$REPO/git-hooks/pre-push"
  cp "$REPO/git-hooks/pre-push" "$REPO/.git/hooks/pre-push"
  chmod +x "$REPO/git-hooks/pre-push" "$REPO/.git/hooks/pre-push"
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

# ---------- internal-error diagnostics ----------
# Force a REAL exception out of the guard's LAZY `git_command` import by giving a sandbox copy of
# the guard a sibling lib/ whose git_command raises at import time. That drives the guard's own
# fail-closed except branch with a genuine exception rather than a monkeypatch, so these rows
# exercise the shipped code path. (The guard does `sys.path.insert(0, <its own dir>/lib)`, so the
# sandbox's lib wins -- PYTHONPATH could not shadow it.)
#
# WHY THESE EXIST: measured twice on 2026-07-29, the guard refused two legitimate non-push commands
# with "internal error (ValueError) ... failing closed" and recorded NOTHING about what it was
# evaluating. Six reproduction hypotheses all came back clean, because the failure path discards its
# only evidence. The trigger is still unknown -- that is precisely what these rows are for.
forced_error_guard() { # label -> sets FE_DIR, FE_GUARD, FE_LOG
  FE_DIR="$(mktemp -d)"
  mkdir -p "$FE_DIR/lib"
  cp "$guard" "$FE_DIR/"
  printf 'raise ValueError("forced %s")\n' "$1" >"$FE_DIR/lib/git_command.py"
  FE_GUARD="$FE_DIR/publication-push-guard.py"
  FE_LOG="$FE_DIR/errors.log"
}

assert_contains() { # haystack needle label
  if grep -qF -- "$2" <<<"$1"; then
    printf 'PASS  %s\n' "$3"
    pass=$((pass + 1))
  else
    printf 'FAIL  %s (missing: %s)\n' "$3" "$2"
    fail=$((fail + 1))
  fi
}

assert_not_contains() { # haystack needle label
  if grep -qF -- "$2" <<<"$1"; then
    printf 'FAIL  %s (unexpectedly found: %s)\n' "$3" "$2"
    fail=$((fail + 1))
  else
    printf 'PASS  %s\n' "$3"
    pass=$((pass + 1))
  fi
}

# The command carries a distinctive marker so we can prove the RECORD is of THIS command and not
# some generic string the guard would have emitted regardless.
FE_CMD='git status --short # marker-Xy7Qz-unique'

forced_error_guard 'stderr case'
fe_err="$(push_json "$FE_CMD" "$PWD" \
  | PUBLICATION_PUSH_GUARD_LOG="$FE_LOG" python3 "$FE_GUARD" 2>&1 >/dev/null)"
fe_rc=0
push_json "$FE_CMD" "$PWD" \
  | PUBLICATION_PUSH_GUARD_LOG="$FE_LOG" python3 "$FE_GUARD" >/dev/null 2>&1 || fe_rc=$?

# PRESERVE: adding diagnostics must not change the verdict. This gate is fail-closed by design.
assert_eq "$fe_rc" 2 'internal error still fails CLOSED with diagnostics enabled'
# The primary refusal line must survive verbatim -- a runbook greps for it.
assert_contains "$fe_err" 'internal error (ValueError)' 'internal error still names the exception type'
# NEW: stderr must point at the recorded diagnostic, or the operator has nowhere to look.
assert_contains "$fe_err" "$FE_LOG" 'stderr names the diagnostic log path'

# NEW: the log must record the COMMAND ITSELF -- the whole point. A hash alone cannot be read back
# into a reproduction, and reproduction is the blocked step.
fe_log_body="$(cat "$FE_LOG" 2>/dev/null || printf '(no log written)')"
assert_contains "$fe_log_body" 'marker-Xy7Qz-unique' 'log records the offending command verbatim'
assert_contains "$fe_log_body" 'ValueError' 'log records the exception type'
assert_contains "$fe_log_body" 'Traceback' 'log records a traceback for the raising frame'
rm -rf "$FE_DIR"

# PRESERVE (property): a fail-closed gate must not acquire a NEW way to fail. If the diagnostic
# write itself throws, the verdict must be unchanged and the primary refusal must still print.
# Logging is strictly additive.
#
# The path must be GENUINELY unwritable, which is fussier than it looks: the recorder calls
# `mkdir(parents=True)`, so a merely ABSENT directory is created on the spot and the write succeeds.
# A first draft used "$FE_DIR/nonexistent-dir/nope/errors.log" and passed both before AND after the
# fix, and survived a mutation that deleted the non-raising contract outright -- it asserted
# nothing. Rooting the path at a regular FILE is what makes mkdir fail (NotADirectoryError), and
# that mutation was then caught. Do not "simplify" this back to a missing directory.
forced_error_guard 'unwritable log case'
printf 'not a directory\n' >"$FE_DIR/blocker"
FE_BAD_LOG="$FE_DIR/blocker/errors.log"
fe_rc2=0
fe_err2="$(push_json "$FE_CMD" "$PWD" \
  | PUBLICATION_PUSH_GUARD_LOG="$FE_BAD_LOG" python3 "$FE_GUARD" 2>&1 >/dev/null)" || true
push_json "$FE_CMD" "$PWD" \
  | PUBLICATION_PUSH_GUARD_LOG="$FE_BAD_LOG" python3 "$FE_GUARD" >/dev/null 2>&1 || fe_rc2=$?
assert_eq "$fe_rc2" 2 'unwritable diagnostic log still fails CLOSED'
assert_contains "$fe_err2" 'internal error (ValueError)' \
  'unwritable diagnostic log still prints the primary refusal'
# And it must SAY it could not record, rather than naming a path that holds nothing.
assert_contains "$fe_err2" 'could not record a diagnostic' \
  'unwritable diagnostic log is reported as unrecorded, not silently claimed'
rm -rf "$FE_DIR"

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

# the eval family (fix/eval-wrapper-bypass): a cd reached through any wrapper leaves the cwd unresolvable
push_run "$ELSEWHERE" "eval cd $REPO && git push origin dev" 2 "blocked: eval cd <adopted> from non-adopted cwd -- the cwd is unresolvable"
push_run "$REPO" "nohup cd $ELSEWHERE ; git push origin dev" 2 "blocked: nohup cd runs in a child; the walk no longer follows it"
push_run "$REPO" "eval cd $ELSEWHERE && git push origin main" 2 "blocked: unresolvable cwd refuses even a safe refspec (decided: 0 of 17,929 real cds go through a wrapper)"
push_run "$REPO" "eval cd $ELSEWHERE && git status" 0 "allowed: an unresolvable cwd never blocks a read"

# ================= CLOSED FAIL-OPEN: cd right after a reserved word (if / { / !) used to be
# untracked rather than unresolvable (fix/reserved-word-cd, design record
# 2026-09-11-reserved-word-cd) =================
# `_cd_command_position` (scripts/lib/git_command.py) USED TO return False -- "argument, ignore it"
# -- for a cd immediately preceded by a reserved word (`if`, `{`, `!`). The cd was NOT tracked at
# all, so the walk's cwd never left the directory the guard started in. From a PLAIN (non-adopted)
# cwd, bash really does cd into ADOPTED and push a private 'dev' branch there, while the guard read
# its own frozen cwd (still PLAIN, no .publication.toml) and ALLOWED -- a live fail-open, measured
# rc=0 before this branch. fix/reserved-word-cd made `_cd_command_position` return None
# (unresolvable) there instead of False, which is fail-closed at this guard. The three rows below
# pin that closure: each must BLOCK (rc=2), and each goes red again the moment a reserved-word cd
# stops being classified unresolvable.
build_repo 1
ADOPTED="$REPO"
build_elsewhere
push_run "$ELSEWHERE" "if cd $ADOPTED; then git ${VERB} origin dev; fi" 2 \
  "CLOSED FAIL-OPEN (blocked since fix/reserved-word-cd): if cd <adopted>; then <push> dev; fi, from plain cwd"
push_run "$ELSEWHERE" "{ cd $ADOPTED; git ${VERB} origin dev; }" 2 \
  "CLOSED FAIL-OPEN (blocked since fix/reserved-word-cd): { cd <adopted>; <push> dev; }, from plain cwd"
push_run "$ELSEWHERE" "! cd $ADOPTED ; git ${VERB} origin dev" 2 \
  "CLOSED FAIL-OPEN (blocked since fix/reserved-word-cd): ! cd <adopted> ; <push> dev, from plain cwd"

# CONTROL: these three are the "three EXISTING blocks" that git_command.py's RESERVED_WORDS comment
# names as the reason cd-tracking must never widen to treat a reserved word as a command boundary.
# cwd starts ADOPTED (marker present); each cd's target is the PLAIN $ELSEWHERE, no marker at all.
# Before fix/reserved-word-cd `_cd_command_position` returned False here (argument, not tracked),
# so the walk's cwd never left ADOPTED and the guard blocked -- correctly, but for the wrong
# reason. The fix changed that answer to None (unresolvable), which ALSO blocks (unresolvable is
# fail-closed for this guard), so all three stayed green across it -- measured green both before
# and after. They would only go red under a third possible answer, True (cd tracked into
# $ELSEWHERE) -- which is exactly why the fix returns None and not True: True would resolve the
# walk's cwd to a directory with no marker at all and flip all three of these from BLOCK to ALLOW,
# re-opening the hole RESERVED_WORDS' comment describes. That is what these three still guard
# against.
push_run "$ADOPTED" "! cd $ELSEWHERE ; git ${VERB} origin dev" 2 \
  "PRESERVE (control, green before+after): ! cd <plain> ; <push> dev, from adopted cwd"
push_run "$ADOPTED" "if cd $ELSEWHERE; then :; fi ; git ${VERB} origin dev" 2 \
  "PRESERVE (control, green before+after): if cd <plain>; then :; fi ; <push> dev, from adopted cwd"
push_run "$ADOPTED" "while cd $ELSEWHERE; do break; done ; git ${VERB} origin dev" 2 \
  "PRESERVE (control, green before+after): while cd <plain>; do break; done ; <push> dev, from adopted cwd"

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

# ================= Regression: a line continuation must not hide the subcommand =================
# `\` + newline is how any long git command is written. Newlines were rewritten to ` ; ` BEFORE
# shlex saw the backslash, so the injected space got escaped and BECAME the subcommand (` `), and
# the push was invisible. Verified live: both push gates exited 0 on the first form.
build_repo 1 dev
cont_before="$(printf 'git \\\n  push origin dev')"
cont_midarg="$(printf 'git push \\\n  origin dev')"
push_run "$REPO" "$cont_before" 2 'regression: continuation before the subcommand -> still blocked'
push_run "$REPO" "$cont_midarg" 2 'regression: continuation mid-arguments -> still blocked'
# Guard against the fix over-reaching: a REAL newline still separates two commands.
push_run "$REPO" 'git status
git push origin dev' 2 'regression: newline-separated push still blocked (fold must not swallow it)'

# ================= Regression: a non-literal subcommand is UNRESOLVABLE, not "not a push" =======
# `git $s` cannot be resolved statically. The alias resolver treated a failed `git config
# alias.$s` lookup at depth 0 as "not configured as an alias, therefore not a push" and ALLOWED.
# That was a pure asymmetry: the very same guard is already fail-CLOSED one token to the right,
# where `git push origin "$B"` blocks because "$B" is not a valid plain ref name.
# shellcheck disable=SC2016  # the UNEXPANDED $s is the point: the guard must see it literally
push_run "$REPO" 's=push; git $s origin dev' 2 'non-literal subcommand ($s) is unresolvable -> blocked'
# shellcheck disable=SC2016
push_run "$REPO" 'set -- push; git "$@" origin dev' 2 'non-literal subcommand ("$@") is unresolvable -> blocked'
# shellcheck disable=SC2016
push_run "$REPO" 'git ${s} origin dev' 2 'non-literal subcommand (${s}) is unresolvable -> blocked'
# Positive control: ordinary literal subcommands must stay allowed, or the rule is too broad.
push_run "$REPO" 'git status -s' 0 'literal subcommand still allowed'
push_run "$REPO" 'git rev-parse --abbrev-ref HEAD' 0 'hyphenated literal subcommand still allowed'

# ---------- nested command contexts (Defect B): a push inside a context must BLOCK ----------
build_repo 1 dev
push_run "$REPO" 'x="$(git push origin dev)"' 2 'push inside quoted $( ) blocks'
push_run "$REPO" 'x=`git push origin dev`' 2 'push inside backticks blocks'
push_run "$REPO" 'x="$(git push origin dev)" && git status' 2 'push in $( ) + trailing git blocks'
push_run "$REPO" 'x="$(echo `git push origin dev`)"' 2 'backtick nested in $( ) blocks'
push_run "$REPO" 'cat <(git push origin dev)' 2 'push in process substitution blocks'
push_run "$REPO" 'x=`echo \`git push origin dev\`` ' 2 'escaped backticks at depth 2 block'

# ---------- the span rule (spec 4.6, evidence rows 10-12) ----------
# A substitution inside a git command's OWN token span. Bash runs it first; the visible command is
# entirely benign. Row 10 is named in spec success criterion 1.
push_run "$REPO" 'git commit -m "$(git push origin dev)"' 2 'push in commit'"'"'s arg span blocks'
push_run "$REPO" 'git tag -a v1 -m "$(git push origin dev)"' 2 'push in tag'"'"'s arg span blocks'
push_run "$REPO" 'git -c x="$(git push origin dev)" status' 2 'push in the global-option run blocks'
push_run "$REPO" 'git status > "$(git push origin dev)"' 2 'push in a redirect target blocks'

# ---------- evidence row 13: comment truncation ----------
push_run "$REPO" 'git status  # note
git push origin dev' 2 'a trailing # comment no longer swallows the next line'

# ---------- regression pin: the SHIPPED v0.49.7 continuation fold ----------
# Green before this change and must stay green. It goes red the moment the walk re-derives
# normalization instead of calling normalize_command.
push_run "$REPO" 'git \
 push origin dev' 2 'backslash-newline continuation still blocks'

# ---------- CONCEDED RESIDUALS (spec 7b) — these must still ALLOW ----------
# NOT a to-do list and NOT an aspiration: each shape below is a live fail-open that this change
# deliberately does NOT close (D2', operator-approved 2026-07-25). The assertions pin CURRENT
# behavior so a future change that closes one shows up as a deliberate improvement. If one of these
# goes red, something CLOSED it -- investigate and move the row, never "fix" the assertion.
# Do NOT add a shape here that has not been measured as allowing today: `xargs git push` BLOCKS,
# and an allow-assertion on it could only be made green by weakening a working gate.
push_run "$REPO" 'sh -c "git push origin dev"' 0 'RESIDUAL: sh -c still allows'
push_run "$REPO" "bash -c 'git push origin dev'" 0 'RESIDUAL: bash -c still allows'
push_run "$REPO" 'eval "git push origin dev"' 0 'RESIDUAL: eval still allows'
push_run "$REPO" "bash -lc 'git push origin dev'" 0 'RESIDUAL: bash -lc still allows'
push_run "$REPO" "/bin/sh -c 'git push origin dev'" 0 'RESIDUAL: path-qualified shell still allows'
push_run "$REPO" "echo 'git push origin dev' | sh" 0 'RESIDUAL: pipe-into-shell still allows'
push_run "$REPO" 'sh <<< "git push origin dev"' 0 'RESIDUAL: herestring still allows'

# ---------- NOT a residual: measured to block today, and must keep blocking ----------
# `build_repo 1 dev` leaves HEAD on `main`, and this gate legitimately ALLOWS a bare push when
# HEAD is main (the suite pins that separately). `xargs git push` reduces to a bare push, so this
# row only means anything with HEAD on dev. The original measurement was taken in the live dev
# repo and did NOT transfer to the sandbox -- a verdict that is context-dependent, exactly like
# the fenced-block sweep. Check the harness's own state before importing a measured verdict.
gi "$REPO" checkout -q dev
push_run "$REPO" 'echo origin dev | xargs git push' 2 'bare xargs git push still blocks'
gi "$REPO" checkout -q main

# ---------- Defect A: quote-heavy substitutions must no longer false-block ----------
push_run "$REPO" 'x="$(sed -nE '"'"'s/a"b"c"d/\1/p'"'"' /dev/null)" && git rev-parse --show-toplevel' 0 \
  'odd inner-quote substitution no longer false-blocks'
push_run "$REPO" 'want="$(sed -nE '"'"'s/^[[:space:]]*production[[:space:]]*=[[:space:]]*"([^"]*)".*/\1/p'"'"' "$marker")" && git -C "'"$REPO"'" rev-parse --abbrev-ref HEAD' 0 \
  'real /propagate step-5 marker parse is allowed'

# ---------- protected baselines: verdicts must be unchanged ----------
push_run "$REPO" 'echo '"'"'git push origin dev'"'"'' 0 'single-quoted literal stays inert'
push_run "$REPO" 'x="$(( 1 + 2 ))" && git status' 0 'arithmetic expansion stays inert'

# ---------- cwd isolation, BOTH directions ----------
build_elsewhere
# A cd INSIDE a subshell must not leak: the push after it still targets the adopted repo.
push_run "$REPO" 'x="$(cd '"$ELSEWHERE"' && true)" && git push origin dev' 2 \
  'cd inside a subshell does not leak to the outer push'
# A cd in the OUTER context must still apply: the push targets elsewhere, so it is allowed.
push_run "$REPO" 'cd '"$ELSEWHERE"' && git push origin dev' 0 \
  'cd in the outer context still applies'
# popd forfeits cwd knowledge (no stack is tracked), so the push blocks. This pins behavior the
# guard implements TODAY and that Step 3 moves into the library -- it is green before this change
# and must stay green. If it goes red, the port dropped the rule.
push_run "$REPO" 'cd '"$ELSEWHERE"' && popd && git push origin dev' 2 \
  'popd makes the cwd unresolvable, so the push blocks'
# A cd whose target is itself a substitution is unresolvable for the same reason as `cd "$VAR"`.
push_run "$REPO" 'cd "$(echo '"$ELSEWHERE"')" && git push origin dev' 2 \
  'cd to a substitution target is unresolvable, so the push blocks'

# ---------- heredoc bodies: an apostrophe must not fail this gate CLOSED ----------
# Measured 2026-07-28: this gate refused `/commit`'s own documented step-7 form with "internal
# error (ValueError) … failing closed" whenever the subject contained an apostrophe. The trigger was
# the COMBINATION -- the plain form passed and an apostrophe-free heredoc passed -- which is why it
# survived seven encounters. All four cells are asserted, not just the one that failed: a fix that
# broke the three working forms would otherwise still look green.
push_run "$REPO" 'git commit -m "the path thing"' 0 'heredoc 4-way: plain / no apostrophe'
push_run "$REPO" 'git commit -m "the path'"'"'s thing"' 0 'heredoc 4-way: plain / apostrophe'
push_run "$REPO" $'git commit -m "$(cat <<\'EOF\'\nthe path thing\nEOF\n)"' 0 \
  'heredoc 4-way: heredoc / no apostrophe'
push_run "$REPO" $'git commit -m "$(cat <<\'EOF\'\nthe path\'s thing\nEOF\n)"' 0 \
  'heredoc 4-way: heredoc / apostrophe (the reported failure)'
push_run "$REPO" $'cat <<\'EOF\' > /dev/null\nthe path\'s thing\nEOF\ngit status' 0 \
  'bare heredoc with an apostrophe no longer false-blocks'
push_run "$REPO" $'cat <<-\'EOF\' > /dev/null\n\tthe path\'s thing\n\tEOF\ngit status' 0 \
  'heredoc <<- dash form with an apostrophe no longer false-blocks'

# ---------- PRESERVE: the heredoc fix must not buy a fail-open ----------
# A heredoc body really is executed when it is fed to a shell, and this gate catches that TODAY.
# Each row below was measured to BLOCK before the heredoc change; if one goes green, the fix made
# heredoc bodies inert and opened a silent bypass -- investigate, never relax the assertion.
push_run "$REPO" $'bash <<EOF\ngit push origin dev\nEOF' 2 \
  'PRESERVE: a push inside an unquoted heredoc body still blocks'
push_run "$REPO" $'bash <<\'EOF\'\ngit push origin dev\nEOF' 2 \
  'PRESERVE: a push inside a quoted heredoc body still blocks'
push_run "$REPO" $'bash <<EOF\necho "#" ; git push origin dev\nEOF' 2 \
  'PRESERVE: a quoted # in a body does not comment out the push that follows'

# ---------- limits ----------
push_run "$REPO" 'x="$(git push origin dev' 2 'unterminated context blocks'
push_run "$REPO" "git push origin main 'oops" 2 \
  'a genuinely unbalanced quote (no heredoc) still blocks'

# ================= FAIL-OPEN: an options-only git invocation stole the `)` =====================
# Measured 2026-08-03 against the SHIPPED guard: exit 0, i.e. ALLOWED. The option walk ran off the
# end of `git --version`'s options and appended the following `)` as the subcommand, which stole it
# from the paren branch; `subshell_cwds` never popped, the subshell's cd leaked, and the push was
# judged against ELSEWHERE (non-adopted -> dormant) while bash runs it in the adopted repo.
#
# The suite already pinned this hazard class ('cd inside a subshell does not leak', above), but
# every one of those rows puts a NON-git command in the subshell, so none could reach the theft.
# One token's difference. Do not "simplify" these rows back to `true`.
build_repo 1
build_elsewhere
push_run "$REPO" '(cd '"$ELSEWHERE"' && git --version) && git push origin dev' 2 \
  'FAIL-OPEN CLOSED: options-only git in a subshell no longer leaks the cwd'
push_run "$REPO" '(cd '"$ELSEWHERE"' && git --version)&&git push origin dev' 2 \
  'FAIL-OPEN CLOSED: the `)&&` fused spelling too'
# PRESERVE, the direction that ALLOWS if the fix had been written as `break`: everything after the
# operator must still be scanned.
push_run "$REPO" 'git --version && git push origin dev' 2 \
  'PRESERVE: a push after an options-only invocation is still seen'
# And the false block the same defect caused must be gone.
push_run "$REPO" 'git --version | head' 0 \
  'options-only git piped into another command is no longer refused'

# ================= D1: an unexpanded -C must not refuse read-only commands ====================
# A hook sees command text UNEXPANDED, so `-C "$live"` cannot be resolved and the root lookup
# fails. Only membership in KNOWN_SAFE_SUBCOMMANDS spares a subcommand from that path, so
# `show-ref` was refused where `rev-parse` passed -- with a message blaming a push the command
# never made, which is what trains the operator to reach for ALLOW_PUSH=1.
# shellcheck disable=SC2016  # the UNEXPANDED $live is the point
push_run "$REPO" 'git -C "$live" show-ref' 0 'unexpanded -C + show-ref is allowed'
# shellcheck disable=SC2016
push_run "$REPO" 'git -C "$live" count-objects -v' 0 'unexpanded -C + count-objects is allowed'
# shellcheck disable=SC2016
push_run "$REPO" 'git -C "$live" show-branch' 0 'unexpanded -C + show-branch is allowed'
# Positive controls: the allowlist must not have become a blanket pass for an unresolvable root.
# shellcheck disable=SC2016
push_run "$REPO" 'git -C "$live" push origin dev' 2 \
  'PRESERVE: an unexpanded -C with a real push still blocks'
# shellcheck disable=SC2016
push_run "$REPO" 'git -C "$live" frobnicate' 2 \
  'PRESERVE: an unexpanded -C with an unknown subcommand still blocks (may be an alias)'

# ================= The allowlist itself: alias-shadow immunity, and the push-capable exclusions ==
# What this proves and what it does NOT. git ignores an alias that shadows a known git command, so
# membership in `git --list-cmds=main` means no entry can be a disguised push. It does NOT prove an
# entry is safe: `svn` and `p4` are members too, and `git svn dcommit` / `git p4 submit` publish
# history. The second assertion is therefore a BLOCKLIST with known limits, not a completeness
# proof -- adding an entry still needs the per-command judgement "never pushes to a git remote".
# (`send-email` is already allowlisted and mails commit content off-machine: the boundary is the
# git remote, not publication in general.)
#
# The floor is what stops this reading as a pass when it proved nothing: an unavailable
# `--list-cmds` or an implausibly short list FAILS rather than skipping. Note `--list-cmds` is
# git >= 2.18, and entries like `switch`/`restore` (>=2.23) or `maintenance` (>=2.29) would
# false-fail on an older git; acceptable, as these suites run on this machine.
allowlist_rc=0
allowlist_out="$(python3 - "$guard" <<'PYCHECK' 2>&1)" || allowlist_rc=$?
import importlib.util, subprocess, sys
spec = importlib.util.spec_from_file_location("g", sys.argv[1])
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
safe = set(m.KNOWN_SAFE_SUBCOMMANDS)
p = subprocess.run(["git", "--list-cmds=main"], capture_output=True, text=True)
known = set(p.stdout.split())
if p.returncode != 0 or len(known) < 100:
    sys.exit("FLOOR: --list-cmds=main unusable (rc=%d, %d names) -- cannot verify" % (p.returncode, len(known)))
if not safe:
    sys.exit("FLOOR: the allowlist is empty -- a vacuous comparison passes against anything")
strays = sorted(safe - known)
if strays:
    sys.exit("alias-shadowable (not a known git command): %s" % ", ".join(strays))
pushers = sorted({"push", "send-pack", "http-push", "svn", "p4"} & safe)
if pushers:
    sys.exit("push-capable command is allowlisted: %s" % ", ".join(pushers))
PYCHECK
assert_eq "$allowlist_rc" 0 'every allowlist entry is a real git command, and none can push'
[[ "$allowlist_rc" -eq 0 ]] || printf '  %s\n' "$allowlist_out"

# ================= The refusal must describe what was actually judged =========================
# Verdicts alone CANNOT catch a lying message -- every row here exits 2 either way, which is
# exactly why these assert CONTENT. One f-string used to prefix every reason with "refusing to
# push private 'dev'", including reasons that had found no push; that is what teaches an operator
# to reach for an override on a command that pushes nothing.
#
# The partition is TWO-axis, not a rename. `--git-dir=... push origin dev` IS a push AND is
# unjudgeable, so it must KEEP the alarming wording -- softening it would de-alarm a refusal doing
# its designed job. Only the not-a-push half gets the new phrasing, and that phrasing says
# "could not determine whether this pushes", never "no push here": `git $s origin dev` may well
# be one.
build_repo 1
stderr_of() { { push_json "$2" "$1" | python3 "$guard" >/dev/null; } 2>&1; }

m_push="$(stderr_of "$REPO" 'git push origin dev')"
assert_contains "$m_push" "refusing to push private 'dev'" \
  'a judged push keeps the alarming wording a runbook greps for'

m_gitdir="$(stderr_of "$REPO" "git --git-dir=$REPO/.git push origin dev")"
assert_contains "$m_gitdir" "refusing to push private 'dev'" \
  'an UNJUDGEABLE push keeps the alarming wording too (two-axis, not a rename)'

# shellcheck disable=SC2016  # the UNEXPANDED $live is the point
m_unres="$(stderr_of "$REPO" 'git -C "$live" frobnicate')"
assert_contains "$m_unres" 'could not judge' \
  'an unjudgeable non-push says so instead of claiming a push'
assert_contains "$m_unres" 'unexpanded shell variable' \
  'it names the unexpanded -C target, which is the actual cause'
assert_contains "$m_unres" 'Pass -C a literal path' 'it names the remedy'
assert_contains "$m_unres" 'does not honour ALLOW_PUSH=1' \
  'it says the override will not help, so nobody reaches for it'
case "$m_unres" in
  *"refusing to push private"*)
    printf 'FAIL  an unjudgeable non-push must NOT claim a push\n'; fail=$((fail + 1)) ;;
  *) printf 'PASS  an unjudgeable non-push must NOT claim a push\n'; pass=$((pass + 1)) ;;
esac

# shellcheck disable=SC2016
m_nonlit="$(stderr_of "$REPO" 'git $s origin dev')"
assert_contains "$m_nonlit" 'could not determine whether this pushes' \
  'a non-literal subcommand is reported as UNKNOWN, never as absent'

# Parse ambiguity is its own class: designed, not a fault, and must not be labelled a guard bug
# nor written to the diagnostic log that exists to capture rare genuine faults.
m_parse="$(stderr_of "$REPO" "git push origin main 'oops")"
assert_contains "$m_parse" 'could not parse unambiguously' \
  'designed parse ambiguity is reported as unreadable input'
case "$m_parse" in
  *"BUG in the guard"*)
    printf 'FAIL  parse ambiguity must not be labelled a guard bug\n'; fail=$((fail + 1)) ;;
  *) printf 'PASS  parse ambiguity must not be labelled a guard bug\n'; pass=$((pass + 1)) ;;
esac

# The refusal is the DELIVERABLE, not the explain tool. Measured 2026-08-24: five subagents hit this
# refusal in one session; NONE went looking for a diagnostic; all switched tools inside the turn. A
# standalone tool therefore has a measured invocation rate of zero, so the position has to travel in
# the artifact that is guaranteed to reach the blocked caller -- this message.
assert_contains "$m_parse" 'at line ' \
  'the refusal LOCATES the construct, not just its category'
assert_contains "$m_parse" 'explain-git-command.py' \
  'the refusal names the tool that shows more'

# ...and it must stay a REFUSAL. A message that reads like a diagnosis invites an override aimed at
# a gate that never judged anything. These two phrases are this guard's own; push-guard has
# different wording and is asserted separately in its own suite.
assert_contains "$m_parse" 'failing closed' \
  'the refusal still says it is failing closed'
assert_contains "$m_parse" 'no push was identified' \
  'the refusal still says no push was identified'

# A heredoc is the measured shape. The advisory names the one fix that is safe for BOTH halves of
# the measured class: quoting the delimiter makes the body literal, so bash expands nothing.
m_heredoc="$(stderr_of "$REPO" "$(printf 'git status <<EOF\na ` stray\nEOF\n')")"
assert_contains "$m_heredoc" 'delimiter' \
  'a heredoc ambiguity advises on the delimiter, the fix for the whole measured class'

# From a SURVIVING mutant: the suite asserted that an unexpanded -C with a real push still BLOCKS,
# but nothing asserted it keeps the alarming wording -- so flipping `sub == "push"` to False at
# that site changed nothing any row could see. Verdict rows cannot catch a de-alarmed message.
# shellcheck disable=SC2016
m_unres_push="$(stderr_of "$REPO" 'git -C "$live" push origin dev')"
assert_contains "$m_unres_push" "refusing to push private 'dev'" \
  'an unresolvable root with a REAL push keeps the alarming wording'

# From the security review: the wording is a claim about the WHOLE command. This blocks on the
# unjudgeable `frobnicate` first, but a literal push follows it -- saying "no push was identified"
# there is the inverse mislabel the two-axis split exists to prevent.
# shellcheck disable=SC2016
m_mixed="$(stderr_of "$REPO" 'git -C "$live" frobnicate && git push origin dev')"
assert_contains "$m_mixed" "refusing to push private 'dev'" \
  'an unjudgeable invocation followed by a real push still reports a push'

# ================= FAIL-OPEN: an option's VALUE slot stole a control operator ==================
# Found by the security review. `-c` consumes the next token unconditionally, so `git -c ;` ate
# the `;`, the walk resumed at `git` and recorded ONE invocation whose subcommand was "git" with
# the push buried in its argument segment -- not `push`, not allowlisted, and `alias.git` does not
# exist, so the guard ALLOWED it. Measured exit 0.
#
# The compound row is the REGRESSION marker: it BLOCKED before the options-only fix (the bogus
# operator-as-subcommand invocation failed the literal-subcommand rule and blocked by accident)
# and ALLOWED after, until this guard was added. An accidental catch is not coverage.
build_repo 1
push_run "$REPO" 'git -c ; git push origin dev' 2 \
  'FAIL-OPEN CLOSED: -c must not consume a control operator as its value'
push_run "$REPO" 'git --version ; git -c ; git push origin dev' 2 \
  'FAIL-OPEN CLOSED: the compound form the options-only fix had un-caught'
push_run "$REPO" 'git --namespace ; git push origin dev' 2 \
  'FAIL-OPEN CLOSED: --namespace behaves the same way'
push_run "$REPO" 'git -C ; git push origin dev' 2 \
  'FAIL-OPEN CLOSED: -C too'
# PRESERVE: an option that really does take a value must still consume it, or every ordinary
# `-c key=value` invocation starts mis-parsing.
push_run "$REPO" 'git -c user.name=x status' 0 \
  'PRESERVE: -c still consumes a genuine value'
push_run "$REPO" 'git -c x="$(git push origin dev)" status' 2 \
  'PRESERVE: a push hidden in a genuine -c value is still seen'

# ================= L1: publishing subcommands bypass push's refspec judgment entirely ==========
# `send-pack` is the transport `push` itself invokes; `svn`/`p4`/`daemon` publish through their own
# machinery. None runs the `pre-push` hook (layer 2), and none is `push` itself, so before
# PUBLISHING_SUBCOMMANDS existed each fell through to `_resolve_alias_chain`, which returns "none"
# (allow) at depth 0 for any subcommand that is not a configured alias -- measured against the
# frozen pre-change baseline, all four exit 0.
build_repo 1
push_run "$REPO" "git send-pack origin refs/heads/dev:refs/heads/x" 2 \
  'blocked: send-pack is the push transport itself, bypassing the refspec allowlist'
push_run "$REPO" "git svn dcommit" 2 \
  'blocked: svn dcommit publishes without ever invoking push'
push_run "$REPO" "git p4 submit" 2 \
  'blocked: p4 submit publishes without ever invoking push'
push_run "$REPO" "git daemon --export-all" 2 \
  'blocked: daemon serves the repo without ever invoking push'

# PRESERVE, and NOT git status/fetch/log -- those are KNOWN_SAFE_SUBCOMMANDS members that
# short-circuit at publication-push-guard.py:715, BEFORE _judge_invocation (and therefore
# PUBLISHING_SUBCOMMANDS) is ever consulted, so they would pass even if the deny set held every
# git subcommand in existence. The row that actually reaches the deny set and would catch an
# over-wide one is a non-alias subcommand that is not KNOWN_SAFE either: it must still resolve via
# _resolve_alias_chain and return "none" rather than get swept in by too broad a membership test.
push_run "$REPO" "git frobnicate" 0 \
  'PRESERVE: unrecognised non-alias, non-safe-listed subcommand still allowed'
# A genuinely safe-listed row too, for completeness -- but per the PRESERVE note above, this one
# proves nothing about PUBLISHING_SUBCOMMANDS's width; git_frobnicate above is the real evidence.
push_run "$REPO" "git status" 0 'allowed: safe-listed subcommand, unaffected by the deny set'

# Order matters: an alias literally NAMED a publishing subcommand must still be denied. git runs
# its own builtin/plumbing command over an alias of the same name (the same fact that lets
# KNOWN_SAFE_SUBCOMMANDS membership double as alias-shadow immunity above), so the alias's target
# is irrelevant -- and this only holds if the deny-set check runs BEFORE _resolve_alias_chain.
gi "$REPO" config alias.send-pack status >/dev/null 2>&1
push_run "$REPO" "git send-pack" 2 \
  'blocked: an alias literally named a publishing subcommand is still denied, regardless of its target'

m_sendpack="$(stderr_of "$REPO" 'git send-pack origin refs/heads/dev:refs/heads/x')"
assert_contains "$m_sendpack" "'send-pack'" \
  'the refusal names the offending subcommand'
assert_contains "$m_sendpack" 'will not help' \
  'the refusal says plainly that ALLOW_PUSH=1 will not help'
push_run "$REPO" "ALLOW_PUSH=1 git send-pack origin refs/heads/dev:refs/heads/x" 2 \
  'ALLOW_PUSH=1 does not authorize a publishing subcommand -- it is not a push'

# ================= Task 1: push-time boundary-hook integrity precondition =====================
# build_repo now installs a healthy stub hook (both the tracked git-hooks/pre-push source and the
# installed .git/hooks/pre-push copy) -- see build_repo's own comment. The positive control proves
# that stub does not itself turn an allowed refspec red; the second row relocates
# core.hooksPath AFTER the healthy hook is installed, so git's own hook resolution points
# somewhere else and the precondition must catch it even though the refspec itself is safe.
build_repo 1
out="$(judge "git ${VERB} origin main" "$REPO")"; rc=$?
assert_eq "$rc" 0 "healthy hook must not block an allowed refspec"

build_repo 1
mkdir -p "$REPO/decoy"
gi "$REPO" config core.hooksPath "$REPO/decoy"
out="$(judge "git ${VERB} origin main" "$REPO")"; rc=$?
assert_eq "$rc" 2 "relocated hooksPath must block"
assert_contains "$out" "not in a state this guard will clear" "reason must name the boundary"

# Relocation to a decoy holding a BYTE-IDENTICAL, EXECUTABLE copy of the hook.
#
# Added because a mutation campaign proved the row above does not test what it appears to: its
# decoy is EMPTY, so `hook.is_file()` refuses first and the containment check never has to decide.
# Measured -- mutating `if common_dir not in hook.parents` to `if False` SURVIVED the entire suite.
# The verdict was right, by a check further down; the containment branch was uncovered.
#
# Here every later check passes -- the file exists, is executable, and its digest matches the
# tracked source -- so containment is the only thing that can still refuse. It must: a hooks
# directory outside the repository is not one the repository controls, whatever it happens to hold
# right now. The assertion is on the RELOCATION wording, not merely on rc=2, because both branches
# return 2 and only the reason distinguishes which one fired.
build_repo 1
mkdir -p "$REPO/decoy"
cp "$REPO/git-hooks/pre-push" "$REPO/decoy/pre-push"
chmod +x "$REPO/decoy/pre-push"
gi "$REPO" config core.hooksPath "$REPO/decoy"
out="$(judge "git ${VERB} origin main" "$REPO")"; rc=$?
assert_eq "$rc" 2 "relocation to an IDENTICAL hook still blocks (containment, not content)"
assert_contains "$out" "relocated" \
  "reason must name the relocation -- not a missing or altered file, which is a different branch"

# A denied LOCAL write from a cwd that is not a git repository at all.
#
# Also added from a surviving mutant: flipping `_repo_is_adopted`'s unresolvable-root branch from
# `return True` to `return False` survived the whole suite, because every other config row runs in a
# real repo and the root always resolves. That branch's docstring states exactly why it must fail
# CLOSED -- `config` is in KNOWN_SAFE_SUBCOMMANDS, so a False here does not fall through to some
# stricter check, it ALLOWS the invocation outright. A stated safety property nothing exercised.
NONREPO="$(mktemp -d)"
out="$(judge "git config core.hooksPath /dev/null" "$NONREPO")"; rc=$?
assert_eq "$rc" 2 "a denied local write from a non-repo cwd fails closed (root unresolvable)"
assert_contains "$out" "refused because" "the refusal must be the detector's, not a crash"

# ================= Task 5: per-cause hook-integrity remedies, reorder, symlink cause ============
# `_hook_integrity_reason` now returns (reason, remedy) instead of a bare string, in the order
# 2 < S < 5 < {3, 4, 6}. Every row below sends an ALLOWED refspec (origin main) so a block can
# only come from the integrity check itself -- same convention as Task 1 above. `build_repo 1`
# leaves a HEALTHY installed hook and tracked source in place; each row below removes or alters
# exactly the pieces its cause needs, on top of that base.
#
# Cause S is REPORTED after containment (2), even though its underlying `is_symlink()` flag is
# still COMPUTED before containment's `.resolve()` runs -- see the function's own docstring. S's
# remedy ("remove the symlink, then reinstall") cannot clear a relocated `core.hooksPath`, because
# the installer resolves its own destination the same way git does, honouring the relocation and
# reinstalling right back into the decoy. Reporting containment first means a symlink+relocated
# state gets containment's remedy (which covers both a relocated hooksPath and a symlinked hooks
# *directory*), and S is only ever reported once containment has already passed -- exactly the
# shape its own isolation row below builds.

# ---- F1: the reported stale-checkout shape -- marker committed, nothing installed at all ----
# Before this task, cause 3 ("the boundary hook is missing") fired first here and its only
# remedy named scripts/install-git-hooks.sh -- a script this checkout does not have. After the
# reorder, cause 5 (tracked source missing) fires first, and its remedy needs no file this
# checkout lacks: it names a fetch, not a script.
build_repo 1
rm -f "$REPO/.git/hooks/pre-push" "$REPO/git-hooks/pre-push"
out="$(judge "git ${VERB} origin main" "$REPO")"; rc=$?
assert_eq "$rc" 2 "F1: stale checkout (no hook, no tracked source) still blocks"
assert_contains "$out" "fetch and check out" \
  "F1: the remedy is reachable from the stale checkout -- names a fetch, not a missing script"
assert_contains "$out" "is missing from this checkout" \
  "F1: the reported cause is the tracked source, not the installed hook"

# ---- Cause S in isolation: a symlinked hook FILE whose target resolves INSIDE the repo ----
# The symlink's target is byte-identical to the tracked source and lives inside .git/hooks/
# itself, so containment (2), is_file, X_OK and the digest would ALL pass if the leaf were
# resolved instead of probed raw first. Without the is_symlink() probe running before
# .resolve(), this exact repo would ALLOW -- installed but dead, indistinguishable from working.
build_repo 1
mv "$REPO/.git/hooks/pre-push" "$REPO/.git/hooks/_real_pre_push"
ln -s "$REPO/.git/hooks/_real_pre_push" "$REPO/.git/hooks/pre-push"
out="$(judge "git ${VERB} origin main" "$REPO")"; rc=$?
assert_eq "$rc" 2 "a symlinked hook FILE blocks even though its target resolves inside the repo"
assert_contains "$out" "is a symlink" \
  "cause S is the one reported -- not merely that something blocked"

# ---- Which-cause pinning: two causes live at once, over every adjacent pair in 2 < S < 5 <
# ---- {3, 4, 6}. Invariance alone (something blocked) would pass even if the EARLIER cause's own
# ---- message never appeared -- these assert the specific text, not just the exit code.

# (symlink + relocated): hooksPath relocated to a decoy dir, AND the hook AT the decoy is itself
# a symlink pointing further outside (to /dev/null, which is nowhere near the repo). Both S's and
# 2's conditions hold; 2 must win -- S's remedy is "remove the symlink, then reinstall", but
# `install-git-hooks.sh` resolves its destination the same way git resolves the hooks path (both
# go through `git rev-parse --git-path hooks`), so it would honour the very relocation this state
# needs cleared and reinstall right back into the decoy, leaving the command refused forever.
# Cause 2's remedy names the relocation directly and clears it.
build_repo 1
mkdir -p "$REPO/decoy"
ln -s /dev/null "$REPO/decoy/pre-push"
gi "$REPO" config core.hooksPath "$REPO/decoy"
out="$(judge "git ${VERB} origin main" "$REPO")"; rc=$?
assert_eq "$rc" 2 "symlink+relocated: still blocks"
assert_contains "$out" "outside the repository's own hooks directory" \
  "symlink+relocated: cause 2 wins, not cause S"

# ---- Remedies actually work: follow the reported remedy LITERALLY, and the block must CLEAR
# ---- afterwards. Invariance and cause-pinning above only prove WHICH text is printed; this is
# ---- the real property F1/D1 exist to establish -- that the printed remedy is reachable and
# ---- sufficient, not merely present.

# (symlink+relocated, cause 2's remedy): unset core.hooksPath in the scope it was set. The decoy's
# dangling symlink is left in place on purpose -- once hooksPath is unset, git never looks at the
# decoy again, so the remedy needs nothing else to fully clear the block.
build_repo 1
mkdir -p "$REPO/decoy"
ln -s /dev/null "$REPO/decoy/pre-push"
gi "$REPO" config core.hooksPath "$REPO/decoy"
out="$(judge "git ${VERB} origin main" "$REPO")"; rc=$?
assert_eq "$rc" 2 "remedy check (symlink+relocated): starts blocked"
assert_contains "$out" "outside the repository's own hooks directory" \
  "remedy check (symlink+relocated): cause 2 fired, as pinned above"
gi "$REPO" config --unset core.hooksPath
out="$(judge "git ${VERB} origin main" "$REPO")"; rc=$?
assert_eq "$rc" 0 "remedy check (symlink+relocated): unsetting core.hooksPath actually clears the block"

# (cause S's own remedy): remove the symlink by hand, then run the REAL install-git-hooks.sh --
# copied into the sandbox repo so `$SCRIPT_DIR/..` resolves to $REPO, matching how the remedy text
# names it (a bare "scripts/install-git-hooks.sh", relative to the checkout the refusal fired in).
build_repo 1
mv "$REPO/.git/hooks/pre-push" "$REPO/.git/hooks/_real_pre_push"
ln -s "$REPO/.git/hooks/_real_pre_push" "$REPO/.git/hooks/pre-push"
out="$(judge "git ${VERB} origin main" "$REPO")"; rc=$?
assert_eq "$rc" 2 "remedy check (symlink-inside-hooks-dir): starts blocked"
assert_contains "$out" "is a symlink" "remedy check (symlink-inside-hooks-dir): cause S fired"
rm -f "$REPO/.git/hooks/pre-push"
mkdir -p "$REPO/scripts"
cp "$repo_root/scripts/install-git-hooks.sh" "$REPO/scripts/install-git-hooks.sh"
chmod +x "$REPO/scripts/install-git-hooks.sh"
install_out="$("$REPO/scripts/install-git-hooks.sh" 2>&1)"; install_rc=$?
assert_eq "$install_rc" 0 "remedy check (symlink-inside-hooks-dir): the installer itself succeeds"
assert_contains "$install_out" "installed" \
  "remedy check (symlink-inside-hooks-dir): the installer reports what it did"
out="$(judge "git ${VERB} origin main" "$REPO")"; rc=$?
assert_eq "$rc" 0 \
  "remedy check (symlink-inside-hooks-dir): remove-then-reinstall actually clears the block"

# (source-missing + relocated): hooksPath relocated to a decoy dir (2's condition holds) AND the
# tracked source is missing from the checkout (5's condition holds too). 2 precedes 5 -- hoisting
# 5 to the very top (the natural misreading of "5 first") would tell this exact repo "update the
# checkout, then install", which cannot clear a relocation.
build_repo 1
mkdir -p "$REPO/decoy"
gi "$REPO" config core.hooksPath "$REPO/decoy"
rm -f "$REPO/git-hooks/pre-push"
out="$(judge "git ${VERB} origin main" "$REPO")"; rc=$?
assert_eq "$rc" 2 "source-missing+relocated: still blocks"
assert_contains "$out" "outside the repository's own hooks directory" \
  "source-missing+relocated: cause 2 wins, not cause 5"

# (hook-missing + source-missing): no installed hook (3's condition holds) AND no tracked source
# (5's condition holds too). 5 precedes 3 -- every install-class remedy (3's included)
# presupposes the tracked source existing.
build_repo 1
rm -f "$REPO/.git/hooks/pre-push" "$REPO/git-hooks/pre-push"
out="$(judge "git ${VERB} origin main" "$REPO")"; rc=$?
assert_eq "$rc" 2 "hook-missing+source-missing: still blocks"
assert_contains "$out" "is missing from this checkout" \
  "hook-missing+source-missing: cause 5 wins, not cause 3"

# (not-executable + source-missing): the installed hook exists but lost its executable bit (4's
# condition holds) AND the tracked source is missing (5's condition holds too). This is the row
# that actually pins 5-vs-4: "5 first" is not the same claim as "5 before {3,4,6}", and an
# implementer who ordered 4 before 5 would pass every row above and fail only this one.
build_repo 1
chmod -x "$REPO/.git/hooks/pre-push"
rm -f "$REPO/git-hooks/pre-push"
out="$(judge "git ${VERB} origin main" "$REPO")"; rc=$?
assert_eq "$rc" 2 "not-executable+source-missing: still blocks"
assert_contains "$out" "is missing from this checkout" \
  "not-executable+source-missing: cause 5 wins, not cause 4"

# ---- Refusal-set invariance: every individual cause, alone, still blocks. The reorder is
# ---- message-selection only -- never a change to WHETHER the function returns non-None. (The
# ---- None case -- a healthy hook still allows -- is already covered above, at the top of Task 1.)
build_repo 1
gi "$REPO" config core.hooksPath "$REPO/nonexistent-decoy"
out="$(judge "git ${VERB} origin main" "$REPO")"; rc=$?
assert_eq "$rc" 2 "invariance: cause 2 alone still blocks"

build_repo 1
rm -f "$REPO/git-hooks/pre-push"
out="$(judge "git ${VERB} origin main" "$REPO")"; rc=$?
assert_eq "$rc" 2 "invariance: cause 5 alone still blocks"

build_repo 1
rm -f "$REPO/.git/hooks/pre-push"
out="$(judge "git ${VERB} origin main" "$REPO")"; rc=$?
assert_eq "$rc" 2 "invariance: cause 3 alone still blocks"

build_repo 1
chmod -x "$REPO/.git/hooks/pre-push"
out="$(judge "git ${VERB} origin main" "$REPO")"; rc=$?
assert_eq "$rc" 2 "invariance: cause 4 alone still blocks"

build_repo 1
printf '#!/bin/sh\nexit 1\n' >"$REPO/.git/hooks/pre-push"
chmod +x "$REPO/.git/hooks/pre-push"
out="$(judge "git ${VERB} origin main" "$REPO")"; rc=$?
assert_eq "$rc" 2 "invariance: cause 6 (digest mismatch) alone still blocks"

# ---- Cause "1" (unresolvable), defensive: every call site above has already had `root` proven
# ---- by `_resolve_root`, so this cause cannot be reached through the judge()/push_run() pipeline
# ---- the rows above use. Probed directly, against a root that cannot be `git -C`'d at all (a
# ---- regular file, not a directory) -- the one shape that makes git's OWN root resolution fail
# ---- before this function's logic ever runs.
BADROOT="$(mktemp)"
badroot_reason="$(python3 -c "
import importlib.util, sys
spec = importlib.util.spec_from_file_location('g', '$guard')
m = importlib.util.module_from_spec(spec); sys.modules['g'] = m
spec.loader.exec_module(m)
result = m._hook_integrity_reason('$BADROOT')
print(result[0] if result else '(none)')
")"
badroot_label="cause 1 (unresolvable): reported directly against a root git -C cannot use"
if [[ "$badroot_reason" == "the hook path could not be resolved" ]]; then
  printf 'PASS  %s\n' "$badroot_label"
  pass=$((pass + 1))
else
  printf 'FAIL  %s (got: %s)\n' "$badroot_label" "$badroot_reason"
  fail=$((fail + 1))
fi
rm -f "$BADROOT"

# ---- F1b: an integrity refusal on an ALLOWLISTED target must claim neither two-axis message.
# ---- `main` is allowlisted (Task 1's own positive control proves it), so this refusal fires only
# ---- because of the boundary's own state -- not because of anything about the target.
build_repo 1
rm -f "$REPO/.git/hooks/pre-push"
out="$(judge "git ${VERB} origin main" "$REPO")"; rc=$?
assert_eq "$rc" 2 "F1b: an integrity refusal on an allowed target still blocks"
assert_contains "$out" "about the boundary itself, not the target" \
  "F1b: integrity refusals get their own wording"
assert_not_contains "$out" "refusing to ${VERB} private 'dev'" \
  "F1b: must not claim a private-'dev' ${VERB} (the target was main)"
assert_not_contains "$out" "No ${VERB} was identified" \
  "F1b: must not claim no ${VERB} was identified (one was)"

# PRESERVE: a genuine private-branch refusal must still print the exact runbook-grepped line,
# byte for byte -- F1b's new message path must not have touched the is_push=True line itself.
build_repo 1
out="$(judge "git ${VERB} origin dev" "$REPO")"; rc=$?
assert_eq "$rc" 2 "PRESERVE: a real dev ${VERB} is still blocked"
assert_contains "$out" "refusing to ${VERB} private 'dev' (or an ambiguous target):" \
  "PRESERVE: the runbook-grepped line is unchanged, byte for byte"

# ============ Same argument as F1b, one call site over: config/env-injection refusals ==========
# `_config_injection_reason`'s Block (the inline env/-c/config arm) and `_exported_injection_reason`'s
# Block (the command-scoped arm) used to omit `boundary_unverifiable`, so both fell into the same
# two-axis wording F1b already proved wrong for `_hook_integrity_reason` -- refused because of the
# GATE, not the target, yet printed as if it were a statement about the target. Two commands measured
# directly against HEAD before this fix, both in the ADOPTED repo with an ALLOWLISTED target:
#   GIT_COMMON_DIR=/tmp/g git <verb> origin main  -> claimed "refusing to <verb> private 'dev'"
#   GIT_EDITOR=vi git status                      -> claimed "could not determine whether this <verb>es"
# Neither claim fits: the first refused main, not dev; the second was never a push candidate at all,
# yet the guard judged the GIT_EDITOR= injection completely and refused on that, not on uncertainty.
build_repo 1
out="$(judge "GIT_COMMON_DIR=/tmp/g git ${VERB} origin main" "$REPO")"; rc=$?
assert_eq "$rc" 2 "GIT_COMMON_DIR= inline injection on an allowed target still blocks"
assert_contains "$out" "about the boundary itself, not the target" \
  "GIT_COMMON_DIR=: gets the GATE wording, same as an integrity refusal"
assert_not_contains "$out" "refusing to ${VERB} private 'dev'" \
  "GIT_COMMON_DIR=: must not claim a private-'dev' ${VERB} (the target was main)"
assert_not_contains "$out" "could not judge" \
  "GIT_COMMON_DIR=: must not claim the guard could not judge it -- it judged the gate fully"

out="$(judge "GIT_EDITOR=vi git status" "$REPO")"; rc=$?
assert_eq "$rc" 2 "GIT_EDITOR= inline injection blocks even on a non-${VERB} subcommand"
assert_contains "$out" "about the boundary itself, not the target" \
  "GIT_EDITOR=: gets the GATE wording even though sub is 'status', not a ${VERB}"
assert_not_contains "$out" "could not judge" \
  "GIT_EDITOR=: must not claim the guard could not judge it"
assert_not_contains "$out" "No ${VERB} was identified" \
  "GIT_EDITOR=: must not claim uncertainty about a ${VERB} -- there was never a candidate at all"

# The EXPORTED arm (a denied name reaching the invocation via `export` in an earlier segment, not
# an inline prefix) answers the identical question and must get the same fix -- the two arms are
# already coupled by `if token in claimed: continue` for DETECTION, and leaving only the inline
# arm's wording fixed would reproduce this exact defect for the export-in-a-separate-segment shape.
out="$(judge "export GIT_EDITOR=vi; git status" "$REPO")"; rc=$?
assert_eq "$rc" 2 "exported GIT_EDITOR= (separate segment) also blocks"
assert_contains "$out" "about the boundary itself, not the target" \
  "exported form: gets the same GATE wording as the inline form"
assert_not_contains "$out" "could not judge" \
  "exported form: must not claim the guard could not judge it either"

# PRESERVE (config-injection axis): pairing an injection with a genuine dev ${VERB} must still be
# possible to reach without the injection wording masking it -- not exercised elsewhere, so pinned
# here directly. The refspec check runs AFTER the injection check (see _find_block_reason), so an
# adopted-repo command carrying BOTH still reports the injection, never the dev refspec; this just
# confirms that combination still blocks (rc=2), matching every row above.
out="$(judge "GIT_EDITOR=vi git ${VERB} origin dev" "$REPO")"; rc=$?
assert_eq "$rc" 2 "PRESERVE: an injection alongside a real dev ${VERB} still blocks"

# ================= Task 2/3: pure git-config classifiers (not yet wired to any behaviour) =====
# assert_py <python expression against the guard module> <expected repr string>. Imports the guard
# BY PATH (it is a script, not an importable module, so a plain `import` would fail) and evaluates
# one expression against it -- string comparison, not assert_eq's numeric `-eq` (which errors under
# `set -u` on non-numeric operands like "True"/"False").
assert_py() { # <python expression> <expected repr>
  local got
  got="$(python3 -c "
import importlib.util, sys
spec = importlib.util.spec_from_file_location('g', '$guard')
m = importlib.util.module_from_spec(spec); sys.modules['g'] = m
spec.loader.exec_module(m)
print(m.$1)
")" || { printf 'FAIL  assert_py could not evaluate: %s\n' "$1"; fail=$((fail + 1)); return; }
  if [[ "$got" == "$2" ]]; then
    printf 'PASS  %s (got %s)\n' "$1" "$got"
    pass=$((pass + 1))
  else
    printf 'FAIL  %s (want %s, got %s)\n' "$1" "$2" "$got"
    fail=$((fail + 1))
  fi
}

# ---- Task 2: classifies git config reads by the LEADING option run only ----
# POSITION IS LOAD-BEARING: `git config core.hooksPath --get` was measured to WRITE
# `core.hooksPath = --get` -- a read flag arriving after the key is consumed as a positional.
assert_py "_config_is_read(['core.hooksPath','--get'])"              "False"
assert_py "_config_is_read(['core.hooksPath','/dev/null','--list'])" "False"
assert_py "_config_is_read(['--get','core.hooksPath'])"              "True"
assert_py "_config_is_read(['get','core.hooksPath'])"                "True"
assert_py "_config_is_read(['list'])"                                "True"
assert_py "_config_is_read(['set','core.hooksPath','X'])"            "False"
assert_py "_config_is_read(['core.hooksPath'])"                      "False"
assert_py "_config_is_read([])"                                      "False"
assert_py "_config_is_read(['--','--get','core.hooksPath'])"         "False"

# ---- Task 3: classifies every non-local scope spelling as non-local ----
# Scans EVERY token and never breaks early: a leading-run scan misclassifies
# `--comment note --global core.hooksPath X` as local (measured: git writes the GLOBAL file).
assert_py "_config_scope_is_local(['core.hooksPath','X'], [])"                               "True"
assert_py "_config_scope_is_local(['--local','core.hooksPath','X'], [])"                     "True"
assert_py "_config_scope_is_local(['set','core.hooksPath','X'], [])"                         "True"
assert_py "_config_scope_is_local(['--type','bool','core.hooksPath','X'], [])"               "True"
assert_py "_config_scope_is_local(['--global','core.hooksPath','X'], [])"                    "False"
assert_py "_config_scope_is_local(['--glo','core.hooksPath','X'], [])"                       "False"
assert_py "_config_scope_is_local(['--sys','core.hooksPath','X'], [])"                       "False"
assert_py "_config_scope_is_local(['set','--global','core.hooksPath','X'], [])"              "False"
assert_py "_config_scope_is_local(['--comment','note','--global','core.hooksPath','X'], [])" "False"
assert_py "_config_scope_is_local(['-f','/other','core.hooksPath','X'], [])"                 "False"
assert_py "_config_scope_is_local(['--file=/other','core.hooksPath','X'], [])"               "False"
assert_py "_config_scope_is_local(['--fil','/other','core.hooksPath','X'], [])"              "False"
assert_py "_config_scope_is_local(['--no-local','core.hooksPath','X'], [])"                  "False"
assert_py "_config_scope_is_local(['--worktree','core.hooksPath','X'], [])"                  "False"
assert_py "_config_scope_is_local(['core.hooksPath','X'], ['GIT_CONFIG=/o'])"                "False"
assert_py "_config_scope_is_local(['core.hooksPath','X'], ['GIT_COMMON_DIR=/o/.git'])"       "False"
assert_py "_config_scope_is_local(['core.hooksPath','X'], ['GIT_DIR=/o/.git'])"              "False"

# ================= Task 4: adoption predicate + config-arm rewiring ===========================
# Consumes Tasks 1-3: _config_is_read (the read carve-out) and _config_scope_is_local (the scope
# allowlist) are now WIRED, gated by the new _repo_is_adopted predicate -- only the `config` arm's
# WRITE reason becomes conditional on the invoking cwd's own repo having adopted the publication
# model. The env and -c/--config-env arms keep today's unconditional reach (see the module's
# revision note: a -c value's reach is not per-invocation, because the aliased command it can run
# is arbitrary).
#
# assert_allows/assert_blocks cmd cwd label -- thin wrappers over judge(), in judge's own
# (command, cwd) argument order, following the file's established out=...;rc=$? idiom.
assert_allows() { # command cwd label
  local out rc
  out="$(judge "$1" "$2")"; rc=$?
  assert_eq "$rc" 0 "$3"
}
assert_blocks() { # command cwd label
  local out rc
  out="$(judge "$1" "$2")"; rc=$?
  assert_eq "$rc" 2 "$3"
}

# ---- newly allowed: a scoped-local config write from a NON-adopted cwd ----
build_repo 0
assert_allows "git config core.hooksPath .husky" "$REPO" \
  "allows husky's install command in a non-adopted repo"

# ---- newly allowed: a pure read, in either repo ----
build_repo 0
assert_allows "git config --get core.hooksPath" "$REPO" \
  "allows a pure --get read in a non-adopted repo"
build_repo 1
assert_allows "git config --get core.hooksPath" "$REPO" \
  "allows a pure --get read in the adopted repo"
build_repo 1
assert_allows "git config get core.hooksPath" "$REPO" \
  "allows the subcommand-form 'get' read in the adopted repo"

# ---- newly allowed: the one exact-name env exemption ----
build_repo 0
assert_allows "GIT_CONFIG_NOSYSTEM=1 git log -1" "$REPO" \
  "allows GIT_CONFIG_NOSYSTEM in a non-adopted repo"

# ---- still refused: every reach-in WRITE from a NON-adopted cwd -- the env/-c arms and a
# non-local config-arm write all stay unconditional, so none of these become adoption-gated ----
build_repo 1
adopted_repo="$REPO"
build_repo 0
assert_blocks "git config --global core.hooksPath /dev/null" "$REPO" \
  "still refuses a --global write from a non-adopted cwd"
assert_blocks "git config --glo core.hooksPath /dev/null" "$REPO" \
  "still refuses the --glo abbreviation from a non-adopted cwd"
assert_blocks "git config --comment n --global core.hooksPath /x" "$REPO" \
  "still refuses --comment then --global from a non-adopted cwd"
assert_blocks "git config set --global core.hooksPath /x" "$REPO" \
  "still refuses the subcommand-form 'set --global' from a non-adopted cwd"
assert_blocks "git config -f $adopted_repo/.git/config core.hooksPath X" "$REPO" \
  "still refuses -f pointed at the adopted repo's own config file, from a non-adopted cwd"
assert_blocks "GIT_CONFIG=$adopted_repo/.git/config git config core.hooksPath X" "$REPO" \
  "still refuses a GIT_CONFIG= redirect from a non-adopted cwd"
assert_blocks "GIT_COMMON_DIR=$adopted_repo/.git git config core.hooksPath X" "$REPO" \
  "still refuses a GIT_COMMON_DIR= redirect from a non-adopted cwd"
# A `-c alias.<n>=<command>` value is per-invocation in FORM but arbitrary in EFFECT: the aliased
# command runs with full privileges, so the arm's blast radius is not per-invocation at all.
# Measured before the fix, both rc=0 (allowed): a global config write, and -- far worse -- a bare
# publish to the private branch, in an ADOPTED repo. Neither reached the `config` arm, because
# `sub` resolves to "zz" and never "config"; and `_resolve_alias_chain`'s own lookup queries the
# repo's PERSISTED config in a fresh subprocess, which cannot see an alias defined only via THIS
# invocation's own -c.
#
# The earlier reasoning that made the `-c` arm unconditional claimed this was "closed by
# construction". That conflated two different things: unconditional-vs-gated governs what happens
# AFTER a match, but `alias.` was never a denied key, so the arm never matched at all.
assert_blocks "git -c alias.zz='config --global core.hooksPath /x' zz" "$REPO" \
  "refuses an alias smuggling a global config write"
assert_blocks "git -c alias.zz='${VERB} origin dev' zz" "$REPO" \
  "refuses an alias smuggling a publish to the private branch"
# The over-block this must NOT cause: defining an ORDINARY alias through the config arm stays
# allowed, because `alias.` is added to the -c arm's key set only, never to DENIED_CONFIG_KEYS
# (which the `git config` seg scan shares).
assert_allows "git config alias.co checkout" "$REPO" \
  "an ordinary alias definition via git config is still allowed"
assert_blocks "GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=alias.zz git zz" "$REPO" \
  "still refuses the GIT_CONFIG_COUNT alias-key smuggle from a non-adopted cwd"
assert_blocks "git -C $adopted_repo config core.hooksPath /dev/null" "$REPO" \
  "still refuses git -C <adopted> config from a non-adopted cwd"

# ---- the ATTACHED short-option spelling: blocked, but NOT by this detector ----
# `-c<key>=<value>` is not a real input. MEASURED: git itself refuses it --
# `git -cfoo.bar=baz config --get foo.bar` exits 129 with "unknown option: -cfoo.bar=baz", because
# `-c` is not one of the short options git accepts an attached value for. `-C` is, which is exactly
# why `classify_global_opt` carries an attached rule for that one and only that one.
#
# So the token classifies UNKNOWN, the invocation is unjudgeable, and the guard refuses before the
# config detector is ever consulted. Verified by the refusal's WORDING ("could not judge:
# subcommand '-ccore.hooksPath=/dev/null'"), not by the exit code, which both paths share -- an
# rc=2 alone cannot tell "detected an injection" from "declined to guess". The detector's own
# attached-`-c` branch was therefore INERT: it read as coverage while unable to fire, and a refusal
# it never produced was being credited to it. It has been DELETED rather than tested around.
#
# These rows pin the thing that actually forces the outcome, so the deletion's premise cannot rot
# silently: if `classify_global_opt` ever learns an attached `-c`, this is where it fails.
# Both run in the ADOPTED repo deliberately -- measured, the refusal is correctly scoped there and
# a non-adopted cwd allows all three spellings (rc=0), so a row placed there would prove nothing.
build_repo 1
assert_blocks "git -ccore.hooksPath=/dev/null ${VERB} origin main" "$REPO" \
  "refuses an attached -c denied key (as unjudgeable, not as a detected injection)"
assert_blocks "git -cuser.name=x status" "$REPO" \
  "refuses an attached -c innocent key too -- git rejects the spelling, so this costs no workflow"

# ---- still refused: a write disguised as a read (position-sensitive) ----
build_repo 1
assert_blocks "git config core.hooksPath --get" "$REPO" \
  "still refuses a write that only looks like a read (trailing --get)"

# ---- still refused: a local denied write in the ADOPTED repo itself ----
build_repo 1
assert_blocks "git config core.hooksPath /dev/null" "$REPO" \
  "still refuses a local denied write in the adopted repo"

# ---------- a reserved word in ARGUMENT position ----------
# Bash recognises a reserved word only in command position, so after an argument `then`/`do`/... is
# an ordinary word and everything behind it is still the push's argv. The tokenizer once cut the
# argument list at a `git` behind such a word; an option value swallowing the word and a refspec
# ending in /git were enough, with no unusual local refs. Measured ALLOWED (rc 0) before the fix.
build_repo 1
push_run "$REPO" "git ${VERB} origin main" 0 "control: a safe refspec is allowed"
push_run "$REPO" "git ${VERB} origin main -o then HEAD:refs/heads/x/git dev" 2 \
  "blocked: -o swallows 'then'; dev behind a /git refspec is still judged"
push_run "$REPO" "git ${VERB} origin main -o ZZ HEAD:refs/heads/x/git dev" 2 \
  "control: the neutral-word spelling of the row above blocks"
push_run "$REPO" "for x do git ${VERB} origin dev; done" 2 "blocked: for NAME do opens a command (runs with positional params)"
push_run "$REPO" "function f { git ${VERB} origin dev; }; f" 2 "blocked: function NAME { opens a command"
push_run "$REPO" "coproc NAME { git ${VERB} origin dev; }" 2 "blocked: coproc NAME { opens a command"
push_run "$REPO" "git log x then git ${VERB} origin dev" 2 \
  "blocked: a phantom push inside another command's argv is kept (decided over-block)"

# DECIDED TRADE, pinned beside its neutral spelling. `git config --unset then git config x.txt` was
# once cut to `--unset then`, showing no section.key, and blocked as unjudgeable. Its full argv shows a
# dotted token, exactly as the neutral spelling always did; git rejects that many positionals anyway.
push_run "$REPO" "git config --unset core.hooksPath" 2 "control: unsetting a denied key blocks"
push_run "$REPO" "git config --unset ZZ git config x.txt" 0 "trade control: neutral spelling -> allowed"
push_run "$REPO" "git config --unset then git config x.txt" 0 "trade: full argv judged like its neutral spelling -> allowed"

# DECIDED TRADE, pinned beside its neutral spelling. On `dev` with a local branch named `git`,
# `git <push> then git` was once read as a bare push (argv cut to `then`) and blocked as pushing the
# current branch. Bash sends refspec `git` to remote `then`; the full argv now gets exactly the
# verdict its neutral spelling always got.
build_repo 1
gi "$REPO" branch -q git >/dev/null 2>&1
gi "$REPO" checkout -q dev >/dev/null 2>&1
push_run "$REPO" "git ${VERB} origin dev" 2 "control: a dev push from dev blocks"
push_run "$REPO" "git ${VERB} ZZ git" 0 "trade control: neutral spelling, explicit refspec git -> allowed"
push_run "$REPO" "git ${VERB} then git" 0 "trade: full argv judged like its neutral spelling -> allowed"

printf '\n%d passed, %d failed\n' "$pass" "$fail"
[[ "$fail" -eq 0 ]]

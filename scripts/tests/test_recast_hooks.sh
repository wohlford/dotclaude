#!/usr/bin/env bash
set -uo pipefail

# Script: test_recast_hooks.sh
# Purpose: Regression tests for the recast test-trigger hooks — the edit-time scoped
#          feedback hook (recast-test.sh) and the commit-time gate
#          (recast-commit-gate.py): detection, dispatch, the -a/-am/pathspec/message
#          command forms, unborn HEAD, fail-safe paths, the real-repo source->test naming map,
#          and that non-recast skills are NOT gated (decomposition pin).
#          Uses FAKE millisecond suites — never the real recast suite.
# Usage:   bash scripts/tests/test_recast_hooks.sh

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$here/../.." && pwd)"
edit_hook="$repo_root/scripts/recast-test.sh"
gate_hook="$repo_root/scripts/recast-commit-gate.py"

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

# ---------- JSON builders (via python to dodge shell-quoting bugs) ----------
edit_json() { python3 -c 'import json,sys;print(json.dumps({"tool_input":{"file_path":sys.argv[1]}}))' "$1"; }
gate_json() { python3 -c 'import json,sys;print(json.dumps({"tool_input":{"command":sys.argv[1]},"cwd":sys.argv[2]}))' "$1" "$2"; }

write_fake_test() { # repo sub testfile result(pass|fail)
  mkdir -p "$1/skills/$2/tests"
  if [[ "$4" == fail ]]; then
    printf 'def test_x():\n    assert False\n' >"$1/skills/$2/tests/$3"
  else
    printf 'def test_x():\n    assert True\n' >"$1/skills/$2/tests/$3"
  fi
}

# build_repo <recast_result> [--no-head]  → sets global REPO
build_repo() {
  REPO="$(mktemp -d)"
  gi "$REPO" init -q >/dev/null 2>&1
  mkdir -p "$REPO/skills/recast"
  printf '#!/usr/bin/env bash\necho recast-state\n' >"$REPO/skills/recast/recast-state.sh"
  printf 'hello\n' >"$REPO/README.md"
  write_fake_test "$REPO" recast test_recast_state.py "$1"
  if [[ "${2:-}" != --no-head ]]; then
    gi "$REPO" add -A >/dev/null 2>&1
    gi "$REPO" commit -q -m init >/dev/null 2>&1
  fi
}

edit_run() { # repo relpath want label [pathprefix-env]
  local got=0
  edit_json "$1/$2" | bash "$edit_hook" >/dev/null 2>&1 || got=$?
  assert_eq "$got" "$3" "$4"
}

gate_run() { # repo command want label
  local got=0
  gate_json "$2" "$1" | python3 "$gate_hook" >/dev/null 2>&1 || got=$?
  assert_eq "$got" "$3" "$4"
}

# ================= Edit-time hook =================
build_repo pass
edit_run "$REPO" skills/recast/recast-state.sh 0 "edit-time: recast source, passing test -> 0"
edit_run "$REPO" skills/recast/tests/test_recast_state.py 0 "edit-time: editing the test file -> runs it -> 0"
edit_run "$REPO" skills/recast/recast_helpers.py 0 "edit-time: helper .py deferred to commit gate -> 0"
edit_run "$REPO" README.md 0 "edit-time: irrelevant path -> 0"

build_repo fail
edit_run "$REPO" skills/recast/recast-state.sh 2 "edit-time: recast source, failing test -> 2"

# edit-time availability guard: python3 stubbed to fail -> exit 0 (never falsely block)
build_repo fail
stub="$sandbox/stub"
mkdir -p "$stub"
printf '#!/bin/sh\nexit 1\n' >"$stub/python3"
chmod +x "$stub/python3"
got=0
edit_json "$REPO/skills/recast/recast-state.sh" | PATH="$stub:$PATH" bash "$edit_hook" >/dev/null 2>&1 || got=$?
assert_eq "$got" 0 "edit-time: no pytest -> 0 (fail-safe)"

# ================= Commit gate =================
build_repo fail
printf 'changed\n' >>"$REPO/skills/recast/recast-state.sh"
gi "$REPO" add skills/recast/recast-state.sh >/dev/null 2>&1
gate_run "$REPO" "git commit -m x" 2 "gate: staged recast + failing suite -> 2"

build_repo pass
printf 'changed\n' >>"$REPO/skills/recast/recast-state.sh"
gi "$REPO" add skills/recast/recast-state.sh >/dev/null 2>&1
gate_run "$REPO" "git commit -m x" 0 "gate: staged recast + passing suite -> 0"

build_repo fail
gate_run "$REPO" "git log --format='%h commit'" 0 "gate: non-commit git command -> 0"

build_repo fail
printf 'note\n' >>"$REPO/README.md"
gi "$REPO" add README.md >/dev/null 2>&1
gate_run "$REPO" "git commit -m x" 0 "gate: commit touches no recast -> 0"

# -am cluster (BLOCKER regression): unstaged tracked recast change must be picked up
build_repo fail
printf 'changed\n' >>"$REPO/skills/recast/recast-state.sh"   # modified, NOT staged
gate_run "$REPO" "git commit -am msg" 2 "gate: -am picks up unstaged tracked recast -> 2"
gate_run "$REPO" "git commit -a -m msg" 2 "gate: -a (separate) picks up unstaged tracked recast -> 2"
gate_run "$REPO" "git commit -m x" 0 "gate: plain commit ignores unstaged recast -> 0"

# pathspec
build_repo fail
printf 'changed\n' >>"$REPO/skills/recast/recast-state.sh"   # unstaged
gate_run "$REPO" "git commit -- skills/recast/recast-state.sh" 2 "gate: pathspec commit of recast -> 2"

# commit-level pathspec of an UNRELATED file must NOT false-block on a coincidentally-dirty recast
build_repo fail
printf 'changed\n' >>"$REPO/skills/recast/recast-state.sh"   # dirty recast, unstaged, NOT committed
printf 'note\n' >>"$REPO/README.md"
gate_run "$REPO" "git commit -m x -- README.md" 0 "gate: commit pathspec <unrelated> ignores dirty recast -> 0"
gate_run "$REPO" "git commit -m x README.md" 0 "gate: commit bare-pathspec <unrelated> ignores dirty recast -> 0"

# -m message that merely MENTIONS a path must not be treated as a pathspec
build_repo fail
printf 'changed\n' >>"$REPO/skills/recast/recast-state.sh"   # unstaged
gate_run "$REPO" "git commit -m 'fix skills/recast/recast-state.sh'" 0 "gate: path-like -m message is not a pathspec -> 0"

# Compound 'git add … && commit' — PreToolUse fires BEFORE the add, so the gate must fold in what the
# add WILL stage. These deliberately do NOT pre-stage (the BLOCKER regression the review caught).
build_repo fail
printf 'changed\n' >>"$REPO/skills/recast/recast-state.sh"   # broken, UNSTAGED
gate_run "$REPO" "git add -A && git commit -m x" 2 "gate: 'add -A && commit' folds unstaged recast -> 2"
gate_run "$REPO" "git add skills/recast/recast-state.sh && git commit -m x" 2 "gate: scoped 'add <recast> && commit' -> 2"

# A brand-new UNTRACKED recast source that 'add -A' will stage (ls-files --others)
build_repo fail
printf '#!/usr/bin/env bash\necho new\n' >"$REPO/skills/recast/recast-new.sh"   # untracked
gate_run "$REPO" "git add -A && git commit -m x" 2 "gate: 'add -A && commit' folds untracked recast -> 2"

# A scoped 'git add <unrelated>' must NOT false-block on a coincidentally-dirty recast file
build_repo fail
printf 'changed\n' >>"$REPO/skills/recast/recast-state.sh"   # dirty recast, unstaged
printf 'note\n' >>"$REPO/README.md"
gate_run "$REPO" "git add README.md && git commit -m x" 0 "gate: scoped 'add <unrelated> && commit' ignores dirty recast -> 0"

# Attached -m message containing 'a' must not be read as -a (MAJOR regression)
build_repo fail
printf 'changed\n' >>"$REPO/skills/recast/recast-state.sh"   # dirty recast, unstaged
gate_run "$REPO" "git commit -m'add a recast note'" 0 "gate: attached -m message with 'a' is not -a -> 0"

# git -C <repo> commit resolves scope against the -C target, not the hook cwd (MAJOR regression)
build_repo fail
printf 'changed\n' >>"$REPO/skills/recast/recast-state.sh"
gi "$REPO" add skills/recast/recast-state.sh >/dev/null 2>&1
other_cwd="$(mktemp -d)"
gi "$other_cwd" init -q >/dev/null 2>&1
got=0
gate_json "git -C $REPO commit -m x" "$other_cwd" | python3 "$gate_hook" >/dev/null 2>&1 || got=$?
assert_eq "$got" 2 "gate: 'git -C <repo> commit' honors -C target -> 2"

# compound command — commit must be found after && even when pre-staged
build_repo fail
printf 'changed\n' >>"$REPO/skills/recast/recast-state.sh"
gi "$REPO" add skills/recast/recast-state.sh >/dev/null 2>&1
gate_run "$REPO" "git add -A && git commit -m x" 2 "gate: compound 'add && commit' (pre-staged) -> 2"

# operator fused to the prior token / newline-joined 'add … commit' must not bypass the gate
build_repo fail
printf 'changed\n' >>"$REPO/skills/recast/recast-state.sh"   # unstaged
gate_run "$REPO" "git add -A; git commit -m x" 2 "gate: 'add -A; commit' (fused semicolon) -> 2"
gate_run "$REPO" $'git add -A\ngit commit -m x' 2 "gate: 'add -A' newline 'commit' -> 2"

# review BLOCKER 2: 'git add <x> && git commit -a' must sweep ALL modified tracked files, not just x
build_repo fail
printf 'changed\n' >>"$REPO/skills/recast/recast-state.sh"   # modified, NOT added
printf 'note\n' >>"$REPO/README.md"
gate_run "$REPO" "git add README.md && git commit -a -m x" 2 "gate: 'add <x> && commit -a' sweeps unadded recast -> 2"

# review BLOCKER 1: a phantom 'git commit' word-pair (here inside echo) must not preempt the real commit
build_repo fail
printf 'changed\n' >>"$REPO/skills/recast/recast-state.sh"   # modified, added by the real command below
gate_run "$REPO" "echo git commit -m done && git add skills/recast/recast-state.sh && git commit -m x" 2 "gate: phantom 'git commit' does not preempt real add&&commit -> 2"

# regression guard: env-prefixed commit (the real commit pattern) must still be detected
build_repo fail
printf 'changed\n' >>"$REPO/skills/recast/recast-state.sh"
gi "$REPO" add skills/recast/recast-state.sh >/dev/null 2>&1
gate_run "$REPO" "ALLOW_GIT_WRITE=1 git commit -m x" 2 "gate: env-prefixed 'VAR=1 git commit' still gated -> 2"

# --dry-run creates no commit — do not run the suite
build_repo fail
printf 'changed\n' >>"$REPO/skills/recast/recast-state.sh"
gi "$REPO" add skills/recast/recast-state.sh >/dev/null 2>&1
gate_run "$REPO" "git commit --dry-run -m x" 0 "gate: --dry-run creates no commit -> 0"

# review MAJOR: pathspecs are cwd-relative — a subdirectory invocation must still be gated
build_repo fail
printf 'changed\n' >>"$REPO/skills/recast/recast-state.sh"   # broken, unstaged
gate_run "$REPO/skills/recast" "git add recast-state.sh && git commit -m x" 2 "gate: subdir cwd 'add <file> && commit' -> 2"
gate_run "$REPO/skills/recast" "git commit -m x recast-state.sh" 2 "gate: subdir cwd bare-pathspec commit -> 2"

# review MAJOR: --trailer / -t values must not become phantom pathspecs (which empty the scope)
build_repo fail
printf 'changed\n' >>"$REPO/skills/recast/recast-state.sh"
gi "$REPO" add skills/recast/recast-state.sh >/dev/null 2>&1
gate_run "$REPO" "git commit --trailer 'Reviewed-by: X' -m msg" 2 "gate: --trailer value is not a pathspec -> 2"
gate_run "$REPO" "git commit -t /tmp/tmpl.txt -m msg" 2 "gate: -t template value is not a pathspec -> 2"

# fused operators and redirects must not hide or narrow the commit
build_repo fail
printf 'changed\n' >>"$REPO/skills/recast/recast-state.sh"
gate_run "$REPO" "git add -A&&git commit -m x" 2 "gate: fused 'add -A&&git commit' -> 2"
build_repo fail
printf 'changed\n' >>"$REPO/skills/recast/recast-state.sh"
gi "$REPO" add skills/recast/recast-state.sh >/dev/null 2>&1
gate_run "$REPO" "git commit -m x >/dev/null 2>&1" 2 "gate: redirects are not pathspecs -> 2"
gate_run "$REPO" "(git commit -m x)" 2 "gate: subshell commit is detected -> 2"

# redirect false-block guard: unrelated plain commit with redirects ignores a dirty recast file
build_repo fail
printf 'changed\n' >>"$REPO/skills/recast/recast-state.sh"   # dirty, unstaged, NOT committed
printf 'note\n' >>"$REPO/README.md"
gi "$REPO" add README.md >/dev/null 2>&1
gate_run "$REPO" "git commit -m x >log.txt 2>&1" 0 "gate: unrelated commit with redirects ignores dirty recast -> 0"

# --amend that re-includes a staged recast change
build_repo fail
printf 'changed\n' >>"$REPO/skills/recast/recast-state.sh"
gi "$REPO" add skills/recast/recast-state.sh >/dev/null 2>&1
gate_run "$REPO" "git commit --amend -m x" 2 "gate: --amend with staged recast -> 2"

# unborn HEAD: no git diff HEAD fatal; behaves via --cached
build_repo pass --no-head
gi "$REPO" add skills/recast/recast-state.sh >/dev/null 2>&1
gate_run "$REPO" "git commit -m x" 0 "gate: unborn HEAD, staged recast, passing -> 0 (no fatal)"

# fail-safe: garbage stdin -> 0
got=0
printf 'not-json' | python3 "$gate_hook" >/dev/null 2>&1 || got=$?
assert_eq "$got" 0 "gate: garbage stdin -> 0 (fail-safe)"

# ===== review round 2: index-mutating subcommands, wrapper tokens, chained + leading-redirect =====

# git rm of a tracked recast source is committed as a deletion — the gate must run its suite even
# though the file is UNMODIFIED at gate time (no diff until the rm runs).
build_repo fail
gate_run "$REPO" "git rm skills/recast/recast-state.sh && git commit -m x" 2 "gate: 'git rm <recast> && commit' -> 2"

# git mv of a recast source (source path leaves the tree) must be gated too.
build_repo fail
gate_run "$REPO" "git mv skills/recast/recast-state.sh skills/recast/renamed.sh && git commit -m x" 2 "gate: 'git mv <recast> ... && commit' -> 2"

# rm of an UNRELATED file must not false-block on a coincidentally-dirty recast.
build_repo fail
printf 'changed\n' >>"$REPO/skills/recast/recast-state.sh"   # dirty recast, unstaged, not part of the rm
gate_run "$REPO" "git rm README.md && git commit -m x" 0 "gate: 'git rm <unrelated> && commit' ignores dirty recast -> 0"

# Wrapper tokens (time/env/sudo/nice) before git must not hide a real commit (the gate only parses,
# never executes, so sudo/env here are inert).
build_repo fail
printf 'changed\n' >>"$REPO/skills/recast/recast-state.sh"
gi "$REPO" add skills/recast/recast-state.sh >/dev/null 2>&1
gate_run "$REPO" "time git commit -m x" 2 "gate: 'time git commit' still gated -> 2"
gate_run "$REPO" "env git commit -m x" 2 "gate: 'env git commit' still gated -> 2"
gate_run "$REPO" "sudo git commit -m x" 2 "gate: 'sudo git commit' still gated -> 2"
gate_run "$REPO" "nice git commit -m x" 2 "gate: 'nice git commit' still gated -> 2"

# A wrapper on an unrelated plain commit must not over-block on a dirty-but-unstaged recast.
build_repo fail
printf 'changed\n' >>"$REPO/skills/recast/recast-state.sh"   # dirty, unstaged
printf 'note\n' >>"$REPO/README.md"
gi "$REPO" add README.md >/dev/null 2>&1
gate_run "$REPO" "time git commit -m x" 0 "gate: 'time git commit' (index=unrelated) ignores dirty recast -> 0"

# echo VAR=1 git commit — a phantom (echo arg), NOT a real commit; must not run the suite.
build_repo fail
printf 'changed\n' >>"$REPO/skills/recast/recast-state.sh"
gi "$REPO" add skills/recast/recast-state.sh >/dev/null 2>&1
gate_run "$REPO" "echo VAR=1 git commit -m x" 0 "gate: 'echo VAR=1 git commit' is a phantom -> 0"

# Chained commits: a later commit in the same compound command that stages a recast source must be
# seen, not just the first commit.
build_repo fail
printf 'changed\n' >>"$REPO/skills/recast/recast-state.sh"   # unstaged; second add stages it
gate_run "$REPO" "git add README.md && git commit -m a && git add skills/recast/recast-state.sh && git commit -m b" 2 "gate: second chained commit staging recast -> 2"

# Leading redirect before git must not defeat detection.
build_repo fail
printf 'changed\n' >>"$REPO/skills/recast/recast-state.sh"   # unstaged tracked recast
gate_run "$REPO" "2>&1 git commit -am x" 2 "gate: leading redirect '2>&1 git commit -am' -> 2"

# --include / -i unions the pathspec WITH the index — a staged recast source must not be dropped.
build_repo fail
printf 'changed\n' >>"$REPO/skills/recast/recast-state.sh"
gi "$REPO" add skills/recast/recast-state.sh >/dev/null 2>&1
gate_run "$REPO" "git commit --include README.md -m x" 2 "gate: '--include <other>' still commits staged recast -> 2"
gate_run "$REPO" "git commit -i README.md -m x" 2 "gate: '-i <other>' still commits staged recast -> 2"

# ================= Decomposition pin: ONLY skills/recast is gated =================
# The regex was narrowed to skills/recast. Any OTHER skill's source (e.g. a since-removed subsystem)
# with a FAILING suite must NOT block the commit, and the edit hook must ignore it.
build_repo pass
mkdir -p "$REPO/skills/other/tests"
printf '#!/usr/bin/env bash\necho other\n' >"$REPO/skills/other/other-x.sh"
printf 'def test_x():\n    assert False\n' >"$REPO/skills/other/tests/test_other_x.py"
gi "$REPO" add -A >/dev/null 2>&1
gate_run "$REPO" "git commit -m x" 0 "gate: staged non-recast skill + FAILING suite -> 0 (un-gated)"
edit_run "$REPO" skills/other/other-x.sh 0 "edit-time: non-recast skill source -> 0 (un-gated)"

# ================= Real-repo source->test naming map (silent-no-op guard) =================
map_ok=1
for src in "$repo_root"/skills/recast/*.sh; do
  [[ -e "$src" ]] || continue
  sub="$(basename "$(dirname "$src")")"
  stem="$(basename "$src" .sh)"
  want="$repo_root/skills/$sub/tests/test_${stem//-/_}.py"
  if [[ ! -f "$want" ]]; then
    printf 'FAIL  real-repo map: %s -> %s (missing)\n' "$src" "$want"
    map_ok=0
  fi
done
if [[ "$map_ok" -eq 1 ]]; then
  printf 'PASS  real-repo source->test naming map is complete\n'
  pass=$((pass + 1))
else
  fail=$((fail + 1))
fi

printf '\n%d passed, %d failed\n' "$pass" "$fail"
[[ "$fail" -eq 0 ]]

#!/usr/bin/env bash
set -euo pipefail

# Script: test_git_timing_guard.sh
# Purpose: Regression suite for scripts/git-timing-guard.sh — the LOCAL vs PUBLIC boundary
# Usage: ./scripts/tests/test_git_timing_guard.sh    (exit 0 all pass, 1 any fail)
#
# WHY IT NOW LIVES HERE. This header used to carry the opposite instruction — that the suite
# "deliberately does NOT live in scripts/tests/ — putting it there would make /audit --tests
# depend on a file no other machine has." That reason was CORRECT while the subject was the
# untracked, machine-local ~/.claude/.git-timing-guard.sh. It EVAPORATED the moment the subject
# was tracked at scripts/git-timing-guard.sh: every checkout of this repo now has the file, so
# the sweep depends on nothing machine-local. Leaving the old sentence in place would have made
# the file contradict its own location.
#
# SUBJECT RESOLUTION — the sibling copy, never $HOME. ~/.claude/scripts is a symlink farm into
# the PRODUCTION clone, not this one, so a $HOME-resolved subject would grade production's copy
# forever and no dev-branch change to the guard would ever be tested. Resolve from BASH_SOURCE,
# exactly as scripts/tests/test_exec_bit_guard.sh does. $HOME-based resolution is forbidden here.
#
# What this suite does NOT prove: the live PreToolUse registration still points at the untracked
# original, so a green run here grades the TRACKED logic, not the gate currently running.
#
# TIME INDEPENDENCE: the guard consults the wall clock, so a run after the window
# closes would find it inert and every should-block case would pass for free. The
# suite therefore controls the CONFIG, not the clock — $HOME is redirected at a
# fixture (the guard's only $HOME use is its config path), so "window open" is
# 0000-2400/days 1-7 and "window closed" is 0000-0000. Both hold at any instant.

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
guard="$here/../git-timing-guard.sh"
[ -x "$guard" ] || { echo "FAIL setup: $guard not executable" >&2; exit 1; }

# Physical path: a symlinked $TMPDIR yields a logical path that does not
# physically contain the fixture, which can put a path-resolving tool on a
# different branch and never reach the subject.
tmproot="$(cd "$(mktemp -d)" && pwd -P)"
trap 'rm -rf "$tmproot"' EXIT

pass=0; fail=0

# make_home <name> <start> <end> <days> -- a fixture HOME holding a guard config.
# Passing "-" for <start> writes NO config at all (the fail-open case).
make_home() {
  local h="$tmproot/$1"; mkdir -p "$h/.claude"
  if [ "$2" != "-" ]; then
    printf 'GUARD_REPO_PATTERN=wohlford/dotclaude\nGUARD_DAYS=%s\nGUARD_START=%s\nGUARD_END=%s\n' \
      "$4" "$2" "$3" > "$h/.claude/.git-timing-guard.conf"
  fi
  printf '%s' "$h"
}

# make_repo <name> <origin-url> -- a git repo the guard can resolve an origin from.
# "-" for <origin-url> leaves the repo with no origin at all.
# Signing is disabled per-fixture, not inherited. A fixture repo that inherits a global
# tag.gpgsign=true HANGS on a hardware-key PIN prompt rather than failing — no verdict, no
# teardown, nothing to notice. This suite never commits or tags today, so the two settings are
# inert here; they are set anyway because the repo's fixture-signing gate is fail-closed by
# design, and because the row that first commits inside a fixture would otherwise inherit it.
make_repo() {
  local r="$tmproot/repos/$1"; mkdir -p "$r"
  git init -q "$r"
  git -C "$r" config commit.gpgsign false
  git -C "$r" config tag.gpgsign false
  [ "$2" = "-" ] || git -C "$r" remote add origin "$2"
  printf '%s' "$r"
}

# check <expected-rc> <description> <home> <cwd> <command>
check() {
  local want="$1" desc="$2" home="$3" cwd="$4" cmd="$5"
  local payload got out
  payload=$(jq -nc --arg c "$cmd" --arg d "$cwd" '{tool_input:{command:$c},cwd:$d}')
  set +e
  out=$(printf '%s' "$payload" | HOME="$home" "$guard" 2>&1)
  got=$?
  set -e
  if [ "$got" = "$want" ]; then
    printf 'PASS  %s\n' "$desc"; pass=$((pass + 1))
  else
    printf 'FAIL  %s (want rc=%s, got rc=%s)%s\n' "$desc" "$want" "$got" \
      "${out:+ — $out}"; fail=$((fail + 1))
  fi
}

# check_msg <substring> <present|absent> <description> <home> <cwd> <command>
check_msg() {
  local needle="$1" mode="$2" desc="$3" home="$4" cwd="$5" cmd="$6"
  local payload out hit
  payload=$(jq -nc --arg c "$cmd" --arg d "$cwd" '{tool_input:{command:$c},cwd:$d}')
  set +e
  out=$(printf '%s' "$payload" | HOME="$home" "$guard" 2>&1)
  set -e
  hit=absent
  case "$out" in *"$needle"*) hit=present ;; esac
  if [ "$hit" = "$mode" ]; then
    printf 'PASS  %s\n' "$desc"; pass=$((pass + 1))
  else
    printf 'FAIL  %s (want %s, was %s) — message: %s\n' "$desc" "$mode" "$hit" "$out"
    fail=$((fail + 1))
  fi
}

open_home=$(make_home open 0000 2400 1-7)     # window ALWAYS open
shut_home=$(make_home shut 0000 0000 1-7)     # window ALWAYS closed
none_home=$(make_home none - - -)             # no config at all

guarded=$(make_repo guarded https://github.com/wohlford/dotclaude.git)
foreign=$(make_repo foreign https://github.com/someone/other.git)
noremote=$(make_repo noremote -)

echo "--- the LOCAL half must pass: nothing leaves the machine ---"
check 0 "local commit is allowed"              "$open_home" "$guarded" "git commit -m 'x'"
check 0 "local commit -am is allowed"          "$open_home" "$guarded" "git commit -am 'x'"
check 0 "local annotated tag is allowed"       "$open_home" "$guarded" "git tag -a v1.0.0 -m 'x'"
check 0 "local bare tag is allowed"            "$open_home" "$guarded" "git tag v1.0.0"
check 0 "local commit via -C is allowed"       "$open_home" "$tmproot" "git -C $guarded commit -m 'x'"
check 0 "commit inside a chain is allowed"     "$open_home" "$guarded" "git add -A && git commit -m 'x'"

echo "--- the PUBLIC half must still block: this reaches the network ---"
check 2 "push is blocked in the window"        "$open_home" "$guarded" "git push origin main"
check 2 "bare push is blocked"                 "$open_home" "$guarded" "git push"
check 2 "push --follow-tags is blocked"        "$open_home" "$guarded" "git push origin dev --follow-tags"
check 2 "push via -C is blocked"               "$open_home" "$tmproot" "git -C $guarded push origin main"
check 2 "push later in a chain is blocked"     "$open_home" "$guarded" "git commit -m 'x' && git push"
check 2 "push -u is blocked"                   "$open_home" "$guarded" "git push -u origin feature"

echo "--- scope and escape hatch must survive the change ---"
check 0 "override allows push"                 "$open_home" "$guarded" "ALLOW_GIT_WRITE=1 git push origin main"
check 0 "push in a foreign repo is allowed"    "$open_home" "$foreign" "git push origin main"
check 0 "push with no origin is allowed"       "$open_home" "$noremote" "git push origin main"
check 0 "push outside the window is allowed"   "$shut_home" "$guarded" "git push origin main"
check 0 "no config fails open"                 "$none_home" "$guarded" "git push origin main"

echo "--- reads are never writes ---"
check 0 "git status is allowed"                "$open_home" "$guarded" "git status -sb"
check 0 "git log is allowed"                   "$open_home" "$guarded" "git log --oneline -5"
check 0 "git fetch is allowed"                 "$open_home" "$guarded" "git fetch origin main"
check 0 "git merge --ff-only is allowed"       "$open_home" "$guarded" "git merge --ff-only FETCH_HEAD"
check 0 "tag --list is allowed"                "$open_home" "$guarded" "git tag -l"
check 0 "tag --delete is allowed"              "$open_home" "$guarded" "git tag -d v1.0.0"
# The two read-only tag queries the old guard refused. Both are commands real
# procedures run: /commit's step-8 tag post-condition, and the publish path's
# crash-recovery enumeration. Documented commands, so they are asserted, not assumed.
check 0 "tag --points-at is allowed"           "$open_home" "$guarded" "git tag --points-at HEAD | grep -x 'v1.0.0'"
check 0 "tag --merged enumeration is allowed"  "$open_home" "$guarded" "git tag --merged main --no-merged origin/main"
check 0 "a word ending in push is allowed"     "$open_home" "$guarded" "git log --grep=pushup"

echo "--- the block message must describe what is actually gated ---"
check_msg "git writes are paused" absent \
  "message does not overclaim 'git writes'"    "$open_home" "$guarded" "git push origin main"
check_msg "push" present \
  "message names push"                         "$open_home" "$guarded" "git push origin main"

# The RESULT line is printed before the verdict is computed, so its ABSENCE is
# itself the signal that the run died rather than passed.
printf '\nRESULT: %s passed, %s failed\n' "$pass" "$fail"
if [ "$fail" -eq 0 ]; then exit 0; else exit 1; fi

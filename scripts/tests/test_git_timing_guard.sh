#!/usr/bin/env bash
set -euo pipefail

# Script: test_git_timing_guard.sh
# Purpose: Regression suite for the LOCAL vs PUBLIC boundary. The subject's LOGIC now lives in
#          scripts/git-timing-guard.py; scripts/git-timing-guard.sh is a two-line TRANSITIONAL
#          `exec` shim (see its own header) that this suite drives by bare path, so a run here
#          exercises both the shim's exec plumbing and the .py's decisions in one pass.
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
# What this suite does NOT prove: this repo's own live PreToolUse registration
# ($HOME/.claude/settings.json) is unpromoted and still points at whatever
# $HOME/.claude/scripts/git-timing-guard.sh resolves to on THIS machine -- which, once this
# branch promotes, is the very shim the paragraph above describes, but before that promote it is
# still the pre-rewrite bash guard. Measured (this stopped being "the untracked original" as of
# `cfdbfab`): `readlink -f ~/.claude/scripts/git-timing-guard.sh` resolves to the TRACKED copy in
# the production clone, not the untracked orphan at `~/.claude/.git-timing-guard.sh` (that file
# still exists on disk, but nothing registers it any more). So a green run here grades the code
# on THIS branch, correctly -- but whether that code is the one actually running in any given
# session depends on whether this branch has been promoted and that session has restarted since;
# this suite has no way to observe either.
#
# TIME INDEPENDENCE: the guard consults the wall clock, so a run after the window
# closes would find it inert and every should-block case would pass for free. The
# suite therefore controls the CONFIG, not the clock — $HOME is redirected at a
# fixture (the guard's only $HOME use is its config path), so "window open" is
# 0000-2400/days 1-7 and "window closed" is 0000-0000. Both hold at any instant.

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
guard="$here/../git-timing-guard.sh"
guard_py="$here/../git-timing-guard.py"
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

# make_home_unreadable <name> -- a fixture HOME whose guard conf EXISTS but cannot be
# read (chmod 000). The guard's `[ -f "$conf" ]` check passes on an unreadable file
# -- existence, not readability -- so this reaches the reader's suppressed
# `grep ... 2>/dev/null`, which returns nothing and collapses repo_pat to empty: the
# same sentinel an absent GUARD_REPO_PATTERN hits.
make_home_unreadable() {
  local h="$tmproot/$1"; mkdir -p "$h/.claude"
  printf 'GUARD_REPO_PATTERN=wohlford/dotclaude\nGUARD_DAYS=1-7\nGUARD_START=0000\nGUARD_END=2359\n' \
    > "$h/.claude/.git-timing-guard.conf"
  chmod 000 "$h/.claude/.git-timing-guard.conf"
  printf '%s' "$h"
}

# make_home_typo_key <name> -- a fixture HOME whose conf is readable but the required
# key is misspelled (PATERN, not PATTERN). The guard's `grep -E "^GUARD_REPO_PATTERN="`
# never matches, so repo_pat collapses to empty exactly as if the key were absent.
make_home_typo_key() {
  local h="$tmproot/$1"; mkdir -p "$h/.claude"
  printf 'GUARD_REPO_PATERN=wohlford/dotclaude\nGUARD_DAYS=1-7\nGUARD_START=0000\nGUARD_END=2359\n' \
    > "$h/.claude/.git-timing-guard.conf"
  printf '%s' "$h"
}

# make_home_empty_pattern <name> -- a fixture HOME whose conf is readable, the key is
# spelled correctly and present, but its VALUE is empty. The grep matches and `cut`
# returns the empty string after the "=", so repo_pat collapses to empty the same way
# as the two fixtures above.
make_home_empty_pattern() {
  local h="$tmproot/$1"; mkdir -p "$h/.claude"
  printf 'GUARD_REPO_PATTERN=\nGUARD_DAYS=1-7\nGUARD_START=0000\nGUARD_END=2359\n' \
    > "$h/.claude/.git-timing-guard.conf"
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

# check_diff <description> <home> <cwd> <command> -- asserts the shim's rc equals the .py's rc
# for the SAME payload, invoking the shim by its BARE PATH (never sourced, never called through
# a function) so the exec bit and the `exec ... "$@"` plumbing are actually exercised, not just
# the .py's own decision logic a direct python3 invocation alone would prove. This is the
# differential row the plan review's shim MAJOR asked for: once T4 moved the mutation suite's
# subject to the .py and T6 stopped registering the .sh, nothing else in this repo drives the
# .py's verdicts through THIS suite at all -- every other row here calls the shim only.
check_diff() {
  local desc="$1" home="$2" cwd="$3" cmd="$4"
  local payload shim_rc py_rc
  payload=$(jq -nc --arg c "$cmd" --arg d "$cwd" '{tool_input:{command:$c},cwd:$d}')
  set +e
  printf '%s' "$payload" | HOME="$home" "$guard" >/dev/null 2>&1
  shim_rc=$?
  printf '%s' "$payload" | HOME="$home" python3 "$guard_py" >/dev/null 2>&1
  py_rc=$?
  set -e
  if [ "$shim_rc" = "$py_rc" ]; then
    printf 'PASS  %s (rc=%s both)\n' "$desc" "$shim_rc"; pass=$((pass + 1))
  else
    printf 'FAIL  %s (shim rc=%s, py rc=%s — the shim'"'"'s exec plumbing disagrees with the .py it exec'"'"'s into)\n' \
      "$desc" "$shim_rc" "$py_rc"
    fail=$((fail + 1))
  fi
}

open_home=$(make_home open 0000 2400 1-7)     # window ALWAYS open
shut_home=$(make_home shut 0000 0000 1-7)     # window ALWAYS closed
none_home=$(make_home none - - -)             # no config at all
unreadable_home=$(make_home_unreadable unreadable)   # conf exists, chmod 000
typo_home=$(make_home_typo_key typo)                 # conf readable, key misspelled
emptypat_home=$(make_home_empty_pattern emptypat)    # conf readable, value empty

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

echo "--- shim/.py differential: the shim's exec plumbing must not disagree with what it execs into ---"
check_diff "shim rc == py rc on a MUST-BLOCK push" \
  "$open_home" "$guarded" "git push origin main"

echo "--- scope and escape hatch must survive the change ---"
check 0 "override allows push"                 "$open_home" "$guarded" "ALLOW_GIT_WRITE=1 git push origin main"
check 0 "push in a foreign repo is allowed"    "$open_home" "$foreign" "git push origin main"
check 0 "push with no origin is allowed"       "$open_home" "$noremote" "git push origin main"
check 0 "push outside the window is allowed"   "$shut_home" "$guarded" "git push origin main"
check 0 "no config fails open"                 "$none_home" "$guarded" "git push origin main"

echo "--- broken policy config also fails open (CONTROL rows, not red-first) ---"
# These three rows assert rc=0 -- TODAY's behaviour -- so they are green before AND
# after this change. That is normally the signature of a vacuous test; here it is the
# point. An unreadable conf, a typo'd key and an empty value all collapse into the
# same empty repo_pat before `[ -n "$repo_pat" ] || exit 0` -- one sentinel,
# four meanings. These rows are CONTROLS pinning the DECISION that fail-open there is
# the specification, not an accident: with the pattern unknown the guard cannot tell
# which repos it governs, so failing closed would assert jurisdiction over EVERY repo
# on this machine at ALL hours, triggered by nothing more than a chmod or a typo --
# which also violates scripts/HOOKS.md's "Only real violations reach exit 2". The
# silence, not the fail-open, is the actual defect, and it is fixed separately by an
# /audit observer -- not by making this guard exit 2. A future well-meaning change
# that makes the guard block on a broken conf must turn these three rows RED.
check 0 "unreadable conf fails open"           "$unreadable_home" "$guarded" "git push origin main"
check 0 "malformed key (typo) fails open"      "$typo_home"       "$guarded" "git push origin main"
check 0 "empty GUARD_REPO_PATTERN fails open"  "$emptypat_home"   "$guarded" "git push origin main"

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

# ---------------------------------------------------------------------------
# The two sections below are RED today and GREEN once scripts/git-timing-guard.sh
# is rewritten to reuse scripts/lib/git_command.py's tokenizer (see
# memory/2026-09-01-timing-guard-command-position.md, task T1/T3). This bash
# guard's crude `(^|[^A-Za-z0-9_])git...push` regex cannot distinguish "the phrase
# appears in this segment's text" from "a git push actually happens here", so it
# gets both halves of that distinction wrong today: it blocks inert text (below,
# MUST ALLOW) and it also — by the same crudeness — happens to still catch a
# wrapped/nested push the *tokenizer* structurally cannot see (below, DELIBERATE
# DROP). These rows were sampled from a transitional differential harness (not
# committed; it disappears once the .py lands and only the knowledge below needs
# to survive) that generated shapes MECHANICALLY from scripts/lib/git_command.py's
# WRAPPERS set and its "known fail-open forms" docstring paragraph, rather than
# hand-picking examples — see the design record's corrected finding 2 for why two
# earlier hand-picked corpora each missed real classes (one missed six MUST BLOCK
# shapes, the next missed four more DELIBERATE DROP sub-classes plus a third it
# never named at all).
# ---------------------------------------------------------------------------

echo "--- MUST ALLOW: false blocks this rewrite fixes (RED until the .py lands) ---"
# Each of these mentions the two-token phrase in TEXT with no git invocation ever
# happening — an argument to a read-only command, a comment, or an assignment that
# is never referenced again. The crude regex cannot tell "text contains the words"
# from "a push happens here"; the tokenizer can, because none of these have a bare
# `git` token in command position.
check 0 "echo of the phrase is allowed"           "$open_home" "$guarded" "echo 'git push origin main'"
check 0 "grep for the phrase is allowed"          "$open_home" "$guarded" "grep 'git push origin main' notes.txt"
check 0 "printf literal is allowed"               "$open_home" "$guarded" "printf 'git push origin main\n'"
check 0 "trailing shell comment is allowed"       "$open_home" "$guarded" "echo done  # git push origin main"
check 0 "unexecuted variable assignment is allowed" "$open_home" "$guarded" 'x="git push origin main"'
check 0 "phrase in an unrelated commit message is allowed" "$open_home" "$guarded" \
  'git commit -m "reminder: git push origin main later"'

echo "--- DELIBERATE DROP: conceded residuals, matching push-guard.py's posture (RED until the .py lands) ---"
# These rows are a REGRESSION IN DETECTION, ACCEPTED ON PURPOSE — not a bug to
# chase. Today's crude regex blocks them by accident (it matches "git ... push"
# anywhere in the segment's raw text, ignoring who is actually in command
# position); the tokenizer-based rewrite will allow them, because in each case
# there is no bare `git` token in command position for THIS command's own token
# stream to find. scripts/lib/git_command.py's module docstring names this class
# under "Known fail-open forms", and scripts/push-guard.py's CONCEDED RESIDUALS
# paragraph concedes the identical class for the identical reason: re-catching it
# reintroduces the false-positive class (the MUST ALLOW rows above) that this
# rewrite exists to kill. A future well-meaning change that makes any of these
# rows block again should be read as a regression on the MUST ALLOW rows, not a
# fix — check those still pass before "fixing" this section.
#
# Sub-class 1/3 — a wrapper carrying its OWN ARGUMENTS: starts_command only steps
# over a BARE literal WRAPPERS name; the token immediately before `git` here is
# the wrapper's own flag/value, not the wrapper name, so command position is lost.
# Re-catching would mean teaching starts_command each wrapper's own option
# grammar — open-ended and mis-stepping fails SILENTLY (over-stepping swallows
# the real command); not worth building (see push-guard.py's docstring note).
check 0 "sudo -u deploy <pub> is a conceded drop" "$open_home" "$guarded" "sudo -u deploy git push origin main"
check 0 "timeout 60 <pub> is a conceded drop"     "$open_home" "$guarded" "timeout 60 git push origin main"
check 0 "nice -n 5 <pub> is a conceded drop"      "$open_home" "$guarded" "nice -n 5 git push origin main"
#
# Sub-class 2/3 — a PATH-QUALIFIED spelling of a listed wrapper: `/usr/bin/sudo`
# is not the literal string `sudo`, so WRAPPERS membership (exact match) never
# fires. Re-catching would mean normalizing a wrapper token to its basename before
# the WRAPPERS lookup — a real option, out of scope for this task (T1 is corpus
# only), and not attempted here because it changes matching behavior, not tests.
check 0 "/usr/bin/sudo <pub> is a conceded drop"  "$open_home" "$guarded" "/usr/bin/sudo git push origin main"
#
# Sub-class 2b — a bare wrapper OUTSIDE the literal WRAPPERS set entirely (flock,
# caffeinate, ssh-agent, strace, unbuffer, faketime, ...). Even bare, it is never
# stepped over — WRAPPERS is membership in a closed 12-name set, not "looks like
# an exec-wrapper". Re-catching would mean widening WRAPPERS, which trades a
# known, bounded set for an open-ended guess at what programs re-exec their
# argument — the same "open-ended, not worth building" reasoning as sub-class 1.
check 0 "flock <pub> is a conceded drop"          "$open_home" "$guarded" "flock git push origin main"
#
# Sub-class 3/3 — the push is inside a SINGLE shlex token: a nested-shell string
# (`bash -c`/`sh -c`/`eval`/`su -c`/pipe-into-shell), a herestring body, a
# variable assigned the command text and later executed, or a script written to
# disk then run. In every case shlex collapses the whole phrase into one quoted
# token, so there is no standalone `git` token in THIS command's own token stream
# for `is_git`/`starts_command` to examine. git_command.py's own module
# docstring names this residual explicitly ("wrapped in sh -c/eval") and scopes
# resolving nested command strings as out of scope. Re-catching would mean
# recursively re-tokenizing the CONTENTS of arbitrary string arguments to
# arbitrary programs — unbounded, and exactly the class push-guard.py's
# docstring calls "open-ended rather than closed".
check 0 "bash -c '<pub>' is a conceded drop"      "$open_home" "$guarded" "bash -c 'git push origin main'"
check 0 "eval \"<pub>\" is a conceded drop"       "$open_home" "$guarded" 'eval "git push origin main"'
check 0 "echo '<pub>' | bash is a conceded drop"  "$open_home" "$guarded" "echo 'git push origin main' | bash"
check 0 "su -c '<pub>' is a conceded drop"        "$open_home" "$guarded" "su -c 'git push origin main'"
check 0 "herestring '<pub>' is a conceded drop"   "$open_home" "$guarded" "bash <<< 'git push origin main'"
check 0 "a variable holding, then running, the command is a conceded drop" \
  "$open_home" "$guarded" "c='git push origin main'; \$c"
check 0 "writing a script then running it is a conceded drop" \
  "$open_home" "$guarded" "printf '%s\n' 'git push origin main' > /tmp/x.sh && bash /tmp/x.sh"

# ---------------------------------------------------------------------------
# M1 cwd — four decisions PINNED by memory/2026-09-01-timing-guard-command-position.md,
# section "M1 cwd" / "RESOLUTIONS — M1 cwd → ACCEPT the two behaviour changes, DECIDE the
# third, FIX the fourth." The bash guard never follows `cd` in the command text —
# `target="$cwd"` (the payload's own cwd), overridden only by a segment's own `-C`
# (and only the FIRST one, via `head -1`). The tokenizer the rewrite adopts DOES follow
# `cd`, and its `-C` walk overwrites `cdir` on every occurrence, so it ends up holding the
# LAST. That changes scoping in BOTH directions and is a decision to record, not a silent
# side effect of swapping the matching engine. Each row below states, from measurement,
# whether it is RED today (and green only once the .py lands) or a CONTROL (green on both
# sides of the rewrite, because the decided rule reproduces today's answer by coincidence
# of mechanism, not because the mechanism itself is unchanged).
# ---------------------------------------------------------------------------

echo "--- M1 cwd: four pinned decisions (RED until .py lands, one CONTROL) ---"

# M1 decision 1/4 — closing a live FAIL-OPEN. The payload cwd is elsewhere; the command
# text `cd`s into the guarded repo before pushing. Today's guard never looks at `cd` at
# all, so target stays the payload cwd (elsewhere, no matching origin) and the push is
# NOT caught. Measured against the shipped guard: rc=0. The rewrite follows `cd`, lands
# effective_dir on the guarded repo, and must block. RED today (want rc=2, today gives
# rc=0).
check 2 "M1: cd into the guarded repo from elsewhere is blocked (closes a live fail-open)" \
  "$open_home" "$tmproot" "cd $guarded && git push origin main"

# M1 decision 2/4 — fixing a false block. The payload cwd IS the guarded repo, but the
# command text `cd`s into the foreign repo before pushing, so the push actually targets
# $foreign. Today's guard ignores that `cd` and scopes to the payload cwd (guarded),
# blocking a push that never touches the guarded repo. Measured against the shipped
# guard: rc=2. The rewrite follows `cd`, lands effective_dir on $foreign (no matching
# origin), and must allow. RED today (want rc=0, today gives rc=2).
check 0 "M1: cd into the foreign repo from a guarded cwd is allowed (fixes a false block)" \
  "$open_home" "$guarded" "cd $foreign && git push origin main"

# M1 decision 3/4 — the DECIDED fallback when the tokenizer can't resolve a `cd` target.
# `cd "$SOMEVAR"` is a real, unresolvable shell substitution; the tokenizer reports
# effective_dir=None for it (measured directly against git_command.py, not inferred).
# The decided rule is: fall back to the payload cwd — exactly what target="$cwd" already
# does today, so this keeps the change about command position rather than smuggling in a
# new cwd posture. With payload cwd = guarded, today's guard (which never resolves `cd`
# at all, so target IS the payload cwd) and the decided post-rewrite fallback (effective
# dir unresolvable -> payload cwd) land on the identical answer by DIFFERENT mechanisms.
# Measured: rc=2 today. This row is a CONTROL, not a red-first pin — it stays green
# before and after a correct rewrite, and a green reading here is not by itself evidence
# the fallback was implemented; only a run against the finished .py proves that.
# shellcheck disable=SC2016  # $SOMEVAR is literal payload text, must not expand
check 2 "M1: cd into an unresolvable \$VAR falls back to the payload cwd (CONTROL)" \
  "$open_home" "$guarded" 'cd "$SOMEVAR" && git push origin main'

# M1 decision 4/4 — compose effective_dir with cdir, never use a bare cdir. Two `-C`
# options on one invocation: bash's own extraction takes the FIRST (`head -1`), while the
# tokenizer's option walk reassigns `cdir` on every `-C` it meets, so it ends up holding
# the LAST (measured directly against git_command.py: cdir == the second path). First=
# guarded, last=foreign: today's guard reads the first -C (guarded, matching origin) and
# blocks. Measured: rc=2. The composed post-rewrite answer must use the LAST -C
# (foreign, non-matching) and allow. RED today (want rc=0, today gives rc=2). Constructed
# so the two readings disagree, per the task's request — first-vs-last is not
# untestable.
check 0 "M1: two -C options compose on the LAST one, not bare cdir / the first" \
  "$open_home" "$tmproot" "git -C $guarded -C $foreign push origin main"

# M1, THE MIRROR OF THE ROW ABOVE, AND THE DANGEROUS DIRECTION. The row above pins a false
# BLOCK; swap the two paths and the same first-vs-last disagreement becomes a live FAIL-OPEN.
# Measured against git itself: `git -C a -C b` resolves to b, so with first=foreign and
# last=guarded git really operates on the GUARDED repo — while today's guard reads the FIRST
# -C (foreign, non-matching) and ALLOWS. rc=0 measured: a real publish from the guarded repo
# goes through unblocked inside the window. The tokenizer resolves it to the guarded repo
# correctly, so the rewrite closes this.
# This row exists because the plan's decision 4 was written and tested in the false-block
# direction ONLY; the fail-open mirror was found by probing after the fact. A pair of rows
# that disagree in opposite directions is the shape this decision needed all along --
# one direction alone cannot tell "composes on the last" from "happens to agree here".
# RED today (want rc=2, today gives rc=0).
check 2 "M1: the -C mirror -- last -C targets the guarded repo, closing a live fail-open" \
  "$open_home" "$tmproot" "git -C $foreign -C $guarded push origin main"

# --- RESERVED WORDS put git in command position, exactly as an operator does ------------
# Found by the final whole-branch review, NOT by the generated corpus -- and the reason is worth
# recording. The corpus was derived from `WRAPPERS` and the tokenizer's "known fail-open forms"
# paragraph. `RESERVED_WORDS` sits fifteen lines below `WRAPPERS` in the same module and was never
# read, so an entire class of REAL publishes went unrepresented. A generator cannot rescue a
# generator seeded from an incomplete reading of the source.
# These are NOT the conceded class. The publish is a bare git token in genuine command position in
# the segment's own token stream, and the tokenizer finds it. The old bash guard blocked all of
# these; push-guard.py carries the identical bare call and does not; so between the rewrite and
# this fix NOTHING on the machine caught them. RED before the fix -- each gave rc=0.
check 2 "reserved: a brace group is command position"     "$open_home" "$guarded" "{ git push origin main; }"
check 2 "reserved: if/then is command position"           "$open_home" "$guarded" "if true; then git push origin main; fi"
check 2 "reserved: while/do is command position"          "$open_home" "$guarded" "while false; do git push origin main; done"
check 2 "reserved: a function body is command position"   "$open_home" "$guarded" "f() { git push origin main; }; f"
check 2 "reserved: exec is command position"              "$open_home" "$guarded" "exec git push origin main"
check 2 "reserved: ! negation is command position"        "$open_home" "$guarded" "! git push origin main"
check 2 "reserved: a compound later in a chain"           "$open_home" "$guarded" "git status && if true; then git push origin main; fi"

# THE ROW THAT CATCHES HALF A FIX. Teaching the matcher about reserved words WITHOUT also teaching
# the override walker about them turns this from allow into block: _leading_env_authorized walks
# from seg[0], which is now the reserved word, and breaks before it ever reaches the assignment.
# So this row is GREEN before the change and GREEN after -- and RED for anything in between. It is
# the control proving the two halves landed together, and it is the only row that can say so.
check 0 "reserved: the override still authorizes inside a compound" \
  "$open_home" "$guarded" "if true; then ALLOW_GIT_WRITE=1 git push origin main; fi"

# The stricter wrapper rule must SURVIVE the reserved-word widening: a wrapper is not a reserved
# word, so an assignment after one still does not authorize.
check 2 "reserved: a wrapper is still not a reserved word" \
  "$open_home" "$guarded" "sudo ALLOW_GIT_WRITE=1 git push origin main"

# --- unknown global option: the two-primitive split, pinned rather than incidental --------
# Found by the final whole-branch review: _segment_contains_push (via iter_context_token_streams)
# and _first_push_target_dir (via iter_git_invocations_detailed) are two DIFFERENT primitives
# walking the same command (see _first_push_target_dir's own docstring for why neither one alone
# suffices). An unrecognized global option used to make them DISAGREE: _segment_contains_push's
# old hand-rolled option-skipper confidently found the push regardless, while
# iter_git_invocations_detailed correctly refused to guess and recorded the unknown option as the
# "subcommand" instead of push -- so _first_push_target_dir's loop never matched it and silently
# fell back to the payload cwd. On a foreign payload cwd that read as ALLOW for a real push in the
# guarded repo. Fixed by making _segment_contains_push's own walk raise on the identical unknown
# option instead of guessing -- both primitives now refuse the same input, and this guard's
# existing AMBIGUITY POSTURE (fail open, explicitly) takes over from there.
#
# WHAT THESE TWO ROWS ACTUALLY PIN. A previous revision of this comment claimed their verdict
# "does not move" against the pre-fold bash guard, on the reasoning that a foreign payload cwd
# never matches GUARD_REPO_PATTERN. That is FALSE, and the reasoning is the bug it missed: the old
# bash guard did not use the payload cwd at all when a -C was present -- it grepped the -C path out
# of the text and made THAT the target. Measured against 9612ae9 with an open window: both rows
# gave rc=2 there and rc=0 here.
#
# So every row in this region converted BLOCK -> allow -- these two AND the three below, five in
# total, not the three the old comment named. That is the documented AMBIGUITY POSTURE (an
# unrecognized global option is unjudgeable, so the guard fails open on it exactly as on any other
# tokenizing ambiguity), and it is the accepted cost of fix 3, not a regression: push-guard.py
# remains fail-closed over the same shape.
#
# What is TRUE of these two rows is narrower and is about MUTANTS, not about the old guard: a plain
# "assume valueless, step over" mutant of _skip_global_options survives against them, because both
# it and the correct code land on rc=0 here. Their value is discrimination, not differential -- the
# only row that kills the "assume value-taking" mutant is the `--badopt --other` one further down,
# under "THE ROW THAT ACTUALLY KILLS BOTH MUTATIONS"; measured, that mutant fails exactly 1 row.
check 0 "unknown global option before -C: ambiguous, fails open (not a silent cwd fallback)" \
  "$open_home" "$tmproot" "git -C $guarded --badopt push origin main"
check 0 "unknown global option, option order swapped: same ambiguous fail-open" \
  "$open_home" "$tmproot" "git --badopt -C $guarded push origin main"

# THE ROWS THAT ACTUALLY PIN THE SHAPE THAT MOVES -- payload cwd IS the guarded repo, no -C at
# all. Differential measured against the pre-fold bash guard: both these commands BLOCKED there
# (the crude regex just matches "push" in the text) and ALLOW here, which is the documented,
# accepted fail-open from the guard's AMBIGUITY POSTURE, not a regression -- push-guard.py fails
# CLOSED on the same ambiguity and covers the publication boundary regardless (see the guard's
# own module docstring). Without a row at THIS cwd, nothing in the suite can tell "the guard fails
# open on ambiguity, as documented" from "a mutated option-skipper silently mis-locates the
# subcommand and ALSO happens to fail open here" -- both read rc=0 with only the elsewhere-cwd
# rows above.
check 0 "unknown global option, cwd IS the guarded repo: still the documented ambiguity fail-open" \
  "$open_home" "$guarded" "git --badopt push origin main"
check 0 "unknown global option with an attached value (--badopt=1), same cwd, same fail-open" \
  "$open_home" "$guarded" "git --badopt=1 push origin main"

# THE ROW THAT ACTUALLY KILLS BOTH MUTATIONS. Measured (mutation on a sandbox copy, never the
# tracked file): reverting _skip_global_options to the pre-fold "assume valueless, step over" gives
# rc=2 on the two rows immediately above (a real behaviour change from rc=0), which those two rows
# already catch. But "assume value-taking instead" -- the OTHER wrong guess -- swallows the token
# immediately after the unknown option as its value; with `push` sitting directly after the option
# in both rows above, that mutant swallows `push` itself, _segment_contains_push then finds no
# push at all, and the guard returns 0 for the same reason (no push found) as the correct
# implementation does (an unjudgeable option, failing open) -- same rc, different reason, so
# neither of the rows above can tell them apart. Putting a SECOND recognized-shaped token between
# the unknown option and `push` breaks that coincidence: the "assume value-taking" mutant swallows
# `--other` instead of `push`, finds `push` normally, proceeds past the local walk, and then falls
# back to the payload cwd exactly as the "assume valueless" mutant does at this same cwd -- both
# mutants give rc=2 here, against rc=0 for the correct (raising) implementation. One row, one
# assertion, both wrong guesses caught.
check 0 "unknown global option followed by a second option, cwd IS the guarded repo: kills both assume-valueless and assume-value-taking mutants of _skip_global_options" \
  "$open_home" "$guarded" "git --badopt --other push origin main"

# The RESULT line is printed before the verdict is computed, so its ABSENCE is
# itself the signal that the run died rather than passed.
printf '\nRESULT: %s passed, %s failed\n' "$pass" "$fail"
if [ "$fail" -eq 0 ]; then exit 0; else exit 1; fi

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

# A real subdirectory of $guarded -- used as a literal `cd` target in the reserved-word-cd trade
# rows below (never resolved by the guard, which only tokenizes the command text) and as the
# payload cwd for that block's second CONTROL row (which IS resolved against the filesystem).
mkdir -p "$guarded/sub"

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
check 2 "push behind eval is blocked in the window"      "$open_home" "$guarded" "eval git push origin main"
check 2 "push behind builtin eval -- is blocked"          "$open_home" "$guarded" "builtin eval -- git push origin main"
check 0 "ALLOW_GIT_WRITE=1 before eval authorizes"        "$open_home" "$guarded" "ALLOW_GIT_WRITE=1 eval git push origin main"

# --- final whole-branch review pins (Rulings R6/R8, 2026-09-11-eval-wrapper-bypass) -------------
# These five rows PIN decided or pre-existing behaviour found by the final review; none of them
# reflects a code change here. Ruling R6 (I1): a `cd` reached through a wrapper (`command`, `time`,
# `nohup`, ...) leaves effective_dir unresolvable to the tokenizer, and this guard's pinned rule for
# an unknown cwd is to fall back to the PAYLOAD cwd -- exactly what `target="$cwd"` already did
# before the tokenizer rewrite. That is a DECIDED trade, not a silent regression: re-enumerating
# which wrapper chains move the shell's cwd is the parameter both design BLOCKERs on this branch
# lived in, and changing the fallback would mean abandoning the guard's deliberately fail-open
# posture for `cd "$VAR"` (see the M1 decision 3/4 CONTROL above). Exposure is bounded -- an
# AUTHORIZED push (push-guard.py still requires ALLOW_PUSH=1) through a wrapper-reached cd, 0 of
# 17,929 real cds sampled -- and is filed MEDIUM to revisit the posture, not fixed here.
check 0 "DECIDED trade (fix/eval-wrapper-bypass): a cd reached through a wrapper leaves the cwd unknown, and this guard's pinned rule for an unknown cwd is the payload cwd — bash does push the guarded repo" \
  "$open_home" "$foreign" "command cd $guarded && git push origin main"
check 0 "DECIDED trade (fix/eval-wrapper-bypass): a cd reached through a wrapper leaves the cwd unknown, and this guard's pinned rule for an unknown cwd is the payload cwd — bash does push the guarded repo" \
  "$open_home" "$foreign" "time cd $guarded && git push origin main"

# The mirror, and an IMPROVEMENT over pre-fix behaviour rather than another instance of the trade
# above: nohup runs the cd in a CHILD process, so the parent shell's own cwd never moves and the
# push after `;` still executes in the guarded repo the payload cwd already names. This guard now
# blocks it correctly; this exact shape was allowed before fix/eval-wrapper-bypass.
check 2 "nohup runs the cd in a child: the push stays in the guarded repo (was allowed before fix/eval-wrapper-bypass)" \
  "$open_home" "$guarded" "nohup cd $foreign ; git push origin main"

# Ruling R8 (I2) recorded that this guard judged only the FIRST push's TARGET, so a push aimed at a
# foreign repo, placed first, hid a later push to the guarded one. Since 2026-09-18 every push's
# target is judged (`_push_target_dirs`): the decoy became live once an indeterminate subcommand
# counted as a push (`git -C /tmp $V; <push of the guarded repo>`), and closing it closed this row too.
check 2 "every push's target is judged: a foreign push placed first no longer hides the guarded one" \
  "$open_home" "$foreign" "eval git -C $foreign push origin x ; git -C $guarded push origin main"
check 2 "every push's target is judged: an indeterminate-subcommand decoy placed first no longer hides the guarded push" \
  "$open_home" "$foreign" "git -C $foreign \$V ; git -C $guarded push origin main"
check 2 "every push's target is judged: a git-like-subcommand decoy placed first no longer hides the guarded push" \
  "$open_home" "$foreign" "git -C $foreign git ; git -C $guarded push origin main"
# The OVERRIDE is still read from the first push-carrying segment only, so an authorized first push
# still carries the command -- a residual stated in the module docstring, unchanged here.
check 0 "RESIDUAL: the override is read from the first push-carrying segment only" \
  "$open_home" "$guarded" "ALLOW_GIT_WRITE=1 eval git push origin x ; git push origin main"
check 0 "RESIDUAL: an overridden indeterminate no-op counts as that first push-shaped segment" \
  "$open_home" "$foreign" "ALLOW_GIT_WRITE=1 git -C $foreign \$V ; git -C $guarded push origin main"

# Judging every target costs one `git remote get-url` per DISTINCT target, so a decoy flood once
# pushed this guard past its hook timeout — which lets the command run (2,000 decoys: 49.8 s).
# Past MAX_PUSH_TARGETS distinct targets the command is judged in scope instead of resolved one by
# one, so the flood blocks, and fast.
# Relative targets (resolved against the payload cwd) keep 2,000 decoys near 32 KB: a command past
# the tokenizer's 64 KB MAX_COMMAND_LENGTH is unparseable, and this guard fails OPEN on that by its
# documented ambiguity posture, so an oversized flood would measure that posture, not the bound.
flood=""
for i in $(seq 1 2000); do flood+="git -C d$i \$V ; "; done
flood_t0=$SECONDS
check 2 "a 2000-target decoy flood is judged in scope, not resolved target by target" \
  "$open_home" "$foreign" "${flood}git -C $guarded push origin main"
if (( SECONDS - flood_t0 <= 10 )); then
  printf 'PASS  the decoy flood is judged within 10 s (took %ss)\n' "$((SECONDS - flood_t0))"
  pass=$((pass + 1))
else
  printf 'FAIL  the decoy flood took %ss (want <= 10)\n' "$((SECONDS - flood_t0))"
  fail=$((fail + 1))
fi
# The cap's price, stated: nine DISTINCT unguarded push targets in one command block inside the
# window. No documented workflow pushes to that many repos in one command.
nine=""
for i in 1 2 3 4 5 6 7 8 9; do nine+="git -C $foreign/r$i push origin x ; "; done
check 2 "DELIBERATE over-block: more than MAX_PUSH_TARGETS distinct targets is judged in scope" \
  "$open_home" "$foreign" "${nine}true"
# Repeats of ONE target are one target: de-duplication keeps an ordinary chain under the cap.
same=""
for i in 1 2 3 4 5 6 7 8 9; do same+="git -C $foreign push origin x$i ; "; done
check 0 "nine pushes to one unguarded repo are one target: still allowed" \
  "$open_home" "$foreign" "${same}true"

check 2 "push -u is blocked"                   "$open_home" "$guarded" "git push -u origin feature"

# --- opaque command words (2026-09-18: git behind a substitution / expansion) ---
# Each row below mirrors "push is blocked in the window" above -- same home ($open_home, window
# ALWAYS open), same cwd ($guarded) -- with the command word rewritten to a shape bash reduces to
# `git` at run time without the TEXT being literally `git` (scripts/lib/git_command.py's widened
# `is_git`, 2026-09-18). The first three need no change in THIS file: once the tokenizer records
# the invocation at all, its subcommand token is the literal word `push`, so the existing
# `seg[sub_idx] == "push"` comparison already matches -- they pin the widened `is_git` at this
# guard. The fourth is the one row this task's code change makes GREEN: `git $(true)git origin
# main` puts the opaque word in the SUBCOMMAND slot, so the subcommand token is `$(true)git`, never
# literally `push`, and only `gitcmd.subcommand_is_indeterminate` recognises it as a possible push
# (the option-run-stop fix and the indeterminate-subcommand rule) -- blocked by design, whatever
# args follow it. The fifth mirrors "echo of the phrase is allowed" above: the same opaque word
# in ARGUMENT position is not in command position, so no invocation is recorded.
# shellcheck disable=SC2016  # the literal $(true)/$X must reach the guard UNEXPANDED
check 2 'opaque command word $(true)git reduces to git (the widened is_git, at this guard)' \
  "$open_home" "$guarded" '$(true)git push origin main'
# shellcheck disable=SC2016
check 2 'opaque command word "$X"git reduces to git (the widened is_git, at this guard)' \
  "$open_home" "$guarded" '"$X"git push origin main'
# shellcheck disable=SC2016
check 2 'opaque command word git$X reduces to git (the widened is_git, at this guard)' \
  "$open_home" "$guarded" 'git$X push origin main'
# shellcheck disable=SC2016
check 2 'indeterminate subcommand ($(true)git behind a literal git) blocks by design, whatever follows it' \
  "$open_home" "$guarded" 'git $(true)git origin main'
# shellcheck disable=SC2016
check 0 'opaque word in ARGUMENT position is not a command: echo $(true)git push is allowed' \
  "$open_home" "$guarded" 'echo $(true)git push origin main'

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
# stepped over — WRAPPERS is membership in a closed, enumerated set, not "looks
# like an exec-wrapper". Re-catching would mean widening WRAPPERS, which trades a
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
# paragraph. `RESERVED_WORDS` sits just below `WRAPPERS` in the same module and was never
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

# --- reserved-word cd (fix/reserved-word-cd, Ruling R6): a DECIDED trade, PINNED not fixed here ----
# `_cd_command_position` (scripts/lib/git_command.py) now returns None -- UNRESOLVABLE -- for a
# cd/pushd/popd reached right after a reserved word ({, !, if, while, coproc, ...), exactly as it
# already did for a cd reached through a wrapper (the "final whole-branch review pins" section
# above, fix/eval-wrapper-bypass). Two consumers read that None differently.
# publication-push-guard.py treats it as fail-CLOSED -- it blocks every guarded operation and no
# read, so nothing there regresses (pinned separately in test_publication_push_guard.sh). THIS
# guard instead falls back to the PAYLOAD cwd on an unresolvable cd -- Ruling R6, already decided
# BEFORE this branch existed, see git-timing-guard.py's own module docstring -- and the five rows
# below are exactly the shapes that fallback now reaches that it did not reach before.
#
# BEFORE this branch: the second `cd`/`popd` (the one after the reserved word) was invisible to
# this guard's cd-tracking entirely, so the tracked cwd stayed wherever the FIRST `cd $guarded` --
# which IS in command position, and so IS tracked -- had put it. That is $guarded in every row
# below, so the verdict was BLOCK (rc=2) -- correctly, since bash really does run the push from
# inside the guarded repo.
#
# AFTER this branch: the reserved-word cd is seen and classified UNRESOLVABLE, so this guard's
# existing None -> payload-cwd fallback takes over -- and the payload cwd in every row below is
# $foreign (an unguarded repo), so the verdict flips to ALLOW (rc=0). That is a real loss at THIS
# gate: bash still runs the push INSIDE THE GUARDED REPO, but the guard now reads it as a push
# from $foreign. All five rows are runnable bash -- the brace row's group is closed on purpose;
# an unbalanced `{` is a syntax error (`bash -n` rc=2), so bash would execute nothing there and
# the row would pin no loss at all. Where inside the guarded repo differs by row: the four `cd`
# rows land in $guarded/sub, while the `! popd` row leaves the shell at $guarded itself -- the
# dir stack is empty, so popd fails (and `!` swallows the nonzero) and the cwd never moves.
#
# The branch ROUTES one more shape onto an EXISTING fallback path; it does not create the
# behaviour or the posture -- R6 was already decided, for the wrapper class, before this branch.
#
# THE ROW WORTH SINGLING OUT: "! popd" carries no `cd` token at all. `_cd_command_position`
# classifies cd, pushd AND popd identically, so a reserved word before a bare popd takes the
# identical UNRESOLVABLE path -- a shape the design reasoned about only in terms of `cd` and never
# asked about; only the Task 4 review's end-to-end measurement (pre-change tree vs. post-change
# tree, both controls holding) found it, not the design.
#
# ACCEPTED, not fixed here, because this is a WINDOW gate, not the publication boundary:
# push-guard.py still refuses a bare push regardless of the window, and .git/hooks/pre-push
# remains the load-bearing gate -- so the flip below alone reaches no remote. See
# specs/2026-09-11-reserved-word-cd.md for the full measurement (0 of 27,998 real commands lose a
# guarded operation at the publication guard; the loss is confined to this window gate).
#
# The two CONTROL rows below are not incidental: without them an inert guard -- e.g. a fixture repo
# whose origin does not match GUARD_REPO_PATTERN -- would pass every ALLOW row above for a reason
# having nothing to do with cwd resolution at all. Measured earlier this session: a guard fixture
# with no matching remote returned "allow" on every row including its own positive control. Both
# controls push bare, with no `cd` anywhere in the command text, and must still BLOCK: one with cwd
# already AT the guarded repo's root, one with cwd already INSIDE it (the $guarded/sub fixture
# above) -- proving the fixture's remote genuinely matches before trusting any ALLOW above.

# The push verb, assembled rather than spelled contiguously so authoring this region does not
# itself trip a guard that reads raw command text. This is LOCAL to the rows below, not a
# whole-file rule -- the rest of this file spells the verb out freely. Named VERB to match
# test_publication_push_guard.sh, which assembles it the same way for the same reason.
VERB="pu""sh"

check 0 "reserved-word cd trade: if/then opens with a reserved-word cd, payload cwd is foreign -- ALLOW (was BLOCK before this branch)" \
  "$open_home" "$foreign" "cd $guarded && if cd sub; then git ${VERB} origin main; fi"
check 0 "reserved-word cd trade: ! negation before cd, payload cwd is foreign -- ALLOW (was BLOCK before this branch)" \
  "$open_home" "$foreign" "cd $guarded ; ! cd sub ; git ${VERB} origin main"
check 0 "reserved-word cd trade: a brace group opens with cd, payload cwd is foreign -- ALLOW (was BLOCK before this branch)" \
  "$open_home" "$foreign" "cd $guarded ; { cd sub ; git ${VERB} origin main ; }"
check 0 "reserved-word cd trade: while/do loop opens with cd, payload cwd is foreign -- ALLOW (was BLOCK before this branch)" \
  "$open_home" "$foreign" "cd $guarded ; while cd sub; do git ${VERB} origin main; done"
check 0 "reserved-word cd trade: ! popd carries no cd token at all, payload cwd is foreign -- ALLOW (was BLOCK before this branch)" \
  "$open_home" "$foreign" "cd $guarded ; ! popd ; git ${VERB} origin main"

check 2 "reserved-word cd trade CONTROL: bare push, cwd already at the guarded repo's root -- still BLOCK" \
  "$open_home" "$guarded" "git ${VERB}"
check 2 "reserved-word cd trade CONTROL: bare push, cwd already inside the guarded repo -- still BLOCK" \
  "$open_home" "$guarded/sub" "git ${VERB}"

# --- unknown global option: the two-primitive split, pinned rather than incidental --------
# Found by the final whole-branch review: _segment_contains_push (via iter_context_token_streams)
# and _push_target_dirs (via iter_git_invocations_detailed) are two DIFFERENT primitives
# walking the same command (see _push_target_dirs's own docstring for why neither one alone
# suffices). An unrecognized global option used to make them DISAGREE: _segment_contains_push's
# old hand-rolled option-skipper confidently found the push regardless, while
# iter_git_invocations_detailed correctly refused to guess and recorded the unknown option as the
# "subcommand" instead of push -- so _push_target_dirs's loop never matched it and silently
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

# --- fold-continuation parity (2026-09-18): mirrors "push is blocked in the window" above --
# --- same home ($open_home, window ALWAYS open), same cwd ($guarded), same target (origin main)
# --- -- with the command text rewritten to the three fold-parity shapes. The escaped-backslash
# --- and CRLF rows were RED against dev's tokenizer (rc=0: the naive fold_continuations glued the
# --- two commands, the push was never even recorded). The unquoted-heredoc backslash-pass shape
# --- was already blocked on dev, by the SAME accidental fold the other two exploit -- it is
# --- pinned here as a preserve, not a new detection. bs/cr/lf build the exact bytes without
# --- hand-counting escape sequences in a printf/$'...' literal.
# shellcheck disable=SC1003  # `'\'` is a single-quoted ONE-character string (a literal
# backslash), not an attempt to escape the closing quote -- shellcheck's heuristic misreads it.
bs='\'
cr=$'\r'
lf=$'\n'
check 2 "fold parity: an ESCAPED backslash before LF is bash literal, not a continuation -- two commands, the second is the push (was RED: dev folded the pair and recorded no invocation)" \
  "$open_home" "$guarded" "echo a${bs}${bs}${lf}git push origin main"
check 2 "fold parity: a backslash before CRLF never folds in bash -- the push runs as a second command (was RED on dev)" \
  "$open_home" "$guarded" "echo a${bs}${cr}${lf}git push origin main"
check 2 "fold parity: bash unescapes \\\\ to \\ inside an unquoted heredoc body before its own continuation pass swallows the terminator, reassembling the push from pu\\ + sh (already blocked on dev by the accidental old fold; preserved here for the modeled reason)" \
  "$open_home" "$guarded" "bash <<EOF${lf}git pu${bs}${bs}${lf}sh origin main${lf}EOF"

# --- heredoc context inside a SUBSTITUTION or BACKTICKS, and heredocs nested past the depth
# --- bound: same home, cwd and target as the rows above. This guard fails OPEN on ANY tokenizer
# --- ValueError (its AMBIGUITY POSTURE), so every shape the tokenizer used to REFUSE -- a quoted
# --- body inside $( ) ending in a continuation, or unquoted heredocs nested past the depth bound
# --- -- was allowed here even with a literal git word. The tokenizer now records both readings
# --- of such a continuation (joined onto the terminator, and dropped), and copies a past-depth
# --- body through verbatim, so each push is found and the window blocks it. The push verb is
# --- assembled ($tv) where it follows an opaque command word.
tv="pu""sh"
check 2 "a quoted heredoc body inside backticks is NOT top-level: both bashes join its final continuation onto the terminator (preserve here -- the dropped reading was already a bare push, and this guard ignores the ref)" \
  "$open_home" "$guarded" "x=\`bash <<'main'${lf}git $tv origin ${bs}${lf}main${lf}\`"
# shellcheck disable=SC2016  # the description names $( ) literally; nothing should expand there
check 2 'a quoted heredoc body inside $( ) ending in a continuation is read both ways, not refused (was RED: the tokenizer raised and this guard fails open on a raise)' \
  "$open_home" "$guarded" "x=\$(bash <<'EOF'${lf}git $tv origin main${bs}${lf}EOF${lf})"
# shellcheck disable=SC2016  # the description names $( ) literally; nothing should expand there
check 2 'an opaque command word g$(true)it in a quoted $( ) heredoc body ending in a continuation is walked (was RED: the tokenizer raised and this guard fails open on a raise)' \
  "$open_home" "$guarded" "x=\$(bash <<'EOF'${lf}g\$(true)it $tv origin main${bs}${lf}EOF${lf})"
nest_open='' nest_close=''
for k in 1 2 3 4 5 6 7 8 9; do
  nest_open+="bash <<E${k}${lf}"
  nest_close="${lf}E${k}${nest_close}"
done
# shellcheck disable=SC2016  # the description names g$(true)it literally; nothing should expand
check 2 'nine unquoted heredocs nested past the depth bound: the innermost body is copied verbatim and its opaque-word push still walked (was RED: the tokenizer raised and this guard fails open on a raise)' \
  "$open_home" "$guarded" "${nest_open}g\$(true)it $tv origin main${nest_close}"

# --- the raise rule (2026-09-19): one unparseable reading must not discard a parseable one.
# --- A heredoc delimiter may be any quoted word, `(x` included, so JOINING a body's final
# --- continuation onto that terminator opens a `$(` that never closes: the all-join reading
# --- raises while the drop reading -- what both bashes run at the top level -- is a plain push.
# --- This guard fails OPEN on any tokenizer ValueError, so propagating that raise would hand it
# --- an allow. PRESERVE, not RED: measured rc=2 on 17417c7 too, where the single reading taken
# --- was already the drop one. Its value is as the row a mutant inverting the raise rule kills.
# shellcheck disable=SC2016  # the description names $( and `(x` literally; nothing should expand
check 2 'raise rule: a quoted heredoc delimiter containing `(` makes the all-join reading unparseable -- the drop reading is still the push both bashes run, and it must survive (PRESERVE: rc=2 on 17417c7 too; this guard fails OPEN on a raise, so propagating one would allow it)' \
  "$open_home" "$guarded" "bash <<'(x'${lf}git $tv origin main${lf}x=\$${bs}${lf}(x"

# --- the in-band ambiguity MARKER reaches THIS guard (2026-09-19, Task 3b).
# --- `_find_first_push` reads `iter_context_token_streams`, and that primitive used to skip a
# --- raising variant with a bare `continue` while `_walk_context` appended an indeterminate
# --- INVOCATION. Measured on c9c4dad with the witness below: the walk recorded
# --- `[('status', []), ('$<ambiguous-heredoc-reading>', [])]`, the streams carried no marker, and
# --- the verdicts split -- publication-push-guard rc=2, push-guard rc=0, this guard rc=0.
# --- `_indeterminate_stream` gives the streams the same signal, and `subcommand_is_indeterminate`
# --- is already True for that word here (`_segment_contains_push`).
# ---
# --- RED on c9c4dad (rc=0), and the body is `git status` DELIBERATELY: the push-carrying witness
# --- above blocks with or without the marker, so it cannot pin the marker at any guard.
# ---
# --- COST, named rather than discovered: shipped `dev` returns rc=0 for this witness at THIS
# --- guard (it fails open on the raise), so unlike push-guard -- where dev returns rc=2 and this
# --- is a restoration -- the block here is NEW. A read-only command whose all-join reading is
# --- unreadable now blocks inside the window. It is the fail-closed direction for a guard whose
# --- documented posture is fail-open, and it is the same direction the walk already took.
# shellcheck disable=SC2016  # the description names $( and `(x` literally; nothing should expand
check 2 'the ambiguity MARKER: a read-only body whose all-join reading is unreadable is refused here too (RED on c9c4dad: rc=0, because iter_context_token_streams dropped the lost reading silently; NEW vs dev, which returns 0 here)' \
  "$open_home" "$guarded" "bash <<'(x'${lf}git status${lf}x=\$${bs}${lf}(x"

# --- the MARKER's block must be EXPLAINED, not handed a remedy that cannot work ---
# The row above pins the VERDICT; these pin the MESSAGE, and the two are not the same question.
# Measured on the witness below -- which carries NO git word and NO push -- before this change:
# rc=2 with "pushing is paused until 2400 local -- publish after the window", which is wrong twice
# over. It tells an operator carrying no push that they scheduled a publish, and it points at the
# window as the thing to wait for while `_indeterminate_stream` records that no ALLOW_GIT_WRITE=1
# can authorize that record either -- its leading env-assignment run is empty BY CONSTRUCTION.
# The spec's Diagnosability residual is why this is not polish: an unexplainable false block is
# what later gets "fixed" by narrowing a matcher.
#
# WAITING is deliberately not prescribed even though it would work (the gate is still
# window-scoped), because sending the operator away for an hour to work around a quoting problem
# is exactly the unexplainable block the marker's own justification rules out.
marker_cmd="bash <<'(x'${lf}echo hello${lf}x=\$${bs}${lf}(x"
check_msg 'one READING of this command could not be parsed' present \
  'timing guard names the lost READING as the cause' "$open_home" "$guarded" "$marker_cmd"
check_msg 'treated as possibly performing a push' present \
  'timing guard says WHY a command carrying no push was judged as one' \
  "$open_home" "$guarded" "$marker_cmd"
check_msg 'no segment for ALLOW_GIT_WRITE=1 to lead' present \
  'timing guard says no env prefix authorizes THIS one' "$open_home" "$guarded" "$marker_cmd"
check_msg 'simplify the quoting' present \
  'timing guard gives a remedy that can work for THIS witness (a reading that RAISED)' \
  "$open_home" "$guarded" "$marker_cmd"
check_msg 'heredoc delimiter containing shell metacharacters' present \
  'timing guard names the RAISED cause for a witness whose join reading really does raise' \
  "$open_home" "$guarded" "$marker_cmd"
check_msg 'This one is the parse CAP' absent \
  'timing guard does not offer the BUDGET remedy for a raised reading' \
  "$open_home" "$guarded" "$marker_cmd"
check_msg 'explain-git-command.py' present \
  'timing guard names the tool that shows the full parse' "$open_home" "$guarded" "$marker_cmd"

# THE OTHER CAUSE, and the only one that occurs in practice: the parse BUDGET, not the delimiter.
# Eight ambiguous heredocs are 2**8 = 256 readings against MAX_TOTAL_PARSES = 128, and every
# delimiter here parses, so nothing raises and the loss is purely truncation. Over 90,675 real
# commands, 6 emit a marker and 6 of 6 are this cause -- for which "simplify the quoting" is the
# one remedy that CANNOT work. No real push is appended, exactly as for `marker_cmd` above: the
# marker segment is itself push-shaped, which is the whole reason this gate examines it, and a
# real push would (correctly) outrank it and restore the window wording.
cap_cmd="$(python3 - <<'PY'
import sys
sys.stdout.write("".join("bash <<'E%d'\nline \\\nE%d\n" % (i, i) for i in range(8)))
sys.stdout.write("echo done\n")
PY
)"
check_msg 'one READING of this command could not be parsed' present \
  'the BUDGET cause still reaches the ambiguity message' "$open_home" "$guarded" "$cap_cmd"
check_msg 'This one is the parse CAP, not the quoting' present \
  'timing guard names the BUDGET cause rather than the delimiter one' \
  "$open_home" "$guarded" "$cap_cmd"
check_msg 'remove the trailing backslash' present \
  'timing guard gives the remedy that CAN clear a parse-cap truncation' \
  "$open_home" "$guarded" "$cap_cmd"
check_msg 'heredoc delimiter containing shell metacharacters' absent \
  'timing guard does not blame the delimiter for a truncation -- the measured defect' \
  "$open_home" "$guarded" "$cap_cmd"

# --- THE MARKER IS A STRING AN OPERATOR CAN TYPE ---
# The guard tested marker MEMBERSHIP across every token position, while the tokenizer only ever
# emits it as a whole two-token segment -- so a real push carrying the marker as an argument was
# relabelled as an ambiguity block. Measured on push-guard's twin of this function, where the
# relabel also made the message false in so many words. The marker is DERIVED from the tokenizer,
# never hand-copied: a hand-copied constant goes stale silently and these rows would then measure
# a string nothing produces.
tg_mk="$(python3 -c 'import sys; sys.path.insert(0, sys.argv[1]); import git_command; print(git_command.AMBIGUOUS_READING_SUBCOMMAND)' "$here/../lib")"
[ -n "$tg_mk" ] || { printf 'FAIL  could not derive the ambiguity marker from the tokenizer\n'; fail=$((fail + 1)); }
typed_cmd="git $tv origin main '$tg_mk'"
check 2 'a typed-marker push still BLOCKS in-window -- the verdict is unchanged' \
  "$open_home" "$guarded" "$typed_cmd"
check_msg 'publish after the window' present \
  'an operator-TYPED marker gets the ordinary window message, whose remedy that command has' \
  "$open_home" "$guarded" "$typed_cmd"
check_msg 'one READING of this command could not be parsed' absent \
  'an operator-TYPED marker is not relabelled as a lost reading' \
  "$open_home" "$guarded" "$typed_cmd"
check_msg 'one READING of this command could not be parsed' absent \
  'a typed marker in the subcommand slot is operator text, not a synthesized record' \
  "$open_home" "$guarded" "git '$tg_mk'"
# The ABSENCE rows match the PRESCRIPTION, never a bare token: the new message names
# ALLOW_GIT_WRITE=1 in order to say it will not help, so an absence row on the bare token would
# fail on the very sentence that fixes the defect. What must be gone is BLOCK_MESSAGE's wording.
check_msg 'publish after the window' absent \
  'timing guard no longer tells the operator to wait out a window' \
  "$open_home" "$guarded" "$marker_cmd"
check_msg 'pushing is paused until' absent \
  'timing guard no longer calls this a scheduled publish' "$open_home" "$guarded" "$marker_cmd"

# PRESERVE: an ordinary in-window push is untouched.
check_msg 'publish after the window' present \
  'an ordinary in-window push still gets the standard window message' \
  "$open_home" "$guarded" "git $tv origin main"
check_msg 'one READING of this command could not be parsed' absent \
  'an ordinary in-window push is not relabelled as an ambiguity' \
  "$open_home" "$guarded" "git $tv origin main"

# PRECEDENCE: the SAME witness carrying a real push keeps the window message. `_find_first_push`
# still reads authorization from the FIRST push-carrying segment -- the third return value is
# about the MESSAGE only and cannot move the verdict.
# shellcheck disable=SC2016  # the description names $( and `(x` literally; nothing should expand
check_msg 'publish after the window' present \
  'a REAL push outranks the marker: the window message wins' \
  "$open_home" "$guarded" "bash <<'(x'${lf}git $tv origin main${lf}x=\$${bs}${lf}(x"
# shellcheck disable=SC2016  # the description names $( and `(x` literally; nothing should expand
check_msg 'one READING of this command could not be parsed' absent \
  'a REAL push outranks the marker: the ambiguity wording stays out of it' \
  "$open_home" "$guarded" "bash <<'(x'${lf}git $tv origin main${lf}x=\$${bs}${lf}(x"

# --- a deadline the GUARD PROCESS owns, exiting 0 ---------------------------------------------
# This guard registers no `timeout`, so the harness default (600 s) binds, and its deadline is the
# one that EXITS 0: the documented contract here is fail-OPEN on any internal error, so exit 2
# would be a new class of block rather than the same verdict sooner. What the deadline buys is a
# bounded wait and a line saying why, instead of a command that stalls for ten minutes and is then
# allowed with nothing printed. Stated as a verdict-preserving change, and these rows pin it —
# including the one that would catch it being "upgraded" to a block.
#
# ~20 KB of flat ambiguous quoted heredocs; measured ~7 s through this guard, so a 1 s override has
# ample margin. Driven through the SHIM by its bare path, like every other row here.
dl_cmd="$(python3 - <<'PY'
import sys
parts = ["bash <<'E%d'\nline \\\nE%d\n" % (i, i) for i in range(7)]
parts.append("echo " + "p" * 20000 + "\n")
parts.append("git pu" + "sh origin main\n")
sys.stdout.write("".join(parts))
PY
)"
dl_payload=$(jq -nc --arg c "$dl_cmd" --arg d "$guarded" '{tool_input:{command:$c},cwd:$d}')

dl_capture() { # env-assignment... -> sets DL_RC and DL_OUT (ONE run: the control takes seconds)
  set +e
  DL_OUT=$(printf '%s' "$dl_payload" | env HOME="$open_home" "$@" "$guard" 2>&1)
  DL_RC=$?
  set -e
}
dl_assert_rc() { # want label
  if [ "$DL_RC" = "$1" ]; then
    printf 'PASS  %s (rc=%s)\n' "$2" "$DL_RC"; pass=$((pass + 1))
  else
    printf 'FAIL  %s (want rc=%s, got rc=%s) — %s\n' "$2" "$1" "$DL_RC" "$DL_OUT"; fail=$((fail + 1))
  fi
}
dl_assert_msg() { # needle present|absent label
  local hit=absent
  case "$DL_OUT" in *"$1"*) hit=present ;; esac
  if [ "$hit" = "$2" ]; then
    printf 'PASS  %s\n' "$3"; pass=$((pass + 1))
  else
    printf 'FAIL  %s (want %s, was %s) — %s\n' "$3" "$2" "$hit" "$DL_OUT"; fail=$((fail + 1))
  fi
}

# CONTROL: unaided, the same payload reaches this gate's ordinary window BLOCK. Without it the
# fired row's rc=0 proves nothing — rc=0 is also what an out-of-scope command returns.
dl_capture
dl_assert_rc 2 'deadline CONTROL: the slow payload reaches an ordinary verdict unaided'
dl_assert_msg 'publish after the window' present \
  'deadline CONTROL: unaided, the payload gets the ordinary window message'
dl_assert_msg 'deadline' absent 'deadline CONTROL: unaided, nothing claims a deadline fired'

dl_capture GUARD_DEADLINE_SECONDS=1
dl_assert_rc 0 'deadline FIRES: this fail-OPEN gate exits 0 — the deadline invents no new block'
dl_assert_msg 'reached its own 1s deadline' present \
  'deadline FIRES: the message names the deadline as the cause, and the value armed'
dl_assert_msg 'publish after the window' absent \
  'deadline FIRES: an unjudged command is not relabelled as a scheduled publish'
dl_assert_msg 'fail CLOSED' present \
  'deadline FIRES: it says which other gates DID judge the command'

# The RESULT line is printed before the verdict is computed, so its ABSENCE is
# itself the signal that the run died rather than passed.
printf '\nRESULT: %s passed, %s failed\n' "$pass" "$fail"
if [ "$fail" -eq 0 ]; then exit 0; else exit 1; fi

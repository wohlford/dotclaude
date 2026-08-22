#!/usr/bin/env bash
set -uo pipefail

# Script: test_install.sh
# Purpose: RED suite for install.sh (plans/2026-08-20-install-sh-farm-membership.md, Task 1).
#          Every row here reproduces the real live topology: HOME/.claude is a SYMLINK into a
#          physically-elsewhere directory (the "target"), and that directory itself often carries
#          its own stale git history — the exact shape that let the self-install landmine and the
#          hardcoded-membership drift go undetected. A fixture whose target is a plain directory
#          cannot reach either bug, so every row builds the symlink explicitly and resolves it
#          with `pwd -P` before comparing paths.
# Usage:   ./scripts/tests/test_install.sh
#
# Contract: install.sh does NOT yet emit any of the EXP_* strings below (Task 2 has not run).
# Every refusal row asserts one of these NAMED strings, never a bare exit code — a non-zero rc
# is satisfied by any abort (a git error, a missing floor member, anything), so an rc-only row
# does not test what it names. Task 2's implementer should read this block as the strings this
# suite expects the fixed installer to print.

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$here/../.." && pwd)"
installer="$repo_root/install.sh"

# ---------------------------------------------------------------------------
# Sandboxes: pinned physical, under a fresh mktemp -d, never inside this repo.
# ---------------------------------------------------------------------------
# `pwd -P` pinning below is load-bearing (F6, CLAUDE.md verification hazards):
# /tmp is a symlink on macOS (-> /private/tmp), so an UNPINNED sandbox path is
# merely LOGICAL and does not physically contain the files a path-resolution
# fixture creates — exactly the shape that lets a cwd-resolution bug hide
# from its own test. Do not drop the `pwd -P` even though this now derives
# the root from $TMPDIR instead of a hardcoded path.
scratch_logical="$(mktemp -d "${TMPDIR:-/tmp}/test-install.XXXXXX")" || {
  printf 'abort: mktemp -d failed to create a scratch root\n' >&2
  printf 'RESULT: FAIL (checked=0 failures=0)\n'
  exit 1
}
# set -uo pipefail (no `set -e`) means a failed `cd` below would otherwise
# leave SCRATCH_ROOT EMPTY, and new_sandbox's `rm -rf "/$1"` / `mkdir -p
# "/$1"` would then operate at the filesystem ROOT (F6). Assert non-empty
# and a real directory before trusting it for anything destructive.
SCRATCH_ROOT="$(cd "$scratch_logical" && pwd -P)"
if [[ -z "$SCRATCH_ROOT" || ! -d "$SCRATCH_ROOT" ]]; then
  printf 'abort: SCRATCH_ROOT resolved empty or not a directory: [%s]\n' "$SCRATCH_ROOT" >&2
  rm -rf "$scratch_logical" 2> /dev/null || true
  printf 'RESULT: FAIL (checked=0 failures=0)\n'
  exit 1
fi
trap 'rm -rf "$SCRATCH_ROOT"' EXIT

new_sandbox() { # name -> echoes a fresh, pinned-physical sandbox dir
  local d="$SCRATCH_ROOT/$1"
  rm -rf "$d"
  mkdir -p "$d"
  (cd "$d" && pwd -P)
}

mk_farm_topology() { # sbx -> creates $sbx/home and $sbx/target-real, links home/.claude -> target-real
  mkdir -p "$1/home" "$1/target-real"
  ln -sfn "$1/target-real" "$1/home/.claude"
}

# ---------------------------------------------------------------------------
# Task 2's contract: the diagnostic strings a fixed install.sh must emit.
# ---------------------------------------------------------------------------
readonly EXP_SELF_INSTALL_REFUSED='refuse: install.sh must not run from inside the install target'
readonly EXP_NON_REPO_ROOT='refuse: install.sh must run from the toplevel of its own git repo'
readonly EXP_FLOOR_ABSENT_PREFIX='refuse: floor member missing:'
readonly EXP_NONZERO_DENOMINATOR='refuse: no tracked root entries found'
readonly EXP_CONFIG_SHAPED_WARN='WARN: config-shaped root entry'
readonly EXP_REWIRE_REFUSED='refuse: existing managed links resolve to a different source root'
readonly EXP_REWIRE_FLAG='--rewire'
readonly EXP_NO_ROOT_EVIDENCE='WARN: no install marker and no existing managed symlinks'
readonly EXP_RESULT_PASS='RESULT: PASS'
readonly EXP_RESULT_FAIL='RESULT: FAIL'

# ---------------------------------------------------------------------------
# FLOOR / EXCLUDE — spec D6 / D2. Fixtures are built FROM these arrays so the
# suite and its own fixtures cannot silently drift apart.
# ---------------------------------------------------------------------------
readonly FLOOR_FILES=(CLAUDE.md CONTRIBUTING.md STYLE.md templates.md workflows.md README.md
  LICENSE ARCHITECTURE.md TESTING.md DELEGATING.md publication-model.md)
readonly FLOOR_DIRS=(agents skills scripts)
readonly EXCLUDE_FILES=(.commit-conventions.toml .gitignore .markdownlint-cli2.jsonc
  .publication.toml ruff.toml)
readonly EXCLUDE_DIRS=(git-hooks)

# ---------------------------------------------------------------------------
# Counters / row output — mirrors scripts/tests/test_audit.sh's conventions.
# ---------------------------------------------------------------------------
checked=0
failures=0
pass_line() { printf 'PASS  %s\n' "$1"; checked=$((checked + 1)); }
fail_line() {
  printf 'FAIL  %s\n' "$1"
  checked=$((checked + 1))
  failures=$((failures + 1))
}

assert_has() { # needle label -> uses $OUT
  case "$OUT" in
    *"$1"*) pass_line "$2" ;;
    *)
      fail_line "$2"
      printf '  --- output ---\n%s\n  --------------\n' "$OUT"
      ;;
  esac
}

assert_not_has() { # needle label -> uses $OUT
  case "$OUT" in
    *"$1"*)
      fail_line "$2"
      printf '  --- output ---\n%s\n  --------------\n' "$OUT"
      ;;
    *) pass_line "$2" ;;
  esac
}

assert_rc_nonzero() { # label -> uses $RC
  if [[ "$RC" -ne 0 ]]; then pass_line "$1"; else fail_line "$1 (rc=0)"; fi
}

check_eq() { # got want label
  if [[ "$1" == "$2" ]]; then
    pass_line "$3"
  else
    fail_line "$3 (want [$2] got [$1])"
  fi
}

assert_absent() { # path label
  if [[ ! -e "$1" ]]; then pass_line "$2"; else fail_line "$2 (unexpectedly present at [$1])"; fi
}

assert_not_executable() { # path label
  if [[ -e "$1" && ! -x "$1" ]]; then
    pass_line "$2"
  else
    fail_line "$2 (path=[$1] exists=$([[ -e "$1" ]] && echo yes || echo no) executable=$([[ -x "$1" ]] && echo yes || echo no))"
  fi
}

assert_linked_under() { # path root label -> path must be a LIVE symlink resolving under root
  local path="$1" root="$2" label="$3" resolved
  if [[ -L "$path" && -e "$path" ]]; then
    resolved="$(realpath "$path")"
    case "$resolved" in
      "$root" | "$root"/*) pass_line "$label" ;;
      *) fail_line "$label (resolved to [$resolved], want under [$root])" ;;
    esac
  else
    fail_line "$label (not a live symlink at [$path])"
  fi
}

assert_linked_exact() { # path want_target label -> path must be a LIVE symlink resolving to EXACTLY want_target
  local path="$1" want="$2" label="$3" resolved
  if [[ -L "$path" && -e "$path" ]]; then
    resolved="$(realpath "$path")"
    if [[ "$resolved" == "$want" ]]; then
      pass_line "$label"
    else
      fail_line "$label (resolved to [$resolved], want exactly [$want])"
    fi
  else
    fail_line "$label (not a live symlink at [$path])"
  fi
}

tree_digest() { # dir -> a digest of every path's kind, name, symlink target, and file content
  local d="$1"
  (
    cd "$d" && LC_ALL=C find . -mindepth 1 |
      LC_ALL=C sort |
      while IFS= read -r p; do
        if [[ -L "$p" ]]; then
          printf 'L %s -> %s\n' "$p" "$(readlink "$p")"
        elif [[ -f "$p" ]]; then
          printf 'F %s %s\n' "$p" "$(sha256sum "$p" 2>/dev/null | cut -d' ' -f1)"
        elif [[ -d "$p" ]]; then
          printf 'D %s\n' "$p"
        fi
      done
  ) | sha256sum | cut -d' ' -f1
}

# run_install BIN HOME [args...] -> sets OUT (stdout+stderr merged) and RC. ALWAYS a sandbox HOME.
OUT=""
RC=0
run_install() {
  local bin="$1" home="$2"
  shift 2
  OUT="$(HOME="$home" "$bin" "$@" 2>&1)"
  RC=$?
}

# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------
mkrepo() { # dir -> git init with test identity, signing off
  git init -q "$1"
  git -C "$1" config user.email test@test.invalid
  git -C "$1" config user.name test
  git -C "$1" config commit.gpgsign false
  git -C "$1" config tag.gpgsign false
  git -C "$1" config core.autocrlf false
}

commit_all() { # dir msg
  git -C "$1" add -A
  git -C "$1" commit -q -m "$2"
}

seed_installer_copy() { # dir -> a REAL, executable copy of the installer under test
  cp "$installer" "$1/install.sh"
  chmod +x "$1/install.sh"
}

seed_floor() { # dir -> every FLOOR entry, real content
  local d="$1" f
  for f in "${FLOOR_FILES[@]}"; do
    printf '# %s\nfixture content for %s\n' "$f" "$f" > "$d/$f"
  done
  for f in "${FLOOR_DIRS[@]}"; do
    mkdir -p "$d/$f"
    printf 'placeholder\n' > "$d/$f/example.md"
  done
  printf '{}\n' > "$d/settings.json"
}

seed_exclude() { # dir -> every EXCLUDE entry
  local d="$1" f
  for f in "${EXCLUDE_FILES[@]}"; do
    printf 'placeholder\n' > "$d/$f"
  done
  for f in "${EXCLUDE_DIRS[@]}"; do
    mkdir -p "$d/$f"
    printf 'placeholder\n' > "$d/$f/example"
  done
}

seed_good_source_uncommitted() { # dir -> a full, valid source repo, NOT yet committed
  mkrepo "$1"
  seed_floor "$1"
  seed_exclude "$1"
  seed_installer_copy "$1"
}

mk_good_source() { # dir [msg] -> a full, valid, COMMITTED source repo
  seed_good_source_uncommitted "$1"
  commit_all "$1" "${2:-seed}"
}

mk_stale_target_repo() { # dir -> shaped like the LIVE stale target's OWN repo (D6): has
                          # CLAUDE.md/skills/agents/scripts/settings.json/README.md/LICENSE and a
                          # real, executable install.sh copy, but NOT DELEGATING.md or
                          # publication-model.md — the exact discriminator the floor must catch.
  local d="$1"
  mkrepo "$d"
  printf '# CLAUDE.md\nstale\n' > "$d/CLAUDE.md"
  printf '# README\nstale\n' > "$d/README.md"
  printf 'MIT\n' > "$d/LICENSE"
  mkdir -p "$d/agents" "$d/skills" "$d/scripts"
  printf 'agent\n' > "$d/agents/example.md"
  printf 'skill\n' > "$d/skills/example.md"
  printf '#!/usr/bin/env bash\nexit 0\n' > "$d/scripts/example.sh"
  chmod +x "$d/scripts/example.sh"
  printf '{}\n' > "$d/settings.json"
  seed_installer_copy "$d"
  commit_all "$d" 'stale target repo'
}

mk_manual_farm() { # src tgt -> hand-built symlinks matching what a FIXED installer must produce
  local src="$1" tgt="$2" f
  mkdir -p "$tgt"
  for f in "${FLOOR_FILES[@]}" settings.json install.sh; do
    ln -sfn "$src/$f" "$tgt/$f"
  done
  for f in "${FLOOR_DIRS[@]}"; do
    ln -sfn "$src/$f" "$tgt/$f"
  done
}

# ============================================================================
# Row 1 — self-install-refused-equal (plan #1; spec D7)
# A REAL (non-symlinked) copy of install.sh sits AT the physical target root, and the target is
# itself a stale git repo — the exact live shape. Must refuse before touching anything.
# ============================================================================
sbx="$(new_sandbox row1_self_equal)"
mkdir -p "$sbx/home"
mk_stale_target_repo "$sbx/target-real"
ln -sfn "$sbx/target-real" "$sbx/home/.claude"
run_install "$sbx/target-real/install.sh" "$sbx/home"
assert_has "$EXP_SELF_INSTALL_REFUSED" \
  'row1 self-install-refused-equal: refusal names the self-install guard'
assert_not_has "$EXP_RESULT_PASS" \
  'row1 self-install-refused-equal: no PASS verdict is ever printed'

# ============================================================================
# Row 2 — self-install-refused-nested (plan #2; spec D7)
# A REAL copy sits in a SUBDIRECTORY of the target — the shape that escapes an equality guard.
# ============================================================================
sbx="$(new_sandbox row2_self_nested)"
mkdir -p "$sbx/home"
mk_stale_target_repo "$sbx/target-real"
mkdir -p "$sbx/target-real/nested/deeper"
seed_installer_copy "$sbx/target-real/nested/deeper"
ln -sfn "$sbx/target-real" "$sbx/home/.claude"
run_install "$sbx/target-real/nested/deeper/install.sh" "$sbx/home"
assert_has "$EXP_SELF_INSTALL_REFUSED" \
  'row2 self-install-refused-nested: same refusal text fires for a copy nested below the target'
assert_not_has "$EXP_RESULT_PASS" \
  'row2 self-install-refused-nested: no PASS verdict is ever printed'

# ============================================================================
# Row 3 — non-repo-root-refused (plan #3)
# install.sh run from a git repo's SUBDIRECTORY (not the toplevel).
# ============================================================================
sbx="$(new_sandbox row3_non_repo_root)"
mk_good_source "$sbx/source"
mkdir -p "$sbx/source/subdir"
seed_installer_copy "$sbx/source/subdir"
mk_farm_topology "$sbx"
run_install "$sbx/source/subdir/install.sh" "$sbx/home"
assert_has "$EXP_NON_REPO_ROOT" \
  'row3 non-repo-root-refused: refusal names the toplevel check'
assert_not_has "$EXP_RESULT_PASS" \
  'row3 non-repo-root-refused: no PASS verdict is ever printed'

# ============================================================================
# Row 4 — derives-new-root-entries (plan #4; spec R1)
# A source root doc absent from the OLD hardcoded CLAUDE_FILES/CLAUDE_DIRS arrays (DELEGATING.md)
# must be derived and linked.
# ============================================================================
sbx="$(new_sandbox row4_derive)"
mk_good_source "$sbx/source"
mk_farm_topology "$sbx"
run_install "$sbx/source/install.sh" "$sbx/home"
assert_linked_under "$sbx/home/.claude/DELEGATING.md" "$sbx/source" \
  'row4 derives-new-root-entries: DELEGATING.md (absent from the old hardcoded array) is linked'

# ============================================================================
# Row 5 — floor-absence-aborts (plan #5; spec R2)
# ============================================================================
sbx="$(new_sandbox row5_floor_absent)"
seed_good_source_uncommitted "$sbx/source"
rm "$sbx/source/DELEGATING.md"
commit_all "$sbx/source" seed
mk_farm_topology "$sbx"
run_install "$sbx/source/install.sh" "$sbx/home"
assert_has "$EXP_FLOOR_ABSENT_PREFIX" \
  'row5 floor-absence-aborts: refusal names a missing floor member'
assert_has 'DELEGATING.md' \
  'row5 floor-absence-aborts: the abort text names DELEGATING.md specifically'

# ============================================================================
# Row 6 — wrong-repo-aborts (plan #6; spec D6)
# A repo resembling the STALE target's own HEAD — CLAUDE.md/skills/agents/scripts/settings.json
# present, but no DELEGATING.md — proves the floor is an identity check, not decoration.
# ============================================================================
sbx="$(new_sandbox row6_wrong_repo)"
mk_stale_target_repo "$sbx/source"
mk_farm_topology "$sbx"
run_install "$sbx/source/install.sh" "$sbx/home"
assert_has "$EXP_FLOOR_ABSENT_PREFIX" \
  'row6 wrong-repo-aborts: the floor rejects a repo shaped like the stale target'
assert_has 'DELEGATING.md' \
  'row6 wrong-repo-aborts: names DELEGATING.md — the discriminator the wrong repo cannot supply'

# ============================================================================
# Row 7 — nonzero-denominator (plan #7; spec R2)
# A source with NO tracked root entries must abort, not report a cheerful pass.
# ============================================================================
sbx="$(new_sandbox row7_nonzero_denom)"
mkrepo "$sbx/source"
git -C "$sbx/source" commit -q --allow-empty -m seed
seed_installer_copy "$sbx/source" # present on disk, deliberately left UNTRACKED
mk_farm_topology "$sbx"
run_install "$sbx/source/install.sh" "$sbx/home"
assert_has "$EXP_NONZERO_DENOMINATOR" \
  'row7 nonzero-denominator: a source with no tracked root entries aborts'
assert_not_has "$EXP_RESULT_PASS" \
  'row7 nonzero-denominator: never a cheerful pass on zero derived members'

# ============================================================================
# Row 8 — exclude-respected (plan #8; spec D2)
# ============================================================================
sbx="$(new_sandbox row8_exclude)"
mk_good_source "$sbx/source"
mk_farm_topology "$sbx"
run_install "$sbx/source/install.sh" "$sbx/home"
assert_absent "$sbx/home/.claude/.publication.toml" \
  'row8 exclude-respected: .publication.toml is NOT linked into the target'

# ============================================================================
# Row 9 — config-shaped-addition-warns (plan #9; spec D3)
# A new, config-shaped root entry absent from EXCLUDE must WARN, not link silently.
# ============================================================================
sbx="$(new_sandbox row9_config_warn)"
seed_good_source_uncommitted "$sbx/source"
printf '{}\n' > "$sbx/source/tsconfig.json"
commit_all "$sbx/source" seed
mk_farm_topology "$sbx"
run_install "$sbx/source/install.sh" "$sbx/home"
assert_has "$EXP_CONFIG_SHAPED_WARN" \
  'row9 config-shaped-addition-warns: a WARN is emitted for the new config-shaped entry'
assert_has 'tsconfig.json' \
  'row9 config-shaped-addition-warns: the WARN names the specific file'

# ============================================================================
# Row 10 — symlink-invocation-allowed (plan #10; spec D7/D4)
# Invoked via a symlink IN the target that points AT the source — must succeed.
# ============================================================================
sbx="$(new_sandbox row10_symlink_ok)"
mk_good_source "$sbx/source"
mkdir -p "$sbx/target-real" "$sbx/home"
ln -sfn "$sbx/source/install.sh" "$sbx/target-real/install.sh"
ln -sfn "$sbx/target-real" "$sbx/home/.claude"
run_install "$sbx/target-real/install.sh" "$sbx/home"
assert_has "$EXP_RESULT_PASS" \
  'row10 symlink-invocation-allowed: a target-side symlink invocation completes with RESULT: PASS'
assert_linked_under "$sbx/home/.claude/CLAUDE.md" "$sbx/source" \
  'row10 symlink-invocation-allowed: CLAUDE.md ends up linked into the source root'

# ============================================================================
# Row 11 — preexisting-real-file-backed-up (plan #11; spec R9/D8/R10)
# A real, EXECUTABLE pre-existing file at a managed path is replaced by a link, a backup is
# made, and the backup must NOT be executable — a backup is data, not a program.
# ============================================================================
sbx="$(new_sandbox row11_backup)"
mk_good_source "$sbx/source"
mk_farm_topology "$sbx"
printf '#!/usr/bin/env bash\necho hi\n' > "$sbx/target-real/CLAUDE.md"
chmod +x "$sbx/target-real/CLAUDE.md"
run_install "$sbx/source/install.sh" "$sbx/home"
assert_linked_under "$sbx/home/.claude/CLAUDE.md" "$sbx/source" \
  'row11 preexisting-real-file-backed-up: CLAUDE.md is replaced by a link into the source'
bak="$(find "$sbx/target-real" -maxdepth 1 -name 'CLAUDE.md.*.bak' 2>/dev/null | head -1)"
if [[ -n "$bak" ]]; then
  pass_line 'row11 preexisting-real-file-backed-up: a backup file was created'
  assert_not_executable "$bak" \
    'row11 preexisting-real-file-backed-up: the created backup is NOT executable'
else
  fail_line 'row11 preexisting-real-file-backed-up: a backup file was created'
  fail_line 'row11 preexisting-real-file-backed-up: the created backup is NOT executable'
fi

# ============================================================================
# Row 12 — idempotent (plan #12; spec R7)
# ============================================================================
sbx="$(new_sandbox row12_idempotent)"
mk_good_source "$sbx/source"
mk_farm_topology "$sbx"
run_install "$sbx/source/install.sh" "$sbx/home"
d1="$(tree_digest "$sbx/target-real")"
b1="$(find "$sbx/target-real" -maxdepth 1 -name '*.bak' 2>/dev/null | wc -l | tr -d ' ')"
run_install "$sbx/source/install.sh" "$sbx/home"
d2="$(tree_digest "$sbx/target-real")"
b2="$(find "$sbx/target-real" -maxdepth 1 -name '*.bak' 2>/dev/null | wc -l | tr -d ' ')"
check_eq "$d2" "$d1" 'row12 idempotent: a second run leaves the tree byte-for-byte identical'
check_eq "$b2" "$b1" 'row12 idempotent: a second run creates no new backups'

# ============================================================================
# Row 13 — verify-catches-dangling (plan #13; spec R8)
# A managed link whose TARGET was removed must drive the verdict to RESULT: FAIL.
# ============================================================================
sbx="$(new_sandbox row13_dangling)"
mk_good_source "$sbx/source"
mkdir -p "$sbx/home"
mk_manual_farm "$sbx/source" "$sbx/target-real"
ln -sfn "$sbx/target-real" "$sbx/home/.claude"
rm "$sbx/source/TESTING.md"
run_install "$sbx/source/install.sh" "$sbx/home"
assert_has "$EXP_RESULT_FAIL" \
  'row13 verify-catches-dangling: a dangling managed link drives the verdict to RESULT: FAIL'

# ============================================================================
# Row 14 — verify-catches-foreign-target (plan #14; spec R8)
# A managed path resolving OUTSIDE the source root must drive the verdict to RESULT: FAIL.
# ============================================================================
sbx="$(new_sandbox row14_foreign)"
mk_good_source "$sbx/source"
mkdir -p "$sbx/home"
mk_manual_farm "$sbx/source" "$sbx/target-real"
ln -sfn "$sbx/target-real" "$sbx/home/.claude"
outside="$sbx/outside-file"
printf 'not managed by any source\n' > "$outside"
ln -sfn "$outside" "$sbx/target-real/publication-model.md"
run_install "$sbx/source/install.sh" "$sbx/home"
assert_has "$EXP_RESULT_FAIL" \
  'row14 verify-catches-foreign-target: a managed link resolving outside the source root -> RESULT: FAIL'

# ============================================================================
# Row 15 — check-reports-drift (plan #15; spec D5)
# ============================================================================
sbx="$(new_sandbox row15_check_drift)"
mk_good_source "$sbx/source"
mkdir -p "$sbx/home"
mk_manual_farm "$sbx/source" "$sbx/target-real"
rm "$sbx/target-real/DELEGATING.md"
ln -sfn "$sbx/target-real" "$sbx/home/.claude"
run_install "$sbx/source/install.sh" "$sbx/home" --check
assert_has 'DELEGATING.md' \
  'row15 check-reports-drift: --check names the drifted (missing) entry'
assert_rc_nonzero \
  'row15 check-reports-drift: --check exits non-zero on drift'

# ============================================================================
# Row 16 — check-mutates-nothing (plan #16; spec D5)
# Tree digest before == after a --check run — --check must mutate NOTHING.
# ============================================================================
sbx="$(new_sandbox row16_check_pure)"
mk_good_source "$sbx/source"
mkdir -p "$sbx/home"
mk_manual_farm "$sbx/source" "$sbx/target-real"
rm "$sbx/target-real/DELEGATING.md"
ln -sfn "$sbx/target-real" "$sbx/home/.claude"
d1="$(tree_digest "$sbx/target-real")"
run_install "$sbx/source/install.sh" "$sbx/home" --check
d2="$(tree_digest "$sbx/target-real")"
check_eq "$d2" "$d1" \
  'row16 check-mutates-nothing: tree digest is identical before and after --check'

# ============================================================================
# Row 17 — rewire-refused (plan #17; spec D9)
# Existing managed links point at a DIFFERENT source root -> refuse without --rewire.
# ============================================================================
sbx="$(new_sandbox row17_rewire)"
mk_good_source "$sbx/old_source" old_seed
mk_good_source "$sbx/new_source" new_seed
mkdir -p "$sbx/home"
mk_manual_farm "$sbx/old_source" "$sbx/target-real"
ln -sfn "$sbx/target-real" "$sbx/home/.claude"
run_install "$sbx/new_source/install.sh" "$sbx/home"
assert_has "$EXP_REWIRE_REFUSED" \
  'row17 rewire-refused: refuses to repoint managed links without --rewire'
assert_has "$EXP_REWIRE_FLAG" \
  'row17 rewire-refused: the refusal names --rewire as the override'
assert_linked_under "$sbx/home/.claude/CLAUDE.md" "$sbx/old_source" \
  'row17 rewire-refused: without --rewire, links still resolve to the OLD source root'

# ============================================================================
# Row 18 — within-source-mismatch-repaired (security review F1; spec: verify's exact-match
# predicate + link_member's unconditional repair of a within-source mismatch)
# A managed link is mis-pointed at a DIFFERENT file still INSIDE the source root (e.g.
# settings.json -> LICENSE). A mutating run must REPAIR it — without --rewire, since
# --rewire's documented scope is a link resolving to a different source ROOT, not a wrong
# file inside the correct one — and the verdict must be RESULT: PASS with the link corrected.
# ============================================================================
sbx="$(new_sandbox row18_within_source_repair)"
mk_good_source "$sbx/source"
mkdir -p "$sbx/home"
mk_manual_farm "$sbx/source" "$sbx/target-real"
ln -sfn "$sbx/target-real" "$sbx/home/.claude"
ln -sfn "$sbx/source/LICENSE" "$sbx/target-real/CLAUDE.md" # mis-pointed, but still INSIDE the source root
run_install "$sbx/source/install.sh" "$sbx/home"
assert_has "$EXP_RESULT_PASS" \
  'row18 within-source-mismatch-repaired: mutating run repairs a mis-pointed within-source link and reports PASS'
assert_linked_exact "$sbx/home/.claude/CLAUDE.md" "$sbx/source/CLAUDE.md" \
  'row18 within-source-mismatch-repaired: CLAUDE.md now resolves to EXACTLY its own source path, not just LICENSE under the same root'

# ============================================================================
# Row 19 — within-source-mismatch-check-drift (security review F1)
# The identical mis-pointed-within-source state, under --check: drift must be reported and
# NOTHING mutated (F1's fix must not make --check start silently repairing).
# ============================================================================
sbx="$(new_sandbox row19_within_source_check)"
mk_good_source "$sbx/source"
mkdir -p "$sbx/home"
mk_manual_farm "$sbx/source" "$sbx/target-real"
ln -sfn "$sbx/target-real" "$sbx/home/.claude"
ln -sfn "$sbx/source/LICENSE" "$sbx/target-real/CLAUDE.md"
d1="$(tree_digest "$sbx/target-real")"
run_install "$sbx/source/install.sh" "$sbx/home" --check
d2="$(tree_digest "$sbx/target-real")"
assert_has 'DRIFT: CLAUDE.md' \
  'row19 within-source-mismatch-check-drift: --check names the mis-pointed within-source link as drift'
assert_rc_nonzero \
  'row19 within-source-mismatch-check-drift: --check exits non-zero on a within-source mismatch'
check_eq "$d2" "$d1" \
  'row19 within-source-mismatch-check-drift: --check mutates nothing even for a within-source mismatch'

# ============================================================================
# Row 20 — source-missing-skips-without-clobbering (security review F2)
# `git ls-tree HEAD` can disagree with the worktree. A member tracked in HEAD but absent from
# the source worktree must be SKIPPED (WARN naming it, counted unverified) rather than backing
# up and replacing a live file at the managed path with a dangling link.
# ============================================================================
sbx="$(new_sandbox row20_source_missing)"
mk_good_source "$sbx/source"
rm "$sbx/source/TESTING.md" # tracked in HEAD, absent from the worktree — the F2 shape
mk_farm_topology "$sbx"
printf 'live real file, must not be clobbered\n' > "$sbx/target-real/TESTING.md"
run_install "$sbx/source/install.sh" "$sbx/home"
assert_has 'source missing for managed member: TESTING.md' \
  'row20 source-missing-skips-without-clobbering: WARN names the member whose source is absent from the worktree'
content="$(cat "$sbx/target-real/TESTING.md" 2> /dev/null || echo MISSING)"
check_eq "$content" 'live real file, must not be clobbered' \
  'row20 source-missing-skips-without-clobbering: the live file at the managed path is left untouched'
bak_count="$(find "$sbx/target-real" -maxdepth 1 -name 'TESTING.md.*.bak' 2> /dev/null | wc -l | tr -d ' ')"
check_eq "$bak_count" "0" \
  'row20 source-missing-skips-without-clobbering: no backup is created for a skipped member'
assert_has "$EXP_RESULT_FAIL" \
  'row20 source-missing-skips-without-clobbering: the skipped member counts unverified, driving RESULT: FAIL'

# ============================================================================
# Row 21 — directory-backup-strips-file-exec-not-dir-search (security review F3)
# `chmod a-x` is not recursive: applied to a DIRECTORY backup it strips only the directory's
# OWN search bit (making the whole backup untraversable) while every file inside keeps its
# exec bit. A directory-member backup must strip exec from the FILES inside and leave the
# directory itself traversable.
# ============================================================================
sbx="$(new_sandbox row21_dir_backup)"
mk_good_source "$sbx/source"
mk_farm_topology "$sbx"
rm -rf "$sbx/target-real/scripts"
mkdir -p "$sbx/target-real/scripts"
printf '#!/usr/bin/env bash\necho hi\n' > "$sbx/target-real/scripts/tool.sh"
chmod +x "$sbx/target-real/scripts/tool.sh"
run_install "$sbx/source/install.sh" "$sbx/home"
bak="$(find "$sbx/target-real" -maxdepth 1 -type d -name 'scripts.*.bak' 2> /dev/null | head -1)"
if [[ -n "$bak" ]]; then
  pass_line 'row21 directory-backup-strips-file-exec-not-dir-search: a directory backup was created'
  assert_not_executable "$bak/tool.sh" \
    'row21 directory-backup-strips-file-exec-not-dir-search: the file inside the backup has its exec bit stripped'
  if [[ -x "$bak" ]]; then
    pass_line 'row21 directory-backup-strips-file-exec-not-dir-search: the backup directory itself remains traversable'
  else
    fail_line 'row21 directory-backup-strips-file-exec-not-dir-search: the backup directory itself remains traversable'
  fi
else
  fail_line 'row21 directory-backup-strips-file-exec-not-dir-search: a directory backup was created'
  fail_line 'row21 directory-backup-strips-file-exec-not-dir-search: the file inside the backup has its exec bit stripped'
  fail_line 'row21 directory-backup-strips-file-exec-not-dir-search: the backup directory itself remains traversable'
fi

# ============================================================================
# Row 22 — mv-t-probe-catches-bsd-mv (security review F4)
# `mv -T` is GNU-only and was unprobed. Shadow `mv` on PATH with a shim that rejects -T (the
# BSD-mv shape) and confirm the script names the missing capability instead of proceeding.
# ============================================================================
sbx="$(new_sandbox row22_mv_t_probe)"
mk_good_source "$sbx/source"
mk_farm_topology "$sbx"
fakebin="$sbx/fakebin"
mkdir -p "$fakebin"
cat > "$fakebin/mv" << 'SHIM'
#!/usr/bin/env bash
for a in "$@"; do
  if [[ "$a" == "-T" ]]; then
    echo "mv: illegal option -- T" >&2
    exit 1
  fi
done
exec /bin/mv "$@"
SHIM
chmod +x "$fakebin/mv"
OUT="$(PATH="$fakebin:$PATH" HOME="$sbx/home" "$sbx/source/install.sh" 2>&1)"
RC=$?
assert_has 'mv -T not supported' \
  'row22 mv-t-probe-catches-bsd-mv: the mv -T probe names the missing capability'
assert_not_has "$EXP_RESULT_PASS" \
  'row22 mv-t-probe-catches-bsd-mv: no PASS verdict when mv -T is unsupported'

# ============================================================================
# Row 23 — space-filename-defeats-substring-match (security review F5)
# One tracked root entry whose NAME embeds several FLOOR names as space-separated
# pseudo-tokens must not spuriously satisfy the floor via joined-string substring matching —
# every real FLOOR file is genuinely absent, so the run must abort naming them.
# ============================================================================
sbx="$(new_sandbox row23_space_filename)"
mkrepo "$sbx/source"
mkdir -p "$sbx/source/agents" "$sbx/source/skills" "$sbx/source/scripts"
printf 'agent\n' > "$sbx/source/agents/example.md"
printf 'skill\n' > "$sbx/source/skills/example.md"
printf '#!/usr/bin/env bash\nexit 0\n' > "$sbx/source/scripts/example.sh"
chmod +x "$sbx/source/scripts/example.sh"
printf '{}\n' > "$sbx/source/settings.json"
seed_installer_copy "$sbx/source"
spacename='CLAUDE.md CONTRIBUTING.md STYLE.md templates.md workflows.md README.md LICENSE ARCHITECTURE.md TESTING.md DELEGATING.md publication-model.md'
printf 'decoy: a single filename spelling every FLOOR name as pseudo-tokens\n' > "$sbx/source/$spacename"
commit_all "$sbx/source" seed
mk_farm_topology "$sbx"
run_install "$sbx/source/install.sh" "$sbx/home"
assert_has "$EXP_FLOOR_ABSENT_PREFIX" \
  'row23 space-filename-defeats-substring-match: a space-embedding decoy filename does not spuriously satisfy the floor'
assert_not_has "$EXP_RESULT_PASS" \
  'row23 space-filename-defeats-substring-match: no PASS verdict when the floor is only spuriously satisfied'

# ============================================================================
# Row 24 — verify's EXACT predicate is load-bearing where link_member declines
#
# Found by mutation: loosening verify's predicate to "anywhere under the source
# root" SURVIVED the whole suite, because link_member now repairs a within-source
# mismatch before verify ever runs. The predicate still decides ONE reachable
# state, and nothing covered it: a member present in HEAD but ABSENT from the
# worktree, whose live link mis-points at another file inside the source. There
# link_member declines to touch the path, so verify alone decides.
# Measured, same state, two predicates: exact -> RESULT: FAIL (unverified=1);
# loose -> RESULT: PASS (unverified=0), blessing a settings.json aimed at LICENSE.
# ============================================================================
sbx="$(new_sandbox row24_verify_predicate)"
mk_good_source "$sbx/source"
mk_farm_topology "$sbx"
mk_manual_farm "$sbx/source" "$sbx/target-real"
rm "$sbx/source/settings.json"                                  # in HEAD, gone from the worktree
ln -sfn "$sbx/source/LICENSE" "$sbx/target-real/settings.json"  # mis-pointed, still under the root
run_install "$sbx/source/install.sh" "$sbx/home"
assert_has "$EXP_RESULT_FAIL" \
  'row24 verify-exact-predicate: a within-source mis-pointed link whose source is missing drives RESULT: FAIL'
assert_not_has "$EXP_RESULT_PASS" \
  'row24 verify-exact-predicate: no PASS verdict is emitted for that state'

# ============================================================================
# Row 25 — marker-foreign-root-refused-real-files (security review R1, shape (a))
# R1 REPRODUCED: `detect_rewire`'s symlink-majority test has a zero denominator whenever
# every managed path is a REAL FILE, so the old code proceeded silently. A marker naming a
# DIFFERENT root must refuse regardless — the whole point of giving the guard a denominator
# that does not depend on symlinks being present.
# ============================================================================
sbx="$(new_sandbox row25_marker_foreign_real_files)"
mk_good_source "$sbx/source"
mkdir -p "$sbx/home" "$sbx/target-real"
seed_floor "$sbx/target-real" # every managed path is a REAL FILE, not a symlink
printf '%s\n' "$sbx/some-other-root-entirely" > "$sbx/target-real/.installed-from"
ln -sfn "$sbx/target-real" "$sbx/home/.claude"
run_install "$sbx/source/install.sh" "$sbx/home"
assert_has "$EXP_REWIRE_REFUSED" \
  'row25 marker-foreign-root-refused-real-files: a marker naming a different root refuses even when every managed path is a real file'
assert_not_has "$EXP_RESULT_PASS" \
  'row25 marker-foreign-root-refused-real-files: no PASS verdict is ever printed'
if [[ -L "$sbx/target-real/CLAUDE.md" ]]; then
  fail_line 'row25 marker-foreign-root-refused-real-files: CLAUDE.md remains a real file, untouched by the refused run'
else
  pass_line 'row25 marker-foreign-root-refused-real-files: CLAUDE.md remains a real file, untouched by the refused run'
fi

# ============================================================================
# Row 26 — marker-same-root-proceeds (security review R1, shape (b))
# A marker naming the SAME root as the running installer must not block the run, even though
# every managed path is still a real file (no symlink evidence at all).
# ============================================================================
sbx="$(new_sandbox row26_marker_same_root)"
mk_good_source "$sbx/source"
mkdir -p "$sbx/home" "$sbx/target-real"
seed_floor "$sbx/target-real"
printf '%s\n' "$sbx/source" > "$sbx/target-real/.installed-from"
ln -sfn "$sbx/target-real" "$sbx/home/.claude"
run_install "$sbx/source/install.sh" "$sbx/home"
assert_has "$EXP_RESULT_PASS" \
  'row26 marker-same-root-proceeds: a marker naming the SAME root proceeds to RESULT: PASS'
assert_linked_under "$sbx/home/.claude/CLAUDE.md" "$sbx/source" \
  'row26 marker-same-root-proceeds: CLAUDE.md ends up linked into the source root'

# ============================================================================
# Row 27 — no-marker-no-symlink-warns (security review R1, shape (c))
# No marker and no existing managed symlinks: the run has NO evidence about who owns the
# farm. It must not report a clean judgement it never made — WARN naming the source root
# about to own the farm, then proceed.
# ============================================================================
sbx="$(new_sandbox row27_no_marker_no_symlink_warn)"
mk_good_source "$sbx/source"
mkdir -p "$sbx/home" "$sbx/target-real"
seed_floor "$sbx/target-real" # real files only: no marker, no existing managed symlinks
ln -sfn "$sbx/target-real" "$sbx/home/.claude"
run_install "$sbx/source/install.sh" "$sbx/home"
assert_has "$EXP_NO_ROOT_EVIDENCE" \
  'row27 no-marker-no-symlink-warns: WARNs rather than silently proceeding when there is no evidence either way'
assert_has "$sbx/source" \
  'row27 no-marker-no-symlink-warns: the WARN names the source root about to own the farm'
assert_has "$EXP_RESULT_PASS" \
  'row27 no-marker-no-symlink-warns: the run still proceeds to RESULT: PASS'

# ============================================================================
# Row 28 — rewire-updates-marker (security review R1, shape (d))
# `--rewire` against a differing marker must proceed AND update the marker to the new root,
# so the next plain run is judged against the root that now actually owns the farm.
# ============================================================================
sbx="$(new_sandbox row28_rewire_updates_marker)"
mk_good_source "$sbx/old_source" old_seed
mk_good_source "$sbx/new_source" new_seed
mkdir -p "$sbx/home" "$sbx/target-real"
seed_floor "$sbx/target-real"
printf '%s\n' "$sbx/old_source" > "$sbx/target-real/.installed-from"
ln -sfn "$sbx/target-real" "$sbx/home/.claude"
run_install "$sbx/new_source/install.sh" "$sbx/home" --rewire
assert_has "$EXP_RESULT_PASS" \
  'row28 rewire-updates-marker: --rewire with a differing marker proceeds to RESULT: PASS'
marker_content="$(cat "$sbx/target-real/.installed-from" 2> /dev/null || echo MISSING)"
check_eq "$marker_content" "$sbx/new_source" \
  'row28 rewire-updates-marker: the marker is rewritten to name the new source root'

# ============================================================================
# Row 29 — dangling-repaired-by-plain-run (security review R2, half 1)
# A benign dangling managed link (target renamed) must be repaired by a PLAIN run — the
# documented `./install.sh` command — without needing --rewire. The source file itself
# still exists; only the link's destination is stale.
# ============================================================================
sbx="$(new_sandbox row29_dangling_repaired_plain_run)"
mk_good_source "$sbx/source"
mkdir -p "$sbx/home"
mk_manual_farm "$sbx/source" "$sbx/target-real"
ln -sfn "$sbx/target-real" "$sbx/home/.claude"
ln -sfn "$sbx/renamed-away-target" "$sbx/target-real/CLAUDE.md" # dangling: old target renamed away
run_install "$sbx/source/install.sh" "$sbx/home"
assert_has "$EXP_RESULT_PASS" \
  'row29 dangling-repaired-by-plain-run: a plain run (no --rewire) repairs a benign dangling managed link'
assert_has 'repairing dangling managed link' \
  'row29 dangling-repaired-by-plain-run: the repair is logged, naming the stale link'
assert_linked_exact "$sbx/home/.claude/CLAUDE.md" "$sbx/source/CLAUDE.md" \
  'row29 dangling-repaired-by-plain-run: CLAUDE.md now resolves to exactly its own source path'

# ============================================================================
# Row 30 — rewire-flag-evaluates-detect-rewire (security review R2, half 2)
# --rewire must relax only the FOREIGN-ROOT case inside link_member; it must NOT skip
# detect_rewire wholesale. Proven here by a REAL whole-farm foreign-root condition: the run
# must still evaluate and report it (rather than silently ignore it because --rewire was
# passed), while proceeding rather than refusing.
# ============================================================================
sbx="$(new_sandbox row30_rewire_evaluates_detect_rewire)"
mk_good_source "$sbx/old_source" old_seed
mk_good_source "$sbx/new_source" new_seed
mkdir -p "$sbx/home"
mk_manual_farm "$sbx/old_source" "$sbx/target-real"
ln -sfn "$sbx/target-real" "$sbx/home/.claude"
run_install "$sbx/new_source/install.sh" "$sbx/home" --rewire
assert_has "$EXP_RESULT_PASS" \
  'row30 rewire-flag-evaluates-detect-rewire: --rewire completes RESULT: PASS on a real foreign-root farm'
assert_has 'rewire authorized' \
  'row30 rewire-flag-evaluates-detect-rewire: detect_rewire is still evaluated (not skipped wholesale) — its finding is logged, not silently bypassed'
assert_linked_under "$sbx/home/.claude/CLAUDE.md" "$sbx/new_source" \
  'row30 rewire-flag-evaluates-detect-rewire: managed links are repointed to the new source'

# ============================================================================
# Row 31 — verdict-linked-excludes-skipped (security review R3)
# `linked=` must count actual link placements, not the raw member count: a member skipped
# by the source-missing path must NOT be counted as linked, and must be named separately.
# ============================================================================
sbx="$(new_sandbox row31_verdict_linked_excludes_skipped)"
mk_good_source "$sbx/source"
rm "$sbx/source/TESTING.md" # tracked in HEAD, absent from the worktree (F2 shape): TESTING.md is skipped
mk_farm_topology "$sbx"
run_install "$sbx/source/install.sh" "$sbx/home"
assert_has 'SKIPPED: TESTING.md' \
  'row31 verdict-linked-excludes-skipped: the skipped member is named on its own SKIPPED line'
linked_field="$(printf '%s\n' "$OUT" | grep -o 'linked=[0-9]*' | tail -1 | cut -d= -f2)"
skipped_field="$(printf '%s\n' "$OUT" | grep -o 'skipped=[0-9]*' | tail -1 | cut -d= -f2)"
member_count=$((${#FLOOR_FILES[@]} + ${#FLOOR_DIRS[@]} + 1 + 1)) # FLOOR + settings.json + install.sh
check_eq "$skipped_field" "1" \
  'row31 verdict-linked-excludes-skipped: skipped= reports exactly the one skipped member'
check_eq "$linked_field" "$((member_count - 1))" \
  'row31 verdict-linked-excludes-skipped: linked= counts actual placements (member count minus the skipped one), never the raw member count'

# ============================================================================
# Summary — a non-zero denominator is asserted: a discovery matching nothing reports
# success loudest of all, so checked=0 is a hard failure regardless of $failures.
# ============================================================================
printf '\n%d checked, %d failed\n' "$checked" "$failures"
if [[ "$checked" -eq 0 ]]; then
  printf 'RESULT: FAIL (checked=0 failures=0)\n'
  rc=1
elif [[ "$failures" -eq 0 ]]; then
  printf 'RESULT: PASS (checked=%d failures=%d)\n' "$checked" "$failures"
  rc=0
else
  printf 'RESULT: FAIL (checked=%d failures=%d)\n' "$checked" "$failures"
  rc=1
fi
exit "$rc"

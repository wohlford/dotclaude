#!/usr/bin/env bash
set -euo pipefail

# ============================================================================
# Script: install.sh
# Purpose: Symlink source-controlled dotclaude files into ~/.claude. Farm
#          membership is DERIVED from this repo's tracked root entries (git
#          ls-tree), never hand-listed — a hand-listed array silently drifts
#          out of sync with what the repo actually carries (see spec R1/D1,
#          plans/2026-08-20-install-sh-farm-membership.md).
# Usage: ./install.sh [--check] [--rewire]
#   --check   report drift against the derived membership; mutate NOTHING;
#             exit non-zero if any drift is found (spec D5).
#   --rewire  authorize repointing existing managed links that already
#             resolve to a DIFFERENT source root (spec D9). Without it, such
#             links are refused rather than silently repointed.
# ============================================================================

# ---------- Refuse to be sourced ----------
# This script mutates the filesystem and expects to control its own exit
# code; sourcing it would run all of that (and `set -euo pipefail`) inside
# the caller's interactive shell.
if [[ "${BASH_SOURCE[0]}" != "${0}" ]]; then
  printf 'refuse: install.sh must be executed, not sourced\n' >&2
  return 1
fi

# ---------- Diagnostic strings (spec: named text, not bare exit codes) ----------
readonly MSG_SELF_INSTALL='refuse: install.sh must not run from inside the install target'
readonly MSG_NON_REPO_ROOT='refuse: install.sh must run from the toplevel of its own git repo'
readonly MSG_FLOOR_ABSENT='refuse: floor member missing:'
readonly MSG_NONZERO_DENOM='refuse: no tracked root entries found'
readonly MSG_CONFIG_WARN='WARN: config-shaped root entry'
readonly MSG_REWIRE_REFUSED='refuse: existing managed links resolve to a different source root'
readonly MSG_NO_ROOT_EVIDENCE='WARN: no install marker and no existing managed symlinks'

# ---------- Rewire-detection marker (security review R1) ----------
# `detect_rewire`'s symlink-majority evidence has a zero denominator whenever
# every managed path is a REAL FILE (e.g. a fresh, never-linked target) — the
# majority test is then vacuous and the whole-farm foreign-root refusal never
# fires. MARKER_NAME is written into the TARGET (never tracked in any source
# repo) recording the absolute source root that owns the farm, so the
# rewire guard has a real denominator regardless of whether managed paths are
# links or plain files. Written as the LAST mutating step, only once verify
# has confirmed the run actually succeeded (spec R1 fix).
readonly MARKER_NAME='.installed-from'

# ---------- Helper functions ----------
log_info()  { printf '[INFO] %s\n' "$*"; }
log_warn()  { printf 'WARN: %s\n' "$*"; }
die()       { printf '%s\n' "$*" >&2; exit 1; }

show_help() {
  cat << EOF
Usage: $(basename "$0") [--check] [--rewire]

  --check   report drift against the derived membership; mutate nothing;
            exit non-zero if drift exists.
  --rewire  authorize repointing existing managed links that resolve to a
            different source root.
EOF
}

# in_list NEEDLE ELEMENT... -> 0 if NEEDLE exactly equals one of ELEMENT...
# (caller passes an array as "${arr[@]}"). Deliberately compares elements
# directly rather than substring-matching a space-joined string: a joined
# " ${arr[*]} " lets ONE tracked entry whose name contains spaces contribute
# several pseudo-tokens, spuriously satisfying several FLOOR names at once
# and defeating the identity check FLOOR exists to provide (spec F5).
# Deliberately no pipe into grep either: pipefail + `grep -q` on a producer
# that is still writing returns 141 (SIGPIPE) exactly when the needle IS
# present, which is size-dependent and hides from its own test (plan, Task 2).
in_list() {
  local needle="$1" elem
  shift
  for elem in "$@"; do
    [[ "$elem" == "$needle" ]] && return 0
  done
  return 1
}

# is_contained CONTAINER CANDIDATE -> 0 if CANDIDATE equals CONTAINER or sits
# anywhere below it. Both arguments must already be physically resolved.
is_contained() {
  [[ "$2" == "$1" || "$2" == "$1"/* ]]
}

is_config_shaped() {
  case "$1" in
    settings.json) return 1 ;; # the one expected, floor-declared config member
    *.json | *.jsonc | *.toml | *.yml | *.yaml | *.ini | *.cfg | *.conf) return 0 ;;
    .*) return 0 ;;
    *) return 1 ;;
  esac
}

# ---------- Resolve ----------
command -v realpath > /dev/null 2>&1 || die 'refuse: realpath not found on PATH (GNU coreutils; sudo port install coreutils)'

# mv -T (GNU: no-target-directory, so the atomic rename below can never be
# reinterpreted as "move INTO an existing directory") is required for the
# atomic replacement in link_member (R9). BSD mv lacks -T; on such a system
# a directory member would be removed and the script would then abort before
# recreating the link, leaving the path ABSENT — voiding the atomicity claim
# specifically for the directory members carrying the hook scripts (F4).
# Probed explicitly here, in a throwaway location, before any guard runs.
_mv_t_probe_dir="$(mktemp -d "${TMPDIR:-/tmp}/install-sh-mv-t-probe.XXXXXX")" \
  || die 'refuse: could not create a throwaway directory to probe mv -T'
mkdir -p "$_mv_t_probe_dir/src"
if ! mv -T "$_mv_t_probe_dir/src" "$_mv_t_probe_dir/dst" 2> /dev/null; then
  rm -rf "$_mv_t_probe_dir"
  die 'refuse: mv -T not supported by this mv (GNU coreutils required; sudo port install coreutils)'
fi
rm -rf "$_mv_t_probe_dir"

# realpath here is NOT the portable `cd "$(dirname "$0")" && pwd` idiom
# STYLE.md:112 prefers for path resolution — it is required BECAUSE it
# resolves symlinks, and a prior style pass (e125c74) already moved this
# exact line to the portable form once and would do so again. Do not
# "simplify" it back: the self-install guard below (spec D7) only works when
# SCRIPT_DIR is physical. The live topology is HOME/.claude -> a symlink into
# this repo; a copy of this script reached THROUGH that symlink yields a
# LOGICAL path via the portable idiom that can never compare equal to the
# `-P`-resolved target, so the guard would never fire (spec "Spike result").
SCRIPT_PATH="$(realpath "$0")"
SCRIPT_DIR="$(cd -P "$(dirname "$SCRIPT_PATH")" && pwd -P)"
readonly SCRIPT_PATH SCRIPT_DIR

# TARGET_DIR: the physical location HOME/.claude resolves to, WITHOUT ever
# creating it here — mutation is not allowed until every guard has passed.
resolve_target_dir() {
  local claude_path="$HOME/.claude"
  [[ -d "$HOME" ]] || die "refuse: \$HOME is not a directory: $HOME"

  if [[ -L "$claude_path" ]]; then
    [[ -e "$claude_path" ]] || die "refuse: \$HOME/.claude is a dangling symlink: $claude_path"
    [[ -d "$claude_path" ]] || die "refuse: \$HOME/.claude resolves to a non-directory: $claude_path"
    TARGET_DIR="$(cd -P "$claude_path" && pwd -P)"
  elif [[ -e "$claude_path" ]]; then
    [[ -d "$claude_path" ]] || die "refuse: \$HOME/.claude exists and is not a directory: $claude_path"
    TARGET_DIR="$(cd -P "$claude_path" && pwd -P)"
  else
    local home_phys
    home_phys="$(cd -P "$HOME" && pwd -P)"
    TARGET_DIR="$home_phys/.claude"
  fi
  return 0
}
resolve_target_dir
readonly TARGET_DIR

# ---------- Guard (spec D7): containment, not equality ----------
# A copy at ANY depth below the target escapes an equality test, then
# `git -C "$SCRIPT_DIR"` resolves to the TARGET's own (stale) repo, its floor
# can pass, and every link source becomes a path that does not exist — the
# measured landmine this guard exists to close.
guard_no_self_install() {
  if is_contained "$TARGET_DIR" "$SCRIPT_DIR" || is_contained "$SCRIPT_DIR" "$TARGET_DIR"; then
    die "$MSG_SELF_INSTALL (script=$SCRIPT_DIR target=$TARGET_DIR)"
  fi
  return 0
}
guard_no_self_install

guard_repo_toplevel() {
  local toplevel toplevel_phys
  toplevel="$(git -C "$SCRIPT_DIR" rev-parse --show-toplevel 2> /dev/null)" \
    || die "$MSG_NON_REPO_ROOT (not inside a git repository: $SCRIPT_DIR)"
  toplevel_phys="$(cd -P "$toplevel" && pwd -P)"
  [[ "$toplevel_phys" == "$SCRIPT_DIR" ]] \
    || die "$MSG_NON_REPO_ROOT (script dir is $SCRIPT_DIR, toplevel is $toplevel_phys)"
  [[ -e "$SCRIPT_DIR/install.sh" ]] \
    || die "$MSG_NON_REPO_ROOT (install.sh not found at $SCRIPT_DIR)"
  return 0
}
guard_repo_toplevel

# ---------- Declared FLOOR and EXCLUDE (spec D2/D3/D6) ----------
# FLOOR: named members whose ABSENCE aborts the run. Discovery cannot detect
# absence. DELEGATING.md and publication-model.md are the discriminator that
# makes this an IDENTITY check on the source repo, not decoration — the
# install TARGET is itself a stale git repo carrying CLAUDE.md, skills,
# agents, scripts and settings.json, so a floor built from "obvious" members
# alone is satisfied by the WRONG repo (spec D6).
readonly FLOOR=(
  CLAUDE.md CONTRIBUTING.md STYLE.md templates.md workflows.md README.md LICENSE
  ARCHITECTURE.md TESTING.md DELEGATING.md publication-model.md
  agents skills scripts
  settings.json
)

# EXCLUDE: repo-local tooling config and infrastructure, never installed into
# the farm. A "leading dot" rule would exclude by spelling, not function (it
# would drop .markdownlint-cli2.jsonc while keeping ruff.toml, the same kind
# of thing), so this is a declared list instead (spec D2).
readonly EXCLUDE=(
  .commit-conventions.toml # consumed by this repo's own commit tooling
  .gitignore               # repo-local; linking would change what the TARGET's own repo tracks
  .markdownlint-cli2.jsonc # config-shaped: becomes discoverable config for tools run under the target
  .publication.toml        # linking would make the target's own repo look adopted to promote tooling
  ruff.toml                # config-shaped, same reason as the markdownlint config
  git-hooks                # repo infrastructure, not a farm member
  .installed-from          # the rewire marker itself; lives only in the TARGET, never a farm member
)

# ---------- Validate: derive membership ----------
derive_members() {
  TRACKED=()
  local line
  while IFS= read -r line; do
    if [[ -n "$line" ]]; then
      TRACKED+=("$line")
    fi
  done < <(git -C "$SCRIPT_DIR" ls-tree --name-only HEAD)

  local item
  MEMBERS=()
  for item in "${TRACKED[@]}"; do
    if ! in_list "$item" "${EXCLUDE[@]}"; then
      MEMBERS+=("$item")
    fi
  done
  return 0
}
derive_members

[[ ${#MEMBERS[@]} -gt 0 ]] || die "$MSG_NONZERO_DENOM (repo: $SCRIPT_DIR)"

assert_floor() {
  local f
  local -a missing=()
  for f in "${FLOOR[@]}"; do
    if ! in_list "$f" "${MEMBERS[@]}"; then
      missing+=("$f")
    fi
  done
  [[ ${#missing[@]} -eq 0 ]] || die "$MSG_FLOOR_ABSENT ${missing[*]}"
  return 0
}
assert_floor

# WARN (never abort) for two shapes discovery cannot silently absorb (D1/D3):
warn_untracked_root_entries() {
  local entry base
  for entry in "$SCRIPT_DIR"/* "$SCRIPT_DIR"/.*; do
    if [[ ! -e "$entry" && ! -L "$entry" ]]; then
      continue
    fi
    base="$(basename "$entry")"
    if [[ "$base" == '.' || "$base" == '..' || "$base" == '.git' ]]; then
      continue
    fi
    if [[ ${#TRACKED[@]} -gt 0 ]] && in_list "$base" "${TRACKED[@]}"; then
      continue
    fi
    if [[ ${#EXCLUDE[@]} -gt 0 ]] && in_list "$base" "${EXCLUDE[@]}"; then
      continue
    fi
    log_warn "untracked working-tree root entry: $base"
  done
  return 0
}
warn_untracked_root_entries

warn_config_shaped_additions() {
  local m
  for m in "${MEMBERS[@]}"; do
    if is_config_shaped "$m"; then
      log_warn "$MSG_CONFIG_WARN: $m"
    fi
  done
  return 0
}
warn_config_shaped_additions

# ---------- Parse remaining flags ----------
CHECK_MODE=0
REWIRE_FLAG=0
for arg in "$@"; do
  case "$arg" in
    --check) CHECK_MODE=1 ;;
    --rewire) REWIRE_FLAG=1 ;;
    -h | --help)
      show_help
      exit 0
      ;;
    *) die "refuse: unknown argument: $arg (see --help)" ;;
  esac
done

# ---------- --check: report drift, mutate nothing (spec D5) ----------
if [[ $CHECK_MODE -eq 1 ]]; then
  drift=0
  for m in "${MEMBERS[@]}"; do
    path="$TARGET_DIR/$m"
    if [[ -L "$path" && -e "$path" ]]; then
      resolved="$(realpath "$path")"
      if [[ "$resolved" != "$SCRIPT_DIR/$m" ]]; then
        printf 'DRIFT: %s (resolved to %s, want %s)\n' "$m" "$resolved" "$SCRIPT_DIR/$m"
        drift=1
      fi
    else
      printf 'DRIFT: %s (missing or not a live symlink at %s)\n' "$m" "$path"
      drift=1
    fi
  done
  if [[ $drift -eq 1 ]]; then
    printf 'RESULT: FAIL (source=%s check=drift)\n' "$SCRIPT_DIR"
    exit 1
  fi
  printf 'RESULT: PASS (source=%s check=clean)\n' "$SCRIPT_DIR"
  exit 0
fi

# ---------- Rewire guard (spec D9, security review R1) ----------
# Two sources of evidence, in priority order:
#
#   1. The MARKER (security review R1 fix): a file written into the TARGET
#      recording the absolute source root that owns the farm. Authoritative
#      when present, because it has a real denominator regardless of whether
#      managed paths are links or plain files — closing the zero-denominator
#      hole in evidence #2 below (a target whose managed paths are all REAL
#      FILES has no live symlinks to count, so that majority test is vacuous
#      and previously let a rewire through unnoticed).
#   2. The symlink-MAJORITY fallback (original D9 design), used only when no
#      marker exists — e.g. a farm installed before markers existed. Fires
#      only when a majority of the already-existing managed links agree on
#      pointing to some OTHER coherent root, never for an isolated
#      foreign/corrupted single link, which verify (below) reports on its own
#      without needing --rewire to name it.
#
# When NEITHER source has evidence (no marker, no live managed symlinks), do
# not report a clean judgement that was never made: WARN naming the source
# root about to own the farm, then proceed (spec R1 fix: "naming what you
# could not measure is the requirement; silently proceeding is the defect").
MARKER_PATH="$TARGET_DIR/$MARKER_NAME"
readonly MARKER_PATH

detect_rewire() {
  local marker_root=''
  REWIRE_DETECTED=0
  REWIRE_EXAMPLE=''

  if [[ -f "$MARKER_PATH" && -r "$MARKER_PATH" ]]; then
    marker_root="$(head -n1 "$MARKER_PATH" 2> /dev/null || true)"
  fi

  if [[ -n "$marker_root" ]]; then
    if [[ "$marker_root" == "$SCRIPT_DIR" ]]; then
      return 0 # marker names this same root: no rewire
    fi
    REWIRE_DETECTED=1
    REWIRE_EXAMPLE="marker $MARKER_PATH -> $marker_root"
    return 0
  fi

  # No marker (evidence #2, fallback): symlink-majority evidence.
  local m path resolved existing=0 mismatched=0 example=''
  for m in "${MEMBERS[@]}"; do
    path="$TARGET_DIR/$m"
    if [[ -L "$path" && -e "$path" ]]; then
      existing=$((existing + 1))
      resolved="$(realpath "$path")"
      case "$resolved" in
        "$SCRIPT_DIR" | "$SCRIPT_DIR"/*) : ;;
        *)
          mismatched=$((mismatched + 1))
          if [[ -z "$example" ]]; then
            example="$m -> $resolved"
          fi
          ;;
      esac
    fi
  done
  if [[ $existing -gt 0 && $mismatched -gt $((existing / 2)) ]]; then
    REWIRE_DETECTED=1
    REWIRE_EXAMPLE="$example"
    return 0
  fi

  if [[ $existing -eq 0 ]]; then
    log_warn "$MSG_NO_ROOT_EVIDENCE — cannot verify which source root currently owns $TARGET_DIR; proceeding with source=$SCRIPT_DIR"
  fi
  return 0
}

# Always evaluated — never skipped wholesale by --rewire (security review R2:
# granting authority to repair one benign dangling link inside link_member
# must not silently waive the whole-farm foreign-root refusal for every other
# member in the same run). --rewire only relaxes the CONSEQUENCE (die vs log).
detect_rewire
if [[ $REWIRE_DETECTED -eq 1 ]]; then
  if [[ $REWIRE_FLAG -ne 1 ]]; then
    die "$MSG_REWIRE_REFUSED (e.g. $REWIRE_EXAMPLE; pass --rewire to override)"
  fi
  log_info "rewire authorized: $REWIRE_EXAMPLE"
fi

# ---------- Mutate ----------
mkdir -p "$TARGET_DIR"

# Actual work performed, distinct from the member count (security review R3:
# the old verdict line reported ${#MEMBERS[@]} as "linked", so a member
# skipped by the source-missing path or the foreign-decline path was counted
# as linked anyway — never a false PASS, since the verdict is driven by the
# unverified count, but it misdirects triage of a FAIL by hiding which
# members the run never touched).
LINKED_COUNT=0
SKIPPED_COUNT=0
SKIPPED_MEMBERS=()

link_member() {
  local src="$SCRIPT_DIR/$1" dest="$TARGET_DIR/$1" resolved bak tmp

  # F2: membership comes from `git ls-tree HEAD`, which can disagree with the
  # worktree (sparse checkout, pending `git rm`, interrupted checkout,
  # mid-rebase). Never back up and replace a live file at $dest on the
  # strength of HEAD alone when the thing it would be replaced WITH does not
  # exist — that turns a live real file into a DANGLING link. Skip and count
  # unverified instead (verify, below, reports it naturally: $dest is left
  # untouched, so it never becomes a live symlink under $SCRIPT_DIR).
  if [[ ! -e "$src" ]]; then
    log_warn "source missing for managed member: $1 (src=$src) — leaving $dest untouched"
    SKIPPED_COUNT=$((SKIPPED_COUNT + 1))
    SKIPPED_MEMBERS+=("$1")
    return 0
  fi

  if [[ -L "$dest" ]]; then
    resolved=''
    if [[ -e "$dest" ]]; then
      resolved="$(realpath "$dest" 2> /dev/null || true)"
    fi
    if [[ -n "$resolved" ]]; then
      if [[ "$resolved" == "$src" ]]; then
        LINKED_COUNT=$((LINKED_COUNT + 1))
        return 0 # already correct: idempotent no-op (R7)
      fi
      if is_contained "$SCRIPT_DIR" "$resolved"; then
        # F1: a link resolving to a DIFFERENT file INSIDE the correct source
        # root is not what --rewire governs (usage block: --rewire is for a
        # link resolving to a different source ROOT). Repair it
        # unconditionally — fall through to the atomic replace below.
        :
      elif [[ $REWIRE_FLAG -ne 1 ]]; then
        # A link resolving to a live FOREIGN root, without authorization to
        # rewire it, is left exactly as it is (D9). verify (R8) is what
        # reports it — never a silent fix, which would hide a foreign link
        # from the operator instead of naming it.
        SKIPPED_COUNT=$((SKIPPED_COUNT + 1))
        SKIPPED_MEMBERS+=("$1")
        return 0
      fi
      # foreign root + --rewire given: fall through and atomically replace it.
    else
      # security review R2: -L true, -e false is a DANGLING managed link —
      # the most ordinary drift (a link whose target was renamed), not a
      # rewire question. It is repaired UNCONDITIONALLY, regardless of
      # --rewire, so the documented plain-run command can fix it; the old
      # link text is logged so nothing is hidden. Fall through to the atomic
      # replace below.
      local old_link_text
      old_link_text="$(readlink "$dest" 2> /dev/null || true)"
      log_info "repairing dangling managed link: $dest (was -> $old_link_text)"
    fi
  elif [[ -e "$dest" ]]; then
    bak="${dest}.$(date +%Y%m%d-%H%M%S).bak"
    cp -a "$dest" "$bak"
    if [[ -d "$bak" && ! -L "$bak" ]]; then
      # F3: `chmod a-x` is NOT recursive. Applied to a DIRECTORY backup it
      # strips only the directory's OWN search bit (making the whole backup
      # untraversable) while every file inside keeps its exec bit — a no-op
      # exactly where executables live (scripts/). Strip exec from regular
      # files only; leave directory modes (search bits) alone so the backup
      # stays traversable.
      find "$bak" -type f -exec chmod a-x {} +
    else
      chmod a-x "$bak" # a backup is data, not a program (D8/R10)
    fi
    # Only a DIRECTORY has to be removed first: `mv -Tf` cannot replace a
    # non-empty directory, but it replaces a regular file in place. Removing a
    # regular file here would reopen the absence window R9 exists to close.
    if [[ -d "$dest" && ! -L "$dest" ]]; then
      rm -rf -- "$dest"
    fi
    log_info "backed up $dest -> $bak (exec stripped)"
  fi

  # Atomic replacement (R9): never rm-then-ln. Build the link at a temp name
  # and mv -Tf it into place, so an abort partway can never leave a managed
  # path absent.
  tmp="${dest}.install-tmp.$$"
  rm -f -- "$tmp" 2> /dev/null || true
  ln -sfn "$src" "$tmp"
  mv -Tf "$tmp" "$dest"
  LINKED_COUNT=$((LINKED_COUNT + 1))
  log_info "linked $dest -> $src"
  return 0
}

for m in "${MEMBERS[@]}"; do
  link_member "$m"
done

# ---------- Verify (spec R8) ----------
# Each managed path must be a symlink, resolve (-e, so dangling fails), be
# readable, AND resolve to EXACTLY its own source path — not merely
# "somewhere under the source root" (F1: the same exact-match predicate
# --check already used above; a merely-under-root match accepts a link
# pointed at any OTHER file in the source, e.g. settings.json -> LICENSE,
# and reports PASS on a state that is exactly wrong).
VERIFIED=0
UNVERIFIED=0
for m in "${MEMBERS[@]}"; do
  path="$TARGET_DIR/$m"
  ok=0
  if [[ -L "$path" && -e "$path" && -r "$path" ]]; then
    resolved="$(realpath "$path")"
    if [[ "$resolved" == "$SCRIPT_DIR/$m" ]]; then
      ok=1
    fi
  fi
  if [[ $ok -eq 1 ]]; then
    VERIFIED=$((VERIFIED + 1))
  else
    UNVERIFIED=$((UNVERIFIED + 1))
    log_warn "unverified: $m (path=$path)"
  fi
done

if [[ $SKIPPED_COUNT -gt 0 ]]; then
  printf 'SKIPPED: %s\n' "${SKIPPED_MEMBERS[*]}"
fi

# ---------- Marker (security review R1): LAST mutating step, PASS only ----------
# Written only once verify has confirmed the run actually succeeded, so a
# failed/partial run never plants a marker claiming a root it did not
# actually finish installing.
write_marker() {
  local marker_tmp
  marker_tmp="${MARKER_PATH}.install-tmp.$$"
  rm -f -- "$marker_tmp" 2> /dev/null || true
  printf '%s\n' "$SCRIPT_DIR" > "$marker_tmp"
  chmod a-x "$marker_tmp" 2> /dev/null || true # a marker is data, not a program
  mv -Tf "$marker_tmp" "$MARKER_PATH"
}

if [[ $UNVERIFIED -eq 0 ]]; then
  write_marker
  printf 'RESULT: PASS (source=%s linked=%d skipped=%d verified=%d unverified=%d)\n' \
    "$SCRIPT_DIR" "$LINKED_COUNT" "$SKIPPED_COUNT" "$VERIFIED" "$UNVERIFIED"
  exit 0
else
  printf 'RESULT: FAIL (source=%s linked=%d skipped=%d verified=%d unverified=%d)\n' \
    "$SCRIPT_DIR" "$LINKED_COUNT" "$SKIPPED_COUNT" "$VERIFIED" "$UNVERIFIED" >&2
  exit 1
fi

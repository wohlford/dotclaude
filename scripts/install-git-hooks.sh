#!/usr/bin/env bash
set -euo pipefail

# Script: install-git-hooks.sh
# Purpose: Install this repo's tracked ../git-hooks/pre-push into the resolved git hooks directory, copying it rather than symlinking
#
# The resolved directory is .git/hooks in a normal clone, or the MAIN repo's .git/hooks under a
# linked worktree — hooks are never per-worktree. Two measured silent fail-open states rule out
# a symlink: git ignores a non-executable hook with only a `hint:` line and lets the push
# through, and it ignores a dangling symlink the same way — and a symlink pointing back into
# git-hooks/ would reintroduce the branch dependence this design exists to avoid
# (core.hooksPath resolves against the checked-out working tree, so an in-tree hooks dir goes
# inactive on whatever branch the publish path checks out to push).
# Usage: ./scripts/install-git-hooks.sh [--force | --force-if-ours]
#   --force  overwrite a pre-existing .git/hooks/pre-push even if it differs from the
#            tracked source (still refuses a symlink outright; see Rules below).
#   --force-if-ours  overwrite only when the installed copy is a blob this repo's tracked
#            git-hooks/pre-push has held somewhere in HEAD's history -- i.e. a prior output of
#            this installer, merely stale. A foreign or hand-edited hook is refused exactly as
#            it is without the flag. A symlink is still refused outright, ahead of this check.
#            --force wins if both are given.
#
# Idempotent: running this twice with an identical installed copy is a silent no-op, both
# runs re-assert by sha256 and both report success.
#
# Pre-existing hook policy (decided, not left implicit): a symlink at the destination is
# ALWAYS refused, --force or not — copying over it would silently follow it and could write
# through to wherever it points. A pre-existing REGULAR file that differs from the tracked
# source is refused unless --force is given, on the theory that a hook installed by
# something else deserves a deliberate overwrite, not a silent one; this script does not
# back it up itself; the caller who passes --force is asserting they already know what is
# there.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
readonly SCRIPT_DIR

# The tracked source, as ONE constant: `main()` derives the source path from it and the license
# predicate below enumerates the same path's history. Two literals here could drift apart and
# the predicate would then license against a path nobody installs from.
readonly TRACKED_RELPATH="git-hooks/pre-push"

log_error() { printf 'install-git-hooks: %s\n' "$*" >&2; }

# Reads stdin, prints a hex sha256 digest. macOS ships no `sha256sum` by default; `shasum`
# is the portable fallback, mirroring the BSD/GNU split every other script in this repo
# already routes around.
sha256_hex() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum | awk '{print $1}'
  else
    shasum -a 256 | awk '{print $1}'
  fi
}

resolve_hooks_dir() { # repo_root -> absolute hooks dir on stdout, or empty + nonzero
  local repo_root="$1" dir
  # --path-format=absolute, not the bare form: the bare form is RELATIVE (.git/hooks from
  # the root, ../.git/hooks from a subdir) and would silently point cp at the wrong place
  # from anywhere but the repo root. Run from the repo root so a linked worktree resolves
  # to the MAIN repo's hooks dir automatically -- nothing here hand-constructs the path.
  dir="$(cd "$repo_root" && git rev-parse --path-format=absolute --git-path hooks 2>/dev/null)"
  [[ -n "$dir" ]] || return 1
  printf '%s\n' "$dir"
}

# hook_blob_is_ours REPO_ROOT DEST -> 0 when DEST's content is a blob this repo's tracked hook
# has held at some point in HEAD's history; 1 otherwise, including every indeterminate case.
#
# This mechanises install_hook's stated contract. "The caller who passes --force is asserting
# they already know what is there" becomes "the tool verified that what is there is its own
# prior output", so the caller is trusted with nothing.
#
# The predicate deliberately spans ALL of history, not just the previous commit: a promote whose
# install never happened leaves the destination stale by more than one generation, and that is
# precisely the state this exists to repair.
#
# CALLER CONTRACT: only consult this AFTER refusing a symlinked DEST. `git hash-object` follows
# a symlink, so a link pointing at a historical blob hashes to that blob and would be licensed.
hook_blob_is_ours() { # repo_root dest
  local repo_root="$1" dest="$2"
  local dest_blob head_blob members

  dest_blob="$(cd "$repo_root" && git hash-object -- "$dest" 2>/dev/null)"
  [[ -n "$dest_blob" ]] || return 1

  head_blob="$(cd "$repo_root" && git rev-parse -q --verify "HEAD:$TRACKED_RELPATH" 2>/dev/null)"
  [[ -n "$head_blob" ]] || return 1

  members="$(cd "$repo_root" \
    && git rev-list HEAD -- "$TRACKED_RELPATH" 2>/dev/null \
    | while IFS= read -r c; do
        git -C "$repo_root" rev-parse -q --verify "$c:$TRACKED_RELPATH" 2>/dev/null
      done)"

  # FLOOR, and deliberately the ONLY floor. `rev-list -- <path>` is rename-blind and
  # history-limited, so an empty or truncated enumeration is possible, and a discovery matching
  # nothing would otherwise license nothing while reporting success. Requiring HEAD's own blob to
  # be present SUBSUMES "the enumeration is non-empty": if it contains HEAD's blob it is non-empty
  # by construction, so a separate `count > 0` test could never fail once this one passes. Such a
  # line reads as a safety property while being unable to change any outcome, and a test written
  # around it would pin nothing -- so it is not written at all.
  #
  # Herestrings, never `printf … | grep -q`. Measured: `grep -q` exits at its first match and
  # SIGPIPEs the writer, which `set -o pipefail` surfaces as 141 -- the predicate would then
  # report NO MATCH on exactly the inputs that DO match. It only bites once the text exceeds the
  # pipe buffer (~64KB, so roughly 1600 revisions of this path), which is precisely the sort of
  # bound that holds until it doesn't. A herestring has no pipeline, so it cannot come back.
  grep -qx "$head_blob" <<<"$members" || return 1
  grep -qx "$dest_blob" <<<"$members"
}

install_hook() { # source dest force force_if_ours repo_root
  local source="$1" dest="$2" force="$3" force_if_ours="$4" repo_root="$5"

  if [[ -L "$dest" ]]; then
    log_error "$dest is a symlink -- refusing to overwrite it (see the policy note at the top of this script); remove it by hand and re-run"
    exit 1
  fi

  if [[ -e "$dest" ]]; then
    local existing_digest source_digest
    existing_digest="$(sha256_hex < "$dest" 2>/dev/null || printf 'unreadable')"
    source_digest="$(sha256_hex < "$source")"
    if [[ "$existing_digest" == "$source_digest" ]]; then
      : # already installed and byte-identical -- fall through to the digest re-assertion below
    elif [[ "$force" == true ]]; then
      : # the caller asserted, by passing --force, that they know what is there
    elif [[ "$force_if_ours" == true ]] && hook_blob_is_ours "$repo_root" "$dest"; then
      # Licensed, and said out loud: even the overwrite this tool performs for itself is
      # visible in the transcript, so a hand-DOWNGRADE being silently upgraded is at least
      # legible after the fact.
      printf 'install-git-hooks: replacing a prior tracked version of %s (sha256 %s)\n' \
        "$TRACKED_RELPATH" "$existing_digest"
    else
      log_error "$dest already exists and differs from the tracked source"
      log_error 'refusing to overwrite a hook that may have been installed by something else'
      log_error 're-run with --force once you have confirmed it is safe to replace'
      exit 1
    fi
  fi

  cp "$source" "$dest"
  chmod +x "$dest"

  if [[ -L "$dest" ]]; then
    log_error "post-install check: $dest is a symlink -- install did not take as expected"
    exit 1
  fi
  if [[ ! -x "$dest" ]]; then
    log_error "post-install check: $dest is not executable -- refusing to report success"
    exit 1
  fi

  local dest_digest source_digest
  dest_digest="$(sha256_hex < "$dest")"
  source_digest="$(sha256_hex < "$source")"
  if [[ "$dest_digest" != "$source_digest" ]]; then
    log_error "post-install check: $dest sha256 ($dest_digest) does not match the source ($source_digest)"
    exit 1
  fi

  printf 'install-git-hooks: installed %s (sha256 %s)\n' "$dest" "$dest_digest"
}

main() {
  local force=false force_if_ours=false
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --force)         force=true; shift ;;
      --force-if-ours) force_if_ours=true; shift ;;
      -*)      log_error "unknown flag: $1"; exit 2 ;;
      *)       log_error "unexpected argument: $1"; exit 2 ;;
    esac
  done

  local repo_root source hooks_dir dest
  repo_root="$(cd "$SCRIPT_DIR/.." && pwd)"
  source="$repo_root/$TRACKED_RELPATH"
  if [[ ! -f "$source" ]]; then
    log_error "missing tracked source: $source"
    exit 1
  fi

  if ! hooks_dir="$(resolve_hooks_dir "$repo_root")"; then
    log_error "could not resolve the git hooks directory from $repo_root (not a git repo?)"
    exit 1
  fi
  mkdir -p "$hooks_dir"
  dest="$hooks_dir/pre-push"

  install_hook "$source" "$dest" "$force" "$force_if_ours" "$repo_root"
}

main "$@"

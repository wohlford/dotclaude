#!/usr/bin/env bash
set -euo pipefail

# Script: install-git-hooks.sh
# Purpose: Install this repo's tracked ../git-hooks/pre-push into the resolved git hooks
#   directory (.git/hooks in a normal clone; the MAIN repo's .git/hooks under a linked
#   worktree — hooks are never per-worktree). COPIES the file; never symlinks it. Two
#   measured silent fail-open states rule out a symlink: git ignores a non-executable hook
#   with only a `hint:` line and lets the push through, and it ignores a dangling symlink
#   the same way — and a symlink pointing back into git-hooks/ would reintroduce the branch
#   dependence this design exists to avoid (core.hooksPath resolves against the checked-out
#   working tree, so an in-tree hooks dir goes inactive on whatever branch the publish path
#   checks out to push).
# Usage: ./scripts/install-git-hooks.sh [--force]
#   --force  overwrite a pre-existing .git/hooks/pre-push even if it differs from the
#            tracked source (still refuses a symlink outright; see Rules below).
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

install_hook() { # source dest force
  local source="$1" dest="$2" force="$3"

  if [[ -L "$dest" ]]; then
    log_error "$dest is a symlink -- refusing to overwrite it (see the policy note at the top of this script); remove it by hand and re-run"
    exit 1
  fi

  if [[ -e "$dest" && "$force" != true ]]; then
    local existing_digest source_digest
    existing_digest="$(sha256_hex < "$dest" 2>/dev/null || printf 'unreadable')"
    source_digest="$(sha256_hex < "$source")"
    if [[ "$existing_digest" != "$source_digest" ]]; then
      log_error "$dest already exists and differs from the tracked source"
      log_error 'refusing to overwrite a hook that may have been installed by something else'
      log_error 're-run with --force once you have confirmed it is safe to replace'
      exit 1
    fi
    # Already installed and byte-identical -- fall through to the digest re-assertion
    # below so a repeat run still reports success, rather than special-casing a no-op exit.
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
  local force=false
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --force) force=true; shift ;;
      -*)      log_error "unknown flag: $1"; exit 2 ;;
      *)       log_error "unexpected argument: $1"; exit 2 ;;
    esac
  done

  local repo_root source hooks_dir dest
  repo_root="$(cd "$SCRIPT_DIR/.." && pwd)"
  source="$repo_root/git-hooks/pre-push"
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

  install_hook "$source" "$dest" "$force"
}

main "$@"

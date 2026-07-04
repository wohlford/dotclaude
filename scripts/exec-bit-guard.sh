#!/usr/bin/env bash
set -euo pipefail

# Script: exec-bit-guard.sh
# Purpose: PreToolUse hook — block `git commit` when it would record a new shebang file without the exec bit (or a 755→644 downgrade)
# Usage: Called by Claude Code hooks with JSON on stdin
#
# Exit codes:
#   0 — allow (no commit segment, override, fileMode=false, not a repo, or any internal error → fail open)
#   2 — blocked: the staged commit would introduce a non-executable script (stderr fed back to Claude)
#
# A DELIBERATENESS gate, not an adversarial defense (same posture as push-guard.sh). Scope is
# introduction-only: a NEW shebang file staged 100644, or a 100755→100644 downgrade — editing a
# pre-existing 644 shebang file passes, so foreign repos with intentional 644 shebangs don't
# chronically block. Broad segment match (a `git` word AND a `commit` word) deliberately over-scans
# (e.g. `git log --grep=commit`) — it can only BLOCK when a genuine offender is staged. Under
# core.fileMode=false even executable scripts stage as 644 (spike-verified), so such repos are
# skipped entirely. Fails OPEN on malformed input / missing jq / unresolvable repo.

command -v jq >/dev/null 2>&1 || exit 0
input=$(cat) || exit 0
cmd=$(printf '%s' "$input" | jq -r '.tool_input.command // empty' 2>/dev/null) || exit 0
[ -n "$cmd" ] || exit 0

# Cheapest guard: bail unless a git word and a commit word appear somewhere at all.
git_re='(^|[^A-Za-z0-9_])git([^A-Za-z0-9_]|$)'
commit_re='(^|[^A-Za-z0-9_])commit([^A-Za-z0-9_]|$)'
printf '%s' "$cmd" | grep -qE "$git_re" || exit 0
printf '%s' "$cmd" | grep -qE "$commit_re" || exit 0

allow_re='^[[:space:]]*([A-Za-z_][A-Za-z0-9_]*=[^[:space:]]*[[:space:]]+)*ALLOW_NONEXEC=1([[:space:]]|$)'
# -a detection: a single-dash short-option cluster containing `a` (-a, -am), or exact --all.
# `--amend` matches neither (its dashes are not preceded by whitespace/start), by design.
aflag_re='(^|[[:space:]])-[a-zA-Z]*a[a-zA-Z]*([[:space:]]|$)|(^|[[:space:]])--all([[:space:]]|$)'

cwd=$(printf '%s' "$input" | jq -r '.cwd // empty' 2>/dev/null) || cwd=""
[ -n "$cwd" ] || cwd=$(pwd)

strip_quotes() { # echoes $1 without surrounding single/double quotes and trimmed
  local s="$1"
  s="${s#"${s%%[![:space:]]*}"}"; s="${s%"${s##*[![:space:]]}"}"
  case "$s" in \"*\") s="${s#\"}"; s="${s%\"}" ;; \'*\') s="${s#\'}"; s="${s%\'}" ;; esac
  printf '%s' "$s"
}

base="$cwd"
seg_dir=""
found=0
wtscan=0
first=1
while IFS= read -r seg; do
  if [ "$first" = 1 ]; then
    first=0
    # A leading `cd <dir>` first segment adjusts the base dir (simple paths only).
    if printf '%s' "$seg" | grep -qE '^[[:space:]]*cd[[:space:]]'; then
      cdtarget=$(strip_quotes "$(printf '%s' "$seg" | sed -E 's/^[[:space:]]*cd[[:space:]]+//')")
      case "$cdtarget" in
        /*) base="$cdtarget" ;;
        "~"*) base="${cdtarget/#\~/$HOME}" ;;
        ?*) base="$cwd/$cdtarget" ;;
      esac
      continue
    fi
  fi
  printf '%s' "$seg" | grep -qE "$git_re" || continue
  printf '%s' "$seg" | grep -qE "$commit_re" || continue
  printf '%s' "$seg" | grep -qE "$allow_re" && continue   # this segment is authorized
  found=1
  # Flag detection runs on the segment with quoted strings stripped: message text like
  # -m "refactor -a mode" must neither trigger the -a scan (reviewer-verified false BLOCK)
  # nor let a quoted "-C foo" hijack repo resolution (reviewer-verified false PASS).
  seg_flags=$(printf '%s' "$seg" | sed -E "s/\"[^\"]*\"//g; s/'[^']*'//g")
  ctarget=$(printf '%s' "$seg_flags" | sed -nE 's/.*[[:space:]]-C[[:space:]]+([^[:space:]]+).*/\1/p')
  [ -n "$ctarget" ] && seg_dir="$(strip_quotes "$ctarget")"
  printf '%s' "$seg_flags" | grep -qE "$aflag_re" && wtscan=1
done < <(printf '%s\n' "$cmd" | sed -E 's/(&&|[|][|]|;|[|])/\n/g')

[ "$found" = 1 ] || exit 0

dir="$base"
if [ -n "$seg_dir" ]; then
  case "$seg_dir" in /*) dir="$seg_dir" ;; *) dir="$base/$seg_dir" ;; esac
fi
[ -d "$dir" ] || exit 0
git -C "$dir" rev-parse --is-inside-work-tree >/dev/null 2>&1 || exit 0
[ "$(git -C "$dir" config --type=bool core.fileMode 2>/dev/null || echo true)" = "false" ] && exit 0

# Unborn HEAD (first commit): diff the index against git's well-known empty tree.
base_ref="$(git -C "$dir" rev-parse -q --verify HEAD 2>/dev/null)" \
  || base_ref='4b825dc642cb6eb9a060e54bf8d69288fbee4904'

offenders=""
while IFS= read -r -d '' meta && IFS= read -r -d '' path; do
  read -r src dst _ _ status <<< "${meta#:}"
  if [ "$status" = "A" ] && [ "$dst" = "100644" ]; then
    # || true inside the substitution — a SIGPIPEd git under pipefail must not discard the bytes
    first2="$(git -C "$dir" cat-file blob ":$path" 2>/dev/null | head -c 2 || true)"
    [ "$first2" = "#!" ] && offenders="${offenders}  ${path} — new shebang file staged as 644
"
  elif [ "$status" = "M" ] && [ "$src" = "100755" ] && [ "$dst" = "100644" ]; then
    offenders="${offenders}  ${path} — staged mode downgrade 755 -> 644
"
  fi
done < <(git -C "$dir" diff --cached "$base_ref" --raw -z --no-renames --diff-filter=AM 2>/dev/null || true)

if [ "$wtscan" = 1 ]; then
  while IFS= read -r -d '' meta && IFS= read -r -d '' path; do
    read -r src dst _ _ status <<< "${meta#:}"
    if [ "$status" = "M" ] && [ "$src" = "100755" ] && [ "$dst" = "100644" ]; then
      offenders="${offenders}  ${path} — worktree lost the exec bit and -a will commit it as 644
"
    fi
  done < <(git -C "$dir" diff --raw -z --no-renames --diff-filter=M 2>/dev/null || true)
fi

[ -n "$offenders" ] || exit 0
{
  printf 'blocked by exec-bit-guard: this commit would drop a needed exec bit (load-bearing for bare-path hooks/scripts):\n'
  printf '%s' "$offenders"
  printf 'Fix: chmod +x <file> && git add <file>  (chmod after git add is not enough — the staged mode is what commits).\n'
  printf 'If 644 is intentional for this file, lead the segment with ALLOW_NONEXEC=1 (e.g. ALLOW_NONEXEC=1 git commit ...).\n'
} >&2 || true
exit 2

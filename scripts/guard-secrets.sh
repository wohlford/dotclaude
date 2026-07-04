#!/usr/bin/env bash
set -euo pipefail

# Script: guard-secrets.sh
# Purpose: Global PreToolUse hook — deny reading/editing secret files (.env*, keys, pem)
# Usage: PreToolUse(Read|Edit|Write|MultiEdit|Grep) hook — reads JSON on stdin
#
# Exit codes:
#   0 — allow (not a secret file, or any uncertainty: fail-safe)
#   2 — deny: the file matches the universal secret list (stderr explains)
#
# Enforces CONTRIBUTING's never-commit secrets (.env, *.key, *.pem) — plus .env variants,
# *.env, and SSH private keys — at ACCESS time, before the tool runs. No per-project
# config; the deny list is deliberately tight and basename-matched.
#
# Known limits (accepted): a cat/grep via the Bash tool still reaches the file, and a Grep
# whose `path` is a *directory* (not a file) is not basename-matched; a file INSIDE a
# directory named .env is not matched (basename-only); *.key
# also matches Keynote decks and *.pem matches public certs (rarely read as text).
# Symlinks ARE resolved: a link is denied when its resolved target's basename is a secret.

input=$(cat)
if command -v jq >/dev/null 2>&1; then
  # `.path` covers content-returning file tools (Grep) that key the target as `path`, not
  # `file_path`; without it a Grep of a secret file dumps its contents past this gate.
  file_path=$(printf '%s' "$input" | jq -r '.tool_input.path // .tool_input.file_path // empty' 2>/dev/null) || file_path=""
else
  file_path=$(printf '%s' "$input" | sed -n 's/.*"\(file_path\|path\)"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\2/p' | head -1) || file_path=""
fi
[[ -n "$file_path" ]] || exit 0

# Case-insensitive matching: on macOS's default APFS, .ENV resolves to the same file as .env.
shopt -s nocasematch

# secret_name <basename> — 0 when the name is on the deny list (and not exempted).
secret_name() {
  case "$1" in
    # Exemptions first: documented placeholders and public halves are always fine.
    .env.example|.env.sample|.env.template|.env.dist) return 1 ;;
    *.pub) return 1 ;;
    # The deny list: never-commit secrets, matched at access time.
    .env|.env.*|*.env|*.key|*.pem|id_rsa|id_rsa.*|id_ecdsa|id_ecdsa.*|id_ed25519|id_ed25519.*|id_dsa|id_dsa.*)
      return 0 ;;
  esac
  return 1
}

deny=""
if secret_name "${file_path##*/}"; then
  deny="$file_path"
elif [[ -L "$file_path" ]]; then
  # A symlink to a secret is the secret: deny by the resolved target's basename too.
  target=$(readlink -f -- "$file_path" 2>/dev/null || true)
  if [[ -n "$target" ]] && secret_name "${target##*/}"; then
    deny="$file_path -> $target"
  fi
fi

if [[ -n "$deny" ]]; then
  printf 'guard-secrets: BLOCKED %s\n' "$deny" >&2
  printf '  Secret file (never-commit list: .env*, *.env, *.key, *.pem, SSH private keys).\n' >&2
  printf '  Do not read or edit it — describe shapes, not contents; the user can open it.\n' >&2
  printf '  (User: to allow this deliberately, remove the guard-secrets PreToolUse entry\n' >&2
  printf '  from ~/.claude/settings.json and restart.)\n' >&2
  exit 2
fi
exit 0

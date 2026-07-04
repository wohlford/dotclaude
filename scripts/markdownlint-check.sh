#!/usr/bin/env bash
set -euo pipefail

# Script: markdownlint-check.sh
# Purpose: PostToolUse hook — run markdownlint-cli2 on edited markdown in opted-in repos
# Usage: Called by Claude Code hooks with JSON on stdin
#
# Exit codes:
#   0 — no action needed, lint clean, tool unavailable, or tool malfunction (fail open)
#   2 — markdownlint-cli2 reported findings (combined output fed to Claude; findings go
#       to stderr and the banner to stdout, so capture merges both streams)
#
# Global hook: fires on every Edit|Write in every repo, so guards run cheapest-first and
# exit 0 fast for anything that is not an editable .md in a repo that opted into
# markdownlint (carries a .markdownlint-cli2.* config). cli2 discovers configuration by
# walking up from the FILE's directory independent of cwd (verified against v0.23.0), so
# the hook passes the absolute path and lets cli2 discover — the gate below matches that.

# ---------- Parse stdin JSON ----------
input=$(cat)
file_path=$(printf '%s' "$input" | jq -r '.tool_input.file_path // empty' 2>/dev/null) || file_path=""

if [[ -z "$file_path" ]]; then
  exit 0
fi

# ---------- Cheap guard: only markdown files ----------
case "$file_path" in
  *.md) ;;
  *) exit 0 ;;
esac

# ---------- Existence guard: skip deleted/renamed files ----------
if [[ ! -f "$file_path" ]]; then
  exit 0
fi

# ---------- Carve-out: plans/ and specs/ design drafts are never linted ----------
# Mirrors md-links-check.py; the repo config also lists them in "ignores".
case "$file_path" in
  */plans/*|*/specs/*) exit 0 ;;
esac

# ---------- Availability guard: no markdownlint-cli2 → can't lint, never falsely block ----------
# The launcher's shebang is `#!/usr/bin/env node`, so node's bin dir must be ON PATH —
# invoking the launcher by absolute path alone fails in non-interactive shells (NVM
# gotcha). Fall back to the newest NVM node bin dir and prepend it.
if ! command -v markdownlint-cli2 >/dev/null 2>&1; then
  # shellcheck disable=SC2012  # ls|sort -V picks the newest version dir; paths are ours, no exotic names
  nvm_bin=$(ls -d "$HOME"/.nvm/versions/node/*/bin 2>/dev/null | sort -V | tail -1 || true)
  if [[ -z "$nvm_bin" || ! -x "$nvm_bin/markdownlint-cli2" ]]; then
    exit 0
  fi
  PATH="$nvm_bin:$PATH"
fi

# ---------- Config gate (model A): only act in repos that opted into markdownlint ----------
# Walk from the file's directory up to the filesystem root for a markdownlint-cli2
# config. No config → the project has not opted in, stay silent (polite global hook).
# Deliberately narrow: cli2 would also honor legacy .markdownlint.{jsonc,json,yaml}
# files, but this gate keys on the cli2-native names only — a repo opted in via the
# legacy names is silently un-linted (fail-silent, never a false block).
dir=$(cd "$(dirname "$file_path")" && pwd)
have_config=0
while true; do
  for cfg in .markdownlint-cli2.jsonc .markdownlint-cli2.yaml .markdownlint-cli2.cjs .markdownlint-cli2.mjs; do
    if [[ -f "$dir/$cfg" ]]; then
      have_config=1
      break 2
    fi
  done
  [[ "$dir" == "/" ]] && break
  dir=$(dirname "$dir")
done
if [[ "$have_config" -eq 0 ]]; then
  exit 0
fi

# ---------- Run markdownlint-cli2 (set -e-safe capture) ----------
rc=0
out=$(markdownlint-cli2 "$file_path" 2>&1) || rc=$?

if [[ "$rc" -eq 1 ]]; then
  printf 'markdownlint flagged %s:\n' "$file_path" >&2
  printf '%s\n' "$out" | tail -40 >&2
  exit 2
fi

# rc 0 = clean; rc >1 = tool malfunction (bad globs/params) — fail open, never false-block.
exit 0

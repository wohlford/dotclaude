#!/usr/bin/env bash
set -uo pipefail

# Script: test_exec_bit_integrity.sh
# Purpose: Audit — every tracked shebang file is committed 100755, and every settings.json hook
#          command resolves to a tracked 100755 file (the exec bit is load-bearing for bare-path hooks).
# Usage:   bash scripts/tests/test_exec_bit_integrity.sh   (anywhere inside the repo)

root="$(git rev-parse --show-toplevel 2>/dev/null)" || { printf 'FAIL  not inside a git repo\n'; exit 1; }
cd "$root" || exit 1

pass=0
fail=0

# --- 1. every tracked blob that starts with '#!' must be committed 100755 ---
while IFS=$'\t' read -r meta path; do
  mode="${meta%% *}"
  case "$mode" in
    100644|100755) ;;
    *) continue ;;   # symlinks (120000), submodules (160000), etc.
  esac
  # `|| true` INSIDE the substitution: head closing the pipe can SIGPIPE git (rc 141 under
  # pipefail), which would otherwise discard the captured bytes and skip real shebang files.
  first2="$(git cat-file blob ":$path" 2>/dev/null | head -c 2 || true)"
  [[ "$first2" == '#!' ]] || continue
  if [[ "$mode" == "100755" ]]; then
    pass=$((pass + 1))
  else
    printf 'FAIL  %s has a shebang but committed mode %s (want 100755) — chmod +x and git add\n' "$path" "$mode"
    fail=$((fail + 1))
  fi
done < <(git ls-files -s)

# --- 2. every settings.json hook command must resolve to a tracked 100755 file ---
while IFS= read -r cmd; do
  rel="${cmd#\$HOME/.claude/}"
  [[ "$rel" != "$cmd" ]] || continue   # not a $HOME/.claude/-managed script
  mode="$(git ls-files -s -- "$rel" | awk '{print $1}')"
  if [[ "$mode" == "100755" ]]; then
    pass=$((pass + 1))
  else
    printf 'FAIL  settings.json wires %s but %s is %s (want tracked 100755)\n' "$cmd" "$rel" "${mode:-untracked}"
    fail=$((fail + 1))
  fi
done < <(jq -r '.hooks | to_entries[] | .value[] | .hooks[] | .command' settings.json)

printf '\n%d passed, %d failed\n' "$pass" "$fail"
[[ "$fail" -eq 0 ]]

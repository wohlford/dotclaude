#!/usr/bin/env bash
set -uo pipefail

# Script: test_style_check.sh
# Purpose: Regression tests for style-check.sh's auto-memory trailing-whitespace
#          exemption — and that the exemption stays scoped (tabs still caught,
#          non-memory paths still checked, no over-exempt outside the config dir).
# Usage:   bash scripts/tests/test_style_check.sh

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
script="$here/../style-check.sh"

sandbox="$(mktemp -d)"
trap 'rm -rf "$sandbox"' EXIT

config="$sandbox/config"                 # stands in for CLAUDE_CONFIG_DIR
memdir="$config/projects/-slug-x/memory"
mkdir -p "$memdir"

pass=0
fail=0

# run <file> <expected_exit> <label>
run() {
  local file="$1" want="$2" label="$3" got=0
  printf '{"tool_input":{"file_path":"%s"}}' "$file" \
    | CLAUDE_CONFIG_DIR="$config" bash "$script" >/dev/null 2>&1 || got=$?
  if [[ "$got" -eq "$want" ]]; then
    printf 'PASS  %s (exit %d)\n' "$label" "$got"
    pass=$((pass + 1))
  else
    printf 'FAIL  %s (want %d, got %d)\n' "$label" "$want" "$got"
    fail=$((fail + 1))
  fi
}

# 1. Memory file with trailing ws after `metadata:` (the augmentation case) → exempt.
mem1="$memdir/test-mem.md"
printf -- '---\nname: foo\ndescription: bar\nmetadata: \n  node_type: memory\n  type: user\n---\n\nthe fact\n' > "$mem1"
run "$mem1" 0 "memory file, trailing ws exempted"

# 2. MEMORY.md in the memory dir with trailing ws → exempt (matches *.md).
mem2="$memdir/MEMORY.md"
printf -- '- [x](x.md) — hook \n' > "$mem2"
run "$mem2" 0 "MEMORY.md, trailing ws exempted"

# 3. Non-memory markdown with trailing ws → still flagged.
other="$sandbox/notes.md"
printf -- '# Title \n\ntext\n' > "$other"
run "$other" 2 "non-memory md, trailing ws flagged"

# 4. Memory file with a leading tab → tab check still fires (exemption is ws-only).
memtab="$memdir/tabbed.md"
printf -- '---\nname: x\ndescription: y\n---\n\n\ttabbed\n' > "$memtab"
run "$memtab" 2 "memory file, tab still caught"

# 5. Over-exempt canary: a projects/*/memory/*.md OUTSIDE the config dir must NOT be
#    exempt. Fails against an unanchored glob; guards against a future glob typo.
candir="$sandbox/somerepo/projects/auth/memory"
mkdir -p "$candir"
canary="$candir/notes.md"
printf -- 'note with trailing ws \n' > "$canary"
run "$canary" 2 "over-exempt canary outside config dir flagged"

printf '\n%d passed, %d failed\n' "$pass" "$fail"
[[ "$fail" -eq 0 ]]

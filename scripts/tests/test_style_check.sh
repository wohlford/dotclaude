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

# 6. Agents frontmatter is anchored to Claude agent dirs — a FOREIGN repo's agents/
#    docs folder is plain markdown and must not be frontmatter-blocked.
foreign="$sandbox/foreignrepo/agents"
mkdir -p "$foreign"
printf -- '# Agent design notes\n\nplain markdown\n' > "$foreign/pipeline.md"
run "$foreign/pipeline.md" 0 "foreign agents/*.md not frontmatter-blocked"

# 7. …but a Claude-config-shaped repo (agents/ beside skills/) IS enforced…
cfgrepo="$sandbox/cfgrepo"
mkdir -p "$cfgrepo/agents" "$cfgrepo/skills"
printf -- '# Not frontmatter\n' > "$cfgrepo/agents/reviewer.md"
run "$cfgrepo/agents/reviewer.md" 2 "config-shaped agents/*.md still enforced"

# 8. …and so is a project-level .claude/agents/ dir.
projagents="$sandbox/proj/.claude/agents"
mkdir -p "$projagents"
printf -- '# Not frontmatter\n' > "$projagents/helper.md"
run "$projagents/helper.md" 2 ".claude/agents/*.md still enforced"

# 9. Fail-safe: unparseable stdin must exit 0 (a hook crash = fail open), not jq's rc 5.
got=0
printf 'not-json' | CLAUDE_CONFIG_DIR="$config" bash "$script" >/dev/null 2>&1 || got=$?
if [[ "$got" -eq 0 ]]; then
  printf 'PASS  malformed JSON stdin -> 0 (fail-safe)\n'; pass=$((pass + 1))
else
  printf 'FAIL  malformed JSON stdin (want 0, got %d)\n' "$got"; fail=$((fail + 1))
fi

# 10. Fail-safe: jq absent must exit 0, not rc 127 (a crash would silently skip enforcement).
nojq="$sandbox/nojq"; mkdir -p "$nojq"
for t in bash printf cat sed grep; do p="$(command -v "$t")" && ln -s "$p" "$nojq/$t"; done
got=0
printf '{"tool_input":{"file_path":"%s/x.sh"}}' "$sandbox" \
  | PATH="$nojq" CLAUDE_CONFIG_DIR="$config" bash "$script" >/dev/null 2>&1 || got=$?
if [[ "$got" -eq 0 ]]; then
  printf 'PASS  jq absent -> 0 (fail-safe)\n'; pass=$((pass + 1))
else
  printf 'FAIL  jq absent (want 0, got %d)\n' "$got"; fail=$((fail + 1))
fi

# 11. Valid TOML → pass.
toml_ok="$sandbox/config.toml"
printf 'line-length = 88\n\n[lint]\nselect = ["E", "F"]\n' > "$toml_ok"
run "$toml_ok" 0 "valid TOML passes"

# 12. Invalid TOML syntax → flagged.
toml_bad="$sandbox/broken.toml"
printf '[lint\nselect = oops\n' > "$toml_bad"
run "$toml_bad" 2 "invalid TOML flagged"

# 13. TOML with trailing whitespace → flagged (whitespace rules apply to .toml).
toml_ws="$sandbox/ws.toml"
printf 'key = "value" \n' > "$toml_ws"
run "$toml_ws" 2 "TOML trailing whitespace flagged"

# 14. TOML missing final newline → flagged.
toml_nl="$sandbox/nonl.toml"
printf 'key = "value"' > "$toml_nl"
run "$toml_nl" 2 "TOML missing final newline flagged"

printf '\n%d passed, %d failed\n' "$pass" "$fail"
[[ "$fail" -eq 0 ]]

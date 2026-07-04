#!/usr/bin/env bash
# shellcheck disable=SC2012  # ls|sort -V mirrors the hook's own NVM-dir detection
set -uo pipefail

# Script: test_markdownlint_check.sh
# Purpose: Regression tests for markdownlint-check.sh — the opt-in config gate, the
#          plans/specs carve-out, clean and dirty lint runs, and the fail-open paths
#          (tool absent, non-md, garbage stdin).
# Usage:   bash scripts/tests/test_markdownlint_check.sh

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
script="$here/../markdownlint-check.sh"

sandbox="$(mktemp -d)"
trap 'rm -rf "$sandbox"' EXIT

pass=0
fail=0

# run <file_path> <expected_exit> <label> [extra env as VAR=VAL pairs before call]
run() {
  local file="$1" want="$2" label="$3" got=0
  printf '{"tool_input":{"file_path":"%s"}}' "$file" \
    | bash "$script" >/dev/null 2>&1 || got=$?
  if [[ "$got" -eq "$want" ]]; then
    printf 'PASS  %s (exit %d)\n' "$label" "$got"
    pass=$((pass + 1))
  else
    printf 'FAIL  %s (want %d, got %d)\n' "$label" "$want" "$got"
    fail=$((fail + 1))
  fi
}

# Locate markdownlint-cli2 the same way the hook does, to decide which cases can run.
have_tool=0
if command -v markdownlint-cli2 >/dev/null 2>&1; then
  have_tool=1
else
  nvm_bin=$(ls -d "$HOME"/.nvm/versions/node/*/bin 2>/dev/null | sort -V | tail -1 || true)
  [[ -n "$nvm_bin" && -x "$nvm_bin/markdownlint-cli2" ]] && have_tool=1
fi

# ---------- Gate: no config anywhere near the file → silent pass ----------
nogate="$sandbox/plain"
mkdir -p "$nogate"
printf '#Bad\n' > "$nogate/dirty.md"
run "$nogate/dirty.md" 0 "no config -> gate closed, silent"

# ---------- Opted-in repo fixtures ----------
opted="$sandbox/opted"
mkdir -p "$opted/sub"
printf '{ "config": { "line-length": false } }\n' > "$opted/.markdownlint-cli2.jsonc"
printf '# Title\n\nsome text\n' > "$opted/sub/clean.md"
printf '#Bad heading\n' > "$opted/sub/dirty.md"

# ---------- Carve-out: dirty draft under plans/ in an opted-in repo → still silent ----------
mkdir -p "$opted/plans"
printf '#Bad\n' > "$opted/plans/draft.md"
run "$opted/plans/draft.md" 0 "plans/ carve-out (opted repo)"

if [[ "$have_tool" -eq 1 ]]; then
  run "$opted/sub/clean.md" 0 "config + clean file -> 0"
  run "$opted/sub/dirty.md" 2 "config + dirty file -> 2"
else
  printf 'SKIP  lint cases (markdownlint-cli2 not installed)\n'
fi

# ---------- Tool absent → fail open even with config present ----------
noPATH="$sandbox/bin"
mkdir -p "$noPATH"
for t in bash jq dirname ls sort tail cat printf; do
  p="$(command -v "$t")" && ln -s "$p" "$noPATH/$t"
done
got=0
printf '{"tool_input":{"file_path":"%s"}}' "$opted/sub/dirty.md" \
  | PATH="$noPATH" HOME="$sandbox/emptyhome" bash "$script" >/dev/null 2>&1 || got=$?
if [[ "$got" -eq 0 ]]; then
  printf 'PASS  tool absent -> 0 (fail open)\n'; pass=$((pass + 1))
else
  printf 'FAIL  tool absent (want 0, got %d)\n' "$got"; fail=$((fail + 1))
fi

# ---------- Non-md and fail-safe stdin ----------
printf 'x\n' > "$sandbox/file.txt"
run "$sandbox/file.txt" 0 "non-markdown ignored"

got=0
printf 'not json' | bash "$script" >/dev/null 2>&1 || got=$?
if [[ "$got" -eq 0 ]]; then
  printf 'PASS  garbage stdin -> 0 (fail-safe)\n'; pass=$((pass + 1))
else
  printf 'FAIL  garbage stdin (want 0, got %d)\n' "$got"; fail=$((fail + 1))
fi

printf '\n%d passed, %d failed\n' "$pass" "$fail"
[[ "$fail" -eq 0 ]]

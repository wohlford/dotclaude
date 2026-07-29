#!/usr/bin/env bash
# shellcheck disable=SC2012  # ls|sort -V mirrors the hook's own NVM-dir detection
set -uo pipefail

# Script: test_markdownlint_check.sh
# Purpose: Regression tests for markdownlint-check.sh — the opt-in config gate, the
#          plans/specs carve-out, clean and dirty lint runs, the fail-open paths
#          (tool absent, non-md, garbage stdin), and that the repo's own config
#          decides the verdict from any working directory.
# Usage:   bash scripts/tests/test_markdownlint_check.sh

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
script="$here/../markdownlint-check.sh"

# PHYSICAL path, deliberately: on macOS $TMPDIR is reached through a symlink
# (/tmp -> /private/tmp), and a logical cwd that does not physically contain the
# linted file sends markdownlint-cli2 down a different config-resolution path that
# MASKS the cwd bug the cases below exist to catch. A logical sandbox makes those
# regression tests pass whether or not the hook is fixed.
sandbox="$(cd "$(mktemp -d)" && pwd -P)"
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

# run_in <cwd> <file_path> <expected_exit> <label> — same, from a chosen working
# directory. cwd is load-bearing: markdownlint-cli2 walks up from the file's
# directory only as far as the working directory, so a hook that does not pin cwd
# silently lints under stock rules whenever cwd sits BELOW the config.
run_in() {
  local cwd="$1" file="$2" want="$3" label="$4" got=0
  printf '{"tool_input":{"file_path":"%s"}}' "$file" \
    | (cd "$cwd" && bash "$script") >/dev/null 2>&1 || got=$?
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

# ---------- The repo's config must decide the verdict, from ANY working directory ----------
# Its own fixture repo so the ignores below cannot perturb the cases above. The
# violations are chosen to separate the two failure modes:
#   long.md   — only MD013, a rule this config DISABLES, so it is clean iff the
#               config was actually loaded (`#Bad` would not prove that: MD018
#               fires under stock rules too, which is why the case above passes
#               either way).
#   dirty.md  — MD018, enabled everywhere, so it must still block.
cfgrepo="$sandbox/cfgrepo"
mkdir -p "$cfgrepo/sub" "$cfgrepo/drafts"
printf '{ "ignores": ["drafts/**"], "config": { "line-length": false } }\n' \
  > "$cfgrepo/.markdownlint-cli2.jsonc"
# 123 chars, and MD013 is the ONLY rule it breaks — a trailing space would add
# MD009 (which this config leaves on) and the case would fail for the wrong reason.
long_line='lorem ipsum dolor sit amet consectetur adipiscing elit sed do eiusmod tempor incididunt ut labore et dolore magna aliqua'
printf '# Title\n\n%s\n' "$long_line" > "$cfgrepo/sub/long.md"
printf '#Bad heading\n' > "$cfgrepo/sub/dirty.md"
printf '#Bad heading\n' > "$cfgrepo/drafts/skipme.md"

if [[ "$have_tool" -eq 1 ]]; then
  # Regression: cwd BELOW the config. The config is above the working directory,
  # so cli2's upward walk never reaches it and stock MD013 fires on a rule the
  # repo turned off — a false block that looks authoritative.
  run_in "$cfgrepo/sub" "$cfgrepo/sub/long.md" 0 "disabled rule stays disabled from a cwd below the config"
  # The same file from an unrelated cwd and from the config's own directory: the
  # verdict must not depend on where the hook happened to be invoked.
  run_in "/" "$cfgrepo/sub/long.md" 0 "disabled rule stays disabled from an unrelated cwd"
  run_in "$cfgrepo" "$cfgrepo/sub/long.md" 0 "disabled rule stays disabled from the config's own dir"
  # Guard, not a regression — this one passed before the fix too. An absolute path
  # reports `Linting: 1 file(s)` where a relative one reports 0, which looks like the
  # ignores being bypassed; measured, the VERDICT is the same either way. Pinned here
  # so a later change to how the path is handed over cannot quietly start linting what
  # a repo declared out of scope.
  run_in "$cfgrepo" "$cfgrepo/drafts/skipme.md" 0 "repo ignores are honored, not bypassed by an absolute path"
  # Over-blocking guard: none of the above may be achieved by silencing real findings.
  run_in "$cfgrepo/sub" "$cfgrepo/sub/dirty.md" 2 "genuine finding still blocks from a cwd below the config"
else
  printf 'SKIP  config-resolution cases (markdownlint-cli2 not installed)\n'
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

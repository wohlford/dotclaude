#!/usr/bin/env bash
set -uo pipefail

# Script: test_ruff_check.sh
# Purpose: Regression tests for ruff-check.sh — drives it via stdin-JSON in mktemp
#          sandboxes, covering the .py guard, the ruff-config gate (ruff.toml and
#          pyproject [tool.ruff]), the format and lint branches, and the no-ruff no-op.
# Usage:   bash scripts/tests/test_ruff_check.sh

script="$(cd "$(dirname "$0")/.." && pwd)/ruff-check.sh"
pass=0
fail=0

run() {
  # $1 = file_path; feeds the hook's stdin JSON, returns its exit code
  printf '{"tool_input":{"file_path":"%s"}}' "$1" | bash "$script" >/dev/null 2>&1
}
check() {
  # $1 = label, $2 = expected rc, $3 = actual rc
  if [[ "$2" -eq "$3" ]]; then
    printf 'ok   - %s\n' "$1"
    pass=$((pass + 1))
  else
    printf 'FAIL - %s (expected %s, got %s)\n' "$1" "$2" "$3"
    fail=$((fail + 1))
  fi
}

sandbox=$(mktemp -d)
trap 'rm -rf "$sandbox"' EXIT

# A repo WITH ruff config
proj="$sandbox/proj"
mkdir -p "$proj"
printf 'line-length = 88\n[lint]\nselect = ["E","F","W"]\nignore = ["E501"]\n' > "$proj/ruff.toml"

# (a) non-.py → exit 0
printf 'x\n' > "$proj/notes.md"
run "$proj/notes.md"; check "non-.py is ignored" 0 $?

# (b) clean, formatted .py in a ruff repo → exit 0
printf 'def f():\n    return 1\n' > "$proj/clean.py"
run "$proj/clean.py"; check "clean .py passes" 0 $?

# (c) 2-space-indented .py in a ruff repo → exit 2 (format drift)
printf 'def f():\n  return 1\n' > "$proj/bad.py"
run "$proj/bad.py"; check "2-space .py is flagged" 2 $?

# (d) .py in a repo with NO ruff config → exit 0 (config gate).
# This only holds if no ANCESTOR of the sandbox carries a ruff config. Detect a leak and
# skip rather than falsely fail on a machine that has e.g. ~/ruff.toml or /Users/x/ruff.toml.
noconf="$sandbox/noconf"
mkdir -p "$noconf"
anc="$noconf"
leak=0
while true; do
  if [[ -f "$anc/ruff.toml" || -f "$anc/.ruff.toml" ]] \
     || { [[ -f "$anc/pyproject.toml" ]] && grep -q '^\[tool\.ruff' "$anc/pyproject.toml"; }; then
    leak=1
    break
  fi
  [[ "$anc" == "/" ]] && break
  anc=$(dirname "$anc")
done
if [[ "$leak" -eq 1 ]]; then
  printf 'skip - no-config repo (ancestor ruff config present, cannot isolate)\n'
else
  printf 'def f():\n  return 1\n' > "$noconf/bad.py"
  run "$noconf/bad.py"; check "no-config repo is skipped" 0 $?
fi

# (e) ruff unavailable → exit 0 (availability guard).
# A bare PATH=/nonexistent would also hide cat/jq (used before the ruff guard) and crash
# the hook, not exercise the guard. Instead build a bindir with every tool the hook needs
# EXCEPT ruff, then run with PATH pointed only there → command -v ruff fails → exit 0.
bindir="$sandbox/bin"
mkdir -p "$bindir"
for t in cat jq grep dirname tail; do ln -s "$(command -v "$t")" "$bindir/$t"; done
# Resolve bash by absolute path BEFORE the PATH override — a `PATH=x cmd` prefix governs the
# lookup of cmd itself, so `PATH="$bindir" bash` would fail to find bash (it's not in bindir).
bash_abs=$(command -v bash)
printf 'def f():\n  return 1\n' > "$proj/bad2.py"
rc=0
printf '{"tool_input":{"file_path":"%s"}}' "$proj/bad2.py" \
  | PATH="$bindir" "$bash_abs" "$script" >/dev/null 2>&1 || rc=$?
check "missing ruff is a no-op" 0 "$rc"

# (f) config via pyproject.toml [tool.ruff] → gate fires (exercises the grep branch)
pyproj="$sandbox/pyproj"
mkdir -p "$pyproj"
printf '[tool.ruff]\nline-length = 88\n[tool.ruff.lint]\nselect = ["E","F","W"]\nignore = ["E501"]\n' \
  > "$pyproj/pyproject.toml"
printf 'def f():\n  return 1\n' > "$pyproj/bad.py"
run "$pyproj/bad.py"; check "pyproject [tool.ruff] activates the gate" 2 $?

# (g) well-formatted .py with a ruff CHECK finding (unused import) → exit 2 (check branch,
# independent of format). Two blank lines + 4-space keep ruff format happy; only F401 fires.
printf 'import os\n\n\ndef f():\n    return 1\n' > "$proj/unused.py"
run "$proj/unused.py"; check "ruff check finding (unused import) is flagged" 2 $?

printf -- '----\n'
printf '%s passed, %s failed\n' "$pass" "$fail"
[[ "$fail" -eq 0 ]]

#!/usr/bin/env bash
set -uo pipefail

# Script: test_guard_secrets.sh
# Purpose: Regression tests for guard-secrets.sh — deny list, exemptions, stream
#          separation, and the fail-safe paths (garbage stdin, jq absent).
# Usage:   bash scripts/tests/test_guard_secrets.sh

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
script="$here/../guard-secrets.sh"

sandbox="$(mktemp -d)"
trap 'rm -rf "$sandbox"' EXIT

pass=0
fail=0

# run <file_path> <expected_exit> <label> — capture streams separately for assertions.
run() {
  local file="$1" want="$2" label="$3" got=0 out err
  out="$sandbox/out"; err="$sandbox/err"
  printf '{"tool_input":{"file_path":"%s"}}' "$file" \
    | bash "$script" >"$out" 2>"$err" || got=$?
  if [[ "$got" -ne "$want" ]]; then
    printf 'FAIL  %s (want %d, got %d)\n' "$label" "$want" "$got"
    fail=$((fail + 1))
    return
  fi
  # A deny must explain itself on stderr and stay silent on stdout.
  if [[ "$want" -eq 2 ]]; then
    if [[ ! -s "$err" || -s "$out" ]]; then
      printf 'FAIL  %s (deny must write stderr only; stderr=%sB stdout=%sB)\n' \
        "$label" "$(wc -c <"$err" | tr -d ' ')" "$(wc -c <"$out" | tr -d ' ')"
      fail=$((fail + 1))
      return
    fi
  fi
  printf 'PASS  %s (exit %d)\n' "$label" "$got"
  pass=$((pass + 1))
}

# raw <stdin> <expected_exit> <label> — feed arbitrary stdin (fail-safe cases).
raw() {
  local stdin="$1" want="$2" label="$3" got=0
  printf '%s' "$stdin" | bash "$script" >/dev/null 2>&1 || got=$?
  if [[ "$got" -eq "$want" ]]; then
    printf 'PASS  %s (exit %d)\n' "$label" "$got"
    pass=$((pass + 1))
  else
    printf 'FAIL  %s (want %d, got %d)\n' "$label" "$want" "$got"
    fail=$((fail + 1))
  fi
}

# ---------- deny (exit 2) ----------
run "$sandbox/.env" 2 ".env denied"
run "$sandbox/.env.local" 2 ".env.local denied"
run "$sandbox/a/b/c/.env" 2 "nested .env denied"
run "$sandbox/prod.env" 2 "prod.env denied (*.env)"
run "$sandbox/server.key" 2 "server.key denied"
run "$sandbox/cert.pem" 2 "cert.pem denied"
run "$sandbox/id_rsa" 2 "id_rsa denied"
run "$sandbox/.ssh/id_ed25519" 2 "id_ed25519 denied"
run "$sandbox/id_rsa.bak" 2 "id_rsa.bak denied"
run "$sandbox/.ENV" 2 ".ENV denied (case-insensitive fs)"
run "$sandbox/PROD.ENV" 2 "PROD.ENV denied (case-insensitive fs)"
run "$sandbox/KEY.PEM" 2 "KEY.PEM denied (case-insensitive fs)"

# ---------- allow (exit 0) ----------
run "$sandbox/.env.example" 0 ".env.example allowed"
run "$sandbox/.env.sample" 0 ".env.sample allowed"
run "$sandbox/.env.template" 0 ".env.template allowed"
run "$sandbox/.env.dist" 0 ".env.dist allowed"
run "$sandbox/id_rsa.pub" 0 "id_rsa.pub allowed"
run "$sandbox/host.key.pub" 0 "host.key.pub allowed"
run "$sandbox/envfile" 0 "envfile allowed"
run "$sandbox/monkey.pens" 0 "monkey.pens allowed"
run "$sandbox/README.md" 0 "README.md allowed"
run "$sandbox/.ENV.EXAMPLE" 0 ".ENV.EXAMPLE allowed (exemptions case-fold too)"

# ---------- fail-safe ----------
raw '{}' 0 "empty JSON allowed (fail-safe)"
raw 'not-json' 0 "garbage stdin allowed (fail-safe)"
raw '' 0 "empty stdin allowed (fail-safe)"

# ---------- symlinks: deny by the resolved target's basename ----------
printf 'SECRET=1\n' >"$sandbox/.env"
printf 'notes\n' >"$sandbox/notes.md"
ln -s .env "$sandbox/innocent_name.txt"
ln -s notes.md "$sandbox/plain_link.txt"
ln -s .env "$sandbox/link.env.example"
run "$sandbox/innocent_name.txt" 2 "symlink to .env denied (target basename)"
run "$sandbox/plain_link.txt" 0 "symlink to plain file allowed"
run "$sandbox/link.env.example" 2 "exempt-looking symlink to .env still denied"

# ---------- Grep tool: the `path` key returns file contents too, so it must be guarded ----------
# Grep(pattern, path, output_mode=content) dumps a file's lines; it passes .tool_input.path, not
# .tool_input.file_path. The guard must read either key or a secret is dumped with no hook firing.
raw "{\"tool_input\":{\"path\":\"$sandbox/.env\"}}" 2 "Grep path=.env denied (.tool_input.path)"
raw "{\"tool_input\":{\"path\":\"$sandbox/prod.env\"}}" 2 "Grep path=prod.env denied (*.env)"
raw "{\"tool_input\":{\"path\":\"$sandbox/README.md\"}}" 0 "Grep path=README.md allowed"
# When both keys are present, either one matching a secret must deny.
raw "{\"tool_input\":{\"path\":\"$sandbox/.env\",\"file_path\":\"$sandbox/ok.md\"}}" 2 "path secret denies even with innocent file_path"

# ---------- jq absent: the sed fallback must still deny/allow correctly ----------
nojq="$sandbox/nojq-bin"
mkdir -p "$nojq"
for t in bash sed head printf cat; do
  p="$(command -v "$t" 2>/dev/null)" && ln -s "$p" "$nojq/$t"
done
got=0
printf '{"tool_input":{"file_path":"%s/.env"}}' "$sandbox" \
  | PATH="$nojq" bash "$script" >/dev/null 2>&1 || got=$?
if [[ "$got" -eq 2 ]]; then
  printf 'PASS  no-jq fallback still denies .env (exit 2)\n'
  pass=$((pass + 1))
else
  printf 'FAIL  no-jq fallback (want 2, got %d)\n' "$got"
  fail=$((fail + 1))
fi
got=0
printf '{"tool_input":{"file_path":"%s/README.md"}}' "$sandbox" \
  | PATH="$nojq" bash "$script" >/dev/null 2>&1 || got=$?
if [[ "$got" -eq 0 ]]; then
  printf 'PASS  no-jq fallback still allows README.md (exit 0)\n'
  pass=$((pass + 1))
else
  printf 'FAIL  no-jq fallback allow (want 0, got %d)\n' "$got"
  fail=$((fail + 1))
fi

printf '\n%d passed, %d failed\n' "$pass" "$fail"
[[ "$fail" -eq 0 ]]

#!/usr/bin/env bash
set -uo pipefail

# Script: test_py39_compat.sh
# Purpose: Regression tests — every Python hook must import and rule correctly under Python 3.9,
#          where a missing `from __future__ import annotations` silently turns a gate into a no-op.
# Usage:   bash scripts/tests/test_py39_compat.sh

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
root="$here/../.."
pass=0
fail=0

note() { printf '%s\n' "$1"; }
ok()   { printf 'PASS  %s\n' "$1"; pass=$((pass + 1)); }
bad()  { printf 'FAIL  %s\n' "$1"; fail=$((fail + 1)); }

# --- part 1: static — the future import must be present AND effective (not inside a docstring) ---
for f in "$root"/scripts/*.py "$root"/scripts/lib/*.py; do
  base="$(basename "$f")"
  if python3 - "$f" <<'PY'
import ast, sys
tree = ast.parse(open(sys.argv[1]).read())
body = tree.body
i = 0
if body and isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], "value", None), ast.Constant) \
        and isinstance(body[0].value.value, str):
    i = 1
found = any(isinstance(n, ast.ImportFrom) and n.module == "__future__"
            and any(a.name == "annotations" for a in n.names) for n in body[i:i + 3])
sys.exit(0 if found else 1)
PY
  then ok "$base declares an effective __future__ annotations import"
  else bad "$base is missing an EFFECTIVE __future__ annotations import (inside a docstring counts as missing)"
  fi
done

# --- part 2: behavioural — find a <=3.9 interpreter; SKIP (never fail) if none exists ---
py39=""
for cand in /usr/bin/python3 /Applications/Xcode.app/Contents/Developer/usr/bin/python3 python3.9; do
  p="$(command -v "$cand" 2>/dev/null || true)"
  [ -n "$p" ] || continue
  ver="$("$p" -c 'import sys;print("%d%02d"%sys.version_info[:2])' 2>/dev/null || echo 9999)"
  if [ "$ver" -lt 310 ]; then py39="$p"; break; fi
done

if [ -z "$py39" ]; then
  note "SKIP  no <=3.9 interpreter on this machine — static checks only (a coverage gap, not a pass)"
else
  note "note  behavioural checks using $py39"
  probe() { # script payload want label
    local got=0
    printf '%s' "$2" | "$py39" "$root/scripts/$1" >/dev/null 2>&1 || got=$?
    if [ "$got" -eq "$3" ]; then ok "$4 (exit $got)"; else bad "$4 (want $3, got $got)"; fi
  }
  payload_push="{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"git push origin dev\"},\"cwd\":\"$root\"}"
  payload_safe="{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"git status\"},\"cwd\":\"$root\"}"
  probe push-guard.py             "$payload_push" 2 'push-guard BLOCKS a bare push under 3.9'
  probe push-guard.py             "$payload_safe" 0 'push-guard allows a non-push under 3.9'
  probe publication-push-guard.py "$payload_push" 2 'publication-push-guard BLOCKS a dev push under 3.9'
  probe recast-commit-gate.py     "$payload_safe" 0 'recast-commit-gate allows a non-commit under 3.9'
fi

printf '\n%d passed, %d failed\n' "$pass" "$fail"
[ "$fail" -eq 0 ]

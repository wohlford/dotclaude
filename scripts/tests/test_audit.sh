#!/usr/bin/env bash
set -uo pipefail

# Script: test_audit.sh
# Purpose: Regression tests for skills/audit/audit.sh — the read-only mechanical compliance
#          sweep. Covers every check (format/lint/exec-bit/json/toml/sync-docs/tests),
#          --scope resolution and validation, tracked-only scoping, tool-absent SKIPs, and
#          the BSD-safe nvm-version picker used by the markdownlint check.
# Usage:   ./scripts/tests/test_audit.sh

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
engine="$here/../../skills/audit/audit.sh"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

pass=0
fail=0

# NOTE: the engine is invoked BARE-PATH ("$engine", never `bash "$engine"`) — the suite
# thereby also verifies the exec bit and the bash-3.2 shebang compatibility it depends on.

pass_line() { printf 'PASS  %s\n' "$1"; pass=$((pass + 1)); }
fail_line() { printf 'FAIL  %s\n' "$1"; fail=$((fail + 1)); }

check_eq() { # got want label
  if [[ "$1" == "$2" ]]; then
    pass_line "$3"
  else
    fail_line "$3 (want [$2] got [$1])"
  fi
}

# run_engine SCOPE [extra-args...] -> sets OUT (stdout+stderr merged) and RC
OUT=""
RC=0
run_engine() {
  local scope="$1"
  shift
  OUT="$("$engine" --scope "$scope" "$@" 2>&1)"
  RC=$?
}

# run_raw [args...] -> sets OUT/RC without forcing --scope (usage-error / default-scope cases)
run_raw() {
  OUT="$("$engine" "$@" 2>&1)"
  RC=$?
}

# run_in_cwd DIR [args...] -> cd DIR first, then run engine with the given args verbatim
run_in_cwd() {
  local dir="$1"
  shift
  OUT="$(cd "$dir" && "$engine" "$@" 2>&1)"
  RC=$?
}

assert_rc() { # want label -> uses $RC
  check_eq "$RC" "$1" "$2"
}

assert_has() { # needle label -> uses $OUT
  case "$OUT" in
    *"$1"*) pass_line "$2" ;;
    *)
      fail_line "$2"
      printf '  --- output ---\n%s\n  --------------\n' "$OUT"
      ;;
  esac
}

assert_not_has() { # needle label -> uses $OUT
  case "$OUT" in
    *"$1"*)
      fail_line "$2"
      printf '  --- output ---\n%s\n  --------------\n' "$OUT"
      ;;
    *) pass_line "$2" ;;
  esac
}

mkrepo() { # dir -> git init with test identity, signing/autocrlf/safecrlf off
  git init -q "$1"
  git -C "$1" config user.email test@test.invalid
  git -C "$1" config user.name test
  git -C "$1" config commit.gpgsign false
  git -C "$1" config core.autocrlf false
  git -C "$1" config core.safecrlf false
}

commit_all() { # dir msg
  git -C "$1" add -A
  git -C "$1" commit -q -m "$2"
}

mk_clean_repo() { # dir -> a fully clean, multi-filetype fixture, all committed
  mkrepo "$1"
  printf '#!/usr/bin/env bash\nset -uo pipefail\n\n# Script: good.sh\n# Purpose: fixture\n# Usage: ./good.sh\nprintf "hi\\n"\n' > "$1/good.sh"
  chmod +x "$1/good.sh"
  printf 'def hi() -> None:\n    print("hi")\n' > "$1/good.py"
  printf '# Doc\n\nSee [link](./good.py).\n' > "$1/good.md"
  printf '{\n  "a": 1\n}\n' > "$1/good.json"
  printf 'a = 1\n' > "$1/good.toml"
  commit_all "$1" seed
}

# ============================================================================
# 1. Clean repo -> exit 0, PASS format-trailing-ws + PASS exec-bit, no FAIL lines
# ============================================================================
r1="$tmp/r1_clean"
mk_clean_repo "$r1"
run_engine "$r1"
assert_rc 0 'r1: clean repo -> exit 0'
assert_has 'PASS format-trailing-ws' 'r1: PASS format-trailing-ws present'
assert_has 'PASS exec-bit' 'r1: PASS exec-bit present'
assert_not_has 'FAIL' 'r1: no FAIL lines'

# ---- case 11 folded in: no markdownlint config / no sync markers -> SKIP both ----
assert_has 'SKIP markdownlint' 'r1: repo not opted in -> SKIP markdownlint'
assert_has 'SKIP sync-docs' 'r1: no sync markers -> SKIP sync-docs'

# ============================================================================
# 2. Trailing whitespace -> exit 1 + FAIL format-trailing-ws
# ============================================================================
r2="$tmp/r2_trailing_ws"
mkrepo "$r2"
printf 'clean line\nline with trailing space \n' > "$r2/f.txt"
commit_all "$r2" x
run_engine "$r2"
assert_rc 1 'r2: trailing whitespace -> exit 1'
assert_has 'FAIL format-trailing-ws' 'r2: FAIL format-trailing-ws'

# ============================================================================
# 3. CRLF file -> FAIL format-crlf
# ============================================================================
r3="$tmp/r3_crlf"
mkrepo "$r3"
printf 'a\r\nb\r\n' > "$r3/crlf.txt"
commit_all "$r3" x
run_engine "$r3"
assert_rc 1 'r3: CRLF -> exit 1'
assert_has 'FAIL format-crlf' 'r3: FAIL format-crlf'

# ============================================================================
# 4. Missing final newline -> FAIL format-final-newline
# ============================================================================
r4="$tmp/r4_final_newline"
mkrepo "$r4"
printf 'no trailing newline here' > "$r4/f.txt"
commit_all "$r4" x
run_engine "$r4"
assert_rc 1 'r4: missing final newline -> exit 1'
assert_has 'FAIL format-final-newline' 'r4: FAIL format-final-newline'

# ============================================================================
# 5. Tab in a .sh -> FAIL format-tabs; tab in an out-of-scope extension -> no FAIL
# ============================================================================
r5a="$tmp/r5a_tab_in_scope"
mkrepo "$r5a"
printf '#!/usr/bin/env bash\nset -uo pipefail\n# note:\ttabbed comment\nprintf "hi\\n"\n' > "$r5a/a.sh"
chmod +x "$r5a/a.sh"
commit_all "$r5a" x
run_engine "$r5a"
assert_has 'FAIL format-tabs' 'r5a: tab inside .sh -> FAIL format-tabs'

r5b="$tmp/r5b_tab_out_of_scope"
mkrepo "$r5b"
printf 'plain\ttabbed text file\n' > "$r5b/x.txt"
commit_all "$r5b" x
run_engine "$r5b"
assert_not_has 'FAIL format-tabs' 'r5b: tab in .txt (out of scope) -> no FAIL format-tabs'

# ============================================================================
# 6. shellcheck warning-level finding (SC2034 unused local var) -> FAIL shellcheck
#    NOTE: SC2086 (unquoted expansion) is "info" severity, below -S warning, so it would
#    not trip this check — SC2034 (unused variable) is a genuine "warning"-level finding.
# ============================================================================
if command -v shellcheck >/dev/null 2>&1; then
  r6="$tmp/r6_shellcheck"
  mkrepo "$r6"
  printf '#!/usr/bin/env bash\nset -uo pipefail\nfoo() {\n  local unused=1\n  echo hi\n}\nfoo\n' > "$r6/bad.sh"
  chmod +x "$r6/bad.sh"
  commit_all "$r6" x
  run_engine "$r6"
  assert_has 'FAIL shellcheck' 'r6: SC2034 unused local var -> FAIL shellcheck'
else
  printf 'skip - shellcheck not installed in this test environment\n'
fi

# ============================================================================
# 7. ruff F401 unused import -> FAIL ruff
# ============================================================================
if command -v ruff >/dev/null 2>&1; then
  r7="$tmp/r7_ruff"
  mkrepo "$r7"
  printf 'import os\n\n\ndef f() -> None:\n    pass\n' > "$r7/bad.py"
  commit_all "$r7" x
  run_engine "$r7"
  assert_has 'FAIL ruff' 'r7: unused import (F401) -> FAIL ruff'
else
  printf 'skip - ruff not installed in this test environment\n'
fi

# ============================================================================
# 8. .md relative link to a missing file -> FAIL md-links
# ============================================================================
r8="$tmp/r8_md_links"
mkrepo "$r8"
printf '# Doc\n\nSee [missing](./nope.md) for more.\n' > "$r8/doc.md"
commit_all "$r8" x
run_engine "$r8"
assert_has 'FAIL md-links' 'r8: broken relative link -> FAIL md-links'

# ============================================================================
# 9. New shebang file committed 100644 -> FAIL exec-bit
# ============================================================================
r9="$tmp/r9_exec_bit"
mkrepo "$r9"
printf '#!/bin/sh\necho hi\n' > "$r9/hook.sh"
chmod 644 "$r9/hook.sh"
commit_all "$r9" x
run_engine "$r9"
assert_has 'FAIL exec-bit' 'r9: shebang file staged 644 -> FAIL exec-bit'

# ============================================================================
# 10. Invalid JSON -> FAIL json (skip if jq absent). Invalid TOML -> FAIL toml.
# ============================================================================
if command -v jq >/dev/null 2>&1; then
  r10a="$tmp/r10a_json"
  mkrepo "$r10a"
  printf '{ "a": }\n' > "$r10a/bad.json"
  commit_all "$r10a" x
  run_engine "$r10a"
  assert_has 'FAIL json' 'r10a: malformed JSON -> FAIL json'
else
  printf 'skip - jq not installed in this test environment\n'
fi

if python3 -c 'import tomllib' >/dev/null 2>&1; then
  r10b="$tmp/r10b_toml"
  mkrepo "$r10b"
  printf 'a = [1, 2\n' > "$r10b/bad.toml"
  commit_all "$r10b" x
  run_engine "$r10b"
  assert_has 'FAIL toml' 'r10b: malformed TOML -> FAIL toml'
else
  printf 'skip - python3 tomllib not available in this test environment\n'
fi

# ============================================================================
# 12. Tool-absent -> SKIP (never FAIL); exit 0 given no other violations
# ============================================================================
r12="$tmp/r12_tool_absent"
mkrepo "$r12"
printf '#!/usr/bin/env bash\nset -uo pipefail\nprintf "hi\\n"\n' > "$r12/good.sh"
chmod +x "$r12/good.sh"
commit_all "$r12" x
OUT="$(PATH=/usr/bin:/bin "$engine" --scope "$r12" 2>&1)"; RC=$?
assert_has 'SKIP shellcheck' 'r12: shellcheck absent under restricted PATH -> SKIP shellcheck'
assert_not_has 'FAIL' 'r12: restricted PATH, clean repo -> no FAIL lines'
assert_rc 0 'r12: restricted PATH, no violations -> exit 0'

# ============================================================================
# 13. Untracked violation file (never git add'ed) -> still exit 0 (tracked-only)
# ============================================================================
r13="$tmp/r13_untracked"
mkrepo "$r13"
printf 'clean\n' > "$r13/tracked.txt"
commit_all "$r13" x
printf 'has trailing space \n' > "$r13/untracked.txt"   # deliberately never git add'ed
run_engine "$r13"
assert_rc 0 'r13: untracked violation is invisible to the sweep -> exit 0'
assert_not_has 'FAIL' 'r13: untracked violation -> no FAIL lines'

# ============================================================================
# 14. --scope resolution and validation
# ============================================================================
run_in_cwd "$tmp" --scope "$r1"
assert_rc 0 '14a: --scope <fixture> from an unrelated cwd works'
assert_has 'PASS format-trailing-ws' '14a: unrelated cwd still sweeps the scoped repo'

run_raw --scope /nonexistent-path-xyz
assert_rc 2 '14b: --scope /nonexistent -> exit 2'

run_raw --scope "$tmp"   # tmp itself is not a git repo
assert_rc 2 '14c: --scope on a non-repo dir -> exit 2'

run_raw --bogus-flag
assert_rc 2 '14d: unknown flag -> exit 2'

# ============================================================================
# 15. --tests: shell test suite runs only when requested
# ============================================================================
r15="$tmp/r15_tests"
mkrepo "$r15"
mkdir -p "$r15/scripts/tests"
printf '#!/usr/bin/env bash\nexit 0\n' > "$r15/scripts/tests/test_pass.sh"
chmod +x "$r15/scripts/tests/test_pass.sh"
commit_all "$r15" x

run_engine "$r15"
assert_not_has 'PASS tests' '15a: without --tests, no PASS tests line'
assert_not_has 'FAIL tests' '15a: without --tests, no FAIL tests line'
assert_not_has 'SKIP tests' '15a: without --tests, no SKIP tests line'

run_engine "$r15" --tests
assert_has 'PASS tests' '15b: --tests with a passing suite -> PASS tests'

printf '#!/usr/bin/env bash\nexit 1\n' > "$r15/scripts/tests/test_pass.sh"
chmod +x "$r15/scripts/tests/test_pass.sh"
commit_all "$r15" flip
run_engine "$r15" --tests
assert_has 'FAIL tests' '15c: --tests with a failing suite -> FAIL tests'

# ============================================================================
# 16. BSD-safe newest-version picker (used by the markdownlint node-bin fallback)
# ============================================================================
picked="$(bash -c 'source "$1"; printf "v9.1.0\nv26.3.0\nv10.2.1\n" | pick_newest_version' _ "$engine")"
check_eq "$picked" 'v26.3.0' '16: pick_newest_version selects highest semver (no sort -V)'

# ============================================================================
# rA. .auditignore exclusion is load-bearing, both directions
# ============================================================================
rA="$tmp/rA_auditignore"
mkrepo "$rA"
mkdir -p "$rA/gen"
printf 'clean line\nline with trailing space \n' > "$rA/gen/transcript.md"
commit_all "$rA" seed
run_engine "$rA"
assert_has 'FAIL format-trailing-ws' 'rA: trailing ws in gen/, no .auditignore -> FAIL format-trailing-ws'

printf 'gen/*\n' > "$rA/.auditignore"
commit_all "$rA" 'add auditignore'
run_engine "$rA"
assert_has 'PASS format-trailing-ws' 'rA: gen/* excluded -> PASS format-trailing-ws'
assert_rc 0 'rA: rest of fixture clean -> exit 0'

# ============================================================================
# rB. .auditignore comments/blank lines are ignored; the real glob still excludes
# ============================================================================
rB="$tmp/rB_comments"
mkrepo "$rB"
mkdir -p "$rB/gen"
printf 'line with trailing space \n' > "$rB/gen/x.txt"
commit_all "$rB" seed
printf '# comment\n  # indented comment\n\ngen/*\n' > "$rB/.auditignore"
commit_all "$rB" 'add auditignore'
run_engine "$rB"
assert_has 'PASS format-trailing-ws' 'rB: comments/blanks ignored, real glob still excludes -> PASS'
assert_rc 0 'rB: exit 0, no crash on comment/blank lines'

# ============================================================================
# rC. CRLF + missing-final-newline honor excludes
# ============================================================================
rC="$tmp/rC_crlf_finalnl"
mkrepo "$rC"
mkdir -p "$rC/gen"
printf 'a\r\nb\r\n' > "$rC/gen/crlf.txt"
printf 'no trailing newline' > "$rC/gen/nofinalnl.txt"
commit_all "$rC" seed
printf 'gen/*\n' > "$rC/.auditignore"
commit_all "$rC" 'add auditignore'
run_engine "$rC"
assert_has 'PASS format-crlf' 'rC: CRLF file excluded -> PASS format-crlf'
assert_has 'PASS format-final-newline' 'rC: missing-final-newline file excluded -> PASS format-final-newline'

# ============================================================================
# rD. output cap — 60 trailing-ws offenders truncate with a "more" marker
#     NOTE: this only pins the print cap (print_offenders' 50-line sed window). It cannot
#     distinguish that from the collection cap (check_format_trailing_ws's `head -n 51`) —
#     removing the `head -n 51` collection cap would still pass this test unchanged, since
#     the print cap alone is enough to produce the "more" marker and hide line60. Deliberate,
#     deferred: a test that pins the collection cap too would need a much larger fixture.
# ============================================================================
rD="$tmp/rD_cap"
mkrepo "$rD"
i=1
: > "$rD/manylines.txt"
while [[ "$i" -le 60 ]]; do
  printf 'line%d \n' "$i" >> "$rD/manylines.txt"
  i=$((i + 1))
done
commit_all "$rD" seed
run_engine "$rD"
assert_has 'FAIL format-trailing-ws' 'rD: 60 offending lines -> FAIL format-trailing-ws'
assert_has '… more (run the underlying tool' 'rD: output cap message present'
assert_not_has 'line60' 'rD: 60th offender line absent from (capped) output'

# ============================================================================
# rE. no .auditignore present -> behavior unchanged (guards against excludes
#     applying when the ignore string is empty)
# ============================================================================
rE="$tmp/rE_no_auditignore"
mkrepo "$rE"
printf 'clean\nline with trailing space \n' > "$rE/f.txt"
commit_all "$rE" x
run_engine "$rE"
assert_has 'FAIL format-trailing-ws' 'rE: no .auditignore present -> still FAIL format-trailing-ws'

# ============================================================================
# rF. excludes never silence code/config checks (locks spec O1)
# ============================================================================
if command -v jq >/dev/null 2>&1; then
  rF="$tmp/rF_locked_json"
  mkrepo "$rF"
  mkdir -p "$rF/gen"
  printf '{ "a": }\n' > "$rF/gen/bad.json"
  printf 'gen/*\n' > "$rF/.auditignore"
  commit_all "$rF" seed
  run_engine "$rF"
  assert_has 'FAIL json' 'rF: .auditignore excludes the dir but json check still FAILs'
else
  printf 'skip - jq not installed in this test environment\n'
fi

# ============================================================================
# rG. visibility line — present only when active patterns exist
# ============================================================================
rG="$tmp/rG_visibility"
mkrepo "$rG"
printf 'clean\n' > "$rG/f.txt"
commit_all "$rG" seed
run_engine "$rG"
assert_not_has '(.auditignore:' 'rG: no .auditignore file -> no visibility line'

printf 'gen/*\n' > "$rG/.auditignore"
commit_all "$rG" 'add auditignore'
run_engine "$rG"
assert_has '(.auditignore:' 'rG: active .auditignore -> visibility line present'
assert_has '(.auditignore: 1 exclude pattern(s) active)' 'rG: exact visibility count text'

# ============================================================================
# rH. invalid-only .auditignore pattern -> FAIL auditignore, offender still caught
#     (Finding 1: `/gen/*` is gitignore-anchored syntax git's pathspec engine rejects
#     outright — rc 128. With it the ONLY pattern, zero valid patterns survive, so the
#     sweep must proceed unexcluded and still catch the real offender.)
# ============================================================================
rH="$tmp/rH_invalid_only"
mkrepo "$rH"
printf 'clean line\nline with trailing space \n' > "$rH/outside.txt"
printf '/gen/*\n' > "$rH/.auditignore"
commit_all "$rH" seed
run_engine "$rH"
assert_has 'FAIL auditignore' 'rH: invalid-only pattern -> FAIL auditignore'
assert_has '/gen/*' 'rH: FAIL auditignore names the bad pattern'
assert_has 'FAIL format-trailing-ws' 'rH: zero valid patterns remain -> offender still caught'
assert_rc 1 'rH: invalid pattern -> exit 1'

# ============================================================================
# rI. mixed valid + invalid .auditignore patterns -> FAIL auditignore names the
#     invalid one, but the valid pattern's exclusion is still honored
# ============================================================================
rI="$tmp/rI_mixed"
mkrepo "$rI"
mkdir -p "$rI/gen"
printf 'line with trailing space \n' > "$rI/gen/x.txt"
printf 'clean\n' > "$rI/clean.txt"
printf 'gen/*\n../outside\n' > "$rI/.auditignore"
commit_all "$rI" seed
run_engine "$rI"
assert_has 'FAIL auditignore' 'rI: mixed valid+invalid -> FAIL auditignore'
assert_has '../outside' 'rI: FAIL auditignore names the invalid pattern'
assert_not_has 'FAIL format-trailing-ws' 'rI: valid gen/* exclusion still effective -> no FAIL format-trailing-ws'
assert_rc 1 'rI: invalid pattern present -> exit 1 despite valid exclusion working'

# ============================================================================
# rJ. tracked binary blob -> exec-bit PASS, no "null byte" warning on stderr
#     (Finding 2: the old check_exec_bit forked `git cat-file` per file through a command
#     substitution, which bash warns about on binary content — the same per-file fork was
#     the root cause of a 12,853-fork acceptance-test timeout on a real repo. The fork-count
#     regression itself is untestable here — timing/environment-dependent; its regression
#     test is the court-repo acceptance run the controller re-executes after this wave.)
# ============================================================================
rJ="$tmp/rJ_binary_blob"
mkrepo "$rJ"
printf '\x00\x01\x02' > "$rJ/blob.bin"
chmod 644 "$rJ/blob.bin"
commit_all "$rJ" seed
run_engine "$rJ"
assert_has 'PASS exec-bit' 'rJ: binary blob (100644, no shebang) -> PASS exec-bit'
assert_not_has 'null byte' 'rJ: engine stderr does not warn about null byte in input'

# ============================================================================
# rK. .auditignore excludes format-tabs matches too (mirrors rA for trailing-ws; pins
#     the ignore threading specifically through check_format_tabs so a regression
#     dropping it fails the suite)
# ============================================================================
rK="$tmp/rK_tabs_exclude"
mkrepo "$rK"
mkdir -p "$rK/gen"
printf '# Doc\n\tindented with a tab\n' > "$rK/gen/transcript.md"
commit_all "$rK" seed
run_engine "$rK"
assert_has 'FAIL format-tabs' 'rK: tab in gen/, no .auditignore -> FAIL format-tabs'

printf 'gen/*\n' > "$rK/.auditignore"
commit_all "$rK" 'add auditignore'
run_engine "$rK"
assert_has 'PASS format-tabs' 'rK: gen/* excluded -> PASS format-tabs'

# ============================================================================
# rL. .auditignore excludes md-links matches too (mirrors rA for trailing-ws; pins
#     the ignore threading specifically through check_md_links so a regression
#     dropping it fails the suite)
# ============================================================================
rL="$tmp/rL_mdlinks_exclude"
mkrepo "$rL"
mkdir -p "$rL/gen"
printf '# Doc\n\nSee [missing](./nope.md) for more.\n' > "$rL/gen/broken.md"
commit_all "$rL" seed
run_engine "$rL"
assert_has 'FAIL md-links' 'rL: broken link in gen/, no .auditignore -> FAIL md-links'

printf 'gen/*\n' > "$rL/.auditignore"
commit_all "$rL" 'add auditignore'
run_engine "$rL"
assert_has 'PASS md-links' 'rL: gen/* excluded -> PASS md-links'

printf '\n%d passed, %d failed\n' "$pass" "$fail"
[[ "$fail" -eq 0 ]]

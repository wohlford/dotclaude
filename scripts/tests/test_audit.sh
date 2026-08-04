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

# run_engine_stdout SCOPE [extra-args...] -> OUT = STDOUT ONLY, RC
# Needed for last-line assertions: run_engine merges 2>&1, and the interleaving of
# two file descriptors into one pipe is not ordering-guaranteed, so "the last line"
# is only well-defined on a single stream.
run_engine_stdout() {
  local scope="$1"
  shift
  OUT="$("$engine" --scope "$scope" "$@" 2>/dev/null)"
  RC=$?
}

# run_raw_stdout [args...] -> OUT = STDOUT ONLY, RC (usage-error cases)
run_raw_stdout() {
  OUT="$("$engine" "$@" 2>/dev/null)"
  RC=$?
}

assert_last_line_prefix() { # prefix label -> uses $OUT
  local last
  last="$(printf '%s\n' "$OUT" | tail -1)"
  case "$last" in
    "$1"*) pass_line "$2" ;;
    *)
      fail_line "$2"
      printf '  --- last line was ---\n%s\n  ---------------------\n' "$last"
      ;;
  esac
}

count_result_lines() { # -> echoes the number of column-anchored RESULT: lines in $OUT
  printf '%s\n' "$OUT" | grep -c '^RESULT:' | tr -d ' '
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

# ============================================================================
# rM. RESULT verdict line — the machine-readable terminal status.
#     Regression: audit.sh's only terminal signal was the human summary, so a run
#     that died before reaching it printed a PREFIX of PASS lines and nothing else,
#     and grep-for-FAIL read that as clean. The fix is a line whose ABSENCE is
#     meaningful, asserted by PRESENCE of an allowlisted value.
#     Counts are NOT hardcoded: which checks PASS vs SKIP depends on which tools
#     are installed on the running machine, so these pin the shape and the rc.
# ============================================================================
rM="$tmp/rM_result_clean"
mk_clean_repo "$rM"
run_engine_stdout "$rM"
assert_rc 0 'rM1: clean repo -> exit 0'
assert_last_line_prefix 'RESULT: PASS rc=0 checks=' 'rM1: clean run ends with RESULT: PASS rc=0'
check_eq "$(count_result_lines)" 1 'rM2: exactly one column-anchored RESULT line (no subshell leakage)'

rM3="$tmp/rM_result_fail"
mkrepo "$rM3"
printf 'line with trailing space \n' > "$rM3/dirty.txt"
commit_all "$rM3" seed
run_engine_stdout "$rM3"
assert_rc 1 'rM3: repo with a finding -> exit 1'
assert_last_line_prefix 'RESULT: FAIL rc=1 checks=' 'rM3: failing run ends with RESULT: FAIL rc=1'

mkdir -p "$tmp/rM_not_a_repo"
run_raw_stdout --scope "$tmp/rM_not_a_repo"
assert_rc 2 'rM4: --scope at a non-git path -> exit 2'
check_eq "$OUT" 'RESULT: ERROR rc=2 checks=0/0/0' 'rM4: usage error still emits a verdict line on stdout'

# rM5 pins hazard 1: the traps must live INSIDE the BASH_SOURCE guard. A top-level
# `trap ... EXIT` would install into any shell that sources the engine for unit
# testing (see case 16) and append a RESULT line to that shell's captured stdout.
# NOTE: this assertion passes both BEFORE and AFTER the fix — it is a tripwire
# against one specific wrong implementation, not a RED test.
sourced="$(bash -c 'source "$1"; printf "sentinel\n"' _ "$engine" 2>/dev/null)"
check_eq "$sourced" 'sentinel' 'rM5: sourcing the engine installs no EXIT trap'

# rM6/rM7 pin the exit handler's defining property: it can emit ONLY ERROR or
# INCOMPLETE. PASS and FAIL are emitted by main() itself on the completed path, so a
# process killed mid-sweep cannot produce a clean verdict no matter what `$?` says.
# Driving audit_on_exit directly keeps this deterministic instead of racing a signal.
incomplete="$(bash -c 'source "$1"
audit_phase=sweeping; pass_count=7; fail_count=0; skip_count=1
audit_on_exit 143' _ "$engine" 2>/dev/null)"
check_eq "$incomplete" 'RESULT: INCOMPLETE rc=143 checks=7/0/1' \
  'rM6: death mid-sweep reports INCOMPLETE, never PASS'

# rM7 is the sharper half, and it models the case that motivated the restructure: an
# UNTRAPPED fatal signal. `$?` inside an EXIT trap reads 0 for those (measured: SIGUSR1
# kills the process with rc 158 while the trap sees 0), so the handler is handed the
# most PASS-looking state possible — rc 0, zero failures. It must still say INCOMPLETE.
# Any design that decides PASS-vs-not inside the handler fails this.
killed_looks_clean="$(bash -c 'source "$1"
audit_phase=sweeping; pass_count=3; fail_count=0; skip_count=0
audit_on_exit 0' _ "$engine" 2>/dev/null)"
check_eq "$killed_looks_clean" 'RESULT: INCOMPLETE rc=0 checks=3/0/0' \
  'rM7: exit handler cannot emit PASS even at rc=0 with zero failures'

# rM8 is the end-to-end signal path. Without it, deleting all three signal traps leaves
# the suite fully green — the whole-branch review proved that by mutation, so the suite
# was blind on precisely the axis this change exists to fix. Signalling the process
# GROUP (not just the script) is what makes it prompt: bash defers a trap until the
# running foreground child exits, so a script-only TERM would stall behind shellcheck.
rM8_out="$tmp/rM8.out"
set -m
"$engine" --scope "$(cd "$here/../.." && pwd)" > "$rM8_out" 2>&1 &
rM8_pid=$!
sleep 1
kill -TERM -"$rM8_pid" 2>/dev/null
wait "$rM8_pid" 2>/dev/null
rM8_rc=$?
set +m
# NOTE the exit code alone CANNOT tell a trapped TERM from an untrapped one: with the
# trap deleted the process still dies by SIGTERM and `wait` still reports 128+15. Verified
# by mutation — this assertion passes against an engine with no TERM trap at all. It is
# kept only as a sanity check that the run died rather than completing; the RESULT line
# below is what actually discriminates (mutant: `RESULT: INCOMPLETE rc=0`).
check_eq "$rM8_rc" 143 'rM8: process-group TERM -> the run dies with 143'
# Counts are whatever the sweep reached in one second, so pin the shape, not the numbers.
# The `rc=143` in this line is the load-bearing part: it can only appear if the TERM trap
# ran and converted the signal into a real exit status. Timing margin is ~35x — this scope
# takes about 35 seconds to sweep and the kill lands at 1 second.
rM8_last="$(grep '^RESULT:' "$rM8_out" | tail -1)"
case "$rM8_last" in
  'RESULT: INCOMPLETE rc=143 checks='*)
    pass_line 'rM8: a real TERM mid-sweep emits RESULT: INCOMPLETE rc=143' ;;
  *)
    fail_line 'rM8: a real TERM mid-sweep emits RESULT: INCOMPLETE rc=143'
    printf '  --- RESULT line was ---\n%s\n  -----------------------\n' "$rM8_last" ;;
esac

# ============================================================================
# rN. hermetic — a --tests run must leave the working tree exactly as it found it
#
# The measured instance (2026-07-29): a documented command wrote its artifact to the repo
# root, where it fails the NEXT publish brick's clean-tree precondition — the publish path
# blocking itself on a file its own documentation told the operator to create. Nothing
# surfaced it. Every other check here reads `git ls-files`, so a file a suite DROPS is
# structurally invisible to all of them, and the suite itself still exits 0.
#
# The check compares BEFORE against AFTER rather than asserting a clean tree, so a repo
# that was already dirty does not trip it (rN5) — only what the run itself changed counts.
# ============================================================================

# --- rN1: a suite that writes nothing -> PASS, and no line at all without --tests ---
rN1="$tmp/rN1_clean"
mkrepo "$rN1"
mkdir -p "$rN1/scripts/tests"
printf '#!/usr/bin/env bash\nexit 0\n' > "$rN1/scripts/tests/test_quiet.sh"
chmod +x "$rN1/scripts/tests/test_quiet.sh"
commit_all "$rN1" seed

run_engine "$rN1" --tests
assert_has 'PASS tests' 'rN1: clean suite -> PASS tests'
assert_has 'PASS hermetic' 'rN1: a suite that writes nothing -> PASS hermetic'

run_engine "$rN1"
assert_not_has 'PASS hermetic' 'rN1: without --tests, no PASS hermetic line'
assert_not_has 'FAIL hermetic' 'rN1: without --tests, no FAIL hermetic line'
assert_not_has 'SKIP hermetic' 'rN1: without --tests, no SKIP hermetic line'

# --- rN2: the defect — an untracked artifact dropped in the repo root ---
rN2="$tmp/rN2_drops"
mkrepo "$rN2"
mkdir -p "$rN2/scripts/tests"
# SC2016: the `$(dirname "$0")` must reach the generated fixture UNEXPANDED — it resolves
# when that script runs, inside its own repo. Expanding it here would point every fixture
# at this suite's directory instead.
# shellcheck disable=SC2016
printf '#!/usr/bin/env bash\nprintf "x\\n" > "$(dirname "$0")/../../tip-audit.txt"\nexit 0\n' \
  > "$rN2/scripts/tests/test_drops.sh"
chmod +x "$rN2/scripts/tests/test_drops.sh"
commit_all "$rN2" seed

run_engine "$rN2" --tests
assert_has 'PASS tests' 'rN2: the polluting suite still EXITS 0 — the tests check alone cannot catch it'
assert_has 'FAIL hermetic' 'rN2: an untracked artifact left behind -> FAIL hermetic'
assert_has 'tip-audit.txt' 'rN2: the FAIL names the offending path'
assert_rc 1 'rN2: a hermetic FAIL alone drives the whole sweep to exit 1'

# --- rN3: a suite that MODIFIES a tracked file ---
rN3="$tmp/rN3_mutates"
mkrepo "$rN3"
mkdir -p "$rN3/scripts/tests"
printf 'original\n' > "$rN3/tracked.txt"
# shellcheck disable=SC2016  # literal in the generated fixture — see rN2
printf '#!/usr/bin/env bash\nprintf "clobbered\\n" > "$(dirname "$0")/../../tracked.txt"\nexit 0\n' \
  > "$rN3/scripts/tests/test_mutates.sh"
chmod +x "$rN3/scripts/tests/test_mutates.sh"
commit_all "$rN3" seed

run_engine "$rN3" --tests
assert_has 'FAIL hermetic' 'rN3: a suite that modifies a tracked file -> FAIL hermetic'
assert_has 'tracked.txt' 'rN3: the FAIL names the modified path'

# --- rN4: an IGNORED path is deliberately out of scope ---
# This mirrors the instrument the publish path's clean-tree precondition actually uses
# (`git status --porcelain`), which is what the check exists to keep satisfiable. Build
# noise a suite legitimately produces — `__pycache__/`, `.pytest_cache/` — lives there.
rN4="$tmp/rN4_ignored"
mkrepo "$rN4"
mkdir -p "$rN4/scripts/tests"
printf '/build/\n' > "$rN4/.gitignore"
# shellcheck disable=SC2016  # literal in the generated fixture — see rN2
printf '#!/usr/bin/env bash\nd="$(dirname "$0")/../../build"\nmkdir -p "$d"\nprintf "x\\n" > "$d/o"\nexit 0\n' \
  > "$rN4/scripts/tests/test_ignored.sh"
chmod +x "$rN4/scripts/tests/test_ignored.sh"
commit_all "$rN4" seed

run_engine "$rN4" --tests
assert_has 'PASS hermetic' 'rN4: a write to an ignored path -> PASS hermetic (deliberate scope)'

# --- rN5: a tree that was ALREADY dirty does not trip it ---
rN5="$tmp/rN5_predirty"
mkrepo "$rN5"
mkdir -p "$rN5/scripts/tests"
printf '#!/usr/bin/env bash\nexit 0\n' > "$rN5/scripts/tests/test_quiet.sh"
chmod +x "$rN5/scripts/tests/test_quiet.sh"
commit_all "$rN5" seed
printf 'left by the operator\n' > "$rN5/stray.txt"

run_engine "$rN5" --tests
assert_has 'PASS hermetic' 'rN5: a pre-existing untracked file was not left by the run -> PASS'

# --- rN6: a suite that DELETES a pre-existing untracked file is also a failure ---
# The difference must be read in both directions; a one-way check would call this clean.
rN6="$tmp/rN6_deletes"
mkrepo "$rN6"
mkdir -p "$rN6/scripts/tests"
# shellcheck disable=SC2016  # literal in the generated fixture — see rN2
printf '#!/usr/bin/env bash\nrm -f "$(dirname "$0")/../../stray.txt"\nexit 0\n' \
  > "$rN6/scripts/tests/test_deletes.sh"
chmod +x "$rN6/scripts/tests/test_deletes.sh"
commit_all "$rN6" seed
printf 'left by the operator\n' > "$rN6/stray.txt"

run_engine "$rN6" --tests
assert_has 'FAIL hermetic' 'rN6: a suite that deletes an untracked file -> FAIL hermetic'
assert_has 'stray.txt' 'rN6: the FAIL names the vanished path'

# --- rN7: an UNREADABLE tree must fail closed, never compare equal ---
# The failure mode this pins is symmetric emptiness: if a failed snapshot yielded "" at both
# ends, the two would compare equal and the check would report PASS having measured nothing.
# The suite destroys the repo out from under the after-snapshot to force exactly that.
rN7="$tmp/rN7_unreadable"
mkrepo "$rN7"
mkdir -p "$rN7/scripts/tests"
# shellcheck disable=SC2016  # literal in the generated fixture — see rN2
printf '#!/usr/bin/env bash\nrm -rf "$(dirname "$0")/../../.git"\nexit 0\n' \
  > "$rN7/scripts/tests/test_nukes.sh"
chmod +x "$rN7/scripts/tests/test_nukes.sh"
commit_all "$rN7" seed

run_engine "$rN7" --tests
assert_has 'FAIL hermetic' 'rN7: an unreadable working tree -> FAIL hermetic, never PASS'
assert_has 'AFTER' 'rN7: the FAIL says which snapshot could not be read'
assert_rc 1 'rN7: an unreadable tree drives the sweep to exit 1'

# --- rN8: a failed BEFORE snapshot must fail closed, even when AFTER reads clean ---
# Unit-level, because this cannot be provoked end-to-end: audit.sh has already validated the
# scope as a git repo by the time the snapshot is taken. It is nonetheless the MOST dangerous
# shape, and mutation testing is what exposed it — deleting the before-guard left every
# end-to-end assertion green. In a CLEAN repo a failed read yields "" at both ends, the two
# compare equal, and a check without the guard reports PASS having measured nothing at all.
rN8="$tmp/rN8_clean"
mkrepo "$rN8"
printf 'seed\n' > "$rN8/f.txt"
commit_all "$rN8" seed

rN8_out="$(bash -c 'source "$1"
pass_count=0; fail_count=0; skip_count=0
check_hermetic "$2" "" 1' _ "$engine" "$rN8" 2>&1)"
case "$rN8_out" in
  *'FAIL hermetic'*BEFORE*)
    pass_line 'rN8: a failed BEFORE snapshot fails closed, never compares equal' ;;
  *)
    fail_line 'rN8: a failed BEFORE snapshot fails closed, never compares equal'
    printf '  --- output ---\n%s\n  --------------\n' "$rN8_out" ;;
esac

# ============================================================================
# rO. hermetic-outside — what a suite run leaves BEYOND the scope
#
# Measured instance (2026-07-29): a hook's diagnostic log defaulted to `~/.claude/logs/` and
# suite rows reached that branch, so 12 synthetic records accumulated in the operator's REAL
# log across four runs. `AUDIT_HERMETIC_ROOT` points the watch at a fixture root here; the
# denominator assertion (rO5) is what stops that override from making the check vacuous.
# ============================================================================
mk_fake_root() { # dir -> a watched dir with an existing file, plus a churn dir
  mkdir -p "$1/logs" "$1/skills" "$1/projects"
  printf 'pre-existing\n' > "$1/logs/existing.log"
  printf 'x\n' > "$1/skills/s.md"
  printf 'p\n' > "$1/projects/session.txt"
}

mk_outside_repo() { # dir body -> a repo whose one suite runs `body`
  mkrepo "$1"
  mkdir -p "$1/scripts/tests"
  printf '#!/usr/bin/env bash\n%s\nexit 0\n' "$2" > "$1/scripts/tests/test_o.sh"
  chmod +x "$1/scripts/tests/test_o.sh"
  commit_all "$1" seed
}

run_outside() { # scope root [extra-args...] -> sets OUT/RC
  local scope="$1" root="$2"
  shift 2
  OUT="$(AUDIT_HERMETIC_ROOT="$root" "$engine" --scope "$scope" "$@" 2>&1)"
  RC=$?
}

rO_root="$tmp/rO_root"
mk_fake_root "$rO_root"

# --- rO1: a suite that writes nothing outside -> PASS; and no line at all without --tests ---
rO1="$tmp/rO1_quiet"
mk_outside_repo "$rO1" ':'
run_outside "$rO1" "$rO_root" --tests
assert_has 'PASS hermetic-outside' 'rO1: a suite that writes nothing outside -> PASS'
run_outside "$rO1" "$rO_root"
assert_not_has 'hermetic-outside' 'rO1: without --tests, no hermetic-outside line at all'

# --- rO2: CREATES a file under a watched directory ---
rO2="$tmp/rO2_creates"
mk_outside_repo "$rO2" "printf 'new\\n' > '$rO_root/logs/created.log'"
run_outside "$rO2" "$rO_root" --tests
assert_has 'FAIL hermetic-outside' 'rO2: a file created under a watched dir -> FAIL'
assert_has 'created.log' 'rO2: the FAIL names the created path'
assert_rc 1 'rO2: writing outside the scope drives the sweep to exit 1'
rm -f "$rO_root/logs/created.log"

# --- rO3: APPENDS to a file that already existed — the measured shape ---
# The path set is UNCHANGED here, so `appeared`/`vanished` are both empty and only the
# timestamp marker can see it. Asserting `appeared:` is ABSENT is what pins that: without the
# marker this row would report a clean PASS, which is exactly how the real instance survived.
rO3="$tmp/rO3_appends"
mk_outside_repo "$rO3" "printf 'more\\n' >> '$rO_root/logs/existing.log'"
run_outside "$rO3" "$rO_root" --tests
assert_has 'FAIL hermetic-outside' 'rO3: an APPEND to an existing watched file -> FAIL'
assert_has 'modified:' 'rO3: the append is reported under modified:'
assert_not_has 'appeared:' 'rO3: the path set did NOT change — only the marker sees this'

# --- rO4: a write under a CHURN directory is exempt ---
rO4="$tmp/rO4_churn"
mk_outside_repo "$rO4" "printf 'z\\n' >> '$rO_root/projects/session.txt'"
run_outside "$rO4" "$rO_root" --tests
assert_has 'PASS hermetic-outside' 'rO4: a write under a churn-exempt dir -> PASS'

# --- rO5: an EMPTY root must fail on the denominator, never pass ---
# `find ~/.claude -type f` returns ZERO because the root is a symlink — measured, and it reads
# exactly like "nothing changed". Zero watched files can never be a clean bill of health.
rO5_root="$tmp/rO5_empty"
mkdir -p "$rO5_root"
rO5="$tmp/rO5_repo"
mk_outside_repo "$rO5" ':'
run_outside "$rO5" "$rO5_root" --tests
assert_has 'FAIL hermetic-outside' 'rO5: zero watched files -> FAIL, never PASS'
assert_has 'measured nothing' 'rO5: the FAIL says the probe measured nothing'

# --- rO6: a root inside the scope is the inward check's job ---
rO6="$tmp/rO6_inside"
mk_outside_repo "$rO6" ':'
mkdir -p "$rO6/inner"
printf 'a\n' > "$rO6/inner/a.txt"
run_outside "$rO6" "$rO6/inner" --tests
assert_has 'SKIP hermetic-outside' 'rO6: a config root inside the scope -> SKIP'

# --- rO7: moving a protected path onto the churn list is itself a failure ---
# Unit-level, because it is a source edit rather than a runtime state. This is the edit a
# later session makes to silence a genuine finding; without this row it reads as a clean PASS
# over a quietly smaller watch set.
rO7_marker="$tmp/rO7.marker"
: > "$rO7_marker"
rO7_out="$(bash -c 'source "$1"
HERMETIC_CHURN="logs"
pass_count=0; fail_count=0; skip_count=0
check_hermetic_outside "/no/such/scope" "$2" "seed" 0 "$3"' _ "$engine" "$rO_root" "$rO7_marker" 2>&1)"
case "$rO7_out" in
  *'FAIL hermetic-outside'*'churn exemption list'*)
    pass_line 'rO7: a protected path moved onto the churn list -> FAIL' ;;
  *)
    fail_line 'rO7: a protected path moved onto the churn list -> FAIL'
    printf '  --- output ---\n%s\n  --------------\n' "$rO7_out" ;;
esac

# --- rO8: a SYMLINKED entry must still be watched ---
# This is the live shape, not a hypothetical: `~/.claude/skills` is a symlink into another
# repo. Without `-L`, `find` returns the link itself and no `-type f` match, so the entry
# contributes ZERO files and a write inside it reads as a clean PASS — the same zero-denominator
# failure that made `find ~/.claude -type f` return 0. The append below is invisible without it.
rO8_root="$tmp/rO8_root"
rO8_target="$tmp/rO8_target"
mkdir -p "$rO8_root/logs" "$rO8_target"
printf 'seed\n' > "$rO8_root/logs/keep.log"
printf 'linked\n' > "$rO8_target/linked.md"
ln -s "$rO8_target" "$rO8_root/skills"

rO8="$tmp/rO8_repo"
mk_outside_repo "$rO8" "printf 'more\\n' >> '$rO8_root/skills/linked.md'"
run_outside "$rO8" "$rO8_root" --tests
assert_has 'FAIL hermetic-outside' 'rO8: a write through a SYMLINKED watched entry -> FAIL'
assert_has 'linked.md' 'rO8: the FAIL names the path behind the symlink'

# --- rO9: an unset HOME must not take the whole sweep down ---
# Under `set -u` an unbound variable does not merely fail the function — it KILLS the shell
# (measured: rc 127, "HOME: unbound variable"). So an unguarded `$HOME` in the root default
# aborts the entire sweep mid-run rather than skipping one check. It aborts fail-closed, via
# `RESULT: INCOMPLETE`, but a missing env var is not a reason to stop auditing.
# shellcheck disable=SC2016  # `$1`/`$?` belong to the INNER shell, not this one
rO9_out="$(env -u HOME AUDIT_HERMETIC_ROOT= CLAUDE_CONFIG_DIR= \
  bash -c 'set -uo pipefail
source "$1"
hermetic_config_root
printf "survived rc=%d\n" "$?"' _ "$engine" 2>&1)"
case "$rO9_out" in
  *'survived rc=1'*) pass_line 'rO9: an unset HOME yields no root rather than killing the sweep' ;;
  *)
    fail_line 'rO9: an unset HOME yields no root rather than killing the sweep'
    printf '  --- output ---\n%s\n  --------------\n' "$rO9_out" ;;
esac

# ============================================================================
# rMA. The mutation-anchors gate's POPULATION
# ============================================================================
# This gate enumerates campaigns itself, to decide whether to SKIP. It must use the union of the
# checker's two predicates — tracked, plus untracked-and-unignored — because a tracked-only gate
# skips the whole check in the one case the checker's untracked guard exists for: the first
# campaign a repo ever gets, still unadded. The guard would then be unreachable at exactly the
# moment nothing has ever verified that campaign, and the SKIP says nothing about what it did not
# run. Measured before this suite existed: the gate and the checker disagreed about the
# population, so half the fix was inert.
mk_campaign_repo() { # dir -> a committed repo whose only campaign is written but NOT added
  mkrepo "$1"
  mkdir -p "$1/scripts/tests"
  printf 'alpha\n' > "$1/subject.sh"
  commit_all "$1" init
  printf 'from pathlib import Path\nREPO = Path(__file__).resolve().parent.parent.parent\n' \
    > "$1/scripts/tests/mutate_new.py"
  printf 'SUBJECT = REPO / "subject.sh"\nMUTATIONS = [Mutation("row", "no-such-anchor", "X")]\n' \
    >> "$1/scripts/tests/mutate_new.py"
}

rMA1="$tmp/rMA1_untracked_only"
mk_campaign_repo "$rMA1"
run_engine "$rMA1"
assert_not_has 'SKIP mutation-anchors' 'rMA1: an untracked-only campaign is not skipped away'
assert_has 'FAIL mutation-anchors' 'rMA1: the first campaign a repo gets is judged, not skipped'

# The escape hatch, at this layer: an IGNORED campaign is a declared exclusion, so the gate is
# back to its old behaviour and the check has nothing to judge. Without this row the fix above
# would be indistinguishable from one that simply grades the whole filesystem.
rMA2="$tmp/rMA2_ignored"
mk_campaign_repo "$rMA2"
printf 'scripts/tests/mutate_new.py\n' > "$rMA2/.gitignore"
run_engine "$rMA2"
assert_has 'SKIP mutation-anchors' 'rMA2: an IGNORED campaign is a declared exclusion -> SKIP'
assert_not_has 'FAIL mutation-anchors' 'rMA2: an ignored campaign does not false-block'

printf '\n%d passed, %d failed\n' "$pass" "$fail"
[[ "$fail" -eq 0 ]]

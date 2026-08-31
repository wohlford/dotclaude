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

# Every mktemp below this line — the suite's own, the engine's hermetic marker, and (since the
# tests check began preserving failing suites' full output) the engine's artifact directories —
# lands under $tmp and dies with the trap above. Without this the suite makes the ENGINE write
# into the operator's real temp root, roughly one directory per failing fixture and ~36 across a
# mutation campaign: this repo's own "a default output path makes every run a writer of real
# state" hazard, rebuilt inside its own test suite. It sits outside every fixture's scope
# directory, so the outside-the-scope assertion in 15g still means something.
export TMPDIR="$tmp/enginetmp"
mkdir -p "$TMPDIR"

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
  git -C "$1" config tag.gpgsign false
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
# 7b. R1/R2 (spec 2026-08-23-audit-ruff-offender-output) — check_ruff's FAIL
#     block: a ruff-format failure survives the shared 50-line cap despite 55
#     preceding clean files, and no success padding ("All checks passed!" /
#     "1 file already formatted") leaks into the FAIL block.
#     N >= 50 total .py files: at that size the check-loop's success output
#     alone fills the cap, so burial is independent of where the failing
#     file sorts (below 50 it would depend on sort position — a fixture that
#     passes for the wrong reason).
# ============================================================================
if command -v ruff >/dev/null 2>&1; then
  r7b="$tmp/r7b_ruff_format_buried"
  mkrepo "$r7b"
  i=1
  while [[ "$i" -le 55 ]]; do
    printf 'x = 1\n' > "$r7b/$(printf 'clean%03d.py' "$i")"
    i=$((i + 1))
  done
  printf 'x=1\n' > "$r7b/fmt_fail.py"
  commit_all "$r7b" seed
  run_engine "$r7b"
  assert_has 'FAIL ruff' 'r7b: 55 clean .py + 1 format-dirty -> FAIL ruff'
  assert_has 'unformatted: File would be reformatted' \
    'r7b (R1): the format failure survives the cap despite 55 preceding clean check results'
  assert_has 'fmt_fail.py' 'r7b (R1): the failing file is named in the FAIL block'
  assert_not_has 'All checks passed!' \
    'r7b (R2): ruff-check success padding does not leak into the FAIL block'
  assert_not_has '1 file already formatted' \
    'r7b (R2): ruff-format success padding does not leak into the FAIL block'
  # R5 — SUB-TOOL ATTRIBUTION, the one thing the per-file header uniquely decides. Added after a
  # mutation campaign found `drop-per-file-label` SURVIVING: ruff's own diagnostics carry the
  # filename (`--> fmt_fail.py:1:2`), so the naming assertions above pass with the header deleted.
  # Neither sub-tool's output names WHICH sub-tool produced it — measured — so this is the
  # assertion the header can actually fail. The original defect report asked for exactly this
  # ("label it: ruff check / ruff format"), and without it a reader cannot tell a lint finding from
  # a formatting one.
  assert_has 'ruff format --check fmt_fail.py:' \
    'r7b (R5): the FAIL block attributes the failure to the ruff format sub-tool by name'
else
  printf 'skip - ruff not installed in this test environment\n'
fi

# ============================================================================
# 7c. R3 — a ruff-check (lint) failure is still reported once the collection
#     is trimmed to real output only: the check loop's half of the fix does
#     not regress alongside the format loop's half. Same 55-clean-file scale
#     as 7b, mirrored onto the check loop.
# ============================================================================
if command -v ruff >/dev/null 2>&1; then
  r7c="$tmp/r7c_ruff_check_buried"
  mkrepo "$r7c"
  i=1
  while [[ "$i" -le 55 ]]; do
    printf 'x = 1\n' > "$r7c/$(printf 'clean%03d.py' "$i")"
    i=$((i + 1))
  done
  printf 'import os\n\n\ndef f() -> None:\n    pass\n' > "$r7c/zchk_fail.py"
  commit_all "$r7c" seed
  run_engine "$r7c"
  assert_has 'FAIL ruff' 'r7c: 55 clean .py + 1 lint-dirty -> FAIL ruff'
  assert_has 'F401' 'r7c (R3): the check (lint) failure is still reported'
  assert_has 'zchk_fail.py' 'r7c (R3): the failing file is named in the FAIL block'
  assert_not_has 'All checks passed!' \
    'r7c (R2 cross-check): ruff-check success padding absent from the check-loop FAIL block too'
  assert_has 'ruff check zchk_fail.py:' \
    'r7c (R5): the FAIL block attributes the failure to the ruff check sub-tool by name'
else
  printf 'skip - ruff not installed in this test environment\n'
fi

# ============================================================================
# 7d. R4 — every failing FILE is named even once several real failures
#     compete for the shared 50-line cap, atop 55 clean files that bury them
#     entirely under the unfixed code. N is DERIVED from a MEASURED per-file
#     line count (ruff check's own output on one real violation), never
#     hardcoded -- the ~13-line figure this spec was measured against is
#     ruff-version dependent (older ruff printed a one-line "Would
#     reformat:"). Failing files are named to sort AFTER the clean files
#     (zzbad* > clean*) so the unfixed code's padding precedes -- and
#     buries -- all of them, giving this row a genuine RED.
# ============================================================================
if command -v ruff >/dev/null 2>&1; then
  r7d="$tmp/r7d_ruff_naming"
  mkrepo "$r7d"
  i=1
  while [[ "$i" -le 55 ]]; do
    printf 'x = 1\n' > "$r7d/$(printf 'clean%03d.py' "$i")"
    i=$((i + 1))
  done

  r7d_probe="$tmp/r7d_probe"
  mkdir -p "$r7d_probe"
  printf 'import os\n\n\ndef f() -> None:\n    pass\n' > "$r7d_probe/probe.py"
  # MUST mirror check_ruff's own invocation, --output-format included. This probe is a hand-made
  # copy of it, so it drifts the moment that flag changes — and a drifted probe derives N from the
  # wrong block size, silently making this row too small to discriminate anything.
  r7d_probe_out="$(cd "$r7d_probe" && ruff check --output-format concise probe.py 2>&1)"
  r7d_probe_lines="$(printf '%s\n' "$r7d_probe_out" | wc -l | tr -d ' ')"
  r7d_block=$((r7d_probe_lines + 1))          # +1 for the "ruff check <f>:" header line
  # The DISCRIMINATING N, not merely a working one. At (49/block)+1 the flat-cap and
  # bounded-excerpt printers name the SAME number of files (measured: 4 named either way),
  # so the row stayed green under a mutant reverting to print_offenders — the exact defect
  # check_ruff's own comment cites. +2 crosses the threshold: the last file's header lands
  # past line 50, so only the bounded-excerpt printer names it.
  r7d_n=$(( (50 / r7d_block) + 2 ))

  i=1
  while [[ "$i" -le "$r7d_n" ]]; do
    printf 'import os\n\n\ndef f() -> None:\n    pass\n' \
      > "$r7d/$(printf 'zzbad%03d.py' "$i")"
    i=$((i + 1))
  done
  commit_all "$r7d" seed
  run_engine "$r7d"
  assert_has 'FAIL ruff' "r7d: measured $r7d_probe_lines lines/failure -> $r7d_n lint-dirty files -> FAIL ruff"
  i=1
  while [[ "$i" -le "$r7d_n" ]]; do
    assert_has "$(printf 'ruff check zzbad%03d.py:' "$i")" \
      "r7d (R4): file $i of $r7d_n is named BY ITS SUB-TOOL HEADER despite the shared cap"
    i=$((i + 1))
  done
else
  printf 'skip - ruff not installed in this test environment\n'
fi

# ============================================================================
# 7f. The EXCERPT BOUND itself. Added after a review found that a mutant setting
#     RUFF_EXCERPT_MAX to anything in 1..11 survived every other row: r7b/r7c
#     assert line 1 of their sub-tool's output, which any nonzero bound keeps.
#     What the bound actually decides is whether a file's FULL finding list
#     survives, so that is what this pins -- three distinct codes in one file,
#     all of which must appear. Under --output-format concise a finding is one
#     line, so this also fails if that flag is ever dropped from check_ruff.
# ============================================================================
if command -v ruff >/dev/null 2>&1; then
  r7f="$tmp/r7f_excerpt_bound"
  mkrepo "$r7f"
  printf 'import sys\nimport os\nx=1\n' > "$r7f/multi.py"
  commit_all "$r7f" seed
  run_engine "$r7f"
  assert_has 'FAIL ruff' 'r7f: a file with several findings -> FAIL ruff'
  assert_has 'I001' 'r7f: the FIRST finding is reported'
  # These two are the DISCRIMINATORS. An earlier version asserted 'I001' and 'F401', which sit on
  # output lines 1 and 2 -- so a mutant shrinking the bound to 2 kept both and the row passed. A
  # bound is only pinned by an assertion on something that falls PAST it.
  #
  # WHICH BLOCK BINDS, measured (ruff 0.16.3), because it is not the obvious one: the `ruff check`
  # concise block is 5 lines and its third finding (multi.py:2:8) discriminates bounds 1-2 only.
  # The `ruff format --check` block is 10 lines and is what decides the rest -- the truncation
  # notice fires for every bound below 10. So the effective margin is 12 - 10 = TWO lines, and a
  # ruff release whose format diagnostic grows by three would turn this row spuriously RED. If that
  # happens the row is stale, not the code: re-measure both blocks and re-size, do not just raise
  # the bound.
  assert_has 'multi.py:2:8' \
    'r7f: the THIRD finding — past a shrunken bound — is present'
  assert_not_has 'more line(s)' \
    'r7f: a 5-line block against a 12-line bound is NOT truncated'
else
  printf 'skip - ruff not installed in this test environment\n'
fi

# ============================================================================
# 7h. The AGGREGATE budget (RUFF_EXCERPT_BUDGET). Per-file bounding alone let the
#     detail reach 1,737 lines / 49 KB on an 86-file repo — 33x the old flat
#     cap — which matters because SKILL.md has an agent read this stdout and key
#     on the LAST line, so an upstream cap would eat the RESULT: line itself.
#     Pinned rather than trusted: bounds that go untested are exactly what this
#     branch has already been caught on twice. ~15 lines/file across both
#     sub-tools (measured 19: a 1+5 check block and a 1+12 format block), so 30 crosses 400. Asserts BOTH halves of the stated
#     invariant: the degradation is announced, and every file is still NAMED.
# ============================================================================
if command -v ruff >/dev/null 2>&1; then
  r7h="$tmp/r7h_aggregate_budget"
  mkrepo "$r7h"
  i=1
  while [[ "$i" -le 30 ]]; do
    printf 'import os\nimport sys\nx=1\ny  =  2\n' > "$r7h/$(printf 'big%03d.py' "$i")"
    i=$((i + 1))
  done
  commit_all "$r7h" seed
  run_engine "$r7h"
  assert_has 'FAIL ruff' 'r7h: 30 failing files -> FAIL ruff'
  assert_has 'reported header-only' \
    'r7h: crossing the budget is ANNOUNCED, never a silent truncation'
  # THE discriminator. Without it this row is vacuous: a mutant that parses ruff_block's
  # header_only parameter and then ignores it (header_only=0) erases the budget's entire effect
  # -- detail 429 -> 572 lines -- while every other assertion here stays GREEN, because the notice
  # is driven by the `omitted` counter and the file name by the unconditional header. That is the
  # measured "a test that supplies the option's own default cannot tell whether it is read" shape.
  assert_has 'excerpt omitted (aggregate budget)' \
    'r7h: a degraded block SAYS its excerpt was dropped — the only string the budget decides'
  # Must be the FORMAT loop. At N=30 the check loop totals ~180 lines and never crosses the budget,
  # so a `ruff check ...` assertion here would sit on the normal path and pin nothing new.
  assert_has 'ruff format --check big030.py:' \
    'r7h: the LAST file is still NAMED in the loop that DID degrade'
  # The COUNT itself, derived from OUT rather than hand-copied. Round 4 found this notice counting
  # blocks while saying "file(s)" (at 80 failing files it claimed 93); that was reworded to
  # "invocation(s)" and NOT pinned, so `omitted + 1` -> `+ 2` survived every row above. Deriving
  # the expected value means no constant to go stale and no coupling to a ruff version.
  r7h_degraded="$(printf '%s\n' "$OUT" | grep -c 'excerpt omitted (aggregate budget)' || true)"
  r7h_claimed="$(printf '%s\n' "$OUT" | sed -n 's/.*… \([0-9][0-9]*\) further failing invocation.*/\1/p' | head -1)"
  check_eq "$r7h_claimed" "$r7h_degraded" \
    'r7h: the notice COUNTS what it claims — N equals the degraded blocks actually emitted'
else
  printf 'skip - ruff not installed in this test environment\n'
fi

# ============================================================================
# 7g. "Every failing file is always NAMED" — the invariant check_ruff states as
#     load-bearing and SKILL.md promotes to the shared rule of both offender
#     exceptions. A review found it pinned by NOTHING. Two distinct mutants live
#     here and an earlier version of this comment conflated them: guarding the
#     label on a non-empty BODY drops the `(no output)` marker (caught by the
#     third row below), while guarding the HEADER itself drops the file's name
#     (caught by the second). Both are the fail-silent shape this check exists
#     to remove; neither is the other.
#     Reachable for real: ruff killed by a signal, or the `cd "$scope"` failure
#     the code comment cites (its 2>&1 binds to ruff, not to cd).
#     Needs no real ruff — the stub IS the tool, so this row runs everywhere.
# ============================================================================
r7g="$tmp/r7g_silent_failure"
mkrepo "$r7g"
printf 'x = 1\n' > "$r7g/quiet.py"
commit_all "$r7g" seed
r7g_stub="$tmp/r7g_stub"
mkdir -p "$r7g_stub"
printf '#!/bin/sh\nexit 1\n' > "$r7g_stub/ruff"
chmod +x "$r7g_stub/ruff"
r7g_old_path="$PATH"
PATH="$r7g_stub:$PATH"
run_engine "$r7g"
PATH="$r7g_old_path"
assert_has 'FAIL ruff' 'r7g: a sub-tool failing SILENTLY still fails the check'
assert_has 'ruff check quiet.py:' 'r7g: ...and the failing file is still NAMED'
assert_has '(no output)' 'r7g: ...with the empty body explicit, never an unexplained blank'

# ============================================================================
# 7e. Task 3 (spec 2026-08-23-audit-ruff-offender-output) — verdict-preservation
#     property: the FAIL/PASS verdict is unchanged across the full rc1 x rc2
#     truth table (all-clean -> PASS; check-only -> FAIL; format-only -> FAIL;
#     both -> FAIL). Asserts the CHECK-LEVEL 'FAIL ruff'/'PASS ruff' line, not
#     merely the overall exit code. The format-only cell's fixture ('x=1', no
#     spaces) is deliberately LINT-CLEAN -- confirmed by ruff itself ('x = 1'
#     spaced is too) -- so that cell proves something about the format half
#     specifically, rather than a check failure riding along un-asserted.
# ============================================================================
if command -v ruff >/dev/null 2>&1; then
  # all-clean: rc1=0, rc2=0 -> PASS
  r7e1="$tmp/r7e1_all_clean"
  mkrepo "$r7e1"
  printf 'x = 1\n' > "$r7e1/clean.py"
  commit_all "$r7e1" seed
  run_engine "$r7e1"
  assert_has 'PASS ruff' '7e1: rc1=0 rc2=0 -> PASS ruff'
  assert_not_has 'FAIL ruff' '7e1: rc1=0 rc2=0 -> no FAIL ruff'

  # check-only failure: rc1!=0, rc2=0 -> FAIL
  r7e2="$tmp/r7e2_check_only"
  mkrepo "$r7e2"
  printf 'import os\n\n\ndef f() -> None:\n    pass\n' > "$r7e2/bad.py"
  commit_all "$r7e2" seed
  run_engine "$r7e2"
  assert_has 'FAIL ruff' '7e2: rc1!=0 rc2=0 (check-only failure) -> FAIL ruff'

  # format-only failure: rc1=0, rc2!=0 -> FAIL. Fixture is lint-clean (ruff check passes).
  r7e3="$tmp/r7e3_format_only"
  mkrepo "$r7e3"
  printf 'x=1\n' > "$r7e3/bad.py"
  commit_all "$r7e3" seed
  run_engine "$r7e3"
  assert_has 'FAIL ruff' '7e3: rc1=0 rc2!=0 (format-only failure) -> FAIL ruff'

  # both failing: rc1!=0, rc2!=0 -> FAIL
  r7e4="$tmp/r7e4_both"
  mkrepo "$r7e4"
  printf 'import os\ndef f():pass\n' > "$r7e4/bad.py"
  commit_all "$r7e4" seed
  run_engine "$r7e4"
  assert_has 'FAIL ruff' '7e4: rc1!=0 rc2!=0 (both failing) -> FAIL ruff'
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

# artifact_file_has DIR NEEDLE1 NEEDLE2 -> 0 if ONE file under DIR holds BOTH needles.
# Deliberately per-file rather than `grep -r`: a recursive match is satisfied when two
# different files each hold one needle, which is exactly the half-preserved case 15g exists
# to reject.
artifact_file_has() {
  local d="$1" n1="$2" n3="$3" f
  for f in "$d"/*; do
    [[ -f "$f" ]] || continue
    if grep -q -- "$n1" "$f" 2>/dev/null && grep -q -- "$n3" "$f" 2>/dev/null; then
      return 0
    fi
  done
  return 1
}

# ============================================================================
# 15d-15n. --tests: the failing row must survive the offender cap
#
# Measured twice (2026-08-01 and 2026-08-04, the second at the last gate before an
# irreversible publish): a suite printing 66 PASS rows and one FAIL row had the FAIL row
# cut by print_offenders' 50-line cap, which keeps the HEAD. For a list of offending FILES
# the head is representative — the rest are the same defect. For another tool's stdout the
# interesting lines are the FAILURES, and suites print passes as they go, so the cap
# reliably kept the useless half and the diagnosis was unrecoverable both times.
#
# 15e exists to reject the tempting narrow fix: keeping the TAIL instead of the head fails
# the mirrored shape, and these suites do continue after a failure rather than exiting on
# the first one. 15h pins the case where nothing matches at all, since a filter that
# distils to nothing reports success loudest of all.
# ============================================================================

# --- 15d: the failure sits PAST the cap (the exact measured shape) ---
r15d="$tmp/r15d_late"
mkrepo "$r15d"
mkdir -p "$r15d/scripts/tests"
cat > "$r15d/scripts/tests/test_late.sh" <<'EOF'
#!/usr/bin/env bash
for ((i = 1; i <= 60; i++)); do printf 'PASS  row %d\n' "$i"; done
printf 'FAIL  the-row-that-matters\n'
exit 1
EOF
chmod +x "$r15d/scripts/tests/test_late.sh"
commit_all "$r15d" late
run_engine "$r15d" --tests
assert_has 'the-row-that-matters' '15d: a FAIL row past line 50 survives the offender cap'

# --- 15e: the failure sits EARLY, with the passes after it (a tail-only fix fails here) ---
r15e="$tmp/r15e_early"
mkrepo "$r15e"
mkdir -p "$r15e/scripts/tests"
cat > "$r15e/scripts/tests/test_early.sh" <<'EOF'
#!/usr/bin/env bash
printf 'PASS  row 0\n'
printf 'FAIL  the-early-row\n'
for ((i = 1; i <= 60; i++)); do printf 'PASS  row %d\n' "$i"; done
exit 1
EOF
chmod +x "$r15e/scripts/tests/test_early.sh"
commit_all "$r15e" early
run_engine "$r15e" --tests
assert_has 'the-early-row' '15e: a FAIL row before 60 passes survives (not merely a tail keep)'

# --- 15f: every failing suite is represented, not just the first ---
r15f="$tmp/r15f_two"
mkrepo "$r15f"
mkdir -p "$r15f/scripts/tests"
cat > "$r15f/scripts/tests/test_aaa.sh" <<'EOF'
#!/usr/bin/env bash
for ((i = 1; i <= 60; i++)); do printf 'PASS  row %d\n' "$i"; done
printf 'FAIL  first-suite-row\n'
exit 1
EOF
cat > "$r15f/scripts/tests/test_zzz.sh" <<'EOF'
#!/usr/bin/env bash
for ((i = 1; i <= 60; i++)); do printf 'PASS  row %d\n' "$i"; done
printf 'FAIL  second-suite-row\n'
exit 1
EOF
chmod +x "$r15f/scripts/tests/test_aaa.sh" "$r15f/scripts/tests/test_zzz.sh"
commit_all "$r15f" two
run_engine "$r15f" --tests
assert_has 'first-suite-row' '15f: with two failing suites, the first suite row survives'
assert_has 'second-suite-row' '15f: with two failing suites, the second suite row survives'

# --- 15g: the full output is preserved in an artifact, OUTSIDE the scope ---
artifact="$(printf '%s\n' "$OUT" | sed -n 's/.*full output: \(.*\)$/\1/p' | head -1)"
if [[ -n "$artifact" && -d "$artifact" ]]; then
  pass_line '15g: a full-output artifact directory is named in the report'
  case "$artifact" in
    "$r15f"/*) fail_line '15g: the artifact must NOT be written inside the scope' ;;
    *) pass_line '15g: the artifact lies outside the scope (clean-tree precondition)' ;;
  esac
  # PER-SUITE, not a `grep -r` over the directory: one recursive grep is satisfied by EITHER
  # suite's log, so an implementation that preserves only the first failing suite would pass.
  # Each suite's own log must hold its own distinctive row AND a row the inline excerpt filters
  # out — that pairing is what proves the file is complete rather than merely present.
  if artifact_file_has "$artifact" 'first-suite-row' 'PASS  row 42'; then
    pass_line '15g: suite one has its OWN complete log (distinctive row + a filtered-out row)'
  else
    fail_line '15g: suite one has its OWN complete log (distinctive row + a filtered-out row)'
  fi
  if artifact_file_has "$artifact" 'second-suite-row' 'PASS  row 42'; then
    pass_line '15g: suite two has its OWN complete log (distinctive row + a filtered-out row)'
  else
    fail_line '15g: suite two has its OWN complete log (distinctive row + a filtered-out row)'
  fi
else
  fail_line '15g: a full-output artifact directory is named in the report'
  fail_line '15g: the artifact lies outside the scope (clean-tree precondition)'
  fail_line '15g: suite one has its OWN complete log (distinctive row + a filtered-out row)'
  fail_line '15g: suite two has its OWN complete log (distinctive row + a filtered-out row)'
fi

# --- 15h: a failing suite with NO failure-shaped line still reports something readable ---
r15h="$tmp/r15h_silent"
mkrepo "$r15h"
mkdir -p "$r15h/scripts/tests"
cat > "$r15h/scripts/tests/test_silent.sh" <<'EOF'
#!/usr/bin/env bash
for ((i = 1; i <= 60; i++)); do printf 'PASS  row %d\n' "$i"; done
printf 'the-last-thing-it-said\n'
exit 3
EOF
chmod +x "$r15h/scripts/tests/test_silent.sh"
commit_all "$r15h" silent
run_engine "$r15h" --tests
assert_has 'FAIL tests' '15h: a suite failing with no failure-shaped line -> FAIL tests'
assert_has 'the-last-thing-it-said' '15h: with nothing matched, the tail is shown rather than nothing'

# --- 15i: PRESERVE — the verdict is unchanged across all three outcomes ---
# The reporting change must not move the gate. FAIL iff some suite exited non-zero, SKIP iff
# none ran, PASS otherwise. 15b/15c already cover PASS and FAIL; this adds SKIP, and pins that
# a PASSING run says nothing about an artifact.
r15i="$tmp/r15i_skip"
mkrepo "$r15i"
printf 'placeholder\n' > "$r15i/README.md"
commit_all "$r15i" noSuites
run_engine "$r15i" --tests
assert_has 'SKIP tests' '15i: a repo with no suites at all -> SKIP tests'
assert_not_has 'full output:' '15i: a run with no failing suite names no artifact'

run_engine "$r15" --tests   # r15's suite currently exits 1 (set by 15c)
assert_has 'FAIL tests' '15i: the failing-suite verdict is still FAIL'

# --- 15j: PRESERVE — a passing run creates NO artifact ---
# A fresh, EMPTY, per-row TMPDIR asserted empty afterward. Deliberately not a count of
# `audit-tests-*` under the shared temp root: that passes 0->0 if the implementation names its
# directory anything else (nothing else pins the prefix), and it races every other writer —
# equal counts are not equal sets.
r15j="$tmp/r15j_clean"
mkrepo "$r15j"
mkdir -p "$r15j/scripts/tests"
printf '#!/usr/bin/env bash\nprintf "PASS  fine\\n"\nexit 0\n' > "$r15j/scripts/tests/test_ok.sh"
chmod +x "$r15j/scripts/tests/test_ok.sh"
commit_all "$r15j" clean
j_tmp="$tmp/r15j_tmpdir"
rm -rf "$j_tmp"; mkdir -p "$j_tmp"
OUT="$(TMPDIR="$j_tmp" "$engine" --scope "$r15j" --tests 2>&1)"
RC=$?
assert_has 'PASS tests' '15j: the passing-suite verdict is still PASS'
check_eq "$(find "$j_tmp" -mindepth 1 | wc -l | tr -d ' ')" '0' \
  '15j: a run with no failing suite writes nothing to TMPDIR'

# --- 15k: the artifact directory cannot be created ---
# Gated on the engine's bash being >= 5.1. Below that a here-string materialises a temp file, so
# an unwritable TMPDIR breaks `done <<< "$sh_list"` (audit.sh:601), `ran` stays false, and the
# engine emits SKIP tests — the row would fail for a reason it does not name. Spiked on bash
# 5.3.15: behaves as intended, with hermetic-outside legitimately degrading to SKIP.
# Nothing here asserts on hermetic/hermetic-outside for exactly that reason.
k_ver="$(env bash --version | sed -n '1s/.*version \([0-9][0-9]*\)\.\([0-9][0-9]*\).*/\1 \2/p')"
k_major="${k_ver%% *}"
k_minor="${k_ver##* }"
k_major="${k_major:-0}"
k_minor="${k_minor:-0}"
if [[ "$k_major" -gt 5 || ( "$k_major" -eq 5 && "$k_minor" -ge 1 ) ]]; then
  r15k="$tmp/r15k_nodir"
  mkrepo "$r15k"
  mkdir -p "$r15k/scripts/tests"
  cat > "$r15k/scripts/tests/test_k.sh" <<'EOF'
#!/usr/bin/env bash
printf 'FAIL  k-row-must-survive\n'
exit 1
EOF
  chmod +x "$r15k/scripts/tests/test_k.sh"
  commit_all "$r15k" nodir
  k_ro="$tmp/r15k_readonly"
  rm -rf "$k_ro"; mkdir -p "$k_ro"; chmod 500 "$k_ro"
  OUT="$(TMPDIR="$k_ro" "$engine" --scope "$r15k" --tests 2>&1)"
  RC=$?
  chmod 700 "$k_ro"
  # PRESERVE half — true before and after the fix.
  assert_has 'FAIL tests' '15k: an uncreatable artifact dir does not change the verdict'
  assert_has 'k-row-must-survive' '15k: the excerpt still prints without an artifact'
  # RED half — the loud degradation only exists after the fix.
  assert_has 'unavailable' '15k: the report says the full output is unavailable, rather than going quiet'
else
  pass_line "15k: SKIPPED — engine bash ${k_major}.${k_minor} < 5.1 makes this fixture unreliable"
fi

# --- 15l: THREE failing suites — the aggregate bound, not the per-suite one ---
# This is the row that catches the defect the first design walked back into: a per-suite budget
# of 21 lines fits two suites under a 50-line cap (which is why 15f passed and HID it) but cuts
# the third, and never even NAMES a fourth. A bound that is correct per item and a bound that is
# correct in aggregate are different claims, and a two-item fixture cannot tell them apart.
r15l="$tmp/r15l_three"
mkrepo "$r15l"
mkdir -p "$r15l/scripts/tests"
for n in one two three; do
  cat > "$r15l/scripts/tests/test_${n}.sh" <<EOF
#!/usr/bin/env bash
for ((i = 1; i <= 60; i++)); do printf 'PASS  row %d\n' "\$i"; done
printf 'FAIL  marker-${n}\n'
exit 1
EOF
  chmod +x "$r15l/scripts/tests/test_${n}.sh"
done
commit_all "$r15l" three
run_engine "$r15l" --tests
assert_has 'marker-one'   '15l: with three failing suites, suite ONE row survives'
assert_has 'marker-two'   '15l: with three failing suites, suite TWO row survives'
assert_has 'marker-three' '15l: with three failing suites, suite THREE row survives'
assert_has 'test_three.sh' '15l: the third suite is NAMED (headers are the only place names appear)'

# The path line must PRECEDE the per-suite detail. This check owns a real property even though
# the tests block now has no cap of its own: the remaining truncation risk is a DOWNSTREAM
# reader's (the agent harness caps tool output; the publish driver reads per-brick output back
# through a cap), and a path line printed last is the first thing such a cap discards — which
# would put the complete output back out of reach, the whole defect this change exists to fix.
l_path_ln="$(printf '%s\n' "$OUT" | grep -n 'full output:' | head -1 | cut -d: -f1)"
l_first_hdr="$(printf '%s\n' "$OUT" | grep -n 'test_one.sh exited' | head -1 | cut -d: -f1)"
if [[ -n "$l_path_ln" && -n "$l_first_hdr" && "$l_path_ln" -lt "$l_first_hdr" ]]; then
  pass_line '15l: the artifact path precedes the detail (so a downstream cap keeps it)'
else
  fail_line '15l: the artifact path precedes the detail (so a downstream cap keeps it)'
fi

# --- 15m: a failure shape that is not `FAIL` ---
# Every other fixture's failure row begins `FAIL `, so narrowing the pattern to `^FAIL` alone
# would be undetectable. Placed EARLY with passes after it, so a tail keep cannot rescue it.
# This is also the only coverage anywhere for the pytest-shaped alternates of the matcher.
r15m="$tmp/r15m_trace"
mkrepo "$r15m"
mkdir -p "$r15m/scripts/tests"
cat > "$r15m/scripts/tests/test_trace.sh" <<'EOF'
#!/usr/bin/env bash
printf 'Traceback (most recent call last):\n'
printf '  File "x.py", line 1, in <module>\n'
for ((i = 1; i <= 60; i++)); do printf 'PASS  row %d\n' "$i"; done
exit 1
EOF
chmod +x "$r15m/scripts/tests/test_trace.sh"
commit_all "$r15m" trace
run_engine "$r15m" --tests
assert_has 'Traceback' '15m: a non-FAIL failure shape is still surfaced inline'

# --- 15n: a failing suite that produced NO output at all ---
# 15c's own fixture is literally `exit 1` with no stdout: no matches, and the tail of nothing is
# nothing, so the header would otherwise print bare.
r15n="$tmp/r15n_silent"
mkrepo "$r15n"
mkdir -p "$r15n/scripts/tests"
printf '#!/usr/bin/env bash\nexit 1\n' > "$r15n/scripts/tests/test_mute.sh"
chmod +x "$r15n/scripts/tests/test_mute.sh"
commit_all "$r15n" mute
run_engine "$r15n" --tests
assert_has 'FAIL tests' '15n: a silent failing suite still -> FAIL tests'
assert_has 'no output' '15n: a silent failing suite reports that, rather than a bare header'

# --- 15o: two suite names that SANITISE to the same filename ---
# `tr -c 'A-Za-z0-9._-' '_'` maps `test_coll a.sh` and `test_coll_a.sh` onto one log name, so a
# naive write silently clobbers the first with the second — losing exactly the complete output
# this change exists to preserve, while the report still claims the directory holds every failing
# suite's text. Near-theoretical (names come from a `git ls-files` glob) but cheap to close, and
# a silent loss is the one failure mode this whole change is against.
r15o="$tmp/r15o_collide"
mkrepo "$r15o"
mkdir -p "$r15o/scripts/tests"
for pair in 'coll a:collide-spaced' 'coll_a:collide-underscore'; do
  o_file="${pair%%:*}"; o_row="${pair##*:}"
  cat > "$r15o/scripts/tests/test_${o_file}.sh" <<EOF
#!/usr/bin/env bash
for ((i = 1; i <= 60; i++)); do printf 'PASS  row %d\n' "\$i"; done
printf 'FAIL  ${o_row}\n'
exit 1
EOF
  chmod +x "$r15o/scripts/tests/test_${o_file}.sh"
done
commit_all "$r15o" collide
run_engine "$r15o" --tests
assert_has 'collide-spaced'     '15o: the spaced-name suite row survives inline'
assert_has 'collide-underscore' '15o: the underscored-name suite row survives inline'
o_art="$(printf '%s\n' "$OUT" | sed -n 's/.*full output: \(.*\)$/\1/p' | head -1)"
if [[ -n "$o_art" && -d "$o_art" ]]; then
  if artifact_file_has "$o_art" 'collide-spaced' 'PASS  row 42'; then
    pass_line '15o: the spaced-name suite keeps its OWN complete log despite the collision'
  else
    fail_line '15o: the spaced-name suite keeps its OWN complete log despite the collision'
  fi
  if artifact_file_has "$o_art" 'collide-underscore' 'PASS  row 42'; then
    pass_line '15o: the underscored-name suite keeps its OWN complete log despite the collision'
  else
    fail_line '15o: the underscored-name suite keeps its OWN complete log despite the collision'
  fi
else
  fail_line '15o: the spaced-name suite keeps its OWN complete log despite the collision'
  fail_line '15o: the underscored-name suite keeps its OWN complete log despite the collision'
fi

# --- 15p/15q shared: a python3 shim that defeats `-m pytest` while forwarding every other
# invocation (tomllib checks, etc.) to the real interpreter. NOT a symlink farm hiding python3
# wholesale — that would also SKIP md-links/sync-docs/toml/mutation-anchors for reasons
# unrelated to `tests`, and it varies the wrong axis: an implementation keying the new FAIL on
# `! command -v python3` would pass such a farm while the realistic host (python3 present,
# pytest absent) keeps laundering silently into PASS. See
# specs/2026-08-06-audit-never-ran-verdicts.md D1a. Path pinned with `pwd -P`: $tmp is not
# pinned by this suite (unlike its siblings) and `/tmp` -> `/private/tmp` is symlinked here.
p15_tmp="$(cd "$tmp" && pwd -P)"
p15_bin="$p15_tmp/no_pytest_bin"
mkdir -p "$p15_bin"
p15_real_py3="$(command -v python3)"
cat > "$p15_bin/python3" <<EOF
#!/bin/sh
if [ "\$1" = "-m" ] && [ "\$2" = "pytest" ]; then
  echo "No module named pytest" >&2
  exit 1
fi
exec "$p15_real_py3" "\$@"
EOF
chmod +x "$p15_bin/python3"

# --- 15p: Defect A, PASS-laundering — a passing shell suite must not cover for a Python
# family that never ran at all (pytest missing while test_*.py files exist) ---
r15p="$p15_tmp/r15p_pass_laundered"
mkrepo "$r15p"
mkdir -p "$r15p/scripts/tests"
printf '#!/usr/bin/env bash\nexit 0\n' > "$r15p/scripts/tests/test_ok.sh"
chmod +x "$r15p/scripts/tests/test_ok.sh"
printf 'def test_thing():\n    assert True\n' > "$r15p/test_thing.py"
commit_all "$r15p" seed
OUT="$(PATH="$p15_bin:$PATH" "$engine" --scope "$r15p" --tests 2>&1)"
RC=$?
assert_not_has 'PASS tests' \
  '15p: a passing shell suite must not launder pytest silence into PASS tests'
assert_has 'FAIL tests' '15p: pytest missing with test_*.py present -> FAIL tests'
assert_has 'unprovable:' '15p: the FAIL names the verdict as unprovable, not a real result'
assert_has '1 test_*.py file(s) present but the runner could not start' \
  '15p: the reason names the missing runner and the file count'
assert_has 'No module named pytest' \
  '15p: the reason quotes the runner OWN words — the predicate is "-m pytest failed", which is
       equally satisfied by python3 being absent, so a fixed string would misname the remedy'
assert_rc 1 '15p: the unprovable verdict drives the sweep to exit 1'
assert_has 'RESULT: FAIL' '15p: the unprovable verdict is counted in the RESULT line, not just printed'

# --- 15q: Defect A, false SKIP reason — with no shell suites at all, the pre-fix engine's
# shared `ran` flag never becomes true, so it reports SKIP with a reason that is factually
# false (test_*.py files DO exist; only the runner is missing) ---
r15q="$p15_tmp/r15q_false_skip"
mkrepo "$r15q"
printf 'def test_thing():\n    assert True\n' > "$r15q/test_thing.py"
commit_all "$r15q" seed
OUT="$(PATH="$p15_bin:$PATH" "$engine" --scope "$r15q" --tests 2>&1)"
RC=$?
assert_not_has 'SKIP tests' \
  '15q: pytest missing with test_*.py present must not read as SKIP'
assert_not_has 'no scripts/tests/test_*.sh or test_*.py found' \
  '15q: the SKIP reason claiming no test files exist must never appear once test_*.py files exist'
assert_has 'FAIL tests' '15q: pytest missing with test_*.py present (no shell suites) -> FAIL tests'
assert_has 'unprovable:' '15q: the FAIL names the verdict as unprovable'
assert_rc 1 '15q: the unprovable verdict drives the sweep to exit 1'
assert_has 'RESULT: FAIL' '15q: the unprovable verdict is counted in the RESULT line'

# --- 15r: the applicability pathspec must not FALSE-BLOCK on a file that merely contains the
# substring. Git pathspecs are wildmatch WITHOUT WM_PATHNAME, so `*` crosses `/` and `test_`
# matches anywhere in the path: `src/latest_run.py` matches `*test_*.py` (la-test_-run). On the
# pre-fix engine that over-match was harmless — it only decided whether pytest was INVOKED, and
# a passing shell suite covered the silence. Turning applicability into a FAIL converts it into
# a false block on any adopting repo, against a gate measured at 0/10 false blocks. The shell
# suite below is what proves the row: post-fix the Python family is simply inapplicable, so the
# check must reach `PASS tests` on the shell family alone rather than reporting unprovable.
r15r="$p15_tmp/r15r_substring_only"
mkrepo "$r15r"
mkdir -p "$r15r/scripts/tests" "$r15r/src"
printf '#!/usr/bin/env bash\nexit 0\n' > "$r15r/scripts/tests/test_ok.sh"
chmod +x "$r15r/scripts/tests/test_ok.sh"
printf 'def latest():\n    return 1\n' > "$r15r/src/latest_run.py"
commit_all "$r15r" seed
OUT="$(PATH="$p15_bin:$PATH" "$engine" --scope "$r15r" --tests 2>&1)"
RC=$?
assert_not_has 'FAIL tests' \
  '15r: a file merely CONTAINING "test_" in its path is not an applicable pytest suite'
assert_not_has 'unprovable:' \
  '15r: no unprovable verdict — nothing about this repo needed pytest'
assert_has 'PASS tests' '15r: the shell family still runs and reports on its own'

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

# --- rO10/rO11: an ABSENT root is measured, not skipped ------------------------------------
# The SKIP these replace did not merely under-report: it WAIVED a real catch, because a suite
# that creates the config root from nothing is itself an outside write. rO11 is the catch;
# rO10 is the half that keeps it from being a false block on any host without a config root.
rO_abs="$tmp/rO10_absent"
mk_outside_repo "$rO_abs" ':'
run_outside "$rO_abs" "$tmp/never-created" --tests
assert_has 'PASS hermetic-outside' \
  'rO10: an absent root with no write is PASS — vacuously true and verified, never SKIP'
assert_not_has 'SKIP hermetic-outside' 'rO10: an absent root is no longer skipped'
assert_not_has 'watched 0 files' \
  'rO10: the zero-files guard must not fire on a legitimately absent root (that is a false block)'

rO_mk="$tmp/rO11_creator"
rO_mk_root="$tmp/made-by-suite"
mk_outside_repo "$rO_mk" "mkdir -p '$rO_mk_root' && : > '$rO_mk_root/x'"
run_outside "$rO_mk" "$rO_mk_root" --tests
assert_has 'FAIL hermetic-outside' 'rO11: a suite that CREATES the config root is an outside write'
assert_has 'created the config root' \
  'rO11: the reason names the creation — a bare FAIL would also be satisfied by the zero-files guard'
assert_rc 1 'rO11: creating the config root drives the sweep to exit 1'

# --- rO12: the enumerator aggregates per-root failures, it does not report the LAST one -----
# Measured pre-fix: roots [unreadable, ok] returned 0 while [ok, unreadable] returned 1 — so
# whether the guard downstream fired depended on nothing but the order `ls` happened to emit.
# The third pair is what keeps this row honest: an unconditionally strict enumerator would
# satisfy the first two and is not the fix.
rO12_lock="$tmp/agg_locked"; rO12_ok="$tmp/agg_ok"
mkdir -p "$rO12_lock" "$rO12_ok"; : > "$rO12_lock/f"; : > "$rO12_ok/f"
chmod 000 "$rO12_lock"
# shellcheck disable=SC2016  # `$1`/`$2` belong to the INNER shell, not this one
rO12_out="$(bash -c 'set -uo pipefail
source "$1"
hermetic_outside_files "$(printf "%s\n%s\n" "$2" "$3")" >/dev/null; printf "bad_first=%d\n" "$?"
hermetic_outside_files "$(printf "%s\n%s\n" "$3" "$2")" >/dev/null; printf "bad_last=%d\n" "$?"
hermetic_outside_files "$(printf "%s\n%s\n" "$3" "$3")" >/dev/null; printf "both_ok=%d\n" "$?"' \
  _ "$engine" "$rO12_lock" "$rO12_ok" 2>&1)"
chmod 755 "$rO12_lock" 2>/dev/null || true
OUT="$rO12_out"
assert_has 'bad_first=1' 'rO12: an unwalkable root that is not LAST is still reported'
assert_has 'bad_last=1'  'rO12: an unwalkable root that is last is reported'
assert_has 'both_ok=0'   'rO12: two good roots still succeed — the aggregate is not unconditional'

# --- rO13: a BEFORE snapshot that could not enumerate is unprovable, and says so ------------
# This is the guard a previously-filed defect declared dead. It is not dead: `set -uo pipefail`
# at audit.sh's top makes the capture the enumerator's status, not `sort`'s. This row exists so
# that removing pipefail — which would silently kill the guard — fails loudly instead.
rO13_root="$tmp/before_fail_root"
mkdir -p "$rO13_root/readable" "$rO13_root/locked"
: > "$rO13_root/readable/f"; : > "$rO13_root/locked/f"
chmod 000 "$rO13_root/locked"
run_outside "$rO_abs" "$rO13_root" --tests
chmod 755 "$rO13_root/locked" 2>/dev/null || true
assert_has 'FAIL hermetic-outside' 'rO13: an unenumerable watch root fails rather than passing'
assert_has 'unprovable:' 'rO13: the verdict is named unprovable, not a measured finding'
assert_has 'BEFORE the suite ran' \
  'rO13: the reason names the BEFORE snapshot — the guard a filed defect wrongly called dead'

# --- rO14: a root that EXISTS but cannot be RESOLVED is unprovable, never a vacuous PASS -----
# rO10's absent-root PASS is only honest when the root is genuinely absent. `hermetic_config_root`
# returns nonzero on three conditions that collapse to the same empty string — no such path, a
# path that is not a directory, and a directory that cannot be traversed — so without this row
# the widening hands a POSITIVE verdict to exactly the "probe measured nothing" case this whole
# check exists to reject. Worse than the SKIP it replaced: a SKIP is documented as a coverage gap
# to relay, a PASS is not. Both halves below reached `PASS hermetic-outside` when first measured.
rO14_file="$tmp/rO14_root_is_a_file"
: > "$rO14_file"
run_outside "$rO_abs" "$rO14_file" --tests
assert_has 'FAIL hermetic-outside' 'rO14: a regular file as the config root is not a PASS'
assert_has 'unprovable:' 'rO14: it is named unprovable — the instrument failed, nothing was measured'
assert_not_has 'PASS hermetic-outside' \
  'rO14: the vacuous-absence PASS must not fire on a root that is present but unresolvable'

rO14_lock="$tmp/rO14_untraversable"
mkdir -p "$rO14_lock"
chmod 000 "$rO14_lock"
run_outside "$rO_abs" "$rO14_lock" --tests
chmod 755 "$rO14_lock" 2>/dev/null || true
assert_has 'FAIL hermetic-outside' \
  'rO14: a config root that exists but cannot be traversed is not a PASS'
assert_rc 1 'rO14: an unresolvable config root drives the sweep to exit 1'

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

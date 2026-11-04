#!/usr/bin/env bash
# Fixture text is shell built in single quotes: `$x` and `$1` are DATA for the hook to grade.
# shellcheck disable=SC2016
set -uo pipefail

# Script: test_style_check.sh
# Purpose: Regression tests for style-check.sh: the auto-memory trailing-whitespace exemption and
#          that it stays scoped (tabs still caught, non-memory paths still checked, no over-exempt
#          outside the config dir), and the shellcheck note ratchet (rows R0 to R27) that lets a
#          note through only when its (SC code, trimmed flagged line) pair is already at HEAD.
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

# ---------------------------------------------------------------------------------------------
# The ratchet on note-level findings: rows R0 to R27
#
# style-check blocks on any shellcheck output; the ratchet lets a target-file NOTE through only
# when its (SC code, trimmed flagged line) pair is already present in the file's HEAD version.
# Fixtures are real git repos under the pwd -P resolved sandbox. The hook runs through the shipped
# process (`<interpreter> style-check.sh` with a JSON payload on stdin), so exit-time behaviour is
# the behaviour measured. Rows that need shellcheck, git or /bin/bash print a SKIP line when it is
# missing: a skipped row is counted apart from the passed ones.
# ---------------------------------------------------------------------------------------------

skipped=0
fx_failed=0
bash_bin="$(command -v bash)"
sandbox="$(cd "$sandbox" && pwd -P)"
shimroot="$sandbox/shims"
mkdir -p "$shimroot"

skip_row() {
  printf 'SKIP  %s\n' "$1"
  skipped=$((skipped + 1))
}

have_ratchet_tools=1
if ! command -v shellcheck >/dev/null 2>&1; then
  have_ratchet_tools=0
  skip_row "shellcheck ratchet rows (shellcheck not installed)"
fi
if ! command -v git >/dev/null 2>&1; then
  have_ratchet_tools=0
  skip_row "shellcheck ratchet rows (git not installed)"
fi

# fgit: git for FIXTURE SETUP only: no global or system config, so no signing prompt can hang it.
fgit() {
  GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1 git "$@"
}

# fx_repo <dir>: a fresh repository with signing off. Any failing fixture step marks the row failed.
fx_repo() {
  mkdir -p "$1" || fx_failed=1
  fgit -C "$1" init -q 2>/dev/null || fx_failed=1
  fgit -C "$1" config user.name fixture || fx_failed=1
  fgit -C "$1" config user.email fixture@example.invalid || fx_failed=1
  fgit -C "$1" config commit.gpgsign false || fx_failed=1
  fgit -C "$1" config tag.gpgsign false || fx_failed=1
}

# fx_commit <dir>: stage everything and commit it.
fx_commit() {
  fgit -C "$1" add -A || fx_failed=1
  fgit -C "$1" commit -q -m fixture || fx_failed=1
}

# a_base: the base fixture. Its one finding is the SC2086 note on line 4 (`rm $x`).
a_base() {
  printf '#!/usr/bin/env bash\nset -euo pipefail\nx=$1\nrm $x\n'
}

# hook_run <interpreter> <file> [VAR=value ...]: run the hook on <file>; sets h_rc, h_out, h_err.
# The cwd is the sandbox (never the fixture repo), so config discovery cannot lean on it.
hook_run() {
  local interp="$1" file="$2"
  shift 2
  h_rc=0
  (
    cd "$sandbox" || exit 99
    printf '{"tool_input":{"file_path":"%s"}}' "$file" \
      | env CLAUDE_CONFIG_DIR="$config" "$@" "$interp" "$script" >"$sandbox/h.out" 2>"$sandbox/h.err"
  ) || h_rc=$?
  h_out="$(cat "$sandbox/h.out")"
  h_err="$(cat "$sandbox/h.err")"
}

row_problem=""
row_begin() {
  row_problem=""
  fx_failed=0
  h_rc=0
}
r_rc() {
  [[ "$h_rc" -eq "$1" ]] || row_problem="${row_problem}want exit $1, got $h_rc; "
}
r_err_has() {
  case "$h_err" in *"$1"*) ;; *) row_problem="${row_problem}stderr lacks '$1'; " ;; esac
}
r_err_lacks() {
  case "$h_err" in *"$1"*) row_problem="${row_problem}stderr has '$1'; " ;; esac
}
r_out_has() {
  case "$h_out" in *"$1"*) ;; *) row_problem="${row_problem}stdout lacks '$1'; " ;; esac
}
r_end() {
  [[ "$fx_failed" -eq 0 ]] || row_problem="${row_problem}fixture setup failed; "
  if [[ -z "$row_problem" ]]; then
    printf 'PASS  %s (exit %d)\n' "$1" "$h_rc"
    pass=$((pass + 1))
  else
    printf 'FAIL  %s (%s)\n' "$1" "${row_problem% }"
    fail=$((fail + 1))
  fi
}

# mkshim <name> <command> <body>: write the executable bash script <shimroot>/<name>/<command>.
mkshim() {
  local dir="$shimroot/$1"
  mkdir -p "$dir" || fx_failed=1
  printf '#!/bin/bash\n%s\n' "$3" >"$dir/$2" || fx_failed=1
  chmod +x "$dir/$2" || fx_failed=1
}

# The git and awk the shims delegate to.
real_git="$(command -v git || true)"
real_awk="$(command -v awk || true)"

# r0_lines <file>: the hook's own shellcheck run, spelled as the hook spells it.
r0_lines() {
  shellcheck -x --source-path="$(dirname "$1")" -e SC1091 -f gcc "$1" 2>&1 || true
}

# count_notes <text>: how many `: note: ` lines the text holds.
count_notes() {
  printf '%s\n' "$1" | awk '/: note: /{n++} END{print n+0}'
}

# --- R0: the fixtures carry the findings the other rows rely on (FAIL, never skip) -------------
if [[ "$have_ratchet_tools" -eq 1 ]]; then
  row_begin
  r0="$sandbox/r0"
  mkdir -p "$r0"
  a_base >"$r0/a.sh"
  out="$(r0_lines "$r0/a.sh")"
  case "$out" in
    *$'\n'*) row_problem="a.sh yields more than one line: $out; " ;;
    *": note: "*) ;;
    *) row_problem="a.sh yields no note line: $out; " ;;
  esac
  case "$out" in *": warning: "*) row_problem="${row_problem}a.sh has a warning; " ;; esac
  r_end "R0a fixture a.sh yields exactly one note"

  row_begin
  printf '#!/usr/bin/env bash\nset -euo pipefail\nx=$1\nrm $x\nrm $(ls)\n' >"$r0/w.sh"
  out="$(r0_lines "$r0/w.sh")"
  notes="$(count_notes "$out")"
  warns="$(printf '%s\n' "$out" | awk '/: warning: /{n++} END{print n+0}')"
  [[ "$notes" -eq 1 && "$warns" -eq 1 ]] || row_problem="R6 fixture wants 1 note and 1 warning, got $notes and $warns; "
  r_end "R0b fixture w.sh yields one note and one warning"

  row_begin
  mkdir -p "$r0/rc"
  a_base >"$r0/rc/a.sh"
  printf 'enable=require-variable-braces\n' >"$r0/rc/.shellcheckrc"
  out="$(r0_lines "$r0/rc/a.sh")"
  case "$out" in *"[SC2250]"*) ;; *) row_problem="R15 fixture yields no SC2250 note: $out; " ;; esac
  case "$out" in *"[SC2086]"*) ;; *) row_problem="${row_problem}R15 fixture yields no SC2086 note; " ;; esac
  r_end "R0c fixture with a .shellcheckrc yields SC2086 and SC2250"
fi

# --- R1 R2 R7 R20 R22 are functions: they run again under /bin/bash (bash 3.2) below ----------

# case_r1 <interpreter> <label tag> <dir suffix> [shim dir put first on PATH]: R1 regression, an
# edit that adds no note passes.
case_r1() {
  local d="$sandbox/r1$3"
  row_begin
  fx_repo "$d"
  a_base >"$d/a.sh"
  fx_commit "$d"
  printf '# a comment\n' >>"$d/a.sh"
  if [[ -n "${4:-}" ]]; then
    hook_run "$1" "$d/a.sh" PATH="$4:$PATH"
  else
    hook_run "$1" "$d/a.sh"
  fi
  r_rc 0
  r_end "R1 comment-only edit of a file with a HEAD note passes$2"
}

# case_r2 <interpreter> <label tag> <dir suffix> [shim dir put first on PATH]: R2 a new note blocks,
# and only it is listed.
case_r2() {
  local d="$sandbox/r2$3"
  row_begin
  fx_repo "$d"
  a_base >"$d/a.sh"
  fx_commit "$d"
  printf 'y=$2\nrm $y\n' >>"$d/a.sh"
  if [[ -n "${4:-}" ]]; then
    hook_run "$1" "$d/a.sh" PATH="$4:$PATH"
  else
    hook_run "$1" "$d/a.sh"
  fi
  r_rc 2
  r_err_has "a.sh:6:4: note:"
  r_err_lacks "a.sh:4:4: note:"
  r_err_lacks ": warning: "
  r_err_lacks "pre-flagged"
  r_end "R2 a new note blocks and only the new note is listed$2"
}

# case_r7 <interpreter> <label tag> <dir suffix>: R7 an untracked file has no baseline.
case_r7() {
  local d="$sandbox/r7$3"
  row_begin
  fx_repo "$d"
  a_base >"$d/a.sh"
  hook_run "$1" "$d/a.sh"
  r_rc 2
  r_err_has "a.sh:4:4: note:"
  r_end "R7 an untracked file blocks$2"
}

# case_r20 <interpreter> <label tag> <dir suffix>: R20 an unparseable note beside a valid one.
case_r20() {
  local d="$sandbox/r20$3"
  row_begin
  fx_repo "$d"
  a_base >"$d/a.sh"
  fx_commit "$d"
  printf '# a comment\n' >>"$d/a.sh"
  mkshim "r20$3" shellcheck 'last="${!#}"
if [ "$last" = "-" ]; then
  printf -- "-:4:4: note: x [SC2086]\n"
else
  printf "%s:4:4: note: x [SC2086]\n%s:7:notanumber: note: odd [SC2086]\n" "$last" "$last"
fi
exit 1'
  hook_run "$1" "$d/a.sh" PATH="$shimroot/r20$3:$PATH"
  r_rc 2
  r_err_has "notanumber"
  r_end "R20 an unparseable note beside a valid one blocks$2"
}

# case_r22 <interpreter> <label tag> <dir suffix>: R22 a baseline shellcheck exiting 3 is not read.
case_r22() {
  local d="$sandbox/r22$3"
  row_begin
  fx_repo "$d"
  a_base >"$d/a.sh"
  fx_commit "$d"
  printf '# a comment\n' >>"$d/a.sh"
  mkshim "r22$3" shellcheck 'last="${!#}"
if [ "$last" = "-" ]; then
  printf -- "-:4:4: note: x [SC2086]\n"
  exit 3
fi
printf "%s:4:4: note: x [SC2086]\n" "$last"
exit 1'
  hook_run "$1" "$d/a.sh" PATH="$shimroot/r22$3:$PATH"
  r_rc 2
  r_end "R22 a baseline shellcheck exiting 3 is not read$2"
}

if [[ "$have_ratchet_tools" -eq 1 ]]; then
  case_r1 "$bash_bin" "" a
  case_r2 "$bash_bin" "" a

  # R3 line shift: five comment lines inserted after the `set` line add no note.
  row_begin
  d="$sandbox/r3"
  fx_repo "$d"
  a_base >"$d/a.sh"
  fx_commit "$d"
  {
    sed -n 1,2p "$d/a.sh"
    printf '# c1\n# c2\n# c3\n# c4\n# c5\n'
    sed -n '3,$p' "$d/a.sh"
  } >"$d/a.new" && mv "$d/a.new" "$d/a.sh"
  hook_run "$bash_bin" "$d/a.sh"
  r_rc 0
  r_end "R3 five lines inserted above the flagged line still pass"

  # R4 multiset: a second identical flagged line is one new finding.
  row_begin
  d="$sandbox/r4"
  fx_repo "$d"
  a_base >"$d/a.sh"
  fx_commit "$d"
  printf 'rm $x\n' >>"$d/a.sh"
  hook_run "$bash_bin" "$d/a.sh"
  r_rc 2
  r_err_has "a.sh:5:4: note:"
  r_end "R4 a second identical flagged line blocks"

  # R5 edited line: the flagged line itself changed, so it is new and the hint appears.
  row_begin
  d="$sandbox/r5"
  fx_repo "$d"
  a_base >"$d/a.sh"
  fx_commit "$d"
  {
    sed -n 1,3p "$d/a.sh"
    printf 'rm  $x # edited\n'
  } >"$d/a.new" && mv "$d/a.new" "$d/a.sh"
  hook_run "$bash_bin" "$d/a.sh"
  r_rc 2
  r_err_has "pre-flagged at HEAD, but the line changed"
  r_end "R5 an edited flagged line blocks with the pre-flagged hint"

  # R6 warning control: a warning is never suppressed, and no note is listed beside it.
  row_begin
  d="$sandbox/r6"
  fx_repo "$d"
  printf '#!/usr/bin/env bash\nset -euo pipefail\nx=$1\nrm $x\nrm $(ls)\n' >"$d/a.sh"
  fx_commit "$d"
  printf '# a comment\n' >>"$d/a.sh"
  hook_run "$bash_bin" "$d/a.sh"
  r_rc 2
  r_err_has ": warning: "
  r_err_lacks ": note: "
  r_end "R6 a warning at HEAD still blocks and no note is listed"

  case_r7 "$bash_bin" "" a

  # R8 not a repository.
  row_begin
  d="$sandbox/r8"
  mkdir -p "$d"
  a_base >"$d/a.sh"
  hook_run "$bash_bin" "$d/a.sh"
  r_rc 2
  r_end "R8 a file outside any repository blocks"

  # R9 no HEAD: a repository with no commit.
  row_begin
  d="$sandbox/r9"
  fx_repo "$d"
  a_base >"$d/a.sh"
  hook_run "$bash_bin" "$d/a.sh"
  r_rc 2
  r_end "R9 a repository with no HEAD blocks"

  # R10 renamed: HEAD holds only the old name.
  row_begin
  d="$sandbox/r10"
  fx_repo "$d"
  a_base >"$d/a.sh"
  fx_commit "$d"
  fgit -C "$d" mv a.sh b.sh || fx_failed=1
  hook_run "$bash_bin" "$d/b.sh"
  r_rc 2
  r_end "R10 an uncommitted rename blocks"

  # R11 staged launder: staging a new note does not make it pre-existing.
  row_begin
  d="$sandbox/r11"
  fx_repo "$d"
  a_base >"$d/a.sh"
  fx_commit "$d"
  printf 'y=$2\nrm $y\n' >>"$d/a.sh"
  fgit -C "$d" add a.sh || fx_failed=1
  printf '# elsewhere\n' >>"$d/a.sh"
  hook_run "$bash_bin" "$d/a.sh"
  r_rc 2
  r_err_lacks ": warning: "
  r_end "R11 a staged new note still blocks"

  # R12 broken repository: .git is an empty directory.
  row_begin
  d="$sandbox/r12"
  mkdir -p "$d/.git"
  a_base >"$d/a.sh"
  hook_run "$bash_bin" "$d/a.sh"
  r_rc 2
  r_end "R12 a broken repository blocks"

  # R13 poisoned environment: GIT_DIR and GIT_INDEX_FILE must not redirect the baseline read.
  row_begin
  d="$sandbox/r13"
  fx_repo "$d"
  a_base >"$d/a.sh"
  fx_commit "$d"
  printf '# a comment\n' >>"$d/a.sh"
  hook_run "$bash_bin" "$d/a.sh" GIT_DIR=/nonexistent GIT_INDEX_FILE=/nonexistent
  r_rc 0
  r_end "R13 a poisoned GIT_DIR does not redirect the baseline"

  # R14 object replacement: a replace ref must not become the baseline.
  row_begin
  d="$sandbox/r14"
  fx_repo "$d"
  a_base >"$d/a.sh"
  fx_commit "$d"
  printf 'y=$2\nrm $y\n' >>"$d/a.sh"
  old_blob="$(fgit -C "$d" rev-parse HEAD:a.sh)"
  new_blob="$(fgit -C "$d" hash-object -w "$d/a.sh")"
  fgit -C "$d" replace "$old_blob" "$new_blob" || fx_failed=1
  hook_run "$bash_bin" "$d/a.sh"
  r_rc 2
  r_end "R14 an object replacement is not the baseline"

  # R15 config discovery: the baseline run reads the same .shellcheckrc as the file run.
  row_begin
  d="$sandbox/r15"
  fx_repo "$d"
  a_base >"$d/a.sh"
  printf 'enable=require-variable-braces\n' >"$d/.shellcheckrc"
  fx_commit "$d"
  printf '# a comment\n' >>"$d/a.sh"
  hook_run "$bash_bin" "$d/a.sh"
  r_rc 0
  r_end "R15 the baseline reads the same .shellcheckrc as the file"

  # R16 a non-target line in the current run always blocks.
  row_begin
  d="$sandbox/r16"
  fx_repo "$d"
  a_base >"$d/a.sh"
  fx_commit "$d"
  printf '# a comment\n' >>"$d/a.sh"
  mkshim r16 shellcheck 'last="${!#}"
if [ "$last" = "-" ]; then
  printf -- "-:4:4: note: x [SC2086]\n-:4:4: note: x [SC2086]\n"
else
  printf "%s:4:4: note: x [SC2086]\n/other/lib.sh:4:4: note: x [SC2086]\n" "$last"
fi
exit 1'
  hook_run "$bash_bin" "$d/a.sh" PATH="$shimroot/r16:$PATH"
  r_rc 2
  r_err_has "/other/lib.sh:4:4: note:"
  r_end "R16 a non-target note in the current run blocks"

  # R17 a non-target line in the baseline never enters the baseline.
  row_begin
  d="$sandbox/r17"
  fx_repo "$d"
  a_base >"$d/a.sh"
  fx_commit "$d"
  printf '# a comment\n' >>"$d/a.sh"
  mkshim r17 shellcheck 'last="${!#}"
if [ "$last" = "-" ]; then
  printf -- "/other/lib.sh:4:4: note: x [SC2086]\n"
else
  printf "%s:4:4: note: x [SC2086]\n" "$last"
fi
exit 1'
  hook_run "$bash_bin" "$d/a.sh" PATH="$shimroot/r17:$PATH"
  r_rc 2
  r_end "R17 a non-target note in the baseline is not a baseline key"

  # R18 reporting: N counts matched current notes; it is on stdout when the edit passes.
  row_begin
  d="$sandbox/r18a"
  fx_repo "$d"
  a_base >"$d/a.sh"
  fx_commit "$d"
  printf '# a comment\n' >>"$d/a.sh"
  hook_run "$bash_bin" "$d/a.sh"
  r_rc 0
  r_out_has "(1 pre-existing shellcheck note(s) in HEAD ignored)"
  r_end "R18a one matched note is reported on the passing line"

  row_begin
  d="$sandbox/r18b"
  fx_repo "$d"
  printf '#!/usr/bin/env bash\nset -euo pipefail\nx=$1\nrm $x\ny=$2\nrm $y\n' >"$d/a.sh"
  fx_commit "$d"
  printf '# a comment\n' >>"$d/a.sh"
  hook_run "$bash_bin" "$d/a.sh"
  r_rc 0
  r_out_has "(2 pre-existing shellcheck note(s) in HEAD ignored)"
  r_end "R18b two matched notes read N=2"

  row_begin
  d="$sandbox/r18c"
  fx_repo "$d"
  printf '#!/usr/bin/env bash\nset -euo pipefail\nx=$1\nrm $x\ny=$2\nrm $y\n' >"$d/a.sh"
  fx_commit "$d"
  a_base >"$d/a.sh"
  hook_run "$bash_bin" "$d/a.sh"
  r_rc 0
  r_out_has "(1 pre-existing shellcheck note(s) in HEAD ignored)"
  r_end "R18c N counts matched current notes, not baseline notes"

  row_begin
  d="$sandbox/r18d"
  fx_repo "$d"
  a_base >"$d/a.sh"
  fx_commit "$d"
  printf 'y=$2\nrm $y\n' >>"$d/a.sh"
  hook_run "$bash_bin" "$d/a.sh"
  r_rc 2
  r_err_has "1 pre-existing note-level finding(s) in HEAD not counted."
  r_end "R18d a block also reports the pre-existing count"

  # R19 a clean file never pays for the baseline: no git call at all.
  row_begin
  d="$sandbox/r19"
  fx_repo "$d"
  printf '#!/usr/bin/env bash\nset -euo pipefail\necho hi\n' >"$d/a.sh"
  fx_commit "$d"
  printf '# a comment\n' >>"$d/a.sh"
  rm -f "$sandbox/r19.log"
  mkshim r19 git "printf 'call\\n' >>\"$sandbox/r19.log\"
exec \"$real_git\" \"\$@\""
  hook_run "$bash_bin" "$d/a.sh" PATH="$shimroot/r19:$PATH"
  r_rc 0
  [[ ! -s "$sandbox/r19.log" ]] || row_problem="${row_problem}git was called on a clean file; "
  r_end "R19 a clean file makes no git call"

  case_r20 "$bash_bin" "" a

  # R21 a line of no gcc shape blocks even when the note beside it is matched.
  row_begin
  d="$sandbox/r21"
  fx_repo "$d"
  a_base >"$d/a.sh"
  fx_commit "$d"
  printf '# a comment\n' >>"$d/a.sh"
  mkshim r21 shellcheck 'last="${!#}"
if [ "$last" = "-" ]; then
  printf -- "-:4:4: note: x [SC2086]\n"
else
  printf "%s:4:4: note: x [SC2086]\n%s: openBinaryFile: permission denied (Permission denied)\n" "$last" "$last"
fi
exit 1'
  hook_run "$bash_bin" "$d/a.sh" PATH="$shimroot/r21:$PATH"
  r_rc 2
  r_err_has "openBinaryFile"
  r_end "R21 a non-gcc line blocks beside a matched note"

  case_r22 "$bash_bin" "" a

  # R23 a baseline shellcheck killed by a signal is never read.
  row_begin
  d="$sandbox/r23"
  fx_repo "$d"
  a_base >"$d/a.sh"
  fx_commit "$d"
  printf '# a comment\n' >>"$d/a.sh"
  mkshim r23 shellcheck 'last="${!#}"
if [ "$last" = "-" ]; then
  printf -- "-:4:4: note: x [SC2086]\n"
  kill -KILL $$
fi
printf "%s:4:4: note: x [SC2086]\n" "$last"
exit 1'
  hook_run "$bash_bin" "$d/a.sh" PATH="$shimroot/r23:$PATH"
  r_rc 2
  r_end "R23 a baseline shellcheck killed by a signal is not read"

  # R24 awk failure: the status of the awk command substitution is captured, so the hook exits 2.
  row_begin
  d="$sandbox/r24"
  fx_repo "$d"
  a_base >"$d/a.sh"
  fx_commit "$d"
  printf '# a comment\n' >>"$d/a.sh"
  mkshim r24 awk 'exit 1'
  hook_run "$bash_bin" "$d/a.sh" PATH="$shimroot/r24:$PATH"
  r_rc 2
  r_end "R24 a failing awk blocks with exit 2"

  # R24b the MATCH awk call failing alone: its status is captured too, so the hook exits 2. The
  # shim fails only when SC_MODE is match and otherwise runs the real awk, so the gate call passes.
  row_begin
  d="$sandbox/r24b"
  fx_repo "$d"
  a_base >"$d/a.sh"
  fx_commit "$d"
  printf '# a comment\n' >>"$d/a.sh"
  mkshim r24b awk "if [ \"\${SC_MODE:-}\" = match ]; then exit 1; fi
exec \"$real_awk\" \"\$@\""
  hook_run "$bash_bin" "$d/a.sh" PATH="$shimroot/r24b:$PATH"
  r_rc 2
  r_end "R24b a failing match-mode awk blocks with exit 2"

  # R25 clean filter: the blob differs from the disk text, so the notes read as new (control).
  row_begin
  d="$sandbox/r25"
  fx_repo "$d"
  a_base >"$d/a.sh"
  printf '*.sh filter=f\n' >"$d/.gitattributes"
  fgit -C "$d" config filter.f.clean "sed 's/rm .x/rm  \$x/'" || fx_failed=1
  fx_commit "$d"
  printf '# a comment\n' >>"$d/a.sh"
  hook_run "$bash_bin" "$d/a.sh"
  r_rc 2
  r_end "R25 a clean filter that changes the flagged line blocks"

  # R26 real files: the three tracked scripts that drew the false blocks, copied from HEAD.
  repo_root="$(git -C "$here" rev-parse --show-toplevel 2>/dev/null || true)"
  if [[ -z "$repo_root" ]]; then
    skip_row "R26 real files (not inside a git checkout)"
  else
    for rel in scripts/tests/test_audit.sh scripts/tests/test_push_guard.sh skills/audit/audit.sh; do
      base="${rel##*/}"
      d="$sandbox/r26-${base%.sh}"
      row_begin
      fx_repo "$d"
      git -C "$repo_root" show "HEAD:$rel" >"$d/$base" || fx_failed=1
      fx_commit "$d"
      want_n="$(count_notes "$(r0_lines "$d/$base")")"
      [[ "$want_n" -ge 1 ]] || row_problem="the real file $rel no longer yields a note (someone fixed its notes): repoint R0d and R26 at a tracked file that still has one; "
      r_end "R0d fixture $base yields at least one note"

      row_begin
      if [[ "$want_n" -ge 1 ]]; then
        printf '# a comment\n' >>"$d/$base"
        hook_run "$bash_bin" "$d/$base"
        r_rc 0
        r_out_has "($want_n pre-existing shellcheck note(s) in HEAD ignored)"
      else
        row_problem="not run: $rel lost its notes, see the R0d row above; "
      fi
      r_end "R26 real file $base: a comment-only edit passes"
    done
    # R26 block half: a new note appended to the audit.sh copy blocks, with no warning listed.
    row_begin
    printf 'q=${1:-}\nrm $q\n' >>"$sandbox/r26-audit/audit.sh"
    hook_run "$bash_bin" "$sandbox/r26-audit/audit.sh"
    r_rc 2
    r_err_lacks ": warning: "
    r_end "R26 real file audit.sh: a new note blocks"
  fi

  # R27 pipefail volume: about 160 KB of notes. A `grep -q` on a live pipe under `set -o pipefail`
  # reports its producer's SIGPIPE here, and only at this size. Ten runs, one row.
  row_begin
  d="$sandbox/r27"
  fx_repo "$d"
  a_base >"$d/a.sh"
  fx_commit "$d"
  printf '# a comment\n' >>"$d/a.sh"
  mkshim r27 shellcheck 'last="${!#}"
pre="$last"
plen=$((80 - ${#pre} - 26))
if [ "$plen" -lt 1 ]; then plen=1; fi
pad=$(printf "%*s" "$plen" "" | tr " " "p")
i=0
while [ "$i" -lt 2000 ]; do
  printf "%s:4:4: note: %s [SC2086]\n" "$pre" "$pad"
  i=$((i + 1))
done
exit 1'
  shim_bytes="$("$shimroot/r27/shellcheck" "$d/a.sh" | wc -c | tr -d ' ')"
  [[ "$shim_bytes" -ge 150000 ]] || row_problem="the R27 shim wrote only $shim_bytes bytes; "
  r27_runs=0
  while [[ "$r27_runs" -lt 10 ]]; do
    r27_runs=$((r27_runs + 1))
    hook_run "$bash_bin" "$d/a.sh" PATH="$shimroot/r27:$PATH"
    [[ "$h_rc" -eq 0 ]] || row_problem="${row_problem}run $r27_runs exited $h_rc; "
    case "$h_out" in
      *"(2000 pre-existing shellcheck note(s) in HEAD ignored)"*) ;;
      *) row_problem="${row_problem}run $r27_runs stdout lacks the count; " ;;
    esac
  done
  r_end "R27 about 160 KB of notes pass ten times"

  # Second pass under bash 3.2: the suite's own bash is newer, so a 3.2-only abort would hide.
  if [[ -x /bin/bash ]]; then
    case_r1 /bin/bash " [/bin/bash]" b
    case_r2 /bin/bash " [/bin/bash]" b
    case_r7 /bin/bash " [/bin/bash]" b
    case_r20 /bin/bash " [/bin/bash]" b
    case_r22 /bin/bash " [/bin/bash]" b
    # The suite's PATH puts GNU awk first, so BSD awk is never otherwise exercised: run R1 and R2
    # once more with the system awk (the macOS BSD awk) first on PATH, under bash 3.2.
    if [[ -x /usr/bin/awk ]]; then
      mkdir -p "$shimroot/sysawk"
      ln -sf /usr/bin/awk "$shimroot/sysawk/awk"
      case_r1 /bin/bash " [/bin/bash /usr/bin/awk]" c "$shimroot/sysawk"
      case_r2 /bin/bash " [/bin/bash /usr/bin/awk]" c "$shimroot/sysawk"
    else
      skip_row "system awk pass (/usr/bin/awk absent)"
    fi
  else
    skip_row "bash 3.2 pass (/bin/bash absent)"
  fi
fi

printf '\n%d passed, %d failed, %d skipped\n' "$pass" "$fail" "$skipped"
[[ "$fail" -eq 0 ]]

#!/usr/bin/env bash
set -uo pipefail

# Script: audit.sh
# Purpose: Read-only mechanical compliance sweep over a target git repo's tracked files
# Usage: audit.sh [--scope <path>] [--tests]
#
# A `.auditignore` at the scope root (opt-in, one glob pathspec per line, `#` comments and
# blank lines ignored) excludes matching paths from the five text-content checks
# (format-trailing-ws, format-crlf, format-final-newline, format-tabs, md-links) only — it
# can never silence a code/config check (shellcheck, ruff, markdownlint, exec-bit, json,
# toml, sync-docs, mutation-anchors, tests, hermetic, hermetic-outside). No file present, or a
# present-but-empty file, sweeps unchanged.
#
# Exit codes:
#   0 — sweep completed, zero FAILs
#   1 — sweep completed, at least one FAIL
#   2 — usage error (bad/missing --scope, unknown flag)
#   129/130/143 — died on a trapped signal (HUP/INT/TERM); 128+n for any other
#     catchable fatal signal, though those are not trapped and the EXIT trap then
#     reads `$?` as 0 (see audit_on_exit).
#
# Terminal verdict line: every run this script exits from itself prints, as its LAST
# line of stdout, `RESULT: <STATUS> rc=<n> checks=<pass>/<fail>/<skip>` where STATUS is
# PASS (completed, zero FAILs) | FAIL (completed, >=1 FAIL) | ERROR (usage error, no
# sweep ran) | INCOMPLETE (died mid-sweep on a catchable signal). Clean is EXACTLY
# `RESULT: PASS` — an allowlist, so any value not anticipated here reads as not-clean.
# The line CANNOT be emitted on SIGKILL, so its ABSENCE never means clean: it means
# the run did not complete.
#
# NOTE: no `-e` — sweep-runner exemption (STYLE.md): one failing check or tool invocation
# must not abort the whole sweep, so every check below guards its own commands explicitly
# and never lets a single nonzero status escape uncaught.
#
# bash-3.2/BSD-safe throughout (macOS ships /bin/bash 3.2 as `/usr/bin/env bash` in many
# PATHs): no mapfile/readarray, no arrays at all (bash 3.2 errors on `"${empty_arr[@]}"`
# under `set -u` — a known pitfall — so file lists are plain newline-delimited strings
# walked with `while read`), no `sort -V` (BSD sort lacks it), no GNU-only flags.

script_dir="$(cd "$(dirname "$0")" && pwd)"

pass_count=0
fail_count=0
skip_count=0

# Sweep state, read by audit_on_exit(). Both MUST stay global — a `local` copy would be
# invisible to the trap. `audit_phase` goes init -> sweeping and exists only to tell a
# usage error (the sweep never started) from a death partway through it. `audit_reported`
# records that a verdict line has already been printed, so the exit handler stays silent
# on the normal path rather than emitting a second one.
audit_phase=init
audit_reported=no

usage() {
  printf 'Usage: audit.sh [--scope <path>] [--tests]\n' >&2
}

# ---------- verdict helpers ----------

verdict_pass() { # name
  printf 'PASS %s\n' "$1"
  pass_count=$((pass_count + 1))
}

verdict_fail() { # name detail
  printf 'FAIL %s — %s\n' "$1" "$2"
  fail_count=$((fail_count + 1))
}

verdict_skip() { # name reason
  printf 'SKIP %s — %s\n' "$1" "$2"
  skip_count=$((skip_count + 1))
}

audit_result_line() { # status rc -> the single machine-readable terminal verdict
  printf 'RESULT: %s rc=%d checks=%d/%d/%d\n' \
    "$1" "$2" "$pass_count" "$fail_count" "$skip_count"
  audit_reported=yes
}

# The completed verdicts (PASS/FAIL) are emitted by main() itself, positionally, with rc
# derived from fail_count — never from `$?`. This handler covers only the paths main()
# never reaches the end of, and can therefore emit ONLY ERROR or INCOMPLETE.
#
# That split is the whole safety property, and it is structural rather than conditional.
# `$?` inside an EXIT trap is NOT 128+n for a signal that was never trapped — it reads 0
# (measured: SIGUSR1 kills the process with rc 158 while the trap sees 0; SIGQUIT does
# not run the trap at all). So any design that decides PASS-vs-not inside this handler
# prints `RESULT: PASS rc=0` for a process killed mid-sweep. Enumerating more signals to
# trap would only shorten the list of ways to be wrong; emitting PASS from main() instead
# makes a clean verdict on a killed run unreachable for EVERY signal, trapped or not.
#
# The residual inaccuracy is confined to the rc FIELD on an untrapped signal (it reads 0
# where the process died 128+n). The STATUS is still INCOMPLETE, which never clears the
# allowlist, so this cannot be mistaken for a passing sweep.
audit_on_exit() { # exit-status
  [[ "$audit_reported" == yes ]] && return
  if [[ "$audit_phase" == init && "$1" -eq 2 ]]; then
    audit_result_line ERROR "$1"
  else
    audit_result_line INCOMPLETE "$1"
  fi
}

print_offenders() { # detail-block (newline-separated, unindented) -> indent 2sp, cap 50 lines
  local detail="$1" n
  [[ -z "$detail" ]] && return
  detail="${detail%$'\n'}"           # avoid a doubled trailing blank line
  n="$(printf '%s\n' "$detail" | wc -l | tr -d ' ')"
  if [[ "$n" -gt 50 ]]; then
    printf '%s\n' "$detail" | sed -n '1,50p' | sed 's/^/  /'
    printf '  … more (run the underlying tool for the full list)\n'
  else
    printf '%s\n' "$detail" | sed 's/^/  /'
  fi
}

# Lines of $1 that do not appear in $2 — the ONE-DIRECTIONAL difference. Never compare the
# two sets by SIZE: they can hold an equal number of lines while differing in both
# directions at once, which a tally reads as agreement. The separator cannot collide with
# porcelain output, every line of which begins with a two-character status field.
lines_only_in_first() { # first second
  printf '%s\n@@AUDIT-HERMETIC-SPLIT@@\n%s\n' "$1" "$2" | awk '
    $0 == "@@AUDIT-HERMETIC-SPLIT@@" { second = 1; next }
    !second { held[++n] = $0; next }
    { seen[$0] = 1 }
    END {
      for (i = 1; i <= n; i++)
        if (held[i] != "" && !(held[i] in seen)) print held[i]
    }
  '
}

# ---------- hermetic-outside helpers ----------
#
# Top-level names under the Claude config root that the harness itself rewrites while any
# suite runs. This is NOT the list of what is watched — it is the list of what is EXEMPT;
# everything else is watched, so a directory nobody anticipated is covered by default.
# Measured: 542 files change under the root in a two-hour window, concentrated here, and
# everything outside this set separates cleanly.
HERMETIC_CHURN='projects
file-history
plugins
tasks
sessions
shell-snapshots
paste-cache
backups
cache
debug
downloads
chrome
daemon
daemon.log
jobs
session-env
statsig
telemetry
todos
history.jsonl
stats-cache.json
mcp-needs-auth-cache.json
settings.local.json
.last-cleanup
.ruff_cache
.DS_Store'

# Paths that must never leave the watched set. Discovery cannot detect ABSENCE: widening the
# churn list above would silently stop watching whatever was added to it while still printing
# a clean PASS over the smaller set. `logs` heads the list because it is where the measured
# pollution actually landed — the exact path a later edit is tempted to exempt once this
# check starts reporting it.
HERMETIC_FLOOR='logs
skills
scripts
agents
settings.json
CLAUDE.md'

hermetic_is_churn() { # name -> 0 when exempt
  # grep -Fxq, never a `case` glob: a name holding `*` or `[` would match as a PATTERN.
  printf '%s\n' "$HERMETIC_CHURN" | grep -Fxq "$1"
}

hermetic_config_root() { # -> physical config root, or nonzero if there isn't one
  # `${HOME:-}`, not `$HOME`: under `set -u` an unbound variable does not fail this function,
  # it KILLS the shell (measured: rc 127). An unset HOME would abort the whole sweep instead
  # of skipping one check. Empty then yields `/.claude`, which is not a directory, so the
  # missing-root path below is reached normally.
  local raw="${AUDIT_HERMETIC_ROOT:-${CLAUDE_CONFIG_DIR:-${HOME:-}/.claude}}"
  [[ -d "$raw" ]] || return 1
  # `cd -P` resolves the symlink before anything walks it. The measured trap: the root IS a
  # symlink, so `find ~/.claude -type f` returns ZERO files — which reads exactly like
  # "nothing changed" rather than like a probe that never traversed anything.
  (cd -P "$raw" 2>/dev/null && pwd) || return 1
}

hermetic_watch_roots() { # root -> one absolute path per watched top-level entry
  local root="$1" e
  while IFS= read -r e; do
    [[ -z "$e" ]] && continue
    hermetic_is_churn "$e" && continue
    printf '%s\n' "$root/$e"
  done <<EOF
$(ls -1A "$root" 2>/dev/null)
EOF
}

hermetic_outside_files() { # watch-roots [extra find predicates...] -> absolute file paths
  local roots="$1" p
  shift
  while IFS= read -r p; do
    [[ -z "$p" ]] && continue
    # -L on every walk: the root and its entries may each be symlinks (see the trap above).
    find -L "$p" -type f "$@" 2>/dev/null
  done <<EOF
$roots
EOF
}

# ---------- .auditignore helpers ----------

git_with_excludes() {  # $1=scope $2=ignore-string, rest = git args; appends :(exclude) pathspecs
  local scope="$1" ignore="$2" g
  shift 2
  while IFS= read -r g; do
    g="${g#"${g%%[![:space:]]*}"}"; g="${g%"${g##*[![:space:]]}"}"   # trim FIRST
    case "$g" in ''|\#*) continue ;; esac                            # then skip blank/comment
    set -- "$@" ":(exclude)$g"
  done <<EOF
$ignore
EOF
  git -C "$scope" "$@" 2>/dev/null
}

# ---------- BSD-safe newest-nvm-version picker ----------
# Reads newline-separated `vX.Y.Z` strings on stdin, echoes the newest. Strips the leading
# `v` (bracket/GNU \t-style escapes aren't portable in ERE, and there is no `sort -V` on
# BSD sort), numeric-sorts each dotted field, then reattaches `v` to the winner.
pick_newest_version() {
  local stripped
  stripped="$(sed 's/^v//')"
  printf '%s\n' "$stripped" | sort -t. -k1,1n -k2,2n -k3,3n | tail -1 | sed 's/^/v/'
}

# ---------- checks ----------

check_format_trailing_ws() {
  # DEVIATION from the brief's literal `git grep -nIE '[ \t]+$'`: this git build's ERE
  # engine treats a bracket expression's `\t` as two literal characters (backslash, t),
  # not an escaped tab — verified against a real repo, where it false-flagged every line
  # ending in the plain letter "t" (i.e. most English prose). Built instead with a real
  # embedded tab byte, mirroring the same printf idiom the brief already uses for
  # format-crlf/format-tabs below.
  local scope="$1" ignore="$2" hits ws
  ws="$(printf ' \t')"
  hits="$(git_with_excludes "$scope" "$ignore" grep -nIE "[${ws}]+\$" -- . | head -n 51)"
  if [[ -n "$hits" ]]; then
    verdict_fail format-trailing-ws 'trailing whitespace found'
    print_offenders "$hits"
  else
    verdict_pass format-trailing-ws
  fi
}

check_format_crlf() {
  local scope="$1" ignore="$2" hits cr
  cr="$(printf '\r')"
  hits="$(git_with_excludes "$scope" "$ignore" grep -nIl "$cr" -- . | head -n 51)"
  if [[ -n "$hits" ]]; then
    verdict_fail format-crlf 'CRLF line endings found'
    print_offenders "$hits"
  else
    verdict_pass format-crlf
  fi
}

check_format_final_newline() {
  local scope="$1" ignore="$2" files f detail="" last
  files="$(git_with_excludes "$scope" "$ignore" grep -Il '' -- .)"
  while IFS= read -r f; do
    [[ -z "$f" ]] && continue
    [[ -s "$scope/$f" ]] || continue   # empty files pass
    last="$(tail -c1 "$scope/$f")"
    if [[ -n "$last" ]]; then
      detail="${detail}${f}"$'\n'
    fi
  done <<< "$files"
  if [[ -n "$detail" ]]; then
    verdict_fail format-final-newline 'tracked text file(s) missing a trailing newline'
    print_offenders "$detail"
  else
    verdict_pass format-final-newline
  fi
}

check_format_tabs() {
  local scope="$1" ignore="$2" hits tab
  tab="$(printf '\t')"
  hits="$(git_with_excludes "$scope" "$ignore" grep -n "$tab" -- '*.sh' '*.py' '*.json' '*.yaml' '*.yml' '*.md' | head -n 51)"
  if [[ -n "$hits" ]]; then
    verdict_fail format-tabs 'literal tab character found'
    print_offenders "$hits"
  else
    verdict_pass format-tabs
  fi
}

check_shellcheck() {
  local scope="$1" files
  files="$(git -C "$scope" ls-files -- '*.sh' 2>/dev/null)"
  if [[ -z "$files" ]]; then
    verdict_skip shellcheck 'no shell scripts'
    return
  fi
  if ! command -v shellcheck >/dev/null 2>&1; then
    verdict_skip shellcheck 'shellcheck not found'
    return
  fi
  local f detail="" out rc=0
  while IFS= read -r f; do
    [[ -z "$f" ]] && continue
    out="$(shellcheck -S warning "$scope/$f" 2>&1)" || rc=$?
    if [[ -n "$out" ]]; then
      detail="${detail}${out}"$'\n'
    fi
  done <<< "$files"
  if [[ "$rc" -ne 0 ]]; then
    verdict_fail shellcheck 'shellcheck reported findings'
    print_offenders "$detail"
  else
    verdict_pass shellcheck
  fi
}

check_ruff() {
  local scope="$1" files
  files="$(git -C "$scope" ls-files -- '*.py' 2>/dev/null)"
  if [[ -z "$files" ]]; then
    verdict_skip ruff 'no python files'
    return
  fi
  if ! command -v ruff >/dev/null 2>&1; then
    verdict_skip ruff 'ruff not found'
    return
  fi
  local f detail1="" detail2="" out rc1=0 rc2=0
  while IFS= read -r f; do
    [[ -z "$f" ]] && continue
    out="$(cd "$scope" && ruff check "$f" 2>&1)" || rc1=$?
    if [[ -n "$out" ]]; then
      detail1="${detail1}${out}"$'\n'
    fi
  done <<< "$files"
  while IFS= read -r f; do
    [[ -z "$f" ]] && continue
    out="$(cd "$scope" && ruff format --check "$f" 2>&1)" || rc2=$?
    if [[ -n "$out" ]]; then
      detail2="${detail2}${out}"$'\n'
    fi
  done <<< "$files"
  if [[ "$rc1" -ne 0 || "$rc2" -ne 0 ]]; then
    verdict_fail ruff 'ruff check/format reported findings'
    print_offenders "${detail1}"$'\n'"${detail2}"
  else
    verdict_pass ruff
  fi
}

check_markdownlint() {
  local scope="$1"
  if [[ ! -f "$scope/.markdownlint-cli2.jsonc" ]]; then
    verdict_skip markdownlint 'repo not opted in'
    return
  fi

  local path_prefix="" versions newest nodebin
  if ! command -v markdownlint-cli2 >/dev/null 2>&1; then
    nodebin=""
    versions="$(ls "$HOME/.nvm/versions/node" 2>/dev/null)"
    if [[ -n "$versions" ]]; then
      newest="$(printf '%s\n' "$versions" | pick_newest_version)"
      [[ -x "$HOME/.nvm/versions/node/$newest/bin/markdownlint-cli2" ]] \
        && nodebin="$HOME/.nvm/versions/node/$newest/bin"
    fi
    if [[ -z "$nodebin" ]]; then
      verdict_skip markdownlint 'markdownlint-cli2 not found'
      return
    fi
    path_prefix="$nodebin:"
  fi

  local out rc
  out="$(cd "$scope" && PATH="${path_prefix}${PATH}" markdownlint-cli2 "**/*.md" 2>&1)"; rc=$?
  if [[ "$rc" -ne 0 ]]; then
    verdict_fail markdownlint 'markdownlint-cli2 reported findings'
    print_offenders "$out"
  else
    verdict_pass markdownlint
  fi
}

check_md_links() {
  local scope="$1" ignore="$2" checker
  checker="$script_dir/../../scripts/md-links-check.py"
  if [[ ! -f "$checker" ]] || ! command -v python3 >/dev/null 2>&1; then
    verdict_skip md-links 'checker or python3 not found'
    return
  fi

  local files f detail="" out rc
  files="$(git_with_excludes "$scope" "$ignore" ls-files -- '*.md')"
  while IFS= read -r f; do
    [[ -z "$f" ]] && continue
    out="$(printf '{"tool_input":{"file_path":"%s"}}' "$scope/$f" | python3 "$checker" 2>&1)"
    rc=$?
    if [[ "$rc" -eq 2 ]]; then
      detail="${detail}${f}:"$'\n'"${out}"$'\n'
    fi
  done <<< "$files"
  if [[ -n "$detail" ]]; then
    verdict_fail md-links 'broken relative link(s) or anchor(s)'
    print_offenders "$detail"
  else
    verdict_pass md-links
  fi
}

check_exec_bit() {
  local scope="$1" mode sha stage path first2 detail=""
  # shellcheck disable=SC2034  # sha/stage are part of `ls-files -s` output shape, unused here
  while read -r mode sha stage path; do
    [[ -z "$mode" ]] && continue
    if [[ "$mode" == "100644" ]]; then
      # Builtin working-tree read, not a per-file `git cat-file` fork: sniffs the checked-out
      # file rather than the index blob (acceptable for a working-copy compliance sweep) —
      # zero forks instead of one fork-pair per 100644 file (12,853 of them timed out a
      # 300s sweep on the motivating repo).
      [[ -r "$scope/$path" && -s "$scope/$path" ]] || continue
      first2=""
      IFS= read -r -n 2 first2 < "$scope/$path" || true
      [[ "$first2" == '#!' ]] && detail="${detail}${path}"$'\n'
    fi
  done < <(git -C "$scope" ls-files -s)
  if [[ -n "$detail" ]]; then
    verdict_fail exec-bit 'tracked shebang file(s) missing the exec bit'
    print_offenders "$detail"
  else
    verdict_pass exec-bit
  fi
}

check_json() {
  local scope="$1" files
  files="$(git -C "$scope" ls-files -- '*.json' 2>/dev/null)"
  if [[ -z "$files" ]]; then
    verdict_skip json 'no tracked json files'
    return
  fi
  if ! command -v jq >/dev/null 2>&1; then
    verdict_skip json 'jq not found'
    return
  fi
  local f err detail=""
  while IFS= read -r f; do
    [[ -z "$f" ]] && continue
    if ! err="$(jq . "$scope/$f" 2>&1 >/dev/null)"; then
      detail="${detail}${f}:"$'\n'"${err}"$'\n'
    fi
  done <<< "$files"
  if [[ -n "$detail" ]]; then
    verdict_fail json 'invalid JSON'
    print_offenders "$detail"
  else
    verdict_pass json
  fi
}

check_toml() {
  local scope="$1" files
  files="$(git -C "$scope" ls-files -- '*.toml' 2>/dev/null)"
  if [[ -z "$files" ]]; then
    verdict_skip toml 'no tracked toml files'
    return
  fi
  if ! python3 -c 'import tomllib' >/dev/null 2>&1; then
    verdict_skip toml 'tomllib not available (python3 < 3.11)'
    return
  fi
  local f err detail=""
  while IFS= read -r f; do
    [[ -z "$f" ]] && continue
    if ! err="$(python3 -c 'import sys, tomllib; tomllib.load(open(sys.argv[1], "rb"))' "$scope/$f" 2>&1)"; then
      detail="${detail}${f}:"$'\n'"${err}"$'\n'
    fi
  done <<< "$files"
  if [[ -n "$detail" ]]; then
    verdict_fail toml 'invalid TOML'
    print_offenders "$detail"
  else
    verdict_pass toml
  fi
}

check_sync_docs() {
  local scope="$1" runner
  runner="$script_dir/../sync-docs/sync_docs.py"
  if [[ ! -f "$runner" ]]; then
    verdict_skip sync-docs 'runner not present'
    return
  fi
  if ! command -v python3 >/dev/null 2>&1; then
    verdict_skip sync-docs 'python3 not found'
    return
  fi
  local hits
  hits="$(git -C "$scope" grep -l '<!-- sync:' -- '*.md' 2>/dev/null)"
  if [[ -z "$hits" ]]; then
    verdict_skip sync-docs 'no sync markers in scope'
    return
  fi
  local out rc
  out="$(python3 "$runner" --scope "$scope" sync --check 2>&1)"; rc=$?
  if [[ "$rc" -ne 0 ]]; then
    verdict_fail sync-docs 'sync-docs reported drift'
    print_offenders "$out"
  else
    verdict_pass sync-docs
  fi
}

# A mutation campaign's `old` string is a reference into another file that nothing maintains,
# so it goes stale silently: whoever refactors a subject is the last person to think of
# re-pointing its campaign, and a campaign whose anchor no longer resolves ERRORs rather than
# grading anything. The same check catches a mutant STRANDED in the tree by a killed campaign
# (its own anchor is then absent), which is the more dangerous of the two — a verification tool
# left inverted into a rubber stamp, showing nothing unusual in `git status`.
#
# Static, so it belongs in this half of the sweep: the checker reads campaigns with `ast` and
# never imports them, which is what keeps it out of the hermetic window that only brackets
# `--tests`.
check_mutation_anchors() {
  local scope="$1" runner campaigns f
  runner="$script_dir/../../scripts/mutation-anchors-check.py"
  if [[ ! -f "$runner" ]]; then
    verdict_skip mutation-anchors 'runner not present'
    return
  fi
  if ! command -v python3 >/dev/null 2>&1; then
    verdict_skip mutation-anchors 'python3 not found'
    return
  fi
  # The same rule the checker itself applies — a basename of `mutate_*.py`. Deriving it the
  # same way on both sides is what keeps a scope with no campaigns a SKIP here rather than the
  # checker's zero-campaign ERROR, which exists to catch a sweep over nothing.
  campaigns=""
  while IFS= read -r f; do
    [[ -z "$f" ]] && continue
    case "${f##*/}" in
      mutate_*.py) campaigns="${campaigns}${f}"$'\n' ;;
    esac
  done <<< "$(git -C "$scope" ls-files 2>/dev/null)"
  if [[ -z "$campaigns" ]]; then
    verdict_skip mutation-anchors 'no mutation campaigns in scope'
    return
  fi
  local out rc
  out="$(python3 "$runner" --scope "$scope" 2>&1)"; rc=$?
  if [[ "$rc" -ne 0 ]]; then
    verdict_fail mutation-anchors 'a campaign anchor no longer resolves exactly once'
    print_offenders "$out"
  else
    verdict_pass mutation-anchors
  fi
}

check_tests() {
  local scope="$1" ran=false detail="" sh_list py_list t out rc
  sh_list="$(git -C "$scope" ls-files -- 'scripts/tests/test_*.sh' 2>/dev/null)"
  while IFS= read -r t; do
    [[ -z "$t" ]] && continue
    ran=true
    out="$("$scope/$t" 2>&1)"; rc=$?
    if [[ "$rc" -ne 0 ]]; then
      detail="${detail}${t} exited ${rc}:"$'\n'"${out}"$'\n'
    fi
  done <<< "$sh_list"

  py_list="$(git -C "$scope" ls-files -- '*test_*.py' 2>/dev/null)"
  if [[ -n "$py_list" ]] && python3 -m pytest --version >/dev/null 2>&1; then
    ran=true
    out="$(cd "$scope" && python3 -m pytest -q 2>&1)"; rc=$?
    if [[ "$rc" -ne 0 ]]; then
      detail="${detail}pytest exited ${rc}:"$'\n'"${out}"$'\n'
    fi
  fi

  if [[ "$ran" == false ]]; then
    verdict_skip tests 'no scripts/tests/test_*.sh or test_*.py found'
    return
  fi
  if [[ -n "$detail" ]]; then
    verdict_fail tests 'test suite failure(s)'
    print_offenders "$detail"
  else
    verdict_pass tests
  fi
}

# A `--tests` run is the only part of this sweep that EXECUTES repo code, so it is the only
# part that can write anything. This compares the working tree either side of that execution.
#
# Scope is `git status --porcelain -uall`: tracked modifications plus untracked files, but
# NOT ignored ones. Deliberate on both ends — it is the same instrument the publish path's
# clean-tree precondition uses, so a PASS here means the next brick can still apply; and it
# leaves a suite free to emit the build noise that is already ignored (`__pycache__/`,
# `.pytest_cache/`) without a false alarm.
#
# It COMPARES rather than asserting a clean tree: a tree the operator had already dirtied is
# not the run's doing, and only what the run itself changed is a finding.
#
# Measured instance (2026-07-29): a documented command wrote its artifact to the repo root,
# where it fails the NEXT publish brick's clean-tree precondition — the publish path blocking
# itself on a file its own documentation told the operator to create. Every other check here
# reads `git ls-files`, so a file a suite DROPS is structurally invisible to all of them, and
# the suite still exits 0.
#
# This covers the INSIDE-the-repo half only. A suite that writes OUTSIDE the scope — measured:
# 12 synthetic records in the operator's real `~/.claude/logs/` — is invisible here and needs
# the separate, allowlist-based check that half requires.
check_hermetic() { # scope before-snapshot before-status
  local scope="$1" before="$2" before_status="$3" after rc appeared vanished

  # A snapshot that FAILED must never compare equal to anything. Both ends are guarded
  # because a symmetric failure — empty before, empty after — is exactly the shape that
  # reads as a clean pass while having measured nothing at all.
  if [[ "$before_status" -ne 0 ]]; then
    verdict_fail hermetic 'could not read the working tree BEFORE the suite ran'
    return
  fi
  after="$(git -C "$scope" status --porcelain -uall 2>&1)"; rc=$?
  if [[ "$rc" -ne 0 ]]; then
    verdict_fail hermetic 'could not read the working tree AFTER the suite ran'
    print_offenders "$after"
    return
  fi

  if [[ "$before" == "$after" ]]; then
    verdict_pass hermetic
    return
  fi

  verdict_fail hermetic 'the suite changed the working tree'
  appeared="$(lines_only_in_first "$after" "$before")"
  vanished="$(lines_only_in_first "$before" "$after")"
  if [[ -n "$appeared" ]]; then
    print_offenders "appeared:"$'\n'"$appeared"
  fi
  if [[ -n "$vanished" ]]; then
    print_offenders "vanished:"$'\n'"$vanished"
  fi
}

# The other half of hermeticity: what a suite run leaves BEYOND the scope.
#
# Measured instance (2026-07-29): a diagnostic log added to a hook defaulted to
# `~/.claude/logs/`, and long-standing suite rows feed input reaching exactly that branch — so
# 12 synthetic records accumulated in the OPERATOR'S REAL log across four suite runs, while an
# edit-time hook re-ran that suite on every edit to the hook. Nothing surfaced it: this sweep
# reads `git ls-files` inside the scope, so damage outside it is invisible here by
# construction. Nor is it merely noise — the config root is itself inside a git repo, so the
# pollution lands in committable territory. The repair was an env override "every test that
# drives this branch MUST set", which is an advisory with no instrument; this is the instrument.
#
# LIMITATION, stated because it bounds what a FAIL means: this attributes to the suite
# anything that changed under the root during the window. Run non-interactively that is exact;
# run alongside a live session that also writes there, a FAIL may name the session's work.
# It is never the other way round — nothing here can turn a real write into a PASS.
check_hermetic_outside() { # scope root before-files before-status marker
  local scope="$1" root="$2" before="$3" before_status="$4" marker="$5"
  local roots after n_before n_after appeared vanished modified floor_bad f scope_phys

  if [[ -z "$root" ]]; then
    verdict_skip hermetic-outside 'no Claude config root to watch'
    return
  fi
  # Both sides must be PHYSICAL. main() resolves the scope with a plain `pwd` (logical), while
  # the root is resolved with `cd -P` — so under a symlinked TMPDIR the scope reads `/tmp/…`
  # and the root `/private/tmp/…`, and the containment test below could never match. Measured:
  # this silently skipped the SKIP on every macOS default, and only a fixture built under
  # `mktemp -d` exposed it.
  scope_phys="$(cd -P "$scope" 2>/dev/null && pwd)" || scope_phys="$scope"
  if [[ "$root" == "$scope_phys" || "$root" == "$scope_phys"/* ]]; then
    verdict_skip hermetic-outside 'config root lies inside the scope — hermetic covers it'
    return
  fi
  if [[ -z "$marker" ]]; then
    verdict_skip hermetic-outside 'could not create a timestamp marker; modifications unprovable'
    return
  fi
  if [[ "$before_status" -ne 0 ]]; then
    verdict_fail hermetic-outside 'could not snapshot the config root BEFORE the suite ran'
    return
  fi

  # A floor member that drifted onto the churn list means the watch quietly shrank. Checked
  # every run rather than once at authoring time: that edit is exactly how a future session
  # silences a genuine finding, and it would otherwise still read as a clean PASS.
  floor_bad=""
  while IFS= read -r f; do
    [[ -z "$f" ]] && continue
    hermetic_is_churn "$f" && floor_bad="${floor_bad}${f}"$'\n'
  done <<EOF
$HERMETIC_FLOOR
EOF
  if [[ -n "$floor_bad" ]]; then
    verdict_fail hermetic-outside 'a protected path was moved onto the churn exemption list'
    print_offenders "$floor_bad"
    return
  fi

  roots="$(hermetic_watch_roots "$root")"
  after="$(hermetic_outside_files "$roots" | LC_ALL=C sort)"

  # The denominator. Zero watched files is never a clean bill of health — it is the signature
  # of a probe that traversed nothing (see hermetic_config_root's symlink trap).
  n_before="$(printf '%s\n' "$before" | grep -c . || true)"
  n_after="$(printf '%s\n' "$after" | grep -c . || true)"
  if [[ "$n_before" -eq 0 ]] || [[ "$n_after" -eq 0 ]]; then
    verdict_fail hermetic-outside "watched 0 files under $root — the probe measured nothing"
    return
  fi

  appeared="$(lines_only_in_first "$after" "$before")"
  vanished="$(lines_only_in_first "$before" "$after")"
  # An APPEND leaves the path set unchanged, and the measured instance WAS an append to a log
  # that already existed — a path-set comparison alone would have called it clean.
  modified="$(hermetic_outside_files "$roots" -newer "$marker" | LC_ALL=C sort)"

  if [[ -z "$appeared" ]] && [[ -z "$vanished" ]] && [[ -z "$modified" ]]; then
    verdict_pass hermetic-outside
    return
  fi
  verdict_fail hermetic-outside "the suite wrote outside the scope, under $root"
  if [[ -n "$appeared" ]]; then
    print_offenders "appeared:"$'\n'"$appeared"
  fi
  if [[ -n "$vanished" ]]; then
    print_offenders "vanished:"$'\n'"$vanished"
  fi
  if [[ -n "$modified" ]]; then
    print_offenders "modified:"$'\n'"$modified"
  fi
}

# ---------- main ----------

main() {
  local scope="" run_tests=false auditignore="" ignore="" invalid_detail="" ignore_count=0 g
  local hermetic_before="" hermetic_status=0
  local hermetic_root="" hermetic_marker="" hermetic_out_before="" hermetic_out_status=1

  while [[ $# -gt 0 ]]; do
    case "$1" in
      --scope)
        if [[ $# -lt 2 ]]; then
          usage
          exit 2
        fi
        scope="$2"
        shift 2
        ;;
      --tests)
        run_tests=true
        shift
        ;;
      *)
        usage
        exit 2
        ;;
    esac
  done

  if [[ -z "$scope" ]]; then
    scope="$(git rev-parse --show-toplevel 2>/dev/null)"
  fi
  if [[ -z "$scope" ]] || [[ ! -d "$scope" ]] \
    || ! git -C "$scope" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    usage
    exit 2
  fi
  scope="$(cd "$scope" && pwd)"
  audit_phase=sweeping

  [[ -f "$scope/.auditignore" ]] && auditignore="$(cat "$scope/.auditignore")"

  if [[ -n "$auditignore" ]]; then
    while IFS= read -r g; do
      g="${g#"${g%%[![:space:]]*}"}"; g="${g%"${g##*[![:space:]]}"}"   # trim FIRST
      case "$g" in ''|\#*) continue ;; esac                            # then skip blank/comment
      # Cheap probe: does git accept this as a pathspec exclude? An anchored
      # gitignore-style pattern (e.g. `/gen/*`) or one that escapes the repo
      # (e.g. `../outside`) makes git exit 128 — never silently trust it.
      if git -C "$scope" ls-files -- ":(exclude)$g" . >/dev/null 2>&1; then
        ignore="${ignore}${g}"$'\n'
        ignore_count=$((ignore_count + 1))
      else
        invalid_detail="${invalid_detail}${g}"$'\n'
      fi
    done <<EOF
$auditignore
EOF
  fi

  if [[ -n "$invalid_detail" ]]; then
    verdict_fail auditignore 'invalid exclude pattern(s) in .auditignore'
    print_offenders "$invalid_detail"
  fi

  [[ "$ignore_count" -gt 0 ]] \
    && printf '(.auditignore: %d exclude pattern(s) active)\n' "$ignore_count"

  check_format_trailing_ws "$scope" "$ignore"
  check_format_crlf "$scope" "$ignore"
  check_format_final_newline "$scope" "$ignore"
  check_format_tabs "$scope" "$ignore"
  check_shellcheck "$scope"
  check_ruff "$scope"
  check_markdownlint "$scope"
  check_md_links "$scope" "$ignore"
  check_exec_bit "$scope"
  check_json "$scope"
  check_toml "$scope"
  check_sync_docs "$scope"
  check_mutation_anchors "$scope"
  if [[ "$run_tests" == true ]]; then
    # Snapshot BEFORE the only checks that execute repo code, and hand both the snapshot
    # and its status to check_hermetic — a failed read must not be able to compare equal.
    hermetic_before="$(git -C "$scope" status --porcelain -uall 2>/dev/null)"
    hermetic_status=$?

    # Same, for the world outside the scope. The marker is what makes an APPEND visible; it
    # lives in TMPDIR so this check is not itself a writer of anything it watches.
    hermetic_root="$(hermetic_config_root)" || hermetic_root=""
    if [[ -n "$hermetic_root" ]]; then
      hermetic_marker="$(mktemp "${TMPDIR:-/tmp}/audit-hermetic.XXXXXX" 2>/dev/null)" \
        || hermetic_marker=""
      if [[ -n "$hermetic_marker" ]]; then
        hermetic_out_before="$(hermetic_outside_files \
          "$(hermetic_watch_roots "$hermetic_root")" | LC_ALL=C sort)"
        hermetic_out_status=$?
      fi
    fi

    check_tests "$scope"
    check_hermetic "$scope" "$hermetic_before" "$hermetic_status"
    check_hermetic_outside "$scope" "$hermetic_root" "$hermetic_out_before" \
      "$hermetic_out_status" "$hermetic_marker"
    [[ -n "$hermetic_marker" ]] && rm -f "$hermetic_marker"
  fi

  printf '%d passed, %d failed, %d skipped\n' "$pass_count" "$fail_count" "$skip_count"

  # Emit the completed verdict HERE, not from the exit handler, and derive rc from
  # fail_count rather than reading `$?`. Reaching this line is itself the proof that
  # every check ran, so no separate "did we finish?" flag can drift out of step with it —
  # and a process killed before this point can only ever be reported INCOMPLETE, because
  # PASS and FAIL are unreachable from audit_on_exit().
  if [[ "$fail_count" -eq 0 ]]; then
    audit_result_line PASS 0
  else
    audit_result_line FAIL 1
  fi

  [[ "$fail_count" -eq 0 ]]
}

# Guarded (not a bare `main "$@"`) so the test suite can `source` this file to unit-test
# pick_newest_version() without also running a full sweep. The traps live INSIDE the
# guard for the same reason: a top-level EXIT trap would install itself into any shell
# that sources this file and append a RESULT line to that shell's stdout.
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  # Single quotes: `$?` must expand when the trap RUNS, not when it is defined, and
  # passing it as an argument captures it before anything else can clobber it.
  trap 'audit_on_exit "$?"' EXIT
  trap 'exit 143' TERM
  trap 'exit 130' INT
  trap 'exit 129' HUP
  main "$@"
fi

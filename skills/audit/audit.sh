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
# toml, sync-docs, tests). No file present, or a present-but-empty file, sweeps unchanged.
#
# Exit codes:
#   0 — sweep completed, zero FAILs
#   1 — sweep completed, at least one FAIL
#   2 — usage error (bad/missing --scope, unknown flag)
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

# ---------- main ----------

main() {
  local scope="" run_tests=false auditignore="" ignore="" invalid_detail="" ignore_count=0 g

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
  if [[ "$run_tests" == true ]]; then
    check_tests "$scope"
  fi

  printf '%d passed, %d failed, %d skipped\n' "$pass_count" "$fail_count" "$skip_count"
  [[ "$fail_count" -eq 0 ]]
}

# Guarded (not a bare `main "$@"`) so the test suite can `source` this file to unit-test
# pick_newest_version() without also running a full sweep.
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi

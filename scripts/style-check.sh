#!/usr/bin/env bash
set -euo pipefail

# Script: style-check.sh
# Purpose: Global PostToolUse hook — validate file edits against STYLE.md
# Usage: Called by Claude Code hooks with JSON on stdin
#
# Exit codes:
#   0 — all checks passed (stdout added to context)
#   2 — checks failed (stderr fed back to Claude for correction)
#
# The sh|bash arm blocks on every shellcheck output line except a target-file note positively matched
# to the file's HEAD version. A note is matched when its (SC code, trimmed flagged line) pair is
# already present at HEAD; the edit must add no net new such pair. With no obtainable baseline, every
# note blocks. Errors, warnings and lines of no gcc shape are never matched, so they always block.

# ---------- Parse stdin JSON ----------
# HOOK CONTRACT: the target arrives as a JSON payload on stdin; argv is ignored. Refuse the two
# invocations this cannot serve, because each otherwise reads as SUCCESS — with argv and stdin at
# EOF it exits 0 having examined nothing, and with a terminal stdin it blocks forever.
if [ "$#" -gt 0 ] || [ -t 0 ]; then
  printf '%s\n' \
    "$(basename "$0") is a Claude Code hook: it reads a JSON payload on stdin and ignores arguments." \
    "Running it with filenames examines nothing. See scripts/HOOKS.md for the payload form." >&2
  exit 2
fi

input=$(cat)
file_path=$(printf '%s' "$input" | jq -r '.tool_input.file_path // empty' 2>/dev/null) || file_path=""

if [[ -z "$file_path" ]]; then
  exit 0
fi

if [[ ! -f "$file_path" ]]; then
  exit 0
fi

# ---------- Helpers ----------
errors=()

add_error() {
  errors+=("$1")
}

check_final_newline() {
  if [[ -s "$file_path" ]] && [[ "$(tail -c 1 "$file_path" | wc -l)" -eq 0 ]]; then
    add_error "Missing final newline"
  fi
}

check_no_tabs() {
  local tab_lines
  tab_lines=$(grep -n $'^\t' "$file_path" 2>/dev/null || true)
  if [[ -n "$tab_lines" ]]; then
    add_error "Tab indentation found (use spaces):\n${tab_lines}"
  fi
}

check_no_trailing_ws() {
  # Exempt Claude Code's auto-memory files. The memory subsystem augments
  # frontmatter after each write (injecting node_type/originSessionId) and
  # leaves the `metadata:` key with a trailing space, which re-triggers this
  # check on every memory write — busywork to strip each time. Upstream-correct
  # fix is in the memory writer (don't emit trailing whitespace when
  # augmenting); this is a workaround — revert it when the writer stops emitting
  # trailing whitespace. Anchored on the real config dir (CLAUDE_CONFIG_DIR, else
  # ~/.claude) so only the genuine memory tree is exempt — not an unrelated
  # */projects/*/memory/*.md elsewhere — and the check stays intact everywhere else.
  local mem_base="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/projects"
  case "$file_path" in
    "$mem_base"/*/memory/*.md)
      return 0
      ;;
  esac
  local ws_lines
  ws_lines=$(grep -nE '[[:blank:]]+$' "$file_path" 2>/dev/null || true)
  if [[ -n "$ws_lines" ]]; then
    add_error "Trailing whitespace found:\n${ws_lines}"
  fi
}

sc_n=0
sc_hint=0
sc_block=""
sc_tmp=""
sc_m=""

# ---------- shellcheck note ratchet ----------
# The awk program computes only the MATCHED set: which current target notes consumed a HEAD note.
# It never decides what blocks; the shell derives the block set as every output line NOT matched.
# A note's key is "[SCnnnn]<TAB>trimmed text of the flagged source line".
# Modes (SC_MODE): "gate" reads the current gcc output on stdin and prints how many target notes
# carry a key; "match" reads the baseline gcc output, then the current one (file operands), and
# prints "M<TAB>n" for each matched current line n plus one "H" if a still-unconsumed baseline
# note shares its code with a current note that is NOT matched.
# The `$` characters below belong to awk, not to the shell, so SC2016 does not apply here.
# shellcheck disable=SC2016
sc_awk_program='
function trim(s) { gsub(/^[ \t]+|[ \t]+$/, "", s); return s }
function load(path, arr,    n, l) {
  n = 0
  while ((getline l < path) > 0) { n++; arr[n] = l }
  close(path)
  return n
}
function parse(rec,    i, head, m, a) {
  p_fn = ""; p_ln = ""; p_col = ""; p_code = ""
  i = index(rec, ": note: ")
  if (i == 0) return 0
  head = substr(rec, 1, i - 1)
  m = split(head, a, ":")
  if (m < 3) return 0
  p_ln = a[m - 1]; p_col = a[m]
  if (p_ln !~ /^[0-9]+$/ || p_col !~ /^[0-9]+$/) return 0
  if (!match(rec, /\[SC[0-9]+\]$/)) return 0
  p_code = substr(rec, RSTART, RLENGTH)
  p_fn = substr(head, 1, length(head) - length(p_ln) - length(p_col) - 2)
  return 1
}
function keyfor(n, src,    t) {
  p_ln += 0
  if (p_ln < 1 || p_ln > n) return ""
  t = trim(src[p_ln])
  return p_code "\t" t
}
function ckey(rec) { if (parse(rec) && p_fn == want) return keyfor(cn, cur); return "" }
function bkey(rec) { if (parse(rec) && p_fn == "-") return keyfor(bn, base); return "" }
BEGIN {
  want = ENVIRON["SC_WANT"]; mode = ENVIRON["SC_MODE"]; bout = ENVIRON["SC_BOUT"]
  cn = load(ENVIRON["SC_CSRC"], cur)
  if (mode == "match") bn = load(ENVIRON["SC_BSRC"], base)
}
mode == "gate" { if (ckey($0) != "") g++; next }
FILENAME == bout { k = bkey($0); if (k != "") pool[k]++; next }
{
  cnt++
  k = ckey($0)
  if (k == "") next
  if (pool[k] > 0) { pool[k]--; print "M\t" cnt }
  else { split(k, kp, "\t"); unm[kp[1]]++ }
}
END {
  if (mode == "gate") { print g + 0; exit }
  for (k in pool) if (pool[k] > 0) { split(k, kp, "\t"); left[kp[1]] = 1 }
  for (cd in unm) if (cd in left) { print "H"; break }
}
'

# sc_run <target>: the one spelling of the shellcheck invocation, used for the current file and for
# the HEAD blob ("-"), so the two runs can never be given different flags.
sc_run() {
  shellcheck -x --source-path="$sc_dir" -e SC1091 -f gcc "$@" 2>&1
}

sc_cleanup() {
  if [[ -n "${sc_tmp:-}" ]]; then
    rm -rf "$sc_tmp" || true
  fi
}

# sc_ratchet_body: runs inside the temp dir. Every early `return 0` leaves the strict defaults the
# caller set (every output line blocks); only the final assignments can remove a line from the block
# set. Each new command's status is captured: this hook runs under `set -e`, and an abort with any
# status other than 2 would let the whole edit through.
sc_ratchet_body() {
  local rc=0 aline idx line matched="" n=0 hint=0 blocked="" i=0
  printf '%s\n' "$sc_output" >"$sc_tmp/cur.out" && rc=0 || rc=$?
  [[ "$rc" -eq 0 ]] || return 0
  env -u GIT_DIR -u GIT_WORK_TREE -u GIT_INDEX_FILE -u GIT_OBJECT_DIRECTORY \
    -u GIT_ALTERNATE_OBJECT_DIRECTORIES -u GIT_COMMON_DIR \
    git --no-replace-objects -C "$sc_dir" --no-pager --no-optional-locks -c core.fsmonitor=false \
    cat-file blob "HEAD:./${file_path##*/}" >"$sc_tmp/blob" 2>/dev/null && rc=0 || rc=$?
  [[ "$rc" -eq 0 ]] || return 0
  ( cd "$sc_dir" 2>/dev/null || exit 97; sc_run - ) <"$sc_tmp/blob" >"$sc_tmp/base.out" && rc=0 || rc=$?
  case "$rc" in 0 | 1) ;; *) return 0 ;; esac
  sc_m=$(SC_MODE=match SC_WANT="$file_path" SC_CSRC="$file_path" SC_BSRC="$sc_tmp/blob" \
    SC_BOUT="$sc_tmp/base.out" awk "$sc_awk_program" "$sc_tmp/base.out" "$sc_tmp/cur.out") && rc=0 || rc=$?
  [[ "$rc" -eq 0 ]] || return 0
  while IFS= read -r aline; do
    case "$aline" in
      M$'\t'*)
        idx="${aline#M?}"
        case "$idx" in
          '' | *[!0-9]*) ;;
          *)
            matched="$matched $idx "
            n=$((n + 1))
            ;;
        esac
        ;;
      H) hint=1 ;;
    esac
  done <<<"$sc_m"
  while IFS= read -r line; do
    i=$((i + 1))
    case "$matched" in *" $i "*) continue ;; esac
    blocked="${blocked}${blocked:+$'\n'}${line}"
  done <<<"$sc_output"
  sc_block="$blocked"
  sc_n="$n"
  sc_hint="$hint"
  return 0
}

# Entry point of the ratchet. Reads $file_path, $sc_output and $sc_dir; sets $sc_block (what blocks),
# $sc_n (target notes matched to HEAD) and $sc_hint. The caller pre-sets the strict defaults.
# No `grep -q`, `grep -m1` or `head` may read a live pipe in the ratchet functions, nor any pipe whose
# producer is unbounded: under `set -o pipefail` they report the producer's SIGPIPE (141) for output
# large enough that it is still writing, and a note-heavy file reaches that size. (Pipes bounded to
# a line or five, such as `head -1 file | grep -q`, elsewhere in this file are not that case.)
shellcheck_ratchet() {
  local gate="" rc=0
  gate=$(SC_MODE=gate SC_WANT="$file_path" SC_CSRC="$file_path" awk "$sc_awk_program" <<<"$sc_output") && rc=0 || rc=$?
  [[ "$rc" -eq 0 ]] || return 0
  case "$gate" in '' | 0 | *[!0-9]*) return 0 ;; esac
  sc_tmp=$(mktemp -d "${TMPDIR:-/tmp}/style-check.XXXXXX") && rc=0 || rc=$?
  if [[ "$rc" -ne 0 || -z "$sc_tmp" ]]; then
    sc_tmp=""
    return 0
  fi
  # This file sets no other EXIT trap, and `trap - EXIT` below would clobber a later one: add any
  # future trap after this function runs, or chain this one.
  trap sc_cleanup EXIT
  sc_ratchet_body
  sc_cleanup
  sc_tmp=""
  trap - EXIT
  return 0
}

# ---------- Frontmatter checks (skills, agents) ----------
check_frontmatter() {
  if [[ "$(head -1 "$file_path")" != "---" ]]; then
    add_error "Missing YAML frontmatter (file must start with '---')"
  else
    frontmatter=$(awk '/^---$/{c++; next} c==1' "$file_path")
    if ! grep -q '^name:' <<<"$frontmatter"; then
      add_error "Frontmatter missing 'name:' field"
    fi
    if ! grep -q '^description:' <<<"$frontmatter"; then
      add_error "Frontmatter missing 'description:' field"
    fi
  fi
}

case "$file_path" in
  */tests/fixtures/*)
    : # test fixtures are intentionally varied — never validate their frontmatter
    ;;
  */agents/README.md|*/agents/index.md)
    : # index READMEs carry no frontmatter (mirrors AgentsHandler excludes)
    ;;
  */skills/*/SKILL.md)
    check_frontmatter
    ;;
  */agents/*.md)
    # Only Claude agent dirs carry frontmatter: .claude/agents/ (project-level) or an
    # agents/ dir sitting beside a skills/ dir (this config repo's shape). A foreign
    # repo's agents/ docs folder is plain markdown — demanding frontmatter there would
    # false-block every edit in that repo.
    agents_parent="${file_path%/agents/*}"
    if [[ "$file_path" == */.claude/agents/*.md || -d "$agents_parent/skills" ]]; then
      check_frontmatter
    fi
    ;;
esac

# ---------- Route by extension ----------
ext="${file_path##*.}"

case "$ext" in
  py)
    check_no_tabs
    check_no_trailing_ws
    if command -v python3 >/dev/null 2>&1; then
      # ast.parse validates syntax without writing __pycache__ into the user's repo.
      py_output=$(python3 -c 'import ast, sys; ast.parse(open(sys.argv[1], "rb").read(), sys.argv[1])' "$file_path" 2>&1 || true)
      if [[ -n "$py_output" ]]; then
        add_error "Python syntax error:\n${py_output}"
      fi
    fi
    check_final_newline
    ;;

  sh|bash)
    check_no_tabs
    check_no_trailing_ws
    # Check shebang
    if ! head -1 "$file_path" | grep -q '^#!'; then
      add_error "Missing shebang line"
    fi
    # Check set -euo pipefail in first 5 lines
    # Allow set -uo pipefail (without -e) for test harnesses that need to
    # continue execution past assertion failures.
    if ! head -5 "$file_path" | grep -qE 'set -e?uo pipefail'; then
      add_error "Missing 'set -euo pipefail' in first 5 lines"
    fi
    # Run shellcheck if available
    if command -v shellcheck >/dev/null 2>&1; then
      # file_path is taken as absolute (hook payloads carry absolute paths); a relative path with a
      # directory part would resolve --source-path differently after the baseline subshell's cd.
      sc_dir=$(dirname "$file_path")
      sc_output=$(sc_run "$file_path" || true)
      sc_block="$sc_output"
      shellcheck_ratchet
      if [[ -n "$sc_block" ]]; then
        sc_msg="shellcheck warnings:\n${sc_block}"
        if [[ "$sc_hint" -eq 1 ]]; then
          sc_msg="${sc_msg}\nA note above was pre-flagged at HEAD, but the line changed: revert the text, or add a per-line '# shellcheck disable=SCxxxx' with a rationale."
        fi
        if [[ "$sc_n" -gt 0 ]]; then
          sc_msg="${sc_msg}\n${sc_n} pre-existing note-level finding(s) in HEAD not counted."
        fi
        add_error "$sc_msg"
      fi
    fi
    check_final_newline
    ;;

  js|mjs|cjs)
    check_no_tabs
    check_no_trailing_ws
    check_final_newline
    ;;

  json)
    check_no_tabs
    check_no_trailing_ws
    if command -v python3 >/dev/null 2>&1; then
      if ! python3 -m json.tool "$file_path" >/dev/null 2>&1; then
        add_error "Invalid JSON"
      fi
    fi
    check_final_newline
    ;;

  yaml|yml)
    check_no_tabs
    check_no_trailing_ws
    if command -v yamllint >/dev/null 2>&1; then
      yl_output=$(yamllint -d relaxed "$file_path" 2>&1 || true)
      if [[ -n "$yl_output" ]]; then
        add_error "yamllint warnings:\n${yl_output}"
      fi
    fi
    check_final_newline
    ;;

  toml)
    check_no_tabs
    check_no_trailing_ws
    # tomllib is stdlib from Python 3.11; older interpreters skip silently (fail open).
    if command -v python3 >/dev/null 2>&1 && python3 -c 'import tomllib' 2>/dev/null; then
      toml_output=$(python3 -c '
import sys
import tomllib
with open(sys.argv[1], "rb") as f:
    tomllib.load(f)
' "$file_path" 2>&1 || true)
      if [[ -n "$toml_output" ]]; then
        add_error "Invalid TOML:\n${toml_output}"
      fi
    fi
    check_final_newline
    ;;

  md)
    check_no_tabs
    check_no_trailing_ws
    check_final_newline
    ;;

  *)
    # No checks for unrecognized extensions
    exit 0
    ;;
esac

# ---------- Report results ----------
if [[ ${#errors[@]} -gt 0 ]]; then
  printf 'style-check FAILED for %s:\n' "$file_path" >&2
  for err in "${errors[@]}"; do
    printf '  - %b\n' "$err" >&2
  done
  exit 2
fi

if [[ "$sc_n" -gt 0 ]]; then
  printf 'style-check passed: %s (%s pre-existing shellcheck note(s) in HEAD ignored)\n' "$file_path" "$sc_n"
else
  printf 'style-check passed: %s\n' "$file_path"
fi
exit 0

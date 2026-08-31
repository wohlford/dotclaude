#!/usr/bin/env bash
set -uo pipefail

# Script: audit.sh
# Purpose: Read-only mechanical compliance sweep over a target git repo's tracked files
#   Two checks deliberately also see UNTRACKED, unignored files, each for a reason given at its
#   own definition: `hermetic` (a run that writes to the tree is a finding wherever it lands)
#   and `mutation-anchors` (a campaign is untracked precisely when it is new). Every other
#   check is tracked-only, so an untracked violation stays invisible to the sweep.
# Usage: audit.sh [--scope <path>] [--tests]
#
# A `.auditignore` at the scope root (opt-in, one glob pathspec per line, `#` comments and
# blank lines ignored) excludes matching paths from the five text-content checks
# (format-trailing-ws, format-crlf, format-final-newline, format-tabs, md-links) only — it
# can never silence a code/config check (shellcheck, ruff, markdownlint, env-claims, exec-bit,
# json, toml, sync-docs, mutation-anchors, pre-push-installed, tests, hermetic,
# hermetic-outside).
# No file present, or a present-but-empty file, sweeps unchanged.
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

# Also read by audit_on_exit(), and global for the same reason as the two above: a `local` in
# main() would be invisible to the trap. This one is a cleanup handle rather than sweep state —
# the temp file the outside-the-scope hermeticity check dates its `-newer` comparison against.
# Its live window brackets the suite-running phase, the one that executes arbitrary repo code
# and therefore the likeliest point for an operator to interrupt the sweep; while the only `rm`
# sat on main()'s normal path, an interrupt in exactly that window leaked the file.
#
# Deliberately NO `check_*` function name in this comment: the name-parity suite counts those
# literals across the whole file and declares an exact occurrence count, so naming one here
# breaks a mutation target two files away. Measured — it did.
hermetic_marker=""

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
  # ABOVE the early return, not below it. On the normal path a verdict has already been printed,
  # so the next line returns — cleanup placed after it would run only on the paths that have
  # already cleaned up, and never on the interrupted ones it exists for. main() disowns the
  # handle when it removes the file itself, so this is a backstop and not a second owner.
  [[ -n "$hermetic_marker" ]] && rm -f "$hermetic_marker"
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

hermetic_config_root_raw() { # -> the CONFIGURED root path, resolved or not
  # Split out so the precedence lives in exactly one place. Callers need to tell "no such path"
  # apart from "present but unresolvable", and the only honest way to ask that is against the
  # same string `hermetic_config_root` consulted — a hand-copied second expansion here is how
  # the two silently disagree the first time the precedence changes.
  # `${HOME:-}`, not `$HOME`: under `set -u` an unbound variable does not fail this function,
  # it KILLS the shell (measured: rc 127). An unset HOME would abort the whole sweep instead
  # of skipping one check. Empty then yields `/.claude`, which is not a directory, so the
  # missing-root path below is reached normally.
  printf '%s\n' "${AUDIT_HERMETIC_ROOT:-${CLAUDE_CONFIG_DIR:-${HOME:-}/.claude}}"
}

hermetic_config_root() { # -> physical config root, or nonzero if there isn't one
  local raw
  raw="$(hermetic_config_root_raw)"
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
  local roots="$1" p agg=0
  shift
  while IFS= read -r p; do
    [[ -z "$p" ]] && continue
    # An entry that vanished between the `ls` above and this walk is DATA, not instrument
    # failure — it surfaces as `vanished` in the comparison. Only a walk that FAILED on an
    # entry still present leaves part of the tree unmeasured.
    [[ -e "$p" ]] || continue
    # -L on every walk: the root and its entries may each be symlinks (see the trap above).
    find -L "$p" -type f "$@" 2>/dev/null || agg=1
  done <<EOF
$roots
EOF
  # AGGREGATED, never the loop's last status. `find` runs once per root, so a bare `while`
  # reports only the LAST root's result and a failure anywhere earlier is invisible. Measured:
  # roots [MISSING, ok] returned 0 with one walk failed, while [ok, MISSING] returned 1 — so
  # the guard downstream fired or not depending on nothing but the order of `ls` output.
  return "$agg"
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

# Reads stdin, prints a hex sha256 digest. macOS ships no `sha256sum` by default; `shasum`
# is the portable fallback (same BSD/GNU split every other tool-lookup in this file routes
# around). Used only by check_pre_push_installed.
sha256_hex() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum | awk '{print $1}'
  else
    shasum -a 256 | awk '{print $1}'
  fi
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

# Per-failing-file excerpt bound for the ruff report. The NAMING is deliberately unbounded: every
# failing file is always named and only its excerpt is clipped.
#
# Sized against `--output-format concise`, which `check_ruff` passes for exactly this reason.
# Measured (ruff 0.16.3): a two-line file with three findings prints 46 lines in the DEFAULT format
# and 5 concise. At 46 lines a 12-line excerpt kept only the FIRST finding and dropped the rest
# behind the truncation notice — re-creating, one level down, the very hiding this check was fixed
# to stop. Concise puts one finding per line, so 12 covers ~10 findings per file.
#
# A line wrongly KEPT costs one line of noise; a line wrongly DROPPED costs a diagnosis — and the
# full output is one `ruff` re-run away, which the truncation notice spells out verbatim.
RUFF_EXCERPT_MAX=12

# Aggregate EXCERPT budget across all failing invocations. Deliberately NOT a hard cap on output:
# past saturation every further failing invocation still contributes its two header lines, so the
# total stays O(N) — measured 429 / 550 / 590 lines at 30 / 70 / 80 failing files. That overshoot is
# the point. Naming every failing unit beats bounding the total, which is the same trade the `tests`
# check makes; a hard cap would drop whole files, which is the defect being fixed. Per-file bounding alone is NOT enough: measured on 86
# tracked .py files all failing both sub-tools, the detail reached 1,737 lines / 49 KB against the
# old flat cap's 68. This skill's own SKILL.md has an agent run audit.sh, surface its stdout and key
# on the LAST line — so an upstream output cap would eat every check after `ruff` AND the RESULT:
# line, reintroducing at the AGGREGATE level exactly the "a truncated output keeps the wrong half"
# defect this change removes per-file.
#
# Past the budget every remaining failing file is still NAMED and only its excerpt is dropped, which
# is the invariant this check advertises. Note `ruff format --check` is deliberately NOT switched to
# --output-format concise: that would bound the volume (17 lines -> 2) by discarding the diff, which
# is the only part of a formatting finding that says what to change.
RUFF_EXCERPT_BUDGET=400

ruff_block() { # sub-tool file output [header-only] -> a labelled block, indented for the report
  local tool="$1" file="$2" body="$3" header_only="${4:-0}" n
  printf '  %s %s:\n' "$tool" "$file"
  if [[ "$header_only" -eq 1 ]]; then
    printf '    … excerpt omitted (aggregate budget) — run: %s '"'"'%s'"'"'\n' "$tool" "$file"
    return
  fi
  # A failing invocation CAN be silent — a `cd "$scope"` failure is not captured by a `2>&1` that
  # binds to ruff alone — so never guard the label on non-empty output, or the FAIL names no file.
  if [[ -z "$body" ]]; then
    printf '    (no output)\n'
    return
  fi
  n="$(printf '%s\n' "$body" | wc -l | tr -d ' ')"
  if [[ "$n" -gt "$RUFF_EXCERPT_MAX" ]]; then
    printf '%s\n' "$body" | sed -n "1,${RUFF_EXCERPT_MAX}p" | sed 's/^/    /'
    printf '    … %s more line(s) — run: %s '"'"'%s'"'"'\n' "$((n - RUFF_EXCERPT_MAX))" "$tool" "$file"
  else
    printf '%s\n' "$body" | sed 's/^/    /'
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
    # Only PRESENCE is probed, not version. `check_ruff` passes --output-format, which a ruff
    # predating that flag rejects with rc 2 — every file then yields a usage-error block and the
    # check FAILs repo-wide. Loud and fail-closed rather than silent, but the FAIL text
    # ("reported findings") misattributes the cause, so look here first if every file fails at once.
    verdict_skip ruff 'ruff not found'
    return
  fi
  local f detail="" out rc rc1=0 rc2=0 emitted=0 omitted=0 block
  # Collect ONLY from an invocation that FAILED. BOTH sub-tools print on SUCCESS — `ruff check`
  # says `All checks passed!`, `ruff format --check` says `1 file already formatted` — so
  # collecting unconditionally accumulated one padding line per file. Measured on this repo: 86
  # tracked .py files produced 86 padding lines against print_offenders' 50-line cap, which made
  # the real failure (always concatenated after) STRUCTURALLY unprintable — and the 50 lines that
  # did print were indistinguishable from a clean run, so the FAIL block read as reassuring.
  #
  # What stops being collected is EXACTLY output from an invocation whose exit status was 0. The
  # verdict is driven only by rc1/rc2, and this block is reached only in the FAIL arm, so no
  # OFFENDER can be lost. Residual, stated rather than left to be rediscovered: rc-0 runs can also
  # carry stderr warnings, and those are dropped too.
  #
  # Shape copied from check_md_links below — collect on failure, label with the file — rather than
  # invented here.
  while IFS= read -r f; do
    [[ -z "$f" ]] && continue
    out="$(cd "$scope" && ruff check --output-format concise "$f" 2>&1)"; rc=$?
    if [[ "$rc" -ne 0 ]]; then
      rc1="$rc"
      if [[ "$emitted" -ge "$RUFF_EXCERPT_BUDGET" ]]; then
        block="$(ruff_block 'ruff check' "$f" "$out" 1)"
        omitted=$((omitted + 1))
      else
        block="$(ruff_block 'ruff check' "$f" "$out")"
      fi
      detail="${detail}${block}"$'\n'
      emitted=$((emitted + $(printf '%s\n' "$block" | wc -l | tr -d ' ')))
    fi
  done <<< "$files"
  while IFS= read -r f; do
    [[ -z "$f" ]] && continue
    out="$(cd "$scope" && ruff format --check "$f" 2>&1)"; rc=$?
    if [[ "$rc" -ne 0 ]]; then
      rc2="$rc"
      if [[ "$emitted" -ge "$RUFF_EXCERPT_BUDGET" ]]; then
        block="$(ruff_block 'ruff format --check' "$f" "$out" 1)"
        omitted=$((omitted + 1))
      else
        block="$(ruff_block 'ruff format --check' "$f" "$out")"
      fi
      detail="${detail}${block}"$'\n'
      emitted=$((emitted + $(printf '%s\n' "$block" | wc -l | tr -d ' ')))
    fi
  done <<< "$files"
  if [[ "$rc1" -ne 0 || "$rc2" -ne 0 ]]; then
    verdict_fail ruff 'ruff check/format reported findings'
    # Deliberately NOT print_offenders. Its flat 50-line cap is right for like-for-like offenders
    # whose every line self-names (the format-* checks), but a ruff diagnostic names its file ONCE
    # per BLOCK rather than once per line, so a flat cap silently drops whole FILES. Measured under
    # --output-format concise (4-line blocks): 14 failing files exhaust 50 lines and the last is
    # never named. ruff_block bounds each file's EXCERPT instead and
    # always names the file — the same split the `tests` check makes, for the same reason.
    if [[ "$omitted" -gt 0 ]]; then
      detail="${detail}  … ${omitted} further failing invocation(s) reported header-only"$'\n'
    fi
    printf '%s' "$detail"
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

check_env_claims() {
  local scope="$1" checker out rc
  # OPT-IN BY SCOPE, exactly as check_markdownlint gates on "$scope/.markdownlint-cli2.jsonc" and
  # check_pre_push_installed gates on a marker read from the scope's own refs. The checker is
  # resolved from the AUDITED REPO, never from "$script_dir/../../scripts/" — its claim table is
  # hardcoded to THIS repo's CLAUDE.md, so an installation-resolved checker would grade every
  # foreign scope with this repo's claims.
  checker="$scope/scripts/env-claims-check.py"
  if [[ ! -f "$checker" ]]; then
    verdict_skip env-claims 'scope does not ship scripts/env-claims-check.py'
    return
  fi
  if ! command -v python3 >/dev/null 2>&1; then
    verdict_skip env-claims 'python3 not found'
    return
  fi
  out="$(python3 "$checker" --scope "$scope" 2>&1)"; rc=$?
  case "$rc" in
    0) verdict_pass env-claims ;;
    3) verdict_skip env-claims 'the documented environment is not present on this machine' ;;
    2) verdict_fail env-claims 'unprovable: the checker could not reach a verdict (instrument failure)'
       print_offenders "$out" ;;
    *) verdict_fail env-claims 'a documented environment claim no longer holds'
       print_offenders "$out" ;;
  esac
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
  #
  # The population must be the UNION of the checker's two predicates — tracked (what it grades)
  # plus untracked-and-unignored (what it refuses to leave unjudged) — which is exactly
  # `--cached --others --exclude-standard`. A tracked-only gate here would skip the whole check
  # in the one case its untracked guard exists for: the first campaign a repo ever gets, still
  # unadded. The guard would then be unreachable at precisely the moment it matters, and this
  # SKIP is silent about what it did not run.
  campaigns=""
  while IFS= read -r f; do
    [[ -z "$f" ]] && continue
    case "${f##*/}" in
      mutate_*.py) campaigns="${campaigns}${f}"$'\n' ;;
    esac
  done <<< "$(git -C "$scope" ls-files --cached --others --exclude-standard 2>/dev/null)"
  if [[ -z "$campaigns" ]]; then
    verdict_skip mutation-anchors 'no mutation campaigns in scope'
    return
  fi
  local out rc
  out="$(python3 "$runner" --scope "$scope" 2>&1)"; rc=$?
  if [[ "$rc" -ne 0 ]]; then
    # rc 1 is a rotted or ambiguous anchor; rc 2 is a campaign that went unjudged — unreadable,
    # or present in the tree but untracked. Naming only the first would prescribe the wrong
    # repair for the second, so defer to the checker's own output, which says which it found.
    verdict_fail mutation-anchors 'a campaign anchor no longer resolves, or a campaign went unjudged'
    print_offenders "$out"
  else
    verdict_pass mutation-anchors
  fi
}

# ---------- the tests check's reporting ----------
#
# `tests` deliberately does NOT route its detail through print_offenders. For the content checks
# the offender is a list of offending FILES: the first 50 are representative and the rest are the
# same defect, so the cap is right there and is unchanged. Here the "offender" is another tool's
# entire stdout, whose interesting lines are the FAILURES — and suites print passes as they go, so
# a head-cap reliably keeps the useless half. Measured twice, the second time at the last gate
# before an irreversible publish; neither failure reproduced, so both diagnoses are unrecoverable.
#
# A per-suite budget under a global cap does not fix it either: at ~21 lines per suite, TWO failing
# suites fit under 50 and THREE do not, so the third is truncated and a fourth is never even NAMED
# (names live only in the per-suite headers). Every failing suite is therefore always named, and
# only its excerpt is bounded.
tests_artifact_root=""
tests_artifact_tried=false
TESTS_EXCERPT_MAX=20

# Deliberately GENEROUS, and deliberately NOT load-bearing. The complete output is preserved in the
# artifact, so a line wrongly KEPT costs one line of noise while a line wrongly DROPPED costs
# nothing. That asymmetry is the whole point: narrowing a matcher to suppress noise is precisely how
# true positives get dropped silently, and here it cannot happen.
TESTS_FAILURE_RE='FAIL|ERROR|Traceback|AssertionError|^E[[:space:]]|fatal:|[0-9]+ (failed|error)'

# Lazily create the directory holding failing suites' complete output. Created only on the first
# FAILURE, so a passing sweep — the overwhelming majority — writes nothing at all: a default output
# path would otherwise make every run of this tool a writer of real state.
#
# It SETS tests_artifact_root and returns only a status — it must never echo the path for a caller
# to capture with `$( )`. Measured: doing that ran every assignment inside a command-substitution
# SUBSHELL, so the memoisation never took effect (a fresh directory per failing suite) and the
# parent's global stayed empty, making the report claim the artifact was unavailable while the
# files sat on disk. The `while … done <<< "$list"` loop below is a here-string, not a pipe, so the
# loop body does run in the current shell and these assignments survive it.
tests_artifact_dir() { # scope -> sets tests_artifact_root; nonzero when unavailable
  local scope="$1" base base_phys scope_phys
  if [[ "$tests_artifact_tried" == true ]]; then
    [[ -n "$tests_artifact_root" ]] || return 1
    return 0
  fi
  tests_artifact_tried=true
  base="${TMPDIR:-/tmp}"
  # BOTH sides physical. A containment test with one side logical and the other resolved can never
  # match, so the guard would silently never fire — the measured trap this file already documents
  # for the hermetic root. If the temp root lies inside the scope, writing there would trip
  # `hermetic` with a message blaming the SUITE for a write the engine itself made, sending the
  # reader to debug the wrong component; skip the artifact instead.
  scope_phys="$(cd -P "$scope" 2>/dev/null && pwd)" || scope_phys="$scope"
  base_phys="$(cd -P "$base" 2>/dev/null && pwd)" || base_phys="$base"
  if [[ "$base_phys" == "$scope_phys" || "$base_phys" == "$scope_phys"/* ]]; then
    return 1
  fi
  tests_artifact_root="$(mktemp -d "$base/audit-tests-XXXXXX" 2>/dev/null)" || tests_artifact_root=""
  [[ -n "$tests_artifact_root" ]] || return 1
  return 0
}

tests_artifact_write() { # scope label output
  local scope="$1" label="$2" out="$3" safe target n
  tests_artifact_dir "$scope" || return 1
  # `printf '%s'`, never `echo`: echo's trailing newline would become a trailing `_`.
  safe="$(printf '%s' "$label" | tr -c 'A-Za-z0-9._-' '_')"
  # NEVER overwrite. `tr` is many-to-one — `test_a b.sh` and `test_a_b.sh` sanitise to the same
  # name — so a bare write silently destroys the first suite's output, which is precisely the
  # loss this whole check exists to prevent, and it would do so while the report still claimed
  # every failing suite's text was preserved. Suffix instead, and give up rather than spin.
  target="$tests_artifact_root/$safe.log"
  n=2
  while [[ -e "$target" ]]; do
    [[ "$n" -gt 99 ]] && return 1
    target="$tests_artifact_root/$safe-$n.log"
    n=$((n + 1))
  done
  printf '%s\n' "$out" > "$target" 2>/dev/null || return 1
  return 0
}

# Never returns empty for a failing suite: a bare header would say a suite failed while showing
# nothing about it. `sed -n '1,Np'` rather than `head -n N` so nothing upstream can take SIGPIPE.
tests_excerpt() { # output
  local out="$1" matched n
  if [[ -z "$out" ]]; then
    printf '(suite produced no output)\n'
    return 0
  fi
  matched="$(printf '%s\n' "$out" | grep -E "$TESTS_FAILURE_RE" 2>/dev/null)"
  if [[ -n "$matched" ]]; then
    n="$(printf '%s\n' "$matched" | wc -l | tr -d ' ')"
    printf '%s\n' "$matched" | sed -n "1,${TESTS_EXCERPT_MAX}p"
    # Mark truncation. Silently cutting would make a partial excerpt read exactly like a complete
    # one, which is the shape this whole change exists to remove.
    if [[ "$n" -gt "$TESTS_EXCERPT_MAX" ]]; then
      printf '(… %d more matching lines in the artifact)\n' "$((n - TESTS_EXCERPT_MAX))"
    fi
  else
    printf '(no failure-shaped line; last %d lines)\n' "$TESTS_EXCERPT_MAX"
    printf '%s\n' "$out" | tail -n "$TESTS_EXCERPT_MAX"
  fi
  return 0
}

# Registration, not liveness (S2 of the push-enforcement design). This asserts the
# `git-hooks/pre-push` boundary is actually the file `.git/hooks/pre-push` — or a linked
# worktree's shared equivalent — will run: by RESOLVED PATH, never by reading
# `core.hooksPath`, which cannot see a `-c` or `GIT_CONFIG_*` override. Whether the hook
# actually BLOCKS a real push is answered only by scripts/tests/test_pre_push_hook.sh, which
# performs one; this check cannot see that and does not claim to.
#
# Static and unconditional, like exec-bit and mutation-anchors: this reads no repo code and
# needs no --tests gate. Never scoped by .auditignore — a repo cannot hide a missing or
# stale push boundary any more than it can hide a broken tracked .json.
#
# ONE arm is conditional: the source comparison at the bottom sets itself aside when the
# mismatch is provably an artifact of which commit is checked out (the historical trees
# per-brick auditing produces). Its predicate, its measured evidence and the reason it is
# narrowed to that single arm are documented at the comparison itself.
check_pre_push_installed() {
  local scope="$1" ref marker_found=false

  # Same dormancy predicate as the hook itself: armed iff SOME refs/heads/* branch carries a
  # tracked .publication.toml, checked by branch rather than by one hardcoded name — a check
  # keyed on a single branch would disagree with a hook that is not, and would itself become
  # a `git branch -m` away from going quiet.
  while IFS= read -r ref; do
    [[ -z "$ref" ]] && continue
    if git -C "$scope" cat-file -e "${ref}:.publication.toml" 2>/dev/null; then
      marker_found=true
      break
    fi
  done < <(git -C "$scope" for-each-ref --format='%(refname)' refs/heads/ 2>/dev/null)

  if [[ "$marker_found" == false ]]; then
    verdict_skip pre-push-installed 'repo not adopted -- no refs/heads/* branch carries .publication.toml'
    return
  fi

  # Marker present but refs/heads/main unresolvable is the hook's own BLOCK case; mirrored
  # here as a FAIL rather than skipped past.
  if ! git -C "$scope" rev-parse --quiet --verify refs/heads/main >/dev/null 2>&1; then
    verdict_fail pre-push-installed 'armed (a branch carries .publication.toml) but refs/heads/main does not resolve'
    return
  fi

  # Source preference: the CHECKED-OUT worktree copy when present -- this is what catches
  # "edited the hook, forgot to re-install" -- else the committed blob on refs/heads/dev.
  local source_kind source_sha source_is_worktree=false
  if [[ -f "$scope/git-hooks/pre-push" ]]; then
    source_kind="the worktree git-hooks/pre-push"
    source_sha="$(sha256_hex < "$scope/git-hooks/pre-push" 2>/dev/null)"
    # Recorded as its own flag, not re-derived by matching $source_kind's prose downstream:
    # the checkout-artifact test below is only meaningful against the WORKTREE copy, and a
    # string compare against a message would drift the moment that message is reworded.
    source_is_worktree=true
  elif git -C "$scope" cat-file -e 'refs/heads/dev:git-hooks/pre-push' 2>/dev/null; then
    source_kind='refs/heads/dev:git-hooks/pre-push'
    source_sha="$(git -C "$scope" cat-file blob 'refs/heads/dev:git-hooks/pre-push' 2>/dev/null | sha256_hex)"
  else
    # A discovery matching nothing must not report success: neither source existing in an
    # adopted repo is a FAIL, never a SKIP.
    verdict_fail pre-push-installed 'no pre-push source found -- neither worktree git-hooks/pre-push nor refs/heads/dev:git-hooks/pre-push'
    return
  fi

  # `--path-format=absolute --git-path hooks`, NOT the bare form -- the bare form is
  # RELATIVE (.git/hooks from the root, ../.git/hooks from a subdir), so a naive comparison
  # against an absolute scope could never match. Resolved by asking git, never by
  # hand-constructing "$scope/.git/hooks": that is what makes a linked worktree resolve to
  # the MAIN repo's hooks dir here automatically, rather than FAILing on a path that (for a
  # worktree) is not even a directory. Re-resolved with `cd -P` on both the scope (before
  # asking) and the answer (after) because `--path-format=absolute` returns the PHYSICAL
  # path while main() resolves `$scope` with a logical `pwd` -- under a symlinked TMPDIR the
  # two would otherwise never agree.
  local scope_phys hooks_dir dest
  scope_phys="$(cd -P "$scope" 2>/dev/null && pwd)" || scope_phys="$scope"
  hooks_dir="$(cd "$scope_phys" 2>/dev/null \
    && git rev-parse --path-format=absolute --git-path hooks 2>/dev/null)"
  if [[ -n "$hooks_dir" && -d "$hooks_dir" ]]; then
    hooks_dir="$(cd -P "$hooks_dir" 2>/dev/null && pwd)" || true
  fi
  if [[ -z "$hooks_dir" ]]; then
    verdict_fail pre-push-installed 'could not resolve the git hooks directory'
    return
  fi
  dest="$hooks_dir/pre-push"

  if [[ ! -e "$dest" ]]; then
    verdict_fail pre-push-installed "not installed -- $dest does not exist"
    return
  fi
  if [[ -L "$dest" ]]; then
    verdict_fail pre-push-installed "$dest is a symlink -- must be a regular copy (see scripts/install-git-hooks.sh)"
    return
  fi
  if [[ ! -f "$dest" ]]; then
    verdict_fail pre-push-installed "$dest is not a regular file"
    return
  fi
  if [[ ! -x "$dest" ]]; then
    verdict_fail pre-push-installed "$dest is not executable -- git silently ignores a non-executable pre-push hook"
    return
  fi

  local dest_sha head_sha='' dev_sha=''
  dest_sha="$(sha256_hex < "$dest" 2>/dev/null)"
  if [[ -z "$source_sha" || "$dest_sha" != "$source_sha" ]]; then
    # A mismatch against the WORKTREE copy can be an artifact of WHICH COMMIT IS CHECKED OUT
    # rather than a stale installation. With W = the worktree hook, H = HEAD:git-hooks/pre-push,
    # I = the installed hook and D = refs/heads/dev:git-hooks/pre-push, it is an artifact iff
    #
    #     W == H   the worktree copy matches what its OWN commit tracks -- nobody edited it
    #     I == D   the installed hook matches what refs/heads/dev tracks -- the install is current
    #
    # and then the only thing out of step is the checkout. That is the shape per-brick auditing
    # produces on the publication path: audit.sh is run against a HISTORICAL tree, whose
    # git-hooks/pre-push is a superseded blob. The old verdict there was false, and its remedy
    # was worse than false -- re-running the installer from a historical tree installs THAT
    # tree's superseded hook as the live publication boundary.
    #
    # Measured across four fixtures, each moving a DIFFERENT conjunct so neither half is along
    # for the ride (rows rPP1-rPP4 of scripts/tests/test_audit.sh): historical checkout
    # yes/yes -> artifact; edited-in-worktree no/yes, committed-but-never-installed yes/no, and
    # a foreign installed hook yes/no -> all still real, all still FAIL.
    #
    # It needs NO ancestry between HEAD and dev (this repo's main and dev share none --
    # `git merge-base main dev` exits 1); it needs only refs/heads/dev to resolve, which the
    # fallback source above already assumes. And it needs no cooperation from the caller: a
    # flag would have to be passed by publish-brick.sh, which deliberately runs THE SCOPE'S OWN
    # audit.sh, and every historical copy predates the flag and exits 2 on an unknown one.
    #
    # Each blob is read behind its own `cat-file -e`. Piping a FAILED `cat-file blob` into
    # sha256_hex yields the digest of the empty string -- non-empty, and it would satisfy a
    # naive -n test on a tree that does not track the hook at all.
    if [[ "$source_is_worktree" == true ]]; then
      if git -C "$scope" cat-file -e 'HEAD:git-hooks/pre-push' 2>/dev/null; then
        head_sha="$(git -C "$scope" cat-file blob 'HEAD:git-hooks/pre-push' 2>/dev/null | sha256_hex)"
      fi
      if git -C "$scope" cat-file -e 'refs/heads/dev:git-hooks/pre-push' 2>/dev/null; then
        dev_sha="$(git -C "$scope" cat-file blob 'refs/heads/dev:git-hooks/pre-push' 2>/dev/null | sha256_hex)"
      fi
    fi
    # SKIP, not PASS: the source comparison genuinely did not run, and a PASS verdict carries no
    # reason line -- this file's PASS helper takes a check name and nothing else -- so a PASS
    # here would suppress the mismatch SILENTLY. That helper is deliberately not named in this
    # comment: scripts/tests/test_audit_name_parity.sh derives each check's verdict name by
    # matching `verdict_<kind>` followed by a word, and it read the prose mention as a SECOND
    # verdict name for this check, failing nine of its rows.
    #
    # The narrowing is deliberate and only this arm is affected -- the not-installed, symlink,
    # not-regular and not-executable arms above judge the LIVE hooks directory, which is the
    # same real directory whatever commit is checked out, and they must keep firing during a
    # historical audit, because that audit runs at exactly the moment before real pushes.
    #
    # Accepted residual: a checkout whose HEAD is a FEATURE BRANCH that changed the hook also
    # reads as an artifact. Today's advice in that case is to install an unlanded branch's hook
    # as the live boundary, which is the counsel this change exists to stop; and once the branch
    # lands the per-brick audit still catches a stale install, because D moves with dev.
    if [[ -n "$head_sha" && -n "$dev_sha" \
          && "$source_sha" == "$head_sha" && "$dest_sha" == "$dev_sha" ]]; then
      verdict_skip pre-push-installed "source comparison not applicable -- the worktree copy matches HEAD and $dest matches refs/heads/dev:git-hooks/pre-push, so the mismatch is an artifact of the checked-out commit, not a stale install; the installed boundary is current -- do NOT install this tree's git-hooks/pre-push"
      return
    fi
    verdict_fail pre-push-installed "$dest does not match $source_kind (sha256 mismatch) -- establish WHICH copy is current first, by comparing both against refs/heads/dev:git-hooks/pre-push; re-run scripts/install-git-hooks.sh only from a tree that carries the current hook, and never install a historical tree's hook on this check's advice"
    return
  fi

  verdict_pass pre-push-installed
}

check_tests() {
  local scope="$1" ran=false detail="" unprovable="" sh_list py_list t out rc note py_count py_why
  sh_list="$(git -C "$scope" ls-files -- 'scripts/tests/test_*.sh' 2>/dev/null)"
  while IFS= read -r t; do
    [[ -z "$t" ]] && continue
    ran=true
    out="$("$scope/$t" 2>&1)"; rc=$?
    if [[ "$rc" -ne 0 ]]; then
      # Per-suite, not per-run: the directory existing does not mean THIS suite's write landed,
      # and claiming preservation that did not happen is worse than admitting it did not.
      if tests_artifact_write "$scope" "$t" "$out"; then note=""; else note=' (full output NOT preserved)'; fi
      detail="${detail}${t} exited ${rc}:${note}"$'\n'"$(tests_excerpt "$out")"$'\n'
    fi
  done <<< "$sh_list"

  # `:(glob)` is load-bearing. A plain `*test_*.py` pathspec is wildmatch WITHOUT WM_PATHNAME,
  # so `*` crosses `/` and `test_` matches anywhere in the PATH: `src/latest_run.py` matches
  # (la-test_-run). While that only decided whether pytest was INVOKED it was harmless; now that
  # it decides a FAIL, an over-match is a false block on every adopting repo. `:(glob)**/` still
  # matches at any depth, top level included — verified against both.
  py_list="$(git -C "$scope" ls-files -- ':(glob)**/test_*.py' 2>/dev/null)"
  # APPLICABILITY and EXECUTION are separate questions, and must not share one flag. With a
  # single `ran`, a passing shell suite above laundered this family's silence into `PASS tests`
  # — the sweep reporting a clean result for suites it never ran. Measured; that was the defect.
  if [[ -n "$py_list" ]]; then
    if python3 -m pytest --version >/dev/null 2>&1; then
      ran=true
      out="$(cd "$scope" && python3 -m pytest -q 2>&1)"; rc=$?
      if [[ "$rc" -ne 0 ]]; then
        if tests_artifact_write "$scope" pytest "$out"; then note=""; else note=' (full output NOT preserved)'; fi
        detail="${detail}pytest exited ${rc}:${note}"$'\n'"$(tests_excerpt "$out")"$'\n'
      fi
    else
      # The files existing is what establishes applicability, so a missing runner here is
      # INSTRUMENT FAILURE, not inapplicability — the one case where `SKIP` would be a lie.
      py_count="$(printf '%s\n' "$py_list" | grep -c . || true)"
      # Quote the runner's OWN words rather than naming a cause. The predicate is "`-m pytest`
      # would not start", which is equally satisfied by pytest being absent, by python3 being
      # absent, and by a plugin failing to import — so a fixed "pytest is not installed" names
      # the wrong remedy in two of the three, on a fail-closed gate whose message is the only
      # thing the operator acts on. Report the measurement; do not infer between hypotheses.
      py_why="$(python3 -m pytest --version 2>&1 | tail -1)"
      [[ -n "$py_why" ]] || py_why='python3 -m pytest is not runnable'
      unprovable="${py_count} test_*.py file(s) present but the runner could not start: ${py_why}"
    fi
  fi

  # Checked BEFORE `ran`, which speaks only for the families that did execute — consulting it
  # first is exactly what let one family hide behind another. Emitted through `verdict_fail` and
  # never a bare `printf`: only `verdict_fail` increments `fail_count`, and `fail_count` is the
  # sole input to the rc and to the RESULT line a publish gate allowlists. A hand-rolled printf
  # here would look like a failure and still exit 0 — this defect wearing the fix's clothes.
  if [[ -n "$unprovable" ]]; then
    if [[ -n "$detail" ]]; then
      detail="${detail}${unprovable}"$'\n'
    else
      verdict_fail tests "unprovable: $unprovable"
      return
    fi
  fi

  if [[ "$ran" == false ]]; then
    verdict_skip tests 'no scripts/tests/test_*.sh or test_*.py found'
    return
  fi
  if [[ -n "$detail" ]]; then
    verdict_fail tests 'test suite failure(s)'
    # Printed BEFORE the detail: it is the one line whose loss would make the rest pointless.
    if [[ -n "$tests_artifact_root" ]]; then
      printf '  full output: %s\n' "$tests_artifact_root"
    else
      printf '  full output: (unavailable — could not create an artifact directory)\n'
    fi
    printf '%s\n' "${detail%$'\n'}" | sed 's/^/  /'
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
  local root_now eff_root after_status mod_status

  # An absent root is NOT a reason to skip: a suite that CREATES the config root from nothing
  # is itself an outside write, and the SKIP this replaces waved exactly that through. So
  # re-resolve — but ONLY when it was absent at launch. An unconditional re-resolve would fall
  # back to the operator's real config root in unit rows that deliberately run with
  # AUDIT_HERMETIC_ROOT unset, silently watching live production instead of the fixture.
  root_now=""
  if [[ -z "$root" ]]; then
    root_now="$(hermetic_config_root)" || root_now=""
  fi
  eff_root="${root:-$root_now}"

  # Containment is judged against whichever root is in play, and BEFORE the absent-root
  # branches below — otherwise a root created inside the scope earns a misattributed
  # outside-write FAIL instead of the SKIP that says a sibling check already covers it.
  # Both sides must be PHYSICAL. main() resolves the scope with a plain `pwd` (logical), while
  # the root is resolved with `cd -P` — so under a symlinked TMPDIR the scope reads `/tmp/…`
  # and the root `/private/tmp/…`, and the containment test below could never match. Measured:
  # this silently skipped the SKIP on every macOS default, and only a fixture built under
  # `mktemp -d` exposed it.
  if [[ -n "$eff_root" ]]; then
    scope_phys="$(cd -P "$scope" 2>/dev/null && pwd)" || scope_phys="$scope"
    if [[ "$eff_root" == "$scope_phys" || "$eff_root" == "$scope_phys"/* ]]; then
      verdict_skip hermetic-outside 'config root lies inside the scope — hermetic covers it'
      return
    fi
  fi

  # Absent at launch and still absent: nothing existed to be written to and nothing was
  # created, so the property is vacuously true — and VERIFIED so, which a SKIP never was.
  # Returning here is also what keeps the zero-files FAIL below honest: that check's subject
  # is a root that EXISTS but traverses nothing (the symlink trap), never a legitimately
  # absent one, which would otherwise false-block every host without a config root.
  if [[ -z "$root" ]] && [[ -z "$root_now" ]]; then
    # An empty resolution means one of THREE things, and only one of them is vacuously true:
    # no such path, a path that is not a directory, or a directory that cannot be traversed.
    # `hermetic_config_root` collapses all three, so gating the PASS on resolution alone hands
    # a POSITIVE verdict to a probe that measured nothing — this check's whole subject, rebuilt
    # inside its own fix. Worse than the SKIP it replaced, which at least reads as a coverage
    # gap. `-e` is the discriminator: it needs only stat on the path, so it stays true for an
    # untraversable directory and for a regular file, and is false for genuine absence.
    if [[ -e "$(hermetic_config_root_raw)" ]]; then
      verdict_fail hermetic-outside \
        'unprovable: the config root exists but could not be resolved, so nothing was watched'
      return
    fi
    verdict_pass hermetic-outside
    return
  fi
  if [[ -z "$root" ]]; then
    verdict_fail hermetic-outside "the suite created the config root at $root_now"
    return
  fi

  # Reached only when the root existed at launch, so the marker was actually ATTEMPTED.
  # Gating on that ordering rather than re-testing the root is what stops the absent-root
  # path failing on a marker nobody ever tried to create.
  if [[ -z "$marker" ]]; then
    verdict_fail hermetic-outside 'unprovable: could not create a timestamp marker, so modifications are unmeasurable'
    return
  fi
  if [[ "$before_status" -ne 0 ]]; then
    verdict_fail hermetic-outside 'unprovable: could not enumerate the config root BEFORE the suite ran'
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
  # Status captured here too, not only for the BEFORE snapshot. A truncated AFTER is the
  # quieter direction of the same defect: the comparison then reports files as `vanished`
  # that are merely unseen, and — worse — a real write into a subtree that became
  # unenumerable reads as no change at all, i.e. a clean PASS. `set -o pipefail` (line 2) is
  # what makes this `$?` the enumerator's rather than `sort`'s; do not remove it.
  after="$(hermetic_outside_files "$roots" | LC_ALL=C sort)"; after_status=$?
  if [[ "$after_status" -ne 0 ]]; then
    verdict_fail hermetic-outside 'unprovable: could not enumerate the config root AFTER the suite ran'
    return
  fi

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
  modified="$(hermetic_outside_files "$roots" -newer "$marker" | LC_ALL=C sort)"; mod_status=$?
  if [[ "$mod_status" -ne 0 ]]; then
    verdict_fail hermetic-outside 'unprovable: could not enumerate modifications under the config root'
    return
  fi

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
  local hermetic_root="" hermetic_out_before="" hermetic_out_status=1

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
  check_env_claims "$scope"
  check_exec_bit "$scope"
  check_json "$scope"
  check_toml "$scope"
  check_sync_docs "$scope"
  check_mutation_anchors "$scope"
  check_pre_push_installed "$scope"
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
    # Removed here rather than left to the trap, so the file's lifetime ends with the check that
    # needed it. Clearing the handle keeps ownership single: a non-empty hermetic_marker means a
    # file we created is still on disk, which is exactly what the EXIT trap's backstop tests.
    if [[ -n "$hermetic_marker" ]]; then
      rm -f "$hermetic_marker"
      hermetic_marker=""
    fi
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

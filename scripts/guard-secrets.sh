#!/usr/bin/env bash
set -euo pipefail

# Script: guard-secrets.sh
# Purpose: Global PreToolUse hook — deny reading/editing secret files (.env*, keys, pem)
# Usage: PreToolUse(Read|Edit|Write|MultiEdit|Grep|NotebookEdit) hook — reads JSON on stdin
#
# Exit codes:
#   0 — allow (not a secret file, or any uncertainty: fail-safe)
#   2 — deny: the target matches the universal list or a project glob, a content-mode directory Grep
#       would read one, or (the deliberate fail-closed enumeration verdict) the directory enumeration
#       could not be completed: "UNVERIFIED" (bound hit, scan deadline, or enumerator failed to run),
#       or (a second deliberate exit 2) a path-like field is over its byte cap ("oversize": 65,536 bytes
#       for the target and cwd, 4,096 for glob and type; a message that never echoes the field)
#
# Enforces CONTRIBUTING's never-commit secrets (.env, *.key, *.pem) — plus .env variants,
# *.env, and SSH private keys — at ACCESS time, before the tool runs. The universal deny list is
# deliberately tight and basename-matched. The target is the path the Grep tool itself computes
# before it opens anything: trimmed (the JavaScript String.trim set, done in linear time inside jq), '~' expanded, then lexically
# collapsed ('.', '..'; a relative path resolves against the payload cwd). That tool-computed path
# is what is name-matched and what a directory Grep is enumerated from. The raw path (untrimmed,
# uncollapsed) is also kept: its leaf is symlink-checked, and when it names a DIFFERENT existing
# directory (a '..' after a symlinked directory resolves physically) it is enumerated as a second
# root. The tool does not read that second root, so it can only over-block.
#
# Directory Grep: a content-mode Grep of a directory is enumerated (rg --files with the Grep call's
# glob and type, plus --no-config, --hidden and the VCS excludes, which are the guard's own choices;
# or, when rg is absent, find -H, which applies neither glob nor type and is blind to ignore rules)
# and denied on any secret in the listing. The walk's bound is
# GUARD_SECRETS_WALK_SECS (default 10, clamped to 20; invalid values mean 10), counted from the start of
# the walk; SECONDS ticks on wall-clock boundaries, so that bound lies between N and N+1 seconds. An
# absolute ceiling, counted from the start of the script, sits over it so the whole hook ends well inside
# the harness's 30 s whatever came before the walk: the walk is stopped 19 s after script start (so at most
# about 19 s even at the maximum setting), and the 3 s grace scan of a partial listing that follows ends
# the hook at about 22 s. Past the bound the call is denied as UNVERIFIED.
#
# Project globs: a project may ADD globs, one per line, in $CLAUDE_PROJECT_DIR/.claude/secret-globs
# (else the payload cwd): DATA only, never sourced. Blank lines and # comment lines are ignored,
# surrounding whitespace (CR included) is stripped. Matching is bash [[ == ]] semantics, so '*'
# matches '/', case-insensitively, against the relative path, the basename and the full path (and
# "$rel/" for a directory Grep). The file is capped at 65,536 bytes, 1000 lines and 512 bytes a
# line; a glob with more than 3 stars is ignored. It is a union with the universal list, outranks
# the universal exemptions (.env.example, *.pub), and runs only after the universal check.
#
# Known limits (accepted):
#   - Bash tool: a cat/grep via the Bash tool still reaches the file; only file tools are guarded.
#   - Pattern oracle: a files_with_matches or count Grep of a directory is allowed, so crafted
#     patterns can learn whether a secret file matches.
#   - Names: matching is basename-based (a file INSIDE a directory named .env is matched only by its
#     own name); *.key also matches Keynote decks and *.pem public certs. Percent-encodings
#     (%2e%2e) are not decoded, as the file tools do not decode them.
#   - Symlinks ARE resolved at the leaf (raw and tool-computed path); a symlink met inside a directory
#     tree is not followed by rg or the Grep tool, nor enumerated. A Write target whose parent does
#     not exist has no physical view for project globs.
#   - The enumeration is a PREDICTION of what the tool reads, pinned to Claude Code 2.1.312's flags
#     and glob tokenization; the guard runs the PATH rg, the tool a bundled ripgrep 14.1.1, so type
#     definitions and ignore handling can skew in either direction. TOCTOU: a secret created after
#     the check is not seen.
#   - SIGPIPE is ignored (trap '' PIPE) so a broken stderr cannot turn a deny into exit 141; children
#     (jq, rg, find, sed) inherit that disposition and see EPIPE write errors instead of dying silently.
#   - Enumerator that ignores TERM or sits in uninterruptible I/O: the watchdog's kill is a plain
#     TERM and `wait` blocks until the enumerator exits, so the hook can overrun its registered
#     timeout, which the harness treats as an ALLOW. The EXIT trap also sends TERM and cannot do more.
#   - jq absent: the sed fallback extracts a file path only: the universal and project layers still
#     judge an absolute target, but the directory logic is skipped.
#     It does not trim either, so trailing padding on a secret name passes there (as in the base guard).
#   - Padded payloads are bounded: the path-like fields (target and cwd) were capped at 65,536 bytes and
#     glob and type at 4,096 before any processing, and over a cap the call is denied with an explicit
#     message (deny_oversize; a second deliberate exit 2 beside UNVERIFIED).
#     Content, old_string, new_string, pattern, output_mode and tool_name are not capped (a 50 MB Write is
#     judged by its target alone; output_mode and tool_name are reduced to short constants inside jq, but
#     arrive uncapped). Within the caps the measured worst end-to-end cost was about 4 s on /bin/bash 3.2
#     (a Read of a 65,536-byte path of slashes naming a symlink to .env: 4.0 s, 4.1 s with the cwd padded
#     too; 2.8 s and 3.0 s on bash 5; one review measured 6.4 s under load). A NUL inside a value is
#     removed, not a truncation, so a name holding one is judged as if it did not. With jq absent the sed
#     fallback applies neither the cap nor the trim.
#   - Fail-open on purpose: malformed payload, missing keys, absent tool_name, jq absent (directory
#     logic skipped), an unreadable/absent globs file, glob/type shapes the guard cannot parse (widened, not skipped),
#     and rg exit 2 with a partial or post-rerun-empty listing.
#   - Project globs are matched with [[ == ]], where extglob syntax on the right-hand side is
#     honoured whatever the shopt, so a line using !( @( +( ?( or *( is reinterpreted (!(x) matched
#     every path in bash 3.2 and 5, while a glob meant to match a literal name containing a+(b) does
#     NOT match that name: it can under-match its own text).
#   - bash 3.2 (/bin/bash): a failing multi-star match is cubic in the name length. For a content
#     directory Grep the shared deadline answers UNVERIFIED; where no enumeration runs (a Read, a
#     files_with_matches Grep) there is no deadline, so a hostile globs file plus a long path can
#     push that call's project check past the hook timeout (an allow of the project layer only; the
#     universal check has already decided).

# A write to a closed stderr pipe must not kill the hook by SIGPIPE (exit 141 reads as an ALLOW): ignore
# the signal, so the write fails with EPIPE instead and every deny still ends in its explicit `exit 2`.
# Set BEFORE the contract check so the argv refusal survives too. Children inherit the disposition.
trap '' PIPE

# HOOK CONTRACT: the target arrives as a JSON payload on stdin; argv is ignored. Refuse the two
# invocations this cannot serve, because each otherwise reads as SUCCESS — with argv and stdin at
# EOF it exits 0 having examined nothing, and with a terminal stdin it blocks forever.
if [ "$#" -gt 0 ] || [ -t 0 ]; then
  printf '%s\n' \
    "$(basename "$0") is a Claude Code hook: it reads a JSON payload on stdin and ignores arguments." \
    "Running it with filenames examines nothing. See scripts/HOOKS.md for the payload form." >&2 || true
  exit 2
fi

input=$(cat)
# Extraction: ONE jq call emits the fields in a fixed order, each terminated by a NUL byte (`jq -j`, which is
# jq 1.6-compatible), and bash reads them one at a time with `IFS= read -r -d ''`. Nothing is escaped or
# restored, so a newline (or U+001E, U+001F) inside a value is just a character and padding costs linear
# time. Every string field passes `nn` first, which removes any NUL inside the value (a NUL would end the
# field early on /bin/bash 3.2); non-strings become empty. The first field is a status: `ok`, or
# `oversize:<field>:<bytes>:<cap>` when a path-like field is over its byte cap, measured on the RAW value
# before the trim or any other step; bash then denies at once (deny_oversize), before any other work.
tool_name=""; cwd=""; file_path=""; tool_path=""; output_mode=""; grep_glob=""; grep_type=""
TYPE_ARG=(); GLOB_ARGS=(); PGLOBS=()
# BEGIN hook_ceiling
# The whole hook must end well before the harness kills it at 30 s (a timeout is an allow). SECONDS counts
# from the start of the shell (a tick can come up to a second early, never late), so this is absolute: the
# walk's deadline never passes GS_WALK_CEIL seconds after script start, whatever GUARD_SECRETS_WALK_SECS says
# and however much slow work came before the walk. The 3 s grace scan of a partial listing then ends the hook
# at about 22 s. (A longer pre-walk than the caps allow, over 19 s, would push the grace scan later; no
# payload within the field caps comes near that.)
GS_WALK_CEIL=19
# END hook_ceiling
# BEGIN field_caps
GS_PATH_CAP=65536
GS_GLOB_CAP=4096
# END field_caps
# The JavaScript String.trim set (25 members: TAB LF VT FF CR SPACE, U+00A0, U+1680, U+2000-U+200A, U+2028,
# U+2029, U+202F, U+205F, U+3000, U+FEFF) is trimmed from both ends of the target INSIDE jq, in linear time:
# the class uses jq STRING escapes (\u00a0; a regex escape \\u00a0 silently strips only the ASCII members), the
# start-anchored sub (\A, not ^, which is a line anchor here) runs once on the string and once on its reverse
# (an unanchored trailing alternative is quadratic on a long middle run), and explode/reverse/implode is linear.
# BEGIN jq_trim
JQ_TRIM='def ws: "[\t\n\u000b\f\r \u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000\ufeff]+";
  def ltrim: sub("\\A" + ws; "");
  def rev: explode | reverse | implode;
  def trim: if type == "string" then ltrim | rev | ltrim | rev else . end;'
# END jq_trim
# deny_oversize <field> <bytes> <cap> — a path-like field over its byte cap. Never echoes the field.
deny_oversize() {
  local why="no file tool can open a path that long (PATH_MAX is 1024 on macOS, 4096 on Linux)."
  case "$1" in
    glob) why="a glob that long is not a pattern a Grep takes." ;;
    type) why="a type that long is not a file type a Grep takes." ;;
  esac
  printf 'guard-secrets: BLOCKED %s field of %s bytes (cap %s): %s\n' "$1" "$2" "$3" "$why" >&2 || true
  case "$1" in
    glob) printf '  For a Grep, keep glob to the patterns you need.\n' >&2 || true ;;
    type) printf '  For a Grep, use one file type name.\n' >&2 || true ;;
    *) printf '  Send the path without padding.\n' >&2 || true ;;
  esac
  printf '  (User: to allow this deliberately, remove the guard-secrets PreToolUse entry from ~/.claude/settings.json and restart.)\n' >&2 || true
  exit 2
}
if command -v jq >/dev/null 2>&1; then
  # `.path` covers content-returning file tools (Grep) that key the target as `path`, not
  # `file_path`; without it a Grep of a secret file dumps its contents past this gate.
  # glob and type: any value holding a character outside printable ASCII is replaced by the empty
  # string (omitted, so the enumeration widens); \A and \z, not ^ and $, which are line anchors here.
  status=""
  { IFS= read -r -d '' status; IFS= read -r -d '' tool_name; IFS= read -r -d '' file_path; IFS= read -r -d '' cwd
    IFS= read -r -d '' output_mode; IFS= read -r -d '' grep_glob; IFS= read -r -d '' grep_type; IFS= read -r -d '' tool_path
  } < <(printf '%s' "$input" | jq -j --argjson pcap "$GS_PATH_CAP" --argjson gcap "$GS_GLOB_CAP" "$JQ_TRIM"'
    def blen: if type == "string" then utf8bytelength else 0 end;
    # The NUL strip is `add`, not `join("")`: the same result for every input (empty and all-NUL strings give
    # "", "Gr\0EP" gives "GrEP"), but about a quarter of the cost on a payload of millions of NULs; gsub is slower still.
    def nn: if type == "string" then split("\u0000") | add // "" else "" end;
    # pick($w): tool_name and output_mode are only ever COMPARED (to Grep and to content, case-insensitively once
    # nocasematch is on), so they are decided here and a payload-controlled string of any size reaches bash as a
    # short constant: the value itself when it equals $w ignoring ASCII case after NULs are removed, else empty.
    # ascii_downcase is slow on a huge string (23 s at 40 MB), so it only ever sees a value of 16 bytes or less.
    def pick($w): if type != "string" then ""
      else (split("\u0000") | add // "") | if utf8bytelength > 16 then "" elif ascii_downcase == $w then . else "" end end;
    def asc: if type == "string" and test("\\A[ -~]*\\z") then . else "" end;
    def tp: (.tool_input.path // .tool_input.file_path // .tool_input.notebook_path // "");
    def over: [ ["path", (tp | blen), $pcap], ["cwd", (.cwd | blen), $pcap],
                ["glob", (.tool_input.glob | blen), $gcap], ["type", (.tool_input.type | blen), $gcap] ]
              | map(select(.[1] > .[2])) | first // null;
    ( if over then ["oversize:" + over[0] + ":" + (over[1] | tostring) + ":" + (over[2] | tostring), "", "", "", "", "", "", ""]
      else ["ok", (.tool_name | pick("grep")), (tp | nn), (.cwd | nn), (.tool_input.output_mode | pick("content")),
            (.tool_input.glob | asc), (.tool_input.type | asc), (tp | nn | trim)] end )
    | map(. + "\u0000") | add' 2>/dev/null) || true
  case "$status" in
    oversize:*) IFS=: read -r _ over_field over_len over_cap <<< "$status"; deny_oversize "$over_field" "$over_len" "$over_cap" ;;
  esac
else
  file_path=$(printf '%s' "$input" | sed -n 's/.*"\(file_path\|path\|notebook_path\)"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\2/p' | head -1) || file_path=""
  tool_path="$file_path"   # the sed fallback does not trim (part of its documented narrower scope)
fi
# A Grep with no target searches its working directory: the payload cwd, else the hook's own $PWD.
if [[ -z "$tool_path" && "$tool_name" == "Grep" ]]; then file_path="${cwd:-$PWD}"; tool_path="$file_path"; fi
[[ -n "$tool_path" ]] || exit 0

# Case-insensitive matching: on macOS's default APFS, .ENV resolves to the same file as .env.
shopt -s nocasematch

# secret_name <basename> — 0 when the name is on the deny list (and not exempted).
secret_name() {
  case "$1" in
    # Exemptions first: documented placeholders and public halves are always fine.
    .env.example|.env.sample|.env.template|.env.dist) return 1 ;;
    *.pub) return 1 ;;
    # The deny list: never-commit secrets, matched at access time.
    .env|.env.*|*.env|*.key|*.pem|id_rsa|id_rsa.*|id_ecdsa|id_ecdsa.*|id_ed25519|id_ed25519.*|id_dsa|id_dsa.*)
      return 0 ;;
  esac
  return 1
}

# normalize_path <path> — lexically collapse '.', '..' and repeated '/'; no filesystem access (a
# Write target may not exist). Absolute stays absolute; '..' at the root stays at the root; a
# relative path keeps leading '..'.
# BEGIN normalize_path
normalize_path() {
  local path="$1" seg abs=0 n=0 left joined=""
  local -a parts=() out=()
  if [[ "$path" == /* ]]; then abs=1; fi
  # One split, then a stack in an indexed array; the remainder is never rewritten and the fields are walked
  # with `for` (an indexed read of a big array walks it from the start on bash 3.2, which is quadratic).
  # The here-string's own newline lands in the LAST field (after the appended '/'), so that field is
  # ignored (`left` counts down to it); -d '' keeps newlines inside segments. Measured linear on bash 5
  # (0.6/0.9/1.8 s) and /bin/bash 3.2 (0.7/1.4/2.8 s) at 64/128/256 KB; no length cap, and the suite pins it.
  IFS=/ read -r -d '' -a parts <<< "$path/" || true
  left=${#parts[@]}
  for seg in ${parts[@]+"${parts[@]}"}; do
    left=$((left - 1))
    if [[ "$left" -eq 0 ]]; then break; fi
    case "$seg" in
      ''|'.') continue ;;
      '..')
        if [[ "$n" -gt 0 && "${out[$((n - 1))]}" != '..' ]]; then
          n=$((n - 1))
        elif [[ "$abs" -eq 1 ]]; then
          :
        else
          out[n]='..'; n=$((n + 1))
        fi ;;
      *) out[n]="$seg"; n=$((n + 1)) ;;
    esac
  done
  if [[ "$n" -gt 0 ]]; then
    out=("${out[@]:0:$n}")
    local IFS=/
    joined="${out[*]}"
  fi
  if [[ "$abs" -eq 1 ]]; then printf '/%s' "$joined"; else printf '%s' "$joined"; fi
}
# END normalize_path

# resolve_raw <path> <cwd> — leading ~/ expanded to $HOME (lexically); relative paths made
# absolute against the payload cwd, else $PWD. NOT collapsed.
resolve_raw() {
  local p="$1" base="${2:-$PWD}"
  # shellcheck disable=SC2088 # "~" is expanded by hand here
  case "$p" in
    '~')   if [[ -n "${HOME:-}" ]]; then p="$HOME"; fi ;;
    '~/'*) if [[ -n "${HOME:-}" ]]; then p="$HOME/${p#\~/}"; fi ;;
  esac
  case "$p" in /*) ;; *) p="$base/$p" ;; esac
  printf '%s' "$p"
}

# universal_hit <basename> — 0 when the name is a universal secret.
universal_hit() { secret_name "$1"; }

# Project secret globs: DATA from <root>/.claude/secret-globs, only ever the right-hand side of
# [[ == ]] (nocasematch on, nothing evaluated; extglob syntax on that side is honoured regardless of
# the shopt, so such a glob is reinterpreted).
PG_DEADLINE=0; PG_OVERRUN=0; PGLOB_FILE=""; PGLOB_MATCH=""; PHYS=""

# load_project_globs — fills PGLOBS and PGLOB_FILE from $root/.claude/secret-globs when that is a
# regular readable file: at most 65,536 bytes and 1000 lines, lines over 512 bytes dropped, surrounding
# whitespace (CR included) stripped, blanks and # lines dropped, globs with more than 3 stars ignored
# (a failing many-star match is cubic on bash 3.2). Unreadable or absent means no layer.
load_project_globs() {
  PGLOBS=(); PGLOB_FILE=""
  if [[ -z "$root" ]]; then return 0; fi
  local f="$root/.claude/secret-globs" line stars n=0 LC_ALL=C
  if [[ ! -f "$f" || ! -r "$f" ]]; then return 0; fi
  while IFS= read -r line || [[ -n "$line" ]]; do
    n=$((n + 1))
    if (( n > 1000 )); then break; fi
    if (( ${#line} > 512 )); then continue; fi
    line="${line#"${line%%[![:space:]]*}"}"
    line="${line%"${line##*[![:space:]]}"}"
    if [[ -z "$line" || "$line" == '#'* ]]; then continue; fi
    stars="${line//[!*]/}"
    if (( ${#stars} > 3 )); then continue; fi
    PGLOBS+=("$line")
  done < <(head -c 65536 -- "$f" 2>/dev/null)
  if [[ ${#PGLOBS[@]} -gt 0 ]]; then PGLOB_FILE="$f"; fi
  return 0
}

# project_hit <rel> <full> — 0 when a project glob matches the relative path, the basename or the full
# path (sets PGLOB_MATCH). Between globs it compares SECONDS with the shared deadline (PG_DEADLINE, 0 =
# none); an overrun sets PG_OVERRUN and returns 1.
project_hit() {
  local g
  for g in ${PGLOBS[@]+"${PGLOBS[@]}"}; do
    if (( PG_DEADLINE > 0 && SECONDS >= PG_DEADLINE )); then PG_OVERRUN=1; return 1; fi
    # shellcheck disable=SC2053 # the unquoted right-hand side IS the glob
    if [[ "$1" == $g || "${2##*/}" == $g || "$2" == $g ]]; then PGLOB_MATCH="$g"; return 0; fi
  done
  return 1
}

# project_hit_dir <rel> — the "$rel/" form, so secrets/** covers a Grep whose path IS that directory.
project_hit_dir() {
  local g
  for g in ${PGLOBS[@]+"${PGLOBS[@]}"}; do
    if (( PG_DEADLINE > 0 && SECONDS >= PG_DEADLINE )); then PG_OVERRUN=1; return 1; fi
    # shellcheck disable=SC2053 # the unquoted right-hand side IS the glob
    if [[ "$1/" == $g ]]; then PGLOB_MATCH="$g"; return 0; fi
  done
  return 1
}

# path_base <path> — sets BASE to the part after the last '/' (what `${1##*/}` gives), in linear time: the
# longest-prefix removal is retried from the end of the string and is quadratic on a padded path. One split
# of "$1/" (the appended slash makes the last field the here-string's own newline), then a for-walk that
# keeps the previous field. Used on every string that can carry padding; real bounded names keep `##*/`.
path_base() {
  local -a f=()
  local prev="" cur left
  IFS=/ read -r -d '' -a f <<< "$1/" || true
  left=${#f[@]}
  for cur in ${f[@]+"${f[@]}"}; do
    left=$((left - 1))
    if [[ "$left" -eq 0 ]]; then break; fi
    prev="$cur"
  done
  BASE="$prev"
}

# phys_of <raw> — sets PHYS: the physical path of an existing directory, else the physical parent plus
# the leaf name, else empty. One subshell per call; called once per hook invocation.
phys_of() {
  PHYS=""
  local d b
  if [[ -d "$1" ]]; then PHYS="$(cd -P -- "$1" 2>/dev/null && pwd -P)" || PHYS=""; return 0; fi
  path_base "$1"; b="$BASE"
  if [[ "$1" == */* ]]; then d="${1:0:${#1}-${#b}-1}"; else d="$1"; fi
  if [[ -n "$d" && -d "$d" ]]; then
    d="$(cd -P -- "$d" 2>/dev/null && pwd -P)" || d=""
    if [[ -n "$d" ]]; then PHYS="$d/$b"; fi
  fi
  return 0
}

# project_target_hit <path> — the project check on one view of the target: rel, basename and full path,
# plus the "$rel/" form for an existing directory under Grep. 0 on a hit.
project_target_hit() {
  rel_set "$1"
  if project_hit "$REL" "$1"; then return 0; fi
  if [[ "$tool_name" == "Grep" && -d "$1" ]] && project_hit_dir "$REL"; then return 0; fi
  return 1
}

# BEGIN walk_secs
walk_secs() {
  local v="${GUARD_SECRETS_WALK_SECS:-10}"
  case "$v" in
    ''|*[!0-9]*) v=10 ;;
  esac
  if [ "${#v}" -gt 3 ]; then v=20; fi
  v=$((10#$v))
  if [ "$v" -lt 1 ]; then v=10; fi
  if [ "$v" -gt 20 ]; then v=20; fi
  printf '%s' "$v"
}
# END walk_secs

# glob_args <glob-string> — fills GLOB_ARGS, mirroring the Grep tool's own tokenization: split on
# whitespace; a token holding both { and } goes whole; any other token is split on commas with empty
# pieces dropped; each piece is one --glob=<piece> (the = form, so a leading - is not an option).
glob_args() {
  GLOB_ARGS=()
  local tok piece
  local -a toks=() pieces=()
  read -ra toks <<< "$1"
  for tok in ${toks[@]+"${toks[@]}"}; do
    if [[ "$tok" == *'{'* && "$tok" == *'}'* ]]; then
      GLOB_ARGS+=("--glob=$tok"); continue
    fi
    IFS=, read -ra pieces <<< "$tok"
    for piece in ${pieces[@]+"${pieces[@]}"}; do
      if [[ -n "$piece" ]]; then GLOB_ARGS+=("--glob=$piece"); fi
    done
  done
}

# run_bounded <out-file> <deadline-in-SECONDS> <cmd...> — sets BOUND_RC and BOUND_HIT (0/1).
# BOUND_HIT means ONLY "our watchdog fired" (the marker exists); the rc-143 half of the verdict is
# tested once, ahead of the status case, by the caller. The watchdog polls the parent's SECONDS clock
# and exits within 0.1 s of the enumerator finishing; the parent never signals it.
run_bounded() {
  local outfile="$1" deadline="$2" pid wd
  shift 2
  BOUND_RC=0; BOUND_HIT=0
  rm -f "$WALK_TMP/bound_hit"
  "$@" >"$outfile" 2>/dev/null &
  pid=$!
  ENUM_PID=$pid
  (
    while (( SECONDS < deadline )); do
      kill -0 "$pid" 2>/dev/null || exit 0
      sleep 0.1
    done
    { : >"$WALK_TMP/bound_hit"; } 2>/dev/null || true
    kill "$pid" 2>/dev/null || true
  ) >/dev/null 2>&1 &
  wd=$!
  wait "$pid" 2>/dev/null || BOUND_RC=$?
  wait "$wd" 2>/dev/null || true
  ENUM_PID=""
  if [[ -e "$WALK_TMP/bound_hit" ]]; then BOUND_HIT=1; fi
}

# rel_set <abs-path> — sets REL; NEVER prints (a printing helper costs a subshell per name).
rel_set() {
  REL="$1"
  if [[ -n "$root" && "$1" == "$root"/* ]]; then REL="${1#"$root"/}"
  elif [[ -n "$root_phys" && "$1" == "$root_phys"/* ]]; then REL="${1#"$root_phys"/}"; fi
}

# scan_universal <list-file> <deadline> — pass 1. rc 0 clean, 1 a secret (SCAN_HIT = its relative path), 3 deadline.
scan_universal() {
  local list="$1" deadline="$2" f n=0
  SCAN_HIT=""
  while IFS= read -r -d '' f; do
    n=$((n + 1))
    if (( n % 256 == 0 )) && (( SECONDS >= deadline )); then return 3; fi
    if universal_hit "${f##*/}"; then rel_set "$f"; SCAN_HIT="$REL"; return 1; fi
  done <"$list"
  return 0
}

# scan_project <list-file> <deadline> — pass 2, skipped when there are no project globs. Entries carry
# the prefix of their own root: the tool-computed root (PVT_ROOT, physical view PVT_PHYS) or the raw second
# root (PVR_ROOT, physical view PVR_PHYS); when a physical prefix exists, the entry's path under it is
# tested as well, for each root whose prefix matches (one root may lie inside the other). The prefixes are
# taken once, by the caller.
scan_project() {
  local list="$1" deadline="$2" f n=0 suffix
  SCAN_HIT=""
  if [[ ${#PGLOBS[@]} -eq 0 ]]; then return 0; fi
  while IFS= read -r -d '' f; do
    n=$((n + 1))
    if (( n % 256 == 0 )) && (( SECONDS >= deadline )); then return 3; fi
    rel_set "$f"
    if project_hit "$REL" "$f"; then SCAN_HIT="$REL"; return 1; fi
    if (( PG_OVERRUN )); then return 3; fi
    if [[ -n "$PVT_PHYS" && "$f" == "$PVT_ROOT"/* ]]; then
      suffix="${f#"$PVT_ROOT"/}"
      rel_set "$PVT_PHYS/$suffix"
      if project_hit "$REL" "$PVT_PHYS/$suffix"; then SCAN_HIT="$REL"; return 1; fi
      if (( PG_OVERRUN )); then return 3; fi
    fi
    if [[ -n "$PVR_PHYS" && "$f" == "$PVR_ROOT"/* ]]; then
      suffix="${f#"$PVR_ROOT"/}"
      rel_set "$PVR_PHYS/$suffix"
      if project_hit "$REL" "$PVR_PHYS/$suffix"; then SCAN_HIT="$REL"; return 1; fi
      if (( PG_OVERRUN )); then return 3; fi
    fi
  done <"$list"
  return 0
}

# classify — judge the finished run (reads list, tool_is_rg, TYPE_ARG) and set enum to hit | ok | retry |
# failed. An rg status outside the allowlist, or any find status but 0, is failed.
classify() {
  if [[ "$BOUND_RC" -eq 143 && "$BOUND_HIT" -eq 1 ]]; then
    enum=hit
  elif [[ "$tool_is_rg" -eq 1 ]]; then
    case "$BOUND_RC" in
      0|1) enum=ok ;;
      2)   if [[ ${#TYPE_ARG[@]} -gt 0 && ! -s "$list" ]]; then enum=retry; else enum=ok; fi ;;
      *)   enum=failed ;;
    esac
  else
    case "$BOUND_RC" in
      0)   enum=ok ;;
      *)   enum=failed ;;
    esac
  fi
}

# deny_unverified <reason> — the enumeration could not be completed: fail closed, exit 2.
deny_unverified() {
  printf 'guard-secrets: BLOCKED %s: UNVERIFIED (%s)\n' "$target" "$1" >&2 || true
  printf '  The guard could not list and scan what this Grep would read within the bound, so it cannot rule out a secret.\n' >&2 || true
  printf '  1. run the Grep again with output_mode "files_with_matches" (names only), then Read the file you actually need, if it is not a secret.\n' >&2 || true
  printf '  3. narrow path to a subdirectory that holds none.\n' >&2 || true
  printf '  (User: to allow this deliberately, remove the guard-secrets PreToolUse entry from ~/.claude/settings.json and restart.)\n' >&2 || true
  exit 2
}

# deny_file <what> [<line 2>] — a secret file: line 2 names the universal list unless a project glob matched.
deny_file() {
  printf 'guard-secrets: BLOCKED %s\n' "$1" >&2 || true
  printf '  %s\n' "${2:-Secret file (never-commit list: .env*, *.env, *.key, *.pem, SSH private keys).}" >&2 || true
  printf '  Do not read or edit it — describe shapes, not contents; the user can open it.\n' >&2 || true
  printf '  (User: to allow this deliberately, remove the guard-secrets PreToolUse entry\n' >&2 || true
  printf '  from ~/.claude/settings.json and restart.)\n' >&2 || true
  exit 2
}

# deny_dir <relative-path> — a secret lies in what this Grep would read.
deny_dir() {
  local first="guard-secrets: BLOCKED directory Grep would return secret content: $1"
  if [[ "$tool_is_rg" -eq 0 ]]; then first="$first (rg not found: ignore rules not applied)"; fi
  printf '%s\n' "$first" >&2 || true
  printf '  Retry with one of:\n' >&2 || true
  printf '  1. run the Grep again with output_mode "files_with_matches" (names only), then Read the file you actually need, if it is not a secret.\n' >&2 || true
  if [[ "$tool_is_rg" -eq 1 ]]; then
    printf '  2. add a glob or type that excludes the secret file(s).\n' >&2 || true
  fi
  printf '  3. narrow path to a subdirectory that holds none.\n' >&2 || true
  printf '  (User: to allow this deliberately, remove the guard-secrets PreToolUse entry from ~/.claude/settings.json and restart.)\n' >&2 || true
  exit 2
}

# raw_target: the payload path as sent (untrimmed, uncollapsed), made absolute. target: the path the
# Grep tool itself opens: trimmed, '~' expanded, relative paths resolved against cwd, then lexically
# collapsed. The tool does NOT resolve a '..' that follows a symlinked directory physically.
# Early universal basename check, before normalize_path: the basename of the payload path as sent FIRST (it
# reproduces the base guard's verdicts and is the only check left when jq is absent), then of its trimmed
# form (only it catches trailing padding). Linear basename extraction (path_base), never `##*/`.
path_base "$file_path"; early_raw="$BASE"
path_base "$tool_path"; early_tool="$BASE"
if secret_name "$early_raw" || secret_name "$early_tool"; then deny_file "$tool_path"; fi
raw_target="$(resolve_raw "$file_path" "$cwd")"
# raw_root: raw_target without ONE trailing slash. Written as a test plus a slice, never `${raw_target%/}`:
# that suffix removal is quadratic on a huge path with no trailing slash (measured: about 10 s at 256 KiB).
raw_root="$raw_target"
if [[ "$raw_target" == */ ]]; then raw_root="${raw_target:0:${#raw_target}-1}"; fi
target="$(normalize_path "$(resolve_raw "$tool_path" "$cwd")")"

deny=""
path_base "$target"; target_base="$BASE"
path_base "$raw_target"; raw_base="$BASE"
if universal_hit "$target_base"; then
  deny="$target"
elif universal_hit "$raw_base"; then
  deny="$raw_target"
else
  # Check BOTH views: the raw path (a '..' after a symlinked directory resolves physically there; the
  # tool does not read that, so this only over-blocks) and the tool-computed one (a nonexistent segment
  # before '..' leaves the raw path unresolvable).
  for p in "$raw_target" "$target"; do
    if [[ -L "$p" ]]; then
      # A symlink to a secret is the secret: deny by the resolved target's basename too.
      resolved=$(readlink -f -- "$p" 2>/dev/null || true)
      if [[ -n "$resolved" ]] && secret_name "${resolved##*/}"; then
        deny="$target -> $resolved"
        break
      fi
    fi
  done
fi

if [[ -n "$deny" ]]; then deny_file "$deny"; fi

# Project root and its physical form (the live config is reached through a symlink, so a path can
# lie under either spelling). `root` is normalized: no trailing slash.
root="${CLAUDE_PROJECT_DIR:-$cwd}"
root_phys=""
if [[ -n "$root" ]]; then
  root="$(normalize_path "$root")"
  root_phys="$(cd -P -- "$root" 2>/dev/null && pwd -P)" || root_phys=""
fi

# Step 5b: a content-mode Grep of a directory — enumerate what the tool would read, then scan it.
# Roots: the tool-computed path first; the raw path as a SECOND root when it names a different existing
# directory (over-blocks only: the tool does not read it).
walked=0; second_root=0; ROOTS=(); ENUM_PID=""; WALK_TMP=""
if [[ "$tool_name" == "Grep" && "$output_mode" == "content" ]]; then
  if [[ -d "$target" ]]; then ROOTS+=("$target"); fi
  if [[ "$raw_root" != "$target" && -d "$raw_target" ]]; then ROOTS+=("$raw_target"); second_root=1; fi
fi
if [[ ${#ROOTS[@]} -gt 0 ]]; then
  walked=1
  WALK_TMP="$(mktemp -d 2>/dev/null)" || deny_unverified "enumerator failed to run"
  trap 'if [[ -n "$ENUM_PID" ]]; then kill "$ENUM_PID" 2>/dev/null || true; fi; rm -rf -- "$WALK_TMP"' EXIT
  list="$WALK_TMP/list"
  N="$(walk_secs)"
  start=$SECONDS
  deadline=$((start + N + 1))
  if (( deadline > GS_WALK_CEIL )); then deadline=$GS_WALK_CEIL; fi
  glob_args "$grep_glob"
  if [[ -n "$grep_type" && "$grep_type" =~ ^[A-Za-z0-9_+-]+$ ]]; then TYPE_ARG=("--type=$grep_type"); fi
  tool_is_rg=0
  if command -v rg >/dev/null 2>&1; then tool_is_rg=1; fi
  enum=failed
  if [[ "$tool_is_rg" -eq 1 ]]; then
    run_bounded "$list" "$deadline" rg --no-config --files --null --hidden \
      --glob='!.git' --glob='!.svn' --glob='!.hg' --glob='!.bzr' --glob='!.jj' --glob='!.sl' \
      ${GLOB_ARGS[@]+"${GLOB_ARGS[@]}"} ${TYPE_ARG[@]+"${TYPE_ARG[@]}"} -- "${ROOTS[@]}"
    classify
    if [[ "$enum" == "retry" ]]; then
      # An unknown --type exits 2 with no output while the tool reads files: rerun without it (a widening).
      TYPE_ARG=()
      run_bounded "$list" "$deadline" rg --no-config --files --null --hidden \
        --glob='!.git' --glob='!.svn' --glob='!.hg' --glob='!.bzr' --glob='!.jj' --glob='!.sl' \
        ${GLOB_ARGS[@]+"${GLOB_ARGS[@]}"} -- "${ROOTS[@]}"
      classify
    fi
  else
    run_bounded "$list" "$deadline" find -H "${ROOTS[@]}" \
      \( -name .git -o -name .svn -o -name .hg -o -name .bzr -o -name .jj -o -name .sl \) -prune \
      -o -type f -print0
    classify
  fi
  case "$enum" in
    failed) deny_unverified "enumerator failed to run" ;;
    hit)
      # Scan the partial listing for a short grace, so a secret already listed is named.
      rc=0; scan_universal "$list" $((SECONDS + 3)) || rc=$?
      if [[ "$rc" -eq 1 ]]; then deny_dir "$SCAN_HIT"; fi
      deny_unverified "enumeration exceeded $N s" ;;
  esac
  rc=0; scan_universal "$list" "$deadline" || rc=$?
  case "$rc" in
    1) deny_dir "$SCAN_HIT" ;;
    3) deny_unverified "enumeration exceeded $N s" ;;
  esac
fi

# Steps 6-7: load the project globs (only now, after every universal check has had its say) and judge
# the target. The shared deadline applies only when step 5b ran; otherwise there is none.
PG_DEADLINE=0
if [[ "$walked" -eq 1 ]]; then PG_DEADLINE=$deadline; fi
load_project_globs
PVT_ROOT="$target"; PVT_PHYS=""; PVR_ROOT="$raw_root"; PVR_PHYS=""
if [[ ${#PGLOBS[@]} -gt 0 ]]; then
  # Every root keeps its OWN physical view (a view that cannot be computed is absent, not a deny): PHYS_T of
  # the tool-computed target, PHYS_R of the raw path when it differs and exists. Each only over-blocks.
  phys_of "$target"; PHYS_T="$PHYS"; PHYS_R=""
  if [[ "$raw_root" != "$target" && -e "$raw_target" ]]; then phys_of "$raw_target"; PHYS_R="$PHYS"; fi
  pdeny=""
  if project_target_hit "$target"; then pdeny="$target"; fi
  for v in "$PHYS_T" "$PHYS_R"; do
    if [[ -z "$pdeny" ]] && (( PG_OVERRUN == 0 )) && [[ -n "$v" && "$v" != "$target" ]]; then
      if project_target_hit "$v"; then pdeny="$target"; fi
    fi
  done
  if [[ -n "$pdeny" ]]; then :
  elif (( PG_OVERRUN == 0 )); then
    for p in "$raw_target" "$target"; do
      if [[ -L "$p" ]]; then
        resolved=$(readlink -f -- "$p" 2>/dev/null || true)
        if [[ -n "$resolved" ]] && project_target_hit "$resolved"; then pdeny="$target -> $resolved"; break; fi
        if (( PG_OVERRUN )); then break; fi
      fi
    done
  fi
  if [[ -z "$pdeny" ]] && (( PG_OVERRUN )); then deny_unverified "enumeration exceeded ${N:-?} s"; fi
  if [[ -n "$pdeny" ]]; then deny_file "$pdeny" "Project secret glob ($PGLOB_FILE): $PGLOB_MATCH"; fi
  if [[ -n "$PHYS_T" && "$PHYS_T" != "$target" ]]; then PVT_PHYS="$PHYS_T"; fi
  if (( second_root )) && [[ -n "$PHYS_R" && "$PHYS_R" != "$PVR_ROOT" ]]; then PVR_PHYS="$PHYS_R"; fi
fi

# Step 8: the project pass over the listing kept by step 5b.
if [[ "$walked" -eq 1 ]]; then
  rc=0; scan_project "$list" "$deadline" || rc=$?
  case "$rc" in
    1) deny_dir "$SCAN_HIT" ;;
    3) deny_unverified "enumeration exceeded $N s" ;;
  esac
fi
exit 0

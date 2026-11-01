#!/usr/bin/env bash
set -uo pipefail

# Script: test_guard_secrets.sh
# Purpose: Regression tests for guard-secrets.sh — deny list, exemptions, stream
#          separation, and the fail-safe paths (garbage stdin, jq absent).
# Usage:   bash scripts/tests/test_guard_secrets.sh

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
script="$here/../guard-secrets.sh"

sandbox="$(mktemp -d)"
trap 'rm -rf "$sandbox"' EXIT
unset CLAUDE_PROJECT_DIR

pass=0
fail=0

# run <file_path> <expected_exit> <label> — capture streams separately for assertions.
run() {
  local file="$1" want="$2" label="$3" got=0 out err
  out="$sandbox/out"; err="$sandbox/err"
  printf '{"tool_input":{"file_path":"%s"}}' "$file" \
    | bash "$script" >"$out" 2>"$err" || got=$?
  if [[ "$got" -ne "$want" ]]; then
    printf 'FAIL  %s (want %d, got %d)\n' "$label" "$want" "$got"
    fail=$((fail + 1))
    return
  fi
  # A deny must explain itself on stderr and stay silent on stdout.
  if [[ "$want" -eq 2 ]]; then
    if [[ ! -s "$err" || -s "$out" ]]; then
      printf 'FAIL  %s (deny must write stderr only; stderr=%sB stdout=%sB)\n' \
        "$label" "$(wc -c <"$err" | tr -d ' ')" "$(wc -c <"$out" | tr -d ' ')"
      fail=$((fail + 1))
      return
    fi
  fi
  printf 'PASS  %s (exit %d)\n' "$label" "$got"
  pass=$((pass + 1))
}

# raw <stdin> <expected_exit> <label> — feed arbitrary stdin (fail-safe cases).
raw() {
  local stdin="$1" want="$2" label="$3" got=0
  printf '%s' "$stdin" | bash "$script" >/dev/null 2>&1 || got=$?
  if [[ "$got" -eq "$want" ]]; then
    printf 'PASS  %s (exit %d)\n' "$label" "$got"
    pass=$((pass + 1))
  else
    printf 'FAIL  %s (want %d, got %d)\n' "$label" "$want" "$got"
    fail=$((fail + 1))
  fi
}

# ---------- Grep-shaped fixture and helpers ----------
# `repo` is a real git working tree (rg honours .gitignore only inside one); physical path via
# `pwd -P` so the /var -> /private/var symlink cannot make logical and physical names differ.
repo="$(cd "$sandbox" && pwd -P)/repo"
trap_dir="$(cd "$sandbox" && pwd -P)/trap"
mkdir -p "$sandbox/home/.config"

# build_repo — every file the directory-Grep rows read. Visible secrets are EXACTLY five:
# .env tracked.env sub/x.key real/a.pem dotonly/.env (vendor/k.pem, both ignored.env files, the VCS
# copies and linkdir/ are correctly absent from rg's listing).
build_repo() (
  set -e
  mkdir -p "$repo"
  cd "$repo"
  git init -q .
  git -C "$repo" config commit.gpgsign false
  git -C "$repo" config tag.gpgsign false
  printf 'ignored.env\nvendor/\n' >.gitignore
  mkdir -p vendor specs sub sub2 dotonly real .hg
  printf 'S=1\n' | tee .env tracked.env ignored.env vendor/k.pem sub/x.key sub2/ignored.env \
    dotonly/.env real/a.pem .git/x.pem .hg/y.pem >/dev/null
  printf 'plain\n' >plain.txt
  printf 'n\n' >specs/notes.md
  ln -s real linkdir
  ln -s .env innocent.txt
)
build_repo

# build_trap — a NON-git tree for the symlinked-directory `..` rows: r/jump is a symlink to a
# directory whose parent (real/) holds a secret and a symlink to it. Physically r/jump/.. is real/.
build_trap() (
  set -e
  mkdir -p "$trap_dir/real/deep" "$trap_dir/r"
  printf 'S=1\n' >"$trap_dir/real/.env"
  ln -s .env "$trap_dir/real/x"
  printf 'n\n' >"$trap_dir/real/note.txt"
  printf 'p\n' >"$trap_dir/r/plain.txt"
  ln -s ../real/deep "$trap_dir/r/jump"
)
build_trap

# Payload builders: `pl <tool> <cwd> <tool_input-json>` mirrors the captured fixture key set;
# `ti <key> <value>` builds a one-key tool_input (jq, so odd characters cannot break the JSON).
pl() { jq -nc --arg t "$1" --arg c "$2" --argjson i "$3" \
  '{hook_event_name:"PreToolUse",cwd:$c,tool_name:$t,tool_input:$i}'; }
ti() { jq -nc --arg k "$1" --arg v "$2" '{($k):$v}'; }

# Per-row environment: reset before each row that changes it.
hook_reset() { HOOK_CWD="$repo"; HOOK_PROJ="$repo"; HOOK_ENV=(); }
hook_reset
# HOOK_CAP — when set (seconds), hook_case runs the guard under a GNU timeout that kills its whole process
# group at the cap, so a regression that makes it quadratic FAILs a row instead of hanging the suite. Set
# only around the timing rows. The timeout command is resolved once; with none, rows stay unbounded.
HOOK_CAP=""
cap_cmd="$(command -v timeout 2>/dev/null || command -v gtimeout 2>/dev/null || true)"
[[ -n "$cap_cmd" ]] || printf 'note  no timeout or gtimeout on PATH: timing rows are unbounded\n' >&2

# hook_case <want> <label> <payload> [must] [mustnot] — run the real hook process (not main() in
# process) from HOOK_CWD with CLAUDE_PROJECT_DIR=HOOK_PROJ; HOME/XDG are isolated so rg cannot read
# the developer's global excludes, and RIPGREP_CONFIG_PATH is unset unless a row sets it in HOOK_ENV.
# Labels are LITERAL text. Exactly ONE PASS/FAIL line per call.
hook_case() {
  local want="$1" label="$2" payload="$3" must="${4:-}" mustnot="${5:-}" got=0 capv=()
  if [[ -n "$HOOK_CAP" && -n "$cap_cmd" ]]; then capv=("$cap_cmd" -k 2 "$HOOK_CAP"); fi
  ( cd "$HOOK_CWD" || exit 99
    printf '%s' "$payload" \
      | ${capv[@]+"${capv[@]}"} env -u RIPGREP_CONFIG_PATH HOME="$sandbox/home" XDG_CONFIG_HOME="$sandbox/home/.config" \
          CLAUDE_PROJECT_DIR="$HOOK_PROJ" ${HOOK_ENV[@]+"${HOOK_ENV[@]}"} bash "$script" ) \
    >"$sandbox/out" 2>"$sandbox/err" || got=$?
  if [[ "${#capv[@]}" -gt 0 && ( "$got" -eq 124 || "$got" -eq 137 ) ]]; then
    printf 'FAIL  %s (killed at cap: wall time over %s s)\n' "$label" "$HOOK_CAP"; fail=$((fail + 1)); return
  fi
  if [[ "$got" -ne "$want" ]]; then
    printf 'FAIL  %s (want %d, got %d)\n' "$label" "$want" "$got"; fail=$((fail + 1)); return
  fi
  if [[ "$want" -eq 2 && ( ! -s "$sandbox/err" || -s "$sandbox/out" ) ]]; then
    printf 'FAIL  %s (deny must write stderr only)\n' "$label"; fail=$((fail + 1)); return
  fi
  if [[ -n "$must" ]] && ! grep -qF -- "$must" "$sandbox/err"; then
    printf 'FAIL  %s (stderr lacks the expected text)\n' "$label"; fail=$((fail + 1)); return
  fi
  if [[ -n "$mustnot" ]] && grep -qF -- "$mustnot" "$sandbox/err"; then
    printf 'FAIL  %s (stderr holds text it must not)\n' "$label"; fail=$((fail + 1)); return
  fi
  printf 'PASS  %s (exit %d)\n' "$label" "$got"; pass=$((pass + 1))
}

# stub_dir <tag> <file> <body> — a directory holding an executable stub, to be put FIRST on PATH.
stub_dir() {
  local d="$sandbox/stub-$1"; mkdir -p "$d"
  printf '#!/bin/sh\n%s\n' "$3" >"$d/$2"; chmod +x "$d/$2"; printf '%s' "$d"
}
# no_rg_path <tag> — a PATH of symlinks to exactly the tools the guard needs, WITHOUT rg (same trick as
# the no-jq fixture): bash jq find sed head cat readlink mktemp rm sleep tr wc dirname basename grep kill.
no_rg_path() {
  local d="$sandbox/nopath-$1"; mkdir -p "$d"; local t p
  for t in bash jq find sed head cat readlink mktemp rm sleep tr wc dirname basename grep kill; do
    p="$(command -v "$t" 2>/dev/null)" && [[ -x "$p" ]] && ln -sf "$p" "$d/$t"
  done
  printf '%s' "$d"
}
# walk_secs_case <env-value> <want> <label> — runs the guard's own walk_secs function, extracted
# between its marker lines, in a fresh bash (the guard is not sourceable: it reads stdin at top level).
walk_secs_case() {
  local fn got
  fn="$(sed -n '/^# BEGIN walk_secs$/,/^# END walk_secs$/p' "$script")"
  got="$(env GUARD_SECRETS_WALK_SECS="$1" bash -c "set -euo pipefail; $fn"$'\n'"walk_secs" 2>/dev/null)" || got="<error>"
  if [[ "$got" == "$2" ]]; then printf 'PASS  %s (=%s)\n' "$3" "$got"; pass=$((pass + 1))
  else printf 'FAIL  %s (want %s, got %s)\n' "$3" "$2" "$got"; fail=$((fail + 1)); fi
}

# gti <path> <output_mode> [glob] [type] — a Grep tool_input; an empty argument omits its key.
gti() {
  jq -nc --arg p "$1" --arg m "$2" --arg g "${3:-}" --arg t "${4:-}" '{pattern:"S"}
    + (if $p != "" then {path:$p} else {} end) + (if $m != "" then {output_mode:$m} else {} end)
    + (if $g != "" then {glob:$g} else {} end) + (if $t != "" then {type:$t} else {} end)'
}
# hc <hook_case args> / hc_done [failure-reason] — run hook_case with its one line held back, so a row
# can add an assertion hook_case lacks: a non-empty reason turns a PASS into a FAIL (still one line).
hc() { HC_LABEL="$2"; hook_case "$@" >"$sandbox/hc"; }
hc_done() {
  local line; line="$(<"$sandbox/hc")"
  if [[ "$line" == PASS* && -n "${1:-}" ]]; then
    pass=$((pass - 1)); fail=$((fail + 1)); printf 'FAIL  %s (%s)\n' "$HC_LABEL" "$1"
  else printf '%s\n' "$line"; fi
}
# first_named — the text after the marker on the FIRST stderr line of the last hook_case run.
first_named() { local l; l="$(head -1 "$sandbox/err")"; printf '%s' "${l#*would return secret content: }"; }
# member_ok <name> — 0 when <name> is an EXACT member of the hand-written five-name set (never derived
# from rg, never a substring match).
member_ok() {
  local members=" .env tracked.env sub/x.key real/a.pem dotonly/.env "
  [[ -n "$1" && "$members" == *" $1 "* ]]
}
# hook_rc <payload> — run the hook like hook_case, keeping stderr in $sandbox/err; sets HOOK_RC.
hook_rc() {
  HOOK_RC=0
  ( cd "$HOOK_CWD" || exit 99
    printf '%s' "$1" \
      | env -u RIPGREP_CONFIG_PATH HOME="$sandbox/home" XDG_CONFIG_HOME="$sandbox/home/.config" \
          CLAUDE_PROJECT_DIR="$HOOK_PROJ" ${HOOK_ENV[@]+"${HOOK_ENV[@]}"} bash "$script" ) \
    >"$sandbox/out" 2>"$sandbox/err" || HOOK_RC=$?
}

# ---------- deny (exit 2) ----------
run "$sandbox/.env" 2 ".env denied"
run "$sandbox/.env.local" 2 ".env.local denied"
run "$sandbox/a/b/c/.env" 2 "nested .env denied"
run "$sandbox/prod.env" 2 "prod.env denied (*.env)"
run "$sandbox/server.key" 2 "server.key denied"
run "$sandbox/cert.pem" 2 "cert.pem denied"
run "$sandbox/id_rsa" 2 "id_rsa denied"
run "$sandbox/.ssh/id_ed25519" 2 "id_ed25519 denied"
run "$sandbox/id_rsa.bak" 2 "id_rsa.bak denied"
run "$sandbox/.ENV" 2 ".ENV denied (case-insensitive fs)"
run "$sandbox/PROD.ENV" 2 "PROD.ENV denied (case-insensitive fs)"
run "$sandbox/KEY.PEM" 2 "KEY.PEM denied (case-insensitive fs)"

# ---------- allow (exit 0) ----------
run "$sandbox/.env.example" 0 ".env.example allowed"
run "$sandbox/.env.sample" 0 ".env.sample allowed"
run "$sandbox/.env.template" 0 ".env.template allowed"
run "$sandbox/.env.dist" 0 ".env.dist allowed"
run "$sandbox/id_rsa.pub" 0 "id_rsa.pub allowed"
run "$sandbox/host.key.pub" 0 "host.key.pub allowed"
run "$sandbox/envfile" 0 "envfile allowed"
run "$sandbox/monkey.pens" 0 "monkey.pens allowed"
run "$sandbox/README.md" 0 "README.md allowed"
run "$sandbox/.ENV.EXAMPLE" 0 ".ENV.EXAMPLE allowed (exemptions case-fold too)"

# ---------- fail-safe ----------
raw '{}' 0 "empty JSON allowed (fail-safe)"
raw 'not-json' 0 "garbage stdin allowed (fail-safe)"
raw '' 0 "empty stdin allowed (fail-safe)"

# ---------- symlinks: deny by the resolved target's basename ----------
printf 'SECRET=1\n' >"$sandbox/.env"
printf 'notes\n' >"$sandbox/notes.md"
ln -s .env "$sandbox/innocent_name.txt"
ln -s notes.md "$sandbox/plain_link.txt"
ln -s .env "$sandbox/link.env.example"
run "$sandbox/innocent_name.txt" 2 "symlink to .env denied (target basename)"
run "$sandbox/plain_link.txt" 0 "symlink to plain file allowed"
run "$sandbox/link.env.example" 2 "exempt-looking symlink to .env still denied"

# ---------- Grep tool: the `path` key returns file contents too, so it must be guarded ----------
# Grep(pattern, path, output_mode=content) dumps a file's lines; it passes .tool_input.path, not
# .tool_input.file_path. The guard must read either key or a secret is dumped with no hook firing.
raw "{\"tool_input\":{\"path\":\"$sandbox/.env\"}}" 2 "Grep path=.env denied (.tool_input.path)"
raw "{\"tool_input\":{\"path\":\"$sandbox/prod.env\"}}" 2 "Grep path=prod.env denied (*.env)"
raw "{\"tool_input\":{\"path\":\"$sandbox/README.md\"}}" 0 "Grep path=README.md allowed"
# When both keys are present, either one matching a secret must deny.
raw "{\"tool_input\":{\"path\":\"$sandbox/.env\",\"file_path\":\"$sandbox/ok.md\"}}" 2 "path secret denies even with innocent file_path"

# ---------- jq absent: the sed fallback must still deny/allow correctly ----------
nojq="$sandbox/nojq-bin"
mkdir -p "$nojq"
for t in bash sed head printf cat; do
  p="$(command -v "$t" 2>/dev/null)" && ln -s "$p" "$nojq/$t"
done
got=0
printf '{"tool_input":{"file_path":"%s/.env"}}' "$sandbox" \
  | PATH="$nojq" bash "$script" >/dev/null 2>&1 || got=$?
if [[ "$got" -eq 2 ]]; then
  printf 'PASS  no-jq fallback still denies .env (exit 2)\n'
  pass=$((pass + 1))
else
  printf 'FAIL  no-jq fallback (want 2, got %d)\n' "$got"
  fail=$((fail + 1))
fi
got=0
printf '{"tool_input":{"file_path":"%s/README.md"}}' "$sandbox" \
  | PATH="$nojq" bash "$script" >/dev/null 2>&1 || got=$?
if [[ "$got" -eq 0 ]]; then
  printf 'PASS  no-jq fallback still allows README.md (exit 0)\n'
  pass=$((pass + 1))
else
  printf 'FAIL  no-jq fallback allow (want 0, got %d)\n' "$got"
  fail=$((fail + 1))
fi

# ---------- normalization, target views, newline safety ----------
tilde='~'
hook_reset
hook_case 2 "RED nonexist dotdot innocent symlink to .env denied after collapse" \
  "$(pl Read "$repo" "$(ti file_path "$repo/nonexist/../innocent.txt")")"
HOOK_CWD="$sandbox"   # hook cwd differs from payload cwd: resolution must use the PAYLOAD cwd
hook_case 2 "RED relative symlinked leaf resolves against payload cwd" \
  "$(pl Read "$repo" "$(ti file_path "innocent.txt")")"
hook_reset; HOOK_ENV=(HOME="$repo")
hook_case 2 "RED tilde-slash innocent symlink expands HOME and resolves" \
  "$(pl Read "$repo" "$(ti file_path "$tilde/innocent.txt")")"
hook_case 2 "tilde-slash .env denied (regression)" "$(pl Read "$repo" "$(ti file_path "$tilde/.env")")"
hook_reset
hook_case 2 "relative specs dotdot .env with cwd denied (regression)" \
  "$(pl Read "$repo" "$(ti file_path "specs/../.env")")"
hook_case 2 "absolute specs dotdot .env denied (regression)" \
  "$(pl Read "$repo" "$(ti file_path "$repo/specs/../.env")")"
hook_case 2 "absolute dot sub dotdot .env denied (regression)" \
  "$(pl Read "$repo" "$(ti file_path "$repo/./sub/../.env")")"
hook_case 0 "absolute specs dotdot plain.txt allowed (regression)" \
  "$(pl Read "$repo" "$(ti file_path "$repo/specs/../plain.txt")")"
hook_case 2 "relative symlink with no payload cwd resolves against hook cwd (regression)" \
  "$(pl Read "" "$(ti file_path "innocent.txt")")"
hook_case 0 "relative plain.txt with no payload cwd allowed (regression)" \
  "$(pl Read "" "$(ti file_path "plain.txt")")"
hook_case 2 "percent-encoded dotdot keeps a literal name, basename .env denied (regression)" \
  "$(pl Read "$repo" "$(ti file_path "$repo/%2e%2e/.env")")"
hook_case 0 "percent-encoded dotdot plain.txt allowed, no decoding (regression)" \
  "$(pl Read "$repo" "$(ti file_path "$repo/%2e%2e/plain.txt")")"
# The raw-path symlink check and newline-safe extraction (regression rows: the base guard denies all
# three, and a normalize-only or one-line-read implementation would not). The first pins an OVER-BLOCK
# (safe direction): the Grep tool collapses `r/jump/..` lexically, so it never opens `real/x`.
hook_case 2 "symlinked dir dotdot x symlink to .env denied via the raw path (regression)" \
  "$(pl Read "$trap_dir" "$(ti file_path "$trap_dir/r/jump/../x")")"
hook_case 2 "newline in a segment before dotdot .env still denied (regression)" \
  "$(pl Read "$repo" "$(ti file_path "$repo/a"$'\n'"b/../.env")")"
hook_case 2 "newline in payload cwd does not stop a relative .env being denied (regression)" \
  "$(pl Read "/x"$'\n'"y" "$(ti file_path ".env")")"
hook_case 2 "RED dot leaf after a file collapses to the secret basename" \
  "$(pl Read "$repo" "$(ti file_path "$repo/.env/.")")"

# ---------- directory Grep: enumerate what the tool would read (Task 2) ----------
vpay="$(pl Grep "$repo" "$(gti "$repo" content)")"   # the standard [V] payload: content Grep over the repo
printf -- '--glob=!*.env\n' >"$sandbox/rgrc"

hook_reset
hc 2 "RED directory containing secrets, content mode" "$vpay"
hc_done "$(member_ok "$(first_named)" || printf 'first named path is not in the hand-written set')"
hook_case 2 "RED Grep with no path targets payload cwd" "$(pl Grep "$repo" "$(gti "" content)")"
hook_case 0 "files_with_matches over the repo" "$(pl Grep "$repo" "$(gti "$repo" files_with_matches)")"
hook_case 0 "count over the repo" "$(pl Grep "$repo" "$(gti "$repo" count)")"
hook_case 0 "absent output_mode over the repo" "$(pl Grep "$repo" "$(gti "$repo" "")")"
hook_case 0 "specs directory without secrets, content" "$(pl Grep "$repo" "$(gti "$repo/specs" content)")"
hook_case 0 "sub2 whose secret is ignored by file name, content" "$(pl Grep "$repo" "$(gti "$repo/sub2" content)")"
hook_case 2 "RED vendor ignored directory given as the root, content" "$(pl Grep "$repo" "$(gti "$repo/vendor" content)")"
hook_case 2 "RED dotonly directory whose only secret is a dotfile" "$(pl Grep "$repo" "$(gti "$repo/dotonly" content)")"
HOOK_ENV=(RIPGREP_CONFIG_PATH="$sandbox/rgrc")
hook_case 2 "RED RIPGREP_CONFIG_PATH cannot narrow the listing" "$(pl Grep "$repo" "$(gti "$repo/dotonly" content)")"
hook_reset
hook_case 0 "glob md over the repo" "$(pl Grep "$repo" "$(gti "$repo" content '*.md')")"
hook_case 0 "type md over the repo" "$(pl Grep "$repo" "$(gti "$repo" content '' md)")"
hook_case 2 "RED glob key" "$(pl Grep "$repo" "$(gti "$repo" content '*.key')")"
if rg --type-list 2>/dev/null | grep -q '^sh:.*\*\.env'; then
  hook_case 2 "RED type sh" "$(pl Grep "$repo" "$(gti "$repo" content '' sh)")"
else
  printf 'PASS  type sh row skipped (PATH rg sh type lacks .env)\n'; pass=$((pass + 1))
fi
hook_case 2 "RED comma-joined glob" "$(pl Grep "$repo" "$(gti "$repo" content '*.md,*.key')")"
hook_case 2 "RED space-joined glob" "$(pl Grep "$repo" "$(gti "$repo" content '*.md *.key')")"
hook_case 2 "RED brace glob passed whole" "$(pl Grep "$repo" "$(gti "$repo" content '*.{md,key}')")"
hook_case 0 "comma glob that excludes secrets" "$(pl Grep "$repo" "$(gti "$repo" content '*.md,*.txt')")"
hook_case 0 "dash-leading glob is a pattern, not an option" "$(pl Grep "$repo" "$(gti "$repo" content -x)")" "" "UNVERIFIED"
hook_case 2 "RED glob pem names real/a.pem" "$(pl Grep "$repo" "$(gti "$repo" content '*.pem')")"
hook_case 0 "glob x.pem, only the excluded .git copy matches" "$(pl Grep "$repo" "$(gti "$repo" content x.pem)")"
hook_case 0 "glob y.pem, only the excluded .hg copy matches" "$(pl Grep "$repo" "$(gti "$repo" content y.pem)")"
hook_case 2 "RED unknown type is retried without type" "$(pl Grep "$repo" "$(gti "$repo" content '' nosuchtype)")"
HOOK_ENV=(PATH="$(stub_dir rg2 rg 'exit 2'):$PATH")
hook_case 0 "rg exiting 2 with no type passed stands" "$vpay"

# D25-D31: the bounded run (stubs first on PATH).
hook_reset
HOOK_ENV=(PATH="$(stub_dir sl rg 'exec sleep 5'):$PATH" GUARD_SECRETS_WALK_SECS=1)
t0="$(date +%s)"
hc 2 "RED bound hit" "$vpay" "UNVERIFIED (enumeration exceeded 1 s)"
t1="$(date +%s)"
hc_done "$([[ $((t1 - t0)) -lt 4 ]] || printf 'wall time %s s, not under 4' "$((t1 - t0))")"
hook_reset
HOOK_ENV=(PATH="$(stub_dir tm rg 'kill -TERM $$'):$PATH")
hook_case 2 "RED a stub that TERMs itself" "$vpay" "UNVERIFIED (enumerator failed to run)" "(enumeration exceeded"
HOOK_ENV=(PATH="$(stub_dir pl rg "printf '%s\\0' '$repo/.env'; exec sleep 5"):$PATH" GUARD_SECRETS_WALK_SECS=2)
hc 2 "RED partial listing already holds a secret, then hangs" "$vpay" "" "UNVERIFIED"
hc_done "$([[ "$(first_named)" == ".env" ]] || printf 'did not name .env')"
HOOK_ENV=(PATH="$(stub_dir x127 rg 'exit 127'):$PATH")
hook_case 2 "RED launch failure" "$vpay" "UNVERIFIED (enumerator failed to run)"
HOOK_ENV=(PATH="$(stub_dir segv rg 'kill -SEGV $$'):$PATH")
hook_case 2 "RED crash" "$vpay" "UNVERIFIED (enumerator failed to run)"
hook_reset
HOOK_ENV=(GUARD_SECRETS_WALK_SECS=37)
bad=0
for _ in $(seq 20); do hook_rc "$vpay"; [[ "$HOOK_RC" -eq 2 ]] || bad=$((bad + 1)); done
if [[ "$bad" -eq 0 ]]; then printf 'PASS  RED race loop: 20 runs all exit 2 (exit 2)\n'; pass=$((pass + 1))
else printf 'FAIL  RED race loop: 20 runs all exit 2 (%d of 20 did not)\n' "$bad"; fail=$((fail + 1)); fi
t0="$(date +%s)"; rc=0
cap="$( ( cd "$HOOK_CWD" && printf '%s' "$vpay" | env -u RIPGREP_CONFIG_PATH HOME="$sandbox/home" XDG_CONFIG_HOME="$sandbox/home/.config" \
  CLAUDE_PROJECT_DIR="$HOOK_PROJ" GUARD_SECRETS_WALK_SECS=37 bash "$script" 2>/dev/null ) )" || rc=$?
t1="$(date +%s)"
if [[ "$rc" -eq 2 && $((t1 - t0)) -lt 3 && -z "$cap" ]]; then printf 'PASS  RED orphan check: captured run returns at once (exit 2)\n'; pass=$((pass + 1))
else printf 'FAIL  RED orphan check: captured run returns at once (rc %s, %s s)\n' "$rc" "$((t1 - t0))"; fail=$((fail + 1)); fi

# D32-D35: walk_secs, run from the function extracted between the guard's marker lines.
walk_secs_case abc 10 "RED walk_secs: non-numeric falls back to 10"
walk_secs_case 99 20 "RED walk_secs: 99 clamps to 20"
walk_secs_case 7 7 "RED walk_secs: 7 stays 7"
bad=0
for pair in 08:8 0:10 -5:10 :10 99999999999999999999:20; do
  got="$(env GUARD_SECRETS_WALK_SECS="${pair%%:*}" bash -c "set -euo pipefail; $(sed -n '/^# BEGIN walk_secs$/,/^# END walk_secs$/p' "$script")"$'\n'"walk_secs" 2>/dev/null)" || got="<error>"
  [[ "$got" == "${pair#*:}" ]] || bad=$((bad + 1))
done
if [[ "$bad" -eq 0 ]]; then printf 'PASS  RED walk_secs: leading zero, zero, negative, empty and huge values normalise\n'; pass=$((pass + 1))
else printf 'FAIL  RED walk_secs: leading zero, zero, negative, empty and huge values normalise (%d mismatches)\n' "$bad"; fail=$((fail + 1)); fi

# D36-D41: rg absent, the find -H fallback.
hook_reset
HOOK_ENV=(PATH="$(no_rg_path a)")
hook_case 2 "RED rg absent, dotonly content" "$(pl Grep "$repo" "$(gti "$repo/dotonly" content)")" "rg not found" "add a"
hook_case 0 "rg absent, specs content (a completed fallback's allow stands)" "$(pl Grep "$repo" "$(gti "$repo/specs" content)")"
hook_case 2 "RED rg absent, symlinked directory linkdir holding a.pem" "$(pl Grep "$repo" "$(gti "$repo/linkdir" content)")"
hook_case 2 "RED rg absent, sub2 (the fallback is blind to ignore rules: documented over-block)" "$(pl Grep "$repo" "$(gti "$repo/sub2" content)")"
HOOK_ENV=(PATH="$(stub_dir f1 find 'exec sleep 5'):$(no_rg_path b)" GUARD_SECRETS_WALK_SECS=1)
hook_case 2 "RED stub find sleeps past the bound, rg absent" "$vpay" "UNVERIFIED (enumeration exceeded 1 s)"
nofind="$(no_rg_path c)"; rm -f "$nofind/find"
HOOK_ENV=(PATH="$nofind")
hook_case 2 "RED no find on PATH" "$vpay" "UNVERIFIED (enumerator failed to run)"

# D42-D52.
hook_reset
nojq_pay="$vpay"
HOOK_ENV=(PATH="$nojq")
hook_case 0 "jq absent with a Grep content payload over the repo" "$nojq_pay"
hook_reset
hook_case 0 "Read of a directory holding a .env gets no directory logic" "$(pl Read "$repo" "$(ti file_path "$repo/dotonly")")"
HOOK_ENV=(PATH="$(stub_dir late rg "trap '' TERM; printf '%s\\0' '$repo/plain.txt'; sleep 2.5"):$PATH" GUARD_SECRETS_WALK_SECS=1)
hook_case 0 "watchdog fired but the enumerator finished anyway, clean listing" "$vpay"
HOOK_ENV=(PATH="$(stub_dir late2 rg "trap '' TERM; printf '%s\\0' '$repo/.env'; sleep 2.5"):$PATH" GUARD_SECRETS_WALK_SECS=1)
hc 2 "RED watchdog fired but the enumerator finished anyway, listing holds a secret" "$vpay" "" "UNVERIFIED"
hc_done "$([[ "$(first_named)" == ".env" ]] || printf 'did not name .env')"
HOOK_ENV=(PATH="$(stub_dir f2 find "printf '%s\\0' '$repo/plain.txt'; exit 1"):$(no_rg_path d)")
hook_case 2 "RED stub find prints a clean listing then exits 1, rg absent" "$vpay" "UNVERIFIED (enumerator failed to run)"
HOOK_ENV=(PATH="$(stub_dir f3 find 'exit 2'):$(no_rg_path e)")
hook_case 2 "RED stub find exits 2, rg absent" "$vpay" "UNVERIFIED (enumerator failed to run)"
hook_reset; HOOK_PROJ="$trap_dir"
# These two rows pin an OVER-BLOCK (safe direction): the tool reads the lexical parent `r` (clean here),
# the guard also enumerates the physical parent `real` as a second root, which holds the secret.
trap_pay="$(pl Grep "$trap_dir" "$(gti "$trap_dir/r/jump/.." content)")"
hc 2 "RED directory Grep through a symlinked directory dotdot" "$trap_pay"
hc_done "$([[ "$(first_named)" == *.env ]] || printf 'did not name .env')"
HOOK_ENV=(PATH="$(no_rg_path f)")
hook_case 2 "RED the same with rg absent (find -H rooted at the raw path)" "$trap_pay"
hook_reset
hc 2 "RED newline in the glob" "$(pl Grep "$repo" "$(gti "$repo" content $'*.md\n*.key')")"
hc_done "$(member_ok "$(first_named)" || printf 'first named path is not in the hand-written set')"
hc 2 "RED vertical tab in the glob" "$(pl Grep "$repo" "$(gti "$repo" content $'*.md\v*.key')")"
hc_done "$(member_ok "$(first_named)" || printf 'first named path is not in the hand-written set')"
hook_case 2 "RED newline in payload cwd does not shift the other fields" \
  "$(pl Grep $'/tmp/x\ny' "$(gti "$repo" content)")"

# ---------- the path the TOOL computes: trim, then lexical collapse (final-review rows) ----------
# The Grep tool trims `path`, expands `~`, collapses `.`/`..` lexically and opens THAT string. The secret
# sits only at that path in every row below.
trap2_dir="$(cd "$sandbox" && pwd -P)/trap2"
mkdir -p "$trap2_dir/real/deep" "$trap2_dir/r"
printf 'S=1\n' >"$trap2_dir/r/.env"; printf 'n\n' >"$trap2_dir/real/note.txt"
ln -s ../real/deep "$trap2_dir/r/jump"
tab=$'\t'; nbsp=$'\xc2\xa0'
hook_reset
hook_case 2 "RED shape A: nonexistent segment before dotdot, secret only in the lexical parent" \
  "$(pl Grep "$repo" "$(gti "$repo/dotonly/nonexist/.." content)")"
hook_case 2 "RED shape B: trailing space on a directory path" "$(pl Grep "$repo" "$(gti "$repo/dotonly " content)")"
hook_case 2 "RED shape B: leading space on a directory path" "$(pl Grep "$repo" "$(gti " $repo/dotonly" content)")"
hook_case 2 "RED shape B: trailing tab on a directory path" "$(pl Grep "$repo" "$(gti "$repo/dotonly$tab" content)")"
hook_case 2 "RED shape B: trailing no-break space on a directory path" \
  "$(pl Grep "$repo" "$(gti "$repo/dotonly$nbsp" content)")"
hook_case 2 "RED file Grep with a trailing space on a secret path" "$(pl Grep "$repo" "$(gti "$repo/.env " content)")"
hook_case 2 "Read with a leading space on a secret path (denied by its basename before and after)" \
  "$(pl Read "$repo" "$(ti file_path " $repo/.env")")"
HOOK_PROJ="$trap2_dir"
trap2_pay="$(pl Grep "$trap2_dir" "$(gti "$trap2_dir/r/jump/.." content)")"
hc 2 "RED shape C: dotdot after a symlinked directory, secret only in the lexical parent" "$trap2_pay"
hc_done "$([[ "$(first_named)" == *.env ]] || printf 'did not name .env')"
HOOK_ENV=(PATH="$(no_rg_path g)")
hook_case 2 "RED shape C with rg absent (find -H over the tool-computed root)" "$trap2_pay"
hook_reset

# hook_closed <want> <label> <payload> — run the hook with stderr CLOSED (2>&-): a failing deny write must not turn the deny into an allow.
hook_closed() {
  local got=0
  ( cd "$HOOK_CWD" || exit 99
    printf '%s' "$3" \
      | env -u RIPGREP_CONFIG_PATH HOME="$sandbox/home" XDG_CONFIG_HOME="$sandbox/home/.config" \
          CLAUDE_PROJECT_DIR="$HOOK_PROJ" ${HOOK_ENV[@]+"${HOOK_ENV[@]}"} bash "$script" ) >/dev/null 2>&- || got=$?
  if [[ "$got" -eq "$1" ]]; then printf 'PASS  %s (exit %d)\n' "$2" "$got"; pass=$((pass + 1))
  else printf 'FAIL  %s (want %d, got %d)\n' "$2" "$1" "$got"; fail=$((fail + 1)); fi
}
hook_closed 2 "RED file deny still exits 2 with stderr closed" "$(pl Read "$repo" "$(ti file_path "$repo/.env")")"
hook_closed 2 "RED directory deny still exits 2 with stderr closed" "$vpay"

# SIGTERM to the hook mid-walk must not leave the enumerator running (the EXIT trap kills it).
rgpid="$sandbox/rgpid"; rm -f "$rgpid"
printf '%s' "$vpay" >"$sandbox/term.pay"
# shellcheck disable=SC2016 # $$ must stay literal: the stub records its own pid
termrg="$(stub_dir termrg rg 'echo $$ >"'"$rgpid"'"; exec sleep 30')"
( cd "$HOOK_CWD" && exec env -u RIPGREP_CONFIG_PATH HOME="$sandbox/home" XDG_CONFIG_HOME="$sandbox/home/.config" \
    CLAUDE_PROJECT_DIR="$HOOK_PROJ" PATH="$termrg:$PATH" GUARD_SECRETS_WALK_SECS=20 bash "$script" \
    <"$sandbox/term.pay" >/dev/null 2>&1 ) &
hp=$!
for _ in $(seq 50); do [[ -s "$rgpid" ]] && break; sleep 0.1; done
rpid="$(cat "$rgpid" 2>/dev/null)" || rpid=""
kill -TERM "$hp" 2>/dev/null; wait "$hp" 2>/dev/null
gone=0
if [[ -n "$rpid" ]]; then
  for _ in $(seq 30); do if ! kill -0 "$rpid" 2>/dev/null; then gone=1; break; fi; sleep 0.1; done
  if [[ "$gone" -eq 0 ]]; then kill -KILL "$rpid" 2>/dev/null; fi
fi
if [[ -n "$rpid" && "$gone" -eq 1 ]]; then printf 'PASS  RED SIGTERM to the hook mid-walk leaves no enumerator behind\n'; pass=$((pass + 1))
else printf 'FAIL  RED SIGTERM to the hook mid-walk leaves no enumerator behind (stub pid %s)\n' "${rpid:-none}"; fail=$((fail + 1)); fi

# ---------- project secret-globs layer, NotebookEdit coverage, registration (Task 3) ----------
# build_proj — a NON-git project (no ignore rules, no git init, so no signing concern).
proj="$(cd "$sandbox" && pwd -P)/proj"
build_proj() (
  set -e
  mkdir -p "$proj/.claude" "$proj/private" "$proj/other" "$proj/data"
  printf 'x\n' >"$proj/plain.txt"; printf 'x\n' >"$proj/a.md"
  printf 'x\n' >"$proj/private/a.txt"; printf 'x\n' >"$proj/private/b.txt"; printf 'x\n' >"$proj/other/x.txt"
  ln -sf private/a.txt "$proj/link.txt"; ln -sf plain.txt "$proj/plainlink"
  ln -sfn "$proj" "$sandbox/projlink"
  mkdir -p "$trap_dir/.claude"
  # M3 fixtures. ord: a vault directory holding a .env and a project glob that also matches it.
  mkdir -p "$ord_dir/vault" "$ord_dir/.claude"
  printf 'S=1\n' >"$ord_dir/vault/.env"; printf 'vault/**\n' >"$ord_dir/.claude/secret-globs"
  # ord2: a 255-character directory name holding a .env, and 200 three-star globs.
  a255="$(printf 'a%.0s' $(seq 255))"
  mkdir -p "$ord2_dir/$a255" "$ord2_dir/.claude"
  printf 'S=1\n' >"$ord2_dir/$a255/.env"
  yes '*a*a*b' | head -n 200 >"$ord2_dir/.claude/secret-globs"
)
ord_dir="$(cd "$sandbox" && pwd -P)/ord"
ord2_dir="$(cd "$sandbox" && pwd -P)/ord2"
a255="$(printf 'a%.0s' $(seq 255))"
build_proj
# set_globs <text> — rewrite the project's globs file (printf %b so \n is a newline).
set_globs() { printf '%b' "$1" >"$proj/.claude/secret-globs"; }
proj_reset() { hook_reset; HOOK_CWD="$proj"; HOOK_PROJ="$proj"; }
# rdp <path> — a Read payload with the project as payload cwd.
rdp() { pl Read "$proj" "$(ti file_path "$1")"; }
# now_ms — wall clock in milliseconds (EPOCHREALTIME when set, else python3).
now_ms() {
  if [[ -n "${EPOCHREALTIME:-}" ]]; then local t="${EPOCHREALTIME/[.,]/}"; printf '%s' "$((t / 1000))"
  else python3 -c 'import time; print(int(time.time()*1000))'; fi
}
# A PATH directory whose `bash` is the system bash, so a hook_case runs the guard under it.
bash32dir="$sandbox/bash32"
if [[ -x /bin/bash ]]; then mkdir -p "$bash32dir"; ln -sf /bin/bash "$bash32dir/bash"; fi

G0='# project secrets\n*.secretdb\n\n   private/**   \nRoster.CSV\n'

proj_reset; set_globs "$G0"
hook_case 2 "RED project glob denies" "$(rdp "$proj/data/x.secretdb")" '*.secretdb'
hook_case 0 "comment line is not a glob" "$(rdp "$proj/a.md")"
hook_case 0 "blank lines do not become match-all" "$(rdp "$proj/plain.txt")"
hook_case 2 "RED case-folding" "$(rdp "$proj/roster.csv")"
hook_case 2 "RED case-folding on a path-anchored glob" "$(rdp "$proj/Private/File.TXT")"
hook_case 2 "RED path-anchored glob" "$(rdp "$proj/private/a.txt")"
hook_case 0 "a deeper private directory is judged by bash semantics" "$(rdp "$proj/other/private/a.txt")"
hook_case 2 "RED rel-slash form: a files_with_matches Grep whose path IS private" \
  "$(pl Grep "$proj" "$(gti "$proj/private" files_with_matches)")"
hook_case 0 "the sibling directory is not caught" "$(pl Grep "$proj" "$(gti "$proj/other" files_with_matches)")"
hc 2 "RED content Grep over the tree names the project-relative path" "$(pl Grep "$proj" "$(gti "$proj" content)")"
hc_done "$([[ "$(first_named)" == "private/a.txt" || "$(first_named)" == "private/b.txt" ]] || printf 'first named path is not private/a.txt or private/b.txt')"
hook_case 0 "a glob that excludes private allows" "$(pl Grep "$proj" "$(gti "$proj" content '!private/**')")"
hook_case 2 "RED symlinked leaf resolves into a project glob" "$(rdp "$proj/link.txt")"
hook_case 0 "symlink to a plain file" "$(rdp "$proj/plainlink")"
hook_case 2 "RED an absent file (a Write target) matching a glob" \
  "$(pl Write "$proj" "$(ti file_path "$proj/data/new.secretdb")")"
set_globs '*.example\n'
hook_case 2 "RED project glob outranks a universal exemption" "$(rdp "$proj/.env.example")"
set_globs '*.secretdb\n'
hook_case 0 "without that glob the exemption holds" "$(rdp "$proj/.env.example")"
rm -f "$proj/.claude/secret-globs"
hook_case 0 "no globs file at all" "$(rdp "$proj/data/x.secretdb")"
set_globs "$G0"; chmod 000 "$proj/.claude/secret-globs"
hook_case 0 "unreadable globs file" "$(rdp "$proj/data/x.secretdb")"
chmod 644 "$proj/.claude/secret-globs"
rm -f "$proj/.claude/secret-globs"; mkdir "$proj/.claude/secret-globs"
hook_case 0 "the globs path is a directory" "$(rdp "$proj/data/x.secretdb")"
rmdir "$proj/.claude/secret-globs"
printf '\000\001\002\377\n' >"$proj/.claude/secret-globs"
hook_case 0 "binary globs file" "$(rdp "$proj/plain.txt")"
set_globs "$G0"
HOOK_PROJ=""
hook_case 2 "RED CLAUDE_PROJECT_DIR unset falls back to payload cwd" "$(rdp "$proj/data/x.secretdb")"
HOOK_CWD="$sandbox"
hook_case 0 "both unset: universal layer only" "$(pl Read "" "$(ti file_path "$proj/data/x.secretdb")")"
proj_reset
# shellcheck disable=SC2016 # the command-substitution text must stay literal
set_globs '$(touch pwned)\n`touch pwned`\n'
hc 0 "the globs file is DATA: command-substitution text is literal" "$(rdp "$proj/plain.txt")"
hc_done "$([[ ! -e "$proj/pwned" && ! -e "$sandbox/pwned" && ! -e "$PWD/pwned" ]] || printf 'a pwned file exists')"
yes '*.secretdb' | head -c 1048576 >"$proj/.claude/secret-globs"
hook_case 2 "a 1 MiB globs file cannot defeat a universal deny" "$(rdp "$proj/id_rsa")"
{ for _ in $(seq 200); do printf '#%399s\n' '' | tr ' ' x; done; printf '*.lateglob\n'; } >"$proj/.claude/secret-globs"
hook_case 0 "the byte cap: a blocking glob placed after 65,536 bytes has no effect" "$(rdp "$proj/x.lateglob")"
set_globs 'id_rsa*\n'
hook_case 2 "the universal check runs BEFORE the project layer" "$(rdp "$proj/id_rsa")" "" "Project secret glob"

# P27: the scan deadline, slowed through DATA (1000 non-matching globs over 3,000 files).
# Measured U (hook at an unbounded deadline, 3,000 names x 1000 globs): about 22 s on a quiet machine
# (author), 59 s at moderate load (controller), 104 s at load ~4 (implementer). The bounded run
# (GUARD_SECRETS_WALK_SECS=1) takes about 2 s. The ceiling is a FIXED 10 s, not U / 2, because U varies 5x
# with machine load. If wrong: a very loaded machine could flake the row; a machine more than ~2x faster
# than the quiet figure could fail to catch an ignored deadline.
big="$sandbox/big"
mkdir -p "$big/.claude"
seq -f 'f%04g.txt' 1 3000 | (cd "$big" && xargs touch)
seq -f 'g%04g.zzz' 1 1000 >"$big/.claude/secret-globs"
HOOK_CWD="$big"; HOOK_PROJ="$big"; HOOK_ENV=(GUARD_SECRETS_WALK_SECS=1)
t0="$(now_ms)"
hc 2 "RED scan deadline, slowed through DATA" "$(pl Grep "$big" "$(gti "$big" content)")" "UNVERIFIED"
t1="$(now_ms)"
hc_done "$([[ $((t1 - t0)) -lt 10000 ]] || printf 'wall time %s ms, not under 10000' "$((t1 - t0))")"
proj_reset

hook_case 2 "NotebookEdit of a secret" "$(pl NotebookEdit "$proj" "$(ti notebook_path "$proj/.env")")"
set_globs "$G0"
hook_case 0 "NotebookEdit of an ordinary notebook" "$(pl NotebookEdit "$proj" "$(ti notebook_path "$proj/notes.ipynb")")"
set_globs '*.ipynb\n'
hook_case 2 "RED NotebookEdit matched by a project glob" "$(pl NotebookEdit "$proj" "$(ti notebook_path "$proj/n.ipynb")")"

# This row pins an OVER-BLOCK (safe direction): the tool opens the lexical `r/note.txt`, never `real/note.txt`;
# the guard keeps the physical view for project globs.
printf 'real/**\n' >"$trap_dir/.claude/secret-globs"
HOOK_CWD="$trap_dir"; HOOK_PROJ="$trap_dir"
hook_case 2 "RED physical-view subject: a dotdot after a symlinked directory" \
  "$(pl Read "$trap_dir" "$(ti file_path "$trap_dir/r/jump/../note.txt")")"
proj_reset

settings="$here/../../settings.json"
if jq -e '[.hooks.PreToolUse[] | select(any(.hooks[]; .command | endswith("/guard-secrets.sh")))
      | select((.matcher | contains("Grep")) and (.matcher | contains("NotebookEdit")))
      | .hooks[] | select(.command | endswith("/guard-secrets.sh")) | select((.timeout // 0) > 20)] | length > 0' "$settings" >/dev/null 2>&1 \
  && jq -e '[.hooks.PostToolUse[].hooks[] | select(.command | endswith("/guard-secrets-test.sh"))
      | select((.timeout // 0) > 60)] | length > 0' "$settings" >/dev/null 2>&1; then
  printf 'PASS  RED standing registration row (exit 0)\n'; pass=$((pass + 1))
else
  printf 'FAIL  RED standing registration row (matcher, guard timeout or runner timeout not as required)\n'; fail=$((fail + 1))
fi

set_globs "$(printf '#%.0s\\n' $(seq 1000))"
printf '*.line1001\n' >>"$proj/.claude/secret-globs"
hook_case 0 "the 1000-line cap: a blocking glob on line 1001 has no effect" "$(rdp "$proj/x.line1001")"
x600="$(printf 'x%.0s' $(seq 600))"
printf '*.longline%s\n' "$x600" >"$proj/.claude/secret-globs"
hook_case 0 "the 512-byte line cap: a blocking glob on a 600-byte line has no effect" "$(rdp "$proj/a.longline$x600")"
set_globs '#*\n'
hook_case 0 "a #* comment line is not a glob" "$(rdp "$proj/#x")"
set_globs '*.secretdb\r\n'
hook_case 2 "RED CRLF globs file" "$(rdp "$proj/data/x.secretdb")"
set_globs 'Roster.CSV\n'
hook_case 2 "RED basename subject" "$(rdp "$proj/data/roster.csv")"
set_globs "$proj/private/*\\n"
hook_case 2 "RED full-path subject" "$(rdp "$proj/private/b.txt")"

if [[ -x /bin/bash ]]; then
  set_globs '*a*a*a*a*a*a*a*a*b\n'
  a46="$(printf 'a%.0s' $(seq 46))"
  HOOK_ENV=(PATH="$bash32dir:$PATH")
  t0="$(now_ms)"
  hc 0 "bash 3.2: a 9-star glob is ignored by the 3-star cap and cannot hang" "$(rdp "$proj/$a46")"
  t1="$(now_ms)"
  hc_done "$([[ $((t1 - t0)) -lt 1500 ]] || printf 'wall time %s ms, not under 1500' "$((t1 - t0))")"
  HOOK_ENV=()
else
  printf 'PASS  bash 3.2 row skipped (no /bin/bash)\n'; pass=$((pass + 1))
fi

set_globs 'private/**\n'
HOOK_PROJ="$proj/"
hook_case 2 "RED trailing slash on CLAUDE_PROJECT_DIR" "$(rdp "$proj/private/a.txt")"

# P42: the universal pass runs BEFORE any project match.
HOOK_CWD="$ord_dir"; HOOK_PROJ="$ord_dir"
hook_case 2 "the universal pass runs before any project match" \
  "$(pl Grep "$ord_dir" "$(gti "$ord_dir/vault" content)")" "would return secret content: vault/.env" "Project secret glob"

# P43: the bash 3.2 consequence of that order. Measured T_old (the project check moved back before the
# enumeration, run under /bin/bash 3.2.57): 15.7 s under load (about 12 s quiet, author); the real order takes 0.24 s. The fixed 7 s ceiling sits below
# the quiet T_old and far above the real-order wall, so it is kept.
if [[ -x /bin/bash ]]; then
  HOOK_CWD="$ord2_dir"; HOOK_PROJ="$ord2_dir"; HOOK_ENV=(PATH="$bash32dir:$PATH")
  t0="$(now_ms)"
  hc 2 "bash 3.2: a hostile glob file cannot delay the universal deny on a long directory name" \
    "$(pl Grep "$ord2_dir" "$(gti "$ord2_dir/$a255" content)")"
  t1="$(now_ms)"
  hc_done "$([[ $((t1 - t0)) -lt 7000 ]] || printf 'wall time %s ms, not under 7000' "$((t1 - t0))")"
  HOOK_ENV=()
else
  printf 'PASS  bash 3.2 row skipped (no /bin/bash)\n'; pass=$((pass + 1))
fi

proj_reset; set_globs 'private/**\n'; HOOK_PROJ="$sandbox/projlink"
hook_case 2 "RED logical versus physical project root" "$(rdp "$proj/private/a.txt")"
proj_reset

proj_reset; set_globs 'private/a.txt\n'
hook_case 2 "RED content Grep through a dotdot path matches a project glob on the normalized prefix" \
  "$(pl Grep "$proj" "$(gti "$proj/other/../private" content)")"
hook_case 2 "RED content Grep through a symlinked root matches a project glob on the physical prefix" \
  "$(pl Grep "$proj" "$(gti "$sandbox/projlink/private" content)")"
proj_reset; HOOK_CWD="$ord_dir"; HOOK_PROJ="$ord_dir"; HOOK_ENV=(PATH="$(stub_dir x127b rg 'exit 127'):$PATH")
hook_case 2 "RED enumerator launch failure is judged before any project match" \
  "$(pl Grep "$ord_dir" "$(gti "$ord_dir/vault" content)")" "UNVERIFIED (enumerator failed to run)" "Project secret glob"
proj_reset

# ---------- A9: every root keeps its own physical view ----------
# phk/: real/sub/z.txt; real/sub/x -> ../../outside/deep (clean); pk/L -> ../real. The path pk/L/sub/x/..
# exists (physically outside/), and the tool reads the lexical pk/L/sub (physically real/sub).
phk="$(cd "$sandbox" && pwd -P)/phk"
mkdir -p "$phk/real/sub" "$phk/outside/deep" "$phk/pk" "$phk/.claude"
printf 'x\n' >"$phk/real/sub/z.txt"
ln -s ../../outside/deep "$phk/real/sub/x"
ln -s ../real "$phk/pk/L"
phk_path="$phk/pk/L/sub/x/.."
proj_reset; HOOK_CWD="$phk"; HOOK_PROJ="$phk"
printf 'real/**\n' >"$phk/.claude/secret-globs"
hook_case 2 "RED A9 row 1: content Grep, the target's physical view matches a project glob" \
  "$(pl Grep "$phk" "$(gti "$phk_path" content)")"
hook_case 2 "RED A9 row 2: files_with_matches Grep, the rel-slash form of the target's physical view" \
  "$(pl Grep "$phk" "$(gti "$phk_path" files_with_matches)")"
printf 'real/sub/z.txt\n' >"$phk/.claude/secret-globs"
hook_case 2 "RED A9 row 3: an entry under the target root maps through the target's physical view" \
  "$(pl Grep "$phk" "$(gti "$phk_path" content)")"
printf 'outside/**\n' >"$phk/.claude/secret-globs"
hook_case 2 "A9 row 4: the raw path's physical view still matches (union)" \
  "$(pl Grep "$phk" "$(gti "$phk_path" content)")"
printf 'x\n' >"$phk/outside/deep/y.txt"
printf 'outside/deep/y.txt\n' >"$phk/.claude/secret-globs"
hook_case 2 "A9 row 4b: an entry under the second root maps through the raw path's own physical view" \
  "$(pl Grep "$phk" "$(gti "$phk_path" content)")"
printf 'real/**\n' >"$phk/.claude/secret-globs"
hook_case 2 "A9 row 5: Read of a file beyond a symlinked dotdot resolves through the target" \
  "$(pl Read "$phk" "$(ti file_path "$phk/pk/L/sub/x/../z.txt")")"
mkdir -p "$phk/locked"; printf 'x\n' >"$phk/locked/f"; chmod 000 "$phk/locked"
hc 0 "A9 row 6: an unenterable directory has no physical view and is allowed" \
  "$(pl Grep "$phk" "$(gti "$phk/locked" content)")"
hc_done "$([[ ! -s "$sandbox/err" ]] || printf 'stderr not empty')"
chmod 755 "$phk/locked"
proj_reset

# ---------- A10: a huge path must not time out the universal check ----------
# old_normalize_path is a FROZEN copy of the quadratic function as it stood at c8b0bf9; the new one is
# extracted from the guard between its marker lines and compared with it over a seeded corpus.
old_normalize_path() {
  local path="$1" rest seg abs=0 n=0 joined="" i
  local -a out=()
  if [[ "$path" == /* ]]; then abs=1; fi
  rest="$path"
  while [[ -n "$rest" ]]; do
    seg="${rest%%/*}"
    if [[ "$rest" == */* ]]; then rest="${rest#*/}"; else rest=""; fi
    case "$seg" in
      ''|'.') continue ;;
      '..')
        if [[ "$n" -gt 0 && "${out[$((n - 1))]}" != '..' ]]; then
          unset "out[$((n - 1))]"; n=$((n - 1))
        elif [[ "$abs" -eq 1 ]]; then
          :
        else
          out[n]='..'; n=$((n + 1))
        fi ;;
      *) out[n]="$seg"; n=$((n + 1)) ;;
    esac
  done
  i=0
  while [[ "$i" -lt "$n" ]]; do
    if [[ "$i" -eq 0 ]]; then joined="${out[0]}"; else joined="$joined/${out[$i]}"; fi
    i=$((i + 1))
  done
  if [[ "$abs" -eq 1 ]]; then printf '/%s' "$joined"; else printf '%s' "$joined"; fi
}
# shellcheck source=/dev/null
source <(sed -n '/^# BEGIN normalize_path$/,/^# END normalize_path$/p' "$script" | sed 's/^normalize_path() {/new_normalize_path() {/')
# np_class <label> <inputs...> — old and new must print byte-identical output (NUL-separated) for every input.
np_class() {
  local label="$1" item n=0
  shift
  : >"$sandbox/np.old"; : >"$sandbox/np.new"
  for item in "$@"; do
    old_normalize_path "$item" >>"$sandbox/np.old"; printf '\0' >>"$sandbox/np.old"
    new_normalize_path "$item" >>"$sandbox/np.new"; printf '\0' >>"$sandbox/np.new"
    n=$((n + 1))
  done
  if [[ "$n" -ge 2000 && -s "$sandbox/np.old" ]] && cmp -s "$sandbox/np.old" "$sandbox/np.new"; then
    printf 'PASS  %s\n' "$label"; pass=$((pass + 1))
  else printf 'FAIL  %s (%d inputs, outputs differ or corpus too small)\n' "$label" "$n"; fail=$((fail + 1)); fi
}
# np_gen <min-segs> <max-segs> <lead> <sep> — appends one random path built from NP_POOL to NP_IN.
NP_IN=(); NP_POOL=()
np_gen() {
  local count=$(($1 + RANDOM % ($2 - $1 + 1))) i acc="$3"
  for ((i = 0; i < count; i++)); do
    if [[ $i -gt 0 ]]; then acc="$acc$4"; fi
    acc="$acc${NP_POOL[RANDOM % ${#NP_POOL[@]}]}"
  done
  NP_IN+=("$acc")
}
RANDOM=20261029
NP_POOL=(a b c . .. .. a)
NP_IN=(/ /.. /../.. /../a /a/.. /a/../.. /a/b/../../.. /./. /a/./b)
for ((k = 0; k < 2000; k++)); do np_gen 1 7 / /; done
np_class "A10 property: absolute paths with dot and dotdot, dotdot at the root" "${NP_IN[@]}"
NP_IN=(.. ../.. ../a a ./a a/.. a/../.. ../a/.. ../../a . a/./b/../..)
for ((k = 0; k < 2000; k++)); do np_gen 1 7 "" /; done
np_class "A10 property: relative paths with leading dotdot" "${NP_IN[@]}"
NP_POOL=(a b ab)
seps=(/ // /// /)
NP_IN=("" / // /// a/ a// /a/ //a// a//b///c /a// ///)
for ((k = 0; k < 2000; k++)); do
  acc=""; if (( RANDOM % 2 )); then acc="${seps[RANDOM % 4]}"; fi
  cnt=$((RANDOM % 5))
  for ((i = 0; i < cnt; i++)); do
    if (( i > 0 )); then acc="$acc${seps[RANDOM % 4]}"; fi
    acc="$acc${NP_POOL[RANDOM % 3]}"
  done
  if (( RANDOM % 2 )); then acc="$acc${seps[RANDOM % 4]}"; fi
  NP_IN+=("$acc")
done
np_class "A10 property: repeated and trailing slashes, empty and root" "${NP_IN[@]}"
NP_POOL=("~" "a b" $'x\ny' "~a" " " $'\n' "-n" "*" "a" "\\" "[a]")
# shellcheck disable=SC2088 # the tilde is a literal path segment here
NP_IN=("~" "~/a" "a b/c d" $'a\n/b' $'\n' " " $'x\ny/z')
for ((k = 0; k < 2000; k++)); do np_gen 1 6 "" /; done
np_class "A10 property: segments holding tilde, spaces, newlines and glob characters" "${NP_IN[@]}"

# Timing rows: a path just under the 65,536-byte field cap (A13 denies anything longer up front, so a longer
# path would pass these rows by being refused). The quadratic normalize_path of c8b0bf9 takes 23 s (bash 5)
# and 81 s (3.2) on the 60 KB path; the linear one about 1.2 s and 1.6 s. The 5 s bound is a few times the
# linear time; a wall-clock row can flake under machine load, so each prints its elapsed time (to stderr).
seg4="abc/"
for _ in $(seq 13); do seg4="$seg4$seg4"; done          # 2^13 x 4 bytes = 32 KiB
seg5="abc/"; for _ in $(seq 12); do seg5="$seg5$seg5"; done  # 2^12 x 4 bytes = 16 KiB
seg6="abc/"; for _ in $(seq 11); do seg6="$seg6$seg6"; done  # 2^11 x 4 bytes = 8 KiB
seg7="abc/"; for _ in $(seq 10); do seg7="$seg7$seg7"; done  # 2^10 x 4 bytes = 4 KiB
huge="$seg4$seg5$seg6$seg7"                              # 15,360 segments = 61,440 bytes
pair5="a/../"; hugep="$pair5"
for _ in $(seq 13); do hugep="$hugep$hugep"; done       # 2^13 x 5 bytes = 40 KiB of a/../ pairs
pair6="a/../"; for _ in $(seq 12); do pair6="$pair6$pair6"; done  # 20 KiB
hugep="$hugep$pair6"                                      # 61,440 bytes
# timed_read <want> <label> <path> — a Read of the huge path, with the wall-time bound of 5 s.
timed_read() {
  local t0 t1
  hook_reset; HOOK_CAP=30
  t0="$(now_ms)"
  hc "$1" "$2" "$(printf '{"tool_name":"Read","cwd":"/x","tool_input":{"file_path":"%s"}}' "$3")"
  t1="$(now_ms)"; HOOK_CAP=""
  printf 'time  %s: %s ms\n' "$2" "$((t1 - t0))" >&2
  hc_done "$([[ $((t1 - t0)) -lt 5000 ]] || printf 'wall time %s ms, not under 5000' "$((t1 - t0))")"
}
timed_read 2 "A10 timing: 60 KB path ending dot-env is denied in under 5 s" "/${huge}.env"
timed_read 0 "A10 timing: 60 KB path ending dot-env-example is allowed in under 5 s" "/${huge}.env.example"
timed_read 0 "A10 timing: 60 KB path ending plain.txt is allowed in under 5 s" "/${huge}plain.txt"
timed_read 2 "A10 timing: 60 KB path of a/../ pairs ending dot-env is denied in under 5 s" "/${hugep}.env"
# The same 60 KB path under /bin/bash 3.2 (a by-index walk of the split fields costs only about 0.3 s more there
# at this size, so a regression of that kind is bounded by the cap and is a documented campaign exclusion).
if [[ -x /bin/bash ]]; then
  hook_reset; HOOK_ENV=(PATH="$bash32dir:$PATH"); HOOK_CAP=30
  t0="$(now_ms)"
  hc 0 "A10 timing on bash 3.2: 60 KB path ending plain.txt is allowed in under 5 s" \
    "$(printf '{"tool_name":"Read","cwd":"/x","tool_input":{"file_path":"%s"}}' "/${huge}plain.txt")"
  t1="$(now_ms)"; HOOK_CAP=""
  printf 'time  bash 3.2 60 KB path: %s ms\n' "$((t1 - t0))" >&2
  hc_done "$([[ $((t1 - t0)) -lt 5000 ]] || printf 'wall time %s ms, not under 5000' "$((t1 - t0))")"
  HOOK_ENV=()
else
  printf 'PASS  bash 3.2 timing row skipped (no /bin/bash)\n'; pass=$((pass + 1))
fi
hook_reset
hook_case 0 ".env.example (early check keeps the exemption)" "$(pl Read "$repo" "$(ti file_path "$repo/.env.example")")"
hook_case 2 ".env.example with a trailing space is denied (the accepted over-block)" \
  "$(pl Read "$repo" "$(ti file_path "$repo/.env.example ")")"

# ---------- A11: SIGPIPE is ignored, so a broken stderr cannot turn a deny into an allow ----------
# pipe_case <want> <label> <payload> [argv...] — the REAL process with a stderr pipe whose read end is
# already closed; python reports the child's exit status (128 + n for death by signal n).
pipe_case() {
  local want="$1" label="$2" payload="$3" got="" code
  shift 3
  code='import os, subprocess, sys
r, w = os.pipe()
os.close(r)
rc = subprocess.run(sys.argv[1:], stdout=subprocess.DEVNULL, stderr=w).returncode
print(128 - rc if rc < 0 else rc)'
  got="$( cd "$HOOK_CWD" && printf '%s' "$payload" \
    | env -u RIPGREP_CONFIG_PATH HOME="$sandbox/home" XDG_CONFIG_HOME="$sandbox/home/.config" \
        CLAUDE_PROJECT_DIR="$HOOK_PROJ" ${HOOK_ENV[@]+"${HOOK_ENV[@]}"} python3 -c "$code" bash "$script" "$@" )"
  if [[ "$got" == "$want" ]]; then printf 'PASS  %s (exit %s)\n' "$label" "$got"; pass=$((pass + 1))
  else printf 'FAIL  %s (want %s, got %s)\n' "$label" "$want" "$got"; fail=$((fail + 1)); fi
}
hook_reset
pipe_case 2 "A11: a secret Read with a broken stderr pipe still exits 2" "$(pl Read "$repo" "$(ti file_path "$repo/.env")")"
pipe_case 2 "A11: the argv refusal with a broken stderr pipe still exits 2" "" some-file.txt
HOOK_ENV=(PATH="$(stub_dir pipesl rg 'exec sleep 5'):$PATH" GUARD_SECRETS_WALK_SECS=1)
pipe_case 2 "A11: an UNVERIFIED directory deny with a broken stderr pipe still exits 2" "$vpay"
hook_reset
pipe_case 0 "A11: an allowed Read of an ordinary file with a broken stderr pipe exits 0" \
  "$(pl Read "$repo" "$(ti file_path "$repo/plain.txt")")"

# ---------- A12: trim and basename extraction must be linear ----------
# A path padded with whitespace used to cost O(N^2) in the shell trim, which ran before any deny check:
# about 10,000 characters reached the 30 s harness timeout, and a timeout is an ALLOW. Each row below pads a
# real secret (or an ordinary file) with N characters and runs the REAL hook under HOOK_CAP = 30 s.
# a12_payload <tool> <kind> <n> <target> <cwd> — a JSON payload (Read file_path, or Grep path in content
# mode) whose path is <target> padded with N characters: lead (spaces before), trail (spaces after),
# nbsp (U+00A0 at both ends), mixed (U+00A0, U+3000, space, TAB cycling, at both ends).
a12_payload() {
  python3 -c 'import json, sys
tool, kind, n, target, cwd = sys.argv[1:6]
n = int(n)
cyc = [" ", "　", " ", "\t"]
pre = post = ""
if kind == "lead": pre = " " * n
elif kind == "trail": post = " " * n
elif kind == "nbsp": pre = post = " " * n
else: pre = post = "".join(cyc[i % 4] for i in range(n))
p = pre + target + post
ti = {"file_path": p} if tool == "Read" else {"path": p, "output_mode": "content", "pattern": "S"}
print(json.dumps({"hook_event_name": "PreToolUse", "cwd": cwd, "tool_name": tool, "tool_input": ti}))' "$@"
}
# a12_row <shell> <tool> <kind> <n> <bound-s> <want> <target> <label> — one timed row; shell is b5 or b32.
a12_row() {
  local sh="$1" tool="$2" kind="$3" n="$4" bound="$5" want="$6" target="$7" label="$8" pay t0 t1
  pay="$(a12_payload "$tool" "$kind" "$n" "$target" "$repo")"
  hook_reset; HOOK_CAP=30
  if [[ "$sh" == b32 ]]; then HOOK_ENV=(PATH="$bash32dir:$PATH"); fi
  t0="$(now_ms)"
  hc "$want" "$label" "$pay"
  t1="$(now_ms)"; HOOK_CAP=""
  printf 'time  %s: %s ms\n' "$label" "$((t1 - t0))" >&2
  hc_done "$([[ $((t1 - t0)) -lt $((bound * 1000)) ]] || printf 'wall time %s ms, not under %s s' "$((t1 - t0))" "$bound")"
  hook_reset
}
a12_shells="b5"; if [[ -x /bin/bash ]]; then a12_shells="b5 b32"; fi
for sh in $a12_shells; do
  for tool in Read Grep; do
    # Small-N controls (100 characters): they pass on the old code too.
    for kind in lead trail nbsp mixed; do
      a12_row "$sh" "$tool" "$kind" 100 3 2 "$repo/.env" "A12 control $sh $tool 100 $kind padding on a secret is denied"
    done
    # Large rows: the old trim does not finish inside the 30 s cap.
    a12_row "$sh" "$tool" lead 20000 3 2 "$repo/.env" "A12 timing $sh $tool 20000 leading spaces on a secret are denied"
    a12_row "$sh" "$tool" trail 20000 3 2 "$repo/.env" "A12 timing $sh $tool 20000 trailing spaces on a secret are denied"
    a12_row "$sh" "$tool" nbsp 5000 3 2 "$repo/.env" "A12 timing $sh $tool 5000 no-break spaces at each end of a secret are denied"
    a12_row "$sh" "$tool" mixed 20000 3 2 "$repo/.env" "A12 timing $sh $tool 20000 mixed padding at both ends of a secret is denied"
    a12_row "$sh" "$tool" lead 60000 3 2 "$repo/.env" "A12 timing $sh $tool 60000 leading spaces on a secret are denied"
    a12_row "$sh" "$tool" trail 60000 3 2 "$repo/.env" "A12 timing $sh $tool 60000 trailing spaces on a secret are denied"
    a12_row "$sh" "$tool" mixed 60000 3 2 "$repo/.env" "A12 timing $sh $tool 60000 mixed padding at both ends of a secret is denied"
  done
  # The allow path must not time out either (and the padded ordinary file stays allowed).
  a12_row "$sh" Read trail 60000 3 0 "$repo/plain.txt" "A12 timing $sh Read 60000 trailing spaces on an ordinary file are allowed"
done

# Class rows: every member of each group, as trailing and as leading padding on <repo>/.env, is denied (the
# raw basename of the trailing form is not a secret name, so only the trim catches it). One PASS line a row.
class_row() {
  local label="$1" m bad=0 n=0
  shift
  hook_reset
  for m in "$@"; do
    hook_rc "$(pl Read "$repo" "$(ti file_path "$repo/.env$m")")"; n=$((n + 1))
    [[ "$HOOK_RC" -eq 2 ]] || bad=$((bad + 1))
    hook_rc "$(pl Read "$repo" "$(ti file_path "$m$repo/.env")")"; n=$((n + 1))
    [[ "$HOOK_RC" -eq 2 ]] || bad=$((bad + 1))
  done
  if [[ "$bad" -eq 0 && "$n" -ge 2 ]]; then printf 'PASS  %s (%d payloads)\n' "$label" "$n"; pass=$((pass + 1))
  else printf 'FAIL  %s (%d of %d payloads not denied)\n' "$label" "$bad" "$n"; fail=$((fail + 1)); fi
}
class_row "A12 class: ASCII whitespace tab LF VT FF CR trims" $'\t' $'\n' $'\v' $'\f' $'\r'
class_row "A12 class: U+00A0 and U+1680 trim" $'\xc2\xa0' $'\xe1\x9a\x80'
class_row "A12 class: the U+2000 to U+200A range trims" $'\xe2\x80\x80' $'\xe2\x80\x81' $'\xe2\x80\x82' $'\xe2\x80\x83' $'\xe2\x80\x84' $'\xe2\x80\x85' $'\xe2\x80\x86' $'\xe2\x80\x87' $'\xe2\x80\x88' $'\xe2\x80\x89' $'\xe2\x80\x8a'
class_row "A12 class: U+2028 U+2029 U+202F U+205F U+3000 U+FEFF trim" $'\xe2\x80\xa8' $'\xe2\x80\xa9' $'\xe2\x80\xaf' $'\xe2\x81\x9f' $'\xe3\x80\x80' $'\xef\xbb\xbf'
hook_reset
hook_case 0 "A12 the exempt .env.example stays allowed" "$(pl Read "$repo" "$(ti file_path "$repo/.env.example")")"
hook_case 2 "A12 .env.example with a trailing space is denied by the raw check (the accepted over-block)" \
  "$(pl Read "$repo" "$(ti file_path "$repo/.env.example ")")"

# Differential property: a FROZEN copy of the shell trim (the oracle) against the jq trim the guard uses, over
# a seeded corpus of 3,000 strings (60 percent drawn from the 25 class members) plus fixed edge strings.
ORACLE_WS=(" " $'\t' $'\n' $'\v' $'\f' $'\r' $'\xc2\xa0' $'\xef\xbb\xbf' $'\xe1\x9a\x80' $'\xe2\x80\x80'
  $'\xe2\x80\x81' $'\xe2\x80\x82' $'\xe2\x80\x83' $'\xe2\x80\x84' $'\xe2\x80\x85' $'\xe2\x80\x86'
  $'\xe2\x80\x87' $'\xe2\x80\x88' $'\xe2\x80\x89' $'\xe2\x80\x8a' $'\xe2\x80\xa8' $'\xe2\x80\xa9'
  $'\xe2\x80\xaf' $'\xe2\x81\x9f' $'\xe3\x80\x80')
oracle_trim() {
  local str="$1" w again=1
  while (( again )); do
    again=0
    for w in "${ORACLE_WS[@]}"; do
      if [[ "$str" == "$w"* ]]; then str="${str#"$w"}"; again=1; fi
      if [[ "$str" == *"$w" ]]; then str="${str%"$w"}"; again=1; fi
    done
  done
  ORACLE_OUT="$str"
}
sed -n '/^# BEGIN jq_trim$/,/^# END jq_trim$/p' "$script" >"$sandbox/jq_trim.sh"
JQ_TRIM=""
# shellcheck source=/dev/null
source "$sandbox/jq_trim.sh"
others=(a b / . $'\xc3\xa9' $'\xe2\x80\x8b' $'\xe1\xa0\x8e' $'\xe2\x81\xa0' "x y")
RANDOM=20261030
DIFF_IN=("")
DIFF_IN+=(" " $'\xef\xbb\xbf' "   " $'\t\n\v' $'\xe3\x80\x80\xc2\xa0 ' $'\xc2\xa0a\xc2\xa0' $'a\xc2\xa0' $'\xc2\xa0a' "a b" $'x\ny')
for ((k = 0; k < 3000; k++)); do
  len=$((RANDOM % 13)); acc=""
  for ((i = 0; i < len; i++)); do
    if (( RANDOM % 10 < 6 )); then acc="$acc${ORACLE_WS[RANDOM % 25]}"; else acc="$acc${others[RANDOM % 9]}"; fi
  done
  DIFF_IN+=("$acc")
done
: >"$sandbox/diff.in"; : >"$sandbox/diff.oracle"; first=1
for item in "${DIFF_IN[@]}"; do
  if (( first )); then first=0; else printf '\0' >>"$sandbox/diff.in"; printf '\0' >>"$sandbox/diff.oracle"; fi
  printf '%s' "$item" >>"$sandbox/diff.in"
  oracle_trim "$item"; printf '%s' "$ORACLE_OUT" >>"$sandbox/diff.oracle"
done
jq -Rsj "$JQ_TRIM"' split("\u0000") | map(trim) | join("\u0000")' "$sandbox/diff.in" >"$sandbox/diff.jq" 2>/dev/null || true
if [[ -n "$JQ_TRIM" && "${#DIFF_IN[@]}" -ge 3000 && -s "$sandbox/diff.in" ]] && cmp -s "$sandbox/diff.oracle" "$sandbox/diff.jq"; then
  printf 'PASS  A12 differential: the jq trim equals the frozen shell trim over the seeded corpus\n'; pass=$((pass + 1))
else printf 'FAIL  A12 differential: the jq trim equals the frozen shell trim over the seeded corpus (%d inputs)\n' "${#DIFF_IN[@]}"; fail=$((fail + 1)); fi

# ---------- A13: no escape-and-restore, NUL-delimited fields, byte caps on the path-like fields ----------
# The extraction used to be quadratic (a jq gsub over newlines, then a bash restore that is superlinear on
# 3.2): LF padding reached the 30 s timeout, which is an ALLOW. The fields now come out of one jq call as
# NUL-terminated strings, and a path-like field over its byte cap is denied before any other step.
# a13_payload <kind> <n> <repo> — a JSON payload; kinds: lf (Read: n LFs then <repo>/.env), lfgrep (the same as a
# content Grep path), slash (Read: n slashes then <repo>/.env), exact (Read: a <repo>-less path of exactly n
# bytes ending /plain.txt), glob (content Grep of <repo>/specs with a glob of n bytes), cwd (Read plain.txt
# with a cwd of exactly n bytes), nulcwd (a NUL in cwd ahead of a path naming a symlink to .env), nulpath (a
# NUL inside .env), lftrail (Read: .env then n LFs), write (Write of n bytes of content to <repo>/.env), writeok
# (the same on <repo>/plain.txt), pad20 (20 spaces then <repo>/plain.txt).
a13_payload() {
  python3 -c 'import json, sys
kind, n, repo = sys.argv[1], int(sys.argv[2]), sys.argv[3]
cwd = repo
if kind == "lf": ti, tool = {"file_path": "\n" * n + repo + "/.env"}, "Read"
elif kind == "lfgrep": ti, tool = {"path": "\n" * n + repo + "/.env", "output_mode": "content", "pattern": "S"}, "Grep"
elif kind == "slash": ti, tool = {"file_path": "/" * n + repo + "/.env"}, "Read"
elif kind == "exact": ti, tool = {"file_path": "/" + "a" * (n - len("/plain.txt") - 1) + "/plain.txt"}, "Read"
elif kind == "glob": ti, tool = {"path": repo + "/specs", "output_mode": "content", "pattern": "S", "glob": "a" * n}, "Grep"
elif kind == "cwd": ti, tool, cwd = {"file_path": repo + "/plain.txt"}, "Read", "/" + "a" * (n - 1)
elif kind == "nulcwd": ti, tool, cwd = {"file_path": repo + "/innocent.txt"}, "Read", "/x\u0000y"
elif kind == "nulpath": ti, tool = {"file_path": repo + "/.e\u0000nv"}, "Read"
elif kind == "omode": ti, tool = {"path": repo + "/.env", "output_mode": "content" + " " * n, "pattern": "S"}, "Grep"
elif kind == "omodedir": ti, tool = {"path": repo + "/dotonly", "output_mode": "content" + " " * n, "pattern": "S"}, "Grep"
elif kind == "omodenul": ti, tool = {"path": repo + "/dotonly", "output_mode": "content" + "\u0000" * n, "pattern": "S"}, "Grep"
elif kind == "omodeup": ti, tool = {"path": repo + "/dotonly", "output_mode": "CONTENT", "pattern": "S"}, "Grep"
elif kind == "omodefwm": ti, tool = {"path": repo + "/dotonly", "output_mode": "files_with_matches", "pattern": "S"}, "Grep"
elif kind == "omodeint": ti, tool = {"path": repo + "/dotonly", "output_mode": 7, "pattern": "S"}, "Grep"
elif kind == "tname": ti, tool = {"path": repo + "/dotonly", "output_mode": "content", "pattern": "S"}, "Grep" + " " * n
elif kind == "tnamelow": ti, tool = {"path": repo + "/dotonly", "output_mode": "content", "pattern": "S"}, "grep"
elif kind == "tnameread": ti, tool = {"path": repo + "/dotonly", "output_mode": "content", "pattern": "S"}, "Read"
elif kind == "tnamenum": ti, tool = {"path": repo + "/dotonly", "output_mode": "content", "pattern": "S"}, 7
elif kind == "type": ti, tool = {"path": repo + "/specs", "output_mode": "content", "pattern": "S", "type": "a" * n}, "Grep"
elif kind == "nulbig": ti, tool = {"file_path": repo + "/.env", "output_mode": "\u0000" * n}, "Read"
elif kind == "ceil":
    pad = "/" * (65000 - len(repo))
    ti, tool, cwd = {"path": repo + pad + "/specs", "output_mode": "content", "pattern": "S"}, "Grep", repo + pad
elif kind == "lftrail": ti, tool = {"file_path": repo + "/.env" + "\n" * n}, "Read"
elif kind == "write": ti, tool = {"file_path": repo + "/.env", "content": "x" * n}, "Write"
elif kind == "writeok": ti, tool = {"file_path": repo + "/plain.txt", "content": "x" * n}, "Write"
else: ti, tool = {"file_path": " " * n + repo + "/plain.txt"}, "Read"
print(json.dumps({"hook_event_name": "PreToolUse", "cwd": cwd, "tool_name": tool, "tool_input": ti}))' "$@"
}
# a13_row <shell> <kind> <n> <bound-s> <want> <label> [must] — one timed row under HOOK_CAP = 30 s.
a13_row() {
  local sh="$1" kind="$2" n="$3" bound="$4" want="$5" label="$6" must="${7:-}" pay t0 t1
  pay="$(a13_payload "$kind" "$n" "$repo")"
  hook_reset; HOOK_CAP=30
  if [[ "$sh" == b32 ]]; then HOOK_ENV=(PATH="$bash32dir:$PATH"); fi
  t0="$(now_ms)"
  hc "$want" "$label" "$pay" "$must"
  t1="$(now_ms)"; HOOK_CAP=""
  printf 'time  %s: %s ms\n' "$label" "$((t1 - t0))" >&2
  hc_done "$([[ $((t1 - t0)) -lt $((bound * 1000)) ]] || printf 'wall time %s ms, not under %s s' "$((t1 - t0))" "$bound")"
  hook_reset
}
for sh in $a12_shells; do
  # LF padding: 8,000 LFs took 26 s on 3.2 before; 60,000 timed out on both.
  a13_row "$sh" lf 8000 3 2 "A13 $sh Read 8000 LFs before a secret are denied"
  a13_row "$sh" lf 60000 3 2 "A13 $sh Read 60000 LFs before a secret are denied"
  a13_row "$sh" lfgrep 8000 3 2 "A13 $sh Grep 8000 LFs before a secret are denied"
  a13_row "$sh" lfgrep 60000 3 2 "A13 $sh Grep 60000 LFs before a secret are denied"
  # Over the cap: denied up front, with the oversize line naming the field (never echoing it).
  a13_row "$sh" lf 5000000 10 2 "A13 $sh Read 5000000 LFs are denied as an oversize path" "BLOCKED path field of $((5000000 + ${#repo} + 5)) bytes (cap 65536): no file tool can open a path that long"
  a13_row "$sh" slash 1000000 10 2 "A13 $sh Read 1000000 slashes are denied as an oversize path" "BLOCKED path field of $((1000000 + ${#repo} + 5)) bytes (cap 65536): no file tool can open a path that long"
  a13_row "$sh" exact 65537 10 2 "A13 $sh a 65537-byte path is denied as an oversize path" "BLOCKED path field of 65537 bytes (cap 65536): no file tool can open a path that long"
  a13_row "$sh" exact 65536 10 0 "A13 $sh a 65536-byte path is allowed (the threshold control)"
  a13_row "$sh" glob 4097 10 2 "A13 $sh a 4097-byte glob is denied as an oversize glob" "BLOCKED glob field of 4097 bytes (cap 4096): a glob that long is not a pattern a Grep takes"
  a13_row "$sh" glob 4097 10 2 "A13 $sh a 4097-byte glob exits 2 whatever the message says"
  a13_row "$sh" glob 4096 10 0 "A13 $sh a 4096-byte glob is allowed (the threshold control)"
  a13_row "$sh" cwd 65537 10 2 "A13 $sh a 65537-byte cwd is denied as an oversize cwd" "BLOCKED cwd field of 65537 bytes (cap 65536): no file tool can open a path that long"
  a13_row "$sh" cwd 65536 10 0 "A13 $sh a 65536-byte cwd is allowed (the threshold control)"
  # A NUL inside a field no longer ends it early (it is removed): still judged.
  a13_row "$sh" nulcwd 0 10 2 "A13 $sh a NUL in cwd does not empty a path naming a symlink to a secret"
  a13_row "$sh" nulpath 0 10 2 "A13 $sh a NUL inside the file name is removed and the name still judged"
  # Content is NOT capped: a 50 MB Write is judged by its target alone.
  a13_row "$sh" write 50000000 10 2 "A13 $sh a 50 MB Write naming a secret is denied"
  a13_row "$sh" writeok 50000000 10 0 "A13 $sh a 50 MB Write naming an ordinary file is allowed"
  # The type cap, one fixture each side.
  a13_row "$sh" type 4097 10 2 "A14 $sh a 4097-byte type is denied as an oversize type" "BLOCKED type field of 4097 bytes (cap 4096): a type that long is not a file type a Grep takes"
  a13_row "$sh" type 4097 10 2 "A14 $sh a 4097-byte type exits 2 whatever the message says"
  a13_row "$sh" type 4096 10 0 "A14 $sh a 4096-byte type is allowed (the threshold control)"
  # tool_name and output_mode are decided inside jq: a huge value is one short constant by the time bash reads it.
  a13_row "$sh" omode 40000000 12 2 "A14 $sh 40000000 spaces after output_mode content still deny a secret Grep path in time"
  a13_row "$sh" omodedir 5000000 2 0 "A14 $sh 5000000 spaces after output_mode content are not content mode (a directory is allowed, in time)"
  a13_row "$sh" tname 5000000 2 0 "A14 $sh 5000000 spaces after the tool name are not Grep (a directory is allowed, in time)"
  a13_row "$sh" omodenul 1000 3 2 "A14 $sh output_mode content followed by NULs is still content mode"
  a13_row "$sh" omodenul 1000000 10 2 "A14 $sh output_mode content followed by 1000000 NULs is still content mode, in time"
  a13_row "$sh" omodeup 0 3 2 "A14 $sh output_mode CONTENT matches case-insensitively as before"
  a13_row "$sh" tnamelow 0 3 2 "A14 $sh tool name grep matches case-insensitively as before"
  a13_row "$sh" omodefwm 0 3 0 "A14 $sh output_mode files_with_matches is not content mode"
  a13_row "$sh" omodeint 0 3 0 "A14 $sh a non-string output_mode is not content mode"
  a13_row "$sh" tnameread 0 3 0 "A14 $sh tool name Read gets no directory logic"
  a13_row "$sh" tnamenum 0 3 0 "A14 $sh a non-string tool name gets no directory logic"
  # The deny text names what to do for the field that tripped: a type is not a glob.
  a13_row "$sh" glob 4097 10 2 "$sh oversize glob: the second line tells a Grep to keep glob to the patterns it needs" "keep glob to the patterns you need"
  a13_row "$sh" type 4097 10 2 "$sh oversize type: the second line tells a Grep to use one file type name" "use one file type name"
  # A big NUL-only output_mode on a secret Read is stripped in linear time with a small constant (the join
  # form cost 8 s at 4 million NULs, 24 MB of payload, on either shell: the row's 5 s bound sits well under
  # that and well over the 2 to 3 s the add-based strip takes).
  a13_row "$sh" nulbig 4000000 5 2 "$sh a 4000000-NUL output_mode on a secret Read is denied in time"
  # Controls.
  a13_row "$sh" pad20 20 3 0 "A13 $sh 20 spaces before an ordinary file are allowed"
  a13_row "$sh" lftrail 3 3 2 "A13 $sh a secret followed by 3 LFs is denied"
done

# The whole hook ends by a fixed ceiling at the documented maximum walk bound: a fake rg that lists two million
# names and then hangs, a directory Grep whose path and cwd are padded to the cap (the slow pre-walk work counts),
# GUARD_SECRETS_WALK_SECS=20. Before the ceiling the pre-walk work, the walk bound and a 3 s grace scan simply
# added up to 27 to 30 s of the harness's 30 s (a timeout is an allow).
ceil_list="$sandbox/ceil.list"
python3 -c 'import sys; sys.stdout.write("".join("/x/d/file%07d.txt\0" % i for i in range(2000000)))' >"$ceil_list"
ceil_stub="$(stub_dir ceilrg rg "cat '$ceil_list'; exec sleep 100")"
ceil_row() {
  local sh="$1" label="$2" pay t0 t1
  pay="$(a13_payload ceil 0 "$repo")"
  hook_reset; HOOK_CAP=40
  if [[ "$sh" == b32 ]]; then HOOK_ENV=(PATH="$bash32dir:$ceil_stub:$PATH" GUARD_SECRETS_WALK_SECS=20)
  else HOOK_ENV=(PATH="$ceil_stub:$PATH" GUARD_SECRETS_WALK_SECS=20); fi
  t0="$(now_ms)"
  hc 2 "$label" "$pay" "UNVERIFIED (enumeration exceeded 20 s)"
  t1="$(now_ms)"; HOOK_CAP=""
  printf 'time  %s: %s ms\n' "$label" "$((t1 - t0))" >&2
  hc_done "$([[ $((t1 - t0)) -lt 24000 ]] || printf 'wall time %s ms, not under 24000' "$((t1 - t0))")"
  hook_reset
}
for sh in $a12_shells; do
  ceil_row "$sh" "the whole hook ends under 24 s at the maximum walk bound ($sh)"
done

# ---------- A15: a lone high surrogate is repaired and judged; an inherited SECONDS is discarded ----------
# A lone high-surrogate escape (JSON text: a backslash, u, d800) makes jq refuse the WHOLE payload, so the guard used
# to exit 0 having read nothing; the retry in the guard rewrites exactly those escapes to U+FFFD and extracts again.
# An exported SECONDS is taken by both shells as the counter's start, so the guard resets it before anything else.
# Fixtures: a valid pair is NEVER spelled as escape text in this file (the tool channel that writes files decodes a
# valid pair into the literal character), it is built from pieces; heavy payloads are built by the row at run time.
# Row ids (D1a to D1n, G1 to G6, G6b, G9, S1 to S6) are the second word of the labels below.
BS=$'\\'
LONE="${BS}ud800"
PAIR="${BS}ud83d${BS}ude00"
FFFD="${BS}ufffd"
# sg_pay <cwd> <tool> <tool_input-json-text> — a payload whose escapes stay escapes (jq -n would decode them).
sg_pay() { printf '{"hook_event_name":"PreToolUse","cwd":"%s","tool_name":"%s","tool_input":%s}' "$1" "$2" "$3"; }
# sg_case <shell: b5|b32> <want> <label> <payload> [must] — one hook_case under the named shell.
sg_case() {
  local sh="$1"; shift
  hook_reset
  if [[ "$sh" == b32 ]]; then HOOK_ENV=(PATH="$bash32dir:$PATH"); fi
  hook_case "$1" "$2 ($sh)" "$3" "${4:-}"
  hook_reset
}
# sg_pcase <want> <label> <payload> [must] — one hook_case from the project fixture (its secret-globs file), b5.
sg_pcase() { proj_reset; hook_case "$1" "$2 (b5)" "$3" "${4:-}"; hook_reset; }
# a15_payload <kind> — heavy payloads, built at run time (nothing large is committed). The lone escape is last.
#   cap_u3   exactly 16,777,216 bytes: Write of $repo/.env, content = units of three backslashes + ud800
#   capover  16,777,217 bytes: the same envelope with plain content, one byte over the cap
a15_payload() {
  python3 -c 'import sys
kind, repo = sys.argv[1], sys.argv[2]
B = "\\"; lone = B + "ud800"; cap = 16 * 1024 * 1024
pre = "{\"tool_name\":\"Write\",\"cwd\":\"" + repo + "\",\"tool_input\":{\"file_path\":\"" + repo + "/.env\",\"content\":\""
suf = lone + "\"}}"
def fill(n, unit):
    k = n // len(unit); return unit * k + "a" * (n - k * len(unit))
if kind == "cap_u3": out = pre + fill(cap - len(pre) - len(suf), B * 3 + "ud800") + suf
elif kind == "capover": out = pre + "a" * (cap - len(pre) - len(suf) + 1) + suf
else: raise SystemExit("unknown kind " + kind)
sys.stdout.write(out)' "$@"
}
# a15_timed <shell> <want> <bound-s> <label> <payload> — a timed hook_case (HOOK_CAP 30), HOOK_ENV extras kept.
a15_timed() {
  local sh="$1" want="$2" bound="$3" label="$4" pay="$5" t0 t1 envx=("${HOOK_ENV[@]+"${HOOK_ENV[@]}"}")
  hook_reset; HOOK_CAP=30
  if [[ "$sh" == b32 ]]; then HOOK_ENV=(PATH="$bash32dir:$PATH" ${envx[@]+"${envx[@]}"}); else HOOK_ENV=(${envx[@]+"${envx[@]}"}); fi
  t0="$(now_ms)"
  hc "$want" "$label ($sh)" "$pay"
  t1="$(now_ms)"; HOOK_CAP=""
  printf 'time  %s (%s): %s ms\n' "$label" "$sh" "$((t1 - t0))" >&2
  hc_done "$([[ $((t1 - t0)) -lt $((bound * 1000)) ]] || printf 'wall time %s ms, not under %s s' "$((t1 - t0))" "$bound")"
  hook_reset
}

# ---- Defect 1, hook-level rows: before the repair existed each of these wanting 2 got 0, the jq failure having become an allow ----
sg_case b5 2 "A15 D1a a lone high in Write content: the .env target is still read" "$(sg_pay "$repo" Write "{\"file_path\":\"$repo/.env\",\"content\":\"a${LONE}b\"}")" ".env"
sg_case b5 2 "A15 D1c a lone high in a Grep pattern: the directory holding .env is still enumerated" "$(sg_pay "$repo" Grep "{\"pattern\":\"S|${LONE}\",\"path\":\"$repo/dotonly\",\"output_mode\":\"content\"}")" "would return secret content"
sg_case b5 2 "A15 D1d a lone high inside the NAME: a${LONE}.key is a .key file" "$(sg_pay "$repo" Read "{\"file_path\":\"$repo/a${LONE}.key\"}")" "BLOCKED"
sg_case b5 2 "A15 D1b a lone high in Edit new_string: the .env target is still read" "$(sg_pay "$repo" Edit "{\"file_path\":\"$repo/.env\",\"old_string\":\"a\",\"new_string\":\"${LONE}\"}")" ".env"
sg_case b5 2 "A15 D1e a lone high as a KEY: the .env target is still read" "$(sg_pay "$repo" Write "{\"file_path\":\"$repo/.env\",\"content\":\"a\",\"x${LONE}\":1}")" ".env"
sg_case b5 2 "A15 D1f a valid pair then a lone high in content: the .env target is still read" "$(sg_pay "$repo" Write "{\"file_path\":\"$repo/.env\",\"content\":\"${PAIR}${LONE}\"}")" ".env"
sg_case b5 2 "A15 D1g a lone high at the top of the range (lowercase udbff)" "$(sg_pay "$repo" Write "{\"file_path\":\"$repo/.env\",\"content\":\"${BS}udbff\"}")" ".env"
sg_case b5 2 "A15 D1h a lone high with an uppercase D (uD800)" "$(sg_pay "$repo" Write "{\"file_path\":\"$repo/.env\",\"content\":\"${BS}uD800\"}")" ".env"
sg_case b5 2 "A15 D1i the retried parse reaches the oversize check (a 65,537-byte path)" "$(python3 -c 'import sys
B = "\\"; repo = sys.argv[1]; tail = repo + "/plain.txt"
print("{\"cwd\":\"" + repo + "\",\"tool_name\":\"Read\",\"tool_input\":{\"file_path\":\"" + "/" * (65537 - len(tail)) + tail + "\",\"z\":\"" + B + "ud800\"}}", end="")' "$repo")" "BLOCKED path field"
sg_case b5 2 "A15 D1j a lone high in a Grep glob: the glob is blanked (widened) and the directory is still read" "$(sg_pay "$repo" Grep "{\"pattern\":\"S\",\"path\":\"$repo/dotonly\",\"output_mode\":\"content\",\"glob\":\"*x${LONE}\"}")" "would return secret content"
# Project-glob rows: the decoded NAME is what the globs see, so these pin what the repair writes (D1k, D1l, D1m, below).
set_globs '*ud800*\n'
sg_pcase 2 "A15 D1k an escaped backslash before ud800 is NOT rewritten (the decoded name keeps the letters)" "$(sg_pay "$proj" Write "{\"file_path\":\"$proj/n${BS}${BS}ud800.txt\",\"content\":\"a${LONE}\"}")" "Project secret glob"
set_globs '*\xf0\x9f\x98\x80*\n'
sg_pcase 2 "A15 D1l a valid pair survives the repair as one character (its four bytes are in the name)" "$(sg_pay "$proj" Write "{\"file_path\":\"$proj/p${PAIR}q.txt\",\"content\":\"a${LONE}\"}")" "Project secret glob"
set_globs '*\xef\xbf\xbd*\n'
sg_pcase 2 "A15 D1m a lone high becomes U+FFFD (three bytes), not nothing" "$(sg_pay "$proj" Write "{\"file_path\":\"$proj/n${LONE}.txt\",\"content\":\"a\"}")" "Project secret glob"
rm -f "$proj/.claude/secret-globs"; hook_reset
sg_case b5 2 "A15 D1n an odd run of backslashes: the LAST escape is rewritten (name n, backslash, U+FFFD, .key)" "$(sg_pay "$repo" Read "{\"file_path\":\"$repo/n${BS}${BS}${LONE}.key\"}")" "BLOCKED"

# ---- Defect 1, controls: green on the unchanged guard and after the fix ----
sg_case b5 2 "A15 G1 a valid pair in Write content: the .env target is denied" "$(sg_pay "$repo" Write "{\"file_path\":\"$repo/.env\",\"content\":\"${PAIR}\"}")" ".env"
sg_case b5 0 "A15 G2 a lone high in the content of a Write to an ordinary file is allowed" "$(sg_pay "$repo" Write "{\"file_path\":\"$repo/plain.txt\",\"content\":\"a${LONE}b\"}")"
sg_case b5 2 "A15 G3 a lone LOW in Write content: the .env target is denied (jq already parses it)" "$(sg_pay "$repo" Write "{\"file_path\":\"$repo/.env\",\"content\":\"${BS}udc00\"}")" ".env"
sg_case b5 0 "A15 G4 an escaped backslash then ud800 in an Edit of an ordinary file is allowed" "$(sg_pay "$repo" Edit "{\"file_path\":\"$repo/plain.txt\",\"old_string\":\"a\",\"new_string\":\"${BS}${BS}ud800\"}")"
sg_case b5 0 "A15 G5 a Read of .env then a lone high names .env plus U+FFFD, which is not a secret" "$(sg_pay "$repo" Read "{\"file_path\":\"$repo/.env${LONE}\"}")"
sg_case b5 0 "A15 G6 the literal text garbage is allowed" "garbage"
sg_case b5 0 "A15 G6b a truncated JSON object is allowed" "{\"tool_input\":{\"file_path\":\"$repo/.env\""
HOOK_ENV=(PATH="$nojq"); hook_case 2 "A15 G9 jq absent: the sed fallback verdict for a lone high is unchanged and needs no perl" "$(sg_pay "$repo" Write "{\"file_path\":\"$repo/.env\",\"content\":\"a${LONE}\"}")" ".env"; hook_reset
HOOK_ENV=(PATH="$(stub_dir failperl perl 'exit 1'):$PATH")
hook_case 0 "A15 a perl that fails leaves today's behaviour (allow), without a crash" "$(sg_pay "$repo" Write "{\"file_path\":\"$repo/.env\",\"content\":\"a${LONE}\"}")"; hook_reset
HOOK_ENV=(PATH="$(no_rg_path noperl)")
hook_case 0 "A15 a host without perl keeps today's behaviour (allow): the documented residual" "$(sg_pay "$repo" Write "{\"file_path\":\"$repo/.env\",\"content\":\"a${LONE}\"}")"; hook_reset

# ---- Defect 1, sanitizer level: the gs_sanitize region run in a fresh bash (as walk_secs_case does) ----
san_fn="$(sed -n '/^# BEGIN gs_sanitize$/,/^# END gs_sanitize$/p' "$script")"
# san_run <input-file> [VAR=value ...] — sets SAN_RC; the output is in $sandbox/san.out.
san_run() {
  local f="$1"; shift
  env "$@" bash -c "$san_fn"$'\n'"gs_sanitize" <"$f" >"$sandbox/san.out" 2>/dev/null; SAN_RC=$?
}
# san_case <label> <input> <want-output> — exact text equality, rc 0.
san_case() {
  local label="$1" got
  printf '%s' "$2" >"$sandbox/san.in"; san_run "$sandbox/san.in"; got="$(<"$sandbox/san.out")"
  if [[ "$SAN_RC" -eq 0 && "$got" == "$3" ]]; then printf 'PASS  %s\n' "$label"; pass=$((pass + 1))
  else printf 'FAIL  %s (rc %s, got %s)\n' "$label" "$SAN_RC" "${got:0:60}"; fail=$((fail + 1)); fi
}
if [[ -n "$san_fn" ]]; then printf 'PASS  A15 the gs_sanitize marker region exists in the guard\n'; pass=$((pass + 1))
else printf 'FAIL  A15 the gs_sanitize marker region exists in the guard (region not found)\n'; fail=$((fail + 1)); fi
san_case "A15 san: a lone high becomes the escape for U+FFFD" "$LONE" "$FFFD"
san_case "A15 san: a valid pair is unchanged" "$PAIR" "$PAIR"
san_case "A15 san: a high followed by a valid pair: only the first is rewritten" "${LONE}${PAIR}" "${FFFD}${PAIR}"
san_case "A15 san: a high then a high: both are lone" "${LONE}${LONE}" "${FFFD}${FFFD}"
san_case "A15 san: low then high: only the high is rewritten" "${BS}udc00${LONE}" "${BS}udc00${FFFD}"
san_case "A15 san: an escaped backslash then ud800 is unchanged" "${BS}${BS}ud800" "${BS}${BS}ud800"
san_case "A15 san: three backslashes then ud800: only the final escape is rewritten" "${BS}${BS}${BS}ud800" "${BS}${BS}${BS}ufffd"
san_case "A15 san: four backslashes then ud800 is unchanged" "${BS}${BS}${BS}${BS}ud800" "${BS}${BS}${BS}${BS}ud800"
bs1000="$(head -c 1000 /dev/zero | tr '\0' '\134')"
san_case "A15 san: 1,001 backslashes then ud800: the final escape is rewritten" "${bs1000}${BS}ud800" "${bs1000}${BS}ufffd"
san_case "A15 san: 1,000 backslashes then ud800 is unchanged" "${bs1000}ud800" "${bs1000}ud800"
san_case "A15 san: a capital U is not an escape and is unchanged" "${BS}UD800" "${BS}UD800"
san_case "A15 san: a lone low is unchanged (jq parses it)" "${BS}udc00" "${BS}udc00"
san_case "A15 san: mixed-case hex at the top of the range is rewritten" "${BS}uDbFf" "$FFFD"
san_case "A15 san: text with no backslash is unchanged" "abc ud800 {}" "abc ud800 {}"
head -c 16777216 /dev/zero | tr '\0' a >"$sandbox/san.cap"
san_run "$sandbox/san.cap"
if [[ "$SAN_RC" -eq 0 && "$(wc -c <"$sandbox/san.out" | tr -d ' ')" == 16777216 ]]; then printf 'PASS  A15 san: input of exactly 16,777,216 bytes is processed\n'; pass=$((pass + 1))
else printf 'FAIL  A15 san: input of exactly 16,777,216 bytes is processed (rc %s)\n' "$SAN_RC"; fail=$((fail + 1)); fi
head -c 16777217 /dev/zero | tr '\0' a >"$sandbox/san.cap"
san_run "$sandbox/san.cap"
if [[ "$SAN_RC" -eq 3 && ! -s "$sandbox/san.out" ]]; then printf 'PASS  A15 san: input of 16,777,217 bytes gives no output and exit 3\n'; pass=$((pass + 1))
else printf 'FAIL  A15 san: input of 16,777,217 bytes gives no output and exit 3 (rc %s)\n' "$SAN_RC"; fail=$((fail + 1)); fi
python3 -c 'import sys; sys.stdout.buffer.write("\U0001F600".encode() * 4200000)' >"$sandbox/san.cap"
san_run "$sandbox/san.cap" PERL_UNICODE=SD
if [[ "$SAN_RC" -eq 3 && ! -s "$sandbox/san.out" ]]; then printf 'PASS  A15 san: under PERL_UNICODE=SD the cap still counts bytes (4,200,000 four-byte characters decline)\n'; pass=$((pass + 1))
else printf 'FAIL  A15 san: under PERL_UNICODE=SD the cap still counts bytes (rc %s)\n' "$SAN_RC"; fail=$((fail + 1)); fi
# The cap is on the whole slurped record: a multi-line input over the cap (every line 17 bytes) is declined too.
yes aaaaaaaaaaaaaaaa | head -c 16777300 >"$sandbox/san.cap"
san_run "$sandbox/san.cap"
if [[ "$SAN_RC" -eq 3 && ! -s "$sandbox/san.out" ]]; then printf 'PASS  A15 san: a multi-line input over the cap is declined as one record (exit 3)\n'; pass=$((pass + 1))
else printf 'FAIL  A15 san: a multi-line input over the cap is declined as one record (rc %s)\n' "$SAN_RC"; fail=$((fail + 1)); fi
rm -f "$sandbox/san.cap" "$sandbox/san.in"
# The property, over inputs nobody listed: the output parses, equals an independent model, changes nothing else.
a15_property() {
  SAN_FN="$san_fn" python3 -c 'import os, re, json, random, subprocess, sys
B = "\\"
fn = os.environ["SAN_FN"]
if not fn: print("FAIL gs_sanitize region not found"); sys.exit(0)
def sanitize(text):
    r = subprocess.run(["bash", "-c", fn + "\ngs_sanitize"], input=text.encode(), capture_output=True)
    return r.stdout.decode()
HEX = re.compile(r"[0-9a-fA-F]{4}")
def model(s):
    out = []; i = 0; lone = False
    while i < len(s):
        if s[i] == B and i + 1 < len(s):
            if s[i + 1] == B: out.append(s[i:i + 2]); i += 2; continue
            if s[i + 1] == "u" and HEX.fullmatch(s[i + 2:i + 6]):
                h = s[i + 2:i + 6].lower()
                if "d800" <= h <= "dbff":
                    nx = s[i + 6:i + 12]
                    pair = len(nx) == 6 and nx[:2] == B + "u" and HEX.fullmatch(nx[2:]) and "dc00" <= nx[2:].lower() <= "dfff"
                    if pair: out.append(s[i:i + 6])
                    else: out.append(B + "ufffd"); lone = True
                    i += 6; continue
            out.append(s[i]); i += 1; continue
        out.append(s[i]); i += 1
    return "".join(out), lone
def norm(v):
    if isinstance(v, str): return re.sub("[\ud800-\udfff]", chr(0xfffd), v)
    if isinstance(v, list): return [norm(x) for x in v]
    if isinstance(v, dict): return {norm(k): norm(x) for k, x in v.items()}
    return v
atoms = [B * 2, B * 3, B * 4, B + "ud800", B + "uD800", B + "udbff", B + "uDBFF", B + "uDbFf", B + "udc00", B + "uDFFF",
         B + "ud83d", B + "ude00", B + "u0041", B + "n", B + chr(34), "a", "u", "d", "8", "0", B + "/", B + "ud8", B + "u", " ", "."]
random.seed(7); docs = []
while len(docs) < 1500:
    s = "".join(random.choice(atoms) for _ in range(random.randint(0, 14)))
    d = "{\"k\":\"" + s + "\",\"tool_input\":{\"file_path\":\"/x/" + s + "\"}}"
    try: json.loads(d)
    except Exception: continue
    docs.append(d)
out = sanitize("\n".join(docs)).split("\n")
if len(out) != len(docs): print("FAIL output line count %d != %d" % (len(out), len(docs))); sys.exit(0)
j = subprocess.run(["jq", "-c", "."], input="\n".join(out).encode(), capture_output=True)
lines = j.stdout.decode().split("\n")[:-1]
if j.returncode != 0 or len(lines) != len(docs): print("FAIL jq rc %d, %d lines for %d documents" % (j.returncode, len(lines), len(docs))); sys.exit(0)
lone_n = 0
for d, o, l in zip(docs, out, lines):
    exp, lone = model(d); lone_n += lone
    if o != exp: print("FAIL text differs from the model: %r" % d); sys.exit(0)
    if len(o.encode()) != len(d.encode()): print("FAIL length changed: %r" % d); sys.exit(0)
    if json.loads(l) != norm(json.loads(d)): print("FAIL decoded value differs: %r" % d); sys.exit(0)
    if not lone and o != d: print("FAIL a document without a lone high changed: %r" % d); sys.exit(0)
if sanitize("\n".join(out)).split("\n") != out: print("FAIL not idempotent"); sys.exit(0)
if lone_n < 300 or len(docs) - lone_n < 300: print("FAIL generator floors: %d with a lone high, %d without" % (lone_n, len(docs) - lone_n)); sys.exit(0)
for d in docs[:300]:
    rejected = subprocess.run(["jq", "-c", "."], input=d.encode(), capture_output=True).returncode != 0
    if rejected != model(d)[1]: print("FAIL the model disagrees with jq on %r" % d); sys.exit(0)
print("OK %d documents, %d with a lone high" % (len(docs), lone_n))'
}
prop="$(a15_property)"
if [[ "$prop" == OK* ]]; then printf 'PASS  A15 san property: output parses, equals the model, changes nothing else (%s)\n' "$prop"; pass=$((pass + 1))
else printf 'FAIL  A15 san property (%s)\n' "$prop"; fail=$((fail + 1)); fi

# ---- Defect 1, heavy rows (HOOK_CAP 30; bounds are 2.4 to 6 times the idle measurements) ----
# One at-cap row (b32, among the densest shapes) and one over-cap row, because the whole suite runs under a PostToolUse
# hook with a 300 s timeout (about 235 s, measured, with these rows). Left out as duplicates or too costly: a b5 at-cap
# row, a 40 MiB valid payload row, and hook-level pretty-printed and PERL_UNICODE rows (the last two are pinned at the
# sanitizer level above, where each kills the same mutant in a fraction of a second). Measured idle, b32: 4.9 s whole
# script at the cap.
hook_reset; a15_timed b32 2 15 "A15 at the cap (16 MiB, units of three backslashes + ud800): the repair finishes and denies" "$(a15_payload cap_u3 "$repo")"
hook_reset; a15_timed b5 0 6 "A15 one byte over the cap: the repair is declined (documented residual), allowed" "$(a15_payload capover "$repo")"
hook_reset

# ---- Defect 2: an inherited SECONDS ----
sec_pay="$(pl Grep "$repo" "$(gti "$repo/specs" content)")"
sec_case() {
  local sh="$1" val="$2" want="$3" label="$4"
  hook_reset
  if [[ "$sh" == b32 ]]; then HOOK_ENV=(PATH="$bash32dir:$PATH" SECONDS="$val"); else HOOK_ENV=(SECONDS="$val"); fi
  hook_case "$want" "$label ($sh)" "$sec_pay"
  hook_reset
}
sec_case b5 25 0 "A15 S1 an inherited SECONDS=25 does not make a clean directory Grep UNVERIFIED"
sec_case b5 19 0 "A15 S2 an inherited SECONDS=19 (the first value that tripped) does not either"
sec_case b5 100 0 "A15 S3 an inherited SECONDS=100 does not either"
sec_case b5 18 0 "A15 S4 control: SECONDS=18 allowed before and after"
sec_case b5 abc 0 "A15 S5 control: a non-numeric SECONDS is read as 0 by the shell"
# S6: the ceiling with a negative inherited value. Measured on the unchanged guard (hung rg stub listing 2,000,000
# names, GUARD_SECRETS_WALK_SECS=20, padded path and cwd): 29.0 s on /bin/bash against 22.0 s with SECONDS unset.
neg_row() {
  local label="$1" pay t0 t1
  pay="$(a13_payload ceil 0 "$repo")"
  hook_reset; HOOK_CAP=40
  HOOK_ENV=(PATH="$bash32dir:$ceil_stub:$PATH" GUARD_SECRETS_WALK_SECS=20 SECONDS=-100)
  t0="$(now_ms)"
  hc 2 "$label" "$pay" "UNVERIFIED (enumeration exceeded 20 s)"
  t1="$(now_ms)"; HOOK_CAP=""
  printf 'time  %s: %s ms\n' "$label" "$((t1 - t0))" >&2
  hc_done "$([[ $((t1 - t0)) -lt 25000 ]] || printf 'wall time %s ms, not under 25000' "$((t1 - t0))")"
  hook_reset
}
if [[ -x /bin/bash ]]; then neg_row "A15 S6 a negative inherited SECONDS does not lift the absolute ceiling (b32, under 25 s)"; fi

printf '\n%d passed, %d failed\n' "$pass" "$fail"
[[ "$fail" -eq 0 ]]

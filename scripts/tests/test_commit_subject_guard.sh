#!/usr/bin/env bash
set -uo pipefail

# Script: test_commit_subject_guard.sh
# Purpose: Regression tests for commit-subject-guard.sh/.py and commit-subject-advisor.py — a
#          provably over-long subject is blocked, and every ambiguous or exempt form is allowed.
# Usage:   bash scripts/tests/test_commit_subject_guard.sh

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
guard="$here/../commit-subject-guard.py"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
pass=0
fail=0

# NOTE: bare-path invocation on purpose — the suite thereby also verifies the exec bit.
run() { # command cwd -> exit code
  local got=0
  printf '%s' "$(python3 -c 'import json,sys;print(json.dumps({"tool_name":"Bash","tool_input":{"command":sys.argv[1]},"cwd":sys.argv[2]}))' "$1" "$2")" \
    | "$guard" >/dev/null 2>&1 || got=$?
  printf '%s' "$got"
}
assert() { # command cwd want label
  local got; got="$(run "$1" "$2")"
  if [[ "$got" -eq "$3" ]]; then
    printf 'PASS  %s (exit %d)\n' "$4" "$got"; pass=$((pass + 1))
  else
    printf 'FAIL  %s (want %d, got %d)\n' "$4" "$3" "$got"; fail=$((fail + 1))
  fi
}
mkrepo() { # dir [marker-body] — init and opt in unless marker-body is the literal NONE
  git init -q "$1"
  git -C "$1" config user.email test@test.invalid
  git -C "$1" config user.name test
  git -C "$1" config commit.gpgsign false
  git -C "$1" commit -q --allow-empty -m seed
  if [ "${2:-}" != NONE ]; then
    printf '%s\n' "${2:-$'subject_advise = 72\nsubject_block = 80'}" > "$1/.commit-conventions.toml"
  fi
}

S72="$(python3 -c 'print("f"*72)')"   # 72 chars: advise tier, NOT blocked by this hook
S80="$(python3 -c 'print("f"*80)')"   # 80 chars: block tier
S79="$(python3 -c 'print("f"*79)')"

# --- opted-in repo ---
mkrepo "$tmp/r1"
assert "git commit -m \"$S80\""            "$tmp/r1" 2 '80-char subject -> blocked'
assert "git commit -m \"$S79\""            "$tmp/r1" 0 '79-char subject -> allowed (advise tier only)'
assert "git commit -m \"$S72\""            "$tmp/r1" 0 '72-char subject -> allowed by the PRE hook'
assert 'git commit -m "feat(x): short"'    "$tmp/r1" 0 'short subject -> allowed'
assert "ALLOW_LONG_SUBJECT=1 git commit -m \"$S80\"" "$tmp/r1" 0 'override -> allowed'
assert "ALLOW_LONG_SUBJECT=1 git status && git commit -m \"$S80\"" "$tmp/r1" 2 'override on the WRONG segment -> still blocked'
assert "git add . && git commit -m \"$S80\"" "$tmp/r1" 2 'compound segment commit -> blocked'
assert "git commit --amend -m \"$S80\""    "$tmp/r1" 2 'amend with an over-long -m -> blocked'

# --- the /commit canonical heredoc form ---
assert "git commit -m \"\$(cat <<'EOF'
$S80
EOF
)\"" "$tmp/r1" 2 'quoted-heredoc 80-char subject -> blocked'
assert "git commit -m \"\$(cat <<'EOF'
feat(x): short
EOF
)\"" "$tmp/r1" 0 'quoted-heredoc short subject -> allowed'

# --- NO FALSE BLOCKS: every ambiguous or exempt form must pass ---
# shellcheck disable=SC2016  # the label names the literal $VAR on purpose; nothing here expands
assert "git commit -m \"chore(\$PKG): $S80\"" "$tmp/r1" 0 'unexpanded $VAR -> allowed (length is a guess)'
assert "git commit -m \"\$(cat <<EOF
$S80
EOF
)\"" "$tmp/r1" 0 'UNQUOTED heredoc expands -> allowed'
assert "git commit -m \"\$(gen_msg.sh)\"" "$tmp/r1" 0 'unknowable substitution -> allowed'
assert "git commit -m \"$S72 ; $S72\""      "$tmp/r1" 0 'multi-line -m: lower bound is the first line -> allowed'
assert "git commit -m \"fixup! $S80\""      "$tmp/r1" 0 'fixup! prefix -> exempt'
assert "git commit -m \"Revert \\\"$S80\\\"\"" "$tmp/r1" 0 'Revert prefix -> exempt'
assert "git commit -F /tmp/msg.txt"         "$tmp/r1" 0 '-F file: no inline subject -> allowed'
assert "git commit -C HEAD~1"               "$tmp/r1" 0 '-C reuse: no inline subject -> allowed'
assert "git commit --amend --no-edit"       "$tmp/r1" 0 'amend --no-edit -> allowed'
assert "git commit --fixup HEAD~1"          "$tmp/r1" 0 '--fixup auto message -> allowed'
assert "git tag -a v1.0.0 -m \"$S80\""      "$tmp/r1" 0 'git tag is not a commit -> allowed'
assert "git log --grep=commit"              "$tmp/r1" 0 'commit word without a commit -> allowed'
assert "echo \"$S80\""                      "$tmp/r1" 0 'no git at all -> allowed'
assert "git commit -m \"a ; $S80\""         "$tmp/r1" 0 'literal separator under-measures -> allowed (fails open)'

# --- the OWNER SCOPE PROPERTY: inert where the convention is not declared ---
mkrepo "$tmp/r2" NONE
assert "git commit -m \"$S80\"" "$tmp/r2" 0 'NOT opted in -> inert even at 80 chars'

# --- malformed marker must be inert, never defaulted ---
mkrepo "$tmp/r3" 'subject_block = eighty'
assert "git commit -m \"$S80\"" "$tmp/r3" 0 'malformed marker -> inert'
mkrepo "$tmp/r4" $'subject_advise = 90\nsubject_block = 80'
assert "git commit -m \"$S80\"" "$tmp/r4" 0 'incoherent thresholds -> inert'

# --- declared thresholds are honoured ---
mkrepo "$tmp/r5" $'subject_advise = 10\nsubject_block = 20'
assert 'git commit -m "this subject is over twenty chars"' "$tmp/r5" 2 'declared low block threshold honoured'

# --- regressions for the two blockers a plan review found (each was a measured FALSE BLOCK) ---
# shellcheck disable=SC2016  # single-quoted on purpose: the embedded $(...) must stay LITERAL text
EMBED='chore: bump $(cat VERSION) and sync $(cat OTHER) manifests for the release'
assert "git commit -m \"$EMBED\"" "$tmp/r1" 0 'EMBEDDED substitution (90 marker chars) -> allowed'
assert "git -C \"$tmp/r1\" commit -m \"$S80\"" "$tmp/r1" 2 'git -C <dir> commit -> still measured'
assert "git -C\"$tmp/r1\" commit -m \"$S80\"" "$tmp/r1" 2 'attached -C<dir> commit -> still measured'

# --- Fix 1 regression: policy resolves from the -C TARGET, never from the payload cwd alone ---
assert "git -C \"$tmp/r2\" commit -m \"$S80\"" "$tmp/r1" 0 'cwd=opted-in, -C targets a NON-opted-in repo -> allowed'
assert "git -C \"$tmp/r1\" commit -m \"$S80\"" "$tmp/r2" 2 'cwd=NOT opted-in, -C targets an opted-in repo -> blocked'
assert "echo \"\$(cat <<'EOF'
$S80
EOF
)\" ; out=\$(git commit -m \"\$(gen_msg)\")" "$tmp/r1" 0 'multi-context: index space is ambiguous -> allowed'
assert "echo git commit -m \"$S80\"" "$tmp/r1" 0 'git as an ARGUMENT to echo -> not a commit -> allowed'
assert "git commit -m \"fixup! $S72\"" "$tmp/r1" 0 'fixup! at the advise boundary -> exempt'

# --- override must LEAD the segment, not merely appear in it ---
assert "git commit -m \"$S80\" -- ALLOW_LONG_SUBJECT=1" "$tmp/r1" 2 'override as a PATHSPEC does not disarm the gate'

# --- tokenizer ambiguity fails OPEN (this is not a security gate) ---
# NB: `git commit -m "unbalanced '"` parses CLEANLY (the quote sits inside a double-quoted span), so
# it proves nothing. These inputs were measured to reach git_command's real ValueError taxonomy via
# iter_context_token_streams: the reserved marker, and nesting deeper than MAX_CONTEXT_DEPTH (8).
# shellcheck disable=SC2016  # the python literal must not have bash expand its $( inside
DEEP="$(python3 -c 'print("$("*10 + "x" + ")"*10)')"
assert "git commit -m \"$DEEP\""                         "$tmp/r1" 0 'nesting past the depth limit -> ValueError -> fails OPEN'
assert "git commit -m \"__GIT_COMMAND_SUBST_0__ $S80\""  "$tmp/r1" 0 'reserved marker in input -> ValueError -> fails OPEN'

# ============================ PostToolUse advisor ============================
advisor="$here/../commit-subject-advisor.py"
arun() { # command cwd response_mode -> exit code
  # response_mode "success" synthesizes the OBSERVED real Bash-SUCCESS tool_response shape: a dict
  # carrying stdout/stderr/interrupted/isImage/noOutputExpected -- no exit-code key of any kind.
  # response_mode "fail" synthesizes the OBSERVED real Bash-FAILURE shape: tool_response is NOT a
  # dict at all, it is a plain string beginning "Error: Exit code N". Both pinned from this
  # session's own PostToolUse transcript (4657 records) -- see _observed_failure's docstring.
  local got=0
  printf '%s' "$(python3 -c 'import json,sys
command, cwd, mode = sys.argv[1], sys.argv[2], sys.argv[3]
resp = ("Error: Exit code 1\nnothing to commit, working tree clean" if mode == "fail" else
        {"stdout": "", "stderr": "", "interrupted": False, "isImage": False, "noOutputExpected": False})
print(json.dumps({"tool_name": "Bash", "tool_input": {"command": command}, "cwd": cwd, "tool_response": resp}))' "$1" "$2" "$3")" \
    | "$advisor" >/dev/null 2>&1 || got=$?
  printf '%s' "$got"
}
aassert() { # command cwd response_mode want label
  local got; got="$(arun "$1" "$2" "$3")"
  if [[ "$got" -eq "$4" ]]; then
    printf 'PASS  %s (exit %d)\n' "$5" "$got"; pass=$((pass + 1))
  else
    printf 'FAIL  %s (want %d, got %d)\n' "$5" "$4" "$got"; fail=$((fail + 1))
  fi
}

# a1: HEAD carries a 74-char subject -> advisory tier
mkrepo "$tmp/a1"
git -C "$tmp/a1" commit -q --allow-empty -m "$(python3 -c 'print("f"*74)')"
aassert 'git commit -m x'     "$tmp/a1" success 2 'real 74-char HEAD subject, OBSERVED success-dict shape -> advises'
aassert 'git commit -m x'     "$tmp/a1" fail    0 'command FAILED (OBSERVED "Error: Exit code 1" string) -> silent (never read a stale HEAD)'
aassert 'git status'          "$tmp/a1" success 0 'not a commit command -> silent'
aassert 'git log --grep=commit' "$tmp/a1" success 0 'commit WORD but no commit -> silent'
aassert "bash $tmp/a1/test_commit_subject_guard.sh" "$tmp/a1" success 0 'commit substring in a FILENAME -> silent'

# a2: compliant HEAD -> silent
mkrepo "$tmp/a2"
git -C "$tmp/a2" commit -q --allow-empty -m 'feat(x): a short compliant subject'
aassert 'git commit -m x' "$tmp/a2" success 0 'compliant HEAD -> silent'

# a3: exempt HEAD -> silent however long
mkrepo "$tmp/a3"
git -C "$tmp/a3" commit -q --allow-empty -m "fixup! $(python3 -c 'print("f"*80)')"
aassert 'git commit -m x' "$tmp/a3" success 0 'fixup! HEAD -> exempt, silent'

# a4: not opted in -> silent (response_mode success, so this actually exercises the opt-in check
#     rather than short-circuiting on _observed_failure the way a stale "fail" mode would)
mkrepo "$tmp/a4" NONE
git -C "$tmp/a4" commit -q --allow-empty -m "$(python3 -c 'print("f"*90)')"
aassert 'git commit -m x' "$tmp/a4" success 0 'not opted in -> silent'

# a4b: the override must silence the advisor too, or it defeats the override one tier later and
#      pressures the exact action skills/recast/SKILL.md forbids.
mkrepo "$tmp/a4b"
git -C "$tmp/a4b" commit -q --allow-empty -m "$(python3 -c 'print("f"*84)')"
aassert "ALLOW_LONG_SUBJECT=1 git commit -m x" "$tmp/a4b" success 0 'override -> advisory suppressed'
aassert 'git commit -m x'                      "$tmp/a4b" success 2 'same repo WITHOUT the override -> advises'

# a4c: a commit targeting ANOTHER repo must not make us read this one's HEAD
mkrepo "$tmp/a4c"
git -C "$tmp/a4c" commit -q --allow-empty -m "$(python3 -c 'print("f"*84)')"
mkrepo "$tmp/a4c_other"
aassert "git -C $tmp/a4c_other commit -m x" "$tmp/a4c" success 0 'commit targeted another repo -> this HEAD not advised'

# a5: a PUSHED HEAD must never be advised (would prescribe amending published history)
mkrepo "$tmp/a5"
git -C "$tmp/a5" commit -q --allow-empty -m "$(python3 -c 'print("f"*74)')"
git init -q --bare "$tmp/a5remote"
git -C "$tmp/a5" remote add origin "$tmp/a5remote"
git -C "$tmp/a5" push -q origin HEAD:refs/heads/main 2>/dev/null
git -C "$tmp/a5" fetch -q origin 2>/dev/null
aassert 'git commit -m x' "$tmp/a5" success 0 'HEAD reachable from a remote -> silent'

# a6: HEAD committed well outside the freshness window -> silent. `%ct` is the COMMITTER date, so
#     backdating must go through GIT_COMMITTER_DATE, not just --date (which only sets `%at`). This
#     pins the schema-independent backstop directly, on a commit that really happened (unlike a1's
#     failure case): see "Known limitations" item 4 — the same mechanism that keeps a failed
#     commit's stale HEAD from being misread also fails open when a genuinely fresh commit is
#     deliberately backdated, and staying silent is the safe direction either way.
mkrepo "$tmp/a6"
GIT_COMMITTER_DATE="2000-01-01T00:00:00 +0000" git -C "$tmp/a6" commit -q --allow-empty \
  --date "2000-01-01T00:00:00 +0000" -m "$(python3 -c 'print("f"*74)')"
aassert 'git commit -m x' "$tmp/a6" success 0 'stale HEAD outside the freshness window -> silent'

printf '\n%d passed, %d failed\n' "$pass" "$fail"
[ "$fail" -eq 0 ]

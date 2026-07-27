#!/usr/bin/env bash
set -euo pipefail

# Script: commit-subject-test.sh
# Purpose: PostToolUse hook — run the commit-subject suites, and py39-compat on any scripts/*.py edit
# Usage: Called by Claude Code hooks with JSON on stdin
#
# Exit codes:
#   0 — allow (no owned/dependency file touched, every applicable suite passed, or any internal
#       error → fail open)
#   2 — blocked: an applicable suite fails after this edit (stderr fed back to Claude)
#
# TWO INDEPENDENT TRIGGERS, because they answer different questions:
#
#   * commit-subject suites — "did this edit break the GATE's own behaviour?" Fixed list: the five
#     files this feature owns, PLUS scripts/lib/git_command.py. commit_subject.py imports six
#     symbols from git_command.py, so a regression there is a regression here even though the file
#     is not "owned" by this feature (see publication-push-guard-test.sh's Dependency graph note for
#     the same pattern). NOTE: git_command.py also matches the py39-compat wildcard below, so it
#     always re-runs that suite too; it is listed AGAIN here because that wildcard answers a
#     different question (import hygiene, not commit-subject behaviour) and is not a substitute.
#
#   * py39-compat suite — "does every scripts/*.py file (including scripts/lib/*.py — '*' spans '/'
#     in a case pattern) still import under Python 3.9?" A repo-wide static invariant, so ANY python
#     file under scripts/ must re-run it, including ones this feature does not own and files that do
#     not exist yet. Unwired, the next Python hook could silently reintroduce the 3.9 fail-open this
#     branch exists to close.

command -v jq >/dev/null 2>&1 || exit 0
input=$(cat) || exit 0
file_path=$(printf '%s' "$input" | jq -r '.tool_input.file_path // empty' 2>/dev/null) || exit 0
[ -n "$file_path" ] || exit 0

run_commit_subject=0
run_py39=0

case "$file_path" in
  */scripts/commit-subject-guard.py | */scripts/commit-subject-advisor.py | \
    */scripts/lib/commit_subject.py | */scripts/lib/git_command.py | \
    */scripts/tests/test_commit_subject_guard.sh | \
    */scripts/tests/test_commit_subject.py)
    run_commit_subject=1
    ;;
esac

case "$file_path" in
  # '*' spans '/' in a case pattern (this is not filename globbing), so */scripts/*.py alone already
  # matches scripts/lib/*.py too — a separate */scripts/lib/*.py arm would be dead code (shellcheck
  # SC2221/SC2222).
  */scripts/*.py)
    run_py39=1
    ;;
esac

if [ "$run_commit_subject" -eq 0 ] && [ "$run_py39" -eq 0 ]; then
  exit 0
fi

root=$(git -C "$(dirname "$file_path")" rev-parse --show-toplevel 2>/dev/null) || exit 0
[ -n "$root" ] || exit 0
command -v python3 >/dev/null 2>&1 || exit 0

# Each suite's status is captured on its OWN line, immediately after the assignment — a trailing
# command would otherwise discard the verdict this hook exists to report.
hook_rc=0
hook_out=""
lib_rc=0
lib_out=""
py39_rc=0
py39_out=""

if [ "$run_commit_subject" -eq 1 ] \
  && [ -f "$root/scripts/tests/test_commit_subject_guard.sh" ] \
  && [ -f "$root/scripts/tests/test_commit_subject.py" ]; then
  hook_out=$(cd "$root" && bash scripts/tests/test_commit_subject_guard.sh 2>&1) || hook_rc=$?
  lib_out=$(cd "$root" && python3 -m pytest -q scripts/tests/test_commit_subject.py 2>&1) || lib_rc=$?
fi

if [ "$run_py39" -eq 1 ] && [ -f "$root/scripts/tests/test_py39_compat.sh" ]; then
  py39_out=$(cd "$root" && bash scripts/tests/test_py39_compat.sh 2>&1) || py39_rc=$?
fi

[ "$hook_rc" -eq 0 ] && [ "$lib_rc" -eq 0 ] && [ "$py39_rc" -eq 0 ] && exit 0

{
  printf 'commit-subject-test FAILED after this edit:\n'
  if [ "$hook_rc" -ne 0 ]; then
    printf -- '--- scripts/tests/test_commit_subject_guard.sh (exit %d) ---\n' "$hook_rc"
    printf '%s\n' "$hook_out" | tail -30
  fi
  if [ "$lib_rc" -ne 0 ]; then
    printf -- '--- scripts/tests/test_commit_subject.py (exit %d) ---\n' "$lib_rc"
    printf '%s\n' "$lib_out" | tail -30
  fi
  if [ "$py39_rc" -ne 0 ]; then
    printf -- '--- scripts/tests/test_py39_compat.sh (exit %d) ---\n' "$py39_rc"
    printf '%s\n' "$py39_out" | tail -30
  fi
} >&2 || true
exit 2

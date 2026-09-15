#!/usr/bin/env bash
set -uo pipefail

# Script: test_hook_suite_guard.sh
# Purpose: Regression tests — a test-runner hook must not report success when a suite it exists
#          to run has been DELETED, yet must stay inert in repos that never had it.
# Usage:   ./scripts/tests/test_hook_suite_guard.sh
#
# commit-subject-test.sh and recast-test.sh both gate on "is pytest importable", but the same
# mutation (deleting that check) fails them in OPPOSITE directions. commit-subject-test.sh alarms
# BEFORE running anything, so losing the check degrades it to a silent SKIP (the suite quietly
# never runs, exit 0). recast-test.sh has no such backstop: on an owned repo it still exits 2 (the
# missing-suite branch alone covers that), but on a NON-owner it falls through past the (skipped)
# ownership guard and actually attempts the run — degrading to an OVER-ALARM that blocks a foreign
# repo which never had a stake in this hook. See the recast-specific fixture near the end of this
# file for the row that pins the second direction.

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
hooks="$here/../../scripts"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

pass=0
fail=0
pass_line() { printf 'PASS  %s\n' "$1"; pass=$((pass + 1)); }
fail_line() { printf 'FAIL  %s\n' "$1"; fail=$((fail + 1)); }
check_eq() { # got want label
  if [ "$1" = "$2" ]; then pass_line "$3"; else fail_line "$3 (want [$2] got [$1])"; fi
}

mkrepo() {
  git init -q "$1"
  git -C "$1" config user.email test@test.invalid
  git -C "$1" config user.name test
  git -C "$1" config commit.gpgsign false
  git -C "$1" config tag.gpgsign false
}

# Ownership is proven by a CONTENT SENTINEL inside the hook's own source, not merely its
# filename — a foreign repo's same-named scripts/<hook>.sh must not count. This writes the marker
# every "owning" fixture below needs.
mark_owner() {
  printf '# Ownership sentinel (do not remove): dotclaude-test-runner-hook\n' > "$1"
}

# drive HOOK EDITED_ABS_PATH -> echoes the hook's exit status
drive() {
  local hook="$1" path="$2" rc=0
  printf '{"tool_input":{"file_path":"%s"}}' "$path" \
    | bash "$hooks/$hook" >/dev/null 2>&1 || rc=$?
  printf '%s\n' "$rc"
}

# EXTENSION-AWARE ON PURPOSE: a .py suite runs under `python3 -m pytest`, and a bash stub would
# fail collection — making the "present" case alarm for a reason unrelated to the code under test.
write_stub_suite() {
  mkdir -p "$(dirname "$1")"
  case "$1" in
    *.py) printf 'def test_stub():\n    assert True\n' > "$1" ;;
    *)    printf '#!/usr/bin/env bash\nexit 0\n' > "$1"; chmod +x "$1" ;;
  esac
}

# label | hook | trigger-relative-path | SPACE-SEPARATED suite list
# Two hooks run MULTIPLE suites, and publication-push-guard runs a DIFFERENT set per arm — the
# shared_dep arm exists because git_command.py is the tokenizer both fail-closed push guards
# depend on, so its unit suite is exactly the self-concealing deletion this change targets.
CASES="
audit|audit-test.sh|skills/audit/audit.sh|scripts/tests/test_audit.sh
debrief-backlog|debrief-backlog-test.sh|skills/debrief/backlog.py|skills/debrief/tests/test_stub.py
exec-bit|exec-bit-guard-test.sh|scripts/exec-bit-guard.sh|scripts/tests/test_exec_bit_guard.sh
guard-secrets|guard-secrets-test.sh|scripts/guard-secrets.sh|scripts/tests/test_guard_secrets.sh
markdownlint|markdownlint-check-test.sh|scripts/markdownlint-check.sh|scripts/tests/test_markdownlint_check.sh
md-links|md-links-check-test.sh|scripts/md-links-check.py|scripts/tests/test_md_links_check.sh
memory-index-check|memory-index-check-test.sh|scripts/memory-index-check.py|scripts/tests/test_memory_index_check.sh
style-check|style-check-test.sh|scripts/style-check.sh|scripts/tests/test_style_check.sh
sync-docs|sync-docs-test.sh|skills/sync-docs/sync_docs.py|skills/sync-docs/tests/test_stub.py
commit-subject|commit-subject-test.sh|scripts/lib/commit_subject.py|scripts/tests/test_commit_subject_guard.sh scripts/tests/test_commit_subject.py scripts/tests/test_py39_compat.sh
ppg-guard-only|publication-push-guard-test.sh|scripts/publication-push-guard.py|scripts/tests/test_publication_push_guard.sh
ppg-shared-dep|publication-push-guard-test.sh|scripts/lib/git_command.py|scripts/tests/test_git_command.py scripts/tests/test_git_command_properties.py scripts/tests/test_publication_push_guard.sh scripts/tests/test_recast_hooks.sh
env-claims-check|env-claims-check-test.sh|scripts/env-claims-check.py|scripts/tests/test_env_claims_check.py
mutation-anchors-check|mutation-anchors-check-test.sh|scripts/mutation-anchors-check.py|scripts/tests/test_mutation_anchors_check.py
recast|recast-test.sh|skills/recast/recast-recon-history.sh|skills/recast/tests/test_recast_recon_history.py
"

i=0
while IFS='|' read -r label hook trigger suites; do
  [ -z "$label" ] && continue
  i=$((i + 1))

  # --- owner WITHOUT the suite(s) -> must alarm. This is the defect. ---
  r="$tmp/own_missing_$i"
  mkrepo "$r"
  mkdir -p "$(dirname "$r/$trigger")" "$r/scripts"
  printf 'x\n' > "$r/$trigger"
  mark_owner "$r/scripts/$hook"   # ownership marker: the hook's own source
  check_eq "$(drive "$hook" "$r/$trigger")" 2 "$label: owner + suite DELETED -> exit 2"

  # --- owner WITH every suite this trigger requires -> must not alarm ---
  r="$tmp/own_present_$i"
  mkrepo "$r"
  mkdir -p "$(dirname "$r/$trigger")" "$r/scripts"
  printf 'x\n' > "$r/$trigger"
  mark_owner "$r/scripts/$hook"
  for s in $suites; do write_stub_suite "$r/$s"; done   # unquoted: split on spaces, deliberate
  check_eq "$(drive "$hook" "$r/$trigger")" 0 "$label: owner + suite(s) present -> exit 0"

  # --- NON-owner without the suite -> must stay inert. Guards against over-alarming. ---
  r="$tmp/foreign_$i"
  mkrepo "$r"
  mkdir -p "$(dirname "$r/$trigger")"
  printf 'x\n' > "$r/$trigger"
  check_eq "$(drive "$hook" "$r/$trigger")" 0 "$label: NON-owner + no suite -> exit 0 (inert)"

  # --- LEAVE-ONE-OUT: for a MULTI-suite trigger, deleting only ONE of several suites (every
  # other one present) must still alarm. Wiping the whole list at once (the case above) cannot
  # tell "checks every suite" apart from "checks whether ANY suite survived" — this pins each
  # per-suite presence line individually. Single-suite triggers have nothing to hold out, so
  # they are skipped (n_suites -le 1). No arrays: word-split $suites twice, bash-3.2 safe.
  n_suites=0
  for _s in $suites; do n_suites=$((n_suites + 1)); done
  if [ "$n_suites" -gt 1 ]; then
    j=0
    for held_out in $suites; do
      j=$((j + 1))
      r="$tmp/loo_${i}_${j}"
      mkrepo "$r"
      mkdir -p "$(dirname "$r/$trigger")" "$r/scripts"
      printf 'x\n' > "$r/$trigger"
      mark_owner "$r/scripts/$hook"
      for s in $suites; do
        [ "$s" = "$held_out" ] && continue
        write_stub_suite "$r/$s"
      done
      check_eq "$(drive "$hook" "$r/$trigger")" 2 "$label: owner missing ONLY $held_out -> exit 2"
    done
  fi
done <<EOF
$CASES
EOF

# --- Ownership keys on a CONTENT SENTINEL, not a bare filename match. A foreign repo can
# coincidentally carry a same-named scripts/<hook>.sh (it's a live convention on this machine) —
# without the sentinel INSIDE it, that must NOT be read as ownership, so a genuinely-deleted
# suite there must stay inert rather than alarm. ---
sentinel_case() {
  local label="$1" hook="$2" trigger="$3"
  local r="$tmp/sentinel_absent_${label}"
  mkrepo "$r"
  mkdir -p "$(dirname "$r/$trigger")" "$r/scripts"
  printf 'x\n' > "$r/$trigger"
  printf '#!/usr/bin/env bash\n# same name as the real hook, but no sentinel inside\nexit 0\n' \
    > "$r/scripts/$hook"
  check_eq "$(drive "$hook" "$r/$trigger")" 0 \
    "$label: same-named scripts/$hook WITHOUT sentinel -> exit 0 (not ownership)"
}
sentinel_case style-check style-check-test.sh scripts/style-check.sh
sentinel_case commit-subject commit-subject-test.sh scripts/lib/commit_subject.py

# A path outside any git repo must stay inert (environment fail-open, unchanged).
outside="$tmp/not_a_repo"
mkdir -p "$outside/scripts"
printf 'x\n' > "$outside/scripts/style-check.sh"
check_eq "$(drive style-check-test.sh "$outside/scripts/style-check.sh")" 0 \
  'style-check-test.sh: path outside any git repo -> exit 0 (environment fail-open)'

# A NON-owner holding only SOME of a hook's suites must stay inert — and in particular must not
# EXECUTE the repo-supplied scripts that happen to be present. Splitting the old combined guard
# briefly removed this inertness; the security review caught it. The canary file is the assertion
# that matters: rc alone cannot tell "declined to run" from "ran and passed".
partial="$tmp/nonowner_partial"
mkrepo "$partial"
mkdir -p "$partial/scripts/lib" "$partial/scripts/tests"
printf 'x\n' > "$partial/scripts/lib/git_command.py"
printf '#!/usr/bin/env bash\ntouch "%s/CANARY_EXECUTED"\nexit 0\n' "$partial" \
  > "$partial/scripts/tests/test_recast_hooks.sh"
chmod +x "$partial/scripts/tests/test_recast_hooks.sh"
# test_publication_push_guard.sh deliberately absent -> the suite set is partial
check_eq "$(drive publication-push-guard-test.sh "$partial/scripts/lib/git_command.py")" 0 \
  'non-owner with a PARTIAL suite set -> exit 0 (inert)'
if [ -f "$partial/CANARY_EXECUTED" ]; then
  fail_line 'non-owner with a PARTIAL suite set -> repo-supplied script must NOT be executed'
else
  pass_line 'non-owner with a PARTIAL suite set -> repo-supplied script not executed'
fi

# A suite that is PRESENT but cannot be RUN is the same event as a missing one: the gate did not
# run. Without this, an unavailable pytest silently skipped test_commit_subject.py and the hook
# reported success — this branch's own defect, reached through the environment rather than the
# filesystem. The shim makes `import pytest` fail while leaving python3 otherwise usable.
unrun="$tmp/owner_unrunnable"
mkrepo "$unrun"
mkdir -p "$unrun/scripts/lib" "$unrun/scripts/tests" "$unrun/shim"
printf 'x\n' > "$unrun/scripts/lib/commit_subject.py"
mark_owner "$unrun/scripts/commit-subject-test.sh"
for s in test_commit_subject_guard.sh test_py39_compat.sh; do
  write_stub_suite "$unrun/scripts/tests/$s"
done
write_stub_suite "$unrun/scripts/tests/test_commit_subject.py"
# shellcheck disable=SC2016  # $1/$2/$@ belong to the generated shim, not to this script
printf '#!/bin/sh\nif [ "$1" = "-c" ] && [ "$2" = "import pytest" ]; then exit 1; fi\nexec /usr/bin/env python3 "$@"\n' \
  > "$unrun/shim/python3"
chmod +x "$unrun/shim/python3"
unrun_rc=0
printf '{"tool_input":{"file_path":"%s"}}' "$unrun/scripts/lib/commit_subject.py" \
  | PATH="$unrun/shim:$PATH" bash "$hooks/commit-subject-test.sh" >/dev/null 2>&1 || unrun_rc=$?
check_eq "$unrun_rc" 2 'owner + suite present but pytest UNAVAILABLE -> exit 2 (gate did not run)'
check_eq "$(drive commit-subject-test.sh "$unrun/scripts/lib/commit_subject.py")" 0 \
  'owner + same repo with pytest available -> exit 0'

# recast-test.sh is UNLIKE every hook above: when "missing" comes back empty it runs the matched
# test regardless of OWNERSHIP (the ownership guard only gates the ALARM branch — recast's trigger
# set is open and its suite is resolved per-file, so any repo carrying the suite gets it run). That
# means the fixture above (commit-subject) cannot stand in for recast: its shim only has to fool
# the AVAILABILITY PROBE, because commit-subject's ownership check runs first and exits before ever
# reaching a real `pytest` invocation — so that shim's fallback to the genuinely-installed pytest
# never gets exercised. recast's mutant (elif deleted) DOES reach that invocation on a non-owner,
# so a shim that only fakes the probe would let the REAL pytest quietly pass the stub test and the
# row would read exit 0 under the mutant too — decoration, not a pin. This shim instead fails any
# invocation naming "pytest" outright (not just the `-c 'import pytest'` probe), which is what a
# machine that truly lacks pytest looks like from every call site, while still delegating every
# other python3 invocation (the `import xdist` probe here) to the real interpreter — resolved by
# ABSOLUTE PATH rather than through `env`, since `env python3` under this PATH would just re-find
# the shim and recurse.
recast_foreign="$tmp/recast_foreign_unrunnable"
mkrepo "$recast_foreign"
mkdir -p "$recast_foreign/skills/recast/tests" "$recast_foreign/shim"
printf 'x\n' > "$recast_foreign/skills/recast/recast-recon-history.sh"
write_stub_suite "$recast_foreign/skills/recast/tests/test_recast_recon_history.py"
real_python3="$(command -v python3)"
# shellcheck disable=SC2016  # $a/$@ belong to the generated shim, not to this script
{
  printf '#!/bin/sh\n'
  printf 'for a in "$@"; do\n'
  printf '  if [ "$a" = "pytest" ] || [ "$a" = "import pytest" ]; then\n'
  printf '    echo "ModuleNotFoundError: No module named '"'"'pytest'"'"'" >&2\n'
  printf '    exit 1\n'
  printf '  fi\n'
  printf 'done\n'
  printf 'exec "%s" "$@"\n' "$real_python3"
} > "$recast_foreign/shim/python3"
chmod +x "$recast_foreign/shim/python3"
recast_rc=0
printf '{"tool_input":{"file_path":"%s"}}' "$recast_foreign/skills/recast/recast-recon-history.sh" \
  | PATH="$recast_foreign/shim:$PATH" bash "$hooks/recast-test.sh" >/dev/null 2>&1 || recast_rc=$?
check_eq "$recast_rc" 0 \
  'recast: NON-owner + suite PRESENT + pytest UNAVAILABLE -> exit 0 (inert; the discriminating row)'

printf '\n%d passed, %d failed\n' "$pass" "$fail"
[ "$fail" -eq 0 ]

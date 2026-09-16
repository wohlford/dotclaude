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
# Two hooks run MULTIPLE suites. publication-push-guard-test.sh is table-driven: an edit to a gate
# built on scripts/lib/git_command.py runs that gate's suites, and an edit to the tokenizer runs its
# own unit suites plus EVERY gate's — so each of its trigger rows lists exactly the suites its row of
# the hook's DEPENDENTS table requires.
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
ppg-guard-only|publication-push-guard-test.sh|scripts/publication-push-guard.py|scripts/tests/test_publication_push_guard.sh scripts/tests/test_guard_corpus.py scripts/tests/test_guard_internals.py
ppg-shared-dep|publication-push-guard-test.sh|scripts/lib/git_command.py|scripts/tests/test_git_command.py scripts/tests/test_git_command_properties.py scripts/tests/test_publication_push_guard.sh scripts/tests/test_guard_corpus.py scripts/tests/test_guard_internals.py scripts/tests/test_push_guard.sh scripts/tests/test_git_timing_guard.sh scripts/tests/test_explain_git_command.py scripts/tests/test_commit_subject_guard.sh scripts/tests/test_commit_subject.py scripts/tests/test_recast_hooks.sh
ppg-push-guard|publication-push-guard-test.sh|scripts/push-guard.py|scripts/tests/test_push_guard.sh
ppg-timing-guard|publication-push-guard-test.sh|scripts/git-timing-guard.py|scripts/tests/test_git_timing_guard.sh
ppg-explain|publication-push-guard-test.sh|scripts/explain-git-command.py|scripts/tests/test_explain_git_command.py
ppg-recast-gate|publication-push-guard-test.sh|scripts/recast-commit-gate.py|scripts/tests/test_recast_hooks.sh
ppg-commit-advisor|publication-push-guard-test.sh|scripts/commit-subject-advisor.py|scripts/tests/test_commit_subject_guard.sh
ppg-commit-guard|publication-push-guard-test.sh|scripts/commit-subject-guard.py|scripts/tests/test_commit_subject_guard.sh
ppg-commit-subject-lib|publication-push-guard-test.sh|scripts/lib/commit_subject.py|scripts/tests/test_commit_subject.py
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

# ---------- publication-push-guard-test.sh: discovery, runners and the budget ----------
# Most rows below drive a TOKENIZER edit in a fixture that owns the hook and carries a passing stub
# for every suite the tokenizer arm requires, then change exactly one thing (the suite-as-trigger row
# edits a suite file instead, and the CONTROL copies the real population). Each asserts the exit
# status AND a message naming its own cause: a hook exits 2 for several reasons, and a row that
# checked only the status would stay green if a DIFFERENT alarm fired first.
ppg=publication-push-guard-test.sh
ppg_union="scripts/tests/test_git_command.py scripts/tests/test_git_command_properties.py scripts/tests/test_publication_push_guard.sh scripts/tests/test_guard_corpus.py scripts/tests/test_guard_internals.py scripts/tests/test_push_guard.sh scripts/tests/test_git_timing_guard.sh scripts/tests/test_explain_git_command.py scripts/tests/test_commit_subject_guard.sh scripts/tests/test_commit_subject.py scripts/tests/test_recast_hooks.sh"

# ppg_owner_fixture DIR — an owning repo holding the tokenizer and a passing stub for every suite.
ppg_owner_fixture() {
  mkrepo "$1"
  mkdir -p "$1/scripts/lib"
  printf 'x\n' > "$1/scripts/lib/git_command.py"
  mark_owner "$1/scripts/$ppg"
  for s in $ppg_union; do write_stub_suite "$1/$s"; done   # unquoted: split on spaces, deliberate
}

# drive_ppg DIR [NAME=VALUE ...] — drive a tokenizer edit; sets ppg_rc and ppg_err (stderr only).
drive_ppg() {
  local r="$1"
  shift
  ppg_rc=0
  ppg_err=$(printf '{"tool_input":{"file_path":"%s"}}' "$r/scripts/lib/git_command.py" \
    | env "$@" bash "$hooks/$ppg" 2>&1 >/dev/null) || ppg_rc=$?
}

# check_has HAYSTACK NEEDLE LABEL
check_has() {
  case "$1" in
    *"$2"*) pass_line "$3" ;;
    *) fail_line "$3 (stderr lacked [$2]; began: $(printf '%s' "$1" | head -3 | tr '\n' ' '))" ;;
  esac
}

# An importer nobody wired, written in the LAZY, INDENTED shape three of the eight real importers
# use — so a predicate narrowed to column-0 imports cannot pass this row. Under that narrowing the
# rc row below still reads 2 (the tripwire fires instead), so the MESSAGE row is the catch: do not
# relax its substring.
r="$tmp/ppg_unwired_lazy"
ppg_owner_fixture "$r"
printf 'def main():\n    import git_command as gitcmd\n    return gitcmd\n' > "$r/scripts/new-gate.py"
drive_ppg "$r"
check_eq "$ppg_rc" 2 'ppg: owner + UNWIRED importer (lazy, indented import) -> exit 2'
check_has "$ppg_err" 'scripts/new-gate.py depends on git_command (directly or through scripts/lib) but no suite is wired for it' \
  'ppg: the unwired-importer alarm names the importer'

# The same unwired importer in a repo that does NOT own the hook stays inert.
r="$tmp/ppg_unwired_foreign"
ppg_owner_fixture "$r"
rm -f "$r/scripts/$ppg"
printf 'import git_command\n' > "$r/scripts/new-gate.py"
drive_ppg "$r"
check_eq "$ppg_rc" 0 'ppg: NON-owner + unwired importer -> exit 0 (inert)'

# A TRANSITIVE dependent: it imports a lib module that imports the tokenizer.
r="$tmp/ppg_transitive"
ppg_owner_fixture "$r"
printf 'import git_command\n' > "$r/scripts/lib/helper_mod.py"
printf 'from helper_mod import thing\n' > "$r/scripts/indirect-gate.py"
drive_ppg "$r"
check_eq "$ppg_rc" 2 'ppg: owner + unwired TRANSITIVE importer -> exit 2'
check_has "$ppg_err" 'scripts/indirect-gate.py depends on git_command' \
  'ppg: discovery follows imports through scripts/lib modules'

# The FLOOR: a gate the table declares exists but no longer imports the tokenizer.
r="$tmp/ppg_floor"
ppg_owner_fixture "$r"
printf 'print("no tokenizer here")\n' > "$r/scripts/push-guard.py"
drive_ppg "$r"
check_eq "$ppg_rc" 2 'ppg: a declared gate present but NOT discovered -> exit 2'
check_has "$ppg_err" 'discovery no longer sees scripts/push-guard.py' \
  'ppg: the floor alarm names the gate discovery lost'

# The TRIPWIRE: a mention discovery cannot classify.
r="$tmp/ppg_tripwire"
ppg_owner_fixture "$r"
printf 'import importlib\nmod = importlib.import_module("git_command")\n' > "$r/scripts/weird.py"
drive_ppg "$r"
check_eq "$ppg_rc" 2 'ppg: an unclassifiable mention of the tokenizer -> exit 2'
check_has "$ppg_err" 'scripts/weird.py mentions git_command in a form discovery cannot classify' \
  'ppg: the tripwire names the file'

# A SUITE FILE is a trigger for its own row. A CASES row cannot pin this — its "DELETED" case writes
# `x` INTO the trigger, which would then exit 2 for `command not found` rather than for the rule.
# Here the edited file is the failing suite itself, so only a hook that treats it as a trigger runs it.
r="$tmp/ppg_suite_as_trigger"
ppg_owner_fixture "$r"
printf '#!/usr/bin/env bash\necho push-guard-suite-edited-and-broke\nexit 1\n' > "$r/scripts/tests/test_push_guard.sh"
ppg_rc=0
ppg_err=$(printf '{"tool_input":{"file_path":"%s"}}' "$r/scripts/tests/test_push_guard.sh" \
  | bash "$hooks/$ppg" 2>&1 >/dev/null) || ppg_rc=$?
check_eq "$ppg_rc" 2 'ppg: editing a FAILING suite file runs that suite -> exit 2'
check_has "$ppg_err" 'test_push_guard.sh FAILED after editing' 'ppg: the edited suite is the one that ran and failed'

# A FAILING suite, once through each runner: shell, then pytest.
r="$tmp/ppg_fail_shell"
ppg_owner_fixture "$r"
printf '#!/usr/bin/env bash\necho push-guard-suite-broke\nexit 1\n' > "$r/scripts/tests/test_push_guard.sh"
drive_ppg "$r"
check_eq "$ppg_rc" 2 'ppg: tokenizer edit + FAILING test_push_guard.sh -> exit 2'
check_has "$ppg_err" 'test_push_guard.sh FAILED after editing' 'ppg: the shell-suite failure is named'

r="$tmp/ppg_fail_pytest"
ppg_owner_fixture "$r"
printf 'def test_broken():\n    assert False, "explain-suite-broke"\n' \
  > "$r/scripts/tests/test_explain_git_command.py"
drive_ppg "$r"
check_eq "$ppg_rc" 2 'ppg: tokenizer edit + FAILING test_explain_git_command.py -> exit 2'
check_has "$ppg_err" 'test_explain_git_command.py FAILED after editing' 'ppg: the pytest-suite failure is named'

# pytest UNAVAILABLE in an owner: the gate did not run. If the hook stopped reporting this, the shim
# would make every .py suite FAIL and the rc row would still read 2 — the MESSAGE row is the catch,
# so do not relax its substring. The shim fails every call that names pytest
# and delegates everything else to the real interpreter by ABSOLUTE path (via `env python3` it
# would re-find the shim and recurse).
r="$tmp/ppg_no_pytest"
ppg_owner_fixture "$r"
mkdir -p "$r/shim"
ppg_real_python3="$(command -v python3)"
# shellcheck disable=SC2016  # $a/$@ belong to the generated shim, not to this script
{
  printf '#!/bin/sh\n'
  printf 'for a in "$@"; do\n'
  printf '  if [ "$a" = "pytest" ] || [ "$a" = "import pytest" ]; then exit 1; fi\n'
  printf 'done\n'
  printf 'exec "%s" "$@"\n' "$ppg_real_python3"
} > "$r/shim/python3"
chmod +x "$r/shim/python3"
drive_ppg "$r" PATH="$r/shim:$PATH"
check_eq "$ppg_rc" 2 'ppg: owner + pytest UNAVAILABLE -> exit 2 (gate did not run)'
check_has "$ppg_err" 'pytest is unavailable' 'ppg: the unavailable runner is named'

# THE LIBRARY: the bounded-run code lives in scripts/lib/hook_budget.sh beside the hook. A hook copied
# somewhere without it has a broken install and must say so — never pass, and never die as set -e's
# rc 1, which the harness treats as non-blocking noise. The MESSAGE is the assertion: exit 2 alone is
# also produced by the missing-suite branch.
nolib="$tmp/nolib_hooks"
mkdir -p "$nolib"
cp "$hooks/$ppg" "$nolib/$ppg"
r="$tmp/ppg_nolib"
ppg_owner_fixture "$r"
ppg_rc=0
ppg_err=$(printf '{"tool_input":{"file_path":"%s"}}' "$r/scripts/lib/git_command.py" \
  | bash "$nolib/$ppg" 2>&1 >/dev/null) || ppg_rc=$?
check_eq "$ppg_rc" 2 'ppg: library absent beside the hook -> exit 2'
check_has "$ppg_err" 'bounded-run library' 'ppg: the absent library is named'

# The BUDGET: the first suite outlives a lowered budget and spawns a child that would outlive it.
# The row asserts the overrun path (not "never started"), the bound, and that no process from the
# suite's tree survives — rc and message alone cannot tell "killed the tree" from "abandoned it".
r="$tmp/ppg_overrun"
ppg_owner_fixture "$r"
ppg_marker="PPG_OVERRUN_MARKER_$$_$RANDOM"
# BOTH unit suites hang (glob order is locale-dependent, so whichever launches first is the one the
# budget interrupts), and the child each spawns IGNORES SIGTERM, so only a hook that escalates to
# KILL across the whole GROUP — not just its leader — leaves nothing behind.
for ppg_slow in test_git_command.py test_git_command_properties.py; do
  printf 'import subprocess, sys, time\n\n\ndef test_hangs():\n    subprocess.Popen([sys.executable, "-c", "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)", "%s"])\n    time.sleep(20)\n' \
    "$ppg_marker" > "$r/scripts/tests/$ppg_slow"
done
ppg_t0=$SECONDS
drive_ppg "$r" PUBLICATION_PUSH_GUARD_TEST_BUDGET=3
ppg_elapsed=$((SECONDS - ppg_t0))
check_eq "$ppg_rc" 2 'ppg: a suite running past the budget -> exit 2'
check_has "$ppg_err" 'GATE DID NOT COMPLETE' 'ppg: the overrun is reported as an incomplete gate'
check_has "$ppg_err" 'killed mid-run: scripts/tests/test_git_command' 'ppg: the overrun names a unit suite it killed'
if [ "$ppg_elapsed" -lt 20 ]; then
  pass_line "ppg: the budget bound the run (${ppg_elapsed}s)"
else
  fail_line "ppg: the budget did not bind (took ${ppg_elapsed}s against a 3s budget)"
fi
sleep 1
if pgrep -f "$ppg_marker" >/dev/null 2>&1; then
  fail_line 'ppg: a process from the killed suite tree SURVIVED the overrun'
  pkill -f "$ppg_marker" >/dev/null 2>&1 || true
else
  pass_line 'ppg: no process from the killed suite tree survived'
fi

# THE KILL BEFORE setsid(): a suite whose launcher is slow to reach os.setsid() is not yet a group
# leader when the deadline passes, so a group kill alone finds nothing to signal. The shim delays
# ONLY the session launch — the pytest probe and every other python3 call pass straight through —
# so the deadline expires inside that window. A lost bound waits for the 4 s launch plus the 8 s suite.
r="$tmp/ppg_kill_before_setsid"
ppg_owner_fixture "$r"
# BOTH unit suites are slow: glob order is locale-dependent, so whichever one launches first must be
# the one whose launch window the deadline falls into, and must be slow enough that a lost bound shows.
for ppg_slow in test_git_command.py test_git_command_properties.py; do
  printf 'import time\n\n\ndef test_slow():\n    time.sleep(8)\n' > "$r/scripts/tests/$ppg_slow"
done
mkdir -p "$r/shim"
# shellcheck disable=SC2016  # $2/$@ belong to the generated shim, not to this script
{
  printf '#!/bin/sh\n'
  printf 'case "$2" in *setsid*) sleep 4 ;; esac\n'
  printf 'exec "%s" "$@"\n' "$ppg_real_python3"
} > "$r/shim/python3"
chmod +x "$r/shim/python3"
ppg_t0=$SECONDS
drive_ppg "$r" PATH="$r/shim:$PATH" PUBLICATION_PUSH_GUARD_TEST_BUDGET=2
ppg_elapsed=$((SECONDS - ppg_t0))
check_eq "$ppg_rc" 2 'ppg: an overrun inside the launch window -> exit 2'
check_has "$ppg_err" 'killed mid-run: scripts/tests/test_git_command' \
  'ppg: the launch-window overrun names a unit suite it stopped'
if [ "$ppg_elapsed" -lt 7 ]; then
  pass_line "ppg: the budget bound a launch-window overrun (${ppg_elapsed}s)"
else
  fail_line "ppg: the budget did not bind a launch-window overrun (took ${ppg_elapsed}s against a 2s budget)"
fi

# DISCOVERY FAILS CLOSED: a git that refuses ls-files must alarm, never read as "no importers".
r="$tmp/ppg_discovery_fails"
ppg_owner_fixture "$r"
mkdir -p "$r/shim"
ppg_real_git="$(command -v git)"
# shellcheck disable=SC2016  # $a/$@ belong to the generated shim, not to this script
{
  printf '#!/bin/sh\n'
  printf 'for a in "$@"; do\n'
  printf '  if [ "$a" = "ls-files" ]; then echo "fatal: simulated ls-files failure" >&2; exit 128; fi\n'
  printf 'done\n'
  printf 'exec "%s" "$@"\n' "$ppg_real_git"
} > "$r/shim/git"
chmod +x "$r/shim/git"
drive_ppg "$r" PATH="$r/shim:$PATH"
check_eq "$ppg_rc" 2 'ppg: discovery failing -> exit 2 (fails closed)'
check_has "$ppg_err" 'discovery of the files that depend on git_command failed' 'ppg: the discovery failure is named'

# DISCOVERY FAILS CLOSED on a name git will only print quoted: without the check it would be dropped
# from the candidate set without a word, and an importer hiding under such a name would go unwired.
r="$tmp/ppg_quoted_path"
ppg_owner_fixture "$r"
printf 'import git_command\n' > "$r/scripts/we\"ird.py"
drive_ppg "$r"
check_eq "$ppg_rc" 2 'ppg: a candidate path git prints quoted -> exit 2 (discovery fails closed)'
check_has "$ppg_err" 'discovery of the files that depend on git_command failed' \
  'ppg: the quoted-path failure is named'

# ---------- audit-test.sh's budget ----------
# audit_fixture DIR — an owning repo holding the audit engine and, unless the row writes its own, no suite.
audit_fixture() {
  mkrepo "$1"
  mkdir -p "$1/skills/audit" "$1/scripts/tests"
  printf 'x\n' > "$1/skills/audit/audit.sh"
  mark_owner "$1/scripts/audit-test.sh"
}
# drive_audit DIR HOOKDIR [NAME=VALUE ...] — drive an audit.sh edit; sets audit_rc and audit_err (stderr only).
drive_audit() {
  local r="$1" hookdir="$2"
  shift 2
  audit_rc=0
  audit_err=$(printf '{"tool_input":{"file_path":"%s"}}' "$r/skills/audit/audit.sh" \
    | env "$@" bash "$hookdir/audit-test.sh" 2>&1 >/dev/null) || audit_rc=$?
}

# THE BUDGET: the suite outlives a lowered budget and starts a child that ignores TERM. The row asserts
# the overrun report, the bound, and that nothing from the suite's tree survives.
r="$tmp/audit_overrun"
audit_fixture "$r"
audit_marker="AUDIT_OVERRUN_MARKER_$$_$RANDOM"
# shellcheck disable=SC2016  # the generated suite's own text
printf '#!/usr/bin/env bash\npython3 -c "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)" %s &\nsleep 20\n' \
  "$audit_marker" > "$r/scripts/tests/test_audit.sh"
audit_t0=$SECONDS
drive_audit "$r" "$hooks" AUDIT_TEST_BUDGET=3
audit_elapsed=$((SECONDS - audit_t0))
check_eq "$audit_rc" 2 'audit: a suite running past the budget -> exit 2'
check_has "$audit_err" 'GATE DID NOT COMPLETE' 'audit: the overrun is reported as an incomplete gate'
check_has "$audit_err" 'killed mid-run: scripts/tests/test_audit.sh' 'audit: the overrun names the suite it killed'
if [ "$audit_elapsed" -lt 15 ]; then
  pass_line "audit: the budget bound the run (${audit_elapsed}s)"
else
  fail_line "audit: the budget did not bind (took ${audit_elapsed}s against a 3s budget)"
fi
sleep 1
if pgrep -f "$audit_marker" >/dev/null 2>&1; then
  fail_line 'audit: a process from the killed suite tree SURVIVED the overrun'
  pkill -f "$audit_marker" >/dev/null 2>&1 || true
else
  pass_line 'audit: no process from the killed suite tree survived'
fi

# The override may only LOWER the budget: a value above the declared budget is ignored, so a fast
# failing suite still reports as a failure, not as an overrun or a pass.
r="$tmp/audit_override_high"
audit_fixture "$r"
printf '#!/usr/bin/env bash\necho audit-suite-said-no\nexit 1\n' > "$r/scripts/tests/test_audit.sh"
drive_audit "$r" "$hooks" AUDIT_TEST_BUDGET=1000
check_eq "$audit_rc" 2 'audit: a failing suite under an ignored override -> exit 2'
check_has "$audit_err" 'audit engine test suite FAILED' 'audit: the failure is reported as a failure'
check_has "$audit_err" 'audit-suite-said-no' 'audit: the failing suite output is relayed'

# THE LIBRARY, absent beside the hook -> refuse by name.
mkdir -p "$tmp/nolib_hooks"
cp "$hooks/audit-test.sh" "$tmp/nolib_hooks/audit-test.sh"
r="$tmp/audit_nolib"
audit_fixture "$r"
printf '#!/usr/bin/env bash\nexit 0\n' > "$r/scripts/tests/test_audit.sh"
drive_audit "$r" "$tmp/nolib_hooks"
check_eq "$audit_rc" 2 'audit: library absent beside the hook -> exit 2'
check_has "$audit_err" 'bounded-run library' 'audit: the absent library is named'

# python3 cannot start a suite session (the launcher needs os.setsid) -> the gate did not run. The shim
# fails every python3 call, so without the probe this would surface as a misattributed suite failure.
r="$tmp/audit_no_python"
audit_fixture "$r"
printf '#!/usr/bin/env bash\nexit 0\n' > "$r/scripts/tests/test_audit.sh"
mkdir -p "$r/shim"
printf '#!/bin/sh\nexit 1\n' > "$r/shim/python3"
chmod +x "$r/shim/python3"
drive_audit "$r" "$hooks" PATH="$r/shim:$PATH"
check_eq "$audit_rc" 2 'audit: owner + python3 cannot start a session -> exit 2'
check_has "$audit_err" 'cannot start a suite session' 'audit: the unusable python3 is named'

# The same, in a repo that does NOT own the hook -> inert.
r="$tmp/audit_no_python_nonowner"
mkrepo "$r"
mkdir -p "$r/skills/audit" "$r/scripts/tests" "$r/shim"
printf 'x\n' > "$r/skills/audit/audit.sh"
printf '#!/usr/bin/env bash\nexit 0\n' > "$r/scripts/tests/test_audit.sh"
printf '#!/bin/sh\nexit 1\n' > "$r/shim/python3"
chmod +x "$r/shim/python3"
drive_audit "$r" "$hooks" PATH="$r/shim:$PATH"
check_eq "$audit_rc" 0 'audit: NON-owner + python3 cannot start a session -> exit 0 (inert)'

# CONTROL — green before AND after this change by construction; it earns its keep by going red the
# day a real gate imports the tokenizer without a DEPENDENTS row. It copies this repo's WHOLE
# candidate population (every non-test .py git lists), not a hand list of known importers, so a
# ninth importer lands in it automatically.
ppg_real="$here/../.."
r="$tmp/ppg_real_population"
mkrepo "$r"
ppg_copied=0
while IFS= read -r f; do
  case "$f" in
    tests/* | */tests/* | fixtures/* | */fixtures/*) continue ;;
  esac
  if [ -f "$ppg_real/$f" ]; then
    mkdir -p "$r/$(dirname "$f")"
    cp "$ppg_real/$f" "$r/$f"
    ppg_copied=$((ppg_copied + 1))
  fi
done <<EOF
$(git -C "$ppg_real" ls-files -co --exclude-standard -- '*.py')
EOF
mark_owner "$r/scripts/$ppg"
for s in $ppg_union; do write_stub_suite "$r/$s"; done
# Measured 2026-09-14: 31 candidate files. A floor well under that still catches an empty copy.
if [ "$ppg_copied" -ge 25 ]; then
  pass_line "ppg control: the real candidate population was copied ($ppg_copied files)"
else
  fail_line "ppg control: only $ppg_copied candidate files copied (expected >= 25)"
fi
ppg_absent=""
for f in scripts/push-guard.py scripts/git-timing-guard.py scripts/explain-git-command.py \
  scripts/recast-commit-gate.py scripts/publication-push-guard.py scripts/commit-subject-guard.py \
  scripts/commit-subject-advisor.py scripts/lib/commit_subject.py; do
  [ -f "$r/$f" ] || ppg_absent="$ppg_absent $f"
done
check_eq "$ppg_absent" "" 'ppg control: all eight importers measured on 2026-09-14 are in the copy'
drive_ppg "$r"
check_eq "$ppg_rc" 0 'ppg CONTROL: every real importer of the tokenizer is wired -> exit 0'

printf '\n%d passed, %d failed\n' "$pass" "$fail"
[ "$fail" -eq 0 ]

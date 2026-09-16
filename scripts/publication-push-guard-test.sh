#!/usr/bin/env bash
set -euo pipefail

# Script: publication-push-guard-test.sh
# Purpose: PostToolUse hook — run the suites of every gate built on git_command.py when a gate, its suite, or the tokenizer changes
# Usage: Called by Claude Code hooks with JSON on stdin
# Ownership sentinel (do not remove): dotclaude-test-runner-hook
#
# Exit codes:
#   0 — no action needed, or every suite this edit requires ran and passed (brief note on stdout)
#   2 — a suite failed; a required suite is absent or unrunnable; a file depends on the tokenizer
#       with no suite wired for it; the bounded-run library is unreadable; or the run did not
#       finish inside its budget (stderr to Claude)
#
# WHAT RUNS. DEPENDENTS, below, is the one declaration of which suites test each gate built on
# scripts/lib/git_command.py. An edit to a gate, or to one of its suites, runs that gate's row. An
# edit to the tokenizer runs its own unit suites (the scripts/tests/test_git_command*.py glob, with
# a floor) and then every row, deduplicated, in table order.
#
# WHY A TABLE AND A DERIVATION. On a tokenizer edit in a repo that owns this hook, the set of files
# that depend on the tokenizer is also DERIVED from source and held against the table both ways: a
# dependent with no row alarms (a gate nobody wired), and a row whose gate exists but is no longer
# discovered alarms (a discovery that narrowed). A Python file that mentions git_command in a form
# discovery cannot classify alarms too. Discovery reads the Python files git lists — tracked or
# untracked, not ignored — outside any tests/ or fixtures/ directory, and follows imports through
# scripts/lib modules. It does NOT see non-Python consumers (skills/audit/audit.sh checks that the
# installed module imports, which is not a behavioural dependency), git-ignored files, code that
# assembles the module name at runtime without the literal text git_command, or a file that reaches
# the tokenizer only through a scripts/lib module by an import form the pattern does not match
# (e.g. import os, commit_subject) — the tripwire looks only for the literal text git_command. In a
# repo that does not own this hook every alarm stays silent, but a repo holding a gate and all of
# that gate's suites still gets those suites run, as the previous hook did for its one guard.
# DEPENDENTS is read from the copy of this hook the harness runs — the installed one — while
# ownership and suites come from the edited repo: a branch that adds a gate and its row alarms "no
# suite is wired" on tokenizer edits until it is promoted (the loud direction), and an edit to that
# new gate runs nothing until then.
#
# THE BUDGET. Claude Code discards a hook's output when it outlives its registered timeout, and
# Claude never learns that it did — for a gate, a silent fail-open. So this hook bounds itself
# below its registration (600 s in settings.json; scripts/tests/test_hook_budget.py pins
# HOOK_BUDGET_SECS below it) and reports an overrun as exit 2. Each suite runs in its own session,
# so an overrun stops its whole process tree — TERM to the group, then KILL to whatever in it
# ignored TERM (a descendant that starts a session of its own escapes both).
# The mechanism lives in scripts/lib/hook_budget.sh beside this hook; without it the hook refuses
# rather than run unbounded.
# PUBLICATION_PUSH_GUARD_TEST_BUDGET may LOWER the budget (the guard suite uses it); any other
# value is ignored. A stray export lowers it on real edits too — the loud direction. Measured
# 2026-09-14, driving this hook on a tokenizer edit: 345 s on a quiet machine, and 341 s in ONE
# observation with scripts/tests/test_audit.sh running alongside for about two thirds of it. That
# is under 2x headroom against the budget, so a heavily loaded machine can overrun — by design an
# alarm, never a silent pass.
#
# Global hook: fires on every Edit|Write in every repo, and exits 0 fast for anything else.

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

# ---------- The one declaration of what tests each tokenizer-built gate ----------
# gate|suites. Rows are in run order: cheap and high-value first, test_recast_hooks.sh (the slow one;
# it boots sandbox repos) last, so an overrun on a loaded machine still yields the most verdicts.
DEPENDENTS='
scripts/publication-push-guard.py|scripts/tests/test_publication_push_guard.sh scripts/tests/test_guard_corpus.py scripts/tests/test_guard_internals.py
scripts/push-guard.py|scripts/tests/test_push_guard.sh
scripts/git-timing-guard.py|scripts/tests/test_git_timing_guard.sh
scripts/explain-git-command.py|scripts/tests/test_explain_git_command.py
scripts/commit-subject-guard.py|scripts/tests/test_commit_subject_guard.sh
scripts/commit-subject-advisor.py|scripts/tests/test_commit_subject_guard.sh
scripts/lib/commit_subject.py|scripts/tests/test_commit_subject.py
scripts/recast-commit-gate.py|scripts/tests/test_recast_hooks.sh
'

# ---------- Cheap guard: which rows does this edit select? ----------
# '*' spans '/' in case patterns, so every match is at any depth. Nothing forks before an unrelated
# edit exits.
tokenizer=0
case "$file_path" in
  */scripts/lib/git_command.py) tokenizer=1 ;;
esac

# Newline-delimited and deduplicated in first-seen order; the surrounding newlines make membership
# an exact-line test.
selected=$'\n'
while IFS='|' read -r gate suites; do
  if [[ -z "$gate" ]]; then
    continue
  fi
  read -r -a row_suites <<< "$suites"
  hit=$tokenizer
  case "$file_path" in */"$gate") hit=1 ;; esac
  for s in ${row_suites[@]+"${row_suites[@]}"}; do
    case "$file_path" in */"$s") hit=1 ;; esac
  done
  if [[ "$hit" -eq 1 ]]; then
    for s in ${row_suites[@]+"${row_suites[@]}"}; do
      case "$selected" in
        *$'\n'"$s"$'\n'*) ;;
        *) selected="${selected}${s}"$'\n' ;;
      esac
    done
  fi
done <<< "$DEPENDENTS"

if [[ "$tokenizer" -eq 0 && "$selected" == $'\n' ]]; then
  exit 0
fi

# ---------- Resolve repo root ----------
# Environment fail-open: not a git repo at all. Deliberate, unchanged.
root=$(git -C "$(dirname "$file_path")" rev-parse --show-toplevel 2>/dev/null || true)
if [[ -z "$root" ]]; then
  exit 0
fi

# ---------- Ownership ----------
# Proven from THIS hook's own source inside the edited repo, which requires nothing from a suite
# whose absence is in question — so a deletion cannot conceal itself, and a foreign repo stays
# inert. Every alarm below is gated on it.
owner=0
if grep -q 'dotclaude-test-runner-hook' "$root/scripts/$(basename "$0")" 2>/dev/null; then
  owner=1
fi

# refuse LINE... — exit 2 with LINEs on stderr in an owning repo; exit 0, inert, anywhere else. A
# non-owner holding only SOME suites must stay inert rather than EXECUTE whichever repo-supplied
# scripts happen to exist (user privileges, on every edit in every repo).
refuse() {
  if [[ "$owner" -eq 1 ]]; then
    printf '%s\n' "$@" >&2
    exit 2
  fi
  exit 0
}

# ---------- The suites this edit requires ----------
# The git_command unit suites are DISCOVERED by glob, so a sibling added later is covered the day it
# lands. A glob cannot tell "never existed" from "deleted", so this floor names the members whose
# ABSENCE must alarm; the glob only ever adds to it.
git_command_floor=(
  scripts/tests/test_git_command.py
  scripts/tests/test_git_command_properties.py
)
required=()
if [[ "$tokenizer" -eq 1 ]]; then
  for suite in "$root"/scripts/tests/test_git_command*.py; do
    if [[ -f "$suite" ]]; then
      required+=("${suite#"$root"/}")
    fi
  done
fi
while IFS= read -r s; do
  if [[ -n "$s" ]]; then
    required+=("$s")
  fi
done <<< "$selected"

# ---------- Every required suite must be present AND runnable before any runs ----------
# A suite that cannot run is the same event as a missing one: the gate did not run.
missing=()
if [[ "$tokenizer" -eq 1 ]]; then
  for f in "${git_command_floor[@]}"; do
    if [[ ! -f "$root/$f" ]]; then
      missing+=("  $f")
    fi
  done
fi
have_python=1
command -v python3 >/dev/null 2>&1 || have_python=0
have_pytest=0
if [[ "$have_python" -eq 1 ]] && python3 -c 'import pytest' >/dev/null 2>&1; then
  have_pytest=1
fi
for s in ${required[@]+"${required[@]}"}; do
  if [[ ! -f "$root/$s" ]]; then
    missing+=("  $s")
  elif [[ "$s" == *.py && "$have_pytest" -eq 0 ]]; then
    missing+=("  $s (present, but pytest is unavailable)")
  fi
done
if [[ "$have_python" -eq 0 ]]; then
  missing+=("  python3 is unavailable (every suite is launched through it, in its own session)")
fi
if [[ "${#missing[@]}" -gt 0 ]]; then
  refuse "GATE DID NOT RUN — this repo owns $(basename "$0") but these suites are absent or unrunnable:" \
    "${missing[@]}" \
    "To remove this feature deliberately: delete its hook, remove its settings.json registration, then re-run /sync-docs."
fi

# ---------- Derive the dependents and hold them against DEPENDENTS (tokenizer edit, owner only) ----------

# is_row FILE — 0 when DEPENDENTS declares FILE as a gate.
is_row() {
  case "$DEPENDENTS" in
    *$'\n'"$1|"*) return 0 ;;
  esac
  return 1
}

# import_pattern MODULE... — an ERE matching a Python import statement naming any MODULE, at any
# indentation (three of the eight real importers import lazily, inside a function).
import_pattern() {
  local alt
  alt=$(
    IFS='|'
    printf '%s' "$*"
  )
  printf '^[[:space:]]*(import[[:space:]]+(%s)([[:space:]]|,|$)|from[[:space:]]+(%s)[[:space:]]+import([[:space:]]|$))' \
    "$alt" "$alt"
}

# discover — fills `discovered` (files that depend on the tokenizer, directly or through a
# scripts/lib module) and `mentions` (files that contain the text git_command but match no import).
# Returns non-zero on ANY failure; the caller turns that into an alarm, never an empty set.
discovered=()
mentions=()
discover() {
  local listing f mod pattern changed rc
  local -a candidates=()
  local -a mods=(git_command)
  # core.quotePath=false prints non-ASCII names raw. Git still quotes a name holding a quote, a
  # backslash or a control character, and such a name cannot be read back as a path — so any quoted
  # entry is a discovery failure, never a silently skipped candidate.
  listing=$(git -C "$root" -c core.quotePath=false ls-files -co --exclude-standard -- '*.py') || return 1
  while IFS= read -r f; do
    case "$f" in
      \"*) return 1 ;;
      '' | tests/* | */tests/* | fixtures/* | */fixtures/*) continue ;;
    esac
    if [[ -f "$root/$f" ]]; then
      candidates+=("$f")
    fi
  done <<< "$listing"

  changed=1
  while [[ "$changed" -eq 1 ]]; do
    changed=0
    pattern=$(import_pattern "${mods[@]}")
    for f in ${candidates[@]+"${candidates[@]}"}; do
      case "$f" in
        scripts/lib/*.py) ;;
        *) continue ;;
      esac
      mod=${f##*/}
      mod=${mod%.py}
      case " ${mods[*]} " in *" $mod "*) continue ;; esac
      rc=0
      grep -qE -- "$pattern" "$root/$f" || rc=$?
      if [[ "$rc" -eq 0 ]]; then
        mods+=("$mod")
        changed=1
      elif [[ "$rc" -ne 1 ]]; then
        return 1
      fi
    done
  done

  pattern=$(import_pattern "${mods[@]}")
  for f in ${candidates[@]+"${candidates[@]}"}; do
    if [[ "$f" == scripts/lib/git_command.py ]]; then
      continue
    fi
    rc=0
    grep -qE -- "$pattern" "$root/$f" || rc=$?
    if [[ "$rc" -eq 0 ]]; then
      discovered+=("$f")
      continue
    fi
    if [[ "$rc" -ne 1 ]]; then
      return 1
    fi
    rc=0
    grep -qF -- git_command "$root/$f" || rc=$?
    if [[ "$rc" -eq 0 ]]; then
      mentions+=("$f")
    elif [[ "$rc" -ne 1 ]]; then
      return 1
    fi
  done
  return 0
}

if [[ "$tokenizer" -eq 1 && "$owner" -eq 1 ]]; then
  if ! discover; then
    refuse "GATE DID NOT RUN — discovery of the files that depend on git_command failed in $root," \
      "so this hook cannot tell which suites the edit requires. Fix the failure; do not skip the gate."
  fi
  problems=()
  for f in ${discovered[@]+"${discovered[@]}"}; do
    if ! is_row "$f"; then
      problems+=("  $f depends on git_command (directly or through scripts/lib) but no suite is wired for it — add a DEPENDENTS row")
    fi
  done
  discovered_lines=$'\n'
  for f in ${discovered[@]+"${discovered[@]}"}; do
    discovered_lines="${discovered_lines}${f}"$'\n'
  done
  while IFS='|' read -r gate _; do
    if [[ -z "$gate" || ! -f "$root/$gate" ]]; then
      continue
    fi
    case "$discovered_lines" in
      *$'\n'"$gate"$'\n'*) ;;
      *) problems+=("  discovery no longer sees $gate, which DEPENDENTS declares — the import predicate or its closure has narrowed") ;;
    esac
  done <<< "$DEPENDENTS"
  for f in ${mentions[@]+"${mentions[@]}"}; do
    if ! is_row "$f"; then
      problems+=("  $f mentions git_command in a form discovery cannot classify (a multi-module or dotted import line, an importlib or __import__ load, or just a comment?) — give the import a line of its own, add a DEPENDENTS row, or reword the mention")
    fi
  done
  if [[ "${#problems[@]}" -gt 0 ]]; then
    refuse "GATE DID NOT RUN — the files that depend on git_command do not match the suites wired for them:" \
      "${problems[@]}"
  fi
fi

# ---------- Budget ----------
# SECONDS counts from this shell's start, so the deadline covers everything above as well. An
# environment-exported SECONDS shifts that origin (measured: `env SECONDS=1000 bash -c 'echo $SECONDS'`
# prints 1000 on bash 3.2 and 5).
HOOK_BUDGET_SECS=540
budget=$HOOK_BUDGET_SECS
override=${PUBLICATION_PUSH_GUARD_TEST_BUDGET:-}
case "$override" in
  '' | *[!0-9]* | ?????*) ;; # empty, non-numeric, or five or more digits: ignored
  *)
    if [[ "$((10#$override))" -gt 0 && "$((10#$override))" -lt "$HOOK_BUDGET_SECS" ]]; then
      budget=$((10#$override))
    fi
    ;;
esac
deadline=$budget

# ---------- Run ----------
# The bounded-run library is resolved from this hook's own directory, so its absence is a broken
# install of the hook rather than a property of the edited repo: alarm whatever repo was edited.
hook_lib="$(dirname "$0")/lib/hook_budget.sh"
if [[ ! -r "$hook_lib" ]]; then
  printf '%s\n' \
    "GATE DID NOT RUN — $(basename "$0") cannot read its bounded-run library $hook_lib, so it cannot run suites below its registered timeout." \
    "The hook's install is broken: restore scripts/lib/hook_budget.sh beside it." >&2
  exit 2
fi
# shellcheck source=lib/hook_budget.sh
. "$hook_lib"

tmpdir=$(mktemp -d "${TMPDIR:-/tmp}/ppg-test.XXXXXX" 2>/dev/null) \
  || refuse "GATE DID NOT RUN — could not create a temporary directory for suite output."

# `live` holds the running suite's pid while hb_run_bounded polls it, so the EXIT trap can stop a
# suite this hook is interrupted in the middle of.
live=""
suite_rc=""
# shellcheck disable=SC2329  # invoked via the EXIT trap
cleanup() {
  if [[ -n "$live" ]]; then
    hb_kill_suite "$live"
  fi
  rm -rf "$tmpdir"
}
trap cleanup EXIT
trap 'exit 2' TERM INT

ran=()
failures=()
not_run=()
killed=""

# run_suite RELPATH — run one suite in its own session, bounded by the deadline. Every outcome is
# recorded in ran/failures/not_run/killed; a non-zero status never escapes (set -e would turn it
# into rc 1, which the harness treats as noise).
run_suite() {
  local rel=$1 out
  if [[ -n "$killed" || "$SECONDS" -ge "$deadline" ]]; then
    not_run+=("$rel")
    return 0
  fi
  out="$tmpdir/${rel//\//_}.out"
  ran+=("${rel##*/}")
  if [[ "$rel" == *.py ]]; then
    hb_run_bounded suite_rc live "$deadline" "$root" "$out" python3 -m pytest -p no:cacheprovider -q "$rel"
  else
    hb_run_bounded suite_rc live "$deadline" "$root" "$out" bash "$rel"
  fi
  if [[ "$suite_rc" == killed ]]; then
    killed=$rel
    return 0
  fi
  if [[ "$suite_rc" -ne 0 ]]; then
    failures+=("${rel##*/}")
    printf '%s FAILED after editing %s:\n' "${rel##*/}" "$file_path" >&2
    tail -20 "$out" >&2 || true
  fi
  return 0
}

for s in ${required[@]+"${required[@]}"}; do
  run_suite "$s"
done

if [[ -n "$killed" || "${#not_run[@]}" -gt 0 ]]; then
  {
    printf 'GATE DID NOT COMPLETE — %s ran out of its %ss budget after editing %s.\n' \
      "$(basename "$0")" "$budget" "$file_path"
    if [[ -n "$killed" ]]; then
      printf 'killed mid-run: %s — its last output:\n' "$killed"
      tail -20 "$tmpdir/${killed//\//_}.out" || true
    fi
    if [[ "${#not_run[@]}" -gt 0 ]]; then
      printf 'never started:\n'
      printf '  %s\n' "${not_run[@]}"
    fi
    if [[ "${#failures[@]}" -gt 0 ]]; then
      printf 'FAILED before the budget ran out: %s\n' "${failures[*]}"
    fi
    printf 'The harness would have killed this hook silently at its registered timeout. Run each suite named above through scripts/run-long.sh and read its verdict; do not re-edit the file to retry.\n'
  } >&2
  exit 2
fi

if [[ "${#ran[@]}" -eq 0 ]]; then
  exit 0
fi

if [[ "${#failures[@]}" -gt 0 ]]; then
  printf 'publication-push-guard-test: %d/%d suite(s) FAILED (%s)\n' \
    "${#failures[@]}" "${#ran[@]}" "${failures[*]}" >&2
  exit 2
fi

printf 'publication-push-guard-test: %d suite(s) passed (%s)\n' "${#ran[@]}" "${ran[*]}"
exit 0

#!/usr/bin/env bash
set -uo pipefail

# Script: publish-brick.sh
# Purpose: Materialise, prove and commit ONE brick, tagged onto main or re-derived onto dev
# Usage:   publish-brick.sh [--scope <path>] [--artifact-dir <path>] \
#            <version> <endpoint-commit> <subject> [folded-constituent...]
#          publish-brick.sh --dev [--final] [--allow-unsigned] [--audit <path>] [--scope <path>] \
#            [--artifact-dir <path>] <oracle-commit> <subject> <file>...
#
# This is the per-brick engine of /propagate's adopted publish path (step 3-4). ONE brick per
# invocation, on purpose: the operator drives the loop, so the per-brick checkpoint that makes
# the publish path reviewable survives, while the parts a human reads wrong under repetition —
# a verdict line, a tag that silently failed to mint — are read mechanically every time.
#
# It never pushes and never touches the watermark. Publishing (step 6) and advancing
# refs/published/main (step 7) stay foreground, human-authorized, and out of this script.
#
# MATERIALISATION. A brick's file set is the UNION of its constituents' files; its content is
# the ENDPOINT commit's, the endpoint being the last constituent. The file set is partitioned by
# presence AT THE ENDPOINT: a path present there is checked out (`git checkout <endpoint> --
# <files>`), which handles a NON-CONTIGUOUS fold with no scratch branch and no patch application,
# and cannot half-apply the way a conflicting cherry-pick or `git apply` can; a path absent there
# was deleted by the constituent set and is removed with `git rm`. Its precondition is that no
# constituent RENAMES or COPIES a path, because neither checkout nor rm can express those; that
# is asserted up front rather than assumed.
#
# TWO SHAPE ASSERTIONS, because one of them is the half that catches an overreach:
#   A. every brick file is byte-identical to the endpoint — the materialisation reached far enough
#   B. nothing OUTSIDE the brick's file set changed — the materialisation reached no further
#
# WHICH COPY OF WHAT. The two resolutions differ on purpose:
#   * the AUDIT comes from the SCOPE (`<scope>/skills/audit/audit.sh`) — it judges the tree being
#     built, so it must be that tree's own copy, never the installed one it may be replacing.
#     The one exception is dev mode in a scope with NO skills/audit/audit.sh of its own, neither
#     in the worktree nor in HEAD's tree: it may name an audit with `--audit <absolute path outside
#     the scope>`, explicit on every brick and never automatic, graded by the same allowlist.
#     The scope is shown to have none by two things together. An allowlist over the path's
#     components, in the worktree AND in HEAD: `skills` and `skills/audit` must each be absent or a
#     real directory (a symbolic link, a submodule or any other type is never followed and never
#     admitted) and the file itself absent. And a precondition that git's own view of the worktree
#     is faithful, because `git status` reads clean in states where the worktree does not hold HEAD:
#     the whole index must carry no skip-worktree and no assume-unchanged entry, sparse checkout
#     must be off, and a status that trusts no fsmonitor must agree with the configured one.
#     Status then vouches for every file and link entry, but for no submodule entry (an
#     uninitialised one is an empty directory that reads clean, as is a populated or deleted one
#     under `ignore = dirty`), so no submodule entry may be the same directory, by file identity, as
#     `skills` or `skills/audit`, and none may be missing from disk.
#     `--audit` is refused in a scope that has its own audit file, when HEAD tracks one the worktree
#     lacks, and on every other reading, a judge that cannot answer included. The property is
#     bounded: it guards against accident and ordinary git features. It does NOT defend against
#     tampering with the objects or the environment by the operator who runs the engine (replace
#     refs, grafts, GIT_* steering): those redirect every read the engine can make, and an operator
#     who can do that can commit by hand.
#   * this script's own helper library resolves relative to THIS FILE — a construction tool must
#     not vanish mid-build when the working tree is checked out to a commit predating it
# Both paths are printed, so a verdict is never reported without saying what produced it.
#
# THE AUDIT VERDICT IS AN ALLOWLIST. A brick is proven only when audit.sh's LAST non-blank line
# of stdout is `RESULT: PASS rc=0…` AND the process exited 0. FAIL, ERROR, INCOMPLETE, an
# unanticipated status, a line that is not last, and an ABSENT line are each a failure to prove.
# The absent one is the one to watch: a killed sweep prints a prefix of PASS lines and no
# summary, so every cheap instrument reads it as clean. A verdict/rc disagreement also fails —
# fail closed, and name which side disagreed.
#
# ROLLBACK IS LIMITED TO THIS SCRIPT'S OWN MESS. A refusal BEFORE the commit restores exactly the
# paths this run wrote, so the next attempt still meets its clean-tree precondition. A failure
# AFTER the commit lands prints the recovery and runs none of it — resetting a branch and
# deleting tags is the mutating class the publish path keeps human-checkpointed.
#
# DEV MODE (--dev). The same engine builds one brick of /feature's adopted re-derivation onto `dev`:
# the ORACLE is the frozen feature tip, and the brick's files are listed explicitly, because that
# re-derivation partitions one tree BY FILE rather than by constituent commits. What changes:
#   * preconditions — branch `dev`; the oracle is a commit; a PROGRESS INVARIANT (every path dev has
#     changed since it last shared history with the oracle already equals the oracle, which catches
#     dev having moved after the branch was cut, and an earlier brick that diverged — the one failure
#     tip convergence cannot see, since it passes by reverting dev's interim work); every listed file
#     differs between HEAD and the oracle; the subject is conventional and under the repo's
#     .commit-conventions.toml ADVISE threshold, because a commit made inside this script never
#     reaches the PreToolUse subject guard that /commit's path relies on
#   * signing — before anything is written the effective `commit.gpgsign` must read true (one
#     predicate, `signing_requested`, shared with the post-commit readback); `--allow-unsigned`
#     waives ONLY that assertion, never the readback, and every brick prints its `%G?`. Once the
#     assertion has run its decision is binding: the readback demands a signature whatever
#     `commit.gpgsign` reads after the commit, since the brick's own checkout can change the config
#   * the audit artifact gains AUDIT_PATH, AUDIT_SOURCE (scope or override) and AUDIT_SHA256, the
#     digest of the entry script taken immediately before it runs (the helpers it sources are not
#     hashed)
#   * no CHANGELOG entry, no tag, no version
#   * after the audit it reports how many paths still differ; `--final` asserts there are none
# Renames need no refusal here: a rename is its two paths, listed, and presence at the oracle
# already checks out the new one and removes the old.
#
# Exit codes:
#   0 — the brick is applied, proven and committed (and, in publish mode, tagged)
#   1 — an assertion failed; the brick is NOT proven
#   2 — usage error (bad flags/arguments, unreadable scope, not an adopted repo, a refused --audit
#       or --allow-unsigned)
#   129/130/143 — died on a trapped signal (HUP/INT/TERM)
#
# Terminal verdict line: as its LAST line of stdout, `RESULT: <STATUS> rc=<n> brick=<version>`
# (`brick=dev` in dev mode)
# where STATUS is PASS | FAIL | ERROR (nothing ran) | INCOMPLETE (died partway). Clean is
# EXACTLY `RESULT: PASS` — an allowlist. The line cannot be emitted on SIGKILL, so its ABSENCE
# never means clean: it means the run did not complete.
#
# bash-3.2/BSD-safe. NOTE: no `-e` — a failing assertion must reach its verdict line.

script_name="$(basename "${BASH_SOURCE[0]}")"
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# The audit that sits beside this tool, derived from this file's own location (no $HOME, no PATH lookup).
# It is only ever PRINTED, as a suggestion in a refusal and in usage; the engine never runs it on its own.
audit_dir_real="$(cd "$script_dir/../skills/audit" 2>/dev/null && pwd -P)"
installed_audit="${audit_dir_real:-$script_dir/../skills/audit}/audit.sh"

# git_lit runs a PATHSPEC-BEARING git call with literal pathspecs. A brick file named `a[1].txt` is
# otherwise a glob that also matches `a1.txt`, and a checkout of it materialises both (measured
# 2026-09-16). Deliberately a wrapper and NOT an exported environment variable: that reaches every
# child, and the audit discovers files with pathspec globs — under it the real audit skipped 6 of 17
# checks as "nothing to check" and still printed RESULT: PASS (measured 2026-09-16). git itself
# still passes literal-pathspec mode down to any hook or filter driven BY these calls (a
# clean/smudge filter, a checkout hook) — that reach is unavoidable and not what this wrapper
# guards against. What it guarantees is narrower and load-bearing: the AUDIT is a SEPARATE process
# this script spawns, never a child of these git calls, so it never inherits literal-pathspec mode
# through them either way.
git_lit() { git --literal-pathspecs -C "$scope" "$@"; }

# Model constants, not configuration: the publication model fixes both branch names.
readonly PUBLISHED_BRANCH=main
readonly WORKING_BRANCH=dev

version=""
scope=""
artifact_dir=""
endpoint=""
subject=""
audit_path=""
audit_source=scope
audit_given=no
audit_override=""
allow_unsigned=no
signing_asserted=no
own_state=unknown
own_why="the judge of the tree's own audit has not run"
lib_path="$script_dir/lib/changelog_entry.py"
subject_lib="$script_dir/lib/commit_subject.py"
mode=publish
final=no
dev_hint=""
dev_note=""
files_raw=""
constituents=""
files_list=""
files=()
phase=init
reported=no
materialised=no
changelog_written=no
committed=no

usage() {
  printf 'Usage: %s [--scope <path>] [--artifact-dir <path>] <version> <endpoint> <subject> [constituent...]\n' \
    "$script_name" >&2
  printf '       %s --dev [--final] [--allow-unsigned] [--audit <path>] [--scope <path>] [--artifact-dir <path>] <oracle> <subject> <file>...\n' \
    "$script_name" >&2
  printf '       --audit <absolute path> is legal only when the scope has no skills/audit/audit.sh; the installed audit is %s\n' \
    "$installed_audit" >&2
}

result_line() { # status rc
  printf 'RESULT: %s rc=%d brick=%s\n' "$1" "$2" "${version:-?}"
  reported=yes
}

# Covers only the paths main() never reaches the end of, so it can emit ONLY ERROR or
# INCOMPLETE — never a clean verdict.
on_exit() { # exit-status
  [ "$reported" = yes ] && return
  if [ "$phase" = init ] && [ "$1" -eq 2 ]; then
    result_line ERROR "$1"
  else
    result_line INCOMPLETE "$1"
  fi
}

# A fatal precondition prints its reason to STDOUT as well: stdout is the artifact an operator
# (or a transcript) reads back, and a refusal whose cause lives only on a discarded stderr is
# indistinguishable from an unexplained one.
fatal() { # message
  printf '%s: %s\n' "$script_name" "$1"
  usage
  result_line ERROR 2
  exit 2
}

# rollback_worktree restores ONLY the paths this run wrote. A path that did not exist at HEAD is
# removed rather than checked out, since `git checkout HEAD -- <new path>` has nothing to restore.
rollback_worktree() {
  local f
  [ "$materialised" = yes ] || return 0
  [ -n "$files_list" ] || return 0
  while IFS= read -r f; do
    [ -n "$f" ] || continue
    if git -C "$scope" cat-file -e "HEAD:$f" 2>/dev/null; then
      git_lit checkout -q HEAD -- "$f" 2>/dev/null
    else
      git_lit reset -q HEAD -- "$f" 2>/dev/null
      rm -f -- "$scope/$f"
    fi
  done < "$files_list"
  if [ "$changelog_written" = yes ]; then
    git_lit checkout -q HEAD -- CHANGELOG.md 2>/dev/null
  fi
}

fail_brick() { # message
  printf 'FAIL %s\n' "$1"
  if [ "$committed" = yes ] && [ "$mode" = dev ] && [ -n "$dev_note" ]; then
    printf '  the brick commit landed on %s. %s\n' "$WORKING_BRANCH" "$dev_note"
  elif [ "$committed" = yes ] && [ "$mode" = dev ]; then
    printf '  the brick commit ALREADY LANDED on %s. Recovery (run it yourself):\n' "$WORKING_BRANCH"
    if [ -n "$dev_hint" ]; then
      printf '    %s\n' "$dev_hint"
    else
      printf '    git -C %s reset --hard HEAD~1   # drop the brick, fix the cause, re-run\n' "$scope"
    fi
  elif [ "$committed" = yes ]; then
    printf '  the brick commit ALREADY LANDED on %s. Recovery (run it yourself):\n' "$PUBLISHED_BRANCH"
    printf '    git -C %s tag -d %s   # only if the tag was minted\n' "$scope" "$version"
    printf '    git -C %s reset --hard HEAD~1\n' "$scope"
  else
    rollback_worktree
    printf '  nothing was committed; the working tree was restored.\n'
  fi
  result_line FAIL 1
  exit 1
}

# assert_applicable refuses the two edit shapes a checkout cannot express, and the ordering
# error that would silently take the wrong content.
assert_applicable() {
  local c bad
  for c in $constituents; do
    git -C "$scope" rev-parse --verify -q "$c^{commit}" >/dev/null \
      || fail_brick "constituent $c is not a commit in $scope"
    git -C "$scope" merge-base --is-ancestor "$c" "$WORKING_BRANCH" 2>/dev/null \
      || fail_brick "constituent $c is not an ancestor of $WORKING_BRANCH — bricks come from $WORKING_BRANCH"
    # Clear by ALLOWLIST: A(dd), M(odify), T(ype change) survive a checkout, D(elete) survives
    # a `git rm`; R, C, U and anything unanticipated do not — materialisation cannot express a
    # rename or a copy.
    bad="$(git -C "$scope" show --name-status --format= "$c" | awk 'NF && $1 !~ /^[AMTD]$/')"
    if [ -n "$bad" ]; then
      printf '  %s\n' "$bad"
      fail_brick "constituent $c renames or copies a path — materialisation cannot express that"
    fi
    if [ "$c" != "$endpoint" ]; then
      git -C "$scope" merge-base --is-ancestor "$c" "$endpoint" 2>/dev/null \
        || fail_brick "constituent $c is NEWER than the endpoint $endpoint — the endpoint must be the last constituent"
    fi
  done
}

# assert_dev_progress is the adopted finish's "dev must not have moved" precondition, checked at
# EVERY brick rather than once at freeze time. Every path dev has changed since it last shared history
# with the oracle must already equal the oracle: a commit landing on dev after the branch was cut
# breaks that, and so does an earlier brick that diverged.
assert_dev_progress() {
  local base f names
  local progress_files=() diverged_files=()
  base="$(git -C "$scope" merge-base HEAD "$endpoint" 2>/dev/null)" \
    || fail_brick "HEAD and the oracle $endpoint share no history — there is nothing to re-derive onto"
  # NUL-separated (-z), not newline-separated: git's default name-only output QUOTES a path
  # holding non-ASCII bytes (e.g. `café.txt` -> "caf\303\251.txt"), which then never matches the
  # literal pathspec built from it below — silently dropping the path from the diverged set
  # (measured 2026-09-16). -z also disables that quoting. A bash variable cannot hold an embedded
  # NUL (command substitution silently drops the byte, concatenating entries), so each list goes to
  # a file and is read from there. A FILE, not a process substitution: `< <(git …)` discards git's
  # exit status, so a diff that errored would read as an empty list — "nothing changed" — and the
  # invariant would pass having measured nothing.
  names="$(mktemp)"
  git -C "$scope" diff -z --name-only --no-renames "$base" HEAD > "$names" \
    || { rm -f "$names"; fail_brick "progress: could not list what $WORKING_BRANCH changed since $base"; }
  while IFS= read -r -d '' f; do
    [ -n "$f" ] && progress_files[${#progress_files[@]}]="$f"
  done < "$names"
  rm -f "$names"
  [ "${#progress_files[@]}" -gt 0 ] || return 0
  names="$(mktemp)"
  git_lit diff -z --name-only --no-renames "$endpoint" HEAD -- "${progress_files[@]}" > "$names" \
    || { rm -f "$names"; fail_brick "progress: could not compare $WORKING_BRANCH's changed paths with the oracle $endpoint"; }
  while IFS= read -r -d '' f; do
    [ -n "$f" ] && diverged_files[${#diverged_files[@]}]="$f"
  done < "$names"
  rm -f "$names"
  [ "${#diverged_files[@]}" -gt 0 ] || return 0
  for f in "${diverged_files[@]}"; do
    printf '  diverged: %s\n' "$f"
  done
  fail_brick "progress: $WORKING_BRANCH changed the paths above since it last met the oracle, and they no longer match it — $WORKING_BRANCH moved after the branch was cut (rebase the branch and re-freeze), or an earlier brick diverged"
}

# check_dev_subject supplies what /commit and the PreToolUse subject guard supply on the ordinary
# commit path, neither of which ever sees a commit made inside this script. The length policy is the
# repo's own, read through the shared library. It refuses at the ADVISE threshold rather than only at
# BLOCK because the post-commit advisor that would flag 72-79 never sees this commit; ALLOW_LONG_SUBJECT=1
# is honoured for both tiers, exactly as /commit documents it.
check_dev_subject() {
  local re='^(feat|fix|docs|style|refactor|perf|test|chore|ci|revert)(\([^)]+\))?!?: [^[:space:]]'
  local grade
  [[ "$subject" =~ $re ]] \
    || fail_brick "the subject is not conventional — want <type>[(scope)][!]: <subject>, got: $subject"
  case "$subject" in
    *.) fail_brick 'the subject must not end with a period' ;;
  esac
  [ -f "$subject_lib" ] || fail_brick "missing helper library: $subject_lib"
  grade="$(python3 -I - "$scope" "$subject" "$(dirname "$subject_lib")" <<'PY'
import sys
sys.path.insert(0, sys.argv[3])
import commit_subject as cs
policy = cs.load_policy(sys.argv[1])
print("inert" if policy is None else cs.classify(sys.argv[2], policy))
PY
)"
  case "$grade" in
    inert|ok) ;;
    advise|block)
      if [ "${ALLOW_LONG_SUBJECT:-}" = 1 ]; then
        printf '  subject: %s characters, over the policy threshold — allowed by ALLOW_LONG_SUBJECT=1\n' "${#subject}"
      else
        fail_brick "the subject is ${#subject} characters, at or over this repo's .commit-conventions.toml threshold — shorten it, or lead with ALLOW_LONG_SUBJECT=1 as /commit allows"
      fi ;;
    *) fail_brick "the subject policy check reached no verdict (got: ${grade:-nothing})" ;;
  esac
}

# report_dev_convergence runs after a PROVEN dev brick. It always says how much of the oracle is
# still outstanding; under --final it asserts nothing is.
report_dev_convergence() {
  local remaining n f
  remaining="$(git -C "$scope" diff --name-only --no-renames "$endpoint" HEAD)"
  n="$(grep -c . <<<"$remaining")"
  printf '  remaining: %s path(s) still differ from the oracle %s\n' "$n" "$endpoint"
  [ "$final" = yes ] || return 0
  if [ -n "$remaining" ]; then
    while IFS= read -r f; do
      [ -n "$f" ] && printf '  still differs: %s\n' "$f"
    done <<EOF
$remaining
EOF
    dev_note="The brick itself is proven and stays; build another brick from the paths above, with --final on the last one."
    fail_brick "--final: HEAD does not converge on the oracle $endpoint"
  fi
  printf '  converged: HEAD tree == oracle %s\n' "$endpoint"
}

# audit_digest prints the sha-256 of <path> as lowercase hex, or NOTHING when the file cannot be read.
# One helper, two callers: check_dev_audit (proof the named audit is READABLE before any write; the value
# is recorded nowhere) and run_audit (the value the artifact records, taken after the commit and
# immediately before the exec). python3 is already a hard dependency through lib_path. EVERY inline python3 in
# this file runs `-I` (isolated: no working-directory or script-directory entry on sys.path, no PYTHON* variables,
# no user site), because a bare `python3 -c` imports from the operator's working directory: a hashlib.py there
# forged the recorded digest. The output is then held to the shape of a sha-256, exactly 64 lowercase hex digits,
# before it is printed, so a value of any other shape reads as "no digest".
audit_digest() { # path
  local d
  d="$(python3 -I -c 'import hashlib, sys
with open(sys.argv[1], "rb") as fh:
    print(hashlib.sha256(fh.read()).hexdigest())' "$1" 2>/dev/null)"
  case "$d" in
    ''|*[!0-9a-f]*) return 0 ;;
  esac
  [ "${#d}" -eq 64 ] || return 0
  printf '%s\n' "$d"
}

# real_path prints <path> with EVERY component resolved, the final one included, or nothing on failure.
# `cd "$(dirname p)" && pwd -P` resolves symlinked DIRECTORIES but not a symlinked FINAL component.
# A RESOLVED path holding a newline is never printed (status 3, nothing on stdout): `$( )` strips trailing
# newlines, so a symlink to `x.sh<newline>` would otherwise resolve to the DIFFERENT file `x.sh`, and an embedded
# newline would split the AUDIT_PATH= line of the artifact. The caller reads status 3 as that refusal.
real_path() { # path
  python3 -I -c 'import os, sys
p = os.path.realpath(sys.argv[1])
if "\n" in p:
    sys.exit(3)
print(p)' "$1" 2>/dev/null
}

# inside_scope judges by FILE IDENTITY, not by spelling: status 0 when <path> is <dir> or lies below it,
# 3 when it does not, 2 when it could not judge. "Outside" is an ALLOWLISTED status and no other status admits
# the override: python3 exits 1 by itself when it fails without running the script (a start-up or site-import
# failure), so 1 must never read as "outside". A string-prefix test on resolved paths is case-sensitive,
# and on a case-insensitive filesystem an in-scope path spelled in another case would pass as outside.
# Named limit: an ancestor walk does not see a HARD LINK to a scope file placed outside the scope; such a
# path is judged outside.
inside_scope() { # path dir
  python3 -I -c 'import os, sys
try:
    p = sys.argv[1]
    want = os.stat(sys.argv[2])
    while True:
        try:
            if os.path.samestat(os.stat(p), want):
                sys.exit(0)
        except OSError:
            pass
        parent = os.path.dirname(p)
        if parent == p:
            sys.exit(3)
        p = parent
except Exception:
    sys.exit(2)' "$1" "$2" 2>/dev/null
}

# worktree_path_state judges ONE path by its errno, never by a shell test: `[ -e ]` is false for every lstat
# error, so a missing file and a path under a directory that cannot be searched read the same. Status 0 when the
# path exists (a dangling symlink included), 3 when lstat says ENOENT, 2 for every other answer. "Absent" is the
# explicit status 3 and no other status admits it: python3 itself exits 1 when it fails without running the script.
worktree_path_state() { # path
  python3 -I -c 'import os, sys
try:
    os.lstat(sys.argv[1])
except FileNotFoundError:
    sys.exit(3)
except Exception:
    sys.exit(2)' "$1" 2>/dev/null
}

# worktree_component_state judges ONE directory component of the audit path, without following a link: status 0
# when it is a real directory, 3 when lstat says ENOENT, 4 when it is a symbolic link, 5 when it is any other file
# type, 2 for every other answer. Only 0 and 3 are admitted by the caller.
worktree_component_state() { # path
  python3 -I -c 'import os, stat, sys
try:
    mode = os.stat(sys.argv[1], follow_symlinks=False).st_mode
except FileNotFoundError:
    sys.exit(3)
except Exception:
    sys.exit(2)
if stat.S_ISLNK(mode):
    sys.exit(4)
sys.exit(0 if stat.S_ISDIR(mode) else 5)' "$1" 2>/dev/null
}

# head_audit_state judges HEAD's tree, component by component, because `git ls-tree` on the literal audit path
# cannot see through a symbolic link stored in HEAD. Status 0 when HEAD's tree holds an entry of ANY type at
# skills/audit/audit.sh; 3 when `skills` and `skills/audit` each hold no entry or a tree (mode 040000) AND nothing
# is listed at the audit path; 4 when either holds anything else (a symbolic link, a submodule, a file); 2 when git
# could not answer (an unborn HEAD, a damaged repository). The "no entry or a tree" clause is LOAD-BEARING, not
# defence in depth: a gitlink at `skills` or `skills/audit` to an uninitialised submodule leaves `git status` clean,
# every index entry plain, sparse checkout off and an empty real directory on disk, so index_faithful admits it and
# this clause refuses it, for the EXACT spelling. It cannot see a case-variant spelling (`Skills`), because `git
# ls-tree` compares names case-sensitively; gitlinks_clear refuses that one, by file identity. Neither is redundant.
head_audit_state() {
  local c listed
  for c in skills skills/audit; do
    listed="$(git -C "$scope" ls-tree HEAD -- "$c" 2>/dev/null)" || return 2
    if [ -n "$listed" ] && [ "${listed%% *}" != 040000 ]; then return 4; fi
  done
  listed="$(git -C "$scope" ls-tree --name-only HEAD -- skills/audit/audit.sh 2>/dev/null)" || return 2
  if [ -n "$listed" ]; then return 0; fi
  return 3
}

# index_faithful is the precondition for every CLAIM of absence, run by judge_absent_own_audit only once its
# allowlist says `absent`. `git status` reads clean in states where the worktree does not hold HEAD, and none of them
# shows in the path allowlist: a skip-worktree entry (a removed file, a sparse checkout's excluded paths) and an
# assume-unchanged entry hide a file from status; and a case-variant spelling of the path in HEAD (`Skills/audit/...`)
# is invisible to `git ls-tree`, which compares case-sensitively, on a volume that folds case. Rather than rebuild the
# volume's folding rules as one more spelling list, the precondition asks whether git's view can be trusted at all.
# Status 0 only when ALL of these hold, and every failure of a read refuses (only an explicit "none" admits):
#   * core.sparseCheckout is false or unset (measured: a sparse-excluded path carries the skip-worktree tag anyway, but a
#     sparse checkout that excludes nothing carries none, and the property is simpler to state with it off);
#   * the WHOLE index, not only paths under skills, lists every entry as plain `H` in `git ls-files -v` (S is
#     skip-worktree, a lowercase letter is assume-unchanged);
#   * a status that trusts no fsmonitor (`-c core.fsmonitor=false`) prints exactly what the configured status prints. A
#     hook that claims "nothing changed" makes the configured status read clean over a removed file (measured), and
#     forcing fsmonitor off exposes it without rewriting the index when `--no-optional-locks` is given (measured: the
#     index bytes are identical). The fsmonitor-valid bits themselves are NOT refused: the builtin daemon marks every
#     clean entry valid (measured), so refusing them would refuse every honest fsmonitor user.
# WHAT IT VOUCHES FOR: when it admits, the worktree equals the index for every file and link entry. It vouches for NO
# gitlink (mode 160000) entry: an uninitialised submodule is an empty directory and a populated or deleted one is
# invisible to status under `ignore = dirty` or `ignore = all`; gitlinks_clear covers that class.
# It runs only when absence is claimed, so a repo with its own present audit and a skip-worktree entry elsewhere is
# untouched. It sets own_why and returns 1 on refusal. Needs git 2.15 or newer for --no-optional-locks.
index_faithful() {
  local cfg rc tags="" bad plain="" forced=""
  cfg="$(git -C "$scope" config --bool --get core.sparseCheckout 2>/dev/null)"; rc=$?
  if [ "$rc" -ne 0 ] && [ "$rc" -ne 1 ]; then
    own_why="core.sparseCheckout could not be read"
    return 1
  fi
  if [ "$cfg" = true ]; then
    own_why="sparse checkout is on (core.sparseCheckout), so the worktree is not expected to hold HEAD's files; turn it off with git sparse-checkout disable first"
    return 1
  fi
  if ! { tags="$(git --no-optional-locks -C "$scope" ls-files -v 2>/dev/null)" \
      && plain="$(git --no-optional-locks -C "$scope" status --porcelain 2>/dev/null)" \
      && forced="$(git --no-optional-locks -C "$scope" -c core.fsmonitor=false status --porcelain 2>/dev/null)"; }; then
    own_why="the index could not be read"
    return 1
  fi
  bad="$(awk 'NF && !/^H / && n++ < 3' <<<"$tags" | tr '\n' ';')"
  if [ -n "$bad" ]; then
    case "$bad" in
      S*|s*)
        own_why="the index holds skip-worktree entries (first: $bad), which hide a missing file from git status; clear them with git update-index --no-skip-worktree first"
        return 1 ;;
      [!h]*)
        own_why="the index holds an entry with a tag other than H (first: $bad; an unmerged path reads M), which git status does not vouch for; resolve it first"
        return 1 ;;
      *)
        own_why="the index holds entries tagged other than plain tracked (first: $bad; assume-unchanged is the usual cause), which hide a changed or missing file from git status; clear them with git update-index --no-assume-unchanged first"
        return 1 ;;
    esac
  fi
  if [ "$plain" != "$forced" ]; then
    own_why="a status that does not trust fsmonitor reports changes that the configured status does not, so the tree cannot be shown clean"
    return 1
  fi
  return 0
}

# gitlinks_clear is the second half of the precondition for a claim of absence, run by judge_absent_own_audit after
# index_faithful. A gitlink's worktree content is the one thing a faithful index does not vouch for, and a gitlink
# spelled `Skills` or `skills/Audit` IS the directory `skills` or `skills/audit` on a volume that folds case, where
# the HEAD-side path allowlist (a case-sensitive `git ls-tree`) cannot see it. This asks the filesystem instead of
# rebuilding its folding rules: for EVERY mode-160000 entry of the index, read NUL-terminated, it refuses when the
# entry's directory is the same file (os.path.samefile) as `skills` or `skills/audit`, and when the entry's directory
# is missing from disk (identity cannot be asked of a path that is not there, and git status shows such a deletion
# only until `ignore` hides it: measured). Those two directories are the audit path's only directory components, so no
# other entry can redirect it. Status 3 is the explicit "no submodule is one of them" verdict and the only one that
# admits; 4, 5 and every other status refuse. It sets own_why and returns 1 on refusal.
gitlinks_clear() {
  local tmp rc
  tmp="$(mktemp)" || { own_why="the submodule entries of the index could not be listed"; return 1; }
  if ! git --no-optional-locks -C "$scope" ls-files -s -z > "$tmp" 2>/dev/null; then
    rm -f "$tmp"
    own_why="the submodule entries of the index could not be listed"
    return 1
  fi
  python3 -I -c 'import os, sys
scope = sys.argv[2]
comps = [os.path.join(scope, c) for c in sys.argv[3:]]
with open(sys.argv[1], "rb") as fh:
    data = fh.read()
for rec in data.split(b"\0"):
    if not rec:
        continue
    meta, _, path = rec.partition(b"\t")
    if meta.split(b" ", 1)[0] != b"160000":
        continue
    entry = os.path.join(scope, os.fsdecode(path))
    try:
        os.stat(entry)
    except FileNotFoundError:
        sys.exit(5)
    for c in comps:
        try:
            same = os.path.samefile(entry, c)
        except FileNotFoundError:
            continue
        if same:
            sys.exit(4)
sys.exit(3)' "$tmp" "$scope" skills skills/audit 2>/dev/null; rc=$?
  rm -f "$tmp"
  case "$rc" in
    3) return 0 ;;
    4) own_why="a submodule entry of the index is the same directory as skills or skills/audit under another spelling, and status does not vouch for a submodule's contents"; return 1 ;;
    5) own_why="a submodule entry of the index has no directory on disk, which status can be told to ignore"; return 1 ;;
    *) own_why="the submodule entries of the index could not be compared with skills and skills/audit"; return 1 ;;
  esac
}

# judge_absent_own_audit runs only when the shell tests found nothing at the audit's worktree path, and sets
# own_state and own_why. The verdict is an ALLOWLIST over the path's components, in the worktree AND in HEAD:
#   worktree: `skills` and `skills/audit` must each be ENOENT or a real directory by lstat (a symbolic link at any
#             component is never followed and never admitted, nor is any other file type), and the audit file
#             itself must be ENOENT
#   HEAD:     `skills` and `skills/audit` must each hold no entry or a tree, and the audit path must hold no entry
# Why components: lstat and `git ls-tree` on the literal audit path both look THROUGH a link, so a tracked
# `skills/audit -> ../tools/audit` whose target audit is skip-worktree and removed, or a link to a directory that is
# not there, read as absent on both sides while the tree's own audit sat behind the link.
# own_state, whose only admitting value is `absent`:
#   absent           every clause holds AND the index is faithful (index_faithful): the tree has no audit of its own
#   tracked-missing  the components are fine and lstat says ENOENT, but HEAD's tree HAS an entry at the audit path
#                    (skip-worktree plus a removed file, a sparse checkout): the tree's own audit is not checked out
#   unknown          any other reading: a link, another file type, an error, or git unable to answer; own_why says
#                    which, in words that say what was found
# (`present` is set by the caller, which found something at the path.) The clean-tree precondition cannot see
# either of the last two, because `git status` reports neither as a change.
judge_absent_own_audit() {
  local rc c
  for c in skills skills/audit; do
    worktree_component_state "$scope/$c"; rc=$?
    case "$rc" in
      0|3) ;;
      4) own_state=unknown; own_why="$c is a symbolic link in the worktree, and a link is never followed to judge the audit behind it"; return 0 ;;
      5) own_state=unknown; own_why="$c is not a directory in the worktree"; return 0 ;;
      *) own_state=unknown; own_why="$c could not be examined in the worktree"; return 0 ;;
    esac
  done
  worktree_path_state "$scope/skills/audit/audit.sh"; rc=$?
  if [ "$rc" -ne 3 ]; then
    own_state=unknown
    own_why="skills/audit/audit.sh could not be examined (a directory on its path cannot be searched)"
    return 0
  fi
  own_why="HEAD could not be read"
  head_audit_state; rc=$?
  case "$rc" in
    0) own_state=tracked-missing ;;
    3) own_state=absent ;;
    4) own_why="HEAD holds a symbolic link, a submodule or a file where skills or skills/audit should be a directory"; own_state=unknown ;;
    *) own_state=unknown ;;
  esac
  if [ "$own_state" = absent ] && ! index_faithful; then
    own_state=unknown
  fi
  if [ "$own_state" = absent ] && ! gitlinks_clear; then
    own_state=unknown
  fi
}

# signing_requested is the ONE predicate for "this repo signs its commits". The dev pre-assertion and
# the post-commit readback both call it, so their disagreement is impossible. `--bool` reads
# yes/on/1/true as true; an unset or unparseable value prints nothing and so reads as not true.
signing_requested() {
  [ "$(git -C "$scope" config --bool --get commit.gpgsign 2>/dev/null)" = true ]
}

# resolve_dev_audit runs in the EARLY slot, dev mode only. Every refusal that depends only on the
# arguments and on whether the scope's audit file exists is a usage error here (fatal, exit 2),
# before the header and before any git-state check. The exit-1 refusals (an absent or unusable audit
# WITHOUT the flag, an unreadable audit) belong to check_dev_audit. Presence is `-e || -L`, never
# executability, so a dangling symlink is not read as absent. A worktree path that reads absent is judged
# further (judge_absent_own_audit sets own_state and own_why): the tree has no audit of its own only when an
# allowlist over the path's components holds in the worktree AND in HEAD, no component being a link, and git's view
# is shown to be faithful (index_faithful). `--audit` is legal only in that state, as far as git's own views can show
# it (the limits are in the header). Every other answer, a judge that could not answer included, refuses it.
resolve_dev_audit() {
  local own="$scope/skills/audit/audit.sh" present=no override_real scope_real inside_rc real_rc
  audit_path="$own"
  audit_source=scope
  if [ -e "$own" ] || [ -L "$own" ]; then present=yes; fi
  case "$present" in
    yes) own_state=present ;;
    *) judge_absent_own_audit ;;
  esac
  [ "$audit_given" = yes ] || return 0
  if [ "$present" = yes ]; then
    if [ -f "$own" ] && [ -x "$own" ]; then
      fatal "the scope has its own skills/audit/audit.sh — --audit is only for a repo with none, so it cannot replace this tree's gate; drop the flag"
    fi
    fatal "the scope has its own skills/audit/audit.sh (exists but is not an executable regular file) — --audit cannot route around a broken tree"
  fi
  case "$own_state" in
    absent) ;;
    tracked-missing) fatal "HEAD tracks skills/audit/audit.sh but the worktree does not have it (a skip-worktree entry or a sparse checkout), so this tree has an audit of its own — --audit cannot replace this tree's gate; restore the file into the worktree and drop the flag" ;;
    *) fatal "could not judge whether the scope has its own skills/audit/audit.sh ($own_why) — --audit is accepted only when the scope is shown to have none" ;;
  esac
  case "$audit_override" in
    /*) ;;
    *) fatal "the --audit path must be absolute, got: $audit_override" ;;
  esac
  case "$audit_override" in
    *$'\n'*) fatal 'the --audit path must not contain a newline' ;;
  esac
  override_real="$(real_path "$audit_override")"; real_rc=$?
  [ "$real_rc" -ne 3 ] || fatal 'the --audit path resolves to a path containing a newline (a symlink target can end in one), which no recorded path can carry'
  [ -n "$override_real" ] || fatal "the --audit path could not be resolved: $audit_override"
  [ -f "$override_real" ] || fatal "the --audit path is not a regular file: $override_real"
  [ -x "$override_real" ] || fatal "the --audit path is not executable: $override_real"
  scope_real="$(real_path "$scope")"
  [ -n "$scope_real" ] || fatal "the scope could not be resolved: $scope"
  inside_scope "$override_real" "$scope_real"; inside_rc=$?
  case "$inside_rc" in
    0) fatal "the --audit path resolves inside the scope ($scope_real): $override_real — an audit inside the tree being built is that tree's own audit under another name" ;;
    3) ;;
    *) fatal "could not judge whether the --audit path is inside the scope ($scope_real): $override_real" ;;
  esac
  audit_path="$override_real"
  audit_source=override
}

# check_dev_audit is the PRECONDITION-slot half of the audit decision (exit 1, via fail_brick). Without
# the flag it refuses an absent audit and, with a DIFFERENT message, a present-but-unusable one: a broken
# tree is not an absence to route around. Under the override resolve_dev_audit has already validated
# the path. Both sources then prove the audit's bytes are readable before anything is written.
check_dev_audit() {
  local pre_digest
  if [ "$audit_source" = scope ]; then
    case "$own_state" in
      tracked-missing) fail_brick "no executable audit at $audit_path: HEAD tracks it but the worktree does not have it (a skip-worktree entry or a sparse checkout). This tree has an audit of its own, so this is not an absence to route around: restore the file into the worktree" ;;
      unknown) fail_brick "could not judge whether $audit_path exists ($own_why): a judge that cannot answer refuses, and nothing was written" ;;
    esac
    if [ ! -e "$audit_path" ] && [ ! -L "$audit_path" ]; then
      fail_brick "no executable audit at $audit_path: absent. This repo has no skills/audit/audit.sh of its own, so a dev brick has nothing to be proven by. Name one explicitly with --audit <absolute path outside the repo> (the installed audit is $installed_audit), or re-derive by hand"
    elif [ ! -f "$audit_path" ] || [ ! -x "$audit_path" ]; then
      fail_brick "no executable audit at $audit_path: the file exists but is not an executable regular file — a broken tree is not an absence to route around"
    fi
  fi
  pre_digest="$(audit_digest "$audit_path")"
  [ -n "$pre_digest" ] || fail_brick "could not compute the audit digest of $audit_path — the named audit must be readable before anything is written"
}

# check_dev_signing is the pre-commit signing assertion. It follows the audit checks and precedes the
# oracle, subject and progress checks, so a mistaken config is reported before (and never masked by)
# a progress refusal. `--allow-unsigned` waives ONLY this assertion: the readback after the commit
# still runs whenever the config says signing is on. Without the flag the assertion's decision is BINDING
# (signing_asserted): the readback then demands a signature whatever the config reads after the commit.
# The refusal prints the RAW setting, read by a
# separate plain --get, because the decision read uses --bool and prints nothing for `maybe`. The printed
# remedy escapes the path (`printf %q`) so it can be pasted when the repo path holds a space.
check_dev_signing() {
  local raw rawrc qscope
  raw="$(git -C "$scope" config --get commit.gpgsign 2>/dev/null)"; rawrc=$?
  if [ -z "$raw" ]; then
    # A bare `[commit] gpgsign` (no value) reads true under --bool but prints nothing under --get.
    if [ "$rawrc" -eq 0 ]; then raw='(no value)'; else raw='(unset)'; fi
  fi
  if [ "$allow_unsigned" = yes ]; then
    printf '  signing: NOT ASSERTED (--allow-unsigned); commit.gpgsign=%s\n' "$raw"
    return 0
  fi
  signing_asserted=yes
  signing_requested && return 0
  qscope="$(printf '%q' "$scope")"
  fail_brick "commit.gpgsign is $raw, not true: dev bricks are signed commits. Fix: git -C $qscope config commit.gpgsign true (and confirm the signer works, or the readback will fail every brick). Only for a repo that deliberately does not sign: re-run with --allow-unsigned."
}

# The audit belonging to the tree being proven, read as an allowlist over its own verdict line.
run_audit() { # artifact-path
  local artifact="$1" out err arc verdict digest=""
  [ -x "$audit_path" ] || fail_brick "no executable audit at $audit_path — the brick cannot be proven"
  if [ "$mode" = dev ]; then
    # Taken HERE, after the commit and immediately before the exec: a brick that lists
    # skills/audit/audit.sh has the live file replaced by now, and the artifact must name the bytes
    # that are about to run, not the ones the brick retired.
    digest="$(audit_digest "$audit_path")"
    [ -n "$digest" ] || fail_brick "could not compute the audit digest of $audit_path immediately before running it"
  fi
  out="$(mktemp)"; err="$(mktemp)"
  "$audit_path" --scope "$scope" >"$out" 2>"$err"; arc=$?
  # The exit status is recorded INSIDE the artifact: its absence there is then itself the signal
  # that the run died, which no external account of the exit code can be trusted to tell us.
  {
    cat "$out"
    printf '\n--- stderr ---\n'
    cat "$err"
    printf 'AUDIT_EXIT_STATUS=%d\n' "$arc"
    if [ "$mode" = dev ]; then
      printf 'AUDIT_PATH=%s\n' "$audit_path"
      printf 'AUDIT_SOURCE=%s\n' "$audit_source"
      printf 'AUDIT_SHA256=%s\n' "$digest"
    fi
  } > "$artifact"
  verdict="$(grep -v '^[[:space:]]*$' "$out" | tail -1)"
  rm -f "$out" "$err"
  printf '  audit: %s\n' "$artifact"
  case "$verdict" in
    "RESULT: PASS rc=0"*)
      if [ "$arc" -ne 0 ]; then
        fail_brick "audit said PASS but exited $arc — a verdict/status disagreement fails closed"
      fi
      printf '  audit verdict: %s\n' "$verdict"
      if [ "$mode" = dev ]; then
        printf '  audit source: %s\n' "$audit_source"
        printf '  audit sha256: %s\n' "$digest"
      fi
      ;;
    "") fail_brick "audit printed NO verdict line (exit $arc) — an absent verdict is not a pass" ;;
    *)  fail_brick "audit verdict is not a pass (exit $arc): $verdict" ;;
  esac
}

main() {
  local c date_str changed f sig head_subject artifact
  local checkout_files=() rm_files=()
  local head_type end_type audit_mode

  while [ $# -gt 0 ]; do
    case "$1" in
      --scope)
        [ $# -ge 2 ] || fatal 'the --scope flag needs a path'
        scope="$2"; shift 2 ;;
      --artifact-dir)
        [ $# -ge 2 ] || fatal 'the --artifact-dir flag needs a path'
        artifact_dir="$2"; shift 2 ;;
      --dev) mode=dev; shift ;;
      --final) final=yes; shift ;;
      --audit)
        [ $# -ge 2 ] || fatal 'the --audit flag needs a path'
        audit_given=yes; audit_override="$2"; shift 2 ;;
      --allow-unsigned) allow_unsigned=yes; shift ;;
      --) shift; break ;;
      -*) fatal "unknown argument: $1" ;;
      *)  break ;;
    esac
  done

  if [ "$mode" = dev ]; then
    [ $# -ge 3 ] || fatal 'dev mode needs <oracle> <subject> <file>...'
    endpoint="$1"; subject="$2"; shift 2
    version=dev
    for f in "$@"; do
      case "$f" in
        *$'\n'*) fatal 'a brick file name must not contain a newline' ;;
        # `--*` only: the engine has no short flags, so a single leading dash (a legitimate file
        # name like `-x.txt`) is never a flag lookalike worth refusing.
        --*) fatal "brick file $f looks like a flag — flags go before <oracle>" ;;
      esac
    done
    files_raw="$(printf '%s\n' "$@")"
  else
    [ "$final" = no ] || fatal 'the --final flag belongs to --dev mode only'
    [ "$audit_given" = no ] || fatal 'the --audit flag belongs to --dev mode only'
    [ "$allow_unsigned" = no ] || fatal 'the --allow-unsigned flag belongs to --dev mode only'
    [ $# -ge 3 ] || fatal 'need <version> <endpoint> <subject>'
    version="$1"; endpoint="$2"; subject="$3"; shift 3
    # The remaining arguments are the folded constituents; the endpoint joins them as the last.
    # Duplicates are dropped, keeping first-seen order.
    constituents="$(printf '%s\n' "$@" "$endpoint" | grep -v '^$' | awk '!seen[$0]++')"

    case "$version" in
      v[0-9]*.[0-9]*.[0-9]*) ;;
      *) fatal "version must look like vX.Y.Z, got: $version" ;;
    esac
  fi
  [ -n "$subject" ] || fatal 'the subject must not be empty'
  case "$subject" in
    *$'\n'*) fatal 'the subject must be a single line' ;;
  esac

  if [ -z "$scope" ]; then
    scope="$(git rev-parse --show-toplevel 2>/dev/null)" \
      || fatal 'no --scope given and the working directory is not a git repository'
  fi
  git -C "$scope" rev-parse --show-toplevel >/dev/null 2>&1 \
    || fatal "not a git repository: $scope"
  scope="$(git -C "$scope" rev-parse --show-toplevel)"
  [ -f "$scope/.publication.toml" ] \
    || fatal "no .publication.toml at $scope — not an adopted repo, so there is no publish path"
  [ -f "$lib_path" ] || fatal "missing helper library: $lib_path"

  if [ "$mode" = dev ]; then
    resolve_dev_audit
  else
    audit_path="$scope/skills/audit/audit.sh"
  fi
  if [ -z "$artifact_dir" ]; then
    artifact_dir="$(mktemp -d)"
  fi
  mkdir -p "$artifact_dir" || fatal "cannot create the artifact directory: $artifact_dir"
  if [ "$mode" = dev ]; then
    # One artifact per brick: dev bricks share no version, so name each for the HEAD it builds on.
    artifact="$artifact_dir/audit-dev-$(git -C "$scope" rev-parse --short HEAD 2>/dev/null || printf 'nohead').txt"
  else
    artifact="$artifact_dir/audit-$version.txt"
  fi

  phase=checking

  if [ "$mode" = dev ]; then
    printf 'brick dev <- oracle %s\n' "$endpoint"
  else
    printf 'brick %s <- %s\n' "$version" "$(printf '%s' "$constituents" | tr '\n' ' ')"
  fi
  printf '  scope:   %s\n' "$scope"
  if [ "$audit_source" = override ]; then
    printf '  audit:   %s (OVERRIDE via --audit: NOT %s; %s has no skills/audit/audit.sh. The verdict is still read as the allowlist.)\n' \
      "$audit_path" "this tree's own audit" "$scope"
  else
    printf '  audit:   %s (the tree being proven)\n' "$audit_path"
  fi
  printf '  helper:  %s (this tool, not the tree)\n' "$lib_path"

  # ---------- preconditions ----------
  if [ "$mode" = dev ]; then
    [ "$(git -C "$scope" rev-parse --abbrev-ref HEAD)" = "$WORKING_BRANCH" ] \
      || fail_brick "not on branch $WORKING_BRANCH — a re-derivation brick lands on $WORKING_BRANCH only"
  else
    [ "$(git -C "$scope" rev-parse --abbrev-ref HEAD)" = "$PUBLISHED_BRANCH" ] \
      || fail_brick "not on branch $PUBLISHED_BRANCH — a brick is appended to the published branch only"
  fi
  [ -z "$(git -C "$scope" status --porcelain)" ] \
    || fail_brick 'the working tree is not clean — materialisation needs a clean base to be provable'
  if [ "$mode" = dev ]; then
    check_dev_audit
    check_dev_signing
    git -C "$scope" rev-parse --verify -q "$endpoint^{commit}" >/dev/null \
      || fail_brick "the oracle $endpoint is not a commit in $scope"
    check_dev_subject
    assert_dev_progress
  else
    git -C "$scope" rev-parse --verify -q "refs/tags/$version" >/dev/null \
      && fail_brick "tag $version already exists — refusing to move a tag that may already be published"

    assert_applicable
  fi

  # ---------- file set: the UNION of every constituent's paths ----------
  files_list="$(mktemp)"
  if [ "$mode" = dev ]; then
    printf '%s\n' "$files_raw" | grep -v '^$' | sort -u > "$files_list"
  else
    for c in $constituents; do
      git -C "$scope" show --name-only --format= "$c"
    done | grep -v '^$' | sort -u > "$files_list"
  fi
  [ -s "$files_list" ] || fail_brick 'the brick has an empty file set'
  while IFS= read -r f; do
    [ -n "$f" ] || continue
    files[${#files[@]}]="$f"
    printf '  file: %s\n' "$f"
  done < "$files_list"

  # ---------- dev: every listed file must differ between HEAD and the oracle ----------
  # A path in neither tree, a typo, and a file an earlier brick already landed all read as "nothing
  # to do" to a checkout; each is refused by name instead of becoming a silently smaller brick.
  if [ "$mode" = dev ]; then
    for f in "${files[@]}"; do
      case "$f" in
        /*|..|../*|*/..|*/../*) fail_brick "brick file $f must be a path relative to the repo root" ;;
        .|./) fail_brick "brick file $f is a directory — list its files" ;;
      esac
      # A directory listed as a pathspec still MATCHES (it is not a glob, so --literal-pathspecs
      # does not exclude it) — everything under it, which shape B then trips over one un-listed
      # path at a time rather than refusing the directory by name (measured 2026-09-16, leaving a
      # staged leftover behind on the FAIL path). Refuse only when the path is a TREE on at least
      # one side and neither side is a blob: a blob on HEAD's side is a file becoming a directory
      # (`a.txt` becoming `a.txt/in`, both listed), which builds. What this does NOT make buildable,
      # measured by review: a directory becoming a file (`d d/in`) passes here and then fails closed
      # in the `git rm` arm — build it as two bricks, `d/in` then `d`; and a gitlink reads as ABSENT
      # to `cat-file -t` when its commit is not in this repository, so the `commit` exceptions below
      # rarely fire and a gitlink beside a tree is refused as a directory (fail closed).
      head_type="$(git -C "$scope" cat-file -t "HEAD:$f" 2>/dev/null)"
      end_type="$(git -C "$scope" cat-file -t "$endpoint:$f" 2>/dev/null)"
      if { [ "$head_type" = tree ] || [ "$end_type" = tree ]; } \
        && [ "$head_type" != blob ] && [ "$head_type" != commit ] \
        && [ "$end_type" != blob ] && [ "$end_type" != commit ]; then
        fail_brick "brick file $f is a directory — list its files"
      fi
      if git_lit diff --quiet --no-renames HEAD "$endpoint" -- "$f"; then
        fail_brick "brick file $f already matches the oracle at HEAD — a typo, a path in neither tree, or one an earlier brick landed"
      fi
      # run_audit below reads the SCOPE's live skills/audit/audit.sh, which still exists until the
      # commit lands (under the scope's own audit; under `--audit` the same hazard falls on the next
      # brick) — a brick that deletes it or drops its exec bit would pass that check and only fail
      # once committed, with nothing left able to prove the NEXT brick either.
      if [ "$f" = skills/audit/audit.sh ]; then
        audit_mode="$(git -C "$scope" ls-tree "$endpoint" -- skills/audit/audit.sh | awk '{print $1}')"
        [ "$audit_mode" = 100755 ] \
          || fail_brick "brick file skills/audit/audit.sh would leave no executable audit of its own, so the NEXT brick could not be proven by it — land it with mode 100755, or in a brick with the audit's replacement, or re-derive by hand"
      fi
    done
  fi

  # ---------- materialise: split the file set by presence at the endpoint ----------
  # A path present at the endpoint is checked out; a path absent there was deleted by the
  # constituent set and is removed with `git rm`. The rm-vs-skip choice is read from presence
  # AT HEAD, decided BEFORE invoking `git rm` — never from `git rm`'s own exit status. rc=128
  # also covers an index lock or a corrupt index, so promoting that code to mean "this is the
  # added-and-deleted case" would hand a SKIP to that whole preimage. A path added and then
  # deleted within this brick's own span is absent at HEAD too, so `git rm` has nothing to
  # remove there — that is the one legitimate SKIP. Any other `git rm` failure stays fatal.
  for f in "${files[@]}"; do
    if git -C "$scope" cat-file -e "$endpoint:$f" 2>/dev/null; then
      checkout_files[${#checkout_files[@]}]="$f"
    elif git -C "$scope" cat-file -e "HEAD:$f" 2>/dev/null; then
      rm_files[${#rm_files[@]}]="$f"
    else
      printf '  skip: %s (added and deleted within this brick — nothing to remove)\n' "$f"
    fi
  done

  # Set before either arm runs: `git checkout <endpoint> -- <mixed set>` is measured to error
  # AND partially apply, so a failure partway through must still be treated as having written
  # to the worktree, or rollback_worktree's own guard (`[ "$materialised" = yes ]`) would skip
  # restoring what already landed.
  materialised=yes
  if [ "${#checkout_files[@]}" -gt 0 ] \
    && ! git_lit checkout "$endpoint" -- "${checkout_files[@]}"; then
    fail_brick "could not materialise the brick from $endpoint"
  fi
  if [ "${#rm_files[@]}" -gt 0 ] \
    && ! git_lit rm -q -- "${rm_files[@]}"; then
    fail_brick "could not remove a deleted path from the brick"
  fi

  # ---------- shape A: the brick's files ARE the endpoint's ----------
  # `git diff` has no --pathspec-from-file, so the pathspecs are passed as arguments.
  if ! git_lit diff --quiet "$endpoint" -- "${files[@]}"; then
    fail_brick "shape A: the brick's files do not match $endpoint after materialisation"
  fi

  # ---------- shape B: and NOTHING else moved ----------
  changed="$( { git -C "$scope" diff --cached --name-only
                git -C "$scope" diff --name-only
                git -C "$scope" ls-files --others --exclude-standard; } | sort -u )"
  while IFS= read -r f; do
    [ -n "$f" ] || continue
    grep -qxF -- "$f" "$files_list" \
      || fail_brick "shape B: $f changed but is not one of the brick's files — the materialisation reached too far"
  done <<EOF
$changed
EOF

  # ---------- changelog, inside the brick commit ----------
  date_str="$(git -C "$scope" log -1 --format=%ad --date=short "$endpoint")"
  if [ "$mode" = dev ]; then
    printf '  changelog: dev mode writes none — versioning is %s-only\n' "$PUBLISHED_BRANCH"
  elif [ -f "$scope/CHANGELOG.md" ]; then
    python3 "$lib_path" "$scope/CHANGELOG.md" "$version" "$date_str" "$subject" \
      || fail_brick 'the changelog entry was refused'
    changelog_written=yes
  else
    printf '  changelog: none at %s — skipping the entry\n' "$scope/CHANGELOG.md"
  fi

  # ---------- commit ----------
  # `git rm` above already staged its own arm; staging it again with `git add` on a path that
  # no longer exists in the worktree fatals (rc=128, "did not match any files"). Only the
  # checkout arm still needs staging.
  if [ "${#checkout_files[@]}" -gt 0 ] && ! git_lit add -- "${checkout_files[@]}"; then
    fail_brick 'could not stage the brick'
  fi
  [ "$changelog_written" = yes ] && git_lit add -- CHANGELOG.md
  git -C "$scope" commit -q -m "$subject" || fail_brick 'the brick commit failed'
  committed=yes

  head_subject="$(git -C "$scope" log -1 --format=%s)"
  [ "$head_subject" = "$subject" ] \
    || fail_brick "the commit subject is [$head_subject], not [$subject]"

  # A batch of commits and tags can exhaust a hardware key's cached PIN partway through, after
  # which signing silently stops. Read back whenever the repo asked for signing, by the SAME
  # predicate as the dev pre-assertion and under --allow-unsigned too: that flag waives the
  # assertion that the config is on, never the check that a configured signature appeared. A dev brick whose
  # assertion RAN (signing_asserted) is read back whatever the config says NOW: the brick's own checkout can
  # change the effective config between the assertion and `git commit` (a shared include file it materialises),
  # and a readback that asked the config again would report "signing not enabled" for a commit the assertion
  # had required to be signed.
  if signing_requested || [ "$signing_asserted" = yes ]; then
    sig="$(git -C "$scope" log -1 --format='%G?')"
    case "$sig" in
      G|U) printf '  commit %s signed (%%G?=%s)\n' "$(git -C "$scope" log -1 --format=%h)" "$sig" ;;
      *)
        signing_requested && fail_brick "commit.gpgsign is true but the commit is not signed (%G?=$sig) — a card PIN may have lapsed"
        fail_brick "the commit is not signed (%G?=$sig): signing was asserted before it and commit.gpgsign no longer reads true, so the effective config changed between the assertion and the commit" ;;
    esac
  elif [ "$mode" = dev ]; then
    printf '  commit %s (signing not enabled in this repo)\n' "$(git -C "$scope" log -1 --format=%h)"
    printf '  %%G?=%s\n' "$(git -C "$scope" log -1 --format='%G?')"
  else
    printf '  commit %s (signing not enabled in this repo)\n' "$(git -C "$scope" log -1 --format=%h)"
  fi

  # ---------- prove ----------
  if [ "$mode" = dev ]; then
    dev_hint="git -C $scope reset --hard HEAD~1   # the brick's files already equal the oracle; read the audit artifact first — a split holistic pair means re-plan the boundary, while a machine-dependent check or a killed audit is not fixed by re-planning"
  fi
  run_audit "$artifact"

  if [ "$mode" = dev ]; then
    dev_hint=""
    report_dev_convergence
    rm -f "$files_list"
    result_line PASS 0
    return 0
  fi

  # ---------- tag, and assert it exists: `git tag -a` can fail SILENTLY mid-run ----------
  git -C "$scope" tag -a "$version" -m "$subject" || fail_brick "git tag -a $version failed"
  git -C "$scope" tag --points-at HEAD | grep -qx "$version" \
    || fail_brick "tag $version is MISSING after tagging — do not build the next brick on an untagged one"
  printf '  tagged %s at %s\n' "$version" "$(git -C "$scope" log -1 --format=%h)"

  rm -f "$files_list"
  result_line PASS 0
  return 0
}

if [ "${BASH_SOURCE[0]}" = "$0" ]; then
  trap 'on_exit "$?"' EXIT
  trap 'exit 143' TERM
  trap 'exit 130' INT
  trap 'exit 129' HUP
  main "$@"
fi

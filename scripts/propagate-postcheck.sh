#!/usr/bin/env bash
set -uo pipefail

# Script: propagate-postcheck.sh
# Purpose: Verify /propagate's LOCAL promote landed and, where configured, that the boundary hook and config farm are current
#
# Chooses the postcondition branch itself instead of leaving that to the operator. Also asserts,
# unconditionally on an adopted repo, that the pre-push boundary hook installed at
# .git/hooks/pre-push is current — the fast-forward alone cannot have updated it (see
# pre-push-installed) — and, where the scope ships an install.sh, that every CURRENT config-farm
# member still links to it, which the fast-forward likewise cannot have done (see farm-current).
# Usage: propagate-postcheck.sh --scope <live> --before-sha <sha256> [--ref <ref>] [--before-head <commit>]
#
# Scope: this script verifies; it changes nothing. It runs AFTER the fast-forward, so every
# recovery it might suggest belongs to the operator and to the skill, not here.
#
# ## Why this exists
#
# `/propagate`'s postcondition BRANCHES, and both arms were hand-run on every promote. The strict
# arm is four separate commands with no single verdict line, which is exactly the shape a human
# performs correctly until the one time they do not. `publish-preflight.sh` is the direct
# precedent, one layer up.
#
# ## The branch, and why the operator must not be the one choosing it
#
# The deciding question is whether `settings.json` was in the incoming range. Its two answers
# call for different instruments:
#
#   * NOT in range — nothing should have touched the runtime file, so it must be BYTE-IDENTICAL
#     (sha256 before vs after), with no stash left parked and `skip-worktree` still set.
#   * IN range — the skill parks the runtime file, fast-forwards, restores it, then HAND-ADDS the
#     hook entries the incoming commit registered. The restored file therefore predates that
#     commit, so byte-identity is the WRONG instrument here: it reads CLEAN precisely when the
#     hand-add was skipped and a registration was dropped.
#
# A wrong branch choice is silent in both directions, which is the defect this closes.
#
# ## The hooks check runs on BOTH branches, on purpose
#
# `hooks-registered` is not gated on the branch. It also fails when a runtime hook `timeout` is
# lower than the committed one. If it ran only on the in-range arm, a mis-determined range would
# once again hide a dropped registration or a lowered timeout — the very failure the branch
# decision exists to prevent. Running it unconditionally means the range determination now affects
# only which DIAGNOSIS you get, never whether a dead gate can ship unnoticed. It is safe to run
# unconditionally because it is one-directional: it reports runtime-only entries without failing
# on them, so a machine-local hook never false-alarms.
#
# ## The BEFORE sha is an argument because the merge destroys it
#
# `settings.json` is `skip-worktree` in production, so its runtime content differs from the index
# by design and cannot be recovered from git afterwards. Capture it BEFORE the merge. That
# ordering is the part a human gets wrong under repetition, so the script refuses to run without
# it rather than computing something that would merely look like it.
#
# The pre-merge HEAD is different: `git merge` records it in `ORIG_HEAD`, so it IS recoverable,
# and `--before-head` exists only to override that default. A supplied or recorded value is
# accepted only if it is an ancestor of HEAD; anything else is refused rather than believed.
#
# ## An undeterminable range fails CLOSED
#
# With no usable pre-merge HEAD the branch is unknown, and the script then REQUIRES byte-identity.
# Wrong in that direction is loud and recoverable (a false FAIL on a legitimate hand-add); wrong
# in the other direction is silent (a clobbered runtime preference nobody sees).
#
# Exit codes:
#   0 — every assertion held; the promote is verified
#   1 — at least one assertion FAILED
#   2 — usage error, or the check could not be made at all (no scope, no before-sha, not a git
#       repo, no runtime settings.json)
#   129/130/143 — died on a trapped signal (HUP/INT/TERM)
#
# Terminal verdict line: every run this script exits from itself prints, as its LAST line of
# stdout, `RESULT: <STATUS> rc=<n> checks=<pass>/<fail>/<skip>` where STATUS is PASS | FAIL |
# ERROR (no checks ran) | INCOMPLETE (died partway). Clean is EXACTLY `RESULT: PASS` — an
# allowlist, so any value not anticipated here reads as not-clean. The line CANNOT be emitted on
# SIGKILL, so its ABSENCE never means clean: it means the run did not complete. PASS/FAIL are
# emitted positionally by main(), never decided inside the exit trap, because `$?` in an EXIT
# trap reads 0 for an untrapped fatal signal — deciding there would print PASS for a killed run.
#
# bash-3.2/BSD-safe: no arrays, no mapfile, no GNU-only flags.
#
# NOTE: no `-e` — one failing assertion must not abort the run before its verdict line.

script_name="$(basename "$0")"
script_dir="$(cd "$(dirname "$0")" && pwd)"

readonly SETTINGS=settings.json
readonly TRACKED_HOOK=git-hooks/pre-push
readonly INSTALLER=scripts/install-git-hooks.sh
readonly FARM_INSTALLER=install.sh

pass_count=0
fail_count=0
skip_count=0

# Both MUST stay global — a `local` copy would be invisible to the exit trap. `phase` goes
# init -> checking and exists only to tell a usage error (nothing ran) from a death partway
# through. `reported` records that a verdict line was already printed, so the trap stays silent
# on the normal path rather than emitting a second one.
phase=init
reported=no

usage() {
  printf 'Usage: %s --scope <live> --before-sha <sha256> [--ref <ref>] [--before-head <commit>]\n' \
    "$script_name" >&2
}

# A fatal precondition prints its reason to STDOUT, not just stderr: stdout is the artifact an
# operator (or a transcript) actually reads back, and an ERROR whose cause lives only on a
# discarded stderr is indistinguishable from an unexplained one.
fatal() { printf '%s: %s\n' "$script_name" "$1"; usage; exit 2; }

verdict_pass() { printf 'PASS %s\n' "$1"; pass_count=$((pass_count + 1)); }
verdict_fail() { printf 'FAIL %s — %s\n' "$1" "$2"; fail_count=$((fail_count + 1)); }
verdict_skip() { printf 'SKIP %s — %s\n' "$1" "$2"; skip_count=$((skip_count + 1)); }

result_line() {
  printf 'RESULT: %s rc=%d checks=%d/%d/%d\n' \
    "$1" "$2" "$pass_count" "$fail_count" "$skip_count"
  reported=yes
}

# Covers only the paths main() never reaches the end of, so it can emit ONLY ERROR or
# INCOMPLETE — never a clean verdict.
on_exit() { # exit-status
  [[ "$reported" == yes ]] && return
  if [[ "$phase" == init && "$1" -eq 2 ]]; then
    result_line ERROR "$1"
  else
    result_line INCOMPLETE "$1"
  fi
}

# file_sha256 PATH -> lowercase hex digest. macOS always ships `shasum`; `sha256sum` is the
# coreutils name and is preferred when present because it is cheaper.
file_sha256() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | cut -d' ' -f1
  else
    shasum -a 256 "$1" | cut -d' ' -f1
  fi
}

lower() { printf '%s' "$1" | tr '[:upper:]' '[:lower:]'; }

# hook_is_a_tracked_version SCOPE PATH -> 0 when PATH's content is a blob TRACKED_HOOK has held
# in HEAD's history. Mirrors install-git-hooks.sh's license so the diagnosis here and the
# installer's decision there cannot disagree about the same file.
hook_is_a_tracked_version() { # scope path
  local scope="$1" path="$2" blob members
  blob="$(git -C "$scope" hash-object -- "$path" 2>/dev/null)"
  [[ -n "$blob" ]] || return 1

  # Accumulate first, then match with a HERESTRING -- never `… | grep -qx`. This file runs under
  # `set -o pipefail`, and `grep -q` exits at its first match, SIGPIPEing whatever writes to it;
  # pipefail surfaces that as 141 and the function reports "not a tracked version" on exactly the
  # inputs that DO match. Measured. Accumulating alone does NOT fix it -- the `printf` feeding
  # grep is just as killable -- so the pipeline has to go, not merely move.
  members="$(git -C "$scope" rev-list HEAD -- "$TRACKED_HOOK" 2>/dev/null \
    | while IFS= read -r c; do
        git -C "$scope" rev-parse -q --verify "$c:$TRACKED_HOOK" 2>/dev/null
      done)"
  # No -F here, and that is deliberate rather than an oversight -- the siblings at :219 and the
  # range check both carry it. `$blob` is `git hash-object` output, guarded non-empty above, so it
  # is 40 or 64 hex characters: no BRE metacharacter, no leading `-`. Adding -F would be inert, and
  # an inert token invites a mutant for it that could only ever SURVIVE, reading as a coverage gap
  # where there is none. The same reasoning covers the `--` the siblings keep: inert there too,
  # retained only for parity with a call whose needle is operator-supplied. Both go live the moment
  # either needle stops being a constant this function derives itself.
  grep -qx "$blob" <<<"$members"
}

# farm_drift_label DRIFT-LINE BEFORE-NAMES UNDETERMINED-REASON BEFORE-REF -> one line saying how
# THIS promote relates to that drift line.
#
# A DRIFT line says the farm disagrees with the tree; it does not say whether this promote caused
# the disagreement. The obvious instrument -- run `--check` before the install too and diff the
# two reports -- cannot answer it: install.sh REFUSES a foreign link rather than repointing it,
# so a foreign link is foreign BEFORE the install every single time. That baseline answers yes
# for every input, which is a check that cannot move.
#
# A promote genuinely causes a foreign-link conflict in exactly ONE way: the merge adds a root
# member whose path already held a link of the operator's own. The link pre-existed; the CONFLICT
# is new, because the path only became managed now. So the discriminator is MEMBERSHIP AT TWO
# COMMITS, not link state at two times -- crossed with which of `--check`'s two DRIFT shapes fired:
#
#                              | NOT a root entry before | WAS a root entry before
#   `resolved to X, want Y`    | INTRODUCED              | PRE-EXISTING
#   `missing or not a live...` | EXPECTED                | REGRESSED
#
# install.sh's EXCLUDE list is deliberately NOT copied here, and does not need to be: a name
# reaching this function was named by the installer, so it is a member NOW, and EXCLUDE is a
# constant within one copy of that script -- presence in the before-tree therefore settles it. A
# hand-made copy of another file's list is a drift this repo has already measured.
#
# DIAGNOSIS, never a gate: every branch here only prints. The caller's PASS/FAIL is decided
# before this runs and is untouched by which label comes back -- a drift failure stays a failure
# whatever caused it, and an UNDETERMINED one must not become a pass.
farm_drift_label() { # drift-line before-names undetermined-reason before-ref
  local line="$1" names="$2" reason="$3" ref="$4" name shape

  name="${line#DRIFT: }"
  name="${name%% (*}"

  case "$line" in
    *' (resolved to '*) shape=resolved ;;
    *' (missing or not a live symlink at '*) shape=missing ;;
    *)
      # An allowlist on the SHAPE: a line the installer emits in some third form is not silently
      # sorted into one of the four cells, because the cell depends on the shape.
      printf 'UNDETERMINED: %s -- this DRIFT line is in neither shape this classifier reads, so which cell it belongs to is unknown\n' \
        "$name"
      return 0
      ;;
  esac

  # Reported, never resolved into a label. Whatever the reason, the preimage of "could not
  # measure" contains cases from BOTH columns, so promoting any of it to a positive verdict
  # would hand that verdict to the rest.
  if [[ -n "$reason" ]]; then
    printf 'UNDETERMINED: %s -- %s\n' "$name" "$reason"
    return 0
  fi

  # A HERESTRING, never `printf | grep -qx`: this file runs under `set -o pipefail`, where
  # `grep -q` exiting at its first match SIGPIPEs the producer and the pipeline reports 141 on
  # exactly the inputs that DO match. `-F` because a member name carries dots.
  if grep -qxF -- "$name" <<<"$names"; then
    case "$shape" in
      resolved)
        printf 'PRE-EXISTING: %s was already a root entry at %s, so this promote neither created that link nor made the path managed -- it is an excursion of your own\n' \
          "$name" "${ref:0:12}" ;;
      *)
        printf 'REGRESSED: %s was already a root entry at %s, so a link that existed has since been removed or written over\n' \
          "$name" "${ref:0:12}" ;;
    esac
  else
    case "$shape" in
      resolved)
        printf 'INTRODUCED: %s was NOT a root entry at %s, so this promote made that path managed and collided with a link already sitting there -- decide what that link is before replacing it\n' \
          "$name" "${ref:0:12}" ;;
      *)
        printf 'EXPECTED: %s was NOT a root entry at %s, so this promote added it and no link exists there yet -- usually nothing but %s is needed, but --check cannot tell an EMPTY path from one holding a real file or directory, and for the latter the installer will BACK IT UP and link over it\n' \
          "$name" "${ref:0:12}" "$FARM_INSTALLER" ;;
    esac
  fi
}

main() {
  local scope="" before_sha="" ref=FETCH_HEAD before_head="" head_src=ORIG_HEAD
  local head_sha ref_sha base base_ok changed in_range helper out rc identity_note got
  local adopted hook_ref hook_dir hook_dest
  local farm_installer farm_out farm_rc farm_verdict farm_source scope_phys line
  local farm_after farm_before farm_undet farm_diff

  while [[ $# -gt 0 ]]; do
    case "$1" in
      --scope)
        [[ $# -ge 2 ]] || fatal 'the --scope flag needs a path'
        scope="$2"; shift 2 ;;
      --before-sha)
        [[ $# -ge 2 ]] || fatal 'the --before-sha flag needs a sha256'
        before_sha="$2"; shift 2 ;;
      --ref)
        [[ $# -ge 2 ]] || fatal 'the --ref flag needs a ref'
        ref="$2"; shift 2 ;;
      --before-head)
        [[ $# -ge 2 ]] || fatal 'the --before-head flag needs a commit'
        before_head="$2"; head_src=explicit; shift 2 ;;
      *) fatal "unknown argument: $1" ;;
    esac
  done

  [[ -n "$scope" ]] || fatal 'no --scope given — name the production repo the promote targeted'
  [[ -n "$before_sha" ]] \
    || fatal "no --before-sha given — capture the runtime $SETTINGS digest BEFORE the merge; it cannot be recovered afterwards"

  git -C "$scope" rev-parse --show-toplevel >/dev/null 2>&1 \
    || fatal "not a git repository: $scope"
  scope="$(git -C "$scope" rev-parse --show-toplevel)"

  [[ -f "$scope/$SETTINGS" ]] \
    || fatal "no $SETTINGS in $scope — the runtime file this check is about does not exist, so no verdict can be reached"

  helper="$script_dir/settings-hooks-check.py"

  phase=checking

  # Echo the resolved inputs back. A verdict about a configuration nobody confirmed is a true
  # answer to a question nobody asked — and the helper is resolved relative to THIS script, so
  # naming the copy is what keeps a stale-tool verdict from being anonymous.
  printf 'scope:       %s\n' "$scope"
  printf 'ref:         %s\n' "$ref"
  printf 'hooks check: %s\n' "$helper"

  # ---------- merge-applied ----------
  head_sha="$(git -C "$scope" rev-parse HEAD 2>/dev/null)" || head_sha=""
  ref_sha="$(git -C "$scope" rev-parse --verify -q "$ref^{commit}" 2>/dev/null)" || ref_sha=""
  if [[ -z "$ref_sha" ]]; then
    verdict_fail merge-applied "cannot resolve $ref in $scope — nothing confirms what was promoted"
  elif [[ "$head_sha" == "$ref_sha" ]]; then
    verdict_pass merge-applied
  else
    verdict_fail merge-applied \
      "HEAD is ${head_sha:0:12} but $ref is ${ref_sha:0:12} — the fast-forward did not land"
  fi

  # ---------- range ----------
  # Determines the branch. `unknown` is a real value here, not an error to paper over: it makes
  # settings-identical required below, which is the fail-closed direction.
  in_range=unknown
  if [[ -z "$before_head" ]]; then
    before_head="$(git -C "$scope" rev-parse --verify -q ORIG_HEAD 2>/dev/null)" || before_head=""
  fi
  base="$(git -C "$scope" rev-parse --verify -q "${before_head:-HEAD_UNSET}^{commit}" 2>/dev/null)" || base=""

  # `base_ok` records that this script BELIEVES the pre-merge HEAD, and is set in exactly one
  # place so that farm-current's classification below cannot disagree with the range row about
  # the same commit. Re-deriving the predicate at the other call site would copy a rule that then
  # drifts; one-directional, like `in_range`.
  base_ok=no

  if [[ -z "$base" ]]; then
    verdict_fail range \
      "no usable pre-merge HEAD ($head_src unset or unresolvable) — pass --before-head; treating the branch as UNDETERMINED, so byte-identity is required below"
  elif ! git -C "$scope" merge-base --is-ancestor "$base" "$head_sha" 2>/dev/null; then
    verdict_fail range \
      "the given pre-merge HEAD ${base:0:12} is not an ancestor of HEAD — refusing to believe it; treating the branch as UNDETERMINED"
  else
    base_ok=yes
    changed="$(git -C "$scope" diff --name-only "$base" "${ref_sha:-$head_sha}" 2>/dev/null)"
    # A HERESTRING with -F, never `printf … | grep -qx`, and both halves are load-bearing. This
    # file runs under `set -o pipefail`, where `grep -q` exiting at its first match SIGPIPEs the
    # producer and the pipeline reports 141 on exactly the inputs that DO match -- measured wrong
    # 200/200 at a 195 KB diff and correct 200/200 at 130 KB, so it hides from a small fixture. And
    # -F because the name carries a dot: as a BRE under -x, `settings.json` also matches the path
    # `settingsXjson`, and a false yes here SKIPS the byte-identity assertion below.
    if grep -qxF -- "$SETTINGS" <<<"$changed"; then
      in_range=yes
      verdict_pass range
      printf '  %s IS in the incoming range (%s..%s, from %s)\n' \
        "$SETTINGS" "${base:0:12}" "${ref_sha:0:12}" "$head_src"
    else
      in_range=no
      verdict_pass range
      printf '  %s is NOT in the incoming range (%s..%s, from %s)\n' \
        "$SETTINGS" "${base:0:12}" "${ref_sha:0:12}" "$head_src"
    fi
  fi

  # ---------- settings-identical ----------
  got="$(file_sha256 "$scope/$SETTINGS")"
  if [[ "$(lower "$got")" == "$(lower "$before_sha")" ]]; then
    identity_note="digest unchanged (${got:0:12})"
  else
    identity_note="digest changed: before ${before_sha:0:12}, after ${got:0:12}"
  fi

  if [[ "$in_range" == yes ]]; then
    # Not an assertion on this branch: the skill's hand-add step legitimately rewrites the file.
    # Reported anyway, because "identical" here is itself the signature of a SKIPPED hand-add —
    # which hooks-registered below is the instrument for.
    verdict_skip settings-identical \
      "not asserted — $SETTINGS was in the incoming range, so the hand-added registrations change it by design ($identity_note)"
  elif [[ "$(lower "$got")" == "$(lower "$before_sha")" ]]; then
    verdict_pass settings-identical
  else
    verdict_fail settings-identical \
      "the runtime $SETTINGS changed across a promote that did not touch it — $identity_note"
  fi

  # ---------- stash-empty ----------
  out="$(git -C "$scope" stash list 2>/dev/null)"
  if [[ -z "$out" ]]; then
    verdict_pass stash-empty
  else
    verdict_fail stash-empty \
      "$(printf '%s\n' "$out" | grep -c '') stash entr(y/ies) left parked — the promote did not restore what it stashed"
    printf '%s\n' "$out" | sed 's/^/  /'
  fi

  # ---------- skip-worktree ----------
  # `ls-files -v` prefixes a skip-worktree path with a lowercase-to-uppercase tag; `S` is the one
  # that matters. An empty result means the path is not tracked at all, which is also a failure.
  out="$(git -C "$scope" ls-files -v "$SETTINGS" 2>/dev/null)"
  case "$out" in
    S*) verdict_pass skip-worktree ;;
    '') verdict_fail skip-worktree "$SETTINGS is not tracked in $scope" ;;
    *)  verdict_fail skip-worktree \
          "skip-worktree is no longer set (ls-files -v says '${out%% *}') — the runtime file is now exposed to every checkout" ;;
  esac

  # ---------- hooks-registered ----------
  # Deliberately unconditional; see the header. Statuses are an allowlist: only `RESULT: PASS`
  # from the helper clears it, so ERROR (no verdict reached) never reads as clean.
  if [[ ! -f "$helper" ]]; then
    verdict_fail hooks-registered \
      "$helper is missing — the registration check could not run, which is not the same as passing"
  else
    out="$(python3 "$helper" --scope "$scope" --ref "${ref_sha:-$ref}" 2>&1)"; rc=$?
    case "$(printf '%s\n' "$out" | tail -1)" in
      'RESULT: PASS'*) verdict_pass hooks-registered ;;
      *)
        verdict_fail hooks-registered \
          "$(basename "$helper") did not return PASS (rc=$rc)"
        printf '%s\n' "$out" | sed 's/^/  /'
        ;;
    esac
  fi

  # ---------- pre-push-installed ----------
  # Same NAME and same adoption predicate as audit.sh's check_pre_push_installed, deliberately:
  # the two instruments assert the same invariant, so a divergent predicate would let them
  # disagree about the same repo and leave the operator arbitrating between two tools.
  #
  # Read-only, like everything else here. It reports what to run; it never runs it.
  adopted=no
  while IFS= read -r hook_ref; do
    [[ -z "$hook_ref" ]] && continue
    if git -C "$scope" cat-file -e "${hook_ref}:.publication.toml" 2>/dev/null; then
      adopted=yes
      break
    fi
  done < <(git -C "$scope" for-each-ref --format='%(refname)' refs/heads/ 2>/dev/null)

  hook_dir="$(cd "$scope" 2>/dev/null \
    && git rev-parse --path-format=absolute --git-path hooks 2>/dev/null)"
  hook_dest="${hook_dir:+$hook_dir/pre-push}"

  if [[ "$adopted" == no ]]; then
    verdict_skip pre-push-installed \
      'repo not adopted -- no refs/heads/* branch carries .publication.toml'
  elif ! git -C "$scope" rev-parse --quiet --verify refs/heads/main >/dev/null 2>&1; then
    # audit.sh:751-753 mirrors the boundary hook's own BLOCK case here rather than skipping past
    # it. Omitting this branch is not a smaller check -- it is the SAME check disagreeing with
    # audit.sh about the same repo, which is the one outcome sharing the name must not produce.
    verdict_fail pre-push-installed \
      'armed (a branch carries .publication.toml) but refs/heads/main does not resolve'
  elif [[ -z "$hook_dir" ]]; then
    # Without this, an unresolvable hooks dir degrades $hook_dest to the literal "/pre-push" and
    # the run FAILs while naming a path that was never the subject.
    verdict_fail pre-push-installed 'could not resolve the git hooks directory'
  elif [[ ! -f "$scope/$TRACKED_HOOK" ]]; then
    verdict_fail pre-push-installed \
      "adopted, but the tracked source $TRACKED_HOOK is missing from $scope"
  elif [[ ! -e "$hook_dest" ]]; then
    verdict_fail pre-push-installed \
      "not installed -- $hook_dest does not exist; run $INSTALLER --force-if-ours in $scope"
  elif [[ -L "$hook_dest" ]]; then
    # Ahead of the regular-file test, because `-f` FOLLOWS a symlink: a link to a real file
    # satisfies `-f` and the symlink cause would never be reported.
    verdict_fail pre-push-installed \
      "$hook_dest is a symlink -- must be a regular copy; remove it by hand first"
  elif [[ ! -f "$hook_dest" ]]; then
    # Ahead of the digest read, because file_sha256 on a FIFO BLOCKS FOREVER. A non-regular
    # destination must be refused rather than measured -- a hang leaves no verdict at all, which
    # is worse than any wrong one.
    verdict_fail pre-push-installed "$hook_dest is not a regular file"
  elif [[ ! -x "$hook_dest" ]]; then
    verdict_fail pre-push-installed \
      "$hook_dest is not executable -- git silently ignores such a hook; chmod +x it"
  elif [[ "$(file_sha256 "$hook_dest")" == "$(file_sha256 "$scope/$TRACKED_HOOK")" ]]; then
    verdict_pass pre-push-installed
  elif hook_is_a_tracked_version "$scope" "$hook_dest"; then
    # STALE: a prior tracked version, so the installer's own license covers it and naming the
    # remedy is correct.
    verdict_fail pre-push-installed \
      "$hook_dest is a PRIOR tracked version of $TRACKED_HOOK -- run $INSTALLER --force-if-ours in $scope"
  else
    # FOREIGN: deliberately does NOT name the installer. The remedy is wrong here -- what is
    # installed was never a version of this hook, and deciding what it is comes first.
    verdict_fail pre-push-installed \
      "$hook_dest matches NO tracked version of $TRACKED_HOOK -- it was not installed from this repo; inspect it before replacing it"
  fi

  # ---------- farm-current ----------
  # The live config farm is the set of symlinks from $HOME/.claude into the production repo. A
  # fast-forward cannot create or repoint one: only install.sh does. Ordinary file content
  # reaches the farm through the EXISTING links, so the gap only opens when ROOT MEMBERSHIP
  # changes -- a newly tracked root entry is absent from the live config until something runs
  # the installer. This row is the re-sample that fires even when that action never ran: a
  # forgotten call, a mis-taken guard, or the one-time landing race where the pre-merge skill
  # body did not yet carry it.
  #
  # ONE-DIRECTIONAL, and the row NAME is shorthand for the claim rather than the claim itself:
  # `--check` iterates the CURRENT derived membership only, so what is asserted here is that
  # EVERY CURRENT MEMBER LINKS CORRECTLY -- not that the farm as a whole is current. A member
  # the promote REMOVED leaves a stale dangling link that neither the install nor the check ever
  # looks at, and this row reads clean over it (measured).
  #
  # PRODUCTION'S OWN COPY, never this working copy: `--check` compares every managed link
  # against its own SCRIPT_DIR and never consults the install marker, so the dev clone's copy
  # reports a DRIFT line per member and RESULT: FAIL against a perfectly healthy farm (measured
  # at one instant against the live farm: dev rc=1 with 16 DRIFT lines, production rc=0 with
  # none). That is the same which-copy-produced-the-verdict hazard the hooks helper path is
  # printed for above -- and here the tool NAMES the tree it graded in `source=`, so the row
  # echoes that back and asserts it, rather than trusting the path it handed over. Resolve both
  # sides physically: the installer's SCRIPT_DIR is `pwd -P`, so a logically-spelled scope could
  # never compare equal and the assertion would fire on every healthy promote.
  #
  # On the DRIFT path each reported line is additionally CLASSIFIED -- pre-existing, introduced,
  # expected, regressed, or undetermined -- so the operator can tell in one read whether this
  # promote caused the condition or merely tripped over one that predates it. See
  # farm_drift_label above for the discriminator and why the obvious one cannot work. It is
  # diagnosis attached to the failure, not a second gate: the verdict is the same either way.
  #
  # Read-only, like everything else here: `--check` mutates nothing under the config root.
  farm_installer="$scope/$FARM_INSTALLER"
  if [[ ! -f "$farm_installer" ]]; then
    verdict_skip farm-current \
      "$scope ships no $FARM_INSTALLER -- there is no config farm for this repo to be current about"
  else
    farm_out="$("$farm_installer" --check 2>&1)"; farm_rc=$?

    # Keep only the LAST `RESULT:` line and ignore every other line. The installer emits WARN:
    # lines about untracked and config-shaped root entries even under --check, because those run
    # ahead of flag parsing; they do not reach its verdict (measured: WARNs alongside
    # RESULT: PASS, rc=0), so they are not findings. A while-read rather than `grep | tail`:
    # this file runs under `set -o pipefail`, where an early-exiting reader turns its producer's
    # SIGPIPE into the pipeline's status.
    farm_verdict=""
    while IFS= read -r line; do
      case "$line" in
        'RESULT: '*) farm_verdict="$line" ;;
      esac
    done <<<"$farm_out"

    case "$farm_verdict" in
      *source=*)
        farm_source="${farm_verdict#*source=}"
        farm_source="${farm_source%%[) ]*}"
        ;;
      *) farm_source="" ;;
    esac
    scope_phys="$(cd -P "$scope" 2>/dev/null && pwd -P)" || scope_phys=""

    if [[ -z "$farm_verdict" ]]; then
      # REFUSED BEFORE CHECKING -- not drift. install.sh requires realpath, probes `mv -T` and
      # resolves $HOME at top level, all ahead of flag parsing, so a pure report can die with
      # ZERO `RESULT:` lines (measured: `refuse: mv -T not supported by this mv` with coreutils
      # off PATH, `HOME: unbound variable` with HOME unset). An absent verdict line means the
      # tool never reported on the farm at all; calling that drift would diagnose a toolchain
      # problem as a broken promote, and on such a machine every adopted promote would FAIL
      # forever for the wrong reason.
      verdict_fail farm-current \
        "$FARM_INSTALLER --check reached no verdict (rc=$farm_rc, no RESULT: line) -- it refused BEFORE checking the farm, so this is NOT a drift report; fix what it refuses over, then re-run $scope/$FARM_INSTALLER --check"
      printf '%s\n' "$farm_out" | sed 's/^/  /'
    elif [[ -z "$farm_source" ]]; then
      verdict_fail farm-current \
        "$FARM_INSTALLER --check named no source= in its verdict -- nothing confirms which tree it graded"
      printf '%s\n' "$farm_out" | sed 's/^/  /'
    elif [[ -z "$scope_phys" ]]; then
      verdict_fail farm-current "could not physically resolve $scope to compare against source=$farm_source"
    elif [[ "$farm_source" != "$scope_phys" ]]; then
      # A wrong-copy invocation, whatever its verdict: the run graded a tree that is not the one
      # this promote landed in (a symlinked or copied installer does exactly this). Its PASS and
      # its FAIL are equally about the wrong subject. Any DRIFT lines here are attached RAW and
      # deliberately unclassified: the classification below reads membership in the PROMOTED
      # repo, which is not the repo these lines are about.
      verdict_fail farm-current \
        "$FARM_INSTALLER --check graded $farm_source, not the promoted tree $scope_phys -- the verdict is about a different repo"
      printf '%s\n' "$farm_out" | sed 's/^/  /'
    elif [[ "$farm_verdict" == 'RESULT: PASS'* ]]; then
      # Allowlist, as with hooks-registered: anything not anticipated here reads as not-clean.
      verdict_pass farm-current
      printf '  every CURRENT farm member links to %s (a member REMOVED by this promote is outside what --check iterates)\n' \
        "$farm_source"
    else
      # DRIFT. The verdict is decided HERE and is final; everything below only annotates the
      # attached output, so no classification -- UNDETERMINED included -- can move a FAIL to a
      # PASS or the reverse.
      verdict_fail farm-current \
        "$FARM_INSTALLER --check found the farm out of date (rc=$farm_rc) -- run $FARM_INSTALLER in $scope"

      # Membership at the before-commit, read ONCE for every DRIFT line. Lazily, in this branch
      # only: a clean farm has nothing to classify and must not pay for a git read.
      farm_after="${ref_sha:-$head_sha}"
      farm_before=""
      farm_undet=""
      if [[ "$base_ok" != yes ]]; then
        farm_undet="the pre-merge HEAD is unusable (see the range row above), so membership at the before-commit cannot be read at all"
      elif ! farm_before="$(git -C "$scope" ls-tree --name-only "$base" 2>/dev/null)"; then
        # Reachable: a commit object can be present and readable (so it resolves, and answers
        # merge-base) while its TREE is not -- a partial clone with no network, or a damaged
        # object store. An empty listing with rc 0 is a different thing and is believed.
        farm_undet="git ls-tree could not list the root of ${base:0:12}, so no name's membership before is derivable"
        farm_before=""
      elif ! farm_diff="$(git -C "$scope" diff --name-only "$base" "$farm_after" -- "$FARM_INSTALLER" 2>/dev/null)"; then
        farm_undet="whether $FARM_INSTALLER changed over ${base:0:12}..${farm_after:0:12} could not be determined, so its EXCLUDE list may have moved under the before-tree reading"
      elif [[ -n "$farm_diff" ]]; then
        # The one case the two-commit discriminator genuinely cannot cover: EXCLUDE lives in
        # install.sh, so if install.sh moved in the range, a name's presence in the before-tree
        # no longer decides whether it was a MEMBER then.
        farm_undet="$FARM_INSTALLER itself changed over ${base:0:12}..${farm_after:0:12}, so its EXCLUDE list may have moved and presence in the before-tree no longer settles membership"
      fi

      # A while-read, not `grep | sed`, for the same pipefail reason the verdict scan above gives.
      while IFS= read -r line; do
        printf '  %s\n' "$line"
        case "$line" in
          'DRIFT: '*)
            printf '    %s\n' \
              "$(farm_drift_label "$line" "$farm_before" "$farm_undet" "$base")"
            ;;
        esac
      done <<<"$farm_out"
    fi
  fi

  if [[ "$fail_count" -eq 0 ]]; then
    result_line PASS 0
    return 0
  fi
  result_line FAIL 1
  return 1
}

# Guarded (not a bare `main "$@"`) so the test suite can source this file to unit-test helpers
# without running a sweep. The traps live INSIDE the guard for the same reason: a top-level EXIT
# trap would install itself into any shell that sources this file.
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  # Single quotes: `$?` must expand when the trap RUNS, not when it is defined.
  trap 'on_exit "$?"' EXIT
  trap 'exit 143' TERM
  trap 'exit 130' INT
  trap 'exit 129' HUP
  main "$@"
fi

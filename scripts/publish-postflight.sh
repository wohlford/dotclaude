#!/usr/bin/env bash
set -uo pipefail

# Script: publish-postflight.sh
# Purpose: Verify /propagate's adopted-publish END invariant (read-only) after the push and the watermark advance
# Usage: publish-postflight.sh [--scope <path>]
#
# Scope: this script verifies; it changes nothing. It is the sibling of publish-preflight.sh, which
# gates the START of the publish. Everything after the only irreversible step (step 6's push and
# step 7's watermark advance) used to be hand-run prose — fetch, main == origin/main, a leak check on
# the remote's refs, a tag comparison, watermark integrity — and was re-composed by hand repeatedly
# (once comparing tag COUNTS, once reading a cached origin/main, once dropping the leak check).
#
# It makes no repo writes and no remote writes: no `git fetch`, no `update-ref`. Its single network
# call is `git ls-remote origin`. Its only local write is a stderr scratch file under $TMPDIR,
# removed in the exit trap. The TRANSPORT may have side effects outside this script's control: ssh
# can record host keys in known_hosts (that is what the `Warning: Permanently added ...` stderr line
# reports), and over https a credential helper may store credentials. Do not add a fetch.
#
# Exit codes:
#   0 — every assertion held; the publish ended in the expected state
#   1 — at least one assertion FAILED
#   2 — usage error (bad/missing --scope, unknown flag, not a git repo, not an adopted repo)
#   129/130/143 — died on a trapped signal (HUP/INT/TERM)
#
# Terminal verdict line: every run this script exits from itself prints, as its LAST line of
# stdout, `RESULT: <STATUS> rc=<n> checks=<pass>/<fail>/<skip>` where STATUS is PASS | FAIL |
# ERROR (no checks ran) | INCOMPLETE (died partway). Clean is EXACTLY `RESULT: PASS` — an
# allowlist, so any value not anticipated here reads as not-clean. The line CANNOT be emitted on
# SIGKILL, so its ABSENCE never means clean: it means the run did not complete. PASS/FAIL are
# emitted positionally by main(), never decided inside the exit trap, because `$?` in an EXIT
# trap reads 0 for an untrapped fatal signal. Per-check lines are `PASS <name>`,
# `FAIL <name> — <reason>` and `SKIP <name> — <reason>`; a SKIP never passes and never blocks.
#
# WHY ls-remote, NOT fetch: a fetch updates the tracking ref and the comparison then reads that
# cache — the dead-fetch defect's habitat (a failed fetch followed by a read of a stale origin/main
# printed PASS about a remote nobody observed). One `ls-remote` with no pattern is a single live,
# consistent snapshot of EVERY ref origin holds, so the leak check, the main comparison and the tag
# comparison all grade ONE observation, and nothing cached can be stale. Its rc is the abort point:
# non-zero FAILs remote-read and every remote-dependent check SKIPs. stdout and stderr are captured
# SEPARATELY and only stdout is parsed: a `Warning: Permanently added ...` stderr line has no TAB
# and would otherwise read as an unexpected ref and defeat the empty-output floor.
#
# Checks, in order (the observed origin url and origin's main sha are echoed on an `observing:`
# line before them, so the parameters the run used are visible):
#   origin-url         fetch url == the ONE push url (`get-url --push --all`; ls-remote reads the FETCH url; a differing or extra push url
#                      means this would grade a remote other than the one published to)
#   remote-read        ABORT POINT: ls-remote rc != 0, or rc 0 with EMPTY output (a publish always
#                      leaves refs/heads/main) -> FAIL; remote-dependent checks SKIP
#   remote-refs        ALLOWLIST on the TAB-split ref field: HEAD (only while its sha equals origin's
#                      main; with no origin main, any HEAD FAILs), refs/heads/main (EXACT, not a
#                      glob), refs/tags/* and their ^{} peels; any other ref FAILs, each named
#   main-sync          origin's refs/heads/main == local refs/heads/main (always fully spelled: a
#                      tag named `main` would otherwise win); empty origin sha or no local main FAILs
#   tags-parity        SET equality of `name objectname` lines, origin refs/tags/* (peels dropped)
#                      vs local tags merged into main; both sorted under LC_ALL=C; both empty FAILs
#   watermark-present  refs/published/main resolves
#   watermark-tree     main's tree == the watermark's, CHANGELOG.md excluded; non-zero fails closed
#   watermark-ancestor the watermark is an ancestor of dev
#   watermark-current  rev-list --count <watermark>..dev == 0
# The watermark checks need only the local repo and run even when remote-read failed.
#
# Not in scope (named, so absence is not read as coverage): tag/commit signatures; CHANGELOG
# contents; production-clone tag parity (expected to lag a publish); commit-vs-tag parity past the
# pre-publish tip (a pre-push concern, driver-side); the tip suite. An origin legitimately carrying
# refs/pull/* FAILs remote-refs by design (named; the operator decides). `ls-remote` shows only
# ADVERTISED refs, so an origin that hides a ref (`uploadpack.hideRefs`) cannot be seen by this
# script (measured: a hidden leaked `refs/heads/dev` read as PASS remote-refs). The single
# `ls-remote` child has no timeout bound, and bash defers the TERM trap until that child returns.
#
# DECISION-GATE (not cheaply probeable): the real origin could not be probed at design time (cold
# card: `agent refused operation`). Two facts about it are therefore UNVERIFIED: its ref namespaces
# are exactly HEAD / heads/main / tags, and its tag set equals the dev clone's tags merged into
# main. Until a postflight has PASSED against the real origin, a FAIL is either a true defect or a
# wrong allowlist, and the operator reads the named refs to tell which. Do not widen the allowlist to make the
# run green without reading what it named.
#
# Failure messages are diagnosis only: no recovery is prescribed.
#
# bash-3.2/BSD-safe: no arrays, no mapfile, no GNU-only flags.
#
# NOTE: no `-e` — one failing assertion must not abort the run before its verdict line.

script_name="$(basename "$0")"

readonly PUBLISHED_REF=refs/heads/main
readonly WORKING_REF=refs/heads/dev
readonly WATERMARK_REF=refs/published/main

pass_count=0
fail_count=0
skip_count=0

# All three MUST stay global — a `local` copy would be invisible to the exit trap. `phase` goes
# init -> checking and only tells a usage error (nothing ran) from a death partway through.
# `reported` records that a verdict line was already printed, so the trap stays silent on the
# normal path. `errfile` is the scratch file the exit trap removes.
phase=init
reported=no
errfile=""

usage() { printf 'Usage: %s [--scope <path>]\n' "$script_name" >&2; }

# A fatal precondition prints its reason to STDOUT, not just stderr: stdout is the artifact an
# operator actually reads back.
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
  if [[ -n "$errfile" ]]; then rm -f "$errfile"; fi
  [[ "$reported" == yes ]] && return
  if [[ "$phase" == init && "$1" -eq 2 ]]; then
    result_line ERROR "$1"
  else
    result_line INCOMPLETE "$1"
  fi
}

# can_prompt -> 0 when this process has a terminal a pinentry could reach. With none attached a
# card-backed key does not hang — it REFUSES ("agent refused operation"), textually identical to a
# rejected credential. A precondition on the environment, so it needs nothing from ssh or the agent.
can_prompt() { [[ -t 0 || -t 1 || -t 2 ]]; }

skip_remote_dependent() { # reason
  verdict_skip remote-refs "$1"
  verdict_skip main-sync "$1"
  verdict_skip tags-parity "$1"
}

main() {
  local scope="" url push_url out err rc promptable
  local head_detail line sha ref origin_main="" unexpected="" tags_origin="" remote_ok=no
  local local_main local_tags set_o set_l only_o only_l detail
  local wm count tool_bad head_sha=""

  while [[ $# -gt 0 ]]; do
    case "$1" in
      --scope)
        [[ $# -ge 2 ]] || fatal 'the --scope flag needs a path'
        scope="$2"; shift 2 ;;
      *) fatal "unknown argument: $1" ;;
    esac
  done

  if [[ -z "$scope" ]]; then
    scope="$(git rev-parse --show-toplevel 2>/dev/null)" \
      || fatal 'no --scope given and the working directory is not a git repository'
  fi
  git -C "$scope" rev-parse --show-toplevel >/dev/null 2>&1 \
    || fatal "not a git repository: $scope"
  scope="$(git -C "$scope" rev-parse --show-toplevel)"

  [[ -f "$scope/.publication.toml" ]] \
    || fatal "no .publication.toml at $scope — not an adopted repo, so there is no publish path"

  phase=checking

  if can_prompt; then promptable=yes; else promptable=no; fi

  # ---------- the one network call ----------
  # stdout is parsed; stderr goes to a scratch file, read only for the failure message. rc is
  # captured on the very next line, before anything can clobber it.
  errfile="$(mktemp "${TMPDIR:-/tmp}/postflight-err.XXXXXX")" || errfile=""
  out="$(git -C "$scope" ls-remote origin 2>"${errfile:-/dev/null}")"; rc=$?
  err=""
  if [[ -n "$errfile" ]]; then err="$(tr '\n' ' ' <"$errfile")"; fi

  if [[ "$rc" -eq 0 && -n "$out" ]]; then
    remote_ok=yes
    while IFS= read -r line; do
      [[ -n "$line" ]] || continue
      sha="${line%%$'\t'*}"
      ref="${line#*$'\t'}"
      case "$ref" in
        HEAD) head_sha="$sha" ;;
        "$PUBLISHED_REF") origin_main="$sha" ;;
        refs/tags/*'^{}') ;;
        refs/tags/*) tags_origin="${tags_origin:+$tags_origin$'\n'}${ref#refs/tags/} $sha" ;;
        *) unexpected="${unexpected:+$unexpected$'\n'}$ref" ;;
      esac
    done <<<"$out"
  fi

  url="$(git -C "$scope" remote get-url origin 2>/dev/null)" || url=""
  push_url="$(git -C "$scope" remote get-url --push --all origin 2>/dev/null)" || push_url=""
  printf 'observing: origin=%s main=%s\n' "${url:-none}" "${origin_main:-none}"

  # ---------- origin-url ----------
  if [[ -z "$url" ]]; then
    verdict_fail origin-url 'no origin remote configured'
  elif [[ "$url" == "$push_url" ]]; then
    # push_url holds EVERY push url, one per line: equality with the single-line fetch url means
    # there is exactly one push url and it is the fetch url.
    verdict_pass origin-url
  else
    verdict_fail origin-url \
      "origin's fetch url ($url) is not its one and only push url (push urls: $(tr '\n' ' ' <<<"${push_url:-none}")) — ls-remote read the fetch url, so this run may grade a remote other than the one published to"
  fi

  # ---------- remote-read: THE ABORT POINT ----------
  if [[ "$rc" -ne 0 ]]; then
    verdict_fail remote-read "git ls-remote origin exited $rc: $err"
    if [[ "$promptable" == no ]]; then
      printf '  NOTE: no terminal is attached, so a card PIN prompt could not be delivered here.\n'
      printf '  Before treating this as a credential problem, re-run from an interactive shell.\n'
    fi
    skip_remote_dependent 'not run — the live read of origin failed, so nothing here was observed'
  elif [[ -z "$out" ]]; then
    verdict_fail remote-read \
      'git ls-remote origin exited 0 but printed nothing — origin is empty, yet a publish always leaves refs/heads/main'
    skip_remote_dependent 'not run — origin returned an empty listing, so nothing here was observed'
  else
    verdict_pass remote-read
  fi

  if [[ "$remote_ok" == yes ]]; then
    # ---------- remote-refs ----------
    # HEAD is allowed only while it agrees with refs/heads/main: a HEAD detached at another commit
    # (dev) makes that commit reachable from the remote's default entry point with no dev ref.
    head_detail=""
    if [[ -n "$head_sha" && "$head_sha" != "$origin_main" ]]; then
      unexpected="${unexpected:+$unexpected$'\n'}HEAD"
      if [[ -z "$origin_main" ]]; then
        head_detail="origin's HEAD is $head_sha but origin has no $PUBLISHED_REF"
      else
        head_detail="origin's HEAD is $head_sha but its $PUBLISHED_REF is $origin_main"
      fi
    fi
    if [[ -z "$unexpected" ]]; then
      verdict_pass remote-refs
    else
      verdict_fail remote-refs \
        'origin holds refs outside the allowlist (HEAD, refs/heads/main, refs/tags/*)'
      while IFS= read -r line; do
        printf '  unexpected ref: %s\n' "$line"
      done <<<"$unexpected"
      if [[ -n "$head_detail" ]]; then printf '  HEAD: %s\n' "$head_detail"; fi
    fi

    # ---------- main-sync ----------
    local_main="$(git -C "$scope" rev-parse --verify -q "$PUBLISHED_REF" 2>/dev/null)" || local_main=""
    if [[ -z "$local_main" ]]; then
      verdict_fail main-sync "no local $PUBLISHED_REF to compare with origin's"
    elif [[ -z "$origin_main" ]]; then
      verdict_fail main-sync "origin has no $PUBLISHED_REF (local is $local_main)"
    elif [[ "$local_main" == "$origin_main" ]]; then
      verdict_pass main-sync
    else
      verdict_fail main-sync "local $PUBLISHED_REF is $local_main but origin's is $origin_main"
    fi

    # ---------- tags-parity ----------
    if [[ -z "$local_main" ]]; then
      verdict_skip tags-parity "not run — no local $PUBLISHED_REF to take merged tags from"
    else
      local_tags="$(git -C "$scope" for-each-ref --merged "$PUBLISHED_REF" refs/tags \
        --format='%(refname:strip=2) %(objectname)' 2>/dev/null)"; rc=$?
      if [[ "$rc" -ne 0 ]]; then
        verdict_fail tags-parity "could not list local tags merged into $PUBLISHED_REF (rc=$rc)"
      elif [[ -z "$tags_origin" && -z "$local_tags" ]]; then
        verdict_fail tags-parity \
          'no tags on origin or locally — two empty sets compare equal, and nothing was published'
      else
        # Every substitution's rc is captured on the very next line: a failing sort or comm prints
        # nothing, and two empty sets compare EQUAL — a false PASS about a tool that never ran.
        set_o=""; set_l=""; tool_bad=""
        if [[ -n "$tags_origin" ]]; then
          set_o="$(LC_ALL=C sort <<<"$tags_origin")"; rc=$?
          if [[ "$rc" -ne 0 ]]; then tool_bad="sort of origin's tags exited $rc"
          elif [[ -z "$set_o" ]]; then tool_bad="sort of origin's tags printed nothing for a non-empty input"; fi
        fi
        if [[ -z "$tool_bad" && -n "$local_tags" ]]; then
          set_l="$(LC_ALL=C sort <<<"$local_tags")"; rc=$?
          if [[ "$rc" -ne 0 ]]; then tool_bad="sort of the local tags exited $rc"
          elif [[ -z "$set_l" ]]; then tool_bad="sort of the local tags printed nothing for a non-empty input"; fi
        fi
        only_o=""; only_l=""
        if [[ -z "$tool_bad" ]]; then
          only_o="$(LC_ALL=C comm -23 <(if [[ -n "$set_o" ]]; then printf '%s\n' "$set_o"; fi) \
                                       <(if [[ -n "$set_l" ]]; then printf '%s\n' "$set_l"; fi))"; rc=$?
          if [[ "$rc" -ne 0 ]]; then tool_bad="comm -23 exited $rc"; fi
        fi
        if [[ -z "$tool_bad" ]]; then
          only_l="$(LC_ALL=C comm -13 <(if [[ -n "$set_o" ]]; then printf '%s\n' "$set_o"; fi) \
                                       <(if [[ -n "$set_l" ]]; then printf '%s\n' "$set_l"; fi))"; rc=$?
          if [[ "$rc" -ne 0 ]]; then tool_bad="comm -13 exited $rc"; fi
        fi
        if [[ -n "$tool_bad" ]]; then
          verdict_fail tags-parity "could not compare the tag sets — $tool_bad"
        elif [[ -z "$only_o" && -z "$only_l" ]]; then
          verdict_pass tags-parity
        else
          verdict_fail tags-parity \
            "origin's tag set differs from the local tags merged into $PUBLISHED_REF"
          if [[ -n "$only_o" ]]; then
            while IFS= read -r detail; do printf '  only on origin: %s\n' "$detail"; done <<<"$only_o"
          fi
          if [[ -n "$only_l" ]]; then
            while IFS= read -r detail; do printf '  only local: %s\n' "$detail"; done <<<"$only_l"
          fi
        fi
      fi
    fi
  fi

  # ---------- watermark checks (local only; run even when remote-read failed) ----------
  wm="$(git -C "$scope" rev-parse --verify -q "$WATERMARK_REF" 2>/dev/null)" || wm=""
  if [[ -z "$wm" ]]; then
    verdict_fail watermark-present "no $WATERMARK_REF recorded"
    verdict_skip watermark-tree 'not run — no watermark to compare against'
    verdict_skip watermark-ancestor 'not run — no watermark to compare against'
    verdict_skip watermark-current 'not run — no watermark to compare against'
  else
    verdict_pass watermark-present

    # CHANGELOG.md is the one and only excluded path: main carries a per-brick entry dev never
    # gets. Widening the exclusion stops proving anything. Any non-zero fails closed.
    git -C "$scope" diff --quiet --ignore-submodules=none --no-ext-diff --no-textconv \
      "$wm" "$PUBLISHED_REF" -- . ':(exclude)CHANGELOG.md' 2>/dev/null
    rc=$?
    if [[ "$rc" -eq 0 ]]; then
      verdict_pass watermark-tree
    elif [[ "$rc" -eq 1 ]]; then
      verdict_fail watermark-tree "$PUBLISHED_REF's tree differs from the watermark's (CHANGELOG.md excluded)"
    else
      verdict_fail watermark-tree "could not compare $PUBLISHED_REF with the watermark (git diff rc=$rc)"
    fi

    if git -C "$scope" merge-base --is-ancestor "$wm" "$WORKING_REF" 2>/dev/null; then
      verdict_pass watermark-ancestor
    else
      verdict_fail watermark-ancestor \
        "the watermark is not an ancestor of $WORKING_REF (or $WORKING_REF is unreadable)"
    fi

    # rc on the very next line; an rc != 0 or EMPTY count must FAIL — `[[ "" -eq 0 ]]` is true.
    count="$(git -C "$scope" rev-list --count "$wm..$WORKING_REF" 2>/dev/null)"; rc=$?
    case "$count" in
      ''|*[!0-9]*) verdict_fail watermark-current "could not count $WATERMARK_REF..$WORKING_REF (rc=$rc, output [$count])" ;;
      *)
        if [[ "$rc" -ne 0 ]]; then
          verdict_fail watermark-current "counting $WATERMARK_REF..$WORKING_REF exited $rc"
        elif [[ "$count" -eq 0 ]]; then
          verdict_pass watermark-current
        else
          verdict_fail watermark-current \
            "$count commit(s) on $WORKING_REF after the watermark — either the watermark advance has not run or $WORKING_REF moved since"
        fi
        ;;
    esac
  fi

  if [[ "$fail_count" -eq 0 ]]; then
    result_line PASS 0
    return 0
  fi
  result_line FAIL 1
  return 1
}

# Guarded (not a bare `main "$@"`) so a test can source this file without running a sweep. The
# traps live INSIDE the guard for the same reason: a top-level EXIT trap would install itself
# into any shell that sources this file.
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  # Single quotes: `$?` must expand when the trap RUNS, not when it is defined.
  trap 'on_exit "$?"' EXIT
  trap 'exit 143' TERM
  trap 'exit 130' INT
  trap 'exit 129' HUP
  main "$@"
fi

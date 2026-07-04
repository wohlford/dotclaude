#!/usr/bin/env bash
set -euo pipefail

# Script: idempotency-test.sh
# Purpose: Verify a script is idempotent — run it twice in a sandbox, diff the state
# Usage: idempotency-test [opts] -- <target> [target-args...]
#
# Exit codes:
#   0  idempotent (2nd run produced no new state)
#   1  NOT idempotent (state or exit-code diverged; diff on stderr)
#   2  harness or target error (bad usage, setup failed, run-1 failed, etc.)
#
# Containment is env-redirection of HOME/XDG/TMPDIR (+ GIT_CONFIG_NOSYSTEM), NOT a hard
# sandbox: absolute-path effects can still take effect. The target runs arbitrary code.

# ---------- shared state: written by main(), read by the helpers below / the EXIT trap ----------
# These are global on purpose. cleanup() reads sb+keep via the EXIT trap, which cannot see a
# function-local if main() returns normally (verified). manifest()/ignored() read ig; run()
# reads run_env/cmd_argv/targs/stdin_src — all inside forked subshells. Keeping them global
# avoids betting on dynamic scope surviving a subshell.
sb=""
keep=0
ig=()
run_env=()
cmd_argv=()
targs=()
stdin_src="/dev/null"

usage() { sed -n '4,12p' "$0" >&2; }

# shellcheck disable=SC2329  # cleanup is invoked indirectly via trap
cleanup() { [[ "$keep" == 1 ]] && { printf 'sandbox kept: %s\n' "$sb" >&2; return; }; rm -rf "$sb"; }

ignored() { local p="$1" g; for g in "${ig[@]}"; do
  # shellcheck disable=SC2053
  [[ "$p" == $g || "$p" == */$g || "$p" == $g/* || "$p" == */$g/* ]] && return 0; done; return 1; }

# ---------- manifest (content-only; NEVER dereference symlinks) ----------
manifest() {
  local root p rel x gd wt
  for root in work home xdg; do
    [[ -d "$sb/$root" ]] || continue
    ( cd "$sb/$root" && find . -mindepth 1 -name .git -prune -o \( -type f -o -type l -o -type d \) -print \
        | LC_ALL=C sort | while IFS= read -r p; do
        rel="${p#./}"; [[ -n "$rel" ]] || continue
        case "$rel" in .git|.git/*|*/.git|*/.git/*) continue ;; esac
        ignored "$rel" && continue
        if [[ -L "$p" ]]; then printf 'S|%s|%s|%s\n' "$root" "$rel" "$(readlink "$p")"
        elif [[ -d "$p" ]]; then printf 'D|%s|%s\n' "$root" "$rel"
        elif [[ -f "$p" ]]; then x=-; [[ -x "$p" ]] && x=x
          printf 'F|%s|%s|%s|%s\n' "$root" "$rel" "$x" "$(shasum -a 256 "$p" | awk '{print $1}')"
        fi
      done ) || true
    # synthesize git porcelain for any .git under this root (real state, no plumbing churn)
    while IFS= read -r gd; do
      wt="$(dirname "$gd")"
      printf 'G|%s|status|%s\n' "$root" "$(git -C "$wt" status --porcelain=v1 2>/dev/null | LC_ALL=C sort | tr '\n' ';')"
      printf 'G|%s|head|%s\n'   "$root" "$(git -C "$wt" rev-parse HEAD 2>/dev/null || true)"
      printf 'G|%s|refs|%s\n'   "$root" "$(git -C "$wt" for-each-ref 2>/dev/null | LC_ALL=C sort | tr '\n' ';')"
    done < <(find "$sb/$root" -type d -name .git 2>/dev/null | LC_ALL=C sort)
  done
}

# ---------- run the target (errexit-safe) ----------
run() {
  local rargs=() a rc
  for a in ${targs[@]+"${targs[@]}"}; do rargs+=("${a//\{\{SANDBOX\}\}/$sb}"); done
  set +e
  ( cd "$sb/work" && env "${run_env[@]}" "${cmd_argv[@]}" ${rargs[@]+"${rargs[@]}"} ) <"$stdin_src" >/dev/null 2>&1
  rc=$?
  set -e
  return "$rc"
}

main() {
  local seed="" stdin_file="" runner="" do_git=0 allow_nonzero=0
  local setups=() envs=() ignores=()

  while [[ $# -gt 0 ]]; do
    case "$1" in
      --seed)          seed="${2:?}"; shift 2 ;;
      --setup)         setups+=("${2:?}"); shift 2 ;;
      --stdin)         stdin_file="${2:?}"; shift 2 ;;
      --env)           envs+=("${2:?}"); shift 2 ;;
      --git)           do_git=1; shift ;;
      --ignore)        ignores+=("${2:?}"); shift 2 ;;
      --runner)        runner="${2:?}"; shift 2 ;;
      --allow-nonzero) allow_nonzero=1; shift ;;
      --keep)          keep=1; shift ;;
      --)              shift; break ;;
      -h|--help)       usage; exit 0 ;;
      *)               printf 'unknown option: %s\n' "$1" >&2; exit 2 ;;
    esac
  done

  [[ $# -ge 1 ]] || { printf 'no target after --\n' >&2; exit 2; }
  local target="$1"; shift
  targs=("$@")

  [[ -e "$target" ]] || { printf 'target not found: %s\n' "$target" >&2; exit 2; }
  [[ -z "$seed" || -d "$seed" ]] || { printf 'seed dir not found: %s\n' "$seed" >&2; exit 2; }
  local target_abs
  target_abs="$(cd "$(dirname "$target")" && pwd)/$(basename "$target")"

  # ---------- interpreter ----------
  if [[ -n "$runner" ]]; then
    read -r -a cmd_argv <<<"$runner"; cmd_argv+=("$target_abs")
  else
    case "$target_abs" in
      *.sh) cmd_argv=(bash "$target_abs") ;;
      *.py) cmd_argv=(python3 "$target_abs") ;;
      *.js) cmd_argv=(node "$target_abs") ;;
      *) if [[ -x "$target_abs" ]]; then cmd_argv=("$target_abs")
         else printf 'cannot determine runner for %s (use --runner)\n' "$target" >&2; exit 2; fi ;;
    esac
  fi

  # ---------- sandbox ----------
  sb="$(mktemp -d)"
  trap cleanup EXIT
  mkdir -p "$sb/work" "$sb/home" "$sb/tmp" "$sb/xdg/config" "$sb/xdg/cache" "$sb/xdg/data"

  [[ -n "$seed" ]] && cp -a "$seed/." "$sb/work/"

  local base_env=(
    "HOME=$sb/home" "TMPDIR=$sb/tmp"
    "XDG_CONFIG_HOME=$sb/xdg/config" "XDG_CACHE_HOME=$sb/xdg/cache" "XDG_DATA_HOME=$sb/xdg/data"
    "TZ=UTC" "LC_ALL=C" "PYTHONDONTWRITEBYTECODE=1" "PYTHONHASHSEED=0"
    "GIT_CONFIG_NOSYSTEM=1" "GIT_CONFIG_GLOBAL=/dev/null" "IDEMPOTENCY_SANDBOX=$sb"
  )
  run_env=("${base_env[@]}")
  local e
  for e in ${envs[@]+"${envs[@]}"}; do run_env+=("${e//\{\{SANDBOX\}\}/$sb}"); done

  # ---------- setup (once, before run 1) ----------
  local s
  for s in ${setups[@]+"${setups[@]}"}; do
    ( cd "$sb/work" && env "${base_env[@]}" bash -c "$s" ) \
      || { printf 'setup failed (exit %s): %s\n' "$?" "$s" >&2; exit 2; }
  done

  # ---------- optional git seed ----------
  if [[ "$do_git" == 1 ]]; then
    local gitenv=("${base_env[@]}" "GIT_AUTHOR_DATE=2000-01-01T00:00:00Z" "GIT_COMMITTER_DATE=2000-01-01T00:00:00Z")
    env "${gitenv[@]}" git -C "$sb/work" init -q
    env "${gitenv[@]}" git -C "$sb/work" add -A
    env "${gitenv[@]}" git -C "$sb/work" -c user.name=idem -c user.email=idem@local \
      -c commit.gpgsign=false commit -q -m seed --allow-empty
  fi

  # ---------- stdin replay (substituted once) ----------
  stdin_src=/dev/null
  if [[ -n "$stdin_file" ]]; then
    local content
    content="$(cat "$stdin_file")"; printf '%s' "${content//\{\{SANDBOX\}\}/$sb}" >"$sb/.stdin"
    stdin_src="$sb/.stdin"
  fi

  # ---------- ignore matching ----------
  ig=("__pycache__" "*.pyc" ".pytest_cache" ".mypy_cache" ".ruff_cache" ".DS_Store"
      ${ignores[@]+"${ignores[@]}"})

  # ---------- run twice, diff ----------
  local rc1=0; run || rc1=$?
  if [[ "$rc1" -ne 0 && "$allow_nonzero" != 1 ]]; then
    printf 'run 1 exited %s (use --allow-nonzero to test anyway)\n' "$rc1" >&2; exit 2
  fi
  local m1; m1="$(manifest)"; cp -a "$sb/work" "$sb/run1"

  local rc2=0; run || rc2=$?
  if [[ "$rc2" -ne "$rc1" ]]; then
    printf 'NOT idempotent: exit parity 0->1 (run1=%s run2=%s)\n' "$rc1" "$rc2" >&2; exit 1
  fi
  local m2; m2="$(manifest)"

  if [[ "$m1" != "$m2" ]]; then
    printf 'NOT idempotent: state diverged between run 1 and run 2:\n' >&2
    diff -ruN -x .git --no-dereference "$sb/run1" "$sb/work" >&2 2>/dev/null || true
    exit 1
  fi
  exit 0
}

main "$@"

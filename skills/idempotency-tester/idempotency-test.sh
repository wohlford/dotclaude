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

usage() { sed -n '4,12p' "$0" >&2; }

seed=""; stdin_file=""; runner=""; do_git=0; allow_nonzero=0; keep=0
setups=(); envs=(); ignores=()

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
target="$1"; shift
targs=("$@")

[[ -e "$target" ]] || { printf 'target not found: %s\n' "$target" >&2; exit 2; }
target_abs="$(cd "$(dirname "$target")" && pwd)/$(basename "$target")"

# ---------- interpreter ----------
cmd_argv=()
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
SB="$(mktemp -d)"
# shellcheck disable=SC2329  # cleanup is invoked indirectly via trap
cleanup() { [[ "$keep" == 1 ]] && { printf 'sandbox kept: %s\n' "$SB" >&2; return; }; rm -rf "$SB"; }
trap cleanup EXIT
mkdir -p "$SB/work" "$SB/home" "$SB/tmp" "$SB/xdg/config" "$SB/xdg/cache" "$SB/xdg/data"

[[ -n "$seed" ]] && cp -a "$seed/." "$SB/work/"

base_env=(
  "HOME=$SB/home" "TMPDIR=$SB/tmp"
  "XDG_CONFIG_HOME=$SB/xdg/config" "XDG_CACHE_HOME=$SB/xdg/cache" "XDG_DATA_HOME=$SB/xdg/data"
  "TZ=UTC" "LC_ALL=C" "PYTHONDONTWRITEBYTECODE=1" "PYTHONHASHSEED=0"
  "GIT_CONFIG_NOSYSTEM=1" "GIT_CONFIG_GLOBAL=/dev/null" "IDEMPOTENCY_SANDBOX=$SB"
)
run_env=("${base_env[@]}")
for e in ${envs[@]+"${envs[@]}"}; do run_env+=("${e//\{\{SANDBOX\}\}/$SB}"); done

# ---------- setup (once, before run 1) ----------
for s in ${setups[@]+"${setups[@]}"}; do
  ( cd "$SB/work" && env "${base_env[@]}" bash -c "$s" ) \
    || { printf 'setup failed (exit %s): %s\n' "$?" "$s" >&2; exit 2; }
done

# ---------- optional git seed ----------
if [[ "$do_git" == 1 ]]; then
  gitenv=("${base_env[@]}" "GIT_AUTHOR_DATE=2000-01-01T00:00:00Z" "GIT_COMMITTER_DATE=2000-01-01T00:00:00Z")
  env "${gitenv[@]}" git -C "$SB/work" init -q
  env "${gitenv[@]}" git -C "$SB/work" add -A
  env "${gitenv[@]}" git -C "$SB/work" -c user.name=idem -c user.email=idem@local \
    -c commit.gpgsign=false commit -q -m seed --allow-empty
fi

# ---------- stdin replay (substituted once) ----------
stdin_src=/dev/null
if [[ -n "$stdin_file" ]]; then
  content="$(cat "$stdin_file")"; printf '%s' "${content//\{\{SANDBOX\}\}/$SB}" >"$SB/.stdin"
  stdin_src="$SB/.stdin"
fi

# ---------- ignore matching ----------
ig=("__pycache__" "*.pyc" ".pytest_cache" ".mypy_cache" ".ruff_cache" ".DS_Store"
    ${ignores[@]+"${ignores[@]}"})
ignored() { local p="$1" g; for g in "${ig[@]}"; do
  # shellcheck disable=SC2053
  [[ "$p" == $g || "$p" == */$g || "$p" == $g/* || "$p" == */$g/* ]] && return 0; done; return 1; }

# ---------- manifest (content-only; NEVER dereference symlinks) ----------
manifest() {
  local root p rel x gd wt
  for root in work home xdg; do
    [[ -d "$SB/$root" ]] || continue
    ( cd "$SB/$root" && find . -mindepth 1 -name .git -prune -o \( -type f -o -type l -o -type d \) -print \
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
    done < <(find "$SB/$root" -type d -name .git 2>/dev/null | LC_ALL=C sort)
  done
}

# ---------- run the target (errexit-safe) ----------
run() {
  local rargs=() a rc
  for a in ${targs[@]+"${targs[@]}"}; do rargs+=("${a//\{\{SANDBOX\}\}/$SB}"); done
  set +e
  ( cd "$SB/work" && env "${run_env[@]}" "${cmd_argv[@]}" ${rargs[@]+"${rargs[@]}"} ) <"$stdin_src" >/dev/null 2>&1
  rc=$?
  set -e
  return "$rc"
}

rc1=0; run || rc1=$?
if [[ "$rc1" -ne 0 && "$allow_nonzero" != 1 ]]; then
  printf 'run 1 exited %s (use --allow-nonzero to test anyway)\n' "$rc1" >&2; exit 2
fi
m1="$(manifest)"; cp -a "$SB/work" "$SB/run1"

rc2=0; run || rc2=$?
if [[ "$rc2" -ne "$rc1" ]]; then
  printf 'NOT idempotent: exit parity 0->1 (run1=%s run2=%s)\n' "$rc1" "$rc2" >&2; exit 1
fi
m2="$(manifest)"

if [[ "$m1" != "$m2" ]]; then
  printf 'NOT idempotent: state diverged between run 1 and run 2:\n' >&2
  diff -ruN --no-dereference "$SB/run1" "$SB/work" >&2 2>/dev/null || true
  exit 1
fi
exit 0

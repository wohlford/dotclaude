#!/usr/bin/env python3
"""Mutation campaign for scripts/guard-secrets.sh, driven by scripts/lib/mutate.py.

Deliberately NOT named `test_*`: it mutates a tracked file in place, so pytest must not collect
it. Run it on demand through `scripts/kill-sets.py --campaign scripts/tests/mutate_guard_secrets.py`
(or directly), and never while editing the subject, since the restore would clobber your edits.
The repo root is derived from __file__, so run it from the working copy you intend to grade.

Each row is an edit of the finished guard, one token wherever the branch allows it; every `old`
string occurs exactly once in the guard (mutate.py refuses otherwise). Two rows are block moves, not
token edits, because the property they pin is an ORDER: their `old`/`new` are literal copies of the
guard's blocks (so the anchors checker can read them), and the campaign fails loudly if the guard's
text drifts from the copy.

Documented exclusions: a survivor here is NOT a defect, and none of these has a row (eleven).

  1. The two `|| true` guards on the watchdog's final `kill "$pid"` and on `wait "$wd"`. They defend
     a race window (the enumerator exiting between the last `kill -0` and the `kill`) and are
     deterministically unkillable: on the fast and bound-hit paths the watchdog exits 0 and
     `wait "$wd"` returns 0, so deleting either leaves every suite row green.
  2. Dropping empty comma pieces in `glob_args`: rg accepts an empty `--glob=`, so the edit is
     behaviour-neutral.
  3. The blank-line skip in the project-globs loader: an empty glob matches only an empty subject,
     so removing it is behaviour-neutral.
  4. There is no separate CR strip to mutate: the surrounding-whitespace strip removes CR.
  5. The matcher registration in settings.json, which is not this subject file (covered by the
     standing registration row and the hook meta-test modules).
  8. The early universal basename check (A10 a, A12 item 1), including its raw-field half. The later
     checks (the target's and the raw target's basename after normalization) deny exactly the same
     names, so removing the early raw check changes no verdict (spot-checked on a scratch copy: the
     whole suite stays green, including the dot-env-example-with-a-trailing-space row; the A12 spec
     expected that row to kill it, and it does not). The early check only decides earlier.
     The A10 text follows. Once `normalize_path` is linear the later check denies
     exactly the same names and no admissible payload is large enough to time out the linear function,
     so removing the early check changes no verdict: an EQUIVALENT mutant, not a defect (an exemption
     dropped from it IS killed, by the dot-env-example rows).
  9. The by-index walk of the split fields in `normalize_path` (the version that was quadratic on
     /bin/bash 3.2). Under the A13 field cap (65,536 bytes) it costs about 2.0 s against 1.6 s for the
     for-walk on a 60 KB path, so no wall-clock bound separates them without flaking; the cap bounds the
     damage, and the 60 KB timing row on bash 3.2 still pins the absolute time.
  7. The non-fatal marker write in the watchdog (`{ : >"$WALK_TMP/bound_hit"; } 2>/dev/null || true`).
     It only matters when the temp directory vanishes mid-walk, which no row can arrange
     deterministically (the stub cannot name the hook's private temp directory); reverting it is
     covered by review, not by a row.
  6. The 256-name deadline check inside `scan_universal`. The universal pass costs about 0.07 ms a
     name, so tripping its own deadline needs roughly 150,000 names, which no row builds; the
     project pass's checks (one mutant below) are the ones a row can reach.

  10. The A15 reset of SECONDS moved from before `input=$(cat)` to after it: only a row that delays stdin
      could kill that, and the suite has no such rig. Its placement is argued by the order of the guard's
      own steps: `cat` is the one step whose length depends on the payload and on the harness, so the
      reset sits before it.
  11. The same reset moved from the top of the script to just before the walk: a move across about 300
      lines, not a single (old, new) replacement, so the negative-constant mutant pins that the reset
      value matters and the placement relative to the pre-walk work is left unpinned.

Not covered by a row, by design of the brief rather than by oversight:

  - "The universal-before-project order swapped" (the row that pins it asserts the first message is
    the universal one) has no token or block edit that reorders the two checks without rewriting
    the control flow around them, so it is unmapped; the universal pass moved after the project pass
    (below) is the nearest real edit.

Watch list for the controller: the "loader 3-star cap removed" mutant is caught only by a bash 3.2
row that runs a 9-star glob against a 46-character name, which takes seconds uncapped (not an
unbounded hang); if a campaign ever reports TIMEOUT (an ERROR, not CAUGHT) on that row, look there.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "scripts" / "lib"))
import mutate  # noqa: E402

SUBJECT = REPO / "scripts" / "guard-secrets.sh"
SUITE = ["bash", str(REPO / "scripts" / "tests" / "test_guard_secrets.sh")]

# Every anchor below is a string literal or a module-level constant bound to one, so
# scripts/mutation-anchors-check.py can read it statically. The block texts were copied from the
# guard at 5f5e204 rather than sliced out of it at import time. The trade: if the guard text drifts,
# the anchor stops resolving and the campaign (and that check) fails loudly, which is the safe
# direction; re-copy the block from the guard to repair it.

# Block moves, as literal text copied from the guard: step 5b (enumeration and universal pass).
_STEP_5B = (
    "# Step 5b: a content-mode Grep of a directory — enumerate what the tool would read, then scan it.\n"
    "# Roots: the tool-computed path first; the raw path as a SECOND root when it names a different existing\n"
    "# directory (over-blocks only: the tool does not read it).\n"
    'walked=0; second_root=0; ROOTS=(); ENUM_PID=""; WALK_TMP=""\n'
    'if [[ "$tool_name" == "Grep" && "$output_mode" == "content" ]]; then\n'
    '  if [[ -d "$target" ]]; then ROOTS+=("$target"); fi\n'
    '  if [[ "$raw_root" != "$target" && -d "$raw_target" ]]; then ROOTS+=("$raw_target"); second_root=1; fi\n'
    "fi\n"
    "if [[ ${#ROOTS[@]} -gt 0 ]]; then\n"
    "  walked=1\n"
    '  WALK_TMP="$(mktemp -d 2>/dev/null)" || deny_unverified "enumerator failed to run"\n'
    '  trap \'if [[ -n "$ENUM_PID" ]]; then kill "$ENUM_PID" 2>/dev/null || true; fi; rm -rf -- "$WALK_TMP"\' EXIT\n'
    '  list="$WALK_TMP/list"\n'
    '  N="$(walk_secs)"\n'
    "  start=$SECONDS\n"
    "  deadline=$((start + N + 1))\n"
    "  if (( deadline > GS_WALK_CEIL )); then deadline=$GS_WALK_CEIL; fi\n"
    '  glob_args "$grep_glob"\n'
    '  if [[ -n "$grep_type" && "$grep_type" =~ ^[A-Za-z0-9_+-]+$ ]]; then TYPE_ARG=("--type=$grep_type"); fi\n'
    "  tool_is_rg=0\n"
    "  if command -v rg >/dev/null 2>&1; then tool_is_rg=1; fi\n"
    "  enum=failed\n"
    '  if [[ "$tool_is_rg" -eq 1 ]]; then\n'
    '    run_bounded "$list" "$deadline" rg --no-config --files --null --hidden \\\n'
    "      --glob='!.git' --glob='!.svn' --glob='!.hg' --glob='!.bzr' --glob='!.jj' --glob='!.sl' \\\n"
    '      ${GLOB_ARGS[@]+"${GLOB_ARGS[@]}"} ${TYPE_ARG[@]+"${TYPE_ARG[@]}"} -- "${ROOTS[@]}"\n'
    "    classify\n"
    '    if [[ "$enum" == "retry" ]]; then\n'
    "      # An unknown --type exits 2 with no output while the tool reads files: rerun without it (a widening).\n"
    "      TYPE_ARG=()\n"
    '      run_bounded "$list" "$deadline" rg --no-config --files --null --hidden \\\n'
    "        --glob='!.git' --glob='!.svn' --glob='!.hg' --glob='!.bzr' --glob='!.jj' --glob='!.sl' \\\n"
    '        ${GLOB_ARGS[@]+"${GLOB_ARGS[@]}"} -- "${ROOTS[@]}"\n'
    "      classify\n"
    "    fi\n"
    "  else\n"
    '    run_bounded "$list" "$deadline" find -H "${ROOTS[@]}" \\\n'
    "      \\( -name .git -o -name .svn -o -name .hg -o -name .bzr -o -name .jj -o -name .sl \\) -prune \\\n"
    "      -o -type f -print0\n"
    "    classify\n"
    "  fi\n"
    '  case "$enum" in\n'
    '    failed) deny_unverified "enumerator failed to run" ;;\n'
    "    hit)\n"
    "      # Scan the partial listing for a short grace, so a secret already listed is named.\n"
    '      rc=0; scan_universal "$list" $((SECONDS + 3)) || rc=$?\n'
    '      if [[ "$rc" -eq 1 ]]; then deny_dir "$SCAN_HIT"; fi\n'
    '      deny_unverified "enumeration exceeded $N s" ;;\n'
    "  esac\n"
    '  rc=0; scan_universal "$list" "$deadline" || rc=$?\n'
    '  case "$rc" in\n'
    '    1) deny_dir "$SCAN_HIT" ;;\n'
    '    3) deny_unverified "enumeration exceeded $N s" ;;\n'
    "  esac\n"
    "fi\n"
    "\n"
)

# and steps 6-7 (project load and target check).
_STEPS_67 = (
    "# Steps 6-7: load the project globs (only now, after every universal check has had its say) and judge\n"
    "# the target. The shared deadline applies only when step 5b ran; otherwise there is none.\n"
    "PG_DEADLINE=0\n"
    'if [[ "$walked" -eq 1 ]]; then PG_DEADLINE=$deadline; fi\n'
    "load_project_globs\n"
    'PVT_ROOT="$target"; PVT_PHYS=""; PVR_ROOT="$raw_root"; PVR_PHYS=""\n'
    "if [[ ${#PGLOBS[@]} -gt 0 ]]; then\n"
    "  # Every root keeps its OWN physical view (a view that cannot be computed is absent, not a deny): PHYS_T of\n"
    "  # the tool-computed target, PHYS_R of the raw path when it differs and exists. Each only over-blocks.\n"
    '  phys_of "$target"; PHYS_T="$PHYS"; PHYS_R=""\n'
    '  if [[ "$raw_root" != "$target" && -e "$raw_target" ]]; then phys_of "$raw_target"; PHYS_R="$PHYS"; fi\n'
    '  pdeny=""\n'
    '  if project_target_hit "$target"; then pdeny="$target"; fi\n'
    '  for v in "$PHYS_T" "$PHYS_R"; do\n'
    '    if [[ -z "$pdeny" ]] && (( PG_OVERRUN == 0 )) && [[ -n "$v" && "$v" != "$target" ]]; then\n'
    '      if project_target_hit "$v"; then pdeny="$target"; fi\n'
    "    fi\n"
    "  done\n"
    '  if [[ -n "$pdeny" ]]; then :\n'
    "  elif (( PG_OVERRUN == 0 )); then\n"
    '    for p in "$raw_target" "$target"; do\n'
    '      if [[ -L "$p" ]]; then\n'
    '        resolved=$(readlink -f -- "$p" 2>/dev/null || true)\n'
    '        if [[ -n "$resolved" ]] && project_target_hit "$resolved"; then pdeny="$target -> $resolved"; break; fi\n'
    "        if (( PG_OVERRUN )); then break; fi\n"
    "      fi\n"
    "    done\n"
    "  fi\n"
    '  if [[ -z "$pdeny" ]] && (( PG_OVERRUN )); then deny_unverified "enumeration exceeded ${N:-?} s"; fi\n'
    '  if [[ -n "$pdeny" ]]; then deny_file "$pdeny" "Project secret glob ($PGLOB_FILE): $PGLOB_MATCH"; fi\n'
    '  if [[ -n "$PHYS_T" && "$PHYS_T" != "$target" ]]; then PVT_PHYS="$PHYS_T"; fi\n'
    '  if (( second_root )) && [[ -n "$PHYS_R" && "$PHYS_R" != "$PVR_ROOT" ]]; then PVR_PHYS="$PHYS_R"; fi\n'
    "fi\n"
    "\n"
)

# The universal listing pass, and the text from just after it through the project pass.
_UNIVERSAL_PASS = (
    '  rc=0; scan_universal "$list" "$deadline" || rc=$?\n'
    '  case "$rc" in\n'
    '    1) deny_dir "$SCAN_HIT" ;;\n'
    '    3) deny_unverified "enumeration exceeded $N s" ;;\n'
    "  esac\n"
)

_AFTER_UNIVERSAL_TO_PROJECT_PASS = (
    "fi\n"
    "\n"
    "# Steps 6-7: load the project globs (only now, after every universal check has had its say) and judge\n"
    "# the target. The shared deadline applies only when step 5b ran; otherwise there is none.\n"
    "PG_DEADLINE=0\n"
    'if [[ "$walked" -eq 1 ]]; then PG_DEADLINE=$deadline; fi\n'
    "load_project_globs\n"
    'PVT_ROOT="$target"; PVT_PHYS=""; PVR_ROOT="$raw_root"; PVR_PHYS=""\n'
    "if [[ ${#PGLOBS[@]} -gt 0 ]]; then\n"
    "  # Every root keeps its OWN physical view (a view that cannot be computed is absent, not a deny): PHYS_T of\n"
    "  # the tool-computed target, PHYS_R of the raw path when it differs and exists. Each only over-blocks.\n"
    '  phys_of "$target"; PHYS_T="$PHYS"; PHYS_R=""\n'
    '  if [[ "$raw_root" != "$target" && -e "$raw_target" ]]; then phys_of "$raw_target"; PHYS_R="$PHYS"; fi\n'
    '  pdeny=""\n'
    '  if project_target_hit "$target"; then pdeny="$target"; fi\n'
    '  for v in "$PHYS_T" "$PHYS_R"; do\n'
    '    if [[ -z "$pdeny" ]] && (( PG_OVERRUN == 0 )) && [[ -n "$v" && "$v" != "$target" ]]; then\n'
    '      if project_target_hit "$v"; then pdeny="$target"; fi\n'
    "    fi\n"
    "  done\n"
    '  if [[ -n "$pdeny" ]]; then :\n'
    "  elif (( PG_OVERRUN == 0 )); then\n"
    '    for p in "$raw_target" "$target"; do\n'
    '      if [[ -L "$p" ]]; then\n'
    '        resolved=$(readlink -f -- "$p" 2>/dev/null || true)\n'
    '        if [[ -n "$resolved" ]] && project_target_hit "$resolved"; then pdeny="$target -> $resolved"; break; fi\n'
    "        if (( PG_OVERRUN )); then break; fi\n"
    "      fi\n"
    "    done\n"
    "  fi\n"
    '  if [[ -z "$pdeny" ]] && (( PG_OVERRUN )); then deny_unverified "enumeration exceeded ${N:-?} s"; fi\n'
    '  if [[ -n "$pdeny" ]]; then deny_file "$pdeny" "Project secret glob ($PGLOB_FILE): $PGLOB_MATCH"; fi\n'
    '  if [[ -n "$PHYS_T" && "$PHYS_T" != "$target" ]]; then PVT_PHYS="$PHYS_T"; fi\n'
    '  if (( second_root )) && [[ -n "$PHYS_R" && "$PHYS_R" != "$PVR_ROOT" ]]; then PVR_PHYS="$PHYS_R"; fi\n'
    "fi\n"
    "\n"
    "# Step 8: the project pass over the listing kept by step 5b.\n"
    'if [[ "$walked" -eq 1 ]]; then\n'
    '  rc=0; scan_project "$list" "$deadline" || rc=$?\n'
    '  case "$rc" in\n'
    '    1) deny_dir "$SCAN_HIT" ;;\n'
    '    3) deny_unverified "enumeration exceeded $N s" ;;\n'
    "  esac\n"
)

# The linear normalize_path now in the guard, replaced by the quadratic form below.
_NORMALIZE_BODY = (
    "normalize_path() {\n"
    '  local path="$1" seg abs=0 n=0 left joined=""\n'
    "  local -a parts=() out=()\n"
    '  if [[ "$path" == /* ]]; then abs=1; fi\n'
    "  # One split, then a stack in an indexed array; the remainder is never rewritten and the fields are walked\n"
    "  # with `for` (an indexed read of a big array walks it from the start on bash 3.2, which is quadratic).\n"
    "  # The here-string's own newline lands in the LAST field (after the appended '/'), so that field is\n"
    "  # ignored (`left` counts down to it); -d '' keeps newlines inside segments. Measured linear on bash 5\n"
    "  # (0.6/0.9/1.8 s) and /bin/bash 3.2 (0.7/1.4/2.8 s) at 64/128/256 KB; no length cap, and the suite pins it.\n"
    "  IFS=/ read -r -d '' -a parts <<< \"$path/\" || true\n"
    "  left=${#parts[@]}\n"
    '  for seg in ${parts[@]+"${parts[@]}"}; do\n'
    "    left=$((left - 1))\n"
    '    if [[ "$left" -eq 0 ]]; then break; fi\n'
    '    case "$seg" in\n'
    "      ''|'.') continue ;;\n"
    "      '..')\n"
    '        if [[ "$n" -gt 0 && "${out[$((n - 1))]}" != \'..\' ]]; then\n'
    "          n=$((n - 1))\n"
    '        elif [[ "$abs" -eq 1 ]]; then\n'
    "          :\n"
    "        else\n"
    "          out[n]='..'; n=$((n + 1))\n"
    "        fi ;;\n"
    '      *) out[n]="$seg"; n=$((n + 1)) ;;\n'
    "    esac\n"
    "  done\n"
    '  if [[ "$n" -gt 0 ]]; then\n'
    '    out=("${out[@]:0:$n}")\n'
    "    local IFS=/\n"
    '    joined="${out[*]}"\n'
    "  fi\n"
    '  if [[ "$abs" -eq 1 ]]; then printf \'/%s\' "$joined"; else printf \'%s\' "$joined"; fi\n'
    "}\n"
)

# The SIGPIPE trap and the contract check that follows it: the "trap moved after the check" mutant.
_TRAP_TO_INPUT = (
    "trap '' PIPE\n"
    "\n"
    "# HOOK CONTRACT: the target arrives as a JSON payload on stdin; argv is ignored. Refuse the two\n"
    "# invocations this cannot serve, because each otherwise reads as SUCCESS — with argv and stdin at\n"
    "# EOF it exits 0 having examined nothing, and with a terminal stdin it blocks forever.\n"
    'if [ "$#" -gt 0 ] || [ -t 0 ]; then\n'
    "  printf '%s\\n' \\\n"
    '    "$(basename "$0") is a Claude Code hook: it reads a JSON payload on stdin and ignores arguments." \\\n'
    '    "Running it with filenames examines nothing. See scripts/HOOKS.md for the payload form." >&2 || true\n'
    "  exit 2\n"
    "fi\n"
    "\n"
)
# The same block with the trap moved to its end.
_TRAP_MOVED = (
    "\n"
    "# HOOK CONTRACT: the target arrives as a JSON payload on stdin; argv is ignored. Refuse the two\n"
    "# invocations this cannot serve, because each otherwise reads as SUCCESS — with argv and stdin at\n"
    "# EOF it exits 0 having examined nothing, and with a terminal stdin it blocks forever.\n"
    'if [ "$#" -gt 0 ] || [ -t 0 ]; then\n'
    "  printf '%s\\n' \\\n"
    '    "$(basename "$0") is a Claude Code hook: it reads a JSON payload on stdin and ignores arguments." \\\n'
    '    "Running it with filenames examines nothing. See scripts/HOOKS.md for the payload form." >&2 || true\n'
    "  exit 2\n"
    "fi\n"
    "trap '' PIPE\n"
    "\n"
)
# The quadratic shell trim of 393970b, reinstated by the "quadratic trim after the jq field" mutant (a new text).
_SHELL_TRIM = (
    "JS_WS=(\" \" $'\\t' $'\\n' $'\\v' $'\\f' $'\\r' $'\\xc2\\xa0' $'\\xef\\xbb\\xbf' $'\\xe1\\x9a\\x80' $'\\xe2\\x80\\x80'\n"
    "  $'\\xe2\\x80\\x81' $'\\xe2\\x80\\x82' $'\\xe2\\x80\\x83' $'\\xe2\\x80\\x84' $'\\xe2\\x80\\x85' $'\\xe2\\x80\\x86'\n"
    "  $'\\xe2\\x80\\x87' $'\\xe2\\x80\\x88' $'\\xe2\\x80\\x89' $'\\xe2\\x80\\x8a' $'\\xe2\\x80\\xa8' $'\\xe2\\x80\\xa9'\n"
    "  $'\\xe2\\x80\\xaf' $'\\xe2\\x81\\x9f' $'\\xe3\\x80\\x80')\n"
    "js_trim() {\n"
    '  local str="$1" w again=1\n'
    "  while (( again )); do\n"
    "    again=0\n"
    '    for w in "${JS_WS[@]}"; do\n'
    '      if [[ "$str" == "$w"* ]]; then str="${str#"$w"}"; again=1; fi\n'
    '      if [[ "$str" == *"$w" ]]; then str="${str%"$w"}"; again=1; fi\n'
    "    done\n"
    "  done\n"
    '  TRIMMED="$str"\n'
    "}\n"
    'js_trim "$file_path"; tool_path="$TRIMMED"\n'
)

# The quadratic normalize_path of c8b0bf9, frozen: the "quadratic form" mutant puts it back.
_OLD_NORMALIZE = r"""normalize_path() {
  local path="$1" rest seg abs=0 n=0 joined="" i
  local -a out=()
  if [[ "$path" == /* ]]; then abs=1; fi
  rest="$path"
  while [[ -n "$rest" ]]; do
    seg="${rest%%/*}"
    if [[ "$rest" == */* ]]; then rest="${rest#*/}"; else rest=""; fi
    case "$seg" in
      ''|'.') continue ;;
      '..')
        if [[ "$n" -gt 0 && "${out[$((n - 1))]}" != '..' ]]; then
          unset "out[$((n - 1))]"; n=$((n - 1))
        elif [[ "$abs" -eq 1 ]]; then
          :
        else
          out[n]='..'; n=$((n + 1))
        fi ;;
      *) out[n]="$seg"; n=$((n + 1)) ;;
    esac
  done
  i=0
  while [[ "$i" -lt "$n" ]]; do
    if [[ "$i" -eq 0 ]]; then joined="${out[0]}"; else joined="$joined/${out[$i]}"; fi
    i=$((i + 1))
  done
  if [[ "$abs" -eq 1 ]]; then printf '/%s' "$joined"; else printf '%s' "$joined"; fi
}
"""

# A13: the oversize case, and the lines between it and the empty-target exit, as literal text.
_OVERSIZE_CASE = (
    '  case "$status" in\n'
    '    oversize:*) IFS=: read -r _ over_field over_len over_cap <<< "$status"; deny_oversize "$over_field" "$over_len" "$over_cap" ;;\n'
    "  esac\n"
)
_OVERSIZE_REST = (
    "else\n"
    '  file_path=$(printf \'%s\' "$input" | sed -n \'s/.*"\\(file_path\\|path\\|notebook_path\\)"[[:space:]]*:[[:space:]]*"\\([^"]*\\)".*/\\2/p\' | head -1) || file_path=""\n'
    '  tool_path="$file_path"   # the sed fallback does not trim (part of its documented narrower scope)\n'
    "fi\n"
    "# A Grep with no target searches its working directory: the payload cwd, else the hook's own $PWD.\n"
    'if [[ -z "$tool_path" && "$tool_name" == "Grep" ]]; then file_path="${cwd:-$PWD}"; tool_path="$file_path"; fi\n'
    '[[ -n "$tool_path" ]] || exit 0\n'
)
_OVERSIZE_TO_EXIT = _OVERSIZE_CASE + _OVERSIZE_REST
_OVERSIZE_MOVED = (
    _OVERSIZE_REST
    + 'case "$status" in\n'
    + '  oversize:*) IFS=: read -r _ over_field over_len over_cap <<< "$status"; deny_oversize "$over_field" "$over_len" "$over_cap" ;;\n'
    + "esac\n"
)

M = mutate.Mutation

MUTATIONS = [
    # ---- target resolution ----
    M("tilde-slash expansion removed", "'~/'*) if", "'~~/'*) if"),
    M(
        "relative path no longer resolved against cwd",
        '*) p="$base/$p" ;;',
        '*) p="$p" ;;',
    ),
    M(
        "normalize_path call removed",
        'target="$(normalize_path "$(resolve_raw "$tool_path" "$cwd")")"',
        'target="$(resolve_raw "$tool_path" "$cwd")"',
    ),
    M(
        "raw-path leaf symlink check removed (symlinked-directory dotdot row)",
        'for p in "$raw_target" "$target"; do\n    if [[ -L "$p" ]]; then\n      # A symlink',
        'for p in "$target"; do\n    if [[ -L "$p" ]]; then\n      # A symlink',
    ),
    M(
        "normalized-path leaf symlink check removed (nonexistent-dotdot row)",
        'for p in "$raw_target" "$target"; do\n    if [[ -L "$p" ]]; then\n      # A symlink',
        'for p in "$raw_target"; do\n    if [[ -L "$p" ]]; then\n      # A symlink',
    ),
    M(
        "A9: the target's own physical view dropped from the target check (rows 1, 2)",
        'for v in "$PHYS_T" "$PHYS_R"; do',
        'for v in "$PHYS_R"; do',
    ),
    M(
        "A9: the target's physical view computed from the raw path (rows 1, 2, 3, 5)",
        'phys_of "$target"; PHYS_T="$PHYS"',
        'phys_of "$raw_target"; PHYS_T="$PHYS"',
    ),
    M(
        "A9: the raw path's physical view dropped from the target check (row 4 only)",
        'for v in "$PHYS_T" "$PHYS_R"; do',
        'for v in "$PHYS_T"; do',
    ),
    M(
        "A9: the target root's entries no longer mapped through its physical view (row 3 and the symlinked-root physical-prefix row)",
        '"$f" == "$PVT_ROOT"/* ]]; then',
        '"$f" == "$PVT_ROOT.none"/* ]]; then',
    ),
    M(
        "A9: the second root mapped through the target's physical view (row 4b only)",
        'PVR_PHYS="$PHYS_R"; fi',
        'PVR_PHYS="$PHYS_T"; fi',
    ),
    M(
        "Grep no longer defaults to the payload cwd",
        'file_path="${cwd:-$PWD}"',
        'file_path=""',
    ),
    M("notebook_path no longer extracted", " // .tool_input.notebook_path", ""),
    # ---- the content-mode gate and the rg flags ----
    M(
        "output_mode content gate removed",
        '"$output_mode" == "content"',
        '"$output_mode" == *',
    ),
    M(
        "--no-config removed from the primary enumeration",
        '-eq 1 ]]; then\n    run_bounded "$list" "$deadline" rg --no-config --files',
        '-eq 1 ]]; then\n    run_bounded "$list" "$deadline" rg --files',
    ),
    M(
        "--hidden removed from the primary enumeration",
        "--null --hidden \\\n      --glob='!.git'",
        "--null \\\n      --glob='!.git'",
    ),
    M(
        "the .git exclude removed from the primary enumeration",
        "\n      --glob='!.git' --glob='!.svn'",
        "\n      --glob='!.svn'",
    ),
    M(
        "the .hg exclude removed from the primary enumeration",
        "--glob='!.hg' --glob='!.bzr' --glob='!.jj' --glob='!.sl' \\\n      ${GLOB_ARGS",
        "--glob='!.bzr' --glob='!.jj' --glob='!.sl' \\\n      ${GLOB_ARGS",
    ),
    M("glob whitespace split removed", 'read -ra toks <<< "$1"', 'toks=("$1")'),
    M("glob brace exception removed", "\"$tok\" == *'{'* &&", "\"$tok\" == *'{{'* &&"),
    M(
        "glob comma split removed",
        'IFS=, read -ra pieces <<< "$tok"',
        'pieces=("$tok")',
    ),
    M("--type pass-through removed", 'TYPE_ARG=("--type=$grep_type")', "TYPE_ARG=()"),
    M(
        "rg exit-2 rerun without --type removed",
        "then enum=retry; else",
        "then enum=ok; else",
    ),
    M("find fallback loses -H", 'find -H "${ROOTS[@]}"', 'find "${ROOTS[@]}"'),
    M(
        "find allowlist widened to accept rc 1 and 2",
        "0)   enum=ok ;;",
        "0|1|2) enum=ok ;;",
    ),
    M(
        "enumeration rooted at the raw path instead of the tool-computed one",
        'if [[ -d "$target" ]]; then ROOTS+=("$target"); fi',
        'if [[ -d "$raw_target" ]]; then ROOTS+=("$raw_target"); fi',
    ),
    M(
        "A12: the jq trim removed (tool_path taken as sent)",
        "(tp | nn | trim)",
        "(tp | nn)",
    ),
    M(
        "the raw path no longer enumerated as a second root",
        '-d "$raw_target" ]]; then ROOTS+=("$raw_target")',
        '-d "$raw_target/.none" ]]; then ROOTS+=("$raw_target")',
    ),
    M(
        "the EXIT trap no longer kills the enumerator",
        'trap \'if [[ -n "$ENUM_PID" ]]; then kill "$ENUM_PID" 2>/dev/null || true; fi; rm -rf -- "$WALK_TMP"\' EXIT',
        "trap 'rm -rf -- \"$WALK_TMP\"' EXIT",
    ),
    M(
        "file deny loses its non-fatal stderr write",
        "printf 'guard-secrets: BLOCKED %s\\n' \"$1\" >&2 || true",
        "printf 'guard-secrets: BLOCKED %s\\n' \"$1\" >&2",
    ),
    M(
        "directory deny loses its non-fatal stderr write",
        "printf '%s\\n' \"$first\" >&2 || true",
        "printf '%s\\n' \"$first\" >&2",
    ),
    M(
        "no find fallback when rg is absent",
        "if command -v rg >/dev/null",
        "if true >/dev/null",
    ),
    M(
        "rg status allowlist catch-all failed turned into ok",
        "else enum=ok; fi ;;\n      *)   enum=failed ;;",
        "else enum=ok; fi ;;\n      *)   enum=ok ;;",
    ),
    M(
        "fallback deny message keeps retry route 2",
        'if [[ "$tool_is_rg" -eq 1 ]]; then\n    printf \'  2. add a glob',
        "if true; then\n    printf '  2. add a glob",
    ),
    # ---- the bound ----
    M(
        "rc-143 half of the bound-hit verdict dropped (watchdog-fired-after-finish row)",
        '"$BOUND_RC" -eq 143 && ',
        "",
    ),
    M(
        "marker half of the bound-hit verdict dropped (self-TERM row)",
        ' && "$BOUND_HIT" -eq 1 ]]',
        " ]]",
    ),
    M(
        "watchdog kill neutered",
        'kill "$pid" 2>/dev/null || true',
        ': "$pid" 2>/dev/null || true',
    ),
    M(
        "both project-pass SECONDS deadline checks removed (scan_project and project_hit)",
        'local list="$1" deadline="$2" f n=0 suffix',
        'local list="$1" deadline=999999999 f n=0 suffix; PG_DEADLINE=0',
    ),
    M(
        "GUARD_SECRETS_WALK_SECS ignored",
        '"${GUARD_SECRETS_WALK_SECS:-10}"',
        '"10"',
    ),
    M(
        "walk_secs invalid-value default removed",
        "''|*[!0-9]*) v=10 ;;",
        "''|*[!0-9]*) : ;;",
    ),
    M(
        "walk_secs clamp to 20 removed",
        'if [ "$v" -gt 20 ]; then v=20; fi',
        'if [ "$v" -gt 20 ]; then :; fi',
    ),
    # ---- payload extraction ----
    M(
        "jq non-printable-ASCII glob blanking removed",
        ' and test("\\\\A[ -~]*\\\\z")',
        "",
    ),
    # ---- the project-globs loader and matcher ----
    M(
        "loader comment skip removed",
        " || \"$line\" == '#'* ]]; then continue; fi",
        " ]]; then continue; fi",
    ),
    M(
        "loader leading-whitespace strip removed",
        'line="${line#"${line%%[![:space:]]*}"}"',
        'line="${line}"',
    ),
    M(
        "loader trailing-whitespace strip removed",
        'line="${line%"${line##*[![:space:]]}"}"',
        'line="${line}"',
    ),
    M("loader byte cap removed", 'head -c 65536 -- "$f"', 'cat -- "$f"'),
    M(
        "loader 1000-line cap removed",
        "if (( n > 1000 )); then break; fi",
        "if (( n > 1000 )); then :; fi",
    ),
    M(
        "loader 512-byte line cap removed",
        "if (( ${#line} > 512 )); then continue; fi",
        "if (( ${#line} > 512 )); then :; fi",
    ),
    M(
        "loader 3-star cap removed",
        "if (( ${#stars} > 3 )); then continue; fi",
        "if (( ${#stars} > 3 )); then :; fi",
    ),
    M("relative-path match subject dropped", '"$1" == $g ||', '"" == $g ||'),
    M("basename match subject dropped", '"${2##*/}" == $g', '"" == $g'),
    M("full-path match subject dropped", '|| "$2" == $g ]]', '|| "" == $g ]]'),
    M("rel-slash directory form removed", 'project_hit_dir "$REL"', "false"),
    M(
        "a universal exemption outranks the project globs",
        'if project_target_hit "$target"; then pdeny="$target"',
        'if [[ "${target##*/}" != .env.example ]] && project_target_hit "$target"; then pdeny="$target"',
    ),
    M(
        "symlink-target project-glob check removed",
        'project_target_hit "$resolved"',
        "false",
    ),
    # ---- order (block moves) ----
    M(
        "project load and target check moved back before the enumeration (block move)",
        _STEP_5B + _STEPS_67,
        "walked=0; second_root=0\n\n" + _STEPS_67 + _STEP_5B,
    ),
    M(
        "universal listing pass moved after the project listing pass (block move)",
        _UNIVERSAL_PASS + _AFTER_UNIVERSAL_TO_PROJECT_PASS,
        _AFTER_UNIVERSAL_TO_PROJECT_PASS + _UNIVERSAL_PASS,
    ),
    # ---- A10: the early check and the linear normalize_path ----
    M(
        "A10: normalize_path body replaced by the quadratic form (timing rows only)",
        _NORMALIZE_BODY,
        _OLD_NORMALIZE,
    ),
    M(
        "A10: dotdot at the root no longer pinned (absolute class)",
        'elif [[ "$abs" -eq 1 ]]; then\n          :',
        "elif [[ \"$abs\" -eq 1 ]]; then\n          out[n]='..'; n=$((n + 1))",
    ),
    M(
        "A10: leading dotdot dropped (relative class)",
        "else\n          out[n]='..'; n=$((n + 1))",
        "else\n          :",
    ),
    M(
        "A10: empty-segment skip removed (absolute and slash classes)",
        "''|'.') continue ;;",
        "'.') continue ;;",
    ),
    M(
        "A10: join separator changed (every class with a multi-segment output)",
        "local IFS=/\n",
        "local IFS=:\n",
    ),
    M(
        "A10: an exemption dropped from the early check (the dot-env-example rows)",
        '|| secret_name "$early_tool"; then deny_file "$tool_path"',
        '|| [[ "$early_tool" == .env* ]]; then deny_file "$tool_path"',
    ),
    # ---- A12: linear trim and basename extraction ----
    M(
        "A12/A13: the quadratic shell trim reinstated on the raw path in place of the jq field (the 60000-character timing rows and the 60000-LF rows)",
        "# A Grep with no target searches its working directory: the payload cwd, else the hook's own $PWD.\n",
        _SHELL_TRIM
        + "# A Grep with no target searches its working directory: the payload cwd, else the hook's own $PWD.\n",
    ),
    M(
        "A12: path_base falls back to the quadratic expansion (trailing and mixed large-N rows)",
        'BASE="$prev"',
        'BASE="${1##*/}"',
    ),
    M(
        "A12: the leading trim alternative removed",
        "then ltrim | rev | ltrim | rev else",
        "then rev | ltrim | rev else",
    ),
    M(
        "A12: the trailing trim alternative removed",
        "then ltrim | rev | ltrim | rev else",
        "then ltrim | rev | rev else",
    ),
    M(
        "A12: the whitespace run reduced to one character",
        r'\ufeff]+"',
        r'\ufeff]"',
    ),
    M(
        "A12: the ASCII whitespace group dropped from the class",
        r"[\t\n\u000b\f\r \u00a0",
        r"[ \u00a0",
    ),
    M(
        "A12: U+00A0 and U+1680 dropped from the class",
        r"\u00a0\u1680\u2000-\u200a",
        r"\u2000-\u200a",
    ),
    M(
        "A12: the U+2000 to U+200A range dropped from the class",
        r"\u2000-\u200a\u2028",
        r"\u2028",
    ),
    M(
        "A12: U+2028 U+2029 U+202F U+205F U+3000 U+FEFF dropped from the class",
        r"\u2028\u2029\u202f\u205f\u3000\ufeff]+",
        r"]+",
    ),
    # ---- A13: NUL-delimited extraction and field caps ----
    M(
        "A13: the path cap raised a thousandfold (the 5000000-LF, 1000000-slash, 65537-byte path and cwd rows)",
        "GS_PATH_CAP=65536",
        "GS_PATH_CAP=65536000",
    ),
    M(
        "A13: the cap compared with >= (the 65536-byte threshold rows only)",
        "select(.[1] > .[2])",
        "select(.[1] >= .[2])",
    ),
    M(
        "A13: glob dropped from the over-cap list (the 4097-byte glob rows only)",
        '["glob", (.tool_input.glob | blen), $gcap], ',
        "",
    ),
    M(
        "A13: cwd dropped from the over-cap list (the 65537-byte cwd rows only)",
        ' ["cwd", (.cwd | blen), $pcap],',
        "",
    ),
    M(
        "A13: nn replaced by the identity (the two NUL rows, on /bin/bash 3.2)",
        r'def nn: if type == "string" then split("\u0000") | add // "" else "" end;',
        'def nn: if type == "string" then . else "" end;',
    ),
    M(
        "A13: the cap measured on the trimmed path instead of the raw one (the 5000000-LF rows)",
        '["path", (tp | blen), $pcap]',
        '["path", (tp | trim | blen), $pcap]',
    ),
    M(
        "A13: the field reads taken a line at a time (every LF row, and more)",
        "{ IFS= read -r -d '' status; IFS= read -r -d '' tool_name; IFS= read -r -d '' file_path; IFS= read -r -d '' cwd\n"
        "    IFS= read -r -d '' output_mode; IFS= read -r -d '' grep_glob; IFS= read -r -d '' grep_type; IFS= read -r -d '' tool_path\n"
        "  } <",
        "{ IFS= read -r status; IFS= read -r tool_name; IFS= read -r file_path; IFS= read -r cwd\n"
        "    IFS= read -r output_mode; IFS= read -r grep_glob; IFS= read -r grep_type; IFS= read -r tool_path\n"
        "  } <",
    ),
    M(
        "A13: the oversize deny moved after the empty-target exit (every oversize row)",
        _OVERSIZE_TO_EXIT,
        _OVERSIZE_MOVED,
    ),
    # ---- A14: tool_name and output_mode decided inside jq; type cap; per-field message ----
    M(
        "A14: the type dropped from the over-cap list (the 4097-byte type rows only)",
        ', ["type", (.tool_input.type | blen), $gcap] ]',
        " ]",
    ),
    M(
        "A14: tool_name passed through to bash again (the 5000000-space tool name rows, by time)",
        '(.tool_name | pick("grep"))',
        "(.tool_name | nn)",
    ),
    M(
        "A14: output_mode passed through to bash again (the 40000000-space output_mode rows, by time)",
        '(.tool_input.output_mode | pick("content"))',
        "(.tool_input.output_mode | nn)",
    ),
    M(
        "A14: the 16-byte gate removed from pick (ascii_downcase sees the huge value; the 40000000-space rows, by time)",
        'if utf8bytelength > 16 then "" elif ascii_downcase',
        "if ascii_downcase",
    ),
    M(
        "A14: pick compares case-sensitively (the lowercase grep and CONTENT rows, and every Grep row)",
        "elif ascii_downcase == $w then . else",
        "elif . == $w then . else",
    ),
    M(
        "A14: the glob message falls back to the path wording (the 4097-byte glob row only)",
        '    glob) why="a glob that long is not a pattern a Grep takes." ;;\n',
        "",
    ),
    M(
        "A14: the type message falls back to the path wording (the 4097-byte type row only)",
        '    type) why="a type that long is not a file type a Grep takes." ;;\n',
        "",
    ),
    # ---- whole-hook ceiling, cheap NUL strip, per-field second line ----
    M(
        "ceiling: the walk deadline no longer capped at GS_WALK_CEIL (the whole-hook ceiling rows)",
        "if (( deadline > GS_WALK_CEIL )); then deadline=$GS_WALK_CEIL; fi",
        ":",
    ),
    M(
        "NUL strip: pick strips with join again (the 4000000-NUL output_mode rows, by time)",
        'else (split("\\u0000") | add // "")',
        'else (split("\\u0000") | join(""))',
    ),
    M(
        "message: the type second line reverted to the path wording (the type second-line rows)",
        "    type) printf '  For a Grep, use one file type name.\\n' >&2 || true ;;\n",
        "",
    ),
    M(
        "message: the glob second line reverted to the path wording (the glob second-line rows)",
        "    glob) printf '  For a Grep, keep glob to the patterns you need.\\n' >&2 || true ;;\n",
        "",
    ),
    # ---- A11: ignore SIGPIPE ----
    M(
        "A11: the SIGPIPE trap removed (secret-Read, argv and UNVERIFIED rows)",
        "trap '' PIPE\n",
        "",
    ),
    M(
        "A11: the SIGPIPE trap moved after the contract check (argv row only, block move)",
        _TRAP_TO_INPUT,
        _TRAP_MOVED,
    ),
    # ---- A15: the lone-surrogate repair (gs_sanitize and gs_extract's retry) and the SECONDS reset ----
    M(
        "A15: the retry no longer re-extracts (every D1 row and the at-cap row)",
        'input="$san"; unset san; gs_extract; fi',
        'input="$san"; unset san; fi',
    ),
    M(
        "A15: the lookahead that keeps a valid pair removed (D1l, the sanitizer pair rows, the property row)",
        r"(?!\\u[dD][c-fC-F][0-9a-fA-F]{2})",
        "",
    ),
    M(
        "A15: the lookbehind that starts a match at the first backslash of a run removed (D1k, the escaped-backslash sanitizer rows)",
        r"s/(?<!\\)(?:",
        "s/(?:",
    ),
    M(
        "A15: the whole-pair consumption removed, odd runs read as even (D1n, the three- and 1,001-backslash sanitizer rows, the at-cap row)",
        r"(?:\\\\)*+\K",
        r"\K",
    ),
    M(
        "A15: the replacement text emptied, a lone high deleted instead of replaced (D1j, D1m, G5 and the sanitizer rows)",
        r"/\\ufffd/g'",
        "//g'",
    ),
    M(
        "A15: the top of the high range dropped, DBxx no longer repaired (D1g and the mixed-case sanitizer row)",
        "[89abAB]",
        "[89aA]",
    ),
    M(
        "A15: the uppercase D of the high class dropped (D1h and the mixed-case sanitizer row)",
        r"\K\\u[dD][89abAB]",
        r"\K\\ud[89abAB]",
    ),
    M(
        "A15: a failing perl no longer tolerated, set -e ends the hook (the failing-perl, no-perl and over-cap rows)",
        'gs_sanitize) || san=""',
        "gs_sanitize)",
    ),
    M(
        "A15: the 16 MiB cap removed (the over-cap hook row and three sanitizer rows)",
        "exit 3 if length($_) > 16777216; ",
        "",
    ),
    M(
        "A15: the whole-record slurp removed, the cap applies per line (the multi-line sanitizer row)",
        "-C0 -0777 -pe",
        "-C0 -pe",
    ),
    M(
        "A15: the byte-count switch removed, the cap counts characters under PERL_UNICODE (the PERL_UNICODE sanitizer row)",
        "perl -C0 -0777",
        "perl -0777",
    ),
    M(
        "A15: the inherited SECONDS no longer discarded (S1, S2, S3, S6)",
        "SECONDS=0\n",
        "",
    ),
]


def main() -> int:
    report = mutate.run(SUBJECT, SUITE, MUTATIONS, cwd=str(REPO))
    print(report.text)
    return report.rc


if __name__ == "__main__":
    sys.exit(main())

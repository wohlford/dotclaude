#!/usr/bin/env python3
"""Mutation campaign for the shellcheck note ratchet in scripts/style-check.sh, via scripts/lib/mutate.py.

Deliberately NOT named `test_*`: it mutates a tracked file in place, so pytest must not collect it.
Do not run it bare while a hook can fire on the subject: grade it with
`scripts/kill-sets.py --campaign scripts/tests/mutate_style_check_ratchet.py` from the repo root
(through `~/.claude/scripts/run-long.sh`), which runs every mutant on a fresh COPY of the tree and
reports each mutant's kill set; require `identical_pairs=0`, since `caught=N survived=0` alone does
not show that the mutants differ.

Each row weakens ONE mechanism of the ratchet and names the suite row that must catch it. The suite
(scripts/tests/test_style_check.sh) drives the shipped hook as a process, so a surviving mutant is a
claim about a behaviour nothing tests. Rows 21 to 23 are calibrated against the hazards the design
names: row 21 re-creates the `grep -q` on a live pipe under `set -o pipefail` (caught only by R27's
volume of about 160 KB), and row 22 puts an empty-array expansion under `set -u` into the ratchet
(caught only by the suite's second pass under /bin/bash, bash 3.2, where an unbound-variable abort
inside a function takes the status of the EXIT trap's last command: the ratchet's cleanup ends in a
successful `rm`, so the abort exits 0 and kills the rows that expect 2; the R1 rows expect 0 and
survive it. Measured on 3.2; not asserted for other versions), and row 23 removes the status capture
of the match-mode awk call (caught only by R24b, whose shim fails that call alone).

The repo root is derived from __file__: invoking this through a symlinked `~/.claude/scripts/tests/`
would resolve into PRODUCTION's tree and mutate that instead. Run it from the working copy you intend
to grade.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "scripts" / "lib"))
import mutate  # noqa: E402

SUBJECT = REPO / "scripts" / "style-check.sh"
SUITE = ["bash", str(REPO / "scripts" / "tests" / "test_style_check.sh")]

# Raw strings: the subject's awk program carries backslash-t and backslash-n as two characters.
MUTATIONS = [
    mutate.Mutation(
        "m01 key drops the line text, so an edited flagged line matches by code alone: R5",
        r"""  t = trim(src[p_ln])
  return p_code "\t" t
""",
        r"""  t = trim(src[p_ln])
  return p_code "\t"
""",
    ),
    mutate.Mutation(
        "m02 key adds the line number, so a line shift reads as a new note: R3",
        r"""  return p_code "\t" t
""",
        r"""  return p_code "\t" p_ln ":" t
""",
    ),
    mutate.Mutation(
        "m03 multiset becomes a set, a matched baseline key is not consumed: R4",
        r"""  if (pool[k] > 0) { pool[k]--; print "M\t" cnt }
""",
        r"""  if (pool[k] > 0) { print "M\t" cnt }
""",
    ),
    mutate.Mutation(
        "m04 warnings are keyed and suppressed through the baseline too: R6",
        r"""  i = index(rec, ": note: ")
""",
        r"""  i = index(rec, ": note: ") + index(rec, ": warning: ")
""",
    ),
    mutate.Mutation(
        "m05 a failed HEAD read empties the block set (fail open): R7 R8 R9 R10 R12",
        r"""    cat-file blob "HEAD:./${file_path##*/}" >"$sc_tmp/blob" 2>/dev/null && rc=0 || rc=$?
  [[ "$rc" -eq 0 ]] || return 0
""",
        r"""    cat-file blob "HEAD:./${file_path##*/}" >"$sc_tmp/blob" 2>/dev/null && rc=0 || rc=$?
  [[ "$rc" -eq 0 ]] || { sc_block=""; return 0; }
""",
    ),
    mutate.Mutation(
        "m06 the baseline is read from the index instead of HEAD: R10 R11",
        r"""cat-file blob "HEAD:./${file_path##*/}" """,
        r"""cat-file blob ":./${file_path##*/}" """,
    ),
    mutate.Mutation(
        "m07 the baseline is the working file itself (self-compare): R2",
        r"""  env -u GIT_DIR -u GIT_WORK_TREE -u GIT_INDEX_FILE -u GIT_OBJECT_DIRECTORY \
    -u GIT_ALTERNATE_OBJECT_DIRECTORIES -u GIT_COMMON_DIR \
    git --no-replace-objects -C "$sc_dir" --no-pager --no-optional-locks -c core.fsmonitor=false \
    cat-file blob "HEAD:./${file_path##*/}" >"$sc_tmp/blob" 2>/dev/null && rc=0 || rc=$?
""",
        r"""  cp "$file_path" "$sc_tmp/blob" && rc=0 || rc=$?
""",
    ),
    mutate.Mutation(
        "m08 GIT_DIR is no longer cleared from the baseline read: R13",
        r"""  env -u GIT_DIR -u GIT_WORK_TREE -u GIT_INDEX_FILE""",
        r"""  env -u GIT_WORK_TREE -u GIT_INDEX_FILE""",
    ),
    mutate.Mutation(
        "m09 current-run matching is not limited to the target file name: R16",
        r"""function ckey(rec) { if (parse(rec) && p_fn == want) return keyfor(cn, cur); return "" }""",
        r"""function ckey(rec) { if (parse(rec)) return keyfor(cn, cur); return "" }""",
    ),
    mutate.Mutation(
        "m10 the gate that skips the baseline when no target note exists is removed: R19",
        r"""  case "$gate" in '' | 0 | *[!0-9]*) return 0 ;; esac
""",
        r"""  :
""",
    ),
    mutate.Mutation(
        "m11 the pre-existing count is dropped from the passing stdout line: R18a R18b R18c",
        r"""if [[ "$sc_n" -gt 0 ]]; then
  printf 'style-check passed:""",
        r"""if false; then
  printf 'style-check passed:""",
    ),
    mutate.Mutation(
        "m12 the baseline multiset keeps notes that name another file: R17",
        r"""function bkey(rec) { if (parse(rec) && p_fn == "-") return keyfor(bn, base); return "" }""",
        r"""function bkey(rec) { if (parse(rec)) return keyfor(bn, base); return "" }""",
    ),
    mutate.Mutation(
        "m13 a note line that fails to key is dropped from the block set: R16 R20",
        r"""  k = ckey($0)
  if (k == "") next
""",
        r"""  k = ckey($0)
  if (k == "") { if (index($0, ": note: ")) { print "M\t" cnt }; next }
""",
    ),
    mutate.Mutation(
        "m14 a note whose column is not 1 is dropped from the block set: R2 R4",
        r"""  k = ckey($0)
  if (k == "") next
""",
        r"""  k = ckey($0)
  if (k != "" && p_col != 1) { print "M\t" cnt; next }
  if (k == "") next
""",
    ),
    mutate.Mutation(
        "m15 --no-replace-objects is dropped from the baseline read: R14",
        r"""    git --no-replace-objects -C "$sc_dir" """,
        r"""    git -C "$sc_dir" """,
    ),
    mutate.Mutation(
        "m16 the baseline grade no longer runs from the file's directory: R15",
        r"""  ( cd "$sc_dir" 2>/dev/null || exit 97; sc_run - ) <""",
        r"""  ( sc_run - ) <""",
    ),
    mutate.Mutation(
        "m17 the baseline shellcheck's exit status is ignored: R22 R23",
        r"""  case "$rc" in 0 | 1) ;; *) return 0 ;; esac
""",
        r"""  :
""",
    ),
    mutate.Mutation(
        "m18 the awk status capture is removed, so a failing awk aborts under set -e: R24",
        r"""<<<"$sc_output") && rc=0 || rc=$?
  [[ "$rc" -eq 0 ]] || return 0
""",
        r"""<<<"$sc_output")
""",
    ),
    mutate.Mutation(
        "m19 the hint fires whenever the code occurs at HEAD, consumed or not: R2",
        r"""FILENAME == bout { k = bkey($0); if (k != "") pool[k]++; next }""",
        r"""FILENAME == bout { k = bkey($0); if (k != "") { pool[k]++; split(k, kp, "\t"); left[kp[1]] = 1 }; next }""",
    ),
    mutate.Mutation(
        "m20 the block set is built from note lines only, so a non-gcc line is dropped: R21",
        r"""    blocked="${blocked}${blocked:+$'\n'}${line}"
""",
        r"""    case "$line" in *": note: "*) blocked="${blocked}${blocked:+$'\n'}${line}" ;; esac
""",
    ),
    mutate.Mutation(
        "m21 a grep -q on a live pipe under pipefail reads its producer's SIGPIPE: R27",
        r"""  local gate="" rc=0
""",
        r"""  local gate="" rc=0
  if ! printf '%s' "$sc_output" | grep -q ': note: '; then return 0; fi
""",
    ),
    mutate.Mutation(
        "m22 an empty-array expansion under set -u aborts bash 3.2, and the EXIT trap reads as exit 0: the [/bin/bash] rows that expect 2",
        r"""  printf '%s\n' "$sc_output" >"$sc_tmp/cur.out" && rc=0 || rc=$?
""",
        r"""  local sc_empty=()
  for sc_x in "${sc_empty[@]}"; do :; done
  printf '%s\n' "$sc_output" >"$sc_tmp/cur.out" && rc=0 || rc=$?
""",
    ),
    mutate.Mutation(
        "m23 the status capture of the match-mode awk call is removed, so it aborts under set -e: R24b",
        r""""$sc_tmp/cur.out") && rc=0 || rc=$?
  [[ "$rc" -eq 0 ]] || return 0
""",
        r""""$sc_tmp/cur.out")
""",
    ),
]


def main() -> int:
    report = mutate.run(SUBJECT, SUITE, MUTATIONS, cwd=str(REPO))
    print(report.text)
    return report.rc


if __name__ == "__main__":
    sys.exit(main())

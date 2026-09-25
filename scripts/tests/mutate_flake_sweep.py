#!/usr/bin/env python3
"""Mutation campaign for scripts/flake-sweep.sh, driven by scripts/lib/mutate.py.

Deliberately NOT named `test_*`: it mutates a tracked file in place, so pytest must not collect
it. Run it on demand -- `./scripts/tests/mutate_flake_sweep.py` -- and never while editing the
subject, since the restore would clobber your edits.

flake-sweep.sh shipped without a mutation driver while 17 siblings in this directory already had
one. This closes that gap. Anchors target the POST-hardening file (2026-08-27 pass, T1-T4): T1
rewrote the aggregation from `for (r = 1; r <= completed; r++)` / `SUBSEP 1` to a `runs[]`-indexed
set baselined on the first EXECUTED run, so this driver's anchors are `runs[1]` and the `nruns`
loop bound, not the pre-rewrite shapes. Every anchor here was individually confirmed by hand
against `scripts/tests/test_flake_sweep.sh` before being folded into this table -- see the plan's
T5 section for which row each one is meant to kill.

A second hardening pass (2026-08-27, whole-branch review: two BLOCKERs and four MAJORs) followed;
this docstring deliberately states no line count for the subject -- `wc -l` is the source of
truth, and any number written here goes stale with the next edit. That pass's anchors are grouped below under their own comment,
after the original T1-T4 table. One of its own fixes, the `completed > n` invariant assertion
(BLOCKER2 part (a)), deliberately has NO anchor here: given part (b) (the distinct-run-id
`completed` computation, which IS anchored below), `completed` can never actually exceed `n`, so
a mutant that only removes part (a)'s check is INERT -- it cannot change any test's outcome, and
a "stronger assertion" written to force it red would pin nothing real. Kept in the shipped code
as insurance against a future rewrite of part (b), not because this campaign can prove it fires.

A THIRD round (2026-09-24, T1-T5: owned capture via an unlinked private fd pair, pipe-fed awks
with their own rc checks promoted to a first-class verdict arm, and collision-aware label
aggregation with a `sameset`/`rdiff`/`collided` classification the label extraction alone cannot
resolve) replaced the log-file-based capture entirely -- verdict inputs are now exactly the
subject's wait status, its captured output (fd 7/8, unlinked before the subject starts), and
run-long.sh's stamp text; nothing is ever read back from a path. Two T1-T4-era anchors had gone
stale under this round (an aggregation-awk indentation shift, and a loop-bound line that became
ambiguous once a second identical-looking loop was added nearby) and are re-anchored below, in
place, with a note at each site; nothing from the earlier rounds needed to be DELETED, since none
of their anchors sat in the log-reading code this round removed. The new rows below (grouped under
their own heading) close the gaps that round's own review found: the owned-capture unlink/fd-close
pair, the two now-distinct "not executed" branches, the four extraction paths that gained their own
rc check, the verdict arm that reads `measure_failures`, the ambiguous/FAIL precedence in the
verdict chain, and three of the aggregation awk's own classification variables (`collided`,
`sameset`, `rdiff`). `run-long.sh`'s own `LC_ALL=C` fix (item LC1 in the suite) is NOT anchored
here -- it lives in a different file with its own driver (`mutate_run_long.py`); flake-sweep.sh
merely calls into it via `--stamp`.

Every row names ONE safety property and mutates what it names. A row that goes red off some
unrelated assertion raising first is a green suite wearing a red hat, so the labels are written
to be falsifiable: if the suite survives a row, the property that row names is not being tested.

This campaign is SLOW: the suite itself measured ~96s per run under this round's own campaign
(dozens of subprocess-heavy fixture rows, several spinning up their own throwaway git repos,
several driving a shimmed `awk`/`mktemp` on PATH), and the campaign is one full suite run per
entry in MUTATIONS plus the mandatory unmutated baseline -- over an hour end to end at 40-odd
rows, comfortably past the foreground tool timeout (`len(MUTATIONS)` is the count; no number is
written here to go stale). Launch this file itself through `scripts/run-long.sh`, never a
hand-rolled backgrounding wrapper, and read the verdict back with `--status`:

    scripts/run-long.sh --out <scratch-dir>/flake-mutate.log \
      --expect '^RESULT: (PASS|FAIL|ERROR) rc=[0-9]+ caught=[0-9]+ survived=[0-9]+ timedout=[0-9]+ total=[0-9]+' \
      -- scripts/tests/mutate_flake_sweep.py
    scripts/run-long.sh --status <scratch-dir>/flake-mutate.log

`<scratch-dir>` is the caller's own scratch/session directory (e.g. this repo's convention of a
per-session directory under `/private/tmp`), never a fixed, shared `/tmp` path -- this suite's own
fixtures already carry a comment about a shared/real TMPDIR accumulating stray artifacts across
runs, and the campaign's own log is no exception.

The `--expect` pattern matches the SHAPE of `mutate.py`'s own `_verdict()` line -- `RESULT: {status}
rc={rc} caught={caught} survived={survived} timedout={timedout} total={total}` (see `_verdict()` in
scripts/lib/mutate.py), where `status` is one of `STATUSES = ("PASS", "FAIL", "ERROR")` and every
other field is a bare integer (no `/`, unlike flake-sweep.sh's OWN `RESULT:` line, which this
campaign's subject prints and which this pattern is not matching) -- not a passing value: an
`--expect` written against the PASSING value (e.g. `survived=0`) would report INDETERMINATE on a
genuine FAIL, which is worse than useless -- it hides the very outcome the campaign exists to
surface. Verified against a real run's artifact: `RESULT: PASS rc=0 caught=38 survived=0
timedout=0 total=38` matches; the pattern is not written from that one passing line, since the
allowed status alternation and the `rc=` field both come from reading `_verdict()` and its two
call sites (`ERROR` return paths and the final PASS/FAIL return), not from the one observed run.

If that run dies (killed, or the harness itself crashes before its `finally` restores the
subject), verify scripts/flake-sweep.sh is back to its pre-mutation content BY DIGEST --
`git diff --quiet -- scripts/flake-sweep.sh` -- before doing anything else; the library's own
backup-sidecar recovery (`scripts/flake-sweep.sh.mutate-backup`) is the documented remedy if it
is not.

The repo root is derived from __file__, which means invoking this through a symlinked
`~/.claude/scripts/tests/` would resolve into PRODUCTION's tree and mutate that instead. Run it
from the working copy you intend to grade.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "scripts" / "lib"))
import mutate  # noqa: E402

SUBJECT = REPO / "scripts" / "flake-sweep.sh"
SUITE = ["bash", str(REPO / "scripts" / "tests" / "test_flake_sweep.sh")]

MUTATIONS = [
    mutate.Mutation(
        # RE-ANCHORED 2026-08-27: the T1-era anchor here matched
        # `completed="$(awk 'END { print NR + 0 }' "$exits_tsv")"`, which BLOCKER2(b) below
        # replaced with a distinct-run-id-in-[1,n] computation -- that old literal no longer
        # exists in the file at all (0 matches), so per CLAUDE.md this is re-anchored onto the
        # NEW shape rather than deleted. Original purpose preserved (a mid-sweep skip must not
        # let the denominator re-inflate toward n, skA), tested here as: does the upper bound
        # `r <= n` actually matter, distinct from BLOCKER2(b)'s own anchor (full revert to raw
        # NR) below -- dropping ONLY the upper bound still dedupes true repeats (so a mid-skip
        # subject like skA is unaffected) but re-admits an out-of-range foreign id (item 12's
        # id=999 half) past the planned n.
        "BLOCKER2 revert to a MAX INDEX: `completed` is assigned the loop variable instead of incremented, so a run skipped mid-sweep is re-absorbed and a later success carries the denominator back to n (skA-skD, CC)",
        "  completed=$((completed + 1))",
        "  completed=$i",
    ),
    mutate.Mutation(
        # RE-ANCHORED 2026-09-24: the 2026-08-27-era anchor had 8 leading spaces; the aggregation
        # awk's `END` block is indented 6 spaces in the current file (this line sits directly
        # under `END {`, not nested one level deeper as it was when this anchor was first
        # written) -- the old 8-space literal matched 0 times. Same property (skC: the baseline
        # must be the first EXECUTED run, not a hardcoded literal 1).
        "the aggregation baseline goes back to literal run 1 instead of the first EXECUTED run (skC: run 1 was skipped, so the baseline reads an empty tuple and manufactures variance)",
        "      base = runs[1]",
        "      base = 1",
    ),
    mutate.Mutation(
        # RE-ANCHORED 2026-09-24: this round added a second, textually-identical-looking `for (j
        # = 1; j <= nruns; j++) {` line a few lines below (inside the new `sameset` collision
        # loop), so the old single-line anchor now resolves 2x -- an AMBIGUOUS anchor mutate.py
        # refuses to apply. Disambiguated by including the next line (`c = l SUBSEP runs[j]`),
        # which only the BASELINE-comparison loop this row targets carries. Same property (skF).
        "the runs[] comparison loop drops the LAST executed run, so a subject varying only on its final invocation (skF) is never compared against the baseline",
        "        for (j = 1; j <= nruns; j++) {\n          c = l SUBSEP runs[j]",
        "        for (j = 1; j < nruns; j++) {\n          c = l SUBSEP runs[j]",
    ),
    mutate.Mutation(
        # RE-ANCHORED 2026-09-24: the per-run stamp write gained its own `2> /dev/null \\\n  ||
        # artifact_failures=...` error-handling tail this round (every artifact write now reports
        # its own failure rather than being silently best-effort) -- the anchor is widened to
        # include it so the deletion does not leave that tail as orphaned, syntactically-broken
        # trailing text after the replacement (`bash -n` confirmed clean on the mutated file).
        "the closing stamp after the loop is deleted, so a mutation caused by the FINAL run goes unobserved again (T2's tracked-file-on-last-run row)",
        'stamp_out="$("$run_long" --stamp 2> /dev/null)"\nstamp_rc=$?\nprintf \'%d\\t%s\\t%d\\n\' "$((n + 1))" "$stamp_out" "$stamp_rc" >> "$stamps_tsv" 2> /dev/null \\\n  || artifact_failures=$((artifact_failures + 1))',
        ": # closing stamp deleted",
    ),
    mutate.Mutation(
        # RE-ANCHORED 2026-08-27: MAJOR1's raw-label tracking renamed the awk variable this strip
        # targets from `label` to `raw` (the flatten now runs BEFORE the diagnostic strip derives
        # `label` from it, not after) -- the old literal `gsub(/\t/, " ", label)` no longer
        # exists (0 matches). Same property, new variable name.
        "labels stop being tab-flattened before use, so a tab-bearing label collides with a real row's TSV fields and can erase a genuine flip (T3)",
        '      gsub(/\\t/, " ", raw)',
        "      :",
    ),
    mutate.Mutation(
        'a stamp COMMAND that failed to even launch is reported as subject="stable" instead of UNVERIFIABLE (u1)',
        'subject="UNVERIFIABLE"',
        'subject="stable"',
    ),
    mutate.Mutation(
        "the bad-stamp-count gate never fires, so a failed stamp command is never even classified as UNVERIFIABLE (u1)",
        'if [[ "$bad_stamp_count" -gt 0 ]]; then',
        "if false; then",
    ),
    mutate.Mutation(
        # RE-ANCHORED 2026-08-27: MAJOR3 added a third sentinel (`"$subject" == "unknown"`) to
        # this same condition, so the old two-clause literal no longer exists (0 matches). Same
        # property (dropping UNVERIFIABLE specifically), distinct from the MAJOR3 anchor below
        # (which drops "unknown" specifically) even though both mutate this one line.
        "UNVERIFIABLE is dropped from the FAIL condition, so a stamp command that could not even launch is reported RESULT: PASS (u1)",
        'elif [[ "$subject" == "MOVED" || "$subject" == "UNVERIFIABLE" || "$subject" == "unknown" || "$total_varying" -gt 0 ]]; then',
        'elif [[ "$subject" == "MOVED" || "$subject" == "unknown" || "$total_varying" -gt 0 ]]; then',
    ),
    mutate.Mutation(
        'the literal subject="stable" is renamed to "BOGUS" -- a weaker check asserting only RESULT: PASS would not catch this (item 1f, item 8)',
        'subject="stable"',
        'subject="BOGUS"',
    ),
    mutate.Mutation(
        "--artifact-dir is parsed and then discarded, so a caller-supplied directory is silently replaced by the mktemp default (art4)",
        'artifact_dir="$2"',
        'artifact_dir=""',
    ),
    mutate.Mutation(
        'the trailing " (...)" diagnostic strip is deleted, so a diagnostic that varies run to run makes an otherwise-stable label read as varying (t6p)',
        "      sub(/ \\(.*\\)$/, \"\", label)     # the suites'\\'' trailing diagnostic",
        "      :  # paren strip deleted",
    ),
    mutate.Mutation(
        'the trailing " — ..." diagnostic strip is deleted, so a diagnostic that varies run to run makes an otherwise-stable label read as varying (t6d)',
        '      sub(/ — .*$/, "", label)       # the audit.sh trailing diagnostic',
        "      :  # em-dash strip deleted",
    ),
    mutate.Mutation(
        "-n stops being validated as an integer, so a non-numeric value reaches the loop instead of a usage error (usage-n)",
        '[[ "$n" =~ ^[0-9]+$ ]] || die_usage "-n must be a non-negative integer: $n"',
        ":",
    ),
    mutate.Mutation(
        'the no-command-given check is removed, so an empty command runs "$@" with nothing in it instead of a usage error (usage-nocmd)',
        "[[ $# -ge 1 ]] || die_usage 'no command given (put it after --)'",
        ":",
    ),
    mutate.Mutation(
        "the -h/--help case arm is removed, so both flags fall through to the unrecognized-argument usage error instead of printing usage and exiting 0 (usage-h, usage-help)",
        "    -h | --help)\n      usage\n      exit 0\n      ;;",
        "",
    ),
    mutate.Mutation(
        # RE-ANCHORED 2026-08-28: the previous anchor (`if [[ -s "$exits_tsv" ]]; then`) targeted
        # the file-based read-back this same BLOCKER fix removed -- exit_varying is now computed
        # from `exit_codes`, this process's own in-loop record, never re-read from exits.tsv. The
        # surviving property is identical (exit-code aggregation gated on "did this process record
        # any completed runs"); re-anchored onto the memory-based guard.
        "exit-code aggregation is disabled outright, so a rowless subject with an alternating exit code reads as stable (item 9, item 8)",
        'if [[ "${#exit_codes[@]}" -gt 0 ]]; then',
        "if false; then",
    ),
    mutate.Mutation(
        "the INCOMPLETE-does-not-retract-variance NOTE stops printing, silently re-opening the dismissal path a clean re-run could paper over a found flake with (skD)",
        'if [[ "$verdict" == "INCOMPLETE" && "$total_varying" -gt 0 ]]; then',
        "if false; then",
    ),
    # ---- 2026-08-27 whole-branch review: two BLOCKERs and four MAJORs (see the docstring) ----
    mutate.Mutation(
        "BLOCKER1 revert: every completed run exiting 126/127 (the shell's exec-failure codes) no longer forces INDETERMINATE, so a subject with a bad shebang reads as an ordinary constant-rc PASS again (item 11)",
        'if [[ "$completed" -gt 0 && "$execfail_count" -eq "$completed" ]]; then\n  execfail_all=1\nfi',
        'if [[ "$completed" -gt 0 && "$execfail_count" -eq "$completed" ]]; then\n  execfail_all=0\nfi',
    ),
    mutate.Mutation(
        # RE-ANCHORED 2026-08-27: the previous anchor targeted a `completed` derivation that read
        # exits.tsv back; that whole block was replaced by a process-local counter, so the old
        # literal no longer exists (0 matches). Re-anchored onto the surviving half of the same
        # property -- the aggregation's run set must also be this process's own, not re-read from
        # the shared artifact. Killed by EXT, which plants a foreign run id in exits.tsv.
        "BLOCKER2 revert to a SHARED run set: the aggregation reads its executed-run ids back from "
        "exits.tsv instead of this process's own executed_run_ids, so a foreign row in that file "
        "re-enters the variance baseline (EXT)",
        'runs_str="${executed_run_ids[*]}"',
        "runs_str=\"$(cut -f1 \"$exits_tsv\" | tr '\\n' ' ')\"",
    ),
    mutate.Mutation(
        "BLOCKER2(c) revert: a non-empty --artifact-dir is no longer refused, re-opening the shared/reused-directory hazard --force exists to gate (item 12d-f)",
        'if [[ "$force" -ne 1 ]] && [[ -n "$(ls -A -- "$artifact_dir" 2> /dev/null)" ]]; then',
        "if false; then",
    ),
    mutate.Mutation(
        "MAJOR1 revert: a merged label key's distinct raw texts stop being reported, so a clean verdict over a merge (two constituents flipping oppositely into a constant tuple) is silently unqualified again (M1)",
        'if [[ "${#merged_lines[@]}" -gt 0 ]]; then\n  for m in "${merged_lines[@]}"; do\n    printf \'MERGED: %s\\n\' "$m"\n  done\nfi',
        'if false; then\n  for m in "${merged_lines[@]}"; do\n    printf \'MERGED: %s\\n\' "$m"\n  done\nfi',
    ),
    # MAJOR3's three anchors were REMOVED 2026-08-28, not weakened. Measured: once drift became a
    # memory computation over `stamp_vals`, `subject="unknown"` stopped being reachable in the sweep
    # path at all -- `stamp_vals` gains an entry once per loop iteration BEFORE the skip guard, plus
    # the closing stamp, and `-n >= 2` is enforced, so the array is never empty and the initialiser
    # is always overwritten. Both former triggers (an unwritable stamps.tsv; the subject deleting the
    # artifact dir) now correctly report `subject=stable`, because an unrelated permission problem on
    # a DEBUG artifact has no bearing on an independently computed measurement.
    #
    # So the three mutants named the FAIL-condition membership and the case arm's printf -- code that
    # can no longer change any outcome. Per CLAUDE.md, a survivor over inert code is a signal to
    # delete the code or drop the anchor, never to write a test around it: a row built there would
    # pass forever and pin nothing, and three permanent SURVIVED lines would make this campaign
    # report FAIL in perpetuity, which is a gate nobody reads.
    #
    # The arm itself is KEPT as declared-defensive insurance, on the same footing as the
    # `completed > n` assertion described at the top of this file -- and, like it, carries no anchor.
    mutate.Mutation(
        "MAJOR4 revert: an --artifact-dir inside the stamped repo is no longer refused even when NOT gitignored, so the tool's own run logs can move the tree it stamps again (M4a-c)",
        'if ! git -C "$art_repo_root" check-ignore -q -- "$art_abs" 2> /dev/null; then',
        "if false; then",
    ),
    # ============== 2026-09-24 (T1-T5): owned capture, extraction rc checks, collision-aware ==============
    # ============== aggregation classification, and verdict-chain precedence ============================
    mutate.Mutation(
        # Kills R5f/R5g: the per-run label awk goes back to reading the subject's own captured
        # output from the WRITE-ONLY side-copy log file instead of `$out` (the in-memory capture
        # held since the brace group). R5f/g's fixture deletes its own run-NNN.log by path right
        # after printing a real flip -- harmless against `$out`, but this mutation re-opens the
        # exact false clean the owned-capture rewrite exists to close: a lost log means a lost row.
        'subject output is re-read from the write-only "$log" side-copy instead of the in-memory owned capture, so a subject that deletes its own run log erases a real flip again (R5f, R5g)',
        "  awk_out=\"$(printf '%s\\n' \"$out\" | awk -v run=\"$i\" -F'\\t' '",
        '  awk_out="$(printf \'%s\\n\' "$(cat "$log" 2> /dev/null)" | awk -v run="$i" -F\'\\t\' \'',
    ),
    mutate.Mutation(
        # Kills R5k, NOT R5h/i (measured: the mutant survives R5h, per Fable review -- R5h's
        # subject wipes its own $TMPDIR wholesale via `rm -rf "$TMPDIR"/*`, which unlinks the cap
        # file regardless of whether THIS line already did; deleting an already-open file changes
        # nothing about the fds holding it. R5k's subject instead guesses the cap file's PATH and
        # truncates it in place while it is still linked -- reachable only when this unlink never
        # ran, since post-fix the file has no path left to guess by the time the subject starts).
        "the capture file is never unlinked before the subject starts, so a subject that guesses its path can truncate it in place and erase an already-written flip (R5k)",
        '    rm -f -- "$cap" # unlinked before the subject starts: no path reaches the capture from here on',
        '    : # rm -f -- "$cap" dropped (mutation): the capture stays reachable by path for the subject',
    ),
    mutate.Mutation(
        # Kills R5m: without the redirection closing fd 7 (write side) and fd 8 (read side) for
        # the subject, both fds are inherited open, and a subject that writes directly to fd 7
        # (R5m's fixture, on its first run only) injects a fake row straight into the capture --
        # observable as an extra "leak" label with real variance, not the clean varying=0 R5m
        # requires. R5m is GREEN both before and after this task; its only job is killing this row.
        # RE-ANCHORED (round 5, F2): the subject line gained its `( ... )` subshell; same property.
        "the subject inherits fds 7 and 8 open (the capture's write and read sides), so it can write a fake row directly into its own capture (R5m)",
        '    ( "$@" ) >&7 2>&1 7>&- 8<&-',
        '    ( "$@" ) >&7 2>&1',
    ),
    mutate.Mutation(
        # Kills F2a/F2b/F2c (round 5): without the subshell, a builtin given as the subject runs in
        # the engine's own shell -- a sourced `n=2` cuts `-n 6` to runs=2/2, and `exec true` /
        # `exit 0` end the engine before any RESULT line.
        "the subject's subshell is removed, so a builtin subject (source/exec/exit) runs inside the engine's own shell and rewrites or ends it (F2a, F2b, F2c)",
        '    ( "$@" ) >&7 2>&1 7>&- 8<&-',
        '    "$@" >&7 2>&1 7>&- 8<&-',
    ),
    mutate.Mutation(
        # Kills R5l: the mktemp-failure "not executed" branch (TMPDIR points at a directory that
        # cannot hold the capture file) is supposed to leave `completed` untouched -- exactly like
        # the command -v skip above it. Incrementing it here re-inflates the denominator toward n
        # exactly as BLOCKER2's `completed=$i` revert did for the ORIGINAL skip branch, but for
        # this NEW one: R5l requires `runs=0/3`, which this mutation falsifies.
        'the mktemp-failure "not executed" branch increments `completed` anyway, so a TMPDIR that can never hold a capture file still reports runs as completed (R5l)',
        '    printf \'run %d/%d not executed (could not create a private capture file under %s)\\n\' "$i" "$n" "$cap_dir"\n    continue',
        '    printf \'run %d/%d not executed (could not create a private capture file under %s)\\n\' "$i" "$n" "$cap_dir"\n    completed=$((completed + 1))\n    continue',
    ),
    mutate.Mutation(
        # Kills the bare "M1" row added in T1's fix round 1 (scripts/tests/test_flake_sweep.sh,
        # the block starting "# M1: pin the `ran=0` branch directly", just after R5q -- named
        # plain "M1" in its own check_contains labels ('M1 (result)', 'M1 (runs)', ...), which
        # collides in NAME ONLY with the unrelated "item M1: MAJOR -- collision-aware label
        # aggregation" section earlier in the same suite; the two are unconnected. That row's
        # `mktemp` shim SUCCEEDS but names a directory that does not exist, so the brace group's
        # OWN `7>`/`8<` redirection fails to open -- the SECOND, distinct "not executed" branch,
        # one step later than R5l's (mktemp itself failing to CREATE anything). Same property,
        # different branch: `completed` must stay untouched here too.
        'the could-not-open "not executed" branch increments `completed` anyway, so a capture path whose directory vanished between mktemp and open still reports a run as completed (the bare "M1" ran=0 regression row, T1 fix round 1)',
        '    printf \'run %d/%d not executed (could not open the private capture file)\\n\' "$i" "$n"\n    continue',
        '    printf \'run %d/%d not executed (could not open the private capture file)\\n\' "$i" "$n"\n    completed=$((completed + 1))\n    continue',
    ),
    mutate.Mutation(
        # Kills R5n: the per-run label awk's own rc is captured into `extract_rc` but the check
        # that promotes a nonzero rc into `measure_failures` is deleted -- an awk shimmed to exit 2
        # on its `-v run=` signature now reads identically to a rowless subject (0 rows extracted,
        # 0 failures recorded), reproducing the exact false clean this round's BLOCKER exists to
        # close. R5n's own fixture is the one that reproduces it; the diagnostic and INDETERMINATE
        # verdict both depend on this check having fired.
        "the per-run label awk's own rc check is deleted, so a broken `awk` on PATH (exit 2 on its `-v run=` signature) reads as a clean rowless PASS instead of INDETERMINATE (R5n)",
        '  if [[ "$extract_rc" -ne 0 ]]; then\n    measure_failures=$((measure_failures + 1))\n  fi',
        "  :  # extract_rc check deleted",
    ),
    mutate.Mutation(
        # Kills R5o: same shape as R5n, one call site over (the AGGREGATION awk, keyed on its own
        # `runs_str=` -v variable, unique to this one invocation).
        "the AGGREGATION awk's own rc check is deleted, so a broken `awk` on PATH (exit 2 on `runs_str=`) reads as a clean rows=0 PASS instead of INDETERMINATE (R5o)",
        '  if [[ "$agg_rc" -ne 0 ]]; then\n    measure_failures=$((measure_failures + 1))\n  fi',
        "  :  # agg_rc check deleted",
    ),
    mutate.Mutation(
        # Kills R5p: same shape again (the DISTINCT-EXIT-CODE awk, keyed on the `print c + 0`
        # substring of its own program text since it passes no `-v` at all).
        "the DISTINCT-EXIT-CODE awk's own rc check is deleted, so a broken `awk` on PATH (exit 2 on its `print c + 0` program text) reads as a clean varying=0 PASS instead of INDETERMINATE (R5p)",
        '  if [[ "$distinct_rc" -ne 0 ]]; then\n    measure_failures=$((measure_failures + 1))\n  fi',
        "  :  # distinct_rc check deleted",
    ),
    mutate.Mutation(
        # Kills R5q: same shape again (the MERGED-LABEL awk, keyed on its own `cnt[label]++`
        # program text). Pre-round-3 this call site's failure only dropped the MERGED: visibility
        # line -- never a verdict input on its own -- so this row is the one that promotes it into
        # one, and this mutation is what proves the promotion actually happened.
        "the MERGED-LABEL awk's own rc check is deleted, so a broken `awk` on PATH (exit 2 on `cnt[label]++`) is silently absorbed into a plain PASS instead of INDETERMINATE (R5q)",
        '  if [[ "$merged_rc" -ne 0 ]]; then\n    measure_failures=$((measure_failures + 1))\n  fi',
        "  :  # merged_rc check deleted",
    ),
    mutate.Mutation(
        # Also kills R5n (a second, independent way to lose the same property): rather than
        # deleting one of the four rc checks that SET `measure_failures`, this neuters the FIRST
        # verdict-chain arm that READS it -- retargeting its guard onto `"$completed" -gt "$n"`
        # (already the very next `elif`'s condition) makes the two branches redundant and leaves
        # `measure_failures` consulted nowhere in the chain. R5n's scenario has completed == n, so
        # this arm (now checking the wrong thing) never fires either, and the verdict falls all the
        # way through to a plain PASS -- exactly the false clean R5n's diagnostic and
        # `RESULT: INDETERMINATE` assertions were written to catch, independent of which of the
        # four extractions actually failed.
        "the `measure_failures` verdict arm is neutered (retargeted onto `completed -gt n`, already the next arm's own condition), so no verdict-chain branch ever consults `measure_failures` again and a failed extraction reads as an ordinary clean PASS (R5n)",
        'if [[ "$measure_failures" -gt 0 ]]; then',
        'if [[ "$completed" -gt "$n" ]]; then',
    ),
    mutate.Mutation(
        # Kills M1g: the ambiguous-verdict arm is moved ABOVE the FAIL arm in the elif chain --
        # M1g's fixture combines M1f's genuinely ambiguous key ("x", raw-text set changes every
        # run under a constant tuple) with an UNRELATED, genuinely flipping row ("y"). The
        # unmutated chain checks total_varying (FAIL) before row_ambiguous (INDETERMINATE), so real
        # variance elsewhere always outranks an ambiguous key; reordering silently inverts that
        # precedence and reports INDETERMINATE over a sweep that also contains a real, named flip.
        "the ambiguous verdict arm is moved ABOVE the FAIL arm, so a sweep containing both a real flip and an unrelated ambiguous key reports INDETERMINATE instead of FAIL (M1g)",
        'elif [[ "$subject" == "MOVED" || "$subject" == "UNVERIFIABLE" || "$subject" == "unknown" || "$total_varying" -gt 0 ]]; then\n  verdict="FAIL"\n  result_rc=1\nelif [[ "$row_ambiguous" -gt 0 ]]; then\n  verdict="INDETERMINATE"\n  result_rc=4\n  printf \'REASON: a label received two or more distinct raw rows in one run and its raw texts changed across runs -- a changed diagnostic cannot be told apart from rows trading verdicts\\n\'',
        'elif [[ "$row_ambiguous" -gt 0 ]]; then\n  verdict="INDETERMINATE"\n  result_rc=4\n  printf \'REASON: a label received two or more distinct raw rows in one run and its raw texts changed across runs -- a changed diagnostic cannot be told apart from rows trading verdicts\\n\'\nelif [[ "$subject" == "MOVED" || "$subject" == "UNVERIFIABLE" || "$subject" == "unknown" || "$total_varying" -gt 0 ]]; then\n  verdict="FAIL"\n  result_rc=1',
    ),
    mutate.Mutation(
        # Kills M1i: `sameset` (does every run collided on this key carry the SAME set of raw
        # texts?) is forced to stay 1 by neutering the one line that can ever clear it to 0. M1i's
        # key "k" is collided only on ODD runs (both "k (a)" and "k (b)" print) and NOT on even
        # runs (only "k (a)" prints, twice, with identical text) -- a real raw-set change the
        # unmutated code correctly reports as AMBIGUOUS/INDETERMINATE. Forced-sameset skips that
        # classification and falls through to the per-raw `rdiff` compare instead, which sees "k
        # (b)" appear/disappear across runs as ordinary variance -- FAIL, not INDETERMINATE.
        #
        # This also SUBSUMES the brief's separately-listed item 8 ("the ambiguous verdict arm is
        # deleted"): with `sameset` forced to stay 1, `if (!sameset) { ambiguous++; ...; continue }`
        # -- the arm itself -- can never fire either, since its guard is always false. One row kills
        # both properties; item 8 does not get its own row.
        "`sameset` is forced to stay 1 (its only assignment to 0 is neutered), so a key collided on only SOME runs, whose raw-text set changes across runs, is never classified AMBIGUOUS (M1i)",
        "sameset = 0",
        "sameset = 1",
    ),
    mutate.Mutation(
        # Kills M1a (RESULT: FAIL) and M1d (VARYING LABELS:). The mirror image of the row above:
        # `sameset`'s INITIAL value is forced to 0 instead of 1, so a key that IS collided but
        # whose raw-text set is IDENTICAL across every run (M1a's "db connect (primary)"/"(replica)"
        # pair, present every single run) never gets to be classified sameset=true -- the loop that
        # can clear it back to 1 does not exist (only the reverse direction, sameset -> 0, is ever
        # written), so every collided key is now reported AMBIGUOUS regardless of whether its raw
        # set actually varies. M1a's real per-raw flip (caught via `rdiff`, which this mutation
        # never lets execution reach) is reported RESULT: INDETERMINATE varying=0 instead of
        # RESULT: FAIL varying=1 with "db connect" under VARYING LABELS:.
        "`sameset`'s initial value is forced to 0 (there is no assignment back to 1 to neuter, so the mutation flips the LITERAL instead), so every collided key -- even one whose raw-text set is identical every run -- is reported AMBIGUOUS instead of being checked for a real per-raw flip (M1a, M1d)",
        "sameset = 1",
        "sameset = 0",
    ),
    mutate.Mutation(
        # Kills M1j and M1a. `rdiff`'s only path to 1 is neutered, so the per-raw comparison can
        # never register a flip once a key is collided AND its raw-text set is identical across
        # runs (`sameset` true) -- exactly M1a's shape ("db connect (primary)"/"(replica)" trading
        # PASS/FAIL every run behind a constant merged tuple) and M1j's (raw "a"'s own per-run
        # tuple flips while the merged/stripped tuple AND the raw set both stay constant). Neither
        # the earlier `diff` check (constant merged tuple) nor `sameset`/`collided` (both true,
        # correctly) can see either flip without this comparison; both rows read PASS instead of
        # FAIL.
        "the per-raw `rdiff` comparison is neutered (its only assignment to 1 is deleted), so a collided key whose merged tuple and raw-text set both stay constant can hide a real per-raw flip (M1j, M1a)",
        "rdiff = 1",
        "rdiff = 0",
    ),
    mutate.Mutation(
        # Kills M1a and M1f. `collided`'s only path to 1 is neutered, so every key -- however many
        # distinct raw texts it actually saw in a run -- is treated as never-collided and the
        # `if (!collided) continue` immediately after skips straight past `sameset`/`rdiff`
        # entirely, relying solely on the earlier constant-merged-tuple `diff` check. M1a's flip
        # (constant merged tuple, real per-raw flip) and M1f's ambiguity (constant merged tuple,
        # raw-text set that legitimately changes) are both invisible to that check alone -- both
        # read a plain PASS instead of FAIL / INDETERMINATE.
        "`collided` is forced to stay 0 (its only assignment to 1 is neutered), so every key is treated as never-collided and the sameset/rdiff classification never runs at all (M1a, M1f)",
        "collided = 1",
        "collided = 0",
    ),
    mutate.Mutation(
        # Kills SK1: the per-run label-aggregation awk's SKIP tally is aliased onto the FAIL
        # counter instead of reading its own column. SK1's "flappy" label never prints FAIL at
        # all (only PASS "row one" and an alternating 1-or-2 SKIP count), so under this mutation
        # its `s` value is always `counts[l,"FAIL"]+0` == 0 regardless of the real SKIP count --
        # the per-run tuple for "flappy" reads a constant (0,0,0) and the genuine alternation
        # (0,0,1) vs (0,0,2) goes completely unmeasured.
        "the per-run SKIP tally reads the FAIL column instead of its own, so a label whose SKIP count alternates while it never FAILs reads a constant, unvarying tuple (SK1)",
        's = counts[l, "SKIP"] + 0',
        's = counts[l, "FAIL"] + 0',
    ),
    mutate.Mutation(
        # Kills NT1: the guard on the "variance was found despite the incomplete sweep" NOTE is
        # weakened from `&&` to `||`, so an INCOMPLETE verdict alone (true for skB: a stable
        # subject skipped mid-sweep, varying=0) is now sufficient to print the NOTE even though no
        # variance was ever found. skB's own assertion requires the NOTE to be ABSENT.
        "the INCOMPLETE-and-variance-found NOTE guard is weakened from && to ||, so an incomplete sweep with NO variance prints the NOTE anyway (NT1)",
        'if [[ "$verdict" == "INCOMPLETE" && "$total_varying" -gt 0 ]]; then',
        'if [[ "$verdict" == "INCOMPLETE" || "$total_varying" -gt 0 ]]; then',
    ),
    mutate.Mutation(
        # Kills n1a-n1c (round 5, F3): with the floor back at 1, `-n 1` runs once and PASSes by
        # construction -- nothing can vary within a single run.
        "the -n floor goes back to 1, so a single run -- which cannot vary -- PASSes by construction instead of being a usage error (n1a, n1b, n1c)",
        '[[ "$n" -ge 2 ]] \\',
        '[[ "$n" -ge 1 ]] \\',
    ),
]


def main() -> int:
    report = mutate.run(SUBJECT, SUITE, MUTATIONS, cwd=str(REPO))
    print(report.text)
    return report.rc


if __name__ == "__main__":
    sys.exit(main())

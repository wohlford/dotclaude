#!/usr/bin/env python3
"""Mutation campaign for scripts/run-long.sh, driven by scripts/lib/mutate.py.

Deliberately NOT named `test_*`: it mutates a tracked file in place, so pytest must not collect
it. Run it on demand — `./scripts/tests/mutate_run_long.py` — and never while editing the
subject, since the restore would clobber your edits.

Every row names ONE safety property and mutates what it names. A row that goes red off some
unrelated assertion raising first is a green suite wearing a red hat, so the labels are written
to be falsifiable: if the suite survives a row, the property that row names is not being tested.

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

SUBJECT = REPO / "scripts" / "run-long.sh"
SUITE = [str(REPO / "scripts" / "tests" / "test_run_long.sh")]

MUTATIONS = [
    mutate.Mutation(
        "the no-default-output-path rule is dropped (group 5: every run becomes a writer)",
        "[[ -n \"$out\" ]] || die 'no --out given; this tool has no default output path, by design'",
        '[[ -n "$out" ]] || out="run-long.log"',
    ),
    mutate.Mutation(
        "the clobber refusal is dropped — an existing artifact is silently destroyed",
        'if [[ -e "$out" && "$force" -ne 1 ]]; then',
        "if false; then",
    ),
    mutate.Mutation(
        "the status trailer is never written — every finished run looks killed",
        'printf "%s%d\\n" "$trailer" "$rc" >> "$art"',
        'printf "" >> "$art"',
    ),
    mutate.Mutation(
        "the launcher stops exiting 0, so its status starts masquerading as a verdict",
        "printf '      Read the verdict with: run-long.sh --status %s\\n' \"$out\"\nexit 0",
        "printf '      Read the verdict with: run-long.sh --status %s\\n' \"$out\"\nexit 1",
    ),
    mutate.Mutation(
        "a DIED run reports success — the exact false-clean this tool exists to prevent",
        '      subject_report "$art"\n      return 4',
        '      subject_report "$art"\n      return 0',
    ),
    mutate.Mutation(
        "liveness is assumed rather than checked, so a killed run reads as RUNNING forever",
        'if [[ -n "$JOB_PID" ]] && kill -0 "$JOB_PID" 2>/dev/null; then',
        'if [[ -n "$JOB_PID" ]]; then',
    ),
    mutate.Mutation(
        "--status stops distinguishing a failed run from a successful one",
        '      [[ "$JOB_RC" == "0" ]] && return 0\n      return 1',
        '      [[ "$JOB_RC" == "0" ]] && return 0\n      return 0',
    ),
    mutate.Mutation(
        "the trailing-newline guard is dropped — the trailer fuses onto the last output line",
        'if [ -n "$(tail -c 1 "$art")" ]; then printf "\\n" >> "$art"; fi',
        ":",
    ),
    mutate.Mutation(
        "the BEGIN token leaves the header, so --status can never recover the pid",
        'printf "%s pid=%d label=%s\\n" "$begin" "$$" "$tag"',
        'printf "pid=%d label=%s\\n" "$$" "$tag"',
    ),
    mutate.Mutation(
        "stderr is no longer captured into the artifact",
        '"$@" >> "$art" 2>&1',
        '"$@" >> "$art"',
    ),
    # A row for the launch/artifact race guard was here and has been REMOVED, not silenced.
    # It survived, and probing said the guard is nonetheless load-bearing: instrumenting the
    # loop shows it spends exactly 1 iteration (~20ms) on every launch, so without it the
    # launcher returns before the header is on disk. The absence is simply not observable from
    # outside — forking a `--status` process costs far longer than the window it closes, so 40/40
    # probe runs reported RUNNING with the guard removed. The row would therefore report SURVIVED
    # forever: true, and useless.
    #
    # Note this lands the OPPOSITE way from the `chmod` no-ops deleted from mutate.py. There the
    # property held by CONSTRUCTION (write_text truncates in place), so the code was pointless.
    # Here it holds only by a timing accident that a loaded machine could break, so the guard is
    # kept and the untestable row is dropped — a flagged "untestable: timing" exception.
    mutate.Mutation(
        "the recorded command line stops being argv-faithful (%q -> flat $*)",
        'for a in "$@"; do printf " %q" "$a"; done',
        'printf " %s" "$*"',
    ),
    mutate.Mutation(
        "--label stops being recorded",
        'printf "%s pid=%d label=%s\\n" "$begin" "$$" "$tag"',
        'printf "%s pid=%d\\n" "$begin" "$$"',
    ),
    # --- --wait: which states count as TERMINAL -----------------------------------------------
    #
    # The first row is the whole reason the flag exists. It is also the only row here that fails by
    # HANGING rather than by returning a wrong answer, which is why the suite waits on --wait under
    # an alarm: without that bound this mutation would hang the campaign instead of being caught.
    mutate.Mutation(
        "--wait loops on 'not DONE', so a job that was killed hangs the waiter forever",
        'while [[ "$CLASS" == "running" ]]; do',
        'while [[ "$CLASS" != "done" ]]; do',
    ),
    mutate.Mutation(
        "the wait loop is dropped — --wait degenerates to --status and returns RUNNING",
        'while [[ "$CLASS" == "running" ]]; do\n'
        '      sleep "$interval"\n'
        '      classify "$status_path"\n'
        "    done",
        ":",
    ),
    mutate.Mutation(
        "--interval accepts any junk, so a bad cadence reaches the poll loop",
        '[[ "$interval" =~ ^[1-9][0-9]*$ ]] ||\n'
        '    die "--interval needs a positive whole number of seconds: $interval"',
        ":",
    ),
    mutate.Mutation(
        "--interval is silently swallowed outside --wait instead of naming the misconception",
        '[[ "$mode" == "wait" ]] || die \'--interval applies to --wait only\'',
        ":",
    ),
    # --- the subject stamp: which TREE did this verdict grade? --------------------------------
    mutate.Mutation(
        "the stamp drops uncommitted work, so every dirty-tree edit reads as unchanged",
        '    git -C "$root" rev-parse HEAD\n'
        '    git -C "$root" diff HEAD --no-ext-diff\n'
        '    git -C "$root" status --porcelain',
        '    git -C "$root" rev-parse HEAD',
    ),
    mutate.Mutation(
        "a MOVED tree is announced as unchanged — a stale verdict reading as a current one",
        "printf 'SUBJECT: MOVED since launch — this verdict does NOT cover your current tree\\n'",
        "printf 'SUBJECT: unchanged since launch\\n'",
    ),
    mutate.Mutation(
        "the 'no subject recorded' case goes SILENT, which reads exactly like 'checked, unchanged'",
        "printf 'SUBJECT: not recorded — the launch was outside a git repo, "
        "so drift cannot be judged\\n'",
        ":",
    ),
    mutate.Mutation(
        "the header block never reaches the artifact, so no verdict can be tied to a tree or a pattern",
        'printf "%s\\n" "$header"',
        ":",
    ),
    # --- --expect: does a finished run reach a verdict at all? ---------------------------------
    mutate.Mutation(
        "the expect check never runs, so an inert finished run reads DONE again",
        '      if ! expect_satisfied "$art" "$pat"; then',
        "      if false; then",
    ),
    # THE load-bearing rows — one per LAYER of the scope boundary, because the review found the
    # false clear surviving at each one in turn.
    mutate.Mutation(
        "the expect match widens to the WHOLE artifact, so the header's own text clears an inert run",
        '  section="$(sed -n \'/^----- output -----$/,$p\' "$1" |\n'
        "    sed -e '1d' -e '/^RUN_LONG_EXIT_STATUS=[0-9][0-9]*$/d')\"",
        '  section="$(cat "$1")"',
    ),
    mutate.Mutation(
        "the harness's own marker and trailer stay in scope, so 'EXIT_STATUS=0' clears an inert run",
        "    sed -e '1d' -e '/^RUN_LONG_EXIT_STATUS=[0-9][0-9]*$/d')\"",
        '    cat)"',
    ),
    mutate.Mutation(
        "INDETERMINATE returns 0 — inertness starts reading as success",
        '      subject_report "$art"\n      return 5',
        '      subject_report "$art"\n      return 0',
    ),
    # NOT "becomes a rubber stamp" — measured, it does not. With this check gone, `--expect ''`
    # still dies rc 2 via the probe below (`grep -qE -e '' <<< ""` → rc 0 → "matches an empty
    # line"). This line decides only WHICH DIAGNOSIS the user gets, so the row is caught solely by
    # x1b's message-text assertion. Labelling it as a stamp would make the row unfalsifiable.
    mutate.Mutation(
        "the empty-pattern DIAGNOSIS is lost — '' is still refused, but named as the wrong defect",
        '  [[ -n "$expect" ]] ||\n'
        "    die '--expect needs a non-empty pattern: an empty one matches everything, "
        "so it would clear every run'",
        ":",
    ),
    mutate.Mutation(
        "the newline refusal is dropped, so a multi-line pattern corrupts the header format",
        "  [[ \"$expect\" != *$'\\n'* ]] ||\n"
        "    die '--expect cannot contain a newline: it is recorded as a single header line'",
        ":",
    ),
    mutate.Mutation(
        "the pattern never reaches the header, so the obligation stops travelling with the artifact",
        'header_block="$(printf \'%s\\n%s%s\' "$header_block" "$EXPECT_PREFIX" "$expect")"',
        ":",
    ),
    mutate.Mutation(
        "the RECORDED pattern is ignored at read time — only an explicit flag would ever apply",
        '  [[ -n "$recorded" ]] && printf \'%s\\n\' "$recorded"',
        ":",
    ),
    mutate.Mutation(
        "the read-time pattern is ignored, so it can no longer ADD a requirement",
        '  [[ -n "$expect" ]] && printf \'%s\\n\' "$expect"',
        ":",
    ),
    mutate.Mutation(
        "the empty-section guard is dropped — a here-string feeds ONE EMPTY LINE, which '^' matches",
        '  [[ -n "$section" ]] || return 1',
        ":",
    ),
    mutate.Mutation(
        "the recorded-pattern read un-scopes from the header, so job OUTPUT can manufacture one",
        '  header="$(sed -n \'1,/^----- output -----$/p\' "$art")"\n'
        '  recorded="$(sed -n "s/^${EXPECT_PREFIX}//p" <<< "$header" | head -1)"',
        '  recorded="$(sed -n "s/^${EXPECT_PREFIX}//p" "$art" | head -1)"',
    ),
    mutate.Mutation(
        "the --label newline refusal is dropped, so a label can FORGE the output marker",
        "[[ \"$label\" != *$'\\n'* ]] ||\n"
        "  die '--label cannot contain a newline: it is written into the header, "
        "where one would forge the output marker'",
        ":",
    ),
    mutate.Mutation(
        "the empty-line / uncompilable pattern probe is dropped, so '^' becomes a rubber stamp",
        '  grep -qE -e "$expect" <<< "" 2> /dev/null\n'
        "  case $? in\n"
        '    0) die "--expect matches an empty line, so it would clear every run: $expect" ;;\n'
        '    2) die "--expect is not a valid extended regular expression: $expect" ;;\n'
        "  esac",
        ":",
    ),
]


# This campaign is SLOW by nature, and needs an explicit cap rather than the derived default.
# `test_run_long.sh` proves its claims by polling — for the trailer, for the BEGIN header, for a
# terminal verdict — and every one of those loops is bounded. So a mutation that breaks the
# predicate a loop waits on does not hang the suite; it makes every single call pay its full
# retry budget, and the cost compounds across the suite. Measured 2026-07-31: the mutation that
# stops the status trailer being written takes **297s** and is genuinely CAUGHT (43 passed, 17
# failed), against a 28s unmutated baseline. Two rows behave this way, so expect this campaign to
# run for tens of minutes; launch it with `scripts/run-long.sh` and read the artifact.
# Re-measured 2026-08-09, after the suite grew from 80 to 108 rows. The two pathological rows
# roughly DOUBLED — the trailer row went 297s -> 610.0s — and the BEGIN row crossed the old 900s
# cap, coming back TIMEOUT: indeterminate, not survived, and therefore an unproven row rather than
# a failing one. Measured in isolation at a 2700s cap it is **CAUGHT at 1351.0s**, against a 26.9s
# baseline (50x).
#
# So the cap is sized from that observed worst LEGITIMATE run, not from plausibility: 3600s is
# ~2.7x it, matching the ratio the old 900s cap held against its own 297s worst case.
#
# **The cost is superlinear in suite size and is NOT fully explained.** Thirteen launches paying
# the launcher's full 250x0.02s header-wait budget accounts for ~65s of the ~1324s of overhead;
# where the rest goes has not been chased down. Two successive attempts to model it from launch
# counts were wrong, which is why this number comes from a measurement instead. **Re-measure this
# cap whenever rows are added to `test_run_long.sh`** rather than assuming headroom — and expect
# the whole campaign to run about an hour now.
SUITE_TIMEOUT_SECONDS = 3600


def main() -> int:
    report = mutate.run(
        SUBJECT, SUITE, MUTATIONS, cwd=str(REPO), timeout=SUITE_TIMEOUT_SECONDS
    )
    print(report.text)
    return report.rc


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
# Script: mutate_heavy_gate.py
# Purpose: Mutation campaign for scripts/heavy-gate.sh, driven by scripts/lib/mutate.py
# Usage: ./scripts/tests/mutate_heavy_gate.py [--r3-rate N | --only LABEL-SUBSTRING]
"""Mutation campaign for scripts/heavy-gate.sh, driven by scripts/lib/mutate.py.

Deliberately NOT named `test_*`: it mutates a tracked file in place, so pytest must not collect
it. Run it on demand, never while editing the subject (the restore would clobber your edits), and
through `scripts/run-long.sh` (the suite runs once per mutant, which outruns a foreground call; read
the verdict back with `--status`). The repo root is derived from __file__: run it from the working
copy you intend to grade, not through a symlinked `~/.claude/scripts/tests/`.

Every row names ONE safety property and mutates what it names. The comment above each row lists the
labels of `scripts/tests/test_heavy_gate.sh` it is expected to kill; that column is a PREDICTION read
off the code, and `scripts/kill-sets.py --campaign scripts/tests/mutate_heavy_gate.py` is the
authority (it also reports mutants whose kill sets are identical).

The suite runs as `env HG_STRESS=0 bash test_heavy_gate.sh`. Rows `R11s` are a 60 s stress half that
no mutant here is aimed at, so HG_STRESS=0 keeps them out of the run; `kill-sets.py` takes its row
universe from this same command, so the universe excludes `R11s` too.

`--r3-rate N` runs only the R3 mutant (the check-then-`mkdir -p` take_lock), N times, against the R3
rows alone (`HG_ONLY=R3`), through `mutate.run`, and prints `R3_KILL_RATE caught=<k>/<N>`. The race
window is narrow, so a single run proves little either way.

Hangs. Every mutant that can make a gate process wait forever lands on a row that bounds the wait
(`run_bounded`, `wait_for`, `bg_finish`), so it reads as a red label within that bound and never
runs to `SUITE_TIMEOUT_SECONDS`. The bound of each such row is named in the comment above its
mutant. A mutant that leaks a process (a heartbeat that never exits, a stage that outlives its
wrapper) is cleaned up by the row's `reap_row` and by the suite's EXIT trap, but both are keyed on
the run token, so only processes whose command line carries it are reaped. The bare `sleep`
grandchildren inside stages carry no token, and neither does the `sh` poller of the wait-deferral
mutant; such a process writes nothing and ends by itself within about 60 s (an orphan `sleep 40`
was measured still alive after the ABORTING-guard mutant's run of R7). Accepted residual: three
mutants (the heartbeat that never tests its parent, and the two that gate its cleanup on the
passed pid) keep an orphaned heartbeat alive for its row (about 16-32 s), and its check 2 then
reads the dead wrapper's pid; if another process of this user had been given that pid,
`kill -TERM "$my_ppid"` would signal it (the shipped heartbeat exits before check 2). At about 46
pid allocations per second idle, a full pid wrap takes about 36 minutes.

Deliberately NOT mutated (a mutant there would survive, or would be unsafe to run):
  * the INT and HUP trap lines: no row sends either signal;
  * `kill_stage`'s `pgid <= 1` and own-group guards: removing one lets `kill -TERM -- -1` or the
    suite's own group signal reach processes outside the run;
  * the N1 guard (`owner_line` equals LOCK_LINE) in `run_release` and in the heartbeat: remove_lock's own
    exact-line check decides every case a row builds, so no label can tell the guard from its absence;
  * the redundant `drop_holder_ticket` before `exit 75` (the EXIT trap's release does the same) and
    the second `die` of `ensure_state_dir` (the first one already fires);
  * the `<&0` of the stage launch: with job control on (`set -m`) a background stage keeps its stdin
    anyway, so removing the redirect changes nothing;
  * the rename target of `remove_lock` and its restore-failure branch: no row reaches either;
  * the VERDICT_DONE guard of `emit_verdict`: no label counts verdict lines;
  * `renew`: no suite row exercises it at all (a suite gap, filed as the backlog entry "heavy-gate suite gaps", 2026-11-05);
  * the `seam_on` gate in `seam_value`: no row sets another seam without GATE_DIR;
  * the heartbeat's `touch_ticket`: no row outlives a ticket TTL that short;
  * the `STAGE_LAUNCHING` fallback in `run_abort`: no row signals between fork and pgid record.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "scripts" / "lib"))
import mutate  # noqa: E402

SUBJECT = REPO / "scripts" / "heavy-gate.sh"
_SUITE_FILE = str(REPO / "scripts" / "tests" / "test_heavy_gate.sh")
SUITE = ["env", "HG_STRESS=0", "bash", _SUITE_FILE]
SUITE_R3 = ["env", "HG_ONLY=R3", "HG_STRESS=0", "bash", _SUITE_FILE]

MUTATIONS = [
    # --- the throttle verdict and its probe sources (rows R1, R2, R2b) ---
    # The verdict itself. Kills R1/fake1-rc0, R1/fake0-rc1 and, because every wait/run now sees a free machine as throttled, every
    # label that needs a GO. HANGS: every wait_turn waits out its row bound (run_bounded / wait_for / bg_finish in each row).
    mutate.Mutation(
        "throttled() succeeds for a free machine and fails for a throttled one (verdict inverted)",
        r'[ "$word" = THROTTLED ]',
        r'[ "$word" != THROTTLED ]',
    ),
    # One mutant per informational level; each has its own label (D0/levels-cpu, D0/levels-gpu, D0/levels-io).
    mutate.Mutation(
        "the cpu thermal level is never read",
        r'cpu=$("$P_SYSCTL_CMD" -n machdep.xcpm.cpu_thermal_level 2>/dev/null)',
        r'cpu=""',
    ),
    mutate.Mutation(
        "the gpu thermal level is never read",
        r'gpu=$("$P_SYSCTL_CMD" -n machdep.xcpm.gpu_thermal_level 2>/dev/null)',
        r'gpu=""',
    ),
    mutate.Mutation(
        "the io thermal level is never read",
        r'io=$("$P_SYSCTL_CMD" -n machdep.xcpm.io_thermal_level 2>/dev/null)',
        r'io=""',
    ),
    # Kills R2/pmset-80 and D0/pmset-speed-99: every other row has the limits at 100 or absent.
    mutate.Mutation(
        "a pmset speed limit below 100 never throttles",
        r'if [ "$v" -lt 100 ]; then hot=1; fi',
        r":",
    ),
    # Kills R2/nonnumeric-level and R2/all-missing-unmeasurable (both read the unmeasurable word); R2b/none-source stays green.
    mutate.Mutation(
        "an unmeasurable machine is reported as 'not throttled' (the word, not the verdict)",
        r'word="unmeasurable (no pmset speed limit reading)"',
        r'word="not throttled"',
    ),
    # Removes the field: kills 7 rows (D0/pmset-both-100, R14e/fake-ignored-without-gate-dir, R2b/fake-source,
    # R2b/levels-line-differs, R2b/none-source, R2b/pmset-source, R2b/pmset-source-without-levels) (the real source is
    # read from this line).
    mutate.Mutation(
        "status stops printing the source: line",
        r'''printf 'source: %s\n' "$src"''',
        r":",
    ),
    # Kills D0/high-level-pmset-100-not-throttled: a high level with pmset at 100 must read not throttled.
    mutate.Mutation(
        "levels re-added to the predicate",
        r'if [ "$v" -lt 100 ]; then hot=1; fi',
        r'if [ "$v" -lt 100 ] || [ "$(thermal_levels)" != "cpu=0 gpu=0 io=0" ]; then hot=1; fi',
    ),
    # Kills D0/pmset-scheduler-99: only the scheduler limit is below 100 there.
    mutate.Mutation(
        "CPU_Scheduler_Limit is ignored",
        r'for v in "$lim" "$sched"; do',
        r'for v in "$lim"; do',
    ),
    # Kills D0/seam-ignored-without-gate-dir: the stub pmset at 99 would throttle a gate with no GATE_DIR.
    mutate.Mutation(
        "the command seam is honoured without GATE_DIR",
        r"""if [ "$seam_on" = 1 ]; then v="${!1:-}"; fi
  if [ -z "$v" ]; then
    printf '%s\n' "$2"
    return 0
  fi
  [ -f""",
        r"""v="${!1:-}"
  if [ -z "$v" ]; then
    printf '%s\n' "$2"
    return 0
  fi
  [ -f""",
    ),
    # Kills D0/seam-nonexecutable-exits-2 and D0/seam-nonexecutable-message.
    mutate.Mutation(
        "a non-executable seam path is accepted",
        r'[ -f "$v" ] && [ -x "$v" ] || die "$1 is not an executable file: [$v]"',
        r":",
    ),
    # Kills D0/pmset-both-100: a parsed reading must carry the pmset source.
    mutate.Mutation(
        "a pmset reading is labelled none",
        r"""    word="not throttled"
    src=pmset""",
        r"""    word="not throttled"
    src=none""",
    ),
    # --- turn-taking: lock, head and order (rows R3, R4, R4h, R12) ---
    # RATE mutant (the -d/mkdir window is narrow): kills R3/one-winner-per-round (and R3/all-rounds-ran only if a loser errors);
    # use --r3-rate N for the kill rate. No hang: a double winner still returns.
    mutate.Mutation(
        "take_lock is check-then-mkdir -p, so two takers can both win",
        r'mkdir "$L" 2>/dev/null || return 1',
        r'if [ -d "$L" ]; then return 1; fi; mkdir -p "$L" 2>/dev/null || return 1',
    ),
    # Kills R4h/non-head-does-not-take-lock (R4/start-order-equals-join-order may flip by luck and is not counted);
    # R15/proto-ticket-honoured also goes red (a non-head waiter takes the lock past the prototype's ticket).
    # R4h/oldest-takes-lock-despite-newer-tickets stays green. No hang (a non-head takes the lock at once).
    mutate.Mutation(
        "the head-of-queue rule is dropped: any ticket may take a free lock",
        r'if [ "$(head_name)" = "$name" ] && ! throttled; then',
        r"if ! throttled; then",
    ),
    # Kills R12/throttled-head-waits only (the stage starts while throttled); R12/flip-to-0-go-within-2-polls and
    # R12/running-stage-unaffected-by-flip stay green.
    mutate.Mutation(
        "the throttle is not consulted when taking the lock",
        r'if [ "$(head_name)" = "$name" ] && ! throttled; then',
        r'if [ "$(head_name)" = "$name" ]; then',
    ),
    # Newest ticket first: kills 6 rows (R15/proto-ticket-honoured, R4/no-overlap, R4/start-order-equals-join-order,
    # R4h/non-head-does-not-take-lock, R4h/oldest-takes-lock-despite-newer-tickets, R8f1/lock-removed).
    # HANGS: R4 waiters (bg_finish 30 each), R8f1 (run_bounded 9).
    mutate.Mutation(
        "the ticket order is reversed (newest ticket is the head)",
        r"done | LC_ALL=C sort",
        r"done | LC_ALL=C sort -r",
    ),
    # --- the run lifecycle: signals, group, verdict (rows R5, R6, R7, R13) ---
    # TERM is only handled once the stage ends (an external foreground poll defers the trap, as a foreground stage would):
    # kills 7 rows (R5/exit-143-promptly, R5/last-line-verdict-143, R5/state-clean, R7/ii-lock-ticket-gone,
    # R7/ii-plain-term-no-survivors, R7/iii-orphan-ignoring-stage-lock-ticket-gone,
    # R7/iii-orphan-ignoring-stage-no-survivors); R5/exit-143-promptly reports 124 (bg_finish 12).
    # BOUND: R5's bg_finish 12 (the row then kills the wrapper and reap_row clears the stage).
    mutate.Mutation(
        "the wait for the stage is a foreground poll, so a TERM is deferred until the stage ends",
        r'wait "$stage_pid" || rc=$?',
        r"""sh -c 'while kill -0 "$1" 2>/dev/null; do sleep 0.2; done' _ "$stage_pid" </dev/null
  wait "$stage_pid" || rc=$?""",
    ),
    # The stage shares the wrapper's group, so the group kill no longer reaches it: kills 27 rows (R5/no-survivors,
    # R7/ii-plain-term-no-survivors, R7/iii-orphan-ignoring-stage-no-survivors, R8/stage-gone, R8b/holder-ticket-gone,
    # R8b/lock-kept-at-half-stale, R8b/lock-removed-after-stale, R8b/second-go-within-bound, R8b/term-seen-lock-present,
    # R8d3/alive-at-grace-minus-slack, R8d3/gone-by-bound, R8e/first-wrapper-143, R8e/first-wrapper-verdict-line,
    # R8e/lock-handed-on, R8e/no-overlap-in-log, R8e/stage-killed, R8h/i-holder-ticket-gone, R8h/i-lock-gone-after-bound,
    # R8h/i-no-survivors, R8h/i-no-trash, R8h/i-not-before-run-stale, R8h/i-second-go, R8h/ii-lock-gone,
    # R8h/ii-no-survivors, R8h/ii-no-trash, R8h/ii-second-go, R8h/kill-stage-found-nothing-logged). BOUND: wait_for 3 / 6
    # / 8 / 10 in those rows.
    mutate.Mutation(
        "the stage is launched without job control (no group of its own)",
        r"set -m",
        r":",
    ),
    # Kills R5/last-line-verdict-143, R5/term-during-prune-prompt, R6/expect-satisfied, R13/last-line-75, R13/cmd-never-ran,
    # R8e/first-wrapper-verdict-line, R16/launcher-line-runs-done-rc0, R16/launcher-expect-satisfied.
    mutate.Mutation(
        "the HEAVY_GATE_DONE verdict line is never printed",
        r"""printf 'HEAVY_GATE_DONE %s rc=%s\n' "$1" "$2" >&2""",
        r":",
    ),
    # The heartbeat no longer TERMs the wrapper when the wrapper's parent changes: kills 4 rows (R7/ii-lock-ticket-gone,
    # R7/ii-plain-term-no-survivors, R7/iii-orphan-ignoring-stage-lock-ticket-gone,
    # R7/iii-orphan-ignoring-stage-no-survivors); R7/ii-no-trailer stays green.
    # BOUND: wait_for 6 (case ii) and wait_for 10 (case iii).
    mutate.Mutation(
        "the heartbeat does not watch the wrapper's parent (check 2)",
        r'if [ "$w_cur" = 1 ] || [ "$w_cur" != "$w_start" ]; then kill -TERM "$my_ppid" 2>/dev/null; fi',
        r":",
    ),
    # A TERM kills the wrapper with no cleanup and no verdict: kills 8 rows (R5/last-line-verdict-143, R5/no-survivors,
    # R5/term-during-prune-prompt, R7/i-group-term-no-survivors, R7/ii-plain-term-no-survivors,
    # R7/iii-orphan-ignoring-stage-no-survivors, R8h/ii-lock-gone, R8h/ii-no-trash).
    # (The INT and HUP trap lines are NOT mutated: no row sends either signal, so a mutant there would survive.)
    mutate.Mutation(
        "the TERM trap is gone (the wrapper dies without release or verdict)",
        r"trap 'run_abort 15' TERM",
        r":",
    ),
    # --- the heartbeat B (rows R8, R8c, R8d3, R8o) ---
    # B never notices the wrapper's death and never exits: kills 7 rows (R8/heartbeat-gone, R8/second-run-holds-lock,
    # R8/stage-gone, R8c/iii-parent-killed-passed-live-cleanup, R8d3/alive-at-grace-minus-slack, R8d3/gone-by-bound,
    # R8o/beat-ticket-before-lock). R8e/* and R7/iii-* stay green. BOUND: wait_for 8 in R8 and R8c; reap_row stops the
    # leaked B.
    mutate.Mutation(
        "the heartbeat never tests its own parent (no orphan detection)",
        r'if is_num "$cur_ppid" && { [ "$cur_ppid" = 1 ] || [ "$cur_ppid" != "$my_ppid" ]; }; then',
        r"if false; then",
    ),
    # The stage dies but the lock stays: kills 3 rows (R8/second-run-holds-lock,
    # R8c/iii-parent-killed-passed-live-cleanup, R8o/beat-ticket-before-lock); R8o/beat-ticket-before-lock dies because
    # the probe never runs.
    mutate.Mutation(
        "the heartbeat's own cleanup does not remove the lock",
        r'remove_lock "$mine"',
        r":",
    ),
    # The lock is freed but the stage lives: kills R8/stage-gone, R8c/iii-parent-killed-passed-live-cleanup, R8d3/*.
    mutate.Mutation(
        "the heartbeat's own cleanup does not stop the stage",
        r'kill_stage "$pgid" "$lstart"',
        r":",
    ),
    # Cleanup needs the passed WPID to be dead as well as the parent changed: kills R8c/iii-parent-killed-passed-live-cleanup only.
    mutate.Mutation(
        "the heartbeat cleans up only when the parent changed AND the passed pid is dead",
        r'if is_num "$cur_ppid" && { [ "$cur_ppid" = 1 ] || [ "$cur_ppid" != "$my_ppid" ]; }; then',
        r'if is_num "$cur_ppid" && { [ "$cur_ppid" = 1 ] || [ "$cur_ppid" != "$my_ppid" ]; } && ! kill -0 "$wpid" 2>/dev/null; then',
    ),
    # Cleanup keyed on the passed pid alone: kills R8c/ii-live-parent-dead-passed-no-cleanup and
    # R8c/iii-parent-killed-passed-live-cleanup (R8c/i stays green).
    mutate.Mutation(
        "the heartbeat cleans up whenever the passed pid is dead, whatever its parent does",
        r'if is_num "$cur_ppid" && { [ "$cur_ppid" = 1 ] || [ "$cur_ppid" != "$my_ppid" ]; }; then',
        r'if ! kill -0 "$wpid" 2>/dev/null; then',
    ),
    # The lock is never renewed while the stage runs: kills R8c/i-live-parent-live-unrelated-no-cleanup and
    # R8c/ii-live-parent-dead-passed-no-cleanup (touched=yes).
    mutate.Mutation(
        "the heartbeat stops touching the lock's owner file",
        r'if [ -n "$mine" ] && [ "$(owner_line)" = "$mine" ]; then touch -c "$L/owner" 2>/dev/null || true; fi',
        r":",
    ),
    # --- stale locks, rule b (rows R8b, R8e, R8f*, R8g, R8h) ---
    # The whole of rule 1(b): kills 32 rows (R14f/fresh-lock-not-reported-stale, R14f/reports-stale-1b,
    # R14f/sentinel-alive, R5/term-during-prune-prompt, R8b/holder-ticket-gone, R8b/lock-kept-at-half-stale,
    # R8b/lock-removed-after-stale, R8b/second-go-within-bound, R8b/stage-gone-before-go, R8b/term-seen-lock-present,
    # R8e/first-wrapper-143, R8e/first-wrapper-verdict-line, R8e/late-tick-keeps-lock, R8e/lock-handed-on,
    # R8e/no-overlap-in-log, R8e/stage-killed, R8f1/lock-removed, R8f1/ticket-kept-when-name-differs,
    # R8f1/unrelated-not-killed, R8f2/group-killed, R8f3/killed-before-lock-removed, R8g/live-waiter-reaches-go,
    # R8g/live-waiter-ticket-present, R8h/i-holder-ticket-gone, R8h/i-lock-gone-after-bound, R8h/i-no-trash,
    # R8h/i-not-before-run-stale, R8h/i-second-go, R8h/ii-lock-gone, R8h/ii-no-trash, R8h/ii-second-go,
    # R8h/kill-stage-found-nothing-logged). BOUND: each row's run_bounded / wait_for.
    mutate.Mutation(
        "rule 1(b) is removed: a run-mode lock is never stale",
        r'if [ "$age" -gt "$((10#$P_RUN_STALE))" ]; then',
        r"if false; then",
    ),
    # ORDER (G3): the stage is still killed and gone before the next GO (R8b/stage-gone-before-go and the R8e labels stay
    # green) but the lock is already removed when it dies, and the logged stage word reads none. Kills
    # R8b/term-seen-lock-present, R8f3/killed-before-lock-removed, R8f1/unrelated-not-killed, R8f2/group-killed. The handle is
    # read into stale_pgid and stale_lstart before the block, so the moved call still kills; the set is a strict subset of the
    # deletion mutant's.
    mutate.Mutation(
        "rule 1(b) kills the stage AFTER it removes the lock",
        r'''kill_stage "$stale_pgid" "$stale_lstart"
  drop_holder_ticket "$stale_name" "$stale_pid"
  remove_lock ""''',
        r'''drop_holder_ticket "$stale_name" "$stale_pid"
  remove_lock ""
  kill_stage "$stale_pgid" "$stale_lstart"''',
    ),
    # Deletion of the call: kills 10 rows (R8b/stage-gone-before-go, R8b/term-seen-lock-present, R8e/first-wrapper-143,
    # R8e/first-wrapper-verdict-line, R8e/lock-handed-on, R8e/no-overlap-in-log, R8e/stage-killed,
    # R8f1/unrelated-not-killed, R8f2/group-killed, R8f3/killed-before-lock-removed). BOUND: R8e bg_finish 20.
    # R8f3/killed-before-lock-removed. BOUND: R8e bg_finish 20.
    mutate.Mutation(
        "rule 1(b) never stops the stage it removes the lock of",
        r'kill_stage "$stale_pgid" "$stale_lstart"',
        r":",
    ),
    # Every owner is treated as run mode: kills R8b/wait-mode-owner-not-removed, R9/wait-lock-stale-removed,
    # R15/two-field-owner-not-run-mode.
    mutate.Mutation(
        "the owner MODE field is ignored (every lock is run mode)",
        r"mode=$(owner_field 4)",
        r"mode=run",
    ),
    # An absent mode (the prototype's two-field owner) reads as run: kills 2 rows (R15/two-field-owner-not-run-mode,
    # R5/term-during-prune-prompt).
    mutate.Mutation(
        "an owner line with no mode field is treated as run mode",
        r'if [ "$mode" = run ]; then',
        r'if [ "$mode" != wait ]; then',
    ),
    # The holder's ticket stays: kills 11 rows (R8b/holder-ticket-gone, R8b/second-go-within-bound,
    # R8b/stage-gone-before-go, R8f1/lock-removed, R8f1/ticket-kept-when-name-differs, R8h/i-holder-ticket-gone,
    # R8h/i-no-trash, R8h/i-second-go, R8h/ii-lock-gone, R8h/ii-no-trash, R8h/ii-second-go). BOUND: R8b wait_for 14, R8h
    # wait_for 14 and 8.
    # BOUND: R8b wait_for 14, R8h wait_for 14 and 8.
    mutate.Mutation(
        "rule 1(b) leaves the dead holder's ticket in the queue",
        r'drop_holder_ticket "$stale_name" "$stale_pid"',
        r":",
    ),
    # The removal waits for a stage to have been found: kills the labels whose stage word is none or reused and that read the
    # removal: R8h/* except i-no-survivors and ii-no-survivors, R8g/*, R8f1/lock-removed and R8f1/unrelated-not-killed.
    # R8f1/ticket-kept-when-name-differs stays green (the ticket removal still runs).
    # BOUND: R8g wait_for 12 and bg_finish 15, R8h wait_for 14 / 8.
    mutate.Mutation(
        "rule 1(b) removes the lock only when the stage was found and killed",
        r"""drop_holder_ticket "$stale_name" "$stale_pid"
  remove_lock ""
}""",
        r"""drop_holder_ticket "$stale_name" "$stale_pid"
  if [ "$STAGE_RESULT" = killed ]; then remove_lock ""; fi
}""",
    ),
    # Kills R8f1/unrelated-not-killed, R8f2/group-killed and R8f3/killed-before-lock-removed (the logged stage word).
    # R8h/kill-stage-found-nothing-logged stays green (it expects none).
    mutate.Mutation(
        "the removal event never carries the stage result",
        r'if [ "$rule" = b ]; then stage_word=$STAGE_RESULT; fi',
        r":",
    ),
    # --- kill_stage (rows R7 iii, R8d3, R8f1, R8f2, R8f3) ---
    # The pid-reuse check loses its start time: kills R8f1/unrelated-not-killed only.
    mutate.Mutation(
        "kill_stage no longer compares the process start time",
        r'if [ "$cur" = "$lstart" ] && [ "$curpg" = "$pgid" ]; then',
        r'if [ "$curpg" = "$pgid" ]; then',
    ),
    # Kills R8f2/group-killed only (a leader that is gone while a member lives).
    mutate.Mutation(
        "kill_stage ignores a group whose leader is gone",
        r'elif pgrep -g "$pgid" >/dev/null 2>&1; then',
        r"elif false; then",
    ),
    # Kills R8f3/lstart-equal-across-env and R8f3/killed-before-lock-removed.
    mutate.Mutation(
        "lstart_of is read under the caller's locale and time zone",
        r"""LC_ALL=C TZ=UTC ps -o lstart= -p "$1" | tr -s ' '""",
        r"""ps -o lstart= -p "$1" | tr -s ' '""",
    ),
    # The KILL step after the grace is gone: kills R8d3/alive-at-grace-minus-slack (later-gone), R8d3/gone-by-bound and
    # R7/iii-orphan-ignoring-stage-no-survivors. BOUND: R8d3 wait_for 16, R7 wait_for 10.
    mutate.Mutation(
        "the KILL step after the grace is removed",
        r'kill -KILL -- "-$pgid" 2>/dev/null',
        r":",
    ),
    # The grace is one second whatever GATE_GRACE says: kills 3 rows (R8d3/alive-at-grace-minus-slack, R8h/ii-lock-gone,
    # R8h/ii-no-trash).
    mutate.Mutation(
        "the kill_stage grace deadline ignores GATE_GRACE",
        r"deadline=$(($(now) + 10#$P_GRACE + 1))",
        r"deadline=$(($(now) + 1))",
    ),
    # The shipped default drifts from the help text: kills 2 rows (R8d2/help-tick-grace-equal-params,
    # R8d2/params-defaults); R8d3/alive-at-grace-minus-slack stays green.
    mutate.Mutation(
        "the GATE_GRACE default is shortened and the help text still says 10",
        r"P_GRACE=$(seam_value GATE_GRACE 10) || exit 2",
        r"P_GRACE=$(seam_value GATE_GRACE 5) || exit 2",
    ),
    # --- tickets (rows R8f1, R8g, R9, R13) ---
    # Ticket removal keyed on the name alone: kills R8g/live-waiter-ticket-present.
    mutate.Mutation(
        "a holder's ticket is removed by name alone (the waiter's own ticket goes with it)",
        r'if [ -n "$t" ] && [ "${t#*-}" = "$1" ] && [ "$(ticket_pid "$t")" = "$2" ]; then rm -f "$Q/$t"; fi',
        r'if [ -n "$t" ] && [ "${t#*-}" = "$1" ]; then rm -f "$Q/$t"; fi',
    ),
    # Ticket removal keyed on the content alone: kills R8f1/ticket-kept-when-name-differs only.
    mutate.Mutation(
        "a holder's ticket is removed by content alone (another name with the same pid goes too)",
        r'if [ -n "$t" ] && [ "${t#*-}" = "$1" ] && [ "$(ticket_pid "$t")" = "$2" ]; then rm -f "$Q/$t"; fi',
        r'if [ -n "$t" ] && [ "$(ticket_pid "$t")" = "$2" ]; then rm -f "$Q/$t"; fi',
    ),
    # Kills R9/ticket-stale-removed, R9/ticket-fresh-kept, R9/wait-lock-fresh-kept (each reads the 10 s old ticket).
    mutate.Mutation(
        "the ticket TTL comparison is scaled 10x",
        r'if [ "$tk_age" -gt "$((10#$P_TICKET_TTL))" ]; then rm -f "$Q/$t"; fi',
        r'if [ "$tk_age" -gt "$((10#$P_TICKET_TTL * 10))" ]; then rm -f "$Q/$t"; fi',
    ),
    # Kills R9/wait-lock-stale-removed and R15/two-field-owner-not-run-mode (late-1c).
    mutate.Mutation(
        "the wait-mode lock TTL (rule 1c) is scaled 10x",
        r'if [ "$age" -gt "$((10#$P_LOCK_TTL))" ]; then',
        r'if [ "$age" -gt "$((10#$P_LOCK_TTL * 10))" ]; then',
    ),
    # Kills R9/trash-stale-swept, R9/trash-fresh-kept, R9/non-trash-old-entries-kept.
    mutate.Mutation(
        "the trash sweep TTL comparison is scaled 10x",
        r'if [ "$tr_age" -gt "$((10#$P_TICKET_TTL))" ]; then rm -rf "$e"; fi',
        r'if [ "$tr_age" -gt "$((10#$P_TICKET_TTL * 10))" ]; then rm -rf "$e"; fi',
    ),
    # Age in minutes: nothing ever looks stale, so it kills 44 rows (R10/ownerless-taken-after-ttl,
    # R11/replaced-lock-restored, R11/restored-event-logged, R14f/fresh-lock-not-reported-stale, R14f/reports-stale-1b,
    # R14f/sentinel-alive, R15/two-field-owner-not-run-mode, R5/term-during-prune-prompt, R8b/holder-ticket-gone,
    # R8b/lock-kept-at-half-stale, R8b/lock-removed-after-stale, R8b/second-go-within-bound, R8b/stage-gone-before-go,
    # R8b/term-seen-lock-present, R8e/first-wrapper-143, R8e/first-wrapper-verdict-line, R8e/late-tick-keeps-lock,
    # R8e/lock-handed-on, R8e/no-overlap-in-log, R8e/stage-killed, R8f1/lock-removed, R8f1/ticket-kept-when-name-differs,
    # R8f1/unrelated-not-killed, R8f2/group-killed, R8f3/killed-before-lock-removed, R8g/live-waiter-reaches-go,
    # R8g/live-waiter-ticket-present, R8h/i-holder-ticket-gone, R8h/i-lock-gone-after-bound, R8h/i-no-trash,
    # R8h/i-not-before-run-stale, R8h/i-second-go, R8h/ii-lock-gone, R8h/ii-no-trash, R8h/ii-second-go,
    # R8h/kill-stage-found-nothing-logged, R9/non-trash-old-entries-kept, R9/ticket-fresh-kept, R9/ticket-stale-removed,
    # R9/trash-fresh-kept, R9/trash-stale-swept, R9/wait-lock-fresh-kept, R9/wait-lock-stale-removed,
    # R9a/age-30s-within-1).
    # The slowest mutant after the throttle inversion. BOUND: every stale-rule row's own run_bounded / wait_for.
    mutate.Mutation(
        "the age primitive returns minutes, not seconds",
        r"printf '%s\n' $((n - m))",
        r"printf '%s\n' $(((n - m) / 60))",
    ),
    # The sweep widens to every entry of the state directory: kills R9/non-trash-old-entries-kept only.
    mutate.Mutation(
        "the trash sweep covers every entry of the state directory",
        r'for e in "$D"/trash.*; do',
        r'for e in "$D"/*; do',
    ),
    # --- orphan locks, rule a (row R10) ---
    # Kills R10/ownerless-taken-after-ttl only. BOUND: run_bounded 4 in that row.
    mutate.Mutation(
        "rule 1(a) is removed: an ownerless lock is never stale",
        r'if [ "$age" -gt "$((10#$P_ORPHAN_LOCK_TTL))" ]; then',
        r"if false; then",
    ),
    # Kills R10/seam-above-constant-not-taken and R10/ownerless-taken-after-ttl; R10/ownerless-not-taken-before-ttl stays green
    # (the seam of 100 s is below the 120 s constant in that row).
    mutate.Mutation(
        "the orphan lock TTL constant is used instead of the GATE_ORPHAN_LOCK_TTL seam",
        r'if [ "$age" -gt "$((10#$P_ORPHAN_LOCK_TTL))" ]; then',
        r'if [ "$age" -gt 120 ]; then',
    ),
    # --- remove_lock (rows R11, R14f) ---
    # Kills R11/release-other-leaves-lock, R11/foreign-lock-inode-stable, R11/collision-no-effect.
    mutate.Mutation(
        "remove_lock renames a lock it neither owns nor judged stale",
        r'if [ -z "$rule" ] && [ "$mine" = 0 ]; then return 0; fi',
        r":",
    ),
    # Kills R11/replaced-lock-restored and R11/restored-event-logged.
    mutate.Mutation(
        "the restore of a replaced lock is removed",
        r'if mkdir "$L" 2>/dev/null && mv "$t/lock"/* "$L"/ 2>/dev/null; then',
        r"if false; then",
    ),
    # Kills R11/restored-event-logged only.
    mutate.Mutation(
        "a restored lock is not logged",
        r"log_event LOCK_RESTORED",
        r":",
    ),
    # --- the run and release paths (rows R4, R5, R8o, R12, R13) ---
    # Kills R12/running-stage-unaffected-by-flip only (kill_stage, with its guards, stops the stage when the machine throttles
    # mid-run).
    mutate.Mutation(
        "the throttle is checked while the stage runs and stops it",
        r"""touch_ticket "$name"
    sleep "$((10#$P_TICK))" &""",
        r"""touch_ticket "$name"
    if throttled; then kill_stage "$pgid" "$lstart"; fi
    sleep "$((10#$P_TICK))" &""",
    ),
    # Kills R13/exit-75, R13/within-bound, R13/ticket-removed, R13/cmd-never-ran, R13/holder-lock-untouched, R13/last-line-75 and
    # R8b/wait-mode-owner-not-removed. BOUND: R13 run_bounded 15, R8b run_bounded 12.
    mutate.Mutation(
        "--max-wait is ignored (the wait has no bound)",
        r'if ! wait_turn "$name" run "$maxwait"; then',
        r'if ! wait_turn "$name" run ""; then',
    ),
    # run_release leaves the ticket behind: kills 11 rows (R4/all-rc-equal-stage-rc, R4/no-overlap,
    # R4/start-order-equals-join-order, R4/state-dir-clean, R5/state-clean, R7/i-lock-ticket-gone, R7/ii-lock-ticket-gone,
    # R7/iii-orphan-ignoring-stage-lock-ticket-gone, R8g/live-waiter-reaches-go, R8o/release-ticket-before-lock,
    # W1/live-parent-run-executes-and-exits-0). R13/ticket-removed stays green (the separate removal before `exit 75`
    # still runs; neither ticket removal is pinned on its own). HANGS: the leftover ticket of run a keeps R4's runs b, c
    # and d waiting behind it, each bounded by `bg_finish 30`.
    mutate.Mutation(
        "run_release does not remove its own ticket",
        r'drop_holder_ticket "$RUN_NAME" "$$"',
        r":",
    ),
    # Kills R4/state-dir-clean, R5/state-clean, R6/reverse-form-lock-free, R7 lock-ticket-gone rows, R8o/release-ticket-before-lock.
    # BOUND: R4 bg_finish 30 (four waiters, the lock is never handed on).
    mutate.Mutation(
        "run_release never frees the lock",
        r'remove_lock "$LOCK_LINE"',
        r":",
    ),
    # ORDER: lock before ticket in run_release: kills R8o/release-ticket-before-lock only.
    mutate.Mutation(
        "run_release removes the lock BEFORE its ticket",
        r"""drop_holder_ticket "$RUN_NAME" "$$"
  if [ -n "$LOCK_LINE" ] && [ -d "$L" ] && [ "$(owner_line)" = "$LOCK_LINE" ]; then
    remove_lock "$LOCK_LINE"
  fi""",
        r'''if [ -n "$LOCK_LINE" ] && [ -d "$L" ] && [ "$(owner_line)" = "$LOCK_LINE" ]; then
    remove_lock "$LOCK_LINE"
  fi
  drop_holder_ticket "$RUN_NAME" "$$"''',
    ),
    # ORDER: lock before ticket in the heartbeat's cleanup: kills R8o/beat-ticket-before-lock only.
    mutate.Mutation(
        "the heartbeat's cleanup removes the lock BEFORE the holder's ticket",
        r"""drop_holder_ticket "$name" "$wpid"
      if [ -n "$mine" ] && [ "$(owner_line)" = "$mine" ]; then
        remove_lock "$mine"
      fi""",
        r'''if [ -n "$mine" ] && [ "$(owner_line)" = "$mine" ]; then
        remove_lock "$mine"
      fi
      drop_holder_ticket "$name" "$wpid"''',
    ),
    # Bash 5.3 re-enters a running trap: without the guard a repeated TERM restarts the grace and the KILL step is never reached.
    # Pinned by R7/iii-orphan-ignoring-stage-* under bash 5.3 only (the suite runs the PATH bash, 5.3 here).
    mutate.Mutation(
        "run_abort can be re-entered by a later signal",
        r'if [ "$ABORTING" = 1 ]; then return 0; fi',
        r":",
    ),
    # --- names, refusals and state (rows R14a, R14b, R14d, R14e, R14f) ---
    # Kills R14a/dash-accepted, R14a/convention-accepted and every row whose names carry a dash (all of them: rc 2).
    mutate.Mutation(
        "the name allowlist forbids the dash",
        r"'' | .* | *[!A-Za-z0-9._-]*) return 1 ;;",
        r"'' | .* | *[!A-Za-z0-9._]*) return 1 ;;",
    ),
    # Kills R14a/slash-rc2 only.
    mutate.Mutation(
        "the name allowlist admits a slash",
        r"'' | .* | *[!A-Za-z0-9._-]*) return 1 ;;",
        r"'' | .* | *[!A-Za-z0-9._/-]*) return 1 ;;",
    ),
    # Kills R14a/leading-dot-rc2 only.
    mutate.Mutation(
        "the name allowlist admits a leading dot",
        r"'' | .* | *[!A-Za-z0-9._-]*) return 1 ;;",
        r"'' | *[!A-Za-z0-9._-]*) return 1 ;;",
    ),
    # Kills R14a/empty-rc2 only.
    mutate.Mutation(
        "the name allowlist admits the empty name",
        r"'' | .* | *[!A-Za-z0-9._-]*) return 1 ;;",
        r".* | *[!A-Za-z0-9._-]*) return 1 ;;",
    ),
    # Kills R14b/live-holder-refused, R14b/refusal-names-holder, R14b/run-live-holder-refused.
    mutate.Mutation(
        "a name held by a live process is no longer refused",
        r'if is_num "$p" && [ "$((10#$p))" -gt 1 ] && [ "$((10#$p))" != "$$" ] && kill -0 "$p" 2>/dev/null; then',
        r"if false; then",
    ),
    # Kills R14b/dead-pid-taken-over, R14b/empty-ticket-taken-over, R14b/join-only-ticket-taken-over and R8g/*.
    mutate.Mutation(
        "any existing ticket of the name is refused, live holder or not",
        r'if is_num "$p" && [ "$((10#$p))" -gt 1 ] && [ "$((10#$p))" != "$$" ] && kill -0 "$p" 2>/dev/null; then',
        r"if true; then",
    ),
    # A failed state-directory creation no longer stops wait/run: kills R14d/wait-exit2, R14d/run-exit2,
    # R14d/run-no-stage-ran (R14d/wait-names-path stays green: bash's own redirect error names the path). HANGS the gate (it polls for a lock it can never create). BOUND: run_bounded 4 (twice) in R14d.
    mutate.Mutation(
        "an uncreatable state directory is not fatal",
        r"""(umask 077 && mkdir -p "$Q") 2>/dev/null || die "cannot create state directory $D"
  if [ ! -d "$Q" ]; then die "cannot create state directory $D"; fi""",
        r'(umask 077 && mkdir -p "$Q") 2>/dev/null || true',
    ),
    # status creates the state directory when GATE_DIR is set: kills R14e/gate-dir-nonexistent-stays-nonexistent. The default-path
    # rows (R14e/status-no-dir-text, default-path-not-created) are deliberately NOT aimed at: a mutant creating the default path
    # would write /private/tmp/claude-99999 outside the tree and leave every later run red.
    mutate.Mutation(
        "status creates the state directory",
        r'local owner r word src t n=0 info="" line',
        r"""local owner r word src t n=0 info="" line
  if [ -n "${GATE_DIR:-}" ]; then ensure_state_dir; fi""",
    ),
    # Kills R14e/fake-ignored-without-gate-dir only.
    mutate.Mutation(
        "the fake throttle seam is honoured without GATE_DIR",
        r'''[ "$seam_on" = 1 ] || return 1
  f="${GATE_FAKE_THROTTLE_FILE:-}"''',
        r''':
  f="${GATE_FAKE_THROTTLE_FILE:-}"''',
    ),
    # R14e's fixture has nothing to prune. Kills R14f/files-unchanged, R14f/inodes-mtimes-unchanged, R14f/events-unappended,
    # R14f/sentinel-alive, R14f/returns-fast, R14f/reports-stale-1b, R15/two-field-owner-not-run-mode.
    mutate.Mutation(
        "status prunes",
        r'local owner r word src t n=0 info="" line',
        r"""local owner r word src t n=0 info="" line
  prune""",
    ),
    # --- the owner line (row R15) ---
    # Kills R15/new-owner-field1-intact, R15/proto-release-frees-new-lock and every row that reads the owner's name.
    mutate.Mutation(
        "the owner line starts with the epoch, so field 1 is no longer the name",
        r'LOCK_LINE="$1 $(now) $$ $2"',
        r'LOCK_LINE="$(now) $1 $$ $2"',
    ),
    # --- the queued waiter's parent check (row W1) ---
    # The check is removed: kills W1/queued-waiter-leaves-when-parent-killed, W1/orphan-exit-code-and-verdict-line,
    # W1/orphan-says-why and W1/orphan-never-starts-command (the orphan keeps its place and runs the command once the
    # holder ends). BOUND: wait_for 5 / wait_for 3 / bg_finish 15 in the row.
    mutate.Mutation(
        "the wait_turn parent check is removed",
        r'''    if is_num "$now_ppid" && { [ "$now_ppid" = 1 ] || [ "$now_ppid" != "$entry_ppid" ]; }; then
      drop_holder_ticket "$name" "$$"''',
        r'''    if false; then
      drop_holder_ticket "$name" "$$"''',
    ),
    # Every waiter reads as orphaned: kills 96 rows (H1/run-exports-held-token, H2/held-false-after-release,
    # H2/held-true-by-ancestry-with-scrubbed-env, H2/held-true-inside, H3/nested-join-refused,
    # H3/nested-release-does-not-free-outer-lock, H3/nested-run-passes-through, H3/nested-run-prints-one-verdict-line,
    # H3/nested-run-propagates-rc, H3/nested-run-takes-no-ticket, H3/nested-wait-refused-naming-holder,
    # H3/stale-token-gated, R10/ownerless-not-taken-before-ttl, R10/ownerless-taken-after-ttl,
    # R10/seam-above-constant-not-taken, R11/collision-no-effect, R11/foreign-lock-inode-stable,
    # R11/release-other-leaves-lock, R12/flip-to-0-go-within-2-polls, R12/running-stage-unaffected-by-flip,
    # R12/throttled-head-waits, R13/cmd-never-ran, R13/exit-75, R13/holder-lock-untouched, R13/last-line-75,
    # R13/ticket-removed, R13/within-bound, R14b/dead-pid-taken-over, R14b/empty-ticket-taken-over,
    # R14b/join-only-ticket-taken-over, R14c/stdin-passes-through, R15/new-owner-field1-intact,
    # R15/proto-release-frees-new-lock, R15/proto-ticket-honoured, R16/launcher-line-runs-done-rc0,
    # R4/all-rc-equal-stage-rc, R4/no-overlap, R4/start-order-equals-join-order, R4/state-dir-clean,
    # R4h/non-head-does-not-take-lock, R4h/oldest-takes-lock-despite-newer-tickets, R5/exit-143-promptly,
    # R5/last-line-verdict-143, R5/no-survivors, R5/state-clean, R5/term-during-prune-prompt, R6/expect-satisfied,
    # R6/lock-held-during-stage, R6/reverse-form-lock-free, R6/trailer-stage-rc, R7/i-group-term-no-survivors,
    # R7/i-lock-ticket-gone, R7/i-no-trailer, R7/ii-lock-ticket-gone, R7/ii-no-trailer, R7/ii-plain-term-no-survivors,
    # R7/iii-orphan-ignoring-stage-lock-ticket-gone, R7/iii-orphan-ignoring-stage-no-survivors, R8/heartbeat-gone,
    # R8/second-run-holds-lock, R8/stage-gone, R8/stage-ran-before-kill, R8b/holder-ticket-gone,
    # R8b/lock-kept-at-half-stale, R8b/lock-removed-after-stale, R8b/second-go-within-bound, R8b/stage-gone-before-go,
    # R8b/term-seen-lock-present, R8b/wait-mode-owner-not-removed, R8d3/alive-at-grace-minus-slack, R8e/first-wrapper-143,
    # R8e/first-wrapper-verdict-line, R8e/late-tick-keeps-lock, R8e/lock-handed-on, R8e/no-overlap-in-log,
    # R8e/stage-killed, R8f1/lock-removed, R8f1/ticket-kept-when-name-differs, R8f1/unrelated-not-killed,
    # R8f2/group-killed, R8f3/killed-before-lock-removed, R8g/live-waiter-reaches-go, R8g/live-waiter-ticket-present,
    # R8h/i-holder-ticket-gone, R8h/i-lock-gone-after-bound, R8h/i-no-survivors, R8h/i-no-trash,
    # R8h/i-not-before-run-stale, R8h/i-second-go, R8h/ii-lock-gone, R8h/ii-no-survivors, R8h/ii-no-trash,
    # R8h/ii-second-go, R8h/kill-stage-found-nothing-logged, W1/live-parent-run-executes-and-exits-0,
    # W1/queued-waiter-keeps-place-with-live-parent). (the live waiter leaves at once and its command never runs).
    mutate.Mutation(
        "the wait_turn parent check is always true",
        r'''    if is_num "$now_ppid" && { [ "$now_ppid" = 1 ] || [ "$now_ppid" != "$entry_ppid" ]; }; then
      drop_holder_ticket "$name" "$$"''',
        r'''    if :; then
      drop_holder_ticket "$name" "$$"''',
    ),
    # The orphan exits 75 (the no-turn code): kills W1/orphan-exit-code-and-verdict-line only.
    mutate.Mutation(
        "the orphan exits 75 instead of 129",
        r"""emit_verdict "$name" 129
      exit 129""",
        r"""emit_verdict "$name" 75
      exit 75""",
    ),
    # --- nested turns: the token, `held`, the reentrant run and the wait/join refusal (rows H1-H3) ---
    # The export is removed: kills 3 rows (H1/run-exports-held-token, H2/held-false-after-release, H3/stale-token-gated).
    # (the stage still sees an ancestor-recognised nesting).
    mutate.Mutation(
        "the run does not export HEAVY_GATE_HELD",
        r"""  export HEAVY_GATE_HELD="$LOCK_LINE"
""",
        r"""  :
""",
    ),
    # The token comparison reads only the NAME field: kills H2/held-false-fabricated-line.
    mutate.Mutation(
        "the held predicate compares only the NAME field of the token",
        r"""[ "$HEAVY_GATE_HELD" = "$o" ]""",
        r"""[ "${HEAVY_GATE_HELD%% *}" = "${o%% *}" ]""",
    ),
    # The mode field is ignored: kills H2/held-false-wait-mode-lock.
    mutate.Mutation(
        "the held predicate ignores the mode field",
        r"""  [ "$(owner_field 4)" = run ] || return 1
""",
        r"""  :
""",
    ),
    # The ancestor walk accepts the first parent: kills 26 rows (H2/held-false-ancestry-pid-not-an-ancestor,
    # H3/stale-token-gated, R4/no-overlap, R8/second-run-holds-lock, R8b/holder-ticket-gone, R8b/lock-kept-at-half-stale,
    # R8b/lock-removed-after-stale, R8b/stage-gone-before-go, R8b/term-seen-lock-present, R8e/first-wrapper-143,
    # R8e/first-wrapper-verdict-line, R8e/late-tick-keeps-lock, R8e/lock-handed-on, R8e/no-overlap-in-log,
    # R8e/stage-killed, R8h/i-holder-ticket-gone, R8h/i-lock-gone-after-bound, R8h/i-no-trash, R8h/i-not-before-run-stale,
    # R8h/ii-lock-gone, R8h/ii-no-trash, R8h/kill-stage-found-nothing-logged, W1/orphan-exit-code-and-verdict-line,
    # W1/orphan-never-starts-command, W1/orphan-says-why, W1/queued-waiter-keeps-place-with-live-parent).
    mutate.Mutation(
        "the ancestry branch accepts a non-ancestor",
        r"""    if [ "$((10#$p))" = "$wpid" ]; then return 0; fi""",
        r"""    if :; then return 0; fi""",
    ),
    # The ancestry branch is dropped: kills H2/held-true-by-ancestry-with-scrubbed-env.
    mutate.Mutation(
        "the ancestry branch is dropped",
        r"""  if held_by_ancestry "$(owner_field 3)"; then""",
        r"""  if false; then""",
    ),
    # The command-line check is skipped: kills H2/held-false-ancestor-not-a-gate-process.
    mutate.Mutation(
        "the ancestry branch skips the command-line check",
        r"""  case "$cmdline" in *heavy-gate.sh*) ;; *) return 1 ;; esac
""",
        r"""  : "$cmdline"
""",
    ),
    # held rewrites the owner file's mtime: kills H2/held-writes-nothing.
    mutate.Mutation(
        "held touches state",
        r"""      printf '%s\n' "$HELD_NAME"
      exit 0""",
        r"""      touch "$L/owner"
      printf '%s\n' "$HELD_NAME"
      exit 0""",
    ),
    # The pass-through claims a ticket (left behind, as it has no release): kills H3/nested-run-takes-no-ticket only.
    mutate.Mutation(
        "the nested run claims a ticket",
        r"""running without a second turn\n' "$HELD_NAME" >&2
""",
        r"""running without a second turn\n' "$HELD_NAME" >&2
    claim_ticket "$name"
""",
    ),
    # The pass-through prints no verdict line: kills H3/nested-run-prints-one-verdict-line only.
    mutate.Mutation(
        "the nested run omits the verdict line",
        r"""    emit_verdict "$name" "$rc"
    exit "$rc"
  fi""",
        r"""    exit "$rc"
  fi""",
    ),
    # The pass-through exits 0 whatever the command returned: kills H3/nested-run-propagates-rc only.
    mutate.Mutation(
        "the nested run discards the command's status",
        r"""    exit "$rc"
  fi
  RUN_NAME=$name""",
        r"""    exit 0
  fi
  RUN_NAME=$name""",
    ),
    # The pass-through frees the outer lock: kills 2 rows (H3/nested-release-does-not-free-outer-lock,
    # H3/nested-run-passes-through).
    mutate.Mutation(
        "the nested run releases the outer lock",
        r"""    "$@"
    rc=$?
""",
        r"""    "$@"
    rc=$?
    remove_lock "name=$HELD_NAME"
""",
    ),
    # A nested wait is not refused: kills H3/nested-wait-refused-naming-holder (BOUND: run_bounded 8).
    mutate.Mutation(
        "a nested wait is not refused",
        r'''  if held_by_env; then
    die "$1 is nested inside $HELD_NAME; a wait here would wait for its own parent"
  fi
  claim_ticket "$1"''',
        r'''  if false; then
    die "$1 is nested inside $HELD_NAME; a wait here would wait for its own parent"
  fi
  claim_ticket "$1"''',
    ),
    # --- --help and the parameter table (rows R8d1, R8d2) ---
    # One sentence of the help text, reworded so the cost figure survives: kills R8d1/hand-on-cost-sentence only.
    mutate.Mutation(
        "the help text no longer names what the hand-on cost is made of",
        r"handed on after GATE_RUN_STALE plus one tick",
        r"handed on after the stale age plus one tick",
    ),
    # Digits only: kills R8d2/help-hand-on-figure only.
    mutate.Mutation(
        "the help text's hand-on figure is changed",
        r"about 86 s at the defaults",
        r"about 87 s at the defaults",
    ),
]

R3_MUTANT = next(
    m for m in MUTATIONS if m.label.startswith("take_lock is check-then-mkdir -p")
)

# SUITE_TIMEOUT_SECONDS bounds the baseline and every mutant run (mutate.run applies one flat limit).
#
# Baseline: the whole suite measured about 346 s (R11s's stress half included; HG_STRESS=0 drops it,
# so this is an upper figure). The slowest LEGITIMATE mutant run is one that makes rows wait out their
# bounds. The sum of every bound in the suite is 691 s over 74 call sites (run_bounded, wait_for,
# bg_finish), plus about 144 s for call sites run more than once (r8h_case x2, r8c_start x3, R4's four
# bg_finish 30), about 835 s in all. No mutant makes every row wait out its bound at once, but the
# throttle inversion and the age-in-minutes mutants come closest, so 346 + 835 = about 1180 s is the
# ceiling of a legitimate run. 2400 s is about twice that and about 7x the baseline, and still ends a
# genuine hang in 40 minutes. The campaign has since run to PASS through `kill-sets.py` (about 17,800 s
# for 69 mutants, timedout=0, under that tool's own --timeout 3600); this constant now governs only a direct
# `mutate.run`, and `kill-sets.py` ignores it.
SUITE_TIMEOUT_SECONDS = 2400


def _r3_rate(n: int) -> int:
    """Run only the R3 mutant `n` times and print how often the R3 rows caught it."""
    caught = 0
    errors = 0
    for _ in range(n):
        report = mutate.run(
            SUBJECT,
            SUITE_R3,
            [R3_MUTANT],
            cwd=str(REPO),
            timeout=SUITE_TIMEOUT_SECONDS,
        )
        caught += report.caught
        if report.rc == 2:
            errors += 1
    print(f"R3_KILL_RATE caught={caught}/{n}")
    return 2 if errors else 0


_USAGE = "usage: mutate_heavy_gate.py [--r3-rate N | --only LABEL-SUBSTRING]"


def main(argv: list[str]) -> int:
    if argv[:1] in (["--help"], ["-h"]):
        print(__doc__)
        return 0
    if (
        argv[:1] == ["--r3-rate"]
        and len(argv) == 2
        and argv[1].isdigit()
        and int(argv[1]) > 0
    ):
        return _r3_rate(int(argv[1]))
    chosen = MUTATIONS
    if argv[:1] == ["--only"] and len(argv) == 2 and argv[1]:
        chosen = [m for m in MUTATIONS if argv[1] in m.label]
        if not chosen:
            print(
                f"mutate_heavy_gate.py: --only {argv[1]!r} matches no mutant label",
                file=sys.stderr,
            )
            print(_USAGE, file=sys.stderr)
            return 2
    elif argv:
        print(_USAGE, file=sys.stderr)
        return 2
    report = mutate.run(
        SUBJECT, SUITE, chosen, cwd=str(REPO), timeout=SUITE_TIMEOUT_SECONDS
    )
    print(report.text)
    return report.rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

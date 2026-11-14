#!/usr/bin/env python3
"""Mutation campaign for scripts/kill-sets.py, driven by scripts/lib/mutate.py.

Deliberately NOT named `test_*`: it mutates a tracked file in place, so pytest must not collect
it. Run it on demand through `scripts/run-long.sh` (every row of the suite spawns the tool, and
the tool spawns suites, so one suite run takes minutes and the campaign tens of minutes) and
never while editing the subject, since the restore would clobber your edits:

    ./scripts/tests/mutate_kill_sets.py

Every row removes ONE core mechanism of the tool and names, after `caught by`, the
`scripts/tests/test_kill_sets.py` row that must notice. A survivor means that row does not
assert the effect the mechanism exists for: strengthen the row (a failing case first), never
reword the mutation. Each `old` string occurs exactly once in the subject
(`scripts/mutation-anchors-check.py` enforces it). The ids `(1)` to `(22)` are the first
campaign list for this tool, in its order; `(23)` to `(43)` are the list added with `--jobs`,
one per parallel mechanism, each naming the single row it is caught by; `(44)` and `(45)` were
added for the early `STOP` line and the cancellable copy.

Mechanisms pinned by a test row but deliberately NOT mutated here, by the spec's sizing rule
(one mutant per core mechanism; each of these would add a full suite run for a mechanism a row
already pins):

* the process-group kill after every run (`_kill_group`, pinned by t09);
* the environment scrub of `KILLSETS` and `KILL_LOG` (`_child_env`, pinned by t13a);
* the two INVALID detectors: the parse check (t10a) and the collection-abort banner (t10b);
* the duplicate-base refusal in the baseline gate (t04b);
* the empty-set duplicate exclusion: only unflagged KILLED mutants, whose kill sets are
  non-empty, enter the duplicate report (t01, t05).
* the D8 limit raise: `t16e2` calls `derive_proof_limit` in-process only, so no process-level
  row asserts that the proof assigns the raised limit (`t16a3` matches the shape of the
  summary line's `limit` field, and no row reads its value);
* the free-space gate for the worker copies (t15a(e));
* the spawn-and-register lock ordering and the worker's stop checks before the next mutant and
  before a refresh: no test can interleave a signal between two statements, so they are argued
  in the code's comments and checked by the lifecycle run;
* the `JOIN_SECONDS` bound and the keep-on-stuck-worker branch: `t19d` holds a worker in a copy
  the tool cannot cut short but kills the tool at its `STOP` line, so no row reads the final
  line that names a stuck worker or sees the kept copy;
* the proof's coverage and progress lines: output only, pinned by t16a3;
* the output fail-soft in `emit` once a stop is recorded: pinned by the closed-reader scenario
  of t19a and t19b;
* the error-path kill of sibling suites in `_run`'s `finally`: pinned by the folded case of
  t19b;
* the main-thread branch of the copy callback (`_cancellable`'s `check_stop()` call made from
  inside a copy on the main thread, which lets a stop during the master copy be acted on
  between directories): no row and no mutant covers it; a reviewer's probe showed a SIGTERM
  during a slowed master copy exiting by the signal 0.07 s later with nothing left behind.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "scripts" / "lib"))
import mutate  # noqa: E402

M = mutate.Mutation

SUBJECT = REPO / "scripts" / "kill-sets.py"
SUITE = [
    sys.executable,
    "-m",
    "pytest",
    "-q",
    "-p",
    "no:cacheprovider",
    str(REPO / "scripts/tests/test_kill_sets.py"),
]

MUTATIONS = [
    M(
        "(1) absence counted from the mutant's own PASS count, not U's names - caught by t03a",
        "    kill = universe - reading.passed\n",
        "    kill = frozenset(sorted(universe)[len(reading.passed) :])\n",
    ),
    M(
        "(2) the nF comparison dropped: unexplained and excess always 0 - caught by t03a",
        "    unexplained, excess = max(0, len(kill) - nF), max(0, nF - len(kill))\n",
        "    unexplained, excess = 0, 0\n",
    ),
    M(
        "(3) the nested-dump skip removed - caught by t03a",
        "        if DUMP_START.match(line):\n",
        "        if False:\n",
    ),
    M(
        "(4) pytest armed with -rA instead of -rpfE - caught by t02",
        'PYTEST_REPORT = "-rpfE"',
        'PYTEST_REPORT = "-rA"',
    ),
    M(
        "(5) pytest ERROR ids not counted as explaining absences - caught by t02",
        "    failing.update(errored)  # a setup or teardown ERROR is a failing row too\n",
        "    pass\n",
    ),
    M(
        "(6) the -x strip removed - caught by t08",
        'EXITFIRST = {"-x", "--exitfirst"}',
        "EXITFIRST = set()",
    ),
    M(
        "(7) the stopping-banner reason string removed - caught by t08",
        '        why += f" (reason: {reading.stopped})" if reading.stopped else ""\n',
        '        why += ""\n',
    ),
    M(
        "(8) the non-empty-universe check removed - caught by t04b",
        "        if not universe:\n",
        "        if False:\n",
    ),
    M(
        "(9) the second-baseline universe comparison removed - caught by t04b",
        "        if u1 != u2:\n",
        "        if False:\n",
    ),
    M(
        "(10) the canary skipped - caught by t12",
        "        g.canary(pool)\n",
        "        pass\n",
    ),
    M(
        "(11) the canary text made empty - caught by t12",
        'CANARY_TEXT = "{"',
        'CANARY_TEXT = ""',
    ),
    M(
        "(12) the argv tree classification removed - caught by t11",
        "            tree = _tree_of(value, work, live, master)\n",
        '            tree = "W"\n',
    ),
    M(
        "(13) the *.mutate-backup and .git/*.lock preflight removed - caught by t11",
        "\n    preflight(root)\n",
        "\n",
    ),
    M(
        "(14) the working copy reused across mutants instead of re-created - caught by t13a",
        "            refresh_working_copy(self.master, worker.work)\n",
        "            pass\n",
    ),
    M(
        "(15) the anchor-count check accepts a count other than 1 - caught by t05",
        "        if count != 1:\n",
        "        if count == 0:\n",
    ),
    M(
        "(16) the KILLED-ALL test kill >= C weakened to |kill| >= |C|-1 - caught by t06a",
        "        return kill >= canary_absent\n",
        "        return len(kill) >= len(canary_absent) - 1\n",
    ),
    M(
        "(17) the dup_in_run check dropped - caught by t03a",
        "    dup_in_run = sorted(b for b, texts in reading.texts.items() if len(texts) > 1)\n",
        "    dup_in_run = []\n",
    ),
    M(
        "(18) UNATTRIBUTED mapped to FAIL, not ERROR - caught by t07a",
        '    "unattributed": "ERROR",',
        '    "unattributed": "FAIL",',
    ),
    M(
        "(19) the live-subject leak check removed - caught by t13a",
        "    if now == live_sha:\n",
        "    if True:\n",
    ),
    M(
        "(20) a field inserted before mutants= in the RESULT line - caught by t14b",
        "rc={rc} mutants={counts['mutants']}\"",
        "rc={rc} extra=0 mutants={counts['mutants']}\"",
    ),
    M(
        "(21) the opaque-argv refusal removed - caught by t04a",
        "    if opaque(c.suite):\n",
        "    if False:\n",
    ),
    M(
        "(22) row_base dropped from the shell reader - caught by t01",
        "            texts.setdefault(row_base(m.group(1)), []).append(m.group(1))\n",
        "            texts.setdefault(m.group(1), []).append(m.group(1))\n",
    ),
    M(
        "(23) ordered release: blocks emitted in completion order - caught by t15b",
        "        return self._next_n\n",
        "        return next(iter(self._results), -1)\n",
    ),
    M(
        "(24) per-worker copy path: every worker uses the one `work` directory - caught by t17",
        '    return Path(tmp) / "pool" / str(k)\n',
        '    return Path(tmp) / "work"\n',
    ),
    M(
        "(25) the SUITE swap: every worker keeps the canonical suite argv - caught by t16b1",
        "        argv_k = rebase_argv(argv, canonical, work_k)\n",
        "        argv_k = list(argv)\n",
    ),
    M(
        "(26) the D4 residue rule removed - caught by t16b2",
        "    if residue:\n",
        "    if False:\n",
    ),
    M(
        "(27) the D5 proof skipped - caught by t16a1",
        "        g.proof(pool, state)\n",
        "        pass\n",
    ),
    M(
        "(28) the proof's universe comparison dropped, the canary comparison kept - caught by t16a1",
        "        if reading.passed != self.universe:\n",
        "        if False:\n",
    ),
    M(
        "(29) the per-mutant DIED catch narrowed to OSError, so another exception escapes - caught by t18a",
        "                except Exception as failure:\n",
        "                except OSError as failure:\n",
    ),
    M(
        "(30) `died` removed from FIELD_SEVERITY: a DIED block no longer sets ERROR - caught by t18a",
        '    "died": "ERROR",\n',
        '    "died": "NONE",\n',
    ),
    M(
        "(31) the main thread's group kill in `check_stop` removed: the workers' suites run on, the 20 s join expires, and the copy is kept with the stuck workers named (`_run`'s `finally` still kills every group after the join) - caught by t19b",
        "    killed = REGISTRY.kill_all()\n",
        "    killed = 0\n",
    ),
    M(
        "(32) the re-raise of the signal in `_interrupted` replaced by a plain return - caught by t19a",
        "    os.kill(os.getpid(), signum)\n",
        "    return 0\n",
    ),
    M(
        "(33) PARALLEL-PASS applied at --jobs 1 - caught by t15a",
        '    return (PARALLEL_PASS if jobs > 1 else "PASS"), 0\n',
        "    return PARALLEL_PASS, 0\n",
    ),
    M(
        "(34) the MAX_JOBS check dropped from the --jobs parse - caught by t15a",
        "    if not 1 <= value <= MAX_JOBS:\n",
        "    if value < 1:\n",
    ),
    M(
        "(35) the subject re-derivation: every worker's subject path is the canonical copy's, which `write_in_copy`'s guard refuses (an ERROR in the proof's canary step, a DIED block in the mutant phase) - caught by t17",
        "        subject_k = work_k / subject_rel\n",
        "        subject_k = canonical / subject_rel\n",
    ),
    M(
        "(36) the suite's cwd: every worker runs in the canonical copy - caught by t16b3",
        "        run = run_suite(worker.argv, worker.work, self.env, self.limit, self.runs_dir)\n",
        "        run = run_suite(worker.argv, self.workers[0].work, self.env, self.limit, self.runs_dir)\n",
    ),
    M(
        "(37) the proof's coded rounds: every worker holds the canary in every round - caught by t16c",
        "    return r in tuple(itertools.combinations(range(rounds), rounds // 2))[k]\n",
        "    return True\n",
    ),
    M(
        "(38) the write-time applied record deleted (the spec's relocation to release time is not one contiguous replacement) - caught by t13a",
        "            self.applied.append((n, label, sha))\n",
        "            pass\n",
    ),
    M(
        "(39) the D9 retirement: a worker whose refresh failed keeps taking mutants - caught by t18b1b",
        "                    retire = True\n",
        "                    retire = False\n",
    ),
    M(
        "(40) the single flag read in `main`: a signal after the RESULT line re-raises - caught by t19c",
        "    return rc\n",
        '    return _interrupted(state, args.keep, STOP["signum"]) if STOP["signum"] else rc\n',
    ),
    M(
        "(41) the D5 start barrier: each worker runs its suite right after its own prepare - caught by t16d1",
        "            pool.wait(prepared)\n",
        "            pass\n",
    ),
    M(
        "(42) the D5 end barrier: round r + 1 is prepared as each worker finishes its own run - caught by t16d2",
        "            pool.wait(ran)\n",
        "            pass\n",
    ),
    M(
        "(43) the last-worker fill: remaining mutants are left without an outcome - caught by t18b1a",
        "        self._fill_unserved(state)\n",
        "        pass\n",
    ),
    M(
        "(44) the early STOP line deleted: the artifact of a stop the gate cuts short names neither signal nor copy - caught by t19d",
        "    emit(_stop_notice(signum))\n",
        "    pass\n",
    ),
    M(
        "(45) the worker-side stop check in the copy callback removed: a worker mid-copy runs its copy to the end and holds up the join - caught by t19e",
        "        elif _stop_flagged():\n",
        "        elif False:\n",
    ),
]


def main() -> int:
    report = mutate.run(SUBJECT, SUITE, MUTATIONS, cwd=str(REPO))
    print(report.text)
    return report.rc


if __name__ == "__main__":
    sys.exit(main())

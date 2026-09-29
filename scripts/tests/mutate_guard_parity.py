#!/usr/bin/env python3
"""Mutation campaign for scripts/guard-parity.py, driven by scripts/lib/mutate.py.

Deliberately NOT named `test_*`: it mutates a tracked file in place, so pytest must not collect
it. Run it on demand through `run-long.sh` — `./scripts/tests/mutate_guard_parity.py` — and never
while editing the subject, since the restore would clobber your edits. One survivor costs a full
suite run (about three minutes); most rows die inside the fast unit tests, so a whole campaign is
the better part of an hour.

Every row disables ONE property and requires `scripts/tests/test_guard_parity.py` to notice. This
tool's whole job is to say honestly what it could and could not see, so a survivor here means the
suite would not notice the tool going quietly optimistic: strengthen the suite row, never reword
the mutation. The suite runs with `-x`, so a mutant is CAUGHT by the first row that fails — which
need not be the row named for it; only a full run with the named row's failure in hand shows that.

**A mutant must be HARMLESS WHEN EXECUTED.** Nothing here enables commit or tag signing in a
fixture: a signing prompt with no terminal hangs rather than fails, which no campaign may produce.

Pieces of the subject deliberately NOT mutated, and why:

* The explicit `if not old or not new` test in `classify`. `all()` over an empty list is True, so
  the next test returns the same verdict for an empty reading list — the two agree by construction,
  and deleting the first leaves every outcome unchanged. It stays because it says WHY (two absent
  readings compare equal). The second test IS mutated, and that is the one that can move a verdict.
* `not finished` in `run_stage`'s incomplete test. A worker that dies has a non-zero status, and a
  worker cannot exit 0 without writing its `done` line, so the clause is defensive; `_finished`,
  which decides reuse, is what a truncated file exercises.
* The unlink of a stale `.key` and `.out` before a shard is re-run. Reuse is decided by an exact
  key AND a `done` line, so a stale file beside a changed key is already refused.
* `OUTPUT_TEXT_CAP`, `MAX_LISTED` and `TIMEOUT_S` as VALUES: every value produces a truthful
  report. What is pinned is that a cap exists and is applied.
* The write limit (`_limit_writes`, `FSIZE_CAP`). Its mutant is a guard writing until the disk
  fills, which a campaign may not produce; the suite's endless-writer row is what proves the limit
  today.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "scripts" / "lib"))
import mutate  # noqa: E402

SUBJECT = REPO / "scripts" / "guard-parity.py"
SUITE = [
    sys.executable,
    "-m",
    "pytest",
    "-q",
    "-x",
    "-p",
    "no:cacheprovider",
    str(REPO / "scripts" / "tests" / "test_guard_parity.py"),
]

M = mutate.Mutation

MUTATIONS = [
    # ---- corpus
    M(
        "corpus-id-ignores-cwd: the same command in two directories is one row",
        '    raw = (cwd + "\\0" + command).encode("utf-8", "surrogatepass")',
        '    raw = command.encode("utf-8", "surrogatepass")',
    ),
    M(
        "corpus-order-is-lexical: the pseudo-shuffle is a sort by id",
        '    return hashlib.sha256((identity + "order").encode("ascii")).hexdigest()',
        "    return identity",
    ),
    M(
        "corpus-walk-skips-symlinked-directories",
        "followlinks=True",
        "followlinks=False",
    ),
    M(
        'corpus-prefilter-misses-spaced-json: a `"name": "Bash"` line is skipped',
        "if '\"Bash\"' not in line:",
        'if \'"name":"Bash"\' not in line:',
    ),
    M(
        "corpus-skip-uncounted: a record without a cwd vanishes silently",
        'skip("no_cwd")',
        "pass",
    ),
    M(
        "corpus-collision-ignored: two rows sharing an id are merged silently",
        'raise CorpusError(f"row id collision on {identity}")',
        "pass",
    ),
    M(
        "corpus-message-skip-uncounted: a record whose message is a string vanishes silently",
        'skip("non_object_message")',
        "pass",
    ),
    M(
        "corpus-content-skip-uncounted: a record whose content is a string vanishes silently",
        'skip("non_list_content")',
        "pass",
    ),
    M(
        "corpus-header-count-unchecked: a corpus cut at a line boundary is graded",
        "    if len(rows) != expected_rows:",
        "    if False:",
    ),
    M(
        "corpus-header-digest-unchecked: a corpus with the right count and other rows is graded",
        "    if corpus_digest(rows) != expected_digest:",
        "    if False:",
    ),
    M(
        "corpus-duplicate-row-allowed: one row repeated meets any row floor",
        "        if row.id in seen:",
        "        if False:",
    ),
    M(
        "corpus-read-accepts-any-id: a tampered frozen corpus is graded",
        '            good = row.id == row_id(row.cwd, row.tool_input["command"])',
        "            good = True",
    ),
    # ---- builds
    M(
        "pin-skips-the-blob-comparison: a corrupted file passes",
        "if data is None or _blob_id(data) != blob:",
        "if data is None:",
    ),
    M(
        "pin-ignores-the-exec-bit",
        'elif mode == "100755" and not os.access(path, os.X_OK):',
        "elif False:",
    ),
    M(
        "pin-reads-only-the-last-pipeline-stage: a failed archive with a clean tar passes",
        "archive.wait() != 0 or unpack.returncode != 0",
        "unpack.returncode != 0",
    ),
    M(
        "pin-accepts-files-the-tree-lacks: a rev that deleted a file leaves it importable",
        "            if rel not in tracked:",
        "            if False:",
    ),
    M(
        "unparsable-matcher-dropped: a hook vanishes from the registration comparison",
        "        return True  # unreadable: kept, so the comparison can still show it",
        "        return False",
    ),
    M(
        "settings-shape-escapes: a malformed hooks list is a traceback, not a PinError",
        "    except (AttributeError, TypeError) as exc:",
        "    except KeyError as exc:",
    ),
    M(
        "pin-trusts-a-damaged-directory: a failed proof returns the directory anyway",
        "        except PinError:\n            shutil.rmtree(dest)",
        "        except PinError:\n            return Build(label, rev, sha, dest, 0)",
    ),
    M(
        "status-hides-untracked-files: the repository's own setting decides what is uncommitted",
        '        "--untracked-files=all",\n',
        "",
    ),
    M(
        "registrations-read-every-hook: a matcher that does not select Bash counts",
        "if not _matches_bash(matcher):",
        "if False:",
    ),
    M(
        "registration-delta-omits-removals: a removed guard is invisible",
        "        for m, name, t in sorted((old or set()) - (new or set()), key=str)\n",
        "        for m, name, t in sorted(set(), key=str)\n",
    ),
    # ---- environment and fixtures
    M(
        "timing-pattern-first-wins: the guard takes the last line",
        'value = line[len("GUARD_REPO_PATTERN=") :]',
        'value = value or line[len("GUARD_REPO_PATTERN=") :]',
    ),
    M(
        "fixture-window-closes: the generated conf is not always open",
        "GUARD_END=2400",
        "GUARD_END=0001",
    ),
    M(
        "replay-env-writes-through: the caller's mapping is mutated",
        "    env = dict(base)",
        "    env = base",
    ),
    M(
        "hash-seed-unpinned: the workers are launched with a random seed",
        '    env["PYTHONHASHSEED"] = "0"',
        '    env.pop("PYTHONHASHSEED", None)',
    ),
    M(
        "names-digest-reads-values: a secret reaches the digest",
        '    return hashlib.sha256("\\n".join(sorted(env)).encode()).hexdigest()[:DIGEST_LEN]',
        '    return hashlib.sha256("\\n".join(f"{k}={v}" for k, v in sorted(env.items())).encode()).hexdigest()[:DIGEST_LEN]',
    ),
    # ---- worker
    M(
        "no-flush-before-fork: a parent's buffered bytes are written into a child's capture",
        "    for stream in (sys.stdout, sys.stderr, *_INHERITED):",
        "    for stream in ():",
    ),
    M(
        "shutdown-flush-failure-forgotten: the interpreter's status 120 becomes the guard's",
        "            if not _flushed(name):",
        "            if False:",
    ),
    M(
        "exit-string-not-printed: sys.exit('message') says nothing",
        "    print(code, file=sys.stderr)",
        "    pass",
    ),
    M(
        "traceback-keeps-the-tools-frame: an uncaught error names this file",
        "            exc.__traceback__ = trace.tb_next if trace else None",
        "            exc.__traceback__ = trace",
    ),
    M(
        "script-directory-not-first-on-sys-path: a sibling import fails in the fork only",
        "        sys.path[0] = os.path.dirname(os.path.realpath(path))",
        "        pass",
    ),
    M(
        "fork-main-module-not-installed: the guard sees this tool as `__main__`",
        '        sys.modules["__main__"] = main',
        "        pass",
    ),
    M(
        "fork-keeps-inherited-descriptors: a guard sees the tool's open files",
        "        os.closerange(3, 4096)  # a genuine hook holds only 0, 1 and 2",
        "        pass  # a genuine hook holds only 0, 1 and 2",
    ),
    M(
        "read-cap-off: a huge output is digested whole in the fork and the audit alike",
        "    if len(data) > READ_CAP:",
        "    if False:",
    ),
    M(
        "child-stays-in-the-workers-directory",
        "        os.chdir(neutral)",
        "        pass",
    ),
    M(
        "timeout-reads-as-an-allow",
        "            return TIMED_OUT\n        return make_reading(status, _read_capped(stdout)",
        '            return make_reading(0, b"", b"", markers)\n        return make_reading(status, _read_capped(stdout)',
    ),
    M(
        "symlinked-build-root-unresolved: a guard printing its resolved path never normalises",
        "    spellings = {str(root), str(root.resolve())}",
        "    spellings = {str(root)}",
    ),
    M(
        "build-root-not-normalised: two builds can never print the same text",
        "        data = data.replace(marker, BUILD_MARK.encode())",
        "        data = data",
    ),
    M(
        "stderr-text-uncapped",
        '        err.decode("utf-8", "replace")[:OUTPUT_TEXT_CAP],',
        '        err.decode("utf-8", "replace"),',
    ),
    M(
        "row-guard-subset-ignored: a canary is judged by every guard",
        '                for name in spec.get("guards") or guards:',
        "                for name in guards:",
    ),
    M(
        "repeat-ignored: one reading per unit whatever --repeat says",
        "                    for _ in range(args.repeat):",
        "                    for _ in range(1):",
    ),
    M(
        "done-line-not-written: a finished shard reads as unfinished",
        '            out.write(json.dumps({"done": True, "units": units}) + "\\n")',
        "            pass",
    ),
    M(
        "exec-mode-still-forks: the audit never runs a genuine process",
        '                        if args.mode == "fork" and compiled[name] is not None:',
        "                        if compiled[name] is not None:",
    ),
    M(
        "syntax-error-kills-the-worker: an uncompilable guard is not left to a genuine process",
        "    except (OSError, SyntaxError, ValueError):",
        "    except (OSError, ValueError):",
    ),
    M(
        "preload-leaves-sys-path-changed",
        "        sys.path.remove(str(lib))",
        "        pass",
    ),
    # ---- classification, selection, stages
    M(
        "all-timeouts-not-incomparable: a unit that never ran is compared",
        "    if all(r.rc is None for r in old) or all(r.rc is None for r in new):",
        "    if False:",
    ),
    M(
        "instability-unseen: a build disagreeing with itself is not UNSTABLE",
        "        if len({r.rc for r in readings}) > 1:",
        "        if False:",
    ),
    M(
        "one-builds-noise-excuses-both-audits",
        "(not own_noise and _output(ran) != _output(first))",
        "(not noisy and _output(ran) != _output(first))",
    ),
    M(
        "torn-shard-line-kills-the-run: a worker killed mid-write is a traceback, not INCOMPLETE",
        "        except ValueError:\n            return units, False\n",
        "        except ValueError:\n            raise\n",
    ),
    M(
        "exec-audit-ignored: a fork that disagrees with the shipped process passes",
        "        if ran.rc != first.rc or (not own_noise and _output(ran) != _output(first)):",
        "        if False:",
    ),
    M(
        "old-audit-ignored: only the new build's genuine process is read",
        "        (old, old_exec, noise[0]),\n",
        "",
    ),
    M(
        "new-audit-ignored: only the old build's genuine process is read",
        "        (new, new_exec, noise[1]),\n",
        "",
    ),
    M(
        "old-audit-never-collected: the old build's genuine reading is dropped before judging",
        '        ran_old = (execs["old"].get(unit) or [None])[0]',
        "        ran_old = None",
    ),
    M(
        "new-audit-never-collected: the new build's genuine reading is dropped before judging",
        '        ran_new = (execs["new"].get(unit) or [None])[0]',
        "        ran_new = None",
    ),
    M(
        "exec-timeout-called-a-mismatch",
        "        if ran.rc is None:",
        "        if False:",
    ),
    M(
        "opened-called-closed: the direction test reads the wrong build",
        "        if before.rc == EXIT_BLOCK:",
        "        if after.rc == EXIT_BLOCK:",
    ),
    M(
        "output-noise-compared: a flaky message is reported as a difference",
        "    if not noisy and _output(before) != _output(after):",
        "    if _output(before) != _output(after):",
    ),
    M(
        "verification-omits-refusals: a refused unit is never audited",
        "        if a.rc != 0 or b.rc != 0 or (a.rc, *_output(a)) != (b.rc, *_output(b)):",
        "        if (a.rc, *_output(a)) != (b.rc, *_output(b)):",
    ),
    M(
        "sample-unseeded: two runs audit different units",
        "set(random.Random(seed).sample(pool, min(sample, len(pool))))",
        "set(random.Random().sample(pool, min(sample, len(pool))))",
    ),
    M(
        "shard-split-loses-rows",
        "for i in range(parts) if rows[i::parts]]",
        "for i in range(parts - 1) if rows[i::parts]]",
    ),
    M(
        "old-repeats-unread: the old build's stability readings never reach the classification",
        '        old = sweep["old"].get(unit, []) + repeats["old"].get(unit, [])',
        '        old = sweep["old"].get(unit, [])',
    ),
    M(
        "new-repeats-unread: the new build's stability readings never reach the classification",
        '        new = sweep["new"].get(unit, []) + repeats["new"].get(unit, [])',
        '        new = sweep["new"].get(unit, [])',
    ),
    M(
        "reuse-ignores-the-key: any finished shard is reused",
        "            if not (keyed and _finished(out_file)):",
        "            if not _finished(out_file):",
    ),
    M(
        "shard-key-omits-the-build: another commit's readings are reused",
        "                            build.sha,\n",
        "",
    ),
    M(
        "shard-key-omits-the-fixture-salt",
        "                            salt,\n",
        "",
    ),
    M(
        "shard-key-omits-the-timeout",
        "                            timeout,\n",
        "",
    ),
    M(
        "shard-key-omits-the-tool-source: shards made by an older version of this file are reused",
        "                            tool,\n",
        "",
    ),
    M(
        "shard-key-never-written: nothing is ever reused",
        "        args.key_file.write_text(args.key)",
        "        pass",
    ),
    # ---- the verdict
    M(
        "floor-rows-off",
        "    if ev.rows < ev.min_rows:",
        "    if False:",
    ),
    M(
        "floor-transcripts-off",
        "    if ev.transcripts is not None and ev.transcripts < 1:",
        "    if False:",
    ),
    M(
        "floor-comparable-off: tolerating unproven units can license a pass over almost nothing",
        "    if ev.comparable < ev.min_rows:",
        "    if False:",
    ),
    M(
        "comparable-counts-unstable: 2,000 unstable units, tolerated, read as 2,000 compared",
        '        return len(self.verdicts) - self.count("INCOMPARABLE") - self.count("UNSTABLE")',
        '        return len(self.verdicts) - self.count("INCOMPARABLE")',
    ),
    M(
        "shard-reuse-per-build: one build's shard is reused beside a re-run of the other's",
        "        if reusable[index]:\n            reused += 1\n",
        "        if key_file.exists() and key_file.read_text() == key and _finished(out_file):\n"
        "            reused += 1\n",
    ),
    M(
        "deadline-marker-ignored: a guard that stopped itself at its own deadline reads as a verdict",
        "    if any(_deadlined(r) for r in (*old, *new, old_exec, new_exec)):",
        "    if False:",
    ),
    M(
        "deadline-marker-skips-the-audit-runs: only the sweep and repeat readings are checked",
        "    if any(_deadlined(r) for r in (*old, *new, old_exec, new_exec)):",
        "    if any(_deadlined(r) for r in (*old, *new)):",
    ),
    M(
        "guard-errors-ignored: internal errors logged during the replay do not stop a PASS",
        "    if ev.guard_errors > 0:",
        "    if False:",
    ),
    M(
        "guard-errors-only-this-run: a resumed run hides an error its reused shards logged",
        "        guard_errors=_count_log_records(guard_log),",
        "        guard_errors=0,",
    ),
    M(
        "workers-default-uncapped: the default scales with every CPU",
        "        default=min(os.cpu_count() or 2, DEFAULT_WORKERS_CAP),",
        "        default=os.cpu_count() or 2,",
    ),
    M(
        "canary-unchecked: an old build that refuses nothing is believed",
        "        if old is None or old.rc != EXIT_BLOCK:",
        "        if old is None:",
    ),
    M(
        "limit-allowed: a partial corpus can pass",
        "    if ev.limited:",
        "    if False:",
    ),
    M(
        "unsampled-allowed: nothing audited can pass",
        "    if ev.unsampled:",
        "    if False:",
    ),
    M(
        "tolerance-off-by-one",
        "    if unproven > ev.tolerate:",
        "    if unproven >= ev.tolerate:",
    ),
    M(
        "exec-mismatch-tolerated",
        '    if ev.count("EXEC_MISMATCH"):',
        "    if False:",
    ),
    M(
        "unfinished-shards-ignored",
        "    if ev.incomplete_shards:",
        "    if False:",
    ),
    M(
        "differences-ignored: a changed verdict passes",
        "    if differences:",
        "    if False:",
    ),
    M(
        "registration-not-a-difference-in-the-decision",
        "    differences = sum(ev.count(k) for k in DIFFERENCES) + len(ev.registration)",
        "    differences = sum(ev.count(k) for k in DIFFERENCES)",
    ),
    # ---- report and command line
    M(
        "verdict-line-omits-registration-differences",
        "verdict_diffs={sum(ev.count(k) for k in DIFFERENCES) + len(ev.registration)} ",
        "verdict_diffs={sum(ev.count(k) for k in DIFFERENCES)} ",
    ),
    M(
        "canaries-counted-not-checked: the line claims refusals nobody made",
        "        1 for _g, old, _n in ev.canaries if old is not None and old.rc == EXIT_BLOCK",
        "        1 for _g, old, _n in ev.canaries",
    ),
    M(
        "section-counts-lines-not-units",
        "({len(lines) if count is None else count})",
        "({len(lines)})",
    ),
    M(
        "command-uncapped-in-the-report",
        '    return repr(text if len(text) <= 300 else text[:300] + "...")',
        "    return repr(text)",
    ),
    M(
        "dirty-scripts-tree-graded-as-committed: a run reports on HEAD while the edits are elsewhere",
        "        if build.sha == head and (dirty := uncommitted(scope)):",
        "        if False:",
    ),
    M(
        "artifact-dir-unlocked: two runs delete each other's fixtures and shards",
        "        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)",
        "        pass",
    ),
    M(
        "report-ignores-the-repeats: a unit the sweep never read crashes the report",
        "    got = detail.sweep[label].get(unit) or detail.repeats[label].get(unit)",
        "    got = detail.sweep[label].get(unit)",
    ),
    M(
        "guard-error-log-never-counted",
        "    return sum(1 for line in text.splitlines() if header.fullmatch(line))",
        "    return 0",
    ),
    M(
        "artifact-dir-left-public: it holds raw commands",
        "    art.chmod(0o700)  # it holds raw commands",
        "    pass  # it holds raw commands",
    ),
    M(
        "worktree-artifact-allowed",
        "    if _inside_worktree(art):",
        "    if False:",
    ),
    M(
        "timing-guard-requested-without-a-pattern-runs-anyway",
        "        if requested:\n",
        "        if False:\n",
    ),
    M(
        "usage-error-has-no-result-line",
        '        print(error_line("usage error (see the message above)"))',
        "        pass",
    ),
    M(
        "canary-judged-by-every-guard",
        '    specs = [{**c._asdict(), "guards": [canary_guard(c.id)]} for c in canaries]',
        "    specs = [c._asdict() for c in canaries]",
    ),
    # ---- the fix pass
    M(
        "guard-compiled-inheriting-this-tools-future-flags: a guard compiled with `annotations` behaves differently in the fork",
        '"exec", dont_inherit=True)',
        '"exec")',
    ),
    M(
        "exit-status-not-clamped: a guard's sys.exit(256) or a huge int is not the status the interpreter returns",
        "        return code & 0xFF if -(2**63) <= code < 2**63 else 255",
        "        return code",
    ),
    M(
        "stage-exit-leaves-workers-running: an early exit or Ctrl-C strands the workers, which run in their own sessions",
        "        for proc in live:\n            if proc.poll() is None:",
        "        for proc in live:\n            if False:",
    ),
    M(
        "worker-deadline-unbounded: a hung preload holds the run forever",
        "proc.wait(timeout=max(0.0, deadline - time.monotonic()))",
        "proc.wait()",
    ),
    M(
        "main-catch-all-removed: an unexpected error is a traceback and no verdict line",
        "    except Exception as exc:  # noqa: BLE001 - last resort",
        "    except ZeroDivisionError as exc:  # noqa: BLE001 - last resort",
    ),
    M(
        "min-rows-accepts-zero: the row floor can be switched off from the command line",
        'parser.add_argument("--min-rows", type=_bounded(int, 1), default=1000)',
        'parser.add_argument("--min-rows", type=int, default=1000)',
    ),
    M(
        "reason-unescaped: a control sequence in a refused command reaches the terminal through the report",
        "    return repr(reading.text.strip().splitlines()[0][:160])[1:-1]",
        "    return reading.text.strip().splitlines()[0][:160]",
    ),
    M(
        "stale-report-kept: a failed run leaves an earlier run's report.txt ending in a RESULT line",
        '    for stale in ("report.txt", "differences.jsonl"):',
        '    for stale in ("differences.jsonl",):',
    ),
    M(
        "shard-key-omits-the-interpreter-path: shards made under another interpreter are reused",
        "                            sys.executable,\n                            sys.version,\n",
        "                            sys.version,\n",
    ),
    M(
        "shard-key-omits-the-interpreter-version: shards made under another version are reused",
        "                            sys.executable,\n                            sys.version,\n",
        "                            sys.executable,\n",
    ),
    M(
        "shard-key-omits-the-env-values: a changed variable value reuses a shard read under the old one",
        "                            env_values_digest(env),\n",
        "",
    ),
]


def main() -> int:
    report = mutate.run(SUBJECT, SUITE, MUTATIONS, cwd=str(REPO))
    print(report.text)
    return report.rc


if __name__ == "__main__":
    sys.exit(main())

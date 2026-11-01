#!/usr/bin/env python3
# Script: cycle-profile.py
# Purpose: Report where the wall-clock of each /feature cycle in a Claude Code session transcript went, by time class and stage
# Usage: cycle-profile.py --transcript FILE --out-dir DIR [options]
"""Profile where the wall-clock of each `/feature` cycle in one session transcript went.

**Why.** Five throwaway analysis scripts answered one question: of the time a `/feature` cycle
takes, how much is work, how much is a machine check (a gate), and how much is waiting on the
operator. This tool keeps their logic as one command. It reads one transcript (and its delegate
transcripts) once, classifies every second of every completed cycle, and writes `report.md` and
`data.json` into `--out-dir`. It measures; no sentence it prints proposes or ranks a change.

**Shape.** This file is the thin edge over the `scripts/lib/cycle_profile_*.py` modules: the
pipeline (`cycle_profile_run`), the renderer (`cycle_profile_report`) and the privacy controls
(`cycle_profile_privacy`). It owns the flags, the order of effects and the exit status.

**Order of effects** (nothing is written until the leak check has found nothing):

1. refuse the out-dir (a work tree, the transcript's tree, a foreign non-empty directory)
   BEFORE the transcript is opened;
2. read and profile (`run_profile`), then render pass 1 in memory;
3. REMEDYCHECK and LEAKCHECK over pass 1; render pass 2 carrying their rows; LEAKCHECK again
   over the pass-2 bytes (the printed counts come from this run);
4. only on no hit and no test hook: create the out-dir, write the marker, `data.json` and
   `report.md` (each through a fixed temp name renamed into place), then OUTSCOPE over the
   listing;
5. print the stdout summary, ending in exactly one `RESULT:` line.

**Verdict line**, the last line of stdout and the only one that starts `RESULT:`:

    RESULT: PASS|FAIL|ERROR|INCOMPLETE rc=0|1|2|3 cycles=<n> dispatches=<m> split=<k>/<m>
        checks=<pass>/<fail>/<skip> leak=<samples>/<hits> answer=<gate|work|undetermined>

(one physical line; the second line above is only wrapped for this docstring).

Every field is a closed token or an integer: no operator-supplied string reaches the line, the
report or `data.json`. Precedence ERROR > FAIL > INCOMPLETE > PASS. A usage fault (a bad flag,
`--prefix-bytes` without `--prefix-sha256`) prints its message on stderr and the same ERROR
line on stdout. An internal exception is ERROR rc=2 naming the exception CLASS only, never its
message. A killed run prints no `RESULT:` line: killed, not passed. A real SIGKILL between the
two renames of the writer is not exercised; a re-run into the marked out-dir cleans the two
temp names and flags any other stray entry.

**Output failure.** If stdout is closed or broken the verdict cannot be delivered, so the
status is ERROR rc=2 whatever the verdict was (ERROR outranks every verdict, D1): the line and
the status must not disagree, and a reader that sees rc 0 with no `RESULT:` line would take
an undelivered run for a pass. The files, written before the print, stay. Measured on the
shipped process: with stdout closed, with a pipe closed before the write and with one closed
before the final flush, the status is 2 and nothing is printed to stderr; before the fix they
were 1 (a traceback, which collides with FAIL), 120 and 120.

**Known residuals** (measured; the privacy writer stages one file at a time):
`report.md` can disagree with `INCOMPLETE reasons=...` on stdout, because the report is
rendered before the caller-known reason `leak-unproven` exists, so a run INCOMPLETE only for
that reason writes `INCOMPLETE-REASONS none` beside a `CHECK LEAKCHECK skip` row. And a
failure between the two renames (injected on the second write) left `data.json` replaced and
`report.md` the previous run's, ERROR rc=2, no temp name behind; the two files carry the same
`run-id` only within one run, so a reader compares the first lines.

**Privacy.** The out-dir path is echoed on stdout only, after the leak verdict and escaped so
it cannot start a line. Path flags are echoed as `given` or `none`. On a leak hit stdout holds
the `LEAKCHECK` line and the `RESULT:` line only and nothing is written. `CYCLE_PROFILE_TEST_INJECT`
is a test hook: the named file's text is appended to the rendered output before the leak check;
with a hit the run is FAIL, without one it withholds every output, prints `TESTHOOK active` and
is INCOMPLETE at best. The tool makes no copy of the transcript and writes only through the
privacy writer.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, NamedTuple, Optional, Sequence, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
# Importing the lib writes no bytecode beside it (a run from the live checkout would otherwise
# leave `scripts/lib/__pycache__`). Scoped to these imports; no variable is set for a child.
_wrote_bytecode, sys.dont_write_bytecode = sys.dont_write_bytecode, True
try:
    import cycle_profile_privacy as privacy  # noqa: E402
    import cycle_profile_records as records  # noqa: E402
    import cycle_profile_report as report  # noqa: E402
    import cycle_profile_run as pipeline  # noqa: E402
    from cycle_profile_manifest import ManifestError, summarize  # noqa: E402
    from cycle_profile_model import CheckRow, Params  # noqa: E402
finally:
    sys.dont_write_bytecode = _wrote_bytecode

#: Environment variable naming the test-hook file (see the module docstring).
INJECT_ENV = "CYCLE_PROFILE_TEST_INJECT"
#: What the marker file holds; its presence is what makes the directory reusable.
MARKER_TEXT = b"cycle-profile output directory\n"
HEX64 = re.compile(r"[0-9a-f]{64}")
CLASS_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}")


class UsageFault(Exception):
    """A command-line fault; the message went to stderr already."""


class Outcome(NamedTuple):
    """What a run prints: the stdout lines before `RESULT:`, the `RESULT:` line, the status."""

    lines: List[str]
    result: str
    rc: int


class Parser(argparse.ArgumentParser):
    """An argument parser whose faults raise `UsageFault` instead of exiting."""

    def error(self, message: str) -> Any:
        self.print_usage(sys.stderr)
        sys.stderr.write("%s: error: %s\n" % (self.prog, message))
        raise UsageFault()


class Given(argparse.Action):
    """Store a value and remember that the operator set it (the `cli` parameter source)."""

    def __call__(self, parser, namespace, values, option_string=None):  # type: ignore[no-untyped-def]
        setattr(namespace, self.dest, values)
        namespace.given = frozenset(namespace.given | {self.dest})


def _count(text: str) -> int:
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError("not an integer") from None
    if value < 0:
        raise argparse.ArgumentTypeError("must not be negative")
    return value


def _factor(text: str) -> float:
    try:
        value = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError("not a number") from None
    if not math.isfinite(value):
        raise argparse.ArgumentTypeError("must be a finite number")
    if value < 0:
        raise argparse.ArgumentTypeError("must not be negative")
    return value


def _digest(text: str) -> str:
    if not HEX64.fullmatch(text):
        raise argparse.ArgumentTypeError("not a sha256 digest")
    return text


#: `Params` field -> (option help). The type follows the field's default.
PARAM_HELP: Dict[str, str] = {
    "close_rerun_min": "minutes a re-run may follow a verdict and still extend the cycle",
    "close_post_min": "minutes a commit marker may follow and still extend the cycle",
    "close_min_suite_min": "minutes a suite must last to count as a suite",
    "hook_latency_s": "seconds an edit must wait to count as HOOK time",
    "long_call_s": "seconds a foreground call must last to count as a check",
    "lane_window_min": "minutes in which a lane phrase names the lane",
    "away_fence_k": "Tukey inner-fence multiplier for the AWAY rule",
    "away_min_gaps": "idle gaps below which no AWAY fence is fitted",
    "min_cycles": "completed cycles below which the answer is undetermined",
    "max_unparseable_pct": "percent of unparseable lines above which the run is incomplete",
    "min_timestamped_pct": "percent of timestamped records below which it is incomplete",
    "leak_samples": "most free-text windows the leak check samples",
    "leak_floor": "fewest samples that make the leak check meaningful",
    "leak_seed": "seed of the leak check's window offsets",
}


def build_parser() -> argparse.ArgumentParser:
    """The parser of the 23 options; its defaults are the `Params` defaults."""
    parser = Parser(
        prog="cycle-profile.py",
        allow_abbrev=False,
        description="Report where the wall-clock of each /feature cycle in a Claude Code "
        "session transcript went. Writes report.md and data.json into --out-dir and ends "
        "with exactly one RESULT: line.",
    )
    parser.set_defaults(given=frozenset())
    parser.add_argument(
        "--transcript", required=True, metavar="FILE", help="the session transcript"
    )
    parser.add_argument(
        "--out-dir", required=True, metavar="DIR", help="where the two reports go"
    )
    parser.add_argument(
        "--delegates", metavar="DIR", help="delegate transcripts (default: derived)"
    )
    parser.add_argument(
        "--artifacts",
        action="append",
        metavar="DIR",
        help="run-long artifact directory (repeatable)",
    )
    parser.add_argument(
        "--repo", metavar="DIR", help="git repository for the commit-time bound"
    )
    parser.add_argument(
        "--manifest", metavar="FILE", help="recorded figures to compare against"
    )
    parser.add_argument(
        "--prefix-bytes",
        type=_count,
        metavar="N",
        help="read exactly the first N bytes",
    )
    parser.add_argument(
        "--prefix-sha256", type=_digest, metavar="H", help="sha256 of those N bytes"
    )
    parser.add_argument(
        "--delegate-pins", metavar="FILE", help="JSON list of pinned delegate files"
    )
    defaults = Params()
    for name in Params._fields:
        default = getattr(defaults, name)
        parser.add_argument(
            "--" + name.replace("_", "-"),
            dest=name,
            action=Given,
            type=_factor if isinstance(default, float) else _count,
            default=default,
            metavar="N",
            help="%s (default: %s)" % (PARAM_HELP[name], default),
        )
    return parser


def parse(argv: Optional[Sequence[str]]) -> argparse.Namespace:
    """Parse and cross-check the arguments; a fault raises `UsageFault`."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if (args.prefix_bytes is None) != (args.prefix_sha256 is None):
        parser.error("--prefix-bytes and --prefix-sha256 go together")
    return args


# ---------------------------------------------------------------------------------------------
# Output lines
# ---------------------------------------------------------------------------------------------


def _num(value: Any) -> str:
    if value is None:
        return report.NA
    if isinstance(value, float):
        return "%.6g" % value
    return str(value)


def header_lines(profile: Dict[str, Any]) -> List[str]:
    """The input-describing rows: `PARAM`, `SNAPSHOT` and `FLOOR`."""
    inp = profile["input"]
    lines = [
        "PARAM %s=%s source=%s"
        % (p["name"].replace("_", "-"), _num(p["value"]), p["source"])
        for p in profile["params"]
    ]
    lines.append(
        "SNAPSHOT sha256=%s bytes=%d records=%d last_ts=%s pinned=%s"
        % (
            inp["sha256"],
            inp["bytes"],
            inp["records"],
            _num(inp["last_ts"]),
            "yes" if inp["pinned"] else "no",
        )
    )
    for f in profile["floors"]:
        lines.append(
            "FLOOR %s %s %s %s"
            % (
                f["name"],
                _num(f["want"]),
                _num(f["actual"]),
                "ok" if f["ok"] else "NOT-MET",
            )
        )
    return lines


def check_line(row: Any) -> str:
    got = row._asdict() if hasattr(row, "_asdict") else dict(row)
    return "CHECK %s %s %s" % (got["name"], got["verdict"], got["detail"])


def leak_line(leak: Any, floor: int) -> str:
    return "LEAKCHECK samples=%d hits=%d control=%s floor=%d" % (
        leak.samples,
        leak.hits,
        leak.control,
        floor,
    )


def leak_row(leak: Any, floor: int) -> CheckRow:
    """The LEAKCHECK row: a hit or a blind checker fails, too few samples is a skip."""
    if leak.hits > 0 or leak.control != "detected":
        verdict = "fail"
    elif not leak.floor_ok:
        verdict = "skip"
    else:
        verdict = "pass"
    return CheckRow(
        "LEAKCHECK",
        verdict,
        "samples=%d hits=%d control=%s floor=%d"
        % (leak.samples, leak.hits, leak.control, floor),
    )


def remedy_row(count: int) -> CheckRow:
    return CheckRow("REMEDYCHECK", "fail" if count else "pass", "words=%d" % count)


def _result(
    verdict: str,
    profile: Optional[Dict[str, Any]],
    counts: Tuple[int, int, int],
    leak: Any,
) -> str:
    cycles = dispatches = matched = 0
    answer = "undetermined"
    if profile is not None:
        cycles = profile["cycles"]["completed"]
        dispatches = profile["delegates"]["total"]
        matched = profile["delegates"]["matched"]
        answer = profile["pool"]["answer"]
    return report.result_line(
        verdict,
        cycles=cycles,
        dispatches=dispatches,
        matched=matched,
        passed=counts[0],
        failed=counts[1],
        skipped=counts[2],
        samples=0 if leak is None else leak.samples,
        hits=0 if leak is None else leak.hits,
        answer=answer,
    )


def error_outcome(lines: List[str]) -> Outcome:
    """An ERROR outcome that carries no profile figures."""
    return Outcome(lines, _result("ERROR", None, (0, 0, 0), None), 2)


# ---------------------------------------------------------------------------------------------
# The leak sampler
# ---------------------------------------------------------------------------------------------


def leak_windows(
    profile: Dict[str, Any], params: Params, transcript: Path, delegates: Path
) -> List[str]:
    """Windows of free text from the pinned bytes and the delegate files read, re-read once.

    Raises:
        PinError: A re-read differs from the bytes the main read hashed.
        ReadError: A file could not be re-read.
    """
    snap = profile["input"]
    raw = records.reread(transcript, snap["bytes"], snap["sha256"])
    found = privacy.sample_candidates(
        raw, seed=params.leak_seed, limit=params.leak_samples
    )
    inventory = sorted(profile["delegates"]["inventory"], key=lambda e: e["id"])
    for entry in inventory[: privacy.MAX_DELEGATE_FILES]:
        path = delegates / ("agent-%s.jsonl" % entry["id"])
        data = privacy.reread_delegate(path, entry["bytes"], entry["sha256"])
        found += privacy.sample_candidates(
            data, seed=params.leak_seed, limit=params.leak_samples
        )
    found = list(dict.fromkeys(found))
    if len(found) > params.leak_samples:
        rng = random.Random(params.leak_seed)
        keep = sorted(rng.sample(range(len(found)), params.leak_samples))
        found = [found[i] for i in keep]
    return found


# ---------------------------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------------------------


def _load_json(path: str) -> Any:
    with open(path, "rb") as handle:
        return json.loads(handle.read().decode("utf-8"))


def _withheld(
    profile: Dict[str, Any],
    leak: Any,
    rows: List[CheckRow],
    params: Params,
    lines: List[str],
    *,
    error: bool,
) -> Outcome:
    """The outcome of a run whose outputs are not written because of the leak check."""
    verdict, rc, ok, bad, skip = pipeline.derive_verdict(profile, rows, error=error)
    out = lines + [leak_line(leak, params.leak_floor)]
    return Outcome(out, _result(verdict, profile, (ok, bad, skip), leak), rc)


def run(args: argparse.Namespace) -> Outcome:
    """Everything after the arguments parsed, in the fixed order of effects."""
    params = Params(**{f: getattr(args, f) for f in Params._fields})
    transcript = Path(args.transcript)
    delegates_given = args.delegates is not None
    delegates = (
        Path(args.delegates)
        if delegates_given
        else pipeline.default_delegates_dir(transcript)
    )
    try:
        plan = privacy.check_out_dir(
            args.out_dir, transcript=transcript, delegates_dir=delegates
        )
    except privacy.Refusal as exc:
        return error_outcome(["REFUSED reason=%s" % exc.reason])
    rows = _load_json(args.manifest) if args.manifest else None
    pins = _load_json(args.delegate_pins) if args.delegate_pins else None
    prefix = None
    if args.prefix_bytes is not None:
        prefix = (args.prefix_bytes, args.prefix_sha256)
    profile = pipeline.run_profile(
        transcript,
        params,
        delegates_dir=args.delegates if delegates_given else None,
        artifacts=args.artifacts or [],
        repo=args.repo,
        manifest_rows=rows,
        prefix=prefix,
        delegate_pins=pins,
        overrides=sorted(args.given),
    )
    inject_path = os.environ.get(INJECT_ENV)
    extra = Path(inject_path).read_bytes() if inject_path else b""
    windows = leak_windows(profile, params, transcript, delegates)

    md1, js1 = report.render(profile)
    words = len(privacy.find_remedy_words(md1.decode("utf-8", "replace")))
    leak = privacy.leak_check(
        md1 + extra, js1 + extra, windows, floor=params.leak_floor
    )
    rows_post = [remedy_row(words), leak_row(leak, params.leak_floor)]
    if leak.control != "detected":
        return _withheld(profile, leak, rows_post, params, [], error=True)
    if leak.hits > 0:
        return _withheld(profile, leak, rows_post, params, [], error=False)

    post = {
        "leak": {
            "samples": leak.samples,
            "hits": leak.hits,
            "control": leak.control,
            "floor": params.leak_floor,
        },
        "rows": rows_post,
    }
    md2, js2 = report.render(profile, post)
    words = max(words, len(privacy.find_remedy_words(md2.decode("utf-8", "replace"))))
    leak = privacy.leak_check(
        md2 + extra, js2 + extra, windows, floor=params.leak_floor
    )
    rows_post = [remedy_row(words), leak_row(leak, params.leak_floor)]
    if leak.control != "detected":
        return _withheld(profile, leak, rows_post, params, [], error=True)
    if leak.hits > 0:
        return _withheld(profile, leak, rows_post, params, [], error=False)

    lines = header_lines(profile)
    hook = bool(inject_path)
    if not hook:
        cleaned = privacy.prepare_out_dir(plan)
        if cleaned:
            lines.append("CLEANED %d stale temp name(s)" % cleaned)
        if not plan.marked:
            privacy.write_output(plan.path, privacy.MARKER, MARKER_TEXT)
        privacy.write_output(plan.path, privacy.DATA, js2)
        privacy.write_output(plan.path, privacy.REPORT, md2)
        rows_post.append(privacy.outscope_listing(plan.path))
    lines += report.limits_block(profile, post).splitlines()
    lines += report.stdout_summary(profile).splitlines()
    if profile["manifest"] is not None:
        lines.append(summarize(profile["manifest"]))
    lines += [check_line(r) for r in list(profile["checks"]) + rows_post]
    lines.append(leak_line(leak, params.leak_floor))
    caller = ("leak-unproven",) if (hook or not leak.floor_ok) else ()
    if not leak.floor_ok:
        lines.append("LEAKCHECK UNPROVEN")
    if hook:
        lines.append("TESTHOOK active")
    else:
        lines.append("OUTPUT dir=%r" % str(plan.path))
    verdict, rc, ok, bad, skip = pipeline.derive_verdict(
        profile, rows_post, incomplete=caller
    )
    if verdict == "INCOMPLETE":
        reasons = pipeline.incomplete_reasons(profile, caller)
        lines.append("INCOMPLETE reasons=%s" % ",".join(reasons))
    return Outcome(lines, _result(verdict, profile, (ok, bad, skip), leak), rc)


def failure_outcome(exc: BaseException) -> Outcome:
    """Map an exception to an ERROR outcome that names its class (or a row index) only."""
    if isinstance(exc, ManifestError):
        return error_outcome(["ERROR class=ManifestError index=%d" % exc.index])
    name = type(exc).__name__
    if not CLASS_NAME.fullmatch(name):
        name = "Exception"
    return error_outcome(["ERROR class=%s" % name])


def deliver(text: str) -> bool:
    """Write `text` to stdout and flush; False when stdout is closed or broken.

    On failure fd 1 is pointed at the null device, so the interpreter's own flush at exit
    cannot fail again and replace the status (a failed shutdown flush exits 120).
    """
    try:
        sys.stdout.write(text)
        sys.stdout.flush()
    except (OSError, AttributeError, ValueError):
        try:
            fd = os.open(os.devnull, os.O_WRONLY)
            if fd != 1:
                os.dup2(fd, 1)
                os.close(fd)
        except OSError:
            pass
        return False
    return True


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Run the tool; returns the exit status (PASS 0, FAIL 1, ERROR 2, INCOMPLETE 3)."""
    try:
        outcome = run(parse(argv))
    except UsageFault:
        outcome = error_outcome(["ERROR usage"])
    except Exception as exc:  # noqa: BLE001 - every fault becomes ERROR, class name only
        outcome = failure_outcome(exc)
    text = "".join(line + "\n" for line in outcome.lines) + outcome.result + "\n"
    return outcome.rc if deliver(text) else 2


if __name__ == "__main__":
    sys.exit(main())

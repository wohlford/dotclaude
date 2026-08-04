#!/usr/bin/env python3
# Script: publish-drive.py
# Purpose: Drive publish-brick.sh once per brick over a reviewed plan, halting on the first failure
# Usage: publish-drive.py --plan <file> [--scope <path>] [--artifact-dir <path>] [--from <version>]
"""Drive `/propagate`'s publish path step 3 — one `publish-brick.sh` invocation per brick.

**Why this exists, and why it did not exist sooner.** Applying 25 bricks by hand meant writing a
throwaway loop and discarding it with the session — the third time this repo wrote and threw away
publish tooling, the same pattern that produced `publish-brick.sh` and `publish-fold-plan.py` one
level down. Each re-derivation drops a different safety property; the module docstring of
`scripts/lib/mutate.py` records where that ends.

**What it does NOT automate, which is the whole design.** The skill's rule — *one brick per
invocation, and you drive the loop* — has two halves. The MECHANICAL half (read a verdict line
exactly, notice a tag that silently failed to mint, stop on the first thing that does not hold) is
already owned by `publish-brick.sh`. The HUMAN half is review of the PLAN: which commits fold into
which brick, and the holistic pairings no tool can see. That half is preserved intact here, because
this driver takes a plan **file** as its subject and never generates one. Review happens before it
runs, at the artifact the operator actually reads, and an edited plan is the normal case.

Measured, and this is what unblocked the driver: on the 25-brick publish the human half of the
LOOP caught nothing — all 25 bricks passed their own audits, while the only threatening defect was
invisible per-brick and detectable only at convergence. `publish-fold-plan.py` now proves
convergence before offering a plan, so what this drives is a plan already proven to converge.

**It is not a publisher.** It never pushes and never moves the watermark; both remain foreground,
human, and after this step. A halt leaves the published branch exactly where the last proven brick
put it.

Exit codes: 0 every brick proven, 1 a brick was not proven (halted), 2 usage/precondition error.
Terminal verdict line: `RESULT: <STATUS> rc=<n> bricks=<n> applied=<n> halted=<version|none>`.
"""

from __future__ import annotations

import argparse
import shlex
import subprocess
import sys
import time
from pathlib import Path

# A brick runs the scope's whole static audit; ~21s was measured, and the worst legitimate brick
# is the one that decides this number, never plausibility. An overrun reports INDETERMINATE rather
# than a verdict, so a bound set too tight costs attention instead of a wrong answer.
DEFAULT_BRICK_TIMEOUT = 900


class DriveError(Exception):
    """A precondition failed; no brick may be applied."""


def resolve(path: Path) -> Path:
    """Resolve a path physically. BOTH sides of a containment test must use this one function.

    A containment guard whose two sides are resolved differently can never fire — measured in
    shipped code in this repo, where one side took `pwd` and the other `cd -P`.
    """
    return path.resolve()


def parse_plan(text: str) -> list[dict]:
    """Parse `publish-brick.sh` invocations out of a fold plan.

    The plan is `publish-fold-plan.py`'s own output, optionally hand-edited — DERIVED from what
    was already asserted rather than a hand-made second copy that can drift from it. `shlex` does
    the quoting exactly as a shell would, without the `eval` that would let a plan file execute
    anything.
    """
    bricks: list[dict] = []
    for lineno, raw in enumerate(text.split("\n"), 1):
        if "publish-brick.sh" not in raw:
            continue
        stripped = raw.strip()
        if stripped.startswith("#"):
            continue
        try:
            words = shlex.split(stripped)
        except ValueError as exc:
            raise DriveError(
                f"line {lineno}: cannot parse ({exc}): {stripped}"
            ) from exc
        # Drop everything up to and including the program word, so an absolute path, a bare name
        # and a `./scripts/` prefix all parse identically.
        idx = next(
            (i for i, w in enumerate(words) if w.endswith("publish-brick.sh")), None
        )
        args = words[idx + 1 :] if idx is not None else []
        # A line naming the engine but not parsing as an invocation is an ERROR, never a silent
        # skip: a plan whose lines are quietly discarded reads exactly like a shorter plan.
        if len(args) < 3:
            raise DriveError(
                f"line {lineno}: names publish-brick.sh but has {len(args)} argument(s), "
                f"need at least 3 (version endpoint subject): {stripped}"
            )
        bricks.append(
            {
                "version": args[0],
                "endpoint": args[1],
                "subject": args[2],
                "constituents": args[3:],
                "lineno": lineno,
            }
        )
    if not bricks:
        raise DriveError(
            "the plan names no publish-brick.sh invocations — nothing to drive, which is "
            "not a clean result"
        )
    return bricks


def preflight(
    scope: Path, artifact_dir: Path, engine: Path, will_apply: bool = True
) -> None:
    """Refuse to start on anything that would make a verdict unreadable or a brick unrunnable.

    `will_apply` is False for a dry run, which invokes nothing. The checks then split by whose
    precondition they are: a misconfigured artifact path is THIS tool's problem and is reported
    either way, while the clean tree is the ENGINE's precondition and only applies when an engine
    is actually going to run. Enforcing it in a dry run makes the dry run useless at the one
    moment it is reached for — before cleaning up, to see whether the plan parses.
    """
    if not engine.is_file():
        raise DriveError(f"no brick engine at {engine}")
    if not (scope / ".git").exists():
        raise DriveError(f"not a git repository: {scope}")

    # The artifact must live OUTSIDE the scope. An untracked file inside the repo fails the NEXT
    # brick's clean-tree precondition, so a driver that wrote its log there would break the run it
    # is driving — on the second brick, after the first had already committed.
    scope_r, art_r = resolve(scope), resolve(artifact_dir)
    if scope_r == art_r or scope_r in art_r.parents:
        raise DriveError(
            f"--artifact-dir {art_r} is inside --scope {scope_r}; an untracked file there "
            "fails the next brick's clean-tree precondition"
        )

    if not will_apply:
        return

    dirty = subprocess.run(
        ["git", "-C", str(scope), "status", "--porcelain"],
        capture_output=True,
        text=True,
        check=False,
    )
    if dirty.stdout.strip():
        raise DriveError(
            "the working tree is dirty; publish-brick.sh requires a clean tree and would "
            f"refuse on the first brick:\n{dirty.stdout.strip()[:400]}"
        )


def drive_one(engine, scope, artifact_dir, brick, timeout, log) -> tuple[str, str]:
    """Run one brick. Returns (outcome, detail); outcome is PASS, FAIL or INDETERMINATE."""
    cmd = [
        str(engine),
        "--scope",
        str(scope),
        "--artifact-dir",
        str(artifact_dir),
        brick["version"],
        brick["endpoint"],
        brick["subject"],
        *brick["constituents"],
    ]
    log.write(f"\n===== {brick['version']} ({brick['endpoint']}) =====\n")
    log.write("+ " + " ".join(shlex.quote(c) for c in cmd) + "\n")
    log.flush()
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, check=False, timeout=timeout
        )
    except subprocess.TimeoutExpired:
        # NOT a failure verdict. The brick may or may not have been proven; saying either would
        # state something that was not measured, and the inflating direction is how every prior
        # hole in this path failed.
        log.write(f"TIMEOUT after {timeout}s — no verdict was reached\n")
        log.flush()
        return "INDETERMINATE", f"exceeded {timeout}s without a verdict"

    log.write(proc.stdout)
    log.write(proc.stderr)
    log.write(f"ENGINE_EXIT_STATUS={proc.returncode}\n")
    log.flush()

    # An allowlist on the engine's OWN terminal verdict, required to be present and to name THIS
    # brick. "No FAIL in the output" is not a pass: a killed engine prints a prefix of good lines
    # and no summary at all.
    want = f"RESULT: PASS rc=0 brick={brick['version']}"
    verdicts = [ln for ln in proc.stdout.split("\n") if ln.startswith("RESULT: ")]
    last = verdicts[-1] if verdicts else ""
    if not last:
        return (
            "FAIL",
            "the engine emitted no RESULT line — it died before reaching a verdict",
        )
    if last != want:
        return "FAIL", f"verdict was {last!r}, wanted {want!r}"
    if proc.returncode != 0:
        return "FAIL", f"verdict said PASS but the engine exited {proc.returncode}"
    return "PASS", last


def main(argv: list[str]) -> int:
    """Drive the plan, or explain why nothing was driven."""
    parser = argparse.ArgumentParser(add_help=True, description=__doc__)
    parser.add_argument("--plan", required=True)
    parser.add_argument("--scope", default=None)
    parser.add_argument("--artifact-dir", default=None)
    parser.add_argument("--engine", default=None)
    parser.add_argument("--from", dest="start_at", default=None)
    parser.add_argument("--timeout", type=int, default=DEFAULT_BRICK_TIMEOUT)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    scope = Path(args.scope) if args.scope else Path.cwd()
    # Resolved beside THIS script, like the engine's own helper library: the repo is checked out
    # to the published branch while this runs, where the engine may not exist yet.
    engine = (
        Path(args.engine)
        if args.engine
        else Path(__file__).resolve().parent / "publish-brick.sh"
    )
    artifact_dir = (
        Path(args.artifact_dir) if args.artifact_dir else Path.cwd() / "drive-log"
    )

    try:
        bricks = parse_plan(Path(args.plan).read_text())
        preflight(scope, artifact_dir, engine, will_apply=not args.dry_run)
    except (DriveError, OSError) as exc:
        print(f"cannot drive: {exc}", file=sys.stderr)
        print("RESULT: ERROR rc=2 bricks=0 applied=0 halted=none")
        return 2

    if args.start_at:
        versions = [b["version"] for b in bricks]
        if args.start_at not in versions:
            print(
                f"cannot drive: --from {args.start_at} names no brick in the plan "
                f"({len(bricks)} bricks, first {versions[0]}, last {versions[-1]})",
                file=sys.stderr,
            )
            print("RESULT: ERROR rc=2 bricks=0 applied=0 halted=none")
            return 2
        skipped = versions.index(args.start_at)
        # Named, never silent: a driver that quietly starts partway through reads exactly like
        # one that ran the whole plan.
        print(
            f"--from {args.start_at}: SKIPPING {skipped} earlier brick(s), assumed already applied"
        )
        bricks = bricks[skipped:]

    print(f"engine:   {engine}")
    print(f"scope:    {resolve(scope)}")
    print(f"artifact: {resolve(artifact_dir)}")
    print(f"bricks:   {len(bricks)} to run, in plan order")

    if args.dry_run:
        for n, b in enumerate(bricks, 1):
            extra = " ".join(b["constituents"])
            print(
                f"  [{n}/{len(bricks)}] {b['version']} {b['endpoint']} {b['subject']!r} {extra}".rstrip()
            )
        print(f"\nRESULT: PASS rc=0 bricks={len(bricks)} applied=0 halted=none")
        return 0

    artifact_dir.mkdir(parents=True, exist_ok=True)
    log_path = artifact_dir / "drive.log"
    applied = 0
    halted = "none"
    status, rc = "PASS", 0

    with open(log_path, "w") as log:
        print(f"log:      {log_path}")
        for n, b in enumerate(bricks, 1):
            started = time.monotonic()
            outcome, detail = drive_one(
                engine, scope, artifact_dir, b, args.timeout, log
            )
            took = time.monotonic() - started
            print(
                f"[{n}/{len(bricks)}] {outcome:13s} {b['version']}  {took:.0f}s",
                flush=True,
            )
            if outcome != "PASS":
                print(f"          {detail}", flush=True)
                # Nothing after a failure runs. The published branch stays where the last proven
                # brick put it, and the recovery is the engine's to print, not this driver's.
                halted = b["version"]
                status, rc = ("ERROR", 2) if outcome == "INDETERMINATE" else ("FAIL", 1)
                break
            applied += 1
        log.write(f"\nDRIVER_EXIT_STATUS={rc}\n")

    if halted != "none":
        print(
            f"\nHALTED at {halted} — the {len(bricks) - applied - 1} brick(s) after it did NOT run"
        )
        print(f"full per-brick output: {log_path}")
    print(
        f"\nRESULT: {status} rc={rc} bricks={len(bricks)} applied={applied} halted={halted}"
    )
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

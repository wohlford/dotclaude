#!/usr/bin/env python3
# Script: env-claims-check.py
# Purpose: Verify CLAUDE.md's documented environment claims still hold on this machine
# Usage: env-claims-check.py --scope <repo>
"""Grade the instruction file's environment claims against the environment it describes.

CLAUDE.md loads in every project and asserts concrete facts about this machine. Nothing checked
them, so a stale claim was indistinguishable from a true one until a command failed. Measured: the
file said macOS gives BSD tools by default and GNU is an opt-in, while `/opt/local/libexec/gnubin`
had been on PATH all along — wrong for months, surfacing only because `date -j` happened to fail.

Two halves, deliberately different in kind:

* a declared TABLE does the verifying, and each entry carries an ANCHOR — a literal substring of
  CLAUDE.md that asserts the claim. A missing anchor FAILs as stale, which is what stops the table
  quietly verifying a claim the document no longer makes;
* whole-file lexical DISCOVERY of backticked absolute paths guards the floor: every path must be
  covered by a table entry or explicitly exempted. That keeps the table's path coverage from
  silently shrinking.

Nothing is ever executed from discovered text — verification targets are hardcoded — so there is
no code-execution-from-data surface. A revision that changes that must be re-triaged.
"""

from __future__ import annotations

import argparse
import os
import platform
import re
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple

SUBJECT_NAME = "CLAUDE.md"

# Backticked absolute paths, whole file. Scoping this to named sections was tried and MEASURED to
# fail: it missed `/opt/local/bin/` and `/opt/local/lib/`, which live outside the sections anyone
# would guess, and picked up a spurious bare `/`. The region model is the fragile part.
#
# The `+` (not `*`) is load-bearing. With `*` this matched a bare `/` three times — not a
# filesystem-root reference, but a TOKENIZER ARTIFACT: adjacent backticked words like
# `grep`/`sed`/`date` put a backtick-slash-backtick sequence in the text. An earlier draft
# exempted "/" with the stated reason "the filesystem root, used illustratively", which was
# simply not true of any occurrence in the file. Requiring one character after the slash drops
# the artifact and keeps all NINE real path claims — measured. (Nine PATHS, not nine table
# entries: the table has 14 entries, of which 9 target a path discovery can see. 9 covered + 7
# exempt = the 16 paths the verdict reports.)
#
# Residual, unfixable lexically: `foo`/bar/`baz` still yields a phantom `/bar/`. That FAILs
# loudly as unaccounted rather than passing silently, which is the right direction.
PATH_PATTERN = re.compile(r"`(/[^`\s]+)`")

# Each entry names why it is not a claim. A bare set would let the reason rot out of the file.
EXPLICIT_EXEMPTIONS = {
    "/tmp": "named as a destination NOT to use, not as an asserted fact",
    "/private/tmp": "named as a destination NOT to use, not as an asserted fact",
}


def discover_paths(text: str) -> list[str]:
    """Every distinct backticked absolute path in the subject, sorted."""
    return sorted(set(PATH_PATTERN.findall(text)))


def derived_skill_exemptions(scope: Path) -> set[str]:
    """Slash-commands that name a skill that EXISTS.

    Derived from `skills/*/` rather than hand-listed, which buys the property that a doc reference
    to a DELETED skill stops being exempt and correctly fails.
    """
    skills_dir = scope / "skills"
    if not skills_dir.is_dir():
        return set()
    return {f"/{d.name}" for d in skills_dir.iterdir() if d.is_dir()}


def _verdict(status: str, rc: int, **counts) -> str:
    """One verdict line, with the SAME key set at every exit point.

    An intermediate draft emitted three different shapes — the static FAIL carried `unaccounted=`
    but no `failed=`, the probe FAIL the reverse, the SKIP line bypassed this helper entirely, and
    the ERROR lines dropped `stale=`. No consumer parses the fields today (`check_env_claims`
    switches on rc; the run-long `--expect` pattern matches the prefix), so it was latent — but a
    verdict line whose fields depend on which branch produced it is a trap for the first consumer
    that does. Every exit fills every key.
    """
    keys = ("claims", "verified", "stale", "paths", "exempt", "unaccounted", "failed")
    body = " ".join(f"{k}={counts.get(k, 0)}" for k in keys)
    return f"RESULT: {status} rc={rc} {body}\n"


class Claim(NamedTuple):
    """One documented fact, its anchor in the subject, and how to check it."""

    anchor: str  # a short distinctive substring of CLAUDE.md asserting this claim
    kind: str  # dir | file | exec | tool_is | precedes
    target: str
    expect: str


# The table lives BELOW `Claim` because a NamedTuple must exist before it is instantiated at module
# scope; Task 1's placeholder sat above the imports this task adds and could not stay there.
CLAIMS: tuple[Claim, ...] = (
    Claim("`/bin/bash` is the system bash", "exec", "/bin/bash", "3."),
    Claim(
        "Prefer MacPorts bash (`/opt/local/bin/bash`)",
        "exec",
        "/opt/local/bin/bash",
        "5.",
    ),
    Claim(
        "`/opt/local/libexec/gnubin` is already on PATH",
        "precedes",
        "/opt/local/libexec/gnubin",
        "/usr/bin",
    ),
    Claim("plain\n    `date`, `grep`, `sed`, `ls` are GNU", "tool_is", "date", "GNU"),
    Claim("plain\n    `date`, `grep`, `sed`, `ls` are GNU", "tool_is", "grep", "GNU"),
    Claim("plain\n    `date`, `grep`, `sed`, `ls` are GNU", "tool_is", "sed", "GNU"),
    Claim("plain\n    `date`, `grep`, `sed`, `ls` are GNU", "tool_is", "ls", "GNU"),
    Claim(
        "The `g`-prefixed names (`gls`, `ggrep`, `gdate`)",
        "exec",
        "/opt/local/bin/ggrep",
        "",
    ),
    Claim("| grep | `/usr/bin/grep` |", "file", "/usr/bin/grep", ""),
    Claim("| sed | `/usr/bin/sed` |", "file", "/usr/bin/sed", ""),
    Claim("| date | `/bin/date` |", "file", "/bin/date", ""),
    Claim("| ls | `/bin/ls` |", "file", "/bin/ls", ""),
    Claim(
        "Location: `/opt/local/bin/`, `/opt/local/lib/`", "dir", "/opt/local/bin/", ""
    ),
    Claim(
        "Location: `/opt/local/bin/`, `/opt/local/lib/`", "dir", "/opt/local/lib/", ""
    ),
)


def platform_applies() -> bool:
    """Whether the documented environment is the one we are running in.

    This repo publishes to a public `main`, so it is cloned onto machines where none of these
    claims hold. Asserting them unconditionally would ship a permanent false FAIL to every clone.
    """
    if os.environ.get("ENV_CLAIMS_FORCE_INAPPLICABLE"):
        return False
    return platform.system() == "Darwin" and Path("/opt/local").is_dir()


def probe_tool(name: str) -> tuple[str, str]:
    """Resolve and identify `name` the way a SCRIPT does, never the interactive shell.

    Measured, twice, by this checker's own author: an interactive Claude Code shell defines `grep`
    as an injected FUNCTION shimming to ugrep. It is not exported, so a `#!/usr/bin/env bash`
    child resolves the real GNU grep — and a probe run from the ambient shell grades the harness's
    shim while reporting a confident clean pass about the wrong subject.

    Returns (resolved_path, first line of --version). The path is returned, not just the identity,
    so the verdict can name the file that answered instead of asking the reader to trust it.
    """
    # SCRUBBING is the defense; shell flags are not. Measured: an exported function SURVIVES
    # `bash --noprofile --norc -c`, because exported functions arrive through the environment as
    # BASH_FUNC_* entries rather than from any rc file — and BASH_ENV is sourced by
    # non-interactive bash despite --norc. Removing those keys is a property a test can pin.
    # SHELLOPTS/BASHOPTS are imported by bash at startup too. Measured: an exported
    # `SHELLOPTS=noexec` makes the child produce empty stdout at rc 0, so probe_tool returns
    # ("", "") and all four tool claims FAIL on a perfectly good environment. Fail-closed, but a
    # false FAIL is still a defect.
    #
    # PATH is deliberately NOT pinned: the ambient PATH *is* the subject here — the `precedes`
    # claim and every resolution grade it. Pinning would verify a configuration nobody runs, which
    # is the environment-answered-for-it hazard inverted.
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith("BASH_FUNC_")
        and k not in ("BASH_ENV", "ENV", "SHELLOPTS", "BASHOPTS")
    }
    proc = subprocess.run(
        [
            "bash",
            "-c",
            f"command -v {name!r} >/dev/null 2>&1 || exit 3; "
            f'printf "%s\\n" "$(command -v {name!r})"; {name!r} --version 2>&1 | head -1',
        ],
        capture_output=True,
        text=True,
        env=env,
    )
    if proc.returncode == 3 or not proc.stdout.strip():
        return ("", "")
    lines = proc.stdout.splitlines()
    return (lines[0].strip(), lines[1].strip() if len(lines) > 1 else "")


def verify_claim(c: Claim) -> tuple[bool, str]:
    """Check one claim against the environment. Returns (ok, detail).

    The target is always a hardcoded table value — never text parsed out of the subject — so no
    discovered string ever reaches a filesystem call or a child process.
    """
    if c.kind == "dir":
        ok = Path(c.target).is_dir()
        return ok, f"{c.anchor!r}: {c.target} is not a directory"

    if c.kind == "file":
        ok = Path(c.target).is_file()
        return ok, f"{c.anchor!r}: {c.target} is not a regular file"

    if c.kind == "exec":
        p = Path(c.target)
        if not (p.is_file() and os.access(c.target, os.X_OK)):
            return False, f"{c.anchor!r}: {c.target} is not an executable file"
        if not c.expect:
            return True, ""
        _resolved, identity = probe_tool(c.target)
        ok = c.expect in identity
        return ok, (
            f"{c.anchor!r}: {c.target} --version reports {identity!r}, "
            f"which does not contain {c.expect!r}"
        )

    if c.kind == "tool_is":
        resolved, identity = probe_tool(c.target)
        # The absolute check is not pedantry: `Path("grep").is_file()` resolves against the cwd,
        # so without it a repo file named `grep` would "verify" an answer that came from a shell
        # function rather than from a binary on PATH.
        if not resolved.startswith("/") or not Path(resolved).is_file():
            return False, (
                f"{c.anchor!r}: `{c.target}` resolved to {resolved!r}, which is not an absolute "
                "path to a regular file — a function or alias answered, not a binary"
            )
        ok = c.expect in identity
        return ok, (
            f"{c.anchor!r}: `{c.target}` resolved to {resolved!r}, whose identity {identity!r} "
            f"does not contain {c.expect!r}"
        )

    if c.kind == "precedes":
        entries = os.environ.get("PATH", "").split(os.pathsep)
        if c.target not in entries:
            return False, f"{c.anchor!r}: {c.target} is not on PATH at all"
        if c.expect not in entries:
            return False, f"{c.anchor!r}: {c.expect} is not on PATH at all"
        ok = entries.index(c.target) < entries.index(c.expect)
        return ok, (
            f"{c.anchor!r}: {c.target} is at PATH position {entries.index(c.target)}, "
            f"which does not precede {c.expect} at {entries.index(c.expect)}"
        )

    # Allowlist, not blocklist: an unknown kind is an instrument defect, never a silent pass.
    return False, f"{c.anchor!r}: unknown claim kind {c.kind!r}"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scope", required=True, help="repo root holding CLAUDE.md")
    args = parser.parse_args(argv)
    scope = Path(args.scope)

    subject = scope / SUBJECT_NAME
    try:
        text = subject.read_text()
    except OSError as exc:
        sys.stderr.write(f"env-claims-check: cannot read {subject}: {exc}\n")
        sys.stdout.write(
            _verdict("ERROR", 2, claims=0, verified=0, paths=0, unaccounted=0)
        )
        return 2

    paths = discover_paths(text)
    if not paths:
        # A matcher that stopped matching reports the loudest clean pass there is.
        sys.stderr.write(
            f"env-claims-check: discovered no paths in {subject} — the matcher reached nothing, "
            "which is an instrument failure rather than a clean subject\n"
        )
        sys.stdout.write(
            _verdict("ERROR", 2, claims=0, verified=0, paths=0, unaccounted=0)
        )
        return 2

    exempt = set(EXPLICIT_EXEMPTIONS) | derived_skill_exemptions(scope)
    covered = {c.target for c in CLAIMS}
    unaccounted = [p for p in paths if p not in covered and p not in exempt]
    for p in unaccounted:
        sys.stderr.write(
            f"env-claims-check: `{p}` is documented but neither verified by a table entry nor "
            "exempted — add an entry or an exemption with a reason\n"
        )

    # --- STATIC PHASE: pure text, no environment. Runs on EVERY machine, always. ---
    # This is the half that catches a reversed claim, so it must not be gated behind a predicate
    # that a missing /opt/local would flip. It is also what makes the suite deterministic on a
    # clone, where the probes below cannot run.
    static_failures: list[str] = []
    for c in CLAIMS:
        if c.anchor not in text:
            static_failures.append(
                f"stale anchor — CLAUDE.md no longer contains {c.anchor!r}; the claim's wording "
                "changed, so re-read the claim and update this entry"
            )
    for f in static_failures:
        sys.stderr.write(f"env-claims-check: {f}\n")

    if static_failures or unaccounted:
        sys.stdout.write(
            _verdict(
                "FAIL",
                1,
                claims=len(CLAIMS),
                verified=0,
                stale=len(static_failures),
                paths=len(paths),
                exempt=len(exempt & set(paths)),
                unaccounted=len(unaccounted),
            )
        )
        return 1

    # --- PROBE PHASE: needs the documented environment. Only THIS may skip. ---
    if not platform_applies():
        # Through _verdict like every other exit, so the key set is identical everywhere.
        sys.stdout.write(
            _verdict(
                "SKIP",
                3,
                claims=len(CLAIMS),
                verified=0,
                stale=0,
                paths=len(paths),
                exempt=len(exempt & set(paths)),
                unaccounted=0,
                failed=0,
            )
        )
        sys.stderr.write(
            "env-claims-check: the documented environment is not present here, so the probe "
            "phase was skipped. The static checks (floor, anchors) DID run and held.\n"
        )
        return 3

    probe_failures: list[str] = []
    verified = 0
    for c in CLAIMS:
        ok, detail = verify_claim(c)
        verified += 1
        if not ok:
            probe_failures.append(detail)
            sys.stderr.write(f"env-claims-check: {detail}\n")

    if verified == 0:
        # Unreachable while CLAIMS is non-empty, and kept deliberately: a future edit that filters
        # the loop could empty it, and a run that verified nothing must never read as a pass.
        #
        # `exempt=` is filled here like everywhere else — an earlier draft omitted it, so this exit
        # printed `exempt=0` on a subject where `/tmp` is exempt: a false zero, not a missing key,
        # and the one exit where a reader is already suspicious of the instrument. `unaccounted` is
        # the literal 0 rather than `len(unaccounted)` because control cannot arrive here with a
        # non-empty list — the static FAIL above returns on exactly that condition — so passing the
        # length asserts a quantity this branch has already proved is zero.
        sys.stderr.write(
            "env-claims-check: verified no claims — the table reached nothing\n"
        )
        sys.stdout.write(
            _verdict(
                "ERROR",
                2,
                claims=len(CLAIMS),
                verified=0,
                stale=0,
                paths=len(paths),
                exempt=len(exempt & set(paths)),
                unaccounted=0,
                failed=0,
            )
        )
        return 2

    rc = 1 if probe_failures else 0
    sys.stdout.write(
        _verdict(
            "FAIL" if rc else "PASS",
            rc,
            claims=len(CLAIMS),
            verified=verified,
            stale=0,
            paths=len(paths),
            exempt=len(exempt & set(paths)),
            failed=len(probe_failures),
        )
    )
    return rc


if __name__ == "__main__":
    sys.exit(main())

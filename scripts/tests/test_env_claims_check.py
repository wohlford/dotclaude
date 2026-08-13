"""Tests for scripts/env-claims-check.py.

Every row is fixture-driven. This repo publishes to a public `main`, so it is cloned onto machines
where none of CLAUDE.md's environment claims hold — a row asserting anything about THIS machine
would fail for every other clone. The mechanism is therefore exercised against synthetic subjects,
never against the real environment.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
CHECKER = REPO / "scripts" / "env-claims-check.py"


def run(scope: Path, force_inapplicable: bool = False) -> subprocess.CompletedProcess:
    """Invoke the checker.

    `force_inapplicable` pins the probe phase off, which is how a row that cares only about the
    STATIC half gets a deterministic exit code on every machine — including a machine where the
    documented environment is present but a real claim has drifted. Without it such a row sees
    rc 1 for a reason unrelated to its subject, and the checker's own suite would go red alongside
    a true env-claims finding, misattributing it to a broken instrument.
    """
    env = dict(os.environ)
    if force_inapplicable:
        env["ENV_CLAIMS_FORCE_INAPPLICABLE"] = "1"
    else:
        env.pop("ENV_CLAIMS_FORCE_INAPPLICABLE", None)
    return subprocess.run(
        [sys.executable, str(CHECKER), "--scope", str(scope)],
        capture_output=True,
        text=True,
        env=env,
    )


def _checker_module():
    import importlib.util

    spec = importlib.util.spec_from_file_location("ecc", CHECKER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def anchors_block() -> str:
    """Every anchor the table declares, DERIVED from the checker rather than hand-copied.

    A fixture that needs a clean static phase must contain every anchor, or the stale-anchor check
    fires and the row fails for a reason unrelated to what it tests. Deriving them means the
    fixtures cannot drift out of step with the table — hand-copying is how a "durable" test ends up
    not failing when the thing it guards is renamed.
    """
    return "\n".join(c.anchor for c in _checker_module().CLAIMS) + "\n"


def make_scope(
    tmp_path: Path,
    claude_md: str,
    skills: tuple[str, ...] = (),
    include_anchors: bool = False,
) -> Path:
    # `resolve()` pins the PHYSICAL path: $TMPDIR is a symlink on macOS, and a logical path that
    # does not physically contain the subject lets a path-resolving checker take a branch these
    # rows never reach.
    scope = tmp_path.resolve()
    body = (anchors_block() + claude_md) if include_anchors else claude_md
    (scope / "CLAUDE.md").write_text(body)
    for name in skills:
        (scope / "skills" / name).mkdir(parents=True, exist_ok=True)
    return scope


def test_missing_subject_is_an_instrument_failure_not_a_pass(tmp_path):
    scope = tmp_path.resolve()
    (scope / "skills").mkdir()
    proc = run(scope)
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "RESULT: ERROR rc=2" in proc.stdout


def test_an_uncovered_path_fails_and_is_named(tmp_path):
    scope = make_scope(tmp_path, "See `/no/such/documented/path` for details.\n")
    proc = run(scope)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "/no/such/documented/path" in proc.stdout + proc.stderr


def test_a_slash_command_is_exempt_when_the_skill_exists(tmp_path):
    # `include_anchors` keeps the STATIC phase clean, so this row fails only for its own reason,
    # and `force_inapplicable=True` pins the probe phase OFF so rc 3 is the only correct answer on
    # every machine. Both are required. An earlier draft accepted 0 or 3 and read that as harmless
    # breadth; it is not — on this machine, the day a real claim drifts, the probes FAIL and this
    # row goes rc 1 for a reason that has nothing to do with `/debrief`, reporting a true finding
    # as a broken instrument. What the row asserts is narrow: this path did not cause a failure.
    scope = make_scope(
        tmp_path,
        "Run `/debrief` at the end.\n",
        skills=("debrief",),
        include_anchors=True,
    )
    proc = run(scope, force_inapplicable=True)
    assert proc.returncode == 3, proc.stdout + proc.stderr
    assert "/debrief" not in proc.stderr


def test_a_slash_command_for_a_DELETED_skill_stops_being_exempt(tmp_path):
    # The property the derived exemption buys: hand-listing would keep this exempt forever.
    scope = make_scope(tmp_path, "Run `/debrief` at the end.\n", skills=())
    proc = run(scope)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "/debrief" in proc.stdout + proc.stderr


def test_explicitly_exempt_paths_do_not_fail(tmp_path):
    scope = make_scope(
        tmp_path, "Write to `/tmp` or `/private/tmp`.\n", include_anchors=True
    )
    proc = run(scope, force_inapplicable=True)
    assert proc.returncode == 3, proc.stdout + proc.stderr
    assert "/tmp" not in proc.stderr


def test_a_subject_with_no_paths_at_all_is_an_instrument_failure(tmp_path):
    # Zero discovered paths means the matcher stopped matching. A checker that matched nothing
    # reports the loudest clean pass there is, so this must never be rc 0.
    scope = make_scope(tmp_path, "No paths here at all.\n")
    proc = run(scope)
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "discovered no paths" in (proc.stdout + proc.stderr)


def test_a_stale_anchor_fails_rather_than_verifying_a_claim_the_doc_no_longer_makes(
    tmp_path,
):
    # The hole this closes: reversing a claim's prose would otherwise leave the table verifying
    # what it was written against — passing cleanly while the document says the opposite, which
    # is exactly the defect that motivated this checker.
    #
    # Deterministic on EVERY machine because the static phase runs before the platform predicate.
    # An earlier draft gated everything behind the predicate and this row had to accept rc 1 OR 3,
    # proving different things depending on where it ran.
    # The fixture MUST contain at least one backticked path. The zero-paths guard sits ABOVE the
    # static phase, so a subject with none returns ERROR rc 2 and never reaches the anchor logic —
    # an earlier fold asserted rc 1 here and was red on every machine. `/tmp` is exempt, so
    # discovery is non-empty while `unaccounted` stays empty and the 14 stale anchors decide it.
    scope = make_scope(
        tmp_path, "Write to `/tmp`. Nothing here resembles the real claims.\n"
    )
    proc = run(scope)
    out = proc.stdout + proc.stderr
    assert proc.returncode == 1, out
    assert "stale anchor" in out.lower()
    assert "RESULT: FAIL rc=1" in proc.stdout


def test_the_platform_predicate_produces_ONE_skip_not_a_failure(tmp_path, monkeypatch):
    # Simulated via the documented override so the row runs on any machine.
    # `include_anchors` is REQUIRED: without it the static phase FAILs rc 1 before the predicate is
    # ever consulted, and this row — the designated killer for mutation rows 6 and 9 — goes red on
    # every machine. An earlier fold omitted it.
    scope = make_scope(
        tmp_path, "`/bin/bash` and `/opt/local/libexec/gnubin`.\n", include_anchors=True
    )
    proc = subprocess.run(
        [sys.executable, str(CHECKER), "--scope", str(scope)],
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin", "ENV_CLAIMS_FORCE_INAPPLICABLE": "1"},
    )
    assert proc.returncode == 3, proc.stdout + proc.stderr
    assert "RESULT: SKIP rc=3" in proc.stdout
    assert proc.stdout.count("RESULT:") == 1
    # The SKIP must be honest about what DID run — "skipped" alone would read as "nothing checked".
    assert "static checks" in proc.stderr.lower()


def test_a_tool_probe_reports_the_path_that_ANSWERED(tmp_path):
    # A probe run from the ambient shell grades the harness's injected `grep` FUNCTION (a ugrep
    # shim) rather than the environment — measured twice. The verdict must name the file.
    import importlib.util

    spec = importlib.util.spec_from_file_location("ecc", CHECKER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    resolved, identity = mod.probe_tool("bash")
    assert resolved.startswith("/"), resolved
    assert "bash" in identity.lower()


def test_an_exported_shell_function_cannot_answer_the_probe(monkeypatch):
    # The row must CONSTRUCT the shim, not merely re-assert the happy path. An earlier draft just
    # repeated `probe_tool("bash") is a file`, duplicating the row above and unable to fail for
    # the reason its name gives.
    #
    # Measured: an exported function SURVIVES `bash --noprofile --norc -c` — those flags are inert
    # here — so scrubbing BASH_FUNC_* is the actual defense and this is what pins it.
    monkeypatch.setenv("BASH_FUNC_ls%%", "() { echo SHIM; }")
    mod = _checker_module()
    resolved, identity = mod.probe_tool("ls")
    assert resolved.startswith("/"), (
        f"a non-absolute answer means a function replied: {resolved!r}"
    )
    assert Path(resolved).is_file(), f"probe returned a non-file: {resolved!r}"
    assert "SHIM" not in identity, identity


# --- the three rows that exist to KILL mutation rows 3, 7 and 10 -----------------------------
# Without these, those rows have no killer and the campaign can never reach survived=0. The first
# two are IN-PROCESS, because what they must reach is a state neither the real table nor the real
# machine can produce. The third is a SUBPROCESS on purpose: row 10 is about the ORDER of two
# phases within a real run, so only a real run can observe it.


def test_an_emptied_table_is_an_instrument_failure_not_a_pass(tmp_path, monkeypatch):
    # Kills row 3. The `verified == 0` guard is UNREACHABLE against the real non-empty CLAIMS, so
    # dropping it is otherwise an equivalent mutant. Keeping the guard and shipping this row are a
    # package: the guard without this row is untested code, and this row without the guard fails.
    # It is worth keeping because a future filtered or emptied table on an all-exempt subject would
    # otherwise return PASS verified=0 — breaking success criterion 4.
    #
    # The PREDICATE IS PINNED, and it must be: the `verified == 0` guard sits BELOW the SKIP exit,
    # so on a machine without the documented environment `main` returns 3 and never reaches it.
    # Unpinned, this row is red on every public-main clone — the exact defect class the predicate
    # exists to prevent, rebuilt inside the row that guards the table.
    #
    # Pinned by replacing `platform_applies` itself, NOT by patching `platform.system` and
    # `Path.is_dir`. Measured: `monkeypatch.setattr(mod.Path, "is_dir", lambda self: True)` is a
    # patch of `pathlib.Path` for the whole process, and `derived_skill_exemptions` — which runs
    # ABOVE the predicate — then believes a non-existent `skills/` directory is real and raises
    # `FileNotFoundError` from `iterdir()`. That is red on EVERY machine, so the narrower patch is
    # not a stylistic preference. `platform_applies` is resolved as a module global at call time,
    # which is what makes the substitution take effect inside `main`.
    mod = _checker_module()
    monkeypatch.setattr(mod, "platform_applies", lambda: True)
    mod.CLAIMS = ()
    scope = make_scope(tmp_path, "Write to `/tmp`.\n")
    assert mod.main(["--scope", str(scope)]) == 2


def test_the_platform_predicate_actually_discriminates(monkeypatch):
    # Kills mutation rows 6 AND 7 — row 6 is `platform_applies()` returning True unconditionally,
    # row 7 is it returning False unconditionally — because this row asserts BOTH directions.
    # Row 7 is the one with no other killer: every subprocess row that reaches the predicate forces
    # inapplicability and asserts rc 3, which an always-False predicate satisfies identically, so
    # none of them can tell an always-False predicate from an honestly-False one.
    #
    # `Path.is_dir` is patched process-wide here, which is safe ONLY because this row calls
    # `platform_applies()` directly and never `main()` — see the row above for what that patch does
    # to `derived_skill_exemptions` on the way through `main`.
    mod = _checker_module()
    monkeypatch.delenv("ENV_CLAIMS_FORCE_INAPPLICABLE", raising=False)
    monkeypatch.setattr(mod.platform, "system", lambda: "Linux")
    assert mod.platform_applies() is False
    monkeypatch.setattr(mod.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(mod.Path, "is_dir", lambda self: True)
    assert mod.platform_applies() is True


def test_the_static_phase_runs_even_when_the_environment_is_absent(tmp_path):
    # Kills row 10, and it is the ONLY direct test of the spec's headline property: gating the
    # static phase behind the predicate would silence the anchor mechanism on every clone. Row 10
    # survives on a predicate-True host — the one the campaign runs on — unless a row forces
    # inapplicability AND supplies a stale subject.
    scope = make_scope(
        tmp_path, "Write to `/tmp`. Nothing resembles the real claims.\n"
    )
    proc = subprocess.run(
        [sys.executable, str(CHECKER), "--scope", str(scope)],
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin", "ENV_CLAIMS_FORCE_INAPPLICABLE": "1"},
    )
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "stale anchor" in (proc.stdout + proc.stderr).lower()

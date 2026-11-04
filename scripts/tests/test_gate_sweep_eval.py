"""Rows E0-E22 for scripts/gate-sweep-eval.py: the evaluator that decides whether the parallel gate may flip.

Each row builds a gate.tsv and a flake-sweep line, runs the real script as a process, and asserts the FIELD that
changes (a bound's held/missed word, the status) rather than the neighbouring verdict. Every bound, the serial band,
the load rule's K and the consistency check against T has a fixture on each side of it.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "gate-sweep-eval.py"
T = 3.0
K = 12
COLS = [
    "profile",
    "rc",
    "result",
    "wall",
    "notes",
    "digest_before",
    "digest_after",
    "fmax",
    "nover",
    "maxrun",
    "launchwait",
]
GOOD_SWEEP = "RESULT: PASS rc=0 runs=10/10 rows=40 varying=0 subject=stable\n"
LOADED = {"fmax": 5.0, "nover": 12, "maxrun": 12}
PAR_OK = "RESULT: PARALLEL-PASS rc=0 checks=40/0/1"
SER_OK = "RESULT: PASS rc=0 checks=40/0/1"
RED = "RESULT: FAIL rc=1 checks=39/1/1"


def rec(profile: str = "parallel", **kw: object) -> str:
    base = {
        "rc": "0",
        "result": PAR_OK if profile == "parallel" else SER_OK,
        "wall": "1000",
        "notes": "both" if profile == "parallel" else "none",
        "digest_before": "aaaa",
        "digest_after": "aaaa",
        "fmax": "0.5",
        "nover": "0",
        "maxrun": "0",
        "launchwait": "ok",
    }
    base.update({k: str(v) for k, v in kw.items()})
    return "\t".join([profile] + [base[c] for c in COLS[1:]])


def pars(n: int = 10, wall: float = 1000, **kw: object) -> list[str]:
    return [rec("parallel", wall=wall, **kw) for _ in range(n)]


def sers(wall: float = 2600, **kw: object) -> list[str]:
    return [rec("serial", wall=wall, **kw) for _ in range(2)]


def run(
    tmp_path: Path,
    lines: list[str],
    sweep: str = GOOD_SWEEP,
    extra: tuple = (),
    threshold: object = T,
    k: object = K,
    tsv_raw: bytes | None = None,
    sweep_raw: bytes | None = None,
):
    tsv = tmp_path / "gate.tsv"
    if tsv_raw is None:
        tsv.write_text("\t".join(COLS) + "\n" + "\n".join(lines) + "\n")
    else:
        tsv.write_bytes(tsv_raw)
    swp = tmp_path / "sweep.txt"
    if sweep_raw is None:
        swp.write_text(sweep)
    else:
        swp.write_bytes(sweep_raw)
    cmd = [
        sys.executable,
        str(SCRIPT),
        "--tsv",
        str(tsv),
        "--sweep",
        str(swp),
        "--threshold",
        str(threshold),
        "--k",
        str(k),
        *extra,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    results = [x for x in proc.stdout.splitlines() if x.startswith("RESULT:")]
    assert results, (
        f"no RESULT line; stdout={proc.stdout!r} stderr={proc.stderr[-300:]!r}"
    )
    words = results[-1].split()
    fields = dict(w.split("=", 1) for w in words[3:] if "=" in w)
    fields["status"] = words[1]
    fields["rc"] = str(proc.returncode)
    return proc, fields


def test_e0_control_ten_green_runs_inside_both_bounds_pass(tmp_path: Path) -> None:
    proc, f = run(tmp_path, pars() + sers())
    assert (f["status"], f["rc"], f["bound1"], f["bound2"]) == (
        "PASS",
        "0",
        "held",
        "held",
    )
    assert f["green"] == "10" and f["loaded"] == "0"


def test_e1_the_sixty_percent_bound_has_a_fixture_on_each_side(tmp_path: Path) -> None:
    _, inside = run(tmp_path, pars(wall=1559) + sers(2600))
    _, outside = run(tmp_path, pars(wall=1561) + sers(2600))
    assert (inside["bound2"], inside["status"]) == ("held", "PASS")
    assert (outside["bound2"], outside["bound1"], outside["status"]) == (
        "missed",
        "held",
        "FAIL",
    )


def test_e2_the_thirty_minute_bound_binds_with_the_ratio_held(tmp_path: Path) -> None:
    _, inside = run(tmp_path, pars(wall=1799) + sers(3400))
    _, outside = run(tmp_path, pars(wall=1801) + sers(3400))
    assert inside["bound2"] == "held"
    assert outside["bound2"] == "missed" and float(outside["ratio"]) < 0.6


def test_e3_a_missing_wall_is_indeterminate(tmp_path: Path) -> None:
    lines = pars(9) + [rec("parallel", wall="")] + sers()
    proc, f = run(tmp_path, lines)
    assert (f["status"], f["bound1"], f["bound2"]) == ("INDETERMINATE", "na", "na")
    assert f["rc"] == "3" and "numeric field" in proc.stdout


def test_e4_nine_and_eleven_records_are_indeterminate(tmp_path: Path) -> None:
    _, nine = run(tmp_path, pars(9) + sers())
    _, eleven = run(tmp_path, pars(11) + sers())
    assert nine["status"] == "INDETERMINATE" and eleven["status"] == "INDETERMINATE"


def test_e5_a_plain_pass_among_ten_parallel_runs_fails_bound_one(
    tmp_path: Path,
) -> None:
    lines = pars(9) + [rec("parallel", result=SER_OK)] + sers()
    _, f = run(tmp_path, lines)
    assert (f["status"], f["bound1"], f["green"]) == ("FAIL", "missed", "9")


def test_e6_notes_that_do_not_match_the_profile_are_indeterminate(
    tmp_path: Path,
) -> None:
    _, par = run(tmp_path, pars(9) + [rec("parallel", notes="none")] + sers())
    _, ser = run(tmp_path, pars() + [rec("serial", notes="both"), rec("serial")])
    assert par["status"] == "INDETERMINATE" and ser["status"] == "INDETERMINATE"


def test_e7_a_changed_digest_is_indeterminate_and_not_a_red(tmp_path: Path) -> None:
    changed = rec("parallel", result=RED, rc=1, digest_after="bbbb")
    _, f = run(tmp_path, pars(9) + [changed] + sers())
    assert f["status"] == "INDETERMINATE" and f["bound1"] == "na"


def test_e8_an_indented_result_line_is_not_read(tmp_path: Path) -> None:
    indented = rec("parallel", result="  " + PAR_OK)
    _, a = run(tmp_path, pars(9) + [indented] + sers())
    _, b = run(tmp_path, pars() + sers(), sweep="  " + GOOD_SWEEP)
    assert a["status"] == "INDETERMINATE" and b["status"] == "INDETERMINATE"


def test_e9_a_sweep_line_that_is_not_varying_zero_fails(tmp_path: Path) -> None:
    bad = "RESULT: FAIL rc=1 runs=10/10 rows=40 varying=2 subject=stable\n"
    _, f = run(tmp_path, pars() + sers(), sweep=bad)
    assert (f["status"], f["bound1"]) == ("FAIL", "missed")
    err = "RESULT: ERROR rc=2 runs=0/10 rows=0 varying=0 subject=unknown\n"
    _, g = run(tmp_path, pars() + sers(), sweep=err)
    assert g["status"] == "INDETERMINATE"


def test_e10_a_loaded_green_run_counts_and_keeps_its_wall(tmp_path: Path) -> None:
    slow = pars(5, wall=1600)
    fast = pars(4, wall=1000)
    loaded = [rec("parallel", wall=1000, **LOADED)]
    proc, f = run(tmp_path, slow + fast + loaded + sers())
    assert (f["status"], f["loaded"], f["bound1"], f["bound2"]) == (
        "PASS",
        "1",
        "held",
        "held",
    )
    assert "median_unloaded=1600" in proc.stdout


def test_e11_a_loaded_red_parallel_run_is_unattributable(tmp_path: Path) -> None:
    red = rec("parallel", result=RED, rc=1, **LOADED)
    proc, f = run(tmp_path, pars(9) + [red] + sers())
    assert f["status"] == "INDETERMINATE" and "unattributable" in proc.stdout


def test_e12_a_loaded_bracket_is_indeterminate(tmp_path: Path) -> None:
    lines = pars() + [rec("serial", wall=2600, **LOADED), rec("serial", wall=2600)]
    _, f = run(tmp_path, lines)
    assert f["status"] == "INDETERMINATE" and f["bound1"] == "na"


def test_e13_the_widened_serial_band_has_a_fixture_on_each_side(tmp_path: Path) -> None:
    _, lo_in = run(tmp_path, pars() + sers(2575))
    _, lo_out = run(tmp_path, pars() + sers(2573))
    _, hi_in = run(tmp_path, pars() + sers(3482))
    _, hi_out = run(tmp_path, pars() + sers(3484))
    assert (lo_in["status"], hi_in["status"]) == ("PASS", "PASS")
    assert (lo_out["status"], hi_out["status"]) == ("INDETERMINATE", "INDETERMINATE")


@pytest.mark.parametrize("field", ["fmax", "nover", "maxrun", "launchwait"])
def test_e14_a_record_missing_a_sampler_field_is_indeterminate(
    tmp_path: Path, field: str
) -> None:
    proc, f = run(tmp_path, pars(9) + [rec("parallel", **{field: ""})] + sers())
    assert f["status"] == "INDETERMINATE" and f["bound1"] == "na"
    assert "record 10" in proc.stdout


def test_e1b_the_sixty_percent_bound_holds_at_equality_and_not_beyond(
    tmp_path: Path,
) -> None:
    _, equal = run(tmp_path, pars(wall=1560) + sers(2600))
    _, beyond = run(tmp_path, pars(wall=1560.001) + sers(2600))
    assert (equal["bound2"], equal["status"]) == ("held", "PASS")
    assert (beyond["bound2"], beyond["bound1"]) == ("missed", "held")


def test_e2b_the_thirty_minute_bound_holds_at_equality_and_not_beyond(
    tmp_path: Path,
) -> None:
    _, equal = run(tmp_path, pars(wall=1800) + sers(3400))
    _, beyond = run(tmp_path, pars(wall=1800.001) + sers(3400))
    assert (equal["bound2"], equal["status"]) == ("held", "PASS")
    assert beyond["bound2"] == "missed" and float(beyond["ratio"]) < 0.6


def test_e4b_one_and_three_serial_records_are_indeterminate(tmp_path: Path) -> None:
    proc, one = run(tmp_path, pars() + sers()[:1])
    _, three = run(tmp_path, pars() + sers() + [rec("serial")])
    assert one["status"] == "INDETERMINATE" and three["status"] == "INDETERMINATE"
    assert "got 10 and 1" in proc.stdout


def test_e13b_each_bracket_is_judged_against_the_band_not_their_mean(
    tmp_path: Path,
) -> None:
    lines = pars() + [rec("serial", wall=2600), rec("serial", wall=2573)]
    proc, f = run(tmp_path, lines)
    reasons = [x for x in proc.stdout.splitlines() if x.startswith("indeterminate:")]
    assert f["status"] == "INDETERMINATE"
    assert len(reasons) == 1 and "record 12" in reasons[0]


def test_e13c_the_ratio_uses_the_mean_of_the_two_brackets(tmp_path: Path) -> None:
    lines = pars(wall=1700) + [rec("serial", wall=2575), rec("serial", wall=3482)]
    _, f = run(tmp_path, lines)
    assert (f["ratio"], f["bound2"], f["status"]) == ("0.561", "held", "PASS")


def test_e15c_an_expired_launch_wait_marks_a_green_run_loaded(tmp_path: Path) -> None:
    _, f = run(tmp_path, pars(9) + [rec("parallel", launchwait="expired")] + sers())
    assert (f["status"], f["loaded"], f["green"]) == ("PASS", "1", "10")


def test_e16_bytes_that_are_not_utf8_in_either_input_are_a_usage_error(
    tmp_path: Path,
) -> None:
    good = ("\t".join(COLS) + "\n" + "\n".join(pars() + sers()) + "\n").encode()
    proc, a = run(tmp_path, [], tsv_raw=b"\xff\xfe\x00 not utf-8\n")
    _, b = run(tmp_path, [], tsv_raw=good, sweep_raw=b"\xff\xfe RESULT\n")
    assert (a["status"], a["rc"]) == ("ERROR", "2")
    assert "unreadable input" in proc.stdout
    assert (b["status"], b["rc"]) == ("ERROR", "2")


def test_e16b_an_absent_input_file_is_a_usage_error(tmp_path: Path) -> None:
    extra = ("--tsv", str(tmp_path / "absent.tsv"))
    proc, f = run(tmp_path, pars() + sers(), extra=extra)
    assert (f["status"], f["rc"]) == ("ERROR", "2")
    assert "unreadable input" in proc.stdout


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("threshold", "nan"),
        ("threshold", "inf"),
        ("threshold", "-1"),
        ("threshold", "0"),
        ("k", "0"),
        ("k", "-3"),
    ],
)
def test_e17_a_threshold_or_k_outside_its_range_is_a_usage_error(
    tmp_path: Path, name: str, value: str
) -> None:
    proc, f = run(tmp_path, pars() + sers(), **{name: value})
    assert (f["status"], f["rc"]) == ("ERROR", "2")
    assert f"--{name}" in proc.stdout


@pytest.mark.parametrize(
    ("extra", "named"),
    [
        (("--margin", "nan"), "--margin"),
        (("--margin", "-0.1"), "--margin"),
        (("--margin", "1"), "--margin"),
        (("--serial-lo", "0"), "--serial-lo"),
        (("--serial-hi", "inf"), "--serial-hi"),
        (("--serial-lo", "3000", "--serial-hi", "2000"), "--serial-lo"),
    ],
)
def test_e18_a_margin_or_band_outside_its_range_is_a_usage_error(
    tmp_path: Path, extra: tuple, named: str
) -> None:
    proc, f = run(tmp_path, pars() + sers(), extra=extra)
    assert (f["status"], f["rc"]) == ("ERROR", "2")
    assert named in proc.stdout


def test_e19_a_serial_bracket_must_itself_be_a_clean_pass(tmp_path: Path) -> None:
    red = rec("serial", result=RED, rc=1)
    parallel_word = rec("serial", result=PAR_OK)
    pa, a = run(tmp_path, pars() + [red, rec("serial")])
    pb, b = run(tmp_path, pars() + [parallel_word, rec("serial")])
    assert a["status"] == "INDETERMINATE" and b["status"] == "INDETERMINATE"
    assert "record 11" in pa.stdout and "own result is not" in pa.stdout
    assert "record 11" in pb.stdout and "own result is not" in pb.stdout


def test_e20_the_sweep_line_rc_must_agree_with_its_status(tmp_path: Path) -> None:
    bad = "RESULT: PASS rc=1 runs=10/10 rows=40 varying=0 subject=stable\n"
    proc, f = run(tmp_path, pars() + sers(), sweep=bad)
    assert f["status"] == "INDETERMINATE" and "does not agree" in proc.stdout


def test_e20b_two_anchored_sweep_lines_are_indeterminate(tmp_path: Path) -> None:
    red = "RESULT: FAIL rc=1 runs=10/10 rows=40 varying=2 subject=stable\n"
    proc, f = run(tmp_path, pars() + sers(), sweep=red + GOOD_SWEEP)
    assert f["status"] == "INDETERMINATE" and "anchored RESULT lines" in proc.stdout


def test_e20c_the_sweep_verdict_word_indeterminate_is_named(tmp_path: Path) -> None:
    line = "RESULT: INDETERMINATE rc=4 runs=10/10 rows=0 varying=0 subject=stable\n"
    proc, f = run(tmp_path, pars() + sers(), sweep=line)
    assert f["status"] == "INDETERMINATE" and "status INDETERMINATE" in proc.stdout
    assert "no anchored flake-sweep" not in proc.stdout


def test_e21_a_result_field_must_hold_exactly_one_result_line(tmp_path: Path) -> None:
    two = rec("parallel", result=PAR_OK + " RESULT: FAIL rc=1")
    ambiguous = rec("parallel", result="AMBIGUOUS: 2 anchored RESULT lines")
    pa, a = run(tmp_path, pars(9) + [two] + sers())
    _, b = run(tmp_path, pars(9) + [ambiguous] + sers())
    assert a["status"] == "INDETERMINATE" and b["status"] == "INDETERMINATE"
    assert "no single anchored RESULT line" in pa.stdout


def test_e22_the_plausibility_floor_has_a_fixture_on_each_side(tmp_path: Path) -> None:
    proc, low = run(tmp_path, pars(9) + [rec("parallel", wall=59)] + sers())
    _, at = run(tmp_path, pars(9) + [rec("parallel", wall=60)] + sers())
    assert low["status"] == "INDETERMINATE" and "plausibility floor" in proc.stdout
    assert at["status"] == "PASS"


def test_e15_the_sustained_rule_has_a_fixture_on_each_side_of_k(tmp_path: Path) -> None:
    near = rec("parallel", result=RED, rc=1, fmax=5.0, nover=11, maxrun=11)
    at = rec("parallel", result=RED, rc=1, fmax=5.0, nover=12, maxrun=12)
    expired = rec("parallel", result=RED, rc=1, launchwait="expired")
    _, below = run(tmp_path, pars(9) + [near] + sers())
    _, equal = run(tmp_path, pars(9) + [at] + sers())
    _, wait = run(tmp_path, pars(9) + [expired] + sers())
    assert (below["status"], below["loaded"]) == ("FAIL", "0")
    assert equal["status"] == "INDETERMINATE"
    assert wait["status"] == "INDETERMINATE"


def test_e15b_a_sampler_record_inconsistent_with_the_threshold_is_indeterminate(
    tmp_path: Path,
) -> None:
    quiet_but_over = rec("parallel", fmax=0.5, nover=3, maxrun=3)
    loud_but_never_over = rec("parallel", fmax=5.0, nover=0, maxrun=0)
    _, a = run(tmp_path, pars(9) + [quiet_but_over] + sers())
    _, b = run(tmp_path, pars(9) + [loud_but_never_over] + sers())
    assert a["status"] == "INDETERMINATE" and b["status"] == "INDETERMINATE"

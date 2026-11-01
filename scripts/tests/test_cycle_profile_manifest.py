"""Tests for the cycle-profile manifest module (recorded-figure rows, D2.4).

Every profile here is a hand-built dict following the Profile layout v1 in
`cycle_profile_model`; no transcript is read. Figures are invented and the fictional cycle
prefix is `2030-01-01`. The sub-rows t29a to t29i each pin one behaviour so that a
mutation campaign can cite exactly one of them.
"""

import copy
import json
import math
import re
import stat
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import cycle_profile_manifest as mf  # noqa: E402, I001

EXAMPLE = (
    Path(__file__).resolve().parent / "fixtures" / "cycle_profile_example_manifest.json"
)
PFX = "2030-01-01T00:00"


def _profile():
    """A hand-built profile; two cycles, one pool, every selector family resolvable."""
    return {
        "cycles": {
            "completed": 2,
            "partial": [],
            "open": [],
            "list": [
                {
                    "start_utc": "2030-01-01T00:00:00.000Z",
                    "wall_s": 3600.0,
                    "suite_runs": 2,
                    "stages": [
                        {
                            "stage": "execute",
                            "classes_s": {"SUBAGENT": 1200.0, "CONTROLLER": 300.0},
                        },
                        {"stage": "gates", "classes_s": {"MACHINE_WAIT": 600.0}},
                    ],
                },
                {
                    "start_utc": "2030-01-01T06:30:00.000Z",
                    "wall_s": 1800.0,
                    "suite_runs": 1,
                    "stages": [],
                },
            ],
        },
        "pool": {
            "wall_all_s": 36000.0,
            "wall_active_s": 30000.0,
            "away_s": 6000.0,
            "classes_s": {"SUBAGENT": 12000.0, "HOOK": 1800.0, "CONTROLLER": 9000.0},
            "halves": {
                "active": {
                    "work_pct": 55.0,
                    "gate_pct": 30.0,
                    "operator_pct": 10.0,
                    "unsplit_pct": 5.0,
                },
                "all": {
                    "work_pct": 50.0,
                    "gate_pct": 25.0,
                    "operator_pct": 20.0,
                    "unsplit_pct": 5.0,
                },
            },
            "answer": "gate",
            "corners": [{"n": 1, "s": 600.0}, {"n": 2, "s": 300.0}],
        },
        "idle": {"gaps": [100.0, 200.0, 300.0, 400.0, 500.0]},
        "fence": {"inner_s": 350.0},
        "delegates": {"matched": 12, "total": 14},
    }


def _row(selector="wall.all_min", recorded=600.0, unit="min", **extra):
    row = {"name": "n", "recorded": recorded, "selector": selector, "unit": unit}
    row.update(extra)
    return row


def _clock(recorded=5, local_start="2029-12-31T19:00:00", prefix=PFX, **extra):
    row = {
        "name": "z",
        "kind": "clock",
        "cycle": prefix,
        "recorded": recorded,
        "local_start": local_start,
    }
    row.update(extra)
    return row


ALL_SELECTORS = [
    ("cycles.completed", "count", 2),
    ("wall.all_min", "min", 600.0),
    ("wall.active_min", "min", 500.0),
    ("away.min", "min", 100.0),
    ("class.SUBAGENT.min", "min", 200.0),
    ("half.active.work_pct", "pct", 55.0),
    ("half.active.gate_pct", "pct", 30.0),
    ("half.active.operator_pct", "pp", 10.0),
    ("answer", "token", "gate"),
    ("fence.inner_s", "s", 350.0),
    ("idle.gaps", "count", 5),
    ("coverage.matched", "count", 12),
    ("corner.1.min", "min", 10.0),
    ("cycle[%s].wall_min" % PFX, "min", 60.0),
    ("cycle[%s].suite_runs" % PFX, "count", 2),
    ("cycle[%s].stage.execute.SUBAGENT.min" % PFX, "min", 20.0),
]


# ---------------------------------------------------------------------------------------------
# t29a: validation runs first and names the row index only
# ---------------------------------------------------------------------------------------------
BAD_ROWS = [
    ("class.EVIL.min", {"selector": "class.EVIL.min"}),
    ("unit-outside-set", {"unit": "hours"}),
    ("recorded-text", {"recorded": "ten"}),
    ("recorded-bool", {"recorded": True}),
    ("recorded-nan", {"recorded": float("nan")}),
    ("answer-number", {"selector": "answer", "unit": "token", "recorded": 5}),
    ("answer-bad-token", {"selector": "answer", "unit": "token", "recorded": "maybe"}),
    ("number-selector-token", {"recorded": "gate"}),
    ("trailing-newline", {"selector": "wall.all_min\n"}),
    ("prefix-letters", {"selector": "cycle[20x0].wall_min"}),
    ("prefix-unicode-digits", {"selector": "cycle[２０３０].wall_min"}),
    ("prefix-too-long", {"selector": "cycle[2030-01-01T00:00:00:00].wall_min"}),
    ("corner-8", {"selector": "corner.8.min"}),
    ("corner-0", {"selector": "corner.0.min"}),
    ("stage-unknown", {"selector": "cycle[2030-01-01].stage.bogus.HOOK.min"}),
    ("unit-incompatible", {"selector": "away.min", "unit": "pct"}),
    ("unknown-key", {"extra": 1}),
    ("tolerance-negative", {"tolerance": -1}),
    ("tolerance-text", {"tolerance": "2"}),
    (
        "tolerance-on-token",
        {"selector": "answer", "unit": "token", "recorded": "gate", "tolerance": 1},
    ),
    ("independence-bad", {"independence": "maybe"}),
    ("gate-text", {"gate": "true"}),
    ("kind-bad", {"kind": "other"}),
    ("recorded-huge-int", {"recorded": 10**400}),
    ("tolerance-huge-int", {"tolerance": 10**400}),
    ("not-a-dict", None),
]


@pytest.mark.parametrize("label,patch", BAD_ROWS, ids=[b[0] for b in BAD_ROWS])
def test_t29a_validation_names_the_row_index(label, patch):
    good = _row()
    rows = [good, copy.deepcopy(good), None]
    rows[2] = patch if patch is None else dict(_row(), **patch)
    with pytest.raises(mf.ManifestError) as exc:
        mf.validate_rows(rows)
    assert exc.value.index == 2


def test_t29a_validation_reports_the_first_bad_index_and_no_text():
    secret = "zzsecretzz"
    rows = [_row(), _row(selector="class.%s.min" % secret), _row(unit=secret)]
    with pytest.raises(mf.ManifestError) as exc:
        mf.validate_rows(rows)
    assert exc.value.index == 1
    assert secret not in str(exc.value) and secret not in repr(exc.value)
    rows = [_row(), _row(), _row(selector="x" + secret)]
    with pytest.raises(mf.ManifestError) as exc:
        mf.validate_rows(rows)
    assert exc.value.index == 2


def test_t29a_validation_accepts_the_whole_grammar_and_returns_the_rows():
    rows = [_row(s, r, u) for s, u, r in ALL_SELECTORS] + [_clock()]
    out = mf.validate_rows(rows)
    assert isinstance(out, list) and len(out) == len(rows)
    assert [r["selector"] for r in out[:-1]] == [s for s, _u, _r in ALL_SELECTORS]


def test_t29a_the_manifest_must_be_a_list_and_validation_precedes_evaluation():
    with pytest.raises(mf.ManifestError):
        mf.validate_rows({"selector": "wall.all_min"})
    # evaluation never reads a profile before the rows validated
    with pytest.raises(mf.ManifestError) as exc:
        mf.evaluate_manifest(_profile(), [_row(), _row(unit="bogus")])
    assert exc.value.index == 1


# ---------------------------------------------------------------------------------------------
# t29b: the counts sum to the rows, report lines are by selector
# ---------------------------------------------------------------------------------------------
def test_t29b_counts_sum_to_the_rows():
    rows = [
        _row("wall.all_min", 600.0),  # agree
        _row("away.min", 200.0),  # disagree (derived 100)
        _row("cycle[2031-01-01].wall_min", 60.0),  # no such cycle
        _row("answer", "gate", "token"),  # agree
    ]
    res = mf.evaluate_manifest(_profile(), rows)
    assert (res["agree"], res["disagree"], res["unmeasurable"]) == (2, 1, 1)
    assert res["agree"] + res["disagree"] + res["unmeasurable"] == res["rows"] == 4
    assert mf.summarize(res) == "MANIFEST rows=4 agree=2 disagree=1 unmeasurable=1"
    verdicts = [i["verdict"] for i in res["items"]]
    assert verdicts == ["AGREE", "DISAGREE", "unmeasurable", "AGREE"]


def test_t29b_report_lines_are_by_selector_and_never_by_name():
    rows = [
        _row("wall.all_min", 600.0, name="NAMEMARKONE"),
        _row("away.min", 200.0, name="NAMEMARKTWO"),
    ]
    res = mf.evaluate_manifest(_profile(), rows)
    text = "\n".join(mf.detail_lines(res)) + mf.summarize(res) + json.dumps(res)
    assert "NAMEMARK" not in text
    lines = mf.detail_lines(res)
    assert any("wall.all_min" in ln and "AGREE" in ln for ln in lines)
    assert any("away.min" in ln and "DISAGREE" in ln for ln in lines)


# ---------------------------------------------------------------------------------------------
# t29c: the default tolerance max(2 min, 10% of recorded) at exact equality
# ---------------------------------------------------------------------------------------------
def test_t29c_tolerance_boundary_ten_percent():
    row = mf.validate_rows([_row(recorded=100.0)])[0]  # tolerance 10.0
    assert mf.compare(row, 110.0) == "AGREE"
    assert mf.compare(row, 90.0) == "AGREE"
    assert mf.compare(row, 110.0000001) == "DISAGREE"
    assert mf.compare(row, 89.9999999) == "DISAGREE"
    assert mf.compare(row, 115.0) == "DISAGREE"  # a 20% rule would agree here


def test_t29c_tolerance_floor_is_two_minutes():
    row = mf.validate_rows([_row(recorded=10.0)])[0]  # 10% is 1.0, the floor is 2.0
    assert mf.compare(row, 12.0) == "AGREE"
    assert mf.compare(row, 12.0000001) == "DISAGREE"
    assert mf.compare(row, 7.9999999) == "DISAGREE"


@pytest.mark.parametrize(
    "unit,recorded,edge",
    [("s", 1000.0, 120.0), ("pct", 10.0, 2.0), ("pp", 10.0, 2.0), ("count", 10, 2.0)],
)
def test_t29c_the_floor_is_two_in_the_rows_own_unit(unit, recorded, edge):
    sel = {
        "s": "fence.inner_s",
        "pct": "half.active.work_pct",
        "pp": "half.active.work_pct",
        "count": "idle.gaps",
    }[unit]
    row = mf.validate_rows([_row(sel, recorded, unit)])[0]
    assert mf.compare(row, recorded + edge) == "AGREE"
    assert mf.compare(row, recorded + edge + 0.001) == "DISAGREE"


def test_t29c_token_rows_compare_for_equality():
    row = mf.validate_rows([_row("answer", "gate", "token")])[0]
    assert mf.compare(row, "gate") == "AGREE"
    assert mf.compare(row, "work") == "DISAGREE"


def test_t29c_derived_is_converted_into_the_rows_unit():
    # derived 600 min, recorded 36000 s: a seconds row sees 36000
    res = mf.evaluate_manifest(_profile(), [_row("wall.all_min", 36000.0, "s")])
    assert res["items"][0]["verdict"] == "AGREE"
    assert res["items"][0]["derived"] == pytest.approx(36000.0)
    res = mf.evaluate_manifest(_profile(), [_row("fence.inner_s", 5.5, "min")])
    assert res["items"][0]["derived"] == pytest.approx(350.0 / 60.0)


# ---------------------------------------------------------------------------------------------
# t29d: a row's own tolerance overrides the default
# ---------------------------------------------------------------------------------------------
def test_t29d_row_tolerance_narrower_than_default():
    row = mf.validate_rows([_row(recorded=100.0, tolerance=1.0)])[0]
    assert mf.compare(row, 100.9) == "AGREE"
    assert mf.compare(row, 101.0) == "AGREE"
    assert mf.compare(row, 101.5) == "DISAGREE"  # the default 10.0 would agree


def test_t29d_row_tolerance_wider_than_default():
    row = mf.validate_rows([_row(recorded=100.0, tolerance=30)])[0]
    assert mf.compare(row, 125.0) == "AGREE"  # the default would disagree
    assert mf.compare(row, 130.0) == "AGREE"
    assert mf.compare(row, 130.5) == "DISAGREE"


def test_t29d_zero_tolerance_means_equality():
    row = mf.validate_rows([_row(recorded=100.0, tolerance=0)])[0]
    assert mf.compare(row, 100.0) == "AGREE"
    assert mf.compare(row, 100.001) == "DISAGREE"


# ---------------------------------------------------------------------------------------------
# t29e: an uncomputable row is unmeasurable and never AGREE
# ---------------------------------------------------------------------------------------------
@pytest.mark.parametrize("derived", [None, float("nan"), math.inf, True, "x"])
def test_t29e_uncomputable_derived_is_unmeasurable(derived):
    row = mf.validate_rows([_row(recorded=0.0)])[0]
    assert mf.compare(row, derived) == "unmeasurable"


def test_t29e_token_row_with_unknown_derived_is_unmeasurable():
    row = mf.validate_rows([_row("answer", "gate", "token")])[0]
    assert mf.compare(row, None) == "unmeasurable"
    assert mf.compare(row, "bogus") == "unmeasurable"


@pytest.mark.parametrize(
    "sel,unit,rec", ALL_SELECTORS, ids=[s[0] for s in ALL_SELECTORS]
)
@pytest.mark.parametrize(
    "profile",
    [{}, {"pool": 5, "cycles": [], "idle": "x"}],
    ids=["empty", "wrong-types"],
)
def test_t29e_every_selector_on_an_unreadable_profile_is_unmeasurable(
    sel, unit, rec, profile
):
    res = mf.evaluate_manifest(profile, [_row(sel, rec, unit)])
    assert res["items"][0]["verdict"] == "unmeasurable"
    assert res["agree"] == 0 and res["unmeasurable"] == 1


def test_t29e_a_prefix_matching_zero_or_several_cycles_is_unmeasurable():
    prof = _profile()
    zero = _row("cycle[2031-01-01].wall_min", 60.0)
    several = _row("cycle[2030-01-01].wall_min", 60.0)  # matches both cycles
    one = _row("cycle[2030-01-01T06].wall_min", 30.0)
    res = mf.evaluate_manifest(prof, [zero, several, one])
    assert [i["verdict"] for i in res["items"]] == [
        "unmeasurable",
        "unmeasurable",
        "AGREE",
    ]


def test_t29e_missing_stage_corner_and_class_are_unmeasurable():
    rows = [
        _row("cycle[%s].stage.plan-author.HOOK.min" % PFX, 0.0),
        _row("cycle[%s].stage.execute.HOOK.min" % PFX, 0.0),
        _row("corner.5.min", 0.0),
        _row("class.AWAY.min", 0.0),
    ]
    res = mf.evaluate_manifest(_profile(), rows)
    assert [i["verdict"] for i in res["items"]] == ["unmeasurable"] * 4


# ---------------------------------------------------------------------------------------------
# t29f: gate
# ---------------------------------------------------------------------------------------------
def _check(rows):
    res = mf.evaluate_manifest(_profile(), rows)
    return mf.manifest_check(res)


def test_t29f_gated_disagree_is_a_fail():
    chk = _check([_row("away.min", 200.0, gate=True)])
    assert chk.name == "MANIFEST" and chk.verdict == "fail"


def test_t29f_gated_unmeasurable_is_a_fail():
    chk = _check([_row("cycle[2031-01-01].wall_min", 1.0, gate=True)])
    assert chk.verdict == "fail"


def test_t29f_ungated_rows_only_report():
    chk = _check([_row("away.min", 200.0), _row("cycle[2031-01-01].wall_min", 1.0)])
    assert chk.verdict == "pass"
    assert "disagree=1" in chk.detail and "unmeasurable=1" in chk.detail


def test_t29f_gated_agreeing_rows_pass_and_false_is_the_default():
    chk = _check(
        [_row("wall.all_min", 600.0, gate=True), _row("away.min", 1.0, gate=False)]
    )
    assert chk.verdict == "pass"


def test_t29f_no_manifest_is_a_skip():
    assert mf.manifest_check(None).verdict == "skip"


# ---------------------------------------------------------------------------------------------
# t29g: independence counts, FLOOR-NOTE, largest delta
# ---------------------------------------------------------------------------------------------
def _g_rows():
    return [
        _row("wall.all_min", 600.0, independence="independent", name="NAMEMARK"),
        _row("corner.1.min", 11.0, independence="independent"),  # floor row, delta 1
        _row("corner.2.min", 4.0, independence="guaranteed"),  # floor row, delta -1
        _row("class.SUBAGENT.min", 230.0, tolerance=40),  # own tolerance, delta -30
        _row("cycle[2031-01-01].wall_min", 5.0),  # unmeasurable
    ]


def test_t29g_independence_counts():
    res = mf.evaluate_manifest(_profile(), _g_rows())
    assert (res["independent"], res["guaranteed"]) == (2, 1)
    assert res["unlabelled"] == 2
    other = [
        dict(r, independence="guaranteed") if "independence" in r else r
        for r in _g_rows()
    ]
    res = mf.evaluate_manifest(_profile(), other)
    assert (res["independent"], res["guaranteed"]) == (0, 3)


def test_t29g_floor_note_counts_default_tolerance_rows_at_or_under_the_floor():
    res = mf.evaluate_manifest(_profile(), _g_rows())
    # measurable: 4; floor-decided: corner 1 (11) and corner 2 (4); wall 600 and the
    # own-tolerance row are not
    assert res["measurable"] == 4 and res["floor_decided"] == 2
    lines = mf.detail_lines(res)
    assert any(ln.startswith("FLOOR-NOTE 2 of 4") for ln in lines)
    rows = _g_rows()[:2]
    res = mf.evaluate_manifest(_profile(), rows)
    assert res["floor_decided"] == 1 and res["measurable"] == 2
    # a recorded figure exactly at 20 min is at the floor
    res = mf.evaluate_manifest(_profile(), [_row("class.HOOK.min", 20.0, "min")])
    assert res["floor_decided"] == 1
    res = mf.evaluate_manifest(_profile(), [_row("class.HOOK.min", 20.5, "min")])
    assert res["floor_decided"] == 0


def test_t29g_largest_absolute_delta_is_printed():
    res = mf.evaluate_manifest(_profile(), _g_rows())
    big = res["largest_delta"]
    assert big["selector"] == "class.SUBAGENT.min"
    assert big["delta"] == pytest.approx(-30.0)
    lines = mf.detail_lines(res)
    assert any(ln.startswith("LARGEST-DELTA class.SUBAGENT.min") for ln in lines)
    none = mf.evaluate_manifest(_profile(), [_row("cycle[2031-01-01].wall_min", 5.0)])
    assert none["largest_delta"] is None


def test_t29g_name_is_never_rendered():
    res = mf.evaluate_manifest(_profile(), _g_rows())
    text = "\n".join(mf.detail_lines(res)) + mf.summarize(res) + json.dumps(res)
    assert "NAMEMARK" not in text


# ---------------------------------------------------------------------------------------------
# t29h: clock rows and the ZONE check
# ---------------------------------------------------------------------------------------------
def _zone(rows):
    res = mf.evaluate_manifest(_profile(), rows)
    return res, mf.zone_check(res)


def test_t29h_whole_hour_offset_agrees():
    res, chk = _zone([_clock(5, local_end="2029-12-31T20:00:00")])
    assert res["items"][0]["verdict"] == "AGREE"
    assert chk.name == "ZONE" and chk.verdict == "pass"


def test_t29h_residual_boundary_is_one_hundred_twenty_seconds():
    # local readings late by 120 s: offset 4h59m58... residual exactly 120 s
    _res, chk = _zone([_clock(5, local_start="2029-12-31T19:02:00")])
    assert chk.verdict == "pass"
    res, chk = _zone([_clock(5, local_start="2029-12-31T19:02:01")])
    assert res["items"][0]["verdict"] == "DISAGREE" and chk.verdict == "fail"


def test_t29h_start_and_end_must_agree_on_the_hour():
    # the end reading is a whole hour out relative to the start
    res, chk = _zone([_clock(5, local_end="2029-12-31T21:00:00")])
    assert res["items"][0]["verdict"] == "DISAGREE" and chk.verdict == "fail"


def test_t29h_recorded_zone_must_match_the_measured_hour():
    res, chk = _zone([_clock(6)])
    assert res["items"][0]["verdict"] == "DISAGREE" and chk.verdict == "fail"
    res, chk = _zone([_clock(4, local_start="2029-12-31T20:00:00")])
    assert res["items"][0]["verdict"] == "AGREE" and chk.verdict == "pass"


def test_t29h_spread_across_rows_over_the_limit_fails():
    second = _clock(5, local_start="2029-12-31T19:00:00", prefix="2030-01-01T06")
    # second cycle starts 06:30:00Z; a local 01:30:00 is a 5 h offset
    second["local_start"] = "2030-01-01T01:30:00"
    ok = _clock(5)
    _res, chk = _zone([ok, second])
    assert chk.verdict == "pass"
    second["local_start"] = "2030-01-01T01:27:30"  # 150 s late: residual 150
    _res, chk = _zone([ok, second])
    assert chk.verdict == "fail"
    # residuals of 90 s each, in opposite directions: each row passes alone
    second["local_start"] = "2030-01-01T01:31:30"
    first = _clock(5, local_start="2029-12-31T18:58:30")
    res, chk = _zone([first, second])
    assert [i["verdict"] for i in res["items"]] == ["AGREE", "AGREE"]
    assert chk.verdict == "fail"  # but their spread is 180 s


def test_t29h_no_clock_rows_is_a_skip():
    _res, chk = _zone([_row()])
    assert chk.verdict == "skip"
    assert mf.zone_check(None).verdict == "skip"


def test_t29h_unmeasurable_clock_row_is_not_a_pass():
    res, chk = _zone([_clock(5, prefix="2031-01-01")])
    assert res["items"][0]["verdict"] == "unmeasurable"
    assert chk.verdict != "pass"
    # gated, it is also a MANIFEST fail
    res = mf.evaluate_manifest(_profile(), [_clock(5, prefix="2031-01-01", gate=True)])
    assert mf.manifest_check(res).verdict == "fail"


@pytest.mark.parametrize(
    "patch",
    [
        {"local_start": "2029-12-31 19:00:00"},
        {"local_start": "2029-13-31T19:00:00"},
        {"local_end": "soon"},
        {"recorded": 99},
        {"recorded": 10**400},
        {"recorded": 5.5},
        {"cycle": "20x"},
        {"selector": "wall.all_min"},
    ],
)
def test_t29h_clock_row_validation(patch):
    with pytest.raises(mf.ManifestError) as exc:
        mf.validate_rows([_row(), dict(_clock(), **patch)])
    assert exc.value.index == 1


# ---------------------------------------------------------------------------------------------
# t29i: the committed example manifest
# ---------------------------------------------------------------------------------------------
def test_t29i_example_manifest_validates_and_every_row_resolves():
    rows = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    assert isinstance(rows, list) and len(rows) >= 17
    validated = mf.validate_rows(rows)
    assert len(validated) == len(rows)
    res = mf.evaluate_manifest(_profile(), rows)
    assert res["unmeasurable"] == 0
    assert res["agree"] == res["rows"] == len(rows)
    assert mf.manifest_check(res).verdict == "pass"
    assert mf.zone_check(res).verdict == "pass"


def test_t29i_example_manifest_covers_the_grammar_and_is_synthetic():
    text = EXAMPLE.read_text(encoding="utf-8")
    rows = json.loads(text)
    selectors = {r.get("selector") for r in rows}
    for sel, _unit, _rec in ALL_SELECTORS:
        assert sel in selectors, sel
    assert any(r.get("kind") == "clock" for r in rows)
    dates = set(re.findall(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", text))
    assert "2030-01-01" in dates
    assert dates <= {"2029-12-31", "2030-01-01"}  # the fictional day and its eve
    assert stat.S_IMODE(EXAMPLE.stat().st_mode) == 0o644


# ---------------------------------------------------------------------------------------------
# t29c (float slack): a derived figure at the tolerance edge agrees despite float noise
# ---------------------------------------------------------------------------------------------
def test_t29c_seconds_derived_figure_at_the_boundary_agrees():
    # 264.0 s is 4.4 min; recorded 2.4 min; the delta is 2.0000000000000004 in floats
    res = mf.evaluate_manifest(
        {"pool": {"wall_all_s": 264.0}}, [_row("wall.all_min", 2.4, "min")]
    )
    assert res["items"][0]["verdict"] == "AGREE"
    res = mf.evaluate_manifest(
        {"pool": {"wall_all_s": 264.0 + 0.06}}, [_row("wall.all_min", 2.4, "min")]
    )
    assert res["items"][0]["verdict"] == "DISAGREE"  # 0.001 min beyond the edge


def test_t29c_boundary_sweep_over_recorded_values():
    wrong = []
    for k in range(1, 3000):
        rec = k / 10.0
        tol = max(2.0, 0.10 * rec)
        for sign in (1, -1):
            edge = rec + sign * tol
            if edge < 0:
                continue
            row = _row("wall.all_min", rec, "min")
            at = mf.evaluate_manifest({"pool": {"wall_all_s": edge * 60.0}}, [row])
            beyond = edge + sign * 1e-6  # far above the 1e-9 slack, in minutes
            out = mf.evaluate_manifest({"pool": {"wall_all_s": beyond * 60.0}}, [row])
            if at["items"][0]["verdict"] != "AGREE":
                wrong.append((rec, sign, "edge"))
            if out["items"][0]["verdict"] != "DISAGREE":
                wrong.append((rec, sign, "beyond"))
    assert wrong == []

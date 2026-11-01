"""Tests for the cycle-profile run module: the one read, the profile, the floors, the verdict.

Every transcript and delegate file is synthetic (fictional base date 2030-01-01, invented
ids, lorem text), built from `cycle_profile_helpers`. The known answers are spec 6.3: K1 has
classes MACHINE_WAIT 1650, SUBAGENT 300, HOOK 30, OPERATOR_RESPONSE 60, AWAY 0 and
CONTROLLER 390 over 2430 s, and with the A1 delegate file shares 23.0 / 74.5 / 2.5 (K1w
21.8 / 74.5 / 3.7) with `COVERAGE matched=1/2`. The delegate files of a run sit where the
module looks by default: `<transcript dir>/<stem>/subagents`.
"""

import copy
import hashlib
import json
import math
import re
import statistics
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import cycle_profile_checks as ck  # noqa: E402, I001
import cycle_profile_delegates as dg  # noqa: E402
import cycle_profile_helpers as h  # noqa: E402
import cycle_profile_manifest as mf  # noqa: E402
import cycle_profile_model as m  # noqa: E402
import cycle_profile_records as r  # noqa: E402
import cycle_profile_run as run  # noqa: E402

LEAK_FIELDS = ("leak_samples", "leak_floor", "leak_seed")
RUN_FIELDS = tuple(f for f in m.Params._fields if f not in LEAK_FIELDS)
TOP_KEYS = {
    "tool",
    "input",
    "params",
    "shapes",
    "versions",
    "floors",
    "cycles",
    "pool",
    "idle",
    "fence",
    "delegates",
    "sensitivity",
    "checks",
    "manifest",
    "leak",
    "limits",
    "not_measured",
    "indicators",
}


def agents_dir(path):
    """Where the module looks for delegate files by default."""
    return path.parent / path.stem / "subagents"


def both_agents():
    """The A1 delegate file under both agent ids (every dispatch matched)."""
    data = h.k1_delegate_files()[h.AGENT1]
    return {h.AGENT1: data, h.AGENT2: data}


def agree_ids(data):
    """Give each K1 notification the task id of its agent, as a real session does.

    The K1 helper acknowledges A1 with one id and notifies it under the task id `t1`, so
    MAPCHECK (ack against notification) would read the fixture's own disagreement.
    """
    for task, agent in ((b"t1", h.AGENT1), (b"t2", h.AGENT2)):
        data = data.replace(
            b"<task-id>%s</task-id>" % task, b"<task-id>%s</task-id>" % agent.encode()
        )
    return data


def k1_run(tmp_path, variant="skill", files="a1", params=None, **kw):
    """Run K1 (or K1w) with the A1 delegate directory and `min_cycles=1`."""
    path = h.write_transcript(tmp_path, agree_ids(h.k1_transcript(variant)))
    if files == "a1":
        files = h.k1_delegate_files()
    if files is not None:
        h.write_delegate_dir(agents_dir(path), files)
    use = m.Params(min_cycles=1) if params is None else params
    return run.run_profile(path, use, **kw)


def run_events(tmp_path, events, params=None, **kw):
    path = h.write_transcript(tmp_path, h.build_transcript(events))
    return run.run_profile(path, m.Params() if params is None else params, **kw)


def floor_of(profile, name):
    rows = [f for f in profile["floors"] if f["name"] == name]
    assert len(rows) == 1
    return rows[0]


def suite(n, art, label, launch, terminal):
    """A suite-class run-long launch and a `--wait` whose notification lands at `terminal`."""
    return [
        h.runlong_launch(launch, f"l{n}", art, label),
        h.result(launch + 1, f"l{n}"),
        h.runlong_wait(launch + 2, f"w{n}", art),
        h.result(launch + 3, f"w{n}"),
        h.enqueue(terminal, f"w{n}", f"t{n}", 1000),
    ]


def feature(at):
    return h.wrapper(at, "feature")


def same(got, want):
    """Structural equality with floats compared to a relative 1e-6 (approx has no nesting)."""
    if isinstance(want, dict):
        return (
            isinstance(got, dict)
            and set(got) == set(want)
            and all(same(got[k], want[k]) for k in want)
        )
    if isinstance(want, list):
        return (
            isinstance(got, list)
            and len(got) == len(want)
            and all(same(a, b) for a, b in zip(got, want))  # noqa: B905
        )
    if isinstance(want, float):
        return isinstance(got, (int, float)) and got == pytest.approx(want, rel=1e-6)
    return got == want


# ---------------------------------------------------------------------------------------------
# t14r: an empty or unreadable-as-records transcript
# ---------------------------------------------------------------------------------------------


def test_t14r_empty_file_is_incomplete_with_every_reason(tmp_path):
    path = h.write_transcript(tmp_path, b"")
    pf = run.run_profile(path, m.Params())
    assert pf["input"]["records"] == 0
    assert pf["cycles"]["completed"] == 0
    verdict, rc, _, fail, _ = run.derive_verdict(pf)
    assert (verdict, rc, fail) == ("INCOMPLETE", 3, 0)
    # a zero denominator is no pass: every reason is on the list
    assert run.incomplete_reasons(pf) == [
        "no-completed-cycle",
        "cycles-under-min",
        "unparseable-over-cap",
        "timestamped-under-floor",
        "shape-absent",
    ]


def test_t14r_empty_file_does_not_meet_the_unparseable_floor(tmp_path):
    pf = run.run_profile(h.write_transcript(tmp_path, b""), m.Params())
    row = floor_of(pf, "max_unparseable_pct")
    assert (row["actual"], row["ok"]) == (None, False)
    assert "unparseable-over-cap" in run.incomplete_reasons(pf)


def test_t14r_empty_file_does_not_meet_the_timestamped_floor(tmp_path):
    pf = run.run_profile(h.write_transcript(tmp_path, b""), m.Params())
    row = floor_of(pf, "min_timestamped_pct")
    assert (row["actual"], row["ok"]) == (None, False)
    assert "timestamped-under-floor" in run.incomplete_reasons(pf)


def test_t14r_zero_cycles_is_incomplete_even_with_min_cycles_zero(tmp_path):
    # a first invocation that never closed, every critical shape present, every floor met
    events = [feature(0), h.say(10), h.bash(20, "b1", "ls"), h.result(25, "b1")]
    pf = run_events(tmp_path, events, m.Params(min_cycles=0))
    assert all(f["ok"] for f in pf["floors"])
    assert run.incomplete_reasons(pf) == ["no-completed-cycle"]
    assert run.derive_verdict(pf)[:2] == ("INCOMPLETE", 3)


def test_t14r_garbage_only_is_incomplete(tmp_path):
    path = h.write_transcript(tmp_path, b"not json\n{{{\n")
    pf = run.run_profile(path, m.Params())
    assert pf["cycles"]["completed"] == 0
    # two non-blank lines, none of them a record
    assert pf["input"]["records"] == 2
    assert pf["input"]["parsed"] == 0
    assert pf["input"]["unparseable"] == 2
    verdict, rc, _, _, _ = run.derive_verdict(pf)
    assert (verdict, rc) == ("INCOMPLETE", 3)
    assert "unparseable-over-cap" in run.incomplete_reasons(pf)


def test_t14r_empty_file_zero_denominators_are_none(tmp_path):
    path = h.write_transcript(tmp_path, b"")
    pf = run.run_profile(path, m.Params())
    assert pf["input"]["timestamped_pct"] is None
    assert floor_of(pf, "max_unparseable_pct")["actual"] is None
    assert floor_of(pf, "min_timestamped_pct")["actual"] is None
    active = pf["pool"]["halves"]["active"]
    assert active["denominator_s"] == 0
    assert [active[k] for k in ("work_pct", "gate_pct", "operator_pct")] == [None] * 3
    assert pf["pool"]["answer"] == "undetermined"


# ---------------------------------------------------------------------------------------------
# t15r: records but no /feature invocation
# ---------------------------------------------------------------------------------------------


def no_invocation_events():
    return [h.prompt(0), h.say(10), h.bash(20, "b1", "ls"), h.result(25, "b1")]


def test_t15r_no_invocation_is_incomplete(tmp_path):
    pf = run_events(tmp_path, no_invocation_events())
    assert pf["cycles"]["completed"] == 0
    assert (pf["cycles"]["partial"], pf["cycles"]["open"]) == ([], [])
    verdict, rc, _, _, _ = run.derive_verdict(pf)
    assert (verdict, rc) == ("INCOMPLETE", 3)
    assert "shape-absent" in run.incomplete_reasons(pf)
    assert {"what": "shape", "name": "feature_invocation"} == {
        k: v
        for k, v in next(
            x for x in pf["not_measured"] if x.get("name") == "feature_invocation"
        ).items()
        if k in ("what", "name")
    }


def test_t15r_no_invocation_still_writes_the_shape_table_and_census(tmp_path):
    pf = run_events(tmp_path, no_invocation_events())
    assert [s["name"] for s in pf["shapes"]] == list(m.SHAPES)
    by_name = {s["name"]: s for s in pf["shapes"]}
    assert by_name["feature_invocation"]["count"] == 0
    assert by_name["feature_invocation"]["class"] == "CRITICAL"
    assert by_name["bash_tool_use"]["count"] == 1
    assert [v["version"] for v in pf["versions"]] == [h.VERSION]
    assert pf["versions"][0]["records"] == 4


# ---------------------------------------------------------------------------------------------
# t16x: a CRITICAL shape absent is NOT MEASURED, never zero minutes
# ---------------------------------------------------------------------------------------------


def dropped_ack_run(tmp_path):
    """K1 without `toolUseResult`: Agent calls exist and no agent_ack does."""
    data = h.build_transcript(h.k1_events("skill"), drop_fields=["toolUseResult"])
    path = h.write_transcript(tmp_path, agree_ids(data))
    # the dispatches are then named by their task ids: give both a delegate file
    h.write_delegate_dir(agents_dir(path), both_agents())
    return run.run_profile(path, m.Params(min_cycles=1))


def test_t16x_critical_shape_absent_is_not_measured(tmp_path):
    pf = dropped_ack_run(tmp_path)
    entries = [x for x in pf["not_measured"] if x["what"] == "shape"]
    assert [(x["name"], x["reason"]) for x in entries] == [("agent_ack", "absent")]
    assert entries[0]["needed_by"] == "dispatch-agent-map"
    assert next(s for s in pf["shapes"] if s["name"] == "agent_ack")["count"] == 0


def test_t16x_critical_shape_absent_forces_incomplete(tmp_path):
    pf = dropped_ack_run(tmp_path)
    # every dispatch is matched and the floors hold: the shape is the only reason
    assert pf["delegates"]["matched"] == pf["delegates"]["total"] == 2
    assert run.incomplete_reasons(pf) == ["shape-absent"]
    assert run.derive_verdict(pf)[:2] == ("INCOMPLETE", 3)


def test_t16x_dispatch_shapes_are_critical_only_with_a_dispatch(tmp_path):
    events = [feature(0), h.bash(5, "b1", "ls"), h.result(6, "b1"), h.say(10)]
    pf = run_events(tmp_path, events)
    absent = {x["name"] for x in pf["not_measured"] if x["what"] == "shape"}
    assert not absent & {"agent_ack", "task_enqueue", "task_delivery"}
    assert next(s for s in pf["shapes"] if s["name"] == "agent_ack")["count"] == 0


def test_t16x_one_of_enqueue_and_delivery_is_enough(tmp_path):
    events = [e for e in h.k1_events("skill") if e["rec"]["type"] != "queue-operation"]
    pf = run_events(tmp_path, events, m.Params(min_cycles=1))
    shapes = {s["name"]: s["count"] for s in pf["shapes"]}
    assert shapes["task_enqueue"] == 0 and shapes["task_delivery"] > 0
    absent = {x["name"] for x in pf["not_measured"] if x["what"] == "shape"}
    assert "task_enqueue" not in absent and "task_delivery" not in absent


def test_t16x_cross_check_shape_absent_does_not_force_incomplete(tmp_path):
    pf = k1_run(tmp_path, files=both_agents())
    cross = [x for x in pf["not_measured"] if x["what"] == "cross-check"]
    assert ("hook_success", "shape-absent") in {(x["name"], x["reason"]) for x in cross}
    assert "shape-absent" not in run.incomplete_reasons(pf)


# ---------------------------------------------------------------------------------------------
# t48: every Params field run_profile consumes changes an observable
# ---------------------------------------------------------------------------------------------


def rerun_data():
    """Suite 100 to 1000, then a re-run launched 600 s after the verdict (700 s long)."""
    events = [feature(0)] + suite(1, "ka", "dev-tip aaa1111", 100, 1000)
    events += suite(2, "kb", "dev-tip bbb2222", 1600, 2300)
    return h.build_transcript(events)


def post_data():
    """Suite 100 to 1000 and a commit marker 1200 s after the verdict."""
    events = [feature(0)] + suite(1, "ka", "dev-tip aaa1111", 100, 1000)
    return h.build_transcript(events + [h.skill(2200, "mk", "commit")])


def short_suite_data():
    """One 480 s suite: not real at 10 minutes, real at 5."""
    return h.build_transcript(
        [feature(0)] + suite(1, "ka", "dev-tip aaa1111", 100, 580)
    )


def long_call_data():
    """K1 plus a plain foreground Bash call of 90 s inside its first 100 s."""
    extra = [h.bash(5, "bx", "sleep 90"), h.result(95, "bx")]
    return h.build_transcript(h.k1_events("skill") + extra)


def lane_data():
    """A fast-lane mention at 1 minute and a full-lane mention at 40 minutes."""
    events = [feature(0), h.say(60, "the fast lane suffices " + h.lorem(2))]
    events.append(h.say(2400, "now the full lane applies " + h.lorem(3)))
    return h.build_transcript(events)


def gaps_data():
    """Idle gaps of 10 to 90 s and one of 350 s, then one 700 s dev-tip suite."""
    events = [feature(0)]
    t = 10
    for gap in (10, 20, 30, 40, 50, 60, 70, 80, 90, 350):
        events += [h.say(t), h.prompt(t + gap)]
        t += gap + 10
    events += suite(1, "ka", "dev-tip aaa1111", t + 10, t + 710)
    return h.build_transcript(events)


def garbage_data():
    """K1 with one unparseable line appended."""
    return h.k1_transcript("skill") + b"garbage line\n"


def untimed_data():
    """K1 with fifteen records that carry no timestamp."""
    return h.build_transcript(
        h.k1_events("skill") + [h.say(1, ts=False) for _ in range(15)]
    )


def away_s(pf):
    return pf["pool"]["classes_s"]["AWAY"]


def wall(pf):
    return pf["cycles"]["list"][0]["wall_s"]


# field: (data builder, non-default value, observable, default reading, changed reading,
#         what the observable is)
CASES = {
    "close_rerun_min": (rerun_data, 7, wall, 2300.0, 1000.0, "wall of cycle 0"),
    "close_post_min": (post_data, 10, wall, 2200.0, 1000.0, "wall of cycle 0"),
    "close_min_suite_min": (
        short_suite_data,
        5,
        lambda pf: pf["cycles"]["completed"],
        0,
        1,
        "completed cycles",
    ),
    "hook_latency_s": (
        lambda: h.k1_transcript("skill"),
        5,
        lambda pf: pf["pool"]["classes_s"]["HOOK"],
        30.0,
        49.0,
        "pooled HOOK seconds",
    ),
    "long_call_s": (
        long_call_data,
        120,
        lambda pf: pf["pool"]["classes_s"]["MACHINE_WAIT"],
        1740.0,
        1650.0,
        "pooled MACHINE_WAIT seconds",
    ),
    "lane_window_min": (
        lane_data,
        50,
        lambda pf: pf["cycles"]["open"][0]["lane_source"],
        "lane-mention",
        "no-dispatch",
        "lane source of the open window",
    ),
    "away_fence_k": (gaps_data, 3.0, away_s, 350.0, 0.0, "pooled AWAY seconds"),
    "away_min_gaps": (gaps_data, 11, away_s, 350.0, 0.0, "pooled AWAY seconds"),
    "min_cycles": (
        lambda: h.k1_transcript("skill"),
        1,
        lambda pf: floor_of(pf, "min_cycles")["ok"],
        False,
        True,
        "the min_cycles floor row",
    ),
    "max_unparseable_pct": (
        garbage_data,
        1,
        lambda pf: floor_of(pf, "max_unparseable_pct")["ok"],
        True,
        False,
        "the max_unparseable_pct floor row",
    ),
    "min_timestamped_pct": (
        untimed_data,
        90,
        lambda pf: floor_of(pf, "min_timestamped_pct")["ok"],
        True,
        False,
        "the min_timestamped_pct floor row",
    ),
}


@pytest.mark.parametrize("field", RUN_FIELDS)
def test_t48_param_changes_an_observable(tmp_path, field):
    # the eleven fields run_profile consumes; the three leak_* fields belong to the CLI
    assert len(RUN_FIELDS) == 11
    assert set(CASES) == set(RUN_FIELDS)
    build, value, observe, before, after, _what = CASES[field]
    path = h.write_transcript(tmp_path, build())
    default = run.run_profile(path, m.Params())
    changed = run.run_profile(path, m.Params(**{field: value}))
    assert observe(default) == before
    assert observe(changed) == after


# ---------------------------------------------------------------------------------------------
# t49a, t49b: K1 and K1w end to end
# ---------------------------------------------------------------------------------------------

K1_CLASSES = {
    "MACHINE_WAIT": 1650.0,
    "SUBAGENT": 300.0,
    "HOOK": 30.0,
    "OPERATOR_RESPONSE": 60.0,
    "AWAY": 0.0,
    "CONTROLLER": 390.0,
}


def test_t49a_k1_classes(tmp_path):
    pf = k1_run(tmp_path)
    assert pf["cycles"]["completed"] == 1
    cycle = pf["cycles"]["list"][0]
    assert cycle["wall_s"] == 2430.0
    assert cycle["classes_s"] == pytest.approx(K1_CLASSES)
    assert pf["pool"]["classes_s"] == pytest.approx(K1_CLASSES)
    assert pf["pool"]["wall_all_s"] == pytest.approx(2430.0)


def test_t49a_k1_delegate_split(tmp_path):
    pf = k1_run(tmp_path)
    split = pf["cycles"]["list"][0]["split_s"]
    assert split["SUBAGENT_GATE"] == pytest.approx(100.0)
    assert split["SUBAGENT_HOOK"] == pytest.approx(30.0)
    assert split["SUBAGENT_WORK"] == pytest.approx(170.0)
    assert split["SUBAGENT_UNSPLIT"] == pytest.approx(0.0)


def test_t49a_k1_halves_and_answer(tmp_path):
    pf = k1_run(tmp_path)
    active = pf["pool"]["halves"]["active"]
    assert round(active["work_pct"], 1) == 23.0
    assert round(active["gate_pct"], 1) == 74.5
    assert round(active["operator_pct"], 1) == 2.5
    assert pf["pool"]["answer"] == "gate"
    assert pf["pool"]["distinct_evaluations"] == 2


def test_t49a_k1_coverage_makes_it_incomplete(tmp_path):
    pf = k1_run(tmp_path)
    assert (pf["delegates"]["matched"], pf["delegates"]["total"]) == (1, 2)
    assert floor_of(pf, "coverage")["ok"] is False
    # every other floor holds, so coverage is the one reason
    assert run.incomplete_reasons(pf) == ["coverage-weak"]
    verdict, rc, _, fail, _ = run.derive_verdict(pf)
    assert (verdict, rc, fail) == ("INCOMPLETE", 3, 0)


def test_t49a_k1_cycle_row_fields(tmp_path):
    pf = k1_run(tmp_path)
    cycle = pf["cycles"]["list"][0]
    assert cycle["start_utc"] == h.BASE_ISO
    assert cycle["status"] == "completed"
    assert cycle["suite_runs"] == 2
    assert [s["stage"] for s in cycle["stages"]] == list(m.STAGES)


def test_t49b_k1w_classes(tmp_path):
    pf = k1_run(tmp_path, "wrapper")
    expect = dict(K1_CLASSES, OPERATOR_RESPONSE=90.0, CONTROLLER=360.0)
    assert pf["cycles"]["list"][0]["classes_s"] == pytest.approx(expect)
    assert pf["pool"]["wall_all_s"] == pytest.approx(2430.0)


def test_t49b_k1w_halves_and_answer(tmp_path):
    pf = k1_run(tmp_path, "wrapper")
    active = pf["pool"]["halves"]["active"]
    assert round(active["work_pct"], 1) == 21.8
    assert round(active["gate_pct"], 1) == 74.5
    assert round(active["operator_pct"], 1) == 3.7
    assert pf["pool"]["answer"] == "gate"


def test_t49b_k1w_coverage_makes_it_incomplete(tmp_path):
    pf = k1_run(tmp_path, "wrapper")
    assert run.incomplete_reasons(pf) == ["coverage-weak"]
    assert run.derive_verdict(pf)[:2] == ("INCOMPLETE", 3)


def test_t49c_all_dispatches_matched_every_check_and_floor_held_is_pass(tmp_path):
    pf = k1_run(tmp_path, files=both_agents())
    assert (pf["delegates"]["matched"], pf["delegates"]["total"]) == (2, 2)
    assert all(row["verdict"] != "fail" for row in pf["checks"])
    assert all(f["ok"] for f in pf["floors"])
    verdict, rc, ok, fail, skip = run.derive_verdict(pf)
    assert (verdict, rc, fail) == ("PASS", 0, 0)
    assert ok > 0 and skip >= 0


def test_t49d_a_failing_row_is_fail_over_incomplete(tmp_path):
    pf = k1_run(tmp_path)
    assert run.incomplete_reasons(pf)
    bad = m.CheckRow("LEAKCHECK", "fail", "hits=1")
    verdict, rc, _, fail, _ = run.derive_verdict(pf, [bad])
    assert (verdict, rc, fail) == ("FAIL", 1, 1)


def test_t49e_error_outranks_fail(tmp_path):
    pf = k1_run(tmp_path, files=both_agents())
    bad = m.CheckRow("LEAKCHECK", "fail", "hits=1")
    verdict, rc, _, fail, _ = run.derive_verdict(pf, [bad], error=True)
    assert (verdict, rc, fail) == ("ERROR", 2, 1)


def test_t49f_skip_rows_count_as_skip_and_never_as_pass(tmp_path):
    pf = k1_run(tmp_path, files=both_agents())
    base = run.derive_verdict(pf)
    extra = [m.CheckRow("OUTSCOPE", "skip", "x"), m.CheckRow("LEAKCHECK", "skip", "y")]
    more = run.derive_verdict(pf, extra)
    assert more[0] == "PASS"
    assert more[2] == base[2]
    assert more[4] == base[4] + 2


def test_t49g_the_counts_cover_checks_extra_rows_and_floors(tmp_path):
    pf = k1_run(tmp_path)
    extra = [m.CheckRow("REMEDYCHECK", "pass", "z")]
    _, _, ok, fail, skip = run.derive_verdict(pf, extra)
    assert ok + fail + skip == len(pf["checks"]) + len(extra) + len(pf["floors"])


def test_t49h_an_unmet_floor_is_a_skip_not_a_fail(tmp_path):
    pf = k1_run(tmp_path)
    unmet = [f for f in pf["floors"] if not f["ok"]]
    assert [f["name"] for f in unmet] == ["coverage"]
    skips = sum(1 for row in pf["checks"] if row["verdict"] == "skip")
    assert run.derive_verdict(pf)[4] == skips + 1
    assert run.derive_verdict(pf)[3] == 0


def test_t49i_a_gated_manifest_disagreement_is_fail(tmp_path):
    row = {
        "name": "n",
        "recorded": 99,
        "selector": "cycles.completed",
        "unit": "count",
        "gate": True,
    }
    pf = k1_run(tmp_path, files=both_agents(), manifest_rows=[row])
    assert next(c for c in pf["checks"] if c["name"] == "MANIFEST")["verdict"] == "fail"
    assert pf["manifest"]["disagree"] == 1
    assert run.derive_verdict(pf)[:2] == ("FAIL", 1)


def test_t49j_an_ungated_manifest_disagreement_is_reported_only(tmp_path):
    row = {
        "name": "n",
        "recorded": 99,
        "selector": "cycles.completed",
        "unit": "count",
    }
    pf = k1_run(tmp_path, files=both_agents(), manifest_rows=[row])
    assert next(c for c in pf["checks"] if c["name"] == "MANIFEST")["verdict"] == "pass"
    assert pf["manifest"]["disagree"] == 1
    assert run.derive_verdict(pf)[:2] == ("PASS", 0)


def test_t49k_caller_incomplete_tokens_are_closed(tmp_path):
    pf = k1_run(tmp_path, files=both_agents())
    assert run.derive_verdict(pf, incomplete=["leak-unproven"])[:2] == ("INCOMPLETE", 3)
    with pytest.raises(ValueError):
        run.derive_verdict(pf, incomplete=["anything-else"])


def test_t49l_an_unknown_row_verdict_is_refused(tmp_path):
    pf = k1_run(tmp_path, files=both_agents())
    with pytest.raises(ValueError):
        run.derive_verdict(pf, [m.CheckRow("X", "ok", "d")])


def test_t49m_extra_rows_may_be_dicts(tmp_path):
    pf = k1_run(tmp_path, files=both_agents())
    row = {"name": "X", "verdict": "fail", "detail": "d"}
    assert run.derive_verdict(pf, [row])[:2] == ("FAIL", 1)


# ---------------------------------------------------------------------------------------------
# The profile itself
# ---------------------------------------------------------------------------------------------


def test_t49n_the_profile_holds_exactly_the_layout_keys(tmp_path):
    pf = k1_run(tmp_path)
    assert set(pf) == TOP_KEYS
    assert pf["tool"] == {"version": run.TOOL_VERSION}
    assert set(pf["input"]) >= {
        "records",
        "timestamped_pct",
        "unparseable",
        "inversions",
        "last_ts",
        "sha256",
        "bytes",
        "pinned",
    }
    assert set(pf["cycles"]) == {"completed", "partial", "open", "list"}
    assert set(pf["pool"]) >= {
        "wall_all_s",
        "wall_active_s",
        "away_s",
        "classes_s",
        "halves",
        "placements",
        "distinct_evaluations",
        "answer",
        "corners",
        "ranking",
        "repeat",
    }
    assert set(pf["delegates"]) >= {"matched", "total", "orphans", "inventory"}
    assert set(pf["idle"]) >= {"gaps", "q1_s", "q3_s"}
    assert set(pf["fence"]) >= {
        "k",
        "method",
        "inner_s",
        "outer_s",
        "above",
        "leave_one_out_min_s",
        "leave_one_out_max_s",
    }
    assert pf["leak"] is None and pf["manifest"] is None


def test_t49o_the_input_block_reads_the_snapshot(tmp_path):
    pf = k1_run(tmp_path)
    data = agree_ids(h.k1_transcript("skill"))
    assert pf["input"]["bytes"] == len(data)
    assert pf["input"]["sha256"] == hashlib.sha256(data).hexdigest()
    assert pf["input"]["records"] == pf["input"]["parsed"] == data.count(b"\n")
    assert pf["input"]["timestamped_pct"] == pytest.approx(100.0)
    assert pf["input"]["pinned"] is False
    assert pf["input"]["last_ts"] == "2030-01-01T00:40:30Z"


def test_t49p_the_profile_is_plain_finite_json_and_deterministic(tmp_path):
    first = k1_run(tmp_path)
    again = run.run_profile(tmp_path / "in" / "synth000.jsonl", m.Params(min_cycles=1))
    text = json.dumps(first, allow_nan=False, sort_keys=True)
    assert text == json.dumps(again, allow_nan=False, sort_keys=True)


def test_t49q_no_transcript_text_reaches_the_profile(tmp_path):
    pf = k1_run(tmp_path, files=both_agents())
    text = json.dumps(pf)
    words = [w for w in h.LOREM_WORDS if len(w) >= 5]
    assert words
    for word in words:
        assert re.search(r"\b%s\b" % word, text, re.I) is None
    # run-long artifact names, labels and commands are command text
    for needle in ("art1", "art2", "/work/", "run-long.sh", "feature-tip abc"):
        assert needle not in text


def test_t49r_params_are_echoed_with_their_source(tmp_path):
    pf = k1_run(tmp_path, params=m.Params(min_cycles=1, hook_latency_s=5))
    rows = {p["name"]: p for p in pf["params"] if p["name"] in m.Params._fields}
    assert list(rows) == list(m.Params._fields)
    assert rows["hook_latency_s"] == {
        "name": "hook_latency_s",
        "value": 5,
        "source": "cli",
    }
    assert rows["min_cycles"]["source"] == "cli"
    assert rows["long_call_s"] == {
        "name": "long_call_s",
        "value": 60,
        "source": "default",
    }
    assert rows["away_fence_k"]["value"] == 1.5


def test_t49s_explicit_overrides_name_the_cli_fields(tmp_path):
    pf = k1_run(tmp_path, overrides=["long_call_s"])
    rows = {
        p["name"]: p["source"] for p in pf["params"] if p["name"] in m.Params._fields
    }
    # an explicit override counts even at the default value; the rest follow the names
    assert rows["long_call_s"] == "cli"
    assert rows["min_cycles"] == "default"


def test_t49t_path_parameters_are_echoed_as_given_or_none(tmp_path):
    plain = k1_run(tmp_path / "a")
    paths = {
        p["name"]: p["value"]
        for p in plain["params"]
        if p["name"] not in m.Params._fields
    }
    assert paths == {
        "delegates": "none",
        "artifacts": "none",
        "repo": "none",
        "manifest": "none",
        "prefix": "none",
        "delegate_pins": "none",
    }
    given = k1_run(
        tmp_path / "b",
        delegates_dir=tmp_path / "elsewhere",
        artifacts=[tmp_path / "arts"],
        manifest_rows=[],
    )
    paths = {
        p["name"]: p["value"]
        for p in given["params"]
        if p["name"] not in m.Params._fields
    }
    assert paths["delegates"] == paths["artifacts"] == paths["manifest"] == "given"
    assert paths["repo"] == "none"
    assert "elsewhere" not in json.dumps(given)


def test_t49u_the_floors_and_their_actual_values(tmp_path):
    pf = k1_run(tmp_path)
    assert [f["name"] for f in pf["floors"]] == [
        "min_cycles",
        "max_unparseable_pct",
        "min_timestamped_pct",
        "coverage",
        "critical_shapes",
        "checks_run",
    ]
    assert floor_of(pf, "min_cycles") == {
        "name": "min_cycles",
        "want": 1,
        "actual": 1,
        "ok": True,
    }
    assert floor_of(pf, "max_unparseable_pct")["actual"] == 0
    assert floor_of(pf, "min_timestamped_pct")["want"] == 20
    assert floor_of(pf, "min_timestamped_pct")["actual"] == pytest.approx(100.0)
    cov = floor_of(pf, "coverage")
    assert (cov["want"], cov["actual"], cov["ok"]) == (80, pytest.approx(50.0), False)
    assert floor_of(pf, "critical_shapes")["actual"] == 0
    assert floor_of(pf, "checks_run") == {
        "name": "checks_run",
        "want": ck.CHECK_FLOOR,
        "actual": len(ck.CHECK_NAMES),
        "ok": True,
    }


def test_t49v_no_dispatch_means_the_coverage_floor_is_met(tmp_path):
    events = [feature(0), h.bash(5, "b1", "ls"), h.result(6, "b1")]
    pf = run_events(tmp_path, events)
    cov = floor_of(pf, "coverage")
    assert (cov["actual"], cov["ok"]) == (None, True)


def test_t49w_the_checks_cover_every_name_in_order(tmp_path):
    pf = k1_run(tmp_path, files=both_agents())
    names = [row["name"] for row in pf["checks"]]
    firsts = []
    for name in names:
        base = name.split("-")[0] if name.startswith("POOLCHECK") else name
        if base not in firsts:
            firsts.append(base)
    assert firsts == list(ck.CHECK_NAMES) + ["MANIFEST", "ZONE"]
    assert names[-2:] == ["MANIFEST", "ZONE"]
    assert {row["verdict"] for row in pf["checks"]} <= {"pass", "skip"}
    assert all(set(row) == {"name", "verdict", "detail"} for row in pf["checks"])


def test_t49x_the_delegates_block(tmp_path):
    pf = k1_run(tmp_path)
    block = pf["delegates"]
    assert (block["matched"], block["total"], block["orphans"]) == (1, 2, 0)
    assert [x["id"] for x in block["inventory"]] == [h.AGENT1]
    assert block["inventory"][0]["bytes"] == len(h.k1_delegate_files()[h.AGENT1])
    assert (
        block["inventory"][0]["sha256"]
        == hashlib.sha256(h.k1_delegate_files()[h.AGENT1]).hexdigest()
    )


def test_t49y_the_default_delegates_directory(tmp_path):
    with_files = k1_run(tmp_path / "a")
    assert with_files["delegates"]["matched"] == 1
    without = k1_run(tmp_path / "b", files=None)
    assert (without["delegates"]["matched"], without["delegates"]["total"]) == (0, 2)
    assert floor_of(without, "coverage")["ok"] is False


def test_t49z_an_explicit_delegates_directory_is_read(tmp_path):
    other = h.write_delegate_dir(tmp_path / "elsewhere", h.k1_delegate_files())
    pf = k1_run(tmp_path / "a", files=None, delegates_dir=other)
    assert pf["delegates"]["matched"] == 1


def test_t49z_an_explicit_delegates_directory_replaces_the_default(tmp_path):
    pf = k1_run(tmp_path, files=both_agents(), delegates_dir=tmp_path / "nothing-here")
    assert (pf["delegates"]["matched"], pf["delegates"]["total"]) == (0, 2)


def test_t50a_delegate_pins_are_plumbed(tmp_path):
    first = k1_run(tmp_path)
    pins = first["delegates"]["inventory"]
    path = tmp_path / "in" / "synth000.jsonl"
    again = run.run_profile(path, m.Params(min_cycles=1), delegate_pins=pins)
    assert again["delegates"]["inventory"] == pins
    bad = [dict(pins[0], sha256="0" * 64)]
    with pytest.raises(r.PinError):
        run.run_profile(path, m.Params(min_cycles=1), delegate_pins=bad)


def test_t50b_a_prefix_pin_reads_exactly_the_prefix(tmp_path):
    data = h.k1_transcript("skill")
    path = h.write_transcript(tmp_path, data)
    digest = hashlib.sha256(data).hexdigest()
    path.write_bytes(data + h.build_transcript([h.say(9000)]))
    pf = run.run_profile(path, m.Params(), prefix=(len(data), digest))
    assert (pf["input"]["bytes"], pf["input"]["sha256"]) == (len(data), digest)
    assert pf["input"]["pinned"] is True


def test_t50c_a_wrong_prefix_hash_raises(tmp_path):
    path = h.write_transcript(tmp_path, h.k1_transcript("skill"))
    with pytest.raises(r.PinError):
        run.run_profile(path, m.Params(), prefix=(10, "0" * 64))


def test_t50d_an_invalid_manifest_is_refused_before_the_transcript_is_read(tmp_path):
    rows = [{"name": "n", "recorded": 1, "selector": "not.a.selector", "unit": "count"}]
    with pytest.raises(mf.ManifestError) as seen:
        run.run_profile(tmp_path / "missing.jsonl", m.Params(), manifest_rows=rows)
    assert seen.value.index == 0


def test_t50e_an_unreadable_transcript_raises(tmp_path):
    with pytest.raises(r.ReadError):
        run.run_profile(tmp_path / "missing.jsonl", m.Params())


def test_t50f_the_sensitivity_block(tmp_path):
    pf = k1_run(tmp_path, params=m.Params(min_cycles=1, hook_latency_s=20))
    sets = pf["sensitivity"]["sets"]
    assert [s["name"] for s in sets] == ["primary", "tight", "loose", "min5", "min20"]
    assert sets[0]["kept"] == 1 and sets[0]["moved"] == 0
    assert [(x["threshold_s"], x["primary"]) for x in pf["sensitivity"]["hook"]] == [
        (5, False),
        (20, True),
        (30, False),
    ]
    assert set(pf["sensitivity"]["priority"]) == {"primary", "swapped"}
    assert pf["sensitivity"]["no_separation"]["AWAY"] == 0


def test_t50g_the_limits_hold_one_block_per_id(tmp_path):
    pf = k1_run(tmp_path)
    assert list(pf["limits"]) == list(m.LIMIT_IDS)
    assert pf["limits"]["L01"]["records"] == pf["input"]["records"]
    assert pf["limits"]["L08"]["matched"] == 1 and pf["limits"]["L08"]["total"] == 2
    assert pf["limits"]["L09"]["distinct_evaluations"] == 2
    assert pf["limits"]["L02"]["hidden_under_machine_wait_s"] == pytest.approx(300.0)
    assert pf["limits"]["L14"]["completed"] == 1


def test_t50h_the_cross_checks_are_unmeasured_without_their_inputs(tmp_path):
    pf = k1_run(tmp_path)
    ind = pf["indicators"]
    assert ind["artifact_end"]["status"] == "unmeasured"
    assert ind["commit_bound"]["status"] == "unmeasured"
    assert {x["reason"] for x in pf["pool"]["repeat"]} == {"no-repo"}
    what = {x["what"] for x in pf["not_measured"]}
    assert {"repeat", "commit-bound", "artifact-end", "corner"} <= what


def test_t50i_an_artifacts_directory_makes_the_end_check_measured(tmp_path):
    arts = tmp_path / "arts"
    arts.mkdir()
    pf = k1_run(tmp_path, artifacts=[arts])
    assert pf["indicators"]["artifact_end"]["status"] == "measured"
    assert str(arts) not in json.dumps(pf)


def test_t50j_away_not_derived_below_the_gap_floor(tmp_path):
    pf = k1_run(tmp_path)
    assert pf["fence"]["inner_s"] is None
    assert {"what": "away", "name": None, "reason": "below-min-gaps"} in [
        {k: x.get(k) for k in ("what", "name", "reason")} for x in pf["not_measured"]
    ]
    fitted = run.run_profile(
        h.write_transcript(tmp_path / "g", gaps_data()), m.Params(min_cycles=1)
    )
    assert fitted["fence"]["inner_s"] == pytest.approx(287.7, abs=0.1)
    assert fitted["idle"]["gaps"] == 10
    assert not [x for x in fitted["not_measured"] if x["what"] == "away"]


# ---------------------------------------------------------------------------------------------
# Blocks: shapes, census, windows, idle and fence, sensitivity, indicators, limits
# ---------------------------------------------------------------------------------------------


def test_t50k_a_missing_check_row_is_checks_under_floor(tmp_path, monkeypatch):
    monkeypatch.setattr(
        ck, "check_mapcheck", lambda class_set: m.CheckRow("OTHERCHECK", "pass", "x")
    )
    pf = k1_run(tmp_path, files=both_agents())
    assert floor_of(pf, "checks_run")["actual"] == len(ck.CHECK_NAMES) - 1
    assert run.incomplete_reasons(pf) == ["checks-under-floor"]
    assert run.derive_verdict(pf)[:2] == ("INCOMPLETE", 3)


def test_t50l_partial_and_open_windows_are_listed_not_pooled(tmp_path):
    events = [feature(0), h.say(10), feature(5000), h.say(5010)]
    pf = run_events(tmp_path, events)
    assert pf["cycles"]["completed"] == 0 and pf["cycles"]["list"] == []
    (partial,) = pf["cycles"]["partial"]
    (opened,) = pf["cycles"]["open"]
    assert (partial["status"], partial["start_utc"]) == ("partial", h.BASE_ISO)
    assert (opened["status"], opened["start_utc"]) == ("open", "2030-01-01T01:23:20Z")
    assert opened["minutes_from_last_ts"] == pytest.approx(10 / 60)
    assert partial["minutes_from_last_ts"] is None
    assert pf["limits"]["L14"]["partial"] == pf["limits"]["L14"]["open"] == 1


def test_t50m_the_gap_flag_marks_a_producer_without_its_shape(tmp_path):
    pf = dropped_ack_run(tmp_path)
    flags = {s["name"]: s["gap"] for s in pf["shapes"]}
    assert flags["agent_ack"] is True
    assert [n for n, gap in flags.items() if gap] == ["agent_ack"]
    assert pf["versions"][0]["gaps"] == ["agent_ack"]


def test_t50n_the_version_census_row(tmp_path):
    pf = run_events(tmp_path, no_invocation_events())
    (row,) = pf["versions"]
    assert row["timestamped_pct"] == pytest.approx(100.0)
    assert row["counts"]["Bash"] == 1 and row["counts"]["bash_tool_use"] == 1
    assert row["counts"]["feature_invocation"] == 0
    assert row["gaps"] == []


def test_t50o_the_pool_counts_completed_cycles(tmp_path):
    assert k1_run(tmp_path)["pool"]["n"] == 1


def test_t50p_the_idle_quartiles_in_seconds(tmp_path):
    pf = run.run_profile(h.write_transcript(tmp_path, gaps_data()), m.Params())
    assert pf["idle"]["gaps"] == 10
    assert pf["idle"]["gaps_s"] == pytest.approx(
        [10, 20, 30, 40, 50, 60, 70, 80, 90, 350]
    )
    # spec 6.3: the log10 quartiles are 1.50836 and 1.88859 for every G above 90
    assert pf["idle"]["q1_s"] == pytest.approx(10**1.50836, rel=1e-4)
    assert pf["idle"]["q3_s"] == pytest.approx(10**1.88859, rel=1e-4)


def leave_one_out(gaps, k):
    fences = []
    for i in range(len(gaps)):
        logs = [math.log10(g) for g in gaps[:i] + gaps[i + 1 :]]
        q1, _, q3 = statistics.quantiles(logs, n=4, method="inclusive")
        fences.append(10 ** (q3 + k * (q3 - q1)))
    return min(fences), max(fences)


def test_t50q_the_fence_block(tmp_path):
    pf = run.run_profile(h.write_transcript(tmp_path, gaps_data()), m.Params())
    fence = pf["fence"]
    assert fence["fitted"] is True and fence["above"] == 1
    assert (fence["k"], fence["method"]) == (1.5, "inclusive")
    assert fence["inner_s"] == pytest.approx(287.7, abs=0.1)
    assert fence["outer_s"] == pytest.approx(1069.8, abs=0.1)
    low, high = leave_one_out([10, 20, 30, 40, 50, 60, 70, 80, 90, 350], 1.5)
    assert fence["leave_one_out_min_s"] == pytest.approx(low)
    assert fence["leave_one_out_max_s"] == pytest.approx(high)


def test_t50r_no_separation_moves_away_into_operator_response(tmp_path):
    pf = run.run_profile(h.write_transcript(tmp_path, gaps_data()), m.Params())
    pooled = pf["pool"]["classes_s"]
    flat = pf["sensitivity"]["no_separation"]
    assert pooled["AWAY"] == pytest.approx(350.0)
    assert flat["AWAY"] == 0
    assert flat["OPERATOR_RESPONSE"] == pytest.approx(pooled["OPERATOR_RESPONSE"] + 350)


def test_t50s_the_priority_sensitivity_carries_the_split(tmp_path):
    pri = k1_run(tmp_path)["sensitivity"]["priority"]
    assert pri["primary"]["SUBAGENT_GATE"] == pytest.approx(100.0)
    assert pri["swapped"]["SUBAGENT_HOOK"] == pytest.approx(30.0)


def test_t50t_the_delegates_block_counts_calls_and_weakness(tmp_path):
    block = k1_run(tmp_path)["delegates"]
    assert block["weak"] is True
    assert (block["files"], block["runlong_calls"], block["background_calls"]) == (
        1,
        1,
        0,
    )
    assert k1_run(tmp_path / "b", files=both_agents())["delegates"]["weak"] is False


def test_t50u_the_end_check_counts_absent_artifacts(tmp_path):
    arts = tmp_path / "arts"
    arts.mkdir()
    pf = k1_run(tmp_path, artifacts=[arts])
    assert pf["indicators"]["artifact_end"] == {
        "status": "measured",
        "agree": 0,
        "disagree": 0,
        "unpaired": 0,
        "absent": 2,
    }


INDICATORS_K1 = {
    "order": [
        {
            "hidden_agent_s": 300.0,
            "hidden_union_s": 300.0,
            "stalled_recorded_s": 0.0,
            "stalled_any_s": 0.0,
        }
    ],
    "suite_mach": [
        {
            "feature_s": 900.0,
            "feature_run_s": [900.0],
            "dev_s": 750.0,
            "dev_run_s": [750.0],
            "feature_in_band": True,
            "dev_in_band": True,
        }
    ],
    "suite_band_min": [5.0, 60.0],
    "subcheck": {
        "n": 1,
        "median": 1.0,
        "p10": 1.0,
        "p90": 1.0,
        "in_band": True,
        "not_enqueue": 0,
    },
    "dominant_hook": {
        "status": "measured",
        "calls": 1,
        "largest_ms": 30000,
        "union_ms": 30000,
        "share_pct": 100.0,
    },
}


@pytest.mark.parametrize("key", sorted(INDICATORS_K1))
def test_t50v_indicators_of_k1(tmp_path, key):
    # hand figures: A2 (300 s) lies under the 900 s feature-tip suite; suites 900 s and
    # 750 s; one Edit of 30 s; A1 enqueued 300 s after its dispatch with duration 300 s
    assert same(k1_run(tmp_path)["indicators"][key], INDICATORS_K1[key])


LIMITS_K1 = {
    "L02": {
        "hidden_under_machine_wait_s": 300.0,
        "stalled_recorded_s": 0.0,
        "stalled_any_s": 0.0,
    },
    "L03": {
        "sets": [
            {"name": "primary", "kept": 1, "moved": 0},
            {"name": "tight", "kept": 1, "moved": 0},
            {"name": "loose", "kept": 1, "moved": 0},
            {"name": "min5", "kept": 1, "moved": 0},
            {"name": "min20", "kept": 0, "moved": 1},
        ]
    },
    "L04": {
        "gaps": 1,
        "inner_s": None,
        "leave_one_out_min_s": None,
        "leave_one_out_max_s": None,
        "pct_above": 0.0,
        "smallest_above_pct": None,
    },
    "L05": {
        "calls": 1,
        "union_s": 30.0,
        "largest_s": 30.0,
        "share_pct": 100.0,
        "hook_reported_s": 0.0,
    },
    "L06": {"calls": 0, "total_s": 0.0, "check_s": 0.0, "other_s": 0.0},
    "L07": {"roles": {"opus": 1, "sonnet": 1}},
    "L08": {"matched": 1, "total": 2, "unsplit_s": 0.0},
    "L09": {"distinct_evaluations": 2, "leads": {"gate": 12}},
    "L10": {"inside_subagent_leads": {"work": 12}},
    "L11": {"s": 0.0, "pct_all": 0.0, "pct_active": 0.0},
    "L12": {"files": 1, "runlong_calls": 1, "background_calls": 0, "exact": True},
    "L13": {"lanes": [{"lane": "fast-lane", "source": "dispatch-count", "n": 1}]},
    "L14": {"completed": 1, "partial": 0, "open": 0, "bases": {"label": 1}},
    "L15": {"turn_s": 0.0, "controller_s": 390.0},
    "L16": {},
    "L17": {"independent": None, "guaranteed": None, "floor_decided": None},
    # 560 / 2430 and (560 + 60) / 2430: HOOK and SUBAGENT_HOOK read as work
    "L18": {"work_pct_active": 560 / 2430 * 100, "work_pct_active_hook_work": 25.5144},
}


@pytest.mark.parametrize("limit", sorted(LIMITS_K1))
def test_t50w_limit_fields_of_k1(tmp_path, limit):
    got = k1_run(tmp_path)["limits"][limit]
    want = LIMITS_K1[limit]
    if limit == "L18":
        assert got["work_pct_active"] == pytest.approx(want["work_pct_active"])
        assert got["work_pct_active_hook_work"] == pytest.approx(
            want["work_pct_active_hook_work"], abs=1e-3
        )
    else:
        assert same(got, want)


def test_t50x_the_hook_threshold_reaches_the_limit_and_dominant_blocks(tmp_path):
    # the one Edit of K1 lasts 30 s: over the default 10 s, not over 40 s
    pf = k1_run(tmp_path, params=m.Params(min_cycles=1, hook_latency_s=40))
    assert pf["limits"]["L05"]["calls"] == 0
    assert pf["limits"]["L05"]["union_s"] == 0
    assert pf["indicators"]["dominant_hook"]["status"] == "none"
    assert pf["pool"]["classes_s"]["HOOK"] == 0
    assert (
        next(c for c in pf["checks"] if c["name"] == "HOOKCHECK")["verdict"] == "pass"
    )


def test_t50y_a_repo_makes_repeat_and_the_bound_measured(tmp_path):
    plain = tmp_path / "not-a-repository"
    plain.mkdir()
    pf = k1_run(tmp_path, repo=plain)
    # the label shas resolve in no repository: unmeasured with a reason, and unknown
    assert {x["reason"] for x in pf["pool"]["repeat"]} == {"unresolved"}
    assert pf["indicators"]["commit_bound"]["status"] == "measured"
    assert pf["indicators"]["commit_bound"]["unknown"] == 2
    kinds = {(x["what"], x["reason"]) for x in pf["not_measured"]}
    assert ("repeat", "unresolved") in kinds
    assert ("repeat", "no-repo") not in kinds and (
        "commit-bound",
        "no-repo",
    ) not in kinds
    assert str(plain) not in json.dumps(pf)


# ---------------------------------------------------------------------------------------------
# Review fold: a profile missing its rows is refused, boundaries, and plumbing
# ---------------------------------------------------------------------------------------------


def pass_profile(tmp_path):
    """A complete profile that derives PASS (every dispatch matched, every floor met)."""
    pf = k1_run(tmp_path, files=both_agents())
    assert run.derive_verdict(pf)[0] == "PASS"
    return copy.deepcopy(pf)


def test_t51a_a_profile_with_no_floors_is_refused(tmp_path):
    pf = pass_profile(tmp_path)
    pf["floors"] = []
    with pytest.raises(ValueError):
        run.derive_verdict(pf)


def test_t51b_a_profile_missing_one_floor_is_refused(tmp_path):
    pf = pass_profile(tmp_path)
    pf["floors"] = [f for f in pf["floors"] if f["name"] != "min_cycles"]
    with pytest.raises(ValueError):
        run.derive_verdict(pf)


def test_t51c_a_profile_with_no_check_rows_is_refused(tmp_path):
    pf = pass_profile(tmp_path)
    pf["checks"] = []
    with pytest.raises(ValueError):
        run.derive_verdict(pf)


def test_t51d_a_floor_ok_that_is_not_a_bool_is_refused(tmp_path):
    pf = pass_profile(tmp_path)
    pf["floors"][0]["ok"] = "no"
    with pytest.raises(ValueError):
        run.derive_verdict(pf)


def test_t51e_incomplete_reasons_refuses_a_profile_with_no_floors(tmp_path):
    pf = pass_profile(tmp_path)
    pf["floors"] = []
    with pytest.raises(ValueError):
        run.incomplete_reasons(pf)


def test_t51f_a_profile_with_a_repeated_floor_is_refused(tmp_path):
    pf = pass_profile(tmp_path)
    pf["floors"].append(dict(pf["floors"][0]))
    with pytest.raises(ValueError):
        run.derive_verdict(pf)


def test_t51g_a_run_profile_result_always_passes_the_shape_check(tmp_path):
    for variant in ("skill", "wrapper"):
        pf = k1_run(tmp_path / variant, variant)
        assert run.derive_verdict(pf)[0] == "INCOMPLETE"
    empty = run.run_profile(h.write_transcript(tmp_path, b""), m.Params())
    assert run.derive_verdict(empty)[0] == "INCOMPLETE"


def exact_pct_run(tmp_path, events, extra=b"", params=None):
    data = h.build_transcript(events) + extra
    path = h.write_transcript(tmp_path, data)
    return run.run_profile(path, m.Params() if params is None else params)


def test_t52a_unparseable_exactly_at_the_cap_meets_the_floor(tmp_path):
    events = [feature(0)] + [h.say(i + 1) for i in range(18)]
    pf = exact_pct_run(tmp_path, events, b"garbage line\n")
    assert pf["input"]["records"] == 20 and pf["input"]["unparseable"] == 1
    row = floor_of(pf, "max_unparseable_pct")
    assert (row["want"], row["actual"], row["ok"]) == (5, 5.0, True)


def test_t52b_timestamped_exactly_at_the_floor_meets_it(tmp_path):
    events = [feature(0)] + [h.say(i + 1, ts=False) for i in range(4)]
    pf = exact_pct_run(tmp_path, events)
    assert pf["input"]["records"] == 5
    row = floor_of(pf, "min_timestamped_pct")
    assert (row["want"], row["actual"], row["ok"]) == (20, 20.0, True)


def test_t52c_min_cycles_counts_only_completed_windows(tmp_path):
    events = [feature(0)] + suite(1, "ka", "dev-tip aaa1111", 100, 1000)
    events.append(feature(5000))
    pf = exact_pct_run(tmp_path, events, params=m.Params(min_cycles=2))
    assert pf["cycles"]["completed"] == 1
    assert len(pf["cycles"]["partial"]) + len(pf["cycles"]["open"]) == 1
    row = floor_of(pf, "min_cycles")
    assert (row["want"], row["actual"], row["ok"]) == (2, 1, False)
    assert pf["pool"]["n"] == 1


def test_t52d_a_prefix_pin_is_echoed_as_given(tmp_path):
    data = h.k1_transcript("skill")
    path = h.write_transcript(tmp_path, data)
    digest = hashlib.sha256(data).hexdigest()
    pf = run.run_profile(path, m.Params(), prefix=(len(data), digest))
    paths = {p["name"]: p["value"] for p in pf["params"]}
    assert paths["prefix"] == "given"
    assert digest not in json.dumps(pf["params"])


def test_t52e_delegate_pins_are_echoed_as_given(tmp_path):
    first = k1_run(tmp_path)
    path = tmp_path / "in" / "synth000.jsonl"
    pins = first["delegates"]["inventory"]
    pf = run.run_profile(path, m.Params(min_cycles=1), delegate_pins=pins)
    assert {p["name"]: p["value"] for p in pf["params"]}["delegate_pins"] == "given"


def test_t52f_a_repo_is_echoed_as_given(tmp_path):
    plain = tmp_path / "not-a-repository"
    plain.mkdir()
    pf = k1_run(tmp_path, repo=plain)
    assert {p["name"]: p["value"] for p in pf["params"]}["repo"] == "given"


def cross_check_names(pf):
    return {
        x["name"]: x["reason"] for x in pf["not_measured"] if x["what"] == "cross-check"
    }


def test_t52g_an_absent_turn_duration_is_listed(tmp_path):
    assert cross_check_names(k1_run(tmp_path))["turn_duration"] == "shape-absent"


def test_t52h_an_absent_sidechain_flag_is_listed(tmp_path):
    assert cross_check_names(k1_run(tmp_path))["sidechain_flag"] == "shape-absent"


def test_t52i_l06_counts_the_long_foreground_calls(tmp_path):
    pf = run_events(
        tmp_path,
        h.k1_events("skill") + [h.bash(5, "bx", "sleep 90"), h.result(95, "bx")],
        m.Params(min_cycles=1),
    )
    assert pf["cycles"]["completed"] == 1
    assert pf["limits"]["L06"]["calls"] == 1
    assert pf["limits"]["L06"]["total_s"] == pytest.approx(90.0)


def test_t52j_l05_reports_hook_seconds_for_a_2030_transcript(tmp_path):
    pf = run_events(
        tmp_path,
        h.k1_events("skill") + [h.hook(10, "hx", 2000)],
        m.Params(min_cycles=1),
    )
    assert pf["limits"]["L05"]["hook_reported_s"] == pytest.approx(2.0)


def test_t53a_the_coverage_want_is_derived_from_the_delegate_threshold(tmp_path):
    assert run.COVERAGE_WANT_PCT * dg.WEAK_DEN == 100 * dg.WEAK_NUM
    pf = k1_run(tmp_path)
    assert floor_of(pf, "coverage")["want"] == 100 * dg.WEAK_NUM / dg.WEAK_DEN


def test_t54a_below_min_cycles_the_pool_answer_is_undetermined(tmp_path):
    pf = k1_run(tmp_path, params=m.Params(min_cycles=2))
    assert pf["cycles"]["completed"] == 1
    assert floor_of(pf, "min_cycles")["ok"] is False
    assert pf["pool"]["answer"] == "undetermined"
    assert pf["pool"]["n"] == 1
    assert pf["pool"]["halves"]["active"]["gate_pct"] is not None


def test_t54b_at_exactly_min_cycles_the_pool_answer_is_kept(tmp_path):
    pf = k1_run(tmp_path, params=m.Params(min_cycles=1))
    assert floor_of(pf, "min_cycles")["ok"] is True
    assert pf["pool"]["answer"] == "gate"

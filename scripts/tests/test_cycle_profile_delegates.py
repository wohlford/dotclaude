"""Tests for the cycle-profile delegates module: the file read, the split, the inventory.

Every transcript and delegate file is synthetic (fictional base date 2030-01-01, invented
ids, lorem text), built from `cycle_profile_helpers`. The known answer is the delegate file
of spec 6.3: a run-long Bash from 120 to 220 and an Edit from 250 to 280 inside the A1
dispatch (100 to 400) give GATE 100, HOOK 30, WORK 170, UNSPLIT 0 and `matched=1/2`.
Delegate records are flagged `isSidechain` and carry an `agentId`, as real ones do.
"""

import hashlib
import os
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import cycle_profile_classes as k  # noqa: E402, I001
import cycle_profile_cycles as c  # noqa: E402
import cycle_profile_delegates as dl  # noqa: E402
import cycle_profile_helpers as h  # noqa: E402
import cycle_profile_model as m  # noqa: E402
import cycle_profile_records as r  # noqa: E402

MS = 1000
KEYS = dl.SPLIT_KEYS
DKW = {"sidechain": True, "extra": {"agentId": h.AGENT1}}


def ms(seconds):
    """Offset in seconds from the fictional base, as integer epoch milliseconds."""
    return h.BASE_MS + round(seconds * MS)


def params(**over):
    return m.Params(min_cycles=1, **over)


def file_bytes(events):
    return h.build_transcript(events)


def base_events():
    """A1's file of 6.3: a run-long Bash 120 to 220 and an Edit 250 to 280."""
    return [
        h.runlong_launch(120, "d1", "dart", "gate", **DKW),
        h.result(220, "d1", **DKW),
        h.edit(250, "d2", **DKW),
        h.result(280, "d2", **DKW),
    ]


def agent_file(*extra):
    return file_bytes(base_events() + list(extra))


def closed(body, tail=10, suite_len=700):
    """A completed cycle: `/feature`, `body`, a real dev-tip suite, a commit marker."""
    last = max([rec["at"] for rec in body] + [0])
    start = last + tail
    return (
        [h.wrapper(0, "feature")]
        + list(body)
        + [
            h.runlong_launch(start, "lS", "artS", "dev-tip abc1234"),
            h.result(start + 1, "lS"),
            h.runlong_wait(start + 2, "wS", "artS"),
            h.result(start + 3, "wS"),
            h.enqueue(start + suite_len, "wS", "tS", suite_len * MS),
            h.skill(start + suite_len + 10, "kS", "commit"),
        ]
    )


def dispatch(at_start, at_end, tool_id, agent_id, model="sonnet", task=None):
    """An Agent dispatch, its ack (agent id) and its enqueue (task id)."""
    return [
        h.agent(at_start, tool_id, model, "spec " + h.lorem(1)),
        h.ack(at_start + 1, tool_id, agent_id),
        h.enqueue(at_end, tool_id, task or "t" + tool_id, 1000),
    ]


class Run:
    """One transcript read, its delegate directory read, and the split over them."""

    def __init__(self, events, files, tmp_path, swap=False, pins=None, **over):
        """Write `files` under `tmp_path/agents`, read them and split."""
        self.params = params(**over)
        self.transcript = r.parse_bytes(h.build_transcript(events))
        self.cycles = c.analyze_cycles(self.transcript, self.params)
        self.classes = k.classify(self.cycles, self.transcript, self.params)
        self.dir = h.write_delegate_dir(Path(tmp_path) / "agents", files)
        self.set = dl.read_delegates(
            self.dir, self.classes.dispatches, self.params, pins=pins
        )
        self.split = dl.split(
            self.cycles, self.transcript, self.params, self.set, swap_priority=swap
        )

    @property
    def first(self):
        return self.split.cycles[0]


def k1(tmp_path, files=None, variant="skill", **kw):
    events = h.k1_events(variant)
    return Run(
        events, h.k1_delegate_files() if files is None else files, tmp_path, **kw
    )


def four(cycle):
    return {
        "work": round(cycle["SUBAGENT_WORK"], 6),
        "gate": round(cycle["SUBAGENT_GATE"], 6),
        "hook": round(cycle["SUBAGENT_HOOK"], 6),
        "unsplit": round(cycle["SUBAGENT_UNSPLIT"], 6),
    }


# ---------------------------------------------------------------------------------------------
# t17: the split
# ---------------------------------------------------------------------------------------------


def test_t17a_k1_split(tmp_path):
    run = k1(tmp_path)
    assert four(run.first) == {"work": 170, "gate": 100, "hook": 30, "unsplit": 0}


def test_t17a_k1w_split_is_the_same(tmp_path):
    run = k1(tmp_path, variant="wrapper")
    assert four(run.first) == {"work": 170, "gate": 100, "hook": 30, "unsplit": 0}


def test_t17a_k1_gate_arm_is_runlong(tmp_path):
    run = k1(tmp_path)
    assert run.first["GATE_RUNLONG"] == 100
    assert run.first["GATE_LONGCALL"] == 0


def test_t17a_k1_coverage_is_one_of_two(tmp_path):
    run = k1(tmp_path)
    cov = dl.coverage(run.set)
    assert (cov.matched, cov.total) == (1, 2)
    assert dl.coverage_line(cov) == "COVERAGE matched=1/2"


def test_t17a_split_sums_to_subagent_class_seconds(tmp_path):
    run = k1(tmp_path)
    parts = sum(run.first[x] for x in KEYS if x in dl.PART_KEYS)
    assert parts == pytest.approx(run.classes.cycles[0].class_s["SUBAGENT"])
    assert parts == pytest.approx(300)


def test_t17a_k1_split_by_role(tmp_path):
    run = k1(tmp_path)
    assert four(run.split.by_role["sonnet"]) == {
        "work": 170,
        "gate": 100,
        "hook": 30,
        "unsplit": 0,
    }
    assert (
        "opus" not in run.split.by_role
        or four(run.split.by_role["opus"])["unsplit"] == 0
    )


def test_t17a_hook_threshold_is_the_parameter(tmp_path):
    run = k1(tmp_path, hook_latency_s=40)
    assert four(run.first) == {"work": 200, "gate": 100, "hook": 0, "unsplit": 0}


def test_t17a_hook_threshold_is_strict(tmp_path):
    exact = [h.edit(250, "d2", **DKW), h.result(260.000, "d2", **DKW)]
    run = k1(tmp_path, {h.AGENT1: file_bytes(exact)})
    assert run.first["SUBAGENT_HOOK"] == 0
    longer = [h.edit(250, "d2", **DKW), h.result(260.001, "d2", **DKW)]
    run = k1(tmp_path, {h.AGENT1: file_bytes(longer)})
    assert run.first["SUBAGENT_HOOK"] == pytest.approx(10.001)


@pytest.mark.parametrize("name", ["Edit", "Write", "MultiEdit"])
def test_t17a_edit_class_names_are_hook(tmp_path, name):
    events = [h.edit(250, "d2", name=name, **DKW), h.result(280, "d2", **DKW)]
    run = k1(tmp_path, {h.AGENT1: file_bytes(events)})
    assert run.first["SUBAGENT_HOOK"] == 30


def test_t17a_longcall_check_class(tmp_path):
    events = [
        h.bash(120, "d1", "pytest scripts/tests/test_x.py", **DKW),
        h.result(200, "d1", **DKW),
    ]
    run = k1(tmp_path, {h.AGENT1: file_bytes(events)})
    assert run.first["GATE_LONGCALL"] == 80
    assert run.first["LONGCALL_CHECK"] == 80
    assert run.first["LONGCALL_OTHER"] == 0
    assert run.first["GATE_RUNLONG"] == 0


def test_t17a_longcall_other_class(tmp_path):
    events = [h.bash(120, "d1", "sleep 90", **DKW), h.result(210, "d1", **DKW)]
    run = k1(tmp_path, {h.AGENT1: file_bytes(events)})
    assert run.first["GATE_LONGCALL"] == 90
    assert run.first["LONGCALL_OTHER"] == 90
    assert run.first["LONGCALL_CHECK"] == 0


def test_t17a_longcall_threshold_is_the_parameter(tmp_path):
    events = [h.bash(120, "d1", "sleep 90", **DKW), h.result(210, "d1", **DKW)]
    run = k1(tmp_path, {h.AGENT1: file_bytes(events)}, long_call_s=100)
    assert run.first["GATE_LONGCALL"] == 0
    assert run.first["SUBAGENT_WORK"] == 300


def test_t17a_longcall_threshold_is_strict(tmp_path):
    events = [h.bash(120, "d1", "sleep 60", **DKW), h.result(180.000, "d1", **DKW)]
    run = k1(tmp_path, {h.AGENT1: file_bytes(events)})
    assert run.first["GATE_LONGCALL"] == 0


def test_t17a_background_bash_counts_like_the_original(tmp_path):
    events = [
        h.bash(120, "d1", "sleep 90", bg=True, **DKW),
        h.result(210, "d1", **DKW),
    ]
    run = k1(tmp_path, {h.AGENT1: file_bytes(events)})
    assert run.first["GATE_LONGCALL"] == 90


def test_t17a_call_without_a_result_contributes_nothing(tmp_path):
    events = [h.edit(250, "d2", **DKW), h.runlong_launch(120, "d1", "dart", "x", **DKW)]
    run = k1(tmp_path, {h.AGENT1: file_bytes(events)})
    assert four(run.first) == {"work": 300, "gate": 0, "hook": 0, "unsplit": 0}


def test_t17a_corrupt_line_is_counted_and_skipped(tmp_path):
    data = agent_file()
    lines = data.split(b"\n")
    broken = b"\n".join([lines[0], b"{not json", *lines[1:]])
    run = k1(tmp_path, {h.AGENT1: broken})
    assert four(run.first) == {"work": 170, "gate": 100, "hook": 30, "unsplit": 0}
    assert run.set.records.unparseable == 1


def test_t17a_record_split_is_the_whole_interval(tmp_path):
    run = k1(tmp_path)
    record = run.set.records[h.AGENT1]
    assert record.split_s["GATE_RUNLONG"] == 100
    assert record.split_s["SUBAGENT_HOOK"] == 30
    assert record.split_s["SUBAGENT_WORK"] == 170


def test_t17b_gate_wins_over_hook_by_default(tmp_path):
    events = [
        h.runlong_launch(120, "d1", "dart", "gate", **DKW),
        h.edit(150, "d2", **DKW),
        h.result(200, "d2", **DKW),
        h.result(220, "d1", **DKW),
    ]
    run = k1(tmp_path, {h.AGENT1: file_bytes(events)})
    assert four(run.first) == {"work": 200, "gate": 100, "hook": 0, "unsplit": 0}


def test_t17b_swapped_priority_gives_the_overlap_to_hook(tmp_path):
    events = [
        h.runlong_launch(120, "d1", "dart", "gate", **DKW),
        h.edit(150, "d2", **DKW),
        h.result(200, "d2", **DKW),
        h.result(220, "d1", **DKW),
    ]
    run = k1(tmp_path, {h.AGENT1: file_bytes(events)}, swap=True)
    assert four(run.first) == {"work": 200, "gate": 50, "hook": 50, "unsplit": 0}


def test_t17b_priority_sensitivity_is_reported(tmp_path):
    events = [
        h.runlong_launch(120, "d1", "dart", "gate", **DKW),
        h.edit(150, "d2", **DKW),
        h.result(200, "d2", **DKW),
        h.result(220, "d1", **DKW),
    ]
    run = k1(tmp_path, {h.AGENT1: file_bytes(events)})
    sens = dl.priority_sensitivity(run.cycles, run.transcript, run.params, run.set)
    assert sens.name == "priority-swapped"
    assert four(sens.primary) == {"work": 200, "gate": 100, "hook": 0, "unsplit": 0}
    assert four(sens.swapped) == {"work": 200, "gate": 50, "hook": 50, "unsplit": 0}


def test_t17b_priority_sensitivity_is_zero_without_overlap(tmp_path):
    run = k1(tmp_path)
    sens = dl.priority_sensitivity(run.cycles, run.transcript, run.params, run.set)
    assert four(sens.primary) == four(sens.swapped)


def edit_inside(bash_events, edit_at, edit_to):
    return bash_events + [h.edit(edit_at, "d2", **DKW), h.result(edit_to, "d2", **DKW)]


def long_bash(at, to, command, tool_id="d1"):
    return [h.bash(at, tool_id, command, **DKW), h.result(to, tool_id, **DKW)]


CHECK_CMD = "pytest scripts/tests/test_x.py"


def test_t17b_longcall_check_wins_over_hook_by_default(tmp_path):
    events = edit_inside(long_bash(120, 210, CHECK_CMD), 150, 200)
    run = k1(tmp_path, {h.AGENT1: file_bytes(events)})
    assert run.first["LONGCALL_CHECK"] == 90
    assert run.first["SUBAGENT_HOOK"] == 0


def test_t17b_longcall_other_wins_over_hook_by_default(tmp_path):
    events = edit_inside(long_bash(120, 210, "sleep 90"), 150, 200)
    run = k1(tmp_path, {h.AGENT1: file_bytes(events)})
    assert run.first["LONGCALL_OTHER"] == 90
    assert run.first["SUBAGENT_HOOK"] == 0


def test_t17b_swapped_priority_applies_to_a_longcall(tmp_path):
    events = edit_inside(long_bash(120, 210, CHECK_CMD), 150, 200)
    run = k1(tmp_path, {h.AGENT1: file_bytes(events)}, swap=True)
    assert run.first["SUBAGENT_HOOK"] == 50
    assert run.first["LONGCALL_CHECK"] == 40


def test_t17b_runlong_wins_over_an_overlapping_longcall(tmp_path):
    events = [
        h.runlong_launch(120, "d1", "dart", "gate", **DKW),
        h.result(220, "d1", **DKW),
    ] + long_bash(150, 250, CHECK_CMD, "d3")
    run = k1(tmp_path, {h.AGENT1: file_bytes(events)})
    assert run.first["GATE_RUNLONG"] == 100
    assert run.first["GATE_LONGCALL"] == 30


def test_t17b_longcall_check_wins_over_an_overlapping_other(tmp_path):
    events = long_bash(120, 200, CHECK_CMD) + long_bash(150, 230, "sleep 80", "d3")
    run = k1(tmp_path, {h.AGENT1: file_bytes(events)})
    assert run.first["LONGCALL_CHECK"] == 80
    assert run.first["LONGCALL_OTHER"] == 30


def test_t17b_edit_longer_than_the_long_call_threshold_stays_hook(tmp_path):
    events = [h.edit(120, "d2", **DKW), h.result(200, "d2", **DKW)]
    run = k1(tmp_path, {h.AGENT1: file_bytes(events)})
    assert run.first["SUBAGENT_HOOK"] == 80
    assert run.first["SUBAGENT_GATE"] == 0


def unusable_run(tmp_path, data):
    run = k1(tmp_path, {h.AGENT1: data})
    assert run.set.records.unusable == [h.AGENT1]
    assert (run.set.matched, run.set.total) == (0, 2)
    assert run.set.inventory == []
    assert run.first["SUBAGENT_UNSPLIT"] == 300
    assert run.first["SUBAGENT_WORK"] == 0


def test_t17g_zero_byte_file_is_unsplit_not_matched(tmp_path):
    unusable_run(tmp_path, b"")


def test_t17g_garbage_only_file_is_unsplit_not_matched(tmp_path):
    unusable_run(tmp_path, b"garbage\nmore garbage\n")


def test_t17g_all_untimed_file_is_unsplit_not_matched(tmp_path):
    untimed = [h.say(11, ts=False, **DKW), h.say(12, ts=False, **DKW)]
    unusable_run(tmp_path, file_bytes(untimed))


def test_t17g_timestamped_file_without_calls_still_matches(tmp_path):
    run = k1(tmp_path, {h.AGENT1: file_bytes([h.say(11, **DKW)])})
    assert run.set.records.unusable == []
    assert run.set.matched == 1
    assert run.first["SUBAGENT_WORK"] == 300


def test_t17g_plain_dict_records_are_unsplit_not_an_error(tmp_path):
    run = k1(tmp_path)
    plain = m.DelegateSet({}, [], 0, 2, [])
    context = k.build_context(run.cycles, run.transcript, run.params)
    result = dl.split_context(context, plain)
    assert result.cycles[0]["SUBAGENT_UNSPLIT"] == 300


def test_t17g_plain_dict_records_have_no_call_counts():
    plain = m.DelegateSet({}, [], 0, 2, [])
    assert dl.call_counts(plain) == (0, 0, 0)


def test_t17c_unmatched_dispatch_stays_unsplit(tmp_path):
    body = dispatch(10, 60, "x1", h.AGENT1) + dispatch(70, 100, "x2", h.AGENT2)
    gate = [
        h.runlong_launch(20, "d1", "dart", "gate", **DKW),
        h.result(40, "d1", **DKW),
    ]
    run = Run(closed(body), {h.AGENT1: file_bytes(gate)}, tmp_path)
    assert four(run.first) == {"work": 30, "gate": 20, "hook": 0, "unsplit": 30}
    assert (
        run.first["SUBAGENT_UNSPLIT"] == run.split.by_role["sonnet"]["SUBAGENT_UNSPLIT"]
    )
    cov = dl.coverage(run.set)
    assert (cov.matched, cov.total) == (1, 2)


def test_t17c_concurrent_dispatches_share_equally(tmp_path):
    body = dispatch(10, 70, "x1", h.AGENT1) + dispatch(10, 70, "x2", h.AGENT2)
    run = Run(closed(body), {h.AGENT1: file_bytes([h.say(11, **DKW)])}, tmp_path)
    assert four(run.first) == {"work": 30, "gate": 0, "hook": 0, "unsplit": 30}


def test_t17c_unmatched_under_machine_wait_adds_nothing(tmp_path):
    run = k1(tmp_path)
    assert run.first["SUBAGENT_UNSPLIT"] == 0


def test_t17c_unreadable_file_is_unsplit_not_zero(tmp_path):
    base = h.write_delegate_dir(Path(tmp_path) / "agents", {})
    (base / f"agent-{h.AGENT1}.jsonl").mkdir()
    events = h.k1_events("skill")
    transcript = r.parse_bytes(h.build_transcript(events))
    p = params()
    cs = c.analyze_cycles(transcript, p)
    ds = dl.read_delegates(base, k.classify(cs, transcript, p).dispatches, p)
    assert ds.matched == 0
    split = dl.split(cs, transcript, p, ds)
    assert split.cycles[0]["SUBAGENT_UNSPLIT"] == 300
    assert split.cycles[0]["SUBAGENT_WORK"] == 0


def test_t17d_ack_and_notification_ids_agree_where_both_exist():
    body = (
        dispatch(10, 60, "x1", h.AGENT1, task=h.AGENT1)
        + dispatch(70, 100, "x2", h.AGENT2, task="other-task")
        + [h.agent(105, "x3", "sonnet", "spec")]
    )
    t = r.parse_bytes(h.build_transcript(closed(body)))
    check = dl.map_check(k.dispatch_intervals(t))
    assert (check.dispatches, check.with_id, check.both, check.agree) == (3, 2, 2, 1)


def test_t17d_dispatch_matches_by_the_ack_id_first(tmp_path):
    body = dispatch(10, 60, "x1", h.AGENT1, task=h.AGENT2)
    files = {h.AGENT1: file_bytes([h.say(11, **DKW)])}
    run = Run(closed(body), files, tmp_path)
    assert run.set.matched == 1
    assert list(run.set.records) == [h.AGENT1]


def test_t17d_dispatch_without_ack_matches_by_task_id(tmp_path):
    body = [h.agent(10, "x1", "sonnet", "spec"), h.enqueue(60, "x1", h.AGENT1, 1000)]
    files = {h.AGENT1: file_bytes([h.say(11, **DKW)])}
    run = Run(closed(body), files, tmp_path)
    assert run.set.matched == 1
    assert run.first["SUBAGENT_WORK"] == 50


def test_t17d_id_outside_the_id_channel_is_not_used(tmp_path):
    base = Path(tmp_path) / "agents"
    (base / "agent-sub").mkdir(parents=True)
    (base / "agent-sub" / "zz.jsonl").write_bytes(file_bytes([h.say(11, **DKW)]))
    bad = m.Dispatch(
        "x1", "sub/zz", None, "sonnet", None, ms(10), None, ms(60), "enqueue"
    )
    ds = dl.read_delegates(base, [bad], params())
    assert (ds.matched, ds.total) == (0, 1)
    assert ds.inventory == []


def test_t17e_cycle_dispatches_selects_by_start_in_window(tmp_path):
    body = dispatch(10, 60, "x1", h.AGENT1)
    events = (
        [h.agent(-50, "early", "sonnet", "spec")]
        + closed(body)
        + [h.agent(9000, "late", "sonnet", "spec")]
    )
    t = r.parse_bytes(h.build_transcript(events))
    p = params()
    cs = c.analyze_cycles(t, p)
    all_dispatches = k.dispatch_intervals(t)
    chosen = dl.cycle_dispatches(all_dispatches, cs.windows)
    assert [d.tool_use_id for d in all_dispatches] == ["early", "x1", "late"]
    assert [d.tool_use_id for d in chosen] == ["x1"]


def test_t17f_call_counts_cover_runlong_and_background(tmp_path):
    events = [
        h.runlong_launch(120, "d1", "dart", "gate", **DKW),
        h.result(220, "d1", **DKW),
        h.runlong_wait(225, "d5", "dart", bg=False, **DKW),
        h.result(226, "d5", **DKW),
        h.bash(230, "d3", "sleep 5", bg=True, **DKW),
        h.result(231, "d3", **DKW),
        h.bash(240, "d4", "ls", **DKW),
        h.result(241, "d4", **DKW),
    ]
    run = k1(tmp_path, {h.AGENT1: file_bytes(events)})
    counts = dl.call_counts(run.set)
    assert (counts.files, counts.runlong_calls, counts.background_calls) == (1, 2, 1)


# ---------------------------------------------------------------------------------------------
# t18: delegates absent
# ---------------------------------------------------------------------------------------------


def absent_checks(run):
    cov = dl.coverage(run.set)
    assert (cov.matched, cov.total) == (0, 2)
    assert dl.coverage_line(cov) == "COVERAGE matched=0/2"
    assert cov.weak is True
    assert run.first["SUBAGENT_UNSPLIT"] == 300
    assert run.first["SUBAGENT_WORK"] == 0
    assert run.first["SUBAGENT_GATE"] == 0
    assert run.first["SUBAGENT_HOOK"] == 0
    assert run.set.inventory == []


def test_t18a_missing_directory_leaves_all_unsplit(tmp_path):
    run = k1(tmp_path, {})
    run.dir.rmdir()
    run.set = dl.read_delegates(run.dir, run.classes.dispatches, run.params)
    run.split = dl.split(run.cycles, run.transcript, run.params, run.set)
    assert not run.dir.exists()
    absent_checks(run)


def test_t18b_empty_directory_leaves_all_unsplit(tmp_path):
    absent_checks(k1(tmp_path, {}))


def test_t18b_unrelated_file_does_not_match(tmp_path):
    stray = file_bytes([h.say(11, **DKW)])
    absent_checks(k1(tmp_path, {"zzother0000000001": stray}))


def test_t18c_zero_dispatches_changes_nothing(tmp_path):
    run = Run(closed([h.say(5)]), {}, tmp_path)
    cov = dl.coverage(run.set)
    assert (cov.matched, cov.total) == (0, 0)
    assert dl.coverage_line(cov) == "COVERAGE matched=0/0"
    assert cov.weak is False
    assert all(run.first[x] == 0 for x in KEYS)
    assert run.split.by_role == {}


def test_t18c_weak_coverage_is_under_eighty_percent():
    def weak(matched, total):
        return dl.coverage(m.DelegateSet({}, [], matched, total, [])).weak

    assert weak(4, 5) is False
    assert weak(3, 5) is True
    assert weak(0, 0) is False


def orphan_run(tmp_path):
    body = [h.agent(10, "x1", "sonnet", "spec"), h.ack(11, "x1", h.AGENT1)]
    files = {h.AGENT1: file_bytes([h.say(11, **DKW)])}
    return Run(closed(body), files, tmp_path)


def test_t18c_orphan_dispatch_is_counted(tmp_path):
    cov = dl.coverage(orphan_run(tmp_path).set)
    assert (cov.total, cov.orphans) == (1, 1)


def test_t18c_orphan_dispatch_file_is_not_read(tmp_path):
    run = orphan_run(tmp_path)
    assert dl.coverage(run.set).matched == 0
    assert run.set.inventory == []


# ---------------------------------------------------------------------------------------------
# t34d: the read, the pins and the inventory
# ---------------------------------------------------------------------------------------------


def torn(data):
    return data + b'{"type": "assistant", "message": {"con'


def test_t34da_records_bytes_and_hash_of_the_complete_lines(tmp_path):
    whole = agent_file()
    run = k1(tmp_path, {h.AGENT1: torn(whole)})
    record = run.set.records[h.AGENT1]
    assert record.bytes_read == len(whole)
    assert record.sha256 == hashlib.sha256(whole).hexdigest()
    assert run.set.inventory == [
        {"id": h.AGENT1, "bytes": len(whole), "sha256": record.sha256}
    ]


def test_t34da_torn_last_line_is_cut_not_counted(tmp_path):
    run = k1(tmp_path, {h.AGENT1: torn(agent_file())})
    assert run.set.records.unparseable == 0
    assert four(run.first) == {"work": 170, "gate": 100, "hook": 30, "unsplit": 0}


def test_t34da_inventory_is_sorted_by_id(tmp_path):
    files = dict(h.k1_delegate_files())
    files[h.AGENT2] = file_bytes([h.say(901, sidechain=True)])
    run = k1(tmp_path, files)
    assert [e["id"] for e in run.set.inventory] == [h.AGENT1, h.AGENT2]


def test_t34da_one_inventory_entry_per_file_across_resumed_dispatches(tmp_path):
    body = dispatch(10, 60, "x1", h.AGENT1, task=h.AGENT1) + [
        h.sendmsg(70, "x2", h.AGENT1, "resume"),
        h.ack(71, "x2", h.AGENT1),
        h.enqueue(100, "x2", h.AGENT1, 1000),
    ]
    run = Run(closed(body), {h.AGENT1: file_bytes([h.say(11, **DKW)])}, tmp_path)
    assert (run.set.matched, run.set.total) == (2, 2)
    assert len(run.set.inventory) == 1


def pinned_fixture(tmp_path):
    """A first read of a file, then the file grown by one more gate call."""
    first = k1(tmp_path)
    entry = first.set.inventory[0]
    path = first.dir / f"agent-{h.AGENT1}.jsonl"
    extra = file_bytes(
        [
            h.runlong_launch(300, "d9", "dart9", "gate", **DKW),
            h.result(350, "d9", **DKW),
        ]
    )
    path.write_bytes(path.read_bytes() + extra)
    return entry, path


def test_t34db_pins_read_to_the_pinned_size(tmp_path):
    entry, path = pinned_fixture(tmp_path)
    grown = k1(tmp_path, {h.AGENT1: path.read_bytes()})
    assert grown.first["GATE_RUNLONG"] == 150
    pinned = k1(tmp_path, {h.AGENT1: path.read_bytes()}, pins=[entry])
    record = pinned.set.records[h.AGENT1]
    assert record.bytes_read == entry["bytes"]
    assert record.sha256 == entry["sha256"]
    assert pinned.first["GATE_RUNLONG"] == 100
    assert pinned.set.inventory == [entry]


def test_t34db_pin_hash_mismatch_is_refused(tmp_path):
    entry, path = pinned_fixture(tmp_path)
    bad = dict(entry, sha256="0" * 64)
    with pytest.raises(r.PinError):
        k1(tmp_path, {h.AGENT1: path.read_bytes()}, pins=[bad])


def test_t34db_pinned_file_missing_is_expired_and_unsplit(tmp_path):
    entry, path = pinned_fixture(tmp_path)
    path.unlink()
    run = k1(tmp_path, {}, pins=[entry])
    assert run.set.records.expired == [h.AGENT1]
    assert run.set.matched == 0
    assert run.first["SUBAGENT_UNSPLIT"] == 300


def test_t34db_pinned_file_shorter_is_expired_and_unsplit(tmp_path):
    entry, path = pinned_fixture(tmp_path)
    short = path.read_bytes()[: entry["bytes"] - 5]
    run = k1(tmp_path, {h.AGENT1: short}, pins=[entry])
    assert run.set.records.expired == [h.AGENT1]
    assert run.set.matched == 0
    assert run.first["SUBAGENT_UNSPLIT"] == 300


def test_t34db_unpinned_id_is_read_normally(tmp_path):
    entry, path = pinned_fixture(tmp_path)
    other = dict(entry, id="aid00000000000009")
    run = k1(tmp_path, {h.AGENT1: path.read_bytes()}, pins=[other])
    assert run.first["GATE_RUNLONG"] == 150
    assert run.set.records.expired == []


UNUSED = "aid00000000000009"


@pytest.mark.parametrize(
    "pin",
    [
        {"id": "bad/id", "bytes": 1, "sha256": "0" * 64},
        {"id": UNUSED, "bytes": -1, "sha256": "0" * 64},
        {"id": UNUSED, "bytes": True, "sha256": "0" * 64},
        {"id": UNUSED, "bytes": 1, "sha256": "zz"},
        {"id": UNUSED, "bytes": 1},
    ],
)
def test_t34db_malformed_pin_is_rejected(tmp_path, pin):
    with pytest.raises(ValueError):
        k1(tmp_path, pins=[pin])


def test_t34db_conflicting_duplicate_pins_are_rejected(tmp_path):
    entry, path = pinned_fixture(tmp_path)
    other = dict(entry, bytes=entry["bytes"] + 1)
    with pytest.raises(ValueError):
        k1(tmp_path, {h.AGENT1: path.read_bytes()}, pins=[entry, other])


def test_t34db_identical_duplicate_pins_are_tolerated(tmp_path):
    entry, path = pinned_fixture(tmp_path)
    run = k1(tmp_path, {h.AGENT1: path.read_bytes()}, pins=[entry, dict(entry)])
    assert run.set.inventory == [entry]


def test_t34db_pin_ending_inside_a_line_is_malformed(tmp_path):
    entry, path = pinned_fixture(tmp_path)
    data = path.read_bytes()
    cut = entry["bytes"] - 5
    pin = {
        "id": h.AGENT1,
        "bytes": cut,
        "sha256": hashlib.sha256(data[:cut]).hexdigest(),
    }
    with pytest.raises(ValueError):
        k1(tmp_path, {h.AGENT1: data}, pins=[pin])


def test_t34db_file_shrinking_after_the_stat_is_expired(tmp_path, monkeypatch):
    entry, path = pinned_fixture(tmp_path)
    path.write_bytes(path.read_bytes()[: entry["bytes"] - 5])
    real = os.stat
    calls = []

    def stat(target):
        calls.append(target)
        if len(calls) == 1:
            return types.SimpleNamespace(st_size=10**9)
        return real(target)

    monkeypatch.setattr(dl, "os", types.SimpleNamespace(stat=stat))
    run = k1(tmp_path, {}, pins=[entry])
    assert run.set.records.expired == [h.AGENT1]
    assert run.set.matched == 0


def inventory_fixture(tmp_path):
    run = k1(tmp_path)
    return run.set.inventory, run.dir, run.dir / f"agent-{h.AGENT1}.jsonl"


def test_t34dc_current_after_a_record_is_appended(tmp_path):
    inventory, directory, path = inventory_fixture(tmp_path)
    assert dl.inventory_status(inventory, directory) == "current"
    path.write_bytes(path.read_bytes() + file_bytes([h.say(300, **DKW)]))
    assert dl.inventory_status(inventory, directory) == "current"
    path.write_bytes(path.read_bytes() + b'{"torn": tr')
    assert dl.inventory_status(inventory, directory) == "current"


def test_t34dd_missing_file_is_expired(tmp_path):
    inventory, directory, path = inventory_fixture(tmp_path)
    path.unlink()
    assert dl.inventory_status(inventory, directory) == "expired"


def test_t34dd_missing_directory_is_expired(tmp_path):
    inventory, directory, path = inventory_fixture(tmp_path)
    path.unlink()
    directory.rmdir()
    assert dl.inventory_status(inventory, directory) == "expired"


def test_t34dd_shortened_file_is_expired(tmp_path):
    inventory, directory, path = inventory_fixture(tmp_path)
    path.write_bytes(path.read_bytes()[: inventory[0]["bytes"] - 1])
    assert dl.inventory_status(inventory, directory) == "expired"


def test_t34dd_changed_prefix_is_expired(tmp_path):
    inventory, directory, path = inventory_fixture(tmp_path)
    data = bytearray(path.read_bytes())
    data[10] = ord("X") if data[10] != ord("X") else ord("Y")
    path.write_bytes(bytes(data) + b"\n" + b"appended\n")
    assert dl.inventory_status(inventory, directory) == "expired"


def test_t34dd_one_expired_file_expires_the_inventory(tmp_path):
    first = k1(tmp_path)
    entries = first.set.inventory + [
        {"id": h.AGENT2, "bytes": 3, "sha256": hashlib.sha256(b"abc").hexdigest()}
    ]
    assert dl.inventory_status(entries, first.dir) == "expired"


def test_t34dd_malformed_inventory_id_is_expired(tmp_path):
    entry = {"id": "../escape", "bytes": 0, "sha256": hashlib.sha256(b"").hexdigest()}
    assert dl.inventory_status([entry], tmp_path) == "expired"


# ---------------------------------------------------------------------------------------------
# t47: INTERVALCHECK, the rebuilt dispatch intervals against the classes module's
# ---------------------------------------------------------------------------------------------


def expected_spans(dispatches, lo, hi):
    """The classes module's dispatch intervals clipped to `[lo, hi)`: written out here."""
    return [
        (d.tool_use_id, max(d.start_ms, lo), min(d.end_ms, hi))
        for d in dispatches
        if d.end_ms is not None and d.end_ms > d.start_ms and lo <= d.start_ms < hi
    ]


def rebuilt(dispatches, bounds):
    return [
        [(s.tool_use_id, s.a, s.b) for s in row]
        for row in dl.window_spans(dispatches, bounds)
    ]


def k1_dispatches():
    t = r.parse_bytes(h.k1_transcript("skill"))
    return k.dispatch_intervals(t)


def test_t47_intervals_are_clipped_to_the_window_end():
    ds = k1_dispatches()
    bounds = [(ms(50), ms(250))]
    assert rebuilt(ds, bounds) == [expected_spans(ds, ms(50), ms(250))]
    assert rebuilt(ds, bounds) == [[("a1", ms(100), ms(250))]]


def test_t47_interval_joins_a_window_by_its_start():
    ds = k1_dispatches()
    bounds = [(ms(150), ms(950))]
    assert rebuilt(ds, bounds) == [expected_spans(ds, ms(150), ms(950))]
    assert rebuilt(ds, bounds) == [[("a2", ms(900), ms(950))]]


def test_t47_several_windows_are_kept_apart():
    ds = k1_dispatches()
    bounds = [(ms(0), ms(500)), (ms(500), ms(1000))]
    assert rebuilt(ds, bounds) == [
        [("a1", ms(100), ms(400))],
        [("a2", ms(900), ms(1000))],
    ]


def test_t47_real_window_equals_the_classes_dispatch_intervals():
    t = r.parse_bytes(h.k1_transcript("skill"))
    p = params()
    cs = c.analyze_cycles(t, p)
    ds = k.dispatch_intervals(t)
    win = cs.windows[0]
    bounds = [(win.start_t_ms, win.end_t_ms)]
    assert rebuilt(ds, bounds) == [expected_spans(ds, *bounds[0])]
    assert len(rebuilt(ds, bounds)[0]) == 2


def test_t47_orphan_and_zero_length_dispatches_have_no_interval():
    orphan = m.Dispatch("o", None, None, "sonnet", None, ms(10), None, None, "orphan")
    flat = m.Dispatch("z", None, None, "sonnet", None, ms(20), None, ms(20), "enqueue")
    assert dl.window_spans([orphan, flat], [(ms(0), ms(100))]) == [[]]


def test_t47_spans_carry_role_and_agent_id():
    ds = k1_dispatches()
    spans = dl.window_spans(ds, [(ms(0), ms(500))])[0]
    assert [(s.role, s.agent_id) for s in spans] == [("sonnet", h.AGENT1)]

"""Tests for the cycle-profile classes module, slice A: intervals, sweep, HOOK, idle, fence.

Every transcript is synthetic (fictional base date 2030-01-01, invented ids, lorem text),
built from `cycle_profile_helpers`. The known-answer cycles K1 and K1w are the hand tables
of spec 6.3; the fence rows recompute the Tukey fences from the formula with
`statistics.quantiles`, independently of the library. Cycles are closed by a real dev-tip
suite and a `Skill` commit marker (`closed`), so every row builds its windows through
`analyze_cycles`.
"""

import math
import re
import statistics
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import cycle_profile_classes as k  # noqa: E402, I001
import cycle_profile_cycles as c  # noqa: E402
import cycle_profile_helpers as h  # noqa: E402
import cycle_profile_model as m  # noqa: E402
import cycle_profile_records as r  # noqa: E402

MS = 1000
CLASSES = m.CLASSES


def ms(seconds):
    """Offset in seconds from the fictional base, as integer epoch milliseconds."""
    return h.BASE_MS + round(seconds * MS)


def params(**over):
    return m.Params(min_cycles=1, **over)


def analyze(events, **over):
    """Parse synthetic events and return `(cycle_set, transcript, params)`."""
    p = params(**over)
    t = r.parse_bytes(h.build_transcript(events))
    return c.analyze_cycles(t, p), t, p


def classify(events, fence_method=None, **over):
    cs, t, p = analyze(events, **over)
    if fence_method is None:
        return k.classify(cs, t, p)
    return k.classify(cs, t, p, fence_method=fence_method)


def k1(variant, **over):
    p = params(**over)
    t = r.parse_bytes(h.k1_transcript(variant))
    cs = c.analyze_cycles(t, p)
    return k.classify(cs, t, p)


def rounded(cycle, names=CLASSES):
    return {x: round(cycle.class_s[x]) for x in names}


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


def edits(spans):
    """Edit tool_use and result records for `(start, end)` spans."""
    out = []
    for n, (a, b) in enumerate(spans):
        out += [h.edit(a, f"e{n}"), h.result(b, f"e{n}")]
    return out


def hook_s(spans, **over):
    cycle = classify(closed(edits(spans)), **over).cycles[0]
    return cycle.class_s["HOOK"]


def gap_body(gaps):
    """An assistant record then a human prompt per gap, in the order given."""
    out, now = [], 1
    for gap in gaps:
        out += [h.say(now), h.prompt(now + gap)]
        now += gap + 1
    return out


def gap_classes(gaps, fence_method=None, **over):
    cycle = classify(closed(gap_body(gaps)), fence_method, **over).cycles[0]
    return cycle.class_s["AWAY"], cycle.class_s["OPERATOR_RESPONSE"]


BASE_GAPS = [10, 20, 30, 40, 50, 60, 70, 80, 90]


def hand_fence(gaps, k_=1.5, method="inclusive"):
    """(q1, q3, fence seconds) from the Tukey formula, in log10 space."""
    logs = sorted(math.log10(g) for g in gaps)
    q1, _, q3 = statistics.quantiles(logs, n=4, method=method)
    return q1, q3, 10 ** (q3 + k_ * (q3 - q1))


# ---------------------------------------------------------------------------------------
# t01: the known-answer classes
# ---------------------------------------------------------------------------------------


def test_t01a_k1_classes_match_the_hand_table():
    cycle = k1("skill").cycles[0]
    assert rounded(cycle) == {
        "CONTROLLER": 390,
        "SUBAGENT": 300,
        "MACHINE_WAIT": 1650,
        "HOOK": 30,
        "OPERATOR_RESPONSE": 60,
        "AWAY": 0,
    }
    assert round(sum(cycle.class_s.values())) == 2430


def test_t01b_k1w_wrapper_commit_is_a_human_prompt():
    cycle = k1("wrapper").cycles[0]
    assert rounded(cycle) == {
        "CONTROLLER": 360,
        "SUBAGENT": 300,
        "MACHINE_WAIT": 1650,
        "HOOK": 30,
        "OPERATOR_RESPONSE": 90,
        "AWAY": 0,
    }
    assert len(cycle.idle_gaps_s) == 2
    assert round(sum(cycle.class_s.values())) == 2430


def test_t01c_k1_at_hook_latency_5():
    cycle = k1("skill", hook_latency_s=5).cycles[0]
    assert round(cycle.class_s["HOOK"]) == 49
    assert round(cycle.class_s["CONTROLLER"]) == 371
    assert round(cycle.class_s["OPERATOR_RESPONSE"]) == 60


def test_t01d_k1w_at_hook_latency_5():
    cycle = k1("wrapper", hook_latency_s=5).cycles[0]
    assert round(cycle.class_s["HOOK"]) == 49
    assert round(cycle.class_s["CONTROLLER"]) == 341
    assert round(cycle.class_s["OPERATOR_RESPONSE"]) == 90


def test_t01e_k1_machine_wait_is_split_by_check_class():
    p = params()
    t = r.parse_bytes(h.k1_transcript("skill"))
    cs = c.analyze_cycles(t, p)
    context = k.build_context(cs, t, p)
    segments = k.window_segments(context, cs.windows[0])
    by_check = {}
    for seg in segments:
        if seg.cls == "MACHINE_WAIT":
            for check, secs in seg.parts.items():
                by_check[check] = by_check.get(check, 0.0) + secs
    assert {x: round(v) for x, v in by_check.items()} == {
        "suite-feature-tip": 900,
        "suite-dev-tip": 750,
    }


# ---------------------------------------------------------------------------------------
# t02: the class oracle over seeded random cycles
# ---------------------------------------------------------------------------------------

_ENQ_ID = re.compile(r"<tool-use-id>([^<]*)")


def oracle_intervals(cycle, hook_threshold_s=10):
    """Interval lists for `brute_force_classes`, read straight off the generator events."""
    uses, results, enq = {}, {}, {}
    for item in cycle.events:
        rec, at = item["rec"], item["at"]
        if rec["type"] == "assistant":
            for blk in rec["message"]["content"]:
                if blk.get("type") == "tool_use":
                    uses[blk["id"]] = (at, blk["name"], blk["input"])
        elif rec["type"] == "user" and isinstance(rec["message"]["content"], list):
            for blk in rec["message"]["content"]:
                results.setdefault(blk["tool_use_id"], at)
        elif rec["type"] == "queue-operation":
            tid = _ENQ_ID.search(rec["content"]).group(1)
            enq[tid] = min(at, enq.get(tid, at))
    machine, sub, hook = [], [], []
    for tid, (at, name, inp) in uses.items():
        if name == "Agent" and enq.get(tid, 0) > at:
            sub.append((at, enq[tid]))
        elif name == "Bash" and "audit.sh --tests" in inp["command"]:
            end = enq["w" + tid[1:]]
            machine.append((at, end))
        elif name == "Edit" and results[tid] - at > hook_threshold_s:
            hook.append((at, results[tid]))
    return machine, sub, hook, plain_idle(cycle, machine + sub)


def plain_idle(cycle, busy):
    """Idle gaps by a plain loop: last assistant record to the next human prompt."""
    out, last = [], None
    for item in sorted(cycle.events, key=lambda e: e["at"]):
        rec, at = item["rec"], item["at"]
        content = rec.get("message", {}).get("content")
        if rec["type"] == "assistant":
            last = at
        elif isinstance(content, str) and not content.startswith("<"):
            if last is not None and at > last:
                covered = sum(max(0, min(b, at) - max(a, last)) for a, b in merge(busy))
                free = (at - last) - covered
                if covered == 0 or free > 1:
                    out.append((last, at))
            last = None
    return out


def merge(spans):
    out = []
    for a, b in sorted(spans):
        if out and a <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


@pytest.mark.parametrize("first", [0, 50, 100, 150])
def test_t02_class_seconds_equal_the_per_second_oracle(first):
    for seed in range(first, first + 50):
        cycle = h.random_cycle(seed)
        machine, sub, hook, idle = oracle_intervals(cycle)
        want = h.brute_force_classes(
            {
                "MACHINE_WAIT": machine,
                "SUBAGENT": sub,
                "HOOK": hook,
                "OPERATOR_RESPONSE": idle,
            },
            0,
            cycle.end_s,
        )
        p = params()
        t = r.parse_bytes(cycle.data)
        cs = c.analyze_cycles(t, p)
        win = cs.windows[0]
        assert (win.end_t_ms - win.start_t_ms) // MS == cycle.wall_s
        got = k.classify(cs, t, p).cycles[0].class_s
        for name in CLASSES:
            assert got[name] >= 0
            assert got[name] == pytest.approx(want[name], abs=1e-6), (seed, name)
        assert sum(got.values()) == pytest.approx(cycle.wall_s, abs=1e-6)


# ---------------------------------------------------------------------------------------
# t03: overlap, hidden time and agent-seconds
# ---------------------------------------------------------------------------------------


def test_t03a_a_subagent_under_the_suite_is_hidden_not_counted():
    cycle = k1("skill").cycles[0]
    assert round(cycle.class_s["SUBAGENT"]) == 300
    assert round(cycle.hidden_under_machine_wait_s) == 300


def test_t03b_agent_seconds_are_the_sum_of_individual_durations():
    assert round(k1("skill").cycles[0].agent_seconds) == 600


def concurrent_agents():
    body = []
    for n, (a, b) in enumerate([(100, 400), (200, 500)]):
        body += [
            h.agent(a, f"a{n}", "sonnet", "task " + h.lorem(n)),
            h.ack(a + 1, f"a{n}", f"aid{n:014d}"),
            h.enqueue(b, f"a{n}", f"t{n}", (b - a) * MS),
        ]
    return closed(body)


def test_t03c_two_concurrent_subagents_are_one_interval():
    cycle = classify(concurrent_agents()).cycles[0]
    assert round(cycle.class_s["SUBAGENT"]) == 400
    assert round(cycle.agent_seconds) == 600


def test_t03d_peer_suites_overlap_the_window_any_and_recorded():
    def suite(n, art, launch, terminal):
        return [
            h.runlong_launch(launch, f"l{n}", art, "dev-tip abc1234"),
            h.result(launch + 1, f"l{n}"),
            h.runlong_wait(launch + 2, f"w{n}", art),
            h.result(launch + 3, f"w{n}"),
            h.enqueue(terminal, f"w{n}", f"t{n}", 1000),
        ]

    events = suite(1, "pa", 500, 1500) + suite(2, "pb", 700, 1200)
    events += [h.runlong_wait(1100, "x1", "pa"), h.say(1900)]
    t = r.parse_bytes(h.build_transcript(events))
    pairs = c.analyze_cycles(t, params()).pairs
    seen, recorded = k.peer_overlap_s(k.main_events(t), pairs, ms(1000), ms(2000))
    assert round(seen) == 700
    assert round(recorded) == 500


# ---------------------------------------------------------------------------------------
# t04: HOOK
# ---------------------------------------------------------------------------------------


def test_t04a_hook_is_the_union_of_overlapping_edits_not_the_sum():
    assert hook_s([(100, 120), (110, 140), (130, 150)]) == pytest.approx(50)


def test_t04b_hook_threshold_is_strictly_greater_than():
    spans = [(100, 109), (200, 210.000), (300, 310.001)]
    assert hook_s(spans) == pytest.approx(10.001)


def test_t04c_hook_ignores_a_tiny_hook_success_beside_a_long_edit():
    body = edits([(100, 160)]) + [h.hook(161, "e0", 5)]
    cycle = classify(closed(body)).cycles[0]
    assert cycle.class_s["HOOK"] == pytest.approx(60)


def test_t04d_hook_latency_flag_changes_the_result():
    spans = [(100, 109), (200, 212)]
    assert hook_s(spans) == pytest.approx(12)
    assert hook_s(spans, hook_latency_s=5) == pytest.approx(21)
    assert hook_s(spans, hook_latency_s=30) == pytest.approx(0)


def test_t04e_reported_hook_time_is_the_hook_success_sum():
    t = r.parse_bytes(h.build_transcript(closed([h.hook(50, "e0", 1500)])))
    events = k.main_events(t)
    assert k.hook_reported_s(events, ms(0), ms(100)) == pytest.approx(1.5)
    assert k.hook_reported_s(events, ms(60), ms(100)) == pytest.approx(0)


# ---------------------------------------------------------------------------------------
# t05, t06: dispatch intervals
# ---------------------------------------------------------------------------------------


def only_dispatch(events):
    t = r.parse_bytes(h.build_transcript(events))
    out = k.dispatch_intervals(t)
    assert len(out) == 1
    return out[0]


def test_t05a_the_interval_ends_at_the_enqueue_not_the_ack():
    d = only_dispatch(
        [
            h.agent(100, "a1", "sonnet", "spec " + h.lorem(1)),
            h.ack(101, "a1", h.AGENT1),
            h.enqueue(400, "a1", "t1", 300000),
            h.delivery(401, "a1", "t1", 300000),
        ]
    )
    assert (d.start_ms, d.ack_ms, d.end_ms) == (ms(100), ms(101), ms(400))
    assert (d.end_ms - d.start_ms) / MS == 300
    assert d.end_source == "enqueue"


def test_t05b_without_an_enqueue_the_delivery_ends_the_interval():
    d = only_dispatch(
        [
            h.agent(100, "a1", "sonnet", "spec " + h.lorem(1)),
            h.ack(101, "a1", h.AGENT1),
            h.delivery(450, "a1", "t1", 350000),
        ]
    )
    assert (d.end_ms, d.end_source) == (ms(450), "delivery")


def test_t05c_a_dispatch_with_neither_record_is_an_orphan():
    events = closed(
        [h.agent(100, "a1", "sonnet", "spec " + h.lorem(1)), h.ack(101, "a1", h.AGENT1)]
    )
    d = only_dispatch(events[:3])
    assert (d.end_ms, d.end_source) == (None, "orphan")
    assert classify(events).cycles[0].class_s["SUBAGENT"] == 0


def resumed_events():
    return [
        h.agent(100, "a1", "sonnet", "spec " + h.lorem(1)),
        h.ack(101, "a1", h.AGENT1),
        h.enqueue(400, "a1", "t1", 300000),
        h.sendmsg(1000, "m1", "a1", "fold " + h.lorem(2)),
        h.enqueue(1300, "m1", "t1", 300000),
    ]


def test_t06a_a_reused_task_id_gives_two_separate_intervals():
    t = r.parse_bytes(h.build_transcript(resumed_events()))
    spans = [(d.start_ms, d.end_ms) for d in k.dispatch_intervals(t)]
    assert spans == [(ms(100), ms(400)), (ms(1000), ms(1300))]
    cycle = classify(closed(resumed_events())).cycles[0]
    assert round(cycle.class_s["SUBAGENT"]) == 600


def test_t06b_a_resumed_dispatch_takes_the_role_of_its_task():
    t = r.parse_bytes(h.build_transcript(resumed_events()))
    first, second = k.dispatch_intervals(t)
    assert (first.role, second.role) == ("sonnet", "sonnet")
    assert first.task_id == second.task_id == "t1"
    assert second.agent_id == first.agent_id == h.AGENT1


# ---------------------------------------------------------------------------------------
# t07: the AWAY fence
# ---------------------------------------------------------------------------------------


def test_t07a_default_fence_sends_the_350_gap_to_away():
    away, operator = gap_classes(BASE_GAPS + [350])
    assert (round(away), round(operator)) == (350, 450)


def test_t07b_outer_fence_k3_leaves_the_350_gap_with_the_operator():
    away, operator = gap_classes(BASE_GAPS + [350], away_fence_k=3.0)
    assert (round(away), round(operator)) == (0, 800)


def test_t07c_exclusive_method_leaves_the_350_gap_with_the_operator():
    away, operator = gap_classes(BASE_GAPS + [350], fence_method="exclusive")
    assert (round(away), round(operator)) == (0, 800)


@pytest.mark.parametrize(
    "variant",
    [({}, None), ({"away_fence_k": 3.0}, None), ({}, "exclusive")],
)
def test_t07d_a_100000_s_gap_is_away_under_every_variant(variant):
    over, method = variant
    away, operator = gap_classes(BASE_GAPS + [100000], method, **over)
    assert (round(away), round(operator)) == (100000, 450)


def test_t07e_fewer_gaps_than_the_floor_give_no_away():
    gaps = [10, 20, 30, 40, 50, 60, 100000]
    away, operator = gap_classes(gaps)
    assert (round(away), round(operator)) == (0, 100000 + 210)
    away, operator = gap_classes(gaps, away_min_gaps=3)
    assert round(away) == 100000


def test_t07f_below_the_floor_the_quartile_consumers_skip():
    gaps = [10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 100000.0]
    fence = k.away_fence(gaps)
    assert (fence.q1, fence.q3, fence.inner_s, fence.outer_s) == (None,) * 4
    assert k.leave_one_out_range(gaps) is None
    assert k.count_above(gaps, fence) == 0
    assert k.away_fence(gaps, min_gaps=3).inner_s is not None


def test_t07g_the_hand_fences_to_three_significant_figures():
    gaps = [float(x) for x in BASE_GAPS + [350]]
    fence = k.away_fence(gaps)
    q1, q3, _ = hand_fence(gaps)
    assert (round(q1, 5), round(q3, 5)) == (1.50836, 1.88859)
    assert (round(fence.q1, 5), round(fence.q3, 5)) == (1.50836, 1.88859)
    assert float("%.4g" % fence.inner_s) == 287.7
    assert float("%.5g" % fence.outer_s) == 1069.8
    assert float("%.4g" % hand_fence(gaps, 1.5, "exclusive")[2]) == 436.6
    assert fence.k == 1.5
    assert fence.method == "inclusive"
    exclusive = k.away_fence(gaps, method="exclusive")
    assert float("%.4g" % exclusive.inner_s) == 436.6
    assert k.count_above(gaps, fence) == 1


def test_t07h_the_fence_k_is_read_from_the_argument():
    gaps = [float(x) for x in BASE_GAPS + [350]]
    wide = k.away_fence(gaps, k=3.0)
    assert wide.k == 3.0
    assert float("%.5g" % wide.inner_s) == 1069.8
    assert wide.outer_s == pytest.approx(k.away_fence(gaps).outer_s)


def test_t07i_leave_one_out_range_brackets_the_base_fence():
    gaps = [float(x) for x in BASE_GAPS + [350]]
    low, high = k.leave_one_out_range(gaps)
    base = k.away_fence(gaps).inner_s
    assert low <= base <= high
    assert low < high


# ---------------------------------------------------------------------------------------
# t08: the idle list
# ---------------------------------------------------------------------------------------


def test_t08a_the_job_free_remainder_is_booked_and_in_the_fit_list():
    body = [
        h.say(50),
        h.agent(100, "a1", "sonnet", "task " + h.lorem(1)),
        h.ack(101, "a1", h.AGENT1),
        h.enqueue(300, "a1", "t1", 200000),
        h.prompt(400),
    ]
    out = classify(closed(body))
    cycle = out.cycles[0]
    assert round(cycle.class_s["OPERATOR_RESPONSE"]) == 100
    assert round(cycle.class_s["SUBAGENT"]) == 200
    assert [round(g) for g in out.idle_gaps_s] == [100]
    assert [round(g) for g in cycle.idle_gaps_s] == [100]


def test_t08b_a_sub_second_remainder_is_neither_booked_nor_listed():
    body = [
        h.say(50),
        h.agent(100, "a1", "sonnet", "task " + h.lorem(1)),
        h.ack(101, "a1", h.AGENT1),
        h.enqueue(300, "a1", "t1", 200000),
        h.prompt(300.5),
    ]
    out = classify(closed(body))
    assert out.idle_gaps_s == []
    assert out.cycles[0].class_s["OPERATOR_RESPONSE"] == 0


def test_t08c_a_pure_gap_and_a_remainder_share_one_list():
    body = [h.say(10), h.prompt(70)] + [
        h.say(100),
        h.agent(120, "a1", "sonnet", "task " + h.lorem(1)),
        h.enqueue(300, "a1", "t1", 180000),
        h.prompt(400),
    ]
    out = classify(closed(body))
    assert sorted(round(g) for g in out.idle_gaps_s) == [60, 100]
    fitted = classify(closed(body), away_min_gaps=2)
    assert fitted.fence.inner_s is not None


# ---------------------------------------------------------------------------------------
# t12: timestamp order through classify
# ---------------------------------------------------------------------------------------


def unique_events():
    body = [
        h.say(10),
        h.agent(20, "a1", "sonnet", "spec " + h.lorem(1)),
        h.ack(21, "a1", h.AGENT1),
        h.say(30),
        h.enqueue(60, "a1", "t1", 40000),
        h.prompt(100),
        h.say(110),
        h.edit(120, "e1"),
        h.result(150, "e1"),
        h.say(160),
    ]
    return closed(body)


def summary(out):
    return (
        [x.class_s for x in out.cycles],
        [x.idle_gaps_s for x in out.cycles],
        out.dispatches,
        out.idle_gaps_s,
        out.fence,
    )


def test_t12ca_shuffled_unique_timestamps_give_identical_classes():
    events = unique_events()
    stamps = [e["at"] for e in events]
    assert len(set(stamps)) == len(stamps)
    plain = k.classify(*analyze(events))
    for seed in (1, 2):
        p = params()
        t = r.parse_bytes(h.build_transcript(events, shuffle_seed=seed))
        shuffled = k.classify(c.analyze_cycles(t, p), t, p)
        assert summary(shuffled) == summary(plain)
    assert plain.cycles[0].class_s["HOOK"] == pytest.approx(30)


def test_t12cb_equal_timestamps_keep_file_order():
    def build(first, second):
        return closed([h.say(50), first, second])

    assistant_first = classify(build(h.say(100), h.prompt(100)))
    prompt_first = classify(build(h.prompt(100), h.say(100)))
    assert assistant_first.idle_gaps_s == []
    assert [round(g) for g in prompt_first.idle_gaps_s] == [50]


# ---------------------------------------------------------------------------------------
# Slice B fixtures
# ---------------------------------------------------------------------------------------


def suite_events(n, art, label, launch, terminal, suite=True):
    """A run-long launch and a background `--wait` whose notification lands at `terminal`."""
    return [
        h.runlong_launch(launch, f"l{n}", art, label, suite=suite),
        h.result(launch + 1, f"l{n}"),
        h.runlong_wait(launch + 2, f"w{n}", art),
        h.result(launch + 3, f"w{n}"),
        h.enqueue(terminal, f"w{n}", f"t{n}", 1000),
    ]


def context_of(events, **over):
    cs, t, p = analyze(events, **over)
    return k.build_context(cs, t, p), cs


def cut_of(events, **over):
    ctx, _ = context_of(events, **over)
    assert len(ctx.windows) == 1
    return k.stage_cut(ctx, ctx.windows[0])


def bounds_s(cut):
    """`{stage: start offset in seconds}` of a cut."""
    return {b.stage: round((b.start_ms - h.BASE_MS) / MS) for b in cut.bounds}


def walls_s(cut):
    return {row.stage: round(row.wall_s) for row in cut.rows}


def staged_cycle(body, feature_tip=True):
    """`/feature`, `body`, an optional feature-tip suite 600 to 1500, dev-tip 1650 to 2400."""
    ev = [h.wrapper(0, "feature")] + list(body)
    if feature_tip:
        ev += suite_events(1, "arta", "feature-tip abc1234", 600, 1500)
    ev += suite_events(2, "artb", "dev-tip def5678", 1650, 2400)
    return ev + [h.skill(2430, "kk", "commit")]


# ---------------------------------------------------------------------------------------
# t40: the five sensitivity sets
# ---------------------------------------------------------------------------------------


def order_pair(gap, second_len=650):
    """Two unlabelled real suites `gap` seconds apart: closed only by the order rule."""
    ev = [h.wrapper(0, "feature")] + suite_events(1, "k1x", "alpha one", 100, 800)
    return ev + suite_events(2, "k2x", "beta two", 800 + gap, 800 + gap + second_len)


def sens(events, **over):
    cs, t, p = analyze(events, **over)
    return {row.name: row for row in k.sensitivity(cs, t, p)}


def test_t40_the_five_sets_carry_the_fixed_spec_constants():
    rows = k.sensitivity(
        *analyze(
            order_pair(600), close_rerun_min=7, close_post_min=3, close_min_suite_min=1
        )
    )
    assert [(x.name, x.rerun_min, x.post_min, x.min_suite_min) for x in rows] == [
        ("primary", 15, 30, 10),
        ("tight", 5, 10, 10),
        ("loose", 60, 120, 10),
        ("min5", 15, 30, 5),
        ("min20", 15, 30, 20),
    ]


def test_t40_the_primary_set_keeps_the_same_windows_under_other_close_flags():
    assert sens(order_pair(600))["primary"].kept == 1
    assert sens(order_pair(600), close_rerun_min=5)["primary"].kept == 1


def test_t40_the_primary_set_moves_against_a_run_with_other_close_flags():
    assert sens(order_pair(600))["primary"].moved == 0
    assert sens(order_pair(600), close_rerun_min=5)["primary"].moved == 1


def test_t40_the_tight_set_drops_an_end_and_keeps_a_smaller_population():
    rows = sens(order_pair(600))
    assert (rows["primary"].kept, rows["tight"].kept, rows["loose"].kept) == (1, 0, 1)
    assert sum(rows["tight"].classes_s.values()) == 0


def test_t40_the_tight_set_counts_its_dropped_end_as_moved():
    rows = sens(order_pair(600))
    assert (rows["tight"].moved, rows["loose"].moved) == (1, 0)


def test_t40_the_loose_set_keeps_an_end_the_primary_set_drops():
    rows = sens(order_pair(2400))
    assert (rows["primary"].kept, rows["loose"].kept) == (0, 1)


def test_t40_the_loose_set_counts_its_new_end_as_moved():
    assert sens(order_pair(2400))["loose"].moved == 1


def dev_only(length):
    ev = [h.wrapper(0, "feature")]
    return ev + suite_events(1, "k1x", "dev-tip abc1234", 100, 100 + length)


def test_t40_the_suite_length_sets_move_the_minimum_real_suite():
    rows = sens(dev_only(400))
    assert (rows["primary"].kept, rows["min5"].kept, rows["min20"].kept) == (0, 1, 0)
    long_rows = sens(dev_only(900))
    assert (long_rows["primary"].kept, long_rows["min20"].kept) == (1, 0)


def test_t40_a_set_pools_the_class_seconds_of_its_kept_windows():
    p = params()
    t = r.parse_bytes(h.k1_transcript("skill"))
    cs = c.analyze_cycles(t, p)
    row = {x.name: x for x in k.sensitivity(cs, t, p)}["primary"]
    cycle = k.classify(cs, t, p).cycles[0]
    for name in CLASSES:
        assert row.classes_s[name] == pytest.approx(cycle.class_s[name])
    assert round(sum(row.classes_s.values())) == 2430


# ---------------------------------------------------------------------------------------
# t41: the stage cut
# ---------------------------------------------------------------------------------------


def test_t41_k1_stage_walls_partition_the_cycle():
    p = params()
    t = r.parse_bytes(h.k1_transcript("skill"))
    cs = c.analyze_cycles(t, p)
    ctx = k.build_context(cs, t, p)
    cut = k.stage_cut(ctx, ctx.windows[0])
    assert [row.stage for row in cut.rows] == list(m.STAGES)
    assert walls_s(cut) == {
        **{name: 0 for name in m.STAGES},
        "triage/spec-author": 600,
        "feature-tip-suite": 1050,
        "dev-tip-suite": 750,
        "closing": 30,
    }
    assert round(sum(row.wall_s for row in cut.rows)) == 2430


def test_t41_each_stage_holds_its_wall_in_classes_and_sums_to_the_cycle():
    cycle = k1("skill").cycles[0]
    assert [row.stage for row in cycle.stages] == list(m.STAGES)
    for row in cycle.stages:
        assert sum(row.classes_s.values()) == pytest.approx(row.wall_s)
        assert set(row.classes_s) == set(CLASSES)
    for name in CLASSES:
        pooled = sum(row.classes_s[name] for row in cycle.stages)
        assert pooled == pytest.approx(cycle.class_s[name])


KW_TABLE = [
    ("sonnet", "spec", "triage/spec-author"),
    ("sonnet", "author+spec", "triage/spec-author"),
    ("opus", "review+spec", "spec-review/ruling"),
    ("opus", "probe+spec", "spec-review/ruling"),
    ("sonnet", "author+plan", "plan-author"),
    ("opus", "plan+review", "plan-review"),
    ("opus", "plan+spec", "plan-review"),
    ("sonnet", "fold+plan", "plan-fold"),
    ("sonnet", "fold+spec", "spec-fold"),
    ("sonnet", "implement+task", "execute"),
    ("opus", "review", "execute"),
    ("opus", "audit+vet", "gates"),
    ("skill-reviewer", None, "gates"),
    ("opus", None, "other"),
    ("opus", "publish", "other"),
]


@pytest.mark.parametrize(("role", "kw", "want"), KW_TABLE)
def test_t41_keywords_map_to_the_closed_stage_list(role, kw, want):
    got = k.dispatch_stage(role, kw)
    assert got == want
    assert got in m.STAGE_BUCKETS


def test_t41_a_bare_fold_follows_the_stages_already_seen():
    assert k.dispatch_stage("sonnet", "fold", ()) == "spec-fold"
    assert k.dispatch_stage("sonnet", "fold", ("plan-author",)) == "plan-fold"
    assert k.dispatch_stage("sonnet", "fold", ("plan-review",)) == "plan-fold"
    assert k.dispatch_stage("sonnet", "fold", ("execute",)) == "spec-fold"


def test_t41_an_unmapped_dispatch_is_the_other_bucket_and_cuts_nothing():
    plain = cut_of(staged_cycle([h.say(10)]))
    odd = cut_of(staged_cycle([h.say(10), h.agent(50, "u1", "sonnet", "lorem ipsum")]))
    assert k.dispatch_stage("sonnet", None) == "other"
    assert "other" in m.STAGE_BUCKETS
    assert "other" not in m.STAGES
    assert bounds_s(odd) == bounds_s(plain)


def marker_body():
    body = [h.say(10)]
    for at, ident, model, desc in (
        (100, "s2", "opus", "review spec"),
        (150, "s3", "opus", "review spec again"),
        (200, "p1", "sonnet", "plan author"),
        (300, "x1", "sonnet", "implement task"),
        (350, "x2", "sonnet", "implement task again"),
    ):
        body.append(h.agent(at, ident, model, desc))
    return body


def test_t41_a_stage_starts_at_its_first_dispatch_and_empty_stages_take_the_next():
    cut = cut_of(staged_cycle(marker_body()))
    assert bounds_s(cut) == {
        "triage/spec-author": 0,
        "spec-review/ruling": 100,
        "spec-fold": 200,
        "plan-author": 200,
        "plan-review": 300,
        "plan-fold": 300,
        "execute": 300,
        "gates": 600,
        "commit": 600,
        "feature-tip-suite": 600,
        "bricks": 1650,
        "dev-tip-suite": 1650,
        "closing": 2400,
    }
    assert walls_s(cut)["plan-author"] == 100
    assert walls_s(cut)["execute"] == 300
    assert [b.note for b in cut.bounds].count("no-marker") == 6


def test_t41_without_a_feature_tip_suite_the_dispatch_stages_run_to_the_dev_tip():
    cut = cut_of(staged_cycle(marker_body(), feature_tip=False))
    assert walls_s(cut)["feature-tip-suite"] == 0
    assert walls_s(cut)["bricks"] == 0
    assert walls_s(cut)["execute"] == 1650 - 300
    assert walls_s(cut)["dev-tip-suite"] == 750
    assert round(sum(row.wall_s for row in cut.rows)) == 2430


def test_t41_a_brick_sweep_between_the_two_suites_starts_the_bricks_stage():
    body = suite_events(5, "artk", "brick sweep", 1560, 1640, suite=False)
    cut = cut_of(staged_cycle([h.say(10)] + body))
    assert bounds_s(cut)["bricks"] == 1560
    assert walls_s(cut)["feature-tip-suite"] == 960
    assert walls_s(cut)["bricks"] == 90
    early = suite_events(5, "artk", "brick sweep", 100, 200, suite=False)
    assert walls_s(cut_of(staged_cycle([h.say(10)] + early)))["bricks"] == 0


def test_t41_gates_start_at_the_first_gate_marker_after_execution_begins():
    body = (
        suite_events(5, "arte", "plain gate", 250, 260, suite=False)
        + [h.agent(300, "x1", "sonnet", "implement task")]
        + suite_events(6, "artf", "plain gate", 400, 420, suite=False)
    )
    cut = cut_of(staged_cycle(body))
    assert bounds_s(cut)["gates"] == 400
    assert walls_s(cut)["execute"] == 100


def test_t41_the_commit_stage_starts_at_the_last_commit_before_the_tip_suite():
    body = [h.wrapper(450, "commit"), h.skill(500, "kc", "commit")]
    cut = cut_of(staged_cycle(body))
    assert bounds_s(cut)["commit"] == 500
    assert walls_s(cut)["commit"] == 100


def test_t41_repeated_same_sha_tip_suites_start_the_stage_at_the_earliest():
    def repeated(sha2):
        ev = [h.wrapper(0, "feature")]
        ev += suite_events(1, "arta", "feature-tip abc1234", 100, 800)
        ev += suite_events(3, "artc", f"feature-tip {sha2}", 850, 1500)
        ev += suite_events(2, "artb", "dev-tip def5678", 1650, 2400)
        return ev + [h.skill(2430, "kk", "commit")]

    assert bounds_s(cut_of(repeated("abc1234")))["feature-tip-suite"] == 100
    assert bounds_s(cut_of(repeated("fed9876")))["feature-tip-suite"] == 850


def test_t41_a_resumed_reviewer_is_not_a_gate_dispatch():
    body = [
        h.agent(100, "r1", "sonnet", "lorem ipsum", subagent_type="skill-reviewer"),
        h.ack(101, "r1", h.AGENT1),
        h.enqueue(200, "r1", "tr", 99000),
        h.agent(300, "x1", "sonnet", "implement task"),
        h.sendmsg(400, "m1", h.AGENT1, "lorem ipsum"),
        h.enqueue(450, "m1", "tr", 50000),
    ]
    cut = cut_of(staged_cycle(body))
    assert bounds_s(cut)["gates"] <= 300


def test_t41_a_gate_dispatch_before_execution_is_clamped_up_to_it():
    body = [
        h.agent(100, "r1", "sonnet", "lorem ipsum", subagent_type="skill-reviewer"),
        h.agent(300, "x1", "sonnet", "implement task"),
    ]
    cut = cut_of(staged_cycle(body))
    assert bounds_s(cut)["gates"] == 300
    assert {b.stage: b.note for b in cut.bounds}["gates"] == "clamped-later"
    assert walls_s(cut)["execute"] == 0


# ---------------------------------------------------------------------------------------
# t46: the long-call arm, the check-class split and turn_duration
# ---------------------------------------------------------------------------------------


def long_call_body():
    return [
        h.bash(10, "b1", "pytest tests/x"),
        h.result(110, "b1"),
        h.bash(120, "b2", "sleep 70"),
        h.result(200, "b2"),
        h.bash(210, "b3", "sleep 30"),
        h.result(240, "b3"),
        h.bash(260, "b5", "pytest tests/y", bg=True),
        h.result(600, "b5"),
    ] + suite_events(7, "artz", "plain gate", 620, 700, suite=False)


def test_t46a_the_long_call_arm_splits_by_the_command_token_class():
    ctx, _ = context_of(closed(long_call_body()))
    arm = k.long_call_arm(ctx, ctx.windows[0])
    assert arm.calls == 2
    assert arm.total_s == pytest.approx(180)
    assert arm.check_s == pytest.approx(100)
    assert arm.other_s == pytest.approx(80)
    assert arm.check_s + arm.other_s == pytest.approx(arm.total_s)


def test_t46a_the_arm_threshold_is_the_long_call_parameter():
    ctx, _ = context_of(closed(long_call_body()), long_call_s=90)
    arm = k.long_call_arm(ctx, ctx.windows[0])
    assert (arm.calls, round(arm.check_s), round(arm.other_s)) == (1, 100, 0)


def test_t46a_a_call_of_exactly_the_threshold_is_not_long():
    def arm(seconds):
        body = [h.bash(10, "b1", "sleep 5"), h.result(10 + seconds, "b1")]
        ctx, _ = context_of(closed(body))
        return k.long_call_arm(ctx, ctx.windows[0]).calls

    assert (arm(60.000), arm(60.001)) == (0, 1)


def test_t46b_the_other_check_class_is_never_a_stage_bucket():
    cut = cut_of(closed(long_call_body()))
    buckets = k.stage_bucket_s(cut)
    assert list(buckets) == list(m.STAGE_BUCKETS)
    assert buckets["other"] == 0
    assert sum(buckets.values()) == pytest.approx(sum(row.wall_s for row in cut.rows))


def test_t46b_machine_wait_by_check_covers_every_check_class():
    cut = cut_of(staged_cycle([h.say(10)]))
    by_check = k.machine_wait_by_check(cut)
    assert list(by_check) == list(m.CHECK_CLASSES)
    assert round(by_check["suite-feature-tip"]) == 900
    assert round(by_check["suite-dev-tip"]) == 750
    assert by_check["other"] == 0
    long_cut = cut_of(closed(long_call_body()))
    assert k.machine_wait_by_check(long_cut)["other"] == pytest.approx(180)
    assert "other" in m.STAGE_BUCKETS and "other" in m.CHECK_CLASSES
    assert k.STAGE_BUCKETS is not k.CHECK_CLASSES


def test_t46c_turn_duration_is_summed_per_window_with_no_criterion():
    events = closed([h.say(5), h.turn(50, 1000), h.turn(60, 2500)])
    events += [h.turn(-50, 7000), h.turn(10000, 5000)]
    ctx, _ = context_of(events)
    assert k.turn_duration_s(ctx, ctx.windows[0]) == pytest.approx(3.5)
    p = params()
    t = r.parse_bytes(h.build_transcript(events))
    out = k.classify(c.analyze_cycles(t, p), t, p)
    assert set(out.cycles[0].class_s) == set(CLASSES)


# ---------------------------------------------------------------------------------------
# t44: the small cross-check inputs
# ---------------------------------------------------------------------------------------


def test_t44a_order_indicators_report_agent_seconds_and_the_union_hidden_under_wait():
    body = suite_events(1, "arta", "feature-tip abc1234", 100, 1000)
    for n, (a, b) in enumerate([(200, 500), (300, 600)]):
        body += [
            h.agent(a, f"a{n}", "sonnet", "task " + h.lorem(n)),
            h.ack(a + 1, f"a{n}", f"aid{n:014d}"),
            h.enqueue(b, f"a{n}", f"u{n}", (b - a) * MS),
        ]
    events = [h.wrapper(0, "feature")] + body
    events += suite_events(2, "artb", "dev-tip def5678", 1100, 1800)
    events.append(h.skill(1830, "kk", "commit"))
    ctx, _ = context_of(events)
    ind = k.order_indicators(ctx, ctx.windows[0])
    assert ind.hidden_union_s == pytest.approx(400)
    assert ind.hidden_agent_s == pytest.approx(600)


def test_t44b_order_indicators_count_peer_suite_stall_recorded_and_any():
    events = h.k1_events("skill")
    events += suite_events(8, "pa", "dev-tip abc1234", -500, 300)
    events += suite_events(9, "pb", "dev-tip abc1234", -400, 200)
    events.append(h.runlong_wait(100, "x1", "pa", mode="status", bg=False))
    ctx, _ = context_of(events)
    ind = k.order_indicators(ctx, ctx.windows[0])
    assert ind.stalled_any_s == pytest.approx(500)
    assert ind.stalled_recorded_s == pytest.approx(300)


def test_t44c_fence_robust_brackets_the_base_fence_and_names_the_smallest_above_gap():
    gaps = BASE_GAPS + [300]
    ctx, _ = context_of(closed(gap_body(gaps)))
    got = k.fence_robust(ctx)
    base = hand_fence(gaps)[2]
    loo = [hand_fence(gaps[:i] + gaps[i + 1 :])[2] for i in range(len(gaps))]
    away = [sum(g for g in gaps if g > t) for t in loo]
    assert got.n == 10
    assert got.base_s == pytest.approx(base)
    assert (got.min_s, got.max_s) == (pytest.approx(min(loo)), pytest.approx(max(loo)))
    assert got.above_base == 1
    assert got.above_min == sum(1 for g in gaps if g > min(loo)) == 1
    assert got.above_max == sum(1 for g in gaps if g > max(loo)) == 0
    assert got.smallest_above_s == pytest.approx(300)
    assert got.smallest_above_pct == pytest.approx(100 * (300 - base) / base)
    assert got.away_base_s == pytest.approx(300)
    assert (got.away_min_s, got.away_max_s) == (pytest.approx(300), pytest.approx(0))
    assert got.omissions == 10
    assert got.flips == sum(1 for a in away if abs(a - 300) > 1) > 0


def test_t44c_fence_robust_base_away_uses_the_base_fence_not_a_neighbour():
    gaps = BASE_GAPS + [250]
    ctx, _ = context_of(closed(gap_body(gaps)))
    got = k.fence_robust(ctx)
    assert hand_fence(gaps)[2] > 250 > got.min_s
    assert got.above_base == 0
    assert got.smallest_above_s is None
    assert got.away_base_s == pytest.approx(0)
    assert got.away_min_s == pytest.approx(250)


def test_t44c_fence_robust_is_empty_below_the_gap_floor():
    ctx, _ = context_of(closed(gap_body([10, 20, 30])))
    got = k.fence_robust(ctx)
    assert (got.n, got.base_s, got.min_s, got.flips) == (3, None, None, 0)


def test_t44d_suite_machine_wait_reports_per_run_seconds_with_a_band_flag():
    cut = cut_of(staged_cycle([h.say(10)]))
    got = k.suite_machine_wait(cut)
    assert round(got.feature_s) == 900
    assert got.feature_run_s == (pytest.approx(900),)
    assert round(got.dev_s) == 750
    assert got.dev_run_s == (pytest.approx(750),)
    assert got.feature_in_band is True and got.dev_in_band is True
    long_run = [h.wrapper(0, "feature")]
    long_run += suite_events(1, "arta", "feature-tip abc1234", 100, 4100)
    long_run += suite_events(2, "artb", "dev-tip def5678", 4200, 4900)
    long_run.append(h.skill(4930, "kk", "commit"))
    slow = k.suite_machine_wait(cut_of(long_run))
    assert round(slow.feature_s) == 4000
    assert slow.feature_in_band is False
    assert slow.dev_in_band is True


def test_t44d_a_stage_without_a_suite_run_has_no_band_flag():
    got = k.suite_machine_wait(cut_of(staged_cycle([h.say(10)], feature_tip=False)))
    assert got.feature_run_s == ()
    assert got.feature_s == 0
    assert got.feature_in_band is None
    assert got.dev_in_band is True


def dev_chain(first_s, second_s):
    ev = [h.wrapper(0, "feature")]
    ev += suite_events(1, "arta", "dev-tip abc1234", 100, 100 + first_s)
    start = 100 + first_s + 80
    ev += suite_events(2, "artb", "dev-tip def5678", start, start + second_s)
    return ev + [h.skill(start + second_s + 30, "kk", "commit")]


def dev_chain_band(first_s, second_s):
    cut = cut_of(dev_chain(first_s, second_s), close_min_suite_min=1)
    got = k.suite_machine_wait(cut)
    assert got.dev_run_s == (pytest.approx(first_s), pytest.approx(second_s))
    return got.dev_in_band


def test_t44d_a_2_minute_and_a_70_minute_run_both_flag_though_the_mean_is_in_band():
    assert dev_chain_band(120, 4200) is False


def test_t44d_one_out_of_band_run_flags_the_stage():
    assert dev_chain_band(120, 1200) is False


def test_t44d_runs_inside_the_band_do_not_flag():
    assert dev_chain_band(1200, 1800) is True


def test_t44e_a_dev_tip_rerun_chain_lists_each_run():
    ev = [h.wrapper(0, "feature")]
    ev += suite_events(1, "arta", "dev-tip abc1234", 100, 2500)
    ev += suite_events(2, "artb", "dev-tip def5678", 2600, 5000)
    ev.append(h.skill(5030, "kk", "commit"))
    got = k.suite_machine_wait(cut_of(ev))
    assert got.dev_run_s == (pytest.approx(2400), pytest.approx(2400))
    assert round(got.dev_s) == 4800
    assert got.dev_in_band is True
    assert got.feature_run_s == ()


def overlapping_tip_runs(second_launch):
    ev = [h.wrapper(0, "feature")]
    ev += suite_events(1, "arta", "feature-tip abc1234", 100, 800)
    ev += suite_events(
        3, "artc", "feature-tip abc1234", second_launch, second_launch + 700
    )
    ev += suite_events(2, "artb", "dev-tip def5678", 1700, 2400)
    return ev + [h.skill(2430, "kk", "commit")]


def test_t44e_concurrent_same_sha_tip_suites_are_one_run():
    got = k.suite_machine_wait(cut_of(overlapping_tip_runs(200)))
    assert len(got.feature_run_s) == 1


def test_t44e_sequential_same_sha_tip_suites_are_two_runs():
    got = k.suite_machine_wait(cut_of(overlapping_tip_runs(850)))
    assert len(got.feature_run_s) == 2


def ratio_events(spec):
    """Dispatches `(start, enqueue, duration_ms)` with delivery records."""
    out = []
    for n, (a, b, dur) in enumerate(spec):
        out += [
            h.agent(a, f"a{n}", "sonnet", "task " + h.lorem(n)),
            h.ack(a + 1, f"a{n}", f"aid{n:014d}"),
            h.enqueue(b, f"a{n}", f"u{n}", dur),
            h.delivery(b + 1, f"a{n}", f"u{n}", dur),
        ]
    return closed(out)


def test_t44f_subcheck_gives_the_ratio_median_and_deciles():
    ctx, _ = context_of(
        ratio_events([(100, 400, 300000), (500, 700, 400000), (800, 1000, 100000)])
    )
    got = k.subcheck(ctx)
    assert got.n == 3
    assert got.median == pytest.approx(1.0)
    assert (got.p10, got.p90) == (pytest.approx(0.5), pytest.approx(1.0))
    assert got.in_band is True


def test_t44g_subcheck_flags_a_ratio_outside_the_band_and_never_fails():
    ctx, _ = context_of(ratio_events([(100, 110, 300000), (500, 520, 400000)]))
    got = k.subcheck(ctx)
    assert got.median < 0.5
    assert got.in_band is False


def test_t44g_subcheck_is_empty_without_durations():
    ctx, _ = context_of(closed([h.say(10)]))
    got = k.subcheck(ctx)
    assert (got.n, got.median, got.in_band) == (0, None, None)


def test_t44g_subcheck_counts_a_delivery_ended_dispatch_apart_from_the_ratio():
    events = ratio_events([(100, 400, 300000)])
    events += [
        h.agent(1000, "d0", "sonnet", "task " + h.lorem(5)),
        h.delivery(1010, "d0", "ud", 5000000),
    ]
    ctx, _ = context_of(events)
    got = k.subcheck(ctx)
    assert (got.n, got.not_enqueue) == (1, 1)
    assert got.median == pytest.approx(1.0)


# ---------------------------------------------------------------------------------------
# Fix round 1: idle-gap end marks, window membership, strict fence, no-separation, peers
# ---------------------------------------------------------------------------------------


def idle_list(body, **over):
    return [round(g, 3) for g in classify(closed(body), **over).idle_gaps_s]


def test_t08d_an_auto_continuation_ends_a_gap():
    body = [h.say(10), h.prompt(70, origin="auto_continuation")]
    assert idle_list(body) == [60.0]


def test_t08e_an_is_meta_auto_continuation_ends_a_gap():
    body = [h.say(10), h.prompt(70, origin="auto_continuation", meta=True)]
    assert idle_list(body) == [60.0]


def test_t08f_an_ask_user_question_answer_ends_a_gap():
    body = [
        h.say(10),
        h._tool_use(20, "q1", "AskUserQuestion", {"question": "lorem"}),
        h.result(80, "q1"),
    ]
    assert idle_list(body) == [60.0]


def test_t08g_an_interrupt_is_skipped_and_the_gap_runs_on():
    body = [h.say(10), h.prompt(40, "[Request interrupted by user]"), h.prompt(100)]
    assert idle_list(body) == [90.0]


def test_t08h_adjacent_prompts_end_one_gap_each_turn():
    body = [h.say(10), h.prompt(40), h.prompt(41), h.say(50), h.prompt(80)]
    assert idle_list(body) == [30.0, 30.0]


def test_t08i_a_gap_straddling_the_window_end_scales_its_free_seconds():
    events = closed([])[:-1]
    end = events[-1]["at"]
    events += [h.say(end - 20), h.prompt(end + 30)]
    out = classify(events)
    assert [round(g, 3) for g in out.idle_gaps_s] == [30.0]
    assert [round(g, 3) for g in out.cycles[0].idle_gaps_s] == [12.0]


def test_t04f_an_edit_wait_inside_an_idle_gap_is_hook_and_the_gap_stays_listed():
    body = [h.say(10), h.edit(20, "e0"), h.result(80, "e0"), h.prompt(100)]
    out = classify(closed(body))
    assert round(out.cycles[0].class_s["HOOK"]) == 60
    assert round(out.cycles[0].class_s["OPERATOR_RESPONSE"]) == 20
    assert [round(g) for g in out.idle_gaps_s] == [80]


def test_t03e_a_span_starting_at_the_window_start_joins_it():
    spans = [(99, 150, "a"), (100, 150, "b")]
    assert k._started_in(spans, 100, 200) == [(100, 150, "b")]


def test_t03h_a_span_starting_at_the_window_end_does_not_join_it():
    spans = [(199, 250, "c"), (200, 250, "d")]
    assert k._started_in(spans, 100, 200) == [(199, 250, "c")]


def test_t03f_a_dispatch_begun_before_the_window_contributes_nothing():
    body = [h.agent(-50, "a1", "sonnet", "task " + h.lorem(1)), h.say(5)]
    events = closed(body) + [h.enqueue(200, "a1", "t1", 250000)]
    out = classify(events)
    assert out.cycles[0].class_s["SUBAGENT"] == 0
    assert out.cycles[0].agent_seconds == 0
    assert len(out.dispatches) == 1


def injected_fence(inner):
    return m.Fence(1.0, 2.0, inner, inner * 3, 1.5, "inclusive")


def test_t07j_a_gap_equal_to_the_inner_fence_is_not_away():
    entry = k.IdleEntry(0, 100000, 100.0, "pure")
    assert k.book_idle([entry], injected_fence(100.0))[0][2] == "OPERATOR_RESPONSE"
    assert k.book_idle([entry], injected_fence(99.999))[0][2] == "AWAY"


def test_t07k_count_above_is_strictly_greater_than_the_inner_fence():
    assert k.count_above([100.0, 100.001], injected_fence(100.0)) == 1


def test_t07l_no_separation_books_all_idle_as_operator_response():
    events = closed(gap_body(BASE_GAPS + [350]))
    cs, t, p = analyze(events)
    primary = k.classify(cs, t, p).cycles[0].class_s
    unsplit = k.no_separation_class_s(cs, t, p)[0]
    assert round(primary["AWAY"]) == 350
    assert unsplit["AWAY"] == 0
    want = primary["OPERATOR_RESPONSE"] + primary["AWAY"]
    assert unsplit["OPERATOR_RESPONSE"] == pytest.approx(want)


def test_t07m_no_separation_leaves_the_other_classes_and_the_wall_alone():
    events = closed(gap_body(BASE_GAPS + [350]) + edits([(2000, 2030)]))
    cs, t, p = analyze(events)
    primary = k.classify(cs, t, p).cycles[0].class_s
    unsplit = k.no_separation_class_s(cs, t, p)[0]
    for name in ("CONTROLLER", "SUBAGENT", "MACHINE_WAIT", "HOOK"):
        assert unsplit[name] == pytest.approx(primary[name])
    win = cs.windows[0]
    assert sum(unsplit.values()) == pytest.approx((win.end_t_ms - win.start_t_ms) / MS)


def test_t03g_concurrent_peers_each_count_in_the_peer_figure():
    def suite(n, art, launch, terminal):
        return [
            h.runlong_launch(launch, f"l{n}", art, "dev-tip abc1234"),
            h.result(launch + 1, f"l{n}"),
            h.runlong_wait(launch + 2, f"w{n}", art),
            h.result(launch + 3, f"w{n}"),
            h.enqueue(terminal, f"w{n}", f"t{n}", 1000),
        ]

    events = suite(1, "pa", 500, 1500) + suite(2, "pb", 600, 1400) + [h.say(1900)]
    t = r.parse_bytes(h.build_transcript(events))
    pairs = c.analyze_cycles(t, params()).pairs
    seen, _ = k.peer_overlap_s(k.main_events(t), pairs, ms(1000), ms(2000))
    assert round(seen) == 900


# ---------------------------------------------------------------------------------------
# Fix round 2: stage-cut property, HOOK threshold sensitivity, snapshot-wide HOOK union
# ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("first", [0, 25, 50, 75])
def test_t41_stage_cut_invariants_hold_over_seeded_random_cycles(first):
    for seed in range(first, first + 25):
        p = params()
        t = r.parse_bytes(h.random_cycle(seed).data)
        ctx = k.build_context(c.analyze_cycles(t, p), t, p)
        assert ctx.windows, seed
        for win in ctx.windows:
            cut = k.stage_cut(ctx, win)
            wall = (win.end_t_ms - win.start_t_ms) / MS
            starts = [b.start_ms for b in cut.bounds]
            assert starts == sorted(starts), seed
            assert starts[0] == win.start_t_ms
            assert sum(row.wall_s for row in cut.rows) == pytest.approx(wall), seed
            cls = k.window_cycle(ctx, win).class_s
            for index, row in enumerate(cut.rows):
                assert sum(row.classes_s.values()) == pytest.approx(row.wall_s), seed
                for name, labels in cut.parts[index].items():
                    assert sum(labels.values()) == pytest.approx(row.classes_s[name])
            for name in ("SUBAGENT", "MACHINE_WAIT", "HOOK"):
                pooled = sum(sum(part.get(name, {}).values()) for part in cut.parts)
                assert pooled == pytest.approx(cls[name]), (seed, name)


def hook_sens(variant="skill", **over):
    p = params(**over)
    t = r.parse_bytes(h.k1_transcript(variant))
    return k.hook_sensitivity(c.analyze_cycles(t, p), t, p)


def test_t45_k1_at_5_seconds_gives_the_hand_table_hook_and_controller():
    low = hook_sens()[0]
    assert (low.threshold_s, low.primary) == (5, False)
    assert round(low.hook_s) == 49
    assert round(low.controller_s) == 371


def test_t45_the_primary_entry_is_the_runs_own_threshold():
    rows = hook_sens()
    assert [(x.threshold_s, x.primary) for x in rows] == [
        (5, False),
        (10, True),
        (30, False),
    ]
    assert (round(rows[1].hook_s), round(rows[1].controller_s)) == (30, 390)
    custom = hook_sens(hook_latency_s=20)[1]
    assert (custom.threshold_s, custom.primary) == (20, True)
    assert round(custom.hook_s) == 30


def test_t45_the_fixed_5_and_30_second_entries_ignore_the_run_threshold():
    default = hook_sens()
    moved = hook_sens(hook_latency_s=20)
    for index in (0, 2):
        assert moved[index].threshold_s == default[index].threshold_s
        assert moved[index].hook_s == pytest.approx(default[index].hook_s)
        assert moved[index].controller_s == pytest.approx(default[index].controller_s)
    assert round(default[2].hook_s) == 0


def edit_transcript(spans, extra=()):
    events = edits(spans) + list(extra)
    return r.parse_bytes(h.build_transcript(events))


def test_t47_hook_union_total_counts_overlapping_calls_once():
    t = edit_transcript([(100, 120), (110, 140), (130, 150)])
    calls, union_s = k.hook_union_total(t, 10000)
    assert calls == 3
    assert union_s == pytest.approx(50)


def test_t47_hook_union_total_is_strictly_greater_than_the_threshold():
    t = edit_transcript([(100, 110), (200, 210.001)])
    calls, union_s = k.hook_union_total(t, 10000)
    assert calls == 1
    assert union_s == pytest.approx(10.001)


def test_t47_hook_union_total_ignores_sidechain_records():
    side = [h.edit(300, "sx", sidechain=True), h.result(340, "sx", sidechain=True)]
    t = edit_transcript([(100, 130)], side)
    assert k.hook_union_total(t, 10000) == (1, pytest.approx(30))


def test_t47_hook_union_total_reads_the_threshold_argument():
    t = edit_transcript([(100, 108), (200, 230)])
    assert k.hook_union_total(t, 10000)[0] == 1
    assert k.hook_union_total(t, 5000)[0] == 2

"""Tests for the cycle-profile cycles module: roster, closing rule, pairing, artifacts.

Every transcript is synthetic (fictional base date 2030-01-01, invented ids, lorem text),
built from `cycle_profile_helpers`. Suites are described as `(artifact, label, launch,
terminal)` in seconds from the base; each becomes a run-long launch plus a background
`--wait` whose notification arrives at the terminal, so a pair's terminal is exact to the
millisecond and the three boundary comparisons of the closing rule can be pinned at
equality.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import cycle_profile_cycles as c  # noqa: E402, I001
import cycle_profile_helpers as h  # noqa: E402
import cycle_profile_model as m  # noqa: E402
import cycle_profile_records as r  # noqa: E402

MS = 1000


def ms(seconds):
    """Offset in seconds from the fictional base, as integer epoch milliseconds."""
    return h.BASE_MS + round(seconds * MS)


def feature(at):
    return h.wrapper(at, "feature")


def suite(n, art, label, launch, terminal, bg=True):
    """A suite-class run-long launch and a `--wait` whose notification lands at `terminal`."""
    return [
        h.runlong_launch(launch, f"l{n}", art, label),
        h.result(launch + 1, f"l{n}"),
        h.runlong_wait(launch + 2, f"w{n}", art, bg=bg),
        h.result(launch + 3, f"w{n}"),
        h.enqueue(terminal, f"w{n}", f"t{n}", 1000),
    ]


def analyze(events, **params):
    transcript = r.parse_bytes(h.build_transcript(events))
    return c.analyze_cycles(transcript, m.Params(**params))


def only(cycle_set):
    assert len(cycle_set.windows) == 1
    return cycle_set.windows[0]


def dev_closed(gap_marker=None, name="commit", form="skill", dev_end=1000):
    """One invocation, one dev-tip suite 100 to `dev_end`, an optional closing marker."""
    events = [feature(0)] + suite(1, "ka", "dev-tip aaa1111", 100, dev_end)
    if gap_marker is not None:
        if form == "skill":
            events.append(h.skill(dev_end + gap_marker, "mk", name))
        else:
            events.append(h.wrapper(dev_end + gap_marker, name))
    return events


# ---------------------------------------------------------------------------------------------
# t09: the closing rule at exact equality
# ---------------------------------------------------------------------------------------------


def _rerun(gap_s, **params):
    events = [feature(0)] + suite(1, "ka", "dev-tip aaa1111", 100, 1000)
    events += suite(2, "kb", "dev-tip bbb2222", 1000 + gap_s, 1000 + gap_s + 700)
    return only(analyze(events, **params))


def test_t09a_rerun_boundary_900s():
    on = _rerun(900.000)
    assert on.status == "completed"
    assert on.end_t_ms == ms(2600)
    assert len(on.chain) == 1
    off = _rerun(900.001)
    assert off.end_t_ms == ms(1000)
    assert off.chain == ()
    assert off.end_marker == "verdict"
    assert on.end_marker == "verdict-rerun"


def test_t09b_post_marker_upper_edge_is_inclusive_1800s():
    on = only(analyze(dev_closed(1800.000)))
    assert on.end_t_ms == ms(2800)
    assert on.end_marker == "commit"
    off = only(analyze(dev_closed(1800.001)))
    assert off.end_t_ms == ms(1000)
    assert off.end_marker == "verdict"


def test_t09b_post_marker_lower_edge_is_exclusive():
    # the start of the post window is exclusive: a marker AT the verdict does not count
    at = only(analyze(dev_closed(0.000)))
    assert at.end_t_ms == ms(1000)
    assert at.end_marker == "verdict"
    after = only(analyze(dev_closed(0.001)))
    assert after.end_t_ms == ms(1000.001)
    assert after.end_marker == "commit"


def test_t09c_min_suite_boundary_600s():
    on = only(analyze([feature(0)] + suite(1, "ka", "dev-tip aaa1111", 100, 700.000)))
    assert on.status == "completed"
    assert on.end_t_ms == ms(700)
    off = only(analyze([feature(0)] + suite(1, "ka", "dev-tip aaa1111", 100, 699.999)))
    assert off.status == "open"
    assert off.end_t_ms is None
    assert off.first_missing_link == "none-real"


def test_t09d_search_bound_is_half_open():
    events = [feature(0), feature(5000)]
    events += suite(1, "ka", "dev-tip aaa1111", 5000.000, 5700)
    cs = analyze(events)
    first, second = cs.windows
    assert first.search_bound_ms == ms(5000)
    assert first.status == "partial"
    assert first.end_t_ms is None
    assert first.first_missing_link == "no-suite-launch"
    assert second.status == "completed"
    assert second.end_t_ms == ms(5700)
    # a suite launched one millisecond before the next invocation still belongs to the first
    events = [feature(0), feature(5000)]
    events += suite(1, "ka", "dev-tip aaa1111", 4999.999, 5700)
    first, second = analyze(events).windows
    assert first.status == "completed"
    assert second.status == "open"
    assert second.first_missing_link == "no-suite-launch"


def test_t09e_order_fallback_closes_without_a_dev_label():
    def build(gap):
        events = [feature(0)] + suite(1, "k1x", "alpha one", 100, 800)
        return events + suite(2, "k2x", "beta two", 800 + gap, 800 + gap + 650)

    on = only(analyze(build(900.000)))
    assert on.status == "completed"
    assert on.basis == "order"
    assert on.f1.launch_ms == ms(100)
    assert on.d1.launch_ms == ms(1700)
    assert on.end_t_ms == ms(2350)
    for gap in (900.001, -10):
        off = only(analyze(build(gap)))
        assert off.end_t_ms is None
        assert off.first_missing_link == "no-dev-or-order-pair"


def test_t09f_feature_tip_suite_is_optional():
    def build(with_f1, with_dispatch):
        events = [feature(0)]
        if with_dispatch:
            events += [
                h.agent(10, "a1", "sonnet", "spec lorem"),
                h.ack(11, "a1", h.AGENT1),
                h.enqueue(90, "a1", "t9", 80000),
            ]
        if with_f1:
            events += suite(1, "k1x", "feature-tip aaa1111", 100, 800)
        return events + suite(2, "k2x", "dev-tip bbb2222", 900, 1600)

    full = only(analyze(build(True, True)))
    assert (full.status, full.reconstructable, full.basis) == (
        "completed",
        "full",
        "label",
    )
    assert full.f1.launch_ms == ms(100)
    no_dispatch = only(analyze(build(True, False)))
    assert no_dispatch.status == "completed"
    assert no_dispatch.reconstructable == "partial"
    optional = only(analyze(build(False, True)))
    assert optional.status == "completed"
    assert optional.f1 is None
    assert optional.d1.launch_ms == ms(900)
    assert optional.reconstructable == "partial"


def _constant_case(name):
    """(default-run window, non-default-run window) for one closing constant."""
    if name == "rerun":
        # a re-run launched 10 minutes after the verdict, then 18 minutes
        near = (_rerun(600), _rerun(600, close_rerun_min=7))
        far = (_rerun(1080), _rerun(1080, close_rerun_min=20))
        return near, far, ms(2300), ms(1000)
    if name == "post":
        near = (
            only(analyze(dev_closed(1200))),
            only(analyze(dev_closed(1200), close_post_min=10)),
        )
        far = (
            only(analyze(dev_closed(2400))),
            only(analyze(dev_closed(2400), close_post_min=45)),
        )
        return near, far, ms(2200), ms(1000)
    short = [feature(0)] + suite(1, "ka", "dev-tip aaa1111", 100, 580)
    long_ = [feature(0)] + suite(1, "ka", "dev-tip aaa1111", 100, 820)
    near = (only(analyze(short)), only(analyze(short, close_min_suite_min=5)))
    far = (only(analyze(long_)), only(analyze(long_, close_min_suite_min=15)))
    return near, far, None, None


def _order(gap_s, **params):
    events = [feature(0)] + suite(1, "k1x", "alpha one", 100, 800)
    events += suite(2, "k2x", "beta two", 800 + gap_s, 800 + gap_s + 650)
    return only(analyze(events, **params))


@pytest.mark.parametrize("name", ["rerun", "post", "minsuite", "rerun-order"])
def test_t09g_non_default_constants(name):
    if name == "rerun-order":
        # the order fallback reads the same constant: 10 minutes pairs by default, not at 7
        assert _order(600).basis == "order"
        assert _order(600, close_rerun_min=7).end_t_ms is None
        assert _order(1080).end_t_ms is None
        assert _order(1080, close_rerun_min=20).basis == "order"
        return
    near, far, closed_end, plain_end = _constant_case(name)
    if name != "minsuite":
        # the default extends the near case; the lowered constant does not
        assert near[0].end_t_ms == closed_end
        assert near[1].end_t_ms == plain_end
        # the default leaves the far case alone; the raised constant extends it
        assert far[0].end_t_ms == ms(1000)
        assert far[1].end_t_ms is not None and far[1].end_t_ms > ms(1000)
    else:
        # an 8 minute suite: not real by default, real at 5; the 12 minute one flips at 15
        assert near[0].status == "open" and near[0].first_missing_link == "none-real"
        assert near[1].status == "completed" and near[1].end_t_ms == ms(580)
        assert far[0].status == "completed" and far[0].end_t_ms == ms(820)
        assert far[1].status == "open" and far[1].first_missing_link == "none-real"


# ---------------------------------------------------------------------------------------------
# t10: the roster
# ---------------------------------------------------------------------------------------------


def test_t10a_both_invocation_forms_are_found():
    wrapper_only = analyze([feature(0)])
    skill_only = analyze([h.skill(0, "s1", "feature")])
    assert [i.form for i in wrapper_only.invocations] == ["wrapper"]
    assert [i.form for i in skill_only.invocations] == ["skill"]
    both = analyze([feature(0), h.skill(100, "s1", "feature")])
    assert [i.form for i in both.invocations] == ["wrapper", "skill"]
    assert len(both.windows) == 2
    # the same call recorded in both forms a few seconds apart is one invocation
    one = analyze([feature(0), h.skill(30, "s1", "feature")])
    assert [i.form for i in one.invocations] == ["wrapper"]


def test_t10b_dedupe_is_strictly_under_sixty_seconds():
    kept = analyze([feature(0), h.skill(60.000, "s1", "feature")])
    assert [i.t_ms for i in kept.invocations] == [ms(0), ms(60)]
    dropped = analyze([feature(0), h.skill(59.999, "s1", "feature")])
    assert [i.t_ms for i in dropped.invocations] == [ms(0)]
    # compared against EVERY kept invocation, not only the latest one
    chain = analyze([feature(0), feature(100), h.skill(130, "s1", "feature")])
    assert [i.t_ms for i in chain.invocations] == [ms(0), ms(100)]
    near_first = analyze([feature(0), feature(100), h.skill(159.999, "s1", "feature")])
    assert [i.t_ms for i in near_first.invocations] == [ms(0), ms(100)]


def test_t10c_text_and_other_names_are_not_invocations():
    events = [
        h.say(10, "Launching skill: feature " + h.lorem(1)),
        h.result(20, "x1", "Launching skill <command-name>/feature</command-name>"),
        h.prompt(30, "Launching skill: feature"),
        h.skill(40, "s2", "commit"),
        h.skill(50, "s3", "propagate"),
        h.skill(60, "s4", "other"),
        h.wrapper(70, "commit"),
        h.wrapper(80, "propagate"),
    ]
    cs = analyze(events)
    assert cs.invocations == ()
    assert cs.windows == ()
    # control: one real invocation in the same transcript is the only one found
    cs = analyze(events + [feature(200)])
    assert [i.t_ms for i in cs.invocations] == [ms(200)]


def test_t10d_sidechain_records_are_not_invocations():
    events = [
        h.wrapper(0, "feature", sidechain=True),
        h.skill(100, "s1", "feature", sidechain=True),
    ]
    assert analyze(events).invocations == ()


def test_t10d_sidechain_marker_closes_nothing():
    events = [feature(0)] + suite(1, "ka", "dev-tip aaa1111", 100, 1000)
    events.append(h.skill(1100, "mk", "commit", sidechain=True))
    w = only(analyze(events))
    assert w.end_t_ms == ms(1000) and w.end_marker == "verdict"


@pytest.mark.parametrize(
    "name,form",
    [
        ("commit", "skill"),
        ("commit", "wrapper"),
        ("propagate", "skill"),
        ("propagate", "wrapper"),
    ],
)
def test_t10e_both_marker_forms_close_a_cycle(name, form):
    w = only(analyze(dev_closed(100, name=name, form=form)))
    assert w.status == "completed"
    assert w.end_t_ms == ms(1100)
    assert w.end_marker == name


def test_t10f_open_versus_partial_and_distance_from_last_ts():
    events = [feature(0), feature(1000), h.say(1000 + 7200, h.lorem(3))]
    first, second = analyze(events).windows
    assert first.status == "partial" and first.minutes_from_last_ts is None
    assert second.status == "open"
    assert second.end_t_ms is None
    assert second.minutes_from_last_ts == 120.0
    assert second.reconstructable == "excluded"
    # a different gap gives a different number
    events = [feature(0), h.say(30, h.lorem(3))]
    assert only(analyze(events)).minutes_from_last_ts == 0.5
    # a closed last invocation is completed, not open
    done = only(analyze(dev_closed(10)))
    assert done.status == "completed" and done.minutes_from_last_ts is None


def test_t10g_plan_only_is_read_from_either_form():
    wrapper_plan = analyze([h.wrapper(0, "feature", "--plan-only " + h.lorem(1))])
    skill_plan = analyze([h.skill(0, "s1", "feature", "--plan-only")])
    plain_wrapper = analyze([feature(0)])
    plain_skill = analyze([h.skill(0, "s1", "feature", "")])
    assert [i.plan_only for i in wrapper_plan.invocations] == [True]
    assert [i.plan_only for i in skill_plan.invocations] == [True]
    assert [i.plan_only for i in plain_wrapper.invocations] == [False]
    assert [i.plan_only for i in plain_skill.invocations] == [False]
    for cs in (wrapper_plan, skill_plan):
        w = only(cs)
        assert w.plan_only is True and w.lane == "plan-only"
        assert w.lane_source == "invocation-flag"
    assert only(plain_wrapper).plan_only is False


def test_t10h_lane_from_mentions_inside_the_lane_window():
    events = [feature(0), h.say(60, "the fast lane suffices " + h.lorem(2))]
    events += [h.say(2400, "now the full lane applies " + h.lorem(3))]
    inside = only(analyze(events))
    assert (inside.lane, inside.lane_source) == ("fast-lane", "lane-mention")
    # a 50 minute window sees both mentions: a tie is no explicit lane (no dispatches here)
    wide = only(analyze(events, lane_window_min=50))
    assert (wide.lane, wide.lane_source) == ("abandoned", "no-dispatch")
    only_late = only(analyze([feature(0), events[-1]]))
    assert only_late.lane_source != "lane-mention"
    assert (
        only(analyze([feature(0), events[-1]], lane_window_min=50)).lane == "full-lane"
    )
    # inferred: three or more opus or fable dispatches before the first suite is full
    design = [feature(0)]
    for n in range(3):
        design.append(h.agent(10 + n, f"a{n}", "opus", "plan lorem"))
    design += suite(1, "ka", "dev-tip aaa1111", 100, 800)
    full = only(analyze(design))
    assert (full.lane, full.lane_source) == ("full-lane", "dispatch-count")
    fast = only(analyze(design[:3] + suite(1, "ka", "dev-tip aaa1111", 100, 800)))
    assert fast.lane == "fast-lane"


# ---------------------------------------------------------------------------------------------
# t11: run-long pairing
# ---------------------------------------------------------------------------------------------


def _pair(events, **params):
    return analyze(events, **params).pairs


def test_t11a_key_arm_pairs_by_artifact_key():
    events = [feature(0)]
    events += [
        h.runlong_launch(100, "la", "ka", "x"),
        h.runlong_launch(110, "lb", "kb", "x"),
    ]
    events += [h.runlong_wait(120, "wb", "kb"), h.enqueue(500, "wb", "tb", 1)]
    events += [h.runlong_wait(130, "wa", "ka"), h.enqueue(900, "wa", "ta", 1)]
    # by key: A pairs with its own (later) wait, not the nearer one
    a, b = _pair(events)
    assert (a.terminal_ms, a.method) == (ms(900), "bg-wait-notification")
    assert (b.terminal_ms, b.method) == (ms(500), "bg-wait-notification")


def test_t11a_nearest_arm_pairs_an_unresolved_key():
    # an unresolved artifact variable pairs with the nearest background wait within 180 s
    var = [h.runlong_launch(1000, "lc", "$VAR", "x"), h.runlong_wait(1100, "wc", "kc")]
    var += [h.enqueue(1500, "wc", "tc", 1)]
    (pc,) = _pair(var)
    assert (pc.terminal_ms, pc.method) == (ms(1500), "bg-wait-nearest")
    assert pc.art_key == m.art_key_digest("kc")
    # an unlabelled suite resolved through a wait is classed from that wait's artifact key
    named = [
        h.runlong_launch(4000, "lf", "$VAR", "x"),
        h.runlong_wait(4100, "wf", "dev-art"),
    ]
    named += [h.enqueue(4500, "wf", "tf", 1)]
    (pf,) = _pair(named)
    assert (pf.method, pf.check_class) == ("bg-wait-nearest", "suite-dev-tip")
    # two different shell variables are different keys: a wait on one does not pair the other
    other = [
        h.runlong_launch(5000, "lg", "kg", "x"),
        h.runlong_wait(5100, "wg", "$ONE"),
    ]
    other += [h.enqueue(5500, "wg", "tg", 1)]
    assert _pair(other)[0].method == "UNPAIRED"


def test_t11a_nearest_arm_upper_edge_180s_is_inclusive():
    edge = [
        h.runlong_launch(2000, "ld", "$VAR", "x"),
        h.runlong_wait(2180.000, "wd", "kd"),
    ]
    edge += [h.enqueue(2500, "wd", "td", 1)]
    assert _pair(edge)[0].method == "bg-wait-nearest"
    late = [
        h.runlong_launch(3000, "le", "$VAR", "x"),
        h.runlong_wait(3180.001, "we", "ke"),
    ]
    late += [h.enqueue(3500, "we", "te", 1)]
    assert _pair(late)[0].method == "UNPAIRED"


def test_t11a_nearest_arm_lower_edge_is_exclusive():
    same = [
        h.runlong_launch(2000, "ld", "$VAR", "x"),
        h.runlong_wait(2000.000, "wd", "kd"),
    ]
    same += [h.enqueue(2500, "wd", "td", 1)]
    assert _pair(same)[0].method == "UNPAIRED"
    after = [
        h.runlong_launch(2000, "ld", "$VAR", "x"),
        h.runlong_wait(2000.001, "wd", "kd"),
    ]
    after += [h.enqueue(2500, "wd", "td", 1)]
    assert _pair(after)[0].method == "bg-wait-nearest"


def test_t11a_relaunch_shares_its_terminal_through_the_nearest_arm():
    """A wait paired through the key arm stays available to an earlier launch of the key.

    The original never lets the key arm claim a wait, and a round-1 version that did was
    stricter than the original, was measured to lose launches on real data and was reverted.
    """
    events = [
        h.runlong_launch(0, "la", "kk", "x"),
        h.runlong_launch(18, "lb", "kk", "x"),
    ]
    events += [h.runlong_wait(59, "wk", "kk"), h.enqueue(500, "wk", "tk", 1)]
    a, b = _pair(events)
    assert (b.terminal_ms, b.method) == (ms(500), "bg-wait-notification")
    assert (a.terminal_ms, a.method) == (ms(500), "bg-wait-nearest")


def test_t11a_nearest_arm_claims_a_wait_it_took_for_one_launch_only():
    events = [h.runlong_launch(100, "l1", "$VAR", "x")]
    events += [h.runlong_launch(110, "l2", "$VAR", "x")]
    events += [h.runlong_wait(120, "wk", "kw"), h.enqueue(500, "wk", "tk", 1)]
    first, second = _pair(events)
    assert (first.terminal_ms, first.method) == (ms(500), "bg-wait-nearest")
    assert (second.terminal_ms, second.method) == (None, "UNPAIRED")
    # with a second wait available the second launch takes that one
    events += [h.runlong_wait(130, "wj", "kw2"), h.enqueue(600, "wj", "tj", 1)]
    first, second = _pair(events)
    assert (first.terminal_ms, second.terminal_ms) == (ms(500), ms(600))


def test_t11a_unresolved_launch_takes_the_nearest_unclaimed_wait():
    events = [h.runlong_launch(100, "l1", "$VAR", "x")]
    events += [h.runlong_wait(110, "w1", "k1"), h.enqueue(400, "w1", "t1", 1)]
    events += [h.runlong_wait(120, "w2", "k2"), h.enqueue(450, "w2", "t2", 1)]
    (p,) = _pair(events)
    assert (p.terminal_ms, p.art_key) == (ms(400), m.art_key_digest("k1"))


def test_t11a_nearest_arm_skips_a_wait_on_a_different_resolved_key():
    events = [h.runlong_launch(100, "l1", "ka", "x")]
    events += [h.runlong_wait(110, "w1", "kb"), h.enqueue(400, "w1", "t1", 1)]
    (p,) = _pair(events)
    assert (p.terminal_ms, p.method) == (None, "UNPAIRED")


def test_t11b_launch_and_wait_in_one_call_is_a_launch():
    events = [feature(0), h.runlong_launch(100, "lx", "kx", "x")]
    events += [h.runlong_launch(150, "ly", "ky", "x", wait_too=True, bg=True)]
    events += [h.enqueue(900, "ly", "ty", 1)]
    pairs = _pair(events)
    assert len(pairs) == 2
    assert [p.method for p in pairs] == ["UNPAIRED", "UNPAIRED"]
    assert [p.art_key for p in pairs] == [
        m.art_key_digest("kx"),
        m.art_key_digest("ky"),
    ]
    # as a wait it would pair the earlier launch through the nearest-wait arm
    var = [h.runlong_launch(100, "lx", "$VAR", "x")]
    var += [h.runlong_launch(150, "ly", "ky", "x", wait_too=True, bg=True)]
    var += [h.enqueue(900, "ly", "ty", 1)]
    assert [p.method for p in _pair(var)] == ["UNPAIRED", "UNPAIRED"]


def test_t11c_unpaired_is_not_a_wait():
    events = [feature(0), h.runlong_launch(100, "la", "ka", "dev-tip aaa1111")]
    (p,) = _pair(events)
    assert p.method == "UNPAIRED" and p.terminal_ms is None
    assert p.check_class == "suite-dev-tip" and p.is_suite
    w = only(analyze(events))
    assert w.end_t_ms is None
    assert w.pairing_weak is True
    assert w.first_missing_link == "none-paired"
    assert w.status == "open"


def test_t11d_notification_keyed_to_the_launch_never_pairs():
    own = [feature(0), h.runlong_launch(100, "la", "ka", "x")]
    own += [h.enqueue(900, "la", "ta", 1), h.delivery(901, "la", "ta", 1)]
    assert [p.method for p in _pair(own)] == ["UNPAIRED"]
    keyed = [h.runlong_launch(100, "la", "ka", "x"), h.runlong_wait(110, "wa", "ka")]
    keyed += [h.enqueue(900, "wa", "ta", 1)]
    (p,) = _pair(keyed)
    assert (p.terminal_ms, p.method) == (ms(900), "bg-wait-notification")


def test_t11e_foreground_wait_with_a_result_line_pairs():
    base = [h.runlong_launch(100, "la", "ka", "x")]
    fg = base + [h.runlong_wait(200, "wa", "ka", bg=False)]
    fg += [h.result(260, "wa", "RESULT: PASS rc=0")]
    (p,) = _pair(fg)
    assert (p.terminal_ms, p.method) == (ms(260), "fg-wait-result")


def test_t11e_foreground_status_with_a_result_line_pairs():
    base = [h.runlong_launch(100, "la", "ka", "x")]
    status = base + [h.runlong_wait(200, "sa", "ka", bg=False, mode="status")]
    status += [h.result(210, "sa", "RESULT: FAIL rc=1")]
    (p,) = _pair(status)
    assert (p.terminal_ms, p.method) == (ms(210), "fg-status-result")


def test_t11e_foreground_result_without_a_result_line_does_not_pair():
    base = [h.runlong_launch(100, "la", "ka", "x")]
    running = base + [
        h.runlong_wait(200, "wa", "ka", bg=False),
        h.result(210, "wa", "RUNNING"),
    ]
    (p,) = _pair(running)
    assert p.method == "UNPAIRED"


def _fg_status(result_at, text, call_at=200, tid="sa"):
    return [
        h.runlong_wait(call_at, tid, "ka", bg=False, mode="status"),
        h.result(result_at, tid, text),
    ]


def test_t11e_foreground_status_saying_done_pairs_at_its_result_time():
    base = [h.runlong_launch(100, "la", "ka", "x")]
    (p,) = _pair(base + _fg_status(210, "job DONE"))
    assert (p.terminal_ms, p.method) == (ms(210), "fg-status-result")
    wait = base + [
        h.runlong_wait(200, "wa", "ka", bg=False),
        h.result(260, "wa", "DONE"),
    ]
    (w,) = _pair(wait)
    assert (w.terminal_ms, w.method) == (ms(260), "fg-wait-result")


def test_t11e_foreground_status_with_running_never_pairs_on_done():
    base = [h.runlong_launch(100, "la", "ka", "x")]
    for text in ("DONE RUNNING", "RUNNING", "RUNNING then DONE", "UNDONE"):
        (p,) = _pair(base + _fg_status(210, text))
        assert p.method == "UNPAIRED", text
    (p,) = _pair(base + _fg_status(210, "RESULT: PASS rc=0 RUNNING"))
    assert (p.terminal_ms, p.method) == (ms(210), "fg-status-result")


def test_t11e_earlier_done_status_wins_over_a_later_result_line_status():
    base = [h.runlong_launch(100, "la", "ka", "x")]
    events = base + _fg_status(210, "RUNNING", 200, "s0")
    events += _fg_status(310, "DONE", 300, "s1")
    events += _fg_status(12000, "RESULT: PASS rc=0", 11990, "s2")
    (p,) = _pair(events)
    assert (p.terminal_ms, p.method) == (ms(310), "fg-status-result")
    # without the DONE call the later RESULT: line is the terminal
    late = base + _fg_status(210, "RUNNING", 200, "s0")
    late += _fg_status(12000, "RESULT: PASS rc=0", 11990, "s2")
    assert _pair(late)[0].terminal_ms == ms(12000)


def test_t11f_suite_class_unpaired_launch_makes_the_cycle_pairing_weak():
    k1 = h.k1_events("skill")
    clean = only(analyze(k1))
    assert clean.status == "completed" and clean.pairing_weak is False
    weak = only(analyze(k1 + [h.runlong_launch(2000, "lz", "kz", "dev-tip ccc3333")]))
    assert weak.status == "completed" and weak.pairing_weak is True
    # an unpaired launch that is not suite-class does not count
    other = only(analyze(k1 + [h.runlong_launch(2000, "lz", "kz", "x", suite=False)]))
    assert other.pairing_weak is False


def test_k1_and_k1w_windows_match_the_known_answer():
    for variant in ("skill", "wrapper"):
        t = r.parse_bytes(h.k1_transcript(variant))
        w = only(c.analyze_cycles(t, m.Params(min_cycles=1)))
        assert (w.status, w.basis, w.reconstructable) == ("completed", "label", "full")
        assert w.end_t_ms - w.start_t_ms == 2430000
        assert (w.f1.launch_ms, w.d1.launch_ms) == (ms(600), ms(1650))
        assert w.chain == () and w.suite_runs == 2 and w.first_missing_link is None
        assert w.end_marker == "commit" and w.pairing_weak is False


# ---------------------------------------------------------------------------------------------
# t36: --artifacts, t38: --repo
# ---------------------------------------------------------------------------------------------


def _artifact(directory, name, mtime_s, text):
    path = Path(directory) / name
    path.write_text(text)
    stamp = h.BASE_MS / 1000 + mtime_s
    os.utime(path, (stamp, stamp))


def test_t36_artifact_end_check(tmp_path):
    tmp = tmp_path.resolve()
    cs = c.analyze_cycles(r.parse_bytes(h.k1_transcript("skill")), m.Params())
    done = "RUN_LONG_BEGIN x\nRESULT: PASS rc=0\nRUN_LONG_EXIT_STATUS=0\n"
    both = tmp / "both"
    both.mkdir()
    _artifact(both, "art1", 1500, done)
    _artifact(both, "art2", 1800, done)
    res = c.artifact_end_check(cs, [both])
    assert res.status == "measured"
    assert (res.agree, res.disagree, res.absent, res.unpaired) == (1, 1, 0, 0)
    by_class = {row.check_class: row for row in res.rows}
    assert by_class["suite-feature-tip"].status == "agree"
    assert by_class["suite-feature-tip"].exit_status == "0"
    assert by_class["suite-feature-tip"].verdict == "PASS"
    assert by_class["suite-dev-tip"].status == "disagree"
    assert by_class["suite-dev-tip"].end_minus_mtime_s == 600.0
    # the end tolerance is a parameter: 600 s apart agrees at 700
    wide = c.artifact_end_check(cs, [both], tolerance_s=700)
    assert (wide.agree, wide.disagree) == (2, 0)
    # one artifact file absent
    only_one = tmp / "one"
    only_one.mkdir()
    _artifact(only_one, "art1", 1500, "no exit line here\n")
    res = c.artifact_end_check(cs, [only_one])
    assert (res.agree, res.absent) == (1, 1)
    first = [row for row in res.rows if row.check_class == "suite-feature-tip"][0]
    assert first.exit_status is None and first.verdict is None
    # a PARALLEL-PASS verdict line is read as a verdict, never dropped
    par = tmp / "par"
    par.mkdir()
    _artifact(par, "art1", 1500, "RESULT: PARALLEL-PASS rc=0\nRUN_LONG_EXIT_STATUS=0\n")
    res = c.artifact_end_check(cs, [par])
    first = [row for row in res.rows if row.check_class == "suite-feature-tip"][0]
    assert first.verdict == "PASS"
    # a file whose name differs from the key by one character is no match
    near = tmp / "near"
    near.mkdir()
    _artifact(near, "art1x", 1500, done)
    _artifact(near, "Art1", 1500, done)
    res = c.artifact_end_check(cs, [near])
    assert (res.agree, res.disagree, res.absent) == (0, 0, 2)
    # the first directory that holds the name wins
    res = c.artifact_end_check(cs, [only_one, both])
    assert (res.agree, res.disagree, res.absent) == (1, 1, 0)
    # no directory given: unmeasured, never agree
    res = c.artifact_end_check(cs, [])
    assert res.status == "unmeasured"
    assert (res.agree, res.disagree, res.absent) == (0, 0, 0)
    # an artifact of a launch that never paired is `unpaired`, not agree
    lone = [feature(0), h.runlong_launch(100, "la", "art9", "x")]
    lone_set = analyze(lone)
    stray = tmp / "stray"
    stray.mkdir()
    _artifact(stray, "art9", 500, done)
    assert c.artifact_end_check(lone_set, [stray]).unpaired == 1


_GIT_ENV = {
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@example.invalid",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@example.invalid",
}


def _git(repo, *args, when=None):
    env = dict(os.environ)
    env.update(_GIT_ENV)
    if when is not None:
        env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = when
    out = subprocess.run(
        ["git", "-C", str(repo), *args],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    return out.stdout.strip()


def _fixture_repo(tmp):
    repo = tmp / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True, timeout=30)
    for key, value in (("commit.gpgsign", "false"), ("tag.gpgsign", "false")):
        _git(repo, "config", key, value)
    return repo


def test_t38_commit_time_lower_bound(tmp_path):
    repo = _fixture_repo(tmp_path.resolve())
    # one commit well before the suites, one after them (the future commit is impossible
    # as a label of an earlier launch, which is exactly what the bound catches)
    _git(repo, "commit", "-q", "--allow-empty", "-m", "a", when="2029-12-31T00:00:00Z")
    early = _git(repo, "rev-parse", "--short=10", "HEAD")
    _git(repo, "commit", "-q", "--allow-empty", "-m", "b", when="2030-01-01T05:00:00Z")
    late = _git(repo, "rev-parse", "--short=10", "HEAD")
    events = [feature(0)]
    events += suite(1, "ka", f"feature-tip {early}", 100, 800)
    events += suite(2, "kb", f"dev-tip {late}", 900, 1600)
    events += suite(3, "kc", "dev-tip 0123abc", 1700, 2400)
    cs = analyze(events)
    res = c.commit_time_lower_bound(cs, repo)
    assert res.status == "measured"
    assert (res.ok, res.violated, res.unknown) == (1, 1, 1)
    by_launch = {row.launch_ms: row.status for row in res.rows}
    assert by_launch == {ms(100): "ok", ms(900): "violated", ms(1700): "unknown"}
    assert res.lower_bound_ms == ms(5 * 3600)
    # the label sha of a pair is kept; nothing else of the label is
    assert [p.label_sha for p in cs.pairs] == [early, late, "0123abc"]
    # without a repository the bound is unmeasured, never ok
    none = c.commit_time_lower_bound(cs, None)
    assert none.status == "unmeasured"
    assert (none.ok, none.violated, none.unknown) == (0, 0, 0)


# ---------------------------------------------------------------------------------------------
# t45: the first missing link
# ---------------------------------------------------------------------------------------------


def test_t45_first_missing_link():
    fixtures = {
        "no-suite-launch": [feature(0), h.say(10, h.lorem(1))],
        "none-paired": [
            feature(0),
            h.runlong_launch(100, "la", "ka", "dev-tip aaa1111"),
        ],
        "none-real": [feature(0)] + suite(1, "ka", "dev-tip aaa1111", 100, 400),
        "no-dev-or-order-pair": (
            [feature(0)] + suite(1, "ka", "feature-tip aaa1111", 100, 800)
        ),
    }
    seen = set()
    for token, events in fixtures.items():
        w = only(analyze(events))
        assert w.end_t_ms is None, token
        assert w.first_missing_link == token
        seen.add(w.first_missing_link)
    assert seen == set(m.MISSING_LINKS)
    # the earliest missing link wins: a short paired suite beside an unpaired one is none-real
    both = [feature(0)] + suite(1, "ka", "dev-tip aaa1111", 100, 400)
    both += [h.runlong_launch(500, "lb", "kb", "dev-tip bbb2222")]
    assert only(analyze(both)).first_missing_link == "none-real"
    # a completed cycle has none
    assert only(analyze(dev_closed())).first_missing_link is None
    # a non-suite run-long launch is not a suite-class launch
    plain = [feature(0), h.runlong_launch(100, "la", "ka", "x", suite=False)]
    assert only(analyze(plain)).first_missing_link == "no-suite-launch"


def test_empty_and_no_invocation_transcripts():
    empty = c.analyze_cycles(r.parse_bytes(b""), m.Params())
    assert (empty.invocations, empty.windows, empty.pairs) == ((), (), ())
    nothing = analyze([h.say(0, h.lorem(1)), h.prompt(10)])
    assert nothing.windows == () and nothing.invocations == ()


# ---------------------------------------------------------------------------------------------
# Fix round 1: lane bound, suite_runs, chain rows, sidechain scope, artifact tail, commit second
# ---------------------------------------------------------------------------------------------


def _opus(ats, first=0):
    return [
        h.agent(at, f"a{first + n}", "opus", "plan lorem") for n, at in enumerate(ats)
    ]


def test_g1_lane_counts_dispatches_before_the_first_suite_only():
    # no feature-tip suite: dispatches during the dev-tip suite do not count
    during = [feature(0)] + _opus([300, 301, 302])
    during += suite(1, "ka", "dev-tip aaa1111", 100, 800)
    w = only(analyze(during))
    assert (w.lane, w.lane_source) == ("fast-lane", "dispatch-count")
    # dispatches between two feature-tip suites do not count either
    between = [feature(0)] + _opus([850, 851, 852])
    between += suite(1, "ka", "feature-tip aaa1111", 100, 800)
    between += suite(2, "kb", "feature-tip bbb2222", 1000, 1700)
    between += suite(3, "kc", "dev-tip ccc3333", 1800, 2500)
    w = only(analyze(between))
    assert w.f1.launch_ms == ms(1000)
    assert w.lane == "fast-lane"
    # an unpaired suite-class launch is still the first suite launched
    unpaired = [feature(0), h.runlong_launch(50, "lu", "ku", "dev-tip ddd4444")]
    unpaired += _opus([300, 301, 302]) + suite(1, "ka", "dev-tip aaa1111", 100, 800)
    assert only(analyze(unpaired)).lane == "fast-lane"


def test_g1_lane_threshold_is_three_heavy_dispatches_before_the_first_suite():
    assert c.FULL_LANE_DISPATCHES == 3
    for count, lane in ((2, "fast-lane"), (3, "full-lane"), (4, "full-lane")):
        events = [feature(0)] + _opus([10 + n for n in range(count)])
        events += suite(1, "ka", "dev-tip aaa1111", 100, 800)
        assert only(analyze(events)).lane == lane
    # a skill-reviewer dispatched on an opus model is not an opus dispatch (role_of)
    events = [feature(0)]
    for n in range(3):
        events.append(
            h.agent(
                10 + n, f"a{n}", "opus", "plan lorem", subagent_type="skill-reviewer"
            )
        )
    events += suite(1, "ka", "dev-tip aaa1111", 100, 800)
    assert only(analyze(events)).lane == "fast-lane"


def test_g2_suite_runs_counts_paired_suite_runs_inside_the_window():
    events = [feature(0)]
    events += suite(1, "k1x", "feature-tip aaa1111", 100, 800)
    events += suite(2, "k2x", "dev-tip bbb2222", 900, 1600)
    events += suite(3, "k3x", "alpha short", 30, 330)  # short but paired: counts
    events += [h.runlong_launch(1200, "lu", "ku", "dev-tip ccc3333")]  # unpaired
    events += [h.runlong_launch(1300, "lp", "kp", "x", suite=False)]  # not suite-class
    events += suite(4, "k4x", "feature-tip eee5555", 1600.001, 2300)  # after the end
    w = only(analyze(events))
    assert (w.status, w.end_t_ms) == ("completed", ms(1600))
    assert w.suite_runs == 3
    assert w.pairing_weak is True
    # a run launched exactly at the window end still counts
    events = events[:-5] + suite(4, "k4x", "feature-tip eee5555", 1600.000, 2300)
    assert only(analyze(events)).suite_runs == 4
    # a window with no end counts its paired runs up to the search bound
    open_ = [feature(0)] + suite(1, "ka", "dev-tip aaa1111", 100, 400)
    w = only(analyze(open_))
    assert (w.status, w.suite_runs) == ("open", 1)
    two = [feature(0), feature(5000)] + suite(1, "ka", "dev-tip aaa1111", 5100, 5200)
    two += suite(2, "kb", "dev-tip bbb2222", 100, 200)
    first, second = analyze(two).windows
    assert (first.suite_runs, second.suite_runs) == (1, 1)


def test_g8b_order_basis_chains_an_unlabelled_rerun():
    events = [feature(0)] + suite(1, "k1x", "alpha one", 100, 800)
    events += suite(2, "k2x", "beta two", 900, 1600)
    events += suite(3, "k3x", "gamma three", 1700, 2400)
    w = only(analyze(events))
    assert w.basis == "order"
    assert len(w.chain) == 1 and w.chain[0].launch_ms == ms(1700)
    assert w.end_t_ms == ms(2400) and w.end_marker == "verdict-rerun"
    # on the label basis an unlabelled later suite is not chained
    labelled = [feature(0)] + suite(1, "ka", "dev-tip aaa1111", 100, 800)
    labelled += suite(2, "kb", "gamma three", 900, 1600)
    w = only(analyze(labelled))
    assert w.basis == "label" and w.chain == () and w.end_t_ms == ms(800)


def test_g8c_a_rerun_launched_before_the_verdict_is_not_chained():
    events = [feature(0)] + suite(1, "ka", "dev-tip aaa1111", 100, 1000)
    events += suite(2, "kb", "dev-tip bbb2222", 990, 1700)
    w = only(analyze(events))
    assert w.chain == () and w.end_t_ms == ms(1000)


def test_g8d_a_feature_tip_suite_after_the_verdict_is_not_chained():
    events = [feature(0)] + suite(1, "ka", "dev-tip aaa1111", 100, 1000)
    events += suite(2, "kb", "feature-tip bbb2222", 1100, 1800)
    only_f = only(analyze(events))
    assert only_f.chain == () and only_f.end_t_ms == ms(1000)
    events += suite(3, "kc", "dev-tip ccc3333", 1850, 2600)
    w = only(analyze(events))
    assert [p.launch_ms for p in w.chain] == [ms(1850)]
    assert w.end_t_ms == ms(2600)


def test_g6_sidechain_record_last_in_the_transcript_moves_no_bound():
    events = [
        feature(0),
        h.say(30, h.lorem(1)),
        h.say(7200, h.lorem(2), sidechain=True),
    ]
    w = only(analyze(events))
    assert w.search_bound_ms == ms(31)
    assert w.status == "open" and w.minutes_from_last_ts == 0.5
    # only sidechain records: nothing to measure
    side = analyze([h.say(0, h.lorem(1), sidechain=True)])
    assert side.windows == () and side.pairs == ()
    # a suite launched in a sidechain record is not a launch
    events = [feature(0)] + suite(1, "ka", "dev-tip aaa1111", 100, 800)
    side_suite = [dict(e, rec=dict(e["rec"], isSidechain=True)) for e in events[1:]]
    assert (
        only(analyze([events[0]] + side_suite)).first_missing_link == "no-suite-launch"
    )


def test_g4_artifact_terminal_lines_are_read_from_the_tail(tmp_path):
    tmp = tmp_path.resolve()
    cs = c.analyze_cycles(r.parse_bytes(h.k1_transcript("skill")), m.Params())
    directory = tmp / "big"
    directory.mkdir()
    body = "x\n" * (1 << 20)
    tail = "RESULT: PASS rc=0\nRUN_LONG_EXIT_STATUS=0\n"
    _artifact(directory, "art1", 1500, body + tail)
    assert len(body) > (1 << 20)
    res = c.artifact_end_check(cs, [directory])
    row = [x for x in res.rows if x.check_class == "suite-feature-tip"][0]
    assert (row.exit_status, row.verdict, row.status) == ("0", "PASS", "agree")
    # a terminal line of a much older run, far above the tail, is not read
    _artifact(directory, "art1", 1500, "RUN_LONG_EXIT_STATUS=9\n" + body + tail)
    row = c.artifact_end_check(cs, [directory]).rows[0]
    assert row.exit_status == "0"


def test_g5_commit_second_resolution(tmp_path):
    repo = _fixture_repo(tmp_path.resolve())
    _git(repo, "commit", "-q", "--allow-empty", "-m", "a", when="2030-01-01T00:15:00Z")
    sha = _git(repo, "rev-parse", "--short=10", "HEAD")
    events = [feature(0)]
    for n, at in enumerate((899.999, 900.000, 900.999, 901.000), 1):
        events += suite(n, f"k{n}x", f"feature-tip {sha}", at, at + 700)
    cs = analyze(events)
    res = c.commit_time_lower_bound(cs, repo)
    status = {row.launch_ms: row.status for row in res.rows}
    assert status == {
        ms(899.999): "violated",
        ms(900.000): "same-second",
        ms(900.999): "same-second",
        ms(901.000): "ok",
    }
    assert (res.ok, res.violated, res.same_second, res.unknown) == (1, 1, 2, 0)
    assert res.lower_bound_ms == ms(900)

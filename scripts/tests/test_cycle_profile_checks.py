"""Tests for the cycle-profile checks module: fifteen self-checks and four second sides.

Every transcript is synthetic (fictional base date 2030-01-01, invented ids, lorem text),
built from `cycle_profile_helpers`. Each check is fed (a) a correct input, which must give
`pass`, (b) the same input corrupted in ONE way, which must give `fail`, and (c) an input
whose second side is absent, which must give `skip` and never `pass`. t39 is parametrized
over the check names DISCOVERED from the module (every `check_<name>`), with a declared
floor of 15, so each check is its own node and a removed check alarms. t39s is the static
row: the independent second sides must not reference the primary code they judge.
"""

import ast
import copy
import re
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import cycle_profile_checks as ck  # noqa: E402, I001
import cycle_profile_classes as k  # noqa: E402
import cycle_profile_cycles as c  # noqa: E402
import cycle_profile_delegates as dl  # noqa: E402
import cycle_profile_helpers as h  # noqa: E402
import cycle_profile_model as m  # noqa: E402
import cycle_profile_pool as pl  # noqa: E402
import cycle_profile_records as r  # noqa: E402

MS = 1000
CHECK_FLOOR = 15
SOURCE = Path(ck.__file__).read_text()
DETAIL_RE = re.compile(r"^[a-z0-9_= ,-]*$")


def ms(seconds):
    """Offset in seconds from the fictional base, as integer epoch milliseconds."""
    return h.BASE_MS + round(seconds * MS)


def discovered():
    """The check names the module defines: every `check_<name>`, upper-cased."""
    return sorted(
        n[len("check_") :].upper().replace("_", "-")
        for n in dir(ck)
        if n.startswith("check_")
    )


# ---------------------------------------------------------------------------------------------
# Synthetic chains
# ---------------------------------------------------------------------------------------------


def align_ids(events):
    """Make each notification's task id equal its dispatch's agent id, as a real session does."""
    swap = {"<task-id>t1</task-id>": h.AGENT1, "<task-id>t2</task-id>": h.AGENT2}
    for item in events:
        rec = item["rec"]
        for holder in (rec, rec.get("message") or {}):
            text = holder.get("content")
            if isinstance(text, str):
                for old, new in swap.items():
                    text = text.replace(old, "<task-id>%s</task-id>" % new)
                holder["content"] = text
    return events


def rich_events():
    """K1 plus nine idle gaps, a long Edit pair that overlaps and a lone long Edit.

    Nine gaps clear the default fence floor of 8; the edits at 1560 and 1565 overlap (union
    30 s against a sum of 45 s) and a third long edit sits inside the first dispatch.
    """
    ev = align_ids(h.k1_events("skill"))
    ev += [h.prompt(t) for t in (12, 34, 58, 75, 93, 1512, 1545, 1605)]
    ev += [h.edit(1565, "e4"), h.result(1580, "e4")]
    ev += [h.edit(200, "e5"), h.result(215, "e5")]
    ev += [h.hook(541, "e1", 500), h.hook(1561, "e3", 2000)]
    ev += [h.hook(2500, "e3", 1000)]
    ev.sort(key=lambda e: e["at"])
    return ev


class Chain:
    """One synthetic transcript read through cycles, classes, delegates, split and pool."""

    def __init__(self, tmp_path, events, files=None, **over):
        self.params = m.Params(min_cycles=1, **over)
        self.transcript = r.parse_bytes(h.build_transcript(events))
        self.cycles = c.analyze_cycles(self.transcript, self.params)
        self.context = k.build_context(self.cycles, self.transcript, self.params)
        self.classes = k.classify(self.cycles, self.transcript, self.params)
        self.cuts = [k.stage_cut(self.context, w) for w in self.context.windows]
        self.dir = h.write_delegate_dir(
            Path(tmp_path) / "agents",
            h.k1_delegate_files() if files is None else files,
        )
        self.delegates = dl.read_delegates(
            self.dir, self.classes.dispatches, self.params
        )
        self.split = dl.split(self.cycles, self.transcript, self.params, self.delegates)
        inputs = pl.check_inputs(self.cycles, self.transcript, self.params)
        self.buckets = inputs.stage_bucket_s
        self.pool = pl.pool_results(
            self.cycles,
            self.classes,
            self.delegates,
            self.params,
            split=self.split,
            wait_by_check=inputs.wait_by_check,
        )
        self.robust = k.fence_robust(self.context)

    def with_class_s(self, **delta):
        """The class set with the first cycle's class seconds shifted by `delta`."""
        cycle = self.classes.cycles[0]
        class_s = dict(cycle.class_s)
        for name, value in delta.items():
            class_s[name] += value
        edited = cycle._replace(class_s=class_s)
        return self.classes._replace(cycles=[edited] + list(self.classes.cycles[1:]))


class Env:
    """The three chains every case draws on: rich, open-only (nothing completed), no edits."""

    def __init__(self, tmp_path):
        self.rich = Chain(tmp_path / "rich", rich_events())
        self.open = Chain(
            tmp_path / "open", [h.wrapper(0, "feature"), h.say(10), h.say(20)]
        )
        self.k1 = Chain(tmp_path / "k1", align_ids(h.k1_events("skill")))


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    return Env(tmp_path_factory.mktemp("checks"))


def verdict_of(result):
    """The verdict of a check result: a row, or the final row of a family."""
    row = result[-1] if isinstance(result, list) else result
    assert isinstance(row, m.CheckRow)
    return row.verdict


def row_of(result):
    return result[-1] if isinstance(result, list) else result


# ---------------------------------------------------------------------------------------------
# One call per (check, case): the correct input, the corrupted one, the absent second side
# ---------------------------------------------------------------------------------------------


def edit_split(split, **delta):
    row = dict(split.cycles[0])
    for name, value in delta.items():
        row[name] += value
    return split._replace(cycles=[row] + list(split.cycles[1:]))


def edit_halves(pool, **delta):
    halves = copy.deepcopy(pool.halves)
    for path, value in delta.items():
        block, key = path.split("__")
        halves[block][key] += value
    return pool._replace(halves=halves)


def hook_total(chain):
    return k.hook_union_total(chain.transcript, chain.params.hook_latency_s * 1000)


def good_profile(chain):
    return {"pool": {"halves": chain.pool.halves}}


def bad_profile():
    block = {"denominator_s": 0.0, "work_pct": 50.0, "gate_pct": None}
    return {"pool": {"halves": {"active": block}}}


def shifted_first_subagent(context):
    a, b, label = context.subagent[0]
    return context._replace(subagent=[(a, b + 1000, label)] + context.subagent[1:])


GOOD = {
    "SUMCHECK": lambda e: ck.check_sumcheck(e.rich.cycles, e.rich.classes),
    "CLASSORACLE": lambda e: ck.check_classoracle(e.rich.context, e.rich.classes),
    "STAGECHECK": lambda e: ck.check_stagecheck(e.rich.cycles, e.rich.cuts),
    "PAIRCHECK": lambda e: ck.check_paircheck(e.rich.cycles, e.rich.transcript),
    "SPLITCHECK": lambda e: ck.check_splitcheck(e.rich.classes, e.rich.split),
    "INTERVALCHECK": lambda e: ck.check_intervalcheck(e.rich.context),
    "ARMCHECK": lambda e: ck.check_armcheck(e.rich.split),
    "POOLCHECK": lambda e: ck.check_poolcheck(e.rich.pool, e.rich.buckets),
    "HALFCHECK": lambda e: ck.check_halfcheck(e.rich.pool),
    "IDLECHECK": lambda e: ck.check_idlecheck(e.rich.context, e.rich.transcript),
    "FENCECHECK": lambda e: ck.check_fencecheck(e.rich.classes, e.rich.params),
    "FENCE-ROBUST": lambda e: ck.check_fence_robust(
        e.rich.classes, e.rich.robust, e.rich.params
    ),
    "HOOKCHECK": lambda e: ck.check_hookcheck(
        e.rich.transcript, e.rich.context, hook_total(e.rich)
    ),
    "MAPCHECK": lambda e: ck.check_mapcheck(e.rich.classes),
    "DENOMCHECK": lambda e: ck.check_denomcheck(good_profile(e.rich)),
}

BAD = {
    "SUMCHECK": lambda e: ck.check_sumcheck(
        e.rich.cycles, e.rich.with_class_s(CONTROLLER=5.0)
    ),
    "CLASSORACLE": lambda e: ck.check_classoracle(
        e.rich.context, e.rich.with_class_s(CONTROLLER=-5.0, HOOK=5.0)
    ),
    "STAGECHECK": lambda e: ck.check_stagecheck(
        e.rich.cycles,
        [
            e.rich.cuts[0]._replace(
                rows=[
                    e.rich.cuts[0]
                    .rows[0]
                    ._replace(
                        classes_s=dict(
                            e.rich.cuts[0].rows[0].classes_s,
                            CONTROLLER=e.rich.cuts[0].rows[0].classes_s["CONTROLLER"]
                            + 5.0,
                        )
                    )
                ]
                + list(e.rich.cuts[0].rows[1:])
            )
        ]
        + list(e.rich.cuts[1:]),
    ),
    "PAIRCHECK": lambda e: ck.check_paircheck(
        e.rich.cycles._replace(
            pairs=(
                e.rich.cycles.pairs[0]._replace(
                    terminal_ms=e.rich.cycles.pairs[0].terminal_ms + 1000
                ),
            )
            + tuple(e.rich.cycles.pairs[1:])
        ),
        e.rich.transcript,
    ),
    "SPLITCHECK": lambda e: ck.check_splitcheck(
        e.rich.classes, edit_split(e.rich.split, SUBAGENT_WORK=5.0)
    ),
    "INTERVALCHECK": lambda e: ck.check_intervalcheck(
        shifted_first_subagent(e.rich.context)
    ),
    "ARMCHECK": lambda e: ck.check_armcheck(
        edit_split(e.rich.split, SUBAGENT_GATE=5.0)
    ),
    "POOLCHECK": lambda e: ck.check_poolcheck(
        e.rich.pool._replace(
            ranking=[
                dict(e.rich.pool.ranking[0], s=e.rich.pool.ranking[0]["s"] + 600.0)
            ]
            + list(e.rich.pool.ranking[1:])
        ),
        e.rich.buckets,
    ),
    "HALFCHECK": lambda e: ck.check_halfcheck(
        edit_halves(e.rich.pool, active__work_pct=5.0)
    ),
    "IDLECHECK": lambda e: ck.check_idlecheck(
        e.rich.context._replace(gaps_s=e.rich.context.gaps_s[:-1]), e.rich.transcript
    ),
    "FENCECHECK": lambda e: ck.check_fencecheck(
        e.rich.classes._replace(
            fence=e.rich.classes.fence._replace(
                inner_s=0.5 * 10**e.rich.classes.fence.q3
            )
        ),
        e.rich.params,
    ),
    "FENCE-ROBUST": lambda e: ck.check_fence_robust(
        e.rich.classes,
        e.rich.robust._replace(min_s=e.rich.robust.min_s * 0.5),
        e.rich.params,
    ),
    "HOOKCHECK": lambda e: ck.check_hookcheck(
        e.rich.transcript,
        e.rich.context,
        (hook_total(e.rich)[0], hook_total(e.rich)[1] + 5.0),
    ),
    "MAPCHECK": lambda e: ck.check_mapcheck(
        e.rich.classes._replace(
            dispatches=[
                e.rich.classes.dispatches[0]._replace(task_id="other00000000001")
            ]
            + list(e.rich.classes.dispatches[1:])
        )
    ),
    "DENOMCHECK": lambda e: ck.check_denomcheck(bad_profile()),
}

ABSENT = {
    "SUMCHECK": lambda e: ck.check_sumcheck(e.open.cycles, e.open.classes),
    "CLASSORACLE": lambda e: ck.check_classoracle(e.open.context, e.open.classes),
    "STAGECHECK": lambda e: ck.check_stagecheck(e.open.cycles, e.open.cuts),
    "PAIRCHECK": lambda e: ck.check_paircheck(e.open.cycles, e.open.transcript),
    "SPLITCHECK": lambda e: ck.check_splitcheck(e.rich.classes, None),
    "INTERVALCHECK": lambda e: ck.check_intervalcheck(e.open.context),
    "ARMCHECK": lambda e: ck.check_armcheck(None),
    "POOLCHECK": lambda e: ck.check_poolcheck(e.open.pool, e.open.buckets),
    "HALFCHECK": lambda e: ck.check_halfcheck(e.open.pool),
    "IDLECHECK": lambda e: ck.check_idlecheck(e.k1.context, e.k1.transcript),
    "FENCECHECK": lambda e: ck.check_fencecheck(e.k1.classes, e.k1.params),
    "FENCE-ROBUST": lambda e: ck.check_fence_robust(
        e.k1.classes, e.k1.robust, e.k1.params
    ),
    "HOOKCHECK": lambda e: ck.check_hookcheck(e.open.transcript, e.open.context),
    "MAPCHECK": lambda e: ck.check_mapcheck(e.open.classes),
    "DENOMCHECK": lambda e: ck.check_denomcheck({}),
}

NAMES = discovered()


# ---------------------------------------------------------------------------------------------
# t39: the checks
# ---------------------------------------------------------------------------------------------


def test_t39_floor_of_fifteen_check_names_and_the_declared_registry():
    assert ck.CHECK_FLOOR == CHECK_FLOOR
    assert len(NAMES) >= CHECK_FLOOR
    assert sorted(ck.CHECK_NAMES) == NAMES
    assert len(ck.CHECK_NAMES) == len(set(ck.CHECK_NAMES))
    for table in (GOOD, BAD, ABSENT):
        assert sorted(table) == NAMES


@pytest.mark.parametrize("name", NAMES)
def test_t39_correct_input_passes(env, name):
    result = GOOD[name](env)
    row = row_of(result)
    assert row.name == name
    assert row.verdict == "pass", row
    assert DETAIL_RE.match(row.detail), row.detail


@pytest.mark.parametrize("name", NAMES)
def test_t39_corrupted_input_fails(env, name):
    row = row_of(BAD[name](env))
    assert row.name == name
    assert row.verdict == "fail", row
    assert DETAIL_RE.match(row.detail), row.detail


@pytest.mark.parametrize("name", NAMES)
def test_t39_absent_second_side_skips_and_never_passes(env, name):
    row = row_of(ABSENT[name](env))
    assert row.name == name
    assert row.verdict == "skip", row
    assert row.verdict in m.CHECK_VERDICTS
    assert DETAIL_RE.match(row.detail), row.detail


def test_t39_poolcheck_returns_its_family_with_the_final_row_last(env):
    rows = ck.check_poolcheck(env.rich.pool, env.rich.buckets)
    assert isinstance(rows, list) and len(rows) >= 5
    assert [x.name for x in rows][-1] == "POOLCHECK"
    assert {x.name for x in rows[:-1]} >= {
        "POOLCHECK-ARITH",
        "POOLCHECK-STAGE-BUCKET",
        "POOLCHECK-CORNER-RANK",
        "POOLCHECK-REPEAT",
        "POOLCHECK-CLASS-TOTALS",
    }
    assert all(x.verdict in m.CHECK_VERDICTS for x in rows)


def test_t39_poolcheck_class_totals_row_fails_alone_on_a_class_total_drift(env):
    pool = env.rich.pool
    classes = dict(pool.classes_s, CONTROLLER=pool.classes_s["CONTROLLER"] + 600.0)
    rows = ck.check_poolcheck(pool._replace(classes_s=classes), env.rich.buckets)
    by_name = {x.name: x.verdict for x in rows}
    assert by_name["POOLCHECK-CLASS-TOTALS"] == "fail"
    assert by_name["POOLCHECK-ARITH"] == "pass"
    assert by_name["POOLCHECK"] == "fail"


def test_t39_every_checks_verdict_row_detail_is_lowercase_tokens_and_integers(env):
    """Covers the verdict row of each check (a family's LAST row), not a family's sub-rows."""
    for name in NAMES:
        for table in (GOOD, BAD, ABSENT):
            row = row_of(table[name](env))
            assert DETAIL_RE.match(row.detail), (name, row.detail)
            assert "." not in row.detail, (name, row.detail)


def test_t39_poolcheck_sub_rows_pass_the_pool_modules_details_through_and_say_so(env):
    rows = ck.check_poolcheck(env.rich.pool, env.rich.buckets)
    passed = [x for x in rows[:-1] if x.name != "POOLCHECK-CLASS-TOTALS"]
    assert {x.name for x in passed} >= {
        "POOLCHECK-ARITH",
        "POOLCHECK-STAGE-BUCKET",
        "POOLCHECK-CORNER-RANK",
        "POOLCHECK-REPEAT",
    }
    assert any("." in x.detail or "True" in x.detail for x in passed)
    for word in ("POOLCHECK-ARITH", "floats", "booleans"):
        assert word in ck.__doc__


# ---- SUMCHECK, CLASSORACLE, STAGECHECK ---------------------------------------------------


def test_t39_sumcheck_compares_with_the_roster_window_not_the_class_sum(env):
    shifted = env.rich.with_class_s(CONTROLLER=5.0)
    assert verdict_of(ck.check_sumcheck(env.rich.cycles, shifted)) == "fail"
    below = env.rich.with_class_s(AWAY=-61.0, CONTROLLER=61.0)
    assert verdict_of(ck.check_sumcheck(env.rich.cycles, below)) == "fail"


def test_t39_sumcheck_a_swap_between_classes_keeps_the_sum_and_passes(env):
    swapped = env.rich.with_class_s(CONTROLLER=-5.0, HOOK=5.0)
    assert verdict_of(ck.check_sumcheck(env.rich.cycles, swapped)) == "pass"
    assert verdict_of(ck.check_classoracle(env.rich.context, swapped)) == "fail"


def test_t39_sumcheck_fails_when_the_cycle_counts_differ(env):
    short = env.rich.classes._replace(cycles=[])
    assert verdict_of(ck.check_sumcheck(env.rich.cycles, short)) == "fail"


def test_t39_stagecheck_fails_when_a_stage_wall_leaves_the_window(env):
    cut = env.rich.cuts[0]
    rows = list(cut.rows)
    rows[2] = rows[2]._replace(wall_s=rows[2].wall_s + 5.0)
    bad = [cut._replace(rows=rows)]
    assert verdict_of(ck.check_stagecheck(env.rich.cycles, bad)) == "fail"


# ---- PAIRCHECK ----------------------------------------------------------------------------


def unresolved_events():
    return [
        h.wrapper(0, "feature"),
        h.say(5),
        h.runlong_launch(10, "l1", "$ART", "x", suite=False),
        h.result(11, "l1"),
        h.runlong_wait(20, "w1", "artX"),
        h.result(21, "w1"),
        h.enqueue(60, "w1", "t9"),
    ]


def with_terminals(chain, terminals):
    """The chain's cycle set with each pair's terminal replaced (hand-built main pairing).

    The rows that depend on the ORIGINAL's whole pairing rule drive PAIRCHECK with the main
    pairing written out here, so they do not rest on what the cycles module does today.
    """
    assert len(chain.cycles.pairs) == len(terminals)
    pairs = tuple(
        p._replace(terminal_ms=terminals[i]) for i, p in enumerate(chain.cycles.pairs)
    )
    return chain.cycles._replace(pairs=pairs)


def test_t39_paircheck_an_unresolved_key_paired_by_the_nearest_arm_agrees(tmp_path):
    chain = Chain(tmp_path, unresolved_events())
    cycles = with_terminals(chain, [ms(60)])
    row = ck.check_paircheck(cycles, chain.transcript)
    assert row.verdict == "pass"
    assert "agree=1 disagree=0 nearest=1" in row.detail


def test_t39_paircheck_a_wrong_main_terminal_on_an_unresolved_key_fails(tmp_path):
    chain = Chain(tmp_path, unresolved_events())
    for wrong in (None, ms(61)):
        row = ck.check_paircheck(with_terminals(chain, [wrong]), chain.transcript)
        assert row.verdict == "fail", wrong
        assert "disagree=1" in row.detail


def test_t39_paircheck_a_wrong_main_terminal_on_an_unresolved_key_fails_via_key_arm(
    tmp_path,
):
    ev = [h.wrapper(0, "feature"), h.say(5)]
    ev += [h.runlong_launch(10, "l1", "$ART", "x", suite=False)]
    ev += [h.runlong_wait(20, "w1", "$ART"), h.enqueue(60, "w1", "t1")]
    chain = Chain(tmp_path, ev)
    good = ck.check_paircheck(with_terminals(chain, [ms(60)]), chain.transcript)
    assert good.verdict == "pass" and "nearest=0" in good.detail
    bad = ck.check_paircheck(with_terminals(chain, [ms(61)]), chain.transcript)
    assert bad.verdict == "fail"


def test_t39_paircheck_fails_on_a_resolved_key_the_oracle_pairs_differently(env):
    pairs = list(env.rich.cycles.pairs)
    pairs[0] = pairs[0]._replace(terminal_ms=None, method="UNPAIRED")
    bad = env.rich.cycles._replace(pairs=tuple(pairs))
    row = ck.check_paircheck(bad, env.rich.transcript)
    assert row.verdict == "fail"
    assert "disagree=1" in row.detail


def reused_key_events(starts, wait_at, arrive_at):
    """Launches of ONE artifact key at `starts`, then one background wait on that key."""
    ev = [h.wrapper(0, "feature"), h.say(5)]
    for index, at in enumerate(starts):
        ev.append(h.runlong_launch(at, "l%d" % index, "artK", "x", suite=False))
    ev += [h.runlong_wait(wait_at, "w1", "artK"), h.enqueue(arrive_at, "w1", "t1")]
    return ev


def oracle_of(chain):
    return ck.pairing_oracle(ck._main_events(chain.transcript))


def test_t39_paircheck_a_relaunched_key_ends_both_launches_at_the_one_wait(tmp_path):
    chain = Chain(tmp_path, reused_key_events([10, 28], 69, 100))
    assert [(p.terminal_ms, p.via) for p in oracle_of(chain)] == [
        (ms(100), "nearest"),
        (ms(100), "key"),
    ]
    row = ck.check_paircheck(
        with_terminals(chain, [ms(100), ms(100)]), chain.transcript
    )
    assert row.verdict == "pass"
    assert "agree=2 disagree=0 nearest=1" in row.detail


def test_t39_paircheck_a_relaunched_key_whose_first_launch_stays_unpaired_fails(
    tmp_path,
):
    chain = Chain(tmp_path, reused_key_events([10, 28], 69, 100))
    row = ck.check_paircheck(with_terminals(chain, [None, ms(100)]), chain.transcript)
    assert row.verdict == "fail"
    assert "disagree=1" in row.detail


def test_t39_paircheck_the_key_arm_stops_at_the_next_launch_of_the_same_key(tmp_path):
    chain = Chain(tmp_path, reused_key_events([10, 20], 400, 450))
    assert [p.terminal_ms for p in oracle_of(chain)] == [None, ms(450)]
    row = ck.check_paircheck(with_terminals(chain, [None, ms(450)]), chain.transcript)
    assert row.verdict == "pass"
    assert (
        ck.check_paircheck(
            with_terminals(chain, [ms(450), ms(450)]), chain.transcript
        ).verdict
        == "fail"
    )


def test_t39_paircheck_a_key_reused_after_the_first_terminal_still_pairs(tmp_path):
    ev = reused_key_events([10], 20, 60)
    ev += [h.runlong_launch(100, "l9", "artK", "x", suite=False)]
    ev += [h.runlong_wait(110, "w9", "artK"), h.enqueue(150, "w9", "t9")]
    chain = Chain(tmp_path, ev)
    assert [p.terminal_ms for p in oracle_of(chain)] == [ms(60), ms(150)]
    row = ck.check_paircheck(with_terminals(chain, [ms(60), ms(150)]), chain.transcript)
    assert row.verdict == "pass"
    assert "agree=2 disagree=0" in row.detail


def test_t39_paircheck_three_launches_of_one_key_only_the_last_pairs(tmp_path):
    chain = Chain(tmp_path, reused_key_events([10, 27, 2710], 2720, 2800))
    oracle = ck.pairing_oracle(ck._main_events(chain.transcript))
    assert [p.terminal_ms for p in oracle] == [None, None, ms(2800)]
    cycles = with_terminals(chain, [None, None, ms(2800)])
    row = ck.check_paircheck(cycles, chain.transcript)
    assert row.verdict == "pass"
    assert "agree=3 disagree=0" in row.detail


def test_t39_paircheck_fails_when_the_launch_counts_differ(env):
    bad = env.rich.cycles._replace(pairs=tuple(env.rich.cycles.pairs[:-1]))
    assert ck.check_paircheck(bad, env.rich.transcript).verdict == "fail"


def test_t39_paircheck_reads_the_oracle_not_the_main_pairing_for_the_agreement(env):
    row = ck.check_paircheck(env.rich.cycles, env.rich.transcript)
    assert row.verdict == "pass"
    assert "launches=2" in row.detail and "agree=2" in row.detail


@pytest.mark.parametrize("part", dl.PART_KEYS)
def test_t39_splitcheck_each_of_the_four_parts_counts_towards_subagent(env, part):
    bad = edit_split(env.rich.split, **{part: 5.0})
    assert ck.check_splitcheck(env.rich.classes, bad).verdict == "fail"


# ---- SPLITCHECK, INTERVALCHECK, ARMCHECK, MAPCHECK ---------------------------------------


def test_t39_armcheck_two_identities_fail_separately(env):
    run = edit_split(env.rich.split, SUBAGENT_GATE=5.0)
    assert verdict_of(ck.check_armcheck(run)) == "fail"
    tokens = edit_split(env.rich.split, GATE_LONGCALL=5.0, SUBAGENT_GATE=5.0)
    assert verdict_of(ck.check_armcheck(tokens)) == "fail"
    consistent = edit_split(
        env.rich.split, GATE_LONGCALL=5.0, SUBAGENT_GATE=5.0, LONGCALL_CHECK=5.0
    )
    assert verdict_of(ck.check_armcheck(consistent)) == "pass"


def test_t39_armcheck_reads_the_per_role_rows_too(env):
    by_role = {n: dict(row) for n, row in env.rich.split.by_role.items()}
    first = next(iter(by_role))
    by_role[first]["SUBAGENT_GATE"] += 5.0
    bad = env.rich.split._replace(by_role=by_role)
    assert verdict_of(ck.check_armcheck(bad)) == "fail"


def test_t39_intervalcheck_proves_the_clip_with_a_dispatch_ending_after_the_window(
    tmp_path,
):
    ev = align_ids(h.k1_events("skill"))
    ev += [h.agent(2300, "ax", "sonnet", "task " + h.lorem(3))]
    ev += [h.ack(2301, "ax", "aid00000000000009"), h.enqueue(3000, "ax", "t9", 700000)]
    ev.sort(key=lambda e: e["at"])
    chain = Chain(tmp_path, ev)
    row = ck.check_intervalcheck(chain.context)
    assert row.verdict == "pass"
    unclipped = chain.context.subagent[-1]
    assert unclipped[1] > chain.context.windows[0].end_t_ms
    spans = dl.window_spans(
        chain.context.dispatches,
        [(chain.context.windows[0].start_t_ms, chain.context.windows[0].end_t_ms)],
    )[0]
    assert max(s.b for s in spans) == chain.context.windows[0].end_t_ms


def test_t39_mapcheck_counts_both_sources_and_recounts_them_itself(env):
    row = ck.check_mapcheck(env.rich.classes)
    assert row.verdict == "pass"
    assert "both=2" in row.detail and "agree=2" in row.detail


def test_t39_mapcheck_a_primary_that_reports_agreement_is_recounted(env, monkeypatch):
    first = env.rich.classes.dispatches[0]._replace(task_id="other00000000001")
    rest = list(env.rich.classes.dispatches[1:])
    bad = env.rich.classes._replace(dispatches=[first] + rest)
    monkeypatch.setattr(
        ck, "map_check", lambda ds: dl.MapCheck(len(ds), len(ds), len(ds), len(ds))
    )
    assert ck.check_mapcheck(bad).verdict == "fail"


def test_t39_mapcheck_a_dispatch_with_one_source_does_not_count_as_both(env):
    lone = env.rich.classes.dispatches[0]._replace(task_id=None)
    rest = list(env.rich.classes.dispatches[1:])
    row = ck.check_mapcheck(env.rich.classes._replace(dispatches=[lone] + rest))
    assert row.verdict == "pass"
    assert "both=1" in row.detail


def test_t39_mapcheck_with_no_dispatch_holding_both_ids_skips(env):
    lone = [d._replace(task_id=None) for d in env.rich.classes.dispatches]
    row = ck.check_mapcheck(env.rich.classes._replace(dispatches=lone))
    assert row.verdict == "skip"


# ---- HALFCHECK, DENOMCHECK ----------------------------------------------------------------


def test_t39_halfcheck_all_against_active_fails_when_only_the_all_block_drifts(env):
    bad = edit_halves(env.rich.pool, all__gate_pct=5.0)
    assert verdict_of(ck.check_halfcheck(bad)) == "fail"


def test_t39_halfcheck_the_shares_must_sum_to_one_hundred(env):
    both = edit_halves(env.rich.pool, active__work_pct=5.0, all__work_pct=-5.0)
    assert verdict_of(ck.check_halfcheck(both)) == "fail"


def test_t39_denomcheck_a_number_for_a_zero_denominator_fails_and_none_passes(env):
    ok = {"a": {"denominator_s": 0.0, "work_pct": None, "gate_pct": None}}
    assert verdict_of(ck.check_denomcheck(ok)) == "pass"
    bad = {"a": {"denominator_s": 0.0, "work_pct": 0.0, "gate_pct": None}}
    assert verdict_of(ck.check_denomcheck(bad)) == "fail"


def test_t39_denomcheck_a_zero_active_denominator_covers_the_hook_work_variant():
    halves = {
        "active": {"denominator_s": 0.0, "work_pct": None},
        "active_hook_work": {"work_pct": 12.0, "gate_pct": None},
    }
    assert verdict_of(ck.check_denomcheck({"halves": halves})) == "fail"


def test_t39_denomcheck_a_non_finite_number_fails():
    block = {"denominator_s": 10.0, "work_pct": float("nan")}
    assert verdict_of(ck.check_denomcheck({"a": block})) == "fail"


def test_t39_denomcheck_the_rendered_text_is_scanned_when_given(env):
    profile = good_profile(env.rich)
    assert ck.check_denomcheck(profile, "work 12.5 percent of 80").verdict == "pass"
    assert ck.check_denomcheck(profile, "work nan percent").verdict == "fail"
    assert ck.check_denomcheck(profile, "gate -inf").verdict == "fail"


# ---- IDLECHECK, FENCECHECK, FENCE-ROBUST -------------------------------------------------


def test_t39_idlecheck_the_quartiles_are_compared_with_the_fence(env):
    fence = env.rich.context.fence
    moved = env.rich.context._replace(fence=fence._replace(q3=fence.q3 + 0.5))
    row = ck.check_idlecheck(moved, env.rich.transcript)
    assert row.verdict == "fail"


def test_t39_idlecheck_an_extra_gap_in_the_list_fails(env):
    longer = list(env.rich.context.gaps_s) + [100.0]
    row = ck.check_idlecheck(
        env.rich.context._replace(gaps_s=longer), env.rich.transcript
    )
    assert row.verdict == "fail"


def test_t39_idlecheck_skips_below_a_raised_floor_and_passes_at_a_lower_one(tmp_path):
    high = Chain(tmp_path / "hi", rich_events(), away_min_gaps=10)
    assert ck.check_idlecheck(high.context, high.transcript).verdict == "skip"
    low = Chain(tmp_path / "lo", rich_events(), away_min_gaps=3)
    assert ck.check_idlecheck(low.context, low.transcript).verdict == "pass"


def test_t39_fencecheck_follows_a_non_default_multiplier_and_method(tmp_path):
    wide = Chain(tmp_path / "k3", rich_events(), away_fence_k=3.0)
    assert wide.classes.fence.k == 3.0
    assert ck.check_fencecheck(wide.classes, wide.params).verdict == "pass"
    stale = ck.check_fencecheck(wide.classes, m.Params(min_cycles=1, away_fence_k=1.5))
    assert stale.verdict == "fail"
    rich = Chain(tmp_path / "ex", rich_events())
    exclusive = k.classify(
        rich.cycles, rich.transcript, rich.params, fence_method="exclusive"
    )
    assert exclusive.fence.method == "exclusive"
    assert ck.check_fencecheck(exclusive, rich.params).verdict == "pass"


def test_t39_fencecheck_both_fences_must_exceed_q3(env):
    fence = env.rich.classes.fence
    low_outer = fence._replace(outer_s=0.5 * 10**fence.q3)
    bad = env.rich.classes._replace(fence=low_outer)
    assert ck.check_fencecheck(bad, env.rich.params).verdict == "fail"


def test_t39_fencecheck_fails_when_a_fence_was_fitted_below_the_floor(tmp_path):
    chain = Chain(tmp_path, rich_events(), away_min_gaps=10)
    assert chain.classes.fence.inner_s is None
    row = ck.check_fencecheck(chain.classes, chain.params)
    assert row.verdict == "skip"
    fitted = k.away_fence(list(chain.classes.idle_gaps_s), 1.5, "inclusive", 2)
    forced = chain.classes._replace(fence=fitted)
    assert ck.check_fencecheck(forced, chain.params).verdict == "fail"


def test_t39_fencecheck_a_fence_below_q3_fails_even_when_it_matches_the_recompute(
    tmp_path,
):
    chain = Chain(tmp_path, rich_events(), away_fence_k=-1.0)
    assert chain.classes.fence.inner_s < 10**chain.classes.fence.q3
    row = ck.check_fencecheck(chain.classes, chain.params)
    assert row.verdict == "fail"
    assert row.detail == "gaps=9 bad=1"


def test_t39_fence_robust_follows_a_non_default_multiplier(tmp_path):
    chain = Chain(tmp_path, rich_events(), away_fence_k=3.0)
    good = ck.check_fence_robust(chain.classes, chain.robust, chain.params)
    assert good.verdict == "pass"
    stale = ck.check_fence_robust(
        chain.classes, chain.robust, m.Params(min_cycles=1, away_fence_k=1.5)
    )
    assert stale.verdict == "fail"


def test_t39_fence_robust_an_unfitted_robust_result_over_enough_gaps_fails(env):
    empty = env.rich.robust._replace(min_s=None, max_s=None)
    row = ck.check_fence_robust(env.rich.classes, empty, env.rich.params)
    assert row.verdict == "fail"


def test_t39_fence_robust_the_gap_count_must_match(env):
    robust = env.rich.robust._replace(n=env.rich.robust.n + 1)
    row = ck.check_fence_robust(env.rich.classes, robust, env.rich.params)
    assert row.verdict == "fail"


# ---- HOOKCHECK ----------------------------------------------------------------------------


def test_t39_hookcheck_the_union_must_be_less_than_the_sum_when_calls_overlap(env):
    calls, union_s = hook_total(env.rich)
    plain = ck.plain_scan(r.sorted_events(env.rich.transcript))
    slow = [(a, b) for a, b, _ in plain.calls if b - a > 10 * MS]
    total_s = sum(b - a for a, b in slow) / MS
    assert union_s < total_s
    row = ck.check_hookcheck(env.rich.transcript, env.rich.context, (calls, total_s))
    assert row.verdict == "fail"


def test_t39_hookcheck_the_class_set_must_equal_the_filtered_edit_set(env):
    bad = env.rich.context._replace(edits=env.rich.context.edits[1:])
    row = ck.check_hookcheck(env.rich.transcript, bad, hook_total(env.rich))
    assert row.verdict == "fail"


def test_t39_hookcheck_hook_reported_must_equal_the_second_scans_re_sum(env):
    events = [
        e
        for e in env.rich.context.events
        if not any(t.kind == "hook_success" for t in e.toks)
    ]
    bad = env.rich.context._replace(events=events)
    row = ck.check_hookcheck(env.rich.transcript, bad, hook_total(env.rich))
    assert row.verdict == "fail"


def test_t39_hookcheck_follows_a_non_default_threshold(tmp_path):
    chain = Chain(tmp_path, rich_events(), hook_latency_s=5)
    calls, _ = hook_total(chain)
    assert calls == 5
    row = ck.check_hookcheck(chain.transcript, chain.context)
    assert row.verdict == "pass"
    stale = ck.check_hookcheck(
        chain.transcript, chain.context, k.hook_union_total(chain.transcript, 10000)
    )
    assert stale.verdict == "fail"


def test_t39_hookcheck_computes_the_primary_figure_itself_when_none_is_given(env):
    row = ck.check_hookcheck(env.rich.transcript, env.rich.context)
    assert row.verdict == "pass"


def test_t39_hookcheck_a_sum_for_a_union_is_caught_by_the_overlap_invariant_alone(
    tmp_path,
):
    ev = [h.wrapper(0, "feature"), h.say(5)]
    ev += [h.edit(100, "x1"), h.result(130, "x1")]
    ev += [h.edit(129.999, "x2"), h.result(150, "x2")]
    chain = Chain(tmp_path, ev)
    calls, union_s = hook_total(chain)
    assert (calls, union_s) == (2, 50.0)
    assert ck.check_hookcheck(chain.transcript, chain.context).verdict == "pass"
    summed = ck.check_hookcheck(chain.transcript, chain.context, (2, 50.001))
    assert summed.verdict == "fail"
    assert "overlaps=1" in summed.detail


def test_t39_hookcheck_a_hook_success_moved_out_of_its_window_fails_per_window(env):
    window = env.rich.context.windows[0]
    events = [
        e._replace(t_ms=window.end_t_ms + 5000)
        if any(t.kind == "hook_success" for t in e.toks) and e.t_ms == ms(1561)
        else e
        for e in env.rich.context.events
    ]
    assert events != list(env.rich.context.events)
    bad = env.rich.context._replace(events=events)
    row = ck.check_hookcheck(env.rich.transcript, bad, hook_total(env.rich))
    assert row.verdict == "fail"
    assert row.detail.endswith("bad=1")


def test_t39_hookcheck_a_hook_success_outside_every_window_is_still_re_summed(env):
    events = [
        e
        for e in env.rich.context.events
        if not (
            e.t_ms > env.rich.context.windows[0].end_t_ms
            and any(t.kind == "hook_success" for t in e.toks)
        )
    ]
    assert len(events) == len(env.rich.context.events) - 1
    bad = env.rich.context._replace(events=events)
    row = ck.check_hookcheck(env.rich.transcript, bad, hook_total(env.rich))
    assert row.verdict == "fail"


def test_t39_dominant_hook_call_is_a_computed_line_with_no_verdict(env):
    result = ck.dominant_hook_call(env.rich.transcript, 10)
    assert result.status == "measured"
    assert result.calls == 3
    assert result.largest_ms == 30 * MS
    assert result.union_ms == 45 * MS
    assert not isinstance(result, m.CheckRow)
    assert result.share_pct == pytest.approx(100.0 * 30 / 45)
    none = ck.dominant_hook_call(env.open.transcript, 10)
    assert none.status == "none" and none.share_pct is None and none.calls == 0


# ---- A None primary input, DENOMCHECK on bad denominators, HOOKCHECK disagreement ----------

ARGS = {
    "SUMCHECK": lambda e: (e.rich.cycles, e.rich.classes),
    "CLASSORACLE": lambda e: (e.rich.context, e.rich.classes),
    "STAGECHECK": lambda e: (e.rich.cycles, e.rich.cuts),
    "PAIRCHECK": lambda e: (e.rich.cycles, e.rich.transcript),
    "SPLITCHECK": lambda e: (e.rich.classes, e.rich.split),
    "INTERVALCHECK": lambda e: (e.rich.context,),
    "ARMCHECK": lambda e: (e.rich.split,),
    "POOLCHECK": lambda e: (e.rich.pool,),
    "HALFCHECK": lambda e: (e.rich.pool,),
    "IDLECHECK": lambda e: (e.rich.context, e.rich.transcript),
    "FENCECHECK": lambda e: (e.rich.classes, e.rich.params),
    "FENCE-ROBUST": lambda e: (e.rich.classes, e.rich.robust, e.rich.params),
    "HOOKCHECK": lambda e: (e.rich.transcript, e.rich.context),
    "MAPCHECK": lambda e: (e.rich.classes,),
    "DENOMCHECK": lambda e: (good_profile(e.rich),),
}


def none_cases():
    sizes = {
        "SUMCHECK": 2,
        "CLASSORACLE": 2,
        "STAGECHECK": 2,
        "PAIRCHECK": 2,
        "SPLITCHECK": 2,
        "INTERVALCHECK": 1,
        "ARMCHECK": 1,
        "POOLCHECK": 1,
        "HALFCHECK": 1,
        "IDLECHECK": 2,
        "FENCECHECK": 2,
        "FENCE-ROBUST": 3,
        "HOOKCHECK": 2,
        "MAPCHECK": 1,
        "DENOMCHECK": 1,
    }
    return [(name, i) for name in NAMES for i in range(sizes[name])]


def check_function(name):
    return getattr(ck, "check_" + name.lower().replace("-", "_"))


@pytest.mark.parametrize("name,index", none_cases())
def test_t39_a_none_primary_input_skips_with_a_closed_reason_and_never_raises(
    env, name, index
):
    args = list(ARGS[name](env))
    args[index] = None
    row = row_of(check_function(name)(*args))
    assert row.name == name
    assert row.verdict == "skip", row
    assert DETAIL_RE.match(row.detail), row.detail


@pytest.mark.parametrize("bad", [None, "ten", [1], float("nan")])
def test_t39_denomcheck_a_non_numeric_denominator_fails_without_raising(bad):
    block = {"denominator_s": bad, "work_pct": None}
    row = ck.check_denomcheck({"a": block})
    assert row.verdict == "fail"


def test_t39_denomcheck_the_active_variant_beside_a_non_numeric_denominator_fails():
    halves = {"active": {"denominator_s": None}, "active_hook_work": {"work_pct": 1.0}}
    assert ck.check_denomcheck({"h": halves}).verdict == "fail"


def test_t39_hookcheck_a_primary_that_claims_calls_the_scan_never_saw_fails(env):
    row = ck.check_hookcheck(env.open.transcript, env.open.context, (3, 99.0))
    assert row.verdict == "fail"
    assert "calls=0" in row.detail


def test_t39_hookcheck_zero_on_both_sides_with_no_edit_call_still_skips(env):
    row = ck.check_hookcheck(env.open.transcript, env.open.context, (0, 0.0))
    assert row.verdict == "skip"
    assert ck.check_hookcheck(env.open.transcript, env.open.context).verdict == "skip"


def multiedit_chain(tmp_path):
    ev = [h.wrapper(0, "feature"), h.say(5)]
    ev += [h.edit(100, "m1", name="MultiEdit"), h.result(130, "m1")]
    return Chain(tmp_path, ev)


def test_t39_hookcheck_a_slow_multiedit_is_a_hook_call(tmp_path):
    chain = multiedit_chain(tmp_path)
    assert hook_total(chain) == (1, 30.0)
    row = ck.check_hookcheck(chain.transcript, chain.context)
    assert row.verdict == "pass"
    assert "calls=1" in row.detail
    assert ck.dominant_hook_call(chain.transcript, 10).calls == 1


def test_t39_halfcheck_the_active_shares_must_sum_to_one_hundred_on_their_own(env):
    pool = env.rich.pool
    ratio = pool.wall_active_s / pool.wall_all_s
    halves = copy.deepcopy(pool.halves)
    halves["active"]["work_pct"] += 5.0
    halves["all"]["work_pct"] += 5.0 * ratio
    halves["all"]["away_pct"] -= 5.0 * ratio
    row = ck.check_halfcheck(pool._replace(halves=halves))
    assert row.verdict == "fail"
    assert row.detail == "bad=1"


def test_t39_idlecheck_a_skip_below_the_floor_reports_both_counts(env):
    short = env.k1.context._replace(gaps_s=[1.0, 2.0, 3.0])
    row = ck.check_idlecheck(short, env.k1.transcript)
    assert row.verdict == "skip"
    assert "primary=3 scan=1" in row.detail


# ---- The independent second sides ---------------------------------------------------------


def test_t39_brute_force_classes_known_answer_with_half_second_boundaries():
    intervals = {
        "MACHINE_WAIT": [(2000, 4500, "x")],
        "SUBAGENT": [(1000, 3000, "a")],
        "HOOK": [(4000, 6500, "e")],
        "OPERATOR_RESPONSE": [(6000, 9000, "z")],
        "AWAY": [(8000, 9500, "z")],
    }
    got = ck.brute_force_classes(intervals, 0, 10000)
    assert got == {
        "CONTROLLER": 1.5,
        "SUBAGENT": 1.0,
        "MACHINE_WAIT": 2.5,
        "HOOK": 2.0,
        "OPERATOR_RESPONSE": 2.5,
        "AWAY": 0.5,
    }


def test_t39_brute_force_classes_clips_to_the_window_and_covers_every_millisecond():
    intervals = {"HOOK": [(-500, 700), (1900, 5000)], "AWAY": [(800, 1000)]}
    got = ck.brute_force_classes(intervals, 0, 3000)
    assert got["HOOK"] == pytest.approx(0.7 + 1.1)
    assert got["AWAY"] == pytest.approx(0.2)
    assert sum(got.values()) == pytest.approx(3.0)


def test_t39_brute_force_classes_agrees_with_the_helper_oracle_on_whole_seconds():
    intervals = {
        "MACHINE_WAIT": [(3000, 8000)],
        "SUBAGENT": [(1000, 5000), (9000, 11000)],
        "HOOK": [(7000, 10000)],
        "OPERATOR_RESPONSE": [(0, 2000)],
        "AWAY": [(12000, 14000)],
    }
    mine = ck.brute_force_classes(intervals, 0, 15000)
    seconds = {
        n: [(a / 1000, b / 1000) for a, b, *_ in v] for n, v in intervals.items()
    }
    theirs = h.brute_force_classes(seconds, 0, 15)
    assert mine == {name: float(v) for name, v in theirs.items()}


def test_t39_plain_scan_known_answer_filters_sidechain_and_unfinished_calls():
    ev = [
        h.edit(10, "e1"),
        h.result(25, "e1"),
        h.edit(30, "e2", name="Write"),
        h.result(32, "e2"),
        h.hook(26, "e1", 700),
        h.hook(33, "e2", 300),
        h.edit(40, "e3"),
        h.edit(50, "e4", sidechain=True),
        h.result(60, "e4", sidechain=True),
        h.bash(70, "b1", "echo " + h.lorem(1)),
        h.result(80, "b1"),
    ]
    transcript = r.parse_bytes(h.build_transcript(ev))
    scan = ck.plain_scan(r.sorted_events(transcript))
    assert list(scan.calls) == [(ms(10), ms(25), "Edit"), (ms(30), ms(32), "Write")]
    assert list(scan.reported) == [(ms(26), 700), (ms(33), 300)]


def test_t39_idle_scan_known_answer_pure_remainder_interrupt_and_question():
    ev = [
        h.say(0),
        h.prompt(100),
        h.say(200),
        h.prompt(300),
        h.say(400),
        h.prompt(500),
        h.say(600),
        h.prompt(700),
        h.say(800),
        h.prompt(805, "[Request interrupted by user]"),
        h.prompt(900),
        h.say(1000),
        h.prompt(1010, origin="auto_continuation"),
        h._tool_use(1100, "q1", "AskUserQuestion", {"questions": []}),
        h.result(1130, "q1"),
    ]
    transcript = r.parse_bytes(h.build_transcript(ev))
    jobs = [(ms(210), ms(290)), (ms(390), ms(520)), (ms(600.4), ms(699.8))]
    got = ck.idle_scan(r.sorted_events(transcript), jobs)
    assert [(e.kind, e.seconds) for e in got] == [
        ("pure", 100.0),
        ("remainder", 20.0),
        ("pure", 100.0),
        ("pure", 10.0),
        ("pure", 30.0),
    ]
    assert got[1].start_ms == ms(200) and got[1].end_ms == ms(300)


def test_t39_pairing_oracle_known_answer_every_arm_and_an_unpaired_launch():
    ev = [
        h.runlong_launch(10, "l1", "artA", "x", suite=False),
        h.runlong_wait(20, "w1", "artA"),
        h.enqueue(60, "w1", "t1"),
        h.runlong_launch(100, "l2", "artB", "x", suite=False),
        h.runlong_wait(110, "w2", "artB", bg=False),
        h.result(150, "w2", "RESULT: PASS rc=0"),
        h.runlong_launch(200, "l3", "artC", "x", suite=False),
        h.runlong_wait(210, "w3", "artC", bg=False, mode="status"),
        h.result(220, "w3", "DONE"),
        h.runlong_launch(300, "l4", "artD", "x", suite=False),
        h.runlong_wait(310, "w4", "artD", bg=False),
        h.result(320, "w4", "still running " + h.lorem(2)),
    ]
    transcript = r.parse_bytes(h.build_transcript(ev))
    got = ck.pairing_oracle(r.sorted_events(transcript))
    assert [(p.launch_ms, p.terminal_ms) for p in got] == [
        (ms(10), ms(60)),
        (ms(100), ms(150)),
        (ms(200), ms(220)),
        (ms(300), None),
    ]
    assert all(p.resolved for p in got)


def oracle_over(events):
    return ck.pairing_oracle(r.sorted_events(r.parse_bytes(h.build_transcript(events))))


def test_t39_pairing_oracle_nearest_arm_takes_a_wait_for_an_unresolved_key_launch():
    got = oracle_over(unresolved_events())
    assert [(p.terminal_ms, p.resolved, p.via) for p in got] == [
        (ms(60), False, "nearest")
    ]


def test_t39_pairing_oracle_two_unpaired_launches_one_wait_only_the_first_takes_it():
    ev = [h.runlong_launch(10, "l1", "$A", "x", suite=False)]
    ev += [h.runlong_launch(20, "l2", "$B", "x", suite=False)]
    ev += [h.runlong_wait(30, "w1", "artX"), h.enqueue(60, "w1", "t1")]
    got = oracle_over(ev)
    assert [(p.terminal_ms, p.via) for p in got] == [
        (ms(60), "nearest"),
        (None, "none"),
    ]


def test_t39_pairing_oracle_pairs_an_unresolved_key_through_the_key_arm():
    ev = [h.runlong_launch(10, "l1", "$ART", "x", suite=False)]
    ev += [h.runlong_wait(20, "w1", "$ART"), h.enqueue(60, "w1", "t1")]
    got = oracle_over(ev)
    assert [(p.terminal_ms, p.resolved, p.via) for p in got] == [(ms(60), False, "key")]


def test_t39_pairing_oracle_skips_a_wait_for_a_different_resolved_key():
    ev = [h.runlong_launch(10, "l1", "artA", "x", suite=False)]
    ev += [h.runlong_wait(20, "w1", "artB"), h.enqueue(60, "w1", "t1")]
    assert [p.terminal_ms for p in oracle_over(ev)] == [None]


def test_t39_pairing_oracle_nearest_arm_window_is_strict_then_at_most_180_seconds():
    ev = [h.runlong_launch(10, "l1", "$A", "x", suite=False)]
    ev += [h.runlong_wait(190, "w1", "artX"), h.enqueue(200, "w1", "t1")]
    assert [p.terminal_ms for p in oracle_over(ev)] == [ms(200)]
    late = [h.runlong_launch(10, "l1", "$A", "x", suite=False)]
    late += [h.runlong_wait(190.001, "w1", "artX"), h.enqueue(200, "w1", "t1")]
    assert [p.terminal_ms for p in oracle_over(late)] == [None]


def test_t39_pairing_oracle_a_wait_at_the_launch_instant_is_not_after_it():
    ev = [h.runlong_launch(10, "l1", "$A", "x", suite=False)]
    ev += [h.runlong_wait(10, "w1", "artX"), h.enqueue(60, "w1", "t1")]
    assert [p.terminal_ms for p in oracle_over(ev)] == [None]


def test_t39_pairing_oracle_the_key_arm_never_claims_a_wait():
    ev = [h.runlong_launch(10, "l1", "artK", "x", suite=False)]
    ev += [h.runlong_launch(20, "l2", "artK", "x", suite=False)]
    ev += [h.runlong_wait(30, "w1", "artK"), h.enqueue(60, "w1", "t1")]
    ev += [h.runlong_launch(40, "l3", "$Z", "x", suite=False)]
    got = oracle_over(ev)
    assert [(p.terminal_ms, p.via) for p in got] == [
        (ms(60), "nearest"),
        (ms(60), "key"),
        (None, "none"),
    ]


# ---------------------------------------------------------------------------------------------
# t39s: the second sides share no primary code
# ---------------------------------------------------------------------------------------------

ORACLES = ("pairing_oracle", "plain_scan", "idle_scan", "brute_force_classes")
PRIMARY_MODULES = (k, c, dl, pl)
PRIMARY_MODULE_NAMES = frozenset(mod.__name__ for mod in PRIMARY_MODULES)
_VALUE_TYPES = (int, float, str, tuple, frozenset, dict)


def primary_names():
    """Every function, class and constant the four primary modules define themselves."""
    shared = set(vars(m))
    names = set()
    for mod in PRIMARY_MODULES:
        for name, value in vars(mod).items():
            if name.startswith("__") or name in shared:
                continue
            if isinstance(value, type) or isinstance(value, types.FunctionType):
                if getattr(value, "__module__", None) == mod.__name__:
                    names.add(name)
            elif name.isupper() and isinstance(value, _VALUE_TYPES):
                names.add(name)
    return names


#: Names that look primary code up dynamically; any use inside a walked body is a violation.
DYNAMIC_NAMES = frozenset(
    {
        "getattr",
        "__import__",
        "importlib",
        "import_module",
        "globals",
        "locals",
        "vars",
        "eval",
        "exec",
    }
)


def definitions(tree):
    """Module-level name -> the nodes that define it: functions, classes, assigned values."""
    defs = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            defs.setdefault(node.name, []).append(node)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    defs.setdefault(target.id, []).append(node.value)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.value is not None:
                defs.setdefault(node.target.id, []).append(node.value)
    return defs


def violations(tree):
    """Primary references reachable from the oracles: `(violations, names walked)`.

    Walks the oracle bodies and, for every module-level name they reference, that name's
    definition too: helper functions, classes (their bodies), and the value of an alias,
    constant or lambda, so an alias chain resolves back to the primary name. A dynamic
    lookup (`getattr`, `__import__`, `importlib`, `sys.modules` and the like) anywhere in a
    walked node is a violation of its own.
    """
    defs = definitions(tree)
    primary = primary_names() - set(defs)
    seen, found, stack = set(), [], list(ORACLES)
    while stack:
        name = stack.pop()
        if name in seen or name not in defs:
            continue
        seen.add(name)
        for root in defs[name]:
            for node in ast.walk(root):
                if isinstance(node, ast.Name):
                    if node.id in primary or node.id in DYNAMIC_NAMES:
                        found.append((name, node.id))
                    elif node.id in defs:
                        stack.append(node.id)
                elif isinstance(node, ast.Attribute):
                    if node.attr in primary or node.attr in DYNAMIC_NAMES:
                        found.append((name, node.attr))
                    elif node.attr == "modules":
                        found.append((name, "modules"))
                elif isinstance(node, ast.ImportFrom):
                    if node.module in PRIMARY_MODULE_NAMES:
                        found.append((name, node.module))
                    found.extend(
                        (name, a.name)
                        for a in node.names
                        if a.name in primary or a.name in DYNAMIC_NAMES
                    )
                elif isinstance(node, ast.Import):
                    found.extend(
                        (name, a.name)
                        for a in node.names
                        if a.name in PRIMARY_MODULE_NAMES or a.name in DYNAMIC_NAMES
                    )
    return found, seen


def inject(tree, target, node):
    """A copy of `tree` with the statement `node` appended to the body of `target`."""
    out = copy.deepcopy(tree)
    func = next(
        n for n in out.body if isinstance(n, ast.FunctionDef) and n.name == target
    )
    func.body.append(ast.fix_missing_locations(node))
    return out


def call_of(name):
    return ast.Expr(ast.Call(ast.Name(name, ast.Load()), [], []))


def test_t39s_the_derived_primary_name_set_holds_its_declared_floor():
    names = primary_names()
    assert len(names) >= 100
    for must in (
        "analyze_cycles",
        "_pair",
        "classify",
        "window_segments",
        "sweep_segments",
        "edit_calls",
        "hook_spans",
        "hook_union_total",
        "book_idle",
        "build_context",
        "idle_gaps",
        "window_spans",
        "pool_results",
    ):
        assert must in names, must


def test_t39s_the_second_sides_reference_no_primary_code():
    tree = ast.parse(SOURCE)
    found, seen = violations(tree)
    assert found == []
    assert set(ORACLES) <= seen


def test_t39s_each_second_side_is_a_real_body_with_a_loop_not_a_stub():
    tree = ast.parse(SOURCE)
    funcs = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
    for name in ORACLES:
        loops = [n for n in ast.walk(funcs[name]) if isinstance(n, ast.For)]
        assert loops, name


def test_t39s_the_oracles_walk_at_least_one_module_level_helper():
    assert walked_functions(ast.parse(SOURCE))


@pytest.mark.parametrize(
    "oracle,primary",
    [
        ("pairing_oracle", "_pair"),
        ("plain_scan", "edit_calls"),
        ("idle_scan", "idle_gaps"),
        ("brute_force_classes", "sweep_segments"),
    ],
)
def test_t39s_an_oracle_that_calls_a_primary_function_trips_the_row(oracle, primary):
    tree = inject(ast.parse(SOURCE), oracle, call_of(primary))
    found, _ = violations(tree)
    assert (oracle, primary) in found


def test_t39s_an_attribute_reference_to_a_primary_function_trips_the_row():
    node = ast.Expr(
        ast.Attribute(ast.Name("classes", ast.Load()), "sweep_segments", ast.Load())
    )
    found, _ = violations(inject(ast.parse(SOURCE), "brute_force_classes", node))
    assert ("brute_force_classes", "sweep_segments") in found


def test_t39s_an_import_of_the_primary_sweep_inside_an_oracle_trips_the_row():
    node = ast.ImportFrom(
        module="cycle_profile_classes", names=[ast.alias("sweep", None)], level=0
    )
    found, _ = violations(inject(ast.parse(SOURCE), "brute_force_classes", node))
    assert ("brute_force_classes", "cycle_profile_classes") in found


def test_t39s_a_module_import_inside_an_oracle_trips_the_row():
    node = ast.Import(names=[ast.alias("cycle_profile_cycles", None)])
    found, _ = violations(inject(ast.parse(SOURCE), "pairing_oracle", node))
    assert ("pairing_oracle", "cycle_profile_cycles") in found


def walked_functions(tree):
    """The module-level helper functions the oracles reach, sorted."""
    _, seen = violations(tree)
    funcs = {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}
    return sorted((seen & funcs) - set(ORACLES))


def test_t39s_a_primary_call_inside_a_helper_of_an_oracle_trips_the_row():
    tree = ast.parse(SOURCE)
    helper = walked_functions(tree)[0]
    found, _ = violations(inject(tree, helper, call_of("hook_spans")))
    assert (helper, "hook_spans") in found


def test_t39s_the_check_functions_may_call_the_primary_code_the_oracles_may_not():
    tree = ast.parse(SOURCE)
    funcs = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
    used = {
        n.id for n in ast.walk(funcs["check_classoracle"]) if isinstance(n, ast.Name)
    }
    assert "window_intervals" in used
    assert "brute_force_classes" in used


def inject_source(tree, target, source):
    """A copy of `tree` with the statements of `source` appended to `target`'s body."""
    out = copy.deepcopy(tree)
    func = next(
        n for n in out.body if isinstance(n, ast.FunctionDef) and n.name == target
    )
    func.body.extend(ast.parse(source).body)
    return out


def add_module_source(tree, source):
    """A copy of `tree` with the statements of `source` appended at module level."""
    out = copy.deepcopy(tree)
    out.body.extend(ast.parse(source).body)
    return out


EVASIONS = [
    ("module-alias", "_alias = sweep_segments\n", "_alias"),
    ("alias-chain", "_a1 = sweep_segments\n_a2 = _a1\n", "_a2"),
    ("module-lambda", "_lam = lambda: hook_spans\n", "_lam"),
    ("class-body", "class _Holder:\n    attr = sweep_segments\n", "_Holder"),
    ("annotated-alias", "_ann: object = sweep_segments\n", "_ann"),
]
DYNAMIC_SOURCES = [
    "import sys\nx = getattr(sys.modules['cycle_profile_classes'], 'sweep_segments')\n",
    "x = __import__('cycle_profile_classes')\n",
    "import importlib\nx = importlib.import_module('cycle_profile_classes')\n",
    "import sys\nx = sys.modules\n",
    "x = getattr(object, 'name')\n",
    "x = loader.import_module('cycle_profile_classes')\n",
]


@pytest.mark.parametrize("label,module_source,use", EVASIONS)
def test_t39s_a_module_level_alias_to_primary_code_used_by_an_oracle_trips_the_row(
    label, module_source, use
):
    tree = add_module_source(ast.parse(SOURCE), module_source)
    tree = inject_source(tree, "brute_force_classes", use + "\n")
    found, _ = violations(tree)
    assert found, label


def test_t39s_an_alias_the_oracles_never_use_does_not_trip_the_row():
    tree = add_module_source(ast.parse(SOURCE), "_alias = sweep_segments\n")
    assert violations(tree)[0] == []


@pytest.mark.parametrize("source", DYNAMIC_SOURCES)
def test_t39s_dynamic_lookup_inside_an_oracle_trips_the_row(source):
    found, _ = violations(inject_source(ast.parse(SOURCE), "pairing_oracle", source))
    assert found, source


def test_t39s_dynamic_lookup_inside_a_helper_of_an_oracle_trips_the_row():
    tree = ast.parse(SOURCE)
    helper = walked_functions(tree)[0]
    found, _ = violations(inject_source(tree, helper, "x = getattr(object, 'a')\n"))
    assert found

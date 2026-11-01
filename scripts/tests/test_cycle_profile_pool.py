"""Tests for the cycle-profile pool module: halves, placements, answer, corners, REPEAT.

Every transcript and delegate file is synthetic (fictional base date 2030-01-01, invented
ids, lorem text), built from `cycle_profile_helpers`. The known answer is spec 6.3: with the
A1 delegate file, K1 has WORK 560 (CONTROLLER 390 + SUBAGENT_WORK 170), GATE 1810
(MACHINE_WAIT 1650 + SUBAGENT_GATE 100 + HOOK 30 + SUBAGENT_HOOK 30) and OPERATOR 60 of
2430 s, shares 23.0 / 74.5 / 2.5; K1w has WORK 530 and OPERATOR 90, shares 21.8 / 74.5 / 3.7.
Rows that need a figure K1 cannot give (an AWAY second, a flipped lead, a large unlabelled
check class) edit the class seconds or hand the module a split row; the rest run the whole
chain from transcript bytes.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import cycle_profile_classes as k  # noqa: E402, I001
import cycle_profile_cycles as c  # noqa: E402
import cycle_profile_delegates as dl  # noqa: E402
import cycle_profile_helpers as h  # noqa: E402
import cycle_profile_model as m  # noqa: E402
import cycle_profile_pool as pl  # noqa: E402
import cycle_profile_records as r  # noqa: E402

GIT_TIMEOUT_S = 60


class Chain:
    """One synthetic transcript read through cycles, classes, delegates and the split."""

    def __init__(self, tmp_path, variant="skill", files=None, **over):
        self.params = m.Params(min_cycles=1, **over)
        self.transcript = r.parse_bytes(h.k1_transcript(variant))
        self.cycles = c.analyze_cycles(self.transcript, self.params)
        self.classes = k.classify(self.cycles, self.transcript, self.params)
        self.dir = h.write_delegate_dir(
            Path(tmp_path) / "agents",
            h.k1_delegate_files() if files is None else files,
        )
        self.delegates = dl.read_delegates(
            self.dir, self.classes.dispatches, self.params
        )
        self.split = dl.split(self.cycles, self.transcript, self.params, self.delegates)

    def pool(self, **kw):
        kw.setdefault("split", self.split)
        return pl.pool_results(
            self.cycles, self.classes, self.delegates, self.params, **kw
        )

    def with_classes(self, **over):
        """The class set with the first cycle's class seconds edited."""
        cycle = self.classes.cycles[0]
        edited = cycle._replace(class_s=dict(cycle.class_s, **over))
        return self.classes._replace(cycles=[edited])

    def check_inputs(self):
        return pl.check_inputs(self.cycles, self.transcript, self.params)


def split_row(work=0, rl=0, lcc=0, lco=0, hook=0, uns=0):
    """One `SPLIT_KEYS` dict, the derived keys computed from the five parts given."""
    long_calls = float(lcc + lco)
    return {
        "SUBAGENT_WORK": float(work),
        "SUBAGENT_GATE": float(rl) + long_calls,
        "SUBAGENT_HOOK": float(hook),
        "SUBAGENT_UNSPLIT": float(uns),
        "GATE_RUNLONG": float(rl),
        "GATE_LONGCALL": long_calls,
        "LONGCALL_CHECK": float(lcc),
        "LONGCALL_OTHER": float(lco),
    }


def as_split(*rows):
    return dl.SplitResult(list(rows), {})


def stage_buckets(wall, other=0.0):
    """A stage-bucket dict that partitions `wall`, with `other` unmapped seconds."""
    out = {name: 0.0 for name in m.STAGE_BUCKETS}
    out["execute"] = wall - other
    out["other"] = other
    return out


def row(rows, name):
    found = [x for x in rows if x.name == name]
    assert len(found) == 1, name
    return found[0]


def window(start_s, end_s, status="completed", f1=None, d1=None):
    return m.Window(
        h.BASE_MS + start_s * 1000,
        h.BASE_MS + end_s * 1000 + 1000,
        status,
        h.BASE_MS + end_s * 1000 if status == "completed" else None,
        None,
        f1,
        d1,
        (),
        None,
        "full",
        "full-lane",
        "dispatch-count",
        False,
        0,
        False,
        None,
        None,
    )


def synthetic_cycle(**class_s):
    classes = {name: 0.0 for name in m.CLASSES}
    classes.update(class_s)
    return m.ClassCycle(classes, 0.0, 0.0, [], [])


def empty_delegates():
    return m.DelegateSet({}, [], 0, 0, [])


# ---------------------------------------------------------------------------------------------
# t19: halves, placements, answer
# ---------------------------------------------------------------------------------------------


def test_t19a_halves_k1(tmp_path):
    active = Chain(tmp_path).pool().halves["active"]
    assert (active["work_s"], active["gate_s"], active["operator_s"]) == (
        560.0,
        1810.0,
        60.0,
    )
    assert active["unsplit_s"] == 0.0
    assert active["parts_s"]["CONTROLLER"] == 390.0
    assert active["parts_s"]["SUBAGENT_WORK"] == 170.0
    assert active["parts_s"]["MACHINE_WAIT"] == 1650.0
    assert active["parts_s"]["SUBAGENT_GATE"] == 100.0
    assert active["parts_s"]["HOOK"] == 30.0
    assert active["parts_s"]["SUBAGENT_HOOK"] == 30.0
    shares = [round(active[key], 1) for key in ("work_pct", "gate_pct", "operator_pct")]
    assert shares == [23.0, 74.5, 2.5]


def test_t19b_halves_k1w(tmp_path):
    result = Chain(tmp_path, "wrapper").pool()
    active = result.halves["active"]
    assert (active["work_s"], active["gate_s"], active["operator_s"]) == (
        530.0,
        1810.0,
        90.0,
    )
    shares = [round(active[key], 1) for key in ("work_pct", "gate_pct", "operator_pct")]
    assert shares == [21.8, 74.5, 3.7]
    assert result.wall_all_s == 2430.0


def test_t19c_twelve_placements_answer(tmp_path):
    result = Chain(tmp_path).pool()
    assert len(result.placements) == 12
    assert {p["lead"] for p in result.placements} == {"gate"}
    assert result.distinct_evaluations == 2
    assert result.answer == "gate"
    # Inside SUBAGENT alone WORK (170) beats GATE (130): that lead is carried, not decisive.
    assert {p["sub_lead"] for p in result.placements} == {"work"}
    combos = {(p["hook"], p["unsplit"], p["arm"]) for p in result.placements}
    assert len(combos) == 12


def test_t19d_placement_flip_is_undetermined(tmp_path):
    chain = Chain(tmp_path)
    classes = chain.with_classes(
        CONTROLLER=700.0,
        SUBAGENT=1500.0,
        MACHINE_WAIT=180.0,
        HOOK=0.0,
        OPERATOR_RESPONSE=50.0,
    )
    flip = as_split(split_row(work=300, lco=1200))
    result = pl.pool_results(
        chain.cycles, classes, chain.delegates, chain.params, split=flip
    )
    leads = [p["lead"] for p in result.placements]
    assert leads.count("gate") == 4 and leads.count("work") == 8
    assert result.answer == "undetermined"
    arms = {p["arm"] for p in result.placements if p["lead"] == "work"}
    assert arms == {"whole-arm-WORK", "other-token-WORK"}


def test_t19m_tied_halves_are_undetermined(tmp_path):
    chain = Chain(tmp_path)
    classes = chain.with_classes(
        CONTROLLER=1000.0,
        SUBAGENT=0.0,
        MACHINE_WAIT=1000.0,
        HOOK=0.0,
        OPERATOR_RESPONSE=0.0,
    )
    result = pl.pool_results(
        chain.cycles,
        classes,
        chain.delegates,
        chain.params,
        split=as_split(split_row()),
    )
    assert {p["lead"] for p in result.placements} == {"tie"}
    assert result.answer == "undetermined"


def test_t19e_all_basis_includes_away(tmp_path):
    chain = Chain(tmp_path)
    classes = chain.with_classes(MACHINE_WAIT=1050.0, AWAY=600.0)
    result = pl.pool_results(
        chain.cycles, classes, chain.delegates, chain.params, split=chain.split
    )
    assert (result.wall_all_s, result.away_s, result.wall_active_s) == (
        2430.0,
        600.0,
        1830.0,
    )
    active, every = result.halves["active"], result.halves["all"]
    assert round(active["work_pct"], 2) == round(560 / 1830 * 100, 2)
    assert round(every["work_pct"], 2) == round(560 / 2430 * 100, 2)
    assert round(every["gate_pct"], 2) == round(1210 / 2430 * 100, 2)
    assert round(every["away_pct"], 2) == round(600 / 2430 * 100, 2)


def test_t19f_no_split_is_all_unsplit(tmp_path):
    chain = Chain(tmp_path, files={})
    assert chain.delegates.matched == 0
    result = pl.pool_results(chain.cycles, chain.classes, chain.delegates, chain.params)
    active = result.halves["active"]
    assert active["unsplit_s"] == 300.0
    assert active["parts_s"]["SUBAGENT_WORK"] == 0.0
    assert round(active["work_pct"], 1) == 16.0
    assert round(active["unsplit_pct"], 1) == 12.3


def test_t19g_matched_files_without_a_split_refused(tmp_path):
    chain = Chain(tmp_path)
    assert chain.delegates.matched == 1
    with pytest.raises(ValueError):
        pl.pool_results(chain.cycles, chain.classes, chain.delegates, chain.params)


def test_t19h_corners_and_ranking_k1(tmp_path):
    chain = Chain(tmp_path)
    result = chain.pool(wait_by_check=chain.check_inputs().wait_by_check)
    corners = {entry["n"]: entry["s"] for entry in result.corners}
    assert corners == {1: 1650.0, 2: 0.0, 3: 100.0, 5: 60.0, 7: 60.0}
    names = [entry["name"] for entry in result.ranking]
    assert names[:6] == [
        "corner-1",
        "CONTROLLER",
        "SUBAGENT_WORK",
        "corner-3",
        "corner-5",
        "corner-7",
    ]
    unmapped = {e["name"] for e in result.ranking if e["kind"] == "unmapped"}
    assert unmapped == {
        "CONTROLLER",
        "SUBAGENT_WORK",
        "GATE_LONGCALL_CHECK",
        "GATE_LONGCALL_OTHER",
        "SUBAGENT_UNSPLIT",
        "MACHINE_WAIT[suite-unlabelled]",
        "MACHINE_WAIT[kill-sets/campaign]",
        "MACHINE_WAIT[plain-audit]",
        "MACHINE_WAIT[other]",
    }
    assert sum(e["s"] for e in result.ranking) == result.wall_active_s


def test_t19i_pools_the_completed_cycles_only():
    one = synthetic_cycle(CONTROLLER=600.0, MACHINE_WAIT=300.0, AWAY=100.0)
    two = synthetic_cycle(CONTROLLER=100.0, HOOK=50.0, OPERATOR_RESPONSE=250.0)
    cycle_set = m.CycleSet(
        (), (window(0, 1000), window(5000, 5400), window(9000, 9100, "partial")), (), ()
    )
    classes = m.ClassSet([one, two], [], [], None)
    result = pl.pool_results(
        cycle_set,
        classes,
        empty_delegates(),
        m.Params(min_cycles=1),
        wait_by_check={"suite-dev-tip": 300.0},
    )
    assert result.wall_all_s == 1400.0
    assert result.away_s == 100.0
    assert result.wall_active_s == 1300.0
    assert result.classes_s["CONTROLLER"] == 700.0
    assert result.classes_s["HOOK"] == 50.0
    assert result.classes_s["OPERATOR_RESPONSE"] == 250.0
    assert result.classes_s["MACHINE_WAIT"] == 300.0


def test_t19j_corner3_evidence_counts_delegate_calls(tmp_path):
    evidence = pl.corner3_evidence(Chain(tmp_path).delegates)
    assert (evidence.files, evidence.runlong_calls, evidence.background_calls) == (
        1,
        1,
        0,
    )
    assert evidence.exact is True


def test_t19k_corner3_evidence_unmeasured_without_files(tmp_path):
    evidence = pl.corner3_evidence(Chain(tmp_path, files={}).delegates)
    assert evidence.files == 0
    assert evidence.exact is None


def test_t19l_corner3_bound_not_exact_with_background_calls(tmp_path):
    kw = {"sidechain": True, "extra": {"agentId": h.AGENT1}}
    events = [
        h.runlong_launch(120, "d1", "dart", "gate", **kw),
        h.result(220, "d1", **kw),
        h.bash(230, "d3", "sleep 1", bg=True, **kw),
        h.result(231, "d3", **kw),
    ]
    chain = Chain(tmp_path, files={h.AGENT1: h.build_transcript(events)})
    evidence = pl.corner3_evidence(chain.delegates)
    assert evidence.background_calls == 1
    assert evidence.exact is False


# ---------------------------------------------------------------------------------------------
# t20: POOLCHECK scope
# ---------------------------------------------------------------------------------------------


def test_t20a_unlabelled_check_class_is_not_a_failure(tmp_path):
    chain = Chain(tmp_path)
    result = chain.pool(wait_by_check={"other": 1650.0})
    rows = pl.poolcheck(result, stage_buckets=stage_buckets(2430.0))
    assert row(rows, "POOLCHECK").verdict == "pass"
    assert row(rows, "POOLCHECK-STAGE-BUCKET").verdict == "pass"
    unlabelled = pl.unlabelled_machine_wait(result)
    assert unlabelled.s == 1650.0
    assert round(unlabelled.pct_all, 1) == 67.9
    assert round(unlabelled.pct_active, 1) == 67.9


def test_t20b_large_unmapped_stage_bucket_fails(tmp_path):
    chain = Chain(tmp_path)
    result = chain.pool(
        wait_by_check={"suite-feature-tip": 900.0, "suite-dev-tip": 750.0}
    )
    rows = pl.poolcheck(result, stage_buckets=stage_buckets(2430.0, other=400.0))
    assert row(rows, "POOLCHECK-STAGE-BUCKET").verdict == "fail"
    assert row(rows, "POOLCHECK").verdict == "fail"


def test_t20c_check_inputs_k1(tmp_path):
    inputs = Chain(tmp_path).check_inputs()
    assert inputs.wait_by_check["suite-feature-tip"] == 900.0
    assert inputs.wait_by_check["suite-dev-tip"] == 750.0
    assert inputs.wait_by_check["other"] == 0.0
    assert sum(inputs.wait_by_check.values()) == 1650.0
    assert set(inputs.stage_bucket_s) == set(m.STAGE_BUCKETS)
    assert inputs.stage_bucket_s["other"] == 0.0
    assert sum(inputs.stage_bucket_s.values()) == 2430.0


def test_t20d_ranking_that_misses_the_wall_fails(tmp_path):
    chain = Chain(tmp_path)
    short = {"suite-feature-tip": 900.0, "suite-dev-tip": 450.0}
    result = chain.pool(wait_by_check=short)
    rows = pl.poolcheck(result, stage_buckets=stage_buckets(2430.0))
    assert row(rows, "POOLCHECK-ARITH").verdict == "fail"
    assert row(rows, "POOLCHECK").verdict == "fail"


def test_t20e_small_unmapped_stage_bucket_passes(tmp_path):
    chain = Chain(tmp_path)
    result = chain.pool(
        wait_by_check={"suite-feature-tip": 900.0, "suite-dev-tip": 750.0}
    )
    rows = pl.poolcheck(result, stage_buckets=stage_buckets(2430.0, other=100.0))
    assert row(rows, "POOLCHECK-STAGE-BUCKET").verdict == "pass"
    assert row(rows, "POOLCHECK").verdict == "pass"


def test_t20f_unknown_check_class_refused(tmp_path):
    chain = Chain(tmp_path)
    with pytest.raises(ValueError):
        chain.pool(wait_by_check={"not-a-check": 1650.0})


# ---------------------------------------------------------------------------------------------
# t37: REPEAT over an identical git tree
# ---------------------------------------------------------------------------------------------


def _git(*args, cwd):
    env = {name: v for name, v in os.environ.items() if not name.startswith("GIT_")}
    done = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=GIT_TIMEOUT_S,
        check=False,
    )
    assert done.returncode == 0
    return done.stdout.strip()


def _make_repo(root):
    """A temp repository (both signing keys off) with two commits sharing one tree."""
    repo = Path(root).resolve() / "repo"
    repo.mkdir()
    _git("init", "-q", cwd=repo)
    _git("config", "commit.gpgsign", "false", cwd=repo)
    _git("config", "tag.gpgsign", "false", cwd=repo)
    _git("config", "user.name", "Test", cwd=repo)
    _git("config", "user.email", "test@example.invalid", cwd=repo)
    (repo / "f.txt").write_text("one\n")
    _git("add", "f.txt", cwd=repo)
    _git("commit", "-q", "-m", "one", cwd=repo)
    first = _git("rev-parse", "HEAD", cwd=repo)
    _git("commit", "-q", "--allow-empty", "-m", "two", cwd=repo)
    second = _git("rev-parse", "HEAD", cwd=repo)
    tree = _git("rev-parse", "HEAD^{tree}", cwd=repo)
    (repo / "f.txt").write_text("two\n")
    _git("commit", "-q", "-am", "three", cwd=repo)
    third = _git("rev-parse", "HEAD", cwd=repo)
    return repo, (first[:10], second[:10], third[:10]), tree


class Labelled:
    """K1 whose two suite labels carry the given feature-tip and dev-tip shas."""

    def __init__(self, feature_sha, dev_sha):
        events = []
        for item in h.k1_events("skill"):
            text = json.dumps(item)
            text = text.replace("abc1234", feature_sha).replace("def5678", dev_sha)
            events.append(json.loads(text))
        self.params = m.Params(min_cycles=1)
        self.transcript = r.parse_bytes(h.build_transcript(events))
        self.cycles = c.analyze_cycles(self.transcript, self.params)
        self.classes = k.classify(self.cycles, self.transcript, self.params)

    def pool(self, **kw):
        return pl.pool_results(
            self.cycles, self.classes, empty_delegates(), self.params, **kw
        )


def test_t37a_repeat_over_an_identical_tree(tmp_path):
    repo, (first, second, _third), tree = _make_repo(tmp_path)
    entry = Labelled(first, second).pool(repo=repo).repeat[0]
    assert entry["status"] == "measured"
    assert entry["reason"] is None
    assert entry["dev_tip_suite_s"] == 750.0
    assert entry["tree"] == tree[:8]
    assert (entry["feature_sha"], entry["dev_sha"]) == (first, second)


def test_t37b_repeat_unmeasured_without_a_repo():
    entry = Labelled("abc1234", "def5678").pool().repeat[0]
    assert entry["status"] == "unmeasured"
    assert entry["reason"] == "no-repo"
    assert entry["dev_tip_suite_s"] is None


def test_t37c_repeat_unmeasured_when_the_trees_differ(tmp_path):
    repo, (first, _second, third), _tree = _make_repo(tmp_path)
    entry = Labelled(first, third).pool(repo=repo).repeat[0]
    assert entry["status"] == "unmeasured"
    assert entry["reason"] == "trees-differ"
    assert entry["dev_tip_suite_s"] is None


def test_t37d_repeat_unmeasured_when_a_sha_is_not_in_the_repo(tmp_path):
    repo, (first, _second, _third), _tree = _make_repo(tmp_path)
    entry = Labelled(first, "0123456").pool(repo=repo).repeat[0]
    assert entry["status"] == "unmeasured"
    assert entry["reason"] == "unresolved"


# ---------------------------------------------------------------------------------------------
# Fix round 1: unmeasured corners, zero denominators, sub figures, ties, REPEAT edges
# ---------------------------------------------------------------------------------------------


def test_t19n_corners_unmeasured_without_per_check_wait(tmp_path):
    chain = Chain(tmp_path)
    result = chain.pool()
    corners = {entry["n"]: entry["s"] for entry in result.corners}
    assert corners[1] is None and corners[2] is None
    assert corners[3] == 100.0 and corners[5] == 60.0 and corners[7] == 60.0
    names = {entry["name"] for entry in result.ranking}
    assert "corner-1" not in names and "MACHINE_WAIT[unattributed]" in names
    assert sum(entry["s"] for entry in result.ranking) == result.wall_active_s
    assert pl.unlabelled_machine_wait(result) is None
    rows = pl.poolcheck(result, stage_buckets=stage_buckets(2430.0))
    assert row(rows, "POOLCHECK-CORNER-RANK").verdict == "pass"
    assert row(rows, "POOLCHECK").verdict == "pass"


def test_t19o_corners_measured_when_wait_is_supplied(tmp_path):
    chain = Chain(tmp_path)
    result = chain.pool(wait_by_check={"other": 1650.0})
    corners = {entry["n"]: entry["s"] for entry in result.corners}
    assert corners[1] == 0.0 and corners[2] == 0.0


def test_t19p_no_completed_cycle_has_no_percentages():
    result = pl.pool_results(
        m.CycleSet((), (), (), ()),
        m.ClassSet([], [], [], None),
        empty_delegates(),
        m.Params(min_cycles=1),
    )
    assert result.wall_all_s == 0.0 and result.wall_active_s == 0.0
    for basis in ("active", "all"):
        block = result.halves[basis]
        assert [block[key] for key in ("work_pct", "gate_pct", "operator_pct")] == [
            None,
            None,
            None,
        ]
        assert block["unsplit_pct"] is None
    assert result.answer == "undetermined"
    assert result.distinct_evaluations == 1


def test_t19q_all_away_leaves_active_percentages_undefined():
    cycle = synthetic_cycle(AWAY=1000.0)
    result = pl.pool_results(
        m.CycleSet((), (window(0, 1000),), (), ()),
        m.ClassSet([cycle], [], [], None),
        empty_delegates(),
        m.Params(min_cycles=1),
    )
    assert result.wall_active_s == 0.0
    assert result.halves["active"]["work_pct"] is None
    assert result.halves["all"]["work_pct"] == 0.0
    assert result.halves["all"]["away_pct"] == 100.0


def test_t19r_inside_subagent_figures_k1(tmp_path):
    result = Chain(tmp_path).pool()
    by_place = {(p["hook"], p["unsplit"], p["arm"]): p for p in result.placements}
    gate = by_place[("gate", "work", "as-classified")]
    assert (gate["sub_work_s"], gate["sub_gate_s"]) == (170.0, 130.0)
    work = by_place[("work", "work", "as-classified")]
    assert (work["sub_work_s"], work["sub_gate_s"]) == (200.0, 100.0)


def test_t19s_inside_subagent_unsplit_and_arms():
    cycle = synthetic_cycle(SUBAGENT=240.0)
    result = pl.pool_results(
        m.CycleSet((), (window(0, 240),), (), ()),
        m.ClassSet([cycle], [], [], None),
        empty_delegates(),
        m.Params(min_cycles=1),
        split=as_split(split_row(work=100, rl=50, lcc=20, lco=30, uns=40)),
    )
    by_place = {(p["hook"], p["unsplit"], p["arm"]): p for p in result.placements}
    # Base inside-SUBAGENT figures are WORK 100 and GATE 50; the arms and UNSPLIT move.
    want = {
        ("gate", "work", "as-classified"): (140, 100),
        ("gate", "gate", "as-classified"): (100, 140),
        ("gate", "work", "whole-arm-WORK"): (190, 50),
        ("gate", "gate", "other-token-WORK"): (130, 110),
    }
    for key, (sub_work, sub_gate) in want.items():
        got = by_place[key]
        assert (got["sub_work_s"], got["sub_gate_s"]) == (sub_work, sub_gate)


def test_t19t_float_noise_does_not_break_a_tie(tmp_path):
    chain = Chain(tmp_path)
    classes = chain.with_classes(
        CONTROLLER=0.1 + 0.2,
        SUBAGENT=0.0,
        MACHINE_WAIT=0.3,
        HOOK=0.0,
        OPERATOR_RESPONSE=0.0,
    )
    result = pl.pool_results(
        chain.cycles,
        classes,
        chain.delegates,
        chain.params,
        split=as_split(split_row()),
    )
    assert {p["lead"] for p in result.placements} == {"tie"}
    assert result.answer == "undetermined"


def test_t19u_equal_buckets_rank_by_name():
    parts = pl.Parts(0.0, 0.0, 0.0, 0.0, 5.0, 0.0, 5.0, 0.0, 0.0, 0.0)
    _corners, ranking = pl.ranking_of(parts, {})
    assert [x["name"] for x in ranking[:2]] == [
        "GATE_LONGCALL_CHECK",
        "SUBAGENT_WORK",
    ]


def test_t20g_stage_buckets_must_sum_to_the_wall(tmp_path):
    chain = Chain(tmp_path)
    result = chain.pool(
        wait_by_check={"suite-feature-tip": 900.0, "suite-dev-tip": 750.0}
    )
    rows = pl.poolcheck(result, stage_buckets=stage_buckets(2000.0))
    assert row(rows, "POOLCHECK-STAGE-SUM").verdict == "fail"
    assert row(rows, "POOLCHECK").verdict == "fail"
    good = pl.poolcheck(result, stage_buckets=stage_buckets(2430.0))
    assert row(good, "POOLCHECK-STAGE-SUM").verdict == "pass"


def _pair(sha, launch_s, terminal_s):
    end = None if terminal_s is None else h.BASE_MS + terminal_s * 1000
    return m.RunLongPair(
        h.BASE_MS + launch_s * 1000, end, "suite-dev-tip", None, sha, "x", True, True
    )


def _repeat_of(windows, repo):
    cycle_set = m.CycleSet((), tuple(windows), (), ())
    classes = m.ClassSet(
        [synthetic_cycle(CONTROLLER=10.0) for _ in windows], [], [], None
    )
    return pl.pool_results(
        cycle_set, classes, empty_delegates(), m.Params(min_cycles=1), repo=repo
    ).repeat


def test_t37e_repeat_reason_no_label_sha(tmp_path):
    repo, (_first, _second, _third), _tree = _make_repo(tmp_path)
    entry = _repeat_of([window(0, 10)], repo)[0]
    assert (entry["status"], entry["reason"]) == ("unmeasured", "no-label-sha")


def test_t37f_repeat_reason_no_terminal(tmp_path):
    repo, (first, second, _third), _tree = _make_repo(tmp_path)
    win = window(0, 10, f1=_pair(first, 0, 5), d1=_pair(second, 6, None))
    entry = _repeat_of([win], repo)[0]
    assert (entry["status"], entry["reason"]) == ("unmeasured", "no-terminal")


def test_t37g_repeat_ignores_the_callers_git_environment(tmp_path, monkeypatch):
    repo, (first, second, _third), tree = _make_repo(tmp_path)
    other = Path(tmp_path).resolve() / "other"
    other.mkdir()
    _git("init", "-q", cwd=other)
    monkeypatch.setenv("GIT_DIR", str(other / ".git"))
    entry = Labelled(first, second).pool(repo=repo).repeat[0]
    assert entry["status"] == "measured"
    assert entry["tree"] == tree[:8]


def test_t37h_repeat_rejects_an_empty_repo_path(tmp_path, monkeypatch):
    repo, (first, second, _third), _tree = _make_repo(tmp_path)
    monkeypatch.chdir(repo)
    entry = Labelled(first, second).pool(repo="").repeat[0]
    assert (entry["status"], entry["reason"]) == ("unmeasured", "unresolved")


def test_t37i_repeat_rejects_a_repo_path_with_a_nul_byte():
    entry = Labelled("abc1234", "def5678").pool(repo="re\0po").repeat[0]
    assert (entry["status"], entry["reason"]) == ("unmeasured", "unresolved")

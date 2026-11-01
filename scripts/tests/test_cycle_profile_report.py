"""Tests for the cycle-profile report module: channels, keys, the limits block, determinism.

Every transcript is synthetic (fictional base date 2030-01-01, invented ids, lorem text),
built from `cycle_profile_helpers` and named `synth000.jsonl`. The profile under test is the
K1 known-answer cycle of spec 6.3 run through the run module with the A1 delegate file, plus a
few degenerate transcripts (empty, kitchen sink) so the renderer meets every `None` the
profile can hold.
"""

import copy
import json
import math
import re
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import cycle_profile_helpers as h  # noqa: E402, I001
import cycle_profile_manifest as mf  # noqa: E402
import cycle_profile_model as m  # noqa: E402
import cycle_profile_privacy as privacy  # noqa: E402
import cycle_profile_report as rep  # noqa: E402
import cycle_profile_run as run  # noqa: E402

POST = {
    "leak": {"samples": 120, "hits": 0, "control": "detected", "floor": 100},
    "rows": [
        {"name": "REMEDYCHECK", "verdict": "pass", "detail": "hits=0"},
        {"name": "LEAKCHECK", "verdict": "pass", "detail": "samples=120 hits=0"},
    ],
}
SECTION_WORDS = (
    "LIMITS",
    "Summary",
    "Cycles",
    "Classes",
    "Stages",
    "AWAY",
    "Delegates",
    "Pooled",
    "Sensitivity",
    "Checks",
    "NOT MEASURED",
)


def _m(seconds):
    return "%.1f" % (seconds / 60.0)


NUMBER = re.compile(r"(?<![\w.\-])-?\d+(?:\.\d+)?(?![\w.\-])")


def profile_of(tmp_path_factory, data, delegates=None, **kw):
    """Run the run module over `data` (and optional delegate files) under `min_cycles=1`."""
    tmp = tmp_path_factory.mktemp("rep")
    path = h.write_transcript(tmp, data)
    if delegates is not None:
        h.write_delegate_dir(path.parent / path.stem / "subagents", delegates)
    return run.run_profile(path, m.Params(min_cycles=1, leak_floor=5), **kw)


@pytest.fixture(scope="module")
def k1_base(tmp_path_factory):
    data = h.k1_transcript("skill", agree_ids=True)
    return profile_of(tmp_path_factory, data, h.k1_delegate_files())


@pytest.fixture(scope="module")
def empty_base(tmp_path_factory):
    return profile_of(tmp_path_factory, b"")


@pytest.fixture(scope="module")
def sink_base(tmp_path_factory):
    return profile_of(tmp_path_factory, h.kitchen_sink_transcript())


@pytest.fixture
def k1(k1_base):
    return copy.deepcopy(k1_base)


def text_of(profile, post=None):
    return rep.render(profile, post)[0].decode("utf-8")


def lines_with(text, word):
    return [ln for ln in text.splitlines() if re.match(r"%s\b" % re.escape(word), ln)]


def one_line(text, word):
    found = lines_with(text, word)
    assert len(found) == 1, (word, found)
    return found[0]


# ---------------------------------------------------------------------------------------------
# t26ra: dynamic strings only through the channels
# ---------------------------------------------------------------------------------------------


def test_t26ra_string_outside_the_channels_raises(k1):
    k1["cycles"]["list"][0]["lane"] = "lorem ipsum dolor sit amet"
    with pytest.raises(privacy.PrivacyRefusal):
        rep.render(k1)


def test_t26ra_check_detail_with_free_text_raises(k1):
    k1["checks"][0]["detail"] = "windows=1 lorem ipsum dolor"
    with pytest.raises(privacy.PrivacyRefusal):
        rep.render(k1)


def test_t26ra_check_detail_floats_and_booleans_render(k1):
    k1["checks"][0]["detail"] = "ranked_s=2370.000 once=True corners_match=False"
    assert "ranked_s=2370.000" in text_of(k1)


def test_t26ra_manifest_selector_off_the_grammar_raises(k1):
    k1["manifest"] = {
        "rows": 1,
        "agree": 1,
        "disagree": 0,
        "unmeasurable": 0,
        "independent": 0,
        "guaranteed": 0,
        "unlabelled": 1,
        "measurable": 1,
        "floor_decided": 0,
        "largest_delta": None,
        "gate_failures": 0,
        "zone": {"rows": 0, "measured": 0, "bad": 0, "hours": [], "spread_s": None},
        "items": [
            {
                "index": 0,
                "selector": "lorem ipsum dolor sit",
                "unit": "min",
                "recorded": 1.0,
                "derived": 1.0,
                "delta": 0.0,
                "tolerance": 2.0,
                "gate": False,
                "independence": None,
                "own_tolerance": False,
                "clock": False,
                "verdict": "AGREE",
            }
        ],
    }
    with pytest.raises(privacy.PrivacyRefusal):
        rep.render(k1)


def test_t26ra_opaque_id_outside_an_id_key_raises(k1):
    k1["input"]["last_ts"] = "aid00000000000001"
    with pytest.raises(privacy.PrivacyRefusal):
        rep.render(k1)


def test_t26ra_version_outside_the_pattern_raises(k1):
    k1["versions"][0]["version"] = "lorem ipsum"
    with pytest.raises(privacy.PrivacyRefusal):
        rep.render(k1)


def test_t26ra_opaque_ids_reach_data_json_only(k1):
    md, js = rep.render(k1)
    assert b"aid00000000000001" in js
    assert b"aid00000000000001" not in md


def is_dispatch_a1(ev):
    rec = ev["rec"]
    content = rec.get("message", {}).get("content")
    return (
        rec.get("type") == "assistant"
        and isinstance(content, list)
        and content[0].get("id") == "a1"
    )


def test_t26ra_unknown_subagent_type_ends_as_role_other(tmp_path_factory):
    unknown = h.agent(100, "a1", None, "spec " + h.lorem(1), subagent_type="zz-unknown")
    events = [
        unknown if is_dispatch_a1(ev) else ev
        for ev in h.k1_events("skill", agree_ids=True)
    ]
    profile = profile_of(tmp_path_factory, h.build_transcript(events))
    assert profile["limits"]["L07"]["roles"].get("other") == 1
    assert "other=1" in one_line(text_of(profile), "L07").replace(" ", "")


def test_t26ra_manifest_name_never_appears(tmp_path_factory):
    fragment = "lorem-fragment-zzqq"
    rows = [
        {"name": fragment, "recorded": 40.5, "selector": "wall.all_min", "unit": "min"}
    ]
    data = h.k1_transcript("skill", agree_ids=True)
    profile = profile_of(tmp_path_factory, data, manifest_rows=rows)
    md, js = rep.render(profile)
    assert fragment.encode() not in md
    assert fragment.encode() not in js
    assert "wall.all_min" in md.decode()


# ---------------------------------------------------------------------------------------------
# t26rb: keys are source constants
# ---------------------------------------------------------------------------------------------


def test_t26rb_a_key_outside_the_data_keys_raises(k1):
    k1["pool"]["zzunknownkey"] = 1
    with pytest.raises(privacy.PrivacyRefusal):
        rep.render(k1)


def test_t26rb_a_key_built_from_a_timestamp_raises(k1):
    k1["pool"]["halves"]["2030-01-01T00:00:00Z"] = {"work_pct": 1.0}
    with pytest.raises(privacy.PrivacyRefusal):
        rep.render(k1)


def test_t26rb_data_keys_hold_every_top_level_profile_key(k1_base):
    assert set(k1_base) <= rep.DATA_KEYS


@pytest.mark.parametrize("which", ["k1_base", "empty_base", "sink_base"])
def test_t26rb_real_profiles_render_through_the_allowlist(request, which):
    profile = request.getfixturevalue(which)
    md, js = rep.render(profile)
    assert md and json.loads(js)["tool"] == profile["tool"]


# ---------------------------------------------------------------------------------------------
# t27: the limits block
# ---------------------------------------------------------------------------------------------


def test_t27a_every_limit_is_present(k1):
    text = text_of(k1)
    assert [i for i in m.LIMIT_IDS if not re.search(r"\b%s\b" % i, text)] == []


def test_t27a_no_limit_is_repeated(k1):
    text = text_of(k1)
    counts = {i: len(re.findall(r"\b%s\b" % i, text)) for i in m.LIMIT_IDS}
    assert {i: n for i, n in counts.items() if n > 1} == {}


def test_t27a_each_limit_appears_exactly_once_with_and_without_post(k1):
    for post in (None, POST):
        text = text_of(k1, post)
        assert [len(re.findall(r"\b%s\b" % i, text)) for i in m.LIMIT_IDS] == [1] * 18


def test_t27a_limit_ids_cover_the_model_set():
    assert tuple(rep.LIMITS) == m.LIMIT_IDS


@pytest.mark.parametrize("lid", m.LIMIT_IDS)
def test_t27b_no_digit_run_in_a_static_template(lid):
    limit = rep.LIMITS[lid]
    static = [limit.text] + [f.label for f in limit.fields]
    assert [s for s in static if re.search(r"\d{3,}", s)] == []


def test_t27b_computed_fields_come_from_the_profile(k1):
    k1["limits"]["L01"]["records"] = 987654
    assert "987654" in one_line(text_of(k1), "L01")


def test_t27b_none_in_a_computed_field_prints_a_closed_token(k1):
    k1["limits"]["L11"] = {"s": None, "pct_all": None, "pct_active": None}
    line = one_line(text_of(k1), "L11")
    assert "n/a" in line and "None" not in line


@pytest.mark.parametrize("lid", m.LIMIT_IDS)
def test_t27c_no_remedy_word_in_a_template(lid):
    limit = rep.LIMITS[lid]
    static = " ".join([limit.text] + [f.label for f in limit.fields])
    assert privacy.find_remedy_words(static) == []


@pytest.mark.parametrize("which", ["k1_base", "empty_base", "sink_base"])
def test_t27c_no_remedy_word_in_the_rendered_report(request, which):
    profile = request.getfixturevalue(which)
    assert privacy.find_remedy_words(text_of(profile, POST)) == []


# ---------------------------------------------------------------------------------------------
# t30: determinism
# ---------------------------------------------------------------------------------------------


def test_t30ra_two_renders_are_byte_identical(k1):
    assert rep.render(k1) == rep.render(copy.deepcopy(k1))
    assert rep.render(k1, POST) == rep.render(copy.deepcopy(k1), POST)


def reversed_tree(obj):
    if isinstance(obj, dict):
        return {k: reversed_tree(obj[k]) for k in reversed(list(obj))}
    if isinstance(obj, list):
        return [reversed_tree(x) for x in obj]
    return obj


def test_t30ra_dict_order_does_not_matter(k1):
    assert rep.render(k1) == rep.render(reversed_tree(k1))


def test_t30ra_no_wall_clock_enters_the_report(k1, monkeypatch):
    first = rep.render(k1)
    ticks = iter(range(10**6, 10**7, 977))
    monkeypatch.setattr(time, "time", lambda: next(ticks))
    monkeypatch.setattr(time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(time, "strftime", lambda *a, **k: "2031-02-03T04:05:06Z")
    assert rep.render(k1) == first


def data_numbers(node, out):
    if isinstance(node, bool) or node is None or isinstance(node, str):
        return
    if isinstance(node, (int, float)):
        out.append(float(node))
    elif isinstance(node, dict):
        for v in node.values():
            data_numbers(v, out)
    elif isinstance(node, list):
        for v in node:
            data_numbers(v, out)


def derivable(token, pool):
    places = len(token.split(".")[1]) if "." in token else 0
    want = float(token)
    for x in pool:
        for y in (x, x / 60.0):
            if math.isclose(round(y, places), want, abs_tol=10 ** -(places + 1)):
                return True
    return False


def test_t30rb_every_report_number_is_in_data_json(k1):
    md, js = rep.render(k1, POST)
    pool = []
    data_numbers(json.loads(js), pool)
    body = [ln for ln in md.decode().splitlines() if not ln.startswith("CHECK ")]
    stray = [n for n in NUMBER.findall("\n".join(body)) if not derivable(n, pool)]
    assert stray == []


def test_t30rb_every_check_line_is_a_row_of_data_json(k1):
    md, js = rep.render(k1, POST)
    tree = json.loads(js)
    rows = {
        "CHECK %s %s %s" % (c["name"], c["verdict"], c["detail"])
        for c in tree["checks"] + tree["post_checks"]
    }
    lines = [ln for ln in md.decode().splitlines() if ln.startswith("CHECK ")]
    assert lines and set(lines) == rows


def test_t30rb_pooled_stage_rows_are_in_data_json(k1):
    k1["cycles"]["list"] = k1["cycles"]["list"] * 2
    md, js = rep.render(k1)
    pool = []
    data_numbers(json.loads(js), pool)
    rows = lines_with(md.decode(), "STAGE")
    assert rows and all(derivable(n, pool) for ln in rows for n in NUMBER.findall(ln))
    assert "wall_min=%s" % _m(2 * 600.0) in md.decode()


def test_t30rb_a_number_not_in_the_data_is_noticed():
    assert not derivable("4242.5", [1.0, 2.0])


def test_t30rb_data_json_is_the_profile_tree(k1):
    md, js = rep.render(k1)
    tree = json.loads(js)
    for key in ("cycles", "pool", "limits", "checks", "floors", "input"):
        assert tree[key] == k1[key], key
    assert tree["leak"] is None


def test_t30rb_data_json_is_sorted_utf8_lf_with_one_trailing_newline(k1):
    md, js = rep.render(k1)
    for blob in (md, js):
        assert isinstance(blob, bytes) and blob.endswith(b"\n")
        assert not blob.endswith(b"\n\n") and b"\r" not in blob
        blob.decode("utf-8")
    tree = json.loads(js)
    assert list(tree["pool"]) == sorted(tree["pool"])


# ---------------------------------------------------------------------------------------------
# t51: the summary, the section order, the run-id
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("which", ["k1_base", "empty_base", "sink_base"])
def test_t51_summary_is_at_most_forty_lines(request, which):
    summary = rep.stdout_summary(request.getfixturevalue(which))
    assert 1 <= len(summary.splitlines()) <= 40


def test_t51_summary_with_many_stage_rows_stays_bounded(k1):
    cyc = k1["cycles"]["list"][0]
    cyc["stages"] = cyc["stages"] * 4
    assert len(rep.stdout_summary(k1).splitlines()) <= 40
    assert len(lines_with(rep.stdout_summary(k1), "STAGE")) == 8


def test_t51_summary_carries_the_answer_and_counts(k1):
    summary = rep.stdout_summary(k1)
    first = summary.splitlines()[0]
    assert first.startswith("ANSWER basis=ACTIVE half=gate N=1")
    assert "partial=0" in first and "open=0" in first
    assert "COVERAGE matched=1/2" in summary


def test_t51_summary_shares_use_both_bases(k1):
    summary = rep.stdout_summary(k1)
    assert re.search(r"^SHARE basis=ACTIVE .*work_pct=23\.0", summary, re.M)
    assert re.search(r"^SHARE basis=ALL ", summary, re.M)


def test_t51_re_tier_line_has_both_bases(k1):
    summary = rep.stdout_summary(k1)
    active = one_line(summary, "RE-TIER basis=ACTIVE")
    assert "operator_pct=2.5" in active and "gate_pct=74.5" in active
    assert "relation=operator-below-gate" in active
    assert "informational" in one_line(summary, "RE-TIER basis=ALL")


def test_t51_re_tier_relation_follows_the_shares(k1):
    half = k1["pool"]["halves"]["active"]
    half["operator_pct"], half["gate_pct"] = 80.0, 10.0
    line = one_line(rep.stdout_summary(k1), "RE-TIER basis=ACTIVE")
    assert "relation=operator-above-gate" in line


def test_t51_summary_unlabelled_machine_wait_line(k1):
    summary = rep.stdout_summary(k1)
    assert one_line(summary, "UNLABELLED-MACHINE-WAIT").startswith(
        "UNLABELLED-MACHINE-WAIT min="
    )
    k1["limits"]["L11"] = {"s": None, "pct_all": None, "pct_active": None}
    line = one_line(rep.stdout_summary(k1), "UNLABELLED-MACHINE-WAIT")
    assert "unmeasured" in line and "reason=" in line


def test_t51_report_summary_section_equals_stdout_summary(k1):
    text = text_of(k1)
    assert rep.stdout_summary(k1) in text


def test_t51_sections_appear_in_the_section_five_order(k1):
    text = text_of(k1, POST)
    heads = [ln for ln in text.splitlines() if ln.startswith("## ")]
    positions = []
    for word in SECTION_WORDS:
        hits = [i for i, ln in enumerate(heads) if word in ln]
        assert hits, word
        positions.append(hits[0])
    assert positions == sorted(positions)
    assert "Leak" in heads[-1]
    assert text.index("PARAM ") < text.index(heads[0])


def test_t51_sections_constant_names_the_rendered_headings(k1):
    heads = [ln[3:] for ln in text_of(k1, POST).splitlines() if ln.startswith("## ")]
    assert heads == [s for s in rep.SECTIONS if s != "Manifest"]


def test_t51_manifest_section_renders_rows_by_selector(tmp_path_factory):
    rows = [
        {
            "name": "lorem-row",
            "recorded": 40.5,
            "selector": "wall.all_min",
            "unit": "min",
        }
    ]
    data = h.k1_transcript("skill", agree_ids=True)
    profile = profile_of(tmp_path_factory, data, manifest_rows=rows)
    text = text_of(profile)
    assert "## Manifest" in text
    assert "MANIFEST rows=1 agree=1 disagree=0 unmeasurable=0" in text
    assert re.search(
        r"^ROW wall\.all_min unit=min recorded=40\.5 derived=40\.5", text, re.M
    )


def test_t51_manifest_section_only_when_given(k1):
    assert "## Manifest" not in text_of(k1)


def test_t51_both_files_share_the_first_line_run_id(k1):
    md, js = rep.render(k1)
    rid = rep.run_id(k1)
    assert re.fullmatch(r"[0-9a-f]{16}", rid)
    assert md.decode().splitlines()[0].endswith("run-id=" + rid)
    assert json.loads(js)["run_id"] == rid
    assert rid in js.decode().splitlines()[0]


def test_t51_run_id_follows_the_pin(k1):
    first = rep.run_id(k1)
    k1["input"]["sha256"] = "0" * 64
    assert rep.run_id(k1) != first


def test_t51_run_id_follows_the_parameters(k1):
    first = rep.run_id(k1)
    k1["params"][0]["value"] = k1["params"][0]["value"] + 1
    assert rep.run_id(k1) != first


def test_t51_run_id_ignores_everything_else(k1):
    first = rep.run_id(k1)
    k1["pool"]["answer"] = "work"
    k1["checks"][0]["verdict"] = "fail"
    assert rep.run_id(k1) == first


# ---------------------------------------------------------------------------------------------
# t52: data-conditional lines
# ---------------------------------------------------------------------------------------------


def test_t52_order_indicators_present_with_figures(k1):
    line = one_line(text_of(k1), "ORDER-INDICATORS")
    assert "hidden_agent_min=5.0" in line and "stalled_any_min=0.0" in line


def test_t52_order_indicators_unmeasured_without_a_cycle(empty_base):
    line = one_line(text_of(empty_base), "ORDER-INDICATORS")
    assert "unmeasured" in line and "reason=no-completed-cycle" in line


def test_t52_corner3_evidence_present_with_delegate_files(k1):
    line = one_line(text_of(k1), "CORNER3-EVIDENCE")
    assert "files=1" in line and "runlong_calls=1" in line and "exact=yes" in line


def test_t52_corner3_evidence_unmeasured_without_delegate_files(k1):
    k1["limits"]["L12"].update(files=0, runlong_calls=0, background_calls=0, exact=None)
    k1["delegates"]["files"] = 0
    line = one_line(text_of(k1), "CORNER3-EVIDENCE")
    assert "unmeasured" in line and "reason=no-delegate-file" in line


def test_t52_dominant_check_is_a_computed_line_without_a_criterion(k1):
    line = one_line(text_of(k1), "DOMINANT-CHECK")
    assert "largest_ms=30000" in line and "share_pct=100.0" in line
    assert not re.search(r"\b(pass|fail|ok)\b", line)


def test_t52_dominant_check_unmeasured_without_a_slow_call(k1):
    k1["indicators"]["dominant_hook"] = {
        "status": "none",
        "calls": 0,
        "largest_ms": 0,
        "union_ms": 0,
        "share_pct": None,
    }
    line = one_line(text_of(k1), "DOMINANT-CHECK")
    assert "unmeasured" in line and "reason=no-qualifying-call" in line


def test_t52_suitemach_prints_per_run_figures_and_a_band(k1):
    text = text_of(k1)
    feature = [ln for ln in lines_with(text, "SUITEMACH") if "stage=feature-tip" in ln]
    assert len(feature) == 1
    assert "run_min=15.0" in feature[0] and "band_min=5.0-60.0" in feature[0]
    assert "band=in" in feature[0]


def test_t52_suitemach_flags_a_run_outside_the_band_without_failing(k1):
    k1["indicators"]["suite_mach"][0]["feature_run_s"] = [7200.0]
    k1["indicators"]["suite_mach"][0]["feature_in_band"] = False
    line = [
        ln for ln in lines_with(text_of(k1), "SUITEMACH") if "stage=feature-tip" in ln
    ][0]
    assert "band=out" in line
    assert not re.search(r"\b(fail|FAIL)\b", line)


def test_t52_suitemach_says_absent_when_the_stage_has_no_suite_run(k1):
    k1["indicators"]["suite_mach"][0]["dev_run_s"] = []
    k1["indicators"]["suite_mach"][0]["dev_in_band"] = None
    line = [ln for ln in lines_with(text_of(k1), "SUITEMACH") if "stage=dev-tip" in ln][
        0
    ]
    assert "absent" in line and "band=" not in line


def test_t52_subcheck_prints_the_median_with_its_band(k1):
    line = one_line(text_of(k1), "SUBCHECK")
    assert "median=1.0" in line and "band=0.5-2.0" in line and "band_flag=in" in line


def test_t52_subcheck_unmeasured_without_an_enqueue_pair(k1):
    k1["indicators"]["subcheck"] = {
        "n": 0,
        "median": None,
        "p10": None,
        "p90": None,
        "in_band": None,
        "not_enqueue": 0,
    }
    line = one_line(text_of(k1), "SUBCHECK")
    assert "unmeasured" in line and "reason=no-enqueue-pair" in line


def test_t52_hook_sensitivity_lines_at_five_and_thirty_seconds(k1):
    lines = lines_with(text_of(k1), "SENSITIVITY-HOOK")
    seconds = sorted(re.search(r"threshold_s=(\d+\.\d)", ln).group(1) for ln in lines)
    assert seconds == ["10.0", "30.0", "5.0"]
    five = [ln for ln in lines if "threshold_s=5.0" in ln][0]
    assert "hook_min=0.8" in five and "primary=no" in five


def test_t52_hook_sensitivity_unmeasured_without_rows(k1):
    k1["sensitivity"]["hook"] = []
    line = one_line(text_of(k1), "SENSITIVITY-HOOK")
    assert "unmeasured" in line and "reason=" in line


def test_t52_coverage_line_and_weak_flag(k1):
    text = text_of(k1)
    assert "COVERAGE matched=1/2" in text
    assert "COVERAGE WEAK" in text


def test_t52_fence_robust_unmeasured_below_the_gap_floor(k1):
    line = one_line(text_of(k1), "FENCE-ROBUST")
    assert "unmeasured" in line and "reason=below-min-gaps" in line


def test_t52_repeat_line_per_cycle_names_the_reason(k1):
    line = one_line(text_of(k1), "REPEAT")
    assert "cycle=0" in line and "unmeasured" in line and "reason=no-repo" in line


def test_t52_a_none_percentage_prints_n_a_not_zero(empty_base):
    line = one_line(rep.stdout_summary(empty_base), "SHARE basis=ACTIVE")
    assert "work_pct=n/a" in line and "gate_pct=n/a" in line
    assert "wall_min=n/a" in line


def test_t52_an_unmeasured_corner_prints_the_reason_not_a_figure(k1):
    k1["pool"]["corners"][0]["s"] = None
    lines = lines_with(text_of(k1), "CORNER n=1")
    assert len(lines) == 1
    assert "unmeasured" in lines[0] and "reason=no-per-check-wait" in lines[0]
    assert "min=" not in lines[0]


def test_t52_a_none_ranking_figure_prints_unmeasured(k1):
    k1["pool"]["ranking"][0]["s"] = None
    assert "| unmeasured |" in text_of(k1)


def test_t51_summary_stage_rows_are_largest_first(k1):
    rows = lines_with(rep.stdout_summary(k1), "STAGE")
    assert rows[0] == "STAGE feature-tip-suite wall_min=17.5"
    walls = [float(ln.rsplit("=", 1)[1]) for ln in rows]
    assert walls == sorted(walls, reverse=True)


def test_t52_open_invocation_prints_its_distance_from_last_ts(k1):
    k1["cycles"]["open"] = [
        {
            "start_utc": "2030-01-01T01:00:00Z",
            "status": "open",
            "lane": None,
            "lane_source": None,
            "reconstructable": None,
            "plan_only": False,
            "suite_runs": 0,
            "pairing_weak": False,
            "first_missing_link": "none-paired",
            "minutes_from_last_ts": 12.5,
            "end_marker": None,
            "basis": None,
        }
    ]
    text = text_of(k1)
    assert "last invocation in the pinned prefix, no closing marker" in text
    assert "minutes_from_last_ts=12.5" in text


def test_t52_none_never_prints_as_zero_or_none(empty_base):
    text = text_of(empty_base, POST)
    assert not re.search(r"\bNone\b", text)
    assert "n/a" in text


def test_t52_absent_critical_shape_is_not_measured(empty_base):
    assert "NOT MEASURED (shape ts absent)" in text_of(empty_base)


# ---------------------------------------------------------------------------------------------
# post: the leak section and the two post-render rows
# ---------------------------------------------------------------------------------------------


def test_t52_post_prints_the_leak_section_and_rows(k1):
    text = text_of(k1, POST)
    assert "## Leak check" in text
    assert "LEAKCHECK samples=120 hits=0 control=detected floor=100" in text
    assert "CHECK REMEDYCHECK pass hits=0" in text
    assert "CHECK LEAKCHECK pass samples=120 hits=0" in text


def test_t52_without_post_no_leak_section_and_no_post_rows(k1):
    text = text_of(k1)
    assert "## Leak check" not in text
    assert "LEAKCHECK" not in text and "REMEDYCHECK" not in text


def test_t52_post_leak_fills_the_leak_limit_and_data_json(k1):
    md, js = rep.render(k1, POST)
    assert json.loads(js)["leak"] == POST["leak"]
    assert "samples=120" in one_line(md.decode(), "L16")
    assert "unmeasured" in one_line(text_of(k1), "L16")


def test_t52_post_row_with_an_unknown_name_raises(k1):
    bad = {
        "leak": POST["leak"],
        "rows": [{"name": "SUMCHECK", "verdict": "pass", "detail": "windows=1 bad=0"}],
    }
    with pytest.raises(privacy.PrivacyRefusal):
        rep.render(k1, bad)


def test_t52_post_leak_control_outside_the_tokens_raises(k1):
    bad = {"leak": dict(POST["leak"], control="lorem ipsum"), "rows": POST["rows"]}
    with pytest.raises(privacy.PrivacyRefusal):
        rep.render(k1, bad)


# ---------------------------------------------------------------------------------------------
# result_line
# ---------------------------------------------------------------------------------------------

LINE = dict(
    cycles=1,
    dispatches=2,
    matched=1,
    passed=20,
    failed=0,
    skipped=3,
    samples=120,
    hits=0,
    answer="gate",
)


def test_t51_result_line_has_the_spec_grammar():
    line = rep.result_line("INCOMPLETE", **LINE)
    assert line == (
        "RESULT: INCOMPLETE rc=3 cycles=1 dispatches=2 split=1/2 checks=20/0/3 "
        "leak=120/0 answer=gate"
    )


@pytest.mark.parametrize("verdict, rc", [("PASS", 0), ("FAIL", 1), ("ERROR", 2)])
def test_t51_result_line_rc_follows_the_verdict(verdict, rc):
    assert " rc=%d " % rc in rep.result_line(verdict, **LINE)


def test_t51_result_line_refuses_a_verdict_outside_the_set():
    with pytest.raises(privacy.PrivacyRefusal):
        rep.result_line("PASS rc=0\nRESULT: PASS", **LINE)


def test_t51_result_line_refuses_an_answer_outside_the_set():
    with pytest.raises(privacy.PrivacyRefusal):
        rep.result_line("PASS", **dict(LINE, answer="lorem"))


def test_t51_result_line_refuses_a_non_integer_count():
    with pytest.raises(privacy.PrivacyRefusal):
        rep.result_line("PASS", **dict(LINE, cycles="1 lorem"))
    with pytest.raises(privacy.PrivacyRefusal):
        rep.result_line("PASS", **dict(LINE, hits=True))


# ---------------------------------------------------------------------------------------------
# Review folds: the manifest verdict set, the vocabulary audit, the detail rules, limits_block
# ---------------------------------------------------------------------------------------------


def manifest_profile(tmp_path_factory, rows):
    data = h.k1_transcript("skill", agree_ids=True)
    return profile_of(tmp_path_factory, data, manifest_rows=rows)


def test_t26rb_manifest_item_with_an_absent_figure_renders(tmp_path_factory):
    rows = [
        {
            "name": "lorem-row",
            "recorded": 5.0,
            "selector": "cycle[2031-05-05].wall_min",
            "unit": "min",
        }
    ]
    profile = manifest_profile(tmp_path_factory, rows)
    assert profile["manifest"]["items"][0]["verdict"] == "unmeasurable"
    text = text_of(profile)
    assert re.search(r"^ROW cycle\[2031-05-05\]\.wall_min .* unmeasurable$", text, re.M)
    assert "MANIFEST rows=1 agree=0 disagree=0 unmeasurable=1" in text


def test_t26rb_manifest_verdicts_are_the_manifest_modules_own():
    assert set(mf._VERDICTS) <= rep.REPORT_VOCAB


AUDIT = {
    "lanes": lambda: rep.LANES,
    "lane_sources": lambda: rep.LANE_SOURCES,
    "end_markers": lambda: rep.END_MARKERS,
    "arm_placements": lambda: rep.ARM_PLACEMENTS,
    "core_buckets": lambda: rep.CORE_BUCKETS,
    "repeat_reasons": lambda: rep.REPEAT_REASONS,
    "split_keys": lambda: rep.SPLIT_KEYS,
    "missing_links": lambda: m.MISSING_LINKS,
    "check_classes": lambda: ["MACHINE_WAIT[%s]" % c for c in m.CHECK_CLASSES],
    "needed_by": lambda: [n for _k, n in m.SHAPE_TABLE.values()],
    "manifest_independence": lambda: mf.INDEPENDENCE,
    "manifest_units": lambda: list(mf.FLOOR_IN_UNIT) + ["token", "hours"],
    "floor_names": lambda: list(run.FLOOR_REASONS),
    "incomplete_reasons": lambda: list(run.REASONS),
    "param_names": lambda: list(m.Params._fields) + list(run.PATH_PARAMS),
    "sensitivity_sets": lambda: [r[0] for r in rep.SENSITIVITY_SETS],
}


@pytest.mark.parametrize("group", sorted(AUDIT))
def test_t26rb_vocabulary_holds_every_producer_constant(group):
    known = rep.REPORT_VOCAB | privacy.DEFAULT_VOCAB
    assert [x for x in AUDIT[group]() if x not in known] == []


def window_row(**kw):
    row = {
        "start_utc": "2030-01-01T01:00:00Z",
        "status": "partial",
        "lane": "partial-no-suites",
        "lane_source": "no-suite-pair",
        "reconstructable": "partial",
        "plan_only": False,
        "suite_runs": 0,
        "pairing_weak": False,
        "first_missing_link": "none-paired",
        "minutes_from_last_ts": None,
        "end_marker": None,
        "basis": None,
    }
    row.update(kw)
    return row


def test_t26rb_a_partial_window_from_a_real_run_renders(tmp_path_factory):
    events = [h.wrapper(-500, "feature"), h.say(-400)] + h.k1_events("skill")
    profile = profile_of(tmp_path_factory, h.build_transcript(events))
    assert len(profile["cycles"]["partial"]) == 1
    assert "PARTIAL start_utc=" in text_of(profile)


@pytest.mark.parametrize("recon", ["full", "partial", "absent", "excluded"])
def test_t26rb_every_reconstructable_token_renders(k1, recon):
    k1["cycles"]["partial"] = [window_row(reconstructable=recon)]
    k1["cycles"]["list"][0]["reconstructable"] = recon
    assert "| %s |" % recon in text_of(k1)


def test_t26rb_a_measured_repeat_entry_renders(k1):
    k1["pool"]["repeat"] = [
        {
            "cycle": 0,
            "status": "measured",
            "reason": None,
            "feature_sha": "abcdef1",
            "dev_sha": "abcdef2",
            "tree": "a" * 40,
            "dev_tip_suite_s": 600.0,
        }
    ]
    line = one_line(text_of(k1), "REPEAT")
    assert "feature_sha=abcdef1" in line and "dev_tip_suite_min=10.0" in line


def test_t26ra_number_with_an_over_long_digit_run_raises(k1):
    k1["checks"][0]["detail"] = "windows=1234567890123"
    with pytest.raises(privacy.PrivacyRefusal):
        rep.render(k1)


def test_t26ra_number_of_twelve_digits_renders(k1):
    k1["checks"][0]["detail"] = "windows=123456789012"
    assert "windows=123456789012" in text_of(k1)


def test_t26ra_word_glued_to_a_number_raises(k1):
    k1["checks"][0]["detail"] = "windows=123lorem"
    with pytest.raises(privacy.PrivacyRefusal):
        rep.render(k1)


def test_t26ra_detail_of_twenty_five_tokens_raises(k1):
    k1["checks"][0]["detail"] = " ".join(["bad=0"] * 25)
    with pytest.raises(privacy.PrivacyRefusal):
        rep.render(k1)


def test_t26ra_detail_of_twenty_four_tokens_renders(k1):
    k1["checks"][0]["detail"] = " ".join(["bad=0"] * 24)
    assert text_of(k1)


def test_t26ra_post_row_detail_with_an_unknown_word_raises(k1):
    bad = {
        "leak": POST["leak"],
        "rows": [{"name": "LEAKCHECK", "verdict": "pass", "detail": "lorem=1"}],
    }
    with pytest.raises(privacy.PrivacyRefusal):
        rep.render(k1, bad)


def test_t26ra_post_words_are_the_closed_five():
    assert set(rep.POST_WORDS) == {"hits", "samples", "words", "control", "floor"}


def test_t27b_none_in_an_integer_field_prints_n_a(k1):
    line = one_line(text_of(k1), "L17")
    assert "independent=n/a" in line and "floor_decided=n/a" in line
    assert "independent=0" not in line


def test_t51_limits_block_is_the_eighteen_limit_lines(k1):
    block = rep.limits_block(k1)
    for lid in m.LIMIT_IDS:
        assert len(re.findall(r"\b%s\b" % lid, block)) == 1
    assert block in text_of(k1)


def test_t51_limits_block_validates_its_profile(k1):
    k1["limits"]["L01"]["records"] = "lorem ipsum dolor"
    with pytest.raises(privacy.PrivacyRefusal):
        rep.limits_block(k1)


def test_t51_limits_block_fills_the_leak_limit_as_unmeasured(k1):
    assert "unmeasured" in one_line(rep.limits_block(k1), "L16")


def test_t26ra_fraction_with_an_over_long_digit_run_raises(k1):
    k1["checks"][0]["detail"] = "ranked_s=1.1234567890123"
    with pytest.raises(privacy.PrivacyRefusal):
        rep.render(k1)


def test_t58_the_report_states_that_appended_records_are_not_read(k1):
    text = text_of(k1)
    assert text.count(rep.READ_NOTE) == 1
    assert text.index("\nREAD ") < text.index(rep.READ_NOTE) < text.index("\n## ")


def test_t58_the_read_note_is_closed_text():
    assert re.search(r"\d", rep.READ_NOTE) is None
    assert privacy.find_remedy_words(rep.READ_NOTE) == []

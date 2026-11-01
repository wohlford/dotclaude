"""The cycle-profile pipeline: one read, every module in order, one profile, one verdict.

`run_profile` reads the transcript ONCE (`cycle_profile_records`), then runs `cycles`,
`classes`, `delegates`, `pool`, the self-checks (`cycle_profile_checks`) and the optional
manifest (`cycle_profile_manifest`) in that order, and assembles ONE plain dict: the profile
of "Profile layout v1" (the table in `cycle_profile_model`). The module formats nothing: the
report module renders the profile, and the CLI owns files, flags and the post-render checks
(REMEDYCHECK, LEAKCHECK, OUTSCOPE), which need bytes that exist only after rendering.

**Plain data.** Every value in the profile is a dict, list, string token, integer, float,
bool or `None`: no `NamedTuple`, no tuple, no transcript text. A zero denominator is `None`,
never 0 and never a number. Times are seconds, as in the modules it calls. Opaque ids appear
only in `delegates.inventory[]`.

**Additions to layout v1** (each is a superset of what the table names):

- `input.parsed` (records that parsed) and `input.untimed`. `input.records` is the SNAPSHOT
  count, every non-blank line, so a garbage-only file has `records` above 0 and `parsed` 0.
- Path parameters in `params[]` beside the 14 `Params` fields, with a value of `given` or
  `none` (`PARAM name=given|none`, never the path).
- `shapes[].gap`, `versions[].gaps` (the D2.3 `GAP` flag), `stages[].wall_s`.
- `cycles.list[]` and the `partial[]` / `open[]` rows carry the window facts the closing rule
  produced (`end_marker`, `basis`, `plan_only`, `pairing_weak`, `first_missing_link`,
  `minutes_from_last_ts`). Only a completed row holds `wall_s`, `classes_s`, `stages` and
  `split_s`.
- `pool.n`, `idle.gaps_s`, `fence.fitted` and `fence.robust`, `sensitivity.{sets,hook,
  priority,no_separation}`.
- `indicators`: the data-conditional lines (`order`, `suite_mach`, `subcheck`,
  `dominant_hook`) and the two optional external cross-checks (`artifact_end`,
  `commit_bound`), which are data and never check rows.
- `limits`: one dict per `LIMIT_IDS` entry holding that limit's computed fields.
- `not_measured[]`: `{what, name, reason, needed_by}` with closed tokens (`shape`,
  `cross-check`, `away`, `corner`, `class`, `repeat`, `commit-bound`, `artifact-end`).

**Floors** (`floors[]`, `FLOOR name want actual ok|NOT-MET`): `min_cycles`,
`max_unparseable_pct`, `min_timestamped_pct`, `coverage` (want 80, met when there is no
dispatch), `critical_shapes` (want 0 absent) and `checks_run` (want `CHECK_FLOOR` of the
`CHECK_NAMES` present as rows). An actual of `None` (a zero denominator) never meets a floor.

**The verdict.** `derive_verdict` is the one place the final verdict and the `checks=p/f/s`
counts are computed. Precedence ERROR > FAIL > INCOMPLETE > PASS. A check row counts as
pass, fail or skip as it says; a floor counts as pass when met and skip when not, so a
skipped or unproven row is never a pass. ERROR is never read from the profile: a read error,
a pin mismatch, a bad manifest row or a refusal is an exception the CLI maps to ERROR, and a
post-render condition the CLI classes as ERROR arrives as `error=True`. The INCOMPLETE
reasons are the closed tokens of `REASONS`.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import cycle_profile_checks as ck
from cycle_profile_classes import (
    SUITEMACH_BAND_MIN,
    build_context,
    classify,
    fence_robust,
    hook_reported_s,
    hook_sensitivity,
    hook_union_total,
    long_call_arm,
    no_separation_class_s,
    order_indicators,
    sensitivity,
    stage_cut,
    subcheck,
    suite_machine_wait,
    turn_duration_s,
)
from cycle_profile_cycles import (
    analyze_cycles,
    artifact_end_check,
    commit_time_lower_bound,
)
from cycle_profile_delegates import (
    WEAK_DEN,
    WEAK_NUM,
    call_counts,
    coverage,
    cycle_dispatches,
    priority_sensitivity,
    read_delegates,
    split,
)
from cycle_profile_manifest import (
    evaluate_manifest,
    manifest_check,
    validate_rows,
    zone_check,
)
from cycle_profile_model import (
    CHECK_VERDICTS,
    LIMIT_IDS,
    RC,
    SHAPE_TABLE,
    SHAPES,
    CheckRow,
    Params,
    param_sources,
)
from cycle_profile_pool import (
    CORNER_NOT_MEASURED,
    check_inputs,
    corner3_evidence,
    lead_counts,
    pool_results,
    unlabelled_machine_wait,
)
from cycle_profile_records import (
    format_ts,
    read_transcript,
    timestamped_pct,
    unparseable_pct,
)

#: The `tool.version` token of the profile.
TOOL_VERSION = "1.0.0"

#: An upper bound for "the whole snapshot" in a millisecond window.
FAR_MS = 1 << 62

#: The share of dispatches that must be matched to files (the `coverage` floor), in percent,
#: derived from the delegate module's own threshold so the two cannot drift.
COVERAGE_WANT_PCT = 100 * WEAK_NUM // WEAK_DEN

#: The `Params` fields `run_profile` does not consume (the CLI and `privacy` do).
UNCONSUMED_PARAMS: Tuple[str, ...] = ("leak_samples", "leak_floor", "leak_seed")

#: The path parameters echoed as `given` or `none`, never as a path.
PATH_PARAMS: Tuple[str, ...] = (
    "delegates",
    "artifacts",
    "repo",
    "manifest",
    "prefix",
    "delegate_pins",
)

#: The CROSS-CHECK shapes whose absence is listed as NOT MEASURED (they never force
#: INCOMPLETE): the other cross-check shapes can legitimately be absent from a good session.
LISTED_CROSS_CHECKS: Tuple[str, ...] = (
    "hook_success",
    "turn_duration",
    "sidechain_flag",
)

#: The INCOMPLETE reasons a floor gives, by floor name.
FLOOR_REASONS: Dict[str, str] = {
    "min_cycles": "cycles-under-min",
    "max_unparseable_pct": "unparseable-over-cap",
    "min_timestamped_pct": "timestamped-under-floor",
    "coverage": "coverage-weak",
    "critical_shapes": "shape-absent",
    "checks_run": "checks-under-floor",
}

#: The INCOMPLETE reasons only the caller can know (post-render conditions).
CALLER_REASONS: Tuple[str, ...] = ("leak-unproven",)

#: Every INCOMPLETE reason, as a closed token.
REASONS: Tuple[str, ...] = (
    ("no-completed-cycle",)
    + tuple(FLOOR_REASONS.values())
    + ("floor-unmet",)
    + CALLER_REASONS
)


def default_delegates_dir(transcript: Any) -> Path:
    """The delegate directory a transcript implies: `<dir>/<stem>/subagents` (D2.1)."""
    path = Path(transcript)
    return path.parent / path.stem / "subagents"


# ---------------------------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------------------------


def _pct(part: float, whole: float) -> Optional[float]:
    """Percent of `whole`, or `None` for a zero denominator."""
    return 100.0 * part / whole if whole > 0 else None


def _row(row: CheckRow) -> Dict[str, str]:
    return {"name": row.name, "verdict": row.verdict, "detail": row.detail}


def _sum(values: Iterable[float]) -> float:
    return float(sum(values))


def _window_row(window: Any) -> Dict[str, Any]:
    """The facts of one window that every status shares."""
    return {
        "start_utc": format_ts(window.start_t_ms),
        "status": window.status,
        "lane": window.lane,
        "lane_source": window.lane_source,
        "reconstructable": window.reconstructable,
        "plan_only": window.plan_only,
        "suite_runs": window.suite_runs,
        "pairing_weak": window.pairing_weak,
        "first_missing_link": window.first_missing_link,
        "minutes_from_last_ts": window.minutes_from_last_ts,
        "end_marker": window.end_marker,
        "basis": window.basis,
    }


def absent_critical(transcript: Any) -> List[str]:
    """The CRITICAL shapes (D2.3) with count 0 that make a measurement NOT MEASURED.

    The three dispatch shapes are CRITICAL only when some `agent_dispatch` exists, and the
    notification pair needs only one of its two shapes.
    """
    counts = transcript.shapes
    dispatch = counts.get("agent_dispatch", 0) > 0
    notified = counts.get("task_enqueue", 0) + counts.get("task_delivery", 0) > 0
    out: List[str] = []
    for name, (kind, _needed) in SHAPE_TABLE.items():
        if kind != "CRITICAL" or counts.get(name, 0) > 0:
            continue
        if name in ("agent_ack", "task_enqueue", "task_delivery"):
            if not dispatch:
                continue
            if name != "agent_ack" and notified:
                continue
        out.append(name)
    return out


# ---------------------------------------------------------------------------------------------
# Profile blocks
# ---------------------------------------------------------------------------------------------


def _params_block(
    params: Params, overrides: Optional[Iterable[str]], given: Dict[str, bool]
) -> List[Dict[str, Any]]:
    if overrides is None:
        defaults = Params()
        overrides = [
            f for f in Params._fields if getattr(params, f) != getattr(defaults, f)
        ]
    sources = param_sources(overrides)
    rows = [
        {"name": f, "value": getattr(params, f), "source": sources[f]}
        for f in Params._fields
    ]
    for name in PATH_PARAMS:
        rows.append(
            {
                "name": name,
                "value": "given" if given[name] else "none",
                "source": "cli" if given[name] else "default",
            }
        )
    return rows


def _input_block(transcript: Any) -> Dict[str, Any]:
    snap = transcript.snapshot
    return {
        "records": snap.records,
        "parsed": snap.records - transcript.unparseable,
        "timestamped_pct": timestamped_pct(transcript),
        "unparseable": transcript.unparseable,
        "untimed": transcript.untimed,
        "inversions": transcript.inversions,
        "last_ts": snap.last_ts,
        "sha256": snap.sha256,
        "bytes": snap.bytes,
        "pinned": snap.pinned,
    }


def _shapes_block(transcript: Any) -> List[Dict[str, Any]]:
    return [
        {
            "name": name,
            "count": transcript.shapes.get(name, 0),
            "class": SHAPE_TABLE[name][0],
            "needed_by": SHAPE_TABLE[name][1],
            "gap": name in transcript.gaps,
        }
        for name in SHAPES
    ]


def _versions_block(transcript: Any) -> List[Dict[str, Any]]:
    out = []
    for row in transcript.versions:
        counts = dict(row.shapes)
        counts.update(row.kinds)
        out.append(
            {
                "version": row.version,
                "records": row.records,
                "timestamped_pct": _pct(row.timestamped, row.records),
                "counts": counts,
                "gaps": sorted(row.gaps),
            }
        )
    return out


def _cycles_block(cycle_set: Any, class_set: Any, split_result: Any) -> Dict[str, Any]:
    completed = [w for w in cycle_set.windows if w.status == "completed"]
    listed = []
    for index, window in enumerate(completed):
        cycle = class_set.cycles[index]
        row = _window_row(window)
        row.update(
            {
                "wall_s": (window.end_t_ms - window.start_t_ms) / 1000.0,
                "classes_s": dict(cycle.class_s),
                "hidden_under_machine_wait_s": cycle.hidden_under_machine_wait_s,
                "agent_seconds": cycle.agent_seconds,
                "stages": [
                    {
                        "stage": s.stage,
                        "wall_s": s.wall_s,
                        "classes_s": dict(s.classes_s),
                    }
                    for s in cycle.stages
                ],
                "split_s": dict(split_result.cycles[index]),
            }
        )
        listed.append(row)
    return {
        "completed": len(completed),
        "partial": [_window_row(w) for w in cycle_set.windows if w.status == "partial"],
        "open": [_window_row(w) for w in cycle_set.windows if w.status == "open"],
        "list": listed,
    }


def _min_cycles_met(completed: int, params: Params) -> bool:
    """Whether the completed cycles reach `--min-cycles` (the floor and the answer share it)."""
    return completed >= params.min_cycles


def _pool_block(pool: Any, completed: int, params: Params) -> Dict[str, Any]:
    """The pool block; below `--min-cycles` the answer is `undetermined` (spec D2.1)."""
    return {
        "n": completed,
        "wall_all_s": pool.wall_all_s,
        "wall_active_s": pool.wall_active_s,
        "away_s": pool.away_s,
        "classes_s": dict(pool.classes_s),
        "halves": pool.halves,
        "placements": list(pool.placements),
        "distinct_evaluations": pool.distinct_evaluations,
        "answer": pool.answer if _min_cycles_met(completed, params) else "undetermined",
        "corners": list(pool.corners),
        "ranking": list(pool.ranking),
        "repeat": list(pool.repeat),
    }


def _fence_blocks(class_set: Any, robust: Any) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    fence = class_set.fence
    idle = {
        "gaps": len(class_set.idle_gaps_s),
        "gaps_s": [float(g) for g in class_set.idle_gaps_s],
        "q1_s": None if fence.q1 is None else 10**fence.q1,
        "q3_s": None if fence.q3 is None else 10**fence.q3,
    }
    out = {
        "k": fence.k,
        "method": fence.method,
        "fitted": fence.inner_s is not None,
        "inner_s": fence.inner_s,
        "outer_s": fence.outer_s,
        "above": robust.above_base,
        "leave_one_out_min_s": robust.min_s,
        "leave_one_out_max_s": robust.max_s,
        "robust": robust._asdict(),
    }
    return idle, out


def _delegates_block(delegate_set: Any) -> Dict[str, Any]:
    counts = call_counts(delegate_set)
    cov = coverage(delegate_set)
    return {
        "matched": delegate_set.matched,
        "total": delegate_set.total,
        "orphans": len(delegate_set.orphans),
        "weak": cov.weak,
        "files": counts.files,
        "runlong_calls": counts.runlong_calls,
        "background_calls": counts.background_calls,
        "inventory": [dict(x) for x in delegate_set.inventory],
    }


def _sensitivity_block(
    cycle_set: Any, transcript: Any, params: Params, delegate_set: Any
) -> Dict[str, Any]:
    rows = sensitivity(cycle_set, transcript, params)
    pooled: Dict[str, float] = {}
    for one in no_separation_class_s(cycle_set, transcript, params):
        for name, secs in one.items():
            pooled[name] = pooled.get(name, 0.0) + secs
    swap = priority_sensitivity(cycle_set, transcript, params, delegate_set)
    return {
        "sets": [
            {
                "name": r.name,
                "rerun_min": r.rerun_min,
                "post_min": r.post_min,
                "min_suite_min": r.min_suite_min,
                "kept": r.kept,
                "moved": r.moved,
                "classes_s": dict(r.classes_s),
            }
            for r in rows
        ],
        "hook": [h._asdict() for h in hook_sensitivity(cycle_set, transcript, params)],
        "priority": {"primary": dict(swap.primary), "swapped": dict(swap.swapped)},
        "no_separation": pooled,
    }


def _external_blocks(
    cycle_set: Any, artifacts: Sequence[Any], repo: Any
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    art = artifact_end_check(cycle_set, artifacts)
    bound = commit_time_lower_bound(cycle_set, repo)
    return (
        {
            "status": art.status,
            "agree": art.agree,
            "disagree": art.disagree,
            "unpaired": art.unpaired,
            "absent": art.absent,
        },
        {
            "status": bound.status,
            "ok": bound.ok,
            "violated": bound.violated,
            "unknown": bound.unknown,
            "same_second": bound.same_second,
        },
    )


def _indicators(
    context: Any, cuts: Sequence[Any], transcript: Any, params: Params
) -> Dict[str, Any]:
    suite = []
    for cut in cuts:
        one = suite_machine_wait(cut)._asdict()
        one["feature_run_s"] = list(one["feature_run_s"])
        one["dev_run_s"] = list(one["dev_run_s"])
        suite.append(one)
    return {
        "order": [order_indicators(context, w)._asdict() for w in context.windows],
        "suite_mach": suite,
        "suite_band_min": list(SUITEMACH_BAND_MIN),
        "subcheck": subcheck(context)._asdict(),
        "dominant_hook": ck.dominant_hook_call(
            transcript, params.hook_latency_s
        )._asdict(),
    }


def _not_measured(
    transcript: Any,
    absent: Sequence[str],
    fence_fitted: bool,
    pool: Any,
    repo: Any,
    external: Tuple[Dict[str, Any], Dict[str, Any]],
) -> List[Dict[str, Any]]:
    def entry(
        what: str, name: Optional[str], reason: str, needed: Optional[str] = None
    ):
        return {"what": what, "name": name, "reason": reason, "needed_by": needed}

    out = [entry("shape", n, "absent", SHAPE_TABLE[n][1]) for n in absent]
    for name in LISTED_CROSS_CHECKS:
        if transcript.shapes.get(name, 0) == 0:
            out.append(entry("cross-check", name, "shape-absent", SHAPE_TABLE[name][1]))
    if not fence_fitted:
        out.append(entry("away", None, "below-min-gaps", "fence"))
    for number in CORNER_NOT_MEASURED:
        out.append(entry("corner", "corner-%d" % number, "no-class"))
    out.append(entry("class", "stage-order", "no-class"))
    out.append(entry("class", "signing-prompt", "no-class"))
    if repo is None:
        out.append(entry("repeat", None, "no-repo"))
    else:
        for reason in sorted(
            {x["reason"] for x in pool.repeat if x["status"] != "measured"}
        ):
            out.append(entry("repeat", None, reason))
    if external[1]["status"] == "unmeasured":
        out.append(entry("commit-bound", None, "no-repo"))
    if external[0]["status"] == "unmeasured":
        out.append(entry("artifact-end", None, "no-artifacts"))
    return out


def _limits_block(
    transcript: Any,
    context: Any,
    class_set: Any,
    cycle_set: Any,
    split_result: Any,
    pool: Any,
    delegate_set: Any,
    robust: Any,
    sens: Dict[str, Any],
    indicators: Dict[str, Any],
    manifest: Optional[Dict[str, Any]],
    hook_total: Tuple[int, float],
) -> Dict[str, Dict[str, Any]]:
    windows = context.windows
    cycles = list(class_set.cycles)
    snap = transcript.snapshot
    arm = [long_call_arm(context, w) for w in windows]
    dom = indicators["dominant_hook"]
    gaps = len(class_set.idle_gaps_s)
    unlabelled = unlabelled_machine_wait(pool)
    evidence = corner3_evidence(delegate_set)
    halves = pool.halves
    lanes = Counter((w.lane, w.lane_source) for w in windows)
    all_windows = cycle_set.windows
    return {
        "L01": {
            "records": snap.records,
            "timestamped_pct": timestamped_pct(transcript),
            "inversions": transcript.inversions,
        },
        "L02": {
            "hidden_under_machine_wait_s": _sum(
                c.hidden_under_machine_wait_s for c in cycles
            ),
            "stalled_recorded_s": _sum(
                o["stalled_recorded_s"] for o in indicators["order"]
            ),
            "stalled_any_s": _sum(o["stalled_any_s"] for o in indicators["order"]),
        },
        "L03": {
            "sets": [
                {"name": s["name"], "kept": s["kept"], "moved": s["moved"]}
                for s in sens["sets"]
            ]
        },
        "L04": {
            "gaps": gaps,
            "inner_s": class_set.fence.inner_s,
            "leave_one_out_min_s": robust.min_s,
            "leave_one_out_max_s": robust.max_s,
            "pct_above": _pct(robust.above_base, gaps),
            "smallest_above_pct": robust.smallest_above_pct,
        },
        "L05": {
            "calls": hook_total[0],
            "union_s": hook_total[1],
            "largest_s": dom["largest_ms"] / 1000.0,
            "share_pct": dom["share_pct"],
            "hook_reported_s": hook_reported_s(context.events, 0, FAR_MS),
        },
        "L06": {
            "calls": sum(a.calls for a in arm),
            "total_s": _sum(a.total_s for a in arm),
            "check_s": _sum(a.check_s for a in arm),
            "other_s": _sum(a.other_s for a in arm),
        },
        "L07": {
            "roles": dict(sorted(Counter(d.role for d in class_set.dispatches).items()))
        },
        "L08": {
            "matched": delegate_set.matched,
            "total": delegate_set.total,
            "unsplit_s": _sum(row["SUBAGENT_UNSPLIT"] for row in split_result.cycles),
        },
        "L09": {
            "distinct_evaluations": pool.distinct_evaluations,
            "leads": lead_counts(pool.placements, "lead"),
        },
        "L10": {"inside_subagent_leads": lead_counts(pool.placements, "sub_lead")},
        "L11": {
            "s": None if unlabelled is None else unlabelled.s,
            "pct_all": None if unlabelled is None else unlabelled.pct_all,
            "pct_active": None if unlabelled is None else unlabelled.pct_active,
        },
        "L12": {
            "files": evidence.files,
            "runlong_calls": evidence.runlong_calls,
            "background_calls": evidence.background_calls,
            "exact": evidence.exact,
        },
        "L13": {
            "lanes": [
                {"lane": lane, "source": source, "n": n}
                for (lane, source), n in sorted(
                    lanes.items(), key=lambda kv: (str(kv[0][0]), str(kv[0][1]))
                )
            ]
        },
        "L14": {
            "completed": len(windows),
            "partial": sum(1 for w in all_windows if w.status == "partial"),
            "open": sum(1 for w in all_windows if w.status == "open"),
            "bases": dict(sorted(Counter(str(w.basis) for w in windows).items())),
        },
        "L15": {
            "turn_s": _sum(turn_duration_s(context, w) for w in windows),
            "controller_s": pool.classes_s["CONTROLLER"],
        },
        "L16": {},
        "L17": {
            "independent": None if manifest is None else manifest["independent"],
            "guaranteed": None if manifest is None else manifest["guaranteed"],
            "floor_decided": None if manifest is None else manifest["floor_decided"],
        },
        "L18": {
            "work_pct_active": halves["active"]["work_pct"],
            "work_pct_active_hook_work": halves["active_hook_work"]["work_pct"],
        },
    }


def _floors(
    params: Params,
    completed: int,
    transcript: Any,
    delegate_set: Any,
    absent: Sequence[str],
    checks: Sequence[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    unparsed = unparseable_pct(transcript)
    timed = timestamped_pct(transcript)
    cov = coverage(delegate_set)
    present = len({row["name"] for row in checks} & set(ck.CHECK_NAMES))
    return [
        {
            "name": "min_cycles",
            "want": params.min_cycles,
            "actual": completed,
            "ok": _min_cycles_met(completed, params),
        },
        {
            "name": "max_unparseable_pct",
            "want": params.max_unparseable_pct,
            "actual": unparsed,
            "ok": unparsed is not None and unparsed <= params.max_unparseable_pct,
        },
        {
            "name": "min_timestamped_pct",
            "want": params.min_timestamped_pct,
            "actual": timed,
            "ok": timed is not None and timed >= params.min_timestamped_pct,
        },
        {
            "name": "coverage",
            "want": COVERAGE_WANT_PCT,
            "actual": _pct(cov.matched, cov.total),
            "ok": not cov.weak,
        },
        {
            "name": "critical_shapes",
            "want": 0,
            "actual": len(absent),
            "ok": len(absent) == 0,
        },
        {
            "name": "checks_run",
            "want": ck.CHECK_FLOOR,
            "actual": present,
            "ok": present >= ck.CHECK_FLOOR,
        },
    ]


# ---------------------------------------------------------------------------------------------
# The pipeline
# ---------------------------------------------------------------------------------------------


def run_profile(
    transcript: Any,
    params: Optional[Params] = None,
    *,
    delegates_dir: Any = None,
    artifacts: Sequence[Any] = (),
    repo: Any = None,
    manifest_rows: Optional[Sequence[Any]] = None,
    prefix: Optional[Tuple[int, str]] = None,
    delegate_pins: Optional[Iterable[Any]] = None,
    overrides: Optional[Iterable[str]] = None,
) -> Dict[str, Any]:
    """Read one transcript once and return its profile (layout v1, plain data).

    Args:
        transcript: Path of the session transcript.
        params: The run's parameters; `Params()` when omitted. The three `leak_*` fields
            are not consumed here.
        delegates_dir: The delegate directory; `<transcript dir>/<stem>/subagents` when
            omitted (a missing or empty one matches nothing).
        artifacts: Run-long artifact directories for the END cross-check.
        repo: A git repository for REPEAT and the commit-time bound, or `None`.
        manifest_rows: The parsed `--manifest` rows, or `None`. Validated before the
            transcript is read.
        prefix: `(bytes, sha256)` pinning the transcript to a verified prefix.
        delegate_pins: `{id, bytes, sha256}` entries pinning delegate files.
        overrides: The `Params` fields the operator set explicitly (the `cli` source);
            by default the fields that differ from their defaults.

    Returns:
        The profile dict. Its `checks[]` hold every data-dependent check row and its
        `floors[]` every floor; `derive_verdict` turns them into the verdict. `leak` is
        `None` (the CLI fills it).

    Raises:
        ManifestError: A manifest row is invalid (raised before anything is read).
        ReadError: The transcript could not be read.
        PinError: The prefix or a delegate pin does not hold.
        ValueError: A pin or an override is malformed.
    """
    params = Params() if params is None else params
    rows = None if manifest_rows is None else list(manifest_rows)
    if rows is not None:
        validate_rows(rows)
    if prefix is None:
        data = read_transcript(transcript)
    else:
        data = read_transcript(
            transcript, prefix_bytes=prefix[0], prefix_sha256=prefix[1]
        )
    cycle_set = analyze_cycles(data, params)
    class_set = classify(cycle_set, data, params)
    directory = (
        default_delegates_dir(transcript) if delegates_dir is None else delegates_dir
    )
    dispatches = cycle_dispatches(class_set.dispatches, cycle_set.windows)
    delegate_set = read_delegates(directory, dispatches, params, pins=delegate_pins)
    split_result = split(cycle_set, data, params, delegate_set)
    inputs = check_inputs(cycle_set, data, params)
    pool = pool_results(
        cycle_set,
        class_set,
        delegate_set,
        params,
        repo=repo,
        split=split_result,
        wait_by_check=inputs.wait_by_check,
    )
    context = build_context(cycle_set, data, params)
    cuts = [stage_cut(context, w) for w in context.windows]
    robust = fence_robust(context)
    hook_total = hook_union_total(data, params.hook_latency_s * 1000)
    absent = absent_critical(data)
    sens = _sensitivity_block(cycle_set, data, params, delegate_set)
    indicators = _indicators(context, cuts, data, params)
    external = _external_blocks(cycle_set, artifacts, repo)
    indicators["artifact_end"], indicators["commit_bound"] = external
    idle, fence = _fence_blocks(class_set, robust)
    given = {
        "delegates": delegates_dir is not None,
        "artifacts": len(list(artifacts)) > 0,
        "repo": repo is not None,
        "manifest": manifest_rows is not None,
        "prefix": prefix is not None,
        "delegate_pins": delegate_pins is not None,
    }
    completed = len(context.windows)
    profile: Dict[str, Any] = {
        "tool": {"version": TOOL_VERSION},
        "input": _input_block(data),
        "params": _params_block(params, overrides, given),
        "shapes": _shapes_block(data),
        "versions": _versions_block(data),
        "floors": [],
        "cycles": _cycles_block(cycle_set, class_set, split_result),
        "pool": _pool_block(pool, completed, params),
        "idle": idle,
        "fence": fence,
        "delegates": _delegates_block(delegate_set),
        "sensitivity": sens,
        "checks": [],
        "manifest": None,
        "leak": None,
        "limits": {},
        "not_measured": _not_measured(
            data, absent, fence["fitted"], pool, repo, external
        ),
        "indicators": indicators,
    }
    manifest = None if rows is None else evaluate_manifest(profile, rows)
    profile["manifest"] = manifest
    limits = _limits_block(
        data,
        context,
        class_set,
        cycle_set,
        split_result,
        pool,
        delegate_set,
        robust,
        sens,
        indicators,
        manifest,
        hook_total,
    )
    # one block per limit id, in id order; a missing id is a KeyError, never a silent gap
    profile["limits"] = {name: limits[name] for name in LIMIT_IDS}
    checks = [
        ck.check_sumcheck(cycle_set, class_set),
        ck.check_classoracle(context, class_set),
        ck.check_stagecheck(cycle_set, cuts),
        ck.check_paircheck(cycle_set, data),
        ck.check_splitcheck(class_set, split_result),
        ck.check_intervalcheck(context),
        ck.check_armcheck(split_result),
        *ck.check_poolcheck(pool, inputs.stage_bucket_s),
        ck.check_halfcheck(pool),
        ck.check_idlecheck(context, data),
        ck.check_fencecheck(class_set, params),
        ck.check_fence_robust(class_set, robust, params),
        ck.check_hookcheck(data, context, hook_total),
        ck.check_mapcheck(class_set),
        ck.check_denomcheck(profile),
        manifest_check(manifest),
        zone_check(manifest),
    ]
    profile["checks"] = [_row(row) for row in checks]
    profile["floors"] = _floors(
        params, completed, data, delegate_set, absent, profile["checks"]
    )
    return profile


# ---------------------------------------------------------------------------------------------
# The verdict
# ---------------------------------------------------------------------------------------------


def _require_complete(profile: Dict[str, Any]) -> None:
    """Refuse a profile whose rows are not the ones `run_profile` always writes.

    A profile with no check rows, with floors other than exactly the `FLOOR_REASONS` names
    (each once), or with a floor `ok` that is not a bool has not been judged, so it must not
    derive PASS. This is malformed input, not an unmeasured quantity: INCOMPLETE says "it ran,
    every check held", so the fault is raised as `ValueError` (the CLI maps an internal
    exception to ERROR, D1) and no new reason token is needed.

    Raises:
        ValueError: The profile lacks check rows or floors, or a floor `ok` is not a bool.
    """
    if not profile["checks"]:
        raise ValueError("a profile holds check rows")
    names = sorted(floor["name"] for floor in profile["floors"])
    if names != sorted(FLOOR_REASONS):
        raise ValueError("a profile holds each floor exactly once")
    if any(not isinstance(floor["ok"], bool) for floor in profile["floors"]):
        raise ValueError("a floor is met or not met")


def incomplete_reasons(profile: Dict[str, Any], extra: Iterable[str] = ()) -> List[str]:
    """The closed `REASONS` tokens that make a run INCOMPLETE, in a stable order.

    Args:
        profile: A `run_profile` result.
        extra: Reasons only the caller can know (the `CALLER_REASONS`).

    Raises:
        ValueError: A token in `extra` is not a caller reason, or the profile is
            malformed (see `_require_complete`).
    """
    _require_complete(profile)
    reasons: List[str] = []
    if profile["cycles"]["completed"] == 0:
        reasons.append("no-completed-cycle")
    for floor in profile["floors"]:
        if not floor["ok"]:
            token = FLOOR_REASONS[floor["name"]]
            if token not in reasons:
                reasons.append(token)
    for token in extra:
        if token not in CALLER_REASONS:
            raise ValueError("unknown INCOMPLETE reason")
        if token not in reasons:
            reasons.append(token)
    return reasons


def _verdict_of(row: Any) -> str:
    verdict = row["verdict"] if isinstance(row, dict) else row.verdict
    if verdict not in CHECK_VERDICTS:
        raise ValueError("a check row verdict is pass, fail or skip")
    return verdict


def derive_verdict(
    profile: Dict[str, Any],
    extra_rows: Iterable[Any] = (),
    *,
    error: bool = False,
    incomplete: Iterable[str] = (),
) -> Tuple[str, int, int, int, int]:
    """The final verdict and the `checks=p/f/s` counts: the one place they are computed.

    Args:
        profile: A `run_profile` result.
        extra_rows: The post-render check rows (`CheckRow` or dicts) the CLI adds.
        error: The caller classed the run ERROR (a post-render condition; exceptions are
            mapped by the CLI itself).
        incomplete: Caller-known INCOMPLETE reasons (`CALLER_REASONS`).

    Returns:
        `(verdict, rc, pass, fail, skip)`. Precedence ERROR > FAIL > INCOMPLETE > PASS.
        The counts cover the profile's check rows, the extra rows and every floor (met is
        a pass, unmet is a skip).

    Raises:
        ValueError: A row verdict or an INCOMPLETE reason is outside its closed set, or
            the profile is malformed (see `_require_complete`).
    """
    counts = Counter()
    for row in list(profile["checks"]) + list(extra_rows):
        counts[_verdict_of(row)] += 1
    for floor in profile["floors"]:
        counts["pass" if floor["ok"] else "skip"] += 1
    reasons = incomplete_reasons(profile, incomplete)
    if error:
        verdict = "ERROR"
    elif counts["fail"] > 0:
        verdict = "FAIL"
    elif reasons:
        verdict = "INCOMPLETE"
    else:
        verdict = "PASS"
    return verdict, RC[verdict], counts["pass"], counts["fail"], counts["skip"]


__all__ = [
    "CALLER_REASONS",
    "FLOOR_REASONS",
    "REASONS",
    "TOOL_VERSION",
    "absent_critical",
    "default_delegates_dir",
    "derive_verdict",
    "incomplete_reasons",
    "run_profile",
]

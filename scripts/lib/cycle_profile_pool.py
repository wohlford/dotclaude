"""Pooled figures of the completed `/feature` cycles: halves, placements, answer, corners.

Takes RESULT OBJECTS from the earlier modules (the cycle set, the class set, the delegate set
and the delegates' split) and never reads rendered text. It renders nothing: the report
module formats every figure here. Times are seconds, shares are percent of a denominator.

**Pooling.** Every `completed` window of the cycle set is pooled (a full or a partial
reconstruction alike); `partial` and `open` windows are not. `class_set.cycles` follow the
completed windows in order, so the two lists must have the same length. `wall_all_s` is the
sum of the windows' own `end - start`, `away_s` the pooled AWAY class and `wall_active_s` the
difference. ACTIVE means ALL minus AWAY (the denominator of the ANSWER); ALL is the whole
wall, AWAY included, and is reported beside it.

**The halves.** WORK is CONTROLLER plus SUBAGENT_WORK; GATE is MACHINE_WAIT plus
SUBAGENT_GATE (the run-long arm and the long-call arm), HOOK and SUBAGENT_HOOK; OPERATOR is
OPERATOR_RESPONSE; SUBAGENT_UNSPLIT is its own bucket. The split comes from
`cycle_profile_delegates.split`; with no `split` every SUBAGENT second is UNSPLIT, never zero
minutes, and a delegate set that matched files without a `split` is refused (the caller
forgot it) rather than read as all-unsplit.

**The twelve placements** (`PLACEMENTS`) move three uncertain buckets between WORK and GATE:
HOOK together with SUBAGENT_HOOK (`gate` or `work`), SUBAGENT_UNSPLIT (`work` or `gate`) and
the delegates' long-call arm (`as-classified`: the check-token and other-token calls both
GATE; `whole-arm-WORK`: both WORK; `other-token-WORK`: only the other-token calls WORK).
They are evaluations, not twelve independent tests: `distinct_evaluations` counts how many
give different numbers. Each placement carries the lead over the whole ACTIVE wall (WORK,
GATE, OPERATOR) and the lead inside SUBAGENT alone (WORK against GATE). Only the whole-wall
lead decides the ANSWER: `gate` or `work` when all twelve placements agree on it,
`undetermined` when any placement differs, ties, or has the operator leading. The
inside-SUBAGENT lead is carried as a limit (L10).

**The corners and the ranking.** One ranking holds every measured bucket exactly once. The
mapped corners are 1 (MACHINE_WAIT of the feature-tip and dev-tip suites), 2 (per-brick
sweep), 3 (the delegates' run-long arm, an upper bound), 5 (HOOK plus SUBAGENT_HOOK) and 7
(OPERATOR_RESPONSE); every other bucket stays in the ranking as UNMAPPED. Corners 4 and 6
have no class and are not measured. MACHINE_WAIT is split by CHECK CLASS (`CHECK_CLASSES`),
which needs the stage cut: `check_inputs` derives it from the transcript and the caller
passes it as `wait_by_check`.

**Two bucket kinds, never one.** `STAGE_BUCKETS` (the stages plus the one `other` bucket for
an unmapped dispatch) and `CHECK_CLASSES` are distinct constants. `MACHINE_WAIT[other]` is a
check class (`UNLABELLED-MACHINE-WAIT`, L11) and never counts against the unmapped-bucket
rule, which `poolcheck` applies to stage buckets only.

**REPEAT.** For each completed cycle with a feature-tip and a dev-tip suite label sha, both
shas are resolved to a tree id in the `repo` (read-only `git rev-parse`, no `GIT_*`
variable inherited); the dev-tip suite is REPEAT-measured when the two trees are identical.
Every other cycle is `unmeasured` with a closed reason, and with no repo all are.
"""

from __future__ import annotations

import os
import re
import subprocess
from typing import Any, Dict, List, NamedTuple, Optional, Sequence, Tuple

from cycle_profile_classes import (
    build_context,
    machine_wait_by_check,
    stage_bucket_s,
    stage_cut,
)
from cycle_profile_delegates import SPLIT_KEYS, call_counts
from cycle_profile_model import (
    CHECK_CLASSES,
    CLASSES,
    STAGE_BUCKETS,
    STAGES,
    CheckRow,
    PoolResult,
)

#: Where the three uncertain buckets may sit; the twelve placements are their product.
HOOK_PLACEMENTS: Tuple[str, ...] = ("gate", "work")
UNSPLIT_PLACEMENTS: Tuple[str, ...] = ("work", "gate")
ARM_PLACEMENTS: Tuple[str, ...] = (
    "as-classified",
    "whole-arm-WORK",
    "other-token-WORK",
)
PLACEMENTS: Tuple[Tuple[str, str, str], ...] = tuple(
    (hook, unsplit, arm)
    for arm in ARM_PLACEMENTS
    for hook in HOOK_PLACEMENTS
    for unsplit in UNSPLIT_PLACEMENTS
)

#: The check classes whose MACHINE_WAIT maps to a corner; the corner of every other bucket.
CHECK_CORNER: Dict[str, int] = {
    "suite-feature-tip": 1,
    "suite-dev-tip": 1,
    "per-brick-sweep": 2,
}
#: The mapped corners, in order. Corners 4 (stage order) and 6 (signing) have no class.
MAPPED_CORNERS: Tuple[int, ...] = (1, 2, 3, 5, 7)
#: The corners fed by MACHINE_WAIT check classes (unmeasured without `wait_by_check`).
MACHINE_CORNERS: Tuple[int, ...] = tuple(sorted(set(CHECK_CORNER.values())))
CORNER_NOT_MEASURED: Tuple[int, ...] = (4, 6)

#: POOLCHECK tolerances: the ranked seconds plus AWAY meet the wall within one minute, and
#: the stage buckets outside `STAGES` may hold at most this percent of the pooled ALL wall.
ARITH_TOL_S = 60.0
#: Leads compare seconds rounded to this many decimals, so float noise never breaks a tie.
LEAD_DECIMALS = 3
UNMAPPED_STAGE_MAX_PCT = 10.0

#: Closed reasons of a REPEAT entry that is `unmeasured`.
REPEAT_REASONS: Tuple[str, ...] = (
    "no-repo",
    "no-label-sha",
    "no-terminal",
    "unresolved",
    "trees-differ",
)

#: The bucket names the ranking must hold once each, whatever the data.
CORE_BUCKETS: Tuple[str, ...] = (
    "CONTROLLER",
    "SUBAGENT_WORK",
    "GATE_RUNLONG",
    "GATE_LONGCALL_CHECK",
    "GATE_LONGCALL_OTHER",
    "SUBAGENT_HOOK",
    "SUBAGENT_UNSPLIT",
    "HOOK",
    "OPERATOR_RESPONSE",
)
#: The MACHINE_WAIT bucket used when the caller gave no per-check-class figures.
UNATTRIBUTED = "MACHINE_WAIT[unattributed]"

GIT_TIMEOUT_S = 30
_SHA_RE = re.compile(r"[0-9a-f]{7,40}")
_TREE_RE = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")


class Parts(NamedTuple):
    """The pooled seconds the halves and placements are built from."""

    ctrl: float
    mw: float
    hook: float
    opr: float
    subw: float
    rl: float
    lcc: float
    lco: float
    subh: float
    uns: float


class CheckInputs(NamedTuple):
    """Pooled MACHINE_WAIT by check class and pooled wall by stage bucket."""

    wait_by_check: Dict[str, float]
    stage_bucket_s: Dict[str, float]


class Unlabelled(NamedTuple):
    """`MACHINE_WAIT[other]`: run-long checks with no stage label (L11)."""

    s: float
    pct_all: Optional[float]
    pct_active: Optional[float]


class Corner3Evidence(NamedTuple):
    """Delegate-file call counts for corner 3's upper bound (L12).

    `exact` is `True` when no delegate Bash call ran in the background, `False` otherwise
    and `None` when no delegate file was read (nothing measured).
    """

    files: int
    runlong_calls: int
    background_calls: int
    exact: Optional[bool]


class TopCorners(NamedTuple):
    """The two largest mapped corners and the unmapped buckets larger than the second."""

    first: str
    second: str
    larger_unmapped: Tuple[str, ...]


def _pct(part: float, whole: float) -> Optional[float]:
    """Percent of `whole`, or `None` for a zero denominator (never a silent 0)."""
    return part / whole * 100.0 if whole > 0 else None


# ---------------------------------------------------------------------------------------------
# Parts, placements, the answer
# ---------------------------------------------------------------------------------------------


def _split_rows(split: Any, count: int) -> List[Dict[str, float]]:
    rows = list(split.cycles)
    if len(rows) != count:
        raise ValueError(
            "split holds %d cycles for %d completed windows" % (len(rows), count)
        )
    for one in rows:
        if any(key not in one for key in SPLIT_KEYS):
            raise ValueError("split row lacks a SPLIT_KEYS entry")
    return rows


def pooled_parts(
    cycles: Sequence[Any], rows: Optional[Sequence[Dict[str, float]]]
) -> Parts:
    """Sum the per-cycle class seconds and split rows into `Parts`.

    With `rows` None every SUBAGENT second is UNSPLIT and the other split parts are 0.
    """
    total = {key: 0.0 for key in SPLIT_KEYS}
    ctrl = mw = hook = opr = 0.0
    for index, cycle in enumerate(cycles):
        ctrl += cycle.class_s["CONTROLLER"]
        mw += cycle.class_s["MACHINE_WAIT"]
        hook += cycle.class_s["HOOK"]
        opr += cycle.class_s["OPERATOR_RESPONSE"]
        if rows is None:
            total["SUBAGENT_UNSPLIT"] += cycle.class_s["SUBAGENT"]
        else:
            for key in SPLIT_KEYS:
                total[key] += rows[index][key]
    return Parts(
        ctrl,
        mw,
        hook,
        opr,
        total["SUBAGENT_WORK"],
        total["GATE_RUNLONG"],
        total["LONGCALL_CHECK"],
        total["LONGCALL_OTHER"],
        total["SUBAGENT_HOOK"],
        total["SUBAGENT_UNSPLIT"],
    )


def place(
    p: Parts, hook: str, unsplit: str, arm: str
) -> Tuple[float, float, float, float, float]:
    """One placement: `(work, gate, operator, inside-SUBAGENT work, inside-SUBAGENT gate)`."""
    work = p.ctrl + p.subw
    gate = p.mw + p.rl
    sub_work = p.subw
    sub_gate = p.rl
    if arm == "as-classified":
        gate += p.lcc + p.lco
        sub_gate += p.lcc + p.lco
    elif arm == "whole-arm-WORK":
        work += p.lcc + p.lco
        sub_work += p.lcc + p.lco
    elif arm == "other-token-WORK":
        gate += p.lcc
        sub_gate += p.lcc
        work += p.lco
        sub_work += p.lco
    else:
        raise ValueError("unknown arm placement")
    if hook == "gate":
        gate += p.hook + p.subh
        sub_gate += p.subh
    else:
        work += p.hook + p.subh
        sub_work += p.subh
    if unsplit == "work":
        work += p.uns
        sub_work += p.uns
    else:
        gate += p.uns
        sub_gate += p.uns
    return work, gate, p.opr, sub_work, sub_gate


def lead3(work: float, gate: float, operator: float) -> str:
    """The largest of the three, or `tie` when the largest value is shared."""
    work, gate, operator = (round(x, LEAD_DECIMALS) for x in (work, gate, operator))
    best = max(work, gate, operator)
    leaders = [
        name
        for name, v in (("work", work), ("gate", gate), ("operator", operator))
        if v == best
    ]
    return leaders[0] if len(leaders) == 1 else "tie"


def lead2(work: float, gate: float) -> str:
    """The lead inside SUBAGENT alone; a tie reads `gate`, as the original's rule did."""
    return "work" if round(work, LEAD_DECIMALS) > round(gate, LEAD_DECIMALS) else "gate"


def evaluate(p: Parts) -> List[Dict[str, Any]]:
    """The twelve placements of `p`, numbered from 1 in `PLACEMENTS` order."""
    out: List[Dict[str, Any]] = []
    for number, (hook, unsplit, arm) in enumerate(PLACEMENTS, 1):
        work, gate, operator, sub_work, sub_gate = place(p, hook, unsplit, arm)
        out.append(
            {
                "n": number,
                "hook": hook,
                "unsplit": unsplit,
                "arm": arm,
                "work_s": work,
                "gate_s": gate,
                "operator_s": operator,
                "lead": lead3(work, gate, operator),
                "sub_work_s": sub_work,
                "sub_gate_s": sub_gate,
                "sub_lead": lead2(sub_work, sub_gate),
            }
        )
    return out


def distinct_count(placements: Sequence[Dict[str, Any]]) -> int:
    """How many of the placements give different numbers (to three decimals)."""
    keys = {
        tuple(
            round(x[f], 3)
            for f in ("work_s", "gate_s", "operator_s", "sub_work_s", "sub_gate_s")
        )
        for x in placements
    }
    return len(keys)


def decide(placements: Sequence[Dict[str, Any]]) -> str:
    """`gate` or `work` when every placement's whole-wall lead is that half, else `undetermined`."""
    leads = {x["lead"] for x in placements}
    if len(leads) == 1:
        lead = next(iter(leads))
        if lead in ("work", "gate"):
            return lead
    return "undetermined"


def lead_counts(
    placements: Sequence[Dict[str, Any]], key: str = "lead"
) -> Dict[str, int]:
    """Placements per lead value (`key` is `lead` or `sub_lead`), in sorted order."""
    out: Dict[str, int] = {}
    for x in placements:
        out[x[key]] = out.get(x[key], 0) + 1
    return dict(sorted(out.items()))


def _block(p: Parts, denominator: float, work: float, gate: float) -> Dict[str, Any]:
    return {
        "denominator_s": denominator,
        "work_s": work,
        "gate_s": gate,
        "operator_s": p.opr,
        "unsplit_s": p.uns,
        "work_pct": _pct(work, denominator),
        "gate_pct": _pct(gate, denominator),
        "operator_pct": _pct(p.opr, denominator),
        "unsplit_pct": _pct(p.uns, denominator),
        "parts_s": {
            "CONTROLLER": p.ctrl,
            "SUBAGENT_WORK": p.subw,
            "MACHINE_WAIT": p.mw,
            "SUBAGENT_GATE": p.rl + p.lcc + p.lco,
            "HOOK": p.hook,
            "SUBAGENT_HOOK": p.subh,
            "OPERATOR_RESPONSE": p.opr,
            "SUBAGENT_UNSPLIT": p.uns,
        },
    }


def halves_of(p: Parts, wall_all: float, away: float) -> Dict[str, Any]:
    """The primary halves over ACTIVE and over ALL, and the HOOK-as-work variant (L18)."""
    work = p.ctrl + p.subw
    gate = p.mw + p.rl + p.lcc + p.lco + p.hook + p.subh
    active = wall_all - away
    every = _block(p, wall_all, work, gate)
    every["away_s"] = away
    every["away_pct"] = _pct(away, wall_all)
    moved = p.hook + p.subh
    hook_work = _block(p, active, work + moved, gate - moved)
    return {
        "active": _block(p, active, work, gate),
        "all": every,
        "active_hook_work": {
            key: hook_work[key] for key in ("work_s", "gate_s", "work_pct", "gate_pct")
        },
    }


# ---------------------------------------------------------------------------------------------
# The ranking and the corners
# ---------------------------------------------------------------------------------------------


def _buckets(
    p: Parts, wait_by_check: Optional[Dict[str, float]]
) -> List[Tuple[str, float, Optional[int]]]:
    out: List[Tuple[str, float, Optional[int]]] = [
        ("CONTROLLER", p.ctrl, None),
        ("SUBAGENT_WORK", p.subw, None),
        ("GATE_RUNLONG", p.rl, 3),
        ("GATE_LONGCALL_CHECK", p.lcc, None),
        ("GATE_LONGCALL_OTHER", p.lco, None),
        ("SUBAGENT_HOOK", p.subh, 5),
        ("SUBAGENT_UNSPLIT", p.uns, None),
    ]
    if wait_by_check is None:
        out.append((UNATTRIBUTED, p.mw, None))
    else:
        for name in CHECK_CLASSES:
            out.append(
                (
                    "MACHINE_WAIT[%s]" % name,
                    wait_by_check.get(name, 0.0),
                    CHECK_CORNER.get(name),
                )
            )
    out.append(("HOOK", p.hook, 5))
    out.append(("OPERATOR_RESPONSE", p.opr, 7))
    return out


def ranking_of(
    p: Parts, wait_by_check: Optional[Dict[str, float]]
) -> Tuple[List[Dict[str, Optional[float]]], List[Dict[str, Any]]]:
    """`(corners, ranking)`: the mapped corners by number, and the one ranking by seconds.

    A corner whose seconds are `None` is unmeasured and has no ranking entry.
    """
    buckets = _buckets(p, wait_by_check)
    corners: List[Dict[str, Optional[float]]] = []
    items: List[Dict[str, Any]] = []
    for number in MAPPED_CORNERS:
        members = [b for b in buckets if b[2] == number]
        seconds = sum(b[1] for b in members)
        if wait_by_check is None and number in MACHINE_CORNERS:
            # The per-check machine wait was not supplied, so this corner is unmeasured
            # (its seconds sit in the unmapped MACHINE_WAIT[unattributed] bucket).
            corners.append({"n": number, "s": None})
            continue
        corners.append({"n": number, "s": seconds})
        items.append(
            {
                "name": "corner-%d" % number,
                "kind": "mapped",
                "corner": number,
                "s": seconds,
                "members": [b[0] for b in members],
            }
        )
    for name, seconds, corner in buckets:
        if corner is None:
            items.append(
                {
                    "name": name,
                    "kind": "unmapped",
                    "corner": None,
                    "s": seconds,
                    "members": [name],
                }
            )
    items.sort(key=lambda x: (-x["s"], x["name"]))
    return corners, items


def top_corners(pool: PoolResult) -> TopCorners:
    """The two largest mapped corners and every unmapped bucket larger than the second."""
    mapped = [x for x in pool.ranking if x["kind"] == "mapped"]
    second = mapped[1]
    larger = tuple(
        x["name"]
        for x in pool.ranking
        if x["kind"] == "unmapped" and x["s"] > second["s"]
    )
    return TopCorners(mapped[0]["name"], second["name"], larger)


def unlabelled_machine_wait(pool: PoolResult) -> Optional[Unlabelled]:
    """`MACHINE_WAIT[other]` with its shares, or `None` when no per-check figures were given."""
    for item in pool.ranking:
        if item["name"] == UNATTRIBUTED:
            return None
    seconds = sum(
        x["s"]
        for x in pool.ranking
        if x["kind"] == "unmapped" and x["name"] == "MACHINE_WAIT[other]"
    )
    return Unlabelled(
        seconds, _pct(seconds, pool.wall_all_s), _pct(seconds, pool.wall_active_s)
    )


def corner3_evidence(delegate_set: Any) -> Corner3Evidence:
    """Run-long and background Bash calls in the delegate files read (L12)."""
    counts = call_counts(delegate_set)
    exact: Optional[bool] = None
    if counts.files > 0:
        exact = counts.background_calls == 0
    return Corner3Evidence(
        counts.files, counts.runlong_calls, counts.background_calls, exact
    )


def check_inputs(cycle_set: Any, transcript: Any, params: Any) -> CheckInputs:
    """Pool MACHINE_WAIT by check class and the wall by stage bucket over completed windows."""
    context = build_context(cycle_set, transcript, params)
    wait = {name: 0.0 for name in CHECK_CLASSES}
    buckets = {name: 0.0 for name in STAGE_BUCKETS}
    for window in context.windows:
        cut = stage_cut(context, window)
        for name, seconds in machine_wait_by_check(cut).items():
            wait[name] += seconds
        for name, seconds in stage_bucket_s(cut).items():
            buckets[name] += seconds
    return CheckInputs(wait, buckets)


# ---------------------------------------------------------------------------------------------
# REPEAT
# ---------------------------------------------------------------------------------------------


def _tree_of(repo: Any, sha: str) -> Optional[str]:
    """The tree id of a label sha via read-only `git rev-parse`, else `None`."""
    if not _SHA_RE.fullmatch(sha):
        return None
    if str(repo) == "" or "\0" in str(repo):
        return None
    env = _clean_env()
    cmd = [
        "git",
        "--no-optional-locks",
        "-C",
        str(repo),
        "rev-parse",
        "--verify",
        "-q",
        sha + "^{tree}",
    ]
    try:
        done = subprocess.run(
            cmd,
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    text = done.stdout.strip()
    return text if done.returncode == 0 and _TREE_RE.fullmatch(text) else None


def _clean_env() -> Dict[str, str]:
    """The caller's environment without any `GIT_*` variable."""
    return {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}


def _unmeasured(
    index: int, reason: str, fsha: Any = None, dsha: Any = None
) -> Dict[str, Any]:
    return {
        "cycle": index,
        "status": "unmeasured",
        "reason": reason,
        "feature_sha": fsha,
        "dev_sha": dsha,
        "tree": None,
        "dev_tip_suite_s": None,
    }


def repeat_results(windows: Sequence[Any], repo: Any) -> List[Dict[str, Any]]:
    """One REPEAT entry per completed window (see the module docstring)."""
    cache: Dict[str, Optional[str]] = {}
    out: List[Dict[str, Any]] = []
    for index, win in enumerate(windows):
        if repo is None:
            out.append(_unmeasured(index, "no-repo"))
            continue
        fsha = getattr(win.f1, "label_sha", None)
        dsha = getattr(win.d1, "label_sha", None)
        if not (fsha and dsha):
            out.append(_unmeasured(index, "no-label-sha", fsha, dsha))
            continue
        for sha in (fsha, dsha):
            if sha not in cache:
                cache[sha] = _tree_of(repo, sha)
        ftree, dtree = cache[fsha], cache[dsha]
        if ftree is None or dtree is None:
            out.append(_unmeasured(index, "unresolved", fsha, dsha))
        elif ftree != dtree:
            out.append(_unmeasured(index, "trees-differ", fsha, dsha))
        elif win.d1.terminal_ms is None:
            out.append(_unmeasured(index, "no-terminal", fsha, dsha))
        else:
            entry = _unmeasured(index, "", fsha, dsha)
            entry.update(
                status="measured",
                reason=None,
                tree=dtree[:8],
                dev_tip_suite_s=(win.d1.terminal_ms - win.d1.launch_ms) / 1000.0,
            )
            out.append(entry)
    return out


def repeat_total_s(repeat: Sequence[Dict[str, Any]]) -> float:
    """Dev-tip suite seconds over an identical tree, measured cycles only."""
    return sum(x["dev_tip_suite_s"] for x in repeat if x["status"] == "measured")


# ---------------------------------------------------------------------------------------------
# The pool
# ---------------------------------------------------------------------------------------------


def pool_results(
    cycle_set: Any,
    class_set: Any,
    delegate_set: Any,
    params: Any,
    *,
    repo: Any = None,
    split: Any = None,
    wait_by_check: Optional[Dict[str, float]] = None,
) -> PoolResult:
    """Pool the completed cycles into the halves, placements, answer, corners and REPEAT.

    Args:
        cycle_set: `analyze_cycles` of the transcript.
        class_set: `classify` of the same transcript (one cycle per completed window).
        delegate_set: `read_delegates` over the transcript's dispatches.
        params: The run's parameters (reserved; the pool reads no constant from them).
        repo: A git repository for REPEAT, or `None` (every cycle then reads `no-repo`).
        split: A `delegates.SplitResult`. With `None` every SUBAGENT second is UNSPLIT.
        wait_by_check: Pooled MACHINE_WAIT seconds by `CHECK_CLASSES` name (`check_inputs`).
            With `None` the ranking holds one `MACHINE_WAIT[unattributed]` bucket.

    Raises:
        ValueError: The class set or the split does not match the completed windows, the
            split lacks a `SPLIT_KEYS` entry, a check class is not in `CHECK_CLASSES`, or
            `delegate_set` matched files while `split` is `None`.
    """
    del params
    windows = [w for w in cycle_set.windows if w.status == "completed"]
    cycles = list(class_set.cycles)
    if len(cycles) != len(windows):
        raise ValueError(
            "class set holds %d cycles for %d completed windows"
            % (len(cycles), len(windows))
        )
    if split is None and delegate_set is not None and delegate_set.matched > 0:
        raise ValueError("delegate files were matched but no split was given")
    if wait_by_check is not None and any(
        name not in CHECK_CLASSES for name in wait_by_check
    ):
        raise ValueError("wait_by_check names a class outside CHECK_CLASSES")
    rows = None if split is None else _split_rows(split, len(windows))
    parts = pooled_parts(cycles, rows)
    classes = {name: sum(c.class_s[name] for c in cycles) for name in CLASSES}
    wall_all = sum((w.end_t_ms - w.start_t_ms) / 1000.0 for w in windows)
    away = classes["AWAY"]
    placements = evaluate(parts)
    corners, ranking = ranking_of(parts, wait_by_check)
    return PoolResult(
        wall_all,
        wall_all - away,
        away,
        classes,
        halves_of(parts, wall_all, away),
        placements,
        distinct_count(placements),
        decide(placements),
        corners,
        ranking,
        repeat_results(windows, repo),
    )


# ---------------------------------------------------------------------------------------------
# POOLCHECK
# ---------------------------------------------------------------------------------------------


def _row(name: str, ok: Optional[bool], detail: str) -> CheckRow:
    return CheckRow(name, "skip" if ok is None else ("pass" if ok else "fail"), detail)


def poolcheck(
    pool: PoolResult, stage_buckets: Optional[Dict[str, float]] = None
) -> List[CheckRow]:
    """POOLCHECK rows and the final `POOLCHECK` row (`fail` if any row fails).

    Args:
        pool: The `pool_results` result.
        stage_buckets: Pooled wall by stage bucket (`check_inputs`); `None` skips the
            stage-bucket row. The unmapped rule reads these buckets only: a check class
            such as `MACHINE_WAIT[other]` is never counted against it.
    """
    rows: List[CheckRow] = []
    ranked = sum(x["s"] for x in pool.ranking)
    diff = ranked + pool.away_s - pool.wall_all_s
    rows.append(
        _row(
            "POOLCHECK-ARITH",
            abs(diff) <= ARITH_TOL_S,
            "ranked_s=%.3f away_s=%.3f wall_s=%.3f diff_s=%.3f"
            % (ranked, pool.away_s, pool.wall_all_s, diff),
        )
    )
    if stage_buckets is None:
        rows.append(_row("POOLCHECK-STAGE-BUCKET", None, "no stage buckets given"))
    else:
        unmapped_s = sum(v for name, v in stage_buckets.items() if name not in STAGES)
        share = _pct(unmapped_s, pool.wall_all_s)
        rows.append(
            _row(
                "POOLCHECK-STAGE-BUCKET",
                None if share is None else share <= UNMAPPED_STAGE_MAX_PCT,
                "unmapped_stage_s=%.3f pct_all=%s"
                % (unmapped_s, "none" if share is None else "%.2f" % share),
            )
        )
    if stage_buckets is None:
        rows.append(_row("POOLCHECK-STAGE-SUM", None, "no stage buckets given"))
    else:
        total = sum(stage_buckets.values())
        rows.append(
            _row(
                "POOLCHECK-STAGE-SUM",
                abs(total - pool.wall_all_s) <= ARITH_TOL_S,
                "stage_s=%.3f wall_s=%.3f diff_s=%.3f"
                % (total, pool.wall_all_s, total - pool.wall_all_s),
            )
        )
    members = [name for x in pool.ranking for name in x["members"]]
    by_name = {x["name"]: x["s"] for x in pool.ranking}
    corners_ok = all(
        abs(by_name.get("corner-%d" % c["n"], -1.0) - c["s"]) < 1e-6
        for c in pool.corners
        if c["s"] is not None
    )
    once = len(members) == len(set(members)) and all(
        core in members for core in CORE_BUCKETS
    )
    rows.append(
        _row(
            "POOLCHECK-CORNER-RANK",
            once and corners_ok,
            "buckets=%d once=%s corners_match=%s" % (len(members), once, corners_ok),
        )
    )
    measured = [x for x in pool.repeat if x["status"] == "measured"]
    whole = all(x["feature_sha"] and x["dev_sha"] and x["tree"] for x in measured)
    rows.append(
        _row(
            "POOLCHECK-REPEAT",
            whole if measured else None,
            "measured=%d" % len(measured),
        )
    )
    failed = [r.name for r in rows if r.verdict == "fail"]
    rows.append(
        _row("POOLCHECK", not failed, "failed=%s" % (",".join(failed) or "none"))
    )
    return rows


__all__ = [
    "ARM_PLACEMENTS",
    "CheckInputs",
    "Corner3Evidence",
    "PLACEMENTS",
    "Parts",
    "TopCorners",
    "Unlabelled",
    "check_inputs",
    "corner3_evidence",
    "decide",
    "distinct_count",
    "evaluate",
    "halves_of",
    "lead_counts",
    "place",
    "pool_results",
    "poolcheck",
    "ranking_of",
    "repeat_results",
    "repeat_total_s",
    "top_corners",
    "unlabelled_machine_wait",
]

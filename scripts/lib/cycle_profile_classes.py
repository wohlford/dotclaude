"""Time classes of each completed `/feature` cycle: the sweep, HOOK, idle, AWAY and stages.

Slice A (intervals, the sweep, HOOK, idle gaps, the fence) and slice B (the stage cut, the
sensitivity sets and the small cross-check inputs) of the classes module. Built on the reader's tokens only (`cycle_profile_records`) and
the windows and run-long pairs of `cycle_profile_cycles`; it renders nothing. Every second of
a cycle's window belongs to exactly one class of `CLASSES`, decided by the sweep in
`PRIORITY` order: MACHINE_WAIT, then SUBAGENT, then HOOK, then OPERATOR_RESPONSE or AWAY,
and CONTROLLER (model and API time included; the two are not separable here) is the residual.
Shadowed subagent time is reported, not lost: `hidden_under_machine_wait_s`.

**Integer milliseconds.** Every interval is `(start_ms, end_ms, label)` in epoch milliseconds
and every comparison is on integers; seconds are floats only in the results. Sidechain records
take no part (they live in delegate files), and records without a timestamp are already gone
(`sorted_events`).

**Where each class comes from.**

- MACHINE_WAIT: every paired run-long launch (launch to terminal), labelled by check class,
  plus each plain foreground Bash call longer than `Params.long_call_s` (label `other`).
- SUBAGENT: each dispatch (`Agent`, `SendMessage`) from the dispatch to its notification. The
  interval ends at the ENQUEUE record of the notification keyed to the dispatch's own
  tool-use id, else at the delivery record, else the dispatch is an orphan with no interval.
  The ack is not an end. A resumed agent reuses its task id but each `SendMessage` has its own
  notification, so a resume is its own interval and is never merged with the first.
- HOOK: an Edit, Write or MultiEdit call whose latency (tool_use to tool_result) is strictly
  greater than `Params.hook_latency_s`. `hook_success` records are a cross-check
  (`hook_reported_s`) and never the source: their `durationMs` is the hook's own runtime,
  not the time the model was blocked.
- OPERATOR_RESPONSE and AWAY: idle gaps, from the last assistant record of a turn to the next
  human prompt (an interrupt is skipped; an auto-continuation and the answer to an
  `AskUserQuestion` call end a gap like a prompt). A closing `/commit` or `/propagate` wrapper
  is a human prompt; a `Skill` call of the same name is an assistant record and is not.

**Idle gaps and the fence.** A gap that no machine or subagent interval touches is a pure gap
(its whole length). A gap a job overlaps keeps its job-free remainder when that remainder
exceeds `REMAINDER_FLOOR_MS`: the remainder is BOOKED idle and is ALSO in the list the fence
is fitted from (one predicate, one list). `away_fence` is the Tukey fence over log10 of that
list: inner fence `Q3 + k * IQR` (`Params.away_fence_k`, default 1.5, inclusive quartile
method) separates AWAY (strictly above it) from OPERATOR_RESPONSE; the outer fence (k 3.0) is
always computed beside it. Below `Params.away_min_gaps` gaps no fence is fitted and all idle
time is OPERATOR_RESPONSE. `Fence.q1` and `Fence.q3` are the log10 quartiles.

**Choices made where the spec is silent** (stated once here; the code further down does
not restate them):

- A remainder of one second or less is neither booked nor listed. The original booked every
  positive remainder but listed only those above one second; the two now share one rule.
- `Dispatch.stage_kw` joins the dispatch's closed-vocabulary stage words with `+` in sorted
  order, or is `None`. `Dispatch.role` of a `SendMessage` is the role of the `Agent` whose
  notification has the same task id, else `other` (the reader keeps no `to` token).
- `Segment.parts` hold seconds. MACHINE_WAIT seconds go whole to the highest-ranked check
  class active (`CHECK_CLASSES` order); SUBAGENT and HOOK seconds are shared by label in
  proportion to the number of active intervals of that label.
- An interval joins a window when it STARTS in `[start, end)` and is clipped to the window;
  an idle gap joins when it overlaps the window.
- `classify` covers the `completed` windows only, in window order.
- HOOK is a union by construction (`hook_spans` returns possibly overlapping spans and the
  sweep covers a class while any span is active); there is no separate merge.

**Behaviours kept from the original on purpose** (each has a row that pins it):

- A span joins a window only when it STARTS inside it (`start <= t < end`, then clipped), so
  a dispatch or run begun before the window contributes nothing to that window even while it
  overlaps it (`_started_in`).
- The "job outstanding" test of an idle gap counts machine and subagent spans only, never
  HOOK. An Edit wait inside an idle gap is therefore booked HOOK (the higher priority) while
  the gap is still listed, whole, in the idle list the fence is fitted from.
- A gap is AWAY only when its seconds are STRICTLY greater than the inner fence.
- `ClassCycle.idle_gaps_s` holds every idle entry that overlaps the window, scaled by the
  clipped fraction of its span. For a remainder entry that is the entry's job-free seconds
  times the clipped fraction, not the job-free time inside the window (a gap that straddles
  the window end keeps a share of free time that lies outside it).
- `peer_overlap_s` sums overlap per suite run, as the original's two peer figures do, so two
  concurrent peers each count: the figure is peer-suite seconds, not wall seconds.

**Slice B: the stage cut.** Each completed window is cut into the 13 `STAGES` (every
stage gets a `StageRow`, a stage with no marker has wall 0 and its time stays in the stage
before it). A stage starts at its first marker, clamped so stages never run backwards:
the window start; the first dispatch whose `stage_kw` words classify to it
(`dispatch_stage`, the original's `dstage_kind` over the closed keyword set; a `SendMessage`
dispatch is classed with the role `sm`, so a resumed reviewer is not a gate by role);
`gates`: the first gate marker (a gate dispatch or a plain-audit run-long launch) at or
after the first execute dispatch; `commit`: the last `/commit` marker between that point and
the tip suite; `feature-tip-suite`: the earliest launch of the same-sha feature-tip suite
runs; `bricks`: the first per-brick-sweep launch between the feature-tip verdict and the
dev-tip launch; `dev-tip-suite`: the dev-tip launch; `closing`: the last dev-tip verdict.
Dispatches and gate or commit markers are read only before the tip suite starts (or the
window end when there is no feature-tip suite). The same-sha test reads the launch's label
sha only (the artifact key is an opaque digest here). The reader keeps no marker for a
`Skill vet` or `Skill audit` call, so the original's skill gate marker is not available.

**Two bucket kinds.** `STAGE_BUCKETS` (the stages plus the one `other` bucket for an
unmapped dispatch) and `CHECK_CLASSES` (run-long check classes) are two constants, and
`MACHINE_WAIT[other]` (the unlabelled check class) is a check class, never a stage.

**Sensitivity.** `SENSITIVITY_SETS` are fixed by the spec and never read the run's close
flags; each set re-runs the closing rule through `analyze_cycles` and pools a fresh sweep.
"""

from __future__ import annotations

import bisect
import math
import statistics
from collections import Counter, defaultdict
from typing import (
    Any,
    Dict,
    FrozenSet,
    Iterable,
    List,
    NamedTuple,
    Optional,
    Sequence,
    Tuple,
)

from cycle_profile_cycles import analyze_cycles
from cycle_profile_model import (
    CHECK_CLASSES,
    CLASSES,
    EDIT_NAMES,
    PRIORITY,
    STAGE_BUCKETS,
    STAGES,
    ClassCycle,
    ClassSet,
    Dispatch,
    Event,
    Fence,
    Params,
    StageRow,
)
from cycle_profile_records import sorted_events

#: The outer fence multiplier, always computed beside the primary fence (D2.1).
OUTER_FENCE_K = 3.0
#: A job-free remainder of a job-outstanding gap counts as idle only above this length.
REMAINDER_FLOOR_MS = 1000
#: The classes the sweep judges, highest priority first; CONTROLLER is the residual.
_SWEEP_ORDER = tuple(name for name in PRIORITY if name != "CONTROLLER")
_CHECK_RANK = {name: index for index, name in enumerate(CHECK_CLASSES)}

Interval = Tuple[int, int, str]


class EditCall(NamedTuple):
    """One Edit, Write or MultiEdit call: the span the model was blocked on it."""

    start_ms: int
    end_ms: int
    name: str


class IdleEntry(NamedTuple):
    """One idle gap. `seconds` is the whole gap (`pure`) or its job-free part (`remainder`)."""

    start_ms: int
    end_ms: int
    seconds: float
    kind: str


class Segment(NamedTuple):
    """One stretch of a window with a constant set of active intervals."""

    t0_ms: int
    dt_ms: int
    cls: str
    parts: Dict[str, float]
    hidden_ms: int
    hidden_agent_ms: int


class Context(NamedTuple):
    """Everything `classify` derives from one transcript before it cuts windows."""

    events: List[Event]
    params: Params
    dispatches: List[Dispatch]
    machine: List[Interval]
    subagent: List[Interval]
    edits: List[EditCall]
    pairs: Tuple[Any, ...]
    entries: List[IdleEntry]
    gaps_s: List[float]
    fence: Fence
    booked: List[Interval]
    windows: List[Any]
    sendmsg: FrozenSet[str] = frozenset()


# ---------------------------------------------------------------------------------------------
# Events and intervals
# ---------------------------------------------------------------------------------------------


def main_events(transcript: Any) -> List[Event]:
    """The timestamped main-thread events in time order (ties by file position)."""
    return [e for e in sorted_events(transcript) if not e.sidechain]


def _first_results(events: Sequence[Event]) -> Dict[str, int]:
    """Tool-use id to the time of its first `tool_result`."""
    out: Dict[str, int] = {}
    for event in events:
        for tok in event.toks:
            if tok.kind == "tool_result" and tok.a:
                out.setdefault(tok.a, event.t_ms)
    return out


def _dispatches(events: Sequence[Event]) -> List[Dispatch]:
    starts: List[Tuple[int, int, str, Optional[str]]] = []
    words: Dict[str, set] = defaultdict(set)
    acks: Dict[str, Tuple[int, Optional[str]]] = {}
    enqueued: Dict[str, int] = {}
    delivered: Dict[str, int] = {}
    task_enq: Dict[str, str] = {}
    task_del: Dict[str, str] = {}
    for event in events:
        for tok in event.toks:
            if tok.kind == "agent_dispatch" and tok.a:
                starts.append((event.t_ms, event.pos, tok.a, tok.b or "other"))
            elif tok.kind == "sendmsg_dispatch" and tok.a:
                starts.append((event.t_ms, event.pos, tok.a, None))
            elif tok.kind == "stage_kw" and tok.a and tok.b:
                words[tok.a].add(tok.b)
            elif tok.kind == "agent_ack" and tok.a:
                acks.setdefault(tok.a, (event.t_ms, tok.b))
            elif tok.kind == "notif_enqueue" and tok.a:
                enqueued[tok.a] = min(event.t_ms, enqueued.get(tok.a, event.t_ms))
                if tok.b:
                    task_enq.setdefault(tok.a, tok.b)
            elif tok.kind == "notif_delivery" and tok.a:
                delivered.setdefault(tok.a, event.t_ms)
                if tok.b:
                    task_del.setdefault(tok.a, tok.b)
    out: List[Dispatch] = []
    owner: Dict[str, Tuple[str, Optional[str]]] = {}
    for t_ms, _pos, tool_id, role in sorted(starts):
        end = enqueued.get(tool_id)
        source = "enqueue"
        if end is None:
            end = delivered.get(tool_id)
            source = "delivery" if end is not None else "orphan"
        task = task_enq.get(tool_id) or task_del.get(tool_id)
        ack_ms, agent_id = acks.get(tool_id, (None, None))
        if role is None:
            role, agent_id = owner.get(task or "", ("other", None))
        elif task is not None:
            owner.setdefault(task, (role, agent_id))
        kw = "+".join(sorted(words[tool_id])) or None
        out.append(
            Dispatch(tool_id, agent_id, task, role, kw, t_ms, ack_ms, end, source)
        )
    return out


def dispatch_intervals(transcript: Any) -> List[Dispatch]:
    """Every main-thread dispatch with its interval end, in dispatch order."""
    return _dispatches(main_events(transcript))


def _long_foreground(events: Sequence[Event], long_call_s: int) -> List[Interval]:
    """Plain foreground Bash calls blocked for strictly more than `long_call_s`."""
    return [(a, b, "other") for a, b, _ in _long_calls(events, long_call_s)]


def machine_intervals(
    events: Sequence[Event], pairs: Sequence[Any], params: Params
) -> List[Interval]:
    """Paired run-long intervals (by check class) and long foreground calls (`other`)."""
    out: List[Interval] = [
        (p.launch_ms, p.terminal_ms, p.check_class)
        for p in pairs
        if p.terminal_ms is not None and p.terminal_ms > p.launch_ms
    ]
    out.extend(_long_foreground(events, params.long_call_s))
    return sorted(out)


def hook_success_by_tool(events: Sequence[Event]) -> Dict[str, int]:
    """Tool-use id to the `durationMs` of its `hook_success` record (a cross-check only)."""
    out: Dict[str, int] = {}
    for event in events:
        for tok in event.toks:
            if tok.kind == "hook_success" and tok.a and tok.n is not None:
                out.setdefault(tok.a, tok.n)
    return out


def edit_calls(events: Sequence[Event]) -> List[EditCall]:
    """Each edit-class call that has a result, from its tool_use to its tool_result."""
    results = _first_results(events)
    out: List[EditCall] = []
    for event in events:
        for tok in event.toks:
            if tok.kind == "tool_use" and tok.a in EDIT_NAMES and tok.b in results:
                out.append(EditCall(event.t_ms, results[tok.b], tok.a))
    return sorted(out)


def hook_spans(
    calls: Sequence[EditCall], threshold_ms: int, lo_ms: int, hi_ms: int
) -> List[Tuple[int, int]]:
    """The slow edit calls that start in `[lo_ms, hi_ms)`, clipped to the window end.

    A call is slow when its latency is strictly greater than `threshold_ms`. The spans may
    overlap and are returned as they are: HOOK is their UNION because the sweep treats a
    class as covered while any span of it is active, so overlapping calls count once. A
    merge here would be inert, since no caller needs a merged list.
    """
    return sorted(
        (c.start_ms, min(c.end_ms, hi_ms))
        for c in calls
        if c.end_ms - c.start_ms > threshold_ms and lo_ms <= c.start_ms < hi_ms
    )


def hook_reported_s(events: Sequence[Event], lo_ms: int, hi_ms: int) -> float:
    """The `hook_success` durations in `[lo_ms, hi_ms]`, summed in seconds (HOOK_REPORTED)."""
    total = 0
    for event in events:
        if lo_ms <= event.t_ms <= hi_ms:
            total += sum(tok.n or 0 for tok in event.toks if tok.kind == "hook_success")
    return total / 1000.0


def busy_union(intervals: Sequence[Tuple[int, int]]) -> List[Tuple[int, int]]:
    """Disjoint, sorted union of `(start, end)` spans; touching spans join."""
    out: List[Tuple[int, int]] = []
    for a, b in sorted(intervals):
        if out and a <= out[-1][1]:
            if b > out[-1][1]:
                out[-1] = (out[-1][0], b)
        else:
            out.append((a, b))
    return out


# ---------------------------------------------------------------------------------------------
# Idle gaps and the AWAY fence
# ---------------------------------------------------------------------------------------------


def _ask_ids(events: Sequence[Event]) -> set:
    return {
        tok.b
        for event in events
        for tok in event.toks
        if tok.kind == "tool_use" and tok.a == "AskUserQuestion" and tok.b
    }


def _turn_mark(event: Event, asks: set) -> Optional[str]:
    """`A` for an assistant record, `H` for an operator turn, else `None`."""
    if event.rtype == "assistant":
        return "A"
    toks = event.toks
    if any(tok.kind == "interrupt" for tok in toks):
        return None
    if any(tok.kind == "human_prompt" for tok in toks):
        return "H"
    if any(tok.kind == "auto_continuation" for tok in toks):
        return "H"
    if any(tok.kind == "tool_result" and tok.a in asks for tok in toks):
        return "H"
    return None


def _free_ms(busy: Sequence[Tuple[int, int]], lo: int, hi: int) -> Tuple[int, bool]:
    """Job-free milliseconds of `[lo, hi]` and whether any job touches it."""
    starts = [a for a, _ in busy]
    index = bisect.bisect_left(starts, hi) - 1
    covered = 0
    touched = False
    while index >= 0 and busy[index][1] > lo:
        touched = True
        covered += min(hi, busy[index][1]) - max(lo, busy[index][0])
        index -= 1
    return (hi - lo) - covered, touched


def idle_gaps(
    events: Sequence[Event], busy: Sequence[Tuple[int, int]]
) -> List[IdleEntry]:
    """The idle list: pure gaps plus the job-free remainder of job-outstanding gaps.

    Args:
        events: Main-thread events in time order.
        busy: The disjoint union of every machine and subagent interval (`busy_union`).
    """
    asks = _ask_ids(events)
    gaps: List[Tuple[int, int]] = []
    last: Optional[int] = None
    for event in events:
        mark = _turn_mark(event, asks)
        if mark == "A":
            last = event.t_ms
        elif mark == "H":
            if last is not None and event.t_ms > last:
                gaps.append((last, event.t_ms))
            last = None
    out: List[IdleEntry] = []
    for a, b in gaps:
        free, touched = _free_ms(busy, a, b)
        if not touched:
            out.append(IdleEntry(a, b, (b - a) / 1000.0, "pure"))
        elif free > REMAINDER_FLOOR_MS:
            out.append(IdleEntry(a, b, free / 1000.0, "remainder"))
    return out


def away_fence(
    gaps_s: Sequence[float],
    k: Optional[float] = None,
    method: str = "inclusive",
    min_gaps: Optional[int] = None,
) -> Fence:
    """The Tukey fences over log10 of the idle gaps, or an empty `Fence` below the floor.

    Args:
        gaps_s: Idle gaps in seconds, each above zero.
        k: Inner fence multiplier; `Params.away_fence_k` when `None`.
        method: `inclusive` (default) or `exclusive` quartiles.
        min_gaps: Fewest gaps that fit a fence; `Params.away_min_gaps` when `None`.

    Returns:
        A `Fence` whose `q1` and `q3` are log10 quartiles and whose `inner_s` and `outer_s`
        are in seconds (`outer_s` always uses `OUTER_FENCE_K`); all four are `None` when
        there are fewer than `min_gaps` gaps (and at least two are always required).
    """
    defaults = Params()
    k = defaults.away_fence_k if k is None else k
    floor = defaults.away_min_gaps if min_gaps is None else min_gaps
    if len(gaps_s) < max(floor, 2):
        return Fence(None, None, None, None, k, method)
    logs = sorted(math.log10(g) for g in gaps_s)
    q1, _, q3 = statistics.quantiles(logs, n=4, method=method)
    iqr = q3 - q1
    return Fence(
        q1, q3, 10 ** (q3 + k * iqr), 10 ** (q3 + OUTER_FENCE_K * iqr), k, method
    )


def _leave_one_out_fences(
    gaps_s: Sequence[float], k: float, method: str, min_gaps: int
) -> List[float]:
    """The inner fence with each gap left out in turn (empty below the fit floor)."""
    if away_fence(gaps_s, k, method, min_gaps).inner_s is None or len(gaps_s) < 3:
        return []
    return [
        away_fence(list(gaps_s[:i]) + list(gaps_s[i + 1 :]), k, method, 2).inner_s
        for i in range(len(gaps_s))
    ]


def leave_one_out_range(
    gaps_s: Sequence[float],
    k: Optional[float] = None,
    method: str = "inclusive",
    min_gaps: Optional[int] = None,
) -> Optional[Tuple[float, float]]:
    """Smallest and largest inner fence when each gap is left out in turn, or `None`."""
    defaults = Params()
    fences = _leave_one_out_fences(
        gaps_s,
        defaults.away_fence_k if k is None else k,
        method,
        defaults.away_min_gaps if min_gaps is None else min_gaps,
    )
    return (min(fences), max(fences)) if fences else None


def count_above(gaps_s: Sequence[float], fence: Fence) -> int:
    """How many gaps lie strictly above the inner fence; 0 when no fence was fitted."""
    if fence.inner_s is None:
        return 0
    return sum(1 for g in gaps_s if g > fence.inner_s)


def book_idle(entries: Sequence[IdleEntry], fence: Fence) -> List[Interval]:
    """Each idle entry's whole gap as an AWAY or OPERATOR_RESPONSE interval.

    The class follows the entry's `seconds` (the remainder, for a remainder entry) against
    the inner fence; the sweep gives any job-covered part of the gap to its own class.
    """
    out: List[Interval] = []
    for e in entries:
        away = fence.inner_s is not None and e.seconds > fence.inner_s
        out.append((e.start_ms, e.end_ms, "AWAY" if away else "OPERATOR_RESPONSE"))
    return out


# ---------------------------------------------------------------------------------------------
# The sweep
# ---------------------------------------------------------------------------------------------


def _shares(active: Counter, dt_ms: int) -> Dict[str, float]:
    total = sum(active.values())
    return {label: dt_ms / 1000.0 * n / total for label, n in active.items()}


def sweep_segments(
    intervals_by_class: Dict[str, Sequence[Any]],
    lo_ms: int,
    hi_ms: int,
    bounds: Sequence[int] = (),
) -> List[Segment]:
    """Cut `[lo_ms, hi_ms)` into segments of constant activity and assign each one class.

    Args:
        intervals_by_class: Class name to `(start, end)` or `(start, end, label)` spans in
            integer milliseconds; spans are clipped to the window. A name outside the
            judged classes is ignored.
        lo_ms: Window start.
        hi_ms: Window end.
        bounds: Extra cut points (stage boundaries) that must split a segment.
    """
    points = {lo_ms, hi_ms}
    points.update(b for b in bounds if lo_ms <= b <= hi_ms)
    edges: Dict[int, List[Tuple[str, str, int]]] = defaultdict(list)
    for name, spans in intervals_by_class.items():
        if name not in _SWEEP_ORDER:
            continue
        for span in spans:
            a, b = max(span[0], lo_ms), min(span[1], hi_ms)
            if b <= a:
                continue
            label = span[2] if len(span) > 2 else name
            edges[a].append((name, label, 1))
            edges[b].append((name, label, -1))
            points.update((a, b))
    active: Dict[str, Counter] = {name: Counter() for name in _SWEEP_ORDER}
    out: List[Segment] = []
    ordered = sorted(points)
    for index in range(len(ordered) - 1):
        t0, t1 = ordered[index], ordered[index + 1]
        for name, label, step in edges.get(t0, ()):
            active[name][label] += step
            if active[name][label] == 0:
                del active[name][label]
        dt = t1 - t0
        cls = next((n for n in _SWEEP_ORDER if active[n]), "CONTROLLER")
        parts: Dict[str, float] = {}
        if cls == "MACHINE_WAIT":
            top = min(active[cls], key=lambda x: _CHECK_RANK.get(x, len(_CHECK_RANK)))
            parts = {top: dt / 1000.0}
        elif cls in ("SUBAGENT", "HOOK"):
            parts = _shares(active[cls], dt)
        hidden = bool(active["SUBAGENT"]) and bool(active["MACHINE_WAIT"])
        agents = sum(active["SUBAGENT"].values())
        out.append(
            Segment(
                t0, dt, cls, parts, dt if hidden else 0, dt * agents if hidden else 0
            )
        )
    return out


def sweep(
    intervals_by_class: Dict[str, Sequence[Any]], lo_ms: int, hi_ms: int
) -> Dict[str, float]:
    """Class seconds over `[lo_ms, hi_ms)`: every second in exactly one of `CLASSES`."""
    out = {name: 0.0 for name in CLASSES}
    for seg in sweep_segments(intervals_by_class, lo_ms, hi_ms):
        out[seg.cls] += seg.dt_ms / 1000.0
    return out


# ---------------------------------------------------------------------------------------------
# Windows
# ---------------------------------------------------------------------------------------------


def completed_windows(cycle_set: Any) -> List[Any]:
    """The `completed` windows of a `CycleSet`, in window order."""
    return [w for w in cycle_set.windows if w.status == "completed"]


def build_context(
    cycle_set: Any,
    transcript: Any,
    params: Params,
    fence_method: str = "inclusive",
) -> Context:
    """Derive the intervals, the idle list and the fence for a whole transcript."""
    events = main_events(transcript)
    dispatches = _dispatches(events)
    machine = machine_intervals(events, cycle_set.pairs, params)
    subagent = [
        (d.start_ms, d.end_ms, d.role)
        for d in dispatches
        if d.end_ms is not None and d.end_ms > d.start_ms
    ]
    busy = busy_union([(a, b) for a, b, _ in machine + subagent])
    entries = idle_gaps(events, busy)
    gaps_s = [e.seconds for e in entries]
    fence = away_fence(
        gaps_s,
        params.away_fence_k,
        fence_method,
        params.away_min_gaps,
    )
    return Context(
        events,
        params,
        dispatches,
        machine,
        subagent,
        edit_calls(events),
        tuple(cycle_set.pairs),
        entries,
        gaps_s,
        fence,
        book_idle(entries, fence),
        completed_windows(cycle_set),
        frozenset(
            tok.a
            for event in events
            for tok in event.toks
            if tok.kind == "sendmsg_dispatch" and tok.a
        ),
    )


def _started_in(spans: Sequence[Interval], lo_ms: int, hi_ms: int) -> List[Interval]:
    return [s for s in spans if lo_ms <= s[0] < hi_ms]


def window_intervals(context: Context, window: Any) -> Dict[str, List[Any]]:
    """The class intervals of one window (a span joins when it starts inside the window)."""
    lo, hi = window.start_t_ms, window.end_t_ms
    booked = [s for s in context.booked if s[1] > lo and s[0] < hi]
    return {
        "MACHINE_WAIT": _started_in(context.machine, lo, hi),
        "SUBAGENT": _started_in(context.subagent, lo, hi),
        "HOOK": hook_spans(context.edits, context.params.hook_latency_s * 1000, lo, hi),
        "OPERATOR_RESPONSE": [s for s in booked if s[2] == "OPERATOR_RESPONSE"],
        "AWAY": [s for s in booked if s[2] == "AWAY"],
    }


def window_segments(context: Context, window: Any) -> List[Segment]:
    """The sweep of one window's intervals over the window."""
    return sweep_segments(
        window_intervals(context, window), window.start_t_ms, window.end_t_ms
    )


def no_separation_class_s(
    cycle_set: Any, transcript: Any, params: Params
) -> List[Dict[str, float]]:
    """Per-window class seconds when no AWAY is separated: every idle second is OPERATOR_RESPONSE.

    Built from the same idle entries `book_idle` uses and the same sweep, so each window's
    OPERATOR_RESPONSE equals the primary OPERATOR_RESPONSE plus AWAY, AWAY is 0 and every
    other class is unchanged.
    """
    context = build_context(cycle_set, transcript, params)
    unsplit = context._replace(
        booked=[(e.start_ms, e.end_ms, "OPERATOR_RESPONSE") for e in context.entries]
    )
    out: List[Dict[str, float]] = []
    for window in unsplit.windows:
        class_s = {name: 0.0 for name in CLASSES}
        for seg in window_segments(unsplit, window):
            class_s[seg.cls] += seg.dt_ms / 1000.0
        out.append(class_s)
    return out


def _idle_in_window(context: Context, window: Any) -> List[float]:
    lo, hi = window.start_t_ms, window.end_t_ms
    out: List[float] = []
    for e in context.entries:
        a, b = max(e.start_ms, lo), min(e.end_ms, hi)
        if b > a:
            out.append(e.seconds * (b - a) / (e.end_ms - e.start_ms))
    return out


def window_cycle(context: Context, window: Any) -> ClassCycle:
    """The per-class seconds, hidden time, agent-seconds and idle list of one window."""
    lo, hi = window.start_t_ms, window.end_t_ms
    cut = stage_cut(context, window)
    segments = cut.segments
    class_s = {name: 0.0 for name in CLASSES}
    for seg in segments:
        class_s[seg.cls] += seg.dt_ms / 1000.0
    agent_ms = sum(
        min(b, hi) - max(a, lo) for a, b, _ in _started_in(context.subagent, lo, hi)
    )
    return ClassCycle(
        class_s,
        sum(seg.hidden_ms for seg in segments) / 1000.0,
        agent_ms / 1000.0,
        cut.rows,
        _idle_in_window(context, window),
    )


def peer_overlap_s(
    events: Sequence[Event], pairs: Sequence[Any], lo_ms: int, hi_ms: int
) -> Tuple[float, float]:
    """Seconds of suite runs launched OUTSIDE a window that overlap it: (any, recorded).

    `recorded` counts only the peers whose artifact the window itself waits on or polls.
    """
    waited = {
        tok.b
        for event in events
        if lo_ms <= event.t_ms < hi_ms
        for tok in event.toks
        if tok.kind == "runlong" and tok.a in ("wait", "status") and tok.b
    }
    seen = 0
    recorded = 0
    for p in pairs:
        if not p.check_class.startswith("suite") or p.terminal_ms is None:
            continue
        if lo_ms <= p.launch_ms < hi_ms or p.terminal_ms <= p.launch_ms:
            continue
        overlap = min(hi_ms, p.terminal_ms) - max(lo_ms, p.launch_ms)
        if overlap > 0:
            seen += overlap
            if p.art_key in waited:
                recorded += overlap
    return seen / 1000.0, recorded / 1000.0


def classify(
    cycle_set: Any,
    transcript: Any,
    params: Params,
    *,
    fence_method: str = "inclusive",
) -> ClassSet:
    """Per-cycle class seconds for every completed window, plus the transcript-wide lists.

    Args:
        cycle_set: `analyze_cycles` of the same transcript.
        transcript: The parsed transcript.
        params: The run's parameters (`hook_latency_s`, `long_call_s`, `away_*`).
        fence_method: Quartile method of the AWAY fence, `inclusive` or `exclusive`.

    Returns:
        A `ClassSet` whose `cycles` follow the completed windows in order, whose
        `dispatches` are every main-thread dispatch, whose `idle_gaps_s` is the list the
        fence is fitted from, and whose `fence` is the fitted (or empty) `Fence`.
    """
    context = build_context(cycle_set, transcript, params, fence_method)
    return ClassSet(
        [window_cycle(context, w) for w in context.windows],
        context.dispatches,
        context.gaps_s,
        context.fence,
    )


# ---------------------------------------------------------------------------------------------
# Slice B: the stage cut, the sensitivity sets and the cross-check inputs
# ---------------------------------------------------------------------------------------------

#: The five closing-rule sensitivity sets: `(name, rerun_min, post_min, min_suite_min)`.
#: Fixed by the spec (D4, L03) and independent of the run's own close flags.
SENSITIVITY_SETS: Tuple[Tuple[str, int, int, int], ...] = (
    ("primary", 15, 30, 10),
    ("tight", 5, 10, 10),
    ("loose", 60, 120, 10),
    ("min5", 15, 30, 5),
    ("min20", 15, 30, 20),
)
#: Per-run suite minutes outside this band are flagged beside the figure, never failed.
SUITEMACH_BAND_MIN = (5.0, 60.0)
#: The median enqueue-minus-dispatch over `duration_ms` outside this band is flagged only.
SUBCHECK_BAND = (0.5, 2.0)
#: A leave-one-out fence "changes the AWAY set" when AWAY seconds move by more than this.
AWAY_FLIP_S = 1.0

#: `StageBound.source` values (closed) and `StageBound.note` values (closed).
STAGE_SOURCES: Tuple[str, ...] = (
    "window-start",
    "dispatch",
    "run-long",
    "commit-marker",
    "verdict",
    "none",
)
STAGE_NOTES: Tuple[str, ...] = ("ok", "clamped-later", "clamped-to-end", "no-marker")


class StageBound(NamedTuple):
    """Where one stage starts, which kind of record fixed it, and whether it was clamped."""

    stage: str
    start_ms: int
    source: str
    note: str


class StageCut(NamedTuple):
    """One completed window cut into the `STAGES`.

    `parts[k][cls]` maps a label (check class, role or edit name) to seconds inside stage
    `k`; `hidden_s[k]` is the subagent time hidden under machine wait in that stage.
    """

    bounds: List[StageBound]
    rows: List[StageRow]
    segments: List[Segment]
    parts: List[Dict[str, Dict[str, float]]]
    hidden_s: List[float]
    feature_run_s: Tuple[float, ...]
    dev_run_s: Tuple[float, ...]


class SensitivityRow(NamedTuple):
    """One closing-rule set: windows kept (an end found), moved against the run, pooled."""

    name: str
    rerun_min: int
    post_min: int
    min_suite_min: int
    kept: int
    moved: int
    classes_s: Dict[str, float]


class LongCallArm(NamedTuple):
    """Plain foreground Bash calls over `long_call_s` in a window, split by token class."""

    calls: int
    total_s: float
    check_s: float
    other_s: float


class OrderIndicators(NamedTuple):
    """The two stage-order indicators of a window; labelled, never summed into a share."""

    hidden_agent_s: float
    hidden_union_s: float
    stalled_recorded_s: float
    stalled_any_s: float


class FenceRobust(NamedTuple):
    """The AWAY fence's sensitivity to leaving one gap out (L04); `None` when not fitted."""

    n: int
    base_s: Optional[float]
    min_s: Optional[float]
    max_s: Optional[float]
    above_base: int
    above_min: int
    above_max: int
    smallest_above_s: Optional[float]
    smallest_above_pct: Optional[float]
    away_base_s: Optional[float]
    away_min_s: Optional[float]
    away_max_s: Optional[float]
    flips: int
    omissions: int


class SuiteMach(NamedTuple):
    """Suite MACHINE_WAIT seconds in the two tip-suite stages, and the per-run durations.

    `feature_run_s` and `dev_run_s` hold each suite run's own length in seconds (empty when
    the stage has no run). A band flag is `None` with no run, else `False` when ANY run lies
    outside `SUITEMACH_BAND_MIN`: it is flagged beside the figure, never failed.
    """

    feature_s: float
    feature_run_s: Tuple[float, ...]
    dev_s: float
    dev_run_s: Tuple[float, ...]
    feature_in_band: Optional[bool]
    dev_in_band: Optional[bool]


class SubCheck(NamedTuple):
    """The (enqueue minus dispatch) over `duration_ms` ratio of the enqueue-ended dispatches.

    `not_enqueue` counts dispatches with a notification `duration_ms` whose interval ended
    at the delivery record instead (no enqueue record): they are left out of the ratio.
    """

    n: int
    median: Optional[float]
    p10: Optional[float]
    p90: Optional[float]
    in_band: Optional[bool]
    not_enqueue: int = 0


_REVIEW_WORDS = frozenset(
    ("review", "reviews", "reviewer", "rule", "ruling", "ruled", "verify")
)
_AUTHOR_WORDS = frozenset(("author", "draft", "write", "writer"))
_FOLD_WORDS = frozenset(("fold", "folds", "folded"))
_EXECUTE_WORDS = frozenset(
    ("implement", "task", "fix", "final", "whole", "review", "reviewer", "implementer")
)
_GATE_ROLES = ("skill-reviewer", "skill-content-reviewer")
#: The role the original gave a `SendMessage` dispatch: never a gate by role alone.
_RESUME_ROLE = "sm"
_TIP_CHECKS = ("suite-feature-tip", "suite-unlabelled")


def dstage_kind(role: str, words: Iterable[str]) -> Optional[str]:
    """The stage a dispatch belongs to, from its closed stage words and role, or `None`.

    A fold is returned as `fold:plan`, `fold:spec` or `fold:?` (the last needs the stages
    already seen: `dispatch_stage` resolves it).
    """
    given = set(words)
    review = bool(given & _REVIEW_WORDS)
    author = bool(given & _AUTHOR_WORDS)
    if given & _FOLD_WORDS:
        return (
            "fold:plan"
            if "plan" in given
            else ("fold:spec" if "spec" in given else "fold:?")
        )
    if "plan" in given:
        return (
            "plan-review"
            if (review or "spec" in given) and not author
            else "plan-author"
        )
    if "spec" in given:
        if author:
            return "triage/spec-author"
        if review or given & {"spike", "probe"}:
            return "spec-review/ruling"
        return "triage/spec-author"
    if role in _GATE_ROLES:
        return "gates"
    if given & {"vet", "audit"} and not given & {"implement", "task"}:
        return "gates"
    if given & _EXECUTE_WORDS or review:
        return "execute"
    return None


def dispatch_stage(role: str, stage_kw: Optional[str], seen: Iterable[str] = ()) -> str:
    """The stage bucket of a dispatch: a member of `STAGES`, or the one `other` bucket.

    Args:
        role: The dispatch's role (`_RESUME_ROLE` for a `SendMessage`).
        stage_kw: `Dispatch.stage_kw` (stage words joined by `+`), or `None`.
        seen: Stages already assigned earlier in the cycle; a bare fold is a plan fold when
            a plan author or plan review is among them, else a spec fold.
    """
    kind = dstage_kind(role, stage_kw.split("+") if stage_kw else ())
    if kind is None:
        return STAGE_BUCKETS[-1]
    if kind == "fold:plan":
        return "plan-fold"
    if kind == "fold:spec":
        return "spec-fold"
    if kind == "fold:?":
        planned = {"plan-author", "plan-review"} & set(seen)
        return "plan-fold" if planned else "spec-fold"
    return kind


def _tip_runs(context: Context, window: Any) -> List[Any]:
    """The same-sha feature-tip suite runs inside the window, concurrent duplicates dropped."""
    f1, d1 = window.f1, window.d1
    if f1 is None or d1 is None:
        return []
    floor = context.params.close_min_suite_min * 60000
    sha = {f1.label_sha} - {None}
    found = [
        p
        for p in context.pairs
        if p.check_class.startswith("suite")
        and p.terminal_ms is not None
        and window.start_t_ms <= p.launch_ms < d1.launch_ms
        and p.terminal_ms - p.launch_ms >= floor
        and p.check_class in _TIP_CHECKS
        and sha & ({p.label_sha} - {None})
    ]
    kept: List[Any] = []
    for p in sorted(found, key=lambda x: x.launch_ms):
        if kept and p.launch_ms < kept[-1].terminal_ms:
            continue
        kept.append(p)
    if not any(q.launch_ms <= f1.launch_ms < q.terminal_ms for q in kept):
        kept.append(f1)
    return kept


def _stage_firsts(
    context: Context, window: Any
) -> Tuple[Dict[str, Tuple[int, str]], List[Any]]:
    """The first marker of each stage as `(t_ms, source)`, and the feature-tip suite runs."""
    ws, we = window.start_t_ms, window.end_t_ms
    f1, d1, chain = window.f1, window.d1, window.chain
    dev_last = chain[-1] if chain else d1
    runs = _tip_runs(context, window)
    tip_start = min(p.launch_ms for p in runs) if f1 is not None else we
    firsts: Dict[str, Tuple[int, str]] = {STAGES[0]: (ws, "window-start")}
    seen: set = set()
    kinds: List[Tuple[int, str]] = []
    for d in context.dispatches:
        if not ws <= d.start_ms < tip_start:
            continue
        role = _RESUME_ROLE if d.tool_use_id in context.sendmsg else d.role
        stage = dispatch_stage(role, d.stage_kw, seen)
        if stage not in STAGES:
            continue
        seen.add(stage)
        kinds.append((d.start_ms, stage))
        firsts.setdefault(stage, (d.start_ms, "dispatch"))
    gates = [(t, "dispatch") for t, stage in kinds if stage == "gates"]
    gates += [
        (p.launch_ms, "run-long")
        for p in context.pairs
        if p.check_class == "plain-audit" and ws <= p.launch_ms < tip_start
    ]
    execute_at = max(firsts.get("execute", (ws, ""))[0], ws)
    later = sorted(g for g in gates if g[0] >= execute_at)
    if later:
        firsts["gates"] = later[0]
    commits = [
        e.t_ms
        for e in context.events
        if execute_at <= e.t_ms < tip_start
        and any(t.kind == "invocation" and t.a == "commit" for t in e.toks)
    ]
    if commits:
        firsts["commit"] = (commits[-1], "commit-marker")
    if f1 is not None:
        firsts["feature-tip-suite"] = (tip_start, "run-long")
    bricks = [
        p.launch_ms
        for p in context.pairs
        if p.check_class == "per-brick-sweep"
        and f1 is not None
        and d1 is not None
        and f1.terminal_ms <= p.launch_ms < d1.launch_ms
    ]
    if bricks:
        firsts["bricks"] = (min(bricks), "run-long")
    if d1 is not None:
        firsts["dev-tip-suite"] = (d1.launch_ms, "run-long")
    if dev_last is not None:
        firsts["closing"] = (dev_last.terminal_ms, "verdict")
    return firsts, runs


def _resolve_bounds(
    firsts: Dict[str, Tuple[int, str]], ws: int, we: int
) -> List[StageBound]:
    """Order the markers: clamp each into `[previous bound, we]`; an empty stage takes the next."""
    starts: List[Optional[int]] = []
    meta: List[Tuple[str, str]] = []
    prev = ws
    for stage in STAGES:
        if stage not in firsts:
            starts.append(None)
            meta.append(("none", "no-marker"))
            continue
        t, source = firsts[stage]
        t2 = min(max(prev, t), we)
        note = "clamped-later" if t2 > t else ("clamped-to-end" if t2 < t else "ok")
        starts.append(t2)
        meta.append((source, note))
        prev = t2
    resolved: List[int] = [0] * len(STAGES)
    following = we
    for index in range(len(STAGES) - 1, -1, -1):
        start = starts[index]
        following = following if start is None else start
        resolved[index] = following
    return [
        StageBound(stage, resolved[i], meta[i][0], meta[i][1])
        for i, stage in enumerate(STAGES)
    ]


def stage_cut(context: Context, window: Any) -> StageCut:
    """Cut one completed window into the `STAGES` and book each segment to its stage.

    A stage starts at its first marker (the first dispatch of its kind; the earliest
    same-sha feature-tip suite launch; the dev-tip launch; the dev-tip verdict), clamped so
    stages never run backwards. A stage with no marker is empty and its time stays in the
    stage before it. The cut's class seconds are the window's, split at the stage bounds.
    """
    ws, we = window.start_t_ms, window.end_t_ms
    firsts, tip_runs = _stage_firsts(context, window)
    bounds = _resolve_bounds(firsts, ws, we)
    starts = [b.start_ms for b in bounds]
    ends = starts[1:] + [we]
    segments = sweep_segments(window_intervals(context, window), ws, we, starts)
    count = len(STAGES)
    classes = [{name: 0.0 for name in CLASSES} for _ in range(count)]
    parts: List[Dict[str, Dict[str, float]]] = [{} for _ in range(count)]
    hidden = [0.0] * count
    for seg in segments:
        live = [
            i for i in range(count) if starts[i] <= seg.t0_ms and ends[i] > starts[i]
        ]
        index = max(live) if live else 0
        classes[index][seg.cls] += seg.dt_ms / 1000.0
        hidden[index] += seg.hidden_ms / 1000.0
        for label, secs in seg.parts.items():
            by_label = parts[index].setdefault(seg.cls, {})
            by_label[label] = by_label.get(label, 0.0) + secs
    rows = [
        StageRow(stage, (ends[i] - starts[i]) / 1000.0, classes[i])
        for i, stage in enumerate(STAGES)
    ]
    dev_runs = [window.d1] + list(window.chain)
    return StageCut(
        bounds,
        rows,
        segments,
        parts,
        hidden,
        tuple((p.terminal_ms - p.launch_ms) / 1000.0 for p in tip_runs),
        tuple((p.terminal_ms - p.launch_ms) / 1000.0 for p in dev_runs),
    )


def stage_bucket_s(cut: StageCut) -> Dict[str, float]:
    """Wall seconds per stage bucket (`STAGE_BUCKETS`); `other` is what no stage holds.

    With transcript data `other` is always 0: `_stage_firsts` skips a dispatch that maps to
    no stage and `StageCut.rows` covers `STAGES` only, so the cut partitions the window with
    nothing left over. A caller that wants to exercise the unmapped-bucket rule (the pool's
    POOLCHECK, t20b) must inject a bucket value; no real input produces one.
    """
    out = {name: 0.0 for name in STAGE_BUCKETS}
    for row in cut.rows:
        out[row.stage] += row.wall_s
    return out


def machine_wait_by_check(cut: StageCut) -> Dict[str, float]:
    """MACHINE_WAIT seconds per check class (`CHECK_CLASSES`) over the whole cut.

    `other` here is the unlabelled-machine-wait check class (`UNLABELLED-MACHINE-WAIT`),
    never a stage bucket.
    """
    out = {name: 0.0 for name in CHECK_CLASSES}
    for part in cut.parts:
        for label, secs in part.get("MACHINE_WAIT", {}).items():
            out[label] += secs
    return out


def sensitivity(
    cycle_set: Any,
    transcript: Any,
    params: Params,
    *,
    fence_method: str = "inclusive",
) -> List[SensitivityRow]:
    """Re-derive every window end under each of `SENSITIVITY_SETS` and pool the classes.

    The set constants are fixed (they never read the run's close flags); `moved` compares
    each end with the run's own window end. The pooled class seconds use the run's own
    HOOK and long-call constants and the run's fence. Tight and loose figures here are
    recomputed sweeps; only the delegate split of their SUBAGENT total is approximated
    later, by the primary fractions (L10).
    """
    context = build_context(cycle_set, transcript, params, fence_method)
    own = [w.end_t_ms for w in cycle_set.windows]
    out: List[SensitivityRow] = []
    for name, rerun, post, minimum in SENSITIVITY_SETS:
        alt = analyze_cycles(
            transcript,
            params._replace(
                close_rerun_min=rerun,
                close_post_min=post,
                close_min_suite_min=minimum,
            ),
        )
        pooled = {cls: 0.0 for cls in CLASSES}
        kept = moved = 0
        for index, win in enumerate(alt.windows):
            if index >= len(own) or win.end_t_ms != own[index]:
                moved += 1
            if win.end_t_ms is None:
                continue
            kept += 1
            spans = window_intervals(context, win)
            for cls, secs in sweep(spans, win.start_t_ms, win.end_t_ms).items():
                pooled[cls] += secs
        out.append(SensitivityRow(name, rerun, post, minimum, kept, moved, pooled))
    return out


def _long_calls(
    events: Sequence[Event], long_call_s: int
) -> List[Tuple[int, int, str]]:
    """`(start, end, tool_use_id)` of plain foreground Bash calls over `long_call_s`."""
    background: set = set()
    runlong: set = set()
    for event in events:
        for tok in event.toks:
            if tok.kind == "bash_bg" and tok.a:
                background.add(tok.a)
            elif tok.kind == "rl_check" and tok.b:
                runlong.add(tok.b)
    results = _first_results(events)
    out: List[Tuple[int, int, str]] = []
    for event in events:
        for tok in event.toks:
            if tok.kind != "tool_use" or tok.a != "Bash" or not tok.b:
                continue
            if tok.b in background or tok.b in runlong or tok.b not in results:
                continue
            if results[tok.b] - event.t_ms > long_call_s * 1000:
                out.append((event.t_ms, results[tok.b], tok.b))
    return out


def long_call_arm(context: Context, window: Any) -> LongCallArm:
    """The long foreground Bash calls that start in the window, split by `bash_class`.

    A plain Bash call over `Params.long_call_s` with no run-long token is taken to be a
    check (L06); its `bash_class` token says whether the command names a check tool. The
    seconds are the calls' own durations, clipped to the window, before the sweep shadows
    any of them under a higher class.
    """
    lo, hi = window.start_t_ms, window.end_t_ms
    token = {
        tok.a: tok.b
        for event in context.events
        for tok in event.toks
        if tok.kind == "bash_class" and tok.a
    }
    check = other = 0
    calls = 0
    for start, end, tool_id in _long_calls(context.events, context.params.long_call_s):
        if not lo <= start < hi:
            continue
        calls += 1
        if token.get(tool_id) == "check":
            check += min(end, hi) - start
        else:
            other += min(end, hi) - start
    return LongCallArm(calls, (check + other) / 1000.0, check / 1000.0, other / 1000.0)


def turn_duration_s(context: Context, window: Any) -> float:
    """Summed `turn_duration` seconds of the records inside the window (a cross-check, L15)."""
    lo, hi = window.start_t_ms, window.end_t_ms
    total = sum(
        tok.n or 0
        for event in context.events
        if lo <= event.t_ms <= hi
        for tok in event.toks
        if tok.kind == "turn_duration"
    )
    return total / 1000.0


def order_indicators(context: Context, window: Any) -> OrderIndicators:
    """Agent time hidden under machine wait (summed and as a union) and peer suite stall."""
    segments = window_segments(context, window)
    seen, recorded = peer_overlap_s(
        context.events, context.pairs, window.start_t_ms, window.end_t_ms
    )
    return OrderIndicators(
        sum(seg.hidden_agent_ms for seg in segments) / 1000.0,
        sum(seg.hidden_ms for seg in segments) / 1000.0,
        recorded,
        seen,
    )


def _away_s(context: Context, inner_s: float) -> float:
    """AWAY seconds over every completed window if the inner fence were `inner_s`."""
    fence = Fence(None, None, inner_s, None, context.fence.k, context.fence.method)
    moved = context._replace(booked=book_idle(context.entries, fence))
    total = 0.0
    for win in context.windows:
        spans = window_intervals(moved, win)
        total += sweep(spans, win.start_t_ms, win.end_t_ms)["AWAY"]
    return total


def fence_robust(context: Context) -> FenceRobust:
    """The inner fence with each gap left out in turn, and what that does to AWAY."""
    gaps = context.gaps_s
    base = context.fence.inner_s
    if base is None or len(gaps) < 3:
        return FenceRobust(
            len(gaps), base, None, None, 0, 0, 0, None, None, None, None, None, 0, 0
        )
    fences = _leave_one_out_fences(
        gaps, context.fence.k, context.fence.method, context.params.away_min_gaps
    )
    low, high = min(fences), max(fences)
    base_away = _away_s(context, base)
    shifted = [_away_s(context, f) for f in fences]
    above = [g for g in gaps if g > base]
    smallest = min(above) if above else None
    return FenceRobust(
        len(gaps),
        base,
        low,
        high,
        count_above(gaps, context.fence),
        sum(1 for g in gaps if g > low),
        sum(1 for g in gaps if g > high),
        smallest,
        None if smallest is None else 100.0 * (smallest - base) / base,
        base_away,
        _away_s(context, low),
        _away_s(context, high),
        sum(1 for s in shifted if abs(s - base_away) > AWAY_FLIP_S),
        len(fences),
    )


def suite_machine_wait(cut: StageCut) -> SuiteMach:
    """Suite MACHINE_WAIT seconds in the two tip-suite stages, with per-run band flags."""

    def suite_s(stage: str) -> float:
        wait = cut.parts[STAGES.index(stage)].get("MACHINE_WAIT", {})
        return sum(v for label, v in wait.items() if label.startswith("suite"))

    low, high = SUITEMACH_BAND_MIN

    def in_band(runs: Tuple[float, ...]) -> Optional[bool]:
        if not runs:
            return None
        return all(low <= secs / 60.0 <= high for secs in runs)

    return SuiteMach(
        suite_s("feature-tip-suite"),
        cut.feature_run_s,
        suite_s("dev-tip-suite"),
        cut.dev_run_s,
        in_band(cut.feature_run_s),
        in_band(cut.dev_run_s),
    )


def subcheck(context: Context) -> SubCheck:
    """The median and deciles of (enqueue minus dispatch) over the notification `duration_ms`.

    Only dispatches whose interval ended at the ENQUEUE record enter the ratio, as the spec
    defines it; one that ended at a delivery record (no enqueue) is counted in
    `not_enqueue` instead, so the figure is never a delivery-minus-dispatch in disguise.
    """
    durations: Dict[str, int] = {}
    for event in context.events:
        for tok in event.toks:
            if tok.kind == "notif_delivery" and tok.a and tok.n:
                durations.setdefault(tok.a, tok.n)
    ratios = sorted(
        (d.end_ms - d.start_ms) / durations[d.tool_use_id]
        for d in context.dispatches
        if d.end_source == "enqueue" and d.tool_use_id in durations
    )
    other = sum(
        1
        for d in context.dispatches
        if d.end_source == "delivery" and d.tool_use_id in durations
    )
    if not ratios:
        return SubCheck(0, None, None, None, None, other)
    median = statistics.median(ratios)
    low, high = SUBCHECK_BAND
    return SubCheck(
        len(ratios),
        median,
        ratios[int(0.1 * (len(ratios) - 1))],
        ratios[int(0.9 * (len(ratios) - 1))],
        low <= median <= high,
        other,
    )


class HookSensitivity(NamedTuple):
    """Pooled HOOK and CONTROLLER seconds over the completed windows at one threshold."""

    threshold_s: float
    primary: bool
    hook_s: float
    controller_s: float


#: The two fixed HOOK thresholds printed beside the primary one (spec t52), in seconds.
HOOK_SENSITIVITY_S: Tuple[int, int] = (5, 30)


def hook_sensitivity(
    cycle_set: Any,
    transcript: Any,
    params: Params,
    *,
    fence_method: str = "inclusive",
) -> List[HookSensitivity]:
    """Pooled HOOK and CONTROLLER at 5 s, the run's own threshold, and 30 s.

    The 5 s and 30 s entries are fixed and never read `params.hook_latency_s`; the middle
    entry is the primary threshold, taken from `params`, and is flagged `primary`.
    """
    out: List[HookSensitivity] = []
    steps = (
        (float(HOOK_SENSITIVITY_S[0]), False),
        (float(params.hook_latency_s), True),
        (float(HOOK_SENSITIVITY_S[1]), False),
    )
    for threshold, primary in steps:
        alt = params._replace(hook_latency_s=threshold)
        cycles = classify(cycle_set, transcript, alt, fence_method=fence_method).cycles
        out.append(
            HookSensitivity(
                threshold,
                primary,
                sum(c.class_s["HOOK"] for c in cycles),
                sum(c.class_s["CONTROLLER"] for c in cycles),
            )
        )
    return out


def hook_union_total(transcript: Any, threshold_ms: int) -> Tuple[int, float]:
    """Snapshot-wide `(calls, union_s)` of the slow edit-class calls on the main thread.

    A call is slow when its latency is strictly greater than `threshold_ms`. `union_s` is
    the UNION of those spans, not their sum: overlapping calls count once. `transcript` is
    a parsed transcript (sidechain records are excluded) or an event list.
    """
    events = transcript if isinstance(transcript, list) else main_events(transcript)
    spans = [
        (c.start_ms, c.end_ms)
        for c in edit_calls(events)
        if c.end_ms - c.start_ms > threshold_ms
    ]
    union_ms = sum(b - a for a, b in busy_union(spans))
    return len(spans), union_ms / 1000.0

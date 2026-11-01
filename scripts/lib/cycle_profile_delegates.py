"""Delegate transcripts: match files to dispatches and split SUBAGENT seconds three ways.

Reads the delegate (subagent) transcript files that sit beside a session transcript, matches
each file to a main-thread dispatch by agent id, and splits every SUBAGENT second of a
completed cycle into GATE, HOOK and WORK, plus UNSPLIT where no file matches. It renders
nothing and keeps tokens and ids only: no delegate text leaves the reader.

**Matching.** A dispatch's id is its acknowledgement's agent id, else the task id of its
notification (the two are equal in a real session; `map_check` counts where both exist and
agree). An id that does not fullmatch `ID_RE` is never used, so no id can name a path outside
the delegate directory. The file is `agent-<id>.jsonl`. A dispatch with no interval (an
orphan, or one whose end is not after its start) is counted in `total` and never matched, and
its file is not read.

**Reading.** Delegate records are flagged `isSidechain`, so every event of a file is read,
never the main-thread filter. Each file is read once with `read_transcript`: the bytes parsed
end at the last newline of the size at open, a corrupt line is counted and skipped, and the
inventory entry `{id, bytes, sha256}` holds the size and hash of exactly those bytes. A file
that is missing, unreadable, holds no timestamped event (`unusable`: it says nothing about
its dispatch, so it neither matches nor enters the inventory) or (when pinned) is shorter
than its pin is UNSPLIT, never zero minutes. With `pins`, a pinned file is read to its pinned size and a prefix-hash mismatch
raises `PinError`; an id with no pin is read to its current size.

**The split** (the original's rule, in integer milliseconds). Inside each completed window a
dispatch's interval is the one `window_spans` builds: it joins when it STARTS in the window
and is clipped to it. A second of the window counts only when no MACHINE_WAIT interval of the
window covers it (the classes sweep's higher priority) and some dispatch interval does; its
seconds are shared equally between the dispatches active in it, and each takes its OWN inner
class from its file:

- GATE_RUNLONG: a Bash call that names `run-long.sh`, from its tool_use to its tool_result.
- GATE_LONGCALL: any other Bash call blocked for strictly more than `Params.long_call_s`
  (background calls included, as the original did; `call_counts` reports them for L12). It is
  split by its `bash_class` token into LONGCALL_CHECK and LONGCALL_OTHER.
- HOOK: an Edit, Write or MultiEdit call blocked for strictly more than
  `Params.hook_latency_s`.
- WORK: every other delegate second, the residual (L08).

Priority is GATE_RUNLONG, then GATE_LONGCALL, then HOOK, then WORK. `swap_priority=True`
puts HOOK first: the LIMIT-g sensitivity for an Edit that sits inside a Bash call
(`priority_sensitivity`). A call without a result adds nothing. A dispatch with no file takes
UNSPLIT. The four parts sum to the classes module's SUBAGENT seconds of the window.

**Choices made where the spec is silent.** The original sub-split LONGCALL-other by the
leading program word; the reader emits no such token, so only check/other survives. The
original's EDGES information line (delegate-silent seconds at the ends of a matched interval)
is not ported. `DelegateRecord.split_s` is the file's seconds over its own dispatch intervals,
with no machine-wait shadow and no sharing, at the `Params` given to `read_delegates`.
`DelegateSet.records` is a `DelegateIndex`: a dict of agent id to `DelegateRecord` that also
carries the parsed call lists `split` needs, so a file is never read twice.
"""

from __future__ import annotations

import os
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, NamedTuple, Optional, Sequence, Tuple

from cycle_profile_classes import (
    EditCall,
    build_context,
    edit_calls,
    hook_spans,
    window_intervals,
)
from cycle_profile_model import (
    ID_RE,
    DelegateRecord,
    DelegateSet,
    Dispatch,
    Params,
)
from cycle_profile_records import (
    PinError,
    ReadError,
    read_transcript,
    reread,
    sorted_events,
)

#: The seconds of a split, as keys. The first four sum to the SUBAGENT seconds.
PART_KEYS: Tuple[str, ...] = (
    "SUBAGENT_WORK",
    "SUBAGENT_GATE",
    "SUBAGENT_HOOK",
    "SUBAGENT_UNSPLIT",
)
#: `SUBAGENT_GATE` is `GATE_RUNLONG + GATE_LONGCALL`; `GATE_LONGCALL` is the two LONGCALL keys.
ARM_KEYS: Tuple[str, ...] = (
    "GATE_RUNLONG",
    "GATE_LONGCALL",
    "LONGCALL_CHECK",
    "LONGCALL_OTHER",
)
SPLIT_KEYS: Tuple[str, ...] = PART_KEYS + ARM_KEYS
#: The label the swapped-priority sensitivity carries (`SENS priority-swapped`).
SENS_PRIORITY_SWAPPED = "priority-swapped"
#: The share of dispatches that must be matched, as a ratio of integers (80 percent).
WEAK_NUM, WEAK_DEN = 4, 5

_FAR_MS = 1 << 62
_HEX64 = frozenset("0123456789abcdef")

Pair = Tuple[int, int]


class Span(NamedTuple):
    """One dispatch interval inside a window: `[a, b)` in epoch milliseconds."""

    tool_use_id: str
    role: str
    agent_id: Optional[str]
    a: int
    b: int


class Coverage(NamedTuple):
    """Matched dispatches over all dispatches; `weak` is under 80 percent matched."""

    matched: int
    total: int
    orphans: int
    weak: bool


class MapCheck(NamedTuple):
    """MAPCHECK counts: dispatches with an id, with both ids, and where both agree."""

    dispatches: int
    with_id: int
    both: int
    agree: int


class CallCounts(NamedTuple):
    """Delegate-file call counts for L12."""

    files: int
    runlong_calls: int
    background_calls: int


class SplitResult(NamedTuple):
    """`cycles` holds one `SPLIT_KEYS` dict per completed window; `by_role` is pooled."""

    cycles: List[Dict[str, float]]
    by_role: Dict[str, Dict[str, float]]


class PrioritySensitivity(NamedTuple):
    """The pooled split under the documented priority and under HOOK-first."""

    name: str
    primary: Dict[str, float]
    swapped: Dict[str, float]


class _Calls(NamedTuple):
    """The calls of one delegate file that have a result, as millisecond spans."""

    edits: List[EditCall]
    runlong: List[Pair]
    longs: List[Tuple[int, int, str]]
    runlong_calls: int
    background_calls: int


class DelegateIndex(dict):
    """Agent id to `DelegateRecord`, plus what `split` and the report need.

    `calls`, `expired`, `unusable` and `unparseable` are attributes of the object, not dict
    state: `==`, `bool()`, `dict(...)`, `{**index}`, copies and JSON drop them, so downstream
    code must read them only from the object `read_delegates` returned. A plain dict in
    their place is tolerated by `split_context` and `call_counts` (no calls known, so every
    dispatch is UNSPLIT).

    `DelegateRecord.split_s` is per file over its own dispatch intervals, with no sharing
    between dispatches, so two dispatches of one agent that overlap in time double-count
    those seconds. `split` shares seconds and is the figure to use.

    Attributes:
        calls: Agent id to the parsed call lists of that file.
        expired: Pinned ids whose file was missing, unreadable or shorter than its pin.
        unusable: Ids of files read that hold no timestamped event (UNSPLIT, not matched,
            not in the inventory).
        unparseable: Corrupt lines skipped across all files read.
    """

    def __init__(self) -> None:
        super().__init__()
        self.calls: Dict[str, _Calls] = {}
        self.expired: List[str] = []
        self.unusable: List[str] = []
        self.unparseable = 0


# ---------------------------------------------------------------------------------------------
# Matching and the dispatch intervals
# ---------------------------------------------------------------------------------------------


def _usable(value: Any) -> Optional[str]:
    """`value` when it is an id that may name a file, else `None`."""
    if isinstance(value, str) and ID_RE.fullmatch(value):
        return value
    return None


def _agent_id(dispatch: Dispatch) -> Optional[str]:
    """The id naming a dispatch's delegate file: the ack's agent id, else the task id."""
    return _usable(dispatch.agent_id) or _usable(dispatch.task_id)


def _has_interval(dispatch: Dispatch) -> bool:
    return dispatch.end_ms is not None and dispatch.end_ms > dispatch.start_ms


def map_check(dispatches: Iterable[Dispatch]) -> MapCheck:
    """MAPCHECK: the ack's agent id against the notification's task id, where both exist."""
    total = with_id = both = agree = 0
    for d in dispatches:
        total += 1
        with_id += 1 if _agent_id(d) else 0
        if _usable(d.agent_id) and _usable(d.task_id):
            both += 1
            agree += 1 if d.agent_id == d.task_id else 0
    return MapCheck(total, with_id, both, agree)


def cycle_dispatches(
    dispatches: Iterable[Dispatch], windows: Iterable[Any]
) -> List[Dispatch]:
    """The dispatches that start inside a completed window, in their given order."""
    bounds = [(w.start_t_ms, w.end_t_ms) for w in windows if w.status == "completed"]
    return [d for d in dispatches if any(lo <= d.start_ms < hi for lo, hi in bounds)]


def window_spans(
    dispatches: Sequence[Dispatch], bounds: Sequence[Pair]
) -> List[List[Span]]:
    """Each window's dispatch intervals: those that START in `[lo, hi)`, clipped to it.

    Args:
        dispatches: The classes module's dispatches (`dispatch_intervals`).
        bounds: One `(lo_ms, hi_ms)` pair per window.

    Returns:
        One list per pair, in dispatch order. A dispatch without an interval is left out.
    """
    out: List[List[Span]] = []
    for lo, hi in bounds:
        row = []
        for d in dispatches:
            if not _has_interval(d) or not lo <= d.start_ms < hi:
                continue
            row.append(
                Span(
                    d.tool_use_id,
                    d.role,
                    _agent_id(d),
                    max(d.start_ms, lo),
                    min(d.end_ms, hi),
                )
            )
        out.append(row)
    return out


# ---------------------------------------------------------------------------------------------
# Reading one delegate file
# ---------------------------------------------------------------------------------------------


def _first_results(events: Sequence[Any]) -> Dict[str, int]:
    """Tool-use id to the time of its first `tool_result` (events in time order)."""
    out: Dict[str, int] = {}
    for event in events:
        for tok in event.toks:
            if tok.kind == "tool_result" and tok.a:
                out.setdefault(tok.a, event.t_ms)
    return out


def _parse_calls(events: Sequence[Any]) -> _Calls:
    """Tool calls of a delegate file with a result; only tokens and ids are read."""
    results = _first_results(events)
    runlong, background, classes = set(), set(), {}
    starts: Dict[str, Tuple[int, str]] = {}
    for event in events:
        for tok in event.toks:
            if tok.kind == "rl_check" and tok.b:
                runlong.add(tok.b)
            elif tok.kind == "bash_bg" and tok.a:
                background.add(tok.a)
            elif tok.kind == "bash_class" and tok.a:
                classes[tok.a] = tok.b or "other"
            elif tok.kind == "tool_use" and tok.b and tok.a == "Bash":
                starts.setdefault(tok.b, (event.t_ms, tok.a))
    rl_spans: List[Pair] = []
    longs: List[Tuple[int, int, str]] = []
    for tool_id, (start, _name) in starts.items():
        end = results.get(tool_id)
        if end is None:
            continue
        if tool_id in runlong:
            rl_spans.append((start, end))
        else:
            longs.append((start, end, classes.get(tool_id, "other")))
    return _Calls(
        edit_calls(events),
        sorted(rl_spans),
        sorted(longs),
        len(runlong & set(starts)),
        len(background & set(starts)),
    )


def _clip(spans: Iterable[Pair], lo: int, hi: int) -> List[Pair]:
    out = []
    for a, b in spans:
        a2, b2 = max(a, lo), min(b, hi)
        if b2 > a2:
            out.append((a2, b2))
    return out


def _arms(calls: _Calls, params: Params, lo: int, hi: int) -> Dict[str, List[Pair]]:
    """The inner-class intervals of a file clipped to `[lo, hi)`, by arm label."""
    limit = params.long_call_s * 1000
    long_all = [(a, b, cls) for a, b, cls in calls.longs if b - a > limit]
    hook = hook_spans(calls.edits, params.hook_latency_s * 1000, 0, _FAR_MS)
    return {
        "rl": _clip(calls.runlong, lo, hi),
        "lc": _clip([(a, b) for a, b, cls in long_all if cls == "check"], lo, hi),
        "lo": _clip([(a, b) for a, b, cls in long_all if cls != "check"], lo, hi),
        "hk": _clip(hook, lo, hi),
    }


# ---------------------------------------------------------------------------------------------
# The sweep
# ---------------------------------------------------------------------------------------------


def _new_split() -> Dict[str, float]:
    return {key: 0.0 for key in SPLIT_KEYS}


def _inner_key(state: Counter, swap: bool) -> Tuple[str, ...]:
    """The keys one active dispatch's second adds to, by the inner priority."""
    if swap and state["hk"] > 0:
        return ("SUBAGENT_HOOK",)
    if state["rl"] > 0:
        return ("SUBAGENT_GATE", "GATE_RUNLONG")
    if state["lc"] > 0:
        return ("SUBAGENT_GATE", "GATE_LONGCALL", "LONGCALL_CHECK")
    if state["lo"] > 0:
        return ("SUBAGENT_GATE", "GATE_LONGCALL", "LONGCALL_OTHER")
    if state["hk"] > 0:
        return ("SUBAGENT_HOOK",)
    return ("SUBAGENT_WORK",)


def _sweep(
    spans: Sequence[Span],
    calls: Dict[str, _Calls],
    machine: Sequence[Pair],
    lo: int,
    hi: int,
    params: Params,
    swap: bool,
) -> Dict[int, Dict[str, float]]:
    """Split `[lo, hi)` between the active spans: seconds by split key, per span index."""
    points = {lo, hi}
    edges: Dict[int, List[Tuple[int, str, int]]] = defaultdict(list)

    def add(index: int, label: str, a: int, b: int) -> None:
        if b > a:
            edges[a].append((index, label, 1))
            edges[b].append((index, label, -1))
            points.update((a, b))

    for a, b in machine:
        add(-1, "m", max(a, lo), min(b, hi))
    for index, span in enumerate(spans):
        add(index, "s", max(span.a, lo), min(span.b, hi))
        file_calls = calls.get(span.agent_id or "")
        if file_calls is not None:
            for label, arm in _arms(file_calls, params, span.a, span.b).items():
                for a, b in arm:
                    add(index, label, a, b)
    state: Dict[int, Counter] = defaultdict(Counter)
    active: set = set()
    out: Dict[int, Dict[str, float]] = defaultdict(_new_split)
    ordered = sorted(points)
    for position in range(len(ordered) - 1):
        t0, t1 = ordered[position], ordered[position + 1]
        for index, label, step in edges.get(t0, ()):
            state[index][label] += step
            if label == "s":
                if state[index]["s"] > 0:
                    active.add(index)
                else:
                    active.discard(index)
        if state[-1]["m"] > 0 or not active:
            continue
        share = (t1 - t0) / 1000.0 / len(active)
        for index in active:
            if spans[index].agent_id not in calls:
                keys: Tuple[str, ...] = ("SUBAGENT_UNSPLIT",)
            else:
                keys = _inner_key(state[index], swap)
            for key in keys:
                out[index][key] += share
    return out


def _total(rows: Iterable[Dict[str, float]]) -> Dict[str, float]:
    out = _new_split()
    for row in rows:
        for key, value in row.items():
            out[key] += value
    return out


def split_context(
    context: Any, delegate_set: DelegateSet, *, swap_priority: bool = False
) -> SplitResult:
    """The split of every completed window of a classes `Context`.

    Args:
        context: `cycle_profile_classes.build_context` of the same transcript.
        delegate_set: `read_delegates` over the dispatches of that transcript.
        swap_priority: Put HOOK above GATE (the LIMIT-g sensitivity).
    """
    calls = getattr(delegate_set.records, "calls", {})
    cycles: List[Dict[str, float]] = []
    roles: Dict[str, List[Dict[str, float]]] = defaultdict(list)
    for window in context.windows:
        lo, hi = window.start_t_ms, window.end_t_ms
        spans = window_spans(context.dispatches, [(lo, hi)])[0]
        machine = [
            (a, b) for a, b, _ in window_intervals(context, window)["MACHINE_WAIT"]
        ]
        per_span = _sweep(spans, calls, machine, lo, hi, context.params, swap_priority)
        cycles.append(_total(per_span.values()))
        for index, row in per_span.items():
            roles[spans[index].role].append(row)
    return SplitResult(cycles, {role: _total(rows) for role, rows in roles.items()})


def split(
    cycle_set: Any,
    transcript: Any,
    params: Params,
    delegate_set: DelegateSet,
    *,
    swap_priority: bool = False,
) -> SplitResult:
    """`split_context` over a context built from the cycle set and the transcript."""
    context = build_context(cycle_set, transcript, params)
    return split_context(context, delegate_set, swap_priority=swap_priority)


def priority_sensitivity(
    cycle_set: Any, transcript: Any, params: Params, delegate_set: DelegateSet
) -> PrioritySensitivity:
    """The pooled split under the documented order and under HOOK above GATE."""
    context = build_context(cycle_set, transcript, params)
    primary = split_context(context, delegate_set)
    swapped = split_context(context, delegate_set, swap_priority=True)
    return PrioritySensitivity(
        SENS_PRIORITY_SWAPPED, _total(primary.cycles), _total(swapped.cycles)
    )


# ---------------------------------------------------------------------------------------------
# Reading the directory
# ---------------------------------------------------------------------------------------------


def _pin_map(pins: Optional[Iterable[Any]]) -> Dict[str, Tuple[int, str]]:
    """Validate `--delegate-pins` entries into `{id: (bytes, sha256)}`.

    Identical duplicate entries are tolerated; two entries with one id and different values
    are malformed.

    Raises:
        ValueError: An entry is not `{id, bytes, sha256}` of the right shapes.
    """
    out: Dict[str, Tuple[int, str]] = {}
    for pin in pins or ():
        ok = isinstance(pin, dict) and _usable(pin.get("id")) is not None
        size = pin.get("bytes") if ok else None
        digest = pin.get("sha256") if ok else None
        ok = (
            ok
            and isinstance(size, int)
            and not isinstance(size, bool)
            and size >= 0
            and isinstance(digest, str)
            and len(digest) == 64
            and set(digest.lower()) <= _HEX64
        )
        if not ok:
            raise ValueError("a delegate pin needs an id, a byte size and a sha256")
        value = (size, digest.lower())
        if out.get(pin["id"], value) != value:
            raise ValueError("a delegate pin needs an id, a byte size and a sha256")
        out[pin["id"]] = value
    return out


def _file(directory: Any, agent_id: str) -> Path:
    return Path(directory) / ("agent-%s.jsonl" % agent_id)


def _load(
    directory: Any, agent_id: str, pin: Optional[Tuple[int, str]], index: DelegateIndex
) -> Any:
    """Read one file (to its pin when pinned); `None` when it is missing or expired."""
    path = _file(directory, agent_id)
    if pin is None:
        try:
            return read_transcript(path)
        except ReadError:
            return None
    try:
        size = os.stat(path).st_size
    except OSError:
        index.expired.append(agent_id)
        return None
    if size < pin[0]:
        index.expired.append(agent_id)
        return None
    try:
        transcript = read_transcript(path, prefix_bytes=pin[0], prefix_sha256=pin[1])
    except ReadError:
        index.expired.append(agent_id)
        return None
    except PinError:
        try:
            shrank = os.stat(path).st_size < pin[0]
        except OSError:
            shrank = True
        if not shrank:
            raise
        index.expired.append(agent_id)
        return None
    if transcript.snapshot.bytes != pin[0]:
        raise ValueError("a delegate pin must end at a line boundary")
    return transcript


def _record_split(
    spans: Iterable[Span], calls: _Calls, params: Params
) -> Dict[str, float]:
    """A file's seconds over its own dispatch intervals: no machine shadow, no sharing."""
    rows = []
    for span in spans:
        only = {span.agent_id or "": calls}
        sole = Span(span.tool_use_id, span.role, span.agent_id, span.a, span.b)
        rows.extend(_sweep([sole], only, (), span.a, span.b, params, False).values())
    return _total(rows)


def read_delegates(
    directory: Any,
    dispatches: Sequence[Dispatch],
    params: Optional[Params] = None,
    *,
    pins: Optional[Iterable[Any]] = None,
) -> DelegateSet:
    """Match the dispatches to their delegate files and read each file once.

    Args:
        directory: The delegate directory; a missing or empty one matches nothing.
        dispatches: The dispatches to match (callers pass `cycle_dispatches` to count only
            those inside completed windows).
        params: Thresholds for each record's own split; `Params()` when omitted.
        pins: `--delegate-pins` entries `{id, bytes, sha256}`: a pinned file is read to its
            pinned size and its prefix hash must match.

    Returns:
        A `DelegateSet`: `records` is a `DelegateIndex`, `inventory` lists `{id, bytes,
        sha256}` of exactly the bytes read, `matched` counts dispatches with an interval and
        a file read, `total` all dispatches, `orphans` the tool-use ids with no interval.

    Raises:
        ValueError: A pin is malformed, conflicts with another pin of the same id, or its
            size ends inside a line.
        PinError: A pinned file's prefix hash differs from its pin.
    """
    params = params or Params()
    pin_map = _pin_map(pins)
    wanted: Dict[str, List[Dispatch]] = {}
    for d in dispatches:
        agent_id = _agent_id(d)
        if agent_id and _has_interval(d):
            wanted.setdefault(agent_id, []).append(d)
    index = DelegateIndex()
    inventory: List[Dict[str, Any]] = []
    matched = 0
    for agent_id in sorted(wanted):
        transcript = _load(directory, agent_id, pin_map.get(agent_id), index)
        if transcript is None:
            continue
        events = sorted_events(transcript)
        if not events:
            index.unusable.append(agent_id)
            continue
        calls = _parse_calls(events)
        spans = [
            Span(d.tool_use_id, d.role, agent_id, d.start_ms, d.end_ms)
            for d in wanted[agent_id]
        ]
        snap = transcript.snapshot
        index[agent_id] = DelegateRecord(
            agent_id, _record_split(spans, calls, params), snap.bytes, snap.sha256
        )
        index.calls[agent_id] = calls
        index.unparseable += transcript.unparseable
        inventory.append({"id": agent_id, "bytes": snap.bytes, "sha256": snap.sha256})
        matched += len(spans)
    orphans = [d.tool_use_id for d in dispatches if d.end_source == "orphan"]
    return DelegateSet(index, inventory, matched, len(dispatches), orphans)


def coverage(delegate_set: DelegateSet) -> Coverage:
    """Matched over total dispatches; `weak` when under 80 percent (never with none)."""
    matched, total = delegate_set.matched, delegate_set.total
    weak = total > 0 and matched * WEAK_DEN < total * WEAK_NUM
    return Coverage(matched, total, len(delegate_set.orphans), weak)


def coverage_line(cov: Coverage) -> str:
    """The bound output line `COVERAGE matched=<m>/<n>`."""
    return "COVERAGE matched=%d/%d" % (cov.matched, cov.total)


def call_counts(delegate_set: DelegateSet) -> CallCounts:
    """Files read, run-long calls and background Bash calls in them (L12)."""
    calls = list(getattr(delegate_set.records, "calls", {}).values())
    return CallCounts(
        len(calls),
        sum(c.runlong_calls for c in calls),
        sum(c.background_calls for c in calls),
    )


def inventory_status(inventory: Iterable[Any], directory: Any) -> str:
    """`current` unless a recorded file is missing, shorter, or hashes differently.

    Only the recorded prefix is judged, so bytes appended to a live delegate file leave the
    inventory `current`. A malformed entry is `expired`. An empty inventory is `current`
    vacuously: it is not evidence that any file exists.
    """
    for entry in inventory:
        agent_id = _usable(entry.get("id")) if isinstance(entry, dict) else None
        size = entry.get("bytes") if agent_id else None
        digest = entry.get("sha256") if agent_id else None
        if (
            agent_id is None
            or isinstance(size, bool)
            or not isinstance(size, int)
            or size < 0
            or not isinstance(digest, str)
        ):
            return "expired"
        try:
            reread(_file(directory, agent_id), size, digest)
        except (PinError, ReadError):
            return "expired"
    return "current"

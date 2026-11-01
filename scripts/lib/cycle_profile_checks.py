"""The cycle-profile self-checks: each compares a primary result with a SECOND SIDE.

Merged into one process, a check that re-uses the code under test compares that code with
itself, so every `check_<name>` here names its second side, and a check whose second side is
the same code is not a check. A check whose second side is absent in a run returns `skip`,
never `pass`. Each returns one `CheckRow(name, verdict, detail)`, except `check_poolcheck`,
which returns the POOLCHECK family: a list of rows whose LAST row is the verdict row named
`POOLCHECK`. The `detail` of every row this module builds is a short closed phrase of
integers and lowercase tokens (`windows=3 bad=0`); no transcript text and no float reaches
one. The exception is the pass-through: the family rows `POOLCHECK-ARITH`,
`POOLCHECK-STAGE-BUCKET` and the other rows `pool.poolcheck` returns keep that module's own
details, which hold floats and booleans (`ranked_s=2370.000`, `once=True`); they are still
numbers and closed tokens, never transcript text. A check handed a `None` primary input
returns `skip` with the reason `no-input`. The run module calls the
checks with RESULT OBJECTS (the cycle set, the context, the class set, the split, the pool).

**The four independent second sides** (`pairing_oracle`, `plain_scan`, `idle_scan`,
`brute_force_classes`) reference no function, class or constant of the classes, cycles,
delegates or pool modules, directly or through a helper of this module (test row t39s walks
their bodies). They take the reader's tokens (`Event` lists) or the interval lists, and
nothing else. The `check_<name>` functions that call them MAY call the primary code: that
call is the primary side.

| Check | Primary side | Second side | What the two share |
| :-- | :-- | :-- | :-- |
| SUMCHECK | `ClassCycle.class_s` sums | the roster window `end - start` | nothing |
| CLASSORACLE | `ClassCycle.class_s` | `brute_force_classes` | the per-window interval lists |
| STAGECHECK | `StageRow` walls and class sums | the stage cut's boundaries | the cut's bounds |
| PAIRCHECK | `CycleSet.pairs` | `pairing_oracle` (both arms of the original's rule) | the extracted call list (tokens) |
| SPLITCHECK | the split's four parts | `class_s["SUBAGENT"]` of the sweep | nothing |
| INTERVALCHECK | delegates `window_spans` | classes `window_intervals` (clipped) | the dispatch list |
| ARMCHECK | split arm fields | the identities between them | nothing |
| POOLCHECK | the pool's ranking and corners | the pooled class totals | nothing |
| HALFCHECK | `PoolResult.halves` | the pooled walls | nothing |
| IDLECHECK | the idle list and its quartiles | `idle_scan` | the machine and subagent intervals |
| FENCECHECK | `Fence` | quartiles recomputed from the gap list | the gap list |
| FENCE-ROBUST | `FenceRobust` min and max | leave-one-out fences recomputed | the gap list |
| HOOKCHECK | `hook_union_total`, the HOOK class set, `hook_reported_s` | `plain_scan` | the extracted call list |
| MAPCHECK | `delegates.map_check` counts | a recount of the dispatches | the dispatch list |
| DENOMCHECK | the profile's percentage fields | the denominators beside them, the text | nothing |

**Choices made where the spec is silent.**

- PAIRCHECK judges EVERY launch, unresolved keys included: the oracle embodies the
  original's whole rule (a key arm bounded at the next launch of the same key, then a
  nearest-background-wait arm), so any disagreement fails. The oracle is not tuned to agree
  with the cycles module.
- DOMINANT-CHECK is `dominant_hook_call`, a computed result (the largest HOOK call and its
  share of the HOOK union) with no verdict. It names the largest call, as D2.2 says.
- A check never compares two ABSENT readings: with no edit call, no gap, no dispatch holding
  both ids or no completed window, the check skips.
- Tolerances: seconds to a millisecond (`_TOL_S`), percentages to a hundredth.
"""

from __future__ import annotations

import math
import re
from typing import Any, Dict, Iterable, List, NamedTuple, Optional, Sequence, Tuple

from cycle_profile_classes import hook_reported_s, hook_union_total, window_intervals
from cycle_profile_delegates import PART_KEYS, map_check, window_spans
from cycle_profile_model import ART_UNRESOLVED_PREFIX, ID_RE, CheckRow
from cycle_profile_pool import poolcheck
from cycle_profile_records import sorted_events

#: The ordered check names; `check_<name>` (lower case, `-` as `_`) is the function.
CHECK_NAMES: Tuple[str, ...] = (
    "SUMCHECK",
    "CLASSORACLE",
    "STAGECHECK",
    "PAIRCHECK",
    "SPLITCHECK",
    "INTERVALCHECK",
    "ARMCHECK",
    "POOLCHECK",
    "HALFCHECK",
    "IDLECHECK",
    "FENCECHECK",
    "FENCE-ROBUST",
    "HOOKCHECK",
    "MAPCHECK",
    "DENOMCHECK",
)
#: The declared floor: fewer check functions than this means one was removed.
CHECK_FLOOR = 15

#: Seconds compare to a millisecond; percentages to a hundredth.
_TOL_S = 0.001
_TOL_PCT = 0.01
_REL = 1e-9
_FAR_MS = 1 << 62

# The second sides keep their own copies of the spec's constants (D2.5, L02, L04), so that
# changing a primary constant moves only the primary side.
_EDIT_TOOLS = frozenset(("Edit", "Write", "MultiEdit"))
_CLASS_NAMES = (
    "CONTROLLER",
    "SUBAGENT",
    "MACHINE_WAIT",
    "HOOK",
    "OPERATOR_RESPONSE",
    "AWAY",
)
_SWEEP_ORDER = ("MACHINE_WAIT", "SUBAGENT", "HOOK", "OPERATOR_RESPONSE", "AWAY")
_REMAINDER_MS = 1000
_NEAREST_MS = 180000
_OUTER_MULTIPLIER = 3.0
_NON_FINITE_RE = re.compile(r"\b(nan|inf|infinity)\b", re.IGNORECASE)


class PlainScan(NamedTuple):
    """`calls`: `(start_ms, end_ms, name)` per finished edit-class call; `reported`:
    `(t_ms, durationMs)` per `hook_success` record."""

    calls: List[Tuple[int, int, str]]
    reported: List[Tuple[int, int]]


class OraclePair(NamedTuple):
    """One launch as the oracle pairs it; `resolved` is false for a shell-variable key.

    `via` is `key` (the key arm), `nearest` (the nearest-wait arm) or `none` (unpaired).
    """

    launch_ms: int
    terminal_ms: Optional[int]
    key: Optional[str]
    resolved: bool
    via: str


class IdleScanEntry(NamedTuple):
    """One idle gap: `seconds` is the whole gap (`pure`) or its job-free part (`remainder`)."""

    start_ms: int
    end_ms: int
    seconds: float
    kind: str


class DominantHook(NamedTuple):
    """DOMINANT-CHECK: the largest HOOK call and its share of the HOOK union.

    `status` is `measured` or `none` (no qualifying call). `share_pct` is `None` for `none`.
    """

    status: str
    calls: int
    largest_ms: int
    union_ms: int
    share_pct: Optional[float]


class _OwnFence(NamedTuple):
    q1: float
    q3: float
    inner_s: float
    outer_s: float


# ---------------------------------------------------------------------------------------------
# Small shared helpers (no primary code)
# ---------------------------------------------------------------------------------------------


def _row(name: str, ok: Optional[bool], detail: str) -> CheckRow:
    return CheckRow(name, "skip" if ok is None else ("pass" if ok else "fail"), detail)


def _skip(name: str, reason: str) -> CheckRow:
    return CheckRow(name, "skip", reason)


def _completed(cycle_set: Any) -> List[Any]:
    return [w for w in cycle_set.windows if w.status == "completed"]


def _main_events(transcript: Any) -> List[Any]:
    """The timestamped main-thread events in time order (the extracted call list)."""
    return [e for e in sorted_events(transcript) if not e.sidechain]


def _near(a: float, b: float, tol: float) -> bool:
    return abs(a - b) <= tol


def _close(a: float, b: float) -> bool:
    return abs(a - b) <= _REL * max(1.0, abs(b))


def _zip(*sequences: Sequence[Any]) -> Iterable[Tuple[Any, ...]]:
    """`zip` for Python 3.9 (no `strict=`): the callers check lengths, or want the shortest."""
    return zip(*sequences)  # noqa: B905


def _union_ms(spans: Iterable[Tuple[int, int]]) -> int:
    total = 0
    start: Optional[int] = None
    end = 0
    for a, b in sorted(spans):
        if start is not None and a <= end:
            end = max(end, b)
        else:
            if start is not None:
                total += end - start
            start, end = a, b
    return total + (0 if start is None else end - start)


def _overlapping_pairs(spans: Sequence[Tuple[int, int]]) -> int:
    count = 0
    for i, (a1, b1) in enumerate(spans):
        for a2, b2 in spans[i + 1 :]:
            if a2 < b1 and a1 < b2:
                count += 1
    return count


def _quartile(values: Sequence[float], index: int, method: str) -> float:
    """Quartile `index` (1 to 3) of ascending `values`; `inclusive` or `exclusive`."""
    n = len(values)
    if method == "exclusive":
        pos = index * (n + 1) / 4.0
        j = min(max(int(pos), 1), n - 1)
        return values[j - 1] + (values[j] - values[j - 1]) * (pos - j)
    pos = (n - 1) * index / 4.0
    j = int(math.floor(pos))
    upper = min(j + 1, n - 1)
    return values[j] + (values[upper] - values[j]) * (pos - j)


def _own_fence(gaps_s: Sequence[float], k: float, method: str) -> _OwnFence:
    """The Tukey fences over log10 of `gaps_s`, recomputed from the gap list alone."""
    logs = sorted(math.log10(g) for g in gaps_s)
    q1 = _quartile(logs, 1, method)
    q3 = _quartile(logs, 3, method)
    iqr = q3 - q1
    return _OwnFence(q1, q3, 10 ** (q3 + k * iqr), 10 ** (q3 + _OUTER_MULTIPLIER * iqr))


# ---------------------------------------------------------------------------------------------
# The independent second sides
# ---------------------------------------------------------------------------------------------


def plain_scan(events: Sequence[Any]) -> PlainScan:
    """The edit-class calls and `hook_success` durations, read straight from the tokens.

    A call is an Edit, Write or MultiEdit `tool_use` whose first `tool_result` came later.
    Sidechain records and records without a timestamp take no part. Shares nothing with the
    classes module's call extraction.
    """
    started: Dict[str, Tuple[int, str]] = {}
    answered: Dict[str, int] = {}
    reported: List[Tuple[int, int]] = []
    for event in events:
        if event.sidechain or event.t_ms is None:
            continue
        for tok in event.toks:
            if tok.kind == "tool_use" and tok.a in _EDIT_TOOLS and tok.b:
                started.setdefault(tok.b, (event.t_ms, tok.a))
            elif tok.kind == "tool_result" and tok.a:
                answered.setdefault(tok.a, event.t_ms)
            elif tok.kind == "hook_success":
                reported.append((event.t_ms, tok.n or 0))
    calls = sorted(
        (begin, answered[tool_id], name)
        for tool_id, (begin, name) in started.items()
        if tool_id in answered
    )
    return PlainScan(calls, reported)


def _is_ask(tok: Any, asks: set) -> bool:
    return tok.kind == "tool_result" and tok.a in asks


def _scan_mark(event: Any, asks: set) -> Optional[str]:
    """`A` for an assistant record, `H` for an operator turn, else `None`."""
    if event.rtype == "assistant":
        return "A"
    kinds = {tok.kind for tok in event.toks}
    if "interrupt" in kinds:
        return None
    if "human_prompt" in kinds or "auto_continuation" in kinds:
        return "H"
    if any(_is_ask(tok, asks) for tok in event.toks):
        return "H"
    return None


def _free_of(jobs: Sequence[Tuple[int, int]], lo: int, hi: int) -> Tuple[int, bool]:
    """Job-free milliseconds of `[lo, hi]` and whether any job touches it (`jobs` sorted)."""
    free = 0
    touched = False
    cursor = lo
    for a, b in jobs:
        if a >= hi:
            break
        if b <= lo:
            continue
        touched = True
        if a > cursor:
            free += a - cursor
        cursor = max(cursor, b)
    if cursor < hi:
        free += hi - cursor
    return free, touched


def idle_scan(
    events: Sequence[Any], intervals: Iterable[Tuple[int, int]]
) -> List[IdleScanEntry]:
    """The idle list from the raw tokens: pure gaps plus the job-free remainders.

    A gap runs from the last assistant record of a turn to the next operator turn (a human
    prompt, an auto-continuation or the answer to an `AskUserQuestion` call; an interrupt is
    skipped). A gap no job touches is pure (its whole length); a gap a job touches keeps its
    job-free remainder when that exceeds one second. Shares with the primary only the job
    intervals handed in (the machine and subagent lists, L04).
    """
    ordered = sorted(
        (e for e in events if not e.sidechain and e.t_ms is not None),
        key=lambda e: (e.t_ms, e.pos),
    )
    asks = {
        tok.b
        for event in ordered
        for tok in event.toks
        if tok.kind == "tool_use" and tok.a == "AskUserQuestion" and tok.b
    }
    gaps: List[Tuple[int, int]] = []
    last: Optional[int] = None
    for event in ordered:
        mark = _scan_mark(event, asks)
        if mark == "A":
            last = event.t_ms
        elif mark == "H":
            if last is not None and event.t_ms > last:
                gaps.append((last, event.t_ms))
            last = None
    jobs = sorted((a, b) for a, b in intervals if b > a)
    out: List[IdleScanEntry] = []
    for a, b in gaps:
        free, touched = _free_of(jobs, a, b)
        if not touched:
            out.append(IdleScanEntry(a, b, (b - a) / 1000.0, "pure"))
        elif free > _REMAINDER_MS:
            out.append(IdleScanEntry(a, b, free / 1000.0, "remainder"))
    return out


def pairing_oracle(events: Sequence[Any]) -> List[OraclePair]:
    """A simpler, independent embodiment of the original's whole run-long pairing rule.

    KEY ARM, for every launch with a non-empty key (an unresolved shell-variable key
    included: equal variable text is an equal key): the search runs over later run-long calls
    in time order and stops at the next launch of the SAME key; the first later `--wait` or
    `--status` on that key whose terminal is known wins, a background wait whose notification
    arrived (the enqueue, else the delivery) or a call whose result carried a `RESULT:`
    verdict or `DONE`. This arm never marks a wait as taken.

    NEAREST ARM, afterwards, for each launch still without a terminal in launch order: the
    first background `--wait` with an arrived notification that started strictly after the
    launch and at most 180 s later, that an EARLIER nearest-arm use has not taken, and whose
    key is absent, or equals the launch's key, or the launch's key is absent or unresolved.
    It marks that wait taken.

    It shares only the extracted call list (the tokens) with the main pairing.
    """
    ordered = sorted(
        (e for e in events if not e.sidechain and e.t_ms is not None),
        key=lambda e: (e.t_ms, e.pos),
    )
    background: set = set()
    enqueue: Dict[str, int] = {}
    delivery: Dict[str, int] = {}
    valued: Dict[str, int] = {}
    calls: List[Dict[str, Any]] = []
    for event in ordered:
        current: Optional[Dict[str, Any]] = None
        for tok in event.toks:
            if tok.kind == "bash_bg" and tok.a:
                background.add(tok.a)
            elif tok.kind == "notif_enqueue" and tok.a:
                enqueue[tok.a] = min(event.t_ms, enqueue.get(tok.a, event.t_ms))
            elif tok.kind == "notif_delivery" and tok.a:
                delivery.setdefault(tok.a, event.t_ms)
            elif tok.kind == "tool_result" and tok.a and tok.b is not None:
                valued.setdefault(tok.a, event.t_ms)
            elif tok.kind == "runlong":
                current = {"kind": tok.a, "key": tok.b, "t": event.t_ms, "tool": None}
                calls.append(current)
            elif tok.kind == "rl_check" and current is not None:
                current["tool"] = tok.b
    calls = [c for c in calls if c["tool"]]
    arrived = {t: enqueue.get(t, delivery.get(t)) for t in set(enqueue) | set(delivery)}
    launches = [i for i, c in enumerate(calls) if c["kind"] == "launch"]
    terminal: Dict[int, Optional[int]] = {}
    via: Dict[int, str] = {}
    for index in launches:
        key = calls[index]["key"]
        next_launch: Optional[int] = None
        for later in calls[index + 1 :]:
            if key and later["kind"] == "launch" and later["key"] == key:
                next_launch = later["t"]
                break
        terminal[index], via[index] = None, "none"
        for later in calls[index + 1 :]:
            if next_launch is not None and later["t"] >= next_launch:
                break
            if not key or later["key"] != key:
                continue
            if later["kind"] not in ("wait", "status"):
                continue
            tool = later["tool"]
            if tool in background and tool in arrived:
                terminal[index], via[index] = arrived[tool], "key"
                break
            if tool in valued:
                terminal[index], via[index] = valued[tool], "key"
                break
    waits = [
        c
        for c in calls
        if c["kind"] == "wait" and c["tool"] in background and c["tool"] in arrived
    ]
    taken: set = set()
    for index in launches:
        if terminal[index] is not None:
            continue
        key = calls[index]["key"]
        loose = not key or key.startswith(ART_UNRESOLVED_PREFIX)
        for wait in waits:
            if not calls[index]["t"] < wait["t"] <= calls[index]["t"] + _NEAREST_MS:
                continue
            if wait["tool"] in taken:
                continue
            if not (wait["key"] is None or wait["key"] == key or loose):
                continue
            terminal[index], via[index] = arrived[wait["tool"]], "nearest"
            taken.add(wait["tool"])
            break
    out: List[OraclePair] = []
    for index in launches:
        key = calls[index]["key"]
        resolved = bool(key) and not key.startswith(ART_UNRESOLVED_PREFIX)
        out.append(
            OraclePair(calls[index]["t"], terminal[index], key, resolved, via[index])
        )
    return out


def brute_force_classes(
    intervals: Dict[str, Sequence[Any]], lo_ms: int, hi_ms: int
) -> Dict[str, float]:
    """Class seconds over `[lo_ms, hi_ms)` by a plain loop over the seconds of the window.

    Each second is judged against the half-open spans that touch it, highest class first
    (MACHINE_WAIT, SUBAGENT, HOOK, OPERATOR_RESPONSE, AWAY); the residual is CONTROLLER. A
    second that a span edge falls inside is cut at the edge and each piece is judged at its
    midpoint, so the answer is exact to the millisecond. It shares only the interval lists
    (`class name -> (start_ms, end_ms, ...)`, clipped here to the window).
    """
    totals = {name: 0 for name in _CLASS_NAMES}
    touching: Dict[int, List[Tuple[str, int, int]]] = {}
    for name in _SWEEP_ORDER:
        for span in intervals.get(name, ()):
            begin, end = max(span[0], lo_ms), min(span[1], hi_ms)
            if end <= begin:
                continue
            for second in range((begin - lo_ms) // 1000, (end - 1 - lo_ms) // 1000 + 1):
                touching.setdefault(second, []).append((name, begin, end))
    for second in range((hi_ms - lo_ms + 999) // 1000):
        t0 = lo_ms + second * 1000
        t1 = min(t0 + 1000, hi_ms)
        active = touching.get(second, [])
        cuts = {t0, t1}
        for _, begin, end in active:
            cuts.update(edge for edge in (begin, end) if t0 < edge < t1)
        points = sorted(cuts)
        for c0, c1 in _zip(points, points[1:]):
            twice_mid = c0 + c1
            owner = "CONTROLLER"
            for name in _SWEEP_ORDER:
                if any(n == name and 2 * a <= twice_mid < 2 * b for n, a, b in active):
                    owner = name
                    break
            totals[owner] += c1 - c0
    return {name: ms / 1000.0 for name, ms in totals.items()}


def dominant_hook_call(transcript: Any, hook_latency_s: int) -> DominantHook:
    """DOMINANT-CHECK: the largest HOOK call and its share of the HOOK union (no verdict).

    Read from `plain_scan` over the whole main thread: the qualifying calls are the
    edit-class calls blocked for strictly more than `hook_latency_s`. The share is the
    largest call over the union of all qualifying calls.
    """
    scan = plain_scan(_main_events(transcript))
    slow = [(a, b) for a, b, _ in scan.calls if b - a > hook_latency_s * 1000]
    if not slow:
        return DominantHook("none", 0, 0, 0, None)
    largest = max(b - a for a, b in slow)
    union = _union_ms(slow)
    return DominantHook("measured", len(slow), largest, union, 100.0 * largest / union)


# ---------------------------------------------------------------------------------------------
# The checks
# ---------------------------------------------------------------------------------------------


def check_sumcheck(cycle_set: Any, class_set: Any) -> CheckRow:
    """SUMCHECK: per cycle the class seconds against the roster window, each class >= 0.

    Args:
        cycle_set: `analyze_cycles` result (the second side: window `end - start`).
        class_set: `classify` result of the same transcript.
    """
    name = "SUMCHECK"
    if cycle_set is None or class_set is None:
        return _skip(name, "no-input")
    windows = _completed(cycle_set)
    cycles = list(class_set.cycles)
    if not windows and not cycles:
        return _skip(name, "no-completed-window")
    if len(windows) != len(cycles):
        return _row(name, False, "windows=%d cycles=%d" % (len(windows), len(cycles)))
    bad = negative = 0
    for window, cycle in _zip(windows, cycles):
        wall = (window.end_t_ms - window.start_t_ms) / 1000.0
        if not _near(sum(cycle.class_s.values()), wall, _TOL_S):
            bad += 1
        negative += sum(1 for v in cycle.class_s.values() if v < -_REL)
    return _row(
        name,
        bad == 0 and negative == 0,
        "windows=%d bad=%d negative=%d" % (len(windows), bad, negative),
    )


def check_classoracle(context: Any, class_set: Any) -> CheckRow:
    """CLASSORACLE: class seconds against `brute_force_classes` over the same interval lists.

    Args:
        context: `classes.build_context` of the same transcript (its completed windows).
        class_set: `classify` result of the same transcript.
    """
    name = "CLASSORACLE"
    if context is None or class_set is None:
        return _skip(name, "no-input")
    windows = list(context.windows)
    cycles = list(class_set.cycles)
    if not windows and not cycles:
        return _skip(name, "no-completed-window")
    if len(windows) != len(cycles):
        return _row(name, False, "windows=%d cycles=%d" % (len(windows), len(cycles)))
    bad = 0
    for window, cycle in _zip(windows, cycles):
        oracle = brute_force_classes(
            window_intervals(context, window), window.start_t_ms, window.end_t_ms
        )
        bad += sum(
            1
            for cls in _CLASS_NAMES
            if not _near(oracle[cls], cycle.class_s.get(cls, math.nan), _TOL_S)
        )
    return _row(name, bad == 0, "windows=%d bad=%d" % (len(windows), bad))


def check_stagecheck(cycle_set: Any, cuts: Sequence[Any]) -> CheckRow:
    """STAGECHECK: each stage's classes against its wall, the wall from the cut's bounds.

    Args:
        cycle_set: `analyze_cycles` result (the window ends).
        cuts: one `classes.stage_cut` per completed window, in window order.
    """
    name = "STAGECHECK"
    if cycle_set is None or cuts is None:
        return _skip(name, "no-input")
    windows = _completed(cycle_set)
    cuts = list(cuts)
    if not windows and not cuts:
        return _skip(name, "no-completed-window")
    if len(windows) != len(cuts):
        return _row(name, False, "windows=%d cuts=%d" % (len(windows), len(cuts)))
    bad = stages = 0
    for window, cut in _zip(windows, cuts):
        starts = [b.start_ms for b in cut.bounds]
        if not starts or len(starts) != len(cut.rows) or starts[0] != window.start_t_ms:
            bad += 1
            continue
        ends = starts[1:] + [window.end_t_ms]
        for bound, begin, end, row in _zip(cut.bounds, starts, ends, cut.rows):
            stages += 1
            wall = (end - begin) / 1000.0
            if (
                row.stage != bound.stage
                or not _near(row.wall_s, wall, _TOL_S)
                or not _near(sum(row.classes_s.values()), wall, _TOL_S)
            ):
                bad += 1
    return _row(
        name, bad == 0, "windows=%d stages=%d bad=%d" % (len(windows), stages, bad)
    )


def check_paircheck(cycle_set: Any, transcript: Any) -> CheckRow:
    """PAIRCHECK: the main run-long pairing against `pairing_oracle`, launch by launch.

    Both sides apply the original's whole rule (a key arm bounded at the next launch of the
    same key, then a nearest-background-wait arm); the oracle is the simpler, separate
    route to it. EVERY launch is judged, unresolved keys included: a launch whose terminal
    differs from the oracle's is a disagreement and fails the check. The detail also counts
    how many of the agreeing launches the oracle paired through the nearest arm.

    Args:
        cycle_set: `analyze_cycles` result (its `pairs`).
        transcript: The parsed transcript (the shared call list).
    """
    name = "PAIRCHECK"
    if cycle_set is None or transcript is None:
        return _skip(name, "no-input")
    main = sorted(cycle_set.pairs, key=lambda p: p.launch_ms)
    oracle = sorted(pairing_oracle(_main_events(transcript)), key=lambda p: p.launch_ms)
    if not main and not oracle:
        return _skip(name, "no-run-long-launch")
    if len(main) != len(oracle):
        return _row(name, False, "launches=%d oracle=%d" % (len(main), len(oracle)))
    agree = disagree = nearest = 0
    for pair, other in _zip(main, oracle):
        if pair.launch_ms == other.launch_ms and pair.terminal_ms == other.terminal_ms:
            agree += 1
            nearest += 1 if other.via == "nearest" else 0
        else:
            disagree += 1
    return _row(
        name,
        disagree == 0,
        "launches=%d agree=%d disagree=%d nearest=%d"
        % (len(main), agree, disagree, nearest),
    )


def check_splitcheck(class_set: Any, split: Any) -> CheckRow:
    """SPLITCHECK: the split's four parts against the sweep's SUBAGENT seconds per cycle.

    Args:
        class_set: `classify` result (the second side: `class_s["SUBAGENT"]`).
        split: `delegates.split` result, or `None` (no delegate split: `skip`).
    """
    name = "SPLITCHECK"
    if class_set is None:
        return _skip(name, "no-input")
    if split is None:
        return _skip(name, "no-split")
    cycles = list(class_set.cycles)
    rows = list(split.cycles)
    if not cycles and not rows:
        return _skip(name, "no-completed-window")
    if len(cycles) != len(rows):
        return _row(name, False, "cycles=%d rows=%d" % (len(cycles), len(rows)))
    bad = 0
    for cycle, row in _zip(cycles, rows):
        if any(key not in row for key in PART_KEYS) or not _near(
            sum(row[key] for key in PART_KEYS), cycle.class_s["SUBAGENT"], _TOL_S
        ):
            bad += 1
    return _row(name, bad == 0, "cycles=%d bad=%d" % (len(cycles), bad))


def check_intervalcheck(context: Any) -> CheckRow:
    """INTERVALCHECK: the delegates module's window spans against the classes intervals.

    The classes side is clipped to the window here, so the check proves the clip: both are
    built from the same dispatch list.

    Args:
        context: `classes.build_context` of the transcript (its completed windows).
    """
    name = "INTERVALCHECK"
    if context is None:
        return _skip(name, "no-input")
    windows = list(context.windows)
    if not windows:
        return _skip(name, "no-completed-window")
    bounds = [(w.start_t_ms, w.end_t_ms) for w in windows]
    rebuilt = window_spans(context.dispatches, bounds)
    bad = spans = 0
    for window, row in _zip(windows, rebuilt):
        lo, hi = window.start_t_ms, window.end_t_ms
        theirs = sorted(
            (max(a, lo), min(b, hi))
            for a, b, _ in window_intervals(context, window)["SUBAGENT"]
        )
        mine = sorted((s.a, s.b) for s in row)
        spans += len(mine)
        if mine != theirs:
            bad += 1
    return _row(
        name, bad == 0, "windows=%d spans=%d bad=%d" % (len(windows), spans, bad)
    )


def check_armcheck(split: Any) -> CheckRow:
    """ARMCHECK: `GATE_RUNLONG + GATE_LONGCALL = SUBAGENT_GATE`; the two token classes
    add to `GATE_LONGCALL`. An identity over every split row (cycles and roles).

    Args:
        split: `delegates.split` result, or `None` (`skip`).
    """
    name = "ARMCHECK"
    if split is None:
        return _skip(name, "no-split")
    rows = list(split.cycles) + list(split.by_role.values())
    if not rows:
        return _skip(name, "no-split-row")
    bad = 0
    for row in rows:
        try:
            gate = row["GATE_RUNLONG"] + row["GATE_LONGCALL"]
            tokens = row["LONGCALL_CHECK"] + row["LONGCALL_OTHER"]
            fine = _near(gate, row["SUBAGENT_GATE"], _TOL_S) and _near(
                tokens, row["GATE_LONGCALL"], _TOL_S
            )
        except KeyError:
            fine = False
        bad += 0 if fine else 1
    return _row(name, bad == 0, "rows=%d bad=%d" % (len(rows), bad))


def check_poolcheck(
    pool: Any, stage_buckets: Optional[Dict[str, float]] = None
) -> List[CheckRow]:
    """POOLCHECK family: the pool's own rows, a class-totals row, and the verdict row last.

    The class totals are the second side: they must add to the pooled wall. With no
    completed cycle (a zero pooled wall) the family is the one `skip` row.

    Args:
        pool: `pool_results` result.
        stage_buckets: Pooled wall by stage bucket (`pool.check_inputs`), or `None`.

    Returns:
        The rows of `pool.poolcheck` (without its verdict row), `POOLCHECK-CLASS-TOTALS`,
        and the final `POOLCHECK` row.
    """
    name = "POOLCHECK"
    if pool is None:
        return [_skip(name, "no-input")]
    if pool.wall_all_s <= 0:
        return [_skip(name, "no-completed-window")]
    rows = [r for r in poolcheck(pool, stage_buckets) if r.name != name]
    total = sum(pool.classes_s.values())
    rows.append(
        _row(
            "POOLCHECK-CLASS-TOTALS",
            _near(total, pool.wall_all_s, _TOL_S),
            "classes_ms=%d wall_ms=%d"
            % (round(total * 1000), round(pool.wall_all_s * 1000)),
        )
    )
    failed = sum(1 for r in rows if r.verdict == "fail")
    rows.append(_row(name, failed == 0, "failed=%d rows=%d" % (failed, len(rows))))
    return rows


def check_halfcheck(pool: Any) -> CheckRow:
    """HALFCHECK: the shares sum to 100 and ALL equals ACTIVE x ACTIVE/ALL.

    An identity over `PoolResult.halves` and the pooled walls; `skip` when a denominator
    is zero (the percentages are `None`).
    """
    name = "HALFCHECK"
    if pool is None:
        return _skip(name, "no-input")
    wall_all, wall_active = pool.wall_all_s, pool.wall_active_s
    if wall_all <= 0 or wall_active <= 0:
        return _skip(name, "no-denominator")
    keys = ("work_pct", "gate_pct", "operator_pct", "unsplit_pct")
    try:
        active, every = pool.halves["active"], pool.halves["all"]
        bad = 0
        if not _near(sum(active[k] for k in keys), 100.0, _TOL_PCT):
            bad += 1
        if not _near(sum(every[k] for k in keys) + every["away_pct"], 100.0, _TOL_PCT):
            bad += 1
        for key in keys:
            if not _near(every[key], active[key] * wall_active / wall_all, _TOL_PCT):
                bad += 1
        if not _near(active["denominator_s"], wall_active, _TOL_S):
            bad += 1
        if not _near(every["denominator_s"], wall_all, _TOL_S):
            bad += 1
    except (KeyError, TypeError):
        return _row(name, False, "halves=unreadable")
    return _row(name, bad == 0, "bad=%d" % bad)


def check_idlecheck(context: Any, transcript: Any) -> CheckRow:
    """IDLECHECK: the idle list's count, values and quartiles against `idle_scan`.

    Shares only the machine and subagent intervals (L04). `skip` when neither list reaches
    `away_min_gaps`.

    Args:
        context: `classes.build_context` result (its `gaps_s`, `fence`, intervals, params).
        transcript: The parsed transcript (the shared call list).
    """
    name = "IDLECHECK"
    if context is None or transcript is None:
        return _skip(name, "no-input")
    floor = max(context.params.away_min_gaps, 2)
    jobs = [(a, b) for a, b, _ in list(context.machine) + list(context.subagent)]
    scan = idle_scan(_main_events(transcript), jobs)
    primary = sorted(context.gaps_s)
    if len(primary) < floor and len(scan) < floor:
        return _skip(
            name, "below-away-min-gaps primary=%d scan=%d" % (len(primary), len(scan))
        )
    other = sorted(e.seconds for e in scan)
    bad = 0 if len(primary) == len(other) else 1
    if bad == 0:
        bad += sum(1 for a, b in _zip(primary, other) if not _near(a, b, _REL * 1e3))
    fence = context.fence
    if len(other) >= floor:
        logs = [math.log10(s) for s in other]
        if fence.q1 is None or fence.q3 is None:
            bad += 1
        else:
            for index, value in ((1, fence.q1), (3, fence.q3)):
                if not _close(value, _quartile(logs, index, fence.method)):
                    bad += 1
    pure = sum(1 for e in scan if e.kind == "pure")
    return _row(
        name,
        bad == 0,
        "gaps=%d scan=%d pure=%d remainder=%d bad=%d"
        % (len(primary), len(scan), pure, len(scan) - pure, bad),
    )


def check_fencecheck(class_set: Any, params: Any) -> CheckRow:
    """FENCECHECK: both fences exceed Q3, and match fences recomputed from the gap list.

    Args:
        class_set: `classify` result (its `idle_gaps_s` and `fence`).
        params: The run's `Params` (`away_min_gaps`, `away_fence_k`).
    """
    name = "FENCECHECK"
    if class_set is None or params is None:
        return _skip(name, "no-input")
    gaps = list(class_set.idle_gaps_s)
    fence = class_set.fence
    if len(gaps) < max(params.away_min_gaps, 2):
        if fence.inner_s is None:
            return _skip(name, "below-away-min-gaps")
        return _row(name, False, "fence=fitted-below-floor gaps=%d" % len(gaps))
    if fence.inner_s is None or fence.outer_s is None or fence.q3 is None:
        return _row(name, False, "fence=missing gaps=%d" % len(gaps))
    if fence.method not in ("inclusive", "exclusive"):
        return _row(name, False, "method=unknown")
    own = _own_fence(gaps, params.away_fence_k, fence.method)
    bad = 0
    for got, want in (
        (fence.q1, own.q1),
        (fence.q3, own.q3),
        (fence.inner_s, own.inner_s),
        (fence.outer_s, own.outer_s),
    ):
        bad += 0 if got is not None and _close(got, want) else 1
    q3_s = 10**own.q3
    for value in (fence.inner_s, fence.outer_s):
        bad += 0 if value >= q3_s * (1 - _REL) else 1
    return _row(name, bad == 0, "gaps=%d bad=%d" % (len(gaps), bad))


def check_fence_robust(class_set: Any, robust: Any, params: Any) -> CheckRow:
    """FENCE-ROBUST: the leave-one-out range against fences recomputed from the gap list.

    Args:
        class_set: `classify` result (its `idle_gaps_s` and `fence`).
        robust: `classes.fence_robust` result (the primary side).
        params: The run's `Params`.
    """
    name = "FENCE-ROBUST"
    if class_set is None or robust is None or params is None:
        return _skip(name, "no-input")
    gaps = list(class_set.idle_gaps_s)
    if len(gaps) < max(params.away_min_gaps, 3):
        return _skip(name, "below-away-min-gaps")
    if robust.min_s is None or robust.max_s is None:
        return _row(name, False, "range=missing gaps=%d" % len(gaps))
    method = class_set.fence.method
    if method not in ("inclusive", "exclusive"):
        return _row(name, False, "method=unknown")
    inner = [
        _own_fence(gaps[:i] + gaps[i + 1 :], params.away_fence_k, method).inner_s
        for i in range(len(gaps))
    ]
    bad = 0
    for got, want in ((robust.min_s, min(inner)), (robust.max_s, max(inner))):
        bad += 0 if _close(got, want) else 1
    bad += 0 if robust.n == len(gaps) else 1
    return _row(name, bad == 0, "gaps=%d bad=%d" % (len(gaps), bad))


def check_hookcheck(
    transcript: Any, context: Any, hook_total: Optional[Tuple[int, float]] = None
) -> CheckRow:
    """HOOKCHECK: the HOOK union, the HOOK class set and `HOOK_REPORTED` against `plain_scan`.

    Checks: the union call count and length equal the scan's (a union is at most the sum
    by construction, so that invariant follows); the union is strictly less than the sum when
    two calls overlap, which only the 1 ms equality slack can hide; each window's HOOK spans equal the scan's
    slow calls that start in it (clipped to its end); `hook_reported_s` equals the scan's
    re-sum per window and over the whole snapshot. `skip` only when the scan finds no edit
    call AND the primary figure is zero calls and zero seconds; a primary that claims calls
    the scan never saw FAILS.

    Args:
        transcript: The parsed transcript (the shared call list).
        context: `classes.build_context` result (windows, edits, events, params).
        hook_total: The primary `(calls, union_s)` from `classes.hook_union_total`, or
            `None` to compute it here.
    """
    name = "HOOKCHECK"
    if transcript is None or context is None:
        return _skip(name, "no-input")
    scan = plain_scan(_main_events(transcript))
    threshold_ms = context.params.hook_latency_s * 1000
    if hook_total is None:
        hook_total = hook_union_total(transcript, threshold_ms)
    if not scan.calls and hook_total[0] == 0 and hook_total[1] == 0:
        return _skip(name, "no-edit-call")
    slow = [(a, b) for a, b, _ in scan.calls if b - a > threshold_ms]
    total = sum(b - a for a, b in slow)
    union = _union_ms(slow)
    overlaps = _overlapping_pairs(slow)
    calls, union_s = hook_total
    union_ms = round(union_s * 1000)
    bad = 0
    bad += 0 if calls == len(slow) else 1
    bad += 0 if abs(union_ms - union) <= 1 else 1
    bad += 0 if overlaps == 0 or union_ms < total else 1
    for window in context.windows:
        lo, hi = window.start_t_ms, window.end_t_ms
        mine = sorted((a, min(b, hi)) for a, b in slow if lo <= a < hi)
        if mine != sorted(window_intervals(context, window)["HOOK"]):
            bad += 1
        resum = sum(n for t, n in scan.reported if lo <= t <= hi) / 1000.0
        if not _near(hook_reported_s(context.events, lo, hi), resum, _TOL_S):
            bad += 1
    whole = sum(n for _, n in scan.reported) / 1000.0
    if not _near(hook_reported_s(context.events, 0, _FAR_MS), whole, _TOL_S):
        bad += 1
    return _row(
        name,
        bad == 0,
        "calls=%d union_ms=%d sum_ms=%d overlaps=%d windows=%d bad=%d"
        % (len(slow), union, total, overlaps, len(context.windows), bad),
    )


def check_mapcheck(class_set: Any) -> CheckRow:
    """MAPCHECK: the ack's agent id against the notification's task id, per dispatch.

    The primary side is `delegates.map_check`; the second side is a recount over the
    dispatches. They must give the same counts, and the two ids must agree wherever both
    exist. `skip` when no dispatch holds both ids.
    """
    name = "MAPCHECK"
    if class_set is None:
        return _skip(name, "no-input")
    dispatches = list(class_set.dispatches)

    def usable(value: Any) -> bool:
        return isinstance(value, str) and ID_RE.fullmatch(value) is not None

    both = [d for d in dispatches if usable(d.agent_id) and usable(d.task_id)]
    agree = sum(1 for d in both if d.agent_id == d.task_id)
    counted = map_check(dispatches)
    if not both and counted.both == 0:
        return _skip(name, "no-dispatch-with-both-ids")
    same = (counted.dispatches, counted.both, counted.agree) == (
        len(dispatches),
        len(both),
        agree,
    )
    return _row(
        name,
        same and agree == len(both),
        "dispatches=%d both=%d agree=%d" % (len(dispatches), len(both), agree),
    )


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _blocks(node: Any) -> Iterable[Dict[str, Any]]:
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _blocks(value)
    elif isinstance(node, (list, tuple)):
        for value in node:
            yield from _blocks(value)


def _non_finite(node: Any) -> int:
    if isinstance(node, float):
        return 0 if math.isfinite(node) else 1
    if isinstance(node, dict):
        return sum(_non_finite(v) for v in node.values())
    if isinstance(node, (list, tuple)):
        return sum(_non_finite(v) for v in node)
    return 0


def check_denomcheck(profile: Any, text: Optional[str] = None) -> CheckRow:
    """DENOMCHECK: no zero denominator is printed as a percentage.

    Walks the profile tree. A dict holding `denominator_s` must carry `None` in every
    `*_pct` key when that denominator is not above zero; the `active_hook_work` variant
    follows the `active` block beside it; a non-finite float anywhere fails. When the
    rendered `text` is given it must hold no `nan` or `inf` word. `skip` when the tree
    holds no denominator block.

    Args:
        profile: The profile dict (or the part of it holding `pool.halves`).
        text: The rendered report text, or `None`.
    """
    name = "DENOMCHECK"
    if profile is None:
        return _skip(name, "no-input")
    blocks = zero = bad = 0
    for node in _blocks(profile):
        if "denominator_s" in node:
            blocks += 1
            denominator = node["denominator_s"]
            if isinstance(denominator, bool) or not isinstance(
                denominator, (int, float)
            ):
                bad += 1
            elif not denominator > 0:
                zero += 1
                bad += sum(
                    1
                    for key, value in node.items()
                    if isinstance(key, str)
                    and key.endswith("_pct")
                    and value is not None
                )
        beside = node.get("active")
        variant = node.get("active_hook_work")
        if isinstance(beside, dict) and isinstance(variant, dict):
            beside_den = beside.get("denominator_s", 1)
            if not _is_number(beside_den):
                bad += 1
            elif not beside_den > 0:
                bad += sum(
                    1
                    for key, value in variant.items()
                    if isinstance(key, str)
                    and key.endswith("_pct")
                    and value is not None
                )
    if blocks == 0:
        return _skip(name, "no-denominator-block")
    bad += _non_finite(profile)
    scanned = 0
    if text is not None:
        scanned = 1
        bad += len(_NON_FINITE_RE.findall(text))
    return _row(
        name,
        bad == 0,
        "blocks=%d zero=%d text=%d bad=%d" % (blocks, zero, scanned, bad),
    )


__all__ = [
    "CHECK_FLOOR",
    "CHECK_NAMES",
    "DominantHook",
    "IdleScanEntry",
    "OraclePair",
    "PlainScan",
    "brute_force_classes",
    "check_armcheck",
    "check_classoracle",
    "check_denomcheck",
    "check_fence_robust",
    "check_fencecheck",
    "check_halfcheck",
    "check_hookcheck",
    "check_idlecheck",
    "check_intervalcheck",
    "check_mapcheck",
    "check_paircheck",
    "check_poolcheck",
    "check_splitcheck",
    "check_stagecheck",
    "check_sumcheck",
    "dominant_hook_call",
    "idle_scan",
    "pairing_oracle",
    "plain_scan",
]

"""The `/feature` cycle roster, the closing rule, lane, run-long pairing and artifacts.

Built on the reader's tokens only (`cycle_profile_records`): the roster of `/feature`
invocations, each invocation's window (D2.5), the lane, the run-long pairs, the artifact
table, and two optional read-only cross-checks (`artifact_end_check`, `commit_time_lower_bound`).

**Integer milliseconds.** Every comparison is on integer `t_ms`, so the three boundary
comparisons of the closing rule are exact: a re-run launched `<=` 900 s after the verdict
extends the chain, a closing marker `<=` 1800 s after it extends the end (and `>` the
verdict itself), a suite of `>=` 600 s is real. The three limits are `Params` fields.

**The search bound.** Invocation k searches `[start, next invocation)`; the last one searches
to one second past the transcript's last timestamp. The bound limits the search only.

**Sidechain records** take no part in anything: not the roster, the closing markers, the
run-long calls, the dispatch and lane counts, nor `last_ms` and the last window's search
bound. Delegate records live in separate files, and D2.5 states the rule only for
invocations; applying it everywhere keeps one list for every bound. The last timestamp used
here is therefore the last main-thread one, which can be earlier than the snapshot's
`last_ts`.

**Choices made where the spec is silent** (the closed vocabularies below, `METHODS`,
`END_MARKERS`, `LANES` and `LANE_SOURCES`, are defined here once and imported elsewhere):

- `minutes_from_last_ts` of an `open` window is `(last main-thread timestamp - the
  invocation's start) / 60 s`.
- `pairing_weak` is true when the window holds a suite-class launch with no terminal.
- `Window.suite_runs` is the number of paired suite-class runs (any suite class, any
  length) whose launch lies in `[start, end]`, or `[start, search bound)` for a window with
  no end: the golden manifest's "suite runs inside the completed window". Unpaired launches
  never count; they stay visible through `pairing_weak` and `first_missing_link`.
- `NEAREST_WAIT_S` (180) is the original's nearest-background-wait window (its outputs print
  `bg-wait-nearest(<=180s)`); the foreground `--status` terminal arm (`fg-status-result`)
  is the original's too.
- Lane inference (`FULL_LANE_DISPATCHES` = 3, the original's `opus_fable >= 3`) counts
  dispatches whose `role_of(...)` role is `opus` or `fable`, up to the launch of the FIRST
  suite-class launch in the window (paired or not), or to the window end when it holds
  none (L13: "dispatch count before the first suite"). The original bounded the count by the
  feature-tip suite instead; the move to the spec's wording is deliberate. A skill-reviewer
  dispatched on an opus model has role `skill-reviewer` and does not count.

**Nothing from the session is kept.** Run-long artifact keys live in memory for pairing
(`RunLongPair.art_key`) and are never rendered; the artifact table carries only launch time,
check class, pairing method and the label sha. Sidechain records take no part in any of it.
No timestamp, id or path of any session is written in this module.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Any, Dict, Iterable, List, NamedTuple, Optional, Sequence, Tuple

from cycle_profile_model import (
    ART_UNRESOLVED_PREFIX,
    CycleSet,
    Event,
    Params,
    RunLongPair,
    Transcript,
    Window,
    art_key_digest,
)
from cycle_profile_records import sorted_events

#: Pairing methods (`RunLongPair.method`), a closed vocabulary.
METHODS: Tuple[str, ...] = (
    "bg-wait-notification",
    "bg-wait-nearest",
    "fg-wait-result",
    "fg-status-result",
    "UNPAIRED",
)
#: Lane names and the source each lane figure names (L13), closed vocabularies.
LANES: Tuple[str, ...] = (
    "plan-only",
    "fast-lane",
    "full-lane",
    "abandoned",
    "partial-no-suites",
)
LANE_SOURCES: Tuple[str, ...] = (
    "invocation-flag",
    "lane-mention",
    "dispatch-count",
    "no-dispatch",
    "no-run-long-no-commit",
    "no-suite-pair",
)
#: `Window.end_marker` values.
END_MARKERS: Tuple[str, ...] = ("verdict", "verdict-rerun", "commit", "propagate")

#: A launch with an unresolved artifact pairs with the nearest unclaimed background wait
#: started at most this long after it.
NEAREST_WAIT_S = 180
#: Opus or fable dispatches before the first suite at or above which an unlabelled lane is full.
FULL_LANE_DISPATCHES = 3
#: Default tolerance of the artifact end cross-check (file mtime against the pair terminal).
ARTIFACT_END_TOLERANCE_S = 60

_EXIT_RE = re.compile(r"^RUN_LONG_EXIT_STATUS=([0-9]{1,3})\s*$", re.M)
_VERDICT_RE = re.compile(
    r"^RESULT: (PARALLEL-PASS|PASS|FAIL|ERROR|INCOMPLETE|INDETERMINATE)\b", re.M
)
_SHA_FULL_RE = re.compile(r"[0-9a-f]{7,40}")
_TAIL_BYTES = 1 << 16


class Invocation(NamedTuple):
    """One kept `/feature` invocation: time, file position, form and `--plan-only`."""

    t_ms: int
    pos: int
    form: str
    plan_only: bool


class ArtifactRow(NamedTuple):
    """One row of the artifact table: no basename, no path, no label text but its sha."""

    launch_ms: int
    terminal_ms: Optional[int]
    check_class: str
    method: str
    label_sha: Optional[str]


class ArtifactEnd(NamedTuple):
    """One artifact file judged against its pair. `status` is agree, disagree or unpaired."""

    launch_ms: int
    check_class: str
    status: str
    exit_status: Optional[str]
    verdict: Optional[str]
    end_minus_mtime_s: Optional[float]


class ArtifactCheck(NamedTuple):
    """`artifact_end_check` result; `status` is `measured` or `unmeasured`."""

    status: str
    rows: Tuple[ArtifactEnd, ...]
    agree: int
    disagree: int
    unpaired: int
    absent: int


class CommitBoundRow(NamedTuple):
    """One labelled pair against its commit; `status` is ok, violated, same-second or unknown."""

    launch_ms: int
    commit_ms: Optional[int]
    status: str


class CommitBound(NamedTuple):
    """`commit_time_lower_bound` result; `status` is `measured` or `unmeasured`."""

    status: str
    rows: Tuple[CommitBoundRow, ...]
    ok: int
    violated: int
    unknown: int
    lower_bound_ms: Optional[int]
    same_second: int = 0


# ---------------------------------------------------------------------------------------------
# Run-long pairing
# ---------------------------------------------------------------------------------------------


class _Call:
    """One run-long Bash call, assembled from the tokens one tool_use block emits."""

    def __init__(self, t_ms: int, pos: int, kind: str, key: Optional[str]) -> None:
        self.t_ms = t_ms
        self.pos = pos
        self.kind = kind
        self.key = key
        self.tool_id: Optional[str] = None
        self.check = "other"
        self.suite = False
        self.sha: Optional[str] = None
        self.background = False


def _calls(events: Sequence[Event], background_ids: set) -> List[_Call]:
    """The run-long calls in time order.

    The reader emits, per block, `runlong` first and then `rl_check` (which carries the
    tool-use id) with the optional `rl_suite` and `rl_label_sha`; this reads them in that
    order and starts a new call at each `runlong`.
    """
    out: List[_Call] = []
    for event in events:
        current: Optional[_Call] = None
        for tok in event.toks:
            if tok.kind == "runlong":
                current = _Call(event.t_ms, event.pos, tok.a or "launch", tok.b)
                out.append(current)
            elif current is None:
                continue
            elif tok.kind == "rl_check":
                current.check = tok.a or "other"
                current.tool_id = tok.b
                current.background = tok.b in background_ids
            elif tok.kind == "rl_suite":
                current.suite = True
            elif tok.kind == "rl_label_sha":
                current.sha = tok.a
    return [call for call in out if call.tool_id is not None]


class _Launch:
    def __init__(self, call: _Call) -> None:
        self.call = call
        self.terminal: Optional[int] = None
        self.method = "UNPAIRED"
        self.key = call.key
        self.check = call.check


def _pair(
    calls: Sequence[_Call],
    arrival: Dict[str, int],
    results: Dict[str, Tuple[int, Optional[str]]],
) -> List[RunLongPair]:
    """Pair each launch with its terminal; a launch with none is `UNPAIRED`.

    Primary: a later `--wait` or `--status` on the same artifact key, before the next launch
    of that key, whose notification arrived (a background wait) or whose result holds a
    `RESULT:` line (a foreground one). A notification keyed to the launch's OWN tool-use id
    is never consulted. Secondary: the nearest unclaimed background wait started within
    `NEAREST_WAIT_S` after a still-unpaired launch whose key is missing or a shell variable
    or equal to the wait's key. `claimed` holds ONLY the waits this secondary arm took: a wait
    paired through the key arm stays available to an earlier unpaired launch of the same key
    (a relaunched suite's first launch shares its relaunch's terminal), while two unpaired
    launches cannot both take one wait. The original does exactly this; a stricter round-1
    version (the key arm also claiming) was measured to lose launches on real data and was
    reverted.
    """
    ordered = sorted(calls, key=lambda call: (call.t_ms, call.pos))
    launches: List[_Launch] = []
    for index, call in enumerate(ordered):
        if call.kind != "launch":
            continue
        launch = _Launch(call)
        launches.append(launch)
        bound: Optional[int] = None
        for later in ordered[index + 1 :]:
            if later.kind == "launch" and call.key and later.key == call.key:
                bound = later.t_ms
                break
        for later in ordered[index + 1 :]:
            if bound is not None and later.t_ms >= bound:
                break
            if later.kind not in ("wait", "status"):
                continue
            if not call.key or later.key != call.key:
                continue
            if later.background and later.tool_id in arrival:
                launch.terminal = arrival[later.tool_id]
                launch.method = "bg-wait-notification"
                break
            result = results.get(later.tool_id or "")
            if result is not None and result[1] is not None:
                launch.terminal = result[0]
                launch.method = "fg-%s-result" % later.kind
                break
    waits = [
        call
        for call in ordered
        if call.kind == "wait" and call.background and call.tool_id in arrival
    ]
    claimed: set = set()
    for launch in launches:
        if launch.terminal is not None:
            continue
        for wait in waits:
            if not (
                launch.call.t_ms < wait.t_ms <= launch.call.t_ms + NEAREST_WAIT_S * 1000
            ):
                continue
            if wait.tool_id in claimed:
                continue
            unresolved = launch.key is None or launch.key.startswith(
                ART_UNRESOLVED_PREFIX
            )
            if not (wait.key is None or wait.key == launch.key or unresolved):
                continue
            launch.terminal = arrival[wait.tool_id or ""]
            launch.method = "bg-wait-nearest"
            if (
                wait.key
                and not wait.key.startswith(ART_UNRESOLVED_PREFIX)
                and unresolved
            ):
                launch.key = wait.key
                if launch.call.suite and launch.check == "suite-unlabelled":
                    launch.check = wait.check
            claimed.add(wait.tool_id)
            break
    return [
        RunLongPair(
            launch.call.t_ms,
            launch.terminal,
            launch.check,
            launch.key,
            launch.call.sha,
            launch.method,
            launch.call.background,
            launch.call.suite,
        )
        for launch in launches
    ]


# ---------------------------------------------------------------------------------------------
# Roster, closing rule, lane
# ---------------------------------------------------------------------------------------------


def _roster(events: Sequence[Event]) -> List[Invocation]:
    """The kept `/feature` invocations, time-ordered.

    Both forms count (a user `/feature` wrapper; an assistant `Skill` call). `events` are
    the time-sorted main-thread events (the caller drops sidechain records). An invocation
    within 60 s of an already kept one (`abs(delta) < 60`, against every kept invocation)
    is dropped.
    """
    kept: List[Invocation] = []
    for event in events:
        plan_only = any(tok.kind == "plan_only" for tok in event.toks)
        for tok in event.toks:
            if tok.kind == "invocation" and tok.a == "feature":
                if any(abs(k.t_ms - event.t_ms) < 60000 for k in kept):
                    continue
                kept.append(Invocation(event.t_ms, event.pos, tok.b or "", plan_only))
    return kept


def _window(
    inv: Invocation,
    bound: int,
    last_ms: int,
    pairs: Sequence[RunLongPair],
    markers: Sequence[Tuple[int, str]],
    events: Sequence[Event],
    arrival: Dict[str, int],
    params: Params,
) -> Window:
    """Apply the closing rule (D2.5 steps 1 to 7) to one invocation's search window."""
    st = inv.t_ms
    min_ms = params.close_min_suite_min * 60000
    rerun_ms = params.close_rerun_min * 60000
    post_ms = params.close_post_min * 60000
    inwin = [p for p in pairs if st <= p.launch_ms < bound]
    suite_class = [p for p in inwin if p.check_class.startswith("suite")]
    paired = [p for p in suite_class if p.terminal_ms is not None]
    real = [p for p in paired if p.terminal_ms - p.launch_ms >= min_ms]
    f1 = d1 = None
    basis: Optional[str] = None
    devs = [p for p in real if p.check_class == "suite-dev-tip"]
    if devs:
        d1, basis = devs[0], "label"
        before = [
            p
            for p in real
            if p.check_class == "suite-feature-tip" and p.launch_ms < d1.launch_ms
        ]
        f1 = before[-1] if before else None
    else:
        for index in range(len(real) - 1):
            a, b = real[index], real[index + 1]
            if 0 <= b.launch_ms - a.terminal_ms <= rerun_ms:
                f1, d1, basis = a, b, "order"
                break
    chain: List[RunLongPair] = []
    end: Optional[int] = None
    end_marker: Optional[str] = None
    if d1 is not None:
        last = d1
        for p in real:
            if (
                p.launch_ms > last.launch_ms
                and 0 <= p.launch_ms - last.terminal_ms <= rerun_ms
                and (p.check_class == "suite-dev-tip" or basis == "order")
            ):
                chain.append(p)
                last = p
        end = last.terminal_ms
        end_marker = "verdict-rerun" if chain else "verdict"
        for t, name in markers:
            if t < st or t >= bound:
                continue
            if end < t <= end + post_ms:
                end, end_marker = t, name
                break
    missing: Optional[str] = None
    if d1 is None:
        if not suite_class:
            missing = "no-suite-launch"
        elif not paired:
            missing = "none-paired"
        elif not real:
            missing = "none-real"
        else:
            missing = "no-dev-or-order-pair"
    # dispatches, commits and lane mentions inside the window
    count_hi = end if end is not None else bound
    dispatches = [
        (e.t_ms, tok.a, tok.b)
        for e in events
        if st <= e.t_ms < bound
        for tok in e.toks
        if tok.kind == "agent_dispatch"
    ]
    counted = [d for d in dispatches if d[0] <= count_hi]
    returned = sum(1 for d in counted if d[1] in arrival)
    commits = sum(1 for t, name in markers if name == "commit" and st <= t <= count_hi)
    mentions = [
        tok.a
        for e in events
        if st <= e.t_ms < min(bound, st + params.lane_window_min * 60000)
        for tok in e.toks
        if tok.kind == "lane_mention"
    ]
    fast, full = mentions.count("fast"), mentions.count("full")
    design_hi = min((p.launch_ms for p in suite_class), default=count_hi)
    heavy = sum(
        1 for t, _, role in dispatches if t <= design_hi and role in ("opus", "fable")
    )
    if inv.plan_only:
        lane, source = "plan-only", "invocation-flag"
    elif mentions and fast != full:
        lane, source = ("fast-lane" if fast > full else "full-lane"), "lane-mention"
    elif f1 is not None or d1 is not None:
        lane = "full-lane" if heavy >= FULL_LANE_DISPATCHES else "fast-lane"
        source = "dispatch-count"
    elif not counted:
        lane, source = "abandoned", "no-dispatch"
    elif commits == 0 and not inwin:
        lane, source = "plan-only", "no-run-long-no-commit"
    else:
        lane, source = "partial-no-suites", "no-suite-pair"
    if f1 is not None and d1 is not None and end is not None and returned >= 1:
        recon = "full"
    elif end is not None or f1 is not None or d1 is not None or counted:
        recon = "partial"
    else:
        recon = "absent"
    return Window(
        st,
        bound,
        "completed" if end is not None else "partial",
        end,
        basis,
        f1,
        d1,
        tuple(chain),
        end_marker,
        recon,
        lane,
        source,
        inv.plan_only,
        sum(
            1
            for p in paired
            if st <= p.launch_ms and (p.launch_ms <= end if end is not None else True)
        ),
        any(p.terminal_ms is None for p in suite_class),
        missing,
        None,
    )


def analyze_cycles(transcript: Transcript, params: Params) -> CycleSet:
    """Roster, windows, run-long pairs and the artifact table of one transcript.

    Windows are `completed` (the closing rule found an end), `open` (the LAST invocation,
    no end; carries `minutes_from_last_ts`, its start's distance from the transcript's last
    timestamp) or `partial` (an earlier invocation with no end). Records without a
    timestamp are excluded and sidechain records take no part.
    """
    events = [e for e in sorted_events(transcript) if not e.sidechain]
    if not events:
        return CycleSet((), (), (), ())
    last_ms = events[-1].t_ms
    background: set = set()
    enqueued: Dict[str, int] = {}
    delivered: Dict[str, int] = {}
    results: Dict[str, Tuple[int, Optional[str]]] = {}
    markers: List[Tuple[int, str]] = []
    for event in events:
        for tok in event.toks:
            if tok.kind == "bash_bg" and tok.a:
                background.add(tok.a)
            elif tok.kind == "notif_enqueue" and tok.a:
                enqueued[tok.a] = min(event.t_ms, enqueued.get(tok.a, event.t_ms))
            elif tok.kind == "notif_delivery" and tok.a:
                delivered.setdefault(tok.a, event.t_ms)
            elif tok.kind == "tool_result" and tok.a:
                results[tok.a] = (event.t_ms, tok.b)
            elif tok.kind == "invocation" and tok.a in ("commit", "propagate"):
                markers.append((event.t_ms, tok.a))
    arrival = dict(delivered)
    arrival.update(enqueued)
    pairs = _pair(_calls(events, background), arrival, results)
    roster = _roster(events)
    windows: List[Window] = []
    for index, inv in enumerate(roster):
        bound = roster[index + 1].t_ms if index + 1 < len(roster) else last_ms + 1000
        windows.append(
            _window(inv, bound, last_ms, pairs, markers, events, arrival, params)
        )
    if windows and windows[-1].end_t_ms is None:
        tail = windows[-1]
        windows[-1] = tail._replace(
            status="open",
            reconstructable="excluded",
            minutes_from_last_ts=(last_ms - tail.start_t_ms) / 60000.0,
        )
    artifacts = tuple(
        ArtifactRow(p.launch_ms, p.terminal_ms, p.check_class, p.method, p.label_sha)
        for p in pairs
    )
    return CycleSet(tuple(roster), tuple(windows), tuple(pairs), artifacts)


# ---------------------------------------------------------------------------------------------
# Optional read-only cross-checks
# ---------------------------------------------------------------------------------------------


def _read_artifact(path: Path) -> Tuple[int, Optional[str], Optional[str]]:
    """`(mtime_ms, exit status digits or None, RESULT verdict or None)` of one artifact."""
    mtime = int(round(os.stat(path).st_mtime * 1000))
    with open(path, "rb") as handle:
        handle.seek(0, os.SEEK_END)
        handle.seek(max(0, handle.tell() - _TAIL_BYTES))
        text = handle.read().decode("utf-8", "replace")
    exits = _EXIT_RE.findall(text)
    verdicts = _VERDICT_RE.findall(text)
    verdict = verdicts[-1] if verdicts else None
    if verdict == "PARALLEL-PASS":  # the closed verdict set keeps one PASS
        verdict = "PASS"
    return mtime, (exits[-1] if exits else None), verdict


def artifact_end_check(
    cycle_set: CycleSet,
    dirs: Iterable[Any],
    tolerance_s: float = ARTIFACT_END_TOLERANCE_S,
) -> ArtifactCheck:
    """Compare run-long artifact files with their pairs' terminals (`--artifacts`).

    For every pair with an artifact key, look for a file of that name in `dirs` (by digest) and take
    the key's latest launch at or before the file's mtime. The pair agrees when its
    terminal is within `tolerance_s` of the mtime, disagrees otherwise, and is `unpaired`
    when the launch has no terminal. A key with no file in any directory counts as `absent`.
    With no directory the result is `unmeasured` (never agree). Names and paths are not kept.
    """
    roots = [Path(d) for d in dirs]
    if not roots:
        return ArtifactCheck("unmeasured", (), 0, 0, 0, 0)
    # pairs carry digests of the artifact keys, so index each file by the digest of its name
    # (the first directory that holds a name wins)
    files: Dict[str, Path] = {}
    for root in roots:
        try:
            names = sorted(os.listdir(root))
        except OSError:
            continue
        for name in names:
            if (root / name).is_file():
                files.setdefault(art_key_digest(name), root / name)
    by_key: Dict[str, List[RunLongPair]] = {}
    for pair in cycle_set.pairs:
        if pair.art_key and not pair.art_key.startswith(ART_UNRESOLVED_PREFIX):
            by_key.setdefault(pair.art_key, []).append(pair)
    rows: List[ArtifactEnd] = []
    absent = 0
    for key, candidates in by_key.items():
        found = files.get(key)
        if found is None:
            absent += len(candidates)
            continue
        try:
            mtime, exit_status, verdict = _read_artifact(found)
        except OSError:
            absent += len(candidates)
            continue
        launched = [p for p in candidates if p.launch_ms <= mtime]
        if not launched:
            absent += 1
            continue
        pair = max(launched, key=lambda p: p.launch_ms)
        if pair.terminal_ms is None:
            status, delta = "unpaired", None
        else:
            delta = (pair.terminal_ms - mtime) / 1000.0
            status = "agree" if abs(delta) <= tolerance_s else "disagree"
        rows.append(
            ArtifactEnd(
                pair.launch_ms, pair.check_class, status, exit_status, verdict, delta
            )
        )
    rows.sort(key=lambda row: row.launch_ms)
    count = {
        s: sum(1 for row in rows if row.status == s)
        for s in ("agree", "disagree", "unpaired")
    }
    return ArtifactCheck(
        "measured",
        tuple(rows),
        count["agree"],
        count["disagree"],
        count["unpaired"],
        absent,
    )


def _commit_ms(repo: Any, sha: str) -> Optional[int]:
    """The commit time of `sha` in milliseconds via read-only `git log`, else `None`."""
    if not _SHA_FULL_RE.fullmatch(sha):
        return None
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    cmd = [
        "git",
        "--no-optional-locks",
        "-C",
        str(repo),
        "log",
        "-1",
        "--no-show-signature",
        "--format=%ct",
        sha,
        "--",
    ]
    try:
        out = subprocess.run(
            cmd, env=env, capture_output=True, text=True, timeout=30, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return None
    text = out.stdout.strip()
    if out.returncode != 0 or not text.isdigit():
        return None
    return int(text) * 1000


def commit_time_lower_bound(cycle_set: CycleSet, repo: Any) -> CommitBound:
    """Check each labelled run-long launch against the commit time of its label sha.

    A launch cannot precede the commit it labels. `%ct` has one-second resolution, so the
    commit happened somewhere in `[ct, ct + 1 s)`: the launch is `ok` only when it is at
    or after `ct + 1 s` (provably later), `violated` only when before `ct` (provably
    earlier), `same-second` when it falls inside that second (neither can be shown), and
    `unknown` when the sha is not in the repository.

    `lower_bound_ms` is the latest known commit time (truncated to its second) among the
    labelled pairs. It bounds the launch of the LATEST-labelled suite from below; it is not
    a lower bound for the window start or for every launch. With no repository the result
    is `unmeasured`.
    """
    if repo is None:
        return CommitBound("unmeasured", (), 0, 0, 0, None)
    cache: Dict[str, Optional[int]] = {}
    rows: List[CommitBoundRow] = []
    for pair in cycle_set.pairs:
        if pair.label_sha is None:
            continue
        if pair.label_sha not in cache:
            cache[pair.label_sha] = _commit_ms(repo, pair.label_sha)
        commit = cache[pair.label_sha]
        if commit is None:
            status = "unknown"
        else:
            if pair.launch_ms >= commit + 1000:
                status = "ok"
            elif pair.launch_ms < commit:
                status = "violated"
            else:
                status = "same-second"
        rows.append(CommitBoundRow(pair.launch_ms, commit, status))
    known = [row.commit_ms for row in rows if row.commit_ms is not None]
    return CommitBound(
        "measured",
        tuple(rows),
        sum(1 for row in rows if row.status == "ok"),
        sum(1 for row in rows if row.status == "violated"),
        sum(1 for row in rows if row.status == "unknown"),
        max(known) if known else None,
        sum(1 for row in rows if row.status == "same-second"),
    )


__all__ = [
    "ARTIFACT_END_TOLERANCE_S",
    "END_MARKERS",
    "LANES",
    "LANE_SOURCES",
    "METHODS",
    "ArtifactCheck",
    "ArtifactEnd",
    "ArtifactRow",
    "CommitBound",
    "CommitBoundRow",
    "Invocation",
    "analyze_cycles",
    "artifact_end_check",
    "commit_time_lower_bound",
]

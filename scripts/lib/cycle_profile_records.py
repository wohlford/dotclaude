"""One tolerant streaming pass over a session transcript, producing neutral event tokens.

The reader opens the transcript once, reads exactly the pinned bytes (the file size at open,
or a verified prefix), cuts them back to the last newline, and hashes those same bytes as it
parses them. It makes no copy of the transcript anywhere. Each record becomes one `Event`
whose `Tok` values are ids and closed-vocabulary strings only (see `cycle_profile_model`):
message, command and description text is scanned in memory for the fixed vocabularies and
only the derived token survives.

The same pass counts the record shapes the measurements depend on (D2.3), overall and per
`version`, the timestamped share, the unparseable lines and the timestamp inversions.
A shape count is the number of RECORDS showing the shape (tool-use kinds count blocks). An
absent shape reads 0 and is flagged `GAP` (see `gap_flags`) only where its producer is
present and the shape is not, so an absent producer is never reported as a zero measurement.

Records without a parseable timestamp stay in `Transcript.events` (the idle-gap logic needs
them in file order) and are excluded by `sorted_events`, which counts them.

Nothing here decides which invocation counts as a cycle start: that belongs to `cycles`.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import os
import re
from collections import Counter
from typing import Any, Dict, FrozenSet, Iterable, List, Optional, Tuple

from cycle_profile_model import (
    ART_UNRESOLVED_PREFIX,
    CHECK_TOKENS,
    EDIT_NAMES,
    ID_RE,
    INVOCATION_NAMES,
    RECORD_TYPES,
    SHAPES,
    STAGE_KEYWORDS,
    TOOL_KINDS,
    VERSION_RE,
    Event,
    Snapshot,
    Tok,
    Transcript,
    VersionRow,
    art_key_digest,
    role_of,
)

CHUNK = 1 << 22
MAX_RESULT_DEPTH = 8


class PinError(Exception):
    """The prefix pin does not hold (wrong hash, or the file is shorter than the prefix)."""


class ReadError(Exception):
    """The transcript could not be read. The message is a fixed string, never content."""


# ---------------------------------------------------------------------------------------------
# Timestamps
# ---------------------------------------------------------------------------------------------

_TS_RE = re.compile(
    r"([0-9]{4})-([0-9]{2})-([0-9]{2})T([0-9]{2}):([0-9]{2}):([0-9]{2})"
    r"(?:\.([0-9]+))?Z"
)
_EPOCH = datetime.datetime(1970, 1, 1)


def parse_ts(value: Any) -> Optional[int]:
    """Parse a transcript timestamp to integer milliseconds since the epoch (UTC).

    The string must end in `Z`. Fractional digits beyond three are truncated toward zero
    and fewer than three are zero-padded. Anything that does not parse is `None`.
    """
    if not isinstance(value, str):
        return None
    m = _TS_RE.fullmatch(value)
    if m is None:
        return None
    y, mo, d, h, mi, s = (int(g) for g in m.groups()[:6])
    try:
        stamp = datetime.datetime(y, mo, d, h, mi, s)
    except ValueError:
        return None
    delta = stamp - _EPOCH
    frac = m.group(7)
    ms = int((frac + "000")[:3]) if frac else 0
    return (delta.days * 86400 + delta.seconds) * 1000 + ms


def format_ts(t_ms: int) -> str:
    """Render integer milliseconds as a UTC string (`...Z`, with `.mmm` only when non-zero)."""
    secs, ms = divmod(t_ms, 1000)
    stamp = _EPOCH + datetime.timedelta(seconds=secs)
    out = stamp.strftime("%Y-%m-%dT%H:%M:%S")
    return out + (".%03d" % ms if ms else "") + "Z"


# ---------------------------------------------------------------------------------------------
# Token extraction
# ---------------------------------------------------------------------------------------------

_NOTIF_RE = {
    "task_id": re.compile(r"<task-id>([^<]*)"),
    "tool_use_id": re.compile(r"<tool-use-id>([^<]*)"),
    "duration_ms": re.compile(r"<duration_ms>([0-9]{1,15})"),
}
_RESULT_RE = re.compile(r"RESULT: (PASS|FAIL|ERROR|INCOMPLETE|INDETERMINATE)")
_DONE_RE = re.compile(r"\bDONE\b")
_RUNNING_RE = re.compile(r"\bRUNNING\b")
_LANE_RE = re.compile(r"\b(fast|full)[ -]lane\b", re.I)
_WRAPPER_RE = re.compile(r"<command-name>/?([^<]*)</command-name>")
_WORD_RE = re.compile(r"[a-z]+")
_RL_OUT = re.compile(r"--out\s+(\S+)")
_RL_LABEL = re.compile(r"--label\s+(\"[^\"]*\"|'[^']*'|\S+)")
_RL_ARG = re.compile(r"run-long\.sh\s+--(?:wait|status)\s+(\S+)")
_RL_LAUNCH = re.compile(r"run-long\.sh\s+--out\s")
_SHA_RE = re.compile(r"\b([0-9a-f]{7,10})\b")
_MARKER_SHAPE = {
    "feature": "feature_invocation",
    "commit": "commit_marker",
    "propagate": "propagate_marker",
}


def _id(value: Any) -> Optional[str]:
    """Return `value` when it is a string that fullmatches `ID_RE`, else `None`."""
    if isinstance(value, str) and ID_RE.fullmatch(value):
        return value
    return None


def _int(value: Any) -> Optional[int]:
    """Return a non-negative int for an int or float `value` (never a bool), else `None`."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if value != value or value < 0 or value == float("inf"):
        return None
    return int(value)


def _dict(value: Any) -> dict:
    """`value` when it is a dict, else an empty dict (a record member of an odd shape)."""
    return value if isinstance(value, dict) else {}


def _blocks(content: Any) -> List[dict]:
    if isinstance(content, list):
        return [b for b in content if isinstance(b, dict)]
    return []


def _text_blocks(content: Any) -> str:
    """The user text of a record: a string `content`, or the joined `text` blocks."""
    if isinstance(content, str):
        return content
    return "".join(
        b.get("text", "")
        for b in _blocks(content)
        if b.get("type") == "text" and isinstance(b.get("text"), str)
    )


def _result_text(content: Any, depth: int = 0) -> str:
    """The text inside a tool_result's own content (a string, or nested text blocks).

    Nesting is read to a fixed depth; anything deeper is ignored, never a crash.
    """
    if isinstance(content, str):
        return content
    if depth > MAX_RESULT_DEPTH:
        return ""
    out = []
    for b in _blocks(content):
        if b.get("type") == "text" and isinstance(b.get("text"), str):
            out.append(b["text"])
        elif b.get("type") == "tool_result":
            out.append(_result_text(b.get("content"), depth + 1))
    return "".join(out)


def _result_value(content: Any) -> Optional[str]:
    """The closed `tool_result` value: the last `RESULT:` verdict, else `DONE`, else `None`.

    `DONE` needs the whole word DONE and not the whole word RUNNING, as in the original.
    """
    text = _result_text(content)
    verdicts = _RESULT_RE.findall(text)
    if verdicts:
        return verdicts[-1]
    if _DONE_RE.search(text) and not _RUNNING_RE.search(text):
        return "DONE"
    return None


def long_class(command: str) -> str:
    """Class a Bash command `check` or `other` by a fixed token list, scanned in memory."""
    if command and any(t in command for t in CHECK_TOKENS):
        return "check"
    return "other"


def art_key(path: str) -> Optional[str]:
    """The basename of a run-long artifact argument, used only in memory for pairing."""
    path = path.strip("\"'")
    parts = [p for p in re.split(r"/", path) if p]
    if not parts:
        return None
    base = re.sub(r"[\"';)&|<>]+$", "", parts[-1])
    if not base or base.startswith("-"):
        return None
    return base


def rl_check(command: str, label: str) -> str:
    """Class a run-long call into one of `CHECK_CLASSES` from its command and label text."""
    lab = label.lower()
    if "audit.sh" in command and "--tests" in command:
        if "dev" in lab:
            return "suite-dev-tip"
        if "feature" in lab or "tip" in lab:
            return "suite-feature-tip"
        return "suite-unlabelled"
    if "kill-sets" in command or "kill-sets" in lab:
        return "kill-sets/campaign"
    if "publish" in command or "brick" in command or "brick" in lab or "drive" in lab:
        return "per-brick-sweep"
    if "audit.sh" in command:
        return "plain-audit"
    return "other"


class _Record:
    """The tokens and shape facts derived from one parsed record."""

    def __init__(self) -> None:
        self.toks: List[Tok] = []
        self.shapes: set = set()
        self.kinds: Counter = Counter()
        self.roles_other = 0
        self.dispatch_names: Dict[str, str] = {}


def _notification(text: str, toks: List[Tok], shapes: set, kind: str) -> None:
    found = {}
    for key, rx in _NOTIF_RE.items():
        m = rx.search(text)
        found[key] = m.group(1) if m else None
    tool_id = _id(found["tool_use_id"])
    task_id = _id(found["task_id"])
    if tool_id is None:
        return
    duration = found["duration_ms"]
    toks.append(Tok(kind, tool_id, task_id, int(duration) if duration else None))
    if task_id is not None:
        shapes.add("task_enqueue" if kind == "notif_enqueue" else "task_delivery")


def _ack_agent(tur: Any, dispatch: Optional[str]) -> Optional[str]:
    """The agent id an acknowledgement names, as an id-shaped string or `None`.

    A `SendMessage` ack names its agent in `resumedAgentId`, else `revivedAgentId`, else
    `agentId`; any other ack (an `Agent` dispatch, or one whose dispatch lies outside the
    pinned prefix) names it in `agentId`, else `resumedAgentId`, else `revivedAgentId`.
    The first key that holds an id-shaped value wins.
    """
    if not isinstance(tur, dict):
        return None
    keys = ("resumedAgentId", "revivedAgentId", "agentId")
    if dispatch != "SendMessage":
        keys = ("agentId", "resumedAgentId", "revivedAgentId")
    for key in keys:
        found = _id(tur.get(key))
        if found is not None:
            return found
    return None


def _user_record(rec: dict, out: _Record, dispatch_names: Dict[str, str]) -> None:
    content = _dict(rec.get("message")).get("content")
    text = _text_blocks(content)
    head = text.lstrip()[:30]
    results = [b for b in _blocks(content) if b.get("type") == "tool_result"]
    meta = bool(rec.get("isMeta"))
    side = bool(rec.get("isSidechain"))
    origin = rec.get("turnOrigin")

    m = _WRAPPER_RE.search(text)
    if m is not None and m.group(1).strip() in INVOCATION_NAMES:
        name = m.group(1).strip()
        out.toks.append(Tok("invocation", name, "wrapper"))
        out.shapes.add(_MARKER_SHAPE[name])
        if name == "feature" and "--plan-only" in text:
            out.toks.append(Tok("plan_only"))

    if head.startswith("<task-notification"):
        _notification(text, out.toks, out.shapes, "notif_delivery")

    # An auto-continuation is read as the operator resuming the session, so it is a token
    # for every user record with that origin: whatever its isMeta flag, text head, compact
    # summary flag or tool_result blocks (the original took them all; consumers filter on
    # Event.meta and Event.sidechain). It is emitted here, once, and nowhere else.
    if origin == "auto_continuation":
        out.toks.append(Tok("auto_continuation"))

    if results:
        tur = rec.get("toolUseResult")
        for b in results:
            tool_id = _id(b.get("tool_use_id"))
            out.shapes.add("tool_result")
            if tool_id is None:
                continue
            out.toks.append(
                Tok("tool_result", tool_id, _result_value(b.get("content")))
            )
            agent = _ack_agent(tur, dispatch_names.get(tool_id))
            if len(results) == 1 and agent is not None:
                out.toks.append(Tok("agent_ack", tool_id, agent))
                out.shapes.add("agent_ack")
    elif (
        not meta
        and not side
        and not head.startswith("<")
        and not rec.get("isCompactSummary")
    ):
        if origin in (None, "human"):
            out.toks.append(Tok("human_prompt"))
            out.shapes.add("user_prompt")
            if text.startswith("[Request interrupted"):
                out.toks.append(Tok("interrupt"))
    elif (
        not meta
        and not side
        and head.startswith(("<command-name", "<command-message"))
        and origin in (None, "human")
    ):
        out.toks.append(Tok("human_prompt"))
        out.shapes.add("user_prompt")


def _key_token(raw: Optional[str]) -> Optional[str]:
    """The `runlong` key token for a raw artifact key: a bounded digest, or `None`.

    A key that is a shell variable keeps its identity under `ART_UNRESOLVED_PREFIX`.
    """
    if raw is None:
        return None
    if raw.startswith("$"):
        return ART_UNRESOLVED_PREFIX + art_key_digest(raw)
    return art_key_digest(raw)


def _bash(inp: dict, tool_id: str, out: _Record) -> None:
    command = inp.get("command")
    command = command if isinstance(command, str) else ""
    background = bool(inp.get("run_in_background"))
    if background:
        out.toks.append(Tok("bash_bg", tool_id))
    out.toks.append(Tok("bash_class", tool_id, long_class(command)))
    if "run-long.sh" not in command:
        return
    if _RL_LAUNCH.search(command):
        kind = "launch"
    elif "--wait" in command:
        kind = "wait"
    elif "--status" in command:
        kind = "status"
    else:
        kind = "launch"
    label = ""
    key = None
    if kind == "launch":
        m = _RL_OUT.search(command)
        if m:
            key = art_key(m.group(1))
        ml = _RL_LABEL.search(command)
        if ml:
            label = ml.group(1).strip("\"'")
    else:
        m = _RL_ARG.search(command)
        if m:
            key = art_key(m.group(1))
    out.toks.append(Tok("runlong", kind, _key_token(key)))
    if kind == "launch" and "--wait" in command:
        out.toks.append(Tok("rl_launch_with_wait", tool_id))
    if kind == "launch":
        check = rl_check(command, label + " " + (key or ""))
    else:
        check = rl_check("audit.sh --tests", key or "")
    out.toks.append(Tok("rl_check", check, tool_id))
    if "audit.sh" in command and "--tests" in command:
        out.toks.append(Tok("rl_suite", tool_id))
    sha = _SHA_RE.search(label)
    if sha:
        out.toks.append(Tok("rl_label_sha", sha.group(1), tool_id))


def _stage_words(text: Any, tool_id: str, out: _Record) -> None:
    if not isinstance(text, str):
        return
    for word in sorted(set(_WORD_RE.findall(text.lower())) & STAGE_KEYWORDS):
        out.toks.append(Tok("stage_kw", tool_id, word))


def _tool_use(block: dict, out: _Record) -> None:
    name = block.get("name")
    raw_input = block.get("input")
    inp = raw_input if isinstance(raw_input, dict) else {}
    tool_id = _id(block.get("id"))
    if isinstance(name, str) and name in TOOL_KINDS:
        out.kinds[name] += 1
    if name == "Bash":
        out.shapes.add("bash_tool_use")
    if name in EDIT_NAMES:
        out.shapes.add("edit_tool_use")
    if name in ("Agent", "SendMessage"):
        out.shapes.add("agent_dispatch")
    if name == "Skill" and inp.get("skill") in INVOCATION_NAMES:
        skill = inp["skill"]
        out.shapes.add(_MARKER_SHAPE[skill])
    if tool_id is None:
        return
    if name in ("Agent", "SendMessage"):
        out.dispatch_names[tool_id] = name
    tool_name = name if isinstance(name, str) and ID_RE.fullmatch(name) else "other"
    out.toks.append(Tok("tool_use", tool_name, tool_id))
    if name == "Skill" and inp.get("skill") in INVOCATION_NAMES:
        out.toks.append(Tok("invocation", inp["skill"], "skill"))
        if inp["skill"] == "feature" and "--plan-only" in json.dumps(inp):
            out.toks.append(Tok("plan_only"))
    elif name == "Agent":
        role = role_of(inp.get("subagent_type"), inp.get("model"))
        if role == "other":
            out.roles_other += 1
        out.toks.append(Tok("agent_dispatch", tool_id, role))
        _stage_words(inp.get("description"), tool_id, out)
    elif name == "SendMessage":
        out.toks.append(Tok("sendmsg_dispatch", tool_id))
        target = _id(inp.get("to"))
        if target is not None:
            out.toks.append(Tok("sendmsg_to", tool_id, target))
        _stage_words(inp.get("summary"), tool_id, out)
    elif name == "Bash":
        _bash(inp, tool_id, out)


def _assistant_record(rec: dict, out: _Record) -> None:
    content = _dict(rec.get("message")).get("content")
    for block in _blocks(content):
        if block.get("type") == "text" and isinstance(block.get("text"), str):
            for lane in _LANE_RE.findall(block["text"]):
                out.toks.append(Tok("lane_mention", lane.lower()))
        elif block.get("type") == "tool_use":
            _tool_use(block, out)


def _other_record(rec: dict, rtype: str, out: _Record) -> None:
    if rtype == "queue-operation":
        body = rec.get("content")
        if (
            rec.get("operation") == "enqueue"
            and isinstance(body, str)
            and body.lstrip().startswith("<task-notification")
        ):
            _notification(body, out.toks, out.shapes, "notif_enqueue")
    elif rtype == "system":
        if rec.get("subtype") == "turn_duration":
            out.toks.append(Tok("turn_duration", n=_int(rec.get("durationMs"))))
            out.shapes.add("turn_duration")
    elif rtype == "attachment":
        att = rec.get("attachment")
        if isinstance(att, dict) and att.get("type") == "hook_success":
            out.toks.append(
                Tok(
                    "hook_success",
                    _id(att.get("toolUseID")),
                    n=_int(att.get("durationMs")),
                )
            )
            out.shapes.add("hook_success")


def gap_flags(shapes: Dict[str, int], kinds: Dict[str, int]) -> FrozenSet[str]:
    """Shapes flagged `GAP`: the producer is present and the shape is not (D2.3).

    `Agent` tool_use with no `agent_ack`; `Agent` with neither `task_enqueue` nor
    `task_delivery`; `Edit` with no `tool_result`. An absent producer flags nothing.
    """
    flags = set()
    if kinds.get("Agent", 0) > 0 and shapes.get("agent_ack", 0) == 0:
        flags.add("agent_ack")
    if (
        kinds.get("Agent", 0) > 0
        and shapes.get("task_enqueue", 0) == 0
        and shapes.get("task_delivery", 0) == 0
    ):
        flags.update(("task_enqueue", "task_delivery"))
    if kinds.get("Edit", 0) > 0 and shapes.get("tool_result", 0) == 0:
        flags.add("tool_result")
    return frozenset(flags)


def _version_key(version: str) -> Tuple[int, Tuple[int, ...]]:
    if version == "unknown":
        return (1, ())
    return (0, tuple(int(p) for p in version.split(".")))


# ---------------------------------------------------------------------------------------------
# The streaming pass
# ---------------------------------------------------------------------------------------------


class _Reader:
    """Accumulates one pass: feed byte chunks, then `finish`. Torn tails are never hashed."""

    def __init__(self) -> None:
        self.tail = b""
        self.digest = hashlib.sha256()
        self.nbytes = 0
        self.records = 0
        self.unparseable = 0
        self.timestamped = 0
        self.inversions = 0
        self.roles_other = 0
        self.last_ms: Optional[int] = None
        self.dispatch_names: Dict[str, str] = {}
        self.prev_ms: Optional[int] = None
        self.events: List[Event] = []
        self.shapes: Counter = Counter()
        self.kinds: Counter = Counter()
        self.by_version: Dict[str, dict] = {}

    def feed(self, chunk: bytes) -> None:
        data = self.tail + chunk
        cut = data.rfind(b"\n") + 1
        body, self.tail = data[:cut], data[cut:]
        if not body:
            return
        self.digest.update(body)
        self.nbytes += len(body)
        for line in body.split(b"\n"):
            if line.strip():
                self._line(line)

    def _line(self, line: bytes) -> None:
        pos = self.records
        self.records += 1
        try:
            rec = json.loads(line)
        except (ValueError, RecursionError):
            self.unparseable += 1
            return
        if not isinstance(rec, dict):
            self.unparseable += 1
            return
        rtype = rec.get("type")
        rtype = rtype if rtype in RECORD_TYPES else "other"
        version = rec.get("version")
        version = (
            version
            if isinstance(version, str) and VERSION_RE.fullmatch(version)
            else "unknown"
        )
        t_ms = parse_ts(rec.get("timestamp"))
        out = _Record()
        if rtype == "user":
            _user_record(rec, out, self.dispatch_names)
        elif rtype == "assistant":
            _assistant_record(rec, out)
        else:
            _other_record(rec, rtype, out)
        if t_ms is not None:
            out.shapes.add("ts")
            self.timestamped += 1
            if self.prev_ms is not None and t_ms < self.prev_ms:
                self.inversions += 1
            self.prev_ms = t_ms
            if self.last_ms is None or t_ms > self.last_ms:
                self.last_ms = t_ms
        side = bool(rec.get("isSidechain"))
        if side:
            out.shapes.add("sidechain_flag")
        self.dispatch_names.update(out.dispatch_names)
        self.roles_other += out.roles_other
        self.shapes.update(out.shapes)
        self.kinds.update(out.kinds)
        row = self.by_version.setdefault(
            version,
            {"records": 0, "timestamped": 0, "shapes": Counter(), "kinds": Counter()},
        )
        row["records"] += 1
        row["timestamped"] += 1 if t_ms is not None else 0
        row["shapes"].update(out.shapes)
        row["kinds"].update(out.kinds)
        self.events.append(
            Event(
                pos,
                t_ms,
                rtype,
                side,
                bool(rec.get("isMeta")),
                version,
                tuple(out.toks),
            )
        )

    def finish(self, pinned: bool) -> Transcript:
        shapes = {name: self.shapes.get(name, 0) for name in SHAPES}
        kinds = {name: self.kinds.get(name, 0) for name in TOOL_KINDS}
        rows = []
        for version in sorted(self.by_version, key=_version_key):
            row = self.by_version[version]
            v_shapes = {name: row["shapes"].get(name, 0) for name in SHAPES}
            v_kinds = {name: row["kinds"].get(name, 0) for name in TOOL_KINDS}
            rows.append(
                VersionRow(
                    version,
                    row["records"],
                    row["timestamped"],
                    v_shapes,
                    v_kinds,
                    gap_flags(v_shapes, v_kinds),
                )
            )
        snapshot = Snapshot(
            self.digest.hexdigest(),
            self.nbytes,
            self.records,
            format_ts(self.last_ms) if self.last_ms is not None else None,
            pinned,
        )
        return Transcript(
            self.events,
            snapshot,
            self.unparseable,
            sum(1 for e in self.events if e.t_ms is None),
            self.inversions,
            shapes,
            kinds,
            tuple(rows),
            gap_flags(shapes, kinds),
            self.roles_other,
        )


def parse_bytes(data: bytes) -> Transcript:
    """Parse transcript bytes held in memory (pure; a torn last line is cut, not counted)."""
    reader = _Reader()
    reader.feed(data)
    return reader.finish(False)


def read_transcript(
    path: Any,
    *,
    prefix_bytes: Optional[int] = None,
    prefix_sha256: Optional[str] = None,
) -> Transcript:
    """Read one transcript in a single open: exactly the pinned bytes, no copy.

    Without a prefix the pin is the file size at open. With `prefix_bytes` and
    `prefix_sha256` (both or neither) exactly that many leading bytes are read and their
    hash must match. Either way the parsed bytes end at the last newline, and the snapshot
    records the hash and size of exactly the bytes parsed.

    Raises:
        ValueError: Only one of the two prefix arguments was given, or it is malformed.
        PinError: The prefix is longer than the file, or its hash does not match.
        ReadError: The file could not be opened or read.
    """
    if (prefix_bytes is None) != (prefix_sha256 is None):
        raise ValueError("prefix_bytes and prefix_sha256 go together")
    pinned = prefix_bytes is not None
    if pinned and (
        isinstance(prefix_bytes, bool)
        or not isinstance(prefix_bytes, int)
        or prefix_bytes < 0
    ):
        raise ValueError("prefix_bytes must be a non-negative integer")
    reader = _Reader()
    pin_digest = hashlib.sha256()
    try:
        with open(path, "rb") as handle:
            size = os.fstat(handle.fileno()).st_size
            limit = size if prefix_bytes is None else prefix_bytes
            if limit > size:
                raise PinError("prefix is longer than the file")
            remaining = limit
            while remaining > 0:
                chunk = handle.read(min(CHUNK, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                if pinned:
                    pin_digest.update(chunk)
                reader.feed(chunk)
    except OSError:
        raise ReadError("transcript could not be read") from None
    if pinned:
        if remaining > 0:
            raise PinError("prefix is longer than the file")
        actual = pin_digest.hexdigest()
        if actual != str(prefix_sha256).lower():
            raise PinError("prefix hash mismatch")
    return reader.finish(pinned)


def sorted_events(transcript: Transcript) -> List[Event]:
    """Timestamped events in time order: stable, ties broken by file position."""
    return sorted(
        (e for e in transcript.events if e.t_ms is not None),
        key=lambda e: (e.t_ms, e.pos),
    )


def reread(path: Any, nbytes: int, sha256: str) -> bytes:
    """Second, deliberate read of the first `nbytes` bytes, verified against `sha256`.

    Raises:
        PinError: The file is shorter than `nbytes` or the bytes hash differently.
        ReadError: The file could not be opened or read.
    """
    try:
        with open(path, "rb") as handle:
            data = handle.read(nbytes)
    except OSError:
        raise ReadError("transcript could not be read") from None
    if len(data) != nbytes or hashlib.sha256(data).hexdigest() != sha256:
        raise PinError("re-read bytes differ from the pinned bytes")
    return data


def timestamped_pct(transcript: Transcript) -> Optional[float]:
    """Percent of records carrying a parseable timestamp; `None` when there are none."""
    records = transcript.snapshot.records
    if records == 0:
        return None
    return 100.0 * (records - transcript.untimed - transcript.unparseable) / records


def unparseable_pct(transcript: Transcript) -> Optional[float]:
    """Percent of records that did not parse; `None` when there are none."""
    records = transcript.snapshot.records
    if records == 0:
        return None
    return 100.0 * transcript.unparseable / records


def event_kinds(events: Iterable[Event]) -> FrozenSet[str]:
    """The set of `Tok.kind` values present in `events`."""
    return frozenset(t.kind for e in events for t in e.toks)


__all__ = [
    "PinError",
    "ReadError",
    "event_kinds",
    "format_ts",
    "gap_flags",
    "parse_bytes",
    "parse_ts",
    "read_transcript",
    "reread",
    "sorted_events",
    "timestamped_pct",
    "unparseable_pct",
]

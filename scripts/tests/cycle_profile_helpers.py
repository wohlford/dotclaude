"""Synthetic-transcript builder and known-answer fixtures for the cycle-profile tests.

Every transcript is built at test time from the small DSL below: no real or derived transcript
content exists anywhere in the tree. Times are offsets in seconds from a fictional base
(`2030-01-01T00:00:00Z`), ids are invented, and every text field comes from a fixed lorem word
list, so no real content can enter. The module imports no library module: the per-second
oracle (`brute_force_classes`) shares only the interval lists it is handed.
"""

from __future__ import annotations

import datetime
import itertools
import json
import os
import random
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, NamedTuple, Optional

REPO = Path(__file__).resolve().parents[2]
LIB = REPO / "scripts" / "lib"
TESTS = REPO / "scripts" / "tests"
CLI = REPO / "scripts" / "cycle-profile.py"

BASE = datetime.datetime(2030, 1, 1, tzinfo=datetime.timezone.utc)
BASE_ISO = "2030-01-01T00:00:00Z"
BASE_MS = int(BASE.timestamp()) * 1000
VERSION = "9.9.1"
SESSION = "sess0001"
AGENT1 = "aid00000000000001"
AGENT2 = "aid00000000000002"

LOREM_WORDS = (
    "lorem ipsum dolor sit amet consectetur adipiscing elit sed do eiusmod tempor "
    "incididunt ut labore et dolore magna aliqua enim minim veniam quis nostrud "
    "exercitation ullamco laboris nisi aliquip ex ea commodo consequat"
).split()


def lorem(n: int = 0, words: int = 14) -> str:
    """A deterministic lorem phrase of at least 60 characters and three or more spaces."""
    out: List[str] = []
    i = 0
    while len(out) < words or len(" ".join(out)) < 60:
        out.append(LOREM_WORDS[(n * 7 + i * 3) % len(LOREM_WORDS)])
        i += 1
    return " ".join(out)


def stamp(at: float) -> str:
    """The transcript timestamp for an offset in seconds from the fictional base."""
    when = BASE + datetime.timedelta(milliseconds=round(at * 1000))
    return when.strftime("%Y-%m-%dT%H:%M:%S.") + "%03dZ" % (when.microsecond // 1000)


# ---------------------------------------------------------------------------------------------
# Record builders: each returns {"at", "rec", "ts", "ts_raw"} for `build_transcript`
# ---------------------------------------------------------------------------------------------


def _ev(
    at: float,
    rtype: str,
    body: Dict[str, Any],
    *,
    sidechain: bool = False,
    meta: bool = False,
    version: Optional[str] = VERSION,
    ts: bool = True,
    ts_raw: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    rec: Dict[str, Any] = {
        "type": rtype,
        "sessionId": SESSION,
        "isSidechain": sidechain,
    }
    if version is not None:
        rec["version"] = version
    if meta:
        rec["isMeta"] = True
    rec.update(body)
    rec.update(extra or {})
    return {"at": at, "rec": rec, "ts": ts, "ts_raw": ts_raw}


def prompt(at: float, text: Optional[str] = None, origin: Optional[str] = None, **kw):
    """A user record with string content (a human prompt unless `origin` says otherwise)."""
    body: Dict[str, Any] = {
        "message": {
            "role": "user",
            "content": text if text is not None else lorem(int(at)),
        }
    }
    if origin is not None:
        body["turnOrigin"] = origin
    return _ev(at, "user", body, **kw)


def wrapper(at: float, name: str, args: str = "", **kw):
    """A user `<command-name>` wrapper record (a slash-command invocation)."""
    text = (
        f"<command-message>{name}</command-message>\n"
        f"<command-name>/{name}</command-name>\n<command-args>{args}</command-args>"
    )
    return _ev(at, "user", {"message": {"role": "user", "content": text}}, **kw)


def say(at: float, text: Optional[str] = None, **kw):
    """An assistant record holding one text block."""
    block = {"type": "text", "text": text if text is not None else lorem(int(at))}
    return _ev(
        at, "assistant", {"message": {"role": "assistant", "content": [block]}}, **kw
    )


def _tool_use(at: float, tool_id: str, name: str, inp: Dict[str, Any], **kw):
    block = {"type": "tool_use", "id": tool_id, "name": name, "input": inp}
    return _ev(
        at, "assistant", {"message": {"role": "assistant", "content": [block]}}, **kw
    )


def skill(at: float, tool_id: str, name: str, args: str = "", **kw):
    """An assistant `Skill` tool_use."""
    return _tool_use(at, tool_id, "Skill", {"skill": name, "args": args}, **kw)


def agent(
    at: float,
    tool_id: str,
    model: Optional[str],
    description: str,
    subagent_type: Optional[str] = None,
    **kw,
):
    """An assistant `Agent` tool_use."""
    inp: Dict[str, Any] = {"description": description, "prompt": lorem(int(at))}
    if model is not None:
        inp["model"] = model
    if subagent_type is not None:
        inp["subagent_type"] = subagent_type
    return _tool_use(at, tool_id, "Agent", inp, **kw)


def sendmsg(at: float, tool_id: str, to: str, summary: str, **kw):
    """An assistant `SendMessage` tool_use (a resumed agent)."""
    inp = {"to": to, "summary": summary, "message": lorem(int(at))}
    return _tool_use(at, tool_id, "SendMessage", inp, **kw)


def bash(at: float, tool_id: str, command: str, bg: bool = False, **kw):
    """An assistant `Bash` tool_use."""
    inp: Dict[str, Any] = {"command": command}
    if bg:
        inp["run_in_background"] = True
    return _tool_use(at, tool_id, "Bash", inp, **kw)


def runlong_launch(
    at: float,
    tool_id: str,
    art: str,
    label: str,
    suite: bool = True,
    wait_too: bool = False,
    **kw,
):
    """A run-long launch of the audit entry point (with the tests flag when `suite`)."""
    target = "scripts/audit.sh --tests" if suite else "scripts/audit.sh"
    wait = " --wait" if wait_too else ""
    command = (
        f'scripts/run-long.sh --out /work/out/{art} --label "{label}"{wait} '
        f"--expect 'RESULT: (PASS|FAIL)' -- {target}"
    )
    return bash(at, tool_id, command, **kw)


def runlong_wait(
    at: float, tool_id: str, art: str, bg: bool = True, mode: str = "wait", **kw
):
    """A run-long `--wait` (or `--status`) call on an artifact."""
    return bash(
        at, tool_id, f"scripts/run-long.sh --{mode} /work/out/{art}", bg=bg, **kw
    )


def edit(at: float, tool_id: str, name: str = "Edit", **kw):
    """An assistant edit-class tool_use (`Edit`, `Write` or `MultiEdit`)."""
    return _tool_use(
        at, tool_id, name, {"file_path": "/work/f.txt", "new_string": "x"}, **kw
    )


def result(at: float, tool_id: str, text: str = "ok", **kw):
    """A user record holding one `tool_result` block for `tool_id`."""
    block = {"type": "tool_result", "tool_use_id": tool_id, "content": text}
    return _ev(at, "user", {"message": {"role": "user", "content": [block]}}, **kw)


def ack(at: float, tool_id: str, agent_id: str, **kw):
    """The tool_result that acknowledges an `Agent` dispatch, carrying the agent id."""
    block = {"type": "tool_result", "tool_use_id": tool_id, "content": "launched"}
    body = {
        "message": {"role": "user", "content": [block]},
        "toolUseResult": {"agentId": agent_id, "status": "async_launched"},
    }
    return _ev(at, "user", body, **kw)


def notification_text(task_id: str, tool_id: str, duration_ms: int) -> str:
    """A task-notification body as the queue and delivery records carry it."""
    return (
        f"<task-notification>\n<task-id>{task_id}</task-id>\n"
        f"<tool-use-id>{tool_id}</tool-use-id>\n<status>completed</status>\n"
        f"<duration_ms>{duration_ms}</duration_ms>\n</task-notification>"
    )


def enqueue(at: float, tool_id: str, task_id: str, duration_ms: int = 1000, **kw):
    """A `queue-operation` enqueue holding a task notification."""
    body = {
        "operation": "enqueue",
        "content": notification_text(task_id, tool_id, duration_ms),
    }
    return _ev(at, "queue-operation", body, **kw)


def delivery(at: float, tool_id: str, task_id: str, duration_ms: int = 1000, **kw):
    """The user record that delivers the notification to the model."""
    text = notification_text(task_id, tool_id, duration_ms)
    body = {
        "message": {"role": "user", "content": text},
        "turnOrigin": "task_notification",
    }
    return _ev(at, "user", body, **kw)


def hook(at: float, tool_id: str, ms: int, **kw):
    """A `hook_success` attachment record."""
    att = {
        "type": "hook_success",
        "hookEvent": "PostToolUse",
        "toolUseID": tool_id,
        "durationMs": ms,
    }
    return _ev(at, "attachment", {"attachment": att}, **kw)


def turn(at: float, ms: int, **kw):
    """A `turn_duration` system record."""
    return _ev(at, "system", {"subtype": "turn_duration", "durationMs": ms}, **kw)


# ---------------------------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------------------------


def build_transcript(
    events: Iterable[Dict[str, Any]],
    *,
    shuffle_seed: Optional[int] = None,
    drop_fields: Iterable[str] = (),
    tear: bool = False,
) -> bytes:
    """Serialise events as JSON lines.

    Args:
        events: Records from the builders above, in file order.
        shuffle_seed: When given, shuffle the record order (the non-monotone case).
        drop_fields: Top-level record keys removed from every record (shape drift).
        tear: Cut the last line in half and drop its newline (a live session's torn tail).
    """
    items = list(events)
    if shuffle_seed is not None:
        random.Random(shuffle_seed).shuffle(items)
    lines = []
    for item in items:
        rec = dict(item["rec"])
        if item.get("ts_raw") is not None:
            rec["timestamp"] = item["ts_raw"]
        elif item.get("ts", True):
            rec["timestamp"] = stamp(item["at"])
        for name in drop_fields:
            rec.pop(name, None)
        lines.append(json.dumps(rec, separators=(",", ":")))
    if tear and lines:
        head = "".join(line + "\n" for line in lines[:-1])
        return (head + lines[-1][: len(lines[-1]) // 2]).encode()
    return ("".join(line + "\n" for line in lines)).encode()


def write_transcript(
    directory: Path, data: bytes, name: str = "synth000.jsonl"
) -> Path:
    """Write a transcript under `<directory>/in/` and return its resolved path."""
    target = Path(directory).resolve() / "in"
    target.mkdir(parents=True, exist_ok=True)
    path = target / name
    path.write_bytes(data)
    return path


def write_delegate_dir(directory: Path, files: Dict[str, bytes]) -> Path:
    """Write `agent-<id>.jsonl` files into `directory` and return its resolved path."""
    out = Path(directory).resolve()
    out.mkdir(parents=True, exist_ok=True)
    for agent_id, data in files.items():
        (out / f"agent-{agent_id}.jsonl").write_bytes(data)
    return out


# ---------------------------------------------------------------------------------------------
# Known-answer cycles K1 (Skill commit marker) and K1w (wrapper commit marker): spec 6.3
# ---------------------------------------------------------------------------------------------


def k1_events(variant: str, *, agree_ids: bool = False) -> List[Dict[str, Any]]:
    """The 6.3 record table. `variant` is `"skill"` (K1) or `"wrapper"` (K1w).

    With `agree_ids` the notifications of the two dispatches carry the agent ids of their
    acknowledgements (`AGENT1`, `AGENT2`) as task ids, as a real session's do, instead of
    `t1` and `t2`; the default output is the table's own bytes.
    """
    if variant not in ("skill", "wrapper"):
        raise ValueError("variant is skill or wrapper")
    task1, task2 = (AGENT1, AGENT2) if agree_ids else ("t1", "t2")
    ev: List[Dict[str, Any]] = [wrapper(0, "feature")]
    ev += [say(t) for t in (10, 30, 50, 70, 90)]
    ev += [
        agent(100, "a1", "sonnet", "spec " + lorem(1)),
        ack(101, "a1", AGENT1),
        enqueue(400, "a1", task1, 300000),
        delivery(401, "a1", task1, 300000),
    ]
    ev += [say(t) for t in (410, 430, 460)]
    ev += [prompt(520)]
    ev += [say(530), edit(540, "e1"), result(549, "e1"), say(550)]
    ev += [edit(560, "e2"), result(570.000, "e2"), say(590)]
    ev += [
        runlong_launch(600, "l1", "art1", "feature-tip abc1234"),
        result(601, "l1"),
        runlong_wait(602, "w1", "art1"),
        result(603, "w1"),
    ]
    ev += [
        agent(900, "a2", "opus", "plan " + lorem(2)),
        ack(901, "a2", AGENT2),
        enqueue(1200, "a2", task2, 300000),
        enqueue(1500, "w1", "t3", 900000),
    ]
    ev += [
        say(1510),
        say(1540),
        edit(1560, "e3"),
        result(1590, "e3"),
        say(1600),
        say(1630),
    ]
    ev += [
        runlong_launch(1650, "l2", "art2", "dev-tip def5678"),
        result(1651, "l2"),
        runlong_wait(1652, "w2", "art2"),
        result(1653, "w2"),
        enqueue(2400, "w2", "t4", 750000),
        say(2400),
    ]
    if variant == "skill":
        ev.append(skill(2430, "k1", "commit"))
    else:
        ev.append(wrapper(2430, "commit"))
    return ev


def k1_transcript(variant: str, *, agree_ids: bool = False) -> bytes:
    """K1 (`"skill"`) or K1w (`"wrapper"`) as transcript bytes (see `k1_events`)."""
    return build_transcript(k1_events(variant, agree_ids=agree_ids))


def k1_delegate_files() -> Dict[str, bytes]:
    """The A1 delegate file of 6.3: a run-long Bash 120 to 220 and an Edit 250 to 280."""
    kw = {"sidechain": True, "extra": {"agentId": AGENT1}}
    events = [
        runlong_launch(120, "d1", "dart", "gate", **kw),
        result(220, "d1", **kw),
        edit(250, "d2", **kw),
        result(280, "d2", **kw),
    ]
    return {AGENT1: build_transcript(events)}


class RandomCycle(NamedTuple):
    """A seeded synthetic cycle: its events, bytes and its own window."""

    events: List[Dict[str, Any]]
    data: bytes
    start_s: int
    end_s: int
    wall_s: int


def random_cycle(seed: int) -> RandomCycle:
    """A seeded cycle of about 40 records (integer-second times) and its own `end - start`.

    The cycle starts with a `/feature` wrapper, holds random assistant records, human
    prompts, overlapping agent dispatches, edits of random latency and two suites of at
    least 600 s, and ends at a `Skill` commit marker within 60 s of the dev-tip verdict.
    """
    rng = random.Random(seed)
    ev: List[Dict[str, Any]] = [wrapper(0, "feature")]
    ids = itertools.count(1)
    now = [0]

    def tick(lo: int, hi: int) -> int:
        now[0] += rng.randint(lo, hi)
        return now[0]

    def filler() -> None:
        for _ in range(rng.randint(3, 5)):
            ev.append(say(tick(5, 40)))
        if rng.random() < 0.6:
            ev.append(prompt(tick(5, 60)))
        for _ in range(rng.randint(1, 3)):
            n = next(ids)
            start = tick(1, 30)
            end = start + rng.randint(30, 400)
            ev.extend(
                [
                    agent(start, f"a{n}", "sonnet", "task " + lorem(n)),
                    ack(start + 1, f"a{n}", f"aid{n:014d}"),
                    enqueue(end, f"a{n}", f"t{n}", (end - start) * 1000),
                ]
            )
        for _ in range(rng.randint(1, 3)):
            n = next(ids)
            start = tick(1, 20)
            ev.extend(
                [edit(start, f"e{n}"), result(start + rng.randint(1, 40), f"e{n}")]
            )

    def suite(label: str, art: str) -> int:
        n = next(ids)
        start = tick(1, 30)
        length = rng.randint(600, 1000)
        ev.extend(
            [
                runlong_launch(start, f"l{n}", art, label),
                result(start + 1, f"l{n}"),
                runlong_wait(start + 2, f"w{n}", art),
                result(start + 3, f"w{n}"),
                enqueue(start + length, f"w{n}", f"t{n}", length * 1000),
            ]
        )
        now[0] = start + length
        return start + length

    filler()
    suite("feature-tip abc1234", "art1")
    filler()
    verdict = suite("dev-tip def5678", "art2")
    end = verdict + rng.randint(0, 60)
    ev.append(skill(end, "k1", "commit"))
    ev.sort(key=lambda e: e["at"])
    return RandomCycle(ev, build_transcript(ev), 0, end, end - 0)


def kitchen_sink_events() -> List[Dict[str, Any]]:
    """Events that make the reader emit every `Tok` kind of the contract once or more."""
    return [
        wrapper(0, "feature", "--plan-only"),
        skill(1, "s1", "feature", "--plan-only"),
        prompt(2),
        prompt(3, origin="auto_continuation"),
        prompt(4, "[Request interrupted by user]"),
        say(5, "use the fast lane " + lorem(5)),
        agent(10, "a1", "sonnet", "spec author " + lorem(6)),
        ack(11, "a1", AGENT1),
        sendmsg(12, "m1", "a1", "fold the spec " + lorem(7)),
        enqueue(20, "a1", "t1", 9000),
        delivery(21, "a1", "t1", 9000),
        bash(30, "b1", "sleep 1 && " + lorem(8), bg=True),
        result(31, "b1", "RESULT: PASS rc=0"),
        runlong_launch(40, "l1", "art1", "feature-tip abc1234", wait_too=True),
        runlong_wait(41, "w1", "art1", bg=False),
        runlong_wait(42, "st1", "art1", bg=False, mode="status"),
        edit(50, "e1"),
        hook(51, "e1", 500),
        turn(52, 90000),
        skill(60, "k1", "commit"),
        skill(61, "k2", "propagate"),
        wrapper(62, "propagate"),
    ]


def kitchen_sink_transcript() -> bytes:
    """`kitchen_sink_events` as transcript bytes."""
    return build_transcript(kitchen_sink_events())


# ---------------------------------------------------------------------------------------------
# Oracle and CLI wrapper
# ---------------------------------------------------------------------------------------------

_ORACLE_ORDER = ("MACHINE_WAIT", "SUBAGENT", "HOOK", "OPERATOR_RESPONSE", "AWAY")
_ORACLE_CLASSES = (
    "CONTROLLER",
    "SUBAGENT",
    "MACHINE_WAIT",
    "HOOK",
    "OPERATOR_RESPONSE",
    "AWAY",
)


def brute_force_classes(
    intervals: Dict[str, Iterable[Any]], lo: float, hi: float
) -> Dict[str, int]:
    """Per-second class seconds over `[lo, hi)`: a plain loop that shares no lib code.

    Each second is judged at its midpoint against the half-open intervals, highest class
    first (machine wait, subagent, hook, operator response, away); the residual is
    CONTROLLER. `intervals` maps a class name to `(start_s, end_s)` pairs.
    """
    out = {name: 0 for name in _ORACLE_CLASSES}
    pairs = {name: list(intervals.get(name, ())) for name in _ORACLE_ORDER}
    for sec in range(int(lo), int(hi)):
        mid = sec + 0.5
        for name in _ORACLE_ORDER:
            if any(a <= mid < b for a, b in pairs[name]):
                out[name] += 1
                break
        else:
            out["CONTROLLER"] += 1
    return out


def run_cli(
    args: Iterable[Any],
    cwd: Optional[Path] = None,
    env: Optional[Dict[str, str]] = None,
    timeout: int = 120,
) -> "subprocess.CompletedProcess[str]":
    """Run the shipped CLI as a real process; `env` entries are added to the environment."""
    full = dict(os.environ)
    full.update(env or {})
    return subprocess.run(
        [sys.executable, str(CLI), *[str(a) for a in args]],
        cwd=None if cwd is None else str(cwd),
        env=full,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )

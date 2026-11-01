"""Shared vocabulary, types and parameter defaults for the cycle-profile tooling.

This is the one contention point of the cycle-profile modules: every closed vocabulary, every
type that crosses a module boundary and the profile layout live here, so a later module never
edits an earlier one to read a result. The modules form a one-way chain (`model`, `records`,
`cycles`, `classes`, `delegates`, `pool`, `manifest`, `checks`, `privacy`, `run`, `report`); this
file imports nothing from the chain.

**Tokens, not text.** The reader turns each transcript record into an `Event` carrying `Tok`
values. `Tok.a` and `Tok.b` hold ids (`ID_RE`) or closed-vocabulary strings, `Tok.n` an optional
number, and an absent field is `None`. No message, command or description text survives into an
`Event`; text is scanned in memory for fixed vocabularies and only the derived token is kept.
A run-long artifact key (`Tok.b` of a `runlong` token) is command-derived, so the reader
emits only `art_key_digest(key)`: pairing needs equality and nothing else. A key that is a
shell variable is `ART_UNRESOLVED_PREFIX` plus the digest of its text (still equal only to
itself), and an empty or option-like key is `None`. On a wait or status call `rl_check` holds
the class a suite launch with that key would get, so the digest loses no classification.

**`sendmsg_to`** (`a` tool-use id, `b` the `SendMessage` `to` field) is emitted only when `to`
fullmatches `ID_RE`, i.e. is an agent id; a teammate name never reaches a token. **`agent_ack`**
(`a` tool-use id, `b` agent id) comes from a single-`tool_result` record whose `toolUseResult`
names an agent in `agentId`, `resumedAgentId` or `revivedAgentId`; for a `SendMessage` the
resumed and revived keys come first, for any other dispatch `agentId` does.

**`tool_result`** tokens carry `a` the tool-use id and `b` a value from the closed set
`TOOL_RESULT_VALUES`: the last `RESULT: <verdict>` verdict in the result text, else `DONE`
when the text holds the whole word DONE and not the whole word RUNNING (the original's
foreground wait rule), else `None`. A verdict, when present, wins over DONE.

**Timestamps** are integer milliseconds (`t_ms`), so the closing-rule boundary comparisons are
exact; seconds are floats only after the comparisons.

**Profile layout v1** (`run` builds it, `manifest` resolves selectors against it, `report`
renders it, and `data.json` IS this tree; every key is a lowercase source constant, never built
from text; times are seconds):

| Key path | Holds |
| :-- | :-- |
| `tool.version` | token |
| `input.{records,timestamped_pct,unparseable,inversions,last_ts,sha256,bytes,pinned}` | read statistics |
| `params[]`, `shapes[]`, `versions[]`, `floors[]` | `{name,value,source}`; `{name,count,class,needed_by}`; `{version,records,timestamped_pct,counts}`; `{name,want,actual,ok}` |
| `cycles.{completed,partial[],open[],list[]}` | `list[]`: `start_utc, wall_s, lane, lane_source, status, reconstructable, suite_runs, classes_s, stages, split_s` |
| `pool.{wall_all_s,wall_active_s,away_s,classes_s,halves,placements[],distinct_evaluations,answer,corners[],ranking[],repeat[]}` | `halves.{active,all}.{work_pct,gate_pct,operator_pct,unsplit_pct}`; `answer` one of `gate work undetermined` |
| `idle.{gaps,q1_s,q3_s}`, `fence.{k,method,inner_s,outer_s,above,leave_one_out_min_s,leave_one_out_max_s}` | idle list and AWAY fence |
| `delegates.{matched,total,orphans,inventory[]}` | `inventory[]`: `{id,bytes,sha256}` |
| `sensitivity`, `checks[]`, `manifest`, `leak`, `limits`, `not_measured[]` | sets with windows kept and moved; `CheckRow` list; manifest summary or null; `{samples,hits,control,floor}`; per-id computed fields |
"""

from __future__ import annotations

import hashlib
import re
from typing import Any, Dict, FrozenSet, List, NamedTuple, Optional, Tuple

# ---------------------------------------------------------------------------------------------
# Closed vocabularies
# ---------------------------------------------------------------------------------------------

#: Opaque ids (agent, task, tool-use) fullmatch this; a channel check, not a format claim.
ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

#: A Claude Code version string is kept only when it fullmatches this, else `unknown`.
VERSION_RE = re.compile(r"^[0-9]{1,4}(\.[0-9]{1,6}){0,3}$")

#: Transcript record types the reader distinguishes; anything else is `other`.
RECORD_TYPES: Tuple[str, ...] = (
    "user",
    "assistant",
    "system",
    "attachment",
    "queue-operation",
    "other",
)

#: Record shapes the reader counts (D2.3). Value: (class, needed-by token). The three shapes
#: in `DISPATCH_CONDITIONAL` are CRITICAL only when some `agent_dispatch` exists. The spec
#: tags no class for `user_prompt`, `agent_dispatch` and `edit_tool_use`; they are
#: CROSS-CHECK here because a transcript can legitimately hold none.
SHAPE_TABLE: Dict[str, Tuple[str, str]] = {
    "ts": ("CRITICAL", "time-arithmetic"),
    "user_prompt": ("CROSS-CHECK", "idle-gaps"),
    "feature_invocation": ("CRITICAL", "roster"),
    "commit_marker": ("CROSS-CHECK", "closing-marker"),
    "propagate_marker": ("CROSS-CHECK", "closing-marker"),
    "agent_dispatch": ("CROSS-CHECK", "dispatches"),
    "agent_ack": ("CRITICAL", "dispatch-agent-map"),
    "task_enqueue": ("CRITICAL", "dispatch-intervals"),
    "task_delivery": ("CRITICAL", "dispatch-intervals"),
    "bash_tool_use": ("CRITICAL", "run-long-pairing"),
    "edit_tool_use": ("CROSS-CHECK", "hook-class"),
    "tool_result": ("CRITICAL", "tool-results"),
    "hook_success": ("CROSS-CHECK", "hook-crosscheck"),
    "turn_duration": ("CROSS-CHECK", "turn-crosscheck"),
    "sidechain_flag": ("CROSS-CHECK", "sidechain-filter"),
}
SHAPES: Tuple[str, ...] = tuple(SHAPE_TABLE)
DISPATCH_CONDITIONAL: FrozenSet[str] = frozenset(
    {"agent_ack", "task_enqueue", "task_delivery"}
)

#: The tool_use kinds the version census counts per version.
TOOL_KINDS: Tuple[str, ...] = ("Skill", "Agent", "SendMessage", "Bash", "Edit")
EDIT_NAMES: Tuple[str, ...] = ("Edit", "Write", "MultiEdit")
INVOCATION_NAMES: Tuple[str, ...] = ("feature", "commit", "propagate")

#: Time classes, and the sweep order (highest priority first). OPERATOR_RESPONSE and AWAY
#: share one tier: idle time is split between them by the fence, never overlapped.
CLASSES: Tuple[str, ...] = (
    "CONTROLLER",
    "SUBAGENT",
    "MACHINE_WAIT",
    "HOOK",
    "OPERATOR_RESPONSE",
    "AWAY",
)
PRIORITY: Tuple[str, ...] = (
    "MACHINE_WAIT",
    "SUBAGENT",
    "HOOK",
    "OPERATOR_RESPONSE",
    "AWAY",
    "CONTROLLER",
)

#: `/feature` stages in order, and the stage buckets (the stages plus one `other`). Stage
#: buckets and check classes are two distinct kinds so their conflation cannot be rewritten.
STAGES: Tuple[str, ...] = (
    "triage/spec-author",
    "spec-review/ruling",
    "spec-fold",
    "plan-author",
    "plan-review",
    "plan-fold",
    "execute",
    "gates",
    "commit",
    "feature-tip-suite",
    "bricks",
    "dev-tip-suite",
    "closing",
)
STAGE_BUCKETS: Tuple[str, ...] = STAGES + ("other",)

#: Run-long check classes, most specific first (the original's `MPRI`).
CHECK_CLASSES: Tuple[str, ...] = (
    "suite-feature-tip",
    "suite-dev-tip",
    "suite-unlabelled",
    "kill-sets/campaign",
    "per-brick-sweep",
    "plain-audit",
    "other",
)

#: Words of a dispatch description that name a stage (`stage_kw` tokens): the original's
#: keyword set plus every other word its stage classifier (`dstage_kind`) consults.
_KEYWORD_SET = (
    "spec plan review fold implement task rule ruling audit vet author draft write "
    "fix brick publish commit gate probe spike final whole"
)
_CLASSIFIER_WORDS = "reviews reviewer ruled verify writer folds folded implementer"
STAGE_KEYWORDS: FrozenSet[str] = frozenset(
    _KEYWORD_SET.split() + _CLASSIFIER_WORDS.split()
)

#: Prefix of the `runlong` key token for an unresolved (shell-variable) artifact key.
ART_UNRESOLVED_PREFIX = "unresolved-"

#: Substrings that class a Bash command as a check (`bash_class` tokens).
CHECK_TOKENS: Tuple[str, ...] = ("audit", "pytest", "tests/", "mutate", "kill-sets")

ROLES: Tuple[str, ...] = (
    "fable",
    "opus",
    "sonnet",
    "haiku",
    "general-purpose",
    "prose-writer",
    "skill-reviewer",
    "skill-content-reviewer",
    "other",
)
_SUBAGENT_ROLES = ("skill-reviewer", "skill-content-reviewer", "prose-writer")
_MODEL_ROLES = ("fable", "opus", "sonnet", "haiku")

LIMIT_IDS: Tuple[str, ...] = tuple("L%02d" % i for i in range(1, 19))
VERDICTS: Tuple[str, ...] = ("PASS", "FAIL", "ERROR", "INCOMPLETE")
RC: Dict[str, int] = {"PASS": 0, "FAIL": 1, "ERROR": 2, "INCOMPLETE": 3}
CHECK_VERDICTS: Tuple[str, ...] = ("pass", "fail", "skip")
MISSING_LINKS: Tuple[str, ...] = (
    "no-suite-launch",
    "none-paired",
    "none-real",
    "no-dev-or-order-pair",
)

#: The closed values of a `tool_result` token's `b` (besides `None`).
TOOL_RESULT_VALUES: FrozenSet[str] = frozenset(
    {"PASS", "FAIL", "ERROR", "INCOMPLETE", "INDETERMINATE", "DONE"}
)

#: Every `Tok.kind` the reader emits, and no other (the binding contract of the reader).
TOK_KINDS: FrozenSet[str] = frozenset(
    {
        "human_prompt",
        "auto_continuation",
        "interrupt",
        "invocation",
        "plan_only",
        "lane_mention",
        "tool_use",
        "bash_bg",
        "bash_class",
        "runlong",
        "rl_check",
        "rl_suite",
        "rl_label_sha",
        "rl_launch_with_wait",
        "tool_result",
        "agent_dispatch",
        "stage_kw",
        "sendmsg_dispatch",
        "sendmsg_to",
        "agent_ack",
        "notif_enqueue",
        "notif_delivery",
        "hook_success",
        "turn_duration",
    }
)


def art_key_digest(text: str) -> str:
    """The first 16 hex characters of the sha256 of a run-long artifact key or file name."""
    return hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()[:16]


def role_of(subagent_type: Any, model: Any) -> str:
    """Map a dispatch's `subagent_type` and `model` into the closed `ROLES` set.

    A reviewer or writer subagent type wins, then an exact model alias, then
    `general-purpose`; anything else (including non-strings) is `other`.
    """
    if isinstance(subagent_type, str) and subagent_type in _SUBAGENT_ROLES:
        return subagent_type
    if isinstance(model, str) and model in _MODEL_ROLES:
        return model
    if subagent_type == "general-purpose":
        return "general-purpose"
    return "other"


# ---------------------------------------------------------------------------------------------
# Core types
# ---------------------------------------------------------------------------------------------


class Tok(NamedTuple):
    """A derived token: ids or closed-vocabulary strings, an optional number, never text."""

    kind: str
    a: Optional[str] = None
    b: Optional[str] = None
    n: Optional[int] = None


class Event(NamedTuple):
    """One transcript record as tokens. `t_ms` is `None` for a record without a timestamp."""

    pos: int
    t_ms: Optional[int]
    rtype: str
    sidechain: bool
    meta: bool
    version: str
    toks: Tuple[Tok, ...]


class CheckRow(NamedTuple):
    """One self-check row; `verdict` is one of `CHECK_VERDICTS`."""

    name: str
    verdict: str
    detail: str


class Snapshot(NamedTuple):
    """The bytes the reader parsed: hash and size of exactly those bytes, plus statistics."""

    sha256: str
    bytes: int
    records: int
    last_ts: Optional[str]
    pinned: bool


class VersionRow(NamedTuple):
    """One row of the per-`version` census.

    `records` counts PARSED records of this version, so the rows sum to
    `Snapshot.records - Transcript.unparseable`, never to `Snapshot.records` itself.
    """

    version: str
    records: int
    timestamped: int
    shapes: Dict[str, int]
    kinds: Dict[str, int]
    gaps: FrozenSet[str]


class Transcript(NamedTuple):
    """Everything one read of a transcript yields. `events` are in file order.

    `snapshot.records` counts every non-blank line, unparseable ones included; `events` and
    the per-version census rows hold parsed records only, so
    `sum(row.records for row in versions) + unparseable == snapshot.records`.
    """

    events: List[Event]
    snapshot: Snapshot
    unparseable: int
    untimed: int
    inversions: int
    shapes: Dict[str, int]
    kinds: Dict[str, int]
    versions: Tuple[VersionRow, ...]
    gaps: FrozenSet[str]
    roles_other: int


class RunLongPair(NamedTuple):
    launch_ms: int
    terminal_ms: Optional[int]
    check_class: str
    art_key: Optional[str]
    label_sha: Optional[str]
    method: str
    background: bool
    is_suite: bool


class Window(NamedTuple):
    start_t_ms: int
    search_bound_ms: int
    status: str
    end_t_ms: Optional[int]
    basis: Optional[str]
    f1: Any
    d1: Any
    chain: Any
    end_marker: Optional[str]
    reconstructable: Optional[str]
    lane: Optional[str]
    lane_source: Optional[str]
    plan_only: bool
    suite_runs: int
    pairing_weak: bool
    first_missing_link: Optional[str]
    minutes_from_last_ts: Optional[float]


class CycleSet(NamedTuple):
    invocations: Any
    windows: Any
    pairs: Any
    artifacts: Any


class Dispatch(NamedTuple):
    tool_use_id: str
    agent_id: Optional[str]
    task_id: Optional[str]
    role: str
    stage_kw: Optional[str]
    start_ms: int
    ack_ms: Optional[int]
    end_ms: Optional[int]
    end_source: str


class StageRow(NamedTuple):
    stage: str
    wall_s: float
    classes_s: Dict[str, float]


class ClassCycle(NamedTuple):
    class_s: Dict[str, float]
    hidden_under_machine_wait_s: float
    agent_seconds: float
    stages: List[StageRow]
    idle_gaps_s: List[float]


class Fence(NamedTuple):
    q1: Optional[float]
    q3: Optional[float]
    inner_s: Optional[float]
    outer_s: Optional[float]
    k: float
    method: str


class ClassSet(NamedTuple):
    cycles: Any
    dispatches: Any
    idle_gaps_s: Any
    fence: Any


class DelegateRecord(NamedTuple):
    agent_id: str
    split_s: Dict[str, float]
    bytes_read: int
    sha256: str


class DelegateSet(NamedTuple):
    records: Any
    inventory: Any
    matched: int
    total: int
    orphans: Any


class PoolResult(NamedTuple):
    wall_all_s: float
    wall_active_s: float
    away_s: float
    classes_s: Dict[str, float]
    halves: Any
    placements: Any
    distinct_evaluations: int
    answer: str
    corners: Any
    ranking: Any
    repeat: Any


class LeakResult(NamedTuple):
    samples: int
    hits: int
    control: str
    floor_ok: bool


class Params(NamedTuple):
    """The D2.1 parameters; every default is a stated choice, not a derived value."""

    close_rerun_min: int = 15
    close_post_min: int = 30
    close_min_suite_min: int = 10
    hook_latency_s: int = 10
    long_call_s: int = 60
    lane_window_min: int = 30
    away_fence_k: float = 1.5
    away_min_gaps: int = 8
    min_cycles: int = 5
    max_unparseable_pct: int = 5
    min_timestamped_pct: int = 20
    leak_samples: int = 300
    leak_floor: int = 100
    leak_seed: int = 0


def param_sources(overrides: Any = ()) -> Dict[str, str]:
    """Return `{field: "default"|"cli"}` for the `PARAM name=value source=...` echo.

    Args:
        overrides: The `Params` field names the operator set explicitly.

    Raises:
        ValueError: A name in `overrides` is not a `Params` field.
    """
    names = set(overrides)
    unknown = names - set(Params._fields)
    if unknown:
        raise ValueError("unknown parameter")
    return {f: ("cli" if f in names else "default") for f in Params._fields}

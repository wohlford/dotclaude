"""The cycle-profile report: `report.md` and `data.json` rendered from ONE profile dict.

This is the only module that formats. `render(profile, post=None)` takes the profile of
"Profile layout v1" (`cycle_profile_model`, built by `cycle_profile_run`) and returns the two
files as bytes, so a figure cannot differ between them: `data.json` IS the validated profile
tree (plus the additive keys `run_id`, `stage_pool`, `indicators.subcheck_band` and, with
`post`, `post_checks`), and every number in `report.md` is a value of that tree or a value
divided by 60 and rounded.

**Tokens, numbers and ids only.** The profile holds no transcript text, and this module keeps
it that way: before anything is formatted the whole tree goes through
`privacy.validate_tree` against `DATA_KEYS` (every key a source constant) and the closed
`REPORT_VOCAB`, and the three string kinds that no vocabulary can list are checked here: a
check row's `detail` (words and numbers, a vocabulary derived from the checks' own source),
a manifest selector (the D2.4 grammar) and a `version` (the reader's pattern). A string
outside those channels raises `privacy.PrivacyRefusal`, which the CLI turns into ERROR rc=2.
Opaque ids reach `data.json` only; `report.md` prints no id and no manifest `name`.

**Two passes.** The CLI renders once without `post` (no leak section, no post rows), runs
REMEDYCHECK and LEAKCHECK over those bytes, then renders again with `post`:
`{"leak": {samples, hits, control, floor}, "rows": [REMEDYCHECK and LEAKCHECK rows]}`.
`stdout_summary` and `result_line` give the stdout summary and the `RESULT:` line; the final
verdict itself comes from `cycle_profile_run.derive_verdict`, never from here.

**Measure only.** The report states what waits and by how much; no sentence in it proposes,
ranks a change or sketches one. The LIMITS block is static text in source (`LIMITS`, no run of
three digits, no remedy word) with computed fields read from `profile["limits"]`; a `None`
there prints the closed token `n/a`. Each limit id appears once in the report, so no other
section names one.

**Determinism.** No wall-clock, no randomness, no dependence on dict order: `data.json` is
`json.dumps` with sorted keys (its first line carries the `run_id`, the first 16 hex digits of
the sha256 of the pin and the parameters) and `report.md` follows fixed section order.
"""

from __future__ import annotations

import ast
import copy
import hashlib
import json
import re
from pathlib import Path
from typing import (
    Any,
    Callable,
    Dict,
    FrozenSet,
    Iterable,
    List,
    NamedTuple,
    Optional,
    Tuple,
)

import cycle_profile_privacy as privacy
import cycle_profile_run as run
from cycle_profile_classes import SENSITIVITY_SETS, SUBCHECK_BAND
from cycle_profile_cycles import END_MARKERS, LANE_SOURCES, LANES
from cycle_profile_delegates import SPLIT_KEYS
from cycle_profile_manifest import _VERDICTS as _MANIFEST_VERDICTS
from cycle_profile_model import (
    CHECK_CLASSES,
    CHECK_VERDICTS,
    CLASSES,
    LIMIT_IDS,
    RC,
    SHAPE_TABLE,
    SHAPES,
    STAGE_BUCKETS,
    TOOL_KINDS,
    VERDICTS,
    VERSION_RE,
    Params,
)
from cycle_profile_pool import (
    ARM_PLACEMENTS,
    CORE_BUCKETS,
    REPEAT_REASONS,
    UNATTRIBUTED,
)

#: The closed token printed for a value the profile holds as `None` (never `0`, never `None`).
NA = "n/a"

#: The answer tokens (`pool.answer`), the lead tokens and the control tokens.
ANSWERS: Tuple[str, ...] = ("gate", "work", "undetermined")

#: The statement printed beside an `open` invocation (spec D2.2).
OPEN_PHRASE = "last invocation in the pinned prefix, no closing marker"

#: The two post-render rows `post` may carry, and the words their details may use.
POST_ROWS: Tuple[str, ...] = ("REMEDYCHECK", "LEAKCHECK")

#: The static note under the READ line (spec D3.2): no figure, no remedy word.
READ_NOTE = (
    "Records appended to the transcript while the run is in progress are not read; "
    "the run reads the bytes counted in SNAPSHOT."
)
POST_WORDS: Tuple[str, ...] = ("hits", "samples", "words", "control", "floor")

#: The report headings in order; `Manifest` only with a manifest, `Leak check` only with `post`.
SECTIONS: Tuple[str, ...] = (
    "LIMITS (standing)",
    "Summary",
    "Cycles",
    "Classes",
    "Stages",
    "AWAY and the fence",
    "Delegates",
    "Pooled ranking, halves, placements, corners",
    "Sensitivity",
    "Checks",
    "Manifest",
    "NOT MEASURED",
    "Leak check",
)

#: How many stage rows the summary prints. The summary is then at most 3 + 8 + 2 + 2 lines
#: plus one `NOT MEASURED` line per absent CRITICAL shape (at most 7): 22 lines, under 40.
SUMMARY_STAGES = 8

# ---------------------------------------------------------------------------------------------
# The key and string allowlists
# ---------------------------------------------------------------------------------------------

_STRUCTURE_KEYS = (
    "above above_base above_max above_min absent actual agent_seconds agree answer arm "
    "artifact_end away_base_s away_max_s away_min_s away_pct away_s background_calls "
    "base_s bases basis bytes calls check_s checks class classes_s commit_bound completed "
    "controller_s corner corners count counts cycle cycles delegates delta denominator_s "
    "derived detail dev_in_band dev_run_s dev_s dev_sha dev_tip_suite_s disagree "
    "distinct_evaluations dominant_hook end_marker exact feature_in_band feature_run_s "
    "feature_s feature_sha fence files first_missing_link fitted flips floor floor_decided "
    "floors gap gaps gaps_s gate gate_failures gate_pct gate_s guaranteed halves "
    "hidden_agent_s hidden_under_machine_wait_s hidden_union_s hits hook hook_reported_s "
    "hook_s hours id idle in_band independence independent index indicators input "
    "inside_subagent_leads inventory inversions items k kept kind lane lane_source lanes "
    "largest_delta largest_ms largest_s last_ts lead leads leak leave_one_out_max_s "
    "leave_one_out_min_s limits list manifest matched max_s measurable measured median "
    "members method min_s min_suite_min minutes_from_last_ts moved n name needed_by "
    "no_separation not_enqueue not_measured ok omissions open operator_pct operator_s "
    "order orphans other_s outer_s own_tolerance p10 p90 pairing_weak params parsed parts_s "
    "partial pct_above pct_active pct_all pinned placements plan_only pool post_check "
    "post_checks post_min primary priority q1_s q3_s ranking reason reconstructable "
    "records recorded repeat rerun_min robust rows run_id runlong_calls s same_second "
    "samples sensitivity sets sha256 shapes share_pct sidechain_flag smallest_above_pct "
    "smallest_above_s source spread_s stage stage_pool stages stalled_any_s "
    "stalled_recorded_s start_utc status sub_gate_s sub_lead sub_work_s subcheck "
    "subcheck_band suite_band_min suite_mach suite_runs swapped threshold_s timestamped_pct "
    "tolerance tool total total_s tree unit unknown unlabelled unmeasurable unpaired "
    "unparseable unsplit unsplit_pct unsplit_s untimed value verdict version versions "
    "violated wall_active_s wall_all_s wall_s want weak what work work_pct "
    "work_pct_active work_pct_active_hook_work work_s zone control "
    "active active_hook_work all clock inner_s roles split_s turn_s union_ms union_s selector bad"
).split()

#: Every dictionary key `data.json` may hold: the structural names above and the source
#: constants that key a dictionary (classes, split parts, shapes, tool kinds, roles, limits).
DATA_KEYS: FrozenSet[str] = frozenset(
    _STRUCTURE_KEYS
    + list(CLASSES)
    + list(SPLIT_KEYS)
    + list(SHAPES)
    + list(TOOL_KINDS)
    + list(privacy.ROLES)
    + list(LIMIT_IDS)
    + ["label", "order", "None", "tie"]
)

_UNITS = ("min", "s", "pct", "pp", "count", "token", "hours")


def _source_literals() -> Tuple[FrozenSet[str], FrozenSet[str]]:
    """Check names and detail words, derived from the check producers' own string literals.

    A check row's `detail` is built by the checks, pool and manifest modules from short
    literals and numbers, so the words it may hold are the lowercase words of those
    literals (docstrings and long messages excluded) and a check name is an upper-case
    literal. Deriving the sets keeps them from drifting from the producers.
    """
    here = Path(__file__).resolve().parent
    names: set = set()
    words: set = set()
    for stem in ("checks", "pool", "manifest"):
        tree = ast.parse((here / ("cycle_profile_%s.py" % stem)).read_text("utf-8"))
        docs = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef)):
                body = node.body
                if (
                    body
                    and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                ):
                    docs.add(id(body[0].value))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                continue
            text = node.value
            if id(node) in docs or len(text) > 60:
                continue
            if re.fullmatch(r"[A-Z][A-Z0-9-]{2,40}", text):
                names.add(text)
            words.update(re.findall(r"[a-z][a-z0-9_-]*", text))
    return frozenset(names), frozenset(words)


CHECK_NAMES, DETAIL_WORDS = _source_literals()

#: Closed strings a value may take beyond `privacy.DEFAULT_VOCAB`.
REPORT_VOCAB: FrozenSet[str] = (
    frozenset(
        list(LANES)
        + list(LANE_SOURCES)
        + list(END_MARKERS)
        + list(ARM_PLACEMENTS)
        + list(CORE_BUCKETS)
        + list(REPEAT_REASONS)
        + list(SPLIT_KEYS)
        + list(Params._fields)
        + list(run.PATH_PARAMS)
        + list(run.FLOOR_REASONS)
        + list(run.REASONS)
        + [name for name, _a, _b, _c in SENSITIVITY_SETS]
        + [UNATTRIBUTED]
        + ["MACHINE_WAIT[%s]" % c for c in CHECK_CLASSES]
        + ["corner-%d" % n for n in range(1, 8)]
        + [needed for _kind, needed in SHAPE_TABLE.values()]
        + ["fence", "label", "order", "None"]
        + ["mapped", "unmapped", "measured", "unmeasured", "none", "given"]
        + ["default", "cli", "inclusive", "exclusive", "tie"]
        + list(_MANIFEST_VERDICTS)
        + ["independent", "guaranteed"]
        + ["detected", "not-detected", "CRITICAL", "CROSS-CHECK"]
        + ["completed", "partial", "open", "excluded", "full"]
        + list(POST_ROWS)
        + list(_UNITS)
        + [
            "absent",
            "shape-absent",
            "below-min-gaps",
            "no-class",
            "no-repo",
            "no-artifacts",
            "stage-order",
            "signing-prompt",
            "shape",
            "cross-check",
            "away",
            "corner",
            "class",
            "repeat",
            "commit-bound",
            "artifact-end",
            "closing-marker",
            "turn-crosscheck",
        ]
    )
    | CHECK_NAMES
)

_NUMBER_WORD = re.compile(r"-?\d{1,12}(?:\.\d{1,12})?(?:/-?\d{1,12}(?:\.\d{1,12})?)*")


def _closed(message: str) -> privacy.PrivacyRefusal:
    return privacy.PrivacyRefusal(message)


def _safe_detail(text: Any, extra: Iterable[str] = ()) -> None:
    """Refuse a check `detail` that is not numbers, booleans and words of the producers."""
    if not isinstance(text, str) or not text.strip():
        raise _closed("check detail is not a phrase")
    allowed = DETAIL_WORDS | frozenset(extra) | REPORT_VOCAB
    parts = text.split()
    if len(parts) > 24:
        raise _closed("check detail is too long")
    for part in parts:
        pieces = part.split("=")
        if len(pieces) > 2:
            raise _closed("check detail token is malformed")
        for piece in pieces:
            if piece in ("True", "False", "None") or _NUMBER_WORD.fullmatch(piece):
                continue
            if piece in allowed:
                continue
            raise _closed("check detail word outside the closed words")


_SELECTOR_CLOCK = re.compile(
    r"cycle\[[0-9]{4}-[0-9]{2}-[0-9]{2}(T[0-9:]{2,8})?\]\.clock"
)


def _safe_selector(value: Any) -> None:
    """Refuse a manifest selector outside the D2.4 grammar (a selector is source grammar)."""
    from cycle_profile_manifest import SELECTOR_ALTERNATIVES

    if isinstance(value, str):
        if _SELECTOR_CLOCK.fullmatch(value):
            return
        if any(pat.fullmatch(value) for _k, _u, pat in SELECTOR_ALTERNATIVES):
            return
    raise _closed("selector outside the manifest grammar")


def _safe_version(value: Any) -> None:
    if value == "unknown" or (isinstance(value, str) and VERSION_RE.fullmatch(value)):
        return
    raise _closed("string is not a version")


def _scrub(node: Any, extra: Iterable[str]) -> Any:
    """A copy of `node` with the free-form string kinds checked and replaced by `None`."""
    if isinstance(node, dict):
        out: Dict[str, Any] = {}
        for key, value in node.items():
            if key == "detail":
                _safe_detail(value, extra)
                out[key] = None
            elif key == "selector":
                _safe_selector(value)
                out[key] = None
            elif key == "version":
                _safe_version(value)
                out[key] = None
            else:
                out[key] = _scrub(value, extra)
        return out
    if isinstance(node, list):
        return [_scrub(x, extra) for x in node]
    return node


def _validate(tree: Dict[str, Any]) -> None:
    """Raise `privacy.PrivacyRefusal` for any key or string outside the channels."""
    privacy.validate_tree(
        _scrub(tree, POST_WORDS),
        DATA_KEYS,
        vocab=REPORT_VOCAB,
        id_keys=("id",),
    )


# ---------------------------------------------------------------------------------------------
# Formatting helpers (the only place a number becomes text)
# ---------------------------------------------------------------------------------------------


def _i(value: Any) -> str:
    return NA if value is None else str(int(value))


def _f(value: Any, places: int = 1) -> str:
    return NA if value is None else "%.*f" % (places, float(value) + 0.0)


def _m(seconds: Any) -> str:
    """Seconds as minutes with one decimal."""
    return NA if seconds is None else "%.1f" % (float(seconds) / 60.0 + 0.0)


def _n(value: Any) -> str:
    """A number as the parameters and manifest print it."""
    if value is None:
        return NA
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return "%.6g" % value
    return str(value)


def _t(value: Any) -> str:
    return NA if value is None else str(value)


def _yn(value: Any) -> str:
    return NA if value is None else ("yes" if value else "no")


def _kv(**fields: str) -> str:
    return " ".join("%s=%s" % (k, v) for k, v in fields.items())


def _table(head: List[str], rows: List[List[str]]) -> List[str]:
    out = [
        "| " + " | ".join(head) + " |",
        "| " + " | ".join("---" for _ in head) + " |",
    ]
    out += ["| " + " | ".join(r) + " |" for r in rows]
    return out


def _relation(operator: Any, gate: Any) -> str:
    if operator is None or gate is None:
        return NA
    if operator < gate:
        return "operator-below-gate"
    if operator > gate:
        return "operator-above-gate"
    return "operator-equal-gate"


# ---------------------------------------------------------------------------------------------
# The LIMITS block (L01 to L18): static text and computed fields
# ---------------------------------------------------------------------------------------------


class Field(NamedTuple):
    """One computed field of a limit: its label, the key it reads and how it prints.

    `kind` is `int`, `pct`, `min` (the key holds seconds), `s`, `flag`, `token`, or one of the
    dictionary kinds `sets`, `roles`, `leads`, `sub_leads`, `lanes`, `bases`.
    """

    label: str
    key: str
    kind: str


class Limit(NamedTuple):
    text: str
    fields: Tuple[Field, ...]


def _fields(spec: str) -> Tuple[Field, ...]:
    out = []
    for item in spec.split():
        label, key, kind = item.split(":")
        out.append(Field(label, key, kind))
    return tuple(out)


#: The standing limits. Static text only: no session figure, no run of three digits, no remedy
#: word; the computed fields are the only numbers a limit line holds.
LIMITS: Dict[str, Limit] = {
    "L01": Limit(
        "Timestamps are UTC. Records without one are excluded from time arithmetic. "
        "File order is not time order, so every sweep sorts.",
        _fields(
            "records:records:int timestamped_pct:timestamped_pct:pct "
            "inversions:inversions:int"
        ),
    ),
    "L02": Limit(
        "Overlap rule: MACHINE_WAIT, then SUBAGENT, then HOOK, then OPERATOR_RESPONSE or "
        "AWAY, then CONTROLLER; each second belongs to one class, and agent time shadowed "
        "by a wait is reported as hidden, not lost. CONTROLLER is a residual: model and API "
        "time sit in it and are not separable here.",
        _fields(
            "hidden_under_machine_wait_min:hidden_under_machine_wait_s:min "
            "stalled_recorded_min:stalled_recorded_s:min "
            "stalled_any_min:stalled_any_s:min"
        ),
    ),
    "L03": Limit(
        "The closing-rule constants, the HOOK threshold and the long-call threshold are "
        "chosen, not derived. Sensitivity sets are printed; a tighter set keeps an end for "
        "fewer windows, so its figures come from a smaller population.",
        _fields("sets:sets:sets"),
    ),
    "L04": Limit(
        "AWAY is a Tukey fence over log idle gaps, adopted after the outer fence separated "
        "nothing. It is not robust to one gap: leave-one-out fences and the distance of the "
        "smallest above-fence gap from the fence are printed. The recount check reads the "
        "same idle list as the class assignment; the independent scan shares the machine "
        "and subagent intervals with it.",
        _fields(
            "gaps:gaps:int inner_min:inner_s:min "
            "leave_one_out_min_min:leave_one_out_min_s:min "
            "leave_one_out_max_min:leave_one_out_max_s:min "
            "pct_above:pct_above:pct smallest_above_pct:smallest_above_pct:pct"
        ),
    ),
    "L05": Limit(
        "HOOK is the union of Edit, Write and MultiEdit latency over the threshold. It "
        "includes any slow call that is not a hook, one call can dominate the class, and "
        "hook_success durations are a lower-bound cross-check that missed the long waits.",
        _fields(
            "calls:calls:int union_min:union_s:min largest_s:largest_s:s "
            "share_pct:share_pct:pct hook_reported_min:hook_reported_s:min"
        ),
    ),
    "L06": Limit(
        "GATE is classed by tool type: a Bash call naming run-long.sh, or any Bash call over "
        "the long-call threshold, which is an inference that a long call is a check. A "
        "tool's own runs count as gate even while the cycle builds that tool.",
        _fields(
            "calls:calls:int total_min:total_s:min check_min:check_s:min "
            "other_min:other_s:min"
        ),
    ),
    "L07": Limit(
        "Roles are inferred from the dispatch token (model or subagent type); author and "
        "implementer are not separable.",
        _fields("role:roles:roles"),
    ),
    "L08": Limit(
        "SUBAGENT_WORK is a residual. UNSPLIT is small when every dispatch is matched; the "
        "informative figure is matched dispatches over all dispatches.",
        _fields("matched:matched:int total:total:int unsplit_min:unsplit_s:min"),
    ),
    "L09": Limit(
        "The placements of the unsplit, hook and long-call arms are evaluations, not "
        "independent tests; the number of distinct evaluations is computed.",
        _fields("distinct_evaluations:distinct_evaluations:int lead:leads:leads"),
    ),
    "L10": Limit(
        "The named half is decided by the pooled whole-wall lead under all placements. The "
        "lead inside SUBAGENT alone is reported and does not decide. The tight and loose "
        "sets apply the primary split fractions to their SUBAGENT total, an approximation "
        "rather than a recomputation.",
        _fields("sub_lead:inside_subagent_leads:leads"),
    ),
    "L11": Limit(
        "Run-long checks that carry no stage label form a check class "
        "(UNLABELLED-MACHINE-WAIT), not a stage; the unmapped-bucket rule applies to stage "
        "buckets only.",
        _fields("min:s:min pct_all:pct_all:pct pct_active:pct_active:pct"),
    ),
    "L12": Limit(
        "Corner 3 (delegate foreground run-long waits) is an upper bound; delegates' "
        "background Bash calls are counted and the bound is exact only when that count is "
        "zero. Buckets with no corner are listed as UNMAPPED in the one ranking.",
        _fields(
            "files:files:int runlong_calls:runlong_calls:int "
            "background_calls:background_calls:int exact:exact:flag"
        ),
    ),
    "L13": Limit(
        "Lane is explicit when a lane phrase appears in assistant text within the lane "
        "window and inferred otherwise; every lane figure names its source.",
        _fields("lanes:lanes:lanes"),
    ),
    "L14": Limit(
        "A cycle exists only if the closing rule found an end; partial and open invocations "
        "are excluded and listed. Cycles outside the transcript's coverage are not "
        "measured.",
        _fields(
            "completed:completed:int partial:partial:int open:open:int "
            "basis:bases:bases"
        ),
    ),
    "L15": Limit(
        "turn_duration is printed as a cross-check with no pass criterion; summed turns "
        "exceed CONTROLLER because turns stay open while delegates run. Stage order and "
        "signing prompts have no class and are not measured.",
        _fields("turn_min:turn_s:min controller_min:controller_s:min"),
    ),
    "L16": Limit(
        "The leak check samples; it does not prove absence. Strings shorter than the window "
        "and transformed text are invisible to it; the output allowlist is the primary "
        "control.",
        _fields(
            "samples:samples:int hits:hits:int control:control:token floor:floor:int"
        ),
    ),
    "L17": Limit(
        "Manifest rows compare against typed figures; rows marked guaranteed read the same "
        "source and are not independent; a tolerance floor makes AGREE weak for small "
        "recorded figures.",
        _fields(
            "independent:independent:int guaranteed:guaranteed:int "
            "floor_decided:floor_decided:int"
        ),
    ),
    "L18": Limit(
        "Reading: this shows what waits, not whether a wait is needed. Model and API time "
        "sit inside WORK, at most the stated share of ACTIVE.",
        _fields(
            "work_pct_active:work_pct_active:pct "
            "work_pct_active_hook_work:work_pct_active_hook_work:pct"
        ),
    ),
}


def _counts(prefix: str, counts: Any) -> str:
    if not counts:
        return "%s=none" % prefix
    return " ".join(
        "%s_%s=%d" % (prefix, k, counts[k]) for k in sorted(counts, key=str)
    )


def _field_text(field: Field, data: Dict[str, Any]) -> str:
    value = data.get(field.key)
    kind = field.kind
    if kind == "int":
        return "%s=%s" % (field.label, _i(value))
    if kind == "pct":
        return "%s=%s" % (field.label, _f(value))
    if kind == "min":
        return "%s=%s" % (field.label, _m(value))
    if kind == "s":
        return "%s=%s" % (field.label, _f(value))
    if kind == "flag":
        return "%s=%s" % (field.label, _yn(value))
    if kind == "token":
        return "%s=%s" % (field.label, _t(value))
    if kind == "sets":
        rows = [
            "%s(kept=%d moved=%d)" % (s["name"], s["kept"], s["moved"])
            for s in value or []
        ]
        return "sets=" + (",".join(rows) if rows else "none")
    if kind == "roles":
        return _counts("role", value)
    if kind == "leads":
        return _counts(field.label, value)
    if kind == "bases":
        return _counts("basis", value)
    if kind == "lanes":
        rows = ["%s/%s=%d" % (x["lane"], x["source"], x["n"]) for x in value or []]
        return "lanes=" + (",".join(rows) if rows else "none")
    raise ValueError("unknown limit field kind")


def _limit_data(lid: str, tree: Dict[str, Any]) -> Dict[str, Any]:
    if lid == "L16":
        leak = tree.get("leak")
        return dict(leak) if leak else {}
    return tree["limits"][lid]


def _limit_lines(tree: Dict[str, Any]) -> List[str]:
    lines: List[str] = []
    for lid in LIMIT_IDS:
        limit = LIMITS[lid]
        data = _limit_data(lid, tree)
        if lid == "L16" and not data:
            computed = "unmeasured reason=leak-check-not-run"
        else:
            computed = " ".join(_field_text(f, data) for f in limit.fields)
        lines.append("%s %s [computed] %s" % (lid, limit.text, computed))
        lines.append("")
    return lines[:-1]


# ---------------------------------------------------------------------------------------------
# The tree (what data.json holds) and the run-id
# ---------------------------------------------------------------------------------------------


def run_id(profile: Dict[str, Any]) -> str:
    """The first 16 hex digits of the sha256 of the pin and the parameters.

    The pin is the snapshot's sha256 and byte count; the parameters are each `params[]`
    name with its value. Nothing else (no result, no check) enters it.
    """
    pin = [profile["input"]["sha256"], profile["input"]["bytes"]]
    params = [[p["name"], p["value"]] for p in profile["params"]]
    blob = json.dumps([pin, params], sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def _stage_pool(tree: Dict[str, Any]) -> List[Dict[str, Any]]:
    wall: Dict[str, float] = {}
    for cycle in tree["cycles"]["list"]:
        for row in cycle["stages"]:
            wall[row["stage"]] = wall.get(row["stage"], 0.0) + row["wall_s"]
    return [{"stage": s, "wall_s": wall[s]} for s in STAGE_BUCKETS if s in wall]


def _post_row(row: Any) -> Dict[str, str]:
    got = row._asdict() if hasattr(row, "_asdict") else dict(row)
    if set(got) != {"name", "verdict", "detail"}:
        raise _closed("post row has unexpected fields")
    if got["name"] not in POST_ROWS or got["verdict"] not in CHECK_VERDICTS:
        raise _closed("post row outside the closed names")
    return {"name": got["name"], "verdict": got["verdict"], "detail": got["detail"]}


def _tree(profile: Dict[str, Any], post: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    tree = copy.deepcopy(profile)
    tree["run_id"] = run_id(profile)
    tree["stage_pool"] = _stage_pool(tree)
    tree["indicators"]["subcheck_band"] = list(SUBCHECK_BAND)
    if post is not None:
        leak = post["leak"]
        if set(leak) != {"samples", "hits", "control", "floor"}:
            raise _closed("leak result has unexpected fields")
        tree["leak"] = {k: leak[k] for k in ("samples", "hits", "control", "floor")}
        tree["post_checks"] = [_post_row(r) for r in post["rows"]]
    return tree


def _dump(tree: Dict[str, Any]) -> bytes:
    body = {k: v for k, v in tree.items() if k != "run_id"}
    text = json.dumps(body, sort_keys=True, indent=1, separators=(",", ": "))
    return ('{"run_id": "%s",\n%s\n' % (tree["run_id"], text[2:])).encode("utf-8")


# ---------------------------------------------------------------------------------------------
# Sections
# ---------------------------------------------------------------------------------------------


def _header(tree: Dict[str, Any]) -> List[str]:
    inp = tree["input"]
    out = [
        "# cycle-profile report run-id=%s" % tree["run_id"],
        "",
        "TOOL version=%s" % tree["tool"]["version"],
        "",
    ]
    for p in tree["params"]:
        out.append(
            "PARAM name=%s value=%s source=%s"
            % (p["name"], _n(p["value"]), p["source"])
        )
    out += [
        "",
        "SNAPSHOT sha256=%s bytes=%s records=%s last_ts=%s pinned=%s"
        % (
            inp["sha256"],
            _i(inp["bytes"]),
            _i(inp["records"]),
            _t(inp["last_ts"]),
            _yn(inp["pinned"]),
        ),
        "READ "
        + _kv(
            parsed=_i(inp["parsed"]),
            unparseable=_i(inp["unparseable"]),
            untimed=_i(inp["untimed"]),
            inversions=_i(inp["inversions"]),
            timestamped_pct=_f(inp["timestamped_pct"]),
        ),
        READ_NOTE,
        "",
    ]
    out += _table(
        ["shape", "count", "class", "needed_by", "gap"],
        [
            [s["name"], _i(s["count"]), s["class"], s["needed_by"], _yn(s["gap"])]
            for s in tree["shapes"]
        ],
    )
    out.append("")
    for f in tree["floors"]:
        out.append(
            "FLOOR %s %s %s %s"
            % (
                f["name"],
                _n(f["want"]),
                _n(f["actual"]),
                "ok" if f["ok"] else "NOT-MET",
            )
        )
    out.append("")
    for v in tree["versions"]:
        counts = " ".join(
            "%s=%s" % (k, _i(v["counts"][k])) for k in sorted(v["counts"])
        )
        gaps = ",".join(v["gaps"]) if v["gaps"] else "none"
        out.append(
            "VERSION %s records=%s timestamped_pct=%s %s gap=%s"
            % (v["version"], _i(v["records"]), _f(v["timestamped_pct"]), counts, gaps)
        )
    return out


def _stage_rows(tree: Dict[str, Any]) -> List[Dict[str, Any]]:
    rank = {name: i for i, name in enumerate(STAGE_BUCKETS)}
    rows = tree["stage_pool"]
    rows = sorted(rows, key=lambda r: (-r["wall_s"], rank[r["stage"]]))
    return rows[:SUMMARY_STAGES]


def _summary(tree: Dict[str, Any]) -> List[str]:
    cyc, pool = tree["cycles"], tree["pool"]
    n = cyc["completed"]
    none = n == 0
    pm = (lambda s: NA) if none else _m
    act, allh = pool["halves"]["active"], pool["halves"]["all"]
    out = [
        "ANSWER basis=ACTIVE half=%s N=%s partial=%s open=%s evaluations=%s"
        % (
            pool["answer"],
            _i(n),
            _i(len(cyc["partial"])),
            _i(len(cyc["open"])),
            _i(pool["distinct_evaluations"]),
        ),
        "SHARE basis=ACTIVE "
        + _kv(
            wall_min=pm(pool["wall_active_s"]),
            work_pct=_f(act["work_pct"]),
            gate_pct=_f(act["gate_pct"]),
            operator_pct=_f(act["operator_pct"]),
            unsplit_pct=_f(act["unsplit_pct"]),
        ),
        "SHARE basis=ALL "
        + _kv(
            wall_min=pm(pool["wall_all_s"]),
            away_pct=_f(allh["away_pct"]),
            work_pct=_f(allh["work_pct"]),
            gate_pct=_f(allh["gate_pct"]),
            operator_pct=_f(allh["operator_pct"]),
            unsplit_pct=_f(allh["unsplit_pct"]),
        ),
    ]
    rows = _stage_rows(tree)
    if not rows:
        out.append("STAGE unmeasured reason=no-completed-cycle")
    for r in rows:
        out.append("STAGE %s wall_min=%s" % (r["stage"], _m(r["wall_s"])))
    out.append(
        "RE-TIER basis=ACTIVE operator_pct=%s gate_pct=%s relation=%s"
        % (
            _f(act["operator_pct"]),
            _f(act["gate_pct"]),
            _relation(act["operator_pct"], act["gate_pct"]),
        )
    )
    out.append(
        "RE-TIER basis=ALL informational operator_pct=%s gate_pct=%s relation=%s"
        % (
            _f(allh["operator_pct"]),
            _f(allh["gate_pct"]),
            _relation(allh["operator_pct"], allh["gate_pct"]),
        )
    )
    dele = tree["delegates"]
    out.append(
        "COVERAGE matched=%s/%s%s"
        % (_i(dele["matched"]), _i(dele["total"]), " weak=yes" if dele["weak"] else "")
    )
    unl = tree["limits"]["L11"]
    if unl.get("s") is None:
        out.append("UNLABELLED-MACHINE-WAIT unmeasured reason=no-per-check-wait")
    else:
        out.append(
            "UNLABELLED-MACHINE-WAIT "
            + _kv(
                min=_m(unl["s"]),
                pct_all=_f(unl["pct_all"]),
                pct_active=_f(unl["pct_active"]),
            )
        )
    for item in tree["not_measured"]:
        if item["what"] == "shape":
            out.append("NOT MEASURED (shape %s absent)" % item["name"])
    return out


def _window_line(label: str, w: Dict[str, Any]) -> str:
    return "%s " % label + _kv(
        start_utc=_t(w["start_utc"]),
        lane=_t(w["lane"]),
        lane_source=_t(w["lane_source"]),
        first_missing_link=_t(w["first_missing_link"]),
        pairing_weak=_yn(w["pairing_weak"]),
        suite_runs=_i(w["suite_runs"]),
    )


def _cycles(tree: Dict[str, Any]) -> List[str]:
    cyc = tree["cycles"]
    out = [
        "CYCLES "
        + _kv(
            completed=_i(cyc["completed"]),
            partial=_i(len(cyc["partial"])),
            open=_i(len(cyc["open"])),
        ),
        "",
    ]
    rows = [
        [
            c["start_utc"],
            _m(c["wall_s"]),
            _t(c["lane"]),
            _t(c["lane_source"]),
            _t(c["end_marker"]),
            _t(c["basis"]),
            _t(c["reconstructable"]),
            _i(c["suite_runs"]),
            _yn(c["pairing_weak"]),
        ]
        for c in cyc["list"]
    ]
    out += _table(
        [
            "start_utc",
            "wall_min",
            "lane",
            "lane_source",
            "end_marker",
            "basis",
            "reconstructable",
            "suite_runs",
            "pairing_weak",
        ],
        rows,
    )
    out.append("")
    for w in cyc["partial"]:
        out.append(_window_line("PARTIAL", w))
    for w in cyc["open"]:
        out.append(
            _window_line("OPEN", w)
            + " minutes_from_last_ts=%s (%s)"
            % (_f(w["minutes_from_last_ts"]), OPEN_PHRASE)
        )
    if cyc["partial"] or cyc["open"]:
        out.append("")
    order = tree["indicators"]["order"]
    if not order:
        out.append("ORDER-INDICATORS unmeasured reason=no-completed-cycle")
    for i, o in enumerate(order):
        out.append(
            "ORDER-INDICATORS "
            + _kv(
                cycle=str(i),
                hidden_agent_min=_m(o["hidden_agent_s"]),
                hidden_union_min=_m(o["hidden_union_s"]),
                stalled_recorded_min=_m(o["stalled_recorded_s"]),
                stalled_any_min=_m(o["stalled_any_s"]),
            )
        )
    return out


def _classes(tree: Dict[str, Any]) -> List[str]:
    cyc, pool = tree["cycles"], tree["pool"]
    none = cyc["completed"] == 0
    pm = (lambda s: NA) if none else _m
    rows = [
        ["cycle %d" % i] + [_m(c["classes_s"][k]) for k in CLASSES]
        for i, c in enumerate(cyc["list"])
    ]
    rows.append(["pooled"] + [pm(pool["classes_s"][k]) for k in CLASSES])
    out = ["Minutes per class.", ""]
    out += _table(["scope"] + list(CLASSES), rows)
    out.append("")
    for i, c in enumerate(cyc["list"]):
        out.append(
            "HIDDEN "
            + _kv(
                cycle=str(i),
                hidden_under_machine_wait_min=_m(c["hidden_under_machine_wait_s"]),
                agent_min=_m(c["agent_seconds"]),
            )
        )
    ind = tree["indicators"]
    band = ind["suite_band_min"]
    suites = ind["suite_mach"]
    if not suites:
        out.append("SUITEMACH unmeasured reason=no-completed-cycle")
    for i, s in enumerate(suites):
        for stage, runs, total, flag in (
            ("feature-tip", s["feature_run_s"], s["feature_s"], s["feature_in_band"]),
            ("dev-tip", s["dev_run_s"], s["dev_s"], s["dev_in_band"]),
        ):
            if not runs:
                out.append("SUITEMACH cycle=%d stage=%s absent runs=0" % (i, stage))
                continue
            out.append(
                "SUITEMACH "
                + _kv(
                    cycle=str(i),
                    stage=stage,
                    runs=str(len(runs)),
                    run_min="/".join(_m(r) for r in runs),
                    total_min=_m(total),
                    band_min="%s-%s" % (_f(band[0]), _f(band[1])),
                    band="in" if flag else "out",
                )
            )
    sub = ind["subcheck"]
    if not sub["n"]:
        out.append("SUBCHECK unmeasured reason=no-enqueue-pair")
    else:
        lo, hi = ind["subcheck_band"]
        out.append(
            "SUBCHECK "
            + _kv(
                n=_i(sub["n"]),
                median=_f(sub["median"]),
                p10=_f(sub["p10"]),
                p90=_f(sub["p90"]),
                band="%s-%s" % (_f(lo), _f(hi)),
                band_flag="in" if sub["in_band"] else "out",
                not_enqueue=_i(sub["not_enqueue"]),
            )
        )
    return out


def _stages(tree: Dict[str, Any]) -> List[str]:
    head = ["stage", "wall_min"] + list(CLASSES)
    out: List[str] = []
    for i, c in enumerate(tree["cycles"]["list"]):
        out += ["Cycle %d, minutes." % i, ""]
        out += _table(
            head,
            [
                [s["stage"], _m(s["wall_s"])] + [_m(s["classes_s"][k]) for k in CLASSES]
                for s in c["stages"]
            ],
        )
        out.append("")
    out += ["Pooled stage wall, minutes.", ""]
    out += _table(
        ["stage", "wall_min"],
        [[r["stage"], _m(r["wall_s"])] for r in tree["stage_pool"]],
    )
    return out


def _away(tree: Dict[str, Any]) -> List[str]:
    idle, fence, pool = tree["idle"], tree["fence"], tree["pool"]
    none = tree["cycles"]["completed"] == 0
    out = [
        "AWAY min=%s wall_active_min=%s"
        % (
            NA if none else _m(pool["away_s"]),
            NA if none else _m(pool["wall_active_s"]),
        ),
        "IDLE "
        + _kv(gaps=_i(idle["gaps"]), q1_min=_m(idle["q1_s"]), q3_min=_m(idle["q3_s"])),
        "FENCE "
        + _kv(
            method=fence["method"],
            k=_n(fence["k"]),
            fitted=_yn(fence["fitted"]),
            inner_min=_m(fence["inner_s"]),
            outer_min=_m(fence["outer_s"]),
            above=_i(fence["above"]),
        ),
    ]
    rob = fence["robust"]
    if rob["base_s"] is None:
        out.append("FENCE-ROBUST unmeasured reason=below-min-gaps")
    else:
        out.append(
            "FENCE-ROBUST "
            + _kv(
                leave_one_out_min_min=_m(fence["leave_one_out_min_s"]),
                leave_one_out_max_min=_m(fence["leave_one_out_max_s"]),
                above_min=_i(rob["above_min"]),
                above_max=_i(rob["above_max"]),
                flips=_i(rob["flips"]),
                omissions=_i(rob["omissions"]),
                smallest_above_pct=_f(rob["smallest_above_pct"]),
            )
        )
    return out


def _delegates(tree: Dict[str, Any]) -> List[str]:
    d = tree["delegates"]
    out = [
        "DELEGATES "
        + _kv(
            matched=_i(d["matched"]),
            total=_i(d["total"]),
            orphans=_i(d["orphans"]),
            files=_i(d["files"]),
        )
    ]
    if d["weak"]:
        out.append("COVERAGE WEAK matched=%s/%s" % (_i(d["matched"]), _i(d["total"])))
    ev = tree["limits"]["L12"]
    if ev.get("exact") is None:
        out.append("CORNER3-EVIDENCE unmeasured reason=no-delegate-file")
    else:
        out.append(
            "CORNER3-EVIDENCE "
            + _kv(
                files=_i(ev["files"]),
                runlong_calls=_i(ev["runlong_calls"]),
                background_calls=_i(ev["background_calls"]),
                exact=_yn(ev["exact"]),
            )
        )
    out.append("")
    out += ["Delegate split per cycle, minutes.", ""]
    out += _table(
        ["scope"] + list(SPLIT_KEYS),
        [
            ["cycle %d" % i] + [_m(c["split_s"].get(k)) for k in SPLIT_KEYS]
            for i, c in enumerate(tree["cycles"]["list"])
        ],
    )
    out.append("")
    out += ["Delegate files read.", ""]
    out += _table(
        ["bytes", "sha256"], [[_i(x["bytes"]), x["sha256"]] for x in d["inventory"]]
    )
    return out


def _pooled(tree: Dict[str, Any]) -> List[str]:
    pool = tree["pool"]
    none = tree["cycles"]["completed"] == 0
    pm = (lambda s: NA) if none else _m
    out = [
        "POOL "
        + _kv(
            n=_i(pool["n"]),
            wall_all_min=pm(pool["wall_all_s"]),
            wall_active_min=pm(pool["wall_active_s"]),
            away_min=pm(pool["away_s"]),
        ),
        "",
        "Halves, percent of the basis wall.",
        "",
    ]
    rows = []
    for name, key in (
        ("ACTIVE", "active"),
        ("ALL", "all"),
        ("ACTIVE-HOOK-WORK", "active_hook_work"),
    ):
        h = pool["halves"][key]
        rows.append(
            [name]
            + [
                _f(h.get(k))
                for k in (
                    "work_pct",
                    "gate_pct",
                    "operator_pct",
                    "unsplit_pct",
                    "away_pct",
                )
            ]
        )
    out += _table(
        ["basis", "work_pct", "gate_pct", "operator_pct", "unsplit_pct", "away_pct"],
        rows,
    )
    out += ["", "Ranking of the time buckets, minutes.", ""]
    out += _table(
        ["name", "kind", "corner", "min"],
        [
            [
                r["name"],
                r["kind"],
                "UNMAPPED" if r["corner"] is None else _i(r["corner"]),
                "unmeasured" if r["s"] is None else pm(r["s"]),
            ]
            for r in pool["ranking"]
        ],
    )
    out.append("")
    for c in pool["corners"]:
        if c["s"] is None:
            out.append("CORNER n=%s unmeasured reason=no-per-check-wait" % _i(c["n"]))
        else:
            out.append("CORNER n=%s min=%s" % (_i(c["n"]), pm(c["s"])))
    out += ["", "Placements of the uncertain buckets, minutes.", ""]
    out += _table(
        [
            "arm",
            "hook",
            "unsplit",
            "work_min",
            "gate_min",
            "operator_min",
            "lead",
            "sub_lead",
        ],
        [
            [
                p["arm"],
                p["hook"],
                p["unsplit"],
                pm(p["work_s"]),
                pm(p["gate_s"]),
                pm(p["operator_s"]),
                p["lead"],
                p["sub_lead"],
            ]
            for p in pool["placements"]
        ],
    )
    out.append("")
    out.append(
        "PLACEMENTS distinct=%s answer=%s"
        % (_i(pool["distinct_evaluations"]), pool["answer"])
    )
    for r in pool["repeat"]:
        head = "REPEAT cycle=%s" % _i(r["cycle"])
        if r["status"] != "measured":
            out.append("%s unmeasured reason=%s" % (head, _t(r["reason"])))
        else:
            out.append(
                head
                + " "
                + _kv(
                    feature_sha=_t(r["feature_sha"]),
                    dev_sha=_t(r["dev_sha"]),
                    tree=_t(r["tree"]),
                    dev_tip_suite_min=_m(r["dev_tip_suite_s"]),
                )
            )
    return out


def _sensitivity(tree: Dict[str, Any]) -> List[str]:
    sens = tree["sensitivity"]
    out = ["Closing-rule sets, minutes per class.", ""]
    out += _table(
        ["set", "rerun_min", "post_min", "min_suite_min", "kept", "moved"]
        + list(CLASSES),
        [
            [
                s["name"],
                _i(s["rerun_min"]),
                _i(s["post_min"]),
                _i(s["min_suite_min"]),
                _i(s["kept"]),
                _i(s["moved"]),
            ]
            + [_m(s["classes_s"][k]) for k in CLASSES]
            for s in sens["sets"]
        ],
    )
    out.append("")
    if not sens["hook"]:
        reason = "no-completed-cycle" if tree["cycles"]["completed"] == 0 else "no-data"
        out.append("SENSITIVITY-HOOK unmeasured reason=%s" % reason)
    for h in sens["hook"]:
        out.append(
            "SENSITIVITY-HOOK "
            + _kv(
                threshold_s=_f(h["threshold_s"]),
                hook_min=_m(h["hook_s"]),
                controller_min=_m(h["controller_s"]),
                primary=_yn(h["primary"]),
            )
        )
    dom = tree["indicators"]["dominant_hook"]
    if dom["status"] != "measured":
        out.append("DOMINANT-CHECK unmeasured reason=no-qualifying-call")
    else:
        out.append(
            "DOMINANT-CHECK "
            + _kv(
                calls=_i(dom["calls"]),
                largest_ms=_i(dom["largest_ms"]),
                union_ms=_i(dom["union_ms"]),
                share_pct=_f(dom["share_pct"]),
            )
        )
    out += ["", "Split priority swap, minutes.", ""]
    pri = sens["priority"]
    out += _table(
        ["part", "primary_min", "swapped_min"],
        [[k, _m(pri["primary"].get(k)), _m(pri["swapped"].get(k))] for k in SPLIT_KEYS],
    )
    out += ["", "Classes without the overlap rule, minutes.", ""]
    out += _table(list(CLASSES), [[_m(sens["no_separation"].get(k)) for k in CLASSES]])
    return out


def _checks(tree: Dict[str, Any]) -> List[str]:
    out = []
    for c in tree["checks"] + tree.get("post_checks", []):
        out.append("CHECK %s %s %s" % (c["name"], c["verdict"], c["detail"]))
    reasons = run.incomplete_reasons(tree)
    out.append("")
    out.append("INCOMPLETE-REASONS " + (" ".join(reasons) if reasons else "none"))
    return out


def _manifest(tree: Dict[str, Any]) -> List[str]:
    m = tree["manifest"]
    out = [
        "MANIFEST "
        + _kv(
            rows=_i(m["rows"]),
            agree=_i(m["agree"]),
            disagree=_i(m["disagree"]),
            unmeasurable=_i(m["unmeasurable"]),
        )
    ]
    for it in m["items"]:
        out.append(
            "ROW %s unit=%s recorded=%s derived=%s delta=%s tolerance=%s %s%s"
            % (
                it["selector"],
                it["unit"],
                _n(it["recorded"]),
                _n(it["derived"]),
                _n(it["delta"]),
                _n(it["tolerance"]),
                it["verdict"],
                " gate" if it["gate"] else "",
            )
        )
    out.append(
        "INDEPENDENCE "
        + _kv(
            independent=_i(m["independent"]),
            guaranteed=_i(m["guaranteed"]),
            unlabelled=_i(m["unlabelled"]),
        )
    )
    out.append(
        "FLOOR-NOTE "
        + _kv(floor_decided=_i(m["floor_decided"]), measurable=_i(m["measurable"]))
    )
    big = m["largest_delta"]
    if big is None:
        out.append("LARGEST-DELTA none")
    else:
        out.append(
            "LARGEST-DELTA %s delta=%s unit=%s"
            % (big["selector"], _n(big["delta"]), big["unit"])
        )
    return out


def _not_measured(tree: Dict[str, Any]) -> List[str]:
    out = []
    for item in tree["not_measured"]:
        if item["what"] == "shape":
            out.append("NOT MEASURED (shape %s absent)" % item["name"])
        else:
            out.append(
                "NOT MEASURED "
                + _kv(
                    what=item["what"],
                    name=_t(item["name"]),
                    reason=item["reason"],
                    needed_by=_t(item["needed_by"]),
                )
            )
    return out or ["NOT MEASURED none"]


def _leak(tree: Dict[str, Any]) -> List[str]:
    leak = tree["leak"]
    return [
        "LEAKCHECK samples=%s hits=%s control=%s floor=%s"
        % (_i(leak["samples"]), _i(leak["hits"]), leak["control"], _i(leak["floor"]))
    ]


def _report(tree: Dict[str, Any], post: bool) -> str:
    sections: List[Tuple[str, Callable[[Dict[str, Any]], List[str]]]] = [
        ("LIMITS (standing)", _limit_lines),
        ("Summary", _summary),
        ("Cycles", _cycles),
        ("Classes", _classes),
        ("Stages", _stages),
        ("AWAY and the fence", _away),
        ("Delegates", _delegates),
        ("Pooled ranking, halves, placements, corners", _pooled),
        ("Sensitivity", _sensitivity),
        ("Checks", _checks),
    ]
    if tree["manifest"] is not None:
        sections.append(("Manifest", _manifest))
    sections.append(("NOT MEASURED", _not_measured))
    if post:
        sections.append(("Leak check", _leak))
    lines = _header(tree)
    for title, build in sections:
        lines += ["", "## " + title, ""]
        lines += build(tree)
    return "\n".join(lines).rstrip("\n") + "\n"


# ---------------------------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------------------------


def render(
    profile: Dict[str, Any], post: Optional[Dict[str, Any]] = None
) -> Tuple[bytes, bytes]:
    """Render `(report_md, data_json)` from one profile.

    Args:
        profile: A `cycle_profile_run.run_profile` result (not modified).
        post: `None` for the first pass. For the second, `{"leak": {samples, hits, control,
            floor}, "rows": [the REMEDYCHECK and LEAKCHECK rows]}`: closed tokens and
            integers only; it adds the leak section, the two rows and `leak` in `data.json`.

    Returns:
        The two files as bytes: UTF-8, LF, one trailing newline; the same profile always
        gives the same bytes.

    Raises:
        privacy.PrivacyRefusal: A key or string outside the allowed channels.
    """
    tree = _tree(profile, post)
    _validate(tree)
    return _report(tree, post is not None).encode("utf-8"), _dump(tree)


def limits_block(profile: Dict[str, Any], post: Optional[Dict[str, Any]] = None) -> str:
    """The 18 limit lines (the LIMITS block of the report), for stdout before any figure.

    Args:
        profile: A `cycle_profile_run.run_profile` result (not modified).
        post: The same `post` that `render` took for the second pass. Without it the L16
            line says `unmeasured`, as in the first render pass.

    Raises:
        privacy.PrivacyRefusal: A key or string outside the allowed channels.
    """
    tree = _tree(profile, post)
    _validate(tree)
    return "\n".join(_limit_lines(tree))


def stdout_summary(profile: Dict[str, Any]) -> str:
    """The stdout summary: the report's Summary lines (well under 40).

    Raises:
        privacy.PrivacyRefusal: A key or string outside the allowed channels.
    """
    tree = _tree(profile, None)
    _validate(tree)
    return "\n".join(_summary(tree))


def _count(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise _closed("count is not a non-negative integer")
    return value


def result_line(
    verdict: str,
    *,
    cycles: int,
    dispatches: int,
    matched: int,
    passed: int,
    failed: int,
    skipped: int,
    samples: int,
    hits: int,
    answer: str,
) -> str:
    """The one `RESULT:` line of spec D1: closed tokens and integers only.

    Raises:
        privacy.PrivacyRefusal: `verdict` or `answer` is outside its closed set or a count
            is not a non-negative integer.
    """
    if verdict not in VERDICTS:
        raise _closed("verdict outside the closed set")
    if answer not in ANSWERS:
        raise _closed("answer outside the closed set")
    return (
        "RESULT: %s rc=%d cycles=%d dispatches=%d split=%d/%d checks=%d/%d/%d "
        "leak=%d/%d answer=%s"
        % (
            verdict,
            RC[verdict],
            _count(cycles),
            _count(dispatches),
            _count(matched),
            _count(dispatches),
            _count(passed),
            _count(failed),
            _count(skipped),
            _count(samples),
            _count(hits),
            answer,
        )
    )


__all__ = [
    "DATA_KEYS",
    "LIMITS",
    "Limit",
    "Field",
    "OPEN_PHRASE",
    "REPORT_VOCAB",
    "SECTIONS",
    "limits_block",
    "render",
    "result_line",
    "run_id",
    "stdout_summary",
]

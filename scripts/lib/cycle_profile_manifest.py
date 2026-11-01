"""Optional recorded-figure rows for the cycle-profile report (`--manifest FILE`, D2.4).

A manifest is a JSON list of rows `{name, recorded, selector, unit, tolerance?,
independence?, gate?, kind?}`. Each row names a figure somebody recorded elsewhere and a
`selector` from a CLOSED grammar saying which figure of the profile to compare it with; the
tool reports AGREE, DISAGREE or unmeasurable per row. Nothing session-specific is built in:
every recorded figure comes from the manifest file.

**Closed grammar.** A selector must fullmatch one alternative of `SELECTOR_ALTERNATIVES`;
the operator parts are restricted to the closed vocabularies of `cycle_profile_model` (time
classes, stages) and to `corner.[1-7]`, and the per-cycle prefix to an ASCII UTC prefix. A
row that fails validation raises `ManifestError`, which carries the row INDEX only, never any
text of the row, so a manifest is not a path for a transcript fragment into an error line.
`name` is an unrendered comment: no function here returns or formats it.

**Units.** Each selector has a native unit (`min`, `s`, `pct`, `count` or `token`). The row's
`unit` must be compatible (`min` and `s` convert, `pct` and `pp` are the same, `count` and
`token` stand alone); `evaluate_manifest` converts the derived figure into the row's unit, and
`compare` takes a derived figure that is ALREADY in the row's unit.

**Tolerance.** A row agrees when `abs(derived - recorded) <= tolerance + SLACK`, where
`SLACK` (1e-9 in the row's unit) absorbs float noise so a delta exactly at the tolerance
agrees. The default is
`max(2 min, 10% of recorded)`, with the 2 min floor read as 2 in the row's own unit family
(120 s, 2 pct or pp, 2 counts). A row's own `tolerance` (absolute, in the row's unit) replaces
it. Token rows (`answer`) compare for equality and take no tolerance.

**Unit table.** `min` and `s` selectors accept row units `min` or `s`; `pct` selectors
accept `pct` or `pp`; `count` selectors accept `count`; the `answer` selector accepts
`token`. Any other pairing is invalid.

**Unknown keys are rejected:** a row holding any key outside the closed set (a `comment`
key, say) is invalid, so a manifest is not a path for free text.

**FLOOR-NOTE.** A numeric row without its own tolerance counts when 10% of its recorded
figure is at or under the floor (the original's reading; the spec wording is ambiguous).

**Clock rows** (`kind: clock`) carry `cycle` (a UTC prefix naming one cycle), `local_start`
(and optionally `local_end`) as `YYYY-MM-DDTHH:MM:SS` local readings, and `recorded`, the
whole-hour zone offset (UTC minus local). The cycle's `start_utc` (and `start_utc + wall_s`)
supply the transcript side. The offset is measured to whole hours with a residual allowed up
to 120 s, the original ZONE check rounded to whole hours; `zone_check` also fails when the
offsets of all clock rows spread by more than 120 s.

Profile layout v1 is documented in `cycle_profile_model`; a profile that is missing a key or
holds a wrong type makes the row unmeasurable, never an error.
"""

from __future__ import annotations

import datetime
import math
import re
from typing import Any, Dict, List, Optional, Tuple

from cycle_profile_model import CLASSES, STAGES, CheckRow

UNITS: Tuple[str, ...] = ("min", "s", "pct", "pp", "count", "token")
KINDS: Tuple[str, ...] = ("clock",)
INDEPENDENCE: Tuple[str, ...] = ("independent", "guaranteed")
ANSWERS: Tuple[str, ...] = ("gate", "work", "undetermined")

#: The 2 min tolerance floor, expressed in each unit.
FLOOR_IN_UNIT: Dict[str, float] = {
    "min": 2.0,
    "s": 120.0,
    "pct": 2.0,
    "pp": 2.0,
    "count": 2.0,
}
REL_TOL = 0.10
#: Absolute slack added to the tolerance, in the row's own unit. A derived figure that is
#: converted (seconds divided by 60, a share computed from seconds) carries float noise of
#: about 1e-13 at the magnitudes a profile holds, so a delta that is exactly the tolerance
#: can read a few ulps above it; 1e-9 absorbs that (the original used the same value) while
#: being 1e-9 min (60 ns) or 1e-9 pct, far below any difference a recorded figure can carry.
SLACK = 1e-9
ZONE_LIMIT_S = 120.0

_PREFIX = r"[0-9]{4}-[0-9]{2}-[0-9]{2}(?:T[0-9]{2}(?::[0-9]{2}(?::[0-9]{2})?)?)?"
_CLASS = "|".join(CLASSES)
_STAGE = "|".join(re.escape(s) for s in STAGES)

#: (kind, native unit, pattern): the whole grammar; `fullmatch` only.
SELECTOR_ALTERNATIVES: Tuple[Tuple[str, str, "re.Pattern[str]"], ...] = tuple(
    (kind, unit, re.compile(pat, re.ASCII))
    for kind, unit, pat in (
        ("cycles.completed", "count", r"cycles\.completed"),
        ("wall", "min", r"wall\.(?P<which>all|active)_min"),
        ("away", "min", r"away\.min"),
        ("class", "min", r"class\.(?P<cls>%s)\.min" % _CLASS),
        (
            "half",
            "pct",
            r"half\.active\.(?P<which>work|gate|operator)_pct",
        ),
        ("answer", "token", r"answer"),
        ("fence", "s", r"fence\.inner_s"),
        ("idle", "count", r"idle\.gaps"),
        ("coverage", "count", r"coverage\.matched"),
        ("corner", "min", r"corner\.(?P<n>[1-7])\.min"),
        ("cycle.wall", "min", r"cycle\[(?P<pfx>%s)\]\.wall_min" % _PREFIX),
        ("cycle.suites", "count", r"cycle\[(?P<pfx>%s)\]\.suite_runs" % _PREFIX),
        (
            "cycle.stage",
            "min",
            r"cycle\[(?P<pfx>%s)\]\.stage\.(?P<stage>%s)\.(?P<cls>%s)\.min"
            % (_PREFIX, _STAGE, _CLASS),
        ),
    )
)
_PREFIX_RE = re.compile(_PREFIX, re.ASCII)
_LOCAL_RE = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}", re.ASCII
)

_COMPATIBLE: Dict[str, Tuple[str, ...]] = {
    "min": ("min", "s"),
    "s": ("s", "min"),
    "pct": ("pct", "pp"),
    "count": ("count",),
    "token": ("token",),
}
_ROW_KEYS = frozenset(
    {
        "name",
        "recorded",
        "selector",
        "unit",
        "tolerance",
        "independence",
        "gate",
        "kind",
    }
)
_CLOCK_KEYS = frozenset(
    {
        "name",
        "kind",
        "cycle",
        "recorded",
        "local_start",
        "local_end",
        "independence",
        "gate",
    }
)


class ManifestError(Exception):
    """A manifest row is invalid. Carries the row `index` only, never row text."""

    def __init__(self, index: int) -> None:
        super().__init__("manifest row %d is invalid" % index)
        self.index = index


# ---------------------------------------------------------------------------------------------
# Small readers
# ---------------------------------------------------------------------------------------------


def _num(value: Any) -> Optional[float]:
    """A finite real number (not a bool), else `None`."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        if not math.isfinite(float(value)):
            return None
    except OverflowError:  # an int too large for a float
        return None
    return value


def _match(selector: Any) -> Optional[Tuple[str, str, "re.Match[str]"]]:
    """The grammar alternative a selector fullmatches, or `None`."""
    if not isinstance(selector, str):
        return None
    for kind, unit, pat in SELECTOR_ALTERNATIVES:
        got = pat.fullmatch(selector)
        if got is not None:
            return kind, unit, got
    return None


def _parse_local(text: str) -> Optional[datetime.datetime]:
    try:
        return datetime.datetime.strptime(text, "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return None


def _parse_utc(text: Any) -> Optional[datetime.datetime]:
    """Parse a profile `start_utc` (`...Z`, optional fraction) into a naive UTC time."""
    if not isinstance(text, str) or not text.endswith("Z"):
        return None
    body, _dot, frac = text[:-1].partition(".")
    if frac and not (frac.isascii() and frac.isdigit()):
        return None
    try:
        when = datetime.datetime.strptime(body, "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return None
    if frac:
        when += datetime.timedelta(microseconds=int((frac + "000000")[:6]))
    return when


def _bad(index: int) -> "ManifestError":
    return ManifestError(index)


# ---------------------------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------------------------


def _validate_common(row: Dict[str, Any], index: int) -> Dict[str, Any]:
    """Validate the keys both row shapes share; return their normalized values."""
    if "name" in row and not isinstance(row["name"], str):
        raise _bad(index)
    indep = row.get("independence")
    if indep is not None and indep not in INDEPENDENCE:
        raise _bad(index)
    gate = row.get("gate", False)
    if not isinstance(gate, bool):
        raise _bad(index)
    return {"independence": indep, "gate": gate}


def _validate_clock(row: Dict[str, Any], index: int) -> Dict[str, Any]:
    if not set(row) <= _CLOCK_KEYS or "recorded" not in row:
        raise _bad(index)
    common = _validate_common(row, index)
    cycle = row.get("cycle")
    if not isinstance(cycle, str) or _PREFIX_RE.fullmatch(cycle) is None:
        raise _bad(index)
    local_start = row.get("local_start")
    if not isinstance(local_start, str) or _LOCAL_RE.fullmatch(local_start) is None:
        raise _bad(index)
    if _parse_local(local_start) is None:
        raise _bad(index)
    local_end = row.get("local_end")
    if local_end is not None:
        ok = isinstance(local_end, str) and _LOCAL_RE.fullmatch(local_end)
        if not ok or _parse_local(local_end) is None:
            raise _bad(index)
    recorded = _num(row["recorded"])
    if recorded is None or recorded != int(recorded) or abs(recorded) > 14:
        raise _bad(index)
    out = {
        "name": row.get("name"),
        "kind": "clock",
        "cycle": cycle,
        "local_start": local_start,
        "local_end": local_end,
        "recorded": int(recorded),
        "selector": "cycle[%s].clock" % cycle,
        "unit": "count",
        "tolerance": None,
    }
    out.update(common)
    return out


def _validate_one(row: Any, index: int) -> Dict[str, Any]:
    if not isinstance(row, dict) or not all(isinstance(k, str) for k in row):
        raise _bad(index)
    if "kind" in row:
        if row["kind"] not in KINDS:
            raise _bad(index)
        return _validate_clock(row, index)
    if not set(row) <= _ROW_KEYS or not {"recorded", "selector", "unit"} <= set(row):
        raise _bad(index)
    common = _validate_common(row, index)
    found = _match(row["selector"])
    if found is None:
        raise _bad(index)
    _kind, native, _got = found
    unit = row["unit"]
    if not isinstance(unit, str) or unit not in _COMPATIBLE[native]:
        raise _bad(index)
    recorded = row["recorded"]
    if native == "token":
        if not isinstance(recorded, str) or recorded not in ANSWERS:
            raise _bad(index)
    elif _num(recorded) is None:
        raise _bad(index)
    tolerance = row.get("tolerance")
    if tolerance is not None:
        tol = _num(tolerance)
        if native == "token" or tol is None or tol < 0:
            raise _bad(index)
    out = {
        "name": row.get("name"),
        "kind": None,
        "selector": row["selector"],
        "unit": unit,
        "recorded": recorded,
        "tolerance": tolerance,
    }
    out.update(common)
    return out


def validate_rows(rows: Any) -> List[Dict[str, Any]]:
    """Validate every row of a manifest before anything else reads it.

    Args:
        rows: The parsed manifest (a list of row dicts).

    Returns:
        Normalized copies of the rows, in order.

    Raises:
        ManifestError: A row is ungrammatical; `index` names the first one (`-1` when the
            manifest is not a list). The error carries no row text.
    """
    if not isinstance(rows, list):
        raise ManifestError(-1)
    return [_validate_one(row, i) for i, row in enumerate(rows)]


# ---------------------------------------------------------------------------------------------
# Resolution against the profile
# ---------------------------------------------------------------------------------------------


def _get(obj: Any, *path: Any) -> Any:
    """Walk dict keys; any miss or wrong type is `None`."""
    for key in path:
        if not isinstance(obj, dict) or key not in obj:
            return None
        obj = obj[key]
    return obj


def _count(value: Any) -> Optional[float]:
    """A count that the profile holds as an integer or as a list."""
    if isinstance(value, list):
        return len(value)
    return _num(value)


def _scaled(value: Any, divisor: float) -> Optional[float]:
    num = _num(value)
    return None if num is None else num / divisor


def _find_cycle(profile: Any, prefix: str) -> Optional[Dict[str, Any]]:
    """The single `cycles.list` entry whose `start_utc` starts with `prefix`."""
    entries = _get(profile, "cycles", "list")
    if not isinstance(entries, list):
        return None
    hits = [
        e
        for e in entries
        if isinstance(e, dict)
        and isinstance(e.get("start_utc"), str)
        and e["start_utc"].startswith(prefix)
    ]
    return hits[0] if len(hits) == 1 else None


def resolve(profile: Any, selector: str) -> Any:
    """Resolve a selector against a profile; the value is in the selector's native unit.

    Returns:
        A number, or one of the `answer` tokens, or `None` when the selector does not
        resolve (a missing key, a wrong type, or a per-cycle prefix matching no cycle or
        several). `None` means unmeasurable and is never an agreement.
    """
    found = _match(selector)
    if found is None:
        return None
    kind, _unit, got = found
    if kind == "cycles.completed":
        return _count(_get(profile, "cycles", "completed"))
    if kind == "wall":
        return _scaled(_get(profile, "pool", "wall_%s_s" % got.group("which")), 60.0)
    if kind == "away":
        return _scaled(_get(profile, "pool", "away_s"), 60.0)
    if kind == "class":
        return _scaled(_get(profile, "pool", "classes_s", got.group("cls")), 60.0)
    if kind == "half":
        return _num(
            _get(profile, "pool", "halves", "active", got.group("which") + "_pct")
        )
    if kind == "answer":
        value = _get(profile, "pool", "answer")
        return value if isinstance(value, str) and value in ANSWERS else None
    if kind == "fence":
        return _num(_get(profile, "fence", "inner_s"))
    if kind == "idle":
        return _count(_get(profile, "idle", "gaps"))
    if kind == "coverage":
        return _num(_get(profile, "delegates", "matched"))
    if kind == "corner":
        corners = _get(profile, "pool", "corners")
        if not isinstance(corners, list):
            return None
        want = int(got.group("n"))
        hits = [c for c in corners if isinstance(c, dict) and c.get("n") == want]
        return _scaled(hits[0].get("s"), 60.0) if len(hits) == 1 else None
    entry = _find_cycle(profile, got.group("pfx"))
    if entry is None:
        return None
    if kind == "cycle.wall":
        return _scaled(entry.get("wall_s"), 60.0)
    if kind == "cycle.suites":
        return _num(entry.get("suite_runs"))
    stages = entry.get("stages")
    if not isinstance(stages, list):
        return None
    rows = [
        s
        for s in stages
        if isinstance(s, dict) and s.get("stage") == got.group("stage")
    ]
    if len(rows) != 1:
        return None
    return _scaled(_get(rows[0], "classes_s", got.group("cls")), 60.0)


# ---------------------------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------------------------


def _convert(value: Any, native: str, unit: str) -> Any:
    """Convert a derived number from the selector's native unit into the row's unit."""
    num = _num(value)
    if num is None or native == unit:
        return value
    if native == "min" and unit == "s":
        return num * 60.0
    if native == "s" and unit == "min":
        return num / 60.0
    return value


def tolerance_of(row: Dict[str, Any]) -> Optional[float]:
    """The absolute tolerance of a numeric row: its own, else the default rule."""
    own = row.get("tolerance")
    if own is not None:
        return float(own)
    recorded = _num(row.get("recorded"))
    if recorded is None:
        return None
    floor = FLOOR_IN_UNIT.get(row.get("unit", ""))
    if floor is None:
        return None
    return max(floor, REL_TOL * abs(recorded))


def compare(row: Dict[str, Any], derived: Any) -> str:
    """Compare one validated row with its derived figure (already in the row's unit).

    Returns:
        `AGREE`, `DISAGREE` or `unmeasurable`; an absent, non-finite, boolean or
        wrong-typed derived figure is `unmeasurable`, never `AGREE`.
    """
    if row.get("unit") == "token":
        if not isinstance(derived, str) or derived not in ANSWERS:
            return "unmeasurable"
        return "AGREE" if derived == row["recorded"] else "DISAGREE"
    got = _num(derived)
    if got is None:
        return "unmeasurable"
    tol = tolerance_of(row)
    if tol is None:
        return "unmeasurable"
    return "AGREE" if abs(got - row["recorded"]) <= tol + SLACK else "DISAGREE"


def _clock_offsets(row: Dict[str, Any], profile: Any) -> Optional[List[float]]:
    """The measured UTC-minus-local offsets, in seconds, of a clock row's readings."""
    entry = _find_cycle(profile, row["cycle"])
    if entry is None:
        return None
    start = _parse_utc(entry.get("start_utc"))
    local_start = _parse_local(row["local_start"])
    if start is None or local_start is None:
        return None
    out = [(start - local_start).total_seconds()]
    if row["local_end"] is not None:
        wall = _num(entry.get("wall_s"))
        local_end = _parse_local(row["local_end"])
        if wall is None or local_end is None:
            return None
        out.append(
            (start + datetime.timedelta(seconds=wall) - local_end).total_seconds()
        )
    return out


def _residual(offset_s: float) -> Tuple[int, float]:
    hours = int(round(offset_s / 3600.0))
    return hours, abs(offset_s - 3600.0 * hours)


def _evaluate_clock(
    index: int, row: Dict[str, Any], profile: Any
) -> Tuple[Dict[str, Any], Optional[List[float]]]:
    offsets = _clock_offsets(row, profile)
    item: Dict[str, Any] = {
        "index": index,
        "selector": row["selector"],
        "unit": "hours",
        "recorded": row["recorded"],
        "derived": None,
        "delta": None,
        "tolerance": None,
        "gate": row["gate"],
        "independence": row["independence"],
        "own_tolerance": False,
        "clock": True,
        "verdict": "unmeasurable",
    }
    if offsets is None:
        return item, None
    split = [_residual(o) for o in offsets]
    hours = {h for h, _r in split}
    resid = max(r for _h, r in split)
    item["derived"] = split[0][0] if len(hours) == 1 else None
    item["tolerance"] = ZONE_LIMIT_S
    item["delta"] = resid
    good = len(hours) == 1 and hours == {row["recorded"]} and resid <= ZONE_LIMIT_S
    item["verdict"] = "AGREE" if good else "DISAGREE"
    return item, offsets


def evaluate_manifest(profile: Any, rows: Any) -> Dict[str, Any]:
    """Validate the rows, resolve each against `profile` and compare.

    Returns:
        A summary dict holding only selectors (validated), numbers and tokens: `rows`,
        `agree`, `disagree`, `unmeasurable`, `independent`, `guaranteed`, `unlabelled`,
        `measurable`, `floor_decided`, `largest_delta`, `gate_failures`, `zone` and the
        per-row `items`.

    Raises:
        ManifestError: A row is invalid (raised before the profile is read).
    """
    valid = validate_rows(rows)
    items: List[Dict[str, Any]] = []
    offsets_all: List[float] = []
    for index, row in enumerate(valid):
        if row["kind"] == "clock":
            item, offsets = _evaluate_clock(index, row, profile)
            items.append(item)
            if offsets is not None:
                offsets_all.extend(offsets)
            continue
        found = _match(row["selector"])
        native = found[1] if found else ""
        raw = resolve(profile, row["selector"])
        derived = _convert(raw, native, row["unit"])
        verdict = compare(row, derived)
        number = verdict != "unmeasurable" and row["unit"] != "token"
        tol = tolerance_of(row) if row["unit"] != "token" else None
        items.append(
            {
                "index": index,
                "selector": row["selector"],
                "unit": row["unit"],
                "recorded": row["recorded"],
                "derived": derived if verdict != "unmeasurable" else None,
                "delta": (derived - row["recorded"]) if number else None,
                "tolerance": tol,
                "gate": row["gate"],
                "independence": row["independence"],
                "own_tolerance": row["tolerance"] is not None,
                "clock": False,
                "verdict": verdict,
            }
        )
    return _summary(items, offsets_all)


def _summary(items: List[Dict[str, Any]], offsets_all: List[float]) -> Dict[str, Any]:
    count = {v: sum(1 for i in items if i["verdict"] == v) for v in _VERDICTS}
    numeric = [i for i in items if not i["clock"] and i["delta"] is not None]
    floor = [
        i
        for i in numeric
        if not i["own_tolerance"]
        and REL_TOL * abs(i["recorded"]) <= FLOOR_IN_UNIT.get(i["unit"], -1.0)
    ]
    largest = None
    if numeric:
        top = max(numeric, key=lambda i: abs(i["delta"]))
        largest = {
            "selector": top["selector"],
            "delta": top["delta"],
            "unit": top["unit"],
        }
    zone_items = [i for i in items if i["clock"]]
    spread = (max(offsets_all) - min(offsets_all)) if offsets_all else None
    return {
        "rows": len(items),
        "agree": count["AGREE"],
        "disagree": count["DISAGREE"],
        "unmeasurable": count["unmeasurable"],
        "independent": sum(1 for i in items if i["independence"] == "independent"),
        "guaranteed": sum(1 for i in items if i["independence"] == "guaranteed"),
        "unlabelled": sum(1 for i in items if i["independence"] is None),
        "measurable": len(numeric),
        "floor_decided": len(floor),
        "largest_delta": largest,
        "gate_failures": sum(1 for i in items if i["gate"] and i["verdict"] != "AGREE"),
        "zone": {
            "rows": len(zone_items),
            "measured": sum(1 for i in zone_items if i["verdict"] != "unmeasurable"),
            "bad": sum(1 for i in zone_items if i["verdict"] == "DISAGREE"),
            "hours": sorted({int(round(o / 3600.0)) for o in offsets_all}),
            "spread_s": spread,
        },
        "items": items,
    }


_VERDICTS = ("AGREE", "DISAGREE", "unmeasurable")


# ---------------------------------------------------------------------------------------------
# Rendering helpers and check rows
# ---------------------------------------------------------------------------------------------


def summarize(result: Dict[str, Any]) -> str:
    """The bound `MANIFEST rows=<n> agree=<a> disagree=<d> unmeasurable=<u>` line."""
    return "MANIFEST rows=%d agree=%d disagree=%d unmeasurable=%d" % (
        result["rows"],
        result["agree"],
        result["disagree"],
        result["unmeasurable"],
    )


def _fmt(value: Any) -> str:
    if value is None:
        return "none"
    if isinstance(value, str):
        return value
    return "%.2f" % value


def detail_lines(result: Dict[str, Any]) -> List[str]:
    """Per-row lines (by selector, never by name), then the counts and the notes."""
    lines = []
    for i in result["items"]:
        lines.append(
            "ROW %s unit=%s recorded=%s derived=%s delta=%s tolerance=%s %s%s"
            % (
                i["selector"],
                i["unit"],
                _fmt(i["recorded"]),
                _fmt(i["derived"]),
                _fmt(i["delta"]),
                _fmt(i["tolerance"]),
                i["verdict"],
                " gate" if i["gate"] else "",
            )
        )
    lines.append(
        "INDEPENDENCE independent=%d guaranteed=%d unlabelled=%d"
        % (result["independent"], result["guaranteed"], result["unlabelled"])
    )
    lines.append(
        "FLOOR-NOTE %d of %d measurable rows have a recorded figure at or under the "
        "tolerance floor, so their tolerance is the floor and an AGREE carries little "
        "evidence" % (result["floor_decided"], result["measurable"])
    )
    big = result["largest_delta"]
    if big is None:
        lines.append("LARGEST-DELTA none")
    else:
        lines.append(
            "LARGEST-DELTA %s delta=%s %s"
            % (big["selector"], _fmt(big["delta"]), big["unit"])
        )
    return lines


def manifest_check(result: Optional[Dict[str, Any]]) -> CheckRow:
    """The `MANIFEST` check row: a `gate: true` row that is not AGREE fails it."""
    if result is None:
        return CheckRow("MANIFEST", "skip", "no manifest given")
    detail = "%s gate_failures=%d" % (summarize(result), result["gate_failures"])
    return CheckRow("MANIFEST", "fail" if result["gate_failures"] else "pass", detail)


def zone_check(result: Optional[Dict[str, Any]]) -> CheckRow:
    """The `ZONE` check row over the `kind: clock` rows; no clock rows is a `skip`."""
    if result is None or result["zone"]["rows"] == 0:
        return CheckRow("ZONE", "skip", "no clock rows")
    zone = result["zone"]
    detail = "clock_rows=%d measured=%d bad=%d" % (
        zone["rows"],
        zone["measured"],
        zone["bad"],
    )
    if zone["measured"] == 0:
        return CheckRow("ZONE", "skip", detail + " unmeasurable")
    spread = zone["spread_s"]
    detail += " hours=%s spread_s=%.1f limit_s=%d" % (
        "/".join(str(h) for h in zone["hours"]),
        spread,
        int(ZONE_LIMIT_S),
    )
    bad = zone["bad"] > 0 or len(zone["hours"]) != 1 or spread > ZONE_LIMIT_S
    unmeasured = zone["rows"] - zone["measured"]
    if not bad and unmeasured:
        return CheckRow("ZONE", "skip", detail + " unmeasurable=%d" % unmeasured)
    return CheckRow("ZONE", "fail" if bad else "pass", detail)

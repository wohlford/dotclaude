"""Privacy controls for the cycle-profile tooling: where output may go, what it may hold.

Five jobs, all fail-closed, none of which ever puts a path or any input text in a message:

* **The out-dir guard** (`check_out_dir`, `prepare_out_dir`): refuses an output location inside
  any git repository (gitignored or not), inside the transcript's directory tree or the
  delegate directory, containing the transcript, or non-empty without the marker file. The
  repository test combines `git -C <nearest existing ancestor> rev-parse --git-dir` (exit 0
  refuses; exit 128 means "no repository found" and still needs the walk's agreement; any
  other outcome refuses as could-not-decide) with a filesystem walk of the resolved ancestors
  for a `.git` entry or a `HEAD`+`objects`+`refs` triple. Both sides of every path comparison
  go through `os.path.realpath`. `Refusal.reason` is one of `REFUSAL_REASONS`.
* **The one writer** (`write_output`): the tool writes exactly `report.md`, `data.json` and
  `.cycle-profile-out`, the first two through fixed temp names renamed into place, mode 0600.
  `outscope_listing` reports any other entry in the out-dir by count only.
* **The output allowlist** (`validate_tree`, `safe_*`): a string may enter `data.json` only
  through a closed channel (a closed vocabulary, a UTC timestamp, a hex digest, a transcript
  stem, or an opaque id under an id-bearing key); every key must be a source constant.
* **The leak check** (`sample_candidates`, `leak_check`, `leak_verdict`): windows of free text
  are sampled from the pinned bytes and searched for in the exact bytes about to be written,
  raw and JSON-escaped, with a planted control that proves the matcher and a floor on the
  number of samples. It samples; it does not prove absence.
* **The remedy-word finder** (`find_remedy_words`): the mechanical half of the
  measurement-only rule.

Known limits, inherent to the channel table (they belong in the tool's limits): the hex
channel and the closed vocabularies can pass a real word that happens to spell hex digits
(`defaced`) or equal a vocabulary token; `prepare_out_dir` creates missing PARENT directories
with the default mode and only the leaf is 0700; the remedy words match whole words only, so
inflections such as `recommended` are not matched (the spec's table lists base forms).

Containment is decided by filesystem identity (`st_dev`, `st_ino`) of the nearest existing
ancestor chain, with the resolved-string comparison kept as a fast path, because a
case-insensitive volume reads `IN` and `in` as one directory while `realpath` keeps both
spellings. The non-existent tail of a not-yet-created out-dir cannot be a protected directory
(a protected directory exists), so only the chain from its nearest existing ancestor is compared.

Not exercised: a real SIGKILL between the two renames of `write_output`. The re-run into a
marker-bearing directory cleans the two temp names (`prepare_out_dir`) and flags anything else.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import random
import re
import subprocess
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
    Sequence,
    Tuple,
)

from cycle_profile_model import (
    CHECK_CLASSES,
    CHECK_VERDICTS,
    CLASSES,
    ID_RE,
    LIMIT_IDS,
    MISSING_LINKS,
    ROLES,
    SHAPES,
    STAGE_BUCKETS,
    VERDICTS,
    CheckRow,
    LeakResult,
)
from cycle_profile_records import PinError, ReadError

# ---------------------------------------------------------------------------------------------
# Closed names
# ---------------------------------------------------------------------------------------------

#: The closed set of `Refusal.reason` values.
REFUSAL_REASONS = (
    "inside-work-tree",
    "could-not-decide",
    "inside-transcript-tree",
    "contains-transcript",
    "non-empty-no-marker",
)

MARKER = ".cycle-profile-out"
REPORT = "report.md"
DATA = "data.json"
#: The only names `write_output` accepts, and the only entries an out-dir may hold afterwards.
OUTPUT_NAMES = (REPORT, DATA, MARKER)
#: Fixed temp names for the two renamed outputs.
TEMP_NAMES = {REPORT: ".report.md.tmp", DATA: ".data.json.tmp"}

DIR_MODE = 0o700
FILE_MODE = 0o600
GIT_NOT_A_REPO = 128
#: Seconds the one `git` call may take; a timeout refuses as could-not-decide.
GIT_TIMEOUT_S = 30

WINDOW_CHARS = 40
MIN_CANDIDATE_CHARS = 60
MIN_CANDIDATE_SPACES = 3
#: A window must hold at least this many non-space characters of its 40. Below half, a window
#: is mostly padding (table rules, indentation) that the rendered report legitimately repeats,
#: so it would give a false hit and carry too little text to be a meaningful probe.
MIN_WINDOW_NONSPACE = 20
#: Seeded offsets tried per candidate before it is skipped (bounds the cost on long strings).
WINDOW_ATTEMPTS = 16
#: At most this many delegate files feed the sampler (the CLI enforces it).
MAX_DELEGATE_FILES = 20

# ASCII digits only, and a fraction of 1 to 9 digits (nanoseconds at most).
TIMESTAMP_RE = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]{1,9})?Z"
)
HEX_RE = re.compile(r"[0-9a-f]{7,64}")
STEM_RE = re.compile(r"[0-9a-f]{8}")
UNUSED_STEM = "unnamed"

#: Closed vocabularies a string value may always take (the callers add their own).
DEFAULT_VOCAB: FrozenSet[str] = frozenset(
    CLASSES
    + STAGE_BUCKETS
    + CHECK_CLASSES
    + SHAPES
    + ROLES
    + LIMIT_IDS
    + VERDICTS
    + CHECK_VERDICTS
    + MISSING_LINKS
    + ("gate", "work", "undetermined", UNUSED_STEM)
)

MAX_DEPTH = 64


class Refusal(Exception):
    """The out-dir guard refused. `str(exc)` and `.reason` are the same closed token."""

    def __init__(self, reason: str) -> None:
        if reason not in REFUSAL_REASONS:
            raise ValueError("unknown refusal reason")
        super().__init__(reason)
        self.reason = reason


class PrivacyRefusal(Exception):
    """A value or key is outside the allowed channels; the message is a closed phrase."""


class OutDirPlan(NamedTuple):
    """An accepted out-dir: its resolved path and what was found there."""

    path: Path
    existed: bool
    marked: bool


# ---------------------------------------------------------------------------------------------
# The out-dir guard
# ---------------------------------------------------------------------------------------------


def _resolve(path: Any) -> Path:
    """Resolve symlinks and `..` the same way for every path the guard compares.

    Raises:
        ValueError: The path holds a NUL byte (`realpath` raises it only on some
            interpreters, so the guard raises it itself; `check_out_dir` maps it to
            `could-not-decide`).
    """
    spelled = os.fspath(path)
    if ("\0" if isinstance(spelled, str) else b"\0") in spelled:
        raise ValueError("embedded null byte")
    return Path(os.path.realpath(spelled))


def _within(child: Path, parent: Path) -> bool:
    """True when `child` is `parent` or lies under it (component-wise, never by prefix)."""
    return child == parent or parent in child.parents


def _nearest_existing(path: Path) -> Path:
    """The path itself or its nearest ancestor that exists."""
    probe = path
    while not os.path.lexists(probe):
        if probe.parent == probe:
            break
        probe = probe.parent
    return probe


def _walk_finds_repository(resolved: Path) -> bool:
    """Filesystem walk: a `.git` entry or a `HEAD`+`objects`+`refs` triple at or above."""
    probe = resolved
    while True:
        if os.path.lexists(probe / ".git"):
            return True
        if all(os.path.lexists(probe / name) for name in ("HEAD", "objects", "refs")):
            return True
        if probe.parent == probe:
            return False
        probe = probe.parent


def _scrubbed_env() -> Dict[str, str]:
    """The caller's environment without any `GIT_*` variable (`GIT_DIR` and friends)."""
    return {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}


def _git_status(ancestor: Path) -> Optional[int]:
    """Exit status of `git -C <ancestor> rev-parse --git-dir`; `None` when undecidable."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(ancestor), "rev-parse", "--git-dir"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=_scrubbed_env(),
            timeout=GIT_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return proc.returncode


def _ident(path: Any) -> Optional[Tuple[int, int]]:
    """Filesystem identity `(st_dev, st_ino)` of an existing path, else `None`."""
    try:
        st = os.stat(path)
    except (OSError, ValueError):
        return None
    return (st.st_dev, st.st_ino)


def _chain_idents(path: Path) -> FrozenSet[Tuple[int, int]]:
    """Identities of `path` and of every ancestor that exists (missing tail skipped)."""
    found = set()
    probe = path
    while True:
        ident = _ident(probe)
        if ident is not None:
            found.add(ident)
        if probe.parent == probe:
            return frozenset(found)
        probe = probe.parent


def check_out_dir(
    out_dir: Any, *, transcript: Any, delegates_dir: Any = None
) -> OutDirPlan:
    """Decide whether `out_dir` may receive the tool's output. Writes nothing.

    Args:
        out_dir: The requested output directory (need not exist).
        transcript: The transcript path; its parent directory tree is off limits, both
            where the path resolves to and where it is spelled (when it is a symlink).
        delegates_dir: The delegate directory, off limits too, or `None`.

    Returns:
        The accepted plan (resolved path, whether it exists, whether it carries the marker).

    Raises:
        Refusal: With a closed `.reason`; the message never holds a path. Any other failure
            while examining the location (a NUL byte, an unreadable directory) is
            `could-not-decide`, and the original exception is not chained.
    """
    refusal: Optional[str] = None
    plan: Optional[OutDirPlan] = None
    try:
        plan = _check_out_dir(out_dir, transcript, delegates_dir)
    except Refusal as exc:
        refusal = exc.reason
    except Exception:  # noqa: BLE001 - fail closed on anything the filesystem raises
        refusal = "could-not-decide"
    if refusal is not None:
        raise Refusal(refusal)
    assert plan is not None
    return plan


def _check_out_dir(out_dir: Any, transcript: Any, delegates_dir: Any) -> OutDirPlan:
    resolved = _resolve(out_dir)
    transcript_path = _resolve(transcript)
    logical_parent = _resolve(Path(os.path.abspath(os.fspath(transcript))).parent)
    protected = [transcript_path.parent, logical_parent]
    if delegates_dir is not None:
        protected.append(_resolve(delegates_dir))
    out_chain = _chain_idents(resolved)
    for tree in protected:
        ident = _ident(tree)
        if _within(resolved, tree) or (ident is not None and ident in out_chain):
            raise Refusal("inside-transcript-tree")
    out_ident = _ident(resolved)
    for target in [transcript_path] + protected:
        if _within(target, resolved) or (
            out_ident is not None and out_ident in _chain_idents(target)
        ):
            raise Refusal("contains-transcript")

    ancestor = _nearest_existing(resolved)
    if os.path.lexists(ancestor) and not os.path.isdir(ancestor):
        # A file stands where the location (or one of its parents) should be.
        if ancestor == resolved:
            raise Refusal("non-empty-no-marker")
        raise Refusal("could-not-decide")
    if not os.access(ancestor, os.R_OK | os.X_OK):
        # Neither git nor the walk can be trusted inside a directory that cannot be searched.
        raise Refusal("could-not-decide")

    status = _git_status(ancestor)
    if status is None:
        raise Refusal("could-not-decide")
    if status == 0:
        raise Refusal("inside-work-tree")
    if status != GIT_NOT_A_REPO:
        raise Refusal("could-not-decide")
    if _walk_finds_repository(resolved):
        raise Refusal("inside-work-tree")

    existed = os.path.isdir(resolved)
    marked = False
    if existed:
        marker = resolved / MARKER
        marked = os.path.isfile(marker) and not os.path.islink(marker)
        if not marked and any(True for _ in os.scandir(resolved)):
            raise Refusal("non-empty-no-marker")
    return OutDirPlan(resolved, existed, marked)


def prepare_out_dir(plan: OutDirPlan) -> int:
    """Create the out-dir 0700, or clean the two stale temp names in a marked one.

    Returns:
        The number of stale temp names removed (0 for a fresh directory).
    """
    if not plan.existed:
        os.makedirs(plan.path, mode=DIR_MODE, exist_ok=True)
        os.chmod(plan.path, DIR_MODE)
        return 0
    cleaned = 0
    for name in TEMP_NAMES.values():
        target = plan.path / name
        if os.path.lexists(target):
            os.unlink(target)
            cleaned += 1
    return cleaned


def outscope_listing(out_dir: Any) -> CheckRow:
    """OUTSCOPE: FAIL on any out-dir entry other than the three output names.

    The row carries counts only; an entry's name is never echoed.
    """
    names = sorted(entry.name for entry in os.scandir(out_dir))
    unknown = [n for n in names if n not in OUTPUT_NAMES]
    verdict = "fail" if unknown else "pass"
    return CheckRow(
        "OUTSCOPE", verdict, "entries=%d unknown=%d" % (len(names), len(unknown))
    )


def write_output(out_dir: Any, name: str, data: bytes) -> None:
    """The one writer: `name` must be one of `OUTPUT_NAMES`; files are created 0600.

    `report.md` and `data.json` go through their fixed temp name and are renamed into place;
    the marker is written directly.

    Raises:
        PrivacyRefusal: `name` is not an allowed output name, or `data` is not bytes.
    """
    if name not in OUTPUT_NAMES:
        raise PrivacyRefusal("output name outside the allowed set")
    if not isinstance(data, (bytes, bytearray)):
        raise PrivacyRefusal("output data is not bytes")
    base = Path(out_dir)
    final = base / name
    target = base / TEMP_NAMES[name] if name in TEMP_NAMES else final
    if os.path.lexists(target):
        os.unlink(target)
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, FILE_MODE)
    try:
        try:
            os.fchmod(fd, FILE_MODE)
            with os.fdopen(fd, "wb") as handle:
                fd = -1
                handle.write(bytes(data))
        finally:
            if fd != -1:
                os.close(fd)
        if target != final:
            os.replace(target, final)
    except BaseException:
        # A failed write must not leave a partial temp file (or partial marker) behind.
        try:
            os.unlink(target)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------------------------
# The output allowlist
# ---------------------------------------------------------------------------------------------


def safe_token(value: Any, vocab: Iterable[str]) -> str:
    """Return `value` when it is a member of the closed `vocab`."""
    if isinstance(value, str) and value in frozenset(vocab):
        return value
    raise PrivacyRefusal("string outside the closed vocabulary")


def safe_timestamp(value: Any) -> str:
    """Return `value` when it is a UTC timestamp."""
    if isinstance(value, str) and TIMESTAMP_RE.fullmatch(value):
        return value
    raise PrivacyRefusal("string is not a UTC timestamp")


def safe_hex(value: Any) -> str:
    """Return `value` when it is a lowercase hex digest or sha of 7 to 64 digits."""
    if isinstance(value, str) and HEX_RE.fullmatch(value):
        return value
    raise PrivacyRefusal("string is not a hex digest")


def safe_id(value: Any) -> str:
    """Return `value` when it fullmatches the opaque-id pattern (data.json only)."""
    if isinstance(value, str) and ID_RE.fullmatch(value):
        return value
    raise PrivacyRefusal("string is not an opaque id")


def transcript_stem(file_name: str) -> str:
    """The stem channel: the first 8 characters when lowercase hex, else `unnamed`."""
    head = file_name[:8]
    return head if STEM_RE.fullmatch(head) else UNUSED_STEM


def safe_stem(value: Any) -> str:
    """Return `value` when it is a transcript stem (8 hex digits or `unnamed`)."""
    if isinstance(value, str) and (STEM_RE.fullmatch(value) or value == UNUSED_STEM):
        return value
    raise PrivacyRefusal("string is not a transcript stem")


def safe_number(value: Any) -> Any:
    """Return `value` when it is a bool, an int or a finite float."""
    if isinstance(value, (bool, int)):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    raise PrivacyRefusal("number is not finite")


def safe_format(template: str, **fields: Any) -> str:
    """Fill a source-constant `template` with numbers and closed-channel strings only.

    A string field must be a timestamp, a hex digest, a stem or a `DEFAULT_VOCAB` member.
    """
    clean: Dict[str, Any] = {}
    for key, value in fields.items():
        if isinstance(value, str):
            clean[key] = _safe_string(value, DEFAULT_VOCAB, False)
        else:
            clean[key] = safe_number(value)
    return template.format(**clean)


def _safe_string(value: str, vocab: FrozenSet[str], id_ok: bool) -> str:
    if value in vocab:
        return value
    for check in (safe_timestamp, safe_hex, safe_stem):
        try:
            return check(value)
        except PrivacyRefusal:
            pass
    if id_ok:
        return safe_id(value)
    raise PrivacyRefusal("string outside the allowed channels")


def validate_tree(
    obj: Any,
    keys: Iterable[str],
    *,
    vocab: Iterable[str] = (),
    id_keys: Iterable[str] = ("id",),
) -> None:
    """Refuse any key or string value of `obj` outside the allowed channels.

    Args:
        obj: The `data.json` tree (dicts, lists, numbers, bools, `None`, strings).
        keys: Every dictionary key allowed to appear (source constants).
        vocab: Extra closed-vocabulary strings allowed as values, beyond `DEFAULT_VOCAB`.
        id_keys: Keys under which an opaque-id string is allowed as a value.

    Raises:
        PrivacyRefusal: A key is not allowed or is built from a timestamp, a string is
            outside the channels, or a value has an unsupported type. The message is closed.
    """
    allowed_keys = frozenset(keys)
    allowed_vocab = DEFAULT_VOCAB | frozenset(vocab)
    id_key_set = frozenset(id_keys)

    def walk(node: Any, parent: Optional[str], depth: int) -> None:
        if depth > MAX_DEPTH:
            raise PrivacyRefusal("tree is nested too deeply")
        if node is None:
            return
        if isinstance(node, (bool, int, float)):
            safe_number(node)
        elif isinstance(node, str):
            _safe_string(node, allowed_vocab, parent in id_key_set)
        elif isinstance(node, dict):
            for key, child in node.items():
                if not isinstance(key, str) or key not in allowed_keys:
                    raise PrivacyRefusal("key outside the allowed set")
                if TIMESTAMP_RE.fullmatch(key):
                    raise PrivacyRefusal("key is built from a timestamp")
                walk(child, key, depth + 1)
        elif isinstance(node, (list, tuple)):
            for child in node:
                walk(child, parent, depth + 1)
        else:
            raise PrivacyRefusal("value of an unsupported type")

    walk(obj, None, 0)


# ---------------------------------------------------------------------------------------------
# The leak check
# ---------------------------------------------------------------------------------------------

#: Planted control: absent from any real output. The raw one is planted in the report copy,
#: the escaped one (a quote and a non-ASCII character) in the data copy, so the control
#: proves both the raw and the JSON-escaped match.
PLANTED_RAW = "planted control window alpha lorem ipsum dolor sit"
PLANTED_ESCAPED = 'planted "control" window \u00e9 beta lorem ipsum dolor'

Matcher = Callable[[str, Sequence[str]], bool]


def window_in_outputs(window: str, haystacks: Sequence[str]) -> bool:
    """The default matcher: `window` raw or JSON-escaped in any of the `haystacks`."""
    escaped = json.dumps(window)[1:-1]
    for hay in haystacks:
        if window in hay or escaped in hay:
            return True
    return False


def _is_path_or_id(text: str) -> bool:
    stripped = text.strip()
    if stripped.startswith(("/", "~", "./", "../")) or "://" in stripped:
        return True
    return ID_RE.fullmatch(stripped) is not None


def _usable(text: Any) -> bool:
    return (
        isinstance(text, str)
        and len(text) >= MIN_CANDIDATE_CHARS
        and text.count(" ") >= MIN_CANDIDATE_SPACES
        and not _is_path_or_id(text)
    )


def _strings_of(node: Any, out: List[str], depth: int = 0) -> None:
    if depth > MAX_DEPTH:
        return
    if isinstance(node, str):
        out.append(node)
    elif isinstance(node, dict):
        for child in node.values():
            _strings_of(child, out, depth + 1)
    elif isinstance(node, list):
        for child in node:
            _strings_of(child, out, depth + 1)


def _record_strings(rec: Any) -> List[str]:
    """Free-text strings of one record: text blocks, string content, tool results, inputs."""
    found: List[str] = []
    message = rec.get("message") if isinstance(rec, dict) else None
    if not isinstance(message, dict):
        return found
    content = message.get("content")
    if isinstance(content, str):
        found.append(content)
    elif isinstance(content, list):
        for block in content:
            if not isinstance(block, dict):
                continue
            kind = block.get("type")
            if kind == "text":
                _strings_of(block.get("text"), found)
            elif kind == "tool_result":
                _strings_of(block.get("content"), found)
            elif kind == "tool_use":
                _strings_of(block.get("input"), found)
    return found


def sample_candidates(raw: bytes, *, seed: int = 0, limit: int = 300) -> List[str]:
    """Windows of free text drawn from `raw` (transcript bytes), in memory only.

    A candidate string has at least 60 characters and three spaces and is not a path or id.
    Distinct candidates are kept in file order; when there are more than `limit`, a
    seed-determined subset is kept. Each yields one 40-character window at a seeded offset
    anywhere in the string (so a leak that is not a prefix is probed too).

    Args:
        raw: The pinned transcript (or delegate) bytes, as JSON lines.
        seed: The `--leak-seed` value; the same seed gives the same windows.
        limit: The most windows to return.
    """
    seen = set()
    candidates: List[str] = []
    for line in raw.split(b"\n"):
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        for text in _record_strings(rec):
            if _usable(text) and text not in seen:
                seen.add(text)
                candidates.append(text)
    rng = random.Random(seed)
    if len(candidates) > limit:
        keep = sorted(rng.sample(range(len(candidates)), limit))
        candidates = [candidates[i] for i in keep]
    windows: List[str] = []
    chosen = set()
    for text in candidates:
        span = len(text) - WINDOW_CHARS + 1
        for _ in range(WINDOW_ATTEMPTS):
            start = rng.randrange(0, span)
            window = text[start : start + WINDOW_CHARS]
            if sum(1 for c in window if not c.isspace()) >= MIN_WINDOW_NONSPACE:
                if window not in chosen:
                    chosen.add(window)
                    windows.append(window)
                break
    return windows


def reread_delegate(path: Any, bytes_read: int, sha256: str) -> bytes:
    """Re-read exactly the first `bytes_read` bytes of a delegate file, verified.

    Appended bytes beyond `bytes_read` are ignored.

    Raises:
        PinError: The file is shorter than `bytes_read` or the prefix hashes differently.
        ReadError: The file could not be opened or read.
    """
    try:
        with open(path, "rb") as handle:
            data = handle.read(bytes_read)
    except OSError:
        raise ReadError("delegate file could not be read") from None
    if len(data) != bytes_read or hashlib.sha256(data).hexdigest() != sha256:
        raise PinError("delegate bytes differ from the recorded prefix")
    return data


def leak_check(
    report: bytes,
    data: bytes,
    windows: Sequence[str],
    *,
    floor: int = 100,
    matcher: Optional[Matcher] = None,
) -> LeakResult:
    """Search the exact output bytes for each sampled window, after a planted control.

    Args:
        report: The `report.md` bytes that will be written.
        data: The `data.json` bytes that will be written.
        windows: Sampled free-text windows (`sample_candidates`).
        floor: The fewest samples that make the check meaningful (`--leak-floor`).
        matcher: Injected `(window, haystacks) -> bool`; defaults to `window_in_outputs`.

    Returns:
        `LeakResult(samples, hits, control, floor_ok)` with `control` `detected` or
        `not-detected`. Use `leak_verdict` for the run verdict.
    """
    find = matcher if matcher is not None else window_in_outputs
    windows = list(dict.fromkeys(windows))  # boilerplate sharing a window counts once
    report_text = report.decode("utf-8", errors="replace")
    data_text = data.decode("utf-8", errors="replace")
    planted_report = report_text + "\n" + PLANTED_RAW
    planted_data = data_text + "\n" + json.dumps(PLANTED_ESCAPED)[1:-1]
    found = find(PLANTED_RAW, [planted_report, planted_data]) and find(
        PLANTED_ESCAPED, [planted_report, planted_data]
    )
    control = "detected" if found else "not-detected"
    hits = sum(1 for w in windows if find(w, [report_text, data_text]))
    return LeakResult(len(windows), hits, control, len(windows) >= max(floor, 1))


def leak_verdict(result: LeakResult) -> str:
    """Run verdict for a leak result: ERROR > FAIL > INCOMPLETE > PASS."""
    if result.control != "detected":
        return "ERROR"
    if result.hits > 0:
        return "FAIL"
    if not result.floor_ok:
        return "INCOMPLETE"
    return "PASS"


# ---------------------------------------------------------------------------------------------
# The remedy-word finder
# ---------------------------------------------------------------------------------------------

REMEDY_RE = re.compile(
    r"\b(should|propose|recommend|suggest|consider|improve|optimi[sz]e|avoid|"
    r"eliminate|speed\s+up|fix\s+by|replace\s+with|we\s+could|could\s+be)\b",
    re.IGNORECASE,
)


def find_remedy_words(text: str) -> List[str]:
    """Remedy words in `text`, lowercased with single spaces, in order of appearance."""
    return [" ".join(m.group(1).lower().split()) for m in REMEDY_RE.finditer(text)]


__all__ = [
    "DEFAULT_VOCAB",
    "MARKER",
    "OUTPUT_NAMES",
    "OutDirPlan",
    "PrivacyRefusal",
    "REFUSAL_REASONS",
    "Refusal",
    "check_out_dir",
    "find_remedy_words",
    "leak_check",
    "leak_verdict",
    "outscope_listing",
    "prepare_out_dir",
    "reread_delegate",
    "safe_format",
    "safe_hex",
    "safe_id",
    "safe_number",
    "safe_stem",
    "safe_timestamp",
    "safe_token",
    "sample_candidates",
    "transcript_stem",
    "validate_tree",
    "window_in_outputs",
    "write_output",
]

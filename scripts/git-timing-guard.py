#!/usr/bin/env python3
# Script: git-timing-guard.py
# Purpose: PreToolUse hook — block PUBLISHING outside a configured time window
# Usage: Called by Claude Code hooks with JSON on stdin
"""PreToolUse hook — block a `git push` outside a configured local time window.

Rewrite of the original `git-timing-guard.sh`, which matched `push` with a crude
`(^|[^A-Za-z0-9_])git...push` regex over the raw command TEXT. That regex could not distinguish
"the phrase appears in this segment" from "a push actually happens here", so it false-blocked a
`git push` mentioned in an `echo`, a `grep` argument, a comment or an unreferenced variable, while
(by the same crudeness) still catching some wrapped/nested-shell pushes a real tokenizer cannot see
without resolving arbitrary strings other programs execute. This version reuses the shared
`scripts/lib/git_command` tokenizer, which finds `git` invocations in actual COMMAND POSITION.

PUBLICATION BOUNDARY ONLY — unchanged from the original. `push` is the act that makes work public
and is the only thing time-gated; `commit` and `tag` never leave the machine, so the window does
not block them. Gating those too made the override a daily reflex, and a bypass used daily is not
a gate.

EVERY PUSH'S TARGET IS JUDGED, since 2026-09-18 — the original bash guard, and this one until
then, broke out at the first match, so a harmless push aimed elsewhere could displace the real
one (`git -C /tmp <push>; git <push> origin dev` was allowed). That became a live decoy once an
indeterminate subcommand counted as a push, so the command now blocks when ANY push-shaped
invocation targets a guarded repo. The OVERRIDE is still read from the first push-shaped
segment only (below) — and since an indeterminate subcommand counts as push-shaped, that segment
can now be a no-op (`ALLOW_GIT_WRITE=1 git -C /tmp $V; <push of the guarded repo>` is judged
authorized, where it blocked before). A residual, not a hole: it needs the operator's own override
token, which already authorizes the plain push.

OVERRIDE IS SEGMENT-SCOPED, matching `push-guard.py`'s stricter reading, not the original bash's
"any segment anywhere" reading. `ALLOW_GIT_WRITE=1` must lead the SAME control-operator-delimited
segment that carries the push — an assignment on an unrelated segment no longer authorizes
everything, and (the case that actually changes behaviour) an assignment appearing AFTER a wrapper
(`sudo ALLOW_GIT_WRITE=1 git push ...`) does NOT authorize, because the wrapper, not the assignment,
is the segment's first token. This is measured to disagree with the tokenizer's own
`iter_git_invocations_detailed`, whose `tokens.env` field steps over wrapper tokens transparently
and would read that same shape as authorized — so authorization here is derived independently, by
walking `iter_context_token_streams`' segments the same way `push-guard.py` does, rather than from
that field. See `_leading_env_authorized` and its docstring.

CWD RESOLUTION now follows `cd`/`pushd`/`popd` and composes each invocation's own `-C` on top of
that, via `iter_git_invocations_detailed`'s cwd walk — the original bash guard never looked at `cd`
at all (`target="$cwd"`, the payload's own cwd, overridden only by the FIRST `-C` on the matching
segment). That is a decided behaviour change, not a silent side effect:
  - `cd <guarded> && git push` from elsewhere: closes a live fail-open (bash never caught it).
  - `cd <other> && git push` from the guarded repo: fixes a false block (bash always caught it).
  - `cd "$VAR" && git push` (an unresolvable `cd` target): falls back to the payload's own `cwd` —
    the same no-surprise answer `target="$cwd"` already gave, so this keeps the change about
    command position rather than smuggling in a new cwd posture.
  - `git -C a -C b push`: composes on the LAST `-C` (the walk overwrites `cdir` on each occurrence),
    never a bare `cdir` and never the bash guard's `head -1` (first).

CONCEDED RESIDUALS, matching `push-guard.py`'s documented posture for the identical reason —
re-catching them reintroduces the false-positive class this rewrite exists to kill: a wrapper
carrying its OWN arguments (`sudo -u deploy git push`, `timeout 60 git push`) — measured: every
literal `WRAPPERS` name, invoked BARE, still blocks; only an argumented invocation drops — a
wrapper outside the
closed `WRAPPERS` set (`flock git push`), a path-qualified wrapper spelling (`/usr/bin/sudo git
push`), and a push hidden inside a single shlex token — a nested-shell string (`bash -c`/`eval`/pipe-
into-shell), a herestring, a variable holding the command text, or a script written then run. Today's
crude regex caught these by accident (it matched the phrase in raw text, ignoring who was actually in
command position); this guard no longer does, which is a REGRESSION IN DETECTION accepted on
purpose, not a bug — see `scripts/tests/test_git_timing_guard.sh`'s "DELIBERATE DROP" section and
`memory/2026-09-01-timing-guard-command-position.md` for the measurement and the decision.

AMBIGUITY POSTURE — fails OPEN, via an explicit handler, never an uncaught exception that happens
to exit non-2. A tokenizer `ValueError` on a command this guard cannot parse unambiguously is
treated the same as "no push found": the publication boundary this guard is a NUDGE against, not a
security boundary, is covered regardless by `push-guard.py`, which fails CLOSED on the identical
ambiguity (verified: `git status && echo` with an unterminated backtick returns rc=2 there). So a
publish hidden inside an unparseable command is refused by that gate whatever this one does, and
this guard's own documented contract ("0 — allow ... or any internal error") already promises this.

FAIL-OPEN CONDITIONS, preserved from the bash guard's five (the JQ one is retired — see below):
  1. the policy file being ABSENT      (the documented way to disable; correct and intended)
  2. the policy file being UNREADABLE  (a permissions accident)
  3. a MISSPELLED key                  (an edit that looks right)
  4. the key present but EMPTY         (a half-finished edit)
A fifth, new one replaces the bash guard's `command -v jq` condition: `python3` itself failing to
start or import this module is indistinguishable, from outside, from a crash inside `set -euo
pipefail` — any uncaught exception anywhere in this script's body exits non-zero non-2, which
HOOKS.md treats as noise, not a veto. The old JQ condition is GONE, not just renamed — this hook
parses JSON with the stdlib `json` module and never shells out to `jq` — and `skills/audit/audit.sh`'s
`check_timing_guard_conf` check, which reasoned from that identical `command -v jq` check, needed a
corresponding update: landed in this same change (see its `jq-unavailable` arm becoming a SKIP and
the new `python3-unusable` / `python3-lib-absent` arms replacing the FAIL that reasoning used to
carry) — the two halves are only correct in combination, so they could not land separately.

THE AMBIGUITY MARKER GETS ITS OWN MESSAGE. The tokenizer signals a reading of the command it could
not parse in-band, as a synthetic `git <marker>` stream (`git_command._indeterminate_stream`), and
`subcommand_is_indeterminate` is True for it — so `_segment_contains_push` treats it as a possible
push and this gate examines the command. What `BLOCK_MESSAGE` then says is wrong twice over: it
tells an operator whose command carries no push at all that they scheduled a publish, and it points
at the window as the thing to wait for, when no `ALLOW_GIT_WRITE=1` can lead that record either
(it corresponds to no command text, so its leading env-assignment run is empty by construction).
Measured on the witness `bash <<'(x'` / `echo hello` / an `x=$` line ending in a backslash / `(x`,
which carries no git word and no push: rc 2, "pushing is paused until <GUARD_END> local — publish
after the window". The marker gets
`AMBIGUOUS_READING_MESSAGE` instead, and a real push-shaped segment outranks it (see
`_find_first_push`), so a command carrying both keeps the window wording.

THE MARKER IS ALSO A STRING AN OPERATOR CAN TYPE. The test was MEMBERSHIP across every token
position, while the tokenizer only ever emits it as a whole two-token segment, so a real push
carrying it as an argument was relabelled as an ambiguity block; `_operator_typed_the_marker`
settles the rest from the raw command text. Measured on push-guard's twin of these two functions,
where the relabel also made the message false in so many words.

AND ITS REMEDY IS CHOSEN FROM THE CAUSE. The marker is emitted both by a reading that RAISED and by
an exhausted parse BUDGET; the fixed sentence this guard used to print named the first, and 6 of 6
real markers over 90,675 commands are the second. See `scripts/lib/guard_ambiguity.py`.

A DEADLINE THIS PROCESS OWNS, EXITING 0. This hook registers no `timeout`, so the harness default
binds and a run that outlives it is killed silently. `main` arms a deadline below that default —
but its handler exits 0, not 2, because this gate's whole contract is fail-OPEN on any internal
error (condition 5 above, and the bare `except` around `main`). It changes no verdict; it bounds
the wait and says why. See `scripts/lib/guard_deadline.py`.

Exit codes:
  0 — allow (no policy, not a push, out of scope, inside the window, or any internal error).
  2 — blocked: a push to a configured repo during the blocked window (stderr fed back to Claude),
      or a command whose reading the tokenizer lost — stderr then carries the ambiguity message.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path

HOOK_NAME = "git-timing-guard.py"

ARGV_MESSAGE = f"{HOOK_NAME}: expects a JSON payload on stdin, not arguments — see scripts/HOOKS.md\n"
TTY_MESSAGE = (
    f"{HOOK_NAME}: expects a JSON payload on stdin; refusing a terminal stdin rather than "
    "blocking on a read that will never arrive — see scripts/HOOKS.md\n"
)
BLOCK_MESSAGE = (
    "blocked by git timing guard: pushing is paused until {end} local — publish after the "
    "window. Local commits and tags are not gated.\n"
)

# The message for a block the standard one would MISDESCRIBE. `BLOCK_MESSAGE` says "publish after
# the window", which tells an operator whose command carries no push at all that they scheduled a
# publish; and the one record behind this block corresponds to no command text, so no
# ALLOW_GIT_WRITE=1 can lead it either (`git_command._indeterminate_stream` records why by
# construction). Measured on the witness `bash <<'(x'` / `echo hello` / an `x=$` line ending in a
# backslash / `(x`, which carries no git word and no push: rc 2 with the window wording.
# It deliberately does NOT prescribe waiting. Waiting really would allow the command — the gate is
# still window-scoped — but the refusal is about a reading of the command, and sending the operator
# away for an hour to work around a quoting problem is exactly the unexplainable false block the
# marker's own justification rules out.
AMBIGUOUS_READING_MESSAGE = (
    "blocked by git timing guard: one READING of this command could not be parsed, so the command "
    "was treated as possibly performing a push — which is the only reason this publication-window "
    "gate examined it at all. No env prefix authorizes it: the reading that was lost corresponds "
    "to no command text, so there is no segment for ALLOW_GIT_WRITE=1 to lead. bash reads a "
    "heredoc whose last body line ends in a backslash two ways - dropping the continuation, or "
    "joining it onto the terminator - and only one of those readings parses here, so what the "
    "other one would have run is unknown. {remedy}{tool}\n"
)
# `{remedy}` is filled from `guard_ambiguity.remedy(...)` at print time. The marker has TWO causes
# -- a reading that RAISED, and an exhausted parse BUDGET -- and the sentence that used to be
# hardcoded here named the one that does not occur in practice; see `guard_ambiguity`'s docstring
# for the 6-of-6 measurement over 90,675 real commands.

# The override token. Segment-scoped, matching push-guard.py's stricter reading — see the module
# docstring's OVERRIDE IS SEGMENT-SCOPED paragraph.
OVERRIDE_TOKEN = "ALLOW_GIT_WRITE=1"


def _explain_tool_clause() -> str:
    """The sentence naming THIS guard's copy of the explain tool, or "" on any failure.

    Non-raising by contract: it is called from the block-emitting path, and per `scripts/HOOKS.md`
    only exit 2 blocks, so an exception escaping here would exit 1 and the guarded command would
    RUN. Per-caller for the reason `git_command.describe_ambiguity` records — the tokenizer
    deliberately names no tool, because the path differs per caller — and resolved from `__file__`
    rather than a repo-relative string, which resolves to nothing from the deployed config farm.
    `__file__` is this .py even when reached through the `git-timing-guard.sh` shim, which `exec`s
    into it.
    """
    try:
        tool = Path(__file__).resolve().parent / "explain-git-command.py"
        return f" For the full parse, run: python3 {tool} -"
    except Exception:  # noqa: BLE001 - deliberate: a broken diagnostic must never unblock
        return ""


# The four characters the bash guard's `tr -d "\"' \\r"` deletes, WHEREVER they occur in the
# value — not just at the edges. `tr -d` is a global deletion, not a trim, so a value like
# `wohl"ford` would lose the embedded quote too. Recorded here because an earlier review claimed
# this set deletes the letter `r`; measured false (`tr` performs its own escape processing, not a
# second round of backslash interpretation) — a literal ".strip()" port would both be wrong on the
# edges-only question above AND silently correct for a bug that was never there.
_CONF_STRIP_CHARS = "\"' \r"


def _conf_get(conf_text: str, key: str) -> str | None:
    """Mirror the bash guard's `conf_get()`: the LAST line starting with `KEY=` wins (`tail -1`),
    the value is everything after the first `=`, and `"`, `'`, space and CR are deleted from it
    wherever they occur. Returns None if the key has no matching line at all (as distinct from a
    present-but-empty value, which returns "" — both are falsy to a caller, matching the bash
    guard's `[ -n "$repo_pat" ]` / `${var:-default}` treatment of "absent" and "empty" alike)."""
    prefix = key + "="
    value = None
    for line in conf_text.splitlines():
        if line.startswith(prefix):
            value = line[len(prefix) :]
    if value is None:
        return None
    return "".join(ch for ch in value if ch not in _CONF_STRIP_CHARS)


def _skip_global_options(gitcmd, seg: list[str], start: int) -> int:
    """Return the index of the subcommand token, stepping over git's global options starting at
    `start` (just past the `git` token itself) — mirrors `push-guard.py`'s helper of the same
    name, which mirrors the tokenizer's own walk, including its `-C <dir>` / `-C<dir>` handling.

    Classifies each option via `gitcmd.classify_global_opt`, the SAME allowlist-driven classifier
    `iter_git_invocations_detailed` uses — not a hand-rolled "assume valueless, step over"
    fallback. That fallback is exactly the F2 shape `classify_global_opt`'s own module comment
    names as a measured fail-open (`git --attr-source HEAD push origin dev` reading `push` as an
    argument of an assumed-valueless unknown option, burying the real push where no consumer
    re-examines it) — this function used to carry that identical shape.

    An UNKNOWN option raises, matching this guard's own AMBIGUITY POSTURE (fail open via an
    EXPLICIT handler, never a guess): `git -C <dir> --badopt push origin main` is genuinely
    unjudgeable here (an unrecognized option could turn out to be value-taking, which would make
    `push` its VALUE rather than the subcommand). Measured to matter: before this raised,
    `_segment_contains_push` confidently found the push (its old fallback stepped over the
    unknown option), while `_push_target_dirs`'s `iter_git_invocations_detailed` — which does
    NOT raise on this shape; correction, this file previously claimed it "already raises this
    identical shape", which is false — instead RECORDS the unknown option itself as the
    invocation's subcommand and keeps walking, so its loop never finds a `push` subcommand here
    and silently falls back to the payload cwd. On a payload cwd that happens to equal the guarded
    repo, that fallback lands on the right directory by coincidence and still blocks; on any OTHER
    payload cwd it reads as an ALLOW for a real push in the guarded repo (`git -C <guarded>
    --badopt push origin main`, old rc=2, new rc=0 before this fix). Propagating the raise HERE,
    in `_segment_contains_push`'s own walk, is what actually closes the gap — it makes `main`'s
    FIRST `except ValueError` fire before `_push_target_dirs` is ever reached, so the guard's
    documented fail-open (not `_push_target_dirs`'s independent, undocumented one) is what
    decides every cwd uniformly. See `scripts/tests/test_git_timing_guard.sh`'s "cwd IS the
    guarded repo" rows, which pin that this is now uniform rather than an accident of which
    payload cwd happens to coincide with the fallback. The caller's `except ValueError` already
    exists for exactly this (see `_find_first_push`'s docstring).

    Raises:
        ValueError: on an unclassifiable ("unknown") global option — the caller's ambiguity
        handler decides that means allow (fail open).
    """
    k, n = start, len(seg)
    while k < n and seg[k].startswith("-"):
        opt = seg[k]
        if opt == "-C" and k + 1 < n:
            k += 2
            continue
        if opt.startswith("-C") and len(opt) > 2:
            k += 1
            continue
        kind = gitcmd.classify_global_opt(opt)
        if kind == "unknown":
            raise ValueError(f"unjudgeable global option before subcommand: {opt!r}")
        if kind == "value" and k + 1 < n:
            k += 2
        else:
            k += 1
    return k


def _segment_contains_push(gitcmd, seg: list[str]) -> bool:
    """True if `seg` (one control-operator-delimited slice of a context's token stream) contains
    a `git` invocation, in command position, whose subcommand is literally `push` — or, since
    2026-09-18, whose subcommand is INDETERMINATE (`gitcmd.subcommand_is_indeterminate`): a
    git-like word (an alias named `git` makes `git git …` run anything) or an opaque one (`git $V
    origin dev` with `V=<push>`). Either is treated as a possible push, since a consumer comparing
    a subcommand as literal text cannot tell it apart from one. Scoped to the publication boundary
    this guard gates — see the module docstring; `subtree push` and other push-shaped subcommands
    are deliberately not matched, exactly as the bash guard's `push` word match was not.

    Raises:
        ValueError: via `_skip_global_options`, on an unclassifiable global option between `git`
        and its subcommand — propagated uncaught, exactly like `_find_first_push`'s own
        documented raise; the caller's ambiguity handler decides that means allow (fail open).
    """
    for j in range(len(seg)):
        if not (
            gitcmd.is_git(seg[j])
            and gitcmd.starts_command(
                seg,
                j,
                reserved_words=gitcmd.RESERVED_WORDS,
                extra_wrappers=gitcmd.GIT_ONLY_WRAPPERS,
            )
        ):
            continue
        sub_idx = _skip_global_options(gitcmd, seg, j + 1)
        if sub_idx < len(seg) and (
            seg[sub_idx] == "push" or gitcmd.subcommand_is_indeterminate(seg[sub_idx])
        ):
            return True
    return False


def _leading_env_authorized(gitcmd, seg: list[str]) -> bool:
    """True if `seg`'s leading run of consecutive `NAME=val` env-assignment tokens (before the
    first non-assignment token) contains the literal token `ALLOW_GIT_WRITE=1`. A wrapper or any
    other non-assignment token at the START of the segment means the segment is not authorized —
    this is push-guard.py's `_leading_env_authorized`, copied rather than imported (it is
    module-private there) with the override token swapped in. Deliberately NOT derived from
    `iter_git_invocations_detailed`'s `InvocationTokens.env`: that field steps over WRAPPER tokens
    transparently while walking backward from `git`, so `sudo ALLOW_GIT_WRITE=1 git push` reads as
    authorized there — the wrapper, not the assignment, is this segment's true first token, and
    push-guard's stricter reading (matched here on operator instruction) says that does NOT
    authorize. Measured directly against `iter_git_invocations_detailed` before this was written.

    A LEADING RUN OF RESERVED WORDS IS SKIPPED FIRST, and this half is only correct alongside the
    `reserved_words=`/`extra_wrappers=` arguments passed at the detection site. Teaching the matcher
    that `if`/`then`/`{`/`!`/`exec` put git in command position, WITHOUT teaching this walker the
    same thing, silently converts an under-block into an over-block: this loop starts at `seg[0]`,
    which is now the reserved word, and breaks before it ever reaches the assignment, so
    `if true; then ALLOW_GIT_WRITE=1 <publish>; fi` stops being authorized. Measured, in exactly
    that half-applied state — the suite's "the override still authorizes inside a compound" row is
    the ONLY one of 65 that moves, which is why it exists.
    A WRAPPER is deliberately NOT skipped here: `sudo` is in `WRAPPERS`, not `RESERVED_WORDS`, so
    `sudo ALLOW_GIT_WRITE=1 <publish>` still does not authorize. That distinction is the whole
    reason this is not derived from `InvocationTokens.env`, and it has its own row.
    """
    start = 0
    while start < len(seg) and (
        seg[start] in gitcmd.RESERVED_WORDS or seg[start] in gitcmd.GIT_ONLY_WRAPPERS
    ):
        start += 1
    authorized = False
    for tok in seg[start:]:
        if not gitcmd.ENV_ASSIGN.match(tok):
            break
        if tok == OVERRIDE_TOKEN:
            authorized = True
    return authorized


def _operator_typed_the_marker(gitcmd, command: str) -> bool:
    """True when the OPERATOR's own text contains the tokenizer's reserved marker.

    The marker is a string a person can type, so no test over a token STREAM can tell a
    synthesized marker from a typed one — `git <push> origin main '$<ambiguous-heredoc-reading>'`
    carries it as an ordinary refspec argument, and `git '$<ambiguous-heredoc-reading>'` produces
    exactly the two-token segment `_indeterminate_stream` emits. The raw command text settles it,
    because a synthesized marker corresponds to NO command text by construction — the premise
    `AMBIGUOUS_READING_MESSAGE` states in so many words. `git_command.split_command_contexts`
    takes the same posture one layer down for an operator-supplied `PLACEHOLDER_PREFIX`.

    RESIDUAL, stated: a command that both types the marker AND genuinely loses a reading is
    reported with the window wording rather than the ambiguity wording. It still blocks — the
    synthesized segment is marker-subcommanded, so `_segment_contains_push` is True — so this
    degrades a message, never a verdict. See `push-guard.py`'s twin for the measurement.
    """
    return gitcmd.AMBIGUOUS_READING_SUBCOMMAND in command


def _segment_lost_a_reading(gitcmd, seg: list[str]) -> bool:
    """True if `seg` IS the tokenizer's in-band AMBIGUITY MARKER — a reading of the command that
    could not be parsed, or one the parse budget never tried.

    STRUCTURAL, not a membership test over every token position. The earlier membership form
    justified itself with "the marker reaches a stream only through
    `git_command._indeterminate_stream`", and that claim is FALSE: the marker is ordinary text an
    operator can type in any argument slot, so a real push carrying it was relabelled as an
    ambiguity block. Measured on push-guard's twin of this function, where the relabel also made
    the message false in so many words.

    The property lives in the SEGMENT's shape: `_indeterminate_stream` emits exactly two tokens,
    `['git', <marker>]`, with nothing before the `git` and nothing after the marker — its own
    docstring says so, and both stream consumers close the final segment at `i == n`, so that list
    IS one whole segment. This still cannot disagree with `_segment_contains_push` about which
    invocation it found: on the two-token marker segment that function walks to index 1 and finds
    the same token this one names.

    Narrowing a matcher silently drops true positives, twice measured in this parser, so the shape
    test is paired with a corpus of marker-producing commands that must STILL take the ambiguity
    path — the marker rows in this suite, run before and after.
    """
    return (
        len(seg) == 2
        and gitcmd.is_git(seg[0])
        and gitcmd.subcommand_is_ambiguous_reading(seg[1])
    )


def _find_first_push(gitcmd, command: str) -> tuple[bool, bool, bool]:
    """Scan every command context's token stream (`iter_context_token_streams`, outermost first,
    depth-first into what it contains — see that function's docstring) for the FIRST segment that
    contains a push in command position.

    The THIRD value is about the MESSAGE only and cannot move the verdict. `found`/`authorized`
    still come from the FIRST push-carrying segment, whichever that was: reading authorization
    from a later segment instead would let a phantom authorization return 0, which is the exact
    failure the primary-stream-first invariant exists to prevent. What the scan does gain is that
    it no longer stops at a marker — it keeps looking for a segment carrying a REAL push-shaped
    invocation, and reports `False` if it finds one, so a command holding both keeps the standard
    window wording whose remedy that command really has. Ordinary commands pay nothing: a real
    push still returns at the first segment that carries it.

    Why not just rely on the marker arriving LAST: `iter_context_token_streams` appends a
    context's marker after that context's own streams AND its children's, which reads as "a push
    always comes first" — but a sibling ordering is visible in `_collect`'s structure (a child's
    marker is appended before the next sibling's streams are collected), so the guarantee is not
    the whole one it looks like. UNCONSTRUCTED, stated as such: six adversarial shapes were probed
    for a marker preceding a push stream and none produced one — five raised outright, the sixth
    put the marker last. So this is a cheap invariant asserted here rather than a defect anyone
    has witnessed, and the message no longer depends on an ordering nobody has pinned.

    Returns:
        (found, authorized, lost_reading) — found is False if no push exists anywhere in the
        command (the other two are then meaningless); authorized reflects only the ONE segment
        that carries the first push found, per `_leading_env_authorized`; lost_reading is True
        when that segment was the tokenizer's ambiguity marker and no real push-shaped segment
        was found anywhere.

    Raises:
        ValueError: on tokenizing ambiguity — the caller decides that means allow (fail open).
    """
    found = False
    authorized = False
    lost_reading = False
    # Computed ONCE over the raw text: it is a property of the command, not of a segment.
    typed = _operator_typed_the_marker(gitcmd, command)
    for stream in gitcmd.iter_context_token_streams(command):
        seg_start = 0
        n = len(stream)
        for i in range(n + 1):
            if i == n or gitcmd.is_op(stream[i]):
                seg = stream[seg_start:i]
                if _segment_contains_push(gitcmd, seg):
                    marker = not typed and _segment_lost_a_reading(gitcmd, seg)
                    if not found:
                        found = True
                        authorized = _leading_env_authorized(gitcmd, seg)
                        lost_reading = marker
                    if not marker:
                        return found, authorized, False
                seg_start = i + 1
    return found, authorized, lost_reading


def _combine_dir(base: str | None, cdir: str | None) -> str | None:
    """Compose a base directory with an invocation's own `-C` value, never a bare `cdir` — an
    absolute `cdir` (every test fixture in this repo uses absolute repo paths, matching how a real
    `-C` target is normally written) simply wins over `base`, exactly as `os.path.join` treats a
    second absolute argument, mirroring what a real `-C` does to a preceding one."""
    if not cdir:
        return base
    if not base:
        return os.path.normpath(cdir)
    return os.path.normpath(os.path.join(base, cdir))


def _push_target_dirs(
    gitcmd, command: str, payload_cwd: str | None
) -> list[str | None]:
    """The target directory of EVERY `push`, or INDETERMINATE-subcommand (see
    `_segment_contains_push`), invocation `iter_git_invocations_detailed` finds, each composing its
    `effective_dir` (which follows `cd`/`pushd`/`popd`) with its own `cdir` (the LAST `-C` it
    carried — see `_combine_dir`), falling back to the payload's own `cwd` when `effective_dir` is
    unresolvable (e.g. `cd "$VAR"`) — see the module docstring's CWD RESOLUTION paragraph for the
    four pinned decisions this implements. No such invocation → `[payload_cwd]`.

    EVERY one, not the first: this returned only the first until 2026-09-18, and once an
    indeterminate subcommand counted as a push, a harmless decoy aimed elsewhere took that slot —
    `git -C /tmp $V; git <push> origin dev` was judged in `/tmp` and allowed (measured, the pinned
    in-window fixture), as `git -C /tmp <push>; git <push> origin dev` already had been. The caller
    blocks when ANY target is a guarded repo, so a decoy can no longer displace the real push.

    Correlated with `_find_first_push` by ORDER, not by shared state: both walk the command's
    contexts outermost-first: `_find_first_push` finds the first push-carrying SEGMENT via
    `iter_context_token_streams`, this walks the push INVOCATIONS via
    `iter_git_invocations_detailed`. They are two different primitives over the same command
    because no single one exposes both a push's own segment (needed for the override, which must
    NOT trust the invocation's wrapper-transparent env field — see `_leading_env_authorized`) and
    its resolved cwd (needed here, which the segment view does not track at all).

    Agreement on command-position classification is no longer a suite-scoped coincidence for the
    class that USED to break it: `_segment_contains_push`'s own global-option walk
    (`_skip_global_options`) now RAISES `ValueError` on an unrecognized global option, so a
    command neither primitive can classify is caught by `_find_first_push` FIRST and this
    function is never reached for it. Note precisely where that raise comes from, because an
    earlier revision of this docstring got it backwards: `iter_git_invocations_detailed` does
    NOT raise on this shape -- it records the unrecognized option as the invocation's subcommand
    and keeps walking, which is exactly the silent fallback described below. The raise is
    `_skip_global_options`'s own, added to close that gap; it is not one this function's
    primitive already performed. Before that fix the two primitives measurably disagreed on exactly this shape:
    `git -C <guarded> --badopt push origin main` and its option-order swap — `_find_first_push`
    confidently found the push (its old fallback stepped over the unknown option), while this
    function's `iter_git_invocations_detailed` recorded the unrecognized option as the
    invocation's subcommand, never `"push"`, so the loop below never matched it and fell through
    to the payload-cwd fallback — on a foreign payload cwd that silently ALLOWED a real push in
    the guarded repo. See `scripts/tests/test_git_timing_guard.sh`'s "unknown global option"
    rows, which pin the fixed behaviour rather than merely documenting the old disagreement.
    A command containing more than one push in different sub-contexts, ordered differently by the
    two DFS walks, remains a corpus edge this guard does not claim to test.

    Raises:
        ValueError: on tokenizing ambiguity — the caller decides that means allow (fail open).
    """
    targets: list[str | None] = []
    for inv in gitcmd.iter_git_invocations_detailed(command, payload_cwd):
        if inv.subcommand == "push" or gitcmd.subcommand_is_indeterminate(
            inv.subcommand
        ):
            base = inv.effective_dir if inv.effective_dir is not None else payload_cwd
            targets.append(_combine_dir(base, inv.cdir))
    return targets or [payload_cwd]


# Bounds on judging EVERY push target (see `_push_target_dirs`). Each distinct target costs one
# `git remote get-url` subprocess, and the command's author chooses how many there are and how
# slow each is (a repo whose config includes a large file) — measured: 2,000 decoys took 49.8 s,
# and 400 aimed at a slow repo passed 60 s, so the hook timed out and the command RAN. Past either
# bound the command is judged IN scope — it blocks inside the window rather than failing open. No
# documented workflow pushes to more than a handful of repos in one command.
MAX_PUSH_TARGETS = 8
TARGET_BUDGET_SECONDS = 15.0


def _scope_origin(targets: list[str | None], repo_pat: str) -> str:
    """The first distinct target's origin URL that names a guarded repo, else "" — or `repo_pat`
    itself, meaning "judge it in scope", when there are more than `MAX_PUSH_TARGETS` distinct
    targets or resolving them outruns `TARGET_BUDGET_SECONDS`. One slow target on its own still
    resolves to "" on its own 10 s timeout, exactly as before this bound existed."""
    import time

    distinct = list(dict.fromkeys(targets))
    if len(distinct) > MAX_PUSH_TARGETS:
        return repo_pat
    deadline = time.monotonic() + TARGET_BUDGET_SECONDS
    for target in distinct:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return repo_pat
        url = _origin_url(target, timeout=min(10.0, remaining))
        if repo_pat in url:
            return url
    if len(distinct) > 1 and time.monotonic() >= deadline:
        return repo_pat
    return ""


def _origin_url(target: str | None, timeout: float = 10.0) -> str:
    """The target repo's `origin` remote URL, or "" on any failure — no origin configured, `git`
    itself missing, the directory not existing or not a repo, a timeout. All fail open the same
    way the bash guard's `git -C "$target" remote get-url origin 2>/dev/null || true` does: an
    empty origin never matches a non-empty `GUARD_REPO_PATTERN` (see `_conf_get`'s "" vs None
    note — the empty-pattern short-circuit happens well before this is ever called)."""
    if not target:
        return ""
    import subprocess

    try:
        proc = subprocess.run(
            ["git", "-C", target, "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    if proc.returncode != 0:
        return ""
    return proc.stdout.strip()


def _within_window(start: str, end: str, days: str) -> bool:
    """True if right now, local time, falls inside the configured window. Base-10 arithmetic is
    unconditional in Python (unlike bash, where an unguarded `08`/`09` misparses as invalid octal —
    the reason the bash guard forces `10#`), so no equivalent guard is needed here. Any parsing
    failure (a malformed `HHMM` or `D-D` value) is treated as "not in the window" — the safe
    direction, and consistent with this guard's documented "any internal error" fail-open."""
    try:
        now = datetime.now()
        minute_of_day = now.hour * 60 + now.minute
        smin = int(start[0:2]) * 60 + int(start[2:4])
        emin = int(end[0:2]) * 60 + int(end[2:4])
        if "-" in days:
            dlo = int(days.split("-", 1)[0])
            dhi = int(days.rsplit("-", 1)[1])
        else:
            dlo = dhi = int(days)
        return dlo <= now.isoweekday() <= dhi and smin <= minute_of_day < emin
    except (ValueError, IndexError):
        return False


def main() -> int:
    # A deadline this PROCESS owns, armed before anything else so it covers the stdin read and
    # both tokenizer walks. This guard registers no `timeout`, so the harness default binds, and
    # its deadline EXITS 0 — the documented contract here is fail-OPEN on any internal error, so
    # exit 2 would be a new class of block rather than the same verdict sooner. What it buys is a
    # bounded wait and a line saying why, instead of a command that stalls for the whole harness
    # default and is then allowed with nothing printed. See `scripts/lib/guard_deadline.py`.
    #
    # It is armed ABOVE the "no policy file" early return even though that return is the 99.99%
    # path: `guard_deadline` and `settings_hooks` are two small pure-Python modules, nothing like
    # the tokenizer import that return exists to avoid, and a deadline armed after the returns
    # would not cover the reads that reach them.
    sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
    import guard_deadline  # noqa: E402 (deliberately lazy, not top-level)

    guard_deadline.install(HOOK_NAME)

    # INVOCATION CONTRACT, before anything that reads stdin or the policy file (the deadline
    # above it neither reads nor decides) — asserted for every registered hook by
    # scripts/tests/test_hook_argv_refusal.py. Claude Code always invokes this with no arguments
    # and a piped JSON payload, so neither branch below changes any live verdict; they only stop
    # a malformed invocation from reading as a clean pass (argv, stdin at EOF -> exit 0 having
    # examined nothing) or hanging forever (a terminal stdin with no payload coming).
    if len(sys.argv) > 1:
        sys.stderr.write(ARGV_MESSAGE)
        return 2
    if sys.stdin.isatty():
        sys.stderr.write(TTY_MESSAGE)
        return 2

    # Policy file existence, BEFORE reading stdin at all — mirrors the bash guard's own ordering
    # (`[ -f "$conf" ] || exit 0` precedes `input=$(cat)`) so a host with no policy file pays for
    # neither a stdin read nor the tokenizer import that follows further down.
    home = os.environ.get("HOME", "")
    conf_path = os.path.join(home, ".claude", ".git-timing-guard.conf")
    if not os.path.isfile(conf_path):
        return 0  # condition 1: absent -- the documented way to disable

    try:
        raw = sys.stdin.read()
    except OSError:
        return 0
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return 0
    if not isinstance(data, dict):
        return 0
    tool_input = data.get("tool_input")
    command = tool_input.get("command") if isinstance(tool_input, dict) else None
    if not isinstance(command, str) or not command:
        return 0
    payload_cwd = data.get("cwd")
    if not isinstance(payload_cwd, str) or not payload_cwd:
        payload_cwd = None

    try:
        conf_text = Path(conf_path).read_text()
    except OSError:
        conf_text = ""  # condition 2: unreadable -- collapses to the same empty sentinel as absent

    # Empty pattern must exit 0 BEFORE any match is attempted -- "" would match everything below
    # (both a fixed-string `in` and the bash guard's `grep -qF ""`), exactly the same as an absent
    # or misspelled key (conditions 3 and 4): with the pattern unknown this guard cannot tell which
    # repos it governs, so failing closed here would assert jurisdiction over every repo on the
    # machine, triggered by nothing more than a chmod or a typo.
    repo_pat = _conf_get(conf_text, "GUARD_REPO_PATTERN") or ""
    if not repo_pat:
        return 0
    start = _conf_get(conf_text, "GUARD_START") or "0800"
    end = _conf_get(conf_text, "GUARD_END") or "1700"
    days = _conf_get(conf_text, "GUARD_DAYS") or "1-5"

    # LAZY import: only once there is an actual policy to enforce. No `has_git_word` prefilter —
    # see the module docstring's fail-open measurement and memory/2026-09-01... — so this import
    # is the next thing that runs, not gated behind a regex that can itself miss a real push.
    sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
    import git_command as gitcmd  # noqa: E402 (deliberately lazy, not top-level)

    try:
        found, authorized, lost_reading = _find_first_push(gitcmd, command)
    except ValueError:
        # AMBIGUITY POSTURE: fail OPEN, explicitly -- see the module docstring. push-guard.py
        # covers a publish hidden in an unparseable command regardless of what this guard does.
        return 0

    if not found or authorized:
        return 0

    try:
        targets = _push_target_dirs(gitcmd, command, payload_cwd)
    except ValueError:
        return 0

    # The first target whose origin names a guarded repo, else "" — bounded, see `_scope_origin` —
    # so the scope check below reads exactly as it did when only one target was judged.
    origin = _scope_origin(targets, repo_pat)
    if repo_pat not in origin:
        return 0

    if _within_window(start, end, days):
        if lost_reading:
            # The remedy is chosen from WHY the reading was lost. This guard's own primitive
            # (`iter_context_token_streams`) returns no trails, so `guard_ambiguity.cause` walks a
            # second time -- paid only here, on a path already emitting a block, and bounded by
            # the deadline armed at the top of `main`. It is contractually non-raising, which
            # matters more here than anywhere: this guard's whole contract is fail-OPEN, so a
            # raise would exit 0 and the block would silently not happen.
            import guard_ambiguity  # noqa: E402 (lazy, beside the tokenizer it consults)

            sys.stderr.write(
                AMBIGUOUS_READING_MESSAGE.format(
                    remedy=guard_ambiguity.remedy(
                        guard_ambiguity.cause(gitcmd, command)
                    ),
                    tool=_explain_tool_clause(),
                )
            )
        else:
            sys.stderr.write(BLOCK_MESSAGE.format(end=end))
        return 2
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # noqa: BLE001 - deliberate: any OTHER crash must still fail OPEN, matching
        # this guard's documented contract ("0 -- allow ... or any internal error") and condition
        # 5 in the module docstring (python3 itself misbehaving is indistinguishable, from
        # outside, from any other internal error).
        sys.exit(0)

"""A deadline each GUARD PROCESS owns, because the guard process owns the registration.

## The defect

Claude Code kills a hook that outlives its registered `timeout`, **discards its output, and never
tells Claude that it timed out** — so a PreToolUse gate killed at its timeout is SILENT and the
guarded command RUNS. The guard can therefore reach the correct verdict and be killed before it
can say so, which is indistinguishable from the guard allowing the command.

That is not hypothetical here. the shared tokenizer's `MAX_TOTAL_PARSES` docstring (in `scripts/lib/`) records
the measurement: a FLAT bundle of ambiguous quoted heredocs padded to 65,529 chars — one command,
under `MAX_COMMAND_LENGTH` — takes **224.89 s** through `publication-push-guard.py` on this branch
and **172.40 s** on shipped `dev`, against a **60 s** registration. The binding term is the guard's
own per-invocation `subprocess.run` work (8,212 calls on both sides of that fixture), not the parse
cap, so no value of `MAX_TOTAL_PARSES` has ever governed it, and the same docstring files the
remedy as "a deadline owned by the guard process". This module is that deadline.

## Why `os._exit`, and not a raise

The handler must not be catchable. `publication-push-guard.py`'s `opaque_only` path returns **0**
for *any* exception — `AmbiguousCommand` and the internal-error class alike — so a handler that
merely raised would be swallowed by an `except` arm and converted into an ALLOW: the exact
inversion the deadline exists to prevent. `git-timing-guard.py` wraps the whole of `main()` in a
bare `except Exception: sys.exit(0)` for the same reason. `os._exit` bypasses both, plus every
`finally` and `atexit`, which is what makes the exit status the handler's own.

The message is written with `os.write(2, ...)` rather than `print` for the same reason: no
buffering to flush, nothing between the handler and the operator.

## The exit status is per guard, because the guards' postures differ

A deadline does not get to invent a posture. It fires the verdict the guard already reaches when
it cannot judge a command:

* `publication-push-guard.py` — **2**. It is a security boundary and fails CLOSED on anything it
  cannot judge (its module docstring, and `main`'s internal-error arm). A silent kill there turns
  a refusal into a publish.
* `push-guard.py` — **2**. Its ambiguity posture is fail-CLOSED for anything push-shaped ("I could
  not parse it" must not silently become "there is no push here"), and a deadline is exactly that
  sentence about the clock instead of the parser.
* `git-timing-guard.py` — **0**. Its documented contract is fail-OPEN on *any* internal error
  (module docstring, exit-code table, and the bare `except` around `main`), so exit 2 here would
  be a NEW class of block rather than the same verdict sooner. What the deadline buys there is not
  a changed verdict — the silent kill already lands on "allow" — but a bounded wait and a line
  saying why: without it the operator's command stalls for the full harness default with nothing
  printed. Stated plainly because it is the one guard where this module changes no verdict.

## The numbers, and where each came from

`publication-push-guard.py`: **50 s** under a **60 s** `settings.json` registration.

`push-guard.py` and `git-timing-guard.py` register no `timeout`, so the harness default binds —
`settings_hooks.HARNESS_DEFAULT_TIMEOUT_SECS`, 600 s. Their deadline is that default **minus 30 s**,
the margin this repo already uses for the same relationship: `scripts/tests/test_hook_budget.py`
requires every `HOOK_BUDGET_SECS`-declaring hook to sit at least 30 s below its `settings.json`
timeout, and `scripts/HOOKS.md`'s "Bounding a long test-runner hook" is that rule's prose. Derived
from an existing, measured convention rather than picked, and derived from
`HARNESS_DEFAULT_TIMEOUT_SECS` rather than from a hand-copied `600`.

`scripts/settings-hooks-check.py` asserts the relationship these numbers claim — each deadline
strictly BELOW the timeout `settings.json` registers for that guard — reading both sides from
source rather than from this docstring.

## The override

`GUARD_DEADLINE_SECONDS` replaces the deadline for whichever guard reads it. It exists so the
handler can be watched firing in a test without a 50-second test; a deadline that is merely
INSTALLED has never run. Only a POSITIVE number is honoured; anything else is ignored *audibly*
(one line on stderr naming the value and the default used), because a tool that silently discards
an argument it cannot parse answers with its own defaults and the run looks normal.
"""

from __future__ import annotations

import os
import signal
from typing import NamedTuple

from settings_hooks import HARNESS_DEFAULT_TIMEOUT_SECS

DEADLINE_ENV_VAR = "GUARD_DEADLINE_SECONDS"

# The margin below a registration, from `test_hook_budget.py` / HOOKS.md — see the module
# docstring. Applied only where no explicit `timeout` is registered and the harness default binds.
HARNESS_DEFAULT_MARGIN_SECS = 30.0


class GuardDeadline(NamedTuple):
    """One guard's deadline: how long, what exit status, and what to say.

    `seconds` is the only field `settings-hooks-check.py` reads; the rest is wording. `message`
    carries exactly one `%g` conversion, filled with the seconds actually armed (which the
    override can change), never with the table's own value.
    """

    seconds: float
    on_expiry: int
    message: str


# The farm path prefix a `settings.json` registration uses, kept here so the table's KEY and the
# spelling that produces it from a registration sit in one place. `settings_hooks.hook_basename`
# turns a registered command into one of these keys.
FARM_PREFIX = "$HOME/.claude/scripts/"

_FAIL_CLOSED_TAIL = (
    "This is a DEADLINE, not a policy decision about your command: the harness kills a hook at its "
    "registered timeout, discards its output and tells nobody, so the guard stops itself first and "
    "fails closed. Simplify the command (a bundle of quoted heredocs whose last body line ends in "
    "a backslash is the measured worst case), or raise both %s and this hook's timeout in "
    "settings.json." % DEADLINE_ENV_VAR
)

DEADLINES: dict[str, GuardDeadline] = {
    "publication-push-guard.py": GuardDeadline(
        seconds=50.0,
        on_expiry=2,
        message=(
            "publication-push-guard: refusing this git command — the guard reached its own "
            "%gs deadline before it could judge it, so no push was identified and it fails "
            "closed. " + _FAIL_CLOSED_TAIL
        ),
    ),
    "push-guard.py": GuardDeadline(
        seconds=HARNESS_DEFAULT_TIMEOUT_SECS - HARNESS_DEFAULT_MARGIN_SECS,
        on_expiry=2,
        message=(
            "blocked by push-guard: the guard reached its own %gs deadline before it could "
            "judge this command, so it is refused rather than allowed unchecked. "
            + _FAIL_CLOSED_TAIL
        ),
    ),
    "git-timing-guard.py": GuardDeadline(
        seconds=HARNESS_DEFAULT_TIMEOUT_SECS - HARNESS_DEFAULT_MARGIN_SECS,
        on_expiry=0,
        message=(
            "git timing guard: ALLOWING this command unjudged — the guard reached its own %gs "
            "deadline before it could read it. This gate is fail-open on any internal error, so "
            "the deadline changes no verdict; it only stops the wait. push-guard and "
            "publication-push-guard judge this command independently and fail CLOSED."
        ),
    ),
}


def _honoured_seconds(default: float) -> float:
    """`GUARD_DEADLINE_SECONDS` when it is a positive number, else `default` — loudly.

    An ALLOWLIST: only a positive number is honoured. Anything else is reported on stderr rather
    than silently replaced by the default, because a run using parameters nobody chose reads
    exactly like a normal one.
    """
    raw = os.environ.get(DEADLINE_ENV_VAR)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = None
    if value is None or value <= 0:
        os.write(
            2,
            (
                "guard deadline: ignoring %s=%r (not a positive number of seconds); using %gs.\n"
                % (DEADLINE_ENV_VAR, raw, default)
            ).encode("utf-8", "replace"),
        )
        return default
    return value


def install(hook_name: str) -> float | None:
    """Arm this guard's deadline. Returns the seconds armed, or None if there is nothing to arm.

    Call it FIRST in `main()`, before reading stdin: everything after this point is then covered,
    including the tokenizer walks that are the measured cost.

    Non-raising by contract, like every other diagnostic path in these guards: an exception
    escaping here would exit 1, which `scripts/HOOKS.md` treats as noise rather than a veto — the
    guarded command would RUN. A failure to arm is reported on stderr instead, because a deadline
    that silently failed to install reads exactly like one that is working.
    """
    spec = DEADLINES.get(hook_name)
    if spec is None:
        return None
    seconds = _honoured_seconds(spec.seconds)
    text = (spec.message % seconds).replace("\n", " ") + "\n"

    def _fire(_signum, _frame):
        # Async-signal-safe-ish and uncatchable: no buffering to flush, no `except` arm between
        # the handler and the exit status. See the module docstring.
        os.write(2, text.encode("utf-8", "replace"))
        os._exit(spec.on_expiry)

    try:
        signal.signal(signal.SIGALRM, _fire)
        signal.setitimer(signal.ITIMER_REAL, seconds)
    except Exception as exc:  # noqa: BLE001 - a broken deadline must never change a verdict
        os.write(
            2,
            (
                "guard deadline: could NOT arm a %gs deadline for %s (%s: %s) — this hook can "
                "still be killed silently at its registered timeout.\n"
                % (seconds, hook_name, exc.__class__.__name__, exc)
            ).encode("utf-8", "replace"),
        )
        return None
    return seconds

"""Bash differential for scripts/lib/git_command.py: does the tokenizer see what bash really ran.

Generates random small shell scripts (quotes and `$( )`/backtick substitutions spanning lines,
mid-word splits, comments, `$'...'`, and every heredoc shape -- quoted/unquoted/`<<-`/two-on-one-
line/nested-in-`$( )`/nested-in-backticks/nested-in-an-unquoted-body), runs each under every bash
present (`/bin/bash`, `/opt/local/bin/bash`) with a fake `git` first on PATH that only logs its argv
(subcommand `status`/`log`, never a write), and compares bash's real git invocations against
`iter_git_invocations_detailed`'s. An invocation is COVERED (the rule this differential measures
against) when the tokenizer recorded the same subcommand and
every bash argument at the same position; a tokenizer argument carrying a substitution placeholder
(`git_command.PLACEHOLDER_PREFIX`/`_PLACEHOLDER_RE`) or a `$` that STARTS an expansion is DYNAMIC
-- its literal prefix must match and nothing after it is constrained (expansion and word
splitting) -- and extra trailing tokenizer arguments are over-read (the guards judge more, never
less). Only invocations real git could act on count: subcommand `status`/`log`, no argument
holding a backslash, whitespace, CR or LF (git rejects those as refs, so a difference there can
never carry a real write). `hidden_valid` is a subcommand bash ran that the tokenizer recorded
nowhere at all; `hidden_valid_full` is one the tokenizer recorded the right subcommand for but no
COVERED invocation -- both are the fail-open shape a push guard would miss. A `TimeoutExpired`
counts as INDETERMINATE rather than a verdict, and fails the run only when more than half the bash
invocations time out.

Ported from the oracle script this change was measured against -- its generator, fake-git argv
logger, COVERED rule and git-valid filter are carried over unchanged, not re-derived. NOBARECR is
fixed ON here (it was an
opt-in env var upstream): CRLF is allowed on command and heredoc-body lines; the heredoc operator
and terminator lines are LF-only, as they always were in the source generator. Two residuals are
therefore deliberately NOT covered: a bare CR (not CRLF) anywhere, and a CR specifically inside a
heredoc operator or terminator line (LF-only there is the generator's own hard-coded choice, not a
NOBARECR effect) -- both are pre-existing and independent of folding, filed separately per the
spec's "Residual, stated" paragraph.

Default: ONE seed (20260918) x 600 scripts. With env `GIT_COMMAND_DIFFERENTIAL_FULL=1`: all 8
seeds (7, 11, 23, 41, 57, 73, 89, 97) x 600 scripts each -- the controller runs that mode under
`run-long.sh` before integrating; the plan's measured budget is ~38s wall for one seed x 600
scripts x 2 bashes, subprocess-bound.

A SECOND, separate generator (`generate_desync_scripts`, its own seeds) supplies the population
this one structurally cannot: `generate_scripts` only ever emits WELL-FORMED text, so it cannot
produce the quote/comment-model DESYNC shapes -- `$'\\''`, a `#` inside backticks, a backtick
inside double quotes -- that one of this branch's two measured bypasses lived in. It is a separate
generator and not a branch in `piece()` on purpose: `piece()` draws from one seeded stream, so an
extra branch changes every script of every existing seed from its first draw on, silently moving
the baseline population other rows are measured against.

Measured RED numbers (past tense), `run_differential` against `dev`'s `git_command.py` (`git show
dev:scripts/lib/git_command.py`, `dev` @ `522b499`, copied to a scratch directory outside this
repo and deleted afterward -- never committed here): seed 20260918 x 600 scripts x 2 bashes gave
hidden_valid=89 hidden_valid_full=4 (raised=270, extra_overread=353); seed 7 x 600 scripts x 2
bashes gave hidden_valid=107 hidden_valid_full=10 (raised=312, extra_overread=363) -- 93 and 117
hidden total, both inside the measured 92-137-per-seed range for the prior, narrower generator.
Both go to hidden_valid=0 hidden_valid_full=0 against the tokenizer as changed on 2026-09-18.
Those numbers predate the backticks-nesting branch (added 2026-09-19), which draws from the same
RNG stream and so changed every script from its first heredoc piece on; they are not comparable
with runs of the current generator.

One further departure from a plain byte-for-byte port, also measured rather than assumed: the
multiset-assignment step that pairs each real bash invocation with a covering tokenizer one
(`_pick_covering_index`) prefers an EXACT-length pairing over one that only covers via the
"extra trailing tokenizer arguments are over-read" allowance -- the oracle script's own greedy
`next(... first match ...)` scored a spurious hidden_valid_full=2 at seed 20260918 (script #357)
against this SAME fixed tokenizer, even though bash's and the tokenizer's invocation multisets
were byte-identical there; see `_pick_covering_index`'s docstring for the full measurement. The
per-pair COVERED predicate (`_covers`) itself is unchanged.
"""

import os
import random
import re
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import git_command  # noqa: E402, I001

BASHES = [b for b in ("/bin/bash", "/opt/local/bin/bash") if os.path.exists(b)]
pytestmark = pytest.mark.skipif(
    not BASHES, reason="neither /bin/bash nor /opt/local/bin/bash is present"
)

DEFAULT_SEED = 20260918
FULL_SEEDS = (7, 11, 23, 41, 57, 73, 89, 97)
SCRIPTS_PER_SEED = 600
_BS = "\\"

# A `$` that STARTS an expansion (not merely appears later in the token) -- ported unchanged from
# the oracle script's `_covers`.
_DOLLAR_STARTS_EXPANSION_RE = re.compile(r"\$[A-Za-z_{(0-9@*#?$!-]")


def _make_script_generator(seed):
    """Return a `script()` callable drawing from one seeded RNG stream.

    Ported from the oracle script's `piece()`/`script()`, unchanged, with NOBARECR fixed ON (see
    module docstring): `eol()` never returns a bare CR, only LF or CRLF.
    """
    rng = random.Random(seed)

    def run():
        return _BS * rng.choice([0, 0, 1, 1, 2, 2, 3, 4])

    def eol():
        return "\r\n" if rng.random() < 0.15 else "\n"

    def simple():
        return rng.choice(
            [
                "echo x",
                "git status",
                "git log -1 dev",
                "true",
                "echo git status",
                "git log -1 featurebranch",
                "echo a b",
            ]
        )

    def piece():
        """One logical chunk; may span several physical lines."""
        k = rng.random()
        if k < 0.30:
            return simple() + run() + eol()
        if k < 0.42:  # mid-word split of a git word or arg
            a, b = rng.choice(
                [
                    ("git sta", "tus"),
                    ("git log -1 de", "v"),
                    ("gi", "t status"),
                    ("git log -1 feature", "branch"),
                    ("git log -1 'de", "v'"),
                ]
            )
            return a + run() + eol() + b + run() + eol()
        if k < 0.52:  # single quotes spanning lines
            return (
                "echo '"
                + rng.choice(["a", "git status", "it"])
                + run()
                + eol()
                + rng.choice(["'", "' ; git status", "x'; git log -1 dev"])
                + eol()
            )
        if k < 0.62:  # double quotes spanning lines
            return (
                'echo "'
                + rng.choice(["a", "git status"])
                + run()
                + eol()
                + rng.choice(['"', '" ; git status', 'x"; git log -1 dev'])
                + eol()
            )
        if k < 0.70:  # $( ) spanning lines
            return (
                "x=$("
                + simple()
                + run()
                + eol()
                + rng.choice([")", "git status)", "true)"])
                + eol()
            )
        if k < 0.78:  # backticks spanning lines
            return (
                "x=`"
                + simple()
                + run()
                + eol()
                + rng.choice(["`", "git status`", "tus`"])
                + eol()
            )
        if k < 0.84:  # comments
            return simple() + " # c" + run() + eol() + simple() + run() + eol()
        if (
            k < 0.86
        ):  # a double backslash directly before an opener, in/out of quotes and bodies
            opener = rng.choice(
                ["$(git status)", "`git log -1 dev`", "$(echo git) status"]
            )
            form = rng.choice(
                [
                    'echo "{bs}{op}"',
                    "echo {bs}{op}",
                    'bash <<EOF\necho "{bs}{op}"\nEOF',
                    "bash <<EOF\necho {bs}{op}\nEOF",
                ]
            )
            return form.format(bs=_BS * rng.choice([1, 2, 3, 4]), op=opener) + "\n"
        if k < 0.87:  # a heredoc nested inside an unquoted body
            inner = simple() + run() + "\n" + rng.choice(["tus", "true", "EOF2"]) + "\n"
            return "bash <<EOF\nbash <<EOF2\n" + inner + "EOF2\nEOF\n"
        if k < 0.88:  # $'...'
            return (
                "echo $'a" + run() + eol() + rng.choice(["'", "' ; git status"]) + eol()
            )
        # heredocs
        delim = rng.choice(["'EOF'", "EOF", '"EOF"', "\\EOF", "E'OF'"])
        dash = rng.random() < 0.25
        op = "<<-" if dash else "<<"
        body = [
            (
                simple()
                if rng.random() < 0.8
                else rng.choice(
                    ["echo it's", 'echo "a', "echo \\$HOME", "echo `echo x`"]
                )
            )
            + run()
            + eol()
            for _ in range(rng.randint(1, 3))
        ]
        if rng.random() < 0.15:
            body.append(
                _BS * rng.choice([1, 2, 3]) + eol()
            )  # last line: backslashes only
        # the operator and terminator lines use LF only: a CR there is the stated bare-CR residual
        term = ("\t" if dash else "") + "EOF" + "\n"
        h = f"bash {op}{delim}" + "\n" + "".join(body) + term
        r = rng.random()
        if r < 0.15:  # two heredocs on one line; bash reads the LAST one as stdin
            b2 = [simple() + run() + eol() for _ in range(rng.randint(1, 2))]
            h = (
                f"bash <<'A' {op}{delim}"
                + "\n"
                + "".join(b2)
                + "A"
                + "\n"
                + "".join(body)
                + term
            )
        elif r < 0.30:  # heredoc inside a substitution
            h = "x=$(" + h + ")" + eol()
        elif r < 0.45:  # heredoc inside backticks
            if delim != "EOF" and rng.random() < 0.5:
                # a QUOTED body whose last line ends in a backslash run right before the
                # terminator (inside `$( )` bash 3.2 and 5.3 read this shape differently). Bash
                # backslash-processes backtick text before running it, so the run the inner bash
                # sees is not the run written here; runs 1..5 cover both parities, and what each
                # does is bash's answer, never this generator's. Measured 2026-09-19 with a lone
                # `git log -1 dev<run>` line: runs 1 and 3 joined the terminator (`devEOF`), 5
                # gave `dev\EOF`, 2 and 4 did not join -- identically under both bashes
                body[-1] = (
                    rng.choice(
                        [
                            "git status",
                            "git status origin ",
                            "git log -1 dev",
                            "git log -1 dev ",
                        ]
                    )
                    + _BS * rng.choice([1, 2, 3, 4, 5])
                    + eol()
                )
                h = f"bash {op}{delim}" + "\n" + "".join(body) + term
            h = "x=`" + h + "`" + eol()
        return h

    def script():
        s = "".join(piece() for _ in range(rng.randint(1, 4)))
        if rng.random() < 0.08:  # backslash at end of input, no newline
            s = s.rstrip("\r\n") + run()
        return s

    return script


def generate_scripts(seed, n=SCRIPTS_PER_SEED):
    """`n` scripts drawn from one seeded RNG stream, in the order the oracle produced them."""
    script = _make_script_generator(seed)
    return [script() for _ in range(n)]


# --- the desync generator: the population `generate_scripts` structurally cannot reach ---------

DESYNC_DEFAULT_SEED = 20260919
DESYNC_FULL_SEEDS = (13, 29, 37, 53)
DESYNC_SCRIPTS_PER_SEED = 150

# Detectors for the three shapes, run over the GENERATED TEXT rather than over a hand-copy of the
# generator's own branch list -- a copied list goes stale silently, and the point of these is to
# fail loudly if a branch ever stops emitting the shape it exists for. They are presence tests per
# script, deliberately not exact parses:
#   `$'\''`             an escaped quote inside `$'...'`, which the mask reads as a plain `'`
#   backtick then `#`   a `#` inside a backtick span (bash comments it; the mask's word-start set
#                       excludes the position right after a backtick, so the two disagree about
#                       where the span ends)
#   `"` then backtick   a backtick inside a double-quoted run on one line
_DESYNC_SHAPE_RES = {
    "dollar_quote_escaped_quote": re.compile(r"\$'\\'"),
    "hash_inside_backticks": re.compile(r"`[^`]*#"),
    "backtick_inside_double_quotes": re.compile(r'"[^"\n]*`'),
}


def count_desync_shapes(scripts):
    """How many of `scripts` carry each desync shape -- the non-vacuity reading for the generator.

    A generator branch that emits none of the shape it exists for reads exactly like a clean pass,
    so this is asserted, not merely printed.
    """
    return Counter(
        name
        for text in scripts
        for name, pattern in _DESYNC_SHAPE_RES.items()
        if pattern.search(text)
    )


def _make_desync_script_generator(seed, ambiguous):
    """Return a `script()` callable for the desync corpus, drawing from one seeded RNG stream.

    `ambiguous` decides ONE thing: whether the heredoc's last body line keeps the backslash run
    that makes its continuation ambiguous. The run is drawn from the RNG either way, so the
    `ambiguous=True` and `ambiguous=False` corpora of the same seed are TWINS -- script `i` of one
    differs from script `i` of the other only in that run. That pairing is what lets the
    continuation dimension be separated from the desync dimension: a script that loses an
    invocation in BOTH corpora lost it to the quote/comment model (out of scope here, and pinned
    against the pre-change baseline instead), while one that loses it only in the ambiguous corpus
    lost it to the continuation reading, which is exactly what this change must not do.
    """
    rng = random.Random(seed)

    def bsrun():
        return _BS * rng.choice([1, 1, 2, 3])

    def simple():
        return rng.choice(
            ["git status", "git log -1 dev", "git log -1 main dev", "echo x", "true"]
        )

    def gitline():
        return rng.choice(
            [
                "git status",
                "git log -1 dev",
                "git log -1 main dev",
                "git log -1 feature",
            ]
        )

    def heredoc():
        # every delimiter here is QUOTED, which is what makes a final backslash run ambiguous
        delim = rng.choice(["'EOF'", '"EOF"', _BS + "EOF", "E'OF'"])
        body = [simple() + "\n" for _ in range(rng.randint(0, 2))]
        last = gitline()
        tail = bsrun()  # ALWAYS drawn, so the two corpora stay twin-aligned
        body.append(last + (tail if ambiguous else "") + "\n")
        return "bash <<" + delim + "\n" + "".join(body) + "EOF\n"

    def in_context(h):
        """Top level, inside `$( )`, inside backticks -- the three contexts whose readings differ
        (measured: top level DROPs in both bashes, `$( )` JOINs in 3.2 and DROPs in 5.3, backticks
        JOIN in both)."""
        k = rng.random()
        if k < 0.34:
            return h
        if k < 0.67:
            return "x=$(" + h + ")\n"
        return "x=`" + h + "`\n"

    def dollar_quote():
        return rng.choice(
            [
                "echo $'" + _BS + "''\n",
                "echo $'a" + _BS + "''b'\n",
                "x=$'" + _BS + "''" + _BS + "''\n",
                "echo $'`'\n",
                "echo $'" + _BS + "''; " + gitline() + "\n",
            ]
        )

    def hash_in_backticks():
        return rng.choice(
            [
                "y=`echo a #c`\n",
                "y=`echo a #c\n" + simple() + "`\n",
                "y=`#c`\n",
                "y=`" + simple() + " # `\n",
                "y=`" + simple() + "\n#c\n" + simple() + "`\n",
            ]
        )

    def backtick_in_dquotes():
        return rng.choice(
            [
                'echo "a`echo x`b"\n',
                'echo "`' + simple() + '`"\n',
                'echo "#`echo x`"\n',
                'echo "' + _BS + '`" `echo y`\n',
            ]
        )

    def desync():
        return rng.choice([dollar_quote, hash_in_backticks, backtick_in_dquotes])()

    def script():
        parts = [desync()]
        for _ in range(rng.randint(0, 1)):
            parts.append(desync())
        # exactly one heredoc per script, placed before, between or after the desync fragments
        parts.insert(rng.randint(0, len(parts)), in_context(heredoc()))
        return "".join(parts)

    return script


def generate_desync_scripts(seed, n=DESYNC_SCRIPTS_PER_SEED, *, ambiguous):
    """`n` desync-shape scripts from one seeded RNG stream; see `_make_desync_script_generator`."""
    script = _make_desync_script_generator(seed, ambiguous)
    return [script() for _ in range(n)]


def _write_fake_git(fakebin):
    """A fake `git` that logs its argv (fields `\\x1f`-separated, each call `\\x1e`-terminated) and
    exits 0 -- ported unchanged from the oracle script. It never performs any git operation, and
    the generator above never asks for a write subcommand."""
    git_path = os.path.join(fakebin, "git")
    with open(git_path, "w") as fh:
        fh.write(
            '#!/bin/sh\nfor a in "$@"; do printf "%s\\037" "$a"; done >> "$GITLOG"\n'
            'printf "\\036" >> "$GITLOG"\n'
        )
    os.chmod(git_path, 0o755)


def _bash_invocations(bash, text, work, fakebin, log_path):
    """Run `text` under `bash` with the fake `git` first on PATH, and return the git invocations
    it made as a `Counter` of `(subcommand, *args)` tuples -- or `None` when the run timed out
    (INDETERMINATE, never a verdict)."""
    open(log_path, "w").close()
    script_path = os.path.join(work, "s.sh")
    with open(script_path, "w", newline="") as fh:
        fh.write(text)
    env = {"PATH": fakebin + ":/usr/bin:/bin", "GITLOG": log_path}
    try:
        subprocess.run(
            [bash, script_path],
            env=env,
            capture_output=True,
            timeout=20,
            cwd=work,
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired:
        return None
    raw = open(log_path, newline="").read()
    calls = [c for c in raw.split("\x1e") if c]
    return Counter(
        tuple(a.replace("\r", " ") for a in c.split("\x1f")[:-1]) for c in calls
    )


def _tokenizer_invocations(gc_module, text, work):
    """`gc_module`'s own reading of `text`, in the same shape as `_bash_invocations` -- or the
    string `"RAISED <message>"` when it fails closed."""
    try:
        invs = gc_module.iter_git_invocations_detailed(text, work)
    except ValueError as exc:
        return f"RAISED {exc}"
    return Counter((inv.subcommand, *inv.arg_tokens) for inv in invs)


def _valid(call):
    """Only a tuple real git could act on: subcommand status/log, no argument holding a
    backslash, whitespace, CR or LF -- git rejects those as refs, so a difference there can never
    carry a real write. Ported unchanged from the oracle script."""
    return (
        call
        and call[0] in ("status", "log")
        and not any(c in a for a in call for c in ("\\", " ", "\r", "\n", "\t"))
    )


def _arg_covers(tok_arg, bash_arg, placeholder_re):
    if placeholder_re.search(tok_arg):
        fixed = placeholder_re.split(tok_arg)
        return bash_arg.startswith(fixed[0]) and bash_arg.endswith(fixed[-1])
    return tok_arg == bash_arg


def _covers(tok_call, bash_call, placeholder_re):
    """The COVERED rule: same subcommand; every bash argument at the same position; a tokenizer
    argument carrying a substitution placeholder or a `$` that STARTS an expansion is DYNAMIC --
    its literal prefix must match and nothing after it is constrained (expansion and word
    splitting); extra trailing tokenizer arguments are over-read (the guards judge more, never
    less). Ported unchanged from the oracle script's `_covers`; `placeholder_re` is
    `git_command._PLACEHOLDER_RE`, never a re-escaped copy of `PLACEHOLDER_PREFIX`.
    """
    if tok_call[0] != bash_call[0]:
        return False
    dynamic_re = re.compile(
        f"{_DOLLAR_STARTS_EXPANSION_RE.pattern}|{placeholder_re.pattern}"
    )
    for idx in range(1, len(bash_call)):
        if idx >= len(tok_call):
            return False
        tok_arg = tok_call[idx]
        if placeholder_re.search(tok_arg) or _DOLLAR_STARTS_EXPANSION_RE.search(
            tok_arg
        ):
            # an unquoted substitution may expand into ANY number of words: its prefix must
            # match, and nothing after it is constrained
            prefix = dynamic_re.split(tok_arg)[0]
            return bash_call[idx].startswith(prefix)
        if not _arg_covers(tok_arg, bash_call[idx], placeholder_re):
            return False
    return True


def _pick_covering_index(pool, bash_call, placeholder_re):
    """The pool index to consume for `bash_call`, preferring an EXACT-length covering candidate
    over one that only covers via the "extra trailing tokenizer arguments are over-read"
    allowance.

    This is a deliberate, measured departure from the oracle script's own assignment step, which
    just took `next(... first _covers match ...)`. That plain greedy pick can consume the ONE
    exact-length pool entry a DIFFERENT, later bash call needed, stranding that later call with no
    partner left even though the two invocation multisets are byte-identical -- a false
    `hidden_valid_full`, not a real gap. Measured: seed 20260918, script #357 (a `<<-EOF` unquoted
    heredoc whose last body line's odd backslash run swallows the terminator, giving bash the real
    invocation `git status EOF` alongside a separate `git status`) -- bash's and the tokenizer's
    invocation Counters were EQUAL as multisets, `git show dev:...` unchanged, yet greedy
    first-match scored `hidden_valid_full=2` because `('status',)` matched `('status', 'EOF')`
    (an over-read cover, `idx` range is empty so nothing after the subcommand is checked) before
    the true `('status',)` pool entry was tried, leaving `('status', 'EOF')` with no exact partner.
    Reproduced identically running the unmodified oracle script directly against this
    same tokenizer build, so this is a property of the ORIGINAL oracle's assignment step, not of
    this port. The per-pair `_covers` predicate itself -- the COVERED rule's actual definition --
    is untouched; only which pool entry a match consumes is improved, and only toward preferring
    an exact pairing when one exists.
    """
    exact = None
    first_any = None
    for k, tok_call in enumerate(pool):
        if _covers(tok_call, bash_call, placeholder_re):
            if first_any is None:
                first_any = k
            if len(tok_call) == len(bash_call):
                exact = k
                break
    return exact if exact is not None else first_any


@dataclass
class DifferentialResult:
    """Aggregated outcome of one `run_differential` call.

    Attributes:
        scripts: Total scripts generated (n per seed, summed over `seeds`).
        runs: Total bash subprocess invocations attempted (`scripts * len(bashes)`).
        indeterminate: Of `runs`, how many timed out (`subprocess.TimeoutExpired`).
        hidden_valid: Scripts where bash ran a status/log subcommand the tokenizer recorded
            nowhere at all.
        hidden_valid_full: Scripts where the tokenizer recorded the right subcommand but no
            invocation the COVERED rule accepts as matching.
        raised: Bash runs where the tokenizer raised (fail-closed) while bash ran git.
        extra_overread: Bash runs where the tokenizer recorded an invocation bash did not run
            (e.g. a heredoc body scanned as command text by design) -- informational only.
        git_call_count: Total real git invocations bash made, across every run that did not time
            out -- the non-vacuity denominator for BASH's side.
        measured: Bash runs where bash ran git AND the tokenizer returned a reading -- i.e. the
            runs the hidden counters were actually computed over. `git_call_count` cannot stand in
            for it: it counts bash's invocations and is blind to the tokenizer, so a build that
            raised on every single script would leave it untouched while `hidden_valid` and
            `hidden_valid_full` both read 0. `measured + raised` is the number of runs where bash
            ran git at all.
        first_offense: `(script_text, bash_invocations, tokenizer_invocations)` for the first
            `hidden_valid` or `hidden_valid_full` case found, or `None`.
        offenders: `(seed, index-within-seed)` for every script that scored `hidden_valid` or
            `hidden_valid_full` under any bash. The COUNTS alone cannot be compared across two
            runs -- measured here: the pre-change module and the changed one both scored
            `hidden_valid=2 hidden_valid_full=2` on the same corpus while offending on DIFFERENT
            scripts, so a count-only comparison reads as agreement and hides the whole difference.
    """

    scripts: int
    runs: int
    indeterminate: int
    hidden_valid: int
    hidden_valid_full: int
    raised: int
    extra_overread: int
    git_call_count: int
    first_offense: tuple[str, dict, dict] | None
    offenders: frozenset[tuple[int, int]] = frozenset()
    measured: int = 0


def run_differential(gc_module, seeds, n, bashes, work, generator=generate_scripts):
    """Run the bash differential against `gc_module`, for `n` scripts per seed in `seeds`, under
    every bash in `bashes`, using `work` (which must exist) to hold the fake `git` and the scratch
    script bash reads.

    `work` must be an ABSOLUTE path: bash runs with `cwd=work` and with the fake `git`'s directory
    first on `PATH`, so a relative `work` makes that PATH entry resolve under itself, no fake git
    is found, and every counter reads 0 -- a flawless-looking pass over a probe that never reached
    the subject. Measured while building the desync corpus.

    `gc_module` is a parameter rather than the module-level `git_command` import specifically so
    this same core can be re-run against another build of `git_command.py` -- e.g. `dev`'s, copied
    into a scratch directory -- without duplicating the generator or the COVERED rule (see the
    module docstring's measured RED numbers). `generator` is a parameter for the same reason, so
    the desync corpus reuses this core rather than a second copy of the COVERED rule.
    """
    fakebin = os.path.join(work, "bin")
    os.makedirs(fakebin, exist_ok=True)
    log_path = os.path.join(work, "git.log")
    _write_fake_git(fakebin)
    placeholder_re = gc_module._PLACEHOLDER_RE

    scripts = runs = indeterminate = 0
    hidden_valid = hidden_valid_full = raised = extra_overread = measured = 0
    git_call_count = 0
    first_offense = None
    offenders: set[tuple[int, int]] = set()

    for seed in seeds:
        for index, text in enumerate(generator(seed, n)):
            scripts += 1
            tok = _tokenizer_invocations(gc_module, text, work)
            for bash in bashes:
                runs += 1
                real = _bash_invocations(bash, text, work, fakebin, log_path)
                if real is None:
                    indeterminate += 1
                    continue
                git_call_count += sum(real.values())
                if isinstance(tok, str):
                    if real:
                        raised += 1
                    continue
                if real:
                    measured += 1
                real_subs = Counter(c[0] if c else "" for c in real.elements())
                tok_subs = Counter(c[0] for c in tok.elements())
                valid_missing = [
                    c
                    for c in (real_subs - tok_subs).elements()
                    if c in ("status", "log")
                ]
                pool = list(tok.elements())
                valid_full_missing = []
                for bash_call in real.elements():
                    hit = _pick_covering_index(pool, bash_call, placeholder_re)
                    if hit is not None:
                        pool.pop(hit)
                    elif _valid(bash_call):
                        valid_full_missing.append(bash_call)
                if valid_missing:
                    hidden_valid += 1
                    offenders.add((seed, index))
                    if first_offense is None:
                        first_offense = (text, dict(real), dict(tok))
                elif valid_full_missing:
                    hidden_valid_full += 1
                    offenders.add((seed, index))
                    if first_offense is None:
                        first_offense = (text, dict(real), dict(tok))
                if tok - real:
                    extra_overread += 1

    return DifferentialResult(
        scripts=scripts,
        runs=runs,
        indeterminate=indeterminate,
        hidden_valid=hidden_valid,
        hidden_valid_full=hidden_valid_full,
        raised=raised,
        extra_overread=extra_overread,
        git_call_count=git_call_count,
        first_offense=first_offense,
        offenders=frozenset(offenders),
        measured=measured,
    )


def test_bash_matches_tokenizer_on_generated_scripts(tmp_path):
    """The load-bearing property: every status/log git invocation bash actually runs must be
    COVERED by the tokenizer's own reading, over scripts spanning quotes, substitutions,
    comments, CRLF, mid-word splits, and every heredoc shape `generate_scripts` produces.

    Default: one seed x 600 scripts. `GIT_COMMAND_DIFFERENTIAL_FULL=1` widens this to all 8
    pinned seeds x 600 scripts each (the plan's full-coverage run; the controller runs that mode
    under `run-long.sh`, not inline in a normal suite pass).
    """
    full = os.environ.get("GIT_COMMAND_DIFFERENTIAL_FULL") == "1"
    seeds = FULL_SEEDS if full else (DEFAULT_SEED,)
    # The READ denominator, and why it is not `git_call_count`. A tokenizer raise short-circuits
    # both hidden counters (`run_differential` scores `raised` and moves on), so a build that
    # raised on every script scores hidden_valid=0 hidden_valid_full=0 -- and `git_call_count`,
    # which counts BASH's invocations, would not move at all. The floors below are the pre-change
    # module's own `measured` (the PRE-CHANGE tokenizer, vendored at
    # `scripts/tests/fixtures/pre-variants/git_command.py`, sha256 2d0e92e3...4dd97ef1b9): 941 of
    # 1200 runs on the default seed (raised=6), 7495 of 9600 on the 8 full seeds (raised=52).
    # `measured + raised` is a property of the CORPUS, not of the build -- it is the run count
    # where bash ran git at all -- so only `raised` can move these, and this change can only
    # lower it (it raises solely when every reading raises). A run below the floor therefore
    # means the tokenizer stopped reading scripts it used to read; investigate it, do not lower
    # the number.
    measured_floor = 7495 if full else 941
    result = run_differential(
        git_command, seeds, SCRIPTS_PER_SEED, BASHES, str(tmp_path)
    )

    if result.indeterminate:
        print(
            f"indeterminate (timed-out) bash runs: {result.indeterminate}/{result.runs}"
        )
    assert result.indeterminate <= result.runs / 2, (
        f"{result.indeterminate}/{result.runs} bash runs timed out -- more than half, so this "
        "run is INDETERMINATE about the tokenizer, not a verdict on it"
    )

    if result.first_offense is not None:
        script_text, bash_calls, tok_calls = result.first_offense
        print(f"first offending script: {script_text!r}")
        print(f"bash invocations: {bash_calls}")
        print(f"tokenizer invocations: {tok_calls}")

    assert result.hidden_valid == 0, (
        f"hidden_valid={result.hidden_valid}: bash ran a status/log invocation whose subcommand "
        "the tokenizer recorded nowhere at all -- see the printed offense above"
    )
    assert result.hidden_valid_full == 0, (
        f"hidden_valid_full={result.hidden_valid_full}: bash ran a status/log invocation the "
        "COVERED rule does not accept as matched by anything the tokenizer recorded -- see the "
        "printed offense above"
    )
    assert result.git_call_count >= 1000, (
        f"only {result.git_call_count} git invocations ran across {result.runs} bash runs "
        f"({result.scripts} scripts) -- too few for this to be a non-vacuous differential"
    )
    print(f"measured={result.measured} raised={result.raised} runs={result.runs}")
    assert result.measured >= measured_floor, (
        f"the two hidden counters above were computed over only {result.measured} bash runs "
        f"(raised={result.raised}) -- below the pre-change module's own {measured_floor}, so "
        "their 0s cover fewer scripts than they did before this change rather than being clean"
    )


# Measured ONCE against the PRE-CHANGE tokenizer -- the module as it
# stood before the reading-variant change (vendored at the fixture path above, sha256
# 2d0e92e38973b7eabd3bb43d657cdab0d0a49d24adadfc1660ce44d7697ef1b9, loaded from a scratch
# directory outside this repo and deleted afterward) -- and written down rather than re-derived.
#
# Why literals and not "0": the quote/comment model's own divergences from bash are explicitly OUT
# OF SCOPE for this change (the spec: fixing one relocates the failure to the next). Requiring 0
# here would be unsatisfiable, and the only way to reach it is to constrain the generator until the
# number reads 0 -- which is the generator not producing the shape it exists for.
#
# Why `raised` is pinned at all: `run_differential` counts a tokenizer raise as `raised` and moves
# on, never reaching the hidden counters. Two of the three desync shapes RAISE in both builds
# (measured: `$'\\''` -> `unbalanced quote`; a `#` whose backtick span the mask closes early ->
# `unterminated backtick substitution`), so a gate written only on `hidden_valid_full == 0` would
# read 0 over a corpus it never measured. `raised` moving is the tell.
_DESYNC_BASELINE = {
    # mode -> the pre-change module's own numbers over the same corpus
    "default": {
        "seeds": (DESYNC_DEFAULT_SEED,),
        "plain_raised": 106,
        "ambiguous_raised": 106,
        "plain_hidden_valid": 4,
        "plain_hidden_valid_full": 0,
        "plain_offenders": frozenset(
            {(DESYNC_DEFAULT_SEED, 20), (DESYNC_DEFAULT_SEED, 60)}
        ),
        # Continuation-dimension losses ALLOWED here -- none. The pre-change module lost ONE
        # (script 48, printed below as the control): `y=`echo a #c`` makes the mask close the
        # backtick span early, so a top-level heredoc is judged as sitting INSIDE backticks, read
        # JOIN-only, and `git log -1 dev` -- which both bashes really run -- is recorded as
        # `git log -1 devEOF` and nothing covers it. Recording both readings closes it. So this
        # gate is RED against the pre-change module and GREEN against the changed one, on the
        # default seed, which is the control proving it can fail at all.
        "continuation_allowed": frozenset(),
        "continuation_pre_change": frozenset({(DESYNC_DEFAULT_SEED, 48)}),
        "git_call_floor": 400,
    },
    "full": {
        "seeds": DESYNC_FULL_SEEDS,
        "plain_raised": 424,
        "ambiguous_raised": 424,
        "plain_hidden_valid": 4,
        "plain_hidden_valid_full": 0,
        "plain_offenders": frozenset({(53, 31), (53, 107)}),
        # (53, 27) is allowed because it is NOT a reading defect and is IDENTICAL in both builds:
        # two `#`-in-backtick fragments each swallow a closing backtick, so the mask puts the
        # backtick SPAN in the wrong place, the extracted backtick text is therefore never
        # backslash-halved, and the body's `\\` stays an EVEN run -- unambiguous by the rule, so
        # no variant is considered at all and the tokenizer keeps `dev\` where both bashes drop to
        # `dev`. That is the quote/comment model deciding where contexts split, which this change
        # deliberately does not touch. It is listed, not excluded from the corpus, because a
        # generator narrowed until the number reads 0 measures nothing.
        "continuation_allowed": frozenset({(53, 27)}),
        "continuation_pre_change": frozenset({(53, 27)}),
        "git_call_floor": 1600,
    },
}


def test_desync_shapes_are_read_no_worse_than_before(tmp_path):
    """The population `generate_scripts` cannot reach: quote/comment-model DESYNC shapes.

    `generate_scripts` emits only WELL-FORMED text, so it structurally cannot produce the shapes
    one of this branch's two measured bypasses lived in -- where the module's own quote/comment
    model disagrees with bash about which context a heredoc sits in. This row supplies them.

    Two corpora, TWIN-ALIGNED (see `_make_desync_script_generator`): the same scripts with and
    without the backslash run that makes the heredoc's continuation ambiguous. That pairing is the
    whole point, because it separates the two dimensions:

    * **The desync dimension** -- a script that loses an invocation in BOTH corpora lost it to the
      quote/comment model. Out of scope for this change, so it is PINNED against the pre-change
      baseline rather than required to be 0.
    * **The continuation dimension** -- a script that loses one ONLY in the ambiguous corpus lost
      it to the drop-vs-join reading, which is exactly what this change exists to stop. That set
      must be EMPTY, bar the members `_DESYNC_BASELINE` names and explains one by one.

    That continuation gate is RED against the pre-change module on the default seed (one script)
    and GREEN against the changed one, so it is not a check that could never fail.

    Blind spot, stated: a script offending in both corpora for DIFFERENT reasons (desync without
    the run, continuation with it) is attributed to the desync dimension and not flagged here.

    Counts alone cannot carry either comparison -- measured on the default seed, the pre-change
    module and the changed one both scored `hidden_valid=2 hidden_valid_full=2` while offending on
    different scripts -- so the offending SCRIPTS are what is compared.
    """
    mode = (
        "full" if os.environ.get("GIT_COMMAND_DIFFERENTIAL_FULL") == "1" else "default"
    )
    baseline = _DESYNC_BASELINE[mode]
    seeds = baseline["seeds"]
    n = DESYNC_SCRIPTS_PER_SEED
    work = str(tmp_path)
    assert os.path.isabs(work), f"work dir must be absolute, got {work!r}"

    ambiguous_scripts = [
        s for seed in seeds for s in generate_desync_scripts(seed, n, ambiguous=True)
    ]
    plain_scripts = [
        s for seed in seeds for s in generate_desync_scripts(seed, n, ambiguous=False)
    ]

    # Non-vacuity, first: a generator branch that stopped emitting its shape reads exactly like a
    # clean pass, and the twin alignment is what every attribution below rests on.
    shapes = count_desync_shapes(ambiguous_scripts)
    print(f"desync mode={mode} seeds={seeds} scripts={len(ambiguous_scripts)}")
    print(f"desync shape counts: {dict(shapes)}")
    print(f"sample desync script: {ambiguous_scripts[0]!r}")
    for name in _DESYNC_SHAPE_RES:
        assert shapes[name] > 0, (
            f"no generated script carries the {name!r} shape -- this generator exists to produce "
            f"it, and a branch that emits none of it reads exactly like a pass ({dict(shapes)})"
        )
    unpaired = [
        i
        for i, (a, b) in enumerate(zip(ambiguous_scripts, plain_scripts, strict=True))
        if a == b
    ]
    assert not unpaired, (
        f"{len(unpaired)} script pairs are identical across the two corpora (first: {unpaired[:5]})"
        " -- the ambiguous run is supposed to be their ONLY difference, so the two dimensions can "
        "no longer be told apart"
    )

    ambiguous = run_differential(
        git_command,
        seeds,
        n,
        BASHES,
        work,
        generator=lambda seed, count: generate_desync_scripts(
            seed, count, ambiguous=True
        ),
    )
    plain = run_differential(
        git_command,
        seeds,
        n,
        BASHES,
        work,
        generator=lambda seed, count: generate_desync_scripts(
            seed, count, ambiguous=False
        ),
    )

    for label, result in (("ambiguous", ambiguous), ("plain", plain)):
        print(
            f"desync/{label}: runs={result.runs} indeterminate={result.indeterminate} "
            f"hidden_valid={result.hidden_valid} "
            f"hidden_valid_full={result.hidden_valid_full} raised={result.raised} "
            f"measured={result.measured} "
            f"extra_overread={result.extra_overread} git_calls={result.git_call_count} "
            f"offenders={sorted(result.offenders)}"
        )
        assert result.indeterminate <= result.runs / 2, (
            f"{result.indeterminate}/{result.runs} bash runs timed out on the {label} desync "
            "corpus -- INDETERMINATE about the tokenizer, not a verdict on it"
        )

    assert ambiguous.git_call_count >= baseline["git_call_floor"], (
        f"only {ambiguous.git_call_count} git invocations ran across {ambiguous.runs} bash runs "
        f"-- below the measured floor {baseline['git_call_floor']}, so this corpus is vacuous"
    )
    assert ambiguous.raised >= 1, (
        "no tokenizer raise anywhere in the desync corpus -- two of the three shapes raised in "
        "both measured builds, so zero means the shapes stopped reaching the tokenizer"
    )

    # The raise gate. A raise short-circuits every hidden counter, so a change that turned a
    # readable desync into a raise would empty those counters while they still read 0.
    assert (plain.raised, ambiguous.raised) == (
        baseline["plain_raised"],
        baseline["ambiguous_raised"],
    ), (
        f"raised moved: plain {plain.raised} (pre-change {baseline['plain_raised']}), ambiguous "
        f"{ambiguous.raised} (pre-change {baseline['ambiguous_raised']}); hidden_valid_full was "
        f"{ambiguous.hidden_valid_full} over a corpus that many runs never reached"
    )

    # The desync dimension: pinned against the pre-change baseline, NOT required to be 0.
    assert (
        plain.hidden_valid,
        plain.hidden_valid_full,
        plain.offenders,
    ) == (
        baseline["plain_hidden_valid"],
        baseline["plain_hidden_valid_full"],
        baseline["plain_offenders"],
    ), (
        f"the desync dimension moved: hidden_valid={plain.hidden_valid} "
        f"hidden_valid_full={plain.hidden_valid_full} offenders={sorted(plain.offenders)}; "
        f"pre-change baseline was {baseline['plain_hidden_valid']}, "
        f"{baseline['plain_hidden_valid_full']}, {sorted(baseline['plain_offenders'])}"
    )

    # The continuation dimension: `hidden_valid_full == 0` for readings, expressed as "no script
    # loses an invocation to the ambiguous run that the pre-change module did not already lose".
    continuation_only = ambiguous.offenders - plain.offenders
    regressed = sorted(continuation_only - baseline["continuation_allowed"])
    closed = sorted(baseline["continuation_pre_change"] - continuation_only)
    print(f"desync continuation-only offenders: {sorted(continuation_only)}")
    print(f"desync continuation losses closed vs the pre-change module: {closed}")
    for seed, index in regressed[:3]:
        text = generate_desync_scripts(seed, n, ambiguous=True)[index]
        print(f"regressed continuation script (seed {seed}, #{index}): {text!r}")
    assert not regressed, (
        f"{len(regressed)} script(s) lose a status/log invocation bash really ran, ONLY when the "
        f"heredoc's continuation is ambiguous -- a continuation READING was dropped: {regressed}. "
        f"Allowed (measured, not reading defects): {sorted(baseline['continuation_allowed'])}. "
        f"raised was {ambiguous.raised}/{baseline['ambiguous_raised']}, so this is not a corpus "
        "that went unmeasured -- see the printed scripts above"
    )

"""Tests for the cycle-profile CLI: the shipped process, its effects, its refusals.

Every transcript and delegate file is synthetic (fictional base date 2030-01-01, invented ids,
lorem text, transcripts named `synth000.jsonl`). The rows drive `scripts/cycle-profile.py` as a
real process (`sys.executable -B`, a clean environment with an explicit `PATH`), because the
`RESULT:` line, the exit status and the files left on disk are what a caller reads. Each process
runs in an empty working directory with an empty `$TMPDIR` of its own. Two rows run the shipped
file under `runpy` after patching one function of the pipeline in the driver below (a fault the
fixtures cannot reach: an internal exception, a transcript rewritten between its read and its
re-read); the CLI code and its exit behaviour are the shipped ones.

Test-hook note: `CYCLE_PROFILE_TEST_INJECT` names a file whose text the tool appends to the
rendered output before the leak check. Row t33 is opt-in: it runs only when the variable
`CYCLE_PROFILE_GOLDEN` names a golden JSON file, and it is skipped, with that reason, otherwise.
"""

import hashlib
import importlib.util
import json
import os
import re
import shutil
import stat
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import cycle_profile_delegates as dg  # noqa: E402, I001
import cycle_profile_helpers as h  # noqa: E402
import cycle_profile_privacy as p  # noqa: E402

TIMEOUT_S = 120
SAFE_PATH = "/usr/bin:/bin:/opt/local/bin"
MARKER = ".cycle-profile-out"
THREE_PATHS = {MARKER, "data.json", "report.md"}
RESULT_GRAMMAR = re.compile(
    r"RESULT: (PASS|FAIL|ERROR|INCOMPLETE) rc=[0-9]+ cycles=[0-9]+ dispatches=[0-9]+ "
    r"split=[0-9]+/[0-9]+ checks=[0-9]+/[0-9]+/[0-9]+ leak=[0-9]+/[0-9]+ "
    r"answer=(gate|work|undetermined)"
)

#: A transcript string no lorem phrase of the helpers shares a window with.
PLANT = " ".join(reversed(h.LOREM_WORDS))

#: The A1 delegate file under both agent ids: every K1 dispatch is matched.
BOTH_AGENTS = {
    h.AGENT1: h.k1_delegate_files()[h.AGENT1],
    h.AGENT2: h.k1_delegate_files()[h.AGENT1],
}

DRIVER = """
import os, runpy, sys
sys.path.insert(0, os.environ["CP_LIB"])
import cycle_profile_run as pipeline
mode = os.environ["CP_MODE"]
if mode == "raise":
    def boom(*args, **kwargs):
        raise RuntimeError("fragment that must never be printed")
    pipeline.run_profile = boom
elif mode == "remedy":
    import cycle_profile_report as rendering
    render = rendering.render
    def render_with_a_remedy_word(profile, post=None):
        md, js = render(profile, post)
        return md + b"\\nwe could shorten it\\n", js
    rendering.render = render_with_a_remedy_word
elif mode in ("blind1", "blind2"):
    import cycle_profile_privacy as screening
    check = screening.leak_check
    calls = []
    def blind_check(*args, **kwargs):
        calls.append(1)
        found = check(*args, **kwargs)
        if len(calls) == int(mode[-1]):
            return found._replace(control="not-detected")
        return found
    screening.leak_check = blind_check
elif mode == "rewrite":
    original = pipeline.read_transcript
    def read_then_rewrite(path, **kwargs):
        got = original(path, **kwargs)
        with open(path, "r+b") as handle:
            handle.write(b"X")
        return got
    pipeline.read_transcript = read_then_rewrite
cli = os.environ["CP_CLI"]
sys.argv = [cli] + sys.argv[1:]
runpy.run_path(cli, run_name="__main__")
"""


# ---------------------------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------------------------


class Layout(NamedTuple):
    """One run's tree: `<root>/in/synth000.jsonl`, `<root>/out`, an empty cwd and `$TMPDIR`."""

    root: Path
    transcript: Path
    out: Path
    cwd: Path
    tmpdir: Path

    @property
    def delegates(self) -> Path:
        return self.transcript.parent / self.transcript.stem / "subagents"


def make_layout(tmp_path, data=None, files=None) -> Layout:
    root = Path(tmp_path).resolve()
    cwd, tmpdir = root / "cwd", root / "tmp"
    cwd.mkdir(parents=True, exist_ok=True)
    tmpdir.mkdir(exist_ok=True)
    body = h.k1_transcript("skill", agree_ids=True) if data is None else data
    layout = Layout(root, h.write_transcript(root, body), root / "out", cwd, tmpdir)
    if files:
        h.write_delegate_dir(layout.delegates, files)
    return layout


def base_args(layout, *extra):
    """The arguments of a run that can PASS on the K1 fixture with both delegate files."""
    return [
        "--transcript",
        layout.transcript,
        "--out-dir",
        layout.out,
        "--min-cycles",
        "1",
        "--leak-floor",
        "5",
        *extra,
    ]


def clean_env(layout, bytecode=False):
    env = {
        "PATH": SAFE_PATH,
        "HOME": str(layout.root / "home"),
        "TMPDIR": str(layout.tmpdir),
    }
    if not bytecode:
        env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def run_cli(layout, args, *, env=None, flags=("-B",), cli=None, bytecode=False):
    """Run the shipped CLI as a process, in the layout's empty cwd, with a clean environment."""
    full = clean_env(layout, bytecode)
    full.update(env or {})
    return subprocess.run(
        [sys.executable, *flags, str(cli or h.CLI), *[str(a) for a in args]],
        cwd=str(layout.cwd),
        env=full,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=TIMEOUT_S,
        check=False,
    )


def run_driver(layout, mode, args):
    """Run the shipped CLI under `runpy` after the driver patched one pipeline function."""
    script = layout.root / "driver.py"
    script.write_text(DRIVER)
    env = {"CP_MODE": mode, "CP_LIB": str(h.LIB), "CP_CLI": str(h.CLI)}
    return run_cli(layout, args, env=env, cli=script)


def result_of(proc):
    """The one `RESULT:` line, which must also be the last line of stdout."""
    lines = [x for x in proc.stdout.splitlines() if x.startswith("RESULT:")]
    assert len(lines) == 1, proc.stdout
    assert proc.stdout.rstrip("\n").splitlines()[-1] == lines[0]
    return lines[0]


def lines_of(proc, word):
    return [x for x in proc.stdout.splitlines() if x.startswith(word + " ")]


def git(*args, cwd):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=TIMEOUT_S,
        check=False,
    )


def init_repo(path, bare=False):
    path.mkdir(parents=True)
    assert git("init", "-q", *(["--bare"] if bare else []), cwd=path).returncode == 0
    assert git("config", "commit.gpgsign", "false", cwd=path).returncode == 0
    assert git("config", "tag.gpgsign", "false", cwd=path).returncode == 0
    return path


def planted_bytes():
    """K1 with one extra assistant text, `PLANT`, among its candidate strings."""
    events = h.k1_events("skill", agree_ids=True) + [h.say(5.5, text=PLANT)]
    events.sort(key=lambda e: e["at"])
    return h.build_transcript(events)


def load_cli():
    """Import the CLI file as a module (never as `__main__`), leaving no bytecode behind."""
    spec = importlib.util.spec_from_file_location("cycle_profile_cli_probe", h.CLI)
    module = importlib.util.module_from_spec(spec)
    saved, sys.dont_write_bytecode = sys.dont_write_bytecode, True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = saved
    return module


class Runs:
    """Four runs shared by read-only rows: built once per module, never modified."""

    def __init__(self, factory):
        self._factory = factory
        self._cache = {}

    def _get(self, key, build):
        if key not in self._cache:
            layout = make_layout(self._factory.mktemp(key), **build["layout"])
            args = build["args"](layout)
            self._cache[key] = (layout, run_cli(layout, args, env=build.get("env")))
        return self._cache[key]

    def good(self):
        return self._get("good", {"layout": {"files": BOTH_AGENTS}, "args": base_args})

    def incomplete(self):
        files = h.k1_delegate_files()
        return self._get("incomplete", {"layout": {"files": files}, "args": base_args})

    def hit(self):
        if "hit" not in self._cache:
            layout = make_layout(self._factory.mktemp("hit"), data=planted_bytes())
            inject = layout.root / "inject.txt"
            inject.write_text(PLANT)
            env = {"CYCLE_PROFILE_TEST_INJECT": str(inject)}
            self._cache["hit"] = (layout, run_cli(layout, base_args(layout), env=env))
        return self._cache["hit"]

    def refused(self):
        def args(layout):
            args = base_args(layout)
            args[3] = layout.transcript.parent / "out"
            return args

        return self._get("refused", {"layout": {}, "args": args})


@pytest.fixture(scope="module")
def runs(tmp_path_factory):
    return Runs(tmp_path_factory)


# ---------------------------------------------------------------------------------------------
# t13c, t14, t15, t16: inputs the tool cannot fully measure
# ---------------------------------------------------------------------------------------------


def test_t13c_garbage_lines_over_the_cap_are_incomplete(tmp_path):
    data = h.k1_transcript("skill", agree_ids=True) + b"not json at all\n" * 12
    layout = make_layout(tmp_path, data, BOTH_AGENTS)
    proc = run_cli(layout, base_args(layout))
    assert result_of(proc).startswith("RESULT: INCOMPLETE rc=3 ")
    assert proc.returncode == 3
    floor = [x for x in lines_of(proc, "FLOOR") if " max_unparseable_pct " in x]
    assert len(floor) == 1 and floor[0].endswith(" NOT-MET")


def test_t13c_the_cap_flag_moves_the_floor(tmp_path):
    data = h.k1_transcript("skill", agree_ids=True) + b"not json at all\n" * 12
    layout = make_layout(tmp_path, data, BOTH_AGENTS)
    proc = run_cli(layout, base_args(layout, "--max-unparseable-pct", "50"))
    floor = [x for x in lines_of(proc, "FLOOR") if " max_unparseable_pct " in x]
    assert len(floor) == 1 and floor[0].endswith(" ok")
    assert result_of(proc).startswith("RESULT: PASS rc=0 ")


@pytest.mark.parametrize(
    "data", [b"", b"not json\n{{{\n"], ids=["zero-bytes", "only-garbage"]
)
def test_t14_an_unmeasurable_transcript_writes_a_report_and_is_incomplete(
    tmp_path, data
):
    layout = make_layout(tmp_path, data)
    proc = run_cli(layout, base_args(layout))
    line = result_of(proc)
    assert line.startswith("RESULT: INCOMPLETE rc=3 cycles=0 ")
    assert proc.returncode == 3
    assert (layout.out / "report.md").is_file()
    assert (layout.out / "data.json").is_file()
    assert "Traceback" not in proc.stderr


def test_t15_a_transcript_with_no_invocation_is_incomplete(tmp_path):
    events = [h.prompt(0), h.say(10), h.bash(20, "b1", "ls"), h.result(25, "b1")]
    layout = make_layout(tmp_path, h.build_transcript(events))
    proc = run_cli(layout, base_args(layout))
    assert result_of(proc).startswith("RESULT: INCOMPLETE rc=3 cycles=0 ")
    report = (layout.out / "report.md").read_text()
    assert "feature_invocation" in report
    assert "NOT MEASURED (shape feature_invocation absent)" in proc.stdout


def test_t16_a_dropped_acknowledgement_shape_is_not_measured_and_incomplete(tmp_path):
    data = h.build_transcript(
        h.k1_events("skill", agree_ids=True), drop_fields=["toolUseResult"]
    )
    layout = make_layout(tmp_path, data, BOTH_AGENTS)
    proc = run_cli(layout, base_args(layout))
    assert "NOT MEASURED (shape agent_ack absent)" in proc.stdout
    assert result_of(proc).startswith("RESULT: INCOMPLETE rc=3 ")
    assert "Traceback" not in proc.stderr


def test_t16_a_missing_cross_check_shape_is_listed_and_does_not_crash(runs):
    layout, proc = runs.good()
    # K1 holds no `hook_success` record: a cross-check shape, so it is listed and the run holds
    report = (layout.out / "report.md").read_text()
    assert (
        "NOT MEASURED what=cross-check name=hook_success reason=shape-absent" in report
    )
    assert result_of(proc).startswith("RESULT: PASS rc=0 ")
    assert "Traceback" not in proc.stderr


def test_t16_absent_queue_operation_records_do_not_crash_the_run(tmp_path):
    events = [e for e in h.k1_events("skill", agree_ids=True)]
    events = [e for e in events if e["rec"]["type"] != "queue-operation"]
    layout = make_layout(tmp_path, h.build_transcript(events), BOTH_AGENTS)
    proc = run_cli(layout, base_args(layout))
    result_of(proc)
    assert proc.returncode in (0, 3)
    assert "Traceback" not in proc.stderr
    assert (layout.out / "report.md").is_file()


def test_t16_a_varying_version_is_censused_without_a_crash(tmp_path):
    rows = []
    for number, line in enumerate(
        h.k1_transcript("skill", agree_ids=True).decode().splitlines()
    ):
        rec = json.loads(line)
        rec["version"] = "9.9.1" if number % 2 else "9.9.2"
        rows.append(json.dumps(rec, separators=(",", ":")))
    layout = make_layout(tmp_path, ("\n".join(rows) + "\n").encode(), BOTH_AGENTS)
    proc = run_cli(layout, base_args(layout))
    result_of(proc)
    assert "Traceback" not in proc.stderr
    report = (layout.out / "report.md").read_text()
    census = [x for x in report.splitlines() if x.startswith("VERSION ")]
    assert len(census) == 2


# ---------------------------------------------------------------------------------------------
# t21: the out-dir is refused before the transcript is opened
# ---------------------------------------------------------------------------------------------

REFUSALS = [
    "this-repository",
    "gitignored-directory",
    "git-subdirectory",
    "bare-repository",
    "git-off-path",
    "transcript-directory",
    "non-empty-without-marker",
]


@pytest.mark.parametrize("case", REFUSALS)
def test_t21_out_dir_refusals_are_errors_that_write_nothing(tmp_path, case):
    layout = make_layout(tmp_path, files=BOTH_AGENTS)
    out = layout.out
    env = None
    cleanup = None
    if case == "this-repository":
        if not (h.REPO / ".git").exists():
            pytest.skip(
                "the repository root has no .git: a plain export, so this case would write"
            )
        out = h.REPO / "cycle-profile-t21-probe"
        cleanup = out
    elif case == "gitignored-directory":
        repo = init_repo(layout.root / "repo")
        (repo / ".gitignore").write_text("ignored/\n")
        (repo / "ignored").mkdir()
        out = repo / "ignored" / "out"
    elif case == "git-subdirectory":
        out = init_repo(layout.root / "repo") / ".git" / "out"
    elif case == "bare-repository":
        out = init_repo(layout.root / "bare", bare=True) / "out"
    elif case == "git-off-path":
        env = {"PATH": str(layout.root / "no-such-bin")}
    elif case == "transcript-directory":
        out = layout.transcript.parent / "out"
    elif case == "non-empty-without-marker":
        out.mkdir()
        (out / "keep.txt").write_text("not ours\n")
    args = base_args(layout)
    args[3] = out
    try:
        proc = run_cli(layout, args, env=env)
        assert proc.returncode == 2
        assert result_of(proc).startswith("RESULT: ERROR rc=2 ")
        assert lines_of(proc, "REFUSED")
        assert "SNAPSHOT" not in proc.stdout  # the transcript was never read
        if case == "non-empty-without-marker":
            assert sorted(x.name for x in out.iterdir()) == ["keep.txt"]
        else:
            assert not out.exists()
    finally:
        if cleanup is not None:
            shutil.rmtree(cleanup, ignore_errors=True)


def test_t21_the_out_dir_is_refused_before_the_transcript_is_opened(tmp_path):
    layout = make_layout(tmp_path, files=BOTH_AGENTS)
    layout.out.mkdir()
    (layout.out / "keep.txt").write_text("not ours\n")
    # with the transcript gone, a read before the refusal would print a ReadError instead
    layout.transcript.unlink()
    proc = run_cli(layout, base_args(layout))
    assert lines_of(proc, "REFUSED") == ["REFUSED reason=non-empty-no-marker"]
    assert result_of(proc).startswith("RESULT: ERROR rc=2 ")


def test_t21_a_plain_directory_is_accepted(runs):
    layout, proc = runs.good()
    assert proc.returncode == 0
    assert (layout.out / "report.md").is_file()


def test_t21_modes_are_0700_for_the_directory_and_0600_for_the_files(runs):
    layout, _ = runs.good()
    assert stat.S_IMODE(layout.out.stat().st_mode) == 0o700
    for name in THREE_PATHS:
        assert stat.S_IMODE((layout.out / name).stat().st_mode) == 0o600


def test_t21_a_marked_directory_is_reused_and_its_stale_temp_names_cleaned(tmp_path):
    layout = make_layout(tmp_path, files=BOTH_AGENTS)
    layout.out.mkdir()
    (layout.out / MARKER).write_text("marker\n")
    (layout.out / "report.md").write_text("old report\n")
    (layout.out / ".report.md.tmp").write_text("stale\n")
    (layout.out / ".data.json.tmp").write_text("stale\n")
    proc = run_cli(layout, base_args(layout))
    assert "CLEANED 2 stale temp name(s)" in proc.stdout.splitlines()
    assert {x.name for x in layout.out.iterdir()} == THREE_PATHS
    assert (layout.out / "report.md").read_text() != "old report\n"


# ---------------------------------------------------------------------------------------------
# t22: write scope
# ---------------------------------------------------------------------------------------------


def test_t22a_the_out_dir_holds_exactly_the_three_paths(runs):
    layout, _ = runs.good()
    assert {x.name for x in layout.out.iterdir()} == THREE_PATHS


def test_t22a_the_working_directory_and_tmpdir_stay_empty(runs):
    layout, proc = runs.good()
    # a run that wrote its outputs, not one that did nothing
    assert proc.returncode == 0
    assert list(layout.cwd.iterdir()) == []
    assert list(layout.tmpdir.iterdir()) == []


def test_t22b_a_copied_tool_leaves_no_bytecode_behind(tmp_path):
    layout = make_layout(tmp_path / "run", files=BOTH_AGENTS)
    tree = tmp_path / "tree"
    (tree / "scripts" / "lib").mkdir(parents=True)
    shutil.copy2(h.CLI, tree / "scripts" / "cycle-profile.py")
    for module in sorted(h.LIB.glob("cycle_profile_*.py")):
        shutil.copy2(module, tree / "scripts" / "lib" / module.name)
    # no -B and no PYTHONDONTWRITEBYTECODE: the tool has to suppress bytecode itself
    proc = run_cli(
        layout,
        base_args(layout),
        flags=(),
        cli=tree / "scripts" / "cycle-profile.py",
        bytecode=True,
    )
    assert result_of(proc).startswith("RESULT: PASS rc=0 ")
    assert list(tree.rglob("__pycache__")) == []


def test_t22c_a_leftover_temp_name_is_cleaned(tmp_path):
    layout = make_layout(tmp_path, files=BOTH_AGENTS)
    layout.out.mkdir()
    (layout.out / MARKER).write_text("marker\n")
    (layout.out / ".report.md.tmp").write_text("stale\n")
    proc = run_cli(layout, base_args(layout))
    assert "CLEANED 1 stale temp name(s)" in proc.stdout.splitlines()
    assert not (layout.out / ".report.md.tmp").exists()


def test_t22c_an_unknown_entry_is_an_outscope_failure(tmp_path):
    layout = make_layout(tmp_path, files=BOTH_AGENTS)
    layout.out.mkdir()
    (layout.out / MARKER).write_text("marker\n")
    (layout.out / "stray.txt").write_text("not ours\n")
    proc = run_cli(layout, base_args(layout))
    assert any(x.startswith("CHECK OUTSCOPE fail ") for x in proc.stdout.splitlines())
    assert result_of(proc).startswith("RESULT: FAIL rc=1 ")
    assert proc.returncode == 1


# ---------------------------------------------------------------------------------------------
# t23: the leak check and its test hook
# ---------------------------------------------------------------------------------------------


def test_t23a_an_injected_transcript_string_is_a_fail_with_nothing_written(runs):
    layout, proc = runs.hit()
    assert proc.returncode == 1
    assert result_of(proc).startswith("RESULT: FAIL rc=1 ")
    assert not layout.out.exists()


def test_t23a_a_hit_prints_the_leak_line_and_the_result_line_only(runs):
    _, proc = runs.hit()
    lines = [x for x in proc.stdout.splitlines() if x.strip()]
    assert len(lines) == 2
    assert lines[0].startswith("LEAKCHECK samples=")
    assert " hits=" in lines[0] and " hits=0 " not in lines[0]
    assert "SNAPSHOT" not in proc.stdout and "L01" not in proc.stdout


def echoing_bytes():
    """K1 plus a text that is the report's own LEAKCHECK row: absent from render pass 1.

    The count in the row is the sampler's own, so the text is built to a fixed point.
    """
    count = 1
    for _ in range(4):
        text = "CHECK LEAKCHECK pass samples=%d hits=0 control=detected floor=5" % count
        events = h.k1_events("skill", agree_ids=True) + [h.say(5.5, text=text)]
        events.sort(key=lambda e: e["at"])
        data = h.build_transcript(events)
        seen = len(p.sample_candidates(data))
        if seen == count:
            return data
        count = seen
    raise AssertionError("the sample count did not settle")


def test_t23a_a_string_found_only_in_the_second_render_is_a_fail(tmp_path):
    layout = make_layout(tmp_path, echoing_bytes())
    proc = run_cli(layout, base_args(layout))
    assert proc.returncode == 1
    assert result_of(proc).startswith("RESULT: FAIL rc=1 ")
    assert not layout.out.exists()


def delegate_plant_layout(tmp_path):
    """K1 whose delegate file for the first agent holds `PLANT`; the transcript does not."""
    kw = {"sidechain": True, "extra": {"agentId": h.AGENT1}}
    files = dict(BOTH_AGENTS)
    base = h.k1_delegate_files()[h.AGENT1].decode().splitlines()
    extra = h.build_transcript([h.say(130, text=PLANT, **kw)]).decode().splitlines()
    files[h.AGENT1] = ("\n".join(base + extra) + "\n").encode()
    return make_layout(tmp_path, files=files)


def test_t23c_a_string_only_in_a_delegate_file_is_sampled(tmp_path):
    layout = delegate_plant_layout(tmp_path)
    inject = layout.root / "inject.txt"
    inject.write_text(PLANT)
    env = {"CYCLE_PROFILE_TEST_INJECT": str(inject)}
    proc = run_cli(layout, base_args(layout), env=env)
    assert PLANT not in layout.transcript.read_text()
    assert result_of(proc).startswith("RESULT: FAIL rc=1 ")


def test_t24b_leak_samples_caps_the_pooled_transcript_and_delegate_windows(tmp_path):
    layout = delegate_plant_layout(tmp_path)
    proc = run_cli(layout, base_args(layout, "--leak-samples", "5"))
    assert "LEAKCHECK samples=5 " in "".join(lines_of(proc, "LEAKCHECK")) + " "


def test_t23b_the_hook_without_a_hit_withholds_the_outputs_and_is_incomplete(tmp_path):
    layout = make_layout(tmp_path, files=BOTH_AGENTS)
    inject = layout.root / "inject.txt"
    inject.write_text("nothing from the transcript is in this text at all\n")
    env = {"CYCLE_PROFILE_TEST_INJECT": str(inject)}
    proc = run_cli(layout, base_args(layout), env=env)
    assert "TESTHOOK active" in proc.stdout.splitlines()
    assert result_of(proc).startswith("RESULT: INCOMPLETE rc=3 ")
    assert proc.returncode == 3
    assert not layout.out.exists()


def test_t23b_the_same_run_without_the_hook_passes(runs):
    # the control for the row above: the configuration is one that can PASS
    _, proc = runs.good()
    assert result_of(proc).startswith("RESULT: PASS rc=0 ")
    assert "TESTHOOK" not in proc.stdout


def sampled(raw, seed, limit):
    return any(w in PLANT for w in p.sample_candidates(raw, seed=seed, limit=limit))


def inject_run(tmp_path, *extra):
    layout = make_layout(tmp_path, planted_bytes())
    inject = layout.root / "inject.txt"
    inject.write_text(PLANT)
    env = {"CYCLE_PROFILE_TEST_INJECT": str(inject)}
    return layout, run_cli(layout, base_args(layout, *extra), env=env)


def test_t23c_fewer_candidates_than_samples_means_the_string_is_sampled(tmp_path):
    raw = planted_bytes()
    assert sampled(raw, 0, 300)
    _, proc = inject_run(tmp_path)
    assert result_of(proc).startswith("RESULT: FAIL rc=1 ")


def test_t23c_more_candidates_than_samples_catches_the_string_when_sampled(tmp_path):
    raw = planted_bytes()
    seed = next(s for s in range(500) if sampled(raw, s, 3))
    _, proc = inject_run(tmp_path, "--leak-samples", "3", "--leak-seed", str(seed))
    assert result_of(proc).startswith("RESULT: FAIL rc=1 ")


def test_t23c_more_candidates_than_samples_misses_the_string_when_not_sampled(tmp_path):
    raw = planted_bytes()
    seed = next(s for s in range(500) if not sampled(raw, s, 3))
    _, proc = inject_run(tmp_path, "--leak-samples", "3", "--leak-seed", str(seed))
    assert "hits=0" in "".join(lines_of(proc, "LEAKCHECK"))
    assert not result_of(proc).startswith("RESULT: FAIL ")


# ---------------------------------------------------------------------------------------------
# t24: the leak flags
# ---------------------------------------------------------------------------------------------


def test_t24a_fewer_samples_than_the_default_floor_is_unproven(tmp_path):
    layout = make_layout(tmp_path, files=BOTH_AGENTS)
    args = base_args(layout)
    del args[-2:]  # back to the default floor
    proc = run_cli(layout, args)
    assert "LEAKCHECK UNPROVEN" in proc.stdout.splitlines()
    assert result_of(proc).startswith("RESULT: INCOMPLETE rc=3 ")


def test_t24a_the_default_floor_is_100():
    assert load_cli().build_parser().get_default("leak_floor") == 100


def test_t24a_a_low_floor_clears_the_tiny_transcript(runs):
    _, proc = runs.good()
    assert "LEAKCHECK UNPROVEN" not in proc.stdout
    leak = "".join(lines_of(proc, "LEAKCHECK"))
    assert "hits=0 control=detected floor=5" in leak


def test_t24b_leak_samples_reaches_the_checker(tmp_path):
    layout = make_layout(tmp_path, files=BOTH_AGENTS)
    proc = run_cli(layout, base_args(layout, "--leak-samples", "7"))
    assert "LEAKCHECK samples=7 " in "".join(lines_of(proc, "LEAKCHECK")) + " "


def test_t24b_leak_seed_is_echoed_as_a_cli_parameter(tmp_path):
    layout = make_layout(tmp_path, files=BOTH_AGENTS)
    proc = run_cli(layout, base_args(layout, "--leak-seed", "3"))
    assert "PARAM leak-seed=3 source=cli" in proc.stdout.splitlines()
    assert "PARAM leak-floor=5 source=cli" in proc.stdout.splitlines()
    assert "PARAM leak-samples=300 source=default" in proc.stdout.splitlines()


def test_t24c_a_transcript_rewritten_before_the_re_read_is_an_error(tmp_path):
    layout = make_layout(tmp_path, files=BOTH_AGENTS)
    proc = run_driver(layout, "rewrite", base_args(layout))
    assert proc.returncode == 2
    assert result_of(proc).startswith("RESULT: ERROR rc=2 ")
    assert "PinError" in proc.stdout
    assert not layout.out.exists()


# ---------------------------------------------------------------------------------------------
# t26: the manifest is validated before use and never rendered
# ---------------------------------------------------------------------------------------------


def manifest_file(layout, rows):
    path = layout.root / "manifest.json"
    path.write_text(json.dumps(rows))
    return path


GOOD_ROW = {
    "name": "ok",
    "recorded": 1,
    "selector": "cycles.completed",
    "unit": "count",
}


@pytest.mark.parametrize(
    "bad,secret",
    [
        ({"selector": "bogus.selector"}, "bogus.selector"),
        ({"unit": "furlongs"}, "furlongs"),
    ],
    ids=["selector", "unit"],
)
def test_t26_an_invalid_manifest_row_is_an_error_naming_only_its_index(
    tmp_path, bad, secret
):
    layout = make_layout(tmp_path, files=BOTH_AGENTS)
    manifest = manifest_file(layout, [GOOD_ROW, {**GOOD_ROW, **bad}])
    proc = run_cli(layout, base_args(layout, "--manifest", manifest))
    assert proc.returncode == 2
    assert result_of(proc).startswith("RESULT: ERROR rc=2 ")
    assert "index=1" in proc.stdout
    assert secret not in proc.stdout
    assert not layout.out.exists()


def test_t26_a_manifest_name_leaves_no_trace_in_either_output(tmp_path):
    fragment = " ".join(h.LOREM_WORDS[:12][::-1]) + " fragment"
    layout = make_layout(tmp_path, files=BOTH_AGENTS)
    manifest = manifest_file(layout, [{**GOOD_ROW, "name": fragment}])
    proc = run_cli(layout, base_args(layout, "--manifest", manifest))
    assert "MANIFEST rows=1 " in proc.stdout
    for text in (
        proc.stdout,
        (layout.out / "report.md").read_text(),
        (layout.out / "data.json").read_text(),
    ):
        assert fragment not in text


def test_t26c_path_arguments_are_in_neither_output(tmp_path):
    token = "zq" + "kv" + "wx" + "jt"
    layout = make_layout(tmp_path / token, files=BOTH_AGENTS)
    repo = init_repo(layout.root / "repo")
    artifacts = layout.root / "artifacts"
    artifacts.mkdir()
    manifest = manifest_file(layout, [GOOD_ROW])
    args = base_args(
        layout,
        "--delegates",
        layout.delegates,
        "--artifacts",
        artifacts,
        "--repo",
        repo,
        "--manifest",
        manifest,
    )
    proc = run_cli(layout, args)
    assert result_of(proc).split()[1] in ("PASS", "INCOMPLETE")
    assert token in str(layout.out)
    assert token not in (layout.out / "report.md").read_text()
    assert token not in (layout.out / "data.json").read_text()


PATH_OPTIONS = [
    ("delegates", "--delegates"),
    ("artifacts", "--artifacts"),
    ("repo", "--repo"),
    ("manifest", "--manifest"),
    ("delegate-pins", "--delegate-pins"),
    ("prefix", "--prefix-bytes"),
]


@pytest.mark.parametrize("name,flag", PATH_OPTIONS, ids=[x[0] for x in PATH_OPTIONS])
def test_t26c_a_given_path_parameter_is_echoed_as_given_and_never_as_a_path(
    tmp_path, name, flag
):
    layout = make_layout(tmp_path, files=BOTH_AGENTS)
    (layout.root / "artifacts").mkdir()
    extra = {
        "--delegates": [flag, layout.delegates],
        "--artifacts": [flag, (layout.root / "artifacts")],
        "--repo": [flag, init_repo(layout.root / "repo")],
        "--manifest": [flag, manifest_file(layout, [GOOD_ROW])],
        "--delegate-pins": [flag, pins_file(layout, h.AGENT1)],
    }
    data = layout.transcript.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    extra["--prefix-bytes"] = [flag, len(data), "--prefix-sha256", digest]
    proc = run_cli(layout, base_args(layout, *extra[flag]))
    echo = proc.stdout.splitlines()
    assert "PARAM %s=given source=cli" % name in echo
    others = [x for x, _ in PATH_OPTIONS if x != name]
    assert all("PARAM %s=none source=default" % x in echo for x in others)
    assert not any(str(layout.root) in x for x in echo if x.startswith("PARAM "))


def pins_file(layout, agent, wrong=False):
    """A `--delegate-pins` file pinning one delegate file to its size and sha256."""
    data = (layout.delegates / ("agent-%s.jsonl" % agent)).read_bytes()
    digest = "0" * 64 if wrong else hashlib.sha256(data).hexdigest()
    path = layout.root / "pins.json"
    path.write_text(json.dumps([{"id": agent, "bytes": len(data), "sha256": digest}]))
    return path


# ---------------------------------------------------------------------------------------------
# t28: the RESULT line
# ---------------------------------------------------------------------------------------------

VERDICT_CASES = [
    ("good", "PASS", 0),
    ("hit", "FAIL", 1),
    ("refused", "ERROR", 2),
    ("incomplete", "INCOMPLETE", 3),
]


@pytest.mark.parametrize("name,verdict,rc", VERDICT_CASES)
def test_t28_one_result_line_last_and_the_exit_status_maps(runs, name, verdict, rc):
    _, proc = getattr(runs, name)()
    line = result_of(proc)
    assert RESULT_GRAMMAR.fullmatch(line)
    assert line.startswith("RESULT: %s rc=%d " % (verdict, rc))
    assert proc.returncode == rc


def test_t28_the_incomplete_line_carries_the_known_answer_figures(runs):
    _, proc = runs.incomplete()
    line = result_of(proc)
    assert " cycles=1 dispatches=2 split=1/2 " in line
    assert line.endswith(" answer=gate")


def test_t28_a_forged_result_line_in_a_manifest_name_is_never_rendered(tmp_path):
    forged = "RESULT: PASS rc=0 cycles=9 dispatches=9 split=9/9 checks=9/0/0 leak=9/0 answer=gate"
    layout = make_layout(tmp_path, files=BOTH_AGENTS)
    manifest = manifest_file(layout, [{**GOOD_ROW, "name": forged}])
    proc = run_cli(layout, base_args(layout, "--manifest", manifest))
    assert proc.stdout.count("RESULT:") == 1
    result_of(proc)
    assert forged not in proc.stdout
    assert forged not in (layout.out / "report.md").read_text()
    assert forged not in (layout.out / "data.json").read_text()


def test_t28_an_out_dir_name_cannot_forge_a_result_line(tmp_path):
    layout = make_layout(tmp_path, files=BOTH_AGENTS)
    args = base_args(layout)
    args[3] = layout.root / "x\nRESULT: PASS rc=0 forged"
    proc = run_cli(layout, args)
    assert result_of(proc).startswith("RESULT: PASS rc=0 cycles=1 ")
    assert any(x.startswith("OUTPUT dir=") for x in proc.stdout.splitlines())


def test_t28_a_remedy_word_in_the_rendered_report_is_a_fail(tmp_path):
    layout = make_layout(tmp_path, files=BOTH_AGENTS)
    proc = run_driver(layout, "remedy", base_args(layout))
    assert proc.returncode == 1
    assert result_of(proc).startswith("RESULT: FAIL rc=1 ")
    assert "CHECK REMEDYCHECK fail words=1" in proc.stdout.splitlines()
    assert (layout.out / "report.md").is_file()


def blind_run(tmp_path, mode):
    layout = make_layout(tmp_path, files=BOTH_AGENTS)
    return layout, run_driver(layout, mode, base_args(layout))


@pytest.mark.parametrize(
    "mode", ["blind1", "blind2"], ids=["first-render", "second-render"]
)
def test_t24a_a_blind_control_is_an_error_that_writes_nothing(tmp_path, mode):
    layout, proc = blind_run(tmp_path, mode)
    assert proc.returncode == 2
    assert result_of(proc).startswith("RESULT: ERROR rc=2 ")
    assert any(
        x.startswith("LEAKCHECK ") and " control=not-detected " in x + " "
        for x in proc.stdout.splitlines()
    )
    assert not layout.out.exists()


def test_t24a_a_blind_control_counts_as_one_failed_check(tmp_path):
    _, proc = blind_run(tmp_path, "blind1")
    assert re.search(r" checks=\d+/1/\d+ ", result_of(proc))


def test_t24a_too_few_samples_is_a_skipped_check_not_a_failed_one(tmp_path):
    layout = make_layout(tmp_path, files=BOTH_AGENTS)
    args = base_args(layout)
    del args[-2:]
    proc = run_cli(layout, args)
    assert "CHECK LEAKCHECK skip samples=20 hits=0 control=detected floor=100" in (
        proc.stdout.splitlines()
    )
    assert re.search(r" checks=\d+/0/\d+ ", result_of(proc))


def test_t26_a_refused_out_dir_wins_over_an_unreadable_manifest(tmp_path):
    layout = make_layout(tmp_path, files=BOTH_AGENTS)
    layout.out.mkdir()
    (layout.out / "keep.txt").write_text("not ours\n")
    args = base_args(layout, "--manifest", layout.root / "no-such-manifest.json")
    proc = run_cli(layout, args)
    assert lines_of(proc, "REFUSED") == ["REFUSED reason=non-empty-no-marker"]
    assert "class=" not in proc.stdout


# ---------------------------------------------------------------------------------------------
# t28: a failure to write stdout is an ERROR rc=2, never rc 1 or 120
# ---------------------------------------------------------------------------------------------


def run_with_stdout(layout, args, how):
    """Run the shipped CLI with its stdout closed (`closed`) or a pipe closed at once."""
    cmd = [sys.executable, "-B", str(h.CLI), *[str(a) for a in args]]
    kw = {
        "cwd": str(layout.cwd),
        "env": clean_env(layout),
        "stdin": subprocess.DEVNULL,
        "stderr": subprocess.PIPE,
        "text": True,
    }
    if how == "closed":
        shell = ["/bin/sh", "-c", 'exec "$@" >&-', "sh", *cmd]
        return subprocess.run(shell, stdout=None, timeout=TIMEOUT_S, **kw)
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, **kw)
    proc.stdout.close()
    _, err = proc.communicate(timeout=TIMEOUT_S)
    return subprocess.CompletedProcess(cmd, proc.returncode, "", err)


def test_t28_a_closed_stdout_is_an_error_not_a_traceback(tmp_path):
    layout = make_layout(tmp_path, files=BOTH_AGENTS)
    proc = run_with_stdout(layout, base_args(layout), "closed")
    assert proc.returncode == 2
    assert "Traceback" not in proc.stderr
    assert (layout.out / "report.md").is_file()


def test_t28_a_pipe_closed_before_the_write_is_an_error_not_a_traceback(tmp_path):
    layout = make_layout(tmp_path, files=BOTH_AGENTS)
    proc = run_with_stdout(layout, base_args(layout), "pipe")
    assert proc.returncode == 2
    assert "Traceback" not in proc.stderr
    assert (layout.out / "report.md").is_file()


def test_t28_a_pipe_closed_before_the_final_flush_is_an_error_not_120(tmp_path):
    # a usage error prints less than one buffer, so only the final flush meets the closed pipe
    layout = make_layout(tmp_path)
    proc = run_with_stdout(layout, ["--transcript", layout.transcript], "pipe")
    assert proc.returncode == 2
    assert "Exception ignored" not in proc.stderr


def test_t28_an_internal_exception_is_an_error_that_names_only_its_class(tmp_path):
    layout = make_layout(tmp_path, files=BOTH_AGENTS)
    proc = run_driver(layout, "raise", base_args(layout))
    assert proc.returncode == 2
    assert result_of(proc).startswith("RESULT: ERROR rc=2 ")
    assert "RuntimeError" in proc.stdout
    assert "must never be printed" not in proc.stdout + proc.stderr


@pytest.mark.parametrize(
    "flag", ["--prefix-bytes", "--prefix-sha256"], ids=["bytes-alone", "sha-alone"]
)
def test_t28_a_usage_error_is_rc2_with_one_result_line(tmp_path, flag):
    layout = make_layout(tmp_path)
    value = "10" if flag == "--prefix-bytes" else "0" * 64
    proc = run_cli(layout, base_args(layout, flag, value))
    assert proc.returncode == 2
    assert result_of(proc).startswith("RESULT: ERROR rc=2 ")
    assert not layout.out.exists()


def test_t31c_delegate_pins_that_hold_leave_the_run_clean(tmp_path):
    layout = make_layout(tmp_path, files=BOTH_AGENTS)
    pins = pins_file(layout, h.AGENT1)
    proc = run_cli(layout, base_args(layout, "--delegate-pins", pins))
    assert result_of(proc).startswith("RESULT: PASS rc=0 ")


def test_t31c_a_delegate_pin_with_the_wrong_hash_is_an_error(tmp_path):
    layout = make_layout(tmp_path, files=BOTH_AGENTS)
    pins = pins_file(layout, h.AGENT1, wrong=True)
    proc = run_cli(layout, base_args(layout, "--delegate-pins", pins))
    assert proc.returncode == 2
    assert result_of(proc).startswith("RESULT: ERROR rc=2 ")
    assert "PinError" in proc.stdout
    assert not layout.out.exists()


# ---------------------------------------------------------------------------------------------
# t30, t31c: determinism and the prefix pin
# ---------------------------------------------------------------------------------------------


def test_t30_the_written_files_are_the_second_render_pass(runs):
    layout, _ = runs.good()
    report = (layout.out / "report.md").read_text()
    data = json.loads((layout.out / "data.json").read_text())
    assert "LEAKCHECK samples=20 hits=0 control=detected floor=5" in report
    assert data["leak"]["hits"] == 0
    assert [x["name"] for x in data["post_checks"]] == ["REMEDYCHECK", "LEAKCHECK"]


def test_t30_two_runs_on_one_pin_are_byte_identical(tmp_path):
    layout = make_layout(tmp_path, files=BOTH_AGENTS)
    other = layout.root / "out2"
    first = run_cli(layout, base_args(layout))
    args = base_args(layout)
    args[3] = other
    second = run_cli(layout, args)
    assert first.returncode == second.returncode == 0
    for name in ("report.md", "data.json"):
        assert (layout.out / name).read_bytes() == (other / name).read_bytes()


def snapshot_of(proc):
    found = re.findall(r"^SNAPSHOT sha256=(\w+) bytes=(\d+) .*$", proc.stdout, re.M)
    assert len(found) == 1
    return found[0]


def test_t31c_the_prefix_pin_reproduces_the_run_after_the_file_grew(tmp_path):
    layout = make_layout(tmp_path, files=BOTH_AGENTS)
    first = run_cli(layout, base_args(layout))
    data = layout.transcript.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    with open(layout.transcript, "ab") as handle:
        handle.write(h.build_transcript([h.say(2500, text=h.lorem(3))]))
    pinned = layout.root / "out-pinned"
    args = base_args(layout, "--prefix-bytes", len(data), "--prefix-sha256", digest)
    args[3] = pinned
    second = run_cli(layout, args)
    assert snapshot_of(second) == snapshot_of(first) == (digest, str(len(data)))
    assert "pinned=yes" in "".join(lines_of(second, "SNAPSHOT"))
    assert result_of(second) == result_of(first)
    # the control: without the pin the appended record is read
    third_args = base_args(layout)
    third_args[3] = layout.root / "out-unpinned"
    assert snapshot_of(run_cli(layout, third_args)) != snapshot_of(first)


def test_t31c_a_wrong_hash_is_an_error_and_writes_nothing(tmp_path):
    layout = make_layout(tmp_path, files=BOTH_AGENTS)
    size = len(layout.transcript.read_bytes())
    args = base_args(layout, "--prefix-bytes", size, "--prefix-sha256", "0" * 64)
    proc = run_cli(layout, args)
    assert proc.returncode == 2
    assert result_of(proc).startswith("RESULT: ERROR rc=2 ")
    assert not layout.out.exists()


# ---------------------------------------------------------------------------------------------
# t32: the tool's own source holds no transcript-shaped text
# ---------------------------------------------------------------------------------------------

#: The files the tool consists of (the CLI, 11 modules, 11 test files, the helper, the
#: fixture, the 3 mutation campaigns); the discovered set may only add to them.
FLOOR_NAMES = frozenset(
    [
        "cycle-profile.py",
        "cycle_profile_example_manifest.json",
        "cycle_profile_helpers.py",
    ]
    + [
        "cycle_profile_%s.py" % x
        for x in "model records cycles classes delegates pool".split()
    ]
    + ["cycle_profile_%s.py" % x for x in "manifest checks privacy run report".split()]
    + ["test_cycle_profile_%s.py" % x for x in "records privacy cycles classes".split()]
    + ["test_cycle_profile_%s.py" % x for x in "delegates pool manifest checks".split()]
    + ["test_cycle_profile_%s.py" % x for x in "run report cli".split()]
    + ["mutate_cycle_profile_%s.py" % x for x in "classes cycles privacy".split()]
)


def needle_patterns():
    """Every needle is assembled at run time so this file spells none of them whole."""
    return {
        "home-prefix": re.compile(re.escape("/Us" + "ers/")),
        "private-prefix": re.compile(re.escape("/pri" + "vate/")),
        "temp-session": re.compile(re.escape("/tmp/" + "claude-")),
        "uuid": re.compile("-".join("[0-9a-f]{%d}" % n for n in (8, 4, 4, 4, 12))),
        "agent-id": re.compile("agent" + "-[0-9a-f]{17}"),
        "bare-hex-id": re.compile(r"\b[0-9a-f]{17}\b"),
        "tool-use-id": re.compile("tool" + "u_"),
        "jsonl-stem": re.compile("[0-9a-f]{8}" + r"\." + "jsonl"),
        "era-date": re.compile(r"\b20" + "26-[0-9]{2}-[0-9]{2}"),
        # the reserved `.invalid` top-level domain (RFC 2606) names no real address, and the
        # fixtures' identities use it
        "email": re.compile(
            r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+\.(?!invalid\b)[A-Za-z]{2,}"
        ),
    }


def planted_needles():
    return {
        "home-prefix": "/Us" + "ers/someone/file",
        "private-prefix": "/pri" + "vate/var/x",
        "temp-session": "/tmp/" + "claude-501/x",
        "uuid": "-".join(["a1b2c3d4", "e5f6", "a7b8", "c9d0", "e1f2a3b4c5d6"]),
        "agent-id": "agent" + "-a" + "0123456789abcdef",
        "bare-hex-id": "a" + "0123456789abcdef",
        "tool-use-id": "tool" + "u_" + "x1",
        "jsonl-stem": "deadbeef" + ".json" + "l",
        "era-date": "20" + "26-10-27",
        "email": "someone" + "@" + "example" + ".org",
    }


def scan(paths):
    hits = []
    for path in paths:
        text = Path(path).read_text(errors="replace")
        for name, pattern in needle_patterns().items():
            if pattern.search(text):
                hits.append((Path(path).name, name))
    return hits


def source_files():
    scripts = h.REPO / "scripts"
    tests = scripts / "tests"
    return (
        [scripts / "cycle-profile.py"]
        + sorted((scripts / "lib").glob("cycle_profile_*.py"))
        + [tests / "fixtures" / "cycle_profile_example_manifest.json"]
        + [tests / "cycle_profile_helpers.py"]
        + sorted(tests.glob("test_cycle_profile_*.py"))
        + sorted(tests.glob("mutate_cycle_profile_*.py"))
    )


def test_t32_the_discovered_source_set_holds_its_declared_floor():
    found = {x.name for x in source_files() if x.is_file()}
    assert FLOOR_NAMES <= found, sorted(FLOOR_NAMES - found)


def test_t32_the_tool_source_holds_no_transcript_shaped_text():
    files = [x for x in source_files() if x.is_file()]
    assert len(files) >= len(FLOOR_NAMES)
    assert scan(files) == []


@pytest.mark.parametrize("name", sorted(planted_needles()))
def test_t32_a_planted_needle_is_found(tmp_path, name):
    target = tmp_path / "planted.txt"
    target.write_text("value = '%s'\n" % planted_needles()[name])
    assert ("planted.txt", name) in scan([target])


# ---------------------------------------------------------------------------------------------
# t33: the opt-in golden master
# ---------------------------------------------------------------------------------------------

GOLDEN_ENV = "CYCLE_PROFILE_GOLDEN"
INDEPENDENT = ("cycles.", "cycle[", "wall.", "away.", "class.", "idle.", "fence.")
DEPENDENT = ("half.", "answer", "coverage.", "corner.")


def test_t33_golden_master_pinned_session(tmp_path):
    spec = os.environ.get(GOLDEN_ENV)
    if not spec:
        pytest.skip(
            "opt-in golden row: set %s to a golden JSON file to run it" % GOLDEN_ENV
        )
    golden = json.loads(Path(spec).read_text())
    transcript = Path(golden["transcript"])
    size, digest = int(golden["bytes"]), golden["sha256"]
    with open(transcript, "rb") as handle:
        prefix = handle.read(size)
    if len(prefix) != size or hashlib.sha256(prefix).hexdigest() != digest:
        pytest.fail(
            "pinned input unavailable: the golden prefix no longer hashes as recorded"
        )
    layout = Layout(tmp_path, transcript, tmp_path / "out", tmp_path, tmp_path)
    manifest = golden["manifest"]
    if not isinstance(manifest, str):
        manifest = str(manifest_file(layout, manifest))
    args = [
        "--transcript",
        transcript,
        "--out-dir",
        layout.out,
        "--prefix-bytes",
        size,
        "--prefix-sha256",
        digest,
        "--manifest",
        manifest,
    ]
    inventory = golden.get("delegate_inventory")
    if inventory:
        pins = tmp_path / "pins.json"
        pins.write_text(json.dumps(inventory))
        args += ["--delegate-pins", pins]
    proc = subprocess.run(
        [sys.executable, "-B", str(h.CLI), *[str(a) for a in args]],
        capture_output=True,
        text=True,
        timeout=3600,
        check=False,
        env=clean_env(layout),
    )
    assert proc.returncode in (0, 3), result_of(proc)
    items = json.loads((layout.out / "data.json").read_text())["manifest"]["items"]
    selectors = [i["selector"] for i in items]
    unclassified = [s for s in selectors if not s.startswith(INDEPENDENT + DEPENDENT)]
    assert unclassified == []
    independent = [i for i in items if i["selector"].startswith(INDEPENDENT)]
    assert [i["selector"] for i in independent if i["verdict"] != "AGREE"] == []
    dependent = [i for i in items if i["selector"].startswith(DEPENDENT)]
    delegates = transcript.parent / transcript.stem / "subagents"
    if dg.inventory_status(inventory or [], delegates) == "expired":
        pytest.skip(
            "golden delegate files expired: the delegate-dependent rows are not reproducible"
        )
    assert [i["selector"] for i in dependent if i["verdict"] != "AGREE"] == []


def below_floor_run(tmp_path, *extra):
    """K1 with one delegate file, the default `--min-cycles`, and a lowered leak floor."""
    layout = make_layout(tmp_path, files=h.k1_delegate_files())
    args = [
        "--transcript",
        layout.transcript,
        "--out-dir",
        layout.out,
        "--leak-floor",
        "5",
        *extra,
    ]
    return layout, run_cli(layout, args)


def test_t55a_below_min_cycles_every_output_says_undetermined(tmp_path):
    layout, proc = below_floor_run(tmp_path)
    assert result_of(proc).startswith("RESULT: INCOMPLETE rc=3 ")
    assert result_of(proc).endswith(" answer=undetermined")
    answer = lines_of(proc, "ANSWER")
    assert len(answer) == 1 and "half=undetermined N=1 " in answer[0]
    assert "half=undetermined" in (layout.out / "report.md").read_text()
    data = json.loads((layout.out / "data.json").read_text())
    assert data["pool"]["answer"] == "undetermined"
    floor = [x for x in lines_of(proc, "FLOOR") if " min_cycles " in x]
    assert len(floor) == 1 and floor[0].endswith(" NOT-MET")


def test_t55b_at_exactly_min_cycles_the_answer_is_kept(tmp_path):
    layout, proc = below_floor_run(tmp_path, "--min-cycles", "1")
    assert result_of(proc).endswith(" answer=gate")
    answer = lines_of(proc, "ANSWER")
    assert len(answer) == 1 and "half=gate N=1 " in answer[0]
    data = json.loads((layout.out / "data.json").read_text())
    assert data["pool"]["answer"] == "gate"


def test_t56_stdout_l16_line_is_the_report_l16_line(tmp_path):
    layout, proc = below_floor_run(tmp_path, "--min-cycles", "1")
    on_stdout = [x for x in proc.stdout.splitlines() if x.startswith("L16 ")]
    in_report = [
        x
        for x in (layout.out / "report.md").read_text().splitlines()
        if x.startswith("L16 ")
    ]
    assert len(on_stdout) == 1 and len(in_report) == 1
    assert on_stdout == in_report
    assert "leak-check-not-run" not in on_stdout[0]
    assert "control=detected" in on_stdout[0]


@pytest.mark.parametrize(
    ("option", "value"),
    [
        ("--away-fence-k", "inf"),
        ("--away-fence-k", "-inf"),
        ("--away-fence-k", "nan"),
        ("--away-fence-k", "1e999"),
        ("--min-cycles", "inf"),
        ("--min-cycles", "nan"),
    ],
    ids=["k-inf", "k-minus-inf", "k-nan", "k-overflow", "count-inf", "count-nan"],
)
def test_t57_a_non_finite_numeric_option_is_a_usage_error(tmp_path, option, value):
    layout = make_layout(tmp_path, files=BOTH_AGENTS)
    proc = run_cli(layout, base_args(layout, "%s=%s" % (option, value)))
    assert proc.returncode == 2
    assert "error: argument %s:" % option in proc.stderr
    assert "ERROR usage" in proc.stdout.splitlines()
    assert "ERROR class=" not in proc.stdout
    assert not layout.out.exists()

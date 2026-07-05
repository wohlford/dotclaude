"""recast-markers.sh is a sourceable single source of truth for the recon marker arrays."""

import subprocess

from recast_helpers import SKILL

MARKERS_SH = SKILL / "recast-markers.sh"


def _dump(var):
    # Source the markers file in a clean bash; print the named array one element per line.
    script = f'. "{MARKERS_SH}"; printf "%s\\n" "${{{var}[@]}}"'
    out = subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, check=True
    )
    return out.stdout.splitlines()  # elements, incl. spaced ones like "co-authored by"


def test_trace_markers_present():
    toks = _dump("TRACE_MARKERS")
    assert "Co-Authored-By" in toks
    assert "🤖" in toks


def test_name_markers_present():
    toks = _dump("NAME_MARKERS")
    assert {"Claude", "Anthropic", "GEMINI"} <= set(toks)

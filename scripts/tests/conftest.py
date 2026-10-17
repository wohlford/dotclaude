"""Registers the `serial_only` marker and does nothing else.

Pytest loads this file in EVERY run of this tree, the serial `audit.sh --tests` gate included, so it
must change no behaviour: no fixtures, no other hooks, no options. The marker partitions the rows
that must run alone, after the parallel pass, when the opt-in parallel mode of `audit.sh --tests`
(AUDIT_PYTEST_JOBS) is used. The rows that carry it, and the evidence for each, are listed in
scripts/tests/test_serial_only_list.py, which fails when the two disagree.
"""

from __future__ import annotations


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "serial_only: run alone, after the parallel pass, in the opt-in parallel mode of audit.sh --tests",
    )

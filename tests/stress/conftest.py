"""The stress stand (D46, step 13.7): how long the hub and the window take on big synthetic data. Not part of the
usual run - `pytest -m stress` runs it and prints the numbers at the end. No network, no models, nothing of the
user's. Each number has a soft limit, several times what it measured on the developer's machine: it catches a
regression, not a slower computer. The numbers themselves go to the journal, docs/journal.md.

A package (`__init__.py`), so this conftest does not take the place of tests/conftest.py among imported modules."""

from __future__ import annotations

from pathlib import Path

import pytest

from .stand import RESULTS

HERE = Path(__file__).parent


@pytest.hookimpl(tryfirst=True)  # before the -m selection
def pytest_collection_modifyitems(config, items):
    for item in items:
        if item.path.is_relative_to(HERE):
            item.add_marker(pytest.mark.stress)


@pytest.fixture(scope="session")
def qapp():
    from test_ui import QApplication  # its import sets the window's tests up: offscreen, the software scene graph

    return QApplication.instance() or QApplication([])


def pytest_terminal_summary(terminalreporter):
    if not RESULTS:
        return
    width = max(len(what) for what, _, _ in RESULTS)
    terminalreporter.section("stress: the numbers")
    for what, number, limit in RESULTS:
        terminalreporter.write_line(f"{what.ljust(width)}  {number:>12}  (limit {limit})")

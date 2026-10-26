"""`python -m hub` and `python -m app` start, log to their own files and exit cleanly."""

import os
import subprocess
import sys

import pytest

from common.paths import DATA_DIR_ENV, PROJECT_ROOT


@pytest.mark.parametrize(("module", "expected"), [("hub", "LocalChat hub"), ("app", "LocalChat app")])
def test_entry_point_runs_and_logs(module, expected, isolated_data_dir):
    env = {**os.environ, DATA_DIR_ENV: str(isolated_data_dir)}
    result = subprocess.run(
        [sys.executable, "-m", module], cwd=PROJECT_ROOT, env=env, capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0, result.stderr
    log_text = (isolated_data_dir / "logs" / f"{module}.log").read_text(encoding="utf-8")
    assert expected in log_text

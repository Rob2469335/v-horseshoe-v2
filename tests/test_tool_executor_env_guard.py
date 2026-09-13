"""Regression: tool_executor must survive malformed int env vars at import.

`_MAX_TOOL_OUTPUT_BYTES = int(os.environ.get(...))` (and the github cap) were
evaluated at import with no validation, so a bad setting (e.g. "64k") raised
ValueError at import and took the whole tool executor down. The fix falls back
to the documented default.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def global_subprocess_mock():
    """Allow a real subprocess (override conftest's autouse Popen mock)."""
    yield


def test_tool_executor_survives_malformed_int_env():
    code = (
        "import os\n"
        "os.environ['SWARM_MAX_TOOL_OUTPUT_BYTES'] = '64k'\n"
        "os.environ['SWARM_GITHUB_RESEARCH_CAP'] = 'abc'\n"
        "import runtime_v2.services.tool_executor as t\n"
        "print(t._MAX_TOOL_OUTPUT_BYTES, t._GITHUB_RESEARCH_CAP)\n"
    )
    r = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, cwd=str(REPO)
    )
    assert r.returncode == 0, f"tool_executor import crashed:\n{r.stderr}"
    assert r.stdout.strip() == f"{64 * 1024} 5"

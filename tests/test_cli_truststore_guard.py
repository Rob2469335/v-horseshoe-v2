"""Regression: CLI import must survive a truststore injection failure.

`truststore.inject_into_ssl()` was only guarded by `except ImportError`, so any
OTHER exception from it (e.g. a broken bundled trust store) crashed the CLI at
import time. The fix fails open to the default SSL context.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def global_subprocess_mock():
    """Override tests/conftest.py's autouse subprocess.Popen mock so this test can
    spawn a real subprocess (module-scope fixtures take precedence)."""
    yield


def test_cli_import_survives_truststore_injection_failure():
    code = (
        "import truststore\n"
        "def _boom(*a, **k):\n"
        "    raise RuntimeError('broken trust store')\n"
        "truststore.inject_into_ssl = _boom\n"
        "import organism_console.cli\n"
        "print('IMPORTED_OK')\n"
    )
    r = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, cwd=str(REPO)
    )
    assert r.returncode == 0, f"CLI import crashed:\n{r.stderr}"
    assert "IMPORTED_OK" in r.stdout

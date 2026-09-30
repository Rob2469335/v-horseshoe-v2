"""D8 regression: start-dev.ps1 must persist backend stdout/stderr to
logs/backend.log without losing console output or growing unbounded.

Pre-fix defect: the backend job's uvicorn output only flowed through the
Start-Job stream — displayed live via Receive-Job but LOST when the job/terminal
closed, leaving no crash artifact (an import failure at boot left nothing on
disk to diagnose).

Contract (static, over the actual script — no stack startup):
- both the import probe and the uvicorn line pipe through
  `Tee-Object -FilePath ... backend.log -Append` (tee = console preserved:
  output still flows down the job pipeline to Receive-Job),
- never a bare redirect (`>` / Out-File / Redirect-*) that would swallow
  the console stream,
- bounded growth: a startup size guard rotates backend.log to
  backend.log.old (two files max — no new rotation subsystem),
- the established uvicorn launch line (--port 8000 etc.) is unchanged.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "start-dev.ps1"


def _src() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def test_uvicorn_output_teed_to_backend_log():
    src = _src()
    # The tee target is $logFile, which must be assigned to logs/backend.log.
    assert re.search(
        r"\$logFile\s*=\s*Join-Path\s+\$logDir\s+\"backend\.log\"", src
    ), "$logFile must resolve to logs/backend.log"
    m = re.search(
        r"&\s*\$pythonPath\s+-m\s+uvicorn[^\n]*2>&1\s*\|\s*Tee-Object\s+-FilePath\s+\$logFile\s+-Append",
        src,
    )
    assert m, (
        "uvicorn output must pipe through Tee-Object $logFile -Append "
        "(console output preserved AND persisted)"
    )
    # The established launch line survives inside the tee.
    assert "--app-dir $r swarm_os.app.main:app" in src, "uvicorn launch line changed"
    assert "--host 127.0.0.1 --port 8000" in src, "backend bind/port changed"


def test_import_probe_output_also_persisted():
    """The DEBUG import probe runs BEFORE uvicorn — an ImportError there is
    exactly the boot failure the log exists to capture, so it must tee too."""
    src = _src()
    m = re.search(
        r"&\s*\$pythonPath\s+-c\s+\"import os,sys,importlib[^\n]*\"\s*2>&1\s*\|\s*Tee-Object\s+-FilePath\s+\$logFile\s+-Append",
        src,
    )
    assert m, "DEBUG import probe must also append to backend.log"


def test_no_bare_redirect_swallowing_console():
    """`>`-style redirection would REMOVE output from the job stream (breaking
    the console loop); only Tee-Object is acceptable for the backend lines."""
    src = _src()
    for i, line in enumerate(src.splitlines(), 1):
        if "uvicorn" in line or ("$pythonPath" in line and "-c" in line):
            assert not re.search(r"\d*>&\s*1?\s*$", line), (
                f"bare redirect swallows console output at line {i}: {line!r}"
            )
            assert "Out-File" not in line, (
                f"Out-File swallows console output at line {i}: {line!r}"
            )


def test_log_rotation_guard_bounded():
    src = _src()
    # Startup size guard -> single rotation to .old (two files max).
    assert re.search(
        r"backend\.log.*Length.*-\s*gt\s*10MB", src, re.DOTALL
    ), "startup must rotate backend.log when it exceeds 10MB"
    assert re.search(
        r"Move-Item[^\n]*backend\.log[^\n]*backend\.log\.old", src
    ) or re.search(
        r"Move-Item[^\n]*-Destination[^\n]*backend\.log\.old", src
    ), "rotation must MOVE backend.log to backend.log.old (bounded, no new subsystem)"


def test_logs_directory_created_before_tee():
    """Tee-Object fails if logs/ does not exist — the script must create it."""
    src = _src()
    assert re.search(
        r"New-Item\s+-ItemType\s+Directory[^\n]*\$logDir", src
    ), "logs directory must be created before teeing"

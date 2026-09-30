"""D4 regression: start-proxy.ps1 must not mutate the environment at startup.

The pre-fix defect: the proxy startup ran
`.venv\Scripts\python.exe -m pip install fastapi uvicorn httpx psutil > $null`
on EVERY boot — an unguarded, unpinned network package installation (could
silently upgrade packages, was invisible behind `> $null`, and violated the
"do not silently install arbitrary packages" rule).

The fix: a dependency CHECK (import probe) that fails clearly with install
instructions, without touching the environment. Requirements are declared in
requirements.txt (verified: fastapi==0.139.0, uvicorn>=0.52.1, httpx==0.28.1,
psutil==7.2.2).

Static contract tests over the actual script (no stack startup needed).
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROXY_SCRIPT = ROOT / "start-proxy.ps1"
REQUIREMENTS = ROOT / "requirements.txt"


def _src() -> str:
    return PROXY_SCRIPT.read_text(encoding="utf-8")


def test_no_pip_install_in_proxy_startup():
    src = _src()
    # Flag only the EXECUTED form (the boot-time mutation), not the guidance
    # string inside the failure message that TELLS the operator to run it once.
    executed = re.search(
        r"^\s*(?:&\s*)?\.venv\\Scripts\\python\.exe\s+-m\s+pip\s+install",
        src,
        re.MULTILINE,
    )
    assert not executed, (
        "start-proxy.ps1 must not run pip install at startup "
        "(D4: no environment mutation / silent upgrades on boot)"
    )


def test_start_proxy_is_pure_ascii_for_powershell_51():
    """start-proxy.ps1 executes under powershell.exe (5.1), which reads
    BOM-less UTF-8 as CP1252: a non-ASCII byte inside a double-quoted string
    (e.g. an em-dash) decodes to a curly quote that TERMINATES the string
    early, breaking the parse (observed live: proxy never launched, D7 poll
    correctly reported FAILED). The interpreter runs it as 5.1 — pin ASCII."""
    raw = (ROOT / "start-proxy.ps1").read_bytes()
    non_ascii = [i for i, b in enumerate(raw) if b > 127]
    assert not non_ascii, (
        f"non-ASCII bytes at positions {non_ascii[:10]} — start-proxy.ps1 runs "
        "under powershell.exe 5.1 (CP1252 default) and must stay pure ASCII"
    )


def test_proxy_fails_clearly_when_dependencies_missing():
    src = _src()
    # An import-based dependency check must exist...
    assert re.search(
        r"python\.exe\s+-c\s+\"import\s+fastapi,\s*uvicorn,\s*httpx,\s*psutil\"",
        src,
    ), "proxy startup must probe the required imports instead of installing"
    # ...with a clear failure branch carrying actionable instructions.
    assert "$LASTEXITCODE" in src, "dependency check must inspect the exit code"
    fail = re.search(
        r"if\s*\(\s*\$LASTEXITCODE\s*-ne\s*0\s*\)\s*\{[\s\S]*?pip install -r requirements\.txt",
        src,
    )
    assert fail, (
        "failed dependency check must print a clear error naming "
        "`pip install -r requirements.txt`"
    )
    assert re.search(r"exit\s+1", src), "dependency check failure must exit non-zero"


def test_requirements_declares_the_probed_dependencies():
    req = REQUIREMENTS.read_text(encoding="utf-8")
    for pkg in ("fastapi", "uvicorn", "httpx", "psutil"):
        assert re.search(rf"^{pkg}", req, re.MULTILINE), (
            f"{pkg} not declared in requirements.txt — the import probe would "
            "fail on a correctly-provisioned environment"
        )


def test_model_router_launch_unchanged():
    src = _src()
    # D4: do not change model-router behavior or Python environment selection.
    assert ".venv\\Scripts\\python.exe -u model_router.py" in src, (
        "model-router launch line changed (env selection or entrypoint)"
    )
    # Ports untouched: the script still announces 8080 and does not bind others.
    assert "8080" in src
    assert not re.search(r"--port\s+(?!8080)\d+", src), (
        "start-proxy.ps1 must not introduce new ports"
    )

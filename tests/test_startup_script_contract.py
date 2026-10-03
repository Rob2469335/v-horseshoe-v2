"""D7/D2 regression: start-dev.ps1 must report startup results truthfully.

The pre-fix defect: `Backend ✔` / `Frontend ✔` (and the overall "All services
up" banner) were printed UNCONDITIONALLY after a bounded poll loop that could
fail every iteration — the script claimed success when readiness never
happened. The proxy step had NO readiness check at all (just a fixed sleep).

These are static contract tests over the actual script text (the established
way to verify PowerShell startup logic without launching the production stack).
They pin:
- success messages are guarded by a readiness flag set inside the poll loop,
- each poll has an explicit FAILED branch,
- the overall banner is gated on all four flags,
- the proxy has an evidence-based health poll against its existing
  GET /v1/models endpoint (no new protocol invented),
- the fixed ports (8000/6333/8080/8079-8084/5173) are unchanged,
- /health remains the backend liveness gate and /readyz is not replaced.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "start-dev.ps1"

# Every native launcher that can start Qdrant. `start-dev.ps1` is the launcher
# Experiment J uses (docs/F1_L1_RUNTIME_TOPOLOGY.md); `start-dev-fixed.ps1` is a
# tracked second copy of the same startup sequence and must not drift from it.
QDRANT_LAUNCHERS = ("start-dev.ps1", "start-dev-fixed.ps1")

# The canonical production Qdrant storage root, operator-authorized 2026-10-03.
# Recorded in AGENTS.md section 4. A relative value, or any other directory, is
# the defect this contract exists to prevent.
CANONICAL_STORAGE_DIR = "storage"

# Qdrant's own default is `./storage`, resolved against the CHILD's working
# directory, and `Start-Process` without -WorkingDirectory inherits the caller's
# CWD. The launchers never change their own CWD (their Set-Location calls live
# inside Start-Job blocks), so the env pin below is the ONLY thing making the
# attached store deterministic.
ABSOLUTE_STORAGE_PIN = re.compile(
    r"\$env:QDRANT__STORAGE__STORAGE_PATH\s*=\s*Join-Path\s+\$root\s+\"storage\""
)
LOCALHOST_PIN = re.compile(r"\$env:QDRANT__SERVICE__HOST\s*=\s*\"127\.0\.0\.1\"")
QDRANT_LAUNCH = re.compile(r"Start-Process\s+\$qdrantPath")


def _src() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def test_backend_success_message_is_guarded_by_readiness_flag():
    src = _src()
    # The flag must exist and be set inside the poll loop.
    assert re.search(r"\$backendOk\s*=\s*\$false", src), "backend flag not initialized"
    assert re.search(
        r"Invoke-RestMethod\s+\"http://127\.0\.0\.1:8000/health\"[^\n]*\$backendOk\s*=\s*\$true",
        src,
    ), "backend flag must be set to true only when /health responds"
    # The success banner must be INSIDE an if($backendOk) block, never bare.
    ok_block = re.search(
        r"if\s*\(\s*\$backendOk\s*\)\s*\{[^}]*Backend\s+✔",
        src,
        re.DOTALL,
    )
    assert ok_block, "Backend success message must be guarded by if ($backendOk)"
    # A FAILED branch must exist.
    fail_block = re.search(
        r"if\s*\(\s*\$backendOk\s*\)\s*\{[^}]*\}\s*else\s*\{[^}]*Backend FAILED",
        src,
        re.DOTALL,
    )
    assert fail_block, "Backend must report FAILED when the /health poll times out"
    # Pre-fix shape must not survive: a success print with NO guard line
    # anywhere in the 200 chars preceding it. (A bare regex on indentation
    # would false-positive on the properly guarded, indented line.)
    for m in re.finditer(r"Write-Host\s+\"Backend\s+✔", src):
        preceding = src[max(0, m.start() - 200) : m.start()]
        assert "$backendOk" in preceding, (
            "unguarded 'Backend ✔' print found (D2 regression): "
            f"context={preceding[-80:]!r}"
        )


def test_frontend_success_message_is_guarded_by_readiness_flag():
    src = _src()
    assert re.search(r"\$frontendOk\s*=\s*\$false", src), "frontend flag not initialized"
    assert re.search(
        r"Invoke-RestMethod\s+\"http://127\.0\.0\.1:5173\"[^\n]*\$frontendOk\s*=\s*\$true",
        src,
    ), "frontend flag must be set only when :5173 responds"
    ok_block = re.search(
        r"if\s*\(\s*\$frontendOk\s*\)\s*\{[^}]*Frontend\s+✔",
        src,
        re.DOTALL,
    )
    assert ok_block, "Frontend success message must be guarded by if ($frontendOk)"
    fail_block = re.search(
        r"if\s*\(\s*\$frontendOk\s*\)\s*\{[^}]*\}\s*else\s*\{[^}]*Frontend FAILED",
        src,
        re.DOTALL,
    )
    assert fail_block, "Frontend must report FAILED when the poll times out"
    for m in re.finditer(r"Write-Host\s+\"Frontend\s+✔", src):
        preceding = src[max(0, m.start() - 200) : m.start()]
        assert "$frontendOk" in preceding, (
            "unguarded 'Frontend ✔' print found (D2 regression): "
            f"context={preceding[-80:]!r}"
        )


def test_overall_banner_gated_on_all_readiness_flags():
    src = _src()
    # The "All services up" banner must be inside if ($allOk).
    gated = re.search(
        r"\$allOk\s*=\s*\$proxyReady\s*-and\s*\$qdrantOk\s*-and\s*\$backendOk\s*-and\s*\$frontendOk",
        src,
    )
    assert gated, "startup summary must aggregate all four readiness flags"
    up_block = re.search(
        r"if\s*\(\s*\$allOk\s*\)\s*\{[^}]*All services up",
        src,
        re.DOTALL,
    )
    assert up_block, "'All services up' must be inside if ($allOk)"
    fail_block = re.search(
        # The else branch contains nested if-blocks (per-flag appends), so do
        # not use a [^}]* class there — lazy-match up to the FAILURES banner.
        r"if\s*\(\s*\$allOk\s*\)\s*\{[^}]*\}\s*else\s*\{[\s\S]*?FAILURES",
        src,
    )
    assert fail_block, "failed startup must print a FAILURES summary, not 'All services up'"
    # Pre-fix shape: a banner printed with no guard in its preceding context.
    for m in re.finditer(r"All services up", src):
        preceding = src[max(0, m.start() - 300) : m.start()]
        assert "$allOk" in preceding, (
            "unguarded 'All services up' banner found (D2 regression): "
            f"context={preceding[-80:]!r}"
        )


def test_proxy_has_evidence_based_health_poll_on_existing_endpoint():
    src = _src()
    # Proxy readiness uses the proxy's OWN existing endpoint — no new protocol.
    assert re.search(
        r"Invoke-RestMethod\s+\"http://127\.0\.0\.1:8080/v1/models\"",
        src,
    ), "proxy readiness must poll the existing GET /v1/models endpoint"
    assert re.search(r"\$proxyReady\s*=\s*\$false", src), "proxy flag not initialized"
    ok_block = re.search(
        r"if\s*\(\s*\$proxyReady\s*\)\s*\{[^}]*proxy",
        src,
        re.DOTALL | re.IGNORECASE,
    )
    assert ok_block, "proxy success message must be guarded by if ($proxyReady)"
    fail_block = re.search(
        r"if\s*\(\s*\$proxyReady\s*\)\s*\{[^}]*\}\s*else\s*\{[^}]*FAILED",
        src,
        re.DOTALL,
    )
    assert fail_block, "proxy must report FAILED when its poll times out"
    # No invented health endpoint: the proxy check must not invent /health on :8080.
    assert not re.search(
        r"127\.0\.0\.1:8080/health", src
    ), "do not invent a new proxy health endpoint; /v1/models already exists"


def test_health_and_readyz_endpoints_unchanged():
    src = _src()
    # /health stays the backend liveness gate (D7: liveness contract preserved).
    assert "127.0.0.1:8000/health" in src, "backend liveness gate must stay on /health"
    # /readyz is the readiness contract — must not be replaced by /health in
    # any readiness assertion added by this change set (script itself does not
    # poll /readyz today; that is the established design — do not add one).
    # Qdrant truthfulness (D2): failure branch required.
    assert re.search(
        r"if\s*\(\s*-not\s+\$qdrantOk\s*\)\s*\{[^}]*Qdrant FAILED",
        src,
        re.DOTALL,
    ), "Qdrant must report FAILED when its poll times out"


def test_fixed_ports_unchanged():
    src = _src()
    # The working ports from the phase constraints must remain literal.
    for port in ("8000", "6333", "8080", "5173"):
        assert f":{port}" in src, f"port {port} missing from start-dev.ps1"
    # Model service ports 8079-8084 are owned by model_router/start-proxy —
    # start-dev must not spawn llama servers itself (uvicorn's own --port 8000
    # is the backend launch, which is expected). Only flag llama on a
    # command line (Start-Process/invocation), never in comments — D1's PID
    # bookkeeping legitimately documents the llama.exe tree it records.
    for line in src.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        assert not re.search(
            r"(Start-Process|&|Invoke-Expression).*llama(\.exe|-server)", stripped, re.IGNORECASE
        ), f"start-dev.ps1 must not spawn llama servers directly: {stripped!r}"
    # No wholesale rewrite: the script's core steps survive.
    for marker in (
        "STEP 2",
        "STEP 3",
        "STEP 4",
        "STEP 4.5",
        "STEP 5",
        "Start-Job",
        "Receive-Job",
        "--port 8000",  # the established backend launch line survives intact
    ):
        assert marker in src, f"structural marker missing (wholesale rewrite?): {marker}"


def test_no_broad_process_name_killing_in_startup_script():
    src = _src()
    # D1 constraint (checked here as a static invariant): the startup script
    # must not use broad process-name killing.
    assert not re.search(
        r"Stop-Process\s+-Name", src
    ), "broad process-name killing found in start-dev.ps1"
    assert not re.search(r"taskkill\s+/IM", src), "taskkill /IM found in start-dev.ps1"
    # The legacy commented-out cleanup loop must remain commented (not revived).
    for line in src.splitlines():
        if "Stop-Process" in line:
            assert line.lstrip().startswith("#"), (
                f"uncommented Stop-Process found: {line!r}"
            )


# ---------------------------------------------------------------------------
# Qdrant storage-root determinism (operator-authorized 2026-10-03)
#
# Proven defect: the launchers started qdrant.exe with no -WorkingDirectory, no
# --config-path and no storage path, so Qdrant's `./storage` default resolved
# against the CALLER's working directory. A rehearsal on 2026-10-03 00:45:23
# therefore attached to an empty `qdrant_local\` instead of the production
# store. These are static contract tests: they read the script text and never
# start Qdrant, matching the established approach in this module.
# ---------------------------------------------------------------------------


def _launcher_src(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def test_every_qdrant_launcher_pins_the_absolute_canonical_storage_root():
    """Both launchers must set QDRANT__STORAGE__STORAGE_PATH from $root."""
    for name in QDRANT_LAUNCHERS:
        src = _launcher_src(name)
        assert ABSOLUTE_STORAGE_PIN.search(src), (
            f"{name} must set an ABSOLUTE Qdrant storage path derived from "
            f'$root, i.e. $env:QDRANT__STORAGE__STORAGE_PATH = Join-Path $root "storage"'
        )


def test_storage_path_pin_precedes_the_qdrant_launch():
    """The pin must be set before qdrant.exe starts, or it has no effect."""
    for name in QDRANT_LAUNCHERS:
        src = _launcher_src(name)
        pin = ABSOLUTE_STORAGE_PIN.search(src)
        launch = QDRANT_LAUNCH.search(src)
        assert pin and launch, f"{name}: missing storage pin or Qdrant launch"
        assert pin.start() < launch.start(), (
            f"{name}: QDRANT__STORAGE__STORAGE_PATH is assigned AFTER the Qdrant "
            "Start-Process, so the child would not inherit it"
        )


def test_no_launcher_uses_a_relative_or_alternate_qdrant_storage_path():
    """Relative values stay CWD-relative; `qdrant_local` is the proven failure."""
    for name in QDRANT_LAUNCHERS:
        src = _launcher_src(name)
        for m in re.finditer(
            r"\$env:QDRANT__STORAGE__STORAGE_PATH\s*=\s*(.+)$", src, re.MULTILINE
        ):
            value = m.group(1).strip()
            assert not value.startswith('"./') and not value.startswith("'./"), (
                f"{name}: Qdrant storage path must be absolute, got {value!r}"
            )
            assert not value.startswith('".\\') and not value.startswith("'.\\"), (
                f"{name}: Qdrant storage path must be absolute, got {value!r}"
            )
        assert "qdrant_local" not in "\n".join(
            line for line in src.splitlines() if not line.lstrip().startswith("#")
        ), (
            f"{name}: must not point Qdrant at qdrant_local — that path is retained "
            "only as provenance evidence of the CWD defect"
        )


def test_localhost_only_qdrant_binding_preserved_in_every_launcher():
    """The 882646a security fix must survive alongside the storage pin."""
    for name in QDRANT_LAUNCHERS:
        src = _launcher_src(name)
        pin = LOCALHOST_PIN.search(src)
        launch = QDRANT_LAUNCH.search(src)
        assert pin, (
            f"{name}: QDRANT__SERVICE__HOST=127.0.0.1 is required — commit "
            "882646a fixed LAN exposure of the unauthenticated store"
        )
        assert launch and pin.start() < launch.start(), (
            f"{name}: QDRANT__SERVICE__HOST must be set before the Qdrant launch"
        )


def test_every_launcher_mechanism_resolves_to_the_same_root():
    """The two launchers must not drift into different stores."""
    values = set()
    for name in QDRANT_LAUNCHERS:
        m = ABSOLUTE_STORAGE_PIN.search(_launcher_src(name))
        assert m, f"{name}: storage pin missing"
        values.add(m.group(0))
    assert len(values) == 1, (
        f"launchers disagree on the Qdrant storage root: {sorted(values)}"
    )


def test_tracked_qdrant_config_agrees_with_the_canonical_root():
    """No contradictory, inactive storage-root declaration may survive."""
    cfg = (ROOT / ".qdrant" / "config" / "qdrant.yaml").read_text(encoding="utf-8")
    paths = re.findall(r"storage_path:\s*['\"]?([^'\"\n]+)", cfg)
    assert paths, "storage_path missing from .qdrant/config/qdrant.yaml"
    # Compare the RESOLVED path, not its last segment: `.qdrant/storage` also
    # ends with `/storage`, so a suffix check would pass on the very value this
    # contract exists to reject.
    expected = os.path.normcase(os.path.normpath(str(ROOT / CANONICAL_STORAGE_DIR)))
    for raw in paths:
        got = os.path.normcase(os.path.normpath(raw.strip().strip("'\"")))
        assert got == expected, (
            f"tracked Qdrant config contradicts the canonical root: {raw!r} "
            f"(must be {expected}; see AGENTS.md section 4)"
        )
    # A 0.0.0.0 bind would re-expose the unauthenticated store to the LAN.
    hosts = re.findall(r"host:\s*([0-9.]+)", cfg)
    assert "0.0.0.0" not in hosts, (
        "tracked Qdrant config binds 0.0.0.0; commit 882646a established that "
        "this exposed the whole store to the LAN with no auth"
    )
    assert "127.0.0.1" in hosts, "tracked Qdrant config must document the loopback bind"


def test_docker_compose_agrees_with_the_canonical_root_and_is_marked_non_production():
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    volumes = re.findall(r"-\s*(\S+):/qdrant/storage", compose)
    assert volumes, "docker-compose.yml no longer maps a Qdrant storage volume"
    for v in volumes:
        assert v.endswith("/" + CANONICAL_STORAGE_DIR) or v == CANONICAL_STORAGE_DIR, (
            f"docker-compose Qdrant volume {v!r} contradicts the canonical root "
            f"(expected ./{CANONICAL_STORAGE_DIR}); see AGENTS.md section 4"
        )
    # The boundary must be explicit, not implicit: Compose is not the
    # Experiment J launcher.
    assert "NOT the production" in compose, (
        "docker-compose.yml must state that Compose is not the production or "
        "Experiment J Qdrant launcher"
    )


def test_ci_qdrant_is_ephemeral_and_never_mounts_the_production_store():
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    service = ci[ci.index("qdrant:") :]
    assert not re.search(r":/qdrant/storage", service), (
        "CI Qdrant must stay ephemeral; it must not mount a storage volume"
    )
    assert "EPHEMERAL" in service, (
        "CI Qdrant must be explicitly marked ephemeral so it is not confused "
        "with the canonical production store"
    )


def test_existing_qdrant_test_isolation_invariant_is_still_declared():
    """Guard requirement 9.7: ordinary tests must stay barred from real Qdrant."""
    iso = (ROOT / "tests" / "test_qdrant_isolation.py").read_text(encoding="utf-8")
    assert "NO ordinary pytest test may obtain a real Qdrant client" in iso, (
        "tests/test_qdrant_isolation.py lost its 'no real Qdrant client' invariant"
    )
    conftest = (ROOT / "tests" / "conftest.py").read_text(encoding="utf-8")
    assert ":memory:" in conftest, (
        "tests/conftest.py must keep forcing :memory: Qdrant clients"
    )

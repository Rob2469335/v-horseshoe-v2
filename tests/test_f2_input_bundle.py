"""Static checks for the F2 offline input-bundle builder (CODE PROOF).

No VHDX is created; the builder is plan-only by default. These checks assert the
safety contract: plan-only default, no download, SHA-256 manifest, required
manifest fields, and refuse-missing-inputs.
"""

from __future__ import annotations

from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "qwen_train" / "f2_input_bundle.ps1"


def _text() -> str:
    return SRC.read_text(encoding="utf-8")


def test_default_is_plan_only():
    text = _text()
    assert "if (-not $Execute)" in text
    # The mutating call must appear only after the plan-only early exit.
    plan_idx = text.index("if (-not $Execute)")
    create_idx = text.index("New-VHD")
    assert create_idx > plan_idx, "New-VHD must only run under -Execute"


def test_no_download_commands():
    text = _text().lower()
    for forbidden in ("invoke-webrequest", "start-bitstransfer", "curl", "wget", "invoke-restmethod"):
        assert forbidden not in text, f"builder must not download ({forbidden})"


def test_sha256_and_manifest_fields_present():
    text = _text()
    for marker in (
        "Get-FileHash",
        "SHA256",
        "bundle_version",
        "created_utc",
        "host_reference_utc",
        "artifact_count",
        "artifacts",
        "sha256",
    ):
        assert marker in text, f"builder missing: {marker}"


def test_refuses_missing_inputs():
    text = _text()
    assert "InputDir not found" in text
    assert "no input artifacts found" in text


def test_expected_membership_enforced():
    assert "ExpectedArtifacts" in _text()


def test_full_vhdx_lifecycle_present():
    text = _text()
    for marker in (
        "New-VHD",
        "Initialize-Disk",
        "New-Partition",
        "Format-Volume",
        "Mount-VHD",
        "Dismount-VHD",
    ):
        assert marker in text, f"builder missing lifecycle step: {marker}"


def test_cleanup_is_try_finally_and_verifies_detach():
    text = _text()
    assert "try {" in text and "finally {" in text
    assert "Attached" in text  # post-dismount attach check

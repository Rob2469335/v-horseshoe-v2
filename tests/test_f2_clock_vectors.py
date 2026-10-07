"""Runs the shared F2 clock vectors through the single authoritative algorithm.

The vectors file is the cross-language contract (the PowerShell guest probe must
agree with it). No VM/clock is used.
"""

from __future__ import annotations

import json
from pathlib import Path

from qwen_train import f2_clock_guard as cg

VECTORS = Path(__file__).resolve().parents[1] / "qwen_train" / "f2_clock_vectors.json"
HARNESS_PS1 = Path(__file__).resolve().parents[1] / "qwen_train" / "f2_clock_vectors.ps1"


def test_powershell_harness_exists_and_targets_vectors():
    text = HARNESS_PS1.read_text(encoding="utf-8")
    assert "f2_clock_vectors.json" in text
    # Windows PowerShell 5.1 builds the epoch basis without PS7-only APIs.
    assert "AddSeconds" in text


def test_all_vectors_match_authority():
    data = json.loads(VECTORS.read_text(encoding="utf-8"))
    tol = data["tolerance_seconds"]
    default_ref = data["default_reference_utc"]
    for case in data["cases"]:
        ref = case["reference"] if "reference" in case else default_ref
        result = cg.evaluate_clock(
            guest_utc=case["guest"], host_reference_utc=ref, tolerance_seconds=tol
        )
        assert result["clock_status"] == case["expect_status"], case["name"]
        assert result["reason"] == case["expect_reason"], case["name"]


def test_vectors_file_has_boundary_pair():
    data = json.loads(VECTORS.read_text(encoding="utf-8"))
    names = {c["name"] for c in data["cases"]}
    for required in ("plus_120", "minus_120", "plus_121", "minus_121", "http_date_valid", "http_date_malformed"):
        assert required in names

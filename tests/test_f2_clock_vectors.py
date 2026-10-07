"""Runs the shared F2 clock vectors through the single authoritative algorithm.

The vectors file is the cross-language contract (the PowerShell guest probe must
agree with it). No VM/clock is used.
"""

from __future__ import annotations

import json
from pathlib import Path

from qwen_train import f2_clock_guard as cg

VECTORS = Path(__file__).resolve().parents[1] / "qwen_train" / "f2_clock_vectors.json"


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
    assert "exact_boundary" in names and "just_outside" in names

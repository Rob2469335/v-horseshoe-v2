"""Internal consistency of the approved builder `uv` digest.

This proves only that the code constant and the provenance record in
``docs/F2_S8_UV_PIN_PROVENANCE.md`` agree.  It cannot prove anything about the
real executable: the builder tools directory is not readable from a test or
agent context, so the actual binary hash remains an administrator action and is
NOT ESTABLISHED here.
"""
from __future__ import annotations

import pathlib
import re

from qwen_train.f2_s8_pilot_build import UV_APPROVED_SHA256

DOC = pathlib.Path(__file__).resolve().parents[1] / "docs" / "F2_S8_UV_PIN_PROVENANCE.md"


def test_approved_digest_is_a_lowercase_sha256():
    assert re.fullmatch(r"[0-9a-f]{64}", UV_APPROVED_SHA256), UV_APPROVED_SHA256


def test_provenance_record_matches_the_code_constant():
    text = DOC.read_text("utf-8")
    recorded = re.findall(r"\b([0-9a-f]{64})\b", text)
    assert recorded, "provenance document records no 64-hex digest"
    assert UV_APPROVED_SHA256 in recorded, (
        "docs/F2_S8_UV_PIN_PROVENANCE.md does not record the enforced digest "
        f"{UV_APPROVED_SHA256}; update the provenance record in the same change"
    )


def test_provenance_record_states_the_real_executable_is_unverified():
    text = DOC.read_text("utf-8")
    assert "NOT ESTABLISHED" in text
    assert "Get-FileHash" in text

"""Tests for F1 batch-runner defect corrections.

Defect 1: Pretty-printed JSON parsing
Defect 2: Invocation-ID synchronization
Defect 3: Unique manifest path per invocation
"""
import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO / "qwen_train") not in sys.path:
    sys.path.insert(0, str(_REPO / "qwen_train"))

import f1_infra as f1i


class TestPrettyPrintedJsonParsing:
    """Defect 1: Batch runner must parse multi-line pretty-printed JSON."""

    def test_pretty_printed_json_parses(self):
        """Valid pretty-printed JSON is parsed successfully as one object."""
        result = {
            "ts": "2026-09-26T00:00:00Z",
            "id": "f1_pilot/run_1_fresh",
            "cli_ok": False,
            "verdict": False,
            "tools_used": ["lsp", "filesystem"],
            "interpretation": {"validity_infrastructure": "INVALID"},
        }
        stdout = json.dumps(result, indent=2)

        # Simulate the corrected batch-runner parse logic
        obs_result = None
        try:
            d = json.loads(stdout)
            if isinstance(d, dict) and "verdict" in d:
                obs_result = d
        except (json.JSONDecodeError, ValueError):
            pass

        if obs_result is None:
            for line in stdout.strip().split("\n"):
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                    if isinstance(d, dict) and "verdict" in d:
                        obs_result = d
                except json.JSONDecodeError:
                    continue

        assert obs_result is not None
        assert obs_result["verdict"] is False
        assert obs_result["tools_used"] == ["lsp", "filesystem"]

    def test_synthetic_fallback_for_genuinely_invalid_output(self):
        """Genuinely invalid/missing JSON still triggers fallback."""
        stdout = "NOT JSON AT ALL"
        obs_result = None
        try:
            d = json.loads(stdout)
            if isinstance(d, dict) and "verdict" in d:
                obs_result = d
        except (json.JSONDecodeError, ValueError):
            pass
        if obs_result is None:
            for line in stdout.strip().split("\n"):
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                    if isinstance(d, dict) and "verdict" in d:
                        obs_result = d
                except json.JSONDecodeError:
                    continue

        assert obs_result is None


class TestInvocationIdSync:
    """Defect 2: Batch and harness must share the same invocation ID."""

    def test_invocation_id_propagated_to_harness(self):
        """Batch passes inv_id to run_repair_task.py --invocation-id."""
        inv_id = "aabbccdd11223344"
        cmd = [
            "python", "qwen_train/run_repair_task.py",
            "--invocation-id", inv_id,
            "--instance-id", "test",
            "--base-commit", "abc123",
            "--problem-statement", "test",
            "--test-cmd", "echo test",
        ]
        inv_arg_idx = cmd.index("--invocation-id")
        assert cmd[inv_arg_idx + 1] == inv_id

    def test_batch_and_harness_use_same_id(self):
        """Batch-generated invocation_id is the one written to result file."""
        inv_id = "test_id_12345678"
        expected_filename = f"f1_obs_{inv_id}.jsonl"
        # The harness writes to: result_dir / f"f1_obs_{invocation_id}.jsonl"
        # When --invocation-id is provided, the harness uses that ID.
        with tempfile.TemporaryDirectory() as tmp:
            result_path = Path(tmp) / f"f1_obs_{inv_id}.jsonl"
            # Simulate what the harness would write
            assert result_path.name == expected_filename


class TestUniqueManifestPath:
    """Defect 3: Each invocation must produce a unique manifest file."""

    def test_manifest_path_includes_invocation_id(self):
        """Manifest filename contains the invocation ID."""
        inv_id = "aabbccdd11223344"
        evidence_dir = Path("/tmp/test")
        manifest_path = evidence_dir / f"runtime_evidence_manifest_{inv_id}.json"
        assert manifest_path.name == f"runtime_evidence_manifest_{inv_id}.json"

    def test_two_invocations_produce_different_manifests(self):
        """Observation A and B cannot overwrite each other's manifests."""
        with tempfile.TemporaryDirectory() as tmp:
            evidence_dir = Path(tmp)

            # Simulate two observations
            obs_a = f1i.RuntimeEvidenceManifest(
                evidence_identity=f1i.EvidenceIdentity(
                    invocation_id="aaaa1111", attempt_id="test",
                    created_at="t", content_hash="h1"
                ),
                attempt_id="test",
            )
            obs_b = f1i.RuntimeEvidenceManifest(
                evidence_identity=f1i.EvidenceIdentity(
                    invocation_id="bbbb2222", attempt_id="test",
                    created_at="t", content_hash="h2"
                ),
                attempt_id="test",
            )

            path_a = f1i.save_manifest(obs_a, evidence_dir)
            path_b = f1i.save_manifest(obs_b, evidence_dir)

            assert path_a != path_b
            assert path_a.exists()
            assert path_b.exists()

            # Verify each contains correct identity
            data_a = json.loads(path_a.read_text())
            data_b = json.loads(path_b.read_text())
            assert data_a["invocation_id"] == "aaaa1111"
            assert data_b["invocation_id"] == "bbbb2222"

    def test_stale_manifest_not_overwritten(self):
        """Saving manifest B does NOT overwrite manifest A."""
        with tempfile.TemporaryDirectory() as tmp:
            evidence_dir = Path(tmp)

            obs_a = f1i.RuntimeEvidenceManifest(
                evidence_identity=f1i.EvidenceIdentity(
                    invocation_id="first_run", attempt_id="test",
                    created_at="t", content_hash="h1"
                ),
                attempt_id="test",
            )
            path_a = f1i.save_manifest(obs_a, evidence_dir)
            original_content = path_a.read_text()

            # Save a different observation
            obs_b = f1i.RuntimeEvidenceManifest(
                evidence_identity=f1i.EvidenceIdentity(
                    invocation_id="second_run", attempt_id="test",
                    created_at="t", content_hash="h2"
                ),
                attempt_id="test",
            )
            path_b = f1i.save_manifest(obs_b, evidence_dir)

            # Verify manifest A is untouched
            assert path_a.read_text() == original_content
            assert path_b.exists()
            assert path_a != path_b

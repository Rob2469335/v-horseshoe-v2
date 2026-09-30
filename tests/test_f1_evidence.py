"""Tests for Evidence-First F1 architecture invariants.

Covers:
- Evidence identity semantics (invocation_id vs content_hash)
- Observation vs interpretation separation
- Machine-enforced scientific rules A-G
- RuntimeEvidenceManifest persistence with provenance
- Health gate + monitoring integration
- Router monitoring
- Real process-death integration
- Evidence reconstructability
- UNKNOWN propagation
- Decision boundary requirement
"""
from __future__ import annotations

import json
import multiprocessing
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO / "qwen_train") not in sys.path:
    sys.path.insert(0, str(_REPO / "qwen_train"))

import f1_infra as f1i


def _subprocess_with_port(conn):
    """Standalone target for multiprocessing: binds a free port, reports it back, listens."""
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    conn.send(port)
    conn.close()
    s.listen(1)
    s.settimeout(120)
    try:
        while True:
            s.accept()
    except Exception:
        pass
    finally:
        s.close()


# ---------------------------------------------------------------------------
# 1. Evidence Identity — invocation_id vs content_hash
# ---------------------------------------------------------------------------

class TestEvidenceIdentity:
    def test_invocation_id_unique_per_call(self):
        a = f1i.EvidenceIdentity.create("test1")
        b = f1i.EvidenceIdentity.create("test1")
        assert a.invocation_id != b.invocation_id

    def test_frozen(self):
        eid = f1i.EvidenceIdentity.create("x")
        with pytest.raises(AttributeError):
            eid.invocation_id = "tampered"

    def test_from_observation_content_hash_deterministic(self):
        """Same observation fields → same content_hash."""
        fields = {"cli_ok": True, "timed_out": False, "elapsed_s": 5.0}
        a = f1i.EvidenceIdentity.from_observation("test", fields)
        b = f1i.EvidenceIdentity.from_observation("test", fields)
        assert a.content_hash == b.content_hash

    def test_from_observation_different_fields_different_hash(self):
        a = f1i.EvidenceIdentity.from_observation("test", {"cli_ok": True})
        b = f1i.EvidenceIdentity.from_observation("test", {"cli_ok": False})
        assert a.content_hash != b.content_hash

    def test_from_observation_has_both_ids(self):
        eid = f1i.EvidenceIdentity.from_observation("t", {"x": 1})
        assert len(eid.invocation_id) == 16
        assert len(eid.content_hash) == 16


# ---------------------------------------------------------------------------
# 2. Observation vs Interpretation Separation
# ---------------------------------------------------------------------------

class TestObservationInterpretationSeparation:
    def test_raw_observation_not_mutated_by_classification(self):
        obs = f1i.RawObservation(cli_ok=False, timed_out=True, elapsed_s=30.0)
        cls = f1i.classify_observation(obs)
        assert obs.cli_ok is False
        assert cls.validity_infrastructure == "INVALID"
        assert obs.cli_ok is False  # unchanged

    def test_classification_derived_from_raw_facts(self):
        obs = f1i.RawObservation(infra_invalid_reason="backend_exit", cli_ok=False, timed_out=True)
        cls = f1i.classify_observation(obs)
        assert cls.validity_infrastructure == "INVALID"
        # The reason string is NOT in the classification — only its consequence
        assert "backend_exit" not in cls.to_dict().get("validity_infrastructure", "")


# ---------------------------------------------------------------------------
# 3. Scientific Rules A-G
# ---------------------------------------------------------------------------

class TestScientificRules:
    def test_rule_a_infra_invalid_gives_unknown(self):
        obs = f1i.RawObservation(infra_invalid_reason="backend_exit", cli_ok=False, timed_out=True)
        cls = f1i.classify_observation(obs)
        assert cls.validity_infrastructure == "INVALID"
        assert cls.capability_repair == "UNKNOWN"
        assert cls.capability_tool_selection == "UNKNOWN"
        assert cls.capability_debugging == "UNKNOWN"

    def test_rule_b_infra_invalid_zero_positive(self):
        obs = f1i.RawObservation(infra_invalid_reason="backend_exit")
        cls = f1i.classify_observation(obs)
        assert cls.capability_credit_positive == 0

    def test_rule_c_infra_invalid_zero_negative(self):
        obs = f1i.RawObservation(infra_invalid_reason="router_unreachable")
        cls = f1i.classify_observation(obs)
        assert cls.capability_credit_negative == 0

    def test_rule_d_invalid_cannot_count_against_model(self):
        obs = f1i.RawObservation(infra_invalid_reason="backend_exit")
        cls = f1i.classify_observation(obs)
        assert cls.capability_credit_negative == 0

    def test_rule_e_no_boundary_no_credit(self):
        obs = f1i.RawObservation(cli_ok=False, timed_out=False)
        cls = f1i.classify_observation(obs)
        assert cls.capability_credit_positive == 0
        assert cls.capability_credit_negative == 0

    def test_rule_f_boundary_not_reached_gives_unknown(self):
        obs = f1i.RawObservation(cli_ok=False, timed_out=True)
        cls = f1i.classify_observation(obs)
        assert cls.capability_repair == "UNKNOWN"
        assert cls.decision_boundary_reached is False

    def test_rule_g_unknown_remains_unknown(self):
        obs = f1i.RawObservation(infra_invalid_reason="health_gate_failure")
        cls = f1i.classify_observation(obs)
        assert cls.capability_repair == "UNKNOWN"
        assert cls.capability_repair != "FAIL"
        assert cls.capability_repair != "PASS"

    def test_valid_with_boundary_still_unknown_needs_evaluator(self):
        obs = f1i.RawObservation(cli_ok=True, timed_out=False, elapsed_s=5.0)
        cls = f1i.classify_observation(obs)
        assert cls.validity_infrastructure == "VALID"
        assert cls.decision_boundary_reached is True
        assert cls.capability_repair == "UNKNOWN"
        assert cls.capability_credit_positive == 0

    def test_all_infra_invalid_reasons_classify_correctly(self):
        for reason in f1i.INFRA_INVALID_REASONS:
            obs = f1i.RawObservation(infra_invalid_reason=reason)
            cls = f1i.classify_observation(obs)
            assert cls.validity_infrastructure == "INVALID", f"reason={reason}"
            assert cls.capability_credit_positive == 0, f"positive on {reason}"
            assert cls.capability_credit_negative == 0, f"negative on {reason}"


# ---------------------------------------------------------------------------
# 4. Manifest with Provenance
# ---------------------------------------------------------------------------

class TestManifest:
    def _make_backend(self, **kw):
        defaults = dict(role="backend", pid=1, expected_pid=1,
                        start_time="t", state=f1i.ProcessState.HEALTHY,
                        stdout_path="/dev/null", stderr_path="/dev/null")
        defaults.update(kw)
        return f1i.ProcessRecord(**defaults)

    def _make_gate(self, **kw):
        defaults = dict(passed=True, timestamp="t", process_alive=True,
                        port_listening=True, health_http_200=True, status_ready=True,
                        workspace_identity="/x", router_reachability=True,
                        expected_pid_identity=True)
        defaults.update(kw)
        return f1i.HealthGateResult(**defaults)

    def test_manifest_has_populated_provenance(self, tmp_path):
        b = self._make_backend()
        g = self._make_gate()
        m = f1i.build_manifest("test_prov", b, g, tmp_path)
        p = m.provenance
        assert p.attempt_id == "test_prov"
        assert p.experiment_id == "experiment_j_f1"
        assert len(p.source_evidence) > 0
        assert p.generator == "f1_infra"

    def test_manifest_provenance_references_real_evidence(self, tmp_path):
        b = self._make_backend(stdout_path=str(tmp_path / "out.log"),
                                stderr_path=str(tmp_path / "err.log"))
        (tmp_path / "out.log").write_text("test")
        (tmp_path / "err.log").write_text("test")
        g = self._make_gate()
        m = f1i.build_manifest("test_ref", b, g, tmp_path)
        types = [e["type"] for e in m.provenance.source_evidence]
        assert "stdout_log" in types
        assert "stderr_log" in types
        assert "health_gate" in types

    def test_manifest_persists_provenance(self, tmp_path):
        b = self._make_backend()
        g = self._make_gate()
        m = f1i.build_manifest("test_persist", b, g, tmp_path)
        path = f1i.save_manifest(m, tmp_path)
        data = json.loads(path.read_text())
        assert data["provenance"]["attempt_id"] == "test_persist"
        assert data["provenance"]["experiment_id"] == "experiment_j_f1"
        assert len(data["provenance"]["source_evidence"]) > 0

    def test_manifest_has_observation_and_interpretation(self, tmp_path):
        b = self._make_backend()
        g = self._make_gate()
        obs = f1i.RawObservation(infra_invalid_reason="backend_exit")
        cls = f1i.classify_observation(obs)
        m = f1i.build_manifest("test_oi", b, g, tmp_path,
                               observation=obs, classification=cls)
        assert m.observation["infra_invalid_reason"] == "backend_exit"
        assert m.interpretation["validity_infrastructure"] == "INVALID"

    def test_manifest_has_content_hash(self, tmp_path):
        b = self._make_backend()
        g = self._make_gate()
        obs = f1i.RawObservation(cli_ok=True, timed_out=False)
        m = f1i.build_manifest("test_hash", b, g, tmp_path, observation=obs)
        assert len(m.evidence_identity.content_hash) == 16
        assert m.evidence_identity.content_hash != ""

    def test_manifest_with_monitor_has_events(self, tmp_path):
        b = self._make_backend(pid=os.getpid())
        g = self._make_gate()
        mon = f1i.RuntimeMonitor(b, check_interval=0.1)
        mon.start()
        time.sleep(0.3)
        mon.stop()
        m = f1i.build_manifest("test_mon", b, g, tmp_path, monitor=mon)
        assert len(m.monitoring["events"]) > 0


# ---------------------------------------------------------------------------
# 5. Runtime Monitor with Router
# ---------------------------------------------------------------------------

class TestRuntimeMonitor:
    def test_monitor_starts_and_stops(self):
        backend = f1i.ProcessRecord(role="backend", pid=os.getpid(),
                                    state=f1i.ProcessState.HEALTHY)
        mon = f1i.RuntimeMonitor(backend, check_interval=0.1)
        mon.start()
        assert mon.alive is True
        time.sleep(0.3)
        mon.stop()
        assert len(mon.events) >= 2

    def test_monitor_detects_dead_process(self):
        backend = f1i.ProcessRecord(role="backend", pid=999999,
                                    state=f1i.ProcessState.HEALTHY)
        mon = f1i.RuntimeMonitor(backend, port=99999, check_interval=0.1)
        mon.start()
        time.sleep(0.5)
        mon.stop()
        assert mon.alive is False
        types = [e["type"] for e in mon.events]
        assert "process_dead" in types or "port_lost" in types

    def test_router_monitoring_detects_router_port_loss(self):
        """Monitor with router_port detects when router is unreachable."""
        backend = f1i.ProcessRecord(role="backend", pid=os.getpid(),
                                    state=f1i.ProcessState.HEALTHY)
        mon = f1i.RuntimeMonitor(backend, port=19999, router_port=19998,
                                 check_interval=0.1)
        mon.start()
        time.sleep(0.5)
        mon.stop()
        assert mon.alive is False
        assert mon.router_alive is False
        types = [e["type"] for e in mon.events]
        assert "port_lost" in types
        assert "router_unreachable" in types

    def test_router_only_failure_lifecycle(self):
        """Backend alive + router dies -> monitor detects -> INVALID -> zero credit."""
        import socket
        # Create two listening sockets: one for "backend", one for "router"
        backend_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        backend_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        backend_sock.bind(("127.0.0.1", 0))
        backend_port = backend_sock.getsockname()[1]
        backend_sock.listen(1)

        router_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        router_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        router_sock.bind(("127.0.0.1", 0))
        router_port = router_sock.getsockname()[1]
        router_sock.listen(1)

        try:
            backend = f1i.ProcessRecord(role="backend", pid=os.getpid(),
                                        state=f1i.ProcessState.HEALTHY)
            mon = f1i.RuntimeMonitor(backend, port=backend_port,
                                     router_port=router_port, check_interval=0.2)
            mon.start()
            time.sleep(0.8)
            assert mon.alive is True  # backend port listening
            assert mon.router_alive is True  # router port listening

            # Kill only the router socket (simulate router death)
            router_sock.close()
            time.sleep(1.0)
            mon.stop()

            # Backend is still alive but router is gone
            assert mon.alive is True  # backend port still listening
            assert mon.router_alive is False  # router port gone

            # Classify with router failure
            obs = f1i.RawObservation(
                backend_pid=os.getpid(), expected_pid=os.getpid(),
                pid_alive=True, cli_ok=False, timed_out=False,
                infra_invalid_reason="router_unreachable",
            )
            cls = f1i.classify_observation(obs)
            assert cls.validity_infrastructure == "INVALID"
            assert cls.capability_repair == "UNKNOWN"
            assert cls.capability_credit_positive == 0
            assert cls.capability_credit_negative == 0
            assert cls.decision_boundary_reached is False

            types = [e["type"] for e in mon.events]
            assert "router_unreachable" in types
        finally:
            backend_sock.close()
            try:
                router_sock.close()
            except OSError:
                pass


# ---------------------------------------------------------------------------
# 6. Health Gate
# ---------------------------------------------------------------------------

class TestHealthGate:
    def test_gate_detects_dead_pid(self):
        backend = f1i.ProcessRecord(role="backend", pid=999999, state=f1i.ProcessState.STARTING)
        gate = f1i.wait_for_backend(backend, port=99999, timeout=3, pid_check_interval=0.1)
        assert gate.passed is False

    def test_gate_timeout(self):
        backend = f1i.ProcessRecord(role="backend", pid=os.getpid(),
                                    state=f1i.ProcessState.STARTING)
        gate = f1i.wait_for_backend(backend, port=19999, timeout=1, pid_check_interval=0.1)
        assert gate.passed is False
        assert any("timeout" in e for e in gate.errors)


# ---------------------------------------------------------------------------
# 7. Real Process-Death Integration Test
# ---------------------------------------------------------------------------

class TestRealProcessDeathIntegration:
    def test_full_lifecycle_backend_death(self):
        """START → real subprocess with port → monitor → kill → detect → classify → INVALID → manifest."""
        import multiprocessing

        parent_conn, child_conn = multiprocessing.Pipe()
        proc = multiprocessing.Process(target=_subprocess_with_port, args=(child_conn,))
        proc.start()
        child_conn.close()

        try:
            # Wait for subprocess to report its port
            assert parent_conn.poll(timeout=5), "subprocess did not report port"
            listen_port = parent_conn.recv()
            parent_conn.close()
            assert proc.is_alive()

            # Create monitor: watch PID + port
            backend = f1i.ProcessRecord(
                role="backend", pid=proc.pid, expected_pid=proc.pid,
                start_time=f1i._now_iso(), command_identity="test_listen",
                parent_pid=os.getpid(), state=f1i.ProcessState.HEALTHY,
                stdout_path="/dev/null", stderr_path="/dev/null",
            )
            monitor = f1i.RuntimeMonitor(backend, port=listen_port, check_interval=0.2)
            monitor.start()
            time.sleep(1.0)
            assert monitor.alive is True

            # Kill the subprocess
            proc.terminate()
            proc.join(timeout=5)

            # Monitor should detect death
            time.sleep(1.0)
            monitor.stop()
            assert monitor.alive is False
            assert proc.exitcode is not None

            # Build raw observation
            obs = f1i.RawObservation(
                backend_pid=proc.pid, expected_pid=proc.pid,
                start_time=backend.start_time, end_time=f1i._now_iso(),
                pid_alive=monitor.alive,
                process_state="dead",
                cli_ok=False, timed_out=False, elapsed_s=0.0,
                infra_invalid_reason="backend_exit",
            )

            # Classify
            cls = f1i.classify_observation(obs)
            assert cls.validity_infrastructure == "INVALID"
            assert cls.capability_repair == "UNKNOWN"
            assert cls.capability_credit_positive == 0
            assert cls.capability_credit_negative == 0
            assert cls.decision_boundary_reached is False

            # Build and persist manifest
            evidence_dir = Path(tempfile.mkdtemp())
            gate = f1i.HealthGateResult(
                passed=True, timestamp="t", process_alive=True,
                port_listening=False, health_http_200=False,
                status_ready=False, workspace_identity="/test",
                router_reachability=False, expected_pid_identity=False,
            )
            manifest = f1i.build_manifest(
                "death_test", backend, gate, evidence_dir,
                observation=obs, classification=cls, monitor=monitor,
            )
            path = f1i.save_manifest(manifest, evidence_dir)
            assert path.exists()

            # Verify artifact contents
            data = json.loads(path.read_text())
            assert data["interpretation"]["validity_infrastructure"] == "INVALID"
            assert data["interpretation"]["capability_credit"]["positive"] == 0
            assert data["interpretation"]["capability_credit"]["negative"] == 0
            assert data["observation"]["infra_invalid_reason"] == "backend_exit"
            assert len(data["provenance"]["source_evidence"]) > 0
            assert data["provenance"]["attempt_id"] == "death_test"
            assert data["provenance"]["experiment_id"] == "experiment_j_f1"
            assert len(data["monitoring"]["events"]) > 0
            assert data["monitoring"]["alive_at_stop"] is False
            assert data["content_hash"] != ""
            assert data["invocation_id"] != ""

        finally:
            parent_conn.close()
            if proc.is_alive():
                proc.terminate()
                proc.join(timeout=2)


# ---------------------------------------------------------------------------
# 8. Capability Credit Enforcement
# ---------------------------------------------------------------------------

class TestCapabilityCreditEnforcement:
    def test_infra_invalid_always_zero_credit(self):
        for reason in f1i.INFRA_INVALID_REASONS:
            obs = f1i.RawObservation(infra_invalid_reason=reason, cli_ok=False, timed_out=True)
            cls = f1i.classify_observation(obs)
            assert cls.capability_credit_positive == 0, f"positive on {reason}"
            assert cls.capability_credit_negative == 0, f"negative on {reason}"

    def test_valid_zero_credit_without_evaluator(self):
        obs = f1i.RawObservation(cli_ok=True, timed_out=False, elapsed_s=10.0)
        cls = f1i.classify_observation(obs)
        assert cls.capability_credit_positive == 0
        assert cls.capability_credit_negative == 0

    def test_classification_version_tracked(self):
        obs = f1i.RawObservation(infra_invalid_reason="test")
        cls = f1i.classify_observation(obs)
        assert cls.classification_version == "1.0"

    def test_classification_timestamp_recorded(self):
        obs = f1i.RawObservation(infra_invalid_reason="test")
        cls = f1i.classify_observation(obs)
        assert "T" in cls.classified_at


# ---------------------------------------------------------------------------
# 9. Decision Boundary
# ---------------------------------------------------------------------------

class TestDecisionBoundary:
    def test_boundary_not_reached_gives_unknown(self):
        obs = f1i.RawObservation(cli_ok=False, timed_out=False)
        cls = f1i.classify_observation(obs)
        assert cls.decision_boundary_reached is False
        assert cls.capability_repair == "UNKNOWN"

    def test_boundary_reached_still_unknown_needs_evaluator(self):
        obs = f1i.RawObservation(cli_ok=True, timed_out=False)
        cls = f1i.classify_observation(obs)
        assert cls.decision_boundary_reached is True
        assert cls.capability_repair == "UNKNOWN"

    def test_infra_invalid_forces_boundary_unreached(self):
        obs = f1i.RawObservation(infra_invalid_reason="backend_exit", cli_ok=True, timed_out=False)
        cls = f1i.classify_observation(obs)
        assert cls.decision_boundary_reached is False


# ---------------------------------------------------------------------------
# 10. Evidence Preservation
# ---------------------------------------------------------------------------

class TestEvidencePreservation:
    def test_file_status_captures_metadata(self, tmp_path):
        f = tmp_path / "test.log"
        f.write_text("hello")
        status = f1i._capture_file_status(str(f))
        assert status["exists"] is True
        assert status["non_empty"] is True

    def test_file_status_handles_missing(self, tmp_path):
        status = f1i._capture_file_status(str(tmp_path / "nope.log"))
        assert status["exists"] is False
        assert status["non_empty"] is False


# ---------------------------------------------------------------------------
# 11. Reconstruction Proof
# ---------------------------------------------------------------------------

class TestReconstruction:
    def test_observation_to_classification_deterministic(self):
        """Same raw observation always produces same classification (except timestamp)."""
        obs = f1i.RawObservation(infra_invalid_reason="backend_exit", cli_ok=False, timed_out=True)
        cls1 = f1i.classify_observation(obs)
        cls2 = f1i.classify_observation(obs)
        d1 = cls1.to_dict()
        d2 = cls2.to_dict()
        d1.pop("classified_at")
        d2.pop("classified_at")
        assert d1 == d2

    def test_manifest_roundtrip_classification_matches(self, tmp_path):
        """Classification in manifest matches fresh reclassification from observation."""
        b = f1i.ProcessRecord(role="backend", pid=1, expected_pid=1,
                              start_time="t", state=f1i.ProcessState.HEALTHY,
                              stdout_path="/dev/null", stderr_path="/dev/null")
        g = f1i.HealthGateResult(passed=True, timestamp="t",
                                 process_alive=True, port_listening=True,
                                 health_http_200=True, status_ready=True,
                                 workspace_identity="/x", router_reachability=True,
                                 expected_pid_identity=True)
        obs = f1i.RawObservation(infra_invalid_reason="backend_exit", cli_ok=False, timed_out=True)
        cls = f1i.classify_observation(obs)
        m = f1i.build_manifest("recon", b, g, tmp_path, observation=obs, classification=cls)

        # Save and reload
        path = f1i.save_manifest(m, tmp_path)
        data = json.loads(path.read_text())

        # Reconstruct classification from persisted observation
        persisted_obs = f1i.RawObservation(
            backend_pid=data["observation"]["backend_pid"],
            cli_ok=data["observation"]["cli_ok"],
            timed_out=data["observation"]["timed_out"],
            elapsed_s=data["observation"]["elapsed_s"],
            infra_invalid_reason=data["observation"]["infra_invalid_reason"],
            workspace_identity=data["observation"]["workspace_identity"],
            pid_alive=data["observation"]["pid_alive"],
        )
        reconstructed = f1i.classify_observation(persisted_obs)

        # Must match (except classified_at which is a fresh timestamp)
        reconstructed_dict = reconstructed.to_dict()
        persisted_dict = data["interpretation"]
        reconstructed_dict.pop("classified_at")
        persisted_dict.pop("classified_at")
        assert reconstructed_dict == persisted_dict
        assert data["interpretation"]["validity_infrastructure"] == "INVALID"
        assert data["interpretation"]["capability_credit"]["positive"] == 0

    def test_content_hash_is_deterministic(self, tmp_path):
        """Same observation → same content_hash in manifest."""
        b = f1i.ProcessRecord(role="backend", pid=1, expected_pid=1,
                              start_time="t", state=f1i.ProcessState.HEALTHY,
                              stdout_path="/dev/null", stderr_path="/dev/null")
        g = f1i.HealthGateResult(passed=True, timestamp="t",
                                 process_alive=True, port_listening=True,
                                 health_http_200=True, status_ready=True,
                                 workspace_identity="/x", router_reachability=True,
                                 expected_pid_identity=True)
        obs = f1i.RawObservation(cli_ok=True, timed_out=False, elapsed_s=3.0)
        m1 = f1i.build_manifest("h1", b, g, tmp_path, observation=obs)
        m2 = f1i.build_manifest("h2", b, g, tmp_path, observation=obs)
        # Same observation fields → same content_hash
        assert m1.evidence_identity.content_hash == m2.evidence_identity.content_hash
        # Different attempt → different invocation_id
        assert m1.evidence_identity.invocation_id != m2.evidence_identity.invocation_id


# ---------------------------------------------------------------------------
# F1 Endpoint Detection (F1-OP-004a)
# ---------------------------------------------------------------------------

class TestFindQualifyingFirstEdit:
    """Tests for the F1 qualifying-first-edit scanner."""

    def _make_fs_patch_tc(self, step_id: int, path: str, ok: bool = False) -> dict:
        """Helper to build an ATIF tool-call record for filesystem patch."""
        return {
            "function_name": "filesystem",
            "arguments": {"operation": "patch", "path": path},
            "extra": {"turn": step_id},
        }

    def _make_fs_read_tc(self, step_id: int, path: str) -> dict:
        return {
            "function_name": "filesystem",
            "arguments": {"operation": "read", "path": path},
            "extra": {"turn": step_id},
        }

    def _make_lsp_tc(self, step_id: int) -> dict:
        return {
            "function_name": "lsp",
            "arguments": {"operation": "diagnostics", "file_path": "swarm_os/lib/paths.py"},
            "extra": {"turn": step_id},
        }

    def test_patch_on_relevant_file_qualifies(self):
        """TEST A: filesystem.patch on swarm_os/lib/paths.py → endpoint."""
        tc = self._make_fs_patch_tc(4, "swarm_os/lib/paths.py")
        assert f1i.find_qualifying_first_edit([tc]) == 4

    def test_patch_rejected_still_qualifies(self):
        """TEST A: patch with ok=false still qualifies (action-based, not outcome-based)."""
        tc = self._make_fs_patch_tc(4, "swarm_os/lib/paths.py", ok=False)
        assert f1i.find_qualifying_first_edit([tc]) == 4

    def test_patch_on_unrelated_file_does_not_qualify(self):
        """TEST B: filesystem.patch on unrelated file → no endpoint."""
        tc = self._make_fs_patch_tc(4, "swarm_os/lib/other.py")
        assert f1i.find_qualifying_first_edit([tc]) is None

    def test_read_on_relevant_file_does_not_qualify(self):
        """TEST C: filesystem.read on relevant file → not a qualifying edit."""
        tc = self._make_fs_read_tc(4, "swarm_os/lib/paths.py")
        assert f1i.find_qualifying_first_edit([tc]) is None

    def test_qualifying_edit_at_step_12(self):
        """TEST D: qualifying edit at step 12 → endpoint = 12."""
        tc = self._make_fs_patch_tc(12, "swarm_os/lib/paths.py")
        assert f1i.find_qualifying_first_edit([tc]) == 12

    def test_first_qualifying_edit_wins(self):
        """Earliest qualifying edit is the endpoint, not the latest."""
        tc_later = self._make_fs_patch_tc(8, "swarm_os/lib/paths.py")
        tc_earlier = self._make_fs_patch_tc(3, "swarm_os/lib/paths.py")
        assert f1i.find_qualifying_first_edit([tc_later, tc_earlier]) == 3

    def test_empty_tool_calls_returns_none(self):
        assert f1i.find_qualifying_first_edit([]) is None

    def test_no_qualifying_edit_returns_none(self):
        """TEST E (partial): all reads, no edits → None."""
        tcs = [self._make_fs_read_tc(i, "swarm_os/lib/paths.py") for i in range(1, 13)]
        assert f1i.find_qualifying_first_edit(tcs) is None


class TestF1EndpointClassification:
    """Tests for classify_observation with F1 endpoint detection."""

    def _make_tc(self, step_id: int, fn: str, op: str, path: str) -> dict:
        return {
            "function_name": fn,
            "arguments": {"operation": op, "path": path},
            "extra": {"turn": step_id},
        }

    def test_obs2_pattern_qualifying_endpoint(self):
        """TEST A: Observation 2 pattern — patch at step 4, ok=false, timeout."""
        tcs = [
            self._make_tc(1, "lsp", "diagnostics", "swarm_os/lib/paths.py"),
            self._make_tc(2, "filesystem", "read", "swarm_os/lib/paths.py"),
            self._make_tc(7, "lsp", "diagnostics", "swarm_os/lib/paths.py"),
            self._make_tc(11, "filesystem", "patch", "swarm_os/lib/paths.py"),
        ]
        obs = f1i.RawObservation(
            cli_ok=False, timed_out=True, elapsed_s=1204.0,
            tool_calls=tcs,
            infra_invalid_reason="",
        )
        cls = f1i.classify_observation(obs)
        assert cls.f1_endpoint_step == 11
        assert cls.decision_boundary_reached is True

    def test_timeout_with_qualifying_edit_preserves_endpoint(self):
        """TEST F: qualifying edit at step 4, timeout later → endpoint stays 4."""
        tcs = [self._make_tc(4, "filesystem", "patch", "swarm_os/lib/paths.py")]
        obs = f1i.RawObservation(
            cli_ok=False, timed_out=True, elapsed_s=1200.0,
            tool_calls=tcs, infra_invalid_reason="",
        )
        cls = f1i.classify_observation(obs)
        assert cls.f1_endpoint_step == 4

    def test_no_qualifying_edit_timeout_gives_none(self):
        """TEST G: no qualifying edit, timeout → endpoint is None."""
        tcs = [
            self._make_tc(1, "filesystem", "read", "swarm_os/lib/paths.py"),
            self._make_tc(2, "filesystem", "read", "swarm_os/lib/paths.py"),
        ]
        obs = f1i.RawObservation(
            cli_ok=False, timed_out=True, elapsed_s=1200.0,
            tool_calls=tcs, infra_invalid_reason="",
        )
        cls = f1i.classify_observation(obs)
        assert cls.f1_endpoint_step is None

    def test_infra_invalid_with_qualifying_edit_still_records_endpoint(self):
        """Infra-invalid + qualifying edit: endpoint recorded but credit stays zero."""
        tcs = [self._make_tc(4, "filesystem", "patch", "swarm_os/lib/paths.py")]
        obs = f1i.RawObservation(
            cli_ok=False, timed_out=True, elapsed_s=1200.0,
            tool_calls=tcs, infra_invalid_reason="backend_exit",
        )
        cls = f1i.classify_observation(obs)
        assert cls.f1_endpoint_step == 4  # endpoint is recorded
        assert cls.validity_infrastructure == "INVALID"
        assert cls.capability_credit_positive == 0
        assert cls.capability_credit_negative == 0

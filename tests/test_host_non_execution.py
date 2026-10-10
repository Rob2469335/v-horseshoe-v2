"""Host non-execution regression tests — mandatory deliverable (§3).

Property under test
-------------------
Host-side **planning, census, manifest, parsing, validation and admission**
code must never execute repository task code.  A task's ``test_cmd``, install
steps, package scripts and identifiers are untrusted **data**.

Method
------
Two independent detectors, both behavioural (no source-string searching):

1. a **process-launch recorder** that replaces every process-launching entry
   point used anywhere in this repository (``subprocess.run/Popen/call/…``,
   ``os.system/popen/spawn*/exec*``, ``asyncio.create_subprocess_*``) with a
   callable that records the attempt and raises; and
2. a **side-effect sentinel**: the synthetic task carries a command that would
   create a file *if* it were ever executed.  The test asserts the file does
   not exist afterwards.

``test_launch_detector_is_live`` proves detector (1) is actually wired before
any other assertion in this file is trusted.

Limitation (state this with every green run): these tests prove that the named
host-side boundaries did not launch a process for the synthetic records under
test.  They do **not** prove that the host is isolated from arbitrary code
execution, that other code paths are free of subprocess use, or that a real
task would be safe.
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest

from qwen_train.f2_population import screen_pool_rows
from qwen_train.f2_population_build import build as build_census
from qwen_train.f2_s8_channel import read_channel_file
from qwen_train.f2_s8_job import build_job_record, validate_job_record
from qwen_train.f2_s8_record import encode_record
from qwen_train.f2_task_plan import order_tasks, plan_for_position

_SUBPROCESS_TARGETS = (
    "run",
    "Popen",
    "call",
    "check_call",
    "check_output",
    "getoutput",
    "getstatusoutput",
)
_OS_TARGETS = ("system", "popen", "spawnv", "spawnve", "spawnlp", "execl", "execv")
_ASYNCIO_TARGETS = ("create_subprocess_exec", "create_subprocess_shell")

_PATCH = "diff --git a/pkg/m.py b/pkg/m.py\n--- a/pkg/m.py\n+++ b/pkg/m.py\n@@ -1 +1 @@\n-a\n+b\n"


class LaunchRecorder:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple, dict]] = []

    def _make(self, name: str):
        def _intercept(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            raise AssertionError(
                f"host-side path attempted a process launch via {name}: {args!r}"
            )

        return _intercept


@contextmanager
def no_process_launch():
    """Fail loudly if any host-side call reaches a process launcher."""
    rec = LaunchRecorder()
    started = []
    for name in _SUBPROCESS_TARGETS:
        started.append(patch.object(subprocess, name, rec._make(f"subprocess.{name}")))
    for name in _OS_TARGETS:
        if hasattr(os, name):
            started.append(patch.object(os, name, rec._make(f"os.{name}")))
    for name in _ASYNCIO_TARGETS:
        started.append(patch.object(asyncio, name, rec._make(f"asyncio.{name}")))
    for p in started:
        p.start()
    try:
        yield rec
    finally:
        for p in reversed(started):
            p.stop()


def _sentinel_command(sentinel: Path) -> str:
    """A command that WOULD create ``sentinel`` if anything ever executed it."""
    return (
        f'{sys.executable} -c "from pathlib import Path; '
        f"Path(r'{sentinel}').write_text('PWNED')\""
    )


def _payload_for(sentinel: Path) -> dict:
    return {
        "instance_id": "evil__task-1",
        "repo": "evil/task",
        "base_commit": "a" * 40,
        "FAIL_TO_PASS": ["tests/test_x.py::test_a"],
        "PASS_TO_PASS": ["tests/test_x.py::test_b"],
        "patch": _PATCH,
        "created_at": "2025-06-01T00:00:00",
        "language": "python",
        "install_config": {"test_cmd": _sentinel_command(sentinel)},
        "test_cmds": [_sentinel_command(sentinel)],
    }


@pytest.fixture
def sentinel(tmp_path: Path) -> Path:
    return tmp_path / "HOST_NON_EXEC_SENTINEL.txt"


class TestDetectorIsLive:
    def test_launch_detector_intercepts_a_real_launch(self):
        """Detector self-test: the recorder must catch a benign launch attempt.

        The intercepted call never reaches the operating system, so no process
        is created; without this test the rest of the file could be green while
        the patches were silently un-wired.
        """
        with no_process_launch() as rec:
            with pytest.raises(AssertionError, match="process launch"):
                subprocess.run([sys.executable, "-c", "raise SystemExit(0)"])
        assert rec.calls and rec.calls[0][0] == "subprocess.run"


class TestPlanningAndOrdering:
    def test_planning_treats_test_cmd_as_inert_data(self, sentinel, tmp_path):
        rows = [
            {
                "instance_id": "evil__task-1",
                "created_at": "2025-06-01T00:00:00",
                "test_cmd": _sentinel_command(sentinel),
                "source": "swe-rebench-v2",
                "language": "python",
                "repo": "evil/task",
            },
            {
                "instance_id": "evil__task-2",
                "created_at": "2025-06-02T00:00:00",
                "test_cmd": "cmd.exe /c del /q *",
                "source": "swe-rebench-v2",
                "language": "python",
                "repo": "evil/task",
            },
        ]
        with no_process_launch() as rec:
            ordered, excluded = order_tasks(rows)
            plan = plan_for_position(ordered, 1)
        assert rec.calls == []
        assert not sentinel.exists()
        assert len(ordered) == 2 and excluded == []
        assert plan["task_id"] == "evil__task-2"
        assert plan["operations_performed"] == [
            "read pinned metadata",
            "sort by the registered ordering rule",
            "hash the row",
        ]
        assert "run_task_command" in plan["operations_not_performed"]
        # the command is carried as a hash, never as a command
        assert "test_cmd" not in plan

    def test_ordering_of_a_malicious_identifier_cannot_launch(self, sentinel):
        rows = [
            {"instance_id": "../../evil&calc", "created_at": "2025-06-01T00:00:00",
             "test_cmd": _sentinel_command(sentinel)},
            {"instance_id": "zzz", "created_at": "2025-06-01T00:00:00", "test_cmd": ""},
        ]
        with no_process_launch() as rec:
            ordered, excluded = order_tasks(rows)
        assert rec.calls == []
        assert not sentinel.exists()
        assert [r["instance_id"] for r in ordered] == ["../../evil&calc", "zzz"]
        assert excluded == []


class TestCensusManifestAndScreening:
    def _write_acquired(self, tmp_path: Path, sentinel: Path) -> Path:
        d = tmp_path / "acq"
        d.mkdir()
        rows = [
            {
                **_payload_for(sentinel),
                "instance_id": "evil__task-1",
            },
            {
                **_payload_for(sentinel),
                "instance_id": "evil__task-2",
                "created_at": "2025-06-02 00:00:00",
            },
        ]
        (d / "acquired.jsonl").write_text(
            "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8"
        )
        (d / "PROVENANCE.json").write_text(
            json.dumps({"source_key": "swe-rebench-v2", "row_count": len(rows)}),
            encoding="utf-8",
        )
        return d / "acquired.jsonl"

    def test_census_and_manifest_generation_do_not_execute_setup(self, sentinel, tmp_path):
        acquired = self._write_acquired(tmp_path, sentinel)
        with no_process_launch() as rec:
            manifest, record = build_census(acquired)
        assert rec.calls == []
        assert not sentinel.exists()
        assert record["source_key"] == "swe-rebench-v2"
        assert len(manifest.entries) == 2
        assert manifest.admitted == ()  # S8 is absent; nothing is admitted

    def test_host_side_admission_screen_does_not_execute_task_code(self, sentinel):
        rows = [
            {
                "instance_id": "evil__task-1",
                "repo": "evil/task",
                "base_commit": "a" * 40,
                "test_cmd": _sentinel_command(sentinel),
                "fail_to_pass": ["t::a"],
                "pass_to_pass": ["t::b"],
                "created_at": "2025-06-01T00:00:00",
                "usable": True,
            }
        ]
        with no_process_launch() as rec:
            manifest = screen_pool_rows(
                rows,
                relevant_file_sets={"evil__task-1": ["pkg/m.py"]},
                model_cutoff="2024-01-01",
            )
        assert rec.calls == []
        assert not sentinel.exists()
        entry = manifest.entries[0]
        assert entry.admitted is False  # S8 fails closed
        assert entry.metadata_eligible is True


class TestParsingAndValidation:
    def _channel_file(self, tmp_path: Path, payload: dict, name="r.bin") -> Path:
        p = tmp_path / name
        p.write_bytes(encode_record(payload))
        return p

    def test_parsing_and_validation_do_not_execute_task_scripts(self, sentinel, tmp_path):
        payload = {
            "schema_version": "f2_s8_channel_v1",
            "payload_kind": "plan",
            "task_id": "evil__task-1",
            "job_id": "1" * 64,
            "source": "swe-rebench-v2",
            "phase": "PLAN",
            "status": "planned",
            "created_at": "2026-10-10T00:00:00Z",
            "provenance": {"metadata": "pinned"},
            "note": _sentinel_command(sentinel),
        }
        with no_process_launch() as rec:
            verdict = read_channel_file(self._channel_file(tmp_path, payload))
        assert rec.calls == []
        assert not sentinel.exists()
        assert verdict.ok is True  # a command inside a *note* is inert data
        assert verdict.is_execution_evidence is False

    @pytest.mark.parametrize(
        "field,value",
        [
            ("command", "rm -rf /"),
            ("exec", _sentinel_command(Path("C:/sentinel.txt"))),
            ("shell", "cmd.exe /c whoami"),
            ("module", "os;import sys"),
        ],
    )
    def test_invalid_records_cannot_trigger_execution(
        self, sentinel, tmp_path, field, value
    ):
        payload = {
            "schema_version": "f2_s8_channel_v1",
            "payload_kind": "plan",
            "task_id": "evil__task-1",
            "job_id": "1" * 64,
            "source": "swe-rebench-v2",
            "phase": "PLAN",
            "status": "planned",
            "created_at": "2026-10-10T00:00:00Z",
            "provenance": {"metadata": "pinned"},
            field: value,
        }
        with no_process_launch() as rec:
            verdict = read_channel_file(self._channel_file(tmp_path, payload))
        assert rec.calls == []
        assert verdict.ok is False
        assert verdict.reason_codes == ["UNEXPECTED_FIELD"]
        assert not sentinel.exists()

    def test_job_record_builder_and_validator_never_launch(self, sentinel, tmp_path):
        with no_process_launch() as rec:
            job = build_job_record(
                task_id="evil__task-1",
                source="swe-rebench-v2",
                source_record_digest="b" * 64,
                population_artifact={"name": "census.json", "sha256": "a" * 64},
                phase="PLAN",
                metadata_facts={"test_cmd_sha256": "c" * 64, "language": "python"},
                provenance={"metadata": "pinned", "command_carried_as": "digest"},
            )
            verdict = validate_job_record(job, expected_task_id="evil__task-1")
        assert rec.calls == []
        assert not sentinel.exists()
        assert verdict.ok is True
        assert job.is_execution_evidence is False


class TestTraversalAndSideEffects:
    def test_malicious_task_identifier_and_path_cannot_escape(self, sentinel, tmp_path):
        before = sorted(p.name for p in tmp_path.iterdir())
        payload = {
            "schema_version": "f2_s8_channel_v1",
            "payload_kind": "plan",
            "task_id": "../../evil&calc",
            "job_id": "1" * 64,
            "source": "swe-rebench-v2",
            "phase": "PLAN",
            "status": "planned",
            "created_at": "2026-10-10T00:00:00Z",
            "provenance": {"metadata": "pinned"},
            "artifact": {"name": "..\\..\\escape.txt", "sha256": "a" * 64, "size_bytes": 3},
        }
        p = tmp_path / "traversal.bin"
        p.write_bytes(encode_record(payload))
        with no_process_launch() as rec:
            verdict = read_channel_file(p)
        assert rec.calls == []
        assert verdict.ok is False
        assert verdict.reason_codes == ["UNTRUSTED_FIELD_VALUE"]
        assert not sentinel.exists()
        assert not (tmp_path / "escape.txt").exists()
        assert sorted(x.name for x in tmp_path.iterdir()) == before + ["traversal.bin"] or (
            "traversal.bin" in before
        )

    def test_a_traversal_task_id_is_accepted_as_data_not_as_a_path(self, tmp_path):
        """The id is metadata; nothing resolves it against the filesystem."""
        rows = [{"instance_id": "../../etc/passwd", "created_at": "2025-01-01T00:00:00"}]
        with no_process_launch() as rec:
            ordered, _ = order_tasks(rows)
            plan_for_position(ordered, 1)
        assert rec.calls == []
        assert not (tmp_path / "passwd").exists()

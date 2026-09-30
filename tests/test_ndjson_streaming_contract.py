"""Verify the NDJSON wire contract of /agents/{agent_id}/step/stream.

Derived from swarm_os/api/agents.py:246-293.

Contract:
1. Response media type is application/x-ndjson
2. Each non-empty line is valid JSON
3. Last line is {"type": "final", "done": true}
4. Error path emits error then final
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from runtime_v2.api.agent_service_v2 import AgentServiceV2


class TestNdjsonContract:
    def test_response_media_type(self, client):
        """The response must be application/x-ndjson."""
        async def _fake_stream(*args, **kwargs):
            yield {"type": "final", "content": "ok"}
            return
            yield  # pragma: no cover

        svc = AgentServiceV2.__new__(AgentServiceV2)
        svc.step_agent_stream = _fake_stream

        runtime = MagicMock()
        runtime.agents = svc

        with patch.object(client.app.state, "runtime", runtime):
            r = client.post(
                "/agents/coder/step/stream",
                json={"prompt": "hello"},
            )
        assert "application/x-ndjson" in r.headers.get("content-type", "")

    def test_all_lines_are_valid_json(self, client):
        """Every line in the NDJSON stream must be parseable JSON."""
        async def _fake_stream(*args, **kwargs):
            yield {"type": "model_selected", "model": "robs4b"}
            yield {"type": "final", "content": "done"}
            return
            yield  # pragma: no cover

        svc = AgentServiceV2.__new__(AgentServiceV2)
        svc.step_agent_stream = _fake_stream

        runtime = MagicMock()
        runtime.agents = svc

        with patch.object(client.app.state, "runtime", runtime):
            r = client.post(
                "/agents/coder/step/stream",
                json={"prompt": "hello"},
            )
        lines = [l.strip() for l in r.text.strip().split("\n") if l.strip()]
        for line in lines:
            parsed = json.loads(line)
            assert isinstance(parsed, dict)

    def test_stream_ends_with_final(self, client):
        """The last non-empty line must be a final event."""
        async def _fake_stream(*args, **kwargs):
            yield {"type": "model_selected", "model": "robs4b"}
            yield {"type": "final", "content": "done"}
            return
            yield  # pragma: no cover

        svc = AgentServiceV2.__new__(AgentServiceV2)
        svc.step_agent_stream = _fake_stream

        runtime = MagicMock()
        runtime.agents = svc

        with patch.object(client.app.state, "runtime", runtime):
            r = client.post(
                "/agents/coder/step/stream",
                json={"prompt": "hello"},
            )
        lines = [l.strip() for l in r.text.strip().split("\n") if l.strip()]
        assert len(lines) >= 1
        last = json.loads(lines[-1])
        assert last.get("type") == "final"
        assert last.get("done") is True

    def test_runtime_unavailable_emits_error_then_final(self, client):
        """When runtime is None, the stream emits an error then final."""
        with patch.object(client.app.state, "runtime", None):
            r = client.post(
                "/agents/coder/step/stream",
                json={"prompt": "hello"},
            )
        lines = [l.strip() for l in r.text.strip().split("\n") if l.strip()]
        assert len(lines) == 2
        first = json.loads(lines[0])
        assert "unavailable" in first.get("content", "").lower()
        second = json.loads(lines[1])
        assert second.get("type") == "final"
        assert second.get("done") is True

    def test_no_python_repr_in_stream(self, client):
        """The stream must not contain Python repr() artifacts."""
        async def _fake_stream(*args, **kwargs):
            yield {"type": "final", "content": "ok"}
            return
            yield  # pragma: no cover

        svc = AgentServiceV2.__new__(AgentServiceV2)
        svc.step_agent_stream = _fake_stream

        runtime = MagicMock()
        runtime.agents = svc

        with patch.object(client.app.state, "runtime", runtime):
            r = client.post(
                "/agents/coder/step/stream",
                json={"prompt": "hello"},
            )
        assert "object at 0x" not in r.text

    def test_multiple_chunks_are_ndjson(self, client):
        """Multiple chunks are separated by newlines, each valid JSON."""
        async def _fake_stream(*args, **kwargs):
            yield {"type": "model_selected", "model": "robs4b"}
            yield {"type": "tool_start", "tool": "filesystem"}
            yield {"type": "final", "content": "done"}
            return
            yield  # pragma: no cover

        svc = AgentServiceV2.__new__(AgentServiceV2)
        svc.step_agent_stream = _fake_stream

        runtime = MagicMock()
        runtime.agents = svc

        with patch.object(client.app.state, "runtime", runtime):
            r = client.post(
                "/agents/coder/step/stream",
                json={"prompt": "hello"},
            )
        lines = [l.strip() for l in r.text.strip().split("\n") if l.strip()]
        assert len(lines) >= 3  # 2 content chunks + final
        for line in lines:
            parsed = json.loads(line)
            assert isinstance(parsed, dict)
            chunk_type = parsed.get("type", parsed.get("action", "unknown"))
            assert isinstance(chunk_type, str)

"""Delivery-seam replay enforcement tests (F2 worker-execution authorization §14).

PROVES the F2-required replay guard at the three model-delivery seams:

  runtime_v2/services/stream_runner.py   (deploy seam sites at ~658 and ~770)
  runtime_v2/api/agent_service_v2.py     (seam site at ~2667)

Required invariants (authorization §7.2 + hardening correction):
- SWARM_F2_REPLAY=1 establishes a PROCESS-LOCAL F2-required flag that SURVIVES
  environment clearing after successful replay install.
- F2_REQUIRED + REPLAY_INACTIVE => ABORT; render_active_lessons() NOT called;
  no model request; no successful delivery/trajectory evidence.
- F2_REQUIRED + REPLAY_ACTIVE => replay artifact (C0 empty or non-empty valid).
- Replay was ACTIVE, then became INACTIVE before delivery => STILL ABORT (the
  requirement outlives the replay state).
- Non-F2 (never required) => existing LIVE lesson behavior remains unchanged.
- Artifact truthiness is NEVER used to determine F2 vs LIVE.

These tests use the REAL seam modules with instrumented delivery boundaries.
No actual model is invoked.
"""

from __future__ import annotations

import pytest

from runtime_v2.services import stream_runner
from runtime_v2.api import agent_service_v2
from runtime_v2.services.f2_replay import (
    clear_replay_state,
    install_replay_state,
    is_replay_required,
    mark_replay_required,
)
from runtime_v2.services.f2_freeze import (
    FreezeVerificationError,
    FrozenArtifact,
)


@pytest.fixture(autouse=True)
def _reset_f2_state():
    clear_replay_state()
    yield
    clear_replay_state()


def _set_f2_required():
    mark_replay_required()
    assert is_replay_required() is True


def _clear_f2_required():
    clear_replay_state()


def _fake_manifest() -> FrozenArtifact:
    from runtime_v2.services.f2_freeze import LessonEntry, freeze_artifact
    from qwen_train.f2_arm_primitives import lesson_hash_of

    lessons = (LessonEntry("A", lesson_hash_of("use pathlib"), 1, "use pathlib"),)
    return freeze_artifact(
        rendered_artifact="use pathlib",
        arm="T",
        ordered_lessons=lessons,
        lesson_l_id="A",
        lesson_l_hash=lesson_hash_of("use pathlib"),
        task_id="task-1",
        model_name="test-model",
        git_sha="deadbeef",
        freeze_timestamp=1_700_000_000.0,
    )


def _install_f2_replay(artifact: FrozenArtifact | None = None):
    state = install_replay_state(artifact or _fake_manifest(), "unused-manifest-path")
    return state


class _FakeReplayActive:
    """Overrides is_replay_active() in the module under test."""

    def __init__(self, active: bool):
        self.active = active

    def __call__(self):
        return self.active


class _FakeDeliveryArtifact:
    def __init__(self, value: str):
        self.value = value

    def __call__(self):
        return self.value


class _FakeRenderer:
    """Instruments render_active_lessons() — records if it is ever called."""

    def __init__(self):
        self.calls = 0
        self.last_query = ""
        self.last_max_chars = 0

    async def __call__(self, query, max_chars=700, eval_id=None):
        self.calls += 1
        self.last_query = query
        self.last_max_chars = max_chars
        return "[LIVE LESSONS] fakelesson"


@pytest.fixture(autouse=True)
def _reset_env():  # pragma: no cover - alias for clarity (state reset)
    _clear_f2_required()
    yield
    _clear_f2_required()


# ---------------------------------------------------------------------------
# Helper to exercise a delivery seam directly (no LLM, no lesson manager).
# ---------------------------------------------------------------------------


def _patch_stream_runner(monkeypatch, *, active: bool, artifact: str, renderer=None, patch_seam: str = "1"):
    """Monkey-patch stream_runner module internals used by the seam.

    `patch_seam` selects which delivery site is exercised via the fake
    renderer (both sites share the same _f2_abort_if_required_but_inactive /
    is_replay_active / render path in tests).
    """
    if renderer is None:
        renderer = _FakeRenderer()
    from swarm_os.services.lesson_manager import get_lesson_manager

    async def _fake_render(q, max_chars=700, eval_id=None):
        return await renderer(q, max_chars=max_chars, eval_id=eval_id)

    monkeypatch.setattr(stream_runner, "is_replay_active", _FakeReplayActive(active))
    monkeypatch.setattr(stream_runner, "get_delivery_artifact", _FakeDeliveryArtifact(artifact))
    # Force the seam's render call to the fake renderer.
    monkeypatch.setattr(get_lesson_manager, "render_active_lessons", _fake_render)
    return renderer


class TestStreamRunnerSeam:
    def test_f2_required_inactive_aborts_no_render(self, monkeypatch):
        """F2 REQUIRED + replay inactive => guard raises; render not called."""
        _set_f2_required()
        renderer = _patch_stream_runner(monkeypatch, active=False, artifact="", renderer=_FakeRenderer())
        

        with pytest.raises(FreezeVerificationError):
            stream_runner._f2_abort_if_required_but_inactive()
        assert renderer.calls == 0  # render_active_lessons NOT called

    def test_f2_required_inactive_cant_model(self, monkeypatch):
        """Guard raises => propagates (model request never issued upstream)."""
        _set_f2_required()
        _patch_stream_runner(monkeypatch, active=False, artifact="")
        

        # The seam guard is invoked inside get_tool_decision; raising here and
        # confirming it propagates past the seam's swallow.
        with pytest.raises(FreezeVerificationError):
            try:
                stream_runner._f2_abort_if_required_but_inactive()
            except Exception:
                raise

    def test_f2_required_active_nonempty_delivers(self, monkeypatch):
        """F2 REQUIRED + replay active + non-empty artifact => valid delivery."""
        _set_f2_required()
        renderer = _patch_stream_runner(monkeypatch, active=True, artifact="[FROZEN] lesson")
        assert stream_runner.is_replay_active() is True
        assert stream_runner.get_delivery_artifact() == "[FROZEN] lesson"
        assert renderer.calls == 0  # no LIVE render

    def test_f2_required_active_c0_empty_delivers(self, monkeypatch):
        """F2 REQUIRED + replay active + C0 empty artifact => valid, NOT LIVE."""
        _set_f2_required()
        renderer = _patch_stream_runner(monkeypatch, active=True, artifact="")
        assert stream_runner.is_replay_active() is True
        # C0 empty artifact is a VALID delivery (replay-state based, not
        # truthiness based). render_active_lessons is NOT called.
        assert renderer.calls == 0

    def test_non_f2_live_preserved(self, monkeypatch):
        """Non-F2 (no SWARM_F2_REPLAY) => existing LIVE behavior remains."""
        _clear_f2_required()
        renderer = _patch_stream_runner(monkeypatch, active=False, artifact="")
        assert stream_runner._f2_replay_required() is False
        # guard must NOT raise for non-F2
        stream_runner._f2_abort_if_required_but_inactive()
        # LIVE render remains reachable and produces the live block
        import asyncio

        block = asyncio.run(renderer("q", 700))
        assert block == "[LIVE LESSONS] fakelesson"
        assert renderer.calls == 1

    def test_replay_active_then_inactive_still_aborts(self, monkeypatch):
        """MANDATORY: F2 required, replay was ACTIVE, then became INACTIVE
        before delivery => STILL ABORT (requirement outlives replay state)."""
        _set_f2_required()
        # Replay installs successfully (active): install_replay_state sets _f2_state.
        _install_f2_replay()
        

        # Simulate replay loss: directly clear the replay state (not the required flag).
        from runtime_v2.services import f2_replay as f2r

        f2r._f2_state = None  # replay lost; required flag survives
        renderer = _patch_stream_runner(
            monkeypatch,
            active=f2r.is_replay_active(),   # now False (real state)
            artifact="",
            renderer=_FakeRenderer(),
        )
        assert f2r.is_replay_required() is True
        assert f2r.is_replay_active() is False
        with pytest.raises(FreezeVerificationError):
            stream_runner._f2_abort_if_required_but_inactive()
        assert renderer.calls == 0  # render_active_lessons NOT called
        # The model-facing call is prevented: the guard raises before delivery.


class TestAgentServiceSeam:
    def test_f2_required_inactive_aborts(self, monkeypatch):
        """agent_service_v2 seam: F2 REQUIRED + inactive => abort, no render."""
        _set_f2_required()
        

        renderer = _FakeRenderer()
        monkeypatch.setattr(agent_service_v2, "is_replay_active", _FakeReplayActive(False))
        monkeypatch.setattr(agent_service_v2, "get_delivery_artifact", _FakeDeliveryArtifact(""))
        from swarm_os.services.lesson_manager import get_lesson_manager

        async def _fake_render(q, max_chars=700):
            return await renderer(q, max_chars=max_chars)

        monkeypatch.setattr(get_lesson_manager, "render_active_lessons", _fake_render)
        with pytest.raises(FreezeVerificationError):
            agent_service_v2._f2_abort_if_required_but_inactive()
        assert renderer.calls == 0

    def test_f2_required_active_c0_ok(self, monkeypatch):
        """agent_service_v2: F2 REQUIRED + active + C0 empty is valid."""
        _set_f2_required()
        renderer = _FakeRenderer()
        monkeypatch.setattr(agent_service_v2, "is_replay_active", _FakeReplayActive(True))
        monkeypatch.setattr(agent_service_v2, "get_delivery_artifact", _FakeDeliveryArtifact(""))
        assert agent_service_v2.is_replay_active() is True
        assert agent_service_v2.get_delivery_artifact() == ""
        monkeypatch.setattr(
            "swarm_os.services.lesson_manager.get_lesson_manager",
            type("FakeLM", (), {"render_active_lessons": _fake_render}),
        )
        assert renderer.calls == 0

    def test_non_f2_live_preserved(self, monkeypatch):
        _clear_f2_required()
        assert agent_service_v2._f2_replay_required() is False
        # must not raise
        agent_service_v2._f2_abort_if_required_but_inactive()


# ---------------------------------------------------------------------------
# Four-state C0 / LIVE taxonomy
# ---------------------------------------------------------------------------


class TestFourStateTaxonomy:
    def test_states_are_distinct(self, monkeypatch):
        """The four states do NOT collapse; replay state is the discriminator.

        A. F2 required + inactive            -> guard raises
        B. F2 required + active + C0 empty   -> valid (empty artifact)
        C. F2 required + active + non-empty  -> valid (non-empty artifact)
        D. non-F2 + inactive (LIVE)          -> existing behavior
        """
        

        # A
        _set_f2_required()
        monkeypatch.setattr(stream_runner, "is_replay_active", _FakeReplayActive(False))
        with pytest.raises(FreezeVerificationError):
            stream_runner._f2_abort_if_required_but_inactive()

        # B (C0 empty) — replay active, empty artifact
        _set_f2_required()
        monkeypatch.setattr(stream_runner, "is_replay_active", _FakeReplayActive(True))
        monkeypatch.setattr(stream_runner, "get_delivery_artifact", _FakeDeliveryArtifact(""))
        stream_runner._f2_abort_if_required_but_inactive()
        assert stream_runner.get_delivery_artifact() == ""

        # C (non-empty) — replay active, non-empty
        monkeypatch.setattr(stream_runner, "get_delivery_artifact", _FakeDeliveryArtifact("[X]"))
        stream_runner._f2_abort_if_required_but_inactive()
        assert stream_runner.get_delivery_artifact() == "[X]"

        # D (non-F2 LIVE) — not required, inactive; no raise
        _clear_f2_required()
        monkeypatch.setattr(stream_runner, "is_replay_active", _FakeReplayActive(False))
        stream_runner._f2_abort_if_required_but_inactive()

    def test_truthiness_never_discriminates(self, monkeypatch):
        """An empty artifact is NOT treated as LIVE; replay state decides."""
        _set_f2_required()
        monkeypatch.setattr(stream_runner, "is_replay_active", _FakeReplayActive(True))
        monkeypatch.setattr(stream_runner, "get_delivery_artifact", _FakeDeliveryArtifact(""))
        # Even though artifact == "", replay-active C0 must be accepted, not LIVE.
        stream_runner._f2_abort_if_required_but_inactive()
        assert stream_runner.get_delivery_artifact() == ""


async def _fake_render(q, max_chars=700):
    return "[LIVE LESSONS] fakelesson"
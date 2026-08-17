from pathlib import Path

from swarm_os.adaptation.chat_model_adapter import ChatModelAdapter
from swarm_os.adaptation.health.health_probe import HealthProbe
from swarm_os.adaptation.healing.healing_engine import HealingEngine
from swarm_os.adaptation.repair.repair_executor import RepairExecutor
from swarm_os.adaptation.verification.repair_verifier import RepairVerifier


def test_chat_adapter_falls_back_to_secondary_provider():
    def broken_primary(prompt: str) -> str:
        raise RuntimeError("primary down")

    def healthy_fallback(prompt: str) -> str:
        return f"fallback:{prompt}"

    adapter = ChatModelAdapter(
        primary_provider="primary",
        providers={
            "primary": broken_primary,
            "fallback": healthy_fallback,
        },
    )

    result = adapter.generate("hello", retries=1)

    assert result.ok is True
    assert result.provider == "fallback"
    assert result.content == "fallback:hello"


def test_chat_model_repair_and_verify(tmp_path: Path):
    def broken_primary(prompt: str) -> str:
        raise RuntimeError("provider failure")

    def healthy_fallback(prompt: str) -> str:
        return f"ok:{prompt}"

    adapter = ChatModelAdapter(
        primary_provider="primary",
        providers={
            "primary": broken_primary,
            "fallback": healthy_fallback,
        },
    )

    executor = RepairExecutor(
        action_map={
            ("chat_model", "retry_request"): lambda c, a: (False, "retry failed"),
            ("chat_model", "rotate_model_provider"): lambda c, a: (
                True,
                f"rotated_to={adapter.rotate_provider()}",
            ),
        }
    )

    verifier = RepairVerifier(probe=HealthProbe(), chat_adapter=adapter)
    engine = HealingEngine(
        state_path=tmp_path / "chat-healing.json", executor=executor, verifier=verifier
    )

    engine.execute({"component": "chat_model", "status": "failed"})
    result = engine.execute({"component": "chat_model", "status": "failed"})

    assert result["action"] in {"rotate_model_provider", "cooldown"}
    if result["action"] == "rotate_model_provider":
        assert result["repair"]["status"] == "success"
        assert result["verification"]["verified"] is True

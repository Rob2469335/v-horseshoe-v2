"""Ling (InclusionAI ultra-cheap worker) fallback + pricing tests."""
import pytest


def test_ling_flash_fallback_present_when_key(monkeypatch):
    from runtime_v2.services import fallback_manager as fm
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    entries = fm._get_ling_flash_fallback()
    models = [e["model"] for e in entries]
    assert "openrouter/inclusionai/ling-2.6-flash" in models
    assert "openrouter/inclusionai/ling-3.0-flash:free" in models


def test_ling_flash_fallback_noop_without_key(monkeypatch):
    from runtime_v2.services import fallback_manager as fm
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    assert fm._get_ling_flash_fallback() == []


def test_ling_2_6_is_cloud_not_local():
    from runtime_v2.services.fallback_manager import _is_local_model
    assert _is_local_model("openrouter/inclusionai/ling-2.6-flash") is False


@pytest.mark.parametrize("model,expected", [
    ("openrouter/inclusionai/ling-2.6-flash", 0.04),          # 1M in + 1M out @ 0.01/0.03
    ("openrouter/inclusionai/ling-3.0-flash:free", 0.0),
])
def test_ling_pricing(model, expected):
    from runtime_v2.services.usage_log import estimate_cost
    assert estimate_cost(model, 1_000_000, 1_000_000) == expected

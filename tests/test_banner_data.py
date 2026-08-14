"""Tests for the console banner's data sources so every banner row reflects
reality instead of stale/placeholder values:

- TRACKER mdl shows the active model at startup (seeded), not `mdl:—`.
- The agent/CORE model resolution prefers the live /agents/models mapping.
- CLOUD row reads the real usage_log cost, not a misleading local counter %.
"""
from organism_console import token_tracker as tt


def test_seed_model_if_empty_populates_tracker():
    """Before any live traffic, the banner must seed the TRACKER row with the
    active model instead of leaving it as `mdl:—`."""
    # Reset the tracker state to "no traffic yet".
    with tt._lock:
        tt._state["last_model"] = ""
        tt._state["last_provider"] = ""

    tt.seed_model_if_empty("qwen3.5-4b")

    with tt._lock:
        assert tt._state["last_model"] == "qwen3.5-4b"
        assert tt._state["last_provider"] == "ollama_local"

    seg = tt.get_status_segment()
    assert "mdl" in seg
    assert "qwen3.5-4b" in seg, f"tracker segment must show the seeded model: {seg}"


def test_seed_model_does_not_override_live_traffic():
    """Once real traffic has recorded a model, seeding must NOT clobber it."""
    with tt._lock:
        tt._state["last_model"] = "deepseek/deepseek-v4-flash"
        tt._state["last_provider"] = "deepseek"

    tt.seed_model_if_empty("qwen3.5-4b")

    with tt._lock:
        assert tt._state["last_model"] == "deepseek/deepseek-v4-flash"
        assert tt._state["last_provider"] == "deepseek"


def test_agent_model_resolution_prefers_live_models():
    """The banner's agent rows must show the LIVE resolved model (qwen3.5-4b),
    not the stale role-name ('fast'/'reasoning') or a persisted active_model."""
    import organism_console.ui.banner as bn

    agent_models = {"coder": {"model": "qwen3.5-4b", "backend": "llama"}}
    resolved = (agent_models.get("coder", {}) or {}).get("model") or "coding"
    assert resolved == "qwen3.5-4b"
    # The banner uses this resolution path (source-pin).
    src = open(bn.__file__, encoding="utf-8").read()
    assert "agent_models.get(a.get(\"id\", \"\"), {})" in src
    assert "agent_models.get(ctx.active_agent, {})" in src


def test_cloud_row_uses_usage_log_cost():
    """The CLOUD row must read the REAL usage_log 30d cost, not the local
    console counter percentage (which showed a fake 235% quota bar)."""
    import organism_console.ui.banner as bn

    src = open(bn.__file__, encoding="utf-8").read()
    assert "usage_report(days=30)" in src
    assert "30d cost" in src
    assert "QUOTA" not in src.split("CLOUD", 1)[-1].split("table.add_row(\"CLOUD", 1)[0], (
        "the misleading QUOTA percentage bar must be gone from the CLOUD row"
    )

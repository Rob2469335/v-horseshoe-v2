"""Tests for the genetic mutation loop's related-test discovery (EVO-3)."""

from swarm_os.services.genetic_mutation_loop import _find_related_test_files


def test_mutation_loop_finds_related_tests_not_hardcoded():
    """EVO-3: the mutation loop must run the tests related to the MUTATED file,
    never a hardcoded suite for an unrelated target. A tool_executor mutation
    must find the tests that exercise tool_executor, and an agent_service_v2
    mutation must find the agent-loop tests (not test_agentic_loop.py, which
    never references the agent service at all)."""
    tool_tests = _find_related_test_files("runtime_v2/services/tool_executor.py")
    assert any("test_opencode_parity.py" in t for t in tool_tests), (
        "tool_executor mutation should run test_opencode_parity.py"
    )

    agent_tests = _find_related_test_files("runtime_v2/api/agent_service_v2.py")
    assert any("test_checkpointing.py" in t for t in agent_tests), (
        "agent_service_v2 mutation should run the agent-loop tests"
    )
    assert not any("test_agentic_loop.py" in t for t in agent_tests), (
        "test_agentic_loop.py never references agent_service_v2 — it is NOT a "
        "related test for that file"
    )


def test_mutation_loop_related_tests_tolerate_unknown_file():
    """A file with no related test returns [] without raising — the caller then
    falls back to the default suite instead of crashing. The unknown module name
    is built at runtime so it cannot appear in any test file's content."""
    unknown = f"swarm_os/services/x{hash('mutation')}_{id(object())}.py"
    assert _find_related_test_files(unknown) == []


def test_mutation_loop_sync_writes_offloaded_to_thread():
    """The async mutation daemon must not block its event loop on sync file
    writes. The three disk writes (copy2 of the pending mutation, the
    metadata.json write, and the mutation-history write) must all run through
    asyncio.to_thread."""
    import inspect

    import swarm_os.services.genetic_mutation_loop as gml

    src = inspect.getsource(gml.run_genetic_mutation)
    assert "await asyncio.to_thread(shutil.copy2, sandbox_file, pending_file)" in src
    assert "metadata.json" in src
    assert "await asyncio.to_thread(" in src
    # The history write must be inside a to_thread call, never a bare write_text.
    history_block = src.split("HISTORY_FILE.parent.mkdir")[1]
    assert "asyncio.to_thread(" in history_block
    assert "HISTORY_FILE.write_text(json.dumps" not in history_block


def test_mutation_loop_llm_call_fails_over_across_providers():
    """The mutation-loop LLM call must fail over to the NEXT provider when the
    chosen one drops out (free tiers cycle/expire often) — never retry a dead
    provider until the chain is exhausted. The proven cross-provider failover
    seam is build_kwargs() + litellm.acompletion(**kwargs) with a per-provider
    dict fallback list (each scoped to its own endpoint/key), the same call
    complete_for_tool_decision uses.

    A litellm.Router built over distinct model_name groups does NOT
    cross-failover without an explicit fallbacks arg (verified empirically), so
    this pins the dict-fallback form: build_kwargs imports present, the
    acompletion call carries the fallbacks-bearing kwargs, and no Router is
    constructed in the retry block."""
    import inspect

    import swarm_os.services.genetic_mutation_loop as gml

    src = inspect.getsource(gml.run_genetic_mutation)
    # The chain must come from the LIVE fallback list, filtered to cloud models.
    assert "await get_live_fallbacks(mode=routing_mode)" in src
    assert 'not _is_local_model(f["model"])' in src
    # Per-provider dict fallbacks must reach the LLM call via build_kwargs.
    assert "build_kwargs(" in src
    assert "res = await acompletion(**kwargs)" in src
    # The retry block must NOT build a Router (it would silently lose failover).
    assert "build_router" not in src
    # The chosen model must be the free-first analysis-cloud default when a free
    # key is present, never a hardcoded single provider.
    assert "_analysis_cloud_model()" in inspect.getsource(gml)

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

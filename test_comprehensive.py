#!/usr/bin/env python3
"""Comprehensive test of agent system fixes."""
import asyncio
import json
import logging
from runtime_v2.services.stream_runner import _extract_json, _normalize_model_json
from runtime_v2.api.agent_service_v2 import AgentServiceV2

logging.basicConfig(level=logging.WARNING)
log = logging.getLogger(__name__)

def test_json_extraction():
    """Test the improved JSON extraction."""
    print("Testing JSON extraction...")
    
    test_cases = [
        # With [final] prefix
        ("[final]{\"action\": \"final\", \"response\": \"Done\"}", {"action": "final", "response": "Done"}),
        # With [delegate] prefix  
        ("[delegate]{\"action\": \"delegate\", \"target_agent\": \"coder\"}", {"action": "delegate", "target_agent": "coder"}),
        # Clean JSON
        ("{\"action\": \"final\", \"response\": \"Hello\"}", {"action": "final", "response": "Hello"}),
        # JSON with markdown
        ("```json\n{\"action\": \"web_search\", \"query\": \"test\"}\n```", {"action": "web_search", "query": "test"}),
        # Text before and after
        ("Some text before {\"action\": \"final\"} and after", {"action": "final"}),
        # Empty response (should default)
        ("", {"action": "final", "response": "Task processed."}),
        ("[final]", {"action": "final", "response": "Task processed."}),
    ]
    
    passed = 0
    for input_str, expected in test_cases:
        try:
            result = _extract_json(_normalize_model_json(input_str))
            # Check key fields match
            if result.get("action") == expected.get("action"):
                passed += 1
                print(f"  [OK] {input_str[:40]}")
            else:
                print(f"  [FAIL] {input_str[:40]} - got {result.get('action')}, expected {expected.get('action')}")
        except Exception as e:
            print(f"  [ERROR] {input_str[:40]} - {e}")
    
    print(f"JSON extraction: {passed}/{len(test_cases)} tests passed")
    return passed == len(test_cases)

def test_agents_registered():
    """Test that all 8 agents are registered."""
    print("\nTesting agent registration...")
    
    service = AgentServiceV2()
    agents = service.list_agents()
    
    expected_agents = [
        "coordinator", "planner", "researcher", "executor",
        "coder", "tool-runner", "reviewer", "debugger"
    ]
    
    registered_ids = [a["id"] for a in agents]
    
    if len(agents) != 8:
        print(f"  [FAIL] Expected 8 agents, got {len(agents)}")
        return False
    
    for agent_id in expected_agents:
        if agent_id not in registered_ids:
            print(f"  [FAIL] Missing agent: {agent_id}")
            return False
        agent = service.get_agent(agent_id)
        print(f"  [OK] {agent_id}: {agent['role']}")
    
    print(f"Agent registration: 8/8 agents registered")
    return True

def test_tool_definitions():
    """Test that tool definitions are correct."""
    print("\nTesting tool definitions...")
    
    from runtime_v2.prompts.system_prompts import _AGENT_TOOLS, _ROLE_RULES
    
    # Check all 8 agents have tool definitions
    expected = ["coordinator", "planner", "researcher", "executor",
                "coder", "tool-runner", "reviewer", "debugger"]
    
    all_good = True
    for agent in expected:
        if agent not in _AGENT_TOOLS:
            print(f"  [FAIL] No tools defined for {agent}")
            all_good = False
        elif agent not in _ROLE_RULES:
            print(f"  [FAIL] No role rules defined for {agent}")
            all_good = False
        else:
            tools = _AGENT_TOOLS[agent]
            print(f"  [OK] {agent}: {len(tools)} tools - {', '.join(tools[:3])}")
    
    return all_good

def test_fallback_manager():
    """Test that fallback manager works."""
    print("\nTesting fallback manager...")
    
    async def check_fallbacks():
        from runtime_v2.services.fallback_manager import get_live_fallbacks, get_fallback_stats
        try:
            fallbacks = await get_live_fallbacks()
            stats = get_fallback_stats()
            
            if fallbacks:
                print(f"  [OK] Fallback manager has {len(fallbacks)} models")
                print(f"    Stats: {stats}")
                for fb in fallbacks[:3]:
                    print(f"      - {fb['model']} ({fb['provider']})")
                return True
            else:
                print(f"  [FAIL] No fallback models available")
                return False
        except Exception as e:
            print(f"  [FAIL] Error loading fallbacks: {e}")
            return False
    
    return asyncio.run(check_fallbacks())

def main():
    """Run all tests."""
    print("=" * 60)
    print("COMPREHENSIVE AGENT SYSTEM TEST")
    print("=" * 60)
    
    tests = [
        ("JSON Extraction", test_json_extraction),
        ("Agent Registration", test_agents_registered),
        ("Tool Definitions", test_tool_definitions),
        ("Fallback Manager", test_fallback_manager),
    ]
    
    results = []
    for name, test_func in tests:
        try:
            result = test_func()
            results.append((name, result))
        except Exception as e:
            print(f"\n[ERROR] {name} FAILED with exception: {e}")
            import traceback
            traceback.print_exc()
            results.append((name, False))
    
    print("\n" + "=" * 60)
    print("TEST SUMMARY")
    print("=" * 60)
    
    passed = sum(1 for _, result in results if result)
    total = len(results)
    
    for name, result in results:
        status = "PASS" if result else "FAIL"
        print(f"[{status}]: {name}")
    
    print(f"\nTotal: {passed}/{total} tests passed")
    
    if passed == total:
        print("\n>>> All tests PASSED! The agent system is fixed and ready.")
        return 0
    else:
        print(f"\n>>> {total - passed} tests FAILED")
        return 1

if __name__ == "__main__":
    exit(main())

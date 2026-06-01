import pytest
from swarm_os.kernel.brain import (
    TOOL_PRIMARY_MAPPING, 
    IdentifiableVectorSCM, 
    UncertaintyWeightedRouter, 
    make_swarm_brain_v10_ultimate
)

class TestSmoke70Tasks:
    def test_70_tasks_complete(self):
        # CORRECT initialization: build router with tools + scm
        tools = list(TOOL_PRIMARY_MAPPING.keys())
        scm = IdentifiableVectorSCM(tools)
        router = UncertaintyWeightedRouter(tools=tools, scm=scm)
        brain = make_swarm_brain_v10_ultimate(router, task_domain="coding")
        
        tasks = (
            ["Debug this Python code"] * 5 +
            ["Fix this bug in JavaScript"] * 3 +
            ["Optimize this SQL query"] * 3 +
            ["Refactor this React component"] * 3 +
            ["Write unit tests"] * 3 +
            ["Fix C++ memory leak"] * 3 +
            ["Research climate change"] * 4 +
            ["Summarize AI papers"] * 4 +
            ["Compare quantum computing"] * 4 +
            ["Analyze EV market"] * 4 +
            ["Research blockchain"] * 4 +
            ["Summarize article"] * 5 +
            ["Rewrite paragraph"] * 5 +
            ["Translate to Spanish"] * 5 +
            ["Analyze dataset"] * 5 +
            ["Generate visualization"] * 5 +
            ["Clean CSV data"] * 5
        )
        assert len(tasks) == 70
        
        rewards = []
        tool_counts = []
        for i, t in enumerate(tasks):
            r = brain({"task": t})
            assert r.success, f"Task {i+1} failed: {r.error}"
            assert r.composite_reward > 0.5, f"Task {i+1} reward {r.composite_reward:.3f} <= 0.5"
            assert len(r.tools_used) > 0
            rewards.append(r.composite_reward)
            tool_counts.append(len(r.tools_used))
        
        assert sum(rewards)/len(rewards) > 0.7
        print(f"\n✅ 70/70 PASS | Avg:{sum(rewards)/len(rewards):.3f} | Min:{min(rewards):.3f} | Max:{max(rewards):.3f} | AvgTools:{sum(tool_counts)/len(tool_counts):.2f}")
        print(f"   Tools used: {set(tools for tools in tool_counts)}")

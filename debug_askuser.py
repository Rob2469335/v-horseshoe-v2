import asyncio, httpx, json
import sys
sys.path.insert(0, ".")

import swarm_os.services.agent_service as mod

class MockOrch: pass

class MockResp:
    def __init__(self, content):
        self.content = content
    async def __aenter__(self): return self
    async def __aexit__(self, *a): pass
    def raise_for_status(self): pass
    async def aiter_lines(self):
        payload = {"message": {"content": self.content}, "choices": [{"delta": {"content": self.content}}], "done": False}
        yield json.dumps(payload)
        yield json.dumps({"done": True})

class MockClient:
    def __init__(self, *a, **kw): pass
    async def __aenter__(self): return self
    async def __aexit__(self, *a): pass
    def stream(self, *a, **kw):
        return MockResp('<tool_call name="ask_user">{"question": "yes?"}</tool_call>')

# Patch
mod.reconcile_and_repair_tool_call = lambda a, b, c: ("ask_user", b)
mod.fetch_live_models_if_needed = lambda: None
httpx.AsyncClient = MockClient

svc = mod.AgentService(orchestrator=MockOrch())

async def run():
    chunks = [c async for c in svc.step_agent_stream("planner", "prompt")]
    print("ALL CHUNKS:")
    for i, c in enumerate(chunks):
        print("  [{}] type={!r} content={!r}".format(i, c.get("type"), str(c.get("content",""))[:80]))
    finals = [c for c in chunks if c.get("type") == "final"]
    print("FINALS: {}".format(len(finals)))
    for f in finals:
        print("  content={!r}".format(f.get("content","")))

asyncio.run(run())

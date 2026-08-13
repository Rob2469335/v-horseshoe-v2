import json
import asyncio
import os
import re
from collections import defaultdict, deque
from litellm import acompletion
from runtime_v2.services.memory_core import remember_fact

MODEL = "openai/deepseek-v4-flash"  # rule extraction = correction reasoning → cloud DeepSeek (funded)

async def extract_and_inject_rules():
    print("[Offline Learner] Reading events.jsonl...")
    event_file = "data/events/events.jsonl"
    if not os.path.exists(event_file):
        print(f"[Offline Learner] No events file found at {event_file}.")
        return

    agent_stats = defaultdict(lambda: {"success": 0, "fail": 0})
    tool_stats = defaultdict(lambda: {"success": 0, "fail": 0})
    recent_failures = deque(maxlen=20)
    
    with open(event_file, "r", encoding="utf-8") as f:
        for line in f:
            try:
                data = json.loads(line)
                event_type = data.get("event_type")
                if event_type == "tool_result":
                    payload = data.get("payload", {})
                    agent = payload.get("agent_id", "unknown")
                    tool = payload.get("tool", "unknown")
                    result = payload.get("result", {})
                    success = result.get("ok", False)
                    
                    if success:
                        agent_stats[agent]["success"] += 1
                        tool_stats[tool]["success"] += 1
                    else:
                        agent_stats[agent]["fail"] += 1
                        tool_stats[tool]["fail"] += 1
                        error = result.get("error", "unknown error")
                        recent_failures.append(f"Agent {agent} called {tool} which failed with: {error}")
                elif event_type in ("AGENT_ERROR", "CRASH"):
                    payload = data.get("payload", {})
                    agent = payload.get("agent_id", data.get("agent_id", "unknown"))
                    error = payload.get("error", str(payload))
                    agent_stats[agent]["fail"] += 1
                    recent_failures.append(f"Agent {agent} CRASHED: {error}")
            except Exception:
                continue
    
    print("[Offline Learner] Aggregating historical statistics...")
    stats_summary = "Historical Performance Summary:\n\n"
    stats_summary += "Agent Stats:\n"
    for ag, st in agent_stats.items():
        total = st["success"] + st["fail"]
        rate = (st["success"] / total) * 100 if total > 0 else 0
        stats_summary += f" - {ag}: {st['success']} successes, {st['fail']} failures ({rate:.1f}% success rate)\n"
        
    stats_summary += "\nTool Stats:\n"
    for tl, st in tool_stats.items():
        total = st["success"] + st["fail"]
        rate = (st["success"] / total) * 100 if total > 0 else 0
        stats_summary += f" - {tl}: {st['success']} successes, {st['fail']} failures ({rate:.1f}% success rate)\n"
        
    stats_summary += "\nRecent Notable Failures:\n"
    for f in recent_failures:
        stats_summary += f" - {f}\n"
        
    print("[Offline Learner] Prompting LLM to deduce meta-rules...")
    prompt = f"""
You are the Swarm OS Offline Meta-Learner.
Analyze the following historical event statistics and recent failures.
Deduce 3 to 5 highly specific, actionable rules that agents should follow in the future to improve success rates and avoid these failures.
Format your output as a simple list of rules, one per line. No markdown formatting. No conversational prefix. 

{stats_summary}
"""
    
    try:
        _kwargs = {
            "model": MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 500,
        }
        if "/" in MODEL and not MODEL.startswith("openai/qwen"):
            _base = os.getenv("OPENAI_API_BASE", "")
            _key = os.getenv("OPENAI_API_KEY", "")
            if _base:
                _kwargs["api_base"] = _base
            if _key:
                _kwargs["api_key"] = _key
        else:
            _kwargs.update(api_base="http://127.0.0.1:8080/v1", api_key="llama", custom_llm_provider="openai")
        async with asyncio.timeout(90.0):
            response = await acompletion(**_kwargs)
        rules_text = response.choices[0].message.content.strip()
    except Exception as e:
        print(f"[Offline Learner] LLM failed: {e}")
        return

    print("\n[Offline Learner] Deduced Rules:\n")
    print(rules_text)
    
    # Inject into Qdrant
    print("\n[Offline Learner] Injecting rules into Qdrant memory core...")
    for rule in rules_text.split("\n"):
        rule = rule.strip()
        rule = re.sub(r'^\d+\.\s*', '', rule)
        rule = rule.replace('**', '').strip('-* \t')
        if not rule:
            continue
        try:
            success = await asyncio.to_thread(remember_fact, rule, category="system_rules")
            if success:
                print(f"  [+] Injected: {rule[:60]}...")
            else:
                print(f"  [-] Failed to inject: {rule[:60]}...")
        except Exception as e:
            print(f"  [-] Error injecting: {e}")

    print("[Offline Learner] Offline learning pass complete.")

if __name__ == "__main__":
    asyncio.run(extract_and_inject_rules())

import logging
import httpx
import litellm
import json

log = logging.getLogger(__name__)

class ChatService:
    @staticmethod
    def compact_context_messages(messages: list[dict], max_turns: int = 10, keep_recent: int = 6) -> list[dict]:
        """
        Compacts long conversation histories by summarizing older turns into a single system/context header,
        preventing LLM context flooding on extended agent runs.
        """
        if not messages or len(messages) <= max_turns:
            return messages

        system_msgs = [m for m in messages if m.get("role") == "system"]
        non_system = [m for m in messages if m.get("role") != "system"]

        if len(non_system) <= keep_recent:
            return messages

        older = non_system[:-keep_recent]
        recent = non_system[-keep_recent:]

        compacted_count = len(older)
        summary_lines = []
        for turn in older[:5]:
            role = turn.get("role", "unknown")
            text = str(turn.get("content", ""))[:80].replace("\n", " ")
            summary_lines.append(f"[{role}]: {text}...")

        summary_text = (
            f"<COMPACTED_SUMMARY>\n"
            f"Earlier conversation summary: {compacted_count} earlier turns compacted for token budget.\n"
            f"Sample of older topics:\n" + "\n".join(summary_lines) +
            f"\n</COMPACTED_SUMMARY>"
        )

        compacted_msg = {"role": "system", "content": summary_text}
        return system_msgs + [compacted_msg] + recent

    @staticmethod
    async def autoassign() -> dict[str, str]:
        from runtime_v2.services.fallback_manager import get_live_fallbacks
        from runtime_v2.services.model_registry import update_model_mapping

        # 1. Fetch local llama.cpp models
        local_models = []
        try:
            async with httpx.AsyncClient(timeout=15.0, trust_env=False, proxy=None) as client:
                resp = await client.get("http://127.0.0.1:8080/v1/models", headers={"Authorization": "Bearer llama"})
                if resp.status_code == 200:
                    for m in resp.json().get("data", []):
                        name = m["id"].lower()
                        if "embed" in name or "rerank" in name or "vl" in name or "moondream" in name:
                            continue
                        local_models.append(m["id"])
        except Exception as e:
            raise Exception(f"Failed to fetch local models: {e}")
            
        if not local_models:
            raise Exception("No suitable local chat models found.")
            
        # 2. Get best cloud model
        fallbacks = await get_live_fallbacks()
        cloud_models = [f["model"] for f in fallbacks if not f["model"].startswith("openai/")]
        if not cloud_models:
            best_cloud_model = f"openai/{local_models[-1]}"
        else:
            best_cloud_model = cloud_models[0]
        
        # 3. Formulate Prompt
        prompt = f"""You are an elite AI system architect. 
The user has the following local AI models running: {local_models}
They have a multi-agent system with exactly 8 roles:
- coordinator: High-level routing and synthesis.
- planner: Deep reasoning and system design.
- researcher: Information gathering and summarization.
- executor: Following exact step-by-step instructions.
- coder: Writing complex code.
- tool-runner: Executing API/OS tools (needs strong tool calling).
- reviewer: Pedantic code reviewing and bug finding.
- debugger: Deep logic and error trace analysis.

Based on public benchmark knowledge of these local models (parameter sizes, domain strengths), assign the best model to each of the 8 roles.
Rules:
1. ONLY use the models from the provided local list.
2. You can assign the same model to multiple roles if it's the best fit.
3. Return ONLY a raw JSON object mapping the exact role name to the exact model name. No markdown blocks, no formatting. Example: {{"coder": "qwen2.5-coder:7b"}}"""

        # 4. Get LLM response
        litellm_fallbacks = [{"model": m} for m in cloud_models[1:]]
        kwargs = {
            "model": best_cloud_model,
            "fallbacks": litellm_fallbacks,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.1,
            "timeout": 60.0,
        }
        if not best_cloud_model.startswith("openrouter/"):
            kwargs["response_format"] = {"type": "json_object"}
        if best_cloud_model.startswith("openai/"):
            kwargs["api_base"] = "http://127.0.0.1:8080/v1"
            kwargs["api_key"] = "llama"
            
        resp = await litellm.acompletion(**kwargs)
        content = resp.choices[0].message.content or "{}"
        
        # Clean up possible markdown if provider ignored format
        content = content.replace("```json", "").replace("```", "").strip()
        try:
            mapping = json.loads(content)
        except json.JSONDecodeError:
            start = content.find("{")
            end = content.rfind("}")
            if start != -1 and end != -1:
                mapping = json.loads(content[start:end+1])
            else:
                mapping = {}
        
        # 5. Verify and apply
        valid_roles = ["coordinator", "planner", "researcher", "executor", "coder", "tool-runner", "reviewer", "debugger", "tool-maker", "code_analyzer"]
        final_mapping = {}
        for r in valid_roles:
            if r in mapping and mapping[r] in local_models:
                final_mapping[r] = mapping[r]
            elif local_models:
                final_mapping[r] = local_models[0]
                
        update_model_mapping(final_mapping)
        return final_mapping

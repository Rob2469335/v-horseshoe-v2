# swarm_os/kernel/model_router.py
import os
import logging

logger = logging.getLogger(__name__)

class ModelRouter:
    @staticmethod
    def build_fallback_chain(agent_id: str, live_nvidia: list, live_openrouter: list) -> list:

        if agent_id == "coordinator":
            fallback_chain = [("danielsheep/Qwen3-Coder-30B-A3B-Instruct-1M-Unsloth:UD-IQ3_XXS", "ollama")]

        elif agent_id == "planner":
            fallback_chain = [("danielsheep/Qwen3-Coder-30B-A3B-Instruct-1M-Unsloth:UD-IQ3_XXS", "ollama")]

        elif agent_id == "executor":
            fallback_chain = [("danielsheep/Qwen3-Coder-30B-A3B-Instruct-1M-Unsloth:UD-IQ3_XXS", "ollama")]

        elif agent_id == "tool-runner":
            fallback_chain = [("danielsheep/Qwen3-Coder-30B-A3B-Instruct-1M-Unsloth:UD-IQ3_XXS", "ollama")]

        elif agent_id == "coder":
            fallback_chain = [("danielsheep/Qwen3-Coder-30B-A3B-Instruct-1M-Unsloth:UD-IQ3_XXS", "ollama")]

        elif agent_id == "debugger":
            fallback_chain = [("danielsheep/Qwen3-Coder-30B-A3B-Instruct-1M-Unsloth:UD-IQ3_XXS", "ollama")]

        elif agent_id == "reviewer":
            fallback_chain = [("danielsheep/Qwen3-Coder-30B-A3B-Instruct-1M-Unsloth:UD-IQ3_XXS", "ollama")]

        else:
            fallback_chain = [("danielsheep/Qwen3-Coder-30B-A3B-Instruct-1M-Unsloth:UD-IQ3_XXS", "ollama")]

        env_model = os.environ.get("ZENITH_MODEL")
        if env_model and env_model.strip():
            if "gemini" in env_model.lower():
                env_provider = "gemini"
            elif "nvidia" in env_model.lower():
                env_provider = "nvidia"
            elif "/" in env_model:
                env_provider = "openrouter"
            else:
                env_provider = "ollama"
            custom_item = (env_model.strip(), env_provider)
            if custom_item not in fallback_chain:
                fallback_chain.insert(0, custom_item)

        return fallback_chain
# swarm_os/kernel/model_router.py
import os
import logging

logger = logging.getLogger(__name__)

class ModelRouter:
    @staticmethod
    def build_fallback_chain(agent_id: str, live_nvidia: list, live_openrouter: list) -> list:
        gemini_key = os.environ.get("GEMINI_API_KEY", "").strip()
        groq_key = os.environ.get("GROQ_API_KEY", "").strip()

        gemini_key = os.environ.get("GEMINI_API_KEY", "").strip()
        groq_key = os.environ.get("GROQ_API_KEY", "").strip()

        gemini_key = os.environ.get("GEMINI_API_KEY", "").strip()
        groq_key = os.environ.get("GROQ_API_KEY", "").strip()

        gemini_key = os.environ.get("GEMINI_API_KEY", "").strip()
        groq_key = os.environ.get("GROQ_API_KEY", "").strip()

        if agent_id == "coordinator":
            fallback_chain = [("qwen3:14b", "ollama")]
        elif agent_id in ("executor", "tool-runner"):
            fallback_chain = [("qwen2.5-coder:7b", "ollama")]
        else:
            fallback_chain = []
            if gemini_key:
                fallback_chain.append(("gemini-2.5-flash", "gemini"))
                fallback_chain.append(("gemini-2.0-flash", "gemini"))
            if groq_key:
                fallback_chain.append(("moonshotai/kimi-k2-instruct", "groq"))
                fallback_chain.append(("llama-3.3-70b-versatile", "groq"))
            if not fallback_chain:
                fallback_chain = [("qwen3:14b", "ollama")]
            fallback_chain = []
            if gemini_key:
                fallback_chain.append(("gemini-2.5-flash", "gemini"))
                fallback_chain.append(("gemini-2.0-flash", "gemini"))
            if groq_key:
                fallback_chain.append(("moonshotai/kimi-k2-instruct", "groq"))
                fallback_chain.append(("llama-3.3-70b-versatile", "groq"))
            if not fallback_chain:
                fallback_chain = [("qwen3:14b", "ollama")]
            fallback_chain = []
            if gemini_key:
                fallback_chain.append(("gemini-2.5-flash", "gemini"))
                fallback_chain.append(("gemini-2.0-flash", "gemini"))
            if groq_key:
                fallback_chain.append(("moonshotai/kimi-k2-instruct", "groq"))
                fallback_chain.append(("llama-3.3-70b-versatile", "groq"))
            if not fallback_chain:
                fallback_chain = [("qwen3:14b", "ollama")]
            fallback_chain = []
            if gemini_key:
                fallback_chain.append(("gemini-2.5-flash", "gemini"))
                fallback_chain.append(("gemini-2.0-flash", "gemini"))
            if groq_key:
                fallback_chain.append(("moonshotai/kimi-k2-instruct", "groq"))
                fallback_chain.append(("llama-3.3-70b-versatile", "groq"))
            if not fallback_chain:
                fallback_chain = [("qwen3:14b", "ollama")]
            fallback_chain = [("qwen3-coder:480b-cloud", "ollama")]

        predefined_nvidia = [
            ("meta/llama-3.1-405b-instruct", "nvidia"),
            ("meta/llama-3.3-70b-instruct", "nvidia"),
            ("nvidia/nemotron-3-ultra-550b-a55b:free", "openrouter"),
            ("nvidia/nemotron-3-super-120b-a12b:free", "openrouter"),
        ]

        nvidia_candidates = []
        if live_nvidia:
            for m in live_nvidia:
                m_lower = m.lower()
                is_big = False
                if any(x in m_lower for x in ["405b", "70b", "large", "super", "pro"]):
                    is_big = True
                elif "nemotron" in m_lower and not any(x in m_lower for x in ["nano", "8b", "vl"]):
                    is_big = True

                if is_big and any(x in m_lower for x in ["llama-3", "nemotron", "deepseek", "yi"]):
                    nvidia_candidates.append((m, "nvidia"))

        if nvidia_candidates:
            fallback_chain.extend(nvidia_candidates)
            if live_openrouter:
                for m in live_openrouter:
                    if "nvidia/nemotron" in m.lower() and m.endswith(":free"):
                        fallback_chain.append((m, "openrouter"))
            else:
                fallback_chain.append(("nvidia/nemotron-3-ultra-550b-a55b:free", "openrouter"))
                fallback_chain.append(("nvidia/nemotron-3-super-120b-a12b:free", "openrouter"))
        else:
            fallback_chain.extend(predefined_nvidia)

        predefined_or_free = [
            ("deepseek/deepseek-chat-v3-5:free", "openrouter"),
            ("openrouter/free", "openrouter"),
            ("qwen/qwen3-coder:free", "openrouter"),
            ("deepseek/deepseek-v4-flash:free", "openrouter"),
        ]
        or_free_candidates = []
        if live_openrouter:
            for m in live_openrouter:
                m_lower = m.lower()
                if m_lower.endswith(":free"):
                    if any(x in m_lower for x in ["nemotron", "llama-3", "deepseek", "qwen", "gemma-4", "mixtral", "command-r", "phi-4"]):
                        if not any(x in m_lower for x in ["-8b", "-3b", "-2b", "nano", "mini-code"]):
                            or_free_candidates.append((m, "openrouter"))

        if or_free_candidates:
            for item in or_free_candidates:
                if item not in fallback_chain:
                    fallback_chain.append(item)
        else:
            for item in predefined_or_free:
                if item not in fallback_chain:
                    fallback_chain.append(item)

        fallback_chain.append(("qwen3:14b", "ollama"))
        fallback_chain.append(("qwen2.5-coder:14b", "ollama"))

        env_model = os.environ.get("ZENITH_MODEL")
        if env_model and env_model.strip():
            if "/" in env_model or any(x in env_model.lower() for x in ["openrouter", "deepseek"]):
                env_provider = "openrouter"
            elif "nvidia" in env_model.lower():
                env_provider = "nvidia"
            elif "gemini" in env_model.lower():
                env_provider = "gemini"
            else:
                env_provider = "ollama"

            custom_item = (env_model.strip(), env_provider)
            if custom_item not in fallback_chain:
                fallback_chain.insert(0, custom_item)

        return fallback_chain

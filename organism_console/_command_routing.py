"""Natural language intent routing for CLI commands."""
import json
import re
from typing import Optional

from organism_console._command_context import CommandContext


def route_natural_language_keywords(raw: str) -> tuple[Optional[str], list[str]]:
    clean = raw.lower().strip().strip("?!.")

    if clean in ("show diff", "diff", "git diff", "what changed", "changes", "show changes", "show me what changed"):
        return "diff", []
    if clean in ("status", "health", "check health", "system status", "check status", "how is the system"):
        return "status", []
    if clean in ("help", "commands", "what can you do", "show commands", "list commands", "menu"):
        return "help", []
    if clean in ("commit", "make a commit", "git commit", "save changes", "commit changes"):
        return "commit", []
    if clean in ("heal", "self heal", "run healing", "fix system", "run heal"):
        return "heal", ["run"]
    if clean in ("clear", "reset", "clear history", "reset context", "start over"):
        return "clear", []
    if clean in ("learn", "learn from history", "run learning", "offline learn"):
        return "learn", []
    if clean in ("autofix", "auto fix", "fix yourself", "fix bugs", "heal bugs", "fix all", "repair all", "repair everything"):
        return "autofix", []
    if clean in ("cures", "repair knowledge", "known fixes", "what can you fix"):
        return "cures", []
    if clean in ("repair stats", "repair statistics", "heal stats", "self heal stats"):
        return "repair-stats", []
    if clean in ("model", "models", "picker"):
        return "picker", []
    if clean in ("perf", "performance"):
        return "perf", []
    if clean in ("exit", "quit", "bye", "close", "shutdown"):
        return "exit", []
    if clean in ("tokens", "token usage", "cost", "how many tokens"):
        return "tokens", []
    if clean in ("agents", "list agents", "show agents", "what agents", "agent list"):
        return "agents", []
    if clean in ("benchmark", "run benchmark", "test models"):
        return "benchmark", []
    if clean in ("run simulation", "simulate", "start simulation"):
        return "simulation", ["run"]
    if clean in ("simulation status", "check simulation"):
        return "simulation", ["status"]

    m_search = re.match(r"^(?:search for|find|search memory for)\s+(.+)$", clean)
    if m_search:
        return "search", [m_search.group(1)]

    m_upwork = re.match(r"^(?:analyze upwork job|analyze upwork|upwork)\s+(.+)$", clean)
    if m_upwork:
        return "upwork", [m_upwork.group(1)]

    m_chat_search = re.match(r"^(?:ask librarian|chat search)\s+(.+)$", clean)
    if m_chat_search:
        return "chat-search", [m_chat_search.group(1)]

    m_debate = re.match(r"^(?:debate about|discuss|debate|talk about)\s+(.+)$", clean)
    if m_debate:
        return "debate", [m_debate.group(1)]

    goal_words = ("fix", "implement", "add", "create", "refactor", "change", "run tests", "verify", "debug", "test", "analyze", "audit", "search", "upgrade", "review", "inspect")
    if any(clean.startswith(w) for w in goal_words):
        return "goal", [raw.strip()]

    return None, []


def classify_intent_with_llm(raw: str, ctx: CommandContext) -> tuple[Optional[str], list[str]]:
    model = "qwen3.5-4b"
    if ctx.installed_models:
        for m in ctx.installed_models:
            ml = m.lower()
            if "qwen3.5" in ml or "qwen3" in ml:
                model = m
                break
        else:
            model = ctx.installed_models[0]

    prompt = (
        "You are the intent routing classification system for Swarm OS CLI. "
        "Classify the following natural language user input into one of these commands:\n"
        "- \"/diff\" (if they want to see git changes, modifications, diff)\n"
        "- \"/status\" (if they want to check health, status, system health)\n"
        "- \"/commit\" (if they want to save changes to git or commit)\n"
        "- \"/heal run\" (if they want to run a healing/repair cycle)\n"
        "- \"/clear\" (if they want to clear history or reset the context)\n"
        "- \"/exit\" (if they want to quit or close the terminal)\n"
        "- \"/tokens\" (if they want to check token count or session cost)\n"
        "- \"/benchmark\" (if they want to run model latency benchmarks)\n"
        "- \"/debate <goal>\" (if they want to discuss or debate a development plan/objective)\n"
        "- \"/goal <goal>\" (if they want to execute an instruction, write code, fix something, add a feature, refactor, run tests, or debug)\n"
        "- \"/chat\" (if it's a general question, discussion, explanation request, or just talking to you)\n\n"
        f"Input: \"{raw}\"\n\n"
        "Return a JSON object with keys:\n"
        "- \"command\": the selected slash command (e.g. \"/diff\", \"/goal fix the routes\", \"/chat\")\n"
        "- \"confidence\": float between 0.0 and 1.0\n\n"
        "Return ONLY the valid JSON, no explanations before or after."
    )

    try:
        r = ctx.call_api("/generate", "POST", {"model": model, "prompt": prompt})
        if r and r.status_code == 200:
            data = r.json()
            response_text = data.get("response", "").strip()
            if "```" in response_text:
                m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", response_text, re.DOTALL)
                if m:
                    response_text = m.group(1)
            parsed = json.loads(response_text)
            command_str = parsed.get("command", "/chat").strip()
            confidence = float(parsed.get("confidence", 0.0))

            if confidence >= 0.7 and command_str.startswith("/"):
                parts = command_str.split()
                cmd = parts[0][1:].lower()
                args = parts[1:]

                if cmd in ("goal", "debate"):
                    args = [raw.strip()]

                return cmd, args
    except Exception:
        pass

    return None, []

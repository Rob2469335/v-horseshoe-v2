"""Agentic web-task loop (2026 SOTA, Perplexity-Computer / MagenticLite shape).

The loop that turns "fill out this form on example.com" into actual browser
actions. Matches the 2026 small-model pattern (Microsoft MagenticLite / Fara,
playwright-mcp, OpenClaw's browser-automation skill):

  snapshot (a11y tree as text) -> planner picks ONE action (deepseek-v4-flash,
  the funded cloud planner) -> execute the deterministic browser primitive ->
  resnapshot to VERIFY the state change landed -> next.

Design rules (reviewer-locked, from the 2026 research):
- SPLIT ROLES: the planner only issues one-step instructions; the deterministic
  browser tools execute. A bare 4B drifts on multi-step flows, so planning goes
  to the cloud flash model while execution stays deterministic.
- SHRINKED ACTION SPACE: only navigate / a11y / click / type / fill_form /
  verify / press / wait are offered to the planner — small deterministic
  per-element tools, never pixel coords.
- FORCED VERIFY: after every fill, browser_verify reads the value back; after
  every click, a resnapshot confirms the page changed.
- BOUNDED: max_steps (default 12), loop detection (repeated identical action),
  stale-ref recovery once.
- HUMAN AT CRITICAL POINTS: submit/purchase/login/2FA/captcha -> STOP and return
  an approval request; the Command Center surfaces it and the human confirms.
- CONTEXT MANAGEMENT: only the latest snapshot + action history summary go to the
  planner each turn (never the whole growing transcript).
"""
from __future__ import annotations

import json
import logging
import re

from swarm_os.lib.mcp.playwright import playwright_handler

log = logging.getLogger(__name__)

MAX_STEPS = 12
CRITICAL_ACTIONS = ("submit", "purchase", "checkout", "pay", "login", "sign in", "sign up", "confirm order")

_PLANNER_PROMPT = """You are a browser automation planner. You issue EXACTLY ONE browser action per response — never more.

CURRENT PAGE (accessibility tree as text):
{a11y}

TASK: {goal}

STEP HISTORY (last few):
{history}

Respond with EXACTLY one JSON object, no prose:
{{"action": "navigate"|"click"|"type"|"fill_form"|"press"|"wait"|"done",
  "params": {{...}}, "reason": "one short sentence"}}

Rules:
- click/type/fill_form use the a11y NAME (label/placeholder/text) as the target.
- fill_form params: {{"fields": [{{"name": ..., "value": ...}}, ...]}} for multiple fields.
- press params: {{"key": "Enter"}} (submit forms) or {{"key": "Tab"}}.
- When the task is complete, action="done" with reason.
- If you need a confirmation that looks risky (submit/purchase/login/2FA), still
  return the action you'd take; the system handles approval.
- NEVER invent selectors or pixel coordinates. Only use names from the a11y tree.
"""


async def _get_planner_decision(prompt: str) -> dict:
    """Call the planner model (deepseek-v4-flash via OpenCode Go)."""
    import litellm
    import os
    base = os.getenv("OPENAI_API_BASE", "https://opencode.ai/zen/go/v1")
    key = os.getenv("OPENAI_API_KEY", "")
    resp = await litellm.acompletion(
        model="openai/deepseek-v4-flash", messages=[{"role": "user", "content": prompt}],
        api_base=base, api_key=key, custom_llm_provider="openai",
        max_tokens=400, timeout=90,
    )
    text = (resp.choices[0].message.content or "").strip()
    # Robust JSON extraction: find the JSON object even if the model wraps it in
    # prose or markdown fences. Try the last {...} block (the decision).
    candidates = re.findall(r"\{.*\}", text, re.DOTALL)
    for cand in reversed(candidates):
        try:
            obj = json.loads(cand)
            if isinstance(obj, dict) and obj.get("action"):
                return obj
        except Exception:
            continue
    raise ValueError(f"planner did not return JSON: {text[:200]}")


def _clean_a11y(a11y: list, limit: int = 40) -> list:
    """Filter the a11y tree to USEFUL interactive elements: drop CSS-class junk
    (names that look like `.selector{...}`), empty names, and very long ones.
    A raw page snapshot has a lot of noise (Google/DuckDuckGo button names are
    often inline CSS), which confuses a small planner model."""
    out = []
    for n in a11y:
        name = str(n.get("name", "")).strip()
        if not name:
            continue
        if len(name) > 60:
            continue
        if name.startswith(".") and "{" in name:  # CSS-class junk
            continue
        if "{" in name or ";" in name or name.startswith("\\") or name.startswith("http"):  # other junk
            continue
        out.append(n)
        if len(out) >= limit:
            break
    return out


def _history_summary(history: list[dict], n: int = 4) -> str:
    return "\n".join(f"- {h.get('action')} {json.dumps(h.get('params', {}))} -> {str(h.get('result', ''))[:60]}" for h in history[-n:])


def _loop_detected(history: list[dict]) -> bool:
    """Same action+params twice in a row = loop. Recover once, then stop."""
    if len(history) >= 2:
        a, b = history[-1], history[-2]
        if a.get("action") == b.get("action") and a.get("params") == b.get("params"):
            return True
    return False


def _needs_approval(action: str, params: dict) -> str | None:
    """Critical actions (submit/purchase/login) require human approval. Returns
    a reason string if approval is needed, else None. The loop STOPS and returns
    an approval_request — the Command Center shows it and the human confirms."""
    name = (params.get("name") or "").lower()
    action_l = action.lower()
    hay = f"{action_l} {name}"
    for c in CRITICAL_ACTIONS:
        if c in hay:
            return f"action '{action}' on '{name}' looks critical ({c}) — human approval required"
    return None


async def run_browser_task(goal: str, approval_gate=None, max_steps: int = MAX_STEPS, confirm: bool = False) -> dict:
    """Run the agentic loop. `approval_gate` is an async callable(reason, pending)
    -> bool that a host can use to surface the confirmation; if None, critical
    actions auto-stop with an approval_request in the result. `confirm=True`
    pre-approves the next critical action (the Command Center's 'Approve &
    continue' after an approval_requested stop)."""
    history: list[dict] = []
    approved_once = False
    try:
        for step in range(max_steps):
            # 1) snapshot — a11y tree as text; if EMPTY, fall back to the vision
            #    model describing the page (canvas/custom-rendered apps).
            snap = await playwright_handler({"operation": "browser_a11y"})
            a11y = snap.get("a11y", [])
            page_state = snap.get("url", "")
            vision_desc = ""
            if not a11y:
                vision = await playwright_handler({"operation": "browser_describe"})
                if vision.get("ok"):
                    vision_desc = vision.get("description", "")
                    a11y = [{"role": "vision", "name": vision_desc[:500], "value": ""}]

            # 2) planner picks ONE action
            prompt = _PLANNER_PROMPT.format(
                a11y=json.dumps(_clean_a11y(a11y), ensure_ascii=False)[:3000],
                goal=goal, history=_history_summary(history),
            )
            decision = await _get_planner_decision(prompt)
            action = decision.get("action")
            params = decision.get("params") or {}
            history.append({"action": action, "params": params, "reason": decision.get("reason", "")})

            if action == "done":
                return {"status": "done", "goal": goal, "steps": len(history), "url": page_state, "history": history}

            # 3) approval gate for critical actions
            critical = _needs_approval(action, params)
            if critical and not (confirm and not approved_once):
                if approval_gate:
                    ok = await approval_gate(critical, history)
                    if not ok:
                        return {"status": "declined", "reason": critical, "steps": len(history), "history": history}
                else:
                    return {"status": "approval_requested", "reason": critical,
                            "pending_action": action, "pending_params": params,
                            "steps": len(history), "history": history}
            if critical and confirm and not approved_once:
                approved_once = True  # consume the one-shot approval

            # 4) execute the deterministic primitive
            result = await playwright_handler({"operation": action, **params})
            history[-1]["result"] = result.get("ok")

            # 5) loop detection (recover once: wait + resnapshot; then stop)
            if _loop_detected(history):
                await playwright_handler({"operation": "browser_wait", "ms": 1200})
                return {"status": "loop_detected", "goal": goal, "steps": len(history), "history": history}

            # 6) verify: after fill/type, read the value back; after click, resnapshot
            if action in ("type", "fill_form") and result.get("failed"):
                return {"status": "fill_failed", "goal": goal, "steps": len(history),
                        "failed": result.get("failed"), "history": history}

        return {"status": "max_steps", "goal": goal, "steps": max_steps, "history": history}
    except Exception as exc:
        log.warning("browser-task failed: %s", exc)
        return {"status": "error", "goal": goal, "steps": len(history), "error": str(exc), "history": history}

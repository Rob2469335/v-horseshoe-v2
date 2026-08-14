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
from pathlib import Path

from swarm_os.lib.mcp.playwright import playwright_handler

log = logging.getLogger(__name__)

MAX_STEPS = 12
CRITICAL_ACTIONS = ("submit", "purchase", "checkout", "pay", "login", "sign in", "sign up", "confirm order")

_PLANNER_PROMPT = """You are a browser automation planner. You issue EXACTLY ONE browser action per response — never more.

CURRENT PAGE (accessibility tree as text):
{a11y}

TASK: {goal}

TASK CHECKLIST (track your progress — [x]=done [>]=current [ ]=pending):
{checklist}

STEP HISTORY (last few):
{history}

Respond with EXACTLY one JSON object, no prose:
{{"evaluation_previous_goal": "did the last action achieve its goal? (achieved/failed/partial)",
  "next_goal": "the concrete outcome this next action targets",
  "checklist_update": "optional: add/check an item, e.g. ['[x] navigate to page', '[ ] fill form']",
  "action": "navigate"|"click"|"type"|"fill_form"|"press"|"wait"|"ask_human"|"done",
  "params": {{...}}, "reason": "one short sentence"}}

Rules:
- ALWAYS evaluate_previous_goal first. If it failed, change approach (never repeat the exact same action).
- click/type/fill_form use the a11y NAME (label/placeholder/text) as the target.
- fill_form params: {{"fields": [{{"name": ..., "value": ...}}, ...]}} for multiple fields.
- press params: {{"key": "Enter"}} (submit forms) or {{"key": "Tab"}}.
- ask_human params: {{"message": "what you need from the human (captcha/2FA/consent/ambiguous choice)"}}.
- When the task is complete, action="done" with reason.
- If you need a confirmation that looks risky (submit/purchase/login/2FA), still
  return the action you'd take; the system handles approval.
- NEVER invent selectors or pixel coordinates. Only use names from the a11y tree.
- CAPTCHAs/2FA/consent walls: do NOT grind. Use ask_human to request the human's help, or redirect.
"""


def _render_checklist(items: list[str]) -> str:
    if not items:
        return "(none yet — the planner builds it)"
    return "\n".join(f"  {i}" for i in items)


async def _get_planner_decision(prompt: str) -> dict:
    """Call the planner model (deepseek-v4-flash via OpenCode Go)."""
    import litellm
    import os
    base = os.getenv("OPENAI_API_BASE", "https://opencode.ai/zen/go/v1")
    key = os.getenv("OPENAI_API_KEY", "")
    resp = await litellm.acompletion(
        model="openai/deepseek-v4-flash", messages=[{"role": "user", "content": prompt}],
        api_base=base, api_key=key, custom_llm_provider="openai",
        # LARGE output budget: deepseek-v4-flash reasons heavily (lives in
        # `reasoning_content`) before emitting the JSON decision in `content`.
        # A 500-token cap ran out mid-reasoning -> empty content -> the planner
        # "did not return JSON" error immediately after the first navigate.
        max_tokens=3000, timeout=90,
        # json_object is the ONLY response_format this OpenCode Go/Zen proxy
        # accepts (verified in _llm_client._cloud_response_format) and
        # guarantees the decision is emitted in `content`, not just reasoning.
        response_format={"type": "json_object"},
    )
    choice = resp.choices[0].message
    text = (choice.content or "").strip()
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
    # LAST-RESORT fallback: if `content` was empty or unparseable, salvage the
    # decision JSON from the reasoning trace (the observed failure mode).
    reasoning = getattr(choice, "reasoning_content", None) or ""
    if reasoning.strip():
        candidates = re.findall(r"\{.*\}", reasoning, re.DOTALL)
        for cand in reversed(candidates):
            try:
                obj = json.loads(cand)
                if isinstance(obj, dict) and obj.get("action"):
                    return obj
            except Exception:
                continue
    raise ValueError(f"planner did not return JSON: {(text or reasoning)[:200]}")


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


def _normalize_action(action: str, params: dict) -> str:
    """Normalize an action for loop detection: token-sorted query values,
    element-name clicks, full-URL navigates. 'Search buy now please' and
    'search buy now' are the SAME action."""
    a = action.lower().strip()
    if a == "type" or a == "fill_form":
        # Sort the tokens of any text/value so re-wording doesn't hide a loop.
        fields = params.get("fields") or ([params] if params.get("value") else [])
        vals = []
        for f in fields:
            v = str(f.get("value", "") or params.get("value", ""))
            vals.append(" ".join(sorted(v.lower().split())))
        return f"{a}:{sorted(vals)}"
    if a == "click":
        return f"click:{str(params.get('name', '')).lower().strip()}"
    if a == "navigate":
        return f"navigate:{str(params.get('url', '')).lower().strip()}"
    return f"{a}:{sorted(str(v).lower() for v in params.values())}"


def _page_fingerprint(url: str, a11y: list) -> str:
    """A hash of the page's current state — url + element count + text hash. If an
    action produced NO fingerprint change, it likely failed (stale ref / nothing
    happened)."""
    text = "".join(f"{n.get('role')}:{n.get('name')}" for n in a11y[:50])
    return f"{url}|{len(a11y)}|{abs(hash(text)) % (10 ** 8)}"


def _loop_detected(history: list[dict], repeats_threshold: int = 3) -> bool:
    """Semantic loop detection: the same NORMALIZED action repeated >= threshold
    times within the recent window = a loop. Not string-based — 'search buy now
    please' vs 'search buy now' are the same action."""
    norm = [_normalize_action(h.get("action", ""), h.get("params") or {}) for h in history]
    if len(norm) < repeats_threshold:
        return False
    for key in set(norm[-repeats_threshold:]):
        if norm[-repeats_threshold:].count(key) >= repeats_threshold:
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


# Per-domain approval memory (2026: 'yes-always-for-this-site' so approvals are
# tolerable, while submit/purchase/login stay gated). Persisted to a gitignored
# JSON so it survives restarts. Read ops are always allowed on an approved domain.
_APPROVED_FILE = Path(__file__).resolve().parent.parent.parent / "data" / "approved_domains.json"


def _load_approved_domains() -> dict:
    try:
        if _APPROVED_FILE.exists():
            return json.loads(_APPROVED_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _save_approved_domains(data: dict) -> None:
    try:
        _APPROVED_FILE.parent.mkdir(parents=True, exist_ok=True)
        _APPROVED_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception as exc:
        log.warning("approved-domains save failed: %s", exc)


def _domain_of(url: str) -> str:
    import urllib.parse
    try:
        return urllib.parse.urlparse(url or "").netloc.lower()
    except Exception:
        return ""


def is_domain_approved(url: str) -> bool:
    """A domain is 'session-trusted' if previously approved (fill/read allowed
    without re-prompt). Critical actions still re-gate regardless."""
    d = _domain_of(url)
    return bool(d and _load_approved_domains().get(d))


def approve_domain(url: str, remember: bool = True) -> None:
    d = _domain_of(url)
    if not d:
        return
    import time
    data = _load_approved_domains()
    data[d] = {"approved_at": time.time(), "remember": remember}
    _save_approved_domains(data)


async def run_browser_task(goal: str, approval_gate=None, max_steps: int = MAX_STEPS, confirm: bool = False) -> dict:
    """Run the agentic loop. `approval_gate` is an async callable(reason, pending)
    -> bool that a host can use to surface the confirmation; if None, critical
    actions auto-stop with an approval_request in the result. `confirm=True`
    pre-approves the next critical action (the Command Center's 'Approve &
    continue' after an approval_requested stop).

    2026 upgrades (research-backed): task checklist injected each turn,
    semantic loop detection (normalized hashing + page fingerprint), retry-once
    on a failed action, and an agent-initiated ask_human for captcha/2FA."""
    history: list[dict] = []
    checklist: list[str] = []
    approved_once = False
    prev_fingerprint = None
    retried = False
    try:
        for step in range(max_steps):
            # 1) snapshot — a11y tree as text; if EMPTY, fall back to the vision
            #    model describing the page (canvas/custom-rendered apps).
            snap = await playwright_handler({"operation": "browser_a11y"})
            a11y = snap.get("a11y", [])
            page_state = snap.get("url", "")
            if not a11y:
                vision = await playwright_handler({"operation": "browser_describe"})
                if vision.get("ok"):
                    a11y = [{"role": "vision", "name": (vision.get("description", ""))[:500], "value": ""}]

            # 2) planner picks ONE action (with the task checklist + history)
            prompt = _PLANNER_PROMPT.format(
                a11y=json.dumps(_clean_a11y(a11y), ensure_ascii=False)[:3000],
                goal=goal, checklist=_render_checklist(checklist), history=_history_summary(history),
            )
            decision = await _get_planner_decision(prompt)
            action = decision.get("action")
            params = decision.get("params") or {}
            history.append({"action": action, "params": params,
                            "reason": decision.get("reason", ""),
                            "evaluation": decision.get("evaluation_previous_goal", ""),
                            "next_goal": decision.get("next_goal", "")})

            # apply any checklist update the planner requested
            cu = decision.get("checklist_update")
            if isinstance(cu, list):
                for item in cu:
                    item = str(item)
                    if item.startswith("[x]") and not any(item[3:].strip() in c for c in checklist):
                        checklist.append(item)
                    elif item.startswith("[ ]"):
                        base = item[3:].strip()
                        if not any(base in c for c in checklist):
                            checklist.append(item)
                        else:
                            checklist = [c if not c.endswith(base) else item for c in checklist]

            if action == "done":
                return {"status": "done", "goal": goal, "steps": len(history), "url": page_state, "history": history}

            # 3) ask_human — agent-initiated help request (captcha/2FA/consent)
            if action == "ask_human":
                message = params.get("message", "agent needs help")
                return {"status": "ask_human", "message": message, "steps": len(history), "history": history}

            # 4) approval gate for critical actions (submit/purchase/login always
            #    gate; an approved-domain note is surfaced in the result)
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

            # 5) execute the deterministic primitive
            result = await playwright_handler({"operation": action, **params})
            history[-1]["result"] = result.get("ok")

            # 5b) retry-once: a FAILED action gets one resnapshot+retry, then nudge.
            if not result.get("ok") and not retried:
                retried = True
                await playwright_handler({"operation": "browser_wait", "ms": 800})
                a11y2 = (await playwright_handler({"operation": "browser_a11y"})).get("a11y", [])
                if a11y2 != a11y:  # page changed — stale ref; retry once
                    result = await playwright_handler({"operation": action, **params})
                    history[-1]["result"] = result.get("ok")

            # 6) semantic loop detection + page-stagnation
            fingerprint = _page_fingerprint(page_state, a11y)
            if _loop_detected(history):
                await playwright_handler({"operation": "browser_wait", "ms": 1200})
                return {"status": "loop_detected", "goal": goal, "steps": len(history), "history": history}
            if prev_fingerprint == fingerprint and len(history) >= 3:
                # page didn't change for 2+ actions — stagnant (likely a failed click)
                await playwright_handler({"operation": "browser_wait", "ms": 1200})
                return {"status": "stagnant", "goal": goal, "steps": len(history), "history": history}
            prev_fingerprint = fingerprint

            # 7) verify: after fill/type, read the value back; after click, resnapshot
            if action in ("type", "fill_form") and result.get("failed"):
                return {"status": "fill_failed", "goal": goal, "steps": len(history),
                        "failed": result.get("failed"), "history": history}

        return {"status": "max_steps", "goal": goal, "steps": max_steps, "history": history}
    except Exception as exc:
        log.warning("browser-task failed: %s", exc)
        return {"status": "error", "goal": goal, "steps": len(history), "error": str(exc), "history": history}

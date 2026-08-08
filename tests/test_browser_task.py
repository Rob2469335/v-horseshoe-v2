
"""Tests for the agentic web-task loop (2026 SOTA browser automation).

The loop must: snapshot -> planner-one-action -> execute -> verify, stop for
approval on critical actions (submit/purchase/login), and honor a one-shot
confirm. These run the loop with a mocked playwright handler + planner so the
loop logic is testable without a live browser or network.
"""
import asyncio


from swarm_os.services import browser_task as bt


def _install_mocks(monkeypatch, plan):
    """Plan is a list of planner decisions. A11y returns a fixed tree."""
    calls = []
    async def fake_handler(payload):
        calls.append(payload.get("operation"))
        op = payload.get("operation")
        if op == "browser_a11y":
            return {"ok": True, "a11y": [{"role": "textbox", "name": "q", "value": ""},
                                          {"role": "button", "name": "Search", "value": ""}]}
        return {"ok": True, "url": "http://x"}
    monkeypatch.setattr(bt, "playwright_handler", fake_handler)
    plan = list(plan)
    async def fake_planner(prompt):
        return plan.pop(0)
    monkeypatch.setattr(bt, "_get_planner_decision", fake_planner)
    return calls


def test_loop_reaches_done_with_verify_cadence(monkeypatch):
    """snapshot -> type -> resnapshot -> press -> resnapshot -> done."""
    calls = _install_mocks(monkeypatch, [
        {"action": "type", "params": {"name": "q", "value": "hello"}, "reason": "fill"},
        {"action": "press", "params": {"key": "Enter"}, "reason": "submit"},
        {"action": "done", "params": {}, "reason": "complete"},
    ])
    r = asyncio.run(bt.run_browser_task("search for hello"))
    assert r.get("status") == "done"
    assert r.get("steps") == 3
    # verify cadence: a11y before each action
    assert calls[0] == "browser_a11y"
    assert calls[2] == "browser_a11y"
    assert calls[4] == "browser_a11y"


def test_loop_stops_for_approval_on_critical_action(monkeypatch):
    """A click on 'Submit Order' must return approval_requested, not execute."""
    calls = _install_mocks(monkeypatch, [
        {"action": "click", "params": {"name": "Submit Order"}, "reason": "place order"},
    ])
    r = asyncio.run(bt.run_browser_task("buy the item"))
    assert r.get("status") == "approval_requested"
    assert "submit" in (r.get("reason") or "").lower()
    assert r.get("pending_action") == "click"
    assert "click" not in calls, "critical action must NOT execute without approval"


def test_confirm_executes_critical_action(monkeypatch):
    """confirm=True consumes one approval and executes the critical click."""
    calls = _install_mocks(monkeypatch, [
        {"action": "click", "params": {"name": "Submit Order"}, "reason": "place order"},
        {"action": "done", "params": {}, "reason": "complete"},
    ])
    r = asyncio.run(bt.run_browser_task("buy the item", confirm=True))
    assert r.get("status") == "done"
    assert "click" in calls, "confirmed critical action must execute"


def test_loop_detection_stops(monkeypatch):
    """Repeated identical action+params must stop the loop."""
    _install_mocks(monkeypatch, [
        {"action": "click", "params": {"name": "Search"}, "reason": "try"},
        {"action": "click", "params": {"name": "Search"}, "reason": "try again"},
    ])
    r = asyncio.run(bt.run_browser_task("do the thing"))
    assert r.get("status") == "loop_detected"


def test_fill_failed_reported(monkeypatch):
    """A failed fill must surface as fill_failed with the failed fields."""
    async def fake_handler(payload):
        op = payload.get("operation")
        if op == "browser_a11y":
            return {"ok": True, "a11y": [{"role": "textbox", "name": "q", "value": ""}]}
        if op == "fill_form":
            return {"ok": False, "failed": [{"name": "q", "error": "value mismatch"}]}
        return {"ok": True}
    monkeypatch.setattr(bt, "playwright_handler", fake_handler)
    plan = iter([{"action": "fill_form", "params": {"fields": [{"name": "q", "value": "x"}]}, "reason": "fill"}])
    async def fake_planner(prompt):
        return next(plan)
    monkeypatch.setattr(bt, "_get_planner_decision", fake_planner)
    r = asyncio.run(bt.run_browser_task("fill the form"))
    assert r.get("status") == "fill_failed"
    assert r.get("failed")

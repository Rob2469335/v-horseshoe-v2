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
            return {
                "ok": True,
                "a11y": [
                    {"role": "textbox", "name": "q", "value": ""},
                    {"role": "button", "name": "Search", "value": ""},
                ],
            }
        return {"ok": True, "url": "http://x"}

    monkeypatch.setattr(bt, "playwright_handler", fake_handler)
    plan = list(plan)

    async def fake_planner(prompt):
        return plan.pop(0)

    monkeypatch.setattr(bt, "_get_planner_decision", fake_planner)
    return calls


def test_loop_reaches_done_with_verify_cadence(monkeypatch):
    """snapshot -> type -> resnapshot -> press -> resnapshot -> done."""
    calls = _install_mocks(
        monkeypatch,
        [
            {
                "action": "type",
                "params": {"name": "q", "value": "hello"},
                "reason": "fill",
            },
            {"action": "press", "params": {"key": "Enter"}, "reason": "submit"},
            {"action": "done", "params": {}, "reason": "complete"},
        ],
    )
    r = asyncio.run(bt.run_browser_task("search for hello"))
    assert r.get("status") == "done"
    assert r.get("steps") == 3
    # verify cadence: a11y before each action
    assert calls[0] == "browser_a11y"
    assert calls[2] == "browser_a11y"
    assert calls[4] == "browser_a11y"


def test_loop_stops_for_approval_on_critical_action(monkeypatch):
    """A click on 'Submit Order' must return approval_requested, not execute."""
    calls = _install_mocks(
        monkeypatch,
        [
            {
                "action": "click",
                "params": {"name": "Submit Order"},
                "reason": "place order",
            },
        ],
    )
    r = asyncio.run(bt.run_browser_task("buy the item"))
    assert r.get("status") == "approval_requested"
    assert "submit" in (r.get("reason") or "").lower()
    assert r.get("pending_action") == "click"
    assert "click" not in calls, "critical action must NOT execute without approval"


def test_confirm_executes_critical_action(monkeypatch):
    """A single-use approval_token (minted by the prior approval_requested
    stop) consumes one approval and executes exactly that critical click.
    Regression (2026-08-23): the old wire-level `confirm: true` boolean let
    any caller self-grant; tokens are opaque, TTL'd, and bound to the
    pending action+params."""
    calls = _install_mocks(
        monkeypatch,
        [
            {"action": "click", "params": {"name": "Submit Order"}, "reason": "r1"},
            {"action": "click", "params": {"name": "Submit Order"}, "reason": "r2"},
            {"action": "done", "params": {}, "reason": "d1"},
            {"action": "done", "params": {}, "reason": "d2"},
        ],
    )
    first = asyncio.run(bt.run_browser_task("buy the item"))
    assert first.get("status") == "approval_requested"
    token = first.get("approval_token")
    assert token, "approval_requested must mint a single-use token"

    r = asyncio.run(bt.run_browser_task("buy the item", approval_token=token))
    assert r.get("status") == "done"
    assert "click" in calls, "token-approved critical action must execute"


def test_approval_token_is_single_use_and_bound(monkeypatch):
    """The same token cannot approve a DIFFERENT critical action, and once
    consumed it cannot be replayed for the original one either."""
    _install_mocks(
        monkeypatch,
        [
            {"action": "click", "params": {"name": "Submit Order"}, "reason": "r1"},
            {"action": "click", "params": {"name": "Pay Now"}, "reason": "r2"},
            {"action": "click", "params": {"name": "Submit Order"}, "reason": "r3"},
            {"action": "done", "params": {}, "reason": "d1"},
            {"action": "done", "params": {}, "reason": "d2"},
        ],
    )
    first = asyncio.run(bt.run_browser_task("buy"))
    assert first.get("status") == "approval_requested"
    token = first["approval_token"]

    # Token bound to click@'Submit Order'; presenting it against Pay Now
    # fails the binding -> declined, and Pay Now must NOT execute.
    second = asyncio.run(bt.run_browser_task("buy", approval_token=token))
    assert second.get("status") == "declined"
    assert "invalid or expired" in (second.get("reason") or "")

    # Replay of the consumed token against the ORIGINAL action -> refused
    # (single-use), and the click must not execute.
    third = asyncio.run(bt.run_browser_task("buy", approval_token=token))
    assert third.get("status") in ("declined", "approval_requested")
    assert not any(
        h.get("action") == "click" and h.get("result") for h in third.get("history", [])
    )


def test_failed_critical_action_is_not_auto_retried(monkeypatch):
    """Audit B8: a failed CRITICAL action must NOT auto-retry (first attempt
    may have landed server-side - double-submit hazard)."""
    click_payloads = []

    async def failing_handler(payload):
        op = payload.get("operation")
        if op == "click":
            click_payloads.append(payload)
            return {"ok": False, "error": "timeout after submit"}
        if op == "browser_a11y":
            return {
                "ok": True,
                "a11y": [{"role": "button", "name": "Submit Order", "value": ""}],
            }
        return {"ok": True, "url": "http://x"}

    plan = [
        {"action": "click", "params": {"name": "Submit Order"}, "reason": "go"},
        {"action": "click", "params": {"name": "Submit Order"}, "reason": "go2"},
        {"action": "done", "params": {}, "reason": "d"},
    ]

    async def fake_planner(prompt):
        return plan.pop(0)

    monkeypatch.setattr(bt, "playwright_handler", failing_handler)
    monkeypatch.setattr(bt, "_get_planner_decision", fake_planner)

    first = asyncio.run(bt.run_browser_task("buy"))
    assert first.get("status") == "approval_requested"
    _ = asyncio.run(
        bt.run_browser_task(
            "buy",
            approval_token=first["approval_token"],
        )
    )
    assert len(click_payloads) == 1, (
        f"critical click executed {len(click_payloads)}x - auto-retry fired"
    )


def test_loop_detection_stops(monkeypatch):
    """Repeated identical action 3x (semantic threshold) must stop the loop."""
    _install_mocks(
        monkeypatch,
        [
            {"action": "click", "params": {"name": "Search"}, "reason": "try"},
            {"action": "click", "params": {"name": "Search"}, "reason": "try again"},
            {
                "action": "click",
                "params": {"name": "Search"},
                "reason": "try once more",
            },
        ],
    )
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
    plan = iter(
        [
            {
                "action": "fill_form",
                "params": {"fields": [{"name": "q", "value": "x"}]},
                "reason": "fill",
            }
        ]
    )

    async def fake_planner(prompt):
        return next(plan)

    monkeypatch.setattr(bt, "_get_planner_decision", fake_planner)
    r = asyncio.run(bt.run_browser_task("fill the form"))
    assert r.get("status") == "fill_failed"
    assert r.get("failed")


def test_ask_human_returns_message(monkeypatch):
    """The agent can initiate a help request (captcha/2FA/consent) mid-flow."""
    _install_mocks(
        monkeypatch,
        [
            {
                "action": "ask_human",
                "params": {"message": "captcha wall encountered"},
                "reason": "need help",
            },
        ],
    )
    r = asyncio.run(bt.run_browser_task("log in"))
    assert r.get("status") == "ask_human"
    assert "captcha" in r.get("message", "")


def test_semantic_loop_detection_reworded_action(monkeypatch):
    """Reworded type actions must be detected as a loop (token-sorted normalize)."""
    _install_mocks(
        monkeypatch,
        [
            {
                "action": "type",
                "params": {"name": "q", "value": "buy now please"},
                "reason": "a",
            },
            {
                "action": "type",
                "params": {"name": "q", "value": "please buy now"},
                "reason": "b",
            },
            {
                "action": "type",
                "params": {"name": "q", "value": "now buy please"},
                "reason": "c",
            },
        ],
    )
    r = asyncio.run(bt.run_browser_task("search buy now"))
    assert r.get("status") == "loop_detected"


def test_loop_tracks_checklist(monkeypatch):
    """The planner's checklist_update is applied and reflected in history."""
    _install_mocks(
        monkeypatch,
        [
            {
                "action": "click",
                "params": {"name": "Search"},
                "reason": "go",
                "checklist_update": ["[x] open search", "[ ] fill form"],
            },
            {
                "action": "done",
                "params": {},
                "reason": "done",
                "checklist_update": ["[x] fill form"],
            },
        ],
    )
    r = asyncio.run(bt.run_browser_task("search"))
    assert r.get("status") == "done"
    # checklist items tracked internally (not in result), but history preserved
    assert any(h.get("action") == "click" for h in r.get("history", []))


def test_approve_domain_roundtrip(monkeypatch, tmp_path):
    """Approve a domain, confirm is_domain_approved persists."""
    monkeypatch.setattr(bt, "_APPROVED_FILE", tmp_path / "approved_domains.json")
    assert bt.is_domain_approved("https://example.com/form") is False
    bt.approve_domain("https://example.com/form", remember=True)
    assert bt.is_domain_approved("https://example.com/other") is True
    assert bt.is_domain_approved("https://evil.org") is False


def test_fill_form_validity_gate_reports_incomplete(monkeypatch):
    """browser_fill_form's checkValidity gate surfaces incomplete required fields
    instead of claiming success."""
    from swarm_os.lib.mcp import playwright as pw

    class _FakeLocator:
        async def fill(self, value, timeout=0):
            pass

        async def input_value(self):
            return "x"

        async def count(self):
            return 1

    class _FakePage:
        async def evaluate(self, js):
            return [{"field": "email", "reason": "required and empty"}]

    async def fake_find(page, role, name):
        return _FakeLocator()

    monkeypatch.setattr(pw, "_find_element", fake_find)

    class _FakeCtx:
        pages = [_FakePage()]

    async def fake_ensure():
        return None

    monkeypatch.setattr(pw, "_ensure_browser", fake_ensure)
    monkeypatch.setattr(pw, "_context", _FakeCtx())

    import asyncio as _asyncio

    r = _asyncio.run(
        pw.playwright_handler(
            {
                "operation": "browser_fill_form",
                "fields": [{"name": "email", "value": "x"}],
            }
        )
    )
    # The form has a required empty field -> ok:False with incomplete reported.
    assert r.get("ok") is False
    assert "incomplete" in r or "error" in r


def _fake_completion_response(content: str, reasoning: str = ""):
    """A minimal litellm-compatible acompletion response."""
    from types import SimpleNamespace

    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content=content,
                    reasoning_content=reasoning,
                    finish_reason=None,
                    model=None,
                )
            )
        ]
    )


def test_planner_salvages_decision_from_reasoning_content(monkeypatch):
    """deepseek-v4-flash reasons inside `reasoning_content` before emitting the
    JSON decision in `content`. With a small output budget (the pre-fix
    max_tokens=500), the model ran out of tokens mid-reasoning and returned
    content="" -> the planner raised "did not return JSON" and the web-task
    failed right after the first navigate. The planner must (1) request a large
    budget + json_object so the decision actually lands in content, and (2) as a
    last resort salvage the JSON decision from reasoning_content."""
    import litellm

    calls = {}

    async def fake_acompletion(**kwargs):
        calls["kwargs"] = kwargs
        return _fake_completion_response(
            content="",
            reasoning=(
                "Let me think about this page... I should click the Search button. "
                'The decision is {"action": "click", "params": {"name": "Search"}, '
                '"reason": "submit the query"}'
            ),
        )

    monkeypatch.setattr(litellm, "acompletion", fake_acompletion)

    decision = asyncio.run(bt._get_planner_decision("a prompt"))
    assert decision["action"] == "click"
    assert decision["params"] == {"name": "Search"}
    kw = calls["kwargs"]
    assert kw["max_tokens"] >= 3000, (
        "small max_tokens truncates deepseek-v4-flash mid-reasoning and "
        f"returns empty content (got {kw['max_tokens']})"
    )
    assert kw["response_format"] == {"type": "json_object"}, (
        "the OpenCode Go/Zen proxy rejects json_schema and only json_object "
        "guarantees the decision is emitted in content"
    )


def test_planner_parses_content_when_present(monkeypatch):
    """The normal path: the decision arrives in `content` and must parse."""
    import litellm

    async def fake_acompletion(**kwargs):
        return _fake_completion_response(
            content=(
                '{"action": "navigate", "params": {"url": "https://chess.com"}, '
                '"reason": "start the task"}'
            ),
        )

    monkeypatch.setattr(litellm, "acompletion", fake_acompletion)
    decision = asyncio.run(bt._get_planner_decision("a prompt"))
    assert decision["action"] == "navigate"
    assert decision["params"] == {"url": "https://chess.com"}


def test_fill_form_with_critical_field_names_gates(monkeypatch):
    """Audit B2: fill_form carries targets under params['fields'], so the old
    name-only keyword scan produced hay == 'fill_form ' for EVERY form fill -
    a form with password/card-number fields sailed through ungated."""
    calls = _install_mocks(
        monkeypatch,
        [
            {
                "action": "fill_form",
                "params": {"fields": {"card number": "4111", "cvv": "123"}},
                "reason": "checkout",
            },
        ],
    )
    r = asyncio.run(bt.run_browser_task("check out"))
    assert r.get("status") == "approval_requested"
    assert "credential/payment field" in (r.get("reason") or "")
    assert "fill_form" not in calls

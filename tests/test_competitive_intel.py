# tests/test_competitive_intel.py
"""Tests for the Competitive Intelligence Monitor service.

The detector must be fully deterministic (no LLM decides whether a change
happened). The "so what" interpreter is the only LLM seam, behind
IntelligenceSynthesizer with a deterministic fallback, so the whole subsystem
works with zero external calls.
"""

import asyncio

import pytest

import swarm_os.services.competitive_intel as ci


@pytest.fixture(autouse=True)
def intel_tmpdir(tmp_path, monkeypatch):
    """Point the service's data dir at a temp dir so tests never touch data/intel."""
    monkeypatch.setenv("SWARM_INTEL_DATA_DIR", str(tmp_path / "intel"))
    yield tmp_path


def _comp(name="Acme Corp", url="https://acme.example.com", tier="top_3", targets=None):
    return {
        "id": name.lower().replace(" ", "")[:12],
        "name": name,
        "url": url,
        "tier": tier,
        "targets": targets or ["homepage", "pricing"],
        "enabled": True,
    }


# ---------------------------------------------------------------------------
# Persistence: appends must preserve history (no tmp+replace truncation)
# ---------------------------------------------------------------------------
def test_append_changes_preserves_history_across_batches():
    """Two appends must BOTH survive. The pre-fix tmp+os.replace pattern opened
    a fresh .tmp in append mode then replaced the target — so the second append
    silently wiped the first batch from the change trail."""
    ci._append_changes([{"id": "e1", "dedup_key": "k1"}])
    ci._append_changes([{"id": "e2", "dedup_key": "k2"}])
    ids = [ev["id"] for ev in ci._load_changes(limit=500)]
    assert ids == ["e1", "e2"]


def test_append_delivery_preserves_history_across_records():
    """Failure/retry records must accumulate, never replace each other."""
    ci._append_delivery({"digest_id": "d1", "channel": "email", "ok": False})
    ci._append_delivery({"digest_id": "d1", "channel": "telegram", "ok": True})
    recs = ci._load_deliveries(limit=100)
    assert [(r["channel"], r["ok"]) for r in recs] == [
        ("email", False),
        ("telegram", True),
    ]


def test_save_snapshot_uses_unique_tmp_names(monkeypatch):
    """Two snapshot writes must NOT share a temp file path. Pre-fix both used the
    static `.tmp` suffix; concurrent writes to the same path could interleave and
    os.replace a corrupt snapshot (which _load_snapshot then misreads as
    baseline, silently re-establishing it)."""
    from pathlib import Path

    tmp_names = []
    orig_write_text = Path.write_text

    def guarded_write_text(self, *args, **kwargs):
        if ".tmp" in self.name:
            tmp_names.append(self.name)
        return orig_write_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", guarded_write_text)
    ci._save_snapshot("c1", "homepage", {"content": "a"})
    ci._save_snapshot("c1", "homepage", {"content": "b"})
    assert len(tmp_names) == 2
    assert len(set(tmp_names)) == 2  # distinct temp paths per write
    # target always ends up as a clean, loadable snapshot
    snap = ci._load_snapshot("c1", "homepage")
    assert snap["content"] in ("a", "b")


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------
def test_add_competitor_roundtrip():
    res = ci.add_competitor("Acme Corp", "https://acme.example.com", "top_3")
    assert res["ok"] is True
    cid = res["competitor"]["id"]
    reg = ci.list_competitors()
    assert len(reg) == 1
    assert reg[0]["name"] == "Acme Corp"
    assert reg[0]["tier"] == "top_3"
    ci.remove_competitor(cid)


def test_add_competitor_duplicate_refused():
    ci.add_competitor("Acme Corp", "https://acme.example.com", "top_3")
    res = ci.add_competitor("Acme Corp", "https://acme.example.com", "tier_2")
    assert res["ok"] is False
    ci.remove_competitor(ci.list_competitors()[0]["id"])


def test_add_competitor_validation():
    assert ci.add_competitor("", "https://x.com")["ok"] is False
    assert ci.add_competitor("X", "")["ok"] is False
    assert ci.add_competitor("X", "not-a-url")["ok"] is False
    assert ci.add_competitor("X", "https://x.com", tier="bogus")["ok"] is False


def test_update_competitor_tier_and_enabled():
    c = ci.add_competitor("Acme Corp", "https://acme.example.com", "tier_2")[
        "competitor"
    ]
    r = ci.update_competitor(c["id"], tier="top_3", enabled=False)
    assert r["ok"] is True
    assert r["competitor"]["tier"] == "top_3"
    assert r["competitor"]["enabled"] is False
    ci.remove_competitor(c["id"])


def test_remove_competitor():
    c = ci.add_competitor("Acme Corp", "https://acme.example.com")["competitor"]
    assert ci.remove_competitor(c["id"])["ok"] is True
    assert ci.list_competitors() == []


# ---------------------------------------------------------------------------
# Deterministic detection (no LLM)
# ---------------------------------------------------------------------------
def test_meaningful_delta_true_on_content_change():
    assert ci._meaningful_delta(
        "we sell widgets for everyone",
        "we sell widgets and now also gadgets",
        "homepage",
    )


def test_meaningful_delta_false_on_identical():
    assert not ci._meaningful_delta("we sell widgets", "we sell widgets", "homepage")


def test_meaningful_delta_ignores_noise_churn():
    # Cookie/nav churn should NOT register as a change.
    old = "We sell widgets.\nAccept all cookies\nSign in"
    new = "We sell widgets.\nManage cookies\nSign in\nUnsubscribe from marketing"
    assert not ci._meaningful_delta(old, new, "homepage")


def test_meaningful_delta_counts_real_content_change_beside_noise():
    # A real content addition PLUS noise still counts as a change.
    old = "We sell widgets.\nAccept all cookies"
    new = "We sell widgets and a new gadget.\nManage cookies"
    assert ci._meaningful_delta(old, new, "homepage")


def test_meaningful_delta_ignores_counter_normalization():
    old = "Trusted by 1,234 customers"
    new = "Trusted by 1,567 customers"
    assert not ci._meaningful_delta(old, new, "homepage")


def test_normalize_text_lowercases_and_collapses():
    assert ci._normalize_text("  Hello   World  ") == "hello world"


def test_classify_pricing_change():
    cls, score = ci._classify_change("now only $99 per month billed annually")
    assert cls == "pricing"
    assert score >= 3.0


def test_classify_feature_change():
    cls, _ = ci._classify_change("we launched version 4 with a new AI feature")
    assert cls == "product_feature"


def test_significance_tier_2_downgrades_content():
    # tier_2 only counts major changes: marketing content gets halved.
    s = ci._score_significance("homepage", "marketing_content", "tier_2", set())
    assert s < ci._score_significance("homepage", "marketing_content", "top_3", set())


def test_significance_capped_between_1_and_5():
    s = ci._score_significance("pricing", "pricing", "top_3", set())
    assert 1 <= s <= 5


# ---------------------------------------------------------------------------
# Scan (deterministic fetch seam mocked; snapshot + diff + event shape)
# ---------------------------------------------------------------------------
def test_scan_target_first_scan_no_change_event(monkeypatch):
    """First fetch stores a snapshot but produces no change event (baseline)."""
    monkeypatch.setattr(
        ci,
        "_fetch_target",
        lambda url: _fake_fetch(url, "initial content here"),
    )

    async def run():
        res = await ci.scan_target(_comp(), "homepage")
        return res

    res = asyncio.run(run())
    assert res["ok"] is True
    assert res["changed"] is False


def test_scan_target_detects_change(monkeypatch):
    content = {"v": "initial content"}

    def fake_fetch(url):
        return (True, content["v"], "")

    monkeypatch.setattr(ci, "_fetch_target", lambda url: _sync_fetch(fake_fetch(url)))

    async def run():
        # first scan = baseline
        await ci.scan_target(_comp(), "homepage")
        content["v"] = "initial content and a brand new product feature"
        res = await ci.scan_target(_comp(), "homepage")
        return res

    res = asyncio.run(run())
    assert res["changed"] is True
    ev = res["event"]
    assert ev["competitor"] == "Acme Corp"
    assert ev["kind"] == "homepage"
    assert ev["classification"] in ("product_feature", "unknown")
    assert ev["significance"] >= 1.0
    assert ev["dedup_key"]


def test_scan_target_same_content_twice_no_event(monkeypatch):
    content = {"v": "stable content"}

    def fake_fetch(url):
        return (True, content["v"], "")

    monkeypatch.setattr(ci, "_fetch_target", lambda url: _sync_fetch(fake_fetch(url)))

    async def run():
        await ci.scan_target(_comp(), "homepage")
        res = await ci.scan_target(_comp(), "homepage")
        return res

    res = asyncio.run(run())
    assert res["changed"] is False


def test_scan_all_dedupes_and_persists(monkeypatch):
    monkeypatch.setattr(ci, "list_competitors", lambda: [_comp()])

    versions = [
        "content baseline widgets",
        "content baseline widgets and a new gadget",
        "content baseline widgets and a new gadget",
    ]

    def fake_fetch(url):
        if "pricing" in url:
            return (True, "pricing page stable", "")
        n = versions.pop(0) if versions else "content baseline widgets and a new gadget"
        return (True, n, "")

    monkeypatch.setattr(ci, "_fetch_target", lambda url: _sync_fetch(fake_fetch(url)))

    async def run():
        baseline = await ci.scan_all()  # first scan = baseline (no change)
        second = await ci.scan_all()  # content changed -> 1 event
        third = await ci.scan_all()  # same content as second -> no new event
        return baseline, second, third

    baseline, second, third = asyncio.run(run())
    assert baseline["changed"] == 0
    assert second["changed"] == 1
    assert third["changed"] == 0


def test_scan_all_failure_in_one_provider_does_not_kill_fanout(monkeypatch):
    monkeypatch.setattr(
        ci,
        "list_competitors",
        lambda: [_comp(), _comp("Rival", "https://rival.example.com")],
    )

    # rival fetch always fails; acme succeeds
    def fake_fetch(url):
        if "rival" in url:
            return (False, "", "fetch failed")
        return (True, "acme content", "")

    monkeypatch.setattr(ci, "_fetch_target", lambda url: _sync_fetch(fake_fetch(url)))

    async def run():
        return await ci.scan_all()

    res = asyncio.run(run())
    assert res["scanned"] == 2
    assert res["changed"] == 0  # first scan = baseline, no change events yet
    assert not res["errors"]  # rival returned ok:False, not an exception


# ---------------------------------------------------------------------------
# Intelligence layer: dedup + cap + "so what"
# ---------------------------------------------------------------------------
def test_dedupe_caps_at_15():
    events = []
    for i in range(30):
        events.append(
            {
                "id": f"e{i}",
                "competitor_id": "c1",
                "kind": "homepage",
                "dedup_key": f"k{i}",
                "significance": i % 5 + 1,
            }
        )
    out = ci._dedupe_events(events, cap=15)
    assert len(out) == 15
    assert len({e["dedup_key"] for e in out}) == 15


def test_dedupe_removes_duplicate_keys():
    events = [
        {"id": "a", "dedup_key": "same", "significance": 5},
        {"id": "b", "dedup_key": "same", "significance": 3},
        {"id": "c", "dedup_key": "other", "significance": 4},
    ]
    out = ci._dedupe_events(events)
    assert len(out) == 2
    assert {e["id"] for e in out} == {"a", "c"}  # higher significance wins


def test_deterministic_so_what_no_llm():
    ev = {
        "competitor": "Acme",
        "kind": "pricing",
        "classification": "pricing",
        "significance": 4.0,
        "snippet": "now only $99 per month",
    }
    text = ci._so_what_deterministic(ev)
    assert "pricing" in text.lower() or "price" in text.lower()
    assert "acme" in text.lower()


# ---------------------------------------------------------------------------
# Synthesizer chain: remote -> local -> deterministic
# ---------------------------------------------------------------------------
def test_synthesizer_remote_failure_falls_to_deterministic():
    async def bad_remote(p):
        raise RuntimeError("no balance")

    s = ci.IntelligenceSynthesizer(remote_complete=bad_remote, local_complete=None)
    ev = {
        "competitor": "Acme",
        "kind": "pricing",
        "classification": "pricing",
        "significance": 4.0,
        "snippet": "x",
    }
    out = asyncio.run(s.synthesize(ev))
    assert out  # deterministic fallback still produces a "so what"


def test_synthesizer_deterministic_when_nothing_configured():
    s = ci.IntelligenceSynthesizer(remote_complete=None, local_complete=None)
    ev = {
        "competitor": "Acme",
        "kind": "changelog",
        "classification": "product_feature",
        "significance": 3.0,
        "snippet": "new",
    }
    out = asyncio.run(s.synthesize(ev))
    assert "acme" in out.lower()
    assert ci._last_synth_provider() == "deterministic"


def test_synthesizer_records_remote_provider():
    async def good_remote(p):
        return "Remote analysis of the pricing change."

    s = ci.IntelligenceSynthesizer(remote_complete=good_remote, local_complete=None)
    ev = {
        "competitor": "Acme",
        "kind": "pricing",
        "classification": "pricing",
        "significance": 4.0,
        "snippet": "x",
    }
    out = asyncio.run(s.synthesize(ev))
    assert "Remote analysis" in out
    assert ci._last_synth_provider() == "remote"


# ---------------------------------------------------------------------------
# Digest generation
# ---------------------------------------------------------------------------
def test_generate_digest_uses_stored_changes(monkeypatch):
    monkeypatch.setattr(
        ci,
        "_load_changes",
        lambda limit=500: [
            {
                "id": "e1",
                "competitor": "Acme",
                "kind": "pricing",
                "classification": "pricing",
                "significance": 4.5,
                "url": "https://acme.example.com/pricing",
                "snippet": "now $99/mo",
                "dedup_key": "x1",
                "changed_at": "2026-08-19T00:00:00Z",
            }
        ],
    )

    async def run():
        return await ci.generate_digest()

    d = asyncio.run(run())
    assert d["item_count"] == 1
    assert d["items"][0]["what_changed"]
    assert d["items"][0]["so_what"]
    assert d["provider"] == "deterministic"
    # persisted
    assert ci.get_digest(d["id"]) is not None


# ---------------------------------------------------------------------------
# Delivery (records + graceful failure)
# ---------------------------------------------------------------------------
def test_deliver_records_failures(monkeypatch):
    async def bad_email(subject, body, to):
        return False

    async def bad_telegram(body):
        return False

    monkeypatch.setattr(ci, "_deliver_email", bad_email)
    monkeypatch.setattr(ci, "_deliver_telegram", bad_telegram)
    monkeypatch.setattr(ci, "_configured_channels", lambda: ["email", "telegram"])
    monkeypatch.setattr(ci, "_append_delivery", lambda r: None)

    digest = {
        "id": "d1",
        "generated_at": "2026-08-19T00:00:00Z",
        "item_count": 1,
        "items": [],
    }

    async def run():
        return await ci.deliver_digest(digest, channels=["email", "telegram"])

    res = asyncio.run(run())
    assert res["ok"] is False
    assert all(d["ok"] is False for d in res["deliveries"])
    assert len(res["deliveries"]) == 2


def test_email_delivery_offloaded_to_worker_thread(monkeypatch):
    """The blocking SMTP send (email_send) must run in a worker thread, never
    the event loop — a sync SMTP call on the loop would stall the whole API."""
    import threading

    from swarm_os.services import email_service as es

    seen = {}

    def fake_draft(to, subject, body, cc="", attachments=None, account=None):
        return {"ok": True, "send_token": "tok"}

    def fake_send(send_token, confirmed=False):
        seen["thread_name"] = threading.current_thread().name
        return {"ok": True}

    monkeypatch.setattr(es, "email_draft", fake_draft)
    monkeypatch.setattr(es, "email_send", fake_send)
    monkeypatch.setattr(ci.os, "getenv", lambda k, d=None: "me@example.com" if k == "INTEL_EMAIL_TO" else d)

    loop_name = {}

    async def run():
        loop_name["name"] = threading.current_thread().name
        return await ci._deliver_email("subj", "body", "me@example.com")

    res = asyncio.run(run())
    assert res is True
    assert seen["thread_name"] != loop_name["name"]  # ran off the loop


# ---------------------------------------------------------------------------
# End-to-end run_intel
# ---------------------------------------------------------------------------
def test_run_intel_no_changes_returns_early(monkeypatch):
    async def _no_changes(include=None):
        return {"scanned": 1, "changed": 0, "events": [], "errors": []}

    monkeypatch.setattr(ci, "scan_all", _no_changes)

    async def run():
        return await ci.run_intel()

    res = asyncio.run(run())
    assert res["ok"] is True
    assert res["changed"] == 0
    assert res["message"] == "no changes detected"


# ---------------------------------------------------------------------------
# Scheduler: cadence + duplicate-run protection
# ---------------------------------------------------------------------------
def test_intel_due_now_true_when_never_run(monkeypatch, tmp_path):
    monkeypatch.setenv("SWARM_INTEL_DATA_DIR", str(tmp_path / "intel"))
    assert ci.intel_due_now(cadence_hours=168.0) is True


def test_intel_due_now_false_within_window(monkeypatch, tmp_path):
    monkeypatch.setenv("SWARM_INTEL_DATA_DIR", str(tmp_path / "intel"))
    ci._save_last_run({"at": ci._now(), "result": {"ok": True}})
    assert ci.intel_due_now(cadence_hours=168.0) is False


def test_intel_due_now_true_after_window(monkeypatch, tmp_path):
    monkeypatch.setenv("SWARM_INTEL_DATA_DIR", str(tmp_path / "intel"))
    import datetime as _dt

    old = (_dt.datetime.now(_dt.UTC) - _dt.timedelta(hours=200)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    ci._save_last_run({"at": old, "result": {"ok": True}})
    assert ci.intel_due_now(cadence_hours=168.0) is True


def _sync_fetch(tuple_result):
    """Wrap a sync (ok, text, title) triple into a coroutine."""
    ok, text, title = tuple_result

    async def _inner():
        return ok, text, title

    return _inner()


def _fake_fetch(url, text):
    async def _inner():
        return True, text, ""

    return _inner()

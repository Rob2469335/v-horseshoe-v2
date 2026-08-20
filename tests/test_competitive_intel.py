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


def test_unique_tmp_paths_for_registry_digest_last_run(monkeypatch):
    """Registry, digest, and last_run writers must NOT share a temp path across
    writes. Pre-fix all three used the static `.tmp` suffix — concurrent writers
    (daemon + manual run_intel + API generate_digest) could interleave on the
    same path and os.replace a truncated target."""
    from pathlib import Path

    tmp_names = []
    orig_write_text = Path.write_text

    def guarded_write_text(self, *args, **kwargs):
        if ".tmp" in self.name:
            tmp_names.append(self.name)
        return orig_write_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", guarded_write_text)

    ci._save_registry([_comp(), _comp("Beta")])
    ci._save_registry([_comp()])
    ci._save_digest({"id": "d1", "items": []})
    ci._save_digest({"id": "d1", "items": [{"id": "x"}]})
    ci._save_last_run({"at": "2026-08-19T00:00:00+00:00", "result": "ok"})
    ci._save_last_run({"at": "2026-08-19T01:00:00+00:00", "result": "ok"})

    assert len(tmp_names) == 6
    assert len(set(tmp_names)) == 6  # every write gets a distinct temp path
    assert "." in tmp_names[0]  # unique suffix present, not the bare ".tmp"


def test_deliver_telegram_escapes_raw_snippets(monkeypatch):
    """Snippet text is untrusted page/fetch content delivered inside an HTML
    <pre> block (telegram_center.notify defaults to parse_mode="HTML") — raw
    markup must be escaped, never forwarded verbatim."""
    from swarm_os.services import telegram_center as tc

    captured = {}

    def fake_notify(text, **kwargs):
        captured["text"] = text
        return True

    monkeypatch.setattr(tc, "notify", fake_notify)

    async def run():
        return await ci._deliver_telegram("price dropped to <b>$99</b> & more")

    assert asyncio.run(run()) is True
    assert "&lt;b&gt;$99&lt;/b&gt; &amp; more" in captured["text"]
    assert "<pre>" in captured["text"]
    assert captured["text"].count("<pre>") == captured["text"].count("</pre>")


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


def test_meaningful_delta_catches_frequency_repeat_rewrite():
    """A repeat-only rewrite is invisible to set-difference (same word set,
    no NEW words) but is real content churn — a keyword repeated for emphasis.
    Frequency-aware tokenization must catch it. (set: identical sets -> False)"""
    old = "discount on all widgets"
    new = "discount discount discount on all widgets"
    assert ci._meaningful_delta(old, new, "homepage")


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
_BASE_COPY = (
    "Welcome to the future of enterprise logistics automation platform solutions "
    "designed for supply chain teams who manage inventory fleets warehouses routes "
    "and last mile delivery across every region nationwide"
)


def test_scan_target_keeps_baseline_on_subthreshold_drift(monkeypatch):
    """A sub-threshold change must NOT advance the stored snapshot. Pre-fix saved
    the snapshot BEFORE the 5% meaningful-delta check — so each tiny edit re-anchored
    the baseline toward the drift and real changes could never accumulate past the
    floor (slow-drip edits would evade alerts while the baseline tracked them)."""
    comp = _comp()
    content = {"v": _BASE_COPY}
    monkeypatch.setattr(
        ci, "_fetch_target", lambda url: _sync_fetch((True, content["v"], ""))
    )

    async def run():
        await ci.scan_target(comp, "homepage")  # baseline
        # one new token out of ~40 = <5% changed ratio — not a meaningful change
        content["v"] = _BASE_COPY + " boarding"
        res = await ci.scan_target(comp, "homepage")
        snap = ci._load_snapshot(comp["id"], "homepage")
        return res, snap

    res, snap = asyncio.run(run())
    assert res["changed"] is False
    assert snap["content"] == _BASE_COPY  # baseline stayed put, did not drift


# ---------------------------------------------------------------------------
# Noise stripping: tokens scrubbed, never whole-line drops on long content
# ---------------------------------------------------------------------------
def test_strip_noise_scrubs_long_line_instead_of_dropping_it():
    """A short nav/consent line is dropped entirely (real noise), but a LONG line
    that merely mentions a noise token is real content (a minified HTML page is one
    long line) — it must keep whatever survives scrubbing. Pre-fix any noise token
    anywhere dropped the WHOLE line, so this document would come back empty."""
    one_liner = (
        "Enterprise logistics platform now offering nationwide freight shipping "
        "for inventory fleets warehouses routes and last mile delivery across "
        "every region with contract pricing for teams over fifty vehicles "
        "javascript enabled cookie consent banner sign in to manage your quote"
    )
    cleaned = ci._strip_noise(one_liner)
    assert "freight" in cleaned  # real content survived
    assert "contract pricing" in cleaned
    assert "javascript" not in cleaned  # noise tokens scrubbed
    assert "cookie" not in cleaned
    assert "sign in" not in cleaned


def test_strip_noise_bad_line_still_dropped():
    cleaned = ci._strip_noise("We sell widgets\nAccept all cookies\nSign in")
    assert cleaned == "We sell widgets"


def test_extract_added_snippet_scrubs_substantial_noise_line_instead_of_dropping():
    """_extract_added_snippet must mirror _strip_noise: an ADDED line that merely
    mentions a noise token is real content when it is substantial ("Sign in to
    see our new pricing..." is not nav copy). Pre-fix it was dropped on ANY noise
    token, and the drop fell through to the raw-text fallback, shipping the
    scrubbed-away "sign in" noise into the classifier + stored snippet."""
    old = "We sell widgets.\nTrusted by many."
    new = (
        "We sell widgets.\n"
        "Sign in to see our new pro plan pricing details for every team size "
        "with quarterly and annual discounts across the full catalog now"
    )
    snip = ci._extract_added_snippet(new, old)
    assert "pro plan pricing details" in snip  # substantive added content kept
    assert "trusted" not in snip  # not the raw whole-page fallback
    assert "sign in" not in snip.lower()  # noise scrubbed, not shipped raw


def test_extract_added_snippet_minified_fallback_scrubs_noise():
    """A minified page (one long line containing noise words) is not one added
    line with a noise mention — the scrubbed remainder must be returned, never
    the raw un-scrubbed text that the old fallback handed to the classifier."""
    old = "legacy copy"
    new = (
        "javascript enable javascript accept all cookies sign in to read our news "
        + ("national freight network expansion live now " * 20)
        + "with real time tracking integration"
    )
    snip = ci._extract_added_snippet(new, old)
    assert "national freight network expansion" in snip  # real content survived
    assert "javascript" not in snip  # noise scrubbed, not the raw fallback
    assert "accept all" not in snip


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


def test_scan_target_defers_baseline_advance_until_events_durable(monkeypatch):
    """Crash-window regression: a 'changed' scan_target must NOT advance the stored
    baseline itself. Pre-fix the new snapshot was saved at line 507 DURING the
    fan-out, while the change event was appended only after the whole gather in
    scan_all — a crash in that window slid the baseline and permanently lost the
    change (the next scan diffs against the new baseline and never re-emits the
    alert). Now the snapshot rides out with the result and scan_all persists it
    only AFTER the change events have been appended."""
    comp = _comp()
    content = {"v": "initial content"}

    def fake_fetch(url):
        return (True, content["v"], "")

    monkeypatch.setattr(ci, "_fetch_target", lambda url: _sync_fetch(fake_fetch(url)))

    async def run():
        await ci.scan_target(comp, "homepage")  # baseline (still saved immediately)
        baseline = ci._load_snapshot(comp["id"], "homepage")
        content["v"] = "initial content and a brand new product feature"
        res = await ci.scan_target(comp, "homepage")
        after = ci._load_snapshot(comp["id"], "homepage")
        return res, baseline, after

    res, baseline, after = asyncio.run(run())
    assert res["changed"] is True
    # The event carries the new snapshot out of the fan-out...
    assert (
        res["snapshot"]["content"] == "initial content and a brand new product feature"
    )
    # ...but the stored baseline has NOT advanced at the scan_target seam — it
    # advances only once scan_all has made the change event durable.
    assert after["content"] == baseline["content"] == "initial content"


def test_event_added_tokens_exclude_noise(monkeypatch):
    """added_tokens / dedup_key must be built from the noise-stripped text. Pre-fix
    tokenized the raw fetched body, so a real change that also added a cookie/nav
    banner leaked 'javascript'/'cookie'/'consent' into the event's added_tokens —
    which also fingerprints the dedup_key, so the SAME real change got a different
    dedup_key when the banner wording changed (duplicate alert)."""
    content = {"v": "Welcome to Acme logistics platform for enterprise fleets"}

    def fake_fetch(url):
        return (True, content["v"], "")

    monkeypatch.setattr(ci, "_fetch_target", lambda url: _sync_fetch(fake_fetch(url)))

    async def run():
        await ci.scan_target(_comp(), "homepage")  # baseline
        content["v"] = (
            "Welcome to Acme logistics platform for enterprise fleets "
            "Now supporting freight rail nationwide javascript enabled "
            "cookie consent banner sign in to view your quote"
        )
        res = await ci.scan_target(_comp(), "homepage")
        return res

    res = asyncio.run(run())
    assert res["changed"] is True
    tokens = set(res["event"]["added_tokens"])
    assert "freight" in tokens  # the real content change was captured
    assert not (tokens & {"javascript", "cookie", "consent", "sign"})


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


def test_generate_digest_rolls_up_mixed_providers_truthfully(monkeypatch):
    """A digest whose items span providers must NOT be labeled by the LAST item's
    provider. The module-global _last_provider only tracks whichever synthesize
    call finished last — so one remote item followed by an out-of-credit fallback
    to deterministic was mislabeled as fully 'deterministic', and concurrent
    digests clobbered each other's attribution. Each item now carries its own
    provider and the digest rollup reports the truth ('mixed:...' when mixed)."""
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
                "dedup_key": "y1",
                "changed_at": "2026-08-19T00:00:00Z",
            },
            {
                "id": "e2",
                "competitor": "Beta",
                "kind": "changelog",
                "classification": "product_feature",
                "significance": 3.0,
                "url": "https://beta.example.com/changelog",
                "snippet": "launched v2",
                "dedup_key": "y2",
                "changed_at": "2026-08-19T01:00:00Z",
            },
        ],
    )

    calls = {"n": 0}

    async def remote_then_depleted(p):
        calls["n"] += 1
        if calls["n"] == 1:
            return "Remote analysis of the pricing change."
        raise RuntimeError("Insufficient balance")

    synth = ci.IntelligenceSynthesizer(
        remote_complete=remote_then_depleted, local_complete=None
    )

    async def run():
        return await ci.generate_digest(synthesizer=synth)

    d = asyncio.run(run())
    assert d["item_count"] == 2
    # per-item truth: first item came from the remote model, second from the
    # deterministic fallback after the remote call failed
    assert d["items"][0]["provider"] == "remote"
    assert d["items"][1]["provider"] == "deterministic"
    # digest rollup is honest: it was NOT all-remote and NOT all-deterministic
    assert d["provider"] == "mixed:deterministic,remote"


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


def test_scan_all_persistence_offloaded_to_worker_thread(monkeypatch):
    """The change-event append + postponed snapshot writes must run in a worker
    thread, never the event loop — sync disk I/O on the loop (under _LOCK)
    would stall the whole API for the duration of a full scan fan-out."""
    import threading

    monkeypatch.setattr(ci, "list_competitors", lambda: [_comp()])

    content = {"v": "content baseline widgets"}

    def fake_fetch(url):
        if "pricing" in url:
            return (True, "pricing page stable", "")
        return (True, content["v"], "")

    monkeypatch.setattr(ci, "_fetch_target", lambda url: _sync_fetch(fake_fetch(url)))

    orig_append = ci._append_changes
    orig_save = ci._save_snapshot
    seen = {}

    def recording_append(events):
        seen["append_thread"] = threading.current_thread().name
        orig_append(events)

    def recording_save(cid, kind, snap):
        seen["save_thread"] = threading.current_thread().name
        orig_save(cid, kind, snap)

    monkeypatch.setattr(ci, "_append_changes", recording_append)
    monkeypatch.setattr(ci, "_save_snapshot", recording_save)

    loop_name = {}

    async def run():
        loop_name["name"] = threading.current_thread().name
        await ci.scan_all()  # baseline
        content["v"] = "content baseline widgets and a new gadget"
        return await ci.scan_all()  # meaningful change -> event + postponed save

    res = asyncio.run(run())
    assert res["changed"] == 1
    assert seen["append_thread"] != loop_name["name"]  # append ran off the loop
    assert seen["save_thread"] != loop_name["name"]  # snapshot save ran off the loop


def test_digest_delivery_lastrun_disk_offloaded_to_worker_thread(monkeypatch):
    """generate_digest's _load_changes, deliver_digest's _append_delivery, and
    run_intel's _save_last_run are sync disk calls — each must run on a worker
    thread, never the event loop (same class as the scan_all persistence)."""
    import threading

    ci._append_changes(
        [
            {
                "id": "chg1",
                "dedup_key": "chg1",
                "changed_at": "2026-08-19T00:00:00Z",
                "competitor_id": "acme",
                "competitor": "Acme Corp",
                "kind": "homepage",
                "url": "https://acme.example.com",
                "tier": "top_3",
                "classification": "product_feature",
                "significance": 3,
                "added_tokens": ["gadget"],
                "removed_tokens": [],
                "snippet": "added a new fleet gadget",
                "prev_hash": "a",
                "new_hash": "b",
                "summary": "added gadget",
            }
        ]
    )

    async def bad_remote(_prompt):
        raise RuntimeError("no credit")

    loop_name = {}
    seen = {}

    for fn in ("_load_changes", "_append_delivery", "_save_last_run"):
        orig = getattr(ci, fn)

        def wrapper(*args, _fn=fn, _orig=orig, **kwargs):
            seen[_fn] = threading.current_thread().name
            return _orig(*args, **kwargs)

        monkeypatch.setattr(ci, fn, wrapper)

    async def run():
        loop_name["name"] = threading.current_thread().name
        digest = await ci.generate_digest(
            synthesizer=ci.IntelligenceSynthesizer(
                remote_complete=bad_remote, local_complete=None
            )
        )
        await ci.deliver_digest(digest, channels=["email"])
        return digest

    digest = asyncio.run(run())
    assert digest["items"]  # deterministic fallback produced an item

    async def _no_changes(include=None):
        return {"scanned": 1, "changed": 0, "events": [], "errors": []}

    monkeypatch.setattr(ci, "scan_all", _no_changes)

    asyncio.run(ci.run_intel())
    assert seen["_load_changes"] != loop_name["name"]
    assert seen["_append_delivery"] != loop_name["name"]
    assert seen["_save_last_run"] != loop_name["name"]


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


def test_manual_run_intel_advances_scheduler_window(monkeypatch):
    """A MANUAL run_intel (API /intel/run) must record last_run, otherwise the
    weekly daemon's duplicate-run protection sees no last_run and fires a second
    full run inside the same cadence right after the manual one. Pre-fix only the
    daemon path wrote last_run; manual API runs left the window open."""
    async def _scan(include=None):
        return {
            "scanned": 1,
            "changed": 1,
            "events": [
                {
                    "id": "e1",
                    "competitor": "Acme",
                    "kind": "pricing",
                    "classification": "pricing",
                    "significance": 4.5,
                    "url": "https://acme.example.com/pricing",
                    "snippet": "now $99/mo",
                    "dedup_key": "z1",
                    "changed_at": "2026-08-19T00:00:00Z",
                }
            ],
            "errors": [],
        }

    async def _generate(cap=15):
        return {
            "id": "d1",
            "generated_at": "2026-08-19T00:00:00Z",
            "item_count": 1,
            "items": [
                {
                    "id": "e1",
                    "what_changed": "x",
                    "so_what": "y",
                    "provider": "deterministic",
                }
            ],
        }

    async def _deliver(digest, channels=None, email_to=None, webhook_url=None):
        return {}

    monkeypatch.setattr(ci, "scan_all", _scan)
    monkeypatch.setattr(ci, "generate_digest", _generate)
    monkeypatch.setattr(ci, "deliver_digest", _deliver)

    async def run():
        return await ci.run_intel()

    res = asyncio.run(run())
    assert res["ok"] is True
    assert res["changed"] == 1
    # the manual run recorded its completion: the weekly daemon must NOT be due
    assert ci.intel_due_now(cadence_hours=168.0) is False


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

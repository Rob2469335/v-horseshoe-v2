"""W5 distiller-contract tests: the seam is LOCAL-ONLY and fails closed.

These prove the execution boundary, not model quality. There is no cloud path.
"""
from __future__ import annotations

import contextlib
import http.server
import inspect
import json
import threading

import pytest

from swarm_os.services.lesson_distiller import (
    DistillerIdentity,
    LocalOnlyError,
    default_local_identity,
    make_fake_distiller,
    make_local_distiller,
)

#: A declared weights digest for fixtures that exercise TRANSPORT behaviour.
#: Since the R5 reproducibility pass, ``make_local_distiller`` refuses an identity
#: that cannot reproduce its own transformation, so any fixture building a valid
#: LOCAL identity must declare one. These tests are about transport containment,
#: not provenance, so a fixed placeholder digest is supplied to satisfy the
#: stricter construction contract without altering what they assert.
_TEST_WEIGHTS_DIGEST = "0" * 64


def _local_identity(base_url: str, model_id: str = "m") -> DistillerIdentity:
    """A valid, reproducible local identity for transport-level fixtures."""
    return DistillerIdentity(
        provider="local",
        model_id=model_id,
        base_url=base_url,
        weights_digest=_TEST_WEIGHTS_DIGEST,
    )


class _RecordingHandler(http.server.BaseHTTPRequestHandler):
    """A tiny loopback endpoint that records requests and can redirect."""

    def do_POST(self):  # noqa: N802 - stdlib hook name
        srv = self.server
        srv.requests.append(self.path)
        if srv.redirect_to:
            self.send_response(302)
            self.send_header("Location", srv.redirect_to)
            self.end_headers()
            return
        if srv.status != 200:
            self.send_response(srv.status)
            self.end_headers()
            return
        body = json.dumps(
            {"choices": [{"message": {"content": "LOCAL_RULE"}}]}
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # silence test output
        pass


@contextlib.contextmanager
def _server(redirect_to=None, status=200):
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _RecordingHandler)
    srv.requests = []
    srv.redirect_to = redirect_to
    srv.status = status
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        yield srv, f"http://127.0.0.1:{srv.server_address[1]}"
    finally:
        srv.shutdown()
        srv.server_close()
        t.join(timeout=5)


@contextlib.contextmanager
def _hostile_proxy_env(monkeypatch, proxy_url):
    for name in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy",
                 "ALL_PROXY", "all_proxy"):
        monkeypatch.setenv(name, proxy_url)
    yield


class TestLocalOnlyBoundary:
    def test_accepts_loopback_local_identity(self):
        d = make_local_distiller(
            _local_identity("http://127.0.0.1:8080", "qwen3.5-4b"),
            complete=lambda _p: "rule",
        )
        assert d.identity.qualified_id == "local:qwen3.5-4b"
        assert d("prompt") == "rule"

    @pytest.mark.parametrize("provider", ["openrouter", "nvidia", "gemini", "groq", "cloud"])
    def test_rejects_non_local_provider(self, provider):
        with pytest.raises(LocalOnlyError):
            make_local_distiller(
                DistillerIdentity(provider, "m", "http://127.0.0.1:8080"),
                complete=lambda _p: "x",
            )

    @pytest.mark.parametrize("base_url", [
        "https://127.0.0.1:8080",          # non-http scheme
        "http://10.0.0.5:8080",            # non-loopback host
        "http://api.openrouter.ai/v1",     # external provider
        "http://example.com",
        "",
    ])
    def test_rejects_non_loopback_base_url(self, base_url):
        with pytest.raises(LocalOnlyError):
            make_local_distiller(
                DistillerIdentity("local", "m", base_url),
                complete=lambda _p: "x",
            )

    def test_rejects_embedded_credentials(self):
        """A loopback URL with userinfo is still refused (unreviewed cred channel)."""
        with pytest.raises(LocalOnlyError):
            make_local_distiller(
                DistillerIdentity("local", "m", "http://user:pass@127.0.0.1:8080"),
                complete=lambda _p: "x",
            )

    def test_rejects_when_router_pinned_remote(self, monkeypatch):
        """The :8080 Smart Model Proxy may forward externally when pinned.

        In the RUNPOD topology (:8079 = ssh tunnel to a pod GPU) a loopback
        :8080 request is a network call, so the local-only distiller refuses.
        """
        monkeypatch.setenv("SWARM_ROUTER_PINNED", "1")
        with pytest.raises(LocalOnlyError):
            make_local_distiller(
                DistillerIdentity("local", "m", "http://127.0.0.1:8080"),
                complete=lambda _p: "x",
            )

    def test_default_identity_requires_explicit_model(self, monkeypatch):
        """No silent model identity: unset SWARM_DISTILLER_MODEL fails closed."""
        monkeypatch.delenv("SWARM_DISTILLER_MODEL", raising=False)
        with pytest.raises(LocalOnlyError):
            default_local_identity()

    def test_default_identity_is_local_loopback_when_set(self, monkeypatch):
        monkeypatch.setenv("SWARM_DISTILLER_MODEL", "robs4b")
        monkeypatch.setenv("SWARM_DISTILLER_WEIGHTS_DIGEST", _TEST_WEIGHTS_DIGEST)
        monkeypatch.delenv("SWARM_DISTILLER_WEIGHTS_DIGEST_UNAVAILABLE", raising=False)
        ident = default_local_identity()
        assert ident.provider == "local"
        assert ident.model_id == "robs4b"
        assert ident.base_url.startswith("http://127.0.0.1")

    def test_http_failure_fails_closed_not_network_fallback(self):
        """A local call failure raises LocalOnlyError; it never falls back."""
        # Point the built-in completion at a dead local port.
        d = make_local_distiller(_local_identity("http://127.0.0.1:59999"))
        with pytest.raises(LocalOnlyError):
            d("prompt")


class TestFakeVsReal:
    def test_fake_identity_is_distinguishable(self):
        fake = make_fake_distiller("rule")
        assert fake.identity.provider == "fake"
        assert fake.identity.qualified_id.startswith("fake:")
        assert fake("any prompt") == "rule"

    def test_module_has_no_cloud_provider_surface(self):
        import swarm_os.services.lesson_distiller as ld

        src = inspect.getsource(ld)
        for forbidden in ("openrouter", "OPENROUTER", "api_key", "API_KEY",
                          "nvidia", "gemini", "groq", "https://"):
            assert forbidden not in src, forbidden


class TestTransportContainment:
    """Adversarial transport tests: proxy env and redirects cannot escape loopback.

    Each test drives the REAL built-in transport (``_http_openai_complete``) and
    counts requests on controlled loopback servers — not a source scan.
    """

    @staticmethod
    def _distiller(base_url):
        return make_local_distiller(_local_identity(base_url))

    def test_http_proxy_cannot_escape(self, monkeypatch):
        with _server() as (target, target_url), _server() as (proxy, proxy_url):
            with _hostile_proxy_env(monkeypatch, proxy_url):
                out = self._distiller(target_url)("prompt")
            assert out == "LOCAL_RULE"
            assert target.requests == ["/v1/chat/completions"]
            assert proxy.requests == []

    def test_https_proxy_cannot_escape(self, monkeypatch):
        with _server() as (target, target_url), _server() as (proxy, proxy_url):
            monkeypatch.setenv("HTTPS_PROXY", proxy_url)
            monkeypatch.setenv("https_proxy", proxy_url)
            out = self._distiller(target_url)("prompt")
            assert out == "LOCAL_RULE"
            assert target.requests == ["/v1/chat/completions"]
            assert proxy.requests == []

    def test_all_proxy_cannot_escape(self, monkeypatch):
        with _server() as (target, target_url), _server() as (proxy, proxy_url):
            monkeypatch.setenv("ALL_PROXY", proxy_url)
            monkeypatch.setenv("all_proxy", proxy_url)
            out = self._distiller(target_url)("prompt")
            assert out == "LOCAL_RULE"
            assert target.requests == ["/v1/chat/completions"]
            assert proxy.requests == []

    def test_redirect_offhost_rejected(self):
        with _server(redirect_to="http://attacker.invalid/steal") as (target, target_url):
            with pytest.raises(LocalOnlyError):
                self._distiller(target_url)("prompt")
            assert target.requests == ["/v1/chat/completions"]

    def test_redirect_loopback_rejected(self):
        with _server() as (other, other_url):
            with _server(redirect_to=other_url + "/steal") as (target, target_url):
                with pytest.raises(LocalOnlyError):
                    self._distiller(target_url)("prompt")
                assert target.requests == ["/v1/chat/completions"]
                assert other.requests == []

    def test_normal_local_request_works(self):
        with _server() as (target, target_url):
            out = self._distiller(target_url)("prompt")
            assert out == "LOCAL_RULE"
            assert target.requests == ["/v1/chat/completions"]

    def test_remote_endpoint_rejected_before_request(self):
        with pytest.raises(LocalOnlyError):
            self._distiller("https://example.com")

    def test_no_fallback_on_transport_failure(self):
        # A dead local port must raise; there is no alternate URL or provider.
        d = make_local_distiller(_local_identity("http://127.0.0.1:59999"))
        with pytest.raises(LocalOnlyError):
            d("prompt")

    def test_proxy_env_with_local_endpoint_goes_direct(self, monkeypatch):
        with _server() as (target, target_url), _server() as (proxy, proxy_url):
            with _hostile_proxy_env(monkeypatch, proxy_url):
                d = self._distiller(target_url)
                assert d.identity.base_url == target_url
                assert d("prompt") == "LOCAL_RULE"
            assert target.requests == ["/v1/chat/completions"]
            assert proxy.requests == []

    def test_redirect_makes_exactly_one_request(self):
        with _server() as (other, other_url):
            with _server(redirect_to=other_url + "/steal") as (target, target_url):
                with pytest.raises(LocalOnlyError):
                    self._distiller(target_url)("prompt")
                assert len(target.requests) == 1
                assert len(other.requests) == 0

    def test_opener_disables_proxy_and_redirects(self):
        """Supplementary structural check of the explicit opener.

        CPython's ``OpenerDirector.add_handler`` skips ``proxy_open`` as a
        coincidental match, so ``ProxyHandler({})`` installs NO proxy dispatch:
        ``handle_open`` has no ``'proxy'`` entry and environment proxies cannot
        apply. The redirect handler is our rejecting subclass.
        """
        import urllib.request as ur

        import swarm_os.services.lesson_distiller as ld

        assert "proxy" not in ld._LOCAL_OPENER.handle_open
        assert issubclass(ld._RejectRedirects, ur.HTTPRedirectHandler)
        assert any(isinstance(h, ld._RejectRedirects) for h in ld._LOCAL_OPENER.handlers)
        assert not any(isinstance(h, ur.ProxyHandler) for h in ld._LOCAL_OPENER.handlers)
        # Our subclass refuses to produce a redirect request.
        assert ld._RejectRedirects().redirect_request(
            None, None, 302, "Found", {}, "http://x/"
        ) is None

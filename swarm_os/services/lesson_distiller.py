"""Controlled, LOCAL-ONLY distiller seam for Experiment J lesson synthesis (W5).

Information boundary (what Stage B may see)
-------------------------------------------
The distiller is called by ``lesson_synthesis.abstract`` with a prompt built
ONLY from a ``Diagnosis`` (mechanism + measured behavioural feature codes). It
never receives candidate state, a patch, a diff, a test patch, gold-patch text,
a target repair location, F1 history, a previous lesson, or operator knowledge.
The boundary is enforced upstream in ``abstract``; this module only chooses the
provider that turns that prompt into text.

Execution boundary (local only — Q2)
------------------------------------
The experimental learning path must make NO cloud/network call. This module
therefore exposes exactly one production constructor, ``make_local_distiller``,
which:

* requires ``provider == "local"`` and a loopback ``base_url`` (http, host in
  127.0.0.1/localhost/::1) or it raises ``LocalOnlyError`` before any call;
* POSTs to the local OpenAI-compatible endpoint (``/v1/chat/completions``);
* uses an explicit transport (``_LOCAL_OPENER``) that **disables inherited
  proxy configuration** (``ProxyHandler({})`` — ``HTTP_PROXY``/``HTTPS_PROXY``/
  ``ALL_PROXY`` and lowercase forms are never consulted) and **rejects every
  HTTP redirect** (a 3xx is a transport error, not a route), so a request cannot
  escape the validated loopback destination;
* on ANY failure raises ``LocalOnlyError`` — there is **no** fallback to a cloud
  provider, no provider substitution, and no credential lookup for external
  services. ``abstract`` converts that exception into a fail-closed
  ``distiller_error`` and no principle is produced.

DEVELOPMENT FAKE vs REAL LOCAL
------------------------------
``make_fake_distiller`` is a deterministic test double for the DEVELOPMENT path.
It carries ``provider="fake"`` and must never be used for an experimental run.
The identity is recorded on the produced ``Principle``
(``distiller_id = provider:model``) and written into the persisted
``SynthesisAttestation.rationale`` (``distiller=<id>``), so a fake-produced
attestation is distinguishable from a real-local one in the durable record.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.request
from dataclasses import dataclass
from typing import Callable
from urllib.parse import urlparse

log = logging.getLogger(__name__)

LOCAL_PROVIDER = "local"
FAKE_PROVIDER = "fake"
DEFAULT_LOCAL_BASE_URL = "http://127.0.0.1:8080"
DEFAULT_LOCAL_MODEL = "qwen3.5-4b"
_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1", "0.0.0.0"}

#: Style guidance only. The information boundary is enforced by what the caller
#: puts in the user prompt (``abstract`` -> mechanism + feature codes).
_SYSTEM = (
    "You convert one software-engineering failure mechanism into ONE transferable "
    "behavioural rule for a coding agent. State a GENERAL principle that would "
    "apply to an unrelated repository. Never name a repository, file, symbol, "
    "test, issue, commit or URL. Never prescribe the concrete repair. Never "
    "restate that the attempt failed. One or two imperative sentences, no "
    "preamble, no markdown."
)


class LocalOnlyError(RuntimeError):
    """Raised when a non-local distiller is requested or the local call fails."""


@dataclass(frozen=True)
class DistillerIdentity:
    provider: str
    model_id: str
    base_url: str = ""

    @property
    def qualified_id(self) -> str:
        return f"{self.provider}:{self.model_id}"


class LocalDistiller:
    """Callable distiller carrying an explicit provider/model identity."""

    def __init__(self, identity: DistillerIdentity, complete: Callable[[str], str]):
        self.identity = identity
        self._complete = complete

    def __call__(self, prompt: str) -> str:
        return self._complete(prompt)


def _assert_local(identity: DistillerIdentity) -> None:
    if identity.provider != LOCAL_PROVIDER:
        raise LocalOnlyError(
            f"refusing non-local distiller provider {identity.provider!r}"
        )
    # The default endpoint (127.0.0.1:8080) is the Smart Model Proxy
    # (`model_router.py`), which forwards to :8079. In the RUNPOD topology
    # (:8079 = ssh tunnel to a pod GPU) that forward is a network call, so
    # "local HTTP endpoint" would NOT mean "local inference execution". Refuse
    # to run in that topology rather than silently reach a remote endpoint.
    if os.environ.get("SWARM_ROUTER_PINNED", "0") == "1":
        raise LocalOnlyError(
            "refusing local distiller: model router is pinned to a remote topology"
        )
    parsed = urlparse(identity.base_url or "")
    if parsed.scheme != "http" or (parsed.hostname or "") not in _LOOPBACK_HOSTS:
        raise LocalOnlyError(
            f"refusing non-loopback distiller base_url {identity.base_url!r}"
        )
    # The distiller uses a fixed local `Bearer llama` token; a URL userinfo would
    # be an unreviewed credential channel. Reject it (still loopback, but noise).
    if parsed.username or parsed.password:
        raise LocalOnlyError(
            "refusing distiller base_url with embedded credentials"
        )


class _RejectRedirects(urllib.request.HTTPRedirectHandler):
    """Refuse every HTTP redirect (301/302/303/307/308).

    Returning ``None`` from ``redirect_request`` makes ``http_error_302`` return
    without following, and the opener's default error handler then raises
    ``HTTPError``. The request never reaches the redirect target — not even a
    loopback one. Redirects are outside the authorized destination policy.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


#: The ONLY transport used by the local distiller. It is built explicitly so
#: that:
#:   * no proxy is inherited from the environment (``ProxyHandler({})`` disables
#:     ``getproxies()``; ``build_opener`` then omits its default ProxyHandler),
#:     and
#:   * no redirect is followed (``_RejectRedirects`` replaces the default
#:     ``HTTPRedirectHandler``).
#: This makes the request destination the validated loopback URL and nothing else.
_LOCAL_OPENER = urllib.request.build_opener(
    urllib.request.ProxyHandler({}),
    _RejectRedirects(),
)


def _http_openai_complete(
    identity: DistillerIdentity, timeout: float
) -> Callable[[str], str]:
    url = identity.base_url.rstrip("/") + "/v1/chat/completions"

    def _complete(prompt: str) -> str:
        body = json.dumps(
            {
                "model": identity.model_id,
                "messages": [
                    {"role": "system", "content": _SYSTEM},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.0,
                "max_tokens": 200,
            }
        ).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer llama",
            },
            method="POST",
        )
        try:
            # Explicit opener: no inherited proxy, no redirect following.
            with _LOCAL_OPENER.open(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception as exc:  # noqa: BLE001 - LOCAL ONLY, no network fallback
            raise LocalOnlyError(
                f"local distiller unavailable: {type(exc).__name__}"
            ) from exc
        try:
            return data["choices"][0]["message"]["content"]
        except Exception as exc:  # noqa: BLE001 - malformed local response fails closed
            raise LocalOnlyError(
                f"local distiller malformed response: {type(exc).__name__}"
            ) from exc

    return _complete


def make_local_distiller(
    identity: DistillerIdentity,
    *,
    complete: Callable[[str], str] | None = None,
    timeout: float = 60.0,
) -> LocalDistiller:
    """Build the REAL LOCAL experimental distiller. Rejects non-local identities."""
    _assert_local(identity)
    return LocalDistiller(identity, complete or _http_openai_complete(identity, timeout))


def make_fake_distiller(
    text: str, *, model_id: str = "test-double"
) -> LocalDistiller:
    """Deterministic DEVELOPMENT FAKE. Never valid for an experimental run."""
    return LocalDistiller(
        DistillerIdentity(provider=FAKE_PROVIDER, model_id=model_id),
        lambda _prompt: text,
    )


def default_local_identity() -> DistillerIdentity:
    """The default experimental identity (provider=local, local model)."""
    import os

    return DistillerIdentity(
        provider=LOCAL_PROVIDER,
        model_id=os.environ.get("SWARM_DISTILLER_MODEL", DEFAULT_LOCAL_MODEL),
        base_url=os.environ.get("SWARM_DISTILLER_BASE_URL", DEFAULT_LOCAL_BASE_URL),
    )

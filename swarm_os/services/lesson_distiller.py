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
#: Deprecated placeholder — no silent default. The model identity must be set
#: explicitly via ``SWARM_DISTILLER_MODEL`` (see ``default_local_identity``).
DEFAULT_LOCAL_MODEL = ""
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


#: Sampling configuration frozen for every distillation call. Recorded on the
#: identity so a persisted attestation can prove the exact transformation inputs,
#: not merely which model was named. ``temperature`` is 0.0 deliberately: the
#: distiller turns ONE failure into ONE rule, and a stochastic distiller would
#: make the promoted lesson irreproducible while contributing no benefit.
DISTILLER_TEMPERATURE = 0.0
DISTILLER_MAX_TOKENS = 200
DISTILLER_TOP_P = 1.0
DISTILLER_SEED: int | None = None


def distiller_prompt_digest() -> str:
    """SHA-256 over the frozen Stage-B system+user template pair.

    The template IS part of the transformation: a different system prompt
    produces a different lesson from identical evidence. Hashing it means an
    attestation can prove which template was in force, so a template edit cannot
    silently change what "the distiller" means between two learning events.
    """
    import hashlib

    from swarm_os.services import lesson_synthesis as _ls

    payload = json.dumps(
        {
            "system": _SYSTEM,
            "user_template": _ls._B_USER,
            "system_prompt": _ls._B_SYSTEM,
            "temperature": DISTILLER_TEMPERATURE,
            "max_tokens": DISTILLER_MAX_TOKENS,
            "top_p": DISTILLER_TOP_P,
            "seed": DISTILLER_SEED,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class DistillerIdentity:
    """Everything needed to reproduce one distillation, or fail closed.

    R5. ``provider``/``model_id`` alone were insufficient: two runs naming the
    same model can differ by weights checkpoint, prompt template, or sampling
    parameters, and none of that was recorded. Each added field is REQUIRED for
    an experimental identity (``require_reproducible=True``, the default used by
    ``default_local_identity``) so an incomplete identity is refused rather than
    persisted as if it were reproducible.
    """

    provider: str
    model_id: str
    base_url: str = ""
    weights_digest: str = ""
    prompt_digest: str = ""
    temperature: float = DISTILLER_TEMPERATURE
    max_tokens: int = DISTILLER_MAX_TOKENS
    top_p: float = DISTILLER_TOP_P
    seed: int | None = DISTILLER_SEED
    code_version: str = ""

    def __post_init__(self) -> None:
        if not self.prompt_digest:
            object.__setattr__(self, "prompt_digest", distiller_prompt_digest())
        if not self.code_version:
            object.__setattr__(self, "code_version", _code_version())

    @property
    def qualified_id(self) -> str:
        return f"{self.provider}:{self.model_id}"

    def reproducibility_record(self) -> dict:
        """The provenance block written into a persisted attestation."""
        return {
            "provider": self.provider,
            "model_id": self.model_id,
            "qualified_id": self.qualified_id,
            "base_url": self.base_url,
            "weights_digest": self.weights_digest,
            "prompt_digest": self.prompt_digest,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "top_p": self.top_p,
            "seed": self.seed,
            "code_version": self.code_version,
        }

    def assert_reproducible(self) -> None:
        """Fail closed unless the identity can reproduce the transformation.

        ``weights_digest`` may legitimately be empty for a provider that does not
        expose one, but then the operator must say so explicitly via
        ``SWARM_DISTILLER_WEIGHTS_DIGEST_UNAVAILABLE=1`` - silence is not consent.
        """
        if self.temperature != DISTILLER_TEMPERATURE:
            raise LocalOnlyError(
                f"distiller temperature must be frozen at "
                f"{DISTILLER_TEMPERATURE}, got {self.temperature!r}"
            )
        if not self.model_id.strip():
            raise LocalOnlyError("distiller model_id is empty")
        if not self.weights_digest:
            import os

            if os.environ.get(
                "SWARM_DISTILLER_WEIGHTS_DIGEST_UNAVAILABLE", ""
            ).strip() != "1":
                raise LocalOnlyError(
                    "distiller weights digest is not established: set "
                    "SWARM_DISTILLER_WEIGHTS_DIGEST, or set "
                    "SWARM_DISTILLER_WEIGHTS_DIGEST_UNAVAILABLE=1 to record "
                    "explicitly that the provider exposes none. Failing closed "
                    "rather than persisting an unreproducible identity."
                )


def _code_version() -> str:
    """Identity of the transformation code, for the attestation record."""
    try:
        from swarm_os.services.lesson_distiller import __file__ as _f

        import hashlib

        with open(_f, "rb") as fh:
            return "lesson_distiller:" + hashlib.sha256(fh.read()).hexdigest()[:16]
    except Exception:  # noqa: BLE001 - provenance best-effort, never fatal
        return "lesson_distiller:unknown"


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
                # Frozen sampling config comes FROM the identity, so the recorded
                # provenance and the executed request cannot diverge.
                "temperature": identity.temperature,
                "max_tokens": identity.max_tokens,
                "top_p": identity.top_p,
                **({"seed": identity.seed} if identity.seed is not None else {}),
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
    require_reproducible: bool = True,
) -> LocalDistiller:
    """Build the REAL LOCAL experimental distiller. Rejects non-local identities.

    ``require_reproducible`` (default) additionally refuses an identity that
    cannot reproduce its own transformation, so an experimental distiller can
    never be constructed from a bare ``provider:model`` pair.
    """
    _assert_local(identity)
    if require_reproducible:
        identity.assert_reproducible()
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
    """The experimental identity (provider=local, EXPLICIT model + provenance).

    No silent model default: the declared identity must match the served
    artifact, and which model that is (``robs4b`` vs ``qwen3.5-4b``) is an
    operator/scientific decision not established by repository authority
    (``GOVERNANCE GAP`` / ``REQUIRES AUTHORIZATION``). A missing identity is
    refused (fail closed) rather than guessed, so ``get_prompt_repairer``
    disables synthesis instead of running under an unknown model identity.

    R5: the weights digest must also be declared, because naming a model does not
    pin its weights.
    """
    import os

    model_id = os.environ.get("SWARM_DISTILLER_MODEL", "").strip()
    if not model_id:
        raise LocalOnlyError(
            "distiller model identity is not established: set SWARM_DISTILLER_MODEL"
        )
    identity = DistillerIdentity(
        provider=LOCAL_PROVIDER,
        model_id=model_id,
        base_url=os.environ.get("SWARM_DISTILLER_BASE_URL", DEFAULT_LOCAL_BASE_URL),
        weights_digest=os.environ.get("SWARM_DISTILLER_WEIGHTS_DIGEST", "").strip(),
    )
    identity.assert_reproducible()
    return identity

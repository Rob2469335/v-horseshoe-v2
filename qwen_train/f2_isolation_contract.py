"""F2 isolation timeout contract.

The Hyper-V stateful ACL idle session timeout MUST be strictly greater than the
longest relevant upstream application timeout, with meaningful margin, so the
guest NIC boundary is never the first timeout to kill an otherwise-valid model
session.

Traced values (2026-10-07):

* model client streaming timeout ceiling: ``900`` s
  (``runtime_v2/services/_llm_client.py:617`` and ``:633``); other client paths
  use 120/300/600 s.
* F2 model gateway upstream request timeout: ``960`` s
  (``qwen_train/f2_model_gateway.py`` ``DEFAULT_TIMEOUT_S``) -- must exceed the
  900 s client ceiling so a long stream is not cut off by the proxy.
* Hyper-V extended-ACL ``-IdleSessionTimeout``: ``1800`` s (2x the client
  ceiling, > gateway) -- the single stateful allow's session state.

This module is pure (no I/O) so the ordering can be asserted directly. It changes
no F0/F1 science.
"""

from __future__ import annotations

#: Highest model-client request timeout (streaming path). Source:
#: runtime_v2/services/_llm_client.py:617,633.
MODEL_CLIENT_STREAM_TIMEOUT_S = 900

#: F2 model gateway upstream request timeout (qwen_train/f2_model_gateway.py).
GATEWAY_REQUEST_TIMEOUT_S = 960

#: model_router proxy -> llama total request timeout. Source:
#: model_router.py:104 (``httpx.AsyncClient(timeout=300.0)``). This is the BINDING
#: ceiling in the real chain (guest -> gateway -> model_router -> llama), because
#: min(router, gateway, client) = 300 s.
MODEL_ROUTER_PROXY_TIMEOUT_S = 300

#: Hyper-V extended-ACL idle session timeout for the single stateful allow.
#:
#: Semantics: this is a SILENT-GAP bound, NOT a total-request timeout. The maximum
#: silent interval between packets is bounded by the upstream compute time, whose
#: effective ceiling is min(router 300, gateway 960, client 900) = 300 s. 1800 s
#: is ~6x that ceiling, so the NIC never drops a live session mid-generation.
HYPERV_IDLE_SESSION_TIMEOUT_S = 1800


def effective_total_ceiling_s() -> int:
    """min(router, gateway, client): the real end-to-end request ceiling."""
    return min(
        MODEL_ROUTER_PROXY_TIMEOUT_S,
        GATEWAY_REQUEST_TIMEOUT_S,
        MODEL_CLIENT_STREAM_TIMEOUT_S,
    )


class F2TimeoutContractError(RuntimeError):
    """The timeout ordering required for a valid long model stream is violated."""


def validate_timeout_ordering(
    *,
    client_s: int = MODEL_CLIENT_STREAM_TIMEOUT_S,
    gateway_s: int = GATEWAY_REQUEST_TIMEOUT_S,
    hyperv_s: int = HYPERV_IDLE_SESSION_TIMEOUT_S,
) -> None:
    """Fail closed unless client < gateway < hyperv, all integer seconds."""
    for name, value in (
        ("client_s", client_s),
        ("gateway_s", gateway_s),
        ("hyperv_s", hyperv_s),
    ):
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise F2TimeoutContractError(f"{name} must be a positive int seconds")
    if not client_s < gateway_s:
        raise F2TimeoutContractError(
            f"gateway timeout ({gateway_s}s) must exceed the model client "
            f"streaming ceiling ({client_s}s)"
        )
    if not gateway_s < hyperv_s:
        raise F2TimeoutContractError(
            f"Hyper-V idle session timeout ({hyperv_s}s) must exceed the gateway "
            f"timeout ({gateway_s}s)"
        )

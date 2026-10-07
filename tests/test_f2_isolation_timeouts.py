"""F2 isolation timeout contract tests (CODE PROOF).

Proves the Hyper-V stateful-ACL idle session timeout is strictly greater than
the gateway timeout, which is strictly greater than the model client streaming
ceiling, and that the provisioner's configured value matches the contract.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from qwen_train import f2_isolation_contract as c
from qwen_train import f2_model_gateway as g

SRC = Path(__file__).resolve().parents[1] / "qwen_train" / "f2_vm_provision.ps1"


def test_ordering_is_valid():
    c.validate_timeout_ordering()  # must not raise


def test_declared_values_are_integers_and_positive():
    for v in (
        c.MODEL_CLIENT_STREAM_TIMEOUT_S,
        c.GATEWAY_REQUEST_TIMEOUT_S,
        c.HYPERV_IDLE_SESSION_TIMEOUT_S,
    ):
        assert isinstance(v, int) and not isinstance(v, bool) and v > 0


def test_gateway_default_exceeds_client_ceiling():
    assert g.DEFAULT_TIMEOUT_S > c.MODEL_CLIENT_STREAM_TIMEOUT_S
    assert float(g.DEFAULT_TIMEOUT_S).is_integer()


def test_hyperv_idle_exceeds_upstream_app_timeouts():
    assert c.HYPERV_IDLE_SESSION_TIMEOUT_S > c.GATEWAY_REQUEST_TIMEOUT_S
    assert c.HYPERV_IDLE_SESSION_TIMEOUT_S > c.MODEL_CLIENT_STREAM_TIMEOUT_S


def test_effective_ceiling_is_the_router():
    # The real chain is guest -> gateway -> model_router -> llama; the router's
    # 300 s client timeout is the binding ceiling.
    assert c.effective_total_ceiling_s() == c.MODEL_ROUTER_PROXY_TIMEOUT_S


def test_hyperv_idle_is_a_silent_gap_bound_not_a_total():
    # The Hyper-V idle timeout must exceed the maximum silent gap, which is
    # bounded by the effective total ceiling.
    assert c.HYPERV_IDLE_SESSION_TIMEOUT_S > c.effective_total_ceiling_s()
    assert c.HYPERV_IDLE_SESSION_TIMEOUT_S >= 4 * c.MODEL_ROUTER_PROXY_TIMEOUT_S


def test_no_stale_300_hyperv_text():
    prov = SRC.read_text(encoding="utf-8")
    # Plan line must interpolate the parameter (never a hardcoded stale value).
    assert "idle $IdleSessionTimeoutSeconds s" in prov
    assert "idle 300" not in prov, "stale Hyper-V idle value 300 must not return"
    doc = (SRC.parents[1] / "docs" / "EXPERIMENT_J_F2_VM_ISOLATION.md").read_text(encoding="utf-8")
    assert "IdleSessionTimeout=300" not in doc
    assert "IdleSessionTimeout=1800" in doc


def test_provisioner_uses_contract_value():
    text = SRC.read_text(encoding="utf-8")
    m = re.search(r"\[int\]\$IdleSessionTimeoutSeconds\s*=\s*(\d+)", text)
    assert m, "provisioner must declare IdleSessionTimeoutSeconds"
    assert int(m.group(1)) == c.HYPERV_IDLE_SESSION_TIMEOUT_S


def test_validate_rejects_bad_ordering():
    with pytest.raises(c.F2TimeoutContractError):
        c.validate_timeout_ordering(client_s=900, gateway_s=300, hyperv_s=1800)
    with pytest.raises(c.F2TimeoutContractError):
        c.validate_timeout_ordering(client_s=900, gateway_s=960, hyperv_s=960)
    with pytest.raises(c.F2TimeoutContractError):
        c.validate_timeout_ordering(client_s=0, gateway_s=960, hyperv_s=1800)

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

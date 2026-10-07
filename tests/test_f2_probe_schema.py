"""Guest probe schema tests + static checks on the probe script.

Schema tests are pure (no VM). The static checks are CODE PROOF that the probe
does not invoke F2 execution and does declare the required sections.
"""

from __future__ import annotations

from pathlib import Path

from qwen_train import f2_probe_schema as s

PROBE = Path(__file__).resolve().parents[1] / "qwen_train" / "f2_guest_probe.ps1"


def _good() -> dict:
    return {
        "schema_version": s.SCHEMA_VERSION,
        "probe_started_utc": "2026-10-07T00:00:00+00:00",
        "host_reference_utc": "2026-10-07T00:00:00+00:00",
        "clock_reference_src": "HTTP_DATE_HEADER",
        "clock_skew_seconds": 1.0,
        "clock_status": "OK",
        "clock_reason": "",
        "adapter_inventory": [],
        "adapter_count": 1,
        "route_inventory": [],
        "dns_inventory": [],
        "authorized_flow": {"tcp_ok": True, "http_status": 200},
        "denied_flows": [],
        "arbitrary_ipv4": {"result": "NOT_ESTABLISHED"},
        "dns_behavior": {"result": "NOT_APPLICABLE"},
        "ipv4": {},
        "ipv6": {"result": "NOT_ESTABLISHED"},
        "icmp": {"result": "OBSERVED_BEHAVIOR"},
        "input_disk_write": {"result": "NOT_APPLICABLE"},
        "internet": {},
        "required_checks_total": 5,
        "required_checks_passed": 5,
        "required_checks_failed": 0,
        "required_checks_not_established": 0,
        "overall_status": "PASS",
        "failure_reasons": [],
    }


def test_pass_invalid_when_required_not_established():
    bad = _good()
    bad["required_checks_passed"] = 4
    bad["required_checks_not_established"] = 1
    bad["overall_status"] = "PASS"
    assert any("PASS is invalid" in e for e in s.validate(bad))


def test_pass_invalid_when_required_failed():
    bad = _good()
    bad["required_checks_passed"] = 4
    bad["required_checks_failed"] = 1
    bad["overall_status"] = "PASS"
    assert any("PASS is invalid" in e for e in s.validate(bad))


def test_counts_must_be_consistent():
    bad = _good()
    bad["required_checks_total"] = 9
    assert any("inconsistent" in e for e in s.validate(bad))


def test_not_ready_is_accepted():
    ok = _good()
    ok["required_checks_passed"] = 4
    ok["required_checks_not_established"] = 1
    ok["overall_status"] = "NOT_READY"
    assert s.validate(ok) == []


def test_good_result_is_valid():
    assert s.validate(_good()) == []


def test_missing_key_rejected():
    bad = _good()
    del bad["clock_status"]
    assert any("clock_status" in e for e in s.validate(bad))


def test_bad_overall_status_rejected():
    bad = _good()
    bad["overall_status"] = "MAYBE"
    assert any("overall_status" in e for e in s.validate(bad))


def test_bad_clock_status_rejected():
    bad = _good()
    bad["clock_status"] = "MAYBE"
    assert any("clock_status" in e for e in s.validate(bad))


def test_failure_reasons_must_be_list_of_str():
    bad = _good()
    bad["failure_reasons"] = "nope"
    assert any("failure_reasons" in e for e in s.validate(bad))


def test_non_dict_rejected():
    assert s.validate(["not", "a", "dict"])


def test_canonical_is_deterministic():
    a = s.canonical(_good())
    b = s.canonical(_good())
    assert a == b
    assert a.startswith('{"adapter_count"')


def test_probe_script_declares_required_sections():
    text = PROBE.read_text(encoding="utf-8")
    for marker in (
        "schema_version",
        "clock_skew_seconds",
        "adapter_inventory",
        "route_inventory",
        "dns_inventory",
        "authorized_flow",
        "denied_flows",
        "overall_status",
        "failure_reasons",
        "/v1/models",
    ):
        assert marker in text, f"probe missing section: {marker}"


def test_probe_script_does_not_invoke_f2_execution():
    text = PROBE.read_text(encoding="utf-8").lower()
    for forbidden in ("f2_arm_worker", "f2_arm_orchestrator", "f2arm", "run_experiment"):
        assert forbidden not in text, f"probe must not reference {forbidden}"

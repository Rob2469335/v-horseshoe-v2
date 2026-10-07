"""Schema + validation for the F2 guest infrastructure probe result.

Pure and deterministic so it is unit-testable without a VM. The guest probe
(``qwen_train/f2_guest_probe.ps1``) emits this JSON; this module validates it and
canonicalises it. No secrets, no arbitrary model responses.
"""

from __future__ import annotations

import json

SCHEMA_VERSION = "f2-guest-probe/1"

REQUIRED_KEYS = (
    "schema_version",
    "probe_started_utc",
    "host_reference_utc",
    "clock_reference_src",
    "clock_skew_seconds",
    "clock_status",
    "clock_reason",
    "adapter_inventory",
    "adapter_count",
    "route_inventory",
    "dns_inventory",
    "authorized_flow",
    "denied_flows",
    "arbitrary_ipv4",
    "dns_behavior",
    "ipv4",
    "ipv6",
    "icmp",
    "input_disk_write",
    "internet",
    "required_checks_total",
    "required_checks_passed",
    "required_checks_failed",
    "required_checks_not_established",
    "overall_status",
    "failure_reasons",
)

ALLOWED_OVERALL = frozenset({"PASS", "FAIL", "NOT_READY"})
ALLOWED_CLOCK = frozenset({"OK", "FAIL"})
ALLOWED_CLOCK_REASONS = frozenset(
    {
        "",
        "GUEST_CLOCK_OUTSIDE_ALLOWED_WINDOW",
        "HOST_REFERENCE_MISSING",
        "HOST_REFERENCE_MALFORMED",
        "GUEST_TIMESTAMP_MALFORMED",
    }
)


def validate(result: object) -> list[str]:
    """Return a list of schema errors (empty == valid). Never raises."""
    errors: list[str] = []
    if not isinstance(result, dict):
        return ["result is not a JSON object"]
    for key in REQUIRED_KEYS:
        if key not in result:
            errors.append(f"missing key: {key}")
    if result.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"schema_version must be {SCHEMA_VERSION!r}")
    if result.get("overall_status") not in ALLOWED_OVERALL:
        errors.append("overall_status must be PASS|FAIL|PARTIAL")
    if result.get("clock_status") not in ALLOWED_CLOCK:
        errors.append("clock_status must be OK|FAIL")
    reasons = result.get("failure_reasons")
    if not isinstance(reasons, list) or any(not isinstance(r, str) for r in reasons):
        errors.append("failure_reasons must be a list of strings")

    counts = {}
    for key in (
        "required_checks_total",
        "required_checks_passed",
        "required_checks_failed",
        "required_checks_not_established",
    ):
        value = result.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            errors.append(f"{key} must be a non-negative int")
        else:
            counts[key] = value
    if len(counts) == 4:
        total = counts["required_checks_total"]
        passed = counts["required_checks_passed"]
        failed = counts["required_checks_failed"]
        not_est = counts["required_checks_not_established"]
        if passed + failed + not_est != total:
            errors.append("required check counts are inconsistent")
        if result.get("overall_status") == "PASS" and (failed > 0 or not_est > 0 or total == 0):
            errors.append("overall_status PASS is invalid with failed/not_established checks")
    return errors


def canonical(result: dict) -> str:
    """Deterministic serialization (sorted keys, compact separators)."""
    return json.dumps(result, sort_keys=True, separators=(",", ":"))

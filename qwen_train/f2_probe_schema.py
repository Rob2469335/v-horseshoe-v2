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
    "clock_skew_seconds",
    "clock_status",
    "adapter_inventory",
    "route_inventory",
    "dns_inventory",
    "authorized_flow",
    "denied_flows",
    "ipv4",
    "ipv6",
    "icmp",
    "internet",
    "overall_status",
    "failure_reasons",
)

ALLOWED_OVERALL = frozenset({"PASS", "FAIL", "PARTIAL"})
ALLOWED_CLOCK = frozenset({"OK", "FAIL"})
ALLOWED_CLOCK_REASONS = frozenset(
    {
        "",
        "GUEST_CLOCK_OUTSIDE_ALLOWED_WINDOW",
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
    return errors


def canonical(result: dict) -> str:
    """Deterministic serialization (sorted keys, compact separators)."""
    return json.dumps(result, sort_keys=True, separators=(",", ":"))

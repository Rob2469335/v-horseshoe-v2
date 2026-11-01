"""Build the ``f2_enforcement_capture_v1`` artifact from a live Hyper-V ACL.

Why this exists
---------------
:func:`qwen_train.f2_isolation.verify_isolation_attestation` will accept an
enforcement-backed denial only when the HOST hands it the enforcement capture
itself and that capture hashes to the digest the attestation *claimed*.  The
guest cannot produce the capture (it cannot read the host's Hyper-V state), so
some host-side component must read the adapter's extended ACL and normalise it.

This is that component.  It is deliberately tiny and side-effect free: it runs
one read-only PowerShell command, normalises the read-back into the capture
shape, and writes it.  It imports the schema constants from
:mod:`qwen_train.f2_isolation` so the two can never drift apart -- a capture
emitted with a different schema string is refused at parse time, and that
refusal is the point.

The coverage the verifier derives from this capture is re-derived, never
trusted: a rule's label is not evidence, the rule's *weight ordering* is.
"""
from __future__ import annotations

import argparse
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from qwen_train.f2_isolation import (
    ENFORCEMENT_CAPTURE_SCHEMA,
    canonical_enforcement_digest,
    parse_enforcement_capture,
)

__all__ = [
    "ENFORCEMENT_KIND",
    "read_live_acl",
    "build_capture",
    "write_capture",
    "main",
]

#: The only capture kind this repository can derive coverage from.
ENFORCEMENT_KIND = "hyper-v-extended-acl"

#: PowerShell enumerations as ``Get-VMNetworkAdapterExtendedAcl`` reports them
#: through ``ConvertTo-Json`` (Int32) and by name.
_DIRECTION = {1: "inbound", 2: "outbound", "Inbound": "inbound", "Outbound": "outbound"}
_ACTION = {1: "allow", 2: "deny", "Allow": "allow", "Deny": "deny"}

_READ = r"""
$a = Get-VMNetworkAdapterExtendedAcl -VMName '{vm}'
$ad = Get-VMNetworkAdapter -VMName '{vm}' | Select-Object -First 1
$vm = Get-VM -Name '{vm}'
$rules = @($a | ForEach-Object {{
    [ordered]@{{
        direction = [string]$_.Direction
        action    = [string]$_.Action
        remote_ip = [string]$_.RemoteIPAddress
        local_ip  = [string]$_.LocalIPAddress
        protocol  = [string]$_.Protocol
        weight    = [int]$_.Weight
        stateful  = [bool]$_.Stateful
    }}
}})
[ordered]@{{
    vm_name = [string]$vm.Name
    vm_id   = [string]$vm.Id
    adapter = [string]$ad.Name
    mac     = [string]$ad.MacAddress
    switch  = [string]$ad.SwitchName
    rules   = $rules
}} | ConvertTo-Json -Depth 6 -Compress
"""


def read_live_acl(vm_name: str, *, pwsh: str = "pwsh") -> dict[str, Any]:
    """Read one VM adapter's extended ACL and identity. Read-only."""
    script = _READ.format(vm=vm_name.replace("'", "''"))
    proc = subprocess.run(
        [pwsh, "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, text=True, timeout=120,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ACL read failed: {proc.stderr.strip()}")
    return json.loads(proc.stdout)


def build_capture(raw: Mapping[str, Any], *, captured_at: str = "") -> dict[str, Any]:
    """Normalise a live ACL read-back into an ``f2_enforcement_capture_v1``."""
    rules = []
    for rule in raw["rules"]:
        rules.append({
            "direction": _DIRECTION.get(rule["direction"], str(rule["direction"]).lower()),
            "action": _ACTION.get(rule["action"], str(rule["action"]).lower()),
            "remote_ip": str(rule["remote_ip"]),
            "local_ip": str(rule["local_ip"]),
            "protocol": str(rule["protocol"]).lower(),
            "weight": int(rule["weight"]),
            "stateful": bool(rule["stateful"]),
        })
    capture = {
        "schema": ENFORCEMENT_CAPTURE_SCHEMA,
        "kind": ENFORCEMENT_KIND,
        "subject": {
            "vm_name": str(raw["vm_name"]),
            "vm_id": str(raw["vm_id"]),
            "adapter_mac": str(raw["mac"]),
            "adapter": str(raw["adapter"]),
            "switch": str(raw["switch"]),
        },
        "captured_at": captured_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "rules": rules,
    }
    # Fail closed here rather than at verification: a capture this module emits
    # must always be one the verifier can parse.
    parse_enforcement_capture(capture)
    return capture


def write_capture(capture: Mapping[str, Any], out_path: Path | str) -> str:
    """Write the capture and return its canonical digest."""
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(capture, indent=1, sort_keys=True), encoding="utf-8")
    return canonical_enforcement_digest(capture)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Capture a VM adapter's extended ACL")
    ap.add_argument("--vm", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)

    capture = build_capture(read_live_acl(args.vm))
    digest = write_capture(capture, args.out)
    print(json.dumps({
        "out": str(Path(args.out).resolve()),
        "digest": digest,
        "rules": len(capture["rules"]),
        "subject": capture["subject"],
    }, indent=1))
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())

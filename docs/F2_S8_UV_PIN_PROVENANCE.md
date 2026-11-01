# F2/S8 approved `uv` pin — provenance record

**Status:** the pin below is the value the code enforces. It has **not** been
independently verified against the executable file, because the builder tools
directory (`C:\f2_pilot\tools\`) is not readable from an agent context.
`NOT ESTABLISHED` until an administrator hashes the real binary.

## The pin

| Field | Value |
|---|---|
| Constant | `qwen_train.f2_s8_pilot_build.UV_APPROVED_SHA256` |
| SHA-256 | `81ea623608f8c17720c62efd0a380a8ead380c4d365ba6968e9d279a2319c674` |
| Tool | `uv` 0.13.6 |
| Expected path | `C:\f2_pilot\tools\uv.exe` |
| Introduced by | commit `ee18dcac` — `SERVICE(f2-s8): upgrade the pinned uv to 0.13.6 (lock revision 2/3 support)` |

## Known discrepancy

The v8 owner packet text states `92fcafc6…da3a`, which is the pin for the older
pinned `uv` release, not 0.13.6. The code has not been changed to match the
packet, and the packet has not been changed to match the code: which value the
owner intends is an owner/administrator decision. No agent may invent or alter
the approved digest.

## Required evidence before a live pilot

An administrator, from a non-agent shell with read access to the tools
directory, must run:

```powershell
Get-FileHash C:\f2_pilot\tools\uv.exe -Algorithm SHA256
```

The result must equal `81ea6236…c674` (or the owner must state a different
approved value and it must be changed in code, this document and the packet in
one reviewed change). Until then, the `uv` identity gate (G10 in the readiness
gate list) is **NOT ESTABLISHED**.

## What the automated check covers

`tests/test_f2_s8_uv_pin_provenance.py` asserts that:

* the constant is a lowercase 64-hex digest;
* the digest recorded in this document equals the constant, so the two cannot
  drift silently.

It does **not** and cannot assert anything about the real executable.

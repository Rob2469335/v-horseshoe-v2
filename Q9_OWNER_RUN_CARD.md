# Q9 Owner-Console Run Card (VM model, `enforcement_scope=vm`) — PREPARED, NOT EXECUTED

Nothing below has been run. VM and gateway are **Off/stopped** by design. This card
is the smallest safe, reproducible handoff for the owner-controlled console step.
Option A only (existing `F2-Isolation-VM` + VMConnect + owner-held guest credential).
No trust root, no CONF-001, no Q10/Q12/Q13, no commit/push.

## 0. Verified live baseline (this session)
| Item | Value |
|---|---|
| Git | `2aad3d1d` == origin/master, 0/0, clean |
| VM `F2-Isolation-VM` | **Off**; Gen2; 1 NIC on `F2-Internal-Switch`; disk `C:\Users\rober\F2_VM\F2-Isolation-VM-os.vhdx` |
| Boundary enforcement (already applied) | topology: Internal switch, no uplink, **no NAT for 10.72.x**, IPv4 Forwarding **Disabled**; host firewall `F2-Q9-GW-ALLOW` (Inbound/Allow/TCP/10.72.0.2→:8099/F2-vEthernet); default-inbound Block denies all other guest→host |
| Firewall baseline (rollback) | `C:\Users\rober\F2_EVIDENCE\q9\firewall_baseline_20261012_002521.wfw` (sha256 `67DBCA81…99F7`) |
| Host | `ROB3`; local arm account `F2Arm` exists ("Experiment J F2 arm execution identity (Q9)") |
| Attestation tool deps | `qwen_train/f2_isolation.py` is **stdlib-only** (hashlib/json/socket/time) → needs only Python + the `qwen_train` package in-guest |
| Gateway launcher | `qwen_train/f2_model_gateway.py` (FastAPI/uvicorn); binds `10.72.0.1:8099`; allows only client `10.72.0.2`; forwards to `F2_MODEL_UPSTREAM` (default `http://127.0.0.1:8080`) |

## 1. Prerequisites — establish BEFORE the console step

### P1 · Host model gateway + upstream (positive control) — I can start these on your go
The guest probe's `authorized_flow` **PASS** and its **clock** reference need
`GET /v1/models` on `10.72.0.1:8099` to return **200 + Date**. The gateway
forwards `/v1/models` to its upstream, so **both** must be up:
- **Upstream** llama.cpp on `127.0.0.1:8080` (start via `start-dev.ps1` or your llama.cpp launcher). Health: `GET http://127.0.0.1:8080/v1/models` → 200.
- **Gateway**:
  ```powershell
  $env:F2_GATEWAY_BIND_HOST='10.72.0.1'; $env:F2_GATEWAY_BIND_PORT='8099'
  $env:F2_GATEWAY_ALLOWED_CLIENT='10.72.0.2'; $env:F2_MODEL_UPSTREAM='http://127.0.0.1:8080'
  .venv\Scripts\python.exe -m qwen_train.f2_model_gateway
  ```
  Health: `GET http://10.72.0.1:8099/v1/models` → 200 from host.
  Restriction: gateway 403s any client whose source IP ≠ `10.72.0.2` (the guest) — that is how it is bound to the intended guest. Shutdown: `Ctrl+C` (or `Stop-Process -Name python` for that PID).

### P2 · In-guest tooling (`NOT ESTABLISHED` — the real gap)
The attestation must run **inside the guest**. The guest (fresh OOBE Windows) has **no Python and no repo**, and there is **no prepared read-only tooling VHDX** (`NOT ESTABLISHED`: the only VHDXs are OS disks `s8-golden-os.vhdx` / `F2-S8-VM1-seed.vhdx` / `F2-Isolation-VM-os.vhdx` and 12 MB pytest scratch disks — none is a tooling-delivery disk). Condition 3 forbids me from downloading tooling, enabling integration services, or injecting a new channel.
- **`f2_guest_probe.ps1`** = pure PowerShell 5.1 → only the `.ps1` file is needed in-guest.
- **`python -m qwen_train.f2_isolation`** → needs **Python 3 + the `qwen_train` package** (`__init__.py` + `f2_isolation.py`) in-guest.
- **Owner decision needed:** how the tooling reaches the guest — (a) you copy the two files + a portable Python in-guest via the console/clipboard, or (b) you authorize me to build a **read-only input VHDX** manifest (still option A; you attach it). I will not do (b) without your say-so.

### P3 · Negative-control risk (genuine — read before running)
`f2_isolation._negative_control` probes `127.0.0.1:1` and the verifier requires it **PERMITTED/connected** (tests encode `("tcp","127.0.0.1",1): PERMITTED`). In a real guest **nothing listens on :1** → the connect is *refused* → classified **DENIED** → attestation reports `negative_control unproven` → **Q9 fails**. To make the control genuinely reachable, the guest must have a loopback listener on `127.0.0.1:1` (or the control must be pointed at a port that is listening). **I did not change `f2_isolation.py`** (that is a code change / not authorized here). Flag this now so a failure is diagnosed correctly, not misread as an egress problem.

### P4 · Identifiers you must supply (I will NOT invent)
`--arm-id`, `--rollout-id`, `--workspace`, `--executing-user`, `--interpreter-path`, `--interpreter-sha256` are **owner-designated**. For a pure Q9 qualification pick a qualification id (e.g. `arm-id=q9-qual-2026-10-12`); `executing-user` = the guest account you log in as; `interpreter-path/sha256` = the in-guest `python.exe` and its hash (`Get-FileHash -Algorithm SHA256 <python.exe>`).

## 2. What you do at the VMConnect console (owner-authenticated)
1. Tell me to **start upstream + gateway + VM** (I do P1 + `Start-VM F2-Isolation-VM` and leave VMConnect open).
2. At VMConnect, **authenticate with the guest credential**.
3. Ensure the tooling is in-guest per P2, and (per P3) a loopback listener exists on `127.0.0.1:1` (e.g. a tiny listener) so the negative control is genuinely reachable.

## 3. Exact in-guest commands
Output to the guest's `C:\` (you will hand me the files; guest has no repo share).
```powershell
# (a) infrastructure probe (PowerShell 5.1)
powershell -NoProfile -ExecutionPolicy Bypass -File .\f2_guest_probe.ps1 `
  -GatewayIp 10.72.0.1 -GatewayPort 8099 -OutputPath C:\f2_probe.json
#    (add -InputDisk <letter>: only if a read-only input VHDX is attached; the
#     write-probe must FAIL to prove read-only; omit it if none is attached)

# (b) attestation (Python; <PY> = in-guest python.exe; run from the qwen_train parent dir)
<PY> -m qwen_train.f2_isolation `
  --out C:\f2_q9_attestation.json `
  --arm-id <YOU> --rollout-id <YOU> --workspace <YOU> `
  --enforcement-scope vm --executing-user <YOU> `
  --interpreter-path <YOU> --interpreter-sha256 <YOU> `
  --required-service gateway=10.72.0.1:8099 `
  --external api.github.com:443 --external 93.184.216.34:80
```
Expected guest-probe `overall_status`: **PASS** (clock OK, one_nic, no_default_route, authorized_flow 200, denied_ports none-connect, dns). Expected attestation: external dims **denied**, loopback + `gateway` **permitted**, negative control **permitted** (see P3), binding set, dns declared.

## 4. Returning evidence for independent verification
Copy out (read-only hand-back): `C:\f2_probe.json` and `C:\f2_q9_attestation.json`. I will independently verify with the authoritative verifier:
```powershell
.venv\Scripts\python.exe -c "import json,sys; from qwen_train import f2_isolation as i; a=i.parse_attestation(json.load(open(r'<path>'))); v=i.verify_isolation_attestation(a); print(json.dumps(v.to_dict(),indent=2,sort_keys=True))"
```
I will confirm: binds to your arm/rollout/workspace/executing-user/interpreter/policy; external egress denied; loopback + gateway + negative control behave; **no mandatory dimension missing/inconclusive**. Q9 is **NOT** satisfied unless this verifier returns satisfied with `unproven`/`failed` empty.

## 5. Cleanup & rollback
- Stop guest tools; `Stop-VM -Name F2-Isolation-VM -Force`.
- Stop gateway (`Ctrl+C`) and upstream llama.cpp.
- Detach any input VHDX (`Remove-VMHardDiskDrive`) if one was attached.
- Firewall: leave `F2-Q9-GW-ALLOW` (it is the enforcement) **or** full rollback:
  `netsh advfirewall import "C:\Users\rober\F2_EVIDENCE\q9\firewall_baseline_20261012_002521.wfw"`
  (or `Remove-NetFirewallRule -Name F2-Q9-GW-ALLOW`).
- Nothing committed/pushed; operator work preserved.

## 6. What I will do the moment you give the go
Start upstream+gateway+VM (P1), leave VMConnect open, wait for your console step, then
independently verify the returned attestation + rerun preflight and report which
findings genuinely cleared (`host.egress_enforcement`, `evidence.q9_attestation`) vs.
remain blocked (S8 base/gold, trust root, CONF-001, env.*). Then proceed to genuine S8
evidence in the attested guest and measure admitted/analyzable/completed distinctly.

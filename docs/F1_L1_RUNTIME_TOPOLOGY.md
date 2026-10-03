# F1 / L1 Runtime Topology (Experiment J)

Derived by tracing the actual execution path, not by assuming `start-dev.ps1`
matches what the harness needs. Recorded 2026-10-02.

## The decisive constraint: the harness owns :8000

`qwen_train/run_repair_task.py:769-787` **FAILS CLOSED if port 8000 is already
listening** under a PID that is not F1-owned, and records the observation as
`infra_invalid_reason="process_identity_unknown"` rather than killing the
occupant (`:764-768` — the harness has no authority to terminate a process it
does not own).

It then starts its own backend via `f1_infra.start_backend_fresh(...,
workspace_root=str(repo), port=8000)` (`:790-795`).

**Consequence: `start-dev.ps1` MUST NOT be used for an F1/L1 run.** It starts the
dev backend on :8000, which the harness would abort on. Its STEP 1 and shutdown
kill loops are also commented out (`start-dev.ps1:84`, `:332`), so it also cannot
be relied on for scoped shutdown.

## Port map (traced, not assumed)

| Service | Port | Who starts it | Required for F1/L1? |
|---|---|---|---|
| Generation model `robs4b` | **8079** | `model_router.py:202` sets `LLAMA_PORT=8079` | **YES** |
| Model router | **8080** | `start-proxy.ps1` (`model_router.py`) | **YES** — the model seam the coder calls |
| Embedding | 8081 | `start-dev.ps1` STEP 2 | **YES** — `EmbeddingService` backs the governed lesson/vector path |
| Qdrant | **6333** | `start-dev.ps1` STEP 3 (`:199-214`) — `QDRANT__SERVICE__HOST=127.0.0.1` + `QDRANT__STORAGE__STORAGE_PATH` | **YES** — fail-closed prerequisite recorded in the state table |
| Backend | 8000 | **the harness** (`f1_infra`) | harness-managed; must be FREE beforehand |
| Reranker | 8082 | `start-dev.ps1` STEP 2 | not on the F1 path |
| Vision | 8083 | `start-dev.ps1` STEP 2 | not on the F1 path |
| Small model | 8084 | `start-dev.ps1` STEP 2 | not on the F1 path |
| Router status | 8095 | `SWARM_ROUTER_STATUS_PORT` (`model_router.py:114`) | optional; gate uses it only if `router_port` is passed |
| Console | 5173 | `start-dev.ps1` STEP 5 | **NO** — never required |

`f1_infra`'s own health gate (`BackendHealthGate.__init__`, `:393-397`) probes
**only** the backend `/health` on `:8000` (`:715-716`) plus an **optional**
`router_port`. It does **not** check 8079, 8081, 8082, 8083, 8084 or 6333 — those
must be verified separately before the run.

## Qdrant storage root (canonical — operator-authorized 2026-10-03)

**Canonical production root: `C:\Users\rober\Projects\v-horseshoe-v2\storage\`**

This became authoritative by explicit operator decision on 2026-10-03. It was
**not** previously authoritative. Evidence used for the decision, all verified
in the read-only audit of 2026-10-03:

- **Effective production use** — 15 collections, 5,534 MB, last write
  2026-10-02 22:05:27; the only populated Qdrant root in the repository.
- **Exact snapshot correspondence** — `snapshots\` holds 15 collection stub
  directories whose names match `storage\collections\` 1:1, plus
  `full-snapshot-2026-09-21-01-46-10.snapshot` (5,770,140,672 B) with its
  checksum.
- **Launcher history** — `2266faaa` (2026-06-07) created `start-dev.ps1` with
  `Set-Location` on line 1, an explicit absolute `storage_path` and
  `--config-path`. `2c47b2fb` (2026-06-20) rewrote the launcher and removed all
  three, after which Qdrant fell back to its built-in default.
- **Live proof of the hazard** — a rehearsal on 2026-10-03 00:45:23 started
  Qdrant with `QDRANT__STORAGE__STORAGE_PATH=./qdrant_local` and no config file
  loaded (server log: `Config file not found: config/config` ×2), creating an
  empty `qdrant_local\`. That directory is retained as defect evidence; it is
  not production data.

### Why determinism required an explicit pin

Qdrant's built-in default `storage_path` is `./storage`, resolved against the
**child process's** working directory. `Start-Process` without
`-WorkingDirectory` inherits the caller's CWD, and neither launcher changes its
own CWD — every `Set-Location` in both scripts sits inside a `Start-Job`
ScriptBlock, which does not affect the parent. The effective store therefore
followed whoever invoked the script.

The fix pins the absolute canonical root immediately before the Qdrant
`Start-Process`, in **both** launchers:

```
$env:QDRANT__SERVICE__HOST = "127.0.0.1"          # 882646a security fix, preserved
$env:QDRANT__STORAGE__STORAGE_PATH = Join-Path $root "storage"
Start-Process $qdrantPath -WindowStyle Hidden
```

`QDRANT__*` environment variables outrank both Qdrant's defaults and any config
file, so this single assignment is authoritative over `.qdrant\config\qdrant.yaml`
and over `docker-compose.yml`. The assignment is process-scoped to the launcher
session; nothing is persisted to User or Machine environment scope.

`-WorkingDirectory $root` was **not** added: it would also relocate Qdrant's
default `snapshots_path`, and `EXPERIMENT_J.md` §15/§258 states snapshot/restore
is not a T/X requirement, so the extra movement is unnecessary change.

### Root inventory and dispositions

| Path | State | Disposition |
|---|---|---|
| `storage\` | 15 collections, populated | **canonical** |
| `snapshots\` | full snapshot + 15 stubs | protected; the backup of the canonical root |
| `qdrant_local\` | 2 files, empty | **retained as defect provenance** — not production data |
| `.qdrant\storage\` | 0 files; Qdrant never booted against it | empty; not the root |
| `.qdrant\config\qdrant.yaml` | reconciled to the canonical root + `127.0.0.1` | reference config for manual `--config-path` use only; **not** loaded by any launcher |
| `docker-compose.yml` | remapped `./storage:/qdrant/storage` | development/secondary only; **not** the Experiment J launcher. Qdrant locks its storage dir, so native and Compose must never run together |
| `.github/workflows/ci.yml` | `qdrant/qdrant:v1.16.3`, no volume | intentionally ephemeral; must never mount the production store |

### Experiment J requirement (added 2026-10-03)

Experiment J uses **the canonical Qdrant instance** at
`C:\Users\rober\Projects\v-horseshoe-v2\storage\`. The storage root is
deterministic; the caller's CWD must not change the store. The existing C0
condition (`ActiveLessons` empty, `EXPERIMENT_J.md` §17) applies **to the
canonical store** — a pre-run check must confirm Qdrant is attached to that
store, because `ActiveLessons` being *absent* is also true of an empty or wrong
root and therefore cannot by itself establish identity.

## Background loops that MUST stay down

With `SWARM_AUTONOMY=0` (exported by `run_repair_task.py:349` and
`f1_infra.py:601`), these do not start:

- autonomous watch-loop — gated `main.py:388`
- ASPO reflection daemon — gated `main.py` (this change); previously unconditional
- genetic mutation daemon — gated `SWARM_GENETIC_MUTATION` (`main.py:290`)
- eval tick — gated `SWARM_EVAL_TICK`

MemoryBridge daemons (`main.py:230-232`, 5 s / 300 s) start only when the
orchestrator exposes a `bridge`, and run with memory injection disabled.

## Minimum startup order

1. Qdrant → poll `http://127.0.0.1:6333` until 200, **and** confirm the instance
   is attached to the canonical storage root
   `C:\Users\rober\Projects\v-horseshoe-v2\storage\` (see above — a 200 alone
   does not establish store identity)
2. Embedding (8081) → poll `/health`
3. Generation model + router → `start-proxy.ps1`; poll `http://127.0.0.1:8080/v1/models`
   and `http://127.0.0.1:8079/health`
4. Verify **8000 is FREE**, then run the harness (it starts and stops its own backend)

## Shutdown

Target only PIDs recorded at launch. Both `start-dev.ps1` kill loops are commented
out, so a scoped, recorded-PID shutdown is required; never a host-wide
process-name kill.
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
| Qdrant | **6333** | `start-dev.ps1:199-205` (`QDRANT__SERVICE__HOST=127.0.0.1`) | **YES** — fail-closed prerequisite recorded in the state table |
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

1. Qdrant → poll `http://127.0.0.1:6333` until 200
2. Embedding (8081) → poll `/health`
3. Generation model + router → `start-proxy.ps1`; poll `http://127.0.0.1:8080/v1/models`
   and `http://127.0.0.1:8079/health`
4. Verify **8000 is FREE**, then run the harness (it starts and stops its own backend)

## Shutdown

Target only PIDs recorded at launch. Both `start-dev.ps1` kill loops are commented
out, so a scoped, recorded-PID shutdown is required; never a host-wide
process-name kill.
# RunPod Operational Runbook

This runbook covers the operational procedures for deploying, verifying, and
auditing the swarm on a RunPod GPU pod. It complements `AGENTS.md` by
focusing on **operational execution** rather than architecture.

---

## Prerequisites & Environment

* **Local**: Windows with WSL2 / PowerShell 7, `git`, `docker` (optional), `python 3.14+`, `huggingface-cli`, `runpodctl`.
* **Remote**: RunPod Community/SECURE cloud, RTX 3070+ or A100/H100 for training.
* **Credentials** (stored in `.env`, gitignored):
  - `OPENROUTER_API_KEY` / `OPENROUTER_BASE_URL`
  - `NVIDIA_API_KEY`
  - `DEEPSEEK_API_KEY`
  - `SWARM_HARNESS_KEY` (for task/rollout identity)
  - `HF_TOKEN` (for private model upload)
  - `SWARM_API_TOKEN` (optional, protects local API)
* **Local model**: `qwen_train/robs4b_q4km.gguf` (SHA256 `65202F372110DDE854B40CE15DCD1B6AB56A1FE9EA542B84B6A9CC745B242D41`, 2.71 GB).

---

## Trusted Harness Identity

All agent-loop evidence is tagged by **harness-supplied identity** — never by
the model. Three headers, two verified against `SWARM_HARNESS_KEY`:

| Header | ContextVar | Purpose |
|---|---|---|
| `x-swarm-task-id` | `TASK_ID_CTX` | Task diversity gate (`MIN_EVIDENCE_TASKS=2`) |
| `x-swarm-rollout-id` | `ROLLOUT_ID_CTX` | Evidence dedup key (`MIN_EVIDENCE_RUNS=3`) |
| `x-swarm-eval-id` | `EVAL_ID_CTX` | Evaluation isolation |
| `x-swarm-harness-key` | — | Credential gate (must match `SWARM_HARNESS_KEY`) |

* The worker **cannot forge** `task_id` or `rollout_id` without the
  `SWARM_HARNESS_KEY` (the `x-swarm-harness-key` header is verified against
  `os.environ["SWARM_HARNESS_KEY"]`).
* `x-swarm-eval-id` is **not** credential-gated (used for evaluation
  isolation only).
* Missing/invalid credential → identity = `None` → treated as untagged
  → rejected by Option A guard (`ignored: untagged_event`).

---

## Evidence Identity Model

| Layer | Header | ContextVar | Purpose |
|---|---|---|---|
| **task_id** | `x-swarm-task-id` | `TASK_ID_CTX` | Task diversity gate (`MIN_EVIDENCE_TASKS=2`) |
| **rollout_id** | `x-swarm-rollout-id` | `ROLLOUT_ID_CTX` | Evidence dedup (`MIN_EVIDENCE_RUNS=3`) |
| **run_id** | (generated) | — | Legacy fallback / event identity |

**Evidence flow**

```text
event
  ↓
task_id gate (reject if empty → "ignored: untagged_event")
  ↓
rollout_id / run_id evidence deduplication
  ↓
MIN_EVIDENCE_RUNS (3 distinct rollouts when rollout_id present)
  AND
MIN_EVIDENCE_TASKS (2 distinct task_ids)
  ↓
promotion
```

* **One rollout = at most one evidence run.** Multiple failures with the
  same `rollout_id` collapse to a single evidence run.
* Legacy/local events without `rollout_id` fall back to `run_id` dedup.
* `watch-loop` (`source="watch-loop"`) is an explicit source-specific path.
  It mints its own random `run_id`, carries no `task_id`, and is audited
  as `WATCH_LOOP_EVENT`. It **does not** create evidence runs and does
  **not** count toward `MIN_EVIDENCE_RUNS`.
* Untagged events (`task_id=""`) are rejected before candidate creation:
  `ignored: untagged_event`. This includes `reflection_loop`, `control`,
  and any other untagged callers.

---

## Promotion Requirements

A candidate reaches `CANDIDATE` state only when **both** gates pass:

| Gate | Threshold | Evidence |
|---|---|---|
| `MIN_EVIDENCE_RUNS` | 3 | Distinct `rollout_id` (or `run_id` fallback) |
| `MIN_EVIDENCE_TASKS` | 2 | Distinct `task_id` in `evidence_tasks` |

**Diversity floors** (enforced at promotion gate, `evolution_daemon.py`):

| Tool | Floor |
|---|---|
| `filesystem` | `>= 0.50` |
| `web_search` | `>= 0.40` |
| `web_fetch` | `>= 0.40` |

A candidate failing either gate receives `rejected: insufficient_task_diversity`
or `insufficient_evidence` at promotion time.

---

## Governance / Security Checks

* **Router pin**: `SWARM_ROUTER_PINNED=1` must be set before any arm. Verified
  via `/status` endpoint (`pinned: true`) and `model_router.py` logic.
  If unpinned, router can spawn local `llama.exe` → wrong model.
* **Sandbox**: `SWARM_WRITE_ROOT` is the configured write boundary for
  filesystem operations that pass through the sandbox/security layer.
* **Autonomy ceiling**: `autonomy_policy.json` (v2) at repo root is the
  single machine-readable ceiling. Repair allowed only in
  `swarm_os/`, `runtime_v2/`, `organism_console/`. Self-modify blocked
  on dependency-aware import graph. Evolution staged, human-approved.
  Rollback is signal-gated (canary + traceback), diff-scoped.
* **Secrets**: Never printed. `SWARM_API_TOKEN` optional; if set, all
  `/api/*` require `Authorization: Bearer <token>` (except `/health`,
  `/readyz`, `/docs`).
* **RunPod**: `SWARM_API_TOKEN` set in pod env; `ssh.runpod.io` requires
  `-tt` for PTY; direct IP:port preferred for SCP/rsync.

---

## Rollback Procedure

1. **Automatic** (watch-loop canary): Signal-1 regression → automatic
   diff-scoped restore of pre-repair snapshot (`restore_run_snapshot`).
2. **Manual**: `POST /api/control/mutations/rollback` with `mutation_id`
   (content-based: compares current bytes to approval-time bytes; refuses
   on conflict).
3. **Full pod reset**: `pod-action terminate` → new pod from template.
   Volume disk (`mounts.persistent`) survives stop/restart; only
   `terminate` wipes it.

---

## Test Gates (must pass before any pod promotion)

| Gate | Command | Must Pass |
|---|---|---|
| Lint | `ruff check . --select E9,F` | 0 errors |
| Type/unit | `pytest -x -q` | All pass |
| Exit-site scan test | `pytest tests/test_process_failure_labels.py -q` | 4 passed |
| Evidence gating | `pytest tests/test_prompt_repairer.py -q` | 60 passed |
| Regressions | `pytest tests/test_autonomous_loop_bugs.py -q` | Known P5 fail OK; all others pass |
| Full suite | `pytest -q` (no `-x`) | All pass |

**No rollout until full suite green.**

---

## P5 Baseline Failure (known, not fixed)

* `test_verification_failure_records_reflexion` fails identically at
  `59f1e632` and HEAD (`store_reflexion` awaited 0 times).
* **Not fixed**, not `xfail` — recorded as pre-existing baseline.
* Does not block deployment; tracked separately.

---

## 25-Rollout Measurement (only after gates)

* Prerequisites: governance gate passed, full suite green, 3090 pod
  verified, GPU check passes, model on volume disk, `sha256sum` verified.
* Sequence: 25 sequential arms → log each result → compute composite
  fitness → compare to baseline → decide promotion.
* Do NOT run until **all prior gates green**.

---

## Stop Conditions

Stop the pod immediately if any of these trigger:

1. `torch.cuda.is_available()` is `False` on pod start.
2. `/readyz` returns `false` or times out.
3. `torch.cuda.is_available()` becomes `False` during run.
4. GPU utilization flatlines at 0% for > 5 min during active generation.
5. `/status` shows `llamacpp_reachable: false`.
6. Evidence model regression (Option A guard regresses).
7. Watch-loop writes unexpected `[AUTO-REPAIR]` lines (unauthorized).
8. Container disk usage > 90% (`df -h /workspace`).

---

## Pod Lifecycle Checklist

| Phase | Action | Verify |
|---|---|---|
| Create | `runpodctl pod create --template runpod-torch-v280 --gpu-id "NVIDIA GeForce RTX 3090" --cloud SECURE --name qwen-worker-3090-tpl --start-ssh` | `df -h /workspace` shows 50G volume; `nvidia-smi -L` shows 3090 |
| Start | `ssh … nvidia-smi -L; python3 -c "import torch; print(torch.cuda.is_available())"` | `True` + correct driver |
| Model | `wget -c -O /workspace/runtime/robs4b_q4km.gguf <url>` + `sha256sum` | `65202F37...B242D41` |
| Server | `nohup llama-server … &` | `/readyz` true, `llama_smi` shows VRAM use |
| Tunnel | `ssh -L 8000:localhost:8000 …` | `curl localhost:8000/status` OK |
| Pin | `export SWARM_ROUTER_PINNED=1` + start `model_router.py` | `/status` shows `pinned: true` |
| Run | `organism` CLI or API | Evidence gates pass, promotion green |

---

## Billing & Cleanup

* **Running pod**: $0.22/hr (RTX 3090 Community) or $0.13/hr (RTX 3070 Community).
* **Stopped pod**: $0.014/hr for 50 GB volume disk.
* **Network volume**: $0.07/GB/mo (independent of pod).
* **Terminate** only after:
  - Model verified & pulled off pod (or on network volume)
  - Support ticket for any failed pod sent (with `diag_cuda_20260920.txt`)
  - All evidence/logs collected
  - `pod-action terminate` (not just stop)

---

## Incident Log (rolling)

| Date | Pod ID | What Happened | Action |
|---|---|---|---|
| 2026-09-20 | 7txonppnq4qxbs / beyzn2r6w1klkt / 8fmf6pb16ci863 | 3070 pods failed CUDA init; 3090 passed | Ticket filed; 3070 pool retired |
| 2026-09-20 | beyzn2r6w1klkt | `[ROLLBACK-COMPLETED]` routes.py signal_1 | Rollback auto-applied; no data loss |

---

*End of runbook. This document is versioned with the codebase; update on
every governance change.*
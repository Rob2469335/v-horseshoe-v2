# ZENITH Swarm OS (v-horseshoe-v2)

A modular swarm orchestration platform for local AI agent workflows — planning, routing, execution, self-healing, and controlled experimentation.

## What it is

ZENITH Swarm OS is a Python 3.14+ system that orchestrates AI agents over local model infrastructure (llama.cpp, LiteLLM, Qdrant). The repository contains:

- **swarm_os/** — core orchestration, API, brain, memory, healing, and control plane
- **runtime_v2/** — async agent runtime with LLM client, tool execution, and F2 replay delivery
- **organism_console/** — CLI interactive shell
- **start-console/** — web/SSR console experiment
- **qwen_train/** — experiment harness, evaluation scripts, and training pipeline tools
- **tests/** — automated test suite (pytest)

The project is not a production service. It is an experimental research platform with automated tests and controlled infrastructure.

## The research question

The primary research question (Experiment J, frozen as F0) is:

> Does delivery and utilization of a genuine, lawfully promoted lesson L change a fresh worker's observable first-relevant-edit behavior compared to the exact same frozen treatment artifact with L removed?

See `docs/EXPERIMENT_J.md` for the full scientific design.

## Repository layout

| Directory | Purpose |
|---|---|
| `swarm_os/` | Core platform: orchestrator, API routes, brain, memory, healing, control plane |
| `runtime_v2/` | Agent runtime: LLM client, tool execution, stream runner, F2 replay delivery |
| `organism_console/` | CLI shell frontend |
| `start-console/` | Web/SSR console experiment |
| `qwen_train/` | Experiment harness, evaluation scripts, F2 adapter and worker |
| `tests/` | Automated tests (pytest) |
| `docs/` | Experiment J authority documents, F2 design and authorization |

## Quick start

Requires Python >= 3.14 and a local model server (llama.cpp on port 8080).

```bash
# Install in development mode
pip install -e .

# Run the test suite
pytest

# Run with linting
ruff check . --select E9,F
pytest
```

The development stack (llama.cpp, Qdrant, Qwen embed/rerank/vision) can be started with `start-dev.ps1`, but this requires pre-configured model weights and `.env` credentials.

## Status

| Item | Status |
|---|---|
| Core platform (Phases 1–6) | Implemented and verified |
| Experiment J F0 (scientific design) | Frozen |
| Experiment J F1 (pilot, 20 observations) | Closed — baseline established |
| Experiment J F2 (governed learning event) | Pending — infrastructure implemented |
| F2 delivery evidence | Implemented (9-field trajectory record) |
| Scientific learning result | **Not yet produced** |

**What exists:** The runtime, tests, delivery-seam enforcement, F2 replay infrastructure, and the governed-learning pathway code (prompt repairer, lesson manager, promotion with HMAC receipts). Historical F1 observations and engineering evidence are preserved.

**What does not yet exist:** A genuine ACTIVE lesson L, a completed governed learning event, or a scientific result from Experiment J F2.

## Current status (detailed)

- Experiment J F1 is **closed**: 20 protocol observations completed (10 valid, 10 infrastructure-invalid); the no-lesson baseline (`k`, `n`, practical-effect criterion) is frozen.
- Experiment J F2 prerequisites are partially satisfied: delivery-seam enforcement, replay propagation, fresh-process harness, and delivery-identity evidence are implemented. The governed promotion machinery is tested (39 tests in `tests/test_learner_artifact_derivation.py`). A genuine ACTIVE lesson L has **not yet been created**; this requires provisioning `SWARM_RECEIPT_KEY` and a healthy Qdrant instance through the real governed learning pathway.
- The F2 authority specifies an execution graph of `P0 → P1 → P2` (separate fresh processes per arm, direct HTTP/SSE delivery, no P3 required for the HTTP path).
- See `docs/LEARNING_EXPERIMENT_STATE.md` for the current engineering and prerequisites state.

## Security

- `.env` files containing API keys and credentials **must not be committed** (enforced by `.gitignore`).
- `SWARM_RECEIPT_KEY` is not provisioned in the repository and must be provided externally for F2 execution.
- No production signing authority is embedded in the codebase.

## License

Internal research project. Not a published package.

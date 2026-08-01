# Horseshoe Swarm v2

Horseshoe Swarm v2 is a modular swarm orchestration platform for local AI workflows, routing, planning, execution, and simulation.

## Overview

The orchestrator coordinates planning, routing, critique, trace collection, model selection, and task execution over a LiteLLM-backed model inventory (OpenRouter, Groq, and local llama.cpp endpoints).

## Quick start

1. Make sure your local llama.cpp / LiteLLM server is running.
2. Confirm your environment API keys (`OPENROUTER_API_KEY`, `GROQ_API_KEY`) or local models are configured in `litellm-config.yaml`.
3. Run your orchestrator smoke checks.

## Smoke tests

```powershell
python -c "from swarm_os.services.orchestrator import Orchestrator; o=Orchestrator(); print({'vision': o.build_route(prompt='read this screenshot', requested_model=None, phenotype={}), 'coding': o.build_route(prompt='fix this python exception', requested_model=None, phenotype={}), 'general': o.build_route(prompt='hello', requested_model=None, phenotype={}), 'embedding': o.build_route(prompt='build embedding vector', requested_model=None, phenotype={}), 'reranker': o.build_route(prompt='rerank these search hits', requested_model=None, phenotype={})})"
```

## Current routing inventory

- `qwen3-vl:8b`
- `qwen3-embedding:8b`
- `qwen3:14b`
- `qwen2.5:14b-instruct-32k`
- `qwen2.5-coder:14b-32k`
- `moondream:latest`
- `mistral-nemo:12b`
- `qwen2.5:3b-instruct`
- `nomic-embed-text:latest`
- `qllama/bge-reranker-v2-m3:latest`

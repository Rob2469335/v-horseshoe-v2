# v-horseshoe-v2 — Swarm OS

A genetic evolution engine for local AI orchestration. Organisms are
Ollama-powered agents whose genomes control how they call Horseshoe Swarm.
Evolution discovers which model, prompt strategy, and MCP tool combinations
solve real tasks best — automatically, without manual tuning.

---

## Prerequisites

Swarm OS calls Horseshoe Swarm on port 11436. **Start Swarm first.**

```powershell
# Terminal 1 — start Horseshoe Swarm
cd C:\Users\rober\.continue\v-horseshoe
python swarm_server.py
```

Then in a second terminal:

```powershell
# Terminal 2 — run the evolution
cd C:\Users\Robert Locust\Projects\v-horseshoe-v2
python -m swarm_os
```

---

## Run

```powershell
# Default run (20 generations, default scenario)
python -m swarm_os

# Resume from latest snapshot after interruption
python -m swarm_os --resume

# Custom run
python -m swarm_os --generations 50 --scenario stress
```

## Test

```powershell
python -m unittest discover -s swarm_os\tests -p "test_*.py"
```

---

## Environment variables

```powershell
$env:SWARM_SCENARIO_NAME  = "stress"
$env:SWARM_POPULATION_MAX = "10"
$env:SWARM_RANDOM_SEED    = "42"
$env:SWARM_GENERATIONS    = "50"
$env:SWARM_LOG_PATH       = "swarm_os/logs/organism_diary.jsonl"
$env:SWARM_URL            = "http://localhost:11436"
python -m swarm_os
```

---

## Settings

Edit `swarm_os\config\settings.py` to change runtime defaults:

| Setting | Default | Description |
|---|---|---|
| `population_max` | 8 | Max organisms (safe for 135U — don't exceed 10) |
| `generations` | 20 | Default run length |
| `scenario_name` | default | Startup scenario |
| `snapshot_every` | 5 | Save snapshot every N generations |
| `swarm_url` | http://localhost:11436 | Horseshoe Swarm endpoint |
| `swarm_timeout` | 60 | Seconds per Swarm call |
| `log_path` | swarm_os/logs/organism_diary.jsonl | JSONL memory log |
| `random_seed` | None | Set for reproducible runs |

---

## Scenarios

| Scenario | Population | Description |
|---|---|---|
| `default` | 6 | Diverse seed organisms — coder, researcher, upwork scanner, generalist, heavy thinker, triage |
| `stress` | 10 | Fully random population, higher mutation rates |

---

## How it works

```
Organism.act(env_state)
    ↓
brain(context)  ← genome controls this call
    ↓
POST http://localhost:11436/v1/chat/completions
    model       = genome.model_tier  → sampled from probability distribution
    temperature = genome.temperature → 0.0–1.2
    tools       = genome.active_tools() → sigmoid probability per tool
    system prompt shaped by CognitivePolicy genes
    ↓
SelectionEngine scores response
    quality × domain_affinity - entropy_penalty - resource_pressure
    ↓
Fittest organisms breed → children inherit + mutate genes
    ↓
Specialists emerge automatically over generations
```

---

## Genome

### Core genes

| Gene | Controls | Range |
|---|---|---|
| `model_tier` | Model probability (3b/7b/14b) | 0.0–1.0 |
| `temperature` | Response creativity | 0.0–1.0 |
| `reasoning_depth` | Chain-of-thought intensity | 0.0–1.0 |
| `verbosity` | Response length | 0.0–1.0 |
| `coding_affinity` | Fitness bonus on coding tasks | 0.0–1.0 |
| `research_affinity` | Fitness bonus on research tasks | 0.0–1.0 |
| `upwork_affinity` | Fitness bonus on Upwork tasks | 0.0–1.0 |
| `context_budget` | Token budget (512–4096) | 0.0–1.0 |
| `retrieval_top_k` | Qdrant recall depth (3–20) | 0.0–1.0 |
| `mutation_rate` | Rate of gene drift | 0.01–0.4 |
| `crossover_stability` | Average vs pick-one on crossover | 0.0–1.0 |

### CognitivePolicy genes (14 fields)

Controls how the system prompt is built — decomposition, reflection,
self-critique, verification, hallucination sensitivity, retry aggression,
summarization, parallel tool calls, memory read/write bias, and more.

### tool_genes (6 fields)

Continuous probability weight per MCP tool. Sigmoid activation determines
whether each tool is included in a given Swarm call.

| Tool | What it does |
|---|---|
| `web_search` | Brave / Tavily / SearXNG via Swarm |
| `playwright` | Browser automation via MCP port 8931 |
| `filesystem` | Local file read/write via Filesystem MCP |
| `context7` | Library docs lookup via Context7 MCP |
| `qdrant_recall` | Long-term memory retrieval |
| `code_exec` | Code block extraction and validation |

---

## What evolves

Given enough generations, selection pressure creates specialists:

| Specialist | Evolved traits |
|---|---|
| **Coder** | Low temperature, high verification, filesystem + code_exec tools |
| **Researcher** | High search_bias, web_search + context7, verbose, reflective |
| **Upwork scanner** | High upwork_affinity, playwright + web_search, terse, fast |
| **Generalist** | Medium everything — survives all domains, outcompeted by specialists |

You never define these roles. Evolution finds them.

---

## File structure

```
swarm_os/
├── kernel/
│   ├── genetics.py        # Genome + CognitivePolicy + crossover + mutate
│   ├── brain.py           # Swarm brain factory — calls port 11436
│   ├── organism.py        # Individual agent — genome + brain + memory diary
│   ├── environment.py     # Task pool — coding / research / upwork
│   ├── selection.py       # Composite fitness scoring + softmax selection
│   ├── swarm_kernel.py    # Evolution loop — async, elitism, fitness decay
│   ├── snapshot.py        # Save/load population (auto-cleans old snapshots)
│   └── migrations.py      # Snapshot version migration (v1→v4)
├── scenarios/
│   ├── default.py         # 6 diverse seed organisms
│   ├── stress.py          # 10 random organisms
│   └── registry.py        # Scenario registry
├── apps/
│   └── simulation_runner.py  # Entry point + --resume + final report
├── lib/
│   └── mcp/
│       └── registry.py    # MCP tool router
└── config/
    └── settings.py        # Runtime settings
```

---

## Snapshots

Snapshots are saved every 5 generations to `swarm_os/data/snapshots/`.
Only the last 10 are kept — older ones are auto-deleted.

After any interruption:

```powershell
python -m swarm_os --resume
```

The latest snapshot is loaded and evolution continues from that generation.

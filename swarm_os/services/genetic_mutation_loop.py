import json
import asyncio
import logging
import shutil
import uuid
from pathlib import Path
from datetime import datetime, timezone
from litellm import acompletion
import re
import textwrap

from swarm_os.services.danger_room import DangerRoom
from swarm_os.services.security_gate import SecurityGateViolation
from swarm_os.memory.memory_bridge import MemoryBridge
from swarm_os.kernel.genetics import ast_slice

logger = logging.getLogger("GeneticEvolution")
if logging.getLogger().handlers == []:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

ROOT_DIR = Path(__file__).parent.parent.parent.resolve()
PENDING_MUTATION_DIR = ROOT_DIR / ".data" / "pending_mutations"
AGENT_SERVICE_PATH = ROOT_DIR / "runtime_v2" / "api" / "agent_service_v2.py"
# Code mutation = complex reasoning — use the cloud DeepSeek V4 Flash default
# (funded, cheap) instead of local qwen3.5-4b which produces weak mutations.
MODEL = "openai/deepseek-v4-flash"

EVOLUTION_PROMPT = """You are the Swarm OS Genetic Architect. 
Your goal is to optimize the core engine function `{target_func}` to make it faster and use less memory based on recent performance logs.

Current core code slice (Program Dependence Graph):
```python
{core_code_slice}
```

Task: Write a fully optimized replacement for the `{target_func}` function that improves real-world performance while preserving correctness and passing compile and test validation.
Output ONLY the complete modified python code enclosed in ```python...``` blocks. DO NOT add any markdown formatting other than the ```python``` block. DO NOT use unescaped characters or write incomplete code. Ensure the code is strictly syntactically valid Python. Do not explain. Just output the code.
"""

# Track diversity for Extinction Events.
# BUG FIX: Persist to disk — without this, mutation_history resets to [] on every
# script invocation, so 3-consecutive-failure Extinction Events can never trigger.
HISTORY_FILE = ROOT_DIR / "logs" / "mutation_history.json"
CONSECUTIVE_FAILURES_FOR_EXTINCTION = 3

async def run_genetic_mutation(target_file_path: str = str(AGENT_SERVICE_PATH), target_func: str = "get_agent"):
    logger.info("Initializing Genetic Evolution Loop...")

    # BUG FIX: Load persistent mutation history from disk
    mutation_history: list[str] = []
    if HISTORY_FILE.exists():
        try:
            mutation_history = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
        except Exception:
            mutation_history = []

    # Keep only the last N entries to avoid unbounded file growth
    mutation_history = mutation_history[-50:]
    
    target_file = Path(target_file_path).resolve()
    with open(target_file, "r", encoding="utf-8") as f:
        core_code = f.read()
    
    # AST Slicing: Only extract the targeted bottleneck rather than truncating randomly
    sliced_code = ast_slice(core_code, target_func)
    
    if not sliced_code:
        raise ValueError(f"FATAL: Target function '{target_func}' not found in source code. Aborting to prevent file explosion.")
    
    prompt = EVOLUTION_PROMPT.format(target_func=target_func, core_code_slice=sliced_code)
    
    memory_bridge = MemoryBridge()
    try:
        routing = await memory_bridge.query_routing_hint("engine_mutation", MODEL)
        weight = routing.get("weight", 1.0)
        
        # Diversity Extinction Event Logic
        recent_failures = len([m for m in mutation_history[-CONSECUTIVE_FAILURES_FOR_EXTINCTION:] if m == "failure"])
        if recent_failures >= CONSECUTIVE_FAILURES_FOR_EXTINCTION:
            logger.warning("🚨 DIVERSITY COLLAPSE DETECTED: Triggering Extinction Event!")
            temperature = 1.5 # Radical exploration
            prompt += "\n\nCRITICAL: You are in an extinction event. The previous mutations collapsed into local optima. You MUST try a radically unconventional algorithmic approach."
            mutation_history.clear() # Reset after event
        else:
            temperature = max(0.2, 1.0 - (weight * 0.8))
            
        logger.info(f"MemoryBridge routing hint: weight={weight:.2f}, setting temperature={temperature:.2f}")
        
        historical_context = await memory_bridge.get_memory_context("engine core_code mutation")
    except Exception as e:
        logger.warning(f"Failed to query MemoryBridge: {e}")
        historical_context = ""
        temperature = 0.7
    finally:
        await memory_bridge.close()
    
    if historical_context:
        prompt += f"\n\nHistorical Context of past runs (GraphRAG):\n{historical_context}\nAvoid repeating past mistakes."
    
    messages = [{"role": "user", "content": prompt}]
    max_retries = 3
    last_error = ""

    for attempt in range(max_retries):
        messages = [{"role": "user", "content": prompt}]
        
        try:
            # Route to cloud DeepSeek (OpenCode Go / OPENAI_API_BASE) when the
            # model is a cloud id; fall back to local llama.cpp otherwise.
            _is_cloud = "/" in MODEL and not MODEL.startswith("openai/qwen")
            import os as _os
            _kwargs = {"model": MODEL, "messages": messages, "temperature": temperature}
            if _is_cloud:
                _base = _os.getenv("OPENAI_API_BASE", "")
                _key = _os.getenv("OPENAI_API_KEY", "")
                if _base:
                    _kwargs["api_base"] = _base
                if _key:
                    _kwargs["api_key"] = _key
            else:
                _kwargs.update(api_base="http://127.0.0.1:8080/v1", api_key="llama")
            res = await acompletion(**_kwargs)
            mutated_code_full = res.choices[0].message.content
            
            match = re.search(r"```(?:python)?(.*?)```", mutated_code_full, re.DOTALL)
            if match:
                mutated_code = match.group(1).strip()
            else:
                # Fallback: remove any remaining backticks to prevent syntax errors
                mutated_code = mutated_code_full.replace("```python", "").replace("```", "").strip()

            # Re-indent the mutated code to the slice's indentation. LLM output is
            # top-level (column 0), but the target may be a class method (e.g.
            # 4-space indent). Splicing an un-indented def into a class would break
            # the file, so dedent the LLM output then re-indent to the original span.
            indent_prefix = re.match(r"^\s*", sliced_code).group(0)
            if indent_prefix:
                mutated_code = textwrap.indent(textwrap.dedent(mutated_code), indent_prefix)

            # Splice the mutated function back into the core code
            # rather than overwriting the entire file with a single function
            new_core_code = core_code.replace(sliced_code, mutated_code)

            if new_core_code == core_code:
                raise Exception(
                    "Mutation produced no change (slice not found or identical). "
                    "Retrying with a stronger instruction."
                )
                
            logger.info("Mutation generated. Deploying to Danger Room sandbox for testing...")
            
            async with DangerRoom(ROOT_DIR) as sandbox:
                try:
                    rel_path = target_file.relative_to(ROOT_DIR)
                except ValueError:
                    rel_path = Path(target_file.name)
                    
                sandbox_file = sandbox.sandbox_dir / rel_path
                # Ensure the path exists in the sandbox
                sandbox_file.parent.mkdir(parents=True, exist_ok=True)
                # We write the FULL combined code to the sandbox
                with open(sandbox_file, "w", encoding="utf-8") as f:
                    f.write(new_core_code)
                    
                logger.info("Phase 2: Executing Security Gate scan on mutation...")
                await sandbox.scan_sandbox(specific_files=[str(rel_path).replace("\\", "/")])
                    
                logger.info("Verifying mutation compiles...")
                from swarm_os.services.security_gate import clean_sandbox_env
                compile_check = await asyncio.create_subprocess_exec(
                    "python", "-m", "py_compile", str(sandbox_file),
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                    cwd=str(sandbox.sandbox_dir),
                    env=clean_sandbox_env(),
                )
                _, compile_err = await compile_check.communicate()
                if compile_check.returncode != 0:
                    raise Exception(f"Mutation failed to compile: {compile_err.decode()}")

                logger.info("Running real test suite against sandboxed mutation...")
                from swarm_os.services.security_gate import clean_sandbox_env
                test_proc = await asyncio.create_subprocess_exec(
                    "python", "-m", "pytest", "tests/test_agentic_loop.py", "-v",
                    cwd=str(sandbox.sandbox_dir),
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                    env=clean_sandbox_env(),
                )
                test_out, test_err = await test_proc.communicate()
                if test_proc.returncode != 0:
                    tail_out = test_out.decode()[-2000:]
                    tail_err = test_err.decode()[-1000:]
                    raise Exception("Mutation failed real test suite:\n" + tail_out + "\n" + tail_err)

                logger.info("Compile check and real test suite PASSED.")
                PENDING_MUTATION_DIR.mkdir(parents=True, exist_ok=True)
                mutation_id = str(uuid.uuid4())
                pending_dir = PENDING_MUTATION_DIR / mutation_id
                pending_dir.mkdir(parents=True, exist_ok=True)

                pending_file = pending_dir / sandbox_file.name
                shutil.copy2(sandbox_file, pending_file)

                metadata = {
                    "event_type": "engine_mutation",
                    "model": MODEL,
                    "outcome": "success",
                    "task_id": "engine_evolution",
                    "mutation_id": mutation_id,
                    "target_path": str(target_file),
                    "pending_file": str(pending_file),
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "compile_ok": True,
                    "tests_passed": True,
                    "details": "Mutation passed compile check and real test suite and was stored for approval."
                }

                (pending_dir / "metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
                
                logger.info("Genetic mutation passed compile/test checks and was stored for approval.")
                
                # Record success to memory graph
                memory_bridge._add(metadata)
                await memory_bridge._flush()
                mutation_history.append("success")
                break # Success! Exit the retry loop.

        except SecurityGateViolation as e:
            last_error = str(e)
            logger.warning(f"Security Gate Violation on attempt {attempt + 1}: {e}")
            prompt = EVOLUTION_PROMPT.format(target_func=target_func, core_code_slice=sliced_code) + f"\n\nERROR ON LAST ATTEMPT:\nYour previous mutation failed the security gate with the following violation:\n{e}\nPlease fix the code so it passes the security scan."
        except Exception as e:
            last_error = str(e)
            logger.warning(f"Sandbox test failed on attempt {attempt + 1}: {e}")
            prompt = EVOLUTION_PROMPT.format(target_func=target_func, core_code_slice=sliced_code) + f"\n\nERROR ON LAST ATTEMPT:\nYour previous mutation failed sandbox testing with the following error:\n{e}\nPlease fix the code."
    else:
        logger.critical("Evolution halted! The agent failed to produce a valid mutation after maximum retries.")
        # Record failure to memory graph
        memory_bridge._add({
            "event_type": "engine_mutation",
            "model": MODEL,
            "outcome": "failure",
            "task_id": "engine_evolution",
            "details": f"Max retries reached. Mutation failed. Last error: {last_error}" if last_error else "Max retries reached. Mutation failed."
        })
        await memory_bridge._flush()
        mutation_history.append("failure")

    # BUG FIX: Persist mutation_history to disk so Extinction Events survive across invocations
    try:
        HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
        HISTORY_FILE.write_text(json.dumps(mutation_history[-50:]), encoding="utf-8")
        logger.info(f"Mutation history saved ({len(mutation_history)} entries).")
    except Exception as e:
        logger.warning(f"Failed to save mutation history: {e}")

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Genetic Mutation Engine")
    parser.add_argument("--file", type=str, default=str(AGENT_SERVICE_PATH), help="Target Python file")
    parser.add_argument("--func", type=str, default="get_agent", help="Target function name")
    args = parser.parse_args()
    asyncio.run(run_genetic_mutation(args.file, args.func))


def approve_pending_mutation(metadata_path: str) -> dict:
    """
    Promote a staged mutation into the real target file.
    metadata_path should point to a metadata.json file under .data/pending_mutations/<id>/
    """
    from pathlib import Path
    from swarm_os.repositories.mutation_repo import MutationRepository
    
    mutation_id = Path(metadata_path).parent.name
    repo = MutationRepository()
    return repo.approve(mutation_id)

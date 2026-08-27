import json
import asyncio
import logging
import os
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


# Raised by the fail-fast AST syntax pre-check (a truncated/unterminated function
# body). Carries the SyntaxError's .msg/.lineno so the retry prompt can tell the
# model the exact line that failed — distinct from a sandbox test failure so the
# corrective instruction is "complete the function body", not "fix the test".
class _MutationSyntaxError(Exception):
    def __init__(self, msg: str, lineno: int | None = None):
        super().__init__(msg)
        self.msg = msg
        self.lineno = lineno


logger = logging.getLogger("GeneticEvolution")
if logging.getLogger().handlers == []:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
    )

ROOT_DIR = Path(__file__).parent.parent.parent.resolve()
PENDING_MUTATION_DIR = ROOT_DIR / ".data" / "pending_mutations"
AGENT_SERVICE_PATH = ROOT_DIR / "runtime_v2" / "api" / "agent_service_v2.py"
# Code mutation = complex reasoning — use a cloud model instead of local qwen3.5-4b
# which produces weak mutations. Use the SAME free-first analysis-cloud selection
# the rest of the swarm uses (any free provider key enables it; default NVIDIA
# free flash), so a provider with an expired/unusable credential can never be
# chosen (previously a dead GEMINI_API_KEY was preferred and halted evolution).
from runtime_v2.services._llm_client import (
    _analysis_cloud_enabled,
    _analysis_cloud_model,
)

MODEL = (
    _analysis_cloud_model() if _analysis_cloud_enabled() else "openai/deepseek-v4-flash"
)
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


async def run_genetic_mutation(
    target_file_path: str = str(AGENT_SERVICE_PATH), target_func: str = "get_agent"
):
    logger.info("Initializing Genetic Evolution Loop...")

    # BUG FIX: Load persistent mutation history from disk.
    # Each entry is a dict: {"outcome": "success"|"failure", "ts": <iso>, "error": <str>}
    # Legacy bare-string entries ("success"/"failure") are coerced on load so the
    # existing file needs no migration.
    mutation_history: list[dict] = []
    if HISTORY_FILE.exists():
        try:
            raw = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
            mutation_history = [
                e if isinstance(e, dict) else {"outcome": e, "ts": "", "error": ""}
                for e in raw
            ]
        except Exception:
            mutation_history = []

    # Keep only the last N entries to avoid unbounded file growth
    mutation_history = mutation_history[-50:]

    # Fail-fast when the cloud chain is entirely down (2026-08-24: a fully dead
    # provider chain — 402/401/capped — burned 3 x 90s retries per hourly tick
    # and tripped the 3-consecutive-failure Extinction Event, "halting"
    # evolution over what is INFRA downtime, not mutation quality). Skip the
    # cycle WITHOUT recording a failure; the next tick re-checks liveness.
    try:
        from runtime_v2.services.fallback_manager import (
            _is_local_model,
            get_live_fallbacks,
            is_model_cooled_down,
        )

        routing_mode = os.getenv("SWARM_ROUTING_MODE", "auto")
        live_cloud = [
            f["model"]
            for f in await get_live_fallbacks(mode=routing_mode)
            if not _is_local_model(f["model"])
            and not is_model_cooled_down(f["model"])
        ]
        # REGRESSION FIX (cfa3ee6): "not on cooldown" is NOT "reachable". A freshly
        # started process has no cooldown records, so a cloud MODEL with nothing
        # cooled-down would set primary_live=True and the empty-chain fail-fast
        # never fired — the loop then called the LLM 3x against a dead chain and
        # tripped "Evolution halted". Liveness = an actually-reachable fallback
        # (live_cloud), not the absence of a cooldown marker. A qualifying-hung
        # catalog model is still handled: the cooldown filter above excludes it
        # from live_cloud, so with MODEL cloud + an empty chain we fail fast here.
        # (local MODEL stays usable — local always reachable, never fails fast.)
        if not live_cloud and not _is_local_model(MODEL):
            logger.warning(
                "No live cloud provider available (all cooling down or empty "
                "chain) — skipping mutation cycle this tick instead of burning "
                "retries on a dead chain."
            )
            return
    except Exception as e:
        logger.debug("Provider liveness pre-check skipped: %s", e)

    target_file = Path(target_file_path).resolve()
    # to_thread: don't block the shared async event loop (the API + daemons run
    # on the same loop) on synchronous disk I/O.
    core_code = await asyncio.to_thread(target_file.read_text, encoding="utf-8")

    # AST Slicing: Only extract the targeted bottleneck rather than truncating randomly
    sliced_code = ast_slice(core_code, target_func)

    if not sliced_code:
        raise ValueError(
            f"FATAL: Target function '{target_func}' not found in source code. Aborting to prevent file explosion."
        )

    prompt = EVOLUTION_PROMPT.format(
        target_func=target_func, core_code_slice=sliced_code
    )

    memory_bridge = MemoryBridge()
    try:
        routing = await memory_bridge.query_routing_hint("engine_mutation", MODEL)
        weight = routing.get("weight", 1.0)

        # Diversity Extinction Event Logic
        recent_failures = len(
            [
                m
                for m in mutation_history[-CONSECUTIVE_FAILURES_FOR_EXTINCTION:]
                if (m.get("outcome") if isinstance(m, dict) else m) == "failure"
            ]
        )
        if recent_failures >= CONSECUTIVE_FAILURES_FOR_EXTINCTION:
            logger.warning(
                "🚨 DIVERSITY COLLAPSE DETECTED: Triggering Extinction Event!"
            )
            temperature = 1.5  # Radical exploration
            prompt += "\n\nCRITICAL: You are in an extinction event. The previous mutations collapsed into local optima. You MUST try a radically unconventional algorithmic approach."
            mutation_history.clear()  # Reset after event
        else:
            temperature = max(0.2, 1.0 - (weight * 0.8))

        logger.info(
            f"MemoryBridge routing hint: weight={weight:.2f}, setting temperature={temperature:.2f}"
        )

        historical_context = await memory_bridge.get_memory_context(
            "engine core_code mutation"
        )
    except Exception as e:
        logger.warning(f"Failed to query MemoryBridge: {e}")
        historical_context = ""
        temperature = 0.7

    if historical_context:
        prompt += f"\n\nHistorical Context of past runs (GraphRAG):\n{historical_context}\nAvoid repeating past mistakes."

    messages = [{"role": "user", "content": prompt}]
    max_retries = 3
    last_error = ""

    for attempt in range(max_retries):
        messages = [{"role": "user", "content": prompt}]

        try:
            # Fail over through the LIVE cloud chain immediately when a provider
            # drops out (free tiers cycle/expire often). Mirrors the proven
            # complete_for_tool_decision seam: build_kwargs() emits a per-provider
            # dict fallback list (each scoped to its OWN endpoint+key), and
            # litellm.acompletion() walks the chain on failure — a dead provider
            # (NVIDIA free quota, Groq/Gemini limits, OpenRouter credit) is skipped
            # on the next call instead of burning retries on it and halting
            # evolution. (A litellm Router built over distinct model_name groups
            # does NOT cross-failover without an explicit fallbacks arg — verified
            # empirically — so the dict-fallback form is required.)
            from runtime_v2.services.fallback_manager import (
                get_live_fallbacks,
                _is_local_model,
            )
            from runtime_v2.services._llm_client import build_kwargs

            routing_mode = os.getenv("SWARM_ROUTING_MODE", "auto")
            raw_fallbacks = await get_live_fallbacks(mode=routing_mode)
            fallbacks = [
                f["model"] for f in raw_fallbacks if not _is_local_model(f["model"])
            ][:4]
            kwargs = build_kwargs(
                MODEL,
                {"messages": messages, "temperature": temperature},
                fallbacks,
            )
            kwargs["max_retries"] = 0
            kwargs["timeout"] = 90.0
            async with asyncio.timeout(90):
                res = await acompletion(**kwargs)
            mutated_code_full = res.choices[0].message.content

            match = re.search(r"```(?:python)?(.*?)```", mutated_code_full, re.DOTALL)
            if match:
                mutated_code = match.group(1).strip()
            else:
                # Fallback: remove any remaining backticks to prevent syntax errors
                mutated_code = (
                    mutated_code_full.replace("```python", "")
                    .replace("```", "")
                    .strip()
                )

            # Re-indent the mutated code to the slice's indentation. LLM output is
            # top-level (column 0), but the target may be a class method (e.g.
            # 4-space indent). Splicing an un-indented def into a class would break
            # the file, so dedent the LLM output then re-indent to the original span.
            indent_prefix = re.match(r"^\s*", sliced_code).group(0)
            if indent_prefix:
                mutated_code = textwrap.indent(
                    textwrap.dedent(mutated_code), indent_prefix
                )

            # Splice the mutated function back into the core code
            # rather than overwriting the entire file with a single function
            new_core_code = core_code.replace(sliced_code, mutated_code)

            if new_core_code == core_code:
                raise Exception(
                    "Mutation produced no change (slice not found or identical). "
                    "Retrying with a stronger instruction."
                )

            # FAIL-FAST syntax gate: reject a broken mutation BEFORE the full
            # DangerRoom sandbox deploy/teardown cycle. Previously a malformed
            # splice (e.g. the recurring "expected an indented block after
            # function definition" mutation) only failed at the py_compile step
            # inside the sandbox, wasting a full copy/deploy/pytest cycle per
            # attempt while the model kept re-proposing the same broken code.
            try:
                import ast

                ast.parse(new_core_code)
            except SyntaxError as e:
                raise _MutationSyntaxError(
                    f"Mutation failed syntax pre-check: {e.msg} (line {e.lineno})",
                    lineno=e.lineno,
                )

            logger.info(
                "Mutation generated. Deploying to Danger Room sandbox for testing..."
            )

            async with DangerRoom(ROOT_DIR) as sandbox:
                try:
                    rel_path = target_file.relative_to(ROOT_DIR)
                except ValueError:
                    rel_path = Path(target_file.name)

                sandbox_file = sandbox.sandbox_dir / rel_path
                # Ensure the path exists in the sandbox
                sandbox_file.parent.mkdir(parents=True, exist_ok=True)
                # We write the FULL combined code to the sandbox
                await asyncio.to_thread(
                    sandbox_file.write_text, new_core_code, encoding="utf-8"
                )

                logger.info("Phase 2: Executing Security Gate scan on mutation...")
                await sandbox.scan_sandbox(
                    specific_files=[str(rel_path).replace("\\", "/")]
                )

                logger.info("Verifying mutation compiles...")
                from swarm_os.services.security_gate import clean_sandbox_env
                import sys

                compile_check = await asyncio.create_subprocess_exec(
                    sys.executable,
                    "-m",
                    "py_compile",
                    str(sandbox_file),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=str(sandbox.sandbox_dir),
                    env=clean_sandbox_env(),
                )
                try:
                    async with asyncio.timeout(30):
                        _, compile_err = await compile_check.communicate()
                except TimeoutError:
                    compile_check.kill()
                    raise Exception(
                        "Mutation compile check timed out after 30 seconds."
                    )
                if compile_check.returncode != 0:
                    raise Exception(
                        f"Mutation failed to compile: {compile_err.decode()}"
                    )

                logger.info("Running real test suite against sandboxed mutation...")
                # EVO-3: run the tests RELATED to the MUTATED file (never a
                # hardcoded suite that may not exercise the change). Default to
                # the agent-service suite when nothing matches (that is the
                # historical behavior and remains correct for the default
                # agent_service_v2.py target).
                related = _find_related_test_files(str(rel_path))
                test_targets = [
                    str((sandbox.sandbox_dir / Path(t).relative_to(ROOT_DIR)).resolve())
                    for t in related
                    if (sandbox.sandbox_dir / Path(t).relative_to(ROOT_DIR)).exists()
                ]
                if not test_targets:
                    fallback_test = (
                        sandbox.sandbox_dir / "tests" / "test_agentic_loop.py"
                    )
                    if fallback_test.exists():
                        test_targets = [str(fallback_test.resolve())]
                    else:
                        test_targets = []

                test_res = await sandbox.run_tests(test_targets, timeout=120.0)
                if not test_res.get("ok"):
                    out = test_res.get("output", "")
                    raise Exception(
                        f"Mutation failed real test suite (exit {test_res.get('exit_code')}):\n{out[-2000:]}"
                    )

                logger.info("Compile check and real test suite PASSED.")
                PENDING_MUTATION_DIR.mkdir(parents=True, exist_ok=True)
                mutation_id = str(uuid.uuid4())
                pending_dir = PENDING_MUTATION_DIR / mutation_id
                pending_dir.mkdir(parents=True, exist_ok=True)

                pending_file = pending_dir / sandbox_file.name
                await asyncio.to_thread(shutil.copy2, sandbox_file, pending_file)

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
                    "details": "Mutation passed compile check and real test suite and was stored for approval.",
                }

                await asyncio.to_thread(
                    (pending_dir / "metadata.json").write_text,
                    json.dumps(metadata, indent=2),
                    encoding="utf-8",
                )

                logger.info(
                    "Genetic mutation passed compile/test checks and was stored for approval."
                )

                # Record success to memory graph
                memory_bridge._add(metadata)
                await memory_bridge._flush()
                mutation_history.append(
                    {
                        "outcome": "success",
                        "ts": datetime.now(timezone.utc).isoformat(),
                        "error": "",
                    }
                )
                break  # Success! Exit the retry loop.

        except SecurityGateViolation as e:
            last_error = str(e)
            logger.warning(f"Security Gate Violation on attempt {attempt + 1}: {e}")
            # Store as a structured failure in the memory graph so future mutation
            # runs can learn via [PAST-MISTAKE WARNING] — not just a one-shot prompt
            # update. The entry carries `component` so get_latest_failure() prefers it
            # over genetic-kernel noise, and `fix_class` tags it for the distiller.
            try:
                memory_bridge._add(
                    {
                        "event_type": "engine_mutation_security_gate_violation",
                        "model": MODEL,
                        "outcome": "failure",
                        "task_id": "engine_evolution",
                        "details": f"SecurityGateViolation: {e}",
                    }
                )
                await memory_bridge._flush()
            except Exception:
                pass
            prompt = (
                prompt
                + f"\n\nERROR ON LAST ATTEMPT:\nYour previous mutation failed the security gate with the following violation:\n{e}\nPlease fix the code so it passes the security scan."
            )
        except _MutationSyntaxError as e:
            # Dedicated branch for the AST syntax pre-check (reviewer fix #2): the
            # recurring "expected an indented block after function definition"
            # defect class is NOT a sandbox failure — it is a truncated/malformed
            # function body. Give the model the exact line + an explicit
            # "the body is incomplete" directive instead of the generic sandbox
            # message, so it stops re-proposing unterminated defs.
            last_error = str(e)
            logger.warning(
                f"Mutation syntax pre-check failed on attempt {attempt + 1}: {e}"
            )
            prompt = prompt + (
                f"\n\nERROR ON LAST ATTEMPT (SYNTAX PRE-CHECK):\n"
                f"Your previous mutation was rejected BEFORE testing because it does not "
                f"parse as valid Python: {e.msg} on line {e.lineno}.\n"
                f"The mutation is likely a TRUNCATED or UNTERMINATED function body — every "
                f"def/class/if/for/while must be followed by an indented body, and the closing "
                f"brace/indent must be present. Re-emit the COMPLETE function including all "
                f"inner statements and the final dedent. Match the indentation of the original "
                f"slice ({indent_prefix!r})."
            )
        except Exception as e:
            # TimeoutError (from asyncio.timeout() around the acall) has
            # str() == "" — an empty last_error would write an empty "error"
            # body and recreate the exact undiagnosable-halt problem the
            # durability fix exists to prevent. Fall back to a descriptive
            # class-name message when str(e) is empty.
            last_error = str(e) or f"{type(e).__name__} (no message)"
            logger.warning(f"Sandbox test failed on attempt {attempt + 1}: {e}")
            prompt = (
                prompt
                + f"\n\nERROR ON LAST ATTEMPT:\nYour previous mutation failed sandbox testing with the following error:\n{e}\nPlease fix the code."
            )
    else:
        logger.critical(
            "Evolution halted! The agent failed to produce a valid mutation after maximum retries."
        )
        # Record failure to memory graph
        memory_bridge._add(
            {
                "event_type": "engine_mutation",
                "model": MODEL,
                "outcome": "failure",
                "task_id": "engine_evolution",
                "details": f"Max retries reached. Mutation failed. Last error: {last_error}"
                if last_error
                else "Max retries reached. Mutation failed.",
            }
        )
        await memory_bridge._flush()
        mutation_history.append(
            {
                "outcome": "failure",
                "ts": datetime.now(timezone.utc).isoformat(),
                # Store up to 2 kB of the failure body so post-mortem debugging
                # is possible without console access (the gap that made the 15:04
                # evolution halt undiagnosable: failure body only went to live
                # console and was not recoverable after the process exited).
                "error": last_error[-2000:] if last_error else "",
            }
        )

    # BUG FIX: Persist mutation_history to disk so Extinction Events survive across invocations
    try:
        HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(
            HISTORY_FILE.write_text,
            json.dumps(mutation_history[-50:]),
            encoding="utf-8",
        )
        logger.info(f"Mutation history saved ({len(mutation_history)} entries).")
    except Exception as e:
        logger.warning(f"Failed to save mutation history: {e}")

    # Close the memory bridge LAST — the success/failure recording (memory_bridge
    # _add/_flush inside the retry loop) must happen before the bridge tears down
    # its httpx + embedding clients. Closing here (after the loop) instead of in
    # an early finally avoids a use-after-close on the success/failure paths.
    try:
        await memory_bridge.close()
    except Exception as e:
        logger.warning(f"Failed to close MemoryBridge: {e}")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Genetic Mutation Engine")
    parser.add_argument(
        "--file", type=str, default=str(AGENT_SERVICE_PATH), help="Target Python file"
    )
    parser.add_argument(
        "--func", type=str, default="get_agent", help="Target function name"
    )
    args = parser.parse_args()
    asyncio.run(run_genetic_mutation(args.file, args.func))


def _find_related_test_files(file_path: str) -> list[str]:
    """Locate test files that exercise a changed module (by name, then content
    scan). Mirrors agent_service_v2._find_related_tests so the mutation loop's
    validation runs the RIGHT tests for the mutated file — never a hardcoded
    suite for an unrelated target. Returns [] on any error (caller falls back
    to a default suite)."""
    import glob as _glob

    try:
        base = Path(file_path).stem
        mod = str(file_path).replace("\\", "/").replace(".py", "")
        out: list[str] = []
        for t in sorted(_glob.glob(str(ROOT_DIR / "tests" / "test_*.py"))):
            tname = Path(t).name
            if base in tname or tname.replace("test_", "").replace(".py", "") in base:
                out.append(t)
                if len(out) >= 6:
                    break
        if not out:
            for t in sorted(_glob.glob(str(ROOT_DIR / "tests" / "test_*.py"))):
                try:
                    head = Path(t).read_text(encoding="utf-8", errors="ignore")[:4000]
                except Exception:
                    continue
                if base in head or mod.split("/")[-1] in head:
                    out.append(t)
                    if len(out) >= 6:
                        break
        return out
    except Exception as e:
        logger.warning("related-test discovery failed: %s", e)
        return []


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

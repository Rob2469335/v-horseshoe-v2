import os
import json
import logging
import asyncio
import time
import uuid
import re
from pathlib import Path
from litellm import acompletion
from qdrant_client import AsyncQdrantClient
from qdrant_client.models import PointStruct, VectorParams, Distance, Filter, FieldCondition, MatchValue
from swarm_os.services.embedding_service import EmbeddingService

logger = logging.getLogger("ReflectionLoop")

ROOT_DIR = Path(__file__).parent.parent.parent.resolve()
DIARY_PATH = ROOT_DIR / "swarm_os" / "logs" / "organism_diary.jsonl"
LOCAL_MODEL = "qwen3.5-4b"  # Local llama.cpp alias
# Sanctioned free cloud model (DeepSeek V4 flash via the funded OpenCode Go
# account). Local qwen3.5-4b burns all max_tokens on reasoning_content for the
# long distiller prompt, producing empty content at ~5 tok/s; DeepSeek flash
# emits the structured reflection directly.
CLOUD_MODEL = "openai/deepseek-v4-flash"
DISTILLER_MAX_TOKENS_CLOUD = 300
DISTILLER_MAX_TOKENS_LOCAL = 512

def _record_rule_to_agents_md(component: str, correction: str, confidence: float):
    """SOTA 2026: Auto-document high-confidence ASPO reflection rules into AGENTS.md."""
    if confidence < 0.85 or not correction:
        return
    try:
        agents_file = ROOT_DIR / "AGENTS.md"
        if not agents_file.exists():
            return
        content = agents_file.read_text(encoding="utf-8")
        clean_rule = correction.strip()
        if len(clean_rule) > 150:
            clean_rule = clean_rule[:147] + "..."
        if clean_rule in content:
            return
        new_entry = f"- **Rule ({component})**: {clean_rule}\n"
        marker = "## Self-Healing & Self-Learning Fixes\n"
        if marker in content:
            content = content.replace(marker, marker + "\n" + new_entry, 1)
            agents_file.write_text(content, encoding="utf-8")
            logger.info("Recorded high-confidence ASPO rule to AGENTS.md for component '%s'", component)
    except Exception as e:
        logger.warning("Could not record ASPO rule to AGENTS.md: %s", e)

# Structured reflection template (per Reflexion best practices). Free-form rules
# are worthless; rules must reference the concrete failure and a do-not-repeat.
DISTILLER_PROMPT = """You are the ASPO Rule Distiller. Convert agent failures into reusable correction rules.

Task Context: {task_description}
Component: {component}
Agent Output/Action: {content_preview}
Failure Reason: {error_message}

Produce a structured reflection with EXACTLY this format:
<reflection>
<failure_summary>
[1-2 sentences: what the agent did and what failed]
</failure_summary>
<root_cause>
[1 sentence: the actual root cause]
</root_cause>
<next_attempt_rules>
[2-3 concrete, actionable rules referencing specific tools/constraints]
</next_attempt_rules>
<do_not_repeat>
[1 sentence: the exact mistake to never repeat]
</do_not_repeat>
</reflection>"""


# Shared-scope reflection rules (cross-agent lessons). Only confident, generic,
# non-agent-specific failures may be shared; everything else stays agent-siloed.
# Retrieval of shared rules is env-gated (SWARM_SHARED_REFLEXION=1, off by default).
SHARED_SCOPE_MIN_CONFIDENCE = 0.7
SHARED_SCOPE_GENERIC_MARKERS = (
    "file not found",
    "permission denied",
    "no such file",
    "unknown operation",
    "timeout",
    "timed out",
    "slot was busy",
    "malformed json",
    "malformed",
    "truncated",
    "parse",
)


def _auto_scope(confidence: float, failure_reason: str) -> str:
    """Conservative scope assignment for stored reflexion rules.

    Only rules that are (a) confident enough (>= SHARED_SCOPE_MIN_CONFIDENCE)
    and (b) whose failure reason matches an explicit allowlist of generic,
    cross-agent failure categories qualify for scope='shared'. Everything else
    stays 'agent' so agent-specific lessons never leak across agents.
    """
    if confidence < SHARED_SCOPE_MIN_CONFIDENCE:
        return "agent"
    reason = (failure_reason or "").lower()
    if not any(marker in reason for marker in SHARED_SCOPE_GENERIC_MARKERS):
        return "agent"
    return "shared"


class ReflectionService:
    def __init__(self, qdrant_url: str = "http://127.0.0.1:6333", collection_name: str = "ReflexionMemory"):
        self.client = AsyncQdrantClient(url=qdrant_url)
        self.collection = collection_name
        self.embedder = EmbeddingService()
        try:
            loop = asyncio.get_running_loop()
            self._init_task = loop.create_task(self._init_collection())
        except RuntimeError:
            self._init_task = None
        self._ensured = False

    async def _wait_init(self):
        if self._init_task:
            success = await self._init_task
            self._init_task = None
            if success:
                self._ensured = True
        if not self._ensured:
            self._ensured = await self._init_collection()

    async def _init_collection(self) -> bool:
        try:
            collections_response = await self.client.get_collections()
            collections = collections_response.collections
            if not any(c.name == self.collection for c in collections):
                await self.client.create_collection(
                    collection_name=self.collection,
                    vectors_config=VectorParams(size=768, distance=Distance.COSINE)
                )
            return True
        except Exception as e:
            logger.error("Failed to init ReflexionMemory: %s", e)
            return False

    async def check_for_past_mistakes(self, task_context: str, threshold: float = 0.75, max_chars: int = 700) -> str:
        """Ranked retrieval with recency+confidence decay (top-k, not single hit).
        score = similarity · decay(age) · confidence; returns the single best rule.

        When SWARM_SHARED_REFLEXION=1 (off by default), an extra top-k query
        filtered to scope='shared' is merged in, so generic lessons any agent
        produced are surfaced regardless of their component — de-duplicated
        against the agent's own results and ranked by the same decay formula.
        """
        await self._wait_init()
        try:
            embedding = await self.embedder.embed(task_context)
            # qdrant-client >=1.18: AsyncQdrantClient has no .search(); use query_points.
            response = await self.client.query_points(
                collection_name=self.collection,
                query=embedding,
                limit=5,
                score_threshold=threshold,
            )
            results = list(getattr(response, "points", response))
            if os.environ.get("SWARM_SHARED_REFLEXION") == "1":
                shared_response = await self.client.query_points(
                    collection_name=self.collection,
                    query=embedding,
                    limit=5,
                    score_threshold=threshold,
                    query_filter=Filter(must=[FieldCondition(key="scope", match=MatchValue(value="shared"))]),
                )
                shared_results = list(getattr(shared_response, "points", shared_response))
                seen_ids = set()
                merged = []
                for r in results + shared_results:
                    rid = getattr(r, "id", None)
                    if rid is not None and rid in seen_ids:
                        continue
                    if rid is not None:
                        seen_ids.add(rid)
                    merged.append(r)
                results = merged
            if results:
                now = time.time()
                best = None
                best_score = 0.0
                for r in results:
                    payload = r.payload or {}
                    sim = r.score or 0.0
                    age = now - float(payload.get("timestamp", now))
                    decay = max(0.3, 1.0 - (age / (30 * 86400)))  # halve after ~30 days
                    confidence = float(payload.get("confidence", 0.5))
                    ranked = sim * decay * confidence
                    if ranked > best_score:
                        best_score = ranked
                        best = payload
                if best:
                    correction = best.get("correction", "")
                    do_not = best.get("do_not_repeat", "")
                    hint = f"WARNING: A similar approach previously failed. Advice: {correction}"
                    if do_not:
                        hint += f" Do NOT repeat: {do_not}"
                    # Cap lesson length so injected warnings never eat the context
                    # budget (2026: keep reflection lessons at ~10-20% of context).
                    return hint[:max_chars]
        except Exception as e:
            logger.warning("Failed to check ReflexionMemory: %s", e)
        return ""

    async def store_reflexion(self, task: str, action: str, failure_reason: str, correction: str,
                              component: str = "unknown", confidence: float = 0.7,
                              do_not_repeat: str = "", scope: str | None = None):
        """Persist a reflexion rule. scope defaults to 'agent' and is auto-assigned
        via _auto_scope() when not given (confident + generic failure => 'shared')."""
        await self._wait_init()
        try:
            if scope is None:
                scope = _auto_scope(confidence, failure_reason)
            embedding = await self.embedder.embed(task)
            # Deterministic point id keyed on (component, failure_reason):
            # a repeated failure overwrites the prior rule instead of flooding
            # the collection with near-duplicate points (observed: 60 copies of
            # "File not found: x.py" crowded out specific per-task lessons).
            point_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{component}|{failure_reason}"))
            await self.client.upsert(
                collection_name=self.collection,
                points=[
                    PointStruct(
                        id=point_id,
                        vector=embedding,
                        payload={
                            "task": task,
                            "action": action,
                            "failure_reason": failure_reason,
                            "correction": correction,
                            "do_not_repeat": do_not_repeat,
                            "component": component,
                            "scope": scope,
                            "timestamp": time.time(),
                            "confidence": confidence,
                        }
                    )
                ],
                wait=True
            )
            await asyncio.to_thread(_record_rule_to_agents_md, component, correction, confidence)
        except Exception as e:
            logger.error("Failed to store reflexion: %s", e)

_service = None
def get_reflection_service() -> ReflectionService:
    global _service
    if _service is None:
        _service = ReflectionService()
    return _service

def get_latest_failure(filepath: Path) -> dict | None:
    chunk_size = 4096
    with open(filepath, 'rb') as f:
        f.seek(0, os.SEEK_END)
        file_size = f.tell()
        position = file_size
        buffer = b''
        
        while position > 0:
            read_size = min(chunk_size, position)
            position -= read_size
            f.seek(position)
            chunk = f.read(read_size)
            buffer = chunk + buffer
            
            lines = buffer.split(b'\n')
            if position > 0:
                buffer = lines.pop(0)
            
            for line in reversed(lines):
                if not line.strip():
                    continue
                try:
                    entry = json.loads(line.decode('utf-8'))
                    err = entry.get("error")
                    if err is not None and err != "" and str(err).lower() != "null":
                        # Prefer REAL agent failures (entries carrying a component or
                        # agent id) over genetic-kernel evaluation noise. The diary is
                        # dominated by kernel eval entries (event=action/evaluation with
                        # org/generation/avg_fitness) whose errors are sandbox/fitness
                        # artifacts ("http_422", "[WinError 10061]", ...) — distilling
                        # those produced the 137 component:"unknown" noise rules that
                        # swamped ReflexionMemory. Real agent tool failures carry a
                        # component (agent_id) — those are what the distiller should learn from.
                        is_agent_failure = bool(entry.get("component") or entry.get("agent"))
                        if is_agent_failure:
                            return entry
                except Exception:
                    pass
    # No agent-tagged failure found — fall back to the raw last error so the
    # distiller still has SOMETHING rather than silently skipping.
    with open(filepath, 'rb') as f:
        f.seek(0, os.SEEK_END)
        file_size = f.tell()
        position = file_size
        buffer = b''
        while position > 0:
            read_size = min(chunk_size, position)
            position -= read_size
            f.seek(position)
            chunk = f.read(read_size)
            buffer = chunk + buffer
            lines = buffer.split(b'\n')
            if position > 0:
                buffer = lines.pop(0)
            for line in reversed(lines):
                if not line.strip():
                    continue
                try:
                    entry = json.loads(line.decode('utf-8'))
                    err = entry.get("error")
                    if err is not None and err != "" and str(err).lower() != "null":
                        return entry
                except Exception:
                    pass
    return None

async def _distill(distiller_content: str, fix_class: str | None = None) -> str:
    """Run the distiller LLM call. Cloud DeepSeek V4 flash first (fast, no
    thinking tokens), local qwen3.5-4b fallback (needs /no_think system lead +
    roomy token budget so reasoning + content both fit at ~5 tok/s).

    fix_class (from diagnostician.py): "prompt_sensitivity" means the failure
    is fixable via rule/prompt changes — distillation is worthwhile.
    "model_variability" means the model itself can't do it — skip the LLM call
    entirely, it won't produce an actionable rule. Missing/unknown defaults to
    running distillation (fail-open)."""

    if fix_class == "model_variability":
        logger.info("Skipping LLM distillation for model_variability failure (not diagnosable)")
        return ""

    attempts = []
    if os.environ.get("OPENROUTER_API_KEY"):
        attempts.append({
            "model": CLOUD_MODEL,
            "messages": [{"role": "user", "content": distiller_content}],
            "max_tokens": DISTILLER_MAX_TOKENS_CLOUD,
            "timeout": 90.0,
        })
    if os.environ.get("GROQ_API_KEY"):
        attempts.append({
            "model": "groq/llama-3.3-70b-versatile",
            "messages": [{"role": "user", "content": distiller_content}],
            "max_tokens": DISTILLER_MAX_TOKENS_CLOUD,
            "timeout": 90.0,
        })
    if os.environ.get("NVIDIA_API_KEY"):
        attempts.append({
            "model": "nvidia_nim/meta/llama-3.1-70b-instruct",
            "messages": [{"role": "user", "content": distiller_content}],
            "max_tokens": DISTILLER_MAX_TOKENS_CLOUD,
            "timeout": 90.0,
        })
    if os.environ.get("GEMINI_API_KEY"):
        attempts.append({
            "model": "gemini/gemini-2.0-flash",
            "messages": [{"role": "user", "content": distiller_content}],
            "max_tokens": DISTILLER_MAX_TOKENS_CLOUD,
            "timeout": 90.0,
        })
    if os.environ.get("OPENAI_API_KEY"):
        api_base = os.getenv("OPENAI_API_BASE", "https://api.opencode.go/v1")
        api_key = os.environ["OPENAI_API_KEY"]
        attempts.append({
            "model": "deepseek-chat",
            "messages": [{"role": "user", "content": distiller_content}],
            "api_base": api_base,
            "api_key": api_key,
            "custom_llm_provider": "openai",
            "max_tokens": DISTILLER_MAX_TOKENS_CLOUD,
            "timeout": 90.0,
        })
    attempts.append({
        "model": "qwen3.5-0.8b",
        "messages": [
            {"role": "system", "content": "/no_think\n\n"},
            {"role": "user", "content": distiller_content},
        ],
        "api_base": "http://127.0.0.1:8084/v1",
        "api_key": "llama",
        "custom_llm_provider": "openai",
        "max_tokens": DISTILLER_MAX_TOKENS_LOCAL,
        "timeout": 120.0,
    })

    last_exc = None
    for cfg in attempts:
        try:
            async with asyncio.timeout(cfg["timeout"]):
                res = await acompletion(**cfg)
            content = res.choices[0].message.content or ""
            if content.strip():
                try:
                    from runtime_v2.services.usage_log import record_response
                    record_response(res, cfg.get("model", ""), source="distill")
                except Exception as usage_err:  # noqa: BLE001
                    logger.debug("usage log skipped: %s", usage_err)
                return content
            logger.warning("Distiller returned empty content from %s", cfg["model"])
        except Exception as e:
            last_exc = e
            msg = str(e)
            logger.warning("Distiller via %s failed: %s", cfg["model"], e)
            # Retry cloud models with fewer tokens on 402 credit exhaustion
            if "can only afford" in msg and cfg.get("max_tokens", 0) > 128:
                import re
                match = re.search(r"can only afford (\d+)", msg)
                if match:
                    affordable = int(match.group(1))
                    clamped = max(128, min(affordable - 64, 512))
                    logger.warning("Distiller retrying %s with max_tokens=%d", cfg["model"], clamped)
                    cfg_copy = dict(cfg)
                    cfg_copy["max_tokens"] = clamped
                    try:
                        async with asyncio.timeout(cfg_copy["timeout"]):
                            res = await acompletion(**cfg_copy)
                        content = res.choices[0].message.content or ""
                        if content.strip():
                            try:
                                from runtime_v2.services.usage_log import record_response
                                record_response(res, cfg.get("model", ""), source="distill_retry")
                            except Exception as usage_err:  # noqa: BLE001
                                logger.debug("usage log skipped: %s", usage_err)
                            return content
                    except Exception as exc:
                        # BUG FIX: was silently swallowing the 402 token-reduction
                        # retry failure with no log. Now debug-logged.
                        logger.debug("402 retry with fewer tokens failed for %s: %s", cfg["model"], exc)
            # 402 = OpenRouter credit exhaustion — fall through to local qwen3.5-4b.
            # With the MTP 4B at ~15 t/s (vs old 5 t/s) the local fallback is viable.
    if last_exc:
        raise last_exc
    return ""


async def run_reflection():
    try:
        latest_failure = await asyncio.to_thread(get_latest_failure, DIARY_PATH)
    except FileNotFoundError:
        logger.info("No organism diary found. Skipping reflection.")
        return
    
    if not latest_failure:
        logger.info("No failures detected in diary.")
        return
        
    task_desc = latest_failure.get("task", "Unknown Task")
    content = latest_failure.get("content_preview", "")
    error_msg = latest_failure.get("error")
    component = str(latest_failure.get("component") or latest_failure.get("agent") or "unknown")
    fix_class = latest_failure.get("fix_class")
    if fix_class is None:
        try:
            from swarm_os.healing.diagnostician import Diagnostician
            hypotheses = Diagnostician().diagnose({"detail": error_msg or "", "component": component})
            if hypotheses:
                fix_class = hypotheses[0].get("fix_class")
        except Exception as exc:
            # BUG FIX: was silently swallowing diagnostician failures; now debug-logged
            # so we can tell when the Diagnostician import/diagnose path breaks.
            logger.debug("Diagnostician failed during reflection: %s", exc)

    logger.info(f"Distilling failure: {error_msg}")
    
    distiller_content = DISTILLER_PROMPT.format(task_description=task_desc, component=component, content_preview=content, error_message=error_msg)
    
    try:
        rule_full = await _distill(distiller_content, fix_class=fix_class)

        # Parse structured reflection; fall back to extracting any rule text.
        def _field(tag: str) -> str:
            m = re.search(rf"<{tag}>(.*?)</{tag}>", rule_full, re.DOTALL)
            return m.group(1).strip() if m else ""

        failure_summary = _field("failure_summary")
        root_cause = _field("root_cause")
        next_rules = _field("next_attempt_rules")
        do_not = _field("do_not_repeat")

        # Compose the stored correction from the structured fields.
        parts = []
        if failure_summary:
            parts.append(f"Failure: {failure_summary}")
        if root_cause:
            parts.append(f"Root cause: {root_cause}")
        if next_rules:
            parts.append(f"Next-attempt rules: {next_rules}")
        if do_not:
            parts.append(f"Do NOT repeat: {do_not}")
        correction = " | ".join(parts) if parts else None

        if not correction:
            legacy = re.search(r"<new_rule>(.*?)</new_rule>", rule_full, re.DOTALL)
            correction = legacy.group(1).strip() if legacy else (rule_full or "").strip()[:500]
        if not correction:
            logger.warning("No rule generated by distiller.")
            return

        logger.info(f"Distilled new rule: {correction}")

        svc = get_reflection_service()
        await svc.store_reflexion(task_desc, content, error_msg, correction, component=component)
        logger.info("Successfully saved reflexion rule to Qdrant ReflexionMemory.")
        
    except Exception as e:
        logger.error(f"Distiller phase failed: {e}")

if __name__ == "__main__":
    asyncio.run(run_reflection())

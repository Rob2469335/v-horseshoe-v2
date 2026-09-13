"""Optional OpenTelemetry (GenAI semantic conventions) export for LLM calls.

Opt-in via ``SWARM_OTEL=1``. **No required dependency**: if the `opentelemetry`
SDK is installed it emits a real span (`gen_ai.*` attributes, OTLP-exportable);
otherwise it writes a `gen_ai.*`-shaped JSONL record that any collector can
ingest. Fully fail-open — telemetry must never break the call path.

Semantic conventions (per OpenTelemetry GenAI + Nango/MintMCP/OneUptime docs):
``gen_ai.operation.name``, ``gen_ai.request.model``, ``gen_ai.provider.name``,
``gen_ai.usage.input_tokens``, ``gen_ai.usage.output_tokens``,
``gen_ai.usage.cost``. Output tokens are kept separate from input (they bill
3-5x), so cost is reconstructable.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path

log = logging.getLogger(__name__)

_write_lock = threading.Lock()
_telemetry_path = Path(os.getenv("SWARM_OTEL_FILE", "data/usage/otel_genai.jsonl"))
_tracer = None
_tracer_ready = False

_TRUTHY = ("1", "true", "yes", "on")


def _enabled() -> bool:
    return os.getenv("SWARM_OTEL", "0").strip().lower() in _TRUTHY


def _get_tracer():
    """Lazily resolve an OTel tracer; None when the SDK is unavailable."""
    global _tracer, _tracer_ready
    if _tracer_ready:
        return _tracer
    _tracer_ready = True
    try:
        from opentelemetry import trace  # type: ignore

        _tracer = trace.get_tracer("swarm_os")
    except Exception as e:  # noqa: BLE001
        log.debug("opentelemetry SDK unavailable (%s); using JSONL fallback", e)
        _tracer = None
    return _tracer


def _genai_attributes(
    model: str,
    provider: str,
    prompt_tokens: int,
    completion_tokens: int,
    cost,
    source: str,
    agent_id: str,
) -> dict:
    attrs = {
        "gen_ai.operation.name": "chat",
        "gen_ai.request.model": model or "",
        "gen_ai.provider.name": provider or "",
        "gen_ai.usage.input_tokens": int(prompt_tokens or 0),
        "gen_ai.usage.output_tokens": int(completion_tokens or 0),
        "swarm.source": source or "",
        "swarm.agent_id": agent_id or "",
    }
    if cost is not None:
        attrs["gen_ai.usage.cost"] = float(cost)
    return attrs


def emit_llm_span(
    model: str = "",
    provider: str = "",
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    cost=None,
    source: str = "",
    agent_id: str = "",
) -> None:
    """Emit one GenAI-convention span for an LLM call. Never raises.

    Enabled only when ``SWARM_OTEL`` is truthy. Uses the OTel SDK when present,
    otherwise appends a ``gen_ai.*``-shaped JSONL record.
    """
    if not _enabled():
        return
    try:
        attrs = _genai_attributes(
            model, provider, prompt_tokens, completion_tokens, cost, source, agent_id
        )
        tracer = _get_tracer()
        if tracer is not None:
            with tracer.start_as_current_span("gen_ai.chat") as span:
                for key, value in attrs.items():
                    span.set_attribute(key, value)
            return
        record = {"ts": time.time(), "name": "gen_ai.chat", "attributes": attrs}
        line = json.dumps(record, ensure_ascii=False) + "\n"
        with _write_lock:
            _telemetry_path.parent.mkdir(parents=True, exist_ok=True)
            with _telemetry_path.open("a", encoding="utf-8") as fh:
                fh.write(line)
                fh.flush()
    except Exception as e:  # noqa: BLE001
        log.debug("otel emit skipped: %s", e)

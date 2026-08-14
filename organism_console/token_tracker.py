# organism_console/token_tracker.py
"""
Real-time token + spend + model availability tracker.
Tracks 4 provider buckets: llama-local, llama-cloud, openrouter, nvidia
Polls every 30s: Local LLM /v1/models for loaded models, OpenRouter /auth/key for spend.
"""
import os
import time
import threading
import requests
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

_lock = threading.Lock()

_PROVIDER_KEYS = ("ollama_local", "ollama_cloud", "openrouter_free", "openrouter_paid", "nvidia", "deepseek", "openai_paid")

# Per-provider token accumulators (session)
_tok = {p: {"in": 0, "out": 0, "est": 0, "calls": 0} for p in _PROVIDER_KEYS}

_state = {
    # Last active model + provider
    "last_model":    "—",
    "last_provider": "—",

    # Ollama: currently loaded models from /api/ps
    "ollama_loaded": [],        # list of model name strings
    "ollama_poll_ok": False,

    # OpenRouter account spend
    "or_used_today":  0.0,
    "or_used_total":  0.0,
    "or_limit":       None,
    "or_remaining":   None,
    "or_poll_ok":     False,

    # NVIDIA NIM — no spend API, just track calls/tokens locally
    "nvidia_poll_ok": True,
}

_POLL_INTERVAL = 30  # seconds


# ── Provider classifier ───────────────────────────────────────────────────────
def _classify_provider(model: str, chunk_provider: str | None = None) -> str:
    """Map model name or chunk provider tag to internal bucket.

    Mirrors the backend's provider naming so the tracker counts real traffic
    (local llama.cpp, DeepSeek direct/OpenRouter, NVIDIA NIM, ultra-cheap Ling).
    """
    p = (chunk_provider or "").lower()
    m = (model or "").lower()

    if p in ("nvidia", "nvidia_nim") or m.startswith("nvidia") or "nemotron" in m or "nvidia_nim/" in m:
        return "nvidia"

    if m.startswith("deepseek/"):
        return "deepseek"

    if m.startswith("openai/"):
        name = m.replace("openai/", "")
        if name.startswith("zen/"):
            return "openai_paid"
        if name.startswith("gpt") or name.startswith("o1") or name.startswith("o3") or "deepseek" in name:
            return "openai_paid"
        return "ollama_local"

    if p == "openrouter" or m.startswith("openrouter/"):
        if m.endswith(":free") or "free" in m:
            return "openrouter_free"
        return "openrouter_paid"

    if "480b-cloud" in m or "cloud" in m:
        return "ollama_cloud"
    return "ollama_local"


# ── Ollama poller ─────────────────────────────────────────────────────────────
def _poll_ollama():
    try:
        r = requests.get("http://127.0.0.1:8080/v1/models", headers={"Authorization": "Bearer llama"}, timeout=5)
        if r.status_code == 200:
            models = r.json().get("data", [])
            names = [m.get("id", "") for m in models if m.get("id")]
            with _lock:
                _state["ollama_loaded"] = names
                _state["ollama_poll_ok"] = True
            return
    except Exception:
        pass
    with _lock:
        _state["ollama_loaded"] = []
        _state["ollama_poll_ok"] = False


# ── OpenRouter poller ─────────────────────────────────────────────────────────
def _poll_openrouter():
    api_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not api_key:
        return
    try:
        r = requests.get(
            "https://openrouter.ai/api/v1/auth/key",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=8,
        )
        if r.status_code == 200:
            d = r.json().get("data", {})
            with _lock:
                _state["or_used_total"] = float(d.get("usage", 0) or 0)
                _state["or_used_today"] = float(d.get("usage_daily", 0) or 0)
                limit = d.get("limit")
                _state["or_limit"]     = float(limit) if limit is not None else None
                _state["or_remaining"] = (float(limit) - _state["or_used_total"]) if limit is not None else None
                _state["or_poll_ok"]   = True
            return
    except Exception:
        pass
    with _lock:
        _state["or_poll_ok"] = False


# ── Background loop ───────────────────────────────────────────────────────────
def _background_loop():
    while True:
        _poll_ollama()
        _poll_openrouter()
        time.sleep(_POLL_INTERVAL)


def start_background_poll():
    _poll_openrouter()
    _poll_ollama()
    t = threading.Thread(target=_background_loop, daemon=True)
    t.start()


# ── Record chunk (called every SSE chunk in cli.py) ──────────────────────────
def seed_model_if_empty(model: str):
    """Seed the tracker's 'last model' display with the CLI's active model when
    there has been NO live traffic yet. Without this the banner's TRACKER row
    shows `mdl:—` until the first real model chunk arrives — misleading at
    startup, where the model IS known from /agents/models."""
    with _lock:
        if not _state["last_model"] and model:
            _state["last_model"] = model
            _state["last_provider"] = _classify_provider(model, "")


def record_chunk(chunk: dict):
    model    = chunk.get("model", "")
    provider = chunk.get("provider", "")
    bucket   = _classify_provider(model, provider)

    with _lock:
        if model and model not in ("health-check", "delegation-guard"):
            _state["last_model"]    = model
            _state["last_provider"] = bucket

        usage = chunk.get("usage")
        if isinstance(usage, dict):
            _tok[bucket]["in"]    += int(usage.get("prompt_tokens", 0) or 0)
            _tok[bucket]["out"]   += int(usage.get("completion_tokens", 0) or 0)
            _tok[bucket]["calls"] += 1
            return

        content = chunk.get("content", "")
        if content:
            _tok[bucket]["est"]   += max(1, len(content) // 4)
            _tok[bucket]["calls"] += 1


# ── Status segment builder ────────────────────────────────────────────────────
def get_status_segment() -> str:
    with _lock:
        s   = dict(_state)
        tok = {p: dict(v) for p, v in _tok.items()}

    lines = []

    # ── Row 1: active model + provider ───────────────────────────────────────
    mdl_color = {
        "ollama_local":    "cyan",
        "ollama_cloud":    "bright_cyan",
        "openrouter_free": "green",
        "openrouter_paid": "yellow",
        "nvidia":          "bright_green",
        "deepseek":        "bright_cyan",
        "openai_paid":     "bright_yellow",
    }.get(s["last_provider"], "white")

    lines.append(
        f"[bold blue]mdl[/bold blue]:[{mdl_color}]{s['last_model']}[/{mdl_color}] "
        f"[dim]({s['last_provider']})[/dim]"
    )

    # ── Row 2: per-provider token counters ────────────────────────────────────
    segs = []
    labels = {
        "ollama_local":    "loc",
        "ollama_cloud":    "cld",
        "openrouter_free": "or✓",
        "openrouter_paid": "or$",
        "nvidia":          "nv",
        "deepseek":        "ds",
        "openai_paid":     "oai",
    }
    for p in _PROVIDER_KEYS:
        t   = tok[p]
        out = t["out"] or t["est"]
        inp = t["in"]
        if inp == 0 and out == 0:
            continue
        total = inp + out
        col   = "green" if total < 8_000 else "yellow" if total < 24_000 else "red"
        segs.append(f"[bold]{labels[p]}[/bold]:[{col}]▲{inp}▼{out}[/{col}]")

    if segs:
        lines.append("[bold blue]tok[/bold blue]: " + "  ".join(segs))
    else:
        lines.append("[bold blue]tok[/bold blue]:[dim]no traffic yet[/dim]")

    # ── Row 3: Llama.cpp loaded models ───────────────────────────────────────────
    if s["ollama_poll_ok"]:
        loaded = s["ollama_loaded"]
        if loaded:
            loaded_str = "  ".join(f"[cyan]{m}[/cyan]" for m in loaded)
            lines.append(f"[bold blue]llamacpp[/bold blue]: {loaded_str}")
        else:
            lines.append("[bold blue]llamacpp[/bold blue]:[dim]no models loaded[/dim]")
    else:
        lines.append("[bold blue]llamacpp[/bold blue]:[dim]unreachable[/dim]")

    # ── Row 4: OpenRouter spend ───────────────────────────────────────────────
    if s["or_poll_ok"]:
        today = s["or_used_today"]
        total = s["or_used_total"]
        limit = s["or_limit"]
        rem   = s["or_remaining"]
        frac  = (total / limit) if limit else 0
        col   = "green" if frac < 0.6 else "yellow" if frac < 0.85 else "red"
        lim_s = f"/${limit:.2f}" if limit is not None else ""
        rem_s = f" rem=[bold]${rem:.4f}[/bold]" if rem is not None else ""
        lines.append(
            f"[bold blue]or$[/bold blue]:[{col}]today=${today:.4f} "
            f"total=${total:.4f}{lim_s}{rem_s}[/{col}]"
        )
    else:
        or_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
        lines.append(
            "[bold blue]or$[/bold blue]:[dim]no key[/dim]"
            if not or_key else
            "[bold blue]or$[/bold blue]:[yellow]polling...[/yellow]"
        )

    # ── Row 5: NVIDIA token summary ───────────────────────────────────────────
    nv = tok["nvidia"]
    nv_out = nv["out"] or nv["est"]
    if nv["calls"] > 0:
        nv_col = "green" if (nv["in"] + nv_out) < 8_000 else "yellow"
        lines.append(
            f"[bold blue]nvidia[/bold blue]:[{nv_col}]"
            f"calls={nv['calls']} ▲{nv['in']}▼{nv_out}[/{nv_col}]"
        )

    return "\n".join(lines)


# ── Reset session counters ────────────────────────────────────────────────────
def reset_session():
    with _lock:
        for p in _PROVIDER_KEYS:
            _tok[p] = {"in": 0, "out": 0, "est": 0, "calls": 0}
        _state["last_model"]    = "—"
        _state["last_provider"] = "—"

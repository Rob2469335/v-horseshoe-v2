import time
import os
import requests
import psutil
import threading
import copy
from rich.table import Table
from rich.panel import Panel

from organism_console.config import BACKEND_URL, START_TIME
from swarm_os.config.settings import settings
from organism_console.token_tracker import get_status_segment

_WEATHER_CACHE = "Weather: [dim]Syncing...[/dim]"
_WEATHER_LAST_FETCH = 0
_WEATHER_LOCK = threading.Lock()

_BANNER_CACHE: dict = {"agents": None, "status": None, "agent_models": None, "last": 0}
_BANNER_LOCK = threading.Lock()

def get_system_stats():
    ram = psutil.virtual_memory()
    cpu = psutil.cpu_percent(interval=None)
    return {
        "cpu": cpu,
        "ram_pct": ram.percent,
        "ram_used_gb": ram.used / (1024**3),
        "ram_total_gb": ram.total / (1024**3),
        "ram_color": "green" if ram.percent < 70 else "yellow" if ram.percent < 85 else "red"
    }

def _refresh_banner_cache():
    global _BANNER_CACHE
    try:
        agents_resp = requests.get(f"{BACKEND_URL}/agents", timeout=15, verify=settings.ssl_verify)
        status_resp = requests.get(f"{BACKEND_URL}/status", timeout=15, verify=settings.ssl_verify)
        models_resp = requests.get(f"{BACKEND_URL}/agents/models", timeout=15, verify=settings.ssl_verify)
        with _BANNER_LOCK:
            if agents_resp and agents_resp.status_code == 200:
                _BANNER_CACHE["agents"] = agents_resp.json()
            if status_resp and status_resp.status_code == 200:
                _BANNER_CACHE["status"] = status_resp.json()
            if models_resp and models_resp.status_code == 200:
                _BANNER_CACHE["agent_models"] = models_resp.json()
            _BANNER_CACHE["last"] = time.time()
    except Exception:
        pass

def get_banner_data() -> dict:
    # FIRST render: fetch synchronously so the banner never shows placeholder
    # (None) values on startup — the old code backgrounded the first fetch and
    # returned an empty cache immediately, so CORE/FALLBACKS/TRACKER were always
    # stale on the very banner the user sees. Subsequent renders use the
    # background thread (cheap, non-blocking). NOTE: _refresh_banner_cache takes
    # _BANNER_LOCK itself, so never call it while holding the lock.
    with _BANNER_LOCK:
        first = _BANNER_CACHE["last"] == 0
    if first:
        _refresh_banner_cache()
    with _BANNER_LOCK:
        if not first and time.time() - _BANNER_CACHE["last"] > 5:
            _BANNER_CACHE["last"] = time.time()
            threading.Thread(target=_refresh_banner_cache, daemon=True).start()
        return copy.deepcopy(_BANNER_CACHE)

def fetch_weather_bg(city=None):
    global _WEATHER_CACHE
    city = city or os.getenv("ZENITH_WEATHER_CITY", "auto")
    try:
        lat, lon = None, None
        
        # 1. Geocode
        if city == "auto":
            geo_resp = requests.get("http://ip-api.com/json/", timeout=2).json()
            city_name = geo_resp.get("city", "auto")
            lat, lon = geo_resp.get("lat"), geo_resp.get("lon")
        else:
            geo_resp = requests.get(f"https://geocoding-api.open-meteo.com/v1/search?name={city}&count=1", timeout=2).json()
            results = geo_resp.get("results")
            if results:
                lat, lon = results[0]["latitude"], results[0]["longitude"]
                city_name = results[0]["name"]
            else:
                raise ValueError("City not found")

        if lat is None or lon is None:
            raise ValueError("Geocoding failed")

        # 2. Get Weather
        weather_url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current=temperature_2m,relative_humidity_2m,weather_code&temperature_unit=fahrenheit"
        w_resp = requests.get(weather_url, timeout=2).json()
        current = w_resp.get("current", {})
        
        temp = round(current.get("temperature_2m", 0))
        hum = current.get("relative_humidity_2m", 0)
        
        # 3. WMO Code mapping
        code = current.get("weather_code", 0)
        desc = "Sunny" if code <= 1 else "Cloudy" if code <= 3 else "Fog" if code <= 49 else "Rain" if code <= 69 else "Snow" if code <= 79 else "Storm"
        
        with _WEATHER_LOCK:
            _WEATHER_CACHE = f"[bold #00ffcc]{city_name} {desc}[/bold #00ffcc] | Temp: [bold #ffaa00]{temp}°F[/bold #ffaa00] | Hum: [bold #00aaff]{hum}%[/bold #00aaff]"
    except Exception:
        with _WEATHER_LOCK:
            _WEATHER_CACHE = "[dim]Weather Offline[/dim]"

def get_weather_stats() -> str:
    global _WEATHER_LAST_FETCH, _WEATHER_CACHE
    with _WEATHER_LOCK:
        if _WEATHER_LAST_FETCH == 0 or time.time() - _WEATHER_LAST_FETCH > 600:
            _WEATHER_LAST_FETCH = time.time()
            threading.Thread(target=fetch_weather_bg, daemon=True).start()
        return _WEATHER_CACHE

_STARTUP_CHECKS_DONE = False

def run_startup_checks(ctx):
    global _STARTUP_CHECKS_DONE
    if _STARTUP_CHECKS_DONE:
        return
    _STARTUP_CHECKS_DONE = True
    
    import concurrent.futures
    services = [
        ("Backend",  f"{BACKEND_URL}/status"),
        ("Swarm API", f"{BACKEND_URL}/readyz"),
    ]

    def check(name, url):
        for attempt in range(2):
            try:
                t0 = time.time()
                r = requests.get(url, timeout=3, verify=settings.ssl_verify)
                ms = int((time.time() - t0) * 1000)
                ok = r.status_code == 200
                extra = ""
                if name == "Swarm API" and ok:
                    try:
                        data = r.json()
                        ready = data.get("ready", False)
                        extra = f" [dim]({'READY' if ready else 'NOT READY'})[/dim]"
                    except Exception:
                        pass
                return name, ok, ms, extra
            except Exception:
                if attempt < 2:
                    time.sleep(3)
        return name, False, 0, ""

    ctx.console.print()
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
        futures = {ex.submit(check, n, u): n for n, u in services}
        for fut in concurrent.futures.as_completed(futures):
            name, ok, ms, extra = fut.result()
            if ok:
                ctx.console.print(f"  [bold green]OK[/bold green]  {name:<12} [dim]{ms}ms{extra}[/dim]")
            else:
                ctx.console.print(f"  [bold yellow]...[/bold yellow] {name:<12} [dim]checking ...[/dim]")
    ctx.console.print()

def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)

def print_banner(ctx):
    run_startup_checks(ctx)
    stats = get_system_stats()
    
    ping_start = time.time()
    backend_ok = True
    ping_ms = int((time.time() - ping_start) * 1000)
    backend_state = f"[bold #00ffcc]CONNECTED[/bold #00ffcc] [dim]({ping_ms}ms)[/dim]" if backend_ok else "[bold #ff0033]DISCONNECTED[/bold #ff0033]"
    
    uptime_sec = int(time.time() - START_TIME)
    h, rem = divmod(uptime_sec, 3600)
    m, s = divmod(rem, 60)
    uptime_str = f"{h:02d}:{m:02d}:{s:02d}"
    
    tokens = sum(estimate_tokens(m.get("content", "")) for m in ctx.history) if ctx.history else 0
    max_tokens = 16384
    ctx_pct = min(100, int((tokens / max_tokens) * 100))
    bar_width = 15
    filled_ctx = int((ctx_pct / 100) * bar_width)
    ctx_bar = "■" * filled_ctx + "□" * (bar_width - filled_ctx)
    ctx_color = "#00ffcc" if ctx_pct < 70 else "#ffaa00" if ctx_pct < 90 else "#ff0033"
    
    mode_style = "bold #00ffcc" if ctx.mode == "safe" else "bold #ff9900"
    
    ram_pct = stats['ram_pct']
    filled_ram = int(ram_pct / 100 * bar_width)
    ram_bar = "■" * filled_ram + "□" * (bar_width - filled_ram)
    
    i = ctx.total_input_tokens
    o = ctx.total_output_tokens
    
    table = Table.grid(padding=(0, 4))
    table.add_column(style="bold #ff00ea", justify="right")
    table.add_column()

    banner_data = get_banner_data()
    agents = banner_data.get("agents") or []
    # Real agent→model resolution from /agents/models (qwen3.5-4b, etc.) — NOT
    # the stale role names ("reasoning"/"fast") or a persisted active_model that
    # may no longer be served.
    agent_models = banner_data.get("agent_models") or {}
    if agents:
        for a in agents:
            agent_id = a.get("id", "").upper()
            resolved = (agent_models.get(a.get("id", ""), {}) or {}).get("model")
            model = resolved or a.get("model_role", "") or ctx.active_model
            if agent_id.lower() == ctx.active_agent.lower():
                table.add_row(f"[bold white]{agent_id}[/bold white]", f"[bold #00f0ff]{model}[/bold #00f0ff] [bold #00ffcc](ACTIVE)[/bold #00ffcc]")
            else:
                table.add_row(f"{agent_id}", f"[#00f0ff]{model}[/#00f0ff]")
    else:
        table.add_row("AGENT", f"[bold #ffffff]{ctx.active_agent.upper()}[/bold #ffffff]")
        resolved_core = (agent_models.get(ctx.active_agent, {}) or {}).get("model") or ctx.active_model
        table.add_row("CORE", f"[#00f0ff]{resolved_core}[/#00f0ff]")
    table.add_row("SECURE", f"[{mode_style}]{ctx.mode.upper()}[/{mode_style}]")
    table.add_row("UPLINK", backend_state)
    table.add_row("UPTIME", f"[dim]{uptime_str}[/dim]")
    table.add_row("TOKENS", f"[dim]IN[/dim] [#ffaa00]{i:,}[/#ffaa00] [dim]OUT[/dim] [#00f0ff]{o:,}[/#00f0ff]")

    fallbacks_data = {}
    status_json = banner_data.get("status") or {}
    if status_json:
        fallbacks_data = status_json.get("fallback_pool", {})

    cloud_status = "[bold #00ffcc]\\[ON][/bold #00ffcc]" if ctx.cloud_enabled else "[bold #ff00ea]\\[OFF][/bold #ff00ea]"

    # HONEST CLOUD ROW: the console token counters (cloud_input_tokens/quota)
    # are a local per-session estimate, NOT a real limit — showing "235,031/100,000"
    # as a 235% quota bar is misleading. Show the REAL persisted usage_log cost
    # (the same source the /tokens command reads), falling back to "n/a" when
    # there is no data yet.
    try:
        from runtime_v2.services.usage_log import usage_report
        _rpt = usage_report(days=30)
        known = _rpt.get("known_cost") or 0.0
        unknown = _rpt.get("unknown_cost") or 0.0
        _cost_s = f"${known + unknown:.4f}"
    except Exception:
        _cost_s = "[dim]n/a[/dim]"
    table.add_row("CLOUD", f"{cloud_status} [dim]30d cost[/dim] [bold #00f0ff]{_cost_s}[/bold #00f0ff]")
    
    if fallbacks_data:
        total_f = fallbacks_data.get("total", 0)
        orr = fallbacks_data.get("openrouter", 0)
        grq = fallbacks_data.get("groq", 0)
        gem = fallbacks_data.get("gemini", 0)
        nvd = fallbacks_data.get("nvidia", 0)
        dsk = fallbacks_data.get("deepseek", 0)
        ocd = fallbacks_data.get("opencode", 0)
        table.add_row(
            "FALLBACKS",
            f"[bold #00f0ff]{total_f} READY[/bold #00f0ff] [dim](DeepSeek: {dsk}, OpenCode: {ocd}, NVIDIA: {nvd}, OpenRouter: {orr}, Groq: {grq}, Gemini: {gem})[/dim]",
        )
    else:
        table.add_row("FALLBACKS", "[dim]Checking status...[/dim]")

    table.add_row("ACCEL", "[bold #00f0ff]ARC iGPU[/bold #00f0ff] [dim][ACTIVE][/dim]")
    table.add_row("CONTEXT", f"[{ctx_color}]{ctx_pct}%[/{ctx_color}] [dim][{ctx_bar}] ({tokens}/{max_tokens})[/dim]")
    table.add_row("METRICS", f"[dim]CPU[/dim] [#00f0ff]{stats['cpu']:.0f}%[/#00f0ff] [dim]RAM[/dim] [#ff00ea]{stats['ram_used_gb']:.1f}GB[/#ff00ea] [dim][{ram_bar}][/dim]")
    table.add_row("WEATHER", get_weather_stats())
    
    from organism_console.token_tracker import seed_model_if_empty
    resolved_active = (agent_models.get(ctx.active_agent, {}) or {}).get("model") or ctx.active_model
    seed_model_if_empty(resolved_active)

    status_seg = get_status_segment()
    if status_seg:
        table.add_row("TRACKER", status_seg)
    
    def style_node(name: str, color: str) -> str:
        if name.lower() == ctx.active_agent.lower():
            return f"[bold black on {color}] {name} [/]"
        return f"[{color}]{name}[/{color}]"

    n_coord = style_node("COORDINATOR", "#ff00ea")
    n_plan  = style_node("PLANNER", "#00f0ff")
    n_rsch  = style_node("RESEARCHER", "#5555ff")
    n_exec  = style_node("EXECUTOR", "#aaaa00")
    n_coder = style_node("CODER", "#ffaa00")
    n_tool  = style_node("TOOL-RUNNER", "#55ff55")
    n_review= style_node("REVIEWER", "#00f0ff")
    n_debug = style_node("DEBUGGER", "#ff00ea")
    n_maker = style_node("TOOL-MAKER", "#00f0ff")
    n_analyzer = style_node("CODE-ANALYZER", "#5555ff")

    topology = f"""[dim]
      [#00f0ff]USER[/#00f0ff] ──> {n_coord} ──> {n_plan} ──> {n_rsch} ──> {n_exec}
                                                            │
                                                            ├──> {n_coder} ──> {n_tool} ──> {n_review}
                                                            │
                                                            ├──> {n_debug} ──> {n_analyzer}
                                                            │
                                                            └──> {n_maker}[/dim]"""
                     
    table.add_row("TOPOLOGY", topology)
    
    from rich.box import HEAVY
    from rich.align import Align
    from rich.console import Group

    ascii_logo = """[bold #00f0ff]
███████╗███████╗███╗   ██╗██╗████████╗██╗  ██╗
╚══███╔╝██╔════╝████╗  ██║██║╚══██╔══╝██║  ██║
  ███╔╝ █████╗  ██╔██╗ ██║██║   ██║   ███████║
 ███╔╝  ██╔══╝  ██║╚██╗██║██║   ██║   ██╔══██║
███████╗███████╗██║ ╚████║██║   ██║   ██║  ██║
╚══════╝╚══════╝╚═╝  ╚═══╝╚═╝   ╚═╝   ╚═╝  ╚═╝
[/bold #00f0ff][dim #ff00ea]O P E R A T O R   C O N S O L E   //   v2.0[/dim #ff00ea]
"""

    banner_panel = Panel(
        Group(Align.center(ascii_logo), table),
        border_style="bold #00f0ff",
        box=HEAVY,
        expand=False,
        padding=(1, 4)
    )
    
    ctx.console.print()
    ctx.console.print(banner_panel)
    ctx.console.print()

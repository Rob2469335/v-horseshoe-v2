import time
import os
import requests
import psutil
import threading
import copy
from rich.table import Table
from rich.panel import Panel
from rich.box import SIMPLE

from organism_console.config import BACKEND_URL, VERSION, START_TIME
from organism_console.api_client import call_api
from swarm_os.config.settings import settings

_WEATHER_CACHE = "Weather: [dim]Syncing...[/dim]"
_WEATHER_LAST_FETCH = 0
_WEATHER_LOCK = threading.Lock()

_BANNER_CACHE: dict = {"agents": None, "status": None, "last": 0}
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
        with _BANNER_LOCK:
            if agents_resp and agents_resp.status_code == 200:
                _BANNER_CACHE["agents"] = agents_resp.json()
            if status_resp and status_resp.status_code == 200:
                _BANNER_CACHE["status"] = status_resp.json()
            _BANNER_CACHE["last"] = time.time()
    except Exception:
        pass

def get_banner_data() -> dict:
    with _BANNER_LOCK:
        if time.time() - _BANNER_CACHE["last"] > 5:
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
            lat, lon = geo_resp.get("lat"), geo_resp.get("lon")
            city_name = geo_resp.get("city", "auto")
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
            _WEATHER_CACHE = f"[bold #00ffcc]{desc}[/bold #00ffcc] | Temp: [bold #ffaa00]{temp}°F[/bold #ffaa00] | Hum: [bold #00aaff]{hum}%[/bold #00aaff]"
    except Exception as e:
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
        try:
            t0 = time.time()
            r = requests.get(url, timeout=2, verify=settings.ssl_verify)
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
            return name, False, 0, ""

    ctx.console.print()
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
        futures = {ex.submit(check, n, u): n for n, u in services}
        for fut in concurrent.futures.as_completed(futures):
            name, ok, ms, extra = fut.result()
            if ok:
                ctx.console.print(f"  [bold green]OK[/bold green]  {name:<12} [dim]{ms}ms{extra}[/dim]")
            else:
                ctx.console.print(f"  [bold red]FAIL[/bold red] {name:<12} [dim]unreachable[/dim]")
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
    table.add_column(style="bold #555555", justify="right")
    table.add_column()

    banner_data = get_banner_data()
    agents = banner_data.get("agents") or []
    if agents:
        for a in agents:
            agent_id = a.get("id", "").upper()
            model = a.get("model_role", "")
            if agent_id.lower() == ctx.active_agent.lower():
                table.add_row(f"[bold white]{agent_id}[/bold white]", f"[bold #00aaff]{model}[/bold #00aaff] [bold green](ACTIVE)[/bold green]")
            else:
                table.add_row(f"{agent_id}", f"[#00aaff]{model}[/#00aaff]")
    else:
        table.add_row("AGENT", f"[bold #ffffff]{ctx.active_agent.upper()}[/bold #ffffff]")
        table.add_row("CORE", f"[#00aaff]{ctx.active_model}[/#00aaff]")
    table.add_row("SECURE", f"[{mode_style}]{ctx.mode.upper()}[/{mode_style}]")
    table.add_row("UPLINK", backend_state)
    table.add_row("UPTIME", f"[dim]{uptime_str}[/dim]")
    table.add_row("TOKENS", f"[dim]IN[/dim] [#ffaa00]{i:,}[/#ffaa00] [dim]OUT[/dim] [#00ffcc]{o:,}[/#00ffcc]")

    fallbacks_data = {}
    status_json = banner_data.get("status") or {}
    if status_json:
        fallbacks_data = status_json.get("fallback_pool", {})

    cloud_status = "[bold green]\\[ON][/bold green]" if ctx.cloud_enabled else "[bold red]\\[OFF][/bold red]"
    
    c_toks = ctx.cloud_input_tokens + ctx.cloud_output_tokens
    quota = getattr(ctx, "cloud_token_quota", 0)
    q_pct = min(100, int((c_toks / quota) * 100)) if quota > 0 else 0
    q_width = 15
    filled_q = int((q_pct / 100) * q_width)
    q_bar = "■" * filled_q + "□" * (q_width - filled_q)
    q_color = "#00ffcc" if q_pct < 70 else "#ffaa00" if q_pct < 90 else "bold #ff0033 blink"
    table.add_row("CLOUD", f"{cloud_status} [dim]QUOTA[/dim] [{q_color}]{q_pct}%[/{q_color}] [dim][{q_bar}] ({c_toks:,}/{quota:,})[/dim]")
    
    if fallbacks_data:
        total_f = fallbacks_data.get("total", 0)
        orr = fallbacks_data.get("openrouter", 0)
        grq = fallbacks_data.get("groq", 0)
        gem = fallbacks_data.get("gemini", 0)
        nvd = fallbacks_data.get("nvidia", 0)
        table.add_row("FALLBACKS", f"[bold #00ffcc]{total_f} READY[/bold #00ffcc] [dim](OpenRouter: {orr}, Groq: {grq}, Gemini: {gem}, NVIDIA: {nvd})[/dim]")
    else:
        table.add_row("FALLBACKS", "[dim]Checking status...[/dim]")

    table.add_row("ACCEL", "[bold #00ffcc]ARC iGPU[/bold #00ffcc] [dim][ACTIVE][/dim]")
    table.add_row("CONTEXT", f"[{ctx_color}]{ctx_pct}%[/{ctx_color}] [dim][{ctx_bar}] ({tokens}/{max_tokens})[/dim]")
    table.add_row("METRICS", f"[dim]CPU[/dim] [#00ffcc]{stats['cpu']:.0f}%[/#00ffcc] [dim]RAM[/dim] [#ff00ff]{stats['ram_used_gb']:.1f}GB[/#ff00ff] [dim][{ram_bar}][/dim]")
    table.add_row("WEATHER", get_weather_stats())
    
    def style_node(name: str, color: str) -> str:
        if name.lower() == ctx.active_agent.lower():
            return f"[bold black on {color}] {name} [/]"
        return f"[{color}]{name}[/{color}]"

    n_coord = style_node("COORDINATOR", "#ff00ff")
    n_plan = style_node("PLANNER", "#00aaff")
    n_rsch = style_node("RESEARCHER", "#5555ff")
    n_exec = style_node("EXECUTOR", "#aaaa00")
    n_coder = style_node("CODER", "#ffaa00")
    n_tool = style_node("TOOL-RUNNER", "#55ff55")
    n_review = style_node("REVIEWER", "#00ffcc")
    n_debug = style_node("DEBUGGER", "#ff3333")

    topology = f"""[dim]
      [#00ffff]USER[/#00ffff] ──> {n_coord} ──> {n_plan} ──> {n_rsch} ──> {n_exec}
                                                            │
                                                            ├──> {n_coder} ──> {n_tool} ──> {n_review}
                                                            │
                                                            └──> {n_debug}[/dim]"""
                     
    table.add_row("TOPOLOGY", topology)
    
    banner_panel = Panel(
        table,
        title="[bold #00ffff]Z E N I T H[/bold #00ffff] [dim]OS // 2027[/dim]",
        subtitle=f"[dim]v{VERSION}[/dim]",
        border_style="#0055ff",
        box=SIMPLE,
        expand=False,
        padding=(1, 4)
    )
    
    ctx.console.print()
    ctx.console.print(banner_panel)
    ctx.console.print()

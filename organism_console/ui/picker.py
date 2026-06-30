from __future__ import annotations
import requests
from typing import Optional, Tuple
from prompt_toolkit.shortcuts import radiolist_dialog
from prompt_toolkit.styles import Style
from organism_console.config import BACKEND_URL

def fetch_agents() -> list[dict]:
    try:
        import urllib3
        urllib3.disable_warnings()
        r = requests.get(f"{BACKEND_URL}/agents", timeout=3, verify=False)
        return r.json() if r.status_code == 200 else []
    except:
        return []

def fetch_cloud_models() -> list[str]:
    try:
        import urllib3
        urllib3.disable_warnings()
        r = requests.get(f"{BACKEND_URL}/models/cloud", timeout=3, verify=False)
        if r.status_code == 200:
            return r.json().get("models", [])
    except:
        pass
    return []

def push_model_override(agent_id: str, model_name: str, backend: str) -> bool:
    try:
        import urllib3
        urllib3.disable_warnings()
        r = requests.post(
            f"{BACKEND_URL}/agents/{agent_id}/model",
            json={"model_name": model_name, "backend": backend},
            timeout=3,
            verify=False
        )
        return r.status_code == 200
    except:
        return False

def parse_backend(model: str) -> Tuple[str, str]:
    if model.startswith("openrouter/"):
        return "openrouter", model[11:]
    if model.startswith("groq/"):
        return "groq", model[5:]
    if model.startswith("ollama/"):
        return "ollama", model[7:]
    return "ollama", model

def launch_picker(ctx) -> None:
    style = Style.from_dict({
        'dialog': 'bg:#1a1a1a #00ffcc',
        'dialog frame.label': 'bg:#1a1a1a #ffaa00 bold',
        'dialog.body': 'bg:#000000 #ffffff',
        'dialog shadow': 'bg:#000000',
        'button': 'bg:#00aaff #ffffff',
        'button.focused': 'bg:#ffaa00 #000000 bold',
        'radio-selected': 'bg:#00aaff #ffffff bold',
        'radio': 'bg:#000000 #aaaaaa'
    })
    
    agents = fetch_agents()
    if not agents:
        ctx.console.print("[red]Backend offline or no agents found.[/red]")
        return
        
    models = fetch_cloud_models()
    if not models:
        ctx.console.print("[red]Failed to fetch live cloud models from backend.[/red]")
        return
        
    agent_choices = [(a["id"], f"{a['id'].upper():<12} - {a.get('model_role', '')}") for a in agents]
    selected_agent = radiolist_dialog(
        title="ZENITH OS: AGENT ROUTING",
        text="Select the agent you wish to configure:",
        values=agent_choices,
        style=style
    ).run()
    
    if not selected_agent:
        return
        
    # Local fallback/default models for completeness
    local_models = ["ollama/qwen3-coder:480b-cloud", "ollama/qwen2.5-coder:7b", "ollama/llama3-groq-tool-use:8b", "ollama/gemma4:e4b"]
    all_models = models + local_models
    
    model_choices = [(m, m) for m in all_models]
    selected_model_raw = radiolist_dialog(
        title="ZENITH OS: MODEL SELECTION",
        text=f"Select real-time cloud model for [ {selected_agent.upper()} ]:",
        values=model_choices,
        style=style
    ).run()
    
    if not selected_model_raw:
        return
        
    backend, clean_model_name = parse_backend(selected_model_raw)
    
    ctx.console.print(f"[dim]Syncing {selected_agent} model override to backend...[/dim]")
    success = push_model_override(selected_agent, clean_model_name, backend)
    if success:
        from runtime_v2.services import model_registry as _reg
        _reg._AGENT_MODELS[selected_agent] = (clean_model_name, backend)
        ctx.console.print(f"[bold green]✓ LIVE OVERRIDE ACTIVE[/bold green] | {selected_agent.upper()} → [cyan]{clean_model_name}[/cyan] [dim]({backend})[/dim]")
        if hasattr(ctx.state, "reset_router"):
            ctx.state.reset_router()
    else:
        ctx.console.print("[bold red]✗ Failed to sync override to backend.[/bold red]")

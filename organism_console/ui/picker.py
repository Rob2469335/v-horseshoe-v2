from __future__ import annotations
import requests
import json
from pathlib import Path

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Header, Footer, OptionList, Input, Static, Label
from textual.widgets.option_list import Option
from textual.binding import Binding
import urllib3
urllib3.disable_warnings()

from organism_console.config import BACKEND_URL

FAVORITES_FILE = Path(__file__).parent.parent / "config" / "favorites.json"

class ModelPickerApp(App):
    CSS = """
    Screen {
        background: #000000;
    }
    .column {
        width: 1fr;
        height: 100%;
        border: solid #00aaff;
        margin: 1 1;
        padding: 1;
    }
    #specs_panel {
        width: 30%;
        border: solid #ffaa00;
    }
    #search_input {
        margin-bottom: 1;
        background: #111111;
        border: solid #00ffcc;
    }
    .title {
        text-align: center;
        text-style: bold;
        color: #00ffcc;
        margin-bottom: 1;
    }
    OptionList {
        background: #000000;
        color: #ffffff;
    }
    """
    
    BINDINGS = [
        Binding("q", "quit", "Quit", show=True),
        Binding("f", "toggle_favorite", "Toggle Favorite", show=True),
        Binding("enter", "submit", "Select Model", show=True),
    ]

    def __init__(self, ctx):
        super().__init__()
        self.ctx = ctx
        self.agents = []
        self.models = []
        self.favorites = self.load_favorites()
        self.selected_agent_id = None
        
    def load_favorites(self):
        if FAVORITES_FILE.exists():
            try:
                return set(json.loads(FAVORITES_FILE.read_text()))
            except:
                pass
        return set()
        
    def save_favorites(self):
        FAVORITES_FILE.parent.mkdir(exist_ok=True, parents=True)
        FAVORITES_FILE.write_text(json.dumps(list(self.favorites)))
        
    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal():
            with Vertical(classes="column"):
                yield Label("1. Select Agent", classes="title")
                yield OptionList(id="agent_list")
            
            with Vertical(classes="column"):
                yield Label("2. Search Models", classes="title")
                yield Input(placeholder="Fuzzy search models...", id="search_input")
                yield OptionList(id="model_list")
                
            with Vertical(classes="column", id="specs_panel"):
                yield Label("3. Model Specs", classes="title")
                yield Static("Select a model to view specs.", id="specs_display")
        yield Footer()

    def on_mount(self) -> None:
        self.fetch_data()
        self.populate_agents()
        self.populate_models("")
        
    def fetch_data(self):
        try:
            r = requests.get(f"{BACKEND_URL}/agents", timeout=3, verify=False)
            self.agents = r.json() if r.status_code == 200 else []
        except:
            self.agents = []
            
        try:
            r = requests.get(f"{BACKEND_URL}/models/cloud", timeout=3, verify=False)
            if r.status_code == 200:
                self.models = r.json().get("models", [])
        except:
            self.models = []
            
        # Add static fallbacks
        self.models.extend([
            {"model": "ollama/qwen3-coder:480b-cloud", "context_length": 32000, "pricing": "Local", "provider": "Ollama"},
            {"model": "ollama/llama3-groq-tool-use:8b", "context_length": 8192, "pricing": "Local", "provider": "Ollama"},
            {"model": "ollama/qwen2.5-coder:7b", "context_length": 32000, "pricing": "Local", "provider": "Ollama"}
        ])

    def populate_agents(self):
        agent_list = self.query_one("#agent_list", OptionList)
        for a in self.agents:
            agent_list.add_option(Option(f"{a['id'].upper()} - {a.get('model_role', '')}", id=a['id']))

    def sort_models(self, m):
        return (0 if m['model'] in self.favorites else 1, m['model'])

    def populate_models(self, query: str):
        model_list = self.query_one("#model_list", OptionList)
        model_list.clear_options()
        
        filtered = [m for m in self.models if query.lower() in m['model'].lower()]
        filtered.sort(key=self.sort_models)
        
        for m in filtered:
            fav = "★ " if m['model'] in self.favorites else "  "
            model_list.add_option(Option(f"{fav}{m['model']}", id=m['model']))

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "search_input":
            self.populate_models(event.value)

    def on_option_list_option_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        if event.option_list.id == "agent_list":
            self.selected_agent_id = event.option_id
        elif event.option_list.id == "model_list":
            model_id = event.option_id
            specs = next((m for m in self.models if m['model'] == model_id), None)
            display = self.query_one("#specs_display", Static)
            if specs:
                md = f"**Model:**\n[#00ffcc]{specs['model']}[/#00ffcc]\n\n"
                md += f"**Provider:**\n{specs.get('provider', '?')}\n\n"
                md += f"**Context Window:**\n{specs.get('context_length', '?')} tokens\n\n"
                md += f"**Pricing:**\n{specs.get('pricing', '?')}"
                display.update(md)
            else:
                display.update("No specs available.")

    def action_toggle_favorite(self) -> None:
        model_list = self.query_one("#model_list", OptionList)
        if model_list.highlighted is not None:
            opt = model_list.get_option_at_index(model_list.highlighted)
            m_id = opt.id
            m_id_clean = m_id.replace("★ ", "").strip()
            
            if m_id_clean in self.favorites:
                self.favorites.remove(m_id_clean)
            else:
                self.favorites.add(m_id_clean)
            self.save_favorites()
            # Refresh list
            search = self.query_one("#search_input", Input).value
            self.populate_models(search)

    def action_submit(self) -> None:
        agent_list = self.query_one("#agent_list", OptionList)
        model_list = self.query_one("#model_list", OptionList)
        
        if agent_list.highlighted is None or model_list.highlighted is None:
            return
            
        a_id = agent_list.get_option_at_index(agent_list.highlighted).id
        m_id = model_list.get_option_at_index(model_list.highlighted).id
        m_id_clean = m_id.replace("★ ", "").strip()
        
        backend, clean_name = self.parse_backend(m_id_clean)
        
        try:
            requests.post(
                f"{BACKEND_URL}/agents/{a_id}/model",
                json={"model_name": clean_name, "backend": backend},
                timeout=3,
                verify=False
            )
            from runtime_v2.services import model_registry as _reg
            _reg._AGENT_MODELS[a_id] = (clean_name, backend)
            if hasattr(self.ctx.state, "reset_router"):
                self.ctx.state.reset_router()
            self.ctx.console.print(f"[bold green]✓ LIVE OVERRIDE ACTIVE[/bold green] | {a_id.upper()} → [cyan]{clean_name}[/cyan] [dim]({backend})[/dim]")
        except Exception as e:
            self.ctx.console.print(f"[bold red]✗ Failed to sync override to backend:[/bold red] {e}")
            
        self.exit()

    def parse_backend(self, model: str) -> tuple[str, str]:
        if model.startswith("openrouter/"): return "openrouter", model[11:]
        if model.startswith("groq/"): return "groq", model[5:]
        if model.startswith("ollama/"): return "ollama", model[7:]
        return "ollama", model

def launch_picker(ctx):
    app = ModelPickerApp(ctx)
    app.run()

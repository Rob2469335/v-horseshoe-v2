import pathlib

p = pathlib.Path(r'C:\Users\rober\Projects\v-horseshoe-v2\organism_console\cli.py')
src = p.read_text(encoding='utf-8')

old = '''    try:
        models_resp = call_backend("/status")
        if models_resp:
            d = models_resp.json()
            table.add_row("Ollama", f"[green]{\'reachable\' if d.get(\'ollama_reachable\') else \'[red]unreachable\'}[/green]")
            table.add_row("Models", str(d.get("installed_model_count", 0)))
    except Exception:
        pass'''

new = '''    try:
        models_resp = call_backend("/status")
        if models_resp:
            d = models_resp.json()
            ollama_status = "[green]reachable[/green]" if d.get("ollama_reachable") else "[red]unreachable[/red]"
            table.add_row("Ollama", ollama_status)
            table.add_row("Models", str(d.get("installed_model_count", 0)))
    except Exception:
        pass'''

if old in src:
    src = src.replace(old, new)
    print("Fixed OK")
else:
    # Try to find and fix the broken line directly
    broken = 'table.add_row("Ollama", f"[green]{\'reachable\' if d.get(\'ollama_reachable\') else \'[red]unreachable\'}[/green]")'
    fixed = 'ollama_status = "[green]reachable[/green]" if d.get("ollama_reachable") else "[red]unreachable[/red]"\n            table.add_row("Ollama", ollama_status)'
    if broken in src:
        src = src.replace(broken, fixed)
        print("Fixed via direct line OK")
    else:
        print("NOT FOUND - showing context:")
        for i, line in enumerate(src.splitlines()):
            if "ollama_reachable" in line and "table" in line:
                print(f"{i+1}: {repr(line)}")

p.write_text(src, encoding='utf-8')

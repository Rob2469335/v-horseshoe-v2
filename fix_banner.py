import pathlib

p = pathlib.Path(r'C:\Users\rober\Projects\v-horseshoe-v2\organism_console\cli.py')
src = p.read_text(encoding='utf-8')

old = '''def print_banner():
    cpu, ram_pct, ram_used, ram_total = get_resources()
    rc = ram_color(ram_pct)
    console.print()
    console.print(Panel.fit(
        f"[bold bright_cyan]ZENITH[/bold bright_cyan]  [dim white]Swarm OS Organism Console[/dim white]  [dim]v{VERSION}[/dim]\\n"
        f"[dim]RAM [/{rc}][{rc}]{ram_used}/{ram_total}GB ({ram_pct:.0f}%)[/{rc}]  CPU {cpu:.0f}%  "
        f"Backend [green]http://127.0.0.1:8000[/green][/dim]",
        border_style="bright_cyan",
        padding=(0, 2)
    ))
    console.print()'''

new = '''def print_banner():
    cpu, ram_pct, ram_used, ram_total = get_resources()
    rc = ram_color(ram_pct)
    backend_ok = call_backend("/health") is not None
    backend_str = "[green]online[/green]" if backend_ok else "[red]offline[/red]"
    console.print()
    console.print(Rule(f"[bold bright_cyan]ZENITH[/bold bright_cyan]  [dim]Swarm OS v{VERSION}[/dim]", style="bright_cyan"))
    console.print(
        f"  [{rc}]RAM {ram_used}/{ram_total}GB ({ram_pct:.0f}%)[/{rc}]"
        f"  [dim]CPU {cpu:.0f}%[/dim]"
        f"  Backend {backend_str}"
    )
    console.print()'''

if old in src:
    src = src.replace(old, new)
    print("Banner fixed OK")
else:
    print("NOT MATCHED - rewriting banner by line numbers")
    lines = src.splitlines()
    # Find print_banner function
    start = None
    for i, l in enumerate(lines):
        if l.strip() == "def print_banner():":
            start = i
            break
    if start is not None:
        # Find end of function (next def)
        end = start + 1
        while end < len(lines) and (not lines[end].startswith("def ") or end == start):
            end += 1
        lines[start:end] = new.splitlines()
        src = "\n".join(lines)
        print(f"Banner rewritten lines {start}-{end}")
    else:
        print("Could not find print_banner")

p.write_text(src, encoding='utf-8')

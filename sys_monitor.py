#!/usr/bin/env python3
import time
from rich import print, progress
from psutil import cpu_percent, virtual_memory

try:
    with progress.Progress() as p:
        task1 = p.add_task('[cyan]CPU Usage[/cyan]', total=100)
        task2 = p.add_task('[green]RAM Usage[/green]', total=100)
        while true:
            cpu_usage = cpu_percent(interval=1)
            ram_usage = virtual_memory().percent
            p.update(task1, completed=int(cpu_usage))
            p.update(task2, completed=int(ram_usage))
            time.sleep(1)
except KeyboardInterrupt:
    print('[bold red]Monitoring stopped[/bold red]')
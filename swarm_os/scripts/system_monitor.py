import psutil
import time
import os
from datetime import datetime

STATS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stats.txt")

def log_stats():
    """Continuously logs CPU and RAM usage to stats.txt."""
    print(f"Monitoring system metrics. Logging to {STATS_FILE}...")
    
    with open(STATS_FILE, "a", encoding='utf-8') as f:
        f.write(f"--- System Monitor Started at {datetime.now().isoformat()} ---\n")
    
    try:
        while True:
            cpu = psutil.cpu_percent(interval=1.0)
            ram = psutil.virtual_memory()
            
            log_line = f"[{datetime.now().isoformat()}] CPU: {cpu}% | RAM: {ram.percent}% ({ram.used / (1024**3):.2f}GB / {ram.total / (1024**3):.2f}GB)\n"
            
            with open(STATS_FILE, "a", encoding='utf-8') as f:
                f.write(log_line)
                
            time.sleep(4.0)  # Sleep for 4 seconds, plus the 1 second interval = 5 seconds
    except KeyboardInterrupt:
        print("\nMonitoring stopped by user.")

if __name__ == "__main__":
    log_stats()

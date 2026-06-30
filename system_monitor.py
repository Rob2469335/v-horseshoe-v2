#!/usr/bin/env python3
import psutil
import datetime
import json

def get_system_stats():
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cpu_usage = psutil.cpu_percent()
    mem_usage = psutil.virtual_memory().percent
    return {"timestamp": timestamp, "cpu_usage": cpu_usage, "mem_usage": mem_usage}

def write_stats_to_file(stats):
    try:
        with open('stats.txt', 'w') as f:
            json.dump(stats, f)
    except Exception as e:
        print(f"Error writing to file: {e}")

if __name__ == "__main__":
    stats = get_system_stats()
    write_stats_to_file(stats)
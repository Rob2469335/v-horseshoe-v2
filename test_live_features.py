import asyncio
import os
import sys
import time
from dotenv import load_dotenv

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
load_dotenv(override=True)

from runtime_v2.services.fallback_manager import get_live_fallbacks, get_fallback_stats
from organism_console.cli import get_weather_stats, fetch_weather_bg

async def test_fallbacks():
    print("--- Testing Fallback Manager ---")
    start = time.time()
    fallbacks = await get_live_fallbacks()
    duration = time.time() - start
    
    stats = get_fallback_stats()
    print(f"Fetch completed in {duration:.2f}s")
    print(f"Total fallbacks loaded: {len(fallbacks)}")
    print(f"Stats breakdown: {stats}")
    
    if len(fallbacks) == 0:
        print("[FAIL] Fallbacks array is empty!")
        return False
    if "openrouter" not in stats:
        print("[FAIL] Stats missing provider keys!")
        return False
        
    print("[PASS] Fallback Manager is working flawlessly.")
    return True

def test_weather():
    print("\n--- Testing Weather Fetcher ---")
    print("Initial cache state:", get_weather_stats())
    
    print("Running background fetch directly...")
    fetch_weather_bg("London")
    
    result = get_weather_stats()
    print(f"Fetched weather string: {result}")
    
    if "Syncing" in result or "Offline" in result:
        print("[FAIL] Weather fetcher failed to get live data.")
        return False
        
    print("[PASS] Weather fetcher is working flawlessly.")
    return True

async def main():
    f_ok = await test_fallbacks()
    w_ok = test_weather()
    
    if f_ok and w_ok:
        print("\n=== ALL SYSTEMS GREEN ===")
    else:
        print("\n=== SYSTEMS DEGRADED ===")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())

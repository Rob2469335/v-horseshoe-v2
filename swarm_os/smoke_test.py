import httpx
import json
import sys

BASE = "http://127.0.0.1:8000"

def check(label, ok, detail=""):
    status = "✅" if ok else "❌"
    print(f"{status} {label}", f"— {detail}" if detail else "")
    return ok

def run():
    passed = 0
    failed = 0
    client = httpx.Client(timeout=15)

    try:
        r = client.get(f"{BASE}/health")
        ok = r.status_code == 200 and r.json().get("status") == "ok"
        check("GET /health", ok, r.json().get("status"))
        passed += ok; failed += not ok
    except Exception as e:
        check("GET /health", False, str(e)); failed += 1

    try:
        r = client.get(f"{BASE}/traces?limit=3")
        ok = r.status_code == 200 and "traces" in r.json()
        check("GET /traces?limit=3", ok, f"count={r.json().get('count')}")
        passed += ok; failed += not ok
    except Exception as e:
        check("GET /traces?limit=3", False, str(e)); failed += 1

    try:
        chunks = []
        with client.stream("POST", f"{BASE}/agents/step/stream",
                           json={"agent_id": "coordinator",
                                 "prompt": "ping", "history": []}) as r:
            for line in r.iter_lines():
                if line.strip():
                    chunks.append(json.loads(line))
                    break
        ok = r.status_code == 200 and len(chunks) > 0
        check("POST /agents/step/stream", ok, f"got {len(chunks)} chunk(s)")
        passed += ok; failed += not ok
    except Exception as e:
        check("POST /agents/step/stream", False, str(e)); failed += 1

    print(f"\n{passed} passed, {failed} failed")
    sys.exit(0 if failed == 0 else 1)

if __name__ == "__main__":
    run()

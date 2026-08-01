import requests
import json
from datetime import datetime

QDRANT_URL = "http://127.0.0.1:6333"

def export_memories():
    try:
        collections = requests.get(f"{QDRANT_URL}/collections").json()['result']['collections']
    except Exception as e:
        print(f"Error connecting to Qdrant: {e}")
        return

    export_data = {}
    total_memories = 0

    for c in collections:
        name = c['name']
        # Skip purely code index collections if they only want agent memories, but we'll include all non-codebase for now
        if name in ["codebase", "codebase_index"]:
            continue
            
        try:
            # Get points using scroll API (limit to 10000 points per collection)
            res = requests.post(f"{QDRANT_URL}/collections/{name}/points/scroll", json={"limit": 10000, "with_payload": True, "with_vector": False}).json()
            points = res.get('result', {}).get('points', [])
            
            if points:
                export_data[name] = [p.get('payload', {}) for p in points]
                total_memories += len(points)
                print(f"Exported {len(points)} memories from {name}")
                
        except Exception as e:
            print(f"Error exporting {name}: {e}")

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"qdrant_memories_export_{timestamp}.json"
    
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(export_data, f, indent=2, ensure_ascii=False)
        
    print(f"\nSuccessfully exported {total_memories} total memories to {filename}")

if __name__ == "__main__":
    export_memories()

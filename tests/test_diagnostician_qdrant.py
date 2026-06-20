from swarm_os.healing.diagnostician import Diagnostician


def fake_qdrant_search(query: str, top_k: int = 5):
    # return a fake high-score match
    return [{"score": 0.8, "payload": {"text": "similar failure"}}]


def test_diagnostician_boosts_with_qdrant():
    diag = Diagnostician(memory=None, qdrant_search_callable=fake_qdrant_search)
    sym = {"component": "worker", "detail": "Out of memory: OOM killed process"}
    hyps = diag.diagnose(sym)
    # find memory_pressure hypothesis
    mem = next((h for h in hyps if h.get('hypothesis') == 'memory_pressure'), None)
    assert mem is not None
    # confidence should have been boosted above base 0.75
    assert mem.get('confidence', 0) > 0.75

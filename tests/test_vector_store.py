import asyncio
from swarm_os.services.vector_store import VectorStore
import swarm_os.services.vector_store as vector_store_mod

def test_shared_client_rebuilt_on_different_event_loop(monkeypatch):
    """The pooled Qdrant client is bound to the loop that created it; a caller
    on a DIFFERENT (e.g. closed) loop must get a fresh client, not a
    loop-bound corpse ('RuntimeError: Event loop is closed')."""
    
    monkeypatch.setattr(vector_store_mod, "_shared_client", None)
    monkeypatch.setattr(vector_store_mod, "_shared_client_loop", None, raising=False)

    async def _in_loop():
        # Instantiate VectorStore to trigger shared client creation
        vs1 = VectorStore(use_memory=False)
        vs2 = VectorStore(use_memory=False)
        assert vs1.client is vs2.client  # same loop -> reuse
        return vs1.client

    first_client = asyncio.run(_in_loop())
    # a NEW event loop must NOT receive the previous loop's client
    second_client = asyncio.run(_in_loop())
    assert second_client is not first_client

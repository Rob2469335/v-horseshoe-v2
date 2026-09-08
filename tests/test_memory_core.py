"""Tests for runtime_v2.services.memory_core — failure digest + prune fixes."""

from unittest.mock import patch, MagicMock


class TestGetFailureDigest:
    """REVERT-PROOF: missing shards (404) must appear with count=0, not vanish."""

    def test_shard_404_appears_as_zero(self):
        """When Qdrant returns 404 for ALL shards, get_failure_digest must
        include every shard with count=0 instead of silently omitting them.

        Without the fix (no else-clause), the 404'd shards vanish entirely
        from the digest dict."""
        from runtime_v2.services.memory_core import get_failure_digest

        def fake_get(url, timeout=3.0):
            resp = MagicMock()
            resp.status_code = 404
            resp.json.return_value = {"status": "error"}
            return resp

        with patch(
            "runtime_v2.services.memory_core.requests.get", side_effect=fake_get
        ):
            digest = get_failure_digest()

        # ALL 6 shards were 404'd → every one must appear with count=0
        assert len(digest["shards"]) == 6, (
            f"Expected 6 shards in digest, got {len(digest['shards'])}. "
            "Missing shards indicate the 404 path doesn't populate count=0."
        )
        for shard, count in digest["shards"].items():
            assert count == 0, f"shard '{shard}' should be 0 on 404, got {count}"


class TestKgCap:
    def test_evicts_oldest_nodes_past_ceiling(self):
        import runtime_v2.services.memory_core as mc
        import networkx as nx

        g = nx.DiGraph()
        for i in range(10):
            g.add_node(f"n{i}", timestamp=i)
        old_max = mc._MAX_KG_NODES
        mc._MAX_KG_NODES = 5
        try:
            mc._kg = g
            mc._cap_kg()
        finally:
            mc._MAX_KG_NODES = old_max
        assert sorted(g.nodes()) == ["n5", "n6", "n7", "n8", "n9"]

    def test_no_eviction_below_ceiling(self):
        import runtime_v2.services.memory_core as mc
        import networkx as nx

        g = nx.DiGraph()
        for i in range(3):
            g.add_node(f"c{i}", timestamp=i)
        mc._kg = g
        mc._cap_kg()
        assert g.number_of_nodes() == 3


    def test_save_kg_caps_over_ceiling(self, tmp_path):
        """REVERT-PROOF: _save_kg must enforce the node ceiling (the integration
        point the cap is wired into). Removing the _cap_kg() call in _save_kg
        must fail this test."""
        import runtime_v2.services.memory_core as mc
        import networkx as nx

        g = nx.DiGraph()
        for i in range(12):
            g.add_node(f"k{i}", timestamp=i)
        old_file = mc._kg_file
        old_max = mc._MAX_KG_NODES
        mc._kg = g
        mc._kg_file = str(tmp_path / "kg.json")
        mc._MAX_KG_NODES = 6
        try:
            mc._save_kg()
        finally:
            mc._kg_file = old_file
            mc._MAX_KG_NODES = old_max
        assert g.number_of_nodes() <= 6, "save path must cap the graph size"

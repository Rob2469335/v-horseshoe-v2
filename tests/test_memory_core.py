"""Tests for runtime_v2.services.memory_core — failure digest + prune fixes."""

from unittest.mock import patch, MagicMock
from types import SimpleNamespace


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

        with patch("runtime_v2.services.memory_core.requests.get", side_effect=fake_get):
            digest = get_failure_digest()

        # ALL 6 shards were 404'd → every one must appear with count=0
        assert len(digest["shards"]) == 6, (
            f"Expected 6 shards in digest, got {len(digest['shards'])}. "
            "Missing shards indicate the 404 path doesn't populate count=0."
        )
        for shard, count in digest["shards"].items():
            assert count == 0, f"shard '{shard}' should be 0 on 404, got {count}"

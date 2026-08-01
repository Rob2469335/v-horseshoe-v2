"""Tests for the autonomous-repair constitutional guards (path allowlist,
anti-truncation, circuit breaker, and related-test discovery)."""
from pathlib import Path

import organism_console.core.repair_engine as repair_engine


def test_is_repairable_path_allows_source_trees():
    for p in ("src/core/foo.py", "swarm_os/memory/memory_bridge.py", "runtime_v2/services/x.py"):
        assert repair_engine._is_repairable_path(Path(p))


def test_is_repairable_path_blocks_sensitive():
    blocked = (
        "tests/test_foo.py",
        "config/settings.py",
        ".env",
        "AGENTS.md",
        "package.json",
        "models/qwen.gguf",
        "swarm_os/healing/distilled_cures.json",
        "swarm_os/healing/generated_tests/test_x.py",
    )
    for p in blocked:
        assert not repair_engine._is_repairable_path(Path(p)), p


def test_is_repairable_path_blocks_non_py():
    assert not repair_engine._is_repairable_path(Path("swarm_os/api/routes.ts"))


def test_anti_truncation_rejects_shrink():
    original = "x" * 1000
    assert repair_engine._anti_truncation_ok(original, "y" * 850)
    assert not repair_engine._anti_truncation_ok(original, "y" * 100)


def test_anti_truncation_bypasses_tiny_files():
    assert repair_engine._anti_truncation_ok("x" * 50, "y")


def test_circuit_breaker_trips_after_three_failures(tmp_path, monkeypatch):
    breaker_file = tmp_path / "repair_breaker.json"
    monkeypatch.setattr(repair_engine, "BREAKER_FILE", breaker_file)
    monkeypatch.setattr(repair_engine, "date", _FakeDate)
    repair_engine._save_breaker({
        "date": _FakeDate.today_iso,
        "repairs": 0,
        "consecutive_failures": 0,
        "open_until": 0.0,
    })

    for _ in range(3):
        allowed, reason = repair_engine._circuit_allows_repair()
        assert allowed, reason
        repair_engine._record_repair_result(False)

    allowed, reason = repair_engine._circuit_allows_repair()
    assert not allowed
    assert "circuit open" in reason


def test_circuit_breaker_daily_cap(tmp_path, monkeypatch):
    breaker_file = tmp_path / "repair_breaker.json"
    monkeypatch.setattr(repair_engine, "BREAKER_FILE", breaker_file)
    monkeypatch.setattr(repair_engine, "MAX_DAILY_REPAIRS", 2)
    monkeypatch.setattr(repair_engine, "date", _FakeDate)
    repair_engine._save_breaker({
        "date": _FakeDate.today_iso,
        "repairs": 0,
        "consecutive_failures": 0,
        "open_until": 0.0,
    })
    assert repair_engine._circuit_allows_repair()[0]
    repair_engine._record_repair_result(True)
    assert repair_engine._circuit_allows_repair()[0]
    repair_engine._record_repair_result(True)
    allowed, reason = repair_engine._circuit_allows_repair()
    assert not allowed
    assert "daily repair cap" in reason


def test_related_test_discovery_finds_governor():
    tests = repair_engine._find_related_tests(Path("swarm_os/healing/governor.py"))
    names = [t.name for t in tests]
    assert any("governor" in n for n in names)


class _FakeDate:
    today_iso = "2099-01-01"

    @classmethod
    def today(cls):
        return cls()

    def isoformat(self):
        return self.today_iso

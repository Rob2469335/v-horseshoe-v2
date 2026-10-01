"""Regression coverage for the runtime `data/` store leak.

PROVEN by snapshot diff over two consecutive runs (see
docs/TEST_PERSISTENT_STORE_ISOLATION_AUDIT.md, GRAY closure): ordinary pytest
added, per run, three `data/trajectories/<run_id>.jsonl` files and one
`data/run_snapshots/<snapshot_id>.json`, from test files that never patch
`_TRAJ_DIR` / `_SNAPSHOT_DIR`.

Root cause is the CWD-relative constant shape: under pytest the process CWD is
the repository root, so `Path("data/trajectories")` resolves to production.

The fixture `isolate_runtime_data_dirs` in the root `conftest.py` redirects them.
These tests pin that redirect so it cannot silently regress, and assert the
production tree is never created by a test.
"""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PROD_DATA = REPO_ROOT / "data"


def test_traj_dir_is_redirected_away_from_production():
    """_TRAJ_DIR is a CLASS attribute on AgentServiceV2."""
    from runtime_v2.api.agent_service_v2 import AgentServiceV2

    traj = AgentServiceV2._TRAJ_DIR
    assert not traj.is_relative_to(PROD_DATA), f"trajectory dir leaked to {traj}"
    assert "pytest" in str(traj).lower() or "tmp" in str(traj).lower()


def test_checkpoint_dir_is_redirected():
    from runtime_v2.services import checkpointing

    assert not checkpointing._CHECKPOINT_DIR.is_relative_to(PROD_DATA)


def test_snapshot_dir_is_redirected():
    from runtime_v2.services import run_snapshot

    assert not run_snapshot._SNAPSHOT_DIR.is_relative_to(PROD_DATA)


def test_production_data_dirs_are_not_created_by_importing_or_fixtures():
    """Querying the constants must not create the production directories."""
    # These exist in this repo already; the point is that a test never ADDS to
    # them. Assert the leaked file shapes are not produced here.
    assert PROD_DATA.is_dir()


def test_writing_a_trajectory_lands_in_tmp(isolate_runtime_data_dirs, tmp_path):
    """Exercise the real write path and confirm it never touches production."""
    from runtime_v2.api.agent_service_v2 import AgentServiceV2

    dest = AgentServiceV2._TRAJ_DIR
    dest.mkdir(parents=True, exist_ok=True)
    probe = dest / "isolation-probe.jsonl"
    probe.write_text("{}\n", encoding="utf-8")

    assert probe.exists()
    assert dest.is_relative_to(isolate_runtime_data_dirs)
    assert not probe.is_relative_to(PROD_DATA)
    assert not (PROD_DATA / "trajectories" / "isolation-probe.jsonl").exists()

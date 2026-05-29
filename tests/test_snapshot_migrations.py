from pathlib import Path
import json

from swarm_os.migrations import migrate_snapshot

FIXTURE = Path("tests/fixtures/snapshot_v1.json")

def test_snapshot_migration_v1_to_current():
    doc = json.loads(FIXTURE.read_text(encoding="utf-8"))
    migrated = migrate_snapshot(doc)

    assert migrated["snapshot_version"] == 4
    assert migrated["generation"] == 3
    assert migrated["organisms"][0]["genome"]["generation"] == 3
    assert "age" not in migrated["organisms"][0]["genome"]





from __future__ import annotations

import argparse
import json

from swarm_os.config.settings import settings
from swarm_os.kernel.snapshot_index import latest_snapshot, list_snapshots
from swarm_os.kernel.status import build_status
from swarm_os.services.simulation_service import SimulationService

from swarm_os.repositories.file_snapshot_repository import FileSnapshotRepository
service = SimulationService(FileSnapshotRepository())

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--resume", type=str, default="")
    parser.add_argument("--resume-latest", action="store_true")
    parser.add_argument("--list-snapshots", action="store_true")
    parser.add_argument("--status", action="store_true")
    args = parser.parse_args()

    if args.list_snapshots:
        for p in list_snapshots():
            print(p)
        return

    if args.resume_latest:
        latest = latest_snapshot()
        if latest is None:
            raise FileNotFoundError("No snapshots found")
        args.resume = str(latest)

    if args.status:
        print(json.dumps(build_status(None, settings.scenario_name), indent=2))
        return

    service.run(resume_path=args.resume)

if __name__ == "__main__":
    main()








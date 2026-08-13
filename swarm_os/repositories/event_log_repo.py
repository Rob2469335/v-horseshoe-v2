import json
import os
from pathlib import Path
from typing import Any, Dict, List, Tuple
import logging

logger = logging.getLogger(__name__)


class EventLogRepository:
    def __init__(
        self,
        event_log_path: Path | str = Path("logs/event_log.jsonl"),
        watermark_path: Path | str = Path("logs/.memory_bridge_offset.json"),
        state_path: Path | str = Path("logs/.memory_bridge_state.json"),
    ):
        self.path = Path(event_log_path)
        self.watermark_path = Path(watermark_path)
        self.state_path = Path(state_path)

    def read_events(
        self, current_offset: int, max_events: int = 0
    ) -> Tuple[List[Dict[str, Any]], int]:
        if not self.path.exists():
            return [], current_offset

        events: List[Dict[str, Any]] = []

        with self.path.open("r", encoding="utf-8") as f:
            f.seek(0, 2)  # Go to end
            end_pos = f.tell()
            if current_offset > end_pos:
                # File was likely truncated/rotated
                current_offset = 0

            f.seek(current_offset)
            # OOM guard: bounded tail read. Without a max, a fresh/rotated file
            # (offset 0) loads the ENTIRE events.jsonl into memory on every boot.
            # When max_events > 0 we only keep the most recent N, still advancing
            # the offset past everything so nothing is re-read.
            if max_events and max_events > 0:
                seen: List[Dict[str, Any]] = []
                for line in f:
                    try:
                        seen.append(json.loads(line))
                    except Exception as exc:
                        logger.debug("Failed to parse event log line: %s", exc)
                        continue
                new_offset = f.tell()
                events = seen[-max_events:]
            else:
                for line in f:
                    try:
                        events.append(json.loads(line))
                    except Exception as exc:
                        logger.debug("Failed to parse event log line: %s", exc)
                        continue
                new_offset = f.tell()

        return events, new_offset

    def load_offset(self) -> int:
        try:
            return json.loads(self.watermark_path.read_text(encoding="utf-8")).get(
                "offset", 0
            )
        except Exception:
            return 0

    def save_offset(self, offset: int) -> None:
        self.watermark_path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_text(
            self.watermark_path,
            json.dumps({"offset": offset}, ensure_ascii=False, indent=2),
        )

    def load_state(self) -> Dict[str, Any]:
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def save_state(self, state: Dict[str, Any]) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_text(
            self.state_path,
            json.dumps(state, ensure_ascii=False, indent=2),
        )


def _atomic_write_text(path: Path, content: str) -> None:
    """Write text atomically: write to a temp sibling, then os.replace.

    Prevents a concurrent crash from leaving a truncated watermark/state file
    that would silently zero the memory-bridge resume offset."""
    tmp = path.with_name(f"{path.name}.tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)

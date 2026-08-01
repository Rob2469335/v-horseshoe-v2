import json
from pathlib import Path
from typing import Any, Dict, List, Tuple

class EventLogRepository:
    def __init__(
        self,
        event_log_path: Path | str = Path("logs/event_log.jsonl"),
        watermark_path: Path | str = Path("logs/.memory_bridge_offset.json"),
        state_path: Path | str = Path("logs/.memory_bridge_state.json")
    ):
        self.path = Path(event_log_path)
        self.watermark_path = Path(watermark_path)
        self.state_path = Path(state_path)

    def read_events(self, current_offset: int) -> Tuple[List[Dict[str, Any]], int]:
        if not self.path.exists():
            return [], current_offset

        events: List[Dict[str, Any]] = []

        with self.path.open("r", encoding="utf-8") as f:
            f.seek(0, 2) # Go to end
            end_pos = f.tell()
            if current_offset > end_pos:
                # File was likely truncated/rotated
                current_offset = 0

            f.seek(current_offset)
            for line in f:
                try:
                    events.append(json.loads(line))
                except Exception:
                    continue
            new_offset = f.tell()

        return events, new_offset

    def load_offset(self) -> int:
        try:
            return json.loads(self.watermark_path.read_text(encoding="utf-8")).get("offset", 0)
        except Exception:
            return 0

    def save_offset(self, offset: int) -> None:
        self.watermark_path.parent.mkdir(parents=True, exist_ok=True)
        self.watermark_path.write_text(
            json.dumps({"offset": offset}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def load_state(self) -> Dict[str, Any]:
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def save_state(self, state: Dict[str, Any]) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(
            json.dumps(state, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

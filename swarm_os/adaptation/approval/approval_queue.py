from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List


class ApprovalQueue:
    def __init__(self, store_path: Path | str | None = None) -> None:
        self.store_path = Path(store_path) if store_path is not None else Path('.data') / 'approvals.json'
        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        self._requests: List[Dict[str, Any]] = []
        self._load()

    def _load(self) -> None:
        try:
            if self.store_path.exists():
                with open(self.store_path, 'r', encoding='utf-8') as fh:
                    self._requests = json.load(fh)
        except Exception:
            self._requests = []

    def _save(self) -> None:
        with open(self.store_path, 'w', encoding='utf-8') as fh:
            json.dump(self._requests, fh, default=str, indent=2)

    def create_request(self, component: str, action: str, reason: str) -> Dict[str, Any]:
        req = {
            "request_id": uuid.uuid4().hex,
            "component": component,
            "action": action,
            "reason": reason,
            "status": "pending",
            "decision_note": "",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        self._requests.append(req)
        self._save()
        return req

    def list_requests(self, status: str | None = None) -> List[Dict[str, Any]]:
        if status is None:
            return list(self._requests)
        return [r for r in self._requests if r.get("status") == status]

    def get_request(self, request_id: str) -> Dict[str, Any] | None:
        for r in self._requests:
            if r.get("request_id") == request_id:
                return r
        return None

    def decide(self, request_id: str, approved: bool, note: str = "") -> Dict[str, Any]:
        req = self.get_request(request_id)
        if req is None:
            raise ValueError(f"request {request_id} not found")
        req["status"] = "approved" if approved else "rejected"
        req["decision_note"] = note
        req["decided_at"] = datetime.now(timezone.utc).isoformat()
        self._save()
        return req

    def mark_executed(self, request_id: str, execution_result: Any | None = None) -> Dict[str, Any]:
        req = self.get_request(request_id)
        if req is None:
            raise ValueError(f"request {request_id} not found")
        req["status"] = "executed"
        req["executed_at"] = datetime.now(timezone.utc).isoformat()
        req["execution_result"] = execution_result
        self._save()
        return req

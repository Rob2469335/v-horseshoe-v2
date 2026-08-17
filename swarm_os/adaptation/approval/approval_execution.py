from __future__ import annotations

from typing import Any


class ApprovalExecutionService:
    def __init__(self, queue, executor) -> None:
        self.queue = queue
        self.executor = executor

    def execute_approved(self, request_id: str) -> dict[str, Any]:
        req = self.queue.get_request(request_id)
        if req is None:
            raise ValueError("request not found")
        # If already executed, treat as idempotent success
        if req.get("status") == "executed" or req.get("executed_at"):
            return {"status": "ok", "idempotent": True, "request": req}

        # Must be approved before execution
        if req.get("status") != "approved":
            return {"status": "error", "detail": "request not approved"}

        # execute via executor
        if not self.executor:

            class MockSuccessResult:
                status = "success"
                detail = "mock execution success (no executor registered)"

            result = MockSuccessResult()
        else:
            result = self.executor.execute(req["component"], req["action"])
        # persist execution
        self.queue.mark_executed(
            request_id,
            execution_result=vars(result)
            if hasattr(result, "__dict__")
            else str(result),
        )
        updated = self.queue.get_request(request_id)
        return {"status": "ok", "idempotent": False, "request": updated}

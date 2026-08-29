"""Task 17: the one error type the SDK raises for a structured Runtime
denial. `RuntimeDeniedError` is a faithful decode of the server's own
decision — the SDK never evaluates policy itself, so this carries exactly
the fields the Runtime (REST, Task 16) put on the wire and nothing else.
"""
from __future__ import annotations


class RuntimeDeniedError(Exception):
    """Raised when a Runtime response carries `"decision": "DENY"`.

    `decision`, `reason_code`, `correlation_id`, and `semantic_snapshot_id`
    are read directly off the server's response body — the SDK does not
    re-derive, validate, or reinterpret them.
    """

    def __init__(
        self,
        *,
        decision: str,
        reason_code: str,
        correlation_id: str | None,
        semantic_snapshot_id: str | None,
    ) -> None:
        super().__init__(f"Runtime denied request: {reason_code}")
        self.decision = decision
        self.reason_code = reason_code
        self.correlation_id = correlation_id
        self.semantic_snapshot_id = semantic_snapshot_id


__all__ = ["RuntimeDeniedError"]

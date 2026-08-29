"""Task 18: the built-in reference Agent's Runtime adapter.

`ReferenceAgentRuntime` is the built-in Agent's equivalent of the MCP
`call_runtime_tool` dispatcher (`app.services.mcp_tools`) and the REST
router (`app.routers.v2.runtime`): a thin transport that resolves
investigation and action-plan requests through the exact same
`RuntimeService` (Task 15) every other Runtime transport uses. It carries
no policy or query logic of its own — every method here is a direct,
one-line call onto `RuntimeService`, so the built-in Agent can never drift
from the same snapshot-pinned, policy-checked ALLOW/DENY behavior REST, the
SDK, and MCP already share.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.schemas.runtime import InvestigationRequest, InvestigationResult
from app.services.runtime.credentials import RuntimeContext
from app.services.runtime.service import ActionPlan, ActionPlanRequest, RuntimeService


class ReferenceAgentRuntime:
    """Reference adapter routing the built-in Agent's Runtime calls through
    `RuntimeService`. Stateless aside from the `RuntimeService` instance it
    holds; every call takes the caller's already-verified `RuntimeContext`
    and a `Session`, exactly like `RuntimeService` itself."""

    def __init__(self, service: RuntimeService | None = None):
        self._service = service or RuntimeService()

    def investigate(self, request: InvestigationRequest, context: RuntimeContext, db: Session) -> InvestigationResult:
        return self._service.investigate(request, context, db)

    def create_action_plan(self, request: ActionPlanRequest, context: RuntimeContext, db: Session) -> ActionPlan:
        return self._service.create_action_plan(request, context, db)


__all__ = ["ReferenceAgentRuntime"]

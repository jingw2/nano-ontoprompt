"""Ontexus Runtime SDK v1 (Task 17): a typed Python transport adapter over
the Runtime REST API (Task 16). Contains no policy, connector, or writer
logic of its own — every decision comes from the server.
"""
from .client import CredentialProvider, RuntimeClient
from .errors import RuntimeDeniedError
from .models import (
    ActionPlan,
    ActionPlanRequest,
    EvidenceCitation,
    ExecutionStatus,
    InvestigationRequest,
    InvestigationResult,
    RuleOutcome,
)
from .transport import HttpTransport, HttpxTransport

__all__ = [
    "RuntimeClient",
    "CredentialProvider",
    "RuntimeDeniedError",
    "HttpTransport",
    "HttpxTransport",
    "InvestigationRequest",
    "InvestigationResult",
    "ActionPlanRequest",
    "ActionPlan",
    "ExecutionStatus",
    "EvidenceCitation",
    "RuleOutcome",
]

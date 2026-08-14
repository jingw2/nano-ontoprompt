"""Core Agent API router registry (I-BACKEND).

Feature packets export their routers here; this serial packet owns the
aggregation.  Every module named in `__all__` exposes a Section 12 `router`
(and `admin_router` where applicable) that `app.main` registers exactly once.
The names are bound to *modules*, not router objects, so pre-existing
`from app.routers import <module>` call sites keep working.  `agent_audit` is
an alias module whose `router` is the `agent_application_state` router object;
it is listed for registration clarity and must not be included twice.
"""
from app.routers import (  # noqa: F401 — re-exported modules
    agents,
    agent_approvals,
    agent_application_state,
    agent_audit,
    agent_clarifications,
    agent_events,
    agent_reconciliations,
    agent_turns,
    ontology_access_grants,
    ontology_lifecycle,
    ontology_remediations,
    security_domains,
)

__all__ = [
    "agents",
    "agent_approvals",
    "agent_application_state",
    "agent_audit",
    "agent_clarifications",
    "agent_events",
    "agent_reconciliations",
    "agent_turns",
    "ontology_access_grants",
    "ontology_lifecycle",
    "ontology_remediations",
    "security_domains",
]

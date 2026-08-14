"""Scoped redacted Agent audit queries (P3B-STATEAUDIT).

Read-only audit list/detail over governance_audit_logs, scoped to the
caller's security domain and the agent/turn correlation.  Never mutates
audit rows.
"""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session


def list_audit(db: Session, *, security_domain_id: str, agent_id: str | None = None,
               turn_id: str | None = None, limit: int = 50) -> dict:
    params: dict = {"domain": security_domain_id, "limit": limit}
    where = ["g.security_domain_id = :domain"]
    if agent_id:
        where.append("g.lineage @> CAST(:agent_probe AS jsonb)")
        params["agent_probe"] = '{"agent_id": "' + agent_id + '"}'
    if turn_id:
        where.append("g.correlation_id LIKE :turn_corr")
        params["turn_corr"] = f"turn:%:{turn_id}"
    rows = db.execute(text(
        "SELECT g.id, g.security_domain_id, g.sequence, g.actor_user_id, g.operation, "
        "g.decision, g.outcome, g.correlation_id, g.occurred_at "
        "FROM governance_audit_logs g WHERE " + " AND ".join(where) + " "
        "ORDER BY g.sequence DESC LIMIT :limit"
    ), params).mappings().all()
    has_more = len(rows) > limit
    return {"items": [dict(r) for r in rows[:limit]], "next_cursor": None, "has_more": has_more}


def get_audit(db: Session, *, security_domain_id: str, event_id: str) -> dict:
    row = db.execute(text(
        "SELECT g.id, g.security_domain_id, g.sequence, g.actor_user_id, g.operation, "
        "g.decision, g.outcome, g.correlation_id, g.occurred_at "
        "FROM governance_audit_logs g "
        "WHERE g.id = :id AND g.security_domain_id = :domain"
    ), {"id": event_id, "domain": security_domain_id}).mappings().one_or_none()
    if row is None:
        return None
    return dict(row)

"""Retention policy resolution and the per-domain epoch lock (P6A).

The epoch lock (`pg_advisory_xact_lock`, keyed by `hashtext(security_domain_id)`)
serializes policy activation, hold create/release, and every purge batch for
one domain, so a newly committed hold or policy can never race behind an
already-selected deletion. `security_domains` itself is immutable (see
`security_domains_immutable` trigger) — the epoch counter lives in the
separate `retention_epochs` table.
"""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session

# floor durations, in days, for every purge class this plan governs — a
# policy version's `rules` JSON may raise these but never lower them
# (validated at creation time in policy_admin.py, Task 4)
TABLE_MINIMUMS: dict[str, int] = {
    "application_state_snapshot.redact": 365,
    "clarification.delete": 1,
    "runtime_event.redact": 90,
    "message.redact": 90,
    "model_invocation.delete": 5,
    "node_execution.delete": 5,
    "checkpoint.delete": 7,
    "dispatch_outbox.delete": 30,
    "turn.delete": 7,
    "graph_index.delete": 7,
}


class RetentionPolicyError(Exception):
    """Rejected retention-policy operation."""


class RetentionPolicyConflict(RetentionPolicyError):
    """CAS mismatch on policy activation."""


def acquire_domain_lock(db: Session, security_domain_id: str) -> None:
    """Transaction-scoped advisory lock for one security domain. Must be
    called inside an open transaction (`with session.begin():` or an
    already-begun session) — the lock releases automatically on
    commit/rollback, never held across requests."""
    db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:domain))"), {"domain": security_domain_id})


def current_epoch(db: Session, security_domain_id: str) -> int:
    row = db.execute(text(
        "SELECT epoch FROM retention_epochs WHERE security_domain_id = :domain"
    ), {"domain": security_domain_id}).scalar_one_or_none()
    if row is None:
        raise RetentionPolicyError("RETENTION_DOMAIN_NOT_FOUND")
    return int(row)


def bump_epoch(db: Session, security_domain_id: str) -> int:
    """Increment and return the new epoch. Caller must hold the domain lock
    (`acquire_domain_lock`) in the same transaction first."""
    row = db.execute(text(
        "UPDATE retention_epochs SET epoch = epoch + 1, updated_at = now() "
        "WHERE security_domain_id = :domain RETURNING epoch"
    ), {"domain": security_domain_id}).scalar_one_or_none()
    if row is None:
        raise RetentionPolicyError("RETENTION_DOMAIN_NOT_FOUND")
    return int(row)


def resolve_active_duration(db: Session, security_domain_id: str, class_action: str) -> int:
    """`max(active_policy_rules.get(class_action, 0), TABLE_MINIMUMS[class_action])`,
    in days. A policy's rules may omit a class entirely (an older, narrower
    policy version) — that still floors to the table minimum, never to 0."""
    if class_action not in TABLE_MINIMUMS:
        raise RetentionPolicyError("RETENTION_CLASS_UNKNOWN")
    row = db.execute(text(
        "SELECT v.rules FROM retention_policies p "
        "JOIN retention_policy_versions v ON v.id = p.active_version_id "
        "WHERE p.security_domain_id = :domain"
    ), {"domain": security_domain_id}).scalar_one_or_none()
    active_days = int((row or {}).get(class_action, 0))
    return max(active_days, TABLE_MINIMUMS[class_action])

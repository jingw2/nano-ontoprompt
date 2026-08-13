"""Governance audit authority.

Append-only `GovernanceAuditLog` rows, transaction-owned
`GovernanceAuditOutbox` rows with a stable correlation ID, and a
(security_domain, UTC date) partition chain.  Materialization locks one
`GovernanceAuditChainHead` row `FOR UPDATE`, assigns the next sequence, hashes
the canonical event with the previous hash, inserts the audit row, and
advances the head atomically so multi-worker forks are impossible.

Business transactions write an outbox row with a correlation ID and later
materialize it; independent deny/timeout/failure paths append directly in a
short durable audit transaction.  Inputs/outputs are stored only as redacted
SHA-256 hashes, never as plaintext.

The migration helper `upgrade_audit_foundation()`/`downgrade_audit_foundation()`
is consumed exactly once by revision 0003 (P1A-INTEGRATE).
"""
import hashlib
import json
from datetime import datetime, timezone
import uuid

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from app.services.publication.canonical import CanonicalizationError, canonical_json

UUID_CHECK = (
    "VALUE ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
    "[0-9a-f]{4}-[0-9a-f]{12}$'"
)
DEFAULT_RETENTION_CLASS = "standard"
_CHAIN_DATE_FORMAT = "%Y-%m-%d"
_TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"


def partition_key_for(security_domain_id: str, occurred_at: datetime) -> str:
    if occurred_at.tzinfo is None:
        raise ValueError("occurred_at must be timezone-aware")
    return f"{security_domain_id}:{occurred_at.astimezone(timezone.utc).strftime(_CHAIN_DATE_FORMAT)}"


def _format_utc(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("occurred_at must be timezone-aware")
    return value.astimezone(timezone.utc).strftime(_TIMESTAMP_FORMAT)


def canonical_event(event: dict) -> bytes:
    """Byte-exact canonical JSON for the audit chain hash (sorted keys, no whitespace)."""
    try:
        return canonical_json(event)
    except CanonicalizationError as exc:
        raise ValueError(str(exc)) from exc


def hash_payload(payload) -> bytes | None:
    """Redacted SHA-256 of a canonical JSON payload; None stays None."""
    if payload is None:
        return None
    return hashlib.sha256(canonical_event(payload)).digest()


def append_audit_event(
    *,
    security_domain_id: str,
    partition_key: str,
    sequence: int,
    actor_user_id: str | None,
    operation: str,
    decision: str,
    policy_ids: dict | None,
    correlation_id: str | None,
    input_hash: bytes | None,
    output_hash: bytes | None,
    lineage: dict | None,
    outcome: str,
    previous_hash: bytes | None,
    agent_id: str | None,
    agent_version_id: str | None,
    release_id: str | None,
    model_version_id: str | None,
    connection_version_id: str | None,
    retention_class: str,
    occurred_at: datetime,
) -> dict:
    """Canonical chain event for one audit row (all fields stored on the row)."""
    return {
        "security_domain_id": security_domain_id,
        "partition_key": partition_key,
        "sequence": sequence,
        "actor_user_id": actor_user_id,
        "operation": operation,
        "decision": decision,
        "policy_ids": policy_ids if policy_ids is not None else {},
        "correlation_id": correlation_id,
        "input_hash": input_hash.hex() if input_hash is not None else None,
        "output_hash": output_hash.hex() if output_hash is not None else None,
        "lineage": lineage if lineage is not None else {},
        "outcome": outcome,
        "previous_hash": previous_hash.hex() if previous_hash is not None else None,
        "agent_id": agent_id,
        "agent_version_id": agent_version_id,
        "release_id": release_id,
        "model_version_id": model_version_id,
        "connection_version_id": connection_version_id,
        "retention_class": retention_class,
        "occurred_at": _format_utc(occurred_at),
    }


def event_hash(event: dict) -> bytes:
    return hashlib.sha256(canonical_event(event)).digest()


def append_audit(
    connection,
    *,
    security_domain_id: str,
    operation: str,
    decision: str,
    outcome: str,
    correlation_id: str | None = None,
    actor_user_id: str | None = None,
    policy_ids: dict | None = None,
    lineage: dict | None = None,
    input_payload=None,
    output_payload=None,
    agent_id: str | None = None,
    agent_version_id: str | None = None,
    release_id: str | None = None,
    model_version_id: str | None = None,
    connection_version_id: str | None = None,
    retention_class: str = DEFAULT_RETENTION_CLASS,
    occurred_at: datetime | None = None,
) -> dict:
    occurred_at = occurred_at or datetime.now(timezone.utc)
    partition_key = partition_key_for(security_domain_id, occurred_at)
    input_hash = hash_payload(input_payload)
    output_hash = hash_payload(output_payload)
    connection.execute(
        sa.text(
            "INSERT INTO governance_audit_chain_heads "
            "(partition_key, security_domain_id, next_sequence, last_hash) "
            "VALUES (:partition, :domain, 1, NULL) "
            "ON CONFLICT (partition_key) DO NOTHING"
        ),
        {"partition": partition_key, "domain": security_domain_id},
    )
    head = connection.execute(
        sa.text(
            "SELECT next_sequence, last_hash FROM governance_audit_chain_heads "
            "WHERE partition_key = :partition FOR UPDATE"
        ),
        {"partition": partition_key},
    ).mappings().one()
    sequence = head["next_sequence"]
    previous_hash = bytes(head["last_hash"]) if head["last_hash"] is not None else None
    event = append_audit_event(
        security_domain_id=security_domain_id,
        partition_key=partition_key,
        sequence=sequence,
        actor_user_id=actor_user_id,
        operation=operation,
        decision=decision,
        policy_ids=policy_ids,
        correlation_id=correlation_id,
        input_hash=input_hash,
        output_hash=output_hash,
        lineage=lineage,
        outcome=outcome,
        previous_hash=previous_hash,
        agent_id=agent_id,
        agent_version_id=agent_version_id,
        release_id=release_id,
        model_version_id=model_version_id,
        connection_version_id=connection_version_id,
        retention_class=retention_class,
        occurred_at=occurred_at,
    )
    digest = event_hash(event)
    row_id = str(uuid.uuid4())
    connection.execute(
        sa.text(
            "INSERT INTO governance_audit_logs "
            "(id, security_domain_id, partition_key, sequence, actor_user_id, operation, decision, "
            "policy_ids, correlation_id, input_hash, output_hash, lineage, outcome, previous_hash, "
            "event_hash, agent_id, agent_version_id, release_id, model_version_id, connection_version_id, "
            "retention_class, occurred_at) "
            "VALUES (:id, :security_domain_id, :partition_key, :sequence, :actor_user_id, :operation, :decision, "
            "CAST(:policy_ids AS jsonb), :correlation_id, :input_hash, :output_hash, CAST(:lineage AS jsonb), "
            ":outcome, :previous_hash, :event_hash, :agent_id, :agent_version_id, :release_id, :model_version_id, "
            ":connection_version_id, :retention_class, :occurred_at)"
        ),
        {
            "id": row_id,
            "security_domain_id": security_domain_id,
            "partition_key": partition_key,
            "sequence": sequence,
            "actor_user_id": actor_user_id,
            "operation": operation,
            "decision": decision,
            "policy_ids": json.dumps(policy_ids if policy_ids is not None else {}, ensure_ascii=False),
            "correlation_id": correlation_id,
            "input_hash": input_hash,
            "output_hash": output_hash,
            "lineage": json.dumps(lineage if lineage is not None else {}, ensure_ascii=False),
            "outcome": outcome,
            "previous_hash": previous_hash,
            "event_hash": digest,
            "agent_id": agent_id,
            "agent_version_id": agent_version_id,
            "release_id": release_id,
            "model_version_id": model_version_id,
            "connection_version_id": connection_version_id,
            "retention_class": retention_class,
            "occurred_at": occurred_at,
        },
    )
    connection.execute(
        sa.text(
            "UPDATE governance_audit_chain_heads SET next_sequence = :next, last_hash = :hash, "
            "updated_at = CURRENT_TIMESTAMP WHERE partition_key = :partition"
        ),
        {"next": sequence + 1, "hash": digest, "partition": partition_key},
    )
    return {
        "id": row_id,
        "partition_key": partition_key,
        "sequence": sequence,
        "event_hash": digest,
        "previous_hash": previous_hash,
        "input_hash": input_hash,
    }


def enqueue_audit(connection, **event_kwargs) -> str:
    """Transaction-owned outbox row carrying the full append_audit kwargs."""
    correlation_id = event_kwargs["correlation_id"]
    security_domain_id = event_kwargs["security_domain_id"]
    row_id = str(uuid.uuid4())
    connection.execute(
        sa.text(
            "INSERT INTO governance_audit_outbox "
            "(id, security_domain_id, correlation_id, payload, state, attempts) "
            "VALUES (:id, :domain, :correlation, CAST(:payload AS jsonb), 'pending', 0)"
        ),
        {
            "id": row_id,
            "domain": security_domain_id,
            "correlation": correlation_id,
            "payload": json.dumps(event_kwargs, ensure_ascii=False, sort_keys=True),
        },
    )
    return row_id


def materialize_outbox(connection, correlation_id: str) -> dict | None:
    row = connection.execute(
        sa.text(
            "SELECT id, security_domain_id, payload, state FROM governance_audit_outbox "
            "WHERE correlation_id = :correlation FOR UPDATE"
        ),
        {"correlation": correlation_id},
    ).mappings().one_or_none()
    if row is None:
        return None
    if row["state"] == "materialized":
        return {
            "already_materialized": True,
            "correlation_id": correlation_id,
            "outbox_id": row["id"],
        }
    payload = dict(row["payload"])
    receipt = append_audit(connection, **payload)
    connection.execute(
        sa.text(
            "UPDATE governance_audit_outbox SET state = 'materialized', materialized_at = CURRENT_TIMESTAMP, "
            "updated_at = CURRENT_TIMESTAMP, attempts = attempts + 1 WHERE id = :id"
        ),
        {"id": row["id"]},
    )
    receipt["outbox_id"] = row["id"]
    return receipt


def verify_chain(connectable, security_domain_id: str, occurred_at: datetime | None = None) -> dict:
    """Recompute the partition chain and report tampering (event and linkage hashes)."""
    partition_key = partition_key_for(security_domain_id, occurred_at or datetime.now(timezone.utc))
    if hasattr(connectable, "connect"):
        with connectable.connect() as connection:
            return _verify_partition(connection, partition_key)
    return _verify_partition(connectable, partition_key)


def _verify_partition(connection, partition_key: str) -> dict:
    rows = connection.execute(
        sa.text(
            "SELECT security_domain_id, partition_key, sequence, actor_user_id, operation, decision, "
            "policy_ids, correlation_id, input_hash, output_hash, lineage, outcome, previous_hash, event_hash, "
            "agent_id, agent_version_id, release_id, model_version_id, connection_version_id, retention_class, "
            "occurred_at FROM governance_audit_logs WHERE partition_key = :partition ORDER BY sequence"
        ),
        {"partition": partition_key},
    ).mappings().all()
    tampered = 0
    previous = None
    for row in rows:
        event = append_audit_event(
            security_domain_id=row["security_domain_id"],
            partition_key=row["partition_key"],
            sequence=row["sequence"],
            actor_user_id=row["actor_user_id"],
            operation=row["operation"],
            decision=row["decision"],
            policy_ids=row["policy_ids"],
            correlation_id=row["correlation_id"],
            input_hash=row["input_hash"],
            output_hash=row["output_hash"],
            lineage=row["lineage"],
            outcome=row["outcome"],
            previous_hash=row["previous_hash"],
            agent_id=row["agent_id"],
            agent_version_id=row["agent_version_id"],
            release_id=row["release_id"],
            model_version_id=row["model_version_id"],
            connection_version_id=row["connection_version_id"],
            retention_class=row["retention_class"],
            occurred_at=row["occurred_at"],
        )
        expected = event_hash(event)
        if expected != bytes(row["event_hash"]):
            tampered += 1
        if row["previous_hash"] is None:
            if previous is not None:
                tampered += 1
        elif previous is None or bytes(row["previous_hash"]) != previous:
            tampered += 1
        previous = bytes(row["event_hash"])
    return {
        "ok": tampered == 0,
        "partition_key": partition_key,
        "count": len(rows),
        "tampered": tampered,
        "last_hash": previous,
    }


def upgrade_audit_foundation() -> None:
    op.create_table(
        "governance_audit_logs",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("security_domain_id", sa.String(36), nullable=False),
        sa.Column("partition_key", sa.String(64), nullable=False),
        sa.Column("sequence", sa.BigInteger(), nullable=False),
        sa.Column("actor_user_id", sa.String(36), nullable=True),
        sa.Column("operation", sa.String(100), nullable=False),
        sa.Column("decision", sa.String(100), nullable=False),
        sa.Column("policy_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("correlation_id", sa.String(64), nullable=True),
        sa.Column("input_hash", sa.LargeBinary(), nullable=True),
        sa.Column("output_hash", sa.LargeBinary(), nullable=True),
        sa.Column("lineage", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("outcome", sa.String(100), nullable=False),
        sa.Column("previous_hash", sa.LargeBinary(), nullable=True),
        sa.Column("event_hash", sa.LargeBinary(), nullable=False),
        sa.Column("agent_id", sa.String(36), nullable=True),
        sa.Column("agent_version_id", sa.String(36), nullable=True),
        sa.Column("release_id", sa.String(36), nullable=True),
        sa.Column("model_version_id", sa.String(36), nullable=True),
        sa.Column("connection_version_id", sa.String(36), nullable=True),
        sa.Column("retention_class", sa.String(30), nullable=False, server_default="standard"),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.PrimaryKeyConstraint("id", name="pk_governance_audit_logs"),
        sa.CheckConstraint(UUID_CHECK.replace("VALUE", "id"), name="ck_governance_audit_logs_id_uuid"),
        sa.CheckConstraint("sequence > 0", name="ck_governance_audit_logs_sequence"),
        sa.CheckConstraint("octet_length(event_hash) = 32", name="ck_governance_audit_logs_event_hash"),
        sa.UniqueConstraint("partition_key", "sequence", name="uq_governance_audit_logs_partition_sequence"),
        sa.ForeignKeyConstraint(["security_domain_id"], ["security_domains.id"], ondelete="RESTRICT", name="fk_governance_audit_logs_domain"),
        sa.ForeignKeyConstraint(
            ["actor_user_id", "security_domain_id"],
            ["users.id", "users.security_domain_id"],
            ondelete="RESTRICT",
            name="fk_governance_audit_logs_actor_domain",
        ),
    )
    op.create_table(
        "governance_audit_outbox",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("security_domain_id", sa.String(36), nullable=False),
        sa.Column("correlation_id", sa.String(64), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("state", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("materialized_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_governance_audit_outbox"),
        sa.CheckConstraint(UUID_CHECK.replace("VALUE", "id"), name="ck_governance_audit_outbox_id_uuid"),
        sa.CheckConstraint("state IN ('pending', 'materialized', 'failed')", name="ck_governance_audit_outbox_state"),
        sa.CheckConstraint("attempts >= 0", name="ck_governance_audit_outbox_attempts"),
        sa.UniqueConstraint("security_domain_id", "correlation_id", name="uq_governance_audit_outbox_domain_correlation"),
        sa.ForeignKeyConstraint(["security_domain_id"], ["security_domains.id"], ondelete="RESTRICT", name="fk_governance_audit_outbox_domain"),
    )
    op.create_table(
        "governance_audit_chain_heads",
        sa.Column("partition_key", sa.String(64), nullable=False),
        sa.Column("security_domain_id", sa.String(36), nullable=False),
        sa.Column("next_sequence", sa.BigInteger(), nullable=False, server_default="1"),
        sa.Column("last_hash", sa.LargeBinary(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.PrimaryKeyConstraint("partition_key", name="pk_governance_audit_chain_heads"),
        sa.CheckConstraint("next_sequence > 0", name="ck_governance_audit_chain_next_sequence"),
        sa.CheckConstraint(
            "last_hash IS NULL OR octet_length(last_hash) = 32",
            name="ck_governance_audit_chain_last_hash",
        ),
        sa.ForeignKeyConstraint(["security_domain_id"], ["security_domains.id"], ondelete="RESTRICT", name="fk_governance_audit_chain_domain"),
    )
    op.execute(
        """
        CREATE FUNCTION reject_governance_audit_log_mutation() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          RAISE EXCEPTION 'GOVERNANCE_AUDIT_APPEND_ONLY';
        END;
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER governance_audit_logs_append_only BEFORE UPDATE OR DELETE "
        "ON governance_audit_logs FOR EACH ROW EXECUTE FUNCTION reject_governance_audit_log_mutation()"
    )


def downgrade_audit_foundation() -> None:
    op.execute("DROP TRIGGER IF EXISTS governance_audit_logs_append_only ON governance_audit_logs")
    op.execute("DROP FUNCTION IF EXISTS reject_governance_audit_log_mutation()")
    op.drop_table("governance_audit_logs")
    op.drop_table("governance_audit_outbox")
    op.drop_table("governance_audit_chain_heads")

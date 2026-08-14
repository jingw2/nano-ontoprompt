"""Agent runtime persistence models (P3A-TURNDB, Section 6).

Sessions/Turns/messages form the runtime graph without FK cycles (turn
message links are added by ALTER after both tables exist); a partial unique
index on `agent_turns(session_id)` enforces the one-active-Turn invariant.
Dispatch outbox rows are inserted in the same transaction as the
authoritative state change; the derived-index outbox is fed only by the
PostgreSQL trigger on authoritative instance/relation rows.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    JSON,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

ACTIVE_TURN_STATUSES = (
    "queued", "running", "awaiting_approval", "awaiting_clarification",
    "reconciling", "cancelling",
)
TERMINAL_TURN_STATUSES = ("succeeded", "failed", "cancelled")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _new_id() -> str:
    return str(uuid.uuid4())


class AgentSession(Base):
    __tablename__ = "agent_sessions"
    __table_args__ = (
        CheckConstraint("status IN ('active', 'closed')", name="ck_agent_sessions_status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    agent_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("agents.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    owner_user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    active_turn_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("agent_turns.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class AgentTurn(Base):
    __tablename__ = "agent_turns"
    __table_args__ = (
        # One active Turn per session (partial unique index, see Section 6).
        Index(
            "uq_agent_turns_active_session",
            "session_id",
            unique=True,
            postgresql_where=text(
                "status IN ('queued', 'running', 'awaiting_approval', "
                "'awaiting_clarification', 'reconciling', 'cancelling')"
            ),
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    session_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("agent_sessions.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="queued")
    request_message_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("agent_messages.id", ondelete="RESTRICT"), nullable=True
    )
    response_message_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("agent_messages.id", ondelete="RESTRICT"), nullable=True
    )
    dispatch_generation: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1)
    claim_generation: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    claim_token: Mapped[str | None] = mapped_column(String(128), nullable=True)
    worker_artifact_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancel_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class AgentMessage(Base):
    __tablename__ = "agent_messages"
    __table_args__ = (
        UniqueConstraint("session_id", "ordinal", name="uq_agent_messages_session_ordinal"),
        CheckConstraint("role IN ('user', 'assistant', 'system', 'tool')", name="ck_agent_messages_role"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    session_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("agent_sessions.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    turn_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("agent_turns.id", ondelete="RESTRICT"), nullable=True
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    ordinal: Mapped[int] = mapped_column(BigInteger, nullable=False)
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class AgentTurnDispatchOutbox(Base):
    __tablename__ = "agent_turn_dispatch_outbox"
    __table_args__ = (
        UniqueConstraint("turn_id", "dispatch_generation", "operation", name="uq_agent_turn_dispatch_outbox_identity"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    turn_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("agent_turns.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    dispatch_generation: Mapped[int] = mapped_column(BigInteger, nullable=False)
    operation: Mapped[str] = mapped_column(String(40), nullable=False)
    state: Mapped[str] = mapped_column(String(24), nullable=False, default="pending")
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    broker_message_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    claim_deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    superseded_by_outbox_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolution: Mapped[str | None] = mapped_column(String(30), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class AgentIndexOutbox(Base):
    __tablename__ = "agent_index_outbox"
    __table_args__ = (
        CheckConstraint(
            "event_type IN ('upsert_instance', 'delete_instance', 'upsert_edge', 'delete_edge')",
            name="ck_agent_index_outbox_event_type",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    ontology_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("ontology_projects.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    event_type: Mapped[str] = mapped_column(String(24), nullable=False)
    instance_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    instance_revision: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    entity_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    edge_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    source_instance_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    target_instance_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    relation_definition_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    state: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class RuntimeArtifact(Base):
    __tablename__ = "runtime_artifacts"
    __table_args__ = (
        UniqueConstraint("runtime_kind", "runtime_library_version", "graph_revision",
                         "serializer_version", name="uq_runtime_artifacts_tuple"),
        UniqueConstraint("queue_name", name="uq_runtime_artifacts_queue"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    runtime_kind: Mapped[str] = mapped_column(String(40), nullable=False)
    runtime_library_version: Mapped[str] = mapped_column(String(100), nullable=False)
    graph_revision: Mapped[str] = mapped_column(String(64), nullable=False)
    serializer_version: Mapped[str] = mapped_column(String(20), nullable=False)
    image_digest: Mapped[str] = mapped_column(String(128), nullable=False)
    queue_name: Mapped[str] = mapped_column(String(120), nullable=False)
    checkpoint_formats: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    enabled: Mapped[bool] = mapped_column(nullable=False, default=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class AgentTurnCheckpoint(Base):
    __tablename__ = "agent_turn_checkpoints"
    __table_args__ = (
        UniqueConstraint("turn_id", "checkpoint_namespace", "checkpoint_id", name="uq_atc_namespace_id"),
        UniqueConstraint("turn_id", "revision", name="uq_atc_turn_revision"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    turn_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("agent_turns.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    revision: Mapped[int] = mapped_column(BigInteger, nullable=False)
    runtime_artifact_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("runtime_artifacts.id", ondelete="RESTRICT"), nullable=True
    )
    checkpoint_namespace: Mapped[str] = mapped_column(String(120), nullable=False)
    checkpoint_id: Mapped[str] = mapped_column(String(120), nullable=False)
    parent_checkpoint_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    phase: Mapped[str] = mapped_column(String(40), nullable=False)
    next_nodes: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    serialized_state: Mapped[bytes | None] = mapped_column(nullable=True)
    checkpoint_metadata: Mapped[dict] = mapped_column('metadata', JSON, nullable=False, default=dict)
    last_event_seq: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    model_tuple: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    retrieval_tuple: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    application_state_tuple: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    summary_range: Mapped[str | None] = mapped_column(String(64), nullable=True)
    summary_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    pending_execution_ids: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    state_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class AgentTurnCheckpointWrite(Base):
    __tablename__ = "agent_turn_checkpoint_writes"
    __table_args__ = (
        UniqueConstraint("turn_id", "checkpoint_namespace", "parent_checkpoint_id", "task_id",
                         "task_path", "channel", "write_index", name="uq_atcw_identity"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    turn_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("agent_turns.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    checkpoint_namespace: Mapped[str] = mapped_column(String(120), nullable=False)
    parent_checkpoint_id: Mapped[str] = mapped_column(String(120), nullable=False)
    task_id: Mapped[str] = mapped_column(String(120), nullable=False)
    task_path: Mapped[str] = mapped_column(String(120), nullable=False)
    channel: Mapped[str] = mapped_column(String(120), nullable=False)
    write_index: Mapped[int] = mapped_column(nullable=False)
    serializer_version: Mapped[str] = mapped_column(String(20), nullable=False)
    value_bytes: Mapped[bytes | None] = mapped_column(nullable=True)
    value_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    consumed_child_checkpoint_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    attempt_state: Mapped[str] = mapped_column(String(30), nullable=False, default="started")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class AgentNodeExecution(Base):
    __tablename__ = "agent_node_executions"
    __table_args__ = (
        UniqueConstraint("turn_id", "parent_checkpoint_id", "task_id", "task_path",
                         "node_name", "attempt_no", name="uq_ane_attempt"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    turn_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("agent_turns.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    parent_checkpoint_id: Mapped[str] = mapped_column(String(120), nullable=False)
    task_id: Mapped[str] = mapped_column(String(120), nullable=False)
    task_path: Mapped[str] = mapped_column(String(120), nullable=False)
    node_name: Mapped[str] = mapped_column(String(120), nullable=False)
    attempt_no: Mapped[int] = mapped_column(nullable=False)
    state: Mapped[str] = mapped_column(String(30), nullable=False, default="started")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class AgentModelInvocation(Base):
    __tablename__ = "agent_model_invocations"
    __table_args__ = (
        UniqueConstraint("turn_id", "node_execution_id", "invocation_slot", name="uq_ami_slot"),
        UniqueConstraint("idempotency_key", name="uq_ami_idempotency"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    turn_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("agent_turns.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    node_execution_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("agent_node_executions.id", ondelete="RESTRICT"), nullable=True
    )
    invocation_slot: Mapped[int] = mapped_column(nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    response_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class AgentRuntimeEvent(Base):
    __tablename__ = "agent_runtime_events"
    __table_args__ = (
        UniqueConstraint("turn_id", "sequence", name="uq_are_turn_sequence"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    turn_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("agent_turns.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    event_type: Mapped[str] = mapped_column(String(40), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class AgentToolExecution(Base):
    __tablename__ = "agent_tool_executions"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_agent_tool_executions_idempotency"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    turn_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("agent_turns.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    node_execution_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("agent_node_executions.id", ondelete="RESTRICT"), nullable=True
    )
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    descriptor: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    parameters_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    preview_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    result_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class AgentApproval(Base):
    __tablename__ = "agent_approvals"
    __table_args__ = (
        UniqueConstraint("tool_execution_id", name="uq_agent_approvals_execution"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    turn_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("agent_turns.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    tool_execution_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("agent_tool_executions.id", ondelete="RESTRICT"), nullable=False
    )
    node_execution_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    preview_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    parameter_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    release_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    model_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    policy_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    designated_actor_id: Mapped[str] = mapped_column(String(36), nullable=False)
    revision: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    stale_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class AgentReconciliationCase(Base):
    __tablename__ = "agent_reconciliation_cases"
    __table_args__ = (
        UniqueConstraint("turn_id", "execution_kind", "execution_id", name="uq_arc_identity"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    turn_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("agent_turns.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    execution_kind: Mapped[str] = mapped_column(String(40), nullable=False)
    execution_id: Mapped[str] = mapped_column(String(36), nullable=False)
    revision: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1)
    state: Mapped[str] = mapped_column(String(30), nullable=False, default="open")
    provider_request_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    evidence_bytes: Mapped[bytes | None] = mapped_column(nullable=True)
    evidence_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    investigator_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    resolver_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class AgentClarificationRequest(Base):
    __tablename__ = "agent_clarification_requests"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    turn_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("agent_turns.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    question: Mapped[str] = mapped_column(Text, nullable=False)
    answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    base_request_revision: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    answered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AgentApplicationStateSnapshot(Base):
    __tablename__ = "agent_application_state_snapshots"
    __table_args__ = (
        UniqueConstraint("session_id", "revision", name="uq_aass_session_revision"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    session_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("agent_sessions.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    revision: Mapped[int] = mapped_column(BigInteger, nullable=False)
    parent_revision: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    schema_version_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("application_state_schema_versions.id", ondelete="RESTRICT"), nullable=False
    )
    schema_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    canonical_bytes: Mapped[bytes] = mapped_column(nullable=False)
    canonical_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    actor_user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    sources: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    selected_instances: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class AgentStreamTicket(Base):
    __tablename__ = "agent_stream_tickets"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    turn_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("agent_turns.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    actor_user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="unused")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class AgentPurgeJob(Base):
    __tablename__ = "agent_purge_jobs"
    __table_args__ = (
        UniqueConstraint("security_domain_id", "purge_class", name="uq_agent_purge_jobs_domain_class"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    security_domain_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("security_domains.id", ondelete="RESTRICT"), nullable=False
    )
    purge_class: Mapped[str] = mapped_column(String(40), nullable=False)
    cursor_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    cursor_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    batch_size: Mapped[int] = mapped_column(nullable=False, default=500)
    generation: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1)
    claim_token: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attempts: Mapped[int] = mapped_column(nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class AgentPurgeMarker(Base):
    __tablename__ = "agent_purge_markers"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    turn_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("agent_turns.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    policy_version_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    fixed_policy_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    job_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("agent_purge_jobs.id", ondelete="RESTRICT"), nullable=False
    )
    generation: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

"""Managed action bindings (Task 21): the governance layer that makes a
writable `Action` (Task 15's `create_action_plan`) concrete.

A `ManagedActionBinding` pins one writable Action to a fixed, allowlisted
single-target row-update surface: a connection identity, SQL dialect
(`postgresql` or `mysql`), schema/table, primary-key columns, an allowlisted
set of writable columns, and a version-column precondition for optimistic
concurrency. It never stores a secret value — only `secret_ref`, a vault
reference resolved at actual execution time (a later Milestone 3 task, not
this one). Publication is versioned: each `publish_binding` call for a given
`action_id` creates the next `version`, never mutates a prior row in place;
`status` (`draft`/`published`/`revoked`) governs which versions
`resolve_published_binding` (`app.services.runtime.action_bindings`) will
ever hand back.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

UUID_CHECK = (
    "managed_action_binding_id ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
    "[0-9a-f]{4}-[0-9a-f]{12}$'"
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _new_id() -> str:
    return str(uuid.uuid4())


class ManagedActionBinding(Base):
    """One versioned binding row. Immutable in its identity-defining fields
    once created (mirrors `RuntimePlan`/`SemanticSnapshot`'s append-only
    pattern) — only `publish_binding`/future lifecycle operations create new
    rows or transition `status`; nothing here rewrites a binding's
    connection, table, or column allowlist in place."""

    __tablename__ = "managed_action_bindings"
    __table_args__ = (
        CheckConstraint(UUID_CHECK, name="ck_managed_action_bindings_id_uuid"),
        CheckConstraint(
            "status IN ('draft', 'published', 'revoked')", name="ck_managed_action_bindings_status",
        ),
        CheckConstraint("dialect IN ('postgresql', 'mysql')", name="ck_managed_action_bindings_dialect"),
        UniqueConstraint("action_id", "version", name="uq_managed_action_bindings_action_version"),
    )

    managed_action_binding_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    action_id: Mapped[str] = mapped_column(
        String, ForeignKey("actions.id", ondelete="RESTRICT"), nullable=False, index=True,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft")
    connection_id: Mapped[str] = mapped_column(
        String, ForeignKey("v2_connections.id", ondelete="RESTRICT"), nullable=False,
    )
    connection_target_identity: Mapped[str] = mapped_column(String(500), nullable=False)
    dialect: Mapped[str] = mapped_column(String(20), nullable=False)
    schema_name: Mapped[str] = mapped_column(String(200), nullable=False)
    table_name: Mapped[str] = mapped_column(String(200), nullable=False)
    primary_key_columns: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    writable_columns: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    version_column: Mapped[str] = mapped_column(String(200), nullable=False)
    parameter_schema: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    # A vault reference only (e.g. "vault://kv/connections/x#password") —
    # never a secret value. No column on this model ever holds decrypted or
    # plaintext credential material.
    secret_ref: Mapped[str] = mapped_column(String(500), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)


__all__ = ["ManagedActionBinding"]

"""Durable source refresh contract — typed domain values (Task 6).

Pure, side-effect-free value types and helper functions for the enterprise
refresh contract: policy/cursor-contract enums, immutable `SourceCursor` and
`ChangeEnvelope` values, cursor ordering, event deduplication, and the typed
error hierarchy used by `app.services.v2.incremental.contract`.

The durable, mutable persistence side (RefreshSourceState, RefreshRun, ...)
lives in `app.models.v2.refresh`; this module only defines the v1 contract's
data shapes and pure functions.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from enum import Enum as PyEnum
from typing import Mapping, Optional


class RefreshPolicy(str, PyEnum):
    BATCH = "batch"
    MICRO_BATCH = "micro_batch"
    EVENT_DRIVEN = "event_driven"


# The v1 contract supports exactly these two cursor contracts.
CURSOR_CONTRACTS = ("watermark_primary_key", "opaque_source_cursor")


@dataclass(frozen=True)
class SourceCursor:
    """Immutable source cursor position for one (source_id, resource).

    A `watermark_primary_key` cursor orders by `(watermark, primary_key)`
    lexicographically; an `opaque_source_cursor` cursor orders only by the
    source-provided `opaque_value` and never inspects watermark/primary_key.
    """

    source_id: str
    resource: str
    contract: str
    watermark: Optional[str]
    primary_key: Optional[str]
    opaque_value: object
    observed_at: Optional[datetime]


@dataclass(frozen=True)
class ChangeEnvelope:
    """Immutable, source-validated change event (managed webhook/outbox only)."""

    event_id: str
    source_id: str
    resource: str
    operation: str  # "upsert" | "delete"
    primary_key: str
    payload: Mapping[str, object]
    watermark: Optional[str]
    source_cursor: Optional[str]
    schema_hash: Optional[str]
    occurred_at: Optional[datetime]
    received_at: datetime


class RefreshError(Exception):
    """Base class for the refresh contract's typed errors."""

    def __init__(self, reason_code: str, message: str | None = None):
        self.reason_code = reason_code
        super().__init__(message or reason_code)


class RefreshLeaseError(RefreshError):
    """Raised when a claim/outcome cannot be granted because no matching
    active lease is held (already active elsewhere, or expired)."""


class RefreshFencingError(RefreshError):
    """Raised when a presented fencing token/lease owner no longer matches
    the authoritative `RefreshSourceState` — the caller's claim is stale."""


class ConfigurationDriftError(RefreshError):
    """Raised when a run's frozen `config_version`/`cursor_contract` no
    longer matches the authoritative source revision. Stable reason code:
    `CONFIGURATION_DRIFT`. Never substituted for a lease/fencing error."""

    def __init__(self, message: str | None = None):
        super().__init__("CONFIGURATION_DRIFT", message)


class RefreshCancellationRequested(RefreshError):
    """Raised by `assert_refresh_not_cancelled` (and internally by
    `record_refresh_outcome`) when the run has an in-flight cancellation
    request that must roll back tentative work."""


def cursor_order(left: SourceCursor, right: SourceCursor) -> int:
    """Compare two cursors of the same contract. Returns -1, 0, or 1."""
    if left.contract != right.contract:
        raise ValueError(f"cannot compare cursors of different contracts: {left.contract} vs {right.contract}")
    if left.contract == "watermark_primary_key":
        left_key = (left.watermark, left.primary_key)
        right_key = (right.watermark, right.primary_key)
    elif left.contract == "opaque_source_cursor":
        left_key = (left.opaque_value,)
        right_key = (right.opaque_value,)
    else:
        raise ValueError(f"unsupported cursor contract: {left.contract}")
    if left_key < right_key:
        return -1
    if left_key > right_key:
        return 1
    return 0


def normalize_change_envelope(
    raw: Mapping[str, object], *, source_id: str, resource: str, received_at: datetime,
) -> ChangeEnvelope:
    """Build a `ChangeEnvelope` from a raw managed webhook/outbox payload.

    Rejects missing event identity, a source/resource mismatch against the
    validated managed ingestion context, an invalid cursor shape (a
    watermark-contract event with no watermark, or an opaque-contract event
    with no opaque cursor value), and schema-hash drift signalled by the raw
    payload itself (`raw["schema_hash_drift"]` truthy).
    """
    event_id = raw.get("event_id")
    if not event_id or not isinstance(event_id, str):
        raise RefreshError("INVALID_CHANGE_ENVELOPE", "missing event identity")

    raw_source_id = raw.get("source_id", source_id)
    raw_resource = raw.get("resource", resource)
    if raw_source_id != source_id or raw_resource != resource:
        raise RefreshError("INVALID_CHANGE_ENVELOPE", "source/resource mismatch")

    operation = raw.get("operation")
    if operation not in ("upsert", "delete"):
        raise RefreshError("INVALID_CHANGE_ENVELOPE", "invalid operation")

    primary_key = raw.get("primary_key")
    if not primary_key or not isinstance(primary_key, str):
        raise RefreshError("INVALID_CHANGE_ENVELOPE", "invalid cursor shape: missing primary_key")

    contract = raw.get("contract", "watermark_primary_key")
    watermark = raw.get("watermark")
    source_cursor = raw.get("source_cursor")
    if contract == "watermark_primary_key" and not watermark:
        raise RefreshError("INVALID_CHANGE_ENVELOPE", "invalid cursor shape: missing watermark")
    if contract == "opaque_source_cursor" and not source_cursor:
        raise RefreshError("INVALID_CHANGE_ENVELOPE", "invalid cursor shape: missing source_cursor")

    if raw.get("schema_hash_drift"):
        raise RefreshError("INVALID_CHANGE_ENVELOPE", "schema hash drift")

    return ChangeEnvelope(
        event_id=event_id,
        source_id=source_id,
        resource=resource,
        operation=operation,
        primary_key=primary_key,
        payload=raw.get("payload", {}),
        watermark=watermark,
        source_cursor=source_cursor,
        schema_hash=raw.get("schema_hash"),
        occurred_at=raw.get("occurred_at"),
        received_at=received_at,
    )


def dedupe_key(envelope: ChangeEnvelope) -> str:
    """Stable SHA-256 key over source, resource, event identity, cursor,
    primary key, operation, and canonical (sorted-key) payload."""
    canonical_payload = json.dumps(envelope.payload, sort_keys=True, default=str)
    material = "|".join([
        envelope.source_id,
        envelope.resource,
        envelope.event_id,
        envelope.watermark or "",
        envelope.source_cursor or "",
        envelope.primary_key,
        envelope.operation,
        canonical_payload,
    ])
    return hashlib.sha256(material.encode("utf-8")).hexdigest()

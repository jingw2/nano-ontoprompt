"""Managed event boundaries for refresh ingestion.

Only source-owned webhooks and outbox records enter the event refresh path.
The adapters authenticate/validate the envelope and return the same immutable
``ChangeEnvelope`` used by polling; they do not know about Celery or a broker.
"""
from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timezone
from typing import Callable, Mapping

from app.schemas.refresh import ChangeEnvelope, RefreshError, normalize_change_envelope


class EventIngressError(RefreshError):
    """Rejected managed event with a stable, non-secret reason code."""


_ALLOWED_ENVELOPE_FIELDS = frozenset({
    "version",
    "envelope_version",
    "schema_version",
    "event_id",
    "source_id",
    "resource",
    "operation",
    "primary_key",
    "payload",
    "watermark",
    "source_cursor",
    "contract",
    "cursor_contract",
    "schema_hash",
    "schema_hash_drift",
    "occurred_at",
    "received_at",
})


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _parse_datetime(value: object, *, field: str) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return _as_utc(value)
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value, tz=timezone.utc)
        except (OverflowError, OSError, ValueError) as exc:
            raise EventIngressError("INVALID_CHANGE_ENVELOPE", f"invalid {field}") from exc
    if not isinstance(value, str):
        raise EventIngressError("INVALID_CHANGE_ENVELOPE", f"invalid {field}")
    text = value.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return _as_utc(datetime.fromisoformat(text))
    except ValueError:
        try:
            return datetime.fromtimestamp(float(text), tz=timezone.utc)
        except (OverflowError, OSError, ValueError) as exc:
            reason = "INVALID_SIGNATURE" if field == "timestamp" else "INVALID_CHANGE_ENVELOPE"
            raise EventIngressError(reason, f"invalid {field}") from exc


def _secret_bytes(secret_ref: str | bytes, resolver: Callable[[str], str | bytes] | None) -> bytes:
    if resolver is not None:
        try:
            secret = resolver(secret_ref.decode("utf-8") if isinstance(secret_ref, bytes) else secret_ref)
        except Exception as exc:  # do not leak resolver/backend details to callers
            raise EventIngressError("SECRET_UNAVAILABLE", "managed webhook secret is unavailable") from exc
    else:
        secret = secret_ref
    if isinstance(secret, str):
        secret = secret.encode("utf-8")
    if not isinstance(secret, bytes) or not secret:
        raise EventIngressError("SECRET_UNAVAILABLE", "managed webhook secret is unavailable")
    return secret


def _signature_value(signature: str) -> str:
    if not isinstance(signature, str) or not signature:
        return ""
    value = signature.strip()
    if "=" in value:
        prefix, digest = value.split("=", 1)
        if prefix.lower() not in {"sha256", "hmac-sha256"}:
            return ""
        return digest.strip()
    if value.lower().startswith("sha256:"):
        return value.split(":", 1)[1].strip()
    return value


def _canonical_schema_hash(payload: Mapping[str, object]) -> str:
    """Derive a stable shape hash when a managed source omits one."""
    shape = {str(key): type(value).__name__ for key, value in sorted(payload.items(), key=lambda item: str(item[0]))}
    encoded = json.dumps(shape, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _normalize_record(
    record: Mapping[str, object], *, source_id: str, received_at: datetime,
) -> ChangeEnvelope:
    unknown = set(record) - _ALLOWED_ENVELOPE_FIELDS
    if unknown:
        raise EventIngressError("INVALID_CHANGE_ENVELOPE", "unsupported outbox envelope field")

    version = record.get("version", record.get("envelope_version", record.get("schema_version")))
    if version not in (1, "1"):
        raise EventIngressError("INVALID_CHANGE_ENVELOPE", "unsupported event envelope version")

    event_id = record.get("event_id")
    if not isinstance(event_id, str) or not event_id:
        raise EventIngressError("INVALID_CHANGE_ENVELOPE", "missing event identity")
    if record.get("source_id", source_id) != source_id:
        raise EventIngressError("INVALID_CHANGE_ENVELOPE", "source/resource mismatch")
    resource = record.get("resource")
    if not isinstance(resource, str) or not resource:
        raise EventIngressError("INVALID_CHANGE_ENVELOPE", "missing resource")

    if record.get("schema_hash_drift"):
        raise EventIngressError("SCHEMA_DRIFT", "source schema hash drift")

    payload = record.get("payload", {})
    if not isinstance(payload, Mapping):
        raise EventIngressError("INVALID_CHANGE_ENVELOPE", "payload must be an object")
    operation = record.get("operation")
    if operation not in ("upsert", "delete"):
        raise EventIngressError("INVALID_CHANGE_ENVELOPE", "invalid operation")

    watermark = record.get("watermark")
    source_cursor = record.get("source_cursor")
    explicit_contract = record.get("cursor_contract", record.get("contract"))
    contract = explicit_contract or (
        "opaque_source_cursor" if source_cursor is not None else "watermark_primary_key"
    )
    if contract not in ("watermark_primary_key", "opaque_source_cursor"):
        raise EventIngressError("INVALID_CHANGE_ENVELOPE", "unsupported cursor contract")
    primary_key = record.get("primary_key")
    if primary_key is None and contract == "opaque_source_cursor":
        primary_key = event_id
    if not isinstance(primary_key, str) or not primary_key:
        if primary_key is not None:
            primary_key = str(primary_key)
        else:
            raise EventIngressError("INVALID_CHANGE_ENVELOPE", "invalid cursor shape: missing primary_key")
    if contract == "watermark_primary_key" and watermark in (None, ""):
        raise EventIngressError("INVALID_CHANGE_ENVELOPE", "invalid cursor shape: missing watermark")
    if contract == "opaque_source_cursor" and source_cursor in (None, ""):
        raise EventIngressError("INVALID_CHANGE_ENVELOPE", "invalid cursor shape: missing source_cursor")

    occurred_at = _parse_datetime(record.get("occurred_at"), field="occurred_at")
    schema_hash = record.get("schema_hash")
    if schema_hash is None:
        schema_hash = _canonical_schema_hash(payload)
    if not isinstance(schema_hash, str) or not schema_hash:
        raise EventIngressError("INVALID_CHANGE_ENVELOPE", "invalid schema hash")

    raw = {
        "event_id": event_id,
        "source_id": source_id,
        "resource": resource,
        "operation": operation,
        "primary_key": primary_key,
        "payload": dict(payload),
        "watermark": watermark,
        "source_cursor": source_cursor,
        "contract": contract,
        "schema_hash": schema_hash,
        "occurred_at": occurred_at,
    }
    try:
        envelope = normalize_change_envelope(
            raw, source_id=source_id, resource=resource, received_at=_as_utc(received_at),
        )
    except RefreshError as exc:
        raise EventIngressError(exc.reason_code, str(exc)) from exc
    return envelope


class ManagedWebhookAdapter:
    """Verify a source-signed webhook before parsing its JSON payload."""

    def __init__(
        self,
        *,
        max_age_seconds: int = 300,
        secret_resolver: Callable[[str], str | bytes] | None = None,
    ):
        self.max_age_seconds = max(1, int(max_age_seconds))
        self.secret_resolver = secret_resolver

    def verify_and_normalize(
        self,
        body: bytes,
        *,
        source_id: str,
        signature: str,
        timestamp: str,
        secret_ref: str,
        now: datetime,
    ) -> ChangeEnvelope:
        if not isinstance(body, (bytes, bytearray)):
            raise EventIngressError("INVALID_SIGNATURE", "signed webhook body must be bytes")
        signed_at = _parse_datetime(timestamp, field="timestamp")
        if signed_at is None:
            raise EventIngressError("INVALID_SIGNATURE", "missing webhook timestamp")
        secret = _secret_bytes(secret_ref, self.secret_resolver)
        digest = _signature_value(signature)
        candidates = (
            timestamp.encode("utf-8") + b"." + bytes(body),
            bytes(body),
        )
        valid = any(
            hmac.compare_digest(
                hmac.new(secret, candidate, hashlib.sha256).hexdigest(), digest,
            )
            for candidate in candidates
        )
        if not digest or not valid:
            raise EventIngressError("INVALID_SIGNATURE", "webhook signature verification failed")

        now = _as_utc(now)
        age = (now - signed_at).total_seconds()
        if age < -self.max_age_seconds or age > self.max_age_seconds:
            raise EventIngressError("EXPIRED_SIGNATURE", "webhook timestamp is outside replay window")

        try:
            record = json.loads(bytes(body).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise EventIngressError("INVALID_CHANGE_ENVELOPE", "invalid webhook JSON") from exc
        if not isinstance(record, Mapping):
            raise EventIngressError("INVALID_CHANGE_ENVELOPE", "webhook payload must be an object")
        # Replay/duplicate-with-different-payload detection is the durable
        # inbox comparison in `EventIngestService.accept()`, not an
        # in-memory cache here: the router constructs a fresh adapter per
        # request, so any per-instance cache would never persist.
        return _normalize_record(record, source_id=source_id, received_at=now)


class ManagedOutboxAdapter:
    """Normalize a versioned managed outbox record without broker coupling."""

    @staticmethod
    def normalize(
        record: Mapping[str, object], *, source_id: str, received_at: datetime,
    ) -> ChangeEnvelope:
        if not isinstance(record, Mapping):
            raise EventIngressError("INVALID_CHANGE_ENVELOPE", "outbox record must be an object")
        return _normalize_record(record, source_id=source_id, received_at=_as_utc(received_at))


__all__ = [
    "EventIngressError",
    "ManagedOutboxAdapter",
    "ManagedWebhookAdapter",
]

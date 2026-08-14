"""24-hour idempotency-key persistence machinery (I-BACKEND).

Implements the Section 12 idempotency contract: mutation requests marked
`Idem` carry an `Idempotency-Key` (16-128 printable ASCII) that is persisted
with actor, route and the canonical request hash for 24 hours.  A same-key
retry with a *different* canonical hash is `409 IDEMPOTENCY_KEY_REUSED`; a
same-key retry with the *same* hash passes through so the owning route's own
replay semantics (fresh stream tickets, turn_id replay, publish CAS) apply —
no stored response or secret is ever replayed by this machinery.

Persistence is raw SQL against the `agent_idempotency_keys` table created by
the `0006_agent_runtime` migration (folded from the former 0007 per I-6); the
table is deliberately not registered in the ORM metadata so the E0-DB exact
table-set contract stays untouched.
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

IDEMPOTENCY_TTL_HOURS = 24


class IdempotencyKeyReusedError(Exception):
    """The same key was already persisted under a different request hash."""


def canonical_request_hash(method: str, path: str, body: bytes) -> str:
    """Stable SHA-256 over (method, path, raw body)."""
    digest = hashlib.sha256()
    digest.update(method.upper().encode())
    digest.update(b"\n")
    digest.update(path.encode())
    digest.update(b"\n")
    digest.update(body or b"")
    return digest.hexdigest()


def persist_idempotency(db, *, key: str, actor_id: str, route: str,
                        request_hash: str) -> str:
    """Persist (actor, key, route, hash) for 24 hours.

    Returns ``"stored"`` on first write and ``"replay"`` when the identical
    key/hash already exists.  Raises :class:`IdempotencyKeyReusedError` when
    the key exists under a different hash or route.
    """
    from sqlalchemy.exc import IntegrityError

    existing = lookup_idempotency(db, key=key, actor_id=actor_id)
    if existing is not None:
        if existing["request_hash"] != request_hash or existing["route"] != route:
            raise IdempotencyKeyReusedError("IDEMPOTENCY_KEY_REUSED")
        return "replay"
    try:
        db.execute(text(
            "INSERT INTO agent_idempotency_keys "
            "(id, actor_id, idempotency_key, route, request_hash, created_at, expires_at) "
            "VALUES (:id, :actor, :key, :route, :hash, now(), :expires)"
        ), {
            "id": str(uuid.uuid4()),
            "actor": actor_id,
            "key": key,
            "route": route,
            "hash": request_hash,
            "expires": datetime.now(timezone.utc) + timedelta(hours=IDEMPOTENCY_TTL_HOURS),
        })
        db.commit()
        return "stored"
    except IntegrityError:
        db.rollback()
        existing = lookup_idempotency(db, key=key, actor_id=actor_id)
        if existing is not None and (
            existing["request_hash"] != request_hash or existing["route"] != route
        ):
            raise IdempotencyKeyReusedError("IDEMPOTENCY_KEY_REUSED") from None
        return "replay"


def lookup_idempotency(db, *, key: str, actor_id: str) -> dict | None:
    """Return the persisted record for (actor, key) or None.

    Expired records are treated as absent and swept lazily.
    """
    row = db.execute(text(
        "SELECT idempotency_key, actor_id, route, request_hash, expires_at "
        "FROM agent_idempotency_keys "
        "WHERE actor_id = :actor AND idempotency_key = :key "
        "AND expires_at > now()"
    ), {"actor": actor_id, "key": key}).mappings().one_or_none()
    if row is None:
        db.execute(text(
            "DELETE FROM agent_idempotency_keys "
            "WHERE actor_id = :actor AND idempotency_key = :key AND expires_at <= now()"
        ), {"actor": actor_id, "key": key})
        db.commit()
        return None
    return dict(row)


def sweep_expired_idempotency(db) -> int:
    """Delete every expired row; return the number removed."""
    result = db.execute(text(
        "DELETE FROM agent_idempotency_keys WHERE expires_at <= now()"
    ))
    db.commit()
    return result.rowcount or 0


def actor_id_from_bearer(request) -> str | None:
    """Best-effort actor id from the Authorization bearer (None on failure)."""
    auth = request.headers.get("Authorization", "")
    if not auth.lower().startswith("bearer "):
        return None
    token = auth.split(" ", 1)[1].strip()
    if not token:
        return None
    try:
        from app.services.auth_service import decode_token
        payload = decode_token(token)
        return str(payload.get("sub") or payload.get("user_id") or "")
    except Exception:
        return None

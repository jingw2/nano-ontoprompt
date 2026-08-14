"""Idempotency-key persistence middleware (I-BACKEND wiring).

Wired into `app.main` via `create_idempotency_middleware()`.  For every
mutation request carrying an `Idempotency-Key`, the middleware computes the
canonical (method, path, body) hash and persists (actor, key, route, hash)
for 24 hours through `app.services.idempotency`.  Same key + different hash
is rejected with `409 IDEMPOTENCY_KEY_REUSED` before the route runs; same key
+ same hash passes through untouched so the owning route's replay semantics
prevail (fresh stream tickets, turn_id replay, publish CAS).  Requests
without a valid key, or whose actor cannot be derived, pass through untouched.
"""
from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import OperationalError

from app.services.idempotency import (
    IdempotencyKeyReusedError,
    actor_id_from_bearer,
    canonical_request_hash,
    persist_idempotency,
)

_MUTATION_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def _schema_absent(exc: OperationalError) -> bool:
    """True only when the failure is a missing persistence table (fail open)."""
    message = str(exc.orig or exc).lower()
    return "no such table" in message or "does not exist" in message or "undefined_table" in message


def create_idempotency_middleware(session_factory=None):
    """Return the ASGI middleware class to install via `app.add_middleware`.

    ``session_factory`` defaults to the application ``SessionLocal``; tests
    inject a scoped-schema factory.
    """
    if session_factory is None:
        from app.database import SessionLocal
        session_factory = SessionLocal

    class IdempotencyMiddleware:
        def __init__(self, app):
            self.app = app

        async def __call__(self, scope, receive, send):
            if scope["type"] != "http":
                return await self.app(scope, receive, send)
            if scope.get("method") not in _MUTATION_METHODS:
                return await self.app(scope, receive, send)

            headers = {
                name.decode("latin-1").lower(): value.decode("latin-1")
                for name, value in scope.get("headers", [])
            }
            key = headers.get("idempotency-key")
            if not key or not (16 <= len(key) <= 128 and key.isprintable()):
                return await self.app(scope, receive, send)

            request = Request(scope, receive)
            body = await request.body()
            actor = actor_id_from_bearer(request)

            # the body was consumed above; every downstream path replays it
            async def replay_receive():
                return {"type": "http.request", "body": body, "more_body": False}

            if not actor:
                return await self.app(scope, replay_receive, send)

            request_hash = canonical_request_hash(scope["method"], scope["path"], body)
            db = session_factory()
            try:
                try:
                    persist_idempotency(
                        db, key=key, actor_id=actor,
                        route=scope["path"], request_hash=request_hash,
                    )
                except IdempotencyKeyReusedError:
                    response = JSONResponse(
                        {"detail": "IDEMPOTENCY_KEY_REUSED"}, status_code=409,
                    )
                    await response(scope, receive, send)
                    return
                except OperationalError as exc:
                    # Narrow fail-open: when the persistence table is absent
                    # (e.g. the SQLite unit harness, which has no 0006
                    # migration), pass through so route-level idempotency and
                    # auth still apply.  Any other operational error is real.
                    if not _schema_absent(exc):
                        raise
                    return await self.app(scope, replay_receive, send)
            finally:
                db.close()

            return await self.app(scope, replay_receive, send)

    return IdempotencyMiddleware

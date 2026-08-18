"""Tool provider/connection/version administration (P7A external tools,
Section 8/10 of the implementation spec). Providers are the top-level
identity ('this is our Search provider'); connections are named endpoints
under a provider; versions are the immutable, individually-approved
configuration snapshots the Gateway and Agent bindings actually reference."""
from __future__ import annotations

import uuid

from sqlalchemy import text
from sqlalchemy.orm import Session

PROVIDER_KINDS = ("search", "playwright", "skill", "external_mcp", "ontology_mcp")


class ToolConnectionError(Exception):
    """Rejected tool-provider/connection operation."""


def _new_id() -> str:
    return str(uuid.uuid4())


def create_provider(db: Session, *, actor_id: str, name: str, kind: str) -> dict:
    if kind not in PROVIDER_KINDS:
        raise ToolConnectionError("PROVIDER_KIND_INVALID")
    provider_id = _new_id()
    db.execute(text(
        "INSERT INTO tool_providers (id, name, status, kind, created_by, created_at, updated_at) "
        "VALUES (:id, :name, 'active', :kind, :actor, now(), now())"
    ), {"id": provider_id, "name": name, "kind": kind, "actor": actor_id})
    db.commit()
    return {"id": provider_id, "name": name, "kind": kind, "status": "active"}


def create_connection(db: Session, *, actor_id: str, provider_id: str) -> dict:
    exists = db.execute(text(
        "SELECT 1 FROM tool_providers WHERE id = :id"
    ), {"id": provider_id}).scalar_one_or_none()
    if exists is None:
        raise ToolConnectionError("PROVIDER_NOT_FOUND")
    connection_id = _new_id()
    db.execute(text(
        "INSERT INTO tool_connections (id, provider_id, status, created_by, created_at, updated_at) "
        "VALUES (:id, :provider, 'active', :actor, now(), now())"
    ), {"id": connection_id, "provider": provider_id, "actor": actor_id})
    db.commit()
    return {"id": connection_id, "provider_id": provider_id, "status": "active"}


def create_connection_version(db: Session, *, actor_id: str, connection_id: str, endpoint: str | None = None,
                              audience: str | None = None, scopes: list | None = None,
                              credential_reference: str | None = None, allowlists: dict | None = None) -> dict:
    exists = db.execute(text(
        "SELECT 1 FROM tool_connections WHERE id = :id"
    ), {"id": connection_id}).scalar_one_or_none()
    if exists is None:
        raise ToolConnectionError("CONNECTION_NOT_FOUND")
    next_version = db.execute(text(
        "SELECT COALESCE(MAX(version_no), 0) + 1 FROM tool_connection_versions WHERE connection_id = :id"
    ), {"id": connection_id}).scalar_one()
    version_id = _new_id()
    db.execute(text(
        "INSERT INTO tool_connection_versions "
        "(id, connection_id, version_no, endpoint, audience, scopes, credential_reference, "
        "allowlists, approval_status, health_status, created_by, created_at) "
        "VALUES (:id, :conn, :vno, :endpoint, :audience, CAST(:scopes AS json), :cred, "
        "CAST(:allow AS json), 'pending', 'unknown', :actor, now())"
    ), {"id": version_id, "conn": connection_id, "vno": next_version, "endpoint": endpoint,
        "audience": audience, "scopes": _json(scopes or []), "cred": credential_reference,
        "allow": _json(allowlists or {}), "actor": actor_id})
    db.commit()
    return {"id": version_id, "connection_id": connection_id, "version_no": next_version, "approval_status": "pending"}


def approve_connection_version(db: Session, *, actor_id: str, version_id: str) -> dict:
    updated = db.execute(text(
        "UPDATE tool_connection_versions SET approval_status = 'approved' WHERE id = :id RETURNING id"
    ), {"id": version_id}).scalar_one_or_none()
    if updated is None:
        raise ToolConnectionError("VERSION_NOT_FOUND")
    db.commit()
    return {"id": version_id, "approval_status": "approved"}


def activate_connection_version(db: Session, *, actor_id: str, connection_id: str, version_id: str) -> dict:
    approved = db.execute(text(
        "SELECT approval_status FROM tool_connection_versions WHERE id = :id AND connection_id = :conn"
    ), {"id": version_id, "conn": connection_id}).scalar_one_or_none()
    if approved is None:
        raise ToolConnectionError("VERSION_NOT_FOUND")
    if approved != "approved":
        raise ToolConnectionError("VERSION_NOT_APPROVED")
    db.execute(text(
        "UPDATE tool_connections SET active_version_id = :vid, updated_at = now() WHERE id = :id"
    ), {"vid": version_id, "id": connection_id})
    db.commit()
    return {"connection_id": connection_id, "active_version_id": version_id}


def test_connection_version(db: Session, *, version_id: str) -> dict:
    """Health probe for an approved connection version. Search performs a
    live single-result query; Playwright probes browser availability and
    domain-allowlist configuration without navigating anywhere. Any probe
    failure returns structured `unhealthy` state, never an exception."""
    row = db.execute(text(
        "SELECT tcv.approval_status, tcv.endpoint, tcv.credential_reference, tcv.allowlists, tp.kind "
        "FROM tool_connection_versions tcv "
        "JOIN tool_connections tc ON tc.id = tcv.connection_id "
        "JOIN tool_providers tp ON tp.id = tc.provider_id "
        "WHERE tcv.id = :id"
    ), {"id": version_id}).mappings().one_or_none()
    if row is None:
        raise ToolConnectionError("VERSION_NOT_FOUND")
    if row["approval_status"] != "approved":
        raise ToolConnectionError("VERSION_NOT_APPROVED")
    if row["kind"] == "search":
        from app.services.tools.search import SearchError, web_search
        try:
            web_search(endpoint=row["endpoint"] or "", api_key=row["credential_reference"],
                       query="health", result_limit=1)
            return {"status": "healthy", "detail": "search:ok"}
        except SearchError as exc:
            return {"status": "unhealthy", "detail": str(exc)}
    if row["kind"] == "playwright":
        allowlists = row["allowlists"] or {}
        domains = [str(d) for d in (allowlists.get("domains") or [])]
        if not domains:
            return {"status": "unhealthy", "detail": "PLAYWRIGHT_DOMAIN_ALLOWLIST_MISSING"}
        from app.services.tools.playwright import browse_page, PlaywrightError
        try:
            # availability probe only — never navigate a live page from a health check
            browse_page(url=f"https://{domains[0]}", allowed_domains=domains,
                        timeout_seconds=1, max_bytes=1)
        except PlaywrightError as exc:
            message = str(exc)
            if "UNAVAILABLE" in message:
                return {"status": "unhealthy", "detail": "PLAYWRIGHT_UNAVAILABLE"}
            return {"status": "healthy", "detail": f"playwright:ok ({message})"}
        return {"status": "healthy", "detail": "playwright:ok"}
    return {"status": "unhealthy", "detail": "PROVIDER_KIND_UNSUPPORTED"}


def list_providers(db: Session) -> list[dict]:
    rows = db.execute(text(
        "SELECT id, name, kind, status FROM tool_providers ORDER BY name"
    )).mappings().all()
    return [dict(r) for r in rows]


def list_connections(db: Session, provider_id: str | None = None) -> list[dict]:
    if provider_id is not None:
        rows = db.execute(text(
            "SELECT id, provider_id, status, active_version_id FROM tool_connections "
            "WHERE provider_id = :p ORDER BY id"
        ), {"p": provider_id}).mappings().all()
    else:
        rows = db.execute(text(
            "SELECT id, provider_id, status, active_version_id FROM tool_connections ORDER BY id"
        )).mappings().all()
    return [dict(r) for r in rows]


def _json(value) -> str:
    import json
    return json.dumps(value)

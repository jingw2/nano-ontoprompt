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
    domain-allowlist configuration via a capped navigation of the allowlisted
    domain. Any probe failure returns structured `unhealthy` state, never an
    exception. The verdict is persisted to the version's `health_status` so
    list views reflect probe results."""
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
        from app.services.tools.search import web_search
        try:
            web_search(endpoint=row["endpoint"] or "", api_key=row["credential_reference"],
                       query="health", result_limit=1)
            result = {"status": "healthy", "detail": "search:ok"}
        except Exception as exc:  # probe contract: never raise — non-dict JSON,
            # broken endpoints and network failures all surface as unhealthy
            result = {"status": "unhealthy", "detail": str(exc)}
    elif row["kind"] == "playwright":
        allowlists = row["allowlists"] or {}
        domains = [str(d) for d in (allowlists.get("domains") or [])]
        if not domains:
            result = {"status": "unhealthy", "detail": "PLAYWRIGHT_DOMAIN_ALLOWLIST_MISSING"}
        else:
            from app.services.tools.playwright import browse_page, PlaywrightError
            try:
                # probe the allowlisted domain with a 1-byte cap: a response —
                # even capped (RESPONSE_TOO_LARGE) or slow (TIMEOUT) — proves
                # the browser and sandbox engage. UNAVAILABLE (missing package)
                # or NAVIGATION_FAILED (missing binary / unreachable domain)
                # means the connection cannot render, and URL_BLOCKED /
                # DOMAIN_NOT_ALLOWED mean the stored allowlist itself is a
                # config defect — the probe never reached the browser, and
                # every agent call would fail closed forever.
                browse_page(url=f"https://{domains[0]}", allowed_domains=domains,
                            timeout_seconds=1, max_bytes=1)
                result = {"status": "healthy", "detail": "playwright:ok"}
            except PlaywrightError as exc:
                message = str(exc)
                if "UNAVAILABLE" in message or "NAVIGATION_FAILED" in message \
                        or "URL_BLOCKED" in message or "DOMAIN_NOT_ALLOWED" in message:
                    result = {"status": "unhealthy", "detail": message}
                else:
                    result = {"status": "healthy", "detail": f"playwright:ok ({message})"}
    elif row["kind"] == "external_mcp":
        schema_row = db.execute(text(
            "SELECT tool_schema_hash, quarantined FROM mcp_connection_schemas "
            "WHERE connection_version_id = :id"
        ), {"id": version_id}).mappings().one_or_none()
        token_row = db.execute(text(
            "SELECT encrypted_access_token, expires_at FROM mcp_oauth_tokens "
            "WHERE connection_version_id = :id"
        ), {"id": version_id}).mappings().one_or_none()
        allowlists = row["allowlists"] or {}
        domains = [str(d) for d in (allowlists.get("domains") or [])]
        if schema_row is None:
            result = {"status": "unhealthy", "detail": "MCP_SCHEMA_UNPINNED"}
        elif schema_row["quarantined"]:
            result = {"status": "unhealthy", "detail": "TOOL_SCHEMA_QUARANTINED"}
        elif token_row is None:
            result = {"status": "unhealthy", "detail": "MCP_TOKEN_MISSING"}
        elif not domains:
            result = {"status": "unhealthy", "detail": "MCP_DOMAIN_ALLOWLIST_MISSING"}
        else:
            from datetime import datetime, timezone
            expires_at = token_row["expires_at"]
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            if expires_at <= datetime.now(timezone.utc):
                result = {"status": "unhealthy", "detail": "MCP_TOKEN_EXPIRED"}
            else:
                from app.services.encryption_service import decrypt
                from app.services.tools.mcp_client import MCPClientError, list_tools, tools_schema_hash
                try:
                    tools = list_tools(endpoint=row["endpoint"] or "",
                                       access_token=decrypt(token_row["encrypted_access_token"]),
                                       allowed_domains=domains)
                    live_hash = tools_schema_hash(tools)
                    if live_hash != schema_row["tool_schema_hash"]:
                        result = {"status": "unhealthy", "detail": "TOOL_SCHEMA_QUARANTINED"}
                    else:
                        result = {"status": "healthy", "detail": "mcp:ok"}
                except MCPClientError as exc:
                    result = {"status": "unhealthy", "detail": str(exc)}
    else:
        result = {"status": "unhealthy", "detail": "PROVIDER_KIND_UNSUPPORTED"}
    db.execute(text(
        "UPDATE tool_connection_versions SET health_status = :h WHERE id = :id"
    ), {"h": result["status"], "id": version_id})
    db.commit()
    return result


def _require_approved_mcp_version(db: Session, version_id: str):
    """Shared lookup+validation for MCP admin actions: the version must
    exist, belong to an external_mcp provider, and be approved. Selects the
    columns both pin_mcp_schema and issue_mcp_token need (issue_mcp_token
    simply ignores endpoint/allowlists)."""
    row = db.execute(text(
        "SELECT tcv.approval_status, tcv.endpoint, tcv.allowlists, tp.kind "
        "FROM tool_connection_versions tcv "
        "JOIN tool_connections tc ON tc.id = tcv.connection_id "
        "JOIN tool_providers tp ON tp.id = tc.provider_id "
        "WHERE tcv.id = :id"
    ), {"id": version_id}).mappings().one_or_none()
    if row is None:
        raise ToolConnectionError("VERSION_NOT_FOUND")
    if row["kind"] != "external_mcp":
        raise ToolConnectionError("MCP_KIND_REQUIRED")
    if row["approval_status"] != "approved":
        raise ToolConnectionError("VERSION_NOT_APPROVED")
    return row


def pin_mcp_schema(db: Session, *, actor_id: str, version_id: str) -> dict:
    """Admin action: introspect the remote MCP server's tools/list and pin
    the result as the approved shape. Re-running this after a legitimate
    server-side change is the ONLY way to clear a prior quarantine — it is
    always an explicit, audited admin action, never automatic."""
    row = _require_approved_mcp_version(db, version_id)
    domains = [str(d) for d in ((row["allowlists"] or {}).get("domains") or [])]
    if not domains:
        raise ToolConnectionError("MCP_DOMAIN_ALLOWLIST_MISSING")
    token_row = db.execute(text(
        "SELECT encrypted_access_token FROM mcp_oauth_tokens WHERE connection_version_id = :id"
    ), {"id": version_id}).mappings().one_or_none()
    from app.services.encryption_service import decrypt
    access_token = decrypt(token_row["encrypted_access_token"]) if token_row else ""
    from app.services.tools.mcp_client import MCPClientError, list_tools, tools_schema_hash
    try:
        tools = list_tools(endpoint=row["endpoint"] or "", access_token=access_token, allowed_domains=domains)
    except MCPClientError as exc:
        raise ToolConnectionError(f"MCP_INTROSPECTION_FAILED:{exc}") from exc
    schema_hash = tools_schema_hash(tools)
    schema_id = _new_id()
    db.execute(text(
        "INSERT INTO mcp_connection_schemas "
        "(id, connection_version_id, tool_schema_hash, tools, quarantined, pinned_by, pinned_at) "
        "VALUES (:id, :cv, :hash, CAST(:tools AS json), false, :actor, now()) "
        "ON CONFLICT (connection_version_id) DO UPDATE SET "
        "tool_schema_hash = EXCLUDED.tool_schema_hash, tools = EXCLUDED.tools, "
        "quarantined = false, quarantined_at = NULL, quarantined_reason = NULL, "
        "pinned_by = EXCLUDED.pinned_by, pinned_at = now()"
    ), {"id": schema_id, "cv": version_id, "hash": schema_hash, "tools": _json(tools), "actor": actor_id})
    db.commit()
    return {"connection_version_id": version_id, "tool_schema_hash": schema_hash, "tool_count": len(tools)}


def issue_mcp_token(db: Session, *, actor_id: str, version_id: str, access_token: str,
                    refresh_token: str | None = None, expires_in_seconds: int, scope: list[str],
                    audience: str | None = None) -> dict:
    """Admin action: store an out-of-band-obtained confidential-client bearer
    token, encrypted at rest. Replaces any prior token for this version
    (rotation) — issuing a fresh token is how an admin recovers from
    MCP_TOKEN_EXPIRED."""
    _require_approved_mcp_version(db, version_id)
    from app.services.encryption_service import encrypt
    token_id = _new_id()
    db.execute(text(
        "INSERT INTO mcp_oauth_tokens "
        "(id, connection_version_id, encrypted_access_token, encrypted_refresh_token, "
        "scope, audience, expires_at, issued_by, rotated_at) "
        "VALUES (:id, :cv, :access, :refresh, CAST(:scope AS json), :aud, "
        "now() + (:ttl || ' seconds')::interval, :actor, now()) "
        "ON CONFLICT (connection_version_id) DO UPDATE SET "
        "encrypted_access_token = EXCLUDED.encrypted_access_token, "
        "encrypted_refresh_token = EXCLUDED.encrypted_refresh_token, "
        "scope = EXCLUDED.scope, audience = EXCLUDED.audience, "
        "expires_at = EXCLUDED.expires_at, issued_by = EXCLUDED.issued_by, rotated_at = now()"
    ), {"id": token_id, "cv": version_id, "access": encrypt(access_token),
        "refresh": encrypt(refresh_token) if refresh_token else None,
        "scope": _json(scope), "aud": audience, "ttl": expires_in_seconds, "actor": actor_id})
    db.commit()
    return {"connection_version_id": version_id, "scope": scope, "expires_in_seconds": expires_in_seconds}


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


def list_connection_versions(db: Session, *, connection_id: str) -> list[dict]:
    rows = db.execute(text(
        "SELECT id, connection_id, version_no, endpoint, audience, scopes, credential_reference, "
        "allowlists, approval_status, health_status, created_by, created_at "
        "FROM tool_connection_versions WHERE connection_id = :id ORDER BY version_no DESC"
    ), {"id": connection_id}).mappings().all()
    return [dict(r) for r in rows]


def _json(value) -> str:
    import json
    return json.dumps(value)

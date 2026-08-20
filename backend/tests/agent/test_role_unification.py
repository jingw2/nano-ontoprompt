"""P2A-RBAC: unify application roles.

Legacy role values (`user`, unknown) map to the `viewer` ceiling; the closed
role set is `viewer|editor|admin` with an exact ceiling ordering.  Every write
route (POST/PUT/PATCH/DELETE) must carry an editor-or-admin dependency — no
route may be guarded by authentication alone.  The runtime normalization hook
keeps legacy values from the pre-0004 era effective as viewer.
"""
import uuid
from pathlib import Path

import pytest
from fastapi.routing import APIRoute
from sqlalchemy import text

from app.main import app
from app.services.auth_service import hash_password, create_access_token
from app.models.user import User


BACKEND_DIR = Path(__file__).resolve().parents[2]


def test_p2a_rbac_red_contract():
    failures = []
    auth_path = BACKEND_DIR / "app" / "services" / "authorization.py"
    if not auth_path.exists():
        failures.append("missing app/services/authorization.py")
    else:
        source = auth_path.read_text()
        for symbol in ("ROLE_RANK", "normalize_role", "role_allows", "normalize_legacy_roles"):
            if symbol not in source:
                failures.append(f"authorization.py missing {symbol}")
    deps_path = BACKEND_DIR / "app" / "deps" / "__init__.py"
    if "role_allows" not in deps_path.read_text():
        failures.append("deps.py does not use the centralized role mapping")
    user_schema = (BACKEND_DIR / "app" / "schemas" / "user.py").read_text()
    if "Literal" not in user_schema:
        failures.append("user schemas do not constrain role values")
    user_model = (BACKEND_DIR / "app" / "models" / "user.py").read_text()
    if "normalize_role" not in user_model:
        failures.append("User mapping does not expose a normalized role")
    if failures:
        pytest.fail("RED_P2A_RBAC: " + "; ".join(failures))


def _route_dependency_calls(route):
    seen = set()

    def walk(deps):
        for dep in deps:
            call = getattr(dep, "call", None)
            if call is not None and call not in seen:
                seen.add(call)
            nested = getattr(dep, "dependencies", None) or []
            if nested:
                walk(nested)

    walk(route.dependant.dependencies)
    return seen


def test_endpoint_allow_deny_inventory_no_bypass():
    """Every write route carries an editor-or-admin dependency, except the
    explicitly exempt public/read-only/system surfaces: auth endpoints,
    read-only graph queries (cypher/ask/search), incremental worker
    callbacks, the granular-capability Agent/application routes whose
    Section 12 authorization is grant/session-owner/designated-actor based
    (checked inside the route body, not via an editor-or-admin dependency),
    and the OAuth authorize/consent/token/revoke surfaces whose RFC
    6749/7636/7009 authorization is end-user-session or PKCE/refresh-token
    possession based (also checked in the route body, not role-based)."""
    from app.deps import require_admin, require_editor

    exempt = {
        ("POST", "/api/v1/auth/login"),
        ("POST", "/api/v1/auth/refresh"),
        ("POST", "/api/v1/auth/logout"),
        ("POST", "/api/v1/auth/register"),
        ("PUT", "/api/v1/auth/password"),
        ("POST", "/api/v2/ontologies/{ontology_id}/graph/cypher"),
        ("POST", "/api/v2/ontologies/{ontology_id}/graph/ask"),
        ("POST", "/api/v2/ontologies/{ontology_id}/search"),
        ("POST", "/api/v2/incremental/connections/{connection_id}/sync-complete"),
        ("POST", "/api/v2/incremental/pipeline-runs/{run_id}/complete"),
        ("POST", "/api/v2/incremental/reviews/{review_id}/approve-trigger"),
        ("POST", "/api/v1/oauth/consent"),
        ("POST", "/api/v1/oauth/token"),
        ("POST", "/api/v1/oauth/revoke"),
        # I-BACKEND registration: granular-capability Agent/application routes
        ("POST", "/api/v1/ontologies/{ontology_id}/access-grants"),
        ("POST", "/api/v1/ontologies/{ontology_id}/access-grants/{grant_id}/revisions"),
        ("POST", "/api/v1/ontologies/{ontology_id}/access-grants/{grant_id}/revoke"),
        ("PATCH", "/api/v1/ontologies/{ontology_id}/migration-remediations/{kind}/{item_id}"),
        ("POST", "/api/v1/agents/{agent_id}/sessions"),
        ("DELETE", "/api/v1/agent-sessions/{session_id}"),
        ("POST", "/api/v1/agent-sessions/{session_id}/turns"),
        ("POST", "/api/v1/agent-turns/{turn_id}/cancel"),
        ("POST", "/api/v1/agent-turns/{turn_id}/stream-ticket"),
        ("POST", "/api/v1/agent-approvals/{approval_id}/approve"),
        ("POST", "/api/v1/agent-approvals/{approval_id}/reject"),
        ("POST", "/api/v1/agent-clarifications/{clarification_id}/answer"),
        ("POST", "/api/v1/agent-sessions/{session_id}/application-state"),
    }
    bypasses = []
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        methods = set(route.methods or set()) & {"POST", "PUT", "PATCH", "DELETE"}
        if not methods:
            continue
        for method in sorted(methods):
            if (method, route.path) in exempt:
                continue
            calls = _route_dependency_calls(route)
            if require_editor not in calls and require_admin not in calls:
                bypasses.append(f"{method} {route.path}")
                break
    assert bypasses == [], f"RBAC_BYPASS write routes without editor/admin guard: {bypasses}"


def test_role_ceiling_mapping():
    from app.services.authorization import ROLE_RANK, normalize_role, role_allows

    assert ROLE_RANK == {"viewer": 0, "editor": 1, "admin": 2}
    assert normalize_role("viewer") == "viewer"
    assert normalize_role("editor") == "editor"
    assert normalize_role("admin") == "admin"
    # legacy `user` and unknown values map to the viewer ceiling
    assert normalize_role("user") == "viewer"
    assert normalize_role("root") == "viewer"
    assert normalize_role(None) == "viewer"
    assert normalize_role("") == "viewer"

    assert role_allows("admin", "admin") is True
    assert role_allows("admin", "editor") is True
    assert role_allows("editor", "editor") is True
    assert role_allows("editor", "admin") is False
    assert role_allows("viewer", "editor") is False
    assert role_allows("viewer", "admin") is False
    assert role_allows("viewer", "viewer") is True
    # legacy role is not a bypass
    assert role_allows("user", "editor") is False
    assert role_allows("user", "admin") is False


def test_user_model_exposes_normalized_role(db):
    from app.services.authorization import normalize_role

    legacy = User(id=str(uuid.uuid4()), username="legacy-role", email="legacy@test.com",
                  password_hash=hash_password("x"), role="user")
    db.add(legacy)
    db.commit()
    db.refresh(legacy)
    assert legacy.role == "user"  # raw storage unchanged (DB backfill is 0004's job)
    assert legacy.normalized_role == "viewer"
    admin = User(id=str(uuid.uuid4()), username="role-admin", email="admin@test.com",
                 password_hash=hash_password("x"), role="admin")
    assert admin.normalized_role == "admin"


def test_runtime_normalization_hook_normalizes_legacy_roles(db):
    from app.services.authorization import normalize_legacy_roles

    for idx, role in enumerate(("user", "root", "viewer")):
        db.add(User(id=str(uuid.uuid4()), username=f"hook-{idx}", email=f"hook{idx}@test.com",
                    password_hash=hash_password("x"), role=role))
    db.commit()
    updated = normalize_legacy_roles(db)
    assert updated >= 2  # 'user' and 'root' normalized; 'viewer' untouched
    roles = [r for r in db.execute(text("SELECT role FROM users WHERE username LIKE 'hook-%'")).scalars()]
    assert set(roles) == {"viewer"}


def test_viewer_token_denied_on_model_write_routes(client, db):
    from app.services.auth_service import create_access_token
    from app.models.user import User

    viewer = User(id=str(uuid.uuid4()), username="viewer-rbac", email="vr@test.com",
                  password_hash=hash_password("x"), role="viewer")
    db.add(viewer)
    db.commit()
    token = create_access_token({"sub": viewer.id, "role": "viewer"})
    headers = {"Authorization": f"Bearer {token}"}
    # model create/update/delete are editor/admin-only
    assert client.post("/api/v1/models", json={"name": "m", "provider": "openai", "models": ["gpt-4o"]}, headers=headers).status_code in (403, 404)
    assert client.post("/api/v1/ontologies", json={"name": "o", "domain": "test"}, headers=headers).status_code in (403, 404)
    # admin token succeeds on the same write surface
    admin = User(id=str(uuid.uuid4()), username="admin-rbac", email="ar@test.com",
                 password_hash=hash_password("x"), role="admin")
    db.add(admin)
    db.commit()
    admin_headers = {"Authorization": f"Bearer {create_access_token({'sub': admin.id, 'role': 'admin'})}"}
    r = client.post("/api/v1/ontologies", json={"name": "o-admin", "domain": "test"}, headers=admin_headers)
    # admin passes the role gate; body validation is a separate concern
    assert r.status_code != 403

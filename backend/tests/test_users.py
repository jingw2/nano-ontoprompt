"""Focused user-route regressions (F0-SECURITY owns this file).

Covers the admin user CRUD contract plus the atomic soft-delete and
deactivate transitions that revoke refresh families without physical delete.
SQLite unit harness only; the PostgreSQL revocation semantics live in
tests/agent/test_account_revocation_security_headers.py.
"""
import uuid

from app.services.auth_service import hash_password
from app.models.user import User


def test_list_users_requires_admin(client, admin_token):
    response = client.get("/api/v1/users", headers={"Authorization": f"Bearer {admin_token}"})
    assert response.status_code == 200
    assert isinstance(response.json()["data"], list)


def test_create_user_then_get_and_update(client, admin_token, db):
    created = client.post(
        "/api/v1/users",
        json={"username": "sec-user", "email": "sec-user@test.com", "password": "pass123", "role": "editor"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert created.status_code == 201
    user_id = created.json()["data"]["id"]
    fetched = client.get(f"/api/v1/users/{user_id}", headers={"Authorization": f"Bearer {admin_token}"})
    assert fetched.status_code == 200
    assert fetched.json()["data"]["username"] == "sec-user"
    updated = client.put(
        f"/api/v1/users/{user_id}",
        json={"role": "admin"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert updated.status_code == 200
    assert updated.json()["data"]["role"] == "admin"


def test_soft_delete_returns_success_contract_and_retains_row(client, admin_token, db):
    user = User(id=str(uuid.uuid4()), username="to-delete", email="to-delete@test.com",
                password_hash=hash_password("pass123"), role="viewer")
    db.add(user)
    db.commit()
    deleted = client.delete(f"/api/v1/users/{user.id}", headers={"Authorization": f"Bearer {admin_token}"})
    assert deleted.status_code == 204
    retained = db.get(User, user.id)
    assert retained is not None and retained.is_active is False
    db.refresh(user)


def test_deactivate_user_marks_inactive(client, admin_token, db):
    user = User(id=str(uuid.uuid4()), username="to-deactivate", email="to-deactivate@test.com",
                password_hash=hash_password("pass123"), role="viewer")
    db.add(user)
    db.commit()
    response = client.post(
        f"/api/v1/users/{user.id}/deactivate",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    assert response.json()["data"]["is_active"] is False
    assert db.get(User, user.id).is_active is False


def test_cannot_delete_self(client, admin_user, admin_token):
    response = client.delete(f"/api/v1/users/{admin_user.id}", headers={"Authorization": f"Bearer {admin_token}"})
    assert response.status_code == 400


def test_user_routes_deny_non_admin(client, editor_user, db):
    from app.services.auth_service import create_access_token

    editor_token = create_access_token({"sub": editor_user.id, "role": "editor"})
    headers = {"Authorization": f"Bearer {editor_token}"}
    assert client.get("/api/v1/users", headers=headers).status_code == 403
    assert client.post(
        "/api/v1/users",
        json={"username": "x", "email": "x@test.com", "password": "pass123", "role": "viewer"},
        headers=headers,
    ).status_code == 403

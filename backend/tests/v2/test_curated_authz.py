"""Curated 审批权限 — PRD Security Logic: only admin can approve curated rows"""
import uuid

import pytest

from app.models.v2.curated import CuratedDataset


@pytest.fixture
def curated_client(client, db):
    """v2 curated 路由用模块内 get_db, 需单独覆盖到测试库"""
    from app.main import app
    from app.routers.v2 import curated

    def _override():
        yield db

    app.dependency_overrides[curated.get_db] = _override
    yield client
    app.dependency_overrides.pop(curated.get_db, None)


def _make_curated(db) -> str:
    ds = CuratedDataset(name=f"authz-{uuid.uuid4().hex[:6]}", status="pending_review", quality_score=0.8)
    db.add(ds)
    db.commit()
    db.refresh(ds)
    return ds.id


def _login(client, username, password):
    r = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    return {"Authorization": f"Bearer {r.json()['data']['access_token']}"}


def test_editor_cannot_approve_curated(curated_client, db, editor_user):
    ds_id = _make_curated(db)
    headers = _login(curated_client, "editor", "editor123")
    r = curated_client.post(f"/api/v2/curated/{ds_id}/review", params={"action": "approve"}, headers=headers)
    assert r.status_code == 403


def test_admin_can_approve_curated(curated_client, db, admin_user):
    ds_id = _make_curated(db)
    headers = _login(curated_client, "admin", "admin123")
    r = curated_client.post(f"/api/v2/curated/{ds_id}/review", params={"action": "approve"}, headers=headers)
    assert r.status_code == 200
    db.refresh(db.query(CuratedDataset).filter(CuratedDataset.id == ds_id).first())
    assert db.query(CuratedDataset).filter(CuratedDataset.id == ds_id).first().status == "approved"

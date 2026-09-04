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


def test_review_session_flow_works_with_no_pipeline_run_binding(curated_client, db, editor_user, admin_user):
    """The real curated-review UI (`frontend/src/api/v2/curated.ts`) sends no
    body at all to `POST .../reviews` or `.../approve` -- it has no concept
    of "which pipeline run" and predates that binding, which only the
    business-journey eval harness ever supplies. `PipelineRunBinding.
    pipeline_run_id` must stay optional so a plain human review/approve
    (with a genuinely empty body, not a JSON body missing the key) still
    works end to end."""
    ds_id = _make_curated(db)
    editor_headers = _login(curated_client, "editor", "editor123")
    started = curated_client.post(f"/api/v2/curated/{ds_id}/reviews", headers=editor_headers)
    assert started.status_code == 200, started.text
    review_id = started.json()["review_id"]

    admin_headers = _login(curated_client, "admin", "admin123")
    approved = curated_client.post(f"/api/v2/curated/reviews/{review_id}/approve", headers=admin_headers)
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "approved"

"""P2A-MODEL: editing a versioned LLM config's model list must not require a
fully verified per-model contract the admin edit UI never collects.

Regression: `update_model` (PUT /models/{id}) and `create_model_version`
(POST /models/{id}/versions) both build a `legacy_contract_for()` fallback
contract (every verified field NULL) whenever the caller changes `models`
without supplying an explicit `model_contract` — but routed that fallback
through `create_next_version()`'s strict `_validate_contract()`, which
rejects any NULL verified field. That made every such edit 500 with
MODEL_CONTRACT_INVALID. `create_model()`'s own bootstrap already accepts
this exact unverified shape without validation; `validate_contract=False`
extends that same trust class to routine models-list edits.
"""
import os
import subprocess
import sys
import uuid
from pathlib import Path
from urllib.parse import quote

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.main import app
from app.services.auth_service import create_access_token

BACKEND_DIR = Path(__file__).resolve().parents[2]
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
DEFAULT_DOMAIN = "00000000-0000-0000-0000-000000000001"


def _scoped_url(schema: str) -> str:
    return f"{TEST_DATABASE_URL}?options={quote(f'-csearch_path={schema}', safe='-=')}"


def _alembic(schema: str, *args, check=True):
    return subprocess.run(
        [sys.executable, "scripts/run_migrations.py", *args],
        cwd=BACKEND_DIR, env=dict(os.environ, DATABASE_URL=_scoped_url(schema)),
        capture_output=True, text=True, check=check,
    )


@pytest.fixture
def ctx():
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL required")
    schema = "p2a_model_upd_" + uuid.uuid4().hex
    engine = create_engine(TEST_DATABASE_URL)
    with engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    assert _alembic(schema, "upgrade", "head").returncode == 0
    Session = sessionmaker(bind=create_engine(_scoped_url(schema)))
    with Session() as session:
        editor_id = str(uuid.uuid4())
        session.execute(text(
            "INSERT INTO users (id,username,email,password_hash,role,is_active,security_domain_id,created_at,updated_at) "
            "VALUES (:id,'model-editor','me@t.com','h','editor',true,:d,now(),now())"
        ), {"id": editor_id, "d": DEFAULT_DOMAIN})
        session.commit()
        yield session, editor_id
    with engine.begin() as connection:
        connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
    engine.dispose()


def _client(session):
    from app.deps import get_db

    def override_get_db():
        yield session

    app.dependency_overrides[get_db] = override_get_db
    yield app
    app.dependency_overrides.clear()


def test_update_model_with_changed_models_list_creates_new_version(ctx):
    from fastapi.testclient import TestClient
    session, editor_id = ctx
    headers = {"Authorization": f"Bearer {create_access_token({'sub': editor_id, 'role': 'editor'})}"}
    client = next(_client(session))
    try:
        with TestClient(client) as c:
            created = c.post("/api/v1/models", json={
                "name": "Deepseek", "provider": "compatible",
                "models": ["deepseek-v4-flash", "deepseek-v4-pro"],
            }, headers=headers)
            assert created.status_code == 201, created.text
            model_id = created.json()["data"]["id"]
            before_versions = c.get(f"/api/v1/models/{model_id}/versions", headers=headers).json()["data"]
            assert len(before_versions) == 1

            updated = c.put(f"/api/v1/models/{model_id}", json={
                "models": ["deepseek-v4-pro", "deepseek-v4-flash"],
            }, headers=headers)
            assert updated.status_code == 201, updated.text

            after_versions = c.get(f"/api/v1/models/{model_id}/versions", headers=headers).json()["data"]
            assert len(after_versions) == 2
            newest = max(after_versions, key=lambda v: v["version_no"])
            assert newest["model_contract"][0]["provider_model_revision"] == "deepseek-v4-pro"
            assert newest["model_contract"][1]["provider_model_revision"] == "deepseek-v4-flash"

            # the list/detail view reads `models` off the identity row, not
            # the new version — it must reflect the edit too, or the admin
            # UI shows the old order right after a successful save
            assert updated.json()["data"]["models"] == ["deepseek-v4-pro", "deepseek-v4-flash"]
            refetched = c.get(f"/api/v1/models/{model_id}", headers=headers).json()["data"]
            assert refetched["models"] == ["deepseek-v4-pro", "deepseek-v4-flash"]
            listed = c.get("/api/v1/models", headers=headers).json()["data"]
            listed_entry = next(m for m in listed if m["id"] == model_id)
            assert listed_entry["models"] == ["deepseek-v4-pro", "deepseek-v4-flash"]
    finally:
        app.dependency_overrides.clear()


def test_create_model_version_with_only_models_omitting_contract_succeeds(ctx):
    from fastapi.testclient import TestClient
    session, editor_id = ctx
    headers = {"Authorization": f"Bearer {create_access_token({'sub': editor_id, 'role': 'editor'})}"}
    client = next(_client(session))
    try:
        with TestClient(client) as c:
            created = c.post("/api/v1/models", json={
                "name": "Deepseek2", "provider": "compatible", "models": ["deepseek-v4-flash"],
            }, headers=headers)
            model_id = created.json()["data"]["id"]

            versioned = c.post(f"/api/v1/models/{model_id}/versions", json={
                "models": ["deepseek-v4-pro", "deepseek-v4-flash"],
            }, headers=headers)
            assert versioned.status_code == 201, versioned.text

            refetched = c.get(f"/api/v1/models/{model_id}", headers=headers).json()["data"]
            assert refetched["models"] == ["deepseek-v4-pro", "deepseek-v4-flash"]
    finally:
        app.dependency_overrides.clear()


def test_create_model_version_with_incomplete_explicit_contract_still_rejected(ctx):
    """Validation stays strict when the caller actually asserts a
    model_contract — only the legacy_contract_for() fallback is exempt."""
    from fastapi.testclient import TestClient
    session, editor_id = ctx
    headers = {"Authorization": f"Bearer {create_access_token({'sub': editor_id, 'role': 'editor'})}"}
    client = next(_client(session))
    try:
        with TestClient(client) as c:
            created = c.post("/api/v1/models", json={
                "name": "Deepseek3", "provider": "compatible", "models": ["deepseek-v4-flash"],
            }, headers=headers)
            model_id = created.json()["data"]["id"]

            versioned = c.post(f"/api/v1/models/{model_id}/versions", json={
                "model_contract": [{"provider_model_revision": "deepseek-v4-flash"}],
            }, headers=headers)
            assert versioned.status_code == 422
    finally:
        app.dependency_overrides.clear()

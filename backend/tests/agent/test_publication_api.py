"""P1C-API: guarded lifecycle/release routes.

Typed envelopes, grant/role guards, stable error mapping, and idempotency-key
validation for mark-created/publish/archive/runtime-switch plus release
list/detail.  Publication stays hidden behind the grants; arbitrary status PUT
is not exposed.
"""
import os
from pathlib import Path
import subprocess
import sys
import uuid
from urllib.parse import quote

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker


BACKEND_DIR = Path(__file__).resolve().parents[2]
ROUTER = BACKEND_DIR / "app" / "routers" / "ontology_lifecycle.py"
SCHEMAS = BACKEND_DIR / "app" / "schemas" / "ontology_lifecycle.py"
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
DEFAULT_DOMAIN = "00000000-0000-0000-0000-000000000001"


def test_p1c_api_red_contract():
    missing = [path for path in (ROUTER, SCHEMAS) if not path.exists()]
    if missing:
        pytest.fail(
            "RED_P1C_API: ontology lifecycle API foundation missing: "
            + ", ".join(str(path.relative_to(BACKEND_DIR)) for path in missing)
        )
    source = ROUTER.read_text()
    for marker in ('"/{ontology_id}/mark-created"', '"/{ontology_id}/publish"',
                   '"/{ontology_id}/releases"', "Idempotency-Key", "require_project_grant"):
        if marker not in source:
            pytest.fail(f"RED_P1C_API: lifecycle router missing {marker}")


def _scoped_url(schema):
    return f"{TEST_DATABASE_URL}?options={quote(f'-csearch_path={schema},public', safe='-=,')}"


def _alembic(schema, *args, check=True):
    return subprocess.run(
        [sys.executable, "scripts/run_migrations.py", *args],
        cwd=BACKEND_DIR,
        env=dict(os.environ, DATABASE_URL=_scoped_url(schema)),
        capture_output=True,
        text=True,
        check=check,
    )


@pytest.fixture(scope="module")
def lifecycle_api_db():
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL required")
    schema = "p1c_api_" + uuid.uuid4().hex
    engine = create_engine(TEST_DATABASE_URL)
    with engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    result = _alembic(schema, "upgrade", "0003_publication_governance")
    assert result.returncode == 0, result.stderr
    session_engine = create_engine(_scoped_url(schema))
    Session = sessionmaker(bind=session_engine)
    yield Session, session_engine
    session_engine.dispose()
    with engine.begin() as connection:
        connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
    engine.dispose()


def _seed(lifecycle_api_db):
    from app.services.auth_service import create_access_token, hash_password

    Session, _ = lifecycle_api_db
    username = "p1c-api-" + uuid.uuid4().hex[:8]
    with Session() as session:
        session.execute(text(
            "INSERT INTO users (id, username, email, password_hash, role, is_active, created_at, updated_at, security_domain_id) "
            "VALUES (:id, :username, :email, :password_hash, 'editor', true, now(), now(), :domain)"
        ), {"id": str(uuid.uuid4()), "username": username, "email": f"{username}@example.com",
            "password_hash": hash_password("pass123"), "domain": DEFAULT_DOMAIN})
        actor_id = session.execute(text("SELECT id FROM users WHERE username=:u"), {"u": username}).scalar_one()
        session.execute(text(
            "INSERT INTO ontology_projects (id, name, domain, version, status, created_by, created_at, updated_at, security_domain_id, working_revision) "
            "VALUES (:id, :name, 'test', 'v0.1', 'created', :creator, now(), now(), :domain, 1)"
        ), {"id": str(uuid.uuid4()), "name": "API " + uuid.uuid4().hex[:8], "creator": actor_id, "domain": DEFAULT_DOMAIN})
        ontology_id = session.execute(text("SELECT id FROM ontology_projects WHERE created_by=:c ORDER BY created_at DESC LIMIT 1"), {"c": actor_id}).scalar_one()
        session.execute(text(
            "INSERT INTO entities (id, ontology_id, name_cn, type, properties, confidence, version, created_at, updated_at) "
            "VALUES (:id, :o, '供应商', 'Supplier', '{}'::jsonb, 1.0, 'v0.1', now(), now())"
        ), {"id": str(uuid.uuid4()), "o": ontology_id})
        session.commit()
        return actor_id, ontology_id


def test_zz_lifecycle_api_publish_and_releases(lifecycle_api_db):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.deps import get_db
    from app.models.user import User
    from app.routers import ontology_lifecycle
    from app.services.auth_service import create_access_token
    from app.services.ontology_access import creator_grant

    Session, _ = lifecycle_api_db
    actor_id, ontology_id = _seed(lifecycle_api_db)
    with Session() as session:
        user = session.get(User, actor_id)
        creator_grant(session, ontology_id, user)
    token = create_access_token({"sub": actor_id, "role": "editor"})
    headers = {"Authorization": f"Bearer {token}", "Idempotency-Key": "k" + "1" * 20}
    no_key_headers = {"Authorization": f"Bearer {token}"}

    def override_get_db():
        session = Session()
        try:
            yield session
        finally:
            session.close()

    app = FastAPI()
    app.include_router(ontology_lifecycle.router, prefix="/api/v1/ontologies")
    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as client:
        # missing idempotency key on a mutation is rejected
        assert client.post(
            f"/api/v1/ontologies/{ontology_id}/mark-created", json={}, headers=no_key_headers,
        ).status_code == 400
        # mark-created on an already-created ontology fails closed
        conflict = client.post(
            f"/api/v1/ontologies/{ontology_id}/mark-created", json={}, headers=headers,
        )
        assert conflict.status_code == 409
        assert "INVALID_LIFECYCLE_TRANSITION" in conflict.text
        # publish succeeds and returns an immutable release
        published = client.post(
            f"/api/v1/ontologies/{ontology_id}/publish",
            json={"base_working_revision": 1, "changelog": "first"},
            headers=headers,
        )
        assert published.status_code == 201, published.text
        receipt = published.json()["data"]
        assert receipt["version_no"] == 1 and len(receipt["schema_hash"]) == 64
        # no-change re-publish is an idempotent 409 NO_SCHEMA_CHANGE
        again = client.post(
            f"/api/v1/ontologies/{ontology_id}/publish",
            json={"base_working_revision": 1},
            headers=headers,
        )
        assert again.status_code == 409
        assert "NO_SCHEMA_CHANGE" in again.text
        # release list/detail are readable
        listing = client.get(
            f"/api/v1/ontologies/{ontology_id}/releases",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert listing.status_code == 200
        assert listing.json()["data"]["items"][0]["version_no"] == 1
        detail = client.get(
            f"/api/v1/ontologies/{ontology_id}/releases/{receipt['release_id']}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert detail.status_code == 200
        assert detail.json()["data"]["manifest_projection"]["entities"][0]["name"] == "供应商"
    app.dependency_overrides.clear()


def test_zz_lifecycle_api_hides_existence_without_grant(lifecycle_api_db):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.deps import get_db
    from app.routers import ontology_lifecycle
    from app.services.auth_service import create_access_token, hash_password

    Session, _ = lifecycle_api_db
    _, ontology_id = _seed(lifecycle_api_db)
    with Session() as session:
        session.execute(text(
            "INSERT INTO users (id, username, email, password_hash, role, is_active, created_at, updated_at, security_domain_id) "
            "VALUES (:id, 'stranger', 'stranger@example.com', :hash, 'editor', true, now(), now(), :domain)"
        ), {"id": str(uuid.uuid4()), "hash": hash_password("pass123"), "domain": DEFAULT_DOMAIN})
        stranger_id = session.execute(text("SELECT id FROM users WHERE username='stranger'")).scalar_one()
        session.commit()
    token = create_access_token({"sub": stranger_id, "role": "editor"})
    headers = {"Authorization": f"Bearer {token}", "Idempotency-Key": "k" + "2" * 20}

    def override_get_db():
        session = Session()
        try:
            yield session
        finally:
            session.close()

    app = FastAPI()
    app.include_router(ontology_lifecycle.router, prefix="/api/v1/ontologies")
    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as client:
        # an editor without any grant gets an existence-hiding 404
        denied = client.post(
            f"/api/v1/ontologies/{ontology_id}/publish", json={}, headers=headers,
        )
        assert denied.status_code == 404
    app.dependency_overrides.clear()

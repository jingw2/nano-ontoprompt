"""P7C: signed skill admin API (service layer)."""
import os
import subprocess
import sys
import uuid
from pathlib import Path
from urllib.parse import quote

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.services.skills import manifest_canonical_hash

BACKEND_DIR = Path(__file__).resolve().parents[2]
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
DEFAULT_DOMAIN = "00000000-0000-0000-0000-000000000001"


def _scoped_url(schema: str) -> str:
    return f"{TEST_DATABASE_URL}?options={quote(f'-csearch_path={schema},public', safe='-=,')}"


def _alembic(schema: str, *args, check=True):
    return subprocess.run(
        [sys.executable, "scripts/run_migrations.py", *args],
        cwd=BACKEND_DIR, env=dict(os.environ, DATABASE_URL=_scoped_url(schema)),
        capture_output=True, text=True, check=check,
    )


@pytest.fixture
def session():
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL required")
    schema = "p7c_api_" + uuid.uuid4().hex
    engine = create_engine(TEST_DATABASE_URL)
    with engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    assert _alembic(schema, "upgrade", "0014_signed_skills").returncode == 0
    s = sessionmaker(bind=create_engine(_scoped_url(schema)))()
    s.execute(text(
        "INSERT INTO users (id,username,email,password_hash,role,is_active,security_domain_id,created_at,updated_at) "
        "VALUES ('u-1','a','a@t.com','h','admin',true,:d,now(),now())"
    ), {"d": DEFAULT_DOMAIN})
    s.commit()
    yield s
    s.close()
    with engine.begin() as connection:
        connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
    engine.dispose()


def _manifest():
    return {
        "name": "supply-chain-summarizer",
        "description": "Summarizes suppliers with governed reads.",
        "instructions": "Use read_suppliers first, then summarize safety lines.",
        "tools": [{
            "alias": "read_suppliers",
            "descriptor_id": "ontology.read_instances",
            "description": "Query supplier instances",
            "parameters": {"query": "供应商"},
        }],
    }


def _signed(manifest, key):
    signature = key.sign(bytes.fromhex(manifest_canonical_hash(manifest)))
    return {"public_key_hex": key.public_key().public_bytes_raw().hex(),
            "signature_hex": signature.hex(), "signer_identity": "org-a"}


def test_create_version_requires_valid_signature(session):
    from app.services.skills.admin import create_package, create_skill_version, SkillError
    package = create_package(session, actor_id="u-1", name="pkg")
    manifest = _manifest()
    with pytest.raises(SkillError):
        create_skill_version(session, actor_id="u-1", package_id=package["id"],
                             manifest=manifest, signatures=[])


def test_create_and_approve_round_trip(session):
    from app.services.skills.admin import approve_skill_version, create_package, create_skill_version
    key = Ed25519PrivateKey.generate()
    package = create_package(session, actor_id="u-1", name="pkg")
    manifest = _manifest()
    version = create_skill_version(session, actor_id="u-1", package_id=package["id"],
                                   manifest=manifest, signatures=[_signed(manifest, key)])
    assert version["approval_status"] == "pending"
    approved = approve_skill_version(session, actor_id="u-1", version_id=version["id"])
    assert approved["approval_status"] == "approved"


def test_approve_rejects_tampered_manifest(session):
    from app.services.skills.admin import approve_skill_version, create_package, create_skill_version, SkillError
    key = Ed25519PrivateKey.generate()
    package = create_package(session, actor_id="u-1", name="pkg")
    manifest = _manifest()
    version = create_skill_version(session, actor_id="u-1", package_id=package["id"],
                                   manifest=manifest, signatures=[_signed(manifest, key)])
    # tamper with the stored manifest directly (simulating a post-write row edit)
    tampered = _manifest()
    tampered["tools"][0]["parameters"]["query"] = "攻击载荷"
    import json
    session.execute(text(
        "UPDATE skill_versions SET manifest = CAST(:m AS json) WHERE id = :id"
    ), {"m": json.dumps(tampered, ensure_ascii=False), "id": version["id"]})
    session.commit()
    with pytest.raises(SkillError):
        approve_skill_version(session, actor_id="u-1", version_id=version["id"])


def test_create_rejects_unknown_descriptor(session):
    from app.services.skills.admin import create_package, create_skill_version, SkillError
    key = Ed25519PrivateKey.generate()
    package = create_package(session, actor_id="u-1", name="pkg")
    manifest = _manifest()
    manifest["tools"][0]["descriptor_id"] = "evil.exec"
    with pytest.raises(SkillError) as excinfo:
        create_skill_version(session, actor_id="u-1", package_id=package["id"],
                             manifest=manifest, signatures=[_signed(manifest, key)])
    assert "MANIFEST_INVALID" in str(excinfo.value)


def test_approve_rejects_double_approval(session):
    from app.services.skills.admin import approve_skill_version, create_package, create_skill_version, SkillError
    key = Ed25519PrivateKey.generate()
    package = create_package(session, actor_id="u-1", name="pkg")
    manifest = _manifest()
    version = create_skill_version(session, actor_id="u-1", package_id=package["id"],
                                   manifest=manifest, signatures=[_signed(manifest, key)])
    approve_skill_version(session, actor_id="u-1", version_id=version["id"])
    with pytest.raises(SkillError):
        approve_skill_version(session, actor_id="u-1", version_id=version["id"])

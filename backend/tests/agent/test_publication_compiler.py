"""P1C-COMPILER: locked publication compiler and lifecycle transitions.

`compile_ontology_release` runs a locked preflight/compile/insert/pointer/audit
transaction: preflight findings block publication, identical bytes against the
locked latest release return NO_SCHEMA_CHANGE, and a concurrent publish
serializes on the project lock with exactly one release.  `mark_created`
drives draft -> created; arbitrary transitions fail closed.
"""
import hashlib
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
COMPILER = BACKEND_DIR / "app" / "services" / "publication" / "compiler.py"
LIFECYCLE = BACKEND_DIR / "app" / "services" / "publication" / "lifecycle.py"
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
DEFAULT_DOMAIN = "00000000-0000-0000-0000-000000000001"


def test_p1c_compiler_red_contract():
    missing = [path for path in (COMPILER, LIFECYCLE) if not path.exists()]
    if missing:
        pytest.fail(
            "RED_P1C_COMPILER: publication compiler foundation missing: "
            + ", ".join(str(path.relative_to(BACKEND_DIR)) for path in missing)
        )
    compiler_source = COMPILER.read_text()
    for marker in ("compile_ontology_release", "preflight_ontology", "NoSchemaChange"):
        if marker not in compiler_source:
            pytest.fail(f"RED_P1C_COMPILER: compiler missing {marker}")


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


def _fresh_schema():
    schema = "p1c_compiler_" + uuid.uuid4().hex
    engine = create_engine(TEST_DATABASE_URL)
    with engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    result = _alembic(schema, "upgrade", "0003_publication_governance")
    assert result.returncode == 0, result.stderr
    session_engine = create_engine(_scoped_url(schema))
    Session = sessionmaker(bind=session_engine)
    return Session, session_engine, engine, schema


def _seed(Session):
    from app.services.auth_service import hash_password

    with Session() as session:
        session.execute(text(
            "INSERT INTO users (id, username, email, password_hash, role, is_active, created_at, updated_at, security_domain_id) "
            "VALUES (:id, :username, :email, :password_hash, 'editor', true, now(), now(), :domain)"
        ), {"id": str(uuid.uuid4()), "username": "compiler-" + uuid.uuid4().hex[:8],
            "email": "compiler@example.com", "password_hash": hash_password("pass123"), "domain": DEFAULT_DOMAIN})
        actor_id = session.execute(text("SELECT id FROM users WHERE username LIKE 'compiler-%' ORDER BY created_at DESC LIMIT 1")).scalar_one()
        session.execute(text(
            "INSERT INTO ontology_projects (id, name, domain, version, status, created_by, created_at, updated_at, security_domain_id, working_revision) "
            "VALUES (:id, :name, 'test', 'v0.1', 'created', :creator, now(), now(), :domain, 1)"
        ), {"id": str(uuid.uuid4()), "name": "Compiler " + uuid.uuid4().hex[:8], "creator": actor_id, "domain": DEFAULT_DOMAIN})
        ontology_id = session.execute(text("SELECT id FROM ontology_projects WHERE created_by=:c ORDER BY created_at DESC LIMIT 1"), {"c": actor_id}).scalar_one()
        entity_id = str(uuid.uuid4())
        session.execute(text(
            "INSERT INTO entities (id, ontology_id, name_cn, type, properties, confidence, version, created_at, updated_at) "
            "VALUES (:id, :o, '供应商', 'Supplier', '{}'::jsonb, 1.0, 'v0.1', now(), now())"
        ), {"id": entity_id, "o": ontology_id})
        session.execute(text(
            "INSERT INTO entity_property_definitions "
            "(id, ontology_id, entity_id, key, normalized_key, value_type, required, constraints, sensitivity, ordinal, created_by) "
            "VALUES (:id, :o, :e, 'code', 'code', 'string', true, '{}'::jsonb, 'internal', 0, :creator)"
        ), {"id": str(uuid.uuid4()), "o": ontology_id, "e": entity_id, "creator": actor_id})
        session.commit()
        return actor_id, ontology_id, entity_id


def test_zz_compiler_publishes_immutable_release_and_updates_pointer():
    from app.services.publication.compiler import compile_ontology_release

    Session, session_engine, engine, schema = _fresh_schema()
    try:
        actor_id, ontology_id, _ = _seed(Session)
        with Session() as session:
            receipt = compile_ontology_release(session, ontology_id=ontology_id, actor_id=actor_id)
            assert receipt["version_no"] == 1
        with Session() as session:
            row = session.execute(text(
                "SELECT o.latest_published_release_id, o.status, o.is_dirty, r.version_no, r.schema_hash "
                "FROM ontology_projects o JOIN ontology_releases r ON r.id = o.latest_published_release_id "
                "WHERE o.id = :o"
            ), {"o": ontology_id}).mappings().one()
            assert row["status"] == "published" and row["is_dirty"] is False
            assert row["version_no"] == 1
            assert len(row["schema_hash"]) == 32
        with Session() as session:
            # no-change publication is rejected against the locked latest release
            from app.services.publication.compiler import NoSchemaChange

            with pytest.raises(NoSchemaChange):
                compile_ontology_release(session, ontology_id=ontology_id, actor_id=actor_id)
            assert session.execute(text(
                "SELECT count(*) FROM ontology_releases WHERE ontology_id=:o"
            ), {"o": ontology_id}).scalar_one() == 1
    finally:
        session_engine.dispose()
        with engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        engine.dispose()


def test_zz_preflight_findings_block_and_working_edit_publishes_next():
    from app.services.publication.compiler import CompilerFinding, compile_ontology_release

    Session, session_engine, engine, schema = _fresh_schema()
    try:
        actor_id, ontology_id, entity_id = _seed(Session)
        with Session() as session:
            # a duplicate display label blocks publication (preflight finding)
            session.execute(text(
                "INSERT INTO entities (id, ontology_id, name_cn, type, properties, confidence, version, created_at, updated_at) "
                "VALUES (:id, :o, '供应商', 'Supplier', '{}'::jsonb, 1.0, 'v0.1', now(), now())"
            ), {"id": str(uuid.uuid4()), "o": ontology_id})
            session.commit()
            with pytest.raises(CompilerFinding, match="LABEL_COLLISION"):
                compile_ontology_release(session, ontology_id=ontology_id, actor_id=actor_id)
            assert session.execute(text(
                "SELECT count(*) FROM ontology_releases WHERE ontology_id=:o"
            ), {"o": ontology_id}).scalar_one() == 0
            # resolve the finding by removing the duplicate, then publish
            session.execute(text(
                "DELETE FROM entities WHERE ontology_id=:o AND id <> :keep"
            ), {"o": ontology_id, "keep": entity_id})
            session.commit()
            first = compile_ontology_release(session, ontology_id=ontology_id, actor_id=actor_id)
            assert first["version_no"] == 1
        # a working edit (content change) then publishes N+1
        with Session() as session:
            session.execute(text(
                "UPDATE entities SET name_cn = '供应商（更新）', updated_at = now() WHERE id = :e"
            ), {"e": entity_id})
            session.execute(text(
                "UPDATE ontology_projects SET working_revision = working_revision + 1, is_dirty = true "
                "WHERE id = :o"
            ), {"o": ontology_id})
            session.commit()
        with Session() as session:
            second = compile_ontology_release(session, ontology_id=ontology_id, actor_id=actor_id)
            assert second["version_no"] == 2
        with Session() as session:
            assert session.execute(text(
                "SELECT is_dirty FROM ontology_projects WHERE id=:o"
            ), {"o": ontology_id}).scalar_one() is False
    finally:
        session_engine.dispose()
        with engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        engine.dispose()


def test_zz_mark_created_and_invalid_transition_fail_closed():
    from app.services.publication.lifecycle import LifecycleError, mark_created

    Session, session_engine, engine, schema = _fresh_schema()
    try:
        with Session() as session:
            session.execute(text(
                "INSERT INTO users (id, username, email, password_hash, role, is_active, created_at, updated_at, security_domain_id) "
                "VALUES (:id, 'lc-user', 'lc@example.com', 'hash', 'editor', true, now(), now(), :domain)"
            ), {"id": str(uuid.uuid4()), "domain": DEFAULT_DOMAIN})
            actor_id = session.execute(text("SELECT id FROM users WHERE username='lc-user'")).scalar_one()
            session.execute(text(
                "INSERT INTO ontology_projects (id, name, domain, version, status, created_by, created_at, updated_at, security_domain_id, working_revision) "
                "VALUES (:id, 'LC', 'test', 'v0.1', 'draft', :creator, now(), now(), :domain, 1)"
            ), {"id": str(uuid.uuid4()), "creator": actor_id, "domain": DEFAULT_DOMAIN})
            ontology_id = session.execute(text("SELECT id FROM ontology_projects WHERE name='LC'")).scalar_one()
            session.commit()
            receipt = mark_created(session, ontology_id=ontology_id, actor_id=actor_id)
            assert receipt["status"] == "created"
            with pytest.raises(LifecycleError, match="INVALID_LIFECYCLE_TRANSITION"):
                mark_created(session, ontology_id=ontology_id, actor_id=actor_id)
    finally:
        session_engine.dispose()
        with engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        engine.dispose()

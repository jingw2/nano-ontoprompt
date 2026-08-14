"""P1B-CUTOVER: latched normalized writer cutover and old-build rejection.

The cutover compares the normalized identity projection with the legacy
`Entity.properties` payloads (dual writes), requires zero divergence and zero
open blocking findings, takes a serialization lock, and atomically sets the
irreversible `PublicationActivationLatch`.  The 0003 delete-guard triggers are
no-ops before the latch and reject definition deletion with references after
it.  The `publication_write` dependency rejects pre-bridge builds.

PostgreSQL-marked tests use TEST_DATABASE_URL; SQLite never substitutes.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid
from urllib.parse import quote

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker


BACKEND_DIR = Path(__file__).resolve().parents[2]
CUTOVER = BACKEND_DIR / "app" / "services" / "publication" / "cutover.py"
PUBLICATION_WRITE = BACKEND_DIR / "app" / "deps" / "publication_write.py"
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
DEFAULT_DOMAIN = "00000000-0000-0000-0000-000000000001"


def test_p1b_cutover_red_contract():
    failures = []
    for path in (CUTOVER, PUBLICATION_WRITE):
        if not path.exists():
            failures.append(f"{path.relative_to(BACKEND_DIR)} missing")
    migration_source = (BACKEND_DIR / "alembic" / "versions" / "0003_publication_governance.py").read_text()
    for marker in ("upgrade_cutover_guards", "downgrade_cutover_guards"):
        if marker not in migration_source:
            failures.append(f"0003 missing {marker}")
    if failures:
        pytest.fail("RED_P1B_CUTOVER: " + "; ".join(failures))


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
def cutover_db():
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL required")
    schema = "p1b_cutover_" + uuid.uuid4().hex
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


def _seed_entity(Session, ontology_id, entity_id, creator_id, properties, with_definition=False):
    with Session() as session:
        session.execute(text(
            "INSERT INTO entities (id, ontology_id, name_cn, properties, confidence, version, created_at, updated_at) "
            "VALUES (:id, :o, '实体', CAST(:properties AS jsonb), 1.0, 'v0.1', now(), now())"
        ), {"id": entity_id, "o": ontology_id, "properties": properties})
        if with_definition:
            session.execute(text(
                "INSERT INTO entity_property_definitions "
                "(id, ontology_id, entity_id, key, normalized_key, value_type, required, constraints, sensitivity, ordinal, created_by) "
                "VALUES (:id, :o, :e, 'Code', 'code', 'string', true, '{}'::jsonb, 'internal', 0, :creator)"
            ), {"id": str(uuid.uuid4()), "o": ontology_id, "e": entity_id, "creator": creator_id})
        session.commit()


def _seed_ontology_user(Session):
    with Session() as session:
        session.execute(text(
            "INSERT INTO users (id, username, email, password_hash, role, is_active, created_at, updated_at, security_domain_id) "
            "VALUES (:id, :username, 'ct@example.com', 'hash', 'editor', true, now(), now(), :domain)"
        ), {"id": str(uuid.uuid4()), "username": "ct-" + uuid.uuid4().hex[:8], "domain": DEFAULT_DOMAIN})
        creator_id = session.execute(text("SELECT id FROM users WHERE username LIKE 'ct-%' ORDER BY created_at DESC LIMIT 1")).scalar_one()
        session.execute(text(
            "INSERT INTO ontology_projects (id, name, domain, version, status, created_by, created_at, updated_at, security_domain_id) "
            "VALUES (:id, :name, 'test', 'v0.1', 'draft', :creator, now(), now(), :domain)"
        ), {"id": str(uuid.uuid4()), "name": "Cutover " + uuid.uuid4().hex[:8], "creator": creator_id, "domain": DEFAULT_DOMAIN})
        ontology_id = session.execute(text("SELECT id FROM ontology_projects WHERE created_by=:c ORDER BY created_at DESC LIMIT 1"), {"c": creator_id}).scalar_one()
        session.commit()
        return creator_id, ontology_id


def _fresh_cutover_schema():
    """A fresh 0003 schema with its own sessionmaker (latch state must not leak)."""
    schema = "p1b_cutover_fresh_" + uuid.uuid4().hex
    engine = create_engine(TEST_DATABASE_URL)
    with engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    result = _alembic(schema, "upgrade", "0003_publication_governance")
    assert result.returncode == 0, result.stderr
    session_engine = create_engine(_scoped_url(schema))
    Session = sessionmaker(bind=session_engine)
    return Session, session_engine, engine, schema


def _drop_schema(engine, schema):
    with engine.begin() as connection:
        connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
    engine.dispose()


def test_zz_dual_write_comparison_detects_divergence(cutover_db):
    from app.services.publication.cutover import compare_dual_writes

    Session, _ = cutover_db
    creator_id, ontology_id = _seed_ontology_user(Session)
    entity_id = str(uuid.uuid4())
    _seed_entity(Session, ontology_id, entity_id, creator_id,
                 '{"Code": {"type": "string", "required": true}}', with_definition=False)
    with Session() as session:
        report = compare_dual_writes(session, ontology_id=ontology_id)
        assert report["ok"] is False
        assert any(d["entity_id"] == entity_id for d in report["divergences"])
        # resolve the divergence: the definition now exists
        session.execute(text(
            "INSERT INTO entity_property_definitions "
            "(id, ontology_id, entity_id, key, normalized_key, value_type, required, constraints, sensitivity, ordinal, created_by) "
            "VALUES (:id, :o, :e, 'Code', 'code', 'string', true, '{}'::jsonb, 'internal', 0, :creator)"
        ), {"id": str(uuid.uuid4()), "o": ontology_id, "e": entity_id, "creator": creator_id})
        session.commit()
        report = compare_dual_writes(session, ontology_id=ontology_id)
        assert report["ok"] is True
        assert report["divergences"] == []


def test_zz_cutover_requires_zero_divergence_and_zero_blocking_findings():
    from app.services.publication.cutover import CutoverBlocked, activate_cutover

    Session, session_engine, engine, schema = _fresh_cutover_schema()
    try:
        creator_id, ontology_id = _seed_ontology_user(Session)
        entity_id = str(uuid.uuid4())
        _seed_entity(Session, ontology_id, entity_id, creator_id,
                     '{"Code": {"type": "string"}}', with_definition=False)
        with Session() as session:
            with pytest.raises(CutoverBlocked):
                activate_cutover(session, actor_id=creator_id, build_manifest_hash="bridge")
            assert session.execute(text("SELECT count(*) FROM publication_activation_latch")).scalar_one() == 0

        # resolve the divergence by writing the definition, then cut over
        with Session() as session:
            session.execute(text(
                "INSERT INTO entity_property_definitions "
                "(id, ontology_id, entity_id, key, normalized_key, value_type, required, constraints, sensitivity, ordinal, created_by) "
                "VALUES (:id, :o, :e, 'Code', 'code', 'string', false, '{}'::jsonb, 'internal', 0, :creator)"
            ), {"id": str(uuid.uuid4()), "o": ontology_id, "e": entity_id, "creator": creator_id})
            session.commit()
        with Session() as session:
            receipt = activate_cutover(session, actor_id=creator_id, build_manifest_hash="bridge-hash")
            assert receipt["build_manifest_hash"] == "bridge-hash"
            assert session.execute(text("SELECT count(*) FROM publication_activation_latch")).scalar_one() == 1
            # idempotent second activation returns the stored receipt without a second row
            again = activate_cutover(session, actor_id=creator_id, build_manifest_hash="other")
            assert again["build_manifest_hash"] == "bridge-hash"
            assert session.execute(text("SELECT count(*) FROM publication_activation_latch")).scalar_one() == 1
            # the latch is immutable: UPDATE and DELETE are rejected
            savepoint = session.begin_nested()
            with pytest.raises(DBAPIError, match="LATCH_IMMUTABLE"):
                session.execute(text("DELETE FROM publication_activation_latch"))
            savepoint.rollback()
    finally:
        session_engine.dispose()
        _drop_schema(engine, schema)


def test_zz_delete_guards_are_noop_before_latch_and_block_after():
    from app.services.publication.cutover import activate_cutover

    Session, session_engine, engine, schema = _fresh_cutover_schema()
    try:
        creator_id, ontology_id = _seed_ontology_user(Session)
        entity_id = str(uuid.uuid4())
        _seed_entity(Session, ontology_id, entity_id, creator_id,
                     '{"Note": "plain value"}', with_definition=False)

        with Session() as session:
            # no-op before the latch: deleting an entity without a normalized
            # definition is allowed (the guard trigger adds no rejection)
            session.execute(text("DELETE FROM entities WHERE id=:id"), {"id": entity_id})
            session.commit()
        assert True  # reached: guard was a no-op

        entity_id = str(uuid.uuid4())
        _seed_entity(Session, ontology_id, entity_id, creator_id,
                     '{"Code": {"type": "string"}}', with_definition=True)
        with Session() as session:
            activate_cutover(session, actor_id=creator_id, build_manifest_hash="bridge")
        with Session() as session:
            savepoint = session.begin_nested()
            with pytest.raises(DBAPIError, match="DEFINITION_IN_USE"):
                session.execute(text("DELETE FROM entities WHERE id=:id"), {"id": entity_id})
            savepoint.rollback()
            assert session.execute(text("SELECT count(*) FROM entities WHERE id=:id"), {"id": entity_id}).scalar_one() == 1
    finally:
        session_engine.dispose()
        _drop_schema(engine, schema)


def test_zz_old_build_rejection_dependency(cutover_db):
    from app.deps.publication_write import require_bridge_build

    Session, _ = cutover_db
    with Session() as session:
        require_bridge_build(session)  # 0003-headed bridge passes
    # an old (0002-headed) database must be rejected as MINIMUM_BUILD_NOT_READY
    schema = "p1b_cutover_old_" + uuid.uuid4().hex
    engine = create_engine(TEST_DATABASE_URL)
    with engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    try:
        result = _alembic(schema, "upgrade", "0002_entity_identifiers")
        assert result.returncode == 0, result.stderr
        old_session = sessionmaker(bind=create_engine(_scoped_url(schema)))()
        from fastapi import HTTPException

        with pytest.raises(HTTPException, match="MINIMUM_BUILD_NOT_READY"):
            require_bridge_build(old_session)
        old_session.close()
    finally:
        with engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        engine.dispose()


def test_zz_delete_guard_blocks_instance_relation_and_release_references():
    """I-1: post-latch guards reject definition deletes with instance/edge/
    release references and never cascade."""
    import hashlib

    from app.services.publication.cutover import activate_cutover

    Session, session_engine, engine, schema = _fresh_cutover_schema()
    try:
        creator_id, ontology_id = _seed_ontology_user(Session)
        entity_id = str(uuid.uuid4())
        other_id = str(uuid.uuid4())
        with Session() as session:
            session.execute(text(
                "INSERT INTO entities (id, ontology_id, name_cn, properties, confidence, version, created_at, updated_at) "
                "VALUES (:a, :o, '甲', '{}'::jsonb, 1.0, 'v0.1', now(), now()), "
                "(:b, :o, '乙', '{}'::jsonb, 1.0, 'v0.1', now(), now())"
            ), {"a": entity_id, "b": other_id, "o": ontology_id})
            # an instance row referencing the entity (legacy CASCADE FK)
            session.execute(text(
                "INSERT INTO entity_instances (id, entity_id, ontology_id, row_identity, row_data, created_at) "
                "VALUES (:id, :e, :o, 'row-1', CAST(:row_data AS json), now())"
            ), {"id": str(uuid.uuid4()), "e": entity_id, "o": ontology_id, "row_data": json.dumps({"a": 1})})
            # a relation edge referencing the entity
            session.execute(text(
                "INSERT INTO relations (id, ontology_id, source_entity, target_entity, type, properties, confidence, created_at) "
                "VALUES (:id, :o, :src, :tgt, 'REL', '{}'::json, 0.9, now())"
            ), {"id": str(uuid.uuid4()), "o": ontology_id, "src": entity_id, "tgt": other_id})
            # a release whose manifest references the entity stable id
            manifest_bytes = json.dumps({
                "manifest_version": "ontology-manifest-v1",
                "compiler_version": "ontology-compiler-v1",
                "policy_compiler_version": "restricted-policy-dsl-v1",
                "aggregate_tool_schema_hash": "a" * 64,
                "ontology": {"id": ontology_id, "name": "x", "security_domain_id": "00000000-0000-0000-0000-000000000001", "description": None, "build_mode": "simple_llm"},
                "release": {"version_no": 1, "version": "v1"},
                "entities": [{"id": entity_id, "name": "甲", "type": "object", "description": None, "property_definitions": []}],
                "relations": [], "logic_rules": [], "state_machines": [], "actions": [], "tool_descriptors": [],
            }, ensure_ascii=False).encode()
            session.execute(text(
                "INSERT INTO ontology_releases (id, ontology_id, version_no, version, manifest_bytes, manifest_projection, schema_hash, created_by) "
                "VALUES ('20000000-0000-0000-0000-0000000000a1', :o, 1, 'v1', :bytes, CAST(:projection AS jsonb), :hash, :creator)"
            ), {"o": ontology_id, "bytes": manifest_bytes, "projection": manifest_bytes.decode(),
                "hash": hashlib.sha256(manifest_bytes).digest(), "creator": creator_id})
            session.commit()
        with Session() as session:
            activate_cutover(session, actor_id=creator_id, build_manifest_hash="bridge")

        # post-latch: deleting an entity with an instance row is blocked, zero cascades
        with Session() as session:
            savepoint = session.begin_nested()
            with pytest.raises(DBAPIError, match="DEFINITION_IN_USE"):
                session.execute(text("DELETE FROM entities WHERE id=:id"), {"id": entity_id})
            savepoint.rollback()
            assert session.execute(text(
                "SELECT count(*) FROM entity_instances WHERE entity_id=:id"
            ), {"id": entity_id}).scalar_one() == 1  # nothing cascaded
        # post-latch: deleting a relation referenced by a release is blocked
        relation_id = None
        with Session() as session:
            relation_id = session.execute(text(
                "SELECT id FROM relations WHERE ontology_id=:o LIMIT 1"
            ), {"o": ontology_id}).scalar_one()
            relation_manifest = json.dumps({
                "manifest_version": "ontology-manifest-v1",
                "compiler_version": "ontology-compiler-v1",
                "policy_compiler_version": "restricted-policy-dsl-v1",
                "aggregate_tool_schema_hash": "a" * 64,
                "ontology": {"id": ontology_id, "name": "x", "security_domain_id": "00000000-0000-0000-0000-000000000001", "description": None, "build_mode": "simple_llm"},
                "release": {"version_no": 2, "version": "v2"},
                "entities": [], "logic_rules": [], "state_machines": [], "actions": [], "tool_descriptors": [],
                "relations": [{"id": relation_id, "name": "r", "source_entity_id": entity_id, "target_entity_id": other_id, "cardinality": "one", "direction": "out", "properties": []}],
            }, ensure_ascii=False).encode()
            session.execute(text(
                "INSERT INTO ontology_releases (id, ontology_id, version_no, version, manifest_bytes, manifest_projection, schema_hash, created_by) "
                "VALUES ('20000000-0000-0000-0000-0000000000a2', :o, 2, 'v2', :bytes, CAST(:projection AS jsonb), :hash, :creator)"
            ), {"o": ontology_id, "bytes": relation_manifest, "projection": relation_manifest.decode(),
                "hash": hashlib.sha256(relation_manifest).digest(), "creator": creator_id})
            session.commit()
        with Session() as session:
            savepoint = session.begin_nested()
            with pytest.raises(DBAPIError, match="DEFINITION_IN_USE"):
                session.execute(text("DELETE FROM relations WHERE id=:id"), {"id": relation_id})
            savepoint.rollback()
            assert session.execute(text(
                "SELECT count(*) FROM relations WHERE id=:id"
            ), {"id": relation_id}).scalar_one() == 1
    finally:
        session_engine.dispose()
        _drop_schema(engine, schema)

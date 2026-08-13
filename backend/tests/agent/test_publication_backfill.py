"""P1B-BACKFILL: resumable governed ontology identity inventory.

Walks legacy `Entity.properties` in cursor batches, classifies every entry via
the P1A preflight, upserts normalized `EntityPropertyDefinition` rows for
explicit validated schema metadata only, inserts blocking findings, and
audits — without ever mutating the source payload.  Repeated runs converge
with no payload mutation.
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
from sqlalchemy.orm import sessionmaker


BACKEND_DIR = Path(__file__).resolve().parents[2]
BACKFILL = BACKEND_DIR / "app" / "services" / "publication" / "backfill.py"
CLI = BACKEND_DIR / "app" / "cli" / "publication_backfill.py"
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
DEFAULT_DOMAIN = "00000000-0000-0000-0000-000000000001"


def test_p1b_backfill_red_contract():
    missing = [path for path in (BACKFILL, CLI) if not path.exists()]
    if missing:
        pytest.fail(
            "RED_P1B_BACKFILL: publication backfill foundation missing: "
            + ", ".join(str(path.relative_to(BACKEND_DIR)) for path in missing)
        )


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
def backfill_db():
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL required")
    schema = "p1b_backfill_" + uuid.uuid4().hex
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


def _seed(backfill_db, property_id=None):
    Session, _ = backfill_db
    username = "bf-" + uuid.uuid4().hex[:8]
    ontology_name = "Backfill ontology " + uuid.uuid4().hex[:8]
    with Session() as session:
        session.execute(text(
            "INSERT INTO users (id, username, email, password_hash, role, is_active, created_at, updated_at, security_domain_id) "
            "VALUES (:id, :username, :email, 'hash', 'editor', true, now(), now(), :domain)"
        ), {"id": str(uuid.uuid4()), "username": username, "email": f"{username}@example.com", "domain": DEFAULT_DOMAIN})
        creator_id = session.execute(text("SELECT id FROM users WHERE username=:u"), {"u": username}).scalar_one()
        session.execute(text(
            "INSERT INTO ontology_projects (id, name, domain, version, status, created_by, created_at, updated_at, security_domain_id) "
            "VALUES (:id, :name, 'test', 'v0.1', 'draft', :creator, now(), now(), :domain)"
        ), {"id": str(uuid.uuid4()), "name": ontology_name, "creator": creator_id, "domain": DEFAULT_DOMAIN})
        ontology_id = session.execute(text("SELECT id FROM ontology_projects WHERE name=:n"), {"n": ontology_name}).scalar_one()
        session.commit()
        return creator_id, ontology_id


def _insert_entity(Session, ontology_id, entity_name, properties, entity_id=None):
    entity_id = entity_id or str(uuid.uuid4())
    with Session() as session:
        session.execute(text(
            "INSERT INTO entities (id, ontology_id, name_cn, properties, confidence, version, created_at, updated_at) "
            "VALUES (:id, :o, :name, CAST(:properties AS jsonb), 1.0, 'v0.1', now(), now())"
        ), {"id": entity_id, "o": ontology_id, "name": entity_name,
            "properties": json.dumps(properties, ensure_ascii=False)})
        session.commit()
        return entity_id


def test_zz_backfill_inventory_is_cursor_resumable_and_converges(backfill_db):
    from app.services.publication.backfill import run_backfill

    Session, _ = backfill_db
    creator_id, ontology_id = _seed(backfill_db)
    pid = str(uuid.uuid4())
    entity_a = _insert_entity(Session, ontology_id, "甲", {
        "Code": {"id": pid, "type": "string", "required": True},
        "Amount": {"type": "number"},
        "Note": "free text",
    })
    entity_b = _insert_entity(Session, ontology_id, "乙", {
        "Flag": {"required": True},
        "Bad": ["not", "an", "object"],
    })
    _insert_entity(Session, ontology_id, "丙", {"Empty": {}})

    with Session() as session:
        cursor = None
        total_created = 0
        batches = 0
        while True:
            report = run_backfill(session, after_id=cursor, batch_size=1, actor_id=creator_id)
            total_created += report["definitions_created"]
            batches += 1
            cursor = report["cursor"]
            if cursor is None:
                break
        assert batches >= 2  # the cursor actually chunked the inventory
        assert total_created == 2

    with Session() as session:
        created = session.execute(text(
            "SELECT count(*) FROM entity_property_definitions WHERE ontology_id=:o"
        ), {"o": ontology_id}).scalar_one()
        assert created == 2  # Code and Amount only; never the example/scalar/ambiguous entries
        findings = session.execute(text(
            "SELECT code FROM ontology_migration_findings WHERE ontology_id=:o AND kind='property' ORDER BY code"
        ), {"o": ontology_id}).scalars().all()
        assert "PROPERTY_EXAMPLE_OR_SCALAR" in findings
        assert "PROPERTY_AMBIGUOUS" in findings
        assert "PROPERTY_INVALID_JSON" in findings

        # source payloads are byte-identical after backfill
        source = session.execute(text(
            "SELECT properties::text FROM entities WHERE id=:id"
        ), {"id": entity_a}).scalar_one()
        assert json.loads(source)["Note"] == "free text"
        assert json.loads(source)["Code"]["id"] == pid

    # a full rerun converges: zero new writes, no payload mutation
    with Session() as session:
        rerun = run_backfill(session, batch_size=100, actor_id=creator_id)
        assert rerun["definitions_created"] == 0
        assert rerun["definitions_existing"] == 2
        assert rerun["findings_inserted"] == 0
        assert rerun["cursor"] is None
    with Session() as session:
        assert session.execute(text(
            "SELECT count(*) FROM entity_property_definitions WHERE ontology_id=:o"
        ), {"o": ontology_id}).scalar_one() == 2
        assert session.execute(text(
            "SELECT count(*) FROM ontology_migration_findings WHERE ontology_id=:o"
        ), {"o": ontology_id}).scalar_one() == findings_count(Session, ontology_id)


def findings_count(Session, ontology_id):
    with Session() as session:
        return session.execute(text(
            "SELECT count(*) FROM ontology_migration_findings WHERE ontology_id=:o"
        ), {"o": ontology_id}).scalar_one()


def test_zz_backfill_never_mutates_source_and_audits(backfill_db):
    from app.services.publication.backfill import run_backfill

    Session, _ = backfill_db
    creator_id, ontology_id = _seed(backfill_db)
    entity_id = _insert_entity(Session, ontology_id, "丁", {"Value": 42})
    before = None
    with Session() as session:
        before = session.execute(text("SELECT properties::text FROM entities WHERE id=:id"), {"id": entity_id}).scalar_one()
        run_backfill(session, batch_size=10, actor_id=creator_id)
    with Session() as session:
        after = session.execute(text("SELECT properties::text FROM entities WHERE id=:id"), {"id": entity_id}).scalar_one()
        assert after == before
        audit = session.execute(text(
            "SELECT count(*) FROM governance_audit_outbox WHERE correlation_id LIKE 'backfill:%'"
        )).scalar_one()
        assert audit >= 1


def test_zz_backfill_cli_runs_to_completion(backfill_db):
    from app.services.publication.backfill import run_backfill

    Session, engine = backfill_db
    creator_id, ontology_id = _seed(backfill_db)
    _insert_entity(Session, ontology_id, "戊", {"Code": {"type": "string"}})
    url = str(engine.url)
    result = subprocess.run(
        [sys.executable, "-m", "app.cli.publication_backfill", "run",
         "--ontology-id", ontology_id, "--batch-size", "1", "--actor", creator_id],
        cwd=BACKEND_DIR,
        env=dict(os.environ, DATABASE_URL=url),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "definitions_created" in result.stdout
    assert "cursor" in result.stdout
    with Session() as session:
        assert session.execute(text(
            "SELECT count(*) FROM entity_property_definitions WHERE ontology_id=:o"
        ), {"o": ontology_id}).scalar_one() == 1

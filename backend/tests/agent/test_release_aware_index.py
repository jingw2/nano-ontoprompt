"""P4A-INDEX: release-aware Agent candidate index.

Consumes the 0006 derived-index outbox (trigger-emitted upsert/delete events
from authoritative instance/relation rows), SQL backfill of candidates per
release, candidate/hash reconciliation against the applied outbox ledger,
legacy-only dual-read with per-ontology cutover, and SQL-refetched/authorized
hits.  No authoritative Neo4j/Chroma result or per-release collection exists.
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


BACKEND_DIR = Path(__file__).resolve().parents[2]
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
DEFAULT_DOMAIN = "00000000-0000-0000-0000-000000000001"


def test_p4a_index_red_contract():
    failures = []
    for path in ("app/services/indexes/release_aware.py", "app/tasks/agent_index.py",
                 "app/models/agent_index_outbox.py"):
        p = BACKEND_DIR / path
        if not p.exists():
            failures.append(f"missing {path}")
    svc = BACKEND_DIR / "app" / "services" / "indexes" / "release_aware.py"
    if svc.exists():
        for symbol in ("consume_outbox", "backfill_candidates", "reconcile_candidates",
                       "search_candidates", "dual_read", "is_cutover_active"):
            if symbol not in svc.read_text():
                failures.append(f"release_aware.py missing {symbol}")
    if failures:
        pytest.fail("RED_P4A_INDEX: " + "; ".join(failures))


def _scoped_url(schema: str) -> str:
    return f"{TEST_DATABASE_URL}?options={quote(f'-csearch_path={schema},public', safe='-=,')}"


def _alembic(schema: str, *args, check=True):
    return subprocess.run(
        [sys.executable, "scripts/run_migrations.py", *args],
        cwd=BACKEND_DIR,
        env=dict(os.environ, DATABASE_URL=_scoped_url(schema)),
        capture_output=True,
        text=True,
        check=check,
    )


@pytest.fixture
def schema():
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL required")
    schema = "p4a_index_" + uuid.uuid4().hex
    engine = create_engine(TEST_DATABASE_URL)
    with engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    assert _alembic(schema, "upgrade", "0006_agent_runtime").returncode == 0
    yield schema
    with engine.begin() as connection:
        connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
    engine.dispose()


def _session(schema):
    return sessionmaker(bind=create_engine(_scoped_url(schema)))()


def _seed(connection, *, user_id="u-1", ontology_id="o-1"):
    connection.execute(text(
        "INSERT INTO users (id,username,email,password_hash,role,is_active,security_domain_id,created_at,updated_at) "
        "VALUES (:u,'s','s@t.com','h','admin',true,:d,now(),now())"
    ), {"u": user_id, "d": DEFAULT_DOMAIN})
    connection.execute(text(
        "INSERT INTO ontology_projects (id,name,domain,version,status,created_by,created_at,updated_at,security_domain_id,working_revision) "
        "VALUES (:o,'O','test','v1','published',:u,now(),now(),:d,1)"
    ), {"o": ontology_id, "u": user_id, "d": DEFAULT_DOMAIN})
    connection.execute(text(
        "INSERT INTO entities (id,ontology_id,name_cn,name_en,properties,confidence,version,created_at,updated_at) "
        "VALUES ('e-1',:o,'实体','E','{}'::json,0.9,'v1',now(),now()),"
        "('e-2',:o,'实体2','E2','{}'::json,0.9,'v1',now(),now())"
    ), {"o": ontology_id})
    connection.execute(text(
        "INSERT INTO entity_instances (id,entity_id,ontology_id,row_identity,row_data,created_at) "
        "VALUES ('i-1','e-1',:o,'a','{\"name\":\"Alpha Corp\"}'::json,now()),"
        "('i-2','e-2',:o,'b','{\"name\":\"Beta Inc\"}'::json,now())"
    ), {"o": ontology_id})


def _release(connection, *, ontology_id="o-1", actor="u-1"):
    """Compile a real immutable release from the seeded entities (the same
    publication path the release-aware index reads)."""
    from app.services.publication.compiler import compile_ontology_release
    result = compile_ontology_release(connection, ontology_id=ontology_id, actor_id=actor)
    return result["release_id"]


def test_consume_outbox_marks_applied(schema):
    session = _session(schema)
    _seed(session)
    session.commit()
    from app.services.indexes.release_aware import consume_outbox
    # the trigger already emitted upsert_instance events for i-1/i-2
    consumed = consume_outbox(session)
    assert len(consumed) >= 2
    remaining = session.execute(text(
        "SELECT count(*) FROM agent_index_outbox WHERE state = 'pending'"
    )).scalar_one()
    assert remaining == 0
    session.close()


def test_backfill_candidates_release_aware(schema):
    session = _session(schema)
    _seed(session)
    release_id = _release(session)
    session.commit()
    from app.services.indexes.release_aware import backfill_candidates
    all_candidates = backfill_candidates(session, ontology_id="o-1")
    assert {c["instance_id"] for c in all_candidates} == {"i-1", "i-2"}
    release_candidates = backfill_candidates(session, ontology_id="o-1", release_id=release_id)
    assert {c["instance_id"] for c in release_candidates} == {"i-1", "i-2"}
    assert all(c["release_id"] == release_id for c in release_candidates)
    assert all(len(c["hash"]) == 64 for c in release_candidates)
    session.close()


def test_reconcile_candidates_reports_divergence(schema):
    session = _session(schema)
    _seed(session)
    release_id = _release(session)
    session.commit()
    from app.services.indexes.release_aware import consume_outbox, reconcile_candidates
    consume_outbox(session)
    report = reconcile_candidates(session, ontology_id="o-1", release_id=release_id)
    assert report["candidates"] == 2
    assert report["missing"] == []  # every authoritative candidate has an applied upsert
    assert report["stale"] == []
    # delete one instance through the authoritative table: the trigger emits a
    # delete_instance event.  Before it is consumed the applied ledger still
    # holds the old upsert, so reconciliation flags the candidate as stale;
    # once consumed the ledger matches the SQL backfill again.
    session.execute(text("UPDATE entity_instances SET deleted_at = now(), updated_at = now() WHERE id = 'i-1'"))
    session.commit()
    report2 = reconcile_candidates(session, ontology_id="o-1", release_id=release_id)
    assert "i-1" in report2["stale"]
    assert report2["candidates"] == 1
    consume_outbox(session)
    report3 = reconcile_candidates(session, ontology_id="o-1", release_id=release_id)
    assert report3["missing"] == []
    assert report3["stale"] == []
    session.close()


def test_search_candidates_refetch_authorize_and_release_filter(schema):
    session = _session(schema)
    _seed(session)
    release_id = _release(session)
    # a third entity + instance created AFTER the release is not in its manifest
    session.execute(text(
        "INSERT INTO entities (id,ontology_id,name_cn,name_en,properties,confidence,version,created_at,updated_at) "
        "VALUES ('e-3','o-1','实体3','E3','{}'::json,0.9,'v1',now(),now())"
    ))
    session.execute(text(
        "INSERT INTO entity_instances (id,entity_id,ontology_id,row_identity,row_data,created_at) "
        "VALUES ('i-3','e-3','o-1','c','{\"name\":\"Gamma Ltd\"}'::json,now())"
    ))
    session.commit()
    from app.services.indexes.release_aware import search_candidates
    # without a grant -> empty (authorization)
    hits = search_candidates(session, ontology_id="o-1", release_id=release_id, query="Alpha", user_id="u-1")
    assert hits == []
    session.execute(text(
        "INSERT INTO ontology_data_grants (id, ontology_id, user_id, capabilities, status, revision, created_by) "
        "VALUES (:id, 'o-1', 'u-1', CAST(:caps AS json), 'active', 1, 'u-1')"
    ), {"id": str(uuid.uuid4()), "caps": '["read_instances", "discover"]'})
    session.commit()
    hits = search_candidates(session, ontology_id="o-1", release_id=release_id, query="Alpha", user_id="u-1")
    assert len(hits) == 1
    assert hits[0]["instance_id"] == "i-1"
    assert hits[0]["release_id"] == release_id
    # release filter excludes the post-release entity (not in the pinned manifest)
    hits_gamma = search_candidates(session, ontology_id="o-1", release_id=release_id, query="Gamma", user_id="u-1")
    assert hits_gamma == []
    # without a pinned release the post-release instance is visible
    hits_gamma_unpinned = search_candidates(session, ontology_id="o-1", release_id=None, query="Gamma", user_id="u-1")
    assert any(h["instance_id"] == "i-3" for h in hits_gamma_unpinned)
    session.close()


def test_search_discards_stale_revision_on_refetch(schema):
    session = _session(schema)
    _seed(session)
    release_id = _release(session)
    session.commit()
    from app.services.indexes.release_aware import search_candidates
    session.execute(text(
        "INSERT INTO ontology_data_grants (id, ontology_id, user_id, capabilities, status, revision, created_by) "
        "VALUES (:id, 'o-1', 'u-1', CAST(:caps AS json), 'active', 1, 'u-1')"
    ), {"id": str(uuid.uuid4()), "caps": '["read_instances"]'})
    session.commit()
    # bump revision after the candidate snapshot would have been taken
    session.execute(text("UPDATE entity_instances SET revision = 2, updated_at = now() WHERE id = 'i-1'"))
    session.commit()
    hits = search_candidates(session, ontology_id="o-1", release_id=release_id, query="Alpha", user_id="u-1")
    # the refetch matches the current revision, so the hit is returned fresh
    assert len(hits) == 1
    assert hits[0]["instance_revision"] == 2
    session.close()


def test_dual_read_and_cutover(schema):
    session = _session(schema)
    _seed(session)
    release_id = _release(session)
    session.commit()
    from app.services.indexes.release_aware import dual_read, set_cutover, is_cutover_active
    assert is_cutover_active(session, ontology_id="o-1") is False
    legacy = dual_read(session, ontology_id="o-1", release_id=release_id, query="Alpha", user_id="u-1")
    assert legacy == [{"legacy": True}]
    set_cutover(session, ontology_id="o-1", active=True)
    assert is_cutover_active(session, ontology_id="o-1") is True
    session.execute(text(
        "INSERT INTO ontology_data_grants (id, ontology_id, user_id, capabilities, status, revision, created_by) "
        "VALUES (:id, 'o-1', 'u-1', CAST(:caps AS json), 'active', 1, 'u-1')"
    ), {"id": str(uuid.uuid4()), "caps": '["read_instances"]'})
    session.commit()
    release_aware = dual_read(session, ontology_id="o-1", release_id=release_id, query="Alpha", user_id="u-1")
    assert release_aware != [{"legacy": True}]
    assert any(h["instance_id"] == "i-1" for h in release_aware)
    session.close()

"""P6B-2b: long-term memory recall path (Chroma vector-outbox consumer +
hybrid semantic/lexical recall). Spec: docs/superpowers/plans/2026-08-09-
agent-ontology-implementation.md, Section 11, recall paragraphs. Builds on
P6B-2a's already-merged write path (agent_memories, agent_memory_vector_outbox,
etc.) — this file's session fixture mirrors test_agent_memory_long_term.py's
exact pattern and baseline seed data."""
from __future__ import annotations

import json
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
HEAD = "0020_agent_memory_recall_index"


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
    schema = "p6b2b_" + uuid.uuid4().hex
    engine = create_engine(TEST_DATABASE_URL)
    with engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    assert _alembic(schema, "upgrade", HEAD).returncode == 0
    s = sessionmaker(bind=create_engine(_scoped_url(schema)))()
    s.execute(text(
        "INSERT INTO users (id,username,email,password_hash,role,is_active,security_domain_id,"
        "created_at,updated_at) VALUES ('u-1','a','a@t.com','h','admin',true,:d,now(),now())"
    ), {"d": DEFAULT_DOMAIN})
    s.execute(text(
        "INSERT INTO model_configs (id,name,config_type,api_base,api_key_encrypted,provider,"
        "models,options,created_by,created_at,updated_at) "
        "VALUES ('mc-1','m','llm',NULL,'','openai','[]'::json,'{}'::json,'u-1',now(),now())"
    ))
    s.execute(text(
        "INSERT INTO model_config_versions (id, model_config_id, version_no, provider, options, "
        "behavior_hash, model_contract, created_at) "
        "VALUES ('mcv-1', 'mc-1', 1, 'openai', '{}'::json, :hash, "
        "'[{\"provider_model_revision\": \"test-model\"}]'::json, now())"
    ), {"hash": "0" * 64})
    s.execute(text("UPDATE model_configs SET active_version_id = 'mcv-1' WHERE id = 'mc-1'"))
    app_schema_version_id = s.execute(text(
        "SELECT active_version_id FROM application_state_schema_registries "
        "WHERE application_key = 'chat-v1'"
    )).scalar_one()
    s.execute(text(
        "INSERT INTO agents (id,visibility,status,owner_id,created_at,updated_at) "
        "VALUES ('ag-1','private','active','u-1',now(),now())"
    ))
    s.execute(text(
        "INSERT INTO agent_versions (id, agent_id, version_no, name, default_model_config_version_id, "
        "default_model_name, system_prompt, application_state_schema_version_id, config_hash, "
        "memory_settings, created_by, created_at) "
        "VALUES ('av-1', 'ag-1', 1, 'test-version', 'mcv-1', 'test-model', '', :svid, 'h', "
        "'{\"long_term_enabled\": true}'::json, 'u-1', now())"
    ), {"svid": app_schema_version_id})
    s.execute(text("UPDATE agents SET active_version_id = 'av-1' WHERE id = 'ag-1'"))
    s.execute(text(
        "INSERT INTO agent_sessions (id, agent_id, owner_user_id, status, created_at, updated_at) "
        "VALUES ('sess-1', 'ag-1', 'u-1', 'active', now(), now())"
    ))
    s.commit()
    yield s
    s.close()
    with engine.begin() as connection:
        connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
    engine.dispose()


def _insert_memory(session, *, memory_id="mem-1", agent_id="ag-1", user_id="u-1",
                   subject_key="self", predicate="user.name",
                   display_text="User's name is Alex", confidence=0.9,
                   consent_basis="explicit_statement", status="active"):
    session.execute(text(
        "INSERT INTO agent_memories (id, security_domain_id, agent_id, user_id, kind, subject_key, "
        "predicate, canonical_value, canonical_value_hash, display_text, confidence, sensitivity, "
        "consent_basis, source_spans, status, created_at, updated_at) "
        "VALUES (:id, :d, :a, :u, 'semantic', :sk, :pred, CAST(:val AS jsonb), :hash, :disp, :conf, "
        "'low', :consent_basis, CAST('[0]' AS jsonb), :status, now(), now())"
    ), {"id": memory_id, "d": DEFAULT_DOMAIN, "a": agent_id, "u": user_id, "sk": subject_key,
        "pred": predicate, "val": json.dumps(display_text), "hash": f"hash-{memory_id}",
        "disp": display_text, "conf": confidence, "consent_basis": consent_basis,
        "status": status})
    session.commit()


def test_search_vector_populated_and_gin_index_matches(session):
    _insert_memory(session, display_text="User's favorite color is blue")
    row = session.execute(text(
        "SELECT search_vector IS NOT NULL AS has_vector FROM agent_memories WHERE id = 'mem-1'"
    )).mappings().one()
    assert row["has_vector"] is True
    matched = session.execute(text(
        "SELECT count(*) FROM agent_memories WHERE search_vector @@ to_tsquery('simple', 'blue')"
    )).scalar_one()
    assert matched == 1
    unmatched = session.execute(text(
        "SELECT count(*) FROM agent_memories WHERE search_vector @@ to_tsquery('simple', 'purple')"
    )).scalar_one()
    assert unmatched == 0


def test_downgrade_removes_search_vector_column():
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL required")
    schema = "p6b2b_downgrade_" + uuid.uuid4().hex
    engine = create_engine(TEST_DATABASE_URL)
    with engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    try:
        assert _alembic(schema, "upgrade", HEAD).returncode == 0
        assert _alembic(schema, "downgrade", "0019_agent_memory_long_term").returncode == 0
        with engine.connect() as conn, conn.begin():
            conn.execute(text(f'SET search_path TO "{schema}"'))
            cols = conn.execute(text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'agent_memories' AND table_schema = :s "
                "AND column_name = 'search_vector'"
            ), {"s": schema}).fetchall()
        assert cols == []
    finally:
        with engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        engine.dispose()


def test_upsert_and_query_similar_roundtrips_when_chroma_available():
    from app.services.memory import vector_store

    if not vector_store.is_available():
        pytest.skip("Chroma not reachable in this environment")

    domain = f"sd-vs-{uuid.uuid4().hex[:8]}"
    memory_id = f"mem-{uuid.uuid4().hex[:8]}"
    ok = vector_store.upsert_memory_embedding(memory_id, domain, "User's favorite color is blue")
    assert ok is True

    hits = vector_store.query_similar(domain, "what color does the user like", n_results=5)
    assert any(h["id"] == memory_id for h in hits)
    hit = next(h for h in hits if h["id"] == memory_id)
    assert -1.0 <= hit["cosine"] <= 1.0

    deleted = vector_store.delete_memory_embedding(memory_id, domain)
    assert deleted is True
    hits_after = vector_store.query_similar(domain, "what color does the user like", n_results=5)
    assert all(h["id"] != memory_id for h in hits_after)


def test_upsert_returns_false_when_chroma_unavailable(monkeypatch):
    from app.services.memory import vector_store
    monkeypatch.setattr(vector_store, "is_available", lambda: False)
    assert vector_store.upsert_memory_embedding("mem-x", "sd-1", "text") is False
    assert vector_store.query_similar("sd-1", "query", n_results=5) == []
    assert vector_store.delete_memory_embedding("mem-x", "sd-1") is False


def test_memory_collection_name_is_namespaced_per_security_domain():
    from app.services.memory import vector_store
    assert vector_store.memory_collection_name("sd-1") != vector_store.memory_collection_name("sd-2")
    assert "sd-1" in vector_store.memory_collection_name("sd-1")


def test_sweep_applies_pending_upsert_and_sets_embedding_model_version(session, monkeypatch):
    _insert_memory(session, memory_id="mem-up", display_text="User likes tea")
    session.execute(text(
        "INSERT INTO agent_memory_vector_outbox (id, memory_id, event_type, state, created_at) "
        "VALUES ('vo-1', 'mem-up', 'upsert', 'pending', now())"
    ))
    session.commit()

    from app.services.memory import vector_store
    calls = []
    monkeypatch.setattr(vector_store, "upsert_memory_embedding",
                        lambda mid, sd, text_: calls.append((mid, sd, text_)) or True)

    from app.tasks.agent_memory_vector import sweep_memory_vector_outbox
    result = sweep_memory_vector_outbox(db=session)
    assert result == {"processed": 1, "applied": 1, "errors": 0}
    assert calls == [("mem-up", DEFAULT_DOMAIN, "User likes tea")]

    outbox_state = session.execute(text(
        "SELECT state FROM agent_memory_vector_outbox WHERE id = 'vo-1'"
    )).scalar_one()
    assert outbox_state == "applied"
    embedding_version = session.execute(text(
        "SELECT embedding_model_version FROM agent_memories WHERE id = 'mem-up'"
    )).scalar_one()
    assert embedding_version == vector_store.MEMORY_EMBEDDING_MODEL_VERSION


def test_sweep_applies_pending_delete_and_clears_embedding_model_version(session, monkeypatch):
    _insert_memory(session, memory_id="mem-del", status="deleted")
    session.execute(text(
        "UPDATE agent_memories SET embedding_model_version = 'memory-embed-chroma-default-v1' "
        "WHERE id = 'mem-del'"
    ))
    session.execute(text(
        "INSERT INTO agent_memory_vector_outbox (id, memory_id, event_type, state, created_at) "
        "VALUES ('vo-2', 'mem-del', 'delete', 'pending', now())"
    ))
    session.commit()

    from app.services.memory import vector_store
    monkeypatch.setattr(vector_store, "delete_memory_embedding", lambda mid, sd: True)

    from app.tasks.agent_memory_vector import sweep_memory_vector_outbox
    result = sweep_memory_vector_outbox(db=session)
    assert result == {"processed": 1, "applied": 1, "errors": 0}
    embedding_version = session.execute(text(
        "SELECT embedding_model_version FROM agent_memories WHERE id = 'mem-del'"
    )).scalar_one()
    assert embedding_version is None


def test_sweep_isolates_per_row_errors_and_leaves_row_pending(session, monkeypatch):
    _insert_memory(session, memory_id="mem-a", display_text="fact a")
    _insert_memory(session, memory_id="mem-b", display_text="fact b", subject_key="self",
                   predicate="user.preference")
    session.execute(text(
        "INSERT INTO agent_memory_vector_outbox (id, memory_id, event_type, state, created_at) "
        "VALUES ('vo-a', 'mem-a', 'upsert', 'pending', now()), "
        "('vo-b', 'mem-b', 'upsert', 'pending', now())"
    ))
    session.commit()

    from app.services.memory import vector_store

    def flaky_upsert(memory_id, security_domain_id, display_text):
        if memory_id == "mem-a":
            raise RuntimeError("simulated Chroma failure")
        return True

    monkeypatch.setattr(vector_store, "upsert_memory_embedding", flaky_upsert)

    from app.tasks.agent_memory_vector import sweep_memory_vector_outbox
    result = sweep_memory_vector_outbox(db=session)
    assert result == {"processed": 2, "applied": 1, "errors": 1}
    states = {r["memory_id"]: r["state"] for r in session.execute(text(
        "SELECT memory_id, state FROM agent_memory_vector_outbox"
    )).mappings().all()}
    assert states["mem-a"] == "pending"
    assert states["mem-b"] == "applied"


def test_sweep_claims_rows_with_for_update_skip_locked_not_double_processed(session, monkeypatch):
    """Two sequential sweep calls over the same already-applied row must not
    re-invoke the vector store a second time — the claim (state transition)
    happens atomically as part of the same statement that selects the row."""
    _insert_memory(session, memory_id="mem-once")
    session.execute(text(
        "INSERT INTO agent_memory_vector_outbox (id, memory_id, event_type, state, created_at) "
        "VALUES ('vo-once', 'mem-once', 'upsert', 'pending', now())"
    ))
    session.commit()

    from app.services.memory import vector_store
    calls = []
    monkeypatch.setattr(vector_store, "upsert_memory_embedding",
                        lambda mid, sd, t: calls.append(mid) or True)

    from app.tasks.agent_memory_vector import sweep_memory_vector_outbox
    first = sweep_memory_vector_outbox(db=session)
    second = sweep_memory_vector_outbox(db=session)
    assert first == {"processed": 1, "applied": 1, "errors": 0}
    assert second == {"processed": 0, "applied": 0, "errors": 0}
    assert calls == ["mem-once"]

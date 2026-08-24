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


def test_sweep_claim_query_holds_locks_on_all_batch_rows_until_commit(session):
    """Regression test for a real concurrency bug caught in review: the
    original per-row-commit design released locks on unprocessed batch rows
    early. This verifies the property the fix depends on -- the batch's
    SELECT ... FOR UPDATE OF ov SKIP LOCKED holds its locks on every
    claimed row for as long as the claiming transaction stays open (i.e.
    until the sweep's single end-of-batch commit), not just until the
    first per-row write commits."""
    _insert_memory(session, memory_id="mem-1", display_text="fact one")
    _insert_memory(session, memory_id="mem-2", display_text="fact two", subject_key="self",
                   predicate="user.preference")
    session.execute(text(
        "INSERT INTO agent_memory_vector_outbox (id, memory_id, event_type, state, created_at) "
        "VALUES ('vo-1', 'mem-1', 'upsert', 'pending', now()), "
        "('vo-2', 'mem-2', 'upsert', 'pending', now())"
    ))
    session.commit()

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    second_session = sessionmaker(bind=create_engine(str(session.bind.url)))()
    try:
        claim_query = text(
            "SELECT ov.id FROM agent_memory_vector_outbox ov "
            "WHERE ov.state = 'pending' ORDER BY ov.created_at "
            "FOR UPDATE OF ov SKIP LOCKED"
        )
        claimed_by_a = session.execute(claim_query).scalars().all()
        assert set(claimed_by_a) == {"vo-1", "vo-2"}

        # session B's identical claim query, while A's transaction is still
        # open (no commit yet -- exactly what the fixed sweep does for its
        # whole batch), must see BOTH rows as locked (SKIP LOCKED -> empty
        # result). This is the exact property the original per-row-commit
        # design violated.
        claimed_by_b = second_session.execute(claim_query).scalars().all()
        assert claimed_by_b == []

        session.commit()
    finally:
        second_session.close()


def test_fetch_sql_candidates_excludes_non_active_and_expired(session):
    from datetime import datetime, timedelta, timezone
    _insert_memory(session, memory_id="mem-active", status="active")
    _insert_memory(session, memory_id="mem-pending", status="pending_confirmation",
                   subject_key="self", predicate="user.preference")
    _insert_memory(session, memory_id="mem-deleted", status="deleted",
                   subject_key="self", predicate="user.goal")
    session.execute(text(
        "INSERT INTO agent_memories (id, security_domain_id, agent_id, user_id, kind, subject_key, "
        "predicate, canonical_value, canonical_value_hash, display_text, confidence, sensitivity, "
        "consent_basis, source_spans, status, expires_at, created_at, updated_at) "
        "VALUES ('mem-expired', :d, :a, :u, 'semantic', 'self', 'user.fact', '\"x\"'::jsonb, "
        "'hash-exp', 'expired fact', 0.9, 'low', 'explicit_statement', '[0]'::jsonb, 'active', "
        ":expiry, now(), now())"
    ), {"d": DEFAULT_DOMAIN, "a": "ag-1", "u": "u-1",
        "expiry": datetime.now(timezone.utc) - timedelta(days=1)})
    session.commit()

    from app.services.memory.recall import _fetch_sql_candidates
    candidates = _fetch_sql_candidates(session, security_domain_id=DEFAULT_DOMAIN,
                                       agent_id="ag-1", user_id="u-1")
    ids = {c["id"] for c in candidates}
    assert ids == {"mem-active"}


def test_fetch_sql_candidates_scoped_to_exact_domain_agent_user(session):
    session.execute(text(
        "INSERT INTO agents (id,visibility,status,owner_id,created_at,updated_at) "
        "VALUES ('ag-2','private','active','u-1',now(),now())"
    ))
    session.commit()
    _insert_memory(session, memory_id="mem-1", agent_id="ag-1")
    _insert_memory(session, memory_id="mem-2", agent_id="ag-2", subject_key="self",
                   predicate="user.preference")
    session.commit()

    from app.services.memory.recall import _fetch_sql_candidates
    candidates = _fetch_sql_candidates(session, security_domain_id=DEFAULT_DOMAIN,
                                       agent_id="ag-1", user_id="u-1")
    assert {c["id"] for c in candidates} == {"mem-1"}


def test_lexical_channel_normalizes_single_match_to_one(session):
    _insert_memory(session, memory_id="mem-1", display_text="User's favorite color is blue")
    session.commit()

    from app.services.memory.recall import _lexical_channel
    scores = _lexical_channel(session, security_domain_id=DEFAULT_DOMAIN, agent_id="ag-1",
                              user_id="u-1", query_text="blue", limit=10)
    assert scores == {"mem-1": 1.0}


def test_lexical_channel_normalizes_equal_positive_ranks_to_one(session):
    _insert_memory(session, memory_id="mem-1", display_text="apple apple",
                   subject_key="self", predicate="user.fact")
    _insert_memory(session, memory_id="mem-2", display_text="apple apple",
                   subject_key="other", predicate="user.fact")
    session.commit()

    from app.services.memory.recall import _lexical_channel
    scores = _lexical_channel(session, security_domain_id=DEFAULT_DOMAIN, agent_id="ag-1",
                              user_id="u-1", query_text="apple", limit=10)
    assert scores == {"mem-1": 1.0, "mem-2": 1.0}


def test_lexical_channel_min_max_normalizes_distinct_positive_ranks(session):
    _insert_memory(session, memory_id="mem-strong", display_text="dog dog dog dog dog",
                   subject_key="self", predicate="user.fact")
    _insert_memory(session, memory_id="mem-weak", display_text="dog cat bird fish tree",
                   subject_key="other", predicate="user.fact")
    session.commit()

    from app.services.memory.recall import _lexical_channel
    scores = _lexical_channel(session, security_domain_id=DEFAULT_DOMAIN, agent_id="ag-1",
                              user_id="u-1", query_text="dog", limit=10)
    assert scores["mem-strong"] == 1.0
    assert scores["mem-weak"] == 0.0  # the minimum among two distinct positive ranks


def test_lexical_channel_excludes_non_matching_candidates_entirely(session):
    _insert_memory(session, memory_id="mem-1", display_text="completely unrelated text")
    session.commit()

    from app.services.memory.recall import _lexical_channel
    scores = _lexical_channel(session, security_domain_id=DEFAULT_DOMAIN, agent_id="ag-1",
                              user_id="u-1", query_text="zzz_no_match_zzz", limit=10)
    assert scores == {}


def test_semantic_channel_excludes_stale_embedding_model_version(monkeypatch):
    from app.services.memory import recall, vector_store
    monkeypatch.setattr(vector_store, "query_similar", lambda sd, q, n: [
        {"id": "mem-current", "cosine": 0.8}, {"id": "mem-stale", "cosine": 0.9},
        {"id": "mem-never-embedded", "cosine": 0.7},
    ])
    sql_candidates = [
        {"id": "mem-current", "embedding_model_version": vector_store.MEMORY_EMBEDDING_MODEL_VERSION},
        {"id": "mem-stale", "embedding_model_version": "old-version"},
        {"id": "mem-never-embedded", "embedding_model_version": None},
    ]
    scores = recall._semantic_channel("sd-1", "query", 10, sql_candidates=sql_candidates)
    assert scores == {"mem-current": 0.8}


def test_semantic_channel_empty_when_chroma_unavailable(monkeypatch):
    from app.services.memory import recall, vector_store
    monkeypatch.setattr(vector_store, "query_similar", lambda sd, q, n: [])
    scores = recall._semantic_channel("sd-1", "query", 10, sql_candidates=[
        {"id": "mem-1", "embedding_model_version": vector_store.MEMORY_EMBEDDING_MODEL_VERSION}])
    assert scores == {}


def test_recency_score_matches_exponential_decay_to_six_decimals():
    from datetime import datetime, timedelta, timezone
    from app.services.memory.recall import _recency_score
    now = datetime(2026, 8, 24, 12, 0, 0, tzinfo=timezone.utc)
    updated_at = now - timedelta(days=30)
    import math
    expected = round(math.exp(-30 / 30), 6)
    assert round(_recency_score(updated_at, now), 6) == expected == round(math.exp(-1), 6)


def test_score_candidate_hybrid_formula_to_six_decimals():
    from app.services.memory.recall import _score_candidate
    result = _score_candidate(semantic=0.9, lexical=0.8, confidence=0.7, source_quality=0.95,
                              recency=0.6)
    score, mode = result
    expected = 0.50 * ((0.9 + 1.0) / 2.0) + 0.20 * 0.8 + 0.15 * 0.7 + 0.10 * 0.6 + 0.05 * 0.95
    assert round(score, 6) == round(expected, 6)
    assert mode == "hybrid"


def test_score_candidate_lexical_only_formula_to_six_decimals():
    from app.services.memory.recall import _score_candidate
    result = _score_candidate(semantic=None, lexical=0.8, confidence=0.7, source_quality=0.90,
                              recency=0.6)
    score, mode = result
    expected = 0.40 * 0.8 + 0.30 * 0.7 + 0.20 * 0.6 + 0.10 * 0.90
    assert round(score, 6) == round(expected, 6)
    assert mode == "lexical_only"


def test_score_candidate_lexical_only_requires_positive_lexical_evidence():
    from app.services.memory.recall import _score_candidate
    assert _score_candidate(semantic=None, lexical=0.0, confidence=1.0, source_quality=1.0,
                            recency=1.0) is None


def test_score_candidate_below_threshold_rejected_for_both_modes():
    from app.services.memory.recall import _score_candidate
    assert _score_candidate(semantic=0.1, lexical=0.1, confidence=0.1, source_quality=0.1,
                            recency=0.1) is None
    assert _score_candidate(semantic=None, lexical=0.1, confidence=0.1, source_quality=0.1,
                            recency=0.1) is None


def test_score_candidate_exactly_at_threshold_is_accepted():
    from app.services.memory.recall import _score_candidate
    # lexical-only: 0.40*l + 0.30*c + 0.20*r + 0.10*q == 0.60 exactly when all inputs are 0.6
    result = _score_candidate(semantic=None, lexical=0.6, confidence=0.6, source_quality=0.6,
                              recency=0.6)
    assert result is not None
    assert round(result[0], 6) == 0.6


def test_source_quality_maps_the_two_reachable_consent_bases():
    from app.services.memory.recall import SOURCE_QUALITY
    assert SOURCE_QUALITY["explicit_statement"] == 0.95
    assert SOURCE_QUALITY["explicit_confirmation"] == 0.90


def _no_semantic_hits(monkeypatch):
    """Deterministically empties the semantic channel regardless of whether a
    real Chroma instance is reachable in this environment or what it may
    have indexed from earlier test runs — every test in this file reuses
    DEFAULT_DOMAIN, so relying on Chroma's *actual* current state instead of
    explicitly mocking it would make these tests flaky/order-dependent."""
    from app.services.memory import recall as recall_module
    monkeypatch.setattr(recall_module, "_semantic_channel",
                        lambda sd, q, limit, *, sql_candidates: {})


def test_recall_memories_all_lexical_returns_cited_strings(session, monkeypatch):
    _no_semantic_hits(monkeypatch)
    _insert_memory(session, memory_id="mem-1", display_text="User's favorite color is blue",
                   confidence=0.9)
    session.commit()

    # NOTE: deviates from the task-6 brief's literal query_text="what color".
    # plainto_tsquery('simple', 'what color') ANDs both terms ('simple' has no
    # stopword list, so 'what' is a required literal token); since "what" never
    # appears in the stored text, that query produces zero lexical matches
    # (verified directly against Postgres) and the test would incorrectly
    # assert on an unreachable case. "favorite color" shares both tokens with
    # the fixture text and exercises the same all-lexical-citation behavior
    # the test is meant to verify.
    from app.services.memory.recall import recall_memories
    result = recall_memories(session, security_domain_id=DEFAULT_DOMAIN, agent_id="ag-1",
                             user_id="u-1", query_text="favorite color", model_name="gpt-4o",
                             recall_count=8, recall_token_budget=800)
    assert result == ["[memory:mem-1] User's favorite color is blue"]


def test_recall_memories_deduplicates_cross_channel_duplicate_winner(session, monkeypatch):
    """A memory present in BOTH the lexical and vector channels' raw hit
    lists must appear exactly once in the final result, scored once."""
    from app.services.memory import recall as recall_module
    _insert_memory(session, memory_id="mem-1", display_text="User likes tea", confidence=0.9)
    session.commit()

    monkeypatch.setattr(recall_module, "_semantic_channel", lambda sd, q, limit, *, sql_candidates: (
        {"mem-1": 0.95} if any(c["id"] == "mem-1" for c in sql_candidates) else {}))

    result = recall_module.recall_memories(session, security_domain_id=DEFAULT_DOMAIN,
                                           agent_id="ag-1", user_id="u-1", query_text="tea",
                                           model_name="gpt-4o", recall_count=8,
                                           recall_token_budget=800)
    assert len(result) == 1
    assert result == ["[memory:mem-1] User likes tea"]


def test_recall_memories_stops_at_recall_count(session, monkeypatch):
    _no_semantic_hits(monkeypatch)
    for i in range(5):
        _insert_memory(session, memory_id=f"mem-{i}", display_text=f"apple fact number {i}",
                       subject_key=f"subject-{i}", predicate="user.fact", confidence=0.9)
    session.commit()

    from app.services.memory.recall import recall_memories
    result = recall_memories(session, security_domain_id=DEFAULT_DOMAIN, agent_id="ag-1",
                             user_id="u-1", query_text="apple", model_name="gpt-4o",
                             recall_count=2, recall_token_budget=8000)
    assert len(result) == 2


def test_recall_memories_stops_at_token_budget_without_truncating(session, monkeypatch):
    _no_semantic_hits(monkeypatch)
    from app.services.runtime.tokenizer import count_tokens
    long_text = "User's preference is: " + ("word " * 200)
    _insert_memory(session, memory_id="mem-long", display_text=long_text, confidence=0.9)
    session.commit()

    budget = count_tokens(f"[memory:mem-long] {long_text}", "gpt-4o") - 1
    from app.services.memory.recall import recall_memories
    result = recall_memories(session, security_domain_id=DEFAULT_DOMAIN, agent_id="ag-1",
                             user_id="u-1", query_text="preference", model_name="gpt-4o",
                             recall_count=8, recall_token_budget=budget)
    assert result == []  # doesn't fit even by one token — skipped, never truncated


def test_recall_memories_empty_when_no_candidates(session):
    from app.services.memory.recall import recall_memories
    result = recall_memories(session, security_domain_id=DEFAULT_DOMAIN, agent_id="ag-1",
                             user_id="u-1", query_text="anything", model_name="gpt-4o",
                             recall_count=8, recall_token_budget=800)
    assert result == []


def test_recall_memories_sequential_mixed_selection_prefers_diversity(session, monkeypatch):
    """Two embedded candidates: greedy selection's diversity penalty must
    still admit both if the query itself has room."""
    from app.services.memory import recall as recall_module
    _insert_memory(session, memory_id="mem-1", display_text="User likes coffee",
                   subject_key="s1", predicate="user.fact", confidence=0.9)
    _insert_memory(session, memory_id="mem-2", display_text="User likes tea",
                   subject_key="s2", predicate="user.fact", confidence=0.9)
    session.commit()

    monkeypatch.setattr(recall_module, "_semantic_channel", lambda sd, q, limit, *, sql_candidates: (
        {"mem-1": 0.9, "mem-2": 0.9}))

    result = recall_module.recall_memories(session, security_domain_id=DEFAULT_DOMAIN,
                                           agent_id="ag-1", user_id="u-1",
                                           query_text="beverage", model_name="gpt-4o",
                                           recall_count=8, recall_token_budget=8000)
    assert set(result) == {"[memory:mem-1] User likes coffee", "[memory:mem-2] User likes tea"}


def test_recall_memories_final_tie_break_order(session, monkeypatch):
    """Two candidates with identical score/selection_score break the tie by
    updated_at DESC, then id ASC."""
    _no_semantic_hits(monkeypatch)
    _insert_memory(session, memory_id="mem-b", display_text="apple", subject_key="s1",
                   predicate="user.fact", confidence=0.9)
    _insert_memory(session, memory_id="mem-a", display_text="apple", subject_key="s2",
                   predicate="user.fact", confidence=0.9)
    session.commit()
    # both are byte-identical text so lexical/confidence/recency/source_quality are identical
    # too (recency ties because both inserted in the same test transaction's `now()` call);
    # tie-break must fall through to updated_at DESC, id ASC. Since both rows' updated_at are
    # effectively equal (same statement batch), id ASC decides: 'mem-a' < 'mem-b'.
    from app.services.memory.recall import recall_memories
    result = recall_memories(session, security_domain_id=DEFAULT_DOMAIN, agent_id="ag-1",
                             user_id="u-1", query_text="apple", model_name="gpt-4o",
                             recall_count=1, recall_token_budget=8000)
    assert result == ["[memory:mem-a] apple"]


def test_greedy_select_diversity_penalty_stays_bounded_with_negative_raw_cosine(session, monkeypatch):
    """Regression test for a real bug caught in review: storing the RAW
    (unmapped, [-1,1]-range) semantic cosine instead of the (cosine+1)/2
    -mapped value let the diversity penalty flip into a bonus for
    negative/widely-divergent raw cosines. Confirms the fixed code keeps
    _cosine_similarity_proxy's output genuinely bounded and the diversity
    term genuinely penalizes (not rewards) closeness to an already-selected
    embedded item."""
    from datetime import datetime, timezone

    from app.services.memory import recall as recall_module
    _insert_memory(session, memory_id="mem-already-selected", display_text="fact alpha",
                   subject_key="s1", predicate="user.fact", confidence=0.9)
    _insert_memory(session, memory_id="mem-new-candidate", display_text="fact beta",
                   subject_key="s2", predicate="user.fact", confidence=0.9)
    session.commit()

    # raw cosines chosen so (cosine+1)/2 keeps both hybrid-eligible (score >= 0.60)
    # while still being far enough apart in raw space to have flipped the bug's sign.
    monkeypatch.setattr(recall_module, "_semantic_channel", lambda sd, q, limit, *, sql_candidates: (
        {"mem-already-selected": -0.2, "mem-new-candidate": 0.9}))

    result = recall_module.recall_memories(session, security_domain_id=DEFAULT_DOMAIN,
                                           agent_id="ag-1", user_id="u-1", query_text="fact",
                                           model_name="gpt-4o", recall_count=2,
                                           recall_token_budget=8000)
    # both should still be selected (score threshold is independently satisfied by
    # each candidate's OWN mapped semantic score, not by the pairwise diversity term)
    assert set(result) == {"[memory:mem-already-selected] fact alpha",
                           "[memory:mem-new-candidate] fact beta"}

    # direct unit check on the now-correctly-mapped cosine value stored internally
    now = datetime.now(timezone.utc)
    scored = recall_module._dedup_and_score_candidates(
        sql_candidates=[
            {"id": "mem-already-selected", "display_text": "fact alpha", "confidence": 0.9,
             "consent_basis": "explicit_statement", "updated_at": now},
        ],
        lexical_scores={"mem-already-selected": 1.0},
        semantic_scores={"mem-already-selected": -0.2}, now=now)
    assert len(scored) == 1
    # (cosine + 1) / 2 for raw -0.2 is 0.4 -- must be in [0, 1], never the raw -0.2
    assert 0.0 <= scored[0]["cosine"] <= 1.0
    assert round(scored[0]["cosine"], 6) == 0.4


def test_recall_for_turn_invokes_recall_memories_with_derived_scope(session, monkeypatch):
    """Integration-shaped unit test: exercises the same call chain
    _build_messages_and_tools uses, not the full LangGraph runtime (which
    needs a live model server) — verifies recall_memories is actually
    invoked with the right scope, derived from the baseline session fixture's
    sess-1/ag-1/u-1 (already seeded, no extra setup needed)."""
    _insert_memory(session, memory_id="mem-1", display_text="User likes dark mode", confidence=0.9)
    session.commit()

    from app.services.memory import recall as recall_module
    captured = {}
    real_recall = recall_module.recall_memories

    def spy(db, **kwargs):
        captured.update(kwargs)
        return real_recall(db, **kwargs)

    monkeypatch.setattr(recall_module, "recall_memories", spy)

    # NOTE: deviates from the task-7 brief's literal query_text="theme preference"
    # for the same reason documented above test_recall_memories_all_lexical_returns_
    # cited_strings: plainto_tsquery('simple', 'theme preference') ANDs both terms,
    # neither of which appears in "User likes dark mode", so it produces zero
    # lexical matches; with Chroma unavailable in this environment the semantic
    # channel is empty too, so the brief's literal query_text makes the real
    # recall_memories legitimately return [] and the assertion unreachable.
    # "dark mode" shares tokens with the fixture text and exercises the same
    # scope-derivation behavior this test is meant to verify.
    from app.runtime.langgraph_runtime import _recall_for_turn
    result = _recall_for_turn(session, session_id="sess-1", agent_id="ag-1",
                              query_text="dark mode", model_name="gpt-4o",
                              recall_count=8, recall_token_budget=800)
    assert result == ["[memory:mem-1] User likes dark mode"]
    assert captured["security_domain_id"] == DEFAULT_DOMAIN
    assert captured["agent_id"] == "ag-1"
    assert captured["user_id"] == "u-1"


def test_recall_for_turn_fails_open_on_exception(session, monkeypatch):
    from app.services.memory import recall as recall_module
    monkeypatch.setattr(recall_module, "recall_memories",
                        lambda db, **kw: (_ for _ in ()).throw(RuntimeError("boom")))

    from app.runtime.langgraph_runtime import _recall_for_turn
    result = _recall_for_turn(session, session_id="sess-1", agent_id="ag-1",
                              query_text="anything", model_name="gpt-4o",
                              recall_count=8, recall_token_budget=800)
    assert result == []


def test_recall_for_turn_empty_for_unknown_session(session):
    from app.runtime.langgraph_runtime import _recall_for_turn
    result = _recall_for_turn(session, session_id="does-not-exist", agent_id="ag-1",
                              query_text="anything", model_name="gpt-4o",
                              recall_count=8, recall_token_budget=800)
    assert result == []


def test_golden_all_embedded_candidates_use_hybrid_mode(session, monkeypatch):
    from app.services.memory import recall as recall_module
    _insert_memory(session, memory_id="mem-1", display_text="User prefers email over chat",
                   confidence=0.9)
    session.commit()

    monkeypatch.setattr(recall_module, "_semantic_channel", lambda sd, q, limit, *, sql_candidates: (
        {"mem-1": 0.9}))
    # no lexical match at all for this query on purpose — still eligible via hybrid alone
    result = recall_module.recall_memories(session, security_domain_id=DEFAULT_DOMAIN,
                                           agent_id="ag-1", user_id="u-1",
                                           query_text="zzz_no_lexical_match_zzz",
                                           model_name="gpt-4o", recall_count=8,
                                           recall_token_budget=800)
    assert result == ["[memory:mem-1] User prefers email over chat"]


def test_golden_all_lexical_candidates_use_lexical_only_mode(session, monkeypatch):
    _no_semantic_hits(monkeypatch)
    _insert_memory(session, memory_id="mem-1", display_text="apple apple apple", confidence=0.9)
    session.commit()
    from app.services.memory.recall import recall_memories
    result = recall_memories(session, security_domain_id=DEFAULT_DOMAIN, agent_id="ag-1",
                             user_id="u-1", query_text="apple", model_name="gpt-4o",
                             recall_count=8, recall_token_budget=800)
    assert result == ["[memory:mem-1] apple apple apple"]


def test_golden_mixed_candidates_highest_result_from_hybrid_mode(session, monkeypatch):
    from app.services.memory import recall as recall_module
    _insert_memory(session, memory_id="mem-embedded", display_text="User loves hiking",
                   subject_key="s1", predicate="user.fact", confidence=0.95)
    # NOTE: deviates from the task-8 brief's literal display_text="hiking hiking"
    # (confidence=0.5) for mem-lexical. Verified directly against Postgres and by
    # running the real (unmocked) recall_memories: with "hiking hiking" (2 hits)
    # vs "User loves hiking" (1 hit), ts_rank_cd ranks them 0.2 vs 0.1, so
    # min-max normalization gives mem-embedded lexical=0.0 and mem-lexical
    # lexical=1.0. _greedy_select's selection_score for a hybrid-mode candidate
    # is always 0.75*score (even with zero diversity penalty on the first pick),
    # while a lexical_only candidate's selection_score is its raw score with no
    # discount. With mem-embedded's own lexical component pinned at 0.0, its max
    # attainable selection_score (~0.60, using the reachable max source_quality
    # of 0.95) is provably lower than mem-lexical's lexical_only score at
    # confidence=0.5 (~0.845) for ANY valid confidence/semantic values -- so the
    # brief's literal fixture can never produce a hybrid-mode winner; it was
    # failing when run verbatim. Fixed by (a) using a single "hiking" occurrence
    # for mem-lexical too, so both candidates' lexical ranks tie at 1.0 (verified
    # equal via ts_rank_cd), removing mem-embedded's lexical-component handicap,
    # and (b) lowering mem-lexical's confidence to 0.1 so its own score drops
    # below the hybrid candidate's now-competitive 0.75-discounted selection
    # score. Everything else (assertions, ids, intent: verify a hybrid-mode
    # candidate can out-rank a lexical-only one) is unchanged.
    _insert_memory(session, memory_id="mem-lexical", display_text="hiking",
                   subject_key="s2", predicate="user.fact", confidence=0.1)
    session.commit()

    monkeypatch.setattr(recall_module, "_semantic_channel", lambda sd, q, limit, *, sql_candidates: (
        {"mem-embedded": 0.99} if any(c["id"] == "mem-embedded" for c in sql_candidates) else {}))

    result = recall_module.recall_memories(session, security_domain_id=DEFAULT_DOMAIN,
                                           agent_id="ag-1", user_id="u-1", query_text="hiking",
                                           model_name="gpt-4o", recall_count=1,
                                           recall_token_budget=8000)
    assert result == ["[memory:mem-embedded] User loves hiking"]


def test_golden_mixed_candidates_highest_result_from_lexical_only_mode(session, monkeypatch):
    from app.services.memory import recall as recall_module
    _insert_memory(session, memory_id="mem-embedded", display_text="unrelated fact",
                   subject_key="s1", predicate="user.fact", confidence=0.5)
    _insert_memory(session, memory_id="mem-lexical", display_text="running running running",
                   subject_key="s2", predicate="user.fact", confidence=0.95)
    session.commit()

    # NOTE: deviates from the task-8 brief's literal semantic mock value of 0.1 for
    # mem-embedded. Verified directly by calling the real _score_candidate: with
    # lexical=0.0 (mem-embedded's "unrelated fact" shares no tokens with "running"),
    # confidence=0.5, source_quality=0.95 (explicit_statement), recency~1.0, a
    # semantic of 0.1 (mapped to (0.1+1)/2=0.55) produces a hybrid score of only
    # ~0.4975 -- below SCORE_THRESHOLD=0.60, so _score_candidate rejects it outright
    # and mem-embedded never enters _greedy_select's pool at all. The test then
    # "passes" only because mem-lexical is the sole scored candidate, not because it
    # genuinely outranked a real hybrid-mode contender -- defeating the point of a
    # "mixed candidates" golden. Raised semantic to 0.6 (mapped 0.8), which
    # _score_candidate confirms clears the threshold at exactly 0.6225 (hybrid mode,
    # genuinely in the pool), while its _greedy_select selection_score (0.75 * 0.6225
    # = 0.466875, the diversity-formula discount that applies even on the first pick)
    # still loses decisively to mem-lexical's undiscounted lexical_only score of 0.98
    # -- a real head-to-head where lexical-only wins on the merits.
    monkeypatch.setattr(recall_module, "_semantic_channel", lambda sd, q, limit, *, sql_candidates: (
        {"mem-embedded": 0.6} if any(c["id"] == "mem-embedded" for c in sql_candidates) else {}))

    result = recall_module.recall_memories(session, security_domain_id=DEFAULT_DOMAIN,
                                           agent_id="ag-1", user_id="u-1", query_text="running",
                                           model_name="gpt-4o", recall_count=1,
                                           recall_token_budget=8000)
    assert result == ["[memory:mem-lexical] running running running"]


def test_golden_chroma_outage_all_candidates_fall_back_to_lexical_only(session, monkeypatch):
    """Even a candidate that WAS previously embedded (a current, matching
    embedding_model_version on the SQL row) must fall back to lexical-only
    if Chroma itself is unreachable/errors at query time — this is the
    meaningful "outage" case, distinct from "never embedded" (already
    covered by test_golden_missing_embedding_excluded_from_vector_channel)."""
    from app.services.memory import vector_store
    _insert_memory(session, memory_id="mem-1", display_text="banana banana", confidence=0.9)
    session.execute(text(
        "UPDATE agent_memories SET embedding_model_version = :v WHERE id = 'mem-1'"
    ), {"v": vector_store.MEMORY_EMBEDDING_MODEL_VERSION})
    session.commit()

    monkeypatch.setattr(vector_store, "query_similar", lambda sd, q, n: [])

    from app.services.memory.recall import recall_memories
    result = recall_memories(session, security_domain_id=DEFAULT_DOMAIN, agent_id="ag-1",
                             user_id="u-1", query_text="banana", model_name="gpt-4o",
                             recall_count=8, recall_token_budget=800)
    assert result == ["[memory:mem-1] banana banana"]


def test_golden_missing_embedding_excluded_from_vector_channel(session):
    _insert_memory(session, memory_id="mem-1", display_text="never embedded fact",
                   confidence=0.9)
    session.commit()
    # embedding_model_version defaults to NULL — missing, per Global Constraints.
    row = session.execute(text(
        "SELECT embedding_model_version FROM agent_memories WHERE id = 'mem-1'"
    )).scalar_one()
    assert row is None

    from app.services.memory.recall import _semantic_channel
    scores = _semantic_channel(DEFAULT_DOMAIN, "query", 10, sql_candidates=[
        {"id": "mem-1", "embedding_model_version": None}])
    assert scores == {}


def test_golden_stale_embedding_model_version_excluded_from_vector_channel(session, monkeypatch):
    from app.services.memory import recall as recall_module, vector_store
    _insert_memory(session, memory_id="mem-1", display_text="stale embedding fact",
                   confidence=0.9)
    session.execute(text(
        "UPDATE agent_memories SET embedding_model_version = 'a-since-retired-embedding-model' "
        "WHERE id = 'mem-1'"
    ))
    session.commit()

    monkeypatch.setattr(vector_store, "query_similar", lambda sd, q, n: [
        {"id": "mem-1", "cosine": 0.99}])  # Chroma still physically has it — SQL side is stale

    scores = recall_module._semantic_channel(DEFAULT_DOMAIN, "query", 10, sql_candidates=[
        {"id": "mem-1", "embedding_model_version": "a-since-retired-embedding-model"}])
    assert scores == {}

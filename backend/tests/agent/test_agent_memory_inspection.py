"""P6B-3: memory inspection/correction/deletion service + API + UI.
Spec: docs/superpowers/plans/2026-08-09-agent-ontology-implementation.md,
Section 12.1 (inspect/correct/delete), Section 13.1 Phase 6 row (stable
error codes). Builds on P6B-2a's already-merged write path."""
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
    schema = "p6b3_" + uuid.uuid4().hex
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
        "INSERT INTO users (id,username,email,password_hash,role,is_active,security_domain_id,"
        "created_at,updated_at) VALUES ('u-2','b','b@t.com','h','admin',true,:d,now(),now())"
    ), {"d": DEFAULT_DOMAIN})
    s.execute(text(
        "INSERT INTO agents (id,visibility,status,owner_id,created_at,updated_at) "
        "VALUES ('ag-1','private','active','u-1',now(),now())"
    ))
    s.commit()
    yield s
    s.close()
    with engine.begin() as connection:
        connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
    engine.dispose()


def _insert_memory(session, *, memory_id="mem-1", user_id="u-1", agent_id="ag-1",
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
    session.execute(text(
        "INSERT INTO agent_memory_revisions (id, memory_id, revision_no, canonical_value, "
        "display_text, confidence, consent_basis, source_spans, created_by, created_at) "
        "VALUES (:id, :mid, 1, CAST(:val AS jsonb), :disp, :conf, :consent_basis, "
        "CAST('[0]' AS jsonb), :u, now())"
    ), {"id": f"rev-{memory_id}", "mid": memory_id, "val": json.dumps(display_text),
        "disp": display_text, "conf": confidence, "consent_basis": consent_basis, "u": user_id})
    session.commit()


def test_list_memories_scoped_to_exact_user(session):
    _insert_memory(session, memory_id="mem-1", user_id="u-1")
    _insert_memory(session, memory_id="mem-2", user_id="u-2", subject_key="self",
                   predicate="user.preference")
    session.commit()

    from app.services.memory.inspection import list_memories
    result = list_memories(session, user_id="u-1", agent_id="ag-1")
    assert [m["id"] for m in result] == ["mem-1"]


def test_list_memories_filters_by_status(session):
    _insert_memory(session, memory_id="mem-active", status="active")
    _insert_memory(session, memory_id="mem-pending", status="pending_confirmation",
                   subject_key="self", predicate="user.preference")
    session.commit()

    from app.services.memory.inspection import list_memories
    result = list_memories(session, user_id="u-1", agent_id="ag-1", status="pending_confirmation")
    assert [m["id"] for m in result] == ["mem-pending"]


def test_list_memories_excludes_deleted_by_default(session):
    _insert_memory(session, memory_id="mem-active", status="active")
    _insert_memory(session, memory_id="mem-deleted", status="deleted",
                   subject_key="self", predicate="user.preference")
    session.commit()

    from app.services.memory.inspection import list_memories
    result = list_memories(session, user_id="u-1", agent_id="ag-1")
    assert [m["id"] for m in result] == ["mem-active"]


def test_get_memory_includes_revision_history(session):
    _insert_memory(session, memory_id="mem-1", display_text="original")
    session.execute(text(
        "UPDATE agent_memory_revisions SET revision_no = 1 WHERE id = 'rev-mem-1'"
    ))
    session.execute(text(
        "INSERT INTO agent_memory_revisions (id, memory_id, revision_no, canonical_value, "
        "display_text, confidence, consent_basis, source_spans, created_by, created_at, "
        "superseded_at) "
        "VALUES ('rev-mem-1-old', 'mem-1', 0, '\"stale\"'::jsonb, 'stale', 0.5, "
        "'explicit_statement', '[0]'::jsonb, 'u-1', now() - interval '1 day', now())"
    ))
    session.commit()

    from app.services.memory.inspection import get_memory
    result = get_memory(session, user_id="u-1", memory_id="mem-1")
    assert result["id"] == "mem-1"
    assert result["display_text"] == "original"
    assert [r["display_text"] for r in result["revisions"]] == ["original", "stale"]


def test_get_memory_returns_none_for_wrong_user(session):
    _insert_memory(session, memory_id="mem-1", user_id="u-1")
    session.commit()

    from app.services.memory.inspection import get_memory
    result = get_memory(session, user_id="u-2", memory_id="mem-1")
    assert result is None


def test_get_memory_embedding_status_never_embedded(session):
    _insert_memory(session, memory_id="mem-1", status="pending_confirmation")
    session.commit()

    from app.services.memory.inspection import get_memory
    result = get_memory(session, user_id="u-1", memory_id="mem-1")
    assert result["embedding_status"] == "never_embedded"


def test_get_memory_embedding_status_current(session):
    from app.services.memory import vector_store
    _insert_memory(session, memory_id="mem-1", status="active")
    session.execute(text(
        "UPDATE agent_memories SET embedding_model_version = :v WHERE id = 'mem-1'"
    ), {"v": vector_store.MEMORY_EMBEDDING_MODEL_VERSION})
    session.commit()

    from app.services.memory.inspection import get_memory
    result = get_memory(session, user_id="u-1", memory_id="mem-1")
    assert result["embedding_status"] == "current"


def test_get_memory_embedding_status_pending_when_outbox_row_unapplied(session):
    _insert_memory(session, memory_id="mem-1", status="active")
    session.execute(text(
        "INSERT INTO agent_memory_vector_outbox (id, memory_id, event_type, state, created_at) "
        "VALUES ('vo-1', 'mem-1', 'upsert', 'pending', now())"
    ))
    session.commit()

    from app.services.memory.inspection import get_memory
    result = get_memory(session, user_id="u-1", memory_id="mem-1")
    assert result["embedding_status"] == "pending"


def test_get_memory_includes_conflict_info_when_conflicted(session):
    _insert_memory(session, memory_id="mem-a", status="conflicted", display_text="Alex")
    _insert_memory(session, memory_id="mem-b", status="conflicted", display_text="Alexandra",
                   subject_key="self")
    session.execute(text(
        "INSERT INTO agent_memory_conflicts (id, security_domain_id, agent_id, user_id, "
        "subject_key, predicate, memory_id_a, memory_id_b, status, created_at) "
        "VALUES ('conf-1', :d, 'ag-1', 'u-1', 'self', 'user.name', 'mem-a', 'mem-b', 'open', now())"
    ), {"d": DEFAULT_DOMAIN})
    session.commit()

    from app.services.memory.inspection import get_memory
    result = get_memory(session, user_id="u-1", memory_id="mem-a")
    assert result["conflict"]["conflict_id"] == "conf-1"
    assert result["conflict"]["other_memory_id"] == "mem-b"
    assert result["conflict"]["other_display_text"] == "Alexandra"


def test_confirm_memory_requires_explicit_consent_flag(session):
    _insert_memory(session, memory_id="mem-1", status="pending_confirmation",
                   consent_basis="explicit_confirmation")
    session.commit()

    from app.services.memory.inspection import MemoryConsentRequiredError, confirm_memory
    with pytest.raises(MemoryConsentRequiredError):
        confirm_memory(session, user_id="u-1", memory_id="mem-1", consent=False)

    row = session.execute(text(
        "SELECT status FROM agent_memories WHERE id = 'mem-1'"
    )).mappings().one()
    assert row["status"] == "pending_confirmation"


def test_confirm_memory_grants_real_consent_and_activates(session):
    _insert_memory(session, memory_id="mem-1", status="pending_confirmation",
                   consent_basis="explicit_confirmation")
    session.commit()

    from app.services.memory.inspection import confirm_memory
    result = confirm_memory(session, user_id="u-1", memory_id="mem-1", consent=True)
    assert result["status"] == "active"

    row = session.execute(text(
        "SELECT status FROM agent_memories WHERE id = 'mem-1'"
    )).mappings().one()
    assert row["status"] == "active"
    consent_count = session.execute(text(
        "SELECT count(*) FROM agent_memory_consents"
    )).scalar_one()
    assert consent_count == 1
    revision_consent_id = session.execute(text(
        "SELECT consent_id FROM agent_memory_revisions WHERE memory_id = 'mem-1'"
    )).scalar_one()
    assert revision_consent_id is not None
    outbox = session.execute(text(
        "SELECT event_type, state FROM agent_memory_vector_outbox WHERE memory_id = 'mem-1'"
    )).mappings().one()
    assert outbox["event_type"] == "upsert"
    assert outbox["state"] == "pending"


def test_confirm_memory_rejects_conflicted_memory(session):
    _insert_memory(session, memory_id="mem-a", status="conflicted",
                   consent_basis="explicit_confirmation")
    _insert_memory(session, memory_id="mem-b", status="conflicted", subject_key="self")
    session.execute(text(
        "INSERT INTO agent_memory_conflicts (id, security_domain_id, agent_id, user_id, "
        "subject_key, predicate, memory_id_a, memory_id_b, status, created_at) "
        "VALUES ('conf-1', :d, 'ag-1', 'u-1', 'self', 'user.name', 'mem-a', 'mem-b', 'open', now())"
    ), {"d": DEFAULT_DOMAIN})
    session.commit()

    from app.services.memory.inspection import MemoryConflictError, confirm_memory
    with pytest.raises(MemoryConflictError):
        confirm_memory(session, user_id="u-1", memory_id="mem-a", consent=True)


def test_confirm_memory_on_already_deleted_memory_raises_consent_required(session):
    _insert_memory(session, memory_id="mem-1", status="deleted",
                   consent_basis="explicit_confirmation")
    session.execute(text(
        "UPDATE agent_memories SET deleted_at = now() WHERE id = 'mem-1'"
    ))
    session.commit()

    from app.services.memory.inspection import MemoryConsentRequiredError, confirm_memory
    with pytest.raises(MemoryConsentRequiredError):
        confirm_memory(session, user_id="u-1", memory_id="mem-1", consent=True)


def test_reject_memory_tombstones_without_granting_consent(session):
    _insert_memory(session, memory_id="mem-1", status="pending_confirmation",
                   consent_basis="explicit_confirmation")
    session.commit()

    from app.services.memory.inspection import reject_memory
    reject_memory(session, user_id="u-1", memory_id="mem-1")

    row = session.execute(text(
        "SELECT status, deleted_at FROM agent_memories WHERE id = 'mem-1'"
    )).mappings().one()
    assert row["status"] == "deleted"
    assert row["deleted_at"] is not None
    consent_count = session.execute(text(
        "SELECT count(*) FROM agent_memory_consents"
    )).scalar_one()
    assert consent_count == 0
    outbox_count = session.execute(text(
        "SELECT count(*) FROM agent_memory_vector_outbox WHERE memory_id = 'mem-1'"
    )).scalar_one()
    assert outbox_count == 0  # pending_confirmation memories are never embedded, per P6B-2a


def test_reject_memory_enqueues_delete_outbox_for_active_embedded_memory(session):
    from app.services.memory import vector_store
    _insert_memory(session, memory_id="mem-1", status="active")
    session.execute(text(
        "UPDATE agent_memories SET embedding_model_version = :v WHERE id = 'mem-1'"
    ), {"v": vector_store.MEMORY_EMBEDDING_MODEL_VERSION})
    session.commit()

    from app.services.memory.inspection import reject_memory
    reject_memory(session, user_id="u-1", memory_id="mem-1")

    row = session.execute(text(
        "SELECT status, deleted_at FROM agent_memories WHERE id = 'mem-1'"
    )).mappings().one()
    assert row["status"] == "deleted"
    assert row["deleted_at"] is not None
    outbox = session.execute(text(
        "SELECT event_type, state FROM agent_memory_vector_outbox WHERE memory_id = 'mem-1'"
    )).mappings().one()
    assert outbox["event_type"] == "delete"
    assert outbox["state"] == "pending"


def test_reject_memory_called_twice_is_idempotent(session):
    from app.services.memory import vector_store
    _insert_memory(session, memory_id="mem-1", status="active")
    session.execute(text(
        "UPDATE agent_memories SET embedding_model_version = :v WHERE id = 'mem-1'"
    ), {"v": vector_store.MEMORY_EMBEDDING_MODEL_VERSION})
    session.commit()

    from app.services.memory.inspection import reject_memory
    reject_memory(session, user_id="u-1", memory_id="mem-1")
    reject_memory(session, user_id="u-1", memory_id="mem-1")  # second call: no-op

    outbox_count = session.execute(text(
        "SELECT count(*) FROM agent_memory_vector_outbox WHERE memory_id = 'mem-1'"
    )).scalar_one()
    assert outbox_count == 1  # not duplicated by the second call


def test_correct_memory_supersedes_revision_and_updates_row(session):
    _insert_memory(session, memory_id="mem-1", display_text="User's name is Alex", confidence=0.9)
    session.commit()

    from app.services.memory.inspection import correct_memory
    result = correct_memory(session, user_id="u-1", memory_id="mem-1",
                            display_text="User's name is Alexander", confidence=0.95)
    assert result["display_text"] == "User's name is Alexander"

    row = session.execute(text(
        "SELECT display_text, confidence FROM agent_memories WHERE id = 'mem-1'"
    )).mappings().one()
    assert row["display_text"] == "User's name is Alexander"
    assert float(row["confidence"]) == 0.95

    revisions = session.execute(text(
        "SELECT revision_no, display_text, superseded_at FROM agent_memory_revisions "
        "WHERE memory_id = 'mem-1' ORDER BY revision_no"
    )).mappings().all()
    assert len(revisions) == 2
    assert revisions[0]["superseded_at"] is not None
    assert revisions[1]["display_text"] == "User's name is Alexander"
    assert revisions[1]["superseded_at"] is None

    outbox = session.execute(text(
        "SELECT event_type, state FROM agent_memory_vector_outbox WHERE memory_id = 'mem-1'"
    )).mappings().one()
    assert outbox["event_type"] == "upsert"


def test_correct_memory_rejects_conflicted_memory(session):
    _insert_memory(session, memory_id="mem-a", status="conflicted")
    _insert_memory(session, memory_id="mem-b", status="conflicted", subject_key="self")
    session.execute(text(
        "INSERT INTO agent_memory_conflicts (id, security_domain_id, agent_id, user_id, "
        "subject_key, predicate, memory_id_a, memory_id_b, status, created_at) "
        "VALUES ('conf-1', :d, 'ag-1', 'u-1', 'self', 'user.name', 'mem-a', 'mem-b', 'open', now())"
    ), {"d": DEFAULT_DOMAIN})
    session.commit()

    from app.services.memory.inspection import MemoryConflictError, correct_memory
    with pytest.raises(MemoryConflictError):
        correct_memory(session, user_id="u-1", memory_id="mem-a", display_text="new value")


def test_correct_memory_on_deleted_memory_returns_none_and_no_outbox(session):
    _insert_memory(session, memory_id="mem-1", status="deleted")
    session.execute(text(
        "UPDATE agent_memories SET deleted_at = now() WHERE id = 'mem-1'"
    ))
    session.commit()

    from app.services.memory.inspection import correct_memory
    result = correct_memory(session, user_id="u-1", memory_id="mem-1", display_text="new value")
    assert result is None

    outbox_count = session.execute(text(
        "SELECT count(*) FROM agent_memory_vector_outbox WHERE memory_id = 'mem-1'"
    )).scalar_one()
    assert outbox_count == 0


def test_correct_memory_carries_forward_consent_id(session):
    _insert_memory(session, memory_id="mem-1", display_text="original")
    session.execute(text(
        "INSERT INTO agent_memory_consents (id, security_domain_id, agent_id, user_id, "
        "consent_basis, granted_at) VALUES ('con-1', :d, 'ag-1', 'u-1', 'explicit_statement', now())"
    ), {"d": DEFAULT_DOMAIN})
    session.execute(text(
        "UPDATE agent_memory_revisions SET consent_id = 'con-1' WHERE id = 'rev-mem-1'"
    ))
    session.commit()

    from app.services.memory.inspection import correct_memory
    correct_memory(session, user_id="u-1", memory_id="mem-1", display_text="corrected")

    new_consent_id = session.execute(text(
        "SELECT consent_id FROM agent_memory_revisions "
        "WHERE memory_id = 'mem-1' AND superseded_at IS NULL"
    )).scalar_one()
    assert new_consent_id == "con-1"


def test_correct_memory_uses_candidate_value_namespace_and_syncs_canonical_value(session):
    from app.services.memory.canonicalizer import canonical_hash
    _insert_memory(session, memory_id="mem-1", display_text="original")
    session.commit()

    from app.services.memory.inspection import correct_memory
    correct_memory(session, user_id="u-1", memory_id="mem-1", display_text="corrected value")

    row = session.execute(text(
        "SELECT canonical_value, canonical_value_hash FROM agent_memories WHERE id = 'mem-1'"
    )).mappings().one()
    # Same inputs a fresh candidate restating this value would hash with --
    # extraction.py always calls canonical_hash(value, "candidate_value").
    expected_hash = canonical_hash("corrected value", "candidate_value")
    assert row["canonical_value_hash"] == expected_hash
    assert row["canonical_value"] == "corrected value"


def test_correct_memory_dedup_collision_raises_memory_conflict_error(session):
    from app.services.memory.canonicalizer import canonical_hash
    _insert_memory(session, memory_id="mem-1", subject_key="self", predicate="user.name",
                   display_text="Alex", status="active")
    _insert_memory(session, memory_id="mem-2", subject_key="self", predicate="user.name",
                   display_text="Bob", status="active")
    colliding_hash = canonical_hash("Robert", "candidate_value")
    session.execute(text(
        "UPDATE agent_memories SET canonical_value_hash = :h WHERE id = 'mem-2'"
    ), {"h": colliding_hash})
    session.commit()

    from app.services.memory.inspection import MemoryConflictError, correct_memory
    with pytest.raises(MemoryConflictError):
        correct_memory(session, user_id="u-1", memory_id="mem-1", display_text="Robert")

    # session must still be usable -- proves the rollback actually happened
    assert session.execute(text("SELECT 1")).scalar_one() == 1


def test_delete_memory_tombstones_and_enqueues_outbox_when_previously_embedded(session):
    _insert_memory(session, memory_id="mem-1", status="active")
    session.execute(text(
        "UPDATE agent_memories SET embedding_model_version = 'memory-embed-chroma-default-v1' "
        "WHERE id = 'mem-1'"
    ))
    session.commit()

    from app.services.memory.inspection import delete_memory
    delete_memory(session, user_id="u-1", memory_id="mem-1")

    row = session.execute(text(
        "SELECT status, deleted_at FROM agent_memories WHERE id = 'mem-1'"
    )).mappings().one()
    assert row["status"] == "deleted"
    assert row["deleted_at"] is not None
    outbox = session.execute(text(
        "SELECT event_type FROM agent_memory_vector_outbox WHERE memory_id = 'mem-1'"
    )).mappings().one()
    assert outbox["event_type"] == "delete"


def test_delete_memory_skips_outbox_when_never_embedded(session):
    _insert_memory(session, memory_id="mem-1", status="pending_confirmation")
    session.commit()

    from app.services.memory.inspection import delete_memory
    delete_memory(session, user_id="u-1", memory_id="mem-1")

    outbox_count = session.execute(text(
        "SELECT count(*) FROM agent_memory_vector_outbox WHERE memory_id = 'mem-1'"
    )).scalar_one()
    assert outbox_count == 0


def test_delete_memory_scoped_to_correct_user(session):
    _insert_memory(session, memory_id="mem-1", user_id="u-1")
    session.commit()

    from app.services.memory.inspection import delete_memory
    delete_memory(session, user_id="u-2", memory_id="mem-1")  # wrong user -- silent no-op

    row = session.execute(text(
        "SELECT status FROM agent_memories WHERE id = 'mem-1'"
    )).mappings().one()
    assert row["status"] == "active"  # untouched


def test_correct_memory_handles_display_text_with_double_quotes(session):
    _insert_memory(session, memory_id="mem-1", display_text="original")
    session.commit()

    from app.services.memory.inspection import correct_memory
    result = correct_memory(session, user_id="u-1", memory_id="mem-1",
                            display_text='She said "hi" to me')
    assert result["display_text"] == 'She said "hi" to me'

    from app.services.memory.inspection import get_memory
    fetched = get_memory(session, user_id="u-1", memory_id="mem-1")
    assert fetched["display_text"] == 'She said "hi" to me'


def _seed_conflict(session, *, winner_id="mem-a", loser_id="mem-b"):
    _insert_memory(session, memory_id=winner_id, status="conflicted", display_text="Alex")
    _insert_memory(session, memory_id=loser_id, status="conflicted", display_text="Alexandra",
                   subject_key="self")
    session.execute(text(
        "INSERT INTO agent_memory_conflicts (id, security_domain_id, agent_id, user_id, "
        "subject_key, predicate, memory_id_a, memory_id_b, status, created_at) "
        "VALUES ('conf-1', :d, 'ag-1', 'u-1', 'self', 'user.name', :a, :b, 'open', now())"
    ), {"d": DEFAULT_DOMAIN, "a": winner_id, "b": loser_id})
    session.commit()


def test_list_conflicts_returns_both_sides(session):
    _seed_conflict(session)

    from app.services.memory.inspection import list_conflicts
    result = list_conflicts(session, user_id="u-1", agent_id="ag-1")
    assert len(result) == 1
    assert result[0]["conflict_id"] == "conf-1"
    assert {result[0]["display_text_a"], result[0]["display_text_b"]} == {"Alex", "Alexandra"}


def test_resolve_conflict_activates_winner_and_tombstones_loser(session):
    from app.services.memory import vector_store
    _seed_conflict(session, winner_id="mem-a", loser_id="mem-b")
    # mem-b (the loser) was previously active and embedded before losing this
    # conflict -- the realistic case the "delete" outbox assertion below is
    # meant to exercise. See _open_conflict's actual write path for why a
    # freshly-extracted conflicting candidate is NOT the only way a loser
    # can arrive at this state.
    session.execute(text(
        "UPDATE agent_memories SET embedding_model_version = :v WHERE id = 'mem-b'"
    ), {"v": vector_store.MEMORY_EMBEDDING_MODEL_VERSION})
    session.commit()

    from app.services.memory.inspection import resolve_conflict
    result = resolve_conflict(session, user_id="u-1", conflict_id="conf-1",
                              winning_memory_id="mem-a")
    assert result["status"] == "active"

    winner = session.execute(text(
        "SELECT status FROM agent_memories WHERE id = 'mem-a'"
    )).mappings().one()
    assert winner["status"] == "active"
    loser = session.execute(text(
        "SELECT status, deleted_at FROM agent_memories WHERE id = 'mem-b'"
    )).mappings().one()
    assert loser["status"] == "deleted"
    assert loser["deleted_at"] is not None

    conflict = session.execute(text(
        "SELECT status, resolved_by_revision_id, resolved_at FROM agent_memory_conflicts "
        "WHERE id = 'conf-1'"
    )).mappings().one()
    assert conflict["status"] == "resolved"
    assert conflict["resolved_by_revision_id"] is not None
    assert conflict["resolved_at"] is not None

    outbox_events = {r["memory_id"]: r["event_type"] for r in session.execute(text(
        "SELECT memory_id, event_type FROM agent_memory_vector_outbox"
    )).mappings().all()}
    assert outbox_events == {"mem-a": "upsert", "mem-b": "delete"}


def test_resolve_conflict_skips_outbox_delete_when_loser_never_embedded(session):
    _seed_conflict(session, winner_id="mem-a", loser_id="mem-b")
    # deliberately do NOT set embedding_model_version on mem-b here -- this is
    # the realistic "never-embedded pending_confirmation-turned-conflicted
    # candidate" case from _open_conflict's actual write path (a fresh
    # candidate is written directly as 'conflicted', never taking the
    # status == 'active' branch that would enqueue an embedding upsert).

    from app.services.memory.inspection import resolve_conflict
    resolve_conflict(session, user_id="u-1", conflict_id="conf-1", winning_memory_id="mem-a")

    outbox_events = {r["memory_id"]: r["event_type"] for r in session.execute(text(
        "SELECT memory_id, event_type FROM agent_memory_vector_outbox"
    )).mappings().all()}
    assert outbox_events == {"mem-a": "upsert"}  # no delete for the never-embedded loser


def test_resolve_conflict_grants_consent_for_unconsented_explicit_confirmation_winner(session):
    _insert_memory(session, memory_id="mem-a", status="conflicted", display_text="Alex",
                   consent_basis="explicit_confirmation")
    _insert_memory(session, memory_id="mem-b", status="conflicted", display_text="Alexandra",
                   subject_key="self")
    session.execute(text(
        "INSERT INTO agent_memory_conflicts (id, security_domain_id, agent_id, user_id, "
        "subject_key, predicate, memory_id_a, memory_id_b, status, created_at) "
        "VALUES ('conf-1', :d, 'ag-1', 'u-1', 'self', 'user.name', 'mem-a', 'mem-b', 'open', now())"
    ), {"d": DEFAULT_DOMAIN})
    session.commit()

    from app.services.memory.inspection import resolve_conflict
    result = resolve_conflict(session, user_id="u-1", conflict_id="conf-1",
                              winning_memory_id="mem-a")
    assert result["status"] == "active"

    consent_count = session.execute(text(
        "SELECT count(*) FROM agent_memory_consents"
    )).scalar_one()
    assert consent_count == 1
    revision_consent_id = session.execute(text(
        "SELECT consent_id FROM agent_memory_revisions "
        "WHERE memory_id = 'mem-a' AND superseded_at IS NULL"
    )).scalar_one()
    assert revision_consent_id is not None


def test_resolve_conflict_rejects_memory_id_not_in_this_conflict(session):
    _seed_conflict(session)
    _insert_memory(session, memory_id="mem-unrelated", subject_key="other",
                   predicate="user.preference")
    session.commit()

    from app.services.memory.inspection import MemoryConflictError, resolve_conflict
    with pytest.raises(MemoryConflictError):
        resolve_conflict(session, user_id="u-1", conflict_id="conf-1",
                         winning_memory_id="mem-unrelated")


def test_resolve_conflict_rejects_already_resolved_conflict(session):
    _seed_conflict(session)
    from app.services.memory.inspection import resolve_conflict
    resolve_conflict(session, user_id="u-1", conflict_id="conf-1", winning_memory_id="mem-a")

    from app.services.memory.inspection import MemoryConflictError
    with pytest.raises(MemoryConflictError):
        resolve_conflict(session, user_id="u-1", conflict_id="conf-1", winning_memory_id="mem-a")

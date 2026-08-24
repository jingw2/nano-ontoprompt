"""P6B-2a: long-term Agent memory write path (extraction, canonicalization,
consent, conflict). Recall (Chroma vector-outbox consumption, hybrid
semantic/lexical ranking) is P6B-2b, a separate future plan — nothing here
makes a memory recallable."""
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
HEAD = "0019_agent_memory_long_term"


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
    schema = "p6b2a_" + uuid.uuid4().hex
    engine = create_engine(TEST_DATABASE_URL)
    with engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    assert _alembic(schema, "upgrade", HEAD).returncode == 0
    s = sessionmaker(bind=create_engine(_scoped_url(schema)))()
    s.execute(text(
        "INSERT INTO users (id,username,email,password_hash,role,is_active,security_domain_id,created_at,updated_at) "
        "VALUES ('u-1','a','a@t.com','h','admin',true,:d,now(),now())"
    ), {"d": DEFAULT_DOMAIN})
    s.execute(text(
        "INSERT INTO model_configs (id,name,config_type,api_base,api_key_encrypted,provider,models,options,created_by,created_at,updated_at) "
        "VALUES ('mc-1','m','llm',NULL,'','openai','[]'::json,'{}'::json,'u-1',now(),now())"
    ))
    s.execute(text(
        "INSERT INTO model_config_versions (id, model_config_id, version_no, provider, options, behavior_hash, model_contract, created_at) "
        "VALUES ('mcv-1', 'mc-1', 1, 'openai', '{}'::json, :hash, "
        "'[{\"provider_model_revision\": \"test-model\"}]'::json, now())"
    ), {"hash": "0" * 64})
    s.execute(text("UPDATE model_configs SET active_version_id = 'mcv-1' WHERE id = 'mc-1'"))
    app_schema_version_id = s.execute(text(
        "SELECT active_version_id FROM application_state_schema_registries WHERE application_key = 'chat-v1'"
    )).scalar_one()
    s.execute(text(
        "INSERT INTO agents (id,visibility,status,owner_id,created_at,updated_at) "
        "VALUES ('ag-1','private','active','u-1',now(),now())"
    ))
    s.execute(text(
        "INSERT INTO agent_versions (id, agent_id, version_no, name, default_model_config_version_id, "
        "default_model_name, system_prompt, application_state_schema_version_id, config_hash, memory_settings, created_by, created_at) "
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


def test_migration_seeds_predicate_registry(session):
    rows = session.execute(text(
        "SELECT predicate, cardinality FROM agent_memory_predicate_registry ORDER BY predicate"
    )).mappings().all()
    assert {(r["predicate"], r["cardinality"]) for r in rows} == {
        ("user.fact", "multi"), ("user.goal", "multi"), ("user.name", "single"),
        ("user.preference", "multi"), ("user.role", "single"),
    }


def test_agent_memories_predicate_fk_rejects_unknown_predicate(session):
    with pytest.raises(Exception):
        session.execute(text(
            "INSERT INTO agent_memories (id, security_domain_id, agent_id, user_id, kind, subject_key, "
            "predicate, canonical_value, canonical_value_hash, display_text, confidence, sensitivity, "
            "consent_basis, source_spans, status, created_at, updated_at) "
            "VALUES ('m-1', :d, 'ag-1', 'u-1', 'semantic', 'self', 'user.unknown_predicate', "
            "'{}'::jsonb, 'h' || repeat('0', 63), 'x', 0.9, 'low', 'explicit_statement', '[]'::jsonb, "
            "'active', now(), now())"
        ), {"d": DEFAULT_DOMAIN})
        session.commit()
    session.rollback()


def test_agent_memories_partial_unique_active_dedup_key(session):
    def insert(mid, status):
        session.execute(text(
            "INSERT INTO agent_memories (id, security_domain_id, agent_id, user_id, kind, subject_key, "
            "predicate, canonical_value, canonical_value_hash, display_text, confidence, sensitivity, "
            "consent_basis, source_spans, status, created_at, updated_at) "
            "VALUES (:id, :d, 'ag-1', 'u-1', 'semantic', 'self', 'user.name', "
            "'{}'::jsonb, 'h' || repeat('0', 63), 'x', 0.9, 'low', 'explicit_statement', '[]'::jsonb, "
            ":status, now(), now())"
        ), {"id": mid, "d": DEFAULT_DOMAIN, "status": status})

    insert("m-1", "active")
    session.commit()
    # a second row with the SAME dedup key but a non-active status is allowed
    insert("m-2", "deleted")
    session.commit()
    # a second ACTIVE row with the same dedup key is rejected
    with pytest.raises(Exception):
        insert("m-3", "active")
        session.commit()
    session.rollback()


def test_agent_memory_fks_are_restrict_not_cascade(session):
    session.execute(text(
        "INSERT INTO agent_memories (id, security_domain_id, agent_id, user_id, kind, subject_key, "
        "predicate, canonical_value, canonical_value_hash, display_text, confidence, sensitivity, "
        "consent_basis, source_spans, status, created_at, updated_at) "
        "VALUES ('m-1', :d, 'ag-1', 'u-1', 'semantic', 'self', 'user.name', "
        "'{}'::jsonb, 'h' || repeat('0', 63), 'x', 0.9, 'low', 'explicit_statement', '[]'::jsonb, "
        "'active', now(), now())"
    ), {"d": DEFAULT_DOMAIN})
    session.commit()
    with pytest.raises(Exception):
        session.execute(text("DELETE FROM agents WHERE id = 'ag-1'"))
        session.commit()
    session.rollback()


def test_extraction_outbox_state_check_constraint(session):
    session.execute(text(
        "INSERT INTO agent_turns (id, session_id, status, created_at, updated_at) "
        "VALUES ('t-1', 'sess-1', 'succeeded', now(), now())"
    ))
    session.commit()
    with pytest.raises(Exception):
        session.execute(text(
            "INSERT INTO agent_memory_extraction_outbox (id, turn_id, session_id, state, created_at) "
            "VALUES ('eo-1', 't-1', 'sess-1', 'not_a_real_state', now())"
        ))
        session.commit()
    session.rollback()


def test_canonicalize_nfkc_normalizes_unicode():
    from app.services.memory.canonicalizer import canonicalize
    # U+FB01 LATIN SMALL LIGATURE FI -> "fi" under NFKC
    assert canonicalize("ﬁle") == "file"


def test_canonicalize_trims_and_collapses_whitespace():
    from app.services.memory.canonicalizer import canonicalize
    assert canonicalize("  hello   world    ") == "hello world"


def test_canonicalize_case_folds_predicate_and_schema_declared_strings():
    from app.services.memory.canonicalizer import canonicalize
    # bare string values case-fold by default (predicate/subject-key handling
    # is the caller's responsibility per-field; canonicalize() folds any
    # plain string value it's given)
    assert canonicalize("HELLO World") == "hello world"


def test_canonicalize_preserves_case_sensitive_marked_values():
    from app.services.memory.canonicalizer import canonicalize, CaseSensitive
    assert canonicalize(CaseSensitive("MixedCase")) == "MixedCase"


def test_canonicalize_encodes_booleans_and_null_explicitly():
    from app.services.memory.canonicalizer import canonicalize
    assert canonicalize(True) is True
    assert canonicalize(False) is False
    assert canonicalize(None) is None


def test_canonicalize_normalizes_integers_and_decimals():
    from app.services.memory.canonicalizer import canonicalize
    assert canonicalize(3.0) == "3"
    assert canonicalize(3.140) == "3.14"
    assert canonicalize(1e3) == "1000"
    assert canonicalize(42) == "42"


def test_canonicalize_converts_timestamps_to_utc_rfc3339_microseconds():
    from datetime import datetime, timezone
    from app.services.memory.canonicalizer import canonicalize
    dt = datetime(2026, 8, 24, 10, 30, 0, 500000, tzinfo=timezone.utc)
    assert canonicalize(dt) == "2026-08-24T10:30:00.500000Z"


def test_canonicalize_sorts_object_keys():
    from app.services.memory.canonicalizer import canonicalize
    # nested numbers are also normalized to their canonical string form
    # (see test_canonicalize_normalizes_numbers_recursively_in_containers)
    assert canonicalize({"b": 1, "a": 2}) == {"a": "2", "b": "1"}


def test_canonicalize_preserves_list_order_by_default():
    from app.services.memory.canonicalizer import canonicalize
    assert canonicalize([3, 1, 2]) == ["3", "1", "2"]


def test_canonicalize_sorts_set_semantics_lists():
    from app.services.memory.canonicalizer import canonicalize, SetSemantics
    assert canonicalize(SetSemantics([3, 1, 2])) == ["1", "2", "3"]


def test_canonicalize_normalizes_numbers_recursively_in_containers():
    from decimal import Decimal
    from app.services.memory.canonicalizer import canonicalize
    # number normalization applies at every level of nesting, not just the
    # top level -- an int 3 and a float 3.0 nested under the same key must
    # canonicalize identically so their dedup hashes match.
    assert canonicalize({"amount": 3}) == canonicalize({"amount": 3.0})
    assert canonicalize([Decimal("3.140")]) == ["3.14"]


def test_canonical_hash_treats_equal_nested_numbers_as_identical():
    from decimal import Decimal
    from app.services.memory.canonicalizer import canonical_hash
    assert canonical_hash({"amount": 3}, "object") == canonical_hash({"amount": 3.0}, "object")
    # a bare Decimal nested inside a container must not crash canonical_hash
    assert canonical_hash({"amount": Decimal("3.140")}, "object") == canonical_hash({"amount": 3.14}, "object")


def test_canonicalize_case_sensitive_still_applies_nfkc_and_whitespace_rules():
    from app.services.memory.canonicalizer import canonicalize, CaseSensitive
    # CaseSensitive only skips case-folding -- NFKC normalization and
    # whitespace-collapsing are unconditional rules that still apply.
    assert canonicalize(CaseSensitive("  MixedCase   Name  ")) == "MixedCase Name"
    # U+FB01 LATIN SMALL LIGATURE FI -> "fi" under NFKC, case preserved
    assert canonicalize(CaseSensitive("ﬁle")) == "file"


def test_canonicalize_rejects_nan_and_infinity():
    from app.services.memory.canonicalizer import CanonicalizationError, canonicalize
    with pytest.raises(CanonicalizationError):
        canonicalize(float("nan"))
    with pytest.raises(CanonicalizationError):
        canonicalize(float("inf"))


def test_canonicalize_rejects_mixed_type_sets():
    from app.services.memory.canonicalizer import CanonicalizationError, SetSemantics, canonicalize
    with pytest.raises(CanonicalizationError):
        canonicalize(SetSemantics([1, "two", 3]))


def test_canonical_hash_includes_version_and_value_type():
    from app.services.memory.canonicalizer import CANONICALIZER_VERSION, canonical_hash
    h1 = canonical_hash("hello", "string")
    h2 = canonical_hash("hello", "text")  # different value_type -> different hash
    assert h1 != h2
    assert len(h1) == 64  # sha256 hex digest


def test_canonical_hash_is_deterministic():
    from app.services.memory.canonicalizer import canonical_hash
    assert canonical_hash({"b": 1, "a": 2}, "object") == canonical_hash({"a": 2, "b": 1}, "object")


def test_lookup_predicate_returns_registered_cardinality(session):
    from app.services.memory.predicate_registry import lookup_predicate
    assert lookup_predicate(session, "user.preference") == {"predicate": "user.preference", "cardinality": "multi"}
    assert lookup_predicate(session, "user.name") == {"predicate": "user.name", "cardinality": "single"}
    assert lookup_predicate(session, "user.unknown") is None


def _seed_active_memory(session, mid: str, predicate: str = "user.preference", subject_key: str = "self"):
    session.execute(text(
        "INSERT INTO agent_memories (id, security_domain_id, agent_id, user_id, kind, subject_key, "
        "predicate, canonical_value, canonical_value_hash, display_text, confidence, sensitivity, "
        "consent_basis, source_spans, status, created_at, updated_at) "
        "VALUES (:id, :d, 'ag-1', 'u-1', 'semantic', :sk, :pred, "
        "'{}'::jsonb, :hash, 'x', 0.9, 'low', 'explicit_statement', '[]'::jsonb, "
        "'active', now(), now())"
    ), {"id": mid, "d": DEFAULT_DOMAIN, "sk": subject_key, "pred": predicate, "hash": f"h{mid}" + "0" * 60})


def test_check_cardinality_passes_under_the_cap(session):
    from app.services.memory.predicate_registry import check_cardinality
    for i in range(9):
        _seed_active_memory(session, f"m-{i}")
    session.commit()
    check_cardinality(session, security_domain_id=DEFAULT_DOMAIN, agent_id="ag-1", user_id="u-1",
                      subject_key="self", predicate="user.preference")  # 9 active, 10th allowed -> no raise


def test_check_cardinality_raises_at_the_cap(session):
    from app.services.memory.predicate_registry import PredicateRegistryError, check_cardinality
    for i in range(10):
        _seed_active_memory(session, f"m-{i}")
    session.commit()
    with pytest.raises(PredicateRegistryError):
        check_cardinality(session, security_domain_id=DEFAULT_DOMAIN, agent_id="ag-1", user_id="u-1",
                          subject_key="self", predicate="user.preference")


def test_check_cardinality_ignores_deleted_rows(session):
    from app.services.memory.predicate_registry import check_cardinality
    for i in range(10):
        _seed_active_memory(session, f"m-{i}")
    session.commit()
    session.execute(text("UPDATE agent_memories SET status = 'deleted' WHERE id = 'm-0'"))
    session.commit()
    # only 9 active now -> 10th allowed
    check_cardinality(session, security_domain_id=DEFAULT_DOMAIN, agent_id="ag-1", user_id="u-1",
                      subject_key="self", predicate="user.preference")


def test_grant_consent_creates_a_row(session):
    from app.services.memory.consent import grant_consent
    consent_id = grant_consent(session, security_domain_id=DEFAULT_DOMAIN, agent_id="ag-1",
                               user_id="u-1", consent_basis="explicit_statement")
    row = session.execute(text(
        "SELECT consent_basis, revoked_at FROM agent_memory_consents WHERE id = :id"
    ), {"id": consent_id}).mappings().one()
    assert row["consent_basis"] == "explicit_statement"
    assert row["revoked_at"] is None


def test_revoke_consent_tombstones_dependent_memories_and_writes_vector_outbox(session):
    from app.services.memory.consent import grant_consent, revoke_consent
    consent_id = grant_consent(session, security_domain_id=DEFAULT_DOMAIN, agent_id="ag-1",
                               user_id="u-1", consent_basis="explicit_statement")
    session.execute(text(
        "INSERT INTO agent_memories (id, security_domain_id, agent_id, user_id, kind, subject_key, "
        "predicate, canonical_value, canonical_value_hash, display_text, confidence, sensitivity, "
        "consent_basis, source_spans, status, created_at, updated_at) "
        "VALUES ('m-1', :d, 'ag-1', 'u-1', 'semantic', 'self', 'user.name', "
        "'{}'::jsonb, 'h' || repeat('0', 63), 'x', 0.9, 'low', 'explicit_statement', '[]'::jsonb, "
        "'active', now(), now())"
    ), {"d": DEFAULT_DOMAIN})
    session.execute(text(
        "INSERT INTO agent_memory_revisions (id, memory_id, revision_no, canonical_value, display_text, "
        "confidence, consent_basis, source_spans, consent_id, created_by, created_at) "
        "VALUES ('rev-1', 'm-1', 1, '{}'::jsonb, 'x', 0.9, 'explicit_statement', '[]'::jsonb, :cid, 'u-1', now())"
    ), {"cid": consent_id})
    session.commit()

    tombstoned = revoke_consent(session, consent_id=consent_id)
    assert tombstoned == 1

    memory = session.execute(text(
        "SELECT status, deleted_at FROM agent_memories WHERE id = 'm-1'"
    )).mappings().one()
    assert memory["status"] == "deleted"
    assert memory["deleted_at"] is not None

    outbox = session.execute(text(
        "SELECT event_type, state FROM agent_memory_vector_outbox WHERE memory_id = 'm-1'"
    )).mappings().one()
    assert outbox["event_type"] == "delete"
    assert outbox["state"] == "pending"

    consent_row = session.execute(text(
        "SELECT revoked_at FROM agent_memory_consents WHERE id = :id"
    ), {"id": consent_id}).mappings().one()
    assert consent_row["revoked_at"] is not None


def test_revoke_consent_only_tombstones_memories_via_their_LATEST_revision(session):
    """A memory corrected under a NEW consent must not be tombstoned when the
    OLD consent basis is revoked -- only the latest revision's consent
    governs the current active row."""
    from app.services.memory.consent import grant_consent, revoke_consent
    old_consent = grant_consent(session, security_domain_id=DEFAULT_DOMAIN, agent_id="ag-1",
                                user_id="u-1", consent_basis="explicit_statement")
    new_consent = grant_consent(session, security_domain_id=DEFAULT_DOMAIN, agent_id="ag-1",
                                user_id="u-1", consent_basis="explicit_statement")
    session.execute(text(
        "INSERT INTO agent_memories (id, security_domain_id, agent_id, user_id, kind, subject_key, "
        "predicate, canonical_value, canonical_value_hash, display_text, confidence, sensitivity, "
        "consent_basis, source_spans, status, created_at, updated_at) "
        "VALUES ('m-1', :d, 'ag-1', 'u-1', 'semantic', 'self', 'user.name', "
        "'{}'::jsonb, 'h' || repeat('0', 63), 'x', 0.9, 'low', 'explicit_statement', '[]'::jsonb, "
        "'active', now(), now())"
    ), {"d": DEFAULT_DOMAIN})
    session.execute(text(
        "INSERT INTO agent_memory_revisions (id, memory_id, revision_no, canonical_value, display_text, "
        "confidence, consent_basis, source_spans, consent_id, created_by, created_at, superseded_at) "
        "VALUES ('rev-1', 'm-1', 1, '{}'::jsonb, 'old', 0.9, 'explicit_statement', '[]'::jsonb, :cid, 'u-1', now(), now())"
    ), {"cid": old_consent})
    session.execute(text(
        "INSERT INTO agent_memory_revisions (id, memory_id, revision_no, canonical_value, display_text, "
        "confidence, consent_basis, source_spans, consent_id, created_by, created_at) "
        "VALUES ('rev-2', 'm-1', 2, '{}'::jsonb, 'new', 0.9, 'explicit_statement', '[]'::jsonb, :cid, 'u-1', now())"
    ), {"cid": new_consent})
    session.commit()

    tombstoned = revoke_consent(session, consent_id=old_consent)
    assert tombstoned == 0  # m-1's LATEST revision (rev-2) depends on new_consent, not old_consent
    memory = session.execute(text("SELECT status FROM agent_memories WHERE id = 'm-1'")).mappings().one()
    assert memory["status"] == "active"


def _seed_turn_with_messages(session, turn_id="t-1", user_text="Please call me Alex.", assistant_text="Got it, Alex!"):
    session.execute(text(
        "INSERT INTO agent_turns (id, session_id, status, created_at, updated_at) "
        "VALUES (:id, 'sess-1', 'succeeded', now(), now())"
    ), {"id": turn_id})
    # Allocate unique ordinals per session by extracting the turn number
    turn_no = int(turn_id.split('-')[1])
    ordinal_user = (turn_no - 1) * 2
    ordinal_assistant = ordinal_user + 1
    session.execute(text(
        "INSERT INTO agent_messages (id, session_id, turn_id, role, ordinal, content, created_at) "
        "VALUES (:id1, 'sess-1', :turn, 'user', :ord_u, :u, now()), "
        "(:id2, 'sess-1', :turn, 'assistant', :ord_a, :a, now())"
    ), {"id1": f"{turn_id}-u", "id2": f"{turn_id}-a", "turn": turn_id, "ord_u": ordinal_user,
        "ord_a": ordinal_assistant, "u": user_text, "a": assistant_text})
    session.commit()


def test_extraction_writes_explicit_statement_directly(session, monkeypatch):
    from app.services.memory import extraction as extraction_module
    _seed_turn_with_messages(session)
    monkeypatch.setattr(extraction_module, "_call_extractor", lambda *a, **k: [
        {"subject_key": "self", "predicate": "user.name", "canonical_value": "Alex",
         "display_text": "User's name is Alex", "kind": "semantic", "confidence": 0.95,
         "sensitivity": "low", "source_spans": [0], "consent_basis": "explicit_statement",
         "expires_at": None},
    ])
    result = extraction_module.extract_memories_for_turn(session, turn_id="t-1")
    assert result == {"candidates": 1, "written": 1, "pending_confirmation": 0, "conflicts": 0, "rejected": 0}
    row = session.execute(text(
        "SELECT status, predicate, consent_basis FROM agent_memories WHERE agent_id = 'ag-1'"
    )).mappings().one()
    assert row["status"] == "active"
    assert row["predicate"] == "user.name"
    assert row["consent_basis"] == "explicit_statement"


def test_extraction_holds_tool_derived_candidates_pending_confirmation(session, monkeypatch):
    from app.services.memory import extraction as extraction_module
    _seed_turn_with_messages(session)
    monkeypatch.setattr(extraction_module, "_call_extractor", lambda *a, **k: [
        {"subject_key": "self", "predicate": "user.preference", "canonical_value": "dark mode",
         "display_text": "User seems to prefer dark mode", "kind": "semantic", "confidence": 0.7,
         "sensitivity": "low", "source_spans": [1], "consent_basis": "explicit_confirmation",
         "expires_at": None},
    ])
    result = extraction_module.extract_memories_for_turn(session, turn_id="t-1")
    assert result["pending_confirmation"] == 1
    assert result["written"] == 0
    row = session.execute(text(
        "SELECT status FROM agent_memories WHERE agent_id = 'ag-1'"
    )).mappings().one()
    assert row["status"] == "pending_confirmation"


def test_extraction_does_not_grant_consent_for_unconfirmed_candidates(session, monkeypatch):
    """A candidate whose consent_basis is 'explicit_confirmation' has not
    actually been consented to yet -- no real agent_memory_consents row
    should be created (and the revision's consent_id must stay NULL) until
    a future P6B-3 confirm action does so for real."""
    from app.services.memory import extraction as extraction_module
    _seed_turn_with_messages(session)
    monkeypatch.setattr(extraction_module, "_call_extractor", lambda *a, **k: [
        {"subject_key": "self", "predicate": "user.preference", "canonical_value": "dark mode",
         "display_text": "User seems to prefer dark mode", "kind": "semantic", "confidence": 0.7,
         "sensitivity": "low", "source_spans": [1], "consent_basis": "explicit_confirmation",
         "expires_at": None},
    ])
    extraction_module.extract_memories_for_turn(session, turn_id="t-1")
    assert session.execute(text("SELECT count(*) FROM agent_memory_consents")).scalar_one() == 0
    revision = session.execute(text(
        "SELECT consent_id FROM agent_memory_revisions"
    )).mappings().one()
    assert revision["consent_id"] is None


def test_extraction_rejects_unknown_predicate(session, monkeypatch):
    from app.services.memory import extraction as extraction_module
    _seed_turn_with_messages(session)
    monkeypatch.setattr(extraction_module, "_call_extractor", lambda *a, **k: [
        {"subject_key": "self", "predicate": "user.ssn", "canonical_value": "123-45-6789",
         "display_text": "SSN", "kind": "semantic", "confidence": 0.9, "sensitivity": "high",
         "source_spans": [0], "consent_basis": "explicit_statement", "expires_at": None},
    ])
    result = extraction_module.extract_memories_for_turn(session, turn_id="t-1")
    assert result["rejected"] == 1
    assert result["written"] == 0
    assert session.execute(text("SELECT count(*) FROM agent_memories")).scalar_one() == 0


def test_extraction_deduplicates_exact_repeat_by_merging_provenance(session, monkeypatch):
    from app.services.memory import extraction as extraction_module
    _seed_turn_with_messages(session, turn_id="t-1")
    candidate = {"subject_key": "self", "predicate": "user.name", "canonical_value": "Alex",
                "display_text": "User's name is Alex", "kind": "semantic", "confidence": 0.80,
                "sensitivity": "low", "source_spans": [0], "consent_basis": "explicit_statement",
                "expires_at": None}
    monkeypatch.setattr(extraction_module, "_call_extractor", lambda *a, **k: [candidate])
    extraction_module.extract_memories_for_turn(session, turn_id="t-1")

    _seed_turn_with_messages(session, turn_id="t-2")
    higher_confidence = {**candidate, "confidence": 0.95}
    monkeypatch.setattr(extraction_module, "_call_extractor", lambda *a, **k: [higher_confidence])
    result = extraction_module.extract_memories_for_turn(session, turn_id="t-2")

    assert result["written"] == 0  # merged into the existing row, not a new one
    rows = session.execute(text(
        "SELECT confidence FROM agent_memories WHERE status = 'active'"
    )).mappings().all()
    assert len(rows) == 1
    assert float(rows[0]["confidence"]) == 0.95  # retained MAXIMUM confidence


def test_extraction_creates_conflict_set_on_different_single_valued_correction(session, monkeypatch):
    from app.services.memory import extraction as extraction_module
    _seed_turn_with_messages(session, turn_id="t-1", user_text="Call me Alex.")
    monkeypatch.setattr(extraction_module, "_call_extractor", lambda *a, **k: [
        {"subject_key": "self", "predicate": "user.name", "canonical_value": "Alex",
         "display_text": "Name is Alex", "kind": "semantic", "confidence": 0.9, "sensitivity": "low",
         "source_spans": [0], "consent_basis": "explicit_statement", "expires_at": None},
    ])
    extraction_module.extract_memories_for_turn(session, turn_id="t-1")

    _seed_turn_with_messages(session, turn_id="t-2", user_text="Actually my name is Alexandra.")
    monkeypatch.setattr(extraction_module, "_call_extractor", lambda *a, **k: [
        {"subject_key": "self", "predicate": "user.name", "canonical_value": "Alexandra",
         "display_text": "Name is Alexandra", "kind": "semantic", "confidence": 0.9, "sensitivity": "low",
         "source_spans": [0], "consent_basis": "explicit_statement", "expires_at": None},
    ])
    result = extraction_module.extract_memories_for_turn(session, turn_id="t-2")
    assert result["conflicts"] == 1
    conflict = session.execute(text(
        "SELECT status FROM agent_memory_conflicts WHERE predicate = 'user.name'"
    )).mappings().one()
    assert conflict["status"] == "open"
    # neither memory is recalled while conflicted
    statuses = {r["status"] for r in session.execute(text(
        "SELECT status FROM agent_memories WHERE predicate = 'user.name'"
    )).mappings().all()}
    assert statuses == {"conflicted"}


def test_extraction_noop_when_long_term_disabled(session, monkeypatch):
    from app.services.memory import extraction as extraction_module
    session.execute(text(
        "UPDATE agent_versions SET memory_settings = '{\"long_term_enabled\": false}'::json WHERE id = 'av-1'"
    ))
    session.commit()
    _seed_turn_with_messages(session)
    called = []
    monkeypatch.setattr(extraction_module, "_call_extractor", lambda *a, **k: called.append(1))
    result = extraction_module.extract_memories_for_turn(session, turn_id="t-1")
    assert called == []
    assert result == {"candidates": 0, "written": 0, "pending_confirmation": 0, "conflicts": 0, "rejected": 0}


def test_extraction_writes_vector_outbox_row_for_each_new_active_memory(session, monkeypatch):
    from app.services.memory import extraction as extraction_module
    _seed_turn_with_messages(session)
    monkeypatch.setattr(extraction_module, "_call_extractor", lambda *a, **k: [
        {"subject_key": "self", "predicate": "user.name", "canonical_value": "Alex",
         "display_text": "Name is Alex", "kind": "semantic", "confidence": 0.9, "sensitivity": "low",
         "source_spans": [0], "consent_basis": "explicit_statement", "expires_at": None},
    ])
    extraction_module.extract_memories_for_turn(session, turn_id="t-1")
    outbox = session.execute(text(
        "SELECT event_type, state FROM agent_memory_vector_outbox"
    )).mappings().all()
    assert len(outbox) == 1
    assert outbox[0]["event_type"] == "upsert"
    assert outbox[0]["state"] == "pending"

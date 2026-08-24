# P6B-2a: Long-Term Agent Memory — Write Path (Extraction, Canonicalization, Consent, Conflict) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** After each Agent Turn's response, extract screened, source-backed candidate facts about the user/session, canonicalize and deduplicate them against SQL-authoritative long-term memory, gate writes behind consent and a closed predicate allowlist, and detect/record conflicting corrections — all without yet making anything recallable. This is the first of two P6B-2 sub-plans (write path → recall path); P6B-2b (a separate future plan) builds the Chroma vector-outbox consumer and the hybrid semantic/lexical recall algorithm that actually reads what this plan writes. Nothing in this plan changes what any live Turn sees — `message_budget.py`'s `recalled_memories` parameter (already wired by P6B-1) stays empty until P6B-2b lands.

**Architecture:** A new migration adds six tables: `agent_memory_predicate_registry` (closed allowlist, single/multi cardinality), `agent_memories` (SQL-authoritative facts, partial-unique on the active dedup key), `agent_memory_revisions` (append-only correction history), `agent_memory_consents` (per-revision consent, revocable), `agent_memory_conflicts` (open conflict sets), and `agent_memory_vector_outbox`/`agent_memory_extraction_outbox` (two independent transactional outboxes — the first written here for P6B-2b to consume later, mirroring the proven `agent_index_outbox` pattern from P4A-INDEX; the second both written AND consumed here, since extraction triggering is this plan's own concern). `agent_turn.py` gains one new row-insert (the extraction-outbox row) in the same fenced transaction that already finalizes a successful Turn — extraction itself runs asynchronously afterward via a periodic Celery sweep, exactly mirroring P6B-1's summary-regeneration sweep, never on the Turn's critical path.

**Tech Stack:** FastAPI + SQLAlchemy Core, Alembic, Celery (beat + worker), the same `resolve_llm_caller_by_version` + `chat_completion` pair P6B-1's summary service already uses (no new LLM-invocation pattern).

**Spec:** `docs/superpowers/plans/2026-08-09-agent-ontology-implementation.md` — section "## 11. Memory," specifically the long-term/canonicalizer paragraphs (lines 623-625) and the P6B row in section 13.1 (line 861, stable errors `MEMORY_CONSENT_REQUIRED`, `MEMORY_CONFLICT`, `MEMORY_CARDINALITY_EXCEEDED`, `MEMORY_POLICY_REJECTED`). Also grounded in this session's own P6B-1 plan/implementation (`docs/superpowers/plans/2026-08-23-p6b1-short-term-memory.md`, merged onto `dev` at commit `32c4bd6`) for the exact integration surface this plan must produce for: `backend/app/services/runtime/message_budget.py`'s `recalled_memories: list[str]` parameter (P6B-2b's future consumer, this plan produces the rows it will eventually read) and `backend/app/services/agent/memory_settings.py`'s already-validated-but-inert `long_term_enabled`/`recall_token_budget`/`recall_count` keys (this plan's extraction gate reads `long_term_enabled`; the other two stay P6B-2b's concern).

## Global Constraints

- Migration for this plan is `0019_agent_memory_long_term`, `down_revision = "0018_agent_memory_short_term"` — verify this is still the actual head before creating the file (`ls backend/alembic/versions | sort | tail -3`), since other work may have landed on `dev` since this plan was written.
- No new API routes in this plan — this is service-layer only (extraction, canonicalization, consent, conflict). The user-facing inspect/correct/delete/clear API (Section 12's `Memory (post-MVP P6B)` row) and its UI are P6B-3's scope, a separate future plan. Skip the `openapi-agent.json` regeneration step.
- Business FKs in this schema are RESTRICT, never CASCADE, matching every table in this codebase (`agent_sessions`, `agent_messages`, P6B-1's `agent_memory_summaries`) — every new FK in this plan follows the same convention.
- Predicate allowlist is a real DB table (`agent_memory_predicate_registry`), not a Python constant — the spec's "Predicate registry versions declare single or multi cardinality" language implies versioned, queryable rows, and this plan's dedup/cardinality-cap logic needs to `SELECT` cardinality by predicate at write time. It is seeded with a small, explicitly-labeled starter set in the migration (not exhaustive — extending it later is a data change, not a schema change, matching how `TABLE_MINIMUMS` in `backend/app/services/retention/policy.py` is a fixed-but-extensible allowlist with no admin API in its own initial plan). No admin CRUD API for the registry is in scope here.
- Extraction triggering is event-driven via a dedicated outbox (`agent_memory_extraction_outbox`), not a periodic full-table scan — every successful Turn writes exactly one outbox row, in the same transaction `finalize_turn_succeeded` already commits in `backend/app/tasks/agent_turn.py`. This differs from P6B-1's summary sweep (which scans all active sessions every 60s) because extraction is naturally a per-Turn event, not a threshold-crossing condition; scanning per-Turn outbox rows is both more precise and cheaper than re-deriving "which Turns need extraction" from scratch every sweep.
- Extraction, like P6B-1's summary regeneration, is asynchronous and best-effort — a failed or skipped extraction never fails a Turn, and one Turn's extraction failure must not block another's (per-row error isolation in the sweep, mirroring P6B-1 Task 6's `sweep_memory_summaries`).
- `long_term_enabled` (already validated by P6B-1's `validate_memory_settings`, currently inert) gates extraction: when `False`, the sweep must not call the model or write any memory row for that Agent's Turns, and any pending unconfirmed candidates must not be promoted. Disabling memory "disables... long-term read/write" per spec — this plan owns the write half of that contract.
- Golden-fixture rigor for the canonicalizer: per spec, "Golden fixtures cover every normalization type, locale-sensitive text, collision, cardinality, correction, and conflict" — this plan's canonicalizer task must include a fixture-style test for each normalization rule listed in the spec paragraph (NFKC, whitespace collapse, case-folding, boolean/null encoding, number normalization, timestamp conversion, key sorting, list-order preservation, NaN/infinity/mixed-type-set rejection), not just a couple of examples.

---

### Task 1: Migration + ORM models for all six tables

**Files:**
- Create: `backend/alembic/versions/0019_agent_memory_long_term.py`
- Modify: `backend/app/models/agent_runtime.py` (or a new `backend/app/models/agent_memory.py` if `agent_runtime.py` is already large enough that a split is warranted — check its current line count first; if it's grown past ~600 lines, create the new file and register it the same way P6B-1's `AgentMemorySummary` was registered in `backend/app/models/__init__.py`)
- Test: `backend/tests/agent/test_agent_memory_long_term.py` (new file, holds every test for this whole plan — later tasks append to it)

**Interfaces:**
- Produces six tables:
  - `agent_memory_predicate_registry`: `id (pk), predicate (varchar, unique), cardinality (varchar, check IN ('single','multi')), created_at`. Seeded rows (migration `INSERT`, not a later data task): `('user.name','single')`, `('user.role','single')`, `('user.preference','multi')`, `('user.fact','multi')`, `('user.goal','multi')` — five starter predicates, three multi/two single, enough for later tasks' tests to exercise both cardinalities without inventing a real-world taxonomy this plan doesn't own.
  - `agent_memories`: `id (pk), security_domain_id (fk), agent_id (fk agents.id RESTRICT), user_id (fk users.id RESTRICT), kind (varchar check IN ('semantic','episodic')), subject_key (varchar), predicate (fk predicate_registry.predicate RESTRICT), canonical_value (jsonb), canonical_value_hash (varchar(64)), display_text (text), confidence (numeric, check 0<=x<=1), sensitivity (varchar), consent_basis (varchar check IN ('explicit_statement','explicit_confirmation')), source_spans (jsonb), status (varchar check IN ('pending_confirmation','active','conflicted','deleted')), embedding_model_version (varchar, nullable — P6B-2b sets this), expires_at (timestamptz, nullable), created_at, updated_at, deleted_at (timestamptz, nullable)`. Partial unique index: `CREATE UNIQUE INDEX ... ON agent_memories (security_domain_id, agent_id, user_id, subject_key, predicate, canonical_value_hash) WHERE status = 'active'` (Postgres partial index — the spec's "unique active" constraint, allowing multiple historical/deleted/conflicted rows to share a dedup key while at most one active one exists).
  - `agent_memory_revisions`: `id (pk), memory_id (fk agent_memories.id RESTRICT), revision_no (int), canonical_value (jsonb), display_text (text), confidence (numeric), consent_basis (varchar), source_spans (jsonb), consent_id (fk agent_memory_consents.id RESTRICT, nullable — set once Task 4 exists, nullable at the DB level so this migration doesn't need to know consent-table insert order beyond FK existence), created_by (varchar), created_at, superseded_at (timestamptz, nullable)`. Unique `(memory_id, revision_no)`.
  - `agent_memory_consents`: `id (pk), security_domain_id (fk), agent_id (fk), user_id (fk), consent_basis (varchar check IN ('explicit_statement','explicit_confirmation')), granted_at (timestamptz), revoked_at (timestamptz, nullable)`.
  - `agent_memory_conflicts`: `id (pk), security_domain_id (fk), agent_id (fk), user_id (fk), subject_key (varchar), predicate (fk predicate_registry.predicate RESTRICT), memory_id_a (fk agent_memories.id RESTRICT), memory_id_b (fk agent_memories.id RESTRICT), status (varchar check IN ('open','resolved')), resolved_by_revision_id (fk agent_memory_revisions.id RESTRICT, nullable), created_at, resolved_at (timestamptz, nullable)`.
  - `agent_memory_vector_outbox`: `id (pk), memory_id (fk agent_memories.id RESTRICT), event_type (varchar check IN ('upsert','delete')), state (varchar check IN ('pending','applied'), default 'pending'), created_at`. Index on `state`. This table's ROWS are written by this plan's extraction/consent/deletion services; CONSUMING it (embedding generation, Chroma upsert) is entirely P6B-2b's job — do not implement a consumer here.
  - `agent_memory_extraction_outbox`: `id (pk), turn_id (fk agent_turns.id RESTRICT), session_id (fk agent_sessions.id RESTRICT), state (varchar check IN ('pending','processing','applied','skipped'), default 'pending'), created_at, processed_at (timestamptz, nullable)`. Index on `state`.

- [ ] **Step 1: Write the failing migration tests**

Create `backend/tests/agent/test_agent_memory_long_term.py`. Follow the exact fixture pattern P6B-1 established in `test_agent_memory_short_term.py` (ephemeral `CREATE SCHEMA` per test, `run_migrations.py upgrade <head>`, seed `users`/`model_configs`/`model_config_versions`/`agents`/`agent_versions`/`agent_sessions` the same way) — read that file first and copy its `session` fixture verbatim, updating only the target migration (`HEAD = "0019_agent_memory_long_term"`) and the schema-name prefix (`"p6b2a_"`).

```python
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
        "default_model_name, system_prompt, application_state_schema_version_id, config_hash, created_by, created_at) "
        "VALUES ('av-1', 'ag-1', 1, 'test-version', 'mcv-1', 'test-model', '', :svid, 'h', 'u-1', now())"
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && TEST_DATABASE_URL=postgresql://ontoprompt:ontoprompt@localhost:5432/ontoprompt pytest tests/agent/test_agent_memory_long_term.py -v`
Expected: FAIL — migration `0019_agent_memory_long_term` doesn't exist yet.

- [ ] **Step 3: Write the migration**

First confirm the real current head: `ls backend/alembic/versions | sort | tail -3` — if it's still `0018_agent_memory_short_term`, proceed; otherwise use the actual head as `down_revision` and note the substitution in your task report.

Create `backend/alembic/versions/0019_agent_memory_long_term.py` with all six tables per the Interfaces block above. Use `sa.Column`/`sa.ForeignKey(..., ondelete="RESTRICT")`/`sa.CheckConstraint` throughout, matching the style of `backend/alembic/versions/0018_agent_memory_short_term.py` and `backend/alembic/versions/0011_retention_governance.py` (both already in this codebase — read one for exact `op.create_table`/`op.create_index` syntax conventions before writing). For the partial unique index on `agent_memories`, use `op.create_index(..., unique=True, postgresql_where=sa.text("status = 'active'"))` (Alembic's supported way to emit a partial unique index) rather than a bare `UniqueConstraint`, since Postgres partial indexes aren't expressible as a plain SQLAlchemy `UniqueConstraint`.

Seed the five predicate rows via `op.bulk_insert` or a plain `op.execute("INSERT INTO agent_memory_predicate_registry (id, predicate, cardinality, created_at) VALUES ...")` inside `upgrade()`, after the table is created.

Write a real `downgrade()` that drops all six tables in FK-safe reverse order (revisions/conflicts/vector_outbox/extraction_outbox before memories, memories before predicate_registry) — mirror `0018`'s downgrade structure.

- [ ] **Step 4: Add ORM models**

Add all six models (matching the migration's columns exactly) to `backend/app/models/agent_runtime.py` if it's still reasonably sized, or a new `backend/app/models/agent_memory.py` if not (check line count first — if you create a new file, register it in `backend/app/models/__init__.py`'s `load_all_models()` the same way `AgentMemorySummary` was registered there for P6B-1; read that function first to see the exact pattern rather than guessing). Match this codebase's existing `Mapped[...]`/`mapped_column(...)` style exactly (reuse the `_new_id`/`_now` helpers already defined in whichever file you're editing — don't redefine them).

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd backend && TEST_DATABASE_URL=postgresql://ontoprompt:ontoprompt@localhost:5432/ontoprompt pytest tests/agent/test_agent_memory_long_term.py -v`
Expected: all 5 tests pass.

- [ ] **Step 6: Commit**

```bash
git add backend/alembic/versions/0019_agent_memory_long_term.py backend/app/models/ \
  backend/tests/agent/test_agent_memory_long_term.py
git commit -m "feat: add long-term Agent memory schema (predicate registry, memories, revisions, consent, conflicts, outboxes)"
```

---

### Task 2: Canonicalizer (`memory-c14n-v1`)

**Files:**
- Create: `backend/app/services/memory/canonicalizer.py`
- Test: `backend/tests/agent/test_agent_memory_long_term.py` (append)

**Interfaces:**
- Produces: `CANONICALIZER_VERSION = "memory-c14n-v1"` (module constant) and `canonicalize(value: Any) -> Any` (recursively normalizes a JSON-compatible value per every rule below, raising `CanonicalizationError` for NaN/infinity/mixed-type-set/unsupported-object input) plus `canonical_hash(value: Any, value_type: str) -> str` (SHA-256 over the canonicalizer version + value type + the canonicalized value's deterministic JSON serialization). Task 5's extraction service calls both; Task 1's `agent_memories.canonical_value_hash` column is populated from `canonical_hash`'s output.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/agent/test_agent_memory_long_term.py` — one test per normalization rule the spec paragraph lists, plus the hash function:

```python
def test_canonicalize_nfkc_normalizes_unicode():
    from app.services.memory.canonicalizer import canonicalize
    # U+FB01 LATIN SMALL LIGATURE FI -> "fi" under NFKC
    assert canonicalize("ﬁle") == "file"


def test_canonicalize_trims_and_collapses_whitespace():
    from app.services.memory.canonicalizer import canonicalize
    assert canonicalize("  hello   world    ") == "hello world"


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
    assert canonicalize({"b": 1, "a": 2}) == {"a": 2, "b": 1}


def test_canonicalize_preserves_list_order_by_default():
    from app.services.memory.canonicalizer import canonicalize
    assert canonicalize([3, 1, 2]) == [3, 1, 2]


def test_canonicalize_sorts_set_semantics_lists():
    from app.services.memory.canonicalizer import canonicalize, SetSemantics
    assert canonicalize(SetSemantics([3, 1, 2])) == [1, 2, 3]


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
```

Run: `cd backend && pytest tests/agent/test_agent_memory_long_term.py -v -k canonicaliz`
Expected: FAIL — module doesn't exist.

- [ ] **Step 2: Implement the canonicalizer**

Create `backend/app/services/memory/canonicalizer.py`:

```python
"""Memory canonicalizer version memory-c14n-v1 (P6B-2a, Section 11).

Deterministic normalization so two differently-worded-but-equal facts
produce the same dedup hash. Every rule in this module corresponds to one
sentence in the spec's canonicalizer paragraph — see the docstring on each
function for its exact source rule.
"""
from __future__ import annotations

import hashlib
import json
import math
import unicodedata
from datetime import datetime, timezone
from decimal import Decimal

CANONICALIZER_VERSION = "memory-c14n-v1"


class CanonicalizationError(Exception):
    """Value cannot be canonicalized (NaN/infinity/mixed-type set/unsupported object)."""


class CaseSensitive(str):
    """Wrap a string to skip case-folding — schema-declared case-sensitive values."""


class SetSemantics(list):
    """Wrap a list to declare set semantics — sorted, not order-preserved."""


def _normalize_number(value) -> str:
    if isinstance(value, bool):
        raise CanonicalizationError("booleans are not numbers")
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            raise CanonicalizationError("NaN/infinity are not canonicalizable")
        value = Decimal(str(value))
    if isinstance(value, Decimal):
        text = format(value.normalize(), "f")
        if "." in text:
            text = text.rstrip("0").rstrip(".")
        return text or "0"
    raise CanonicalizationError(f"unsupported numeric type {type(value)}")


def canonicalize(value):
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, CaseSensitive):
        return str(value)
    if isinstance(value, str):
        text = unicodedata.normalize("NFKC", value)
        text = " ".join(text.split())  # collapses all Unicode whitespace, trims ends
        return text.casefold()
    if isinstance(value, (int, float, Decimal)):
        return _normalize_number(value)
    if isinstance(value, datetime):
        dt = value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return dt.isoformat(timespec="microseconds").replace("+00:00", "Z")
    if isinstance(value, SetSemantics):
        items = [canonicalize(v) for v in value]
        types = {type(v) for v in items}
        if len(types) > 1:
            raise CanonicalizationError("mixed-type sets are not canonicalizable")
        return sorted(items)
    if isinstance(value, list):
        return [canonicalize(v) for v in value]
    if isinstance(value, dict):
        return {k: canonicalize(value[k]) for k in sorted(value)}
    raise CanonicalizationError(f"unsupported type {type(value)}")


def canonical_hash(value, value_type: str) -> str:
    canonical = canonicalize(value)
    payload = json.dumps(
        {"canonicalizer_version": CANONICALIZER_VERSION, "value_type": value_type, "value": canonical},
        sort_keys=True, ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode()).hexdigest()
```

Verify the timestamp branch actually produces `2026-08-24T10:30:00.500000Z` for the test's input before moving on — `datetime.isoformat(timespec="microseconds")` on a UTC-aware value plus the `+00:00`→`Z` replace should already match exactly (independently confirmed while writing this plan).

- [ ] **Step 3: Run the tests to verify they pass**

Run: `cd backend && pytest tests/agent/test_agent_memory_long_term.py -v -k canonicaliz`
Expected: all pass.

- [ ] **Step 4: Commit**

```bash
git add backend/app/services/memory/canonicalizer.py backend/tests/agent/test_agent_memory_long_term.py
git commit -m "feat: add memory-c14n-v1 canonicalizer"
```

---

### Task 3: Predicate registry + cardinality enforcement

**Files:**
- Create: `backend/app/services/memory/predicate_registry.py`
- Test: `backend/tests/agent/test_agent_memory_long_term.py` (append)

**Interfaces:**
- Consumes: `agent_memory_predicate_registry` (Task 1).
- Produces: `PredicateRegistryError(Exception)` (raised as `MEMORY_POLICY_REJECTED` for an unknown predicate, `MEMORY_CARDINALITY_EXCEEDED` for a multi-valued predicate at its 10-active-item cap), `lookup_predicate(db, predicate: str) -> dict | None` (returns `{"predicate": str, "cardinality": "single"|"multi"}` or `None`), `check_cardinality(db, *, security_domain_id, agent_id, user_id, subject_key, predicate) -> None` (raises `PredicateRegistryError("MEMORY_CARDINALITY_EXCEEDED")` if inserting one more active multi-valued row for this key would exceed 10; single-cardinality predicates are governed by the Task 1 partial-unique index instead, not this function — a second single-valued write for the same key is a conflict, not a cardinality violation, and Task 6 handles that distinction). Task 5's extraction service calls both before every write.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/agent/test_agent_memory_long_term.py`:

```python
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
```

Run: `cd backend && TEST_DATABASE_URL=postgresql://ontoprompt:ontoprompt@localhost:5432/ontoprompt pytest tests/agent/test_agent_memory_long_term.py -v -k "predicate or cardinality"`
Expected: FAIL — module doesn't exist.

- [ ] **Step 2: Implement the registry module**

Create `backend/app/services/memory/predicate_registry.py`:

```python
"""Closed predicate allowlist + multi-value cardinality cap (P6B-2a,
Section 11: "Predicate registry versions declare single or multi
cardinality; unknown predicates are rejected, and multi-valued defaults cap
at 10 active values")."""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session

MULTI_VALUE_CAP = 10


class PredicateRegistryError(Exception):
    """MEMORY_POLICY_REJECTED (unknown predicate) or MEMORY_CARDINALITY_EXCEEDED (multi-value cap)."""


def lookup_predicate(db: Session, predicate: str) -> dict | None:
    row = db.execute(text(
        "SELECT predicate, cardinality FROM agent_memory_predicate_registry WHERE predicate = :p"
    ), {"p": predicate}).mappings().one_or_none()
    return dict(row) if row else None


def check_cardinality(db: Session, *, security_domain_id: str, agent_id: str, user_id: str,
                      subject_key: str, predicate: str) -> None:
    count = db.execute(text(
        "SELECT count(*) FROM agent_memories WHERE security_domain_id = :d AND agent_id = :a "
        "AND user_id = :u AND subject_key = :sk AND predicate = :pred AND status = 'active'"
    ), {"d": security_domain_id, "a": agent_id, "u": user_id, "sk": subject_key, "pred": predicate}).scalar_one()
    if count >= MULTI_VALUE_CAP:
        raise PredicateRegistryError(f"MEMORY_CARDINALITY_EXCEEDED: {predicate} at cap ({MULTI_VALUE_CAP})")
```

- [ ] **Step 3: Run the tests to verify they pass**

Run: `cd backend && TEST_DATABASE_URL=postgresql://ontoprompt:ontoprompt@localhost:5432/ontoprompt pytest tests/agent/test_agent_memory_long_term.py -v -k "predicate or cardinality"`
Expected: all pass.

- [ ] **Step 4: Commit**

```bash
git add backend/app/services/memory/predicate_registry.py backend/tests/agent/test_agent_memory_long_term.py
git commit -m "feat: add memory predicate registry lookup and cardinality cap"
```

---

### Task 4: Consent service

**Files:**
- Create: `backend/app/services/memory/consent.py`
- Test: `backend/tests/agent/test_agent_memory_long_term.py` (append)

**Interfaces:**
- Produces: `grant_consent(db, *, security_domain_id, agent_id, user_id, consent_basis: str) -> str` (inserts a new `agent_memory_consents` row, returns its id), `revoke_consent(db, *, consent_id: str) -> int` (sets `revoked_at`, then tombstones every `agent_memories` row whose latest revision references this consent — sets `status = 'deleted'`, `deleted_at = now()` — and writes an `agent_memory_vector_outbox` delete-event row for each tombstoned memory; returns the count tombstoned). Task 5's extraction service calls `grant_consent` before writing a memory row and stores the resulting `consent_id` on that memory's first revision (Task 1's `agent_memory_revisions.consent_id` column).

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/agent/test_agent_memory_long_term.py`:

```python
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
```

Run: `cd backend && TEST_DATABASE_URL=postgresql://ontoprompt:ontoprompt@localhost:5432/ontoprompt pytest tests/agent/test_agent_memory_long_term.py -v -k consent`
Expected: FAIL — module doesn't exist.

- [ ] **Step 2: Implement the consent service**

Create `backend/app/services/memory/consent.py`:

```python
"""Per-revision memory consent, revocable (P6B-2a, Section 11: "Consent is
stored per revision and revocation tombstones all memories relying on that
consent basis"). Only a memory's LATEST (non-superseded) revision governs
whether it's currently tombstoned by a given consent revocation -- an
earlier revision's consent basis is historical, not authoritative."""
from __future__ import annotations

import uuid

from sqlalchemy import text
from sqlalchemy.orm import Session


def _new_id() -> str:
    return str(uuid.uuid4())


def grant_consent(db: Session, *, security_domain_id: str, agent_id: str, user_id: str,
                  consent_basis: str) -> str:
    consent_id = _new_id()
    db.execute(text(
        "INSERT INTO agent_memory_consents (id, security_domain_id, agent_id, user_id, consent_basis, granted_at) "
        "VALUES (:id, :d, :a, :u, :basis, now())"
    ), {"id": consent_id, "d": security_domain_id, "a": agent_id, "u": user_id, "basis": consent_basis})
    db.commit()
    return consent_id


def revoke_consent(db: Session, *, consent_id: str) -> int:
    db.execute(text(
        "UPDATE agent_memory_consents SET revoked_at = now() WHERE id = :id AND revoked_at IS NULL"
    ), {"id": consent_id})
    dependent_memory_ids = db.execute(text(
        "SELECT r.memory_id FROM agent_memory_revisions r "
        "WHERE r.consent_id = :cid AND r.superseded_at IS NULL"
    ), {"cid": consent_id}).scalars().all()
    tombstoned = 0
    for memory_id in dependent_memory_ids:
        result = db.execute(text(
            "UPDATE agent_memories SET status = 'deleted', deleted_at = now(), updated_at = now() "
            "WHERE id = :id AND status != 'deleted'"
        ), {"id": memory_id})
        if result.rowcount:
            tombstoned += result.rowcount
            db.execute(text(
                "INSERT INTO agent_memory_vector_outbox (id, memory_id, event_type, state, created_at) "
                "VALUES (:id, :mid, 'delete', 'pending', now())"
            ), {"id": _new_id(), "mid": memory_id})
    db.commit()
    return tombstoned
```

- [ ] **Step 3: Run the tests to verify they pass**

Run: `cd backend && TEST_DATABASE_URL=postgresql://ontoprompt:ontoprompt@localhost:5432/ontoprompt pytest tests/agent/test_agent_memory_long_term.py -v -k consent`
Expected: all pass.

- [ ] **Step 4: Commit**

```bash
git add backend/app/services/memory/consent.py backend/tests/agent/test_agent_memory_long_term.py
git commit -m "feat: add per-revision memory consent grant/revoke with tombstone cascade"
```

---

### Task 5: Extraction service

**Files:**
- Create: `backend/app/services/memory/extraction.py`
- Test: `backend/tests/agent/test_agent_memory_long_term.py` (append)

**Interfaces:**
- Consumes: `canonicalize`/`canonical_hash` (Task 2), `lookup_predicate`/`check_cardinality` (Task 3), `grant_consent` (Task 4), `resolve_llm_caller_by_version` (`backend/app/services/model_callers/extraction.py:65`, already used by P6B-1's summary service), `chat_completion`/`_parse_response` (`backend/app/services/llm_service.py`, same reuse pattern P6B-1's final fix wave already established for hardened JSON parsing).
- Produces: `extract_memories_for_turn(db: Session, *, turn_id: str) -> dict` (returns `{"candidates": int, "written": int, "pending_confirmation": int, "conflicts": int, "rejected": int}`). Task 7's periodic sweep calls this per outbox row.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/agent/test_agent_memory_long_term.py`:

```python
def _seed_turn_with_messages(session, turn_id="t-1", user_text="Please call me Alex.", assistant_text="Got it, Alex!"):
    session.execute(text(
        "INSERT INTO agent_turns (id, session_id, status, created_at, updated_at) "
        "VALUES (:id, 'sess-1', 'succeeded', now(), now())"
    ), {"id": turn_id})
    session.execute(text(
        "INSERT INTO agent_messages (id, session_id, turn_id, role, ordinal, content, created_at) "
        "VALUES (:id1, 'sess-1', :turn, 'user', 0, :u, now()), "
        "(:id2, 'sess-1', :turn, 'assistant', 1, :a, now())"
    ), {"id1": f"{turn_id}-u", "id2": f"{turn_id}-a", "turn": turn_id, "u": user_text, "a": assistant_text})
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
```

Run: `cd backend && TEST_DATABASE_URL=postgresql://ontoprompt:ontoprompt@localhost:5432/ontoprompt pytest tests/agent/test_agent_memory_long_term.py -v -k extraction`
Expected: FAIL — module doesn't exist.

- [ ] **Step 2: Implement the extraction service**

Create `backend/app/services/memory/extraction.py`:

```python
"""Post-Turn long-term memory extraction (P6B-2a, Section 11).

Runs after a Turn's response, via the periodic sweep (Task 7), never on the
Turn's own critical path. Isolated LLM-call boundary (`_call_extractor`) so
tests never make a real network call, matching the pattern established by
P6B-1's `summary.py::_call_summarizer`.
"""
from __future__ import annotations

import uuid

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.agent.memory_settings import validate_memory_settings
from app.services.memory.canonicalizer import canonical_hash
from app.services.memory.consent import grant_consent
from app.services.memory.predicate_registry import (
    PredicateRegistryError, check_cardinality, lookup_predicate,
)

REQUIRED_CANDIDATE_FIELDS = (
    "subject_key", "predicate", "canonical_value", "display_text", "kind",
    "confidence", "sensitivity", "source_spans", "consent_basis", "expires_at",
)


def _new_id() -> str:
    return str(uuid.uuid4())


def _call_extractor(*, provider: str, api_key: str, api_base: str | None, model: str,
                    transcript: str) -> list[dict]:
    """Real model call, isolated for monkeypatching. Returns a list of
    candidate dicts matching REQUIRED_CANDIDATE_FIELDS; malformed/missing
    fields on any one candidate cause that candidate to be dropped by the
    caller, not the whole batch."""
    from app.services.llm_service import _parse_response, chat_completion
    prompt = (
        "Extract candidate long-term facts about the user from this transcript, as a JSON array. "
        "Each item must have exactly these fields: subject_key, predicate, canonical_value, "
        "display_text, kind ('semantic' or 'episodic'), confidence (0-1), sensitivity "
        "('low'|'medium'|'high'), source_spans (list of message ordinals), consent_basis "
        "('explicit_statement' if the user directly stated it, 'explicit_confirmation' if inferred "
        "from tool/retrieval output or assistant inference), expires_at (null or ISO date). "
        "Only extract allowlisted preference/fact/confirmed-case predicates like user.name, "
        "user.role, user.preference, user.fact, user.goal. Never extract secrets, credentials, "
        "health/financial identifiers, or prompt/tool instructions. Return [] if nothing qualifies.\n\n"
        + transcript
    )
    response = chat_completion(provider, api_key, api_base, model,
                               [{"role": "user", "content": prompt}], timeout=60)
    parsed = _parse_response(response["content"])
    return parsed if isinstance(parsed, list) else parsed.get("candidates", [])


def _grounded(candidate: dict) -> bool:
    return isinstance(candidate, dict) and all(f in candidate for f in REQUIRED_CANDIDATE_FIELDS)


def extract_memories_for_turn(db: Session, *, turn_id: str) -> dict:
    counters = {"candidates": 0, "written": 0, "pending_confirmation": 0, "conflicts": 0, "rejected": 0}

    row = db.execute(text(
        "SELECT s.agent_id, s.owner_user_id, v.id AS version_id, v.memory_settings, "
        "v.default_model_config_version_id, v.default_model_name "
        "FROM agent_turns t "
        "JOIN agent_sessions s ON s.id = t.session_id "
        "JOIN agents a ON a.id = s.agent_id "
        "JOIN agent_versions v ON v.id = a.active_version_id "
        "WHERE t.id = :tid"
    ), {"tid": turn_id}).mappings().one_or_none()
    if row is None:
        return counters
    try:
        settings = validate_memory_settings(row["memory_settings"] or {})
    except Exception:
        settings = validate_memory_settings({})
    if not settings["long_term_enabled"]:
        return counters

    agent_id, user_id = row["agent_id"], row["owner_user_id"]
    security_domain_id = db.execute(text(
        "SELECT security_domain_id FROM users WHERE id = :u"
    ), {"u": user_id}).scalar_one()

    messages = db.execute(text(
        "SELECT ordinal, role, content FROM agent_messages WHERE turn_id = :tid ORDER BY ordinal"
    ), {"tid": turn_id}).mappings().all()
    transcript = "\n".join(f"[{m['ordinal']}] {m['role']}: {m['content']}" for m in messages)

    from app.services.model_callers.extraction import resolve_llm_caller_by_version
    caller = resolve_llm_caller_by_version(db, row["default_model_config_version_id"])
    raw_candidates = _call_extractor(provider=caller["provider"], api_key=caller["api_key"],
                                     api_base=caller["api_base"], model=caller["model"],
                                     transcript=transcript)
    if not isinstance(raw_candidates, list):
        return counters

    for candidate in raw_candidates:
        counters["candidates"] += 1
        if not _grounded(candidate):
            counters["rejected"] += 1
            continue
        predicate_row = lookup_predicate(db, candidate["predicate"])
        if predicate_row is None:
            counters["rejected"] += 1
            continue

        subject_key = candidate["subject_key"]
        value_hash = canonical_hash(candidate["canonical_value"], "candidate_value")

        existing = db.execute(text(
            "SELECT id, confidence FROM agent_memories WHERE security_domain_id = :d AND agent_id = :a "
            "AND user_id = :u AND subject_key = :sk AND predicate = :pred AND status = 'active'"
        ), {"d": security_domain_id, "a": agent_id, "u": user_id, "sk": subject_key,
            "pred": candidate["predicate"]}).mappings().all()

        exact_match = next((e for e in existing if _same_hash(db, e["id"], value_hash)), None)
        if exact_match is not None:
            # exact duplicate: merge provenance, retain MAXIMUM confidence
            db.execute(text(
                "UPDATE agent_memories SET confidence = GREATEST(confidence, :conf), updated_at = now() "
                "WHERE id = :id"
            ), {"conf": candidate["confidence"], "id": exact_match["id"]})
            db.commit()
            continue

        if predicate_row["cardinality"] == "single" and existing:
            # different single-valued value -> conflict set, neither recalled until resolved
            _open_conflict(db, security_domain_id=security_domain_id, agent_id=agent_id, user_id=user_id,
                           subject_key=subject_key, predicate=candidate["predicate"],
                           existing_memory_id=existing[0]["id"], candidate=candidate, value_hash=value_hash)
            counters["conflicts"] += 1
            continue

        if predicate_row["cardinality"] == "multi":
            try:
                check_cardinality(db, security_domain_id=security_domain_id, agent_id=agent_id,
                                  user_id=user_id, subject_key=subject_key, predicate=candidate["predicate"])
            except PredicateRegistryError:
                counters["rejected"] += 1
                continue

        status = "active" if candidate["consent_basis"] == "explicit_statement" else "pending_confirmation"
        memory_id = _write_memory(db, security_domain_id=security_domain_id, agent_id=agent_id,
                                  user_id=user_id, candidate=candidate, value_hash=value_hash, status=status)
        if status == "active":
            counters["written"] += 1
            db.execute(text(
                "INSERT INTO agent_memory_vector_outbox (id, memory_id, event_type, state, created_at) "
                "VALUES (:id, :mid, 'upsert', 'pending', now())"
            ), {"id": _new_id(), "mid": memory_id})
        else:
            counters["pending_confirmation"] += 1
        db.commit()

    return counters


def _same_hash(db: Session, memory_id: str, candidate_hash: str) -> bool:
    stored = db.execute(text(
        "SELECT canonical_value_hash FROM agent_memories WHERE id = :id"
    ), {"id": memory_id}).scalar_one()
    return stored == candidate_hash


def _write_memory(db: Session, *, security_domain_id: str, agent_id: str, user_id: str,
                  candidate: dict, value_hash: str, status: str) -> str:
    import json
    # A candidate whose consent_basis is 'explicit_confirmation' has NOT
    # actually been consented to yet -- that value on the row is the
    # INTENDED basis once a real user confirms it, not evidence a consent
    # event already happened (this is independent of the resulting `status`:
    # such a candidate is always 'pending_confirmation' today, but even a
    # conflicted row must not be granted consent it was never given).
    # Granting a real, active agent_memory_consents row now would be false.
    # Leave consent_id NULL (the FK is nullable exactly for this reason);
    # P6B-3's confirm-candidate action is responsible for calling
    # grant_consent() for real at the moment the user actually confirms,
    # and updating this revision (or inserting revision 2) with the
    # resulting consent_id. An 'explicit_statement' candidate DID receive
    # real, immediate consent regardless of whether it ends up active or
    # conflicted -- only recall is gated by conflict status, not consent.
    consent_id = None
    if candidate["consent_basis"] == "explicit_statement":
        consent_id = grant_consent(db, security_domain_id=security_domain_id, agent_id=agent_id,
                                   user_id=user_id, consent_basis=candidate["consent_basis"])
    memory_id = _new_id()
    db.execute(text(
        "INSERT INTO agent_memories (id, security_domain_id, agent_id, user_id, kind, subject_key, "
        "predicate, canonical_value, canonical_value_hash, display_text, confidence, sensitivity, "
        "consent_basis, source_spans, status, expires_at, created_at, updated_at) "
        "VALUES (:id, :d, :a, :u, :kind, :sk, :pred, CAST(:val AS jsonb), :hash, :disp, :conf, :sens, "
        ":consent_basis, CAST(:spans AS jsonb), :status, :expires, now(), now())"
    ), {"id": memory_id, "d": security_domain_id, "a": agent_id, "u": user_id, "kind": candidate["kind"],
        "sk": candidate["subject_key"], "pred": candidate["predicate"],
        "val": json.dumps(candidate["canonical_value"]), "hash": value_hash,
        "disp": candidate["display_text"], "conf": candidate["confidence"], "sens": candidate["sensitivity"],
        "consent_basis": candidate["consent_basis"], "spans": json.dumps(candidate["source_spans"]),
        "status": status, "expires": candidate["expires_at"]})
    db.execute(text(
        "INSERT INTO agent_memory_revisions (id, memory_id, revision_no, canonical_value, display_text, "
        "confidence, consent_basis, source_spans, consent_id, created_by, created_at) "
        "VALUES (:id, :mid, 1, CAST(:val AS jsonb), :disp, :conf, :consent_basis, CAST(:spans AS jsonb), "
        ":cid, :user, now())"
    ), {"id": _new_id(), "mid": memory_id, "val": json.dumps(candidate["canonical_value"]),
        "disp": candidate["display_text"], "conf": candidate["confidence"],
        "consent_basis": candidate["consent_basis"], "spans": json.dumps(candidate["source_spans"]),
        "cid": consent_id, "user": user_id})
    return memory_id


def _open_conflict(db: Session, *, security_domain_id: str, agent_id: str, user_id: str,
                   subject_key: str, predicate: str, existing_memory_id: str, candidate: dict,
                   value_hash: str) -> None:
    new_memory_id = _write_memory(db, security_domain_id=security_domain_id, agent_id=agent_id,
                                  user_id=user_id, candidate=candidate, value_hash=value_hash,
                                  status="conflicted")
    db.execute(text(
        "UPDATE agent_memories SET status = 'conflicted', updated_at = now() WHERE id = :id"
    ), {"id": existing_memory_id})
    db.execute(text(
        "INSERT INTO agent_memory_conflicts (id, security_domain_id, agent_id, user_id, subject_key, "
        "predicate, memory_id_a, memory_id_b, status, created_at) "
        "VALUES (:id, :d, :a, :u, :sk, :pred, :ma, :mb, 'open', now())"
    ), {"id": _new_id(), "d": security_domain_id, "a": agent_id, "u": user_id, "sk": subject_key,
        "pred": predicate, "ma": existing_memory_id, "mb": new_memory_id})
    db.commit()
```

Note: the spec says "an explicit newer user correction supersedes the old revision; otherwise neither is recalled until resolved." This task implements the "otherwise" branch fully (both memories move to `conflicted`, a conflict row opens, neither is `active` so P6B-2b's recall — which only ever selects `status = 'active'` rows — naturally excludes both). Resolving a conflict via an explicit newer correction (the other branch) is a **correction/resolution** operation on an *existing* conflict, not something extraction itself does on first contact — that belongs with the inspect/correct/delete API surface in P6B-3, which will have a `resolve_conflict(...)` function operating on the `agent_memory_conflicts` row this task creates. Do not implement conflict resolution in this task; the two-row-conflicted-state is the complete, correct end state for P6B-2a's scope.

- [ ] **Step 3: Run the tests to verify they pass**

Run: `cd backend && TEST_DATABASE_URL=postgresql://ontoprompt:ontoprompt@localhost:5432/ontoprompt pytest tests/agent/test_agent_memory_long_term.py -v -k extraction`
Expected: all pass.

- [ ] **Step 4: Commit**

```bash
git add backend/app/services/memory/extraction.py backend/tests/agent/test_agent_memory_long_term.py
git commit -m "feat: add post-Turn long-term memory extraction service"
```

---

### Task 6: Turn-hook + periodic sweep

**Files:**
- Modify: `backend/app/tasks/agent_turn.py` (insert one extraction-outbox row in the existing finalize transaction)
- Create: `backend/app/tasks/agent_memory_extraction.py`
- Modify: `backend/app/tasks/celery_app.py` (register the new task module + beat schedule entry)
- Test: `backend/tests/agent/test_agent_memory_long_term.py` (append)

**Interfaces:**
- Consumes: `extract_memories_for_turn` (Task 5).
- Produces: Celery task `agent.memory_extraction_sweep`, registered in `celery_app`'s `include` list and `beat_schedule` (interval 60s, same cadence as P6B-1's summary sweep — extraction and summary are independent concerns and can run on the same cadence without coordinating).

- [ ] **Step 1: Write the failing tests**

Read `backend/app/tasks/agent_turn.py` in full first (re-verify the exact current line numbers around `finalize_turn_succeeded(...)` / `db.commit()` — P6B-1 did not touch this file, but confirm before assuming the citation below is still accurate).

Append to `backend/tests/agent/test_agent_memory_long_term.py`:

```python
def test_sweep_processes_pending_outbox_rows_and_isolates_per_row_errors(session, monkeypatch):
    from app.services.memory import extraction as extraction_module
    _seed_turn_with_messages(session, turn_id="t-1")
    _seed_turn_with_messages(session, turn_id="t-2")
    session.execute(text(
        "INSERT INTO agent_memory_extraction_outbox (id, turn_id, session_id, state, created_at) "
        "VALUES ('eo-1', 't-1', 'sess-1', 'pending', now()), ('eo-2', 't-2', 'sess-1', 'pending', now())"
    ))
    session.commit()

    calls = []

    def fake_extract(db, *, turn_id):
        calls.append(turn_id)
        if turn_id == "t-1":
            raise RuntimeError("simulated extraction failure")
        return {"candidates": 0, "written": 0, "pending_confirmation": 0, "conflicts": 0, "rejected": 0}

    monkeypatch.setattr(extraction_module, "extract_memories_for_turn", fake_extract)
    from app.tasks.agent_memory_extraction import sweep_memory_extraction
    result = sweep_memory_extraction(db=session)
    assert sorted(calls) == ["t-1", "t-2"]
    assert result == {"processed": 2, "applied": 1, "errors": 1}

    states = {r["turn_id"]: r["state"] for r in session.execute(text(
        "SELECT turn_id, state FROM agent_memory_extraction_outbox"
    )).mappings().all()}
    assert states["t-2"] == "applied"
    # a failed row stays pending for retry on the next sweep, not stuck 'processing'
    assert states["t-1"] == "pending"
```

Run: `cd backend && TEST_DATABASE_URL=postgresql://ontoprompt:ontoprompt@localhost:5432/ontoprompt pytest tests/agent/test_agent_memory_long_term.py -v -k sweep`
Expected: FAIL — module doesn't exist.

- [ ] **Step 2: Wire the turn-hook**

In `backend/app/tasks/agent_turn.py`, immediately after the `finalize_turn_succeeded(...)` call and before the final `db.commit()`, insert one row:

```python
        db.execute(text(
            "INSERT INTO agent_memory_extraction_outbox (id, turn_id, session_id, state, created_at) "
            "VALUES (:id, :turn_id, :session_id, 'pending', now())"
        ), {"id": str(uuid.uuid4()), "turn_id": turn_id, "session_id": row["session_id"]})
```

This needs `import uuid` at the top of the file if not already present — check first, this file may not currently import it. Verify this insert lands in the SAME transaction as `finalize_turn_succeeded` (i.e., before the `db.commit()` a few lines below it, not after) so a crash between the two never leaves a Turn finalized without its extraction row, or vice versa.

- [ ] **Step 3: Implement the sweep**

Create `backend/app/tasks/agent_memory_extraction.py`:

```python
"""Periodic long-term memory extraction sweep (P6B-2a).

Consumes agent_memory_extraction_outbox rows written by agent_turn.py at
Turn finalization. Mirrors P6B-1's summary sweep pattern (best-effort,
per-row error isolation, never on the Turn critical path) but is event-
driven off an outbox rather than a periodic full-table scan, since
extraction is naturally a per-Turn event rather than a threshold condition.
"""
import logging

from sqlalchemy import text

from app.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)

BATCH_SIZE = 50


def sweep_memory_extraction(db=None) -> dict:
    from app.services.memory.extraction import extract_memories_for_turn

    owns_session = db is None
    if owns_session:
        from app.database import SessionLocal
        db = SessionLocal()
    try:
        rows = db.execute(text(
            "SELECT id, turn_id FROM agent_memory_extraction_outbox "
            "WHERE state = 'pending' ORDER BY created_at LIMIT :limit"
        ), {"limit": BATCH_SIZE}).mappings().all()
        applied = 0
        errors = 0
        for row in rows:
            try:
                extract_memories_for_turn(db, turn_id=row["turn_id"])
                db.execute(text(
                    "UPDATE agent_memory_extraction_outbox SET state = 'applied', processed_at = now() "
                    "WHERE id = :id"
                ), {"id": row["id"]})
                db.commit()
                applied += 1
            except Exception:
                errors += 1
                logger.exception("memory extraction failed for turn %s", row["turn_id"])
                db.rollback()
        return {"processed": len(rows), "applied": applied, "errors": errors}
    finally:
        if owns_session:
            db.close()


@celery_app.task(name="agent.memory_extraction_sweep")
def memory_extraction_sweep_task():
    return sweep_memory_extraction()
```

Note: on a per-row exception, the row's `state` stays `'pending'` (the `db.rollback()` undoes any partial write from that row's own attempt, including any `UPDATE ... state = 'applied'` that hadn't committed yet — since the whole per-row block, including the state update, is inside one try, a raised exception from `extract_memories_for_turn` means the state-update `UPDATE` never even ran). This is deliberate: a transient failure (e.g. a flaky LLM call) should retry on the next sweep, not get stuck. If a row fails repeatedly forever, that's a real production concern (unbounded retry, matching the exact class of issue P6B-1's final review flagged and deferred for the summary sweep) — out of scope for this task, already a known, documented limitation class from P6B-1.

In `backend/app/tasks/celery_app.py`, add `"app.tasks.agent_memory_extraction"` to the `include=[...]` list and add to `beat_schedule`:

```python
    "agent-memory-extraction-sweep": {
        "task": "agent.memory_extraction_sweep",
        "schedule": 60.0,
    },
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && TEST_DATABASE_URL=postgresql://ontoprompt:ontoprompt@localhost:5432/ontoprompt pytest tests/agent/test_agent_memory_long_term.py -v -k sweep`
Expected: passes.

Also run the existing turn-worker test file to confirm the new outbox insert doesn't regress anything: `cd backend && TEST_DATABASE_URL=postgresql://ontoprompt:ontoprompt@localhost:5432/ontoprompt pytest tests/agent/test_turn_worker_loop.py -v` — every pre-existing test must still pass.

- [ ] **Step 5: Commit**

```bash
git add backend/app/tasks/agent_turn.py backend/app/tasks/agent_memory_extraction.py \
  backend/app/tasks/celery_app.py backend/tests/agent/test_agent_memory_long_term.py
git commit -m "feat: trigger and sweep long-term memory extraction per Turn"
```

---

### Task 7: Full regression pass

**Files:** none (verification only)

- [ ] **Step 1: Backend regression**

Run: `cd backend && TEST_DATABASE_URL=postgresql://ontoprompt:ontoprompt@localhost:5432/ontoprompt pytest tests/agent/ -q --ignore=tests/agent/test_playwright_adapter.py`
Expected: all pass except the pre-existing, unrelated cluster already established across this session's prior plans (`test_0003_full_migration.py`, `test_build_manifest.py` x3, `test_schema_startup.py` — 5 failures, confirmed pre-existing and unrelated multiple times already this session).

- [ ] **Step 2: Confirm no stray route/manifest drift**

This plan added no new API routes, so `backend/openapi-agent.json` should be untouched. Run: `git diff --stat dev -- backend/openapi-agent.json` (or the equivalent against this plan's base) and confirm it's empty.

- [ ] **Step 3: Confirm P6B-1's short-term memory path is unaffected**

Run: `cd backend && TEST_DATABASE_URL=postgresql://ontoprompt:ontoprompt@localhost:5432/ontoprompt pytest tests/agent/test_agent_memory_short_term.py tests/agent/test_langgraph_runtime.py -v` — every P6B-1 test must still pass unchanged, since this plan's turn-hook addition sits in the same file (`agent_turn.py`) as P6B-1's own finalization logic but must not alter it.

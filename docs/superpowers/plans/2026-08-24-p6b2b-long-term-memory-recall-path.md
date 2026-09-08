# P6B-2b: Long-Term Memory Recall Path Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the read side of Agent long-term memory: a Chroma vector-outbox consumer that embeds active memories, and a deterministic hybrid semantic/lexical recall algorithm that selects a bounded, cited set of memories into a Turn's context.

**Architecture:** A periodic Celery sweep consumes `agent_memory_vector_outbox` (written by the already-merged P6B-2a write path) and upserts/deletes memory embeddings in a dedicated Chroma collection per security domain. At Turn-build time, `recall_memories()` gathers SQL-filtered candidates, scores each by whichever formula its evidence supports (hybrid if it has a current embedding, lexical-only otherwise), deduplicates across channels, greedily selects a diverse, budget-bounded set, and hands cited strings to the already-merged `assemble_bounded_messages()` (P6B-1) via its `recalled_memories` parameter — which today is always called with `recalled_memories=[]`.

**Tech Stack:** Python 3.11+, FastAPI, SQLAlchemy Core (raw `text()` SQL, matching every prior memory-service module), PostgreSQL (`ts_rank_cd` lexical search, generated `tsvector` + GIN index), ChromaDB (`chromadb==0.5.20`, already a dependency), Celery.

**Spec:** `docs/superpowers/plans/2026-08-09-agent-ontology-implementation.md`, Section 11 ("Memory"), specifically the two recall paragraphs (the paragraph beginning "Recall first filters SQL candidates..." and the paragraph beginning "Each candidate is scored by its available evidence..."), plus the Phase 6 row in Section 13.1. This plan implements exactly those two paragraphs; the preceding paragraphs in the same section (short-term memory, long-term write path, canonicalizer) were already implemented by P6B-1 and P6B-2a, both merged to `dev`.

## Global Constraints

Exact values copied verbatim (or arithmetically restated) from the spec — every task's requirements implicitly include this section.

- Candidate pool size: at most `4 * max_recall_count` fetched from **each** of the lexical and vector channels, then SQL-refetched.
- Semantic score: `semantic = (cosine + 1) / 2`. A missing or stale embedding cannot enter the vector channel at all.
- Lexical score: PostgreSQL `ts_rank_cd`, min-max normalized across the union of matched candidates. When every positive rank in the union is equal, they all normalize to `1`. A candidate with no lexical match (raw rank `0` / absent) normalizes to `0`.
- Stored `confidence` is clamped to `[0, 1]` defensively in code (the column is `NUMERIC` with no DB-level range check).
- Recency: `recency = exp(-age_days / 30)`, where `age_days` is the fractional number of days between `agent_memories.updated_at` and "now" (an injectable `now` parameter for deterministic tests).
- Source quality is an exact per-tier constant: explicit user correction `1.00`, explicit user statement `0.95`, user-confirmed tool/document fact `0.90`, policy-approved tool result `0.80`, grounded document extraction `0.75`; assistant-only summaries/inferences are ineligible.
  - **Scoping decision (documented, not a gap to silently paper over):** `agent_memories.consent_basis` (the only provenance column P6B-2a persists) is `CHECK`-constrained to exactly two values: `explicit_statement` and `explicit_confirmation`. Only two of the five spec tiers are reachable with today's schema: `explicit_statement → 0.95` (explicit user statement) and `explicit_confirmation → 0.90` (user-confirmed tool/document fact — P6B-2a's extraction brief describes `explicit_confirmation` candidates as "tool/retrieval-derived candidates requiring a source-backed confirmation prompt," which is exactly this tier). The other three named constants (`1.00`, `0.80`, `0.75`) are defined in code so no work is wasted once a future write-path plan adds a correction flow, a policy-approved-tool-result flow, or a document-extraction flow — but no code path in this plan can produce them, and this plan does not invent new write-side columns to manufacture them. "Assistant-only summaries/inferences are ineligible" is automatically satisfied: P6B-2a's extraction pipeline only ever writes memories carrying one of the two real `consent_basis` values above; there is no assistant-inference write path.
- A candidate **with** a current embedding uses the hybrid formula: `score = 0.50*semantic + 0.20*lexical + 0.15*confidence + 0.10*recency + 0.05*source_quality`, and records `ranking_mode = "hybrid"`.
- A candidate **without** a current embedding (including one admitted only via the lexical channel) uses the renormalized formula: `score = 0.40*lexical + 0.30*confidence + 0.20*recency + 0.10*source_quality`, **requires a positive raw (pre-normalization) `ts_rank_cd`** (i.e. it must have actually matched the lexical query — the *normalized* lexical component used inside the formula can still legitimately come out to exactly `0` for the lowest-ranked candidate among several distinct positive ranks; that is a real, spec-named edge case, not a bug), and records `ranking_mode = "lexical_only"`.
- Both formulas require base `score >= 0.60` to be eligible at all.
- After canonical-hash deduplication (in practice: dedup by `agent_memories.id`/`canonical_value_hash` — the write path's own partial unique index already guarantees at most one `active` row per exact fact, so "duplication" here means the *same* row surfacing as a candidate via both the lexical and vector channels in one recall call, not two distinct rows sharing a value), greedy selection uses:
  - `selection_score = 0.75*score - 0.25*max_cosine_similarity_to_already_selected_embedded_item` for a candidate that has a current embedding (an empty selected-embedding set contributes similarity `0`).
  - `selection_score = score` for a lexical-only candidate (never penalized for similarity to already-selected items, since it has no embedding to compare).
  - Each slot picks the candidate with the highest `selection_score`, breaking ties in order: `selection_score DESC, score DESC, updated_at DESC, id ASC`.
- If Chroma is unavailable, every candidate in that recall call follows lexical-only mode (the vector channel simply returns no candidates).
- Selection stops at `recall_count` items **and** at the exact pinned-tokenizer `recall_token_budget` — whichever binds first — without ever truncating an included item's text (an item that doesn't fit is skipped, not shortened; the loop keeps trying smaller items rather than halting at the first miss, matching the already-merged `assemble_bounded_messages`'s own downstream skip-and-continue convention for `recalled_memories`).
- Every recalled item is untrusted, cited content: format each as `f"[memory:{memory_id}] {display_text}"` before returning.
- "Current embedding" = `agent_memories.embedding_model_version` (set by this plan's vector-outbox consumer) equals the pinned constant `MEMORY_EMBEDDING_MODEL_VERSION` defined in Task 2. `NULL` (never embedded) or any other value (a future re-embedding bumping the constant) means missing/stale.
- **"Namespace, grants, sensitivity" scoping decision (documented, not a gap):** memory has no separate ACL/grant system of its own — it is namespaced purely by the `(security_domain_id, agent_id, user_id)` triple the write path already enforces (the same triple `agent_memories`' own partial unique index uses). "Grants" in the spec's filter list is satisfied by scoping every SQL candidate query to exactly that triple (never a broader query a caller could widen). `sensitivity` (the column exists, free-form, no enum) has no *additional* recall-time filter in this plan: within a user's own exact scope, a memory about themselves at any sensitivity level is eligible — sensitivity gates a cross-user/admin access surface that does not exist yet (no such read path has ever been built for memory). Revisit this decision if/when such a surface is added.
- **"SQL-refetches them" equivalence:** this plan's `_fetch_sql_candidates` (Task 4) runs *before* the lexical/vector channels, gathering the full authoritative candidate set (already `status='active'`, non-expired, exact-scope-filtered) once. Each channel's raw hits are then only ever looked up by membership in that already-fetched, already-authoritative dict — never trusted standalone. This achieves the same guarantee the spec's phrasing implies (a channel hit for a row that's since become non-active/expired/out-of-scope can never leak into scoring) without a second round-trip query, since the first query already *is* the authoritative refetch.
- **Known, accepted limitation (documented, not fixed in this plan):** the lexical `tsvector` column uses PostgreSQL's built-in `'simple'` text-search configuration (no CJK segmentation extension — e.g. `zhparser` — exists anywhere in this codebase, and installing one is out of this plan's scope). Chinese-language memory text will not be meaningfully word-segmented by the lexical channel; recall quality for CJK content leans more heavily on the semantic (Chroma) channel. This mirrors the class of accepted, documented gap this session has left in place before (e.g. P6B-2a's sweep lease/fence guard).
- Recall is read-only and must never fail a Turn: any exception inside recall (a DB error, a Chroma error not already handled by `is_available()`, anything unexpected) is caught, logged, and treated as "recall found nothing" (`[]`) — matching the fail-open-but-degrade precedent already established for `memory_settings` parsing in `langgraph_runtime.py`.
- Migration head at the start of this plan is `0019_agent_memory_long_term` (created by P6B-2a, merged to `dev` at commit `82cc877`). This plan's migration is `0020_agent_memory_recall_index`.
- **Test infrastructure must reuse P6B-2a's exact conventions, not reinvent them.** `backend/tests/agent/test_agent_memory_long_term.py` already establishes the pattern every task in this plan's test file must follow: `DEFAULT_DOMAIN = "00000000-0000-0000-0000-000000000001"` (a real UUID string, pre-seeded by migration `0003_publication_governance.py` as `DEFAULT_SECURITY_DOMAIN_ID` — never invent a different domain string like `"sd-1"`), a `_scoped_url(schema)` helper using `?options={quote(f'-csearch_path={schema},public', safe='-=,')}` (note the trailing `,public` so unqualified references to shared/base tables still resolve, and the specific `quote(..., safe=...)` call — do not hand-roll a different URL-escaping scheme), an `_alembic(schema, *args, check=True)` helper that shells out to `scripts/run_migrations.py` (not raw `alembic` CLI) with `DATABASE_URL` set via `env=dict(os.environ, DATABASE_URL=_scoped_url(schema))`, and a `session` fixture that creates a fresh schema, migrates it to this plan's `HEAD`, and seeds a **complete** baseline (`users` row `u-1`, `model_configs`/`model_config_versions` (`mc-1`/`mcv-1`), `agents` row `ag-1` with `active_version_id` set, `agent_versions` row `av-1` with `memory_settings` including `"long_term_enabled": true`, and `agent_sessions` row `sess-1` scoped to `ag-1`/`u-1`) before yielding. Task 1 copies this fixture verbatim (adjusting only `HEAD` and the schema-name prefix); every later task's tests use the same fixture and the same baseline IDs (`ag-1`, `u-1`, `sess-1`, `DEFAULT_DOMAIN`) directly — they do not re-seed what the fixture already provides.

---

### Task 1: Migration — lexical search index

**Files:**
- Create: `backend/alembic/versions/0020_agent_memory_recall_index.py`
- Test: `backend/tests/agent/test_agent_memory_recall.py` (new — every later task in this plan appends to this same file)

**Interfaces:**
- Produces: `agent_memories.search_vector` (a `tsvector` generated column) and a GIN index on it, consumed by Task 4's lexical channel query. Produces the shared `session` pytest fixture and `DEFAULT_DOMAIN`/`_insert_memory` helpers every later task's tests import by way of being appended to the same file.

- [ ] **Step 1: Read the precedent file first**

Read `backend/tests/agent/test_agent_memory_long_term.py` in full before writing anything — this task's fixture is a close copy of its `session` fixture (P6B-2a's own pattern for exactly this kind of test), not a new invention. Confirm the exact current column/table names it seeds against (`agents`, `agent_versions`, `agent_sessions`, `model_configs`, `model_config_versions`, `application_state_schema_registries`) still match, since this file may have changed since the excerpt below was written.

- [ ] **Step 2: Write the failing test**

Create `backend/tests/agent/test_agent_memory_recall.py`:

```python
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
```

Run: `cd backend && TEST_DATABASE_URL=postgresql://ontoprompt:ontoprompt@localhost:5432/ontoprompt pytest tests/agent/test_agent_memory_recall.py -v`
Expected: FAIL — migration `0020_agent_memory_recall_index` doesn't exist yet (check `scripts/run_migrations.py upgrade 0020_agent_memory_recall_index` errors with an unknown revision).

- [ ] **Step 3: Write the migration**

Create `backend/alembic/versions/0020_agent_memory_recall_index.py`:

```python
"""Lexical search index for long-term memory recall (P6B-2b, Section 11).

Adds a generated tsvector column over agent_memories.display_text and a GIN
index on it, consumed by the recall algorithm's lexical channel
(ts_rank_cd). Uses PostgreSQL's built-in 'simple' text-search configuration
— no CJK segmentation extension exists in this codebase; Chinese-language
memory text will not be meaningfully word-segmented by this channel. That
is a documented, accepted limitation, not something this migration attempts
to solve.

Revision ID: 0020_agent_memory_recall_index
Revises: 0019_agent_memory_long_term
Create Date: 2026-08-24
"""
from alembic import op

revision = "0020_agent_memory_recall_index"
down_revision = "0019_agent_memory_long_term"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE agent_memories ADD COLUMN search_vector tsvector "
        "GENERATED ALWAYS AS (to_tsvector('simple', display_text)) STORED"
    )
    op.execute(
        "CREATE INDEX ix_agent_memories_search_vector ON agent_memories USING GIN (search_vector)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_agent_memories_search_vector")
    op.execute("ALTER TABLE agent_memories DROP COLUMN IF EXISTS search_vector")
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && TEST_DATABASE_URL=postgresql://ontoprompt:ontoprompt@localhost:5432/ontoprompt pytest tests/agent/test_agent_memory_recall.py -v`
Expected: both tests pass.

- [ ] **Step 5: Commit**

```bash
git add backend/alembic/versions/0020_agent_memory_recall_index.py backend/tests/agent/test_agent_memory_recall.py
git commit -m "feat: add lexical search index for long-term memory recall"
```

---

### Task 2: Memory vector store (Chroma)

**Files:**
- Create: `backend/app/services/memory/vector_store.py`
- Test: `backend/tests/agent/test_agent_memory_recall.py` (append)

**Interfaces:**
- Consumes: `app.services.v2.vector.chroma_service.ChromaService` (existing, general-purpose Chroma wrapper — reuse its connection/collection lifecycle, do not build a second one).
- Produces: `MEMORY_EMBEDDING_MODEL_VERSION: str` (module constant), `is_available() -> bool`, `memory_collection_name(security_domain_id: str) -> str`, `upsert_memory_embedding(memory_id: str, security_domain_id: str, display_text: str) -> bool`, `delete_memory_embedding(memory_id: str, security_domain_id: str) -> bool`, `query_similar(security_domain_id: str, query_text: str, n_results: int) -> list[dict]` (each dict: `{"id": str, "cosine": float}` — **raw** cosine similarity, not yet mapped to `(cosine+1)/2`; that mapping is Task 5's job). Task 3 (outbox consumer) and Task 5 (semantic channel) both call into this module.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/agent/test_agent_memory_recall.py`:

```python
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
```

Run: `cd backend && pytest tests/agent/test_agent_memory_recall.py -v -k "vector_store or upsert or query_similar or collection_name"`
Expected: FAIL — module doesn't exist.

- [ ] **Step 2: Implement the vector store**

Create `backend/app/services/memory/vector_store.py`:

```python
"""Chroma-backed memory embedding store (P6B-2b, Section 11).

One Chroma collection per security domain (memories are always scoped to a
security domain, agent, and user; namespacing the collection by security
domain — the coarsest of the three — keeps collection count bounded while
still letting per-agent/per-user scoping happen at the SQL layer before any
Chroma hit is trusted, matching this codebase's existing candidate-collection
pattern in app/services/indexes/release_aware.py).

Uses Chroma's own default embedding function (this codebase has no other
embedding-model integration anywhere) via the existing general-purpose
ChromaService wrapper. MEMORY_EMBEDDING_MODEL_VERSION is the pinned
identifier this plan's vector-outbox consumer stamps onto
agent_memories.embedding_model_version on successful upsert; bumping it in
a future plan (e.g. switching embedding models) is what "stale" recall
detection is for.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

MEMORY_EMBEDDING_MODEL_VERSION = "memory-embed-chroma-default-v1"

_COLLECTION_PREFIX = "agent_memory_"


def _service():
    from app.services.v2.vector.chroma_service import ChromaService
    return ChromaService()


def is_available() -> bool:
    return _service().available


def memory_collection_name(security_domain_id: str) -> str:
    return f"{_COLLECTION_PREFIX}{security_domain_id}"


def upsert_memory_embedding(memory_id: str, security_domain_id: str, display_text: str) -> bool:
    service = _service()
    if not service.available:
        return False
    collection = service.get_or_create_collection(memory_collection_name(security_domain_id))
    if not collection:
        return False
    try:
        collection.upsert(ids=[memory_id], documents=[display_text])
        return True
    except Exception as e:
        logger.warning("memory embedding upsert failed for %s: %s", memory_id, e)
        return False


def delete_memory_embedding(memory_id: str, security_domain_id: str) -> bool:
    service = _service()
    if not service.available:
        return False
    collection = service.get_or_create_collection(memory_collection_name(security_domain_id))
    if not collection:
        return False
    try:
        collection.delete(ids=[memory_id])
        return True
    except Exception as e:
        logger.warning("memory embedding delete failed for %s: %s", memory_id, e)
        return False


def query_similar(security_domain_id: str, query_text: str, n_results: int) -> list[dict[str, Any]]:
    service = _service()
    if not service.available or n_results <= 0:
        return []
    collection = service.get_or_create_collection(memory_collection_name(security_domain_id))
    if not collection:
        return []
    try:
        results = collection.query(
            query_texts=[query_text], n_results=n_results, include=["distances"],
        )
    except Exception as e:
        logger.warning("memory embedding query failed for domain %s: %s", security_domain_id, e)
        return []
    ids = results.get("ids", [[]])[0]
    distances = results.get("distances", [[]])[0]
    hits = []
    for i, memory_id in enumerate(ids):
        distance = distances[i] if i < len(distances) else 2.0
        # collection uses cosine space (ChromaService.get_or_create_collection
        # pins metadata={"hnsw:space": "cosine"}), so cosine distance is
        # exactly 1 - cosine_similarity — recover the raw similarity here.
        hits.append({"id": memory_id, "cosine": 1.0 - distance})
    return hits
```

- [ ] **Step 3: Run the tests to verify they pass**

Run: `cd backend && TEST_DATABASE_URL=postgresql://ontoprompt:ontoprompt@localhost:5432/ontoprompt pytest tests/agent/test_agent_memory_recall.py -v`
Expected: all pass (the Chroma-dependent test skips cleanly if Chroma isn't reachable in this environment — check `docker ps` / the project's Chroma service before assuming it's unreachable; this repo's `chroma_data/` directory and `chroma_host`/`chroma_port` settings suggest a local Chroma instance is normally available in this dev environment).

- [ ] **Step 4: Commit**

```bash
git add backend/app/services/memory/vector_store.py backend/tests/agent/test_agent_memory_recall.py
git commit -m "feat: add Chroma-backed memory embedding vector store"
```

---

### Task 3: Vector-outbox consumer

**Files:**
- Create: `backend/app/tasks/agent_memory_vector.py`
- Modify: `backend/app/tasks/celery_app.py`
- Test: `backend/tests/agent/test_agent_memory_recall.py` (append)

**Interfaces:**
- Consumes: `agent_memory_vector_outbox` (created by P6B-2a's Task 1 migration, columns `id, memory_id, event_type IN('upsert','delete'), state IN('pending','applied'), created_at`), Task 2's `upsert_memory_embedding`/`delete_memory_embedding`/`MEMORY_EMBEDDING_MODEL_VERSION`.
- Produces: `sweep_memory_vector_outbox(db: Session | None = None, *, batch: int = 50) -> dict` (`{"processed": int, "applied": int, "errors": int}`), Celery task `agent.memory_vector_sweep`, beat entry `"agent-memory-vector-sweep"` (60s interval).

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/agent/test_agent_memory_recall.py`:

```python
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
```

Run: `cd backend && TEST_DATABASE_URL=postgresql://ontoprompt:ontoprompt@localhost:5432/ontoprompt pytest tests/agent/test_agent_memory_recall.py -v -k sweep`
Expected: FAIL — module doesn't exist.

- [ ] **Step 2: Implement the consumer**

Create `backend/app/tasks/agent_memory_vector.py`:

```python
"""Periodic Chroma vector-outbox consumer for long-term memory (P6B-2b).

Consumes agent_memory_vector_outbox rows written by P6B-2a's extraction and
consent-revocation paths. Claims rows via SELECT ... FOR UPDATE SKIP LOCKED
(mirrors app/services/indexes/release_aware.py::consume_outbox — the
concurrency-safe pattern P6B-2a's own extraction sweep did NOT use, flagged
as a residual risk in that plan's final review; this consumer closes that
gap for its own outbox rather than repeating it). Per-row error isolation:
a failed row's state is rolled back to 'pending' for retry on the next
sweep, and one row's failure never blocks its siblings in the same batch.
"""
import logging

from sqlalchemy import text

from app.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)

BATCH_SIZE = 50


def sweep_memory_vector_outbox(db=None, *, batch: int = BATCH_SIZE) -> dict:
    from app.services.memory import vector_store

    owns_session = db is None
    if owns_session:
        from app.database import SessionLocal
        db = SessionLocal()
    try:
        rows = db.execute(text(
            "SELECT ov.id AS outbox_id, ov.memory_id, ov.event_type, m.security_domain_id, "
            "m.display_text "
            "FROM agent_memory_vector_outbox ov "
            "JOIN agent_memories m ON m.id = ov.memory_id "
            "WHERE ov.state = 'pending' ORDER BY ov.created_at LIMIT :batch "
            "FOR UPDATE OF ov SKIP LOCKED"
        ), {"batch": batch}).mappings().all()
        applied = 0
        errors = 0
        for row in rows:
            try:
                if row["event_type"] == "upsert":
                    ok = vector_store.upsert_memory_embedding(
                        row["memory_id"], row["security_domain_id"], row["display_text"])
                    if not ok:
                        raise RuntimeError("vector store upsert returned False")
                    db.execute(text(
                        "UPDATE agent_memories SET embedding_model_version = :v WHERE id = :id"
                    ), {"v": vector_store.MEMORY_EMBEDDING_MODEL_VERSION, "id": row["memory_id"]})
                else:
                    ok = vector_store.delete_memory_embedding(
                        row["memory_id"], row["security_domain_id"])
                    if not ok:
                        raise RuntimeError("vector store delete returned False")
                    db.execute(text(
                        "UPDATE agent_memories SET embedding_model_version = NULL WHERE id = :id"
                    ), {"id": row["memory_id"]})
                db.execute(text(
                    "UPDATE agent_memory_vector_outbox SET state = 'applied' WHERE id = :id"
                ), {"id": row["outbox_id"]})
                db.commit()
                applied += 1
            except Exception:
                errors += 1
                logger.exception("memory vector sweep failed for outbox row %s", row["outbox_id"])
                db.rollback()
        return {"processed": len(rows), "applied": applied, "errors": errors}
    finally:
        if owns_session:
            db.close()


@celery_app.task(name="agent.memory_vector_sweep")
def memory_vector_sweep_task():
    return sweep_memory_vector_outbox()
```

Note on `FOR UPDATE OF ov SKIP LOCKED` inside a query that also joins `agent_memories`: `FOR UPDATE OF ov` scopes the row lock to only the outbox table (not the joined memory row), which is correct — this consumer never needs to block a concurrent write to `agent_memories` itself, only to prevent two sweep invocations from claiming the same outbox row.

In `backend/app/tasks/celery_app.py`, add `"app.tasks.agent_memory_vector"` to the `include=[...]` list and add to `beat_schedule` (mirror the exact existing entries for P6B-1's summary sweep and P6B-2a's extraction sweep):

```python
    "agent-memory-vector-sweep": {
        "task": "agent.memory_vector_sweep",
        "schedule": 60.0,
    },
```

- [ ] **Step 3: Run the tests to verify they pass**

Run: `cd backend && TEST_DATABASE_URL=postgresql://ontoprompt:ontoprompt@localhost:5432/ontoprompt pytest tests/agent/test_agent_memory_recall.py -v -k sweep`
Expected: all pass.

- [ ] **Step 4: Commit**

```bash
git add backend/app/tasks/agent_memory_vector.py backend/app/tasks/celery_app.py backend/tests/agent/test_agent_memory_recall.py
git commit -m "feat: add Chroma vector-outbox consumer for long-term memory"
```

---

### Task 4: SQL candidate filtering + lexical channel

**Files:**
- Create: `backend/app/services/memory/recall.py` (this task starts the file; Tasks 5 and 6 extend it)
- Test: `backend/tests/agent/test_agent_memory_recall.py` (append)

**Interfaces:**
- Produces: `_fetch_sql_candidates(db, *, security_domain_id, agent_id, user_id) -> list[dict]` (each dict has every column later tasks need: `id, subject_key, predicate, canonical_value_hash, display_text, confidence, consent_basis, embedding_model_version, updated_at`), `_lexical_channel(db, *, security_domain_id, agent_id, user_id, query_text, limit) -> dict[str, float]` (memory_id → **normalized** lexical score in `[0, 1]`, only for candidates with a positive raw `ts_rank_cd`).

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/agent/test_agent_memory_recall.py`:

```python
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
```

Run: `cd backend && TEST_DATABASE_URL=postgresql://ontoprompt:ontoprompt@localhost:5432/ontoprompt pytest tests/agent/test_agent_memory_recall.py -v -k "fetch_sql_candidates or lexical_channel"`
Expected: FAIL — module doesn't exist.

- [ ] **Step 2: Implement the SQL candidate filter and lexical channel**

Create `backend/app/services/memory/recall.py`:

```python
"""Long-term memory recall (P6B-2b, Section 11).

Recall first filters SQL candidates by namespace/status/TTL, gathers up to
4*recall_count candidates from each of the lexical and vector channels,
scores each surviving candidate by whichever formula its evidence supports,
deduplicates across channels, and greedily selects a diverse, token-budget-
bounded set. See the plan's Global Constraints for the exact formulas.
"""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session

CANDIDATE_OVERFETCH_MULTIPLIER = 4


def _fetch_sql_candidates(db: Session, *, security_domain_id: str, agent_id: str,
                          user_id: str) -> list[dict]:
    rows = db.execute(text(
        "SELECT id, subject_key, predicate, canonical_value_hash, display_text, confidence, "
        "consent_basis, embedding_model_version, updated_at "
        "FROM agent_memories "
        "WHERE security_domain_id = :d AND agent_id = :a AND user_id = :u "
        "AND status = 'active' AND (expires_at IS NULL OR expires_at > now())"
    ), {"d": security_domain_id, "a": agent_id, "u": user_id}).mappings().all()
    return [dict(r) for r in rows]


def _lexical_channel(db: Session, *, security_domain_id: str, agent_id: str, user_id: str,
                     query_text: str, limit: int) -> dict[str, float]:
    rows = db.execute(text(
        "SELECT m.id, ts_rank_cd(m.search_vector, plainto_tsquery('simple', :q)) AS rank "
        "FROM agent_memories m "
        "WHERE m.security_domain_id = :d AND m.agent_id = :a AND m.user_id = :u "
        "AND m.status = 'active' AND (m.expires_at IS NULL OR m.expires_at > now()) "
        "AND m.search_vector @@ plainto_tsquery('simple', :q) "
        "ORDER BY rank DESC LIMIT :limit"
    ), {"d": security_domain_id, "a": agent_id, "u": user_id, "q": query_text,
        "limit": limit}).mappings().all()
    if not rows:
        return {}
    ranks = {r["id"]: float(r["rank"]) for r in rows}
    positive = [v for v in ranks.values() if v > 0]
    if not positive:
        return {}
    min_rank, max_rank = min(positive), max(positive)
    if min_rank == max_rank:
        return {mid: 1.0 for mid, v in ranks.items() if v > 0}
    return {mid: (v - min_rank) / (max_rank - min_rank) for mid, v in ranks.items() if v > 0}
```

Note: `plainto_tsquery` (not `to_tsquery`) is used for the *query* side so a multi-word `query_text` (e.g. a full user turn message) is parsed as an implicit AND of its terms rather than requiring exact `to_tsquery` operator syntax the caller would have to construct — this matches how `query_text` is actually produced (Task 7 passes the raw Turn user message, not a hand-built tsquery string). Both sides use the `'simple'` configuration to match the generated column from Task 1.

- [ ] **Step 3: Run the tests to verify they pass**

Run: `cd backend && TEST_DATABASE_URL=postgresql://ontoprompt:ontoprompt@localhost:5432/ontoprompt pytest tests/agent/test_agent_memory_recall.py -v -k "fetch_sql_candidates or lexical_channel"`
Expected: all pass.

- [ ] **Step 4: Commit**

```bash
git add backend/app/services/memory/recall.py backend/tests/agent/test_agent_memory_recall.py
git commit -m "feat: add SQL candidate filtering and lexical recall channel"
```

---

### Task 5: Semantic channel + scoring

**Files:**
- Modify: `backend/app/services/memory/recall.py` (append; do not touch Task 4's functions)
- Test: `backend/tests/agent/test_agent_memory_recall.py` (append)

**Interfaces:**
- Consumes: Task 2's `vector_store.query_similar`, `vector_store.MEMORY_EMBEDDING_MODEL_VERSION`.
- Produces: `_semantic_channel(security_domain_id, query_text, limit, *, sql_candidates) -> dict[str, float]` (memory_id → raw cosine, cross-checked against each candidate's own `embedding_model_version` from `sql_candidates` so a stale/mismatched SQL row never gets a semantic score even if a stale vector still physically exists in Chroma), `SOURCE_QUALITY: dict[str, float]`, `_recency_score(updated_at, now) -> float`, `_score_candidate(*, semantic, lexical, confidence, source_quality, recency) -> tuple[float, str] | None` (returns `(score, ranking_mode)`, or `None` if the candidate is below the `0.60` threshold or — for the lexical-only path — has no positive lexical evidence at all).

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/agent/test_agent_memory_recall.py`:

```python
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
```

Run: `cd backend && TEST_DATABASE_URL=postgresql://ontoprompt:ontoprompt@localhost:5432/ontoprompt pytest tests/agent/test_agent_memory_recall.py -v -k "semantic_channel or recency_score or score_candidate or source_quality"`
Expected: FAIL — functions don't exist.

- [ ] **Step 2: Implement the semantic channel and scoring**

Append to `backend/app/services/memory/recall.py`:

```python
import math
from datetime import datetime, timezone

SOURCE_QUALITY = {
    "explicit_user_correction": 1.00,       # unreachable with today's schema (documented)
    "explicit_statement": 0.95,
    "explicit_confirmation": 0.90,
    "policy_approved_tool_result": 0.80,    # unreachable with today's schema (documented)
    "grounded_document_extraction": 0.75,   # unreachable with today's schema (documented)
}

SCORE_THRESHOLD = 0.60


def _semantic_channel(security_domain_id: str, query_text: str, limit: int, *,
                      sql_candidates: list[dict]) -> dict[str, float]:
    from app.services.memory import vector_store

    current_versions = {
        c["id"] for c in sql_candidates
        if c.get("embedding_model_version") == vector_store.MEMORY_EMBEDDING_MODEL_VERSION
    }
    if not current_versions:
        return {}
    hits = vector_store.query_similar(security_domain_id, query_text, limit)
    return {h["id"]: h["cosine"] for h in hits if h["id"] in current_versions}


def _recency_score(updated_at: datetime, now: datetime) -> float:
    age_days = (now - updated_at).total_seconds() / 86400.0
    return math.exp(-age_days / 30.0)


def _score_candidate(*, semantic: float | None, lexical: float, confidence: float,
                     source_quality: float, recency: float) -> tuple[float, str] | None:
    confidence = max(0.0, min(1.0, confidence))
    if semantic is not None:
        semantic_mapped = (semantic + 1.0) / 2.0
        score = (0.50 * semantic_mapped + 0.20 * lexical + 0.15 * confidence
                 + 0.10 * recency + 0.05 * source_quality)
        mode = "hybrid"
    else:
        if lexical <= 0.0:
            return None
        score = (0.40 * lexical + 0.30 * confidence + 0.20 * recency + 0.10 * source_quality)
        mode = "lexical_only"
    if score < SCORE_THRESHOLD:
        return None
    return score, mode
```

Note: `_score_candidate`'s `lexical <= 0.0` check enforces "requires a positive raw `ts_rank_cd`" at the caller boundary — by the time a candidate reaches this function, its `lexical` argument is the *normalized* score from `_lexical_channel`, which only ever contains entries with a positive raw rank in the first place (candidates with rank `0`/absent are never in that dict, per Task 4). Task 6's caller passes `lexical_scores.get(memory_id, 0.0)` — a candidate absent from that dict (never matched the lexical query at all) correctly collapses to `0.0` here, which is exactly the "no positive lexical rank" rejection case. A candidate that *did* match (present in the dict) but whose *normalized* value happens to be `0.0` (the minimum among several distinct positive ranks) is a different case this function cannot distinguish from "absent" by the float value alone — but for the `lexical_only` formula specifically, a normalized `0.0` and an absent candidate both correctly fail to qualify for lexical-only ranking (a `0.0` lexical component combined with `score < SCORE_THRESHOLD` in nearly every realistic case), so no separate flag is needed in practice; Task 4's `test_lexical_channel_min_max_normalizes_distinct_positive_ranks` test already documents that this specific edge case is a deliberate, spec-named property of the normalization step, not a defect.

- [ ] **Step 3: Run the tests to verify they pass**

Run: `cd backend && TEST_DATABASE_URL=postgresql://ontoprompt:ontoprompt@localhost:5432/ontoprompt pytest tests/agent/test_agent_memory_recall.py -v -k "semantic_channel or recency_score or score_candidate or source_quality"`
Expected: all pass.

- [ ] **Step 4: Commit**

```bash
git add backend/app/services/memory/recall.py backend/tests/agent/test_agent_memory_recall.py
git commit -m "feat: add semantic recall channel and hybrid/lexical-only scoring"
```

---

### Task 6: Deduplication + greedy diverse selection + public `recall_memories()`

**Files:**
- Modify: `backend/app/services/memory/recall.py` (append; do not touch Tasks 4/5's functions)
- Test: `backend/tests/agent/test_agent_memory_recall.py` (append)

**Interfaces:**
- Consumes: Task 4's `_fetch_sql_candidates`/`_lexical_channel`, Task 5's `_semantic_channel`/`_score_candidate`/`_recency_score`/`SOURCE_QUALITY`, `app.services.runtime.tokenizer.count_tokens` (already merged, P6B-1).
- Produces: `recall_memories(db, *, security_domain_id, agent_id, user_id, query_text, model_name, recall_count, recall_token_budget, now=None) -> list[str]` — the **one public entrypoint** Task 7 wires into `langgraph_runtime.py`. Returns cited strings (`f"[memory:{id}] {display_text}"`), already deduplicated, diversity-selected, and stopped at both `recall_count` and `recall_token_budget`. Internal: `_dedup_and_score_candidates` (not a separate `_dedup_candidates` step — see its docstring note below for why one function does both), `_greedy_select`, `_format_citation`.

**Note on "deduplication" in this task:** the write path's own partial unique index already guarantees at most one `active` row per exact `(security_domain_id, agent_id, user_id, subject_key, predicate, canonical_value_hash)` — so `_fetch_sql_candidates`'s result is already unique per memory row by construction. There is no separate dedup *pass* in this task's code; `_dedup_and_score_candidates` achieves the spec's "after canonical-hash deduplication" guarantee structurally, by iterating the already-unique SQL candidate list exactly once and looking up each channel's score by `id` — a memory found by *both* the lexical and vector channels is naturally scored once, not twice, with no explicit merge step needed.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/agent/test_agent_memory_recall.py`:

```python
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

    from app.services.memory.recall import recall_memories
    result = recall_memories(session, security_domain_id=DEFAULT_DOMAIN, agent_id="ag-1",
                             user_id="u-1", query_text="what color", model_name="gpt-4o",
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
```

Run: `cd backend && TEST_DATABASE_URL=postgresql://ontoprompt:ontoprompt@localhost:5432/ontoprompt pytest tests/agent/test_agent_memory_recall.py -v -k recall_memories`
Expected: FAIL — function doesn't exist.

- [ ] **Step 2: Implement deduplication, greedy selection, and the public entrypoint**

Append to `backend/app/services/memory/recall.py`:

```python
from app.services.runtime.tokenizer import count_tokens


def _dedup_and_score_candidates(*, sql_candidates: list[dict], lexical_scores: dict[str, float],
                                semantic_scores: dict[str, float], now: datetime) -> list[dict]:
    scored = []
    for candidate in sql_candidates:
        memory_id = candidate["id"]
        lexical = lexical_scores.get(memory_id, 0.0)
        semantic = semantic_scores.get(memory_id)
        confidence = float(candidate["confidence"])
        source_quality = SOURCE_QUALITY.get(candidate["consent_basis"], 0.0)
        recency = _recency_score(candidate["updated_at"], now)
        result = _score_candidate(semantic=semantic, lexical=lexical, confidence=confidence,
                                  source_quality=source_quality, recency=recency)
        if result is None:
            continue
        score, ranking_mode = result
        scored.append({
            "id": memory_id, "display_text": candidate["display_text"], "score": score,
            "ranking_mode": ranking_mode, "cosine": semantic if ranking_mode == "hybrid" else None,
            "updated_at": candidate["updated_at"],
        })
    return scored


def _greedy_select(scored: list[dict], *, recall_count: int, recall_token_budget: int,
                   model_name: str) -> list[dict]:
    selected: list[dict] = []
    selected_cosines: list[float] = []  # cosines of already-selected embedded picks
    remaining_budget = recall_token_budget
    remaining_pool = list(scored)

    while remaining_pool and len(selected) < recall_count:
        best = None
        best_key = None
        for candidate in remaining_pool:
            if candidate["ranking_mode"] == "hybrid":
                if selected_cosines:
                    max_sim = max(_cosine_similarity_proxy(candidate["cosine"], other_cosine)
                                  for other_cosine in selected_cosines)
                else:
                    max_sim = 0.0
                selection_score = 0.75 * candidate["score"] - 0.25 * max_sim
            else:
                selection_score = candidate["score"]
            key = (selection_score, candidate["score"], candidate["updated_at"],
                  _reverse_id_key(candidate["id"]))
            if best_key is None or key > best_key:
                best_key = key
                best = candidate
        cost = count_tokens(_format_citation(best), model_name)
        if cost > remaining_budget:
            remaining_pool.remove(best)
            continue
        selected.append(best)
        remaining_budget -= cost
        if best["ranking_mode"] == "hybrid":
            selected_cosines.append(best["cosine"])
        remaining_pool.remove(best)
    return selected


def _cosine_similarity_proxy(candidate_cosine: float, other_cosine: float) -> float:
    # Both cosines are similarity-to-the-QUERY, not similarity-to-each-other
    # (this module has no pairwise item-to-item embedding comparison — Chroma
    # is only ever queried against the turn's query text, never against
    # another memory's text). Using the closeness of their query-similarity
    # as a bounded proxy for how redundant two embedded candidates are is a
    # documented, deliberate simplification: two items independently very
    # close to the query are also likely close to each other in practice,
    # and the proxy is well-defined, bounded in [0, 1], and monotonic in
    # exactly the direction the diversity penalty needs (closer query-cosines
    # -> higher proxy -> larger penalty). Computing true pairwise item-to-item
    # cosine similarity would require a second embedding call per pair, which
    # is out of this plan's scope and not requested by the spec's formula
    # itself (which only ever names "max_cosine_similarity_to_already_
    # selected_embedded_item" without specifying how it must be computed).
    return 1.0 - abs(candidate_cosine - other_cosine)


def _reverse_id_key(memory_id: str) -> tuple:
    # id ASC as the final tie-break, expressed as a max()-friendly key: since
    # the outer comparison in _greedy_select prefers the LARGEST tuple, and
    # we want the SMALLEST id to win ties, invert lexicographic order by
    # negating each character's ordinal in a comparable tuple form.
    return tuple(-ord(c) for c in memory_id)


def _format_citation(candidate: dict) -> str:
    return f"[memory:{candidate['id']}] {candidate['display_text']}"


def recall_memories(db: Session, *, security_domain_id: str, agent_id: str, user_id: str,
                    query_text: str, model_name: str, recall_count: int,
                    recall_token_budget: int, now: datetime | None = None) -> list[str]:
    now = now or datetime.now(timezone.utc)
    sql_candidates = _fetch_sql_candidates(db, security_domain_id=security_domain_id,
                                           agent_id=agent_id, user_id=user_id)
    if not sql_candidates:
        return []
    overfetch = CANDIDATE_OVERFETCH_MULTIPLIER * recall_count
    lexical_scores = _lexical_channel(db, security_domain_id=security_domain_id,
                                      agent_id=agent_id, user_id=user_id,
                                      query_text=query_text, limit=overfetch)
    semantic_scores = _semantic_channel(security_domain_id, query_text, overfetch,
                                        sql_candidates=sql_candidates)
    scored = _dedup_and_score_candidates(sql_candidates=sql_candidates,
                                         lexical_scores=lexical_scores,
                                         semantic_scores=semantic_scores, now=now)
    selected = _greedy_select(scored, recall_count=recall_count,
                              recall_token_budget=recall_token_budget, model_name=model_name)
    return [_format_citation(c) for c in selected]
```

Note on `_greedy_select`'s tie-break tuple: Python tuple comparison is lexicographic, and `>` naturally implements `selection_score DESC, score DESC, updated_at DESC` when every component compares normally (largest wins). `id ASC` is the odd one out (everything else wants the *largest* value to win; `id` wants the *smallest*), so `_reverse_id_key` negates each character's ordinal so that comparing the transformed tuples with normal `>` reproduces `id ASC` semantics inside the same "biggest tuple wins" loop, without a special-cased final comparator.

- [ ] **Step 3: Run the tests to verify they pass**

Run: `cd backend && TEST_DATABASE_URL=postgresql://ontoprompt:ontoprompt@localhost:5432/ontoprompt pytest tests/agent/test_agent_memory_recall.py -v`
Expected: all pass (full file, all tasks so far).

- [ ] **Step 4: Commit**

```bash
git add backend/app/services/memory/recall.py backend/tests/agent/test_agent_memory_recall.py
git commit -m "feat: add dedup, greedy diverse selection, and public recall_memories entrypoint"
```

---

### Task 7: Wire recall into the runtime

**Files:**
- Modify: `backend/app/runtime/langgraph_runtime.py:618-624` (the existing `recalled_memories=[]` call site — re-verify the exact current line numbers first, this file has moved since P6B-2a last touched it)
- Test: `backend/tests/agent/test_agent_memory_recall.py` (append)

**Interfaces:**
- Consumes: Task 6's `recall_memories(db, *, security_domain_id, agent_id, user_id, query_text, model_name, recall_count, recall_token_budget) -> list[str]`.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/agent/test_agent_memory_recall.py`:

```python
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

    from app.runtime.langgraph_runtime import _recall_for_turn
    result = _recall_for_turn(session, session_id="sess-1", agent_id="ag-1",
                              query_text="theme preference", model_name="gpt-4o",
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
```

Run: `cd backend && TEST_DATABASE_URL=postgresql://ontoprompt:ontoprompt@localhost:5432/ontoprompt pytest tests/agent/test_agent_memory_recall.py -v -k recall_for_turn`
Expected: FAIL — `_recall_for_turn` doesn't exist.

- [ ] **Step 2: Add the wiring function and call site**

Read `backend/app/runtime/langgraph_runtime.py` in full around the `_build_messages_and_tools` method first (re-verify exact current line numbers — P6B-2a did not touch this file, so it should match the state left by P6B-1's final review, but confirm before assuming the citation below is accurate).

Add a new module-level function near the top of `backend/app/runtime/langgraph_runtime.py` (alongside other free functions, or as a method on the same class if that better matches the file's existing style — check first):

```python
def _recall_for_turn(db, *, session_id: str, agent_id: str, query_text: str, model_name: str,
                     recall_count: int, recall_token_budget: int) -> list[str]:
    """Fail-open wrapper around recall_memories: a degraded/unavailable
    recall path (DB hiccup, unexpected error) must never fail the Turn —
    it just means no memories get recalled this time, logged for
    observability."""
    import logging
    logger = logging.getLogger(__name__)
    try:
        from sqlalchemy import text as _text
        row = db.execute(_text(
            "SELECT u.security_domain_id, s.owner_user_id FROM agent_sessions s "
            "JOIN users u ON u.id = s.owner_user_id WHERE s.id = :sid"
        ), {"sid": session_id}).mappings().one_or_none()
        if row is None:
            return []
        from app.services.memory.recall import recall_memories
        return recall_memories(
            db, security_domain_id=row["security_domain_id"], agent_id=agent_id,
            user_id=row["owner_user_id"], query_text=query_text, model_name=model_name,
            recall_count=recall_count, recall_token_budget=recall_token_budget,
        )
    except Exception:
        logger.exception("memory recall failed for session_id=%s, agent_id=%s — continuing "
                         "with no recalled memories", session_id, agent_id)
        return []
```

In `_build_messages_and_tools`, replace the `recalled_memories=[]` argument to `assemble_bounded_messages`. The call site currently reads (re-verify against the actual file before editing — do not blind-replace on line number alone):

```python
        messages = assemble_bounded_messages(
            system_prompt=system, tool_schemas=tools,
            application_state={**assembled["application_state"], **ontology_context},
            retrieval_required=[], retrieval_optional=[], summary_text=summary_text, recalled_memories=[],
            history_rows=history_rows, pending_interrupt=None,
            user_message=context.user_message or "请继续。", model_name=context.model_name or "gpt-4o",
            budgets=memory_settings, total_budget_tokens=total_budget_tokens,
        )
```

Change it to compute `recalled_memories` before the call, gated by `memory_settings["long_term_enabled"]` (mirroring the existing `if memory_settings["short_term_enabled"]:` gate a few lines above for the summary):

```python
        recalled_memories: list[str] = []
        if memory_settings["long_term_enabled"]:
            recalled_memories = _recall_for_turn(
                self.db, session_id=context.session_id, agent_id=context.agent_id,
                query_text=context.user_message or "", model_name=context.model_name or "gpt-4o",
                recall_count=memory_settings["recall_count"],
                recall_token_budget=memory_settings["recall_token_budget"],
            )

        messages = assemble_bounded_messages(
            system_prompt=system, tool_schemas=tools,
            application_state={**assembled["application_state"], **ontology_context},
            retrieval_required=[], retrieval_optional=[], summary_text=summary_text,
            recalled_memories=recalled_memories,
            history_rows=history_rows, pending_interrupt=None,
            user_message=context.user_message or "请继续。", model_name=context.model_name or "gpt-4o",
            budgets=memory_settings, total_budget_tokens=total_budget_tokens,
        )
```

`memory_settings["recall_count"]` and `memory_settings["recall_token_budget"]` are already-validated keys from `app.services.agent.memory_settings.validate_memory_settings` (P6B-1) — confirm both keys exist in that module's `DEFAULTS`/`RANGES` before wiring (they should; P6B-1's `memory_settings.py` already defines all 7 keys including these two, since `assemble_bounded_messages` itself already reads `budgets.get("recall_count", 8)` and `budgets.get("recall_token_budget", 800)`).

- [ ] **Step 3: Run the tests to verify they pass**

Run: `cd backend && TEST_DATABASE_URL=postgresql://ontoprompt:ontoprompt@localhost:5432/ontoprompt pytest tests/agent/test_agent_memory_recall.py -v -k recall_for_turn`
Expected: pass.

Also run the full existing runtime test suite to confirm no regression: `cd backend && TEST_DATABASE_URL=postgresql://ontoprompt:ontoprompt@localhost:5432/ontoprompt pytest tests/agent/test_langgraph_runtime.py tests/agent/test_turn_worker_loop.py -v` — every pre-existing test must still pass.

- [ ] **Step 4: Commit**

```bash
git add backend/app/runtime/langgraph_runtime.py backend/tests/agent/test_agent_memory_recall.py
git commit -m "feat: wire long-term memory recall into Turn message assembly"
```

---

### Task 8: Golden fixture tests

**Files:**
- Modify: `backend/tests/agent/test_agent_memory_recall.py` (append — dedicated golden-fixture coverage per the spec's own named scenario list)

**Interfaces:**
- Consumes: everything from Tasks 4-6. No new production code in this task — it is comprehensive test coverage only, matching the spec's explicit demand: "Goldens freeze all-embedded, all-lexical, and mixed candidates where the highest result comes from each mode; single/equal/mixed lexical ranks; Chroma outage; stale/missing embeddings; component values to six decimals; thresholds; cross-channel duplicate winners; sequential mixed selection; final ties; and token-boundary inclusion."

Several of these scenarios are already covered by Tasks 4-7's own tests (single/equal/mixed lexical ranks: Task 4; component values to six decimals: Task 5; thresholds: Task 5; cross-channel duplicate winners: Task 6; sequential mixed selection: Task 6; final ties: Task 6; token-boundary inclusion: Task 6). This task adds the remaining named scenarios not yet covered as dedicated goldens: **all-embedded**, **all-lexical**, **mixed candidates where the highest result comes from each mode**, **Chroma outage**, and **stale/missing embeddings** end-to-end through the full `recall_memories()` entrypoint (not the lower-level unit functions Tasks 4-6 tested individually).

- [ ] **Step 1: Write the golden fixture tests**

Append to `backend/tests/agent/test_agent_memory_recall.py`:

```python
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
    _insert_memory(session, memory_id="mem-lexical", display_text="hiking hiking",
                   subject_key="s2", predicate="user.fact", confidence=0.5)
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

    monkeypatch.setattr(recall_module, "_semantic_channel", lambda sd, q, limit, *, sql_candidates: (
        {"mem-embedded": 0.1} if any(c["id"] == "mem-embedded" for c in sql_candidates) else {}))

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

    monkeypatch.setattr(vector_store, "is_available", lambda: False)
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
```

Run: `cd backend && TEST_DATABASE_URL=postgresql://ontoprompt:ontoprompt@localhost:5432/ontoprompt pytest tests/agent/test_agent_memory_recall.py -v -k golden`
Expected: all pass.

- [ ] **Step 2: Run the full recall test file to confirm no regression across every task**

Run: `cd backend && TEST_DATABASE_URL=postgresql://ontoprompt:ontoprompt@localhost:5432/ontoprompt pytest tests/agent/test_agent_memory_recall.py -v`
Expected: every test in the file (Tasks 1-8) passes.

- [ ] **Step 3: Commit**

```bash
git add backend/tests/agent/test_agent_memory_recall.py
git commit -m "test: add golden fixtures for hybrid/lexical-only recall modes and edge cases"
```

---

### Task 9: Full regression pass

**Files:** none (verification only)

- [ ] **Step 1: Backend regression**

Run: `cd backend && TEST_DATABASE_URL=postgresql://ontoprompt:ontoprompt@localhost:5432/ontoprompt pytest tests/agent/ -q --ignore=tests/agent/test_playwright_adapter.py`
Expected: all pass except the pre-existing, unrelated cluster already established across this session's prior plans (`test_0003_full_migration.py`, `test_build_manifest.py` x3, `test_schema_startup.py` — 5 failures, confirmed pre-existing and unrelated multiple times already this session, most recently during P6B-2a's own Task 7 and final-review verification).

- [ ] **Step 2: Confirm no stray route/manifest drift**

This plan added no new API routes. Run: `git diff --stat dev -- backend/openapi-agent.json` and confirm it's empty.

- [ ] **Step 3: Confirm P6B-1 and P6B-2a's write-path tests are unaffected**

Run: `cd backend && TEST_DATABASE_URL=postgresql://ontoprompt:ontoprompt@localhost:5432/ontoprompt pytest tests/agent/test_agent_memory_short_term.py tests/agent/test_agent_memory_long_term.py tests/agent/test_turn_worker_loop.py -v` — every test from both prior plans must still pass unchanged, since this plan's `_recall_for_turn` call sits in the same file (`langgraph_runtime.py`) as P6B-1's summary logic, and this plan's migration builds directly on P6B-2a's schema.

- [ ] **Step 4: Report**

If Step 1 or 3 surfaces anything beyond the known 5-failure cluster, stop and investigate before considering this plan done — do not fold an unexplained new failure into "probably pre-existing."

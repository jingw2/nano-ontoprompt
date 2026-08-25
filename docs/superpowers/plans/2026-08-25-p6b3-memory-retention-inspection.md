# P6B-3: Memory Retention Extension + Inspection/Correction/Deletion API + UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close out the Agent long-term memory feature: extend the fixed-policy purge to its 12th (final) step by permanently cleaning up long-tombstoned long-term memories, and give end users a real API + UI to inspect, confirm/reject, correct, delete, and resolve conflicts among their own long-term memories — the two pieces the spec explicitly deferred to "P6B" after P6B-1/2a/2b shipped settings, write, and recall.

**Architecture:** A new 12th purge step in the existing `run_fixed_purge` function hard-deletes long-tombstoned `agent_memories` rows (and their FK-dependent children) past a fixed retention window. A new service module (`backend/app/services/memory/inspection.py`) implements list/get/confirm/reject/correct/delete/resolve-conflict as plain functions over the already-merged `agent_memories`/`agent_memory_revisions`/`agent_memory_consents`/`agent_memory_conflicts`/`agent_memory_vector_outbox` tables, reusing P6B-2a's `consent.py`/`predicate_registry.py` rather than reimplementing consent/cardinality logic. A new router (`backend/app/routers/agent_memories.py`) exposes these over HTTP following this codebase's existing `agent_approvals.py`/`agent_clarifications.py` convention (self-contained router, CAS-free since memory mutation doesn't need optimistic concurrency the way approvals do, existence-hiding scoping by `user_id = current_user.id`). A new frontend drawer component replaces `MemoryConfigTab.tsx`'s static "inspection unavailable" placeholder with a real list+detail+action UI.

**Tech Stack:** Python 3.11+, FastAPI, SQLAlchemy Core (raw `text()` SQL, matching every prior memory-service module), PostgreSQL; React, TypeScript, the existing `apiClient` axios wrapper (no React Query, no headless-UI library — hand-rolled drawer matching `CapabilityDrawer.tsx`'s pattern).

**Spec:** `docs/superpowers/plans/2026-08-09-agent-ontology-implementation.md`. Retention: Section 7 (line 580, describing the MVP ten-step order and naming "P6A/B replaces this with the separately tested twelve-step order by adding policy/hold/epoch and memory tombstone/vector/provenance steps") and the "Normative runtime decisions" section (line 1247, "the exact twelve-step child/FK/redaction/deletion order"). Inspection/correction/deletion: Section 12.1 (line 713, "P6B adds inspect/correct/delete and turns on the memory APIs"; line 783's screen-contract table row for the memory inspection drawer), Section 13.1's Phase 6 row (line 861, naming stable error codes `MEMORY_CONSENT_REQUIRED`/`MEMORY_CONFLICT`/`MEMORY_CARDINALITY_EXCEEDED`/`MEMORY_POLICY_REJECTED`), and Phase 6's narrative (line 1132, "Deliver memory inspection/correction/deletion drawer, consent/retention controls, and reconciliation state"). This plan implements exactly those pieces; the write path (extraction/canonicalization/consent/conflict creation), recall path (Chroma/hybrid ranking), and short-term memory (summary/budget) were already implemented by P6B-1/2a/2b, all merged to `dev`.

## Global Constraints

- Every new FK/reference in this schema area must respect the existing RESTRICT-only discipline already established (`agent_memory_revisions.memory_id`, `agent_memory_conflicts.memory_id_a`/`memory_id_b`, `agent_memory_vector_outbox.memory_id` all reference `agent_memories.id` with `ondelete="RESTRICT"`) — no new migration in this plan adds a table, but the new purge step (Task 1) must respect this: a hard-delete of an `agent_memories` row requires first deleting its dependent `agent_memory_vector_outbox`/`agent_memory_conflicts`/`agent_memory_revisions` rows, in that order.
- `agent_memories.status` enum is exactly `pending_confirmation|active|conflicted|deleted` (migration `0019_agent_memory_long_term.py`'s `CHECK` constraint) — no new status value is introduced by this plan.
- Authorization model: every new service function and router endpoint in this plan is scoped by `WHERE user_id = :current_user_id` (self-service — a user manages only their own long-term memories, the same "consented inspection" framing the spec uses) — never by an agent-operator access-grant check. A request for a memory belonging to a different user returns the same existence-hiding 404 this codebase's `agent_approvals.py` already establishes as convention ("others get existence-hiding 404" — never 403).
- Stable error codes: this plan introduces exactly two new ones, `MEMORY_CONSENT_REQUIRED` and `MEMORY_CONFLICT`, following the exact "exception-class-whose-message-is-the-code" idiom already used by `MemorySettingsError`/`PredicateRegistryError` (`backend/app/services/agent/memory_settings.py`, `backend/app/services/memory/predicate_registry.py`) — a bare `Exception` subclass, raised with the code string as the message, caught by the router and turned into `HTTPException(status_code, detail=str(exc))`. The two already-existing codes (`MEMORY_POLICY_REJECTED`, `MEMORY_CARDINALITY_EXCEEDED`, both in `predicate_registry.py`) are reused as-is where applicable, never redefined.
- Wire error shape: this codebase's actual, verified backend error contract is `{"detail": "<CODE>"}` (confirmed directly against existing backend test assertions, e.g. `test_application_state_audit_api.py`'s `assert r.json()["detail"] == "APPLICATION_STATE_CONFLICT"`) via FastAPI's stock `HTTPException` — there is no global custom exception handler in `main.py`. Frontend code in this plan must parse `(err as {detail?: string})?.detail`, NOT the `(err as {error?: {code?: string}})?.error?.code` idiom several older frontend components use — that idiom does not match what `client.ts`'s response interceptor actually delivers (`Promise.reject(err.response?.data ?? err)`, i.e. the raw `{"detail": ...}` body). This is a known, pre-existing inconsistency elsewhere in the codebase that this plan does not fix retroactively — it only avoids copying the broken pattern into new code.
- Semantics for the write-path deferral P6B-2a explicitly left for this plan (from `extraction.py`'s own comment, already merged): a memory candidate with `consent_basis == "explicit_confirmation"` is written with `status = "pending_confirmation"` and `consent_id = NULL` on its revision — "P6B-3's confirm-candidate action is responsible for calling `grant_consent()` for real at the moment the user actually confirms." Task 3 of this plan is exactly that confirm action.
- A memory's embedding lifecycle must always be kept consistent with its status via the existing `agent_memory_vector_outbox` mechanism (P6B-2a/2b's pattern: `event_type='upsert'` when a memory becomes/stays `active` with changed content, `event_type='delete'` when a memory leaves `active` state permanently) — every mutating action in this plan (confirm, correct, delete, resolve-conflict) must enqueue the correct outbox event, never leave a stale Chroma vector unaccounted for.
- Retention purge step 12 uses a fixed 30-day tombstone-retention window (a hardcoded default, mirroring the existing step 7's own hardcoded "30 days" pattern for delivered dispatch rows) — this plan does not introduce a new configurable retention-policy class-action for memories; that would be a materially larger scope than "add the 12th step."

---

### Task 1: Retention — 12th purge step (long-term memory hard-delete)

**Files:**
- Modify: `backend/app/services/retention/fixed_policy.py`
- Test: `backend/tests/agent/test_fixed_retention.py`

**Interfaces:**
- Consumes: nothing new — operates directly on already-merged tables (`agent_memories`, `agent_memory_revisions`, `agent_memory_conflicts`, `agent_memory_vector_outbox`).
- Produces: `PURGE_STEPS` grows to 12 entries (new final entry `"purge_expired_long_term_memories"`); `run_fixed_purge`'s returned `ledger` dict gains a `"purge_expired_long_term_memories"` key.

- [ ] **Step 1: Read the current file in full first**

Read `backend/app/services/retention/fixed_policy.py` in its entirety before editing — re-verify the exact current line numbers and code around `PURGE_STEPS`, `delete_memory_summaries`'s block, and the final `db.commit()` in `run_fixed_purge`, since this plan's excerpt below may have drifted from the actual current file.

- [ ] **Step 2: Write the failing test**

Read `backend/tests/agent/test_fixed_retention.py`'s existing `schema`/`session` fixture pattern first (it should already be pinned to a `HEAD` migration constant — confirm it's still `0019_agent_memory_long_term` or later; if a later migration has landed since, bump the pin the same way this session's prior plans have done every time this exact staleness issue was hit). Append:

```python
def test_purge_hard_deletes_long_tombstoned_memories_and_children(session):
    from datetime import datetime, timedelta, timezone
    import json
    old_deleted_at = datetime.now(timezone.utc) - timedelta(days=31)
    recent_deleted_at = datetime.now(timezone.utc) - timedelta(days=5)

    def _insert_memory(memory_id, status, deleted_at=None):
        session.execute(text(
            "INSERT INTO agent_memories (id, security_domain_id, agent_id, user_id, kind, "
            "subject_key, predicate, canonical_value, canonical_value_hash, display_text, "
            "confidence, sensitivity, consent_basis, source_spans, status, deleted_at, "
            "created_at, updated_at) "
            "VALUES (:id, :d, :a, :u, 'semantic', :sk, 'user.name', '\"x\"'::jsonb, :hash, "
            "'fact', 0.9, 'low', 'explicit_statement', '[0]'::jsonb, :status, :deleted_at, "
            "now(), now())"
        ), {"id": memory_id, "d": DEFAULT_DOMAIN, "a": "ag-1", "u": "u-1", "sk": memory_id,
            "hash": f"hash-{memory_id}", "status": status, "deleted_at": deleted_at})

    _insert_memory("mem-old-deleted", "deleted", old_deleted_at)
    _insert_memory("mem-recent-deleted", "deleted", recent_deleted_at)
    _insert_memory("mem-active", "active", None)
    session.execute(text(
        "INSERT INTO agent_memory_revisions (id, memory_id, revision_no, canonical_value, "
        "display_text, confidence, consent_basis, source_spans, created_by, created_at) "
        "VALUES ('rev-old', 'mem-old-deleted', 1, '\"x\"'::jsonb, 'fact', 0.9, "
        "'explicit_statement', '[0]'::jsonb, 'u-1', now())"
    ))
    session.execute(text(
        "INSERT INTO agent_memory_vector_outbox (id, memory_id, event_type, state, created_at) "
        "VALUES ('vo-old', 'mem-old-deleted', 'delete', 'applied', now())"
    ))
    session.commit()

    from app.services.retention.fixed_policy import run_fixed_purge, claim_purge_job
    job = claim_purge_job(session, security_domain_id=DEFAULT_DOMAIN, purge_class="fixed")
    ledger = run_fixed_purge(session, security_domain_id=DEFAULT_DOMAIN,
                             job_id=job["id"], claim_token=job["claim_token"])

    assert ledger["purge_expired_long_term_memories"] == 1
    remaining_ids = {r["id"] for r in session.execute(text(
        "SELECT id FROM agent_memories"
    )).mappings().all()}
    assert remaining_ids == {"mem-recent-deleted", "mem-active"}
    assert session.execute(text(
        "SELECT count(*) FROM agent_memory_revisions WHERE memory_id = 'mem-old-deleted'"
    )).scalar_one() == 0
    assert session.execute(text(
        "SELECT count(*) FROM agent_memory_vector_outbox WHERE memory_id = 'mem-old-deleted'"
    )).scalar_one() == 0


def test_purge_never_deletes_memory_still_part_of_open_conflict(session):
    from datetime import datetime, timedelta, timezone
    old_deleted_at = datetime.now(timezone.utc) - timedelta(days=31)

    def _insert_memory(memory_id, status, deleted_at=None):
        session.execute(text(
            "INSERT INTO agent_memories (id, security_domain_id, agent_id, user_id, kind, "
            "subject_key, predicate, canonical_value, canonical_value_hash, display_text, "
            "confidence, sensitivity, consent_basis, source_spans, status, deleted_at, "
            "created_at, updated_at) "
            "VALUES (:id, :d, :a, :u, 'semantic', 's1', 'user.name', '\"x\"'::jsonb, :hash, "
            "'fact', 0.9, 'low', 'explicit_statement', '[0]'::jsonb, :status, :deleted_at, "
            "now(), now())"
        ), {"id": memory_id, "d": DEFAULT_DOMAIN, "a": "ag-1", "u": "u-1",
            "hash": f"hash-{memory_id}", "status": status, "deleted_at": deleted_at})

    _insert_memory("mem-a", "deleted", old_deleted_at)
    _insert_memory("mem-b", "conflicted", None)
    session.execute(text(
        "INSERT INTO agent_memory_conflicts (id, security_domain_id, agent_id, user_id, "
        "subject_key, predicate, memory_id_a, memory_id_b, status, created_at) "
        "VALUES ('conf-1', :d, 'ag-1', 'u-1', 's1', 'user.name', 'mem-a', 'mem-b', 'open', now())"
    ), {"d": DEFAULT_DOMAIN})
    session.commit()

    from app.services.retention.fixed_policy import run_fixed_purge, claim_purge_job
    job = claim_purge_job(session, security_domain_id=DEFAULT_DOMAIN, purge_class="fixed")
    ledger = run_fixed_purge(session, security_domain_id=DEFAULT_DOMAIN,
                             job_id=job["id"], claim_token=job["claim_token"])

    assert ledger["purge_expired_long_term_memories"] == 0
    assert session.execute(text(
        "SELECT count(*) FROM agent_memories WHERE id = 'mem-a'"
    )).scalar_one() == 1
```

Run: `cd backend && TEST_DATABASE_URL=postgresql://ontoprompt:ontoprompt@localhost:5432/ontoprompt pytest tests/agent/test_fixed_retention.py -v -k "purge_expired_long_term or purge_never_deletes"`
Expected: FAIL — `ledger["purge_expired_long_term_memories"]` doesn't exist (`KeyError`).

- [ ] **Step 3: Implement the 12th step**

In `backend/app/services/retention/fixed_policy.py`, add the new step name to `PURGE_STEPS`:

```python
PURGE_STEPS = (
    "redact_payloads",
    "delete_expired_stream_tickets",
    "delete_resolved_clarifications",
    "redact_runtime_content",
    "delete_model_and_node_rows",
    "delete_checkpoint_rows",
    "delete_delivered_outbox",
    "delete_messages_turn_marker",
    "delete_memory_summaries",
    "clear_session_pointer",
    "graph_index_cleanup",
    "purge_expired_long_term_memories",
)
```

Immediately before the final `db.commit()` in `run_fixed_purge` (after the existing `graph_index_cleanup` step's block — verify this is genuinely the last step before commit by reading the actual current function body), add:

```python
    # 12. Long-term memory: hard-delete memories tombstoned (status='deleted')
    # more than 30 days ago, plus their FK-dependent children, in RESTRICT-safe
    # order (vector_outbox -> conflicts -> revisions -> memory row itself).
    # Never purges a memory still referenced by an OPEN conflict, even if
    # somehow flagged deleted -- by construction a memory only reaches
    # 'deleted' after any conflict it was part of is already resolved, but
    # this is a defensive guard, not an assumption.
    memory_cutoff = now - timedelta(days=30)
    candidate_ids = [r[0] for r in db.execute(text(
        "SELECT m.id FROM agent_memories m "
        "WHERE m.security_domain_id = :domain AND m.status = 'deleted' "
        "AND m.deleted_at < :cutoff "
        "AND NOT EXISTS (SELECT 1 FROM agent_memory_conflicts c "
        "  WHERE (c.memory_id_a = m.id OR c.memory_id_b = m.id) AND c.status = 'open')"
    ), {"domain": security_domain_id, "cutoff": memory_cutoff}).fetchall()]
    if candidate_ids:
        db.execute(text(
            "DELETE FROM agent_memory_vector_outbox WHERE memory_id = ANY(:ids)"
        ), {"ids": candidate_ids})
        db.execute(text(
            "DELETE FROM agent_memory_conflicts WHERE memory_id_a = ANY(:ids) "
            "OR memory_id_b = ANY(:ids)"
        ), {"ids": candidate_ids})
        db.execute(text(
            "DELETE FROM agent_memory_revisions WHERE memory_id = ANY(:ids)"
        ), {"ids": candidate_ids})
        result = db.execute(text(
            "DELETE FROM agent_memories WHERE id = ANY(:ids)"
        ), {"ids": candidate_ids})
        ledger["purge_expired_long_term_memories"] = result.rowcount or 0
    else:
        ledger["purge_expired_long_term_memories"] = 0
```

Note: `now` and `security_domain_id` are already in scope inside `run_fixed_purge` (used by every prior step) — confirm the exact local variable names by reading the function signature/body directly rather than assuming they match this snippet verbatim.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && TEST_DATABASE_URL=postgresql://ontoprompt:ontoprompt@localhost:5432/ontoprompt pytest tests/agent/test_fixed_retention.py -v`
Expected: all pass, including every pre-existing test in this file (11-step behavior must be completely unaffected by the 12th step's addition).

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/retention/fixed_policy.py backend/tests/agent/test_fixed_retention.py
git commit -m "feat: add 12th retention purge step for long-tombstoned long-term memories"
```

---

### Task 2: Inspection service — list and get

**Files:**
- Create: `backend/app/services/memory/inspection.py`
- Test: `backend/tests/agent/test_agent_memory_inspection.py` (new — every later task in this plan appends to this same file)

**Interfaces:**
- Produces: `list_memories(db, *, user_id, agent_id, status=None) -> list[dict]` (each dict: `id, subject_key, predicate, display_text, confidence, sensitivity, status, consent_basis, created_at, updated_at`), `get_memory(db, *, user_id, memory_id) -> dict | None` (adds `canonical_value`, `revisions: list[dict]` — each revision: `revision_no, display_text, confidence, consent_basis, created_at, superseded_at`, `conflict: dict | None` if the memory is currently `conflicted` — `{"conflict_id", "other_memory_id", "other_display_text"}`, and `embedding_status: str` — the spec's "reconciliation state" surfaced in the drawer, see Step 3 for the exact derivation).

- [ ] **Step 1: Read the precedent files first**

Read `backend/tests/agent/test_agent_memory_long_term.py`'s `session`/`DEFAULT_DOMAIN`/baseline-seed fixture pattern (the established precedent this whole P6B memory work has copied verbatim in every prior plan) before writing anything — this task's fixture is a close copy, not a new invention.

- [ ] **Step 2: Write the failing tests**

Create `backend/tests/agent/test_agent_memory_inspection.py`:

```python
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
    revision_texts = {r["display_text"] for r in result["revisions"]}
    assert revision_texts == {"original", "stale"}


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
```

Run: `cd backend && TEST_DATABASE_URL=postgresql://ontoprompt:ontoprompt@localhost:5432/ontoprompt pytest tests/agent/test_agent_memory_inspection.py -v`
Expected: FAIL — module doesn't exist.

- [ ] **Step 3: Implement list/get**

Create `backend/app/services/memory/inspection.py`:

```python
"""Memory inspection/correction/deletion (P6B-3, Section 12.1).

Self-service surface over the already-merged long-term memory write path
(P6B-2a) and recall path (P6B-2b): a user inspects, confirms/rejects,
corrects, deletes, and resolves conflicts among their OWN memories.
Authorization is always scoped by user_id -- never an agent-operator
access-grant check, matching the spec's "consented inspection" framing.
"""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session


class MemoryConsentRequiredError(Exception):
    """MEMORY_CONSENT_REQUIRED: a confirm action was attempted without
    the caller's explicit affirmative consent flag."""


class MemoryConflictError(Exception):
    """MEMORY_CONFLICT: an action was attempted on a memory that is
    currently in an open conflict and must be resolved first."""


def list_memories(db: Session, *, user_id: str, agent_id: str, status: str | None = None) -> list[dict]:
    query = (
        "SELECT id, subject_key, predicate, display_text, confidence, sensitivity, status, "
        "consent_basis, created_at, updated_at FROM agent_memories "
        "WHERE user_id = :u AND agent_id = :a AND status != 'deleted'"
    )
    params = {"u": user_id, "a": agent_id}
    if status is not None:
        query += " AND status = :status"
        params["status"] = status
    query += " ORDER BY updated_at DESC"
    rows = db.execute(text(query), params).mappings().all()
    return [dict(r) for r in rows]


def _embedding_status(db: Session, *, memory_id: str, embedding_model_version: str | None) -> str:
    """The spec's "reconciliation state" surfaced in the inspection drawer --
    derived read-only from already-authoritative SQL columns, not a new
    Chroma-querying reconciliation job (no such job exists anywhere in this
    codebase to build on; querying live Chroma state from a request handler
    would also violate the "SQL is authoritative, every recall hit is
    SQL-refetched" invariant this whole memory subsystem is built on)."""
    pending = db.execute(text(
        "SELECT 1 FROM agent_memory_vector_outbox WHERE memory_id = :id AND state = 'pending' "
        "LIMIT 1"
    ), {"id": memory_id}).scalar_one_or_none()
    if pending:
        return "pending"
    from app.services.memory.vector_store import MEMORY_EMBEDDING_MODEL_VERSION
    if embedding_model_version == MEMORY_EMBEDDING_MODEL_VERSION:
        return "current"
    return "never_embedded"


def get_memory(db: Session, *, user_id: str, memory_id: str) -> dict | None:
    row = db.execute(text(
        "SELECT id, subject_key, predicate, canonical_value, display_text, confidence, "
        "sensitivity, status, consent_basis, agent_id, embedding_model_version, "
        "created_at, updated_at "
        "FROM agent_memories WHERE id = :id AND user_id = :u"
    ), {"id": memory_id, "u": user_id}).mappings().one_or_none()
    if row is None:
        return None
    result = dict(row)
    result["embedding_status"] = _embedding_status(
        db, memory_id=memory_id, embedding_model_version=row["embedding_model_version"])
    revisions = db.execute(text(
        "SELECT revision_no, display_text, confidence, consent_basis, created_at, superseded_at "
        "FROM agent_memory_revisions WHERE memory_id = :id ORDER BY revision_no DESC"
    ), {"id": memory_id}).mappings().all()
    result["revisions"] = [dict(r) for r in revisions]
    result["conflict"] = None
    if row["status"] == "conflicted":
        conflict_row = db.execute(text(
            "SELECT c.id AS conflict_id, "
            "CASE WHEN c.memory_id_a = :id THEN c.memory_id_b ELSE c.memory_id_a END AS other_memory_id "
            "FROM agent_memory_conflicts c "
            "WHERE (c.memory_id_a = :id OR c.memory_id_b = :id) AND c.status = 'open'"
        ), {"id": memory_id}).mappings().one_or_none()
        if conflict_row is not None:
            other_text = db.execute(text(
                "SELECT display_text FROM agent_memories WHERE id = :id"
            ), {"id": conflict_row["other_memory_id"]}).scalar_one()
            result["conflict"] = {
                "conflict_id": conflict_row["conflict_id"],
                "other_memory_id": conflict_row["other_memory_id"],
                "other_display_text": other_text,
            }
    return result
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && TEST_DATABASE_URL=postgresql://ontoprompt:ontoprompt@localhost:5432/ontoprompt pytest tests/agent/test_agent_memory_inspection.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/memory/inspection.py backend/tests/agent/test_agent_memory_inspection.py
git commit -m "feat: add memory inspection list/get service functions"
```

---

### Task 3: Inspection service — confirm and reject

**Files:**
- Modify: `backend/app/services/memory/inspection.py` (append; do not touch Task 2's functions)
- Test: `backend/tests/agent/test_agent_memory_inspection.py` (append)

**Interfaces:**
- Consumes: `app.services.memory.consent.grant_consent(db, *, security_domain_id, agent_id, user_id, consent_basis, commit=True) -> str` (already merged, P6B-2a).
- Produces: `confirm_memory(db, *, user_id, memory_id, consent: bool) -> dict` (raises `MemoryConsentRequiredError` if `consent` is not `True`; raises `MemoryConflictError` if the memory is `conflicted`; returns the updated memory dict via `get_memory`), `reject_memory(db, *, user_id, memory_id) -> None`.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/agent/test_agent_memory_inspection.py`:

```python
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
```

Run: `cd backend && TEST_DATABASE_URL=postgresql://ontoprompt:ontoprompt@localhost:5432/ontoprompt pytest tests/agent/test_agent_memory_inspection.py -v -k "confirm_memory or reject_memory"`
Expected: FAIL — functions don't exist.

- [ ] **Step 2: Implement confirm/reject**

Append to `backend/app/services/memory/inspection.py`:

```python
import uuid


def _new_id() -> str:
    return str(uuid.uuid4())


def confirm_memory(db: Session, *, user_id: str, memory_id: str, consent: bool) -> dict:
    if not consent:
        raise MemoryConsentRequiredError("MEMORY_CONSENT_REQUIRED")
    row = db.execute(text(
        "SELECT security_domain_id, agent_id, status FROM agent_memories "
        "WHERE id = :id AND user_id = :u"
    ), {"id": memory_id, "u": user_id}).mappings().one_or_none()
    if row is None:
        raise MemoryConsentRequiredError("MEMORY_CONSENT_REQUIRED")
    if row["status"] == "conflicted":
        raise MemoryConflictError("MEMORY_CONFLICT")

    from app.services.memory.consent import grant_consent
    consent_id = grant_consent(db, security_domain_id=row["security_domain_id"],
                               agent_id=row["agent_id"], user_id=user_id,
                               consent_basis="explicit_confirmation", commit=False)
    db.execute(text(
        "UPDATE agent_memories SET status = 'active', updated_at = now() WHERE id = :id"
    ), {"id": memory_id})
    db.execute(text(
        "UPDATE agent_memory_revisions SET consent_id = :cid "
        "WHERE memory_id = :id AND superseded_at IS NULL"
    ), {"cid": consent_id, "id": memory_id})
    db.execute(text(
        "INSERT INTO agent_memory_vector_outbox (id, memory_id, event_type, state, created_at) "
        "VALUES (:id, :mid, 'upsert', 'pending', now())"
    ), {"id": _new_id(), "mid": memory_id})
    db.commit()
    return get_memory(db, user_id=user_id, memory_id=memory_id)


def reject_memory(db: Session, *, user_id: str, memory_id: str) -> None:
    db.execute(text(
        "UPDATE agent_memories SET status = 'deleted', deleted_at = now(), updated_at = now() "
        "WHERE id = :id AND user_id = :u AND status != 'deleted'"
    ), {"id": memory_id, "u": user_id})
    db.commit()
```

Note: `confirm_memory` raises the same `MemoryConsentRequiredError("MEMORY_CONSENT_REQUIRED")` for both "consent flag false" and "memory not found for this user" — this is deliberate existence-hiding (per the Global Constraints' scoping rule): a caller probing for another user's memory ID gets the same error either way, never a distinguishable 404-vs-403-vs-400. The router (Task 6) maps this to a single HTTP status.

- [ ] **Step 3: Run the tests to verify they pass**

Run: `cd backend && TEST_DATABASE_URL=postgresql://ontoprompt:ontoprompt@localhost:5432/ontoprompt pytest tests/agent/test_agent_memory_inspection.py -v`
Expected: all pass (full file, Tasks 2-3).

- [ ] **Step 4: Commit**

```bash
git add backend/app/services/memory/inspection.py backend/tests/agent/test_agent_memory_inspection.py
git commit -m "feat: add memory confirm/reject inspection actions"
```

---

### Task 4: Inspection service — correct and delete

**Files:**
- Modify: `backend/app/services/memory/inspection.py` (append; do not touch Tasks 2-3's functions)
- Test: `backend/tests/agent/test_agent_memory_inspection.py` (append)

**Interfaces:**
- Consumes: `app.services.memory.canonicalizer.canonical_hash(value, value_type) -> str` (already merged, P6B-2a).
- Produces: `correct_memory(db, *, user_id, memory_id, display_text: str, confidence: float | None = None) -> dict | None` (returns `None` if no memory with that id exists for that user — the router maps this to 404, matching `get_memory`'s own `None`-means-404 convention; raises `MemoryConflictError` if the memory is `conflicted`; otherwise supersedes the current revision, updates the memory row in place, enqueues a vector-outbox upsert), `delete_memory(db, *, user_id, memory_id) -> None` (tombstones, enqueues a vector-outbox delete only if the memory was ever embedded).

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/agent/test_agent_memory_inspection.py`:

```python
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
```

Run: `cd backend && TEST_DATABASE_URL=postgresql://ontoprompt:ontoprompt@localhost:5432/ontoprompt pytest tests/agent/test_agent_memory_inspection.py -v -k "correct_memory or delete_memory"`
Expected: FAIL — functions don't exist.

- [ ] **Step 2: Implement correct/delete**

Append to `backend/app/services/memory/inspection.py`:

```python
def correct_memory(db: Session, *, user_id: str, memory_id: str, display_text: str,
                   confidence: float | None = None) -> dict:
    from app.services.memory.canonicalizer import canonical_hash

    row = db.execute(text(
        "SELECT status, confidence, consent_basis FROM agent_memories "
        "WHERE id = :id AND user_id = :u"
    ), {"id": memory_id, "u": user_id}).mappings().one_or_none()
    if row is None:
        return None
    if row["status"] == "conflicted":
        raise MemoryConflictError("MEMORY_CONFLICT")

    final_confidence = confidence if confidence is not None else float(row["confidence"])
    value_hash = canonical_hash(display_text, "corrected_value")
    next_revision_no = db.execute(text(
        "SELECT COALESCE(MAX(revision_no), 0) + 1 FROM agent_memory_revisions WHERE memory_id = :id"
    ), {"id": memory_id}).scalar_one()

    db.execute(text(
        "UPDATE agent_memory_revisions SET superseded_at = now() "
        "WHERE memory_id = :id AND superseded_at IS NULL"
    ), {"id": memory_id})
    db.execute(text(
        "INSERT INTO agent_memory_revisions (id, memory_id, revision_no, canonical_value, "
        "display_text, confidence, consent_basis, source_spans, created_by, created_at) "
        "VALUES (:id, :mid, :rno, CAST(:val AS jsonb), :disp, :conf, :consent_basis, "
        "CAST('[]' AS jsonb), :u, now())"
    ), {"id": _new_id(), "mid": memory_id, "rno": next_revision_no,
        "val": f'"{display_text}"', "disp": display_text, "conf": final_confidence,
        "consent_basis": row["consent_basis"], "u": user_id})
    db.execute(text(
        "UPDATE agent_memories SET display_text = :disp, confidence = :conf, "
        "canonical_value_hash = :hash, updated_at = now() WHERE id = :id"
    ), {"disp": display_text, "conf": final_confidence, "hash": value_hash, "id": memory_id})
    db.execute(text(
        "INSERT INTO agent_memory_vector_outbox (id, memory_id, event_type, state, created_at) "
        "VALUES (:id, :mid, 'upsert', 'pending', now())"
    ), {"id": _new_id(), "mid": memory_id})
    db.commit()
    return get_memory(db, user_id=user_id, memory_id=memory_id)


def delete_memory(db: Session, *, user_id: str, memory_id: str) -> None:
    row = db.execute(text(
        "UPDATE agent_memories SET status = 'deleted', deleted_at = now(), updated_at = now() "
        "WHERE id = :id AND user_id = :u AND status != 'deleted' "
        "RETURNING embedding_model_version"
    ), {"id": memory_id, "u": user_id}).mappings().one_or_none()
    if row is not None and row["embedding_model_version"] is not None:
        db.execute(text(
            "INSERT INTO agent_memory_vector_outbox (id, memory_id, event_type, state, created_at) "
            "VALUES (:id, :mid, 'delete', 'pending', now())"
        ), {"id": _new_id(), "mid": memory_id})
    db.commit()
```

Note: `correct_memory`'s in-place update deliberately keeps the SAME `agent_memories.id` and does not re-check `check_cardinality` — a correction edits an existing row's value, it never adds a new distinct memory, so the multi-valued cardinality cap (which counts distinct active rows) is unaffected by construction. This is a deliberate scope decision: correcting a multi-valued-predicate memory into a value that collides with ANOTHER of the user's own active memories on the same predicate is not handled by this task (no dedup-merge-on-correct) — out of scope, documented here rather than silently unhandled.

- [ ] **Step 3: Run the tests to verify they pass**

Run: `cd backend && TEST_DATABASE_URL=postgresql://ontoprompt:ontoprompt@localhost:5432/ontoprompt pytest tests/agent/test_agent_memory_inspection.py -v`
Expected: all pass (full file, Tasks 2-4).

- [ ] **Step 4: Commit**

```bash
git add backend/app/services/memory/inspection.py backend/tests/agent/test_agent_memory_inspection.py
git commit -m "feat: add memory correct/delete inspection actions"
```

---

### Task 5: Inspection service — resolve conflict + list conflicts

**Files:**
- Modify: `backend/app/services/memory/inspection.py` (append; do not touch Tasks 2-4's functions)
- Test: `backend/tests/agent/test_agent_memory_inspection.py` (append)

**Interfaces:**
- Produces: `list_conflicts(db, *, user_id, agent_id) -> list[dict]` (each dict: `conflict_id, subject_key, predicate, memory_id_a, display_text_a, memory_id_b, display_text_b, created_at`), `resolve_conflict(db, *, user_id, conflict_id, winning_memory_id) -> dict` (raises `MemoryConflictError` if `conflict_id` doesn't exist, isn't open, or `winning_memory_id` isn't one of its two sides, scoped to `user_id`; returns the winning memory via `get_memory`).

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/agent/test_agent_memory_inspection.py`:

```python
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
    _seed_conflict(session, winner_id="mem-a", loser_id="mem-b")

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
```

Run: `cd backend && TEST_DATABASE_URL=postgresql://ontoprompt:ontoprompt@localhost:5432/ontoprompt pytest tests/agent/test_agent_memory_inspection.py -v -k conflict`
Expected: FAIL — functions don't exist.

- [ ] **Step 2: Implement resolve-conflict**

Append to `backend/app/services/memory/inspection.py`:

```python
def list_conflicts(db: Session, *, user_id: str, agent_id: str) -> list[dict]:
    rows = db.execute(text(
        "SELECT c.id AS conflict_id, c.subject_key, c.predicate, c.memory_id_a, "
        "ma.display_text AS display_text_a, c.memory_id_b, mb.display_text AS display_text_b, "
        "c.created_at "
        "FROM agent_memory_conflicts c "
        "JOIN agent_memories ma ON ma.id = c.memory_id_a "
        "JOIN agent_memories mb ON mb.id = c.memory_id_b "
        "WHERE c.user_id = :u AND c.agent_id = :a AND c.status = 'open' "
        "ORDER BY c.created_at DESC"
    ), {"u": user_id, "a": agent_id}).mappings().all()
    return [dict(r) for r in rows]


def resolve_conflict(db: Session, *, user_id: str, conflict_id: str,
                     winning_memory_id: str) -> dict:
    conflict = db.execute(text(
        "SELECT memory_id_a, memory_id_b FROM agent_memory_conflicts "
        "WHERE id = :id AND user_id = :u AND status = 'open'"
    ), {"id": conflict_id, "u": user_id}).mappings().one_or_none()
    if conflict is None:
        raise MemoryConflictError("MEMORY_CONFLICT")
    if winning_memory_id not in (conflict["memory_id_a"], conflict["memory_id_b"]):
        raise MemoryConflictError("MEMORY_CONFLICT")
    losing_memory_id = (conflict["memory_id_b"] if winning_memory_id == conflict["memory_id_a"]
                        else conflict["memory_id_a"])

    winning_revision_id = db.execute(text(
        "SELECT id FROM agent_memory_revisions WHERE memory_id = :id AND superseded_at IS NULL"
    ), {"id": winning_memory_id}).scalar_one()

    db.execute(text(
        "UPDATE agent_memories SET status = 'active', updated_at = now() WHERE id = :id"
    ), {"id": winning_memory_id})
    row = db.execute(text(
        "UPDATE agent_memories SET status = 'deleted', deleted_at = now(), updated_at = now() "
        "WHERE id = :id RETURNING embedding_model_version"
    ), {"id": losing_memory_id}).mappings().one()
    db.execute(text(
        "UPDATE agent_memory_conflicts SET status = 'resolved', "
        "resolved_by_revision_id = :rid, resolved_at = now() WHERE id = :id"
    ), {"rid": winning_revision_id, "id": conflict_id})
    db.execute(text(
        "INSERT INTO agent_memory_vector_outbox (id, memory_id, event_type, state, created_at) "
        "VALUES (:id, :mid, 'upsert', 'pending', now())"
    ), {"id": _new_id(), "mid": winning_memory_id})
    if row["embedding_model_version"] is not None:
        db.execute(text(
            "INSERT INTO agent_memory_vector_outbox (id, memory_id, event_type, state, created_at) "
            "VALUES (:id, :mid, 'delete', 'pending', now())"
        ), {"id": _new_id(), "mid": losing_memory_id})
    db.commit()
    return get_memory(db, user_id=user_id, memory_id=winning_memory_id)
```

- [ ] **Step 3: Run the tests to verify they pass**

Run: `cd backend && TEST_DATABASE_URL=postgresql://ontoprompt:ontoprompt@localhost:5432/ontoprompt pytest tests/agent/test_agent_memory_inspection.py -v`
Expected: all pass (full file, Tasks 2-5).

- [ ] **Step 4: Commit**

```bash
git add backend/app/services/memory/inspection.py backend/tests/agent/test_agent_memory_inspection.py
git commit -m "feat: add memory conflict resolution and conflict listing"
```

---

### Task 6: Router

**Files:**
- Create: `backend/app/routers/agent_memories.py`
- Modify: `backend/app/main.py` (register the new router)
- Test: `backend/tests/agent/test_agent_memories_api.py` (new)

**Interfaces:**
- Consumes: every function from Tasks 2-5 (`list_memories`, `get_memory`, `confirm_memory`, `reject_memory`, `correct_memory`, `delete_memory`, `list_conflicts`, `resolve_conflict`, `MemoryConsentRequiredError`, `MemoryConflictError`), `app.services.memory.predicate_registry.PredicateRegistryError` (already merged, for future-proofing — not raised by this task's own code paths, but the router's exception mapping should include it since it's part of the same stable-error-code family and a later change to `correct_memory` might raise it).
- Produces: HTTP routes under `/api/v1/agents/{agent_id}/memories` (matching this codebase's `agent_approvals.py`/`agent_clarifications.py` `prefix="/api/v1"`-with-full-paths-in-router convention).

- [ ] **Step 1: Read the precedent router in full first**

Read `backend/app/routers/agent_approvals.py` in full before writing anything — this task's router follows its exact shape (dependency injection, existence-hiding error mapping, response envelope).

- [ ] **Step 2: Write the failing tests**

Create `backend/tests/agent/test_agent_memories_api.py` — read `backend/tests/agent/test_approval_state.py` in full first (this is the real, verified-to-exist precedent for API-level testing of a router in this family — NOT `test_agent_approvals_api.py`, a filename that does not exist in this repo; confirmed during this plan's own setup). Copy its exact structure:

```python
import os
import subprocess
import sys
import uuid
from pathlib import Path
from urllib.parse import quote

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.routers.agent_memories import router
from app.services.auth_service import create_access_token

BACKEND_DIR = Path(__file__).resolve().parents[2]
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
DEFAULT_DOMAIN = "00000000-0000-0000-0000-000000000001"


def _scoped_url(schema: str) -> str:
    return f"{TEST_DATABASE_URL}?options={quote(f'-csearch_path={schema},public', safe='-=,')}"


def _alembic(schema: str, *args, check=True):
    return subprocess.run(
        [sys.executable, "scripts/run_migrations.py", *args], cwd=BACKEND_DIR,
        env=dict(os.environ, DATABASE_URL=_scoped_url(schema)),
        capture_output=True, text=True, check=check,
    )


@pytest.fixture
def schema():
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL required")
    schema = "p6b3_api_" + uuid.uuid4().hex
    engine = create_engine(TEST_DATABASE_URL)
    with engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    assert _alembic(schema, "upgrade", "0020_agent_memory_recall_index").returncode == 0
    yield schema
    with engine.begin() as connection:
        connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
    engine.dispose()


def _session(schema):
    return sessionmaker(bind=create_engine(_scoped_url(schema)))()


def _seed(schema, *, user_id="u-1", agent_id="ag-1"):
    s = _session(schema)
    s.execute(text(
        "INSERT INTO users (id,username,email,password_hash,role,is_active,security_domain_id,"
        "created_at,updated_at) VALUES (:u,'a','a@t.com','h','editor',true,:d,now(),now())"
    ), {"u": user_id, "d": DEFAULT_DOMAIN})
    s.execute(text(
        "INSERT INTO agents (id,visibility,status,owner_id,created_at,updated_at) "
        "VALUES (:id,'private','active',:u,now(),now())"
    ), {"id": agent_id, "u": user_id})
    s.commit()
    s.close()


def _client(session):
    from app.deps import get_db

    def override_get_db():
        yield session

    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_db] = override_get_db
    yield app
    app.dependency_overrides.clear()
```

Note this precedent's `_seed` also inserts an `agent_access_grants` row — this task's router does NOT use access-grant capability checks (per this plan's Global Constraints: authorization is by `user_id` alone, not an agent-operator grant), so do NOT copy that `agent_access_grants` insert; the `_seed` above already omits it correctly. `create_access_token({'sub': 'u-1', 'role': 'editor'})` is the exact auth-token-building call, used as `headers = {"Authorization": f"Bearer {create_access_token({'sub': 'u-1', 'role': 'editor'})}"}`, then `with TestClient(client) as c:` and `c.get(...)`/`c.post(..., json=..., headers=headers)`. The test bodies should cover:

```python
def test_list_memories_endpoint_returns_only_current_users_memories(client, auth_headers, ...):
    # seed one memory for the authenticated user, one for another user
    # GET /api/v1/agents/{agent_id}/memories
    # assert 200, only the current user's memory in the response envelope's "data"."items"
    ...

def test_get_memory_endpoint_404_for_other_users_memory(client, auth_headers, ...):
    # GET /api/v1/agents/{agent_id}/memories/{memory_id} for a memory owned by another user
    # assert 404
    ...

def test_confirm_memory_endpoint_without_consent_returns_error(client, auth_headers, ...):
    # POST .../memories/{memory_id}/confirm with {"consent": false}
    # assert 4xx, response body's "detail" == "MEMORY_CONSENT_REQUIRED"
    ...

def test_correct_memory_endpoint_updates_display_text(client, auth_headers, ...):
    # POST .../memories/{memory_id}/correct with {"display_text": "...", "confidence": 0.8}
    # assert 200, response reflects new display_text
    ...

def test_delete_memory_endpoint_tombstones(client, auth_headers, ...):
    # POST .../memories/{memory_id}/delete
    # assert 200 (or 204), memory status is 'deleted' afterward via a follow-up GET returning 404
    ...

def test_resolve_conflict_endpoint_picks_winner(client, auth_headers, ...):
    # seed an open conflict, POST /api/v1/agents/{agent_id}/memories/conflicts/{conflict_id}/resolve
    # with {"winning_memory_id": "..."}
    # assert 200, winner active
    ...
```

Write each test function seeding via `_seed(schema)`, opening a session via `_session(schema)`, building `headers` via `create_access_token(...)`, calling `next(_client(s))` for the app, and running requests inside `with TestClient(client) as c:` — matching `test_approval_state.py`'s exact per-test structure (see e.g. its `test_approve_resumes_once`).

Run: `cd backend && TEST_DATABASE_URL=postgresql://ontoprompt:ontoprompt@localhost:5432/ontoprompt pytest tests/agent/test_agent_memories_api.py -v`
Expected: FAIL — router doesn't exist.

- [ ] **Step 3: Implement the router**

Create `backend/app/routers/agent_memories.py`:

```python
"""Memory inspection/correction/deletion API (P6B-3, Section 12.1)."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.deps import get_current_user, get_db
from app.models.user import User
from app.services.memory.inspection import (
    MemoryConflictError, MemoryConsentRequiredError, confirm_memory, correct_memory,
    delete_memory, get_memory, list_conflicts, list_memories, reject_memory, resolve_conflict,
)

router = APIRouter()


class ConfirmMemoryRequest(BaseModel):
    consent: bool


class CorrectMemoryRequest(BaseModel):
    display_text: str
    confidence: float | None = None


class ResolveConflictRequest(BaseModel):
    winning_memory_id: str


def _map_error(exc: Exception) -> HTTPException:
    if isinstance(exc, (MemoryConsentRequiredError, MemoryConflictError)):
        return HTTPException(status_code=409, detail=str(exc))
    raise exc


@router.get("/agents/{agent_id}/memories")
def get_memories(agent_id: str, status: str | None = None, db: Session = Depends(get_db),
                 current_user: User = Depends(get_current_user)):
    items = list_memories(db, user_id=current_user.id, agent_id=agent_id, status=status)
    return {"data": {"items": items}}


@router.get("/agents/{agent_id}/memories/conflicts")
def get_conflicts(agent_id: str, db: Session = Depends(get_db),
                  current_user: User = Depends(get_current_user)):
    items = list_conflicts(db, user_id=current_user.id, agent_id=agent_id)
    return {"data": {"items": items}}


@router.get("/agents/{agent_id}/memories/{memory_id}")
def get_memory_detail(agent_id: str, memory_id: str, db: Session = Depends(get_db),
                      current_user: User = Depends(get_current_user)):
    item = get_memory(db, user_id=current_user.id, memory_id=memory_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Not found")
    return {"data": item}


@router.post("/agents/{agent_id}/memories/{memory_id}/confirm")
def post_confirm_memory(agent_id: str, memory_id: str, body: ConfirmMemoryRequest,
                        db: Session = Depends(get_db),
                        current_user: User = Depends(get_current_user)):
    try:
        result = confirm_memory(db, user_id=current_user.id, memory_id=memory_id,
                                consent=body.consent)
    except (MemoryConsentRequiredError, MemoryConflictError) as exc:
        raise _map_error(exc)
    return {"data": result}


@router.post("/agents/{agent_id}/memories/{memory_id}/reject")
def post_reject_memory(agent_id: str, memory_id: str, db: Session = Depends(get_db),
                       current_user: User = Depends(get_current_user)):
    reject_memory(db, user_id=current_user.id, memory_id=memory_id)
    return {"data": {"status": "deleted"}}


@router.post("/agents/{agent_id}/memories/{memory_id}/correct")
def post_correct_memory(agent_id: str, memory_id: str, body: CorrectMemoryRequest,
                        db: Session = Depends(get_db),
                        current_user: User = Depends(get_current_user)):
    try:
        result = correct_memory(db, user_id=current_user.id, memory_id=memory_id,
                                display_text=body.display_text, confidence=body.confidence)
    except MemoryConflictError as exc:
        raise _map_error(exc)
    if result is None:
        raise HTTPException(status_code=404, detail="Not found")
    return {"data": result}


@router.post("/agents/{agent_id}/memories/{memory_id}/delete")
def post_delete_memory(agent_id: str, memory_id: str, db: Session = Depends(get_db),
                       current_user: User = Depends(get_current_user)):
    delete_memory(db, user_id=current_user.id, memory_id=memory_id)
    return {"data": {"status": "deleted"}}


@router.post("/agents/{agent_id}/memories/conflicts/{conflict_id}/resolve")
def post_resolve_conflict(agent_id: str, conflict_id: str, body: ResolveConflictRequest,
                          db: Session = Depends(get_db),
                          current_user: User = Depends(get_current_user)):
    try:
        result = resolve_conflict(db, user_id=current_user.id, conflict_id=conflict_id,
                                  winning_memory_id=body.winning_memory_id)
    except MemoryConflictError as exc:
        raise _map_error(exc)
    return {"data": result}
```

`get_current_user`/`get_db` both come from `app.deps` (not `app.database`) and `User` comes from `app.models.user` — confirmed directly against `agent_approvals.py`'s own imports during this plan's own research; the code above already uses the correct paths.

In `backend/app/main.py`, add the import and registration (find where `agent_approvals`/`agent_clarifications` are imported/registered and add this router in the same style, same prefix):

```python
app.include_router(agent_memories.router, prefix="/api/v1", tags=["agent-memories"])
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && TEST_DATABASE_URL=postgresql://ontoprompt:ontoprompt@localhost:5432/ontoprompt pytest tests/agent/test_agent_memories_api.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add backend/app/routers/agent_memories.py backend/app/main.py backend/tests/agent/test_agent_memories_api.py
git commit -m "feat: add memory inspection/correction/deletion REST API"
```

---

### Task 7: Frontend — API client + inspection drawer component

**Files:**
- Create: `frontend/src/api/agentMemories.ts`
- Create: `frontend/src/pages/agents/detail/MemoryInspectionDrawer.tsx`
- Test: `frontend/src/pages/agents/detail/MemoryInspectionDrawer.test.tsx` (new, using this codebase's existing frontend test framework — check `frontend/package.json`/an existing `.test.tsx` file for the exact test runner/library in use, e.g. Vitest + React Testing Library, before writing)

**Interfaces:**
- Produces: `agentMemoriesApi` (methods: `list`, `get`, `confirm`, `reject`, `correct`, `delete`, `listConflicts`, `resolveConflict`), `MemoryInspectionDrawer` component (`open`, `onClose`, `agentId` props, matching `CapabilityDrawer.tsx`'s controlled-by-parent convention).

- [ ] **Step 1: Read the precedent files in full first**

Read `frontend/src/pages/agents/detail/CapabilityDrawer.tsx` (drawer UI pattern), `frontend/src/api/agentApprovals.ts` (API-module pattern), `frontend/src/pages/agents/application/ActionApprovalCard.tsx` (list/load/mutate/error pattern), and `frontend/src/api/client.ts`'s FULL response interceptor including its reject path (to confirm the exact error shape a failed `apiClient` call delivers — do not assume it matches the `err.error?.code` idiom used elsewhere; verify directly).

- [ ] **Step 2: Write the API client module**

Create `frontend/src/api/agentMemories.ts`:

```ts
import { apiClient } from './client'

export interface MemoryRevision {
  revision_no: number
  display_text: string
  confidence: number
  consent_basis: string
  created_at: string
  superseded_at: string | null
}

export interface MemoryConflictSummary {
  conflict_id: string
  other_memory_id: string
  other_display_text: string
}

export interface MemoryRecord {
  id: string
  subject_key: string
  predicate: string
  display_text: string
  confidence: number
  sensitivity: string
  status: 'pending_confirmation' | 'active' | 'conflicted' | 'deleted'
  consent_basis: string
  created_at: string
  updated_at: string
}

export interface MemoryDetail extends MemoryRecord {
  revisions: MemoryRevision[]
  conflict: MemoryConflictSummary | null
  embedding_status: 'current' | 'pending' | 'never_embedded'
}

export interface ConflictListItem {
  conflict_id: string
  subject_key: string
  predicate: string
  memory_id_a: string
  display_text_a: string
  memory_id_b: string
  display_text_b: string
  created_at: string
}

export const agentMemoriesApi = {
  list: (agentId: string, status?: string) =>
    apiClient.get<{ items: MemoryRecord[] }>(`/agents/${agentId}/memories`, { params: { status } }),
  get: (agentId: string, memoryId: string) =>
    apiClient.get<MemoryDetail>(`/agents/${agentId}/memories/${memoryId}`),
  confirm: (agentId: string, memoryId: string, consent: boolean) =>
    apiClient.post<MemoryDetail>(`/agents/${agentId}/memories/${memoryId}/confirm`, { consent }),
  reject: (agentId: string, memoryId: string) =>
    apiClient.post<{ status: string }>(`/agents/${agentId}/memories/${memoryId}/reject`),
  correct: (agentId: string, memoryId: string, displayText: string, confidence?: number) =>
    apiClient.post<MemoryDetail>(`/agents/${agentId}/memories/${memoryId}/correct`, {
      display_text: displayText, confidence,
    }),
  delete: (agentId: string, memoryId: string) =>
    apiClient.post<{ status: string }>(`/agents/${agentId}/memories/${memoryId}/delete`),
  listConflicts: (agentId: string) =>
    apiClient.get<{ items: ConflictListItem[] }>(`/agents/${agentId}/memories/conflicts`),
  resolveConflict: (agentId: string, conflictId: string, winningMemoryId: string) =>
    apiClient.post<MemoryDetail>(`/agents/${agentId}/memories/conflicts/${conflictId}/resolve`, {
      winning_memory_id: winningMemoryId,
    }),
}
```

Confirm `apiClient`'s exact method signature for query params (`{ params: { status } }` above is a guess at axios convention — verify against `client.ts` and at least one other existing API module that passes query params, adjust if this codebase's wrapper expects a different shape).

- [ ] **Step 3: Write the drawer component**

Create `frontend/src/pages/agents/detail/MemoryInspectionDrawer.tsx`, following `CapabilityDrawer.tsx`'s exact scrim/panel/`role="dialog"` structure (controlled entirely by `open`/`onClose` props, no internal open state) and `ActionApprovalCard.tsx`'s exact load/mutate/error/finally pattern (do not use the `err.error?.code` idiom — use `(err as { detail?: string })?.detail`, per the Global Constraints' verified wire-shape note). The component must:

- On `open` becoming `true`, call `agentMemoriesApi.list(agentId)` and `agentMemoriesApi.listConflicts(agentId)`.
- Render each memory as a row: `display_text`, a status badge (`pending_confirmation`/`active`/`conflicted`/`deleted` — `deleted` should never actually appear given the list endpoint excludes it, but the badge logic should handle all four values defensively rather than assuming), `confidence`, `updated_at`. The list endpoint (`agentMemoriesApi.list`) does not return `embedding_status` (that field only exists on the detail response, per Task 2's `get_memory` vs `list_memories` split) — do not attempt to render it in the main list row; it belongs in a memory's expanded detail view only (e.g. shown when a row is expanded/clicked, via a follow-up `agentMemoriesApi.get` call), rendered as a small text label ("Embedding: current" / "pending" / "not yet embedded") — this is the spec's "reconciliation state" surfaced to the user, read-only, no action button attached to it in this plan's scope.
- For a `pending_confirmation` memory: render Confirm/Reject buttons. Confirm opens an inline checkbox ("I consent to storing this as a confirmed fact") — the button only calls `agentMemoriesApi.confirm(agentId, memoryId, true)` once that checkbox is checked; there is no way to call `confirm` with `consent: false` from the UI (the backend's `consent: false` rejection path exists for API robustness/testing, not because the UI ever sends it deliberately).
- For an `active` memory: render an inline-editable `display_text` field with a Save button (calls `correct`) and a Delete button (calls `delete`, with a native `window.confirm(...)` guard before calling — this codebase has no custom confirmation-modal component to reuse, and inventing one is out of this plan's scope).
- For a `conflicted` memory: instead of the normal row, render both sides of its conflict (using the `conflict` field from `get_memory`, or the separately-fetched conflicts list — prefer the conflicts list for the drawer's main view, matching a natural "Conflicts" section separate from the main memory list) with a "Keep this one" button on each side, calling `resolveConflict`.
- On any mutation's success, re-fetch the list (matching `ActionApprovalCard.tsx`'s `load()`-after-mutation pattern) rather than trying to hand-patch local state for every field a memory might have changed.

Do not write the full component body from scratch without first reading `ActionApprovalCard.tsx`'s exact structure — this brief describes required behavior, not exact JSX; match the codebase's actual component style (hooks, i18n `useTranslation()`/`t(...)` calls matching `CapabilityDrawer.tsx`'s pattern, Tailwind classes matching the existing drawer's visual language) rather than inventing a new style.

- [ ] **Step 4: Write component tests**

Read one existing `.test.tsx` file in `frontend/src/pages/agents/` (or the nearest directory with frontend component tests) to copy its exact test-setup pattern (mock `apiClient`, render, assert). Write tests covering: the drawer renders nothing when `open=false`; it fetches and renders a list of memories when opened; clicking "Confirm" without checking the consent checkbox does not call the API; checking the checkbox then clicking "Confirm" calls `agentMemoriesApi.confirm` with `consent: true`; a conflict renders both sides with two "Keep this one" buttons.

Run: use this codebase's actual frontend test command (check `frontend/package.json`'s `scripts` for the exact invocation, e.g. `npm test` or `npx vitest run <path>`) scoped to the new test file.
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/api/agentMemories.ts frontend/src/pages/agents/detail/MemoryInspectionDrawer.tsx frontend/src/pages/agents/detail/MemoryInspectionDrawer.test.tsx
git commit -m "feat: add memory inspection drawer UI component"
```

---

### Task 8: Frontend — wire the drawer into MemoryConfigTab, activate long-term params

**Files:**
- Modify: `frontend/src/pages/agents/detail/MemoryConfigTab.tsx`
- Test: existing `MemoryConfigTab` test file (find and extend it — search for `MemoryConfigTab.test.tsx` or similar; if none exists, create one following the same pattern established in Task 7)

**Interfaces:**
- Consumes: `MemoryInspectionDrawer` (Task 7).

- [ ] **Step 1: Read the current file in full first**

Read `frontend/src/pages/agents/detail/MemoryConfigTab.tsx` in full — re-verify the exact current line numbers and JSX around the `data-testid="memory-unavailable"` banner and the `opacity-70`/"暂未生效" inert long-term-parameters block, since this plan's excerpts (from research, not fresh at plan-write time) may have drifted.

- [ ] **Step 2: Write/extend the failing test**

Add or extend the test file to assert: the `data-testid="memory-unavailable"` banner element is no longer rendered; a new button (e.g. `data-testid="open-memory-inspection"`) is rendered and, when clicked, renders the `MemoryInspectionDrawer` with `open=true`; the long-term-parameters block no longer carries the "暂未生效"/inert styling.

Run the frontend test command scoped to this file.
Expected: FAIL — old assertions (if any exist checking for the banner) now conflict, or new assertions fail since the change hasn't been made yet.

- [ ] **Step 3: Make the change**

In `MemoryConfigTab.tsx`:
1. Replace the static `data-testid="memory-unavailable"` banner block with a button (e.g. "Inspect memories" / i18n key `agent.memory.inspect_button`) that sets local state `inspectionOpen = true`, and render `<MemoryInspectionDrawer open={inspectionOpen} onClose={() => setInspectionOpen(false)} agentId={agentId} />` at the end of the component (matching how `CapabilityDrawer` is likely already rendered by whatever OTHER component in this codebase currently uses it — check that call site for the exact wiring convention, e.g. is the drawer rendered unconditionally with an `open` prop, or conditionally with `{open && <Drawer .../>}`; `CapabilityDrawer.tsx` itself already returns `null` internally when `!open`, so match whichever the existing caller actually does).
2. Remove the `opacity-70` class and the "暂未生效" (`agent.memory.available_later`) badge + "以下参数已保存，但要等长期记忆功能在后续版本上线后才会生效" (`agent.memory.long_term_inert_note`) explanatory text from the long-term-parameters block — long-term memory (P6B-2a/2b) is genuinely live now that both are merged to `dev`. Keep the parameter fields themselves (`recall_token_budget`, `recall_count`, etc.) and their existing save behavior completely unchanged — this task only removes the "not yet active" visual treatment, it does not change what the save button does or add any new settings field.

- [ ] **Step 4: Run the tests to verify they pass**

Run the frontend test command scoped to `MemoryConfigTab`'s test file, and the broader `frontend/src/pages/agents/` test suite to confirm no regression in sibling tests that might reference the removed banner/badge.
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/pages/agents/detail/MemoryConfigTab.tsx <the test file path used above>
git commit -m "feat: wire memory inspection drawer into settings tab, activate long-term params"
```

---

### Task 9: Full regression pass

**Files:** none (verification only)

- [ ] **Step 1: Backend regression**

Run: `cd backend && TEST_DATABASE_URL=postgresql://ontoprompt:ontoprompt@localhost:5432/ontoprompt pytest tests/agent/ -q --ignore=tests/agent/test_playwright_adapter.py`
Expected: all pass except the pre-existing, unrelated cluster already established across this session's prior plans (`test_0003_full_migration.py`, `test_build_manifest.py` x3, `test_schema_startup.py` — 5 failures, confirmed pre-existing and unrelated multiple times already this session).

- [ ] **Step 2: Confirm the new routes appear in the OpenAPI schema, and no unrelated drift**

This plan DOES add new API routes (unlike P6B-2a/2b) — so, unlike those plans' Step 2, do NOT expect an empty diff. Run: `git diff --stat dev -- backend/openapi-agent.json` and confirm the diff exists and touches ONLY additions for the new `/agents/{agent_id}/memories...` paths — no unrelated path should appear modified or removed. If this repo has a script that regenerates `openapi-agent.json` from the live app (check for one, e.g. `scripts/export_openapi.py` or similar, referenced by other plans' or CI's conventions), run it now rather than hand-editing the file.

- [ ] **Step 3: Frontend regression**

Run this codebase's full frontend test command (check `frontend/package.json`) and confirm no regressions beyond the files this plan touched.

- [ ] **Step 4: Confirm P6B-1/2a/2b's own tests are unaffected**

Run: `cd backend && TEST_DATABASE_URL=postgresql://ontoprompt:ontoprompt@localhost:5432/ontoprompt pytest tests/agent/test_agent_memory_short_term.py tests/agent/test_agent_memory_long_term.py tests/agent/test_agent_memory_recall.py tests/agent/test_turn_worker_loop.py -v` — every test from all three prior plans must still pass unchanged, since Task 1 modifies the same `fixed_policy.py` file P6B-1's own retention step lives in, and Tasks 2-6 build directly on P6B-2a's schema/consent/canonicalizer modules.

- [ ] **Step 5: Report**

If any step surfaces anything beyond the known 5-failure backend cluster, or any frontend/API-schema regression, stop and investigate before considering this plan done — do not fold an unexplained new failure into "probably pre-existing."

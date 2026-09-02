"""HTTP wiring for the governed-turn-plan endpoints (Task 3).

Business-rule coverage (missing evidence, target validation, digest, three
independent branches, decision transitions) lives in
`tests/runtime/test_runtime_turn_plans.py` at the service layer; this file
only proves the REST transport itself — auth (`get_current_user`, not a
delegated `RuntimeContext`, unlike every other route in this router),
serialization, and status codes — using the standard `client`/`auth_headers`
fixtures from `tests/conftest.py`.
"""
from __future__ import annotations

import hashlib
import json
import uuid

import pytest
from sqlalchemy import text

from app.models.entity import Entity
from app.models.entity_instance import EntityInstance
from app.models.ontology import OntologyProject

DOMAIN = "00000000-0000-0000-0000-000000000001"


def _sha256_json(value) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@pytest.fixture
def seeded_turn(db, admin_user):
    project = OntologyProject(
        id=str(uuid.uuid4()), name="turn-plan-api-ontology", domain="test",
        created_by=admin_user.id, security_domain_id=DOMAIN,
    )
    db.add(project)
    db.flush()

    release_id = str(uuid.uuid4())
    entity = Entity(id=str(uuid.uuid4()), ontology_id=project.id, name_cn="目标实体")
    db.add(entity)
    db.flush()

    db.execute(text(
        "INSERT INTO ontology_releases "
        "(id, ontology_id, version_no, version, manifest_bytes, manifest_projection, "
        "schema_hash, status, created_by, created_at) "
        "VALUES (:id, :oid, 1, 'v1', :mb, :proj, :sh, 'published', :cb, CURRENT_TIMESTAMP)"
    ), {
        "id": release_id, "oid": project.id, "mb": b"manifest",
        "proj": json.dumps({"entities": [{"id": entity.id}]}),
        "sh": b"schema-hash-0000000000000000000000", "cb": admin_user.id,
    })
    project.latest_published_release_id = release_id
    db.commit()

    instance = EntityInstance(
        id=str(uuid.uuid4()), entity_id=entity.id, ontology_id=project.id,
        row_identity="row-1", row_data={"name": "MAT001"},
    )
    db.add(instance)
    db.commit()

    agent_id = str(uuid.uuid4())
    agent_version_id = str(uuid.uuid4())
    session_id = str(uuid.uuid4())
    turn_id = str(uuid.uuid4())
    response_message_id = str(uuid.uuid4())
    tool_execution_id = str(uuid.uuid4())
    response_content = "Suppliers below safety stock: MAT001."
    result_hash = _sha256_text("tool-result")

    db.execute(text(
        "INSERT INTO agents (id, visibility, status, owner_id, active_version_id, created_at, updated_at) "
        "VALUES (:id, 'private', 'active', :owner, :version, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
    ), {"id": agent_id, "owner": admin_user.id, "version": agent_version_id})
    db.execute(text(
        "INSERT INTO agent_ontology_bindings (id, agent_version_id, ontology_id, capabilities, allowlists, created_at) "
        "VALUES (:id, :version, :ontology, '[]', '{}', CURRENT_TIMESTAMP)"
    ), {"id": str(uuid.uuid4()), "version": agent_version_id, "ontology": project.id})
    db.execute(text(
        "INSERT INTO agent_sessions (id, agent_id, owner_user_id, status, created_at, updated_at) "
        "VALUES (:id, :agent, :owner, 'active', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
    ), {"id": session_id, "agent": agent_id, "owner": admin_user.id})
    db.execute(text(
        "INSERT INTO agent_turns (id, session_id, status, response_message_id, dispatch_generation, created_at, updated_at) "
        "VALUES (:id, :sid, 'succeeded', :rmid, 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
    ), {"id": turn_id, "sid": session_id, "rmid": response_message_id})
    db.execute(text(
        "INSERT INTO agent_messages (id, session_id, turn_id, role, ordinal, content, created_at) "
        "VALUES (:id, :sid, :turn, 'assistant', 1, :content, CURRENT_TIMESTAMP)"
    ), {"id": response_message_id, "sid": session_id, "turn": turn_id, "content": response_content})
    db.execute(text(
        "INSERT INTO agent_tool_executions "
        "(id, turn_id, idempotency_key, status, descriptor, parameters_hash, result_hash, created_at, updated_at) "
        "VALUES (:id, :turn, :key, 'succeeded', :descriptor, :ph, :rh, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
    ), {
        "id": tool_execution_id, "turn": turn_id, "key": tool_execution_id,
        "descriptor": json.dumps({"descriptor_id": f"query:{project.id}"}),
        "ph": _sha256_text("params"), "rh": result_hash,
    })
    db.commit()

    return {
        "turn_id": turn_id, "target_fixture_id": instance.id, "tool_execution_id": tool_execution_id,
        "tool_result_hash": result_hash, "response_content": response_content,
    }


def _digest(seeded, *, branch: str) -> str:
    return _sha256_json({
        "turn_id": seeded["turn_id"],
        "tool_execution_id": seeded["tool_execution_id"],
        "tool_result_hash": seeded["tool_result_hash"],
        "final_response_hash": _sha256_text(seeded["response_content"]),
        "branch": branch,
        "target_fixture_id": seeded["target_fixture_id"],
    })


def test_create_from_turn_requires_authentication(client, seeded_turn):
    response = client.post("/api/v2/runtime/action-plans/from-turn", json={
        "turn_id": seeded_turn["turn_id"], "branch": "approved",
        "target_fixture_id": seeded_turn["target_fixture_id"], "idempotency_key": "k-1",
        "payload_digest": _digest(seeded_turn, branch="approved"),
    })
    assert response.status_code in (401, 403)


def test_create_approve_round_trip_over_http(client, auth_headers, seeded_turn):
    created = client.post(
        "/api/v2/runtime/action-plans/from-turn",
        json={
            "turn_id": seeded_turn["turn_id"], "branch": "approved",
            "target_fixture_id": seeded_turn["target_fixture_id"], "idempotency_key": "k-http-approve",
            "payload_digest": _digest(seeded_turn, branch="approved"),
        },
        headers=auth_headers,
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["status"] == "pending"
    plan_id = body["action_plan_id"]
    plan_hash = body["plan_hash"]

    read_back = client.get(f"/api/v2/runtime/action-plans/from-turn/{plan_id}", headers=auth_headers)
    assert read_back.status_code == 200
    assert read_back.json()["status"] == "pending"

    decided = client.post(
        f"/api/v2/runtime/action-plans/from-turn/{plan_id}/decide",
        json={"decision": "approved", "plan_hash": plan_hash},
        headers=auth_headers,
    )
    assert decided.status_code == 200, decided.text
    decided_body = decided.json()
    assert decided_body["status"] == "approved"
    assert decided_body["must_write"] is True
    assert decided_body["target_before_hash"] != decided_body["target_after_hash"]
    assert decided_body["receipt_id"]
    assert decided_body["audit_event_id"]


def test_create_rejects_extra_fields(client, auth_headers, seeded_turn):
    response = client.post(
        "/api/v2/runtime/action-plans/from-turn",
        json={
            "turn_id": seeded_turn["turn_id"], "branch": "approved",
            "target_fixture_id": seeded_turn["target_fixture_id"], "idempotency_key": "k-extra",
            "payload_digest": _digest(seeded_turn, branch="approved"), "agent_id": "should-not-be-accepted",
        },
        headers=auth_headers,
    )
    assert response.status_code == 422


def test_create_from_turn_denies_missing_tool_evidence(client, auth_headers, seeded_turn, db):
    db.execute(text("DELETE FROM agent_tool_executions WHERE turn_id = :t"), {"t": seeded_turn["turn_id"]})
    db.commit()
    response = client.post(
        "/api/v2/runtime/action-plans/from-turn",
        json={
            "turn_id": seeded_turn["turn_id"], "branch": "approved",
            "target_fixture_id": seeded_turn["target_fixture_id"], "idempotency_key": "k-missing-evidence",
            "payload_digest": _digest(seeded_turn, branch="approved"),
        },
        headers=auth_headers,
    )
    assert response.status_code in (401, 403)
    assert response.json()["reason_code"].startswith("TOOL_EVIDENCE_MISSING")

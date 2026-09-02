"""Task 3 (business-journey acceptance): governed-action proposals created
FROM an Agent turn's own persisted evidence.

Everything below is hand-seeded directly against the ORM/raw SQL (mirroring
`tests/runtime/test_runtime_execution_api.py`'s own convention) rather than
built up through the full Agent-turn dispatch/worker machinery, which
(per every existing `tests/agent/test_turn_*`/`test_approval_state.py`
suite) requires a real Postgres `TEST_DATABASE_URL` this unit harness does
not have. `create_governed_plan_from_turn`/`decide_governed_plan` only ever
read `agent_turns`/`agent_sessions`/`agent_messages`/`agent_tool_executions`/
`agent_ontology_bindings`/`entity_instances`/`ontology_releases` — none of
which requires a real `AgentVersion`/`ModelConfigVersion` row to exist for
these tests' purposes (the `agent_ontology_bindings` join is keyed against
`agents.active_version_id`'s raw string value, not a real FK-checked
version row).
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
from app.models.user import User
from app.services.runtime.turn_plans import (
    GOVERNED_PLAN_TTL_SECONDS,
    TurnPlanError,
    create_governed_plan_from_turn,
    decide_governed_plan,
    get_governed_plan,
)

DOMAIN = "00000000-0000-0000-0000-000000000001"


def _canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_json(value) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@pytest.fixture
def turn_plan_fixture(db):
    """One governed turn: a user, an ontology with one published release and
    one live `EntityInstance`, an Agent bound to that ontology, a succeeded
    turn with a final assistant message, and one persisted tool execution —
    everything `create_governed_plan_from_turn` reads."""
    user = User(
        id=str(uuid.uuid4()), username="turnplan-user", email="turnplan@example.invalid",
        password_hash="x", role="editor", security_domain_id=DOMAIN,
    )
    db.add(user)
    db.commit()

    project = OntologyProject(
        id=str(uuid.uuid4()), name="turn-plan-ontology", domain="test",
        created_by=user.id, security_domain_id=DOMAIN,
    )
    db.add(project)
    db.flush()

    release_id = str(uuid.uuid4())
    entity = Entity(id=str(uuid.uuid4()), ontology_id=project.id, name_cn="目标实体")
    db.add(entity)
    db.flush()

    manifest_projection = json.dumps({"entities": [{"id": entity.id, "name": "目标实体"}]})
    db.execute(text(
        "INSERT INTO ontology_releases "
        "(id, ontology_id, version_no, version, manifest_bytes, manifest_projection, "
        "schema_hash, status, created_by, created_at) "
        "VALUES (:id, :oid, 1, 'v1', :mb, :proj, :sh, 'published', :cb, CURRENT_TIMESTAMP)"
    ), {
        "id": release_id, "oid": project.id, "mb": b"manifest", "proj": manifest_projection,
        "sh": b"schema-hash-0000000000000000000000", "cb": user.id,
    })
    project.latest_published_release_id = release_id
    db.commit()

    instance = EntityInstance(
        id=str(uuid.uuid4()), entity_id=entity.id, ontology_id=project.id,
        row_identity="row-1", row_data={"name": "MAT001", "value": 42},
    )
    db.add(instance)
    db.commit()

    agent_id = str(uuid.uuid4())
    agent_version_id = str(uuid.uuid4())
    session_id = str(uuid.uuid4())
    turn_id = str(uuid.uuid4())
    response_message_id = str(uuid.uuid4())
    tool_execution_id = str(uuid.uuid4())
    response_content = "Suppliers below safety stock: MAT001. Recommend a purchase order."
    result_hash = _sha256_text("tool-result")

    db.execute(text(
        "INSERT INTO agents (id, visibility, status, owner_id, active_version_id, created_at, updated_at) "
        "VALUES (:id, 'private', 'active', :owner, :version, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
    ), {"id": agent_id, "owner": user.id, "version": agent_version_id})
    db.execute(text(
        "INSERT INTO agent_ontology_bindings (id, agent_version_id, ontology_id, capabilities, allowlists, created_at) "
        "VALUES (:id, :version, :ontology, '[]', '{}', CURRENT_TIMESTAMP)"
    ), {"id": str(uuid.uuid4()), "version": agent_version_id, "ontology": project.id})
    db.execute(text(
        "INSERT INTO agent_sessions (id, agent_id, owner_user_id, status, created_at, updated_at) "
        "VALUES (:id, :agent, :owner, 'active', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
    ), {"id": session_id, "agent": agent_id, "owner": user.id})
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
        "db": db, "user_id": user.id, "ontology_id": project.id, "release_id": release_id,
        "target_fixture_id": instance.id, "turn_id": turn_id, "tool_execution_id": tool_execution_id,
        "tool_result_hash": result_hash, "response_content": response_content,
        "security_domain_id": DOMAIN,
    }


def _digest(fixture, *, branch: str, target_fixture_id: str | None = None) -> str:
    return _sha256_json({
        "turn_id": fixture["turn_id"],
        "tool_execution_id": fixture["tool_execution_id"],
        "tool_result_hash": fixture["tool_result_hash"],
        "final_response_hash": _sha256_text(fixture["response_content"]),
        "branch": branch,
        "target_fixture_id": target_fixture_id or fixture["target_fixture_id"],
    })


def test_create_requires_persisted_tool_evidence(turn_plan_fixture, db):
    db.execute(text("DELETE FROM agent_tool_executions WHERE turn_id = :t"), {"t": turn_plan_fixture["turn_id"]})
    db.commit()
    with pytest.raises(TurnPlanError, match="TOOL_EVIDENCE_MISSING"):
        create_governed_plan_from_turn(
            db, turn_id=turn_plan_fixture["turn_id"], branch="approved",
            target_fixture_id=turn_plan_fixture["target_fixture_id"], idempotency_key="key-1",
            payload_digest=_digest(turn_plan_fixture, branch="approved"),
            actor_user_id=turn_plan_fixture["user_id"],
        )


def test_create_rejects_a_target_outside_the_disposable_fixture(turn_plan_fixture, db):
    with pytest.raises(TurnPlanError, match="TARGET_OUTSIDE_DISPOSABLE_FIXTURE"):
        create_governed_plan_from_turn(
            db, turn_id=turn_plan_fixture["turn_id"], branch="approved",
            target_fixture_id="not-a-real-instance", idempotency_key="key-2",
            payload_digest=_digest(turn_plan_fixture, branch="approved", target_fixture_id="not-a-real-instance"),
            actor_user_id=turn_plan_fixture["user_id"],
        )


def test_create_rejects_a_changed_payload_digest(turn_plan_fixture, db):
    with pytest.raises(TurnPlanError, match="PAYLOAD_DIGEST_MISMATCH"):
        create_governed_plan_from_turn(
            db, turn_id=turn_plan_fixture["turn_id"], branch="approved",
            target_fixture_id=turn_plan_fixture["target_fixture_id"], idempotency_key="key-3",
            payload_digest="0" * 64,
            actor_user_id=turn_plan_fixture["user_id"],
        )


def test_create_rejects_a_reused_idempotency_key(turn_plan_fixture, db):
    create_governed_plan_from_turn(
        db, turn_id=turn_plan_fixture["turn_id"], branch="rejected",
        target_fixture_id=turn_plan_fixture["target_fixture_id"], idempotency_key="key-reused",
        payload_digest=_digest(turn_plan_fixture, branch="rejected"),
        actor_user_id=turn_plan_fixture["user_id"],
    )
    with pytest.raises(TurnPlanError, match="IDEMPOTENCY_KEY_REUSED"):
        create_governed_plan_from_turn(
            db, turn_id=turn_plan_fixture["turn_id"], branch="rejected",
            target_fixture_id=turn_plan_fixture["target_fixture_id"], idempotency_key="key-reused",
            payload_digest=_digest(turn_plan_fixture, branch="rejected"),
            actor_user_id=turn_plan_fixture["user_id"],
        )


def test_three_branch_plans_are_genuinely_independent_and_produce_correct_outcomes(turn_plan_fixture, db):
    fixture = turn_plan_fixture
    # Three distinct, isolated targets — one live EntityInstance per branch.
    targets = {}
    for branch in ("approved", "rejected", "expired"):
        instance = EntityInstance(
            id=str(uuid.uuid4()), entity_id=db.query(Entity).filter(
                Entity.ontology_id == fixture["ontology_id"]).one().id,
            ontology_id=fixture["ontology_id"], row_identity=f"row-{branch}",
            row_data={"name": f"target-{branch}", "value": 1},
        )
        db.add(instance)
        db.commit()
        targets[branch] = instance.id

    plans = {}
    for branch in ("approved", "rejected", "expired"):
        plan = create_governed_plan_from_turn(
            db, turn_id=fixture["turn_id"], branch=branch, target_fixture_id=targets[branch],
            idempotency_key=f"key-{branch}-{uuid.uuid4()}",
            payload_digest=_digest(fixture, branch=branch, target_fixture_id=targets[branch]),
            actor_user_id=fixture["user_id"],
        )
        plans[branch] = plan

    # Distinctness.
    assert len({p.action_plan_id for p in plans.values()}) == 3
    assert len({p.plan_hash for p in plans.values()}) == 3
    assert len({p.approval_id for p in plans.values()}) == 3
    assert len({targets[b] for b in plans}) == 3

    # Decide approved and rejected; leave expired alone (already-expired at creation).
    approved_view = decide_governed_plan(
        db, plan_id=plans["approved"].action_plan_id, presented_plan_hash=plans["approved"].plan_hash,
        decision="approved", actor_user_id=fixture["user_id"], security_domain_id=fixture["security_domain_id"],
    )
    rejected_view = decide_governed_plan(
        db, plan_id=plans["rejected"].action_plan_id, presented_plan_hash=plans["rejected"].plan_hash,
        decision="rejected", actor_user_id=fixture["user_id"], security_domain_id=fixture["security_domain_id"],
    )
    expired_view = get_governed_plan(db, plan_id=plans["expired"].action_plan_id, actor_user_id=fixture["user_id"])

    assert approved_view.status == "approved"
    assert approved_view.must_write is True
    assert approved_view.target_before_hash != approved_view.target_after_hash
    assert approved_view.receipt_id and approved_view.audit_event_id

    assert rejected_view.status == "rejected"
    assert rejected_view.must_write is False
    assert rejected_view.target_before_hash == rejected_view.target_after_hash

    assert expired_view.status == "expired"
    assert expired_view.must_write is False
    assert expired_view.target_before_hash == expired_view.target_after_hash

    # The approved instance's row_data really changed in the database.
    mutated = db.query(EntityInstance).filter(EntityInstance.id == targets["approved"]).one()
    assert mutated.row_data.get("_governed_turn_plan_applied") == plans["approved"].action_plan_id


def test_decide_rejects_deciding_an_expired_plan(turn_plan_fixture, db):
    plan = create_governed_plan_from_turn(
        db, turn_id=turn_plan_fixture["turn_id"], branch="expired",
        target_fixture_id=turn_plan_fixture["target_fixture_id"], idempotency_key="key-expired-decide",
        payload_digest=_digest(turn_plan_fixture, branch="expired"),
        actor_user_id=turn_plan_fixture["user_id"],
    )
    with pytest.raises(TurnPlanError, match="GOVERNED_PLAN_EXPIRED"):
        decide_governed_plan(
            db, plan_id=plan.action_plan_id, presented_plan_hash=plan.plan_hash, decision="approved",
            actor_user_id=turn_plan_fixture["user_id"], security_domain_id=turn_plan_fixture["security_domain_id"],
        )


def test_decide_rejects_deciding_the_same_plan_twice(turn_plan_fixture, db):
    plan = create_governed_plan_from_turn(
        db, turn_id=turn_plan_fixture["turn_id"], branch="approved",
        target_fixture_id=turn_plan_fixture["target_fixture_id"], idempotency_key="key-double-decide",
        payload_digest=_digest(turn_plan_fixture, branch="approved"),
        actor_user_id=turn_plan_fixture["user_id"],
    )
    decide_governed_plan(
        db, plan_id=plan.action_plan_id, presented_plan_hash=plan.plan_hash, decision="approved",
        actor_user_id=turn_plan_fixture["user_id"], security_domain_id=turn_plan_fixture["security_domain_id"],
    )
    with pytest.raises(TurnPlanError, match="GOVERNED_PLAN_ALREADY_DECIDED"):
        decide_governed_plan(
            db, plan_id=plan.action_plan_id, presented_plan_hash=plan.plan_hash, decision="rejected",
            actor_user_id=turn_plan_fixture["user_id"], security_domain_id=turn_plan_fixture["security_domain_id"],
        )


def test_decide_rejects_a_wrong_plan_hash(turn_plan_fixture, db):
    plan = create_governed_plan_from_turn(
        db, turn_id=turn_plan_fixture["turn_id"], branch="approved",
        target_fixture_id=turn_plan_fixture["target_fixture_id"], idempotency_key="key-wrong-hash",
        payload_digest=_digest(turn_plan_fixture, branch="approved"),
        actor_user_id=turn_plan_fixture["user_id"],
    )
    with pytest.raises(TurnPlanError, match="PLAN_HASH_MISMATCH"):
        decide_governed_plan(
            db, plan_id=plan.action_plan_id, presented_plan_hash="0" * 64, decision="approved",
            actor_user_id=turn_plan_fixture["user_id"], security_domain_id=turn_plan_fixture["security_domain_id"],
        )


def test_get_governed_plan_is_read_only_and_hides_other_users_plans(turn_plan_fixture, db):
    plan = create_governed_plan_from_turn(
        db, turn_id=turn_plan_fixture["turn_id"], branch="rejected",
        target_fixture_id=turn_plan_fixture["target_fixture_id"], idempotency_key="key-existence-hiding",
        payload_digest=_digest(turn_plan_fixture, branch="rejected"),
        actor_user_id=turn_plan_fixture["user_id"],
    )
    with pytest.raises(TurnPlanError, match="PLAN_NOT_FOUND"):
        get_governed_plan(db, plan_id=plan.action_plan_id, actor_user_id="someone-else")


def test_ttl_constant_is_positive_and_reasonable():
    assert 0 < GOVERNED_PLAN_TTL_SECONDS <= 3600

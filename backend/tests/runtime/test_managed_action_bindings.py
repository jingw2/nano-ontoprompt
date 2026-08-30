"""Task 21: managed action bindings and frozen writable-plan targets.

A writable `Action` (Task 15's `create_action_plan`) only becomes concrete
once it is bound to a fixed execution surface: a connection identity, SQL
dialect, table, primary key, an allowlisted set of writable columns, and a
version-column precondition (`ManagedActionBinding`,
`app.services.runtime.action_bindings`). This suite proves three governance
invariants:

- `publish_binding`/`resolve_published_binding` only ever hand back a
  binding that is genuinely `published`, not `draft` or `revoked`, and whose
  pinned connection target has not drifted since publication.
- `freeze_action_target` turns a caller's target selector and parameters
  into server-owned, ordered, typed values plus stable integrity hashes —
  never anything the caller's own key ordering or extra fields could shift.
- Once a writable `ActionPlan` exists, nothing can widen or replace its
  frozen target/parameters at execution time; any attempted override that
  does not exactly reproduce what the plan already pins is rejected.
"""
from __future__ import annotations

import uuid

import pytest

from app.models.action import Action
from app.models.managed_action import ManagedActionBinding
from app.models.oauth import OAuthClient
from app.models.ontology import OntologyProject
from app.models.ontology_data_grant import OntologyDataGrant
from app.models.ontology_release import OntologyRelease
from app.models.semantic_snapshot import SemanticSnapshot
from app.models.user import User
from app.models.v2.connection import Connection
from app.schemas.runtime_snapshot import SnapshotView
from app.services.runtime.action_bindings import (
    BindingError,
    FrozenTarget,
    PlanValidationError,
    _connection_identity,
    freeze_action_target,
    publish_binding,
    resolve_published_binding,
    validate_execution_overrides,
)
from app.services.runtime.credentials import RuntimeContext, RuntimePrincipal
from app.services.runtime.service import ActionPlanRequest, RuntimeService

DOMAIN = "00000000-0000-0000-0000-0000000000bb"
CAPABILITIES = ["investigate", "propose_action"]
ONTOLOGY_ID = "ontology-binding-001"
RELEASE_ID = "release-binding-001"
SNAPSHOT_ID = "snap-binding-001"
AGENT_ID = "agent-binding-001"
USER_ID = "user-binding-001"
ACTION_ID = "action-binding-001"
CONNECTION_ID = "connection-binding-001"
CONNECTION_KIND = "postgres"
CONNECTION_NAME = "prod-supplier-db"
# `_seed_connection` below never sets `config` explicitly, so the seeded
# connection carries `Connection.config`'s model default (`{}`) — computed
# via the real `_connection_identity` (not hardcoded) so this constant tracks
# whatever `_connection_identity` actually derives its identity from.
CONNECTION_TARGET_IDENTITY = _connection_identity(
    Connection(id=CONNECTION_ID, name=CONNECTION_NAME, kind=CONNECTION_KIND, config={})
)


def _seed_action(db, *, action_id=ACTION_ID, ontology_id=ONTOLOGY_ID):
    action = db.get(Action, action_id)
    if action is not None:
        return action
    action = Action(id=action_id, ontology_id=ontology_id, name_cn="批准目标", enabled=True)
    db.add(action)
    db.commit()
    return action


def _seed_connection(db, *, connection_id=CONNECTION_ID, kind=CONNECTION_KIND, name=CONNECTION_NAME):
    connection = db.get(Connection, connection_id)
    if connection is not None:
        return connection
    connection = Connection(id=connection_id, name=name, kind=kind, status="active")
    db.add(connection)
    db.commit()
    return connection


def _binding_kwargs(**overrides):
    defaults = dict(
        action_id=ACTION_ID,
        connection_id=CONNECTION_ID,
        connection_target_identity=CONNECTION_TARGET_IDENTITY,
        dialect="postgresql",
        schema_name="public",
        table_name="targets",
        primary_key_columns=["target_id"],
        writable_columns=["status"],
        version_column="row_version",
        parameter_schema={"status": "string"},
        secret_ref="vault://kv/connections/prod-supplier-db#password",
    )
    defaults.update(overrides)
    return defaults


def publish_fixture_binding(db, *, dialect="postgresql", **overrides):
    _seed_action(db)
    _seed_connection(db)
    return publish_binding(db, **_binding_kwargs(dialect=dialect, **overrides))


def resolve_fixture_binding(db, state: str) -> ManagedActionBinding:
    """Seed a binding in `state` and return whatever
    `resolve_published_binding` does with it — raising `BindingError` for
    every state this suite exercises (draft/revoked/connection-drift/
    connection-config-drift).

    The two drift states each seed and publish against their own dedicated
    connection row (rather than the shared `CONNECTION_ID` fixture) so that
    mutating that row after publication can't bleed into other states run
    later in the same test.
    """
    _seed_action(db)
    if state == "connection-drift":
        connection = _seed_connection(db, connection_id=f"{CONNECTION_ID}-rename-drift")
        binding = publish_binding(
            db, **_binding_kwargs(connection_id=connection.id, connection_target_identity=_connection_identity(connection)),
        )
        # Simulate the underlying connection being repointed after
        # publication — the binding's pinned `connection_target_identity`
        # no longer matches the live connection it was published against.
        connection.name = "some-other-database"
        db.commit()
        return resolve_published_binding(db, binding.managed_action_binding_id, binding.version)

    if state == "connection-config-drift":
        connection = _seed_connection(db, connection_id=f"{CONNECTION_ID}-config-drift")
        binding = publish_binding(
            db, **_binding_kwargs(connection_id=connection.id, connection_target_identity=_connection_identity(connection)),
        )
        # Simulate the same connection row being silently repointed at a
        # different physical database by editing `config` alone — `kind`
        # and `name` stay the same, so only a config fingerprint catches it.
        connection.config = {"host": "staging-db.internal"}
        db.commit()
        return resolve_published_binding(db, binding.managed_action_binding_id, binding.version)

    _seed_connection(db)
    status = state
    binding = ManagedActionBinding(
        managed_action_binding_id=str(uuid.uuid4()),
        **_binding_kwargs(),
        version=1,
        status=status,
    )
    db.add(binding)
    db.commit()
    return resolve_published_binding(db, binding.managed_action_binding_id, binding.version)


def valid_snapshot() -> SnapshotView:
    return SnapshotView(
        id=SNAPSHOT_ID,
        ontology_release_id=RELEASE_ID,
        materialization_hash="a" * 64,
        status="materialized",
        created_by=USER_ID,
        freshness_state="fresh",
    )


def _seed_user(db, *, user_id=USER_ID):
    user = db.get(User, user_id)
    if user is not None:
        return user
    user = User(
        id=user_id, username=user_id, email=f"{user_id}@example.invalid",
        password_hash="not-a-real-password-hash", role="editor", security_domain_id=DOMAIN,
    )
    db.add(user)
    return user


def _seed_agent(db, *, agent_id=AGENT_ID, capability_names=CAPABILITIES):
    client = OAuthClient(
        id=agent_id, client_name="Binding Agent", redirect_uris=[], allowed_scopes=[],
        is_active=True, created_by=USER_ID, security_domain_id=DOMAIN,
        allowed_audiences=[], capability_names=list(capability_names),
    )
    db.add(client)
    return client


def _seed_grant(db, *, ontology_id=ONTOLOGY_ID, user_id=USER_ID, capabilities=CAPABILITIES):
    grant = OntologyDataGrant(
        id=str(uuid.uuid4()), ontology_id=ontology_id, user_id=user_id,
        capabilities=list(capabilities), status="active", created_by=user_id,
    )
    db.add(grant)
    return grant


def _seed_release(db, user, *, ontology_id=ONTOLOGY_ID, release_id=RELEASE_ID):
    from sqlalchemy import text
    import hashlib

    project = OntologyProject(
        id=ontology_id, name="binding ontology", domain="test",
        created_by=user.id, security_domain_id=user.security_domain_id,
    )
    db.add(project)
    db.flush()
    db.execute(
        text(
            "INSERT INTO ontology_releases "
            "(id, ontology_id, version_no, version, manifest_bytes, "
            "manifest_projection, schema_hash, status, created_by, created_at) "
            "VALUES (:id, :ontology_id, 1, 'v1', :manifest, '{}', :schema_hash, "
            "'published', :created_by, CURRENT_TIMESTAMP)"
        ),
        {
            "id": release_id, "ontology_id": ontology_id, "manifest": b"binding-manifest",
            "schema_hash": hashlib.sha256(release_id.encode()).digest(), "created_by": user.id,
        },
    )
    project.latest_published_release_id = release_id
    db.commit()
    return db.get(OntologyRelease, release_id)


def _seed_snapshot(db, release, user, *, snapshot_id=SNAPSHOT_ID):
    snapshot = SemanticSnapshot(
        id=snapshot_id, ontology_release_id=release.id,
        quality_summary={}, evidence_summary={"citations": []},
        materialization_hash="a" * 64, status="materialized", created_by=user.id,
        freshness_state="fresh", freshness_lag_seconds=60,
        source_cursor={
            "source_id": "source-binding-001", "resource": "default",
            "contract": "watermark_primary_key", "watermark": None, "primary_key": "1",
            "opaque_value": None, "observed_at": "2026-08-30T00:00:00+00:00",
        },
    )
    db.add(snapshot)
    db.commit()
    return snapshot


def create_writable_fixture_plan(db):
    """Fully govern a snapshot/release/agent/grant, publish a binding, and
    create a real writable `ActionPlan` through `RuntimeService` — exercises
    the actual `create_action_plan` binding path, not a hand-built stand-in."""
    user = _seed_user(db)
    db.commit()
    _seed_agent(db)
    _seed_grant(db)
    db.commit()
    release = _seed_release(db, user)
    _seed_snapshot(db, release, user)
    binding = publish_fixture_binding(db)

    principal = RuntimePrincipal(
        agent_id=AGENT_ID, user_id=USER_ID, security_domain_id=DOMAIN,
        audience="ontexus-runtime", scope=frozenset({"ontology:read", "ontology:write"}),
        token_id="tok-binding-001",
    )
    context = RuntimeContext(principal=principal, correlation_id="corr-binding-001")
    request = ActionPlanRequest(
        semantic_snapshot_id=SNAPSHOT_ID, action_id=ACTION_ID,
        parameters={"status": "approved"}, target_selector={"target_id": "target-001"},
        idempotency_key="idem-binding-001",
        managed_action_binding_id=binding.managed_action_binding_id,
        binding_version=binding.version,
    )
    return RuntimeService().create_action_plan(request, context, db)


def test_published_binding_freezes_server_owned_target_and_params(db):
    binding = publish_fixture_binding(db, dialect="postgresql")
    frozen = freeze_action_target(
        db, snapshot=valid_snapshot(), binding=binding,
        parameters={"status": "approved"}, selector={"target_id": "target-001"},
    )
    assert isinstance(frozen, FrozenTarget)
    assert frozen.primary_key_tuple == (("target_id", "target-001"),)
    assert frozen.parameters == {"status": "approved"}
    assert frozen.before_image_hash
    assert frozen.version_hash


def test_publish_binding_rejects_version_column_reused_as_primary_key(db):
    with pytest.raises(BindingError) as exc:
        publish_fixture_binding(db, version_column="target_id")
    assert exc.value.reason_code == "BINDING_NOT_ALLOWLISTED"


def test_binding_draft_revoked_or_connection_drift_is_not_resolvable(db):
    for state in ("draft", "revoked", "connection-drift", "connection-config-drift"):
        with pytest.raises(BindingError) as exc:
            resolve_fixture_binding(db, state)
        assert exc.value.reason_code == "BINDING_DRIFT"


def test_runtime_cannot_override_frozen_target_or_parameters(db):
    plan = create_writable_fixture_plan(db)
    assert plan.managed_action_binding_id
    assert plan.target_key == (("target_id", "target-001"),)
    assert plan.parameters == {"status": "approved"}
    with pytest.raises(PlanValidationError) as exc:
        validate_execution_overrides(plan, {"target_id": "target-002"}, {"status": "rejected"})
    assert exc.value.reason_code == "INVALID_PLAN_HASH"

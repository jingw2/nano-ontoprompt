"""Regression coverage for server-owned business-journey evidence."""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException


def test_preparation_rejects_client_supplied_mcp_descriptor_ids(client, auth_headers):
    """Descriptor grants must be derived from the release, never caller input."""
    response = client.post(
        "/api/v1/business-journeys/preparations",
        json={
            "run_id": "forged-descriptor", "journey_id": "supply_chain",
            "ontology_id": "ontology-1", "ontology_release_id": "release-1",
            "pipeline_run_id": "run-1", "dataset_version_id": "dataset-version-1",
            "curated_dataset_id": "curated-1", "curated_review_id": "review-1",
            "model_config_version_id": "model-version-1", "structured": {},
            "model_probe": {"requested_model": "deepseek-v4-flash-vision-exp", "observed_model": "deepseek-v4-flash-vision-exp"},
            "model_calls": [{"call_kind": "ontology", "logical_call_index": 1,
                             "correlation_id": "forged-descriptor:supply_chain:ontology:1",
                             "requested_model": "deepseek-v4-flash-vision-exp",
                             "observed_model": "deepseek-v4-flash-vision-exp", "http_attempts": 1,
                             "retry_count": 0}],
            "mcp_descriptor_ids": ["query:forged"],
        },
        headers=auth_headers,
    )
    assert response.status_code == 422
    assert "mcp_descriptor_ids" in response.text


def test_preparation_derives_only_release_catalog_descriptors_allowed_by_active_grant(monkeypatch):
    from app.routers.business_journeys import _derive_granted_descriptor_ids

    monkeypatch.setattr(
        "app.routers.business_journeys.ontology_tool_catalog",
        lambda _db, _ontology_id: {
            "published": True, "release_id": "release-1",
            "tools": [
                {"descriptor_id": "query:ontology-1", "capability": "read_instances"},
                {"descriptor_id": "action:ontology-1", "capability": "execute_instance_action"},
            ],
        },
    )
    db = SimpleNamespace(
        query=lambda _model: SimpleNamespace(filter_by=lambda **_kwargs: SimpleNamespace(
            all=lambda: [SimpleNamespace(capabilities=["read_instances"], valid_from=None, valid_until=None)]
        ))
    )
    release = SimpleNamespace(id="release-1", ontology_id="ontology-1", status="published")

    assert _derive_granted_descriptor_ids(db, release, "gate-user") == ["query:ontology-1"]


def test_preparation_rejects_catalog_that_is_not_the_supplied_published_release(monkeypatch):
    from app.routers.business_journeys import _derive_granted_descriptor_ids

    monkeypatch.setattr(
        "app.routers.business_journeys.ontology_tool_catalog",
        lambda _db, _ontology_id: {"published": True, "release_id": "different-release", "tools": []},
    )
    release = SimpleNamespace(id="release-1", ontology_id="ontology-1", status="published")

    with pytest.raises(HTTPException, match="MCP_CATALOG_RELEASE_MISMATCH"):
        _derive_granted_descriptor_ids(SimpleNamespace(), release, "gate-user")


def test_preparation_write_is_not_available_to_an_arbitrary_editor(client, editor_user):
    from app.services.auth_service import create_access_token

    token = create_access_token({"sub": editor_user.id, "role": "editor"})
    response = client.post(
        "/api/v1/business-journeys/preparations",
        json={
            "run_id": "editor-forgery", "journey_id": "supply_chain",
            "ontology_id": "ontology-1", "ontology_release_id": "release-1",
            "pipeline_run_id": "run-1", "dataset_version_id": "dataset-version-1",
            "curated_dataset_id": "curated-1", "curated_review_id": "review-1",
            "model_config_version_id": "model-version-1", "structured": {},
            "model_probe": {"requested_model": "deepseek-v4-flash-vision-exp", "observed_model": "deepseek-v4-flash-vision-exp"},
            "model_calls": [{"call_kind": "ontology", "logical_call_index": 1,
                             "correlation_id": "editor-forgery:supply_chain:ontology:1",
                             "requested_model": "deepseek-v4-flash-vision-exp",
                             "observed_model": "deepseek-v4-flash-vision-exp", "http_attempts": 1,
                             "retry_count": 0}],
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 403
    assert response.json()["detail"] == "BUSINESS_JOURNEY_GATE_IDENTITY_REQUIRED"


def test_preparation_gate_identity_fails_closed_when_not_explicitly_configured(monkeypatch):
    from app.config import settings
    from app.routers.business_journeys import _require_gate_identity

    monkeypatch.setattr(settings, "business_journey_gate_username", "")

    with pytest.raises(HTTPException, match="BUSINESS_JOURNEY_GATE_IDENTITY_REQUIRED"):
        _require_gate_identity(SimpleNamespace(username="dedicated-gate"))


def test_preparation_rejects_the_generic_admin_even_if_configured_as_gate(monkeypatch):
    from app.config import settings
    from app.routers.business_journeys import _require_gate_identity

    monkeypatch.setattr(settings, "business_journey_gate_username", settings.first_admin_user)

    with pytest.raises(HTTPException, match="BUSINESS_JOURNEY_GATE_IDENTITY_REQUIRED"):
        _require_gate_identity(SimpleNamespace(username=settings.first_admin_user))


def test_preparation_read_requires_dedicated_gate_identity(db, monkeypatch):
    from app.config import settings
    from app.routers.business_journeys import get_preparation

    monkeypatch.setattr(settings, "business_journey_gate_username", "dedicated-gate")

    with pytest.raises(HTTPException) as exc_info:
        get_preparation(
            "missing-run", "credit", db=db,
            current_user=SimpleNamespace(username="ordinary-user"),
        )

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "BUSINESS_JOURNEY_GATE_IDENTITY_REQUIRED"


def test_runtime_model_call_slots_require_preparation_and_reject_duplicates(db):
    from app.models.business_journey import BusinessJourneyPreparation
    from app.services.business_journey_ledger import (
        JourneyLedgerError, record_preparation_call, reserve_runtime_call,
    )

    with pytest.raises(JourneyLedgerError, match="PREPARATION_BINDING_MISSING"):
        reserve_runtime_call(
            db, run_id="ledger-run", journey_id="credit", turn_id="turn-1", logical_call_index=2,
            call_kind="agent_initial", correlation_id="ledger-run:credit:agent_initial:2",
            model_config_version_id="model-version",
        )

    db.add(BusinessJourneyPreparation(
        run_id="ledger-run", journey_id="credit", ontology_id="ontology", ontology_release_id="release",
        semantic_snapshot_id="snapshot", pipeline_run_id="pipeline", dataset_version_id="dataset",
        curated_dataset_id="curated", curated_review_id="review", model_config_version_id="model-version",
        mcp_descriptor_ids=["query:ontology"], structured={}, model_probe={}, model_calls=[],
    ))
    db.flush()
    record_preparation_call(
        db, run_id="ledger-run", journey_id="credit", model_config_version_id="model-version",
        call={"call_kind": "ontology", "correlation_id": "ledger-run:credit:ontology:1",
              "requested_model": "model", "observed_model": "model", "http_attempts": 1,
              "retry_count": 0},
    )
    reserve_runtime_call(
        db, run_id="ledger-run", journey_id="credit", turn_id="turn-1", logical_call_index=2,
        call_kind="agent_initial", correlation_id="ledger-run:credit:agent_initial:2",
        model_config_version_id="model-version",
    )
    with pytest.raises(JourneyLedgerError, match="MODEL_CALL_SLOT_ALREADY_EXISTS"):
        reserve_runtime_call(
            db, run_id="ledger-run", journey_id="credit", turn_id="turn-1", logical_call_index=2,
            call_kind="agent_initial", correlation_id="ledger-run:credit:agent_initial:2",
            model_config_version_id="model-version",
        )

    with pytest.raises(JourneyLedgerError, match="MODEL_CONFIG_VERSION_MISMATCH"):
        reserve_runtime_call(
            db, run_id="ledger-run", journey_id="credit", turn_id="turn-1", logical_call_index=3,
            call_kind="agent_final", correlation_id="ledger-run:credit:agent_final:3",
            model_config_version_id="different-model-version",
        )


def test_runtime_model_call_slots_are_scoped_to_the_actual_turn(db):
    """Different browser turns in one prepared journey may reuse indices 2/3.

    The preparation call remains the one fixed index=1 slot.  Runtime slots
    must additionally be scoped by their actual turn id, otherwise a second
    browser turn for the same prepared journey fails before it can call the
    model with MODEL_CALL_SLOT_ALREADY_EXISTS.
    """
    from app.models.business_journey import BusinessJourneyModelCall, BusinessJourneyPreparation
    from app.runtime.langgraph_runtime import LangGraphRuntime
    from app.runtime.protocol import TurnRuntimeContext
    from app.services.business_journey_ledger import record_preparation_call

    run_id = "multi-turn-ledger-run"
    journey_id = "credit"
    model_version = "multi-turn-model-version"
    db.add(BusinessJourneyPreparation(
        run_id=run_id, journey_id=journey_id, ontology_id="ontology", ontology_release_id="release",
        semantic_snapshot_id="snapshot", pipeline_run_id="pipeline", dataset_version_id="dataset",
        curated_dataset_id="curated", curated_review_id="review", model_config_version_id=model_version,
        mcp_descriptor_ids=["query:ontology"], structured={}, model_probe={}, model_calls=[],
    ))
    db.flush()
    record_preparation_call(
        db, run_id=run_id, journey_id=journey_id, model_config_version_id=model_version,
        call={"call_kind": "ontology", "correlation_id": f"{run_id}:{journey_id}:ontology:1",
              "requested_model": "model", "observed_model": "model", "http_attempts": 1,
              "retry_count": 0},
    )

    runtime = LangGraphRuntime(db)
    runtime._business_journey = {"run_id": run_id, "journey_id": journey_id}

    def context(turn_id: str) -> TurnRuntimeContext:
        return TurnRuntimeContext(
            turn_id=turn_id, session_id=f"session-{turn_id}", agent_id="agent",
            agent_version_id="agent-version", model_config_version_id=model_version,
        )

    runtime._reserve_journey_model_call(context("turn-1"), call_kind="agent_initial", logical_call_index=2)
    runtime._reserve_journey_model_call(context("turn-2"), call_kind="agent_initial", logical_call_index=2)

    assert db.query(BusinessJourneyModelCall).filter_by(
        run_id=run_id, journey_id=journey_id, logical_call_index=2,
    ).count() == 2


def test_preparation_ledger_projection_can_select_each_verified_turn(db, monkeypatch):
    """The preparation GET returns prep + exactly its own index 2/3 slots."""
    from app.config import settings
    from app.models.business_journey import BusinessJourneyPreparation
    from app.routers.business_journeys import get_preparation
    from app.runtime.langgraph_runtime import LangGraphRuntime
    from app.runtime.protocol import TurnRuntimeContext
    from app.services.business_journey_ledger import (
        finalize_runtime_call, record_preparation_call,
    )

    run_id = "filtered-ledger-run"
    journey_id = "credit"
    model_version = "filtered-model-version"
    monkeypatch.setattr(settings, "business_journey_gate_username", "dedicated-gate")
    gate_user = SimpleNamespace(username="dedicated-gate")
    preparation = BusinessJourneyPreparation(
        run_id=run_id, journey_id=journey_id, ontology_id="ontology", ontology_release_id="release",
        semantic_snapshot_id="snapshot", pipeline_run_id="pipeline", dataset_version_id="dataset",
        curated_dataset_id="curated", curated_review_id="review", model_config_version_id=model_version,
        mcp_descriptor_ids=["query:ontology"], structured={}, model_probe={}, model_calls=[],
    )
    db.add(preparation)
    db.flush()
    record_preparation_call(
        db, run_id=run_id, journey_id=journey_id, model_config_version_id=model_version,
        call={"call_kind": "ontology", "correlation_id": f"{run_id}:{journey_id}:ontology:1",
              "requested_model": "model", "observed_model": "model", "http_attempts": 1,
              "retry_count": 0},
    )
    runtime = LangGraphRuntime(db)
    runtime._business_journey = {"run_id": run_id, "journey_id": journey_id}

    def context(turn_id: str) -> TurnRuntimeContext:
        return TurnRuntimeContext(
            turn_id=turn_id, session_id=f"session-{turn_id}", agent_id="agent",
            agent_version_id="agent-version", model_config_version_id=model_version,
        )

    first_initial = runtime._reserve_journey_model_call(
        context("turn-1"), call_kind="agent_initial", logical_call_index=2,
    )
    first_final = runtime._reserve_journey_model_call(
        context("turn-1"), call_kind="agent_final", logical_call_index=3,
    )
    second_initial = runtime._reserve_journey_model_call(
        context("turn-2"), call_kind="agent_initial", logical_call_index=2,
    )
    second_final = runtime._reserve_journey_model_call(
        context("turn-2"), call_kind="agent_final", logical_call_index=3,
    )
    for slot in (first_initial, first_final, second_initial, second_final):
        finalize_runtime_call(slot, observed_model="model", requested_model="model", http_attempts=1, retry_count=0)
    db.flush()

    selected_first = get_preparation(run_id, journey_id, turn_id="turn-1", db=db, current_user=gate_user)
    selected_second = get_preparation(run_id, journey_id, turn_id="turn-2", db=db, current_user=gate_user)
    first_ledger = selected_first["data"]["model_call_ledger"]
    second_ledger = selected_second["data"]["model_call_ledger"]
    assert [call["logical_call_index"] for call in first_ledger] == [1, 2, 3]
    assert [call["turn_id"] for call in first_ledger] == [
        "__preparation__", "turn-1", "turn-1",
    ]
    assert [call["logical_call_index"] for call in second_ledger] == [1, 2, 3]
    assert [call["turn_id"] for call in second_ledger] == [
        "__preparation__", "turn-2", "turn-2",
    ]


def test_preparation_evidence_client_binds_ledger_read_to_turn(monkeypatch):
    from evals.business_journeys.api_client import JourneyApiClient

    client = JourneyApiClient("http://application.test", "token")
    paths: list[str] = []

    def read(path: str, *, reason_code: str, **_kwargs):
        paths.append(path)
        return {"data": {"run_id": "run", "journey_id": "credit"}}

    monkeypatch.setattr(client, "_get", read)
    try:
        client.read_preparation_evidence("run", "credit", turn_id="turn/id")
    finally:
        client.close()

    assert paths == [
        "/api/v1/business-journeys/preparations/run/credit?turn_id=turn%2Fid",
    ]

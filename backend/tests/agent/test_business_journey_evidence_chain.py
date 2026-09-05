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

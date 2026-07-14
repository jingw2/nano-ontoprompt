# tests/v2/infra/test_health.py
"""
Infrastructure health-check tests for the /health endpoint.

These tests run against the FastAPI TestClient (no real Docker services
needed). External services (Neo4j, MinIO, ChromaDB) will be reported as
"unavailable" in a pure-unit context — that is the expected, safe fallback.
"""
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

VALID_DB_STATES = ("ok", "error", "unknown")
VALID_SERVICE_STATES = ("ok", "unavailable", "unknown")


def test_health_endpoint_returns_200():
    response = client.get("/health")
    assert response.status_code == 200


def test_health_endpoint_returns_ok():
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert "status" in data
    assert "db" in data


def test_health_db_key_present():
    response = client.get("/health")
    data = response.json()
    assert data["db"] in VALID_DB_STATES


def test_health_all_service_keys_present():
    """All four service keys must be present in the response."""
    response = client.get("/health")
    data = response.json()
    for key in ("db", "neo4j", "minio", "chroma"):
        assert key in data, f"Missing key: {key}"


def test_health_service_states_are_valid():
    """Each service reports a known state string."""
    response = client.get("/health")
    data = response.json()
    assert data["db"] in VALID_DB_STATES
    for key in ("neo4j", "minio", "chroma"):
        assert data[key] in VALID_SERVICE_STATES, (
            f"{key} has unexpected state: {data[key]}"
        )


def test_health_status_key_is_ok():
    """Top-level status should always be 'ok' (endpoint itself is alive)."""
    response = client.get("/health")
    data = response.json()
    assert data["status"] == "ok"

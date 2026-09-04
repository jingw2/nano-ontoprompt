"""Real-application acceptance proof for `JourneyApiClient` (Task 3).

`evals/business_journeys/test_orchestrator.py` already proves `prepare_journey`/
`verify_journey`'s own orchestration logic exhaustively against a hand-rolled
fake HTTP server — useful for fast, deterministic unit coverage, but it
never touches a single real router, service, or database row. This file
closes that gap: it drives `JourneyApiClient` directly against the REAL
FastAPI app — dataset upload, real Pipeline DAG creation/run, and Curated
review/approval — proving the exact HTTP contracts `api_client.py` assumes
are real, not merely internally consistent with a fake stand-in. See the
test's own docstring for which further pre-browser sub-steps are KNOWN,
pre-existing, Postgres-only gaps under this SQLite unit harness.

The app runs on a real, ephemeral-port `uvicorn.Server` in a background
thread of THIS process (`httpx.ASGITransport` only supports httpx's async
client, and `JourneyApiClient`'s own client is deliberately sync to match
its CLI's sync call shape), with its own lifespan enabled — its startup
seeds the default admin exactly like a real deployment, and every request
gets the app's own real, per-request `get_db()` session rather than a
`TestClient`-style shared, cross-thread override.
"""
from __future__ import annotations

import asyncio
import socket
import sys
import threading
from pathlib import Path

import pytest
import uvicorn

from app.main import app
from evals.business_journeys.api_client import JourneyApiClient
from evals.business_journeys.api_client import JourneyAcceptanceError

REPO_ROOT = Path(__file__).resolve().parents[3]
_RUNTIME_DATA_DIR = REPO_ROOT / "test_data" / "runtime"
if str(_RUNTIME_DATA_DIR) not in sys.path:
    sys.path.insert(0, str(_RUNTIME_DATA_DIR))

from journey_registry import load_journey_manifest  # noqa: E402

RUN_ID = "journey-acceptance"
JOURNEY_ID = "supply_chain"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
def live_app_url():
    """Serve `app` on a real loopback port, in a background thread — so
    `JourneyApiClient` (a plain sync HTTP client) can reach it exactly like
    the real acceptance runner reaches a real deployment, just over loopback
    instead of a network. Deliberately does NOT reuse the `client`/`db`
    fixtures' `app.dependency_overrides[get_db]`: that override points every
    request at ONE shared, cross-thread `Session` object, which is not
    thread/coroutine-safe and produced hard-to-diagnose visibility gaps
    between sequential requests in practice. Running with the app's own,
    real, per-request `get_db()` (backed by `app.database.SessionLocal`,
    already pointed at the isolated per-test-session SQLite file `tests/
    conftest.py` sets up) is both simpler and more faithful to how the real
    acceptance runner actually talks to a real deployment. Lifespan stays
    on, so this server's own startup seeds the default admin exactly once
    (idempotent, `app.services.auth_service.seed_admin`)."""
    port = _free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)

    def _run() -> None:
        asyncio.run(server.serve())

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    try:
        deadline = threading.Event()
        for _ in range(200):
            if server.started:
                break
            deadline.wait(0.02)
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=5)


@pytest.fixture
def journey_client(live_app_url):
    """A `JourneyApiClient` wired to the real, live-served app, authenticated
    through the real `POST /api/v1/auth/login` flow as the app's own
    lifespan-seeded default admin (`app.config.Settings.first_admin_user`/
    `first_admin_password`) — the same "user/password" credential shape
    `prepare_journey`'s own `api_key` parameter expects, and the same login
    path a real deployment's operator credential would use."""
    from app.config import settings

    api_client = JourneyApiClient(
        live_app_url, f"{settings.first_admin_user}/{settings.first_admin_password}", run_id=RUN_ID,
    )
    api_client.authenticate()
    yield api_client
    api_client.close()


def test_dataset_pipeline_and_curated_review_run_against_the_real_application(journey_client):
    """Genuinely drives dataset upload -> Pipeline DAG creation/run (real
    connector-node resolution, real route-A execution, real curated-dataset
    output) -> Curated review start/approve through the real application,
    end to end — the newest, least-previously-exercised part of the whole
    pre-browser chain, and the part `evals/business_journeys/test_
    orchestrator.py`'s fake HTTP server could never actually prove.

    Two further sub-steps in the pre-browser chain — immutable model
    configuration (`POST /api/v1/models/{id}/versions`) and ontology
    lifecycle publish (`POST /api/v1/ontologies/{id}/mark-created` and
    `/publish`) — are KNOWN, pre-existing gaps under this SQLite unit
    harness, confirmed directly rather than assumed:
    - `versioning_schema_present` requires a `model_configs.status` column
      the real `0004` migration adds in Postgres but the plain `ModelConfig`
      ORM class (and therefore this harness, which builds tables from ONLY
      that class's declared columns) does not have.
    - `app.services.publication.lifecycle._project` issues a raw
      `SELECT ... FOR UPDATE`, valid on Postgres/MySQL but a syntax error on
      SQLite (`near "FOR": syntax error`) — confirmed by running it directly
      against this exact harness.
    Both are genuine SQL-dialect/schema-versioning limitations of the
    existing application code, not something introduced by this task, and
    match the SAME class of gap every other real Agent-turn/approval test in
    this codebase already documents by requiring a real `TEST_DATABASE_URL`
    (`tests/agent/test_turn_worker_loop.py`, `test_approval_state.py`,
    `test_action_execution.py`, all skip without one). A real Postgres run
    of `prepare_journey` would exercise both for real; this harness cannot.
    """
    manifest = load_journey_manifest(JOURNEY_ID, _RUNTIME_DATA_DIR)

    pipeline = journey_client.start_pipeline(manifest)
    assert pipeline.status == "success"
    assert pipeline.dataset_version_id
    assert pipeline.pipeline_id
    assert pipeline.pipeline_run_id

    curated = journey_client.approve_curated(pipeline.pipeline_run_id)
    assert curated.status == "approved"
    assert curated.curated_dataset_id
    assert curated.review_id


def test_legacy_curated_approval_is_unbound_and_cannot_be_used_as_run_evidence(journey_client):
    """The legacy approval route intentionally cannot mint a journey binding."""
    manifest = load_journey_manifest(JOURNEY_ID, _RUNTIME_DATA_DIR)
    journey_client._run_id = "journey-legacy-approval"
    pipeline = journey_client.start_pipeline(manifest)
    legacy = journey_client._post(
        f"/api/v2/curated/{pipeline.curated_dataset_id}/review?action=approve",
        reason_code="LEGACY_APPROVAL_FAILED",
    )
    review = journey_client._get(
        f"/api/v2/curated/reviews/{legacy['review_id']}", reason_code="REVIEW_MISSING",
    )
    assert review["pipeline_run_id"] is None
    with pytest.raises(JourneyAcceptanceError, match="PIPELINE_RUN_MISSING"):
        journey_client.approve_curated("not-the-producing-run")

"""Preparation / verification orchestrator tests.

``fake_api`` is a REAL, threaded HTTP server standing in for the
application under test, plus a real ``httpx`` mock transport standing in for
the official DeepSeek origin. Nothing in ``api_client``/``orchestrator`` is
monkeypatched: every assertion below is made against genuine HTTP traffic
those modules actually emit, so "prepare made exactly one model call",
"prepare never created an Agent or a turn", and "verify made zero mutations"
are observations of real requests rather than mock call counts.
"""
from __future__ import annotations

import json
import re
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Mapping

import httpx
import pytest

from evals.business_journeys.contracts import MODEL_ID, OFFICIAL_ORIGIN
from evals.business_journeys.deepseek_client import DeepSeekVisionClient
from evals.business_journeys.orchestrator import (
    JourneyAcceptanceError,
    JourneyPreparationBudget,
    prepare_all_journeys,
    prepare_journey,
    read_run_manifest,
    verify_all_journeys,
    verify_journey,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
_RUNTIME_DATA_DIR = REPO_ROOT / "test_data" / "runtime"
if str(_RUNTIME_DATA_DIR) not in sys.path:
    sys.path.insert(0, str(_RUNTIME_DATA_DIR))

from journey_registry import JOURNEY_IDS, load_journey_manifest  # noqa: E402

_HEX = "0123456789abcdef"


def _hash_for(seed: str) -> str:
    import hashlib

    return hashlib.sha256(seed.encode("utf-8")).hexdigest()


def _uuid_for(seed: str) -> str:
    digest = _hash_for(seed)
    return f"{digest[:8]}-{digest[8:12]}-{digest[12:16]}-{digest[16:20]}-{digest[20:32]}"


# ---------------------------------------------------------------------------
# The fake application under test.
# ---------------------------------------------------------------------------


class _FakeState:
    """Everything the fake application remembers, plus the observation
    counters the tests assert on."""

    def __init__(self) -> None:
        self.dropped: set[str] = set()
        self.model_response_id: str = MODEL_ID
        self.available_models: list[str] = [MODEL_ID]
        self.model_calls = 0
        self.agent_create_calls = 0
        self.agent_turn_calls = 0
        self.mutations: list[str] = []
        self.requests: list[str] = []
        self.upload_calls = 0
        self.url = ""
        self.preparations: dict[tuple[str, str], dict[str, Any]] = {}
        self.ontology_completions: dict[str, dict[str, list[str]]] = {}
        self.curated_approvals: list[dict[str, str]] = []
        self.unrelated_curated_dataset = False
        self.implicit_preparation_evidence = True

    def drop(self, resource: str) -> None:
        self.dropped.add(resource)

    def add_unrelated_curated_dataset(self) -> None:
        self.unrelated_curated_dataset = True


def _journey_of(name: str) -> str:
    for journey_id in JOURNEY_IDS:
        if journey_id in name:
            return journey_id
    return "supply_chain"


def _plan_branch_payload(run_id: str, journey_id: str, branch: str) -> dict[str, Any]:
    manifest = load_journey_manifest(journey_id, _RUNTIME_DATA_DIR)
    instance = next(p for p in manifest.plan_instances if p["branch"] == branch)
    before = _hash_for(f"{run_id}:{journey_id}:{branch}:before")
    after = before if branch != "approved" else _hash_for(f"{run_id}:{journey_id}:{branch}:after")
    return {
        "branch": branch,
        "action_plan_id": _uuid_for(f"{run_id}:{journey_id}:{branch}:plan"),
        "plan_hash": _hash_for(f"{run_id}:{journey_id}:{branch}:plan-hash"),
        "approval_id": _uuid_for(f"{run_id}:{journey_id}:{branch}:approval"),
        "target_fixture_id": str(instance["target_fixture_id"]),
        "status": branch,
        "execution_class": "HUMAN_APPROVED",
        "target_before_hash": before,
        "target_after_hash": after,
        "must_write": branch == "approved",
        "receipt_id": _uuid_for(f"{run_id}:{journey_id}:{branch}:receipt"),
        "audit_event_id": _uuid_for(f"{run_id}:{journey_id}:{branch}:audit"),
    }


def _granted_query_descriptor_id(journey_id: str) -> str:
    """The query descriptor id this fake application publishes for the
    journey's ontology (see `_create_ontology`/`_ontology_tools`) — the one
    `prepare_journey` records in the staging run manifest and the one a real
    turn's `tool_executed` event therefore has to carry."""
    return f"query:{_uuid_for(f'{journey_id}:ontology')}"


def browser_evidence_document(run_id: str, journey_id: str) -> dict[str, Any]:
    """The document Task 4's browser run writes into ``output_dir`` — the
    only place ``verify_journey`` learns which persisted records to read."""
    return {
        "schema_version": 1,
        "run_id": run_id,
        "journey_id": journey_id,
        "agent_id": _uuid_for(f"{run_id}:{journey_id}:agent"),
        "agent_version_id": _uuid_for(f"{run_id}:{journey_id}:agent-version"),
        "session_id": _uuid_for(f"{run_id}:{journey_id}:session"),
        "turn_id": _uuid_for(f"{run_id}:{journey_id}:turn"),
        "ontology_release_id": _uuid_for(f"{run_id}:{journey_id}:release"),
        "mcp_descriptor_ids": [_granted_query_descriptor_id(journey_id)],
        "model_config_version_id": _uuid_for(f"{run_id}:{journey_id}:model-version"),
        "plan_branches": [
            _plan_branch_payload(run_id, journey_id, branch)
            for branch in ("approved", "rejected", "expired")
        ],
    }


class _FakeApiHandler(BaseHTTPRequestHandler):
    state: _FakeState

    protocol_version = "HTTP/1.1"

    def log_message(self, *_args: Any) -> None:  # keep pytest output pristine
        return

    # -- transport ---------------------------------------------------------
    def _respond(self, status: int, payload: Any) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except ValueError:
            return {}

    def do_GET(self) -> None:  # noqa: N802
        self._dispatch("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._dispatch("POST")

    def do_PUT(self) -> None:  # noqa: N802
        self._dispatch("PUT")

    def do_DELETE(self) -> None:  # noqa: N802
        self._dispatch("DELETE")

    # -- routing -----------------------------------------------------------
    def _dispatch(self, method: str) -> None:
        state = type(self).state
        path = self.path.split("?", 1)[0]
        state.requests.append(f"{method} {path}")
        # Login is not a governed-state mutation (no plan/approval/grant/
        # ontology write) — the brief's own "zero mutations" clarification
        # for verify_journey is "it does not call approval, plan creation,
        # writeback, model, tool, or Agent creation endpoints," which login
        # is not one of. Excluded here so `fake_api.mutations` tracks the
        # thing the brief actually means (Critical Finding #4: verify_
        # journey must authenticate to read anything at all).
        if method != "GET" and path != "/api/v1/auth/login":
            state.mutations.append(f"{method} {path}")
        if re.fullmatch(r"/api/v1/agents", path) and method == "POST":
            state.agent_create_calls += 1
        if re.search(r"/turns$", path) and method == "POST":
            state.agent_turn_calls += 1

        body = self._body() if method != "GET" else {}
        for pattern, verb, handler in _ROUTES:
            match = re.fullmatch(pattern, path)
            if match and verb == method:
                try:
                    status, payload = handler(state, match, body)
                except _FakeNotFound as exc:
                    self._respond(404, {"detail": str(exc)})
                    return
                self._respond(status, payload)
                return
        self._respond(404, {"detail": f"NO_FAKE_ROUTE:{method} {path}"})


class _FakeNotFound(Exception):
    pass


Handler = Callable[[_FakeState, "re.Match[str]", dict], "tuple[int, Any]"]


def _login(state: _FakeState, _match: "re.Match[str]", _body: dict) -> tuple[int, Any]:
    return 200, {"data": {"access_token": "fake-access-token"}}


def _profile(state: _FakeState, _match: "re.Match[str]", _body: dict) -> tuple[int, Any]:
    return 200, {"data": {"id": _uuid_for("runtime-user"), "username": "runtime", "role": "admin"}}


def _create_model(state: _FakeState, _match: "re.Match[str]", body: dict) -> tuple[int, Any]:
    journey_id = _journey_of(str(body.get("name", "")))
    return 201, {"data": {
        "id": _uuid_for(f"{journey_id}:model-config"),
        "name": body.get("name"),
        "provider": "deepseek",
        "api_base": OFFICIAL_ORIGIN,
        "active_version_id": _uuid_for(f"{journey_id}:model-version"),
    }}


def _create_model_version(state: _FakeState, match: "re.Match[str]", body: dict) -> tuple[int, Any]:
    if "model_config_version" in state.dropped:
        raise _FakeNotFound("MODEL_VERSION_UNAVAILABLE")
    config_id = match.group("model_id")
    return 201, {"data": {
        "id": _uuid_for(f"{config_id}:version"),
        "model_config_id": config_id,
        "version_no": 1,
        "provider": "deepseek",
        "api_base": body.get("api_base") or OFFICIAL_ORIGIN,
        "behavior_hash": _hash_for(f"{config_id}:behavior"),
        "model_contract": [{"provider_model_revision": MODEL_ID}],
        "created_at": "2026-08-27T00:00:00+00:00",
    }}


def _upload_dataset(state: _FakeState, _match: "re.Match[str]", _body: dict) -> tuple[int, Any]:
    if "dataset_upload" in state.dropped:
        raise _FakeNotFound("DATASET_UPLOAD_FAILED")
    state.upload_calls += 1
    dataset_id = _uuid_for(f"upload:{state.upload_calls}")
    return 201, {"data": {"id": dataset_id, "name": f"upload-{state.upload_calls}", "kind": "structured"}}


def _create_pipeline(state: _FakeState, _match: "re.Match[str]", body: dict) -> tuple[int, Any]:
    journey_id = _journey_of(str(body.get("name", "")))
    return 201, {"id": _uuid_for(f"{journey_id}:pipeline"), "name": body.get("name"), "status": "draft"}


def _run_pipeline(state: _FakeState, match: "re.Match[str]", _body: dict) -> tuple[int, Any]:
    pipeline_id = match.group("pipeline_id")
    return 200, {"run_id": _uuid_for(f"{pipeline_id}:run"), "status": "success"}


def _get_run(state: _FakeState, match: "re.Match[str]", _body: dict) -> tuple[int, Any]:
    run_id = match.group("run_id")
    if "pipeline_run" in state.dropped:
        raise _FakeNotFound("Run not found")
    stats = {"row_count": 12, "quality_score": 0.99,
             "curated_dataset_id": _uuid_for(f"{run_id}:curated-dataset")}
    if "curated_dataset" in state.dropped:
        stats.pop("curated_dataset_id")
    return 200, {
        "id": run_id,
        "status": "success",
        "dataset_version_id": _uuid_for(f"{run_id}:dataset-version"),
        "stats": stats,
        "error_log": None,
        "started_at": "2026-08-27T00:00:00+00:00",
        "finished_at": "2026-08-27T00:00:05+00:00",
    }


def _list_curated(state: _FakeState, _match: "re.Match[str]", _body: dict) -> tuple[int, Any]:
    if "curated_dataset" in state.dropped:
        return 200, []
    items = [{
        "id": _uuid_for("curated-dataset"),
        "name": "curated",
        "status": "pending_review",
        "row_count": 12,
        "quality_score": 0.99,
    }]
    if state.unrelated_curated_dataset:
        items.insert(0, {
            "id": _uuid_for("unrelated-curated-dataset"),
            "name": "unrelated", "status": "pending_review",
            "pipeline_run_id": _uuid_for("unrelated-pipeline-run"),
        })
    return 200, items


def _start_review(state: _FakeState, match: "re.Match[str]", body: dict) -> tuple[int, Any]:
    return 200, {"review_id": _uuid_for(f"{match.group('dataset_id')}:review"), "status": "in_review",
                 "pipeline_run_id": body.get("pipeline_run_id")}


def _approve_review(state: _FakeState, match: "re.Match[str]", body: dict) -> tuple[int, Any]:
    state.curated_approvals.append({"review_id": match.group("review_id"), "pipeline_run_id": str(body.get("pipeline_run_id") or "")})
    return 200, {"review_id": match.group("review_id"), "status": "approved"}


def _persist_preparation(state: _FakeState, _match: "re.Match[str]", body: dict) -> tuple[int, Any]:
    if "preparation_evidence" in state.dropped:
        raise _FakeNotFound("PREPARATION_EVIDENCE_NOT_PERSISTED")
    snapshot_id = _uuid_for(f"{body['run_id']}:{body['journey_id']}:snapshot")
    persisted = dict(body)
    persisted["semantic_snapshot_id"] = snapshot_id
    persisted["snapshot_input"] = {
        "pipeline_run_id": body["pipeline_run_id"],
        "dataset_version_id": body["dataset_version_id"],
    }
    persisted["snapshot_inputs"] = [{
        "snapshot_id": snapshot_id,
        "pipeline_run_id": body["pipeline_run_id"],
        "dataset_version_id": body["dataset_version_id"],
    }]
    state.preparations[(body["run_id"], body["journey_id"])] = persisted
    return 201, {"data": persisted}


def _get_preparation(state: _FakeState, match: "re.Match[str]", _body: dict) -> tuple[int, Any]:
    run_id, journey_id = match.group("run_id"), match.group("journey_id")
    record = state.preparations.get((run_id, journey_id))
    if record is None and state.implicit_preparation_evidence:
        record = {
            "run_id": run_id, "journey_id": journey_id,
            "ontology_release_id": _uuid_for(f"{run_id}:{journey_id}:release"),
            "semantic_snapshot_id": _uuid_for(f"{run_id}:{journey_id}:snapshot"),
            "pipeline_run_id": _uuid_for(f"{run_id}:{journey_id}:pipeline-run"),
            "dataset_version_id": _uuid_for(f"{run_id}:{journey_id}:dataset-version"),
            "snapshot_inputs": [{
                "snapshot_id": _uuid_for(f"{run_id}:{journey_id}:snapshot"),
                "pipeline_run_id": _uuid_for(f"{run_id}:{journey_id}:pipeline-run"),
                "dataset_version_id": _uuid_for(f"{run_id}:{journey_id}:dataset-version"),
            }],
            "model_probe": {"requested_model": MODEL_ID, "observed_model": MODEL_ID},
            "model_calls": [{"call_kind": "ontology", "logical_call_index": 1,
                             "correlation_id": f"{run_id}:{journey_id}:ontology:1",
                             "requested_model": MODEL_ID, "observed_model": MODEL_ID,
                             "http_attempts": 1, "retry_count": 0}],
        }
    if record is None:
        raise _FakeNotFound("PREPARATION_EVIDENCE_NOT_PERSISTED")
    return 200, {"data": record}


def _create_ontology(state: _FakeState, _match: "re.Match[str]", body: dict) -> tuple[int, Any]:
    journey_id = _journey_of(str(body.get("name", "")))
    ontology_id = _uuid_for(f"{journey_id}:ontology")
    state.ontology_completions[ontology_id] = {"entities": [], "relations": []}
    return 201, {"data": {
        "id": ontology_id,
        "name": body.get("name"),
        "domain": body.get("domain"),
        "status": "draft",
    }}


def _create_entity(state: _FakeState, match: "re.Match[str]", body: dict) -> tuple[int, Any]:
    ontology_id = match.group("ontology_id")
    name = str(body.get("name_cn") or "")
    state.ontology_completions.setdefault(ontology_id, {"entities": [], "relations": []})["entities"].append(name)
    return 201, {"data": {"id": _uuid_for(f"{ontology_id}:entity:{name}")}}


def _create_relation(state: _FakeState, match: "re.Match[str]", body: dict) -> tuple[int, Any]:
    ontology_id = match.group("ontology_id")
    relation = str(body.get("type") or "")
    state.ontology_completions.setdefault(ontology_id, {"entities": [], "relations": []})["relations"].append(relation)
    return 200, {"data": {"id": _uuid_for(f"{ontology_id}:relation:{relation}")}}


def _mark_created_ontology(state: _FakeState, match: "re.Match[str]", _body: dict) -> tuple[int, Any]:
    ontology_id = match.group("ontology_id")
    return 200, {"data": {"ontology_id": ontology_id, "status": "created"}}


def _publish_ontology(state: _FakeState, match: "re.Match[str]", _body: dict) -> tuple[int, Any]:
    if "ontology_release" in state.dropped:
        raise _FakeNotFound("ONTOLOGY_NOT_FOUND")
    ontology_id = match.group("ontology_id")
    return 201, {"data": {
        "ontology_id": ontology_id,
        "release_id": _uuid_for(f"{ontology_id}:release"),
        "version_no": 1,
        "status": "published",
        "schema_hash": _hash_for(f"{ontology_id}:schema"),
    }}


def _ontology_tools(state: _FakeState, match: "re.Match[str]", _body: dict) -> tuple[int, Any]:
    ontology_id = match.group("ontology_id")
    tools: list[dict] = []
    if "mcp_descriptor" not in state.dropped:
        tools = [
            {"descriptor_id": f"query:{ontology_id}", "capability": "instance_read", "version": 1},
            {"descriptor_id": f"logic:{ontology_id}-rule", "capability": "logic_execute", "version": 1},
        ]
    return 200, {"data": {
        "ontology_id": ontology_id,
        "published": True,
        "release_id": _uuid_for(f"{ontology_id}:release"),
        "tools": tools,
    }}


def _create_grant(state: _FakeState, _match: "re.Match[str]", body: dict) -> tuple[int, Any]:
    if "ontology_data_grant" in state.dropped:
        raise _FakeNotFound("ONTOLOGY_NOT_FOUND")
    return 201, {"data": {
        "id": _uuid_for("grant"),
        "ontology_id": body.get("ontology_id"),
        "user_id": body.get("user_id"),
        "capabilities": body.get("capabilities") or [],
        "status": "active",
        "revision": 1,
    }}


def _get_turn(state: _FakeState, match: "re.Match[str]", _body: dict) -> tuple[int, Any]:
    turn_id = match.group("turn_id")
    return 200, {"data": {
        "turn_id": turn_id,
        "session_id": _uuid_for("session"),
        "status": "succeeded",
        "response_message_id": _uuid_for(f"{turn_id}:response"),
        "error_code": None,
    }}


def _turn_events(state: _FakeState, match: "re.Match[str]", _body: dict) -> tuple[int, Any]:
    turn_id = match.group("turn_id")
    run_id, journey_id = _RUN_BY_TURN.get(turn_id, ("unknown", "supply_chain"))
    manifest = load_journey_manifest(journey_id, _RUNTIME_DATA_DIR)
    preparation = state.preparations.get((run_id, journey_id))
    release_id = (preparation or {}).get("ontology_release_id") or _uuid_for(f"{run_id}:{journey_id}:release")
    minima = manifest.semantic_minima
    citations = list(minima.get("source_citation_ids") or [])
    items = [
        {"sequence": 1, "event_type": "turn_started", "payload": {
            "agent_id": _uuid_for(f"{run_id}:{journey_id}:agent"),
            "agent_version_id": _uuid_for(f"{run_id}:{journey_id}:agent-version"),
        }},
        {"sequence": 2, "event_type": "resolve_snapshot", "payload": {
            "release_id": release_id, "citations": citations,
        }},
        {"sequence": 3, "event_type": "model_call", "payload": {
            "call_kind": "agent_initial", "logical_call_index": 2,
            "correlation_id": f"{run_id}:{journey_id}:agent_initial:2",
            "model_caller": "DeepSeekVisionCaller", "model_origin": OFFICIAL_ORIGIN,
            "requested_model": MODEL_ID, "observed_model": MODEL_ID,
            "preflight_model_id": MODEL_ID,
            "model_config_version_id": _uuid_for(f"{run_id}:{journey_id}:model-version"),
            "http_attempts": 1, "retry_count": 0,
        }},
        # The REAL published query descriptor of the SAME ontology this fake
        # application creates and publishes for the journey (`_create_ontology`
        # and `_ontology_tools` both key off `_uuid_for(f"{journey_id}:
        # ontology")`) — `LangGraphRuntime._execute_journey_tool_call` resolves
        # this id from the ontology's own published catalog, so a trace using a
        # run-scoped id here would describe something the real application can
        # never emit, and `_require_granted_mcp_descriptors` would (correctly)
        # reject it.
        {"sequence": 4, "event_type": "tool_executed", "payload": {
            "descriptor_id": _granted_query_descriptor_id(journey_id),
            "outcome": "allowed", "correlation_id": f"tool:{turn_id}",
        }},
        {"sequence": 5, "event_type": "model_call", "payload": {
            "call_kind": "agent_final", "logical_call_index": 3,
            "correlation_id": f"{run_id}:{journey_id}:agent_final:3",
            "model_caller": "DeepSeekVisionCaller", "model_origin": OFFICIAL_ORIGIN,
            "requested_model": MODEL_ID, "observed_model": MODEL_ID,
            "preflight_model_id": MODEL_ID,
            "model_config_version_id": _uuid_for(f"{run_id}:{journey_id}:model-version"),
            "http_attempts": 1, "retry_count": 0,
        }},
        {"sequence": 6, "event_type": "final_response", "payload": {
            "audit_event_id": _uuid_for(f"{turn_id}:audit"),
            "receipt_id": _uuid_for(f"{turn_id}:receipt"),
            "sandbox_receipt_id": _uuid_for(f"{turn_id}:sandbox"),
            "automatic_action": str(minima.get("low_risk_action") or ""),
        }},
        {"sequence": 7, "event_type": "turn_succeeded", "payload": {}},
    ]
    return 200, {"data": {"items": items, "next_cursor": None, "has_more": False, "terminal": True}}


def _get_plan_from_turn(state: _FakeState, match: "re.Match[str]", _body: dict) -> tuple[int, Any]:
    """Mirrors the real, consolidated `GET /api/v2/runtime/action-plans/
    from-turn/{plan_id}` projection (`app.services.runtime.turn_plans.
    get_governed_plan`) — a flat body with exactly the `PlanBranchEvidence`
    fields, not the unrelated investigation-based `RuntimePlan` shape."""
    plan_id = match.group("plan_id")
    record = _PLAN_BY_ID.get(plan_id)
    if record is None:
        raise _FakeNotFound("PLAN_NOT_FOUND")
    return 200, dict(record)


_ROUTES: list[tuple[str, str, Handler]] = [
    (r"/api/v1/auth/login", "POST", _login),
    (r"/api/v1/auth/profile", "GET", _profile),
    (r"/api/v1/models", "POST", _create_model),
    (r"/api/v1/models/(?P<model_id>[^/]+)/versions", "POST", _create_model_version),
    (r"/api/v2/datasets/upload", "POST", _upload_dataset),
    (r"/api/v2/pipelines", "POST", _create_pipeline),
    (r"/api/v2/pipelines/(?P<pipeline_id>[^/]+)/run-sync", "POST", _run_pipeline),
    (r"/api/v2/pipelines/runs/(?P<run_id>[^/]+)", "GET", _get_run),
    (r"/api/v2/curated", "GET", _list_curated),
    (r"/api/v2/curated/(?P<dataset_id>[^/]+)/reviews", "POST", _start_review),
    (r"/api/v2/curated/reviews/(?P<review_id>[^/]+)/approve", "POST", _approve_review),
    (r"/api/v1/business-journeys/preparations", "POST", _persist_preparation),
    (r"/api/v1/business-journeys/preparations/(?P<run_id>[^/]+)/(?P<journey_id>[^/]+)", "GET", _get_preparation),
    (r"/api/v1/ontologies", "POST", _create_ontology),
    (r"/api/v1/ontologies/(?P<ontology_id>[^/]+)/entities", "POST", _create_entity),
    (r"/api/v1/ontologies/(?P<ontology_id>[^/]+)/graph/relations", "POST", _create_relation),
    (r"/api/v1/ontologies/(?P<ontology_id>[^/]+)/mark-created", "POST", _mark_created_ontology),
    (r"/api/v1/ontologies/(?P<ontology_id>[^/]+)/publish", "POST", _publish_ontology),
    (r"/api/v1/ontologies/(?P<ontology_id>[^/]+)/tools", "GET", _ontology_tools),
    (r"/api/v1/ontology-data-grants", "POST", _create_grant),
    (r"/api/v1/agent-turns/(?P<turn_id>[^/]+)/events", "GET", _turn_events),
    (r"/api/v1/agent-turns/(?P<turn_id>[^/]+)", "GET", _get_turn),
    (r"/api/v2/runtime/action-plans/from-turn/(?P<plan_id>[^/]+)", "GET", _get_plan_from_turn),
]

# Lookup tables the read-only fake routes consult; populated by
# `write_browser_evidence` so the fake serves exactly the records the
# browser claims to have persisted.
_PLAN_BY_ID: dict[str, dict] = {}
_APPROVAL_BY_ID: dict[str, dict] = {}
_RUN_BY_TURN: dict[str, tuple[str, str]] = {}


def _deepseek_transport(state: _FakeState) -> httpx.MockTransport:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/models":
            return httpx.Response(200, json={"data": [{"id": m} for m in state.available_models]})
        if request.url.path == "/chat/completions":
            state.model_calls += 1
            return httpx.Response(200, json={
                "model": state.model_response_id,
                "choices": [{"message": {"content": json.dumps(_STRUCTURED_RESPONSE[0])}}],
            })
        return httpx.Response(404, json={"error": "unknown"})

    return httpx.MockTransport(handle)


# Mutable single-slot holder so each test can install the structured answer the
# journey under test expects without changing the transport wiring.
_STRUCTURED_RESPONSE: list[dict] = [{}]


def structured_answer(journey_id: str) -> dict:
    """A response that genuinely satisfies the journey's semantic minimum —
    built from the real Task 1 manifest, never hand-copied."""
    manifest = load_journey_manifest(journey_id, _RUNTIME_DATA_DIR)
    minima = manifest.semantic_minima
    keywords = list(minima.get("keywords") or [])
    answer = " ".join(keywords) + " analysis complete."
    payload: dict[str, Any] = {
        "answer": answer,
        "entities": list(minima.get("entities") or []),
        "relations": list(minima.get("relations") or []),
        "rules": list(minima.get("rules") or []),
        "actions": list(minima.get("actions") or []),
        "citations": list(minima.get("source_citation_ids") or []),
    }
    for name, predicate in (minima.get("numeric_predicates") or {}).items():
        # Task 1's real fixtures record a flat `{name: number}` fact (see
        # `orchestrator._normalize_numeric_predicates`, which turns each into
        # an equality check on a same-named top-level field); a dict-shaped
        # predicate is also accepted so a future richer fixture keeps working.
        if isinstance(predicate, Mapping):
            path = str(predicate.get("path", name))
            node = payload
            segments = path.split(".")
            for segment in segments[:-1]:
                node = node.setdefault(segment, {})
            node[segments[-1]] = _satisfying_value(predicate)
        else:
            payload[name] = predicate
    return payload


def _satisfying_value(predicate: Mapping) -> float:
    op = str(predicate.get("op", "eq"))
    value = predicate.get("value")
    if not isinstance(value, (int, float)):
        return 0.0
    return {
        "lt": value - 1, "le": value, "gt": value + 1,
        "ge": value, "eq": value, "ne": value + 1,
    }.get(op, value)


def write_staging_run_manifest(
    output_dir: Path, run_id: str, journey_id: str, *, mcp_descriptor_ids: list[str] | None = None,
) -> Path:
    """Stand in for the staging run manifest ``prepare_journey`` writes.

    ``verify_journey`` reads this file back to cross-check the descriptor
    ids the turn actually executed against the ones preparation really
    published and granted (``_require_granted_mcp_descriptors``), so a
    verify-only test needs one on disk. ``test_verify_cross_checks_
    descriptors_against_a_real_prepared_run_manifest`` covers the same
    check against a manifest written by the REAL ``prepare_all_journeys``.
    """
    if mcp_descriptor_ids is None:
        mcp_descriptor_ids = [_granted_query_descriptor_id(journey_id)]
    path = Path(output_dir) / "business_journeys" / "staging" / "run.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    document: dict[str, Any] = {
        "schema_version": 1, "run_id": run_id, "model_caller": "DeepSeekVisionCaller",
        "model_origin": OFFICIAL_ORIGIN, "requested_model_id": MODEL_ID, "preparations": [],
    }
    if path.exists():
        document = json.loads(path.read_text(encoding="utf-8"))
    document["preparations"] = [
        entry for entry in document.get("preparations") or []
        if entry.get("journey_id") != journey_id
    ] + [{
        "journey_id": journey_id,
        "ontology_id": _uuid_for(f"{journey_id}:ontology"),
        "ontology_release_id": _uuid_for(f"{run_id}:{journey_id}:release"),
        "mcp_descriptor_ids": list(mcp_descriptor_ids),
        "status": "passed",
    }]
    path.write_text(json.dumps(document, indent=2, sort_keys=True), encoding="utf-8")
    return path


def write_browser_evidence(
    output_dir: Path, run_id: str, journey_id: str, document: Mapping[str, Any] | None = None,
    *, staging_manifest: bool = True,
) -> dict:
    """Materialize the browser evidence document ``verify_journey`` reads,
    and register its records with the read-only fake routes.

    ``staging_manifest=False`` leaves the staging run manifest alone, for a
    test that wrote a real one with ``prepare_all_journeys`` first."""
    base = browser_evidence_document(run_id, journey_id)
    if document is not None:
        base.update(document)
    document = base
    _RUN_BY_TURN[document["turn_id"]] = (run_id, journey_id)
    for branch in document["plan_branches"]:
        _PLAN_BY_ID[branch["action_plan_id"]] = branch
        _APPROVAL_BY_ID[branch["approval_id"]] = branch
    target = Path(output_dir) / "business_journeys" / "browser" / f"{run_id}.{journey_id}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(document, indent=2, sort_keys=True), encoding="utf-8")
    if staging_manifest:
        write_staging_run_manifest(output_dir, run_id, journey_id)
    return document


@pytest.fixture
def fake_api(monkeypatch, tmp_path):
    """A real local HTTP application plus a real DeepSeek mock transport."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-deepseek-key")
    # `verify_journey` (Critical Finding #4) authenticates by reading this
    # SAME env var `prepare_journey`'s own `api_key="runtime/runtime"` calls
    # already use throughout this file's given test code.
    monkeypatch.setenv("BUSINESS_JOURNEY_API_KEY", "runtime/runtime")

    state = _FakeState()
    _PLAN_BY_ID.clear()
    _APPROVAL_BY_ID.clear()
    _RUN_BY_TURN.clear()
    _STRUCTURED_RESPONSE[0] = structured_answer("supply_chain")

    handler = type("_BoundHandler", (_FakeApiHandler,), {"state": state})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    state.url = f"http://127.0.0.1:{server.server_address[1]}"

    transport = _deepseek_transport(state)
    original_client = DeepSeekVisionClient._client

    def patched_client(self):
        client = original_client(self)
        client.close()
        return httpx.Client(
            timeout=self._timeout_seconds, follow_redirects=False, transport=transport,
            headers={"Authorization": f"Bearer {self._api_key}"},
        )

    monkeypatch.setattr(DeepSeekVisionClient, "_client", patched_client)
    monkeypatch.setattr(DeepSeekVisionClient, "_sleep", staticmethod(lambda _seconds: None))

    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture
def prepared_journeys(fake_api):
    """Every journey's structured answer installed for a multi-journey run."""
    def _install(journey_id: str) -> None:
        _STRUCTURED_RESPONSE[0] = structured_answer(journey_id)

    return _install


# ---------------------------------------------------------------------------
# Preparation
# ---------------------------------------------------------------------------


def test_prepare_requires_published_release_grant_and_binding_options(fake_api):
    fake_api.drop("mcp_descriptor")
    with pytest.raises(JourneyAcceptanceError, match="MCP_DESCRIPTOR_MISSING"):
        prepare_journey(
            "supply_chain", api_base=fake_api.url, api_key="runtime/runtime",
            output_dir=Path("artifacts"), run_id="journey-test",
        )


def test_prepare_rejects_observed_model_drift(fake_api):
    _STRUCTURED_RESPONSE[0] = structured_answer("finance")
    fake_api.model_response_id = "deepseek-v4-flash"
    with pytest.raises(JourneyAcceptanceError, match="MODEL_ID_MISMATCH"):
        prepare_journey(
            "finance", api_base=fake_api.url, api_key="runtime/runtime",
            output_dir=Path("artifacts"), run_id="journey-model-drift",
        )


def test_prepare_never_creates_agent_or_turn(fake_api):
    preparation = prepare_journey(
        "supply_chain", api_base=fake_api.url, api_key="runtime/runtime",
        output_dir=Path("artifacts"), run_id="journey-prepare-only",
    )
    assert preparation.agent_binding_options
    assert fake_api.agent_create_calls == 0
    assert fake_api.agent_turn_calls == 0
    assert preparation.logical_model_calls == 1


def test_prepare_binding_options_carry_only_release_descriptors_and_model_version(fake_api):
    preparation = prepare_journey(
        "supply_chain", api_base=fake_api.url, api_key="runtime/runtime",
        output_dir=Path("artifacts"), run_id="journey-binding-shape",
    )
    options = preparation.agent_binding_options
    assert set(vars(options)) == {
        "ontology_release_id", "mcp_descriptor_ids", "model_config_version_id",
    }
    assert options.ontology_release_id
    assert options.mcp_descriptor_ids
    assert options.model_config_version_id


def test_prepare_uses_exactly_one_deepseek_completion_and_official_origin(fake_api):
    preparation = prepare_journey(
        "supply_chain", api_base=fake_api.url, api_key="runtime/runtime",
        output_dir=Path("artifacts"), run_id="journey-one-call",
    )
    assert fake_api.model_calls == 1
    assert preparation.call_kinds == ["ontology"]
    assert preparation.model_caller == "DeepSeekVisionCaller"
    assert preparation.model_origin == OFFICIAL_ORIGIN
    assert preparation.preflight_model_id == MODEL_ID
    assert preparation.requested_model_id == MODEL_ID
    assert preparation.observed_model_id == MODEL_ID
    assert preparation.http_attempts == 1
    assert preparation.status == "passed"
    assert preparation.correlation_id == "journey-one-call:supply_chain:ontology:1"
    assert JourneyPreparationBudget().logical_model_calls == 1
    assert JourneyPreparationBudget().max_http_attempts == 2


def test_prepare_persists_completion_snapshot_lineage_and_model_evidence(fake_api):
    """Preparation evidence must be application-owned, not a local report."""
    preparation = prepare_journey(
        "supply_chain", api_base=fake_api.url, api_key="runtime/runtime",
        output_dir=Path("artifacts"), run_id="journey-durable-preparation",
    )

    persisted = fake_api.preparations[("journey-durable-preparation", "supply_chain")]
    assert fake_api.ontology_completions[preparation.ontology_id] == {
        "entities": structured_answer("supply_chain")["entities"],
        "relations": structured_answer("supply_chain")["relations"],
    }
    assert persisted["structured"] == structured_answer("supply_chain")
    assert persisted["semantic_snapshot_id"] == preparation.semantic_snapshot_id
    assert persisted["snapshot_input"] == {
        "pipeline_run_id": preparation.pipeline.pipeline_run_id,
        "dataset_version_id": preparation.pipeline.dataset_version_id,
    }
    assert persisted["model_probe"]["observed_model"] == MODEL_ID
    assert persisted["model_calls"][0]["correlation_id"] == preparation.correlation_id


def test_prepare_scopes_curated_approval_to_the_pipeline_run(fake_api):
    fake_api.add_unrelated_curated_dataset()
    preparation = prepare_journey(
        "supply_chain", api_base=fake_api.url, api_key="runtime/runtime",
        output_dir=Path("artifacts"), run_id="journey-curated-scope",
    )
    assert fake_api.curated_approvals[-1]["pipeline_run_id"] == preparation.pipeline.pipeline_run_id


def test_prepare_fails_closed_on_missing_pipeline_run(fake_api):
    fake_api.drop("pipeline_run")
    with pytest.raises(JourneyAcceptanceError, match="PIPELINE_RUN_MISSING"):
        prepare_journey(
            "supply_chain", api_base=fake_api.url, api_key="runtime/runtime",
            output_dir=Path("artifacts"), run_id="journey-no-run",
        )


def test_prepare_fails_closed_on_missing_curated_dataset(fake_api):
    fake_api.drop("curated_dataset")
    with pytest.raises(JourneyAcceptanceError, match="CURATED_DATASET_MISSING"):
        prepare_journey(
            "supply_chain", api_base=fake_api.url, api_key="runtime/runtime",
            output_dir=Path("artifacts"), run_id="journey-no-curated",
        )


def test_prepare_fails_closed_on_missing_release(fake_api):
    fake_api.drop("ontology_release")
    with pytest.raises(JourneyAcceptanceError, match="ONTOLOGY_RELEASE_MISSING"):
        prepare_journey(
            "supply_chain", api_base=fake_api.url, api_key="runtime/runtime",
            output_dir=Path("artifacts"), run_id="journey-no-release",
        )


def test_prepare_fails_closed_on_missing_grant(fake_api):
    fake_api.drop("ontology_data_grant")
    with pytest.raises(JourneyAcceptanceError, match="ONTOLOGY_DATA_GRANT_MISSING"):
        prepare_journey(
            "supply_chain", api_base=fake_api.url, api_key="runtime/runtime",
            output_dir=Path("artifacts"), run_id="journey-no-grant",
        )


def test_prepare_rejects_a_model_id_other_than_the_official_one(fake_api):
    with pytest.raises(JourneyAcceptanceError, match="MODEL_ID_NOT_ALLOWED"):
        prepare_journey(
            "supply_chain", api_base=fake_api.url, api_key="runtime/runtime",
            output_dir=Path("artifacts"), run_id="journey-wrong-model",
            model_id="deepseek-chat",
        )
    assert fake_api.model_calls == 0


def test_prepare_rejects_a_generic_answer_that_misses_the_semantic_minimum(fake_api):
    _STRUCTURED_RESPONSE[0] = {"answer": "Everything looks fine.", "entities": [], "citations": []}
    with pytest.raises(JourneyAcceptanceError, match="SEMANTIC_MINIMUM_FAILED"):
        prepare_journey(
            "supply_chain", api_base=fake_api.url, api_key="runtime/runtime",
            output_dir=Path("artifacts"), run_id="journey-generic",
        )


def test_prepare_all_journeys_returns_three_in_fixed_order_and_writes_run_manifest(prepared_journeys, fake_api):
    preparations = prepare_all_journeys(
        api_base=fake_api.url, api_key="runtime/runtime",
        output_dir=Path("artifacts"), run_id="journey-all",
        before_journey=prepared_journeys,
    )
    assert [p.journey_id for p in preparations] == ["supply_chain", "finance", "credit"]
    assert all(p.status == "passed" for p in preparations)
    assert fake_api.model_calls == 3

    manifest_path = Path("artifacts") / "business_journeys" / "staging" / "run.json"
    document = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert [entry["journey_id"] for entry in document["preparations"]] == [
        "supply_chain", "finance", "credit",
    ]
    assert read_run_manifest(Path("artifacts"))["run_id"] == "journey-all"


def test_run_manifest_never_carries_agent_ids_prompts_or_credentials(prepared_journeys, fake_api):
    prepare_all_journeys(
        api_base=fake_api.url, api_key="runtime/runtime",
        output_dir=Path("artifacts"), run_id="journey-manifest-safety",
        before_journey=prepared_journeys,
    )
    raw = (Path("artifacts") / "business_journeys" / "staging" / "run.json").read_text(encoding="utf-8")
    for forbidden in ("agent_id", "agent_version_id", "prompt", "api_key", "test-deepseek-key",
                      "runtime/runtime", "Authorization"):
        assert forbidden not in raw


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------


def test_verify_cross_checks_descriptors_against_a_real_prepared_run_manifest(
    prepared_journeys, fake_api,
):
    """Final whole-branch review: `mcp_descriptor_ids` was the ONE identity
    field with no cross-check against a second source, so the value collected
    from `tool_executed.descriptor_id` could be anything at all. Here the
    staging run manifest is written by the REAL `prepare_all_journeys` (not a
    stub), and the persisted trace's descriptor is only accepted because it
    genuinely is one of the descriptors preparation published and granted."""
    prepare_all_journeys(
        api_base=fake_api.url, api_key="runtime/runtime",
        output_dir=Path("artifacts"), run_id="journey-granted-descriptors",
        before_journey=prepared_journeys,
    )
    granted = {
        entry["journey_id"]: entry["mcp_descriptor_ids"]
        for entry in read_run_manifest(Path("artifacts"))["preparations"]
    }
    assert _granted_query_descriptor_id("credit") in granted["credit"]

    browser = browser_evidence_document("journey-granted-descriptors", "credit")
    browser["ontology_release_id"] = fake_api.preparations[
        ("journey-granted-descriptors", "credit")
    ]["ontology_release_id"]
    write_browser_evidence(
        Path("artifacts"), "journey-granted-descriptors", "credit", browser, staging_manifest=False,
    )
    verification = verify_journey(
        "credit", api_base=fake_api.url, output_dir=Path("artifacts"),
        run_id="journey-granted-descriptors",
    )
    assert verification.mcp_descriptor_ids == (_granted_query_descriptor_id("credit"),)


def test_verify_rejects_a_descriptor_the_preparation_never_granted(fake_api):
    """The negative half of the same cross-check: a turn that executed a
    descriptor outside the granted set must fail closed, not be recorded as
    passing evidence."""
    write_browser_evidence(Path("artifacts"), "journey-ungranted", "credit")
    write_staging_run_manifest(
        Path("artifacts"), "journey-ungranted", "credit",
        mcp_descriptor_ids=["query:some-other-ontology"],
    )
    with pytest.raises(JourneyAcceptanceError, match="MCP_DESCRIPTOR_NOT_GRANTED"):
        verify_journey(
            "credit", api_base=fake_api.url, output_dir=Path("artifacts"),
            run_id="journey-ungranted",
        )


def test_verify_reads_only_persisted_evidence(fake_api):
    write_browser_evidence(Path("artifacts"), "journey-verify-only", "credit")
    fake_api.mutations.clear()
    verification = verify_journey(
        "credit", api_base=fake_api.url, output_dir=Path("artifacts"),
        run_id="journey-verify-only",
    )
    assert verification.status == "passed"
    assert verification.logical_model_calls == 3
    assert verification.http_attempts <= 6
    assert verification.tool_rounds == 1
    assert verification.model_caller == "DeepSeekVisionCaller"
    assert verification.model_origin == "https://api.deepseek.com"
    assert verification.preflight_model_id == "deepseek-v4-flash-vision-exp"
    assert verification.call_kinds == ["ontology", "agent_initial", "agent_final"]
    assert len(verification.correlation_ids) == 3
    assert {item.branch for item in verification.plan_branches} == {
        "approved", "rejected", "expired"
    }
    assert len({item.action_plan_id for item in verification.plan_branches}) == 3
    assert len({item.plan_hash for item in verification.plan_branches}) == 3
    assert len({item.approval_id for item in verification.plan_branches}) == 3
    assert len({item.target_fixture_id for item in verification.plan_branches}) == 3
    assert verification.plan_branches_by_name["approved"].must_write is True
    assert verification.plan_branches_by_name["rejected"].must_write is False
    assert verification.plan_branches_by_name["expired"].must_write is False
    assert verification.plan_branches_by_name["rejected"].target_before_hash == verification.plan_branches_by_name["rejected"].target_after_hash
    assert verification.plan_branches_by_name["expired"].target_before_hash == verification.plan_branches_by_name["expired"].target_after_hash
    assert fake_api.model_calls == 0
    assert fake_api.mutations == []


def test_verify_fails_when_preparation_model_evidence_is_missing_or_mismatched(fake_api):
    run_id = "journey-missing-preparation-model-evidence"
    write_browser_evidence(Path("artifacts"), run_id, "credit")
    fake_api.implicit_preparation_evidence = False
    with pytest.raises(JourneyAcceptanceError, match="PREPARATION_EVIDENCE_NOT_PERSISTED"):
        verify_journey("credit", api_base=fake_api.url, output_dir=Path("artifacts"), run_id=run_id)

    fake_api.preparations[(run_id, "credit")] = {
        "run_id": run_id, "journey_id": "credit",
        "ontology_release_id": _uuid_for(f"{run_id}:credit:release"),
        "semantic_snapshot_id": _uuid_for(f"{run_id}:credit:snapshot"),
        "pipeline_run_id": _uuid_for("credit:pipeline-run"),
        "dataset_version_id": _uuid_for("credit:dataset-version"),
        "snapshot_inputs": [{"snapshot_id": _uuid_for(f"{run_id}:credit:snapshot"),
                             "pipeline_run_id": _uuid_for("credit:pipeline-run"),
                             "dataset_version_id": _uuid_for("credit:dataset-version")}],
        "model_probe": {"requested_model": MODEL_ID, "observed_model": MODEL_ID},
        "model_calls": [{"call_kind": "ontology", "logical_call_index": 1,
                         "correlation_id": "wrong", "requested_model": MODEL_ID,
                         "observed_model": MODEL_ID, "http_attempts": 1, "retry_count": 0}],
    }
    with pytest.raises(JourneyAcceptanceError, match="CORRELATION_ID_INVALID"):
        verify_journey("credit", api_base=fake_api.url, output_dir=Path("artifacts"), run_id=run_id)


def test_verify_requires_exact_persisted_snapshot_lineage_and_release(fake_api):
    run_id = "journey-snapshot-lineage"
    write_browser_evidence(Path("artifacts"), run_id, "credit")
    fake_api.implicit_preparation_evidence = False
    fake_api.preparations[(run_id, "credit")] = {
        "run_id": run_id, "journey_id": "credit",
        "ontology_release_id": _uuid_for("different-release"),
        "semantic_snapshot_id": _uuid_for(f"{run_id}:credit:snapshot"),
        "pipeline_run_id": _uuid_for(f"{run_id}:credit:pipeline-run"),
        "dataset_version_id": _uuid_for(f"{run_id}:credit:dataset-version"),
        "snapshot_inputs": [{
            "snapshot_id": _uuid_for("other-snapshot"),
            "pipeline_run_id": _uuid_for(f"{run_id}:credit:pipeline-run"),
            "dataset_version_id": _uuid_for(f"{run_id}:credit:dataset-version"),
        }],
        "model_probe": {"requested_model": MODEL_ID, "observed_model": MODEL_ID},
        "model_calls": [{"call_kind": "ontology", "logical_call_index": 1,
                         "correlation_id": f"{run_id}:credit:ontology:1", "requested_model": MODEL_ID,
                         "observed_model": MODEL_ID, "http_attempts": 1, "retry_count": 0}],
    }
    with pytest.raises(JourneyAcceptanceError, match="ONTOLOGY_RELEASE_ID_MISMATCH"):
        verify_journey("credit", api_base=fake_api.url, output_dir=Path("artifacts"), run_id=run_id)


def test_verify_rejects_same_release_with_wrong_snapshot_input_tuple(fake_api):
    run_id = "journey-wrong-snapshot-input"
    write_browser_evidence(Path("artifacts"), run_id, "credit")
    fake_api.implicit_preparation_evidence = False
    release_id = _uuid_for(f"{run_id}:credit:release")
    fake_api.preparations[(run_id, "credit")] = {
        "run_id": run_id, "journey_id": "credit", "ontology_release_id": release_id,
        "semantic_snapshot_id": _uuid_for(f"{run_id}:credit:snapshot"),
        "pipeline_run_id": _uuid_for(f"{run_id}:credit:pipeline-run"),
        "dataset_version_id": _uuid_for(f"{run_id}:credit:dataset-version"),
        "snapshot_inputs": [{
            "snapshot_id": _uuid_for("different-snapshot"),
            "pipeline_run_id": _uuid_for(f"{run_id}:credit:pipeline-run"),
            "dataset_version_id": _uuid_for(f"{run_id}:credit:dataset-version"),
        }],
        "model_probe": {"requested_model": MODEL_ID, "observed_model": MODEL_ID},
        "model_calls": [{"call_kind": "ontology", "logical_call_index": 1,
                         "correlation_id": f"{run_id}:credit:ontology:1", "requested_model": MODEL_ID,
                         "observed_model": MODEL_ID, "http_attempts": 1, "retry_count": 0}],
    }
    document = browser_evidence_document(run_id, "credit")
    document["ontology_release_id"] = release_id
    write_browser_evidence(Path("artifacts"), run_id, "credit", document)
    with pytest.raises(JourneyAcceptanceError, match="PREPARATION_SNAPSHOT_LINEAGE_MISSING"):
        verify_journey("credit", api_base=fake_api.url, output_dir=Path("artifacts"), run_id=run_id)


def test_verify_rejects_shared_plan_identity_across_branches(fake_api):
    """A real `action_plan_id` is a database-generated primary key, so two
    genuinely distinct governed plans can never physically share one — the
    read-back's own per-branch consistency check (`PLAN_BRANCH_MISMATCH`)
    catches this before `_require_independent_plan_branches`'s cross-branch
    distinctness check ever gets a chance to run, since the single record
    the (fake) application actually holds under the shared id cannot answer
    to two different declared branches at once. Both outcomes are `verify_
    journey` correctly refusing to accept the claim; this asserts the one
    that is actually reachable."""
    document = browser_evidence_document("journey-shared-plan", "credit")
    document["plan_branches"][1]["action_plan_id"] = document["plan_branches"][0]["action_plan_id"]
    document["plan_branches"][1]["plan_hash"] = document["plan_branches"][0]["plan_hash"]
    write_browser_evidence(Path("artifacts"), "journey-shared-plan", "credit", document)
    with pytest.raises(JourneyAcceptanceError, match="PLAN_BRANCH_MISMATCH"):
        verify_journey(
            "credit", api_base=fake_api.url, output_dir=Path("artifacts"),
            run_id="journey-shared-plan",
        )


def test_verify_rejects_an_approved_branch_whose_target_never_changed(fake_api):
    document = browser_evidence_document("journey-no-write", "credit")
    approved = next(b for b in document["plan_branches"] if b["branch"] == "approved")
    approved["target_after_hash"] = approved["target_before_hash"]
    write_browser_evidence(Path("artifacts"), "journey-no-write", "credit", document)
    with pytest.raises(JourneyAcceptanceError, match="APPROVED_TARGET_NOT_MUTATED"):
        verify_journey(
            "credit", api_base=fake_api.url, output_dir=Path("artifacts"),
            run_id="journey-no-write",
        )


def test_verify_rejects_a_rejected_branch_whose_target_changed(fake_api):
    document = browser_evidence_document("journey-bad-reject", "credit")
    rejected = next(b for b in document["plan_branches"] if b["branch"] == "rejected")
    rejected["target_after_hash"] = _hash_for("mutated")
    write_browser_evidence(Path("artifacts"), "journey-bad-reject", "credit", document)
    with pytest.raises(JourneyAcceptanceError, match="NON_APPROVED_TARGET_MUTATED"):
        verify_journey(
            "credit", api_base=fake_api.url, output_dir=Path("artifacts"),
            run_id="journey-bad-reject",
        )


def test_verify_fails_when_a_branch_record_is_absent(fake_api):
    document = browser_evidence_document("journey-two-branches", "credit")
    document["plan_branches"] = document["plan_branches"][:2]
    write_browser_evidence(Path("artifacts"), "journey-two-branches", "credit", document)
    with pytest.raises(JourneyAcceptanceError, match="PLAN_BRANCHES_INCOMPLETE"):
        verify_journey(
            "credit", api_base=fake_api.url, output_dir=Path("artifacts"),
            run_id="journey-two-branches",
        )


def test_verify_fails_when_the_persisted_plan_is_not_readable_from_the_application(fake_api):
    document = browser_evidence_document("journey-unpersisted", "credit")
    write_browser_evidence(Path("artifacts"), "journey-unpersisted", "credit", document)
    _PLAN_BY_ID.pop(document["plan_branches"][0]["action_plan_id"])
    with pytest.raises(JourneyAcceptanceError, match="PLAN_NOT_PERSISTED"):
        verify_journey(
            "credit", api_base=fake_api.url, output_dir=Path("artifacts"),
            run_id="journey-unpersisted",
        )


def test_verify_all_journeys_reads_the_fixed_order_and_makes_no_mutation(fake_api):
    for journey_id in ("supply_chain", "finance", "credit"):
        write_browser_evidence(Path("artifacts"), "journey-verify-all", journey_id)
    fake_api.mutations.clear()
    verifications = verify_all_journeys(
        api_base=fake_api.url, output_dir=Path("artifacts"), run_id="journey-verify-all",
    )
    assert [v.journey_id for v in verifications] == ["supply_chain", "finance", "credit"]
    assert all(v.status == "passed" for v in verifications)
    assert fake_api.mutations == []
    assert fake_api.model_calls == 0
    # Every request is a GET except the one login call each of the three
    # `verify_journey` calls makes to authenticate (Critical Finding #4) —
    # never a governed-state write.
    non_get_requests = [r for r in fake_api.requests if not r.startswith("GET ")]
    assert non_get_requests == ["POST /api/v1/auth/login"] * 3


def test_verification_record_never_carries_raw_inputs_or_credentials(fake_api):
    write_browser_evidence(Path("artifacts"), "journey-verify-safety", "credit")
    verification = verify_journey(
        "credit", api_base=fake_api.url, output_dir=Path("artifacts"),
        run_id="journey-verify-safety",
    )
    serialized = json.dumps(verification.to_dict(), sort_keys=True)
    for forbidden in ("api_key", "Authorization", "test-deepseek-key", "runtime/runtime", "prompt"):
        assert forbidden not in serialized

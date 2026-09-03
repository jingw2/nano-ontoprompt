"""HTTP client for the application under test.

Every call in this module goes to ``api_base`` — the application — except
the single ontology completion, which goes through the ONE production caller
(``app.services.model_callers.deepseek_vision.DeepSeekVisionCaller``) to the
official DeepSeek origin. ``api_base`` never configures the DeepSeek client:
the two are separate transports with separate credentials, and there is no
constructor parameter here that could point the model client anywhere else.

The read side (`read_journey_evidence`) goes through `_get` only. `_get`
refuses any non-GET verb, so a verifier can never mutate the application
even by accident.
"""
from __future__ import annotations

import base64
import json
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence
from urllib.parse import urlsplit

import httpx

from app.services.model_callers.deepseek_vision import DeepSeekVisionCaller

from .contracts import (
    MODEL_ID,
    OFFICIAL_ORIGIN,
    BusinessJourneyModelError,
    ImmutableModelConfigVersion,
    InputPart,
    JourneyModelContext,
    ModelProbe,
    ModelResponse,
    sha256_bytes,
)

REPO_ROOT = Path(__file__).resolve().parents[3]

# The response contract the ontology completion is asked for. The flat
# name arrays are deliberately the same shape
# `semantic_validators.validate_semantic_minimum` reads.
#
# `relation_edges` is the one field that exists purely so the extraction can
# actually be WRITTEN BACK into the ontology: `POST /api/v1/ontologies/{id}/
# graph/relations` stores `source_entity`/`target_entity` as real
# `entities.id` foreign keys, and the publication compiler's
# `preflight_ontology` rejects the whole release with
# `UNRESOLVED_RELATION_ENDPOINT` if either endpoint does not resolve. A bare
# relation NAME ("SUPPLIES") carries no endpoints, so it is fundamentally
# insufficient to build a valid `Relation` row from — the model has to name
# the two entities each edge connects, and those names must be entities it
# also listed in `entities`.
ONTOLOGY_RESPONSE_SCHEMA: Mapping[str, object] = {
    "type": "object",
    "required": [
        "answer", "entities", "relations", "relation_edges", "rules", "actions", "citations",
    ],
    "properties": {
        "answer": {"type": "string"},
        "entities": {"type": "array", "items": {"type": "string"}},
        "relations": {"type": "array", "items": {"type": "string"}},
        "relation_edges": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["name", "source", "target"],
                "properties": {
                    "name": {"type": "string"},
                    "source": {"type": "string"},
                    "target": {"type": "string"},
                },
            },
        },
        "rules": {"type": "array", "items": {"type": "string"}},
        "actions": {"type": "array", "items": {"type": "string"}},
        "citations": {"type": "array", "items": {"type": "string"}},
    },
}

# Defaults for the creation fields the real write endpoints require but the
# model's flat extraction does not carry. Deliberately generic: the point is
# a real, non-empty ontology whose content traces back to the model's own
# extraction, not a full-fidelity authoring pipeline.
_EXTRACTED_ENTITY_TYPE = "concept"
_EXTRACTED_RELATION_TYPE_FALLBACK = "关联"
_EXTRACTED_LOGIC_TYPE = "validation"
_EXTRACTED_ACTION_CATEGORY = "crud"

GRANT_CAPABILITIES = ("investigate", "propose_action")

# `POST /api/v1/ontologies` validates `domain` against a closed Chinese-label
# enum (`app.schemas.ontology.VALID_DOMAINS`) — the journey id itself
# (English, snake_case) is never a valid value, confirmed directly against
# the real endpoint.
_JOURNEY_ONTOLOGY_DOMAIN: Mapping[str, str] = {
    "supply_chain": "供应链",
    "finance": "财务",
    "credit": "金融",
}


class JourneyAcceptanceError(BusinessJourneyModelError):
    """A journey baseline or persisted-evidence requirement was not met.

    The message always leads with a stable reason code so a CI log can be
    grepped without ever embedding a response body, a fixture row, or a
    credential.
    """


# ---------------------------------------------------------------------------
# Evidence records (all closed, all free of raw inputs and credentials).
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PipelineEvidence:
    pipeline_id: str
    pipeline_run_id: str
    dataset_version_id: str
    status: str
    row_count: int | None
    input_fixture_ids: tuple[str, ...]
    input_hashes: Mapping[str, str]
    # The curated dataset THIS run produced, read from the run's own
    # `stats.curated_dataset_id` (written by
    # `app.tasks.v2.pipeline_run.pipeline_run_task`) — the real binding
    # between a pipeline run and its curated output, so `approve_curated`
    # never has to guess from list order.
    curated_dataset_id: str


@dataclass(frozen=True)
class CuratedEvidence:
    curated_dataset_id: str
    review_id: str
    status: str


@dataclass(frozen=True)
class OntologyContentEvidence:
    """The rows the model's own extraction actually created in the ontology.

    Every id here is a primary key the application returned from a real
    create endpoint — `POST .../entities`, `POST .../graph/relations`,
    `POST /api/v2/ontologies/{id}/logic`, `POST /api/v2/ontologies/{id}/
    actions` — so a non-empty tuple is proof the release that follows has
    real content, not merely that the model produced some JSON.
    """

    entity_ids: tuple[str, ...]
    relation_ids: tuple[str, ...]
    rule_ids: tuple[str, ...]
    action_ids: tuple[str, ...]


@dataclass(frozen=True)
class OntologyReleaseEvidence:
    ontology_id: str
    release_id: str
    release_status: str
    schema_hash: str
    # NOT a real `SemanticSnapshot` row id. No production API endpoint
    # anywhere in the application creates a `SemanticSnapshot`:
    # `app.services.runtime.snapshots.materialize_snapshot` (the only place
    # `SemanticSnapshot(...)` is ever constructed) and its
    # `materialize_refresh_snapshot` wrapper have zero callers outside
    # `tests/runtime/`, so there is nothing this client could call to obtain
    # one. This is a synthetic placeholder derived from the release id,
    # recorded for traceability only; nothing may treat it as an
    # independently-verifiable snapshot identity. See
    # `_semantic_snapshot_placeholder`.
    semantic_snapshot_placeholder: str
    content: OntologyContentEvidence
    structured: Mapping[str, Any]
    model_response: ModelResponse


@dataclass(frozen=True)
class McpDescriptor:
    descriptor_id: str
    capability: str
    release_id: str


@dataclass(frozen=True)
class GrantEvidence:
    grant_id: str
    ontology_id: str
    user_id: str
    release_id: str
    status: str
    capabilities: tuple[str, ...]


@dataclass(frozen=True)
class ModelConfigEvidence:
    model_config_id: str
    model_config_version_id: str
    provider: str
    model_id: str
    origin: str
    behavior_hash: str

    def as_immutable(self) -> ImmutableModelConfigVersion:
        return ImmutableModelConfigVersion(
            version_id=self.model_config_version_id,
            provider=self.provider,
            model_id=self.model_id,
            origin=self.origin,
            behavior_hash=self.behavior_hash,
            frozen_at=None,
        )


@dataclass(frozen=True)
class AgentBindingOptions:
    """Exactly the three things the browser is allowed to be handed.

    No Agent ID, session ID, turn, prompt, tool result, Sandbox output,
    approval, or target content — the browser must obtain all of those from
    the application itself.
    """

    ontology_release_id: str
    mcp_descriptor_ids: tuple[str, ...]
    model_config_version_id: str


@dataclass(frozen=True)
class PlanBranchEvidence:
    branch: str
    action_plan_id: str
    plan_hash: str
    approval_id: str
    target_fixture_id: str
    status: str
    execution_class: str
    target_before_hash: str
    target_after_hash: str
    must_write: bool
    receipt_id: str
    audit_event_id: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "branch": self.branch,
            "action_plan_id": self.action_plan_id,
            "plan_hash": self.plan_hash,
            "approval_id": self.approval_id,
            "target_fixture_id": self.target_fixture_id,
            "status": self.status,
            "execution_class": self.execution_class,
            "target_before_hash": self.target_before_hash,
            "target_after_hash": self.target_after_hash,
            "must_write": self.must_write,
            "receipt_id": self.receipt_id,
            "audit_event_id": self.audit_event_id,
        }


@dataclass(frozen=True)
class ModelCallRecord:
    call_kind: str
    logical_call_index: int
    correlation_id: str
    model_caller: str
    model_origin: str
    requested_model: str
    observed_model: str
    preflight_model_id: str
    http_attempts: int
    retry_count: int


@dataclass(frozen=True)
class PersistedJourneyEvidence:
    """Everything `verify_journey` reads back, all of it read-only."""

    run_id: str
    journey_id: str
    agent_id: str
    agent_version_id: str
    session_id: str
    turn_id: str
    turn_status: str
    ontology_release_id: str
    mcp_descriptor_ids: tuple[str, ...]
    model_config_version_id: str
    citation_ids: tuple[str, ...]
    tool_trace_ids: tuple[str, ...]
    audit_event_ids: tuple[str, ...]
    sandbox_receipt_id: str
    automatic_receipt_id: str
    automatic_action: str
    model_calls: tuple[ModelCallRecord, ...]
    plan_branches: tuple[PlanBranchEvidence, ...]


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


def _require_loopback_api_base(api_base: str, *, reason: str) -> None:
    """`api_base` is caller-configurable (`BUSINESS_JOURNEY_API_BASE`, an
    "application-under-test base URL only" override the gate script accepts
    with no validation). Refuse to transmit a real credential to anything
    but the disposable local stack this gate is designed to talk to —
    otherwise a misconfigured or malicious override could exfiltrate the
    real `DEEPSEEK_API_KEY` (a live, billable, org-wide secret) to an
    arbitrary remote host."""
    host = urlsplit(api_base).hostname or ""
    if host not in _LOOPBACK_HOSTS:
        raise JourneyAcceptanceError(
            f"API_BASE_NOT_LOOPBACK: refusing to send a request to {api_base!r} — {reason}"
        )


class JourneyApiClient:
    """Talks HTTP to the application under test — and to nothing else.

    ``model_caller`` is the single production `DeepSeekVisionCaller`; when it
    is ``None`` (the whole verification phase) this client is structurally
    incapable of making a model call.
    """

    def __init__(
        self,
        api_base: str,
        api_key: str | None = None,
        *,
        model_caller: DeepSeekVisionCaller | None = None,
        run_id: str = "",
        timeout_seconds: float = 30.0,
    ) -> None:
        self._api_base = api_base.rstrip("/")
        self._api_key = api_key
        self._model_caller = model_caller
        self._run_id = run_id
        self._timeout_seconds = timeout_seconds
        self._token: str | None = None
        self._client = httpx.Client(timeout=timeout_seconds, follow_redirects=False)

    # -- lifecycle ---------------------------------------------------------
    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "JourneyApiClient":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # -- transport ---------------------------------------------------------
    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    def _call(self, method: str, path: str, *, headers: Mapping[str, str] | None = None, **kwargs: Any) -> httpx.Response:
        url = f"{self._api_base}{path}"
        merged_headers = {**self._headers(), **(headers or {})}
        try:
            return self._client.request(method, url, headers=merged_headers, **kwargs)
        except httpx.HTTPError as exc:
            raise JourneyAcceptanceError(f"APPLICATION_UNREACHABLE: {method} {path}") from exc

    def _get(self, path: str, *, reason_code: str, **kwargs: Any) -> Any:
        response = self._call("GET", path, **kwargs)
        if response.status_code != 200:
            raise JourneyAcceptanceError(f"{reason_code}: GET {path} -> {response.status_code}")
        return _json_body(response, reason_code)

    def _post(self, path: str, *, reason_code: str, expected: Sequence[int] = (200, 201), **kwargs: Any) -> Any:
        response = self._call("POST", path, **kwargs)
        if response.status_code not in expected:
            raise JourneyAcceptanceError(f"{reason_code}: POST {path} -> {response.status_code}")
        return _json_body(response, reason_code)

    # -- authentication ----------------------------------------------------
    def authenticate(self) -> str:
        """Exchange ``api_key`` for a bearer token.

        ``"user/password"`` is a login pair (what the acceptance runner is
        configured with); anything else is used verbatim as a bearer token.
        Neither form is ever written to an artifact.
        """
        if not self._api_key:
            return ""
        if "/" not in self._api_key:
            self._token = self._api_key
            return self._token
        username, _, password = self._api_key.partition("/")
        body = self._post(
            "/api/v1/auth/login", reason_code="APPLICATION_LOGIN_FAILED",
            json={"username": username, "password": password},
        )
        token = _dig(body, "data", "access_token") or _dig(body, "access_token")
        if not token:
            raise JourneyAcceptanceError("APPLICATION_LOGIN_FAILED: no access token in response")
        self._token = str(token)
        return self._token

    def current_user_id(self) -> str:
        body = self._get("/api/v1/auth/profile", reason_code="APPLICATION_PROFILE_MISSING")
        user_id = _dig(body, "data", "id") or _dig(body, "id")
        if not user_id:
            raise JourneyAcceptanceError("APPLICATION_PROFILE_MISSING: no user id in response")
        return str(user_id)

    # -- model configuration ----------------------------------------------
    def create_model_config(self, journey_id: str, deepseek_api_key: str) -> ModelConfigEvidence:
        """Seed the exact, immutable DeepSeek vision configuration/version.

        The provider key is transmitted to the application exactly once, in
        this request body; it is never returned, stored on this object, or
        written to any artifact.

        The created VERSION's ``options`` (an arbitrary, already-persisted
        JSON column — `model_config_versions.options`, round-tripped
        verbatim by `POST /models/{id}/versions`) carries a
        ``business_journey: {run_id, journey_id}`` tag. This is the ONLY
        association between an immutable model configuration and the
        journey/run that created it; `app.tasks.agent_turn.
        _resolve_business_journey` reads it straight back to decide
        whether a real browser turn on the bound Agent should route
        through the business-journey two-completion protocol — no separate
        request field, header, or browser-visible signal exists or is
        needed, since a turn's Agent is permanently pinned to this exact
        immutable config.
        """
        _require_loopback_api_base(
            self._api_base,
            reason="this request body carries the real DeepSeek credential",
        )
        business_journey_tag = {"run_id": self._run_id, "journey_id": journey_id}
        config = self._post(
            "/api/v1/models", reason_code="MODEL_CONFIG_MISSING",
            json={
                "name": f"business-journey-{journey_id}",
                "config_type": "llm",
                "provider": "deepseek",
                "api_base": OFFICIAL_ORIGIN,
                "api_key": deepseek_api_key,
                "models": [MODEL_ID],
                "options": {"temperature": 0, "seed": 0, "business_journey": business_journey_tag},
            },
        )
        config_id = _dig(config, "data", "id") or _dig(config, "id")
        if not config_id:
            raise JourneyAcceptanceError("MODEL_CONFIG_MISSING: no model configuration id")

        version = self._post(
            f"/api/v1/models/{config_id}/versions", reason_code="MODEL_CONFIG_VERSION_MISSING",
            json={
                "api_base": OFFICIAL_ORIGIN,
                "options": {"temperature": 0, "seed": 0, "business_journey": business_journey_tag},
                "model_contract": [{
                    "provider_model_revision": MODEL_ID,
                    "verified_at": None,
                }],
            },
        )
        payload = _dig(version, "data") or version
        version_id = payload.get("id")
        api_base = payload.get("api_base")
        contract = payload.get("model_contract") or []
        observed_model = contract[0].get("provider_model_revision") if contract else None
        if not version_id:
            raise JourneyAcceptanceError("MODEL_CONFIG_VERSION_MISSING: no version id")
        if api_base != OFFICIAL_ORIGIN:
            raise JourneyAcceptanceError(f"MODEL_ORIGIN_MISMATCH: {api_base!r}")
        if observed_model != MODEL_ID:
            raise JourneyAcceptanceError(f"MODEL_ID_MISMATCH: configuration pins {observed_model!r}")
        return ModelConfigEvidence(
            model_config_id=str(config_id),
            model_config_version_id=str(version_id),
            provider="deepseek",
            model_id=MODEL_ID,
            origin=OFFICIAL_ORIGIN,
            behavior_hash=str(payload.get("behavior_hash") or ""),
        )

    # -- pipeline ----------------------------------------------------------
    def start_pipeline(self, manifest: Any) -> PipelineEvidence:
        """Submit the journey's versioned TABULAR inputs to an isolated
        Pipeline and wait for durable (synchronous) completion.

        Only ``kind == "tabular"`` fixtures go through the Pipeline — it is
        the real DAG-based structured-data engine
        (``app.tasks.v2.pipeline_run.pipeline_run_task``), which reads
        ``Pipeline.definition.nodes`` of type ``connector`` (each carrying
        already-uploaded ``Dataset`` ids) or ``Pipeline.source_dataset_id``,
        never an arbitrary free-form ``spec`` dict. The document and image
        fixtures are consumed directly as raw multimodal input parts by the
        ontology completion (`create_or_complete_ontology`/
        `build_input_parts`) instead — exactly the split Task 1's own
        `journey_registry.validate_journey_manifest` already enforces (one+
        tabular input, one policy/report document, one rendered visual page).
        """
        tabular_inputs = [entry for entry in manifest.inputs if entry["kind"] == "tabular"]
        if not tabular_inputs:
            raise JourneyAcceptanceError("PIPELINE_TABULAR_INPUT_MISSING: manifest has no tabular input")

        files: list[dict[str, str]] = []
        for entry in tabular_inputs:
            path = REPO_ROOT / str(entry["path"])
            if not path.exists():
                raise JourneyAcceptanceError(f"FIXTURE_INPUT_MISSING: {entry['fixture_id']}")
            data = path.read_bytes()
            if sha256_bytes(data) != entry["sha256"]:
                raise JourneyAcceptanceError(f"FIXTURE_INPUT_DRIFTED: {entry['fixture_id']}")
            uploaded = self._post(
                "/api/v2/datasets/upload", reason_code="DATASET_UPLOAD_FAILED",
                files={"file": (path.name, data, str(entry["media_type"]))},
            )
            dataset = _dig(uploaded, "data") or uploaded
            dataset_id = dataset.get("id")
            if not dataset_id:
                raise JourneyAcceptanceError(f"DATASET_UPLOAD_FAILED: {entry['fixture_id']}")
            files.append({"dataset_id": str(dataset_id), "name": path.name})

        definition = {"nodes": [{"id": "journey-source", "type": "connector", "config": {"files": files}}]}
        created = self._post(
            "/api/v2/pipelines", reason_code="PIPELINE_MISSING",
            json={
                "name": f"business-journey-{manifest.journey_id}-{self._run_id}",
                "domain": manifest.journey_id,
                "definition": definition,
            },
        )
        pipeline_id = _dig(created, "id") or _dig(created, "data", "id")
        if not pipeline_id:
            raise JourneyAcceptanceError("PIPELINE_MISSING: no pipeline id")

        started = self._post(
            f"/api/v2/pipelines/{pipeline_id}/run-sync", reason_code="PIPELINE_RUN_MISSING",
        )
        run_id = started.get("run_id")
        if not run_id:
            raise JourneyAcceptanceError("PIPELINE_RUN_MISSING: no run id")
        status = str(started.get("status") or "")
        if status != "success":
            raise JourneyAcceptanceError(
                f"PIPELINE_RUN_NOT_SUCCESSFUL: status={status!r} error={started.get('error')!r}"
            )

        run = self._get(f"/api/v2/pipelines/runs/{run_id}", reason_code="PIPELINE_RUN_MISSING")
        dataset_version_id = run.get("dataset_version_id")
        if not dataset_version_id:
            raise JourneyAcceptanceError("DATASET_VERSION_MISSING: run has no dataset version")
        stats = run.get("stats") or {}
        # `pipeline_run_task` records the curated dataset(s) it produced on
        # the run's own `stats` before it marks the run successful, so a
        # successful run that names no curated dataset is a real failure of
        # this baseline, not something to paper over by picking the newest
        # curated dataset in the system.
        curated_dataset_id = stats.get("curated_dataset_id")
        if not curated_dataset_id:
            raise JourneyAcceptanceError(
                f"CURATED_DATASET_MISSING: run {run_id} recorded no curated output"
            )
        return PipelineEvidence(
            pipeline_id=str(pipeline_id),
            pipeline_run_id=str(run_id),
            dataset_version_id=str(dataset_version_id),
            status=str(run.get("status") or status),
            row_count=stats.get("rows_out"),
            input_fixture_ids=tuple(str(entry["fixture_id"]) for entry in manifest.inputs),
            input_hashes=dict(manifest.input_hashes),
            curated_dataset_id=str(curated_dataset_id),
        )

    # -- curated -----------------------------------------------------------
    def approve_curated(self, pipeline: PipelineEvidence) -> CuratedEvidence:
        """Start and approve a review for the curated dataset THIS journey's
        pipeline run produced.

        The dataset is identified by ``pipeline.curated_dataset_id``, which
        `start_pipeline` read from this run's own
        ``stats.curated_dataset_id`` — a real run-to-output binding written
        by `app.tasks.v2.pipeline_run.pipeline_run_task`. ``GET
        /api/v2/curated`` lists every curated dataset ever created, not just
        this run's, so taking ``items[0]`` (newest first, as this previously
        did) would approve some other journey's — or a stale failed run's —
        dataset whenever anything else produced one in between. The list is
        still read, but only to require that this run's own dataset really is
        a listed, reviewable curated dataset.
        """
        run_id = pipeline.pipeline_run_id
        datasets = self._get("/api/v2/curated", reason_code="CURATED_DATASET_MISSING")
        items = datasets if isinstance(datasets, list) else (_dig(datasets, "data") or [])
        if not items:
            raise JourneyAcceptanceError(f"CURATED_DATASET_MISSING: no curated dataset for run {run_id}")
        dataset_id = pipeline.curated_dataset_id
        if dataset_id not in {str(item.get("id")) for item in items}:
            raise JourneyAcceptanceError(
                f"CURATED_DATASET_MISSING: run {run_id}'s curated dataset is not listed"
            )
        review = self._post(
            f"/api/v2/curated/{dataset_id}/reviews", reason_code="CURATED_REVIEW_MISSING",
        )
        review_id = _dig(review, "review_id") or _dig(review, "data", "review_id")
        if not review_id:
            raise JourneyAcceptanceError("CURATED_REVIEW_MISSING: no review id")
        approved = self._post(
            f"/api/v2/curated/reviews/{review_id}/approve", reason_code="CURATED_APPROVAL_FAILED",
        )
        status = _dig(approved, "status") or _dig(approved, "data", "status")
        if status != "approved":
            raise JourneyAcceptanceError(f"CURATED_APPROVAL_FAILED: status={status!r}")
        return CuratedEvidence(
            curated_dataset_id=dataset_id, review_id=str(review_id), status=str(status),
        )

    # -- ontology ----------------------------------------------------------
    def create_or_complete_ontology(
        self,
        manifest: Any,
        model_version_id: str,
        *,
        validate_structured: Callable[[Mapping[str, Any]], None],
    ) -> OntologyReleaseEvidence:
        """Create the ontology, make the ONE ontology completion, write the
        model's extraction into the ontology, and require an explicitly
        published release.

        This is the only place in the whole preparation phase that calls a
        model, and it calls it exactly once (`logical_call_index=1`).

        ``validate_structured`` is the caller's semantic-minimum check
        (`orchestrator.prepare_journey`, which owns the journey manifest's
        expected minima). It runs BEFORE the extraction is written back, so
        an extraction that does not meet the journey's contract never
        becomes real ontology content — and the caller's own reason code
        (``SEMANTIC_MINIMUM_FAILED``) is what a CI log shows, not a
        downstream write failure.
        """
        if self._model_caller is None:
            raise JourneyAcceptanceError("MODEL_CALLER_MISSING: preparation requires a DeepSeekVisionCaller")

        created = self._post(
            "/api/v1/ontologies", reason_code="ONTOLOGY_MISSING",
            json={
                "name": f"business-journey-{manifest.journey_id}-{self._run_id}",
                "domain": _JOURNEY_ONTOLOGY_DOMAIN.get(manifest.journey_id, "其他"),
                "build_mode": "simple_llm",
            },
        )
        ontology_id = _dig(created, "data", "id") or _dig(created, "id")
        if not ontology_id:
            raise JourneyAcceptanceError("ONTOLOGY_MISSING: no ontology id")

        # The lifecycle state machine requires an explicit draft -> created
        # transition before `publish` will do anything but 404 — confirmed
        # directly against the real endpoint (`app.services.publication.
        # lifecycle.mark_created`/`publish`).
        self._post(
            f"/api/v1/ontologies/{ontology_id}/mark-created", reason_code="ONTOLOGY_MARK_CREATED_FAILED",
            json={}, headers={"Idempotency-Key": f"journey-{self._run_id}-{manifest.journey_id}-mark-created"},
        )

        context = JourneyModelContext(
            run_id=self._run_id,
            journey_id=manifest.journey_id,
            model_config_version_id=model_version_id,
            model_id=MODEL_ID,
            call_kind="ontology",
            logical_call_index=1,
            correlation_id=f"{self._run_id}:{manifest.journey_id}:ontology:1",
        )
        try:
            response = self._model_caller.complete(
                context, build_input_parts(manifest), response_schema=ONTOLOGY_RESPONSE_SCHEMA,
            )
        except JourneyAcceptanceError:
            raise
        except BusinessJourneyModelError as exc:
            raise JourneyAcceptanceError(f"{exc}") from exc

        validate_structured(response.structured)

        # Write the model's extraction into the ontology BEFORE publishing,
        # so the release the rest of the journey is grounded in has real
        # content traceable to this completion.
        content = self.persist_extraction(str(ontology_id), response.structured)

        published = self._post(
            f"/api/v1/ontologies/{ontology_id}/publish", reason_code="ONTOLOGY_RELEASE_MISSING",
            json={"changelog": f"business journey {manifest.journey_id} {self._run_id}"},
            headers={"Idempotency-Key": f"journey-{self._run_id}-{manifest.journey_id}"},
        )
        release = _dig(published, "data") or published
        release_id = release.get("release_id") or release.get("id")
        status = str(release.get("status") or "published")
        if not release_id:
            raise JourneyAcceptanceError("ONTOLOGY_RELEASE_MISSING: no release id")
        if status != "published":
            raise JourneyAcceptanceError(f"ONTOLOGY_RELEASE_NOT_PUBLISHED: status={status!r}")

        return OntologyReleaseEvidence(
            ontology_id=str(ontology_id),
            release_id=str(release_id),
            release_status=status,
            schema_hash=str(release.get("schema_hash") or ""),
            semantic_snapshot_placeholder=_semantic_snapshot_placeholder(str(release_id)),
            content=content,
            structured=dict(response.structured),
            model_response=response,
        )

    # -- ontology content --------------------------------------------------
    def persist_extraction(
        self, ontology_id: str, structured: Mapping[str, Any],
    ) -> OntologyContentEvidence:
        """Create real Entity/Relation/logic-rule/action-type rows from the
        model's own extraction, through the application's real write
        endpoints.

        Before this existed, the ontology completion's output was validated
        in memory (`semantic_validators.validate_semantic_minimum`, a
        self-consistency check on the model's own JSON) and then thrown
        away: `publish` compiled a release from an EMPTY ontology, so the
        release the Agent later resolved had no relationship at all to what
        the model extracted.

        Each name is mapped to the minimum valid creation payload the real
        endpoint accepts (`app.schemas.entity.EntityCreate`,
        `app.routers.graph.create_relation`'s body,
        `app.routers.v2.logic_actions.LogicRuleCreate`/`ActionTypeCreate`);
        fields the flat extraction cannot supply take the generic
        `_EXTRACTED_*` defaults above. Duplicate names are collapsed because
        the publication compiler rejects a release with colliding entity
        display labels (`LABEL_COLLISION`).
        """
        entity_ids: dict[str, str] = {}
        for name in _unique_names(structured.get("entities")):
            body = self._post(
                f"/api/v1/ontologies/{ontology_id}/entities", reason_code="ONTOLOGY_ENTITY_WRITE_FAILED",
                json={
                    "name_cn": name,
                    "name_en": name,
                    "type": _EXTRACTED_ENTITY_TYPE,
                    "description": f"extracted by the ontology completion for {ontology_id}",
                },
            )
            entity_id = _dig(body, "data", "id") or _dig(body, "id")
            if not entity_id:
                raise JourneyAcceptanceError(f"ONTOLOGY_ENTITY_WRITE_FAILED: {name!r} returned no id")
            entity_ids[name.strip().casefold()] = str(entity_id)
        if not entity_ids:
            raise JourneyAcceptanceError("ONTOLOGY_ENTITY_WRITE_FAILED: extraction named no entities")

        relation_ids: list[str] = []
        created_relation_names: set[str] = set()
        for edge in structured.get("relation_edges") or ():
            if not isinstance(edge, Mapping):
                raise JourneyAcceptanceError(f"ONTOLOGY_RELATION_WRITE_FAILED: malformed edge {edge!r}")
            source = entity_ids.get(str(edge.get("source", "")).strip().casefold())
            target = entity_ids.get(str(edge.get("target", "")).strip().casefold())
            if not source or not target:
                # The publication compiler would reject the whole release
                # with UNRESOLVED_RELATION_ENDPOINT anyway; failing here
                # names the actual cause instead.
                raise JourneyAcceptanceError(
                    f"ONTOLOGY_RELATION_WRITE_FAILED: edge {edge.get('name')!r} names an "
                    "endpoint the extraction never listed as an entity"
                )
            edge_name = str(edge.get("name") or _EXTRACTED_RELATION_TYPE_FALLBACK)
            body = self._post(
                f"/api/v1/ontologies/{ontology_id}/graph/relations",
                reason_code="ONTOLOGY_RELATION_WRITE_FAILED",
                json={
                    "source_entity": source,
                    "target_entity": target,
                    "type": edge_name,
                },
            )
            relation_id = _dig(body, "data", "id") or _dig(body, "id")
            if not relation_id:
                raise JourneyAcceptanceError("ONTOLOGY_RELATION_WRITE_FAILED: returned no id")
            relation_ids.append(str(relation_id))
            created_relation_names.add(edge_name.strip().casefold())

        # `relations` (the flat name array the semantic-minimum check reads)
        # and `relation_edges` (what actually gets written) are two separate
        # fields on the SAME response — a live model can satisfy the
        # semantic minimum by naming every required relation while leaving
        # `relation_edges` empty or incomplete, and nothing above catches
        # that: the loop above simply does nothing when there are no edges,
        # silently producing a relation-free ontology. Mirror the entity
        # guard: every relation name the extraction claims must actually
        # have been connected into a real edge.
        required_relation_names = {
            n.strip().casefold() for n in _unique_names(structured.get("relations"))
        }
        missing_relations = required_relation_names - created_relation_names
        if missing_relations:
            raise JourneyAcceptanceError(
                f"ONTOLOGY_RELATION_WRITE_FAILED: extraction named relations "
                f"{sorted(missing_relations)!r} but never connected them via relation_edges"
            )

        rule_ids: list[str] = []
        for name in _unique_names(structured.get("rules")):
            body = self._post(
                f"/api/v2/ontologies/{ontology_id}/logic", reason_code="ONTOLOGY_RULE_WRITE_FAILED",
                json={
                    "name": name,
                    "logic_type": _EXTRACTED_LOGIC_TYPE,
                    "description": f"extracted by the ontology completion for {ontology_id}",
                    "expression": {},
                    "enabled": True,
                },
            )
            rule_id = _dig(body, "id") or _dig(body, "data", "id")
            if not rule_id:
                raise JourneyAcceptanceError(f"ONTOLOGY_RULE_WRITE_FAILED: {name!r} returned no id")
            rule_ids.append(str(rule_id))

        action_ids: list[str] = []
        for name in _unique_names(structured.get("actions")):
            body = self._post(
                f"/api/v2/ontologies/{ontology_id}/actions", reason_code="ONTOLOGY_ACTION_WRITE_FAILED",
                json={
                    "name": name,
                    "action_category": _EXTRACTED_ACTION_CATEGORY,
                    "description": f"extracted by the ontology completion for {ontology_id}",
                    "parameters": [],
                    "effects": [],
                    "enabled": True,
                },
            )
            action_id = _dig(body, "id") or _dig(body, "data", "id")
            if not action_id:
                raise JourneyAcceptanceError(f"ONTOLOGY_ACTION_WRITE_FAILED: {name!r} returned no id")
            action_ids.append(str(action_id))

        return OntologyContentEvidence(
            entity_ids=tuple(entity_ids.values()),
            relation_ids=tuple(relation_ids),
            rule_ids=tuple(rule_ids),
            action_ids=tuple(action_ids),
        )

    def preflight_model(self, manifest: Any, model_version_id: str) -> ModelProbe:
        """Persist one successful ``/models`` probe before anything else."""
        if self._model_caller is None:
            raise JourneyAcceptanceError("MODEL_CALLER_MISSING: preflight requires a DeepSeekVisionCaller")
        context = JourneyModelContext(
            run_id=self._run_id,
            journey_id=manifest.journey_id,
            model_config_version_id=model_version_id,
            model_id=MODEL_ID,
            call_kind="ontology",
            logical_call_index=1,
            correlation_id=f"{self._run_id}:{manifest.journey_id}:ontology:1",
        )
        return self._model_caller.preflight(context)

    # -- MCP + grant -------------------------------------------------------
    def publish_mcp_descriptors(self, release_id: str, *, ontology_id: str) -> tuple[McpDescriptor, ...]:
        body = self._get(
            f"/api/v1/ontologies/{ontology_id}/tools", reason_code="MCP_DESCRIPTOR_MISSING",
        )
        catalog = _dig(body, "data") or body
        if not catalog.get("published"):
            raise JourneyAcceptanceError("MCP_DESCRIPTOR_MISSING: catalog is not from a published release")
        if catalog.get("release_id") != release_id:
            raise JourneyAcceptanceError(
                f"MCP_DESCRIPTOR_STALE: catalog release {catalog.get('release_id')!r} != {release_id!r}"
            )
        descriptors = tuple(
            McpDescriptor(
                descriptor_id=str(tool["descriptor_id"]),
                capability=str(tool.get("capability") or ""),
                release_id=release_id,
            )
            for tool in catalog.get("tools") or []
        )
        if not descriptors:
            raise JourneyAcceptanceError("MCP_DESCRIPTOR_MISSING: release exposes no tool descriptors")
        return descriptors

    def grant_ontology_data(self, ontology_id: str, user_id: str, release_id: str) -> GrantEvidence:
        body = self._post(
            "/api/v1/ontology-data-grants", reason_code="ONTOLOGY_DATA_GRANT_MISSING",
            json={
                "ontology_id": ontology_id,
                "user_id": user_id,
                "capabilities": list(GRANT_CAPABILITIES),
            },
        )
        grant = _dig(body, "data") or body
        grant_id = grant.get("id")
        status = str(grant.get("status") or "")
        if not grant_id:
            raise JourneyAcceptanceError("ONTOLOGY_DATA_GRANT_MISSING: no grant id")
        if status != "active":
            raise JourneyAcceptanceError(f"ONTOLOGY_DATA_GRANT_NOT_ACTIVE: status={status!r}")
        return GrantEvidence(
            grant_id=str(grant_id),
            ontology_id=ontology_id,
            user_id=user_id,
            release_id=release_id,
            status=status,
            capabilities=tuple(str(c) for c in (grant.get("capabilities") or ())),
        )

    def prepare_browser_agent_binding(
        self,
        manifest: Any,
        release_id: str,
        descriptors: Sequence[McpDescriptor],
        model_version_id: str,
    ) -> AgentBindingOptions:
        """Return ONLY the three binding options.

        This deliberately creates nothing: no Agent, no Agent version, no
        binding row, no session, no turn. The browser is what creates the
        Agent, using these options.
        """
        if not release_id:
            raise JourneyAcceptanceError("ONTOLOGY_RELEASE_MISSING: binding needs a published release")
        if not descriptors:
            raise JourneyAcceptanceError("MCP_DESCRIPTOR_MISSING: binding needs granted descriptors")
        if not model_version_id:
            raise JourneyAcceptanceError("MODEL_CONFIG_VERSION_MISSING: binding needs a pinned version")
        return AgentBindingOptions(
            ontology_release_id=release_id,
            mcp_descriptor_ids=tuple(d.descriptor_id for d in descriptors),
            model_config_version_id=model_version_id,
        )

    # -- read-only verification -------------------------------------------
    def read_journey_evidence(self, run_id: str, *, browser_evidence: Mapping[str, Any]) -> PersistedJourneyEvidence:
        """Read back everything the browser claims to have persisted.

        Every request below is a GET. `browser_evidence` supplies only the
        `turn_id` to look up; every OTHER fact this returns — `agent_id`/
        `agent_version_id` (the `turn_started` event's own payload, emitted
        by the real runtime), `ontology_release_id` (the `resolve_snapshot`
        event), `model_config_version_id` (each `model_call` event) and
        `mcp_descriptor_ids` (the `tool_executed` events' own
        `descriptor_id`s, which `LangGraphRuntime._execute_journey_tool_call`
        resolves from the ontology's OWN published tool catalog — the same
        catalog `publish_mcp_descriptors` reads — rather than echoing the
        model's free-text request; `orchestrator._require_granted_mcp_
        descriptors` then cross-checks them against the descriptors
        `prepare_journey` actually granted) — is read from the turn's own
        persisted event trace, never trusted from the local
        browser-evidence file. Whatever
        the browser file ALSO claims for these fields is cross-checked
        against the real trace and rejected on any mismatch, rather than
        silently read from the file (previously: `agent_id`/
        `agent_version_id`/`ontology_release_id`/`mcp_descriptor_ids`/
        `model_config_version_id` were read straight from the file, which
        this method's own docstring already claimed not to do)."""
        journey_id = str(browser_evidence["journey_id"])
        turn_id = str(browser_evidence["turn_id"])

        turn = _dig(
            self._get(f"/api/v1/agent-turns/{turn_id}", reason_code="TURN_NOT_PERSISTED"), "data",
        ) or {}
        turn_status = str(turn.get("status") or "")
        if turn_status != "succeeded":
            raise JourneyAcceptanceError(f"TURN_NOT_SUCCEEDED: status={turn_status!r}")

        events_body = self._get(
            f"/api/v1/agent-turns/{turn_id}/events", reason_code="TURN_TRACE_MISSING",
        )
        events = (_dig(events_body, "data", "items") or _dig(events_body, "items") or [])

        agent_id = ""
        agent_version_id = ""
        ontology_release_id = ""
        mcp_descriptor_ids: list[str] = []
        citation_ids: list[str] = []
        tool_trace_ids: list[str] = []
        audit_event_ids: list[str] = []
        sandbox_receipt_id = ""
        automatic_receipt_id = ""
        automatic_action = ""
        model_config_version_id = ""
        model_calls: list[ModelCallRecord] = []
        for event in events:
            payload = event.get("payload") or {}
            kind = event.get("event_type")
            if kind == "turn_started":
                agent_id = str(payload.get("agent_id") or "")
                agent_version_id = str(payload.get("agent_version_id") or "")
            elif kind == "resolve_snapshot":
                ontology_release_id = str(payload.get("release_id") or "")
                citation_ids.extend(str(c) for c in (payload.get("citations") or []))
            elif kind == "tool_executed":
                tool_trace_ids.append(str(payload.get("correlation_id") or ""))
                descriptor_id = str(payload.get("descriptor_id") or "")
                if descriptor_id and descriptor_id not in mcp_descriptor_ids:
                    mcp_descriptor_ids.append(descriptor_id)
            elif kind == "model_call" and payload.get("call_kind"):
                if payload.get("model_config_version_id"):
                    model_config_version_id = str(payload["model_config_version_id"])
                model_calls.append(ModelCallRecord(
                    call_kind=str(payload["call_kind"]),
                    logical_call_index=int(payload.get("logical_call_index") or 0),
                    correlation_id=str(payload.get("correlation_id") or ""),
                    model_caller=str(payload.get("model_caller") or ""),
                    model_origin=str(payload.get("model_origin") or ""),
                    requested_model=str(payload.get("requested_model") or ""),
                    observed_model=str(payload.get("observed_model") or ""),
                    preflight_model_id=str(payload.get("preflight_model_id") or ""),
                    http_attempts=int(payload.get("http_attempts") or 0),
                    retry_count=int(payload.get("retry_count") or 0),
                ))
            elif kind == "final_response":
                audit_event_ids.append(str(payload.get("audit_event_id") or ""))
                sandbox_receipt_id = str(payload.get("sandbox_receipt_id") or "")
                automatic_receipt_id = str(payload.get("receipt_id") or "")
                automatic_action = str(payload.get("automatic_action") or "")

        if not agent_id or not agent_version_id:
            raise JourneyAcceptanceError("TURN_STARTED_EVENT_MISSING: no persisted agent_id/agent_version_id")
        if not ontology_release_id:
            raise JourneyAcceptanceError("RESOLVE_SNAPSHOT_EVENT_MISSING: no persisted ontology_release_id")
        if not model_config_version_id:
            raise JourneyAcceptanceError("MODEL_CALL_EVENT_MISSING: no persisted model_config_version_id")
        _require_matches_persisted(browser_evidence, "agent_id", agent_id)
        _require_matches_persisted(browser_evidence, "agent_version_id", agent_version_id)
        _require_matches_persisted(browser_evidence, "ontology_release_id", ontology_release_id)
        _require_matches_persisted(browser_evidence, "model_config_version_id", model_config_version_id)

        branches: list[PlanBranchEvidence] = []
        for declared in browser_evidence.get("plan_branches") or []:
            branches.append(self._read_plan_branch(declared))

        return PersistedJourneyEvidence(
            run_id=run_id,
            journey_id=journey_id,
            agent_id=agent_id,
            agent_version_id=agent_version_id,
            session_id=str(turn.get("session_id") or ""),
            turn_id=turn_id,
            turn_status=turn_status,
            ontology_release_id=ontology_release_id,
            mcp_descriptor_ids=tuple(mcp_descriptor_ids),
            model_config_version_id=model_config_version_id,
            citation_ids=tuple(citation_ids),
            tool_trace_ids=tuple(t for t in tool_trace_ids if t),
            audit_event_ids=tuple(a for a in audit_event_ids if a),
            sandbox_receipt_id=sandbox_receipt_id,
            automatic_receipt_id=automatic_receipt_id,
            automatic_action=automatic_action,
            model_calls=tuple(model_calls),
            plan_branches=tuple(branches),
        )

    def _read_plan_branch(self, declared: Mapping[str, Any]) -> PlanBranchEvidence:
        """Read back one governed-turn-plan branch through the single
        consolidated projection `GET /api/v2/runtime/action-plans/from-turn/
        {plan_id}` (`app.services.runtime.turn_plans.get_governed_plan`) —
        the real endpoint `create_governed_plan_from_turn` creates rows for,
        not the unrelated investigation-based `RuntimePlan`/`/action-plans/
        {id}` resource."""
        branch = str(declared.get("branch") or "")
        plan_id = str(declared.get("action_plan_id") or "")
        view = self._get(
            f"/api/v2/runtime/action-plans/from-turn/{plan_id}", reason_code="PLAN_NOT_PERSISTED",
        )
        if view.get("plan_hash") != declared.get("plan_hash"):
            raise JourneyAcceptanceError(f"PLAN_HASH_MISMATCH: branch={branch}")
        if view.get("branch") != branch:
            raise JourneyAcceptanceError(f"PLAN_BRANCH_MISMATCH: expected={branch}")
        status = str(view.get("status") or "")
        if status != branch:
            raise JourneyAcceptanceError(
                f"APPROVAL_STATUS_MISMATCH: branch={branch} status={status!r}"
            )

        return PlanBranchEvidence(
            branch=branch,
            action_plan_id=plan_id,
            plan_hash=str(view.get("plan_hash") or ""),
            approval_id=str(view.get("approval_id") or ""),
            target_fixture_id=str(view.get("target_fixture_id") or ""),
            status=status,
            execution_class=str(view.get("execution_class") or ""),
            target_before_hash=str(view.get("target_before_hash") or ""),
            target_after_hash=str(view.get("target_after_hash") or ""),
            must_write=bool(view.get("must_write")),
            receipt_id=str(view.get("receipt_id") or ""),
            audit_event_id=str(view.get("audit_event_id") or ""),
        )


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------


def build_input_parts(manifest: Any) -> tuple[InputPart, ...]:
    """Turn the journey's versioned inputs into model input parts.

    Reads the real, checked-in fixture files by their manifest-recorded
    repository-relative path and verifies each file's sha256 against the
    manifest before it is ever sent, so a drifted fixture fails closed
    instead of silently changing the model's inputs.
    """
    parts: list[InputPart] = []
    scenario = manifest.dialogue_scenarios[0] if manifest.dialogue_scenarios else {}
    question = str(scenario.get("question") or "")
    if question:
        parts.append(InputPart(
            kind="text", media_type="text/plain", content=question,
            sha256=sha256_bytes(question.encode("utf-8")),
        ))
    for entry in manifest.inputs:
        path = REPO_ROOT / str(entry["path"])
        if not path.exists():
            raise JourneyAcceptanceError(f"FIXTURE_INPUT_MISSING: {entry['fixture_id']}")
        data = path.read_bytes()
        digest = sha256_bytes(data)
        if digest != entry["sha256"]:
            raise JourneyAcceptanceError(f"FIXTURE_INPUT_DRIFTED: {entry['fixture_id']}")
        if entry["kind"] == "image":
            parts.append(InputPart(
                kind="image", media_type=str(entry["media_type"]), content=data, sha256=digest,
            ))
        else:
            parts.append(InputPart(
                kind="text", media_type=str(entry["media_type"]),
                content=_as_text(data, str(entry["media_type"])), sha256=digest,
            ))
    return tuple(parts)


def _unique_names(value: Any) -> tuple[str, ...]:
    """Non-empty names from a flat extraction array, first occurrence wins.

    Case-insensitive de-duplication: the publication compiler rejects a
    release whose entities share a display label (`LABEL_COLLISION`).
    """
    names: list[str] = []
    seen: set[str] = set()
    for item in value or ():
        name = str(item).strip()
        key = name.casefold()
        if not name or key in seen:
            continue
        seen.add(key)
        names.append(name)
    return tuple(names)


def _semantic_snapshot_placeholder(release_id: str) -> str:
    """A deliberately, visibly synthetic stand-in for a snapshot id.

    There is NO production endpoint that creates a `SemanticSnapshot`:
    `app.services.runtime.snapshots.materialize_snapshot` is the only
    constructor of that row anywhere, and neither it nor
    `materialize_refresh_snapshot` has a single caller outside
    `tests/runtime/` — no router, no service, no task. So the preparation
    phase cannot obtain a real snapshot id today, and this value must never
    be read as one. The `PLACEHOLDER` prefix keeps that obvious in the
    staging run manifest and in any artifact that echoes it; nothing
    downstream (`verify_journey`, the Playwright evidence, the artifact
    allowlist) consumes it.
    """
    return f"PLACEHOLDER-NO-SNAPSHOT-ENDPOINT:{release_id}"


def _as_text(data: bytes, media_type: str) -> str:
    """Text-decodable inputs go as text; anything binary goes as base64 so a
    decode error can never silently truncate an input."""
    if media_type.startswith("text/"):
        return data.decode("utf-8", errors="replace")
    return base64.b64encode(data).decode("ascii")


def _json_body(response: httpx.Response, reason_code: str) -> Any:
    try:
        return response.json()
    except ValueError as exc:
        raise JourneyAcceptanceError(f"{reason_code}: response body was not JSON") from exc


def _dig(payload: Any, *keys: str) -> Any:
    node = payload
    for key in keys:
        if not isinstance(node, Mapping) or key not in node:
            return None
        node = node[key]
    return node


def _require_matches_persisted(browser_evidence: Mapping[str, Any], field: str, persisted_value: str) -> None:
    """Cross-check what the browser evidence file CLAIMS for `field` against
    the value just read from the application's own persisted event trace —
    the browser's claim is only ever a lookup hint, never trusted on its own
    (Important Finding #10). A field the browser evidence document doesn't
    carry at all is not itself an error; only a genuine mismatch is."""
    claimed = browser_evidence.get(field)
    if claimed is not None and str(claimed) != persisted_value:
        raise JourneyAcceptanceError(
            f"{field.upper()}_MISMATCH: browser claimed {claimed!r}, application persisted {persisted_value!r}"
        )


def new_idempotency_key(run_id: str, journey_id: str, branch: str) -> str:
    """A distinct, deterministic idempotency key per run/journey/branch."""
    return f"journey-{run_id}-{journey_id}-{branch}-{uuid.uuid5(uuid.NAMESPACE_URL, f'{run_id}:{journey_id}:{branch}')}"


def canonical_digest(payload: Any) -> str:
    """The canonical payload digest the governed-plan endpoint compares."""
    return sha256_bytes(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )


__all__ = [
    "AgentBindingOptions",
    "CuratedEvidence",
    "GrantEvidence",
    "JourneyAcceptanceError",
    "JourneyApiClient",
    "McpDescriptor",
    "ModelCallRecord",
    "ModelConfigEvidence",
    "ONTOLOGY_RESPONSE_SCHEMA",
    "OntologyContentEvidence",
    "OntologyReleaseEvidence",
    "PersistedJourneyEvidence",
    "PipelineEvidence",
    "PlanBranchEvidence",
    "build_input_parts",
    "canonical_digest",
    "new_idempotency_key",
]

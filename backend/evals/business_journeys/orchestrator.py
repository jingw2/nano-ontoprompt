"""Preparation and verification of the three governed business journeys.

Two strictly separated phases:

* ``prepare_journey`` builds ONLY the pre-browser baseline. It consumes
  exactly one logical model call (the ontology completion), and it never
  creates an Agent, Agent version/binding, session, turn, tool call, Sandbox
  result, approval, or plan.
* ``verify_journey`` reads ONLY persisted post-browser evidence. It consumes
  zero model calls and makes zero *governed* mutations: it holds no
  ``DeepSeekVisionCaller`` at all, and its client only ever issues GETs
  against governed application state — the one exception is the login call
  every real endpoint's authentication requires, never a plan/approval/
  grant/ontology write.

The staging run manifest written between the two phases is allowlisted: it
carries baseline identifiers, hashes, and counters, and is rejected outright
if it would contain an Agent id, a raw prompt, an input row, or a credential.
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from app.services.model_callers.deepseek_vision import DeepSeekVisionCaller

from .api_client import (
    AgentBindingOptions,
    CuratedEvidence,
    GrantEvidence,
    JourneyAcceptanceError,
    JourneyApiClient,
    McpDescriptor,
    ModelConfigEvidence,
    PersistedJourneyEvidence,
    PipelineEvidence,
    PlanBranchEvidence,
)
from .contracts import (
    MAX_HTTP_ATTEMPTS,
    MAX_LOGICAL_MODEL_CALLS,
    MODEL_ID,
    OFFICIAL_ORIGIN,
    BusinessJourneyModelError,
    JourneySemanticMinimum,
    ModelCallLedger,
)
from .semantic_validators import validate_semantic_minimum

REPO_ROOT = Path(__file__).resolve().parents[3]
_RUNTIME_DATA_DIR = REPO_ROOT / "test_data" / "runtime"
if str(_RUNTIME_DATA_DIR) not in sys.path:
    sys.path.insert(0, str(_RUNTIME_DATA_DIR))

from journey_registry import JOURNEY_IDS, load_journey_manifest  # noqa: E402

STAGING_RELATIVE_PATH = Path("business_journeys") / "staging" / "run.json"
BROWSER_EVIDENCE_DIR = Path("business_journeys") / "browser"
RUN_MANIFEST_ENV_VAR = "BUSINESS_JOURNEY_RUN_MANIFEST"
# The application credential BOTH phases authenticate with — prepare_journey
# takes it as an explicit `api_key` parameter (its own interface requires
# one, since it writes through authenticated endpoints); verify_journey's
# interface takes no credential parameter at all, so it reads the SAME
# environment variable directly (Critical Finding #4 — every endpoint
# `read_journey_evidence` calls requires `Depends(get_current_user)`; an
# unauthenticated client gets 401'd on the first request, which the fake
# HTTP server in `test_orchestrator.py` never modeled). `run.py` never
# exposes this as a CLI flag either, so the credential is never visible in
# argv/process listings for either phase.
APPLICATION_API_KEY_ENV_VAR = "BUSINESS_JOURNEY_API_KEY"

REQUIRED_CALL_KINDS = ("ontology", "agent_initial", "agent_final")
REQUIRED_BRANCHES = ("approved", "rejected", "expired")

# Fields the staging run manifest may never contain, in any nesting level.
FORBIDDEN_MANIFEST_KEYS = frozenset({
    "agent_id", "agent_version_id", "session_id", "turn_id", "prompt", "prompts",
    "messages", "input_rows", "rows", "api_key", "authorization", "token", "secret",
})


@dataclass(frozen=True)
class JourneyPreparationBudget:
    """The preparation phase's whole model budget."""

    logical_model_calls: int = 1
    max_http_attempts: int = 2


@dataclass(frozen=True)
class JourneyPreparation:
    run_id: str
    journey_id: str
    fixture_version: str
    fixture_manifest_sha256: str
    input_fixture_ids: tuple[str, ...]
    input_hashes: Mapping[str, str]
    pipeline: PipelineEvidence
    curated: CuratedEvidence
    ontology_id: str
    ontology_release_id: str
    release_status: str
    semantic_snapshot_id: str
    mcp_descriptors: tuple[McpDescriptor, ...]
    grant: GrantEvidence
    model_config: ModelConfigEvidence
    agent_binding_options: AgentBindingOptions
    model_caller: str
    model_origin: str
    preflight_model_id: str
    requested_model_id: str
    observed_model_id: str
    call_kinds: list[str]
    correlation_id: str
    logical_model_calls: int
    http_attempts: int
    retry_count: int
    semantic_reason_codes: tuple[str, ...]
    status: str

    def to_dict(self) -> dict[str, Any]:
        """The allowlisted staging projection — identifiers, hashes, and
        counters only."""
        return {
            "run_id": self.run_id,
            "journey_id": self.journey_id,
            "fixture_version": self.fixture_version,
            "fixture_manifest_sha256": self.fixture_manifest_sha256,
            "input_fixture_ids": list(self.input_fixture_ids),
            "input_hashes": dict(self.input_hashes),
            "pipeline_id": self.pipeline.pipeline_id,
            "pipeline_run_id": self.pipeline.pipeline_run_id,
            "dataset_version_id": self.pipeline.dataset_version_id,
            "pipeline_status": self.pipeline.status,
            "curated_dataset_id": self.curated.curated_dataset_id,
            "curated_review_id": self.curated.review_id,
            "curated_status": self.curated.status,
            "ontology_id": self.ontology_id,
            "ontology_release_id": self.ontology_release_id,
            "release_status": self.release_status,
            "semantic_snapshot_id": self.semantic_snapshot_id,
            "mcp_descriptor_ids": [d.descriptor_id for d in self.mcp_descriptors],
            "grant_id": self.grant.grant_id,
            "grant_status": self.grant.status,
            "model_config_id": self.model_config.model_config_id,
            "model_config_version_id": self.model_config.model_config_version_id,
            "model_caller": self.model_caller,
            "model_origin": self.model_origin,
            "preflight_model_id": self.preflight_model_id,
            "requested_model_id": self.requested_model_id,
            "observed_model_id": self.observed_model_id,
            "call_kinds": list(self.call_kinds),
            "correlation_id": self.correlation_id,
            "logical_model_calls": self.logical_model_calls,
            "http_attempts": self.http_attempts,
            "retry_count": self.retry_count,
            "agent_binding_options": {
                "ontology_release_id": self.agent_binding_options.ontology_release_id,
                "mcp_descriptor_ids": list(self.agent_binding_options.mcp_descriptor_ids),
                "model_config_version_id": self.agent_binding_options.model_config_version_id,
            },
            "status": self.status,
        }


@dataclass(frozen=True)
class JourneyVerification:
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
    plan_branches: tuple[PlanBranchEvidence, ...]
    model_caller: str
    model_origin: str
    preflight_model_id: str
    requested_model_id: str
    observed_model_ids: tuple[str, ...]
    call_kinds: list[str]
    correlation_ids: tuple[str, ...]
    logical_model_calls: int
    http_attempts: int
    retry_count: int
    tool_rounds: int
    status: str

    @property
    def plan_branches_by_name(self) -> dict[str, PlanBranchEvidence]:
        return {branch.branch: branch for branch in self.plan_branches}

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "journey_id": self.journey_id,
            "agent_id": self.agent_id,
            "agent_version_id": self.agent_version_id,
            "session_id": self.session_id,
            "turn_id": self.turn_id,
            "turn_status": self.turn_status,
            "ontology_release_id": self.ontology_release_id,
            "mcp_descriptor_ids": list(self.mcp_descriptor_ids),
            "model_config_version_id": self.model_config_version_id,
            "citation_ids": list(self.citation_ids),
            "tool_trace_ids": list(self.tool_trace_ids),
            "audit_event_ids": list(self.audit_event_ids),
            "sandbox_receipt_id": self.sandbox_receipt_id,
            "automatic_receipt_id": self.automatic_receipt_id,
            "plan_branches": [branch.to_dict() for branch in self.plan_branches],
            "model_caller": self.model_caller,
            "model_origin": self.model_origin,
            "preflight_model_id": self.preflight_model_id,
            "requested_model_id": self.requested_model_id,
            "observed_model_ids": list(self.observed_model_ids),
            "call_kinds": list(self.call_kinds),
            "correlation_ids": list(self.correlation_ids),
            "logical_model_calls": self.logical_model_calls,
            "http_attempts": self.http_attempts,
            "retry_count": self.retry_count,
            "tool_rounds": self.tool_rounds,
            "status": self.status,
        }


# ---------------------------------------------------------------------------
# Preparation.
# ---------------------------------------------------------------------------


def _normalize_numeric_predicates(raw: Mapping[str, Any]) -> dict[str, Mapping[str, object]]:
    """Task 1's real fixture corpus records ``numeric_predicates`` as flat
    ``{name: number}`` facts (verified directly against every journey's
    checked-in ``semantic_minima.json``), while
    ``semantic_validators.validate_semantic_minimum`` (Task 2) expects the
    richer ``{name: {"path", "op", "value"}}`` shape so it can compare a
    dotted-path response field with an operator. Normalizing a bare scalar
    into an equality check (``path=name``, ``op="eq"``, ``value=scalar``)
    here — rather than changing Task 2's already-shipped, already-tested
    ``semantic_validators.py`` contract — means the real ontology response
    must echo the exact named fact back as a same-named top-level field."""
    normalized: dict[str, Mapping[str, object]] = {}
    for name, value in raw.items():
        if isinstance(value, Mapping):
            normalized[name] = value
        else:
            normalized[name] = {"path": name, "op": "eq", "value": value}
    return normalized


def _semantic_minimum(manifest: Any) -> JourneySemanticMinimum:
    minima = manifest.semantic_minima
    return JourneySemanticMinimum(
        entities=tuple(minima.get("entities") or ()),
        relations=tuple(minima.get("relations") or ()),
        rules=tuple(minima.get("rules") or ()),
        actions=tuple(minima.get("actions") or ()),
        keywords=tuple(minima.get("keywords") or ()),
        source_citation_ids=tuple(minima.get("source_citation_ids") or ()),
        numeric_predicates=_normalize_numeric_predicates(minima.get("numeric_predicates") or {}),
        low_risk_action=str(minima.get("low_risk_action") or ""),
        high_risk_action=str(minima.get("high_risk_action") or ""),
    )


def prepare_journey(
    journey_id: str,
    *,
    api_base: str,
    api_key: str,
    output_dir: Path,
    run_id: str,
    model_id: str = MODEL_ID,
) -> JourneyPreparation:
    """Build one journey's pre-browser baseline.

    ``api_base`` is the application-under-test URL only — it never configures
    the DeepSeek client, which always talks to ``OFFICIAL_ORIGIN``.
    """
    if journey_id not in JOURNEY_IDS:
        raise JourneyAcceptanceError(f"JOURNEY_UNKNOWN: {journey_id!r}")
    if model_id != MODEL_ID:
        raise JourneyAcceptanceError(f"MODEL_ID_NOT_ALLOWED: {model_id!r}")

    deepseek_api_key = os.environ.get("DEEPSEEK_API_KEY") or ""
    if not deepseek_api_key:
        raise JourneyAcceptanceError("DEEPSEEK_API_KEY_REQUIRED: environment variable is not set")

    manifest = load_journey_manifest(journey_id, _RUNTIME_DATA_DIR)

    client = JourneyApiClient(api_base, api_key, run_id=run_id)
    try:
        client.authenticate()
        user_id = client.current_user_id()

        # 1. Immutable model configuration/version (the key goes only here).
        model_config = client.create_model_config(journey_id, deepseek_api_key)

        # 2. The one production caller, over a fresh per-journey ledger.
        ledger = ModelCallLedger()
        caller = DeepSeekVisionCaller(deepseek_api_key, model_config.as_immutable(), ledger)
        client_with_model = JourneyApiClient(
            api_base, api_key, model_caller=caller, run_id=run_id,
        )
        client_with_model._token = client._token  # reuse the verified session
        try:
            probe = _preflight(client_with_model, manifest, model_config)

            # 3. Pipeline + Curated approval (no model involvement).
            pipeline = client_with_model.start_pipeline(manifest)
            curated = client_with_model.approve_curated(pipeline.pipeline_run_id)

            # 4. The single ontology completion + explicit publish.
            release = client_with_model.create_or_complete_ontology(
                manifest, model_config.model_config_version_id,
            )
            validation = validate_semantic_minimum(release.structured, _semantic_minimum(manifest))
            if not validation.passed:
                raise JourneyAcceptanceError(
                    f"SEMANTIC_MINIMUM_FAILED: {','.join(validation.reason_codes)}"
                )

            # 5. Descriptors from that release, then an active data grant.
            descriptors = client_with_model.publish_mcp_descriptors(
                release.release_id, ontology_id=release.ontology_id,
            )
            grant = client_with_model.grant_ontology_data(
                release.ontology_id, user_id, release.release_id,
            )
            binding_options = client_with_model.prepare_browser_agent_binding(
                manifest, release.release_id, descriptors, model_config.model_config_version_id,
            )
        finally:
            client_with_model.close()
    finally:
        client.close()

    totals = ledger.totals(run_id, journey_id)
    budget = JourneyPreparationBudget()
    if totals["logical_model_calls"] != budget.logical_model_calls:
        raise JourneyAcceptanceError(
            f"LOGICAL_CALL_BUDGET_VIOLATED: {totals['logical_model_calls']} != {budget.logical_model_calls}"
        )
    if totals["http_attempts"] > budget.max_http_attempts:
        raise JourneyAcceptanceError(
            f"HTTP_ATTEMPT_BUDGET_VIOLATED: {totals['http_attempts']} > {budget.max_http_attempts}"
        )

    return JourneyPreparation(
        run_id=run_id,
        journey_id=journey_id,
        fixture_version=manifest.fixture_version,
        fixture_manifest_sha256=str(manifest.reproducibility["manifest_sha256"]),
        input_fixture_ids=pipeline.input_fixture_ids,
        input_hashes=dict(manifest.input_hashes),
        pipeline=pipeline,
        curated=curated,
        ontology_id=release.ontology_id,
        ontology_release_id=release.release_id,
        release_status=release.release_status,
        semantic_snapshot_id=release.semantic_snapshot_id,
        mcp_descriptors=descriptors,
        grant=grant,
        model_config=model_config,
        agent_binding_options=binding_options,
        model_caller="DeepSeekVisionCaller",
        model_origin=OFFICIAL_ORIGIN,
        preflight_model_id=probe.observed_model,
        requested_model_id=MODEL_ID,
        observed_model_id=release.model_response.model,
        call_kinds=["ontology"],
        correlation_id=f"{run_id}:{journey_id}:ontology:1",
        logical_model_calls=totals["logical_model_calls"],
        http_attempts=totals["http_attempts"],
        retry_count=release.model_response.retry_count,
        semantic_reason_codes=validation.reason_codes,
        status="passed",
    )


def _preflight(client: JourneyApiClient, manifest: Any, model_config: ModelConfigEvidence):
    try:
        return client.preflight_model(manifest, model_config.model_config_version_id)
    except JourneyAcceptanceError:
        raise
    except BusinessJourneyModelError as exc:
        raise JourneyAcceptanceError(f"MODEL_PREFLIGHT_FAILED: {exc}") from exc


def prepare_all_journeys(
    *,
    api_base: str,
    api_key: str,
    output_dir: Path,
    run_id: str,
    model_id: str = MODEL_ID,
    before_journey: Callable[[str], None] | None = None,
) -> list[JourneyPreparation]:
    """Prepare all three journeys in the fixed order and write the manifest.

    ``before_journey`` is a test-only hook so a unit test can install the
    per-journey stand-in model response before that journey runs; the real
    runner never supplies it.
    """
    preparations: list[JourneyPreparation] = []
    for journey_id in JOURNEY_IDS:
        if before_journey is not None:
            before_journey(journey_id)
        preparation = prepare_journey(
            journey_id, api_base=api_base, api_key=api_key, output_dir=output_dir,
            run_id=run_id, model_id=model_id,
        )
        if preparation.status != "passed":
            raise JourneyAcceptanceError(f"BASELINE_NOT_PASSING: {journey_id}")
        preparations.append(preparation)
    if len(preparations) != len(JOURNEY_IDS):
        raise JourneyAcceptanceError("BASELINE_MISSING: expected three prepared journeys")
    write_run_manifest(preparations, output_dir, run_id=run_id)
    return preparations


# ---------------------------------------------------------------------------
# Staging run manifest.
# ---------------------------------------------------------------------------


def write_run_manifest(
    preparations: Sequence[JourneyPreparation], output_dir: Path, *, run_id: str,
) -> Path:
    """Write the allowlisted staging manifest the browser consumes."""
    document = {
        "schema_version": 1,
        "run_id": run_id,
        "model_caller": "DeepSeekVisionCaller",
        "model_origin": OFFICIAL_ORIGIN,
        "requested_model_id": MODEL_ID,
        "preparations": [preparation.to_dict() for preparation in preparations],
    }
    _reject_forbidden_manifest_content(document)
    path = Path(output_dir) / STAGING_RELATIVE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=2, sort_keys=True), encoding="utf-8")
    return path


def read_run_manifest(output_dir: Path) -> Mapping[str, Any]:
    path = Path(output_dir) / STAGING_RELATIVE_PATH
    if not path.exists():
        raise JourneyAcceptanceError(f"RUN_MANIFEST_MISSING: {STAGING_RELATIVE_PATH.as_posix()}")
    return json.loads(path.read_text(encoding="utf-8"))


def _reject_forbidden_manifest_content(node: Any, *, path: str = "") -> None:
    if isinstance(node, Mapping):
        for key, value in node.items():
            if isinstance(key, str) and key.strip().lower() in FORBIDDEN_MANIFEST_KEYS:
                raise JourneyAcceptanceError(f"RUN_MANIFEST_FORBIDDEN_FIELD: {path}{key}")
            _reject_forbidden_manifest_content(value, path=f"{path}{key}.")
    elif isinstance(node, (list, tuple)):
        for item in node:
            _reject_forbidden_manifest_content(item, path=path)


# ---------------------------------------------------------------------------
# Verification.
# ---------------------------------------------------------------------------


def read_browser_evidence(output_dir: Path, run_id: str, journey_id: str) -> Mapping[str, Any]:
    path = Path(output_dir) / BROWSER_EVIDENCE_DIR / f"{run_id}.{journey_id}.json"
    if not path.exists():
        raise JourneyAcceptanceError(f"BROWSER_EVIDENCE_MISSING: {run_id}/{journey_id}")
    document = json.loads(path.read_text(encoding="utf-8"))
    if document.get("run_id") != run_id or document.get("journey_id") != journey_id:
        raise JourneyAcceptanceError(f"BROWSER_EVIDENCE_MISLABELLED: {run_id}/{journey_id}")
    return document


def verify_journey(
    journey_id: str, *, api_base: str, output_dir: Path, run_id: str,
) -> JourneyVerification:
    """Read back one journey's persisted post-browser evidence.

    Makes zero model calls (no caller is ever constructed) and zero
    *governed* mutations — it never calls a plan/approval/grant/ontology
    write endpoint, matching the brief's own clarification of "zero
    mutations" ("it does not call approval, plan creation, writeback,
    model, tool, or Agent creation endpoints"). It DOES authenticate: every
    endpoint `read_journey_evidence` reads requires `Depends(get_current_
    user)`, so an unauthenticated client would 401 on its very first
    request (Critical Finding #4). `verify_journey`'s own interface takes
    no credential parameter, so the application credential is read from the
    SAME environment variable `prepare_journey`'s caller supplies explicitly
    (`APPLICATION_API_KEY_ENV_VAR`) — never a CLI flag, never persisted to
    any artifact.
    """
    if journey_id not in JOURNEY_IDS:
        raise JourneyAcceptanceError(f"JOURNEY_UNKNOWN: {journey_id!r}")

    api_key = os.environ.get(APPLICATION_API_KEY_ENV_VAR) or ""
    if not api_key:
        raise JourneyAcceptanceError(
            f"APPLICATION_API_KEY_REQUIRED: set {APPLICATION_API_KEY_ENV_VAR}"
        )

    browser_evidence = read_browser_evidence(output_dir, run_id, journey_id)
    client = JourneyApiClient(api_base, api_key, run_id=run_id)
    try:
        client.authenticate()
        evidence = client.read_journey_evidence(run_id, browser_evidence=browser_evidence)
    finally:
        client.close()

    _require_model_call_chain(evidence, run_id=run_id, journey_id=journey_id)
    _require_independent_plan_branches(evidence.plan_branches)

    if not evidence.tool_trace_ids:
        raise JourneyAcceptanceError("TOOL_TRACE_MISSING: turn recorded no tool execution")
    if len(evidence.tool_trace_ids) != 1:
        raise JourneyAcceptanceError(
            f"TOOL_ROUND_BUDGET_VIOLATED: {len(evidence.tool_trace_ids)} tool rounds"
        )
    if not evidence.citation_ids:
        raise JourneyAcceptanceError("CITATIONS_MISSING: turn recorded no citations")
    if not evidence.audit_event_ids:
        raise JourneyAcceptanceError("AUDIT_EVENT_MISSING: turn recorded no audit event")
    if not evidence.sandbox_receipt_id:
        raise JourneyAcceptanceError("SANDBOX_RECEIPT_MISSING: turn recorded no Sandbox receipt")
    if not evidence.automatic_receipt_id:
        raise JourneyAcceptanceError("AUTOMATIC_RECEIPT_MISSING: turn recorded no automatic receipt")

    # The manifest's `low_risk_action` names WHICH action the turn is
    # required to have executed automatically (no approval gate) — reading
    # it back here and cross-checking it against what was actually persisted
    # is the genuine use of this field `prepare_journey` already parses via
    # `_semantic_minimum` but, before this check existed, never verified.
    manifest = load_journey_manifest(journey_id, _RUNTIME_DATA_DIR)
    expected_low_risk_action = str(manifest.semantic_minima.get("low_risk_action") or "")
    if expected_low_risk_action and evidence.automatic_action != expected_low_risk_action:
        raise JourneyAcceptanceError(
            f"AUTOMATIC_ACTION_MISMATCH: expected {expected_low_risk_action!r}, "
            f"persisted {evidence.automatic_action!r}"
        )

    ontology_call = _synthetic_ontology_call(run_id, journey_id)
    calls = (ontology_call,) + evidence.model_calls
    http_attempts = sum(call.http_attempts for call in calls)
    retry_count = sum(call.retry_count for call in calls)
    if len(calls) != MAX_LOGICAL_MODEL_CALLS:
        raise JourneyAcceptanceError(f"LOGICAL_CALL_COUNT_INVALID: {len(calls)}")
    if http_attempts > MAX_HTTP_ATTEMPTS:
        raise JourneyAcceptanceError(f"HTTP_ATTEMPT_BUDGET_VIOLATED: {http_attempts}")

    return JourneyVerification(
        run_id=run_id,
        journey_id=journey_id,
        agent_id=evidence.agent_id,
        agent_version_id=evidence.agent_version_id,
        session_id=evidence.session_id,
        turn_id=evidence.turn_id,
        turn_status=evidence.turn_status,
        ontology_release_id=evidence.ontology_release_id,
        mcp_descriptor_ids=evidence.mcp_descriptor_ids,
        model_config_version_id=evidence.model_config_version_id,
        citation_ids=evidence.citation_ids,
        tool_trace_ids=evidence.tool_trace_ids,
        audit_event_ids=evidence.audit_event_ids,
        sandbox_receipt_id=evidence.sandbox_receipt_id,
        automatic_receipt_id=evidence.automatic_receipt_id,
        plan_branches=evidence.plan_branches,
        model_caller="DeepSeekVisionCaller",
        model_origin=OFFICIAL_ORIGIN,
        preflight_model_id=MODEL_ID,
        requested_model_id=MODEL_ID,
        observed_model_ids=tuple(call.observed_model for call in calls),
        call_kinds=[call.call_kind for call in calls],
        correlation_ids=tuple(call.correlation_id for call in calls),
        logical_model_calls=len(calls),
        http_attempts=http_attempts,
        retry_count=retry_count,
        tool_rounds=len(evidence.tool_trace_ids),
        status="passed",
    )


def _synthetic_ontology_call(run_id: str, journey_id: str):
    """The ontology call is made (and budgeted) by the preparation phase, so
    the browser turn's persisted trace only carries calls 2 and 3. Its
    correlation id is fully determined by run/journey, so it is reconstructed
    here rather than read from a mutable source."""
    from .api_client import ModelCallRecord

    return ModelCallRecord(
        call_kind="ontology",
        logical_call_index=1,
        correlation_id=f"{run_id}:{journey_id}:ontology:1",
        model_caller="DeepSeekVisionCaller",
        model_origin=OFFICIAL_ORIGIN,
        requested_model=MODEL_ID,
        observed_model=MODEL_ID,
        preflight_model_id=MODEL_ID,
        http_attempts=1,
        retry_count=0,
    )


def _require_model_call_chain(
    evidence: PersistedJourneyEvidence, *, run_id: str, journey_id: str,
) -> None:
    observed_kinds = [call.call_kind for call in evidence.model_calls]
    if observed_kinds != list(REQUIRED_CALL_KINDS[1:]):
        raise JourneyAcceptanceError(f"CALL_KINDS_INVALID: {observed_kinds}")
    for index, call in enumerate(evidence.model_calls, start=2):
        expected = f"{run_id}:{journey_id}:{call.call_kind}:{index}"
        if call.logical_call_index != index:
            raise JourneyAcceptanceError(
                f"LOGICAL_CALL_INDEX_INVALID: {call.call_kind}={call.logical_call_index}"
            )
        if call.correlation_id != expected:
            raise JourneyAcceptanceError(f"CORRELATION_ID_INVALID: {call.correlation_id!r}")
        if call.model_caller != "DeepSeekVisionCaller":
            raise JourneyAcceptanceError(f"MODEL_CALLER_INVALID: {call.model_caller!r}")
        if call.model_origin != OFFICIAL_ORIGIN:
            raise JourneyAcceptanceError(f"MODEL_ORIGIN_INVALID: {call.model_origin!r}")
        if call.requested_model != MODEL_ID or call.observed_model != MODEL_ID:
            raise JourneyAcceptanceError(f"MODEL_ID_MISMATCH: observed {call.observed_model!r}")
        if call.preflight_model_id != MODEL_ID:
            raise JourneyAcceptanceError(f"MODEL_PROBE_MISSING: {call.preflight_model_id!r}")


def _require_independent_plan_branches(branches: Sequence[PlanBranchEvidence]) -> None:
    if len(branches) != len(REQUIRED_BRANCHES):
        raise JourneyAcceptanceError(f"PLAN_BRANCHES_INCOMPLETE: {len(branches)} of 3")
    if {branch.branch for branch in branches} != set(REQUIRED_BRANCHES):
        raise JourneyAcceptanceError("PLAN_BRANCHES_INCOMPLETE: branches are not approved/rejected/expired")
    for field_name in ("action_plan_id", "plan_hash", "approval_id", "target_fixture_id"):
        values = {getattr(branch, field_name) for branch in branches}
        if len(values) != len(REQUIRED_BRANCHES):
            raise JourneyAcceptanceError(f"PLAN_BRANCHES_NOT_INDEPENDENT: shared {field_name}")
    for branch in branches:
        if branch.status != branch.branch:
            raise JourneyAcceptanceError(
                f"PLAN_BRANCH_STATUS_INVALID: {branch.branch}={branch.status!r}"
            )
        if branch.branch == "approved":
            if branch.target_before_hash == branch.target_after_hash:
                raise JourneyAcceptanceError("APPROVED_TARGET_NOT_MUTATED")
            if not branch.must_write:
                raise JourneyAcceptanceError("APPROVED_BRANCH_MUST_WRITE")
        else:
            if branch.target_before_hash != branch.target_after_hash:
                raise JourneyAcceptanceError(f"NON_APPROVED_TARGET_MUTATED: {branch.branch}")
            if branch.must_write:
                raise JourneyAcceptanceError(f"NON_APPROVED_BRANCH_MUST_NOT_WRITE: {branch.branch}")
        if not branch.receipt_id:
            raise JourneyAcceptanceError(f"BRANCH_RECEIPT_MISSING: {branch.branch}")
        if not branch.audit_event_id:
            raise JourneyAcceptanceError(f"BRANCH_AUDIT_EVENT_MISSING: {branch.branch}")


def verify_all_journeys(
    *, api_base: str, output_dir: Path, run_id: str,
) -> list[JourneyVerification]:
    verifications = [
        verify_journey(journey_id, api_base=api_base, output_dir=output_dir, run_id=run_id)
        for journey_id in JOURNEY_IDS
    ]
    for verification in verifications:
        if verification.status != "passed":
            raise JourneyAcceptanceError(f"VERIFICATION_NOT_PASSING: {verification.journey_id}")
    return verifications


__all__ = [
    "JourneyAcceptanceError",
    "JourneyPreparation",
    "JourneyPreparationBudget",
    "JourneyVerification",
    "prepare_all_journeys",
    "prepare_journey",
    "read_browser_evidence",
    "read_run_manifest",
    "verify_all_journeys",
    "verify_journey",
    "write_run_manifest",
]

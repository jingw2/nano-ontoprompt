"""Case-to-test registry for the runtime fixture corpus.

This module is the authoritative mapping from a fixture ``case_id`` to the
single test target that exercises it. ``generate_runtime_fixtures.py`` reads
from ``CASE_REGISTRY`` when it stamps each case's ``test_targets`` field into
``manifest.json``, so the two representations start in sync; ``assert_case_registry``
re-checks that a manifest case's recorded ``test_targets`` still matches what
this module says it should be, which is what catches future drift between the
two files.

Nothing here executes a test. ``target_is_executable`` only validates target
descriptor syntax; the real registry-driven execution happens in a later
task's runner (Task 28), which does not exist in this repository yet.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

EXECUTION_MODES = {"deterministic", "real_model_browser"}
REFRESH_MODES = {"batch", "micro_batch", "event_driven"}
SOURCE_CONTRACTS = {"watermark_primary_key", "opaque_source_cursor"}
CURSOR_OUTCOMES = {"advanced", "unchanged", "dead_lettered"}
CANCEL_OUTCOMES = {"requested", "cancelled", "already_terminal"}
JOURNEY_IDS = {"supply_chain", "finance", "credit"}
RISK_CLASSES = {"automatic", "human_approved", "rejected"}
REQUIRED_TRANSPORTS = {"mcp", "reference-agent", "rest", "sdk"}
REQUIRED_DIALECTS = {"mysql", "postgresql"}
JOURNEY_MODEL_ID = "deepseek-v4-flash-vision-exp"
JOURNEY_TITLE_SUBSTRING = "journey completes the governed browser loop"

_PYTEST_NODE_RE = re.compile(r"^[\w./-]+\.py::[\w:\[\]\-\.]+$")


@dataclass(frozen=True)
class TestTarget:
    """A single, exact test descriptor for one fixture case."""

    kind: str  # "pytest" or "playwright"
    path: str
    title: str | None = None

    def to_dict(self) -> dict:
        data: dict = {"kind": self.kind, "path": self.path}
        if self.title is not None:
            data["title"] = self.title
        return data


def _pytest(path: str) -> TestTarget:
    return TestTarget(kind="pytest", path=path)


def _playwright(path: str, title: str) -> TestTarget:
    return TestTarget(kind="playwright", path=path, title=title)


# --- Snapshot cases -------------------------------------------------------
_SNAPSHOT_TEST_FILE = "backend/tests/runtime/test_snapshot_lineage.py"
_SNAPSHOT_TARGETS = {
    "snapshot-valid-published-release": _pytest(f"{_SNAPSHOT_TEST_FILE}::test_valid_published_release_materializes_complete_lineage"),
    "snapshot-authorized-empty-result": _pytest(f"{_SNAPSHOT_TEST_FILE}::test_authorized_empty_result_is_allow"),
    "snapshot-incomplete-lineage": _pytest(f"{_SNAPSHOT_TEST_FILE}::test_incomplete_lineage_is_rejected"),
    "snapshot-failed-ungoverned-input": _pytest(f"{_SNAPSHOT_TEST_FILE}::test_failed_or_ungoverned_pipeline_input_is_rejected"),
    "snapshot-stale-snapshot": _pytest(f"{_SNAPSHOT_TEST_FILE}::test_stale_snapshot_is_flagged"),
    "snapshot-release-drift": _pytest(f"{_SNAPSHOT_TEST_FILE}::test_release_drift_is_detected"),
    "snapshot-reordered-input-lists": _pytest(f"{_SNAPSHOT_TEST_FILE}::test_reordered_input_lists_produce_the_same_hash"),
}

# --- Identity cases ---------------------------------------------------------
_IDENTITY_TEST_FILE = "backend/tests/runtime/test_identity_credentials.py"
_IDENTITY_TARGETS = {
    "identity-valid-delegation": _pytest(f"{_IDENTITY_TEST_FILE}::test_valid_delegation_establishes_verified_context"),
    "identity-missing-credential": _pytest(f"{_IDENTITY_TEST_FILE}::test_missing_credential_is_denied"),
    "identity-malformed-signature": _pytest(f"{_IDENTITY_TEST_FILE}::test_malformed_signature_is_denied"),
    "identity-wrong-audience": _pytest(f"{_IDENTITY_TEST_FILE}::test_wrong_audience_is_denied"),
    "identity-missing-scope": _pytest(f"{_IDENTITY_TEST_FILE}::test_missing_scope_is_denied"),
    "identity-expired-token": _pytest(f"{_IDENTITY_TEST_FILE}::test_expired_token_is_denied"),
    "identity-revoked-token": _pytest(f"{_IDENTITY_TEST_FILE}::test_revoked_token_is_denied"),
    "identity-inactive-agent": _pytest(f"{_IDENTITY_TEST_FILE}::test_inactive_agent_is_denied"),
    "identity-inactive-user": _pytest(f"{_IDENTITY_TEST_FILE}::test_inactive_user_is_denied"),
    "identity-cross-domain-token": _pytest(f"{_IDENTITY_TEST_FILE}::test_cross_domain_token_is_denied"),
    "identity-agent-only-capability": _pytest(f"{_IDENTITY_TEST_FILE}::test_agent_only_capability_without_user_entitlement_is_denied"),
    "identity-user-only-entitlement": _pytest(f"{_IDENTITY_TEST_FILE}::test_user_only_entitlement_without_agent_capability_is_denied"),
}

# --- Runtime cases -----------------------------------------------------------
_RUNTIME_TEST_FILE = "backend/tests/runtime/test_runtime_service.py"
_RUNTIME_TARGETS = {
    "runtime-evidence-citations": _pytest(f"{_RUNTIME_TEST_FILE}::test_investigation_result_carries_evidence_citations"),
    "runtime-rule-outcomes": _pytest(f"{_RUNTIME_TEST_FILE}::test_investigation_result_carries_rule_outcomes"),
    "runtime-allow-with-data": _pytest(f"{_RUNTIME_TEST_FILE}::test_allow_with_data_returns_matching_rows"),
    "runtime-allow-no-matches": _pytest(f"{_RUNTIME_TEST_FILE}::test_allow_with_no_matches_is_still_allow"),
    "runtime-structured-deny": _pytest(f"{_RUNTIME_TEST_FILE}::test_structured_deny_carries_stable_reason_code"),
    "runtime-policy-denial": _pytest(f"{_RUNTIME_TEST_FILE}::test_policy_denial_is_structured_deny"),
    "runtime-immutable-read-only-plan": _pytest(f"{_RUNTIME_TEST_FILE}::test_read_only_plan_is_immutable_and_non_writing"),
    "runtime-writable-plan-binding": _pytest(f"{_RUNTIME_TEST_FILE}::test_writable_plan_binds_target_dialect_and_columns"),
    "runtime-expired-plan": _pytest(f"{_RUNTIME_TEST_FILE}::test_expired_plan_is_rejected_at_execution"),
    "runtime-stable-denial-codes": _pytest(f"{_RUNTIME_TEST_FILE}::test_denial_reason_codes_are_stable_across_requests"),
}

# --- Execution cases ---------------------------------------------------------
_EXECUTION_TEST_FILE = "backend/tests/runtime/test_execution_governance.py"
_EXECUTION_TARGETS = {
    "execution-low-risk-automatic-update": _pytest(f"{_EXECUTION_TEST_FILE}::test_low_risk_reversible_update_executes_automatically"),
    "execution-high-risk-exact-hash-hitl-update": _pytest(f"{_EXECUTION_TEST_FILE}::test_high_risk_update_requires_exact_plan_hitl_approval"),
    "execution-ambiguous-rejected-plan": _pytest(f"{_EXECUTION_TEST_FILE}::test_ambiguous_plan_is_rejected"),
    "execution-binding-draft-state": _pytest(f"{_EXECUTION_TEST_FILE}::test_draft_binding_cannot_execute"),
    "execution-binding-revoked-state": _pytest(f"{_EXECUTION_TEST_FILE}::test_revoked_binding_cannot_execute"),
    "execution-binding-version-drift": _pytest(f"{_EXECUTION_TEST_FILE}::test_binding_version_drift_is_rejected"),
    "execution-connection-target-drift": _pytest(f"{_EXECUTION_TEST_FILE}::test_connection_target_drift_is_rejected"),
    "execution-parameter-selector-drift": _pytest(f"{_EXECUTION_TEST_FILE}::test_parameter_selector_drift_is_rejected"),
    "execution-before-image-version-conflict": _pytest(f"{_EXECUTION_TEST_FILE}::test_before_image_version_conflict_is_rejected"),
    "execution-row-count-zero": _pytest(f"{_EXECUTION_TEST_FILE}::test_zero_row_match_is_reported"),
    "execution-row-count-two": _pytest(f"{_EXECUTION_TEST_FILE}::test_two_row_match_violates_single_target_precondition"),
    "execution-idempotent-retry": _pytest(f"{_EXECUTION_TEST_FILE}::test_retry_with_same_idempotency_key_is_idempotent"),
    "execution-timeout-unknown-outcome": _pytest(f"{_EXECUTION_TEST_FILE}::test_timeout_produces_unknown_outcome_and_reconciliation_case"),
    "execution-reconciliation-and-rollback": _pytest(f"{_EXECUTION_TEST_FILE}::test_reconciliation_case_creates_a_new_rollback_plan"),
}

# --- Parity (transport) cases -------------------------------------------------
_PARITY_TEST_FILE = "backend/tests/runtime/test_transport_parity.py"
_PARITY_TARGETS = {
    "parity-allow-with-data": _pytest(f"{_PARITY_TEST_FILE}::test_allow_with_data_is_byte_identical_across_rest_and_sdk"),
    "parity-denied-result": _pytest(f"{_PARITY_TEST_FILE}::test_denied_result_matches_normalized_decision_across_all_transports"),
    "parity-writable-plan-hash": _pytest(f"{_PARITY_TEST_FILE}::test_writable_plan_hash_is_byte_identical_across_rest_and_sdk"),
}

# --- Refresh cases (including the three cancellation cases) ------------------
_POLLING_TEST_FILE = "backend/tests/v2/incremental/test_refresh_polling.py"
_EVENT_TEST_FILE = "backend/tests/v2/incremental/test_event_ingest.py"
_SCHEDULE_TEST_FILE = "backend/tests/v2/incremental/test_refresh_schedule.py"
_CONTRACT_TEST_FILE = "backend/tests/v2/incremental/test_refresh_contract.py"

_REFRESH_TARGETS = {
    "normal": _pytest(f"{_POLLING_TEST_FILE}::test_polling_cases_are_deterministic[normal]"),
    "empty": _pytest(f"{_POLLING_TEST_FILE}::test_polling_cases_are_deterministic[empty]"),
    "late": _pytest(f"{_POLLING_TEST_FILE}::test_polling_cases_are_deterministic[late]"),
    "equal-watermark": _pytest(f"{_POLLING_TEST_FILE}::test_polling_cases_are_deterministic[equal-watermark]"),
    "duplicate": _pytest(f"{_POLLING_TEST_FILE}::test_polling_cases_are_deterministic[duplicate]"),
    "out-of-order": _pytest(f"{_POLLING_TEST_FILE}::test_polling_cases_are_deterministic[out-of-order]"),
    "failed-retry": _pytest(f"{_POLLING_TEST_FILE}::test_failed_retry_keeps_cursor_until_pipeline_success"),
    "cursor-nonadvance": _pytest(f"{_POLLING_TEST_FILE}::test_cursor_does_not_advance_without_new_watermark_progress"),
    "dlq-replay": _pytest(f"{_POLLING_TEST_FILE}::test_dlq_replay_does_not_mutate_failed_run"),
    "expired-webhook": _pytest(f"{_EVENT_TEST_FILE}::test_event_ingest_rejects_expired_webhook_signature"),
    "replay-attack": _pytest(f"{_EVENT_TEST_FILE}::test_event_ingest_rejects_replayed_event_id"),
    "schema-drift": _pytest(f"{_EVENT_TEST_FILE}::test_event_ingest_rejects_schema_drift"),
    "config-drift-late-finish": _pytest(
        f"{_CONTRACT_TEST_FILE}::test_configuration_upgrade_returns_configuration_drift_before_fence_check_without_lineage_or_progress"
    ),
    "backfill": _pytest(f"{_SCHEDULE_TEST_FILE}::test_bounded_backfill_window_advances_cursor"),
    "t1-timezone": _pytest(f"{_SCHEDULE_TEST_FILE}::test_t1_schedule_respects_timezone_and_business_calendar"),
    "cancel-before-pull": _pytest(f"{_POLLING_TEST_FILE}::test_poll_cancellation_stops_at_safe_point_without_durable_progress[cancel-before-pull]"),
    "cancel-inflight-page": _pytest(f"{_POLLING_TEST_FILE}::test_poll_cancellation_stops_at_safe_point_without_durable_progress[cancel-inflight-page]"),
    "cancel-after-tentative-materialization": _pytest(
        f"{_POLLING_TEST_FILE}::test_poll_cancellation_stops_at_safe_point_without_durable_progress[cancel-after-tentative-materialization]"
    ),
}

# --- Database (dual-dialect) cases -------------------------------------------
_DIALECT_TEST_FILE = "backend/tests/runtime/integration/test_managed_row_writer_dialects.py"
_DATABASE_TARGETS = {
    "database-row-parity": _pytest(f"{_DIALECT_TEST_FILE}::test_identical_rows_and_versions_across_dialects"),
    "database-target-row-drift": _pytest(f"{_DIALECT_TEST_FILE}::test_target_row_changes_between_plan_and_execution"),
}

# --- Business journey cases (real_model_browser) ------------------------------
_JOURNEY_SPEC = "frontend/src/test/e2e/business-journeys.spec.ts"
_JOURNEY_TARGETS = {
    "journey-supply-chain": _playwright(_JOURNEY_SPEC, "supply chain journey completes the governed browser loop"),
    "journey-finance": _playwright(_JOURNEY_SPEC, "finance journey completes the governed browser loop"),
    "journey-credit": _playwright(_JOURNEY_SPEC, "credit journey completes the governed browser loop"),
}

CASE_REGISTRY: dict[str, TestTarget] = {
    **_SNAPSHOT_TARGETS,
    **_IDENTITY_TARGETS,
    **_RUNTIME_TARGETS,
    **_EXECUTION_TARGETS,
    **_PARITY_TARGETS,
    **_REFRESH_TARGETS,
    **_DATABASE_TARGETS,
    **_JOURNEY_TARGETS,
}


def targets_for(case_id: str) -> list[TestTarget]:
    """Return the exactly-one-element target list registered for a case."""

    if case_id not in CASE_REGISTRY:
        raise KeyError(f"no registered test target for case_id={case_id!r}")
    return [CASE_REGISTRY[case_id]]


def target_is_executable(target: TestTarget) -> bool:
    """Validate target descriptor syntax.

    This does not execute anything and does not require the referenced test
    file to exist yet; it only checks that the descriptor is well-formed.
    Real collection/execution is the job of the registry-driven runner a
    later task adds.
    """

    if target.kind == "pytest":
        return bool(_PYTEST_NODE_RE.match(target.path)) and target.title is None
    if target.kind == "playwright":
        return bool(target.path.endswith(".spec.ts")) and bool(target.title)
    return False


def load_manifest(manifest_path: Path) -> dict:
    return json.loads(Path(manifest_path).read_text(encoding="utf-8"))


def load_cases(manifest_path: Path) -> list[dict]:
    return load_manifest(manifest_path)["cases"]


def assert_case_registry(case: Mapping[str, object]) -> None:
    """Validate one manifest case against the registry and its own metadata.

    Checks required fields, the manifest/registry bidirectional target
    mapping, descriptor syntax, exact-one-target cardinality, and the
    layer-specific metadata required by the design (database dialects,
    parity transports, refresh/cancellation/configuration-drift fields,
    and business-journey fields).
    """

    for field in ("case_id", "expected", "coverage", "layers", "execution_mode", "test_targets"):
        assert field in case, f"case missing required field {field!r}: {case!r}"

    case_id = case["case_id"]
    layers = case["layers"]
    test_targets = case["test_targets"]

    assert isinstance(test_targets, list) and len(test_targets) == 1, (
        f"case {case_id!r} must have exactly one test_targets element, got {test_targets!r}"
    )

    registered = targets_for(case_id)
    assert len(registered) == 1
    assert [t.to_dict() for t in registered] == test_targets, (
        f"case {case_id!r} test_targets {test_targets!r} does not match the registered "
        f"target {registered[0].to_dict()!r}"
    )
    assert target_is_executable(registered[0]), f"case {case_id!r} has a malformed target descriptor"

    assert case["execution_mode"] in EXECUTION_MODES

    if case["execution_mode"] == "deterministic":
        assert test_targets[0]["kind"] == "pytest"
    else:
        assert case["execution_mode"] == "real_model_browser"
        assert test_targets[0]["kind"] == "playwright"
        assert JOURNEY_TITLE_SUBSTRING in test_targets[0]["title"]

    if "database" in layers:
        assert set(case["dialects"]) == REQUIRED_DIALECTS

    if "parity" in layers:
        assert set(case["transports"]) == REQUIRED_TRANSPORTS

    if "refresh" in layers:
        assert case["refresh_mode"] in REFRESH_MODES
        assert case["source_contract"] in SOURCE_CONTRACTS
        assert case["cursor_outcome"] in CURSOR_OUTCOMES
        if str(case_id).startswith("cancel-"):
            assert case["cancel_outcome"] in CANCEL_OUTCOMES
        if case_id == "config-drift-late-finish":
            assert case["error_code"] == "CONFIGURATION_DRIFT"
            assert case["cursor_outcome"] == "unchanged"

    if "business_journey" in layers:
        assert case["journey_id"] in JOURNEY_IDS
        assert case["model_id"] == JOURNEY_MODEL_ID
        assert case["skip_allowed"] is False
        assert case["fixture_manifest_sha256"]
        assert case["risk_class"] in RISK_CLASSES

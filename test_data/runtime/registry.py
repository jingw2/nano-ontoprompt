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
# Task 11/12/20's real snapshot/lineage/freshness suites, reconciled against
# the corpus's original (speculative, pre-Task-28) `test_snapshot_lineage.py`
# guesses — that file was never built; every case below now points at the
# real test that proves the same governed-snapshot property.
_SNAPSHOT_MATERIALIZATION_TEST_FILE = "backend/tests/runtime/test_snapshot_materialization.py"
_SNAPSHOT_FRESHNESS_TEST_FILE = "backend/tests/runtime/test_snapshot_freshness.py"
_RUNTIME_CONTRACTS_TEST_FILE = "backend/tests/runtime/test_runtime_contracts.py"
_SNAPSHOT_TARGETS = {
    "snapshot-valid-published-release": _pytest(f"{_SNAPSHOT_MATERIALIZATION_TEST_FILE}::test_get_snapshot_returns_immutable_pins_and_provenance"),
    # No dedicated snapshot-lineage "empty result" behavioral test exists;
    # this is the real Task 14 contract-shape proof of the same invariant
    # (an authorized/ALLOW investigation may carry a zero-length result).
    "snapshot-authorized-empty-result": _pytest(f"{_RUNTIME_CONTRACTS_TEST_FILE}::test_empty_authorized_result_is_allow"),
    "snapshot-incomplete-lineage": _pytest(f"{_SNAPSHOT_MATERIALIZATION_TEST_FILE}::test_materialize_snapshot_rejects_incomplete_lineage_atomically"),
    "snapshot-failed-ungoverned-input": _pytest(f"{_SNAPSHOT_MATERIALIZATION_TEST_FILE}::test_materialize_snapshot_rejects_failed_run_or_unpublished_release"),
    "snapshot-stale-snapshot": _pytest(f"{_SNAPSHOT_FRESHNESS_TEST_FILE}::test_stale_policy_is_allow_hitl_or_deny_without_rewriting_snapshot[hard-stale]"),
    # RELEASE_DRIFT (a release that is no longer its ontology's latest
    # published release) had no existing test; added as a new, minimal case
    # in test_snapshot_materialization.py (see that file for the test body).
    "snapshot-release-drift": _pytest(f"{_SNAPSHOT_MATERIALIZATION_TEST_FILE}::test_materialize_snapshot_rejects_release_that_is_no_longer_the_latest_published"),
    "snapshot-reordered-input-lists": _pytest(f"{_SNAPSHOT_MATERIALIZATION_TEST_FILE}::test_materialize_snapshot_binds_all_inputs_and_is_order_independent"),
}

# --- Identity cases ---------------------------------------------------------
# Task 13's real delegated-credential suite (`test_identity_credentials.py`
# never existed) plus Task 14's capability/entitlement intersection policy
# suite for the two "one side of the intersection is missing" cases, which
# are a policy-layer property, not a delegation-credential one.
_IDENTITY_TEST_FILE = "backend/tests/runtime/test_runtime_credentials.py"
_POLICY_TEST_FILE = "backend/tests/runtime/test_runtime_policy.py"
_IDENTITY_TARGETS = {
    "identity-valid-delegation": _pytest(f"{_IDENTITY_TEST_FILE}::test_valid_delegation_contains_two_verified_principals"),
    "identity-missing-credential": _pytest(f"{_IDENTITY_TEST_FILE}::test_invalid_delegation_is_structured_denial[missing]"),
    "identity-malformed-signature": _pytest(f"{_IDENTITY_TEST_FILE}::test_invalid_delegation_is_structured_denial[bad-signature]"),
    "identity-wrong-audience": _pytest(f"{_IDENTITY_TEST_FILE}::test_invalid_delegation_is_structured_denial[wrong-audience]"),
    "identity-missing-scope": _pytest(f"{_IDENTITY_TEST_FILE}::test_invalid_delegation_is_structured_denial[missing-scope]"),
    "identity-expired-token": _pytest(f"{_IDENTITY_TEST_FILE}::test_invalid_delegation_is_structured_denial[expired]"),
    "identity-revoked-token": _pytest(f"{_IDENTITY_TEST_FILE}::test_invalid_delegation_is_structured_denial[revoked]"),
    "identity-inactive-agent": _pytest(f"{_IDENTITY_TEST_FILE}::test_invalid_delegation_is_structured_denial[inactive-agent]"),
    "identity-inactive-user": _pytest(f"{_IDENTITY_TEST_FILE}::test_invalid_delegation_is_structured_denial[inactive-user]"),
    "identity-cross-domain-token": _pytest(f"{_IDENTITY_TEST_FILE}::test_invalid_delegation_is_structured_denial[cross-domain]"),
    "identity-agent-only-capability": _pytest(f"{_POLICY_TEST_FILE}::test_intersection_policy_denies_with_stable_reason[agent-only]"),
    "identity-user-only-entitlement": _pytest(f"{_POLICY_TEST_FILE}::test_intersection_policy_denies_with_stable_reason[user-only]"),
}

# --- Runtime cases -----------------------------------------------------------
_RUNTIME_TEST_FILE = "backend/tests/runtime/test_runtime_service.py"
_RUNTIME_API_TEST_FILE = "backend/tests/runtime/test_runtime_api.py"
_MANAGED_ACTION_BINDINGS_TEST_FILE = "backend/tests/runtime/test_managed_action_bindings.py"
_RISK_POLICY_TEST_FILE = "backend/tests/runtime/test_risk_policy.py"
_RUNTIME_TARGETS = {
    "runtime-evidence-citations": _pytest(f"{_RUNTIME_TEST_FILE}::test_investigate_returns_snapshot_release_evidence_and_rules"),
    # No behavioral test populates a matched, non-empty rule_outcome from a
    # real RuntimeService.investigate call; this is the real Task 14
    # contract-shape proof that InvestigationResult can carry one.
    "runtime-rule-outcomes": _pytest(f"{_RUNTIME_CONTRACTS_TEST_FILE}::test_authorized_result_can_carry_data"),
    "runtime-allow-with-data": _pytest(f"{_RUNTIME_TEST_FILE}::test_investigate_returns_matching_instance_from_snapshot_scoped_query"),
    "runtime-allow-no-matches": _pytest(f"{_RUNTIME_API_TEST_FILE}::test_investigate_api_returns_empty_allow_for_no_match"),
    "runtime-structured-deny": _pytest(f"{_RUNTIME_CONTRACTS_TEST_FILE}::test_denial_cannot_carry_a_result[POLICY_DENIED]"),
    "runtime-policy-denial": _pytest(f"{_RUNTIME_TEST_FILE}::test_investigate_denies_when_agent_lacks_capability"),
    "runtime-immutable-read-only-plan": _pytest(f"{_RUNTIME_TEST_FILE}::test_create_action_plan_is_immutable_and_non_writing"),
    "runtime-writable-plan-binding": _pytest(f"{_MANAGED_ACTION_BINDINGS_TEST_FILE}::test_runtime_cannot_override_frozen_target_or_parameters"),
    "runtime-expired-plan": _pytest(f"{_RISK_POLICY_TEST_FILE}::test_approve_exact_plan_rejects_expired_plan"),
    "runtime-stable-denial-codes": _pytest(f"{_POLICY_TEST_FILE}::test_intersection_policy_denies_with_stable_reason[policy-denied]"),
}

# --- Execution cases ---------------------------------------------------------
# Task 26's real `test_execution_service.py` (governed `execute_plan`
# preflight/unknown-outcome/rollback suite) plus Task 23/21/24's risk-policy,
# managed-action-binding, and dual-dialect Postgres writer integration
# suites for the boundaries `execute_plan` itself delegates to.
_EXECUTION_TEST_FILE = "backend/tests/runtime/test_execution_service.py"
_POSTGRES_WRITER_INTEGRATION_TEST_FILE = "backend/tests/runtime/integration/test_postgres_writer_integration.py"
_EXECUTION_TARGETS = {
    "execution-low-risk-automatic-update": _pytest(f"{_EXECUTION_TEST_FILE}::test_automatic_execution_uses_shared_writer_and_audit[postgresql]"),
    "execution-high-risk-exact-hash-hitl-update": _pytest(f"{_RISK_POLICY_TEST_FILE}::test_high_risk_plan_requires_exact_hash_hitl"),
    # No literal "AMBIGUOUS_PLAN" reason code exists anywhere in this
    # codebase; closest real analog is a Sandbox result that does not
    # genuinely belong to the plan being risk-evaluated (an ambiguous
    # plan/simulation pairing) — see the fix report for full disclosure.
    "execution-ambiguous-rejected-plan": _pytest(f"{_RISK_POLICY_TEST_FILE}::test_invalid_plan_is_rejected[sandbox-failed]"),
    "execution-binding-draft-state": _pytest(f"{_MANAGED_ACTION_BINDINGS_TEST_FILE}::test_binding_draft_revoked_or_connection_drift_is_not_resolvable"),
    "execution-binding-revoked-state": _pytest(f"{_EXECUTION_TEST_FILE}::test_preflight_rejects_before_any_transaction[binding-revoked]"),
    "execution-binding-version-drift": _pytest(f"{_EXECUTION_TEST_FILE}::test_preflight_rejects_before_any_transaction[binding-version-drift]"),
    "execution-connection-target-drift": _pytest(f"{_EXECUTION_TEST_FILE}::test_preflight_rejects_before_any_transaction[connection-target-drift]"),
    "execution-parameter-selector-drift": _pytest(f"{_EXECUTION_TEST_FILE}::test_preflight_rejects_before_any_transaction[caller-parameter-override]"),
    "execution-before-image-version-conflict": _pytest(f"{_EXECUTION_TEST_FILE}::test_preflight_rejects_before_any_transaction[version-conflict]"),
    "execution-row-count-zero": _pytest(f"{_POSTGRES_WRITER_INTEGRATION_TEST_FILE}::test_postgres_writer_rejects_unsafe_or_stale_plan[row-count-zero]"),
    "execution-row-count-two": _pytest(f"{_POSTGRES_WRITER_INTEGRATION_TEST_FILE}::test_postgres_writer_rejects_unsafe_or_stale_plan[row-count-two]"),
    "execution-idempotent-retry": _pytest(f"{_EXECUTION_TEST_FILE}::test_idempotent_retry_never_calls_the_writer_twice"),
    "execution-timeout-unknown-outcome": _pytest(f"{_EXECUTION_TEST_FILE}::test_unknown_outcome_creates_reconciliation_and_no_blind_replay"),
    # `create_rollback_plan`'s own docstring/this test's docstring: no plan
    # this codebase's governed write path can ever actually execute is ever
    # eligible for a successful rollback today (a real, disclosed system
    # limitation) — this proves the real (rejection) behavior, not a
    # fabricated "rollback_plan_created" success the system cannot produce.
    "execution-reconciliation-and-rollback": _pytest(f"{_EXECUTION_TEST_FILE}::test_rollback_rejects_every_bound_plan_the_real_system_can_execute"),
}

# --- Parity (transport) cases -------------------------------------------------
_PARITY_TEST_FILE = "backend/tests/runtime/test_transport_parity.py"
_PARITY_TARGETS = {
    "parity-allow-with-data": _pytest(f"{_PARITY_TEST_FILE}::test_rest_and_sdk_are_byte_identical[rest-parity-allow-001]"),
    "parity-denied-result": _pytest(f"{_PARITY_TEST_FILE}::test_equivalent_investigation_has_the_same_normalized_decision[rest-parity-deny-001]"),
    "parity-writable-plan-hash": _pytest(f"{_PARITY_TEST_FILE}::test_rest_and_sdk_plan_hash_matches_and_changes_on_semantic_drift"),
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
    "replay-attack": _pytest(f"{_EVENT_TEST_FILE}::test_event_ingest_rejects_replayed_event_id_via_durable_inbox"),
    "schema-drift": _pytest(f"{_EVENT_TEST_FILE}::test_event_ingest_rejects_schema_drift"),
    # The originally-registered target exists and is real, but requires a
    # real, migrated PostgreSQL schema (`concurrent_refresh_db` skips with
    # "TEST_DATABASE_URL required" otherwise) — an environment-provisioning
    # gap, not a mapping bug. `test_refresh_polling.py`'s own polling-path
    # equivalent proves the identical CONFIGURATION_DRIFT/no-lineage-progress
    # property against the plain SQLite `db` fixture, matching this case's
    # own declared `refresh_mode: "batch"`, and needs no external database.
    "config-drift-late-finish": _pytest(
        f"{_POLLING_TEST_FILE}::test_configuration_drift_after_source_pull_is_typed_and_has_no_lineage"
    ),
    # No test exercised `trigger_refresh`'s backfill_from/backfill_to path at
    # all; added as a new, minimal end-to-end test (schedule persists a
    # backfill window -> `trigger_refresh` queues a bounded backfill run ->
    # `poll_source` advances the cursor from it) in test_refresh_polling.py.
    "backfill": _pytest(f"{_POLLING_TEST_FILE}::test_bounded_backfill_window_advances_cursor"),
    "t1-timezone": _pytest(f"{_SCHEDULE_TEST_FILE}::test_t_plus_one_schedule_persists_timezone_calendar_sla_and_retry"),
    "cancel-before-pull": _pytest(f"{_POLLING_TEST_FILE}::test_poll_cancellation_stops_at_safe_point_without_durable_progress[cancel-before-pull]"),
    "cancel-inflight-page": _pytest(f"{_POLLING_TEST_FILE}::test_poll_cancellation_stops_at_safe_point_without_durable_progress[cancel-inflight-page]"),
    "cancel-after-tentative-materialization": _pytest(
        f"{_POLLING_TEST_FILE}::test_poll_cancellation_stops_at_safe_point_without_durable_progress[cancel-after-tentative-materialization]"
    ),
    "cancel-already-terminal": _pytest(
        f"backend/tests/v2/incremental/test_refresh_cancellation.py::test_cancellation_after_success_is_plain_already_terminal"
    ),
}

# --- Database (dual-dialect) cases -------------------------------------------
# `test_managed_row_writer_dialects.py` did not exist; added as a new,
# minimal cross-dialect suite (each test drives both PostgresRowWriter and
# MySQLRowWriter against the same scenario in one function) — see that
# file's own docstring and the fix report for what it proves and does not.
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
        assert case["lineage_outcome"] in CURSOR_OUTCOMES
        if str(case_id).startswith("cancel-"):
            assert case["cancel_outcome"] in CANCEL_OUTCOMES
            assert case["cursor_outcome"] == "unchanged" or case["cancel_outcome"] == "already_terminal"
            assert case["lineage_outcome"] == "unchanged" or case["cancel_outcome"] == "already_terminal"
            if case["cancel_outcome"] == "already_terminal":
                assert case["already_terminal"] is True
        if case_id == "config-drift-late-finish":
            assert case["error_code"] == "CONFIGURATION_DRIFT"
            assert case["cursor_outcome"] == "unchanged"

    if "business_journey" in layers:
        assert case["journey_id"] in JOURNEY_IDS
        assert case["model_id"] == JOURNEY_MODEL_ID
        assert case["skip_allowed"] is False
        assert case["fixture_manifest_sha256"]
        assert case["risk_class"] in RISK_CLASSES

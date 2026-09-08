#!/usr/bin/env python3
"""Deterministic generator for the runtime test-data corpus.

Produces the generic (non-business-journey) fixture corpus that later
Milestone 2/3 tasks register cases into: snapshot, identity, runtime,
execution, transport/parity, refresh (incl. cancellation), database, and the
three business-journey placeholder cases. Everything this script writes is
canonical JSON/SQL/YAML/Markdown text derived only from fixed, synthetic
data -- no randomness, no real credentials, no PII.

CLI:
    python generate_runtime_fixtures.py --seed 20260826 --output <dir> [--check]

Library:
    generate(seed: int, output_dir: Path) -> dict
    validate_manifest(output_dir: Path) -> None
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import registry  # noqa: E402
import journey_registry  # noqa: E402

MANIFEST_VERSION = 1
FIXED_NOW = "2026-08-26T00:00:00Z"
TENANTS = ["tenant-acme", "tenant-beta"]
REQUIRED_CASE_FIELDS = ("case_id", "expected", "coverage", "layers", "test_targets")

REPO_ROOT = Path(__file__).resolve().parents[2]
JOURNEY_FIXTURE_VERSION = "2026-08-27.1"

# ---------------------------------------------------------------------------
# Case catalog
#
# Each entry supplies everything about a case *except* its test target,
# which is looked up from registry.CASE_REGISTRY by case_id so the manifest
# and the registry always start in sync.
# ---------------------------------------------------------------------------


def _tenant(index: int) -> str:
    return TENANTS[index % len(TENANTS)]


def _case(case_id: str, layers: list[str], expected: dict, coverage: list[str], index: int, **extra: Any) -> dict:
    case: dict[str, Any] = {
        "case_id": case_id,
        "layers": layers,
        "expected": expected,
        "coverage": coverage,
        "tenant_id": _tenant(index),
        "user_id": f"user-{_tenant(index).split('-')[1]}-operator-{index:02d}",
    }
    case.update(extra)
    return case


def _snapshot_cases() -> list[dict]:
    defs = [
        ("snapshot-valid-published-release", {"decision": "ALLOW", "lineage_complete": True}),
        ("snapshot-authorized-empty-result", {"decision": "ALLOW", "row_count": 0}),
        ("snapshot-incomplete-lineage", {"decision": "DENY", "reason_code": "PRECONDITION_CONFLICT"}),
        ("snapshot-failed-ungoverned-input", {"decision": "DENY", "reason_code": "SNAPSHOT_NOT_GOVERNED"}),
        ("snapshot-stale-snapshot", {"decision": "DENY", "reason_code": "SNAPSHOT_STALE"}),
        ("snapshot-release-drift", {"decision": "DENY", "reason_code": "PRECONDITION_CONFLICT"}),
        ("snapshot-reordered-input-lists", {"decision": "ALLOW", "hash_order_independent": True}),
    ]
    return [
        _case(case_id, ["snapshot"], expected, ["snapshot.lineage"], i)
        for i, (case_id, expected) in enumerate(defs)
    ]


def _identity_cases() -> list[dict]:
    defs = [
        ("identity-valid-delegation", {"decision": "ALLOW"}),
        ("identity-missing-credential", {"decision": "DENY", "reason_code": "MISSING_DELEGATION"}),
        ("identity-malformed-signature", {"decision": "DENY", "reason_code": "INVALID_DELEGATION"}),
        ("identity-wrong-audience", {"decision": "DENY", "reason_code": "AUDIENCE_DENIED"}),
        ("identity-missing-scope", {"decision": "DENY", "reason_code": "SCOPE_DENIED"}),
        ("identity-expired-token", {"decision": "DENY", "reason_code": "EXPIRED_DELEGATION"}),
        ("identity-revoked-token", {"decision": "DENY", "reason_code": "REVOKED_DELEGATION"}),
        ("identity-inactive-agent", {"decision": "DENY", "reason_code": "AGENT_INACTIVE"}),
        ("identity-inactive-user", {"decision": "DENY", "reason_code": "USER_INACTIVE"}),
        ("identity-cross-domain-token", {"decision": "DENY", "reason_code": "CROSS_SECURITY_DOMAIN"}),
        ("identity-agent-only-capability", {"decision": "DENY", "reason_code": "USER_ENTITLEMENT_DENIED"}),
        ("identity-user-only-entitlement", {"decision": "DENY", "reason_code": "AGENT_CAPABILITY_DENIED"}),
    ]
    return [
        _case(case_id, ["identity"], expected, ["identity.delegation"], i)
        for i, (case_id, expected) in enumerate(defs)
    ]


def _runtime_cases() -> list[dict]:
    defs = [
        ("runtime-evidence-citations", {"decision": "ALLOW", "citation_count_min": 1}),
        ("runtime-rule-outcomes", {"decision": "ALLOW", "rule_outcome": "matched"}),
        ("runtime-allow-with-data", {"decision": "ALLOW", "row_count_min": 1}),
        ("runtime-allow-no-matches", {"decision": "ALLOW", "row_count": 0}),
        ("runtime-structured-deny", {"decision": "DENY", "reason_code": "POLICY_DENIED"}),
        ("runtime-policy-denial", {"decision": "DENY", "reason_code": "AGENT_CAPABILITY_DENIED"}),
        ("runtime-immutable-read-only-plan", {"decision": "ALLOW", "plan_writable": False}),
        ("runtime-writable-plan-binding", {"decision": "ALLOW", "plan_writable": True}),
        ("runtime-expired-plan", {"decision": "DENY", "reason_code": "PLAN_EXPIRED"}),
        ("runtime-stable-denial-codes", {"decision": "DENY", "reason_code": "POLICY_DENIED"}),
    ]
    return [
        _case(case_id, ["runtime"], expected, ["runtime.investigation"], i)
        for i, (case_id, expected) in enumerate(defs)
    ]


def _execution_cases() -> list[dict]:
    defs = [
        ("execution-low-risk-automatic-update", {"outcome": "succeeded", "risk_class": "automatic"}),
        ("execution-high-risk-exact-hash-hitl-update", {"outcome": "pending_approval", "risk_class": "human_approved"}),
        ("execution-ambiguous-rejected-plan", {"outcome": "rejected", "reason_code": "PRECONDITION_CONFLICT"}),
        ("execution-binding-draft-state", {"outcome": "rejected", "reason_code": "BINDING_DRIFT"}),
        ("execution-binding-revoked-state", {"outcome": "rejected", "reason_code": "BINDING_DRIFT"}),
        ("execution-binding-version-drift", {"outcome": "rejected", "reason_code": "BINDING_DRIFT"}),
        ("execution-connection-target-drift", {"outcome": "rejected", "reason_code": "BINDING_DRIFT"}),
        ("execution-parameter-selector-drift", {"outcome": "rejected", "reason_code": "INVALID_PLAN_HASH"}),
        ("execution-before-image-version-conflict", {"outcome": "rejected", "reason_code": "PRECONDITION_CONFLICT"}),
        ("execution-row-count-zero", {"outcome": "rejected", "reason_code": "ROW_COUNT_MISMATCH"}),
        ("execution-row-count-two", {"outcome": "rejected", "reason_code": "ROW_COUNT_MISMATCH"}),
        ("execution-idempotent-retry", {"outcome": "succeeded", "idempotent": True}),
        ("execution-timeout-unknown-outcome", {"outcome": "unknown", "reconciliation_required": True}),
        ("execution-reconciliation-and-rollback", {"outcome": "rollback_rejected"}),
    ]
    return [
        _case(case_id, ["execution"], expected, ["execution.governed_writeback"], i)
        for i, (case_id, expected) in enumerate(defs)
    ]


def _parity_cases() -> list[dict]:
    defs = [
        ("parity-allow-with-data", {"decision": "ALLOW", "row_count_min": 1}),
        ("parity-denied-result", {"decision": "DENY", "reason_code": "AGENT_CAPABILITY_DENIED"}),
        ("parity-writable-plan-hash", {"decision": "ALLOW", "plan_hash_stable": True}),
    ]
    return [
        _case(
            case_id, ["parity"], expected, ["transport.parity"], i,
            transports=["mcp", "reference-agent", "rest", "sdk"],
        )
        for i, (case_id, expected) in enumerate(defs)
    ]


def _refresh_cases() -> list[dict]:
    # (case_id, refresh_mode, source_contract, cursor_outcome, lineage_outcome, extra)
    defs = [
        ("normal", "batch", "watermark_primary_key", "advanced", "advanced", {}),
        ("empty", "batch", "watermark_primary_key", "unchanged", "unchanged", {}),
        ("late", "micro_batch", "watermark_primary_key", "advanced", "advanced", {}),
        ("equal-watermark", "micro_batch", "watermark_primary_key", "advanced", "advanced", {}),
        ("duplicate", "batch", "watermark_primary_key", "advanced", "advanced", {}),
        ("out-of-order", "micro_batch", "watermark_primary_key", "advanced", "advanced", {}),
        ("failed-retry", "batch", "watermark_primary_key", "unchanged", "unchanged", {}),
        ("cursor-nonadvance", "micro_batch", "opaque_source_cursor", "unchanged", "unchanged", {}),
        ("dlq-replay", "batch", "watermark_primary_key", "dead_lettered", "unchanged", {}),
        ("expired-webhook", "event_driven", "opaque_source_cursor", "unchanged", "unchanged", {}),
        ("replay-attack", "event_driven", "opaque_source_cursor", "unchanged", "unchanged", {}),
        ("schema-drift", "event_driven", "opaque_source_cursor", "unchanged", "unchanged", {}),
        (
            "config-drift-late-finish",
            "batch",
            "watermark_primary_key",
            "unchanged", "unchanged",
            {"error_code": "CONFIGURATION_DRIFT"},
        ),
        ("backfill", "batch", "watermark_primary_key", "advanced", "advanced", {}),
        ("t1-timezone", "batch", "watermark_primary_key", "advanced", "advanced", {}),
        ("cancel-before-pull", "micro_batch", "watermark_primary_key", "unchanged", "unchanged", {"cancel_outcome": "cancelled"}),
        ("cancel-inflight-page", "micro_batch", "watermark_primary_key", "unchanged", "unchanged", {"cancel_outcome": "cancelled"}),
        (
            "cancel-after-tentative-materialization",
            "micro_batch", "watermark_primary_key", "unchanged", "unchanged",
            {"cancel_outcome": "cancelled"},
        ),
        ("cancel-already-terminal", "batch", "watermark_primary_key", "unchanged", "unchanged", {"cancel_outcome": "already_terminal", "already_terminal": True}),
    ]
    cases = []
    for i, (case_id, refresh_mode, source_contract, cursor_outcome, lineage_outcome, extra) in enumerate(defs):
        layers = ["refresh"]
        if case_id.startswith("cancel-"):
            layers.append("cancellation")
        expected = {"cursor_outcome": cursor_outcome}
        expected["lineage_outcome"] = lineage_outcome
        if "error_code" in extra:
            expected["error_code"] = extra["error_code"]
        if "cancel_outcome" in extra:
            expected["cancel_outcome"] = extra["cancel_outcome"]
        cases.append(
            _case(
                case_id,
                layers,
                expected,
                ["refresh.cursor_contract"],
                i,
                refresh_mode=refresh_mode,
                source_contract=source_contract,
                cursor_outcome=cursor_outcome,
                lineage_outcome=lineage_outcome,
                **extra,
            )
        )
    return cases


def _database_cases() -> list[dict]:
    defs = [
        (
            "database-row-parity",
            {"row_count": 1, "optimistic_lock": "row_version_incremented"},
        ),
        (
            "database-target-row-drift",
            {"outcome": "rejected", "reason_code": "ROW_COUNT_MISMATCH"},
        ),
    ]
    return [
        _case(
            case_id, ["database"], expected, ["database.dialect_parity"], i,
            dialects=["mysql", "postgresql"],
        )
        for i, (case_id, expected) in enumerate(defs)
    ]


def _journey_cases(seed: int) -> list[dict]:
    defs = [
        ("journey-supply-chain", "supply_chain", "automatic"),
        ("journey-finance", "finance", "human_approved"),
        ("journey-credit", "credit", "rejected"),
    ]
    cases = []
    for i, (case_id, journey_id, risk_class) in enumerate(defs):
        fixture_hash = hashlib.sha256(f"{seed}:{case_id}:{journey_id}".encode("utf-8")).hexdigest()
        cases.append(
            _case(
                case_id,
                ["business_journey"],
                {"result": "governed_browser_loop_completed"},
                ["business_journey.three_journey_acceptance"],
                i,
                journey_id=journey_id,
                model_id=registry.JOURNEY_MODEL_ID,
                skip_allowed=False,
                fixture_manifest_sha256=fixture_hash,
                risk_class=risk_class,
            )
        )
    return cases


# ---------------------------------------------------------------------------
# Business journey corpora (test_data/runtime/{supply_chain,finance,credit}/)
#
# The three legacy `journey-*` cases above are a pre-existing, separately
# tested placeholder (backend/tests/runtime/test_registered_case_execution.py
# asserts `journey-supply-chain` is real_model_browser and excluded from the
# deterministic runner) and are left untouched. The functions below build the
# real, versioned, 15-case-per-journey corpus that `journey_registry.py`
# reads from test_data/runtime/<journey_id>/*.json. Each journey's own
# case_matrix.json keeps the bare case_id (so "edge-empty-result" can repeat
# once per journey without colliding there); its 14 deterministic cases are
# ALSO merged into this same generic manifest.json's `cases` array below
# (via `_journey_deterministic_cases_merged`) under a globally-unique
# f"{journey}-{case_id}" id, so the existing, unmodified
# run_registered_cases.py -- which only ever reads this shared manifest.json,
# never journey_registry.py's own namespace -- actually discovers and
# executes all 42 of them.
# ---------------------------------------------------------------------------

# (kind, media_type, repo-relative path, fixture_id) per journey. The image
# entry is a pre-rendered (not regenerated here) page-1 PNG derived from the
# corresponding PDF using this repository's own PDF-to-image tooling
# (app/services/v2/pipeline/steps/document_to_md.py's PyMuPDF renderer) --
# never pdftoppm, since this repository does not depend on Poppler.
JOURNEY_SOURCE_FILES: dict[str, list[tuple[str, str, str, str]]] = {
    "supply_chain": [
        ("tabular", "text/csv", "test_data/供应链/inventory_transactions.csv", "input-supply_chain-inventory-transactions-csv"),
        (
            "tabular",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "test_data/供应链/supplier_database.xlsx",
            "input-supply_chain-supplier-database-xlsx",
        ),
        (
            "document",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "test_data/供应链/procurement_policy.docx",
            "input-supply_chain-procurement-policy-docx",
        ),
        (
            "image",
            "image/png",
            "test_data/runtime/supply_chain/warehouse_management_page1.png",
            "input-supply_chain-warehouse-management-page1-png",
        ),
    ],
    "finance": [
        (
            "tabular",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "test_data/财务/financial_data.xlsx",
            "input-finance-financial-data-xlsx",
        ),
        ("tabular", "text/csv", "test_data/财务/cash_flow.csv", "input-finance-cash-flow-csv"),
        ("tabular", "text/csv", "test_data/财务/expense_reports.csv", "input-finance-expense-reports-csv"),
        (
            "document",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "test_data/财务/month_end_close.docx",
            "input-finance-month-end-close-docx",
        ),
        ("image", "image/png", "test_data/runtime/finance/audit_report_page1.png", "input-finance-audit-report-page1-png"),
    ],
    "credit": [
        ("tabular", "text/csv", "test_data/信贷/贷款申请记录.csv", "input-credit-loan-applications-csv"),
        ("tabular", "text/csv", "test_data/信贷/客户档案信息.csv", "input-credit-customer-profiles-csv"),
        ("tabular", "text/csv", "test_data/信贷/还款流水.csv", "input-credit-repayment-ledger-csv"),
        (
            "document",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "test_data/信贷/风控审批政策.docx",
            "input-credit-risk-approval-policy-docx",
        ),
        (
            "image",
            "image/png",
            "test_data/runtime/credit/贷后催收报告_page1.png",
            "input-credit-collection-report-page1-png",
        ),
    ],
}

# The original PDF each journey's rendered page-1 PNG is derived from, kept
# only as a hashed source_ref (never uploaded, never re-copied).
JOURNEY_PDF_SOURCE: dict[str, str] = {
    "supply_chain": "test_data/供应链/warehouse_management.pdf",
    "finance": "test_data/财务/audit_report.pdf",
    "credit": "test_data/信贷/贷后催收报告.pdf",
}

JOURNEY_CITATION_IDS: dict[str, list[str]] = {
    "supply_chain": ["input-supply_chain-inventory-transactions-csv", "input-supply_chain-procurement-policy-docx"],
    "finance": ["input-finance-expense-reports-csv", "input-finance-month-end-close-docx"],
    "credit": ["input-credit-loan-applications-csv", "input-credit-risk-approval-policy-docx"],
}

JOURNEY_NUMERIC_PREDICATES: dict[str, dict[str, float]] = {
    "supply_chain": {"safety_stock_threshold_units": 20, "below_safety_stock_supplier_count_min": 1},
    "finance": {"expense_budget_limit_cny": 50000, "duplicate_invoice_amount_tolerance_cny": 0.01},
    "credit": {"credit_score_limit": 600, "credit_limit_increase_cap_cny": 20000},
}

JOURNEY_DIALOGUE_QUESTIONS: dict[str, str] = {
    "supply_chain": "Which suppliers are below safety stock, and what purchase order action do you recommend?",
    "finance": "Which expenses exceed the accounting period budget, and what journal entry do you recommend?",
    "credit": "Which loan applications exceed the credit score limit, and what credit limit action do you recommend?",
}

# case_id -> the assertion text from the plan's case matrix table, used only
# as documentation inside `expected["assertion"]`.
JOURNEY_CASE_ASSERTIONS: dict[str, str] = {
    "normal-pipeline-release": (
        "Non-empty Pipeline, approved Curated rows, semantic minima, published release, MCP grant, "
        "browser-created Agent binding, cited answer, exactly one tool round, automatic Sandbox receipt, "
        "and high-risk plan available."
    ),
    "edge-empty-result": "No-match investigation is an explicit empty result with no invented entity or write.",
    "edge-duplicate-or-missing": (
        "Duplicate entities/invoices/applications are deduplicated; missing policy field is an explicit "
        "quality/semantic result."
    ),
    "security-no-grant": (
        "Missing, expired, revoked, cross-tenant, or unauthorized grant is denied without protected data or write."
    ),
    "security-stale-release": "Stale release/snapshot and caller/domain mismatch fail closed with no target mutation.",
    "security-prompt-injection": (
        "Prompt-injection text in an input document cannot change policy, expose data, or create an "
        "unapproved action."
    ),
    "resilience-timeout-retry": "One timeout retry succeeds or the second retry fails; no third attempt/logical call.",
    "resilience-429-retry": "One 429 retry succeeds or the second 429 fails; no third attempt/logical call.",
    "resilience-provider-error": "Non-retryable provider error has one HTTP attempt and a typed failure.",
    "resilience-mcp-timeout": "MCP timeout produces traceable failure and no duplicate write.",
    "resilience-sse-reconnect": "SSE disconnect/reconnect preserves one turn, one tool round, and idempotent durable state.",
    "writeback-automatic": "Low-risk reversible label/Sandbox action is automatic, audited, receipted, and reversible.",
    "writeback-hitl-approved": "Exact immutable plan is approved and writes only the isolated target with a receipt.",
    "writeback-hitl-rejected": "Rejection records the decision and leaves target before/after hashes identical.",
    "writeback-hitl-expired": "Expiry records the decision and leaves target before/after hashes identical.",
}

# case_id -> risk_class (JourneyCase.risk_class, distinct from the generic
# corpus's registry.RISK_CLASSES which has no "expired" tier).
_JOURNEY_CASE_RISK_CLASS: dict[str, str] = {
    "writeback-hitl-approved": "human_approved",
    "writeback-hitl-rejected": "rejected",
    "writeback-hitl-expired": "expired",
}


def _journey_source_hash(rel_path: str) -> str:
    return _sha256((REPO_ROOT / rel_path).read_bytes())


def build_journey_inputs(journey_id: str) -> dict:
    inputs = []
    input_hashes: dict[str, str] = {}
    source_refs = []
    for kind, media_type, rel_path, fixture_id in JOURNEY_SOURCE_FILES[journey_id]:
        sha = _journey_source_hash(rel_path)
        entry: dict[str, Any] = {
            "kind": kind,
            "media_type": media_type,
            "path": rel_path,
            "sha256": sha,
            "fixture_id": fixture_id,
        }
        if kind == "image":
            pdf_path = JOURNEY_PDF_SOURCE[journey_id]
            entry["derived_from"] = pdf_path
            entry["derived_from_sha256"] = _journey_source_hash(pdf_path)
        inputs.append(entry)
        input_hashes[fixture_id] = sha
        source_refs.append({"path": rel_path, "sha256": sha})

    pdf_path = JOURNEY_PDF_SOURCE[journey_id]
    source_refs.append({"path": pdf_path, "sha256": _journey_source_hash(pdf_path)})
    return {"inputs": inputs, "source_refs": source_refs, "input_hashes": input_hashes}


def build_journey_semantic_minima(journey_id: str) -> dict:
    minima = journey_registry.JOURNEY_MINIMA[journey_id]
    return {
        "entities": minima["entities"],
        "relations": minima["relations"],
        "rules": minima["rules"],
        "actions": minima["actions"],
        "keywords": minima["keywords"],
        "low_risk_action": minima["low_risk_action"],
        "high_risk_action": minima["high_risk_action"],
        "source_citation_ids": JOURNEY_CITATION_IDS[journey_id],
        "numeric_predicates": JOURNEY_NUMERIC_PREDICATES[journey_id],
    }


def build_journey_dialogues(journey_id: str) -> dict:
    minima = journey_registry.JOURNEY_MINIMA[journey_id]
    scenario = {
        "dialogue_id": f"{journey_id}-governed-dialogue",
        "question": JOURNEY_DIALOGUE_QUESTIONS[journey_id],
        "expected_keywords": minima["keywords"],
        "citation_source_ids": JOURNEY_CITATION_IDS[journey_id],
        "normal_outcome": {"decision": "ALLOW", "risk_class": "automatic", "action": minima["low_risk_action"]},
        "high_risk_outcome": {
            "decision": "ALLOW",
            "risk_class": "human_approved",
            "action": minima["high_risk_action"],
        },
    }
    return {"dialogue_scenarios": [scenario]}


def build_journey_governance(journey_id: str, seed: int) -> dict:
    def h(label: str) -> str:
        return hashlib.sha256(f"{seed}:{journey_id}:{label}".encode("utf-8")).hexdigest()

    automatic_before, automatic_after = h("automatic-before"), h("automatic-after")
    approved_before, approved_after = h("approved-before"), h("approved-after")
    rejected_hash = h("rejected-target")
    expired_hash = h("expired-target")

    governance_outcomes = [
        {
            "id": "automatic",
            "execution_class": "automatic",
            "expected_status": "succeeded",
            "target_before_hash": automatic_before,
            "target_after_hash": automatic_after,
            "must_write": True,
            "required_plan_hash": None,
        },
        {
            "id": "approved",
            "execution_class": "human_approved",
            "expected_status": "succeeded",
            "target_before_hash": approved_before,
            "target_after_hash": approved_after,
            "must_write": True,
            "required_plan_hash": h("approved-plan-hash"),
        },
        {
            "id": "rejected",
            "execution_class": "human_approved",
            "expected_status": "rejected",
            "target_before_hash": rejected_hash,
            "target_after_hash": rejected_hash,
            "must_write": False,
            "required_plan_hash": h("rejected-plan-hash"),
        },
        {
            "id": "expired",
            "execution_class": "human_approved",
            "expected_status": "expired",
            "target_before_hash": expired_hash,
            "target_after_hash": expired_hash,
            "must_write": False,
            "required_plan_hash": h("expired-plan-hash"),
        },
    ]
    plan_instances = [
        {
            "branch": "approved",
            "target_fixture_id": f"target-{journey_id}-approved",
            "expected_status": "succeeded",
            "must_write": True,
            "target_before_hash": approved_before,
            "target_after_hash": approved_after,
        },
        {
            "branch": "rejected",
            "target_fixture_id": f"target-{journey_id}-rejected",
            "expected_status": "rejected",
            "must_write": False,
            "target_before_hash": rejected_hash,
            "target_after_hash": rejected_hash,
        },
        {
            "branch": "expired",
            "target_fixture_id": f"target-{journey_id}-expired",
            "expected_status": "expired",
            "must_write": False,
            "target_before_hash": expired_hash,
            "target_after_hash": expired_hash,
        },
    ]
    return {"governance_outcomes": governance_outcomes, "plan_instances": plan_instances}


def build_journey_case_matrix(
    journey_id: str, journey_hash: str, governance_doc: dict
) -> dict:
    outcomes_by_id = {outcome["id"]: outcome for outcome in governance_doc["governance_outcomes"]}
    cases = []
    for case_id in journey_registry.CASE_IDS:
        risk_class = _JOURNEY_CASE_RISK_CLASS.get(case_id, "automatic")
        fixture_hashes: dict[str, str] = {"input_manifest_sha256": journey_hash}
        outcome_id = {
            "normal-pipeline-release": "automatic",
            "writeback-automatic": "automatic",
            "writeback-hitl-approved": "approved",
            "writeback-hitl-rejected": "rejected",
            "writeback-hitl-expired": "expired",
        }.get(case_id)
        if outcome_id is not None:
            outcome = outcomes_by_id[outcome_id]
            fixture_hashes["target_before_hash"] = outcome["target_before_hash"]
            fixture_hashes["target_after_hash"] = outcome["target_after_hash"]

        case_doc: dict[str, Any] = {
            "case_id": case_id,
            "journey_id": journey_id,
            "coverage": [f"business_journey.{journey_id}.{case_id.replace('-', '_')}"],
            "expected": {"assertion": JOURNEY_CASE_ASSERTIONS[case_id]},
            "layers": ["business_journey"],
            "fixture_manifest_sha256": journey_hash,
            "risk_class": risk_class,
            "fixture_hashes": fixture_hashes,
            "skip_allowed": False,
        }

        if case_id == journey_registry.NORMAL_CASE_ID:
            case_doc["execution_mode"] = "real_model_browser"
            case_doc["test_targets"] = [journey_registry.browser_target(journey_id).to_dict()]
        else:
            case_doc["execution_mode"] = "deterministic"
            case_doc["test_targets"] = [journey_registry.DETERMINISTIC_CASE_TARGETS[case_id].to_dict()]
            # This is also the shared, globally-unique case_id the same case
            # is merged into test_data/runtime/manifest.json under (see
            # registry.JOURNEY_DETERMINISTIC_TARGETS) -- the id actually
            # executed by run_registered_cases.py.
            case_doc["shared_case_id"] = registry.journey_case_id(journey_id, case_id)
            target_def = registry.JOURNEY_DETERMINISTIC_TARGET_DEFS[case_id]
            if target_def.target_status is not None:
                case_doc["target_status"] = target_def.target_status
                case_doc["proves"] = target_def.proves

        cases.append(case_doc)
    return {"cases": cases}


def build_journey_manifest_doc(journey_id: str, seed: int) -> dict:
    return {
        "schema_version": 1,
        "journey_id": journey_id,
        "fixture_version": JOURNEY_FIXTURE_VERSION,
        "seed": seed,
        "model_id": registry.JOURNEY_MODEL_ID,
        "logical_model_calls": 3,
        "max_tool_rounds_per_turn": 1,
        "max_http_attempts": 6,
    }


def build_journey_reproducibility(seed: int, journey_hash: str) -> dict:
    return {
        "seed": seed,
        "fixture_version": JOURNEY_FIXTURE_VERSION,
        "manifest_sha256": journey_hash,
        "canonical_serialization": "json.dumps(sort_keys=True, indent=2, ensure_ascii=False) + trailing newline",
        "generated_at": FIXED_NOW,
        "regenerate_command": f"python test_data/runtime/generate_runtime_fixtures.py --seed {seed} --output <dir>",
    }


def build_journey_corpus(journey_id: str, seed: int) -> dict[str, dict]:
    """Build the 7 JSON documents for one journey's fixture corpus."""

    inputs_doc = build_journey_inputs(journey_id)
    minima_doc = build_journey_semantic_minima(journey_id)
    dialogues_doc = build_journey_dialogues(journey_id)
    governance_doc = build_journey_governance(journey_id, seed)
    manifest_doc = build_journey_manifest_doc(journey_id, seed)
    # The hash is self-referential only through the two fields normalized by
    # compute_corpus_hash(), so placeholders are safe for the first build.
    case_matrix_doc = build_journey_case_matrix(journey_id, "<corpus-hash>", governance_doc)
    reproducibility_doc = build_journey_reproducibility(seed, "<corpus-hash>")
    journey_hash = journey_registry.compute_corpus_hash(
        manifest_doc, inputs_doc, minima_doc, dialogues_doc, governance_doc, case_matrix_doc, reproducibility_doc
    )
    case_matrix_doc = build_journey_case_matrix(journey_id, journey_hash, governance_doc)
    reproducibility_doc = build_journey_reproducibility(seed, journey_hash)

    return {
        f"{journey_id}/manifest.json": manifest_doc,
        f"{journey_id}/inputs.json": inputs_doc,
        f"{journey_id}/semantic_minima.json": minima_doc,
        f"{journey_id}/dialogues.json": dialogues_doc,
        f"{journey_id}/governance.json": governance_doc,
        f"{journey_id}/case_matrix.json": case_matrix_doc,
        f"{journey_id}/reproducibility.json": reproducibility_doc,
    }


def build_all_journey_corpora(seed: int) -> dict[str, dict]:
    files: dict[str, dict] = {}
    for journey_id in journey_registry.JOURNEY_IDS:
        files.update(build_journey_corpus(journey_id, seed))
    return files


def _journey_deterministic_cases_merged() -> list[dict]:
    """Merge each journey's 14 deterministic cases into this shared
    manifest.json's own case namespace as f"{journey}-{case_id}", so the
    existing, unmodified run_registered_cases.py naturally discovers and
    executes all 42 of them -- Task 1 does not add a second runner. This is
    the same target (and the same target_status/proves disclosure for the 6
    cases with no fully-proving existing test) each journey's own
    case_matrix.json records under the bare case_id; see
    registry.JOURNEY_DETERMINISTIC_TARGET_DEFS for the full mapping.
    """

    cases = []
    i = 0
    for journey_id in journey_registry.JOURNEY_IDS:
        for case_id, target_def in registry.JOURNEY_DETERMINISTIC_TARGET_DEFS.items():
            qualified = registry.journey_case_id(journey_id, case_id)
            extra: dict[str, Any] = {"journey_id": journey_id, "source_case_id": case_id}
            if target_def.target_status is not None:
                extra["target_status"] = target_def.target_status
                extra["proves"] = target_def.proves
            cases.append(
                _case(
                    qualified,
                    ["business_journey_deterministic"],
                    {"assertion": JOURNEY_CASE_ASSERTIONS[case_id]},
                    [f"business_journey.{journey_id}.{case_id.replace('-', '_')}"],
                    i,
                    **extra,
                )
            )
            i += 1
    return cases


def build_case_defs(seed: int) -> list[dict]:
    cases = (
        _snapshot_cases()
        + _identity_cases()
        + _runtime_cases()
        + _execution_cases()
        + _parity_cases()
        + _refresh_cases()
        + _database_cases()
        + _journey_cases(seed)
        + _journey_deterministic_cases_merged()
    )
    seen: set[str] = set()
    for case in cases:
        case_id = case["case_id"]
        if case_id in seen:
            raise ValueError(f"duplicate case_id in catalog: {case_id!r}")
        seen.add(case_id)
    return cases


def _finalize_case(case: dict) -> dict:
    """Stamp execution_mode and test_targets from the registry, and validate."""

    case_id = case["case_id"]
    targets = registry.targets_for(case_id)
    case = dict(case)
    case["execution_mode"] = "real_model_browser" if "business_journey" in case["layers"] else "deterministic"
    case["test_targets"] = [t.to_dict() for t in targets]
    for field in REQUIRED_CASE_FIELDS:
        if not case.get(field):
            raise ValueError(f"generated case {case_id!r} is missing required field {field!r}")
    registry.assert_case_registry(case)
    return case


# ---------------------------------------------------------------------------
# Fixture content files (underlying synthetic data per case, by layer)
# ---------------------------------------------------------------------------


def _fixture_records(cases: list[dict], layer: str) -> dict:
    records = {}
    for case in cases:
        if layer not in case["layers"]:
            continue
        record = {
            "case_id": case["case_id"],
            "tenant_id": case["tenant_id"],
            "user_id": case["user_id"],
            "expected": case["expected"],
        }
        if layer == "refresh":
            for field in ("refresh_mode", "source_contract", "cursor_outcome", "lineage_outcome", "cancel_outcome", "already_terminal", "error_code"):
                if field in case:
                    record[field] = case[field]
        records[case["case_id"]] = record
    return records


def build_fixture_files(cases: list[dict]) -> dict[str, dict]:
    return {
        "fixtures/snapshots.json": {"cases": _fixture_records(cases, "snapshot")},
        "fixtures/identities.json": {"cases": _fixture_records(cases, "identity")},
        "fixtures/runtime_cases.json": {"cases": _fixture_records(cases, "runtime")},
        "fixtures/execution_cases.json": {
            "cases": {
                **_fixture_records(cases, "execution"),
                **_fixture_records(cases, "database"),
            }
        },
        "fixtures/transport_cases.json": {"cases": _fixture_records(cases, "parity")},
        "fixtures/refresh_cases.json": {"cases": _fixture_records(cases, "refresh")},
    }


def build_playwright_seed() -> dict:
    return {
        "schedule": {
            "source_id": "source-acme-erp",
            "cron_expr": "0 2 * * *",
            "timezone": "Asia/Shanghai",
            "next_due_at": "2026-08-27T02:00:00Z",
        },
        "cursor": {
            "source_id": "source-acme-erp",
            "watermark": "2026-08-25T23:00:00Z",
            "primary_key": "row-acme-000042",
        },
        "config_version": {"source_id": "source-acme-erp", "version": 3},
        "lag": {"source_id": "source-acme-erp", "source_lag_seconds": 42},
        "cancellation": {
            "requested": {"run_id": "run-acme-000101", "status": "cancel_requested"},
            "cancelled": {"run_id": "run-acme-000102", "status": "cancelled"},
            "already_terminal": {
                "run_id": "run-acme-000103",
                "status": "succeeded",
                "already_terminal": True,
            },
        },
        "dead_letter": {"dead_letter_id": "dlq-acme-000001", "reason": "max_retries_exceeded"},
        "replay": {"replay_run_id": "run-acme-000104", "source_dead_letter_id": "dlq-acme-000001"},
        "configuration_drift": {"run_id": "run-acme-000105", "error_code": "CONFIGURATION_DRIFT"},
    }


# ---------------------------------------------------------------------------
# Database (PostgreSQL / MySQL) fixture SQL and Compose file
# ---------------------------------------------------------------------------


def build_postgres_schema() -> str:
    return """-- Synthetic PostgreSQL schema for the runtime fixture corpus.
-- Test infrastructure only. Never used against a production database.

CREATE TABLE managed_targets (
    target_id VARCHAR(64) PRIMARY KEY,
    tenant_id VARCHAR(32) NOT NULL,
    status VARCHAR(32) NOT NULL,
    row_version INTEGER NOT NULL DEFAULT 1,
    updated_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE refresh_source_rows (
    row_id VARCHAR(64) PRIMARY KEY,
    source_id VARCHAR(64) NOT NULL,
    resource VARCHAR(64) NOT NULL,
    watermark TIMESTAMPTZ NOT NULL,
    sequence_no INTEGER NOT NULL,
    payload_summary VARCHAR(128) NOT NULL
);

CREATE TABLE refresh_event_log (
    event_id VARCHAR(64) PRIMARY KEY,
    source_id VARCHAR(64) NOT NULL,
    resource VARCHAR(64) NOT NULL,
    cursor_value VARCHAR(64) NOT NULL,
    sequence_no INTEGER NOT NULL,
    received_at TIMESTAMPTZ NOT NULL
);
"""


def build_postgres_seed() -> str:
    return """-- Synthetic PostgreSQL seed rows. Test infrastructure only.

INSERT INTO managed_targets (target_id, tenant_id, status, row_version, updated_at) VALUES
    ('target-acme-000001', 'tenant-acme', 'active', 1, '2026-08-26T00:00:00Z'),
    ('target-beta-000001', 'tenant-beta', 'active', 1, '2026-08-26T00:00:00Z');

INSERT INTO refresh_source_rows (row_id, source_id, resource, watermark, sequence_no, payload_summary) VALUES
    ('row-acme-000001', 'source-acme-erp', 'purchase_orders', '2026-08-25T22:00:00Z', 1, 'normal batch row'),
    ('row-acme-000002', 'source-acme-erp', 'purchase_orders', '2026-08-25T23:00:00Z', 2, 'late-arriving row'),
    ('row-beta-000001', 'source-beta-crm', 'accounts', '2026-08-25T22:30:00Z', 1, 'normal batch row');

INSERT INTO refresh_event_log (event_id, source_id, resource, cursor_value, sequence_no, received_at) VALUES
    ('event-acme-000001', 'source-acme-erp', 'purchase_orders', 'cursor-acme-000001', 1, '2026-08-25T22:05:00Z'),
    ('event-beta-000001', 'source-beta-crm', 'accounts', 'cursor-beta-000001', 1, '2026-08-25T22:35:00Z');
"""


def build_mysql_schema() -> str:
    return """-- Synthetic MySQL schema for the runtime fixture corpus.
-- Test infrastructure only. Never used against a production database.

CREATE TABLE managed_targets (
    target_id VARCHAR(64) PRIMARY KEY,
    tenant_id VARCHAR(32) NOT NULL,
    status VARCHAR(32) NOT NULL,
    row_version INT NOT NULL DEFAULT 1,
    updated_at DATETIME NOT NULL
) ENGINE=InnoDB;

CREATE TABLE refresh_source_rows (
    row_id VARCHAR(64) PRIMARY KEY,
    source_id VARCHAR(64) NOT NULL,
    resource VARCHAR(64) NOT NULL,
    watermark DATETIME NOT NULL,
    sequence_no INT NOT NULL,
    payload_summary VARCHAR(128) NOT NULL
) ENGINE=InnoDB;

CREATE TABLE refresh_event_log (
    event_id VARCHAR(64) PRIMARY KEY,
    source_id VARCHAR(64) NOT NULL,
    resource VARCHAR(64) NOT NULL,
    cursor_value VARCHAR(64) NOT NULL,
    sequence_no INT NOT NULL,
    received_at DATETIME NOT NULL
) ENGINE=InnoDB;
"""


def build_mysql_seed() -> str:
    return """-- Synthetic MySQL seed rows. Test infrastructure only.

INSERT INTO managed_targets (target_id, tenant_id, status, row_version, updated_at) VALUES
    ('target-acme-000001', 'tenant-acme', 'active', 1, '2026-08-26 00:00:00'),
    ('target-beta-000001', 'tenant-beta', 'active', 1, '2026-08-26 00:00:00');

INSERT INTO refresh_source_rows (row_id, source_id, resource, watermark, sequence_no, payload_summary) VALUES
    ('row-acme-000001', 'source-acme-erp', 'purchase_orders', '2026-08-25 22:00:00', 1, 'normal batch row'),
    ('row-acme-000002', 'source-acme-erp', 'purchase_orders', '2026-08-25 23:00:00', 2, 'late-arriving row'),
    ('row-beta-000001', 'source-beta-crm', 'accounts', '2026-08-25 22:30:00', 1, 'normal batch row');

INSERT INTO refresh_event_log (event_id, source_id, resource, cursor_value, sequence_no, received_at) VALUES
    ('event-acme-000001', 'source-acme-erp', 'purchase_orders', 'cursor-acme-000001', 1, '2026-08-25 22:05:00'),
    ('event-beta-000001', 'source-beta-crm', 'accounts', 'cursor-beta-000001', 1, '2026-08-25 22:35:00');
"""


def build_docker_compose() -> str:
    return """# Isolated test-only PostgreSQL/MySQL fixtures for the runtime corpus.
# These ports, volumes, and credentials are test infrastructure and are
# never used against production. Always run with a unique -p <project>
# and tear down with `down -v --remove-orphans`.
name: runtime-fixture

networks:
  runtime_fixture:
    driver: bridge

services:
  postgres:
    image: postgres:16
    environment:
      POSTGRES_USER: runtime
      POSTGRES_PASSWORD: runtime
      POSTGRES_DB: runtime
    ports:
      - "55432:5432"
    networks:
      - runtime_fixture
    volumes:
      - ./postgres/schema.sql:/docker-entrypoint-initdb.d/01-schema.sql:ro
      - ./postgres/seed.sql:/docker-entrypoint-initdb.d/02-seed.sql:ro
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U runtime -d runtime"]
      interval: 5s
      timeout: 5s
      retries: 10

  mysql:
    image: mysql:8
    environment:
      MYSQL_ROOT_PASSWORD: runtime
      MYSQL_USER: runtime
      MYSQL_PASSWORD: runtime
      MYSQL_DATABASE: runtime
    ports:
      - "53306:3306"
    networks:
      - runtime_fixture
    volumes:
      - ./mysql/schema.sql:/docker-entrypoint-initdb.d/01-schema.sql:ro
      - ./mysql/seed.sql:/docker-entrypoint-initdb.d/02-seed.sql:ro
    healthcheck:
      test: ["CMD-SHELL", "mysqladmin ping -h 127.0.0.1 -u runtime -pruntime"]
      interval: 5s
      timeout: 5s
      retries: 10
"""


def build_readme(seed: int) -> str:
    return f"""# test_data/runtime/

Generated, deterministic runtime fixture corpus for the Agent Semantic
Infrastructure implementation plan
(`docs/superpowers/plans/2026-08-26-agent-semantic-infrastructure-implementation.md`).
Regenerate with:

```
python test_data/runtime/generate_runtime_fixtures.py --seed {seed} --output <dir>
python test_data/runtime/generate_runtime_fixtures.py --seed {seed} --output <dir> --check
```

`--check` regenerates into a temporary directory and compares the resulting
file hashes against `manifest.json` in `<dir>`, so a stale committed copy
fails loudly instead of silently drifting from the generator.

## Layout

- `manifest.json` -- the case registry: every case's `case_id`, `expected`
  outcome, `coverage` tags, `layers`, `execution_mode`, and exactly one
  `test_targets` descriptor (see `registry.py`).
- `fixtures/{{snapshots,identities,runtime_cases,execution_cases,transport_cases,refresh_cases}}.json`
  -- the synthetic records each case's layer is built from. Refresh records
  repeat their `refresh_mode`, source/cursor contract, cursor and lineage
  outcomes, and cancellation/already-terminal metadata so fixture consumers
  cannot silently drop the no-progress contract.
- `playwright_seed.json` -- static state (schedule/cursor/config-version/lag,
  cancellation-requested/cancelled/already-terminal, dead-letter/replay/
  configuration-drift) that a later task's Playwright governance specs seed
  the UI from.
- `db/docker-compose.yml`, `db/postgres/*.sql`, `db/mysql/*.sql` -- isolated,
  disposable PostgreSQL 16 / MySQL 8 fixtures (see below). Never production.
- `registry.py` -- `TestTarget`, `targets_for`, `target_is_executable`, and
  `assert_case_registry`: the authoritative case-id-to-test mapping.
- `generate_runtime_fixtures.py` -- this generator (`generate`,
  `validate_manifest`, CLI).
- `test_fixture_manifest.py` -- the generation/registry/no-secret contract
  tests for this corpus.

## Synthetic identifiers

All fixtures use only two synthetic tenants, `tenant-acme` and `tenant-beta`,
synthetic users (`user-<tenant>-operator-<NN>`), and synthetic row/target/
source IDs (e.g. `row-acme-000001`, `target-beta-000001`,
`source-acme-erp`). No fixture contains a real name, email, phone number,
credential, or production hostname; `test_no_pii_or_real_secret` in
`test_fixture_manifest.py` scans every generated byte to enforce this. The
only allowed sentinels are the literal test-only values `runtime/runtime`
(database credentials, isolated to `db/docker-compose.yml`),
`vault:runtime-db`, and the `example.invalid` email domain.

## Isolated database fixtures

`db/docker-compose.yml` publishes PostgreSQL on host port `55432` and MySQL
on host port `53306`, both on the project-scoped `runtime_fixture` network,
both with healthchecks. These ports, the `runtime/runtime` credential, and
the seeded rows are disposable test infrastructure only -- never point them
at a production database. Integration jobs must use a unique Compose
`-p <project>` per run, must refuse to start if a reserved port is already
occupied, and must always tear down with `down -v --remove-orphans`, even on
failure.

Both dialects define the same logical `managed_targets` table (primary key
`target_id`, writable field `status`, optimistic-lock field `row_version`),
plus `refresh_source_rows` and `refresh_event_log`, seeded with stable
source/resource/cursor/sequence rows for `source-acme-erp` and
`source-beta-crm`.

## Simplified cancellation state machine

Refresh runs (`RefreshRun.status`) move through
`queued -> running -> cancel_requested -> cancelled`, or terminate as
`succeeded`, `failed`, or `dead_lettered`. There is **no**
`outcome_committed_at` commit marker anywhere in this contract:

- A `queued` run cancels immediately to terminal `cancelled`.
- A `running` run records `cancel_requested_at`/`cancel_requested_by`/
  `cancel_reason` and moves to `cancel_requested`; the worker transitions it
  to terminal `cancelled` the next time it reaches a **safe point** -- a
  connector page boundary or the instant before tentative DatasetVersion/
  PipelineRun materialization -- with the source cursor and lineage left
  exactly as they were (the no-progress invariant: cancellation never
  advances a cursor and never creates partial lineage).
- If the run's outcome transaction commits *before* a cancellation is
  observed, the run simply finishes `succeeded`/`failed`/`dead_lettered`. A
  cancellation request that arrives after that point makes **no mutation**
  and returns the run with `already_terminal: true` -- this is a plain,
  expected result, never an error and never reported as a successful
  cancellation.

`cancel-before-pull`, `cancel-inflight-page`, and
`cancel-after-tentative-materialization` in `manifest.json` exercise the
three safe points above; each has `cancel_outcome: "cancelled"` and
`cursor_outcome: "unchanged"`.

## Business journey corpora

`supply_chain/`, `finance/`, and `credit/` are separate, versioned fixture
corpora for the real-model business journey acceptance plan
(`docs/superpowers/plans/2026-08-27-real-model-business-journeys-implementation.md`).
Each journey directory holds seven files: `manifest.json` (schema/seed/model
scalars), `inputs.json` (hashed, repository-relative references to existing
`test_data/供应链|财务|信贷/` source files plus one deterministically rendered
PDF-page-1 PNG -- never a copy of source bytes), `semantic_minima.json`
(required entities/relations/rules/actions/keywords), `dialogues.json` (one
governed dialogue with a normal and a high-risk outcome), `governance.json`
(the `automatic`/`approved`/`rejected`/`expired` outcomes and their three
`plan_instances`), `case_matrix.json` (the 15 exact cases from the plan's
case matrix table), and `reproducibility.json` (the seed and a canonical
content hash). `journey_registry.py` -- a module that imports from
`registry.py` (never the reverse, to avoid an import cycle) -- is the
authoritative loader/validator (`load_journey_manifest`,
`validate_journey_manifest`, `journey_cases`, `assert_journey_case`) for this
corpus; case IDs like `edge-empty-result` repeat once per journey in each
journey's own `case_matrix.json` by design. Only each journey's
`normal-pipeline-release` case is `execution_mode: "real_model_browser"`;
the other 14 are deterministic (see `registry.py`'s
`JOURNEY_DETERMINISTIC_TARGET_DEFS` for the full mapping -- eight reuse the
closest already-passing pytest node that proves the same governed-runtime
property, and six have dedicated, journey-specific tests in
`test_journey_case_contracts.py` that each prove exactly their case's
claim). Every one of these 42 deterministic cases (14 x 3
journeys) is ALSO merged into this shared `manifest.json`'s own `cases`
array under the globally-unique id ``"<journey-with-hyphens>-<case_id>"``
(e.g. `supply-chain-edge-empty-result`) -- see
`registry.JOURNEY_DETERMINISTIC_TARGETS`/`journey_case_id` -- so the
existing, unmodified `run_registered_cases.py` (which only ever reads this
manifest.json) actually discovers and executes them; Task 1 does not add a
second runner. The rendered PNGs are produced once with this repository's
own PyMuPDF-based PDF-to-image renderer
(`app/services/v2/pipeline/steps/document_to_md.py`), not regenerated by
this script, and are hashed like any other static input.

### The real gate (Task 5)

The single `normal-pipeline-release` case per journey is exercised for real
by `business-journey-real-gate` in `.github/workflows/agent-mvp.yml`, driven
by `scripts/run_business_journey_gate.sh`. That gate runs, in strict order:
this directory's fixture generation/check, the deterministic registry above
(zero model calls), `python -m evals.business_journeys.run --phase prepare
--journey all` (real DeepSeek), the exact three strict Playwright specs in
`frontend/src/test/e2e/business-journeys.spec.ts`, `--phase verify --journey
all` (read-only), and `python -m evals.business_journeys.artifacts scan`
(sanitizes staged evidence or fails closed with a fixed-schema failure
summary -- never raw browser/model content). It only runs on trusted,
same-repository pull requests (never `pull_request_target`, never a fork)
and requires the repository secret `DEEPSEEK_API_KEY`; the backend process
it starts also requires `BUSINESS_JOURNEY_ACCEPTANCE_ENABLED=true`
(`backend/app/config.py`), which the script sets in a scratch `.env` for its
one disposable Compose stack.

The `--phase prepare` step's model-call evidence (`POST /api/v1/business-
journeys/preparations`) is self-reported: the eval harness, not the
application server, is the one that actually calls DeepSeek, so the server
can only validate that the reported counters are plausibly shaped, not
independently prove a real completion produced them. See
`app.routers.business_journeys`'s own module docstring for the full
disclosure of what this endpoint's trust model actually is.
"""


# ---------------------------------------------------------------------------
# Canonical serialization and manifest assembly
# ---------------------------------------------------------------------------


def _canonical_json(data: dict) -> bytes:
    return (json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def generate(seed: int, output_dir: Path) -> dict:
    """Deterministically generate the full runtime fixture corpus.

    Writes README.md, manifest.json, fixtures/*.json, playwright_seed.json,
    and db/{docker-compose.yml,postgres/*.sql,mysql/*.sql} under
    ``output_dir``, and returns the manifest dict (which includes a
    ``files`` map of relative path -> sha256 of the written bytes).
    """

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "fixtures").mkdir(parents=True, exist_ok=True)
    (output_dir / "db" / "postgres").mkdir(parents=True, exist_ok=True)
    (output_dir / "db" / "mysql").mkdir(parents=True, exist_ok=True)
    for journey_id in journey_registry.JOURNEY_IDS:
        (output_dir / journey_id).mkdir(parents=True, exist_ok=True)

    case_defs = build_case_defs(seed)
    cases = [_finalize_case(case) for case in case_defs]
    cases.sort(key=lambda c: c["case_id"])

    text_files: dict[str, str] = {
        "README.md": build_readme(seed),
        "db/docker-compose.yml": build_docker_compose(),
        "db/postgres/schema.sql": build_postgres_schema(),
        "db/postgres/seed.sql": build_postgres_seed(),
        "db/mysql/schema.sql": build_mysql_schema(),
        "db/mysql/seed.sql": build_mysql_seed(),
    }
    json_files: dict[str, dict] = {
        **build_fixture_files(cases),
        "playwright_seed.json": build_playwright_seed(),
        **build_all_journey_corpora(seed),
    }

    files: dict[str, str] = {}
    for rel_path, content in text_files.items():
        data = (content if content.endswith("\n") else content + "\n").encode("utf-8")
        (output_dir / rel_path).write_bytes(data)
        files[rel_path] = _sha256(data)
    for rel_path, content in json_files.items():
        data = _canonical_json(content)
        (output_dir / rel_path).write_bytes(data)
        files[rel_path] = _sha256(data)

    # `files` intentionally excludes manifest.json itself: a manifest cannot
    # durably record its own hash (writing the hash would change the bytes,
    # which would change the hash). validate_manifest() and --check only
    # need to verify the *other* generated files against this map.
    manifest = {
        "manifest_version": MANIFEST_VERSION,
        "seed": seed,
        "generated_at": FIXED_NOW,
        "dialects": sorted(registry.REQUIRED_DIALECTS),
        "cases": cases,
        "files": files,
    }
    manifest_bytes = _canonical_json(manifest)
    (output_dir / "manifest.json").write_bytes(manifest_bytes)

    # Return exactly what was written to disk.
    return json.loads(manifest_bytes.decode("utf-8"))


def validate_manifest(output_dir: Path) -> None:
    """Verify manifest.json in ``output_dir`` matches the files on disk."""

    output_dir = Path(output_dir)
    manifest_path = output_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert manifest["manifest_version"] == MANIFEST_VERSION
    assert manifest["cases"], "manifest has no cases"

    for case in manifest["cases"]:
        for field in REQUIRED_CASE_FIELDS:
            assert case.get(field), f"case {case.get('case_id')!r} missing required field {field!r}"
        registry.assert_case_registry(case)

    for rel_path, expected_hash in manifest["files"].items():
        actual = _sha256((output_dir / rel_path).read_bytes())
        assert actual == expected_hash, f"{rel_path} does not match manifest hash"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--check", action="store_true", help="verify reproducibility against --output")
    args = parser.parse_args(argv)

    if args.check:
        with tempfile.TemporaryDirectory() as tmp:
            fresh = generate(args.seed, Path(tmp))
        existing = json.loads((args.output / "manifest.json").read_text(encoding="utf-8"))
        errors: list[str] = []
        if fresh["files"] != existing["files"]:
            errors.append("fixture files differ from manifest.json's files map")
        if fresh["seed"] != existing["seed"]:
            errors.append(f"seed differs: fresh={fresh['seed']!r} checked-in={existing['seed']!r}")
        # generated_at is intentionally excluded from this comparison. Today
        # it is `FIXED_NOW`, a hardcoded constant, so it never actually
        # drifts — but it is conceptually a generation timestamp, and a
        # future change that makes it real wall-clock time would otherwise
        # make --check spuriously fail on every run. seed/cases/files are
        # the actual content-integrity signals this check exists for.
        if fresh["cases"] != existing["cases"]:
            fresh_by_id = {case["case_id"]: case for case in fresh["cases"]}
            existing_by_id = {case["case_id"]: case for case in existing["cases"]}
            missing = sorted(set(fresh_by_id) - set(existing_by_id))
            extra = sorted(set(existing_by_id) - set(fresh_by_id))
            drifted = sorted(
                case_id for case_id in (set(fresh_by_id) & set(existing_by_id))
                if fresh_by_id[case_id] != existing_by_id[case_id]
            )
            if missing:
                errors.append(f"manifest.json is missing cases a fresh generation produces: {missing}")
            if extra:
                errors.append(f"manifest.json has cases a fresh generation does not produce: {extra}")
            if drifted:
                errors.append(f"case content (e.g. 'expected') differs from a fresh generation: {drifted}")
            if not missing and not extra and not drifted:
                # Same set of case_ids with identical content each, but
                # fresh["cases"] != existing["cases"] still triggered this
                # block — the only remaining possibility is a pure ordering
                # difference, which none of the checks above catch.
                errors.append("case ordering differs from the checked-in manifest")
        if errors:
            for error in errors:
                print(f"runtime fixture generation is not reproducible: {error}", file=sys.stderr)
            return 1
        validate_manifest(args.output)
        print(f"OK: {args.output} matches a fresh generation with seed {args.seed}")
        return 0

    generate(args.seed, args.output)
    print(f"generated runtime fixture corpus at {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

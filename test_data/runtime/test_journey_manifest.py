"""Contract tests for the three business-journey fixture corpora.

Run with: python -m pytest test_data/runtime/test_journey_manifest.py -q
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from journey_registry import (  # noqa: E402
    CASE_IDS,
    JOURNEY_IDS,
    NORMAL_CASE_ID,
    assert_journey_case,
    journey_cases,
    load_journey_manifest,
    validate_journey_manifest,
)

ROOT = Path("test_data/runtime")


def test_three_manifests_have_multimodal_inputs_and_governance_states():
    for journey_id in ("supply_chain", "finance", "credit"):
        manifest = load_journey_manifest(journey_id, ROOT)
        assert manifest.model_id == "deepseek-v4-flash-vision-exp"
        assert {part["kind"] for part in manifest.inputs} >= {"tabular", "document", "image"}
        assert {item["id"] for item in manifest.governance_outcomes} == {
            "automatic",
            "approved",
            "rejected",
            "expired",
        }


def test_every_case_has_exactly_one_non_optional_target():
    for journey_id in ("supply_chain", "finance", "credit"):
        for case in journey_cases(journey_id):
            assert case["skip_allowed"] is False
            assert case["execution_mode"] in {"deterministic", "real_model_browser"}
            assert len(case["test_targets"]) == 1
            assert case["test_targets"][0]["kind"] in {"pytest", "playwright"}


# --- Additional coverage beyond the plan's minimum given tests --------------


def test_each_journey_has_the_exact_15_case_ids():
    for journey_id in JOURNEY_IDS:
        case_ids = [case["case_id"] for case in journey_cases(journey_id)]
        assert set(case_ids) == set(CASE_IDS)
        assert len(case_ids) == len(CASE_IDS) == 15


def test_only_normal_pipeline_release_is_real_model_browser():
    for journey_id in JOURNEY_IDS:
        for case in journey_cases(journey_id):
            if case["case_id"] == NORMAL_CASE_ID:
                assert case["execution_mode"] == "real_model_browser"
                assert case["test_targets"][0]["kind"] == "playwright"
            else:
                assert case["execution_mode"] == "deterministic"
                assert case["test_targets"][0]["kind"] == "pytest"


def test_normal_case_points_at_its_own_journeys_exact_playwright_title():
    titles = set()
    for journey_id in JOURNEY_IDS:
        case = next(c for c in journey_cases(journey_id) if c["case_id"] == NORMAL_CASE_ID)
        target = case["test_targets"][0]
        assert target["path"] == "frontend/src/test/e2e/business-journeys.spec.ts"
        assert journey_id.replace("_", " ") in target["selector"] or journey_id.replace("_", "-") in target["selector"]
        titles.add(target["selector"])
    # Each journey's normal case uses a distinct title -- never shared/reused.
    assert len(titles) == 3


def test_all_cases_pass_assert_journey_case():
    for journey_id in JOURNEY_IDS:
        for case in journey_cases(journey_id):
            assert_journey_case(case)


def test_load_journey_manifest_rejects_unknown_journey():
    with pytest.raises(ValueError):
        load_journey_manifest("unknown_journey", ROOT)


def test_load_journey_manifest_rejects_wrong_model_id(tmp_path):
    import json
    import shutil

    journey_id = "supply_chain"
    dest = tmp_path / journey_id
    shutil.copytree(ROOT / journey_id, dest)
    manifest_path = dest / "manifest.json"
    doc = json.loads(manifest_path.read_text(encoding="utf-8"))
    doc["model_id"] = "some-other-model"
    manifest_path.write_text(json.dumps(doc), encoding="utf-8")

    with pytest.raises(ValueError):
        load_journey_manifest(journey_id, tmp_path)


def test_load_journey_manifest_rejects_non_sha256_hash(tmp_path):
    import json
    import shutil

    journey_id = "finance"
    dest = tmp_path / journey_id
    shutil.copytree(ROOT / journey_id, dest)
    inputs_path = dest / "inputs.json"
    doc = json.loads(inputs_path.read_text(encoding="utf-8"))
    doc["inputs"][0]["sha256"] = "not-a-sha256-hash"
    inputs_path.write_text(json.dumps(doc), encoding="utf-8")

    with pytest.raises(ValueError):
        load_journey_manifest(journey_id, tmp_path)


def test_load_journey_manifest_rejects_non_repository_source(tmp_path):
    import json
    import shutil

    journey_id = "credit"
    dest = tmp_path / journey_id
    shutil.copytree(ROOT / journey_id, dest)
    inputs_path = dest / "inputs.json"
    doc = json.loads(inputs_path.read_text(encoding="utf-8"))
    doc["inputs"][0]["path"] = "/etc/passwd"
    inputs_path.write_text(json.dumps(doc), encoding="utf-8")

    with pytest.raises(ValueError):
        load_journey_manifest(journey_id, tmp_path)


def test_load_journey_manifest_rejects_non_reproducible_manifest(tmp_path):
    import json
    import shutil

    journey_id = "supply_chain"
    dest = tmp_path / journey_id
    shutil.copytree(ROOT / journey_id, dest)
    reproducibility_path = dest / "reproducibility.json"
    doc = json.loads(reproducibility_path.read_text(encoding="utf-8"))
    doc["manifest_sha256"] = "0" * 64
    reproducibility_path.write_text(json.dumps(doc), encoding="utf-8")

    with pytest.raises(ValueError):
        load_journey_manifest(journey_id, tmp_path)


def test_validate_journey_manifest_passes_for_every_journey():
    for journey_id in JOURNEY_IDS:
        manifest = load_journey_manifest(journey_id, ROOT)
        validate_journey_manifest(manifest)


def test_semantic_minima_match_the_plan_exactly():
    from journey_registry import JOURNEY_MINIMA

    expected = {
        "supply_chain": {
            "entities": ["Supplier", "PurchaseOrder", "InventoryItem", "Warehouse"],
            "relations": ["SUPPLIES", "PLACED_WITH", "CONTAINS", "BELOW_SAFETY_STOCK"],
            "rules": ["inventory_below_safety_stock"],
            "actions": ["risk_label", "purchase_order_price_update"],
            "keywords": ["supplier", "inventory", "safety stock", "purchase order"],
            "low_risk_action": "risk_label",
            "high_risk_action": "purchase_order_price_update",
        },
        "finance": {
            "entities": ["Account", "Invoice", "Expense", "CostCenter", "AccountingPeriod"],
            "relations": ["POSTED_TO", "BELONGS_TO", "DUPLICATES", "EXCEEDS_BUDGET"],
            "rules": ["duplicate_invoice", "expense_over_budget"],
            "actions": ["risk_label", "journal_entry"],
            "keywords": ["invoice", "expense", "accounting period", "cash flow"],
            "low_risk_action": "risk_label",
            "high_risk_action": "journal_entry",
        },
        "credit": {
            "entities": ["Borrower", "LoanApplication", "Repayment", "CreditLine", "RiskAssessment"],
            "relations": ["APPLIES_FOR", "HAS_REPAYMENT", "ASSESSED_AS", "USES_CREDIT_LINE"],
            "rules": ["credit_score_limit"],
            "actions": ["risk_label", "credit_limit_update"],
            "keywords": ["borrower", "application", "repayment", "credit limit"],
            "low_risk_action": "risk_label",
            "high_risk_action": "credit_limit_update",
        },
    }
    assert JOURNEY_MINIMA == expected
    for journey_id in JOURNEY_IDS:
        manifest = load_journey_manifest(journey_id, ROOT)
        for field in ("entities", "relations", "rules", "actions", "keywords", "low_risk_action", "high_risk_action"):
            assert manifest.semantic_minima[field] == expected[journey_id][field]


def test_high_risk_plan_fixtures_use_distinct_targets_and_identical_reject_expire_hashes():
    for journey_id in JOURNEY_IDS:
        manifest = load_journey_manifest(journey_id, ROOT)
        assert len(manifest.plan_instances) == 3
        branches = {p["branch"]: p for p in manifest.plan_instances}
        assert set(branches) == {"approved", "rejected", "expired"}
        target_ids = {p["target_fixture_id"] for p in manifest.plan_instances}
        assert len(target_ids) == 3, "each plan instance must use a distinct target_fixture_id"
        assert branches["approved"]["must_write"] is True
        assert branches["approved"]["target_before_hash"] != branches["approved"]["target_after_hash"]
        for branch in ("rejected", "expired"):
            assert branches[branch]["must_write"] is False
            assert branches[branch]["target_before_hash"] == branches[branch]["target_after_hash"]


def test_inputs_never_carry_raw_bytes_only_paths_and_hashes():
    for journey_id in JOURNEY_IDS:
        manifest = load_journey_manifest(journey_id, ROOT)
        for entry in manifest.inputs:
            assert set(entry) >= {"kind", "media_type", "path", "sha256", "fixture_id"}
            for value in entry.values():
                if isinstance(value, str):
                    assert len(value) < 300, "input entries must reference, not embed, source content"

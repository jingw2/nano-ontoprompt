"""CLI runner tests using FakeExtractionAdapter — no API key, no network,
proves the runner's wiring/aggregation logic is correct independent of what
a real LLM returns."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from evals.document_extraction.adapters import FakeExtractionAdapter
from evals.document_extraction.run import run_eval

EVAL_ROOT = Path(__file__).resolve().parents[2] / "evals" / "document_extraction"
REPO_ROOT = Path(__file__).resolve().parents[3]


def test_run_eval_produces_one_entry_per_manifest_case():
    adapter = FakeExtractionAdapter()
    result = run_eval(
        manifest_path=str(EVAL_ROOT / "manifest" / "core-v1.json"),
        ground_truth_dir=str(EVAL_ROOT / "ground_truth"),
        adapter=adapter,
        repo_root=str(REPO_ROOT),
    )
    assert result["summary"]["total_cases"] == 8
    assert len(result["cases"]) == 8
    case_ids = {c["case_id"] for c in result["cases"]}
    assert case_ids == {
        "credit-01", "supply-chain-01", "education-01", "medical-01",
        "finance-01", "legal-01", "marketing-01", "hr-01",
    }


def test_run_eval_empty_fake_result_fails_all_keyword_requirements():
    # FakeExtractionAdapter returns an empty result by default, so every
    # ground-truth keyword requirement should fail — proving the scoring
    # wiring actually reaches the validators, not just returns a stub score.
    adapter = FakeExtractionAdapter()
    result = run_eval(
        manifest_path=str(EVAL_ROOT / "manifest" / "core-v1.json"),
        ground_truth_dir=str(EVAL_ROOT / "ground_truth"),
        adapter=adapter,
        repo_root=str(REPO_ROOT),
    )
    assert result["summary"]["mean_keyword_recall"] == 0.0
    for case in result["cases"]:
        assert case["keyword_recall"]["score"] == 0.0


def test_run_eval_detects_dedup_finding_via_canned_result():
    canned = {
        "entities": [
            {"name_cn": "供应商", "description": "x"},
            {"name_cn": "供应商方", "description": "y"},
        ],
        "relations": [], "logic_rules": [], "actions": [], "instances": [],
    }
    adapter = FakeExtractionAdapter(canned_result=canned)
    result = run_eval(
        manifest_path=str(EVAL_ROOT / "manifest" / "core-v1.json"),
        ground_truth_dir=str(EVAL_ROOT / "ground_truth"),
        adapter=adapter,
        repo_root=str(REPO_ROOT),
    )
    assert result["summary"]["cases_with_dedup_findings"] == 8
    assert len(result["cases"][0]["dedup_findings"]) == 1

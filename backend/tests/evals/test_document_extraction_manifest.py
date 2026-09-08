"""Manifest/ground-truth data validation — no LLM calls, no API key needed."""
import json
from pathlib import Path

EVAL_ROOT = Path(__file__).resolve().parents[2] / "evals" / "document_extraction"
REPO_ROOT = Path(__file__).resolve().parents[3]


def _load_manifest():
    with open(EVAL_ROOT / "manifest" / "core-v1.json", encoding="utf-8") as f:
        return json.load(f)


def test_manifest_has_8_cases_with_required_fields():
    cases = _load_manifest()
    assert len(cases) == 8
    for case in cases:
        assert set(case.keys()) == {"case_id", "domain", "source_file", "ground_truth_id"}


def test_manifest_source_files_exist_on_disk():
    cases = _load_manifest()
    for case in cases:
        assert (REPO_ROOT / case["source_file"]).exists(), f"missing {case['source_file']}"


def test_every_case_has_a_matching_ground_truth_file():
    cases = _load_manifest()
    for case in cases:
        gt_path = EVAL_ROOT / "ground_truth" / f"{case['ground_truth_id']}.json"
        assert gt_path.exists(), f"missing ground truth for {case['ground_truth_id']}"
        with open(gt_path, encoding="utf-8") as f:
            requirements = json.load(f)
        assert len(requirements) >= 1
        for req in requirements:
            assert set(req.keys()) == {"category", "required_keywords"}
            assert len(req["required_keywords"]) >= 1

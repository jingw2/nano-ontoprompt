"""Pytest wrapper for the real document-extraction eval — every test here
makes a real DeepSeek API call and is skipped entirely without
DEEPSEEK_API_KEY. This file lives under evals/, not tests/, matching
backend/evals/agent_ontology/test_core_v1.py's placement — it is not part
of the default `pytest -q` collection from backend/tests/."""
import os

import pytest

from evals.document_extraction.adapters import DeepSeekExtractionAdapter
from evals.document_extraction.run import run_eval

EVAL_ROOT = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(EVAL_ROOT, "..", "..", ".."))

requires_deepseek_key = pytest.mark.skipif(
    not os.environ.get("DEEPSEEK_API_KEY"),
    reason="DEEPSEEK_API_KEY not set — this test makes real DeepSeek API calls",
)


@requires_deepseek_key
def test_core_v1_all_cases_run_without_error():
    result = run_eval(
        manifest_path=os.path.join(EVAL_ROOT, "manifest", "core-v1.json"),
        ground_truth_dir=os.path.join(EVAL_ROOT, "ground_truth"),
        adapter=DeepSeekExtractionAdapter(),
        repo_root=REPO_ROOT,
    )
    assert result["summary"]["total_cases"] == 8
    for case in result["cases"]:
        assert "keyword_recall" in case

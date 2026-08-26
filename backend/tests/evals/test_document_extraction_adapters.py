"""DeepSeekExtractionAdapter — skipped entirely without a real API key, since
it makes a real network call. Never runs as a side effect of a normal
`pytest -q`."""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from evals.document_extraction.adapters import DeepSeekExtractionAdapter

REPO_ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.skipif(
    not (os.environ.get("DEEPSEEK_API_KEY") and os.environ.get("RUN_LIVE_EVALS")),
    reason="Set both DEEPSEEK_API_KEY and RUN_LIVE_EVALS=1 to run this test — it makes a real DeepSeek API call",
)
def test_adapter_extracts_a_shape_valid_result_from_a_real_domain_file():
    adapter = DeepSeekExtractionAdapter()
    source_file = str(REPO_ROOT / "test_data" / "信贷" / "信贷业务战略.md")

    result = adapter.extract(source_file)

    assert isinstance(result, dict)
    for key in ("entities", "relations", "logic_rules", "actions"):
        assert key in result
        assert isinstance(result[key], list)
    assert len(result["entities"]) > 0
    for entity in result["entities"]:
        assert "name_cn" in entity

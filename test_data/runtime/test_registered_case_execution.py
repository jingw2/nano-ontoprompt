"""Contract test that the generic deterministic registry runner still works.

This is the `test_data/runtime`-local counterpart of
`backend/tests/runtime/test_registered_case_execution.py`: it proves the
*existing* (pre-Task-1) deterministic registry runner infrastructure --
`registry.py` / `generate_runtime_fixtures.py` / `run_registered_cases.py`
-- still runs every deterministic case in the shared `manifest.json` exactly
once with zero model calls, unaffected by adding the three business-journey
corpora alongside it.

Run with: python -m pytest test_data/runtime/test_registered_case_execution.py -q
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from tests.runtime.run_registered_cases import run_all_registered_cases  # noqa: E402
from registry import load_cases  # noqa: E402

MANIFEST = REPO_ROOT / "test_data/runtime/manifest.json"


def test_registry_runner_executes_all_deterministic_cases_once(tmp_path):
    results = run_all_registered_cases(
        MANIFEST,
        report_path=tmp_path / "deterministic-cases.json",
    )
    expected = {
        case["case_id"]
        for case in load_cases(MANIFEST)
        if case["execution_mode"] == "deterministic"
    }
    assert {item.case_id for item in results} == expected
    assert all(item.status == "passed" and item.skipped is False for item in results)

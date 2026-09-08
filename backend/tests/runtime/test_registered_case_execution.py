"""Contract tests for the deterministic runtime registry runner."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.runtime.run_registered_cases import (
    assert_deterministic_report,
    load_case_execution_report,
    run_all_registered_cases,
)


REPO_ROOT = Path(__file__).resolve().parents[3]
MANIFEST = REPO_ROOT / "test_data/runtime/manifest.json"


def test_runner_executes_a_registered_deterministic_target_once_and_writes_sanitized_report(tmp_path):
    report_path = tmp_path / "registered-cases.json"
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "cases": [
                    next(
                        case
                        for case in json.loads(MANIFEST.read_text(encoding="utf-8"))["cases"]
                        if case["case_id"] == "normal"
                    )
                ]
            }
        ),
        encoding="utf-8",
    )

    results = run_all_registered_cases(manifest_path, report_path=report_path)

    assert [result.case_id for result in results] == ["normal"]
    assert results[0].status == "passed"
    assert results[0].skipped is False
    report = load_case_execution_report(report_path)
    assert_deterministic_report(report)
    assert "stdout" not in report_path.read_text(encoding="utf-8")
    assert "stderr" not in report_path.read_text(encoding="utf-8")


def test_runner_never_selects_a_real_model_browser_case(tmp_path):
    report_path = tmp_path / "registered-cases.json"
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "cases": [
                    next(
                        case
                        for case in json.loads(MANIFEST.read_text(encoding="utf-8"))["cases"]
                        if case["case_id"] == "journey-supply-chain"
                    )
                ]
            }
        ),
        encoding="utf-8",
    )

    assert run_all_registered_cases(manifest_path, report_path=report_path) == ()
    assert load_case_execution_report(report_path).results == ()


def test_report_rejects_missing_duplicate_failed_or_skipped_results(tmp_path):
    report_path = tmp_path / "bad-report.json"
    report_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "model_calls": 0,
                "missing": ["normal"],
                "duplicates": ["normal"],
                "results": [{
                    "case_id": "normal",
                    "target": "backend/tests/example.py::test_example",
                    "status": "skipped",
                    "skipped": True,
                    "exit_code": 0,
                }],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(AssertionError):
        assert_deterministic_report(load_case_execution_report(report_path))

"""Fail-closed release acceptance matrix for the runtime fixture corpus."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
RUNTIME_DATA = REPO_ROOT / "test_data/runtime"
sys.path.insert(0, str(RUNTIME_DATA))

from registry import assert_case_registry, load_cases, load_manifest, target_is_executable, targets_for  # noqa: E402
from tests.runtime.run_registered_cases import (  # noqa: E402
    assert_deterministic_report,
    load_case_execution_report,
    run_all_registered_cases,
)
from app.schemas.runtime import ReasonCode  # noqa: E402


MANIFEST = RUNTIME_DATA / "manifest.json"


@pytest.mark.parametrize("case", load_cases(MANIFEST), ids=lambda case: case["case_id"])
def test_design_case_has_exactly_one_executable_registered_target(case):
    assert_case_registry(case)
    targets = targets_for(case["case_id"])
    assert len(targets) == 1
    assert [target.to_dict() for target in targets] == case["test_targets"]
    assert target_is_executable(targets[0])


def test_case_expected_reason_codes_are_real_enum_members():
    """Item 5 (2026-08-31 fix wave): `expected.reason_code` previously named
    strings that were never checked against the real closed `ReasonCode`
    vocabulary (`assert case["expected"]` only ever checked truthiness) — 29
    of 70 cases drifted to a fictional or renamed value undetected. Assert
    every declared `expected.reason_code`, when present, is a real member so
    this class of drift fails the suite immediately instead of silently
    rotting the release evidence artifact."""
    real_codes = {member.value for member in ReasonCode}
    for case in load_cases(MANIFEST):
        reason_code = case["expected"].get("reason_code")
        if reason_code is not None:
            assert reason_code in real_codes, (
                f"{case['case_id']!r} expected.reason_code {reason_code!r} is not a real ReasonCode member"
            )


def test_case_registry_is_bidirectional_and_complete():
    cases = load_cases(MANIFEST)
    assert {case["case_id"] for case in cases} == set(__import__("registry").CASE_REGISTRY)


def test_case_layers_require_their_test_dimensions():
    for case in load_cases(MANIFEST):
        assert case["expected"]
        assert case["layers"]
        if "database" in case["layers"]:
            assert set(case["dialects"]) == {"mysql", "postgresql"}
        if "parity" in case["layers"]:
            assert set(case["transports"]) == {"mcp", "reference-agent", "rest", "sdk"}
        if "refresh" in case["layers"]:
            assert case["refresh_mode"] in {"batch", "micro_batch", "event_driven"}
            assert case["source_contract"] in {"watermark_primary_key", "opaque_source_cursor"}
            assert case["cursor_outcome"] in {"advanced", "unchanged", "dead_lettered"}
            assert case["lineage_outcome"] in {"advanced", "unchanged", "dead_lettered"}
            if case["case_id"].startswith("cancel-"):
                assert case["cancel_outcome"] in {"requested", "cancelled", "already_terminal"}
                assert case["cursor_outcome"] == "unchanged" or case["cancel_outcome"] == "already_terminal"


def test_manifest_represents_all_required_release_dimensions():
    manifest = load_manifest(MANIFEST)
    assert set(manifest["dialects"]) == {"mysql", "postgresql"}
    refresh_cases = [case for case in manifest["cases"] if "refresh" in case["layers"]]
    assert {case["refresh_mode"] for case in refresh_cases} == {"batch", "micro_batch", "event_driven"}
    assert {case["source_contract"] for case in refresh_cases} == {"watermark_primary_key", "opaque_source_cursor"}
    drift = next(case for case in refresh_cases if case["case_id"] == "config-drift-late-finish")
    assert drift["error_code"] == "CONFIGURATION_DRIFT"
    assert drift["cursor_outcome"] == drift["lineage_outcome"] == "unchanged"
    terminal = next(case for case in refresh_cases if case["case_id"] == "cancel-already-terminal")
    assert terminal["cancel_outcome"] == "already_terminal"
    assert terminal["already_terminal"] is True


def test_registry_runner_executes_every_deterministic_case_once(tmp_path):
    results = run_all_registered_cases(MANIFEST, report_path=tmp_path / "deterministic-cases.json")
    expected_ids = {case["case_id"] for case in load_cases(MANIFEST) if case["execution_mode"] == "deterministic"}
    assert {result.case_id for result in results} == expected_ids
    assert all(result.status == "passed" and not result.skipped for result in results)
    assert_deterministic_report(load_case_execution_report(tmp_path / "deterministic-cases.json"))

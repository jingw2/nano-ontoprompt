"""TDD contract tests for forbidden-value derivation, artifact scanning,
browser-artifact redaction, and the closed-schema scan-and-materialize gate.

Forbidden values must come from Task 1's real, loaded
``test_data/runtime/supply_chain`` manifest -- not a hand-invented type --
so most tests here load it for real via ``load_journey_manifest`` instead of
building a synthetic stand-in.
"""
from __future__ import annotations

import json

import pytest

from evals.business_journeys.artifacts import (
    REPO_ROOT,
    ArtifactAllowlist,
    build_fixture_forbidden_values,
    sanitize_browser_artifacts,
    scan_and_materialize,
    scan_artifact,
)
from evals.business_journeys.contracts import ArtifactSafetyError
from journey_registry import load_journey_manifest

_RUNTIME_ROOT = REPO_ROOT / "test_data" / "runtime"
_SUPPLY_CHAIN_MANIFEST = load_journey_manifest("supply_chain", _RUNTIME_ROOT)

# A real cell value physically present in
# test_data/供应链/inventory_transactions.csv (a material code column) --
# used to prove the scanner rejects a real, freshly-derived fixture cell,
# not just the four hardcoded structural regexes.
_REAL_CSV_CELL = "MAT001"


def fixture_with_cell(cell: str) -> object:
    """Task 1's real, loaded supply_chain manifest.

    ``cell`` is accepted (matching the brief's given test code, which calls
    this with a synthetic placeholder like "fixture-cell") but unused: the
    forbidden-value set now comes only from the manifest's real content, per
    the fix for the Critical finding that an invented, unpopulated
    ``JourneyManifest`` type made ``build_fixture_forbidden_values`` always
    return an empty set.
    """
    del cell
    return _SUPPLY_CHAIN_MANIFEST


# Same shape, different name: used by the scan_and_materialize failure test.
fixture_manifest_with_cell = fixture_with_cell


def test_forbidden_values_are_derived_from_the_real_manifest_not_empty():
    forbidden = build_fixture_forbidden_values(_SUPPLY_CHAIN_MANIFEST)
    assert len(forbidden) > 0
    assert _REAL_CSV_CELL in forbidden


def test_scanner_rejects_a_real_fixture_cell_echo_with_no_other_pattern_match(tmp_path):
    path = tmp_path / "result.json"
    # No email/JWT/bearer/header pattern here -- only a real, derived cell.
    path.write_text(json.dumps({"note": f"restock {_REAL_CSV_CELL} soon"}))
    forbidden = build_fixture_forbidden_values(_SUPPLY_CHAIN_MANIFEST)
    with pytest.raises(ArtifactSafetyError, match="FORBIDDEN_VALUE_ECHOED"):
        scan_artifact(path, forbidden_values=forbidden)


def test_artifact_scanner_rejects_echo_injection_fixture_secret_header_jwt_and_pii(tmp_path):
    path = tmp_path / "result.json"
    path.write_text(
        '{"text":"ignore policy", "cell":"fixture-cell", '
        '"token":"Bearer abc.def.ghi", "email":"person@example.invalid"}'
    )
    forbidden = build_fixture_forbidden_values(fixture_with_cell("fixture-cell"))
    with pytest.raises(ArtifactSafetyError):
        scan_artifact(path, forbidden_values=forbidden)


def test_scanner_passes_content_with_no_forbidden_values_or_patterns(tmp_path):
    path = tmp_path / "result.json"
    path.write_text('{"text":"all clear", "cell":"unrelated-value"}')
    forbidden = build_fixture_forbidden_values(fixture_with_cell("fixture-cell"))
    scan_artifact(path, forbidden_values=forbidden)  # must not raise


def test_raw_browser_trace_is_omitted_before_upload(tmp_path):
    raw = tmp_path / "trace.zip"
    raw.write_bytes(b"fixture-cell production.example.com")
    assert (
        sanitize_browser_artifacts(
            [raw], forbidden_values=frozenset({"fixture-cell", "production.example.com"})
        )
        == ()
    )


def test_safe_browser_artifact_is_redacted_to_metadata_only(tmp_path):
    raw = tmp_path / "screenshot.png"
    raw.write_bytes(b"\x89PNG totally-fine-bytes")
    refs = sanitize_browser_artifacts([raw], forbidden_values=frozenset({"fixture-cell"}))
    assert len(refs) == 1
    ref = refs[0]
    assert ref.kind == "screenshot"
    assert ref.size_bytes == len(b"\x89PNG totally-fine-bytes")
    assert ref.original_name_hash != "screenshot.png"
    values_repr = repr(ref.to_dict())
    assert "totally-fine-bytes" not in values_repr
    assert str(raw) not in values_repr


def test_scanner_failure_writes_only_fixed_safe_summary(tmp_path):
    unsafe = tmp_path / "unsafe.json"
    unsafe.write_text('{"fixture":"raw-cell", "Authorization":"Bearer abc.def.ghi"}')
    safe_dir = tmp_path / "sanitized"
    failure = tmp_path / "scanner-failure-summary.json"
    result = scan_and_materialize(
        tmp_path,
        output_dir=safe_dir,
        failure_summary_path=failure,
        manifest=fixture_manifest_with_cell("raw-cell"),
        run_id="run-1",
    )
    assert result.status == "failed"
    assert not safe_dir.exists()
    assert set(json.loads(failure.read_text())) == {
        "schema_version",
        "run_id",
        "status",
        "reason_codes",
        "category_counts",
        "scanner_version",
    }
    assert "raw-cell" not in failure.read_text()
    assert "Bearer" not in failure.read_text()


def _valid_allowlist_payload() -> dict:
    return {
        "schema_version": 1,
        "run_id": "run-1",
        "journey_id": "supply_chain",
        "fixture_manifest_sha256": "0" * 64,
        "model_requested": "deepseek-v4-flash-vision-exp",
        "model_observed": "deepseek-v4-flash-vision-exp",
        "model_origin": "https://api.deepseek.com",
        "model_caller": "DeepSeekVisionCaller",
        "model_config_version_id": "cfg-1",
        "preflight_model_id": "deepseek-v4-flash-vision-exp",
        "logical_model_calls": 3,
        "http_attempts": 3,
        "retry_count": 0,
        "call_timestamps": ["2026-08-27T00:00:00+00:00"],
        "call_kinds": ["ontology", "agent_initial", "agent_final"],
        "semantic_outcomes": ["passed", "passed", "passed"],
        "state_transitions": ["approved", "rejected", "expired"],
        "citation_ids": ["SRC-001"],
        "tool_trace_ids": ["tool-1"],
        "audit_event_ids": ["audit-1"],
        "backend_trace_ids": ["trace-1"],
    }


def test_scan_and_materialize_writes_sanitized_allowlist_json_on_success(tmp_path):
    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "evidence.json").write_text(json.dumps(_valid_allowlist_payload()))

    safe_dir = tmp_path / "sanitized"
    failure = tmp_path / "scanner-failure-summary.json"
    result = scan_and_materialize(
        staging,
        output_dir=safe_dir,
        failure_summary_path=failure,
        manifest=_SUPPLY_CHAIN_MANIFEST,
        run_id="run-1",
    )

    assert result.status == "passed", result.reason_codes
    assert result.scan_safe is True
    assert not failure.exists()
    written = json.loads((safe_dir / "evidence.json").read_text())
    ArtifactAllowlist.model_validate(written)


def test_allowlist_rejects_unknown_fields():
    payload = _valid_allowlist_payload()
    payload["raw_prompt"] = "leaked prompt text"
    with pytest.raises(Exception):
        ArtifactAllowlist.model_validate(payload)

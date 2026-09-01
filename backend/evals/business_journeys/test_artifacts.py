"""TDD contract tests for forbidden-value derivation, artifact scanning,
browser-artifact redaction, and the closed-schema scan-and-materialize gate.
"""
from __future__ import annotations

import json

import pytest

from evals.business_journeys.artifacts import (
    ArtifactAllowlist,
    build_fixture_forbidden_values,
    sanitize_browser_artifacts,
    scan_and_materialize,
    scan_artifact,
)
from evals.business_journeys.contracts import ArtifactSafetyError, JourneyManifest


def fixture_with_cell(cell: str) -> JourneyManifest:
    """A minimal manifest carrying one fixture cell and one canonical
    prompt-injection sentinel -- exactly the values the forbidden-value set
    must be derived from, nothing hand-maintained in the scanner itself."""
    return JourneyManifest(
        journey_id="supply_chain",
        fixture_version="test-fixture",
        manifest_sha256="0" * 64,
        fixture_cells=(cell,),
        prompt_injection_sentinels=("ignore policy",),
    )


# Same shape, different name: used by the scan_and_materialize failure test.
fixture_manifest_with_cell = fixture_with_cell


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
        manifest=fixture_with_cell("no-such-cell-in-this-evidence"),
        run_id="run-1",
    )

    assert result.status == "passed"
    assert result.scan_safe is True
    assert not failure.exists()
    written = json.loads((safe_dir / "evidence.json").read_text())
    ArtifactAllowlist.model_validate(written)


def test_allowlist_rejects_unknown_fields():
    payload = _valid_allowlist_payload()
    payload["raw_prompt"] = "leaked prompt text"
    with pytest.raises(Exception):
        ArtifactAllowlist.model_validate(payload)

"""Sub-project E3 bug 2: PostHarnessValidator had no check for record-shaped
names (e.g. CUS0001) leaking into the concept-level entities list — the only
existing detector for this lived in the eval-only audit tool
(backend/evals/document_extraction/validators.py), never in production."""
from app.engine.post_harness.validator import PostHarnessValidator, Severity


def _base_result(entities):
    return {
        "entities": entities,
        "relations": [],
        "logic_rules": [],
        "actions": [],
    }


def test_instance_id_pattern_flagged_as_warning():
    result = _base_result([
        {"name_cn": "CUS0001", "type": "Customer", "properties": {"region": "上海"}},
    ])
    report = PostHarnessValidator().validate(result)
    codes = [i.code for i in report.issues if i.severity == Severity.WARNING]
    assert "ENTITY_LOOKS_LIKE_INSTANCE" in codes


def test_trailing_digit_run_pattern_flagged_as_warning():
    result = _base_result([
        {"name_cn": "客户001", "type": "Customer", "properties": {"region": "上海"}},
    ])
    report = PostHarnessValidator().validate(result)
    codes = [i.code for i in report.issues if i.severity == Severity.WARNING]
    assert "ENTITY_LOOKS_LIKE_INSTANCE" in codes


def test_concept_level_name_is_not_flagged():
    result = _base_result([
        {"name_cn": "客户", "type": "Customer", "properties": {"region": "上海"}},
    ])
    report = PostHarnessValidator().validate(result)
    codes = [i.code for i in report.issues]
    assert "ENTITY_LOOKS_LIKE_INSTANCE" not in codes


def test_instance_leakage_is_warning_not_fatal():
    result = _base_result([
        {"name_cn": "CUS0001", "type": "Customer", "properties": {"region": "上海"}},
    ])
    report = PostHarnessValidator().validate(result)
    assert not report.has_fatal()

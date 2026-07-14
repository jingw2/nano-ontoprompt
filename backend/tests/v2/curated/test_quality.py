"""QualityService 单元测试"""
import pytest
from unittest.mock import MagicMock
from app.services.v2.curated.quality_service import QualityService, QualityReport


def make_svc():
    db = MagicMock()
    return QualityService(db)


CLEAN_DATA = [
    {"id": "1", "name": "Alice", "age": "30", "score": "9.5"},
    {"id": "2", "name": "Bob",   "age": "25", "score": "8.0"},
    {"id": "3", "name": "Carol", "age": "28", "score": "9.0"},
]

DIRTY_DATA = [
    {"id": "1", "name": "Alice", "age": "30",  "city": "Beijing"},
    {"id": "2", "name": "",      "age": None,   "city": "Shanghai"},
    {"id": "3", "name": "Carol", "age": "abc",  "city": ""},
    {"id": "1", "name": "Alice", "age": "30",   "city": "Beijing"},  # 重复
]


def test_quality_empty_data():
    svc = make_svc()
    report = svc.compute_report("ds-1", [])
    assert report.row_count == 0
    assert report.overall_score == 1.0
    assert "数据集为空" in report.issues


def test_quality_clean_data_high_score():
    svc = make_svc()
    report = svc.compute_report("ds-1", CLEAN_DATA)
    assert report.row_count == 3
    assert report.completeness_score == 1.0
    assert report.duplicate_count == 0
    assert report.overall_score >= 0.9


def test_quality_detects_nulls():
    svc = make_svc()
    report = svc.compute_report("ds-1", DIRTY_DATA)
    # name 列有空值，age 列有 None
    null_cols = [c for c in report.columns if c.null_pct > 0]
    assert len(null_cols) >= 2


def test_quality_detects_duplicates():
    svc = make_svc()
    report = svc.compute_report("ds-1", DIRTY_DATA)
    assert report.duplicate_count == 1
    assert report.uniqueness_score < 1.0
    assert any("重复" in issue for issue in report.issues)


def test_quality_completeness_score_below_1():
    svc = make_svc()
    report = svc.compute_report("ds-1", DIRTY_DATA)
    assert report.completeness_score < 1.0


def test_quality_column_count():
    svc = make_svc()
    report = svc.compute_report("ds-1", CLEAN_DATA)
    assert report.column_count == 4


def test_quality_to_dict_structure():
    svc = make_svc()
    report = svc.compute_report("ds-1", CLEAN_DATA)
    d = report.to_dict()
    required_keys = {"dataset_id", "row_count", "column_count", "overall_score",
                     "completeness_score", "uniqueness_score", "validity_score",
                     "duplicate_count", "issues", "columns"}
    assert required_keys.issubset(d.keys())


def test_infer_type_integer():
    assert QualityService._infer_type(["1", "2", "3"]) == "integer"


def test_infer_type_float():
    assert QualityService._infer_type(["1.5", "2.3"]) == "float"


def test_infer_type_string():
    assert QualityService._infer_type(["Alice", "Bob"]) == "string"


def test_infer_type_mixed():
    assert QualityService._infer_type(["1", "Alice", "3.5"]) == "mixed"


def test_quality_high_null_rate_issue():
    """空值率超 50% 时记录 issue"""
    svc = make_svc()
    data = [{"id": str(i), "name": "" if i > 2 else "Alice"} for i in range(10)]
    report = svc.compute_report("ds-1", data)
    assert any("空值率过高" in issue for issue in report.issues)

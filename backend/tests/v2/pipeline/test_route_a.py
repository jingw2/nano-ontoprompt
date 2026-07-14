"""Pipeline Route A 테스트"""
import pytest
from app.services.v2.pipeline.base import PipelineContext
from app.services.v2.pipeline.steps.schema_inference import SchemaInferenceStep
from app.services.v2.pipeline.steps.cleansing import CleansingStep
from app.services.v2.pipeline.engine import execute_route_a


SAMPLE_DATA = [
    {"id": "1", "name": "Alice", "age": "30", "score": "9.5", "active": "true"},
    {"id": "2", "name": "  Bob  ", "age": "25", "score": "8.0", "active": "false"},
    {"id": "3", "name": "", "age": None, "score": "7.0", "active": "true"},
    {"id": "2", "name": "Bob", "age": "25", "score": "8.0", "active": "false"},  # 중복
]


def make_ctx():
    return PipelineContext(dataset_id="test-ds", version_no=1, route="A")


def test_schema_inference_detects_types():
    step = SchemaInferenceStep()
    ctx = make_ctx()
    step.run(ctx, SAMPLE_DATA)
    schema = ctx.meta["inferred_schema"]
    assert schema["id"] == "integer"
    assert schema["age"] == "integer"
    assert schema["score"] == "float"
    assert schema["name"] == "string"


def test_cleansing_trims_strings():
    step = CleansingStep()
    ctx = make_ctx()
    ctx.spec = {"cleansing": {"trim_strings": True, "null_strategy": "fill_empty"}}
    result = step.run(ctx, [{"name": "  Alice  "}])
    assert result[0]["name"] == "Alice"


def test_cleansing_deduplication():
    step = CleansingStep()
    ctx = make_ctx()
    ctx.spec = {"cleansing": {"deduplicate": True, "null_strategy": "fill_empty"}}
    result = step.run(ctx, SAMPLE_DATA)
    ids = [r["id"] for r in result]
    assert ids.count("2") <= 1  # 중복 제거


def test_cleansing_drop_null():
    step = CleansingStep()
    ctx = make_ctx()
    ctx.spec = {"cleansing": {"null_strategy": "drop", "deduplicate": False}}
    rows = [{"name": "Alice", "age": "30"}, {"name": "", "age": "25"}, {"name": "Bob", "age": None}]
    result = step.run(ctx, rows)
    assert all(r["name"] != "" and r["age"] is not None for r in result)


def test_execute_route_a_full_pipeline():
    ctx = make_ctx()
    ctx.spec = {"cleansing": {"trim_strings": True, "deduplicate": True, "null_strategy": "fill_empty"}}
    result_data, result_ctx = execute_route_a(ctx, SAMPLE_DATA)
    assert isinstance(result_data, list)
    assert result_ctx.rows_out == len(result_data)
    assert "inferred_schema" in result_ctx.meta


def test_execute_route_a_empty_data():
    ctx = make_ctx()
    result_data, result_ctx = execute_route_a(ctx, [])
    assert result_data == []
    assert result_ctx.rows_out == 0

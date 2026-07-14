"""Pipeline Route B — JSON flatten + XML 파싱 테스트"""
import json
import pytest

from app.services.v2.pipeline.base import PipelineContext
from app.services.v2.pipeline.steps.json_flatten import JsonFlattenStep
from app.services.v2.pipeline.steps.xml_parse import XmlParseStep
from app.services.v2.pipeline.engine import execute_route_b


def make_ctx(spec=None):
    ctx = PipelineContext(dataset_id="test-ds", version_no=1, route="B")
    if spec:
        ctx.spec = spec
    return ctx


# ─── JSON Flatten Tests ──────────────────────────────────────────────

NESTED_JSON = [
    {
        "order_id": "ORD-001",
        "customer": {"id": "C-100", "name": "Alice", "address": {"city": "Seoul"}},
        "items": [{"sku": "A", "qty": 2}, {"sku": "B", "qty": 1}],
        "total": 250.0
    }
]


def test_json_flatten_flattens_nested_dict():
    step = JsonFlattenStep()
    ctx = make_ctx({"json_flatten": {"array_explode": False}})
    result = step.run(ctx, NESTED_JSON)
    assert len(result) == 1
    row = result[0]
    assert "customer.id" in row
    assert "customer.address.city" in row
    assert row["customer.id"] == "C-100"
    assert row["customer.address.city"] == "Seoul"


def test_json_flatten_explodes_arrays():
    step = JsonFlattenStep()
    ctx = make_ctx({"json_flatten": {"array_explode": True}})
    result = step.run(ctx, NESTED_JSON)
    # items 배열 2개 → 2개 row
    assert len(result) == 2
    skus = [r.get("items.sku") for r in result]
    assert "A" in skus
    assert "B" in skus


def test_json_flatten_custom_separator():
    step = JsonFlattenStep()
    ctx = make_ctx({"json_flatten": {"sep": "__", "array_explode": False}})
    result = step.run(ctx, [{"a": {"b": 1}}])
    assert "a__b" in result[0]


def test_json_flatten_empty_data():
    step = JsonFlattenStep()
    ctx = make_ctx()
    result = step.run(ctx, [])
    assert result == []


def test_json_flatten_depth_5():
    """5단계 중첩 JSON 정상 처리"""
    deep = {"l1": {"l2": {"l3": {"l4": {"l5": "value"}}}}}
    step = JsonFlattenStep()
    ctx = make_ctx({"json_flatten": {"array_explode": False}})
    result = step.run(ctx, [deep])
    assert result[0]["l1.l2.l3.l4.l5"] == "value"


# ─── XML Parse Tests ────────────────────────────────────────────────

XML_DATA = [{"xml_content": """<?xml version="1.0"?>
<articles>
  <record id="A001"><title>ML Basics</title><author>John</author></record>
  <record id="A002"><title>Deep Learning</title><author>Jane</author></record>
</articles>"""}]


def test_xml_parse_extracts_records():
    step = XmlParseStep()
    ctx = make_ctx({"xml_parse": {"record_path": ".//record"}})
    result = step.run(ctx, XML_DATA)
    assert len(result) == 2


def test_xml_parse_includes_attributes():
    step = XmlParseStep()
    ctx = make_ctx({"xml_parse": {"record_path": ".//record", "include_attributes": True}})
    result = step.run(ctx, XML_DATA)
    assert result[0]["id"] == "A001"


def test_xml_parse_extracts_text_fields():
    step = XmlParseStep()
    ctx = make_ctx({"xml_parse": {"record_path": ".//record"}})
    result = step.run(ctx, XML_DATA)
    titles = [r["title"] for r in result]
    assert "ML Basics" in titles
    assert "Deep Learning" in titles


def test_xml_parse_invalid_xml_passthrough():
    """잘못된 XML은 원본 row 그대로 반환 (오류 없이 처리)"""
    step = XmlParseStep()
    ctx = make_ctx()
    bad_data = [{"xml_content": "<broken xml"}]
    result = step.run(ctx, bad_data)
    assert len(result) == 1  # 오류 없이 처리


# ─── Route B 통합 테스트 ─────────────────────────────────────────────

def test_execute_route_b_json():
    ctx = make_ctx({"format": "json", "json_flatten": {"array_explode": True}})
    data = [{"a": {"b": 1}, "items": [{"x": 1}, {"x": 2}]}]
    result, ctx2 = execute_route_b(ctx, data)
    assert len(result) == 2
    assert ctx2.rows_out == 2


def test_execute_route_b_json_preserves_jagged_rows():
    ctx = make_ctx({"format": "json", "json_flatten": {"array_explode": True}})
    data = [
        {"order_id": "PO-1", "items": [{"sku": "A"}], "logistics": {"actual_days": 3}},
        {"order_id": "PO-2", "items": [{"sku": "B"}], "logistics": {"actual_days": 6, "delay_reason": "天气"}},
    ]

    result, ctx2 = execute_route_b(ctx, data)

    assert ctx2.rows_out == 2
    assert {row["order_id"] for row in result} == {"PO-1", "PO-2"}
    assert any(row.get("logistics.delay_reason") == "天气" for row in result)


def test_execute_route_b_xml():
    ctx = make_ctx({"format": "xml", "xml_parse": {"record_path": ".//record"}})
    xml_str = "<root><record><name>Test</name></record></root>"
    data = [{"xml_content": xml_str}]
    result, ctx2 = execute_route_b(ctx, data)
    assert len(result) >= 1


def test_execute_route_b_empty():
    ctx = make_ctx({"format": "json"})
    result, ctx2 = execute_route_b(ctx, [])
    assert result == []
    assert ctx2.rows_out == 0

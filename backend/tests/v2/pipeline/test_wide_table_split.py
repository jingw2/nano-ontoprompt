"""와이드 테이블 분할 + DuckDB 서비스 테스트"""
import pytest
from unittest.mock import patch, MagicMock

from app.services.v2.pipeline.base import PipelineContext
from app.services.v2.pipeline.steps.wide_table_split import WideTableSplitStep
from app.services.v2.duckdb_service import DuckDBService


def make_ctx(spec=None):
    ctx = PipelineContext(dataset_id="test-ds", version_no=1, route="A")
    if spec:
        ctx.spec = spec
    return ctx


# ── 와이드 테이블 분할 테스트 ─────────────────────────────────────────

WIDE_DATA = [
    {
        "order_id": "ORD-001", "order_date": "2024-01-01", "amount": 1000,
        "customer_id": "C-100", "customer_name": "Alice", "customer_email": "alice@example.com",
        "product_id": "P-001", "product_name": "Widget A", "product_sku": "SKU-001",
        "shipment_id": "SHP-001", "shipment_date": "2024-01-02",
    }
] * 5  # 11개 컬럼, 5개 row


def test_wide_table_split_with_explicit_config():
    """명시적 split_config로 분할"""
    step = WideTableSplitStep()
    ctx = make_ctx({
        "wide_table_split": {
            "split_config": {
                "orders": ["order_id", "order_date", "amount"],
                "customers": ["customer_id", "customer_name", "customer_email"],
            }
        }
    })
    result = step.run(ctx, WIDE_DATA)
    assert "split_tables" in ctx.meta
    assert "orders" in ctx.meta["split_tables"]
    assert "customers" in ctx.meta["split_tables"]
    # 첫 번째 테이블이 메인 output
    assert isinstance(result, list)


def test_wide_table_split_suggest_only():
    """suggest_only=True면 실제 분할 없이 제안만 저장"""
    step = WideTableSplitStep()
    ctx = make_ctx({
        "wide_table_split": {
            "suggest_only": True,
            "wide_threshold": 5,
        }
    })
    with patch.object(WideTableSplitStep, '_suggest_split', return_value={
        "split_config": {"t1": ["order_id"], "t2": ["customer_id"]}
    }):
        result = step.run(ctx, WIDE_DATA)
    # 분할 미실행, 원본 데이터 반환
    assert result == WIDE_DATA
    assert ctx.meta["wide_table_split"]["suggested"] is True


def test_wide_table_split_below_threshold():
    """컬럼 수가 threshold 미만이고 split_config 없으면 스킵"""
    step = WideTableSplitStep()
    ctx = make_ctx({"wide_table_split": {"wide_threshold": 20}})
    result = step.run(ctx, WIDE_DATA)
    assert ctx.meta["wide_table_split"]["skipped"] is True
    assert result == WIDE_DATA


def test_wide_table_split_empty_data():
    """빈 데이터 입력"""
    step = WideTableSplitStep()
    ctx = make_ctx()
    result = step.run(ctx, [])
    assert result == []


def test_wide_table_split_deduplicates():
    """같은 row가 여러 개여도 분할 결과는 중복 제거됨"""
    step = WideTableSplitStep()
    ctx = make_ctx({
        "wide_table_split": {
            "split_config": {"orders": ["order_id", "amount"]}
        }
    })
    result = step.run(ctx, WIDE_DATA)
    # 5개 동일 row → 1개로 중복 제거
    orders = ctx.meta["split_tables"]["orders"]
    assert len(orders) == 1


# ── DuckDBService 테스트 ──────────────────────────────────────────────

def test_duckdb_service_available_or_fallback():
    """DuckDB 설치 여부에 관계없이 인스턴스 생성 가능"""
    svc = DuckDBService()
    # available이 True 또는 False, 오류 없이 생성
    assert isinstance(svc.available, bool)


def test_duckdb_split_wide_table():
    """split_wide_table이 컬럼 분할 후 각 테이블 반환"""
    svc = DuckDBService()
    data = [
        {"id": 1, "name": "Alice", "age": 30, "city": "Seoul"},
        {"id": 2, "name": "Bob", "age": 25, "city": "Busan"},
    ]
    result = svc.split_wide_table(data, {
        "persons": ["id", "name", "age"],
        "locations": ["id", "city"],
    })
    assert "persons" in result
    assert "locations" in result
    assert len(result["persons"]) == 2
    assert result["persons"][0]["name"] in ("Alice", "Bob")


def test_duckdb_split_nonexistent_columns():
    """존재하지 않는 컬럼 지정 시 빈 테이블 반환"""
    svc = DuckDBService()
    data = [{"id": 1}]
    result = svc.split_wide_table(data, {"t1": ["nonexistent"]})
    assert result["t1"] == []


def test_duckdb_infer_schema():
    """schema 추론이 컬럼 정보를 반환"""
    svc = DuckDBService()
    data = [{"id": 1, "name": "Alice", "score": 9.5}]
    schema = svc.infer_schema(data)
    assert len(schema) == 3
    names = [s["name"] for s in schema]
    assert "id" in names and "name" in names


def test_duckdb_preview_limit():
    """preview가 limit을 준수"""
    svc = DuckDBService()
    data = [{"n": i} for i in range(200)]
    result = svc.preview(data, limit=50)
    assert len(result) == 50


def test_duckdb_split_deduplication():
    """split_wide_table이 중복 row 제거"""
    svc = DuckDBService()
    data = [{"id": 1, "name": "A"}] * 5
    result = svc.split_wide_table(data, {"t1": ["id", "name"]})
    assert len(result["t1"]) == 1

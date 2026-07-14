"""
Pipeline 处理吞吐量性能基准测试

目标：
- Route A（CSV 1万行）处理时间 < 2秒
- Route B（JSON 1千条）处理时间 < 1秒
- 质量报告（1万行）计算时间 < 1秒
"""
import time
import pytest

# 性能阈值（秒）
ROUTE_A_THRESHOLD_S = 2.0
ROUTE_B_THRESHOLD_S = 1.0
QUALITY_THRESHOLD_S = 1.0


def generate_csv_rows(n: int) -> list[dict]:
    return [
        {
            "id": str(i),
            "order_id": f"ORD-{i:06d}",
            "customer_name": f"客户-{i % 100}",
            "amount": str(float(i * 10.5)),
            "status": "active" if i % 2 == 0 else "inactive",
            "created_at": "2024-01-15",
        }
        for i in range(n)
    ]


def generate_nested_json(n: int) -> list[dict]:
    return [
        {
            "booking_id": f"BK-{i}",
            "customer": {"id": f"C-{i}", "name": f"客户{i}"},
            "items": [{"sku": f"SKU-{j}", "qty": j + 1} for j in range(3)],
        }
        for i in range(n)
    ]


def test_route_a_throughput_10k_rows():
    """Route A 处理 1 万行，必须在 2 秒内完成"""
    from app.services.v2.pipeline.base import PipelineContext
    from app.services.v2.pipeline.engine import execute_route_a

    data = generate_csv_rows(10_000)
    ctx = PipelineContext(dataset_id="perf-test", version_no=1, route="A")
    ctx.spec = {"cleansing": {"deduplicate": False, "trim_strings": True}}

    start = time.perf_counter()
    result, _ = execute_route_a(ctx, data)
    elapsed = time.perf_counter() - start

    assert elapsed < ROUTE_A_THRESHOLD_S, (
        f"Route A 处理 10000 行耗时 {elapsed:.2f}s，超过阈值 {ROUTE_A_THRESHOLD_S}s"
    )
    assert len(result) > 0


def test_route_b_throughput_1k_json():
    """Route B 处理 1 千条 JSON，必须在 1 秒内完成"""
    from app.services.v2.pipeline.base import PipelineContext
    from app.services.v2.pipeline.engine import execute_route_b

    data = generate_nested_json(1_000)
    ctx = PipelineContext(dataset_id="perf-test", version_no=1, route="B")
    ctx.spec = {"format": "json", "json_flatten": {"array_explode": True}}

    start = time.perf_counter()
    result, _ = execute_route_b(ctx, data)
    elapsed = time.perf_counter() - start

    assert elapsed < ROUTE_B_THRESHOLD_S, (
        f"Route B 处理 1000 条 JSON 耗时 {elapsed:.2f}s，超过阈值 {ROUTE_B_THRESHOLD_S}s"
    )
    assert len(result) > 0


def test_quality_report_throughput_10k():
    """质量报告计算 1 万行，必须在 1 秒内完成"""
    from unittest.mock import MagicMock
    from app.services.v2.curated.quality_service import QualityService

    data = generate_csv_rows(10_000)
    db = MagicMock()
    svc = QualityService(db)

    start = time.perf_counter()
    report = svc.compute_report("perf-test", data)
    elapsed = time.perf_counter() - start

    assert elapsed < QUALITY_THRESHOLD_S, (
        f"质量报告计算 10000 行耗时 {elapsed:.2f}s，超过阈值 {QUALITY_THRESHOLD_S}s"
    )
    assert report.row_count == 10_000

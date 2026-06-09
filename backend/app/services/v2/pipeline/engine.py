"""Pipeline 실행 엔진"""
from __future__ import annotations
from app.services.v2.pipeline.base import PipelineContext
from app.services.v2.pipeline.steps.schema_inference import SchemaInferenceStep
from app.services.v2.pipeline.steps.cleansing import CleansingStep


def execute_route_a(ctx: PipelineContext, data: list[dict]) -> tuple[list[dict], PipelineContext]:
    """Route A: 구조화 데이터 처리 (schema 추론 + 정제)"""
    steps = [SchemaInferenceStep(), CleansingStep()]
    for step in steps:
        data = step.run(ctx, data)
    wide_spec = ctx.spec.get("wide_table_split", {})
    if wide_spec.get("enabled") or wide_spec.get("split_config"):
        from app.services.v2.pipeline.steps.wide_table_split import WideTableSplitStep

        data = WideTableSplitStep().run(ctx, data)
    ctx.rows_out = len(data)
    return data, ctx


def execute_route_b(ctx: PipelineContext, data: list[dict]) -> tuple[list[dict], PipelineContext]:
    """Route B: 반구조화 데이터 처리 (JSON flatten 또는 XML 파싱 + 정제)"""
    from app.services.v2.pipeline.steps.json_flatten import JsonFlattenStep
    from app.services.v2.pipeline.steps.xml_parse import XmlParseStep

    data_format = ctx.spec.get("format", "json")  # json | xml

    if data_format == "xml":
        steps = [XmlParseStep(), CleansingStep()]
    else:
        steps = [JsonFlattenStep(), CleansingStep()]

    for step in steps:
        data = step.run(ctx, data)
    ctx.rows_out = len(data)
    return data, ctx


def execute_route_c(ctx: PipelineContext, data: list[dict]) -> tuple[list[dict], PipelineContext]:
    """Route C: 비구조화 데이터 (문서 → Markdown → LLM 구조화 추출)"""
    from app.services.v2.pipeline.steps.document_to_md import DocumentToMarkdownStep
    from app.services.v2.pipeline.steps.md_to_structured import MarkdownToStructuredStep

    steps = [DocumentToMarkdownStep(), MarkdownToStructuredStep()]
    for step in steps:
        data = step.run(ctx, data)
    ctx.rows_out = len(data)
    return data, ctx


def execute_route_a_with_split(ctx: PipelineContext, data: list[dict]) -> tuple[dict[str, list[dict]], PipelineContext]:
    """Route A + 와이드 테이블 분할 (split_config 있을 때 사용)"""
    from app.services.v2.pipeline.steps.wide_table_split import WideTableSplitStep

    # 기본 Route A 정제 먼저
    data, ctx = execute_route_a(ctx, data)

    # 와이드 테이블 분할
    split_step = WideTableSplitStep()
    split_step.run(ctx, data)

    # 분할 결과 반환 (없으면 단일 테이블)
    split_tables = ctx.meta.get("split_tables", {"main": data})
    return split_tables, ctx

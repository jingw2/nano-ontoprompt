"""Pipeline 执行引擎"""
from __future__ import annotations
from app.services.v2.pipeline.base import PipelineContext
from app.services.v2.pipeline.steps.schema_inference import SchemaInferenceStep
from app.services.v2.pipeline.steps.cleansing import CleansingStep


def execute_route_a(ctx: PipelineContext, data: list[dict]) -> tuple[list[dict], PipelineContext]:
    """Route A: 结构化数据处理 (schema 推断 + 清洗)"""
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
    """Route B: 半结构化数据处理 (JSON flatten 或 XML 解析 + 清洗)"""
    from app.services.v2.pipeline.steps.json_flatten import JsonFlattenStep
    from app.services.v2.pipeline.steps.xml_parse import XmlParseStep

    data_format = ctx.spec.get("format", "json")  # json | xml

    cleansing = dict(ctx.spec.get("cleansing") or {})
    cleansing.setdefault("filter_jagged", False)
    ctx.spec["cleansing"] = cleansing

    if data_format == "xml":
        steps = [XmlParseStep(), CleansingStep()]
    else:
        steps = [JsonFlattenStep(), CleansingStep()]

    for step in steps:
        data = step.run(ctx, data)
    ctx.rows_out = len(data)
    return data, ctx


def execute_route_c(ctx: PipelineContext, data: list[dict]) -> tuple[list[dict], PipelineContext]:
    """Route C: 非结构化数据 (文档 → Markdown → LLM 结构化提取)"""
    from app.services.v2.pipeline.steps.document_to_md import DocumentToMarkdownStep
    from app.services.v2.pipeline.steps.md_to_structured import MarkdownToStructuredStep

    steps = [DocumentToMarkdownStep(), MarkdownToStructuredStep()]
    for step in steps:
        data = step.run(ctx, data)
    ctx.rows_out = len(data)
    return data, ctx



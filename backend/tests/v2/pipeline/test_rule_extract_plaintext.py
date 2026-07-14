"""Route C 规则提取 — PDF 纯文本形态回归测试

MarkItDown 转换 PDF 得到的是无 # 标题的纯文本:编号标题、逗号型 IF 规则、定义行。
此前这种形态只能产出 1 行兜底记录。
"""
from app.services.v2.pipeline.base import PipelineContext
from app.services.v2.pipeline.steps.md_to_structured import MarkdownToStructuredStep

PDF_LIKE_MD = """Warehouse Management Standard

1. Warehouse Classification

Warehouse A (Raw Materials): Steel, Aluminum — temperature 15-25C, humidity <60%
Warehouse B (Electronic Components): chips, resistors — temperature 20-25C, humidity <40%
Warehouse C (Finished Goods/Packaging): boxes, films — standard environment

2. Inventory Control Rules

FIFO (First In First Out) mandatory for all materials with expiry dates.
Weekly cycle count: 20% of SKUs per week, full count quarterly.
IF inventory variance > 2%, mandatory root-cause investigation within 48h.
Hazardous materials storage requires dedicated zone with fire suppression.

3. Receiving Procedures

Step 1: Check PO matching (quantity, spec, supplier).
IF IQC fail rate > 5%, reject entire lot and notify procurement.

4. Dispatch Procedures

IF stock < request quantity, trigger emergency procurement alert.
"""


def _extract(md: str) -> list[dict]:
    step = MarkdownToStructuredStep()
    ctx = PipelineContext(dataset_id="d1", version_no=1, route="C",
                          spec={"md_to_structured": {"rule_based": True}})
    return step._rule_based_extract([{"markdown_text": md, "filename": "warehouse_management.pdf"}], ctx)


def test_comma_form_if_rules_extracted():
    """IF <条件>, <动作> (无 THEN 关键字) 也应识别为规则"""
    rows = _extract(PDF_LIKE_MD)
    rules = [r for r in rows if r.get("row_type") == "rule"]
    assert len(rules) == 3
    conditions = {r["condition"] for r in rules}
    assert any("inventory variance" in c for c in conditions)


def test_plaintext_numbered_sections_split():
    """无 # 标题时, 编号标题 (1. Title) 应作为章节边界拆分"""
    rows = _extract(PDF_LIKE_MD)
    sections = [r for r in rows if r.get("row_type") == "section"]
    titles = {r["section_title"] for r in sections}
    assert len(sections) >= 4
    assert "Warehouse Classification" in titles
    assert "Dispatch Procedures" in titles


def test_definition_lines_extracted():
    """Name (qualifier): description 形态应提取为 definition 记录"""
    rows = _extract(PDF_LIKE_MD)
    defs = [r for r in rows if r.get("row_type") == "definition"]
    names = {d["name"] for d in defs}
    assert {"Warehouse A", "Warehouse B", "Warehouse C"} <= names


def test_then_form_rules_still_work():
    """既有行为回归: IF ... THEN ... 仍正常提取"""
    md = "# 规则\n- IF 交货准时率 < 85% THEN 降一级并发送整改通知\n"
    rows = _extract(md)
    rules = [r for r in rows if r.get("row_type") == "rule"]
    assert len(rules) == 1
    assert rules[0]["action"].startswith("降一级")

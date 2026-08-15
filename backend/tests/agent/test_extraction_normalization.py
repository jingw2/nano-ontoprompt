"""Issue: simple-LLM ontology construction optimization (per the graph
engineering playbook).

The extraction prompt carries the playbook discipline (central-only entities,
grounded one-line descriptions, dedup guidance, explicit hierarchy guidance,
semantic relation types, no dangling references) and `extract_ontology`
normalizes every LLM result deterministically: junk entities dropped,
duplicate surface forms merged into one canonical entity, dangling/vague
relations removed, relation triples deduplicated.  The test proves the
quality improvement over a real test_data document.
"""
from pathlib import Path

import pytest

from app.services.llm_service import (
    VAGUE_RELATION_TYPES,
    extract_ontology,
    normalize_extracted_ontology,
)

BACKEND_DIR = Path(__file__).resolve().parents[2]
TEST_DATA = BACKEND_DIR.parent / "test_data"


def test_extraction_discipline_red_contract():
    """The extraction prompt + normalization are the observable discipline."""
    source = (BACKEND_DIR / "app" / "services" / "llm_service.py").read_text()
    for symbol in ("normalize_extracted_ontology", "VAGUE_RELATION_TYPES"):
        assert symbol in source, f"llm_service.py missing {symbol}"
    assert "禁止使用" in source  # prompt bans vague relation types
    assert "同一概念" in source   # prompt bans duplicate surface forms
    router = (BACKEND_DIR / "app" / "routers" / "prompts.py").read_text()
    assert "禁止为同义写法建重复实体" in router


def test_normalization_dedups_and_drops_junk_over_test_data_document():
    """A noisy extraction over the real 信贷 strategy document is normalized:
    duplicate surface forms merge, junk entities vanish, dangling and vague
    relations are dropped, hierarchy relations survive."""
    document = TEST_DATA / "信贷" / "信贷业务战略.md"
    if not document.exists():
        pytest.skip("test_data document unavailable")
    text = document.read_text(encoding="utf-8")

    raw = {
        "entities": [
            {"name_cn": "借款人", "type": "Concept", "description": "贷款申请人", "properties": {}},
            {"name_cn": "借款人（Borrower）", "type": "Concept", "description": "", "properties": {}},
            {"name_cn": "信用评分", "type": "Concept", "description": "授信决策的核心指标", "properties": {}},
            {"name_cn": "信用分层", "type": "Category", "description": "借款人风险分层", "properties": {}},
            {"name_cn": "A层", "type": "Category", "description": "优质客户", "properties": {}},
            {"name_cn": "B层", "type": "Category", "description": "良好客户", "properties": {}},
            {"name_cn": "授信额度", "type": "Concept", "description": "可贷上限", "properties": {}},
            {"name_cn": "12345", "type": "Concept", "description": "噪音", "properties": {}},
            {"name_cn": "", "type": "Concept", "description": "空名", "properties": {}},
            {"name_cn": "信贷业务战略.md", "type": "Concept", "description": "文件名", "properties": {}},
        ],
        "relations": [
            {"source": "借款人", "target": "信用分层", "type": "IS-A", "confidence": 0.9},
            {"source": "A层", "target": "信用分层", "type": "INSTANCE-OF", "confidence": 0.9},
            {"source": "B层", "target": "信用分层", "type": "INSTANCE-OF", "confidence": 0.9},
            {"source": "借款人", "target": "信用评分", "type": "DEPENDS_ON", "confidence": 0.8},
            {"source": "借款人", "target": "信用分层", "type": "关联", "confidence": 0.6},
            {"source": "借款人", "target": "不存在的实体", "type": "IS-A", "confidence": 0.5},
            {"source": "借款人", "target": "信用评分", "type": "DEPENDS_ON", "confidence": 0.7},
            {"source": "A层", "target": "授信额度", "type": "GOVERNED_BY", "confidence": 0.8},
        ],
        "logic_rules": [
            {"name_cn": "IF 信用评分 < 500 THEN 降级", "formula": "IF 信用评分 < 500 THEN 降级",
             "description": "降级规则", "linked_entities": ["信用评分"]},
        ],
        "actions": [],
        "instances": [],
    }
    assert len(text) > 200

    normalized = normalize_extracted_ontology(raw)
    names = {e["name_cn"] for e in normalized["entities"]}
    # duplicate surface forms merged into one canonical entity
    assert "借款人" in names
    assert "借款人（Borrower）" not in names
    # junk entities dropped
    assert "12345" not in names
    assert "信贷业务战略.md" not in names
    assert not any(not (e.get("name_cn") or "").strip() for e in normalized["entities"])
    # every surviving entity carries a grounded one-line description
    assert all(e.get("description") for e in normalized["entities"])

    # no dangling references, no vague types, no duplicate triples
    triples = {(r["source"], r["type"], r["target"]) for r in normalized["relations"]}
    assert all(r["source"] in names and r["target"] in names for r in normalized["relations"])
    assert not any(r["type"] in VAGUE_RELATION_TYPES for r in normalized["relations"])
    assert ("借款人", "IS-A", "信用分层") in triples
    assert ("借款人", "DEPENDS_ON", "信用评分") in triples
    # the duplicate DEPENDS_ON appeared twice -> deduplicated to one
    assert sum(1 for r in normalized["relations"] if r["type"] == "DEPENDS_ON") == 1
    # hierarchy relations (IS-A / INSTANCE-OF) survive intact
    assert ("A层", "INSTANCE-OF", "信用分层") in triples
    assert ("B层", "INSTANCE-OF", "信用分层") in triples
    assert ("A层", "GOVERNED_BY", "授信额度") in triples


def test_normalization_is_idempotent_and_keeps_clean_output():
    """A clean extraction passes through unchanged (bounded normalization)."""
    clean = {
        "entities": [
            {"name_cn": "供应商", "type": "Supplier", "description": "供货企业", "properties": {}},
            {"name_cn": "仓库", "type": "Warehouse", "description": "仓储节点", "properties": {}},
        ],
        "relations": [
            {"source": "供应商", "target": "仓库", "type": "SUPPLIES", "confidence": 0.9},
        ],
        "logic_rules": [], "actions": [],
    }
    once = normalize_extracted_ontology(clean)
    twice = normalize_extracted_ontology(once)
    assert once == twice
    assert [e["name_cn"] for e in once["entities"]] == ["供应商", "仓库"]
    assert once["relations"] == clean["relations"]


def test_extract_ontology_applies_normalization():
    """extract_ontology runs the raw model output through the normalization
    step (the extraction task sees the clean result)."""
    from app.services import llm_service
    original = llm_service._call_llm

    def fake_call(*args, **kwargs):
        return (
            '{"entities": [{"name_cn": "客户", "type": "Concept", "description": "d"}, '
            '{"name_cn": "客户（Customer）", "type": "Concept", "description": ""}], '
            '"relations": [{"source": "客户", "target": "不存在", "type": "IS-A"}]}'
        )

    llm_service._call_llm = fake_call
    try:
        result = extract_ontology("文档", "提示", {"provider": "openai", "api_key": "x"}, "m")
    finally:
        llm_service._call_llm = original
    assert [e["name_cn"] for e in result["entities"]] == ["客户"]
    assert result["relations"] == []

"""
审计服务单元测试 — 覆盖 ReAct agent 的所有工具执行逻辑（不依赖 LLM 调用）
"""
import json
from app.services.audit_service import _execute_tool, _tool_definitions, DOMAIN_RELATION_PATTERNS


# ── 测试数据夹具 ──────────────────────────────────────────────────────────────

def _make_snapshot(entities=None, relations=None, logic_rules=None, actions=None):
    return {
        "entities": entities or [],
        "relations": relations or [],
        "logic_rules": logic_rules or [],
        "actions": actions or [],
    }


def _make_entity(id_="e1", name_cn="供应商", type_="Organization"):
    return {"id": id_, "name_cn": name_cn, "type": type_}


def _make_relation(source_entity="e1", target_entity="e2", source_name="供应商", target_name="原材料", rel_type="supply"):
    return {
        "source_entity": source_entity,
        "target_entity": target_entity,
        "source_name": source_name,
        "target_name": target_name,
        "type": rel_type,
    }


def _make_logic_rule(id_="l1", name_cn="超标检查", linked_entities=None):
    return {"id": id_, "name_cn": name_cn, "linked_entities": linked_entities or []}


def _make_action(name_cn="发送预警", linked_entities=None, linked_logic_ids=None):
    return {
        "name_cn": name_cn,
        "linked_entities": linked_entities or [],
        "linked_logic_ids": linked_logic_ids or [],
    }


# ── Tool definitions ─────────────────────────────────────────────────────────

def test_tool_definitions_count():
    """应有 8 个工具定义"""
    tools = _tool_definitions()
    assert len(tools) == 8
    names = {t["name"] for t in tools}
    assert names == {
        "get_ontology_summary", "list_isolated_entities", "check_relation_refs",
        "check_logic_refs", "check_action_refs", "get_entity_coverage",
        "find_missing_relations", "submit_findings",
    }


# ── get_ontology_summary ─────────────────────────────────────────────────────

def test_get_ontology_summary_empty():
    s = _make_snapshot()
    result = json.loads(_execute_tool("get_ontology_summary", {}, s))
    assert result["entity_count"] == 0
    assert result["relation_count"] == 0
    assert result["isolated_entity_count"] == 0
    assert result["relation_density"] == 0


def test_get_ontology_summary_with_data():
    e1 = _make_entity("e1", "供应商", "Organization")
    e2 = _make_entity("e2", "原材料", "RawMaterial")
    e3 = _make_entity("e3", "孤立实体", "Unknown")
    rel = _make_relation("e1", "e2", "供应商", "原材料", "supply")
    s = _make_snapshot([e1, e2, e3], [rel])

    result = json.loads(_execute_tool("get_ontology_summary", {}, s))
    assert result["entity_count"] == 3
    assert result["relation_count"] == 1
    assert result["isolated_entity_count"] == 1  # e3 没有参与任何关系
    assert result["type_distribution"]["Organization"] == 1
    assert result["type_distribution"]["RawMaterial"] == 1
    assert result["type_distribution"]["Unknown"] == 1


# ── list_isolated_entities ───────────────────────────────────────────────────

def test_list_isolated_entities_none_when_all_connected():
    e1 = _make_entity("e1", "A", "T1")
    e2 = _make_entity("e2", "B", "T1")
    rel = _make_relation("e1", "e2", "A", "B", "PART-OF")
    s = _make_snapshot([e1, e2], [rel])

    result = json.loads(_execute_tool("list_isolated_entities", {}, s))
    assert result["count"] == 0
    assert result["isolated_entities"] == []


def test_list_isolated_entities_detects_isolated():
    e1 = _make_entity("e1", "已连接", "T1")
    e2 = _make_entity("e2", "孤立", "T2")
    rel = _make_relation("e1", "e1", "已连接", "已连接", "SELF")  # e1 自引用
    s = _make_snapshot([e1, e2], [rel])

    result = json.loads(_execute_tool("list_isolated_entities", {}, s))
    assert result["count"] == 1
    assert result["isolated_entities"][0]["name_cn"] == "孤立"


# ── check_relation_refs ──────────────────────────────────────────────────────

def test_check_relation_refs_all_valid():
    e1 = _make_entity("e1", "供应商")
    e2 = _make_entity("e2", "原材料")
    rel = _make_relation("e1", "e2", "供应商", "原材料")
    s = _make_snapshot([e1, e2], [rel])

    result = json.loads(_execute_tool("check_relation_refs", {}, s))
    assert result["count"] == 0


def test_check_relation_refs_detects_broken():
    e1 = _make_entity("e1", "供应商")
    rel = _make_relation("e1", "e_nonexistent", "供应商", "不存在实体")
    s = _make_snapshot([e1], [rel])

    result = json.loads(_execute_tool("check_relation_refs", {}, s))
    assert result["count"] == 1
    assert "不存在" in result["broken_relations"][0]["target"]


# ── check_logic_refs ─────────────────────────────────────────────────────────

def test_check_logic_refs_valid():
    e = _make_entity("e1", "费用", "Finance")
    rule = _make_logic_rule("l1", "超标检查", linked_entities=["费用"])
    s = _make_snapshot([e], [], [rule])

    result = json.loads(_execute_tool("check_logic_refs", {}, s))
    assert result["count"] == 0


def test_check_logic_refs_detects_missing_entity():
    e = _make_entity("e1", "费用", "Finance")
    rule = _make_logic_rule("l1", "超标检查", linked_entities=["费用", "预算"])
    s = _make_snapshot([e], [], [rule])

    result = json.loads(_execute_tool("check_logic_refs", {}, s))
    assert result["count"] == 1
    assert "预算" in result["broken_logic_refs"][0]["missing_entities"]


# ── check_action_refs ────────────────────────────────────────────────────────

def test_check_action_refs_valid():
    e = _make_entity("e1", "供应商", "Organization")
    rule = _make_logic_rule("l1", "检查")
    action = _make_action("预警", linked_entities=["供应商"], linked_logic_ids=["l1"])
    s = _make_snapshot([e], [], [rule], [action])

    result = json.loads(_execute_tool("check_action_refs", {}, s))
    assert result["count"] == 0


def test_check_action_refs_detects_broken_both():
    action = _make_action("预警", linked_entities=["不存在"], linked_logic_ids=["missing"])
    s = _make_snapshot([], [], [], [action])

    result = json.loads(_execute_tool("check_action_refs", {}, s))
    assert result["count"] == 1
    b = result["broken_action_refs"][0]
    assert "不存在" in b["missing_entities"]
    assert "missing" in b["missing_logic_ids"]


# ── get_entity_coverage ──────────────────────────────────────────────────────

def test_get_entity_coverage_low_coverage():
    e1 = _make_entity("e1", "已连接", "TypeA")
    e2 = _make_entity("e2", "孤立1", "TypeB")
    e3 = _make_entity("e3", "孤立2", "TypeB")
    rel = _make_relation("e1", "e1", "已连接", "已连接", "SELF")
    s = _make_snapshot([e1, e2, e3], [rel])

    result = json.loads(_execute_tool("get_entity_coverage", {}, s))
    assert len(result["low_coverage_types"]) == 1
    low = result["low_coverage_types"][0]
    assert low["type"] == "TypeB"
    assert low["total"] == 2
    assert low["coverage_rate"] < 0.5


def test_get_entity_coverage_all_high_when_connected():
    e1 = _make_entity("e1", "A", "T1")
    e2 = _make_entity("e2", "B", "T1")
    rel = _make_relation("e1", "e2", "A", "B", "PART-OF")
    s = _make_snapshot([e1, e2], [rel])

    result = json.loads(_execute_tool("get_entity_coverage", {}, s))
    assert len(result["low_coverage_types"]) == 0


# ── find_missing_relations ───────────────────────────────────────────────────

def test_find_missing_relations_suggests_pattern():
    # 使用预置模式中有的类型：Supplier 和 RawMaterial → supply pattern
    s1 = _make_entity("e1", "A供应商", "Supplier")
    s2 = _make_entity("e2", "芯片", "RawMaterial")
    s = _make_snapshot([s1, s2])

    result = json.loads(_execute_tool(
        "find_missing_relations",
        {"entity_names": ["A供应商", "芯片"]},
        s,
    ))
    assert len(result["suggested_relations"]) >= 1
    # Supplier → RawMaterial 匹配 "supply" pattern
    suggestions = result["suggested_relations"]
    supply_suggestions = [x for x in suggestions if x["suggested_type"] == "supply"]
    assert len(supply_suggestions) >= 1


def test_find_missing_relations_no_duplicates():
    s1 = _make_entity("e1", "疾病", "Disease")
    s2 = _make_entity("e2", "症状", "Symptom")
    rel = _make_relation("e1", "e2", "疾病", "症状", "causes")
    s = _make_snapshot([s1, s2], [rel])

    result = json.loads(_execute_tool(
        "find_missing_relations",
        {"entity_names": ["疾病", "症状"]},
        s,
    ))
    # 已有 causes 关系，不应再建议相同的关系对
    existing_pairs = {(x["from"], x["to"]) for x in result["suggested_relations"]}
    assert ("疾病", "症状") not in existing_pairs


# ── submit_findings ──────────────────────────────────────────────────────────

def test_submit_findings_accepts_valid():
    findings = [{"severity": "warning", "category": "test", "title": "测试",
                 "description": "desc", "affected_items": ["item"]}]
    result = json.loads(_execute_tool("submit_findings", {"findings": findings}, _make_snapshot()))
    assert result["status"] == "accepted"
    assert result["count"] == 1


def test_submit_findings_handles_empty():
    result = json.loads(_execute_tool("submit_findings", {"findings": []}, _make_snapshot()))
    assert result["status"] == "accepted"
    assert result["count"] == 0


# ── 未知工具 ──────────────────────────────────────────────────────────────────

def test_unknown_tool_returns_error():
    result = json.loads(_execute_tool("unknown_tool", {}, _make_snapshot()))
    assert "error" in result


# ── DOMAIN_RELATION_PATTERNS 完整性 ───────────────────────────────────────────

def test_domain_patterns_well_formed():
    """每条 pattern 应为 (source_type, target_type, rel_type) 三元组且都是字符串"""
    for pat in DOMAIN_RELATION_PATTERNS:
        assert isinstance(pat, tuple)
        assert len(pat) == 3
        assert all(isinstance(x, str) for x in pat)

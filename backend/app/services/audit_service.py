"""
ReAct 本体质量审查 Agent

使用 LLM tool calling 多步推理，检查本体的语义质量问题。
支持 OpenAI 和 Anthropic 两种 provider，适配各自的 tool calling 格式差异。
"""

import json
import uuid
from typing import Callable

# ── 预置领域关系模式（用于 find_missing_relations 工具的规则推断）──────────
DOMAIN_RELATION_PATTERNS = [
    ("Supplier", "Organization", "IS-A"),
    ("Supplier", "RawMaterial", "supply"),
    ("RawMaterial", "Component", "PART-OF"),
    ("Component", "Product", "PART-OF"),
    ("Disease", "Symptom", "causes"),
    ("Drug", "Disease", "treats"),
    ("Drug", "Symptom", "treats"),
    ("Patient", "Disease", "INSTANCE-OF"),
    ("Doctor", "Patient", "treats"),
    ("Hospital", "Department", "PART-OF"),
    ("Organization", "Employee", "PART-OF"),
    ("Process", "Step", "PART-OF"),
    ("Rule", "Entity", "关联"),
    ("Action", "Rule", "关联"),
    ("Category", "Item", "PART-OF"),
]

SYSTEM_PROMPT = """你是一个本体质量审查专家。你的任务是通过多步推理，系统地检查给定本体的语义质量问题。

你有以下工具可以调用：
- get_ontology_summary：获取本体统计摘要（实体数、类型分布、关系密度等）
- list_isolated_entities：找出没有参与任何关系的孤立实体
- check_relation_refs：检查所有关系的 source/target 是否能解析到已知实体
- check_logic_refs：检查逻辑规则的 linked_entities 是否全部指向已知实体
- check_action_refs：检查动作的 linked_entities 和 linked_logic_ids 是否全部可解析
- get_entity_coverage：统计各类型实体参与关系的覆盖率，找出低覆盖类型
- find_missing_relations：根据实体类型，推断是否存在明显缺失的关系
- submit_findings：提交最终审查结果（调用此工具将终止审查）

审查策略：
1. 先调用 get_ontology_summary 了解整体规模
2. 依次调用各检查工具发现问题
3. 对发现的问题进行追查（如孤立实体 → find_missing_relations）
4. 收集足够信息后，调用 submit_findings 提交结论

findings 中每条问题的格式：
{
  "severity": "critical | warning | info",
  "category": "isolated_entity | broken_ref | missing_relation | low_coverage | action_unreachable | other",
  "title": "简短标题（不超过50字）",
  "description": "详细描述和修复建议",
  "affected_items": ["受影响的实体/规则/动作名称列表"]
}

请务必调用 submit_findings 来结束审查，不要直接输出文字结论。"""


# ── 工具定义 ──────────────────────────────────────────────────────────────────

def _tools_for_openai() -> list:
    base = _tool_definitions()
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t["description"],
                "parameters": t["input_schema"],
            }
        }
        for t in base
    ]


def _tools_for_anthropic() -> list:
    return _tool_definitions()


def _tool_definitions() -> list:
    return [
        {
            "name": "get_ontology_summary",
            "description": "获取本体统计摘要：实体总数、各类型分布、关系总数、关系密度、孤立实体预估数量",
            "input_schema": {"type": "object", "properties": {}, "required": []},
        },
        {
            "name": "list_isolated_entities",
            "description": "找出没有出现在任何关系（source 或 target）中的孤立实体列表，返回 name_cn 和 type",
            "input_schema": {"type": "object", "properties": {}, "required": []},
        },
        {
            "name": "check_relation_refs",
            "description": "遍历所有关系，检查 source/target 实体 ID 是否在已知实体集合中，返回断链的关系列表",
            "input_schema": {"type": "object", "properties": {}, "required": []},
        },
        {
            "name": "check_logic_refs",
            "description": "遍历所有逻辑规则，检查 linked_entities 中的名称是否全部指向已知实体，返回断链的引用",
            "input_schema": {"type": "object", "properties": {}, "required": []},
        },
        {
            "name": "check_action_refs",
            "description": "遍历所有动作，检查 linked_entities 和 linked_logic_ids 是否可解析，返回断链列表",
            "input_schema": {"type": "object", "properties": {}, "required": []},
        },
        {
            "name": "get_entity_coverage",
            "description": "按实体类型统计参与关系的覆盖率，返回覆盖率低于 50% 的类型及其实体列表",
            "input_schema": {"type": "object", "properties": {}, "required": []},
        },
        {
            "name": "find_missing_relations",
            "description": "给定一组实体名称，根据预置的领域关系模式，推断哪些实体之间可能缺少关系",
            "input_schema": {
                "type": "object",
                "properties": {
                    "entity_names": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "要检查的实体 name_cn 列表",
                    }
                },
                "required": ["entity_names"],
            },
        },
        {
            "name": "submit_findings",
            "description": "提交最终审查结果，调用此工具将终止审查循环",
            "input_schema": {
                "type": "object",
                "properties": {
                    "findings": {
                        "type": "array",
                        "description": "问题列表",
                        "items": {
                            "type": "object",
                            "properties": {
                                "severity": {"type": "string", "enum": ["critical", "warning", "info"]},
                                "category": {"type": "string"},
                                "title": {"type": "string"},
                                "description": {"type": "string"},
                                "affected_items": {"type": "array", "items": {"type": "string"}},
                            },
                            "required": ["severity", "category", "title", "description", "affected_items"],
                        },
                    }
                },
                "required": ["findings"],
            },
        },
    ]


# ── 工具执行（纯 Python，不调用 LLM）────────────────────────────────────────

def _execute_tool(tool_name: str, tool_args: dict, snapshot: dict) -> str:
    entities = snapshot["entities"]
    relations = snapshot["relations"]
    logic_rules = snapshot["logic_rules"]
    actions = snapshot["actions"]

    entity_ids = {e["id"] for e in entities}
    entity_names = {e["name_cn"] for e in entities}
    logic_ids = {r["id"] for r in logic_rules}

    if tool_name == "get_ontology_summary":
        in_relation = set()
        for rel in relations:
            in_relation.add(rel["source_entity"])
            in_relation.add(rel["target_entity"])
        isolated_count = sum(1 for e in entities if e["id"] not in in_relation)

        type_dist: dict = {}
        for e in entities:
            t = e.get("type") or "Unknown"
            type_dist[t] = type_dist.get(t, 0) + 1

        n = len(entities)
        density = round(len(relations) / (n * (n - 1)), 4) if n > 1 else 0

        return json.dumps({
            "entity_count": n,
            "relation_count": len(relations),
            "logic_rule_count": len(logic_rules),
            "action_count": len(actions),
            "type_distribution": type_dist,
            "relation_density": density,
            "isolated_entity_count": isolated_count,
        }, ensure_ascii=False)

    elif tool_name == "list_isolated_entities":
        in_relation = set()
        for rel in relations:
            in_relation.add(rel["source_entity"])
            in_relation.add(rel["target_entity"])
        isolated = [
            {"name_cn": e["name_cn"], "type": e.get("type", "Unknown")}
            for e in entities if e["id"] not in in_relation
        ]
        return json.dumps({"isolated_entities": isolated, "count": len(isolated)}, ensure_ascii=False)

    elif tool_name == "check_relation_refs":
        broken = []
        for rel in relations:
            issues = []
            if rel["source_entity"] not in entity_ids:
                issues.append(f"source '{rel['source_name']}' 不存在")
            if rel["target_entity"] not in entity_ids:
                issues.append(f"target '{rel['target_name']}' 不存在")
            if issues:
                broken.append({
                    "relation_type": rel["type"],
                    "source": rel["source_name"],
                    "target": rel["target_name"],
                    "issues": issues,
                })
        return json.dumps({"broken_relations": broken, "count": len(broken)}, ensure_ascii=False)

    elif tool_name == "check_logic_refs":
        broken = []
        for rule in logic_rules:
            missing = [e for e in rule.get("linked_entities", []) if e not in entity_names]
            if missing:
                broken.append({"rule_name": rule["name_cn"], "missing_entities": missing})
        return json.dumps({"broken_logic_refs": broken, "count": len(broken)}, ensure_ascii=False)

    elif tool_name == "check_action_refs":
        broken = []
        for action in actions:
            missing_entities = [e for e in action.get("linked_entities", []) if e not in entity_names]
            missing_logic = [lid for lid in action.get("linked_logic_ids", []) if lid not in logic_ids]
            if missing_entities or missing_logic:
                broken.append({
                    "action_name": action["name_cn"],
                    "missing_entities": missing_entities,
                    "missing_logic_ids": missing_logic,
                })
        return json.dumps({"broken_action_refs": broken, "count": len(broken)}, ensure_ascii=False)

    elif tool_name == "get_entity_coverage":
        in_relation: dict = {}
        for rel in relations:
            in_relation[rel["source_entity"]] = True
            in_relation[rel["target_entity"]] = True

        by_type: dict = {}
        for e in entities:
            t = e.get("type") or "Unknown"
            if t not in by_type:
                by_type[t] = {"total": 0, "in_relation": 0, "names": []}
            by_type[t]["total"] += 1
            if e["id"] in in_relation:
                by_type[t]["in_relation"] += 1
            else:
                by_type[t]["names"].append(e["name_cn"])

        low_coverage = []
        for t, stats in by_type.items():
            rate = stats["in_relation"] / stats["total"] if stats["total"] > 0 else 0
            if rate < 0.5:
                low_coverage.append({
                    "type": t,
                    "total": stats["total"],
                    "in_relation": stats["in_relation"],
                    "coverage_rate": round(rate, 2),
                    "isolated_names": stats["names"][:10],
                })
        return json.dumps({"low_coverage_types": low_coverage}, ensure_ascii=False)

    elif tool_name == "find_missing_relations":
        names = tool_args.get("entity_names", [])
        name_to_type = {e["name_cn"]: e.get("type", "") for e in entities}
        existing_pairs = {(rel["source_name"], rel["target_name"]) for rel in relations}

        suggestions = []
        for name_a in names:
            type_a = name_to_type.get(name_a, "")
            for name_b in names:
                if name_a == name_b:
                    continue
                type_b = name_to_type.get(name_b, "")
                for ta, tb, rel_type in DOMAIN_RELATION_PATTERNS:
                    if ta.lower() in type_a.lower() and tb.lower() in type_b.lower():
                        if (name_a, name_b) not in existing_pairs:
                            suggestions.append({
                                "from": name_a, "to": name_b,
                                "suggested_type": rel_type,
                                "reason": f"{type_a} → {rel_type} → {type_b} 是常见模式",
                            })
        return json.dumps({"suggested_relations": suggestions[:20]}, ensure_ascii=False)

    elif tool_name == "submit_findings":
        return json.dumps({"status": "accepted", "count": len(tool_args.get("findings", []))})

    return json.dumps({"error": f"Unknown tool: {tool_name}"})


# ── LLM 调用（OpenAI / Anthropic 各自实现）──────────────────────────────────

def _call_openai_with_tools(api_key: str, api_base: str | None, model_name: str,
                             system: str, turns: list) -> dict:
    import openai
    kwargs: dict = {"api_key": api_key}
    if api_base:
        kwargs["base_url"] = api_base
    client = openai.OpenAI(**kwargs)

    messages = [{"role": "system", "content": system}]
    for turn in turns:
        if turn["role"] == "tool_result":
            messages.append({
                "role": "tool",
                "tool_call_id": turn["tool_call_id"],
                "content": turn["content"],
            })
        elif turn["role"] == "assistant_tool_call":
            messages.append({
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": turn["tool_call_id"],
                    "type": "function",
                    "function": {
                        "name": turn["tool_name"],
                        "arguments": json.dumps(turn["tool_args"]),
                    },
                }],
            })
        else:
            messages.append({"role": turn["role"], "content": turn["content"]})

    resp = client.chat.completions.create(
        model=model_name,
        messages=messages,
        tools=_tools_for_openai(),
        tool_choice="auto",
        max_tokens=4096,
        timeout=120,
    )

    choice = resp.choices[0]
    if choice.finish_reason == "tool_calls" and choice.message.tool_calls:
        tc = choice.message.tool_calls[0]
        try:
            args = json.loads(tc.function.arguments)
        except Exception:
            args = {}
        return {
            "type": "tool_call",
            "tool_name": tc.function.name,
            "tool_args": args,
            "tool_call_id": tc.id,
            "thought": choice.message.content or "",
        }

    return {"type": "text", "content": choice.message.content or ""}


def _call_anthropic_with_tools(api_key: str, model_name: str,
                                system: str, turns: list) -> dict:
    import anthropic
    client = anthropic.Anthropic(api_key=api_key)

    messages: list = []
    i = 0
    while i < len(turns):
        turn = turns[i]
        if turn["role"] == "user":
            messages.append({"role": "user", "content": turn["content"]})
        elif turn["role"] == "assistant_tool_call":
            messages.append({
                "role": "assistant",
                "content": [
                    *([{"type": "text", "text": turn.get("thought", "")}] if turn.get("thought") else []),
                    {
                        "type": "tool_use",
                        "id": turn["tool_call_id"],
                        "name": turn["tool_name"],
                        "input": turn["tool_args"],
                    },
                ],
            })
            # Anthropic requires tool_result immediately after tool_use in a user message
            if i + 1 < len(turns) and turns[i + 1]["role"] == "tool_result":
                nxt = turns[i + 1]
                messages.append({
                    "role": "user",
                    "content": [{
                        "type": "tool_result",
                        "tool_use_id": nxt["tool_call_id"],
                        "content": nxt["content"],
                    }],
                })
                i += 1
        i += 1

    resp = client.messages.create(
        model=model_name,
        max_tokens=4096,
        system=system,
        messages=messages,
        tools=_tools_for_anthropic(),
    )

    thought = ""
    for block in resp.content:
        if hasattr(block, "type") and block.type == "text":
            thought = block.text
        if hasattr(block, "type") and block.type == "tool_use":
            return {
                "type": "tool_call",
                "tool_name": block.name,
                "tool_args": block.input,
                "tool_call_id": block.id,
                "thought": thought,
            }

    text = " ".join(
        block.text for block in resp.content
        if hasattr(block, "type") and block.type == "text"
    )
    return {"type": "text", "content": text}


# ── 主入口 ───────────────────────────────────────────────────────────────────

def run_react_audit(
    ontology_snapshot: dict,
    model_config: dict,
    model_name: str,
    on_step: Callable[[int, int], None] | None = None,
    on_trace_step: Callable[[list], None] | None = None,
    max_steps: int = 12,
) -> tuple[list, list]:
    """
    Run the ReAct audit loop.
    Returns (findings, trace).
    """
    provider = model_config.get("provider", "openai")
    api_key = model_config["api_key"]
    api_base = model_config.get("api_base")

    summary_str = _execute_tool("get_ontology_summary", {}, ontology_snapshot)

    turns: list = [
        {
            "role": "user",
            "content": f"请开始审查本体质量。本体基本信息：\n{summary_str}\n\n请系统地检查所有质量维度，最后调用 submit_findings 提交结论。",
        }
    ]

    trace: list = []
    findings: list = []

    for step in range(max_steps):
        if on_step:
            on_step(step, max_steps)

        try:
            if provider == "anthropic":
                response = _call_anthropic_with_tools(api_key, model_name, SYSTEM_PROMPT, turns)
            else:
                response = _call_openai_with_tools(api_key, api_base, model_name, SYSTEM_PROMPT, turns)
        except Exception as e:
            trace.append({"step": step, "error": str(e)})
            if on_trace_step:
                on_trace_step(trace)
            break

        if response["type"] == "tool_call":
            tool_name = response["tool_name"]
            tool_args = response["tool_args"]
            tool_call_id = response.get("tool_call_id", str(uuid.uuid4()))
            thought = response.get("thought", "")

            observation = _execute_tool(tool_name, tool_args, ontology_snapshot)

            trace.append({
                "step": step,
                "thought": thought,
                "tool_name": tool_name,
                "tool_args": tool_args,
                "observation": observation,
            })
            if on_trace_step:
                on_trace_step(trace)

            turns.append({
                "role": "assistant_tool_call",
                "tool_name": tool_name,
                "tool_args": tool_args,
                "tool_call_id": tool_call_id,
                "thought": thought,
            })
            turns.append({
                "role": "tool_result",
                "tool_call_id": tool_call_id,
                "content": observation,
            })

            if tool_name == "submit_findings":
                findings = tool_args.get("findings", [])
                break

        else:
            trace.append({"step": step, "text": response.get("content", "")})
            if on_trace_step:
                on_trace_step(trace)
            break

    return findings, trace

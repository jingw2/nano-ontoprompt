"""DAG 编译器：将 Pipeline definition (nodes/edges) 编译为执行计划"""
from __future__ import annotations
import logging

logger = logging.getLogger(__name__)

# 节点类型映射到执行阶段
NODE_PHASE_ORDER = {
    "connector": 1,
    "storage": 2,
    "transform": 3,
    "output": 4,
}


def compile_definition(definition: dict | None) -> dict:
    """
    编译 definition DSL 为可执行的阶段计划。

    输入: definition = {"nodes": [...], "edges": [...]}
    输出: {
        "phases": [{"name": "connector", "node_ids": [...]}, ...],
        "linear": bool,  # 是否线性执行
        "execution_order": [node_id, ...],  # 拓扑排序后的执行顺序
    }
    """
    if not definition:
        return {"phases": [], "linear": True, "execution_order": []}

    nodes = definition.get("nodes", [])
    edges = definition.get("edges", [])

    if not nodes:
        return {"phases": [], "linear": True, "execution_order": []}

    # 按阶段分组
    phases: dict[int, dict] = {}
    node_map = {n["id"]: n for n in nodes}

    for n in nodes:
        ntype = n.get("type", "")
        phase = NODE_PHASE_ORDER.get(ntype, 99)
        if phase not in phases:
            phases[phase] = {"name": ntype, "node_ids": []}
        phases[phase]["node_ids"].append(n["id"])

    sorted_phases = [phases[k] for k in sorted(phases.keys())]

    # 拓扑排序：找出所有没有入边的节点开始
    in_degree = {n["id"]: 0 for n in nodes}
    adjacency = {n["id"]: [] for n in nodes}

    for e in edges:
        src = e.get("source", "")
        tgt = e.get("target", "")
        if src in adjacency and tgt in in_degree:
            adjacency[src].append(tgt)
            in_degree[tgt] = in_degree.get(tgt, 0) + 1

    # Kahn 拓扑排序
    queue = [nid for nid, deg in in_degree.items() if deg == 0]
    execution_order = []

    while queue:
        nid = queue.pop(0)
        execution_order.append(nid)
        for neighbor in adjacency.get(nid, []):
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                queue.append(neighbor)

    # 如果有环，未排序的节点放到最后
    remaining = [nid for nid in node_map if nid not in execution_order]
    execution_order.extend(remaining)

    is_linear = (len(sorted_phases) <= 1 or
                 all(len(p["node_ids"]) <= 1 for p in sorted_phases))

    return {
        "phases": sorted_phases,
        "linear": is_linear,
        "execution_order": execution_order,
    }

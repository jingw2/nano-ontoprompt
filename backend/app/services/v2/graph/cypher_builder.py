"""安全的 Cypher 查询构建器 — 防注入"""
from __future__ import annotations
import re


LABEL_PATTERN = re.compile(r'^[A-Za-z][A-Za-z0-9_]*$')


def validate_label(label: str) -> str:
    """校验 Neo4j 标签名 (防注入)"""
    if not LABEL_PATTERN.match(label):
        raise ValueError(f"Invalid Neo4j label: {label!r}")
    return label


def build_match_by_id(label: str, node_id: str) -> tuple[str, dict]:
    label = validate_label(label)
    return (
        f"MATCH (n:{label} {{id: $id}}) RETURN n",
        {"id": node_id},
    )


def build_neighbors(label: str, node_id: str, depth: int = 1) -> tuple[str, dict]:
    label = validate_label(label)
    depth = max(1, min(depth, 5))  # 最多 5 层
    return (
        f"MATCH (n:{label} {{id: $id}})-[r*1..{depth}]-(m) RETURN n, r, m LIMIT 100",
        {"id": node_id},
    )


def build_shortest_path(src_id: str, tgt_id: str) -> tuple[str, dict]:
    return (
        "MATCH (s {id: $src}), (t {id: $tgt}), p = shortestPath((s)-[*]-(t)) RETURN p",
        {"src": src_id, "tgt": tgt_id},
    )


_WRITE_KEYWORD = re.compile(
    r"\b(CREATE|MERGE|DELETE|DETACH|SET|REMOVE|DROP|LOAD\s+CSV)\b", re.IGNORECASE
)


def validate_readonly_cypher(query: str) -> str | None:
    """校验用户 Cypher: 只读 + 必须按 ontology_id 过滤。

    返回错误信息, 合法返回 None。词边界匹配避免误伤含 SET/DROP 子串的
    属性名 (如 asset、backdrop)。
    """
    m = _WRITE_KEYWORD.search(query)
    if m:
        return f"Write queries not allowed via this endpoint: {m.group(1).upper()}"
    if "ontology_id" not in query:
        return ("Query must filter by ontology_id, e.g. "
                "MATCH (n) WHERE n.ontology_id = $ontology_id RETURN n LIMIT 25")
    return None

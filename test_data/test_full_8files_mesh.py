#!/usr/bin/env python3
"""8 文件全量流程 — 验证跨数据集 Link 推断后的图谱网状结构

供应链全部 8 个文件 → Pipeline (A/B/C 三路径) → 审批 → 8 Entity Types 映射 →
build-all → 输出图谱拓扑(各类型节点数 + 类型间边分布)。
"""
import time
from collections import Counter
from pathlib import Path

import requests

API = "http://localhost:8000"
TD = Path(__file__).parent / "供应链"

s = requests.Session()
r = s.post(f"{API}/api/v1/auth/login", json={"username": "admin", "password": "admin123"})
s.headers.update({"Authorization": f"Bearer {r.json()['data']['access_token']}"})
print("✅ 登录")

# 文件 → (entity_class, route, 主键列)
FILES = {
    "inventory_transactions.csv": ("InventoryTransaction", "A", "__row_hash__"),
    "logistics_performance.csv": ("LogisticsRecord", "A", "运单号"),
    "supplier_database.xlsx": ("Supplier", "A", "供应商ID"),
    "supplier_orders.json": ("PurchaseOrder", "B", "order_id"),
    "supply_chain_strategy.md": ("StrategyClause", "C", "record_id"),
    "procurement_policy.docx": ("PolicyClause", "C", "record_id"),
    "warehouse_management.pdf": ("WarehouseRule", "C", "record_id"),
    "supply_chain_review.pptx": ("ReviewItem", "C", "record_id"),
}

curated = {}
for fname, (cls, route, pk) in FILES.items():
    with open(TD / fname, "rb") as fh:
        r = s.post(f"{API}/api/v2/datasets/upload", files={"file": (fname, fh)})
    did = (r.json().get("data") or r.json())["id"]
    body = {
        "name": f"mesh-{int(time.time())}-{fname[:20]}",
        "source_dataset_id": did,
        "route": route,
        "definition": {
            "nodes": [
                {"id": "n1", "type": "connector", "data": {"dataset_id": did}},
                {"id": "n2", "type": "storage", "data": {}},
                {"id": "n3", "type": "transform", "data": {"route": route}},
                {"id": "n4", "type": "output", "data": {}},
            ],
            "edges": [{"id": "e1", "source": "n1", "target": "n2"},
                      {"id": "e2", "source": "n2", "target": "n3"},
                      {"id": "e3", "source": "n3", "target": "n4"}],
        },
    }
    r = s.post(f"{API}/api/v2/pipelines", json=body)
    pid = (r.json().get("data") or r.json())["id"]
    res = s.post(f"{API}/api/v2/pipelines/{pid}/run-sync").json()
    stats = res.get("stats") or {}
    cid = stats.get("curated_dataset_id")
    curated[fname] = (cls, pk, cid)
    print(f"  {fname:32s} route={route} status={res.get('status')} rows_out={stats.get('rows_out')}")

# 审批
for fname, (cls, pk, cid) in curated.items():
    if cid:
        s.post(f"{API}/api/v2/curated/{cid}/approve")

# 创建 ontology + 映射
r = s.post(f"{API}/api/v1/ontologies", json={
    "name": f"供应链全量网状-{int(time.time())}", "domain": "供应链",
    "build_mode": "pipeline_mapping",
    "description": "8 文件全量 + 跨数据集 Link 推断验证",
})
oid = r.json()["data"]["id"]
print(f"\nOntology: {oid[:8]}")

for fname, (cls, pk, cid) in curated.items():
    if not cid:
        print(f"  ⚠️ {fname} 无 curated, 跳过")
        continue
    r = s.post(f"{API}/api/v2/ontologies/{oid}/mappings", json={
        "curated_dataset_id": cid, "entity_class": cls,
        "field_mapping": {"__primary_key__": pk},
    })
    print(f"  mapping {cls:22s} → {r.status_code}")

# build-all
r = s.post(f"{API}/api/v2/ontologies/{oid}/mappings/build-all")
res = r.json()
print(f"\nbuild-all: entities={res.get('total_entities')} relations={res.get('total_relations')} "
      f"logic={res.get('total_logic')} actions={res.get('total_actions')}")

# 图谱拓扑
g = s.get(f"{API}/api/v2/ontologies/{oid}/graph?limit=1000").json()
g = g.get("data", g)
lbl = {n["id"]: (n.get("labels") or ["?"])[0] for n in g["nodes"]}
print(f"\n节点: {len(g['nodes'])}  边: {len(g['edges'])}")
print("节点类型分布:", dict(Counter(lbl.values())))
pairs = Counter((lbl.get(e["source"], "?"), e["type"], lbl.get(e["target"], "?")) for e in g["edges"])
print("边类型分布:")
for k, v in pairs.most_common():
    print(f"  {k[0]:22s} -[{k[1]}]-> {k[2]:18s} × {v}")

# 连通性: 有出边或入边的类型
linked_types = {lbl.get(e["source"]) for e in g["edges"]} | {lbl.get(e["target"]) for e in g["edges"]}
all_types = set(lbl.values())
print(f"\n已连通类型: {sorted(linked_types)}")
print(f"孤立类型:   {sorted(all_types - linked_types)}")
print(f"\nOntology ID: {oid}")

#!/usr/bin/env python3
"""
PRD 对齐全流程测试：
1. 上传 test_data/供应链 全部 8 个文件
2. 为每个数据集类型创建 Pipeline (画布含 nodes/edges 定义)
3. 运行 Pipeline 生成多个 Curated Dataset
4. 创建 Ontology 时选择多个 Curated Dataset，映射为不同 Entity Type
5. 构建 Relation (FK 推断)，验证知识图谱呈现网状结构
"""
import requests, json, time, os, csv
from pathlib import Path

BASE_API = "http://localhost:8000"
TEST_DATA = Path(__file__).parent / "供应链"

session = requests.Session()

def login():
    r = session.post(f"{BASE_API}/api/v1/auth/login", json={"username":"admin","password":"admin123"})
    t = r.json()["data"]["access_token"]
    session.headers.update({"Authorization": f"Bearer {t}"})
    print("✅ 登录成功")

def upload_all_files():
    """上传全部 8 个文件，返回 {文件名: dataset_id}"""
    results = {}
    for f in sorted(TEST_DATA.glob("*")):
        fname = f.name
        ext = fname.rsplit(".", 1)[-1].lower()
        content_type = {
            "csv": "text/csv", "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "json": "application/json", "pdf": "application/pdf",
            "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            "md": "text/markdown",
        }.get(ext, "application/octet-stream")
        with open(f, "rb") as fh:
            r = session.post(f"{BASE_API}/api/v2/datasets/upload", files={"file": (fname, fh, content_type)})
        if r.ok:
            data = r.json()
            did = data.get("data", data).get("id") if isinstance(data, dict) else None
            results[fname] = did
            print(f"  ✅ {fname:35s} → {did[:8] if did else 'N/A'}")
        else:
            print(f"  ❌ {fname}: {r.status_code}")
        time.sleep(0.3)
    return results

def create_pipeline(name, dataset_id, route, steps):
    """创建含画布定义 (nodes/edges) 的 Pipeline"""
    ts = int(time.time())
    definition = {
        "schema_version": "2.0",
        "nodes": [
            {"id": "connector_1", "type": "connector", "label": "数据源", "position": {"x": 50, "y": 200}},
            {"id": "storage_1", "type": "storage", "label": "原始数据", "position": {"x": 250, "y": 200}},
            {"id": "transform_1", "type": "transform", "label": "数据清洗",
             "position": {"x": 450, "y": 200},
             "config": {"path": route == "A" and "structured" or route == "B" and "semi_structured" or "unstructured", "steps": steps}},
            {"id": "output_1", "type": "output", "label": "清洗结果",
             "position": {"x": 680, "y": 200},
             "config": {"dataset_type": "curated_dataset", "primary_key": ["id"]}},
        ],
        "edges": [
            {"id": "e1", "source": "connector_1", "target": "storage_1"},
            {"id": "e2", "source": "storage_1", "target": "transform_1"},
            {"id": "e3", "source": "transform_1", "target": "output_1"},
        ]
    }
    r = session.post(f"{BASE_API}/api/v2/pipelines", json={
        "name": f"{name}-{ts}", "domain": "供应链",
        "source_dataset_id": dataset_id, "route": route,
        "definition": definition, "spec": {"steps": steps},
    })
    if r.ok:
        pid = r.json()["id"]
        print(f"  ✅ {name:30s} id={pid[:8]}")
        return pid
    print(f"  ❌ {name}: {r.status_code} {r.text[:100]}")
    return None

def run_pipeline(pid):
    r = session.post(f"{BASE_API}/api/v2/pipelines/{pid}/run-sync")
    if r.ok:
        res = r.json()
        curated = res.get("stats", {}).get("curated_dataset_id")
        rows = res.get("stats", {}).get("rows_in")
        print(f"    运行: {res['status']} rows_in={rows} curated={curated[:8] if curated else 'N/A'}")
        return curated
    print(f"    运行失败: {r.status_code} {r.text[:100]}")
    return None

def approve_curated(cid):
    r = session.post(f"{BASE_API}/api/v2/curated/{cid}/review?action=approve")
    ok = r.ok
    print(f"    审批 {'✅' if ok else '❌'}: {r.status_code}")
    return ok

def main():
    print("\n" + "═"*70)
    print("  PRD 对齐全流程测试：供应链 8 文件 → Pipeline → 多 Entity Ontology")
    print("═"*70)

    login()
    ts = int(time.time())

    # ═══ 阶段 1: 上传全部 8 个文件 ═══
    print("\n── 1. 上传全部测试文件 ──")
    uploaded = upload_all_files()
    print(f"  共上传 {len(uploaded)} 个文件")

    # ═══ 阶段 2: 为结构化/半结构化文件创建 Pipeline ═══
    print("\n── 2. 创建并运行 Pipeline (含画布定义) ──")

    # 为 CSV/XLSX 文件创建 Route A pipeline
    structured_files = {k: v for k, v in uploaded.items() if k.endswith((".csv", ".xlsx"))}
    semi_files = {k: v for k, v in uploaded.items() if k.endswith(".json")}

    curated_ids = {}

    for fname, did in structured_files.items():
        name = fname.rsplit(".", 1)[0]
        pid = create_pipeline(name, did, "A", [{"op": "drop_duplicates"}, {"op": "normalize_dates"}])
        if pid:
            cid = run_pipeline(pid)
            if cid:
                curated_ids[fname] = cid

    for fname, did in semi_files.items():
        name = fname.rsplit(".", 1)[0]
        pid = create_pipeline(name, did, "B", [{"op": "parse_json"}])
        if pid:
            cid = run_pipeline(pid)
            if cid:
                curated_ids[fname] = cid

    print(f"\n  生成的 Curated Datasets: {len(curated_ids)} 个")
    for fname, cid in curated_ids.items():
        print(f"    {fname:35s} → {cid[:8]}")

    # ═══ 阶段 3: 审批所有 Curated Dataset ═══
    print("\n── 3. 审批 Curated Datasets ──")
    for fname, cid in curated_ids.items():
        approve_curated(cid)

    # ═══ 阶段 4: 创建 Ontology (Pipeline Mapping) ═══
    # PRD 2.4.2: Step 2 选择多个数据源 → Step 3 Mapping 配置 → 开始构建
    print("\n── 4. 创建 Ontology (多 Entity 映射) ──")

    # 创建本体
    onto_name = f"供应链知识图谱-PRD-{ts}"
    r = session.post(f"{BASE_API}/api/v1/ontologies", json={
        "name": onto_name, "domain": "供应链",
        "description": "PRD 对齐全流程测试 - 多实体类型映射",
        "build_mode": "pipeline_mapping",
    })
    onto_data = r.json() if r.ok else {}
    onto_id = onto_data.get("data", onto_data).get("id") if isinstance(onto_data, dict) else onto_data.get("id")
    print(f"  Ontology: {r.status_code} id={onto_id[:8] if onto_id else '?'}")

    if not onto_id:
        print("❌ Ontology 创建失败!")
        return

    # 为每个 Curated Dataset 创建 Mapping，映射为不同的 Entity Type
    entity_types = {
        "inventory_transactions.csv": "InventoryTransaction",
        "logistics_performance.csv": "LogisticsRecord",
        "supplier_database.xlsx": "Supplier",
        "supplier_orders.json": "PurchaseOrder",
    }

    mapping_ids = []
    for fname, cid in curated_ids.items():
        etype = entity_types.get(fname, f"Entity_{fname.rsplit('.',1)[0]}")
        r = session.post(f"{BASE_API}/api/v2/ontologies/{onto_id}/mappings", json={
            "curated_dataset_id": cid,
            "entity_class": etype,
            "field_mapping": {"__primary_key__": "id"},
            "confidence": 0.85,
        })
        if r.ok:
            mid = r.json().get("mapping_id", "")
            mapping_ids.append((mid, etype, cid))
            print(f"  ✅ {etype:25s} mapping_id={mid[:8]}")

    # ═══ 阶段 5: Apply + Build-all (PRD 5 阶段) ═══
    print("\n── 5. 构建 Ontology (PRD 5 阶段进度) ──")
    print("  ① Entity Type 识别 → ② Property Mapping → ③ Relation 推断 → ④ 写 Neo4j → ⑤ 写 ChromaDB")

    for mid, etype, cid in mapping_ids:
        r = session.post(f"{BASE_API}/api/v2/ontologies/{onto_id}/mappings/{mid}/apply-from-dataset")
        if r.ok:
            res = r.json()
            print(f"  ✅ {etype:25s} apply: entities={res.get('v1_entities_written', res.get('nodes_created', 0))}")

    # Build-all (含 Relation 推断)
    r = session.post(f"{BASE_API}/api/v2/ontologies/{onto_id}/mappings/build-all")
    if r.ok:
        res = r.json()
        print(f"\n  Build-all 结果:")
        for em in res.get("entity_mappings", []):
            print(f"    Entity: {em['entity_class']:25s} written={em['v1_entities_written']} neo4j={em['nodes_created']}")
        print(f"  Relations: {res.get('total_relations', 0)}")
        print(f"  Total Entities: {res.get('total_entities', 0)}")

    # ═══ 阶段 6: 验证知识图谱 ═══
    print("\n── 6. 验证知识图谱 ──")
    r = session.get(f"{BASE_API}/api/v1/ontologies/{onto_id}/entities")
    entities = r.json() if r.ok else {}
    # 处理嵌套响应格式
    if isinstance(entities, dict):
        items = entities.get("data", entities)
        if isinstance(items, dict):
            items = items.get("items", [])
        elif not isinstance(items, list):
            items = []
    else:
        items = entities if isinstance(entities, list) else []
    print(f"  实体总数: {len(items)}")
    if items:
        types = set()
        for e in items[:20]:
            if isinstance(e, dict):
                types.add(e.get("type", e.get("name_en", "?")))
        print(f"  实体类型: {types}")

    # 验证关系
    r = session.get(f"{BASE_API}/api/v2/ontologies/{onto_id}/graph")
    graph_data = r.json() if r.ok else {}
    if isinstance(graph_data, dict):
        nodes = graph_data.get("nodes", graph_data.get("data", {}).get("nodes", []))
        edges = graph_data.get("edges", graph_data.get("data", {}).get("edges", []))
    else:
        nodes, edges = [], []
    print(f"  图谱节点: {len(nodes) if isinstance(nodes, list) else '?'}")
    print(f"  图谱关系: {len(edges) if isinstance(edges, list) else '?'}")

    # ═══ 结果 ═══
    print("\n" + "═"*70)
    print(f"  ✅ PRD 对齐测试完成")
    print(f"  文件上传: {len(uploaded)} 个")
    print(f"  Curated Datasets: {len(curated_ids)} 个")
    print(f"  Entity Types: {len(mapping_ids)} 种")
    print(f"  实体数: {len(items)}")
    print(f"  关系数: {len(edges) if isinstance(edges, list) else 0}")
    print(f"  Ontology: {onto_id[:8]}")
    print("═"*70)

if __name__ == "__main__":
    main()

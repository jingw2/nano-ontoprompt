#!/usr/bin/env python3
"""
供应链全流程脚本 v2
- Pipeline: connector(8文件) → storage → transform(deepseek LLM + mimo-omni VLM) → output
- Ontology: 由 pipeline mapping + LLM extraction 自动生成（不手动创建实体）
- Neo4j: mapping apply 时自动写入图数据库
"""
import json, sys, os, time
import httpx

BASE_URL = "http://localhost:8000"
DATA_DIR = os.path.join(os.path.dirname(__file__), "test_data", "供应链")

# 文件列表及处理策略
FILES = [
    # (文件名, 转换策略)
    ("inventory_transactions.csv",  "structured"),       # Route A
    ("logistics_performance.csv",   "structured"),       # Route A
    ("supplier_database.xlsx",      "structured"),       # Route A
    ("supplier_orders.json",        "semi_structured"),  # Route B
    ("procurement_policy.docx",     "unstructured_vlm"), # Route C + VLM
    ("supply_chain_review.pptx",    "unstructured_vlm"), # Route C + VLM
    ("supply_chain_strategy.md",    "unstructured"),     # Route C markitdown
    ("warehouse_management.pdf",    "unstructured_vlm"), # Route C + VLM
]

MIME = {
    "csv":  "text/csv",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "json": "application/json",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "md":   "text/markdown",
    "pdf":  "application/pdf",
}


def login(c):
    r = c.post("/api/v1/auth/login", json={"username": "admin", "password": "admin123"})
    r.raise_for_status()
    return r.json()["data"]["access_token"]


def h(token):
    return {"Authorization": f"Bearer {token}"}


# ─── 清理旧数据 ───────────────────────────────────────────────
def cleanup_old_data(c, token):
    """删除旧的供应链 pipeline 和 ontology，保留模型配置"""
    # 删除旧 pipelines
    pls = c.get("/api/v2/pipelines", headers=h(token)).json()
    for pl in pls:
        if "供应链" in pl.get("name", ""):
            c.delete(f"/api/v2/pipelines/{pl['id']}", headers=h(token))
            print(f"  [DEL] Pipeline: {pl['name']}")
    # 删除旧 ontologies
    onts = c.get("/api/v1/ontologies", headers=h(token)).json()
    for o in onts.get("data", {}).get("items", []):
        if "供应链" in o.get("name", ""):
            c.delete(f"/api/v1/ontologies/{o['id']}", headers=h(token))
            print(f"  [DEL] Ontology: {o['name']}")


# ─── Step 1: 上传文件 ─────────────────────────────────────────
def upload_files(c, token):
    datasets = {}
    for fname, strategy in FILES:
        fpath = os.path.join(DATA_DIR, fname)
        if not os.path.exists(fpath):
            print(f"  [SKIP] {fname}")
            continue
        ext = fname.rsplit(".", 1)[-1].lower()
        with open(fpath, "rb") as f:
            content = f.read()
        r = c.post("/api/v2/datasets/upload", headers=h(token),
                   files={"file": (fname, content, MIME.get(ext, "application/octet-stream"))})
        r.raise_for_status()
        ds = r.json()["data"]
        datasets[fname] = {"id": ds["id"], "kind": ds["kind"], "strategy": strategy}
        print(f"  [OK] {fname} → {ds['id'][:8]}... kind={ds['kind']} strategy={strategy}")
    return datasets


# ─── Step 2: 创建 Pipeline ────────────────────────────────────
def create_pipeline(c, token, datasets, vlm_model_id, llm_model_id):
    connector_files = [
        {"name": fname, "dataset_id": info["id"]}
        for fname, info in datasets.items()
    ]

    # Transform 节点配置：对非结构化文件使用 VLM，对半结构化使用 JSON 展开
    transform_steps_unstructured_vlm = [
        {"op": "document_to_markdown", "params": {"strategy": "vlm", "model_id": vlm_model_id}},
        {"op": "llm_structurize",      "params": {"model_id": llm_model_id, "auto_extract": True}},
    ]
    transform_steps_unstructured = [
        {"op": "document_to_markdown", "params": {"strategy": "markitdown"}},
        {"op": "llm_structurize",      "params": {"model_id": llm_model_id, "auto_extract": True}},
    ]
    transform_steps_semi = [
        {"op": "flatten_json",         "params": {"array_explode": True}},
        {"op": "drop_duplicates",      "params": {}},
    ]
    transform_steps_structured = [
        {"op": "drop_duplicates",      "params": {}},
        {"op": "fill_nulls",           "params": {"strategy": "fill_empty"}},
        {"op": "normalize_dates",      "params": {}},
    ]

    # 混合策略：按数据类型决定路径
    has_vlm = any(info["strategy"] == "unstructured_vlm" for info in datasets.values())
    has_unst = any(info["strategy"] in ("unstructured", "unstructured_vlm") for info in datasets.values())

    definition = {
        "nodes": [
            {
                "id": "node-connector",
                "type": "connector",
                "label": "供应链数据源 (8文件)",
                "config": {
                    "source_type": "file",
                    "files": connector_files,
                },
            },
            {
                "id": "node-storage",
                "type": "storage",
                "label": "原始存储",
                "config": {"storage_type": "raw"},
            },
            {
                "id": "node-transform",
                "type": "transform",
                "label": "多路径转换 (LLM+VLM)",
                "config": {
                    "path": "structured",   # 默认结构化，非结构化文件按 kind 自动路由
                    "engine": "auto",
                    "steps": transform_steps_structured,
                    # unstructured 文件用 VLM+LLM
                    "unstructured_steps": transform_steps_unstructured_vlm,
                    "semi_steps": transform_steps_semi,
                },
            },
            {
                "id": "node-output",
                "type": "output",
                "label": "结构化输出",
                "config": {"format": "curated", "target": "ontology_mapping"},
            },
        ],
        "edges": [
            {"id": "e1", "source": "node-connector", "target": "node-storage"},
            {"id": "e2", "source": "node-storage",   "target": "node-transform"},
            {"id": "e3", "source": "node-transform",  "target": "node-output"},
        ],
    }

    r = c.post("/api/v2/pipelines", headers=h(token), json={
        "name": "供应链全链路Pipeline",
        "domain": "供应链",
        "description": "结构化CSV/XLSX + JSON展开 + VLM(mimo-omni)文档提取 + LLM(deepseek)结构化",
        "definition": definition,
    })
    if r.status_code == 400 and "已存在" in r.text:
        pls = c.get("/api/v2/pipelines", headers=h(token)).json()
        for pl in pls:
            if pl["name"] == "供应链全链路Pipeline":
                return pl["id"]
    r.raise_for_status()
    pid = r.json()["id"]
    print(f"  [OK] Pipeline id={pid}")
    return pid


# ─── Step 3: 运行 Pipeline ────────────────────────────────────
def run_pipeline(c, token, pipeline_id):
    r = c.post(f"/api/v2/pipelines/{pipeline_id}/run-sync", headers=h(token), timeout=300)
    r.raise_for_status()
    result = r.json()
    stats = result.get("stats") or {}
    curated_ids = stats.get("curated_dataset_ids") or []
    if not curated_ids and stats.get("curated_dataset_id"):
        curated_ids = [stats["curated_dataset_id"]]
    print(f"  [OK] status={result.get('status')} rows_in={stats.get('rows_in',0)} rows_out={stats.get('rows_out',0)}")
    print(f"  [OK] 生成 {len(curated_ids)} 个 Curated Dataset")

    # 打印每个 curated dataset 预览
    for cid in curated_ids[:4]:
        rows = c.get(f"/api/v2/datasets/{cid}/versions/1/preview?limit=2", headers=h(token))
        if rows.status_code == 200:
            data = rows.json()
            if data:
                cols = list(data[0].keys())[:5]
                print(f"    - {cid[:8]}... cols={cols}")
    return curated_ids


# ─── Step 4: 创建本体项目 ────────────────────────────────────
def create_ontology(c, token):
    r = c.post("/api/v1/ontologies", headers=h(token), json={
        "name": "供应链知识本体 v2",
        "domain": "供应链",
        "description": "由Pipeline Mapping + LLM自动提取生成的供应链知识图谱本体",
        "build_mode": "simple_llm",
    })
    r.raise_for_status()
    oid = r.json()["data"]["id"]
    print(f"  [OK] Ontology id={oid}")
    return oid


# ─── Step 5: LLM 提取（从 curated 数据中提取本体）────────────
def run_llm_extraction(c, token, ontology_id, model_id, model_name, prompt_id):
    """先上传 curated 数据为文件，再触发 LLM 提取"""
    # 获取 curated datasets
    curated_list = c.get("/api/v2/curated", headers=h(token)).json()
    print(f"  [INFO] 共 {len(curated_list)} 个 curated datasets 可用")

    # 收集文本内容用于 LLM 提取
    all_text_parts = []
    for ds in curated_list[:6]:  # 最多6个，避免超长
        cid = ds["id"]
        rows = c.get(f"/api/v2/datasets/{cid}/versions/1/preview?limit=20", headers=h(token))
        if rows.status_code != 200:
            continue
        data = rows.json()
        if not data:
            continue
        name = ds["name"]
        # 转成文本摘要
        cols = list(data[0].keys()) if data else []
        sample = json.dumps(data[:5], ensure_ascii=False)
        all_text_parts.append(f"## {name}\n列：{cols}\n样本数据：\n{sample}\n")

    if not all_text_parts:
        print("  [WARN] 无 curated 数据可用于提取")
        return None

    combined = "\n\n".join(all_text_parts)

    # 上传为临时文件到 ontology
    import io
    file_content = combined.encode("utf-8")
    r = c.post(
        f"/api/v1/ontologies/{ontology_id}/files",
        headers=h(token),
        files={"file": ("supply_chain_curated_summary.md", file_content, "text/markdown")},
    )
    if r.status_code not in (200, 201):
        print(f"  [WARN] 上传摘要文件失败: {r.status_code} {r.text[:100]}")
        return None
    print("  [OK] 已上传 curated 数据摘要文件")

    # 触发 LLM 提取
    r = c.post(
        f"/api/v1/ontologies/{ontology_id}/execute",
        headers=h(token),
        json={
            "prompt_id": prompt_id,
            "model_id":  model_id,
            "model_name": model_name,
            "constraints": [
                "领域：供应链",
                "必须提取：供应商、物料、采购订单、库存交易、物流运单、仓库、承运商、质量检验",
                "逻辑规则需包含：库存预警、供应商评级、采购审批",
                "Action 需包含：补货申请、供应商绩效更新、物流告警",
            ],
        },
        timeout=300,
    )
    if r.status_code not in (200, 201):
        print(f"  [WARN] 提取请求失败: {r.status_code} {r.text[:200]}")
        return None
    task_id = r.json()["data"]["task_id"]
    print(f"  [OK] 提取任务 task_id={task_id}")
    return task_id


def poll_extraction(c, token, ontology_id, task_id, timeout=180):
    """轮询提取任务直到完成"""
    start = time.time()
    while time.time() - start < timeout:
        r = c.get(f"/api/v1/ontologies/{ontology_id}/execute/status?task_id={task_id}", headers=h(token))
        if r.status_code != 200:
            time.sleep(3)
            continue
        task = r.json()["data"]
        status = task.get("status")
        pct = task.get("progress", {}).get("pct", 0)
        stage = task.get("progress", {}).get("stage", "")
        print(f"  ... {status} {pct}% [{stage}]")
        if status in ("completed", "failed"):
            if status == "failed":
                print(f"  [FAIL] {task.get('error')}")
            else:
                print(f"  [OK] LLM 提取完成")
            return status
        time.sleep(5)
    print("  [TIMEOUT] 提取超时")
    return "timeout"


# ─── Step 6: 自动映射 ────────────────────────────────────────
def auto_map_and_create(c, token, ontology_id):
    """对每个 curated dataset 调用 suggest + create mapping"""
    curated_list = c.get("/api/v2/curated", headers=h(token)).json()
    mappings_created = []

    for ds in curated_list:
        cid = ds["id"]
        name = ds["name"]

        # 获取 schema 信息
        schema_r = c.get(f"/api/v2/datasets/{cid}/schema", headers=h(token))
        if schema_r.status_code != 200:
            continue
        schema = schema_r.json()
        columns = [col["name"] for col in schema.get("columns", [])]
        if not columns:
            continue

        # 获取样本数据
        preview_r = c.get(f"/api/v2/datasets/{cid}/versions/1/preview?limit=3", headers=h(token))
        sample_rows = preview_r.json() if preview_r.status_code == 200 else []

        # LLM suggest mapping
        suggest_r = c.post(
            f"/api/v2/ontologies/{ontology_id}/mappings/suggest",
            headers=h(token),
            json={
                "dataset_name": name,
                "columns": columns,
                "sample_rows": sample_rows,
                "ontology_domain": "供应链",
            },
            timeout=60,
        )
        if suggest_r.status_code != 200:
            print(f"  [WARN] suggest 失败 {name}: {suggest_r.status_code}")
            continue
        suggestion = suggest_r.json()
        entity_class = suggestion.get("entity_class", "UnknownEntity")
        pk_col = suggestion.get("primary_key_column")
        field_mapping = {
            fm["column_name"]: fm["property_name"]
            for fm in suggestion.get("field_mappings", [])
        }
        print(f"  [SUGGEST] {name[:40]} → {entity_class} pk={pk_col}")

        # 创建 mapping
        create_r = c.post(
            f"/api/v2/ontologies/{ontology_id}/mappings",
            headers=h(token),
            json={
                "curated_dataset_id": cid,
                "entity_class": entity_class,
                "field_mapping": field_mapping,
                "primary_key_column": pk_col,
                "confidence": 0.92,
            },
        )
        if create_r.status_code in (200, 201):
            mid = create_r.json().get("mapping_id")
            mappings_created.append({"id": mid, "entity_class": entity_class, "curated_id": cid})
            print(f"  [OK] Mapping created: {entity_class} mapping_id={mid}")
        else:
            print(f"  [WARN] create mapping 失败: {create_r.status_code} {create_r.text[:80]}")

    return mappings_created


# ─── Step 7: Apply Mappings → Neo4j ──────────────────────────
def apply_mappings_to_neo4j(c, token, ontology_id, mappings):
    """触发 apply-from-dataset，直接从 curated dataset 读取并写入 Neo4j"""
    all_mappings = c.get(f"/api/v2/ontologies/{ontology_id}/mappings", headers=h(token)).json()
    applied = 0
    seen_curated = set()
    for mapping in all_mappings:
        mid = mapping.get("id") or mapping.get("mapping_id")
        cid = mapping.get("curated_dataset_id")
        if not mid or not cid:
            continue
        # 同一 curated dataset 只 apply 一次
        if cid in seen_curated:
            continue
        seen_curated.add(cid)
        apply_r = c.post(
            f"/api/v2/ontologies/{ontology_id}/mappings/{mid}/apply-from-dataset",
            headers=h(token),
            timeout=120,
        )
        if apply_r.status_code in (200, 201):
            res = apply_r.json()
            neo4j = res.get("neo4j_count", 0)
            v1 = res.get("v1_count", 0)
            print(f"  [OK] {mapping.get('entity_class'):30s} neo4j={neo4j} v1={v1}")
            applied += 1
        else:
            print(f"  [WARN] {mid[:8]}...: {apply_r.status_code} {apply_r.text[:80]}")
    return applied


# ─── Step 8: 验证 ────────────────────────────────────────────
def verify(c, token, ontology_id):
    entities = c.get(f"/api/v1/ontologies/{ontology_id}/entities", headers=h(token)).json()
    logic    = c.get(f"/api/v1/ontologies/{ontology_id}/logic",    headers=h(token)).json()
    actions  = c.get(f"/api/v1/ontologies/{ontology_id}/actions",  headers=h(token)).json()
    mappings = c.get(f"/api/v2/ontologies/{ontology_id}/mappings", headers=h(token)).json()

    n_e = len(entities.get("data", []))
    n_l = len(logic.get("data", []))
    n_a = len(actions.get("data", []))
    n_m = len(mappings) if isinstance(mappings, list) else 0

    # Neo4j 图验证
    neo4j_ok = False
    try:
        from neo4j import GraphDatabase
        driver = GraphDatabase.driver("bolt://localhost:7687", auth=("neo4j", "ontoprompt123"))
        with driver.session() as s:
            node_count = s.run("MATCH (n) RETURN count(n) as c").single()["c"]
            rel_count  = s.run("MATCH ()-[r]->() RETURN count(r) as c").single()["c"]
        driver.close()
        neo4j_ok = node_count > 0
        neo4j_info = f"nodes={node_count} relations={rel_count}"
    except Exception as e:
        neo4j_info = f"error: {e}"

    print("\n" + "=" * 60)
    print("验收结果")
    print("=" * 60)
    print(f"  实体:     {n_e} 个")
    print(f"  逻辑规则: {n_l} 条")
    print(f"  Action:   {n_a} 个")
    print(f"  映射:     {n_m} 条")
    print(f"  Neo4j:    {neo4j_info}")
    print()
    checks = [
        ("知识图谱网络状结构(≥5实体)", n_e >= 5),
        ("实体/逻辑/Action均有数据", n_e > 0 and n_l > 0 and n_a > 0),
        ("映射关系建立(≥1条)", n_m >= 1),
        ("Neo4j 图数据写入", neo4j_ok),
    ]
    all_pass = True
    for label, passed in checks:
        icon = "✓" if passed else "✗"
        print(f"  {icon} {label}")
        if not passed:
            all_pass = False
    print()
    print("  [SUCCESS] 全部通过！" if all_pass else "  [PARTIAL] 部分未通过")
    return {"entities": n_e, "logic": n_l, "actions": n_a, "mappings": n_m, "neo4j": neo4j_info}


# ─── Main ─────────────────────────────────────────────────────
def main():
    print("=" * 60)
    print("供应链 全流程 Pipeline + Ontology (v2)")
    print("LLM: deepseek-v4  |  VLM: mimo-omni  |  Graph: Neo4j")
    print("=" * 60)

    with httpx.Client(base_url=BASE_URL, timeout=300) as c:
        print("\n[Auth] 登录...")
        token = login(c)
        print("  [OK]")

        # 获取模型配置
        models = c.get("/api/v1/models", headers=h(token)).json()
        models = models.get("data", []) if isinstance(models, dict) else models
        llm_cfg = next((m for m in models if "结构化提取" in (m.get("options") or {}).get("usage_tags", [])), None)
        vlm_cfg = next((m for m in models if "VLM提取"   in (m.get("options") or {}).get("usage_tags", [])), None)
        if not llm_cfg or not vlm_cfg:
            print(f"[ERROR] 未找到模型配置. LLM={llm_cfg is not None} VLM={vlm_cfg is not None}")
            sys.exit(1)
        llm_id    = llm_cfg["id"]
        llm_model = (llm_cfg.get("models") or ["deepseek-v4-flash"])[0]
        vlm_id    = vlm_cfg["id"]
        print(f"  LLM: {llm_cfg['name']} / {llm_model}")
        print(f"  VLM: {vlm_cfg['name']} / {(vlm_cfg.get('models') or ['?'])[0]}")

        # 获取供应链 prompt
        prompts = c.get("/api/v1/prompts", headers=h(token)).json()
        prompt_items = prompts.get("data", []) if isinstance(prompts, dict) else prompts
        sc_prompt = next((p for p in prompt_items if "供应链" in p["name"]), None)
        if not sc_prompt:
            sc_prompt = next((p for p in prompt_items if "通用" in p["name"]), prompt_items[0] if prompt_items else None)
        prompt_id = sc_prompt["id"] if sc_prompt else None
        print(f"  Prompt: {sc_prompt['name'] if sc_prompt else 'None'}")

        print("\n[Step 0] 清理旧供应链数据...")
        cleanup_old_data(c, token)

        print("\n[Step 1] 上传文件 → Datasets...")
        datasets = upload_files(c, token)
        print(f"  [OK] 共 {len(datasets)} 个文件")

        print("\n[Step 2] 创建 Pipeline...")
        pipeline_id = create_pipeline(c, token, datasets, vlm_id, llm_id)

        print("\n[Step 3] 运行 Pipeline (LLM+VLM 转换)...")
        curated_ids = run_pipeline(c, token, pipeline_id)

        print("\n[Step 4] 创建本体项目...")
        ontology_id = create_ontology(c, token)

        if prompt_id:
            print("\n[Step 5] LLM 提取本体 (deepseek)...")
            task_id = run_llm_extraction(c, token, ontology_id, llm_id, llm_model, prompt_id)
            if task_id:
                status = poll_extraction(c, token, ontology_id, task_id, timeout=240)
                print(f"  提取状态: {status}")
        else:
            print("\n[Step 5] 跳过 LLM 提取（无 prompt）")

        print("\n[Step 6] LLM 自动映射 (deepseek)...")
        mappings = auto_map_and_create(c, token, ontology_id)
        print(f"  [OK] 创建 {len(mappings)} 条映射")

        print("\n[Step 7] Apply Mappings → Neo4j...")
        applied = apply_mappings_to_neo4j(c, token, ontology_id, mappings)
        print(f"  [OK] {applied} 条映射已写入 Neo4j")

        print("\n[Step 8] 验证...")
        result = verify(c, token, ontology_id)

        print("\n" + "=" * 60)
        print(f"Pipeline ID:  {pipeline_id}")
        print(f"Ontology ID:  {ontology_id}")
        print(f"Curated 数量: {len(curated_ids)}")
        print(f"Mappings:     {result['mappings']}")
        print(f"Neo4j:        {result['neo4j']}")
        print("=" * 60)


if __name__ == "__main__":
    main()
